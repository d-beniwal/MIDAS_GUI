"""Shared display widgets.

ImageViewer / PickableImageViewer / ProfileViewer / LogPanel are ported verbatim
from midas_workflow_gui_v3.py (frozen template).  ResidualBarChart, DistortionTable
and CorrectionFlagsWidget are new Phase-1 additions.

pyqtgraph rules (see context/design_rules.md) preserved:
  - store pg.SignalProxy as instance var      (else GC'd, hover dies)
  - setColorMap() not setLookupTable()        (else reset on setImage)
  - setXRange() not autoRange(axes=)          (else TypeError on this pg version)
  - ring markers redrawn LAST inside _replot  (else don't render)
  - int(x) floor for pixel indexing           (not int(x+0.5))
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from midas_gui.constants import COLORMAPS, DISTORTION_NAMES
from midas_gui.helpers import _NoScrollSpinBox, _NoScrollDoubleSpinBox, _fspin, _twocol


# ═════════════════════════════════════════════════════════════════════════════
#  ImageViewer
# ═════════════════════════════════════════════════════════════════════════════

class ImageViewer(QtWidgets.QWidget):
    """pyqtgraph image viewer with log scale, colormap, vmin/vmax, crosshair,
    pixel-value status bar, and a mask overlay."""

    def __init__(self, parent=None, title=""):
        super().__init__(parent)
        pg.setConfigOptions(background="k", foreground="w")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # Toolbar
        bar = QtWidgets.QHBoxLayout()
        if title:
            bar.addWidget(QtWidgets.QLabel(f"<b>{title}</b>"))
        self._log = QtWidgets.QCheckBox("Log")
        self._log.setChecked(True)
        self._log.toggled.connect(self._redisplay)
        bar.addWidget(self._log)
        bar.addWidget(QtWidgets.QLabel("cmap:"))
        self._cmap = QtWidgets.QComboBox()
        self._cmap.addItems(COLORMAPS)
        self._cmap.currentTextChanged.connect(self._set_cmap)
        self._cmap.setFixedWidth(90)
        bar.addWidget(self._cmap)
        bar.addWidget(QtWidgets.QLabel("vmin%:"))
        self._vmin = _NoScrollSpinBox()
        self._vmin.setRange(0, 99); self._vmin.setValue(30); self._vmin.setFixedWidth(45)
        self._vmin.valueChanged.connect(self._redisplay)
        bar.addWidget(self._vmin)
        bar.addWidget(QtWidgets.QLabel("vmax%:"))
        self._vmax = _NoScrollSpinBox()
        self._vmax.setRange(1, 100); self._vmax.setValue(99); self._vmax.setFixedWidth(45)
        self._vmax.valueChanged.connect(self._redisplay)
        bar.addWidget(self._vmax)
        bar.addStretch(1)
        self._toolbar_layout = bar   # exposed so subclasses can append widgets
        layout.addLayout(bar)

        # Image view
        self._iv = pg.ImageView(view=pg.PlotItem())
        self._iv.ui.roiBtn.hide(); self._iv.ui.menuBtn.hide()
        vb = self._iv.getView().getViewBox()
        vb.setMouseEnabled(x=True, y=True)
        vb.setMouseMode(pg.ViewBox.PanMode)
        layout.addWidget(self._iv, stretch=1)

        # Crosshair
        self._vl = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("y", width=1))
        self._hl = pg.InfiniteLine(angle=0,  movable=False, pen=pg.mkPen("y", width=1))
        self._iv.addItem(self._vl); self._iv.addItem(self._hl)
        self._mouse_proxy = pg.SignalProxy(
            self._iv.scene.sigMouseMoved, rateLimit=60, slot=self._mouse)

        # Overlay (for mask)
        self._overlay = pg.ImageItem()
        self._overlay.setZValue(10)
        self._iv.addItem(self._overlay)

        # Bottom status bar — pixel coordinates and raw value on hover
        self._coord_bar = QtWidgets.QLabel("Move cursor over image to inspect pixel values")
        self._coord_bar.setStyleSheet(
            "color:#dddddd; background:#1a1a1a; font-family:monospace;"
            "font-size:12px; padding:2px 6px; border-top:1px solid #444;")
        layout.addWidget(self._coord_bar)

        self._data: Optional[np.ndarray] = None
        self._set_cmap(COLORMAPS[0])

    def set_image(self, data: np.ndarray, autorange: bool = True):
        self._data = data.astype(np.float32)
        self._redisplay()
        if autorange:
            self._iv.getView().getViewBox().autoRange()
        self._coord_bar.setText(
            f"Image {data.shape[1]}×{data.shape[0]} px  |  "
            "Move cursor over image to inspect pixel values")

    def set_mask_overlay(self, mask: Optional[np.ndarray]):
        if mask is None:
            self._overlay.setImage(np.zeros((1, 1, 4), dtype=np.uint8))
            return
        NZ, NY = mask.shape
        rgba = np.zeros((NY, NZ, 4), dtype=np.uint8)
        bad = mask.T.astype(bool)
        rgba[bad, 0] = 220  # red
        rgba[bad, 3] = 180  # alpha
        self._overlay.setImage(rgba)

    def clear_overlay(self):
        self._overlay.setImage(np.zeros((1, 1, 4), dtype=np.uint8))

    def set_overlay_visible(self, visible: bool):
        self._overlay.setVisible(visible)

    def _redisplay(self):
        if self._data is None:
            return
        d = self._data
        if self._log.isChecked():
            disp = np.log10(np.clip(d, 1e-10, None)).T
        else:
            disp = d.T
        fin = disp[np.isfinite(disp)]
        if fin.size:
            lo = float(np.percentile(fin, self._vmin.value()))
            hi = float(np.percentile(fin, self._vmax.value()))
        else:
            lo, hi = 0.0, 1.0
        # autoRange/autoHistogramRange default to True in pyqtgraph and would reset
        # the view on every redraw; set_image() handles framing explicitly via its
        # `autorange` flag, so the zoom/pan is preserved across frames and re-levels.
        self._iv.setImage(disp.astype(np.float32), autoLevels=False, levels=(lo, hi),
                          autoRange=False, autoHistogramRange=False)

    def _set_cmap(self, name: str):
        try:
            cmap = pg.colormap.get(name)
        except Exception:
            try:
                cmap = pg.colormap.getFromMatplotlib(name)
            except Exception:
                return
        self._iv.setColorMap(cmap)

    def _mouse(self, evt):
        pos = evt[0]
        vb = self._iv.getView().getViewBox()
        if self._iv.getView().sceneBoundingRect().contains(pos):
            mp = vb.mapSceneToView(pos)
            x, y = mp.x(), mp.y()
            self._vl.setPos(x); self._hl.setPos(y)
            if self._data is not None:
                ix, iy = int(x), int(y)   # floor, not round (Bug 6)
                h, w = self._data.shape
                if 0 <= iy < h and 0 <= ix < w:
                    val = self._data[iy, ix]
                    self._coord_bar.setText(
                        f"  x (col) = {ix}    y (row) = {iy}    "
                        f"intensity = {val:.4g}    (image {w}×{h} px)")


# ═════════════════════════════════════════════════════════════════════════════
#  PickableImageViewer
# ═════════════════════════════════════════════════════════════════════════════

class PickableImageViewer(ImageViewer):
    """ImageViewer + beam-centre pick tools.

    Pick BC   — single click sets beam centre (bcPicked signal).
    Pick Ring — 3+ clicks; algebraic circle fit estimates BC (ringFitBC signal).
    """
    bcPicked  = QtCore.pyqtSignal(float, float)         # (BC_y, BC_z)
    ringFitBC = QtCore.pyqtSignal(float, float, float)  # (BC_y, BC_z, R_px)

    PICK_NONE = 0
    PICK_BC   = 1
    PICK_RING = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pick_mode = self.PICK_NONE
        self._ring_pts:        list = []
        self._ring_pt_items:   list = []
        self._ring_fit_item    = None
        self._ring_fit_center  = None
        self._bc_click_item    = None

        _BTN = ("QPushButton{padding:2px 8px;border-radius:3px}"
                "QPushButton:checked{background:#2a7fd4;color:white;font-weight:bold}")
        pick_bar = QtWidgets.QHBoxLayout()
        pick_bar.setSpacing(4)

        self._pick_bc_btn = QtWidgets.QPushButton("Pick BC")
        self._pick_bc_btn.setCheckable(True)
        self._pick_bc_btn.setStyleSheet(_BTN)
        self._pick_bc_btn.setToolTip(
            "Click once on the image to set the beam center as the initial seed")
        self._pick_bc_btn.toggled.connect(self._on_pick_bc_toggled)
        pick_bar.addWidget(self._pick_bc_btn)

        self._pick_ring_btn = QtWidgets.QPushButton("Pick Ring")
        self._pick_ring_btn.setCheckable(True)
        self._pick_ring_btn.setStyleSheet(_BTN)
        self._pick_ring_btn.setToolTip(
            "Click 3+ points on a ring; algebraic circle fit estimates beam center")
        self._pick_ring_btn.toggled.connect(self._on_pick_ring_toggled)
        pick_bar.addWidget(self._pick_ring_btn)

        self._undo_btn = QtWidgets.QPushButton("Undo")
        self._undo_btn.setEnabled(False)
        self._undo_btn.setToolTip("Remove last ring point")
        self._undo_btn.clicked.connect(self._undo_ring_point)
        pick_bar.addWidget(self._undo_btn)

        self._clear_ring_btn = QtWidgets.QPushButton("Clear")
        self._clear_ring_btn.setEnabled(False)
        self._clear_ring_btn.clicked.connect(self._clear_ring_points)
        pick_bar.addWidget(self._clear_ring_btn)

        self._pick_status = QtWidgets.QLabel("")
        self._pick_status.setStyleSheet("color:#f0c060;font-size:11px")
        pick_bar.addWidget(self._pick_status)
        pick_bar.addStretch(1)

        self.layout().insertLayout(1, pick_bar)   # after main toolbar
        self._iv.scene.sigMouseClicked.connect(self._on_scene_clicked)

    def _on_pick_bc_toggled(self, checked: bool):
        if checked:
            self._pick_ring_btn.blockSignals(True)
            self._pick_ring_btn.setChecked(False)
            self._pick_ring_btn.blockSignals(False)
            self._pick_mode = self.PICK_BC
            self._pick_status.setText("Click image to set BC")
        elif self._pick_mode == self.PICK_BC:
            self._pick_mode = self.PICK_NONE
            self._pick_status.setText("")

    def _on_pick_ring_toggled(self, checked: bool):
        if checked:
            self._pick_bc_btn.blockSignals(True)
            self._pick_bc_btn.setChecked(False)
            self._pick_bc_btn.blockSignals(False)
            self._pick_mode = self.PICK_RING
            n = len(self._ring_pts)
            self._pick_status.setText(
                f"{n} pts — click ring to add" if n else
                "Click on a ring to pick points (need ≥3)")
        elif self._pick_mode == self.PICK_RING:
            self._pick_mode = self.PICK_NONE
            self._pick_status.setText(
                f"{len(self._ring_pts)} ring pts (mode off)"
                if self._ring_pts else "")

    def _on_scene_clicked(self, event):
        if self._pick_mode == self.PICK_NONE:
            return
        if event.button() != QtCore.Qt.LeftButton:
            return
        vb  = self._iv.getView().getViewBox()
        pos = vb.mapSceneToView(event.scenePos())
        x, y = pos.x(), pos.y()
        if self._pick_mode == self.PICK_BC:
            self._set_bc_marker(x, y)
            self.bcPicked.emit(x, y)
            self._pick_bc_btn.setChecked(False)   # one-shot
        elif self._pick_mode == self.PICK_RING:
            self._add_ring_point(x, y)

    def _set_bc_marker(self, x: float, y: float):
        if self._bc_click_item is not None:
            self._iv.removeItem(self._bc_click_item)
        self._bc_click_item = pg.ScatterPlotItem(
            [x], [y], symbol="+", size=20,
            pen=pg.mkPen("#00aaff", width=2.5), brush=pg.mkBrush(0, 0, 0, 0))
        self._iv.addItem(self._bc_click_item)

    def _add_ring_point(self, x: float, y: float):
        self._ring_pts.append((x, y))
        dot = pg.ScatterPlotItem(
            [x], [y], symbol="o", size=10,
            pen=pg.mkPen("#f0c060", width=1.5),
            brush=pg.mkBrush(240, 192, 96, 180))
        self._iv.addItem(dot)
        self._ring_pt_items.append(dot)
        self._undo_btn.setEnabled(True)
        self._clear_ring_btn.setEnabled(True)
        self._update_ring_fit()

    def _undo_ring_point(self):
        if not self._ring_pts:
            return
        self._ring_pts.pop()
        if self._ring_pt_items:
            self._iv.removeItem(self._ring_pt_items.pop())
        self._undo_btn.setEnabled(bool(self._ring_pts))
        self._clear_ring_btn.setEnabled(bool(self._ring_pts))
        self._update_ring_fit()

    def _clear_ring_points(self):
        for item in self._ring_pt_items:
            self._iv.removeItem(item)
        self._ring_pt_items.clear()
        self._ring_pts.clear()
        for item in (self._ring_fit_item, self._ring_fit_center):
            if item is not None:
                self._iv.removeItem(item)
        self._ring_fit_item = self._ring_fit_center = None
        self._undo_btn.setEnabled(False)
        self._clear_ring_btn.setEnabled(False)
        self._pick_status.setText(
            "Click on a ring to pick points (need ≥3)"
            if self._pick_mode == self.PICK_RING else "")

    def _update_ring_fit(self):
        n = len(self._ring_pts)
        if n < 3:
            for item in (self._ring_fit_item, self._ring_fit_center):
                if item is not None:
                    self._iv.removeItem(item)
            self._ring_fit_item = self._ring_fit_center = None
            self._pick_status.setText(f"{n} pt{'s' if n != 1 else ''} — need {3-n} more")
            return
        fit = self._fit_circle(self._ring_pts)
        if fit is None:
            self._pick_status.setText(f"{n} pts — fit failed (collinear?)")
            return
        cx, cy, r = fit
        th  = np.linspace(0, 2 * math.pi, 512)
        xs  = cx + r * np.cos(th);  ys = cy + r * np.sin(th)
        pen = pg.mkPen("#f0c060", width=1.5, style=QtCore.Qt.DashLine)
        if self._ring_fit_item is not None:
            self._iv.removeItem(self._ring_fit_item)
        self._ring_fit_item = pg.PlotDataItem(xs, ys, pen=pen)
        self._iv.addItem(self._ring_fit_item)
        if self._ring_fit_center is not None:
            self._iv.removeItem(self._ring_fit_center)
        self._ring_fit_center = pg.ScatterPlotItem(
            [cx], [cy], symbol="+", size=18,
            pen=pg.mkPen("#f0c060", width=2.5), brush=pg.mkBrush(0, 0, 0, 0))
        self._iv.addItem(self._ring_fit_center)
        self._pick_status.setText(
            f"{n} pts | fit: BC=({cx:.1f}, {cy:.1f})  R={r:.1f} px → seed updated")
        self.ringFitBC.emit(cx, cy, r)

    @staticmethod
    def _fit_circle(pts: list) -> Optional[tuple]:
        """Algebraic least-squares circle fit.  Returns (cx, cy, r) or None."""
        arr = np.array(pts, dtype=np.float64)
        x, y = arr[:, 0], arr[:, 1]
        A = np.column_stack([x, y, np.ones(len(x))])
        b = -(x ** 2 + y ** 2)
        try:
            res, _, rank, _ = np.linalg.lstsq(A, b, rcond=None)
        except np.linalg.LinAlgError:
            return None
        if rank < 3:
            return None
        D, E, F = res
        cx, cy = -D / 2, -E / 2
        r2 = cx ** 2 + cy ** 2 - F
        return (cx, cy, math.sqrt(r2)) if r2 > 0 else None


# ═════════════════════════════════════════════════════════════════════════════
#  ProfileViewer
# ═════════════════════════════════════════════════════════════════════════════

class ProfileViewer(QtWidgets.QWidget):
    """1D radial profile viewer with x-axis unit switching and ring markers.

    Optionally shades a ±σ uncertainty band when sigma is supplied.
    Left-clicking the plot emits ``radiusClicked`` (radius in px), so a caller can
    draw the matching ring on the image; a marker line shows the picked position.
    """

    radiusClicked = QtCore.pyqtSignal(float)   # picked radius in px

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(2)

        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(QtWidgets.QLabel("X:"))
        self._xaxis = QtWidgets.QComboBox()
        self._xaxis.addItems(["R (px)", "2θ (°)", "Q (Å⁻¹)"])
        self._xaxis.currentIndexChanged.connect(self._replot)
        self._xaxis.currentIndexChanged.connect(self._clear_pick_line)
        bar.addWidget(self._xaxis)
        self._logy = QtWidgets.QCheckBox("Log Y")
        self._logy.toggled.connect(self._replot)
        bar.addWidget(self._logy)
        bar.addStretch(1)
        self._stat = QtWidgets.QLabel("")
        self._stat.setStyleSheet("color:#aaa;font-size:10px")
        bar.addWidget(self._stat)
        self._toolbar_layout = bar   # exposed for external widget insertion
        layout.addLayout(bar)

        self._plot = pg.PlotWidget(background="k")
        self._plot.setLabel("left", "Mean intensity")
        self._plot.setLabel("bottom", "R (px)")
        self._plot.showGrid(x=True, y=True, alpha=0.2)
        self._plot.setLimits(yMin=-1000)
        self._band_lo = pg.PlotDataItem([], [], pen=None)
        self._band_hi = pg.PlotDataItem([], [], pen=None)
        self._band = pg.FillBetweenItem(self._band_lo, self._band_hi,
                                        brush=pg.mkBrush(136, 204, 255, 60))
        self._band.setVisible(False)
        self._plot.addItem(self._band)
        self._curve = self._plot.plot([], [], pen=pg.mkPen("#88ccff", width=2))
        self._ring_lines: list = []
        layout.addWidget(self._plot, stretch=1)

        # Click-to-pick a radius (drawn on the image by the caller).
        self._pick_line = None
        self._plot.scene().sigMouseClicked.connect(self._on_plot_clicked)

        self._r_px = self._prof = self._sigma = None
        self._wl = self._lsd = self._px = None
        self._ring_radii_px: list = []
        self._ring_lsd = self._ring_px = self._ring_wl = None

    def set_profile(self, r_px, profile, *, sigma=None, wavelength_A=None,
                    lsd_um=None, px_um=None):
        self._r_px   = np.asarray(r_px)
        self._prof   = np.asarray(profile)
        self._sigma  = np.asarray(sigma) if sigma is not None else None
        self._wl     = wavelength_A
        self._lsd    = lsd_um
        self._px     = px_um
        self._replot()

    def set_ring_markers(self, radii_px, lsd_um=None, px_um=None, wl=None):
        self._ring_radii_px = list(radii_px)
        self._ring_lsd = lsd_um
        self._ring_px  = px_um
        self._ring_wl  = wl
        self._replot()

    def _r_to_x(self, r_px, idx, lsd, px, wl):
        if idx == 0 or lsd is None:
            return r_px
        two_theta = math.atan(r_px * px / lsd)
        if idx == 1:
            return math.degrees(two_theta)
        if idx == 2 and wl:
            return 4 * math.pi * math.sin(two_theta / 2) / wl
        return r_px

    def _replot(self):
        if self._r_px is None:
            return
        idx = self._xaxis.currentIndex()
        if idx == 0 or self._lsd is None:
            x = self._r_px
            self._plot.setLabel("bottom", "R (px)")
        else:
            x = np.array([self._r_to_x(r, idx, self._lsd, self._px, self._wl)
                          for r in self._r_px])
            self._plot.setLabel("bottom", ["R (px)", "2θ (°)", "Q (Å⁻¹)"][idx])
        y = self._prof
        log = self._logy.isChecked()
        if log:
            y = np.where(y > 0, np.log10(np.maximum(y, 1e-30)), np.nan)
            self._plot.setLabel("left", "log₁₀(intensity)")
            self._plot.setLimits(yMin=1)
        else:
            self._plot.setLabel("left", "Mean intensity")
            self._plot.setLimits(yMin=-1000)
        self._curve.setData(x, y)

        # Uncertainty band (linear scale only)
        if self._sigma is not None and not log:
            self._band_lo.setData(x, self._prof - self._sigma)
            self._band_hi.setData(x, self._prof + self._sigma)
            self._band.setCurves(self._band_lo, self._band_hi)
            self._band.setVisible(True)
        else:
            self._band.setVisible(False)

        fin = y[np.isfinite(y)]
        if fin.size:
            ymin = float(min(-1000 if not log else 1, fin.min()))
            ymax = float(fin.max()) * 1.05
            self._plot.setYRange(ymin, ymax, padding=0)
        x_arr = np.asarray(x)
        if x_arr.size:
            self._plot.setXRange(float(x_arr.min()), float(x_arr.max()), padding=0.02)
        self._stat.setText(f"{len(self._r_px)} bins | max={np.nanmax(self._prof):.1f}")

        # Ring markers redrawn LAST (after setXRange) so they always appear
        for ln in self._ring_lines:
            self._plot.removeItem(ln)
        self._ring_lines.clear()
        if self._ring_radii_px:
            pen = pg.mkPen("#f0c060", width=1.5, style=QtCore.Qt.DotLine)
            lsd = self._ring_lsd or self._lsd
            px  = self._ring_px  or self._px
            wl  = self._ring_wl  or self._wl
            for r in self._ring_radii_px:
                x_pos = self._r_to_x(r, idx, lsd, px, wl)
                if x_pos is None:
                    continue
                ln = pg.InfiniteLine(pos=x_pos, angle=90, pen=pen, movable=False)
                self._plot.addItem(ln)
                self._ring_lines.append(ln)

    def _x_to_r(self, x, idx, lsd, px, wl):
        """Inverse of _r_to_x: current-axis value → radius in px (None if invalid)."""
        if idx == 0 or lsd is None or px in (None, 0):
            return x
        if idx == 1:                                    # 2θ (deg)
            return math.tan(math.radians(x)) * lsd / px
        if idx == 2 and wl:                             # Q (Å⁻¹)
            s = x * wl / (4 * math.pi)
            if abs(s) >= 1.0:
                return None
            two_theta = 2 * math.asin(s)
            return math.tan(two_theta) * lsd / px
        return x

    def _on_plot_clicked(self, event):
        if event.button() != QtCore.Qt.LeftButton:
            return
        vb = self._plot.getPlotItem().getViewBox()
        if not vb.sceneBoundingRect().contains(event.scenePos()):
            return
        x = vb.mapSceneToView(event.scenePos()).x()
        r = self._x_to_r(x, self._xaxis.currentIndex(), self._lsd, self._px, self._wl)
        if r is None or r <= 0:
            return
        if self._pick_line is None:
            self._pick_line = pg.InfiniteLine(
                angle=90, movable=False, pen=pg.mkPen("#ff30ff", width=1.6))
            self._plot.addItem(self._pick_line)
        self._pick_line.setPos(x)
        self.radiusClicked.emit(float(r))

    def _clear_pick_line(self, *_):
        if self._pick_line is not None:
            self._plot.removeItem(self._pick_line)
            self._pick_line = None


# ═════════════════════════════════════════════════════════════════════════════
#  ResidualBarChart  (NEW — per-ring radial residual after calibration)
# ═════════════════════════════════════════════════════════════════════════════

class ResidualBarChart(QtWidgets.QWidget):
    """Bar chart of Δr = r_observed − r_predicted (px) for each predicted ring.

    Self-contained: the observed radius is the local profile peak within a window
    around each predicted radius.  No dependence on pipeline internals, so it
    works identically for every calibration pipeline.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(2)
        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(QtWidgets.QLabel("Per-ring radial residual  Δr = r_obs − r_pred"))
        bar.addStretch(1)
        self._stat = QtWidgets.QLabel("")
        self._stat.setStyleSheet("color:#aaa;font-size:10px")
        bar.addWidget(self._stat)
        layout.addLayout(bar)

        self._plot = pg.PlotWidget(background="k")
        self._plot.setLabel("left", "Δr (px)")
        self._plot.setLabel("bottom", "ring index")
        self._plot.showGrid(x=True, y=True, alpha=0.2)
        self._zero = pg.InfiniteLine(pos=0, angle=0, pen=pg.mkPen("#888", width=1))
        self._plot.addItem(self._zero)
        self._bars = None
        layout.addWidget(self._plot, stretch=1)

    def set_data(self, r_axis_px, profile, ring_radii_px, window_px: float = 8.0):
        r_axis = np.asarray(r_axis_px, dtype=float)
        prof   = np.asarray(profile, dtype=float)
        if self._bars is not None:
            self._plot.removeItem(self._bars)
            self._bars = None
        if r_axis.size == 0 or not ring_radii_px:
            self._stat.setText("no data")
            return

        idxs, resid = [], []
        for k, r_pred in enumerate(ring_radii_px):
            sel = np.abs(r_axis - r_pred) <= window_px
            if not sel.any():
                continue
            local_r = r_axis[sel]
            local_i = prof[sel]
            if not np.isfinite(local_i).any():
                continue
            r_obs = float(local_r[int(np.nanargmax(local_i))])
            idxs.append(k)
            resid.append(r_obs - float(r_pred))
        if not idxs:
            self._stat.setText("no rings matched the profile")
            return

        x = np.array(idxs, dtype=float)
        h = np.array(resid, dtype=float)
        self._bars = pg.BarGraphItem(x=x, height=h, width=0.6,
                                     brush=pg.mkBrush("#5aa0e0"))
        self._plot.addItem(self._bars)
        rms = float(np.sqrt(np.mean(h ** 2)))
        self._stat.setText(f"{len(h)} rings | RMS Δr = {rms:.3f} px")


