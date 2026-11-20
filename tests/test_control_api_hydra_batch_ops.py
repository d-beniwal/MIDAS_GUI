"""``hydra_batch.*`` control-API ops and the shared auto R max.

Uses the 256x256 one-frame fixtures in ``test_data/gui_synthetic/hydra`` and a
real ``MainWindow`` (one per file). Every ``QMessageBox`` entry point fails the
test: the ops must return structured errors, never open a modal dialog, since
that would block the GUI event loop and the control socket with it.

The regression this pins: an *auto* Rmax used to resolve per panel (each panel's
own farthest corner), so the panels' n_R differed and the four-panel zarr
combine refused every frame ("OmegaSumFrame first-dim differs"). Auto now means
one value shared by all panels.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

FIX = Path(__file__).resolve().parent.parent / "test_data" / "gui_synthetic" / "hydra"
# Per-panel beam centres: different corners -> different per-panel auto Rmax.
BCS = {1: (128.0, 128.0), 2: (100.0, 150.0), 3: (140.0, 110.0), 4: (90.0, 90.0)}

pytestmark = pytest.mark.skipif(not (FIX / "ge1" / "panel.ge1.h5").exists(),
                                reason="synthetic hydra fixtures absent")


@pytest.fixture(scope="module")
def qapp():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def win(qapp):
    import midas_gui.app as app_mod
    return app_mod.MainWindow()


@pytest.fixture
def ctrl(win, monkeypatch):
    from PyQt5 import QtWidgets
    from midas_gui.control_api import GuiController

    def _boom(*a, **k):
        raise AssertionError("a modal QMessageBox was opened by a hydra_batch op")
    for name in ("information", "critical", "warning", "question"):
        monkeypatch.setattr(QtWidgets.QMessageBox, name, staticmethod(_boom))
    return GuiController(win)


def _calib_set(tmp_path, tag, dbc=0.0):
    """One paramstest per panel, with panel-specific BC (shifted by dbc)."""
    d = tmp_path / tag
    d.mkdir()
    out = {}
    for n in (1, 2, 3, 4):
        txt = (FIX / f"ge{n}" / f"ps_ge{n}.txt").read_text()
        by, bz = BCS[n]
        txt = re.sub(r"(?m)^BC .*$", f"BC {by + dbc} {bz + dbc}", txt)
        p = d / f"ps_ge{n}.txt"
        p.write_text(txt)
        out[f"ge{n}"] = str(p)
    return out


def _panel_files():
    return str(FIX / "ge1" / "panel.ge1.h5")


def _wait(ctrl, qapp, job_id, timeout=120):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        qapp.processEvents()
        r = ctrl.dispatch("hydra_batch.get_job", {"job_id": job_id})
        assert r["ok"], r
        if r["result"]["status"] != "running":
            return r["result"]
        time.sleep(0.05)
    raise AssertionError("hydra_batch job did not finish in time")


def test_bad_arguments_are_structured_errors(ctrl):
    r = ctrl.dispatch("hydra_batch.set_scans", {"paths": []})
    assert not r["ok"] and r["error"]["type"] == "bad_args"
    r = ctrl.dispatch("hydra_batch.set_scans", {"paths": ["/no/such/ge1.h5"]})
    assert not r["ok"] and r["error"]["type"] == "bad_args"
    r = ctrl.dispatch("hydra_batch.set_calibration", {})
    assert not r["ok"] and r["error"]["type"] == "bad_args"
    r = ctrl.dispatch("hydra_batch.set_caking", {"r_bin": 1.0, "panels": ["ge9"]})
    assert not r["ok"] and r["error"]["type"] == "bad_args"
    r = ctrl.dispatch("hydra_batch.set_options", {"kernel": "nope"})
    assert not r["ok"] and r["error"]["type"] == "bad_args"
    r = ctrl.dispatch("hydra_batch.set_options", {"formats": ["nope"]})
    assert not r["ok"] and r["error"]["type"] == "bad_args"
    r = ctrl.dispatch("hydra_batch.run_queue", {"jobs": [{"name": "x"}]})
    assert not r["ok"] and r["error"]["type"] == "bad_args"
    r = ctrl.dispatch("hydra_batch.get_job", {"job_id": "nope"})
    assert not r["ok"]


def test_auto_rmax_is_one_value_shared_by_every_panel(ctrl, tmp_path):
    from midas_gui.helpers import rmax_corner_px
    r = ctrl.dispatch("hydra_batch.set_scans", {"paths": [_panel_files()],
                                                "output_dir": str(tmp_path / "o")})
    assert r["ok"], r
    assert sorted(r["result"]["scans"][0]["panels"]) == ["ge1", "ge2", "ge3", "ge4"]
    cal = _calib_set(tmp_path, "c1")
    assert ctrl.dispatch("hydra_batch.set_calibration", {"files": cal})["ok"]
    assert ctrl.dispatch("hydra_batch.set_caking", {"r_max": 0, "r_bin": 1.0})["ok"]

    page = ctrl._hb_page()
    own = {n: rmax_corner_px(BCS[n][0], BCS[n][1], 256, 256) for n in BCS}
    assert len({round(v, 3) for v in own.values()}) == 4        # the bug's precondition
    shared = page.shared_auto_rmax()
    assert shared == pytest.approx(max(own.values()))
    rmaxes = {float(page._spec_for(n).RMax) for n in BCS}
    assert len(rmaxes) == 1, f"panels disagree on Rmax: {rmaxes}"
    assert next(iter(rmaxes)) == pytest.approx(shared)
    # previewing a panel must not write a per-panel radius back into the card
    for n in BCS:
        page._refresh_detector_preview(n)
        assert page._cards[n].caking()["R_MAX"] == 0.0

    # an explicit Rmax on a card still wins over the shared auto value
    page._cards[2].set_caking({"R_MAX": 77.0})
    assert float(page._spec_for(2).RMax) == pytest.approx(77.0)
    page._cards[2].set_caking({"R_MAX": 0.0})


def test_run_without_scans_or_formats_is_a_precondition_error(ctrl):
    page = ctrl._hb_page()
    page._loader._selection = []
    page._loader._siblings = {}
    r = ctrl.dispatch("hydra_batch.run", {})
    assert not r["ok"] and r["error"]["type"] == "precondition_failed"


def test_run_then_queue_with_two_calibration_sets(ctrl, qapp, tmp_path):
    out1 = tmp_path / "scan1"
    assert ctrl.dispatch("hydra_batch.set_scans", {"paths": [_panel_files()],
                                                   "output_dir": str(out1)})["ok"]
    cal1 = _calib_set(tmp_path, "c1")
    assert ctrl.dispatch("hydra_batch.set_calibration", {"files": cal1})["ok"]
    assert ctrl.dispatch("hydra_batch.set_caking", {"r_max": 0, "r_bin": 1.0})["ok"]
    r = ctrl.dispatch("hydra_batch.set_options", {
        "formats": ["csv", "zarr"], "combine_zarr": True, "run_mode": "sequential",
        "zarr_grouping": "frame"})
    assert r["ok"], r

    run = ctrl.dispatch("hydra_batch.run", {})
    assert run["ok"], run
    res = _wait(ctrl, qapp, run["result"]["job_id"])
    assert res["status"] == "done", res
    sc = res["result"]["scans"][0]
    assert {k: v["status"] for k, v in sc["panels"].items()} == {f"ge{n}": "done" for n in (1, 2, 3, 4)}
    assert sc["combine"]["wrote"] == sc["combine"]["total"] >= 1
    assert not sc["combine"]["errors"]
    assert list(out1.glob("*ge1234*.zarr.zip")), "merged archive missing"

    # queue: two datasets, each with its own calibration set (different BC)
    cal2 = _calib_set(tmp_path, "c2", dbc=3.0)
    jobs = [{"name": "A", "scans": [_panel_files()], "output_dir": str(tmp_path / "qA"),
             "calibration": {"files": cal1}},
            {"name": "B", "scans": [_panel_files()], "output_dir": str(tmp_path / "qB"),
             "calibration": {"files": cal2}}]
    q = ctrl.dispatch("hydra_batch.run_queue", {"jobs": jobs})
    assert q["ok"], q
    res = _wait(ctrl, qapp, q["result"]["job_id"])
    assert res["status"] == "done", res
    assert [j["status"] for j in res["result"]["jobs"]] == ["done", "done"]
    assert list((tmp_path / "qA").glob("*ge1234*.zarr.zip"))
    assert list((tmp_path / "qB").glob("*ge1234*.zarr.zip"))
    # the queue really used each job's calibration
    st = ctrl.dispatch("hydra_batch.get_state", {})["result"]
    assert st["panels"]["ge1"]["calibration_file"] == cal2["ge1"]


def test_queue_reports_a_failing_job_and_continues(ctrl, qapp, tmp_path):
    cal = _calib_set(tmp_path, "c1")
    bad = dict(cal, ge1=str(tmp_path / "missing.txt"))
    jobs = [{"name": "bad", "scans": [_panel_files()], "output_dir": str(tmp_path / "b"),
             "calibration": {"files": bad}},
            {"name": "good", "scans": [_panel_files()], "output_dir": str(tmp_path / "g"),
             "calibration": {"files": cal}}]
    q = ctrl.dispatch("hydra_batch.run_queue", {"jobs": jobs})
    assert q["ok"], q
    res = _wait(ctrl, qapp, q["result"]["job_id"])
    sts = {j["name"]: j["status"] for j in res["result"]["jobs"]}
    assert sts == {"bad": "failed", "good": "done"}
    assert res["status"] == "failed"
