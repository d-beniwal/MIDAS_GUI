"""Screen candidate phases against a pooled g-vector set, with same-volume decoys (pure numpy).

For each candidate lattice, the best random-orientation match count (the same search the
known-structure index uses) is compared with the best count of ``n_decoys`` random lattices of
the SAME volume. A scrambled-direction null is far too easy to beat -- real spots are not random,
and on the ruby data even quartz and spinel beat it at z~50 -- while decoys of equal volume
have the same hkl density and therefore the same chance of fitting by luck.

A phase is "supported" only if it beats every decoy and is z >= 4 above their mean; that is a
statement about the LATTICE (distances and angles), not about atoms.
"""
from __future__ import annotations

from typing import Callable, Optional, Sequence

import numpy as np

from . import cell_report as cr

MIN_EDGE, MAX_EDGE = 3.0, 9.0


def random_decoy_cells(volume: float, n: int, rng: np.random.Generator) -> list:
    """Random triclinic cells of the given volume (reasonable edges/angles)."""
    out = []
    edge = volume ** (1 / 3)
    guard = 0
    while len(out) < n and guard < 100 * n:
        guard += 1
        e = rng.uniform(0.75, 1.3, 3) * edge
        ang = rng.uniform(55, 125, 3)
        try:
            v = cr.cell_volume((*e, *ang))
        except Exception:
            continue
        if not np.isfinite(v) or v < 0.2 * volume:
            continue
        s = (volume / v) ** (1 / 3)
        cell = (e[0] * s, e[1] * s, e[2] * s, *ang)
        v2 = cr.cell_volume(cell)
        if np.isfinite(v2) and abs(v2 - volume) < 1e-6 * volume:
            out.append(cell)
    return out


def best_match_count(cell, g_1d: np.ndarray, *, tol: float, n_search: int, seed: int = 0):
    from .pipeline import _bmatrix_from_cell, _random_rotation_search
    B0 = _bmatrix_from_cell(cell)
    return _random_rotation_search(B0, g_1d, n_search, np.random.default_rng(seed), tol)


def screen_phases(g_2pi, phases: Sequence = (), *, tol: float = 0.1, n_search: int = 100_000,
                  n_decoys: int = 20, seed: int = 0,
                  on_progress: Optional[Callable[[str, dict], None]] = None,
                  cancel: Optional[Callable[[], bool]] = None) -> list:
    """Rank ``phases`` (``(name, conventional cell, centring, label)`` rows; default
    :data:`cell_report.REFERENCE_PHASES`) by how much better than same-volume decoys they index
    ``g_2pi`` (N, 3; 2π/d) -- best first."""
    g = np.asarray(g_2pi, float) / (2 * np.pi)
    phases = list(phases) or list(cr.REFERENCE_PHASES)
    rng = np.random.default_rng(seed)
    rows = []
    for k, (name, cell, centring, label) in enumerate(phases):
        if cancel and cancel():
            break
        prim = cr.cell_from_rows(cr.primitive_basis(cell, centring))
        vol = cr.cell_volume(prim)
        n, UB = best_match_count(prim, g, tol=tol, n_search=n_search, seed=seed + k)
        dec = np.array([best_match_count(c, g, tol=tol, n_search=n_search, seed=seed + 1000 + i)[0]
                        for i, c in enumerate(random_decoy_cells(vol, n_decoys, rng))], float)
        mean, sd = float(dec.mean()), float(dec.std())
        z = (n - mean) / max(sd, 1.0)
        supported = bool(n > dec.max() and z >= 4.0)
        row = {"name": name, "space_group": label, "centring": centring, "primitive_cell": prim,
               "volume": vol, "best_n": int(n), "n_spots": int(len(g)), "fraction": n / max(len(g), 1),
               "decoy_mean": mean, "decoy_sd": sd, "decoy_max": int(dec.max()), "n_decoys": int(len(dec)),
               "z": float(z), "supported": supported,
               "verdict": "supported" if supported else ("weak" if z >= 2.0 else "not distinguished"),
               "UB": UB}
        rows.append(row)
        if on_progress:
            on_progress("phase", {k: v for k, v in row.items() if k not in ("UB", "primitive_cell")})
    rows.sort(key=lambda r: -r["z"])
    return rows
