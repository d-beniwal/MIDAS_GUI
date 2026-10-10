"""The Hydra page can set the rotation, and the run records it.

Reported at 1-ID-E, looking at the all-panels caking table: "omega bin size
is not available here."

mpe_wf's cake_parameters CSV has NINE keys -- R_MIN/R_MAX/R_STEP,
ETA_MIN/ETA_MAX/ETA_STEP, OME_SUM/OME_START/OME_STEP -- and this page had
controls for six. OME_STEP is the omega bin size.

The consequence was worse than a missing field. With no controls the page
never passed an omega_cfg, so BatchWorker fell through to start=step=0 and
every frame was written to the zarr's /Omegas as 0 deg. A stationary scan
never notices; a rotation scan is simply wrong, and nothing said so. The
single-detector tab warns about exactly this state ("the state that
produced the all-zero /Omegas in a real run").
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.forked


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def page(app):
    from midas_gui.hydra_batch_page import HydraBatchPage
    return HydraBatchPage()


# ── the cfg the worker actually receives ────────────────────────────────
def test_the_page_now_produces_an_omega_cfg(page):
    cfg = page.omega_cfg()
    assert set(cfg) == {"start", "step", "channel", "collapse"}


def test_the_rotation_reaches_the_cfg(page):
    page._ome_start.setValue(12.5)
    page._ome_step.setValue(0.25)
    page._ome_collapse.setChecked(True)
    cfg = page.omega_cfg()
    assert (cfg["start"], cfg["step"], cfg["collapse"]) == (12.5, 0.25, True)


def test_a_step_fine_enough_to_matter_is_not_rounded_away(page):
    """0.002 deg per sub-frame is a real rotation scan; a 2-decimal spin
    would quantise it to zero."""
    page._ome_step.setValue(0.002)
    assert page.omega_cfg()["step"] == pytest.approx(0.002)


# ── the quiet failure is announced ──────────────────────────────────────
def test_all_zero_rotation_says_so(page):
    assert "0°" in page._ome_lbl.text()
    assert "all zeros" in page._ome_lbl.text()


def test_a_configured_rotation_reports_itself_instead(page):
    page._ome_start.setValue(5.0)
    page._ome_step.setValue(0.1)
    txt = page._ome_lbl.text()
    assert "all zeros" not in txt
    assert "5" in txt and "0.1" in txt


def test_the_note_follows_the_collapse_toggle(page):
    page._ome_start.setValue(5.0)
    page._ome_collapse.setChecked(True)
    assert "averaged" in page._ome_lbl.text()


# ── the CSV round-trip stops losing the rotation ────────────────────────
def test_a_loaded_csv_applies_the_rotation_rather_than_reporting_it(page,
                                                                    tmp_path):
    """It used to be read, logged as "ignored", and dropped -- the page had
    nowhere to put it."""
    from midas_gui import cake_params
    f = tmp_path / "cake_parameters.1ide.ge1.csv"
    cake_params.write_cake_csv(str(f), {
        "R_MIN": 440.0, "R_MAX": 2200.0, "R_STEP": 0.5,
        "ETA_MIN": 120.0, "ETA_MAX": 210.0, "ETA_STEP": 10.0,
        "OME_START": 7.25, "OME_STEP": 0.05})
    from PyQt5 import QtWidgets
    import unittest.mock as _m
    with _m.patch.object(QtWidgets.QFileDialog, "getOpenFileName",
                         staticmethod(lambda *a, **k: (str(f), ""))):
        page._load_cake_csv(1)
    assert page._ome_start.value() == pytest.approx(7.25)
    assert page._ome_step.value() == pytest.approx(0.05)


def test_saving_writes_the_rotation_not_zeros(page, tmp_path):
    """Writing 0 for a rotation the user has set hands the workflow back a
    file saying the stage never moved."""
    from midas_gui import cake_params
    page._ome_start.setValue(7.25)
    page._ome_step.setValue(0.05)
    vals = dict(page._cards[1].caking())
    vals["OME_START"] = page._ome_start.value()
    vals["OME_STEP"] = page._ome_step.value()
    f = tmp_path / "out.csv"
    cake_params.write_cake_csv(str(f), vals)
    got = cake_params.parse_cake_csv(str(f))
    assert got["OME_START"] == pytest.approx(7.25)
    assert got["OME_STEP"] == pytest.approx(0.05)


def test_all_nine_cake_keys_survive_a_write(page, tmp_path):
    from midas_gui import cake_params
    f = tmp_path / "nine.csv"
    cake_params.write_cake_csv(str(f), dict(page._cards[1].caking()))
    assert set(cake_params.parse_cake_csv(str(f))) >= set(cake_params.CAKE_KEYS)


# ── it is shared, because there is one goniometer ───────────────────────
def test_the_rotation_is_page_level_not_per_panel(page):
    """All four panels see the same omega for a given frame; a per-panel
    copy could disagree with itself."""
    assert not any(k.startswith("OME") for k in page._cards[1].caking())
    assert hasattr(page, "_ome_start") and hasattr(page, "_ome_step")


def test_the_rotation_is_saved_with_the_page_state(page):
    assert {"ome_start", "ome_step", "ome_collapse"} <= set(page._state_widgets())


# ── where it is, which is the half that was actually reported ───────────
def _card_title(w):
    c = w.parentWidget()
    while c is not None and not hasattr(c, "title"):
        c = c.parentWidget()
    return c.title() if c is not None else ""


def test_the_rotation_row_sits_in_the_caking_card_not_two_cards_away(page):
    """First put in the shared Integration card — correct, shared, and
    nowhere near where it was asked for. Reported twice as "omega bin size
    is not available here" / "I still don't see the omega step ... option",
    both times while looking at the caking table."""
    page.show()
    page._toolbar.set_available([1, 2, 3, 4])
    page._on_panel_changed("ge1")
    assert "caking" in _card_title(page._ome_start)


def test_it_follows_whichever_caking_card_is_showing(page):
    page.show()
    page._toolbar.set_available([1, 2, 3, 4])
    for key, want in (("ge3", "ge3 — caking"), ("ge4", "ge4 — caking")):
        page._on_panel_changed(key)
        assert _card_title(page._ome_start) == want


def test_it_is_on_the_composite_card_too(page):
    """The all-panels view is where it was asked for, and ω is shared, so
    that is exactly where it belongs."""
    page.show()
    page._toolbar.set_available([1, 2, 3, 4])
    page._on_panel_changed("composite")
    assert "all panels" in _card_title(page._ome_start)
    assert page._ome_start.isVisible() and page._ome_step.isVisible()


def test_it_travels_with_the_grid_toggle_and_neither_is_left_behind(page):
    page.show()
    page._toolbar.set_available([1, 2, 3, 4])
    for key in ("ge1", "composite", "ge2"):
        page._on_panel_changed(key)
        assert _card_title(page._ome_start) == _card_title(page._grid_chk)
        assert not page._ome_start.isHidden()


def test_reparenting_does_not_cost_it_its_value(page):
    page.show()
    page._toolbar.set_available([1, 2, 3, 4])
    page._ome_start.setValue(7.25)
    page._ome_step.setValue(0.05)
    page._on_panel_changed("composite")
    page._on_panel_changed("ge4")
    assert page.omega_cfg()["start"] == pytest.approx(7.25)
    assert page.omega_cfg()["step"] == pytest.approx(0.05)
