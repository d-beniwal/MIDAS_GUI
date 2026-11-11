"""Editing the Data path without pressing Enter must not leave Solve Cell reading the old file."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import h5py
import numpy as np
import pytest
from PyQt5 import QtWidgets

from midas_gui.widgets import DataLoaderPanel


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _h5(path, value):
    with h5py.File(path, "w") as f:
        f.create_dataset("entry/data/data", data=np.full((3, 8, 8), value, np.uint16))


def test_path_edit_without_enter_is_reloaded(app, tmp_path):
    a, b = tmp_path / "a.h5", tmp_path / "b.h5"
    _h5(a, 1); _h5(b, 2)
    w = DataLoaderPanel()
    w.set_path(str(a), dataset="entry/data/data")
    assert w.full_stack().max() == 1
    assert w.reload_if_path_changed() is False            # nothing edited
    w._path_ed.setText(str(b))                            # typed, no Enter
    assert w.full_stack().max() == 1                      # the stale read being guarded against
    assert w.reload_if_path_changed() is True
    assert w.full_stack().max() == 2
    assert w.reload_if_path_changed() is False
