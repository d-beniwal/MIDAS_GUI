"""Hydra Calibrate features brought in line with single-detector Calibrate:
per-panel masks, the Mask / Lab-frame overlays, Rings… exclusion, stack
projection (no double correction) and the four-panel comparison tab."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def page(app, tmp_path_factory):
    h5py = pytest.importorskip("h5py")
    from midas_gui.hydra_calib_page import HydraCalibrationPage
    d = tmp_path_factory.mktemp("hcal")
    for n in (1, 2, 3, 4):
        with h5py.File(str(d / f"c_00001.ge{n}.h5"), "w") as f:
            f.create_dataset("exchange/data", data=np.ones((3, 32, 32), np.float32))
    pg = HydraCalibrationPage()
    pg._loader._set_path(str(d / "c_00001.ge1.h5"))
    return pg


# ── masks ──────────────────────────────────────────────────────────────

def test_calibrate_loader_has_per_panel_masks_but_the_viewer_loader_does_not(app, page):
    from midas_gui.hydra_widgets import HydraLoaderPanel
    assert sorted(page._loader._mask_sels) == [1, 2, 3, 4]
    assert HydraLoaderPanel()._mask_sels == {}                 # Data Viewer: unchanged
    assert sorted(HydraLoaderPanel(mode="stream")._mask_sels) == [1, 2, 3, 4]


def test_each_panels_fit_gets_its_own_mask(app, page, monkeypatch):
    masks = {n: np.full((32, 32), n, np.uint8) for n in (1, 2, 3, 4)}
    monkeypatch.setattr(page._loader, "composite_mask", lambda n: masks.get(n))
    for n in (1, 2, 3, 4):
        assert page._build_cfg(page._cards[n])["mask"] is masks[n]


def test_mask_overlay_reports_pixels_and_flags_a_shape_mismatch(app, page, monkeypatch):
    page._on_panel_changed("ge1")
    good = np.zeros((32, 32), np.uint8); good[:4] = 1
    monkeypatch.setattr(page._loader, "composite_mask", lambda n: good)
    page._update_mask_overlay()
    assert "128 px" in page._mask_status.text() and "excluded" in page._mask_status.text()
    monkeypatch.setattr(page._loader, "composite_mask", lambda n: np.zeros((8, 8), np.uint8))
    page._update_mask_overlay()
    assert "≠" in page._mask_status.text()
    monkeypatch.setattr(page._loader, "composite_mask", lambda n: None)
    page._update_mask_overlay()
    assert page._mask_status.text() == ""


# ── lab-frame axes ─────────────────────────────────────────────────────

def test_lab_axes_follow_the_active_panel_and_its_fixed_tx(app, page, monkeypatch):
    seen = []
    import midas_gui.widgets as W
    monkeypatch.setattr(W, "build_lab_frame_axes_items",
                        lambda iv, shape, by, bz, tx=0.0: seen.append((by, bz, tx)) or [])
    page._img_view.set_raw_frame(np.ones((32, 32), np.float32), ())
    for n, tx in ((2, 30.0), (4, 210.0)):
        page._on_panel_changed(f"ge{n}")
        page._cards[n]._seed_bcy.setValue(10.0 + n)
        page._lab_axes_on.setChecked(False); page._lab_axes_on.setChecked(True)
        assert seen[-1][2] == tx and seen[-1][0] == pytest.approx(10.0 + n)
    page._lab_axes_on.setChecked(False)
    assert page._axis_items == []


# ── Rings… ─────────────────────────────────────────────────────────────

def test_rings_exclusion_is_shared_resets_on_calibrant_change_and_reaches_the_cfg(app, page):
    page._rings_excluded = [2, 5]
    page._sync_rings_btn()
    assert page._rings_btn.text() == "Rings (2 off)…"
    for n in (1, 2, 3, 4):
        assert page._build_cfg(page._cards[n])["rings_to_exclude"] == [2, 5]
    state = page.get_state()
    assert state["rings_excluded"] == [2, 5]
    page._rings_excluded = []
    page.set_state(state)
    assert page._rings_excluded == [2, 5]
    other = "LaB6" if page._cal.currentText() != "LaB6" else "CeO2"
    if page._cal.findText(other) >= 0:
        page._cal.setCurrentText(other)
        assert page._rings_excluded == [] and page._rings_btn.text() == "Rings…"
        assert "rings_to_exclude" not in page._build_cfg(page._cards[1])


def test_ring_rows_are_one_per_ring_number_across_all_panels(app, page):
    page._cal.setCurrentText("CeO2")
    for n, c in page._cards.items():                    # four different geometries
        c._seed_lsd.setValue(2390.0 + 2 * n)
        c._seed_bcy.setValue(2200.0 + 20 * n); c._seed_bcz.setValue(2100.0)
    rows = page._ring_rows_all_panels()
    nrs = [r["ring_nr"] for r in rows]
    assert nrs and nrs == sorted(set(nrs))


# ── projection: no double correction ───────────────────────────────────

def test_a_projected_frame_is_used_and_gets_no_second_correction(app, page, monkeypatch):
    proj = np.full((32, 32), 7.0, np.float32)
    monkeypatch.setattr(page._loader, "projected", lambda n: proj if n == 1 else None)
    monkeypatch.setattr(page._loader, "dark", lambda n: np.ones((32, 32)))
    assert page._panel_raw_image(1) is proj
    d, b, bg, _m = page._corr_fields(1)
    assert d is None and b is None and bg is None            # already corrected
    d2, *_ = page._corr_fields(2)
    assert d2 is not None                                     # un-projected panel: unchanged
    page._on_projection_changed()
    assert not page._avg_card.isEnabled(), "Mean of frames must be off while projected"
    monkeypatch.setattr(page._loader, "projected", lambda n: None)
    page._on_projection_changed()


# ── four-panel comparison ──────────────────────────────────────────────

def test_the_panels_tab_compares_the_fits_and_flags_an_lsd_outlier(app, page):
    def res(lsd, strain):
        return SimpleNamespace(tx=0.0, Lsd=lsd, BC_y=2270.0, BC_z=2150.0, ty=0.1, tz=0.2,
                               post_residual_strain_uE=strain)
    try:
        for n, lsd in zip((1, 2, 3, 4), (2393771.0, 2395923.0, 2391407.0, 2392204.0)):
            page._cards[n].result = res(lsd, 1500.0 + n)
        page._update_panels_table()
        t = page._panels_table
        cols = [c[0] for c in page._PANEL_COLS]
        assert t.item(0, cols.index("tx (°)")).text() == "300.0"          # fixed, from hydra
        assert t.item(1, cols.index("Lsd (mm)")).text() == "2395.923"
        assert "Lsd spread" in page._panels_note.text() and "2θ, Q and d" in page._panels_note.text()
        col = cols.index("Lsd (mm)")
        assert t.item(1, col).background().color().alpha() > 0           # ge2 is highlighted
        for n in (1, 2, 3, 4):                                           # tight panels: no flag
            page._cards[n].result = res(2393000.0 + n, 1500.0)
        page._update_panels_table()
        assert "Highlighted" not in page._panels_note.text()
    finally:
        for c in page._cards.values():
            c.result = None
        page._update_panels_table()
