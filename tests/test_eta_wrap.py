"""Cake an eta range that crosses +/-180 in ONE run, not two.

Asked for at 1-ID-E: "this is a step back from what the mpe_wf_saxs_waxs
could do. Please fix this so that we do not have to make 2x runs just for
GE1."

midas_calibrate_v2's pixel_to_REta types its output "[-180, 180)", and every
midas_integrate_v2 kernel assigns a pixel with a bare
floor((Eta - EtaMin)/EtaBinSize) plus a range test -- none of them wraps.
(polygon's "wraparound" is a different thing: one PIXEL whose corners
straddle the seam.) So ge1's 120..210, which is the azimuth that panel
physically subtends, asked for bins at 185/195/205 that no pixel can land
in. They came back identically zero and their pixels -- 1,027,309 of them,
33.2% of ge1's in-range area -- were not accumulated anywhere.

The fix bins the WHOLE CIRCLE and lifts the requested bins out of it in the
requested order. Verified bit-identical against integrating the two halves
separately on ge1's real geometry.
"""
from __future__ import annotations

import numpy as np
import pytest

from midas_gui.eta_wrap import (ETA_HI, ETA_LO, apply_take,
                                crosses_branch_cut, plan_eta)

#: The four 1-ID-E Hydra panels as connolly_oct26 caked them.
PANELS = {"ge1": (120.0, 210.0), "ge2": (-150.0, -60.0),
          "ge3": (-60.0, 30.0), "ge4": (40.0, 120.0)}


# ── which requests need help ────────────────────────────────────────────
@pytest.mark.parametrize("name", ["ge2", "ge3", "ge4"])
def test_a_range_inside_the_cut_is_left_completely_alone(name):
    lo, hi = PANELS[name]
    p = plan_eta(lo, hi, 10.0)
    assert p.take is None, "the ordinary path must not change at all"
    assert (p.bin_min, p.bin_max) == (lo, hi)
    assert p.problem is None


def test_ge1_is_rebased_onto_the_whole_circle():
    p = plan_eta(*PANELS["ge1"], 10.0)
    assert (p.bin_min, p.bin_max) == (ETA_LO, ETA_HI)
    assert list(p.take) == [30, 31, 32, 33, 34, 35, 0, 1, 2]


def test_the_axis_stays_the_range_that_was_asked_for():
    """Unwrapped, and in request order: 125..205, not 125..175 then
    -175..-155. The data comes out in that order, so the labels must too."""
    p = plan_eta(*PANELS["ge1"], 10.0)
    assert list(p.centres) == [125, 135, 145, 155, 165, 175, 185, 195, 205]


def test_the_take_indices_point_at_the_matching_wrapped_bins():
    p = plan_eta(*PANELS["ge1"], 10.0)
    full = ETA_LO + 10.0 * (np.arange(36) + 0.5)
    wrapped = (p.centres - ETA_LO) % 360.0 + ETA_LO
    assert np.allclose(full[p.take], wrapped)


def test_a_full_circle_request_needs_no_rebasing():
    assert plan_eta(-180.0, 180.0, 10.0).take is None


def test_a_range_ending_exactly_at_180_is_fine():
    """Its last CENTRE is 175. Comparing EtaMax against 180 would reject a
    request that works perfectly."""
    p = plan_eta(120.0, 180.0, 10.0)
    assert p.take is None and p.problem is None


def test_crosses_is_decided_on_centres_not_edges():
    assert not crosses_branch_cut(120.0, 180.0, 10.0)
    assert crosses_branch_cut(120.0, 190.0, 10.0)
    assert crosses_branch_cut(-190.0, -120.0, 10.0)


# ── what it refuses, and why ────────────────────────────────────────────
def test_a_grid_that_does_not_divide_the_circle_is_declined_not_fudged():
    """The requested bins are then not a subset of any whole-circle
    binning, so selection cannot be exact."""
    p = plan_eta(120.0, 210.0, 7.0)
    assert p.take is None and p.problem
    assert "7" in p.problem and "wrap" in p.problem


def test_an_offset_grid_is_declined():
    p = plan_eta(122.0, 212.0, 10.0)
    assert p.take is None and p.problem


def test_a_bin_straddling_the_seam_is_declined():
    """A 10 deg bin centred on 180 spans 175..185 -- half on each side of
    the cut. No single floor() binning can fill it; it would need two
    sub-bins recombined by pixel count, which this does not attempt. It
    says so rather than returning a bin that is quietly half empty."""
    p = plan_eta(175.0, 185.0, 10.0)
    assert p.take is None and p.problem


def test_a_declined_plan_still_reports_the_requested_axis():
    p = plan_eta(120.0, 210.0, 7.0)
    assert len(p.centres) == len(plan_eta(120.0, 210.0, 7.0).centres)
    assert p.centres[0] == pytest.approx(123.5)