# ═════════════════════════════════════════════════════════════════════════════
#  DistortionTable  (NEW — read-only 15-coefficient grid)
# ═════════════════════════════════════════════════════════════════════════════

class DistortionTable(QtWidgets.QTableWidget):
    """Compact read-only display of the 15 distortion coefficients."""

    def __init__(self, parent=None):
        super().__init__(len(DISTORTION_NAMES), 2, parent)
        self.setHorizontalHeaderLabels(["coeff", "value"])
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.horizontalHeader().setStretchLastSection(True)
        self.setColumnWidth(0, 70)
        self.setFixedHeight(150)
        for i, name in enumerate(DISTORTION_NAMES):
            self.setItem(i, 0, QtWidgets.QTableWidgetItem(name))
            self.setItem(i, 1, QtWidgets.QTableWidgetItem("—"))

    def set_distortion(self, distortion: dict):
        for i, name in enumerate(DISTORTION_NAMES):
            val = distortion.get(name)
            txt = f"{val:.6g}" if val is not None else "—"
            self.item(i, 1).setText(txt)


# ═════════════════════════════════════════════════════════════════════════════
#  CorrectionFlagsWidget  (NEW — reusable physics-corrections panel)
# ═════════════════════════════════════════════════════════════════════════════

class CorrectionFlagsWidget(QtWidgets.QGroupBox):
    """Polarization + solid-angle correction toggles with sub-controls."""

    def __init__(self, parent=None):
        super().__init__("Physics corrections", parent)
        form = QtWidgets.QFormLayout(self); form.setSpacing(4)

        self.polar_check = QtWidgets.QCheckBox("Polarization")
        self.polar_check.setToolTip(
            "Apply the polarization correction (synchrotron horizontal plane).")
        self.pol_fraction = _fspin(0.0, 1.0, 3, 0.99)
        self.pol_fraction.setFixedWidth(80)
        self.pol_plane = _fspin(-180.0, 180.0, 1, 0.0, "°")
        self.pol_plane.setFixedWidth(80)
        form.addRow(self.polar_check)
        form.addRow(_twocol("frac:", self.pol_fraction, "plane η:", self.pol_plane))

        self.solid_check = QtWidgets.QCheckBox("Solid-angle (tilt-aware)")
        self.solid_check.setToolTip(
            "Divide by the per-pixel solid angle (accounts for detector tilt).")
        form.addRow(self.solid_check)

        for w in (self.pol_fraction, self.pol_plane):
            w.setEnabled(False)
        self.polar_check.toggled.connect(self.pol_fraction.setEnabled)
        self.polar_check.toggled.connect(self.pol_plane.setEnabled)

    def any_enabled(self) -> bool:
        return self.polar_check.isChecked() or self.solid_check.isChecked()

    def build_corrections(self):
        """Return (polarization, solid_angle) correction objects or None each."""
        pol = sa = None
        if self.polar_check.isChecked():
            from midas_integrate_v2 import PolarizationCorrection
            pol = PolarizationCorrection(
                pol_fraction=self.pol_fraction.value(),
                pol_plane_eta_deg=self.pol_plane.value())
        if self.solid_check.isChecked():
            from midas_integrate_v2 import SolidAngleCorrection
            sa = SolidAngleCorrection()
        return pol, sa


