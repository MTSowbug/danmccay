import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import builtins
import io
import json
import types
import time
from pathlib import Path
import subprocess
import tempfile
import feedfetchtest as fft
import importlib


def test_extract_feed_urls():
    opml = """
    <opml><body>
        <outline type='rss' xmlUrl='http://a.com/feed' title='A'/>
        <outline text='x'><outline type='rss' xmlUrl='http://b.com/rss' title='B'/></outline>
    </body></opml>
    """
    urls = fft._extract_feed_urls(opml)
    assert urls == ['http://a.com/feed', 'http://b.com/rss']
    feeds = fft._extract_feed_urls(opml, with_titles=True)
    assert feeds == [('http://a.com/feed', 'A'), ('http://b.com/rss', 'B')]

def test_entry_timestamp():
    class Entry: pass
    e = Entry()
    e.published_parsed = time.gmtime(0)  # 1970-01-01 00:00 UTC
    ts = fft._entry_timestamp(e)
    assert ts.isoformat() == '1970-01-01T00:00:00+00:00'

    e2 = Entry()
    e2.updated_parsed = time.gmtime(60)
    ts2 = fft._entry_timestamp(e2)
    assert ts2.isoformat() == '1970-01-01T00:01:00+00:00'

    e3 = Entry()
    assert fft._entry_timestamp(e3) is None

def test_strip_html():
    text = '<p>Hello <b>World</b></p>'
    assert fft._strip_html(text) == 'Hello World'
    assert fft._strip_html('NoTags') == 'NoTags'
    assert fft._strip_html('') == ''

def test_extract_doi():
    entry = {'dc_identifier': 'doi:10.1234/xyz'}
    assert fft._extract_doi(entry) == 'https://doi.org/10.1234/xyz'

    entry2 = {'id': 'something doi:10.4321/abc text'}
    assert fft._extract_doi(entry2) == 'https://doi.org/10.4321/abc'

    entry3 = {'link': 'https://doi.org/10.1111/qwe'}
    assert fft._extract_doi(entry3) == 'https://doi.org/10.1111/qwe'

    entry4 = {}
    assert fft._extract_doi(entry4) == ''

def test_extract_doi_object():
    class E:
        link = 'https://doi.org/10.9999/test'

    assert fft._extract_doi(E()) == 'https://doi.org/10.9999/test'

def test_entry_to_article_data(monkeypatch):
    class Entry(dict):
        def __init__(self):
            super().__init__()
            self.published_parsed = time.gmtime(0)
            self['title'] = 'Title'
            self.authors = [{'name': 'A'}, {'name': 'B'}]
            self['dc_source'] = 'Journal'
            self['summary'] = '<p>Abstract</p>'
            self['link'] = 'https://doi.org/10.1234/test'
            self['id'] = 'id1'
    e = Entry()
    data = fft._entry_to_article_data(e)
    assert data['doi'] == 'https://doi.org/10.1234/test'
    assert data['title'] == 'Title'
    assert data['authors'] == ['A', 'B']
    assert data['journal'] == 'Journal'
    assert data['year'] == 1970
    assert data['abstract'] == 'Abstract'
    assert data['link'] == 'https://doi.org/10.1234/test'
    # date-added should parse to datetime isoformat; check endswithZ
    assert data['num-retrievals'] == 0
    assert 'date-added' in data
    assert data['lt-relevance'] == 0


def test_entry_to_article_data_longevity():
    html = (
        "<h3>Paper Details</h3>"
        "<p><strong>Authors:</strong> Alice, Bob</p>"
        "<p><strong>Journal:</strong> Longevity Journal</p>"
        "<h3>Abstract</h3>"
        "<p>Example abstract.</p>"
    )

    class Entry(dict):
        def __init__(self):
            super().__init__()
            self.published_parsed = time.gmtime(0)
            self['title'] = 'T'
            self['summary'] = html
            self['link'] = 'L'
            self['id'] = 'ID'

    e = Entry()
    data = fft._entry_to_article_data(e)
    assert data['authors'] == ['Alice', 'Bob']
    assert data['journal'] == 'Longevity Journal'
    assert data['abstract'] == 'Example abstract.'


def test_sanitize_filename():
    fname = fft._sanitize_filename('a/b?c*<>|')
    assert fname == 'a_b_c_'
    assert fft._sanitize_filename('x'*60) == 'x'*50


