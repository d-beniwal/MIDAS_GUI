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

from midas_gui.constants import COLORMAPS, DISTORTION_NAMES, DEFAULT_COLORMAP

# Default colormap: the configured one if it's a known option, else the first.
_DEFAULT_CMAP = DEFAULT_COLORMAP if DEFAULT_COLORMAP in COLORMAPS else COLORMAPS[0]
from midas_gui.helpers import (_NoScrollSpinBox, _NoScrollDoubleSpinBox, _fspin, _twocol,
                               _browse, is_h5, list_h5_datasets, _NoScrollComboBox,
                               _load_image, _collect_frame_paths, apply_field_corrections)
from midas_gui import style as S


def _mono_font(size: int) -> QtGui.QFont:
    """A fixed-width font at ``size`` pt using the real-family stack in style.py.

    Naming concrete families (Menlo/Consolas/…) instead of ``QFont("Monospace")``
    avoids Qt scanning and building font-family aliases at startup (the
    "qt.qpa.fonts: Populating font family aliases" warning) on macOS/Windows."""
    f = QtGui.QFont()
    try:
        f.setFamilies(S.MONO_FAMILIES)
    except Exception:
        f.setFamily(S.MONO_FAMILIES[0])
    f.setStyleHint(QtGui.QFont.Monospace)
    f.setPointSize(size)
    return f


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
        self._cmap = _NoScrollComboBox()
        self._cmap.addItems(COLORMAPS)
        self._cmap.setCurrentText(_DEFAULT_CMAP)
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
            f"color:#dddddd; background:#1a1a1a; font-family:{S.MONO_CSS};"
            "font-size:12px; padding:2px 6px; border-top:1px solid #444;")
        layout.addWidget(self._coord_bar)

        self._data: Optional[np.ndarray] = None
        self._set_cmap(_DEFAULT_CMAP)

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
        self._xaxis = _NoScrollComboBox()
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


class FieldSelector(QtWidgets.QGroupBox):
    """Compact reusable dark / bright / background field picker.

    A checkable group; its body is hidden while unchecked so three of these stay
    compact.  Browsing a file or folder (⋯ menu) auto-computes the field: a single
    file, a folder / *.tif glob, or an HDF5 dataset averaged over an index range
    that is clamped to the number of frames available.  The bright variant adds a
    divide / subtract mode combo.  ``get_field()`` → computed field (or None);
    ``get_mode()`` → "divide" | "subtract".
    """
    fieldReady = QtCore.pyqtSignal()

    def __init__(self, title, parent=None, *, with_mode=False,
                 default_dataset="exchange/data"):
        super().__init__(title, parent)
        self.setCheckable(True)
        self.setChecked(False)
        self._with_mode = with_mode
        self._field = None
        self._worker = None

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(6, 2, 6, 4); outer.setSpacing(2)
        self._body = QtWidgets.QWidget()
        self._body.setVisible(False)                       # collapsed until enabled
        self.toggled.connect(self._body.setVisible)
        outer.addWidget(self._body)
        v = QtWidgets.QVBoxLayout(self._body)
        v.setContentsMargins(0, 0, 0, 0); v.setSpacing(3)

        # Path + browse (file/folder popup menu)
        self._path_ed = QtWidgets.QLineEdit()
        self._path_ed.setPlaceholderText("file / folder / .h5")
        self._path_ed.textChanged.connect(self._on_path_changed)
        self._path_ed.editingFinished.connect(self._update_frame_limit)
        browse = QtWidgets.QToolButton()
        browse.setText("⋯"); browse.setFixedWidth(28)
        browse.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        menu = QtWidgets.QMenu(browse)
        menu.addAction("File…", self._browse_file)
        menu.addAction("Folder…", self._browse_folder)
        browse.setMenu(menu)
        pr = QtWidgets.QHBoxLayout(); pr.setSpacing(4)
        pr.addWidget(self._path_ed); pr.addWidget(browse)
        v.addLayout(pr)

        # HDF5 dataset dropdown (row hidden unless an HDF5 path is selected)
        self._ds_row = QtWidgets.QWidget()
        dr = QtWidgets.QHBoxLayout(self._ds_row); dr.setContentsMargins(0, 0, 0, 0); dr.setSpacing(4)
        self._ds_combo = _NoScrollComboBox()
        self._ds_combo.setEditable(True); self._ds_combo.setEditText(default_dataset)
        self._ds_combo.currentIndexChanged.connect(self._update_frame_limit)
        dr.addWidget(QtWidgets.QLabel("Dataset:")); dr.addWidget(self._ds_combo, 1)
        self._ds_row.setVisible(False)
        v.addWidget(self._ds_row)

        # Index range (clamped to available frames) + optional mode, on one row
        self._start = _NoScrollSpinBox(); self._start.setRange(0, 0); self._start.setFixedWidth(50)
        self._end = _NoScrollSpinBox(); self._end.setRange(0, 0); self._end.setFixedWidth(50)
        self._end.setToolTip("Last frame index to average (inclusive).")
        self._nfr_lbl = QtWidgets.QLabel("")
        self._nfr_lbl.setStyleSheet("color:#9a9a9a;font-size:10px")
        ir = QtWidgets.QHBoxLayout(); ir.setSpacing(4)
        ir.addWidget(QtWidgets.QLabel("avg")); ir.addWidget(self._start)
        ir.addWidget(QtWidgets.QLabel("–")); ir.addWidget(self._end)
        ir.addWidget(self._nfr_lbl)
        if with_mode:
            self._mode = _NoScrollComboBox()
            self._mode.addItems(["Flat-field divide", "Subtract"])
            self._mode.setFixedWidth(104)
            ir.addStretch(1); ir.addWidget(self._mode)
        else:
            self._mode = None
            ir.addStretch(1)
        v.addLayout(ir)

        # Compute + status
        self._compute_btn = QtWidgets.QPushButton("Compute field")
        self._compute_btn.clicked.connect(self._compute)
        v.addWidget(self._compute_btn)
        self._status = QtWidgets.QLabel("Not computed.")
        self._status.setStyleSheet("color:#9a9a9a;font-size:10px"); self._status.setWordWrap(True)
        v.addWidget(self._status)

    # ── helpers ───────────────────────────────────────────────────
    def _kind(self) -> str:
        from pathlib import Path
        raw = self._path_ed.text().strip()
        if Path(raw).is_dir() or any(c in raw for c in "*?"):
            return "folder"
        if is_h5(raw):
            return "hdf5"
        return "file"

    def _dataset(self) -> str:
        return self._ds_combo.currentText().split("   ")[0].strip() or "exchange/data"

    def _browse_file(self):
        p = _browse(self, f"Select {self.title()} file",
                    "Data (*.tif *.tiff *.h5 *.hdf5 *.hdf *.nxs *.ge*);;All (*)")
        if p:
            self._path_ed.setText(p); self._update_frame_limit(); self._compute()

    def _browse_folder(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, f"Select {self.title()} folder")
        if d:
            self._path_ed.setText(d); self._update_frame_limit(); self._compute()

    def _on_path_changed(self, p: str):
        from pathlib import Path
        h5 = is_h5(p)
        self._ds_row.setVisible(h5)
        if h5 and Path(p).exists():
            try:
                items = list_h5_datasets(p)
            except Exception:
                items = []
            if items:
                keep = self._ds_combo.currentText().strip()
                self._ds_combo.blockSignals(True); self._ds_combo.clear()
                for name, shape in items:
                    self._ds_combo.addItem(f"{name}   {tuple(shape)}", name)
                idx = next((i for i in range(self._ds_combo.count())
                            if self._ds_combo.itemData(i) == keep), -1)
                if idx < 0:
                    idx = next((i for i, (n, s) in enumerate(items) if len(s) >= 3), 0)
                self._ds_combo.setCurrentIndex(idx)
                self._ds_combo.blockSignals(False)
        self._update_frame_limit()

    def _count_frames(self) -> int:
        """Number of frames available in the current source (0 if unknown)."""
        from pathlib import Path
        raw = self._path_ed.text().strip()
        if not raw:
            return 0
        kind = self._kind()
        try:
            if kind == "hdf5":
                import h5py
                if not Path(raw).exists():
                    return 0
                with h5py.File(raw, "r") as f:
                    d = f[self._dataset()]
                    return int(d.shape[0]) if d.ndim >= 3 else 1
            if kind == "folder":
                from midas_gui.helpers import _collect_frame_paths
                return len(_collect_frame_paths(raw))
            if not Path(raw).exists():
                return 0
            if raw.lower().endswith((".tif", ".tiff")):
                import tifffile
                with tifffile.TiffFile(raw) as tf:
                    return len(tf.pages)
            return 1
        except Exception:
            return 0

    def _update_frame_limit(self):
        """Clamp the index spinboxes to the frames actually available."""
        n = self._count_frames()
        if n <= 0:
            self._nfr_lbl.setText("")
            return
        hi = n - 1
        self._nfr_lbl.setText(f"/ {hi}")
        for sp in (self._start, self._end):
            sp.blockSignals(True); sp.setMaximum(hi); sp.blockSignals(False)
        # default the end to the last frame (average the whole stack)
        if self._end.value() == 0 or self._end.value() > hi:
            self._end.blockSignals(True); self._end.setValue(hi); self._end.blockSignals(False)
        if self._start.value() > hi:
            self._start.setValue(hi)

    def _compute(self):
        raw = self._path_ed.text().strip()
        if not raw:
            self._status.setText("Enter a path first."); return
        from midas_gui.workers import FieldAverageWorker
        self._compute_btn.setEnabled(False)
        self._status.setText("Computing…")
        self._worker = FieldAverageWorker(
            self._kind(), raw, self._dataset(),
            self._start.value(), self._end.value(), parent=self)
        self._worker.finished.connect(self._on_computed)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_computed(self, field):
        self._field = field
        self._compute_btn.setEnabled(True)
        self._status.setText(f"Computed — {field.shape}  "
                             f"[{float(field.min()):.4g}, {float(field.max()):.4g}]")
        self.fieldReady.emit()

    def _on_failed(self, msg):
        self._field = None
        self._compute_btn.setEnabled(True)
        self._status.setText(f"Failed: {msg.strip().splitlines()[-1][:120]}")

    # ── public API ────────────────────────────────────────────────
    def is_enabled(self) -> bool:
        return self.isChecked()

    def has_pending(self) -> bool:
        return self.isChecked() and self._field is None

    def get_field(self):
        return self._field if self.isChecked() else None

    def get_mode(self) -> str:
        if self._mode is None:
            return "divide"
        return "divide" if self._mode.currentIndex() == 0 else "subtract"


