"""Tests for ``calib.tilt_seed_effective`` — whether a manual tx/ty/tz seed
actually reaches the solver for a given pipeline/config.

This exists to stop Calibrate silently *dropping* a tilt seed the user typed
in: the answer depends on which internal branch ``run_pipeline`` takes, not
just on the pipeline name, so the two have to be kept in step. These tests pin
the branch table so a future edit to one and not the other fails loudly.
"""
import pytest

from midas_gui.calib import tilt_seed_effective
from midas_gui.constants import DISTORTION_NAMES


@pytest.mark.parametrize("mode", ["four_stage", "bayesian", "joint"])
def test_v1_param_pipelines_always_seed_tilts(mode):
    """These build CalibrationParams, which takes tx/ty/tz directly."""
    assert tilt_seed_effective(mode) is True


def test_first_time_never_seeds_tilts():
    """first_time_calibrate() takes no tilt seed at all, at any backend
    version — a warning case, not a maybe."""
    assert tilt_seed_effective("first_time") is False
    assert tilt_seed_effective("first_time", panel_layout=[{"p": 1}]) is False


def test_unknown_mode_is_conservative():
    assert tilt_seed_effective("something_new") is False


def test_one_shot_with_a_panel_layout_routes_through_four_stage():
    assert tilt_seed_effective("one_shot", panel_layout=[{"p": 1}]) is True


def test_one_shot_with_partial_distortion_routes_through_single_autocalibrate():
    """A distortion refinement restricted to a subset of coefficients takes
    the pipelines.single.autocalibrate branch, which does seed tilts."""
    refine = {"Distortion": True, "distortion_coeffs": {"iso_R2", "iso_R4"}}
    assert tilt_seed_effective("one_shot", refine=refine) is True


def test_one_shot_with_all_distortion_coeffs_is_not_the_subset_branch():
    """Refining *every* coefficient is the plain path, not the subset one, so
    the answer must come from the backend signature check instead."""
    refine = {"Distortion": True, "distortion_coeffs": set(DISTORTION_NAMES)}
    assert tilt_seed_effective("one_shot", refine=refine) is \
        _backend_exposes_tilt_seeds()


def test_one_shot_with_distortion_off_is_not_the_subset_branch():
    refine = {"Distortion": False}
    assert tilt_seed_effective("one_shot", refine=refine) is \
        _backend_exposes_tilt_seeds()


def _backend_exposes_tilt_seeds() -> bool:
    """Mirror of the runtime check: plain one_shot calls calibrate() directly,
    whose initial_tx/ty/tz kwargs are dropped unless the installed backend
    actually declares them."""
    try:
        import inspect

        from midas_calibrate_v2 import calibrate
        params = inspect.signature(calibrate).parameters
        return all(f"initial_{k}" in params for k in ("tx", "ty", "tz"))
    except Exception:
        return False


def test_one_shot_checks_the_installed_backend_not_a_hardcoded_answer(monkeypatch):
    """The plain one_shot answer is looked up at call time, so it stays
    correct if a future backend release adds the kwargs."""
    pytest.importorskip("midas_calibrate_v2")
    import midas_calibrate_v2

    def fake_with_seeds(*, initial_tx=None, initial_ty=None, initial_tz=None,
                        **kw):
        raise AssertionError("never called")

    def fake_without_seeds(**kw):
        raise AssertionError("never called")

    monkeypatch.setattr(midas_calibrate_v2, "calibrate", fake_with_seeds)
    assert tilt_seed_effective("one_shot", refine={"Distortion": False}) is True

    monkeypatch.setattr(midas_calibrate_v2, "calibrate", fake_without_seeds)
    assert tilt_seed_effective("one_shot", refine={"Distortion": False}) is False


def test_one_shot_defaults_when_no_refine_dict_given():
    """refine=None must behave like the legacy all-coefficients default
    (Distortion on, every coeff) rather than raising."""
    assert tilt_seed_effective("one_shot") is _backend_exposes_tilt_seeds()
