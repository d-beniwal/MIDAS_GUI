"""The 3-D pole figure panel.

Matplotlib, not pyqtgraph, so the ~2-test cap the GUI files live under
does not apply -- that cap is a pyqtgraph teardown problem and
``tests/test_tab_zarrviewer.py`` (the other matplotlib tab) runs a dozen
functions clean. Qt imports still go inside fixtures.

Two things here are worth more than the rendering checks: that importing
this module does not restyle the other matplotlib views in the process,
and that the frame-count auto-fill reports frames rather than radial bins.
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
def cake_csv(tmp_path):
    """A cake_parameters CSV in the format this app also writes."""
    from midas_gui.cake_params import write_cake_csv
    p = tmp_path / "cake_parameters_ge1.csv"
    write_cake_csv(str(p), {
        "R_MIN": 440.0, "R_MAX": 2200.0, "R_STEP": 0.5,
        "ETA_MIN": 120.0, "ETA_MAX": 210.0, "ETA_STEP": 10.0,
        "OME_SUM": 1.0, "OME_START": 0.0, "OME_STEP": 1.0})
    return str(p)


def _panel(app, catalog=None):
    from midas_gui.metadata_catalog import MetadataCatalog
    from midas_gui.pole_figure_3d_panel import PoleFigure3DPanel
    return PoleFigure3DPanel(catalog or MetadataCatalog())


# ── the import contract ─────────────────────────────────────────────────

def test_importing_does_not_restyle_the_other_matplotlib_views(app):
    """The source mutated seven global rcParams at import time. Two other
    host modules (peak_fit_panel, tab_zarrviewer) draw with matplotlib and
    would silently inherit that -- a cross-tab visual change with no
    visible cause. Only the backend may differ, which is
    matplotlib.use("Qt5Agg") and is what both of those do too.
    """
    import matplotlib
    before = dict(matplotlib.rcParams)
    import midas_gui.pole_figure_3d_panel  # noqa: F401
    changed = {k for k in before if before[k] != matplotlib.rcParams[k]}
    assert changed <= {"backend", "backend_fallback"}, changed


# ── building and drawing ────────────────────────────────────────────────

def test_it_builds_with_a_three_dimensional_axes(app):
    p = _panel(app)
    assert p.fig.axes, "no axes created"
    assert hasattr(p.fig.axes[0], "zaxis"), "not a 3-D projection"


def test_an_empty_catalog_draws_without_raising(app):
    """Opening the tab before choosing any source is the first thing every
    user does."""
    _panel(app)._refresh()


def test_a_cake_csv_gives_it_something_to_draw(app, cake_csv):
    from midas_gui.metadata_catalog import MetadataCatalog
    c = MetadataCatalog()
    c.set_cake_csv(cake_csv)
    p = _panel(app, c)
    p._refresh()
    ax = p.fig.axes[0]
    assert hasattr(ax, "zaxis")
    assert ax.collections or ax.lines, "nothing was drawn from a valid cake CSV"


def test_every_view_preset_applies(app, cake_csv):
    """Eight canned viewpoints plus Free; a bad elev/azim pair raises
    inside matplotlib rather than being ignored."""
    from midas_gui.metadata_catalog import MetadataCatalog
    c = MetadataCatalog(); c.set_cake_csv(cake_csv)
    p = _panel(app, c)
    p._refresh()
    for key in ("xp", "xn", "yp", "yn", "zp", "zn", "iso", "free"):
        p._set_projection_view(key)
        assert p._view_axis == key, f"{key} did not take"
    # ...and the last one must still render.
    p._refresh()
    assert p.fig.axes and hasattr(p.fig.axes[0], "zaxis")


def test_an_unknown_view_key_is_ignored_rather_than_applied(app, cake_csv):
    """Worth pinning before S6 persists this: _set_projection_view drops
    anything outside its eight names SILENTLY, so a project carrying a
    stale key leaves the view wherever it was rather than erroring. That
    is the right behaviour for a restore, but only if it is deliberate.
    """
    from midas_gui.metadata_catalog import MetadataCatalog
    c = MetadataCatalog(); c.set_cake_csv(cake_csv)
    p = _panel(app, c)
    p._set_projection_view("iso")
    p._set_projection_view("not-a-view")
    assert p._view_axis == "iso"


# ── the frame-count correction ──────────────────────────────────────────

def test_the_frame_count_is_frames_not_radial_bins(app, tmp_path):
    """The source read IntegrationResult/FrameNr_0.shape[0], which in a
    MIDAS archive is (nR, nEta) -- so the "# raw frames" auto-fill showed
    the radial bin count, 3520 instead of 64 on a real file. This panel now
    defers to stream_sources.infer_frame_count.
    """
    zarr = pytest.importorskip("zarr")
    from midas_gui.metadata_catalog import MetadataCatalog
    arc = tmp_path / "a.zarr.zip"
    store = zarr.ZipStore(str(arc), mode="w")
    z = zarr.group(store)
    z["Omegas"] = np.arange(64.0)
    z["REtaMap"] = np.zeros((5, 3520, 9))
    for i in range(64):
        z[f"IntegrationResult/FrameNr_{i}"] = np.zeros((3520, 9))
    store.close()
    c = MetadataCatalog(); c.set_primary_source(str(arc))
    p = _panel(app, c)
    assert p._infer_raw_frame_count() == 64, "radial bins reported as frames"


def test_no_source_means_no_frame_count(app):
    assert _panel(app)._infer_raw_frame_count() is None


# ── sibling panel discovery ─────────────────────────────────────────────

def test_sibling_cake_files_are_found_for_all_four_panels(app, tmp_path):
    """One pick overlays all four GE panels -- that is the reason this view
    exists rather than being per-panel."""
    from midas_gui.cake_params import write_cake_csv
    from midas_gui.stream_sources import discover_cake_siblings
    vals = {"R_MIN": 440.0, "R_MAX": 2200.0, "R_STEP": 0.5,
            "ETA_MIN": 0.0, "ETA_MAX": 90.0, "ETA_STEP": 10.0,
            "OME_SUM": 1.0, "OME_START": 0.0, "OME_STEP": 1.0}
    for n in (1, 2, 3, 4):
        write_cake_csv(str(tmp_path / f"cake_ge{n}.csv"), vals)
    found = discover_cake_siblings(str(tmp_path / "cake_ge1.csv"))
    assert [t for t, _p in found] == ["ge1", "ge2", "ge3", "ge4"]
