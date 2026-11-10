"""Which calibration pipelines honour ``cfg["rings_to_exclude"]``.

The Calibrate tab's "Rings…" picker stores unticked ring numbers in
``cfg["rings_to_exclude"]``; ``calib.run_pipeline`` has to turn them into
``spec.rings_to_exclude`` for the backend. Backend pipelines that take a ``spec``
(one_shot-rerouted, four_stage, bayesian, joint) must receive it; the two that
build their own spec internally (first_time, frozen_point) cannot, and must say
so rather than silently using every ring.

Backend entry points are replaced with capturing fakes, so this pins the GUI
wiring only — the filtering itself is the backend's (``[e-step] ring table``).
"""
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("midas_calibrate_v2")

from midas_gui import calib

EXCL = [1, 2, 5]
SEED = {"BC_y": 50.0, "BC_z": 50.0, "Lsd": 1_000_000.0}


def _cfg(**extra):
    cfg = {
        "wavelength": 0.1729, "pxY": 200.0, "calibrant": "CeO2",
        "refine": {}, "n_iter": 1, "lm_max_iter": 5, "device": "cpu",
        "im_trans": (), "manual_seed": dict(SEED),
        "rings_to_exclude": EXCL, "build_residual_corr": False,
    }
    cfg.update(extra)
    return cfg


IMG = np.zeros((100, 100), dtype=np.float32)


def _capture(monkeypatch, name):
    """Replace ``midas_calibrate_v2.pipelines.<name>``; record its kwargs."""
    import midas_calibrate_v2.pipelines as P
    seen = {}

    def fake(*a, **k):
        seen["args"], seen["kwargs"] = a, k
        return SimpleNamespace(converged=True)

    monkeypatch.setattr(P, name, fake, raising=False)
    return seen


@pytest.mark.parametrize("mode,fn", [
    ("four_stage", "autocalibrate_four_stage"),
    ("bayesian", "autocalibrate_bayesian"),
    ("joint", "autocalibrate_joint"),
])
def test_spec_pipelines_receive_the_exclusions(monkeypatch, mode, fn):
    seen = _capture(monkeypatch, fn)
    calib.run_pipeline(mode, IMG, None, _cfg())
    assert seen["kwargs"]["spec"].rings_to_exclude == EXCL


@pytest.mark.parametrize("mode,fn", [
    ("four_stage", "autocalibrate_four_stage"),
    ("bayesian", "autocalibrate_bayesian"),
    ("joint", "autocalibrate_joint"),
])
def test_no_exclusions_leaves_the_spec_alone(monkeypatch, mode, fn):
    seen = _capture(monkeypatch, fn)
    calib.run_pipeline(mode, IMG, None, _cfg(rings_to_exclude=[]))
    assert seen["kwargs"]["spec"] is None


def test_one_shot_reroutes_through_autocalibrate_with_spec(monkeypatch):
    """calibrate() has no exclusion kwarg, so exclusions force the reroute."""
    import midas_calibrate_v2 as top
    import midas_calibrate_v2.pipelines.single as single
    seen = {}

    def fake_auto(*a, **k):
        seen["kwargs"] = k
        return SimpleNamespace()

    monkeypatch.setattr(single, "autocalibrate", fake_auto)
    monkeypatch.setattr(
        top, "calibrate",
        lambda *a, **k: pytest.fail("calibrate() cannot take exclusions"))
    calib.run_pipeline("one_shot", IMG, None, _cfg())
    assert seen["kwargs"]["spec"].rings_to_exclude == EXCL


def test_one_shot_without_exclusions_still_uses_calibrate(monkeypatch):
    import midas_calibrate_v2 as top
    seen = {}

    def fake_calibrate(image, **k):
        seen["kwargs"] = k
        return SimpleNamespace()

    monkeypatch.setattr(top, "calibrate", fake_calibrate)
    calib.run_pipeline("one_shot", IMG, None, _cfg(rings_to_exclude=[]))
    assert "kwargs" in seen


def test_first_time_warns_that_it_ignores_exclusions(monkeypatch, capsys):
    seen = _capture(monkeypatch, "first_time_calibrate")
    calib.run_pipeline("first_time", IMG, None, _cfg())
    assert "ring exclusions are ignored" in capsys.readouterr().out
    assert "rings_to_exclude" not in seen["kwargs"]


def test_frozen_point_warns_that_it_ignores_exclusions(monkeypatch, capsys):
    monkeypatch.setattr(calib, "_seed_and_v1",
                        lambda *a, **k: SimpleNamespace())
    seen = _capture(monkeypatch, "iterate_frozen_point_until_stable")
    calib.run_pipeline("frozen_point", IMG, None, _cfg())
    assert "ring exclusions are not forwarded" in capsys.readouterr().out
    assert "spec" not in seen["kwargs"]


def test_combined_mode_forwards_exclusions_to_both_stage_cfgs(monkeypatch):
    """frozen_point_one_shot hands the same cfg on to both stages; stage 2
    (one_shot) is the one that can act on it, stage 1 warns (tested above)."""
    stage_cfgs = []
    real = calib.run_pipeline

    def spy(mode, image, dark, cfg):
        stage_cfgs.append((mode, list(cfg.get("rings_to_exclude") or ())))
        if mode == "frozen_point_one_shot":
            return real(mode, image, dark, cfg)
        # Stop the real backends: hand back a converged stage-1 result.
        u = {k: 0.0 for k in ("Lsd", "BC_y", "BC_z", "tx", "ty", "tz")}
        return SimpleNamespace(converged=True, res=SimpleNamespace(unpacked=u))

    monkeypatch.setattr(calib, "run_pipeline", spy)
    spy("frozen_point_one_shot", IMG, None, _cfg())
    modes = dict(stage_cfgs)
    assert modes["frozen_point"] == EXCL
    assert modes["one_shot"] == EXCL


def test_ring_rows_use_the_backend_labels():
    """Picker rows are the backend's own table: labels skip merged numbers."""
    rows = calib.calibrant_ring_rows(
        "CeO2", wavelength=0.1729, pxY=200.0, pxZ=None, NY=2048, NZ=2048,
        lsd_um=1_000_000.0, bc_y=1024.0, bc_z=1024.0)
    nrs = [r["ring_nr"] for r in rows]
    assert nrs == sorted(set(nrs)) and len(nrs) > 3
    assert {"hkl", "d", "two_theta", "radius_px"} <= set(rows[0])
