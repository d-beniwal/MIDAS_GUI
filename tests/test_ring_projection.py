"""The predicted-ring overlay's forward projection.

``helpers.ring_xy_corrected`` answers "where does this ring actually land on the
detector?" for the Calibrate tab's overlay. Getting it wrong is invisible in the
worst way — the rings still look like rings, they just sit slightly off the
measured ones, which is exactly the symptom a user would read as a bad
calibration rather than a bad overlay.

So it is pinned against the backend's own forward model rather than against a
hand-derived expectation: every point the projector draws is fed back through
``midas_calibrate_v2.forward.geometry.pixel_to_REta`` — the function
``midas_integrate_v2`` bins pixels with — and must come back at the ring's
radius.

Pure numpy/torch; no Qt is imported here, at module scope or otherwise.
"""
import math

import numpy as np
import pytest


# A deliberately awkward geometry: all three tilts non-zero, a beam centre off
# both axes, and five non-zero harmonics including a 1-fold term (which is the
# one nearly degenerate with the beam centre, so it stresses the solve).
GEOM = dict(Lsd_um=1_000_000.0, bc_y=1024.3, bc_z=1010.7, pxY_um=200.0, pxZ_um=200.0,
            tx=0.7, ty=-1.9, tz=2.4)
NRPIX = dict(NrPixelsY=2048, NrPixelsZ=2048)
DISTORTION = {"iso_R2": 3e-3, "iso_R4": -1.5e-3, "a1": 1e-3, "phi1": 100.0,
              "a2": 2e-3, "phi2": 33.0}


def _rho_d():
    from midas_gui.helpers import distortion_rho_d_um
    return distortion_rho_d_um(NRPIX["NrPixelsY"], NRPIX["NrPixelsZ"],
                               GEOM["bc_y"], GEOM["bc_z"],
                               GEOM["pxY_um"], GEOM["pxZ_um"])


def _observed_R_px(Y, Z, distortion):
    """R (px) the backend reports for pixel coordinates (Y, Z) — the same
    corrected radius the integration bins on."""
    import torch
    from midas_calibrate_v2.forward.geometry import pixel_to_REta
    from midas_distortion import v2_coeffs_from_named

    def t(v):
        return torch.tensor(float(v), dtype=torch.float64)

    out = pixel_to_REta(
        torch.as_tensor(np.asarray(Y, dtype=float)),
        torch.as_tensor(np.asarray(Z, dtype=float)),
        Lsd=t(GEOM["Lsd_um"]), BC_y=t(GEOM["bc_y"]), BC_z=t(GEOM["bc_z"]),
        tx=t(GEOM["tx"]), ty=t(GEOM["ty"]), tz=t(GEOM["tz"]),
        p_coeffs=torch.as_tensor(v2_coeffs_from_named(distortion or {})),
        parallax=t(0.0), pxY=t(GEOM["pxY_um"]), pxZ=t(GEOM["pxZ_um"]),
        rho_d=t(_rho_d()))
    return out.R_px.detach().cpu().numpy()


def _target_R_px(two_theta_deg):
    """The ideal Bragg radius the calibration matches against —
    ``forward.bragg.R_ideal_px``: Lsd·tan(2θ) over the *mean* pixel pitch."""
    px_mean = 0.5 * (GEOM["pxY_um"] + GEOM["pxZ_um"])
    return GEOM["Lsd_um"] * math.tan(math.radians(two_theta_deg)) / px_mean


@pytest.mark.parametrize("two_theta", [2.0, 6.0, 12.0])
def test_drawn_ring_lands_where_the_backend_says_the_ring_is(two_theta):
    """Every point of the drawn curve must have the ring's corrected radius.

    This is the whole contract. `tilted_ring_xy` (tilt only) fails it as soon
    as any distortion coefficient is non-zero, which is what the old
    "Corrected" overlay drew.
    """
    from midas_gui.helpers import ring_xy_corrected
    Y, Z = ring_xy_corrected(two_theta, **GEOM,
                             distortion=DISTORTION, rho_d_um=_rho_d())
    err = np.abs(_observed_R_px(Y, Z, DISTORTION) - _target_R_px(two_theta))
    assert err.max() < 1e-9, f"max radial error {err.max():.3e} px"


@pytest.mark.parametrize("two_theta, floor", [(6.0, 0.2), (12.0, 2.0)])
def test_tilt_only_projection_is_visibly_wrong_under_distortion(two_theta, floor):
    """Guards the reason this function exists: the tilt-only projection — what
    the old "Corrected" overlay drew — misses the real ring by a third of a
    pixel on the inner rings and over two pixels by 12°, growing with radius
    because the harmonics are polynomials in ρ. So the test above is not
    passing on a geometry where distortion happens not to matter."""
    from midas_gui.helpers import tilted_ring_xy
    Y, Z = tilted_ring_xy(two_theta, GEOM["tx"], GEOM["ty"], GEOM["tz"],
                          GEOM["Lsd_um"], GEOM["bc_y"], GEOM["bc_z"],
                          GEOM["pxY_um"], GEOM["pxZ_um"])
    err = np.abs(_observed_R_px(Y, Z, DISTORTION) - _target_R_px(two_theta))
    assert err.max() > floor, f"only {err.max():.3f} px off — pick a harsher case"


