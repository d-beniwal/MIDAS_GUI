"""Preferences dialog — edit local defaults, materials, calibrants, menus and
algorithms; save them as your local defaults, load/save a JSON config, or reset to
the shipped defaults.

Tables are pre-filled with the current *effective* values (shipped defaults plus
whatever your config already sets), so you add / remove / modify from a complete
list. Saving writes the per-user config file (:mod:`midas_gui.settings`); because
widgets bake values at construction, changes apply on the next launch.
"""
from __future__ import annotations

import json

from PyQt5 import QtWidgets

from midas_gui import settings
from midas_gui import constants as C

_PATH_ROWS = [
    ("calibrant_tif", "Calibrant TIFF:", "file"),
    ("calibrant_h5", "Calibrant HDF5:", "file"),
    ("nickel_h5", "Sample HDF5:", "file"),
    ("nickel_dir", "Sample folder:", "dir"),
    ("nickel_frame0", "Sample frame:", "file"),
    ("calib_file", "Calibration file:", "file"),
    ("pdf_iq_file", "PDF I(Q) file:", "file"),
    ("pdf_calib", "PDF calibration:", "file"),
]
_MAT_HEADERS = ["name", "a", "b", "c", "α", "β", "γ", "SG"]


def _effective_cfg() -> dict:
    """Snapshot the current effective defaults from ``constants`` into the schema."""
    mats = {n: dict(m) for n, m in C.MATERIALS.items()}
    cals = {}
    for n in C.CALIBRANTS:
        m = C._LATT.get(n)
        if m is None and n in C._LC:
            a, b, c, al, be, ga = C._LC[n]
            m = {"a": a, "b": b, "c": c, "alpha": al, "beta": be, "gamma": ga,
                 "sg": C._SG.get(n, 225)}
        if m:
            cals[n] = dict(m)
    return {
        "geometry": {
            "wavelength_A": C.DEFAULT_WAVELENGTH, "pixel_um": C.DEFAULT_PIXEL_UM,
            "lsd_um": C.DEFAULT_LSD_UM, "bc_y": C.DEFAULT_BC_Y, "bc_z": C.DEFAULT_BC_Z,
            "pixel_presets": [list(p) for p in C.PIXEL_PRESETS],
            "k_edge_foils": [list(k) for k in C.K_EDGE_FOILS],
        },
        "materials": mats,
        "calibrants": cals,
        "paths": {
            "calibrant_tif": C.DEFAULT_CALIBRANT_TIF, "calibrant_h5": C.DEFAULT_CALIBRANT_H5,
            "nickel_h5": C.DEFAULT_NICKEL_H5, "nickel_dir": C.DEFAULT_NICKEL_DIR,
            "nickel_frame0": C.DEFAULT_NICKEL_FRAME0, "calib_file": C.DEFAULT_CALIB_FILE,
            "pdf_iq_file": C.DEFAULT_PDF_IQ_FILE, "pdf_calib": C.DEFAULT_PDF_CALIB,
        },
        "ui": {
            "integration_kernel": C.DEFAULT_KERNEL, "calibration_pipeline": C.DEFAULT_PIPELINE,
            "output_format": C.DEFAULT_OUTPUT_FORMAT, "azimuthal_method": C.DEFAULT_ERROR_MODEL,
            "plot_theme": C.DEFAULT_COLORMAP, "visible_tabs": list(C.DEFAULT_VISIBLE_TABS),
        },
    }


