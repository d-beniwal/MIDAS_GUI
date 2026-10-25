"""Bound a pyqtgraph plot's pan/zoom to its data extent (+ a small margin)."""
from __future__ import annotations

import math

import numpy as np
import pyqtgraph as pg

# Annotation items: they must not widen the allowed range.
_SKIP = (pg.InfiniteLine, pg.LinearRegionItem, pg.TextItem, pg.LegendItem)


def data_bounds(plot):
    """``(xmin, xmax, ymin, ymax)`` over the plot's data items, or ``None``."""
    vb = plot.getViewBox()
    lo = [math.inf, math.inf]
    hi = [-math.inf, -math.inf]
    for it in plot.items:
        if isinstance(it, _SKIP):
            continue
        if hasattr(it, "dataBounds") and not isinstance(it, pg.ImageItem):
            rng = [it.dataBounds(0), it.dataBounds(1)]
        else:
            r = vb.mapFromItemToView(it, it.boundingRect()).boundingRect()
            rng = [(r.left(), r.right()), (r.top(), r.bottom())]
        for ax, b in enumerate(rng):
            if b is None or b[0] is None or b[1] is None:
                continue
            if np.isfinite(b[0]) and np.isfinite(b[1]):
                lo[ax] = min(lo[ax], b[0])
                hi[ax] = max(hi[ax], b[1])
    if not all(math.isfinite(v) for v in (*lo, *hi)):
        return None
    return lo[0], hi[0], lo[1], hi[1]


def limit_to_data(plot, pad: float = 0.01, set_range: bool = True) -> bool:
    """Restrict pan/zoom of ``plot`` to its data bounds ± ``pad`` of the span.

    With ``set_range`` the current view is also reset to those bounds.
    Returns False (and leaves the plot alone) when there is no finite data.
    """
    b = data_bounds(plot)
    if b is None:
        return False
    xmin, xmax, ymin, ymax = b
    xs = (xmax - xmin) or 1.0
    ys = (ymax - ymin) or 1.0
    xmin, xmax = xmin - pad * xs, xmax + pad * xs
    ymin, ymax = ymin - pad * ys, ymax + pad * ys
    vb = plot.getViewBox()
    vb.setLimits(xMin=xmin, xMax=xmax, yMin=ymin, yMax=ymax,
                 maxXRange=xmax - xmin, maxYRange=ymax - ymin)
    if set_range:
        vb.setRange(xRange=(xmin, xmax), yRange=(ymin, ymax), padding=0)
    return True


def limit_all(plots, **kw):
    for p in (plots.values() if hasattr(plots, "values") else plots):
        limit_to_data(p, **kw)


FRAME_OUTLINE_COLOR = "#ffd400"


def frame_outline(xlo, xhi, ylo, yhi, color=FRAME_OUTLINE_COLOR, item=None):
    """Yellow rectangle just outside ``[xlo,xhi]x[ylo,yhi]`` (the detector image edge).

    Pass the previous ``item`` to update it in place; returns the item.
    """
    e = 0.002 * max(xhi - xlo, yhi - ylo)
    x0, x1, y0, y1 = xlo - e, xhi + e, ylo - e, yhi + e
    xs, ys = [x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0]
    if item is None:
        item = pg.PlotCurveItem(pen=pg.mkPen(color, width=2, cosmetic=True))
        item.setZValue(50)
    item.setData(xs, ys)
    return item


class AspectFit:
    """Pan/zoom limits that fit a rectangle exactly along one axis.

    The view can never zoom out past "rectangle (+1% margin) fits the widget
    with the aspect ratio preserved" -- it fills the tighter dimension and
    leaves spare room along the other. Zooming in and panning stay inside
    that fitted window. Re-fits when the widget is resized.
    """

    def __init__(self, vb, pad: float = 0.01):
        self.vb = vb
        self.pad = pad
        self.bounds = None
        self._fit = None                      # (fx, fy) of the last full fit
        vb._aspect_fit = self                 # keep alive with the view
        vb.sigResized.connect(self._on_resized)

    def set_bounds(self, xlo, xhi, ylo, yhi, reset: bool = True):
        self.bounds = (float(xlo), float(xhi), float(ylo), float(yhi))
        self._apply(reset)

    def _on_resized(self, *_):
        if self.bounds is None:
            return
        rx, ry = self.vb.viewRange()
        full = (self._fit is None or
                abs((rx[1] - rx[0]) - self._fit[0]) < 1e-6 * self._fit[0] or
                abs((ry[1] - ry[0]) - self._fit[1]) < 1e-6 * self._fit[1])
        self._apply(reset=full)

    def _apply(self, reset: bool):
        xlo, xhi, ylo, yhi = self.bounds
        p = self.pad * (max(xhi - xlo, yhi - ylo) or 1.0)
        xlo, xhi, ylo, yhi = xlo - p, xhi + p, ylo - p, yhi + p
        bw, bh = xhi - xlo, yhi - ylo
        W, H = self.vb.width(), self.vb.height()
        if W > 1 and H > 1:
            if bw * H >= bh * W:
                fx, fy = bw, bw * H / W
            else:
                fx, fy = bh * W / H, bh
        else:                                 # not laid out yet
            fx, fy = bw, bh
        cx, cy = 0.5 * (xlo + xhi), 0.5 * (ylo + yhi)
        self._fit = (fx, fy)
        rng = ((cx - fx / 2, cx + fx / 2), (cy - fy / 2, cy + fy / 2))
        self.vb.setLimits(xMin=rng[0][0], xMax=rng[0][1], yMin=rng[1][0], yMax=rng[1][1],
                          maxXRange=fx, maxYRange=fy)
        if reset:
            self.vb.setRange(xRange=rng[0], yRange=rng[1], padding=0)
