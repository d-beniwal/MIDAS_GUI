"""Cake (multi-azimuth) HDF5 output for Batch Integrate.

``midas_integrate_v2.write_h5`` only accepts a 2-D ``(N, n_r)`` profile stack
— a full ``(N, n_eta, n_r)`` cake has nowhere to go in it — so this module
writes its own file directly with h5py instead of riding on ``write_h5``'s
NeXus ``entry/data``/``entry/extra`` nesting and root soft-link mechanism.
Every dataset lives at exactly one root-level path.

This file is a GUI-native archive, not a GSAS-II input: GSAS-II's own
importer (``G2pwd_MIDAS.py``) only ever opens ``.zarr.zip`` and never an
``.h5``, so nothing here needs to mirror the zarr ``REtaMap``/``OmegaSumFrame``
convention — that convention is reproduced faithfully elsewhere, by the
external ``midas_integrate_v2.io.zarr_gsas.write_gsas_zarr_zip`` (inline
per-frame zarr) and by this repo's own ``gsas_export.py`` (on-demand
project-attempt export), neither of which this module touches.

Unlike the zarr writer, this bundles every processed frame (or "Combine
sub-frames" chunk) from one run into a single file.
"""
from __future__ import annotations

import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

from midas_gui.helpers import collapse_cake_eta, radial_axes_from_r_px

__all__ = ["write_cake_h5"]


