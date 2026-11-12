"""Batch Integrate: end=0 means 'to the last frame'; live plots can be off."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import h5py
import numpy as np
import pytest

pytest.importorskip("midas_integrate_v2")


def test_single_h5_end_zero_is_unbounded(tmp_path):
    from PyQt5 import QtWidgets
    from midas_gui.widgets import DataLoaderPanel
    from midas_gui.workers import _open_source_cfg
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    p = tmp_path / "scan.h5"
    with h5py.File(p, "w") as f:
        f["frames"] = np.zeros((10, 4, 4), np.uint16)
    panel = DataLoaderPanel(mode="stream", unify_combine=True)
    panel.set_path(str(p), "frames", load=False)
    panel._fr_start.setValue(0); panel._fr_end.setValue(0)
    cfg = panel.source_cfg()
    assert cfg["frame_end"] is None
    assert _open_source_cfg(cfg).n_frames == 10


def test_live_plot_toggle_skips_plots():
    from PyQt5 import QtWidgets
    from midas_gui.tab_batch import BatchTab
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    tab = BatchTab()
    r = np.arange(5.0)
    tab._live_plot_chk.setChecked(False)
    tab._on_frame("1", r, np.ones(5), None)
    assert tab._integrated_fids == {"1"}
    assert not getattr(tab, "_wf_started", False)
    tab._live_plot_chk.setChecked(True)
    tab._on_frame("2", r, np.ones(5), None)
    assert tab._wf_started and "2" in tab._integrated_fids