def test_doi_filename():
    assert fft._doi_filename('https://doi.org/10.1234/Ab.C') == 'doiorg10.1234_ab.c'
    assert fft._doi_filename('DOI:10.1/hi-there') == 'doiorg10.1_hi-there'


def test_extract_shell_script():
    text = 'some text\n```bash\necho hi\n```\nmore'
    assert fft._extract_shell_script(text) == 'echo hi'
    assert fft._extract_shell_script('echo hi') == 'echo hi'


def test_is_safe_command():
    assert fft._is_safe_command('wget http://a')
    assert not fft._is_safe_command('rm -rf /')
    assert not fft._is_safe_command('wget http://a && rm')
    assert not fft._is_safe_command('')

def test_pdf_file_valid(tmp_path):
    from PyPDF2 import PdfWriter
    valid = tmp_path / 'v.pdf'
    writer = PdfWriter()
    for _ in range(100):
        writer.add_blank_page(width=72, height=72)
    with open(valid, 'wb') as f:
        writer.write(f)
    assert fft._pdf_file_valid(valid)

    small = tmp_path / 's.pdf'
    small.write_bytes(b'0'*100)
    assert not fft._pdf_file_valid(small)

def test_download_pdf(monkeypatch, tmp_path):
    created = []

    def fake_llm(entry, dest):
        p1 = dest / 'a.pdf'
        p1.write_bytes(b'PDFDATA')
        p2 = dest / 'b.pdf'
        p2.write_bytes(b'PDFDATA2')
        created.extend([p1, p2])
        return ''

    def fake_valid(path):
        return True

    monkeypatch.setattr(fft, '_llm_shell_commands', fake_llm)
    monkeypatch.setattr(fft, '_pdf_file_valid', fake_valid)

    class E: link='x'; title='t'

    result = fft._download_pdf(E(), tmp_path)
    expected = (fft._BASE_DIR / '../pdfs').resolve() / 'a.pdf'
    assert result == expected
    assert not (tmp_path / 'b.pdf').exists()
    assert not (tmp_path / 'a.pdf').exists()


def test_download_pdf_aging_cell(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, cwd=None, check=None):
        calls.append(cmd)
        p = Path(cwd) / 'article_fulltest_version1.pdf'
        p.write_bytes(b'd')
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(fft.subprocess, 'run', fake_run)
    monkeypatch.setattr(fft, '_pdf_file_valid', lambda p: True)
    monkeypatch.setattr(fft, '_llm_shell_commands', lambda *a, **k: None)
    monkeypatch.setattr(fft, '_extract_doi', lambda e: 'https://doi.org/10.1111/acel.70123')

    class E:
        link = 'x'
        title = 't'
        journal = 'Aging Cell'

    result = fft._download_pdf(E(), tmp_path)
    assert calls
    assert calls[0][-1].endswith('acel.70123')
    expected = (fft._BASE_DIR / '../pdfs').resolve() / 'doiorg10.1111_acel.70123.pdf'
    assert result == expected
    assert not (tmp_path / 'article_fulltest_version1.pdf').exists()


def test_download_pdf_aging_cell_case_insensitive(monkeypatch, tmp_path):
    """Ensure journal comparison ignores capitalization."""
    calls = []

    def fake_run(cmd, cwd=None, check=None):
        calls.append(cmd)
        p = Path(cwd) / 'article_fulltest_version1.pdf'
        p.write_bytes(b'd')
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(fft.subprocess, 'run', fake_run)
    monkeypatch.setattr(fft, '_pdf_file_valid', lambda p: True)
    monkeypatch.setattr(fft, '_llm_shell_commands', lambda *a, **k: None)
    monkeypatch.setattr(fft, '_extract_doi', lambda e: 'https://doi.org/10.1111/acel.70123')

    class E:
        link = 'x'
        title = 't'
        journal = 'AGING CELL'

    result = fft._download_pdf(E(), tmp_path)
    assert calls
    assert calls[0][-1].endswith('acel.70123')
    expected = (fft._BASE_DIR / '../pdfs').resolve() / 'doiorg10.1111_acel.70123.pdf'
    assert result == expected
    assert not (tmp_path / 'article_fulltest_version1.pdf').exists()