class LossCurveViewer(QtWidgets.QWidget):
    """Live loss-vs-iteration plot for optimisation tabs (refinement, learnable, PDF)."""

    def __init__(self, parent=None, ylabel="loss"):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(2)
        self._plot = pg.PlotWidget(background="k")
        self._plot.setLabel("left", ylabel)
        self._plot.setLabel("bottom", "iteration")
        self._plot.showGrid(x=True, y=True, alpha=0.2)
        self._curve = self._plot.plot([], [], pen=pg.mkPen("#f0a030", width=2),
                                      symbol="o", symbolSize=4, symbolBrush="#f0a030")
        layout.addWidget(self._plot)
        self._xs: list = []
        self._ys: list = []

    def reset(self):
        self._xs.clear(); self._ys.clear()
        self._curve.setData([], [])

    def add_point(self, it: int, loss: float):
        if loss != loss:  # NaN guard
            return
        self._xs.append(it); self._ys.append(loss)
        self._curve.setData(self._xs, self._ys)


class WaterfallViewer(QtWidgets.QWidget):
    """2-D waterfall of 1-D profiles: x = R (px), y = frame index, colour = intensity.

    Rows are appended incrementally as frames are integrated, so the user watches
    every frame's radial integration stack up live.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(2)
        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(QtWidgets.QLabel("Waterfall (all frames)"))
        bar.addWidget(QtWidgets.QLabel("  cmap:"))
        self._cmap = QtWidgets.QComboBox(); self._cmap.addItems(COLORMAPS); self._cmap.setFixedWidth(90)
        self._cmap.currentTextChanged.connect(self._apply_cmap)
        bar.addWidget(self._cmap)
        self._log = QtWidgets.QCheckBox("Log"); self._log.setChecked(True)
        self._log.toggled.connect(self._redraw)
        bar.addWidget(self._log)
        bar.addStretch(1)
        self._stat = QtWidgets.QLabel(""); self._stat.setStyleSheet("color:#aaa;font-size:10px")
        bar.addWidget(self._stat)
        layout.addLayout(bar)

        self._plot = pg.PlotWidget(background="k")
        self._plot.setLabel("left", "frame #")
        self._plot.setLabel("bottom", "R (px)")
        self._img = pg.ImageItem()
        self._plot.addItem(self._img)
        layout.addWidget(self._plot, stretch=1)

        self._rows: list = []
        self._r_axis = None
        self._apply_cmap(COLORMAPS[0])

    def reset(self, r_axis):
        """Start a new scan; r_axis is the radial bin-centre array (px)."""
        self._rows = []
        self._r_axis = np.asarray(r_axis, dtype=float)
        self._img.clear()
        self._stat.setText("")

    def add_profile(self, profile):
        """Append one frame's 1-D profile as the next waterfall row."""
        self._rows.append(np.asarray(profile, dtype=float))
        self._redraw()
        self._stat.setText(f"{len(self._rows)} frames")

    def _redraw(self):
        if not self._rows or self._r_axis is None:
            return
        arr = np.vstack(self._rows)                       # (n_frames, n_r)
        if self._log.isChecked():
            disp = np.log10(np.clip(arr, 1e-6, None))
        else:
            disp = arr
        fin = disp[np.isfinite(disp)]
        lo, hi = (float(np.percentile(fin, 1)), float(np.percentile(fin, 99))) if fin.size else (0.0, 1.0)
        if hi <= lo:
            hi = lo + 1.0
        # ImageItem (col-major): pass (n_r, n_frames) so x=R, y=frame
        self._img.setImage(disp.T, autoLevels=False, levels=(lo, hi))
        r0, r1 = float(self._r_axis[0]), float(self._r_axis[-1])
        self._img.setRect(QtCore.QRectF(r0, 0.0, r1 - r0, arr.shape[0]))

    def _apply_cmap(self, name: str):
        try:
            cmap = pg.colormap.get(name)
        except Exception:
            try:
                cmap = pg.colormap.getFromMatplotlib(name)
            except Exception:
                return
        self._img.setLookupTable(cmap.getLookupTable(0.0, 1.0, 256))


