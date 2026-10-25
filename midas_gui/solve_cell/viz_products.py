"""Builders for the Solve Cell visualization products that are cached in the
project file (``project.write_solve_cell_viz``). Pure numpy/pandas -- no Qt.

Each builder takes the in-memory analysis (the same pieces ``read_solve_cell``
returns / the tab holds) and returns ``{"meta", "tables", "arrays"}`` ready to
persist. ``meta["inputs"]`` fingerprints what the product was computed from so a
consumer can tell when a later re-index made it stale.
"""
from __future__ import annotations

import hashlib
from typing import Optional

import numpy as np
import pandas as pd

from . import geometry_adapter as ga

PRED_Q_MARGIN = 0.05        # Å⁻¹ (2π/d) beyond the largest observed |q|


def ub_fingerprint(UB) -> str:
    return hashlib.sha1(np.round(np.asarray(UB, float), 9).tobytes()).hexdigest()[:12]


def omega_ranges(snaps: dict) -> dict:
    """Scanned omega span per panel from the ingest schedule (kept window)."""
    return {pid: (s.omega_ref_deg,
                  s.omega_ref_deg + (s.omega_last_frame_idx - s.omega_ref_frame_idx) * s.omega_step_deg)
            for pid, s in snaps.items()}


def build_frame_manifest(ingest_meta: dict) -> dict:
    """V11 prerequisite: one row per LOADED frame per panel with its omega and
    whether it lies in the ingest omega window (the window the analysis used).
    The live-frame (dead-frame) filter result is not retained by ingest, so
    ``kept`` == ``in_window``; ``meta["live_filter"]`` records the counts."""
    snaps = ga.snapshots_from_ingest_meta(ingest_meta)
    pp = (ingest_meta.get("summary") or {}).get("per_panel") or {}
    frames = []
    for pid, s in snaps.items():
        n = int((pp.get(str(pid)) or {}).get("n_frames_loaded", 0)) or (s.omega_last_frame_idx + 1)
        idx = np.arange(n)
        om = s.omega_of_loaded_frame(idx)
        frames.append(pd.DataFrame({
            "panel_id": pid, "loaded_idx": idx, "omega_start_deg": om,
            "omega_mid_deg": om + 0.5 * s.omega_step_deg,
            "in_window": s.in_window(idx), "kept": s.in_window(idx)}))
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return {"meta": {"inputs": {"kind": "ingest"},
                     "omega_convention": "omega_start = ref_deg + (idx - ref_idx)*step; mid adds step/2; "
                                         "spot omega is interpolated on the kept-frame centroid index",
                     "live_filter": {str(pid): {"n_frames": (pp.get(str(pid)) or {}).get("n_frames"),
                                                "n_live": (pp.get(str(pid)) or {}).get("n_live")}
                                     for pid in snaps}},
            "tables": {"frames": df}}


def domain_rows(candidate_df: pd.DataFrame, claimed_index, hkl) -> list:
    """Observed spot records of one domain, each carrying its assigned ``hkl``."""
    cl = candidate_df.loc[claimed_index]
    rows = []
    for (_i, r), h in zip(cl.iterrows(), np.asarray(hkl)):
        d = r.to_dict()
        d["hkl"] = tuple(int(round(x)) for x in h)
        rows.append(d)
    return rows