def test_download_pdf_aging_us(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, cwd=None, check=None):
        calls.append(cmd)
        p = Path(cwd) / 'article_fulltest_version1.pdf'
        p.write_bytes(b'd')
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(fft.subprocess, 'run', fake_run)
    monkeypatch.setattr(fft, '_pdf_file_valid', lambda p: True)
    monkeypatch.setattr(fft, '_llm_shell_commands', lambda *a, **k: None)
    monkeypatch.setattr(fft, '_extract_doi', lambda e: 'https://doi.org/10.18632/aging.206245')

    class E:
        link = 'x'
        title = 't'
        journal = 'Aging'

    result = fft._download_pdf(E(), tmp_path)
    assert calls
    assert calls[0][-1].endswith('10.18632/aging.206245')
    expected = (fft._BASE_DIR / '../pdfs').resolve() / 'doiorg10.18632_aging.206245.pdf'
    assert result == expected
    assert not (tmp_path / 'article_fulltest_version1.pdf').exists()


def test_download_pdf_aging_us_case_insensitive(monkeypatch, tmp_path):
    """Ensure Aging journal comparison ignores capitalization."""
    calls = []

    def fake_run(cmd, cwd=None, check=None):
        calls.append(cmd)
        p = Path(cwd) / 'article_fulltest_version1.pdf'
        p.write_bytes(b'd')
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(fft.subprocess, 'run', fake_run)
    monkeypatch.setattr(fft, '_pdf_file_valid', lambda p: True)
    monkeypatch.setattr(fft, '_llm_shell_commands', lambda *a, **k: None)
    monkeypatch.setattr(fft, '_extract_doi', lambda e: 'https://doi.org/10.18632/aging.206245')

    class E:
        link = 'x'
        title = 't'
        journal = 'AGING'

    result = fft._download_pdf(E(), tmp_path)
    assert calls
    assert calls[0][-1].endswith('10.18632/aging.206245')
    expected = (fft._BASE_DIR / '../pdfs').resolve() / 'doiorg10.18632_aging.206245.pdf'
    assert result == expected
    assert not (tmp_path / 'article_fulltest_version1.pdf').exists()


def test_download_pdf_nataging(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, cwd=None, check=None):
        calls.append(cmd)
        p = Path(cwd) / 'article_fulltest_version1.pdf'
        p.write_bytes(b'd')
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(fft.subprocess, 'run', fake_run)
    monkeypatch.setattr(fft, '_pdf_file_valid', lambda p: True)
    monkeypatch.setattr(fft, '_llm_shell_commands', lambda *a, **k: None)
    monkeypatch.setattr(
        fft,
        '_extract_doi',
        lambda e: 'https://doi.org/10.1038/s43587-025-00901-6',
    )

    class E:
        link = 'x'
        title = 't'
        journal = 'Nature Aging'

    result = fft._download_pdf(E(), tmp_path)
    assert calls
    assert calls[0][-1].endswith('10.1038/s43587-025-00901-6')
    expected = (fft._BASE_DIR / '../pdfs').resolve() / 'doiorg10.1038_s43587-025-00901-6.pdf'
    assert result == expected
    assert not (tmp_path / 'article_fulltest_version1.pdf').exists()


def test_download_pdf_nataging_case_insensitive(monkeypatch, tmp_path):
    """Ensure Nature Aging journal comparison ignores capitalization."""
    calls = []

    def fake_run(cmd, cwd=None, check=None):
        calls.append(cmd)
        p = Path(cwd) / 'article_fulltest_version1.pdf'
        p.write_bytes(b'd')
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(fft.subprocess, 'run', fake_run)
    monkeypatch.setattr(fft, '_pdf_file_valid', lambda p: True)
    monkeypatch.setattr(fft, '_llm_shell_commands', lambda *a, **k: None)
    monkeypatch.setattr(
        fft,
        '_extract_doi',
        lambda e: 'https://doi.org/10.1038/s43587-025-00901-6',
    )

    class E:
        link = 'x'
        title = 't'
        journal = 'NATURE AGING'

    result = fft._download_pdf(E(), tmp_path)
    assert calls
    assert calls[0][-1].endswith('10.1038/s43587-025-00901-6')
    expected = (fft._BASE_DIR / '../pdfs').resolve() / 'doiorg10.1038_s43587-025-00901-6.pdf'
    assert result == expected
    assert not (tmp_path / 'article_fulltest_version1.pdf').exists()


