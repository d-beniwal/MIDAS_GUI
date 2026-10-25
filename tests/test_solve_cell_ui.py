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
    t = SolveCellTab()
    yield t
    # The Reciprocal view is VTK-backed (pyvistaqt QtInteractor): under
    # QT_QPA_PLATFORM=offscreen there is no real OpenGL context, and if
    # Python's interpreter-exit GC is left to destroy a VTK render window
    # instead of Qt's own deleteLater() + event loop, the native cleanup
    # segfaults -- caught by pytest-forked as a CRASHED (not failed) test
    # even when the test body itself passed. Explicit
    # deleteLater()+processEvents() here lets Qt tear the widget down
    # properly before this forked test process exits.
    t.deleteLater()
    app.processEvents()


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


def _write_h5_stack_with_omega(omega, *, omega_field="entry/instrument/sample_stage/omega",
                                data_field="exchange/data") -> str:
    """A minimal HDF5 stack (one tiny frame per *omega* entry) plus a 1-D
    omega dataset at *omega_field* -- enough for the omega-field combo and
    ``detect_omega_frame_range`` to exercise against, without depending on a
    real beamline file."""
    import h5py
    import numpy as np
    tmpdir = Path(tempfile.mkdtemp(prefix="mg_solvecell_omega_"))
    _SCRATCH.append(tmpdir)
    p = tmpdir / "stack.h5"
    with h5py.File(str(p), "w") as f:
        n = len(omega)
        f.create_dataset(data_field, data=np.zeros((n, 2, 2), dtype=np.uint16))
        f.create_dataset(omega_field, data=np.asarray(omega, dtype=np.float64))
    return str(p)


def _write_calib_json(**overrides) -> str:
    d = {
        "wavelength_A": 0.17298, "pxY": 150.0, "pxZ": 150.0,
        "NrPixelsY": 2880, "NrPixelsZ": 2880,
        "Lsd": 1000473.93, "BC_y": 1430.433, "BC_z": 1342.486,
        "tx": 0.0, "ty": -0.4718, "tz": -0.2205,
        "im_trans": [],   # declared-but-empty: "no transform", not "file doesn't say"
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


def _make_panel_ready(panel, monkeypatch, lsd=1000.0, **calib_over):
    """Fakes 'this panel has calibration + raw data loaded + nothing
    pending' without a real DataLoaderPanel data source -- same technique
    test_live_stream.py's own data_source_kind tests use."""
    from PyQt5 import QtWidgets
    path = _write_calib_json(Lsd=lsd, **calib_over)
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
        self.step = _Signal()
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
    assert panel._calib_link.isEnabled() and panel._calib_link.text().endswith(".json")


def test_load_calib_file_shows_but_does_not_use_nonzero_tx(tab, monkeypatch):
    from PyQt5 import QtWidgets
    path = _write_calib_json(tx=1.5)
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (path, "")))
    panel = tab._active_panel()
    panel._load_calib_file()
    assert any("not used" in v for k, v in panel.calibration_summary() if k == "note")
    # tx is still fixed at 0 for the Phase 1 pipeline regardless of display.
    assert "omega_ref_frame_idx" in panel.geometry_cfg()
    assert "tx" not in panel.geometry_cfg()


# ── reciprocal-space map (3-D) / Detector view ──────────────────────────

def test_view_tabs_have_recip_map_and_detector(tab):
    from midas_gui.pyvista_views import PyVistaScatter3DView
    titles = [tab._view_tabs.tabText(i) for i in range(tab._view_tabs.count())]
    assert "Reciprocal space map" in titles
    assert "Detector" in titles
    assert isinstance(tab._recip_view, PyVistaScatter3DView)


def test_update_recip_plot_accepts_xyz_tuples_without_raising(tab):
    import numpy as np
    x, y, z = np.array([0.1, 0.2]), np.array([0.3, 0.4]), np.array([0.5, 0.6])
    tab._recip_view.set_groups([((x, y, z), "#4da3ff", "Ab-initio indexed", [])])
    tab._recip_view.set_groups([])   # all-empty (e.g. zero spots) must not raise either


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

def test_get_state_set_state_round_trips_panels_and_stage_fields(tab, app, monkeypatch):
    from PyQt5 import QtWidgets
    path1 = _write_calib_json(Lsd=111.0)
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (path1, "")))
    first = tab._active_panel()
    first._load_calib_file()
    first._ome_start.setValue(5.0)

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
    try:
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
        assert restored_first._ome_start.value() == pytest.approx(5.0)
        assert restored._proj_ed.text() == "/tmp/some_solve_cell_project"
        assert restored._low_count.value() == pytest.approx(12.5)
        assert restored._sector_candidates_cfg() == (8, 24)
        # `second` was the active panel when get_state() ran -- restore must
        # reproduce that, not default back to the first tab.
        assert restored._panel_tabs.currentWidget() is restored_second
    finally:
        # Own PyVista views too -- must be torn down via Qt, not interpreter-exit
        # GC (see the `tab` fixture above and .context/STATE.md).
        restored.deleteLater()
        app.processEvents()


def test_set_state_with_no_panels_key_leaves_default_panel_alone(tab):
    tab.set_state({})
    assert tab._panel_tabs.count() == 1
    tab.set_state({"fields": {"low_count": 3.0}})
    assert tab._low_count.value() == pytest.approx(3.0)
    assert tab._panel_tabs.count() == 1


# ── Detector tab: renamed stage + zoom preserved across stage switch ────

def test_detector_stage_background_label_says_background_corrected(tab):
    items = [tab._det_stage.itemText(i) for i in range(tab._det_stage.count())]
    assert "Background corrected (after ingest)" in items
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
    max_proj_idx = next(i for i, t in enumerate(items) if "Max projected" in t)

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
    from midas_gui.pyvista_views import _ORIGIN_MARKER_COLOR
    assert tab._recip_view._origin_actor is not None
    assert tab._recip_view._origin_actor.prop.color.hex_rgb == _ORIGIN_MARKER_COLOR


def test_update_recip_plot_keeps_origin_marker_alongside_data(tab):
    import numpy as np
    x, y, z = np.array([0.1]), np.array([0.2]), np.array([0.3])
    tab._recip_view.set_groups([((x, y, z), "#4da3ff", "Ab-initio indexed", [])])
    assert tab._recip_view._origin_actor is not None
    assert len(tab._recip_view._group_actors) == 1


# ── reciprocal-space map: spot size controls ─────────────────────────────

def test_recip_point_size_control_defaults_to_the_view_widgets_own_default(tab):
    from midas_gui.pyvista_views import _DEFAULT_POINT_SIZE, SIZE_MODE_EQUAL
    assert tab._recip_point_size.value() == _DEFAULT_POINT_SIZE
    assert tab._recip_size_mode.currentIndex() == 0
    assert tab._recip_view._size_mode == SIZE_MODE_EQUAL


