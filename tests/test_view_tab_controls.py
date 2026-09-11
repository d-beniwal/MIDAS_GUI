"""Data Viewer control changes: two-way geometry hand-off with Calibrate, the
"Accurate" radial pipeline, the cake's own R/η bins, calibrant-gated d-spacing
picking, card ordering, and one-shot vs. live ring simulation.

Not fork-isolated, for the same reason as ``test_view_bottom_tabs.py``: every
test in a forked file that builds a ``DataViewerTab`` dies with SIGSEGV in the
child. Run plain, these pass — see ``.context/STATE.md``.
"""
from __future__ import annotations

import numpy as np
import pytest
from PyQt5 import QtWidgets

from midas_gui.hydra_geometry_card import (ACCURATE_KERNEL, CAKE_ETA_BIN_DEG,
                                           FAST_KERNEL)
from midas_gui.tab_view import DataViewerTab

#: Index of the kernel entry in ``_midas_radial``'s context-cache signature.
_SIG_R_BIN, _SIG_ETA_BIN, _SIG_KERNEL = 11, 12, 13


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def tab(app):
    """One DataViewerTab for the whole module — building several in a process
    trips pyqtgraph's global named-view registry."""
    t = DataViewerTab()
    # Later tests mutate these controls, so the as-constructed values are
    # snapshotted here rather than asserted from whatever state a test that
    # happened to run earlier left behind.
    t._pristine = {"rad_accurate": t._rad_accurate.isChecked(),
                   "cake_eta_bin": t._cake_eta_bin.value(),
                   "cake_r_bin": t._cake_r_bin.value()}
    return t


@pytest.fixture
def framed(tab):
    """The tab showing a synthetic two-ring frame centred in the image, with a
    clean geometry card (no rings, no live mode, empty engine cache)."""
    n = 192
    zz, yy = np.indices((n, n))
    bc = (n - 1) / 2.0
    r = np.hypot(yy - bc, zz - bc)
    tab._cur = (1000.0 * np.exp(-((r - 30.0) ** 2) / 4.0)
                + 600.0 * np.exp(-((r - 60.0) ** 2) / 4.0)).astype(np.float32)
    card = tab._geom_card
    card._bc_auto.setChecked(False)
    card._bcy.setValue(bc); card._bcz.setValue(bc)
    card._ty.setValue(0.0); card._tz.setValue(0.0)
    card._clear_simulated_rings()
    card._calib_ctx_cache.clear()
    tab._rad_accurate.setChecked(False)
    tab._rad_auto.setChecked(False)
    tab._rad_r_bin.setValue(1.0)
    return tab


def _kernels(card):
    return sorted({sig[_SIG_KERNEL] for sig in card._calib_ctx_cache})


# ── 1) Send / Get geometry ───────────────────────────────────────────

def test_geometry_row_has_both_a_send_and_a_get_button(tab):
    card = tab._geom_card
    assert card._to_calib_btn.text() == "Send →"
    assert card._from_calib_btn.text() == "← Get"


def test_send_emits_the_current_geometry_and_get_emits_a_pull_request(tab):
    pushed, pulled = [], []
    tab.pushGeometry.connect(pushed.append)
    tab.pullGeometry.connect(lambda: pulled.append(True))
    try:
        tab._geom_card._to_calib_btn.click()
        tab._geom_card._from_calib_btn.click()
    finally:
        tab.pushGeometry.disconnect(pushed.append)
    assert len(pushed) == 1
    assert set(pushed[0]) >= {"wavelength_A", "pxY", "Lsd", "BC_y", "BC_z"}
    assert pulled == [True]        # "Get" only asks; MainWindow supplies


def test_calibrate_exposes_the_geometry_get_pulls(app):
    """``MainWindow._pull_geometry_from_calibrate`` reads this — with no
    calibration result it must say "nothing", not raise."""
    from midas_gui.tab_calibrate import CalibrationTab
    assert CalibrationTab.geometry_for_viewer(
        type("_S", (), {"_result": None})()) is None


