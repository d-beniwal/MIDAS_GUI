"""``calib.normalize_result`` for the One-shot *reroute*.

``run_pipeline`` sends one_shot through ``pipelines.single.autocalibrate``
instead of ``calibrate()`` whenever calibrate() cannot express what the user
asked for — non-default ``tol*`` windows, Lsd or BC held fixed, or only one of
ty/tz refined. Those two entry points return different objects: a flat
``AutoCalibrationResult`` vs. a v2 ``CalibrationResult`` that carries its
geometry only inside ``.unpacked``. normalize_result used to return the latter
untouched, so every downstream reader of ``result.Lsd`` / ``result.NrPixelsY``
(paramstest export, the parameter grid, spec building) raised AttributeError.
"""
from types import SimpleNamespace

import pytest

from conftest import force_rmtree

from midas_gui import calib


def _rerouted_raw(**extra):
    """The shape ``pipelines.single.autocalibrate`` returns: no flat geometry
    fields, everything in ``.unpacked``."""
    u = {"Lsd": 1_000_472.84, "BC_y": 1430.44, "BC_z": 1342.491,
         "tx": 0.0, "ty": -0.4782, "tz": -0.228, "iso_R2": 1.5e-4}
    raw = SimpleNamespace(
        spec=None, unpacked=u,
        history=[SimpleNamespace(mean_strain_uE=77.3)],
        residual_corr_map=None, post_residual_strain_uE=None, **extra)
    return raw


def test_rerouted_one_shot_normalises_to_a_flat_result():
    result = calib.normalize_result(
        _rerouted_raw(), "one_shot", NY=2880, NZ=2880, pxY=150.0, pxZ=150.0,
        wavelength=0.172979)

    assert result.Lsd == 1_000_472.84
    assert (result.BC_y, result.BC_z) == (1430.44, 1342.491)
    assert (result.ty, result.tz) == (-0.4782, -0.228)
    # The fields whose absence crashed write_standalone_paramstest.
    assert (result.NrPixelsY, result.NrPixelsZ) == (2880, 2880)
    assert result.distortion == {"iso_R2": 1.5e-4}
    # No post_residual_strain_uE on the raw object, so the last iterate's is used.
    assert result.post_residual_strain_uE == 77.3


def test_rerouted_one_shot_keeps_the_frame_it_was_solved_in():
    """run_pipeline pre-flips the image for this branch, so the transform is
    recorded only on ``raw._im_trans``. Dropping it would integrate the result
    in the untransformed frame."""
    raw = _rerouted_raw()
    raw._im_trans = (2, 3)
    result = calib.normalize_result(
        raw, "one_shot", NY=2880, NZ=1440, pxY=150.0, pxZ=None,
        wavelength=0.172979)
    assert tuple(result.im_trans) == (2, 3)


def test_plain_one_shot_result_is_still_passed_through_untouched():
    """calibrate() already returns an AutoCalibrationResult — the new branch
    must not intercept it."""
    flat = SimpleNamespace(Lsd=200_000.0, BC_y=1.0, BC_z=2.0, unpacked={"Lsd": 1.0})
    assert calib.normalize_result(
        flat, "one_shot", NY=10, NZ=10, pxY=200.0, pxZ=None,
        wavelength=0.1729) is flat


# ── A returned residual-map path is a claim, not a fact ──────────────────────
#
# calibrate() mints residual_corr_bin_path from output_dir alone
# (midas_calibrate_v2/pipelines/auto.py:680-683) and returns it at :976 with no
# existence check, while only actually building the map when
# build_residual_corr is on and no panel_layout is set (:702-703). Untick
# "Build residual map", or run any multi-panel fit, and the result names a file
# nothing wrote — which midas_integrate_v2 then treats as fatal.

_SCRATCH: list = []


@pytest.fixture(autouse=True)
def _clean_scratch():
    """Cleaned by fixture rather than ``atexit``: pytest-forked ends each test
    with ``os._exit()``, which skips atexit handlers entirely — the earlier
    version of this helper left a /tmp directory behind on every run."""
    yield
    while _SCRATCH:
        force_rmtree(_SCRATCH.pop())


def _scratch():
    import tempfile
    d = tempfile.mkdtemp(prefix="mg_rcb_")
    _SCRATCH.append(d)
    return d


def test_a_residual_path_naming_no_file_is_cleared_on_the_plain_branch():
    flat = SimpleNamespace(Lsd=200_000.0, BC_y=1.0, BC_z=2.0,
                           residual_corr_bin_path="/nope/residual_corr.bin")
    out = calib.normalize_result(flat, "one_shot", NY=10, NZ=10, pxY=200.0,
                                 pxZ=None, wavelength=0.1729)
    assert out is flat                      # still a pass-through, not a copy
    assert out.residual_corr_bin_path is None


def test_a_residual_path_that_does_exist_survives():
    import os
    p = os.path.join(_scratch(), "residual_corr.bin")
    with open(p, "wb") as f:
        f.write(b"\x00")
    flat = SimpleNamespace(Lsd=200_000.0, BC_y=1.0, BC_z=2.0,
                           residual_corr_bin_path=p)
    out = calib.normalize_result(flat, "one_shot", NY=10, NZ=10, pxY=200.0,
                                 pxZ=None, wavelength=0.1729)
    assert out.residual_corr_bin_path == p


def test_a_result_without_the_attribute_at_all_is_left_alone():
    flat = SimpleNamespace(Lsd=200_000.0, BC_y=1.0, BC_z=2.0)
    out = calib.normalize_result(flat, "one_shot", NY=10, NZ=10, pxY=200.0,
                                 pxZ=None, wavelength=0.1729)
    assert not hasattr(out, "residual_corr_bin_path")
