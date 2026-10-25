"""Basis-aware comparison of indexed domains (V13). Pure numpy/pandas.

Convention: ``UB`` is in 1/d with ``g = UB @ hkl`` (columns = reciprocal basis
a*, b*, c* in the sample frame). Direct lattice vectors are the columns of
``A = inv(UB).T`` and the metric is ``G = A.T @ A``.

Two lattices are compared in this order (never orientation first):

1. find every integer change of basis ``M`` (det +1, entries in [-2, 2]) with
   ``M.T @ G2 @ M ~= G1`` (relative metric tolerance). If none exists the
   metrics differ ("different metric");
2. for each such ``M`` the relabelled lattice ``A2' = A2 @ M`` is a setting of
   lattice 2 commensurate with lattice 1; ``R = polar(A1 @ inv(A2'))`` is the
   proper rotation between them. The minimum rotation angle over all matching
   ``M`` is the misorientation modulo the LATTICE symmetry (the matching M
   include every proper lattice automorphism), without assuming a crystal
   point group. Angle ~0 -> same lattice relabelled.

Metric-only: it cannot tell apart orientations related by a symmetry of the
lattice that the crystal structure does NOT have (that needs intensities), and
the conclusion thresholds are heuristics, exposed as arguments.
"""
from __future__ import annotations

import itertools
from functools import lru_cache
from typing import Optional

import numpy as np
import pandas as pd

CONCLUSIONS = ("same lattice relabeled", "compatible distinct orientation", "different metric",
               "insufficient evidence")
METRIC_TOL = 0.03          # max |G2'-G1| relative to the largest G1 diagonal
SAME_ANGLE_DEG = 1.0       # below this the settings are the same lattice
MIN_REFLECTIONS = 8        # fewer assigned reflections than this -> insufficient evidence


@lru_cache(maxsize=1)
def unimodular_matrices(limit: int = 2) -> np.ndarray:
    """All integer 3x3 matrices with entries in [-limit, limit] and det +1."""
    vals = np.arange(-limit, limit + 1)
    grid = np.array(list(itertools.product(vals, repeat=9)), dtype=np.int8).reshape(-1, 3, 3)
    det = np.rint(np.linalg.det(grid.astype(float))).astype(int)
    return grid[det == 1].astype(float)


def direct_matrix(UB) -> np.ndarray:
    return np.linalg.inv(np.asarray(UB, float)).T


def metric(UB) -> np.ndarray:
    A = direct_matrix(UB)
    return A.T @ A


def polar_rotation(X: np.ndarray) -> np.ndarray:
    U, _, Vt = np.linalg.svd(X)
    R = U @ Vt
    if np.linalg.det(R) < 0:                    # left-handed input: fix the weakest axis
        U[:, -1] *= -1
        R = U @ Vt
    return R


def rotation_angle_deg(R: np.ndarray) -> float:
    # atan2 form: accurate near 0 and 180 deg, where arccos((tr-1)/2) loses digits
    v = 0.5 * np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])
    return float(np.degrees(np.arctan2(np.linalg.norm(v), 0.5 * (np.trace(R) - 1.0))))


def compare_lattices(UB1, UB2, *, tol: float = METRIC_TOL) -> dict:
    """Best basis change from lattice 2 onto lattice 1 and the resulting misorientation.

    Returns ``{"metric_match": bool, "angle_deg": float|nan, "M": 3x3 or None,
    "metric_error": float, "n_matches": int}``; ``M`` relabels lattice 2's direct
    basis (``A2' = A2 @ M``), so lattice-2 HKLs map by ``hkl1 = inv(M).T @ hkl2``.
    """
    A1, A2 = direct_matrix(UB1), direct_matrix(UB2)
    G1, G2 = A1.T @ A1, A2.T @ A2
    Ms = unimodular_matrices()
    G2p = np.einsum("nji,jk,nkl->nil", Ms, G2, Ms)       # M^T G2 M
    err = np.abs(G2p - G1).reshape(len(Ms), -1).max(axis=1) / np.diag(G1).max()
    ok = np.flatnonzero(err < tol)
    if not len(ok):
        return {"metric_match": False, "angle_deg": float("nan"), "M": None,
                "metric_error": float(err.min()), "n_matches": 0}
    best = None
    for i in ok:
        R = polar_rotation(A1 @ np.linalg.inv(A2 @ Ms[i]))
        ang = rotation_angle_deg(R)
        if best is None or ang < best[0] - 1e-9 or (abs(ang - best[0]) <= 1e-9 and err[i] < best[2]):
            best = (ang, Ms[i], float(err[i]))
    return {"metric_match": True, "angle_deg": best[0], "M": best[1], "metric_error": best[2],
            "n_matches": int(len(ok))}


def conclude(cmp: dict, n_a: int, n_b: int, *, same_angle_deg: float = SAME_ANGLE_DEG,
             min_reflections: int = MIN_REFLECTIONS) -> str:
    if min(n_a, n_b) < min_reflections:
        return "insufficient evidence"
    if not cmp["metric_match"]:
        return "different metric"
    return ("same lattice relabeled" if cmp["angle_deg"] < same_angle_deg
            else "compatible distinct orientation")