@pytest.mark.parametrize("distortion", [None, {}, {"iso_R2": 0.0, "a3": 0.0}])
def test_reduces_exactly_to_tilted_ring_xy_without_distortion(distortion):
    """Bit-identical, not merely close. Every existing caller that has no
    distortion to apply must keep getting exactly the curve it got before,
    so switching them over cannot move a single overlay."""
    from midas_gui.helpers import ring_xy_corrected, tilted_ring_xy
    got = ring_xy_corrected(6.0, **GEOM, distortion=distortion, rho_d_um=_rho_d())
    want = tilted_ring_xy(6.0, GEOM["tx"], GEOM["ty"], GEOM["tz"],
                          GEOM["Lsd_um"], GEOM["bc_y"], GEOM["bc_z"],
                          GEOM["pxY_um"], GEOM["pxZ_um"])
    assert np.array_equal(got[0], want[0])
    assert np.array_equal(got[1], want[1])


def test_missing_rho_d_falls_back_to_the_undistorted_ring():
    """A result with no detector size cannot normalise the harmonic basis.
    Drawing the tilt-only ring is the honest degradation; guessing a ρ_d
    would silently rescale every coefficient."""
    from midas_gui.helpers import (distortion_rho_d_um, ring_xy_corrected,
                                   tilted_ring_xy)
    assert distortion_rho_d_um(0, 0, 100.0, 100.0, 200.0, 200.0) is None
    got = ring_xy_corrected(6.0, **GEOM, distortion=DISTORTION, rho_d_um=None)
    want = tilted_ring_xy(6.0, GEOM["tx"], GEOM["ty"], GEOM["tz"],
                          GEOM["Lsd_um"], GEOM["bc_y"], GEOM["bc_z"],
                          GEOM["pxY_um"], GEOM["pxZ_um"])
    assert np.array_equal(got[0], want[0])


def test_rho_d_matches_the_integration_spec_the_calibration_produces():
    """ρ = R_µm / ρ_d, so a ρ_d that disagrees with the one the backend used
    evaluates the harmonics at the wrong radius — the coefficients no longer
    describe the detector they were fitted on."""
    from types import SimpleNamespace
    from midas_calibrate_v2.compat.to_integrate import spec_from_calibration_result
    from midas_gui.helpers import distortion_rho_d_um

    result = SimpleNamespace(
        Lsd=GEOM["Lsd_um"], BC_y=GEOM["bc_y"], BC_z=GEOM["bc_z"],
        tx=GEOM["tx"], ty=GEOM["ty"], tz=GEOM["tz"],
        pxY=GEOM["pxY_um"], pxZ=GEOM["pxZ_um"], wavelength_A=0.1729,
        distortion=dict(DISTORTION), residual_corr_bin_path=None, **NRPIX)
    spec = spec_from_calibration_result(result, RBinSize=1.0)
    assert float(spec.RhoD) == pytest.approx(
        distortion_rho_d_um(NRPIX["NrPixelsY"], NRPIX["NrPixelsZ"],
                            GEOM["bc_y"], GEOM["bc_z"],
                            GEOM["pxY_um"], GEOM["pxZ_um"]), rel=1e-12)


def test_non_square_pixels_still_land_on_the_ring():
    """pxY != pxZ splits the µm→px conversion (the backend uses the mean) from
    the per-axis pixel offsets. Both have to be right or the curve is an
    ellipse that happens to pass through the axes."""
    from midas_gui.helpers import distortion_rho_d_um, ring_xy_corrected
    geom = dict(GEOM, pxZ_um=150.0)
    rho = distortion_rho_d_um(NRPIX["NrPixelsY"], NRPIX["NrPixelsZ"],
                              geom["bc_y"], geom["bc_z"],
                              geom["pxY_um"], geom["pxZ_um"])
    Y, Z = ring_xy_corrected(6.0, **geom, distortion=DISTORTION, rho_d_um=rho)

    import torch
    from midas_calibrate_v2.forward.geometry import pixel_to_REta
    from midas_distortion import v2_coeffs_from_named

    def t(v):
        return torch.tensor(float(v), dtype=torch.float64)

    out = pixel_to_REta(
        torch.as_tensor(Y), torch.as_tensor(Z),
        Lsd=t(geom["Lsd_um"]), BC_y=t(geom["bc_y"]), BC_z=t(geom["bc_z"]),
        tx=t(geom["tx"]), ty=t(geom["ty"]), tz=t(geom["tz"]),
        p_coeffs=torch.as_tensor(v2_coeffs_from_named(DISTORTION)),
        parallax=t(0.0), pxY=t(geom["pxY_um"]), pxZ=t(geom["pxZ_um"]), rho_d=t(rho))
    px_mean = 0.5 * (geom["pxY_um"] + geom["pxZ_um"])
    target = geom["Lsd_um"] * math.tan(math.radians(6.0)) / px_mean
    assert np.abs(out.R_px.numpy() - target).max() < 1e-9
