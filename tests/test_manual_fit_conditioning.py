"""Conditioning and limits for the manual (d-spacing) geometry fit.

These tests are built around the geometry that exposed the problem: a SAXS
setup with Lsd = 13.5 m, 55 µm pixels, lambda = 0.173 A and AgBH
(d = 58.38/n A) on a 3072x512 detector. At that geometry only a short arc of
each ring lands on the frame (~42 deg of azimuth for the first order, ~20 for
the second), which makes Lsd and the beam centre strongly correlated and
leaves tilt with essentially no leverage on the residual at all.

The fit still converges there. What these tests pin down is that it reports
*how well* it converged, so a run that merely fitted pick noise cannot be
mistaken for a measured geometry.
"""
import math

import numpy as np
import pytest

from midas_gui.helpers import (fit_geometry_from_ring_picks,
                               simulate_rings_from_dspacings, tilted_ring_xy)

# The reported geometry.
WL_A = 0.1730
PX_UM = 55.0
LSD_UM = 13500.0 * 1000.0
BC_Y, BC_Z = 129.0, 124.0
NY, NZ = 3072, 512
D_LIST = [58.380 / n for n in range(1, 11)]

_LSD_BC = {"Lsd": True, "BC": True, "tx": False, "ty": False, "tz": False,
           "Wavelength": False}
_BC_ONLY = {**_LSD_BC, "Lsd": False}
_LSD_BC_TILT = {**_LSD_BC, "ty": True, "tz": True}


def _rings():
    return simulate_rings_from_dspacings(D_LIST, WL_A, LSD_UM, PX_UM)


def _picks(n_rings, noise_px=0.0, n_per_ring=12, seed=0):
    """Synthetic picks on the part of each ring that is actually on the frame.

    Restricting to the on-frame arc is the whole point — a fit given the full
    360 deg of every ring is well conditioned and shows none of the behaviour
    these tests are about.
    """
    rng = np.random.default_rng(seed)
    out = []
    for r in _rings()[:n_rings]:
        ys, zs = tilted_ring_xy(r["two_theta_deg"], 0.0, 0.0, 0.0,
                                LSD_UM, BC_Y, BC_Z, PX_UM, PX_UM)
        on = (ys >= 0) & (ys < NY) & (zs >= 0) & (zs < NZ)
        ys, zs = ys[on], zs[on]
        if not len(ys):
            continue
        for i in np.linspace(0, len(ys) - 1, min(n_per_ring, len(ys))).astype(int):
            out.append((ys[i] + rng.normal(0, noise_px),
                        zs[i] + rng.normal(0, noise_px), r["d_spacing"]))
    return out


def _max_ring_shift(fit, order=0):
    """How far the first ring drawn from ``fit`` sits from the true one, in px
    — the quantity the user actually sees on the image."""
    tt = _rings()[order]["two_theta_deg"]
    y0, z0 = tilted_ring_xy(tt, 0.0, 0.0, 0.0, LSD_UM, BC_Y, BC_Z, PX_UM, PX_UM)
    y1, z1 = tilted_ring_xy(tt, fit["tx"], fit["ty"], fit["tz"],
                            fit["Lsd"], fit["BC_y"], fit["BC_z"], PX_UM, PX_UM)
    on = (y0 >= 0) & (y0 < NY) & (z0 >= 0) & (z0 < NZ)
    return float(np.hypot(y0[on] - y1[on], z0[on] - z1[on]).max())


def test_on_frame_arc_coverage_is_the_underlying_constraint():
    """Sanity-check the premise: these rings are mostly off the detector."""
    frac = []
    for r in _rings()[:3]:
        th = np.linspace(0, 2 * math.pi, 3600)
        y = BC_Y + r["radius_px"] * np.cos(th)
        z = BC_Z + r["radius_px"] * np.sin(th)
        frac.append(float(((y >= 0) & (y < NY) & (z >= 0) & (z < NZ)).mean()))
    assert frac[0] * 360 < 60           # first order: well under a sixth of the ring
    assert frac[0] > frac[1] > frac[2]  # and it only gets worse further out


