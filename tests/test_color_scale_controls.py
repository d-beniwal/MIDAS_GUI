"""Color limits are raw intensities, even when pixels and LUT use log10."""
import numpy as np
import pytest

pytestmark = pytest.mark.forked


@pytest.fixture(scope="module")
def app():
    from PyQt5 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(params=["ImageViewer", "CakeViewer", "WaterfallViewer"])
def view(app, request):
    from midas_gui import widgets
    viewer = getattr(widgets, request.param)()
    yield viewer
    viewer.close()
    viewer.deleteLater()
    app.processEvents()


def fill(view, data):
    from midas_gui.widgets import ImageViewer, CakeViewer
    if isinstance(view, ImageViewer):
        view.set_image(data, reset_levels=False)
    elif isinstance(view, CakeViewer):
        view.set_cake(data, np.arange(data.shape[1]), np.arange(data.shape[0]))
    else:
        view._buf = np.asarray(data)
        view._nrows = data.shape[0]
        view._r_axis = np.arange(data.shape[1])
        view._redraw()


def edit(scale, lo, hi):
    scale.minimum.setText(str(lo))
    scale.maximum.setText(str(hi))
    scale.maximum.editingFinished.emit()


def test_defaults_are_exact_image_extrema_and_follow_frames(view):
    fill(view, np.array([[0., 10.], [100., np.nan]]))
    scale = view._color_scale
    assert float(scale.minimum.text()) == 0
    assert float(scale.maximum.text()) == 100
    assert scale.manual_limits is None
    fill(view, np.array([[-5., 20.], [1000., np.inf]]))
    assert scale.limits == (-5., 1000.)
    assert scale.manual_limits is None


def test_typed_limits_survive_new_frames_log_switches_and_zoom(view, app):
    fill(view, np.arange(1., 13.).reshape(3, 4))
    scale = view._color_scale
    edit(scale, 2, '1e4')
    for log in (False, True, False, True):
        view._log.setChecked(log)
        fill(view, np.arange(1., 13.).reshape(3, 4) * 100)
        app.processEvents()
        assert float(scale.minimum.text()) == 2
        assert float(scale.maximum.text()) == 10000
        expected = np.log10((2., 10000.)) if log else (2., 10000.)
        np.testing.assert_allclose(scale.histogram.getLevels(), expected)
        vb = scale.histogram.item.vb
        lo, hi = expected
        vb.setYRange(lo - 10000, hi + 10000, padding=0)
        app.processEvents()
        actual = vb.viewRange()[1]
        assert actual[0] >= lo - 1e-8 and actual[1] <= hi + 1e-8
        vb.setYRange(lo + (hi-lo)*.2, lo + (hi-lo)*.6, padding=0)
        zoomed = vb.viewRange()[1]
        fill(view, np.ones((3, 4)) * 9)
        np.testing.assert_allclose(vb.viewRange()[1], zoomed)
    scale.auto_button.click()
    assert scale.manual_limits is None
    assert scale.limits == (9., 9.)


def test_zero_and_negative_limits_survive_a_log_round_trip(view):
    fill(view, np.array([[0., 10.], [-20., 100.]]))
    for lo in (0., -10.):
        view._log.setChecked(False)
        edit(view._color_scale, lo, 100)
        view._log.setChecked(True)
        assert float(view._color_scale.minimum.text()) == lo
        assert np.isfinite(view._color_scale.histogram.getLevels()).all()
        view._log.setChecked(False)
        assert view._color_scale.limits == (lo, 100.)


@pytest.mark.parametrize("lo,hi", [(10, 2), ('nan', 10), (0, 'inf'), ('abc', 1)])
def test_invalid_edits_do_not_change_the_scale(view, lo, hi):
    fill(view, np.array([[0., 10.], [20., 100.]]))
    edit(view._color_scale, 2, 50)
    edit(view._color_scale, lo, hi)
    assert view._color_scale.manual_limits == (2., 50.)


def test_histogram_drag_updates_absolute_fields_in_log_mode(view):
    fill(view, np.array([[1., 10.], [100., 1000.]]))
    view._log.setChecked(True)
    view._color_scale.histogram.setLevels(1., 2.)
    assert view._color_scale.limits == (10., 100.)
    assert float(view._color_scale.minimum.text()) == 10
    assert float(view._color_scale.maximum.text()) == 100