class MaskSelector(QtWidgets.QGroupBox):
    """Multiple mask sources unioned into one composite mask.

    Rows are mask files/folders plus an optional auto-managed "Tab 1 mask" row
    (set via :meth:`set_tab1_mask`).  ``composite_mask()`` OR's every source
    (each loaded → ``!= 0``; a folder OR's all its frames).  Masked pixels are the
    ones a caller should zero / ignore.
    """
    maskChanged = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("Mask", parent)
        self._sources: list = []          # dicts: {kind, path, mask(cache), row(QWidget)}
        self._tab1_mask = None
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(6, 4, 6, 6); v.setSpacing(4)
        self._list = QtWidgets.QVBoxLayout(); self._list.setSpacing(2)
        v.addLayout(self._list)
        row = QtWidgets.QHBoxLayout(); row.setSpacing(4)
        bf = QtWidgets.QPushButton("Add file…"); bf.clicked.connect(self._add_file)
        bd = QtWidgets.QPushButton("Add folder…"); bd.clicked.connect(self._add_folder)
        row.addWidget(bf); row.addWidget(bd)
        v.addLayout(row)
        self._status = QtWidgets.QLabel("No mask.")
        self._status.setStyleSheet("color:#9a9a9a;font-size:10px"); self._status.setWordWrap(True)
        v.addWidget(self._status)

    def _add_row(self, entry, label):
        rw = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(rw); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(4)
        lbl = QtWidgets.QLabel(label); lbl.setStyleSheet("font-size:10px")
        x = QtWidgets.QToolButton(); x.setText("✕"); x.setFixedWidth(22)
        x.clicked.connect(lambda: self._remove(entry))
        h.addWidget(lbl, 1); h.addWidget(x)
        entry["row"] = rw
        self._list.addWidget(rw)

    def _remove(self, entry):
        if entry in self._sources:
            self._list.removeWidget(entry["row"]); entry["row"].deleteLater()
            self._sources.remove(entry)
            if entry["kind"] == "tab1":
                self._tab1_mask = None
            self._refresh(); self.maskChanged.emit()

    def _add_source(self, kind, path):
        from pathlib import Path
        entry = {"kind": kind, "path": path, "mask": None, "row": None}
        self._add_row(entry, f"{kind}: {Path(path).name}")
        self._sources.append(entry)
        self._refresh(); self.maskChanged.emit()

    def _add_file(self):
        p = _browse(self, "Add mask file", "Images (*.tif *.tiff *.h5 *.hdf5);;All (*)")
        if p:
            self._add_source("file", p)

    def _add_folder(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Add mask folder")
        if d:
            self._add_source("folder", d)

    def set_tab1_mask(self, mask):
        """Add / update / remove the auto-managed Tab-1 mask row."""
        self._tab1_mask = None if mask is None else (np.asarray(mask) != 0)
        existing = next((e for e in self._sources if e["kind"] == "tab1"), None)
        if self._tab1_mask is None:
            if existing:
                self._remove(existing)
            return
        if existing is None:
            entry = {"kind": "tab1", "path": None, "mask": None, "row": None}
            n = int(self._tab1_mask.sum())
            self._add_row(entry, f"Tab 1 mask ({n:,} px)")
            self._sources.insert(0, entry)
        self._refresh(); self.maskChanged.emit()

    def _load_source(self, entry):
        if entry["kind"] == "tab1":
            return self._tab1_mask
        if entry["mask"] is not None:
            return entry["mask"]
        try:
            if entry["kind"] == "folder":
                acc = None
                for p in _collect_frame_paths(entry["path"]):
                    a = _load_image(p); a = a[0] if a.ndim == 3 else a
                    b = (a != 0)
                    acc = b if acc is None else (acc | b)
                m = acc
            else:
                a = _load_image(entry["path"]); a = a[0] if a.ndim == 3 else a
                m = (a != 0)
            entry["mask"] = m
            return m
        except Exception:
            return None

    def composite_mask(self):
        """uint8 union of all sources (1 = masked), or None if empty."""
        out = None
        for e in self._sources:
            m = self._load_source(e)
            if m is None:
                continue
            m = np.asarray(m) != 0
            if out is None:
                out = m
            elif out.shape == m.shape:
                out = out | m
        return out.astype(np.uint8) if out is not None else None

    def _refresh(self):
        n = len(self._sources)
        self._status.setText(f"{n} mask source(s)" if n else "No mask.")


class IntensityStatsPanel(QtWidgets.QGroupBox):
    """Compact intensity-distribution readout + histogram for the Data Viewer.

    Display-only: the owning tab feeds it the (already corrected + masked) pixel
    values via :meth:`set_data`.  A scope selector (Current frame / All frames)
    emits :data:`scopeChanged` so the tab can recompute the right pixel set.
    """
    scopeChanged = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("Intensity statistics", parent)
        v = QtWidgets.QVBoxLayout(self); v.setContentsMargins(6, 3, 6, 4); v.setSpacing(3)

        top = QtWidgets.QHBoxLayout(); top.setSpacing(6)
        self._scope = _NoScrollComboBox()
        self._scope.addItem("Current frame", "current")
        self._scope.addItem("All frames", "all")
        self._scope.setToolTip("Statistics for the selected frame, or combined over "
                               "all frames in the stack/folder.")
        self._scope.currentIndexChanged.connect(lambda _=0: self.scopeChanged.emit())
        top.addWidget(self._scope, 1)
        self._logchk = QtWidgets.QCheckBox("log y"); self._logchk.setChecked(True)
        self._logchk.toggled.connect(self._redraw_hist)
        top.addWidget(self._logchk)
        v.addLayout(top)

        self._plot = pg.PlotWidget(background="#2b2e35")
        self._plot.setMaximumHeight(130); self._plot.setMinimumHeight(90)
        self._plot.setLabel("bottom", "intensity", **{"color": "#d0d0d0", "font-size": "9pt"})
        self._plot.setLabel("left", "log(count+1)", **{"color": "#d0d0d0", "font-size": "9pt"})
        for ax in ("bottom", "left"):
            self._plot.getAxis(ax).setTextPen("#c8c8c8")
            self._plot.getAxis(ax).setPen("#8a8a8a")
        self._curve = self._plot.plot(
            [], [], stepMode="center", fillLevel=0,
            brush=(90, 140, 220, 150), pen=pg.mkPen("#6ea8ff"))
        v.addWidget(self._plot)

        self._text = QtWidgets.QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setFont(_mono_font(8))
        self._text.setFixedHeight(120)
        self._text.setStyleSheet(
            "QPlainTextEdit { background:#23252b; color:#d6d6d6; border:1px solid #444; }")
        v.addWidget(self._text)
        self._hist = None

    def scope(self) -> str:
        return self._scope.currentData()

    def set_scope_enabled(self, on: bool):
        self._scope.setEnabled(on)

    def set_data(self, values, scope: str = ""):
        vals = np.asarray(values, dtype=np.float64).ravel()
        vals = vals[np.isfinite(vals)]
        if vals.size == 0:
            self._text.setPlainText(f"{scope}\n(no pixels)")
            self._hist = None; self._curve.setData([], [])
            return
        n = vals.size
        p70, p90, p99, p999, p9999 = np.percentile(vals, [70, 90, 99, 99.9, 99.99])

        def g(x):
            return f"{x:.6g}"

        def cnt(p):
            return int(np.count_nonzero(vals > p))
        lines = [
            scope,
            f"N      = {n:,}",
            f"p70    = {g(p70):<10} (>: {cnt(p70):,})",
            f"p90    = {g(p90):<10} (>: {cnt(p90):,})",
            f"p99    = {g(p99):<10} (>: {cnt(p99):,})",
            f"p99.9  = {g(p999):<10} (>: {cnt(p999):,})",
            f"p99.99 = {g(p9999):<10} (>: {cnt(p9999):,})",
        ]
        self._text.setPlainText("\n".join(lines))

        # Histogram over the FULL intensity range so high-intensity pixels appear.
        vmin, vmax = float(vals.min()), float(vals.max())
        if vmax <= vmin:
            vmax = vmin + 1.0
        v_hist = vals
        if vals.size > 50_000_000:      # bound time only on very large stacks
            v_hist = vals[np.random.default_rng(0).integers(0, vals.size, 50_000_000)]
        counts, edges = np.histogram(v_hist, bins=256, range=(vmin, vmax))
        self._hist = (counts, edges)
        self._redraw_hist()

    def _redraw_hist(self, *_):
        if self._hist is None:
            self._curve.setData([], []); return
        counts, edges = self._hist
        y = counts.astype(float)
        log = self._logchk.isChecked()
        if log:
            y = np.log10(y + 1.0)
        self._curve.setData(edges, y)
        self._plot.setLabel("left", "log(count+1)" if log else "count",
                            **{"color": "#d0d0d0", "font-size": "9pt"})
        # Fixed lower-left corner (x=0, y=-2); rescale to (0..xmax, -2..ymax) on refresh.
        xmax = float(edges[-1]) if edges.size else 1.0
        ymax = float(y.max()) if y.size else 1.0
        ymax = ymax * 1.08 if ymax > 0 else 1.0
        vb = self._plot.getViewBox()
        vb.setLimits(xMin=0.0, yMin=-2.0)
        vb.setXRange(0.0, max(xmax, 1.0), padding=0)
        vb.setYRange(-2.0, ymax, padding=0)


class DataLoaderPanel(QtWidgets.QScrollArea):
    """Left-hand data-loading panel shared by Tabs 0/2/3/4.

    Selects the five inputs — Data, Dark, Bright, Background, Mask — each a single
    file / a folder / an HDF5 dataset (container dropdown).  ``mode`` tailors the
    Data card:
      - ``"stack"``  — frame navigator (Data Viewer);
      - ``"single"`` — a frame-index spin (Calibrate / Refinement);
      - ``"stream"`` — frame range + stride, no in-memory load (Batch).
    Dark/Bright/Background reuse :class:`FieldSelector`; Mask uses
    :class:`MaskSelector`.  ``corrected(frame)`` applies dark/bright/background.
    """
    dataChanged = QtCore.pyqtSignal()     # data loaded, or current frame changed
    fieldsChanged = QtCore.pyqtSignal()   # dark/bright/background/mask changed
    monitorToggled = QtCore.pyqtSignal(bool)  # MONITOR button toggled (stream mode)

    def __init__(self, parent=None, *, mode="single", data_dataset="exchange/data",
                 dark_dataset="exchange/data_dark"):
        super().__init__(parent)
        from midas_gui import style as S
        self._mode = mode
        self._stack = self._paths = self._h5 = None
        self._nframes = 0
        self._cur = None

        self.setWidgetResizable(True)
        inner = QtWidgets.QWidget(); self.setWidget(inner)
        lv = QtWidgets.QVBoxLayout(inner); lv.setContentsMargins(4, 4, 4, 4); lv.setSpacing(8)

        # Distinct background + accent right border so the data-loader panel stands
        # out from the middle parameters panel.
        self.setObjectName("dataLoaderPanel")
        inner.setObjectName("dataLoaderInner")
        self.viewport().setObjectName("dataLoaderViewport")
        self.setStyleSheet(
            f"#dataLoaderPanel {{ border: none; border-right: 2px solid {S.ACCENT}; }}"
            f"#dataLoaderViewport, #dataLoaderInner {{ background: #2b2e35; }}")

        # ── Data card ──
        card = S.make_card("Data")
        self._path_ed = QtWidgets.QLineEdit()
        self._path_ed.setPlaceholderText("file / folder / .h5")
        self._path_ed.textChanged.connect(self._on_path_changed)
        self._path_ed.returnPressed.connect(self._load)
        browse = QtWidgets.QToolButton(); browse.setText("⋯"); browse.setFixedWidth(28)
        browse.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        menu = QtWidgets.QMenu(browse)
        menu.addAction("File…", self._browse_file)
        menu.addAction("Folder…", self._browse_folder)
        browse.setMenu(menu)
        pr = QtWidgets.QHBoxLayout(); pr.setSpacing(4)
        pr.addWidget(self._path_ed); pr.addWidget(browse)
        card.body.addLayout(pr)

        self._ds_row = QtWidgets.QWidget()
        dr = QtWidgets.QHBoxLayout(self._ds_row); dr.setContentsMargins(0, 0, 0, 0); dr.setSpacing(4)
        self._ds_combo = _NoScrollComboBox(); self._ds_combo.setEditable(True)
        self._ds_combo.setEditText(data_dataset)
        self._ds_combo.currentIndexChanged.connect(lambda _=0: self._load() if self._nframes else None)
        dr.addWidget(QtWidgets.QLabel("Dataset:")); dr.addWidget(self._ds_combo, 1)
        self._ds_row.setVisible(False)
        card.body.addWidget(self._ds_row)

        # Mode-specific frame controls
        self._frame_spin = _NoScrollSpinBox(); self._frame_spin.setRange(0, 0)
        if mode == "stack":
            self._slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            self._prev_btn = QtWidgets.QPushButton("◀"); self._prev_btn.setFixedWidth(30)
            self._next_btn = QtWidgets.QPushButton("▶"); self._next_btn.setFixedWidth(30)
            self._nframes_lbl = QtWidgets.QLabel("/ 0")
            self._frame_spin.setFixedWidth(64)
            self._slider.valueChanged.connect(self._set_frame)
            self._frame_spin.valueChanged.connect(self._set_frame)
            self._prev_btn.clicked.connect(lambda: self._set_frame(self._frame_spin.value() - 1))
            self._next_btn.clicked.connect(lambda: self._set_frame(self._frame_spin.value() + 1))
            nav = QtWidgets.QHBoxLayout(); nav.setSpacing(4)
            nav.addWidget(self._prev_btn); nav.addWidget(self._slider, 1)
            nav.addWidget(self._frame_spin); nav.addWidget(self._nframes_lbl); nav.addWidget(self._next_btn)
            self._nav_row = QtWidgets.QWidget(); self._nav_row.setLayout(nav)
            self._nav_row.setEnabled(False)
            card.body.addWidget(self._nav_row)
        elif mode == "single":
            self._frame_spin.valueChanged.connect(self._set_frame)
            fr = QtWidgets.QHBoxLayout(); fr.setSpacing(4)
            fr.addWidget(QtWidgets.QLabel("Frame:")); fr.addWidget(self._frame_spin); fr.addStretch(1)
            card.body.addLayout(fr)
        else:  # stream
            self._fr_start = _NoScrollSpinBox(); self._fr_start.setRange(0, 999999); self._fr_start.setFixedWidth(64)
            self._fr_end = _NoScrollSpinBox(); self._fr_end.setRange(0, 999999); self._fr_end.setFixedWidth(64)
            self._fr_end.setToolTip("Last frame (exclusive). 0 = all frames.")
            self._fr_stride = _NoScrollSpinBox(); self._fr_stride.setRange(1, 100000); self._fr_stride.setValue(1); self._fr_stride.setFixedWidth(64)
            sf = S.Form()
            sf.row(("start:", self._fr_start), ("end(0=all):", self._fr_end))
            sf.row(("stride:", self._fr_stride))
            card.body.addLayout(sf)

        self._info = QtWidgets.QLabel("No data loaded.")
        self._info.setStyleSheet("color:#9a9a9a;font-size:10px"); self._info.setWordWrap(True)
        card.body.addWidget(self._info)
        lv.addWidget(card)

        # ── Dark / Bright / Background ──
        fld = S.make_card("Dark / Bright / Background")
        self._dark_sel = FieldSelector("Dark", default_dataset=dark_dataset)
        self._bright_sel = FieldSelector("Bright", with_mode=True)
        self._bg_sel = FieldSelector("Background")
        for w in (self._dark_sel, self._bright_sel, self._bg_sel):
            w.fieldReady.connect(self.fieldsChanged)
            fld.body.addWidget(w)
        lv.addWidget(fld)

        # ── Mask ──
        self._mask_sel = MaskSelector()
        self._mask_sel.maskChanged.connect(self.fieldsChanged)
        lv.addWidget(self._mask_sel)

        # Intensity statistics + histogram, pinned to the bottom (Data Viewer only).
        self.stats_panel = None
        lv.addStretch(1)
        if mode == "stack":
            self.stats_panel = IntensityStatsPanel()
            lv.addWidget(self.stats_panel)

        # ── MONITOR button (stream mode only) — pinned to the bottom ──
        self._monitor_btn = None
        if mode == "stream":
            self._monitor_btn = QtWidgets.QPushButton("●  MONITOR")
            self._monitor_btn.setCheckable(True)
            self._monitor_btn.setMinimumHeight(30)
            self._monitor_btn.setToolTip(
                "Watch the data folder for new frames and integrate them "
                "automatically as they appear, reusing the detector map (no full "
                "re-run). Turns green while active.")
            self._monitor_btn.toggled.connect(self._on_monitor_toggled)
            self._apply_monitor_style(False)
            lv.addWidget(self._monitor_btn)

    # ── data source (path / dataset / loading) ────────────────────
    def _dataset(self) -> str:
        return self._ds_combo.currentText().split("   ")[0].strip() or "exchange/data"

    def _browse_file(self):
        p = _browse(self, "Open data",
                    "Data (*.tif *.tiff *.h5 *.hdf5 *.hdf *.nxs *.ge*);;All (*)")
        if p:
            self._path_ed.setText(p); self._load()

    def _browse_folder(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Select folder of frames")
        if d:
            self._path_ed.setText(d); self._load()

    def _on_path_changed(self, p: str):
        from pathlib import Path
        h5 = is_h5(p)
        self._ds_row.setVisible(h5)
        if h5 and Path(p).exists():
            try:
                items = list_h5_datasets(p)
            except Exception:
                items = []
            if items:
                keep = self._ds_combo.currentText().strip()
                self._ds_combo.blockSignals(True); self._ds_combo.clear()
                for name, shape in items:
                    self._ds_combo.addItem(f"{name}   {tuple(shape)}", name)
                idx = next((i for i in range(self._ds_combo.count())
                            if self._ds_combo.itemData(i) == keep), -1)
                if idx < 0:
                    idx = next((i for i, (n, s) in enumerate(items) if len(s) >= 3), 0)
                self._ds_combo.setCurrentIndex(idx)
                self._ds_combo.blockSignals(False)

    def _collect_paths(self, raw: str) -> list:
        return _collect_frame_paths(raw)

    def _load(self):
        from pathlib import Path
        raw = self._path_ed.text().strip()
        if not raw:
            return
        if self._mode == "stream":
            # No in-memory load; just report the source.
            self._info.setText(f"Source: {raw}")
            self.dataChanged.emit()
            return
        try:
            self._stack = self._paths = self._h5 = None; self._nframes = 0
            p = Path(raw)
            if p.is_dir() or any(ch in raw for ch in "*?"):
                paths = self._collect_paths(raw)
                if not paths:
                    QtWidgets.QMessageBox.warning(self, "Empty", "No frames found."); return
                self._paths = paths; self._nframes = len(paths)
                kind = f"folder/glob ({self._nframes} files)"
            elif is_h5(raw):
                import h5py
                dset = self._dataset()
                with h5py.File(raw, "r") as f:
                    if dset not in f:
                        raise KeyError(f"dataset '{dset}' not in file")
                    shape = f[dset].shape
                n = shape[0] if len(shape) >= 3 else 1
                self._h5 = (raw, dset, n); self._nframes = n
                kind = f"HDF5 [{dset}] {shape}"
            else:
                import tifffile
                arr = np.asarray(tifffile.imread(raw))
                if arr.ndim >= 3:
                    self._stack = arr; self._nframes = arr.shape[0]
                    kind = f"TIFF stack {arr.shape}"
                else:
                    self._stack = arr[None, ...]; self._nframes = 1
                    kind = f"TIFF {arr.shape}"
            self._info.setText(f"Loaded: {kind}")
            self._setup_navigator()
            self._set_frame(0)
        except Exception:
            import traceback
            QtWidgets.QMessageBox.critical(self, "Load error", traceback.format_exc()[:500])

    def _setup_navigator(self):
        hi = max(0, self._nframes - 1)
        self._frame_spin.blockSignals(True); self._frame_spin.setRange(0, hi); self._frame_spin.blockSignals(False)
        if self._mode == "stack":
            self._nav_row.setEnabled(self._nframes > 1)
            self._slider.blockSignals(True); self._slider.setRange(0, hi); self._slider.blockSignals(False)
            self._nframes_lbl.setText(f"/ {hi}")

    def _set_frame(self, i):
        if self._nframes == 0:
            return
        i = max(0, min(int(i), self._nframes - 1))
        widgets = [self._frame_spin] + ([self._slider] if self._mode == "stack" else [])
        for w in widgets:
            w.blockSignals(True); w.setValue(i); w.blockSignals(False)
        self._cur = self._get_frame(i)
        self.dataChanged.emit()

    def _get_frame(self, i: int) -> np.ndarray:
        i = max(0, min(i, self._nframes - 1))
        if self._stack is not None:
            return np.asarray(self._stack[i], dtype=np.float32)
        if self._paths is not None:
            arr = _load_image(self._paths[i]).astype(np.float32)
            return arr[0] if arr.ndim == 3 else arr
        if self._h5 is not None:
            path, dset, _ = self._h5
            return _load_image(path, data_loc=dset, frame=i).astype(np.float32)
        raise RuntimeError("No data loaded")

    def full_stack(self) -> np.ndarray:
        if self._stack is not None:
            return np.asarray(self._stack)
        if self._h5 is not None:
            import h5py
            path, dset, _ = self._h5
            with h5py.File(path, "r") as f:
                return np.asarray(f[dset][()])
        if self._paths is not None:
            frames = [self._get_frame(i) for i in range(self._nframes)]
            return np.stack(frames, axis=0)
        raise RuntimeError("No data loaded")

    # ── public API ────────────────────────────────────────────────
    def set_path(self, path, dataset=None, *, load=True):
        """Preset the data path (and HDF5 dataset); optionally load immediately."""
        self._path_ed.setText(str(path))
        if dataset is not None and is_h5(str(path)):
            self._ds_combo.setEditText(dataset)
        if load:
            from pathlib import Path
            if self._mode == "stream" or Path(str(path)).exists():
                self._load()

    def n_frames(self) -> int:
        return self._nframes

    def frame_index(self) -> int:
        return self._frame_spin.value()

    def set_frame(self, i, **_):
        self._set_frame(i)

    def current_frame(self):
        """Raw (uncorrected) current 2-D frame, or None."""
        if self._nframes == 0:
            return None
        if self._cur is None:
            self._cur = self._get_frame(self.frame_index())
        return self._cur

    def source_cfg(self) -> dict:
        """Streaming source descriptor for BatchWorker (stream mode)."""
        from pathlib import Path
        raw = self._path_ed.text().strip()
        if is_h5(raw):
            return {"type": "hdf5", "path": raw, "dataset": self._dataset()}
        return {"type": "tiff_glob", "path": raw}

    def frame_range(self):
        end = self._fr_end.value() if self._fr_end.value() > 0 else None
        return (self._fr_start.value(), end, max(1, self._fr_stride.value()))

    def dark(self):
        return self._dark_sel.get_field()

    def bright(self):
        return self._bright_sel.get_field()

    def background(self):
        return self._bg_sel.get_field()

    def bright_mode(self) -> str:
        return self._bright_sel.get_mode()

    def has_pending_fields(self):
        return [s for s in (self._dark_sel, self._bright_sel, self._bg_sel) if s.has_pending()]

    def composite_mask(self):
        return self._mask_sel.composite_mask()

    def set_tab1_mask(self, mask):
        self._mask_sel.set_tab1_mask(mask)

    def corrected(self, frame):
        """Apply dark/bright/background to a raw frame (mask handled separately)."""
        if frame is None:
            return None
        d, b, g = self.dark(), self.bright(), self.background()
        if d is None and b is None and g is None:
            return np.asarray(frame, dtype=np.float32)
        return apply_field_corrections(frame, dark=d, bright=b,
                                       bright_mode=self.bright_mode(),
                                       background=g).astype(np.float32)

    # ── MONITOR button (stream mode) ───────────────────────────────
    def _apply_monitor_style(self, active: bool):
        from midas_gui import style as S
        if active:
            self._monitor_btn.setText("●  MONITORING")
            self._monitor_btn.setStyleSheet(
                "QPushButton { background:#2e7d32; color:white; font-weight:bold; "
                "border:1px solid #1b5e20; border-radius:4px; padding:4px; }")
        else:
            self._monitor_btn.setText("●  MONITOR")
            self._monitor_btn.setStyleSheet(
                "QPushButton { background:#3a3d44; color:#ddd; font-weight:bold; "
                f"border:1px solid {S.ACCENT}; border-radius:4px; padding:4px; }}")

    def _on_monitor_toggled(self, on: bool):
        self._apply_monitor_style(on)
        self.monitorToggled.emit(on)

    def set_monitor_active(self, on: bool):
        """Force the MONITOR button state without re-emitting (tab-driven revert)."""
        if self._monitor_btn is None:
            return
        self._monitor_btn.blockSignals(True)
        self._monitor_btn.setChecked(on)
        self._monitor_btn.blockSignals(False)
        self._apply_monitor_style(on)

    def is_monitoring(self) -> bool:
        return bool(self._monitor_btn and self._monitor_btn.isChecked())


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


def _convert_radial(x, lsd, px, wl, native, target):
    """Convert a radial axis between R (px), 2θ (deg) and Q (Å⁻¹).

    ``native`` is the unit ``x`` is already in ("R" or "Q"); returns ``x`` unchanged
    if the target matches or the geometry (lsd/px/wl) is missing.
    """
    x = np.asarray(x, dtype=float)
    if target == native or None in (lsd, px, wl):
        return x
    lsd, px, wl = float(lsd), float(px), float(wl)
    if native == "Q":
        tth = 2.0 * np.degrees(np.arcsin(np.clip(x * wl / (4 * math.pi), -1, 1)))
    else:  # R px
        tth = np.degrees(np.arctan(x * px / lsd))
    if target == "2th":
        return tth
    if target == "Q":
        return 4 * math.pi * np.sin(np.radians(tth) / 2) / wl
    return lsd * np.tan(np.radians(tth)) / px   # target == "R"


_XUNIT_LABEL = {"R": "R (px)", "2th": "2θ (°)", "Q": "Q (Å⁻¹)"}


class _UnitAxis(pg.AxisItem):
    """Bottom axis that relabels R-pixel tick positions in a chosen radial unit.

    The image/curves stay in their native coordinates; only the tick *labels* are
    converted, so the axis is exact (no resampling) even for a nonlinear unit."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._convert = None

    def set_convert(self, fn):
        self._convert = fn
        self.picture = None
        self.update()

    def tickStrings(self, values, scale, spacing):
        if self._convert is None or not len(values):
            return super().tickStrings(values, scale, spacing)
        conv = self._convert(np.asarray(values, dtype=float))
        return [f"{v:.4g}" for v in conv]


class WaterfallViewer(QtWidgets.QWidget):
    """2-D waterfall of 1-D profiles: x = R (px), y = frame index, colour = intensity.

    Rows are appended incrementally as frames are integrated, so the user watches
    every frame's radial integration stack up live.  The x-axis can be shown in
    R / 2θ / Q (tick labels converted from the run's calibration).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(2)
        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(QtWidgets.QLabel("Waterfall (all frames)"))
        bar.addWidget(QtWidgets.QLabel("  cmap:"))
        self._cmap = _NoScrollComboBox(); self._cmap.addItems(COLORMAPS); self._cmap.setFixedWidth(90)
        self._cmap.setCurrentText(_DEFAULT_CMAP)
        self._cmap.currentTextChanged.connect(self._apply_cmap)
        bar.addWidget(self._cmap)
        self._log = QtWidgets.QCheckBox("Log"); self._log.setChecked(True)
        self._log.toggled.connect(self._redraw)
        bar.addWidget(self._log)
        bar.addWidget(QtWidgets.QLabel("  x:"))
        self._xunit_combo = _NoScrollComboBox()
        self._xunit_combo.addItem("R (px)", "R")
        self._xunit_combo.addItem("2θ (°)", "2th")
        self._xunit_combo.addItem("Q (Å⁻¹)", "Q")
        self._xunit_combo.setToolTip("Label the x-axis in R (px), 2θ (deg) or Q (Å⁻¹). "
                                     "Needs the run's calibration for the conversion.")
        self._xunit_combo.currentIndexChanged.connect(self._on_xunit_changed)
        bar.addWidget(self._xunit_combo)
        bar.addStretch(1)
        self._stat = QtWidgets.QLabel(""); self._stat.setStyleSheet("color:#aaa;font-size:10px")
        bar.addWidget(self._stat)
        layout.addLayout(bar)

        self._xaxis = _UnitAxis(orientation="bottom")
        self._plot = pg.PlotWidget(background="k", axisItems={"bottom": self._xaxis})
        self._plot.setLabel("left", "frame #")
        self._plot.setLabel("bottom", "R (px)")
        self._img = pg.ImageItem()
        self._plot.addItem(self._img)
        layout.addWidget(self._plot, stretch=1)

        # Rows are written into a growing pre-allocated buffer (no per-frame vstack),
        # and redraws are throttled so a fast/large scan stays O(N) not O(N²).
        self._buf = None            # (capacity, n_r) float64
        self._nrows = 0
        self._r_axis = None
        self._redraw_timer = QtCore.QTimer(self)
        self._redraw_timer.setSingleShot(True); self._redraw_timer.setInterval(100)
        self._redraw_timer.timeout.connect(self._redraw)
        # Axis conversion context (from the run's calibration).
        self._lsd = self._px = self._wl = None
        self._native_unit = "R"
        self._apply_cmap(_DEFAULT_CMAP)

    # ── x-axis units (R / 2θ / Q) ─────────────────────────────────────

    def set_axis_context(self, lsd_um, px_um, wavelength_A, native_unit="R"):
        """Provide the run's geometry so the x-axis can be labelled in R / 2θ / Q."""
        self._lsd, self._px, self._wl = lsd_um, px_um, wavelength_A
        self._native_unit = native_unit if native_unit in ("R", "Q") else "R"
        self._refresh_xaxis()

    def _refresh_xaxis(self):
        target = self._xunit_combo.currentData()
        self._xaxis.set_convert(
            lambda vals: _convert_radial(vals, self._lsd, self._px, self._wl,
                                         self._native_unit, target))
        self._plot.setLabel("bottom", _XUNIT_LABEL[target])

    def _on_xunit_changed(self, _=0):
        self._refresh_xaxis()

    def reset(self, r_axis=None):
        """Start a new scan (r_axis = radial bin-centre array in px), or clear
        the view entirely when called with no axis."""
        self._buf = None
        self._nrows = 0
        self._r_axis = None if r_axis is None else np.asarray(r_axis, dtype=float)
        self._img.clear()
        self._stat.setText("")

    def add_profile(self, profile):
        """Append one frame's 1-D profile as the next waterfall row (buffered;
        the image redraw is coalesced on a timer)."""
        p = np.asarray(profile, dtype=np.float64)
        if self._buf is None:
            self._buf = np.empty((16, p.size), dtype=np.float64)
        elif self._nrows >= self._buf.shape[0]:
            self._buf = np.vstack([self._buf, np.empty_like(self._buf)])  # grow ×2
        if p.size != self._buf.shape[1]:                 # profile length changed → reset buffer
            self._buf = np.empty((max(16, self._nrows + 1), p.size), dtype=np.float64)
            self._nrows = 0
        self._buf[self._nrows] = p
        self._nrows += 1
        self._stat.setText(f"{self._nrows} frames")
        if not self._redraw_timer.isActive():
            self._redraw_timer.start()

    def _redraw(self):
        if self._buf is None or self._nrows == 0 or self._r_axis is None:
            return
        arr = self._buf[:self._nrows]                     # (n_frames, n_r) view — no copy
        disp = np.log10(np.clip(arr, 1e-6, None)) if self._log.isChecked() else arr
        # Level from a strided sample (fast + stable) rather than sorting every pixel.
        fin = disp[np.isfinite(disp)]
        if fin.size > 200_000:
            fin = fin[:: fin.size // 200_000]
        lo, hi = (float(np.percentile(fin, 1)), float(np.percentile(fin, 99))) if fin.size else (0.0, 1.0)
        if hi <= lo:
            hi = lo + 1.0
        # ImageItem (col-major): pass (n_r, n_frames) so x=R, y=frame
        self._img.setImage(disp.T, autoLevels=False, levels=(lo, hi))
        r0, r1 = float(self._r_axis[0]), float(self._r_axis[-1])
        self._img.setRect(QtCore.QRectF(r0, 0.0, r1 - r0, self._nrows))

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

    # Publication-quality categorical palette (matplotlib tab10 order) — dark,
    # print-friendly colours that read well on a white background.
    _PUB_PALETTE = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e",
                    "#8c564b", "#e377c2", "#17becf", "#bcbd22", "#7f7f7f"]

    # Two saved plot configurations. "White (publication)" is the default; "Dark"
    # preserves the original on-screen look.
    _THEMES = {
        "White (publication)": dict(
            bg="white", fg="#111111", grid_alpha=0.20, symbols=True,
            symbol_size=5, line_width=1.5, box=True, palette="pub"),
        "Dark": dict(
            bg="#111111", fg="#c8c8c8", grid_alpha=0.15, symbols=False,
            symbol_size=5, line_width=1.0, box=False, palette="hue"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(2)

        # Toolbar
        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(QtWidgets.QLabel("spacing:"))
        self._spacing = _NoScrollDoubleSpinBox()
        self._spacing.setRange(0.0, 1e9)
        self._spacing.setValue(500.0)
        self._spacing.setDecimals(0)
        self._spacing.setSingleStep(100.0)
        self._spacing.setSuffix("  cts")
        self._spacing.setFixedWidth(90)
        self._spacing.valueChanged.connect(self._restack)
        bar.addWidget(self._spacing)
        bar.addWidget(QtWidgets.QLabel("x:"))
        self._xunit_combo = _NoScrollComboBox()
        self._xunit_combo.addItem("R (px)", "R")
        self._xunit_combo.addItem("2θ (°)", "2th")
        self._xunit_combo.addItem("Q (Å⁻¹)", "Q")
        self._xunit_combo.setToolTip("Plot the x-axis in R (px), 2θ (deg) or Q (Å⁻¹). "
                                     "Needs the run's calibration for the conversion.")
        self._xunit_combo.currentIndexChanged.connect(self._on_xunit_changed)
        bar.addWidget(self._xunit_combo)
        bar.addWidget(QtWidgets.QLabel("theme:"))
        self._theme_combo = _NoScrollComboBox()
        self._theme_combo.addItems(list(self._THEMES.keys()))
        self._theme_combo.setToolTip("Plot appearance preset "
                                     "(White = publication point+line, Dark = classic).")
        self._theme_combo.currentTextChanged.connect(self._apply_theme)
        bar.addWidget(self._theme_combo)
        self._labels_chk = QtWidgets.QCheckBox("Labels")
        self._labels_chk.setChecked(True)
        self._labels_chk.setToolTip("Show each file's name just below its curve (left edge).")
        self._labels_chk.toggled.connect(self._toggle_labels)
        bar.addWidget(self._labels_chk)
        self._legend_chk = QtWidgets.QCheckBox("Legend")
        self._legend_chk.setChecked(False)
        self._legend_chk.setToolTip("Show a corner legend mapping each curve to its source file.")
        self._legend_chk.toggled.connect(self._toggle_legend)
        bar.addWidget(self._legend_chk)
        self._grid_chk = QtWidgets.QCheckBox("Grid")
        self._grid_chk.setChecked(False)
        self._grid_chk.setToolTip("Show the horizontal + vertical grid.")
        self._grid_chk.toggled.connect(self._toggle_grid)
        bar.addWidget(self._grid_chk)
        bar.addStretch(1)

        # Top-right controls: line width / symbol size / label font, each as
        # [−] label [+], groups separated by a vertical bar.
        def _tbtn(txt, tip, slot):
            b = QtWidgets.QToolButton(); b.setText(txt); b.setToolTip(tip)
            b.setAutoRaise(True); b.clicked.connect(slot); return b

        def _sep():
            s = QtWidgets.QLabel("|"); s.setStyleSheet("color:#888;")
            return s

        def _group(label, tip_minus, on_minus, tip_plus, on_plus):
            bar.addWidget(_tbtn("−", tip_minus, on_minus))
            bar.addWidget(QtWidgets.QLabel(label))
            bar.addWidget(_tbtn("+", tip_plus, on_plus))

        _group("line", "Thinner lines", lambda: self._adjust_linewidth(-0.5),
               "Thicker lines", lambda: self._adjust_linewidth(0.5))
        bar.addWidget(_sep())
        _group("sym", "Smaller symbols", lambda: self._adjust_symbolsize(-1),
               "Larger symbols", lambda: self._adjust_symbolsize(1))
        bar.addWidget(_sep())
        _group("font", "Smaller labels", lambda: self._adjust_fontsize(-1),
               "Larger labels", lambda: self._adjust_fontsize(1))
        self._stat = QtWidgets.QLabel("")
        self._stat.setStyleSheet("color:#aaa;font-size:10px")
        bar.addWidget(self._stat)
        layout.addLayout(bar)

        self._r_axes: list = []          # native x per curve (R px, or Q if Q-uniform)
        self._profiles: list = []
        self._curves: list = []
        self._labels: list = []          # inline pg.TextItem per curve
        self._fontsize = 9
        # Axis conversion context (from the run's calibration).
        self._lsd = self._px = self._wl = None
        self._native_unit = "R"          # unit the stored x arrays are already in

        self._plot = pg.PlotWidget()
        self._plot.setLabel("bottom", "R (px)")
        self._plot.setLabel("left", "Intensity + offset")
        self._legend = self._plot.addLegend(offset=(10, 10), labelTextSize="8pt")
        self._legend.setVisible(False)
        layout.addWidget(self._plot, stretch=1)

        # Default theme = white publication.
        self._theme = "White (publication)"
        self._theme_cfg = self._THEMES[self._theme]
        self._linewidth = self._theme_cfg["line_width"]
        self._symbol_size = self._theme_cfg["symbol_size"]
        self._apply_theme(self._theme)

    # ── public API ───────────────────────────────────────────────────

    def reset(self, r_axis=None):
        """Clear all stored profiles, curves and inline labels."""
        self._r_axes.clear()
        self._profiles.clear()
        for c in self._curves:
            self._plot.removeItem(c)
        self._curves.clear()
        for ti in self._labels:
            self._plot.removeItem(ti)
        self._labels.clear()
        if self._legend is not None:
            self._legend.clear()
        self._stat.setText("")

    def add_profile(self, r_axis, profile, label=None):
        """Append one frame's 1-D profile; draws it at its stacked offset.

        ``label`` (the source file id) is shown in the corner legend and as an
        inline tag just below the curve's left-most point.
        """
        r = np.asarray(r_axis, dtype=float)
        p = np.asarray(profile, dtype=float)
        i = len(self._profiles)
        self._r_axes.append(r)
        self._profiles.append(p)
        color = self._curve_color(i)
        offset = i * float(self._spacing.value())
        xd = self._x_display(r)
        curve = self._plot.plot(xd, p + offset, name=(str(label) if label else None))
        self._style_curve(curve, i)
        self._curves.append(curve)
        # Inline label just below the line's left-most point.
        ti = pg.TextItem(str(label) if label else f"#{i}", color=color, anchor=(0, 0))
        f = QtGui.QFont(); f.setPointSize(self._fontsize); ti.setFont(f)
        if xd.size:
            ti.setPos(float(xd[0]), float(p[0] + offset))
        ti.setVisible(self._labels_chk.isChecked())
        self._plot.addItem(ti)
        self._labels.append(ti)
        n = len(self._profiles)
        self._stat.setText(f"{n} frame{'s' if n != 1 else ''}")

    # ── x-axis units (R / 2θ / Q) ─────────────────────────────────────

    def set_axis_context(self, lsd_um, px_um, wavelength_A, native_unit="R"):
        """Provide the run's geometry so the x-axis can be shown in R / 2θ / Q.

        ``native_unit`` is the unit the profiles arrive in ("R" px, or "Q" when
        Q-uniform binning is active)."""
        self._lsd, self._px, self._wl = lsd_um, px_um, wavelength_A
        self._native_unit = native_unit if native_unit in ("R", "Q") else "R"
        self._restack()
        self._plot.setLabel("bottom", self._xlabel(),
                            **{"color": self._theme_cfg["fg"], "font-size": "11pt"})

    def _x_display(self, x_native):
        """Convert a native x array to the currently-selected unit."""
        return _convert_radial(x_native, self._lsd, self._px, self._wl,
                               self._native_unit, self._xunit_combo.currentData())

    def _xlabel(self) -> str:
        return _XUNIT_LABEL[self._xunit_combo.currentData()]

    def _on_xunit_changed(self, _=0):
        self._restack()
        self._plot.setLabel("bottom", self._xlabel(),
                            **{"color": self._theme_cfg["fg"], "font-size": "11pt"})

    def _toggle_grid(self, on: bool):
        self._plot.showGrid(x=on, y=on, alpha=self._theme_cfg["grid_alpha"])

    # ── theme + styling ───────────────────────────────────────────────

    def _curve_color(self, i: int):
        """Per-curve colour for the active theme."""
        if self._theme_cfg["palette"] == "pub":
            return self._PUB_PALETTE[i % len(self._PUB_PALETTE)]
        return _frame_color(i)

    def _style_curve(self, curve, i: int):
        """Apply the active theme's pen + point/line style to one curve."""
        col = self._curve_color(i)
        curve.setPen(pg.mkPen(col, width=self._linewidth))
        if self._theme_cfg["symbols"]:
            curve.setSymbol("o")
            curve.setSymbolSize(self._symbol_size)
            curve.setSymbolBrush(pg.mkBrush(col))
            curve.setSymbolPen(pg.mkPen(col))
        else:
            curve.setSymbol(None)

    def _apply_theme(self, name: str):
        cfg = self._THEMES.get(name)
        if cfg is None:
            return
        self._theme = name
        self._theme_cfg = cfg
        self._linewidth = cfg["line_width"]
        self._symbol_size = cfg["symbol_size"]
        self._plot.setBackground(cfg["bg"])
        pen = pg.mkPen(cfg["fg"], width=1)
        for ax_name in ("bottom", "left", "top", "right"):
            ax = self._plot.getAxis(ax_name)
            ax.setPen(pen); ax.setTextPen(pen)
        # Box frame (all four spines) for the publication theme.
        self._plot.showAxis("top", cfg["box"]); self._plot.showAxis("right", cfg["box"])
        if cfg["box"]:
            for ax_name in ("top", "right"):
                self._plot.getAxis(ax_name).setStyle(showValues=False)
        grid_on = self._grid_chk.isChecked()
        self._plot.showGrid(x=grid_on, y=grid_on, alpha=cfg["grid_alpha"])
        lbl = {"color": cfg["fg"], "font-size": "11pt"}
        self._plot.setLabel("bottom", self._xlabel(), **lbl)
        self._plot.setLabel("left", "Intensity + offset", **lbl)
        try:
            self._legend.setLabelTextColor(cfg["fg"])
        except Exception:
            pass
        # Restyle existing curves + inline labels.
        for i, curve in enumerate(self._curves):
            self._style_curve(curve, i)
        for i, ti in enumerate(self._labels):
            ti.setColor(self._curve_color(i))
        if self._theme_combo.currentText() != name:
            self._theme_combo.blockSignals(True)
            self._theme_combo.setCurrentText(name)
            self._theme_combo.blockSignals(False)

    def _toggle_legend(self, on: bool):
        if self._legend is not None:
            self._legend.setVisible(on)

    def _toggle_labels(self, on: bool):
        for ti in self._labels:
            ti.setVisible(on)

    def _adjust_linewidth(self, delta: float):
        self._linewidth = min(8.0, max(0.5, self._linewidth + delta))
        for i, curve in enumerate(self._curves):
            self._style_curve(curve, i)

    def _adjust_symbolsize(self, delta: int):
        """Grow/shrink the point markers (point+line themes). Also turns markers
        on if the current theme was line-only, so the control always has effect."""
        self._symbol_size = min(20, max(1, self._symbol_size + delta))
        if not self._theme_cfg.get("symbols"):
            self._theme_cfg = dict(self._theme_cfg, symbols=True)
        for i, curve in enumerate(self._curves):
            self._style_curve(curve, i)

    def _adjust_fontsize(self, delta: int):
        self._fontsize = min(24, max(5, self._fontsize + delta))
        f = QtGui.QFont(); f.setPointSize(self._fontsize)
        for ti in self._labels:
            ti.setFont(f)

    # ── internal ─────────────────────────────────────────────────────

    def _restack(self, _=None):
        """Redraw all curves + inline labels (offsets, spacing, x-unit)."""
        spacing = float(self._spacing.value())
        for i, (curve, r, p) in enumerate(
                zip(self._curves, self._r_axes, self._profiles)):
            xd = self._x_display(r)
            curve.setData(xd, p + i * spacing)
            if i < len(self._labels) and xd.size:
                self._labels[i].setPos(float(xd[0]), float(p[0] + i * spacing))
        if self._curves:
            self._plot.autoRange()


class LogPanel(QtWidgets.QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(1000)
        self.setFont(_mono_font(9))
        self.setMaximumHeight(120)

    def append(self, line: str):
        self.appendPlainText(line)
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
