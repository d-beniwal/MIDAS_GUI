"""Qt-level wiring test for the Calibrate tab's manual d-spacing ring-picking
fit mode (non-crystalline calibrants like AgBH) — exercises the
PICK_DSPACING pick mode, the live per-ring summary, the Fit button gating,
and that a fitted result flows through the same _on_done pipeline as a
regular midas-calibrate-v2 result (ring overlay uses the new `_d_list`
branch of `_predict_ring_radii`, not the CeO2 fallback).
"""
from types import SimpleNamespace

import pytest

# Each test here builds at least one full CalibrationTab (a pyqtgraph
# ImageView plus several PlotWidgets); a couple build a second one for a
# get_state()/set_state() round trip. Per .context/DECISIONS.md's
# pyqtgraph-teardown-crash entry (see test_hydra_calib_ui.py/test_project.py
# for the same fix), enough accumulated pyqtgraph instances across a whole
# pytest run's garbage collection reliably segfaults the interpreter — run
# each test in its own forked subprocess (pytest-forked) so that aborts only
# that subprocess, reported as a normal FAILED with signal info, instead of
# crashing the whole pytest run.
pytestmark = pytest.mark.forked


_QT_LOADED = False

# Bound by _load_qt() at fixture time, declared here so static analysis
# (and the pyflakes diff in the review recipe) can still resolve them.
QtCore = QtWidgets = None
_FakeManualDspacingCalibWorker = _FakeIntegrationWorker = None


def _load_qt():
    """Import Qt and the QObject-subclass fakes, publishing them as module
    globals.

    Deliberately NOT done at module level. pytest imports this module during
    collection, in the *parent* process, while pytest-forked (see
    ``pytestmark`` above) runs each test in a forked child. Importing PyQt5
    in the parent initialises macOS CoreFoundation, which a forked child may
    not use — all 17 tests then die with SIGSEGV ("The process has forked and
    you cannot use this CoreFoundation functionality safely") before their
    bodies run. Importing here means each child does its own first-time
    init, which is legal.

    The two fakes subclass ``QtCore.QObject`` and declare ``pyqtSignal``
    class attributes, so they cannot be defined at module scope either —
    that alone would force the import at collection time. They are defined
    here and published into globals() so the test bodies below can go on
    referring to them by bare name. See .context/STATE.md.
    """
    global _QT_LOADED
    if _QT_LOADED:
        return
    from PyQt5 import QtCore, QtWidgets

    class _FakeManualDspacingCalibWorker(QtCore.QObject):
        """No-op-thread fake: finishes on the next event-loop tick with a fixed
        known result, instead of actually running least_squares."""
        log_line = QtCore.pyqtSignal(str)
        finished = QtCore.pyqtSignal(object)
        failed = QtCore.pyqtSignal(str)

        def __init__(self, picks, wavelength_A, pxY, pxZ, seed, NY, NZ,
                     material_name, d_list, parent=None,
                     refine=None, tilt_seed=(0.0, 0.0, 0.0), bounds=None):
            super().__init__(parent)
            self.refine = refine
            self.bounds = bounds
            self._wavelength_A = wavelength_A
            self._pxY = pxY
            self._pxZ = pxZ
            self._NY = NY
            self._NZ = NZ
            self._material_name = material_name
            self._d_list = d_list
            self._refine = refine
            self._tilt_seed = tilt_seed

        def start(self):
            QtCore.QTimer.singleShot(0, self._finish)

        def isRunning(self) -> bool:
            return False

        def requestInterruption(self):
            pass

        def _finish(self):
            result = SimpleNamespace(
                Lsd=300000.0, BC_y=512.0, BC_z=498.0, tx=0.0, ty=0.0, tz=0.0,
                distortion={}, pxY=self._pxY, pxZ=self._pxZ or self._pxY,
                NrPixelsY=self._NY, NrPixelsZ=self._NZ,
                wavelength_A=self._wavelength_A, post_residual_strain_uE=None,
                _calibrant_name=self._material_name, _d_list=list(self._d_list))
            self.finished.emit(result)


    class _FakeIntegrationWorker(QtCore.QObject):
        """No-op-thread fake for the real ``IntegrationWorker`` that ``_on_done``
        kicks off automatically after any successful fit (manual or not) — the
        Calibrate tab auto-loads ``DEFAULT_CALIBRANT_TIF`` at construction, so
        ``_calib_image()`` is never None and a real background thread (importing
        torch/midas_calibrate_v2) would otherwise be left running past test/
        interpreter teardown."""
        log_line = QtCore.pyqtSignal(str)
        finished = QtCore.pyqtSignal(object)
        failed = QtCore.pyqtSignal(str)

        def __init__(self, result, image, dark, im_trans, r_bin, eta_bin, mask=None,
                     parent=None, bright=None, background=None, bright_mode="divide",
                     weighted=True):
            super().__init__(parent)
            self._result = result

        def start(self):
            QtCore.QTimer.singleShot(0, self._finish)

        def isRunning(self) -> bool:
            return False

        def requestInterruption(self):
            pass

        def _finish(self):
            import numpy as np
            r_axis = np.linspace(0, 100, 50)
            profile = np.ones_like(r_axis)
            eta_axis = np.linspace(-180, 180, 36)
            cake = np.ones((len(eta_axis), len(r_axis)))
            self.finished.emit({"r_axis_px": r_axis, "profile": profile,
                                "wavelength_A": self._result.wavelength_A,
                                "lsd_um": self._result.Lsd, "px_um": self._result.pxY,
                                "cake_2d": cake, "eta_axis_deg": eta_axis})

    _QT_LOADED = True
    globals().update(
        QtCore=QtCore, QtWidgets=QtWidgets,
        _FakeManualDspacingCalibWorker=_FakeManualDspacingCalibWorker,
        _FakeIntegrationWorker=_FakeIntegrationWorker,
    )