def test_recip_point_size_spinbox_resizes_rendered_spots(tab):
    _linked_spots(tab)
    tab._refresh_recip_view()
    assert tab._recip_view._group_actors[0].prop.point_size == tab._recip_point_size.value()
    tab._recip_point_size.setValue(30)
    assert tab._recip_view._group_actors[0].prop.point_size == 30


def test_recip_size_mode_intensity_scales_spots_by_integrated_field(tab):
    import numpy as np
    x, y, z = np.array([0.1, 0.2]), np.array([0.3, 0.4]), np.array([0.5, 0.6])
    rows = [{"spot_id": 1, "integrated": 10.0}, {"spot_id": 2, "integrated": 1000.0}]
    view = tab._recip_view
    view.set_groups([((x, y, z), "#4da3ff", "Ab-initio indexed", rows)],
                     point_size=30, size_mode="intensity")
    actor = view._group_actors[0]
    assert actor.mapper.scale_array == "point_radius"
    radii = actor.mapper.dataset.point_data["point_radius"]
    assert radii[1] == 30.0   # the brightest visible spot renders at the full requested size
    assert radii[0] < radii[1]   # a dimmer spot renders smaller, but not invisible
    assert radii[0] > 0

    # Switching back to equal size must not leave a stale scale array behind.
    view.set_groups([((x, y, z), "#4da3ff", "Ab-initio indexed", rows)], size_mode="equal")
    actor = view._group_actors[0]
    assert "point_radius" not in actor.mapper.dataset.point_data
    assert actor.prop.point_size == 30   # point_size itself is sticky across mode changes


def test_recip_size_mode_intensity_falls_back_to_equal_size_without_integrated_data(tab):
    """Older fixtures/spots_df without an "integrated" column (e.g. this
    test file's own ``_fake_candidate_df``) must not crash intensity mode --
    they just render at the uniform point size, same as equal mode."""
    _linked_spots(tab)
    tab._recip_size_mode.setCurrentIndex(1)
    tab._refresh_recip_view()   # must not raise
    actor = tab._recip_view._group_actors[0]
    assert "point_radius" not in actor.mapper.dataset.point_data


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


# ── live ingest progress (on_step) ───────────────────────────────────────

def test_on_ingest_step_updates_active_panel_preview_and_status(tab):
    import numpy as np
    pid = tab._active_panel_id()
    partial = {"frame_index": None, "loaded_frame_index": None,
               "background_subtracted": None,
               "background_subtracted_max_projection": None,
               "mask": np.zeros((4, 4), dtype=bool)}
    tab._on_ingest_step("mask", {"preview": partial, "stats": {"n_masked": 3}})

    assert tab._ingest_preview is partial
    assert tab._ingest_preview_by_panel[pid] is partial
    assert "mask" in tab._ingest_status.text()
    assert "n_masked=3" in tab._ingest_status.text()


def test_on_ingest_step_routes_pooled_payload_to_its_own_panel_and_ignores_others(tab):
    """A pooled run's steps carry panel_id -- only the panel currently shown
    in the Detector tab should refresh; another panel's in-flight step must
    still be recorded (for when its tab is later selected) without touching
    the currently active panel's own preview."""
    import numpy as np
    first_id = tab._active_panel_id()
    second = tab._add_panel()
    second_id = sorted(tab._panels.keys())[-1]
    assert second_id != first_id

    own_preview = {"mask": np.zeros((2, 2), dtype=bool)}
    tab._on_ingest_step("mask", {"preview": own_preview, "stats": {}, "panel_id": first_id})
    assert tab._ingest_preview_by_panel[first_id] is own_preview
    assert f"panel {first_id}" in tab._ingest_status.text()

    other_preview = {"mask": np.ones((2, 2), dtype=bool)}
    tab._on_ingest_step("mask", {"preview": other_preview, "stats": {}, "panel_id": second_id})
    assert tab._ingest_preview_by_panel[second_id] is other_preview
    # Still showing panel 1's own detector view -- its own preview is untouched.
    assert tab._ingest_preview_by_panel[first_id] is own_preview


def test_refresh_detector_preview_handles_fields_not_yet_populated(tab):
    """Mid-run, keys fill in progressively (see pipeline._stage_ingest) --
    selecting a stage whose field hasn't arrived yet must not raise, and
    should say so rather than silently show stale/blank data."""
    pid = tab._active_panel_id()
    tab._ingest_preview_by_panel[pid] = {
        "frame_index": None, "loaded_frame_index": None,
        "background_subtracted": None,
        "background_subtracted_max_projection": None,
        "mask": None,
    }
    tab._det_stage.setCurrentIndex(4)   # "Mask"
    tab._refresh_detector_preview()   # must not raise
    assert "not available yet" in tab._det_frame_lbl.text()


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

    assert tab._spots_df is not spots
    assert tab._spots_df["spot_id"].tolist() == list(range(len(spots)))
    assert tab._spots_df[spots.columns].equals(spots)
    assert "panel 2" in tab._ingest_status.text() or str(second_id) in tab._ingest_status.text()
    assert tab._diamond_btn.isEnabled()
    assert tab._ingest_preview_by_panel[first_id] is preview1
    assert tab._ingest_preview_by_panel[second_id] is preview2
    assert "pooled" in tab._log.toPlainText().lower()


# ── Project record: single latest analysis in the project file ───────────

def _open_project(tab, name="proj.h5"):
    from midas_gui import project
    proj_dir = Path(tempfile.mkdtemp(prefix="mg_solvecell_project_"))
    _SCRATCH.append(proj_dir)
    path = str(proj_dir / name)
    project.create_project(path)
    ctx = project.ProjectContext()
    ctx.path = path
    tab.set_project_context(ctx)
    return path


def _fake_spots(n=12, panel_id=1):
    import numpy as np
    import pandas as pd
    rng = np.random.default_rng(0)
    return pd.DataFrame({
        "panel_id": panel_id, "row": rng.uniform(0, 100, n), "col": rng.uniform(0, 100, n),
        "frame": rng.uniform(0, 5, n), "loaded_frame_idx": np.arange(n) % 5,
        "omega_deg": np.linspace(0, 10, n), "two_theta_deg": np.linspace(3, 9, n),
        "qsample_x": np.linspace(0.1, 1.0, n), "qsample_y": np.linspace(0.2, 1.1, n),
        "qsample_z": np.linspace(0.3, 1.2, n), "spot_id": np.arange(n),
    })


