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

from midas_gui.constants import COLORMAPS, DISTORTION_NAMES, DEFAULT_COLORMAP, DEVICES

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


def _resolve_cmap(name):
    """Return a pyqtgraph ColorMap for ``name`` — never None.

    The GUI's colormaps (hot / viridis / inferno / plasma / turbo / gray) are
    matplotlib maps: ``pg.colormap.get(name)`` returns None when matplotlib is not
    installed, and passing that None into pyqtgraph crashes viewer construction (see
    the Linux/Windows fresh-env bug). Fall back through matplotlib, then a
    pyqtgraph-native map, then a plain grayscale ramp, so a missing matplotlib can
    never take the tabs down."""
    for attempt in (
        lambda: pg.colormap.get(name),
        lambda: pg.colormap.get(name, source="matplotlib"),
        lambda: pg.colormap.getFromMatplotlib(name),
        lambda: pg.colormap.get("CET-L9"),   # native — no matplotlib needed
    ):
        try:
            cm = attempt()
        except Exception:
            cm = None
        if cm is not None:
            return cm
    return pg.ColorMap([0.0, 1.0], [(0, 0, 0, 255), (255, 255, 255, 255)])


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
        self._vmin.valueChanged.connect(self._on_percentile_changed)
        bar.addWidget(self._vmin)
        bar.addWidget(QtWidgets.QLabel("vmax%:"))
        self._vmax = _NoScrollSpinBox()
        self._vmax.setRange(1, 100); self._vmax.setValue(99); self._vmax.setFixedWidth(45)
        self._vmax.valueChanged.connect(self._on_percentile_changed)
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
        # ignoreBounds: without it, this item's position (which follows the
        # mouse every frame) feeds pyqtgraph's auto-range bounds calculation,
        # so once continuous auto-range is enabled (e.g. via the "A" button)
        # the view range keeps re-fitting itself to wherever the cursor is.
        self._iv.addItem(self._vl, ignoreBounds=True)
        self._iv.addItem(self._hl, ignoreBounds=True)
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
        self._manual_levels: Optional[tuple] = None
        self._suspend_level_track = False
        self._iv.getHistogramWidget().sigLevelsChanged.connect(self._on_hist_levels_changed)
        self._set_cmap(_DEFAULT_CMAP)

    def set_image(self, data: np.ndarray, autorange: bool = True):
        self._data = data.astype(np.float32)
        self._redisplay()
        self._apply_view_limits(data.shape[1], data.shape[0])
        if autorange:
            self._iv.getView().getViewBox().autoRange()
        self._coord_bar.setText(
            f"Image {data.shape[1]}×{data.shape[0]} px  |  "
            "Move cursor over image to inspect pixel values")

    def _apply_view_limits(self, w: int, h: int):
        """Bound pan/zoom to a sane region around the image so the user can't
        scroll/zoom out into an empty void or lose the image off-screen."""
        if w <= 0 or h <= 0:
            return
        span = max(w, h)
        pad = span * 0.5
        vb = self._iv.getView().getViewBox()
        vb.setLimits(
            xMin=-pad, xMax=w + pad,
            yMin=-pad, yMax=h + pad,
            minXRange=max(span * 0.01, 2.0),
            minYRange=max(span * 0.01, 2.0),
            maxXRange=w + 2 * pad,
            maxYRange=h + 2 * pad,
        )

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
        if self._manual_levels is not None:
            lo, hi = self._manual_levels
        else:
            fin = disp[np.isfinite(disp)]
            if fin.size:
                lo = float(np.percentile(fin, self._vmin.value()))
                hi = float(np.percentile(fin, self._vmax.value()))
            else:
                lo, hi = 0.0, 1.0
        # autoRange/autoHistogramRange default to True in pyqtgraph and would reset
        # the view on every redraw; set_image() handles framing explicitly via its
        # `autorange` flag, so the zoom/pan is preserved across frames and re-levels.
        # Levels are likewise pinned to `lo`/`hi` rather than pyqtgraph's autoLevels
        # so a manual histogram drag survives subsequent live frames.
        self._suspend_level_track = True
        self._iv.setImage(disp.astype(np.float32), autoLevels=False, levels=(lo, hi),
                          autoRange=False, autoHistogramRange=False)
        self._suspend_level_track = False

    def _on_hist_levels_changed(self, *_args):
        """User dragged the histogram LUT region — remember it across future frames."""
        if self._suspend_level_track:
            return
        self._manual_levels = tuple(self._iv.getHistogramWidget().getLevels())

    def _on_percentile_changed(self, *_args):
        """User edited vmin%/vmax% — that's an explicit request to go back to auto levels."""
        self._manual_levels = None
        self._redisplay()

    def _set_cmap(self, name: str):
        self._iv.setColorMap(_resolve_cmap(name))

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
            pen=pg.mkPen("#2a7fd4", width=1.5),
            brush=pg.mkBrush(42, 127, 212, 180))
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
        pen = pg.mkPen("#2a7fd4", width=1.5, style=QtCore.Qt.DashLine)
        if self._ring_fit_item is not None:
            self._iv.removeItem(self._ring_fit_item)
        self._ring_fit_item = pg.PlotDataItem(xs, ys, pen=pen)
        self._iv.addItem(self._ring_fit_item)
        if self._ring_fit_center is not None:
            self._iv.removeItem(self._ring_fit_center)
        self._ring_fit_center = pg.ScatterPlotItem(
            [cx], [cy], symbol="+", size=18,
            pen=pg.mkPen("#2a7fd4", width=2.5), brush=pg.mkBrush(0, 0, 0, 0))
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


