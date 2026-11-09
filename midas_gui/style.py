"""Dioptas-inspired theme and layout helpers.

A cohesive dark theme (charcoal panels, off-white text, orange accent, light input
fields) plus small layout helpers (right-aligned labels, separators, section cards,
primary buttons, equal-width button grids) used to give every tab's left control
panel a streamlined, consistent look.

Functionality is unchanged — these only affect appearance and arrangement.
"""
from __future__ import annotations

from PyQt5 import QtCore, QtGui, QtWidgets

# ── Palette ─────────────────────────────────────────────────────────────────────
# Two themes share one set of token names. ``DARK`` is the original look (kept
# byte-for-byte); ``LIGHT`` is its counterpart. ``set_mode()`` rebinds the module
# globals below, so the stylesheet and every ``S.BG`` / ``S.MUTED`` reference
# picks up the active theme. It runs at import (from ``configured_mode``), so the
# theme is a startup setting: changing it in Preferences needs a restart.
DARK = dict(
    BG="#3c3c3c",          # window / panel background
    PANEL="#444444",       # raised panel
    INPUT_BG="#f1f1f1",    # light input fields (Dioptas signature)
    INPUT_FG="#1a1a1a",
    TEXT="#f1f1f1",
    MUTED="#9a9a9a",
    BORDER="#5b5b5b",
    HOVER="#adadad",
    ACCENT="#ff7800",      # orange accent (selected / checked / primary)
    ACCENT_D="#b15000",
    # secondary tokens (formerly literals inside the stylesheet)
    SUBTLE="#aaaaaa",      # help / status text, one step brighter than MUTED
    ALT_BASE="#e4e4e4", TOOLTIP_BG="#2d2d30", BTN_ITEM="#444444",
    BTN_TOP="#3a3a3a", BTN_BOT="#4e4e4e", BTN_PRESS="#333333",
    BTN_OFF_BG="#3a3a3a", BTN_OFF_BORDER="#4a4a4a",
    BTN_ON_TOP="#6a6a6a", BTN_ON_BOT="#444444", PRIMARY_OFF="#555555",
    FIELD_OFF_BG="#c8c8c8", FIELD_OFF_FG="#6a6a6a",
    SPIN_BTN="#dadada", SPIN_BTN_HOVER="#c2c2c2", SPIN_SEP="#c4c4c4",
    SURFACE="#1d1d1f", SURFACE_FG="#d6d6d6", SURFACE_ALT="#242427",
    SURFACE_HOVER="#2a2a2d", GRID="#3a3a3a",
    IND_BG="#2a2a2d", IND_BORDER="#8a8a8a", IND_OFF="#4a4a4a",
    TAB_TOP="#343434", TAB_BOT="#4a4a4a", TAB_FG="#cfcfcf",
    TAB_SEL_TOP="#5a5a5a", TAB_SEL_BOT="#404040", TAB_SEL_FG="white",
    HEADER_BG="#4a4a4a", GROOVE="#2a2a2d", NAV_GROOVE="#707070", NAV_GROOVE_B="#8a8a8a",
    SCROLL_BG="#2f2f2f", SCROLL_HANDLE="#5a5a5a", SCROLL_HANDLE_HOVER="#707070",
    SPLITTER="#2f2f2f", LOG_BG="#1a1a1a", LOG_FG="#dddddd",
    MINI_BG="#333333", MINI_FG="#cccccc",
    STRIP_BG="#262626", STRIP_BORDER="#3a3a3a", STRIP_TAB_BG="#2b2b2b",
    STRIP_TAB_FG="#cccccc", STRIP_TAB_BORDER="#444444",
    CANVAS_BG="k", CANVAS_FG="w", CANVAS_PANEL="#2b2e35", CANVAS_DEEP="#111111", AXIS_TEXT="#d0d0d0", AXIS_TICK="#c8c8c8", READOUT_BORDER="#444444",
    LOADER_BG="#2b2e35", LINK="#4da3ff", INFO="#8ab4f8", LINK2="#8ecdf7", INFO2="#7fb8ff",
)
LIGHT = dict(
    BG="#ececec", PANEL="#f6f6f6", INPUT_BG="#ffffff", INPUT_FG="#1a1a1a",
    TEXT="#1f1f1f", MUTED="#6b6b6b", BORDER="#b4b4b4", HOVER="#6a6a6a",
    ACCENT="#ff7800", ACCENT_D="#c25e00",
    SUBTLE="#555555",
    ALT_BASE="#f2f2f2", TOOLTIP_BG="#fffbe6", BTN_ITEM="#f0f0f0",
    BTN_TOP="#fafafa", BTN_BOT="#e4e4e4", BTN_PRESS="#d2d2d2",
    BTN_OFF_BG="#ececec", BTN_OFF_BORDER="#d0d0d0",
    BTN_ON_TOP="#cfcfcf", BTN_ON_BOT="#e8e8e8", PRIMARY_OFF="#cfcfcf",
    FIELD_OFF_BG="#e2e2e2", FIELD_OFF_FG="#8a8a8a",
    SPIN_BTN="#ececec", SPIN_BTN_HOVER="#dcdcdc", SPIN_SEP="#c4c4c4",
    SURFACE="#ffffff", SURFACE_FG="#222222", SURFACE_ALT="#f4f4f4",
    SURFACE_HOVER="#ececec", GRID="#dcdcdc",
    IND_BG="#ffffff", IND_BORDER="#8a8a8a", IND_OFF="#cccccc",
    TAB_TOP="#e6e6e6", TAB_BOT="#d6d6d6", TAB_FG="#444444",
    TAB_SEL_TOP="#ffffff", TAB_SEL_BOT="#f2f2f2", TAB_SEL_FG="#1f1f1f",
    HEADER_BG="#dcdcdc", GROOVE="#cfcfcf", NAV_GROOVE="#bdbdbd", NAV_GROOVE_B="#9a9a9a",
    SCROLL_BG="#e2e2e2", SCROLL_HANDLE="#b0b0b0", SCROLL_HANDLE_HOVER="#8f8f8f",
    SPLITTER="#d4d4d4", LOG_BG="#fafafa", LOG_FG="#222222",
    MINI_BG="#e8e8e8", MINI_FG="#333333",
    STRIP_BG="#dcdcdc", STRIP_BORDER="#c4c4c4", STRIP_TAB_BG="#e4e4e4",
    STRIP_TAB_FG="#444444", STRIP_TAB_BORDER="#b4b4b4",
    CANVAS_BG="w", CANVAS_FG="#222222", CANVAS_PANEL="#ffffff", CANVAS_DEEP="#ffffff", AXIS_TEXT="#333333", AXIS_TICK="#333333", READOUT_BORDER="#b4b4b4",
    LOADER_BG="#e4e6ea", LINK="#1a5fb4", INFO="#1a5fb4", LINK2="#1a5fb4", INFO2="#1a5fb4",
)
THEMES = {"dark": DARK, "light": LIGHT}
MODE = "dark"