def test_download_pdf_natcomms(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, cwd=None, check=None):
        calls.append(cmd)
        p = Path(cwd) / 'article_fulltest_version1.pdf'
        p.write_bytes(b'd')
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(fft.subprocess, 'run', fake_run)
    monkeypatch.setattr(fft, '_pdf_file_valid', lambda p: True)
    monkeypatch.setattr(fft, '_llm_shell_commands', lambda *a, **k: None)
    monkeypatch.setattr(
        fft,
        '_extract_doi',
        lambda e: 'https://doi.org/10.1038/s41467-025-01234-7',
    )

    class E:
        link = 'x'
        title = 't'
        journal = 'Nature Communications'

    result = fft._download_pdf(E(), tmp_path)
    assert calls
    assert calls[0][-1].endswith('10.1038/s41467-025-01234-7')
    expected = (fft._BASE_DIR / '../pdfs').resolve() / 'doiorg10.1038_s41467-025-01234-7.pdf'
    assert result == expected
    assert not (tmp_path / 'article_fulltest_version1.pdf').exists()


def test_download_pdf_natcomms_case_insensitive(monkeypatch, tmp_path):
    """Ensure Nature Communications journal comparison ignores capitalization."""
    calls = []

    def fake_run(cmd, cwd=None, check=None):
        calls.append(cmd)
        p = Path(cwd) / 'article_fulltest_version1.pdf'
        p.write_bytes(b'd')
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(fft.subprocess, 'run', fake_run)
    monkeypatch.setattr(fft, '_pdf_file_valid', lambda p: True)
    monkeypatch.setattr(fft, '_llm_shell_commands', lambda *a, **k: None)
    monkeypatch.setattr(
        fft,
        '_extract_doi',
        lambda e: 'https://doi.org/10.1038/s41467-025-01234-7',
    )

    class E:
        link = 'x'
        title = 't'
        journal = 'NATURE COMMUNICATIONS'

    result = fft._download_pdf(E(), tmp_path)
    assert calls
    assert calls[0][-1].endswith('10.1038/s41467-025-01234-7')
    expected = (fft._BASE_DIR / '../pdfs').resolve() / 'doiorg10.1038_s41467-025-01234-7.pdf'
    assert result == expected
    assert not (tmp_path / 'article_fulltest_version1.pdf').exists()


def test_download_pdf_geroscience(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, cwd=None, check=None):
        calls.append(cmd)
        p = Path(cwd) / 'article_fulltest_version1.pdf'
        p.write_bytes(b'd')
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(fft.subprocess, 'run', fake_run)
    monkeypatch.setattr(fft, '_pdf_file_valid', lambda p: True)
    monkeypatch.setattr(fft, '_llm_shell_commands', lambda *a, **k: None)
    monkeypatch.setattr(
        fft,
        '_extract_doi',
        lambda e: 'https://doi.org/10.1007/s11357-021-00469-0',
    )

    class E:
        link = 'x'
        title = 't'
        journal = 'GeroScience'

    result = fft._download_pdf(E(), tmp_path)
    assert calls
    assert calls[0][-1].endswith('10.1007/s11357-021-00469-0')
    expected = (fft._BASE_DIR / '../pdfs').resolve() / 'doiorg10.1007_s11357-021-00469-0.pdf'
    assert result == expected
    assert not (tmp_path / 'article_fulltest_version1.pdf').exists()


def test_download_pdf_geroscience_case_insensitive(monkeypatch, tmp_path):
    """Ensure GeroScience journal comparison ignores capitalization."""
    calls = []

    def fake_run(cmd, cwd=None, check=None):
        calls.append(cmd)
        p = Path(cwd) / 'article_fulltest_version1.pdf'
        p.write_bytes(b'd')
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(fft.subprocess, 'run', fake_run)
    monkeypatch.setattr(fft, '_pdf_file_valid', lambda p: True)
    monkeypatch.setattr(fft, '_llm_shell_commands', lambda *a, **k: None)
    monkeypatch.setattr(
        fft,
        '_extract_doi',
        lambda e: 'https://doi.org/10.1007/s11357-021-00469-0',
    )

    class E:
        link = 'x'
        title = 't'
        journal = 'GEROSCIENCE'

    result = fft._download_pdf(E(), tmp_path)
    assert calls
    assert calls[0][-1].endswith('10.1007/s11357-021-00469-0')
    expected = (fft._BASE_DIR / '../pdfs').resolve() / 'doiorg10.1007_s11357-021-00469-0.pdf'
    assert result == expected
    assert not (tmp_path / 'article_fulltest_version1.pdf').exists()

