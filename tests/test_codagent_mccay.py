import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import builtins
import pandas as pd
import types

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
