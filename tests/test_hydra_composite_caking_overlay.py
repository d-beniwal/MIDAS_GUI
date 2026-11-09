"""The composite Detector view draws every panel's caking region at once.

Asked for at 1-ID-E: "showing the caking parameter on the composite view on
the batch integrate tab." The per-panel views each show one region, which is
precisely the view that cannot answer the question the four regions exist to
answer together -- do they pave the azimuth, or is there a wedge nobody is
integrating? connolly_oct26's merged zarr had a 10 deg hole in it (ge4 caked
from 40 where ge3 ends at 30) and nothing on screen said so.

No ``HydraBatchPage`` is constructed here. That page builds 9 pyqtgraph
widgets and ``tests/test_hydra_batch_ui.py`` is held to exactly two
page-building tests because of the teardown segfault recorded in
.context/DECISIONS.md. ``_draw_composite_caking`` is a plain function on the
class, and everything it touches is injectable, so it is called against a
stand-in and the drawing calls are recorded -- which is what the assertions
are about anyway.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from midas_gui import hydra
from midas_gui.hydra_batch_page import HydraBatchPage

STEP = hydra.COMPOSITE_DISPLAY_STEP

#: The windmill as connolly_oct26 ran it -- ge4 one 10 deg bin too high.
GAPPED_ETA = {1: (120.0, 210.0), 2: (-150.0, -60.0),
              3: (-60.0, 30.0), 4: (40.0, 120.0)}


def _card(eta, *, r_min=440.0, r_max=2200.0, r_step=0.5, e_step=10.0):
    lo, hi = eta
    return SimpleNamespace(caking=lambda: {
        "R_MIN": r_min, "R_MAX": r_max, "R_STEP": r_step,
        "ETA_MIN": lo, "ETA_MAX": hi, "ETA_STEP": e_step})


def _page(rec, cards, *, panels=(1, 2, 3, 4), grid=True):
    """A stand-in exposing only what _draw_composite_caking reads. The real
    _composite_axes_bc is used rather than a stub -- the (rows, cols) vs
    (y, z) order it exists to get right is part of what is under test."""
    return SimpleNamespace(
        _composite_axes_bc=HydraBatchPage._composite_axes_bc,
        _cards=cards, _composite_panels=list(panels),
        _grid_chk=SimpleNamespace(isChecked=lambda: grid),
        _det_view=object(), _bin_overlay_items=[],
        _clear_bin_overlay=lambda: rec.append({"cleared": True}))


@pytest.fixture
def calls(monkeypatch):
    rec = []
    monkeypatch.setattr("midas_gui.hydra_batch_page.draw_polar_bin_overlay",
                        lambda viewer, items, **kw: rec.append(kw))
    return rec


@pytest.fixture
def img():
    """A square built canvas. Only its shape is read."""
    return SimpleNamespace(shape=(1664, 1664))


def _draw(page, img):
    HydraBatchPage._draw_composite_caking(page, img)


# ── geometry ────────────────────────────────────────────────────────────
def test_one_call_per_panel_each_in_its_own_colour(calls, img):
    cards = {n: _card(GAPPED_ETA[n]) for n in (1, 2, 3, 4)}
    _draw(_page(calls, cards), img)
    assert len(calls) == 4
    assert len({c["color"] for c in calls}) == 4, \
        "four regions on one canvas are only readable if each is its panel's colour"


def test_only_the_first_call_clears_so_the_four_stack(calls, img):
    _draw(_page(calls, {n: _card(GAPPED_ETA[n]) for n in (1, 2, 3, 4)}), img)
    assert [c["clear"] for c in calls] == [True, False, False, False]


def test_the_beam_centre_is_the_canvas_centre(calls, img):
    """Registered composite: all four beam centres land on one point, and
    that point is the middle of the built image."""
    _draw(_page(calls, {n: _card(GAPPED_ETA[n]) for n in (1, 2, 3, 4)}), img)
    assert all((c["bc_y"], c["bc_z"]) == (832.0, 832.0) for c in calls)


def test_radii_are_scaled_into_built_pixels_but_eta_is_not(calls, img):
    """R is a pixel count and the canvas is decimated; eta is an angle in
    the lab frame the canvas already is, so scaling it would be wrong."""
    _draw(_page(calls, {1: _card((120.0, 210.0))}, panels=(1,)), img)
    c, = calls
    assert c["r_min"] == 440.0 / STEP and c["r_max"] == 2200.0 / STEP
    assert c["r_bin"] == 0.5 / STEP
    assert (c["eta_min"], c["eta_max"]) == (120.0, 210.0)
    assert c["e_bin"] == 10.0


def test_the_overlay_is_drawn_untilted(calls, img):
    """The remap takes tx out and the four panels share no single ty/tz, so
    there is no tilt-aware answer to give -- and passing one panel's tilt
    would bend all four regions by it."""
    _draw(_page(calls, {1: _card((120.0, 210.0))}, panels=(1,)), img)
    c, = calls
    assert not any(c.get(k) for k in ("tx", "ty", "tz", "lsd_um"))


# ── which panels take part ──────────────────────────────────────────────
def test_a_panel_absent_from_the_canvas_is_not_drawn(calls, img):
    """The compositor skips panels with no calibration; drawing a region for
    one would claim it is on a canvas it never reached."""
    cards = {n: _card(GAPPED_ETA[n]) for n in (1, 2, 3, 4)}
    _draw(_page(calls, cards, panels=(1, 2)), img)
    assert len(calls) == 2


def test_an_auto_rmax_panel_is_skipped_not_guessed(calls, img):
    """R_MAX 0 means "resolve from this panel's own corner" -- a per-panel
    pixel count the composite has no business inventing."""
    cards = {1: _card((120.0, 210.0)), 2: _card((-150.0, -60.0), r_max=0.0)}
    _draw(_page(calls, cards, panels=(1, 2)), img)
    assert len(calls) == 1 and calls[0]["color"] is not None


def test_nothing_drawable_still_clears_what_was_there(calls, img):
    _draw(_page(calls, {1: _card((120.0, 210.0), r_max=0.0)}, panels=(1,)), img)
    assert calls == [{"cleared": True}]


def test_no_image_clears_rather_than_drawing_at_a_guessed_centre(calls):
    _draw(_page(calls, {1: _card((120.0, 210.0))}, panels=(1,)), None)
    assert calls == [{"cleared": True}]


# ── the thing this view exists to make visible ──────────────────────────
def test_the_drawn_sectors_expose_the_gap_that_the_spin_boxes_hid(calls, img):
    """Not a drawing assertion -- a statement of what the picture shows. The
    four sectors drawn from these numbers leave 30..40 uncovered, which is
    the 10 deg hole that reached a merged zarr unnoticed."""
    _draw(_page(calls, {n: _card(GAPPED_ETA[n]) for n in (1, 2, 3, 4)}), img)
    covered = sorted((c["eta_min"], c["eta_max"]) for c in calls)
    total = sum(hi - lo for lo, hi in covered)
    assert total == 350.0, "350 of 360 degrees, and the overlay shows which 10 are missing"


def test_a_corrected_ge4_closes_the_sweep(calls, img):
    eta = {**GAPPED_ETA, 4: (30.0, 120.0)}
    _draw(_page(calls, {n: _card(eta[n]) for n in (1, 2, 3, 4)}), img)
    assert sum(c["eta_max"] - c["eta_min"] for c in calls) == 360.0


# ── grid toggle ─────────────────────────────────────────────────────────
def test_the_show_bin_grid_checkbox_reaches_every_panel(calls, img):
    cards = {n: _card(GAPPED_ETA[n]) for n in (1, 2, 3, 4)}
    _draw(_page(calls, cards, grid=False), img)
    assert len(calls) == 4 and not any(c["show_grid"] for c in calls)
