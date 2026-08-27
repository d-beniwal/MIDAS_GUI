"""Tab 3 — Calibration Refinement.

Refines geometry jointly against the integrated profile (η-uniformity) using the
differentiable integration path from midas_integrate_v2 — closing the gap between
Bragg-spot calibration and profile-level accuracy.  Consumes a calibration result
from Tab 2 and emits a refined result that propagates to the downstream tabs.

Layout: [ Data Loader | Parameters | Loss/comparison/log ].
"""
from __future__ import annotations

from typing import Optional

import numpy as np
from PyQt5 import QtCore, QtWidgets
import pyqtgraph as pg

from midas_gui.helpers import (_fspin, _twocol, _NoScrollSpinBox, _NoScrollComboBox,
                               widgets_to_dict, apply_dict_to_widgets)
from midas_gui.widgets import LossCurveViewer, LogPanel, DataLoaderPanel
from midas_gui.workers import RefinementWorker, RefineCompareWorker
from midas_gui.dialogs import show_error
from midas_gui import style as S


class RefinementTab(QtWidgets.QWidget):
    refinedResult = QtCore.pyqtSignal(object)   # updated AutoCalibrationResult

    def __init__(self, parent=None):
        super().__init__(parent)
        self._result = None
        self._orig_result = None         # Tab 2 calibration — kept for before/after comparison
        self._worker = None
        self._compare_worker = None
        self._build_ui()

    def set_calibration(self, result):
        self._result = result
        self._orig_result = result       # preserve the Tab 2 result for before/after comparison
        self._src_lbl.setText(
            f"From Tab 2: Lsd={result.Lsd/1000:.3f} mm  BC=({result.BC_y:.1f},{result.BC_z:.1f})  "
            f"ty={result.ty:.3f} tz={result.tz:.3f}")
        self._update_run_enabled()

    def set_mask_from_tab1(self, mask):
        self._loader.set_tab1_mask(mask)

    # ── GUI state (Save/Load GUI State) ─────────────────────────────
    def _state_widgets(self) -> dict:
        return {
            "start_tab2": self._start_tab2,
            "start_refine": self._start_refine,
            "loss_combo": self._loss_combo,
            "p_bcy": self._p_bcy, "p_bcz": self._p_bcz, "p_lsd": self._p_lsd,
            "p_ty": self._p_ty, "p_tz": self._p_tz, "p_wl": self._p_wl,
            "opt_combo": self._opt_combo,
            "lr": self._lr,
            "iters": self._iters,
            "rbin": self._rbin,
        }

    def get_state(self) -> dict:
        return {"fields": widgets_to_dict(self._state_widgets()),
                "loader": self._loader.get_state()}

    def set_state(self, state: dict):
        self._loader.set_state(state.get("loader") or {})
        apply_dict_to_widgets(self._state_widgets(), state.get("fields", {}))
        self._update_run_enabled()

    # ── UI ──────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6); root.setSpacing(0)
        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        split.setChildrenCollapsible(False); split.setHandleWidth(6)
        root.addWidget(split); self._hsplit = split

        # ── LEFT: data loader ──
        self._loader = DataLoaderPanel(mode="single")
        self._loader.setMinimumWidth(200)
        self._loader.dataChanged.connect(self._update_run_enabled)
        split.addWidget(self._loader)

        # ── MIDDLE: parameters ──
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True); scroll.setMinimumWidth(260)
        inner = QtWidgets.QWidget(); lv = QtWidgets.QVBoxLayout(inner); lv.setSpacing(6)
        scroll.setWidget(inner)

        # Calibration source
        grp_src = QtWidgets.QGroupBox("Calibration source")
        sv = QtWidgets.QVBoxLayout(grp_src); sv.setSpacing(4)
        self._src_lbl = QtWidgets.QLabel("(run Tab 2 calibration first)")
        self._src_lbl.setStyleSheet("color:#aaa;font-size:10px"); self._src_lbl.setWordWrap(True)
        sv.addWidget(self._src_lbl)
        sv.addWidget(S.hline())
        self._start_tab2   = QtWidgets.QRadioButton("Start from Tab 2 calibration (each run independent)")
        self._start_refine = QtWidgets.QRadioButton("Continue from previous refinement (iterative)")
        self._start_tab2.setChecked(True)
        self._start_tab2.setToolTip(
            "Each run refines from the original Tab 2 result — results are reproducible.")
        self._start_refine.setToolTip(
            "Each run starts where the last one stopped — allows iterative improvement.")
        sv.addWidget(self._start_tab2)
        sv.addWidget(self._start_refine)
        reset_btn = QtWidgets.QPushButton("Reset to Tab 2 result")
        reset_btn.setToolTip("Discard refined geometry and revert to the Tab 2 calibration.")
        reset_btn.clicked.connect(self._reset_to_tab2)
        sv.addWidget(reset_btn)
        lv.addWidget(grp_src)

        # Reference data / loss
        grp_loss = QtWidgets.QGroupBox("Reference / loss")
        lf = QtWidgets.QVBoxLayout(grp_loss)
        self._loss_combo = _NoScrollComboBox()
        self._loss_combo.addItem("η-uniformity (rings flat in η)", "eta_uniformity")
        self._loss_combo.setToolTip("Minimise azimuthal intensity variation along each ring.")
        lf.addWidget(self._loss_combo)
        lv.addWidget(grp_loss)

        # Parameters to refine
        grp_ref = QtWidgets.QGroupBox("Parameters to refine")
        rfl = QtWidgets.QGridLayout(grp_ref); rfl.setSpacing(4)
        self._p_bcy = QtWidgets.QCheckBox("BC_y"); self._p_bcy.setChecked(True)
        self._p_bcz = QtWidgets.QCheckBox("BC_z"); self._p_bcz.setChecked(True)
        self._p_lsd = QtWidgets.QCheckBox("Lsd")
        self._p_ty  = QtWidgets.QCheckBox("ty"); self._p_ty.setChecked(True)
        self._p_tz  = QtWidgets.QCheckBox("tz"); self._p_tz.setChecked(True)
        self._p_wl  = QtWidgets.QCheckBox("Wavelength")
        for i, w in enumerate((self._p_bcy, self._p_bcz, self._p_lsd,
                               self._p_ty, self._p_tz, self._p_wl)):
            rfl.addWidget(w, i // 2, i % 2)
        lv.addWidget(grp_ref)

        # Optimizer
        grp_opt = QtWidgets.QGroupBox("Optimizer")
        of = QtWidgets.QFormLayout(grp_opt); of.setSpacing(4)
        self._opt_combo = _NoScrollComboBox(); self._opt_combo.addItems(["adam", "lbfgs"])
        self._lr = _fspin(0.0, 1e6, 3, 0.5)
        of.addRow(_twocol("method:", self._opt_combo, "lr:", self._lr))
        self._iters = _NoScrollSpinBox(); self._iters.setRange(1, 1_000_000); self._iters.setValue(80)
        self._rbin = _fspin(0.5, 20.0, 1, 2.0, "px")
        of.addRow(_twocol("iters:", self._iters, "R bin:", self._rbin))
        lv.addWidget(grp_opt)

        self._run_btn = S.primary_btn("Run Refinement"); self._run_btn.setEnabled(False)
        self._run_btn.clicked.connect(self._run)
        lv.addWidget(self._run_btn)
        self._prog = QtWidgets.QProgressBar(); self._prog.setVisible(False)
        lv.addWidget(self._prog)

        # Refined geometry readout
        grp_out = QtWidgets.QGroupBox("Refined geometry")
        ov = QtWidgets.QFormLayout(grp_out); ov.setSpacing(3)
        self._geo_lbl = QtWidgets.QLabel("—")
        self._geo_lbl.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self._geo_lbl.setWordWrap(True)
        ov.addRow(self._geo_lbl)
        self._apply_btn = QtWidgets.QPushButton("Apply → downstream tabs")
        self._apply_btn.setEnabled(False); self._apply_btn.clicked.connect(self._apply)
        ov.addRow(self._apply_btn)
        lv.addWidget(grp_out)

        lv.addStretch(1)
        split.addWidget(scroll)

        # ── RIGHT: loss curve + comparison plots + log ──
        right = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self._loss_view = LossCurveViewer(ylabel="η-uniformity loss")
        right.addWidget(self._loss_view)

        cmp_panel = QtWidgets.QWidget()
        cmp_v = QtWidgets.QVBoxLayout(cmp_panel)
        cmp_v.setContentsMargins(0, 0, 0, 0); cmp_v.setSpacing(2)
        hdr = QtWidgets.QLabel("Radial profile: before (gray) / after (orange) refinement")
        hdr.setStyleSheet(f"color:{S.MUTED};font-size:10px;padding:2px 4px")
        cmp_v.addWidget(hdr)
        self._cmp_plot = pg.PlotWidget(background="#111111")
        self._cmp_plot.showGrid(x=True, y=True, alpha=0.25)
        self._cmp_plot.setLabel("left", "Intensity")
        self._cmp_plot.setLabel("bottom", "R (px)")
        self._cmp_plot.addLegend(offset=(10, 10))
        self._cmp_before = self._cmp_plot.plot(pen=pg.mkPen("#aaaaaa", width=1.5), name="Before")
        self._cmp_after  = self._cmp_plot.plot(pen=pg.mkPen(S.ACCENT, width=1.8), name="After")
        cmp_v.addWidget(self._cmp_plot, stretch=2)
        self._diff_plot = pg.PlotWidget(background="#111111")
        self._diff_plot.showGrid(x=True, y=True, alpha=0.25)
        self._diff_plot.setLabel("left", "Δ Intensity")
        self._diff_plot.setLabel("bottom", "R (px)")
        self._diff_plot.setXLink(self._cmp_plot)
        self._diff_curve = self._diff_plot.plot(pen=pg.mkPen("#ff6060", width=1.2))
        self._diff_plot.addItem(pg.InfiniteLine(pos=0, angle=0,
                                                pen=pg.mkPen("#555555", width=1)))
        cmp_v.addWidget(self._diff_plot, stretch=1)
        right.addWidget(cmp_panel)

        self._log = LogPanel()
        right.addWidget(self._log)
        right.setStretchFactor(0, 2); right.setStretchFactor(1, 4); right.setStretchFactor(2, 1)
        right.setMinimumWidth(320)
        split.addWidget(right)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 0); split.setStretchFactor(2, 1)
        split.setSizes([286, 361, 950])

    # ── actions ────────────────────────────────────────────────────

    def _update_run_enabled(self, *_):
        self._run_btn.setEnabled(
            self._result is not None and self._loader.current_frame() is not None)

    def _refine_names(self):
        names = []
        if self._p_bcy.isChecked(): names.append("BC_y")
        if self._p_bcz.isChecked(): names.append("BC_z")
        if self._p_lsd.isChecked(): names.append("Lsd")
        if self._p_ty.isChecked():  names.append("ty")
        if self._p_tz.isChecked():  names.append("tz")
        if self._p_wl.isChecked():  names.append("Wavelength")
        return names

    def _run(self):
        image = self._loader.current_frame()
        if self._result is None or image is None:
            QtWidgets.QMessageBox.warning(self, "Missing input",
                                          "Need a Tab 2 calibration and a loaded image."); return
        if self._worker and self._worker.isRunning():
            return
        for sel in self._loader.has_pending_fields():
            QtWidgets.QMessageBox.warning(
                self, "Field not computed",
                f"'{sel.title()}' is enabled but not computed — click 'Compute field'."); return
        names = self._refine_names()
        if not names:
            QtWidgets.QMessageBox.warning(self, "No parameters", "Select at least one parameter."); return
        self._loss_view.reset()
        self._run_btn.setEnabled(False)
        self._prog.setRange(0, self._iters.value()); self._prog.setValue(0); self._prog.setVisible(True)
        start_result = self._result if self._start_refine.isChecked() else self._orig_result
        mode_lbl = "continued" if self._start_refine.isChecked() else "from Tab 2"
        mask = self._loader.composite_mask()
        mask_note = (f"{int(mask.astype(bool).sum()):,} px masked" if mask is not None else "no mask")
        self._log.append("─" * 40 + f"\nRefining {names} ({mode_lbl}, {mask_note})…")
        self._worker = RefinementWorker(
            start_result, image, self._loader.dark(), mask, names,
            loss_kind=self._loss_combo.currentData(),
            optimizer=self._opt_combo.currentText(), lr=self._lr.value(),
            iters=self._iters.value(), r_bin=self._rbin.value(), parent=self,
            bright=self._loader.bright(), background=self._loader.background(),
            bright_mode=self._loader.bright_mode())
        self._worker.progress.connect(self._on_progress)
        self._worker.log_line.connect(self._log.append)
        self._worker.finished.connect(self._on_done)
        self._worker.failed.connect(self._on_fail)
        self._worker.start()

    def _on_progress(self, step, total, loss, params):
        self._prog.setValue(step)
        self._loss_view.add_point(step, loss)
        pstr = "  ".join(f"{k}={v:.4g}" for k, v in params.items())
        self._log.append(f"  step {step}/{total}  loss={loss:.5g}  {pstr}")

    def _on_done(self, result):
        self._result = result
        self._run_btn.setEnabled(True); self._prog.setVisible(False)
        self._apply_btn.setEnabled(True)
        self._geo_lbl.setText(
            f"Lsd = {result.Lsd/1000:.4f} mm\nBC = ({result.BC_y:.2f}, {result.BC_z:.2f}) px\n"
            f"ty = {result.ty:.4f}°   tz = {result.tz:.4f}°\nλ = {result.wavelength_A:.5f} Å")
        self._log.append("Refinement complete.")
        image = self._loader.current_frame()
        if image is not None and self._orig_result is not None:
            self._log.append("[compare] Integrating with original + refined geometry…")
            self._compare_worker = RefineCompareWorker(
                self._orig_result, result, image, mask=self._loader.composite_mask(),
                r_bin=self._rbin.value(), parent=self,
                dark=self._loader.dark(), bright=self._loader.bright(),
                background=self._loader.background(), bright_mode=self._loader.bright_mode())
            self._compare_worker.finished.connect(self._on_compare_done)
            self._compare_worker.failed.connect(
                lambda msg: self._log.append(f"[compare] failed: {msg}"))
            self._compare_worker.start()

    def _on_fail(self, msg):
        self._run_btn.setEnabled(True); self._prog.setVisible(False)
        show_error(self, "Refinement failed", msg, log=self._log, log_prefix="\nERROR:\n")

    def _apply(self):
        if self._result is not None:
            self.refinedResult.emit(self._result)
            self._log.append("Refined geometry broadcast to downstream tabs.")

    def _reset_to_tab2(self):
        if self._orig_result is None:
            QtWidgets.QMessageBox.information(self, "No calibration", "Run Tab 2 calibration first.")
            return
        self._result = self._orig_result
        self._geo_lbl.setText(
            f"[reset] Lsd={self._orig_result.Lsd/1000:.4f} mm  "
            f"BC=({self._orig_result.BC_y:.2f},{self._orig_result.BC_z:.2f}) px")
        self._apply_btn.setEnabled(False)
        self._cmp_before.setData([], [])
        self._cmp_after.setData([], [])
        self._diff_curve.setData([], [])
        self._log.append("[reset] Refined geometry discarded. Starting geometry is now Tab 2 result.")

    def _on_compare_done(self, data):
        r   = data["r_axis_px"]
        bef = data["profile_orig"]
        aft = data["profile_refined"]
        diff = aft - bef
        self._cmp_before.setData(r, bef)
        self._cmp_after.setData(r, aft)
        self._diff_curve.setData(r, diff)
        if r.size:
            self._cmp_plot.setXRange(float(r.min()), float(r.max()), padding=0.02)
        self._log.append(
            f"[compare] Δ profile: mean={diff.mean():.4g}  max|Δ|={np.abs(diff).max():.4g}")