def _add_auto_manual_buttons(plot_widget: "pg.PlotWidget", on_auto, on_manual):
    """Replace a PlotWidget's native "A" auto-range corner button with a small
    "A"/"M" (Auto/Manual) pair in the same bottom-left spot.

    ``on_auto``/``on_manual`` fire on *every* click of the respective button,
    including a reclick of whichever one is already active — a QButtonGroup
    blocks the checked button from being unchecked by its own click, but
    ``clicked`` still fires, so callers use a reclick to mean "reset the view
    to this mode's defaults now" (re-fit for Auto, snap back to the held
    range for Manual).

    Returns the ``(auto_button, manual_button)`` pair.
    """
    plot_widget.getPlotItem().hideButtons()
    grp = QtWidgets.QButtonGroup(plot_widget)
    grp.setExclusive(True)
    btn_a = QtWidgets.QPushButton("A", plot_widget)
    btn_m = QtWidgets.QPushButton("M", plot_widget)
    for b in (btn_a, btn_m):
        b.setCheckable(True)
        b.setFixedSize(18, 18)
        b.setStyleSheet(
            "QPushButton { font-size:9px; padding:0; background:#333; color:#ccc; "
            "border:1px solid #555; } "
            "QPushButton:checked { background:#4a7; color:#000; }")
        grp.addButton(b)
    btn_a.setToolTip("Auto-range: fit the view to the data automatically.\n"
                     "Click again to re-fit right now.")
    btn_m.setToolTip("Manual: hold the axis limits set via each axis's "
                     "right-click menu (“Manual” + min/max), even "
                     "during live acquisition.\nClick again to snap back to "
                     "those exact values.")
    btn_a.setChecked(True)
    btn_a.clicked.connect(on_auto)
    btn_m.clicked.connect(on_manual)

    def _reposition(*_):
        margin = 3
        y = plot_widget.height() - margin - btn_a.height()
        btn_a.move(margin, y)
        btn_m.move(margin + btn_a.width() + 2, y)
        btn_a.raise_(); btn_m.raise_()

    class _ResizeFilter(QtCore.QObject):
        def eventFilter(self, obj, ev):
            if ev.type() in (QtCore.QEvent.Resize, QtCore.QEvent.Show):
                _reposition()
            return False

    rf = _ResizeFilter(plot_widget)
    plot_widget.installEventFilter(rf)
    plot_widget._auto_manual_resize_filter = rf   # keep alive (else GC'd)
    _reposition()
    return btn_a, btn_m