def cross_fit_count(UB, g_obs: np.ndarray, hkl_tol: float = 0.1) -> int:
    """How many of ``g_obs`` (n,3; same 1/d*2pi units as spot q... pass q/(2*pi))
    index to integer HKL within ``hkl_tol`` under ``UB`` -- evidence, not a claim."""
    if not len(g_obs):
        return 0
    h = np.linalg.solve(np.asarray(UB, float), np.asarray(g_obs, float).T).T
    return int((np.abs(h - np.rint(h)).max(axis=1) < hkl_tol).sum())


def entries_from_state(domains: list, ab, candidate_df: Optional[pd.DataFrame]) -> list:
    """Comparable lattices: every refined domain plus (separately labelled) the
    unrefined ab-initio seed. ``domains`` items carry ``label, method, result,
    claimed_index, panel_ids``; ``ab`` has ``UB, indexed_mask, cell, success``."""
    out = []
    for i, d in enumerate(domains):
        r = d["result"]
        if r.get("UB") is None:
            continue
        pids = d.get("panel_ids")
        out.append({"key": f"domain_{i + 1:02d}", "label": d["label"], "kind": "domain", "method": d["method"],
                    "UB": np.asarray(r["UB"], float), "cell": r.get("cell"), "cell_sigma": r.get("cell_sigma"),
                    "n_reflections": int(r.get("n_reflections", len(d["claimed_index"]))),
                    "rms_drlv": r.get("rms_drlv"), "conventional_cell": r.get("conventional_cell"),
                    "conventional_system": r.get("conventional_system"),
                    "claimed_index": np.asarray(d["claimed_index"]),
                    "panel_counts": (pd.Series(np.asarray(pids)).value_counts().sort_index().to_dict()
                                     if pids is not None else {})})
    if ab is not None and getattr(ab, "success", False) and getattr(ab, "UB", None) is not None:
        mask = np.asarray(ab.indexed_mask)
        idx = np.flatnonzero(mask)
        out.append({"key": "ab_initio_seed", "label": "Ab-initio seed (unrefined)", "kind": "candidate",
                    "method": "ab-initio", "UB": np.asarray(ab.UB, float), "cell": list(ab.cell),
                    "cell_sigma": list(ab.cell_sigma) if ab.cell_sigma is not None else None,
                    "n_reflections": int(ab.n_indexed), "rms_drlv": (ab.diagnostics or {}).get("rms_drlv"),
                    "conventional_cell": None, "conventional_system": None, "claimed_index": idx,
                    "panel_counts": ({} if candidate_df is None or "panel_id" not in candidate_df
                                     else candidate_df.iloc[idx]["panel_id"].value_counts().sort_index().to_dict())})
    return out


def compare_all(entries: list, candidate_df: Optional[pd.DataFrame] = None, **kw) -> dict:
    """Pairwise tables for ``entries``: ``domains`` (one row per entry) and ``pairs``
    (every ordered i<j pair: metric match, minimum angle, basis change, cross-fit
    counts, conclusion)."""
    rows = []
    for e in entries:
        vol = float(abs(np.linalg.det(direct_matrix(e["UB"]))))
        cell = list(e["cell"]) if e.get("cell") is not None else [np.nan] * 6
        sig = list(e["cell_sigma"]) if e.get("cell_sigma") is not None else [np.nan] * 6
        rows.append({"key": e["key"], "label": e["label"], "kind": e["kind"], "method": e["method"],
                     "n_reflections": e["n_reflections"], "volume_A3": vol,
                     **{f"cell_{n}": v for n, v in zip("a b c alpha beta gamma".split(), cell)},
                     **{f"sigma_{n}": v for n, v in zip("a b c alpha beta gamma".split(), sig)},
                     "rms_drlv": e.get("rms_drlv") if e.get("rms_drlv") is not None else np.nan,
                     "conventional_system": e.get("conventional_system") or "",
                     "conventional_cell": (", ".join(f"{x:.4g}" for x in e["conventional_cell"])
                                           if e.get("conventional_cell") else ""),
                     "panel_counts": ", ".join(f"P{k}:{v}" for k, v in sorted(e["panel_counts"].items()))})
    pairs = []
    g_all = None
    if candidate_df is not None and len(candidate_df):
        g_all = candidate_df[["qsample_x", "qsample_y", "qsample_z"]].to_numpy() / (2 * np.pi)
    for i, j in itertools.combinations(range(len(entries)), 2):
        a, b = entries[i], entries[j]
        c = compare_lattices(a["UB"], b["UB"], tol=kw.get("tol", METRIC_TOL))
        pairs.append({"a": a["key"], "b": b["key"], "metric_match": c["metric_match"],
                      "angle_deg": c["angle_deg"], "metric_error": c["metric_error"], "n_matches": c["n_matches"],
                      "M": "" if c["M"] is None else " ".join(str(int(x)) for x in c["M"].ravel()),
                      "b_spots_fit_by_a": cross_fit_count(a["UB"], g_all[b["claimed_index"]]) if g_all is not None else -1,
                      "a_spots_fit_by_b": cross_fit_count(b["UB"], g_all[a["claimed_index"]]) if g_all is not None else -1,
                      "conclusion": conclude(c, a["n_reflections"], b["n_reflections"],
                                             same_angle_deg=kw.get("same_angle_deg", SAME_ANGLE_DEG),
                                             min_reflections=kw.get("min_reflections", MIN_REFLECTIONS))})
    return {"domains": pd.DataFrame(rows), "pairs": pd.DataFrame(pairs)}
