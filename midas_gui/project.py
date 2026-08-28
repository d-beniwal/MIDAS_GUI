"""FAIR provenance "project" files.

A project file is a single, long-lived HDF5 file with two parts:

- ``/workspace`` — a single mutable slot holding the most recently saved
  session snapshot (every tab's live field state), overwritten in place on
  each save. See ``write_workspace``/``read_workspace``.
- ``/<panel_key>/{calib,integrate}/attempt_NNNN`` — an append-only record
  every time a Calibrate or Batch-Integrate run finishes (single-detector
  under ``/single``, Hydra panels under ``/ge1``..``/ge4``). Each attempt
  group is self-sufficient: a JSON ``metadata`` blob (full params, full
  result, resolved input paths + hashes, environment/version snapshot) plus
  the resulting profile/cake arrays. Input correction data (mask/dark/
  bright/background) is never duplicated as raw arrays — only referenced by
  path + hash — except a live/drawn-in-tab mask that was never saved to a
  file, which has no path to hash and is embedded as-is.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import h5py
import numpy as np

PROJECT_MARKER = "__midas_gui_project__"
SCHEMA_VERSION = 2

_HASH_FULL_MAX_BYTES = 200 * 1024 * 1024
_HASH_PARTIAL_CHUNK = 4 * 1024 * 1024


class ProjectContext:
    """Mutable holder for the currently-open project path, shared by
    reference between MainWindow and the tabs/pages that log attempts."""

    def __init__(self):
        self.path: Optional[str] = None


def create_project(path, name: Optional[str] = None) -> str:
    path = str(path)
    if Path(path).exists():
        raise FileExistsError(f"{path} already exists")
    from midas_gui import settings
    with h5py.File(path, "w") as f:
        f.attrs[PROJECT_MARKER] = True
        f.attrs["schema_version"] = SCHEMA_VERSION
        f.attrs["project_name"] = name or Path(path).stem
        f.attrs["created_utc"] = _now_iso()
        try:
            f.attrs["active_profile_at_creation"] = settings.active_profile()
        except Exception:
            pass
    return path


def open_project(path) -> str:
    path = str(path)
    with h5py.File(path, "r") as f:
        if not f.attrs.get(PROJECT_MARKER, False):
            raise ValueError(f"{path} is not a MIDAS GUI project file")
    return path


def project_active_profile(project_path) -> Optional[str]:
    """The beamline Profile active when this project was created (a stable,
    file-level attr — see ``create_project``), or ``None`` for a project
    file predating this feature."""
    with h5py.File(str(project_path), "r") as f:
        return f.attrs.get("active_profile_at_creation")


def write_workspace(project_path, state: dict, sidecars: Optional[dict] = None) -> None:
    """Overwrite the project's ``/workspace`` group with the current session
    snapshot (the merged replacement for the old standalone Workspace JSON
    file) — a single mutable slot, unlike the append-only calibration/
    integration attempt history elsewhere in this file. ``sidecars`` holds
    any not-yet-exported derived data a tab's own ``get_state(sidecar_stem=)``
    wrote (e.g. a live/drawn mask array, an unfitted-but-in-progress
    calibration summary) — see ``app.py``'s ``save_project``."""
    with h5py.File(str(project_path), "a") as f:
        if "workspace" in f:
            del f["workspace"]
        grp = f.create_group("workspace")
        grp.create_dataset("state", data=json.dumps(state, indent=2, default=_json_default))
        grp.attrs["saved_utc"] = _now_iso()
        if sidecars:
            side_grp = grp.create_group("sidecars")
            for name, data in sidecars.items():
                if isinstance(data, (bytes, bytearray, str)):
                    # h5py's VLEN string/bytes type rejects embedded NUL
                    # bytes (common in binary sidecars like a mask .npy
                    # export), raising "VLEN strings do not support
                    # embedded NULLs". Store as opaque uint8 bytes instead,
                    # tagged so read_workspace() can tell it apart from a
                    # genuine uint8 array sidecar (e.g. a mask array) and
                    # reconstruct the original str/bytes.
                    is_str = isinstance(data, str)
                    raw = data.encode("utf-8") if is_str else bytes(data)
                    ds = side_grp.create_dataset(name, data=np.frombuffer(raw, dtype=np.uint8))
                    ds.attrs["_midas_gui_encoding"] = "str" if is_str else "bytes"
                else:
                    _write_array(side_grp, name, data)


