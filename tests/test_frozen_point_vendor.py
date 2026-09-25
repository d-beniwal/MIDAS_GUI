"""Numerical regression test for the "frozen_point" (high-tilt) calibration
pipeline, now shipped natively by ``midas_calibrate_v2.pipelines`` (this
pipeline used to live in a vendored copy under
``midas_gui/_vendor/frozen_point_calib`` while it was still on an
unreleased upstream branch; that branch merged and shipped as part of
``midas_calibrate_v2``, so midas_gui now calls the real package directly —
see ``midas_gui/calib.py``'s ``frozen_point`` branch).

Adapted (trimmed) from the source branch's own
``packages/midas_calibrate_v2/tests/test_frozen_point.py``: paints clean,
well-separated Gaussian ring spots at a known, deliberately tilted geometry
and checks the pipeline recovers it from both a nearby seed and a genuinely
blind one.

Also covers the distortion-support behavior: both functions defer to
``v1_params.Refine["p0".."p14"]`` for distortion refinement (like every
sibling pipeline) rather than force-freezing all 15 coefficients regardless
of it.
"""
from __future__ import annotations

import numpy as np
import pytest

from midas_calibrate.params import CalibrationParams
from midas_calibrate.rings import build_ring_table

# The frozen-point pipeline moved out of this repo's _vendor/ and into the
# backend, but it only appears in midas-calibrate-v2 releases newer than the
# 0.17.0 environment.yml currently pins. Skip rather than abort collection, the
# same way the midas_integrate dependency below is handled.
_pipelines = pytest.importorskip("midas_calibrate_v2.pipelines")
autocalibrate_frozen_point = getattr(_pipelines, "autocalibrate_frozen_point", None)
iterate_frozen_point_until_stable = getattr(
    _pipelines, "iterate_frozen_point_until_stable", None)
if autocalibrate_frozen_point is None or iterate_frozen_point_until_stable is None:
    pytest.skip("installed midas-calibrate-v2 predates the frozen-point pipeline",
                allow_module_level=True)

pytest.importorskip("midas_integrate")


NY, NZ = 700, 700
PX_UM = 200.0
WAVELENGTH_A = 0.1729

TRUE = dict(Lsd=200_000.0, BC_y=340.0, BC_z=360.0,
            tx=0.0, ty=2.0, tz=5.0)
SEED = dict(Lsd=TRUE["Lsd"] * 1.002, BC_y=TRUE["BC_y"] + 1.5,
            BC_z=TRUE["BC_z"] - 1.2,
            tx=0.0, ty=TRUE["ty"] + 0.05, tz=TRUE["tz"] - 0.05)

# A genuinely blind tz guess (no tilt information at all) — the actual point
# of this pipeline is recovering from something this rough.
BLIND_SEED = dict(Lsd=TRUE["Lsd"] * 1.002, BC_y=TRUE["BC_y"] + 1.5,
                   BC_z=TRUE["BC_z"] - 1.2, tx=0.0, ty=0.0, tz=0.0)


def _point_pick_kwargs(image: np.ndarray) -> dict:
    return dict(downsample=2, footprint_px=5, snr_threshold=5.0,
                min_ring_gap_deg=0.3,
                panel_mask=np.ones(image.shape, dtype=bool),
                mask_erode_iter=0)


def _build_v1_params(**geom) -> CalibrationParams:
    return CalibrationParams(
        NrPixelsY=NY, NrPixelsZ=NZ, pxY=PX_UM, pxZ=PX_UM,
        Wavelength=WAVELENGTH_A,
        SpaceGroup=225, LatticeConstant=(5.4116, 5.4116, 5.4116, 90, 90, 90),
        MaxRingRad=float(min(NY, NZ)) / 2.0 - 5.0,
        RhoD=float(min(NY, NZ)) / 2.0 - 5.0,
        Refine={"Lsd": True, "BC": True, "ty": True, "tz": True,
                "Wavelength": False, "Parallax": False,
                **{f"p{i}": False for i in range(15)}},
        **geom,
    )


