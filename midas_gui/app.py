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
from midas_gui.tab_pumpprobe import PumpProbeTab
from midas_gui.tab_export import ExportTab
from midas_gui import constants as C

_CHECKMARK_SVG = _make_checkmark_svg()
_ARROW_UP_SVG = _make_arrow_svg("up")
_ARROW_DOWN_SVG = _make_arrow_svg("down")


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"MIDAS GUI v{__version__}")
        self.resize(1600, 950)
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
        self._pump_tab   = _tab(PumpProbeTab,    "Pump Probe")
        self._export_tab = _tab(ExportTab,       "Results & Export")

        # All tabs are always CONSTRUCTED (cross-tab wiring below relies on the
        # attributes existing); modular visibility only controls which are ADDED to
        # the QTabWidget. `always` tabs are pinned; the rest are toggled from
        # Preferences (see apply_tab_visibility / constants.DEFAULT_VISIBLE_TABS).
        self._tabs = tabs
        self._tab_specs = [
            (self._view_tab,   "Data Viewer",       True),
            (self._mask_tab,   "Mask Builder",      True),
            (self._cal_tab,    "Calibrate",         True),
            (self._refine_tab, "Calib. Refinement", False),
            (self._batch_tab,  "Batch Integrate",   True),
            (self._corr_tab,   "Corrections",       False),
            (self._pdf_tab,    "PDF Analysis",      False),
            (self._tex_tab,    "Texture",           False),
            (self._pump_tab,   "Pump Probe",        False),
            (self._export_tab, "Results & Export",  False),
        ]
        self.apply_tab_visibility()

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
                 (self._view_tab, self._cal_tab, self._batch_tab, self._refine_tab,
                  self._corr_tab, self._pdf_tab, self._tex_tab, self._pump_tab,
                  self._export_tab),
                 "set_mask_from_tab1")
        # Calibration propagation (Tab 2 result → consumers)
        _connect(self._cal_tab, "calibrationDone",
                 (self._batch_tab, self._mask_tab, self._refine_tab, self._corr_tab,
                  self._pdf_tab, self._tex_tab, self._pump_tab, self._export_tab),
                 "set_calibration")
        # Refined geometry (Tab 4) re-broadcasts to the calibration consumers
        _connect(self._refine_tab, "refinedResult",
                 (self._batch_tab, self._mask_tab, self._corr_tab, self._pdf_tab,
                  self._tex_tab, self._pump_tab, self._export_tab), "set_calibration")

        # Geometry hand-off between Data Viewer (Tab 0) and Calibrate (Tab 2):
        #   Data Viewer "→ Send geometry to Calibrate" pushes its values;
        #   Calibrate "← Data Viewer" pulls them. Both land in Calibrate.apply_geometry.
        try:
            self._view_tab.pushGeometry.connect(self._cal_tab.apply_geometry)
            self._cal_tab.pullGeometry.connect(
                lambda: self._cal_tab.apply_geometry(self._view_tab.get_geometry()))
            # Calibrate → Data Viewer: push calibrated geometry into the Viewer fields.
            self._cal_tab.sendGeometryToViewer.connect(self._view_tab.set_geometry)
        except Exception:
            _log(f"Geometry hand-off wiring failed:\n{traceback.format_exc()}")

        self._build_menu()
        self.statusBar().showMessage(
            "Tip: mask → calibrate → (refine) → batch integrate")

    # ── modular tab visibility ─────────────────────────────────────
    _NUMERALS = "⓪①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭"

    def apply_tab_visibility(self, visible=None):
        """Rebuild the tab bar from ``self._tab_specs``, showing every pinned tab
        plus the optional tabs named in ``visible`` (default:
        ``constants.DEFAULT_VISIBLE_TABS``). Numerals are renumbered so they stay
        contiguous. All tab widgets already exist, so this applies live (no
        restart) and preserves each tab's in-memory state."""
        if visible is None:
            visible = getattr(C, "DEFAULT_VISIBLE_TABS", None) or []
        visible = set(visible)
        current = self._tabs.currentWidget()
        self._tabs.clear()
        i = 0
        for widget, name, always in self._tab_specs:
            if not (always or name in visible):
                continue
            prefix = self._NUMERALS[i] if i < len(self._NUMERALS) else str(i)
            self._tabs.addTab(widget, f"{prefix}  {name}")
            i += 1
        # keep the previously-selected tab focused if it is still shown
        idx = self._tabs.indexOf(current) if current is not None else -1
        if idx >= 0:
            self._tabs.setCurrentIndex(idx)

    def _build_menu(self):
        """Settings menu: preferences, open config folder, reload."""
        m = self.menuBar().addMenu("&Settings")
        act_pref = m.addAction("Preferences…")
        act_pref.triggered.connect(self._open_preferences)
        act_scale = m.addAction("Interface scaling…")
        act_scale.triggered.connect(self._open_scaling)
        m.addSeparator()
        act_open = m.addAction("Open config folder")
        act_open.triggered.connect(self._open_config_folder)
        act_reload = m.addAction("Reload config")
        act_reload.triggered.connect(self._reload_config)

    def _open_preferences(self):
        try:
            from midas_gui.prefs_dialog import PreferencesDialog
            PreferencesDialog(self).exec_()
        except Exception:
            _log(f"Preferences dialog failed:\n{traceback.format_exc()}")

    def _open_scaling(self):
        """Quick interface-scale picker (whole-app zoom for HiDPI / 4K monitors).
        Persists ui.ui_scale and offers to relaunch so the new scale takes effect."""
        from midas_gui import settings
        cur = float(getattr(C, "DEFAULT_UI_SCALE", 1.0) or 1.0)
        val, ok = QtWidgets.QInputDialog.getDouble(
            self, "Interface scaling",
            "Whole-interface scale (layout + fonts).\n"
            "1.0 ≈ 1080p,  ~1.5 ≈ 1440p,  ~2.0 ≈ 4K.\nApplies after a restart.",
            cur, 0.5, 4.0, 2)
        if not ok or abs(val - cur) < 1e-6:
            return
        try:
            cfg = settings.load_config()
            cfg.setdefault("ui", {})["ui_scale"] = float(val)
            settings.save_user_config(cfg)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Save failed", str(e)); return
        self._offer_restart(f"Interface scale set to {val:g}×.")

    def _offer_restart(self, msg):
        """Ask to relaunch now so a startup-only setting (e.g. UI scale) applies."""
        if QtWidgets.QMessageBox.question(
                self, "Restart to apply",
                f"{msg}\n\nRestart the GUI now to apply?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.Yes) == QtWidgets.QMessageBox.Yes:
            self.restart_app()

    def restart_app(self):
        """Relaunch a fresh instance (which re-reads the scale) and close this one.
        Uses QProcess so the new process is detached from this one."""
        try:
            self._stop_all_workers()
        except Exception:
            pass
        import os
        prog = sys.executable
        if os.path.basename(sys.argv[0] or "") == "__main__.py":
            args = ["-m", "midas_gui", *sys.argv[1:]]
        else:
            args = list(sys.argv)
        try:
            QtCore.QProcess.startDetached(prog, args)
        except Exception:
            _log(f"Restart failed:\n{traceback.format_exc()}")
        QtWidgets.QApplication.quit()

    def _open_config_folder(self):
        from midas_gui import settings
        folder = settings.user_config_path().parent
        folder.mkdir(parents=True, exist_ok=True)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(folder)))

    def _reload_config(self):
        from midas_gui import settings
        settings.reload()
        QtWidgets.QMessageBox.information(
            self, "Config reloaded",
            "Config re-read from disk. Restart the GUI for changes to take full effect.")

    # ── clean shutdown ─────────────────────────────────────────────
    def _stop_all_workers(self):
        """Interruption-request + bounded-wait every background QThread on every
        tab (and any parked orphans), so nothing is left running at teardown — a
        live QThread at interpreter exit is a common hard-crash cause."""
        tab_attrs = ("_view_tab", "_mask_tab", "_cal_tab", "_refine_tab",
                     "_batch_tab", "_corr_tab", "_pdf_tab", "_tex_tab",
                     "_pump_tab", "_export_tab")
        threads = []
        for a in tab_attrs:
            tab = getattr(self, a, None)
            if tab is None:
                continue
            for val in list(vars(tab).values()):
                if isinstance(val, QtCore.QThread):
                    threads.append(val)
                elif isinstance(val, list):
                    threads.extend(x for x in val if isinstance(x, QtCore.QThread))
        for th in threads:                       # ask everyone to stop first
            try:
                if th.isRunning():
                    th.requestInterruption()
            except Exception:
                pass
        for th in threads:                       # then bounded wait
            try:
                if th.isRunning():
                    th.wait(2000)
            except Exception:
                pass

    def closeEvent(self, event):
        try:
            self._stop_all_workers()
        except Exception:
            _log(f"Worker shutdown on close failed:\n{traceback.format_exc()}")
        super().closeEvent(event)


def _apply_ui_scale():
    """Set Qt's whole-application scale factor from the configured ui.ui_scale BEFORE
    the QApplication is created, so layout + fonts scale uniformly on HiDPI / 4K
    screens. Must run before any QApplication instance exists."""
    import os
    try:
        scale = float(getattr(C, "DEFAULT_UI_SCALE", 1.0) or 1.0)
    except Exception:
        scale = 1.0
    scale = min(4.0, max(0.5, scale))
    # In-app setting is authoritative (overwrites any inherited value on restart).
    os.environ["QT_SCALE_FACTOR"] = f"{scale:.4g}"
    _log(f"UI scale (QT_SCALE_FACTOR) = {scale:.4g}")


def main():
    _install_diagnostics()
    _apply_ui_scale()   # must precede QApplication construction
    # Crisper icons/pixmaps at non-unit scales (attribute set before QApplication).
    try:
        QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)
    except Exception:
        pass
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