class PreferencesDialog(QtWidgets.QDialog):
    def __init__(self, main_window=None, parent=None):
        super().__init__(parent or main_window)
        self._mw = main_window
        self.setWindowTitle("MIDAS GUI — Preferences")
        self.setMinimumSize(620, 560)

        root = QtWidgets.QVBoxLayout(self)
        info = QtWidgets.QLabel(
            "Your local defaults. Lists below start from the shipped defaults — "
            "add / remove / modify as you like. Changes apply on the next launch.")
        info.setWordWrap(True); info.setStyleSheet("color:#aaa;font-size:11px")
        root.addWidget(info)

        self._tabs = QtWidgets.QTabWidget(); root.addWidget(self._tabs, 1)
        self._build_geometry_tab()
        self._build_paths_tab()
        self._mat_table = self._build_table_tab("Materials", _MAT_HEADERS)
        self._cal_table = self._build_table_tab("Calibrants", _MAT_HEADERS)
        self._build_menus_tab()
        self._build_algorithms_tab()
        self._build_tabs_tab()

        # action row
        arow = QtWidgets.QHBoxLayout()
        for label, tip, slot in (
            ("Save current GUI state", "Copy the Data Viewer's live λ / pixel / Lsd / "
             "beam centre into the geometry fields.", self._capture_state),
            ("Load config (JSON)…", "Load a JSON config file into this form.",
             self._load_json),
            ("Save config to JSON…", "Export the current form to a JSON file to share.",
             self._save_json),
            ("Reset to shipped defaults", "Discard your local config and return to the "
             "shipped defaults.", self._reset),
        ):
            b = QtWidgets.QPushButton(label); b.setToolTip(tip); b.clicked.connect(slot)
            arow.addWidget(b)
        arow.addStretch(1)
        root.addLayout(arow)

        loc = QtWidgets.QLabel(f"Local config: {settings.user_config_path()}")
        loc.setStyleSheet("color:#888;font-size:10px"); loc.setWordWrap(True)
        root.addWidget(loc)

        bb = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        bb.button(QtWidgets.QDialogButtonBox.Save).setText("Save as my defaults")
        bb.accepted.connect(self._save); bb.rejected.connect(self.reject)
        root.addWidget(bb)

        self._populate(_effective_cfg())

    # ── tab builders ───────────────────────────────────────────────────
    def _build_geometry_tab(self):
        w = QtWidgets.QWidget(); f = QtWidgets.QFormLayout(w)
        self._g_wl = QtWidgets.QLineEdit(); self._g_px = QtWidgets.QLineEdit()
        self._g_lsd = QtWidgets.QLineEdit(); self._g_bcy = QtWidgets.QLineEdit()
        self._g_bcz = QtWidgets.QLineEdit()
        f.addRow("Wavelength λ (Å):", self._g_wl)
        f.addRow("Pixel size (µm):", self._g_px)
        f.addRow("Lsd (µm):", self._g_lsd)
        f.addRow("Beam centre y (px):", self._g_bcy)
        f.addRow("Beam centre z (px):", self._g_bcz)
        self._tabs.addTab(w, "Geometry")

    def _build_paths_tab(self):
        w = QtWidgets.QWidget(); f = QtWidgets.QFormLayout(w)
        self._paths = {}
        for key, label, kind in _PATH_ROWS:
            ed = QtWidgets.QLineEdit(); self._paths[key] = ed
            b = QtWidgets.QPushButton("…"); b.setFixedWidth(28)
            b.clicked.connect(lambda _=0, e=ed, k=kind: self._browse_path(e, k))
            r = QtWidgets.QHBoxLayout(); r.setSpacing(4); r.addWidget(ed); r.addWidget(b)
            f.addRow(label, r)
        self._tabs.addTab(w, "Paths")

    def _build_table_tab(self, title, headers):
        w = QtWidgets.QWidget(); v = QtWidgets.QVBoxLayout(w)
        table = QtWidgets.QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setColumnWidth(0, 150)
        v.addWidget(table, 1)
        br = QtWidgets.QHBoxLayout()
        add = QtWidgets.QPushButton("Add")
        add.clicked.connect(lambda: self._add_row(table, headers))
        rem = QtWidgets.QPushButton("Remove selected")
        rem.clicked.connect(lambda: self._remove_rows(table))
        br.addWidget(add); br.addWidget(rem); br.addStretch(1)
        v.addLayout(br)
        self._tabs.addTab(w, title)
        return table

    def _build_menus_tab(self):
        w = QtWidgets.QWidget(); v = QtWidgets.QVBoxLayout(w)
        v.addWidget(QtWidgets.QLabel("Pixel-size presets (clickable 'px' menu)"))
        self._px_table = QtWidgets.QTableWidget(0, 2)
        self._px_table.setHorizontalHeaderLabels(["label", "µm"])
        v.addWidget(self._px_table, 1)
        r1 = QtWidgets.QHBoxLayout()
        a1 = QtWidgets.QPushButton("Add"); a1.clicked.connect(
            lambda: self._add_row(self._px_table, ["label", "µm"]))
        d1 = QtWidgets.QPushButton("Remove selected"); d1.clicked.connect(
            lambda: self._remove_rows(self._px_table))
        r1.addWidget(a1); r1.addWidget(d1); r1.addStretch(1); v.addLayout(r1)
        v.addWidget(QtWidgets.QLabel("K-edge foils (clickable 'λ' menu)"))
        self._ke_table = QtWidgets.QTableWidget(0, 2)
        self._ke_table.setHorizontalHeaderLabels(["element", "keV"])
        v.addWidget(self._ke_table, 1)
        r2 = QtWidgets.QHBoxLayout()
        a2 = QtWidgets.QPushButton("Add"); a2.clicked.connect(
            lambda: self._add_row(self._ke_table, ["element", "keV"]))
        d2 = QtWidgets.QPushButton("Remove selected"); d2.clicked.connect(
            lambda: self._remove_rows(self._ke_table))
        r2.addWidget(a2); r2.addWidget(d2); r2.addStretch(1); v.addLayout(r2)
        self._tabs.addTab(w, "Menus")

    def _build_algorithms_tab(self):
        w = QtWidgets.QWidget(); f = QtWidgets.QFormLayout(w)
        self._ui_kernel = QtWidgets.QComboBox()
        for label, key in C.KERNELS.items():
            self._ui_kernel.addItem(label, key)
        self._ui_pipe = QtWidgets.QComboBox()
        for label, key, enabled in C.PIPELINES:
            self._ui_pipe.addItem(label, key)
            if not enabled:
                self._ui_pipe.model().item(self._ui_pipe.count() - 1).setEnabled(False)
        self._ui_fmt = QtWidgets.QComboBox()
        for label in C.OUTPUT_FORMATS:
            self._ui_fmt.addItem(label, C.OUTPUT_FORMATS[label])
        self._ui_err = QtWidgets.QComboBox(); self._ui_err.addItems(C.ERROR_MODELS)
        self._ui_cmap = QtWidgets.QComboBox(); self._ui_cmap.addItems(C.COLORMAPS)
        f.addRow("Calibration pipeline:", self._ui_pipe)
        f.addRow("Integration kernel:", self._ui_kernel)
        f.addRow("Output format:", self._ui_fmt)
        f.addRow("Error model:", self._ui_err)
        f.addRow("Colormap / theme:", self._ui_cmap)
        self._tabs.addTab(w, "Algorithms")

    def _build_tabs_tab(self):
        w = QtWidgets.QWidget(); v = QtWidgets.QVBoxLayout(w)
        v.addWidget(QtWidgets.QLabel(
            "Choose which tabs are visible. Data Viewer, Mask, Calibrate and Batch "
            "Integrate are always shown. Changes apply immediately."))
        self._tab_checks = {}
        for name in C.ALWAYS_TABS:
            cb = QtWidgets.QCheckBox(name); cb.setChecked(True); cb.setEnabled(False)
            v.addWidget(cb); self._tab_checks[name] = cb
        for name in C.OPTIONAL_TABS:
            cb = QtWidgets.QCheckBox(name)
            v.addWidget(cb); self._tab_checks[name] = cb
        v.addStretch(1)
        self._tabs.addTab(w, "Tabs")

    # ── table helpers ──────────────────────────────────────────────────
    def _add_row(self, table, headers, values=None):
        r = table.rowCount(); table.insertRow(r)
        values = values or [""] * table.columnCount()
        for c in range(table.columnCount()):
            val = values[c] if c < len(values) else ""
            table.setItem(r, c, QtWidgets.QTableWidgetItem("" if val == "" else str(val)))

    def _remove_rows(self, table):
        for r in sorted({i.row() for i in table.selectedIndexes()}, reverse=True):
            table.removeRow(r)

    def _mat_dict(self, table) -> dict:
        out = {}
        for r in range(table.rowCount()):
            def cell(c):
                it = table.item(r, c); return it.text().strip() if it else ""
            name = cell(0)
            if not name:
                continue
            try:
                out[name] = {"a": float(cell(1)), "b": float(cell(2)), "c": float(cell(3)),
                             "alpha": float(cell(4)), "beta": float(cell(5)),
                             "gamma": float(cell(6)), "sg": int(float(cell(7)))}
            except Exception:
                pass
        return out

    def _pairs(self, table):
        out = []
        for r in range(table.rowCount()):
            a = table.item(r, 0); b = table.item(r, 1)
            an = a.text().strip() if a else ""
            try:
                if an:
                    out.append([an, float(b.text())])
            except Exception:
                pass
        return out

    # ── populate / assemble ────────────────────────────────────────────
    def _populate(self, cfg):
        geo = cfg.get("geometry", {})
        self._g_wl.setText(str(geo.get("wavelength_A", "")))
        self._g_px.setText(str(geo.get("pixel_um", "")))
        self._g_lsd.setText(str(geo.get("lsd_um", "")))
        self._g_bcy.setText(str(geo.get("bc_y", "")))
        self._g_bcz.setText(str(geo.get("bc_z", "")))
        paths = cfg.get("paths", {})
        for key, ed in self._paths.items():
            ed.setText(str(paths.get(key, "") or ""))
        self._mat_table.setRowCount(0)
        for name, m in (cfg.get("materials", {}) or {}).items():
            self._add_row(self._mat_table, _MAT_HEADERS,
                          [name, m.get("a"), m.get("b"), m.get("c"),
                           m.get("alpha"), m.get("beta"), m.get("gamma"), m.get("sg")])
        self._cal_table.setRowCount(0)
        for name, m in (cfg.get("calibrants", {}) or {}).items():
            self._add_row(self._cal_table, _MAT_HEADERS,
                          [name, m.get("a"), m.get("b"), m.get("c"),
                           m.get("alpha"), m.get("beta"), m.get("gamma"), m.get("sg")])
        self._px_table.setRowCount(0)
        for p in geo.get("pixel_presets", []) or []:
            self._add_row(self._px_table, ["label", "µm"], list(p))
        self._ke_table.setRowCount(0)
        for k in geo.get("k_edge_foils", []) or []:
            self._add_row(self._ke_table, ["element", "keV"], list(k))
        ui = cfg.get("ui", {})
        self._select(self._ui_kernel, ui.get("integration_kernel"), by_data=True)
        self._select(self._ui_pipe, ui.get("calibration_pipeline"), by_data=True)
        self._select(self._ui_fmt, ui.get("output_format"), by_data=True)
        self._select(self._ui_err, ui.get("azimuthal_method"))
        self._select(self._ui_cmap, ui.get("plot_theme"))
        visible = ui.get("visible_tabs")
        if isinstance(visible, list):
            vis = set(visible)
            for name, cb in self._tab_checks.items():
                if cb.isEnabled():           # skip the always-on (disabled) boxes
                    cb.setChecked(name in vis)

    @staticmethod
    def _select(combo, value, by_data=False):
        if value is None:
            return
        i = combo.findData(value) if by_data else combo.findText(str(value))
        if i >= 0:
            combo.setCurrentIndex(i)

    def _assemble(self) -> dict:
        def num(ed):
            t = ed.text().strip()
            return float(t) if t else None
        geo = {}
        for key, ed in (("wavelength_A", self._g_wl), ("pixel_um", self._g_px),
                        ("lsd_um", self._g_lsd), ("bc_y", self._g_bcy), ("bc_z", self._g_bcz)):
            v = num(ed)
            if v is not None:
                geo[key] = v
        geo["pixel_presets"] = self._pairs(self._px_table)
        geo["k_edge_foils"] = self._pairs(self._ke_table)
        paths = {k: ed.text().strip() for k, ed in self._paths.items() if ed.text().strip()}
        return {
            "geometry": geo,
            "materials": self._mat_dict(self._mat_table),
            "calibrants": self._mat_dict(self._cal_table),
            "paths": paths,
            "ui": {
                "calibration_pipeline": self._ui_pipe.currentData(),
                "integration_kernel": self._ui_kernel.currentData(),
                "output_format": self._ui_fmt.currentData(),
                "azimuthal_method": self._ui_err.currentText(),
                "plot_theme": self._ui_cmap.currentText(),
                "visible_tabs": [name for name, cb in self._tab_checks.items()
                                 if cb.isChecked()],
            },
        }

    # ── actions ────────────────────────────────────────────────────────
    def _browse_path(self, edit, kind):
        if kind == "dir":
            p = QtWidgets.QFileDialog.getExistingDirectory(self, "Select folder")
        else:
            p, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select file")
        if p:
            edit.setText(p)

    def _capture_state(self):
        try:
            g = self._mw._view_tab.get_geometry()
        except Exception:
            QtWidgets.QMessageBox.warning(self, "Unavailable",
                                          "Could not read the Data Viewer geometry.")
            return
        self._g_wl.setText(str(g.get("wavelength_A", "")))
        self._g_px.setText(str(g.get("pxY", "")))
        self._g_lsd.setText(str(g.get("Lsd", "")))
        self._g_bcy.setText(str(g.get("BC_y", "")))
        self._g_bcz.setText(str(g.get("BC_z", "")))

    def _load_json(self):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load config (JSON)", "", "JSON (*.json);;All (*)")
        if not p:
            return
        try:
            cfg = json.loads(open(p).read())
            assert isinstance(cfg, dict)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Load failed", str(e)); return
        # merge over the effective snapshot so missing sections keep sensible values
        base = _effective_cfg()
        for k in ("geometry", "materials", "calibrants", "paths", "ui"):
            if k in cfg:
                base[k] = cfg[k]
        self._populate(base)
        QtWidgets.QMessageBox.information(
            self, "Loaded", "Config loaded into the form. Click 'Save as my defaults' to keep it.")

    def _save_json(self):
        p, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save config to JSON", "midas_gui_config.json", "JSON (*.json)")
        if not p:
            return
        try:
            with open(p, "w") as fh:
                json.dump(self._assemble(), fh, indent=2)
            QtWidgets.QMessageBox.information(self, "Saved", f"Config written to:\n{p}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Save failed", str(e))

    def _reset(self):
        if QtWidgets.QMessageBox.question(
                self, "Reset to shipped defaults",
                "Discard your local config and return to the shipped defaults?\n"
                "(Takes effect on the next launch.)") != QtWidgets.QMessageBox.Yes:
            return
        try:
            settings.reset_user_config()
            QtWidgets.QMessageBox.information(self, "Reset",
                                             "Local config removed. Restart to apply.")
            self.accept()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Reset failed", str(e))

    def _save(self):
        cfg = self._assemble()
        try:
            path = settings.save_user_config(cfg)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Save failed", str(e)); return
        # Tab visibility can apply live (all tabs already exist); other settings
        # (baked into widgets at construction) still need a restart.
        applied_live = False
        try:
            if self._mw is not None and hasattr(self._mw, "apply_tab_visibility"):
                self._mw.apply_tab_visibility(cfg["ui"]["visible_tabs"])
                applied_live = True
        except Exception:
            pass
        note = ("Tab visibility applied now; restart the GUI for other changes to apply."
                if applied_live else "Restart the GUI to apply.")
        QtWidgets.QMessageBox.information(
            self, "Saved", f"Saved as your defaults:\n{path}\n\n{note}")
        self.accept()
