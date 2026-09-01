"""Zarr cake output for Batch Integrate.

Writes a stack of per-frame (eta, R) cakes to a single-file zarr v2 store
(``.zarr.zip``), in a schema-compatible *subset* of mpe_wf_saxs_waxs's own
caking-pipeline zarr layout (``REtaMap`` + ``IntegrationResult/FrameNr_<i>``
+ ``InstrumentParameters`` + ``Omegas``) — see the convergence roadmap in
QUESTIONS_FOR_COLLEAGUES.md. This is deliberately *not* a full port of that
schema (no per-panel merge fields, no ``OmegaSumFrame`` accumulation group —
those only matter for multi-panel/omega-summed FF-HEDM-style acquisitions,
which Batch Integrate doesn't do). The subset here is exactly what
mpe_wf_saxs_waxs's own generic viewer (``gui_view_zarr.py``) actually reads:
it renders any array generically, and only specially recognizes ``REtaMap``
(for physical-unit axis conversion) and ``InstrumentParameters/Lam`` (for
d-spacing) — so a file written here opens there with working R/2theta/Q
axes, without needing the parts that viewer treats as optional.

No existing writer to build on: midas_integrate_v2's only zarr writer
(``io/zarr_gsas.write_gsas_zarr_zip``) is locked to the GSAS-II reader's own
fixed schema and has no generic attrs slot for provenance, so this is
written from scratch rather than adapted from it.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from midas_gui.workers import axis_conversions
from midas_gui import provenance


def write_cake_zarr(path,
                    cakes: Sequence[np.ndarray],
                    *,
                    r_axis_px: np.ndarray,
                    eta_axis_deg: np.ndarray,
                    lsd_um: float,
                    px_um: float,
                    wavelength_A: float,
                    omegas: Optional[Sequence[float]] = None,
                    bin_area: Optional[np.ndarray] = None,
                    provenance_entry: Optional[dict] = None) -> None:
    """Write ``cakes`` (each an ``(n_eta, n_r)`` array, one per frame) to a
    ``.zarr.zip`` at ``path``.

    ``bin_area`` is an optional ``(n_eta, n_r)`` pixel-count-per-bin array
    (e.g. from ``workers.count_cake``) for REtaMap's BinArea channel; filled
    with NaN when not given. ``omegas`` defaults to the frame index — Batch
    Integrate doesn't model rotation, so this is a placeholder that keeps
    the schema's per-frame ``omega`` attr meaningful as "which frame".
    ``provenance_entry`` (from ``provenance.build_entry()``) is stamped onto
    the store root, in the same ``provenance_history`` list convention used
    for MIDAS_GUI's HDF5 outputs (see ``provenance.append_to_hdf5_attrs``)
    and mpe_wf_saxs_waxs's own zarr outputs.
    """
    import zarr
    import numcodecs

    r_axis_px = np.asarray(r_axis_px, dtype=float)
    eta_axis_deg = np.asarray(eta_axis_deg, dtype=float)
    n_r, n_eta = r_axis_px.size, eta_axis_deg.size
    cakes = list(cakes)
    if not cakes:
        raise ValueError("write_cake_zarr: no frames to write")

    two_theta_deg, _, q_invA = axis_conversions(r_axis_px, lsd_um, px_um, wavelength_A)

    # REtaMap: (5, nR, nEta), channel order Radius/2Theta/Eta/BinArea/Q —
    # radius/2theta/Q vary along R (constant across eta), eta vice versa.
    radius_ch = np.broadcast_to(r_axis_px[:, None], (n_r, n_eta))
    twotheta_ch = np.broadcast_to(two_theta_deg[:, None], (n_r, n_eta))
    eta_ch = np.broadcast_to(eta_axis_deg[None, :], (n_r, n_eta))
    q_ch = np.broadcast_to(q_invA[:, None], (n_r, n_eta))
    if bin_area is not None:
        binarea_ch = np.asarray(bin_area, dtype=float).T   # (n_eta,n_r) -> (n_r,n_eta)
    else:
        binarea_ch = np.full((n_r, n_eta), np.nan)
    reta_map = np.ascontiguousarray(
        np.stack([radius_ch, twotheta_ch, eta_ch, binarea_ch, q_ch], axis=0))

    omegas_arr = (np.asarray(omegas, dtype=float) if omegas is not None
                 else np.arange(len(cakes), dtype=float))

    compressor = numcodecs.Blosc(cname='zstd', clevel=3,
                                 shuffle=numcodecs.Blosc.BITSHUFFLE)

    # Stage in a mutable DirectoryStore, then zip once at the end. A live
    # ZipStore can't have an array's .zattrs written and then updated (e.g.
    # array() followed by setting .attrs) without leaving duplicate zip
    # entries that confuse readers — the same reason provenance.append_to_zip
    # extracts/edits/re-zips rather than mutating a ZipStore in place.
    with tempfile.TemporaryDirectory(prefix='midas_gui_zarr_cake_') as tmp:
        staging = Path(tmp) / 'store'
        store = zarr.DirectoryStore(str(staging))
        root = zarr.group(store=store, overwrite=True)

        reta = root.array('REtaMap', reta_map, chunks=reta_map.shape,
                          compressor=compressor)
        reta.attrs['Header'] = 'Radius,2Theta,Eta,BinArea,Q'
        reta.attrs['Units'] = 'px,deg,deg,px^2,invA'
        reta.attrs['nRBins'] = int(n_r)
        reta.attrs['nEtaBins'] = int(n_eta)

        ir_grp = root.create_group('IntegrationResult')
        for i, cake in enumerate(cakes):
            cake_arr = np.ascontiguousarray(np.asarray(cake, dtype=float).T)  # (n_eta,n_r)->(n_r,n_eta)
            ds = ir_grp.array(f'FrameNr_{i}', cake_arr, chunks=cake_arr.shape,
                              compressor=compressor)
            ds.attrs['Header'] = 'Radius,Eta'
            ds.attrs['Units'] = 'px,deg'
            ds.attrs['omega'] = float(omegas_arr[i])

        ip_grp = root.create_group('InstrumentParameters')
        ip_grp.array('Lam', np.array([float(wavelength_A)]))
        ip_grp.array('Distance', np.array([float(lsd_um) * 1e-3]))  # um -> mm

        root.array('Omegas', omegas_arr)

        if provenance_entry is not None:
            provenance.append_to_zarr_group(root, provenance_entry)

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        _zip_directory(staging, path)


def _zip_directory(src_dir, dest_path) -> None:
    """Deterministically zip ``src_dir``'s contents (flat, no top-level
    folder) into ``dest_path`` — matches how mpe_wf_saxs_waxs's own
    combine_hydra_zarr.py packs a staged DirectoryStore into a .zarr.zip."""
    import os
    import zipfile
    src_dir = Path(src_dir)
    with zipfile.ZipFile(str(dest_path), 'w', compression=zipfile.ZIP_DEFLATED,
                         allowZip64=True) as zf:
        for root_dir, _dirs, files in os.walk(src_dir):
            for fname in sorted(files):
                abs_path = Path(root_dir) / fname
                arcname = abs_path.relative_to(src_dir).as_posix()
                zf.write(abs_path, arcname)
