"""2-D detector background estimation for the Pump Probe tab.

Qt-free numerics.  The ideas come from the MIDAS code base:

* **Frame projection** (mean / median / max / sum / min over a stack).  The median
  is the temporal per-pixel median ``MIDAS-NF-preProc`` uses to remove static
  background; a pre-pump (negative-delay) selection plays the role of the
  empty-cell / dark-baseline frame of ``midas_integrate_v2.corrections.background``.
* **Single image** — any one pooled frame or file as the reference.
* **SNIP** (Morhac et al., NIM A 401 (1997) 113) — the clipping baseline
  ``utils/extract_lineouts.py`` applies to 1-D lineouts, here in its 2-D form
  (LLS transform, neighbours at distance ±m along rows and columns).  It follows
  the smooth diffuse/amorphous background and clips Bragg rings and spots.
* **Median filter** — the iterated 2-D median MIDAS uses to estimate the smooth
  background under diffraction rings (``midas_calibrate_v2.seed.from_image.
  _fast_median_filter`` / ``AutoCalibrateZarr._safe_median_filter``: kernel 51,
  3 iterations; on images >= 1024 px it downsamples 4x, filters with a
  proportionally smaller kernel and upsamples bilinearly — ~100x faster, same
  result for a smooth background).
* **Gaussian smoothing** — a plain low-pass of the reference, for a noisy empty frame.
* **scale / offset** — ``B_eff = scale * B + offset``, the ``EmptySubtraction``
  model (``I - alpha * I_empty - offset``).

The result is a 2-D field handed to ``apply_field_corrections(background=...)``,
which subtracts it from every raw frame (and clips at zero).
"""
from __future__ import annotations

from typing import Callable, Optional, Sequence

import numpy as np

#: projection key -> label.  mean/sum/max are the Data Viewer's projections;
#: median and min are added because they are the robust background choices.
PROJECTIONS = {
    "mean": "Average",
    "median": "Median",
    "min": "Minimum",
    "max": "Maximum",
    "sum": "Sum",
}

#: refinement key -> label
REFINEMENTS = {
    "none": "None (use as-is)",
    "snip": "SNIP baseline (2-D)",
    "median": "Median filter (MIDAS)",
    "gauss": "Gaussian smooth",
}

#: median needs the whole stack in memory, so it is run on an even subsample.
MEDIAN_MAX_FRAMES = 32


class Cancelled(Exception):
    """Raised by :func:`project_frames` when ``should_stop`` returns True."""


def _subsample(n: int, k: int) -> list:
    """``k`` evenly spaced indices out of ``range(n)`` (all of them if n <= k)."""
    if n <= k:
        return list(range(n))
    return sorted({int(round(i)) for i in np.linspace(0, n - 1, k)})


def project_frames(paths: Sequence[str], op: str = "mean", *,
                   loader: Optional[Callable[[str], np.ndarray]] = None,
                   progress: Optional[Callable[[int, int], None]] = None,
                   should_stop: Optional[Callable[[], bool]] = None) -> np.ndarray:
    """Reduce the frames at ``paths`` to one 2-D float64 image with ``op``.

    ``mean`` / ``sum`` / ``max`` / ``min`` stream one frame at a time.  ``median``
    reads at most :data:`MEDIAN_MAX_FRAMES` evenly spaced frames.
    """
    if op not in PROJECTIONS:
        raise ValueError(f"unknown projection {op!r}")
    if not paths:
        raise ValueError("no frames to project")
    if loader is None:
        from midas_gui.helpers import _load_image as loader
    paths = list(paths)
    if op == "median":
        paths = [paths[i] for i in _subsample(len(paths), MEDIAN_MAX_FRAMES)]
    n = len(paths)

    acc = None
    stack = [] if op == "median" else None
    for i, p in enumerate(paths):
        if should_stop is not None and should_stop():
            raise Cancelled()
        img = np.asarray(loader(p), dtype=np.float32 if op == "median" else np.float64)
        if img.ndim != 2:
            raise ValueError(f"{p}: expected a 2-D frame, got shape {img.shape}")
        if stack is not None:
            if stack and img.shape != stack[0].shape:
                raise ValueError(f"{p}: shape {img.shape} != {stack[0].shape}")
            stack.append(img)
        elif acc is None:
            acc = img.copy()
        else:
            if img.shape != acc.shape:
                raise ValueError(f"{p}: shape {img.shape} != {acc.shape}")
            if op in ("mean", "sum"):
                acc += img
            elif op == "max":
                np.maximum(acc, img, out=acc)
            else:
                np.minimum(acc, img, out=acc)
        if progress is not None:
            progress(i + 1, n)
    if stack is not None:
        return np.median(np.stack(stack), axis=0).astype(np.float64)
    if op == "mean":
        acc /= n
    return acc


