"""Data Viewer's frame scrubber lives under the image viewer (mirroring
tab_calibrate.py / Batch Integrate's "Eta-R cakes" tab) instead of the
DataLoaderPanel's own compact nav row.

Not fork-isolated, for the same reason as test_view_tab_controls.py: every
test in a forked file that builds a DataViewerTab dies with SIGSEGV in the
child on this machine. Run plain, these pass — see .context/STATE.md.
"""
from __future__ import annotations

import numpy as np
import pytest
import tifffile
from PyQt5 import QtWidgets

from midas_gui.tab_view import DataViewerTab


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def tab(app):
    return DataViewerTab()


def test_loader_own_nav_row_is_hidden(tab):
    """hide_frame_field=True on this tab's loader — the scrubber under the
    viewer is the only one shown."""
    assert tab._loader._nav_row.isHidden()


def test_scrub_bar_hidden_for_a_single_frame(tab, tmp_path):
    path = tmp_path / "one.tif"
    tifffile.imwrite(path, np.zeros((8, 8), dtype=np.float32))
    tab._loader.set_path(str(path))
    tab._on_loader_data()
    assert tab._loader.n_frames() == 1
    assert tab._frame_scrub_bar.isHidden()


def test_scrub_bar_tracks_a_multi_frame_stack(tab, tmp_path):
    path = tmp_path / "stack.tif"
    tifffile.imwrite(path, np.zeros((5, 8, 8), dtype=np.float32))
    tab._loader.set_path(str(path))
    tab._on_loader_data()
    assert tab._loader.n_frames() == 5
    assert not tab._frame_scrub_bar.isHidden()
    assert tab._frame_slider.minimum() == 0
    assert tab._frame_slider.maximum() == 4
    assert tab._frame_slider.value() == tab._loader.frame_index()
    assert tab._frame_lbl.text() == f"frame {tab._loader.frame_index() + 1}/5"


def test_dragging_the_scrub_bar_moves_the_loaders_frame(tab, tmp_path):
    path = tmp_path / "stack2.tif"
    tifffile.imwrite(path, np.arange(4 * 8 * 8, dtype=np.float32).reshape(4, 8, 8))
    tab._loader.set_path(str(path))
    tab._on_loader_data()
    tab._frame_slider.setValue(2)
    assert tab._loader.frame_index() == 2


def test_step_buttons_clamp_at_the_ends(tab, tmp_path):
    path = tmp_path / "stack3.tif"
    tifffile.imwrite(path, np.zeros((3, 8, 8), dtype=np.float32))
    tab._loader.set_path(str(path))
    tab._on_loader_data()
    tab._frame_slider.setValue(0)
    tab._step_frame(-1)
    assert tab._loader.frame_index() == 0
    tab._frame_slider.setValue(2)
    tab._step_frame(1)
    assert tab._loader.frame_index() == 2