def _install_manual_axis_capture(plot_widget: "pg.PlotWidget", callback):
    """Call ``callback(xmin, xmax, ymin, ymax)`` with the ViewBox's current
    view range whenever the user commits a value into the PlotWidget's own
    native right-click "X axis" / "Y axis" > Manual min/max fields.

    pyqtgraph already applies a typed value to the view itself (see
    ``ViewBoxMenu.xRangeTextChanged``/``yRangeTextChanged``); this just lets a
    caller mirror the result, e.g. to remember it as the range an Auto/Manual
    toggle button pair (see :func:`_add_auto_manual_buttons`) should hold and
    restore.
    """
    vb = plot_widget.getViewBox()

    def _captured(*_):
        (xmin, xmax), (ymin, ymax) = vb.viewRange()
        callback(xmin, xmax, ymin, ymax)

    for axis in (0, 1):
        ctrl = vb.menu.ctrl[axis]
        ctrl.minText.editingFinished.connect(_captured)
        ctrl.maxText.editingFinished.connect(_captured)


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
        self._xaxis.currentIndexChanged.connect(self._on_xaxis_changed)
        bar.addWidget(self._xaxis)
        self._logy = QtWidgets.QCheckBox("Log Y")
        self._logy.toggled.connect(self._on_logy_toggled)
        bar.addWidget(self._logy)
        bar.addStretch(1)
        self._stat = QtWidgets.QLabel("")
        self._stat.setStyleSheet("color:#aaa;font-size:10px")
        bar.addWidget(self._stat)
        self._toolbar_layout = bar   # exposed for external widget insertion
        layout.addLayout(bar)

        # Manual axis limits (see the "A"/"M" buttons added over the plot's
        # bottom-left corner, below) reuse the axis's own native right-click
        # "Manual" min/max fields rather than a separate row of spin boxes.
        self._manual_mode = False
        self._manual_range: Optional[tuple] = None   # (xmin, xmax, ymin, ymax)

        self._plot = pg.PlotWidget(background="k")
        self._plot.setLabel("left", "Mean intensity")
        self._plot.setLabel("bottom", "R (px)")
        self._plot.showGrid(x=True, y=True, alpha=0.2)
        self._user_xrange: Optional[tuple] = None
        self._user_yrange: Optional[tuple] = None
        self._suspend_range_track = False
        vb0 = self._plot.getPlotItem().getViewBox()
        vb0.sigXRangeChanged.connect(self._on_xrange_changed)
        vb0.sigYRangeChanged.connect(self._on_yrange_changed)
        self._band_lo = pg.PlotDataItem([], [], pen=None)
        self._band_hi = pg.PlotDataItem([], [], pen=None)
        self._band = pg.FillBetweenItem(self._band_lo, self._band_hi,
                                        brush=pg.mkBrush(136, 204, 255, 60))
        self._band.setVisible(False)
        self._plot.addItem(self._band)
        self._curve = self._plot.plot([], [], pen=pg.mkPen("#88ccff", width=2))
        self._ring_lines: list = []
        layout.addWidget(self._plot, stretch=1)

        self._btn_auto, self._btn_manual = _add_auto_manual_buttons(
            self._plot, self._on_auto_clicked, self._on_manual_clicked)
        _install_manual_axis_capture(self._plot, self._on_manual_range_edited)

        # Click-to-pick a radius (drawn on the image by the caller).
        self._pick_line = None
        self._plot.scene().sigMouseClicked.connect(self._on_plot_clicked)

        self._r_px = self._prof = self._sigma = None
        self._wl = self._lsd = self._px = None
        self._ring_groups: list = []   # [{"radii": [...], "color": "#hex"}, ...]
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

    def set_ring_markers(self, groups, lsd_um=None, px_um=None, wl=None):
        """``groups``: list of ``{"radii": [r_px, ...], "color": "#rrggbb"}`` —
        one entry per material, each drawn in its own color."""
        self._ring_groups = list(groups)
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

    def _on_xaxis_changed(self, *_):
        """Switching R/2θ/Q changes the X scale entirely — a remembered X zoom
        from the old unit is meaningless in the new one, so drop it."""
        self._user_xrange = None
        self._clear_pick_line()
        self._replot()

    def _on_logy_toggled(self, *_):
        """Log/linear Y have unrelated scales — drop any remembered Y zoom."""
        self._user_yrange = None
        self._replot()

    def _on_manual_range_edited(self, xmin, xmax, ymin, ymax):
        """The user set exact limits via an axis's native right-click
        "Manual" min/max fields (pyqtgraph already applied it to the view) —
        remember it as the held manual range: what "M" reapplies on a
        reclick, and what every live-acquisition redraw holds to while
        Manual is active."""
        self._manual_range = (xmin, xmax, ymin, ymax)
        if self._manual_mode:
            self._apply_manual_range()

    def _on_manual_clicked(self):
        """"M" clicked — switch to Manual, or (on a reclick) snap back to the
        exact limits held from the axes' native "Manual" min/max fields."""
        if self._manual_range is None:
            (xmin, xmax), (ymin, ymax) = self._plot.getViewBox().viewRange()
            self._manual_range = (xmin, xmax, ymin, ymax)
        self._manual_mode = True
        self._apply_manual_range()

    def _on_auto_clicked(self):
        """"A" clicked — switch to Auto, or (on a reclick) force an
        immediate re-fit to the current profile."""
        self._manual_mode = False
        self._user_xrange = self._user_yrange = None
        self._replot()

    def _apply_manual_range(self):
        """Force the exact held manual limits, unclamped by any pan/zoom bound."""
        if self._manual_range is None:
            return
        xmin, xmax, ymin, ymax = self._manual_range
        vb = self._plot.getPlotItem().getViewBox()
        vb.setLimits(xMin=None, xMax=None, yMin=None, yMax=None,
                     maxXRange=None, maxYRange=None)
        self._plot.setXRange(xmin, xmax, padding=0)
        self._plot.setYRange(ymin, ymax, padding=0)

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
        else:
            self._plot.setLabel("left", "Mean intensity")

        # Everything below can trigger incidental view-range signals (pyqtgraph
        # autorange on setData, setLimits() clamping the current view, etc.) —
        # suspend user-zoom tracking for all of it so only genuine mouse-driven
        # pan/zoom ever gets remembered as "the user zoomed".
        self._suspend_range_track = True
        try:
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
            ymin = ymax = None
            if fin.size:
                ymin = float(fin.min() * 0.9) if not log else 1.0
                ymax = float(fin.max()) * 1.05

            x_arr = np.asarray(x)
            xmin = max(0.0, float(x_arr.min())) if x_arr.size else None
            xmax = float(x_arr.max()) if x_arr.size else None

            if self._manual_mode:
                # Manual mode is authoritative: never touch pan/zoom bounds or
                # the view range from the data here — just hold the user's
                # entered limits, even across live-acquisition redraws.
                self._apply_manual_range()
            else:
                if xmin is not None and ymin is not None:
                    self._apply_view_limits(xmin, xmax, ymin, ymax)

                if self._user_xrange is not None:
                    self._plot.setXRange(*self._user_xrange, padding=0)
                elif xmin is not None:
                    self._plot.setXRange(xmin, xmax, padding=0.02)

                if self._user_yrange is not None:
                    self._plot.setYRange(*self._user_yrange, padding=0)
                elif ymin is not None:
                    self._plot.setYRange(ymin, ymax, padding=0)

            self._stat.setText(f"{len(self._r_px)} bins | max={np.nanmax(self._prof):.1f}")

            # Ring markers redrawn LAST (after setXRange) so they always appear
            for ln in self._ring_lines:
                self._plot.removeItem(ln)
            self._ring_lines.clear()
            if self._ring_groups:
                lsd = self._ring_lsd or self._lsd
                px  = self._ring_px  or self._px
                wl  = self._ring_wl  or self._wl
                for group in self._ring_groups:
                    pen = pg.mkPen(group.get("color", "#f0c060"), width=1.5,
                                    style=QtCore.Qt.DotLine)
                    for r in group.get("radii", []):
                        x_pos = self._r_to_x(r, idx, lsd, px, wl)
                        if x_pos is None:
                            continue
                        ln = pg.InfiniteLine(pos=x_pos, angle=90, pen=pen, movable=False)
                        self._plot.addItem(ln)
                        self._ring_lines.append(ln)
        finally:
            self._suspend_range_track = False

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

    def _on_xrange_changed(self, _vb, xrange):
        """User zoomed/panned the X axis — remember it so a later replot (e.g.
        from a parameter change) doesn't reset the view back to full range."""
        if self._suspend_range_track:
            return
        self._user_xrange = (float(xrange[0]), float(xrange[1]))

    def _on_yrange_changed(self, _vb, yrange):
        """User zoomed/panned the Y axis — remember it (see _on_xrange_changed)."""
        if self._suspend_range_track:
            return
        self._user_yrange = (float(yrange[0]), float(yrange[1]))

    def _apply_view_limits(self, xmin, xmax, ymin, ymax):
        """Bound pan/zoom to the current profile (+ margin) so the user can't
        scroll/zoom arbitrarily far away from where the data actually is."""
        if not all(math.isfinite(v) for v in (xmin, xmax, ymin, ymax)):
            return
        if xmax <= xmin:
            xmax = xmin + 1.0
        if ymax <= ymin:
            ymax = ymin + 1.0
        xpad = 0.15 * (xmax - xmin)
        ypad = 0.25 * (ymax - ymin)
        vb = self._plot.getPlotItem().getViewBox()
        vb.setLimits(xMin=max(0.0, xmin - xpad), xMax=xmax + xpad,
                     yMin=ymin - ypad, yMax=ymax + ypad,
                     maxXRange=(xmax - xmin) + 2 * xpad,
                     maxYRange=(ymax - ymin) + 2 * ypad)


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

    # ── GUI state (Save/Load GUI State) ─────────────────────────────
    def get_state(self) -> dict:
        return {
            "polar_check": self.polar_check.isChecked(),
            "pol_fraction": self.pol_fraction.value(),
            "pol_plane": self.pol_plane.value(),
            "solid_check": self.solid_check.isChecked(),
        }

    def set_state(self, state: dict):
        if not state:
            return
        if "pol_fraction" in state:
            self.pol_fraction.setValue(float(state["pol_fraction"]))
        if "pol_plane" in state:
            self.pol_plane.setValue(float(state["pol_plane"]))
        if "polar_check" in state:
            self.polar_check.setChecked(bool(state["polar_check"]))
        if "solid_check" in state:
            self.solid_check.setChecked(bool(state["solid_check"]))


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

    # ── GUI state (Save/Load GUI State) ─────────────────────────────
    def get_state(self) -> dict:
        st = {
            "checked": self.isChecked(),
            "path": self._path_ed.text(),
            "dataset": self._ds_combo.currentText(),
            "start": self._start.value(),
            "end": self._end.value(),
        }
        if self._mode is not None:
            st["mode"] = self._mode.currentIndex()
        return st

    def set_state(self, state: dict):
        """Restore path/range/mode and re-compute the field if it was enabled —
        the "auto re-trigger this tab's own load pipeline" behavior applied to
        dark/bright/background fields specifically."""
        if not state:
            return
        path = state.get("path", "")
        if path:
            self._path_ed.setText(path)
        ds = state.get("dataset")
        if ds:
            self._ds_combo.setEditText(ds)
        self._update_frame_limit()
        # Widen the range if needed so a saved value isn't silently clamped to 0
        # when the file couldn't be probed yet (e.g. path not found at restore time).
        hi = max(self._start.maximum(), int(state.get("start", 0)), int(state.get("end", 0)))
        self._start.setMaximum(hi); self._end.setMaximum(hi)
        if "start" in state:
            self._start.setValue(int(state["start"]))
        if "end" in state:
            self._end.setValue(int(state["end"]))
        if self._mode is not None and "mode" in state:
            self._mode.setCurrentIndex(int(state["mode"]))
        self.setChecked(bool(state.get("checked", False)))
        if self.isChecked() and path:
            self._compute()


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

    def add_file_source(self, path):
        """Programmatically add a mask *file* row (e.g. a tab's default mask).

        No-op on a blank path or one already present, so callers can wire a default
        idempotently without duplicating the row."""
        path = str(path or "").strip()
        if not path:
            return
        if any(e["kind"] == "file" and e["path"] == path for e in self._sources):
            return
        self._add_source("file", path)

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

    # ── GUI state (Save/Load GUI State) ─────────────────────────────
    def get_state(self) -> dict:
        """Serializes file/folder sources only — the "tab1" source is owned
        and re-supplied at runtime by the tab via :meth:`set_tab1_mask`."""
        return {"sources": [{"kind": e["kind"], "path": e["path"]}
                             for e in self._sources if e["kind"] != "tab1"]}

    def set_state(self, state: dict):
        if not state:
            return
        for e in [e for e in self._sources if e["kind"] != "tab1"]:
            self._remove(e)
        for s in state.get("sources", []):
            kind, path = s.get("kind"), s.get("path")
            if kind and path:
                self._add_source(kind, path)


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
        # Min height only (no max) so the splitter above can grow the histogram.
        self._plot.setMinimumHeight(90)
        self._plot.setLabel("bottom", "intensity", **{"color": "#d0d0d0", "font-size": "9pt"})
        self._plot.setLabel("left", "log(count+1)", **{"color": "#d0d0d0", "font-size": "9pt"})
        for ax in ("bottom", "left"):
            self._plot.getAxis(ax).setTextPen("#c8c8c8")
            self._plot.getAxis(ax).setPen("#8a8a8a")
        self._curve = self._plot.plot(
            [], [], stepMode="center", fillLevel=0,
            brush=(90, 140, 220, 150), pen=pg.mkPen("#6ea8ff"))
        v.addWidget(self._plot)

        # Manual axis limits reuse the axis's own native right-click "Manual"
        # min/max fields rather than a separate row of spin boxes.
        self._manual_mode = False
        self._manual_range: Optional[tuple] = None   # (xmin, xmax, ymin, ymax)
        self._btn_auto, self._btn_manual = _add_auto_manual_buttons(
            self._plot, self._on_auto_clicked, self._on_manual_clicked)
        _install_manual_axis_capture(self._plot, self._on_manual_range_edited)

        self._text = QtWidgets.QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setFont(_mono_font(8))
        # The readout is a fixed, short list of lines — show it in full (no inner
        # scrollbar) and let its height track the content. That leaves the plot as
        # the only flexible child, so the splitter above resizes the plot while the
        # whole panel (plot + text) moves as a unit.
        self._text.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        self._text.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._text.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self._text.setSizePolicy(QtWidgets.QSizePolicy.Preferred,
                                 QtWidgets.QSizePolicy.Fixed)
        self._text.setStyleSheet(
            "QPlainTextEdit { background:#23252b; color:#d6d6d6; border:1px solid #444; }")
        v.addWidget(self._text)
        self._fit_text_height()
        self._hist = None

    def _fit_text_height(self):
        """Size the readout box to fit exactly its current line count."""
        fm = self._text.fontMetrics()
        n = max(1, self._text.document().blockCount())
        m = self._text.contentsMargins()
        doc_m = int(self._text.document().documentMargin()) * 2
        fr = self._text.frameWidth() * 2
        self._text.setFixedHeight(
            n * fm.lineSpacing() + doc_m + fr + m.top() + m.bottom() + 4)

    def scope(self) -> str:
        return self._scope.currentData()

    def set_scope_enabled(self, on: bool):
        self._scope.setEnabled(on)

    def _on_manual_range_edited(self, xmin, xmax, ymin, ymax):
        """The user set exact limits via an axis's native right-click
        "Manual" min/max fields (pyqtgraph already applied it to the view) —
        remember it as the held manual range: what "M" reapplies on a
        reclick, and what every live-acquisition redraw holds to while
        Manual is active."""
        self._manual_range = (xmin, xmax, ymin, ymax)
        if self._manual_mode:
            self._apply_manual_range()

    def _on_manual_clicked(self):
        """"M" clicked — switch to Manual, or (on a reclick) snap back to the
        exact limits held from the axes' native "Manual" min/max fields."""
        if self._manual_range is None:
            (xmin, xmax), (ymin, ymax) = self._plot.getViewBox().viewRange()
            self._manual_range = (xmin, xmax, ymin, ymax)
        self._manual_mode = True
        self._apply_manual_range()

    def _on_auto_clicked(self):
        """"A" clicked — switch to Auto, or (on a reclick) force an
        immediate re-fit of the histogram."""
        self._manual_mode = False
        self._redraw_hist()

    def _apply_manual_range(self):
        """Force the exact held manual limits, unclamped by any pan/zoom bound."""
        if self._manual_range is None:
            return
        xmin, xmax, ymin, ymax = self._manual_range
        vb = self._plot.getViewBox()
        vb.setLimits(xMin=None, xMax=None, yMin=None, yMax=None,
                     maxXRange=None, maxYRange=None)
        vb.setXRange(xmin, xmax, padding=0)
        vb.setYRange(ymin, ymax, padding=0)

    def set_data(self, values, scope: str = ""):
        vals = np.asarray(values, dtype=np.float64).ravel()
        vals = vals[np.isfinite(vals)]
        if vals.size == 0:
            self._text.setPlainText(f"{scope}\n(no pixels)")
            self._fit_text_height()
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
        self._fit_text_height()

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
        if self._manual_mode:
            # Manual mode is authoritative — hold the user's limits regardless
            # of how the histogram data changed (e.g. a new live frame).
            self._apply_manual_range()
            return
        # Fixed lower-left corner (x=0, y=-2); rescale to (0..xmax, -2..ymax) on refresh.
        xmax = float(edges[-1]) if edges.size else 1.0
        ymax = float(y.max()) if y.size else 1.0
        ymax = ymax * 1.08 if ymax > 0 else 1.0
        vb = self._plot.getViewBox()
        vb.setLimits(xMin=0.0, yMin=-2.0)
        vb.setXRange(0.0, max(xmax, 1.0), padding=0)
        vb.setYRange(-2.0, ymax, padding=0)


