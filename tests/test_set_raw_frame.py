"""Tests for ``ImageViewer.set_raw_frame`` — the single place a caller holding
both a raw detector frame and calibration geometry goes through.

Every BC-based overlay (Rmin/Rmax circles, bin grids, lab-frame axes,
calibration rings) is computed in the *im_trans-applied* frame's coordinate
system. A call site that forgets the flip draws the overlay offset from the
real rings underneath and nothing errors — exactly the bug Batch Integrate's
Detector view shipped with. So the contract worth pinning is that the returned
array is the transformed one, identical to what ``_apply_im_trans`` produces.

Builds one pyqtgraph ImageView, hence forked — see STATE.md on the
interpreter-teardown crash risk around pyqtgraph widgets.
"""
import numpy as np
import pytest

pytestmark = pytest.mark.forked


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def viewer(app):
    from midas_gui.widgets import ImageViewer
    return ImageViewer()


# Asymmetric so any flip/transpose is detectable.
RAW = np.arange(12, dtype=np.float32).reshape(3, 4)


@pytest.mark.parametrize("codes", [
    (),           # no transform
    (1,),         # flipY
    (2,),         # flipZ
    (3,),         # transpose
    (1, 2),       # flipY + flipZ
    (2, 3),       # order matters
    (3, 2),
])
def test_returns_the_same_array_apply_im_trans_would(viewer, codes):
    from midas_gui.helpers import _apply_im_trans
    out = viewer.set_raw_frame(RAW, codes)
    expected = _apply_im_trans(RAW, codes) if codes else RAW
    np.testing.assert_array_equal(out, expected)


def test_no_codes_passes_the_frame_through_untouched(viewer):
    for empty in ((), None, []):
        out = viewer.set_raw_frame(RAW, empty)
        np.testing.assert_array_equal(out, RAW)


def test_transform_actually_changes_the_frame(viewer):
    """Guard against a no-op regression that would silently restore the
    'overlay offset from the rings' bug."""
    out = viewer.set_raw_frame(RAW, (1,))
    assert not np.array_equal(out, RAW)
    np.testing.assert_array_equal(out, RAW[:, ::-1])


def test_transpose_changes_the_shape(viewer):
    out = viewer.set_raw_frame(RAW, (3,))
    assert out.shape == (RAW.shape[1], RAW.shape[0])


def test_displayed_image_is_the_transformed_frame(viewer):
    """The returned array and what the viewer actually shows must agree —
    callers cache the return value as "the currently displayed frame" for
    pixel readback and mask overlays."""
    out = viewer.set_raw_frame(RAW, (2,))
    np.testing.assert_array_equal(np.asarray(viewer._data), out)


def test_accepts_a_list_of_codes_not_just_a_tuple(viewer):
    """Callers pass whatever `_im_trans_codes()` / a stored geometry hands
    them; a list must behave like the equivalent tuple."""
    np.testing.assert_array_equal(viewer.set_raw_frame(RAW, [1, 2]),
                                  viewer.set_raw_frame(RAW, (1, 2)))