# ── 2) Accurate radial pipeline ──────────────────────────────────────

def test_fast_default_uses_circle_binning_without_a_geometry(framed):
    card = framed._geom_card
    card.radial_integrate()
    assert _kernels(card) == []          # engine never built → circle binning
    assert framed._profile_view._curve.getData()[1].size


def test_fast_path_uses_the_hard_kernel_once_a_tilt_is_set(framed):
    card = framed._geom_card
    card._ty.setValue(3.0)
    card._calib_ctx_cache.clear()
    card.radial_integrate()
    assert _kernels(card) == [FAST_KERNEL]
    card._ty.setValue(0.0)


def test_accurate_forces_the_subpixel_engine_even_with_no_tilt(framed):
    card = framed._geom_card
    framed._rad_accurate.setChecked(True)
    card._calib_ctx_cache.clear()
    card.radial_integrate()
    assert _kernels(card) == [ACCURATE_KERNEL]
    r_ax, prof = framed._profile_view._curve.getData()
    assert r_ax[int(np.nanargmax(prof))] == pytest.approx(30.0, abs=1.5)


def test_accurate_is_off_by_default(tab):
    """The fast, live-view-capable profile stays the default."""
    assert tab._pristine["rad_accurate"] is False


def test_toggling_accurate_reintegrates_when_auto_is_on(framed):
    card = framed._geom_card
    framed._rad_auto.setChecked(True)
    card._calib_ctx_cache.clear()
    framed._rad_accurate.setChecked(True)
    assert _kernels(card) == [ACCURATE_KERNEL]
    framed._rad_auto.setChecked(False)


# ── 3) Cake: own R/η bins, same accurate pipeline ────────────────────

def test_cake_uses_its_own_bin_sizes_and_the_subpixel_engine(framed):
    card = framed._geom_card
    framed._cake_r_bin.setValue(2.0)
    framed._cake_eta_bin.setValue(10.0)
    card._calib_ctx_cache.clear()
    card.cake_integrate()
    sigs = list(card._calib_ctx_cache)
    assert [s[_SIG_KERNEL] for s in sigs] == [ACCURATE_KERNEL]
    assert [(s[_SIG_R_BIN], s[_SIG_ETA_BIN]) for s in sigs] == [(2.0, 10.0)]
    cv = framed._cake_view
    assert cv._cake.shape == (len(cv._eta_axis), len(cv._r_axis))
    assert np.diff(cv._eta_axis)[0] == pytest.approx(10.0)
    assert np.diff(cv._r_axis)[0] == pytest.approx(2.0)


def test_cake_bins_default_to_the_previous_hardcoded_values(tab):
    """Same cake you got before these controls existed, until you change them."""
    assert tab._pristine["cake_eta_bin"] == pytest.approx(CAKE_ETA_BIN_DEG)
    assert tab._pristine["cake_r_bin"] == pytest.approx(1.0)


def test_radial_integrate_no_longer_overwrites_the_cake(framed):
    """The two now bin differently, so only the cake's own Calculate may fill
    it — a profile refresh must leave it alone."""
    card = framed._geom_card
    framed._cake_r_bin.setValue(2.0); framed._cake_eta_bin.setValue(10.0)
    card.cake_integrate()
    cv = framed._cake_view
    before_cake, before_r = cv._cake.copy(), cv._r_axis.copy()
    framed._rad_r_bin.setValue(1.0)
    framed._rad_accurate.setChecked(True)
    card.radial_integrate()
    assert np.array_equal(cv._cake, before_cake)
    assert np.array_equal(cv._r_axis, before_r)


# ── 3b) the engine result is what actually reaches the plots ─────────
#
# Every test above checks the *context cache*, which ``_midas_radial`` fills
# before it integrates — so they all passed while the integration itself
# raised on every call and both plots silently fell back to circle binning
# (a 4-tuple unpacked into three). These pin the half the cache cannot see:
# the fallbacks must not run, and nothing may be logged to the error log.


