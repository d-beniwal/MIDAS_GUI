"""Detector-saturation pre-check.

Not present in pyAutoBeam (confirmed absent there) — this gate is specific
to the GUI's "capture, then check before running the analysis" workflow.
It operates on a stack that has already had dark subtraction and the
selected masks applied, so a pixel already excluded by a bad-pixel mask
never counts as "saturated".
"""

from dataclasses import dataclass, field

import numpy as np


@dataclass
class SaturationResult:
    ok: bool
    per_frame_bad_counts: dict  # {frame_index (in original stack): count}
    bad_frame_indices: list = field(default_factory=list)


def check_saturation(frames, mask, saturation_intensity, tolerate_n, skip_frames=0):
    """Count over-saturated pixels per frame, skipping the first *skip_frames*.

    Parameters
    ----------
    frames : numpy.ndarray
        3D (N, Y, X) stack, already dark-subtracted and masked as desired.
    mask : numpy.ndarray or None
        2D (Y, X) mask (0 = good, 1 = bad); masked pixels are excluded from
        the saturation count. ``None`` means no pixel is excluded.
    saturation_intensity : float
        Pixel values strictly above this are considered saturated.
    tolerate_n : int
        A frame is flagged only when its saturated-pixel count exceeds this.
    skip_frames : int
        Number of frames to skip from the start of the stack (not checked).

    Returns
    -------
    SaturationResult
        ``ok`` is False if any checked frame's saturated-pixel count
        exceeds *tolerate_n*.
    """
    data = np.asarray(frames)
    n_skip = max(0, int(skip_frames))

    good = None
    if mask is not None:
        good = np.asarray(mask) <= 0.5

    per_frame_bad_counts = {}
    bad_frame_indices = []
    for i in range(n_skip, data.shape[0]):
        frame = data[i]
        over = frame > saturation_intensity
        if good is not None:
            over = over & good
        count = int(np.sum(over))
        per_frame_bad_counts[i] = count
        if count > tolerate_n:
            bad_frame_indices.append(i)

    return SaturationResult(
        ok=(len(bad_frame_indices) == 0),
        per_frame_bad_counts=per_frame_bad_counts,
        bad_frame_indices=bad_frame_indices,
    )
