"""Interpretation of a refined Solve Cell lattice (pure numpy; no Qt).

The refinement returns a primitive triclinic cell that is hard to compare with a
textbook cell: the same lattice can be written with many different axes. This
module turns one refined cell into things a person can read and check:

* ``volume``                -- independent of the choice of axes;
* ``reduced``               -- the Niggli-reduced cell (a unique form of the lattice);
* ``ladder``                -- the crystal system the lattice shows at a series of
                               tolerances, so "how close to higher symmetry" is explicit;
* ``matches``               -- known lattices ranked by how well some setting of them
                               reproduces the observed cell, with per-edge / per-angle /
                               volume deviations (always reported, never hidden behind a
                               pass/fail threshold).

Lattice only: a match says the *lattice* agrees, not that the atoms do. No
intensities are used, and supercell/subcell relations (index > 1) are not searched.

Reference values in :data:`REFERENCE_PHASES` are ambient-condition literature
lattices entered by hand; they are a convenience, not a database. Extra phases
(e.g. the Solve Cell material list) are accepted through ``extra_refs``.
"""
from __future__ import annotations

import functools
import itertools
import math
from typing import Optional, Sequence

import numpy as np

# name, conventional cell (a, b, c, alpha, beta, gamma), centring letter, space group label
REFERENCE_PHASES = (
    ("Corundum Al2O3 (ruby, sapphire)", (4.7587, 4.7587, 12.9929, 90.0, 90.0, 120.0), "R", "R-3c"),
    ("Spinel MgAl2O4", (8.0831, 8.0831, 8.0831, 90.0, 90.0, 90.0), "F", "Fd-3m"),
    ("Diamond", (3.5668, 3.5668, 3.5668, 90.0, 90.0, 90.0), "F", "Fd-3m"),
    ("Silicon", (5.4309, 5.4309, 5.4309, 90.0, 90.0, 90.0), "F", "Fd-3m"),
    ("CeO2", (5.4116, 5.4116, 5.4116, 90.0, 90.0, 90.0), "F", "Fm-3m"),
    ("LaB6", (4.1569, 4.1569, 4.1569, 90.0, 90.0, 90.0), "P", "Pm-3m"),
    ("MgO", (4.2112, 4.2112, 4.2112, 90.0, 90.0, 90.0), "F", "Fm-3m"),
    ("Gold", (4.0782, 4.0782, 4.0782, 90.0, 90.0, 90.0), "F", "Fm-3m"),
    ("Platinum", (3.9242, 3.9242, 3.9242, 90.0, 90.0, 90.0), "F", "Fm-3m"),
    ("Aluminium", (4.0495, 4.0495, 4.0495, 90.0, 90.0, 90.0), "F", "Fm-3m"),
    ("Copper", (3.6149, 3.6149, 3.6149, 90.0, 90.0, 90.0), "F", "Fm-3m"),
    ("NaCl", (5.6402, 5.6402, 5.6402, 90.0, 90.0, 90.0), "F", "Fm-3m"),
    ("Iron (bcc)", (2.8665, 2.8665, 2.8665, 90.0, 90.0, 90.0), "I", "Im-3m"),
    ("Tungsten", (3.1652, 3.1652, 3.1652, 90.0, 90.0, 90.0), "I", "Im-3m"),
    ("Rhenium", (2.7609, 2.7609, 4.4583, 90.0, 90.0, 120.0), "P", "P63/mmc"),
    ("Quartz (alpha SiO2)", (4.9134, 4.9134, 5.4052, 90.0, 90.0, 120.0), "P", "P3_221"),
)

# Primitive basis vectors (rows) in conventional fractional coordinates.
_CENTRING = {
    "P": np.eye(3),
    "F": np.array([[0, .5, .5], [.5, 0, .5], [.5, .5, 0]]),
    "I": np.array([[-.5, .5, .5], [.5, -.5, .5], [.5, .5, -.5]]),
    "C": np.array([[.5, .5, 0], [-.5, .5, 0], [0, 0, 1.]]),
    "A": np.array([[1., 0, 0], [0, .5, -.5], [0, .5, .5]]),
    "B": np.array([[.5, 0, -.5], [0, 1., 0], [.5, 0, .5]]),
    "R": np.array([[2 / 3, 1 / 3, 1 / 3], [-1 / 3, 1 / 3, 1 / 3], [-1 / 3, -2 / 3, 1 / 3]]),
}