def test_bc_only_beats_lsd_plus_bc_on_a_single_short_arc():
    """The reported bug: with one ring's worth of picks, floating Lsd as well
    as BC lands the drawn rings visibly off; holding Lsd does not."""
    picks = _picks(1, noise_px=1.0, seed=1)
    seed = (LSD_UM, BC_Y + 3, BC_Z - 3)

    loose = fit_geometry_from_ring_picks(picks, WL_A, PX_UM, PX_UM,
                                         seed=seed, refine=_LSD_BC)
    tight = fit_geometry_from_ring_picks(picks, WL_A, PX_UM, PX_UM,
                                         seed=seed, refine=_BC_ONLY)

    assert _max_ring_shift(tight) < 2.0
    assert _max_ring_shift(loose) > 2 * _max_ring_shift(tight)
    assert tight["Lsd"] == pytest.approx(LSD_UM)      # held exactly at seed
    assert tight["BC_y"] == pytest.approx(BC_Y, abs=1.5)
    assert tight["BC_z"] == pytest.approx(BC_Z, abs=1.5)


def test_sigma_tracks_how_well_each_parameter_is_actually_determined():
    picks = _picks(1, noise_px=1.0, seed=1)
    seed = (LSD_UM, BC_Y + 3, BC_Z - 3)

    loose = fit_geometry_from_ring_picks(picks, WL_A, PX_UM, PX_UM,
                                         seed=seed, refine=_LSD_BC)
    tight = fit_geometry_from_ring_picks(picks, WL_A, PX_UM, PX_UM,
                                         seed=seed, refine=_BC_ONLY)

    # Floating Lsd on one arc: metres of uncertainty on Lsd, many px on BC.
    assert loose["sigma"]["Lsd"] > 0.005 * LSD_UM
    assert loose["sigma"]["BC_y"] > 5.0
    # Holding it: sub-pixel. And a fixed parameter reports no uncertainty.
    assert tight["sigma"]["BC_y"] < 1.0
    assert tight["sigma"]["Lsd"] == 0.0

    # More rings is the real fix, and sigma must reflect that too.
    three = fit_geometry_from_ring_picks(_picks(3, noise_px=1.0, seed=1), WL_A,
                                         PX_UM, PX_UM, seed=seed, refine=_LSD_BC)
    assert three["sigma"]["Lsd"] < loose["sigma"]["Lsd"]
    assert three["sigma"]["BC_y"] < loose["sigma"]["BC_y"]


def test_tilt_is_reported_as_unconstrained_at_this_geometry():
    """Tilt barely moves the residual here, so the optimizer will happily
    return a degree or so of it from pick noise alone. The value is junk; the
    sigma is what says so."""
    picks = _picks(1, noise_px=1.0, seed=1)
    fit = fit_geometry_from_ring_picks(picks, WL_A, PX_UM, PX_UM,
                                       seed=(LSD_UM, BC_Y + 3, BC_Z - 3),
                                       refine=_LSD_BC_TILT)
    assert fit["success"]
    assert fit["sigma"]["ty"] > 10.0        # degrees — nonsense, and says so
    assert fit["sigma"]["tz"] > 10.0
    # ...even though the residual looks perfectly healthy.
    assert fit["residual_deg_rms"] < 1e-3