def read_workspace(project_path) -> tuple:
    """The most recently saved session snapshot, as ``(state, sidecars)`` —
    both ``{}`` for a project with no ``/workspace`` group yet (a brand-new
    project, or one created before this feature/schema_version 1). Not an
    error case: callers should treat an empty result as "nothing to
    restore," not a failure."""
    with h5py.File(str(project_path), "r") as f:
        grp = f.get("workspace")
        if grp is None:
            return {}, {}
        state = json.loads(grp["state"][()])
        sidecars = {}
        side_grp = grp.get("sidecars")
        if side_grp is not None:
            for name in side_grp.keys():
                ds = side_grp[name]
                value = ds[()]
                encoding = ds.attrs.get("_midas_gui_encoding")
                if encoding == "bytes":
                    value = value.tobytes()
                elif encoding == "str":
                    value = value.tobytes().decode("utf-8")
                sidecars[name] = value
        return state, sidecars


def sha256_file(path) -> dict:
    """Full SHA-256 for files under the size threshold; for larger files
    (e.g. multi-thousand-frame raw datasets) a fast head/tail fingerprint
    instead, so logging an attempt never stalls on hashing a huge file."""
    p = Path(path)
    size = p.stat().st_size
    if size <= _HASH_FULL_MAX_BYTES:
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return {"method": "sha256_full", "sha256": h.hexdigest(), "size_bytes": size}
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        h.update(fh.read(_HASH_PARTIAL_CHUNK))
        if size > _HASH_PARTIAL_CHUNK:
            fh.seek(max(size - _HASH_PARTIAL_CHUNK, 0))
            h.update(fh.read(_HASH_PARTIAL_CHUNK))
    return {
        "method": "sha256_partial",
        "sha256_head_tail": h.hexdigest(),
        "size_bytes": size,
        "mtime": p.stat().st_mtime,
    }


def _safe_version(module_name: str):
    try:
        mod = __import__(module_name)
        return getattr(mod, "__version__", "unknown")
    except Exception:
        return None


def _best_effort_git_commit(directory) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=str(directory),
            capture_output=True, text=True, timeout=2)
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return None


def environment_snapshot() -> dict:
    import platform
    import midas_gui

    env = {
        "midas_gui_version": getattr(midas_gui, "__version__", "unknown"),
        "midas_gui_git_commit": _best_effort_git_commit(Path(midas_gui.__file__).resolve().parent),
        "python_version": platform.python_version(),
    }
    for pkg in ("midas_calibrate_v2", "midas_integrate_v2", "midas_calibrate",
                "midas_hkls", "midas_distortion", "h5py", "numpy"):
        env[f"{pkg}_version"] = _safe_version(pkg)
    try:
        from PyQt5 import QtCore
        env["pyqt_version"] = QtCore.PYQT_VERSION_STR
        env["qt_version"] = QtCore.QT_VERSION_STR
    except Exception:
        pass
    return env


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(obj):
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    if isinstance(obj, Path):
        return str(obj)
    return str(obj)


def _hash_paths_in(obj):
    """Recursively walk a JSON-like structure; any dict with a string
    'path' key pointing to an existing file gets a sibling 'path_hash' key.
    Returns a new structure (does not mutate the input)."""
    if isinstance(obj, dict):
        out = {k: _hash_paths_in(v) for k, v in obj.items()}
        p = obj.get("path")
        if isinstance(p, str) and p and Path(p).is_file():
            try:
                out["path_hash"] = sha256_file(p)
            except Exception as e:
                out["path_hash"] = {"error": str(e)}
        return out
    if isinstance(obj, list):
        return [_hash_paths_in(v) for v in obj]
    return obj


def _sanitize_result_dict(result) -> Optional[dict]:
    """Full result fields, including the underscore-prefixed extras the
    GUI bolts on (_calibrant_name, _panel_unpacked, ...) — unlike the
    existing GUI-state sidecar JSON, nothing is dropped here except
    torch-tensor fields (duck-typed via a '.numpy' attribute), which are
    large and already referenced separately (residual_corr_bin_path)."""
    if result is None:
        return None
    return {k: v for k, v in vars(result).items() if not hasattr(v, "numpy")}


def _write_array(group, name, arr):
    arr = np.asarray(arr)
    kwargs = {}
    if arr.ndim > 0 and arr.size > 0:
        kwargs = dict(compression="gzip", compression_opts=4, chunks=True)
    group.create_dataset(name, data=arr, **kwargs)


def _next_attempt_name(group) -> str:
    existing = [k for k in group.keys() if k.startswith("attempt_")]
    return f"attempt_{len(existing) + 1:04d}"