def _fake_ingest_result(spots):
    import numpy as np
    mask = np.zeros((6, 6), dtype=bool)
    mask[0, 0] = True
    return {
        "spots_df": spots, "summary": {"n_spots": len(spots), "n_sectors": 8, "warnings": [],
                                        "counts": {"a": np.int64(3)}},
        "preview": {"mask": mask, "frame_index": 2, "loaded_frame_index": 4,
                    "background_subtracted": np.ones((6, 6), dtype=np.float32),
                    "background_subtracted_max_projection": np.full((6, 6), 2.0, dtype=np.float32)},
    }


def _fake_diamond_result(spots, n_flagged=2):
    flagged = spots.copy()
    flagged["delta_to_diamond_deg"] = 1.0
    flagged["is_diamond"] = [i < n_flagged for i in range(len(flagged))]
    cand = flagged[~flagged["is_diamond"]].reset_index(drop=True)
    return {"flagged_df": flagged, "candidate_df": cand,
            "summary": {"n_spots": len(flagged), "n_diamond_flagged": n_flagged,
                        "n_kept": len(cand), "z_score": float("nan")}}


def _fake_ab_initio_result(n_cand, n_idx=6):
    import numpy as np
    from types import SimpleNamespace
    mask = np.array([True] * n_idx + [False] * (n_cand - n_idx))
    raw = SimpleNamespace(
        success=True, cell=(5.43,) * 3 + (90.0,) * 3, cell_sigma=(0.01,) * 6,
        UB=np.eye(3), UBI=np.eye(3), hkl=np.arange(n_idx * 3).reshape(n_idx, 3),
        indexed_mask=mask, indexed_fraction=n_idx / n_cand, n_indexed=n_idx,
        n_reflections=n_cand, candidates=[], diagnostics={"k": np.float64(1.5)}, notes=["ok"])
    return {"ab_initio_result": {"success": True, "n_indexed": n_idx, "n_reflections": n_cand,
                                  "indexed_fraction": n_idx / n_cand, "notes": ["ok"]},
            "ab_initio_raw": raw, "g_2pi": np.zeros((n_cand, 3)), "sigma_g": 0.05}


def _run_fake_analysis(tab, monkeypatch):
    """Drive the tab through ingest -> diamond -> ab-initio -> refine using
    the same completion handlers a real run ends in."""
    spots = _fake_spots()
    _make_panel_ready(tab._active_panel(), monkeypatch)
    tab._ingest_source_panel_id = tab._active_panel_id()
    tab._on_ingest_done(_fake_ingest_result(spots))
    dia = _fake_diamond_result(tab._spots_df)
    tab._on_diamond_done(dia)
    ab = _fake_ab_initio_result(len(tab._candidate_df))
    ab["g_2pi"] = tab._candidate_df[["qsample_x", "qsample_y", "qsample_z"]].to_numpy()
    tab._on_ab_initio_done(ab)
    refine = dict(_fake_refine_result(), reflection_fit={"hkl": [[1, 0, 0]], "q_residual": [0.01]})
    tab._on_refine_done({"refine_result": refine})
    return spots


def test_ingest_without_a_project_open_writes_nothing_and_does_not_raise(tab, monkeypatch):
    _run_fake_analysis(tab, monkeypatch)
    assert tab._project_path() is None
    assert "Solve Cell record updated" not in tab._log.toPlainText()


def test_stages_write_a_single_latest_record_into_the_project(tab, monkeypatch):
    import h5py
    from midas_gui import project
    path = _open_project(tab)
    _run_fake_analysis(tab, monkeypatch)

    with h5py.File(path, "r") as f:
        root = f["analysis/solve_cell"]
        assert set(root.keys()) == {"metadata", "ingest", "diamond", "index"}
        assert root.attrs["last_stage"] == "index"
        assert "attempt_0001" not in root          # no attempt history

    rec = project.read_solve_cell(path)
    assert len(rec["ingest"]["spots_df"]) == 12
    assert len(rec["diamond"]["flagged_df"]) == 12
    assert len(rec["index"]["domains"]) == 1
    meta = rec["ingest"]["meta"]
    (prec,) = meta["panels"].values()
    # calibration + references needed to repeat the calculation
    assert prec["calibration"]["geometry"]["Lsd"] == pytest.approx(1000.0)
    assert prec["calibration"]["file_text"]
    assert prec["calibration"]["path_hash"]["sha256"]
    assert prec["geometry_cfg"]["omega_step_deg"] == 0.0
    assert "loader" in prec["state"]
    assert rec["ingest"]["panels"]["panel_01"]["mask"][0, 0]


def test_rerunning_ingest_replaces_the_record_and_drops_downstream_results(tab, monkeypatch):
    import h5py
    from midas_gui import project
    path = _open_project(tab)
    _run_fake_analysis(tab, monkeypatch)
    assert project.read_solve_cell(path)["index"] is not None

    tab._on_ingest_done(_fake_ingest_result(_fake_spots(n=7)))

    rec = project.read_solve_cell(path)
    assert len(rec["ingest"]["spots_df"]) == 7
    assert rec["diamond"] is None and rec["index"] is None
    with h5py.File(path, "r") as f:
        assert set(f["analysis/solve_cell"].keys()) == {"metadata", "ingest"}
        assert list(f["analysis"].keys()).count("solve_cell") == 1


def test_rerunning_diamond_filter_drops_index_results(tab, monkeypatch):
    from midas_gui import project
    path = _open_project(tab)
    _run_fake_analysis(tab, monkeypatch)

    tab._on_diamond_done(_fake_diamond_result(tab._spots_df, n_flagged=3))

    rec = project.read_solve_cell(path)
    assert rec["diamond"]["meta"]["summary"]["n_diamond_flagged"] == 3
    assert rec["index"] is None