def test_refine_none_still_uses_lm_and_the_legacy_free_set():
    picks = _picks(2, noise_px=1.0, seed=2)
    seed = (LSD_UM, BC_Y + 3, BC_Z - 3)
    legacy = fit_geometry_from_ring_picks(picks, WL_A, PX_UM, PX_UM, seed=seed)
    explicit = fit_geometry_from_ring_picks(picks, WL_A, PX_UM, PX_UM,
                                            seed=seed, refine=_LSD_BC)
    assert legacy["method"] == "lm"
    assert legacy["n_free"] == 3
    assert legacy["at_limit"] == set() and legacy["clamped"] == set()
    for key in ("Lsd", "BC_y", "BC_z", "tx", "ty", "tz", "wavelength_A"):
        assert legacy[key] == pytest.approx(explicit[key])
    assert legacy["tx"] == 0.0 and legacy["ty"] == 0.0 and legacy["tz"] == 0.0
    assert legacy["wavelength_A"] == pytest.approx(WL_A)


def test_a_finite_bound_switches_to_trf_and_is_respected():
    picks = _picks(1, noise_px=1.0, seed=1)
    lo, hi = LSD_UM * 0.99, LSD_UM * 1.01
    fit = fit_geometry_from_ring_picks(picks, WL_A, PX_UM, PX_UM,
                                       seed=(LSD_UM, BC_Y + 3, BC_Z - 3),
                                       refine=_LSD_BC, bounds={"Lsd": (lo, hi)})
    assert fit["method"] == "trf"
    assert lo - 1e-6 <= fit["Lsd"] <= hi + 1e-6
    # An unbounded parameter alongside a bounded one stays unbounded.
    assert fit["BC_y"] != pytest.approx(BC_Y + 3)


def test_seed_outside_its_bound_is_clamped_not_rejected():
    """trf refuses an x0 outside the box; the seed is moved onto it instead,
    and the caller is told which parameters that happened to."""
    picks = _picks(2, noise_px=0.5, seed=4)
    lo, hi = LSD_UM * 0.99, LSD_UM * 1.01
    fit = fit_geometry_from_ring_picks(picks, WL_A, PX_UM, PX_UM,
                                       seed=(LSD_UM * 1.5, BC_Y, BC_Z),
                                       refine=_LSD_BC, bounds={"Lsd": (lo, hi)})
    assert fit["clamped"] == {"Lsd"}
    assert lo - 1e-6 <= fit["Lsd"] <= hi + 1e-6


def test_parameter_pinned_by_a_bound_is_flagged_rather_than_given_a_sigma():
    """A box that excludes the true value drives Lsd onto the edge. That is a
    constraint the user imposed, not a measurement, so it is reported as
    'at limit' with no uncertainty attached."""
    picks = _picks(3, noise_px=0.3, seed=3)
    fit = fit_geometry_from_ring_picks(picks, WL_A, PX_UM, PX_UM,
                                       seed=(LSD_UM * 1.03, BC_Y, BC_Z),
                                       refine=_LSD_BC,
                                       bounds={"Lsd": (LSD_UM * 1.02, LSD_UM * 1.05)})
    assert fit["at_limit"] == {"Lsd"}
    assert fit["sigma"]["Lsd"] == 0.0
    # A box that comfortably contains the answer leaves nothing at a limit.
    ok = fit_geometry_from_ring_picks(picks, WL_A, PX_UM, PX_UM,
                                      seed=(LSD_UM, BC_Y, BC_Z), refine=_LSD_BC,
                                      bounds={"Lsd": (LSD_UM * 0.9, LSD_UM * 1.1)})
    assert ok["at_limit"] == set()
    assert ok["Lsd"] == pytest.approx(LSD_UM, rel=1e-3)


def test_nothing_to_refine_reports_the_seed_back_with_no_uncertainty():
    fit = fit_geometry_from_ring_picks(
        _picks(2, seed=5), WL_A, PX_UM, PX_UM, seed=(LSD_UM, BC_Y, BC_Z),
        refine={k: False for k in ("Lsd", "BC", "tx", "ty", "tz", "Wavelength")})
    assert fit["success"] and fit["n_free"] == 0 and fit["method"] == "lm"
    assert fit["Lsd"] == LSD_UM and fit["BC_y"] == BC_Y and fit["BC_z"] == BC_Z
    assert set(fit["sigma"].values()) == {0.0}
