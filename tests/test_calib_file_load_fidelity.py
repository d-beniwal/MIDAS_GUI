"""``CalibrationTab._load_calib_file`` must not lose or invent geometry.

Two things a backend-written ``calibration.json`` exposed (it records the
refined distortion but no ``im_trans`` at all):

* the loader unticked Flip Y / Flip Z / Transpose, reading "the file is silent
  about the frame" as "the frame has no transform" — silently re-framing a
  geometry that is only meaningful in the frame it was fitted in;
* it dropped the distortion coefficients entirely, so the next fit started from
  a distortion-free detector.
"""
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from midas_gui.helpers import geometry_fields_from_file

pytest.importorskip("PyQt5.QtWidgets")

_BACKEND_JSON = {
    "wavelength_A": 0.17298, "pxY_um": 150.0, "pxZ_um": 150.0,
    "NrPixelsY": 2880, "NrPixelsZ": 2880,
    "Lsd_um": 1000473.93, "BC_y_px": 1430.433, "BC_z_px": 1342.486,
    "tx_deg": 0.0, "ty_deg": -0.4718, "tz_deg": -0.2205,
    # Exactly as midas-calibrate-v2 writes it: every slot present, most zero.
    "distortion": {"iso_R2": 4.2407e-4, "iso_R4": -2.1591e-4, "iso_R6": -1.5344e-5,
                   "a1": 0.0, "phi1": 0.0, "a2": 0.0, "phi2": 0.0},
    # NOTE: no "im_trans" key — that is the point of this fixture.
}


_SCRATCH: list = []


@pytest.fixture(autouse=True)
def _clean_scratch():
    """Own tempdir rather than ``tmp_path``: pyproject pins
    ``--basetemp=.scratch``, which races with ``--forked`` (see
    .context/DECISIONS.md 2026-09-09) and errors these tests out in setup.
    Owning it also means owning the cleanup — leave nothing in /tmp."""
    yield
    while _SCRATCH:
        shutil.rmtree(_SCRATCH.pop(), ignore_errors=True)


def _tmpdir() -> Path:
    d = tempfile.mkdtemp(prefix="mg_calibload_")
    _SCRATCH.append(d)
    return Path(d)


def _write(**overrides):
    d = dict(_BACKEND_JSON); d.update(overrides)
    p = _tmpdir() / "calibration.json"
    p.write_text(json.dumps(d))
    return str(p)


def test_absent_im_trans_is_reported_as_unknown_not_empty():
    g = geometry_fields_from_file(_write())
    assert g["im_trans"] == []          # callers still get a plain list
    assert g["im_trans_in_file"] is False


def test_present_but_empty_im_trans_is_a_real_answer():
    g = geometry_fields_from_file(_write(im_trans=[]))
    assert g["im_trans"] == []
    assert g["im_trans_in_file"] is True


def test_paramstest_without_imtransopt_lines_means_no_transform():
    """Unlike a JSON, a paramstest that lists no ImTransOpt genuinely says
    'no transform' — it must keep clearing the boxes."""
    p = _tmpdir() / "paramstest.txt"
    p.write_text("NrPixelsY 2880\nNrPixelsZ 2880\npxY 150.0\npxZ 150.0\n"
                 "Lsd 1000473.93\nBC 1430.433 1342.486\n"
                 "tx 0.0\nty -0.4718\ntz -0.2205\nWavelength 0.17298\n")
    g = geometry_fields_from_file(str(p))
    assert g["im_trans_in_file"] is True


def _tab(qapp):
    from midas_gui.tab_calibrate import CalibrationTab
    return CalibrationTab()


def test_loading_a_silent_file_leaves_the_flips_alone(monkeypatch):
    from PyQt5 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    path = _write()
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (path, "")))
    t = _tab(app)
    t._flip_y.setChecked(True); t._flip_z.setChecked(False); t._transp.setChecked(True)
    t._load_calib_file()
    assert (t._flip_y.isChecked(), t._flip_z.isChecked(), t._transp.isChecked()) \
        == (True, False, True)


def test_a_file_that_does_state_the_transform_still_wins(monkeypatch):
    from PyQt5 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    path = _write(im_trans=[2])
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (path, "")))
    t = _tab(app)
    t._flip_y.setChecked(True); t._transp.setChecked(True)
    t._load_calib_file()
    assert (t._flip_y.isChecked(), t._flip_z.isChecked(), t._transp.isChecked()) \
        == (False, True, False)


def test_distortion_is_seeded_from_the_file_skipping_zero_slots(monkeypatch):
    from PyQt5 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    path = _write()
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (path, "")))
    t = _tab(app)
    t._load_calib_file()
    assert set(t._seed_dist) == {"iso_R2", "iso_R4", "iso_R6"}
    assert t._seed_dist["iso_R2"] == pytest.approx(4.2407e-4)
    assert t._seed_en_dist.isChecked()