def append_calibration_attempt(project_path, panel_key, *, cfg, result, loader_state,
                                mask_is_file_backed: bool = False,
                                results: Optional[dict] = None,
                                extra: Optional[dict] = None) -> str:
    cfg_copy = dict(cfg or {})
    mask = cfg_copy.pop("mask", None)
    embed_mask = mask is not None and not mask_is_file_backed

    metadata = {
        "timestamp_utc": _now_iso(),
        "panel_key": panel_key,
        "cfg": _hash_paths_in(cfg_copy),
        "result": _sanitize_result_dict(result),
        "loader_state": _hash_paths_in(loader_state or {}),
        "environment": environment_snapshot(),
        "mask_present": mask is not None,
        "mask_embedded": embed_mask,
    }
    if extra:
        metadata.update(extra)

    with h5py.File(project_path, "a") as f:
        grp = f.require_group(f"{panel_key}/calib")
        name = _next_attempt_name(grp)
        att = grp.create_group(name)
        att.create_dataset("metadata", data=json.dumps(metadata, indent=2, default=_json_default))
        att.attrs["timestamp_utc"] = metadata["timestamp_utc"]
        att.attrs["pipeline"] = str(cfg_copy.get("mode") or "")
        for k in ("Lsd", "BC_y", "BC_z"):
            v = getattr(result, k, None)
            if v is not None:
                att.attrs[k] = float(v)
        if embed_mask:
            _write_array(att, "mask", mask)
        if results:
            res_grp = att.create_group("results")
            for key in ("profile", "r_axis_px", "cake_2d", "eta_axis_deg"):
                if results.get(key) is not None:
                    _write_array(res_grp, key, results[key])
            for k in ("lsd_um", "px_um", "wavelength_A"):
                if results.get(k) is not None:
                    res_grp.attrs[k] = float(results[k])
        grp.attrs["latest"] = name

    return f"/{panel_key}/calib/{name}"


_PANEL_ORDER = ("single", "ge1", "ge2", "ge3", "ge4", "hydra_composite")


def discover_panels(project_path) -> list:
    """Which of the canonical panel groups (single-detector or the 4 Hydra
    GE panels) exist in this project file, in canonical order."""
    with h5py.File(str(project_path), "r") as f:
        return [p for p in _PANEL_ORDER if p in f]


def list_attempts(project_path, panel_key: str, kind: str) -> list:
    """Attempts recorded under ``<panel_key>/<kind>`` (``kind`` is ``"calib"``
    or ``"integrate"``), newest first. Each entry is enough to populate a
    picker without parsing the (possibly large) ``metadata`` JSON blob."""
    with h5py.File(str(project_path), "r") as f:
        grp = f.get(f"{panel_key}/{kind}")
        if grp is None:
            return []
        names = sorted((k for k in grp.keys() if k.startswith("attempt_")), reverse=True)
        return [{"name": n, "ref": f"/{panel_key}/{kind}/{n}",
                 "timestamp_utc": grp[n].attrs.get("timestamp_utc", "")}
                for n in names]


def read_attempt(project_path, ref: str) -> dict:
    """Parsed ``metadata`` JSON for one attempt, given a ref such as
    ``/ge1/calib/attempt_0003`` (as returned by ``append_*_attempt`` /
    ``list_attempts``)."""
    with h5py.File(str(project_path), "r") as f:
        return json.loads(f[ref.lstrip("/")]["metadata"][()])


def read_attempt_results(project_path, ref: str) -> dict:
    """The embedded 1-D result arrays for an *integration* attempt
    (``profiles``/``r_axis_px``/``sigmas``/``frame_ids``) — these live as
    raw HDF5 datasets under ``<ref>/results``, separate from the JSON
    ``metadata`` blob ``read_attempt`` returns (see
    ``append_integration_attempt``). Returns {} if the attempt has no
    ``results`` group (e.g. a calibration attempt, or an aborted run with
    zero frames)."""
    with h5py.File(str(project_path), "r") as f:
        grp = f.get(f"{ref.lstrip('/')}/results")
        if grp is None:
            return {}
        out = {}
        for key in ("profiles", "r_axis_px", "sigmas"):
            if key in grp:
                out[key] = grp[key][()]
        if "frame_ids" in grp:
            out["frame_ids"] = [v.decode() if isinstance(v, bytes) else v
                                 for v in grp["frame_ids"][()]]
        return out


