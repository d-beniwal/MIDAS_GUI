"""Headless Solve Cell runner -- no Qt event loop, no GUI, no MCP.

``run_solve_cell_headless(spec)`` runs the same stage chain the Solve Cell tab
runs (ingest -> material filter -> index [ab-initio or known cell] -> refine)
through ``pipeline.run_stage`` and writes the result as a MIDAS project HDF5
that the GUI opens like one it saved itself (``project.read_solve_cell`` /
File > Open Project / the ``project_open`` control op).

The record layout (``meta`` dicts, array names, stage ``fields``) is copied
from ``SolveCellTab`` (``_snapshot_ingest``/``_panel_record``/``_write_sc_*``
and the ``_on_*_done`` handlers in ``tab_solve_cell.py``) -- that file is
deliberately NOT imported or refactored; keep the two in step by hand.

Safe to run as several concurrent worker processes: it only writes its own
``project_path`` (built under a private temp name in the same directory and
renamed into place at the end, so a reader never sees a half-written file),
passes ``max_workers=1`` to the pipeline unless the spec says otherwise, and
keeps no module-level state. ``midas_gui.helpers`` (calibration parsing) is
imported lazily; it pulls in PyQt5 *modules* but never creates a
QApplication, so this works on a display-less host as long as PyQt5 is
installed. Pipeline ``print()`` output is captured (``log_tail`` in the
result, optional ``log`` callback) rather than left on stdout, so the CLI can
keep stdout for the JSON summary. Because that redirects ``sys.stdout``, do
not run two calls concurrently in different threads of one process -- use
processes.

Spec (all paths absolute or relative to the CWD)::

    {
      "project_path": "out/run.h5",          # required; must not exist unless overwrite
      "overwrite": false,
      "project_name": null,                   # default: file stem
      "output_dir": null,                     # optional: also write pipeline CSV/JSON (panel_NN/data, pooled/)

      # one panel inline ...
      "data_path": "...h5", "dataset": "entry/data/data",
      "frame_range": [start, stop] | null,    # stop exclusive, file frame indices
      "calibration": "...instr.json|paramstest|.poni",
      "omega": {"start": -30.0, "end": 29.0, "step": 0.1,
                "first_idx": 38,             # file frame index; omit to auto-detect (needs "field")
                "field": "entry/instrument/sample_stage/omega",
                "ref_from_field": false},    # true: omega at first_idx = measured field value
      # ... or several (pooled across panels, like the tab):
      "panels": [{"panel_id": 1, "data_path":..., "calibration":..., "omega": {...}, ...}, ...],

      "mask":   {"low_count_threshold": 0.0, "grow": 2},
      "live_frames": {"frac_of_median": 0.002},
      "blobs":  {"threshold": 15.0, "min_vol": 6, "min_frames": 2, "split_ratio": 3.0,
                 "gap_bridge": 21 (0 = off), "sector_candidates": [1,8,24,48,96],
                 "max_workers": 1},
      "material_filter": {"enabled": true,
                          "materials": ["Diamond" | {Material fields...}],   # default Diamond
                          "n_mc": 200000, "rng_seed": 0},
      "index": {"method": "ab_initio" | "known_cell",
                "sigma_g": 0.005, "min_reflections": 20, "tol": 0.15,
                "known_cell": {"cell": [a,b,c,alpha,beta,gamma], "space_group": 1,
                               "tol": 0.15, "n_search": 200000, "min_accept": 20}},
      "torch_threads": null
    }

Per-panel keys override the top-level ones; ``omega`` merges key by key.

Result::

    {"status": "ok" | "no_solution" | "error", "error": str | None,
     "project_path": str | None, "cell": [6] | None, "cell_sigma": [6] | None,
     "holohedry": str | None, "conventional_cell": [6] | None,
     "conventional_system": str | None, "rms_drlv": float | None,
     "indexed_fraction": float | None, "n_reflections": int | None,
     "n_spots": int, "n_candidates": int, "n_panels": int,
     "omega": {panel_id: {...resolved mapping...}}, "timings": {stage: seconds},
     "warnings": [...], "log_tail": [...]}

``no_solution`` = the pipeline ran but indexing found no cell (the project is
still written, like the GUI would on an unsuccessful ab-initio run).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from . import pipeline as solve_pipeline

DEFAULT_DATASET = "entry/data/data"
_PREVIEW_ARRAYS = ("mask", "background_subtracted", "background_subtracted_max_projection")

# SolveCellTab's stage widgets and their defaults (``_stage_state_widgets``).
_KS_KEYS = ("a", "b", "c", "alpha", "beta", "gamma", "sg", "n_search", "tol", "min_accept")


# ═════════════════════════════════════════════════════════════════════════════
#  spec handling
# ═════════════════════════════════════════════════════════════════════════════

class SpecError(ValueError):
    """The spec is missing/inconsistent (reported as status='error')."""


def _panel_specs(spec: dict) -> list:
    """Normalised per-panel spec dicts (top-level keys are defaults)."""
    inherit = ("data_path", "dataset", "frame_range", "calibration", "omega")
    raw = spec.get("panels")
    if not raw:
        raw = [{}]
    out, used = [], set()
    for i, p in enumerate(raw):
        merged = {k: spec[k] for k in inherit if k in spec}
        merged.update({k: v for k, v in p.items() if k != "omega"})
        merged["omega"] = {**(spec.get("omega") or {}), **(p.get("omega") or {})}
        pid = int(p.get("panel_id", i + 1))
        if pid in used:
            raise SpecError(f"duplicate panel_id {pid}")
        used.add(pid)
        merged["panel_id"] = pid
        for req in ("data_path", "calibration"):
            if not merged.get(req):
                raise SpecError(f"panel {pid}: {req!r} is required")
        if not Path(merged["data_path"]).is_file():
            raise SpecError(f"panel {pid}: no such data file {merged['data_path']}")
        if not Path(merged["calibration"]).is_file():
            raise SpecError(f"panel {pid}: no such calibration file {merged['calibration']}")
        merged.setdefault("dataset", DEFAULT_DATASET)
        om = merged["omega"]
        for req in ("start", "end"):
            if om.get(req) is None:
                raise SpecError(f"panel {pid}: omega.{req} is required")
        out.append(merged)
    return out


def _load_calibration(path: str) -> dict:
    """Same parse as ``PanelCard._load_calib_from_path`` (tab_solve_cell.py)."""
    from midas_gui.helpers import geometry_fields_from_file
    g = geometry_fields_from_file(path)
    nry = int(g["NrPixelsY"]) if g.get("NrPixelsY") else None
    nrz = int(g["NrPixelsZ"]) if g.get("NrPixelsZ") else None
    geometry = {
        "Lsd": float(g["Lsd"]), "BC_y": float(g["BC_y"]), "BC_z": float(g["BC_z"]),
        "ty": float(g.get("ty") or 0.0), "tz": float(g.get("tz") or 0.0),
        "wavelength_A": float(g["wavelength_A"]), "px_um": float(g["pxY"]),
        "nrpixels_y": nry, "nrpixels_z": nrz,
        "im_trans": [int(c) for c in g.get("im_trans") or []],
        "im_trans_in_file": bool(g.get("im_trans_in_file", False)),
    }
    record = {"path": str(path), "geometry": dict(geometry)}
    try:
        p = Path(path)
        if p.stat().st_size <= 1_000_000:
            record["file_name"] = p.name
            record["file_text"] = p.read_text(errors="replace")
    except OSError:
        pass
    return geometry, record, float(g.get("tx") or 0.0)


def resolve_omega(data_path: str, om: dict, frame_range, n_file_frames: int) -> dict:
    """The panel's omega mapping in FILE frame indices:
    ``omega(frame) = ref_deg + (frame - first_idx) * step``, valid for
    ``first_idx <= frame <= last_idx``. ``first_idx`` auto-detected (first
    frame whose ``field`` omega lies in [start, end], same rule as
    ``helpers.detect_omega_frame_range``) when not given."""
    import h5py
    start, end = float(om["start"]), float(om["end"])
    field = om.get("field") or ""
    lo_f, hi_f = (0, n_file_frames) if frame_range is None else frame_range
    measured = None
    omega_arr = None
    if field:
        with h5py.File(str(data_path), "r") as f:
            if field not in f:
                raise SpecError(f"omega field {field!r} not found in {data_path}")
            omega_arr = np.asarray(f[field][()], float).ravel()
    first = om.get("first_idx")
    if first is None:
        if omega_arr is None:
            first = lo_f
        else:
            lo, hi = (start, end) if start <= end else (end, start)
            idx = np.flatnonzero((omega_arr >= lo) & (omega_arr <= hi))
            idx = idx[(idx >= lo_f) & (idx < hi_f)]
            if idx.size == 0:
                raise SpecError(
                    f"no frames have {field!r} in [{lo:g}, {hi:g}] deg within frames "
                    f"[{lo_f}, {hi_f}) (field range [{omega_arr.min():g}, {omega_arr.max():g}])")
            first = int(idx[0])
    first = int(first)
    if omega_arr is not None and 0 <= first < len(omega_arr):
        measured = float(omega_arr[first])
    step = om.get("step")
    if step is None:
        if omega_arr is None:
            raise SpecError("omega.step is required when no omega.field is given")
        seg = omega_arr[first:hi_f]
        seg = seg[(seg >= min(start, end)) & (seg <= max(start, end))]
        d = np.diff(seg)
        d = d[np.abs(d) > 1e-6]
        if d.size == 0:
            raise SpecError("cannot estimate omega.step from the field; pass it explicitly")
        step = float(np.median(d))
    step = float(step)
    ref = measured if (om.get("ref_from_field") and measured is not None) else start
    if step == 0.0:
        raise SpecError("omega.step is 0 -- every frame would get the same omega")
    n = int(round((end - start) / step))
    last = first + max(n, 0)
    return {"start": start, "end": end, "step": step, "first_idx": first, "last_idx": last,
            "ref_deg": float(ref), "field": field, "measured_first_deg": measured}


def _stage_fields(spec: dict, blobs: dict, mask: dict, index_cfg: dict, mf_state: dict,
                  output_dir: Optional[str]) -> dict:
    """The ``fields`` dict ``SolveCellTab._stage_fields`` records (keys per its
    ``_stage_state_widgets`` + ``material_filter``)."""
    ks = index_cfg.get("known_cell") or {}
    cell = list(ks.get("cell") or (5.0, 5.0, 5.0, 90.0, 90.0, 90.0))
    ks_state = {"a": cell[0], "b": cell[1], "c": cell[2], "alpha": cell[3], "beta": cell[4],
                "gamma": cell[5], "sg": int(ks.get("space_group", 1)),
                "n_search": int(ks.get("n_search", solve_pipeline.RANDOM_SEARCH_N_DEFAULT)),
                "tol": float(ks.get("tol", solve_pipeline.RANDOM_SEARCH_TOL_DEFAULT)),
                "min_accept": int(ks.get("min_accept", solve_pipeline.RANDOM_SEARCH_MIN_ACCEPT_DEFAULT))}
    ks_default = {"a": 5.0, "b": 5.0, "c": 5.0, "alpha": 90.0, "beta": 90.0, "gamma": 90.0,
                  "sg": 1, "n_search": solve_pipeline.RANDOM_SEARCH_N_DEFAULT,
                  "tol": solve_pipeline.RANDOM_SEARCH_TOL_DEFAULT,
                  "min_accept": solve_pipeline.RANDOM_SEARCH_MIN_ACCEPT_DEFAULT}
    gap = int(blobs.get("gap_bridge", solve_pipeline.GAP_BRIDGE_DEFAULT))
    known = index_cfg["method"] == "known_cell"
    fields = {
        "proj_dir": output_dir or "",
        "low_count": float(mask.get("low_count_threshold", solve_pipeline.LOW_COUNT_THRESHOLD_DEFAULT)),
        "mask_grow": int(mask.get("grow", solve_pipeline.MASK_GROW_DEFAULT)),
        "blob_thresh": float(blobs.get("threshold", solve_pipeline.BLOB_THRESHOLD_DEFAULT)),
        "blob_minvol": int(blobs.get("min_vol", solve_pipeline.BLOB_MIN_VOL_DEFAULT)),
        "blob_minframes": int(blobs.get("min_frames", solve_pipeline.BLOB_MIN_FRAMES_DEFAULT)),
        "split_ratio": float(blobs.get("split_ratio", solve_pipeline.SPLIT_RATIO_DEFAULT)),
        "gap_bridge": gap if gap > 0 else solve_pipeline.GAP_BRIDGE_DEFAULT,
        "gap_bridge_on": gap > 0,
        "ingest_max_workers": int(blobs.get("max_workers", 1)),
        "sector_candidates": ",".join(str(int(n)) for n in blobs.get(
            "sector_candidates", solve_pipeline.SECTOR_CANDIDATES_DEFAULT)),
        "sigma_g": float(index_cfg.get("sigma_g", solve_pipeline.SIGMA_G_DEFAULT)),
        "min_refl": int(index_cfg.get("min_reflections", solve_pipeline.MIN_REFLECTIONS_DEFAULT)),
        "tol_override": float(index_cfg.get("tol") if index_cfg.get("tol") is not None
                              else solve_pipeline.AB_INITIO_TOL_DEFAULT),
        "index_method": "Known structure" if known else "Free (ab-initio)",
        "leftover_method": "Free (ab-initio)",
    }
    for key in _KS_KEYS:
        fields[f"ks3_{key}"] = ks_state[key] if known else ks_default[key]
        fields[f"ks5_{key}"] = ks_default[key]
    fields["material_filter"] = mf_state
    return fields


def _material_state(spec_mf: dict) -> dict:
    from . import material_filter as mf
    mats_in = spec_mf.get("materials")
    if mats_in is None:
        mats_in = ["Diamond"]
    mats = []
    for m in mats_in:
        if isinstance(m, str):
            if m not in mf.PRESETS:
                raise SpecError(f"unknown material preset {m!r} (one of {sorted(mf.PRESETS)})")
            mats.append(mf.preset(m).to_dict())
        else:
            mats.append(mf.Material.from_dict(m).to_dict())
    return {"enabled": bool(spec_mf.get("enabled", True)), "materials": mats,
            "n_mc": int(spec_mf.get("n_mc", solve_pipeline.N_MC_DEFAULT)),
            "rng_seed": int(spec_mf.get("rng_seed", solve_pipeline.RNG_SEED_DEFAULT))}


# ═════════════════════════════════════════════════════════════════════════════
#  frame loading
# ═════════════════════════════════════════════════════════════════════════════

def _h5_shape(path: str, dataset: str) -> tuple:
    import h5py
    with h5py.File(str(path), "r") as f:
        if dataset not in f:
            raise SpecError(f"dataset {dataset!r} not found in {path}")
        return tuple(f[dataset].shape)


def _load_frames(path: str, dataset: str, lo: int, hi: int) -> np.ndarray:
    import h5py
    with h5py.File(str(path), "r") as f:
        return np.asarray(f[dataset][lo:hi])        # raw dtype: the sentinel test needs it


# ═════════════════════════════════════════════════════════════════════════════
#  the runner
# ═════════════════════════════════════════════════════════════════════════════

class _LogCapture(io.TextIOBase):
    def __init__(self, sink: list, forward: Optional[Callable[[str], None]]):
        self._sink, self._fwd, self._buf = sink, forward, ""

    def write(self, s):
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._sink.append(line)
            if self._fwd:
                try:
                    self._fwd(line)
                except Exception:
                    pass
        return len(s)

    def flush(self):
        pass


def run_solve_cell_headless(spec: dict, log: Optional[Callable[[str], None]] = None) -> dict:
    """Run the Solve Cell chain described by ``spec`` and write the project.
    Never raises for analysis/spec problems -- see the module docstring for the
    returned dict (``status`` / ``error``)."""
    from midas_gui import project

    t_all = time.time()
    log_lines: list = []
    result = {
        "status": "error", "error": None, "project_path": None, "cell": None, "cell_sigma": None,
        "holohedry": None, "conventional_cell": None, "conventional_system": None,
        "rms_drlv": None, "indexed_fraction": None, "n_reflections": None,
        "n_spots": 0, "n_candidates": 0, "n_panels": 0, "omega": {}, "timings": {},
        "warnings": [], "log_tail": log_lines,
    }
    cap = _LogCapture(log_lines, log)
    try:
        with contextlib.redirect_stdout(cap), contextlib.redirect_stderr(cap):
            _run(spec, result, project)
    except Exception as exc:
        result["status"] = "error"
        result["error"] = (f"{type(exc).__name__}: {exc}" if isinstance(exc, SpecError)
                           else traceback.format_exc())
        # a failure after ingest still leaves the partial project (written by _run)
    finally:
        del log_lines[:-200]
        result["timings"]["total"] = round(time.time() - t_all, 3)
    return result


def _run(spec: dict, result: dict, project) -> None:
    import pandas as pd

    ppath = spec.get("project_path")
    if not ppath:
        raise SpecError("'project_path' is required")
    ppath = Path(ppath).expanduser().resolve()
    if ppath.exists() and not spec.get("overwrite", False):
        raise SpecError(f"{ppath} already exists (pass overwrite=true to replace it)")
    ppath.parent.mkdir(parents=True, exist_ok=True)

    panels = _panel_specs(spec)
    mask_cfg = dict(spec.get("mask") or {})
    live_cfg = dict(spec.get("live_frames") or {})
    blobs_cfg = dict(spec.get("blobs") or {})
    blobs_cfg.setdefault("max_workers", 1)
    index_cfg = dict(spec.get("index") or {})
    index_cfg["method"] = {"ab_initio": "ab_initio", "known_cell": "known_cell"}.get(
        index_cfg.get("method", "ab_initio"))
    if index_cfg["method"] is None:
        raise SpecError("index.method must be 'ab_initio' or 'known_cell'")
    if index_cfg["method"] == "known_cell":
        kc = (index_cfg.get("known_cell") or {}).get("cell")
        if not kc or len(kc) != 6:
            raise SpecError("index.known_cell.cell needs 6 values (a b c alpha beta gamma)")
    mf_state = _material_state(spec.get("material_filter") or {})
    output_dir = spec.get("output_dir")
    out_root = Path(output_dir).expanduser().resolve() if output_dir else None
    sigma_g = float(index_cfg.get("sigma_g", solve_pipeline.SIGMA_G_DEFAULT))
    if spec.get("torch_threads"):
        import torch
        torch.set_num_threads(int(spec["torch_threads"]))

    timings = result["timings"]
    pooled = len(panels) > 1
    pooled_dir = (out_root / "pooled") if out_root else None
    result["n_panels"] = len(panels)

    # ── per-panel setup: calibration, omega mapping, ingest cfg ──────────
    panel_cfgs, panel_info = [], {}
    for p in panels:
        pid = p["panel_id"]
        geometry, calib_record, tx = _load_calibration(p["calibration"])
        shape = _h5_shape(p["data_path"], p["dataset"])
        if len(shape) != 3:
            raise SpecError(f"panel {pid}: dataset {p['dataset']} has shape {shape}, expected 3-D")
        fr = p.get("frame_range")
        lo, hi = (0, shape[0]) if not fr else (max(int(fr[0]), 0), min(int(fr[1]), shape[0]))
        if hi <= lo:
            raise SpecError(f"panel {pid}: empty frame_range {fr} for {shape[0]} frames")
        om = resolve_omega(p["data_path"], p["omega"], None if not fr else (lo, hi), shape[0])
        if not (lo <= om["first_idx"] < hi):
            raise SpecError(f"panel {pid}: omega first_idx {om['first_idx']} lies outside the "
                            f"loaded frames [{lo}, {hi})")
        result["omega"][str(pid)] = om
        if not geometry["im_trans_in_file"]:
            result["warnings"].append(
                f"panel {pid}: calibration does not declare im_trans; none applied")
        # the pipeline sees the loaded sub-stack: shift file indices down by lo
        geometry_run = dict(geometry)
        geometry_run.update({
            "omega_ref_frame_idx": om["first_idx"] - lo, "omega_ref_deg": om["ref_deg"],
            "omega_last_frame_idx": om["last_idx"] - lo, "omega_step_deg": om["step"]})
        # ...but the record keeps file indices (what the GUI would show/reload)
        geometry_rec = dict(geometry)
        geometry_rec.update({
            "omega_ref_frame_idx": om["first_idx"], "omega_ref_deg": om["ref_deg"],
            "omega_last_frame_idx": om["last_idx"], "omega_step_deg": om["step"]})
        pdir = (out_root / solve_pipeline.panel_dir_name(pid) / "data") if out_root else None
        cfg = {
            "geometry": geometry_run, "corrections": {},
            "mask": {"low_count_threshold": mask_cfg.get(
                "low_count_threshold", solve_pipeline.LOW_COUNT_THRESHOLD_DEFAULT),
                "grow": mask_cfg.get("grow", solve_pipeline.MASK_GROW_DEFAULT)},
            "blobs": {"threshold": blobs_cfg.get("threshold", solve_pipeline.BLOB_THRESHOLD_DEFAULT),
                      "min_vol": blobs_cfg.get("min_vol", solve_pipeline.BLOB_MIN_VOL_DEFAULT),
                      "min_frames": blobs_cfg.get("min_frames", solve_pipeline.BLOB_MIN_FRAMES_DEFAULT),
                      "split_ratio": blobs_cfg.get("split_ratio", solve_pipeline.SPLIT_RATIO_DEFAULT),
                      "gap_bridge": blobs_cfg.get("gap_bridge", solve_pipeline.GAP_BRIDGE_DEFAULT),
                      "sector_candidates": tuple(blobs_cfg.get(
                          "sector_candidates", solve_pipeline.SECTOR_CANDIDATES_DEFAULT)),
                      "max_workers": int(blobs_cfg["max_workers"])},
            "panel_dir": pdir, "preview_frame_index": 0,
        }
        if live_cfg:
            cfg["live_frames"] = live_cfg
        cfg["_load"] = (p["data_path"], p["dataset"], lo, hi)
        panel_cfgs.append((pid, cfg))
        panel_info[pid] = {"spec": p, "shape": shape, "lo": lo, "hi": hi, "om": om,
                           "geometry_rec": geometry_rec,
                           "calib_record": calib_record, "tx": tx, "geometry": geometry}

    fields = _stage_fields(spec, blobs_cfg, mask_cfg, index_cfg, mf_state, str(out_root) if out_root else None)

    # Build the project under a private temp name beside the target, rename at the end.
    tmp_dir = tempfile.mkdtemp(prefix=f".{ppath.stem}.", suffix=".partial", dir=str(ppath.parent))
    tmp_path = os.path.join(tmp_dir, ppath.name)
    recorded = False
    try:
        project.create_project(tmp_path, spec.get("project_name") or ppath.stem)

        # ── stage 1: ingest ──────────────────────────────────────────────
        t0 = time.time()
        per_panel = {}
        if not pooled:
            pid, cfg = panel_cfgs[0]
            _attach_frames(cfg)
            ing = solve_pipeline.run_stage("ingest", cfg)
            spots = ing["spots_df"].copy()
            spots["panel_id"] = pid
            per_panel[pid] = ing
            summaries = {pid: ing["summary"]}
            ing_summary = ing.get("summary", {})
        else:
            pcfgs = []
            for pid, cfg in panel_cfgs:
                _attach_frames(cfg)
                cfg["panel_id"] = pid
                pcfgs.append(cfg)
            ing = solve_pipeline.run_stage(
                "ingest_pooled", {"panels": pcfgs, "pooled_dir": pooled_dir})
            spots = ing["spots_df"].copy()
            per_panel = ing["per_panel"]
            summaries = {pid: r["summary"] for pid, r in per_panel.items()}
            ing_summary = ing["summary"]
        timings["ingest"] = round(time.time() - t0, 3)
        result["warnings"] += list(ing_summary.get("warnings", []))
        if "spot_id" not in spots:
            spots["spot_id"] = np.arange(len(spots))
        # loaded_frame_idx is relative to the loaded sub-stack; the record uses file indices
        for pid, r in per_panel.items():
            lo = panel_info[pid]["lo"]
            if lo and "loaded_frame_idx" in spots.columns:
                sel = spots["panel_id"] == pid
                spots.loc[sel, "loaded_frame_idx"] = spots.loc[sel, "loaded_frame_idx"] + lo
        result["n_spots"] = int(len(spots))

        ing_status = (f"{len(spots)} spots" if not pooled else
                      f"{len(spots)} spots across {len(panels)} panels ("
                      + ", ".join(f"panel {pid}: {r['summary']['n_spots']}" for pid, r in per_panel.items()) + ")")
        if not pooled:
            ing_status = f"{len(spots)} spots (n_frames≥{fields['blob_minframes']})"
        panels_meta, panel_arrays = {}, {}
        for pid in per_panel:
            info = panel_info[pid]
            preview = per_panel[pid].get("preview") or {}
            lpath = info["spec"]["data_path"]
            loader_state = {
                "path": lpath, "dataset": f"{info['spec']['dataset']}   {info['shape']}",
                "dark": {"checked": False, "path": "", "dataset": "exchange/data_dark", "start": 0, "end": 0},
                "bright": {"checked": False, "path": "", "dataset": "exchange/data",
                           "start": 0, "end": 0, "mode": 0},
                "background": {"checked": False, "path": "", "dataset": "exchange/data",
                               "start": 0, "end": 0, "scale": 1.0},
                "mask": {"sources": []}, "frame_index": 0,
            }
            om = info["om"]
            state = {"fields": {"ome_start": om["ref_deg"], "ome_end": om["end"], "ome_step": om["step"],
                                "ome_first_idx": om["first_idx"], "ome_field": om["field"]},
                     "loader": loader_state, "calib_path": info["calib_record"]["path"]}
            lfi = preview.get("loaded_frame_index")
            panels_meta[str(pid)] = {
                "panel_id": pid, "state": state, "geometry_cfg": info["geometry_rec"],
                "calibration": info["calib_record"], "summary": summaries[pid],
                "preview_meta": {"frame_index": preview.get("frame_index"),
                                 "loaded_frame_index": None if lfi is None else int(lfi) + info["lo"]},
            }
            arrays = {n: np.asarray(preview[n]) for n in _PREVIEW_ARRAYS if preview.get(n) is not None}
            panel_arrays[solve_pipeline.panel_dir_name(pid)] = arrays
        ingest_meta = {
            "pooled": pooled, "summary": ing_summary, "panels": panels_meta, "fields": fields,
            "active_panel": panel_cfgs[0][0], "next_panel_id": max(pid for pid, _ in panel_cfgs) + 1,
            "source_panel_id": None if pooled else panel_cfgs[0][0],
            "panel_dir": str(out_root / solve_pipeline.panel_dir_name(panel_cfgs[0][0]) / "data")
            if (out_root and not pooled) else None,
            "pooled_dir": str(pooled_dir) if pooled_dir else None,
            "status_text": ing_status,
            # not read by the GUI; provenance of the headless run
            "headless": {"frame_ranges": {str(pid): [i["lo"], i["hi"]] for pid, i in panel_info.items()}},
        }
        project.write_solve_cell_ingest(tmp_path, meta=ingest_meta, spots_df=spots, panels=panel_arrays)
        recorded = True      # a usable record exists: publish it even if a later stage fails

        if len(spots) == 0:
            result["error"] = "ingest found no spots"
            result["status"] = "no_solution"
            return

        # ── stage 2: material filter ─────────────────────────────────────
        t0 = time.time()
        wavelength = panel_info[panel_cfgs[0][0]]["geometry"]["wavelength_A"]
        fcfg = {"spots_df": spots, "materials": mf_state["materials"],
                "filter_enabled": mf_state["enabled"], "n_mc": mf_state["n_mc"],
                "rng_seed": mf_state["rng_seed"], "known_domains": None,
                "panel_dir": None, "pooled_dir": pooled_dir,
                "wavelength_A": wavelength if mf_state["enabled"] else float("nan")}
        dia = solve_pipeline.run_stage("diamond_filter", fcfg)
        timings["material_filter"] = round(time.time() - t0, 3)
        candidate, flagged, dsum = dia["candidate_df"], dia["flagged_df"], dia["summary"]
        n_flagged = dsum.get("n_flagged", dsum.get("n_diamond_flagged", 0))
        if not dsum.get("enabled", True):
            dtext = f"Filter off: all {dsum['n_spots']} spots are candidates"
        else:
            dtext = f"{n_flagged}/{dsum['n_spots']} flagged, {dsum['n_kept']} candidates remain"
            n_prot = int(dsum.get("n_protected", 0) or 0)
            if n_prot:
                dtext += f" ({n_prot} kept: they index on a solved domain)"
        project.write_solve_cell_diamond(
            tmp_path, meta={"summary": dsum, "fields": fields, "status_text": dtext}, flagged_df=flagged)
        result["n_candidates"] = int(len(candidate))
        if len(candidate) == 0:
            result["status"] = "no_solution"
            result["error"] = "material filter left no candidate spots"
            return

        # ── stage 3/4: index + refine ────────────────────────────────────
        t0 = time.time()
        g_all = candidate[["qsample_x", "qsample_y", "qsample_z"]].to_numpy()
        has_panel_col = "panel_id" in candidate.columns
        domains_meta, domain_arrays = [], []
        ab_meta = ab_arrays = None
        sigma_used = None
        ab_status = "—"
        r = None
        if index_cfg["method"] == "ab_initio":
            acfg = {"candidate_df": candidate, "sigma_g": sigma_g,
                    "min_reflections": int(index_cfg.get("min_reflections", solve_pipeline.MIN_REFLECTIONS_DEFAULT)),
                    "pooled_dir": pooled_dir}
            if index_cfg.get("tol") is not None:
                acfg["tol"] = float(index_cfg["tol"])
            else:
                acfg["tol"] = fields["tol_override"]     # the tab always passes its tol field
            ab = solve_pipeline.run_stage("ab_initio", acfg)
            raw, abres = ab["ab_initio_raw"], ab["ab_initio_result"]
            sigma_used = ab["sigma_g"]
            ab_status = (f"success={abres['success']}  indexed {abres['n_indexed']}/"
                         f"{abres['n_reflections']} ({abres['indexed_fraction'] * 100:.1f}%)")
            ab_meta = {"success": bool(raw.success), "cell": raw.cell, "cell_sigma": raw.cell_sigma,
                       "indexed_fraction": float(raw.indexed_fraction),
                       "n_indexed": int(raw.n_indexed), "n_reflections": int(raw.n_reflections),
                       "notes": list(raw.notes), "diagnostics": dict(raw.diagnostics or {})}
            ab_arrays = {n: getattr(raw, n, None) for n in ("hkl", "indexed_mask", "UB", "UBI")}
            result["indexed_fraction"] = float(raw.indexed_fraction)
            if raw.success:
                rr = solve_pipeline.run_stage("refine", {
                    "ab_initio_raw": raw, "g_2pi": ab["g_2pi"], "sigma_g": sigma_used,
                    "pooled_dir": pooled_dir})
                r = rr["refine_result"]
                mask = raw.indexed_mask
                method = "free (ab-initio)"
            else:
                result["error"] = "ab-initio indexing did not succeed: " + "; ".join(raw.notes)
        else:
            ks = index_cfg["known_cell"]
            kcfg = {"g_2pi": g_all, "known_cell": tuple(float(v) for v in ks["cell"]),
                    "space_group": int(ks.get("space_group", 1)),
                    "tol": float(ks.get("tol", solve_pipeline.RANDOM_SEARCH_TOL_DEFAULT)),
                    "n_search": int(ks.get("n_search", solve_pipeline.RANDOM_SEARCH_N_DEFAULT)),
                    "min_accept": int(ks.get("min_accept", solve_pipeline.RANDOM_SEARCH_MIN_ACCEPT_DEFAULT)),
                    "sigma_g": sigma_g, "pooled_dir": pooled_dir}
            if ks.get("rng_seed") is not None:
                kcfg["rng_seed"] = int(ks["rng_seed"])
            res = solve_pipeline.run_stage("index_known_cell", kcfg)
            if res.get("success"):
                r = res
                mask = res["indexed_mask"]
                method = "known structure (manual)"
                result["indexed_fraction"] = float(res["indexed_fraction"])
                ab_status = (f"success=True  indexed {int(mask.sum())}/{len(candidate)}, "
                             f"a={res['cell'][0]:.4g} Å, rms_drlv={res['rms_drlv']:.4g}")
            else:
                result["error"] = "known-cell index found no match: " + "; ".join(res.get("notes", []))
        timings["index"] = round(time.time() - t0, 3)

        if r is not None:
            claimed = candidate.index[mask]
            domains_meta.append({
                "label": "Domain 1", "method": method,
                "result": {k: v for k, v in r.items() if k != "indexed_mask"}})
            arrays = {"claimed_index": np.asarray(claimed),
                      "xyz": np.column_stack([g_all[mask, 0], g_all[mask, 1], g_all[mask, 2]])}
            if has_panel_col:
                arrays["panel_ids"] = candidate.loc[claimed, "panel_id"].to_numpy()
            domain_arrays.append(arrays)

        # Same writes as SolveCellTab._write_sc_index (a negative known-cell result writes nothing).
        if ab_meta is not None or domains_meta:
            index_meta = {
                "fields": fields, "ab_initio": ab_meta, "domains": domains_meta,
                "sigma_g_used": sigma_used, "round1_domain_index": 0 if domains_meta else None,
                "ab_status_text": ab_status, "leftover_run_status_text": "—",
            }
            project.write_solve_cell_index(tmp_path, meta=index_meta, ab_initio=ab_arrays,
                                           domains=domain_arrays)
        if r is None:
            result["status"] = "no_solution"
            return
        result.update({
            "status": "ok", "cell": [float(v) for v in r["cell"]],
            "cell_sigma": [float(v) for v in r["cell_sigma"]],
            "holohedry": r["holohedry_system"],
            "conventional_cell": [float(v) for v in r["conventional_cell"]],
            "conventional_system": r["conventional_system"],
            "rms_drlv": float(r["rms_drlv"]), "n_reflections": int(r["n_reflections"]),
        })
    finally:
        import shutil
        if recorded:
            os.replace(tmp_path, str(ppath))     # atomic: readers never see a half-written file
            result["project_path"] = str(ppath)
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _attach_frames(cfg: dict) -> None:
    path, dataset, lo, hi = cfg.pop("_load")
    print(f"[solve-cell] headless: loading frames [{lo}, {hi}) of {dataset} from {path}")
    cfg["frames"] = _load_frames(path, dataset, lo, hi)


# ═════════════════════════════════════════════════════════════════════════════
#  CLI
# ═════════════════════════════════════════════════════════════════════════════

def main(argv: Optional[list] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="python -m midas_gui.solve_cell.headless",
        description="Run Solve Cell headlessly from a JSON spec and write a MIDAS project .h5. "
                    "The JSON result summary goes to stdout; progress goes to stderr.")
    ap.add_argument("spec", help="path to spec .json (see module docstring)")
    ap.add_argument("--summary", help="also write the result JSON to this file")
    ap.add_argument("--overwrite", action="store_true", help="replace project_path if it exists")
    ap.add_argument("-q", "--quiet", action="store_true", help="no progress on stderr")
    args = ap.parse_args(argv)
    spec = json.loads(Path(args.spec).read_text())
    if args.overwrite:
        spec["overwrite"] = True
    real_err = sys.stderr      # run_solve_cell_headless redirects sys.stderr while it runs
    res = run_solve_cell_headless(
        spec, log=None if args.quiet else (lambda s: print(s, file=real_err, flush=True)))
    text = json.dumps(res, indent=2, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    if args.summary:
        Path(args.summary).write_text(text)
    print(text)
    return {"ok": 0, "no_solution": 2}.get(res["status"], 1)


if __name__ == "__main__":
    sys.exit(main())
