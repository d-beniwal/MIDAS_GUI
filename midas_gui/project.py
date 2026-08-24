"""FAIR provenance "project" files.

A project file is a single, long-lived HDF5 file that accumulates one
append-only record every time a Calibrate or Batch-Integrate run finishes
(single-detector under ``/single``, Hydra panels under ``/ge1``..``/ge4``).
Each attempt group is self-sufficient: a JSON ``metadata`` blob (full
params, full result, resolved input paths + hashes, environment/version
snapshot) plus a few small embedded arrays (mask/dark/bright/background,
and for integration the resulting profiles). Raw multi-frame datasets are
never duplicated here — only referenced by path + hash.
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
SCHEMA_VERSION = 1

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
    with h5py.File(path, "w") as f:
        f.attrs[PROJECT_MARKER] = True
        f.attrs["schema_version"] = SCHEMA_VERSION
        f.attrs["project_name"] = name or Path(path).stem
        f.attrs["created_utc"] = _now_iso()
    return path


def open_project(path) -> str:
    path = str(path)
    with h5py.File(path, "r") as f:
        if not f.attrs.get(PROJECT_MARKER, False):
            raise ValueError(f"{path} is not a MIDAS GUI project file")
    return path


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
                                dark=None, bright=None, background=None,
                                extra: Optional[dict] = None) -> str:
    cfg_copy = dict(cfg or {})
    mask = cfg_copy.pop("mask", None)

    metadata = {
        "timestamp_utc": _now_iso(),
        "panel_key": panel_key,
        "cfg": _hash_paths_in(cfg_copy),
        "result": _sanitize_result_dict(result),
        "loader_state": _hash_paths_in(loader_state or {}),
        "environment": environment_snapshot(),
        "mask_embedded": mask is not None,
        "dark_embedded": dark is not None,
        "bright_embedded": bright is not None,
        "background_embedded": background is not None,
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
        if mask is not None:
            _write_array(att, "mask", mask)
        if dark is not None:
            _write_array(att, "dark", dark)
        if bright is not None:
            _write_array(att, "bright", bright)
        if background is not None:
            _write_array(att, "background", background)
        grp.attrs["latest"] = name

    return f"/{panel_key}/calib/{name}"


def append_integration_attempt(project_path, panel_key, *, inputs, finished_payload,
                                calibration_snapshot=None, calib_attempt_ref=None,
                                mask=None, dark=None, bright=None, background=None,
                                extra: Optional[dict] = None) -> str:
    payload = dict(finished_payload or {})
    profiles = payload.pop("profiles", None)
    r_axis = payload.pop("r_axis_px", None)
    sigmas = payload.pop("sigmas", None)
    frame_ids = payload.pop("frame_ids", None)

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
        "mask_embedded": mask is not None,
        "dark_embedded": dark is not None,
        "bright_embedded": bright is not None,
        "background_embedded": background is not None,
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
        if mask is not None:
            _write_array(att, "mask", mask)
        if dark is not None:
            _write_array(att, "dark", dark)
        if bright is not None:
            _write_array(att, "bright", bright)
        if background is not None:
            _write_array(att, "background", background)

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
