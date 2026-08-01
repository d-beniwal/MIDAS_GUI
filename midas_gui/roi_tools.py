"""Region-of-interest (ROI) drawing + live stats popups for the Data Viewer.

Draw a box, circle, or line on the image; a small floating, user-draggable
popup shows live intensity statistics (box/circle) or an intensity profile
(line) for that region, updating as the shape is dragged/resized or as new
frames arrive. Multiple simultaneous ROIs are distinguished by a shared
shape/popup color and label.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from midas_gui.widgets import PickableImageViewer, IntensityStatsPanel, _mono_font

# Cycled per new ROI — same palette convention as tab_view._MATERIAL_COLORS
# (duplicated here rather than imported, to avoid a tab_view <-> roi_tools
# circular import).
_ROI_COLORS = ("#f0c060", "#4fc3f7", "#ab47bc", "#66bb6a", "#ef5350",
               "#ffca28", "#26a69a", "#ec407a", "#7e57c2", "#8d6e63")

_MIN_DRAG_PX = 6  # widget-space drag distance below which a draw attempt is cancelled


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
    """Non-modal, freely-draggable floating stats window for one ROI.

    Box/circle show full intensity statistics (via an embedded
    IntensityStatsPanel); a line shows an intensity-vs-distance profile
    plot with a flip-direction control.
    """
    removed = QtCore.pyqtSignal()
    labelChanged = QtCore.pyqtSignal(str)
    flipRequested = QtCore.pyqtSignal()

    def __init__(self, kind: str, color: str, label: str, parent=None):
        super().__init__(parent, QtCore.Qt.Tool)
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
        v.addLayout(head)

        if kind == "line":
            self._plot = pg.PlotWidget(background="#2b2e35")
            self._plot.setMinimumSize(280, 160)
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
            self._panel = None
        else:
            self._panel = IntensityStatsPanel()
            self._panel.set_scope_enabled(False)
            v.addWidget(self._panel)
            self._curve = None
            self._stats_lbl = None

        self.resize(self.sizeHint())

    def _on_label_edited(self, text: str):
        self.setWindowTitle(text)
        self.labelChanged.emit(text)

    def set_label(self, text: str):
        self._label_ed.blockSignals(True)
        self._label_ed.setText(text)
        self._label_ed.blockSignals(False)
        self.setWindowTitle(text)

    def set_area_stats(self, values: np.ndarray, scope: str):
        if self._panel is not None:
            self._panel.set_data(values, scope=scope)

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


class ROIImageViewer(PickableImageViewer):
    """PickableImageViewer + draw-a-box/circle/line ROI tool with live,
    freely-positioned stats popups. Used only by the Data Viewer tab (kept
    out of the shared PickableImageViewer base so other tabs, e.g.
    Calibrate, aren't affected)."""

    PICK_ROI_BOX    = 10
    PICK_ROI_CIRCLE = 11
    PICK_ROI_LINE   = 12

    def __init__(self, parent=None):
        super().__init__(parent)
        self._roi_entries: list = []
        self._roi_counter = 0
        self._rubber_band: Optional[QtWidgets.QRubberBand] = None
        self._drag_origin: Optional[QtCore.QPoint] = None
        self._roi_draw_kind: Optional[str] = None

        _BTN = ("QPushButton{padding:2px 8px;border-radius:3px}"
                "QPushButton:checked{background:#2a7fd4;color:white;font-weight:bold}")
        roi_bar = QtWidgets.QHBoxLayout()
        roi_bar.setSpacing(4)
        roi_bar.addWidget(QtWidgets.QLabel("ROI:"))

        self._roi_box_btn = QtWidgets.QPushButton("Box")
        self._roi_circle_btn = QtWidgets.QPushButton("Circle")
        self._roi_line_btn = QtWidgets.QPushButton("Line")
        self._roi_mode_btns = {
            self._roi_box_btn: ("box", self.PICK_ROI_BOX),
            self._roi_circle_btn: ("circle", self.PICK_ROI_CIRCLE),
            self._roi_line_btn: ("line", self.PICK_ROI_LINE),
        }
        for btn, (kind, mode) in self._roi_mode_btns.items():
            btn.setCheckable(True)
            btn.setStyleSheet(_BTN)
            btn.setToolTip(f"Click, then drag on the image to draw a {kind} ROI.")
            btn.toggled.connect(lambda on, k=kind, m=mode: self._on_roi_mode_toggled(on, k, m))
            roi_bar.addWidget(btn)

        self._roi_clear_btn = QtWidgets.QPushButton("Clear ROIs")
        self._roi_clear_btn.setToolTip("Remove all ROIs and their popups.")
        self._roi_clear_btn.clicked.connect(self._clear_all_rois)
        roi_bar.addWidget(self._roi_clear_btn)
        roi_bar.addStretch(1)
        self.layout().insertLayout(2, roi_bar)  # after main toolbar + pick bar

        self._iv.ui.graphicsView.viewport().installEventFilter(self)

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
        elif kind == "circle":
            d = max(abs(x1 - x0), abs(y1 - y0), 1e-3)
            pos = (min(x0, x1), min(y0, y1))
            roi = pg.CircleROI(pos, (d, d), pen=pen)
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
            arrow = pg.ArrowItem(angle=0, brush=color, pen=pg.mkPen(color))
            arrow.setZValue(21)
            self._iv.addItem(arrow)

        popup = ROIStatsPopup(kind, color, label, parent=self)
        entry = {
            "kind": kind, "roi": roi, "color": color, "label": label,
            "label_item": label_item, "arrow": arrow, "popup": popup,
            "flipped": False, "removing": False,
        }
        self._roi_entries.append(entry)

        popup.removed.connect(lambda e=entry: self._remove_roi(e))
        popup.labelChanged.connect(lambda text, e=entry: self._on_roi_label_changed(e, text))
        popup.flipRequested.connect(lambda e=entry: self._on_roi_flip(e))
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
        popup.adjustSize()
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
            entry["popup"].set_area_stats(self._data[mask], scope=entry["label"])

    def _update_line_arrow(self, entry: dict):
        arrow = entry["arrow"]
        if arrow is None:
            return
        pts = entry["roi"].listPoints()
        if len(pts) < 2:
            return
        p0, p1 = (pts[1], pts[0]) if entry["flipped"] else (pts[0], pts[1])
        scene_p0 = entry["roi"].mapToScene(p0)
        scene_p1 = entry["roi"].mapToScene(p1)
        dx, dy = scene_p1.x() - scene_p0.x(), scene_p1.y() - scene_p0.y()
        angle = 180.0 - math.degrees(math.atan2(dy, dx))
        arrow.setStyle(angle=angle)
        arrow.setPos(scene_p1)

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
        self._iv.removeItem(entry["roi"])
        self._iv.removeItem(entry["label_item"])
        if entry["arrow"] is not None:
            self._iv.removeItem(entry["arrow"])
        entry["popup"].close()
        self._roi_entries.remove(entry)

    def _clear_all_rois(self):
        for entry in list(self._roi_entries):
            self._remove_roi(entry)

    # ── frame refresh hook ───────────────────────────────────────

    def set_image(self, data: np.ndarray, autorange: bool = True, reset_levels: bool = True):
        super().set_image(data, autorange=autorange, reset_levels=reset_levels)
        self._refresh_all_roi_stats()
