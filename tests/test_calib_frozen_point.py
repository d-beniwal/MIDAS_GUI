"""``calib.run_pipeline``/``normalize_result`` dispatch for the "frozen_point"
(high-tilt) pipeline (``midas_calibrate_v2.pipelines.
iterate_frozen_point_until_stable``). The actual fit's numerical behavior is
covered by tests/test_frozen_point_vendor.py against the real package
directly — these tests only pin the GUI-side wiring: panel_layout is
rejected, dark is subtracted before the pipeline sees the image (point_pick
has no native dark argument), and the raw IterateResult normalizes into a
proper AutoCalibrationResult.
"""
from types import SimpleNamespace

import numpy as np
import pytest

from midas_gui import calib

# Two of the tests below (dispatch/dark-subtract and the non-cpu log note)
# monkeypatch midas_calibrate_v2.pipelines.iterate_frozen_point_until_stable —
# a real attribute only on releases newer than the 0.17.0 environment.yml
# currently pins (see tests/test_frozen_point_vendor.py for the same gap).
# Skip just those two rather than the whole module: the rest (panel_layout
# rejection, result normalization) don't touch that import.
_needs_frozen_point_pipeline = pytest.mark.skipif(
    getattr(__import__("midas_calibrate_v2.pipelines", fromlist=["pipelines"]),
            "iterate_frozen_point_until_stable", None) is None,
    reason="installed midas-calibrate-v2 predates the frozen-point pipeline",
)


def _base_cfg(**extra):
    cfg = {
        "wavelength": 0.1729, "pxY": 200.0, "calibrant": "CeO2",
        "refine": {}, "n_iter": 4, "lm_max_iter": 150, "device": "cpu",
        "im_trans": (),
    }
    cfg.update(extra)
    return cfg


def test_frozen_point_rejects_panel_layout():
    image = np.zeros((10, 10), dtype=np.float32)
    cfg = _base_cfg(panel_layout={"n_y": 2, "n_z": 2, "sy": 5, "sz": 5})
    with pytest.raises(RuntimeError, match="Multi-panel"):
        calib.run_pipeline("frozen_point", image, None, cfg)


@_needs_frozen_point_pipeline
def test_frozen_point_subtracts_dark_and_dispatches(monkeypatch):
    image = np.full((4, 4), 10.0, dtype=np.float32)
    dark = np.full((4, 4), 3.0, dtype=np.float32)
    dark[0, 0] = 50.0   # exceeds the pixel there — must clip at 0, not go negative

    fake_v1 = SimpleNamespace(name="fake_v1")
    monkeypatch.setattr(calib, "_seed_and_v1", lambda *a, **k: fake_v1)

    captured = {}

    def fake_iterate(v1, img, *, lm_max_iter, verbose):
        captured["v1"] = v1
        captured["img"] = img.copy()
        captured["lm_max_iter"] = lm_max_iter
        captured["verbose"] = verbose
        return "sentinel-result"

    import midas_calibrate_v2.pipelines as mcv2_pipelines
    monkeypatch.setattr(mcv2_pipelines, "iterate_frozen_point_until_stable", fake_iterate)

    cfg = _base_cfg(lm_max_iter=77)
    out = calib.run_pipeline("frozen_point", image, dark, cfg)

    assert out == "sentinel-result"
    assert captured["v1"] is fake_v1
    assert captured["lm_max_iter"] == 77
    assert captured["verbose"] is True
    np.testing.assert_allclose(captured["img"][1:, 1:], 7.0)
    assert captured["img"][0, 0] == 0.0   # clipped, not negative
    assert (captured["img"] >= 0).all()


@_needs_frozen_point_pipeline
def test_frozen_point_logs_note_for_non_cpu_device(monkeypatch, capsys):
    image = np.zeros((4, 4), dtype=np.float32)
    monkeypatch.setattr(calib, "_seed_and_v1", lambda *a, **k: SimpleNamespace())
    import midas_calibrate_v2.pipelines as mcv2_pipelines
    monkeypatch.setattr(mcv2_pipelines, "iterate_frozen_point_until_stable",
                        lambda *a, **k: "sentinel-result")

    calib.run_pipeline("frozen_point", image, None, _base_cfg(device="cuda"))
    out = capsys.readouterr().out
    assert "always runs on" in out and "cuda" in out


def test_normalize_frozen_point_result():
    fake_pv = SimpleNamespace(
        unpacked={"Lsd": 200_000.0, "BC_y": 340.0, "BC_z": 360.0,
                  "tx": 0.0, "ty": 2.0, "tz": 5.0},
        history=[SimpleNamespace(mean_strain_uE=12.3)],
    )
    raw = SimpleNamespace(res=fake_pv, converged=True, n_iter=7)

    result = calib.normalize_result(
        raw, "frozen_point", NY=700, NZ=700, pxY=200.0, pxZ=None,
        wavelength=0.1729)

    assert result.Lsd == 200_000.0
    assert (result.BC_y, result.BC_z) == (340.0, 360.0)
    assert (result.ty, result.tz) == (2.0, 5.0)
    assert result.post_residual_strain_uE == 12.3
    assert result._frozen_point_converged is True
    assert result._frozen_point_n_iter == 7
