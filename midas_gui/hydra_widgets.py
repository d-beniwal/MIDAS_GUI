"""UI building blocks for the Data Viewer tab's Hydra (4-panel GE) mode.

``HydraModeRibbon`` is the leftmost strip of the whole Data Viewer tab —
it switches the tab between the existing single-detector view and the new
Hydra view. ``HydraLoaderPanel`` and ``HydraDetectorToolbar`` are the
Hydra page's own loader and image-toolbar widgets. A multi-curve profile
viewer is added to this module in a later phase.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import math

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from midas_gui.helpers import _browse, _NoScrollSpinBox, _NoScrollComboBox, hydra_siblings
from midas_gui import hydra
from midas_gui import style as S
from midas_gui.widgets import _convert_radial, _XUNIT_LABEL


class _VerticalToggleButton(QtWidgets.QAbstractButton):
    """Checkable button whose label is painted rotated 90° (reads
    bottom-to-top), for a narrow vertical mode-switch ribbon. Custom-painted
    (rather than a styled QToolButton) so rotated text stays legible under
    the app's global stylesheet — mirrors ``roi_tools._VerticalLabel``."""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self._text = text
        self.setCheckable(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFixedWidth(32)
        self.setAttribute(QtCore.Qt.WA_Hover, True)

    def sizeHint(self) -> QtCore.QSize:
        return QtCore.QSize(32, 120)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        if self.isChecked():
            bg = "#2e7d32"
        elif self.underMouse():
            bg = "#333333"
        else:
            bg = "#1c1c1c"
        painter.fillRect(self.rect(), QtGui.QColor(bg))
        painter.setPen(QtGui.QColor("#f5f5f5"))
        font = painter.font()
        font.setBold(True)
        font.setPixelSize(12)
        painter.setFont(font)
        painter.translate(0, self.height())
        painter.rotate(-90)
        painter.drawText(QtCore.QRect(0, 0, self.height(), self.width()),
                          QtCore.Qt.AlignCenter, self._text)
        painter.end()

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)


class HydraModeRibbon(QtWidgets.QWidget):
    """Leftmost vertical strip of the Data Viewer tab. Two exclusive modes:
    "Single detector" (today's existing view, unchanged) and "Hydra" (the
    new 4-panel GE detector view)."""

    modeChanged = QtCore.pyqtSignal(str)   # "single" | "hydra"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(32)
        self.setObjectName("hydraModeRibbon")
        self.setStyleSheet(
            "QWidget#hydraModeRibbon { background-color: #1c1c1c; "
            "border-right: 1px solid #444; }")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(2)
        self._single_btn = _VerticalToggleButton("Single detector")
        self._single_btn.setToolTip("Single-detector data viewer")
        self._hydra_btn = _VerticalToggleButton("Hydra")
        self._hydra_btn.setToolTip("Hydra 4-panel GE detector viewer")
        self._single_btn.setChecked(True)
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.addButton(self._single_btn)
        self._group.addButton(self._hydra_btn)
        layout.addWidget(self._single_btn)
        layout.addWidget(self._hydra_btn)
        layout.addStretch(1)
        self._single_btn.toggled.connect(self._on_toggled)
        self._hydra_btn.toggled.connect(self._on_toggled)

    def _on_toggled(self, checked: bool):
        if not checked:
            return
        self.modeChanged.emit("hydra" if self.sender() is self._hydra_btn else "single")

    def mode(self) -> str:
        return "hydra" if self._hydra_btn.isChecked() else "single"

    def set_mode(self, mode: str):
        (self._hydra_btn if mode == "hydra" else self._single_btn).setChecked(True)


