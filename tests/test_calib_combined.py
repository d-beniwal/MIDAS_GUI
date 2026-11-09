"""Two-stage calibration must preserve seeds, raw-frame handling and results."""
from types import SimpleNamespace

import numpy as np
import pytest

from midas_gui import calib
from midas_gui.constants import PIPELINES, restored_pipeline_label
from midas_gui.helpers import _apply_im_trans


@pytest.mark.parametrize("codes", [(), (1, 3), (2, 3)])
def test_combined_handoff_and_final_result(monkeypatch, codes):
    import midas_calibrate_v2.pipelines as pipelines
    import midas_calibrate_v2.pipelines.single as single

    image = np.arange(24, dtype=np.float32).reshape(4, 6) + 20
    dark = np.full_like(image, 3)
    original = image.copy()
    seed = {"Lsd": 300_000., "BC_y": 2.5, "BC_z": 1.5,
            "tx": 20., "ty": 7., "tz": -12., "iso_R2": 0.002,
            "Wavelength": 0.18}
    final = dict(seed, Lsd=301_000., ty=7.1, Wavelength=0.181)
    seen = []

    def frozen(v1, img, **kwargs):
        seen.append("frozen")
        np.testing.assert_array_equal(img, _apply_im_trans(image - dark, codes))
        assert v1.Lsd == 250_000.
        return SimpleNamespace(converged=True, n_iter=9,
                               res=SimpleNamespace(unpacked=seed))

    def oneshot(v1, img, *, dark, **kwargs):
        seen.append("one_shot")
        np.testing.assert_array_equal(img, _apply_im_trans(image, codes))
        np.testing.assert_array_equal(dark, _apply_im_trans(np.full_like(image, 3), codes))
        assert v1.Lsd == seed["Lsd"]
        assert (v1.BC_y, v1.BC_z) == (seed["BC_y"], seed["BC_z"])
        assert (v1.tx, v1.ty, v1.tz) == (20., 7., -12.)
        assert v1.p2 == seed["iso_R2"]
        assert v1.Wavelength == 0.18
        assert v1.tolLsd == 1234.
        assert v1.Refine["p2"] is False  # preserve seed even when held fixed
        assert kwargs["n_iter"] == 3 and kwargs["lm_max_iter"] == 77
        assert kwargs["build_residual_corr"] is False
        return SimpleNamespace(unpacked=final, history=[], residual_corr_map=None,
                               post_residual_strain_uE=12.)

    monkeypatch.setattr(pipelines, "iterate_frozen_point_until_stable", frozen)
    monkeypatch.setattr(single, "autocalibrate", oneshot)
    monkeypatch.setattr(calib, "make_seed_safe",
                        lambda *a, **k: pytest.fail("must use the complete supplied seed"))
    cfg = dict(wavelength=0.17, pxY=150., calibrant="CeO2", im_trans=codes,
               manual_seed={"Lsd": 250_000., "BC_y": 3., "BC_z": 2.},
               refine={"Distortion": False}, n_iter=3, lm_max_iter=77,
               tols={"tolLsd": 1234.}, build_residual_corr=False)
    raw = calib.run_pipeline("frozen_point_one_shot", image, dark, cfg)
    ny, nz = calib.effective_pixel_counts(image, codes)
    result = calib.normalize_result(raw, "frozen_point_one_shot", NY=ny, NZ=nz,
                                    pxY=150., pxZ=None, wavelength=0.17)
    assert seen == ["frozen", "one_shot"]
    assert result.Lsd == 301_000. and result.ty == 7.1
    assert result.wavelength_A == 0.181
    assert result.distortion["iso_R2"] == 0.002
    assert result.post_residual_strain_uE == 12.
    assert tuple(result.im_trans) == codes
    assert (result.NrPixelsY, result.NrPixelsZ) == (ny, nz)
    assert result._frozen_point_n_iter == 9
    assert result._one_shot_seed["Lsd"] == 300_000.
    assert cfg["manual_seed"]["Lsd"] == 250_000.
    np.testing.assert_array_equal(image, original)


def test_nonconverged_frozen_point_does_not_start_oneshot(monkeypatch):
    import midas_calibrate_v2.pipelines as pipelines
    monkeypatch.setattr(calib, "_seed_and_v1", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(pipelines, "iterate_frozen_point_until_stable",
                        lambda *a, **k: SimpleNamespace(converged=False))
    with pytest.raises(RuntimeError, match="One-shot stage was not started"):
        calib.run_pipeline("frozen_point_one_shot", np.ones((4, 6)), None,
                           dict(wavelength=0.17, pxY=150., calibrant="CeO2"))


def test_selectable_pipelines_and_legacy_state():
    assert [p[1] for p in PIPELINES] == ["one_shot", "frozen_point", "frozen_point_one_shot"]
    assert restored_pipeline_label("Frozen-point (high-tilt)") == PIPELINES[1][0]
    assert restored_pipeline_label("Four-stage (patchy det.)") == PIPELINES[0][0]
    assert restored_pipeline_label(PIPELINES[2][0]) == PIPELINES[2][0]


def test_combined_windows_cannot_claim_a_whole_run_cap():
    assert calib.limit_recentre_rounds("frozen_point_one_shot", 4) is None