def test_open_project_restores_the_whole_analysis_into_a_fresh_tab(tab, monkeypatch):
    import numpy as np
    from midas_gui import project
    from midas_gui.tab_solve_cell import SolveCellTab
    path = _open_project(tab)
    _run_fake_analysis(tab, monkeypatch)
    rec = project.read_solve_cell(path)

    fresh = SolveCellTab()
    try:
        ctx = project.ProjectContext()
        ctx.path = path
        fresh.set_project_context(ctx)
        fresh.apply_project_solve_cell(rec, restore_config=True)

        assert fresh._spots_df.equals(tab._spots_df)
        assert fresh._flagged_df.equals(tab._flagged_df)
        assert fresh._candidate_df.equals(tab._candidate_df)
        assert fresh._ingest_status.text() == tab._ingest_status.text()
        assert fresh._diamond_status.text() == tab._diamond_status.text()
        assert fresh._ab_status.text() == tab._ab_status.text()
        assert fresh._refine_btn.isEnabled() and fresh._ab_btn.isEnabled()
        # ab-initio state is rerunnable (Refine only needs these)
        assert fresh._ab_initio_raw.success
        assert fresh._ab_initio_raw.indexed_mask.tolist() == tab._ab_initio_raw.indexed_mask.tolist()
        assert np.array_equal(fresh._ab_initio_raw.hkl, tab._ab_initio_raw.hkl)
        assert np.allclose(fresh._g_2pi, tab._g_2pi)
        # domains
        assert len(fresh._domains) == 1 and fresh._domain_list.count() == 1
        d, d0 = fresh._domains[0], tab._domains[0]
        assert d["label"] == d0["label"] and d["method"] == d0["method"]
        assert d["result"]["cell"] == d0["result"]["cell"]
        assert d["result"]["reflection_fit"] == d0["result"]["reflection_fit"]
        assert list(d["claimed_index"]) == list(d0["claimed_index"])
        assert all(np.allclose(a, b) for a, b in zip(d["xyz"], d0["xyz"]))
        assert fresh._round1_domain_index == tab._round1_domain_index
        assert fresh._leftover_status.text() == tab._leftover_status.text()
        # detector previews + panel/calibration config
        pid = tab._active_panel_id()
        assert fresh._ingest_preview_by_panel[pid]["mask"][0, 0]
        assert fresh._ingest_preview_by_panel[pid]["loaded_frame_index"] == 4
        assert fresh._panels[pid].has_calibration()
        # restoring leaves nothing to rewrite
        assert not fresh._sc_dirty and fresh._sc_record_path == path
    finally:
        fresh.deleteLater()


def test_refine_after_restore_replaces_domain_one_instead_of_duplicating(tab, monkeypatch):
    from midas_gui import project
    from midas_gui.tab_solve_cell import SolveCellTab
    path = _open_project(tab)
    _run_fake_analysis(tab, monkeypatch)
    rec = project.read_solve_cell(path)

    fresh = SolveCellTab()
    try:
        fresh.apply_project_solve_cell(rec, restore_config=True)
        fresh._on_refine_done({"refine_result": _fake_refine_result(5.5)})
        assert fresh._domain_list.count() == 1
        assert fresh._domains[0]["result"]["cell"][0] == 5.5
    finally:
        fresh.deleteLater()


def test_restore_fills_in_calibration_from_the_record_when_the_file_is_gone(tab, monkeypatch):
    from midas_gui import project
    from midas_gui.tab_solve_cell import SolveCellTab
    path = _open_project(tab)
    _run_fake_analysis(tab, monkeypatch)
    calib_path = Path(tab._active_panel()._calib_path)
    rec = project.read_solve_cell(path)
    calib_path.unlink()          # e.g. project opened on another machine

    fresh = SolveCellTab()
    try:
        fresh.apply_project_solve_cell(rec, restore_config=True)
        card = fresh._panels[tab._active_panel_id()]
        assert card.has_calibration()
        assert card.geometry_cfg()["Lsd"] == pytest.approx(1000.0)
        assert any("embedded copy" in v for k, v in card.calibration_summary() if k == "note")
    finally:
        fresh.deleteLater()


def test_results_computed_before_a_project_exists_are_written_on_flush(tab, monkeypatch):
    from midas_gui import project
    _run_fake_analysis(tab, monkeypatch)          # no project open yet
    path = _open_project(tab)                     # project created/saved later
    assert not project.has_solve_cell(path)

    tab.flush_project_record()                    # what MainWindow.save_project calls

    rec = project.read_solve_cell(path)
    assert len(rec["ingest"]["spots_df"]) == 12 and len(rec["index"]["domains"]) == 1


def test_flush_rewrites_everything_when_the_project_file_changes(tab, monkeypatch):
    from midas_gui import project
    first = _open_project(tab, "a.h5")
    _run_fake_analysis(tab, monkeypatch)
    second = _open_project(tab, "b.h5")           # Save As
    tab.flush_project_record()
    for path in (first, second):
        rec = project.read_solve_cell(path)
        assert rec["index"] is not None and len(rec["ingest"]["spots_df"]) == 12


def test_flush_is_a_noop_when_nothing_changed(tab, monkeypatch):
    path = _open_project(tab)
    _run_fake_analysis(tab, monkeypatch)
    tab._log.clear()
    tab.flush_project_record()
    assert "Solve Cell record updated" not in tab._log.toPlainText()


def test_pooled_ingest_records_every_panel(tab, monkeypatch):
    import numpy as np
    from midas_gui import project
    path = _open_project(tab)
    first = tab._active_panel()
    _make_panel_ready(first, monkeypatch, lsd=111.0)
    second = tab._add_panel()
    _make_panel_ready(second, monkeypatch, lsd=222.0)
    first_id, second_id = sorted(tab._panels.keys())
    spots = _fake_spots(8)
    spots["panel_id"] = [first_id] * 4 + [second_id] * 4
    result = {
        "spots_df": spots,
        "summary": {"n_panels": 2, "panel_ids": [first_id, second_id], "n_spots_total": 8,
                    "warnings": []},
        "per_panel": {
            first_id: {"summary": {"n_spots": 4}, "preview": {"mask": np.zeros((4, 4), bool)}},
            second_id: {"summary": {"n_spots": 4}, "preview": {"mask": np.ones((4, 4), bool)}},
        },
    }
    tab._on_ingest_pooled_done(result)

    rec = project.read_solve_cell(path)
    panels = rec["ingest"]["meta"]["panels"]
    assert sorted(int(k) for k in panels) == [first_id, second_id]
    assert panels[str(first_id)]["calibration"]["geometry"]["Lsd"] == pytest.approx(111.0)
    assert panels[str(second_id)]["calibration"]["geometry"]["Lsd"] == pytest.approx(222.0)
    assert rec["ingest"]["meta"]["pooled"] is True
    assert rec["ingest"]["panels"][f"panel_{second_id:02d}"]["mask"].all()


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
    assert tab._ks5["widget"].isHidden()
    tab._leftover_method.setCurrentIndex(1)
    assert not tab._ks5["widget"].isHidden()
    tab._leftover_method.setCurrentIndex(0)
    assert tab._ks5["widget"].isHidden()


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
    assert [tab._recip_mode.itemText(i) for i in range(tab._recip_mode.count())] == ["Panel", "Crystal", "Q residual"]
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


# ── stage 3: known-structure index mode ──────────────────────────────

def test_index_method_combo_toggles_free_vs_known_structure_form(tab):
    assert not tab._ab_free_params.isHidden()
    assert tab._ks3["widget"].isHidden()
    tab._index_method.setCurrentIndex(1)
    assert tab._ab_free_params.isHidden()
    assert not tab._ks3["widget"].isHidden()
    tab._index_method.setCurrentIndex(0)
    assert not tab._ab_free_params.isHidden()
    assert tab._ks3["widget"].isHidden()