class HydraLoaderPanel(QtWidgets.QWidget):
    """Left-hand loader for the Hydra page. One path field — point it at any
    single GE panel's file — auto-discovers the other panels via
    ``helpers.hydra_siblings``, plus a frame navigator shared across all
    panels (they are synchronized frames of the same scan)."""

    siblingsChanged = QtCore.pyqtSignal(dict)   # {panel_num: path}, may be {}
    frameChanged = QtCore.pyqtSignal(int)

    #: Dataset key fixed for v1 — every bundled/real Hydra HDF5 file used so
    #: far shares this convention (see hydra_default_geometry's callers).
    DATASET = "exchange/data"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._siblings: dict = {}
        self._n_frames = 1
        self._frame = 0
        self._build_ui()

    def _build_ui(self):
        lv = QtWidgets.QVBoxLayout(self)
        lv.setContentsMargins(0, 0, 0, 0)
        card = S.make_card("Hydra data")

        self._path_ed = QtWidgets.QLineEdit()
        self._path_ed.setPlaceholderText("Any one ge1-ge4 panel file…")
        self._path_ed.returnPressed.connect(
            lambda: self._set_path(self._path_ed.text().strip()))
        row = QtWidgets.QHBoxLayout(); row.setSpacing(4)
        row.addWidget(self._path_ed)
        browse_btn = QtWidgets.QPushButton("…"); browse_btn.setFixedWidth(30)
        browse_btn.clicked.connect(self._browse)
        row.addWidget(browse_btn)
        card.body.addLayout(row)

        self._status_lbls = {}
        status_row = QtWidgets.QHBoxLayout(); status_row.setSpacing(6)
        for n in (1, 2, 3, 4):
            lbl = QtWidgets.QLabel(f"ge{n}")
            lbl.setStyleSheet(self._status_style(False))
            status_row.addWidget(lbl)
            self._status_lbls[n] = lbl
        status_row.addStretch(1)
        card.body.addLayout(status_row)

        nav_row = QtWidgets.QHBoxLayout(); nav_row.setSpacing(4)
        self._prev_btn = QtWidgets.QPushButton("◀"); self._prev_btn.setFixedWidth(28)
        self._next_btn = QtWidgets.QPushButton("▶"); self._next_btn.setFixedWidth(28)
        self._frame_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._frame_spin = _NoScrollSpinBox(); self._frame_spin.setFixedWidth(60)
        self._prev_btn.clicked.connect(lambda: self._set_frame(self._frame - 1))
        self._next_btn.clicked.connect(lambda: self._set_frame(self._frame + 1))
        self._frame_slider.valueChanged.connect(
            lambda v: self._set_frame(v, from_widget="slider"))
        self._frame_spin.valueChanged.connect(
            lambda v: self._set_frame(v, from_widget="spin"))
        nav_row.addWidget(self._prev_btn)
        nav_row.addWidget(self._frame_slider, 1)
        nav_row.addWidget(self._frame_spin)
        nav_row.addWidget(self._next_btn)
        card.body.addLayout(nav_row)
        self._set_nav_enabled(False)

        self._info_lbl = QtWidgets.QLabel("")
        self._info_lbl.setStyleSheet(f"color:{S.MUTED};font-size:10px")
        self._info_lbl.setWordWrap(True)
        card.body.addWidget(self._info_lbl)

        lv.addWidget(card)
        lv.addStretch(1)

    @staticmethod
    def _status_style(found: bool) -> str:
        color = "#66bb6a" if found else "#666"
        weight = "bold" if found else "normal"
        return f"color:{color}; font-weight:{weight};"

    def _set_nav_enabled(self, enabled: bool):
        for w in (self._prev_btn, self._next_btn, self._frame_slider, self._frame_spin):
            w.setEnabled(enabled)

    def _browse(self):
        p = _browse(self, "Open a Hydra GE panel file",
                   "Data (*.tif *.tiff *.h5 *.hdf5 *.hdf *.nxs *.ge*);;All (*)")
        if p:
            self._set_path(p)

    def _set_path(self, path: str):
        if not path:
            return
        self._path_ed.setText(path)
        siblings = hydra_siblings(path)
        self._siblings = siblings
        for n, lbl in self._status_lbls.items():
            lbl.setStyleSheet(self._status_style(n in siblings))
        if len(siblings) < 2:
            self._info_lbl.setText(
                "Fewer than 2 Hydra panels found next to this file — check the path.")
            self._set_nav_enabled(False)
            self._n_frames = 1
            self.siblingsChanged.emit({})
            return
        try:
            first_path = next(iter(siblings.values()))
            self._n_frames = hydra.n_frames_in(first_path, self.DATASET)
        except Exception as exc:
            self._info_lbl.setText(f"Could not read frame count: {exc}")
            self._n_frames = 1
        self._frame_slider.blockSignals(True)
        self._frame_slider.setRange(0, max(0, self._n_frames - 1))
        self._frame_slider.blockSignals(False)
        self._frame_spin.blockSignals(True)
        self._frame_spin.setRange(0, max(0, self._n_frames - 1))
        self._frame_spin.blockSignals(False)
        self._frame = 0
        self._set_nav_enabled(self._n_frames > 1)
        self._info_lbl.setText(f"Found {len(siblings)}/4 panels  ·  {self._n_frames} frame(s)")
        self.siblingsChanged.emit(siblings)
        self.frameChanged.emit(0)

    def _set_frame(self, i: int, from_widget: Optional[str] = None):
        i = max(0, min(int(i), max(0, self._n_frames - 1)))
        changed = (i != self._frame)
        self._frame = i
        if from_widget != "slider":
            self._frame_slider.blockSignals(True); self._frame_slider.setValue(i)
            self._frame_slider.blockSignals(False)
        if from_widget != "spin":
            self._frame_spin.blockSignals(True); self._frame_spin.setValue(i)
            self._frame_spin.blockSignals(False)
        if changed:
            self.frameChanged.emit(i)

    # ── Public accessors ─────────────────────────────────────────
    def siblings(self) -> dict:
        return dict(self._siblings)

    def frame_index(self) -> int:
        return self._frame

    def dataset(self) -> str:
        return self.DATASET

    def set_path(self, path: str):
        """Programmatic equivalent of typing a path and pressing Enter —
        used by Save/Load GUI State restore."""
        self._set_path(path)

    def current_path(self) -> str:
        return self._path_ed.text().strip()


