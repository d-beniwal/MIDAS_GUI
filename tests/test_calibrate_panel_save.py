"""CalibrationTab._save_json must (re)write a co-located _panelshifts.txt
sidecar for a multi-panel result, instead of leaving the saved
calibration.json's panel_shifts_path pointing at whatever transient file
was live when Fit finished (see .context/DECISIONS.md for the bug this
fixes).
"""
import json
from types import SimpleNamespace

import numpy as np
import pytest


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _panel_result(panel_shifts_path):
    ns = SimpleNamespace(
        Lsd=200000.0, BC_y=1024.0, BC_z=1024.0, tx=0.0, ty=0.0, tz=0.0,
        distortion={"iso_R2": 0.1}, pxY=200.0, pxZ=200.0,
        NrPixelsY=2048, NrPixelsZ=2048, wavelength_A=0.1729,
        panel_layout={"n_y": 1, "n_z": 2, "sy": 100, "sz": 100},
        panel_shifts_path=panel_shifts_path,
    )
    ns._calibrant_name = "CeO2"
    ns._panel_unpacked = {
        "panel_delta_yz": np.array([[0.1, -0.2], [0.3, 0.4]]),
        "panel_delta_theta": np.array([0.001, -0.002]),
        "panel_delta_lsd": np.array([1.5, -1.5]),
        "panel_delta_p2": np.array([0.0, 0.0]),
    }
    return ns


def test_save_json_rewrites_colocated_panel_shifts_sidecar(app, tmp_path, monkeypatch):
    from PyQt5 import QtWidgets
    from midas_gui.tab_calibrate import CalibrationTab

    # Simulates the ephemeral tempfile calib._attach_panel_result falls back
    # to when no Output folder was set during Fit.
    tempfile_shifts = tmp_path / "elsewhere" / "some_tmp_panel_shifts.txt"
    tempfile_shifts.parent.mkdir()
    tempfile_shifts.write_text("stale")

    tab = CalibrationTab()
    tab._result = _panel_result(str(tempfile_shifts))

    out_path = tmp_path / "calibration.json"
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName",
                         staticmethod(lambda *a, **k: (str(out_path), "")))
    monkeypatch.setattr(QtWidgets.QMessageBox, "information",
                         staticmethod(lambda *a, **k: None))

    tab._save_json()

    sidecar = out_path.with_name("calibration_panelshifts.txt")
    assert sidecar.is_file()
    saved = json.loads(out_path.read_text())
    assert saved["panel_shifts_path"] == str(sidecar)
    assert not saved["panel_shifts_path"].startswith(str(tempfile_shifts.parent))
    lines = sidecar.read_text().splitlines()
    assert len(lines) == 2