def _paint_synthetic_image(v1_true: CalibrationParams) -> np.ndarray:
    from midas_integrate.geometry import build_tilt_matrix, invert_REta_to_pixel_batch

    rt = build_ring_table(v1_true)
    image = np.zeros((NZ, NY), dtype=np.float64)
    zero15 = {f"p{i}": 0.0 for i in range(15)}
    TRs = build_tilt_matrix(TRUE["tx"], TRUE["ty"], TRUE["tz"])
    rng = np.random.default_rng(1)
    yy, zz = np.meshgrid(np.arange(NY), np.arange(NZ))

    spacing_px = 18.0
    for tt in sorted(set(rt.two_theta_deg.tolist())):
        R0 = (TRUE["Lsd"] / PX_UM) * np.tan(np.radians(tt))
        n_spots = max(8, int(round(2 * np.pi * R0 / spacing_px)))
        phase = rng.uniform(0, 360.0 / n_spots)
        eta = phase + np.arange(n_spots) * (360.0 / n_spots)
        R_targets = np.full_like(eta, R0)
        Y, Z = invert_REta_to_pixel_batch(
            R_targets, eta, Ycen=TRUE["BC_y"], Zcen=TRUE["BC_z"], TRs=TRs,
            Lsd=TRUE["Lsd"], RhoD=1.0, px=PX_UM, parallax=0.0, **zero15,
        )
        on_det = (Y >= 3) & (Y <= NY - 4) & (Z >= 3) & (Z <= NZ - 4)
        for y0, z0 in zip(Y[on_det], Z[on_det]):
            r2 = (yy - y0) ** 2 + (zz - z0) ** 2
            image += 500.0 * np.exp(-r2 / (2 * 1.2 ** 2))

    image += np.random.default_rng(2).normal(scale=3.0, size=image.shape)
    return np.clip(image, 0, None)


@pytest.fixture(scope="module")
def synthetic_image():
    v1_true = _build_v1_params(**TRUE)
    return _paint_synthetic_image(v1_true)


def test_autocalibrate_frozen_point_recovers_known_geometry(synthetic_image):
    v1_seed = _build_v1_params(**SEED)
    result = autocalibrate_frozen_point(
        v1_seed, synthetic_image, snr_min=5.0,
        point_pick_kwargs=_point_pick_kwargs(synthetic_image),
        lm_verbose=False, verbose=False,
    )

    fit = result.history[0]
    lsd_err_pct = 100.0 * abs(fit.Lsd - TRUE["Lsd"]) / TRUE["Lsd"]
    bc_err_px = np.hypot(fit.BC_y - TRUE["BC_y"], fit.BC_z - TRUE["BC_z"])
    tz_err_deg = abs(fit.tz - TRUE["tz"])
    ty_err_deg = abs(fit.ty - TRUE["ty"])

    assert fit.n_fitted > 20
    assert lsd_err_pct < 1.0, f"Lsd error {lsd_err_pct:.4f}% too large"
    assert bc_err_px < 3.0, f"BC error {bc_err_px:.4f}px too large"
    assert tz_err_deg < 0.5, f"tz error {tz_err_deg:.4f}deg too large"
    assert ty_err_deg < 0.5, f"ty error {ty_err_deg:.4f}deg too large"


def test_autocalibrate_frozen_point_freezes_tx_regardless_of_caller_spec(synthetic_image):
    from midas_calibrate_v2.compat.from_v1 import spec_from_v1_params

    v1_seed = _build_v1_params(**SEED)
    spec = spec_from_v1_params(v1_seed)
    if "tx" in spec.parameters:
        spec.parameters["tx"].refined = True  # caller opts in; pipeline must override

    result = autocalibrate_frozen_point(
        v1_seed, synthetic_image, spec=spec, snr_min=5.0,
        point_pick_kwargs=_point_pick_kwargs(synthetic_image),
        lm_verbose=False, verbose=False,
    )
    assert result.spec.parameters["tx"].refined is False


