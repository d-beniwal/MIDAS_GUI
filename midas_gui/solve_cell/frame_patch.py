"""Detector frame painted onto its measured Ewald patch (V02). Pure numpy.

A panel's frame is a set of pixel *cells*: pixel edges sit at ``i - 0.5`` in the
RAW pixel-index frame (centres at integers, same convention as spot centroids).
The cell-corner grid is mapped to q-lab ONCE per panel (via the calibration
frame, honouring ``im_trans``) and rotated into sample q at the frame's omega, so
scrubbing omega is only a rotation of ``(n+1, m+1, 3)`` points. Each cell carries
the block-MAX of the background-subtracted counts of the ``stride x stride``
pixels it covers (max keeps sparse Bragg peaks from aliasing away), with
``NaN`` for cells whose pixels are all masked/sentinel -- those become MISSING
cells in the render, never zero-intensity ones.

Intensity here is the quick-look background-subtracted signal (static median
background; see ``shoeboxes.panel_background``), not calibrated counts.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from . import geometry_adapter as ga

SENTINEL_U32 = 4294967295


def edge_indices(n: int, stride: int) -> np.ndarray:
    """Block start indices ``0, s, 2s, ...`` plus the terminal ``n`` (last block may be short)."""
    return np.r_[np.arange(0, n, stride), n]


def corner_qlab(snap: ga.PanelSnapshot, stride: int) -> np.ndarray:
    """q-lab of the ``(nr+1, nc+1)`` cell corners (pixel edges ``idx - 0.5``)."""
    n_rows, n_cols = snap.raw_shape
    r = edge_indices(n_rows, stride) - 0.5
    c = edge_indices(n_cols, stride) - 0.5
    R, C = np.meshgrid(r, c, indexing="ij")
    return ga.raw_pixels_to_qlab(snap, R.ravel(), C.ravel()).reshape(len(r), len(c), 3)


def corners_at_omega(qlab_corners: np.ndarray, omega_deg: float) -> np.ndarray:
    shp = qlab_corners.shape
    return ga.qlab_to_sample(qlab_corners.reshape(-1, 3), omega_deg).reshape(shp)


def block_reduce_max(img: np.ndarray, stride: int) -> np.ndarray:
    """Block max of ``img`` (NaN-aware; all-NaN block -> NaN) onto the cell grid."""
    nr, nc = img.shape
    ri, ci = edge_indices(nr, stride)[:-1], edge_indices(nc, stride)[:-1]
    filled = np.where(np.isnan(img), -np.inf, img)
    out = np.maximum.reduceat(np.maximum.reduceat(filled, ri, axis=0), ci, axis=1)
    out[~np.isfinite(out)] = np.nan
    return out.astype(np.float32)


def cell_signal(frame: np.ndarray, bg: Optional[np.ndarray], mask: Optional[np.ndarray], stride: int) -> np.ndarray:
    """Per-cell block-max of ``frame - bg`` with masked / sentinel pixels excluded."""
    f = frame.astype(np.float32)
    bad = np.zeros(f.shape, bool)
    if frame.dtype == np.uint32:
        bad |= frame == SENTINEL_U32
    if mask is not None:
        bad |= np.asarray(mask, bool)
    if bg is not None:
        f = f - bg
    f[bad] = np.nan
    return block_reduce_max(f, stride)


def cell_pixel_ranges(snap: ga.PanelSnapshot, stride: int, i: int, j: int) -> tuple:
    """Raw-pixel ``(row_lo, row_hi, col_lo, col_hi)`` (inclusive-exclusive) of cell ``(i, j)``."""
    ri, ci = edge_indices(snap.raw_shape[0], stride), edge_indices(snap.raw_shape[1], stride)
    return int(ri[i]), int(ri[i + 1]), int(ci[j]), int(ci[j + 1])


def pixel_to_cell(snap: ga.PanelSnapshot, stride: int, row: float, col: float) -> tuple:
    ri, ci = edge_indices(snap.raw_shape[0], stride), edge_indices(snap.raw_shape[1], stride)
    i = int(np.clip(np.searchsorted(ri, round(row) + 0.5, side="right") - 1, 0, len(ri) - 2))
    j = int(np.clip(np.searchsorted(ci, round(col) + 0.5, side="right") - 1, 0, len(ci) - 2))
    return i, j


def loaded_frame_for_omega(snap: ga.PanelSnapshot, omega_deg: float) -> Optional[int]:
    """Nearest loaded frame to ``omega_deg`` inside the ingest window, else ``None``."""
    k = int(round((omega_deg - snap.omega_ref_deg) / snap.omega_step_deg)) + snap.omega_ref_frame_idx \
        if snap.omega_step_deg else snap.omega_ref_frame_idx
    return k if bool(snap.in_window(k)) else None


def build_panel_cells(read, n_frames: int, snap: ga.PanelSnapshot, mask, stride: int, bg: np.ndarray):
    """Cell signals for every in-window frame: ``(loaded_idx (n,), cells (n, nr, nc))``."""
    idx = np.flatnonzero(snap.in_window(np.arange(n_frames)))
    out = None
    for k, f in enumerate(idx):
        c = cell_signal(np.asarray(read(int(f), int(f) + 1))[0], bg, mask, stride)
        if out is None:
            out = np.empty((len(idx),) + c.shape, np.float32)
        out[k] = c
    return idx, out