class HydraDetectorToolbar(QtWidgets.QWidget):
    """Row of 5 exclusive buttons above the Hydra image viewer: ge1-ge4 show
    that panel's own raw frame; composite shows the geometry-based windmill
    composite of every currently-available panel."""

    panelChanged = QtCore.pyqtSignal(str)   # "ge1".."ge4" | "composite"

    _KEYS = ("ge1", "ge2", "ge3", "ge4", "composite")

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)
        self._buttons = {}
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        for key in self._KEYS:
            label = "Composite" if key == "composite" else key.upper()
            btn = QtWidgets.QPushButton(label)
            btn.setCheckable(True)
            btn.toggled.connect(lambda checked, k=key: self._on_toggled(k, checked))
            layout.addWidget(btn)
            self._group.addButton(btn)
            self._buttons[key] = btn
        layout.addStretch(1)
        self._buttons["ge1"].setChecked(True)

    def _on_toggled(self, key: str, checked: bool):
        if checked:
            self.panelChanged.emit(key)

    def set_available(self, panel_numbers):
        """Enable only the ge-buttons for panels actually found, and
        Composite iff at least 2 panels are available. If the currently
        selected button just became disabled, falls back to the first
        enabled one (emitting panelChanged)."""
        panel_numbers = set(panel_numbers)
        for n in (1, 2, 3, 4):
            self._buttons[f"ge{n}"].setEnabled(n in panel_numbers)
        self._buttons["composite"].setEnabled(len(panel_numbers) >= 2)
        cur = self.current()
        if not self._buttons[cur].isEnabled():
            for key in self._KEYS:
                if self._buttons[key].isEnabled():
                    self._buttons[key].setChecked(True)
                    break

    def current(self) -> str:
        for key, btn in self._buttons.items():
            if btn.isChecked():
                return key
        return "ge1"

    def set_current(self, key: str):
        if key in self._buttons and self._buttons[key].isEnabled():
            self._buttons[key].setChecked(True)


# Fixed per-curve colors — semantic, not index-based, so ge1 is always the
# same color regardless of which panels happen to be loaded/visible.
_HYDRA_CURVE_COLORS = {
    "ge1": "#4fc3f7", "ge2": "#ef5350", "ge3": "#66bb6a", "ge4": "#ab47bc",
    "composite": "#f5f5f5",
}
_HYDRA_CURVE_KEYS = ("ge1", "ge2", "ge3", "ge4", "composite")