# Declared for readers/linters; ``set_mode`` fills them in.
BG = PANEL = INPUT_BG = INPUT_FG = TEXT = MUTED = BORDER = HOVER = ""
ACCENT = ACCENT_D = SUBTLE = ""
SUCCESS_BTN_QSS = DANGER_BTN_QSS = ""


def _build_btn_qss():
    global SUCCESS_BTN_QSS, DANGER_BTN_QSS
    # Green/red QPushButton hues (Save/Abort in the batch tabs). Both define an
    # explicit :disabled rule so a disabled Save/Abort still reads as inactive.
    off = ("QPushButton:disabled { background: " + BTN_OFF_BG + "; color: " + MUTED
           + "; border: 1px solid " + BORDER + "; }")
    SUCCESS_BTN_QSS = (
        "QPushButton { background: #2e7d32; color: white; font-weight: bold; "
        "border: 1px solid #1b5e20; border-radius: 4px; padding: 3px 10px; }"
        "QPushButton:hover { background: #33902e; }" + off)
    DANGER_BTN_QSS = (
        "QPushButton { background: #c62828; color: white; font-weight: bold; "
        "border: 1px solid #8e0000; border-radius: 4px; padding: 3px 10px; }"
        "QPushButton:hover { background: #d32f2f; }" + off)