@pytest.fixture(scope="module")
def app():
    _load_qt()
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_manual_fit_pick_summary_and_button_gating(app, monkeypatch):
    import midas_gui.tab_calibrate as tab_calibrate_mod
    monkeypatch.setattr(tab_calibrate_mod, "ManualDspacingCalibWorker",
                        _FakeManualDspacingCalibWorker)
    monkeypatch.setattr(tab_calibrate_mod, "IntegrationWorker",
                        _FakeIntegrationWorker)
    tab = tab_calibrate_mod.CalibrationTab()

    # isVisible() reflects on-screen visibility (requires the window to be
    # shown); isHidden() reflects the explicit setVisible()/hide() state
    # independent of ancestors, which is what these mode-switch toggles set.
    idx = tab._cal.findText("CeO2")
    assert idx >= 0
    tab._cal.setCurrentIndex(idx)
    assert not tab._refc_card.isHidden()
    assert tab._manual_card.isHidden()

    idx = tab._cal.findText("AgBH (silver behenate)")
    assert idx >= 0
    tab._cal.setCurrentIndex(idx)
    # The Refine parameters card is now shared with the crystalline path (same
    # checkboxes drive both) — only the distortion/residual-map row, which the
    # manual point-pick fit has no forward model for, is hidden for it.
    assert not tab._refc_card.isHidden()
    assert tab._dist_row.isHidden()
    assert tab._build_rc.isHidden()
    assert not tab._manual_card.isHidden()
    assert tab._dsp_custom_ed.isHidden()
    assert not tab._run_btn.isEnabled()

    # ty/tz default on (shared with CeO2's defaults) would raise the minimum
    # pick count past 3 — uncheck them here so the pick-count assertions below
    # exercise the historical Lsd+BC-only 3-point gate; tilt-refinement's
    # higher threshold is covered separately.
    tab._ref_ty.setChecked(False)
    tab._ref_tz.setChecked(False)

    view = tab._img_view
    view._dsp_ring_spin.setValue(1)
    for x, y in [(100.0, 0.0), (0.0, 100.0), (-100.0, 0.0)]:
        view._add_dspacing_point(512.0 + x, 498.0 + y)

    # 3 points on a single ring already satisfies the ">=3 total" gate.
    assert tab._run_btn.isEnabled()
    assert "Ring 1" in tab._dsp_summary.text()
    assert "58.380" in tab._dsp_summary.text()

    view._dsp_ring_spin.setValue(2)
    for x, y in [(50.0, 0.0), (0.0, 50.0), (-50.0, 0.0)]:
        view._add_dspacing_point(512.0 + x, 498.0 + y)
    assert "Ring 2" in tab._dsp_summary.text()
    assert "29.190" in tab._dsp_summary.text()

    # A ring index beyond the material's d-spacing count is flagged, not silently dropped.
    view._dsp_ring_spin.setValue(20)
    view._add_dspacing_point(700.0, 700.0)
    assert "invalid" in tab._dsp_summary.text()


