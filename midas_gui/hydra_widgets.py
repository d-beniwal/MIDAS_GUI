"""UI building blocks for the Data Viewer tab's Hydra (4-panel GE) mode.

``HydraModeRibbon`` is the leftmost strip of the whole Data Viewer tab —
it switches the tab between the existing single-detector view and the new
Hydra view. Later Hydra-mode widgets (the per-panel image toolbar, loader,
and multi-curve profile viewer) are added to this module as the feature is
built out phase by phase.
"""
from __future__ import annotations

from PyQt5 import QtCore, QtGui, QtWidgets


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
