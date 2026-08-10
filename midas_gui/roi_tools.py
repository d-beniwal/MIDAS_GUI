"""Region-of-interest (ROI) drawing + live stats popups for the Data Viewer.

Draw a box or line on the image; a small floating, user-draggable popup
shows live intensity statistics (box) or an intensity profile (line) for
that region, updating as the shape is dragged/resized or as new frames
arrive. Multiple simultaneous ROIs are distinguished by a shared
shape/popup color and label.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from midas_gui.widgets import PickableImageViewer, _mono_font, _resolve_cmap

# Cycled per new ROI — same palette convention as tab_view._MATERIAL_COLORS
# (duplicated here rather than imported, to avoid a tab_view <-> roi_tools
# circular import).
_ROI_COLORS = ("#f0c060", "#4fc3f7", "#ab47bc", "#66bb6a", "#ef5350",
               "#ffca28", "#26a69a", "#ec407a", "#7e57c2", "#8d6e63")

_MIN_DRAG_PX = 6  # widget-space drag distance below which a draw attempt is cancelled


_ARROW_HEAD_LEN_PX = 16    # arrowhead size in constant screen pixels
_ARROW_HEAD_WIDTH_PX = 10


def _build_arrow_path(p0: QtCore.QPointF, p1: QtCore.QPointF,
                       head_len: float, head_width: float) -> QtGui.QPainterPath:
    """Single path (shaft + filled head, both from p0/p1 directly) so the
    whole line renders as one arrow rather than a shaft plus a separately
    positioned/rotated arrowhead item."""
    path = QtGui.QPainterPath()
    dx, dy = p1.x() - p0.x(), p1.y() - p0.y()
    length = math.hypot(dx, dy)
    if length < 1e-9:
        path.moveTo(p0)
        return path
    ux, uy = dx / length, dy / length     # unit vector along the shaft
    px, py = -uy, ux                      # unit vector perpendicular to it
    hl = min(head_len, length * 0.8)      # don't let the head outgrow a short line
    base = QtCore.QPointF(p1.x() - ux * hl, p1.y() - uy * hl)
    left = QtCore.QPointF(base.x() + px * head_width / 2, base.y() + py * head_width / 2)
    right = QtCore.QPointF(base.x() - px * head_width / 2, base.y() - py * head_width / 2)

    path.moveTo(p0)
    path.lineTo(base)
    path.moveTo(left)
    path.lineTo(p1)
    path.lineTo(right)
    path.lineTo(left)
    path.closeSubpath()
    return path


def _raster_roi_mask(roi, imgitem, shape) -> np.ndarray:
    """Boolean mask (matching `shape`, e.g. (NZ, NY)) of pixels inside a
    pyqtgraph ROI (any shape). Standalone port of MaskTab._raster_roi."""
    NZ, NY = shape
    path = imgitem.mapFromScene(roi.mapToScene(roi.shape()))
    qimg = QtGui.QImage(NY, NZ, QtGui.QImage.Format_Grayscale8)
    qimg.fill(0)
    painter = QtGui.QPainter(qimg)
    painter.setPen(QtCore.Qt.NoPen)
    painter.setBrush(QtGui.QColor(255, 255, 255))
    painter.drawPath(path)
    painter.end()
    bpl = qimg.bytesPerLine()
    ptr = qimg.constBits(); ptr.setsize(NZ * bpl)
    arr = np.frombuffer(ptr, np.uint8).reshape(NZ, bpl)[:, :NY]
    return arr > 127


class ROIStatsPopup(QtWidgets.QDialog):
    """Non-modal, freely-draggable floating window for one ROI.

    A box shows a linear/log intensity histogram plus a zoomed-in crop of
    the ROI region; a line shows an intensity-vs-distance profile plot
    with a flip-direction control.
    """
    removed = QtCore.pyqtSignal()
    labelChanged = QtCore.pyqtSignal(str)
    flipRequested = QtCore.pyqtSignal()
    minimizeRequested = QtCore.pyqtSignal()

    def __init__(self, kind: str, color: str, label: str, parent=None):
        # Qt.Window (not Qt.Tool): a Tool-flagged window is treated as a
        # macOS utility panel and auto-hides whenever the app loses focus.
        # WindowStaysOnTopHint keeps it above every desktop window (not just
        # this app's) while open — otherwise clicking anywhere else buries it.
        super().__init__(parent, QtCore.Qt.Window | QtCore.Qt.WindowStaysOnTopHint)
        self._kind = kind
        self.setModal(False)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, False)
        self.setWindowTitle(label)

        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6); v.setSpacing(4)

        head = QtWidgets.QHBoxLayout(); head.setSpacing(6)
        swatch = QtWidgets.QLabel()
        pix = QtGui.QPixmap(14, 14); pix.fill(QtGui.QColor(color))
        swatch.setPixmap(pix)
        head.addWidget(swatch)
        self._label_ed = QtWidgets.QLineEdit(label)
        self._label_ed.setStyleSheet(f"font-weight:bold; color:{color};")
        self._label_ed.textEdited.connect(self._on_label_edited)
        head.addWidget(self._label_ed, 1)
        min_btn = QtWidgets.QToolButton(); min_btn.setText("–"); min_btn.setFixedWidth(20)
        min_btn.setToolTip("Minimize to the ROI ribbon")
        min_btn.clicked.connect(self.minimizeRequested.emit)
        head.addWidget(min_btn)
        v.addLayout(head)

        if kind == "line":
            self._plot = pg.PlotWidget(background="#2b2e35")
            self._plot.setMinimumSize(110, 65)
            self._plot.setLabel("bottom", "distance (px)", **{"color": "#d0d0d0", "font-size": "9pt"})
            self._plot.setLabel("left", "intensity", **{"color": "#d0d0d0", "font-size": "9pt"})
            for ax in ("bottom", "left"):
                self._plot.getAxis(ax).setTextPen("#c8c8c8")
                self._plot.getAxis(ax).setPen("#8a8a8a")
            self._curve = self._plot.plot([], [], pen=pg.mkPen(color, width=2))
            v.addWidget(self._plot)

            flip_row = QtWidgets.QHBoxLayout()
            self._flip_btn = QtWidgets.QPushButton("Flip direction")
            self._flip_btn.setToolTip(
                "Reverse which end of the line is the profile's x=0 origin.")
            self._flip_btn.clicked.connect(self.flipRequested.emit)
            flip_row.addWidget(self._flip_btn)
            flip_row.addStretch(1)
            v.addLayout(flip_row)

            self._stats_lbl = QtWidgets.QLabel("")
            self._stats_lbl.setFont(_mono_font(9))
            self._stats_lbl.setStyleSheet("color:#d6d6d6;")
            v.addWidget(self._stats_lbl)
            self._hist_plot = None
        else:
            row = QtWidgets.QHBoxLayout(); row.setSpacing(6)

            hist_col = QtWidgets.QVBoxLayout(); hist_col.setSpacing(2)
            self._hist_plot = pg.PlotWidget(background="#2b2e35")
            self._hist_plot.setMinimumSize(85, 55)
            self._hist_plot.setLabel("bottom", "intensity", **{"color": "#d0d0d0", "font-size": "9pt"})
            self._hist_plot.setLabel("left", "count", **{"color": "#d0d0d0", "font-size": "9pt"})
            for ax in ("bottom", "left"):
                self._hist_plot.getAxis(ax).setTextPen("#c8c8c8")
                self._hist_plot.getAxis(ax).setPen("#8a8a8a")
            self._curve = self._hist_plot.plot(
                [], [], stepMode="center", fillLevel=0,
                brush=(90, 140, 220, 150), pen=pg.mkPen(color))
            hist_col.addWidget(self._hist_plot)

            self._logy_chk = QtWidgets.QCheckBox("log y")   # linear by default
            self._logy_chk.toggled.connect(self._redraw_hist)
            hist_col.addWidget(self._logy_chk)
            row.addLayout(hist_col, 1)

            crop_view = pg.PlotWidget(background="#2b2e35")
            crop_view.setMinimumSize(90, 90)
            crop_view.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                     QtWidgets.QSizePolicy.Expanding)
            crop_view.showAxis("bottom", False); crop_view.showAxis("left", False)
            crop_view.setMouseEnabled(x=False, y=False)
            self._crop_vb = crop_view.getViewBox()
            self._crop_vb.setAspectLocked(True)
            self._crop_vb.setMenuEnabled(False)
            # Match the main image viewer's convention (pg.ImageView calls
            # view.invertY() so row 0 sits at the top / origin top-left);
            # this plain PlotWidget defaults to Y-up, which flipped the crop
            # vertically relative to what's drawn in the data viewer.
            self._crop_vb.invertY(True)
            self._crop_img = pg.ImageItem()
            crop_view.addItem(self._crop_img)
            # Stretch factor matches hist_col's so dragging the popup's edge
            # to resize it (a plain QDialog, resizable by default) grows the
            # crop image along with the histogram instead of leaving it
            # pinned at its old fixed 110x110 footprint.
            row.addWidget(crop_view, 1)

            v.addLayout(row)
            self._stats_lbl = None
            self._hist_cache = None

        # pyqtgraph's PlotWidget reports a large default sizeHint regardless
        # of setMinimumSize() above, so self.sizeHint() alone would still
        # size this popup at its old, much larger footprint. Shrink to ~50%
        # of that natural size instead, floored at what the layout actually
        # needs (crop view + our reduced plot minimums) so nothing clips.
        target = self.sizeHint() * 0.5
        self.resize(target.expandedTo(self.minimumSizeHint()))

    def _on_label_edited(self, text: str):
        self.setWindowTitle(text)
        self.labelChanged.emit(text)

    def set_label(self, text: str):
        self._label_ed.blockSignals(True)
        self._label_ed.setText(text)
        self._label_ed.blockSignals(False)
        self.setWindowTitle(text)

    def set_area_data(self, values: np.ndarray, crop: Optional[np.ndarray],
                       cmap_name: str, log: bool):
        if self._hist_plot is None:
            return
        vals = np.asarray(values, dtype=np.float64).ravel()
        vals = vals[np.isfinite(vals)]
        if vals.size:
            vmin, vmax = float(vals.min()), float(vals.max())
            if vmax <= vmin:
                vmax = vmin + 1.0
            self._hist_cache = np.histogram(vals, bins=128, range=(vmin, vmax))
        else:
            self._hist_cache = None
        self._redraw_hist()

        if crop is not None and crop.size:
            disp = np.log10(np.clip(crop, 1e-10, None)) if log else crop
            self._crop_img.setImage(disp.astype(np.float32), autoLevels=True)
            self._crop_img.setColorMap(_resolve_cmap(cmap_name))
            self._crop_vb.autoRange()

    def _redraw_hist(self, *_):
        if self._hist_cache is None:
            self._curve.setData([], [])
            return
        counts, edges = self._hist_cache
        y = counts.astype(float)
        log = self._logy_chk.isChecked()
        if log:
            y = np.log10(y + 1.0)
        self._curve.setData(edges, y)
        self._hist_plot.setLabel("left", "log(count+1)" if log else "count",
                                 **{"color": "#d0d0d0", "font-size": "9pt"})

    def set_line_profile(self, dist: np.ndarray, vals: np.ndarray):
        if self._curve is None:
            return
        self._curve.setData(dist, vals)
        if vals.size:
            self._stats_lbl.setText(
                f"N = {vals.size:,}    min = {np.nanmin(vals):.6g}    "
                f"max = {np.nanmax(vals):.6g}    mean = {np.nanmean(vals):.6g}")
        else:
            self._stats_lbl.setText("(no samples)")

    def closeEvent(self, event):
        self.removed.emit()
        super().closeEvent(event)


class _VerticalLabel(QtWidgets.QWidget):
    """Small bottom-anchored widget that paints `text` rotated 90° so it
    reads bottom-to-top, matching the ribbon's vertical orientation."""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self._text = text
        self.setFixedHeight(64)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setPen(QtGui.QColor("#f5f5f5"))
        font = painter.font()
        font.setBold(True)
        # The app's global stylesheet sets font-size in px (style.py), which
        # makes QFont.pointSize() come back -1 — bumping *that* produced an
        # illegible ~1pt font. Force an explicit pixel size instead.
        font.setPixelSize(14)
        painter.setFont(font)
        painter.translate(0, self.height())
        painter.rotate(-90)
        painter.drawText(QtCore.QRect(0, 0, self.height(), self.width()),
                          QtCore.Qt.AlignCenter, self._text)
        painter.end()


