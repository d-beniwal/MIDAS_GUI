"""Application shell: MainWindow, dark palette, Dioptas-inspired stylesheet, main()."""
from __future__ import annotations

import faulthandler
import sys
import traceback
from datetime import datetime
from pathlib import Path

import midas_gui._paths  # noqa: F401  (KMP_DUPLICATE_LIB_OK env var)
from midas_gui import __version__
from PyQt5 import QtCore, QtGui, QtWidgets

# ── Crash diagnostics ─────────────────────────────────────────────────────────
# On Windows a startup error (or an exception raised inside a Qt slot — which
# PyQt5 turns into a hard abort) makes the window "pop up and die" with no visible
# traceback, especially when launched by double-click (no console).  We log every
# uncaught Python exception and native fault to a file and, if a QApplication is
# up, show it in a dialog.  Installing our own excepthook also stops PyQt5 from
# aborting the process on a slot exception, so the app survives non-fatal errors.

_LOG_FILE = Path.home() / "midas_gui_error.log"


def _log(text: str) -> None:
    try:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n===== {datetime.now().isoformat()} =====\n{text}\n")
    except Exception:
        pass


def _excepthook(exc_type, exc, tb) -> None:
    msg = "".join(traceback.format_exception(exc_type, exc, tb))
    _log(msg)
    try:
        sys.stderr.write(msg)
    except Exception:
        pass
    try:
        if QtWidgets.QApplication.instance() is not None:
            QtWidgets.QMessageBox.critical(
                None, "MIDAS GUI — unexpected error",
                f"{exc_type.__name__}: {exc}\n\nFull traceback written to:\n{_LOG_FILE}")
    except Exception:
        pass


def _install_diagnostics() -> None:
    sys.excepthook = _excepthook
    try:
        faulthandler.enable(open(_LOG_FILE, "a", encoding="utf-8"))
    except Exception:
        pass
    _log(f"MIDAS GUI v{__version__} starting — Python {sys.version.split()[0]}, "
         f"Qt {QtCore.QT_VERSION_STR}, PyQt {QtCore.PYQT_VERSION_STR}, platform {sys.platform}")

from midas_gui.helpers import _make_checkmark_svg, _make_arrow_svg
from midas_gui import style as S
from midas_gui.tab_view import DataViewerTab
from midas_gui.tab_mask import MaskTab
from midas_gui.tab_calibrate import CalibrationTab
from midas_gui.tab_batch import BatchTab
from midas_gui.tab_refine import RefinementTab
from midas_gui.tab_corrections import CorrectionsTab
from midas_gui.tab_pdf import PDFTab
from midas_gui.tab_texture import TextureTab
from midas_gui.tab_export import ExportTab

_CHECKMARK_SVG = _make_checkmark_svg()
_ARROW_UP_SVG = _make_arrow_svg("up")
_ARROW_DOWN_SVG = _make_arrow_svg("down")


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"MIDAS GUI v{__version__}")
        self.resize(1240, 900)
        self._build_ui()

    def _build_ui(self):
        tabs = QtWidgets.QTabWidget()
        self.setCentralWidget(tabs)

        # Build each tab in isolation: a single tab that fails on this platform
        # becomes an error placeholder instead of taking the whole window down.
        def _tab(factory, name):
            try:
                return factory()
            except Exception:
                _log(f"Tab '{name}' failed to build:\n{traceback.format_exc()}")
                w = QtWidgets.QWidget()
                lay = QtWidgets.QVBoxLayout(w)
                lbl = QtWidgets.QLabel(
                    f"{name} failed to load.\n\nSee the error log:\n{_LOG_FILE}")
                lbl.setWordWrap(True)
                lbl.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
                lay.addWidget(lbl); lay.addStretch(1)
                return w

        self._view_tab   = _tab(DataViewerTab,   "Data Viewer")
        self._mask_tab   = _tab(MaskTab,         "Mask Builder")
        self._cal_tab    = _tab(CalibrationTab,  "Calibrate")
        self._batch_tab  = _tab(BatchTab,        "Batch Integrate")
        self._refine_tab = _tab(RefinementTab,   "Calib. Refinement")
        self._corr_tab   = _tab(CorrectionsTab,  "Corrections")
        self._pdf_tab    = _tab(PDFTab,          "PDF Analysis")
        self._tex_tab    = _tab(TextureTab,      "Texture")
        self._export_tab = _tab(ExportTab,       "Results & Export")

        tabs.addTab(self._view_tab,   "⓪  Data Viewer")
        tabs.addTab(self._mask_tab,   "①  Mask Builder")
        tabs.addTab(self._cal_tab,    "②  Calibrate")
        tabs.addTab(self._refine_tab, "③  Calib. Refinement")
        tabs.addTab(self._batch_tab,  "④  Batch Integrate")
        tabs.addTab(self._corr_tab,   "⑤  Corrections")
        tabs.addTab(self._pdf_tab,    "⑥  PDF Analysis")
        tabs.addTab(self._tex_tab,    "⑦  Texture")
        tabs.addTab(self._export_tab, "⑧  Results & Export")

        # Wire cross-tab signals defensively (skip any placeholder tab).
        def _connect(src, signal_name, targets, slot_name):
            sig = getattr(src, signal_name, None)
            if sig is None:
                return
            for t in targets:
                slot = getattr(t, slot_name, None)
                if slot is not None:
                    try:
                        sig.connect(slot)
                    except Exception:
                        _log(f"Signal wiring {signal_name}->{slot_name} failed:\n"
                             f"{traceback.format_exc()}")

        # Mask propagation
        _connect(self._mask_tab, "maskReady",
                 (self._cal_tab, self._batch_tab, self._refine_tab, self._corr_tab,
                  self._pdf_tab, self._tex_tab, self._export_tab), "set_mask_from_tab1")
        # Calibration propagation (Tab 2 result → consumers)
        _connect(self._cal_tab, "calibrationDone",
                 (self._batch_tab, self._mask_tab, self._refine_tab, self._corr_tab,
                  self._pdf_tab, self._tex_tab, self._export_tab), "set_calibration")
        # Refined geometry (Tab 4) re-broadcasts to the calibration consumers
        _connect(self._refine_tab, "refinedResult",
                 (self._batch_tab, self._mask_tab, self._corr_tab, self._pdf_tab,
                  self._tex_tab, self._export_tab), "set_calibration")

        self.statusBar().showMessage(
            "Tip: mask → calibrate → (refine) → batch integrate")


def main():
    _install_diagnostics()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("MIDAS GUI")
    app.setStyle("Fusion")

    pal = QtGui.QPalette()
    for role, col in [
        (QtGui.QPalette.Window,          S.BG),
        (QtGui.QPalette.WindowText,      S.TEXT),
        (QtGui.QPalette.Base,            S.INPUT_BG),
        (QtGui.QPalette.AlternateBase,   "#e4e4e4"),
        (QtGui.QPalette.Text,            S.INPUT_FG),
        (QtGui.QPalette.Button,          "#444444"),
        (QtGui.QPalette.ButtonText,      S.TEXT),
        (QtGui.QPalette.Highlight,       S.ACCENT),
        (QtGui.QPalette.HighlightedText, "#ffffff"),
        (QtGui.QPalette.ToolTipBase,     "#2d2d30"),
        (QtGui.QPalette.ToolTipText,     S.TEXT),
    ]:
        pal.setColor(role, QtGui.QColor(col))
    app.setPalette(pal)
    app.setStyleSheet(S.stylesheet(_CHECKMARK_SVG, _ARROW_UP_SVG, _ARROW_DOWN_SVG))

    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
