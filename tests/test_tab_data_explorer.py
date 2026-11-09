"""The Data Explorer tab: registration, the three panels, the manual grid.

Matplotlib, not pyqtgraph, so the ~2-test cap the GUI files live under does
not apply here -- that cap is a pyqtgraph teardown problem and
``test_tab_zarrviewer.py`` (the other matplotlib tab) runs a dozen clean.
Qt imports go inside fixtures, per the repo rule about forked children.

One hard limit kept: at most ONE ``MainWindow`` construction in this file.
That builds every tab including all the pyqtgraph ones, and STATE.md
records a segfault at four such constructions in a process.
"""
from __future__ import annotations

import numpy as np
import pytest

pytestmark = pytest.mark.forked

#: Tabs built by individual tests, pinned so Qt does not delete their
#: children while the test is still using them.
_alive: list = []


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def tab(app):
    """One tab, reused -- it holds two matplotlib canvases."""
    from midas_gui.tab_data_explorer import DataExplorerTab
    return DataExplorerTab()


# ── registration ────────────────────────────────────────────────────────

def test_it_is_an_optional_tab_that_ships_hidden(app):
    """Zarr Viewer earned its default-visible slot by being verified
    against live data; this has not been, so it ships off.

    Asserted against shipped_defaults(), NOT the live
    constants.DEFAULT_VISIBLE_TABS: a saved profile's ui.visible_tabs
    overrides that global at import (constants.reload_from_config), so the
    moment anyone ticks this tab in Preferences the global contains it and
    a test reading it would fail on their machine only. Same escape hatch
    test_tab_zarrviewer.py uses, and this test did fail exactly that way
    once the tab was enabled for real.
    """
    from midas_gui import constants as C
    assert "Data Explorer" in C.OPTIONAL_TABS
    assert "Data Explorer" not in C.ALWAYS_TABS
    assert "Data Explorer" not in C.shipped_defaults()["ui"]["visible_tabs"]


def test_it_appears_next_to_zarr_viewer_when_enabled(app):
    """The ONE MainWindow in this file. Both tabs open what the pipeline
    wrote, so they belong adjacent."""
    from midas_gui.app import MainWindow
    from midas_gui import constants as C
    w = MainWindow()
    w.apply_tab_visibility(
        list(C.shipped_defaults()["ui"]["visible_tabs"]) + ["Data Explorer"])
    names = [w._tabs.tabText(i) for i in range(w._tabs.count())]
    i = next(k for k, n in enumerate(names) if "Data Explorer" in n)
    assert "Zarr Viewer" in names[i - 1], names


# ── the tab and its panels ──────────────────────────────────────────────

def test_the_three_sub_tabs_are_present_and_named(tab):
    """"(3-D)" is load-bearing: tab_texture also has a pole figure, and it
    answers a different question."""
    labels = [tab._tabs.tabText(i) for i in range(tab._tabs.count())]
    assert labels == ["Sources", "Real space", "Pole figure (3-D)"]


def test_the_panels_share_one_catalog(tab):
    """No cross-panel wiring -- everything hangs off one catalogChanged,
    which is what let the panels be ported one at a time."""
    assert tab._real._catalog is tab._catalog
    assert tab._pole._catalog is tab._catalog
    assert tab._sources._catalog is tab._catalog


def test_the_span_status_flows_upward_not_sideways(tab):
    """Real space reports its multi-file span through an injected callback
    rather than reaching into its sibling. That inversion is what made the
    extraction out of DataExplorerWidget possible at all."""
    assert tab._real._span_status_cb is not None
    tab._real._set_span_status("7 files")
    assert "7 files" in tab._sources.span_status.text()


def test_an_empty_tab_draws_rather_than_raising(tab):
    """Opening it before choosing a source is the first thing anyone does."""
    tab.refresh()


def test_pointing_it_at_a_real_source_populates_the_axis_combos(app, tmp_path):
    from midas_gui.tab_data_explorer import DataExplorerTab
    h5py = pytest.importorskip("h5py")
    src = tmp_path / "scan.h5"
    with h5py.File(src, "w") as f:
        f.create_dataset("measurement/samX", data=np.linspace(0, 1, 8))
        f.create_dataset("measurement/samY", data=np.linspace(2, 3, 8))
    t = DataExplorerTab()
    t.set_source(str(src))
    app.processEvents()
    items = [t._real._axis_combos["x"].itemText(i)
             for i in range(t._real._axis_combos["x"].count())]
    assert any("samX" in s for s in items), items
    t.refresh()


# ── the manual grid (not in the source) ─────────────────────────────────

def _fresh(keep):
    """A new tab, with the tab itself kept alive.

    ``DataExplorerTab()._real`` drops the tab on the next line, and Qt then
    deletes its children -- every spinbox access after that raises
    "wrapped C/C++ object has been deleted". The list pins them.
    """
    from midas_gui.tab_data_explorer import DataExplorerTab
    t = DataExplorerTab()
    keep.append(t)
    return t