class ROIRibbon(QtWidgets.QWidget):
    """Fixed-width vertical strip of small colored buttons, one per minimized
    ROI popup. Lives to the left of the image viewer (see tab_view.py) so an
    always-on-top popup that's in the way can be tucked away without losing
    it — clicking its ribbon entry restores it.

    Visually set off from the viewer (background tint + right border) so the
    strip reads as its own zone. Entries stack from the bottom upward — the
    first ROI minimized lands just above the "ROIs" caption, later ones pile
    on top of it — with unused space collapsing to the top via a stretch."""

    restoreRequested = QtCore.pyqtSignal(object)   # emits the ROIStatsPopup

    _ENTRY_INSERT_INDEX = 1   # right after the top stretch — newest entry highest

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(26)
        self.setObjectName("roiRibbon")
        self.setStyleSheet(
            "QWidget#roiRibbon { background-color: #1c1c1c; "
            "border-right: 1px solid #444; }")
        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(2, 4, 2, 0); self._layout.setSpacing(2)
        self._layout.addStretch(1)
        self._layout.addWidget(_VerticalLabel("ROIs"))
        self._layout.addSpacing(8)   # offset of the caption from the bottom edge
        self._buttons: dict = {}   # popup -> QToolButton

    def add_entry(self, popup, label: str, color: str):
        btn = QtWidgets.QToolButton()
        btn.setFixedSize(22, 22)
        btn.setToolTip(label)
        btn.setStyleSheet(
            f"QToolButton {{ background:{color}; border:1px solid #1a1a1a; border-radius:3px; }}")
        btn.clicked.connect(lambda: self.restoreRequested.emit(popup))
        self._layout.insertWidget(self._ENTRY_INSERT_INDEX, btn)
        self._buttons[popup] = btn

    def remove_entry(self, popup):
        btn = self._buttons.pop(popup, None)
        if btn is not None:
            self._layout.removeWidget(btn)
            btn.deleteLater()

    def update_label(self, popup, label: str):
        btn = self._buttons.get(popup)
        if btn is not None:
            btn.setToolTip(label)