def build_domain_predictions(label: str, UB, rows: list, snaps: dict, *,
                             q_max: Optional[float] = None) -> dict:
    """V08 + V09 for one domain: every Ewald crossing the domain's UB implies on
    each panel inside the scanned omega window (``predictions``), plus
    observed-minus-predicted residuals for its assigned spots (``residuals``)."""
    UB = np.asarray(UB, float)
    if q_max is None:
        q_max = float(max(np.hypot.reduce([[r["qsample_x"], r["qsample_y"], r["qsample_z"]] for r in rows], axis=1))) \
            + PRED_Q_MARGIN if rows else 1.0
    pred = ga.predict_reflections(UB, snaps, omega_ranges(snaps), q_max=q_max)
    pdf = pd.DataFrame(pred)
    res = pd.DataFrame(ga.match_predictions(rows, pred))
    if len(res):
        res["hkl_h"], res["hkl_k"], res["hkl_l"] = (np.array(res["hkl"].tolist()) if len(res) else np.empty((0, 3))).T
        res = res.drop(columns="hkl")
    # flag predictions that an observed spot of this domain already explains
    if len(pdf):
        pdf["observed_spot_id"] = -1
        key = {(int(r.panel_id), int(r.hkl_h), int(r.hkl_k), int(r.hkl_l), round(float(r.pred_omega), 6)): int(r.spot_id)
               for r in res.itertuples()} if len(res) else {}
        pdf["observed_spot_id"] = [key.get((int(a), int(b), int(c), int(d), round(float(e), 6)), -1)
                                   for a, b, c, d, e in zip(pdf.panel_id, pdf.h, pdf.k, pdf.l, pdf.omega_deg)]
    st = {"n_predictions": int(len(pdf)),
          "n_visible": int((pdf.visibility == "visible").sum()) if len(pdf) else 0,
          "n_observed_matched": int(len(res)), "n_assigned": int(len(rows))}
    if len(res):
        st.update(rms_d_row=float(np.sqrt(np.mean(res.d_row ** 2))), rms_d_col=float(np.sqrt(np.mean(res.d_col ** 2))),
                  rms_d_omega_deg=float(np.sqrt(np.mean(res.d_omega_deg ** 2))))
    return {"meta": {"inputs": {"kind": "domain", "label": label, "ub_fingerprint": ub_fingerprint(UB)},
                     "UB": UB.tolist(), "UB_convention": "1/d; q = 2*pi*UB@hkl", "q_max": q_max,
                     "sign_convention": "residual = observed - predicted (pixels; omega in degrees)",
                     "stats": st},
            "tables": {"predictions": pdf, "residuals": res}}


def build_all_from_record(record: dict) -> dict:
    """Every cacheable product derivable from a ``read_solve_cell`` record:
    ``{product_name: product}``. Domains without a stored UB/hkl are skipped."""
    out = {}
    imeta = record["ingest"]["meta"]
    snaps = ga.snapshots_from_ingest_meta(imeta)
    out["frame_manifest"] = build_frame_manifest(imeta)
    dia, idx = record.get("diamond"), record.get("index")
    if dia is None or idx is None:
        return out
    fl = dia["flagged_df"]
    cand = fl[~fl["is_diamond"]].reset_index(drop=True)
    out["domain_comparison"] = build_domain_comparison(*_comparison_inputs_from_record(record), cand)
    for n, (dm, arrays) in enumerate(zip(idx["meta"].get("domains") or [], idx["domains"]), start=1):
        res = dm["result"]
        fit = res.get("reflection_fit") or {}
        if res.get("UB") is None or fit.get("hkl") is None:
            continue
        rows = domain_rows(cand, arrays["claimed_index"], fit["hkl"])
        out[f"domain_{n:02d}_predictions"] = build_domain_predictions(dm["label"], res["UB"], rows, snaps)
    return out


def persist_all(project_path, record: Optional[dict] = None, *, only: Optional[list] = None) -> list:
    """Compute and store every cacheable product in ``project_path``'s Solve
    Cell record. Returns the product names written."""
    from midas_gui import project
    record = record or project.read_solve_cell(project_path)
    if record is None:
        raise ValueError("project has no Solve Cell record")
    written = []
    for name, prod in build_all_from_record(record).items():
        if only is not None and name not in only:
            continue
        project.write_solve_cell_viz(project_path, name, meta=prod["meta"],
                                     tables=prod.get("tables"), arrays=prod.get("arrays"))
        written.append(name)
    return written


# ── overlay geometry (V01 / V15 / V08 helpers; pure) ─────────────────────────
def ewald_cap(wavelength_A: float, omega_deg: float, q_cap: float, n_theta: int = 24,
              n_phi: int = 72) -> dict:
    """Local cap of the Ewald sphere through q=0 in SAMPLE-frame q at ``omega``.

    ``q_lab = k0*n - ki`` with ``n`` a unit vector about the beam direction;
    the cap covers ``|q| <= q_cap`` (chord ``2*k0*sin(theta/2)``). Also returns
    the beam wavevector and rotation axis in the sample frame at this omega.
    """
    k0 = 2.0 * np.pi / float(wavelength_A)
    th_max = 2.0 * np.arcsin(min(q_cap / (2.0 * k0), 1.0))
    th = np.linspace(0.0, th_max, n_theta)
    ph = np.linspace(0.0, 2.0 * np.pi, n_phi)
    TH, PH = np.meshgrid(th, ph, indexing="ij")
    n = np.stack([np.cos(TH), np.sin(TH) * np.cos(PH), np.sin(TH) * np.sin(PH)], -1)
    qlab = (k0 * n - np.array([k0, 0.0, 0.0])).reshape(-1, 3)
    qs = ga.qlab_to_sample(qlab, omega_deg).reshape(n_theta, n_phi, 3)
    ki_s = ga.qlab_to_sample(np.array([[k0, 0.0, 0.0]]), omega_deg)[0]
    return {"patch": qs, "ki": ki_s, "axis": ga.rotation_axis_in_sample(),
            "center": ga.ewald_center_in_sample(wavelength_A, omega_deg), "k0": k0}


