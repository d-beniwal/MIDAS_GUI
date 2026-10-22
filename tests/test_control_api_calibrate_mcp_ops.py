"""Control-API ops added so an MCP agent can drive the Calibrate tab's
projection/seed/refine/tolerance/distortion/residual-map/save/working-dir
controls, not just load an image and click Run — see
``GuiController.op_calibrate_set_projection``/``op_calibrate_project``/
``op_calibrate_run``/``op_calibrate_save_json``/``op_calibrate_save_paramstest``/
``op_calibrate_*working_dir`` in ``midas_gui/control_api.py``.

Each test builds its own ``CalibrationTab`` (via ``GuiController``) and is
``forked`` — building more than a couple of ``CalibrationTab``s in one
process has segfaulted inside pyqtgraph's teardown before (see
``tests/test_calibrate_panel_save.py``/``test_calibrate_distortion_state.py``).
Driven through ``GuiController.dispatch`` (the "namespace.verb" string),
exercising the exact wire-protocol-level entry point ``control_server.py``
calls, not the ``op_*`` methods directly.
"""
from __future__ import annotations

import json
import types
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import tifffile


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _FakeGeomCard:
    def set_agent_mode(self, on):
        pass


class _FakeViewTab:
    def __init__(self):
        self._geom_card = _FakeGeomCard()


class _FakeSignal:
    """Stand-in for a pyqtSignal: records connected callbacks, nothing else
    — these tests never need to actually emit through a fake worker."""
    def __init__(self):
        self._cbs = []

    def connect(self, cb):
        self._cbs.append(cb)


class _FakeCalibrationWorker:
    def __init__(self):
        self.finished = _FakeSignal()
        self.failed = _FakeSignal()


def _make_controller():
    """One real CalibrationTab wired into a GuiController, the same shape
    control_server.py builds — minus the real DataViewerTab, which none of
    these ops touch."""
    from midas_gui.control_api import GuiController
    from midas_gui.tab_calibrate import CalibrationTab
    cal_tab = CalibrationTab()
    win = types.SimpleNamespace(_view_tab=_FakeViewTab(), _cal_tab=cal_tab)
    ctrl = GuiController(win)
    return ctrl, cal_tab


def _crystalline_result():
    """Same minimal fake result shape as test_calibrate_panel_save.py's
    helper of the same name — known to satisfy write_standalone_paramstest
    and the calibration.json dict comprehension."""
    return SimpleNamespace(
        Lsd=200000.0, BC_y=1024.0, BC_z=1024.0, tx=0.15, ty=0.0, tz=0.0,
        distortion={"iso_R2": 0.1}, pxY=200.0, pxZ=200.0,
        NrPixelsY=2048, NrPixelsZ=2048, wavelength_A=0.1729,
        _calibrant_name="CeO2", im_trans=[])


# ── Projection ──────────────────────────────────────────────────────────

@pytest.mark.forked
def test_set_projection_updates_method_skip_nframes(app):
    ctrl, tab = _make_controller()
    res = ctrl.dispatch("calibrate.set_projection",
                        {"method": "mean", "skip": 2, "n_frames": 5})
    assert res["ok"] is True
    assert res["result"] == {"method": "mean", "skip": 2, "n_frames": 5}
    assert tab._proj_method["mean"].isChecked()
    assert tab._proj_skip.value() == 2
    assert tab._proj_nframes.value() == 5


@pytest.mark.forked
def test_set_projection_rejects_unknown_method(app):
    ctrl, tab = _make_controller()
    res = ctrl.dispatch("calibrate.set_projection", {"method": "bogus"})
    assert res["ok"] is False
    assert res["error"]["type"] == "bad_args"


@pytest.mark.forked
def test_project_requires_a_multi_frame_source(app):
    ctrl, tab = _make_controller()
    res = ctrl.dispatch("calibrate.project", {})
    assert res["ok"] is False
    assert res["error"]["type"] == "precondition_failed"