def test_save_articles(tmp_path):
    path = tmp_path / 'a.json'
    fft._save_articles({'k': {'v': 1}}, path)
    data = json.loads(path.read_text())
    assert data['k']['v'] == 1
    # merge
    fft._save_articles({'k2': {}}, path)
    data2 = json.loads(path.read_text())
    assert set(data2.keys()) == {'k', 'k2'}

def test_fetch_recent_articles(monkeypatch, tmp_path):
    opml = '<opml><body><outline type="rss" xmlUrl="http://feed" title="FT"/></body></opml>'

    class Parsed:
        def __init__(self, entries):
            self.entries = entries

    class E(dict):
        def __init__(self):
            super().__init__()
            self.published_parsed = time.gmtime(time.time())
            self['title'] = 'T'
            self['link'] = 'L'
            self['id'] = 'ID'
            self['summary'] = ''
            self.link = 'L'
            self.title = 'T'
    parsed = Parsed([E()])
    monkeypatch.setattr(fft._fp, 'parse', lambda url: parsed)

    articles = fft.fetch_recent_articles(opml, hours=1, json_path=None, download_pdfs=False)
    assert 'ID' in articles
    assert articles['ID']['title'] == 'T'
    assert articles['ID']['rsstitle'] == 'FT'

def test_fetch_recent_articles_pdf_relative(monkeypatch, tmp_path):
    opml = '<opml><body><outline type="rss" xmlUrl="http://feed" title="FT"/></body></opml>'

    class Parsed:
        def __init__(self, entries):
            self.entries = entries

    class E(dict):
        def __init__(self):
            super().__init__()
            self.published_parsed = time.gmtime(time.time())
            self['title'] = 'T'
            self['link'] = 'L'
            self['id'] = 'ID'
            self['summary'] = ''
            self.link = 'L'
            self.title = 'T'

    parsed = Parsed([E()])
    monkeypatch.setattr(fft._fp, 'parse', lambda url: parsed)

    def fake_download(entry, dest):
        p = dest / 'p.pdf'
        p.write_bytes(b'd')
        return p

    monkeypatch.setattr(fft, '_download_pdf', fake_download)
    monkeypatch.setattr(fft, '_discover_doi', lambda *a, **k: '')
    monkeypatch.setattr(fft, '_PDF_DIR', tmp_path)
    monkeypatch.setattr(fft.time, 'sleep', lambda *a, **k: None)
    monkeypatch.setattr(fft.random, 'uniform', lambda *a, **k: 0)

    articles = fft.fetch_recent_articles(opml, hours=1, json_path=None, download_pdfs=True)
    assert articles['ID']['pdf'] == 'p.pdf'
    assert articles['ID']['download_successful'] is True
    assert articles['ID']['rsstitle'] == 'FT'


def test_fightaging_special_case(monkeypatch):
    opml = '<opml><body><outline type="rss" xmlUrl="http://feed" title="FT"/></body></opml>'

    html = '<a href="https://doi.org/10.1234/x">Read more</a>'

    class Parsed:
        def __init__(self, entries):
            self.entries = entries

    class E(dict):
        def __init__(self):
            super().__init__()
            self.published_parsed = time.gmtime(time.time())
            self['title'] = 'T'
            self['link'] = 'https://www.fightaging.org/archives/a-post/'
            self['id'] = 'ID'
            self['summary'] = ''
            self.link = self['link']
            self.title = 'T'

    parsed = Parsed([E()])
    monkeypatch.setattr(fft._fp, 'parse', lambda url: parsed)

    class Resp:
        def __init__(self, text):
            self.text = text.encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        def read(self):
            return self.text
        def geturl(self):
            return 'https://www.fightaging.org/archives/a-post/'

    monkeypatch.setattr(fft.urllib.request, 'urlopen', lambda url: Resp(html))

    called = []
    def fake_doi(url):
        called.append(url)
        return url
    monkeypatch.setattr(fft, '_extract_doi_from_url', fake_doi)
    monkeypatch.setattr(fft, '_extract_journal_from_url', lambda url: 'J')

    class FakeRespLLM:
        class choice:
            def __init__(self):
                self.message = types.SimpleNamespace(content='https://doi.org/10.1234/x')
        choices = [choice()]

    class FakeClient:
        def __init__(self, *a, **kw):
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=lambda **k: FakeRespLLM()))

    monkeypatch.setattr(fft, 'openai', types.SimpleNamespace(OpenAI=lambda: FakeClient()))

    articles = fft.fetch_recent_articles(opml, hours=1, json_path=None, download_pdfs=False)
    assert articles['ID']['doi'] == 'https://doi.org/10.1234/x'
    assert articles['ID']['link'] == 'https://doi.org/10.1234/x'
    assert articles['ID']['journal'] == 'J'
    assert articles['ID']['rsstitle'] == 'FT'
    assert called == []