def _watch_fallbacks(card, monkeypatch):
    """Record any call to the circle-binning fallbacks and the error log."""
    calls, errors = [], []
    for name in ("_radial_profile", "_cake_bin"):
        orig = getattr(card, name)

        def spy(*a, _n=name, _f=orig, **k):
            calls.append(_n)
            return _f(*a, **k)

        monkeypatch.setattr(card, name, spy)
    monkeypatch.setattr(card, "_log_error", errors.append)
    return calls, errors


def test_accurate_profile_comes_from_the_engine_not_the_fallback(framed, monkeypatch):
    card = framed._geom_card
    calls, errors = _watch_fallbacks(card, monkeypatch)
    framed._rad_accurate.setChecked(True)
    card._calib_ctx_cache.clear()
    card.radial_integrate()
    assert calls == [] and errors == []
    assert framed._profile_view._curve.getData()[1].size


def test_cake_comes_from_the_engine_not_the_fallback(framed, monkeypatch):
    card = framed._geom_card
    calls, errors = _watch_fallbacks(card, monkeypatch)
    card._calib_ctx_cache.clear()
    card.cake_integrate()
    assert calls == [] and errors == []
    assert framed._cake_view._cake is not None


def test_engine_profile_is_the_pixel_weighted_collapse_of_its_own_cake(framed):
    """The 1-D profile and the cake must come out of one ``integrate_frame``
    call, not two differently-binned ones — collapsing the cake by hand has to
    reproduce the profile exactly."""
    card = framed._geom_card
    framed._cake_r_bin.setValue(1.0)
    framed._cake_eta_bin.setValue(CAKE_ETA_BIN_DEG)
    card._calib_ctx_cache.clear()
    _, prof = card._midas_radial(framed._cur, card._engine_geom(framed._cur), None,
                                 kernel=ACCURATE_KERNEL, r_bin=1.0,
                                 eta_bin=CAKE_ETA_BIN_DEG, set_cake=True)
    cake = framed._cake_view._cake
    (_, ctx), = card._calib_ctx_cache.values()
    finite = np.isfinite(cake)
    w = np.where(finite, ctx["cnt"], 0.0)
    num = np.sum(np.where(finite, cake, 0.0) * w, axis=0)
    den = np.sum(w, axis=0)
    expect = np.nan_to_num(np.where(den > 0, num / np.where(den > 0, den, 1), np.nan))
    assert np.allclose(prof, expect, equal_nan=True)


def test_an_unedited_widget_keeps_the_calibrations_full_precision(framed):
    """The spinboxes show 1–4 decimals. Reading the geometry back out of them
    quantised a loaded calibration — ~0.5 px of ring placement at a 14° tilt —
    so a widget still parked on the file's value must not override it."""
    card = framed._geom_card
    card._calib_geom = {
        "wavelength_A": 0.172979, "Lsd": 1068594.201492,
        "BC_y": 3056.286943, "BC_z": 1344.076453,
        "tx": 0.0, "ty": -0.326893, "tz": 14.043908,
        "pxY": 150.0, "pxZ": 150.0, "NrPixelsY": 2880, "NrPixelsZ": 2880,
        "distortion": {}, "im_trans": [],
    }
    for w, key, scale in ((card._wl, "wavelength_A", 1.0), (card._lsd, "Lsd", 0.001),
                          (card._bcy, "BC_y", 1.0), (card._bcz, "BC_z", 1.0),
                          (card._ty, "ty", 1.0), (card._tz, "tz", 1.0),
                          (card._px, "pxY", 1.0)):
        w.setValue(card._calib_geom[key] * scale)      # what _load_calibration does
    geom = card._effective_calib_geom(framed._cur)
    for key in ("wavelength_A", "Lsd", "BC_y", "BC_z", "ty", "tz", "pxY", "pxZ"):
        assert geom[key] == card._calib_geom[key], key
    card._calib_geom = None