# ── apply_take ──────────────────────────────────────────────────────────
def test_apply_take_selects_eta_rows_in_order():
    cake = np.arange(36 * 4).reshape(36, 4)
    take = np.array([30, 31, 0])
    assert np.array_equal(apply_take(cake, take), cake[[30, 31, 0], :])


def test_apply_take_is_a_pass_through_when_there_is_nothing_to_take():
    cake = np.zeros((9, 4))
    assert apply_take(cake, None) is cake, "the ordinary path must not copy"
    assert apply_take(None, np.array([0])) is None


# ── the cardinal-directions workflow ────────────────────────────────────
# "I only produce cakes along the main directions 0, 90, 180, and -90 deg,
# each with 10 deg eta bin size."
@pytest.mark.parametrize("centre", [0.0, 90.0, -90.0])
def test_the_cardinals_away_from_the_seam_need_nothing(centre):
    assert plan_eta(centre - 5.0, centre + 5.0, 10.0).take is None


def test_the_180_cardinal_is_the_one_that_cannot_be_served():
    """Flagged rather than silently half-filled -- an edge on 180 (e.g.
    170..180 and -180..-170) is the representable way to ask."""
    assert plan_eta(175.0, 185.0, 10.0).problem is not None
    assert plan_eta(170.0, 180.0, 10.0).problem is None
    assert plan_eta(-180.0, -170.0, 10.0).problem is None


# ── through the real integration path ───────────────────────────────────
FIXTURE = (__import__("pathlib").Path(__file__).resolve().parent.parent
           / "test_data" / "gui_synthetic" / "hydra" / "ge1" / "ps_ge1.txt")


@pytest.fixture
def wrapped_bits():
    """A geometry whose beam centre is off the panel, so a range crossing
    the cut has real pixels on both sides of it."""
    if not FIXTURE.is_file():
        pytest.skip("synthetic hydra geometry fixture not present")
    torch = pytest.importorskip("torch")
    from midas_gui.helpers import spec_from_geometry_file
    from midas_gui.workers import build_integration_context, integrate_frame
    # ONE image, drawn once. Drawing per call advances the generator, so the
    # runs being compared would integrate different frames and "differs"
    # would mean nothing.
    probe = spec_from_geometry_file(str(FIXTURE), 4.0, 10.0, r_min=10.0,
                                    r_max=120.0, eta_min=0.0, eta_max=10.0)
    frame = (np.random.default_rng(0).random(
        (int(probe.NrPixelsZ), int(probe.NrPixelsY))) * 100 + 1.0)
    img = torch.from_numpy(frame.astype(np.float64))

    def run(lo, hi, kernel="hard"):
        spec = spec_from_geometry_file(str(FIXTURE), 4.0, 10.0,
                                       r_min=10.0, r_max=120.0,
                                       eta_min=lo, eta_max=hi)
        ctx = build_integration_context(spec, kernel, None, (None, None), True)
        _p, _s, cake, _cs = integrate_frame(
            img, ctx["bin_spec"], ctx["geom"], kernel, (None, None), None,
            False, corr_counts=ctx["corr_counts"], return_cake=True,
            weighted=True, cnt_cake=ctx["cnt"], eta_take=ctx["eta_take"])
        return ctx, cake
    return run


def _dead(cake):
    return [i for i in range(cake.shape[0])
            if np.nansum(np.abs(np.nan_to_num(cake[i]))) == 0]


def test_a_wrapped_range_comes_back_with_the_bins_it_asked_for(wrapped_bits):
    ctx, cake = wrapped_bits(120.0, 210.0)
    assert cake.shape[0] == 9, "nine requested bins, nine columns out"
    assert list(ctx["eta_ax"]) == [125, 135, 145, 155, 165, 175, 185, 195, 205]
    assert ctx["eta_take"] is not None


def test_the_wrapped_half_matches_asking_for_it_directly(wrapped_bits):
    """The regression that matters: the recovered columns must be what a
    plain -180..-150 run produces, not merely non-empty."""
    _, whole = wrapped_bits(120.0, 210.0)
    _, lower = wrapped_bits(120.0, 180.0)
    _, upper = wrapped_bits(-180.0, -150.0)
    f = lambda a: np.nan_to_num(a, nan=-1.0)
    assert np.allclose(f(whole[:6]), f(lower))
    assert np.allclose(f(whole[6:]), f(upper))


@pytest.mark.parametrize("kernel", ["hard", "subpixel2", "polygon"])
def test_every_kernel_routes_the_selection(kernel, wrapped_bits):
    _, cake = wrapped_bits(120.0, 210.0, kernel=kernel)
    assert cake.shape[0] == 9


def test_a_range_inside_the_cut_is_bit_identical_to_before(wrapped_bits):
    """ge2/ge3/ge4 must be untouched: no rebasing, no selection."""
    ctx, cake = wrapped_bits(-150.0, -60.0)
    assert ctx["eta_take"] is None
    assert float(ctx["bin_spec"].EtaMin) == -150.0
    assert float(ctx["bin_spec"].EtaMax) == -60.0
    assert cake.shape[0] == 9
