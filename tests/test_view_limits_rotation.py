"""Pan limits must bound where the picture IS, not where the array says.

Reported at 1-ID-E, with a Hydra ge4 open on the Calibrate tab: "I still
cannot scroll over to see ge4 detector image."

_apply_view_limits bounded pan to (0, 0, w, h) padded by half the image
span -- the raw ARRAY shape. That is where the picture sits only when it is
drawn untransformed at the origin. set_lab_rotation rotates the ImageItem
about the beam centre, and on a Hydra panel that centre is off the frame:
ge4 is tx=210 deg about BC=(2387, 2190) on 2048x2048, which sends the image
to x in [2751, 5548] while the limits stopped at 3072. Eleven percent of its
width was reachable; the black expanse on screen was empty canvas where the
array's rectangle used to be.

The numbers below are that panel's real geometry, from
connolly_oct26_..._003502.ge4.instr.txt.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

pytestmark = pytest.mark.forked

#: ge4 as installed, and the rotated extent it implies.
TX, BCY, BCZ, N = 210.0, 2386.744, 2189.548, 2048


def _rotated_bbox(tx=TX, bcy=BCY, bcz=BCZ, n=N):
    """Independent of the widget: the map set_lab_rotation documents."""
    a = math.radians(tx)
    c, s = math.cos(a), math.sin(a)
    pts = [(bcy + (Y - bcy) * c + (Z - bcz) * s,
            bcz - (Y - bcy) * s + (Z - bcz) * c)
           for Y in (0, n) for Z in (0, n)]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), max(xs), min(ys), max(ys)


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def viewer(app):
    from midas_gui.widgets import ImageViewer
    v = ImageViewer()
    v.show()
    v.resize(600, 600)
    v.set_image(np.zeros((N, N), dtype=np.float32))
    return v


def _limits(v):
    st = v._iv.getView().getViewBox().state["limits"]
    return st["xLimits"], st["yLimits"]


def test_an_unrotated_frame_keeps_the_original_limits(viewer):
    """Every detector that ever worked here is unrolled, and must be
    byte-identical: image rect padded by half its span."""
    pad = N * 0.5
    assert _limits(viewer) == ([-pad, N + pad], [-pad, N + pad])


def test_the_rotated_image_is_entirely_reachable(viewer):
    viewer.set_lab_rotation(TX, BCY, BCZ)
    (x_lo, x_hi), (y_lo, y_hi) = _limits(viewer)
    bx_lo, bx_hi, by_lo, by_hi = _rotated_bbox()
    assert x_lo <= bx_lo and x_hi >= bx_hi
    assert y_lo <= by_lo and y_hi >= by_hi


def test_the_old_limits_would_not_have_reached_it(viewer):
    """The regression this guards: not a preference, a fact about ge4."""
    bx_lo, bx_hi, _, _ = _rotated_bbox()
    old_hi = N + N * 0.5
    assert bx_lo > 0 and bx_hi > old_hi, "ge4 must sit outside the old box"
    reachable = max(0.0, min(bx_hi, old_hi) - bx_lo) / (bx_hi - bx_lo)
    assert reachable < 0.2, "the whole point is that most of it was cut off"


def test_unrotating_restores_the_plain_limits(viewer):
    pad = N * 0.5
    viewer.set_lab_rotation(TX, BCY, BCZ)
    viewer.set_lab_rotation(0.0, BCY, BCZ)
    assert _limits(viewer) == ([-pad, N + pad], [-pad, N + pad])


def test_a_new_frame_does_not_undo_the_rotated_limits(viewer):
    """set_image recomputes the limits, and it must read the view rather
    than the array shape -- otherwise loading the next frame of a rolled
    panel would strand the user again."""
    viewer.set_lab_rotation(TX, BCY, BCZ)
    viewer.set_image(np.zeros((N, N), dtype=np.float32))
    (x_lo, x_hi), _ = _limits(viewer)
    bx_lo, bx_hi, _, _ = _rotated_bbox()
    assert x_lo <= bx_lo and x_hi >= bx_hi


@pytest.mark.parametrize("tx", [30.0, 120.0, 210.0, 300.0])
def test_every_hydra_roll_leaves_its_panel_reachable(viewer, tx):
    viewer.set_lab_rotation(tx, BCY, BCZ)
    (x_lo, x_hi), (y_lo, y_hi) = _limits(viewer)
    bx_lo, bx_hi, by_lo, by_hi = _rotated_bbox(tx=tx)
    assert x_lo <= bx_lo and x_hi >= bx_hi
    assert y_lo <= by_lo and y_hi >= by_hi


def test_zoom_in_granularity_still_comes_from_the_pixels(viewer):
    """How far you may magnify is a property of the image, not of how far
    an overlay happens to reach."""
    before = viewer._iv.getView().getViewBox().state["limits"]["xRange"][0]
    viewer.set_lab_rotation(TX, BCY, BCZ)
    after = viewer._iv.getView().getViewBox().state["limits"]["xRange"][0]
    assert before == after