def test_manual_fit_min_picks_rises_with_more_refine_flags(app, monkeypatch):
    import midas_gui.tab_calibrate as tab_calibrate_mod
    monkeypatch.setattr(tab_calibrate_mod, "ManualDspacingCalibWorker",
                        _FakeManualDspacingCalibWorker)
    monkeypatch.setattr(tab_calibrate_mod, "IntegrationWorker",
                        _FakeIntegrationWorker)
    tab = tab_calibrate_mod.CalibrationTab()

    idx = tab._cal.findText("AgBH (silver behenate)")
    assert idx >= 0
    tab._cal.setCurrentIndex(idx)

    # Lsd+BC only: 3 free parameters, so 3 picks are enough. Set every flag
    # explicitly rather than relying on the d-spacing defaults — those are
    # deliberately BC-only now (see test_dspacing_calibrant_defaults_to_bc_only),
    # and this test is about the gate tracking the flags, not about the default.
    tab._ref_lsd.setChecked(True)
    tab._ref_bc.setChecked(True)
    tab._ref_tx.setChecked(False)
    tab._ref_ty.setChecked(False)
    tab._ref_tz.setChecked(False)
    tab._ref_wl.setChecked(False)
    assert tab._manual_min_picks() == 3

    view = tab._img_view
    view._dsp_ring_spin.setValue(1)
    for x, y in [(100.0, 0.0), (0.0, 100.0), (-100.0, 0.0)]:
        view._add_dspacing_point(512.0 + x, 498.0 + y)
    assert tab._run_btn.isEnabled()

    # Ticking tz adds a free parameter — the same 3 picks are no longer
    # enough, and the button gate must reflect that live.
    tab._ref_tz.setChecked(True)
    assert tab._manual_min_picks() == 4
    assert not tab._run_btn.isEnabled()

    # A 4th pick on the same ring satisfies the new threshold.
    view._add_dspacing_point(512.0, 498.0 + 40.0)
    assert tab._run_btn.isEnabled()


def test_manual_fit_result_flows_through_on_done_with_correct_rings(app, monkeypatch):
    import midas_gui.tab_calibrate as tab_calibrate_mod
    from midas_gui.helpers import simulate_rings_from_dspacings
    monkeypatch.setattr(tab_calibrate_mod, "ManualDspacingCalibWorker",
                        _FakeManualDspacingCalibWorker)
    monkeypatch.setattr(tab_calibrate_mod, "IntegrationWorker",
                        _FakeIntegrationWorker)
    tab = tab_calibrate_mod.CalibrationTab()

    idx = tab._cal.findText("AgBH (silver behenate)")
    tab._cal.setCurrentIndex(idx)
    view = tab._img_view
    for ring_idx, r in ((1, 100.0), (2, 60.0)):
        view._dsp_ring_spin.setValue(ring_idx)
        for x, y in [(r, 0.0), (0.0, r), (-r, 0.0)]:
            view._add_dspacing_point(512.0 + x, 498.0 + y)

    assert tab._run_btn.isEnabled()
    tab._run_manual_fit()
    assert tab._worker is not None
    assert not tab._run_btn.isEnabled()   # disabled while running

    loop = QtCore.QEventLoop()
    tab.calibrationDone.connect(lambda *_: loop.quit())
    QtCore.QTimer.singleShot(2000, loop.quit)   # safety timeout
    loop.exec_()

    assert tab._result is not None
    assert tab._result._calibrant_name == "AgBH (silver behenate)"
    assert tab._save_json_btn.isEnabled()
    assert tab._save_ps_btn.isEnabled()
    assert tab._run_btn.isEnabled()   # re-enabled after the fake worker finishes

    d_list = sorted(tab._result._d_list, reverse=True)
    expected_radii = sorted({round(r["radius_px"], 3) for r in simulate_rings_from_dspacings(
        d_list, tab._result.wavelength_A, tab._result.Lsd, tab._result.pxY)})
    from midas_gui.helpers import _predict_ring_radii
    assert _predict_ring_radii(tab._result) == expected_radii


def _param_grid_labels(tab):
    """Text of every key-label QLabel currently laid out in the results grid."""
    grid = tab._param_grid
    labels = []
    for i in range(grid.count()):
        w = grid.itemAt(i).widget()
        if isinstance(w, QtWidgets.QLabel) and w.text().endswith(":"):
            labels.append(w.text())
    return labels


