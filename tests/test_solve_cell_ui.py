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


# ── reciprocal-space map (3-D) / Detector view ──────────────────────────

def test_view_tabs_have_recip_map_and_detector(tab):
    titles = [tab._view_tabs.tabText(i) for i in range(tab._view_tabs.count())]
    assert "Reciprocal space map" in titles
    assert "Detector" in titles
    assert tab._recip_ax.name == "3d"


def test_update_recip_plot_accepts_xyz_tuples_without_raising(tab):
    import numpy as np
    x, y, z = np.array([0.1, 0.2]), np.array([0.3, 0.4]), np.array([0.5, 0.6])
    tab._update_recip_plot(all_xyz=(x, y, z), diamond_xyz=(x, y, z), indexed_xyz=(x, y, z))
    tab._update_recip_plot()   # all-None (e.g. zero spots) must not raise either


def test_detector_stage_switch_with_no_data_loaded_does_not_raise(tab):
    for i in range(tab._det_stage.count()):
        tab._det_stage.setCurrentIndex(i)
    assert "Run Ingest" in tab._det_frame_lbl.text() or "no data" in tab._det_frame_lbl.text()


def test_detector_tab_tracks_active_panel_switch(tab):
    tab._det_stage.setCurrentIndex(0)   # Raw
    tab._add_panel()
    assert tab._det_slider.isEnabled() is False   # fresh panel, no data loaded


def test_sector_candidates_cfg_defaults_and_parses_override(tab):
    from midas_gui.solve_cell import pipeline as P
    assert tab._sector_candidates_cfg() == P.SECTOR_CANDIDATES_DEFAULT
    tab._sector_candidates_ed.setText("8, 24")
    assert tab._sector_candidates_cfg() == (8, 24)
    tab._sector_candidates_ed.setText("not a number")
    assert tab._sector_candidates_cfg() == P.SECTOR_CANDIDATES_DEFAULT
    tab._sector_candidates_ed.setText("")
    assert tab._sector_candidates_cfg() == P.SECTOR_CANDIDATES_DEFAULT


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


# ── GUI state (project save/restore) ────────────────────────────────────

def test_get_state_set_state_round_trips_panels_and_stage_fields(tab, monkeypatch):
    from PyQt5 import QtWidgets
    path1 = _write_calib_json(Lsd=111.0)
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (path1, "")))
    first = tab._active_panel()
    first._load_calib_file()
    first._ome_first_deg.setValue(5.0)

    path2 = _write_calib_json(Lsd=222.0)
    second = tab._add_panel()
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (path2, "")))
    second._load_calib_file()

    tab._proj_ed.setText("/tmp/some_solve_cell_project")
    tab._low_count.setValue(12.5)
    tab._sector_candidates_ed.setText("8,24")

    state = tab.get_state()

    restored = type(tab)()
    restored.set_state(state)

    assert restored._panel_tabs.count() == 2
    ids = sorted(tab._panels.keys())
    restored_ids = sorted(restored._panels.keys())
    assert restored_ids == ids
    restored_first = restored._panels[ids[0]]
    restored_second = restored._panels[ids[1]]
    assert restored_first.has_calibration() and restored_second.has_calibration()
    assert restored_first.geometry_cfg()["Lsd"] == pytest.approx(111.0)
    assert restored_second.geometry_cfg()["Lsd"] == pytest.approx(222.0)
    assert restored_first._ome_first_deg.value() == pytest.approx(5.0)
    assert restored._proj_ed.text() == "/tmp/some_solve_cell_project"
    assert restored._low_count.value() == pytest.approx(12.5)
    assert restored._sector_candidates_cfg() == (8, 24)
    # `second` was the active panel when get_state() ran -- restore must
    # reproduce that, not default back to the first tab.
    assert restored._panel_tabs.currentWidget() is restored_second


def test_set_state_with_no_panels_key_leaves_default_panel_alone(tab):
    tab.set_state({})
    assert tab._panel_tabs.count() == 1
    tab.set_state({"fields": {"low_count": 3.0}})
    assert tab._low_count.value() == pytest.approx(3.0)
    assert tab._panel_tabs.count() == 1


# ── Detector tab: renamed stage + zoom preserved across stage switch ────

def test_detector_stage_background_label_says_calculated_background(tab):
    items = [tab._det_stage.itemText(i) for i in range(tab._det_stage.count())]
    assert "Calculated background (after Ingest)" in items
    assert not any("Background-subtracted" in t for t in items)


def test_detector_max_projection_stage_shows_spot_absent_from_single_frame(tab):
    """A spot only present on one frame must still render on the max-
    projection stage even when the captured single-frame preview has none --
    the real failure mode reported against Ge-oP32 c1 data, where the
    single-frame view read as "no background computed"."""
    import numpy as np

    single = np.zeros((8, 8), dtype=np.float32)
    projection = np.zeros((8, 8), dtype=np.float32)
    projection[3, 3] = 500.0
    tab._ingest_preview = {
        "frame_index": 3,
        "background_subtracted": single,
        "background_subtracted_max_projection": projection,
        "mask": np.zeros((8, 8), dtype=bool),
    }
    items = [tab._det_stage.itemText(i) for i in range(tab._det_stage.count())]
    max_proj_idx = next(i for i, t in enumerate(items) if "max over rotation" in t)

    seen = {}

    def fake_set_raw_frame(frame, im_trans, **kw):
        seen["frame"] = frame
        return frame

    tab._det_view.set_raw_frame = fake_set_raw_frame
    tab._det_stage.setCurrentIndex(max_proj_idx)
    assert seen["frame"][3, 3] == 500.0
    assert "max over every kept" in tab._det_frame_lbl.text()


def test_set_detector_image_preserves_zoom_across_stage_switch_same_shape(tab):
    import numpy as np
    calls = []

    def fake_set_raw_frame(frame, im_trans, *, autorange=True, reset_levels=True):
        calls.append({"autorange": autorange, "reset_levels": reset_levels})
        return frame

    tab._det_view.set_raw_frame = fake_set_raw_frame
    frame = np.zeros((10, 10), dtype=np.float32)

    tab._set_detector_image(frame, 0)   # first draw of this shape
    tab._set_detector_image(frame, 1)   # same shape, different stage
    tab._set_detector_image(frame, 2)   # same shape, yet another stage
    assert calls[0]["autorange"] is True
    assert calls[1]["autorange"] is False   # zoom must not reset on a stage switch
    assert calls[1]["reset_levels"] is True  # color window still refreshes per stage
    assert calls[2]["autorange"] is False

    bigger = np.zeros((20, 20), dtype=np.float32)
    tab._set_detector_image(bigger, 2)
    assert calls[3]["autorange"] is True   # a genuinely new detector size does reset zoom


# ── reciprocal-space map: red origin cross ──────────────────────────────

def test_recip_axes_always_show_a_red_origin_cross(tab):
    assert len(tab._recip_ax.collections) == 1
    fc = tab._recip_ax.collections[0].get_facecolor()
    assert fc[0][0] > 0.9 and fc[0][1] < 0.1 and fc[0][2] < 0.1


def test_update_recip_plot_keeps_origin_marker_alongside_data(tab):
    import numpy as np
    x, y, z = np.array([0.1]), np.array([0.2]), np.array([0.3])
    tab._update_recip_plot(all_xyz=(x, y, z))
    assert len(tab._recip_ax.collections) == 2