#: (relative length %, angle deg) tolerances handed to ``midas_hkls.to_conventional``.
LADDER_TOLERANCES = ((0.2, 0.1), (0.5, 0.5), (1.0, 1.0), (2.0, 1.5), (3.0, 2.5), (5.0, 4.0))

#: score (rms of relative edge and angle error, in %) -> wording
VERDICTS = ((1.0, "matches"), (3.0, "close"), (6.0, "weak"))


def verdict(score: Optional[float]) -> str:
    if score is None or not np.isfinite(score):
        return "no match"
    for limit, word in VERDICTS:
        if score <= limit:
            return word
    return "no match"


# ── geometry ────────────────────────────────────────────────────────────────

def basis_rows(cell: Sequence[float]) -> np.ndarray:
    """Real-space basis (rows a, b, c) in the standard orientation: a || x, b in the xy plane."""
    a, b, c, al, be, ga = (float(v) for v in cell)
    ca, cb, cg = (math.cos(math.radians(x)) for x in (al, be, ga))
    sg = math.sin(math.radians(ga))
    cy = c * (ca - cb * cg) / sg
    cz2 = c * c - (c * cb) ** 2 - cy * cy
    return np.array([[a, 0.0, 0.0], [b * cg, b * sg, 0.0], [c * cb, cy, math.sqrt(max(cz2, 0.0))]])


def cell_from_rows(A: np.ndarray) -> tuple:
    """(a, b, c, alpha, beta, gamma) of the rows of ``A``."""
    A = np.asarray(A, float)
    L = np.linalg.norm(A, axis=1)

    def ang(i, j):
        return math.degrees(math.acos(float(np.clip(A[i] @ A[j] / (L[i] * L[j]), -1, 1))))
    return (float(L[0]), float(L[1]), float(L[2]), ang(1, 2), ang(0, 2), ang(0, 1))


def cell_volume(cell: Sequence[float]) -> float:
    return float(abs(np.linalg.det(basis_rows(cell))))


def parallelepiped(A: np.ndarray) -> tuple:
    """(corners (8,3), edges [(i, j) ...]) of the cell spanned by the rows of ``A``."""
    A = np.asarray(A, float)
    idx = [(i, j, k) for i in (0, 1) for j in (0, 1) for k in (0, 1)]
    corners = np.array(idx, float) @ A
    edges = [(p, q) for p in range(8) for q in range(p + 1, 8)
             if sum(x != y for x, y in zip(idx[p], idx[q])) == 1]
    return corners, edges


def primitive_basis(cell: Sequence[float], centring: str = "P") -> np.ndarray:
    """Primitive real-space basis (rows) of a conventional cell with the given centring.

    ``R`` is only expanded for hexagonal axes (alpha=beta=90, gamma=120); a rhombohedral
    cell given as ``R`` is already primitive.
    """
    c = (centring or "P").upper()[:1]
    if c not in _CENTRING:
        c = "P"
    if c == "R" and not (abs(cell[3] - 90) < 1e-3 and abs(cell[4] - 90) < 1e-3 and abs(cell[5] - 120) < 1e-3):
        c = "P"
    return _CENTRING[c] @ basis_rows(cell)


def _kabsch(P: np.ndarray, Q: np.ndarray) -> np.ndarray:
    """Proper rotation R minimising |P @ R.T - Q| for row-vector sets (3x3 each)."""
    H = P.T @ Q
    U, _, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U.T)) or 1.0
    return Vt.T @ np.diag([1, 1, d]) @ U.T


# ── lattice matching ────────────────────────────────────────────────────────

