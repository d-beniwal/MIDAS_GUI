"""Stream-picker widgets: what they offer, and what survives a restore.

Qt but not pyqtgraph and not matplotlib, so the ~2-test cap the GUI files
live under does not apply -- that is a pyqtgraph teardown problem. Qt
imports still go inside fixtures, per the repo rule about forked children.

Driven against a REAL ``MetadataCatalog`` over real temp files rather than
a stubbed one: the whole job of these widgets is to reflect what the
catalog discovered, and a stub would let them agree with a fiction.
"""
from __future__ import annotations

import numpy as np
import pytest

pytestmark = pytest.mark.forked


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture()
def catalog(app, tmp_path):
    """A catalog over an HDF5 with two motor streams and a peak-fit CSV."""
    h5py = pytest.importorskip("h5py")
    from midas_gui.metadata_catalog import MetadataCatalog
    src = tmp_path / "scan.h5"
    with h5py.File(src, "w") as f:
        f.create_dataset("measurement/samX", data=np.linspace(0, 1, 6))
        f.create_dataset("measurement/samY", data=np.linspace(2, 3, 6))
    csv = tmp_path / "fit.csv"
    csv.write_text(
        "name,Use,pos1,int1\n"
        + "\n".join(f"img={i} omega={i}.0 eta=0.0,True,{5 + i*0.1},{100 + i}"
                    for i in range(6)) + "\n")
    c = MetadataCatalog()
    c.set_primary_source(str(src))
    c.set_peak_fit_csv(str(csv))
    return c


# ── what the stream combo offers ────────────────────────────────────────

def test_the_combo_lists_what_the_catalog_found(app, catalog):
    from midas_gui.stream_pickers import MetadataStreamPicker
    p = MetadataStreamPicker(catalog)
    labels = [p.combo.itemText(i) for i in range(p.combo.count())]
    assert any("samX" in t for t in labels), labels
    assert any("samY" in t for t in labels), labels


def test_it_repopulates_when_the_catalog_moves(app, catalog, tmp_path):
    """One catalogChanged drives every picker; nothing wires them to each
    other, so this is the only path by which they update."""
    h5py = pytest.importorskip("h5py")
    from midas_gui.stream_pickers import MetadataStreamPicker
    p = MetadataStreamPicker(catalog)
    assert any("samX" in p.combo.itemText(i) for i in range(p.combo.count()))
    other = tmp_path / "other.h5"
    with h5py.File(other, "w") as f:
        f.create_dataset("measurement/samZ", data=np.arange(4.0))
    catalog.set_primary_source(str(other))
    app.processEvents()
    labels = [p.combo.itemText(i) for i in range(p.combo.count())]
    assert any("samZ" in t for t in labels), labels
    assert not any("samX" in t for t in labels), "stale stream still listed"


# ── set_key: the part the source never had ──────────────────────────────

def test_a_selection_round_trips_by_key(app, catalog):
    """Keys, not display text. The labels embed counts like [N=6] that
    change with the file, so restoring by text would silently miss."""
    from midas_gui.stream_pickers import MetadataStreamPicker
    p = MetadataStreamPicker(catalog)
    target = next(s.key for s in catalog.list_streams() if "samY" in s.key)
    assert p.set_key(target) is True
    assert p.current_key() == target
    # ...and the COMBO ITEM text really does carry a file-dependent count,
    # which is exactly why restoring by display text was not an option.
    # (current_label() is the bare path; the count is in the item text,
    # which is what apply_dict_to_widgets would have matched on.)
    assert "[" in p.combo.currentText(), p.combo.currentText()


def test_an_absent_key_is_reported_not_raised(app, catalog):
    """A project restored against a file that no longer has the stream is
    ordinary, not an error."""
    from midas_gui.stream_pickers import MetadataStreamPicker
    p = MetadataStreamPicker(catalog)
    before = p.current_key()
    assert p.set_key("prim:/measurement/gone") is False
    assert p.current_key() == before, "a failed restore moved the selection"


# ── ColorSourceRow ──────────────────────────────────────────────────────

def test_the_row_remembers_which_source_was_active(app, catalog):
    """Which RADIO is active cannot be read back off either picker, so the
    row has to carry it or a restore silently lands on Metadata."""
    from midas_gui.stream_pickers import ColorSourceRow
    r = ColorSourceRow(catalog)
    r.fit_radio.setChecked(True)
    st = r.state()
    assert st["using_fit"] is True

    r2 = ColorSourceRow(catalog)
    assert r2.using_fit() is False
    r2.set_state(st)
    assert r2.using_fit() is True


def test_the_row_round_trips_its_metadata_selection(app, catalog):
    from midas_gui.stream_pickers import ColorSourceRow
    r = ColorSourceRow(catalog)
    target = next(s.key for s in catalog.list_streams() if "samX" in s.key)
    r.meta_picker.set_key(target)
    r2 = ColorSourceRow(catalog)
    r2.set_state(r.state())
    assert r2.meta_picker.current_key() == target


def test_restoring_junk_leaves_a_usable_widget(app, catalog):
    from midas_gui.stream_pickers import ColorSourceRow
    r = ColorSourceRow(catalog)
    r.set_state({"using_fit": "yes please", "meta_key": None})
    r.set_state({})
    r.set_state(None)
    assert r.current_label() is not None


# ── colour-range helpers ────────────────────────────────────────────────

def test_blank_colour_limits_fall_back_to_the_data_range(app):
    from midas_gui.stream_pickers import make_color_range_row, resolve_color_limits
    _row, lo_ed, hi_ed = make_color_range_row(lambda: None)
    assert resolve_color_limits(lo_ed, hi_ed, 1.0, 5.0) == (1.0, 5.0), (
        "blank boxes must fall back to the data's own range")


def test_colour_limits_are_read_when_given(app):
    from midas_gui.stream_pickers import make_color_range_row, resolve_color_limits
    _row, lo_ed, hi_ed = make_color_range_row(lambda: None)
    lo_ed.setText("2"); hi_ed.setText("4")
    assert resolve_color_limits(lo_ed, hi_ed, 1.0, 5.0) == (2.0, 4.0)


def test_unparseable_colour_limits_do_not_raise(app):
    """Typed into a free-text box mid-edit; a traceback on every keystroke
    would be worse than ignoring it."""
    from midas_gui.stream_pickers import make_color_range_row, resolve_color_limits
    _row, lo_ed, hi_ed = make_color_range_row(lambda: None)
    lo_ed.setText("not a number"); hi_ed.setText("")
    assert resolve_color_limits(lo_ed, hi_ed, 1.0, 5.0) == (1.0, 5.0), (
        "garbage must fall back to the defaults, not raise")
