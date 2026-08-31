"""Export one "final" Batch-Integrate attempt as a native GSAS-II zarr.

Writes ``<out_path>`` (a ``.zarr.zip``) using
``midas_integrate_v2.io.zarr_gsas.write_gsas_zarr_zip`` directly — that
function already reproduces the exact layout GSAS-II's built-in
``G2pwd_MIDAS.py`` "MIDAS zarr" importer (and MIDAS's own
``gsas_ii_refine.py``) expect, bit-for-bit. This module's job is only to
rebuild the inputs that writer needs (a spec, a per-frame cake, a bin-area
array) from ONE attempt already logged in a midas-gui project file — the
project's append-only attempt history is never touched or exported wholesale.

A ``<out_path>.provenance.json`` sidecar carries the attempt's full metadata
(params, hashed input paths, environment snapshot, calibration snapshot)
verbatim, plus a few export-specific fields — mirroring the ``.samprm``/
``.instprm`` sidecar convention ``G2pwd_MIDAS.py`` already uses, so it adds
zero risk to the zip's internal structure.

Scope (v1): single-detector Batch Integrate attempts only, R-uniform binning
only (a Q-uniform attempt's stored ``r_axis_px`` is Q-rebinned, not a simple
function of ``spec``, so the geometry/bin-area can't be reconstructed from it
here). Works whether the attempt's stored ``profiles`` is 2-D (today's
default, single full-circle profile per frame — degenerates to one azimuth)
or 3-D (multi-azimuth "cake" mode, see ``tab_batch.py``'s checkbox).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import h5py
import numpy as np

from midas_gui import project
from midas_gui.helpers import _build_spec, _apply_im_trans
from midas_gui.workers import build_integration_context


def _read_embedded_mask(project_path, ref: str) -> Optional[np.ndarray]:
    with h5py.File(str(project_path), "r") as f:
        grp = f.get(ref.lstrip("/"))
        if grp is None or "mask" not in grp:
            return None
        return grp["mask"][()]


def export_gsas_zarr(project_path, panel_key: str, attempt_ref: str, out_path) -> Path:
    """Write a MIDAS-native GSAS-II ``.zarr.zip`` (+ provenance sidecar) from
    one integration attempt. Returns the zarr path actually written.

    Raises ``ValueError`` for conditions this v1 doesn't support (no results,
    no calibration snapshot, Q-uniform binning, a file-backed mask that isn't
    embedded in the project) rather than silently producing a wrong export.
    """
    meta = project.read_attempt(project_path, attempt_ref)
    results = project.read_attempt_results(project_path, attempt_ref)
    if not results or "profiles" not in results or "r_axis_px" not in results:
        raise ValueError(f"Attempt {attempt_ref} has no integration results to export.")

    calib_snapshot = meta.get("calibration_snapshot")
    if not calib_snapshot:
        raise ValueError(
            f"Attempt {attempt_ref} has no calibration snapshot recorded — "
            "cannot rebuild the geometry needed for a GSAS-II export.")

    inputs = meta.get("inputs") or {}
    if inputs.get("q_cfg"):
        raise ValueError(
            f"Attempt {attempt_ref} was run with Q-uniform binning — its "
            "stored r_axis_px is Q-rebinned, not a simple function of the "
            "calibration geometry, so the 2θ/bin-area GSAS-II needs can't "
            "be reconstructed from it. Re-run this attempt with Q-uniform "
            "bins unchecked to make it exportable.")

    if meta.get("mask_present") and not meta.get("mask_embedded"):
        raise ValueError(
            f"Attempt {attempt_ref}'s mask was file-backed, not embedded "
            "in the project — GSAS-II export currently only supports "
            "attempts whose mask was embedded (drawn live / not loaded "
            "from a file). Re-run Batch Integrate with an embedded mask "
            "to make this attempt exportable.")

    profiles = np.asarray(results["profiles"])
    r_axis_px = np.asarray(results["r_axis_px"])
    frame_ids = results.get("frame_ids") or []
    multi_azimuth = profiles.ndim == 3

    result_ns = project.calibration_namespace(calib_snapshot)
    r_bin = float(inputs.get("r_bin", 1.0))
    if multi_azimuth:
        e_bin = float(inputs.get("e_bin", 5.0))
    else:
        # The original run's η bin (default 5° × 72 internal bins) was only ever
        # used for internal collapse-weighting, not output shape — force a
        # single azimuth here to match the single profile stored per frame.
        e_bin = 360.0

    spec = _build_spec(result_ns, r_bin, e_bin)
    spec.validate()
    if int(spec.n_r_bins) != r_axis_px.size:
        raise ValueError(
            "Rebuilt geometry's radial binning doesn't match this attempt's "
            f"stored r_axis_px ({spec.n_r_bins} vs {r_axis_px.size} bins) — "
            "its recorded R bin size may be stale or from an older schema.")

    mask = None
    if meta.get("mask_present"):
        mask = _read_embedded_mask(project_path, attempt_ref)
        im_trans = tuple(getattr(spec, "TransOpt", None) or ())
        if mask is not None and im_trans:
            mask = _apply_im_trans(mask.astype(np.float32), im_trans)

    # Bin area is a property of geometry + mask alone (polarization/solid-angle
    # corrections reweight intensity within a bin, not which pixels fall in
    # it), so it's reconstructed the same way regardless of what corrections
    # the original run used.
    kernel = inputs.get("kernel", "subpixel2")
    ctx = build_integration_context(spec, kernel, mask, (None, None), weighted=True)
    bin_area = ctx["cnt"]
    if bin_area is None:
        raise ValueError(
            f"Could not derive a bin-area array for attempt {attempt_ref}'s geometry.")

    from midas_integrate_v2.io.zarr_gsas import write_gsas_zarr_zip

    out_path = Path(out_path)
    if not str(out_path).endswith(".zarr.zip"):
        out_path = Path(str(out_path) + ".zarr.zip")

    n_frames = profiles.shape[0]
    cakes = (profiles[i] if multi_azimuth else profiles[i][None, :]
             for i in range(n_frames))
    write_gsas_zarr_zip(out_path, cakes, spec=spec,
                        omegas=[float(i) for i in range(n_frames)],
                        bin_area=bin_area)

    provenance = dict(meta)
    provenance["source_project"] = str(Path(project_path).resolve())
    provenance["panel_key"] = panel_key
    provenance["attempt_ref"] = attempt_ref
    provenance["export_timestamp_utc"] = datetime.now(timezone.utc).isoformat()
    provenance["frame_ids"] = list(frame_ids)
    prov_path = Path(str(out_path) + ".provenance.json")
    prov_path.write_text(json.dumps(provenance, indent=2, default=str))

    return out_path
