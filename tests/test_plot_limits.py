import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pyqtgraph as pg
from PyQt5 import QtWidgets

from midas_gui.plot_limits import limit_to_data

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_limits_are_data_extent_plus_one_percent_and_ignore_annotations():
    w = pg.GraphicsLayoutWidget()
    p = w.addPlot()
    p.addItem(pg.ScatterPlotItem(x=[0, 10], y=[5, 15]))
    p.addItem(pg.InfiniteLine(100))
    assert limit_to_data(p)
    lim = p.getViewBox().state["limits"]
    assert lim["xLimits"] == [-0.1, 10.1]
    assert lim["yLimits"] == [4.9, 15.1]


def test_empty_plot_untouched():
    w = pg.GraphicsLayoutWidget()
    p = w.addPlot()
    assert not limit_to_data(p)


def test_aspect_fit_fills_tight_axis_and_keeps_ratio():
    from midas_gui.plot_limits import AspectFit
    w = pg.GraphicsLayoutWidget()
    w.resize(600, 400)
    p = w.addPlot()
    p.setAspectLocked(True)
    w.show()
    _app.processEvents()
    vb = p.getViewBox()
    AspectFit(vb, pad=0.0).set_bounds(0, 100, 0, 100)   # square in a wide widget
    _app.processEvents()
    (x0, x1), (y0, y1) = vb.viewRange()
    assert abs((y1 - y0) - 100) < 1e-6                  # tight axis fits exactly
    assert (x1 - x0) > 100                              # spare room on the other
    assert abs((x1 - x0) / (y1 - y0) - vb.width() / vb.height()) < 1e-6
    lim = vb.state["limits"]
    assert abs(lim["yLimits"][1] - lim["yLimits"][0] - 100) < 1e-6


def test_image_viewer_fits_frame_and_follows_lab_rotation():
    import numpy as np
    from midas_gui.widgets import ImageViewer
    v = ImageViewer()
    v.resize(500, 500)
    v.show()
    _app.processEvents()
    v.set_image(np.random.rand(200, 400))              # 400 wide, 200 tall
    _app.processEvents()
    vb = v._iv.getView().getViewBox()
    (x0, x1), (y0, y1) = vb.viewRange()
    assert x0 <= 0 and x1 >= 400 and (x1 - x0) < 400 * 1.05
    assert (y1 - y0) > 200                             # spare room on the short axis
    v.set_lab_rotation(90.0, 200.0, 100.0)
    _app.processEvents()
    lim = vb.state["limits"]
    assert lim["xLimits"][1] - lim["xLimits"][0] > 0
