"""Dioptas-inspired theme and layout helpers.

A cohesive dark theme (charcoal panels, off-white text, orange accent, light input
fields) plus small layout helpers (right-aligned labels, separators, section cards,
primary buttons, equal-width button grids) used to give every tab's left control
panel a streamlined, consistent look.

Functionality is unchanged — these only affect appearance and arrangement.
"""
from __future__ import annotations

from PyQt5 import QtCore, QtWidgets

# ── Palette ─────────────────────────────────────────────────────────────────────
BG        = "#3c3c3c"   # window / panel background
PANEL     = "#444444"   # raised panel
INPUT_BG  = "#f1f1f1"   # light input fields (Dioptas signature)
INPUT_FG  = "#1a1a1a"
TEXT      = "#f1f1f1"
MUTED     = "#9a9a9a"
BORDER    = "#5b5b5b"
HOVER     = "#adadad"
ACCENT    = "#ff7800"   # orange accent (selected / checked / primary)
ACCENT_D  = "#b15000"


def stylesheet(checkmark_svg: str, up_arrow_svg: str = "", down_arrow_svg: str = "") -> str:
    """Return the full application QSS.

    ``checkmark_svg`` is the tick for checkboxes; ``up_arrow_svg`` / ``down_arrow_svg``
    are the glyphs drawn inside spinbox step buttons and the combo drop-down.
    """
    return f"""
    QWidget {{ color: {TEXT}; font-size: 12px; }}
    QMainWindow, QScrollArea, QSplitter {{ background: {BG}; }}
    QScrollArea {{ border: none; }}
    QToolTip {{ background: #2d2d30; color: {TEXT}; border: 1px solid {BORDER}; }}

    /* ── Section cards ─────────────────────────────────────────── */
    QGroupBox {{
        border: 1px solid {BORDER};
        border-radius: 5px;
        margin-top: 9px;
        padding: 6px 4px 4px 4px;
        background: {PANEL};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 10px;
        padding: 0 4px;
        color: {HOVER};
        font-weight: bold;
    }}
    QGroupBox::indicator {{ width: 14px; height: 14px; }}

    /* ── Buttons ──────────────────────────────────────────────── */
    QPushButton {{
        color: {TEXT};
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3a3a3a, stop:1 #4e4e4e);
        border: 1px solid {BORDER};
        border-radius: 4px;
        padding: 4px 10px;
        min-height: 18px;
    }}
    QPushButton:hover {{ border: 1px solid {HOVER}; }}
    QPushButton:pressed {{ background: #333333; }}
    QPushButton:disabled {{ color: {MUTED}; background: #3a3a3a; border-color: #4a4a4a; }}
    QPushButton:checked {{
        background: qlineargradient(x1:0, y1:1, x2:0, y2:0, stop:0 #6a6a6a, stop:1 #444444);
        border: 1px solid {ACCENT};
    }}
    QPushButton#primary {{
        color: white; font-weight: bold;
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {ACCENT}, stop:1 {ACCENT_D});
        border: 1px solid {ACCENT_D};
    }}
    QPushButton#primary:hover {{ border: 1px solid {HOVER}; }}
    QPushButton#primary:disabled {{ background: #555; color: {MUTED}; border-color: #4a4a4a; }}

    /* ── Inputs (light fields) ────────────────────────────────── */
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
        background: {INPUT_BG}; color: {INPUT_FG};
        border: 1px solid {BORDER}; border-radius: 3px;
        selection-background-color: {ACCENT}; selection-color: white;
        min-height: 18px; padding: 1px 3px;
    }}
    QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
        background: #c8c8c8; color: #6a6a6a;
    }}
    QComboBox::drop-down {{
        subcontrol-origin: padding; subcontrol-position: center right;
        width: 18px; border-left: 1px solid {BORDER};
    }}
    QComboBox::down-arrow {{ image: url({down_arrow_svg}); width: 9px; height: 9px; }}
    QComboBox QAbstractItemView {{
        background: {INPUT_BG}; color: {INPUT_FG};
        selection-background-color: {ACCENT}; selection-color: white;
        border: 1px solid {BORDER}; outline: 0;
    }}
    QComboBox:disabled {{ background: #c8c8c8; }}

    /* ── Spin-box step buttons (show the up/down arrows) ──────── */
    QSpinBox::up-button, QDoubleSpinBox::up-button {{
        subcontrol-origin: border; subcontrol-position: top right;
        width: 16px; background: #dadada;
        border-left: 1px solid {BORDER}; border-bottom: 1px solid #c4c4c4;
        border-top-right-radius: 3px;
    }}
    QSpinBox::down-button, QDoubleSpinBox::down-button {{
        subcontrol-origin: border; subcontrol-position: bottom right;
        width: 16px; background: #dadada;
        border-left: 1px solid {BORDER}; border-bottom-right-radius: 3px;
    }}
    QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
    QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{ background: #c2c2c2; }}
    QSpinBox::up-button:pressed, QDoubleSpinBox::up-button:pressed,
    QSpinBox::down-button:pressed, QDoubleSpinBox::down-button:pressed {{ background: {ACCENT}; }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({up_arrow_svg}); width: 9px; height: 9px; }}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({down_arrow_svg}); width: 9px; height: 9px; }}
    QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled,
    QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {{ image: none; }}
    QPlainTextEdit, QTextEdit {{ background: #1d1d1f; color: #d6d6d6; }}

    /* ── Checkboxes / radios (orange when on) ─────────────────── */
    QCheckBox, QRadioButton {{ color: {TEXT}; spacing: 6px; background: transparent; }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 14px; height: 14px;
        border: 1px solid #8a8a8a; background: #2a2a2d;
    }}
    QCheckBox::indicator {{ border-radius: 3px; }}
    QRadioButton::indicator {{ border-radius: 7px; }}
    QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {HOVER}; }}
    QCheckBox::indicator:checked {{
        background: {ACCENT}; border-color: {ACCENT}; image: url({checkmark_svg});
    }}
    QGroupBox::indicator:checked {{
        background: {ACCENT}; border-color: {ACCENT}; image: url({checkmark_svg});
    }}
    QRadioButton::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

    /* ── Top tab bar (the 9 modules) ──────────────────────────── */
    QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 4px; top: -1px; }}
    QTabBar::tab {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #343434, stop:1 #4a4a4a);
        color: #cfcfcf; border: 1px solid {BORDER}; border-bottom: none;
        border-top-left-radius: 4px; border-top-right-radius: 4px;
        padding: 5px 10px; margin-right: 1px;
    }}
    QTabBar::tab:hover {{ color: {TEXT}; border-color: {HOVER}; }}
    QTabBar::tab:selected {{
        background: qlineargradient(x1:0, y1:1, x2:0, y2:0, stop:0 #5a5a5a, stop:1 #404040);
        color: white; border-color: {ACCENT}; border-bottom: 2px solid {ACCENT};
    }}

    /* ── Progress / tables / sliders / scrollbars ─────────────── */
    QProgressBar {{
        border: 1px solid {BORDER}; border-radius: 3px; background: #2a2a2d;
        text-align: center; color: {TEXT}; min-height: 14px;
    }}
    QProgressBar::chunk {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT_D}, stop:1 {ACCENT});
        border-radius: 2px;
    }}
    QHeaderView::section {{
        background: #4a4a4a; color: {TEXT}; border: none; padding: 3px;
    }}
    QTableWidget {{
        background: #1d1d1f; color: #d6d6d6; gridline-color: #3a3a3a;
        border: 1px solid {BORDER}; border-radius: 3px;
    }}

    /* ── Item views (file dialogs, lists, trees) ──────────────────
       Without explicit colours these inherit the global off-white
       text on a default light background, making file/folder names
       invisible until selected — notably in Qt's non-native file
       dialog on Linux. */
    QTreeView, QListView, QColumnView, QTableView {{
        background: #1d1d1f; color: #d6d6d6;
        alternate-background-color: #242427;
        selection-background-color: {ACCENT}; selection-color: white;
        border: 1px solid {BORDER}; outline: 0;
    }}
    QTreeView::item, QListView::item, QColumnView::item {{ color: #d6d6d6; }}
    QTreeView::item:hover, QListView::item:hover, QColumnView::item:hover {{
        background: #2a2a2d;
    }}
    QTreeView::item:selected, QListView::item:selected, QColumnView::item:selected {{
        background: {ACCENT}; color: white;
    }}
    /* The file dialog chrome (labels, side bar, look-in combo). */
    QFileDialog {{ background: {BG}; }}
    QFileDialog QLabel, QFileDialog QToolButton, QFileDialog QCheckBox {{
        color: {TEXT}; background: transparent;
    }}
    QSlider::groove:horizontal {{ height: 4px; background: #2a2a2d; border-radius: 2px; }}
    QSlider::handle:horizontal {{
        background: {ACCENT}; width: 12px; margin: -5px 0; border-radius: 6px;
    }}
    QScrollBar:vertical {{ background: #2f2f2f; width: 11px; margin: 0; border: none; }}
    QScrollBar::handle:vertical {{ background: #5a5a5a; border-radius: 5px; min-height: 24px; }}
    QScrollBar::handle:vertical:hover {{ background: #707070; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
    QScrollBar:horizontal {{ background: #2f2f2f; height: 11px; margin: 0; border: none; }}
    QScrollBar::handle:horizontal {{ background: #5a5a5a; border-radius: 5px; min-width: 24px; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QSplitter::handle {{ background: #2f2f2f; }}
    QSplitter::handle:hover {{ background: {BORDER}; }}
    QStatusBar {{ color: {MUTED}; }}
    """


