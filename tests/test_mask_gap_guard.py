"""Spatial-outlier mask next to sensor gaps: gap-aware medians + stricter edge test.

Synthetic Pilatus-like frame: a 6-px-wide gap (-1), module-edge pixels that respond 1.4x
their neighbours (physical), plus real defects (interior and ON the edge)."""
import numpy as np
import pytest

pytest.importorskip("PyQt5")
from midas_gui.workers import spatial_outlier_mask  # noqa: E402


@pytest.fixture
def frame():
    rng = np.random.default_rng(0)
    img = rng.poisson(5000, (120, 160)).astype(np.float64)
    img[:, 80:86] = -1                       # vertical gap, columns 80..85
    img[:, 86] *= 1.4                        # right-hand edge column: systematic edge response
    img[:, 79] *= 1.3
    img[30, 40] = 5000 * 6                   # interior hot pixel
    img[70, 20] = 50                         # interior dead (low but > 0, so not in the threshold mask)
    img[50, 86] = 5000 * 6                   # genuinely hot pixel ON the edge column
    return img, img <= 0


def _flag(img, gap, **kw):
    m, _ = spatial_outlier_mask(img, img, 6.0, 1.25, 0.8, None, **kw)
    return m & ~gap


def test_old_behaviour_flags_the_edge_column(frame):
    img, gap = frame
    m = _flag(img, gap)                       # no invalid mask: previous behaviour
    assert m[:, 86].sum() > 60                # a dotted/solid line along the gap


def test_gap_aware_alone_still_flags_a_real_edge_response(frame):
    img, gap = frame
    m = _flag(img, gap, invalid=gap, guard_px=0)
    assert m[:, 86].sum() > 60                # 1.4x > 1.25x hot gate: the guard is what handles it


def test_edge_guard_removes_edge_line_but_keeps_real_defects(frame):
    img, gap = frame
    m = _flag(img, gap, invalid=gap, guard_px=1, guard_factor=2.0)
    assert m[:, 86].sum() == 1 and m[50, 86]          # only the real defect on the edge
    assert m[:, 79].sum() == 0
    assert m[30, 40] and m[70, 20]                     # interior defects kept
    assert m.sum() == 3


def test_guard_factor_is_the_cutoff(frame):
    img, gap = frame
    assert not _flag(img, gap, invalid=gap, guard_px=1, guard_factor=2.0)[10, 86]
    assert _flag(img, gap, invalid=gap, guard_px=1, guard_factor=1.2)[10, 86]


def test_gap_does_not_bias_neighbours_when_edge_is_normal():
    rng = np.random.default_rng(1)
    img = rng.poisson(3000, (80, 100)).astype(np.float64)
    img[:, 40:47] = -1
    img[39:42, 30:70] = -1                   # crossing gap row (corner pixels)
    gap = img <= 0
    old, _ = spatial_outlier_mask(img, img, 6.0, 1.25, 0.8, None)
    new, _ = spatial_outlier_mask(img, img, 6.0, 1.25, 0.8, None, invalid=gap, guard_px=0)
    assert (old & ~gap).sum() > 0             # the bias artifact existed ...
    assert (new & ~gap).sum() == 0            # ... and is gone with gap-aware medians


def test_no_invalid_pixels_matches_the_plain_filter(frame):
    img, _ = frame
    img = np.abs(img)
    a, _ = spatial_outlier_mask(img, img, 6.0, 1.5, 0.5, None)
    b, _ = spatial_outlier_mask(img, img, 6.0, 1.5, 0.5, None, invalid=np.zeros(img.shape, bool),
                                guard_px=1)
    assert (a == b).all()
