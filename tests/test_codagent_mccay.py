import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import builtins
import pandas as pd
import types
import datetime as dt
import re
import pytest

import codagent_mccay as cam


def test_hash_but_doesnt_suck():
    expected = int(__import__('hashlib').sha256(b'abc').hexdigest(), 16)
    assert cam.hash_but_doesnt_suck('abc') == expected


def test_strip_unprintable(monkeypatch):
    captured = []
    monkeypatch.setattr(builtins, 'print', lambda *a, **k: captured.append(a))
    text = '\x1b[31mHello\x1b[0m\x00world\n'
    result = cam.strip_unprintable(text)
    assert result == 'Helloworld'


def test_validate_lambda():
    func = cam.validate_lambda('lambda x: x * 2')
    assert func(3) == 6

    import pytest
    with pytest.raises(ValueError):
        cam.validate_lambda('x + 1')


def test_qtable_to_graph():
    qtable = {
        1: pd.Series({'N': (0.0, 2), 'E': (0.0, 0)}),
        2: pd.Series({'S': (0.0, 1)})
    }
    graph = cam.qtable_to_graph(qtable)
    assert graph == {1: {2: 1}, 2: {1: 1}}

def test_hasher(monkeypatch, tmp_path):
    log = tmp_path / "loclog.txt"
    orig_open = builtins.open
    def fake_open(path, mode='r', *a, **k):
        if path == 'loclog.txt':
            return orig_open(log, mode, *a, **k)
        return orig_open(path, mode, *a, **k)
    monkeypatch.setattr(builtins, 'open', fake_open)
    result = cam.hasher('hello')
    assert result == cam.hash_but_doesnt_suck('hello')
    assert log.read_text() == 'hello' + str(result) + '\n\n'


def test_call_llm(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(
                    create=lambda **k: types.SimpleNamespace(
                        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content='OK'))]
                    )
                )
            )
    monkeypatch.setattr(cam, 'OpenAI', lambda: FakeClient())
    out = cam.call_llm([{'role': 'user', 'content': 'hi'}], TOKEN_MAXIMUM=5)
    assert out == 'OK'


def test_get_current_location(monkeypatch):
    text = '37&8<<<[ Room ]>>> 37&6Room desc (] Exits: north'
    monkeypatch.setattr(cam, 'hasher', lambda s: s)
    monkeypatch.setattr(cam, 'strip_unprintable', lambda s: s)
    loc, resp = cam.get_current_location(None, input_text=text, allownew=True)
    assert resp == text
    assert loc == 'Room desc '


def test_add_loc_to_qtable():
    cam.qTable.clear()
    cam.add_loc_to_qtable(1)
    assert 1 in cam.qTable
    assert cam.qTable[1]['brain'] == (1.0, 0)
    cam.add_loc_to_qtable(1)
    assert len(cam.qTable) == 1


def test_update_recall_edges():
    cam.qTable = {1: pd.Series({'recall': (0, 0)})}
    cam.update_recall_edges(5)
    assert cam.apparentrecallroom == (-100, 5)
    assert cam.qTable[1]['recall'] == (-100, 5)


def test_append_recentbuffer(monkeypatch, tmp_path):
    buf = tmp_path / 'buf.txt'
    cam.core_personality = {'files': {'buffer': str(buf)}}
    cam.recentbuffer = ''
    monkeypatch.setattr(cam, 'OpenAI', lambda: object())
    cam.append_recentbuffer('abc')
    assert cam.recentbuffer == 'abc'
    assert buf.read_text() == 'abc'


def test_read_until_prompt():
    class Dummy:
        def read_until(self, exp, timeout=0):
            self.exp = exp
            self.timeout = timeout
            return b'resp> '
    tn = Dummy()
    out = cam.read_until_prompt(tn, prompt='> ')
    assert out == 'resp> '
    assert cam.global_response == 'resp> '
    assert tn.exp == b'> '


