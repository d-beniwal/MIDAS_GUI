"""``mask.*`` control-API ops (the Mask Builder tab for an MCP agent).

One real ``MainWindow`` for the file. Every ``QMessageBox`` entry point fails the
test: the ops must record dialogs, never open them (a modal would block the
control socket). Synthetic frames only.
"""
from __future__ import annotations

import time

import numpy as np
import pytest

tifffile = pytest.importorskip("tifffile")


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
        raise AssertionError("a modal QMessageBox was opened")
    for name in ("information", "critical", "warning", "question"):
        monkeypatch.setattr(QtWidgets.QMessageBox, name, staticmethod(_boom))
    c = GuiController(win)
    t = win._mask_tab
    t._clear_shapes()
    t._computed_mask = None
    t._mask = None
    yield c
    win._set_project_path(None)
    win._set_workspace_dirty(False)


def _rng_frame(seed=0, shape=(48, 64), hot=(), dead=(), base=1000.0):
    rng = np.random.default_rng(seed)
    f = rng.poisson(base, shape).astype(np.int32)
    for r, c in hot:
        f[r, c] = int(base * 20)
    for r, c in dead:
        f[r, c] = 0
    return f


@pytest.fixture
def frames(tmp_path):
    d = tmp_path / "frames"
    d.mkdir()
    for i in range(5):
        tifffile.imwrite(d / f"f{i:02d}.tif", _rng_frame(i, hot=[(10, 20)], dead=[(30, 40)]))
    return d


def _run(ctrl, win, op, **kw):
    r = ctrl.dispatch(op, kw)
    assert r["ok"], r
    return r["result"]


def _wait(ctrl, win, job_id, timeout=120):
    from PyQt5 import QtWidgets
    t0 = time.time()
    while time.time() - t0 < timeout:
        QtWidgets.QApplication.processEvents()
        job = _run(ctrl, win, "mask.get_job", job_id=job_id)
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("mask job still running")


def test_state_reports_every_control_group(ctrl):
    st = _run(ctrl, None, "mask.get_state")
    for key in ("image", "threshold", "statistical", "stack", "spike", "cosmic_ray", "calibration",
                "azimuthal", "learnable", "postprocess", "display", "mask", "shapes", "running"):
        assert key in st, key


def test_setters_drive_the_real_widgets(ctrl, win, frames):
    t = win._mask_tab
    _run(ctrl, win, "mask.set_image", path=str(frames))
    r = _run(ctrl, win, "mask.set_threshold", lower=5, upper=123456, projection="Max", skip_frames=1)
    assert (t._lower.value(), t._upper.value()) == (5, 123456)
    assert t._thresh_proj_combo.currentText() == "Max" and t._thresh_skip.value() == 1
    assert r["threshold"]["projection"] == "Max"
    _run(ctrl, win, "mask.set_statistical", spatial=True, temporal=True, k_sigma=4, hot_factor=1.25,
         dead_factor=0.8, frozen_frac=0.1)
    assert t._spatial_check.isChecked() and t._temporal_check.isChecked()
    assert (t._k_sigma.value(), t._hot_factor.value(), t._dead_factor.value(),
            t._frozen_frac.value()) == (4, 1.25, 0.8, 0.1)
    _run(ctrl, win, "mask.set_spike", enabled=True, n_sigma=7)
    _run(ctrl, win, "mask.set_cosmic_ray", enabled=True, n_sigma=9)
    _run(ctrl, win, "mask.set_postprocess", dilation=2)
    assert t._spike_check.isChecked() and t._spike_sigma.value() == 7
    assert t._cosmic_check.isChecked() and t._cosmic_sigma.value() == 9
    assert t._dilation_spin.value() == 2
    _run(ctrl, win, "mask.set_stack", path=str(frames), stride=2)
    assert t._stack_ed.text() == str(frames) and t._stride_spin.value() == 2
    files = sorted(str(p) for p in frames.glob("*.tif"))[:3]
    r = _run(ctrl, win, "mask.set_stack", files=files)
    assert r["stack"]["explicit_files"] == 3
    _run(ctrl, win, "mask.set_stack", path="")
    assert t._stack_ed.text() == "" and not t._stack_files
    _run(ctrl, win, "mask.set_display", show_overlay=False, log=True)
    assert not t._show_overlay_check.isChecked() and t._viewer._log.isChecked()
    for off in ("set_spike", "set_cosmic_ray"):
        _run(ctrl, win, "mask." + off, enabled=False)
    _run(ctrl, win, "mask.set_statistical", spatial=False, temporal=False)
    _run(ctrl, win, "mask.set_postprocess", dilation=0)
    _run(ctrl, win, "mask.set_display", show_overlay=True, log=False)