def match_lattice(cell_obs: Sequence[float], A_ref: np.ndarray, *,
                  len_tol: float = 0.12, n_coeff: int = 4) -> Optional[dict]:
    """Best setting of the reference lattice (primitive basis rows ``A_ref``) for ``cell_obs``.

    Enumerates short reference lattice vectors and keeps triplets that (a) are within
    ``len_tol`` of the observed edge lengths, (b) form a unimodular basis of the
    reference lattice (same volume per cell, index 1), and picks the triplet with the
    smallest rms of relative edge error and angle error. Returns ``None`` if no basis
    lies within ``len_tol``. Deviations are *observed minus reference*.
    """
    A_obs = basis_rows(cell_obs)
    e_obs = np.linalg.norm(A_obs, axis=1)
    A_ref = np.asarray(A_ref, float)
    rng = range(-n_coeff, n_coeff + 1)
    C = np.array([c for c in itertools.product(rng, repeat=3) if any(c)], float)
    V = C @ A_ref
    L = np.linalg.norm(V, axis=1)
    cands = [np.flatnonzero(np.abs(L - e) <= len_tol * e) for e in e_obs]
    if any(len(c) == 0 for c in cands):
        return None
    cos_obs = np.array([A_obs[1] @ A_obs[2], A_obs[0] @ A_obs[2], A_obs[0] @ A_obs[1]]) / np.array(
        [e_obs[1] * e_obs[2], e_obs[0] * e_obs[2], e_obs[0] * e_obs[1]])
    ang_obs = np.arccos(np.clip(cos_obs, -1, 1))
    best, best_cost = None, np.inf
    for i in cands[0]:
        for j in cands[1]:
            if j == i:
                continue
            for k in cands[2]:
                if k in (i, j):
                    continue
                if abs(round(np.linalg.det(C[[i, j, k]]))) != 1:
                    continue
                vs = V[[i, j, k]]
                ls = L[[i, j, k]]
                cs = np.array([vs[1] @ vs[2], vs[0] @ vs[2], vs[0] @ vs[1]]) / np.array(
                    [ls[1] * ls[2], ls[0] * ls[2], ls[0] * ls[1]])
                ang = np.arccos(np.clip(cs, -1, 1))
                dl = (e_obs - ls) / ls
                da = ang_obs - ang
                cost = math.sqrt(float(np.mean(dl ** 2) + np.mean(da ** 2)))
                if cost < best_cost:
                    best_cost, best = cost, (vs.copy(), C[[i, j, k]].copy(), ls, ang, dl, da)
    if best is None:
        return None
    vs, coeff, ls, ang, dl, da = best
    if np.linalg.det(vs) < 0:                       # a lattice is centrosymmetric: flip one axis
        vs[2] = -vs[2]
        coeff[2] = -coeff[2]
    R = _kabsch(vs, A_obs)
    v_ref = abs(float(np.linalg.det(A_ref)))
    v_obs = abs(float(np.linalg.det(A_obs)))
    return {
        "score": best_cost * 100.0,
        "ref_cell": cell_from_rows(vs),
        "ref_rows": vs @ R.T,                     # reference axes rotated onto the observed frame
        "ref_rows_native": vs.copy(),             # the same axes, unrotated (reference's own frame)
        "coefficients": coeff.astype(int).tolist(),
        "d_len_pct": (dl * 100.0).tolist(),
        "d_ang_deg": np.degrees(da).tolist(),
        "d_vol_pct": 100.0 * (v_obs - v_ref) / v_ref,
        "ref_volume": v_ref,
    }


def centring_from_space_group(number: int) -> str:
    try:
        from midas_hkls import list_space_groups
        for n, _hall, hm in list_space_groups():
            if int(n) == int(number):
                return hm[:1].upper()
    except Exception:
        pass
    return "P"


def extra_refs_from_materials(materials) -> list:
    """``(name, cell, centring, label)`` rows for Solve Cell ``Material`` objects."""
    out = []
    for m in materials or []:
        get = (lambda k, m=m: m[k]) if isinstance(m, dict) else (lambda k, m=m: getattr(m, k))
        try:
            sg = int(get("space_group"))
            out.append((str(get("name")), tuple(float(v) for v in get("cell")),
                        centring_from_space_group(sg), f"SG {sg}"))
        except Exception:
            continue
    return out