def test_manual_fit_marks_unrefined_geometry_params_as_fixed_in_results_grid(app, monkeypatch):
    import midas_gui.tab_calibrate as tab_calibrate_mod
    monkeypatch.setattr(tab_calibrate_mod, "ManualDspacingCalibWorker",
                        _FakeManualDspacingCalibWorker)
    monkeypatch.setattr(tab_calibrate_mod, "IntegrationWorker",
                        _FakeIntegrationWorker)
    tab = tab_calibrate_mod.CalibrationTab()

    idx = tab._cal.findText("AgBH (silver behenate)")
    tab._cal.setCurrentIndex(idx)
    # Only Lsd/BC/ty/tz refined; tx and Wavelength stay fixed. Set all six
    # explicitly — a d-spacing calibrant now defaults to BC-only, and this
    # test is about how the grid renders the flags, not what they default to.
    tab._ref_lsd.setChecked(True)
    tab._ref_bc.setChecked(True)
    tab._ref_tx.setChecked(False)
    tab._ref_wl.setChecked(False)
    tab._ref_ty.setChecked(True)
    tab._ref_tz.setChecked(True)

    view = tab._img_view
    for ring_idx, r in ((1, 100.0), (2, 60.0)):
        view._dsp_ring_spin.setValue(ring_idx)
        for x, y in [(r, 0.0), (0.0, r), (-r, 0.0)]:
            view._add_dspacing_point(512.0 + x, 498.0 + y)

    assert tab._run_btn.isEnabled()
    tab._run_manual_fit()

    loop = QtCore.QEventLoop()
    tab.calibrationDone.connect(lambda *_: loop.quit())
    QtCore.QTimer.singleShot(2000, loop.quit)
    loop.exec_()

    assert tab._result is not None
    labels = _param_grid_labels(tab)
    assert any(lbl.startswith("tx") and "(fixed)" in lbl for lbl in labels)
    assert any(lbl.startswith("Wavelength") and "(fixed)" in lbl for lbl in labels)
    assert not any(lbl.startswith("Lsd") and "(fixed)" in lbl for lbl in labels)
    assert not any(lbl.startswith("ty") and "(fixed)" in lbl for lbl in labels)
    assert not any(lbl.startswith("tz") and "(fixed)" in lbl for lbl in labels)


def test_dspacing_picks_round_trip_through_project_state(app, monkeypatch):
    """Regression test for a project/session save-load losing every
    manually-picked d-spacing point (reported as "project file does not
    save the calibration tab state accurately" — a saved-and-reloaded
    session showed "No points picked yet." even though points had been
    picked before saving, so re-running Fit could not reproduce the prior
    result or its ring overlay)."""
    import midas_gui.tab_calibrate as tab_calibrate_mod
    monkeypatch.setattr(tab_calibrate_mod, "ManualDspacingCalibWorker",
                        _FakeManualDspacingCalibWorker)
    monkeypatch.setattr(tab_calibrate_mod, "IntegrationWorker",
                        _FakeIntegrationWorker)
    tab = tab_calibrate_mod.CalibrationTab()
    idx = tab._cal.findText("AgBH (silver behenate)")
    tab._cal.setCurrentIndex(idx)

    view = tab._img_view
    view._dsp_ring_spin.setValue(1)
    for x, y in [(100.0, 0.0), (0.0, 100.0), (-100.0, 0.0)]:
        view._add_dspacing_point(512.0 + x, 498.0 + y)
    view._dsp_ring_spin.setValue(2)
    for x, y in [(50.0, 0.0), (0.0, 50.0), (-50.0, 0.0)]:
        view._add_dspacing_point(512.0 + x, 498.0 + y)
    assert tab._run_btn.isEnabled()

    state = tab.get_state()

    tab2 = tab_calibrate_mod.CalibrationTab()
    assert tab2._img_view.dspacing_picks() == []
    tab2.set_state(state)

    assert tab2._cal.currentText() == "AgBH (silver behenate)"
    picks = tab2._img_view.dspacing_picks()
    assert sorted(p[2] for p in picks) == [1, 1, 1, 2, 2, 2]
    assert tab2._run_btn.isEnabled()   # a single click reproduces the result
    assert tab2._dsp_summary.text() == tab._dsp_summary.text()


