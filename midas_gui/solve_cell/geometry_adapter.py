"""Qt-free geometry helpers for Solve Cell visualizations.

Everything here is derived from what the project file already records for a
Solve Cell ingest (per-panel ``geometry_cfg`` + ``spots``), so visualization
products can be rebuilt -- and cached back into the project -- without
re-running the analysis or reloading raw frames.

Conventions (see scratch/solve_cell_visualization_research_and_implementation.md §3):

* ``q`` is in 1/d * 2*pi (Å⁻¹); ``UB`` stored by the fit is in 1/d, so
  ``q_pred = 2*pi * UB @ hkl``.
* Incident beam is lab ``+X``; in q-lab coordinates the Ewald sphere has centre
  ``-ki`` (radius ``k0 = 2*pi/lambda``) and passes through q = 0.
* Detector pixel/omega bookkeeping mirrors ``pipeline._stage_ingest``: raw
  pixel -> calibration frame via ``im_trans``, then ``pixel_to_qlab``, then
  ``qlab_to_qsample`` at the spot's omega.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np

from .pipeline import _codes, _map_index_points

TWO_PI = 2.0 * math.pi


@dataclass
class PanelSnapshot:
    """What is needed to map one panel's raw pixels <-> q (frozen at ingest)."""
    panel_id: int
    geom_cfg: dict
    raw_shape: tuple            # (n_rows, n_cols) of the RAW frame
    n_frames_loaded: int = 0
    omega_ref_frame_idx: int = 0
    omega_ref_deg: float = 0.0
    omega_step_deg: float = 0.0
    omega_last_frame_idx: int = 0

    @property
    def wavelength_A(self) -> float:
        return float(self.geom_cfg["wavelength_A"])

    @property
    def im_trans(self) -> list:
        return _codes(self.geom_cfg)

    def geometry(self, n_frames: int = 1):
        from midas_defect.geometry import Geometry
        g = self.geom_cfg
        return Geometry(
            lsd_um=g["Lsd"], bcy_px=g["BC_y"], bcz_px=g["BC_z"], px_um=g["px_um"],
            wavelength_A=g["wavelength_A"], n_pix_y=g["nrpixels_y"], n_pix_z=g["nrpixels_z"],
            omega_first_deg=self.omega_ref_deg, omega_step_deg=self.omega_step_deg,
            n_frames=n_frames, tx_deg=0.0, ty_deg=g.get("ty", 0.0), tz_deg=g.get("tz", 0.0))

    def omega_of_loaded_frame(self, loaded_idx):
        """Omega (deg) at the START of a loaded-stack frame, from the ingest's
        linear schedule (frames outside ``[ref, last]`` extrapolate, flagged by
        ``in_window``)."""
        return self.omega_ref_deg + (np.asarray(loaded_idx, float) - self.omega_ref_frame_idx) * self.omega_step_deg

    def in_window(self, loaded_idx):
        a = np.asarray(loaded_idx)
        return (a >= self.omega_ref_frame_idx) & (a <= self.omega_last_frame_idx)


def snapshots_from_ingest_meta(meta: dict) -> dict:
    """``{panel_id: PanelSnapshot}`` from a stored ingest ``metadata`` dict."""
    out = {}
    summary_pp = (meta.get("summary") or {}).get("per_panel") or {}
    for key, p in (meta.get("panels") or {}).items():
        g = dict(p["geometry_cfg"])
        # The dict KEY is the source panel id that the spots table's ``panel_id``
        # column carries; the inner ``panel_id`` is the tab card's index.
        pid = int(key)
        sp = summary_pp.get(str(key)) or {}
        ref_idx = int(g.get("omega_ref_frame_idx", 0))
        out[pid] = PanelSnapshot(
            panel_id=pid, geom_cfg=g,
            raw_shape=(int(g["nrpixels_z"]), int(g["nrpixels_y"])),
            n_frames_loaded=int(sp.get("n_frames_loaded", 0)),
            omega_ref_frame_idx=ref_idx,
            omega_ref_deg=float(g.get("omega_ref_deg", 0.0)),
            omega_step_deg=float(g.get("omega_step_deg", 0.0)),
            omega_last_frame_idx=int(g.get("omega_last_frame_idx", ref_idx)))
    return out


# ── pixel <-> q ──────────────────────────────────────────────────────────────
def raw_pixels_to_qlab(snap: PanelSnapshot, rows, cols) -> np.ndarray:
    """Raw detector pixel-index centroids -> q-lab (N, 3), Å⁻¹ * 2π."""
    import torch
    from midas_defect.geometry import pixel_to_qlab
    cols_c, rows_c = _map_index_points(cols, rows, snap.raw_shape, snap.im_trans)
    q = pixel_to_qlab(np.atleast_1d(rows_c), np.atleast_1d(cols_c), snap.geometry(), device="cpu")
    return q.detach().cpu().numpy()


