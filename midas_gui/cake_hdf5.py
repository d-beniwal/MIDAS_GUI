"""Cake (multi-azimuth) HDF5 output for Batch Integrate.

``midas_integrate_v2.write_h5`` only accepts a 2-D ``(N, n_r)`` profile stack
— a full ``(N, n_eta, n_r)`` cake has nowhere to go in it. Rather than
reimplement a NeXus-shaped HDF5 file from scratch, :func:`write_cake_h5` rides
on ``write_h5``'s existing ``extra_datasets``/``metadata`` mechanism (each
extra dataset also gets a root soft-link) to carry the cake alongside a real
1-D profile, and names the extra datasets after the MIDAS ``.zarr.zip``
layout (``REtaMap``, ``SumFrames``, ``Omegas``, ``InstrumentParameters/...``,
see ``midas_integrate_v2.io.zarr_gsas``) so a reader already familiar with
that zarr layout recognizes this one immediately — with a frame-stack axis
instead of zarr's one-file-per-frame, and the full calibration embedded.

Unlike the zarr writer, this bundles every processed frame (or "Combine
sub-frames" chunk) from one run into a single file.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np

__all__ = ["write_cake_h5"]


def write_cake_h5(path, *, cake, cake_sigma, r_axis, eta_axis, frame_ids,
                  spec, bin_area=None, calibration_snapshot: Optional[dict] = None,
                  kernel: Optional[str] = None, weighted: Optional[bool] = None,
                  cake_params: Optional[dict] = None,
                  collapsed_profiles: Optional[np.ndarray] = None,
                  collapsed_sigmas: Optional[np.ndarray] = None) -> Path:
    """Write one HDF5 file holding every frame's cake plus a real 1-D profile.

    ``cake``/``cake_sigma``: ``(N, n_eta, n_r)``, v2's native axis order —
    transposed here to ``(N, n_r, n_eta)`` to match the zarr layout's own
    ``REtaMap``/``SumFrames``/``OmegaSumFrame`` convention, so the whole file
    stays internally self-consistent.

    ``collapsed_profiles``/``collapsed_sigmas`` (``(N, n_r)``): pass the real
    engine-collapsed 1-D lineout when the caller has it (a live run does, via
    ``integrate_frame``'s own ``prof``/``sigma`` return values). When omitted,
    a masked eta-mean over ``cake`` is used instead (see
    ``helpers.collapse_cake_eta``) — an approximation, not a reproduction, of
    the engine's own collapse.

    ``bin_area`` (pixel-count cake from ``workers.count_cake``) populates
    ``REtaMap``'s BinArea row; when unavailable, that row is left at zero
    (``reta_map`` itself warns about this).
    """
    import midas_integrate_v2 as m
    from midas_integrate_v2.io.zarr_gsas import reta_map, instrument_params_from_spec
    from midas_gui.helpers import collapse_cake_eta
    from midas_gui.project import json_default

    path = Path(path)
    cake = np.asarray(cake, dtype=np.float64)
    cake_sigma = np.asarray(cake_sigma, dtype=np.float64)
    if cake.ndim != 3:
        raise ValueError(f"cake must be 3-D (N, n_eta, n_r); got shape {cake.shape}")
    n_frames = cake.shape[0]

    if collapsed_profiles is None:
        collapsed_profiles = collapse_cake_eta(cake)
    if collapsed_sigmas is None:
        collapsed_sigmas = collapse_cake_eta(cake_sigma)
    collapsed_profiles = np.asarray(collapsed_profiles, dtype=np.float64)
    collapsed_sigmas = np.asarray(collapsed_sigmas, dtype=np.float64)

    # Store in the zarr layout's own (n_r, n_eta) axis order, not v2's native
    # (n_eta, n_r) — so REtaMap/cake/SumFrames all agree within this file.
    cake_store = np.ascontiguousarray(cake.transpose(0, 2, 1))
    cake_sigma_store = np.ascontiguousarray(cake_sigma.transpose(0, 2, 1))
    sum_frames = cake_store.sum(axis=0)
    omegas = np.arange(n_frames, dtype=np.float64)

    instrument_params = instrument_params_from_spec(spec)
    extra_datasets = {
        "cake": cake_store,
        "cake_sigma": cake_sigma_store,
        "eta_deg": np.asarray(eta_axis, dtype=np.float64),
        "REtaMap": reta_map(spec, bin_area),
        "SumFrames": sum_frames,
        "Omegas": omegas,
        **{f"InstrumentParameters/{k}": np.array([v], dtype=np.float64)
           for k, v in instrument_params.items()},
    }

    metadata = m.ProfileMetadata(
        integrate_mode=kernel or "subpixel2",
        n_r_bins=int(spec.n_r_bins), n_eta_bins=int(spec.n_eta_bins),
        spec_summary=dict(cake_params or {}),
        extra={"weighted": bool(weighted), "multi_azimuth": True},
    )

    m.write_h5(str(path), profiles=collapsed_profiles, r_axis=np.asarray(r_axis),
               frame_ids=list(frame_ids), sigmas=collapsed_sigmas,
               metadata=metadata, extra_datasets=extra_datasets)

    if calibration_snapshot is not None:
        import h5py
        with h5py.File(path, "a") as f:
            f["entry"].attrs["calibration_snapshot_json"] = json.dumps(
                calibration_snapshot, default=json_default)

    return path