def test_manual_fit_result_persists_through_project_state_without_rerunning_fit(app, monkeypatch):
    """The project/session state must round-trip the fitted ``_result``
    itself, not just the seed picks (see the pick-only round trip above) —
    reloading a saved project/session should immediately show the fitted
    rings/parameters again, with no need to click Fit a second time."""
    import midas_gui.tab_calibrate as tab_calibrate_mod
    monkeypatch.setattr(tab_calibrate_mod, "ManualDspacingCalibWorker",
                        _FakeManualDspacingCalibWorker)
    monkeypatch.setattr(tab_calibrate_mod, "IntegrationWorker",
                        _FakeIntegrationWorker)
    tab = tab_calibrate_mod.CalibrationTab()
    idx = tab._cal.findText("AgBH (silver behenate)")
    tab._cal.setCurrentIndex(idx)

    view = tab._img_view
    for ring_idx, r in ((1, 100.0), (2, 60.0)):
        view._dsp_ring_spin.setValue(ring_idx)
        for x, y in [(r, 0.0), (0.0, r), (-r, 0.0)]:
            view._add_dspacing_point(512.0 + x, 498.0 + y)

    tab._run_manual_fit()
    loop = QtCore.QEventLoop()
    tab.calibrationDone.connect(lambda *_: loop.quit())
    QtCore.QTimer.singleShot(2000, loop.quit)
    loop.exec_()
    assert tab._result is not None

    state = tab.get_state()

    tab2 = tab_calibrate_mod.CalibrationTab()
    assert tab2._result is None
    tab2.set_state(state)

    assert tab2._result is not None
    assert tab2._result.Lsd == pytest.approx(tab._result.Lsd)
    assert tab2._result.BC_y == pytest.approx(tab._result.BC_y)
    assert tab2._result._calibrant_name == "AgBH (silver behenate)"
    assert tab2._result._d_list == tab._result._d_list
    assert tab2._save_json_btn.isEnabled()
    assert tab2._save_ps_btn.isEnabled()


# ── BC-only default + parameter limits (manual fit conditioning) ────────
#
# See tests/test_manual_fit_conditioning.py for the numerics these UI
# defaults exist to serve: on a d-spacing calibrant the picks are often a
# single short ring arc, where floating Lsd alongside BC is badly
# conditioned and tilt is not identifiable at all.


def _set_limit(tab, name, value, unit):
    """Tick one inline limit row and set its window."""
    cb, spin, combo = tab._limit_widgets[name]
    cb.setChecked(True); spin.setValue(value); combo.setCurrentText(unit)


def _flags(tab):
    return {k: tab._refine_box(k).isChecked() for k in tab._REFINE_BOXES}


def test_dspacing_calibrant_defaults_to_bc_only(app):
    import midas_gui.tab_calibrate as tab_calibrate_mod
    tab = tab_calibrate_mod.CalibrationTab()

    tab._cal.setCurrentIndex(tab._cal.findText("CeO2"))
    assert _flags(tab) == {"Lsd": True, "BC": True, "tx": False,
                           "ty": True, "tz": True, "Wavelength": False}

    tab._cal.setCurrentIndex(tab._cal.findText("AgBH (silver behenate)"))
    assert _flags(tab) == {"Lsd": False, "BC": True, "tx": False,
                           "ty": False, "tz": False, "Wavelength": False}
    summary = tab._refine_summary_lbl.text()
    assert "Refining: BC" in summary
    assert "Lsd" in summary.split("Fixed:")[1]
    # BC alone is 2 free parameters, so the 3-pick floor still governs.
    assert tab._manual_min_picks() == 3


def test_each_calibrant_kind_keeps_its_own_refine_flags(app):
    """A choice made on one kind of calibrant must survive a round trip
    through the other — otherwise switching to check something would
    silently reset the user's refinement scope."""
    import midas_gui.tab_calibrate as tab_calibrate_mod
    tab = tab_calibrate_mod.CalibrationTab()
    agbh = tab._cal.findText("AgBH (silver behenate)")
    ceo2 = tab._cal.findText("CeO2")

    tab._cal.setCurrentIndex(agbh)
    tab._ref_lsd.setChecked(True)                 # user opts Lsd back in
    tab._cal.setCurrentIndex(ceo2)
    assert _flags(tab)["ty"] is True              # crystalline set restored
    tab._ref_ty.setChecked(False)                 # and edited
    tab._cal.setCurrentIndex(agbh)
    assert _flags(tab)["Lsd"] is True             # d-spacing choice remembered
    assert _flags(tab)["ty"] is False
    tab._cal.setCurrentIndex(ceo2)
    assert _flags(tab)["ty"] is False             # crystalline edit remembered


def test_limits_button_is_manual_fit_only(app):
    """The crystalline backend takes no bounds kwargs, so the control is
    hidden for it — and the distortion row, which the manual fit cannot do,
    is hidden the other way round."""
    import midas_gui.tab_calibrate as tab_calibrate_mod
    tab = tab_calibrate_mod.CalibrationTab()

    tab._cal.setCurrentIndex(tab._cal.findText("CeO2"))
    assert all(w.isHidden() for w in tab._limit_cells)
    assert not tab._dist_row.isHidden()

    tab._cal.setCurrentIndex(tab._cal.findText("AgBH (silver behenate)"))
    assert not any(w.isHidden() for w in tab._limit_cells)
    assert tab._dist_row.isHidden()


