"""Contaminant-material filter (Solve Cell stage 2). Pure numpy -- no Qt, and no
``midas_gui`` imports (like ``pipeline.py``, so it stays liftable).

A *material* is anything that scatters into the spot list but is not the sample:
a diamond anvil, a gasket (Re, W, ...), a pressure marker or medium. Each one is
described by its own lattice and the filter parameters that apply to it, so a
run can carry any number of them. A spot is *flagged* when ANY enabled material
claims it; flagged spots are removed from the candidate pool passed to indexing.

Two ways a material can claim a spot (``Material.mode``):

``"2theta"``       radial test only: the spot's 2-theta is within ``tol_deg`` of an
                   allowed reflection of the material. Right for powder-like
                   contaminants (gasket rings). Cannot tell a sample reflection
                   that happens to share a radius from a contaminant one.
``"orientation"``  the spot must also belong to a *single crystal orientation* of
                   the material: among the 2-theta matches, a seed-and-extend
                   search finds orientations of the material's reciprocal lattice
                   under which at least ``min_spots`` spots land on allowed
                   reflections within ``orient_tol_deg``. Up to ``max_crystals``
                   orientations are peeled off (e.g. two anvils). Spots that match
                   in 2-theta but fit no orientation are kept. Right for
                   single-crystal anvils.

``protect_if_indexed`` lets a later re-run keep a spot that indexes on an already
solved domain (``known_domains``) even though the material claims it.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, fields
from typing import Optional, Sequence

import numpy as np
import pandas as pd

TWO_PI = 2.0 * math.pi
N_MC_DEFAULT = 200_000
RNG_SEED_DEFAULT = 0

EXTINCTIONS = ("space_group", "none", "diamond_basis")
MODES = ("2theta", "orientation")
_MAX_SEED_SPOTS = 25          # brightest 2-theta matches used to seed an orientation search


@dataclass
class Material:
    name: str = "Material"
    enabled: bool = True
    cell: tuple = (4.0, 4.0, 4.0, 90.0, 90.0, 90.0)   # a b c (A), alpha beta gamma (deg)
    space_group: int = 1                              # ITA number; used when extinction="space_group"
    extinction: str = "space_group"                   # space_group | none | diamond_basis
    tol_deg: float = 0.07                             # 2-theta window around an allowed reflection
    hkl_max: int = 8                                  # |h|,|k|,|l| enumeration limit
    mode: str = "2theta"                              # 2theta | orientation
    orient_tol_deg: float = 0.5                       # angular tolerance of q direction (orientation mode)
    min_spots: int = 3                                # spots one orientation must own (orientation mode)
    max_crystals: int = 2                             # orientations peeled off (orientation mode)
    min_peak_counts: float = 0.0                      # a spot fainter than this is never flagged
    protect_if_indexed: bool = False                  # keep spots that index on a solved domain
    protect_tol: float = 0.15                         # fractional-hkl tolerance for that test

    def to_dict(self) -> dict:
        d = asdict(self)
        d["cell"] = [float(v) for v in self.cell]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Material":
        """Tolerant of missing/unknown keys (old projects, hand-written MCP calls)."""
        known = {f.name for f in fields(cls)}
        kw = {k: v for k, v in (d or {}).items() if k in known}
        if "cell" in kw:
            cell = tuple(float(v) for v in kw["cell"])
            if len(cell) != 6:
                raise ValueError(f"material cell needs 6 values (a b c alpha beta gamma), got {len(cell)}")
            kw["cell"] = cell
        m = cls(**kw)
        if m.extinction not in EXTINCTIONS:
            raise ValueError(f"extinction must be one of {EXTINCTIONS}, got {m.extinction!r}")
        if m.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {m.mode!r}")
        m.space_group = int(m.space_group); m.hkl_max = int(m.hkl_max)
        m.min_spots = int(m.min_spots); m.max_crystals = int(m.max_crystals)
        return m


def _cubic(a): return (a, a, a, 90.0, 90.0, 90.0)


# Ambient-condition reference lattices; every field is editable in the GUI.
PRESETS = {
    "Diamond": dict(cell=_cubic(3.5667), space_group=227, extinction="diamond_basis"),
    "Rhenium (gasket)": dict(cell=(2.7609, 2.7609, 4.4583, 90.0, 90.0, 120.0), space_group=194),
    "Tungsten (gasket)": dict(cell=_cubic(3.1652), space_group=229),
    "Gold (marker)": dict(cell=_cubic(4.0782), space_group=225),
    "Platinum (marker)": dict(cell=_cubic(3.9242), space_group=225),
    "MgO (medium)": dict(cell=_cubic(4.2112), space_group=225),
}


def preset(name: str, **override) -> Material:
    d = dict(PRESETS[name]); d.update(override)
    return Material(name=name.split(" (")[0], **d)


def default_materials() -> list:
    """The single default entry: diamond, with the long-standing 2-theta behaviour."""
    return [preset("Diamond")]


# ── reciprocal lattice + allowed reflections ─────────────────────────────────

def bmatrix(cell) -> np.ndarray:
    """Columns-convention reciprocal basis in 1/d units (``g = B @ hkl``, Cartesian)."""
    from midas_hkls import Lattice
    lat = Lattice(a=cell[0], b=cell[1], c=cell[2], alpha=cell[3], beta=cell[4], gamma=cell[5])
    return np.asarray(lat.reciprocal_cartesian_vectors(), float).T


def _absent(material: Material, hkl: np.ndarray) -> np.ndarray:
    """True where ``hkl`` is forbidden for this material."""
    out = np.zeros(len(hkl), bool)
    if material.extinction == "none" or not len(hkl):
        return out
    from midas_hkls.space_group import SpaceGroup
    sg = SpaceGroup.from_number(int(material.space_group))
    for i, h in enumerate(hkl):
        out[i] = sg.is_systematically_absent(int(h[0]), int(h[1]), int(h[2]))
    if material.extinction == "diamond_basis":
        odd = np.all(hkl % 2 != 0, axis=1)
        even = np.all(hkl % 2 == 0, axis=1)
        out |= ~(odd | (even & (hkl.sum(axis=1) % 4 == 0)))
    return out


def allowed_reflections(material: Material, wavelength_A: float):
    """``(hkl, g_cart)``: allowed integer hkl reachable at this wavelength and their
    crystal-frame reciprocal vectors (1/d, so ``|g| = 1/d``). One member of each
    Friedel pair (``hkl`` and ``-hkl`` share 2-theta and extinction)."""
    n = int(material.hkl_max)
    r = np.arange(-n, n + 1)
    hkl = np.stack(np.meshgrid(r, r, r, indexing="ij"), -1).reshape(-1, 3)
    first = np.array([next((x for x in h if x != 0), 0) for h in hkl])   # first non-zero index > 0
    hkl = hkl[first > 0]
    B = bmatrix(material.cell)
    g = (B @ hkl.T).T
    gn = np.linalg.norm(g, axis=1)
    keep = gn * wavelength_A / 2.0 < 1.0               # sin(theta) < 1
    hkl, g = hkl[keep], g[keep]
    ok = ~_absent(material, hkl)
    return hkl[ok], g[ok]


def material_lines(material: Material, wavelength_A: float) -> pd.DataFrame:
    """One row per distinct allowed 2-theta: ``tth_deg, d_A, hkl`` (label of the
    lowest-index member), ``q`` (2*pi/d). Same shape as the old diamond table."""
    hkl, g = allowed_reflections(material, wavelength_A)
    if not len(hkl):
        return pd.DataFrame(columns=["tth_deg", "d_A", "hkl", "q"])
    gn = np.linalg.norm(g, axis=1)
    tth = np.round(np.degrees(2.0 * np.arcsin(gn * wavelength_A / 2.0)), 6)
    order = np.lexsort((np.abs(hkl).sum(axis=1), tth))
    seen: dict = {}
    for i in order:
        seen.setdefault(float(tth[i]), (1.0 / gn[i], "".join(f"{int(x)}" if x >= 0 else f"-{abs(int(x))}" for x in hkl[i])))
    rows = [{"tth_deg": t, "d_A": d, "hkl": lab, "q": TWO_PI / d} for t, (d, lab) in sorted(seen.items())]
    return pd.DataFrame(rows)


def nearest_line_distance(values: np.ndarray, lines: np.ndarray) -> np.ndarray:
    """``min(|values - lines|)`` per value (searchsorted: no n_values x n_lines broadcast)."""
    values = np.asarray(values, float)
    lines = np.asarray(lines, float)
    if len(lines) == 0 or values.size == 0:
        return np.full(values.shape, np.inf)
    idx = np.clip(np.searchsorted(lines, values), 1, len(lines) - 1) if len(lines) > 1 else np.zeros(values.shape, int)
    if len(lines) == 1:
        return np.abs(values - lines[0])
    return np.minimum(np.abs(values - lines[idx - 1]), np.abs(values - lines[idx]))


# ── orientation-consistent matching ──────────────────────────────────────────

def _fit_rotation(a: np.ndarray, b: np.ndarray):
    """Rotation ``R`` with ``R @ b_i ~ a_i`` (a, b: (n, 3); n >= 2)."""
    from scipy.spatial.transform import Rotation
    rot, _ = Rotation.align_vectors(a, b)
    return rot


def _unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def _match(rot, g_spots, gn_spots, rel_tol, ghat, model_g, model_n, model_hat, cos_tol):
    """Boolean per spot: lies on a rotated model reflection (direction within the
    angular tolerance AND |g| within the radial tolerance). Also the nearest model index."""
    pred_hat = rot.apply(model_hat)
    cosang = ghat @ pred_hat.T                                # (m, H)
    radial = np.abs(gn_spots[:, None] - model_n[None, :]) <= rel_tol[:, None] * gn_spots[:, None]
    score = np.where(radial, cosang, -2.0)
    j = score.argmax(axis=1)
    ok = score[np.arange(len(j)), j] >= cos_tol
    return ok, j


def find_orientations(g: np.ndarray, tth_deg: np.ndarray, counts: np.ndarray, material: Material,
                      wavelength_A: float) -> list:
    """Orientations of ``material`` that own >= ``min_spots`` of the given spots.

    ``g`` are the spots' sample-frame scattering vectors in 1/d. Returns a list of
    ``{"members": [row indices into g], "rms_deg": float, "rotation": 3x3 list}``,
    strongest first, spots never shared between orientations."""
    if len(g) < max(2, material.min_spots):
        return []
    hkl, gcart = allowed_reflections(material, wavelength_A)
    if len(hkl) < 2:
        return []
    # full +-hkl set so a rotation need not be inversion-aware
    model_g = np.vstack([gcart, -gcart])
    model_n = np.linalg.norm(model_g, axis=1)
    model_hat = model_g / model_n[:, None]
    cos_tol = math.cos(math.radians(material.orient_tol_deg))
    gn = np.linalg.norm(g, axis=1)
    ghat = g / gn[:, None]
    theta = np.radians(np.asarray(tth_deg, float) / 2.0)
    # radial window: d|g|/|g| = cot(theta) * d(theta), d(theta) = tol_deg / 2
    rel_tol = np.clip(np.abs(1.0 / np.tan(np.maximum(theta, 1e-6))) * math.radians(material.tol_deg) / 2.0, 1e-5, 0.5)
    remaining = np.arange(len(g))
    out: list = []
    for _ in range(max(1, int(material.max_crystals))):
        if len(remaining) < max(2, material.min_spots):
            break
        order = remaining[np.argsort(-np.asarray(counts, float)[remaining])][:_MAX_SEED_SPOTS]
        # candidate hkl (model vector indices) per seed spot, by radius only
        cand = {i: np.flatnonzero(np.abs(model_n - gn[i]) <= rel_tol[i] * gn[i]) for i in order}
        best_n, best_members, best_rot = 0, None, None
        done = False
        for ia in range(len(order)):
            for ib in range(ia + 1, len(order)):
                i, j = order[ia], order[ib]
                ci, cj = cand[i], cand[j]
                if not len(ci) or not len(cj):
                    continue
                obs_ang = math.degrees(math.acos(float(np.clip(ghat[i] @ ghat[j], -1, 1))))
                ang_m = np.degrees(np.arccos(np.clip(model_hat[ci] @ model_hat[cj].T, -1, 1)))
                for u, v in np.argwhere(np.abs(ang_m - obs_ang) <= material.orient_tol_deg):
                    a = np.array([ghat[i], ghat[j]]); b = np.array([model_hat[ci[u]], model_hat[cj[v]]])
                    if abs(np.linalg.norm(np.cross(b[0], b[1]))) < 1e-3:
                        continue
                    rot = _fit_rotation(a, b)
                    ok, _jj = _match(rot, g[remaining], gn[remaining], rel_tol[remaining], ghat[remaining],
                                     model_g, model_n, model_hat, cos_tol)
                    n = int(ok.sum())
                    if n > best_n:
                        best_n, best_members, best_rot = n, remaining[ok], rot
                        if n == len(remaining):
                            done = True
                            break
                if done:
                    break
            if done:
                break
        if best_members is None or best_n < material.min_spots:
            break
        # refine on every member, then re-count once
        ok, jj = _match(best_rot, g[remaining], gn[remaining], rel_tol[remaining], ghat[remaining],
                        model_g, model_n, model_hat, cos_tol)
        if ok.sum() >= 2:
            try:
                rot2 = _fit_rotation(ghat[remaining][ok], model_hat[jj[ok]])
                ok2, jj2 = _match(rot2, g[remaining], gn[remaining], rel_tol[remaining], ghat[remaining],
                                  model_g, model_n, model_hat, cos_tol)
                if ok2.sum() >= ok.sum():
                    best_rot, ok, jj = rot2, ok2, jj2
            except Exception:
                pass
        members = remaining[ok]
        if len(members) < material.min_spots:
            break
        pred = best_rot.apply(model_hat[jj[ok]])
        err = np.degrees(np.arccos(np.clip(np.sum(pred * ghat[members], axis=1), -1, 1)))
        out.append({"members": [int(m) for m in members], "rms_deg": float(np.sqrt(np.mean(err ** 2))),
                    "rotation": [[float(x) for x in row] for row in best_rot.as_matrix()]})
        remaining = np.setdiff1d(remaining, members)
    return out


# ── the stage ────────────────────────────────────────────────────────────────

def _null_counts(tth: np.ndarray, lines_tol: Sequence, n_mc: int, rng, chunk: int = 20000):
    """Monte-Carlo chance coincidence: spots drawn uniformly over the observed 2-theta
    span, counted per draw for each ``(lines, tol)`` and for 'any of them'.
    Returns ``(per_material_counts list, any_counts)``; chunked so memory stays bounded."""
    n = len(tth)
    lo, hi = float(tth.min()), float(tth.max())
    per = [np.empty(n_mc, int) for _ in lines_tol]
    anyc = np.empty(n_mc, int)
    for s in range(0, n_mc, chunk):
        m = min(chunk, n_mc - s)
        draws = rng.uniform(lo, hi, size=(m, n))
        any_hit = np.zeros(draws.shape, bool)
        for k, (lines, tol) in enumerate(lines_tol):
            hit = nearest_line_distance(draws, lines) < tol
            per[k][s:s + m] = hit.sum(axis=1)
            any_hit |= hit
        anyc[s:s + m] = any_hit.sum(axis=1)
    return per, anyc


def _z(n_obs: int, counts: np.ndarray) -> tuple:
    mean, std = float(counts.mean()), float(counts.std())
    return mean, std, ((n_obs - mean) / std if std > 0 else float("nan"))


def known_domains_from(domains: Optional[list]) -> list:
    """``[{label, UB}]`` (UB in 1/d, ``g = UB @ hkl``) from the tab's domain dicts."""
    out = []
    for d in domains or []:
        ub = (d.get("result") or {}).get("UB")
        if ub is not None:
            out.append({"label": d.get("label", ""), "UB": np.asarray(ub, float)})
    return out


