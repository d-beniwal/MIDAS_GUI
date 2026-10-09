"""All four panels' caking in one table, with a verdict on the azimuth.

Asked for at 1-ID-E: "I cannot see the ge1-ge4 caking parameters in one show
like the mpe_wf_saxs_waxs." Selecting Composite held whichever panel's card
was last open, so the view that is ABOUT all four panels showed one.

The coverage line is the part that earns the widget. Four panels can each
look fine alone and still leave a wedge nobody integrates -- connolly_oct26
shipped a merged zarr with a 10 deg hole in it (ge4 caked from 40 where ge3
ends at 30), and no single card could have shown that.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.forked


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def Summary(app):
    from midas_gui.hydra_batch_widgets import HydraCakingSummary
    return HydraCakingSummary


def _cake(lo, hi, *, r_min=440.0, r_max=2200.0):
    return {"R_MIN": r_min, "R_MAX": r_max, "R_STEP": 0.5,
            "ETA_MIN": lo, "ETA_MAX": hi, "ETA_STEP": 10.0}


#: The windmill as connolly_oct26 ran it -- ge4 one 10 deg bin too high.
GAPPED = {1: _cake(120.0, 210.0), 2: _cake(-150.0, -60.0),
          3: _cake(-60.0, 30.0), 4: _cake(40.0, 120.0)}
TILED = {**GAPPED, 4: _cake(30.0, 120.0)}


def _texts(w):
    return [[w._table.item(r, c).text() if w._table.item(r, c) else None
             for c in range(w._table.columnCount())]
            for r in range(w._table.rowCount())]


def test_every_panel_gets_a_row_and_every_cake_value_a_column(Summary):
    w = Summary()
    w.refresh(GAPPED)
    rows = _texts(w)
    assert len(rows) == 4
    assert [r[0] for r in rows] == ["ge1", "ge2", "ge3", "ge4"]
    # panel + the six cake_parameters fields, in CSV order
    assert w._table.columnCount() == 7
    assert rows[0][1:] == ["440", "2200", "0.5", "120", "210", "10"]
    assert rows[3][4:6] == ["40", "120"]


def test_the_gap_is_named_not_merely_totalled(Summary):
    w = Summary()
    w.refresh(GAPPED)
    txt = w._coverage.text()
    assert "10 deg gap" in txt and "ge3" in txt and "ge4" in txt
    assert "350" in txt, "the total azimuth is part of the verdict"


def test_a_tiling_set_reports_a_full_turn_and_no_warning(Summary):
    w = Summary()
    w.refresh(TILED)
    txt = w._coverage.text()
    assert "360" in txt and "tile" in txt
    assert "gap" not in txt and "⚠" not in txt


def test_the_warning_and_the_all_clear_are_told_apart_by_colour(Summary):
    w = Summary()
    w.refresh(TILED)
    ok = w._coverage.styleSheet()
    w.refresh(GAPPED)
    assert w._coverage.styleSheet() != ok


def test_refreshing_twice_does_not_stack_rows(Summary):
    w = Summary()
    w.refresh(GAPPED)
    w.refresh(TILED)
    assert w._table.rowCount() == 4
    assert _texts(w)[3][4:6] == ["30", "120"]


def test_a_panel_off_the_canvas_is_marked_not_dropped(Summary):
    """The compositor skips panels with no calibration. Dropping the row
    would quietly turn this into a three-panel table."""
    w = Summary()
    w.refresh(GAPPED, missing={2})
    rows = _texts(w)
    assert len(rows) == 4
    assert "not on canvas" in rows[1][0]
    assert rows[1][1] == "440", "its values are still shown"


def test_a_panel_with_no_caking_shows_a_dash_rather_than_a_zero(Summary):
    w = Summary()
    w.refresh({1: _cake(120.0, 210.0)})
    assert _texts(w)[1][1] == "—"


def test_one_panel_alone_gets_no_coverage_verdict(Summary):
    """Tiling is a statement about neighbours; one panel has none."""
    w = Summary()
    w.refresh({1: _cake(120.0, 210.0)})
    assert w._coverage.text() == ""