def test_run_known_structure_index_dispatches_index_known_cell_with_candidate_pool(tab, monkeypatch):
    from midas_gui import tab_solve_cell
    df = _fake_candidate_df(10)
    tab._candidate_df = df
    tab._index_method.setCurrentIndex(1)
    tab._ks3["a"].setValue(4.0); tab._ks3["b"].setValue(4.0); tab._ks3["c"].setValue(4.0)
    tab._ks3["sg"].setValue(225)

    _CapturingWorker.instances.clear()
    monkeypatch.setattr(tab_solve_cell, "SolveCellWorker", _CapturingWorker)
    tab._run_index()

    assert len(_CapturingWorker.instances) == 1
    worker = _CapturingWorker.instances[0]
    assert worker.stage == "index_known_cell"
    assert worker.cfg["known_cell"] == (4.0, 4.0, 4.0, 90.0, 90.0, 90.0)
    assert worker.cfg["space_group"] == 225
    assert len(worker.cfg["g_2pi"]) == 10


def test_on_index_known_structure_done_negative_result_adds_no_domain(tab):
    tab._candidate_df = _fake_candidate_df(5)
    tab._on_index_known_structure_done({
        "success": False, "notes": ["no match found"],
        "diagnostics": {"best_n": 1, "null_mean": 0.8, "z_score": 0.1},
    })
    assert tab._domain_list.count() == 0
    assert "No match found" in tab._ab_status.text()


def test_on_index_known_structure_done_success_replaces_round1_domain(tab):
    import numpy as np
    df = _fake_candidate_df(10)
    tab._candidate_df = df
    tab._known_structure_g_2pi = df[["qsample_x", "qsample_y", "qsample_z"]].to_numpy()

    mask1 = np.array([True] * 4 + [False] * 6)
    result1 = dict(_fake_refine_result(5.43), success=True, indexed_mask=mask1, notes=[])
    tab._on_index_known_structure_done(result1)
    assert tab._domain_list.count() == 1
    assert len(tab._leftover_df()) == 6

    # Re-running known-structure indexing (round 1) replaces Domain 1 rather
    # than appending a second one -- same contract as _on_refine_done.
    mask2 = np.array([True] * 6 + [False] * 4)
    result2 = dict(_fake_refine_result(5.44), success=True, indexed_mask=mask2, notes=[])
    tab._on_index_known_structure_done(result2)
    assert tab._domain_list.count() == 1
    assert len(tab._leftover_df()) == 4


def test_on_index_known_structure_done_logs_systematic_absence_note(tab):
    import numpy as np
    df = _fake_candidate_df(5)
    tab._candidate_df = df
    tab._known_structure_g_2pi = df[["qsample_x", "qsample_y", "qsample_z"]].to_numpy()
    mask = np.array([True] * 3 + [False] * 2)
    result = dict(_fake_refine_result(), success=True, indexed_mask=mask,
                  notes=["40% of matched reflections are systematically absent for "
                         "space group 225 (Fm-3m) -- the declared space group may not "
                         "match this data."])
    tab._on_index_known_structure_done(result)
    assert "systematically absent" in tab._log.toPlainText()


# ── stage 5: "Fill from latest domain" + space-group cfg ─────────────

def test_fill_ks5_from_latest_domain_populates_cell_fields(tab):
    df = _fake_candidate_df(10)
    tab._candidate_df = df
    xyz = (df["qsample_x"].to_numpy()[:4], df["qsample_y"].to_numpy()[:4], df["qsample_z"].to_numpy()[:4])
    assert not tab._ks5_fill_btn.isEnabled()
    tab._add_domain("free (ab-initio)", _fake_refine_result(5.43), df.index[:4], xyz)
    assert tab._ks5_fill_btn.isEnabled()

    tab._fill_ks5_from_latest_domain()
    assert tab._ks5["a"].value() == pytest.approx(5.43)
    assert tab._ks5["alpha"].value() == pytest.approx(90.0)


def test_run_index_remaining_known_structure_reads_ks5_fields_and_space_group(tab, monkeypatch):
    from midas_gui import tab_solve_cell
    df = _fake_candidate_df(10)
    tab._candidate_df = df
    xyz = (df["qsample_x"].to_numpy()[:4], df["qsample_y"].to_numpy()[:4], df["qsample_z"].to_numpy()[:4])
    tab._add_domain("free (ab-initio)", _fake_refine_result(5.43), df.index[:4], xyz)

    tab._leftover_method.setCurrentIndex(1)
    tab._ks5["b"].setValue(6.0)
    tab._ks5["sg"].setValue(221)

    _CapturingWorker.instances.clear()
    monkeypatch.setattr(tab_solve_cell, "SolveCellWorker", _CapturingWorker)
    tab._run_index_remaining()

    assert len(_CapturingWorker.instances) == 1
    worker = _CapturingWorker.instances[0]
    assert worker.stage == "index_known_cell"
    assert worker.cfg["known_cell"][1] == pytest.approx(6.0)
    assert worker.cfg["space_group"] == 221


# ── reciprocal-space plot legend ──────────────────────────────────────

def test_update_recip_plot_has_no_legend_when_nothing_drawn(tab):
    tab._recip_view.set_groups([])
    assert tab._recip_view.interactor.legend is None


def test_update_recip_plot_legend_names_each_drawn_group(tab):
    import numpy as np
    xyz = (np.array([0.1]), np.array([0.2]), np.array([0.3]))
    tab._recip_view.set_groups([
        (xyz, "#ff3030", "Diamond/gasket", []),
        (xyz, "#4da3ff", "Domain 1 (free (ab-initio))", []),
    ])
    assert tab._recip_view.interactor.legend is not None
    labels = {label for label, _color in tab._recip_view.legend_entries}
    assert "Diamond/gasket" in labels
    assert "Domain 1 (free (ab-initio))" in labels


def test_refresh_recip_view_crystal_mode_legend_names_domains(tab):
    df = _fake_candidate_df(10)
    tab._candidate_df = df
    xyz = (df["qsample_x"].to_numpy()[:4], df["qsample_y"].to_numpy()[:4], df["qsample_z"].to_numpy()[:4])
    tab._add_domain("free (ab-initio)", _fake_refine_result(5.43), df.index[:4], xyz)
    tab._recip_mode.setCurrentIndex(1)
    tab._refresh_recip_view()
    assert tab._recip_view.interactor.legend is not None
    labels = {label for label, _color in tab._recip_view.legend_entries}
    assert "Domain 1 (free (ab-initio))" in labels