def set_mode(mode: str) -> str:
    """Activate the ``"dark"`` or ``"light"`` palette; returns the mode applied."""
    global MODE
    mode = mode if mode in THEMES else "dark"
    MODE = mode
    globals().update(THEMES[mode])
    _build_btn_qss()
    return mode


def configured_mode() -> str:
    """The theme chosen in Preferences (``ui.theme``), defaulting to dark."""
    try:
        from midas_gui import constants as C
        m = str(getattr(C, "DEFAULT_THEME", "dark") or "dark").lower()
    except Exception:
        m = "dark"
    return m if m in THEMES else "dark"


# Fixed-width font stack. Naming real per-platform families (rather than the generic
# "monospace" / QFont("Monospace")) lets Qt resolve immediately, avoiding the
# "qt.qpa.fonts: Populating font family aliases" startup warning on macOS/Windows.
MONO_FAMILIES = ["Menlo", "Consolas", "DejaVu Sans Mono", "Courier New"]
MONO_CSS = ", ".join(f'"{f}"' if " " in f else f for f in MONO_FAMILIES)


# ── Fonts for graphics-scene items (overlays, plot axis titles) ─────────────────
# The application stylesheet below sizes every *widget* in **pixels**
# ("QWidget { font-size: 12px }"). Qt scales logical pixels by
# QT_SCALE_FACTOR (see app._apply_ui_scale), so px-sized text tracks the rest of
# the UI exactly at any interface scale.
#
# Items living in a QGraphicsScene — pg.TextItem overlays, pyqtgraph axis
# titles — do NOT inherit that stylesheet, and the two obvious ways of sizing
# them both misbehave:
#
#   * ``QFont().pointSize()`` on a default-constructed font returns the *system*
#     size (13 pt on macOS), not the app's 12 px, so scaling "the app font by
#     1.2" silently produced ~19 px text. On a widget the stylesheet has already
#     touched, pointSize() comes back -1 instead, and bumping *that* gives a ~1 pt
#     illegible font (see ROIRibbon's paintEvent, which hit this first).
#   * A point size anywhere — QFont.setPointSize, or "font-size: 12pt" in the
#     HTML pyqtgraph feeds its axis-title QGraphicsTextItem — is converted to
#     pixels through the screen's logical DPI, which QT_SCALE_FACTOR already
#     moved. The result is scaled twice and grows out of proportion with
#     everything around it as the interface scale goes up.
#
# So: one base size in px, and every overlay/axis font derived from it.
BASE_FONT_PX = 12        # keep in sync with the QWidget rule in stylesheet()


def font_px(factor: float = 1.0, *, bold: bool = False, mono: bool = False) -> QtGui.QFont:
    """A QFont sized ``factor`` × the app's base UI font, in **pixels**.

    Use for anything drawn into a QGraphicsScene (pg.TextItem overlays, custom
    paintEvent text) instead of setPointSize — see the note above.
    """
    f = QtGui.QFont()
    if mono:
        f.setFamilies(list(MONO_FAMILIES))
        f.setStyleHint(QtGui.QFont.Monospace)
    f.setPixelSize(max(1, round(BASE_FONT_PX * float(factor))))
    f.setBold(bold)
    return f


def axis_label_css(color: str, factor: float = 1.0) -> dict:
    """``setLabel(...)`` style kwargs for a pyqtgraph axis title, sized in px.

    pyqtgraph wraps the title in ``<span style='...'>``; CSS ``px`` is taken
    literally by QTextDocument (no DPI conversion), so the title scales with
    the interface exactly like the surrounding widgets do.
    """
    return {"color": color, "font-size": f"{max(1, round(BASE_FONT_PX * float(factor)))}px"}