class HydraProfileViewer(QtWidgets.QWidget):
    """Radial-integration plot for Hydra mode: one independently-computed
    curve per panel (ge1-4), each converted from its own native R-pixel
    axis to a shared display unit (R/2θ/Q) via ``widgets._convert_radial``,
    plus a toggleable "Composite" curve. This widget only displays curves —
    the composite curve's data (a resampled, NaN-aware sum of the four
    panels' own profiles) is computed by the owner (``HydraViewerPage``)
    and pushed in via ``set_curve`` like any other curve."""

    #: emitted when the "Composite" checkbox is toggled, so the owner knows
    #: whether it's worth computing/pushing that curve at all.
    compositeVisibilityChanged = QtCore.pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._native: dict = {}   # key -> (r_px, profile, lsd_um, px_um, wavelength_A)
        self._curves: dict = {}
        self._checks: dict = {}
        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(2)

        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(QtWidgets.QLabel("X:"))
        self._xaxis = _NoScrollComboBox()
        self._xaxis.addItems(["R (px)", "2θ (°)", "Q (Å⁻¹)"])
        self._xaxis.setCurrentIndex(1)   # 2θ — the meaningful shared axis across panels
        self._xaxis.currentIndexChanged.connect(self._replot)
        bar.addWidget(self._xaxis)
        self._logy = QtWidgets.QCheckBox("Log Y")
        self._logy.toggled.connect(self._replot)
        bar.addWidget(self._logy)
        bar.addSpacing(12)
        for key in _HYDRA_CURVE_KEYS:
            label = "Composite" if key == "composite" else key.upper()
            chk = QtWidgets.QCheckBox(label)
            chk.setChecked(True)
            chk.setStyleSheet(f"QCheckBox {{ color: {_HYDRA_CURVE_COLORS[key]}; }}")
            if key == "composite":
                chk.toggled.connect(self.compositeVisibilityChanged.emit)
            chk.toggled.connect(self._replot)
            bar.addWidget(chk)
            self._checks[key] = chk
        bar.addStretch(1)
        self._toolbar_layout = bar
        layout.addLayout(bar)

        self._plot = pg.PlotWidget(background="k")
        self._plot.setLabel("left", "Mean intensity")
        self._plot.setLabel("bottom", _XUNIT_LABEL["2th"])
        self._plot.showGrid(x=True, y=True, alpha=0.2)
        self._plot.addLegend(offset=(-10, 10))
        for key in _HYDRA_CURVE_KEYS:
            label = "Composite" if key == "composite" else key.upper()
            pen = pg.mkPen(_HYDRA_CURVE_COLORS[key], width=2,
                           style=QtCore.Qt.DashLine if key == "composite" else QtCore.Qt.SolidLine)
            self._curves[key] = self._plot.plot([], [], pen=pen, name=label)
        layout.addWidget(self._plot, stretch=1)

    def _unit_key(self) -> str:
        return ("R", "2th", "Q")[self._xaxis.currentIndex()]

    def set_curve(self, key: str, r_px, profile, *, lsd_um=None, px_um=None,
                 wavelength_A=None):
        """(Re)place one curve's data, in its own native R-pixel axis plus
        the geometry needed to convert it — independent per curve, since
        each Hydra panel generally has its own Lsd/pixel size/beam centre."""
        if key not in self._curves:
            return
        self._native[key] = (np.asarray(r_px), np.asarray(profile), lsd_um, px_um, wavelength_A)
        self._replot()

    def clear_curve(self, key: str):
        self._native.pop(key, None)
        if key in self._curves:
            self._curves[key].setData([], [])

    def get_native(self, key: str) -> Optional[tuple]:
        """The last data pushed via ``set_curve`` for ``key`` — its own
        native (r_px, profile, lsd_um, px_um, wavelength_A), or None. Used
        by an owner (e.g. deriving the "Composite" curve from ge1-4's own
        curves) to read back what's already been computed."""
        return self._native.get(key)

    def _replot(self, *_):
        target = self._unit_key()
        log = self._logy.isChecked()
        self._plot.setLabel("bottom", _XUNIT_LABEL[target])
        self._plot.setLabel("left", "log₁₀(intensity)" if log else "Mean intensity")
        for key, curve in self._curves.items():
            visible = self._checks[key].isChecked()
            data = self._native.get(key)
            if not visible or data is None:
                curve.setData([], [])
                continue
            r_px, profile, lsd, px, wl = data
            x = _convert_radial(r_px, lsd, px, wl, "R", target)
            y = profile
            if log:
                y = np.where(y > 0, np.log10(np.maximum(y, 1e-30)), np.nan)
            curve.setData(x, y)

    def composite_visible(self) -> bool:
        return self._checks["composite"].isChecked()