# ── Layout helpers ──────────────────────────────────────────────────────────────

class LabelRight(QtWidgets.QLabel):
    """Right-aligned, vertically-centred label (Dioptas form style)."""
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)


def hline() -> QtWidgets.QFrame:
    f = QtWidgets.QFrame()
    f.setFrameShape(QtWidgets.QFrame.HLine)
    f.setFrameShadow(QtWidgets.QFrame.Plain)
    f.setFixedHeight(1)
    f.setStyleSheet(f"background:{BORDER}; border:none;")
    return f


def make_card(title: str) -> QtWidgets.QGroupBox:
    """Return a styled section card (QGroupBox) with a tight QVBoxLayout body.

    Use ``card.body`` to add widgets/layouts.
    """
    gb = QtWidgets.QGroupBox(title)
    body = QtWidgets.QVBoxLayout(gb)
    body.setContentsMargins(8, 6, 8, 6)
    body.setSpacing(5)
    gb.body = body          # type: ignore[attr-defined]
    return gb


def primary_btn(text: str) -> QtWidgets.QPushButton:
    """A prominent accent (orange) action button."""
    b = QtWidgets.QPushButton(text)
    b.setObjectName("primary")
    b.setMinimumHeight(32)
    return b


class Form(QtWidgets.QGridLayout):
    """A compact label→field grid (Dioptas form style).

    ``row(("Label:", widget), ...)`` adds up to two right-aligned label/field
    pairs on one line; ``full(widget)`` spans the whole width.
    """
    def __init__(self):
        super().__init__()
        self.setHorizontalSpacing(6)
        self.setVerticalSpacing(5)
        self.setContentsMargins(0, 0, 0, 0)
        self._r = 0

    def row(self, *pairs):
        col = 0
        for lab, w in pairs:
            if isinstance(lab, QtWidgets.QWidget):
                self.addWidget(lab, self._r, col)
            elif lab is not None:
                self.addWidget(LabelRight(lab), self._r, col)
            if isinstance(w, QtWidgets.QLayout):
                self.addLayout(w, self._r, col + 1)
            else:
                self.addWidget(w, self._r, col + 1)
            col += 2
        self.setColumnStretch(1, 1)
        if len(pairs) > 1:
            self.setColumnStretch(3, 1)
        self._r += 1
        return self

    def full(self, widget):
        if isinstance(widget, QtWidgets.QLayout):
            self.addLayout(widget, self._r, 0, 1, 4)
        else:
            self.addWidget(widget, self._r, 0, 1, 4)
        self._r += 1
        return self


def button_grid(buttons, cols: int = 2) -> QtWidgets.QGridLayout:
    """Arrange already-created QPushButtons in an equal-width grid."""
    g = QtWidgets.QGridLayout()
    g.setSpacing(5)
    for i, b in enumerate(buttons):
        g.addWidget(b, i // cols, i % cols)
    for c in range(cols):
        g.setColumnStretch(c, 1)
    return g