def test_summarize_articles(monkeypatch, tmp_path):
    data = {
        '1': {'title': 'T1', 'abstract': 'A1'},
        '2': {'title': 'T2', 'abstract': 'A2'},
    }
    json_path = tmp_path / 'a.json'
    json_path.write_text(json.dumps(data))

    char_path = tmp_path / 'c.yaml'
    char_path.write_text('prompts:\n  char:\n    system: base')

    class FakeResp:
        class choice:
            def __init__(self):
                self.message = types.SimpleNamespace(content='SUM')
        choices = [choice()]

    class FakeClient:
        def __init__(self, *a, **kw):
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=lambda **k: FakeResp()))

    monkeypatch.setattr(fft, 'openai', types.SimpleNamespace(OpenAI=lambda: FakeClient()))

    summary = fft.summarize_articles(json_path, model='m', char_file=char_path)
    assert summary == 'SUM'


def test_download_missing_pdfs_limit(monkeypatch, tmp_path):
    data = {
        '1': {'title': 't1', 'link': 'L1'},
        '2': {'title': 't2', 'link': 'L2'},
    }
    json_path = tmp_path / 'a.json'
    json_path.write_text(json.dumps(data))

    created = []

    def fake_download(entry, dest):
        p = dest / f"{entry.title}.pdf"
        p.write_bytes(b'd')
        created.append(p)
        return p

    monkeypatch.setattr(fft, '_download_pdf', fake_download)
    monkeypatch.setattr(fft, '_discover_doi', lambda *a, **k: '')
    monkeypatch.setattr(fft, '_PDF_DIR', tmp_path)
    monkeypatch.setattr(fft.time, 'sleep', lambda *a, **k: None)
    monkeypatch.setattr(fft.random, 'uniform', lambda *a, **k: 0)

    fft.download_missing_pdfs(json_path=json_path, max_articles=1)

    stored = json.loads(json_path.read_text())
    assert sum('pdf' in v for v in stored.values()) == 1
    assert stored['1']['pdf'] == 't1.pdf'
    assert stored['1']['download_successful'] is True


def test_download_missing_pdfs_key_fallback(monkeypatch, tmp_path):
    data = {
        'https://example.com/a': {'title': 't1'},
        'https://example.com/b': {'title': 't2'},
    }
    json_path = tmp_path / 'a.json'
    json_path.write_text(json.dumps(data))

    captured = []

    def fake_download(entry, dest):
        captured.append(entry.link)
        p = dest / f"{entry.title}.pdf"
        p.write_bytes(b'd')
        return p

    monkeypatch.setattr(fft, '_download_pdf', fake_download)
    monkeypatch.setattr(fft, '_discover_doi', lambda *a, **k: '')
    monkeypatch.setattr(fft, '_PDF_DIR', tmp_path)
    monkeypatch.setattr(fft.time, 'sleep', lambda *a, **k: None)
    monkeypatch.setattr(fft.random, 'uniform', lambda *a, **k: 0)

    fft.download_missing_pdfs(json_path=json_path, max_articles=1)

    stored = json.loads(json_path.read_text())
    assert stored['https://example.com/a']['pdf'] == 't1.pdf'
    assert captured[0] == 'https://example.com/a'
    assert stored['https://example.com/a']['download_successful'] is True


