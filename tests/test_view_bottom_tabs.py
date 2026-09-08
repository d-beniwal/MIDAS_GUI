"""Data Viewer bottom tab strip: Statistics sits next to Radial Profile /
Eta vs R Cake and opens first, the cake's "Calculate" button computes a cake
on demand (including with no calibration loaded), and the intensity readout
carries min/max/std.
"""
from __future__ import annotations

import numpy as np
import pytest
from PyQt5 import QtWidgets

from midas_gui.hydra_geometry_card import CAKE_ETA_BIN_DEG, DetectorGeometryCard
from midas_gui.helpers import _fspin
from midas_gui.tab_view import DataViewerTab
from midas_gui.widgets import CakeViewer, IntensityStatsPanel

# NOT fork-isolated, unlike the other DataViewerTab test files: on this
# machine every test in a forked file that builds a DataViewerTab dies with
# SIGSEGV in the child (the same reason test_hydra_ui.py's 8 tests are in the
# known-failing baseline — os.fork() is unsafe once torch/Qt/HDF5 threads are
# up). Run plain, these pass; see .context/STATE.md.


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def tab(app):
    """One DataViewerTab for the whole module. Building several in a process
    is what trips pyqtgraph's global named-view registry (the crash behind the
    known-failing UI test files), and nothing here needs a second one."""
    return DataViewerTab()


# ── tab placement ────────────────────────────────────────────────────

def test_statistics_is_first_bottom_tab_and_selected(tab):
    bot = tab._bottom_tabs
    assert [bot.tabText(i) for i in range(bot.count())] == [
        "Statistics", "Radial Profile", "Eta vs R Cake"]
    assert bot.currentIndex() == 0
    assert bot.widget(0) is tab._loader.stats_panel


def test_stats_panel_is_not_in_the_loader_column(tab):
    """It is built by the loader but placed by the tab — the loader must not
    also parent it into its own scrolling card column."""
    sp = tab._loader.stats_panel
    assert sp is not None
    assert not tab._loader.isAncestorOf(sp)


# ── statistics readout ───────────────────────────────────────────────

def test_stats_readout_reports_min_max_std(app):
    panel = IntensityStatsPanel()
    vals = np.array([0.0, 1.0, 2.0, 3.0, 10.0])
    panel.set_data(vals, scope="Frame 0")
    text = panel._text.toPlainText()
    lines = dict(l.split("=", 1) for l in text.splitlines() if "=" in l)
    assert float(lines["min    "]) == pytest.approx(vals.min())
    assert float(lines["max    "]) == pytest.approx(vals.max())
    assert float(lines["mean   "]) == pytest.approx(vals.mean())
    assert float(lines["std    "]) == pytest.approx(vals.std())


def test_readout_sits_beside_the_histogram(app):
    """Plot and numbers share one horizontal row (they used to be stacked),
    and the readout is laid out to the right of the plot, not below it."""
    panel = IntensityStatsPanel()
    panel.set_data(np.arange(1000.0), scope="Frame 0")
    v = panel.layout()
    rows = [v.itemAt(i).layout() for i in range(v.count())]
    shared = [r for r in rows
              if isinstance(r, QtWidgets.QHBoxLayout)
              and {r.itemAt(j).widget() for j in range(r.count())}
              >= {panel._plot, panel._text}]
    assert shared, "histogram and readout are not in a shared horizontal row"

    panel.resize(700, 220)
    panel.show()
    QtWidgets.QApplication.instance().processEvents()
    assert panel._text.geometry().left() >= panel._plot.geometry().right()
    # the readout hugs its content; the histogram takes the rest of the width
    assert panel._plot.width() > panel._text.width()


def test_stats_readout_font_is_20_percent_smaller(app):
    panel = IntensityStatsPanel()
    assert panel._text.font().pointSizeF() == pytest.approx(0.8 * 8)


def test_stats_readout_survives_an_all_equal_frame(app):
    """min == max is the degenerate case the histogram range guard patches
    up — the readout must still report the real values."""
    panel = IntensityStatsPanel()
    panel.set_data(np.full(16, 7.0), scope="Frame 0")
    text = panel._text.toPlainText()
    assert "min    = 7" in text and "max    = 7" in text
    assert "std    = 0" in text


# ── cake Calculate ───────────────────────────────────────────────────

def _card_with_cake(img, mask=None):
    """A geometry card bound to ``img`` and a cake view, no calibration."""
    card = DetectorGeometryCard()
    card.set_image_source(lambda: img, (lambda _i: mask) if mask is not None else None)
    cake_view = CakeViewer()
    card.set_cake_view(cake_view)
    r_bin = _fspin(0.1, 20.0, 2, 1.0, "px")
    card.set_radial_controls(r_bin, QtWidgets.QCheckBox("Auto"))
    return card, cake_view