def test_an_edited_widget_still_overrides_the_calibration(framed):
    """The other half of the contract: a value the user actually moved must
    reach the profile, or a BC nudge would silently do nothing."""
    card = framed._geom_card
    card._calib_geom = {
        "wavelength_A": 0.172979, "Lsd": 1068594.201492,
        "BC_y": 3056.286943, "BC_z": 1344.076453,
        "tx": 0.0, "ty": -0.326893, "tz": 14.043908,
        "pxY": 150.0, "pxZ": 150.0, "NrPixelsY": 2880, "NrPixelsZ": 2880,
        "distortion": {}, "im_trans": [],
    }
    card._bcy.setValue(3056.3); card._bcz.setValue(1344.1)
    card._ty.setValue(-0.33); card._tz.setValue(14.04)
    card._bcy.setValue(3000.0)          # a real edit, one widget only
    geom = card._effective_calib_geom(framed._cur)
    assert geom["BC_y"] == 3000.0
    assert geom["BC_z"] == 1344.076453      # untouched neighbours keep precision
    assert geom["tz"] == 14.043908
    card._calib_geom = None


# ── 4) d-spacing picking follows the calibrant ───────────────────────

def _dsp_shown(tab):
    v = tab._viewer
    return (not v._pick_dsp_btn.isHidden(), not v._dsp_ring_spin.isHidden(),
            not v._dsp_ring_lbl.isHidden())


def test_dspacing_pick_controls_are_hidden_for_a_crystalline_material(tab):
    tab._geom_card.set_materials([
        dict(name="Ni (FCC)", a=3.52, b=3.52, c=3.52, alpha=90.0, beta=90.0,
             gamma=90.0, sg=225, enabled=True, color="#f0c060", preset="Ni (FCC)")])
    assert _dsp_shown(tab) == (False, False, False)


def test_dspacing_pick_controls_appear_for_agbh(tab):
    tab._geom_card.set_materials([
        dict(name="AgBH (silver behenate)", kind="dspacing",
             d_list=[58.38 / n for n in range(1, 11)], enabled=True,
             color="#f0c060", preset="AgBH (silver behenate)")])
    assert _dsp_shown(tab) == (True, True, True)


def test_disabling_the_dspacing_material_hides_them_again(tab):
    card = tab._geom_card
    card.set_materials([
        dict(name="AgBH (silver behenate)", kind="dspacing",
             d_list=[58.38 / n for n in range(1, 11)], enabled=True,
             color="#f0c060", preset="AgBH (silver behenate)")])
    card._on_material_enabled(card._materials[0], False)
    assert _dsp_shown(tab) == (False, False, False)
    card._on_material_enabled(card._materials[0], True)
    assert _dsp_shown(tab) == (True, True, True)


def test_hiding_them_also_leaves_dspacing_pick_mode(tab):
    card = tab._geom_card
    card.set_materials([
        dict(name="AgBH (silver behenate)", kind="dspacing",
             d_list=[58.38], enabled=True, color="#f0c060",
             preset="AgBH (silver behenate)")])
    tab._viewer._pick_dsp_btn.setChecked(True)
    assert tab._viewer._pick_mode == tab._viewer.PICK_DSPACING
    card.set_materials([
        dict(name="Ni (FCC)", a=3.52, b=3.52, c=3.52, alpha=90.0, beta=90.0,
             gamma=90.0, sg=225, enabled=True, color="#f0c060", preset="Ni (FCC)")])
    assert tab._viewer._pick_mode == tab._viewer.PICK_NONE


def test_calibrate_tabs_pick_bar_is_unaffected(app):
    """Only the Data Viewer gates these; the Calibrate tab drives them from
    its own calibrant combo, so the shared widget still defaults to visible."""
    from midas_gui.widgets import PickableImageViewer
    v = PickableImageViewer()
    assert not v._pick_dsp_btn.isHidden()


# ── 5) Card ordering ─────────────────────────────────────────────────

def test_transforms_sits_between_projection_and_ring_simulation(tab):
    column = tab._proj_grp.parentWidget().layout()
    order: list = []
    for i in range(column.count()):
        w = column.itemAt(i).widget()
        if isinstance(w, QtWidgets.QGroupBox):
            order.append(w.title())
        elif w is tab._geom_card:
            card_lv = w.layout()
            order += [card_lv.itemAt(j).widget().title()
                      for j in range(card_lv.count())
                      if isinstance(card_lv.itemAt(j).widget(), QtWidgets.QGroupBox)]
    assert order[:3] == ["Projection", "Transforms", "Ring simulation"]


