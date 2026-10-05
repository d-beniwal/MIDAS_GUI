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


def _make_panel_ready(panel, monkeypatch, lsd=1000.0):
    """Fakes 'this panel has calibration + raw data loaded + nothing
    pending' without a real DataLoaderPanel data source -- same technique
    test_live_stream.py's own data_source_kind tests use."""
    from PyQt5 import QtWidgets
    path = _write_calib_json(Lsd=lsd)
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (path, "")))
    panel._load_calib_file()
    monkeypatch.setattr(panel.loader, "data_source_kind", lambda: "loaded")
    monkeypatch.setattr(panel.loader, "has_pending_fields", lambda: [])
    monkeypatch.setattr(panel.loader, "full_stack", lambda: None)
    monkeypatch.setattr(panel.loader, "composite_mask", lambda: None)
    monkeypatch.setattr(panel.loader, "frame_index", lambda: 0)


class _CapturingWorker:
    """Records the (stage, cfg) a tab tries to launch a SolveCellWorker
    with, without starting a real QThread -- these tests only check
    dispatch/cfg-building logic, not the pipeline itself (covered in
    test_solve_cell_pipeline.py)."""
    instances: list = []

    def __init__(self, stage, cfg, parent=None):
        self.stage = stage
        self.cfg = cfg
        self.log_line = _Signal()
        self.finished = _Signal()
        self.failed = _Signal()
        _CapturingWorker.instances.append(self)

    def start(self):
        pass

    def isRunning(self):
        return False


class _Signal:
    """Bare .connect()-only stand-in -- _CapturingWorker never emits, so
    nothing needs to actually fire."""
    def connect(self, *_a, **_k):
        pass


def test_run_ingest_pools_every_ready_panel(tab, monkeypatch):
    from midas_gui import tab_solve_cell
    first = tab._active_panel()
    _make_panel_ready(first, monkeypatch, lsd=111.0)
    second = tab._add_panel()
    _make_panel_ready(second, monkeypatch, lsd=222.0)
    first_id, second_id = sorted(tab._panels.keys())

    _CapturingWorker.instances.clear()
    monkeypatch.setattr(tab_solve_cell, "SolveCellWorker", _CapturingWorker)
    tab._run_ingest()

    assert len(_CapturingWorker.instances) == 1
    worker = _CapturingWorker.instances[0]
    assert worker.stage == "ingest_pooled"
    panel_ids = sorted(p["panel_id"] for p in worker.cfg["panels"])
    assert panel_ids == [first_id, second_id]
    assert worker.cfg["pooled_dir"] is None   # no project folder set


def test_run_ingest_skips_a_not_ready_panel_and_uses_single_stage(tab, monkeypatch):
    from midas_gui import tab_solve_cell
    first = tab._active_panel()
    _make_panel_ready(first, monkeypatch)
    tab._add_panel()   # second panel left unconfigured -- no calibration, no data
    tab._panel_tabs.setCurrentWidget(first)   # _add_panel() switches the active tab to it

    _CapturingWorker.instances.clear()
    monkeypatch.setattr(tab_solve_cell, "SolveCellWorker", _CapturingWorker)
    tab._run_ingest()

    assert len(_CapturingWorker.instances) == 1
    worker = _CapturingWorker.instances[0]
    assert worker.stage == "ingest"
    assert "panels" not in worker.cfg
    assert "not ready" in tab._log.toPlainText()


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


# ── Detector tab: spot overlay ───────────────────────────────────────────

def test_spot_scatter_item_is_added_to_the_detector_view(tab):
    import pyqtgraph as pg
    assert isinstance(tab._spot_scatter, pg.ScatterPlotItem)
    assert tab._spot_scatter.scene() is not None