def test_limit_bounds_conversion_and_zero_value_guard(app):
    import midas_gui.tab_calibrate as tab_calibrate_mod
    tab = tab_calibrate_mod.CalibrationTab()
    tab._manual_seed_check.setChecked(True)
    tab._seed_lsd.setValue(13500.0)     # mm in the UI, µm in the fit
    tab._seed_bcy.setValue(129.0)
    tab._seed_ty.setValue(0.0)

    assert tab._limit_bounds() == (None, [])            # nothing enabled
    assert "No limits set" in tab._limits_note.text()

    _set_limit(tab, "Lsd", 5.0, "%")
    _set_limit(tab, "BC_y", 50.0, "px")
    _set_limit(tab, "ty", 5.0, "%")                     # % of a 0 deg seed
    assert tab._n_limits_set() == 3

    b, skipped = tab._limit_bounds()
    assert skipped == []
    assert set(b) == {"Lsd", "BC_y", "ty"}              # 'off' rows excluded
    assert b["Lsd"] == pytest.approx((13500e3 * 0.95, 13500e3 * 1.05))
    assert b["BC_y"] == pytest.approx((79.0, 179.0))
    # A percentage of zero would pin the parameter exactly; it must not.
    assert b["ty"][0] < 0.0 < b["ty"][1]


def test_seed_relative_limits_are_dropped_when_the_manual_seed_is_off(app):
    """Without 'Use manual seed' the fit auto-seeds Lsd/BC from the picks, so
    a window centred on the (unused) seed spin boxes would bound the fit
    around a value it never starts from. Those rows are dropped and named;
    the wavelength, which is always live, is kept."""
    import midas_gui.tab_calibrate as tab_calibrate_mod
    tab = tab_calibrate_mod.CalibrationTab()
    tab._manual_seed_check.setChecked(False)
    tab._wl.setValue(0.173)
    _set_limit(tab, "Lsd", 5.0, "%")
    _set_limit(tab, "BC_y", 50.0, "px")
    _set_limit(tab, "wavelength_A", 1.0, "%")

    b, skipped = tab._limit_bounds()
    assert sorted(skipped) == ["BC_y", "Lsd"]
    assert set(b) == {"wavelength_A"}
    assert b["wavelength_A"] == pytest.approx((0.173 * 0.99, 0.173 * 1.01))

    # With the seed enabled they all come back.
    tab._manual_seed_check.setChecked(True)
    b, skipped = tab._limit_bounds()
    assert skipped == []
    assert set(b) == {"Lsd", "BC_y", "wavelength_A"}


def test_limits_reach_the_worker_and_survive_a_project_round_trip(app, monkeypatch):
    import midas_gui.tab_calibrate as tab_calibrate_mod
    monkeypatch.setattr(tab_calibrate_mod, "ManualDspacingCalibWorker",
                        _FakeManualDspacingCalibWorker)
    monkeypatch.setattr(tab_calibrate_mod, "IntegrationWorker",
                        _FakeIntegrationWorker)
    tab = tab_calibrate_mod.CalibrationTab()
    tab._cal.setCurrentIndex(tab._cal.findText("AgBH (silver behenate)"))
    tab._manual_seed_check.setChecked(True)
    tab._seed_lsd.setValue(13500.0)
    _set_limit(tab, "Lsd", 5.0, "%")

    view = tab._img_view
    view._dsp_ring_spin.setValue(1)
    for x, y in [(100.0, 0.0), (0.0, 100.0), (-100.0, 0.0)]:
        view._add_dspacing_point(512.0 + x, 498.0 + y)
    assert tab._run_btn.isEnabled()
    tab._run_manual_fit()

    assert tab._worker.bounds is not None
    assert tab._worker.bounds["Lsd"] == pytest.approx((13500e3 * 0.95, 13500e3 * 1.05))
    assert tab._worker.refine["Lsd"] is False        # BC-only default still in force

    state = tab.get_state()
    tab2 = tab_calibrate_mod.CalibrationTab()
    tab2.set_state(state)
    assert tab2._limits["Lsd"] == {"on": True, "value": 5.0, "unit": "%"}
    assert tab2._n_limits_set() == 1
    assert _flags(tab2) == _flags(tab)
    assert tab2._cal.currentText() == "AgBH (silver behenate)"


