import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import numpy as np
from fingerprinting import (
    maccs_fingerprint,
    topological_fingerprint,
    morgan_fingerprint,
)


def test_maccs_fingerprint_basic():
    fp = maccs_fingerprint('CCO', 167)
    assert isinstance(fp, np.ndarray)
    assert len(fp) == 167
    assert fp.dtype == int
    assert fp.sum() > 0


def test_maccs_fingerprint_truncate_pad():
    fp_short = maccs_fingerprint('CCO', 10)
    assert len(fp_short) == 10

    fp_long = maccs_fingerprint('CCO', 200)
    assert len(fp_long) == 200
    assert np.all(fp_long[167:] == 0)


def test_topological_fingerprint_basic():
    fp = topological_fingerprint('CCO', 64)
    assert isinstance(fp, np.ndarray)
    assert len(fp) == 64
    assert fp.dtype == int
    assert fp.sum() > 0


def test_topological_fingerprint_various_lengths():
    fp_small = topological_fingerprint('CCO', 32)
    fp_large = topological_fingerprint('CCO', 128)
    assert len(fp_small) == 32
    assert len(fp_large) == 128


def test_morgan_fingerprint_basic():
    fp = morgan_fingerprint('CCO', 128)
    assert isinstance(fp, np.ndarray)
    assert len(fp) == 128
    assert fp.dtype == int
    assert fp.sum() > 0


def test_morgan_fingerprint_various_lengths():
    fp_small = morgan_fingerprint('CCO', 64)
    fp_large = morgan_fingerprint('CCO', 256)
    assert len(fp_small) == 64
    assert len(fp_large) == 256