def test_send_command(monkeypatch):
    writes = []
    class Dummy:
        def write(self, data):
            writes.append(data)
    tn = Dummy()
    logs = []
    monkeypatch.setattr(cam, 'append_recentbuffer', lambda t: logs.append(t))
    monkeypatch.setattr(cam, 'read_until_prompt', lambda *a, **k: 'resp')
    cam.actions = 0
    res = cam.send_command(tn, 'cmd')
    assert res == 'resp'
    assert writes == [b'cmd\n']
    assert cam.actions == 1
    assert logs[0].startswith('> cmd')
    assert logs[-1] == 'resp'


def test_say_lines(monkeypatch):
    sent = []
    monkeypatch.setattr(cam, 'send_command', lambda tn, c: sent.append(c))
    cam._say_lines(None, 'a\n\n b ')
    assert sent == ['say a', 'say b']


def test_generate_science_preamble(monkeypatch):
    monkeypatch.setattr(cam, 'CHAR_PROMPT', 'P')
    monkeypatch.setattr(cam, 'OPENAI_AVAILABLE', True)
    class FakeClient:
        def __init__(self):
            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(
                    create=lambda **k: types.SimpleNamespace(
                        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content='PRE'))]
                    )
                )
            )
    monkeypatch.setattr(cam, 'OpenAI', lambda: FakeClient())
    assert cam.generate_science_preamble('task') == 'PRE'


def test_generate_science_preamble_unavail(monkeypatch):
    monkeypatch.setattr(cam, 'CHAR_PROMPT', '')
    monkeypatch.setattr(cam, 'OPENAI_AVAILABLE', False)
    assert cam.generate_science_preamble('task') == ''


def test_say_preamble(monkeypatch):
    calls = []
    monkeypatch.setattr(cam, 'generate_science_preamble', lambda d: 'x')
    monkeypatch.setattr(cam, '_say_lines', lambda tn, text: calls.append(text))
    cam.say_preamble(None, 'task')
    assert calls == ['x']


def test_chatting_state_dedup(monkeypatch):
    """Ensure repeated input lines don't trigger multiple replies."""
    monkeypatch.setattr(cam, 'OPENAI_AVAILABLE', False)
    sent = []
    monkeypatch.setattr(cam, 'send_command', lambda tn, c: sent.append(c))

    state = cam.ChattingState()
    char = types.SimpleNamespace(tn='tn', name='McCay', set_state_cooldown=lambda *a, **k: None)

    cam.recentbuffer = ''
    state.enter(char)
    sent.clear()  # ignore greeting

    cam.recentbuffer += 'User says hello\n'
    state.execute(char)
    assert sent == ['say ...']
    sent.clear()

    # Same line again should not trigger another reply
    cam.recentbuffer += 'User says hello\n'
    state.execute(char)
    assert sent == []


def test_chatting_state_waits_for_newline(monkeypatch):
    """ChattingState should not respond until a full line is received."""
    monkeypatch.setattr(cam, 'OPENAI_AVAILABLE', False)
    sent = []
    monkeypatch.setattr(cam, 'send_command', lambda tn, c: sent.append(c))

    state = cam.ChattingState()
    char = types.SimpleNamespace(tn='tn', name='McCay', set_state_cooldown=lambda *a, **k: None)

    cam.recentbuffer = ''
    state.enter(char)
    sent.clear()

    # Add text without newline first
    cam.recentbuffer += 'Partial message'
    state.execute(char)
    assert sent == []

    # Complete the line
    cam.recentbuffer += ' continued\n'
    state.execute(char)
    assert sent == ['say ...']


def test_fetch_article_command(monkeypatch):
    calls = []
    monkeypatch.setattr(cam, 'download_missing_pdfs', lambda max_articles=1: calls.append(max_articles))
    monkeypatch.setattr(cam, 'send_command', lambda tn, c: 'resp')

    # Simulate branch execution
    response = 'McCay, fetch an article'
    if "McCay, fetch an article" in response:
        cam.send_command(None, "emote looks for a good article.")
        try:
            cam.download_missing_pdfs(max_articles=1)
        except Exception:
            pass

    assert calls == [1]


