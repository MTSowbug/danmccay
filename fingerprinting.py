from rdkit import Chem
from rdkit.Chem import MACCSkeys
import numpy as np


def maccs_fingerprint(smiles: str, n_bits: int = 167) -> np.ndarray:
    """Return a MACCS fingerprint for a molecule.

    Parameters
    ----------
    smiles : str
        SMILES representation of the molecule.
    n_bits : int
        Desired length of the fingerprint. MACCS fingerprints are
        naturally 167 bits long. If ``n_bits`` differs, the vector is
        padded with zeros or truncated accordingly.

    Returns
    -------
    numpy.ndarray
        Array of 0/1 integers representing the fingerprint.
    """
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return np.zeros(n_bits, dtype=int)

    fp = np.array(MACCSkeys.GenMACCSKeys(mol), dtype=int)

    if n_bits > len(fp):
        fp = np.pad(fp, (0, n_bits - len(fp)), constant_values=0)
    else:
        fp = fp[:n_bits]
    return fp