def test_bad_arguments_are_structured_errors(ctrl, win, tmp_path):
    assert ctrl.dispatch("mask.set_image", {"path": str(tmp_path / "nope.tif")})["error"]["type"] == "bad_args"
    f = tmp_path / "one.tif"
    tifffile.imwrite(f, _rng_frame())
    _run(ctrl, win, "mask.set_image", path=str(f))
    assert ctrl.dispatch("mask.set_threshold", {"projection": "Max"})["error"]["type"] == "precondition_failed"
    assert ctrl.dispatch("mask.set_threshold", {"projection": "Median"})["error"]["type"] == "bad_args"
    assert ctrl.dispatch("mask.set_frame", {"frame": 1})["error"]["type"] == "precondition_failed"
    assert ctrl.dispatch("mask.set_postprocess", {"dilation": 99})["error"]["type"] == "bad_args"
    assert ctrl.dispatch("mask.set_calibration_methods", {"azimuthal": True})["error"]["type"] == "precondition_failed"
    assert ctrl.dispatch("mask.set_display", {"cmap": "no-such-map"})["error"]["type"] == "bad_args"
    assert ctrl.dispatch("mask.add_shape", {"kind": "star"})["error"]["type"] == "bad_args"
    assert ctrl.dispatch("mask.add_points", {"points": [[9999, 9999]]})["error"]["type"] == "bad_args"
    assert ctrl.dispatch("mask.apply_shapes", {})["error"]["type"] == "precondition_failed"


def test_threshold_only_compute_is_synchronous(ctrl, win, tmp_path):
    f = tmp_path / "one.tif"
    img = _rng_frame(dead=[(5, 6), (7, 8)])
    tifffile.imwrite(f, img)
    _run(ctrl, win, "mask.set_image", path=str(f))
    _run(ctrl, win, "mask.set_threshold", lower=0, upper=10_000_000)
    job = _run(ctrl, win, "mask.compute")
    res = _wait(ctrl, win, job["job_id"], timeout=10)
    assert res["status"] == "done" and res["result"]["n_masked"] == 2
    arr = np.load(_run(ctrl, win, "mask.get_array")["array"]["path"])
    assert arr.shape == img.shape and arr[5, 6] == 1 and arr.sum() == 2


def test_frame_navigation_and_projection(ctrl, win, frames):
    r = _run(ctrl, win, "mask.set_image", path=str(frames), frame=3)
    assert r["image"]["n_frames"] == 5 and r["image"]["frame"] == 3
    r = _run(ctrl, win, "mask.set_frame", frame=5)
    assert r["image"]["frame"] == 5
    assert ctrl.dispatch("mask.set_frame", {"frame": 6})["error"]["type"] == "bad_args"


def test_statistical_compute_finds_hot_and_dead_through_the_worker(ctrl, win, frames):
    _run(ctrl, win, "mask.set_image", path=str(frames))
    _run(ctrl, win, "mask.set_threshold", lower=0, upper=10_000_000, projection="Max")
    _run(ctrl, win, "mask.set_statistical", spatial=True, k_sigma=6, hot_factor=1.5, dead_factor=0.5)
    _run(ctrl, win, "mask.set_stack", path=str(frames))
    job = _run(ctrl, win, "mask.compute")
    res = _wait(ctrl, win, job["job_id"])
    assert res["status"] == "done", res
    arr = np.load(_run(ctrl, win, "mask.get_array")["array"]["path"])
    assert arr[10, 20] == 1 and arr[30, 40] == 1
    assert 2 <= arr.sum() < 20
    _run(ctrl, win, "mask.set_statistical", spatial=False)


def test_temporal_without_a_stack_is_reported_as_skipped(ctrl, win, tmp_path):
    f = tmp_path / "one.tif"
    tifffile.imwrite(f, _rng_frame())
    _run(ctrl, win, "mask.set_image", path=str(f))
    _run(ctrl, win, "mask.set_stack", path="")
    _run(ctrl, win, "mask.set_threshold", lower=0, upper=10_000_000)
    _run(ctrl, win, "mask.set_statistical", spatial=False, temporal=True)
    job = _run(ctrl, win, "mask.compute")
    res = _wait(ctrl, win, job["job_id"])
    assert res["status"] == "done"
    assert any("skipped" in w for w in res["result"]["warnings"]), res["result"]
    _run(ctrl, win, "mask.set_statistical", temporal=False)