def _manual(panel, axis, start, step, n):
    w = panel._manual_widgets[axis]
    w["start"].setValue(start); w["step"].setValue(step); w["n"].setValue(n)


def test_x_varies_fastest_then_y(app):
    """The convention, and the reason it is stated in the UI: getting it
    backwards gives a transposed map that still looks plausible."""
    p = _fresh(_alive)._real
    _manual(p, "x", 0.0, 0.1, 3)
    _manual(p, "y", -1.0, 0.5, 2)
    assert np.allclose(p._manual_axis_values("x", 6), [0, .1, .2, 0, .1, .2])
    assert np.allclose(p._manual_axis_values("y", 6), [-1, -1, -1, -.5, -.5, -.5])


def test_z_is_the_slowest_axis(app):
    p = _fresh(_alive)._real
    _manual(p, "x", 0.0, 1.0, 2)
    _manual(p, "y", 0.0, 1.0, 2)
    _manual(p, "z", 0.0, 10.0, 2)
    assert np.allclose(p._manual_axis_values("z", 8),
                       [0, 0, 0, 0, 10, 10, 10, 10])


def test_zero_points_means_the_axis_is_not_manual(app):
    """0 is the off switch, so an untouched panel behaves exactly as the
    port did -- axes come from the dataset pickers."""
    p = _fresh(_alive)._real
    assert p._manual_shape() == {}
    assert p._manual_axis_values("x", 10) is None
    _manual(p, "x", 0.0, 1.0, 4)
    assert p._manual_shape() == {"x": 4}


def test_the_note_states_the_implied_total(app):
    """Shown because the usual mistake is a grid that does not multiply out
    to the frame count."""
    p = _fresh(_alive)._real
    _manual(p, "x", 0.0, 0.1, 21)
    _manual(p, "y", 0.0, 0.1, 11)
    note = p._manual_note.text()
    assert "21 x 11 = 231" in note, note
    assert "fastest" in note


def test_a_manual_axis_beats_the_dataset_picker(app, tmp_path):
    """It is the explicit instruction, and on a TIFF-sourced archive -- no
    instrument/ motor tree -- it is the only option there is."""
    from midas_gui.tab_data_explorer import DataExplorerTab
    h5py = pytest.importorskip("h5py")
    src = tmp_path / "s.h5"
    with h5py.File(src, "w") as f:
        f.create_dataset("exchange/data", data=np.zeros((6, 2, 2)))
        f.create_dataset("measurement/samX", data=np.full(6, 99.0))
    t = DataExplorerTab()
    t.set_source(str(src))
    app.processEvents()
    p = t._real
    _manual(p, "x", 0.0, 1.0, 6)
    vals = p._read_axis("x")
    assert vals is not None and not np.allclose(vals, 99.0), (
        "the dataset won over the manual grid")
    assert np.allclose(vals, np.arange(6.0))


def test_a_manual_grid_spans_files_rather_than_one_file_s_frames(app, tmp_path):
    """Regression, found on real data at the beamline.

    A manual grid is how you describe a scan that SPANS FILES -- a 13 x 64
    raster lives in thirteen 64-frame files. _read_axis took its point
    count from the primary source's frame count, so it generated 64 points
    with the slow axis stuck on its first value: one line instead of a
    grid, with no error anywhere.
    """
    h5py = pytest.importorskip("h5py")
    from midas_gui.tab_data_explorer import DataExplorerTab
    src = tmp_path / "one_of_thirteen.h5"
    with h5py.File(src, "w") as f:
        f.create_dataset("exchange/data", data=np.zeros((64, 2, 2)))
    t = _fresh(_alive)
    t.set_source(str(src))
    app.processEvents()
    p = t._real
    _manual(p, "x", 0.2758, 0.12059, 64)
    _manual(p, "y", 1.4490, 0.33333, 13)

    assert p._manual_points() == 832, "fell back to one file's frame count"
    y = p._read_axis("y")
    assert y.size == 832
    assert len(np.unique(np.round(y, 5))) == 13, "the slow axis never advanced"
    x = p._read_axis("x")
    assert np.allclose(x[:64], x[64:128]), "the fast axis is not cycling"
    assert "spans 13 files" in p._manual_note.text()


def test_a_single_manual_axis_still_cycles_over_every_frame(app, tmp_path):
    """The opposite intent, and why the point count is a max() rather than
    just the grid total: one axis of 10 points against 100 frames means
    "X repeats every 10", not "only plot 10 points"."""
    h5py = pytest.importorskip("h5py")
    src = tmp_path / "hundred.h5"
    with h5py.File(src, "w") as f:
        f.create_dataset("exchange/data", data=np.zeros((100, 2, 2)))
    t = _fresh(_alive)
    t.set_source(str(src))
    app.processEvents()
    p = t._real
    _manual(p, "x", 0.0, 1.0, 10)
    assert p._manual_points() == 100
    x = p._read_axis("x")
    assert x.size == 100 and np.allclose(x[:10], x[10:20])