def test_update_spot_overlay_shows_only_spots_on_the_matching_loaded_frame(tab):
    import pandas as pd
    tab._spots_df = pd.DataFrame({
        "row": [1.0, 2.0, 3.0], "col": [10.0, 20.0, 30.0],
        "loaded_frame_idx": [2, 2, 5],
    })
    tab._update_spot_overlay(0, 2)
    assert len(tab._spot_scatter.data) == 2
    assert sorted(tab._spot_scatter.data["x"]) == [10.0, 20.0]

    tab._update_spot_overlay(0, 5)
    assert len(tab._spot_scatter.data) == 1
    assert tab._spot_scatter.data["x"][0] == 30.0

    tab._update_spot_overlay(1, 99)   # no spot at this frame
    assert len(tab._spot_scatter.data) == 0


def test_update_spot_overlay_shows_every_spot_on_max_projection_stage(tab):
    import pandas as pd
    tab._spots_df = pd.DataFrame({
        "row": [1.0, 2.0], "col": [10.0, 20.0], "loaded_frame_idx": [2, 5],
    })
    tab._update_spot_overlay(3, None)
    assert len(tab._spot_scatter.data) == 2


def test_update_spot_overlay_shows_nothing_on_mask_stage(tab):
    import pandas as pd
    tab._spots_df = pd.DataFrame({
        "row": [1.0], "col": [10.0], "loaded_frame_idx": [2],
    })
    tab._update_spot_overlay(4, 2)
    assert len(tab._spot_scatter.data) == 0


def test_update_spot_overlay_handles_no_ingest_result_without_raising(tab):
    assert tab._spots_df is None
    tab._update_spot_overlay(0, 3)   # must not raise
    assert len(tab._spot_scatter.data) == 0


def test_update_spot_overlay_filters_to_the_active_panel_when_pooled(tab):
    """row/col are per-panel pixel coordinates -- a pooled spots_df must only
    ever show the ACTIVE panel's own spots on its detector frame, never
    another panel's, even if they happen to share a loaded_frame_idx."""
    import pandas as pd
    second = tab._add_panel()
    first_id, second_id = sorted(tab._panels.keys())
    tab._spots_df = pd.DataFrame({
        "panel_id": [first_id, second_id],
        "row": [1.0, 9.0], "col": [10.0, 90.0], "loaded_frame_idx": [2, 2],
    })
    tab._panel_tabs.setCurrentWidget(tab._panels[first_id])
    tab._update_spot_overlay(0, 2)
    assert len(tab._spot_scatter.data) == 1
    assert tab._spot_scatter.data["x"][0] == 10.0

    tab._panel_tabs.setCurrentWidget(second)
    tab._update_spot_overlay(0, 2)
    assert len(tab._spot_scatter.data) == 1
    assert tab._spot_scatter.data["x"][0] == 90.0


# ── multi-panel pooled ingest: completion handler + provenance ──────────

def test_on_ingest_pooled_done_updates_combined_state(tab):
    import numpy as np
    import pandas as pd
    first = tab._active_panel()
    second = tab._add_panel()
    first_id, second_id = sorted(tab._panels.keys())

    spots = pd.DataFrame({
        "panel_id": [first_id, first_id, second_id],
        "qsample_x": [0.1, 0.2, 0.3], "qsample_y": [0.0, 0.1, 0.2],
        "qsample_z": [0.0, 0.0, 0.1], "loaded_frame_idx": [1, 2, 3],
    })
    preview1 = {"mask": np.zeros((4, 4), dtype=bool)}
    preview2 = {"mask": np.ones((4, 4), dtype=bool)}
    result = {
        "spots_df": spots,
        "summary": {"n_panels": 2, "panel_ids": [first_id, second_id], "n_spots_total": 3},
        "per_panel": {
            first_id: {"summary": {"n_spots": 2}, "preview": preview1},
            second_id: {"summary": {"n_spots": 1}, "preview": preview2},
        },
    }
    tab._on_ingest_pooled_done(result)

    assert tab._spots_df is spots
    assert "panel 2" in tab._ingest_status.text() or str(second_id) in tab._ingest_status.text()
    assert tab._diamond_btn.isEnabled()
    assert tab._ingest_preview_by_panel[first_id] is preview1
    assert tab._ingest_preview_by_panel[second_id] is preview2
    assert "pooled" in tab._log.toPlainText().lower()