def _linked_spots(tab):
    from midas_gui.solve_cell.pipeline import _reflection_fit_data
    import numpy as np
    df = _fake_candidate_df(4, panel_ids=[1, 1, 2, 2]).assign(
        spot_id=[10, 20, 30, 40], loaded_frame_idx=[0, 1, 2, 3],
        row=[2., 3., 4., 5.], col=[5., 6., 7., 8.])
    tab._spots_df = df
    tab._flagged_df = df.assign(is_diamond=[True, False, False, False])
    tab._candidate_df = tab._flagged_df.iloc[1:].reset_index(drop=True)
    claimed = tab._candidate_df.iloc[[2, 0]]  # deliberately non-sorted
    result = _fake_refine_result()
    result.update(_reflection_fit_data(np.eye(3), [[1, 0, 0], [0, 1, 0]],
                                     claimed[['qsample_x', 'qsample_y', 'qsample_z']].to_numpy()))
    tab._add_domain('free (ab-initio)', result, claimed.index, tab._xyz_cols(claimed))
    return df


def test_spot_selection_keeps_source_id_and_fit_order_across_filtering(tab):
    _linked_spots(tab)
    rows = {r['spot_id']: r for r in tab._display_spot_rows()}
    assert rows[10]['category'] == 'Diamond/gasket'
    assert rows[30]['category'] == 'Unindexed/spurious'
    assert rows[40]['hkl'] == [1, 0, 0]
    assert rows[20]['hkl'] == [0, 1, 0]
    tab._select_spot(20)
    tab._update_spot_overlay(0, 1)
    assert tab._spot_scatter.points()[0].data() == 20
    assert tab._det_selected.points()[0].pos().x() == 6
    assert 'hkl: (0, 1, 0)' in tab._spot_details.text()
    tab._update_spot_overlay(0, 0)
    assert len(tab._det_selected.points()) == 0


def test_projected_spot_picking_tracks_camera_and_hidden_population(tab):
    """VTK's own vtkPointPicker does the real screen-space hit-testing (not
    re-tested here -- it has no usable viewport under this project's
    offscreen Qt test convention, see tests/test_pyvista_spike.py); what
    this test owns is that the right spot_ids actually reach the rendered
    scene (so picking has something correct to find) and that hiding the
    unindexed population removes exactly that population, with the camera
    left untouched by either."""
    _linked_spots(tab)
    tab._recip_view.camera_position = [(1.0, 2.0, 3.0), (0.0, 0.0, 0.0), (0.0, 0.0, 1.0)]
    camera = tab._recip_view.camera_position

    def pickable_ids():
        ids = set()
        for actor in tab._recip_view._group_actors:
            arr = actor.mapper.dataset.point_data.get("spot_id")
            if arr is not None:
                ids.update(int(v) for v in arr)
        return ids

    assert 40 in pickable_ids()
    tab._recip_mode.setCurrentIndex(1)
    tab._recip_show_unindexed.setChecked(False)
    ids = pickable_ids()
    assert 30 not in ids
    assert 20 in ids
    assert tab._recip_view.camera_position == camera


def test_residual_mode_and_overlay_keep_one_colorbar_and_camera(tab):
    _linked_spots(tab)
    tab._recip_view.camera_position = [(1.0, 2.0, 3.0), (0.0, 0.0, 0.0), (0.0, 0.0, 1.0)]
    camera = tab._recip_view.camera_position
    tab._recip_mode.setCurrentIndex(2)
    tab._recip_predicted.setChecked(True)
    for _ in range(3):
        tab._refresh_recip_view()
        assert len(tab._recip_view.interactor.scalar_bars.keys()) == 1
        assert len(tab._recip_view._fitted_actors) == 2   # cross points + connecting lines
    assert tab._recip_view.camera_position == camera
    tab._recip_mode.setCurrentIndex(0)
    tab._refresh_recip_view()
    assert len(tab._recip_view.interactor.scalar_bars.keys()) == 0


def test_link_navigation_switches_to_source_panel_and_original_frame(tab, monkeypatch):
    import numpy as np
    _linked_spots(tab)
    panel = tab._add_panel()
    monkeypatch.setattr(panel.loader, 'n_frames', lambda: 5)
    frame = [0]
    monkeypatch.setattr(panel.loader, 'frame_index', lambda: frame[0])
    monkeypatch.setattr(panel.loader, 'set_frame', lambda i: frame.__setitem__(0, i))
    monkeypatch.setattr(panel.loader, 'current_frame', lambda: np.zeros((10, 10)))
    tab._panel_tabs.setCurrentIndex(0)
    tab._select_spot(40)
    tab._show_selected_on_detector()
    assert tab._active_panel() is panel
    assert frame[0] == 3
    assert tab._view_tabs.currentIndex() == 1
    assert len(tab._det_selected.points()) == 1
    assert tab._det_selected.points()[0].pos().x() == 8
    # Picking this detector marker retains the same reciprocal-space identity.
    tab._on_detector_spot_clicked(tab._spot_scatter, tab._spot_scatter.points(), None)
    assert tab._selected_spot_id == 40


def test_new_ingest_invalidates_old_domains_and_selection(tab):
    _linked_spots(tab)
    tab._select_spot(40)
    tab._on_ingest_done({'spots_df': _fake_candidate_df(2)})
    assert tab._domains == []
    assert tab._candidate_df is None
    assert tab._selected_spot_id is None
    assert not tab._show_detector_btn.isEnabled()
    assert not tab._refine_btn.isEnabled()
    assert len(tab._display_spot_rows()) == 2


def test_repeated_residual_refreshes_preserve_plot_area(tab):
    """Regression for the matplotlib-era colorbar-nesting shrink bug (see
    .context/DECISIONS.md 2026-10-23, fixed on main before this branch):
    the native VTK scalar bar this widget uses now has no such GridSpec-
    nesting failure mode, but repeated refreshes must still not accumulate
    duplicate scalar bars or disturb the camera."""
    _linked_spots(tab)
    tab._recip_mode.setCurrentIndex(2)
    tab._recip_view.camera_position = [(1.0, 2.0, 3.0), (0.0, 0.0, 0.0), (0.0, 0.0, 1.0)]
    camera = tab._recip_view.camera_position
    for _ in range(5):
        tab._refresh_recip_view()
        assert len(tab._recip_view.interactor.scalar_bars.keys()) == 1
        assert tab._recip_view.camera_position == camera
    # Leaving and returning to residual mode must not duplicate it either.
    tab._recip_mode.setCurrentIndex(0)
    tab._refresh_recip_view()
    tab._recip_mode.setCurrentIndex(2)
    tab._refresh_recip_view()
    assert len(tab._recip_view.interactor.scalar_bars.keys()) == 1
    assert tab._recip_view.camera_position == camera