def snip_2d(image: np.ndarray, n_iter: int = 30, mask: Optional[np.ndarray] = None
            ) -> np.ndarray:
    """2-D SNIP baseline of ``image``.

    Same LLS transform and decreasing-window clipping as the 1-D
    ``snip_background`` in MIDAS ``utils/extract_lineouts.py``; each pass clips a
    pixel to the mean of its four neighbours at distance ``m`` along each axis
    (window ``m = n_iter .. 1``).  ``n_iter`` should exceed the half-width, in
    pixels, of the widest feature to be treated as signal.  Pixels flagged in
    ``mask`` (True / non-zero = bad) are replaced by the median of the good
    pixels first, so gaps and dead pixels do not drag the baseline to zero.
    """
    y = np.asarray(image, dtype=np.float64).copy()
    if y.ndim != 2:
        raise ValueError("snip_2d needs a 2-D image")
    if mask is not None:
        bad = np.asarray(mask).astype(bool)
        if bad.shape == y.shape and bad.any() and not bad.all():
            y[bad] = np.median(y[~bad])
    y = np.clip(y, 0, None)
    y = np.log(np.log(np.sqrt(y + 1) + 1) + 1)
    h, w = y.shape
    for m in range(int(n_iter), 0, -1):
        p = np.pad(y, m, mode="edge")
        avg = 0.25 * (p[m:m + h, :w] + p[m:m + h, 2 * m:2 * m + w]
                      + p[:h, m:m + w] + p[2 * m:2 * m + h, m:m + w])
        np.minimum(y, avg, out=y)
    bg = (np.exp(np.exp(y) - 1) - 1) ** 2 - 1
    return np.maximum(bg, 0.0)


def median_2d(image: np.ndarray, kernel: int = 51, n_iters: int = 3,
              mask: Optional[np.ndarray] = None) -> np.ndarray:
    """Iterated 2-D median-filter background, as in MIDAS ``_fast_median_filter``.

    ``kernel`` is the window in detector pixels (made odd); ``n_iters`` successive
    passes.  Images with ``min(shape) >= 1024`` are downsampled 4x first (kernel
    scaled to ``kernel/4``, min 3) and bilinearly upsampled after.  ``mask``
    (True / non-zero = bad) pixels are replaced by the median of the good pixels
    first, so gaps / dead pixels do not bias the window medians.
    """
    from scipy import ndimage
    y = np.asarray(image, dtype=np.float64)
    if y.ndim != 2:
        raise ValueError("median_2d needs a 2-D image")
    if mask is not None:
        bad = np.asarray(mask).astype(bool)
        if bad.shape == y.shape and bad.any() and not bad.all():
            y = y.copy(); y[bad] = np.median(y[~bad])
    kernel = max(3, int(kernel)) | 1
    down = 4 if min(y.shape) >= 1024 else 1
    if down == 1:
        out = y
        for _ in range(int(n_iters)):
            out = ndimage.median_filter(out, size=kernel, mode="reflect")
        return np.asarray(out, dtype=np.float64)
    k_down = max(3, int(round(kernel / down))) | 1
    small = ndimage.zoom(y, 1.0 / down, order=1)
    for _ in range(int(n_iters)):
        small = ndimage.median_filter(small, size=k_down, mode="reflect")
    out = ndimage.zoom(small, (y.shape[0] / small.shape[0],
                               y.shape[1] / small.shape[1]), order=1)
    if out.shape != y.shape:           # zoom rounds; crop or edge-pad to fit
        out = out[:y.shape[0], :y.shape[1]]
        out = np.pad(out, ((0, y.shape[0] - out.shape[0]),
                           (0, y.shape[1] - out.shape[1])), mode="edge")
    return out.astype(np.float64)


def refine(image: np.ndarray, method: str = "none", *, snip_iter: int = 30,
           sigma: float = 5.0, mask: Optional[np.ndarray] = None,
           median_kernel: int = 51, median_iters: int = 3) -> np.ndarray:
    """Apply a :data:`REFINEMENTS` method to a projected / single reference image."""
    if method == "none":
        return np.asarray(image, dtype=np.float64)
    if method == "snip":
        return snip_2d(image, snip_iter, mask)
    if method == "median":
        return median_2d(image, median_kernel, median_iters, mask)
    if method == "gauss":
        from scipy.ndimage import gaussian_filter
        return gaussian_filter(np.asarray(image, dtype=np.float64), float(sigma))
    raise ValueError(f"unknown refinement {method!r}")


def effective_background(image: np.ndarray, scale: float = 1.0,
                         offset: float = 0.0) -> np.ndarray:
    """``scale * image + offset`` (the ``EmptySubtraction`` model)."""
    return float(scale) * np.asarray(image, dtype=np.float64) + float(offset)