def test_calculate_button_computes_a_cake_without_calibration(tab):
    n = 48
    zz, yy = np.indices((n, n))
    bc = (n - 1) / 2.0
    tab._cur = np.hypot(yy - bc, zz - bc)     # frame the geometry card reads
    tab._geom_card._bcy.setValue(bc); tab._geom_card._bcz.setValue(bc)
    assert tab._cake_view._cake is None
    tab._cake_btn.click()
    cake = tab._cake_view._cake
    assert cake is not None and cake.ndim == 2
    n_eta, n_r = cake.shape
    assert n_eta == int(round(360.0 / CAKE_ETA_BIN_DEG))
    assert n_r == tab._cake_view._r_axis.size
    assert np.isfinite(cake).all()


def test_cake_bin_axes_and_bin_means(app):
    """A frame whose intensity is its own radius: every (η, R) bin's value is
    that bin's radius, and the η axis spans the full circle."""
    n = 64
    zz, yy = np.indices((n, n))
    bc = (n - 1) / 2.0
    img = np.hypot(yy - bc, zz - bc)
    card, _ = _card_with_cake(img)
    cake, r_axis, eta_axis = card._cake_bin(img, bc, bc, r_bin=2.0)

    assert eta_axis.size == cake.shape[0] == int(round(360.0 / CAKE_ETA_BIN_DEG))
    assert eta_axis[0] == pytest.approx(-180.0 + CAKE_ETA_BIN_DEG / 2.0)
    assert eta_axis[-1] == pytest.approx(180.0 - CAKE_ETA_BIN_DEG / 2.0)
    assert r_axis.size == cake.shape[1]
    filled = cake > 0
    # each filled bin holds the mean radius of its pixels — within half a bin
    # of the bin centre
    r_grid = np.broadcast_to(r_axis, cake.shape)
    assert np.abs(cake[filled] - r_grid[filled]).max() < 1.0


def test_cake_bin_eta_matches_the_overlay_convention(app):
    """η = 0° is +Z (up on screen, where row index increases upward — every
    viewer puts pixel (0,0) bottom-left), and η grows toward +Y: the same
    convention helpers.draw_polar_bin_overlay draws its spokes with."""
    n = 65
    bc = (n - 1) / 2.0
    card, _ = _card_with_cake(np.zeros((n, n)))

    def peak_eta_r(dy, dz):
        img = np.zeros((n, n))
        img[int(bc + dz), int(bc + dy)] = 100.0
        cake, r_axis, eta_axis = card._cake_bin(img, bc, bc, r_bin=1.0)
        i_eta, i_r = np.unravel_index(np.argmax(cake), cake.shape)
        return eta_axis[i_eta], r_axis[i_r]

    eta, r = peak_eta_r(0, +20)             # straight up  → η ≈ 0°
    assert abs(eta) <= CAKE_ETA_BIN_DEG
    assert r == pytest.approx(20.0, abs=1.0)
    assert abs(peak_eta_r(+20, 0)[0] - 90.0) <= CAKE_ETA_BIN_DEG    # +Y → +90°
    assert abs(peak_eta_r(-20, 0)[0] + 90.0) <= CAKE_ETA_BIN_DEG    # -Y → -90°
    assert abs(abs(peak_eta_r(0, -20)[0]) - 180.0) <= CAKE_ETA_BIN_DEG


def test_cake_bin_straightens_a_ring(app):
    """The point of a cake: a ring about the beam centre must sit at one R in
    every η bin."""
    NZ, NY, bcy, bcz = 256, 400, 180.0, 120.0
    zz, yy = np.indices((NZ, NY))
    r = np.hypot(yy - bcy, zz - bcz)
    img = 1000.0 * np.exp(-((r - 100.0) ** 2) / 8.0) + 5.0
    card, _ = _card_with_cake(img)
    cake, r_axis, eta_axis = card._cake_bin(img, bcy, bcz, r_bin=1.0)
    peak_r = r_axis[np.argmax(cake, axis=1)]
    assert eta_axis.size == peak_r.size
    assert np.abs(peak_r - 100.0).max() <= 1.0


def test_cake_bin_honours_the_mask(app):
    n = 32
    bc = (n - 1) / 2.0
    img = np.ones((n, n))
    mask = np.zeros((n, n), dtype=bool)
    mask[:, :] = True                       # everything excluded
    card, _ = _card_with_cake(img, mask=mask)
    cake, _, _ = card._cake_bin(img, bc, bc, r_bin=1.0, mask=mask)
    assert not cake.any()                   # empty bins are 0.0, not NaN
