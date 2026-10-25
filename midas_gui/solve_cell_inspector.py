"""Solve Cell "Spot inspector" (V10): for the selected spot, the raw/background-
subtracted ROI cube around its centroid -- ROI image at a chosen frame, ω–row
and ω–col projections, the rocking curve (ROI sum and peak pixel vs ω), and
row/col profiles at the peak frame. Answers "real peak, merged spots, streak or
artifact?" and shows the predicted crossing next to the observed centroid.

Driven by the cached ``spot_shoeboxes`` product (see ``solve_cell/shoeboxes``);
works with any ``(n_spots, nF, nR, nC)`` float cube where NaN = not a measured
pixel. Pixel (0,0) is drawn bottom-left (x = col, y = row, y up) like every MIDAS
viewer. Frames outside the ingest ω window stay visible but are drawn hollow /
grey in the rocking curve.
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np
import pandas as pd
import pyqtgraph as pg

from midas_gui.plot_limits import AspectFit, frame_outline, limit_all
from PyQt5 import QtCore, QtWidgets

_MORPH_FIELDS = ("integrated", "peak_counts", "n_frames", "volume_vox", "length_px", "width_px", "aspect",
                 "omega_width_frames", "full_n_sub_peaks")


class SpotInspector(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(QtWidgets.QLabel("Data:"))
        self.mode = QtWidgets.QComboBox()
        self.mode.addItems(["Background-subtracted", "Raw counts"])
        self.mode.currentIndexChanged.connect(lambda _=None: self._redraw())
        bar.addWidget(self.mode)
        bar.addWidget(QtWidgets.QLabel("Frame:"))
        self.frame = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.frame.valueChanged.connect(lambda _=None: self._redraw_roi())
        bar.addWidget(self.frame, stretch=1)
        self.frame_label = QtWidgets.QLabel("—")
        self.frame_label.setMinimumWidth(170)
        bar.addWidget(self.frame_label)
        lay.addLayout(bar)
        self.info = QtWidgets.QLabel("Select a spot (Reciprocal map or Detector) to inspect its shoebox.")
        self.info.setWordWrap(True)
        self.info.setTextFormat(QtCore.Qt.PlainText)
        lay.addWidget(self.info)
        self.glw = pg.GraphicsLayoutWidget()
        lay.addWidget(self.glw, stretch=1)

        self._cubes: Optional[dict] = None          # {"raw","bgsub","frame_in_window"}
        self._index: Optional[pd.DataFrame] = None
        self._omega_of: Optional[Callable] = None   # (panel_id, loaded_idx array) -> omega deg
        self._row: Optional[dict] = None
        self._pred: Optional[dict] = None
        self._k: Optional[int] = None
        self.plots: dict = {}

    # ── data ────────────────────────────────────────────────────────
    def set_cache(self, arrays: Optional[dict], index: Optional[pd.DataFrame], omega_of: Callable):
        self._cubes, self._index, self._omega_of = arrays, index, omega_of
        self.show_spot(self._row, self._pred)

    def has_spot(self, spot_id) -> bool:
        return self._index is not None and spot_id in set(self._index.spot_id)

    def show_spot(self, row: Optional[dict], pred: Optional[dict] = None):
        self._row, self._pred = row, pred
        self._k = None
        if row is None or self._cubes is None or self._index is None:
            self.info.setText("Select a spot (Reciprocal map or Detector) to inspect its shoebox."
                              if self._cubes is not None else
                              "No shoebox cache: build it (Diagnostics ▸ build visualization data) or open a project that stores it.")
            self.glw.clear(); self.plots = {}
            return
        hit = np.flatnonzero(self._index.spot_id.to_numpy() == row["spot_id"])
        if not len(hit):
            self.info.setText(f"Spot {row['spot_id']} is not in the shoebox cache.")
            self.glw.clear(); self.plots = {}
            return
        self._k = int(hit[0])
        nF = self._cubes["raw"].shape[1]
        self.frame.blockSignals(True)
        self.frame.setRange(0, nF - 1)
        # open on the frame holding the centroid
        f0 = int(self._index.frame0.iloc[self._k])
        self.frame.setValue(int(np.clip(int(row["loaded_frame_idx"]) - f0, 0, nF - 1)))
        self.frame.blockSignals(False)
        self._redraw()

    # ── drawing ─────────────────────────────────────────────────────
    def _cube(self) -> np.ndarray:
        return self._cubes["bgsub" if self.mode.currentIndex() == 0 else "raw"][self._k]

    def _coords(self):
        ix = self._index.iloc[self._k]
        cube = self._cube()
        nF, nR, nC = cube.shape
        f = int(ix.frame0) + np.arange(nF)
        pid = int(ix.panel_id)
        om = np.asarray(self._omega_of(pid, f), float)
        step = float(np.median(np.diff(om))) if nF > 1 else 0.1
        return ix, f, om, step, int(ix.row0), int(ix.col0), nR, nC

    def _redraw(self):
        if self._k is None:
            return
        self.glw.clear(); self.plots = {}
        cube = self._cube()
        ix, f, om, step, r0, c0, nR, nC = self._coords()
        row = self._row
        in_win = self._cubes["frame_in_window"][self._k]
        with np.errstate(all="ignore"):
            roi_sum = np.nansum(cube, axis=(1, 2))
            roi_sum[np.all(np.isnan(cube), axis=(1, 2))] = np.nan
            peak = np.nanmax(cube.reshape(len(cube), -1), axis=1)
        pk = int(np.nanargmax(np.where(np.isnan(roi_sum), -np.inf, roi_sum))) if np.isfinite(roi_sum).any() else 0
        self._peak_frame = pk
        vals = cube[np.isfinite(cube)]
        self._levels = (float(np.percentile(vals, 1)), float(np.percentile(vals, 99.9))) if vals.size else (0, 1)

        # ROI image at the chosen frame
        p = self.glw.addPlot(row=0, col=0, title="ROI at frame")
        p.setAspectLocked(True)
        self._roi_img = pg.ImageItem(axisOrder="row-major")
        p.addItem(self._roi_img)
        p.setLabel("bottom", "col (px)"); p.setLabel("left", "row (px)")
        self._roi_rect = QtCore.QRectF(c0 - 0.5, r0 - 0.5, nC, nR)   # applied after setImage (needs a size)
        p.addItem(frame_outline(c0 - 0.5, c0 - 0.5 + nC, r0 - 0.5, r0 - 0.5 + nR))
        AspectFit(p.getViewBox()).set_bounds(c0 - 0.5, c0 - 0.5 + nC, r0 - 0.5, r0 - 0.5 + nR)
        p.addItem(pg.ScatterPlotItem(x=[row["col"]], y=[row["row"]], symbol="+", size=16,
                                     pen=pg.mkPen("#ff3b8d", width=2), brush=pg.mkBrush(None), name="centroid"))
        if self._pred:
            p.addItem(pg.ScatterPlotItem(x=[self._pred["pred_col"]], y=[self._pred["pred_row"]], symbol="x", size=14,
                                         pen=pg.mkPen("#38d9a9", width=2), brush=pg.mkBrush(None)))
        self.plots["roi"] = p

        # ω–row and ω–col projections (sum over the other pixel axis; NaN cells ignored)
        for name, axis, lab, off, n, col in (("om_row", 2, "row (px)", r0, nR, 1), ("om_col", 1, "col (px)", c0, nC, 1)):
            q = self.glw.addPlot(row=0 if name == "om_row" else 1, col=1, title=f"ω–{lab.split()[0]} projection")
            img = pg.ImageItem(axisOrder="row-major")
            with np.errstate(all="ignore"):
                proj = np.nansum(cube, axis=axis)          # (nF, n)
            proj[np.all(np.isnan(cube), axis=axis)] = np.nan
            img.setImage(np.nan_to_num(proj.T))            # (n, nF): y = pixel, x = frame
            img.setRect(QtCore.QRectF(om[0] - step / 2, off - 0.5, step * len(om), n))
            q.addItem(img)
            q.setLabel("bottom", "ω (°)"); q.setLabel("left", lab)
            q.addItem(pg.InfiniteLine(row["omega_deg"], angle=90, pen=pg.mkPen("#ff3b8d", width=1)))
            if self._pred:
                q.addItem(pg.InfiniteLine(self._pred["pred_omega"], angle=90, pen=pg.mkPen("#38d9a9", width=1, style=QtCore.Qt.DashLine)))
            self.plots[name] = q

        # rocking curve
        rc = self.glw.addPlot(row=1, col=0, title="Rocking curve")
        rc.addLegend(offset=(5, 5))
        good = np.isfinite(roi_sum)
        rc.plot(om[good & in_win], roi_sum[good & in_win], pen=pg.mkPen("#4da3ff", width=1.5), symbol="o", symbolSize=5,
                symbolBrush="#4da3ff", name="ROI sum (in window)")
        if (good & ~in_win).any():
            rc.plot(om[good & ~in_win], roi_sum[good & ~in_win], pen=None, symbol="o", symbolSize=5,
                    symbolBrush=None, symbolPen="#888888", name="outside ω window")
        rc.plot(om[good & in_win], peak[good & in_win], pen=pg.mkPen("#ffa94d", width=1), name="peak pixel")
        rc.addItem(pg.InfiniteLine(row["omega_deg"], angle=90, pen=pg.mkPen("#ff3b8d", width=1)))
        if self._pred:
            rc.addItem(pg.InfiniteLine(self._pred["pred_omega"], angle=90, pen=pg.mkPen("#38d9a9", width=1, style=QtCore.Qt.DashLine)))
        rc.setLabel("bottom", "ω (°)"); rc.setLabel("left", "counts")
        self.plots["rocking"] = rc

        # row / col profiles at the peak frame (separate axes: they span different pixel ranges)
        sl = cube[pk]
        with np.errstate(all="ignore"):
            for name, ax_sum, off, n, color, lab, c in (
                    ("profile_col", 0, c0, nC, "#da77f2", "col (px)", 0), ("profile_row", 1, r0, nR, "#63e6be", "row (px)", 1)):
                pr = self.glw.addPlot(row=2, col=c, title=f"Profile along {lab.split()[0]} at peak frame")
                pr.plot(np.arange(off, off + n), np.nansum(sl, axis=ax_sum), pen=pg.mkPen(color, width=1.5),
                        symbol="o", symbolSize=4, symbolBrush=color)
                pr.setLabel("bottom", lab); pr.setLabel("left", "counts")
                self.plots[name] = pr

        self._write_info(row, om, in_win, roi_sum, pk)
        self._redraw_roi()
        limit_all({k: v for k, v in self.plots.items() if k != "roi"})

    def _redraw_roi(self):
        if self._k is None or not hasattr(self, "_roi_img"):
            return
        cube = self._cube()
        i = self.frame.value()
        lo, hi = self._levels
        img = cube[i]
        self._roi_img.setImage(np.nan_to_num(img, nan=lo), levels=(lo, hi if hi > lo else lo + 1))
        self._roi_img.setRect(self._roi_rect)
        _ix, f, om, *_ = self._coords()
        inw = bool(self._cubes["frame_in_window"][self._k][i])
        self.frame_label.setText(f"loaded {int(f[i])} · ω {om[i]:+.3f}°" + ("" if inw else "  (outside window)"))

    def _write_info(self, row, om, in_win, roi_sum, pk):
        parts = [f"Spot {row['spot_id']}", f"panel {row.get('panel_id')}",
                 f"centroid row {row['row']:.1f}, col {row['col']:.1f}, ω {row['omega_deg']:+.3f}°"]
        parts += [f"{k}: {row[k]:.5g}" for k in _MORPH_FIELDS if k in row and np.isfinite(row[k])]
        if self._pred:
            parts.append(f"predicted Δ(obs−pred): row {self._pred['d_row']:+.2f}, col {self._pred['d_col']:+.2f} px, "
                         f"ω {self._pred['d_omega_deg']:+.3f}°")
        parts.append(f"peak of ROI sum at ω {om[pk]:+.3f}°")
        if row.get("full_n_sub_peaks", 1) and row.get("full_n_sub_peaks", 1) > 1:
            parts.append(f"split from a parent blob with {int(row['full_n_sub_peaks'])} sub-peaks")
        self.info.setText(" · ".join(parts))