class ROIImageViewer(PickableImageViewer):
    """PickableImageViewer + draw-a-box/line ROI tool with live,
    freely-positioned stats popups. Used only by the Data Viewer tab (kept
    out of the shared PickableImageViewer base so other tabs, e.g.
    Calibrate, aren't affected)."""

    PICK_ROI_BOX    = 10
    PICK_ROI_LINE   = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        self._roi_entries: list = []
        self._roi_counter = 0
        self._ribbon: Optional["ROIRibbon"] = None
        self._rubber_band: Optional[QtWidgets.QRubberBand] = None
        self._drag_origin: Optional[QtCore.QPoint] = None
        self._roi_draw_kind: Optional[str] = None

        _BTN = ("QPushButton{padding:2px 8px;border-radius:3px}"
                "QPushButton:checked{background:#2a7fd4;color:white;font-weight:bold}")
        # Appended onto the existing Pick BC/Pick Ring row (rather than a new
        # row of our own) — its trailing stretch pins these to the right edge,
        # separated from the pick tools by a vline.
        pick_bar = self.layout().itemAt(1).layout()
        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.VLine)
        sep.setFrameShadow(QtWidgets.QFrame.Sunken)
        pick_bar.addWidget(sep)
        pick_bar.addWidget(QtWidgets.QLabel("ROI:"))

        self._roi_box_btn = QtWidgets.QPushButton("Box")
        self._roi_line_btn = QtWidgets.QPushButton("Line")
        self._roi_mode_btns = {
            self._roi_box_btn: ("box", self.PICK_ROI_BOX),
            self._roi_line_btn: ("line", self.PICK_ROI_LINE),
        }
        for btn, (kind, mode) in self._roi_mode_btns.items():
            btn.setCheckable(True)
            btn.setStyleSheet(_BTN)
            btn.setToolTip(f"Click, then drag on the image to draw a {kind} ROI.")
            btn.toggled.connect(lambda on, k=kind, m=mode: self._on_roi_mode_toggled(on, k, m))
            pick_bar.addWidget(btn)

        self._roi_clear_btn = QtWidgets.QPushButton("Clear ROIs")
        self._roi_clear_btn.setToolTip("Remove all ROIs and their popups.")
        self._roi_clear_btn.clicked.connect(self._clear_all_rois)
        pick_bar.addWidget(self._roi_clear_btn)

        self._iv.ui.graphicsView.viewport().installEventFilter(self)

    def set_ribbon(self, ribbon: "ROIRibbon"):
        """Wire this viewer's minimized-ROI entries into `ribbon` (created and
        placed alongside the viewer by tab_view.py)."""
        self._ribbon = ribbon
        ribbon.restoreRequested.connect(self._on_roi_restore)

    # ── mode arming ──────────────────────────────────────────────

    def _on_pick_bc_toggled(self, checked: bool):
        super()._on_pick_bc_toggled(checked)
        if checked:
            self._uncheck_roi_buttons()

    def _on_pick_ring_toggled(self, checked: bool):
        super()._on_pick_ring_toggled(checked)
        if checked:
            self._uncheck_roi_buttons()

    def _uncheck_roi_buttons(self):
        for btn in self._roi_mode_btns:
            if btn.isChecked():
                btn.blockSignals(True); btn.setChecked(False); btn.blockSignals(False)
        self._roi_draw_kind = None

    def _on_roi_mode_toggled(self, checked: bool, kind: str, mode: int):
        if checked:
            for btn, (k, _m) in self._roi_mode_btns.items():
                if k != kind and btn.isChecked():
                    btn.blockSignals(True); btn.setChecked(False); btn.blockSignals(False)
            self._pick_bc_btn.blockSignals(True); self._pick_bc_btn.setChecked(False)
            self._pick_bc_btn.blockSignals(False)
            self._pick_ring_btn.blockSignals(True); self._pick_ring_btn.setChecked(False)
            self._pick_ring_btn.blockSignals(False)
            self._pick_mode = mode
            self._roi_draw_kind = kind
            self._pick_status.setText(f"Drag on the image to draw a {kind} ROI")
        elif self._roi_draw_kind == kind:
            self._pick_mode = self.PICK_NONE
            self._roi_draw_kind = None
            self._pick_status.setText("")

    # ── drag-to-create via QRubberBand ──────────────────────────

    def eventFilter(self, obj, event):
        if self._roi_draw_kind is not None:
            et = event.type()
            if et == QtCore.QEvent.MouseButtonPress and event.button() == QtCore.Qt.LeftButton:
                self._drag_origin = event.pos()
                shape = (QtWidgets.QRubberBand.Line if self._roi_draw_kind == "line"
                         else QtWidgets.QRubberBand.Rectangle)
                self._rubber_band = QtWidgets.QRubberBand(shape, obj)
                self._rubber_band.setGeometry(QtCore.QRect(self._drag_origin, QtCore.QSize()))
                self._rubber_band.show()
                return True
            if et == QtCore.QEvent.MouseMove and self._rubber_band is not None:
                self._rubber_band.setGeometry(
                    QtCore.QRect(self._drag_origin, event.pos()).normalized())
                return True
            if et == QtCore.QEvent.MouseButtonRelease and self._rubber_band is not None:
                geom = QtCore.QRect(self._drag_origin, event.pos()).normalized()
                self._rubber_band.hide()
                self._rubber_band = None
                kind = self._roi_draw_kind
                start, end = self._drag_origin, event.pos()
                self._drag_origin = None
                if (geom.width() < _MIN_DRAG_PX and geom.height() < _MIN_DRAG_PX):
                    return True  # too-small drag: cancelled attempt, mode stays armed
                self._finish_roi_draw(obj, kind, start, end)
                for btn, (k, _m) in self._roi_mode_btns.items():
                    if k == kind:
                        btn.blockSignals(True); btn.setChecked(False); btn.blockSignals(False)
                self._pick_mode = self.PICK_NONE
                self._roi_draw_kind = None
                self._pick_status.setText("")
                return True
        return super().eventFilter(obj, event)

    def _finish_roi_draw(self, viewport, kind: str, start: QtCore.QPoint, end: QtCore.QPoint):
        vb = self._iv.getView().getViewBox()
        view = self._iv.ui.graphicsView
        p0 = vb.mapSceneToView(view.mapToScene(start))
        p1 = vb.mapSceneToView(view.mapToScene(end))
        x0, y0, x1, y1 = p0.x(), p0.y(), p1.x(), p1.y()
        color = _ROI_COLORS[self._roi_counter % len(_ROI_COLORS)]
        pen = pg.mkPen(color, width=2)

        if kind == "box":
            pos = (min(x0, x1), min(y0, y1))
            size = (max(abs(x1 - x0), 1e-3), max(abs(y1 - y0), 1e-3))
            roi = pg.RectROI(pos, size, pen=pen, rotatable=False)
        else:  # line
            roi = pg.LineSegmentROI([(x0, y0), (x1, y1)], pen=pen)

        self._register_roi(roi, kind, color)

    # ── registration / stats wiring ──────────────────────────────

    def _register_roi(self, roi, kind: str, color: str):
        self._roi_counter += 1
        label = f"ROI {self._roi_counter}"
        roi.setZValue(20)
        roi.removable = True
        roi.sigRemoveRequested.connect(lambda r=roi: self._remove_roi_by_item(r))
        self._iv.addItem(roi)

        anchor = roi.pos()
        label_item = pg.TextItem(text=label, color=color, anchor=(0, 1))
        label_item.setPos(anchor)
        label_item.setZValue(21)
        self._iv.addItem(label_item)

        arrow = None
        if kind == "line":
            # The whole line is drawn as a single arrow shape (see
            # _update_line_arrow / _build_arrow_path) — hide the ROI's own
            # connecting line so it isn't drawn a second time underneath.
            roi.setPen(pg.mkPen(None))
            arrow = QtWidgets.QGraphicsPathItem()
            arrow.setPen(pg.mkPen(color, width=2))
            arrow.setBrush(pg.mkBrush(color))
            arrow.setZValue(21)
            self._iv.addItem(arrow, ignoreBounds=True)

        popup = ROIStatsPopup(kind, color, label, parent=self)
        entry = {
            "kind": kind, "roi": roi, "color": color, "label": label,
            "label_item": label_item, "arrow": arrow, "popup": popup,
            "flipped": False, "removing": False, "minimized": False,
        }
        self._roi_entries.append(entry)

        popup.removed.connect(lambda e=entry: self._remove_roi(e))
        popup.labelChanged.connect(lambda text, e=entry: self._on_roi_label_changed(e, text))
        popup.flipRequested.connect(lambda e=entry: self._on_roi_flip(e))
        popup.minimizeRequested.connect(lambda e=entry: self._on_roi_minimize(e))
        roi.sigRegionChanged.connect(lambda *_: self._on_roi_geom_changed(entry))

        self._position_popup_near(popup, roi)
        popup.show()
        self._refresh_roi_stats(entry)

    def _position_popup_near(self, popup: QtWidgets.QDialog, roi):
        view = self._iv.ui.graphicsView
        scene_rect = roi.sceneBoundingRect()
        top_right = view.mapFromScene(scene_rect.topRight())
        global_pt = view.viewport().mapToGlobal(top_right)
        global_pt = QtCore.QPoint(global_pt.x() + 16, global_pt.y())

        screen = QtWidgets.QApplication.screenAt(global_pt) or QtWidgets.QApplication.primaryScreen()
        # Not popup.adjustSize(): that resets to the popup's natural (large)
        # sizeHint(), undoing the ~50% shrink applied in ROIStatsPopup.__init__.
        if screen is not None:
            avail = screen.availableGeometry()
            x = min(max(global_pt.x(), avail.left()), avail.right() - popup.width())
            y = min(max(global_pt.y(), avail.top()), avail.bottom() - popup.height())
            global_pt = QtCore.QPoint(x, y)
        popup.move(global_pt)

    def _on_roi_geom_changed(self, entry: dict):
        label_item = entry["label_item"]
        label_item.setPos(entry["roi"].pos())
        self._refresh_roi_stats(entry)

    def _on_roi_label_changed(self, entry: dict, text: str):
        entry["label"] = text
        entry["label_item"].setText(text)
        if entry["minimized"] and self._ribbon is not None:
            self._ribbon.update_label(entry["popup"], text)

    def _on_roi_minimize(self, entry: dict):
        if entry["minimized"] or self._ribbon is None:
            return
        entry["minimized"] = True
        entry["popup"].hide()
        self._ribbon.add_entry(entry["popup"], entry["label"], entry["color"])

    def _on_roi_restore(self, popup):
        for entry in self._roi_entries:
            if entry["popup"] is popup:
                entry["minimized"] = False
                if self._ribbon is not None:
                    self._ribbon.remove_entry(popup)
                popup.show()
                popup.raise_()
                popup.activateWindow()
                return

    def _on_roi_flip(self, entry: dict):
        entry["flipped"] = not entry["flipped"]
        self._refresh_roi_stats(entry)

    # ── stats computation ─────────────────────────────────────────

    def _refresh_roi_stats(self, entry: dict):
        if self._data is None:
            return
        roi = entry["roi"]
        imgitem = self._iv.getImageItem()
        if entry["kind"] == "line":
            try:
                # getArrayRegion samples against imgitem's own array orientation,
                # which is the transposed display array (see ImageViewer._redisplay),
                # not self._data's (NZ, NY) raw orientation.
                vals = roi.getArrayRegion(self._data.T, imgitem)
            except Exception:
                return
            if vals is None:
                return
            vals = np.asarray(vals).ravel()
            if entry["flipped"]:
                vals = vals[::-1]
            dist = np.arange(vals.size, dtype=float)
            entry["popup"].set_line_profile(dist, vals)
            self._update_line_arrow(entry)
        else:
            mask = _raster_roi_mask(roi, imgitem, self._data.shape)
            try:
                crop = roi.getArrayRegion(self._data.T, imgitem)
            except Exception:
                crop = None
            entry["popup"].set_area_data(
                self._data[mask], crop,
                cmap_name=self._cmap.currentText(), log=self._log.isChecked())

    def _update_line_arrow(self, entry: dict):
        arrow = entry["arrow"]
        if arrow is None:
            return
        pts = entry["roi"].listPoints()
        if len(pts) < 2:
            return
        p0, p1 = (pts[1], pts[0]) if entry["flipped"] else (pts[0], pts[1])
        # mapToParent (not mapToScene): `arrow` lives in the ViewBox's own
        # coordinate frame (added via self._iv.addItem), same as `roi` and
        # `label_item` — mapping to scene coordinates here placed the arrow
        # far from the line.
        view_p0 = entry["roi"].mapToParent(p0)
        view_p1 = entry["roi"].mapToParent(p1)
        vb = self._iv.getView().getViewBox()
        px_size = vb.viewPixelSize()[0] or 1.0
        arrow.setPath(_build_arrow_path(
            view_p0, view_p1,
            head_len=_ARROW_HEAD_LEN_PX * px_size,
            head_width=_ARROW_HEAD_WIDTH_PX * px_size))

    def _refresh_all_roi_stats(self):
        for entry in self._roi_entries:
            self._refresh_roi_stats(entry)

    # ── removal ───────────────────────────────────────────────────

    def _remove_roi_by_item(self, roi):
        for entry in list(self._roi_entries):
            if entry["roi"] is roi:
                self._remove_roi(entry)
                return

    def _remove_roi(self, entry: dict):
        if entry.get("removing") or entry not in self._roi_entries:
            return
        entry["removing"] = True
        if entry["minimized"] and self._ribbon is not None:
            self._ribbon.remove_entry(entry["popup"])
        self._iv.removeItem(entry["roi"])
        self._iv.removeItem(entry["label_item"])
        if entry["arrow"] is not None:
            self._iv.removeItem(entry["arrow"])
        entry["popup"].close()
        self._roi_entries.remove(entry)

    def _clear_all_rois(self):
        for entry in list(self._roi_entries):
            self._remove_roi(entry)
        self._roi_counter = 0

    # ── frame refresh hook ───────────────────────────────────────

    def set_image(self, data: np.ndarray, autorange: bool = True, reset_levels: bool = True):
        super().set_image(data, autorange=autorange, reset_levels=reset_levels)
        self._refresh_all_roi_stats()
