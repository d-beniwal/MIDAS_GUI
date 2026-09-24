"""``calib.run_pipeline``'s per-parameter manual seed: the sparse
``manual_seed`` cfg dict (only the parameters the GUI's seed panel ticked
"include in seed" are present) must reach each backend correctly, mixing in
an automatic seed for whatever's missing rather than either widening to
"everything" or leaving a gap silently unfilled.

Also pins the distortion-refinement simplification: ``calibrate()`` accepts
``refine_distortion`` as an explicit coefficient list (confirmed against the
installed ``midas_calibrate_v2`` — see ``forward.distortion.resolve_distortion_block``,
which it calls internally), so a partial selection must reach it as that list
verbatim, not be widened to "refine all 15" the way a bare bool would.
"""
from types import SimpleNamespace

import numpy as np
import pytest

from midas_gui import calib


def _seed(bc_y=500.0, bc_z=510.0, lsd_um=1_234_000.0):
    return SimpleNamespace(BC_y=bc_y, BC_z=bc_z, Lsd_um=lsd_um)


# ── calib._resolve_seed (v1-native pipelines: four_stage / bayesian / joint /
#    one_shot+panel_layout) ────────────────────────────────────────────────

def test_resolve_seed_uses_manual_values_with_no_auto_seed_call(monkeypatch):
    calls = []
    monkeypatch.setattr(calib, "make_seed_safe",
                        lambda *a, **k: calls.append(1) or _seed())
    manual = {"BC_y": 1.0, "BC_z": 2.0, "Lsd": 3.0, "tx": 4.0}
    out = calib._resolve_seed(manual, np.zeros((4, 4)), 0.17, 150.0, "CeO2")
    assert out == {"BC_y": 1.0, "BC_z": 2.0, "Lsd": 3.0, "tx": 4.0}
    assert calls == [], "BC+Lsd both manual — no auto-seed needed"


def test_resolve_seed_fills_lsd_from_auto_when_only_bc_is_manual(monkeypatch):
    monkeypatch.setattr(calib, "make_seed_safe", lambda *a, **k: _seed(lsd_um=999.0))
    manual = {"BC_y": 1.0, "BC_z": 2.0}
    out = calib._resolve_seed(manual, np.zeros((4, 4)), 0.17, 150.0, "CeO2")
    assert out["BC_y"] == 1.0 and out["BC_z"] == 2.0
    assert out["Lsd"] == 999.0


def test_resolve_seed_fills_bc_from_auto_when_only_lsd_is_manual(monkeypatch):
    monkeypatch.setattr(calib, "make_seed_safe", lambda *a, **k: _seed(bc_y=7.0, bc_z=8.0))
    manual = {"Lsd": 42.0}
    out = calib._resolve_seed(manual, np.zeros((4, 4)), 0.17, 150.0, "CeO2")
    assert out["Lsd"] == 42.0
    assert out["BC_y"] == 7.0 and out["BC_z"] == 8.0


def test_resolve_seed_all_automatic_with_no_manual_seed(monkeypatch):
    monkeypatch.setattr(calib, "make_seed_safe", lambda *a, **k: _seed(1.0, 2.0, 3.0))
    out = calib._resolve_seed(None, np.zeros((4, 4)), 0.17, 150.0, "CeO2")
    assert out == {"BC_y": 1.0, "BC_z": 2.0, "Lsd": 3.0}


def test_resolve_seed_returns_none_when_auto_seed_fails_and_needed(monkeypatch):
    monkeypatch.setattr(calib, "make_seed_safe", lambda *a, **k: None)
    assert calib._resolve_seed(None, np.zeros((4, 4)), 0.17, 150.0, "CeO2") is None
    assert calib._resolve_seed({"BC_y": 1.0}, np.zeros((4, 4)), 0.17, 150.0, "CeO2") is None


