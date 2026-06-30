"""Application shell: MainWindow, dark palette, Dioptas-inspired stylesheet, main()."""
from __future__ import annotations

import sys

import midas_gui._paths  # noqa: F401  (KMP_DUPLICATE_LIB_OK env var)
from midas_gui import __version__
from PyQt5 import QtGui, QtWidgets

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

        self._view_tab   = DataViewerTab()
        self._mask_tab   = MaskTab()
        self._cal_tab    = CalibrationTab()
        self._batch_tab  = BatchTab()
        self._refine_tab = RefinementTab()
        self._corr_tab   = CorrectionsTab()
        self._pdf_tab    = PDFTab()
        self._tex_tab    = TextureTab()
        self._export_tab = ExportTab()

        tabs.addTab(self._view_tab,   "⓪  Data Viewer")
        tabs.addTab(self._mask_tab,   "①  Mask Builder")
        tabs.addTab(self._cal_tab,    "②  Calibrate")
        tabs.addTab(self._refine_tab, "③  Calib. Refinement")
        tabs.addTab(self._batch_tab,  "④  Batch Integrate")
        tabs.addTab(self._corr_tab,   "⑤  Corrections")
        tabs.addTab(self._pdf_tab,    "⑥  PDF Analysis")
        tabs.addTab(self._tex_tab,    "⑦  Texture")
        tabs.addTab(self._export_tab, "⑧  Results & Export")

        # Mask propagation
        for slot in (self._cal_tab.set_mask_from_tab1, self._batch_tab.set_mask_from_tab1,
                     self._refine_tab.set_mask_from_tab1, self._corr_tab.set_mask_from_tab1,
                     self._pdf_tab.set_mask_from_tab1, self._tex_tab.set_mask_from_tab1,
                     self._export_tab.set_mask_from_tab1):
            self._mask_tab.maskReady.connect(slot)

        # Calibration propagation (Tab 2 result → consumers)
        for slot in (self._batch_tab.set_calibration, self._mask_tab.set_calibration,
                     self._refine_tab.set_calibration, self._corr_tab.set_calibration,
                     self._pdf_tab.set_calibration, self._tex_tab.set_calibration,
                     self._export_tab.set_calibration):
            self._cal_tab.calibrationDone.connect(slot)

        # Refined geometry (Tab 4) re-broadcasts to the calibration consumers
        for slot in (self._batch_tab.set_calibration, self._mask_tab.set_calibration,
                     self._corr_tab.set_calibration, self._pdf_tab.set_calibration,
                     self._tex_tab.set_calibration, self._export_tab.set_calibration):
            self._refine_tab.refinedResult.connect(slot)

        self.statusBar().showMessage(
            "Tip: mask → calibrate → (refine) → batch integrate")


def main():
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