# ── Project logging (FAIR provenance) ────────────────────────────────────

def test_log_ingest_to_project_is_a_noop_without_a_project_open(tab):
    import pandas as pd
    tab._spots_df = pd.DataFrame({"row": [1.0], "col": [2.0], "loaded_frame_idx": [0]})
    tab._log_ingest_to_project({"summary": {"n_spots": 1}, "preview": {}})   # must not raise
    assert "Logged ingest" not in tab._log.toPlainText()


def test_log_ingest_to_project_writes_a_solve_cell_attempt(tab, monkeypatch):
    import h5py
    import pandas as pd
    from PyQt5 import QtWidgets
    from midas_gui import project

    calib_path = _write_calib_json()
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (calib_path, "")))
    tab._active_panel()._load_calib_file()

    proj_dir = Path(tempfile.mkdtemp(prefix="mg_solvecell_project_"))
    _SCRATCH.append(proj_dir)
    proj_path = str(proj_dir / "proj.h5")
    project.create_project(proj_path)
    ctx = project.ProjectContext()
    ctx.path = proj_path
    tab.set_project_context(ctx)

    tab._spots_df = pd.DataFrame({"row": [1.0], "col": [2.0], "loaded_frame_idx": [3]})
    tab._log_ingest_to_project({"summary": {"n_spots": 1}, "preview": {"mask": None}})

    with h5py.File(proj_path, "r") as f:
        assert "analysis/solve_cell" in f
        panel_key = next(iter(f["analysis/solve_cell"].keys()))
        att = f[f"analysis/solve_cell/{panel_key}/attempt_0001"]
        assert att.attrs["n_spots"] == 1
    assert "Logged ingest to project" in tab._log.toPlainText()


def test_log_pooled_ingest_to_project_writes_one_attempt_per_panel(tab, monkeypatch):
    import h5py
    import pandas as pd
    from PyQt5 import QtWidgets
    from midas_gui import project
    from midas_gui.solve_cell import pipeline as solve_pipeline

    first = tab._active_panel()
    calib1 = _write_calib_json(Lsd=111.0)
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (calib1, "")))
    first._load_calib_file()
    second = tab._add_panel()
    calib2 = _write_calib_json(Lsd=222.0)
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (calib2, "")))
    second._load_calib_file()
    first_id, second_id = sorted(tab._panels.keys())

    proj_dir = Path(tempfile.mkdtemp(prefix="mg_solvecell_project_pooled_"))
    _SCRATCH.append(proj_dir)
    proj_path = str(proj_dir / "proj.h5")
    project.create_project(proj_path)
    ctx = project.ProjectContext()
    ctx.path = proj_path
    tab.set_project_context(ctx)

    result = {
        "per_panel": {
            first_id: {"summary": {"n_spots": 2}, "preview": {"mask": None},
                       "spots_df": pd.DataFrame({"row": [1.0], "col": [2.0]})},
            second_id: {"summary": {"n_spots": 5}, "preview": {"mask": None},
                       "spots_df": pd.DataFrame({"row": [3.0], "col": [4.0]})},
        }
    }
    tab._log_pooled_ingest_to_project(result)

    with h5py.File(proj_path, "r") as f:
        key1 = solve_pipeline.panel_dir_name(first_id)
        key2 = solve_pipeline.panel_dir_name(second_id)
        assert f[f"analysis/solve_cell/{key1}/attempt_0001"].attrs["n_spots"] == 1
        assert f[f"analysis/solve_cell/{key2}/attempt_0001"].attrs["n_spots"] == 1
    assert tab._log.toPlainText().count("Logged ingest to project") == 2


# ── multi-domain: "Index remaining spots" (leftover re-indexing) ──────────

def _fake_candidate_df(n=10, panel_ids=None):
    import numpy as np
    import pandas as pd
    data = {
        "qsample_x": np.linspace(0.1, 1.0, n), "qsample_y": np.linspace(0.2, 1.1, n),
        "qsample_z": np.linspace(0.3, 1.2, n),
    }
    if panel_ids is not None:
        data["panel_id"] = panel_ids
    return pd.DataFrame(data)