def _indexes_on(UB: np.ndarray, g_1d: np.ndarray, tol: float) -> np.ndarray:
    frac = (np.linalg.inv(UB) @ g_1d.T).T
    return np.all(np.abs(frac - np.rint(frac)) < tol, axis=1)


def apply_material_filter(spots: pd.DataFrame, wavelength_A: float, materials: Sequence,
                          *, enabled: bool = True, n_mc: int = N_MC_DEFAULT,
                          rng_seed: int = RNG_SEED_DEFAULT, known_domains: Optional[list] = None):
    """Flag contaminant spots. Returns ``(flagged_df, summary)``.

    ``flagged_df`` is ``spots`` plus ``delta_to_diamond_deg`` (2-theta distance to the
    nearest line of any enabled material -- the column keeps its old name for saved
    projects), ``is_diamond`` (True = flagged by some material; old name kept),
    ``contaminant_id`` (index into ``summary["materials"]``, -1 = none) and
    ``protected_by`` (1-based solved-domain number that rescued the spot, 0 = none).
    With ``enabled=False`` nothing is flagged."""
    spots = spots.copy()
    mats = [m if isinstance(m, Material) else Material.from_dict(m) for m in materials]
    n_total = len(spots)
    spots["delta_to_diamond_deg"] = np.nan
    spots["is_diamond"] = False
    spots["contaminant_id"] = -1
    spots["protected_by"] = 0
    if "spot_id" not in spots:
        spots["spot_id"] = np.arange(n_total)

    summary = {"enabled": bool(enabled), "n_spots": n_total, "materials": [m.to_dict() for m in mats],
               "per_material": {}, "n_mc": int(n_mc), "rng_seed": int(rng_seed),
               "null_mean": float("nan"), "null_std": float("nan"), "z_score": float("nan")}
    active = [(i, m) for i, m in enumerate(mats) if m.enabled]
    if not enabled or not active or n_total == 0:
        summary.update(n_flagged=0, n_diamond_flagged=0, n_protected=0, n_kept=n_total)
        _legacy_keys(summary, mats)
        return spots, summary

    tth = spots["two_theta_deg"].to_numpy(float)
    counts = spots["peak_counts"].to_numpy(float) if "peak_counts" in spots else np.full(n_total, np.inf)
    g_1d = spots[["qsample_x", "qsample_y", "qsample_z"]].to_numpy(float) / TWO_PI \
        if {"qsample_x", "qsample_y", "qsample_z"} <= set(spots.columns) else None

    deltas, matched2t, flags, crystals_info = {}, {}, {}, {}
    lines_tol = []
    for i, m in active:
        lines = material_lines(m, wavelength_A)
        L = lines["tth_deg"].to_numpy()
        d = nearest_line_distance(tth, L)
        deltas[i] = d
        lines_tol.append((L, m.tol_deg))
        hit = (d < m.tol_deg) & (counts >= m.min_peak_counts)
        matched2t[i] = hit
        if m.mode == "orientation" and g_1d is not None:
            idx = np.flatnonzero(hit)
            cr = find_orientations(g_1d[idx], tth[idx], counts[idx], m, wavelength_A) if len(idx) else []
            fl = np.zeros(n_total, bool)
            info = []
            for c in cr:
                rows = idx[np.asarray(c["members"], int)]
                fl[rows] = True
                info.append({"n_spots": len(rows), "rms_deg": c["rms_deg"],
                             "spot_ids": [int(s) for s in spots["spot_id"].to_numpy()[rows]],
                             "rotation": c["rotation"]})
            flags[i], crystals_info[i] = fl, info
        else:
            flags[i], crystals_info[i] = hit.copy(), []

    spots["delta_to_diamond_deg"] = np.min([deltas[i] for i, _ in active], axis=0)
    flagged = np.zeros(n_total, bool)
    cid = np.full(n_total, -1, int)
    for i, m in active:                       # earlier materials win a shared spot
        new = flags[i] & ~flagged
        cid[new] = i
        flagged |= flags[i]

    # keep spots that index on an already-solved domain
    protected = np.zeros(n_total, int)
    doms = known_domains_from(known_domains)
    if doms and g_1d is not None:
        for i, m in active:
            if not m.protect_if_indexed:
                continue
            sel = np.flatnonzero(flagged & (cid == i) & (protected == 0))
            for k, dom in enumerate(doms, start=1):
                if not len(sel):
                    break
                ok = _indexes_on(dom["UB"], g_1d[sel], m.protect_tol)
                protected[sel[ok]] = k
                sel = sel[~ok]
        flagged &= protected == 0
    spots["is_diamond"] = flagged
    spots["contaminant_id"] = np.where(flagged, cid, -1)
    spots["protected_by"] = protected

    # chance-coincidence control on the 2-theta test (before orientation / protection)
    n_any = int(np.any([matched2t[i] for i, _ in active], axis=0).sum())
    if len(lines_tol):
        per, anyc = _null_counts(tth, lines_tol, int(n_mc), np.random.default_rng(int(rng_seed)))
        summary["null_mean"], summary["null_std"], summary["z_score"] = _z(n_any, anyc)
    else:
        per = []
    for k, (i, m) in enumerate(active):
        n2 = int(matched2t[i].sum())
        mean, std, z = _z(n2, per[k]) if len(per) else (float("nan"),) * 3
        summary["per_material"][m.name] = {
            "index": i, "n_2theta_matches": n2, "n_flagged": int((spots["contaminant_id"] == i).sum()),
            "null_mean": mean, "null_std": std, "z_score": z,
            "n_lines": int(len(lines_tol[k][0])), "crystals": crystals_info[i]}
    n_flagged = int(flagged.sum())
    summary.update(n_flagged=n_flagged, n_diamond_flagged=n_flagged, n_protected=int((protected > 0).sum()),
                   n_kept=n_total - n_flagged)
    _legacy_keys(summary, mats)
    return spots, summary


def _legacy_keys(summary: dict, mats: list):
    """Keys older code/projects read: the first material's tolerance and cell edge."""
    first = mats[0] if mats else None
    summary["tolerance_deg"] = first.tol_deg if first else float("nan")
    summary["diamond_a_angstrom"] = first.cell[0] if first else float("nan")
