"""Qt smoke tests for the Solve Cell tab and its registration in app.py.

Builds a real SolveCellTab, hence forked — see STATE.md on the
interpreter-teardown crash risk around pyqtgraph widgets, and the documented
rule that Qt / midas_gui GUI-module imports must be deferred into a fixture
(module-scope PyQt5 imports break os.fork() in the collecting parent process).
"""
import json
import shutil
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.forked


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def tab(app):
    from midas_gui.tab_solve_cell import SolveCellTab
    return SolveCellTab()


# Own tempdir rather than pytest's `tmp_path`: pyproject pins
# `--basetemp=.scratch`, which races with `--forked` (see
# .context/DECISIONS.md 2026-09-09) and errors these tests out in setup --
# same workaround as tests/test_calib_file_load_fidelity.py.
_SCRATCH: list = []


@pytest.fixture(autouse=True)
def _clean_scratch():
    yield
    while _SCRATCH:
        shutil.rmtree(_SCRATCH.pop(), ignore_errors=True)


def _write_calib_json(**overrides) -> str:
    d = {
        "wavelength_A": 0.17298, "pxY": 150.0, "pxZ": 150.0,
        "NrPixelsY": 2880, "NrPixelsZ": 2880,
        "Lsd": 1000473.93, "BC_y": 1430.433, "BC_z": 1342.486,
        "tx": 0.0, "ty": -0.4718, "tz": -0.2205,
    }
    d.update(overrides)
    tmpdir = Path(tempfile.mkdtemp(prefix="mg_solvecell_calibload_"))
    _SCRATCH.append(tmpdir)
    p = tmpdir / "calibration.json"
    p.write_text(json.dumps(d))
    return str(p)


def test_tab_constructs_without_exception(tab):
    assert tab is not None
    assert tab._worker is None
    assert tab._spots_df is None


def test_run_ingest_warns_when_no_calibration_loaded(tab, monkeypatch):
    from PyQt5 import QtWidgets
    warned = {}

    def fake_warning(parent, title, text):
        warned["title"] = title
        return QtWidgets.QMessageBox.Ok

    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", staticmethod(fake_warning))
    tab._run_ingest()
    assert warned.get("title") == "No calibration loaded"
    assert tab._worker is None


def test_run_ingest_warns_when_no_raw_data_loaded(tab, monkeypatch):
    from PyQt5 import QtWidgets
    path = _write_calib_json()
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (path, "")))
    tab._active_panel()._load_calib_file()

    warned = {}

    def fake_warning(parent, title, text):
        warned["title"] = title
        return QtWidgets.QMessageBox.Ok

    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", staticmethod(fake_warning))
    tab._run_ingest()
    assert warned.get("title") == "No raw data"
    assert tab._worker is None


def test_run_diamond_filter_warns_before_ingest(tab, monkeypatch):
    from PyQt5 import QtWidgets
    warned = {}
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        staticmethod(lambda *a: warned.setdefault("hit", True)))
    tab._run_diamond_filter()
    assert warned.get("hit") is True


def test_run_ab_initio_warns_before_diamond_filter(tab, monkeypatch):
    from PyQt5 import QtWidgets
    warned = {}
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        staticmethod(lambda *a: warned.setdefault("hit", True)))
    tab._run_ab_initio()
    assert warned.get("hit") is True


def test_run_refine_warns_before_ab_initio(tab, monkeypatch):
    from PyQt5 import QtWidgets
    warned = {}
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        staticmethod(lambda *a: warned.setdefault("hit", True)))
    tab._run_refine()
    assert warned.get("hit") is True


def test_populate_result_grid_renders_cell_rows(tab):
    result = {
        "cell": [7.9569, 7.9645, 7.9802, 90.016, 90.030, 89.668],
        "cell_sigma": [0.002, 0.002, 0.002, 0.01, 0.01, 0.01],
        "conventional_cell": [7.9672, 7.9672, 7.9672],
        "conventional_system": "cubic",
        "holohedry_system": "cubic", "holohedry_order": 48,
        "rms_drlv": 0.0343, "n_reflections": 437,
    }
    tab._populate_result_grid(result)
    assert tab._param_grid.count() > 0


def test_solve_cell_is_registered_as_an_optional_tab():
    from midas_gui import constants as C
    assert "Solve Cell" in C.OPTIONAL_TABS
    assert "Solve Cell" not in C.ALWAYS_TABS