def qlab_to_sample(qlab: np.ndarray, omega_deg) -> np.ndarray:
    import torch
    from midas_defect.geometry import qlab_to_qsample
    q = torch.as_tensor(np.asarray(qlab, float))
    om = torch.deg2rad(torch.as_tensor(np.broadcast_to(np.asarray(omega_deg, float), (len(q),)).copy()))
    return qlab_to_qsample(q, om).detach().cpu().numpy()


def sample_to_qlab(qsample: np.ndarray, omega_deg) -> np.ndarray:
    import torch
    from midas_defect.geometry import qsample_to_qlab
    q = torch.as_tensor(np.asarray(qsample, float))
    om = torch.deg2rad(torch.as_tensor(np.broadcast_to(np.asarray(omega_deg, float), (len(q),)).copy()))
    return qsample_to_qlab(q, om).detach().cpu().numpy()


def qlab_to_raw_pixels(snap: PanelSnapshot, qlab: np.ndarray):
    """q-lab -> raw pixel ``(rows, cols)`` (float arrays; NaN where the ray
    misses / goes backward). Inverse of ``raw_pixels_to_qlab`` incl. ``im_trans``."""
    from midas_defect.geometry import qlab_to_pixel
    qlab = np.asarray(qlab, float)
    rows_c = np.full(len(qlab), np.nan)
    cols_c = np.full(len(qlab), np.nan)
    if len(qlab):
        rows_t, cols_t = qlab_to_pixel(qlab, snap.geometry(), device="cpu")
        rows_c = rows_t.detach().cpu().numpy().astype(float)
        cols_c = cols_t.detach().cpu().numpy().astype(float)
    return _calib_to_raw_points(rows_c, cols_c, snap)


def _calib_to_raw_points(rows_c, cols_c, snap: PanelSnapshot):
    """Invert ``im_trans`` for point coordinates (ops are self-inverse; apply
    in reverse order on the TRANSFORMED shape)."""
    codes = snap.im_trans
    shapes = [tuple(snap.raw_shape)]
    for c in codes:
        r, c_ = shapes[-1]
        shapes.append((c_, r) if c == 3 else (r, c_))
    row = np.asarray(rows_c, float).copy()
    col = np.asarray(cols_c, float).copy()
    for k in range(len(codes) - 1, -1, -1):
        n_rows, n_cols = shapes[k]          # shape BEFORE op k = shape AFTER its inverse
        c = codes[k]
        if c == 1:
            col = (n_cols - 1) - col
        elif c == 2:
            row = (n_rows - 1) - row
        elif c == 3:
            col, row = row, col
    return row, col


# ── Ewald construction (V01) ─────────────────────────────────────────────────
def ewald_sphere_params(wavelength_A: float) -> dict:
    k0 = TWO_PI / float(wavelength_A)
    return {"k0": k0, "center_qlab": np.array([-k0, 0.0, 0.0]), "ki": np.array([k0, 0.0, 0.0])}


def ewald_center_in_sample(wavelength_A: float, omega_deg: float) -> np.ndarray:
    """Sphere centre ``-R(omega)^T ki`` in sample-frame q."""
    c = ewald_sphere_params(wavelength_A)["center_qlab"]
    return qlab_to_sample(c[None, :], omega_deg)[0]


def rotation_axis_in_sample() -> np.ndarray:
    """The omega rotation axis (lab Z) expressed in the sample frame (invariant)."""
    return np.array([0.0, 0.0, 1.0])


# ── forward prediction (V08) ─────────────────────────────────────────────────
def enumerate_hkl(UB: np.ndarray, q_max: float, hkl_max: int = 40) -> np.ndarray:
    """All integer hkl (excluding 000) with ``|2*pi*UB@hkl| <= q_max``."""
    UB = np.asarray(UB, float)
    # bound the search box from the metric: |h_i| <= q_max * |row i of (2pi UB)^-1|
    inv = np.linalg.inv(TWO_PI * UB)
    lim = np.minimum(np.ceil(q_max * np.linalg.norm(inv, axis=1)).astype(int), hkl_max)
    rng = [np.arange(-n, n + 1) for n in lim]
    h = np.stack(np.meshgrid(*rng, indexing="ij"), -1).reshape(-1, 3)
    h = h[np.any(h != 0, axis=1)]
    q = np.linalg.norm((TWO_PI * UB @ h.T).T, axis=1)
    return h[q <= q_max]