def test_param_grid_annotates_refined_rows_with_their_uncertainty(app):
    """A converged fit is not the same as a determined one, so the results
    grid carries the 1-sigma next to each refined geometry value."""
    import midas_gui.tab_calibrate as tab_calibrate_mod
    from midas_gui.helpers import paramstest_pairs
    tab = tab_calibrate_mod.CalibrationTab()

    result = SimpleNamespace(
        Lsd=13500000.0, BC_y=129.0, BC_z=124.0, tx=0.0, ty=0.0, tz=0.0,
        distortion={}, pxY=55.0, pxZ=55.0, NrPixelsY=3072, NrPixelsZ=512,
        wavelength_A=0.173, _calibrant_name="AgBH (silver behenate)",
        _d_list=[58.38], im_trans=[])
    refine = {"Lsd": False, "BC": True, "tx": False, "ty": False, "tz": False,
              "Wavelength": False}
    sigma = {"Lsd": 0.0, "BC_y": 0.264, "BC_z": 0.868,
             "tx": 0.0, "ty": 0.0, "tz": 0.0, "wavelength_A": 0.0}
    tab._populate_param_grid(paramstest_pairs(result, selected=set()),
                             refine_flags=refine, sigma=sigma, at_limit=set())

    grid = tab._param_grid
    texts = [grid.itemAt(i).widget().text() for i in range(grid.count())
             if isinstance(grid.itemAt(i).widget(), QtWidgets.QLabel)]
    labels = [t for t in texts if t.endswith(":")]
    values = [t for t in texts if not t.endswith(":")]
    assert any(l.startswith("Lsd") and "(fixed)" in l for l in labels)
    assert any("± 0.264, 0.868" in v for v in values)     # the refined BC row
    # A held-fixed row carries no uncertainty.
    assert not any(v.startswith("13500000") and "±" in v for v in values)


def test_param_grid_marks_a_bound_pinned_row_instead_of_a_sigma(app):
    import midas_gui.tab_calibrate as tab_calibrate_mod
    from midas_gui.helpers import paramstest_pairs
    tab = tab_calibrate_mod.CalibrationTab()
    result = SimpleNamespace(
        Lsd=13635000.0, BC_y=129.0, BC_z=124.0, tx=0.0, ty=0.0, tz=0.0,
        distortion={}, pxY=55.0, pxZ=55.0, NrPixelsY=3072, NrPixelsZ=512,
        wavelength_A=0.173, _calibrant_name="AgBH (silver behenate)",
        _d_list=[58.38], im_trans=[])
    refine = {"Lsd": True, "BC": True, "tx": False, "ty": False, "tz": False,
              "Wavelength": False}
    tab._populate_param_grid(
        paramstest_pairs(result, selected=set()), refine_flags=refine,
        sigma={"Lsd": 0.0, "BC_y": 0.3, "BC_z": 0.4, "tx": 0.0, "ty": 0.0,
               "tz": 0.0, "wavelength_A": 0.0},
        at_limit={"Lsd"})
    values = [tab._param_grid.itemAt(i).widget().text()
              for i in range(tab._param_grid.count())
              if isinstance(tab._param_grid.itemAt(i).widget(), QtWidgets.QLabel)]
    assert any("(at limit)" in v for v in values)


def _ring_extents(tab):
    """(y-extent, z-extent) of each drawn ring curve — a plain circle has equal
    extents, a tilt-distorted one does not."""
    import numpy as np
    out = []
    for it in tab._seed_ring_items:
        d = it.getData() if hasattr(it, "getData") else None
        if d and d[0] is not None and len(d[0]) > 10:
            out.append((float(np.ptp(d[0])), float(np.ptp(d[1]))))
    return out


def test_ring_overlay_is_driven_by_the_seed_card(app):
    """The overlay must come from the geometry shown in the seed card, so a
    stale or badly-converged result can never paint rings that disagree with
    the numbers on screen (the AgBH runaway-tilt bug)."""
    import midas_gui.tab_calibrate as tab_calibrate_mod
    tab = tab_calibrate_mod.CalibrationTab()
    tab._cal.setCurrentIndex(tab._cal.findText("AgBH (silver behenate)"))
    tab._wl.setValue(0.1730); tab._pxY.setValue(55.0)
    tab._manual_seed_check.setChecked(True)
    tab._seed_lsd.setValue(13500.0)
    tab._seed_bcy.setValue(129.0); tab._seed_bcz.setValue(124.0)
    tab._show_rings_check.setChecked(True)
    tab._corrected_check.setChecked(True)
    tab._feedback_check.setChecked(True)

    untilted = _ring_extents(tab)
    assert untilted, "seed geometry should draw rings before any fit"
    # Circles, to within the 512-point sampling of the curve.
    for y_ext, z_ext in untilted:
        assert y_ext == pytest.approx(z_ext, rel=1e-3)

    # A fit that ran away in tilt, fed back into the seed.
    bad = SimpleNamespace(
        Lsd=13500e3, BC_y=129.0, BC_z=124.0, tx=0.0, ty=-82.0, tz=0.0,
        distortion={}, pxY=55.0, pxZ=55.0, NrPixelsY=3072, NrPixelsZ=512,
        wavelength_A=0.1730, _calibrant_name="AgBH (silver behenate)",
        _d_list=sorted([58.380 / n for n in range(1, 11)], reverse=True),
        im_trans=[])
    tab._result = bad
    tab._seed_from_result(bad)

    # The runaway tilt is now visible in the seed card, not hidden in a result.
    assert tab._seed_ty.value() == pytest.approx(-82.0)
    assert "ty=-82" in tab._seed_note.text()
    distorted = _ring_extents(tab)
    assert distorted[0][1] > 3 * distorted[0][0]            # stretched, not circular

    # ...and editing the seed corrects the overlay immediately.
    tab._seed_ty.setValue(0.0)
    for y_ext, z_ext in _ring_extents(tab):
        assert y_ext == pytest.approx(z_ext, rel=1e-3)


