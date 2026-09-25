"""Mask Builder: folder / multi-frame Image loading with a frame navigator,
and threshold-mask projection (average / sum / max) across those frames.

Builds ``MaskTab``, which owns a pyqtgraph ``ImageViewer`` — forked per the
pyqtgraph-teardown-crash risk documented in ``.context/STATE.md``, and every
Qt / ``midas_gui`` import is deferred into ``_load_qt()`` (called from the
``app`` fixture) so pytest's parent-process module import never touches
PyQt5 — see ``tests/test_hydra_batch_ui.py`` for the same pattern and why.
"""
from __future__ import annotations

import numpy as np
import pytest
import tifffile

pytestmark = pytest.mark.forked

_QT_LOADED = False
QtWidgets = None
MaskTab = None


def _load_qt():
    global _QT_LOADED, QtWidgets, MaskTab
    if _QT_LOADED:
        return
    from PyQt5 import QtWidgets as _QtWidgets
    from midas_gui.tab_mask import MaskTab as _MaskTab
    QtWidgets = _QtWidgets
    MaskTab = _MaskTab
    _QT_LOADED = True


@pytest.fixture(scope="module")
def app():
    _load_qt()
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def tab(app):
    return MaskTab()


def _write_frame_folder(tmp_path, frames):
    d = tmp_path / "frames"
    d.mkdir()
    for i, arr in enumerate(frames):
        tifffile.imwrite(str(d / f"f{i:03d}.tif"), arr)
    return d


def _load_folder(tab, tmp_path, frames):
    d = _write_frame_folder(tmp_path, frames)
    tab._img_edit.setText(str(d))
    tab._load_image()


# ── Folder loading + frame navigator ────────────────────────────────

def test_folder_load_shows_frame_navigator_and_first_frame(tab, tmp_path):
    frames = [np.full((4, 5), v, dtype=np.uint16) for v in (10, 20, 30)]
    _load_folder(tab, tmp_path, frames)
    # isVisible() reflects on-screen visibility (requires a shown window);
    # isHidden() reflects the explicit setVisible()/hide() state — see
    # tests/test_manual_dspacing_calib_ui.py for the same convention.
    assert not tab._frame_nav_row.isHidden()
    assert tab._frame_count_lbl.text() == "/ 3"
    np.testing.assert_array_equal(tab._image, frames[0].astype(np.float32))


def test_frame_navigator_moves_between_frames(tab, tmp_path):
    frames = [np.full((4, 5), v, dtype=np.uint16) for v in (10, 20, 30)]
    _load_folder(tab, tmp_path, frames)
    tab._frame_spin.setValue(2)
    np.testing.assert_array_equal(tab._image, frames[1].astype(np.float32))
    tab._frame_next_btn.click()
    np.testing.assert_array_equal(tab._image, frames[2].astype(np.float32))
    tab._frame_prev_btn.click()
    np.testing.assert_array_equal(tab._image, frames[1].astype(np.float32))


def test_single_file_hides_frame_navigator_and_locks_projection(tab, tmp_path):
    p = tmp_path / "one.tif"
    tifffile.imwrite(str(p), np.zeros((4, 5), dtype=np.uint16))
    tab._img_edit.setText(str(p)); tab._load_image()
    assert tab._frame_nav_row.isHidden()
    assert not tab._thresh_proj_combo.isEnabled()
    assert tab._thresh_proj_combo.currentText() == "Current frame"


def test_multipage_tiff_is_treated_as_a_multi_frame_source(tab, tmp_path):
    p = tmp_path / "stack.tif"
    frames = np.stack([np.full((4, 5), v, dtype=np.uint16) for v in (1, 2, 3)])
    tifffile.imwrite(str(p), frames)
    tab._img_edit.setText(str(p)); tab._load_image()
    assert not tab._frame_nav_row.isHidden()
    assert tab._frame_count_lbl.text() == "/ 3"
    np.testing.assert_array_equal(tab._image, frames[0].astype(np.float32))


# ── Threshold-mask projection ────────────────────────────────────────

def test_threshold_default_uses_only_the_selected_frame(tab, tmp_path):
    """No projection selected -> the threshold step reads only the frame the
    navigator currently shows, per the requested behaviour."""
    frames = [np.full((4, 5), 5.0, dtype=np.float32),
              np.full((4, 5), 500.0, dtype=np.float32),
              np.full((4, 5), 5.0, dtype=np.float32)]
    _load_folder(tab, tmp_path, frames)
    assert tab._thresh_proj_combo.currentText() == "Current frame"
    tab._frame_spin.setValue(2)   # frame index 1 -> value 500
    src = tab._threshold_source_image()
    np.testing.assert_allclose(src, 500.0)


@pytest.mark.parametrize("label, expected", [
    ("Average", (5.0 + 500.0 + 5.0) / 3),
    ("Sum", 5.0 + 500.0 + 5.0),
    ("Max", 500.0),
])
def test_threshold_projection_combines_every_frame(tab, tmp_path, label, expected):
    frames = [np.full((4, 5), 5.0, dtype=np.float32),
              np.full((4, 5), 500.0, dtype=np.float32),
              np.full((4, 5), 5.0, dtype=np.float32)]
    _load_folder(tab, tmp_path, frames)
    tab._thresh_proj_combo.setCurrentText(label)
    src = tab._threshold_source_image()
    np.testing.assert_allclose(src, expected)


def test_compute_mask_uses_the_projection_not_the_displayed_frame(tab, tmp_path):
    """Regression guard: ``_compute()`` must threshold against
    ``_threshold_source_image()``, not ``tab._image``, once a projection
    other than 'Current frame' is selected."""
    frames = [np.full((4, 5), 5.0, dtype=np.float32),
              np.full((4, 5), 999.0, dtype=np.float32)]
    _load_folder(tab, tmp_path, frames)
    tab._lower.setValue(-1e9)     # disable the lower bound
    tab._upper.setValue(100.0)
    tab._thresh_proj_combo.setCurrentText("Max")
    tab._compute()
    # Displayed frame (index 0, value 5) is below the upper bound, but the
    # max-projection (999) is above it everywhere.
    assert tab._thresh_mask.all()


def test_single_frame_source_projection_equals_current_frame(tab, tmp_path):
    """With only one frame, Average/Sum/Max must all reduce to that frame —
    the combo is disabled and forced to 'Current frame' (see the
    single-file test above), so this exercises the fallback path directly."""
    p = tmp_path / "one.tif"
    tifffile.imwrite(str(p), np.full((4, 5), 42.0, dtype=np.float32))
    tab._img_edit.setText(str(p)); tab._load_image()
    src = tab._threshold_source_image()
    np.testing.assert_allclose(src, 42.0)