def predict_reflections(UB, snaps: dict, omega_ranges: dict, *, q_max: float,
                        wavelength_A: Optional[float] = None) -> list:
    """Every reflection of a domain that satisfies the Ewald condition inside
    each panel's scanned omega window AND lands on that panel's active area.

    ``omega_ranges`` = ``{panel_id: (omega_min_deg, omega_max_deg)}`` -- the
    kept-frame span actually scanned. Returns a list of dict rows::

        domain-relative: h,k,l, panel_id, omega_deg, row, col, crossing (0/1 root),
        q_x,q_y,q_z (sample frame), visibility ('visible' | reason)

    Only crossings inside the window and on the detector are returned as
    'visible'; crossings in the window that miss the panel are returned with a
    reason so completeness views can distinguish them from "not detected".
    """
    from midas_defect.geometry import ewald_crossing_omegas
    UB = np.asarray(UB, float)
    hkl = enumerate_hkl(UB, q_max)
    q_samp = (TWO_PI * UB @ hkl.T).T
    out = []
    for pid, snap in snaps.items():
        lam = wavelength_A or snap.wavelength_A
        w0, w1 = omega_ranges[pid]
        n_rows, n_cols = snap.raw_shape
        for hk, q in zip(hkl, q_samp):
            for root, cr in enumerate(ewald_crossings(q, lam)):
                om_base = cr["omega_deg"]
                for om in _lift_into_window(om_base, w0, w1):
                    qlab = sample_to_qlab(q[None, :], om)
                    r, c = qlab_to_raw_pixels(snap, qlab)
                    r, c = float(r[0]), float(c[0])
                    ok = np.isfinite(r) and np.isfinite(c)
                    vis = "visible" if ok and -0.5 <= r < n_rows - 0.5 and -0.5 <= c < n_cols - 0.5 \
                        else ("off_panel" if ok else "no_intersection")
                    out.append({"h": int(hk[0]), "k": int(hk[1]), "l": int(hk[2]),
                                "panel_id": int(pid), "omega_deg": float(om),
                                "row": r, "col": c, "crossing": root,
                                "qx": float(q[0]), "qy": float(q[1]), "qz": float(q[2]),
                                "visibility": vis})
    return out


def ewald_crossings(q_sample, wavelength_A):
    from midas_defect.geometry import ewald_crossings as _ec
    return _ec(np.asarray(q_sample, float), wavelength_A)


def _lift_into_window(om_deg: float, w0: float, w1: float):
    """All ``om_deg + 360*n`` inside ``[w0, w1]`` (handles scans >360 deg)."""
    n0 = math.ceil((w0 - om_deg) / 360.0)
    n1 = math.floor((w1 - om_deg) / 360.0)
    return [om_deg + 360.0 * n for n in range(n0, n1 + 1)]


# ── residuals (V09) ──────────────────────────────────────────────────────────
def match_predictions(spots_rows: list, predictions: list, *, hkl_key="hkl",
                      angular_gate_deg: float = 1.0) -> list:
    """Observed minus predicted, per assigned observed spot.

    For every observed spot that carries an assigned ``hkl``, pick the visible
    prediction with the same hkl on the same panel and nearest omega (wrapped
    to +-180 deg), and report ``d_col, d_row`` (pixels, observed - predicted)
    and ``d_omega_deg`` (observed - predicted). Spots with no prediction inside
    ``angular_gate_deg`` are omitted (not silently reassigned)."""
    by_key = {}
    for p in predictions:
        if p["visibility"] != "visible":
            continue
        by_key.setdefault((p["panel_id"], p["h"], p["k"], p["l"]), []).append(p)
    res = []
    for s in spots_rows:
        hk = s.get(hkl_key)
        if hk is None:
            continue
        key = (int(s["panel_id"]), int(round(hk[0])), int(round(hk[1])), int(round(hk[2])))
        cands = by_key.get(key)
        if not cands:
            continue
        dom = [((s["omega_deg"] - c["omega_deg"] + 180.0) % 360.0 - 180.0, c) for c in cands]
        d_om, best = min(dom, key=lambda t: abs(t[0]))
        if abs(d_om) > angular_gate_deg:
            continue
        res.append({"spot_id": s["spot_id"], "panel_id": key[0], "hkl": key[1:],
                    "obs_row": float(s["row"]), "obs_col": float(s["col"]), "obs_omega": float(s["omega_deg"]),
                    "pred_row": best["row"], "pred_col": best["col"], "pred_omega": best["omega_deg"],
                    "d_row": float(s["row"]) - best["row"], "d_col": float(s["col"]) - best["col"],
                    "d_omega_deg": float(d_om)})
    return res