def test_shapes_points_apply_clear(ctrl, win, tmp_path):
    f = tmp_path / "one.tif"
    img = _rng_frame(shape=(80, 100))
    tifffile.imwrite(f, img)
    _run(ctrl, win, "mask.set_image", path=str(f))
    _run(ctrl, win, "mask.add_shape", kind="rectangle", center=[20, 30], size=[10, 6])
    _run(ctrl, win, "mask.add_shape", kind="circle", center=[70, 40], radius=5)
    _run(ctrl, win, "mask.add_shape", kind="polygon", points=[[5, 60], [25, 60], [15, 75]])
    _run(ctrl, win, "mask.add_shape", kind="annulus", center=[60, 20], r_outer=10, r_inner=5)
    _run(ctrl, win, "mask.add_shape", kind="oval", center=[85, 70], size=[8, 4], angle=30)
    listed = _run(ctrl, win, "mask.add_points", points=[[1, 1], [2, 2]])
    assert len(listed["shapes"]) == 5 and len(listed["points"]) == 2
    res = _run(ctrl, win, "mask.apply_shapes")
    m = np.load(_run(ctrl, win, "mask.get_array")["array"]["path"])
    assert m[1, 1] == 1 and m[2, 2] == 1                       # picked points
    assert m[30, 20] == 1 and m[40, 70] == 1                   # rectangle / circle centres
    assert m[20, 60] == 0 and m[20, 52] == 1                   # annulus: hole in the middle, ring masked
    assert 150 < res["mask"]["n_hand_drawn"] < 1100
    # rectangle covers ~10x6 pixels
    rect_only = m[27:34, 14:27]
    assert rect_only.sum() >= 55
    _run(ctrl, win, "mask.remove_shape", index=0)
    assert len(_run(ctrl, win, "mask.list_shapes")["shapes"]) == 4
    _run(ctrl, win, "mask.clear_shapes")
    assert not _run(ctrl, win, "mask.get_state")["mask"]["has_mask"]
    assert _run(ctrl, win, "mask.list_shapes") == {"shapes": [], "points": [], "frame": "raw"}


def test_shapes_follow_the_display_transform_but_mask_stays_raw(ctrl, win, tmp_path):
    f = tmp_path / "one.tif"
    tifffile.imwrite(f, _rng_frame(shape=(60, 90)))
    _run(ctrl, win, "mask.set_image", path=str(f))
    t = win._mask_tab
    t.set_display_transform((1, 3))            # flip + transpose: painted frame differs from raw
    try:
        _run(ctrl, win, "mask.add_shape", kind="rectangle", center=[80, 10], size=[6, 6])
        _run(ctrl, win, "mask.add_shape", kind="polygon", points=[[10, 40], [20, 40], [15, 50]])
        _run(ctrl, win, "mask.add_points", points=[[3, 4]])
        _run(ctrl, win, "mask.apply_shapes")
        m = np.load(_run(ctrl, win, "mask.get_array")["array"]["path"])
    finally:
        t.set_display_transform(())
    assert m.shape == (60, 90)
    assert m[10, 80] == 1 and m[4, 3] == 1 and m[44, 15] == 1


def test_save_load_and_project_round_trip(ctrl, win, tmp_path):
    f = tmp_path / "one.tif"
    tifffile.imwrite(f, _rng_frame(dead=[(3, 4)]))
    _run(ctrl, win, "mask.set_image", path=str(f))
    _run(ctrl, win, "mask.set_threshold", lower=0, upper=10_000_000)
    _wait(ctrl, win, _run(ctrl, win, "mask.compute")["job_id"], timeout=10)
    out = tmp_path / "m.tif"
    saved = _run(ctrl, win, "mask.save", path=str(out))
    assert out.is_file() and saved["n_masked"] == 1
    assert ctrl.dispatch("mask.log_to_project", {})["error"]["type"] == "precondition_failed"
    # load into a cleared mask; a wrong-shape mask is refused
    win._mask_tab._computed_mask = None
    r = _run(ctrl, win, "mask.load_existing", path=str(out))
    assert r["mask"]["n_masked"] == 1
    wrong = tmp_path / "wrong.tif"
    tifffile.imwrite(wrong, np.zeros((5, 5), np.uint8))
    assert ctrl.dispatch("mask.load_existing", {"path": str(wrong)})["error"]["type"] == "bad_args"
    # project: save, then log an attempt
    assert _run(ctrl, win, "project.save", path=str(tmp_path / "p"))["path"].endswith("p.h5")
    logged = _run(ctrl, win, "mask.log_to_project")
    assert "attempt" in logged["attempt"]


def test_restored_state_does_not_dilate_a_second_time(win, tmp_path):
    """set_state restored the (already dilated) sidecar mask through _set_mask, growing it again."""
    from PyQt5 import QtWidgets
    t = win._mask_tab
    t0 = time.time()
    while t._mask_worker is not None and t._mask_worker.isRunning() and time.time() - t0 < 30:
        QtWidgets.QApplication.processEvents(); time.sleep(0.02)
    f = tmp_path / "one.tif"
    tifffile.imwrite(f, _rng_frame(dead=[(20, 20)]))
    t._img_edit.setText(str(f)); t._load_image()
    t._dilation_spin.setValue(2)
    t._lower.setValue(0); t._upper.setValue(10_000_000)
    t._compute()
    n_before = int(t._mask.sum())
    assert n_before == 25
    stem = str(tmp_path / "state")
    state = t.get_state(sidecar_stem=stem)
    t._set_mask(None)
    t.set_state(state, sidecar_stem=stem)
    assert int(t._mask.sum()) == n_before
    t._dilation_spin.setValue(0)