# ── 6) Simulate rings: one-shot button + live tick + clear ───────────

def test_simulate_is_a_plain_button_with_a_live_tick_and_a_clear(tab):
    card = tab._geom_card
    assert not card._sim_btn.isCheckable()
    assert card._sim_live.text() == "live"
    assert card._sim_clear_btn.text() == "✕"


def test_one_shot_click_draws_rings_without_arming_live(framed):
    card = framed._geom_card
    card._sim_live.setChecked(False)
    card._sim_btn.click()
    assert card._any_material_rings()
    assert card._ring_items
    assert not card._sim_is_live()
    assert card._sim_btn.styleSheet() == ""      # not green
    assert card._sim_btn.text() == "Simulate rings"


def test_a_one_shot_simulation_is_frozen_against_later_edits(framed):
    card = framed._geom_card
    card._sim_live.setChecked(False)
    card._sim_btn.click()
    frozen = dict(card._ring_draw_geom)
    card._bcy.setValue(frozen["bc_y"] + 25.0)
    card._ty.setValue(4.0)
    card._wl.setValue(card._wl.value() * 1.05)
    assert card._ring_draw_geom == frozen
    card._ty.setValue(0.0)


def test_clicking_with_live_ticked_arms_live_mode_and_turns_green(framed):
    card = framed._geom_card
    card._sim_live.setChecked(True)
    card._sim_btn.click()
    assert card._sim_is_live()
    assert "2e7d32" in card._sim_btn.styleSheet()
    assert card._sim_btn.text() == "Simulate rings (live)"


def test_ticking_live_alone_changes_nothing_until_clicked(framed):
    card = framed._geom_card
    card._sim_live.setChecked(True)
    assert not card._sim_is_live()
    assert not card._any_material_rings()


def test_live_rings_track_the_beam_centre_and_lattice(framed):
    card = framed._geom_card
    card._sim_live.setChecked(True)
    card._sim_btn.click()
    card._bcy.setValue(60.0)
    assert card._ring_draw_geom["bc_y"] == pytest.approx(60.0)
    radii = [r["radius_px"] for r in card._materials[0]["_rings"]]
    card._lsd.setValue(card._lsd.value() * 1.5)
    assert [r["radius_px"] for r in card._materials[0]["_rings"]] != radii


def test_unticking_live_disarms_and_freezes(framed):
    card = framed._geom_card
    card._sim_live.setChecked(True)
    card._sim_btn.click()
    card._sim_live.setChecked(False)
    assert not card._sim_is_live()
    assert card._sim_btn.styleSheet() == ""
    frozen = dict(card._ring_draw_geom)
    card._bcy.setValue(frozen["bc_y"] + 20.0)
    assert card._ring_draw_geom == frozen


def test_clear_button_removes_every_simulated_ring_and_disarms_live(framed):
    card = framed._geom_card
    card._sim_live.setChecked(True)
    card._sim_btn.click()
    assert card._ring_items

    card._sim_clear_btn.click()
    assert not card._any_material_rings()
    assert card._ring_items == [] and card._label_items == []
    assert card._ring_draw_geom is None
    # disarmed too, or the next parameter edit would redraw them at once
    assert not card._sim_is_live() and not card._sim_live.isChecked()
    assert card._ring_info.toPlainText() == ""


def test_clear_leaves_the_click_picked_radius_ring_alone(framed):
    """The magenta ring comes from clicking the profile, not from a
    simulation — "✕" is scoped to the simulated overlay."""
    card = framed._geom_card
    card._sim_btn.click()
    card.on_radius_clicked(42.0)
    assert card._pick_ring_item is not None

    card._sim_clear_btn.click()
    assert card._ring_items == []
    assert card._picked_r == 42.0 and card._pick_ring_item is not None
