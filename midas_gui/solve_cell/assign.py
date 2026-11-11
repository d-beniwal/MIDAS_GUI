"""Re-assign spots to a solved domain after its lattice has been refined (pure numpy; no Qt).

Indexing assigns spots to a domain once, against the *seed* orientation, and then refines the
lattice from those spots only. Spots that were just outside the tolerance of the seed -- but sit
on the refined lattice -- stay unassigned, and the lattice-point view then shows predicted
crossings with a spot right there that belongs to nobody. :func:`reassign_spots` closes that loop:
re-test every spot the domain may own against the refined UB, refit from the enlarged set, repeat
until the set stops changing.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

TWO_PI = 2.0 * np.pi


def fractional_misfit(UB, g_2pi) -> tuple:
    """(max |hkl_float - round(hkl_float)| per spot, rounded hkl) for ``g_2pi`` (N, 3; 2π/d)."""
    UB = np.asarray(UB, float)
    hkl_f = np.linalg.solve(UB, (np.asarray(g_2pi, float) / TWO_PI).T).T
    hkl = np.round(hkl_f)
    return np.abs(hkl_f - hkl).max(axis=1), hkl


def reassign_spots(UB, g_2pi, available, *, tol: float = 0.15, sigma_g: Optional[float] = None,
                   min_reflections: int = 8, max_iter: int = 6) -> dict:
    """Spots of ``available`` (bool mask over ``g_2pi`` rows) that fit ``UB`` within ``tol``.

    Iterates assign -> ``pipeline.refit_domain`` -> reassign. ``available`` must already exclude
    spots owned by other domains. Returns ``{"mask", "hkl" (for mask rows), "result" (refit dict or
    None), "n_iter", "converged", "history" [n assigned per iteration]}``; ``result`` is ``None`` and
    ``mask`` the plain first-pass assignment when fewer than ``min_reflections`` spots fit.
    """
    from .pipeline import refit_domain

    g_2pi = np.asarray(g_2pi, float)
    available = np.asarray(available, bool)
    cur_UB = np.asarray(UB, float)
    prev, result, history = None, None, []
    mask = np.zeros(len(g_2pi), bool)
    hkl_all = np.zeros_like(g_2pi)
    for it in range(1, max_iter + 1):
        frac, hkl_all = fractional_misfit(cur_UB, g_2pi)
        mask = available & (frac < tol) & (np.abs(hkl_all).sum(axis=1) > 0)
        history.append(int(mask.sum()))
        if mask.sum() < min_reflections:
            return {"mask": mask, "hkl": hkl_all[mask].astype(int), "result": None, "n_iter": it,
                    "converged": False, "history": history}
        if prev is not None and np.array_equal(mask, prev):
            return {"mask": mask, "hkl": hkl_all[mask].astype(int), "result": result, "n_iter": it,
                    "converged": True, "history": history}
        result = refit_domain(hkl_all[mask].astype(int), g_2pi[mask], sigma_g)
        cur_UB = np.asarray(result["UB"], float)
        prev = mask
    frac, hkl_all = fractional_misfit(cur_UB, g_2pi)
    mask = available & (frac < tol) & (np.abs(hkl_all).sum(axis=1) > 0)
    result = refit_domain(hkl_all[mask].astype(int), g_2pi[mask], sigma_g) if mask.sum() >= min_reflections else result
    return {"mask": mask, "hkl": hkl_all[mask].astype(int), "result": result, "n_iter": max_iter,
            "converged": False, "history": history}