def _fake_refine_result(a=5.43):
    return {
        "cell": [a, a, a, 90.0, 90.0, 90.0], "cell_sigma": [0.01] * 6,
        "conventional_cell": [a, a, a], "conventional_system": "cubic",
        "holohedry_system": "cubic", "holohedry_order": 48,
        "rms_drlv": 0.02, "n_reflections": 5,
    }


def test_leftover_method_combo_toggles_known_cell_params_visibility(tab):
    assert tab._kc_params.isHidden()
    tab._leftover_method.setCurrentIndex(1)
    assert not tab._kc_params.isHidden()
    tab._leftover_method.setCurrentIndex(0)
    assert tab._kc_params.isHidden()


def test_leftover_status_and_button_disabled_with_no_domains(tab):
    tab._candidate_df = _fake_candidate_df(10)
    tab._update_leftover_status()
    assert "10 spot" in tab._leftover_status.text()
    assert not tab._leftover_btn.isEnabled()   # no domain claimed anything yet


def test_add_domain_excludes_claimed_rows_from_leftover(tab):
    df = _fake_candidate_df(10)
    tab._candidate_df = df
    xyz = (df["qsample_x"].to_numpy()[:4], df["qsample_y"].to_numpy()[:4], df["qsample_z"].to_numpy()[:4])
    tab._add_domain("free (ab-initio)", _fake_refine_result(), df.index[:4], xyz)

    assert tab._domain_list.count() == 1
    leftover = tab._leftover_df()
    assert len(leftover) == 6
    assert tab._leftover_btn.isEnabled()


def test_two_domains_never_double_claim_the_same_row(tab):
    df = _fake_candidate_df(10)
    tab._candidate_df = df
    xyz = (df["qsample_x"].to_numpy(), df["qsample_y"].to_numpy(), df["qsample_z"].to_numpy())
    tab._add_domain("free (ab-initio)", _fake_refine_result(), df.index[:4], (xyz[0][:4], xyz[1][:4], xyz[2][:4]))
    leftover_before = tab._leftover_df()
    assert len(leftover_before) == 6

    # Second domain only claims rows out of what was actually left over.
    claim2 = leftover_before.index[:3]
    tab._add_domain("known structure", _fake_refine_result(5.0),
                    claim2, (xyz[0][:3], xyz[1][:3], xyz[2][:3]))
    assert tab._domain_list.count() == 2
    leftover_after = tab._leftover_df()
    assert len(leftover_after) == 3
    assert not set(claim2).intersection(set(leftover_after.index))


def test_on_refine_done_replaces_round1_domain_instead_of_duplicating(tab):
    class _FakeAbInitio:
        def __init__(self, mask):
            self.indexed_mask = mask

    df = _fake_candidate_df(10)
    tab._candidate_df = df
    tab._g_2pi = df[["qsample_x", "qsample_y", "qsample_z"]].to_numpy()

    import numpy as np
    mask1 = np.array([True] * 4 + [False] * 6)
    tab._ab_initio_raw = _FakeAbInitio(mask1)
    tab._on_refine_done({"refine_result": _fake_refine_result(5.43)})
    assert tab._domain_list.count() == 1
    assert len(tab._leftover_df()) == 6

    # Re-running Refine (e.g. after re-running ab-initio with a different
    # result) must replace Domain 1, not append a second one.
    mask2 = np.array([True] * 6 + [False] * 4)
    tab._ab_initio_raw = _FakeAbInitio(mask2)
    tab._on_refine_done({"refine_result": _fake_refine_result(5.44)})
    assert tab._domain_list.count() == 1
    assert len(tab._leftover_df()) == 4