def rank_references(cell_obs: Sequence[float], extra_refs: Sequence = (), top: int = 6) -> list:
    """Known lattices ordered by ``score`` (rms edge+angle error, %), best first."""
    seen, rows = set(), []
    for name, cell, centring, label in list(REFERENCE_PHASES) + list(extra_refs):
        key = (name.lower(), tuple(round(v, 3) for v in cell))
        if key in seen:
            continue
        seen.add(key)
        try:
            m = match_lattice(cell_obs, primitive_basis(cell, centring))
        except Exception:
            m = None
        if m is None:
            continue
        rows.append({"name": name, "centring": centring, "space_group": label,
                     "conventional_cell": tuple(cell), **m, "verdict": verdict(m["score"])})
    rows.sort(key=lambda r: r["score"])
    return rows[:top]


# ── symmetry ladder / reduced cell ──────────────────────────────────────────

def reduced_cell(cell: Sequence[float]) -> Optional[tuple]:
    try:
        from midas_hkls import niggli_reduce
        return tuple(float(v) for v in niggli_reduce(tuple(cell)).cell)
    except Exception:
        return None


def symmetry_ladder(cell: Sequence[float]) -> list:
    """Crystal system the lattice shows at increasingly loose tolerances.

    Each row: ``tol_len_pct``, ``tol_ang_deg``, ``system``, ``cell`` (the conventional
    setting midas_hkls reports, *not* idealised to the system), ``centring_multiplicity``.
    Consecutive rows that give the same system are collapsed to the first (tightest).
    """
    try:
        from midas_hkls import to_conventional
    except Exception:
        return []
    rows, last = [], None
    for rel, ang in LADDER_TOLERANCES:
        try:
            cc = to_conventional(tuple(cell), rel_len=rel / 100.0, abs_ang=ang)
        except Exception:
            continue
        key = (cc.system, round(float(cc.volume_ratio), 3))
        if key == last:
            continue
        last = key
        rows.append({"tol_len_pct": rel, "tol_ang_deg": ang, "system": cc.system,
                     "cell": tuple(float(v) for v in cc.cell),
                     "centring_multiplicity": float(cc.volume_ratio)})
    return rows


@functools.lru_cache(maxsize=64)
def _closest_known_cached(cell: tuple) -> tuple:
    return tuple(rank_references(cell, (), top=1))


def closest_known(cell: Sequence[float]) -> Optional[dict]:
    """Best built-in reference match for ``cell`` (cached; built-in phases only)."""
    rows = _closest_known_cached(tuple(round(float(v), 6) for v in cell))
    return dict(rows[0]) if rows else None


# ── the report ──────────────────────────────────────────────────────────────

def build_report(result: dict, extra_refs: Sequence = (), top: int = 6) -> dict:
    """Readable summary of one refined domain (``refit_domain`` result dict)."""
    cell = tuple(float(v) for v in result["cell"])
    sig = result.get("cell_sigma")
    notes = []
    if sig is not None and any(s == s for s in sig):
        notes.append("± values are statistical only (from the fit covariance); they do not "
                     "include detector-geometry error, so they can be far smaller than the "
                     "true uncertainty.")
    matches = rank_references(cell, extra_refs, top)
    if matches:
        b = matches[0]
        notes.append(f"Closest known lattice: {b['name']} ({b['verdict']}, score {b['score']:.2f}%). "
                     "Lattice agreement only -- no intensities are compared.")
    else:
        notes.append("No known lattice has a basis within 12% of this cell's edges.")
    return {
        "cell": cell,
        "volume": cell_volume(cell),
        "reduced": reduced_cell(cell),
        "ladder": symmetry_ladder(cell),
        "matches": matches,
        "notes": notes,
    }


def summary_line(report: dict) -> str:
    m = (report.get("matches") or [None])[0]
    if not m:
        return "no known lattice matches"
    return (f"{m['name']}: {m['verdict']} (edges {max(abs(x) for x in m['d_len_pct']):.1f}% / "
            f"angles {max(abs(x) for x in m['d_ang_deg']):.1f}° / volume {m['d_vol_pct']:+.1f}%)")
