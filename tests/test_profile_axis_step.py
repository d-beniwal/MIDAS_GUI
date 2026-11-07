"""Waterfall / stacked-profile x-axis never resolves finer than the data's bin width."""
import numpy as np
import pytest

pytestmark = pytest.mark.forked


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _viewers(app):
    from midas_gui.widgets import StackedProfileViewer, WaterfallViewer
    q = np.arange(0.5, 8, 0.01)
    s = StackedProfileViewer()
    s.set_axis_context(1e6, 200, 0.17, native_unit="Q", display_unit="Q")
    s.add_profile(q, np.random.rand(q.size), "a")
    w = WaterfallViewer()
    w.set_axis_context(1e6, 200, 0.17, native_unit="Q", display_unit="Q")
    w.reset(q)
    w.add_profile(np.random.rand(q.size))
    w._redraw()
    return [s, w]


def test_ticks_not_finer_than_step(app):
    for v in _viewers(app):
        for sp, vals in v._xaxis.tickValues(2.16, 2.18, 600):
            assert sp >= 0.01 * (1 - 1e-6)
            assert np.allclose(np.asarray(vals) / 0.01, np.round(np.asarray(vals) / 0.01), atol=1e-6)


def test_zoom_floor_is_a_few_bins(app):
    for v in _viewers(app):
        lim = v._plot.getPlotItem().getViewBox().state["limits"]["xRange"][0]
        assert lim >= 3 * 0.01 * (1 - 1e-6)