def basis_matrix(UB) -> np.ndarray:
    """Columns a*, b*, c* in sample-frame q (2*pi scale) from a stored UB."""
    return 2.0 * np.pi * np.asarray(UB, float)


def classify_lattice_points(pred: pd.DataFrame) -> dict:
    """V08 grouping, one entry per unique hkl (a lattice point is omega/panel
    independent): ``observed`` (some crossing is explained by an assigned spot),
    ``missing`` (visible on a panel in the scan, no assigned spot) and
    ``hidden`` (never lands on an active panel). Arrays are (n,3) sample-q."""
    if pred is None or not len(pred):
        e = np.empty((0, 3))
        return {"observed": e, "missing": e, "hidden": e}
    g = pred.groupby(["h", "k", "l"])
    agg = g.agg(qx=("qx", "first"), qy=("qy", "first"), qz=("qz", "first"),
                vis=("visibility", lambda v: bool((v == "visible").any())),
                obs=("observed_spot_id", lambda v: bool((v >= 0).any()))).reset_index()
    xyz = agg[["qx", "qy", "qz"]].to_numpy()
    return {"observed": xyz[agg.obs.to_numpy()],
            "missing": xyz[(agg.vis & ~agg.obs).to_numpy()],
            "hidden": xyz[(~agg.vis).to_numpy()]}


def lattice_points_near_omega(pred: pd.DataFrame, omega_deg: float, half_width_deg: float) -> np.ndarray:
    """Sample-q of crossings whose omega lies within ``half_width_deg`` of
    ``omega_deg`` (mod 360) -- the reflections diffracting at this instant."""
    if pred is None or not len(pred):
        return np.empty((0, 3))
    d = (pred.omega_deg.to_numpy() - omega_deg + 180.0) % 360.0 - 180.0
    sel = pred[np.abs(d) <= half_width_deg]
    return sel[["qx", "qy", "qz"]].drop_duplicates().to_numpy()


# ── reciprocal volume product (V03) ──────────────────────────────────────────
VOLUME_APPROX = ("quick look: static median background, nominal frame omega (ingest convention), "
                 "nearest voxel; no dark/bright/polarization/solid-angle corrections")


def volume_product(vol: dict, spec, ingest_meta: Optional[dict] = None) -> dict:
    """Package a finished (cropped) volume for ``write_solve_cell_viz``."""
    vol = dict(vol)
    per_panel = vol.pop("per_panel", {})
    meta = {"inputs": {"kind": "ingest", "ingest_timestamp": (ingest_meta or {}).get("timestamp")},
            "grid": spec.to_json(), "per_panel": per_panel, "approximations": VOLUME_APPROX,
            "frame": "sample-frame q (A^-1, 2*pi/d); cell data; index = floor((q-origin)/spacing)",
            "fields": {"sum": "sum of background-subtracted counts", "count": "valid pixel samples; 0 = unmeasured",
                       "max": "brightest contributing pixel (NaN where unmeasured)"}}
    arrays = {"sum": vol["sum"], "count": np.asarray(vol["count"]).astype(np.uint32), "max": vol["max"]}
    return {"meta": meta, "tables": {}, "arrays": arrays}


def volume_from_product(prod: dict):
    """Inverse of ``volume_product`` -> ``(vol dict with float32 count, GridSpec)``."""
    from .reciprocal_volume import GridSpec
    g = prod["meta"]["grid"]
    a = prod["arrays"]
    return ({"sum": a["sum"], "count": a["count"].astype(np.float32), "max": a["max"]},
            GridSpec(tuple(g["origin"]), g["spacing"], tuple(g["shape"])))