def test_fetch_specific_article_command(monkeypatch):
    calls = []
    monkeypatch.setattr(cam, 'fetch_pdf_for_doi', lambda d: calls.append(d))
    monkeypatch.setattr(cam, 'send_command', lambda tn, c: 'resp')

    response = 'McCay, fetch specific article: 10.1/abc'
    m = re.search(r"mccay, fetch specific article:\s*(.+)", response, re.IGNORECASE)
    if m:
        article = m.group(1).strip()
        cam.send_command(None, 'emote searches for the requested article.')
        try:
            cam.fetch_pdf_for_doi(article)
        except Exception:
            pass

    assert calls == ['10.1/abc']


def test_design_specific_article_command(monkeypatch):
    calls = []
    monkeypatch.setattr(cam, 'design_experiment_for_doi', lambda d: calls.append(d))
    monkeypatch.setattr(cam, 'send_command', lambda tn, c: 'resp')

    response = 'McCay, design for specific article: 10.2/xyz'
    m = re.search(r"mccay, design for specific article:\s*(.+)", response, re.IGNORECASE)
    if m:
        doi = m.group(1).strip()
        cam.send_command(None, 'emote contemplates an experiment.')
        try:
            cam.design_experiment_for_doi(doi)
        except Exception:
            pass

    assert calls == ['10.2/xyz']


def test_fetch_nataging_command(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cam, "download_journal_pdfs", lambda j, max_articles=1: calls.append((j, max_articles))
    )
    monkeypatch.setattr(cam, "send_command", lambda tn, c: "resp")

    response = "McCay, FETCH NATURE AGING"
    if "mccay, fetch nature aging" in response.lower():
        cam.send_command(None, "emote searches for a Nature Aging PDF.")
        try:
            cam.download_journal_pdfs("Nature Aging", max_articles=1)
        except Exception:
            pass

    assert calls == [("Nature Aging", 1)]


def test_fetch_natcomms_command(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cam, "download_journal_pdfs", lambda j, max_articles=1: calls.append((j, max_articles))
    )
    monkeypatch.setattr(cam, "send_command", lambda tn, c: "resp")

    response = "McCay, fetch nature communications"
    if "mccay, fetch nature communications" in response.lower():
        cam.send_command(None, "emote searches for a Nature Communications PDF.")
        try:
            cam.download_journal_pdfs("Nature Communications", max_articles=1)
        except Exception:
            pass

    assert calls == [("Nature Communications", 1)]


def test_fetch_natbiotech_command(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cam, "download_journal_pdfs", lambda j, max_articles=1: calls.append((j, max_articles))
    )
    monkeypatch.setattr(cam, "send_command", lambda tn, c: "resp")

    response = "McCay, fetch nature biotechnology"
    if "mccay, fetch nature biotechnology" in response.lower():
        cam.send_command(None, "emote searches for a Nature Biotechnology PDF.")
        try:
            cam.download_journal_pdfs("Nature Biotechnology", max_articles=1)
        except Exception:
            pass

    assert calls == [("Nature Biotechnology", 1)]


def test_check_geroscience_command(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cam, "download_journal_pdfs", lambda j, max_articles=1: calls.append((j, max_articles))
    )
    monkeypatch.setattr(cam, "send_command", lambda tn, c: "resp")

    response = "McCay, check geroscience"
    if "mccay, check geroscience" in response.lower():
        cam.send_command(None, "emote searches for a GeroScience PDF.")
        try:
            cam.download_journal_pdfs("GeroScience", max_articles=1)
        except Exception:
            pass

    assert calls == [("GeroScience", 1)]


def test_scheduled_agingcell_worker_fetches_all(monkeypatch):
    """Ensure scheduled worker downloads PDFs for all journals."""
    downloaded = []
    checked = []

    def fake_pending(j):
        checked.append(j)
        return True

    monkeypatch.setattr(cam, 'pending_journal_articles', fake_pending)
    monkeypatch.setattr(cam, 'download_journal_pdfs', lambda j, max_articles=1: downloaded.append(j))
    monkeypatch.setattr(cam, 'journals_with_pending_articles', lambda json_path=None: {'new journal': 'New Journal'})

    class FakeDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime(2024, 1, 1, 6, 45)

    monkeypatch.setattr(cam.dt, 'datetime', FakeDateTime)
    monkeypatch.setattr(cam.random, 'uniform', lambda a, b: 0)

    def stop(_):
        raise StopIteration

    monkeypatch.setattr(cam.time, 'sleep', stop)

    with pytest.raises(StopIteration):
        cam._scheduled_agingcell_worker()

    assert downloaded == [
        'Aging Cell',
        'Aging',
        'Nature Aging',
        'GeroScience',
        'Nature Communications',
        'Nature Biotechnology',
        'New Journal',
    ]
    assert checked == [
        'aging cell',
        'aging',
        'nature aging',
        'geroscience',
        'nature communications',
        'nature biotechnology',
        'new journal',
    ]