def read_calib_attempt_results(project_path, ref: str) -> dict:
    """The embedded cake/profile arrays for a *calibration* attempt (see
    ``append_calibration_attempt``'s ``results`` kwarg) — ``profile``,
    ``r_axis_px``, ``cake_2d``, ``eta_axis_deg`` plus scalar
    ``lsd_um``/``px_um``/``wavelength_A``. Returns {} if the attempt has no
    ``results`` group (e.g. an attempt logged before this feature existed,
    or integration never ran for it)."""
    with h5py.File(str(project_path), "r") as f:
        grp = f.get(f"{ref.lstrip('/')}/results")
        if grp is None:
            return {}
        out = {}
        for key in ("profile", "r_axis_px", "cake_2d", "eta_axis_deg"):
            if key in grp:
                out[key] = grp[key][()]
        for k in ("lsd_um", "px_um", "wavelength_A"):
            if k in grp.attrs:
                out[k] = float(grp.attrs[k])
        return out


def calib_attempt_gui_fields(meta: dict) -> dict:
    """Map a parsed calibration-attempt ``metadata`` dict to the
    widget-keyed field dict consumed by ``CalibrationTab``/
    ``HydraCalibrationPage``/``HydraCalibPanelCard``'s own
    ``_state_widgets()``/``state_widgets()`` (applied via
    ``helpers.apply_dict_to_widgets``). The single-detector tab, the Hydra
    page's shared "recipe" fields, and one Hydra panel card's seed fields are
    three different (non-overlapping) subsets of the same widget-key
    vocabulary, so one dict can be handed to all three — each just ignores
    the keys it doesn't define."""
    cfg = meta.get("cfg") or {}
    result = meta.get("result") or {}
    refine = cfg.get("refine") or {}
    im_trans = set(cfg.get("im_trans") or [])
    fields = {
        "wl": cfg.get("wavelength"),
        "cal": cfg.get("calibrant"),
        "pxY": cfg.get("pxY"),
        "flip_y": 1 in im_trans, "flip_z": 2 in im_trans, "transp": 3 in im_trans,
        "manual_seed_check": True,
        "seed_bcy": result.get("BC_y"), "seed_bcz": result.get("BC_z"),
        "seed_tx": result.get("tx", 0.0), "seed_ty": result.get("ty", 0.0),
        "seed_tz": result.get("tz", 0.0),
        "ref_lsd": refine.get("Lsd"), "ref_bc": refine.get("BC"),
        "ref_ty": refine.get("ty"), "ref_tz": refine.get("tz"), "ref_tx": refine.get("tx"),
        "ref_wl": refine.get("Wavelength"), "ref_dist": refine.get("Distortion"),
        "build_rc": cfg.get("build_residual_corr"),
        "n_iter": cfg.get("n_iter"), "lm_iter": cfg.get("lm_max_iter"),
        "device": cfg.get("device"),
    }
    lsd = result.get("Lsd")
    if lsd is not None:
        fields["seed_lsd"] = float(lsd) / 1000.0   # µm (stored) -> mm (display)
    pxZ = cfg.get("pxZ")
    if pxZ is not None:
        fields["pxZ_check"] = True
        fields["pxZ_spin"] = pxZ
    return {k: v for k, v in fields.items() if v is not None}


def calib_attempt_loader_state(meta: dict) -> dict:
    """The subset of a calibration attempt's ``loader_state`` that
    ``DataLoaderPanel.set_state()`` understands (single-detector mode)."""
    ls = meta.get("loader_state") or {}
    out = {}
    if ls.get("path"):
        out["path"] = ls["path"]
    if ls.get("dataset"):
        out["dataset"] = ls["dataset"]
    if ls.get("frame_index") is not None:
        out["frame_index"] = ls["frame_index"]
    return out


def integrate_attempt_gui_fields(meta: dict) -> dict:
    """Map a parsed integration-attempt ``metadata`` dict to the
    widget-keyed field dict consumed by ``BatchTab``/``HydraBatchPage``'s
    ``_state_widgets()`` (applied via ``helpers.apply_dict_to_widgets``).
    Combo-box fields (kernel, output format) are stored by their short key
    but the widgets are populated by their display label, so both are
    translated via ``constants.KERNELS``/``constants.OUTPUT_FORMATS``."""
    from midas_gui.constants import KERNELS, OUTPUT_FORMATS

    inputs = meta.get("inputs") or {}
    fields = {}
    kernel_label = {v: k for k, v in KERNELS.items()}.get(inputs.get("kernel"))
    if kernel_label:
        fields["kernel"] = kernel_label
    # "fmt" is a list[str] of OUTPUT_FORMATS keys as of the checkbox-list
    # output-format selector; older project files recorded a single string —
    # wrap it so both shapes feed widgets.OutputFormatSelector.set_state the
    # same way.
    fmt_val = inputs.get("fmt")
    if fmt_val:
        fmt_keys = fmt_val if isinstance(fmt_val, list) else [fmt_val]
        valid_keys = set(OUTPUT_FORMATS.values())
        fmt_keys = [k for k in fmt_keys if k in valid_keys]
        if fmt_keys:
            fields["fmt_keys"] = fmt_keys
    if inputs.get("monitor_file"):
        fields["mon_ed"] = inputs["monitor_file"]
    q_cfg = inputs.get("q_cfg")
    if q_cfg:
        fields["q_check"] = True
        if q_cfg.get("QMin") is not None:
            fields["q_min"] = q_cfg["QMin"]
        if q_cfg.get("QMax") is not None:
            fields["q_max"] = q_cfg["QMax"]
        if q_cfg.get("QBinSize") is not None:
            fields["q_bin"] = q_cfg["QBinSize"]
    return fields


