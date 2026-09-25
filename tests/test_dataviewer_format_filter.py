"""Data Viewer folder "Format:" filter: helpers._folder_format_groups /
_collect_frame_paths(ext_group=...), and DataLoaderPanel(folder_format_filter=True)
picking up only the format actually selected from a mixed folder.

Not fork-isolated: a DataLoaderPanel pulls in the same pyqtgraph-backed
midas_gui.widgets module as DataViewerTab, which SIGSEGVs a forked child on
this machine (see test_view_tab_controls.py's header comment). These tests
run plain instead.
"""
from __future__ import annotations

import numpy as np
import pytest
import tifffile


def _app_and_widgets():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    import midas_gui.widgets as W
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return app, W


# ── helpers._folder_format_groups / _collect_frame_paths ────────────────

def test_folder_format_groups_only_lists_formats_present(tmp_path):
    from midas_gui.helpers import _folder_format_groups

    for i in range(3):
        tifffile.imwrite(tmp_path / f"f{i}.tif", np.zeros((4, 4), dtype=np.float32))
    (tmp_path / "notes.txt").write_text("not an image")

    groups = _folder_format_groups(tmp_path)
    assert set(groups) == {"TIFF"}
    assert len(groups["TIFF"]) == 3


def test_collect_frame_paths_filters_by_ext_group(tmp_path):
    from midas_gui.helpers import _collect_frame_paths

    for i in range(2):
        tifffile.imwrite(tmp_path / f"f{i}.tif", np.zeros((4, 4), dtype=np.float32))
    import h5py
    with h5py.File(tmp_path / "stack.h5", "w") as f:
        f.create_dataset("exchange/data", data=np.zeros((4, 4), dtype=np.float32))

    all_paths = _collect_frame_paths(str(tmp_path))
    assert len(all_paths) == 3

    tiff_only = _collect_frame_paths(str(tmp_path), ext_group="TIFF")
    assert len(tiff_only) == 2
    assert all(p.endswith(".tif") for p in tiff_only)

    h5_only = _collect_frame_paths(str(tmp_path), ext_group="HDF5")
    assert len(h5_only) == 1
    assert h5_only[0].endswith(".h5")


def test_collect_frame_paths_unknown_group_falls_back_to_all(tmp_path):
    from midas_gui.helpers import _collect_frame_paths

    tifffile.imwrite(tmp_path / "f0.tif", np.zeros((4, 4), dtype=np.float32))
    assert _collect_frame_paths(str(tmp_path), ext_group="NOPE") == \
        _collect_frame_paths(str(tmp_path))


# ── DataLoaderPanel(folder_format_filter=True) ───────────────────────────

def test_format_row_hidden_for_single_format_folder(tmp_path):
    _app, W = _app_and_widgets()
    for i in range(2):
        tifffile.imwrite(tmp_path / f"f{i}.tif", np.zeros((4, 4), dtype=np.float32))
    panel = W.DataLoaderPanel(mode="stack", folder_format_filter=True)
    panel.set_path(str(tmp_path))
    assert panel.n_frames() == 2
    assert panel._fmt_row.isHidden()


def test_format_row_shown_and_filters_mixed_folder(tmp_path):
    _app, W = _app_and_widgets()
    for i in range(3):
        tifffile.imwrite(tmp_path / f"f{i}.tif", np.zeros((4, 4), dtype=np.float32))
    import h5py
    with h5py.File(tmp_path / "stack.h5", "w") as f:
        f.create_dataset("exchange/data", data=np.zeros((4, 4), dtype=np.float32))

    panel = W.DataLoaderPanel(mode="stack", folder_format_filter=True)
    panel.set_path(str(tmp_path))
    assert not panel._fmt_row.isHidden()
    assert panel.n_frames() == 4
    items = [panel._fmt_combo.itemText(i) for i in range(panel._fmt_combo.count())]
    assert items == ["All", "TIFF", "HDF5"]

    idx = panel._fmt_combo.findText("TIFF")
    panel._fmt_combo.setCurrentIndex(idx)
    assert panel.n_frames() == 3

    idx = panel._fmt_combo.findText("HDF5")
    panel._fmt_combo.setCurrentIndex(idx)
    assert panel.n_frames() == 1


def test_format_filter_off_by_default_for_other_tabs(tmp_path):
    """No opt-in flag => no combo built at all, and today's unfiltered
    behavior is unchanged — Calibrate/Mask Builder/Batch/Refine callers of
    DataLoaderPanel never pass folder_format_filter."""
    _app, W = _app_and_widgets()
    for i in range(2):
        tifffile.imwrite(tmp_path / f"f{i}.tif", np.zeros((4, 4), dtype=np.float32))
    import h5py
    with h5py.File(tmp_path / "stack.h5", "w") as f:
        f.create_dataset("exchange/data", data=np.zeros((4, 4), dtype=np.float32))

    panel = W.DataLoaderPanel(mode="stack")
    panel.set_path(str(tmp_path))
    assert not hasattr(panel, "_fmt_row")
    assert panel.n_frames() == 3


def test_folder_format_persists_through_get_state_set_state(tmp_path):
    _app, W = _app_and_widgets()
    for i in range(3):
        tifffile.imwrite(tmp_path / f"f{i}.tif", np.zeros((4, 4), dtype=np.float32))
    import h5py
    with h5py.File(tmp_path / "stack.h5", "w") as f:
        f.create_dataset("exchange/data", data=np.zeros((4, 4), dtype=np.float32))

    panel = W.DataLoaderPanel(mode="stack", folder_format_filter=True)
    panel.set_path(str(tmp_path))
    idx = panel._fmt_combo.findText("TIFF")
    panel._fmt_combo.setCurrentIndex(idx)
    assert panel.n_frames() == 3
    state = panel.get_state()
    assert state["folder_format"] == "TIFF"

    restored = W.DataLoaderPanel(mode="stack", folder_format_filter=True)
    restored.set_state(state)
    assert restored.n_frames() == 3
    assert restored._fmt_combo.currentText() == "TIFF"