class PvaLiveSource(QtCore.QObject):
    """Subscribes to an EPICS PVA image PV (NTNDArray) and emits decoded
    numpy frames.

    pvapy's ``Channel.monitor()`` delivers callbacks on its own internal
    thread; this class never touches Qt widgets directly, only emits
    signals — Qt auto-queues those onto the receiving (GUI) thread."""

    frameReady = QtCore.pyqtSignal(np.ndarray, int)      # image, uniqueId
    connectionChanged = QtCore.pyqtSignal(bool)
    error = QtCore.pyqtSignal(str)

    _REQUEST = "field(value,dimension,uniqueId,attribute,codec,uncompressedSize)"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._channel = None
        self._AdImageUtility = None

    def start(self, pv_name: str) -> bool:
        self.stop()
        try:
            import pvapy as pva
            from pvapy.utility.adImageUtility import AdImageUtility
        except ImportError as e:
            self.error.emit(f"pvapy not installed: {e}")
            return False
        self._AdImageUtility = AdImageUtility
        try:
            self._channel = pva.Channel(pv_name)
            self._channel.setConnectionCallback(self._on_connection)
            self._channel.monitor(self._on_value, self._REQUEST)
        except Exception as e:
            self.error.emit(str(e))
            self._channel = None
            return False
        return True

    def _on_connection(self, is_connected):
        self.connectionChanged.emit(bool(is_connected))

    def _on_value(self, pv_object):
        try:
            image_id, image, *_ = self._AdImageUtility.reshapeNtNdArray(pv_object)
        except Exception as e:
            self.error.emit(f"Frame decode failed: {e}")
            return
        if image is not None:
            self.frameReady.emit(np.asarray(image, dtype=np.float32), int(image_id))

    def stop(self):
        if self._channel is not None:
            try:
                self._channel.stopMonitor()
            except Exception:
                pass
            self._channel = None

    def is_active(self) -> bool:
        return self._channel is not None