def test_download_journal_pdfs(monkeypatch, tmp_path):
    data = {
        '1': {'title': 't1', 'link': 'L1', 'journal': 'Aging Cell', 'doi': 'https://doi.org/10.1234/x'},
        '2': {'title': 't2', 'link': 'L2', 'journal': 'Other'},
    }
    json_path = tmp_path / 'a.json'
    json_path.write_text(json.dumps(data))

    downloaded = []
    seen_doi = []

    def fake_download(entry, dest):
        downloaded.append(entry.title)
        seen_doi.append(getattr(entry, 'doi', None))
        p = dest / f"{entry.title}.pdf"
        p.write_bytes(b'd')
        return p

    monkeypatch.setattr(fft, '_download_pdf', fake_download)
    monkeypatch.setattr(fft, '_discover_doi', lambda *a, **k: '')
    monkeypatch.setattr(fft, '_PDF_DIR', tmp_path)
    monkeypatch.setattr(fft.time, 'sleep', lambda *a, **k: None)
    monkeypatch.setattr(fft.random, 'uniform', lambda *a, **k: 0)

    fft.download_journal_pdfs('Aging Cell', json_path=json_path, max_articles=1)

    stored = json.loads(json_path.read_text())
    assert stored['1']['pdf'] == 't1.pdf'
    assert '2' not in stored or 'pdf' not in stored['2']
    assert downloaded == ['t1']
    assert seen_doi == ['https://doi.org/10.1234/x']
    assert stored['1']['download_successful'] is True


def test_download_missing_pdfs_failure(monkeypatch, tmp_path):
    data = {'1': {'title': 't1', 'link': 'L1'}}
    json_path = tmp_path / 'a.json'
    json_path.write_text(json.dumps(data))

    monkeypatch.setattr(fft, '_download_pdf', lambda *a, **k: None)
    monkeypatch.setattr(fft, '_discover_doi', lambda *a, **k: '')
    monkeypatch.setattr(fft, '_PDF_DIR', tmp_path)
    monkeypatch.setattr(fft.time, 'sleep', lambda *a, **k: None)
    monkeypatch.setattr(fft.random, 'uniform', lambda *a, **k: 0)

    fft.download_missing_pdfs(json_path=json_path, max_articles=1)

    stored = json.loads(json_path.read_text())
    assert 'pdf' not in stored['1']
    assert stored['1']['download_successful'] is False


def test_download_journal_pdfs_skips_successful(monkeypatch, tmp_path):
    data = {
        '1': {'title': 't1', 'link': 'L1', 'journal': 'Aging Cell', 'download_successful': True},
        '2': {'title': 't2', 'link': 'L2', 'journal': 'Aging Cell'},
    }
    json_path = tmp_path / 'a.json'
    json_path.write_text(json.dumps(data))

    downloaded = []

    def fake_download(entry, dest):
        downloaded.append(entry.title)
        p = dest / f"{entry.title}.pdf"
        p.write_bytes(b'd')
        return p

    monkeypatch.setattr(fft, '_download_pdf', fake_download)
    monkeypatch.setattr(fft, '_discover_doi', lambda *a, **k: '')
    monkeypatch.setattr(fft, '_PDF_DIR', tmp_path)
    monkeypatch.setattr(fft.time, 'sleep', lambda *a, **k: None)
    monkeypatch.setattr(fft.random, 'uniform', lambda *a, **k: 0)

    fft.download_journal_pdfs('Aging Cell', json_path=json_path, max_articles=1)

    stored = json.loads(json_path.read_text())
    assert 'pdf' not in stored['1']
    assert stored['2']['pdf'] == 't2.pdf'
    assert downloaded == ['t2']
    assert stored['2']['download_successful'] is True


def test_download_journal_pdfs_failure(monkeypatch, tmp_path):
    data = {
        '1': {'title': 't1', 'link': 'L1', 'journal': 'Aging Cell'},
    }
    json_path = tmp_path / 'a.json'
    json_path.write_text(json.dumps(data))

    monkeypatch.setattr(fft, '_download_pdf', lambda *a, **k: None)
    monkeypatch.setattr(fft, '_discover_doi', lambda *a, **k: '')
    monkeypatch.setattr(fft, '_PDF_DIR', tmp_path)
    monkeypatch.setattr(fft.time, 'sleep', lambda *a, **k: None)
    monkeypatch.setattr(fft.random, 'uniform', lambda *a, **k: 0)

    fft.download_journal_pdfs('Aging Cell', json_path=json_path, max_articles=1)

    stored = json.loads(json_path.read_text())
    assert 'pdf' not in stored['1']
    assert stored['1']['download_successful'] is False