def test_resolve_seed_does_not_need_auto_seed_when_bc_and_lsd_given_even_if_it_would_fail(monkeypatch):
    monkeypatch.setattr(calib, "make_seed_safe",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be called")))
    manual = {"BC_y": 1.0, "BC_z": 2.0, "Lsd": 3.0}
    out = calib._resolve_seed(manual, np.zeros((4, 4)), 0.17, 150.0, "CeO2")
    assert out["BC_y"] == 1.0


def test_resolve_seed_carries_distortion_seed_through():
    manual = {"BC_y": 1.0, "BC_z": 2.0, "Lsd": 3.0, "distortion": {"iso_R2": 0.1}}
    out = calib._resolve_seed(manual, np.zeros((4, 4)), 0.17, 150.0, "CeO2")
    assert out["distortion"] == {"iso_R2": 0.1}


# ── Plain one_shot: calibrate() kwargs ──────────────────────────────────────

def _cfg(**over):
    cfg = dict(wavelength=0.17, pxY=150.0, calibrant="CeO2", refine={}, n_iter=1,
              device="cpu")
    cfg.update(over)
    return cfg


@pytest.fixture
def spy_calibrate(monkeypatch):
    """Capture kwargs handed to the plain calibrate() one_shot path, with a
    full initial_* signature so _supported_kwargs drops nothing."""
    seen = {}

    def fake(image, **kwargs):
        seen.update(kwargs)
        return "RESULT"
    fake.__name__ = "calibrate"

    import inspect
    # Give the fake the same initial_* parameter names as the real backend so
    # _supported_kwargs (which inspects the signature) does not drop them.
    fake.__signature__ = inspect.Signature(parameters=[
        inspect.Parameter("image", inspect.Parameter.POSITIONAL_OR_KEYWORD),
        inspect.Parameter("kwargs", inspect.Parameter.VAR_KEYWORD),
    ])
    # _supported_kwargs treats **kwargs as "accepts everything", which is what
    # we want here — the real backend's own signature is pinned separately by
    # test_calib_tilt_seed.py.
    import midas_calibrate_v2
    monkeypatch.setattr(midas_calibrate_v2, "calibrate", fake)
    return seen


def test_bc_and_lsd_both_manual_bypass_auto_seed(spy_calibrate, monkeypatch):
    monkeypatch.setattr(calib, "make_seed_safe",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be called")))
    img = np.zeros((4, 4), dtype=np.float32)
    cfg = _cfg(manual_seed={"BC_y": 1.0, "BC_z": 2.0, "Lsd": 3.0})
    calib.run_pipeline("one_shot", img, None, cfg)
    assert spy_calibrate["initial_BC_y"] == 1.0
    assert spy_calibrate["initial_BC_z"] == 2.0
    assert spy_calibrate["initial_Lsd"] == 3.0


def test_bc_only_gets_a_plausible_lsd_from_auto_seed_not_the_1m_default(spy_calibrate, monkeypatch):
    monkeypatch.setattr(calib, "make_seed_safe", lambda *a, **k: _seed(lsd_um=13_500_000.0))
    img = np.zeros((4, 4), dtype=np.float32)
    cfg = _cfg(manual_seed={"BC_y": 1.0, "BC_z": 2.0})
    calib.run_pipeline("one_shot", img, None, cfg)
    assert spy_calibrate["initial_BC_y"] == 1.0
    assert spy_calibrate["initial_Lsd"] == 13_500_000.0


def test_lsd_only_does_not_call_auto_seed_itself(spy_calibrate, monkeypatch):
    """calibrate()'s own auto-seeder determines BC using the Lsd hint — no
    pre-emptive make_seed_safe() call is needed at this layer."""
    monkeypatch.setattr(calib, "make_seed_safe",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be called")))
    img = np.zeros((4, 4), dtype=np.float32)
    cfg = _cfg(manual_seed={"Lsd": 42.0})
    calib.run_pipeline("one_shot", img, None, cfg)
    assert spy_calibrate["initial_Lsd"] == 42.0
    assert "initial_BC_y" not in spy_calibrate
    assert "initial_BC_z" not in spy_calibrate


def test_neither_bc_nor_lsd_manual_passes_no_seed_kwargs(spy_calibrate):
    img = np.zeros((4, 4), dtype=np.float32)
    calib.run_pipeline("one_shot", img, None, _cfg())
    assert "initial_BC_y" not in spy_calibrate
    assert "initial_Lsd" not in spy_calibrate


def test_partial_tilt_seed_is_independent(spy_calibrate):
    img = np.zeros((4, 4), dtype=np.float32)
    cfg = _cfg(manual_seed={"ty": 1.5})
    calib.run_pipeline("one_shot", img, None, cfg)
    assert spy_calibrate["initial_ty"] == 1.5
    assert "initial_tx" not in spy_calibrate
    assert "initial_tz" not in spy_calibrate


# ── Partial distortion selection reaches calibrate() as an explicit list ──

def test_partial_distortion_reaches_calibrate_as_a_list_not_widened_to_all(spy_calibrate):
    img = np.zeros((4, 4), dtype=np.float32)
    coeffs = {"iso_R2", "a2", "phi2"}
    cfg = _cfg(refine={"Distortion": True, "distortion_coeffs": coeffs})
    calib.run_pipeline("one_shot", img, None, cfg)
    assert spy_calibrate["refine_distortion"] == sorted(coeffs)


def test_distortion_off_passes_false(spy_calibrate):
    img = np.zeros((4, 4), dtype=np.float32)
    cfg = _cfg(refine={"Distortion": False})
    calib.run_pipeline("one_shot", img, None, cfg)
    assert spy_calibrate["refine_distortion"] is False


def test_all_15_coefficients_still_reach_calibrate_as_the_full_list(spy_calibrate):
    from midas_gui.constants import DISTORTION_NAMES
    img = np.zeros((4, 4), dtype=np.float32)
    cfg = _cfg(refine={"Distortion": True, "distortion_coeffs": set(DISTORTION_NAMES)})
    calib.run_pipeline("one_shot", img, None, cfg)
    assert spy_calibrate["refine_distortion"] == sorted(DISTORTION_NAMES)
