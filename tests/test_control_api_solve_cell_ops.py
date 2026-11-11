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
    r = _ok(ctrl, "solve_cell.set_ingest_params", blob_threshold=33.0, blob_min_vol=7, blob_min_frames=1,
            gap_bridge=0, sector_candidates=[4, 8])
    assert r["ingest"]["blob_min_frames"] == 1
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
    ov = _ok(ctrl, "solve_cell.set_overlays", frame_patches=False, ewald=False)
    assert not ov["overlays"]["frame_patches"] and not ov["overlays"]["ewald"]
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


# ── data inputs: fields, masks, omega, read-back ────────────────────

def _wait_fields(app, c, roles, timeout=20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        f = _ok(c, "solve_cell.panel_get_inputs")["fields"]
        if all(not f[r]["pending"] for r in roles):
            return f
        time.sleep(0.02)
    raise AssertionError("field did not finish computing")


def _tif_mask(shape, pixels) -> str:
    import numpy as np
    from PIL import Image
    d = Path(tempfile.mkdtemp(prefix="mg_sc_mask_"))
    _SCRATCH.append(d)
    a = np.zeros(shape, dtype=np.uint8)
    for r, col in pixels:
        a[r, col] = 1
    p = d / f"mask_{len(pixels)}.tif"
    Image.fromarray(a).save(p)
    return str(p)


def test_dark_bright_background_are_settable_with_dataset_range_mode_scale(ctrl, app):
    stack = _h5_stack(6)
    _ok(ctrl, "solve_cell.panel_load_data", path=stack, dataset="exchange/data")
    r = _ok(ctrl, "solve_cell.panel_set_field", role="dark", path=stack, dataset="exchange/data",
            start=0, end=2)
    assert r["field"]["checked"] and r["field"]["start"] == 0 and r["field"]["end"] == 2
    _ok(ctrl, "solve_cell.panel_set_field", role="bright", path=stack, mode="subtract")
    _ok(ctrl, "solve_cell.panel_set_field", role="background", path=stack, scale=2.5,
        start=1, end=3)
    f = _wait_fields(app, ctrl, ("dark", "bright", "background"))
    assert f["dark"]["computed"] and f["bright"]["mode"] == 1       # 1 = subtract
    assert f["background"]["scale"] == 2.5 and f["background"]["start"] == 1
    # the same state is visible in get_state, and a field can be switched off
    st = _ok(ctrl, "solve_cell.get_state")["panels"][1 if 1 in _ok(ctrl, "solve_cell.get_state")["panels"] else 0]
    assert st["fields"]["background"]["checked"] and st["fields"]["dark"]["computed"]
    _ok(ctrl, "solve_cell.panel_set_field", role="dark", path="", checked=False)
    assert not _ok(ctrl, "solve_cell.panel_get_inputs")["fields"]["dark"]["checked"]
    # bad arguments are structured errors
    assert _call(ctrl, "solve_cell.panel_set_field", role="dark", path=stack,
                 scale=2.0)["error"]["type"] == "bad_args"
    assert _call(ctrl, "solve_cell.panel_set_field", role="nope", path=stack)["error"]["type"] == "bad_args"


def test_masks_replace_append_disable_clear_and_report_the_composite(ctrl):
    m1 = _tif_mask((8, 8), [(0, 0), (1, 1)])
    m2 = _tif_mask((8, 8), [(1, 1), (5, 5), (6, 6)])
    r = _ok(ctrl, "solve_cell.panel_set_masks", sources=[m1])
    assert r["composite_masked_px"] == 2 and len(r["sources"]) == 1
    r = _ok(ctrl, "solve_cell.panel_set_masks", sources=[{"kind": "file", "path": m2}], mode="append")
    assert r["composite_masked_px"] == 4                              # union: (1,1) counted once
    r = _ok(ctrl, "solve_cell.panel_set_masks", sources=[{"path": m2, "enabled": False}], mode="append")
    assert r["composite_masked_px"] == 2 and [s["enabled"] for s in r["sources"]] == [True, False]
    assert _ok(ctrl, "solve_cell.get_state")["panels"]  # masks listed per panel
    assert _ok(ctrl, "solve_cell.panel_get_inputs")["masks"]["composite_masked_px"] == 2
    r = _ok(ctrl, "solve_cell.panel_set_masks", sources=[m2])         # replace
    assert r["composite_masked_px"] == 3 and len(r["sources"]) == 1
    r = _ok(ctrl, "solve_cell.panel_set_masks", sources=[])
    assert r["sources"] == [] and r["composite_masked_px"] is None
    for bad in ([{"kind": "file", "path": "/no/such/mask.tif"}], [{"kind": "zip", "path": m1}], [{"kind": "file"}]):
        assert _call(ctrl, "solve_cell.panel_set_masks", sources=bad)["error"]["type"] == "bad_args"
    assert _call(ctrl, "solve_cell.panel_set_masks", sources=[m1], mode="x")["error"]["type"] == "bad_args"


def test_omega_mapping_field_and_auto_first_idx(ctrl):
    stack = _h5_stack(10)                               # entry/omega = 0..9
    _ok(ctrl, "solve_cell.panel_load_data", path=stack)
    r = _ok(ctrl, "solve_cell.panel_set_omega", start=3.0, end=7.0, step=1.0,
            field="entry/omega", auto_first_idx=True)
    assert r["first_idx"] == 3 and r["last_idx"] == 7 and r["field"] == "entry/omega"
    assert r["start"] == 3.0 and r["end"] == 7.0 and r["step"] == 1.0
    omega = _ok(ctrl, "solve_cell.panel_get_inputs")["omega"]
    assert omega["first_idx"] == 3 and omega["last_idx"] == 7 and omega["field"] == "entry/omega"
    # auto-detect without a field is a structured error, not a dialog
    ctrl._sc._active_panel()._ome_field = ""
    r = _call(ctrl, "solve_cell.panel_set_omega", auto_first_idx=True)
    assert r["error"]["type"] == "precondition_failed"
    # setting the first idx directly still works and updates last_idx
    r = _ok(ctrl, "solve_cell.panel_set_omega", first_idx=2)
    assert r["first_idx"] == 2 and r["last_idx"] == 6


def test_panel_get_inputs_reports_data_calibration_and_omega(ctrl):
    stack = _h5_stack(5)
    _ok(ctrl, "solve_cell.panel_load_data", path=stack, dataset="exchange/data")
    _ok(ctrl, "solve_cell.panel_load_calibration", path=_calib_json())
    inp = _ok(ctrl, "solve_cell.panel_get_inputs")
    assert inp["data"]["path"] == stack and inp["data"]["n_frames"] == 5
    assert inp["data"]["dataset"] == "exchange/data"
    assert inp["calibration"]["loaded"] and inp["calibration"]["geometry"]["Lsd"] > 0
    assert set(inp["fields"]) == {"dark", "bright", "background"}
    assert _call(ctrl, "solve_cell.panel_get_inputs", panel_id=9)["error"]["type"] == "bad_args"


# ── ingest-parameter suggestion ─────────────────────────────────────

def _tune_result():
    cuts = {"v2f1": [900, 0], "v2f3": [300, 4], "v4f3": [80, 2], "v8f3": [55, 1]}
    return {"suggestion": {"low_count_threshold": 0.0, "mask_grow": 2, "sector_candidates": "24",
                           "blob_thresh": 12.0, "blob_minvol": 4, "blob_minframes": 3},
            "confidence": "high", "warnings": ["diffuse region"], "n_frames": 600,
            "evidence": {"regime": "photon_counting", "median_percentiles": {"50": 4.0}, "sigma": 1.5,
                         "masked_fraction": 1e-4, "thresholds": [8, 10, 12],
                         "sectors": [{"n_sectors": 24, "cuts": cuts}],
                         "sector_choice": {"n_sectors": 24, "noise_positives": 300, "rejected": []},
                         "grid": {f"T{T}_v{mv}f3": [60, 1] for T in (8, 10, 12) for mv in (2, 4, 8)},
                         "blob": {"n_real": 55.0, "plateau": True, "corner_counts": [55, 55],
                                  "confidence": "high", "expected_spots": 60, "expected_negatives": 1},
                         "diffuse": {"n_px": 0}},
            "timings": {"total": 12.0, "workers": 2}}


def test_suggest_then_apply_through_the_control_api(ctrl, app, monkeypatch):
    from midas_gui.solve_cell import pipeline
    _ok(ctrl, "solve_cell.panel_load_data", path=_h5_stack(4))
    _ok(ctrl, "solve_cell.panel_load_calibration", path=_calib_json())
    # nothing to get / apply yet
    assert _call(ctrl, "solve_cell.get_suggestion")["error"]["type"] == "no_data"
    assert _call(ctrl, "solve_cell.apply_suggested_parameters")["error"]["type"] == "no_data"
    seen = {}

    def fake_run_stage(stage, cfg, on_step=None):
        seen["stage"], seen["cfg_keys"] = stage, sorted(cfg)
        return _tune_result()
    monkeypatch.setattr(pipeline, "run_stage", fake_run_stage)
    before = _ok(ctrl, "solve_cell.get_state")["params"]["ingest"]
    r = _ok(ctrl, "solve_cell.suggest_parameters")
    assert r["stage"] == "tune"
    job = _wait_job(app, ctrl, r["job_id"])
    assert job["status"] == "done", job
    res = job["result"]
    assert res["suggestion"]["sector_candidates"] == "24" and res["confidence"] == "high"
    assert "Regime" in res["report"] and res["evidence"]["sector_choice"]["n_sectors"] == 24
    assert seen["stage"] == "tune_ingest" and {"frames", "geometry", "corrections", "workers"} <= set(seen["cfg_keys"])
    # suggesting changed nothing
    assert _ok(ctrl, "solve_cell.get_state")["params"]["ingest"] == before
    assert _ok(ctrl, "solve_cell.get_state")["tuning"]["has_suggestion"]
    assert _ok(ctrl, "solve_cell.get_suggestion")["confidence"] == "high"
    out = _ok(ctrl, "solve_cell.apply_suggested_parameters")
    ing = out["ingest"]
    assert out["applied"]["blob_thresh"] == 12.0
    assert ing["blob_threshold"] == 12.0 and ing["blob_min_vol"] == 4 and ing["blob_min_frames"] == 3
    assert ing["sector_candidates"] == [24] and ing["low_count_threshold"] == 0.0


def test_suggest_parameters_guards_are_structured_errors(ctrl):
    r = _call(ctrl, "solve_cell.suggest_parameters")
    assert r["error"]["type"] == "precondition_failed" and "calibration" in r["error"]["message"]
    assert _call(ctrl, "solve_cell.suggest_parameters", panel_id=9)["error"]["type"] == "bad_args"


# ── domain loop (sequential refinement) ─────────────────────────────

class _IdleWorker:
    def isRunning(self):
        return False


def _fake_stages(ctrl, app, script):
    """Replace the tab's index/refine/index-remaining launchers with fakes that finish on the next
    event-loop turn and mutate the tab like the real handlers do. ``script`` is a list of dicts,
    one per expected stage call: {"stage", "adds_domain", "ok", "ab_success"}."""
    import types
    import pandas as pd
    from PyQt5 import QtCore
    sc = ctrl._sc
    sc._candidate_df = pd.DataFrame({"x": range(40)})
    calls = []

    def remaining():
        n = 40 - 10 * len(sc._domains)
        return pd.DataFrame({"x": range(max(n, 0))}) if n > 0 else None
    sc._leftover_df = remaining

    def launcher(stage):
        def run():
            step = script[len(calls)]
            assert step["stage"] == stage, (stage, step)
            calls.append(stage)
            sc._worker = _IdleWorker()

            def finish():
                if stage == "index" and sc._index_method.currentIndex() == 0:
                    sc._ab_initio_raw = types.SimpleNamespace(success=step.get("ab_success", True))
                if step.get("adds_domain"):
                    sc._domains.append({"label": f"Domain {len(sc._domains) + 1}", "method": "free",
                                        "claimed_index": list(range(10)),
                                        "result": {"cell": (5.0 + len(sc._domains), 5.0, 5.0, 90.0, 90.0, 90.0),
                                                   "rms_drlv": 0.01}})
                sc._finish_stage(stage, ok=step.get("ok", True), error=None if step.get("ok", True) else "boom",
                                 note=step.get("note", ""))
            QtCore.QTimer.singleShot(0, finish)
        return run
    sc._run_index, sc._run_refine = launcher("index"), launcher("refine")
    sc._run_index_remaining = launcher("index_remaining")
    return calls


def _loop(ctrl, app, **args):
    r = _ok(ctrl, "solve_cell.run_domain_loop", **args)
    assert r["stage"] == "domain_loop"
    return _wait_job(app, ctrl, r["job_id"])


def test_domain_loop_runs_index_refine_then_repeats_until_no_new_domain(ctrl, app):
    calls = _fake_stages(ctrl, app, [
        {"stage": "index"}, {"stage": "refine", "adds_domain": True},
        {"stage": "index_remaining", "adds_domain": True}, {"stage": "index_remaining"}])
    job = _loop(ctrl, app)
    assert job["status"] == "done", job
    assert calls == ["index", "refine", "index_remaining", "index_remaining"]
    res = job["result"]
    assert res["stop_reason"] == "no_new_domain" and res["n_new_domains"] == 2
    assert [s["stage"] for s in res["steps"]] == calls
    assert [s["n_domains_after"] for s in res["steps"]] == [0, 1, 2, 2]
    assert len(res["domains"]) == 2 and "2 new domain" in res["note"]
    assert ctrl._sc_chain is None and not _ok(ctrl, "solve_cell.get_state")["domain_loop"]


def test_domain_loop_stops_at_max_domains(ctrl, app):
    calls = _fake_stages(ctrl, app, [
        {"stage": "index"}, {"stage": "refine", "adds_domain": True},
        {"stage": "index_remaining", "adds_domain": True}])
    job = _loop(ctrl, app, max_domains=2)
    assert job["status"] == "done" and job["result"]["stop_reason"] == "max_domains_reached"
    assert calls == ["index", "refine", "index_remaining"]


def test_domain_loop_continues_from_existing_domains_without_reindexing(ctrl, app):
    calls = _fake_stages(ctrl, app, [{"stage": "index_remaining"}])
    ctrl._sc._domains.append({"label": "Domain 1", "method": "free", "claimed_index": list(range(10)),
                              "result": {"cell": (5.0, 5.0, 5.0, 90.0, 90.0, 90.0), "rms_drlv": 0.01}})
    job = _loop(ctrl, app)
    assert calls == ["index_remaining"] and job["result"]["stop_reason"] == "no_new_domain"
    assert job["result"]["n_new_domains"] == 0


def test_domain_loop_reports_first_domain_not_found(ctrl, app):
    calls = _fake_stages(ctrl, app, [{"stage": "index", "ab_success": False}])
    job = _loop(ctrl, app)
    assert calls == ["index"]
    assert job["status"] == "done" and job["result"]["stop_reason"] == "first_domain_not_found"
    assert job["result"]["domains"] == []


def test_domain_loop_failed_stage_fails_the_job_but_keeps_partial_results(ctrl, app):
    _fake_stages(ctrl, app, [{"stage": "index"}, {"stage": "refine", "adds_domain": True},
                             {"stage": "index_remaining", "ok": False}])
    job = _loop(ctrl, app)
    assert job["status"] == "failed" and "index_remaining failed" in job["error"]
    assert len(job["result"]["domains"]) == 1 and job["result"]["steps"][-1]["ok"] is False
    assert ctrl._sc_chain is None


def test_domain_loop_stops_when_too_few_spots_remain(ctrl, app):
    calls = _fake_stages(ctrl, app, [{"stage": "index"}, {"stage": "refine", "adds_domain": True}])
    job = _loop(ctrl, app, min_remaining_spots=35)      # only 30 left after Domain 1
    assert calls == ["index", "refine"]
    assert job["result"]["stop_reason"] == "too_few_remaining_spots"


def test_domain_loop_cancel_stops_after_the_current_stage(ctrl, app):
    _fake_stages(ctrl, app, [{"stage": "index"}, {"stage": "refine", "adds_domain": True}])
    r = _ok(ctrl, "solve_cell.run_domain_loop")
    assert "stage is already running" in _call(ctrl, "solve_cell.run_diamond_filter")["error"]["message"] \
        or "domain loop is running" in _call(ctrl, "solve_cell.run_diamond_filter")["error"]["message"]
    assert _ok(ctrl, "solve_cell.get_state")["domain_loop"]["job_id"] == r["job_id"]
    c = _ok(ctrl, "solve_cell.cancel_domain_loop")
    assert "after the current stage" in c["note"]
    job = _wait_job(app, ctrl, r["job_id"])
    assert job["status"] == "done" and job["result"]["stop_reason"] == "cancelled"
    assert [s["stage"] for s in job["result"]["steps"]] == ["index"]
    assert _call(ctrl, "solve_cell.cancel_domain_loop")["error"]["type"] == "precondition_failed"


def test_domain_loop_preconditions(ctrl):
    r = _call(ctrl, "solve_cell.run_domain_loop")
    assert r["error"]["type"] == "precondition_failed" and "ingest" in r["error"]["message"]
    import pandas as pd
    ctrl._sc._candidate_df = pd.DataFrame({"x": range(5)})
    assert _call(ctrl, "solve_cell.run_domain_loop", max_domains=0)["error"]["type"] == "bad_args"
    assert _call(ctrl, "solve_cell.run_domain_loop",
                 include_first_domain=False)["error"]["type"] == "bad_args"