def test_pending_journal_articles(monkeypatch, tmp_path):
    data = {
        '1': {'title': 't1', 'journal': 'Aging Cell'},
        '2': {
            'title': 't2',
            'journal': 'Aging Cell',
            'pdf': 't2.pdf',
            'download_successful': True,
        },
    }
    json_path = tmp_path / 'a.json'
    json_path.write_text(json.dumps(data))

    assert fft.pending_journal_articles('Aging Cell', json_path=json_path)

    data['1']['download_successful'] = True
    json_path.write_text(json.dumps(data))

    assert not fft.pending_journal_articles('Aging Cell', json_path=json_path)


def test_extract_doi_from_url_ignores_citation_reference(monkeypatch):
    html = (
        '<meta name="citation_reference" '
        'content="B\xf6hm, M. et al. Endocrine controls of skin aging. '
        'Endocr. Rev. https://doi.org/10.1210/endrev/bnae034 (2025)." />\n'
        '<meta name="citation_doi" content="10.5555/main.doi" />'
    )

    class Resp:
        def __init__(self, text):
            self.text = text.encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            return self.text

        def geturl(self):
            return 'http://example.com'

    monkeypatch.setattr(fft.urllib.request, 'urlopen', lambda url: Resp(html))

    doi = fft._extract_doi_from_url('http://example.com')
    assert doi == 'https://doi.org/10.5555/main.doi'


def test_ocr_pdf(monkeypatch, tmp_path):
    from fpdf import FPDF

    pdf_path = tmp_path / 't.pdf'
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font('Arial', size=12)
    pdf.cell(40, 10, 'OCR Test')
    pdf.output(str(pdf_path))

    class FakeResp:
        class choice:
            def __init__(self):
                self.message = types.SimpleNamespace(content='OCR Test')
        choices = [choice()]

    class FakeClient:
        def __init__(self, *a, **kw):
            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(create=lambda **k: FakeResp())
            )

    monkeypatch.setattr(
        fft, 'openai', types.SimpleNamespace(OpenAI=lambda: FakeClient())
    )

    out = fft.ocr_pdf('t.pdf', pdf_dir=tmp_path)
    assert out == pdf_path.with_suffix('.txt')
    assert out.is_file()
    text = out.read_text().strip()
    assert 'OCR' in text
    archive = pdf_path.with_suffix('.zip')
    assert archive.is_file()
    import zipfile
    with zipfile.ZipFile(archive) as zf:
        assert zf.namelist()


def test_fetch_pdf_for_article(monkeypatch, tmp_path):
    data = {
        "message": {
            "items": [
                {
                    "DOI": "10.1234/abc",
                    "container-title": ["J"],
                    "title": ["T"],
                }
            ]
        }
    }

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            import json

            return json.dumps(data).encode()

    monkeypatch.setattr(fft.urllib.request, "urlopen", lambda url: Resp())

    def fake_download(entry, dest):
        p = dest / "x.pdf"
        p.write_bytes(b"d")
        return p

    monkeypatch.setattr(fft, "_download_pdf", fake_download)

    out = fft.fetch_pdf_for_article("T", dest_dir=tmp_path)
    assert out == tmp_path / "x.pdf"


def test_fetch_pdf_for_doi(monkeypatch, tmp_path):
    data = {
        "message": {
            "title": ["T"],
            "container-title": ["J"],
        }
    }

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            import json

            return json.dumps(data).encode()

    monkeypatch.setattr(fft.urllib.request, "urlopen", lambda url: Resp())

    def fake_download(entry, dest):
        assert entry.title == "T"
        assert entry.journal == "J"
        assert entry.doi == "https://doi.org/10.1234/abc"
        p = dest / "x.pdf"
        p.write_bytes(b"d")
        return p

    monkeypatch.setattr(fft, "_download_pdf", fake_download)

    out = fft.fetch_pdf_for_doi("10.1234/abc", dest_dir=tmp_path)
    assert out == tmp_path / "x.pdf"

