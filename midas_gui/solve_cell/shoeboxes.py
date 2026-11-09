"""Per-spot ROI "shoeboxes" for the spot inspector (V10).

For every observed spot we cut a ``(n_frames, n_rows, n_cols)`` cube of the RAW
detector frames centred on its centroid (original pixel coordinates -- no
binning), once as raw counts and once with a static per-pixel median background
removed (an explicit quick-look approximation; ingest's sector background is
not retained). Cells outside the detector, outside the loaded stack, or equal
to the detector sentinel are NaN -- gaps stay gaps. Frames outside the ingest
omega window are kept (so the user can see what was excluded) and flagged in
``frame_in_window``.

Pure numpy/h5py; no Qt.
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np
import pandas as pd

from . import geometry_adapter as ga

SENTINEL_U32 = 4294967295


def panel_background(read: Callable, n_frames: int, snap: ga.PanelSnapshot, stride: int = 8) -> np.ndarray:
    """Per-pixel temporal median over a strided subset of in-window frames."""
    sel = np.flatnonzero(snap.in_window(np.arange(n_frames)))[::stride]
    if not len(sel):
        return np.zeros(snap.raw_shape, np.float32)
    stack = np.stack([np.asarray(read(i, i + 1))[0].astype(np.float32) for i in sel])
    return np.median(stack, axis=0).astype(np.float32)


def extract_shoebox(read: Callable, n_frames: int, shape: tuple, bg: Optional[np.ndarray],
                    row: float, col: float, loaded_frame: int, half_frames: int, half_px: int):
    """``(raw, bgsub, frame0, row0, col0)`` -- cubes are ``(2hf+1, 2hp+1, 2hp+1)`` float32
    with NaN where the cell is not a measured pixel."""
    r0, c0 = int(round(row)) - half_px, int(round(col)) - half_px
    f0 = int(loaded_frame) - half_frames
    nF, nP = 2 * half_frames + 1, 2 * half_px + 1
    raw = np.full((nF, nP, nP), np.nan, np.float32)
    out = np.full_like(raw, np.nan)
    ra, rb = max(r0, 0), min(r0 + nP, shape[0])
    ca, cb = max(c0, 0), min(c0 + nP, shape[1])
    fa, fb = max(f0, 0), min(f0 + nF, n_frames)
    if ra < rb and ca < cb and fa < fb:
        blk = np.asarray(read(fa, fb))[:, ra:rb, ca:cb]
        f = blk.astype(np.float32)
        if blk.dtype == np.uint32:
            f[blk == SENTINEL_U32] = np.nan
        sl = (slice(fa - f0, fb - f0), slice(ra - r0, rb - r0), slice(ca - c0, cb - c0))
        raw[sl] = f
        out[sl] = f - (bg[ra:rb, ca:cb] if bg is not None else 0.0)
    return raw, out, f0, r0, c0


def build_spot_shoeboxes(spots: pd.DataFrame, panels: dict, *, half_frames: int = 12,
                         half_px: int = 20, bg_stride: int = 8, progress=None) -> dict:
    """``panels`` = ``{panel_id: {"read": callable, "n_frames": int, "snap": PanelSnapshot}}``.

    Returns ``{"tables": {"index": DataFrame}, "arrays": {"raw", "bgsub",
    "frame_in_window"}}`` where ``index`` row i describes cube i."""
    bgs = {pid: panel_background(p["read"], p["n_frames"], p["snap"], bg_stride) for pid, p in panels.items()}
    n = len(spots)
    nF, nP = 2 * half_frames + 1, 2 * half_px + 1
    raw = np.full((n, nF, nP, nP), np.nan, np.float32)
    bgsub = np.full_like(raw, np.nan)
    in_win = np.zeros((n, nF), bool)
    idx_rows = []
    for k, s in enumerate(spots.itertuples(index=False)):
        pid = int(s.panel_id)
        p = panels[pid]
        r, b, f0, r0, c0 = extract_shoebox(p["read"], p["n_frames"], p["snap"].raw_shape, bgs[pid],
                                           s.row, s.col, int(s.loaded_frame_idx), half_frames, half_px)
        raw[k], bgsub[k] = r, b
        in_win[k] = p["snap"].in_window(np.arange(f0, f0 + nF))
        idx_rows.append({"spot_id": int(s.spot_id), "panel_id": pid, "frame0": f0, "row0": r0, "col0": c0})
        if progress:
            progress((k + 1) / n)
    return {"tables": {"index": pd.DataFrame(idx_rows)},
            "arrays": {"raw": raw, "bgsub": bgsub, "frame_in_window": in_win},
            "meta": {"half_frames": half_frames, "half_px": half_px, "bg_stride": bg_stride,
                     "approximations": "background = per-pixel median of every %dth in-window frame; "
                                       "NaN = off-detector / outside stack / sentinel" % bg_stride}}