def test_ring_labels_land_on_the_visible_arc(app):
    """The old anchor was the ring's twelve o'clock point, which is off-frame
    for every ring when the beam centre sits near the edge of a wide, short
    strip — so every label vanished."""
    import numpy as np
    from midas_gui.hydra_geometry_card import _ring_label_pos
    from midas_gui.helpers import simulate_rings_from_dspacings

    bc_y, bc_z, shape = 129.0, 124.0, (512, 3072)
    th = np.linspace(0, 2 * np.pi, 400)
    rings = simulate_rings_from_dspacings([58.380 / n for n in range(1, 11)],
                                          0.1730, 13.5e6, 55.0)
    on_image = 0
    for r in rings[:4]:
        rad = r["radius_px"]
        ys, zs = bc_y + rad * np.cos(th), bc_z + rad * np.sin(th)
        assert bc_z - rad < 0                       # old anchor: below the frame
        ly, lz, _anchor = _ring_label_pos(ys, zs, shape)
        if 0 <= ly < shape[1] and 0 <= lz < shape[0]:
            on_image += 1
    assert on_image == 4

    # A ring entirely off the frame gets no label at all: a label out in the
    # empty canvas beside the detector describes nothing the user can see.
    ys, zs = bc_y + 9000 * np.cos(th), bc_z + 9000 * np.sin(th)
    assert _ring_label_pos(ys, zs, shape) is None


def test_seed_can_be_accepted_as_the_calibration_without_a_fit(app, monkeypatch):
    """A hand-matched seed is a usable calibration.

    Every export is gated on a result, and only a fit produced one — so a
    geometry dialled in until the simulated rings sat on the measured ones
    could not be sent anywhere. Accepting the seed publishes it as-is, and
    says so: nothing is refined, so every geometry row reads "(fixed)" and
    the Fit button stays gated on picks it still does not have.
    """
    import midas_gui.tab_calibrate as tab_calibrate_mod
    monkeypatch.setattr(tab_calibrate_mod, "IntegrationWorker",
                        _FakeIntegrationWorker)
    tab = tab_calibrate_mod.CalibrationTab()
    tab._cal.setCurrentIndex(tab._cal.findText("AgBH (silver behenate)"))
    tab._wl.setValue(0.17220)
    tab._pxY.setValue(55.0)
    tab._seed_lsd.setValue(13500.0)          # mm in the UI, µm in the result
    tab._seed_bcy.setValue(129.0)
    tab._seed_bcz.setValue(124.0)

    assert not tab._run_btn.isEnabled()      # no picks
    assert not tab._save_json_btn.isEnabled()
    assert not tab._save_ps_btn.isEnabled()
    assert not tab._to_view_btn.isEnabled()

    tab._accept_seed_btn.click()

    assert tab._result is not None
    assert tab._result.Lsd == pytest.approx(13500.0 * 1000.0)
    assert tab._result.BC_y == pytest.approx(129.0)
    assert tab._result.BC_z == pytest.approx(124.0)
    assert tab._result.wavelength_A == pytest.approx(0.17220)
    assert tab._result.fit_sigma is None     # nothing was measured
    assert tab._save_json_btn.isEnabled()
    assert tab._save_ps_btn.isEnabled()
    assert tab._to_view_btn.isEnabled()
    # Still no picks, so the fit itself stays unavailable.
    assert not tab._run_btn.isEnabled()

    labels = _param_grid_labels(tab)
    for key in ("Lsd", "BC", "tx", "ty", "tz", "Wavelength"):
        assert any(lbl.startswith(key) and "(fixed)" in lbl for lbl in labels), key
    assert "no fit was run" in tab._log.toPlainText()