@pytest.mark.forked
def test_project_runs_the_real_worker_and_the_job_completes(app, tmp_path):
    ctrl, tab = _make_controller()
    stack = np.stack([np.full((6, 6), float(i) + 1.0, dtype=np.float32)
                      for i in range(4)], axis=0)
    stack_path = tmp_path / "stack.tif"
    tifffile.imwrite(stack_path, stack)
    tab._loader.set_path(str(stack_path))
    assert tab._loader.n_frames() == 4

    assert ctrl.dispatch("calibrate.set_projection",
                         {"method": "sum", "skip": 0, "n_frames": 0})["ok"] is True

    proj = ctrl.dispatch("calibrate.project", {})
    assert proj["ok"] is True
    job_id = proj["result"]["job_id"]

    tab._proj_worker.wait()
    for _ in range(20):
        app.processEvents()

    job = ctrl.dispatch("calibrate.get_job", {"job_id": job_id})
    assert job["ok"] is True
    assert job["result"]["status"] == "done"
    assert job["result"]["result"]["shape"] == [6, 6]
    assert tab._proj_active is True
    # No dark/bright/background: sum of 1+2+3+4 = 10 at every pixel.
    assert np.allclose(tab._proj_image, 10.0)


@pytest.mark.forked
def test_clear_projection(app):
    ctrl, tab = _make_controller()
    tab._proj_active = True
    tab._proj_image = np.zeros((4, 4), dtype=np.float32)
    res = ctrl.dispatch("calibrate.clear_projection", {})
    assert res["ok"] is True
    assert tab._proj_active is False
    assert tab._proj_image is None


# ── Working directory ────────────────────────────────────────────────────

@pytest.mark.forked
def test_working_dir_get_and_set(app, tmp_path):
    ctrl, tab = _make_controller()
    assert ctrl.dispatch("calibrate.get_working_dir", {})["result"]["path"] == ""
    d = str(tmp_path)
    res = ctrl.dispatch("calibrate.set_working_dir", {"path": d})
    assert res["ok"] is True
    assert tab._out_ed.text() == d
    assert ctrl.dispatch("calibrate.get_working_dir", {})["result"]["path"] == d


@pytest.mark.forked
def test_suggest_working_dir_fills_the_bc_folder(app, tmp_path):
    ctrl, tab = _make_controller()
    work = tmp_path / "park_may26_bc"
    work.mkdir()
    img = work / "ceo2.tif"
    img.write_bytes(b"")
    tab._loader.set_path(str(img), load=False)
    tab._loader.dataChanged.emit()
    tab._out_ed.setText("")   # cleared, to isolate the explicit "suggest" action
    res = ctrl.dispatch("calibrate.suggest_working_dir", {})
    assert res["ok"] is True
    assert Path(tab._out_ed.text()) == work


# ── Save ──────────────────────────────────────────────────────────────────

@pytest.mark.forked
def test_save_json_requires_a_completed_run(app, tmp_path):
    ctrl, tab = _make_controller()
    res = ctrl.dispatch("calibrate.save_json", {"path": str(tmp_path / "out.json")})
    assert res["ok"] is False
    assert res["error"]["type"] == "no_data"


@pytest.mark.forked
def test_save_json_writes_the_file_with_no_dialog(app, tmp_path):
    ctrl, tab = _make_controller()
    tab._result = _crystalline_result()
    out = tmp_path / "calibration.json"
    res = ctrl.dispatch("calibrate.save_json", {"path": str(out)})
    assert res["ok"] is True
    assert res["result"]["path"] == str(out)
    assert out.is_file()
    saved = json.loads(out.read_text())
    assert saved["Lsd"] == pytest.approx(200000.0)
    assert saved["BC_y"] == pytest.approx(1024.0)


