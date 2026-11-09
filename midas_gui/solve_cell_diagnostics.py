"""Solve Cell "Diagnostics" view: detector residual vector fields (V09) and the
acquisition/omega timeline (V11).

Pure pyqtgraph, driven by the cached visualization products
(``solve_cell/viz_products``): the ``residuals`` + ``predictions`` tables of a
domain and the ``frames`` manifest. Sign convention everywhere:
``residual = observed - predicted`` (pixels; omega in degrees).

Pixel (0, 0) is drawn bottom-left (same as every MIDAS viewer, see
``ImageViewer.set_raw_frame``), so detector scatter plots keep y pointing up.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
import pyqtgraph as pg

from midas_gui.plot_limits import AspectFit, frame_outline, limit_all
from PyQt5 import QtCore, QtWidgets

_PANEL_COLORS = ["#4da3ff", "#ffa94d", "#69db7c", "#da77f2", "#ffd43b", "#63e6be"]


def _finite(*arrs):
    m = np.ones(len(arrs[0]), bool)
    for a in arrs:
        m &= np.isfinite(a)
    return m


class DiagnosticsView(QtWidgets.QWidget):
    """Left: one detector residual-vector plot per panel. Right/bottom: residual
    histograms, residual vs omega, and the omega timeline."""

    spotClicked = QtCore.pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(QtWidgets.QLabel("Arrow scale ×"))
        self.arrow_scale = QtWidgets.QDoubleSpinBox()
        self.arrow_scale.setRange(1, 500)
        self.arrow_scale.setValue(40)
        self.arrow_scale.setToolTip(
            "Residual arrows run predicted → observed, exaggerated by this "
            "factor (residuals are typically a fraction of a pixel).")
        self.arrow_scale.valueChanged.connect(lambda _=None: self._redraw())
        bar.addWidget(self.arrow_scale)
        self.status = QtWidgets.QLabel("No residual data: run Solve Cell, or open a project that stores it.")
        self.status.setWordWrap(True)
        bar.addWidget(self.status, stretch=1)
        lay.addLayout(bar)

        self.glw = pg.GraphicsLayoutWidget()
        lay.addWidget(self.glw, stretch=1)
        self._residuals: Optional[pd.DataFrame] = None
        self._predictions: Optional[pd.DataFrame] = None
        self._frames: Optional[pd.DataFrame] = None
        self._spots: Optional[pd.DataFrame] = None
        self._panel_shapes: dict = {}
        self.plots: dict = {}            # name -> PlotItem, for tests/inspection

    # ── data in ─────────────────────────────────────────────────────
    def set_data(self, *, residuals: Optional[pd.DataFrame], predictions: Optional[pd.DataFrame],
                 frames: Optional[pd.DataFrame], spots: Optional[pd.DataFrame] = None,
                 panel_shapes: Optional[dict] = None, label: str = ""):
        self._residuals, self._predictions = residuals, predictions
        self._frames, self._spots = frames, spots
        self._panel_shapes = panel_shapes or {}
        self._label = label
        self._redraw()

    def clear(self):
        self.set_data(residuals=None, predictions=None, frames=None)

    # ── drawing ─────────────────────────────────────────────────────
    def _redraw(self):
        self.glw.clear()
        self.plots = {}
        self._fitted = set()
        res, pred, frames = self._residuals, self._predictions, self._frames
        has_res = res is not None and len(res) > 0
        if not has_res and (frames is None or not len(frames)):
            self.status.setText("No residual data: run Solve Cell, or open a project that stores it.")
            return
        panels = sorted(set(res.panel_id.astype(int))) if has_res else \
            sorted(set(frames.panel_id.astype(int)))
        if has_res:
            rms = (np.sqrt(np.mean(res.d_row ** 2)), np.sqrt(np.mean(res.d_col ** 2)),
                   np.sqrt(np.mean(res.d_omega_deg ** 2)))
            self.status.setText(
                f"{getattr(self, '_label', '')}: {len(res)} matched reflections · RMS Δrow {rms[0]:.2f} px, "
                f"Δcol {rms[1]:.2f} px, Δω {rms[2]:.3f}° · residual = observed − predicted")
            self._draw_vector_fields(res, panels)
            self._draw_histograms(res)
        self._draw_timeline(res if has_res else None, pred, frames, panels)
        limit_all({k: v for k, v in self.plots.items() if k not in self._fitted})

    def _draw_vector_fields(self, res, panels):
        scale = float(self.arrow_scale.value())
        for i, pid in enumerate(panels):
            d = res[res.panel_id == pid]
            p = self.glw.addPlot(row=0, col=i, title=f"Panel {pid} — residuals ×{scale:g}")
            p.setLabel("bottom", "col (px)")
            p.setLabel("left", "row (px)")
            p.setAspectLocked(True)
            shape = self._panel_shapes.get(pid)
            if shape:
                n_rows, n_cols = shape
                p.addItem(frame_outline(-0.5, n_cols - 0.5, -0.5, n_rows - 0.5))
                AspectFit(p.getViewBox()).set_bounds(-0.5, n_cols - 0.5, -0.5, n_rows - 0.5)
                self._fitted.add(f"vectors_{pid}")
            m = _finite(d.pred_col.to_numpy(), d.pred_row.to_numpy(), d.d_col.to_numpy(), d.d_row.to_numpy())
            d = d[m]
            if len(d):
                x0, y0 = d.pred_col.to_numpy(), d.pred_row.to_numpy()
                x1, y1 = x0 + scale * d.d_col.to_numpy(), y0 + scale * d.d_row.to_numpy()
                xs = np.empty(2 * len(d)); ys = np.empty(2 * len(d))
                xs[0::2], xs[1::2], ys[0::2], ys[1::2] = x0, x1, y0, y1
                p.addItem(pg.PlotCurveItem(xs, ys, connect="pairs",
                                           pen=pg.mkPen(_PANEL_COLORS[i % 6], width=1.5)))
                sc = pg.ScatterPlotItem(x=x0, y=y0, size=5, brush=pg.mkBrush("#ffffff"),
                                        pen=pg.mkPen(None), data=d.spot_id.to_numpy())
                sc.sigClicked.connect(lambda _s, pts, _e: pts and self.spotClicked.emit(pts[0].data()))
                p.addItem(sc)
            self.plots[f"vectors_{pid}"] = p

    def _draw_histograms(self, res):
        for j, (col, lab) in enumerate((("d_row", "Δrow (px)"), ("d_col", "Δcol (px)"),
                                        ("d_omega_deg", "Δω (°)"))):
            p = self.glw.addPlot(row=1, col=j, title=f"{lab}  obs − pred")
            v = res[col].to_numpy(float)
            v = v[np.isfinite(v)]
            if len(v):
                h, edges = np.histogram(v, bins=min(25, max(5, len(v) // 3)))
                p.addItem(pg.BarGraphItem(x=0.5 * (edges[1:] + edges[:-1]), height=h,
                                          width=(edges[1] - edges[0]) * 0.9, brush="#4da3ff"))
                p.addItem(pg.InfiniteLine(0, angle=90, pen=pg.mkPen("#ff6b6b", width=1)))
            p.setLabel("bottom", lab)
            self.plots[f"hist_{col}"] = p

    def _draw_timeline(self, res, pred, frames, panels):
        """Top: loaded-frame omega coverage per panel (in-window vs excluded).
        Bottom: predicted-visible crossings vs observed spots per omega bin --
        a bin with many predictions and few observations is where to look."""
        p = self.glw.addPlot(row=2, col=0, colspan=max(3, len(panels)), title="Acquisition timeline")
        p.setLabel("bottom", "ω (°)")
        p.addLegend(offset=(10, 10))
        n_pan = max(len(panels), 1)
        if frames is not None and len(frames):
            for i, pid in enumerate(panels):
                f = frames[frames.panel_id == pid]
                if not len(f):
                    continue
                for flag, color, nm in ((True, _PANEL_COLORS[i % 6], f"panel {pid} in window"),
                                        (False, "#777777", None)):
                    s = f[f.in_window == flag]
                    if len(s):
                        p.addItem(pg.ScatterPlotItem(x=s.omega_start_deg.to_numpy(),
                                                     y=np.full(len(s), float(i)), size=3,
                                                     brush=pg.mkBrush(color), pen=pg.mkPen(None), name=nm))
            p.setLabel("left", "panel row")
        self.plots["timeline"] = p
        if pred is not None and len(pred):
            p2 = self.glw.addPlot(row=3, col=0, colspan=max(3, len(panels)),
                                  title="Predicted visible crossings vs observed (per 2° ω bin)")
            vis = pred[pred.visibility == "visible"]
            lo = float(np.floor(min(vis.omega_deg.min() if len(vis) else 0, 0)))
            hi = float(np.ceil(max(vis.omega_deg.max() if len(vis) else 1, 1)))
            edges = np.arange(lo - 2, hi + 4, 2.0)
            hp, _ = np.histogram(vis.omega_deg, bins=edges)
            ho, _ = np.histogram(vis[vis.observed_spot_id >= 0].omega_deg, bins=edges)
            ctr = 0.5 * (edges[1:] + edges[:-1])
            p2.addLegend(offset=(10, 10))
            p2.addItem(pg.BarGraphItem(x=ctr, height=hp, width=1.6, brush="#ffa94d", name="predicted"))
            p2.addItem(pg.BarGraphItem(x=ctr, height=ho, width=1.0, brush="#38d9a9", name="observed"))
            p2.setLabel("bottom", "ω (°)")
            p2.setLabel("left", "crossings")
            self.plots["coverage"] = p2
