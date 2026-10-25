"""Control-API ops that let an MCP agent drive every Solve Cell workflow —
``GuiController.op_solve_cell_*`` in ``midas_gui/control_api.py`` plus the
agent-mode / ``stageFinished`` hooks in ``SolveCellTab``.

Driven through ``GuiController.dispatch`` (the wire-protocol entry point).
Forked: builds a real ``SolveCellTab`` (VTK-backed Reciprocal view), see
test_solve_cell_ui.py for why. Heavy stages are exercised by stubbing
``pipeline.run_stage`` so these stay fast and need no beamline data.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.forked

_SCRATCH: list = []


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def _clean_scratch():
    yield
    while _SCRATCH:
        shutil.rmtree(_SCRATCH.pop(), ignore_errors=True)


class _Fake:
    def set_agent_mode(self, on):
        pass


@pytest.fixture
def ctrl(app, monkeypatch):
    """A GuiController around a real SolveCellTab. Any modal dialog would
    hang the forked test, so make them fail loudly instead."""
    from PyQt5 import QtWidgets
    from midas_gui.control_api import GuiController
    from midas_gui.tab_solve_cell import SolveCellTab

    def _no_dialog(*a, **k):
        raise AssertionError(f"modal dialog shown in agent mode: {a[1:3]}")
    for name in ("warning", "critical", "information", "question"):
        monkeypatch.setattr(QtWidgets.QMessageBox, name, staticmethod(_no_dialog))
    monkeypatch.setattr("midas_gui.tab_solve_cell.show_error", _no_dialog)

    tab = SolveCellTab()
    win = types.SimpleNamespace(
        _view_tab=types.SimpleNamespace(_geom_card=_Fake()), _cal_tab=_Fake(),
        _solve_cell_tab=tab, _tabs=None)
    c = GuiController(win)
    yield c
    tab.deleteLater()
    app.processEvents()


def _call(c, op, **args):
    r = c.dispatch(op, args)
    return r


def _ok(c, op, **args):
    r = _call(c, op, **args)
    assert r["ok"], r
    return r["result"]


def _calib_json() -> str:
    d = Path(tempfile.mkdtemp(prefix="mg_sc_ctrl_"))
    _SCRATCH.append(d)
    p = d / "calibration.json"
    p.write_text(json.dumps({
        "wavelength_A": 0.17298, "pxY": 150.0, "pxZ": 150.0, "NrPixelsY": 2880,
        "NrPixelsZ": 2880, "Lsd": 1000473.93, "BC_y": 1430.4, "BC_z": 1342.5,
        "tx": 0.0, "ty": -0.47, "tz": -0.22, "im_trans": []}))
    return str(p)


def _h5_stack(n=4) -> str:
    import h5py
    import numpy as np
    d = Path(tempfile.mkdtemp(prefix="mg_sc_ctrl_"))
    _SCRATCH.append(d)
    p = d / "stack.h5"
    with h5py.File(p, "w") as f:
        f.create_dataset("exchange/data", data=np.zeros((n, 4, 4), dtype=np.uint16))
        f.create_dataset("entry/omega", data=np.arange(n, dtype=float))
    return str(p)


def _wait_job(app, c, job_id, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        job = _ok(c, "solve_cell.get_job", job_id=job_id)
        if job["status"] != "running":
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


# ── configuration ───────────────────────────────────────────────────

def test_get_state_on_a_fresh_tab(ctrl):
    st = _ok(ctrl, "solve_cell.get_state")
    assert list(st["panels"]) == [1] and not st["running"]
    assert st["panels"][1]["has_calibration"] is False
    assert st["results"]["domains"] == [] and st["results"]["n_spots"] is None
    assert st["params"]["index"]["method"] == "free"


def test_panel_configuration_round_trips(ctrl):
    assert _ok(ctrl, "solve_cell.add_panel")["panels"] == [1, 2]
    _ok(ctrl, "solve_cell.select_panel", panel_id=2)
    geo = _ok(ctrl, "solve_cell.panel_load_calibration", path=_calib_json())
    assert geo["panel_id"] == 2 and geo["geometry"]["wavelength_A"] == pytest.approx(0.17298)
    load = _ok(ctrl, "solve_cell.panel_load_data", path=_h5_stack(6), panel_id=2)
    assert load["n_frames"] == 6
    om = _ok(ctrl, "solve_cell.panel_set_omega", start=0.0, end=5.0, step=1.0,
             first_idx=0, panel_id=2)
    assert om["step"] == 1.0 and om["omega_span_without_step"] is False
    assert "entry/omega" in _ok(ctrl, "solve_cell.panel_list_omega_fields", panel_id=2)["fields"]
    assert _ok(ctrl, "solve_cell.panel_detect_first_idx", field="entry/omega",
               panel_id=2)["first_idx"] == 0
    st = _ok(ctrl, "solve_cell.get_state")["panels"][2]
    assert st["has_calibration"] and st["omega"]["last_idx"] == 5


def test_bad_panel_and_bad_calibration_are_errors_not_dialogs(ctrl):
    assert _call(ctrl, "solve_cell.panel_load_data", path="x", panel_id=9)["error"]["type"] == "bad_args"
    r = _call(ctrl, "solve_cell.panel_load_calibration", path="/nonexistent/calib.json")
    assert not r["ok"] and r["error"]["type"] == "precondition_failed"
    assert not _call(ctrl, "solve_cell.panel_detect_first_idx", field="x")["ok"]


def test_stage_parameters_round_trip(ctrl):
    r = _ok(ctrl, "solve_cell.set_ingest_params", blob_threshold=33.0, blob_min_vol=7,
            gap_bridge=0, sector_candidates=[4, 8])
    assert r["ingest"]["blob_threshold"] == 33.0 and r["ingest"]["gap_bridge"] == 0
    assert r["ingest"]["sector_candidates"] == [4, 8]
    assert _ok(ctrl, "solve_cell.set_diamond_params", diamond_a=3.6, tol_deg=0.1)["diamond_a"] == 3.6
    idx = _ok(ctrl, "solve_cell.set_index_params", method="known_structure", sigma_g=0.01,
              known_structure={"a": 8.1, "sg": 227, "min_accept": 12})
    assert idx["method"] == "known_structure" and idx["known_structure"]["a"] == 8.1
    assert idx["known_structure"]["sg"] == 227
    rem = _ok(ctrl, "solve_cell.set_remaining_params", method="known_structure",
              known_structure={"b": 9.0})
    assert rem["known_structure"]["b"] == 9.0
    assert _call(ctrl, "solve_cell.set_index_params", method="bogus")["error"]["type"] == "bad_args"
    assert _call(ctrl, "solve_cell.set_index_params",
                 known_structure={"nope": 1})["error"]["type"] == "bad_args"
    assert _call(ctrl, "solve_cell.set_remaining_params", method="x")["error"]["type"] == "bad_args"


# ── stage guards (would be modal dialogs interactively) ─────────────

@pytest.mark.parametrize("op", ["run_ingest", "run_diamond_filter", "run_index", "run_refine",
                                "run_index_remaining"])
def test_running_a_stage_out_of_order_is_a_structured_error(ctrl, op):
    r = _call(ctrl, f"solve_cell.{op}")
    assert not r["ok"] and r["error"]["type"] == "precondition_failed"
    assert r["error"]["message"]


def test_ingest_zero_omega_step_needs_acknowledgement(ctrl, monkeypatch):
    _ok(ctrl, "solve_cell.panel_load_calibration", path=_calib_json())
    _ok(ctrl, "solve_cell.panel_load_data", path=_h5_stack())
    _ok(ctrl, "solve_cell.panel_set_omega", start=0.0, end=10.0, step=0.0)
    r = _call(ctrl, "solve_cell.run_ingest")
    assert not r["ok"] and "acknowledge_warnings" in r["error"]["message"]
    assert "Omega step is 0" in r["error"]["message"]


def test_ingest_undeclared_im_trans_needs_acknowledgement(ctrl):
    p = Path(_calib_json())
    d = json.loads(p.read_text()); d.pop("im_trans")
    p.write_text(json.dumps(d))
    _ok(ctrl, "solve_cell.panel_load_calibration", path=str(p))
    _ok(ctrl, "solve_cell.panel_load_data", path=_h5_stack())
    r = _call(ctrl, "solve_cell.run_ingest")
    assert not r["ok"] and "im_trans" in r["error"]["message"]


def test_removing_a_shifting_panel_with_results_is_refused(ctrl):
    import numpy as np
    import pandas as pd
    _ok(ctrl, "solve_cell.add_panel")
    ctrl._sc._spots_df = pd.DataFrame({"spot_id": np.arange(2)})
    r = _call(ctrl, "solve_cell.remove_panel", panel_id=1)
    assert not r["ok"] and r["error"]["type"] == "precondition_failed"
    assert _ok(ctrl, "solve_cell.remove_panel", panel_id=2)["panels"] == [1]   # last panel: fine
    assert _call(ctrl, "solve_cell.remove_panel", panel_id=1)["ok"] is False  # only panel left


# ── async job tracking, stubbed pipeline ────────────────────────────

def _seed_spots(tab):
    import numpy as np
    import pandas as pd
    n = 6
    rng = np.random.default_rng(0)
    df = pd.DataFrame({
        "spot_id": np.arange(n), "panel_id": 1,
        "qsample_x": rng.normal(size=n), "qsample_y": rng.normal(size=n),
        "qsample_z": rng.normal(size=n), "q_norm": np.ones(n)})
    tab._spots_df = df
    tab._diamond_btn.setEnabled(True)
    return df


def test_diamond_filter_job_completes_and_reports_summary(ctrl, app, monkeypatch):
    from midas_gui.solve_cell import pipeline
    df = _seed_spots(ctrl._sc)
    # no calibration on the active panel: structured error, not a traceback
    r = _call(ctrl, "solve_cell.run_diamond_filter")
    assert r["error"]["type"] == "precondition_failed" and "calibration" in r["error"]["message"]
    _ok(ctrl, "solve_cell.panel_load_calibration", path=_calib_json())

    def fake_run_stage(stage, cfg, on_step=None):
        assert stage == "diamond_filter"
        flagged = df.copy(); flagged["is_diamond"] = [True, False, False, False, False, False]
        return {"candidate_df": df[1:].reset_index(drop=True), "flagged_df": flagged,
                "summary": {"n_diamond_flagged": 1, "n_spots": len(df), "n_kept": len(df) - 1}}
    monkeypatch.setattr(pipeline, "run_stage", fake_run_stage)

    r = _ok(ctrl, "solve_cell.run_diamond_filter")
    assert r["stage"] == "diamond"
    # a second run while one is in flight is refused rather than silently ignored
    assert _call(ctrl, "solve_cell.run_diamond_filter")["error"]["type"] == "precondition_failed"
    job = _wait_job(app, ctrl, r["job_id"])
    assert job["status"] == "done", job
    assert job["result"]["n_candidates"] == 5 and job["result"]["n_diamond_flagged"] == 1
    assert job["result"]["n_remaining"] == 5
    spots = _ok(ctrl, "solve_cell.get_spots", which="candidates")
    assert spots["n_rows"] == 5 and Path(spots["path"]).exists()
    assert _ok(ctrl, "solve_cell.get_spots", which="diamond")["n_rows"] == 1
    assert _call(ctrl, "solve_cell.get_spots", which="nope")["error"]["type"] == "bad_args"


def test_failed_stage_resolves_the_job_with_the_error(ctrl, app, monkeypatch):
    from midas_gui.solve_cell import pipeline
    _seed_spots(ctrl._sc)
    _ok(ctrl, "solve_cell.panel_load_calibration", path=_calib_json())

    def boom(stage, cfg, on_step=None):
        raise RuntimeError("synthetic failure")
    monkeypatch.setattr(pipeline, "run_stage", boom)
    r = _ok(ctrl, "solve_cell.run_diamond_filter")
    job = _wait_job(app, ctrl, r["job_id"])
    assert job["status"] == "failed" and "synthetic failure" in job["error"]
    assert "synthetic failure" in _ok(ctrl, "solve_cell.get_log")["log"]


def test_known_structure_negative_result_is_ok_with_a_note(ctrl, app, monkeypatch):
    from midas_gui.solve_cell import pipeline
    df = _seed_spots(ctrl._sc)
    tab = ctrl._sc
    tab._candidate_df = df.copy()
    tab._flagged_df = df.assign(is_diamond=False)
    tab._ab_btn.setEnabled(True)
    _ok(ctrl, "solve_cell.set_index_params", method="known_structure")
    monkeypatch.setattr(pipeline, "run_stage", lambda stage, cfg, on_step=None: {
        "success": False, "notes": ["no orientation beat the null"], "diagnostics": {}})
    r = _ok(ctrl, "solve_cell.run_index")
    job = _wait_job(app, ctrl, r["job_id"])
    assert job["status"] == "done" and "no match" in job["result"]["note"]
    assert job["result"]["domains"] == []


# ── views ───────────────────────────────────────────────────────────

def test_view_and_overlay_controls(ctrl):
    _seed_spots(ctrl._sc)
    ctrl._sc._refresh_recip_view()
    assert _ok(ctrl, "solve_cell.set_view", view="diagnostics")["view"] == "diagnostics"
    assert _ok(ctrl, "solve_cell.get_state")["view"] == "diagnostics"
    assert _call(ctrl, "solve_cell.set_view", view="nope")["error"]["type"] == "bad_args"
    _ok(ctrl, "solve_cell.set_reciprocal_options", color_by="crystal", show_unindexed=False,
        point_size=10, size_mode="intensity")
    assert ctrl._sc._recip_mode.currentIndex() == 1
    assert _call(ctrl, "solve_cell.set_reciprocal_options",
                 color_by="x")["error"]["type"] == "bad_args"
    ov = _ok(ctrl, "solve_cell.set_overlays", shells=True, ewald=False)
    assert ov["overlays"]["shells"] and not ov["overlays"]["ewald"]
    assert _call(ctrl, "solve_cell.set_overlays", bogus=True)["error"]["type"] == "bad_args"
    sel = _ok(ctrl, "solve_cell.select_spot", spot_id=3)
    assert sel["found"] and "Spot 3" in sel["description"]
    assert _call(ctrl, "solve_cell.show_selected_on_detector")["ok"]
    ctrl._sc._recip_view.interactor.close()


def test_domain_ops_without_domains(ctrl):
    assert _ok(ctrl, "solve_cell.get_domains")["domains"] == []
    assert _call(ctrl, "solve_cell.select_domain", index=0)["error"]["type"] == "bad_args"
    assert _call(ctrl, "solve_cell.fill_remaining_from_latest_domain")["error"]["type"] == "precondition_failed"
    assert _call(ctrl, "solve_cell.save_project")["error"]["type"] == "precondition_failed"