def write_cake_h5(path, *, cake, cake_sigma, r_axis, eta_axis, frame_ids,
                  spec, bin_area=None, kernel: Optional[str] = None,
                  weighted: Optional[bool] = None,
                  cake_params: Optional[dict] = None,
                  collapsed_profiles: Optional[np.ndarray] = None,
                  collapsed_sigmas: Optional[np.ndarray] = None) -> Path:
    """Write one flat HDF5 file holding every frame's cake plus a real 1-D
    profile. Calibration/run provenance is NOT written here — the caller
    stamps it afterward via ``provenance.build_entry``/``stamp_h5_provenance``
    (see ``workers.py``), the same attrs-only mechanism used for every other
    Batch Integrate output.

    ``cake``/``cake_sigma`` are stored ``(N, n_eta, n_r)`` — v2's own native
    axis order, so ``cake[frame, eta_idx, :]`` is directly the radial profile
    for that azimuthal wedge, no transpose needed.

    The *stored* ``cake`` is not the raw per-bin mean intensity the engine
    computes: it's that mean reweighted by each bin's share of the total
    pixel-count coverage at that radius (``bin_area / bin_area.sum(eta)``),
    so that ``cake.sum(axis=eta) == profiles`` exactly — a caller can
    reconstruct the full profile with a plain sum, not a bin_area-weighted
    one. ``cake_sigma`` carries the same per-bin weight (the correct
    propagated uncertainty of the reweighted value), so
    ``sqrt((cake_sigma**2).sum(axis=eta)) == profile sigma`` (quadrature,
    since the wedges are independent — summing a plain sum of sigmas is
    never meaningful for uncertainties). Recovering the original per-wedge
    mean intensity from the stored ``cake`` needs
    ``cake * bin_area.sum(eta) / bin_area`` (undefined, left as 0, wherever
    ``bin_area`` is 0). Without a ``bin_area`` (see below), ``cake`` falls
    back to the unweighted mean and the sum-equals-profile invariant does
    not hold — this is flagged both by a warning and by the file's own
    ``cake_weighting`` attr.

    ``collapsed_profiles``/``collapsed_sigmas`` (``(N, n_r)``): pass the real
    engine-collapsed 1-D lineout when the caller has it (a live run does, via
    ``integrate_frame``'s own ``prof``/``sigma`` return values) — this is
    exactly what the bin_area-weighted reduction above reproduces. When
    omitted, a masked eta-mean over the (unweighted) ``cake`` is used
    instead (see ``helpers.collapse_cake_eta``) — an approximation, not a
    reproduction, of the engine's own collapse.

    ``bin_area`` (pixel-count cake from ``workers.count_cake``), accepted as
    either ``(n_eta, n_r)`` or ``(n_r, n_eta)``: the per-bin pixel-count
    weight backing the reweighting above, stored verbatim as ``bin_area``
    ``(n_eta, n_r)``.
    """
    import h5py
    import midas_integrate_v2 as m

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cake = np.asarray(cake, dtype=np.float64)
    cake_sigma = np.asarray(cake_sigma, dtype=np.float64)
    if cake.ndim != 3:
        raise ValueError(f"cake must be 3-D (N, n_eta, n_r); got shape {cake.shape}")
    n_frames, n_eta, n_r = cake.shape

    if collapsed_profiles is None:
        collapsed_profiles = collapse_cake_eta(cake)
    if collapsed_sigmas is None:
        collapsed_sigmas = collapse_cake_eta(cake_sigma)
    collapsed_profiles = np.asarray(collapsed_profiles, dtype=np.float64)
    collapsed_sigmas = np.asarray(collapsed_sigmas, dtype=np.float64)

    r_axis = np.asarray(r_axis, dtype=np.float64)
    axes = radial_axes_from_r_px(r_axis, float(spec.Lsd), float(spec.pxY),
                                 float(spec.Wavelength))

    area = None
    if bin_area is not None:
        area = np.asarray(bin_area, dtype=np.float64)
        if area.shape == (n_r, n_eta):
            area = area.T
        if area.shape != (n_eta, n_r):
            raise ValueError(
                f"bin_area shape {area.shape} is neither (n_eta, n_r) "
                f"{(n_eta, n_r)} nor (n_r, n_eta) {(n_r, n_eta)}")

    if area is not None:
        # weight[eta, r] = this wedge's share of the total pixel coverage at
        # r — sum_eta(mean_i * weight_i) is the pixel-count-weighted average,
        # which is exactly what the engine's own full-circle profile is.
        area_total = area.sum(axis=0)   # (n_r,)
        with np.errstate(divide="ignore", invalid="ignore"):
            weight = np.where(area_total > 0, area / area_total[None, :], 0.0)
        cake_store = cake * weight[None, :, :]
        cake_sigma_store = cake_sigma * weight[None, :, :]
        cake_weighting = "bin_area-weighted: cake.sum(eta) == profiles exactly"
    else:
        cake_store = cake
        cake_sigma_store = cake_sigma
        cake_weighting = ("unweighted per-bin mean — no bin_area was supplied, "
                          "so cake.sum(eta) does NOT reproduce profiles")
        warnings.warn(
            "write_cake_h5: no bin_area supplied — 'cake' stores the raw "
            "per-bin mean intensity, and summing it over the eta axis will "
            "NOT reproduce 'profiles' (that needs bin_area's pixel-count "
            "weighting). Pass bin_area (workers.count_cake) to get the "
            "sum-equals-profile guarantee.", stacklevel=2)

    with h5py.File(path, "w") as f:
        f.attrs["description"] = (
            "midas_gui Batch Integrate multi-azimuth ('cake') output. "
            "Not a GSAS-II input file — see gsas_export.py / the Zarr "
            "export for that.")
        f.attrs["package"] = "midas_gui.cake_hdf5"
        f.attrs["integrate_backend"] = "midas_integrate_v2"
        f.attrs["integrate_backend_version"] = getattr(m, "__version__", "unknown")
        f.attrs["timestamp_iso"] = datetime.now(timezone.utc).isoformat()
        f.attrs["integrate_mode"] = kernel or "subpixel2"
        if weighted is not None:
            f.attrs["weighted"] = bool(weighted)
        f.attrs["multi_azimuth"] = True
        f.attrs["n_frames"] = n_frames
        f.attrs["n_r_bins"] = n_r
        f.attrs["n_eta_bins"] = n_eta
        for k, v in (cake_params or {}).items():
            f.attrs[k] = v

        ds = f.create_dataset("frame_ids", data=np.array(list(frame_ids), dtype="S"))
        ds.attrs["long_name"] = "frame identifier"

        ds = f.create_dataset("r_px", data=r_axis)
        ds.attrs["units"] = "pixel"
        ds = f.create_dataset("two_theta_deg", data=axes["two_theta_deg"])
        ds.attrs["units"] = "degree"
        if "d_angstrom" in axes:
            ds = f.create_dataset("d_angstrom", data=axes["d_angstrom"])
            ds.attrs["units"] = "angstrom"
            ds.attrs["note"] = "+inf at r_px=0 (2-theta=0)"
            ds = f.create_dataset("q_invA", data=axes["q_invA"])
            ds.attrs["units"] = "1/angstrom"

        ds = f.create_dataset("eta_deg", data=np.asarray(eta_axis, dtype=np.float64))
        ds.attrs["units"] = "degree"

        ds = f.create_dataset("profiles", data=collapsed_profiles, compression="gzip")
        ds.attrs["units"] = "counts"
        ds.attrs["long_name"] = "engine-collapsed 1-D lineout per frame"
        ds = f.create_dataset("sigmas", data=collapsed_sigmas, compression="gzip")
        ds.attrs["units"] = "counts"
        ds.attrs["long_name"] = "1-sigma uncertainty per bin"

        ds = f.create_dataset("cake", data=cake_store, compression="gzip")
        ds.attrs["units"] = "counts"
        ds.attrs["axes"] = ["frame_index", "eta_deg", "r_px"]
        ds.attrs["weighting"] = cake_weighting
        ds = f.create_dataset("cake_sigma", data=cake_sigma_store, compression="gzip")
        ds.attrs["units"] = "counts"
        ds.attrs["axes"] = ["frame_index", "eta_deg", "r_px"]
        ds.attrs["weighting"] = cake_weighting

        if area is not None:
            ds = f.create_dataset("bin_area", data=area, compression="gzip")
            ds.attrs["long_name"] = "per-bin pixel-count weight"
            ds.attrs["axes"] = ["eta_deg", "r_px"]

    return path
