import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import builtins
import types
import pandas as pd

import codagent_mccay as cam


def test_hash_but_doesnt_suck():
    val = cam.hash_but_doesnt_suck('test')
    import hashlib
    expected = int(hashlib.sha256('test'.encode('utf-8')).hexdigest(), 16)
    assert val == expected


def test_hasher_writes(tmp_path, monkeypatch):
    log = tmp_path / 'log.txt'
    real_open = builtins.open

    def fake_open(path, mode='r', *args, **kwargs):
        if path == 'loclog.txt':
            return real_open(log, mode, *args, **kwargs)
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, 'open', fake_open)
    result = cam.hasher('room')
    assert result == cam.hash_but_doesnt_suck('room')
    assert log.read_text() == 'room' + str(result) + "\n\n"


def test_strip_unprintable():
    text = '\x1b[31mRed\x1b[0m\nabc\x07'
    assert cam.strip_unprintable(text) == 'Redabc'


def test_validate_lambda():
    func = cam.validate_lambda('lambda x: x + 1')
    assert func(1) == 2
    import pytest
    with pytest.raises(ValueError):
        cam.validate_lambda('x + 1')


def test_call_llm(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(
                    create=lambda **kw: types.SimpleNamespace(
                        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content='OK'))]
                    )
                )
            )
    monkeypatch.setattr(cam, 'OpenAI', lambda: FakeClient())
    msg = [{'role': 'user', 'content': 'hi'}]
    out = cam.call_llm(msg, TOKEN_MAXIMUM=5)
    assert out == 'OK'


def test_call_llm_rate_limit(monkeypatch):
    class DummyError(Exception):
        pass

    class FakeClient:
        class C:
            def create(self, **kw):
                raise DummyError('x')
        def __init__(self):
            self.chat = types.SimpleNamespace(completions=self.C())

    monkeypatch.setattr(cam, 'OpenAI', lambda: FakeClient())
    monkeypatch.setattr(cam, 'anthropic', types.SimpleNamespace(RateLimitError=DummyError, BadRequestError=DummyError))
    msg = [{'role': 'user', 'content': 'hi'}]
    out = cam.call_llm(msg)
    assert out == 'YES'


def test_get_current_location_known(monkeypatch):
    sample = 'foo 37&8<<<[ The Room ]>>>37&6The Room desc (] Exits: north'
    monkeypatch.setattr(cam, 'hasher', lambda s: 42)
    monkeypatch.setattr(cam, 'strip_unprintable', lambda s: s)
    cam.qTable = {42: pd.Series([], dtype=object)}
    loc, resp = cam.get_current_location(None, input_text=sample)
    assert loc == 42
    assert resp == sample


def test_get_current_location_unknown(monkeypatch):
    sample = 'foo 37&8<<<[ The Room ]>>>37&6The Room desc (] Exits: north'
    monkeypatch.setattr(cam, 'hasher', lambda s: 99)
    monkeypatch.setattr(cam, 'strip_unprintable', lambda s: s)
    cam.qTable = {}
    loc, resp = cam.get_current_location(None, input_text=sample)
    assert loc == 0 and resp == 0


def test_get_current_location_allow_new(monkeypatch):
    sample = 'foo 37&8<<<[ The Room ]>>>37&6The Room desc (] Exits: north'
    monkeypatch.setattr(cam, 'hasher', lambda s: 88)
    monkeypatch.setattr(cam, 'strip_unprintable', lambda s: s)
    cam.qTable = {}
    loc, resp = cam.get_current_location(None, input_text=sample, allownew=True)
    assert loc == 88 and resp == sample


def test_add_loc_to_qtable():
    cam.qTable = {}
    cam.apparentrecallroom = (-100, 1)
    cam.add_loc_to_qtable(5)
    assert 5 in cam.qTable
    ser = cam.qTable[5]
    assert 'recall' in ser
    assert ser['recall'] == cam.apparentrecallroom


def test_update_recall_edges():
    cam.qTable = {
        1: pd.Series({'north': (0.0, 2)}, name=1),
        2: pd.Series({'south': (0.0, 0), 'recall': (0.0, 1)}, name=2),
    }
    cam.update_recall_edges(99)
    for series in cam.qTable.values():
        if 'recall' in series.index:
            assert series['recall'] == (-100, 99)


def test_qtable_to_graph():
    qtable = {
        1: pd.Series({'N': (0.0, 2), 'E': (0.0, 0)}),
        2: pd.Series({'S': (0.0, 1)})
    }
    graph = cam.qtable_to_graph(qtable)
    assert graph == {1: {2: 1}, 2: {1: 1}}
