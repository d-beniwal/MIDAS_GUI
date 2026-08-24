"""Per-panel calibration widget for the Calibrate tab's Hydra mode.

``HydraCalibPanelCard`` holds everything specific to ONE GE panel's
calibration run: its own Transforms (Flip Y/Flip Z/Transpose — physical
mounting differs per panel), its own initial seed (BC/Lsd/tilts — each GE
panel has a physically independent beam centre), Pick BC/Pick Ring wiring,
and its own fitted result + rings + Ring-Residuals chart + Results grid.
Everything shared across all 4 panels (pipeline, wavelength/pixel/calibrant,
refine-parameter choice, threshold, averaging, advanced settings) lives on
``HydraCalibrationPage`` instead — this card only owns what is genuinely
independent per physical panel.

Mirrors ``hydra_geometry_card.DetectorGeometryCard``'s viewer-rebind pattern
(``bind_viewer``) so switching the active panel is safe: the previous
viewer's pick-in-progress state is cleared, matching the fix in
`eadb14d` (Pick BC/Pick Ring point leakage across panels).
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Callable, Optional

import numpy as np
from PyQt5 import QtCore, QtWidgets
import pyqtgraph as pg

from midas_gui.constants import DEFAULT_WAVELENGTH, DEFAULT_PIXEL_UM, DEFAULT_LSD_UM, \
    DEFAULT_BC_Y, DEFAULT_BC_Z
from midas_gui.helpers import (
    _fspin, im_trans_codes_from_checkboxes, geometry_fields_from_file,
    _predict_ring_radii, tilted_ring_xy, paramstest_pairs, write_standalone_paramstest,
    _PARAMSTEST_DISTORTION)
from midas_gui.widgets import ResidualBarChart, _mono_font
from midas_gui.dialogs import _SaveParamstestDialog
from midas_gui import style as S


class HydraCalibPanelCard(QtWidgets.QWidget):
    """One GE panel's Transforms + Initial seed + fitted-result display."""

    #: a calibration-file was loaded into this panel's seed fields — carries
    #: the parsed geometry so the owning page can mirror λ/pixel into the
    #: shared Detector & Calibrant fields (same precedent as
    #: ``CalibrationTab.apply_geometry``).
    calibFileLoaded = QtCore.pyqtSignal(dict)
    #: Flip Y/Flip Z/Transpose toggled — the owning page refreshes the
    #: preview if this panel is currently displayed.
    imTransChanged = QtCore.pyqtSignal()
    #: "→ Send to Data Viewer" clicked, with this panel's number + geometry.
    sendToViewer = QtCore.pyqtSignal(int, dict)

    def __init__(self, panel_number: int, parent=None):
        super().__init__(parent)
        self.panel_number = panel_number
        self.result = None
        self._viewer = None
        self._log_fn: Optional[Callable[[str], None]] = None
        self._ring_items: list = []
        self._corrected_ring_items: list = []
        self._show_rings = True
        self._corrected = False
        self._build_ui()

    # ── Wiring ───────────────────────────────────────────────────

    def set_log_fn(self, fn: Callable[[str], None]):
        self._log_fn = fn

    def _log(self, text: str):
        if self._log_fn is not None:
            self._log_fn(text)

    def bind_viewer(self, viewer):
        """(Re)bind the shared image viewer's Pick BC/Pick Ring signals and
        ring overlay to this card. Clears this card's rings off the
        previous viewer and clears its in-progress pick-point state, so
        points picked while another panel was active can never leak here
        (mirrors ``DetectorGeometryCard.set_viewer``)."""
        old = self._viewer
        if old is not None:
            try:
                old.bcPicked.disconnect(self._on_bc_picked)
            except TypeError:
                pass
            try:
                old.ringFitBC.disconnect(self._on_ring_fit_bc)
            except TypeError:
                pass
            for it in self._ring_items + self._corrected_ring_items:
                old._iv.removeItem(it)
            old._clear_ring_points()
        self._viewer = viewer
        if viewer is not None:
            viewer.bcPicked.connect(self._on_bc_picked)
            viewer.ringFitBC.connect(self._on_ring_fit_bc)
            self._redraw_rings()

    # ── UI ────────────────────────────────────────────────────────

    def _build_ui(self):
        lv = QtWidgets.QVBoxLayout(self)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(8)

        # ── Transforms ──
        trans = S.make_card("Transforms")
        self._flip_y = QtWidgets.QCheckBox("Flip Y")
        self._flip_z = QtWidgets.QCheckBox("Flip Z")
        self._transp = QtWidgets.QCheckBox("Transpose")
        for cb in (self._flip_y, self._flip_z, self._transp):
            cb.toggled.connect(self.imTransChanged.emit)
        tb = QtWidgets.QHBoxLayout(); tb.setSpacing(8)
        tb.addWidget(self._flip_y); tb.addWidget(self._flip_z); tb.addWidget(self._transp)
        tb.addStretch(1)
        trans.body.addLayout(tb)
        lv.addWidget(trans)

        # ── Initial seed ──
        seed = S.make_card("Initial seed  (Pick tools on image)")
        self._load_seed_btn = QtWidgets.QPushButton("Load calibration file…")
        self._load_seed_btn.setToolTip(
            "Load geometry from a MIDAS paramstest (.txt), a calibration .json, "
            "or a pyFAI .poni — sets this panel's seed BC/Lsd/tilts + transforms.")
        self._load_seed_btn.clicked.connect(self._load_calib_file)
        seed.body.addWidget(self._load_seed_btn)
        self._manual_seed_check = QtWidgets.QCheckBox("Use manual seed")
        self._manual_seed_check.setToolTip(
            "Enable BC + Lsd as the LM starting point.\n"
            "Use Pick BC / Pick Ring on the image to populate BC automatically.")
        seed.body.addWidget(self._manual_seed_check)
        self._seed_bcy = _fspin(-99999, 99999, 2, DEFAULT_BC_Y, "px")
        self._seed_bcz = _fspin(-99999, 99999, 2, DEFAULT_BC_Z, "px")
        self._seed_lsd = _fspin(0.001, 1e5, 4, DEFAULT_LSD_UM / 1000.0, " mm")
        self._seed_tx = _fspin(-180, 180, 4, 0.0, "°")
        self._seed_ty = _fspin(-180, 180, 4, 0.0, "°")
        self._seed_tz = _fspin(-180, 180, 4, 0.0, "°")
        self._seed_tilts = (self._seed_tx, self._seed_ty, self._seed_tz)
        for w in (self._seed_bcy, self._seed_bcz, self._seed_lsd, *self._seed_tilts):
            w.setEnabled(False)
        for w in (self._seed_bcy, self._seed_bcz, self._seed_lsd, *self._seed_tilts):
            self._manual_seed_check.toggled.connect(w.setEnabled)
        sfm = S.Form()
        sfm.row(("BC_y:", self._seed_bcy), ("BC_z:", self._seed_bcz)); sfm.row(("Lsd:", self._seed_lsd))
        sfm.row(("tx:", self._seed_tx), ("ty:", self._seed_ty)); sfm.row(("tz:", self._seed_tz))
        seed.body.addLayout(sfm)
        self._feedback_check = QtWidgets.QCheckBox("Feed result back to seed")
        self._feedback_check.setChecked(True)
        self._feedback_check.setToolTip(
            "After a calibration, copy the optimized BC / Lsd / tilts / distortion "
            "back into these seed fields so the next run starts from them.")
        seed.body.addWidget(self._feedback_check)
        self._seed_note = QtWidgets.QLabel("")
        self._seed_note.setStyleSheet(f"color:{S.ACCENT};font-size:10px"); self._seed_note.setWordWrap(True)
        seed.body.addWidget(self._seed_note)
        lv.addWidget(seed)
        lv.addStretch(1)

        # ── Results (built but placed by the owning page into its own
        #    per-panel Results QStackedWidget, not into this card's layout) ──
        self._build_results_widget()

    def _build_results_widget(self):
        w = QtWidgets.QWidget(); rl = QtWidgets.QVBoxLayout(w)
        rl.setContentsMargins(10, 8, 10, 8); rl.setSpacing(8)
        hdr = QtWidgets.QHBoxLayout()
        hdr.addWidget(QtWidgets.QLabel(f"<b>ge{self.panel_number} — calibration parameters</b> "
                                       "(as written to <code>paramstest.txt</code>)"))
        hdr.addStretch(1)
        self._to_view_btn = QtWidgets.QPushButton("→ Send to Data Viewer")
        self._to_view_btn.setEnabled(False)
        self._to_view_btn.setToolTip(
            f"Replace the Data Viewer's ge{self.panel_number} panel geometry with "
            "these calibrated values.")
        self._to_view_btn.clicked.connect(self._send_to_viewer)
        hdr.addWidget(self._to_view_btn)
        rl.addLayout(hdr)
        self._param_grid = QtWidgets.QGridLayout()
        self._param_grid.setHorizontalSpacing(28); self._param_grid.setVerticalSpacing(9)
        pg_host = QtWidgets.QWidget(); pg_host.setLayout(self._param_grid)
        rl.addWidget(pg_host)
        self._r_diag = QtWidgets.QLabel("Run a calibration to see the parameters.")
        self._r_diag.setStyleSheet(f"color:{S.MUTED};font-size:12px")
        self._r_diag.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        rl.addWidget(self._r_diag)
        self._save_json_btn = QtWidgets.QPushButton("Save .json"); self._save_json_btn.setEnabled(False)
        self._save_json_btn.clicked.connect(self._save_json)
        self._save_ps_btn = QtWidgets.QPushButton("Save paramstest.txt"); self._save_ps_btn.setEnabled(False)
        self._save_ps_btn.clicked.connect(self._save_paramstest)
        rl.addLayout(S.button_grid([self._save_json_btn, self._save_ps_btn], 2))
        rl.addStretch(1)
        self.results_widget = w
        self.residual_chart = ResidualBarChart()

    # ── Transforms / seed accessors ─────────────────────────────────

    def im_trans_codes(self) -> list:
        return im_trans_codes_from_checkboxes(self._flip_y, self._flip_z, self._transp)

    def manual_seed(self) -> Optional[dict]:
        if not self._manual_seed_check.isChecked():
            return None
        return {
            "BC_y": self._seed_bcy.value(), "BC_z": self._seed_bcz.value(),
            "Lsd": self._seed_lsd.value() * 1000.0,   # mm display -> µm
            "tx": self._seed_tx.value(), "ty": self._seed_ty.value(), "tz": self._seed_tz.value(),
        }

    def seed_from_geometry(self, g: dict):
        """Seed this panel's fields from a geometry dict (calibration file,
        Data Viewer import, or a previous result) — BC/Lsd/tilts/transforms
        only; shared fields (λ/pixel/calibrant) are the owning page's job."""
        if not g:
            return
        self._manual_seed_check.setChecked(True)
        if g.get("BC_y") is not None:
            self._seed_bcy.setValue(float(g["BC_y"]))
        if g.get("BC_z") is not None:
            self._seed_bcz.setValue(float(g["BC_z"]))
        if g.get("Lsd"):
            self._seed_lsd.setValue(float(g["Lsd"]) / 1000.0)
        if g.get("tx") is not None:
            self._seed_tx.setValue(float(g["tx"]))
        if g.get("ty") is not None:
            self._seed_ty.setValue(float(g["ty"]))
        if g.get("tz") is not None:
            self._seed_tz.setValue(float(g["tz"]))
        if g.get("im_trans") is not None:
            im_trans = g["im_trans"] or []
            self._flip_y.setChecked(1 in im_trans)
            self._flip_z.setChecked(2 in im_trans)
            self._transp.setChecked(3 in im_trans)

    def seed_from_result(self, result):
        self._manual_seed_check.setChecked(True)
        self._seed_bcy.setValue(float(result.BC_y))
        self._seed_bcz.setValue(float(result.BC_z))
        self._seed_lsd.setValue(float(result.Lsd) / 1000.0)
        self._seed_tx.setValue(float(getattr(result, "tx", 0.0) or 0.0))
        self._seed_ty.setValue(float(getattr(result, "ty", 0.0) or 0.0))
        self._seed_tz.setValue(float(getattr(result, "tz", 0.0) or 0.0))
        self._seed_note.setText("Seed updated from the last calibration result.")

    def _load_calib_file(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, f"Load ge{self.panel_number} calibration file", "",
            "Calibration (*.json *.txt *.poni);;All files (*)")
        if not path:
            return
        try:
            g = geometry_fields_from_file(path)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Load failed", str(e)); return
        self.seed_from_geometry(g)
        self._seed_note.setText(
            f"Loaded {Path(path).name}: BC=({g['BC_y']:.2f}, {g['BC_z']:.2f}), "
            f"Lsd={g['Lsd']/1000:.3f} mm.")
        self.calibFileLoaded.emit(g)

    # ── Pick BC / Pick Ring ──────────────────────────────────────────

    def _on_bc_picked(self, bc_y, bc_z):
        self._manual_seed_check.setChecked(True)
        self._seed_bcy.setValue(bc_y); self._seed_bcz.setValue(bc_z)
        self._seed_note.setText(f"ge{self.panel_number}: BC set from click — also set Lsd before running.")
        self._log(f"ge{self.panel_number}: BC set by click: ({bc_y:.2f}, {bc_z:.2f}) px")

    def _on_ring_fit_bc(self, bc_y, bc_z, r_px):
        self._manual_seed_check.setChecked(True)
        self._seed_bcy.setValue(bc_y); self._seed_bcz.setValue(bc_z)
        self._seed_note.setText(f"ge{self.panel_number}: BC from ring fit (R={r_px:.1f} px).")
        self._log(f"ge{self.panel_number}: ring fit BC=({bc_y:.2f}, {bc_z:.2f}) px  R={r_px:.1f} px")

    # ── Rings ────────────────────────────────────────────────────────

    def set_show_rings(self, visible: bool):
        self._show_rings = visible
        active = self._corrected_ring_items if (self._corrected and self._corrected_ring_items) \
            else self._ring_items
        for it in active:
            it.setVisible(visible)

    def show_rings_checked(self) -> bool:
        return self._show_rings

    def set_corrected(self, checked: bool):
        self._corrected = checked
        if self.result is None:
            return
        if checked:
            for item in self._ring_items:
                item.setVisible(False)
            if not self._corrected_ring_items:
                self._draw_corrected_rings(_predict_ring_radii(self.result))
            for item in self._corrected_ring_items:
                item.setVisible(self._show_rings)
        else:
            for item in self._corrected_ring_items:
                item.setVisible(False)
            for item in self._ring_items:
                item.setVisible(self._show_rings)

    def corrected_checked(self) -> bool:
        return self._corrected

    def _clear_rings(self):
        if self._viewer is not None:
            for it in self._ring_items:
                self._viewer._iv.removeItem(it)
        self._ring_items = []

    def _clear_corrected_rings(self):
        if self._viewer is not None:
            for it in self._corrected_ring_items:
                self._viewer._iv.removeItem(it)
        self._corrected_ring_items = []

    def _redraw_rings(self):
        """(Re)draw this panel's fitted rings onto whichever viewer is
        currently bound — called after a fit completes, and again on
        ``bind_viewer`` so switching back to this panel restores them."""
        self._clear_rings()
        self._clear_corrected_rings()
        if self.result is None or self._viewer is None:
            return
        result = self.result
        radii = _predict_ring_radii(result)
        th = np.linspace(0, 2 * math.pi, 512)
        pen = pg.mkPen("lime", width=1.2)
        max_r = max(result.NrPixelsY, result.NrPixelsZ)
        for r in radii:
            if 0 < r < max_r:
                item = pg.PlotDataItem(result.BC_y + r * np.cos(th),
                                       result.BC_z + r * np.sin(th), pen=pen)
                item.setVisible(self._show_rings and not self._corrected)
                self._viewer._iv.addItem(item); self._ring_items.append(item)
        bc = pg.ScatterPlotItem([result.BC_y], [result.BC_z], symbol="o", size=10,
                                pen=pg.mkPen("yellow", width=2), brush=pg.mkBrush("red"))
        bc.setVisible(self._show_rings and not self._corrected)
        self._viewer._iv.addItem(bc); self._ring_items.append(bc)
        if self._corrected:
            self._draw_corrected_rings(radii)

    def _draw_corrected_rings(self, radii_px):
        if self.result is None or self._viewer is None:
            return
        result = self.result
        self._clear_corrected_rings()
        pen = pg.mkPen("lime", width=1.2)
        for r in radii_px:
            try:
                two_theta_deg = math.degrees(math.atan(r * result.pxY / result.Lsd))
                ys, zs = tilted_ring_xy(two_theta_deg, result.tx, result.ty, result.tz,
                                        result.Lsd, result.BC_y, result.BC_z,
                                        result.pxY, result.pxZ)
            except Exception:
                continue
            item = pg.PlotDataItem(ys, zs, pen=pen)
            item.setVisible(self._show_rings)
            self._viewer._iv.addItem(item); self._corrected_ring_items.append(item)

    def on_result(self, result):
        """A fresh fitted result for this panel — store it, refresh seed
        (if 'feed back' is on), redraw rings (if bound to the viewer), and
        populate the Results grid."""
        self.result = result
        if self._feedback_check.isChecked():
            try:
                self.seed_from_result(result)
            except Exception:
                pass
        self._redraw_rings()
        self._populate_results(result)

    # ── Results grid / save ──────────────────────────────────────────

    def _populate_results(self, result, selected=None):
        try:
            pairs = paramstest_pairs(result, selected=selected)
            self._populate_param_grid(pairs)
        except Exception:
            import traceback as _tb
            self._r_diag.setText("Could not render parameter grid — see log.")
            self._log(f"ge{self.panel_number} param grid error:\n{_tb.format_exc()[:400]}")
        s = getattr(result, "post_residual_strain_uE", None)
        strain_txt = f"{s:.1f} µε" if s else "n/a"
        self._r_diag.setText(f"Post-refine strain: {strain_txt}")
        self._to_view_btn.setEnabled(True)
        self._save_json_btn.setEnabled(True)
        self._save_ps_btn.setEnabled(True)

    def _populate_param_grid(self, pairs, ncols=3):
        grid = self._param_grid
        while grid.count():
            item = grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        mono = _mono_font(12)
        klbl = "font-weight:600; font-size:12px;"
        n = len(pairs); nrows = max(1, math.ceil(n / ncols))
        for idx, (key, val) in enumerate(pairs):
            col, row = idx // nrows, idx % nrows
            label = _PARAMSTEST_DISTORTION.get(key, key)
            k = QtWidgets.QLabel(f"{label}:"); k.setStyleSheet(klbl)
            v = QtWidgets.QLabel(val); v.setFont(mono)
            v.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            grid.addWidget(k, row, col * 2, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            grid.addWidget(v, row, col * 2 + 1, QtCore.Qt.AlignVCenter)
        grid.setColumnStretch(ncols * 2 + 1, 1)

    def _send_to_viewer(self):
        r = self.result
        if r is None:
            return
        self.sendToViewer.emit(self.panel_number, {
            "wavelength_A": float(r.wavelength_A), "pxY": float(r.pxY),
            "pxZ": float(getattr(r, "pxZ", r.pxY) or r.pxY),
            "Lsd": float(r.Lsd), "BC_y": float(r.BC_y), "BC_z": float(r.BC_z),
            "tx": float(getattr(r, "tx", 0.0) or 0.0),
            "ty": float(getattr(r, "ty", 0.0) or 0.0),
            "tz": float(getattr(r, "tz", 0.0) or 0.0),
            "NrPixelsY": int(getattr(r, "NrPixelsY", 0) or 0),
            "NrPixelsZ": int(getattr(r, "NrPixelsZ", 0) or 0),
            "distortion": dict(getattr(r, "distortion", {}) or {}),
            "im_trans": list(getattr(r, "im_trans", []) or [])})

    def _save_json(self):
        if not self.result:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, f"Save ge{self.panel_number} calibration.json",
            f"ge{self.panel_number}_calibration.json", "JSON (*.json)")
        if not path:
            return
        import json
        d = {k: v for k, v in vars(self.result).items()
             if not k.startswith("_") and not hasattr(v, "numpy")}
        d.pop("residual_corr_map", None); d.pop("iter_history", None)
        Path(path).write_text(json.dumps(d, indent=2, default=str))
        self._log(f"ge{self.panel_number}: saved {path}")

    def _save_paramstest(self):
        if not self.result:
            return
        dlg = _SaveParamstestDialog(self)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        out_path = dlg.out_path()
        if not out_path:
            QtWidgets.QMessageBox.warning(self, "No output", "Please specify an output file."); return
        tmpl_path = dlg.template_path()
        try:
            if tmpl_path:
                if not Path(tmpl_path).exists():
                    raise FileNotFoundError(f"Template not found: {tmpl_path}")
                from midas_calibrate_v2.compat.to_v1 import ff_paramstest_from_auto_result
                ff_paramstest_from_auto_result(self.result, tmpl_path, out_path)
                im_trans = getattr(self.result, "im_trans", None)
                if im_trans:
                    with open(out_path, "a") as _f:
                        for code in im_trans:
                            _f.write(f"ImTransOpt {int(code)}\n")
            else:
                write_standalone_paramstest(self.result, out_path)
            self._log(f"ge{self.panel_number}: paramstest.txt saved: {out_path}")
        except Exception:
            import traceback
            self._log(f"ge{self.panel_number}: save paramstest error:\n{traceback.format_exc()[:400]}")
            QtWidgets.QMessageBox.critical(self, "Save failed", traceback.format_exc()[:400])

    # ── GUI state ────────────────────────────────────────────────────

    def state_widgets(self) -> dict:
        return {
            "flip_y": self._flip_y, "flip_z": self._flip_z, "transp": self._transp,
            "manual_seed_check": self._manual_seed_check,
            "seed_bcy": self._seed_bcy, "seed_bcz": self._seed_bcz, "seed_lsd": self._seed_lsd,
            "seed_tx": self._seed_tx, "seed_ty": self._seed_ty, "seed_tz": self._seed_tz,
            "feedback_check": self._feedback_check,
        }