def build_volume_for_project(project_path, pitch: float = 0.06, progress=None, record=None) -> dict:
    """Headless: rebuild the reciprocal volume from the source files recorded in
    the project's ingest and store it. Returns the product."""
    import h5py
    import hdf5plugin  # noqa: F401  (registers the compression filters)
    from midas_gui import project
    from . import reciprocal_volume as rv
    record = record or project.read_solve_cell(project_path)
    meta = record["ingest"]["meta"]
    snaps = ga.snapshots_from_ingest_meta(meta)
    spec = rv.auto_grid(snaps, pitch)
    files, sources = [], []
    try:
        for key, prec in meta["panels"].items():
            pid = int(key)
            ld = prec["state"]["loader"]
            f = h5py.File(ld["path"], "r"); files.append(f)
            d = f[ld["dataset"].split()[0]]
            mask = (record["ingest"]["panels"].get(f"panel_{pid:02d}") or {}).get("mask")
            sources.append({"snap": snaps[pid], "frames": (lambda a, b, _d=d: _d[a:b]),
                            "mask": mask, "n_frames": int(d.shape[0])})
        vol = rv.build_volume(sources, spec, progress=progress)
    finally:
        for f in files:
            f.close()
    vol, spec = rv.crop_to_coverage(vol, spec)
    prod = volume_product(vol, spec, meta)
    project.write_solve_cell_viz(project_path, "reciprocal_volume", meta=prod["meta"], arrays=prod["arrays"])
    return prod


def build_shoeboxes_for_project(project_path, record=None, progress=None, **kw) -> dict:
    """Headless: cut the per-spot shoebox cache from the project's source files
    and store it as the ``spot_shoeboxes`` product."""
    import h5py
    import hdf5plugin  # noqa: F401
    from midas_gui import project
    from . import shoeboxes as sb
    record = record or project.read_solve_cell(project_path)
    meta = record["ingest"]["meta"]
    snaps = ga.snapshots_from_ingest_meta(meta)
    files, panels = [], {}
    try:
        for key, prec in meta["panels"].items():
            ld = prec["state"]["loader"]
            f = h5py.File(ld["path"], "r"); files.append(f)
            d = f[ld["dataset"].split()[0]]
            panels[int(key)] = {"read": (lambda a, b, _d=d: _d[a:b]), "n_frames": int(d.shape[0]),
                                "snap": snaps[int(key)]}
        prod = sb.build_spot_shoeboxes(record["ingest"]["spots_df"], panels, progress=progress, **kw)
    finally:
        for f in files:
            f.close()
    prod["meta"]["inputs"] = {"kind": "ingest", "n_spots": int(len(record["ingest"]["spots_df"]))}
    project.write_solve_cell_viz(project_path, "spot_shoeboxes", meta=prod["meta"],
                                 tables=prod["tables"], arrays=prod["arrays"])
    return prod


# ── contamination fingerprints (V12) ─────────────────────────────────────────
def diamond_line_table(wavelength_A: float, a_angstrom: float, hkl_max: int = 8) -> pd.DataFrame:
    """Allowed diamond (Fd-3m) lines at this wavelength: ``tth_deg``, ``d_A``,
    ``hkl`` label of the lowest-index member, ``q`` (2π/d). Same selection rule
    and ordering as ``pipeline._diamond_tth_lines_deg`` (one row per distinct 2θ)."""
    import math
    seen = {}
    for h in range(hkl_max + 1):
        for k in range(h + 1):
            for l in range(k + 1):
                if h == 0:
                    continue
                odd = (h % 2) and (k % 2) and (l % 2)
                even = not (h % 2) and not (k % 2) and not (l % 2)
                if not (odd or (even and (h + k + l) % 4 == 0)):
                    continue
                d = a_angstrom / math.sqrt(h * h + k * k + l * l)
                s = wavelength_A / (2.0 * d)
                if s >= 1.0:
                    continue
                tth = round(math.degrees(2.0 * math.asin(s)), 6)
                seen.setdefault(tth, (d, f"{h}{k}{l}"))
    rows = [{"tth_deg": t, "d_A": d, "hkl": lab, "q": 2 * math.pi / d} for t, (d, lab) in sorted(seen.items())]
    return pd.DataFrame(rows)


def flag_preview(spots: pd.DataFrame, lines_tth: np.ndarray, tol_deg: float) -> np.ndarray:
    """Boolean flag each spot WOULD get at ``tol_deg`` (no state is changed)."""
    from .pipeline import _nearest_line_distance
    return _nearest_line_distance(spots["two_theta_deg"].to_numpy(), np.asarray(lines_tth)) < tol_deg


