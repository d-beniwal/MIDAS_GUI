"""Quick-look Cartesian reciprocal-space intensity + coverage volume (V03).

Maps every valid detector pixel of every kept frame into SAMPLE-frame q
(Å⁻¹, 2π/d) -- geometry, wavelength and omega only, no UB -- and accumulates
into a bounded voxel grid:

* ``sum``    -- Σ background-subtracted counts of contributing pixels
* ``count``  -- number of valid pixel samples (the coverage; 0 = UNMEASURED)
* ``max``    -- brightest contributing pixel (what sparse Bragg peaks need;
                a mean over the several hundred samples of a voxel would wash them out)

``mean = sum / count`` is only defined where ``count > 0``; unmeasured voxels
stay NaN in every derived field and are never conflated with measured-zero.
Negative background-subtracted values are kept. Nearest-voxel assignment,
omega = the frame's nominal ingest value (``ref + (idx - ref_idx)*step``, same
convention as spot omega) -- both are declared quick-look choices, recorded in
the product metadata. Masked / sentinel pixels contribute nothing.

Pure numpy/torch; no Qt. Batches are streamed so memory is bounded by the grid
plus one batch of frames.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Callable, Iterable, Optional

import warnings
import numpy as np
import torch

from . import geometry_adapter as ga
from .pipeline import _map_index_points

SENTINEL_U32 = 4294967295


class VolumeCancelled(Exception):
    pass


@dataclass
class GridSpec:
    origin: tuple          # lower q-edge of voxel (0,0,0), Å⁻¹ (sample frame)
    spacing: float         # cubic voxel pitch, Å⁻¹
    shape: tuple           # (nx, ny, nz) CELL counts; index(q) = floor((q-origin)/spacing)

    @property
    def n_voxels(self) -> int:
        return int(np.prod(self.shape))

    def edges(self, axis: int) -> np.ndarray:
        return self.origin[axis] + self.spacing * np.arange(self.shape[axis] + 1)

    def centers(self, axis: int) -> np.ndarray:
        return self.origin[axis] + self.spacing * (np.arange(self.shape[axis]) + 0.5)

    def to_json(self) -> dict:
        return {"origin": list(map(float, self.origin)), "spacing": float(self.spacing),
                "shape": list(map(int, self.shape))}


def estimate_memory_mb(spec: GridSpec) -> float:
    """sum(f32)+count(f32)+max(f32) accumulators."""
    return spec.n_voxels * 12 / 2**20


def pixel_qlab(snap: ga.PanelSnapshot) -> np.ndarray:
    """q-lab of every RAW pixel centre, ``(n_rows, n_cols, 3)`` float64 (via the
    calibration frame, i.e. honouring ``im_trans``)."""
    n_rows, n_cols = snap.raw_shape
    rr, cc = np.meshgrid(np.arange(n_rows, dtype=float), np.arange(n_cols, dtype=float), indexing="ij")
    q = ga.raw_pixels_to_qlab(snap, rr.ravel(), cc.ravel())
    return q.reshape(n_rows, n_cols, 3)


def frame_omega(snap: ga.PanelSnapshot, loaded_idx) -> np.ndarray:
    return snap.omega_of_loaded_frame(loaded_idx)


def auto_grid(snaps: dict, spacing: float, *, q_max: Optional[float] = None,
              step_deg: float = 2.0, pad: float = 0.0) -> GridSpec:
    """Smallest cubic-pitch grid covering every panel's pixels over its omega
    window (a coarse pixel x omega sweep gives the bounding box)."""
    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)
    for pid, s in snaps.items():
        n_rows, n_cols = s.raw_shape
        rr = np.r_[np.arange(0, n_rows, 16), n_rows - 1].astype(float)
        cc = np.r_[np.arange(0, n_cols, 16), n_cols - 1].astype(float)
        R, C = np.meshgrid(rr, cc, indexing="ij")
        ql = ga.raw_pixels_to_qlab(s, R.ravel(), C.ravel())
        w0 = float(s.omega_ref_deg)
        w1 = w0 + (s.omega_last_frame_idx - s.omega_ref_frame_idx) * s.omega_step_deg
        for om in np.arange(min(w0, w1), max(w0, w1) + step_deg, step_deg):
            qs = ga.qlab_to_sample(ql, om)
            lo = np.minimum(lo, qs.min(0))
            hi = np.maximum(hi, qs.max(0))
    if q_max is not None:                       # clip a box to the requested |q| range
        lo = np.maximum(lo, -q_max)
        hi = np.minimum(hi, q_max)
    lo = np.floor((lo - pad) / spacing) * spacing
    hi = np.ceil((hi + pad) / spacing) * spacing
    shape = tuple(int(round(x)) for x in (hi - lo) / spacing)
    return GridSpec(tuple(map(float, lo)), float(spacing), shape)


class VolumeAccumulator:
    def __init__(self, spec: GridSpec):
        self.spec = spec
        n = spec.n_voxels
        self.sum = torch.zeros(n, dtype=torch.float32)
        self.count = torch.zeros(n, dtype=torch.float32)
        self.max = torch.full((n,), -float("inf"), dtype=torch.float32)
        self._origin = torch.tensor(spec.origin, dtype=torch.float64)
        self._shape = torch.tensor(spec.shape, dtype=torch.int64)

    def add(self, q_sample: np.ndarray, signal: np.ndarray, valid: np.ndarray):
        """``q_sample`` (n,3), ``signal`` (n,), ``valid`` bool (n,) -- nearest voxel."""
        q = torch.from_numpy(np.ascontiguousarray(q_sample[valid], dtype=np.float64))
        v = torch.from_numpy(np.ascontiguousarray(signal[valid], dtype=np.float32))
        ix = torch.floor((q - self._origin) / self.spec.spacing).to(torch.int64)
        inside = ((ix >= 0) & (ix < self._shape)).all(dim=1)
        ix, v = ix[inside], v[inside]
        if not len(v):
            return
        flat = (ix[:, 0] * self._shape[1] + ix[:, 1]) * self._shape[2] + ix[:, 2]
        self.sum.index_add_(0, flat, v)
        self.count.index_add_(0, flat, torch.ones_like(v))
        self.max.scatter_reduce_(0, flat, v, reduce="amax", include_self=True)

    def result(self) -> dict:
        shp = tuple(self.spec.shape)
        count = self.count.numpy().reshape(shp)
        mx = self.max.numpy().reshape(shp).copy()
        mx[count == 0] = np.nan
        return {"sum": self.sum.numpy().reshape(shp).copy(), "count": count.copy(), "max": mx}


def quick_background(frames: np.ndarray, in_window: np.ndarray, stride: int = 8) -> np.ndarray:
    """Per-pixel temporal median over a strided subset of kept frames --
    an explicitly APPROXIMATE static background (the ingest's sector model is
    not retained), good enough to suppress the constant detector offset/scatter."""
    sel = np.flatnonzero(in_window)[::stride]
    return np.median(frames[sel].astype(np.float32), axis=0).astype(np.float32)


def build_volume(sources: list, spec: GridSpec, *, progress: Optional[Callable] = None,
                 cancel: Optional[Callable[[], bool]] = None, bg_stride: int = 8) -> dict:
    """Accumulate every source into one grid.

    Each source is ``{"snap": PanelSnapshot, "frames": (n_frames, rows, cols) array
    OR a callable ``(start, stop) -> array``, "mask": bool (rows, cols) | None,
    "n_frames": int}``. Frames are the RAW loaded stack; only
    ``snap.in_window`` frames contribute. Returns the arrays plus a
    ``per_panel`` summary ({pid: {n_frames_used, n_pixels_used}})."""
    acc = VolumeAccumulator(spec)
    per_panel = {}
    total = sum(int(s["snap"].in_window(np.arange(s["n_frames"])).sum()) for s in sources) or 1
    done = 0
    for src in sources:
        snap: ga.PanelSnapshot = src["snap"]
        n_frames = int(src["n_frames"])
        rd = src["frames"]
        read = rd if callable(rd) else (lambda a, b, _x=rd: _x[a:b])
        qlab = pixel_qlab(snap).reshape(-1, 3)
        in_win = snap.in_window(np.arange(n_frames))
        mask = src.get("mask")
        good_px = (~np.asarray(mask, bool)).ravel() if mask is not None else np.ones(qlab.shape[0], bool)
        bg = None
        if bg_stride:
            sub = np.flatnonzero(in_win)[::bg_stride]
            bg = np.median(np.stack([np.asarray(read(i, i + 1))[0].astype(np.float32) for i in sub]),
                           axis=0).ravel() if len(sub) else None
        n_used = 0
        for start in range(0, n_frames, 32):
            stop = min(start + 32, n_frames)
            batch = np.asarray(read(start, stop))
            for k, idx in enumerate(range(start, stop)):
                if not in_win[idx]:
                    continue
                if cancel is not None and cancel():
                    raise VolumeCancelled()
                raw = batch[k].reshape(-1)
                valid = good_px & (raw != SENTINEL_U32 if raw.dtype == np.uint32 else good_px)
                sig = raw.astype(np.float32)
                if bg is not None:
                    sig = sig - bg
                q = ga.qlab_to_sample(qlab, float(frame_omega(snap, idx)))
                acc.add(q, sig, valid)
                n_used += 1
                done += 1
                if progress is not None and done % 10 == 0:
                    progress(done / total)
        per_panel[int(snap.panel_id)] = {"n_frames_used": n_used, "n_pixels_per_frame": int(good_px.sum())}
    out = acc.result()
    out["per_panel"] = per_panel
    return out


def mean_field(vol: dict) -> np.ndarray:
    with np.errstate(invalid="ignore", divide="ignore"):
        m = vol["sum"] / vol["count"]
    m[vol["count"] == 0] = np.nan
    return m


def crop_to_coverage(vol: dict, spec: GridSpec, pad: int = 1):
    """Crop every field to the coverage bounding box -> ``(vol, spec)``."""
    cov = vol["count"] > 0
    if not cov.any():
        return vol, spec
    sl = []
    new_origin = []
    for ax in range(3):
        other = tuple(a for a in range(3) if a != ax)
        idx = np.flatnonzero(cov.any(axis=other))
        lo, hi = max(idx[0] - pad, 0), min(idx[-1] + pad + 1, spec.shape[ax])
        sl.append(slice(lo, hi))
        new_origin.append(spec.origin[ax] + lo * spec.spacing)
    sl = tuple(sl)
    out = {k: (v[sl] if isinstance(v, np.ndarray) and v.ndim == 3 else v) for k, v in vol.items()}
    shape = tuple(s.stop - s.start for s in sl)
    return out, GridSpec(tuple(new_origin), spec.spacing, shape)


# ── 2-D reductions (V04) ─────────────────────────────────────────────────────
def axis_slab(field: np.ndarray, count: np.ndarray, axis: int, index: int, half_thickness: int = 0,
              reduce: str = "max") -> tuple:
    """Slab ``[index-h, index+h]`` of ``field`` along ``axis`` reduced to 2-D.
    Unmeasured voxels (count 0) never contribute; an all-unmeasured column stays
    NaN. Returns ``(image, coverage_image)`` with the remaining two axes in
    ascending order."""
    lo = max(index - half_thickness, 0)
    hi = min(index + half_thickness + 1, field.shape[axis])
    f = np.take(field, range(lo, hi), axis=axis).astype(np.float64)
    c = np.take(count, range(lo, hi), axis=axis)
    f = np.where(c > 0, f, np.nan)
    with np.errstate(all="ignore"), warnings.catch_warnings():
        # nanmax/nanmean raise RuntimeWarning (not an FP error) on all-NaN columns
        warnings.simplefilter("ignore", RuntimeWarning)
        if reduce == "max":
            img = np.nanmax(f, axis=axis)
        elif reduce == "sum":
            img = np.nansum(f, axis=axis)
            img[~(c > 0).any(axis=axis)] = np.nan
        else:
            img = np.nanmean(f, axis=axis)
    return img, c.sum(axis=axis)


def asinh_scale(x: np.ndarray, cofactor: float = 10.0) -> np.ndarray:
    """Signed display transform (NaN preserved); never applied to stored data."""
    return np.arcsinh(x / cofactor)