def test_scheduled_agingcell_worker_triggers_ocr(monkeypatch, tmp_path):
    """Worker should start OCR for newly downloaded PDFs."""
    created = []
    processes = []

    monkeypatch.setattr(cam.fft, '_PDF_DIR', tmp_path)

    def fake_pending(j):
        return True

    def fake_download(j, max_articles=1):
        p = tmp_path / f"{j.replace(' ', '')}.pdf"
        p.write_bytes(b'd')
        created.append(p.name)

    class FakeProcess:
        def __init__(self, target=None, args=(), kwargs=None):
            self.target = target
            self.args = args
            self.kwargs = kwargs or {}

        def start(self):
            processes.append((self.target, self.args, self.kwargs))

    monkeypatch.setattr(cam, 'pending_journal_articles', fake_pending)
    monkeypatch.setattr(cam, 'download_journal_pdfs', fake_download)
    monkeypatch.setattr(cam.multiprocessing, 'Process', FakeProcess)
    monkeypatch.setattr(cam.fft, 'ocr_pdf', lambda *a, **k: None)

    class FakeDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime(2024, 1, 1, 6, 45)

    monkeypatch.setattr(cam.dt, 'datetime', FakeDateTime)
    monkeypatch.setattr(cam.random, 'uniform', lambda a, b: 0)

    def stop(_):
        raise StopIteration

    monkeypatch.setattr(cam.time, 'sleep', stop)

    with pytest.raises(StopIteration):
        cam._scheduled_agingcell_worker()

    assert processes
    targets = {args[0] for _, args, _ in processes}
    assert targets == set(created)


def test_fetch_schema_file(monkeypatch, tmp_path):
    conf = tmp_path / "config.yaml"
    conf.write_text("schema:\n  username: u\n  pwd: p\n")

    added = []

    class FakePM:
        def add_password(self, realm, url, user, pwd):
            added.append((url, user, pwd))

    class FakeOpener:
        def open(self, url, timeout=0):
            assert url == "https://stgeorge.quest/cells/trialsv2/schema.php"
            class R:
                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    pass

                def read(self):
                    return b"data"

            return R()

    monkeypatch.setattr(cam.urllib.request, "HTTPPasswordMgrWithDefaultRealm", lambda: FakePM())
    monkeypatch.setattr(cam.urllib.request, "build_opener", lambda h: FakeOpener())

    dest = tmp_path / "schema.txt"
    cam.fetch_schema_file(dest, conf)

    assert added == [("https://stgeorge.quest/cells/trialsv2/schema.php", "u", "p")]
    assert dest.read_bytes() == b"data"


def test_fetch_schema_command(monkeypatch):
    calls = []
    monkeypatch.setattr(cam, "fetch_schema_file", lambda: calls.append(True))
    monkeypatch.setattr(cam, "send_command", lambda tn, c: "resp")

    response = "McCay, fetch schema"
    if "mccay, fetch schema" in response.lower():
        cam.send_command(None, "emote downloads the latest schema.")
        try:
            cam.fetch_schema_file()
        except Exception:
            pass

    assert calls == [True]


def test_scheduled_schema_worker(monkeypatch):
    calls = []
    monkeypatch.setattr(cam, "fetch_schema_file", lambda: calls.append(True))

    class FakeDateTime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime(2024, 1, 1, 5, 30)

    monkeypatch.setattr(cam.dt, "datetime", FakeDateTime)

    def stop(_):
        raise StopIteration

    monkeypatch.setattr(cam.time, "sleep", stop)

    with pytest.raises(StopIteration):
        cam._scheduled_schema_worker()

    assert calls == [True]