def test_click_selection_preserves_scene_camera_and_plot_size(tab):
    """Regression for the matplotlib-era "selection rebuilds the whole
    scene" bug (.context/DECISIONS.md 2026-10-23): a click must only swap
    the selection actor, leaving every group actor, the scalar bar and the
    camera untouched. The screen-to-spot_id resolution itself is VTK's own
    vtkPointPicker (see _PickableInteractor.pick_spot_id) -- not testable
    here since offscreen has no real viewport (see
    tests/test_pyvista_spike.py) -- so this drives the same signal a real
    pick emits, exercising exactly the glue code this project owns."""
    _linked_spots(tab)
    tab._recip_mode.setCurrentIndex(2)
    tab._recip_predicted.setChecked(True)
    tab._recip_view.camera_position = [(1.0, 2.0, 3.0), (0.0, 0.0, 0.0), (0.0, 0.0, 1.0)]
    camera = tab._recip_view.camera_position
    group_actors = tuple(tab._recip_view._group_actors)
    scalar_bar_count = len(tab._recip_view.interactor.scalar_bars.keys())
    for spot_id in (20, 40, 20, 40):
        tab._recip_view.spotClicked.emit(spot_id)
        assert tab._selected_spot_id == spot_id
        assert tuple(tab._recip_view._group_actors) == group_actors
        assert len(tab._recip_view.interactor.scalar_bars.keys()) == scalar_bar_count
        assert tab._recip_view.camera_position == camera


# ── Auto-detect frame range from omega metadata ─────────────────────────────

def test_last_frame_idx_is_derived_from_start_end_step_and_first_idx(tab):
    panel = tab._active_panel()
    panel._ome_start.setValue(-30.0)
    panel._ome_end.setValue(29.0)
    panel._ome_step.setValue(0.1)
    panel._ome_first_idx.setValue(37)
    assert panel._last_idx() == 37 + 590
    panel._geometry = {"Lsd": 1.0}
    assert panel.geometry_cfg()["omega_last_frame_idx"] == 627
    assert panel.geometry_cfg()["omega_ref_deg"] == pytest.approx(-30.0)
    assert "627" in panel._ome_last_lbl.text()


def test_last_frame_idx_open_ended_when_step_is_zero(tab):
    panel = tab._active_panel()
    panel._ome_step.setValue(0.0)
    assert panel._last_idx() == panel._LAST_IDX_OPEN
    assert "end of stack" in panel._ome_last_lbl.text()


def test_detect_first_idx_matches_worked_example(tab):
    """A ramp-up/ramp-down scan: 'first frame whose omega is in range' is the
    rule that gets frame 37 right (see .context/DECISIONS.md)."""
    panel = tab._active_panel()
    ramp = [-30.0 + 0.1 * i for i in range(591)]
    omega = [-40.0] * 37 + ramp + [40.0] * 20
    path = _write_h5_stack_with_omega(omega)
    panel.loader.set_path(path, load=True)
    panel._ome_start.setValue(-30.0)
    panel._ome_end.setValue(29.0)
    panel._ome_step.setValue(0.1)
    assert panel._omega_fields()[0] == "entry/instrument/sample_stage/omega"
    assert panel.detect_first_idx("entry/instrument/sample_stage/omega")
    assert panel._ome_first_idx.value() == 37
    assert panel._last_idx() == 627


def test_detect_first_idx_shows_error_when_nothing_matches(tab, monkeypatch):
    from PyQt5 import QtWidgets
    panel = tab._active_panel()
    path = _write_h5_stack_with_omega([0.0, 1.0, 2.0, 3.0])
    panel.loader.set_path(path, load=True)
    panel._ome_start.setValue(100.0)
    panel._ome_end.setValue(200.0)
    errored = {}
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical",
                        staticmethod(lambda p, title, text: errored.setdefault("title", title)))
    assert not panel.detect_first_idx("entry/instrument/sample_stage/omega")
    assert errored.get("title") == "Detect failed"


def test_first_frame_idx_label_is_clickable_and_opens_the_detect_dialog(tab, monkeypatch):
    from PyQt5 import QtWidgets
    panel = tab._active_panel()
    path = _write_h5_stack_with_omega([0.0, 1.0, 2.0, 3.0, 4.0])
    panel.loader.set_path(path, load=True)
    panel._ome_start.setValue(2.0)
    panel._ome_end.setValue(4.0)
    seen = {}

    def fake_exec(dlg):
        seen["combo"] = dlg.findChild(QtWidgets.QComboBox).itemText(0)
        return QtWidgets.QDialog.Accepted

    monkeypatch.setattr(QtWidgets.QDialog, "exec_", fake_exec)
    panel._ome_first_btn.click()
    assert seen["combo"] == "entry/instrument/sample_stage/omega"
    assert panel._ome_first_idx.value() == 2


def test_loaded_dataset_info_is_just_frames_width_height(tab):
    panel = tab._active_panel()
    path = _write_h5_stack_with_omega([0.0, 1.0, 2.0])
    panel.loader.set_path(path, load=True)
    h, w = panel.loader.current_frame().shape
    assert panel.loader._info.text().startswith(f"3 × {w} × {h}")
    assert panel.loader._nav_row.isHidden()


def test_calibration_link_shows_filename_and_opens_popup(tab, monkeypatch):
    from PyQt5 import QtWidgets
    panel = tab._active_panel()
    assert not panel._calib_link.isEnabled()
    path = _write_calib_json(Lsd=1234.5)
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (path, "")))
    panel._load_calib_file()
    assert panel._calib_link.text() == Path(path).name
    shown = {}

    def fake_exec(dlg):
        table = dlg.findChild(QtWidgets.QTableWidget)
        shown["cols"] = table.columnCount()
        shown["lsd"] = table.item(
            [table.verticalHeaderItem(r).text() for r in range(table.rowCount())].index("Lsd (µm)"), 0).text()
        return 0

    monkeypatch.setattr(QtWidgets.QDialog, "exec_", fake_exec)
    panel._calib_link.click()
    assert shown == {"cols": 1, "lsd": "1234.50"}


def test_panels_are_numbered_in_sequence_and_ids_follow_numbers(tab):
    tab._add_panel(); tab._add_panel()
    assert sorted(tab._panels) == [1, 2, 3]
    assert [tab._panel_tabs.widget(i).panel_id() for i in range(3)] == [1, 2, 3]
    assert [b.text() for b in tab._panel_ribbon._buttons] == ["1", "2", "3"]
    tab._close_panel_tab(0)          # remove panel 1 -> old 2, 3 become 1, 2
    assert sorted(tab._panels) == [1, 2]
    assert [tab._panel_tabs.widget(i).panel_id() for i in range(2)] == [1, 2]
    assert [b.text() for b in tab._panel_ribbon._buttons] == ["1", "2"]


def test_ribbon_click_switches_panel(tab):
    first = tab._active_panel()
    second = tab._add_panel()
    assert tab._panel_ribbon._buttons[1].isChecked()
    tab._panel_ribbon._buttons[0].click()
    assert tab._active_panel() is first
    tab._panel_tabs.setCurrentWidget(second)
    assert tab._panel_ribbon._buttons[1].isChecked()