def test_solve_cell_tab_appears_in_app_tab_specs_source():
    import inspect
    from midas_gui import app as app_module
    src = inspect.getsource(app_module)
    assert '"Solve Cell"' in src
    assert "_solve_cell_tab" in src


# ── panel management ───────────────────────────────────────────────────

def test_tab_starts_with_exactly_one_panel(tab):
    assert tab._panel_tabs.count() == 1
    assert len(tab._panels) == 1
    assert tab._active_panel() is tab._panel_tabs.widget(0)


def test_add_panel_creates_a_second_independent_tab(tab, monkeypatch):
    from PyQt5 import QtWidgets
    first = tab._active_panel()
    second = tab._add_panel()
    assert tab._panel_tabs.count() == 2
    assert len(tab._panels) == 2
    assert second is not first
    assert second.loader is not first.loader
    assert not first.has_calibration()
    assert not second.has_calibration()

    path = _write_calib_json(Lsd=999.0)
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (path, "")))
    second._load_calib_file()
    assert second.has_calibration()
    assert not first.has_calibration()
    assert second.geometry_cfg()["Lsd"] == pytest.approx(999.0)


def test_closing_the_last_panel_tab_is_refused(tab, monkeypatch):
    from PyQt5 import QtWidgets
    informed = {}
    monkeypatch.setattr(QtWidgets.QMessageBox, "information",
                        staticmethod(lambda *a: informed.setdefault("hit", True)))
    tab._close_panel_tab(0)
    assert informed.get("hit") is True
    assert tab._panel_tabs.count() == 1


def test_closing_a_non_last_panel_tab_removes_it(tab):
    tab._add_panel()
    assert tab._panel_tabs.count() == 2
    tab._close_panel_tab(1)
    assert tab._panel_tabs.count() == 1
    assert len(tab._panels) == 1


def test_active_panel_switch_changes_geometry_cfg(tab, monkeypatch):
    from PyQt5 import QtWidgets
    first = tab._active_panel()
    path1 = _write_calib_json(Lsd=111.0)
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (path1, "")))
    first._load_calib_file()

    second = tab._add_panel()
    path2 = _write_calib_json(Lsd=222.0)
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (path2, "")))
    second._load_calib_file()

    assert tab._panel_tabs.currentWidget() is second
    assert tab._geometry_cfg()["Lsd"] == pytest.approx(222.0)
    tab._panel_tabs.setCurrentIndex(0)
    assert tab._geometry_cfg()["Lsd"] == pytest.approx(111.0)


# ── calibration-file loading ────────────────────────────────────────────

def test_load_calib_file_populates_panel_geometry_fields(tab, monkeypatch):
    from PyQt5 import QtWidgets
    path = _write_calib_json()
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (path, "")))
    panel = tab._active_panel()
    assert not panel.has_calibration()
    panel._load_calib_file()
    assert panel.has_calibration()
    g = panel.geometry_cfg()
    assert g["wavelength_A"] == pytest.approx(0.17298)
    assert g["px_um"] == pytest.approx(150.0)
    assert g["Lsd"] == pytest.approx(1000473.93)
    assert g["BC_y"] == pytest.approx(1430.433)
    assert g["BC_z"] == pytest.approx(1342.486)
    assert g["ty"] == pytest.approx(-0.4718)
    assert g["tz"] == pytest.approx(-0.2205)
    assert g["nrpixels_y"] == 2880
    assert g["nrpixels_z"] == 2880
    assert "Loaded" in panel._calib_note.text()


def test_load_calib_file_shows_but_does_not_use_nonzero_tx(tab, monkeypatch):
    from PyQt5 import QtWidgets
    path = _write_calib_json(tx=1.5)
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (path, "")))
    panel = tab._active_panel()
    panel._load_calib_file()
    assert "not used" in panel._calib_note.text()
    # tx is still fixed at 0 for the Phase 1 pipeline regardless of display.
    assert "omega_ref_frame_idx" in panel.geometry_cfg()
    assert "tx" not in panel.geometry_cfg()


def test_load_calib_file_reports_error_on_bad_file(tab, monkeypatch):
    from PyQt5 import QtWidgets
    bad_dir = Path(tempfile.mkdtemp(prefix="mg_solvecell_bad_"))
    _SCRATCH.append(bad_dir)
    bad = bad_dir / "bad.json"
    bad.write_text("{}")
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (str(bad), "")))
    critical = {}
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical",
                        staticmethod(lambda *a: critical.setdefault("hit", True)))
    tab._active_panel()._load_calib_file()
    assert critical.get("hit") is True
