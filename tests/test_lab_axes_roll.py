"""The lab-frame compass must agree with the integration about where η is.

Reported at 1-ID-E: "GE1 image does not show the eta with Tx correctly. same
with ge2 - ge4 individually."

build_lab_frame_axes_items drew η=0 straight up unconditionally. That is only
right on an unrolled detector. The 1-ID-E Hydra panels are installed at
tx = 300/30/120/210, so each panel's compass was wrong by its own roll --
while the caking sector drawn beside it WAS tx-aware (tilted_spoke_xy), so
the two contradicted each other on the same picture.

The authority here is helpers.pixel_eta_deg, whose tx offset is documented as
MEASURED against midas_calibrate_v2's pixel_to_REta rather than derived. So
these tests do not restate the formula -- they put a point where the compass
says η is and ask pixel_eta_deg what η it actually lands at.
"""
from __future__ import annotations

import math

import pytest

from midas_gui.helpers import pixel_eta_deg
from midas_gui.widgets import lab_eta_screen_dir

BCY, BCZ, PX = 1024.0, 1024.0, 200.0
#: As installed at 1-ID-E, from the connolly_oct26 .instr.txt files.
HYDRA_TX = {1: 300.0, 2: 30.0, 3: 120.0, 4: 210.0}


def _eta_at(eta_deg, tx, r=300.0):
    """Ask the compass for η's direction, then ask the geometry what η that
    point really is."""
    sx, sy = lab_eta_screen_dir(eta_deg, tx)
    return float(pixel_eta_deg(BCY + sx * r, BCZ + sy * r,
                               BCY, BCZ, PX, PX, tx=tx))


def _wrapped(a, b):
    return abs((a - b + 180.0) % 360.0 - 180.0)


@pytest.mark.parametrize("tx", [0.0, 30.0, 120.0, 210.0, 300.0, -45.0])
@pytest.mark.parametrize("eta", [0.0, 45.0, 90.0, -90.0, 120.0, 180.0, 210.0])
def test_the_compass_points_where_the_geometry_says_eta_is(tx, eta):
    assert _wrapped(_eta_at(eta, tx), eta) < 1e-9


@pytest.mark.parametrize("n,tx", sorted(HYDRA_TX.items()))
def test_every_hydra_panel_as_installed(n, tx):
    for eta in (-180.0, -90.0, 0.0, 90.0):
        assert _wrapped(_eta_at(eta, tx), eta) < 1e-9, f"ge{n} at η={eta}"


# ── the unrolled case must be byte-identical to the old hardcoded text ──
def test_at_zero_roll_the_directions_are_the_old_hardcoded_ones():
    """η=0 screen-up, η=−90 screen-left, η=+90 screen-right, η=180 down --
    written out by hand before this helper existed."""
    for eta, want in ((0.0, (0.0, 1.0)), (-90.0, (-1.0, 0.0)),
                      (90.0, (1.0, 0.0)), (180.0, (0.0, -1.0))):
        sx, sy = lab_eta_screen_dir(eta, 0.0)
        assert math.isclose(sx, want[0], abs_tol=1e-12)
        assert math.isclose(sy, want[1], abs_tol=1e-12)


def test_the_old_formula_is_what_a_zero_roll_reduces_to():
    for eta in (-180.0, -37.0, 0.0, 12.5, 180.0):
        sx, sy = lab_eta_screen_dir(eta, 0.0)
        assert math.isclose(sx, math.sin(math.radians(eta)), abs_tol=1e-12)
        assert math.isclose(sy, math.cos(math.radians(eta)), abs_tol=1e-12)


# ── the bug itself ──────────────────────────────────────────────────────
def test_a_rolled_panel_is_not_drawn_as_an_unrolled_one():
    """The regression guard: ge1 at tx=300 had η=0 drawn straight up, which
    is 60 degrees from where the integration puts it."""
    sx, sy = lab_eta_screen_dir(0.0, HYDRA_TX[1])
    assert not (abs(sx) < 1e-9 and sy > 0), "η=0 must not be straight up here"
    assert _wrapped(math.degrees(math.atan2(sx, sy)), -300.0) < 1e-9


def test_rolling_by_a_full_turn_changes_nothing():
    for eta in (0.0, 73.0):
        a = lab_eta_screen_dir(eta, 30.0)
        b = lab_eta_screen_dir(eta, 390.0)
        assert math.isclose(a[0], b[0], abs_tol=1e-12)
        assert math.isclose(a[1], b[1], abs_tol=1e-12)


def test_none_and_zero_roll_are_the_same_thing():
    assert lab_eta_screen_dir(45.0, None) == lab_eta_screen_dir(45.0, 0.0)