def test_on_leftover_known_cell_done_negative_result_adds_no_domain(tab):
    tab._leftover_pending_df = _fake_candidate_df(5)
    tab._on_leftover_known_cell_done({
        "success": False, "notes": ["no second domain found"],
        "diagnostics": {"best_n": 2, "null_mean": 1.5, "z_score": 0.4},
    })
    assert tab._domain_list.count() == 0
    assert "No new domain found" in tab._leftover_run_status.text()
    assert tab._leftover_btn.isEnabled()


def test_on_leftover_known_cell_done_success_adds_a_domain(tab):
    import numpy as np
    df = _fake_candidate_df(10)
    tab._candidate_df = df
    tab._leftover_pending_df = df
    tab._leftover_g_2pi = df[["qsample_x", "qsample_y", "qsample_z"]].to_numpy()
    mask = np.array([True] * 3 + [False] * 7)
    result = dict(_fake_refine_result(5.43), success=True, indexed_mask=mask, n_indexed=3)
    tab._on_leftover_known_cell_done(result)
    assert tab._domain_list.count() == 1
    assert len(tab._leftover_df()) == 7


def test_update_multidomain_plot_with_several_domains_does_not_raise(tab):
    df = _fake_candidate_df(10)
    tab._candidate_df = df
    xyz = (df["qsample_x"].to_numpy(), df["qsample_y"].to_numpy(), df["qsample_z"].to_numpy())
    tab._add_domain("free (ab-initio)", _fake_refine_result(5.43), df.index[:4],
                    (xyz[0][:4], xyz[1][:4], xyz[2][:4]))
    tab._add_domain("known structure", _fake_refine_result(5.0), df.index[4:7],
                    (xyz[0][4:7], xyz[1][4:7], xyz[2][4:7]))
    tab._update_multidomain_plot()   # must not raise with 2 domains + leftover


def test_on_domain_selected_populates_result_grid(tab):
    df = _fake_candidate_df(10)
    tab._candidate_df = df
    xyz = (df["qsample_x"].to_numpy()[:4], df["qsample_y"].to_numpy()[:4], df["qsample_z"].to_numpy()[:4])
    tab._add_domain("free (ab-initio)", _fake_refine_result(5.43), df.index[:4], xyz)
    tab._on_domain_selected(0)
    assert tab._param_grid.count() > 0


def test_run_index_remaining_warns_when_nothing_left_over(tab, monkeypatch):
    from PyQt5 import QtWidgets
    warned = {}
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        staticmethod(lambda *a: warned.setdefault("hit", True)))
    tab._candidate_df = None
    tab._run_index_remaining()
    assert warned.get("hit") is True
    assert tab._worker is None


def test_on_diamond_done_resets_stale_domain_state(tab):
    df = _fake_candidate_df(10)
    tab._candidate_df = df
    xyz = (df["qsample_x"].to_numpy()[:4], df["qsample_y"].to_numpy()[:4], df["qsample_z"].to_numpy()[:4])
    tab._add_domain("free (ab-initio)", _fake_refine_result(), df.index[:4], xyz)
    assert tab._domain_list.count() == 1

    new_candidate = _fake_candidate_df(6)
    tab._on_diamond_done({
        "candidate_df": new_candidate,
        "flagged_df": new_candidate.assign(is_diamond=False),
        "summary": {"n_diamond_flagged": 0, "n_spots": 6, "n_kept": 6},
    })
    assert tab._domain_list.count() == 0
    assert tab._claimed_index is None
    assert not tab._leftover_btn.isEnabled()


# ── reciprocal-space map: Panel/Crystal color modes, unindexed toggle, stats ──

def test_recip_mode_and_unindexed_checkbox_defaults(tab):
    assert [tab._recip_mode.itemText(i) for i in range(tab._recip_mode.count())] == ["Panel", "Crystal"]
    assert tab._recip_mode.currentIndex() == 0
    assert tab._recip_show_unindexed.isChecked() is True


def test_refresh_recip_view_does_not_raise_with_no_data(tab):
    tab._refresh_recip_view()
    tab._recip_mode.setCurrentIndex(1)
    tab._refresh_recip_view()
    assert tab._recip_stats.text() != ""