def integrate_attempt_loader_state(meta: dict) -> dict:
    """The subset of an integration attempt's ``inputs`` that
    ``DataLoaderPanel.set_state()`` (stream mode) understands: path/dataset
    plus the frame range as ``fr_start``/``fr_end``/``fr_stride``."""
    inputs = meta.get("inputs") or {}
    src = inputs.get("src_cfg") or {}
    out = {}
    if src.get("path"):
        out["path"] = src["path"]
    if src.get("dataset"):
        out["dataset"] = src["dataset"]
    frame_range = inputs.get("frame_range")
    if frame_range:
        start, end, stride = (list(frame_range) + [None, None, None])[:3]
        if start is not None:
            out["fr_start"] = start
        out["fr_end"] = end if end is not None else 0
        if stride is not None:
            out["fr_stride"] = stride
    return out


def calibration_namespace(calibration_snapshot: dict):
    """Turn a stored ``calibration_snapshot`` (or a calibration attempt's
    ``result`` dict) into a duck-typed object with the attributes
    ``BatchTab.set_calibration``/``HydraBatchPanelCard.set_calibration`` and
    the integration-spec builders expect — mirrors
    ``helpers.result_ns_from_geometry_file``'s shape."""
    from types import SimpleNamespace
    snap = dict(calibration_snapshot or {})
    snap.setdefault("residual_corr_bin_path", None)
    return SimpleNamespace(**snap)


def append_integration_attempt(project_path, panel_key, *, inputs, finished_payload,
                                calibration_snapshot=None, calib_attempt_ref=None,
                                mask=None, mask_is_file_backed: bool = False,
                                extra: Optional[dict] = None) -> str:
    payload = dict(finished_payload or {})
    profiles = payload.pop("profiles", None)
    r_axis = payload.pop("r_axis_px", None)
    sigmas = payload.pop("sigmas", None)
    frame_ids = payload.pop("frame_ids", None)
    embed_mask = mask is not None and not mask_is_file_backed

    metadata = {
        "timestamp_utc": _now_iso(),
        "panel_key": panel_key,
        "inputs": _hash_paths_in(inputs or {}),
        "n_frames": payload.get("n"),
        "out_paths": payload.get("out_paths"),
        "aborted": payload.get("aborted", False),
        "calibration_snapshot": calibration_snapshot,
        "calib_attempt_ref": calib_attempt_ref,
        "environment": environment_snapshot(),
        "mask_present": mask is not None,
        "mask_embedded": embed_mask,
    }
    if extra:
        metadata.update(extra)

    with h5py.File(project_path, "a") as f:
        grp = f.require_group(f"{panel_key}/integrate")
        name = _next_attempt_name(grp)
        att = grp.create_group(name)
        att.create_dataset("metadata", data=json.dumps(metadata, indent=2, default=_json_default))
        att.attrs["timestamp_utc"] = metadata["timestamp_utc"]
        if metadata["n_frames"] is not None:
            att.attrs["n_frames"] = int(metadata["n_frames"])
        if inputs and inputs.get("kernel"):
            att.attrs["kernel"] = str(inputs["kernel"])
        if calib_attempt_ref:
            att.attrs["calib_attempt_ref"] = calib_attempt_ref
        if embed_mask:
            _write_array(att, "mask", mask)

        res_grp = att.create_group("results")
        if profiles is not None:
            _write_array(res_grp, "profiles", profiles)
        if r_axis is not None:
            _write_array(res_grp, "r_axis_px", r_axis)
        if sigmas is not None:
            _write_array(res_grp, "sigmas", sigmas)
        if frame_ids is not None:
            res_grp.create_dataset("frame_ids", data=np.array(list(frame_ids), dtype=h5py.string_dtype()))
        grp.attrs["latest"] = name

    return f"/{panel_key}/integrate/{name}"