def apply_theme(app: QtWidgets.QApplication, checkmark_svg: str,
                 up_arrow_svg: str = "", down_arrow_svg: str = "") -> None:
    """Apply the shared Fusion style, active (dark/light) palette and QSS to *app*.

    Shared by the main GUI and any standalone window (e.g. Auto Attenuation)
    that should look — and scale with QT_SCALE_FACTOR — identically to it.
    """
    app.setStyle("Fusion")
    pal = QtGui.QPalette()
    for role, col in [
        (QtGui.QPalette.Window,          BG),
        (QtGui.QPalette.WindowText,      TEXT),
        (QtGui.QPalette.Base,            INPUT_BG),
        (QtGui.QPalette.AlternateBase,   ALT_BASE),
        (QtGui.QPalette.Text,            INPUT_FG),
        (QtGui.QPalette.Button,          BTN_ITEM),
        (QtGui.QPalette.ButtonText,      TEXT),
        (QtGui.QPalette.Highlight,       ACCENT),
        (QtGui.QPalette.HighlightedText, "#ffffff"),
        (QtGui.QPalette.ToolTipBase,     TOOLTIP_BG),
        (QtGui.QPalette.ToolTipText,     TEXT),
    ]:
        pal.setColor(role, QtGui.QColor(col))
    app.setPalette(pal)
    app.setStyleSheet(stylesheet(checkmark_svg, up_arrow_svg, down_arrow_svg))