class DataLoaderPanel(QtWidgets.QWidget):
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
                 dark_dataset="exchange/data_dark", allow_live=False):
        super().__init__(parent)
        from midas_gui import style as S
        self._mode = mode
        self._stack = self._paths = self._h5 = None
        self._nframes = 0
        self._cur = None
        self._live_src: Optional[PvaLiveSource] = None

        # The cards live inside a scroll area; the stats panel (stack mode) sits
        # below it in a draggable vertical splitter (see the end of __init__).
        self._scroll = QtWidgets.QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        inner = QtWidgets.QWidget(); self._scroll.setWidget(inner)
        lv = QtWidgets.QVBoxLayout(inner); lv.setContentsMargins(4, 4, 4, 4); lv.setSpacing(8)

        # Distinct background + accent right border so the data-loader panel stands
        # out from the middle parameters panel.
        self.setObjectName("dataLoaderPanel")
        inner.setObjectName("dataLoaderInner")
        self._scroll.setObjectName("dataLoaderScroll")
        self._scroll.viewport().setObjectName("dataLoaderViewport")
        self.setStyleSheet(
            f"#dataLoaderPanel {{ border: none; border-right: 2px solid {S.ACCENT}; }}"
            f"#dataLoaderScroll {{ border: none; }}"
            f"#dataLoaderViewport, #dataLoaderInner {{ background: #2b2e35; }}")

        # ── Live Data card (collapsible via its own checkbox; above Data) ──
        if allow_live:
            live_card = S.make_card("Live Data")
            live_card.setCheckable(True)
            live_card.setChecked(False)
            self._live_content = QtWidgets.QWidget()
            lvbox = QtWidgets.QVBoxLayout(self._live_content)
            lvbox.setContentsMargins(0, 4, 0, 0); lvbox.setSpacing(4)
            pv_row = QtWidgets.QHBoxLayout(); pv_row.setSpacing(4)
            pv_row.addWidget(QtWidgets.QLabel("Live PV:"))
            self._pv_ed = _NoScrollComboBox()
            self._pv_ed.setEditable(True)
            self._pv_ed.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
            self._pv_ed.lineEdit().setPlaceholderText("e.g. 20IDFF:Pva1:Image")
            for d in DEVICES:
                full_pv = f"{d.get('prefix', '')}{d.get('pva_suffix', '')}"
                self._pv_ed.addItem(d.get("name", ""), full_pv)
            self._pv_ed.setCurrentIndex(-1)
            self._pv_ed.setEditText("")
            self._pv_ed.activated.connect(self._on_pv_device_picked)
            pv_row.addWidget(self._pv_ed, 1)
            lvbox.addLayout(pv_row)
            btn_row = QtWidgets.QHBoxLayout(); btn_row.setSpacing(4)
            self._live_start_btn = QtWidgets.QPushButton("Start")
            self._live_stop_btn = QtWidgets.QPushButton("Stop")
            self._live_stop_btn.setEnabled(False)
            self._live_start_btn.clicked.connect(self._start_live)
            self._live_stop_btn.clicked.connect(self.stop_live)
            btn_row.addWidget(self._live_start_btn); btn_row.addWidget(self._live_stop_btn)
            lvbox.addLayout(btn_row)
            self._live_status_lbl = QtWidgets.QLabel("Stopped.")
            self._live_status_lbl.setWordWrap(True)
            self._live_status_lbl.setStyleSheet(f"color:{S.MUTED};font-size:10px")
            lvbox.addWidget(self._live_status_lbl)
            self._live_content.setVisible(False)
            live_card.body.addWidget(self._live_content)
            live_card.toggled.connect(self._on_live_card_toggled)
            lv.addWidget(live_card)

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
        if allow_live:
            reload_btn = QtWidgets.QToolButton(); reload_btn.setText("⟳"); reload_btn.setFixedWidth(28)
            reload_btn.setToolTip(
                "Reload the current Data path/folder/dataset — use this after "
                "stopping a live PV stream to restore the static data.")
            reload_btn.clicked.connect(self._load)
            pr.addWidget(reload_btn)
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

        # Intensity statistics + histogram (Data Viewer only). Created here but
        # placed in a draggable splitter below, not in the scrolling card column.
        self.stats_panel = None
        lv.addStretch(1)
        if mode == "stack":
            self.stats_panel = IntensityStatsPanel()

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

        # ── Outer layout: scroll on top, optional draggable stats panel below ──
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(0)
        if self.stats_panel is not None:
            self._left_split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
            self._left_split.setChildrenCollapsible(False)
            self._left_split.setHandleWidth(6)
            self._left_split.addWidget(self._scroll)
            self._left_split.addWidget(self.stats_panel)
            self._left_split.setStretchFactor(0, 3)
            self._left_split.setStretchFactor(1, 1)
            self._left_split.setSizes([560, 320])
            outer.addWidget(self._left_split)
        else:
            outer.addWidget(self._scroll)

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

    # ── live PV stream (Live Data card, allow_live=True only) ───────
    def _on_live_card_toggled(self, checked):
        """The Live Data card's own checkbox collapses/expands its controls.
        Unchecking also stops any active stream — a hidden stream with no
        visible Stop button would otherwise be un-turn-offable."""
        self._live_content.setVisible(checked)
        if not checked:
            self.stop_live()

    def _on_pv_device_picked(self, index):
        """Selecting a known device by name fills in its full live PV
        (prefix + PVA suffix); typing a PV by hand is untouched (this only
        fires on an explicit dropdown pick, not on text edits)."""
        pv = self._pv_ed.itemData(index)
        if pv:
            self._pv_ed.setEditText(pv)

    def _start_live(self):
        try:
            import pvapy  # noqa: F401
        except ImportError:
            QtWidgets.QMessageBox.warning(
                self, "pvapy not installed",
                "pvapy is a required dependency but isn't importable in this "
                "environment.\nReinstall it with:  pip install pvapy==5.4.1")
            return
        pv = self._pv_ed.currentText().strip()
        if not pv:
            QtWidgets.QMessageBox.warning(self, "No PV", "Enter a PV name first.")
            return
        if self._live_src is None:
            self._live_src = PvaLiveSource(self)
            self._live_src.frameReady.connect(self._on_live_frame)
            self._live_src.connectionChanged.connect(self._on_live_connection)
            self._live_src.error.connect(self._on_live_error)
        self._stack = self._paths = self._h5 = None
        self._nframes = 0
        if not self._live_src.start(pv):
            return
        self._live_start_btn.setEnabled(False)
        self._live_stop_btn.setEnabled(True)
        self._pv_ed.setEnabled(False)
        self._live_status_lbl.setText("Waiting for PV…")

    def _on_live_frame(self, image, image_id):
        self._nframes = 1
        self._cur = image
        self._setup_navigator()
        self._live_status_lbl.setText(f"Streaming — frame id {image_id}.")
        self._info.setText(
            f"Live: {self._pv_ed.currentText().strip()}  (id {image_id}, shape {image.shape})")
        self.dataChanged.emit()

    def _on_live_connection(self, is_connected):
        if self._live_src is not None and self._live_src.is_active():
            self._live_status_lbl.setText(
                "Connected — waiting for first frame…" if is_connected
                else "PV not connected.")

    def _on_live_error(self, msg):
        self._live_status_lbl.setText(f"Error: {msg}")
        self.stop_live()

    def stop_live(self):
        """Stop any active live PV stream. No-op on a panel built without
        allow_live, or if never started; safe to call from app shutdown."""
        live_src = getattr(self, "_live_src", None)
        if live_src is not None:
            live_src.stop()
        start_btn = getattr(self, "_live_start_btn", None)
        if start_btn is not None:
            start_btn.setEnabled(True)
            self._live_stop_btn.setEnabled(False)
            self._pv_ed.setEnabled(True)
            self._live_status_lbl.setText("Stopped.")

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

    def average_frames(self, start=0, end=None, step=1):
        """Mean of frames ``start:end:step`` (end None/<=0 = all), streamed one
        frame at a time so large folders / HDF5 stacks stay memory-safe.

        Returns a float32 2-D array, or None if no data / no frames selected.
        """
        if self._nframes == 0:
            return None
        end = self._nframes if (end is None or end <= 0) else min(int(end), self._nframes)
        start = max(0, int(start))
        step = max(1, int(step))
        acc, n = None, 0
        for i in range(start, end, step):
            a = self._get_frame(i).astype(np.float64)
            acc = a if acc is None else acc + a
            n += 1
        if n == 0:
            return None
        return (acc / n).astype(np.float32)

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

    def add_mask_file(self, path):
        """Add a mask file to the mask selector (idempotent) — for wiring a tab default."""
        self._mask_sel.add_file_source(path)

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

    # ── GUI state (Save/Load GUI State) ─────────────────────────────
    def get_state(self) -> dict:
        st = {
            "path": self._path_ed.text(),
            "dataset": self._ds_combo.currentText(),
            "dark": self._dark_sel.get_state(),
            "bright": self._bright_sel.get_state(),
            "background": self._bg_sel.get_state(),
            "mask": self._mask_sel.get_state(),
        }
        if self._mode == "stream":
            st["fr_start"] = self._fr_start.value()
            st["fr_end"] = self._fr_end.value()
            st["fr_stride"] = self._fr_stride.value()
        else:
            st["frame_index"] = self._frame_spin.value()
        if getattr(self, "_pv_ed", None) is not None:
            st["live_pv"] = self._pv_ed.currentText()
        return st

    def set_state(self, state: dict):
        """Restores path/frame/field/mask sub-state, re-triggering the panel's own
        load pipeline via :meth:`set_path` — the one central place that implements
        "auto re-load path-backed data" for every tab that embeds this panel. A
        saved live-PV name is restored into the field but a live connection is
        never auto-started (that's a stateful, non-idempotent action)."""
        if not state:
            return
        path = state.get("path", "")
        if path:
            self.set_path(path, dataset=state.get("dataset"), load=True)
        if self._mode == "stream":
            if "fr_start" in state:
                self._fr_start.setValue(int(state["fr_start"]))
            if "fr_end" in state:
                self._fr_end.setValue(int(state["fr_end"]))
            if "fr_stride" in state:
                self._fr_stride.setValue(int(state["fr_stride"]))
        elif "frame_index" in state and self._nframes:
            self.set_frame(int(state["frame_index"]))
        self._dark_sel.set_state(state.get("dark") or {})
        self._bright_sel.set_state(state.get("bright") or {})
        self._bg_sel.set_state(state.get("background") or {})
        self._mask_sel.set_state(state.get("mask") or {})
        if getattr(self, "_pv_ed", None) is not None and state.get("live_pv"):
            self._pv_ed.setEditText(state["live_pv"])


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
        self._img.setLookupTable(_resolve_cmap(name).getLookupTable(0.0, 1.0, 256))


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
