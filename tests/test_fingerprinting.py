import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import numpy as np
from fingerprinting import maccs_fingerprint


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
