"""Dark subtraction and pixel-mask construction.

Ported from pyAutoBeam's ``processing/dark.py`` and ``processing/mask.py``.
Mask convention throughout: 0 = good pixel, 1 = bad pixel. Every function
here is pure numpy/scipy with no file I/O — the GUI already hands over
in-memory arrays.
"""

import numpy as np
from scipy.ndimage import convolve, uniform_filter


def subtract_dark(data, dark):
    """Subtract dark current from detector frames.

    Parameters
    ----------
    data : numpy.ndarray
        Detector data, 2D (Y, X) or 3D (N, Y, X).
    dark : numpy.ndarray
        Dark frame(s), 2D (Y, X) or 3D (M, Y, X); if 3D, the mean is taken along
        axis 0 first.
    """
    if dark.ndim == 3:
        dark_mean = np.mean(dark, axis=0, dtype=np.float32)
    else:
        dark_mean = dark.astype(np.float32)
    return data.astype(np.float32) - dark_mean


def apply_mask(data, mask):
    """Zero out bad pixels (mask == 1); good pixels (mask == 0) pass through."""
    spatial = data.shape[-2:]
    if mask.shape != spatial:
        raise ValueError(
            f"Mask shape {mask.shape} does not match data spatial "
            f"dimensions {spatial}"
        )
    return data.astype(np.float32) * (1.0 - mask.astype(np.float32))


def create_percentile_mask(data, percentile=99.99):
    """Mask pixels whose (mean-frame) intensity exceeds *percentile*."""
    if data.ndim == 3:
        frame = np.mean(data, axis=0, dtype=np.float32)
    else:
        frame = data.astype(np.float32)
    threshold = np.percentile(frame, percentile)
    return np.where(frame > threshold, 1.0, 0.0).astype(np.float32)


def create_dark_mask(dark_frames, n_sigma=5, local_window=101):
    """Dead- and hot-pixel mask from a raw multi-frame dark stack.

    Parameters
    ----------
    dark_frames : numpy.ndarray
        3D (M, Y, X) raw dark stack, M >= 2.
    n_sigma : float
        Local standard deviations above the local mean to flag a pixel hot.
    local_window : int
        Side length (forced odd) of the local mean window.

    Returns
    -------
    mask : numpy.ndarray
        2D float32 (Y, X), 0 = good, 1 = bad.
    info : dict
        ``n_dead``, ``n_hot``, ``n_total_bad``, ``local_window``.
    """
    frames = np.asarray(dark_frames, dtype=np.float32)
    if frames.ndim != 3 or frames.shape[0] < 2:
        raise ValueError(
            f"Dark stack must have >= 2 frames. Got shape {frames.shape}."
        )

    if local_window % 2 == 0:
        local_window += 1

    mean_image = np.mean(frames, axis=0)
    std_image = np.std(frames, axis=0)

    dead_std_cutoff = 0.5
    dead_mask = std_image < dead_std_cutoff

    local_mean = uniform_filter(
        mean_image.astype(np.float64), size=local_window, mode="reflect"
    )
    local_sq_mean = uniform_filter(
        mean_image.astype(np.float64) ** 2, size=local_window, mode="reflect"
    )
    local_var = np.maximum(local_sq_mean - local_mean ** 2, 0.0)
    local_std = np.sqrt(local_var)

    local_threshold = local_mean + n_sigma * local_std
    hot_mask = mean_image > local_threshold

    bad_mask = dead_mask | hot_mask
    mask = np.where(bad_mask, 1.0, 0.0).astype(np.float32)

    info = {
        "n_dead": int(np.sum(dead_mask)),
        "n_hot": int(np.sum(hot_mask)),
        "n_total_bad": int(np.sum(bad_mask)),
        "local_window": local_window,
    }
    return mask, info


def create_frozen_pixel_mask(data, std_cutoff=0.5):
    """Frozen (stuck, non-responsive) pixels: nonzero mean, near-zero std.

    Looks at the sample data itself (after dark subtraction / earlier
    masking), not the dark stack — pixels already zeroed by an earlier
    mask have mean 0 and are naturally skipped.
    """
    if data.ndim != 3 or data.shape[0] < 2:
        raise ValueError(
            f"create_frozen_pixel_mask requires a stack of >= 2 frames. "
            f"Got shape {data.shape}."
        )
    mean_image = np.mean(data, axis=0)
    std_image = np.std(data, axis=0)
    frozen = (mean_image > 0) & (std_image < std_cutoff)
    return frozen.astype(np.float32)


def create_isolated_hot_pixel_mask(data, noise_floor=30.0, min_hot_intensity=2000.0):
    """Spatially-isolated single-pixel hot spikes.

    A pixel is flagged only when it exceeds *min_hot_intensity* AND all 8
    of its immediate neighbors are below *noise_floor* — real signal
    spreads to neighbors, a lone spike does not.
    """
    frame = (
        np.mean(data, axis=0, dtype=np.float32)
        if data.ndim == 3
        else data.astype(np.float32)
    )
    above = (frame > noise_floor).astype(np.float32)
    kernel = np.array([[1, 1, 1], [1, 0, 1], [1, 1, 1]], dtype=np.float32)
    neighbor_count = convolve(above, kernel, mode="constant", cval=0.0)
    isolated = (frame > min_hot_intensity) & (neighbor_count < 0.5)
    return isolated.astype(np.float32)