def test_iterate_frozen_point_until_stable_escapes_blind_seed(synthetic_image):
    pp_kwargs = _point_pick_kwargs(synthetic_image)

    # A single direct fit from BLIND_SEED must NOT already solve it —
    # otherwise this wouldn't be exercising the iterative re-seeding at all.
    direct = autocalibrate_frozen_point(
        _build_v1_params(**BLIND_SEED), synthetic_image, snr_min=5.0,
        point_pick_kwargs=pp_kwargs, verbose=False,
    )
    assert abs(direct.history[0].tz - TRUE["tz"]) > 0.5, (
        "a single direct fit from BLIND_SEED already recovered the true tz — "
        "this no longer exercises the iterative wrapper"
    )

    out = iterate_frozen_point_until_stable(
        _build_v1_params(**BLIND_SEED), synthetic_image, snr_min=5.0,
        point_pick_kwargs=pp_kwargs,
        max_iter=20, bounds_bc_px=50.0, bounds_lsd_um=10_000.0,
        verbose=False,
    )

    assert out.converged, (
        f"expected the iterative wrapper to escape a blind tz=0 seed within "
        f"20 iterations; got converged=False after {out.n_iter}"
    )
    fit = out.fit
    lsd_err_pct = 100.0 * abs(fit.Lsd - TRUE["Lsd"]) / TRUE["Lsd"]
    bc_err_px = np.hypot(fit.BC_y - TRUE["BC_y"], fit.BC_z - TRUE["BC_z"])
    tz_err_deg = abs(fit.tz - TRUE["tz"])
    ty_err_deg = abs(fit.ty - TRUE["ty"])

    assert lsd_err_pct < 1.0, f"Lsd error {lsd_err_pct:.4f}% too large"
    assert bc_err_px < 3.0, f"BC error {bc_err_px:.4f}px too large"
    assert tz_err_deg < 0.5, f"tz error {tz_err_deg:.4f}deg too large"
    assert ty_err_deg < 0.5, f"ty error {ty_err_deg:.4f}deg too large"


def test_autocalibrate_frozen_point_refine_distortion_overrides_spec(synthetic_image):
    """An explicit ``refine_distortion`` overrides v1_params.Refine, via the
    same selector ``calibrate()``'s own accepts (block name, bool, or an
    explicit coefficient list) — midas_gui.calib doesn't use this kwarg
    itself (it relies on v1_params.Refine, see the test below), but the
    mechanism it's built on must work."""
    from midas_calibrate_v2.forward.distortion import DISTORTION_BLOCKS, P_COEF_NAMES

    v1_seed = _build_v1_params(**SEED)   # Refine sets every p_i False
    result = autocalibrate_frozen_point(
        v1_seed, synthetic_image, snr_min=5.0, refine_distortion="radial",
        point_pick_kwargs=_point_pick_kwargs(synthetic_image),
        lm_verbose=False, verbose=False,
    )
    radial = set(DISTORTION_BLOCKS["radial"])
    for name in P_COEF_NAMES:
        assert result.spec.parameters[name].refined == (name in radial), name
    # tx stays frozen regardless — the distortion knob must not touch it.
    assert result.spec.parameters["tx"].refined is False


def test_iterate_frozen_point_until_stable_default_still_freezes_distortion(synthetic_image):
    """Regression guard: a v1_params whose Refine freezes every p_i (what
    every GUI call site used before Distortion was ticked) must still
    reproduce the pipeline's original, actually-validated geometry-only
    behaviour — unchanged by the distortion-support update."""
    from midas_calibrate_v2.forward.distortion import P_COEF_NAMES

    pp_kwargs = _point_pick_kwargs(synthetic_image)
    out = iterate_frozen_point_until_stable(
        _build_v1_params(**BLIND_SEED), synthetic_image, snr_min=5.0,
        point_pick_kwargs=pp_kwargs,
        max_iter=20, bounds_bc_px=50.0, bounds_lsd_um=10_000.0,
        verbose=False,
    )
    assert out.converged
    assert all(not out.res.spec.parameters[n].refined for n in P_COEF_NAMES)


def test_iterate_frozen_point_until_stable_honors_v1_refine_like_sibling_pipelines(synthetic_image):
    """This is the exact code path midas_gui.calib.run_pipeline("frozen_point", ...)
    exercises: a v1_params built with Distortion coefficients ticked on in
    Refine (via build_v1_params(..., refine=...)), and NO refine_distortion
    kwarg passed to iterate_frozen_point_until_stable at all — the same way
    every other advanced pipeline (four_stage/bayesian/joint) is called."""
    from midas_calibrate_v2.forward.distortion import (
        DISTORTION_BLOCKS, P_COEF_NAMES, V2_TO_V1_DISTORTION,
    )

    radial = set(DISTORTION_BLOCKS["radial"])
    v1_seed = _build_v1_params(**BLIND_SEED)
    v1_seed.Refine = dict(v1_seed.Refine)
    for name in radial:
        v1_seed.Refine[f"p{V2_TO_V1_DISTORTION[name]}"] = True

    out = iterate_frozen_point_until_stable(
        v1_seed, synthetic_image, snr_min=5.0,
        point_pick_kwargs=_point_pick_kwargs(synthetic_image),
        max_iter=20, bounds_bc_px=50.0, bounds_lsd_um=10_000.0,
        verbose=False,
    )
    for name in P_COEF_NAMES:
        assert out.res.spec.parameters[name].refined == (name in radial), name