def _frame_color(i: int) -> tuple:
    """Map a frame index to an RGB colour using the golden-angle hue sequence.

    Consecutive frames get maximally separated hues so individual profiles
    remain distinguishable even in a dense stack.
    """
    hue = (i * 137.508) % 360.0   # golden angle → maximum hue separation
    # HSV → RGB (saturation=0.75, value=1.0)
    h = hue / 60.0; s = 0.75; v = 1.0
    hi = int(h) % 6; f = h - int(h)
    p_ = v * (1 - s); q_ = v * (1 - s * f); t_ = v * (1 - s * (1 - f))
    r, g, b = [(v, t_, p_), (q_, v, p_), (p_, v, t_),
                (p_, q_, v), (t_, p_, v), (v, p_, q_)][hi]
    return (int(r * 255), int(g * 255), int(b * 255))


class StackedProfileViewer(QtWidgets.QWidget):
    """All batch-integration profiles drawn with a vertical Y offset.

    Each frame gets a distinct colour from the golden-angle hue sequence so
    they remain identifiable in a dense stack.  The spacing spinbox (default
    500 counts) shifts each successive frame upward; set it to 0 to overlay
    all frames for a direct comparison.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(2)

        # Toolbar
        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(QtWidgets.QLabel("Stacked profiles  spacing:"))
        self._spacing = _NoScrollDoubleSpinBox()
        self._spacing.setRange(0.0, 1e9)
        self._spacing.setValue(500.0)
        self._spacing.setDecimals(0)
        self._spacing.setSingleStep(100.0)
        self._spacing.setSuffix("  cts")
        self._spacing.setFixedWidth(100)
        self._spacing.valueChanged.connect(self._restack)
        bar.addWidget(self._spacing)
        bar.addStretch(1)
        self._stat = QtWidgets.QLabel("")
        self._stat.setStyleSheet("color:#aaa;font-size:10px")
        bar.addWidget(self._stat)
        layout.addLayout(bar)

        self._plot = pg.PlotWidget(background="#111111")
        self._plot.showGrid(x=True, y=True, alpha=0.15)
        self._plot.setLabel("bottom", "R (px)")
        self._plot.setLabel("left", "Intensity + offset")
        layout.addWidget(self._plot, stretch=1)

        self._r_axes: list = []
        self._profiles: list = []
        self._curves: list = []

    # ── public API ───────────────────────────────────────────────────

    def reset(self, r_axis=None):
        """Clear all stored profiles and curves."""
        self._r_axes.clear()
        self._profiles.clear()
        for c in self._curves:
            self._plot.removeItem(c)
        self._curves.clear()
        self._plot.clear()
        self._stat.setText("")

    def add_profile(self, r_axis, profile):
        """Append one frame's 1-D profile; draws it at its stacked offset."""
        r = np.asarray(r_axis, dtype=float)
        p = np.asarray(profile, dtype=float)
        i = len(self._profiles)
        self._r_axes.append(r)
        self._profiles.append(p)
        offset = i * float(self._spacing.value())
        curve = self._plot.plot(r, p + offset,
                                pen=pg.mkPen(_frame_color(i), width=1.0))
        self._curves.append(curve)
        n = len(self._profiles)
        self._stat.setText(f"{n} frame{'s' if n != 1 else ''}")

    # ── internal ─────────────────────────────────────────────────────

    def _restack(self, _=None):
        """Re-apply Y offsets to all existing curves when spacing changes."""
        spacing = float(self._spacing.value())
        for i, (curve, r, p) in enumerate(
                zip(self._curves, self._r_axes, self._profiles)):
            curve.setData(r, p + i * spacing)
        if self._curves:
            self._plot.autoRange()


class LogPanel(QtWidgets.QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(1000)
        self.setFont(QtGui.QFont("Monospace", 9))
        self.setMaximumHeight(120)

    def append(self, line: str):
        self.appendPlainText(line)
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