def test_removing_a_middle_panel_with_results_asks_and_discards(tab, monkeypatch):
    from PyQt5 import QtWidgets
    tab._add_panel()
    tab._spots_df = _fake_spots(3)
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.No))
    tab._close_panel_tab(0)
    assert tab._panel_tabs.count() == 2 and tab._spots_df is not None
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes))
    tab._close_panel_tab(0)
    assert tab._panel_tabs.count() == 1 and tab._spots_df is None


def test_about_text_is_plain_and_solve_cell_tab_has_no_inline_blurb(tab):
    from PyQt5 import QtWidgets
    assert "<b>" not in tab.ABOUT_TEXT
    texts = [l.text() for l in tab.findChildren(QtWidgets.QLabel)]
    assert not any("work in progress) — each panel" in t for t in texts)


def test_legacy_omega_state_maps_to_start_end(tab):
    panel = tab._active_panel()
    panel.set_state({"fields": {"ome_first_idx": 10, "ome_first_deg": 5.0,
                                "ome_last_idx": 110, "ome_step": 0.5}})
    assert panel._ome_start.value() == pytest.approx(5.0)
    assert panel._ome_end.value() == pytest.approx(55.0)
    assert panel._last_idx() == 110


def test_run_ingest_warns_and_can_be_cancelled_when_omega_step_is_zero(tab, monkeypatch):
    from PyQt5 import QtWidgets
    from midas_gui import tab_solve_cell
    panel = tab._active_panel()
    _make_panel_ready(panel, monkeypatch)
    panel._ome_first_idx.setValue(38)
    panel._ome_end.setValue(62.8)
    panel._ome_step.setValue(0.0)

    warned = {}

    def fake_warning(parent, title, text, *buttons_and_default):
        warned["title"] = title
        return QtWidgets.QMessageBox.No

    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", staticmethod(fake_warning))
    _CapturingWorker.instances.clear()
    monkeypatch.setattr(tab_solve_cell, "SolveCellWorker", _CapturingWorker)
    tab._run_ingest()
    assert warned.get("title") == "Omega step is 0°"
    assert _CapturingWorker.instances == []   # cancelled -- nothing launched


def test_run_ingest_proceeds_past_omega_step_warning_when_confirmed(tab, monkeypatch):
    from PyQt5 import QtWidgets
    from midas_gui import tab_solve_cell
    panel = tab._active_panel()
    _make_panel_ready(panel, monkeypatch)
    panel._ome_first_idx.setValue(38)
    panel._ome_end.setValue(62.8)
    panel._ome_step.setValue(0.0)

    monkeypatch.setattr(
        QtWidgets.QMessageBox, "warning",
        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.Yes))
    _CapturingWorker.instances.clear()
    monkeypatch.setattr(tab_solve_cell, "SolveCellWorker", _CapturingWorker)
    tab._run_ingest()
    assert len(_CapturingWorker.instances) == 1


def test_run_ingest_no_omega_step_warning_when_step_is_set(tab, monkeypatch):
    from PyQt5 import QtWidgets
    from midas_gui import tab_solve_cell
    panel = tab._active_panel()
    _make_panel_ready(panel, monkeypatch)
    panel._ome_first_idx.setValue(38)
    panel._ome_end.setValue(62.8)
    panel._ome_step.setValue(0.1)

    warned = {}
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "warning",
        staticmethod(lambda *a, **k: warned.setdefault("hit", True)))
    _CapturingWorker.instances.clear()
    monkeypatch.setattr(tab_solve_cell, "SolveCellWorker", _CapturingWorker)
    tab._run_ingest()
    assert "hit" not in warned
    assert len(_CapturingWorker.instances) == 1


def test_get_state_set_state_round_trips_omega_fields(tab, app):
    panel = tab._active_panel()
    panel._ome_start.setValue(-30.0)
    panel._ome_end.setValue(29.0)
    panel._ome_step.setValue(0.1)
    panel._ome_first_idx.setValue(37)
    panel._ome_field = "entry/instrument/sample_stage/omega"
    restored = type(tab)()
    try:
        restored.set_state(tab.get_state())
        rp = restored._active_panel()
        assert rp._ome_start.value() == pytest.approx(-30.0)
        assert rp._ome_end.value() == pytest.approx(29.0)
        assert rp._ome_first_idx.value() == 37
        assert rp._ome_field == "entry/instrument/sample_stage/omega"
    finally:
        restored.deleteLater()



def test_detect_first_idx_warns_without_a_loaded_file(tab, monkeypatch):
    from PyQt5 import QtWidgets
    panel = tab._active_panel()

    warned = {}

    def fake_warning(parent, title, text):
        warned["title"] = title
        return QtWidgets.QMessageBox.Ok

    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", staticmethod(fake_warning))
    assert not panel.detect_first_idx("entry/omega")
    assert warned.get("title") == "Solve Cell"


def test_calibration_im_trans_reaches_ingest_geometry(tab, monkeypatch):
    panel = tab._active_panel()
    _make_panel_ready(panel, monkeypatch, im_trans=[2])
    g = panel.geometry_cfg()
    assert g["im_trans"] == [2] and g["im_trans_in_file"] is True


def test_run_ingest_warns_and_can_be_cancelled_when_im_trans_undeclared(tab, monkeypatch):
    from PyQt5 import QtWidgets
    from midas_gui import tab_solve_cell
    panel = tab._active_panel()
    _make_panel_ready(panel, monkeypatch)
    # a calibration.json that records no im_trans at all ("file doesn't say")
    path = _write_calib_json()
    d = json.loads(Path(path).read_text()); d.pop("im_trans"); Path(path).write_text(json.dumps(d))
    assert panel._load_calib_from_path(path)
    assert panel.geometry_cfg()["im_trans_in_file"] is False

    warned = {}

    def fake_warning(parent, title, text, *rest):
        warned["title"] = title
        return QtWidgets.QMessageBox.No

    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", staticmethod(fake_warning))
    _CapturingWorker.instances.clear()
    monkeypatch.setattr(tab_solve_cell, "SolveCellWorker", _CapturingWorker)
    tab._run_ingest()
    assert warned.get("title") == "Calibration image transform not declared"
    assert _CapturingWorker.instances == []


def test_run_ingest_no_im_trans_warning_when_declared(tab, monkeypatch):
    from PyQt5 import QtWidgets
    from midas_gui import tab_solve_cell
    panel = tab._active_panel()
    _make_panel_ready(panel, monkeypatch, im_trans=[2])
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "warning",
        staticmethod(lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected warning"))))
    _CapturingWorker.instances.clear()
    monkeypatch.setattr(tab_solve_cell, "SolveCellWorker", _CapturingWorker)
    tab._run_ingest()
    assert len(_CapturingWorker.instances) == 1
