"""Manual axis limits must survive live-acquisition redraws.

Regression: typing limits into an axis's native right-click "Manual" min/max
fields while the "A" (Auto) button was lit never engaged manual mode, so every
new live frame re-fitted the plot and discarded them. Covers both plots with
the A/M button pair (Data Viewer Statistics histogram, radial ProfileViewer).

Qt is imported lazily (see tests/test_set_raw_frame.py): forked + module-level
PyQt5 import crashes on macOS.
"""
import numpy as np
import pytest

pytestmark = pytest.mark.forked


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _type_limit(plot, axis, lo, hi):
    """What the user does: type min/max into the axis menu and press Enter."""
    ctrl = plot.getViewBox().menu.ctrl[axis]
    ctrl.minText.setText(repr(lo))
    ctrl.maxText.setText(repr(hi))
    ctrl.minText.editingFinished.emit()


def _view(plot):
    (x0, x1), (y0, y1) = plot.getViewBox().viewRange()
    return x0, x1, y0, y1


def _stats_frame(seed, scale):
    rng = np.random.default_rng(seed)
    return rng.gamma(2.0, scale, 20000)


@pytest.fixture
def stats(app):
    from midas_gui.widgets import IntensityStatsPanel
    sp = IntensityStatsPanel()
    sp.set_data(_stats_frame(0, 100.0))
    return sp


@pytest.fixture
def profile(app):
    from midas_gui.widgets import ProfileViewer
    pv = ProfileViewer()
    r = np.arange(0.0, 500.0)
    pv.set_profile(r, 100 + 50 * np.sin(r / 20))
    return pv


def test_stats_typed_limits_hold_across_live_frames(stats):
    assert not stats._manual_mode                    # starts in Auto
    _type_limit(stats._plot, 0, 10.0, 500.0)
    _type_limit(stats._plot, 1, 0.0, 2.5)
    assert stats._manual_mode and stats._btn_manual.isChecked()
    for i in range(1, 6):                            # live: different data each frame
        stats.set_data(_stats_frame(i, 100.0 * i))
        assert _view(stats._plot) == pytest.approx((10.0, 500.0, 0.0, 2.5))


def test_stats_editing_one_axis_keeps_the_other_exactly(stats):
    _type_limit(stats._plot, 0, 1.2345678, 400.123456)
    _type_limit(stats._plot, 1, 0.0, 3.0)
    # Re-editing X must not round Y through the menu's "%0.5g" text.
    _type_limit(stats._plot, 0, 5.0, 250.0)
    assert stats._manual_range == (5.0, 250.0, 0.0, 3.0)


def test_stats_log_toggle_keeps_manual_limits(stats):
    _type_limit(stats._plot, 0, 10.0, 500.0)
    _type_limit(stats._plot, 1, 0.0, 2.5)
    stats._logchk.setChecked(False)
    assert _view(stats._plot) == pytest.approx((10.0, 500.0, 0.0, 2.5))


def test_stats_auto_button_releases_manual(stats):
    _type_limit(stats._plot, 0, 10.0, 500.0)
    _type_limit(stats._plot, 1, 0.0, 2.5)
    stats._btn_auto.click()
    assert not stats._manual_mode
    stats.set_data(_stats_frame(9, 1000.0))
    assert _view(stats._plot)[1] > 600.0             # re-fitted to the new data


def test_stats_manual_button_reclick_snaps_back(stats):
    _type_limit(stats._plot, 0, 10.0, 500.0)
    _type_limit(stats._plot, 1, 0.0, 2.5)
    stats._plot.getViewBox().setXRange(100, 200, padding=0)   # user zoom
    stats._btn_manual.click()
    assert _view(stats._plot) == pytest.approx((10.0, 500.0, 0.0, 2.5))


def test_profile_typed_limits_hold_across_live_frames(profile):
    assert not profile._manual_mode
    _type_limit(profile._plot, 0, 50.0, 300.0)
    _type_limit(profile._plot, 1, 20.0, 180.0)
    assert profile._manual_mode and profile._btn_manual.isChecked()
    r = np.arange(0.0, 500.0)
    for i in range(1, 6):
        profile.set_profile(r, 100 * i + 50 * i * np.sin(r / (10 + i)))
        assert _view(profile._plot) == pytest.approx((50.0, 300.0, 20.0, 180.0))


def test_profile_editing_one_axis_keeps_the_other_exactly(profile):
    _type_limit(profile._plot, 0, 1.2345678, 400.123456)
    _type_limit(profile._plot, 1, 0.0, 3.0)
    _type_limit(profile._plot, 0, 5.0, 250.0)
    assert profile._manual_range == (5.0, 250.0, 0.0, 3.0)


def test_profile_auto_button_releases_manual(profile):
    _type_limit(profile._plot, 0, 50.0, 300.0)
    _type_limit(profile._plot, 1, 20.0, 180.0)
    profile._btn_auto.click()
    assert not profile._manual_mode
    r = np.arange(0.0, 500.0)
    profile.set_profile(r, 5000 + r)
    assert _view(profile._plot)[3] > 1000.0
