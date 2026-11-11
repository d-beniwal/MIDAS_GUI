"""Per-panel omega offsets for a pooled multi-panel Solve Cell run (pure numpy; no Qt).

Each panel is a separate rotation scan, and each scan's frame->omega mapping can carry its own
small error (trigger latency, stage start-up, an auto-detected first frame that is off by a few
frames). Pooling treats all panels as one crystal, so a per-panel error of a degree or two moves
that panel's reciprocal-space points by far more than the indexing tolerance, and a correct lattice
indexes badly. On the real Oct-2026 ruby data the offsets were about +1.9, -4.1, -2.6, -1.0 degrees
on four of six panels.

:func:`fit_panel_omega_offsets` finds those offsets given a lattice HYPOTHESIS (a cell; its
orientation is fitted). It is a hypothesis test, not a free fit: the offsets are only meaningful
if the cell is right, and a wrong cell can also gain from them (five extra parameters), so judge a
phase by comparing against decoys that were aligned the same way (see ``phase_screen``).

Gauge: a common shift of every panel is the same as a rotation of the crystal about the omega axis,
so offsets are reported relative to the panel with the most spots (offset 0).
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from .assign import fractional_misfit

TWO_PI = 2.0 * np.pi


def _g_1d(qlab, omega_deg, shift_deg):
    from .geometry_adapter import qlab_to_sample
    return qlab_to_sample(qlab, np.asarray(omega_deg, float) + shift_deg) / TWO_PI


def _fit_rotation(B0, g, tol, UB, n_iter=6):
    """Rotation-only refinement (fixed cell shape B0): best UB = R @ B0 for the spots within tol."""
    for _ in range(n_iter):
        frac, h = fractional_misfit(UB, g * TWO_PI)
        ok = frac < tol
        if ok.sum() < 3:
            break
        H = (B0 @ h[ok].T).T
        U, _s, Vt = np.linalg.svd(H.T @ g[ok])
        d = np.sign(np.linalg.det(Vt.T @ U.T)) or 1.0
        UB = Vt.T @ np.diag([1, 1, d]) @ U.T @ B0
    return UB


def _score(UB, g, tol):
    frac, _ = fractional_misfit(UB, g * TWO_PI)
    f = np.clip(frac, 0, 0.5)
    return int((frac < tol).sum()) - 0.01 * float(f.mean())


def fit_panel_omega_offsets(qlab, omega_deg, panel_id, B0, *, tol: float = 0.08,
                            max_shift_deg: float = 8.0, step_deg: float = 0.1, n_iter: int = 4,
                            n_search: int = 300_000, seed: int = 0,
                            UB0: Optional[np.ndarray] = None) -> dict:
    """Fit one omega offset per panel + the crystal orientation for the cell ``B0`` (1/d columns).

    ``qlab``/``omega_deg``/``panel_id`` are the spot table's own columns (lab-frame q in 2π/d, the
    omega each spot was ingested at). ``UB0`` skips the random orientation search when a good
    orientation is already known. Returns ``offsets`` ({panel: degrees, relative to the best-populated
    panel}), ``UB``, ``n_matched`` / ``rms`` before and after (at ``tol``), ``per_panel`` rows and
    ``reference_panel``.
    """
    qlab = np.asarray(qlab, float)
    om0 = np.asarray(omega_deg, float)
    pid = np.asarray(panel_id)
    panels = sorted(int(p) for p in set(pid))
    B0 = np.asarray(B0, float)
    shifts = {p: 0.0 for p in panels}
    grid = np.arange(-max_shift_deg, max_shift_deg + 1e-9, step_deg)

    def g_all():
        out = np.empty_like(qlab)
        for p in panels:
            m = pid == p
            out[m] = _g_1d(qlab[m], om0[m], shifts[p])
        return out

    g = g_all()
    if UB0 is None:
        from .pipeline import _random_rotation_search
        _n, UB = _random_rotation_search(B0, g, n_search, np.random.default_rng(seed), max(tol, 0.15))
    else:
        UB = np.asarray(UB0, float)
    UB = _fit_rotation(B0, g, max(tol, 0.15), UB)
    n_before, rms_before = _stats(UB, g, tol)
    per_panel_before = _per_panel(UB, g, pid, panels, tol)

    for _ in range(n_iter):
        for p in panels:
            m = pid == p
            if m.sum() < 3:
                continue
            # all candidate shifts for this panel in ONE batched geometry call
            q_rep = np.tile(qlab[m], (len(grid), 1))
            om_rep = (om0[m][None, :] + grid[:, None]).ravel()
            from .geometry_adapter import qlab_to_sample
            gg = (qlab_to_sample(q_rep, om_rep) / TWO_PI).reshape(len(grid), m.sum(), 3)
            best, best_s = None, 0.0
            for k, s in enumerate(grid):
                sc = _score(UB, gg[k], tol)
                if best is None or sc > best:
                    best, best_s = sc, float(s)
            shifts[p] = best_s
        g = g_all()
        UB = _fit_rotation(B0, g, tol, UB)

    counts = {p: int((pid == p).sum()) for p in panels}
    ref = max(panels, key=lambda p: counts[p])
    rel = {p: shifts[p] - shifts[ref] for p in panels}
    shifts = {p: rel[p] for p in panels}            # gauge fix: reference panel at 0
    g = g_all()
    UB = _fit_rotation(B0, g, tol, UB)
    n_after, rms_after = _stats(UB, g, tol)
    return {"offsets": {p: round(float(v), 3) for p, v in rel.items()}, "reference_panel": int(ref),
            "UB": UB, "tol": tol, "n_spots": int(len(g)),
            "n_matched_before": n_before, "n_matched_after": n_after,
            "rms_before": rms_before, "rms_after": rms_after,
            "per_panel": _per_panel(UB, g, pid, panels, tol, before=per_panel_before)}


def _stats(UB, g, tol):
    frac, h = fractional_misfit(UB, g * TWO_PI)
    ok = frac < tol
    if not ok.any():
        return 0, float("nan")
    d = g[ok] - (UB @ h[ok].T).T
    return int(ok.sum()), float(np.sqrt((d ** 2).sum(1).mean()))


def _per_panel(UB, g, pid, panels, tol, before=None):
    frac, h = fractional_misfit(UB, g * TWO_PI)
    rows = []
    for p in panels:
        m = pid == p
        ok = m & (frac < tol)
        d = g[ok] - (UB @ h[ok].T).T if ok.any() else np.zeros((0, 3))
        rows.append({"panel": int(p), "n_spots": int(m.sum()), "n_matched": int(ok.sum()),
                     "rms": float(np.sqrt((d ** 2).sum(1).mean())) if len(d) else float("nan")})
    if before is not None:
        for r, b in zip(rows, before):
            r["n_matched_before"], r["rms_before"] = b["n_matched"], b["rms"]
    return rows


def apply_omega_offsets(qlab, omega_deg, panel_id, offsets: dict) -> tuple:
    """New ``(omega_deg, qsample_2pi)`` for the spots after shifting each panel's omega."""
    from .geometry_adapter import qlab_to_sample
    qlab = np.asarray(qlab, float)
    pid = np.asarray(panel_id)
    om = np.asarray(omega_deg, float).copy()
    qs = np.empty_like(qlab)
    for p in sorted(int(x) for x in set(pid)):
        m = pid == p
        om[m] = om[m] + float(offsets.get(p, offsets.get(str(p), 0.0)))
        qs[m] = qlab_to_sample(qlab[m], om[m])
    return om, qs
