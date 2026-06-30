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
                           DEFAULT_NICKEL_DIR, DEFAULT_NICKEL_H5)
from midas_gui.helpers import _fspin, _browse, _build_spec, _spec_from_json, _NoScrollSpinBox
from midas_gui.widgets import LogPanel, CorrectionFlagsWidget, WaterfallViewer, StackedProfileViewer
from midas_gui.workers import BatchWorker, apply_q_uniform, DriftWorker
from midas_gui import style as S


class BatchTab(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._mask: Optional[np.ndarray] = None
        self._worker = None
        self._drift_worker = None
        self._drift_traj = None
        self._calib_result = None
        self._wf_started = False
        self._build_ui()

    def set_calibration(self, result):
        self._calib_result = result
        self._calib_src_lbl.setText(
            f"From Tab 2: Lsd={result.Lsd/1000:.3f} mm  "
            f"λ={result.wavelength_A:.5f} Å  {result.NrPixelsY}×{result.NrPixelsZ} px")
        self._use_tab2_btn.setChecked(True)

    def set_mask_from_tab1(self, mask):
        self._mask = mask
        if mask is not None:
            self._mask_lbl.setText(f"From Tab 1: {int(mask.sum()):,} bad px")

    def _build_ui(self):
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6); root.setSpacing(8)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True); scroll.setFixedWidth(372)
        inner = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(inner); lv.setContentsMargins(2, 2, 2, 2); lv.setSpacing(8)
        scroll.setWidget(inner)

        def _br(w=30):
            b = QtWidgets.QPushButton("…"); b.setFixedWidth(w); return b

        # ── Calibration source ──
        cal = S.make_card("Calibration source")
        src_row = QtWidgets.QHBoxLayout(); src_row.setSpacing(10)
        self._use_tab2_btn = QtWidgets.QRadioButton("From Tab 2")
        self._use_json_btn = QtWidgets.QRadioButton("From JSON file")
        self._use_tab2_btn.setChecked(True)
        src_row.addWidget(self._use_tab2_btn); src_row.addWidget(self._use_json_btn); src_row.addStretch(1)
        cal.body.addLayout(src_row)
        self._calib_src_lbl = QtWidgets.QLabel("(run Tab 2 first)")
        self._calib_src_lbl.setStyleSheet(f"color:{S.MUTED};font-size:10px")
        cal.body.addWidget(self._calib_src_lbl)
        self._json_ed = QtWidgets.QLineEdit(); self._json_ed.setPlaceholderText("calibration.json…")
        jr = QtWidgets.QHBoxLayout(); jr.setSpacing(4); jr.addWidget(self._json_ed, 1)
        bj = _br(); bj.clicked.connect(lambda: self._json_ed.setText(
            _browse(self, "Open calibration.json", "JSON (*.json);;All (*)") or "")); jr.addWidget(bj)
        cal.body.addLayout(jr)
        lv.addWidget(cal)

        # ── Data files ──
        data = S.make_card("Data files")
        self._tiff_rb = QtWidgets.QRadioButton("TIFF"); self._tiff_rb.setChecked(True)
        self._tiff_ed = QtWidgets.QLineEdit(DEFAULT_NICKEL_DIR); self._tiff_ed.setPlaceholderText("folder or *.tif glob")
        tr = QtWidgets.QHBoxLayout(); tr.setSpacing(4); tr.addWidget(self._tiff_ed, 1)
        bt = _br(); bt.clicked.connect(self._browse_tiff); tr.addWidget(bt)
        data.body.addLayout(S.Form().row((self._tiff_rb, tr)))
        self._hdf5_rb = QtWidgets.QRadioButton("HDF5")
        self._h5_ed = QtWidgets.QLineEdit(DEFAULT_NICKEL_H5); self._h5_ed.setPlaceholderText("file.h5")
        hr = QtWidgets.QHBoxLayout(); hr.setSpacing(4); hr.addWidget(self._h5_ed, 1)
        bh = _br(); bh.clicked.connect(self._browse_h5); hr.addWidget(bh)
        data.body.addLayout(S.Form().row((self._hdf5_rb, hr)))
        self._h5_dset = QtWidgets.QLineEdit("exchange/data")
        data.body.addLayout(S.Form().row(("Dataset:", self._h5_dset)))
        lv.addWidget(data)

        # ── Streaming controls ──
        stream = S.make_card("Streaming controls")
        self._fr_start = _NoScrollSpinBox(); self._fr_start.setRange(0, 999999); self._fr_start.setValue(0)
        self._fr_start.setToolTip("First frame index to process (0-based, inclusive)")
        self._fr_end = _NoScrollSpinBox(); self._fr_end.setRange(0, 999999); self._fr_end.setValue(0)
        self._fr_end.setToolTip("Last frame index (exclusive). 0 = process all frames.")
        self._fr_stride = _NoScrollSpinBox(); self._fr_stride.setRange(1, 100); self._fr_stride.setValue(1)
        self._fr_stride.setToolTip("Process every Nth frame. 1 = all, 2 = every other, etc.")
        sf = S.Form()
        sf.row(("Start frame:", self._fr_start), ("End (0=all):", self._fr_end))
        sf.row(("Stride:", self._fr_stride))
        stream.body.addLayout(sf)
        lv.addWidget(stream)

        # ── Mask ──
        mask = S.make_card("Mask (optional)")
        self._mask_ed = QtWidgets.QLineEdit(); self._mask_ed.setPlaceholderText("Mask file…")
        mr = QtWidgets.QHBoxLayout(); mr.setSpacing(4); mr.addWidget(self._mask_ed, 1)
        bm = _br(); bm.clicked.connect(self._browse_mask); mr.addWidget(bm)
        lmb = QtWidgets.QPushButton("Load"); lmb.setFixedWidth(52); lmb.clicked.connect(self._load_mask); mr.addWidget(lmb)
        mask.body.addLayout(mr)
        self._mask_lbl = QtWidgets.QLabel("No mask"); self._mask_lbl.setStyleSheet(f"color:{S.MUTED};font-size:10px")
        mask.body.addWidget(self._mask_lbl)
        lv.addWidget(mask)

        # ── Integration ──
        integ = S.make_card("Integration")
        self._kernel = QtWidgets.QComboBox()
        for label, key in KERNELS.items():
            self._kernel.addItem(label, key)
        self._r_bin = _fspin(0.1, 20.0, 2, 1.0, "px")
        self._e_bin = _fspin(0.5, 30.0, 1, 5.0, "°")
        intf = S.Form()
        intf.row(("Kernel:", self._kernel))
        intf.row(("R bin:", self._r_bin), ("η bin:", self._e_bin))
        integ.body.addLayout(intf)
        self._var_check = QtWidgets.QCheckBox("Per-bin variance (σ)")
        self._var_check.setToolTip(
            "Compute per-bin σ via the chosen error model.\n"
            "Mutually exclusive with corrections (corrections win; σ→√I).")
        self._err_model = QtWidgets.QComboBox(); self._err_model.addItems(ERROR_MODELS); self._err_model.setEnabled(False)
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
        self._drift_param = QtWidgets.QComboBox()
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
        self._fmt = QtWidgets.QComboBox()
        for label in OUTPUT_FORMATS:
            self._fmt.addItem(label, OUTPUT_FORMATS[label])
        out.body.addLayout(S.Form().row(("Format:", self._fmt)))
        lv.addWidget(out)

        # ── Run ──
        self._run_btn = S.primary_btn("Start Integration")
        self._run_btn.clicked.connect(self._run)
        lv.addWidget(self._run_btn)
        self._prog = QtWidgets.QProgressBar(); self._prog.setRange(0, 100); self._prog.setVisible(False)
        lv.addWidget(self._prog)
        self._prog_lbl = QtWidgets.QLabel(""); self._prog_lbl.setStyleSheet(f"font-size:10px;color:{S.MUTED}")
        lv.addWidget(self._prog_lbl)
        lv.addStretch(1)
        root.addWidget(scroll)

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
        root.addWidget(right, stretch=1)

    # ── File browsing ─────────────────────────────────────────────

    def _browse_tiff(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Select TIFF folder")
        if d: self._tiff_ed.setText(str(d))

    def _browse_h5(self):
        p = _browse(self, "Open HDF5", "HDF5 (*.h5 *.hdf5 *.hdf *.nxs);;All (*)")
        if p: self._h5_ed.setText(p)

    def _browse_mask(self):
        p = _browse(self, "Open Mask", "TIFF (*.tif *.tiff);;All (*)")
        if p: self._mask_ed.setText(p)

    def _load_mask(self):
        path = self._mask_ed.text().strip()
        if not path or not Path(path).exists(): return
        try:
            import tifffile
            self._mask = (tifffile.imread(path) != 0).astype(np.uint8)
            self._mask_lbl.setText(f"File: {int(self._mask.sum()):,} bad px")
            self._log.append(f"Mask loaded: {Path(path).name}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Load error", str(e))

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
            raise FileNotFoundError(f"calibration.json not found: {path}")
        return _spec_from_json(path, r_bin, e_bin)

    def _run(self):
        if self._worker and self._worker.isRunning():
            return
        try:
            spec = self._build_spec()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Calibration error", str(e)); return

        if self._tiff_rb.isChecked():
            path = self._tiff_ed.text().strip()
            if not path:
                QtWidgets.QMessageBox.warning(self, "No data", "Specify TIFF folder/glob."); return
            src_cfg = {"type": "tiff_glob", "path": path}
        else:
            path = self._h5_ed.text().strip()
            if not path or not Path(path).exists():
                QtWidgets.QMessageBox.warning(self, "No data", "Specify HDF5 file."); return
            src_cfg = {"type": "hdf5", "path": path,
                       "dataset": self._h5_dset.text().strip() or "frames"}

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

        self._run_btn.setEnabled(False)
        self._prog.setVisible(True); self._prog.setValue(0)
        self._wf_started = False
        self._view_tabs.setCurrentWidget(self._waterfall)
        self._log.append("─" * 40 + "\nStarting batch integration…")

        # Frame range
        fr_start = self._fr_start.value()
        fr_end = self._fr_end.value() if self._fr_end.value() > 0 else None
        fr_stride = max(1, self._fr_stride.value())
        frame_range = (fr_start, fr_end, fr_stride)

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

        self._worker = BatchWorker(
            spec, src_cfg, self._mask, out_dir, fmt, kernel,
            corrections, variance_cfg, q_cfg=q_cfg,
            frame_range=frame_range, monitor_file=monitor_file,
            drift_traj=drift_traj, parent=self)
        self._worker.progress.connect(self._on_progress)
        self._worker.frame_done.connect(self._on_frame)
        self._worker.finished.connect(self._on_done)
        self._worker.failed.connect(self._on_fail)
        self._worker.log_line.connect(self._log.append)
        self._worker.start()

    def _on_progress(self, done, total):
        self._prog.setValue(int(100 * done / total) if total else 0)
        self._prog_lbl.setText(f"Integrated {done} / {total} frames")

    def _on_frame(self, fid, r_ax, prof, sigma):
        if not getattr(self, "_wf_started", False):
            self._waterfall.reset(r_ax)
            self._stack_view.reset(r_ax)
            self._wf_started = True
        self._waterfall.add_profile(prof)
        self._stack_view.add_profile(r_ax, prof)
        self._log.append(f"  frame {fid}: peak={prof.max():.1f}")

    def _on_done(self, data):
        self._run_btn.setEnabled(True); self._prog.setVisible(False)
        n = data["n"]; out = data.get("out_paths", [])
        msg = f"Done — {n} frames integrated"
        if out:
            msg += f"\nSaved to: {Path(out[0]).parent}"
        self._log.append(msg)
        self._prog_lbl.setText(f"Complete: {n} frames")
        QtWidgets.QMessageBox.information(self, "Done", msg)

    def _on_fail(self, msg):
        self._run_btn.setEnabled(True); self._prog.setVisible(False)
        self._log.append(f"\nERROR:\n{msg[:600]}")
        QtWidgets.QMessageBox.critical(self, "Integration failed", msg[:400])

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
