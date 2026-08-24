"""Application shell: MainWindow, dark palette, Dioptas-inspired stylesheet, main()."""
from __future__ import annotations

import faulthandler
import inspect
import json
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
from midas_gui import bridge_server
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
from midas_gui import project

_CHECKMARK_SVG = _make_checkmark_svg()
_ARROW_UP_SVG = _make_arrow_svg("up")
_ARROW_DOWN_SVG = _make_arrow_svg("down")


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"MIDAS GUI v{__version__}")
        self.resize(1600, 950)
        self._gui_state_path: Optional[str] = None   # last loaded/saved GUI-state path
        self._project_ctx = project.ProjectContext()  # currently-open FAIR provenance project
        self._build_ui()
        # Lets B-PILOT (a separate Bluesky plan-runner GUI) auto-start Live
        # Data on a detector's PVA channel when it launches a scan — no-op
        # if B-PILOT never connects. See midas_gui/bridge_server.py.
        self._bridge_server = bridge_server.BridgeServer(
            self._resolve_and_start_live, log_fn=_log)
        self._bridge_server.start()

    def _resolve_and_start_live(self, prefix: str) -> None:
        pv = bridge_server.resolve_pv(prefix, C.DEVICES)
        if pv is None:
            _log(f"MIDAS bridge: no DEVICES entry for prefix {prefix!r}")
            return
        self._view_tab.start_live_pv(pv)

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

        # Cross-tab data sharing: every tab's DataLoaderPanel (plus Mask Builder's
        # bespoke loader) registers itself so any other tab's "Import from…" menu
        # can pull its currently-loaded file/folder/buffer. Defensive `getattr`
        # guards mirror `_connect()` below, in case a tab failed to build.
        from midas_gui.data_bridge import DataSourceRegistry
        self._data_registry = registry = DataSourceRegistry()
        for tab, label in (
            (self._view_tab, "Data Viewer"), (self._cal_tab, "Calibrate"),
            (self._refine_tab, "Calib. Refinement"), (self._batch_tab, "Batch Integrate"),
            (self._pump_tab, "Pump Probe"),
        ):
            loader = getattr(tab, "_loader", None)
            if loader is not None:
                try:
                    loader.bind_registry(registry, label)
                except Exception:
                    _log(f"Registry bind failed for {label}:\n{traceback.format_exc()}")
        bind_mask = getattr(self._mask_tab, "bind_registry", None)
        if bind_mask is not None:
            try:
                bind_mask(registry)
            except Exception:
                _log(f"Registry bind failed for Mask Builder:\n{traceback.format_exc()}")

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
            # Same hand-off, Hydra mode: per-panel geometry, keyed by panel number.
            self._cal_tab.pullHydraFromViewer.connect(
                lambda: self._cal_tab.import_hydra_from_viewer(self._view_tab.get_hydra_export()))
            self._cal_tab.sendHydraGeometryToViewer.connect(self._view_tab.set_hydra_panel_geometry)
            # Calibrate (Hydra mode) → Batch Integrate (Hydra mode): a panel's
            # fit auto-populates that panel's calibration source as soon as it
            # finishes, mirroring the single-detector calibrationDone wiring.
            self._cal_tab.hydraPanelCalibrationDone.connect(self._batch_tab.set_hydra_panel_calibration)
        except Exception:
            _log(f"Geometry hand-off wiring failed:\n{traceback.format_exc()}")

        # FAIR provenance: hand the (initially closed) project context to the
        # two tabs that log attempts to it. Defensive, like the wiring above —
        # a placeholder tab (failed to build) simply has no such method.
        for tab in (self._cal_tab, self._batch_tab):
            setter = getattr(tab, "set_project_context", None)
            if setter is not None:
                try:
                    setter(self._project_ctx)
                except Exception:
                    _log(f"Project-context wiring failed:\n{traceback.format_exc()}")

        self._build_file_menu()
        self._build_menu()
        self.statusBar().showMessage(
            "Tip: mask → calibrate → (refine) → batch integrate")
        self._project_lbl = QtWidgets.QLabel("Project: none")
        self._project_lbl.setToolTip(
            "The currently-open FAIR provenance project file (File → New/Open Project…).\n"
            "Calibrate and Batch Integrate runs are logged to it automatically while open.")
        self.statusBar().addPermanentWidget(self._project_lbl)

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

    # ── File menu: Save/Load GUI State ──────────────────────────────
    def _build_file_menu(self):
        m = self.menuBar().addMenu("&File")
        act_save = m.addAction("Save GUI State…")
        act_save.setShortcut(QtGui.QKeySequence("Ctrl+S"))
        act_save.triggered.connect(self._save_gui_state_dialog)
        act_save_as = m.addAction("Save GUI State As…")
        act_save_as.setShortcut(QtGui.QKeySequence("Ctrl+Shift+S"))
        act_save_as.triggered.connect(self._save_gui_state_as_dialog)
        act_load = m.addAction("Load GUI State…")
        act_load.setShortcut(QtGui.QKeySequence("Ctrl+O"))
        act_load.triggered.connect(self._load_gui_state_dialog)

        m.addSeparator()
        act_new_proj = m.addAction("New Project…")
        act_new_proj.triggered.connect(self._new_project_dialog)
        act_open_proj = m.addAction("Open Project…")
        act_open_proj.triggered.connect(self._open_project_dialog)
        self._close_proj_act = m.addAction("Close Project")
        self._close_proj_act.setEnabled(False)
        self._close_proj_act.triggered.connect(self._close_project)

    def _set_project_path(self, path) -> None:
        self._project_ctx.path = path
        self._project_lbl.setText(f"Project: {Path(path).name}" if path else "Project: none")
        self._close_proj_act.setEnabled(path is not None)

    def _new_project_dialog(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "New Project", "midas_project.h5", "MIDAS Project (*.h5)")
        if not path:
            return
        try:
            project.create_project(path)
        except FileExistsError:
            QtWidgets.QMessageBox.critical(
                self, "New Project failed",
                f"{path}\nalready exists — pick a new file, or use "
                "File → Open Project… to continue an existing one.")
            return
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "New Project failed", str(e))
            return
        self._set_project_path(path)
        QtWidgets.QMessageBox.information(
            self, "New Project",
            f"Created project:\n{path}\n\n"
            "Calibrate and Batch Integrate runs will now be logged to it "
            "automatically (for both single-detector and Hydra modes).")

    def _open_project_dialog(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open Project", "", "MIDAS Project (*.h5);;All files (*)")
        if not path:
            return
        try:
            project.open_project(path)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Open Project failed", str(e))
            return
        self._set_project_path(path)
        self._offer_populate_from_project(path)

    def _close_project(self):
        self._set_project_path(None)

    def _offer_populate_from_project(self, path) -> None:
        """After a successful File > Open Project…, offer to populate the
        Calibrate / Batch Integrate tabs from the project's recorded
        attempts — opening a project previously only wired it up for
        *future* logging and left every tab's fields exactly as they were,
        which meant "Open Project" didn't actually let you pick up a saved
        project's data paths/geometry/settings."""
        try:
            panels = {}
            for panel_key in project.discover_panels(path):
                calib = project.list_attempts(path, panel_key, "calib")
                integrate = project.list_attempts(path, panel_key, "integrate")
                if calib or integrate:
                    panels[panel_key] = {"calib": calib, "integrate": integrate}
        except Exception:
            _log(f"Reading project attempts failed:\n{traceback.format_exc()}")
            return
        if not panels:
            return

        from midas_gui.dialogs import ProjectLoadDialog
        dlg = ProjectLoadDialog(panels, self)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return

        try:
            calib_attempts = {k: project.read_attempt(path, ref)
                               for k, ref in dlg.calib_selection().items()}
            integrate_attempts = {k: project.read_attempt(path, ref)
                                   for k, ref in dlg.integrate_selection().items()}
            if calib_attempts:
                self._cal_tab.apply_project_calibration(calib_attempts)
            if integrate_attempts:
                self._batch_tab.apply_project_integration(integrate_attempts)
        except Exception:
            _log(f"Populate from project failed:\n{traceback.format_exc()}")
            QtWidgets.QMessageBox.critical(
                self, "Populate from project failed",
                f"Could not populate the GUI from this project's records.\n"
                f"See the error log:\n{_LOG_FILE}")
            return

        msg = []
        if calib_attempts:
            msg.append("Calibrate: " + ", ".join(sorted(calib_attempts)))
        if integrate_attempts:
            msg.append("Batch Integrate: " + ", ".join(sorted(integrate_attempts)))
        QtWidgets.QMessageBox.information(
            self, "Populate from project",
            "Populated from this project's recorded attempts:\n\n" + "\n".join(msg))

    def _save_gui_state_dialog(self):
        """Ctrl+S: overwrite the file this session last loaded/saved from, if
        any; otherwise fall back to a Save-As prompt (first save)."""
        if self._gui_state_path:
            self.save_gui_state(self._gui_state_path)
            return
        self._save_gui_state_as_dialog()

    def _save_gui_state_as_dialog(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save GUI State", self._gui_state_path or "midas_session.json",
            "JSON (*.json)")
        if not path:
            return
        self.save_gui_state(path)

    def _load_gui_state_dialog(self):
        if QtWidgets.QMessageBox.question(
                self, "Load GUI State",
                "Loading a saved session will overwrite the current values in "
                "every tab. Continue?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No) != QtWidgets.QMessageBox.Yes:
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load GUI State", "", "JSON (*.json);;All files (*)")
        if not path:
            return
        self.load_gui_state(path)

    @staticmethod
    def _accepts_sidecar_stem(fn) -> bool:
        try:
            return "sidecar_stem" in inspect.signature(fn).parameters
        except (TypeError, ValueError):
            return False

    def save_gui_state(self, path):
        """Dump every tab's ``get_state()`` into one JSON file. Tabs that hold
        derived data not yet exported to a file of its own (MaskTab, CalibrationTab)
        write small sidecars named after ``path`` so nothing in-progress is lost."""
        stem = str(Path(path).with_suffix(""))
        current = self._tabs.currentWidget()
        state = {"__midas_gui_state__": True, "version": 1, "tabs": {}}
        for widget, name, _always in self._tab_specs:
            if widget is current:
                state["active_tab"] = name
        errors = []
        for widget, name, _always in self._tab_specs:
            get_state = getattr(widget, "get_state", None)
            if get_state is None:
                continue
            try:
                if self._accepts_sidecar_stem(get_state):
                    state["tabs"][name] = get_state(sidecar_stem=stem)
                else:
                    state["tabs"][name] = get_state()
            except Exception:
                _log(f"Save GUI state failed for tab '{name}':\n{traceback.format_exc()}")
                errors.append(name)
        try:
            Path(path).write_text(json.dumps(state, indent=2))
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Save failed", str(e))
            return
        self._gui_state_path = path
        msg = f"Saved GUI state to:\n{path}"
        if errors:
            msg += "\n\nThe following tabs could not be saved:\n" + "\n".join(errors)
        QtWidgets.QMessageBox.information(self, "Save GUI State", msg)

    def load_gui_state(self, path):
        """Restore every tab's fields from a file written by :meth:`save_gui_state`.
        Path-backed fields (images, masks, dark/bright/background, HDF5 datasets)
        are re-loaded automatically; long-running pipelines (Fit, Batch Integrate,
        PDF transform, Refinement) are not re-run — their inputs are restored so a
        single click of the tab's own Run/Fit button reproduces the result."""
        try:
            data = json.loads(Path(path).read_text())
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Load failed", str(e))
            return
        if not data.get("__midas_gui_state__"):
            QtWidgets.QMessageBox.critical(
                self, "Load failed", "This file is not a MIDAS GUI state file.")
            return
        self._gui_state_path = path
        stem = str(Path(path).with_suffix(""))
        name_to_widget = {name: widget for widget, name, _always in self._tab_specs}
        errors, skipped = [], []
        for name, tstate in data.get("tabs", {}).items():
            widget = name_to_widget.get(name)
            set_state = getattr(widget, "set_state", None) if widget is not None else None
            if set_state is None:
                skipped.append(name)
                continue
            try:
                if self._accepts_sidecar_stem(set_state):
                    set_state(tstate, sidecar_stem=stem)
                else:
                    set_state(tstate)
            except Exception:
                _log(f"Load GUI state failed for tab '{name}':\n{traceback.format_exc()}")
                errors.append(name)
        active = data.get("active_tab")
        widget = name_to_widget.get(active) if active else None
        idx = self._tabs.indexOf(widget) if widget is not None else -1
        if idx >= 0:
            self._tabs.setCurrentIndex(idx)
        msg = (f"Loaded GUI state from:\n{path}\n\n"
               "Path-backed fields (images, masks, dark/bright/background) were "
               "reloaded. Fit / Batch Integrate / PDF transform / Refinement "
               "results are not recomputed — re-run each tab's own action to "
               "reproduce them.")
        if skipped:
            msg += "\n\nTabs in the file no longer present:\n" + "\n".join(skipped)
        if errors:
            msg += "\n\nThe following tabs failed to restore:\n" + "\n".join(errors)
        QtWidgets.QMessageBox.information(self, "Load GUI State", msg)

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
        for a in tab_attrs:                      # then any tab-level explicit shutdown
            tab = getattr(self, a, None)
            if tab is None:
                continue
            fn = getattr(tab, "shutdown", None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass

    def closeEvent(self, event):
        try:
            self._stop_all_workers()
        except Exception:
            _log(f"Worker shutdown on close failed:\n{traceback.format_exc()}")
        try:
            self._bridge_server.stop()
        except Exception:
            _log(f"Bridge server shutdown failed:\n{traceback.format_exc()}")
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