def test_display_state_stores_absolute_limits(view):
    fill(view, np.array([[1., 10.], [100., 1000.]]))
    edit(view._color_scale, 2, 400)
    state = view.display_state()
    view._color_scale.reset()
    view.set_display_state(state)
    assert view._color_scale.manual_limits == (2., 400.)
    view.set_display_state({"log": False})
    assert view._color_scale.manual_limits == (2., 400.)
    np.testing.assert_allclose(view._color_scale.histogram.getLevels(), (2., 400.))


def test_auto_releases_a_legacy_percentile_style(view):
    fill(view, np.arange(12.).reshape(3, 4))
    view.set_display_state({"vmin": 30, "vmax": 99})
    view._color_scale.auto_button.click()
    assert view._color_scale.limits == (0., 11.)
    state = view.display_state()
    assert state.get("vmin", 0) == 0
    assert state.get("vmax", 100) == 100


def test_constant_nonfinite_and_negative_only_images_are_navigable(view):
    for data in (np.zeros((2, 2)), np.full((2, 2), -7.), np.full((2, 2), np.nan)):
        fill(view, data)
        for log in (False, True):
            view._log.setChecked(log)
            lo, hi = view._color_scale.histogram.getLevels()
            assert np.isfinite((lo, hi)).all() and hi > lo


def test_max_above_min_below_histogram(view, app):
    view.resize(700, 500)
    view.show()
    fill(view, np.arange(12.).reshape(3, 4))
    app.processEvents()
    scale = view._color_scale
    assert scale.maximum.parent() == scale
    assert scale.maximum.geometry().bottom() < scale.histogram.geometry().top()
    assert scale.minimum.geometry().top() > scale.histogram.geometry().bottom()


def test_cake_stack_preserves_typed_limits_on_scrub(app):
    from midas_gui.widgets import CakeStackViewer
    view = CakeStackViewer()
    view.set_cakes(np.arange(24.).reshape(2, 3, 4), np.arange(4), np.arange(3))
    edit(view._color_scale, 1, 100)
    view._frame_slider.setValue(1)
    assert view._color_scale.limits == (1., 100.)
    view.deleteLater()


def test_restored_image_limits_survive_the_first_image(app):
    from midas_gui.widgets import ImageViewer
    view = ImageViewer()
    view.set_display_state({"color_limits": [2., 400.], "log": True})
    view.set_image(np.arange(12.).reshape(3, 4))
    assert view._color_scale.limits == (2., 400.)
    view.deleteLater()


def test_residual_map_uses_data_extrema_and_holds_edits(app):
    from midas_gui.widgets import RingResidualViewer
    view = RingResidualViewer()
    view._mode.setCurrentIndex(view._mode.findData("pixel"))
    # Use the already converted map, so this check is independent of the
    # scientific residual-to-strain conversion.
    view._display_cake = lambda cake, x: cake
    data = np.arange(12.).reshape(3, 4) - 3
    view._active = lambda: (data, np.arange(4.), "R", "px")
    view._eta_axis = np.linspace(-180, 180, 3)
    view._redisplay_2d()
    assert view._color_scale.limits == (-3., 8.)
    edit(view._color_scale, -1, 2)
    view._redisplay_2d()
    assert view._color_scale.limits == (-1., 2.)
    view.deleteLater()


def test_pump_probe_color_scale_is_editable_and_persistent(app):
    from midas_gui.tab_pumpprobe import PumpProbeTab
    view = PumpProbeTab()
    view._axis.setCurrentIndex(view._axis.findData("R"))
    view._data = dict(delays=np.array([-1., 1.]), r_axis_px=np.arange(4.) + 1,
                      dI=np.arange(8.).reshape(2, 4) - 3,
                      reference=np.arange(4.) + 1)
    view._replot_heatmap()
    assert view._hm_scale.limits == (-3., 4.)
    edit(view._hm_scale, -1, 2)
    view._replot_heatmap()
    assert view._hm_scale.limits == (-1., 2.)
    assert view.get_state()["heatmap_color"]["color_limits"] == (-1., 2.)
    view.deleteLater()