# ── domain comparison (V13) ──────────────────────────────────────────────────
def _comparison_inputs_from_record(record: dict):
    from types import SimpleNamespace
    idx = record["index"]
    domains = [{"label": dm["label"], "method": dm["method"], "result": dm["result"],
                "claimed_index": arrays["claimed_index"], "panel_ids": arrays.get("panel_ids")}
               for dm, arrays in zip(idx["meta"].get("domains") or [], idx["domains"])]
    ab, am = None, idx["meta"].get("ab_initio")
    if am and idx.get("ab_initio"):
        a = idx["ab_initio"]
        ab = SimpleNamespace(success=am.get("success"), UB=a.get("UB"), indexed_mask=a.get("indexed_mask"),
                             cell=am.get("cell"), cell_sigma=am.get("cell_sigma"), n_indexed=am.get("n_indexed"),
                             diagnostics=am.get("diagnostics"))
    return domains, ab


def build_domain_comparison(domains: list, ab, candidate_df, cand_for_fit=None) -> dict:
    """``domain_comparison`` product: per-entry table + pairwise table. ``meta.inputs``
    lists each entry's UB fingerprint so a re-index invalidates it."""
    from . import domain_compare as dc
    cand = cand_for_fit if cand_for_fit is not None else candidate_df
    entries = dc.entries_from_state(domains, ab, cand)
    tabs = dc.compare_all(entries, cand)
    return {"meta": {"inputs": {"kind": "index", "entries": {e["key"]: ub_fingerprint(e["UB"]) for e in entries}},
                     "thresholds": {"metric_tol": dc.METRIC_TOL, "same_angle_deg": dc.SAME_ANGLE_DEG,
                                    "min_reflections": dc.MIN_REFLECTIONS},
                     "note": "metric-only comparison; conclusions are heuristics, review required; "
                             "the ab-initio seed is a candidate, never an accepted domain"},
            "tables": tabs}


# ── frame patches (V02) ──────────────────────────────────────────────────────
def build_frame_patches(ingest_meta: dict, masks: dict, stride: int = 8, progress=None) -> dict:
    """Block-max background-subtracted cell signal of EVERY in-window frame of each
    panel (``frame_patches`` product) so omega playback needs no raw files. Reads the
    source files named in ``ingest_meta``; ``masks`` is ``{panel_id: bool (rows, cols)}``."""
    import h5py
    import hdf5plugin  # noqa: F401
    from . import frame_patch as fp, shoeboxes as sb
    snaps = ga.snapshots_from_ingest_meta(ingest_meta)
    arrays, rows, clim, files = {}, [], {}, []
    try:
        for n, (key, prec) in enumerate(ingest_meta["panels"].items()):
            pid = int(key)
            ld = prec["state"]["loader"]
            f = h5py.File(ld["path"], "r"); files.append(f)
            d = f[ld["dataset"].split()[0]]
            read = lambda a, b, _d=d: _d[a:b]
            bg = sb.panel_background(read, int(d.shape[0]), snaps[pid])
            idx, cells = fp.build_panel_cells(read, int(d.shape[0]), snaps[pid], (masks or {}).get(pid), stride, bg)
            arrays[f"cells_p{pid}"] = cells
            arrays[f"loaded_idx_p{pid}"] = idx
            fin = cells[np.isfinite(cells)]
            clim[str(pid)] = [float(np.percentile(fin, 1)), float(np.percentile(fin, 99.99))]
            rows += [{"panel_id": pid, "loaded_idx": int(i), "slot": k} for k, i in enumerate(idx)]
            if progress:
                progress((n + 1) / len(ingest_meta["panels"]))
    finally:
        for f in files:
            f.close()
    return {"meta": {"inputs": {"kind": "ingest"}, "stride": stride, "clim": clim,
                     "cell": "block MAX of (frame - static median bg) over stride x stride raw pixels; NaN = fully masked/sentinel",
                     "approximations": "quick-look background; nominal frame omega (ingest convention)"},
            "tables": {"frames": pd.DataFrame(rows)}, "arrays": arrays}


def build_frame_patches_for_project(project_path, stride: int = 8, record=None, progress=None) -> dict:
    from midas_gui import project
    record = record or project.read_solve_cell(project_path)
    meta = record["ingest"]["meta"]
    masks = {int(k): (record["ingest"]["panels"].get(f"panel_{int(k):02d}") or {}).get("mask") for k in meta["panels"]}
    prod = build_frame_patches(meta, masks, stride, progress)
    project.write_solve_cell_viz(project_path, "frame_patches", meta=prod["meta"],
                                 tables=prod["tables"], arrays=prod["arrays"])
    return prod
