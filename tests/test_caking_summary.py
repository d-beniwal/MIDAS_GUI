"""All four panels' caking in one editable table, plus their total azimuth.

Asked for at 1-ID-E: "I cannot see the ge1-ge4 caking parameters in one show
like the mpe_wf_saxs_waxs", then "let's make this editable."

The coverage line is a readout, not a verdict. A non-tiling set still merges
and integrates correctly -- the merge concatenates and REtaMap carries every
column's own eta -- so these tests pin that it is reported WITHOUT claiming
anything is broken.

Editing does not store a value here: the cell emits cakingEdited and the
page applies it to that panel's spin box, which clamps. These tests cover
the emitting half; the clamping half belongs to the card.
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
    assert "350" in txt, "the total azimuth is part of the readout"


def test_a_gap_is_reported_without_claiming_anything_is_broken(Summary):
    """The merge concatenates and REtaMap carries each column's eta, so an
    uncovered wedge is absent rather than wrong."""
    w = Summary()
    w.refresh(GAPPED)
    txt = w._coverage.text().lower()
    assert "unaffected" in txt
    for alarm in ("⚠", "hole", "warning", "error", "wrong"):
        assert alarm not in txt


def test_a_tiling_set_reports_a_full_turn_and_no_warning(Summary):
    w = Summary()
    w.refresh(TILED)
    txt = w._coverage.text()
    assert "360" in txt and "tile" in txt
    assert "gap" not in txt


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


# ── editing ─────────────────────────────────────────────────────────────
def _edit(w, row, col, text):
    w._table.item(row, col).setText(text)


def test_an_edited_cell_is_forwarded_as_panel_key_value(Summary):
    w = Summary()
    w.refresh(GAPPED)
    seen = []
    w.cakingEdited.connect(lambda n, k, v: seen.append((n, k, v)))
    _edit(w, 3, 4, "30")                      # ge4 row, ηmin column
    assert seen == [(4, "ETA_MIN", 30.0)]


def test_repainting_the_table_is_not_mistaken_for_an_edit(Summary):
    """refresh writes every cell; without the guard each write would read
    as a user edit and loop straight back through the card."""
    w = Summary()
    w.refresh(GAPPED)
    seen = []
    w.cakingEdited.connect(lambda *a: seen.append(a))
    w.refresh(TILED)
    assert seen == []


def test_text_that_is_not_a_number_is_restored_not_forwarded(Summary):
    w = Summary()
    w.refresh(GAPPED)
    seen = []
    w.cakingEdited.connect(lambda *a: seen.append(a))
    _edit(w, 0, 1, "banana")
    assert seen == []
    assert w._table.item(0, 1).text() == "440", "the last painted value is back"


def test_the_panel_name_column_cannot_be_edited(Summary):
    from PyQt5 import QtCore
    w = Summary()
    w.refresh(GAPPED)
    assert not (w._table.item(0, 0).flags() & QtCore.Qt.ItemIsEditable)


def test_a_cell_with_no_value_behind_it_cannot_be_edited(Summary):
    from PyQt5 import QtCore
    w = Summary()
    w.refresh({1: _cake(120.0, 210.0)})
    assert not (w._table.item(1, 1).flags() & QtCore.Qt.ItemIsEditable)


def test_a_value_cell_can_be_edited(Summary):
    from PyQt5 import QtCore
    w = Summary()
    w.refresh(GAPPED)
    assert w._table.item(0, 1).flags() & QtCore.Qt.ItemIsEditable


def test_one_panel_alone_gets_no_coverage_verdict(Summary):
    """Tiling is a statement about neighbours; one panel has none."""
    w = Summary()
    w.refresh({1: _cake(120.0, 210.0)})
    assert w._coverage.text() == ""