def test_panel_mode_single_panel_no_panel_id_column_buckets_as_single_panel(tab):
    tab._spots_df = _fake_candidate_df(4)   # no panel_id column
    tab._refresh_recip_view()
    assert "(single panel): 4 spots" in tab._recip_stats.text()
    assert "Total: 4 spots" in tab._recip_stats.text()


def test_panel_mode_pooled_two_panels_stats(tab):
    from midas_gui.solve_cell import pipeline as solve_pipeline
    tab._spots_df = _fake_candidate_df(5, panel_ids=[1, 1, 1, 2, 2])
    tab._refresh_recip_view()
    text = tab._recip_stats.text()
    assert f"{solve_pipeline.panel_dir_name(1)}: 3 spots" in text
    assert f"{solve_pipeline.panel_dir_name(2)}: 2 spots" in text
    assert "Total: 5 spots" in text


def test_panel_mode_hides_unindexed_when_checkbox_unchecked(tab):
    tab._spots_df = _fake_candidate_df(5, panel_ids=[1, 1, 1, 2, 2])
    tab._recip_show_unindexed.setChecked(False)
    text = tab._recip_stats.text()
    assert "Total: 0 spots" in text
    assert "(5 unindexed hidden)" in text


def test_crystal_mode_shows_diamond_domain_and_unindexed_lines(tab):
    flagged = _fake_candidate_df(10).assign(is_diamond=[True] * 3 + [False] * 7)
    candidate = flagged[~flagged["is_diamond"]].reset_index(drop=True)
    tab._flagged_df = flagged
    tab._candidate_df = candidate
    xyz = (candidate["qsample_x"].to_numpy()[:4], candidate["qsample_y"].to_numpy()[:4],
           candidate["qsample_z"].to_numpy()[:4])
    tab._add_domain("free (ab-initio)", _fake_refine_result(5.43), candidate.index[:4], xyz)
    tab._recip_mode.setCurrentIndex(1)
    tab._refresh_recip_view()
    text = tab._recip_stats.text()
    assert "Domain 1 (free (ab-initio)): 4 spots, a=5.43" in text
    assert "Diamond/gasket: 3 spots" in text
    assert "Unindexed: 3 spots" in text
    assert "hidden" not in text

    tab._recip_show_unindexed.setChecked(False)
    assert "Unindexed: 3 spots (hidden)" in tab._recip_stats.text()


def test_domain_with_panel_ids_buckets_with_its_panel_in_panel_mode(tab):
    from midas_gui.solve_cell import pipeline as solve_pipeline
    candidate = _fake_candidate_df(6, panel_ids=[1, 1, 1, 2, 2, 2])
    tab._candidate_df = candidate
    claimed_index = candidate.index[:3]   # all panel 1
    xyz = (candidate["qsample_x"].to_numpy()[:3], candidate["qsample_y"].to_numpy()[:3],
           candidate["qsample_z"].to_numpy()[:3])
    panel_ids = candidate.loc[claimed_index, "panel_id"].to_numpy()
    tab._add_domain("free (ab-initio)", _fake_refine_result(), claimed_index, xyz, panel_ids)
    tab._refresh_recip_view()   # Panel mode is the default
    text = tab._recip_stats.text()
    assert f"{solve_pipeline.panel_dir_name(1)}: 3 spots" in text
    assert f"{solve_pipeline.panel_dir_name(2)}: 3 spots" in text   # 3 leftover, still panel 2
    assert "Total: 6 spots" in text


def test_mode_switch_and_checkbox_toggle_trigger_refresh(tab, monkeypatch):
    calls = []
    monkeypatch.setattr(tab, "_refresh_recip_view", lambda: calls.append(1))
    tab._recip_mode.setCurrentIndex(1)
    tab._recip_show_unindexed.setChecked(False)
    assert len(calls) == 2


def test_update_multidomain_plot_still_delegates_to_refresh(tab, monkeypatch):
    calls = []
    monkeypatch.setattr(tab, "_refresh_recip_view", lambda: calls.append(1))
    tab._update_multidomain_plot()
    assert calls == [1]
