"""Tab 3 — Batch Integrate.

Ports the v3 batch tab and adds Phase-1 features:
  - kernel selector (hard / subpixel K=2/4 / polygon)
  - physics corrections (polarization + solid angle)
  - per-bin variance / σ output (error model selectable)
  - native Q-uniform binning
  - output formats CSV / XYE / FXYE / DAT / HDF5
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
from PyQt5 import QtCore, QtWidgets

from midas_gui.constants import (KERNELS, OUTPUT_FORMATS, ERROR_MODELS,
                           DEFAULT_NICKEL_DIR)
from midas_gui.helpers import (_fspin, _browse, _build_spec, spec_from_geometry_file,
                               _NoScrollSpinBox, _NoScrollComboBox)
from midas_gui.widgets import (LogPanel, CorrectionFlagsWidget, WaterfallViewer,
                               StackedProfileViewer, DataLoaderPanel)
from midas_gui.workers import BatchWorker, apply_q_uniform, DriftWorker, FolderMonitorWorker
from midas_gui import style as S


class BatchTab(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._orphans: list = []       # aborted workers kept alive until they wind down
        self._drift_worker = None
        self._drift_traj = None
        self._calib_result = None
        self._wf_started = False
        self._monitor_worker = None
        self._integrated_fids: set = set()   # frame ids already displayed (batch + monitor)
        self._geom_cache = None              # cached integration context (detector map)
        self._geom_sig = None                # signature the cached context was built for
        self._build_ui()
        self._loader.monitorToggled.connect(self._toggle_monitor)
        self._loader.set_path(DEFAULT_NICKEL_DIR)

    def set_calibration(self, result):
        self._calib_result = result
        self._calib_src_lbl.setText(
            f"From Tab 2: Lsd={result.Lsd/1000:.3f} mm  "
            f"λ={result.wavelength_A:.5f} Å  {result.NrPixelsY}×{result.NrPixelsZ} px")
        self._use_tab2_btn.setChecked(True)

    def set_mask_from_tab1(self, mask):
        self._loader.set_tab1_mask(mask)

    def _build_ui(self):
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6); root.setSpacing(0)
        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        split.setChildrenCollapsible(False); split.setHandleWidth(6)
        root.addWidget(split); self._hsplit = split

        # ── LEFT: data loader (streaming source + dark/bright/bg + mask) ──
        self._loader = DataLoaderPanel(mode="stream")
        self._loader.setMinimumWidth(200)
        split.addWidget(self._loader)

        # ── MIDDLE: parameters ──
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True); scroll.setMinimumWidth(260)
        inner = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(inner); lv.setContentsMargins(2, 2, 2, 2); lv.setSpacing(8)
        scroll.setWidget(inner)

        def _br(w=30):
            b = QtWidgets.QPushButton("…"); b.setFixedWidth(w); return b

        # ── Calibration source ──
        cal = S.make_card("Calibration source")
        src_row = QtWidgets.QHBoxLayout(); src_row.setSpacing(10)
        self._use_tab2_btn = QtWidgets.QRadioButton("From Tab 2")
        self._use_json_btn = QtWidgets.QRadioButton("From file")
        self._use_tab2_btn.setChecked(True)
        src_row.addWidget(self._use_tab2_btn); src_row.addWidget(self._use_json_btn); src_row.addStretch(1)
        cal.body.addLayout(src_row)
        self._calib_src_lbl = QtWidgets.QLabel("(run Tab 2 first)")
        self._calib_src_lbl.setStyleSheet(f"color:{S.MUTED};font-size:10px")
        cal.body.addWidget(self._calib_src_lbl)
        self._json_ed = QtWidgets.QLineEdit()
        self._json_ed.setPlaceholderText("calibration.json / paramstest.txt / .poni…")
        jr = QtWidgets.QHBoxLayout(); jr.setSpacing(4); jr.addWidget(self._json_ed, 1)
        bj = _br(); bj.clicked.connect(lambda: self._json_ed.setText(
            _browse(self, "Open calibration file",
                    "Calibration (*.json *.txt *.poni);;All (*)") or "")); jr.addWidget(bj)
        self._json_ed.textChanged.connect(
            lambda t: self._use_json_btn.setChecked(True) if t.strip() else None)
        cal.body.addLayout(jr)
        lv.addWidget(cal)

        # ── Integration ──
        integ = S.make_card("Integration")
        self._kernel = _NoScrollComboBox()
        for label, key in KERNELS.items():
            self._kernel.addItem(label, key)
        self._r_bin = _fspin(0.1, 20.0, 2, 1.0, "px")
        self._e_bin = _fspin(0.5, 30.0, 1, 5.0, "°")
        self._azim = _NoScrollComboBox()
        self._azim.addItem("Pixel-weighted", True)
        self._azim.addItem("η-bin mean (legacy)", False)
        self._azim.setToolTip(
            "How the 2-D (η, R) cake is collapsed to a 1-D profile:\n"
            "• Pixel-weighted — Σ(mean·count)/Σ(count); robust to partial azimuthal\n"
            "  coverage / off-detector beam centres and independent of η-bin size.\n"
            "• η-bin mean — unweighted mean of the per-η-bin means (can distort the\n"
            "  profile with a coarse η bin when the beam centre is off the detector).")
        intf = S.Form()
        intf.row(("Kernel:", self._kernel))
        intf.row(("R bin:", self._r_bin), ("η bin:", self._e_bin))
        intf.row(("Azim. avg:", self._azim))
        integ.body.addLayout(intf)
        self._var_check = QtWidgets.QCheckBox("Per-bin variance (σ)")
        self._var_check.setToolTip(
            "Compute per-bin σ via the chosen error model.\n"
            "Mutually exclusive with corrections (corrections win; σ→√I).")
        self._err_model = _NoScrollComboBox(); self._err_model.addItems(ERROR_MODELS); self._err_model.setEnabled(False)
        self._var_check.toggled.connect(self._err_model.setEnabled)
        vrow = QtWidgets.QHBoxLayout(); vrow.setSpacing(6)
        vrow.addWidget(self._var_check); vrow.addWidget(self._err_model, 1)
        integ.body.addLayout(vrow)
        self._q_check = QtWidgets.QCheckBox("Q-uniform bins")
        self._q_check.setToolTip("Bin uniformly in Q (Å⁻¹) instead of R (px).")
        integ.body.addWidget(self._q_check)
        self._q_min = _fspin(0.0, 100.0, 3, 0.5, "Å⁻¹")
        self._q_max = _fspin(0.0, 100.0, 3, 8.0, "Å⁻¹")
        self._q_bin = _fspin(0.0001, 1.0, 4, 0.01, "Å⁻¹")
        for w in (self._q_min, self._q_max, self._q_bin):
            w.setEnabled(False)
        self._q_check.toggled.connect(lambda c: [w.setEnabled(c) for w in
                                                 (self._q_min, self._q_max, self._q_bin)])
        qf = S.Form(); qf.row(("Qmin:", self._q_min), ("Qmax:", self._q_max)); qf.row(("ΔQ:", self._q_bin))
        integ.body.addLayout(qf)
        lv.addWidget(integ)

        # ── Corrections ──
        self._corr_widget = CorrectionFlagsWidget()
        lv.addWidget(self._corr_widget)

        # ── Monitor normalisation ──
        mon = S.make_card("Monitor normalisation (optional)")
        self._mon_ed = QtWidgets.QLineEdit()
        self._mon_ed.setPlaceholderText("monitor.txt  (one value per line)")
        monr = QtWidgets.QHBoxLayout(); monr.setSpacing(4); monr.addWidget(self._mon_ed, 1)
        bmon = _br(); bmon.clicked.connect(lambda: self._mon_ed.setText(
            _browse(self, "Open monitor file", "Text (*.txt *.dat *.csv);;All (*)") or ""))
        monr.addWidget(bmon)
        mon.body.addLayout(monr)
        mon_note = QtWidgets.QLabel(
            "Each profile is divided by the corresponding monitor value.\n"
            "File: one floating-point number per line, one per processed frame.")
        mon_note.setWordWrap(True)
        mon_note.setStyleSheet(f"color:{S.MUTED};font-size:10px")
        mon.body.addWidget(mon_note)
        lv.addWidget(mon)

        # ── Drift correction ──
        drift = S.make_card("Drift correction (long scans)")
        self._drift_chk = QtWidgets.QCheckBox("Enable per-frame geometry drift correction")
        drift.body.addWidget(self._drift_chk)
        self._drift_anchor_ed = QtWidgets.QLineEdit()
        self._drift_anchor_ed.setPlaceholderText("anchors.json  ({frame_idx: {Lsd, BC_y, BC_z}})")
        drow = QtWidgets.QHBoxLayout(); drow.setSpacing(4); drow.addWidget(self._drift_anchor_ed, 1)
        bdrift = QtWidgets.QPushButton("…"); bdrift.setFixedWidth(30)
        bdrift.clicked.connect(lambda: self._drift_anchor_ed.setText(
            _browse(self, "Open anchor JSON", "JSON (*.json);;All (*)") or ""))
        drow.addWidget(bdrift); drift.body.addLayout(drow)
        df = S.Form()
        self._drift_param = _NoScrollComboBox()
        self._drift_param.addItems(["spline", "linear", "constant"])
        self._drift_knots = _NoScrollSpinBox(); self._drift_knots.setRange(2, 20); self._drift_knots.setValue(5)
        df.row(("Parametrization:", self._drift_param), ("n_knots:", self._drift_knots))
        drift.body.addLayout(df)
        self._drift_bayesian = QtWidgets.QCheckBox("Bayesian σ estimate"); self._drift_bayesian.setChecked(True)
        drift.body.addWidget(self._drift_bayesian)
        self._drift_fit_btn = QtWidgets.QPushButton("Fit trajectory")
        self._drift_fit_btn.clicked.connect(self._fit_drift)
        drift.body.addWidget(self._drift_fit_btn)
        self._drift_status_lbl = QtWidgets.QLabel("No trajectory fitted")
        self._drift_status_lbl.setStyleSheet(f"color:{S.MUTED};font-size:10px")
        drift.body.addWidget(self._drift_status_lbl)
        lv.addWidget(drift)

        # ── Output ──
        out = S.make_card("Output")
        self._out_ed = QtWidgets.QLineEdit(); self._out_ed.setPlaceholderText("Output directory…")
        orow = QtWidgets.QHBoxLayout(); orow.setSpacing(4); orow.addWidget(self._out_ed, 1)
        bou = _br(); bou.clicked.connect(lambda: self._out_ed.setText(
            QtWidgets.QFileDialog.getExistingDirectory(self, "Output directory") or "")); orow.addWidget(bou)
        out.body.addLayout(S.Form().row(("Folder:", orow)))
        self._fmt = _NoScrollComboBox()
        for label in OUTPUT_FORMATS:
            self._fmt.addItem(label, OUTPUT_FORMATS[label])
        out.body.addLayout(S.Form().row(("Format:", self._fmt)))
        lv.addWidget(out)

        # ── Run ──
        self._run_btn = S.primary_btn("Start Integration")
        self._run_btn.clicked.connect(self._run)
        self._abort_btn = QtWidgets.QPushButton("Abort")
        self._abort_btn.setEnabled(False)
        self._abort_btn.setToolTip("Stop after the current frame, keeping frames already integrated.")
        self._abort_btn.clicked.connect(self._abort)
        run_row = QtWidgets.QHBoxLayout(); run_row.setSpacing(6)
        run_row.addWidget(self._run_btn, 1); run_row.addWidget(self._abort_btn)
        lv.addLayout(run_row)
        self._clear_btn = QtWidgets.QPushButton("Clear results")
        self._clear_btn.setToolTip(
            "Remove the integrated profiles/plots computed this session for the "
            "current data so a fresh integration can start. Does NOT delete raw "
            "data or any files on disk.")
        self._clear_btn.clicked.connect(self._clear_results)
        lv.addWidget(self._clear_btn)
        self._prog = QtWidgets.QProgressBar(); self._prog.setRange(0, 100); self._prog.setVisible(False)
        lv.addWidget(self._prog)
        self._prog_lbl = QtWidgets.QLabel(""); self._prog_lbl.setStyleSheet(f"font-size:10px;color:{S.MUTED}")
        lv.addWidget(self._prog_lbl)
        lv.addStretch(1)
        split.addWidget(scroll)

        # Right: waterfall / stacked-profiles tabs + log
        right = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self._view_tabs = QtWidgets.QTabWidget()
        self._waterfall = WaterfallViewer()
        self._stack_view = StackedProfileViewer()
        self._view_tabs.addTab(self._waterfall, "Waterfall")
        self._view_tabs.addTab(self._stack_view, "Stacked profiles")
        right.addWidget(self._view_tabs)
        self._log = LogPanel()
        right.addWidget(self._log)
        right.setStretchFactor(0, 4); right.setStretchFactor(1, 1)
        right.setMinimumWidth(320)
        split.addWidget(right)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 0); split.setStretchFactor(2, 1)
        split.setSizes([286, 361, 950])

    # ── Run ────────────────────────────────────────────────────────

    def _build_spec(self):
        # Always R-uniform; Q-uniform is handled by rebinning in the worker because the
        # kernels do not implement Q-mode binning (see analyze_workflows/workflow_analysis.md).
        r_bin = self._r_bin.value(); e_bin = self._e_bin.value()
        if self._use_tab2_btn.isChecked():
            if self._calib_result is None:
                raise RuntimeError("No calibration from Tab 2. Run Tab 2 first.")
            return _build_spec(self._calib_result, r_bin, e_bin)
        path = self._json_ed.text().strip()
        if not path or not Path(path).exists():
            raise FileNotFoundError(f"Calibration file not found: {path}")
        return spec_from_geometry_file(path, r_bin, e_bin)

    def _run(self):
        if self._worker and self._worker.isRunning():
            return
        # A fresh batch run resets the display; stop any active monitor first.
        if self._loader.is_monitoring():
            self._stop_monitor()
            self._loader.set_monitor_active(False)
        self._orphans = [o for o in self._orphans if o.isRunning()]   # drop finished ones
        try:
            spec = self._build_spec()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Calibration error", str(e)); return

        src_cfg = self._loader.source_cfg()
        if not src_cfg.get("path"):
            QtWidgets.QMessageBox.warning(self, "No data", "Select a data folder/glob or HDF5 file."); return

        kernel = self._kernel.currentData()
        corrections = self._corr_widget.build_corrections()
        variance_cfg = ({"error_model": self._err_model.currentText()}
                        if self._var_check.isChecked() else None)
        if variance_cfg and self._corr_widget.any_enabled():
            self._log.append("[batch] Note: corrections enabled → variance ignored (σ=√I).")
            variance_cfg = None

        out_dir = self._out_ed.text().strip() or None
        fmt = self._fmt.currentData()
        q_cfg = ({"QMin": self._q_min.value(), "QMax": self._q_max.value(),
                  "QBinSize": self._q_bin.value()} if self._q_check.isChecked() else None)
        self._stack_view.set_axis_context(
            float(spec.Lsd), float(spec.pxY), float(spec.Wavelength),
            "Q" if q_cfg else "R")

        # Dark / bright / background fields (from the loader)
        for sel in self._loader.has_pending_fields():
            QtWidgets.QMessageBox.warning(
                self, "Field not computed",
                f"'{sel.title()}' is enabled but not computed. "
                "Click 'Compute field' in that box first."); return
        dark = self._loader.dark()
        bright = self._loader.bright()
        background = self._loader.background()
        bright_mode = self._loader.bright_mode()

        self._run_btn.setEnabled(False); self._abort_btn.setEnabled(True)
        self._prog.setVisible(True); self._prog.setValue(0)
        self._wf_started = False
        self._integrated_fids = set()
        self._view_tabs.setCurrentWidget(self._waterfall)
        self._log.append("─" * 40 + "\nStarting batch integration…")

        # Frame range (from the loader)
        frame_range = self._loader.frame_range()

        # Monitor normalisation file
        monitor_file = self._mon_ed.text().strip() or None

        # Drift trajectory (optional)
        drift_traj = None
        if self._drift_chk.isChecked():
            if self._drift_traj is None:
                QtWidgets.QMessageBox.warning(
                    self, "No trajectory",
                    "Drift correction enabled but no trajectory fitted.\n"
                    "Click 'Fit trajectory' first."); return
            drift_traj = self._drift_traj

        weighted = bool(self._azim.currentData())
        sig = self._integration_signature(src_cfg, kernel, corrections, weighted)
        context = self._geom_cache if (sig == self._geom_sig and
                                       self._geom_cache is not None) else None
        self._worker = BatchWorker(
            spec, src_cfg, self._loader.composite_mask(), out_dir, fmt, kernel,
            corrections, variance_cfg, q_cfg=q_cfg,
            frame_range=frame_range, monitor_file=monitor_file,
            drift_traj=drift_traj, parent=self,
            dark=dark, bright=bright, background=background, bright_mode=bright_mode,
            weighted=weighted, context=context)
        self._worker.progress.connect(self._on_progress)
        self._worker.frame_done.connect(self._on_frame)
        self._worker.finished.connect(self._on_done)
        self._worker.failed.connect(self._on_fail)
        self._worker.log_line.connect(self._log.append)
        self._worker.geom_ready.connect(lambda ctx, s=sig: self._cache_geom(s, ctx))
        self._worker.start()

    def _abort(self):
        """Stop the run. First ask the worker to stop cooperatively (clean finish
        with a summary); if it does not stop quickly, detach + terminate it and free
        the slot so a new run can start immediately (the orphaned thread winds down
        on its own). Frames already integrated were written to disk as the run went."""
        w = self._worker
        if not (w and w.isRunning()):
            return
        self._abort_btn.setEnabled(False)
        self._abort_btn.setText("Aborting…")
        self._log.append("[batch] aborting…")
        QtWidgets.QApplication.processEvents()   # paint the "Aborting…" state
        w.requestInterruption()
        if w.wait(1000):
            return   # stopped cooperatively → _on_done fires and resets the UI
        self._log.append("[batch] force-terminating worker…")
        for sig in (w.progress, w.frame_done, w.finished, w.failed, w.log_line):
            try:
                sig.disconnect()
            except Exception:
                pass
        w.terminate()
        self._orphans.append(w)
        self._worker = None
        self._reset_run_buttons(); self._prog.setVisible(False)
        self._log.append("[batch] aborted (background thread winding down). "
                         "Completed frames were saved.")

    def _on_progress(self, done, total):
        self._prog.setValue(int(100 * done / total) if total else 0)
        self._prog_lbl.setText(f"Integrated {done} / {total} frames")

    def _on_frame(self, fid, r_ax, prof, sigma):
        if not getattr(self, "_wf_started", False):
            self._waterfall.reset(r_ax)
            self._stack_view.reset(r_ax)
            self._wf_started = True
        self._waterfall.add_profile(prof)
        self._stack_view.add_profile(r_ax, prof, label=fid)
        self._integrated_fids.add(str(fid))
        self._log.append(f"  frame {fid}: peak={prof.max():.1f}")

    def _reset_run_buttons(self):
        self._run_btn.setEnabled(True)
        self._abort_btn.setEnabled(False); self._abort_btn.setText("Abort")

    def _on_done(self, data):
        self._reset_run_buttons(); self._prog.setVisible(False)
        n = data["n"]; out = data.get("out_paths", [])
        aborted = data.get("aborted", False)
        verb = "aborted after" if aborted else "Done —"
        msg = f"{verb} {n} frames integrated"
        if out:
            msg += f"\nSaved to: {Path(out[0]).parent}"
        self._log.append(msg)
        self._prog_lbl.setText(f"{'Aborted' if aborted else 'Complete'}: {n} frames")
        QtWidgets.QMessageBox.information(self, "Aborted" if aborted else "Done", msg)

    def _on_fail(self, msg):
        self._reset_run_buttons(); self._prog.setVisible(False)
        self._log.append(f"\nERROR:\n{msg[:600]}")
        QtWidgets.QMessageBox.critical(self, "Integration failed", msg[:400])

    def _clear_results(self):
        """Clear this session's computed profiles/plots for the current data so a
        fresh integration can start. Only in-session results are cleared — no raw
        data or files on disk are touched."""
        if self._worker and self._worker.isRunning():
            QtWidgets.QMessageBox.warning(
                self, "Run in progress",
                "Abort the running integration before clearing results.")
            return
        if self._loader.is_monitoring():
            self._stop_monitor()
            self._loader.set_monitor_active(False)
        self._waterfall.reset()
        self._stack_view.reset()
        self._integrated_fids = set()
        self._wf_started = False
        self._prog.setVisible(False); self._prog.setValue(0)
        self._prog_lbl.setText("")
        self._log.append("Cleared session results — raw data untouched.")

    # ── Folder monitoring (live new-file integration) ──────────────

    def _integration_signature(self, src_cfg, kernel, corrections, weighted):
        """Signature identifying a reusable detector map for the current settings."""
        if self._use_tab2_btn.isChecked():
            calib = ("tab2", id(self._calib_result))
        else:
            calib = ("file", self._json_ed.text().strip())
        mask = self._loader.composite_mask()
        mask_id = None if mask is None else (tuple(mask.shape), int(np.count_nonzero(mask)))
        pol, sa = corrections
        return (calib, kernel, round(self._r_bin.value(), 4), round(self._e_bin.value(), 4),
                bool(weighted), pol is not None, sa is not None, mask_id,
                src_cfg.get("path"), src_cfg.get("type"), src_cfg.get("dataset"))

    def _cache_geom(self, sig, ctx):
        self._geom_cache = ctx
        self._geom_sig = sig

    def _toggle_monitor(self, on):
        if on:
            self._start_monitor()
        else:
            self._stop_monitor()

    def _start_monitor(self):
        if self._monitor_worker and self._monitor_worker.isRunning():
            return
        if self._worker and self._worker.isRunning():
            QtWidgets.QMessageBox.warning(
                self, "Busy", "Wait for the batch run to finish (or Abort) before monitoring.")
            self._loader.set_monitor_active(False); return
        src_cfg = self._loader.source_cfg()
        if src_cfg.get("type") != "tiff_glob" or not src_cfg.get("path"):
            QtWidgets.QMessageBox.warning(
                self, "Folder needed",
                "MONITOR watches a folder/glob of TIFF frames — select a folder "
                "as the data source (HDF5 sources are not monitored).")
            self._loader.set_monitor_active(False); return
        try:
            spec = self._build_spec()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Calibration error", str(e))
            self._loader.set_monitor_active(False); return
        for sel in self._loader.has_pending_fields():
            QtWidgets.QMessageBox.warning(
                self, "Field not computed",
                f"'{sel.title()}' is enabled but not computed. Click 'Compute field' first.")
            self._loader.set_monitor_active(False); return

        kernel = self._kernel.currentData()
        corrections = self._corr_widget.build_corrections()
        variance_cfg = ({"error_model": self._err_model.currentText()}
                        if self._var_check.isChecked() else None)
        if variance_cfg and self._corr_widget.any_enabled():
            variance_cfg = None
        q_cfg = ({"QMin": self._q_min.value(), "QMax": self._q_max.value(),
                  "QBinSize": self._q_bin.value()} if self._q_check.isChecked() else None)
        self._stack_view.set_axis_context(
            float(spec.Lsd), float(spec.pxY), float(spec.Wavelength),
            "Q" if q_cfg else "R")
        weighted = bool(self._azim.currentData())
        dark = self._loader.dark(); bright = self._loader.bright()
        background = self._loader.background(); bmode = self._loader.bright_mode()
        out_dir = self._out_ed.text().strip() or None
        fmt = self._fmt.currentData()

        sig = self._integration_signature(src_cfg, kernel, corrections, weighted)
        context = self._geom_cache if (sig == self._geom_sig and
                                       self._geom_cache is not None) else None

        self._monitor_worker = FolderMonitorWorker(
            spec, src_cfg["path"], self._loader.composite_mask(), kernel, corrections,
            variance_cfg, q_cfg=q_cfg, dark=dark, bright=bright, background=background,
            bright_mode=bmode, weighted=weighted, seen=set(self._integrated_fids),
            context=context, out_dir=out_dir, fmt=fmt, parent=self)
        self._monitor_worker.frame_done.connect(self._on_frame)
        self._monitor_worker.new_count.connect(self._on_monitor_count)
        self._monitor_worker.log_line.connect(self._log.append)
        self._monitor_worker.failed.connect(self._on_monitor_fail)
        self._monitor_worker.geom_ready.connect(lambda ctx, s=sig: self._cache_geom(s, ctx))
        self._view_tabs.setCurrentWidget(self._stack_view)
        self._log.append("─" * 40 + "\nMONITOR active — watching the folder for new frames…")
        self._monitor_worker.start()

    def _stop_monitor(self):
        w = self._monitor_worker
        if w and w.isRunning():
            w.requestInterruption()
            if not w.wait(1500):
                for s in (w.frame_done, w.new_count, w.status, w.log_line,
                          w.failed, w.geom_ready):
                    try:
                        s.disconnect()
                    except Exception:
                        pass
                w.terminate(); self._orphans.append(w)
            self._log.append("[monitor] stopped.")
        self._monitor_worker = None

    def _on_monitor_count(self, n):
        self._prog_lbl.setText(f"Monitoring — {n} new frame(s) integrated")

    def _on_monitor_fail(self, msg):
        self._loader.set_monitor_active(False)
        self._monitor_worker = None
        self._log.append(f"\n[monitor] ERROR:\n{msg[:600]}")
        QtWidgets.QMessageBox.critical(self, "Monitor failed", msg[:400])

    # ── Drift correction ───────────────────────────────────────────

    def _fit_drift(self):
        """Parse the anchors JSON and fit the drift trajectory."""
        if self._drift_worker and self._drift_worker.isRunning():
            return
        anchor_path = self._drift_anchor_ed.text().strip()
        if not anchor_path:
            QtWidgets.QMessageBox.warning(self, "Missing", "Specify an anchor JSON file."); return
        from pathlib import Path as _Path
        import json as _json
        try:
            raw = _json.loads(_Path(anchor_path).read_text())
            # JSON keys are strings; convert to int
            anchors = {int(k): v for k, v in raw.items()}
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "JSON error", str(e)); return
        if len(anchors) < 2:
            QtWidgets.QMessageBox.warning(self, "Too few anchors",
                                           "Need at least 2 anchor frames."); return
        try:
            calib_result = self._calib_result
            if calib_result is None:
                raise RuntimeError("Run Tab 2 calibration first.")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Calibration missing", str(e)); return

        # Sample indices: span the anchor range
        idx_min = min(anchors); idx_max = max(anchors)
        sample_indices = list(range(idx_min, idx_max + 1))

        cfg = {
            "parametrization": self._drift_param.currentText(),
            "n_knots": self._drift_knots.value(),
            "bayesian_sigma": self._drift_bayesian.isChecked(),
        }
        self._drift_status_lbl.setText("Fitting…")
        self._drift_fit_btn.setEnabled(False)
        self._log.append("─" * 40 + f"\nFitting drift trajectory ({len(anchors)} anchors)…")
        self._drift_worker = DriftWorker(
            calib_result, anchors, sample_indices, cfg, parent=self)
        self._drift_worker.log_line.connect(self._log.append)
        self._drift_worker.finished.connect(self._on_drift_done)
        self._drift_worker.failed.connect(self._on_drift_fail)
        self._drift_worker.start()

    def _on_drift_done(self, traj):
        self._drift_traj = traj
        self._drift_fit_btn.setEnabled(True)
        Lsd_range = f"{traj.Lsd_t.min():.0f}–{traj.Lsd_t.max():.0f} µm"
        self._drift_status_lbl.setText(f"Trajectory ready: Lsd {Lsd_range}  ({len(traj.frame_indices)} knots)")
        self._log.append(f"[drift] trajectory fitted  Lsd {Lsd_range}")

    def _on_drift_fail(self, msg):
        self._drift_fit_btn.setEnabled(True)
        self._drift_status_lbl.setText("Fitting failed")
        self._log.append(f"\n[drift] ERROR:\n{msg[:600]}")
        QtWidgets.QMessageBox.critical(self, "Drift fitting failed", msg[:400])
