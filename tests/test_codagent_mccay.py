import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import builtins
import pandas as pd

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