@pytest.mark.forked
def test_save_paramstest_requires_a_completed_run(app, tmp_path):
    ctrl, tab = _make_controller()
    res = ctrl.dispatch("calibrate.save_paramstest", {"out_path": str(tmp_path / "p.txt")})
    assert res["ok"] is False
    assert res["error"]["type"] == "no_data"


@pytest.mark.forked
def test_save_paramstest_writes_a_standalone_file_with_no_dialog(app, tmp_path):
    ctrl, tab = _make_controller()
    tab._result = _crystalline_result()
    out = tmp_path / "paramstest.instr.txt"
    res = ctrl.dispatch("calibrate.save_paramstest", {"out_path": str(out)})
    assert res["ok"] is True
    assert res["result"]["out_path"] == str(out)
    assert out.is_file()
    assert "Lsd" in out.read_text()


# ── calibrate.run: refine / tolerances / distortion mode / residual map / seed ──

@pytest.mark.forked
def test_run_applies_refine_distortion_mode_limits_residual_and_distortion_seed(app):
    ctrl, tab = _make_controller()
    # Stub out the actual fit — these assertions are about the widget state
    # op_calibrate_run sets *before* calling _run(), not about running a real
    # CalibrationWorker/backend.
    tab._run = lambda: setattr(tab, "_worker", _FakeCalibrationWorker())

    res = ctrl.dispatch("calibrate.run", {
        "mode": "one_shot",
        "refine": {"Lsd": False, "Distortion": True,
                  "distortion_mode": "Isotropic only"},
        "limits": {"Lsd": {"on": True, "value": 3.0, "unit": "mm"}},
        "build_residual_corr": False,
        "manual_seed": {"BC_y": 1024.0, "distortion": {"iso_R2": 0.05}},
    })
    assert res["ok"] is True
    assert "job_id" in res["result"]

    assert tab._ref_lsd.isChecked() is False
    assert tab._ref_dist.isChecked() is True
    assert tab._dist_coeffs == {"iso_R2", "iso_R4", "iso_R6"}

    cb, spin, combo = tab._limit_widgets["Lsd"]
    assert cb.isChecked() is True
    assert spin.value() == pytest.approx(3.0)
    assert combo.currentText() == "mm"

    assert tab._build_rc.isChecked() is False

    assert tab._seed_bcy.value() == pytest.approx(1024.0)
    assert tab._seed_en_bc.isChecked() is True
    assert tab._seed_dist == {"iso_R2": 0.05}
    assert tab._seed_en_dist.isChecked() is True


@pytest.mark.forked
def test_run_accepts_explicit_distortion_coeffs_list(app):
    ctrl, tab = _make_controller()
    tab._run = lambda: setattr(tab, "_worker", _FakeCalibrationWorker())
    res = ctrl.dispatch("calibrate.run", {
        "mode": "one_shot",
        "refine": {"distortion_coeffs": ["a1", "phi1"]},
    })
    assert res["ok"] is True
    assert tab._dist_coeffs == {"a1", "phi1"}


@pytest.mark.forked
def test_run_rejects_unknown_distortion_mode(app):
    ctrl, tab = _make_controller()
    res = ctrl.dispatch("calibrate.run",
                        {"mode": "one_shot", "refine": {"distortion_mode": "bogus"}})
    assert res["ok"] is False
    assert res["error"]["type"] == "bad_args"


@pytest.mark.forked
def test_run_rejects_unknown_distortion_coeffs(app):
    ctrl, tab = _make_controller()
    res = ctrl.dispatch("calibrate.run",
                        {"mode": "one_shot", "refine": {"distortion_coeffs": ["bogus"]}})
    assert res["ok"] is False
    assert res["error"]["type"] == "bad_args"


@pytest.mark.forked
def test_run_rejects_unknown_limit_row(app):
    ctrl, tab = _make_controller()
    res = ctrl.dispatch("calibrate.run",
                        {"mode": "one_shot", "limits": {"bogus_row": {"on": True}}})
    assert res["ok"] is False
    assert res["error"]["type"] == "bad_args"