def stylesheet(checkmark_svg: str, up_arrow_svg: str = "", down_arrow_svg: str = "") -> str:
    """Return the full application QSS.

    ``checkmark_svg`` is the tick for checkboxes; ``up_arrow_svg`` / ``down_arrow_svg``
    are the glyphs drawn inside spinbox step buttons and the combo drop-down.
    """
    return f"""
    QWidget {{ color: {TEXT}; font-size: {BASE_FONT_PX}px; }}
    QMainWindow, QScrollArea, QSplitter {{ background: {BG}; }}
    QScrollArea {{ border: none; }}
    QToolTip {{ background: {TOOLTIP_BG}; color: {TEXT}; border: 1px solid {BORDER}; }}

    /* ── Context menus (incl. pyqtgraph right-click plot menus) ───
       Without an explicit background these inherit the global off-white text on
       the default light menu background, making entries unreadable until hovered. */
    QMenu {{
        background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER};
    }}
    QMenu::item {{ padding: 4px 22px; background: transparent; }}
    QMenu::item:selected {{ background: {ACCENT}; color: white; }}
    QMenu::item:disabled {{ color: {MUTED}; }}
    QMenu::separator {{ height: 1px; background: {BORDER}; margin: 3px 6px; }}
    QMenuBar {{ background: {BG}; color: {TEXT}; }}
    QMenuBar::item:selected {{ background: {ACCENT}; color: white; }}

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
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {BTN_TOP}, stop:1 {BTN_BOT});
        border: 1px solid {BORDER};
        border-radius: 4px;
        padding: 4px 10px;
        min-height: 18px;
    }}
    QPushButton:hover {{ border: 1px solid {HOVER}; }}
    QPushButton:pressed {{ background: {BTN_PRESS}; }}
    QPushButton:disabled {{ color: {MUTED}; background: {BTN_OFF_BG}; border-color: {BTN_OFF_BORDER}; }}
    QPushButton:checked {{
        background: qlineargradient(x1:0, y1:1, x2:0, y2:0, stop:0 {BTN_ON_TOP}, stop:1 {BTN_ON_BOT});
        border: 1px solid {ACCENT};
    }}
    QPushButton#primary {{
        color: white; font-weight: bold;
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {ACCENT}, stop:1 {ACCENT_D});
        border: 1px solid {ACCENT_D};
    }}
    QPushButton#primary:hover {{ border: 1px solid {HOVER}; }}
    QPushButton#primary:disabled {{ background: {PRIMARY_OFF}; color: {MUTED}; border-color: {BTN_OFF_BORDER}; }}
    QPushButton#primary:checked {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3fae4a, stop:1 #2e8a38);
        border: 1px solid #256e2c;
    }}

    /* ── Inputs (light fields) ────────────────────────────────── */
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
        background: {INPUT_BG}; color: {INPUT_FG};
        border: 1px solid {BORDER}; border-radius: 3px;
        selection-background-color: {ACCENT}; selection-color: white;
        min-height: 18px; padding: 1px 3px;
    }}
    QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
        background: {FIELD_OFF_BG}; color: {FIELD_OFF_FG};
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
    QComboBox:disabled {{ background: {FIELD_OFF_BG}; }}

    /* ── Spin-box step buttons (show the up/down arrows) ──────── */
    QSpinBox::up-button, QDoubleSpinBox::up-button {{
        subcontrol-origin: border; subcontrol-position: top right;
        width: 16px; background: {SPIN_BTN};
        border-left: 1px solid {BORDER}; border-bottom: 1px solid {SPIN_SEP};
        border-top-right-radius: 3px;
    }}
    QSpinBox::down-button, QDoubleSpinBox::down-button {{
        subcontrol-origin: border; subcontrol-position: bottom right;
        width: 16px; background: {SPIN_BTN};
        border-left: 1px solid {BORDER}; border-bottom-right-radius: 3px;
    }}
    QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
    QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{ background: {SPIN_BTN_HOVER}; }}
    QSpinBox::up-button:pressed, QDoubleSpinBox::up-button:pressed,
    QSpinBox::down-button:pressed, QDoubleSpinBox::down-button:pressed {{ background: {ACCENT}; }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({up_arrow_svg}); width: 9px; height: 9px; }}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({down_arrow_svg}); width: 9px; height: 9px; }}
    QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled,
    QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {{ image: none; }}
    QPlainTextEdit, QTextEdit {{ background: {SURFACE}; color: {SURFACE_FG}; }}

    /* ── Checkboxes / radios (orange when on) ─────────────────── */
    QCheckBox, QRadioButton {{ color: {TEXT}; spacing: 6px; background: transparent; }}
    QCheckBox::indicator, QRadioButton::indicator {{
        width: 14px; height: 14px;
        border: 1px solid {IND_BORDER}; background: {IND_BG};
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
    /* A disabled box must not render in the live accent fill: a ticked-looking
       control that ignores clicks reads as a bug in the app, not as "fixed". */
    QCheckBox:disabled, QRadioButton:disabled {{ color: {MUTED}; }}
    QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
        border-color: {IND_OFF};
    }}
    QCheckBox::indicator:checked:disabled, QGroupBox::indicator:checked:disabled {{
        background: {IND_OFF}; border-color: {IND_OFF};
    }}
    QRadioButton::indicator:checked:disabled {{
        background: {IND_OFF}; border-color: {IND_OFF};
    }}

    /* ── Top tab bar (the 9 modules) ──────────────────────────── */
    QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 4px; top: -1px; }}
    QTabBar::tab {{
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {TAB_TOP}, stop:1 {TAB_BOT});
        color: {TAB_FG}; border: 1px solid {BORDER}; border-bottom: none;
        border-top-left-radius: 4px; border-top-right-radius: 4px;
        padding: 5px 10px; margin-right: 1px;
    }}
    QTabBar::tab:hover {{ color: {TEXT}; border-color: {HOVER}; }}
    QTabBar::tab:selected {{
        background: qlineargradient(x1:0, y1:1, x2:0, y2:0, stop:0 {TAB_SEL_TOP}, stop:1 {TAB_SEL_BOT});
        color: {TAB_SEL_FG}; border-color: {ACCENT}; border-bottom: 2px solid {ACCENT};
    }}

    /* ── Progress / tables / sliders / scrollbars ─────────────── */
    QProgressBar {{
        border: 1px solid {BORDER}; border-radius: 3px; background: {IND_BG};
        text-align: center; color: {TEXT}; min-height: 14px;
    }}
    QProgressBar::chunk {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT_D}, stop:1 {ACCENT});
        border-radius: 2px;
    }}
    QHeaderView::section {{
        background: {HEADER_BG}; color: {TEXT}; border: none; padding: 3px;
    }}
    QTableWidget {{
        background: {SURFACE}; color: {SURFACE_FG}; gridline-color: {GRID};
        border: 1px solid {BORDER}; border-radius: 3px;
    }}

    /* ── Item views (file dialogs, lists, trees) ──────────────────
       Without explicit colours these inherit the global off-white
       text on a default light background, making file/folder names
       invisible until selected — notably in Qt's non-native file
       dialog on Linux. */
    QTreeView, QListView, QColumnView, QTableView {{
        background: {SURFACE}; color: {SURFACE_FG};
        alternate-background-color: {SURFACE_ALT};
        selection-background-color: {ACCENT}; selection-color: white;
        border: 1px solid {BORDER}; outline: 0;
    }}
    QTreeView::item, QListView::item, QColumnView::item {{ color: {SURFACE_FG}; }}
    QTreeView::item:hover, QListView::item:hover, QColumnView::item:hover {{
        background: {SURFACE_HOVER};
    }}
    QTreeView::item:selected, QListView::item:selected, QColumnView::item:selected {{
        background: {ACCENT}; color: white;
    }}
    /* The file dialog chrome (labels, side bar, look-in combo). */
    QFileDialog {{ background: {BG}; }}
    QFileDialog QLabel, QFileDialog QToolButton, QFileDialog QCheckBox {{
        color: {TEXT}; background: transparent;
    }}
    QSlider::groove:horizontal {{ height: 4px; background: {GROOVE}; border-radius: 2px; }}
    QSlider::handle:horizontal {{
        background: {ACCENT}; width: 12px; margin: -5px 0; border-radius: 6px;
    }}
    /* Frame-navigation prev/next + slider (Data Viewer, Calibrate, Mask
       Builder, Hydra GE-panel nav, Batch's "Eta-R cakes" stack) — several of
       these prev/next buttons are plain QToolButtons with no default
       background/border at all (native/flat), and the generic slider groove
       above is barely visible against a dark panel; give every one of them
       (tagged via objectName, not every QSlider/QToolButton in the app) one
       clearly "this scrubs frames" look. */
    QToolButton#frameNavBtn, QPushButton#frameNavBtn {{
        color: white; font-weight: bold;
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {ACCENT}, stop:1 {ACCENT_D});
        border: 1px solid {ACCENT_D}; border-radius: 4px;
        min-width: 22px; min-height: 20px; padding: 2px;
    }}
    QToolButton#frameNavBtn:hover, QPushButton#frameNavBtn:hover {{ border: 1px solid {HOVER}; }}
    QToolButton#frameNavBtn:pressed, QPushButton#frameNavBtn:pressed {{ background: {ACCENT_D}; }}
    QToolButton#frameNavBtn:disabled, QPushButton#frameNavBtn:disabled {{
        background: {PRIMARY_OFF}; color: {MUTED}; border-color: {BTN_OFF_BORDER};
    }}
    QSlider#frameNavSlider::groove:horizontal {{
        height: 6px; background: {NAV_GROOVE}; border: 1px solid {NAV_GROOVE_B}; border-radius: 3px;
    }}
    QSlider#frameNavSlider::handle:horizontal {{
        background: {ACCENT}; width: 14px; margin: -6px 0; border-radius: 7px;
        border: 1px solid {ACCENT_D};
    }}
    QScrollBar:vertical {{ background: {SCROLL_BG}; width: 11px; margin: 0; border: none; }}
    QScrollBar::handle:vertical {{ background: {SCROLL_HANDLE}; border-radius: 5px; min-height: 24px; }}
    QScrollBar::handle:vertical:hover {{ background: {SCROLL_HANDLE_HOVER}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
    QScrollBar:horizontal {{ background: {SCROLL_BG}; height: 11px; margin: 0; border: none; }}
    QScrollBar::handle:horizontal {{ background: {SCROLL_HANDLE}; border-radius: 5px; min-width: 24px; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QSplitter::handle {{ background: {SPLITTER}; }}
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

    ``row(("Label:", widget), ...)`` adds any number of right-aligned
    label/field pairs on one line; ``full(widget)`` spans the whole width.
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
            # Every field column shares the row's spare width. Stretching only
            # columns 1 and 3 left a third pair's field pinned to its minimum
            # while the first two stretched around it.
            self.setColumnStretch(col + 1, 1)
            col += 2
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


# Resolve the theme at import so module-level uses of S.* see the right colours.
set_mode(configured_mode())
