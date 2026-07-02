"""Tab 2 — Calibrate.

Ports the v3 calibration tab and adds Phase-1 features:
  - pipeline dropdown (one-shot / first-time / four-stage)
  - refine-flags group (Lsd, BC, ty, tz, tx, Wavelength, Distortion)
  - read-only distortion-coefficient table
  - per-ring radial-residual bar chart (new bottom tab)
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

import numpy as np
from PyQt5 import QtCore, QtWidgets
import pyqtgraph as pg

from midas_gui.constants import (
    CALIBRANTS, PIPELINES, _SG, _LC, DEFAULT_WAVELENGTH, DEFAULT_PIXEL_UM,
    DEFAULT_LSD_UM, DEFAULT_BC_Y, DEFAULT_BC_Z, DEFAULT_CALIBRANT_TIF)
from midas_gui.helpers import (
    _load_image, _fspin, _NoScrollSpinBox, _browse, _predict_ring_radii, is_h5)
from midas_gui.widgets import (
    PickableImageViewer, ProfileViewer, LogPanel, ResidualBarChart, DistortionTable,
    FieldSelector)
from midas_gui.workers import CalibrationWorker, IntegrationWorker, CorrectedRingsWorker
from midas_gui.dialogs import _SaveParamstestDialog
from midas_gui import style as S


class CalibrationTab(QtWidgets.QWidget):
    calibrationDone = QtCore.pyqtSignal(object)   # AutoCalibrationResult

    def __init__(self, parent=None):
        super().__init__(parent)
        self._image: Optional[np.ndarray] = None
        self._dark: Optional[np.ndarray] = None
        self._mask: Optional[np.ndarray] = None
        self._result = None
        self._worker = None
        self._int_worker = None
        self._calib_cancelled = False
        self._orphans: list = []       # aborted workers kept alive until they wind down
        self._ring_items: list = []
        self._corrected_ring_items: list = []
        self._corrected_rings_worker = None
        self._calib_result = None
        self._build_ui()
        if Path(self._img_ed.text().strip() or "x").exists():
            self._load_img()

    def set_mask_from_tab1(self, mask: Optional[np.ndarray]):
        self._mask = mask
        self._mask_lbl.setText(f"Mask from Tab 1: {int(mask.sum()):,} bad px"
                               if mask is not None else "No mask")

    # ── UI ────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6); root.setSpacing(8)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True); scroll.setFixedWidth(484)
        inner = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(inner); lv.setContentsMargins(2, 2, 2, 2); lv.setSpacing(8)
        scroll.setWidget(inner)

        def _br(w=30):
            b = QtWidgets.QPushButton("…"); b.setFixedWidth(w); return b

        def _frow(ed, slot):
            r = QtWidgets.QHBoxLayout(); r.setSpacing(4)
            r.addWidget(ed); b = _br(); b.clicked.connect(slot); r.addWidget(b); return r

        # ── Pipeline ──
        pipe = S.make_card("Pipeline")
        self._pipeline = QtWidgets.QComboBox()
        for label, key, enabled in PIPELINES:
            self._pipeline.addItem(label, key)
            if not enabled:
                self._pipeline.model().item(self._pipeline.count() - 1).setEnabled(False)
        self._pipeline.setToolTip(
            "Lsd & beam-centre are recovered well by every pipeline.\n"
            "For trustworthy TILTS / strain, prefer Four-stage or First-time —\n"
            "validation found One-shot / Bayesian can report a spurious tilt on\n"
            "weakly-tilted data (it is self-compensated, so integration is still fine).")
        pipe.body.addWidget(self._pipeline)
        guide = QtWidgets.QLabel("Lsd/BC: any · tilt/strain: Four-stage or First-time")
        guide.setStyleSheet(f"color:{S.MUTED};font-size:10px"); guide.setWordWrap(True)
        pipe.body.addWidget(guide)
        lv.addWidget(pipe)

        # ── Files ──
        files = S.make_card("Files")
        self._img_ed = QtWidgets.QLineEdit(); self._img_ed.setPlaceholderText("Calibrant image…")
        self._img_ed.setText(DEFAULT_CALIBRANT_TIF)
        files.body.addLayout(S.Form().row(("Image:", _frow(self._img_ed, self._browse_img))))
        self._img_h5_lbl = S.LabelRight("Dataset:")
        self._img_h5_ed = QtWidgets.QLineEdit("exchange/data")
        self._img_h5_lbl.setVisible(False); self._img_h5_ed.setVisible(False)
        ir = QtWidgets.QHBoxLayout(); ir.setSpacing(4); ir.addWidget(self._img_h5_lbl); ir.addWidget(self._img_h5_ed, 1)
        files.body.addLayout(ir)
        self._img_ed.textChanged.connect(lambda p: (
            self._img_h5_lbl.setVisible(is_h5(p)), self._img_h5_ed.setVisible(is_h5(p))))
        self._img_ed.returnPressed.connect(self._load_img)
        self._img_h5_ed.editingFinished.connect(
            lambda: self._image is not None and self._load_img())
        self._frame_spin = _NoScrollSpinBox(); self._frame_spin.setRange(0, 99999); self._frame_spin.setValue(0)
        self._frame_spin.valueChanged.connect(
            lambda _=0: self._image is not None and self._load_img())
        files.body.addLayout(S.Form().row(("Frame index:", self._frame_spin)))
        self._mask_file_ed = QtWidgets.QLineEdit(); self._mask_file_ed.setPlaceholderText("Mask file…")
        mr = QtWidgets.QHBoxLayout(); mr.setSpacing(4); mr.addWidget(self._mask_file_ed, 1)
        bm = _br(); bm.clicked.connect(self._browse_mask_file); mr.addWidget(bm)
        files.body.addLayout(S.Form().row(("Mask:", mr)))
        self._mask_lbl = QtWidgets.QLabel("No mask")
        self._mask_lbl.setStyleSheet(f"color:{S.MUTED};font-size:10px")
        files.body.addWidget(self._mask_lbl)
        self._use_mask_check = QtWidgets.QCheckBox("Apply mask to calibration & integration")
        self._use_mask_check.setChecked(True)
        self._use_mask_check.setToolTip(
            "When checked, the mask from Tab 1 is applied before calibration and integration.\n"
            "Uncheck to ignore the mask (useful for diagnosing whether bad pixels are causing issues).")
        files.body.addWidget(self._use_mask_check)
        lv.addWidget(files)

        # ── Dark / Bright / Background ──
        fld = S.make_card("Dark / Bright / Background")
        self._dark_sel = FieldSelector("Dark", default_dataset="exchange/data_dark")
        self._bright_sel = FieldSelector("Bright", with_mode=True)
        self._bg_sel = FieldSelector("Background")
        for w in (self._dark_sel, self._bright_sel, self._bg_sel):
            fld.body.addWidget(w)
        lv.addWidget(fld)

        # ── Detector & Calibrant ──
        det = S.make_card("Detector & Calibrant")
        self._wl = _fspin(0.001, 10.0, 5, DEFAULT_WAVELENGTH, "Å")
        self._cal = QtWidgets.QComboBox(); self._cal.addItems(CALIBRANTS)
        det.body.addLayout(S.Form().row(("λ:", self._wl), ("Calibrant:", self._cal)))
        self._pxY = _fspin(1.0, 5000.0, 2, DEFAULT_PIXEL_UM, "µm")
        self._pxZ_check = QtWidgets.QCheckBox("pxZ")
        self._pxZ_spin = _fspin(1.0, 5000.0, 2, DEFAULT_PIXEL_UM, "µm"); self._pxZ_spin.setEnabled(False)
        self._pxZ_check.toggled.connect(self._pxZ_spin.setEnabled)
        prow = QtWidgets.QHBoxLayout(); prow.setSpacing(4)
        prow.addWidget(self._pxY, 1); prow.addWidget(self._pxZ_check); prow.addWidget(self._pxZ_spin, 1)
        det.body.addLayout(S.Form().row(("Pixel:", prow)))
        self._flip_y = QtWidgets.QCheckBox("Flip Y"); self._flip_z = QtWidgets.QCheckBox("Flip Z")
        self._transp = QtWidgets.QCheckBox("Transpose")
        tb2 = QtWidgets.QHBoxLayout(); tb2.setSpacing(8)
        tb2.addWidget(self._flip_y); tb2.addWidget(self._flip_z); tb2.addWidget(self._transp); tb2.addStretch(1)
        det.body.addWidget(S.LabelRight("Transforms:")); det.body.addLayout(tb2)
        lv.addWidget(det)

        # ── Threshold (calibration image only) ──
        thr = S.make_card("Threshold  (pixels below → 0, calibration image)")
        self._thr_check = QtWidgets.QCheckBox("Apply threshold to calibration image")
        self._thr_check.setToolTip(
            "When on, pixels dimmer than the slider value are set to 0 in the image\n"
            "fed to the calibration pipeline (and the live preview). Useful to drop\n"
            "background / weak pixels before calibrating.")
        thr.body.addWidget(self._thr_check)
        self._thr_min = _fspin(-1e9, 1e9, 1, 0.0)
        self._thr_max = _fspin(-1e9, 1e9, 1, 65535.0)
        thr.body.addLayout(S.Form().row(("slider min:", self._thr_min), ("max:", self._thr_max)))
        self._thr_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._thr_slider.setRange(0, 1000); self._thr_slider.setValue(0)
        self._thr_val = QtWidgets.QLabel("threshold = —")
        self._thr_val.setStyleSheet(f"color:{S.ACCENT};font-size:11px")
        srow = QtWidgets.QHBoxLayout(); srow.setSpacing(6)
        srow.addWidget(self._thr_slider, 1); srow.addWidget(self._thr_val)
        thr.body.addLayout(srow)
        for w in (self._thr_min, self._thr_max, self._thr_slider, self._thr_val):
            w.setEnabled(False)
        self._thr_check.toggled.connect(self._on_threshold_toggled)
        self._thr_slider.valueChanged.connect(self._on_threshold_changed)
        self._thr_min.valueChanged.connect(self._on_threshold_changed)
        self._thr_max.valueChanged.connect(self._on_threshold_changed)
        lv.addWidget(thr)

        # ── Initial seed ──
        seed = S.make_card("Initial seed  (Pick tools on image)")
        self._manual_seed_check = QtWidgets.QCheckBox("Use manual seed")
        self._manual_seed_check.setToolTip(
            "Enable BC + Lsd as the LM starting point.\n"
            "Use Pick BC / Pick Ring on the image to populate BC automatically.")
        seed.body.addWidget(self._manual_seed_check)
        self._seed_bcy = _fspin(-99999, 99999, 2, DEFAULT_BC_Y, "px")
        self._seed_bcz = _fspin(-99999, 99999, 2, DEFAULT_BC_Z, "px")
        self._seed_lsd = _fspin(1e3, 1e8, 1, DEFAULT_LSD_UM, "µm")
        for w in (self._seed_bcy, self._seed_bcz, self._seed_lsd):
            w.setEnabled(False)
        for sig in (self._seed_bcy, self._seed_bcz, self._seed_lsd):
            self._manual_seed_check.toggled.connect(sig.setEnabled)
        sfm = S.Form(); sfm.row(("BC_y:", self._seed_bcy), ("BC_z:", self._seed_bcz)); sfm.row(("Lsd:", self._seed_lsd))
        seed.body.addLayout(sfm)
        self._seed_note = QtWidgets.QLabel("")
        self._seed_note.setStyleSheet(f"color:{S.ACCENT};font-size:10px"); self._seed_note.setWordWrap(True)
        seed.body.addWidget(self._seed_note)
        lv.addWidget(seed)

        # ── Refine parameters ──
        refc = S.make_card("Refine parameters")
        rfl = QtWidgets.QGridLayout(); rfl.setSpacing(4)
        self._ref_lsd = QtWidgets.QCheckBox("Lsd"); self._ref_lsd.setChecked(True)
        self._ref_bc = QtWidgets.QCheckBox("BC"); self._ref_bc.setChecked(True)
        self._ref_ty = QtWidgets.QCheckBox("ty"); self._ref_ty.setChecked(True)
        self._ref_tz = QtWidgets.QCheckBox("tz"); self._ref_tz.setChecked(True)
        self._ref_tx = QtWidgets.QCheckBox("tx")
        self._ref_wl = QtWidgets.QCheckBox("Wavelength")
        self._ref_dist = QtWidgets.QCheckBox("Distortion (15)"); self._ref_dist.setChecked(True)
        self._build_rc = QtWidgets.QCheckBox("Residual map"); self._build_rc.setChecked(True)
        for i, w in enumerate((self._ref_lsd, self._ref_bc, self._ref_ty, self._ref_tz,
                               self._ref_tx, self._ref_wl, self._ref_dist, self._build_rc)):
            rfl.addWidget(w, i // 2, i % 2)
        refc.body.addLayout(rfl)
        lv.addWidget(refc)

        # ── Advanced ──
        grp_adv = QtWidgets.QGroupBox("Advanced")
        grp_adv.setCheckable(True); grp_adv.setChecked(False)
        av = QtWidgets.QVBoxLayout(grp_adv); av.setContentsMargins(8, 6, 8, 6); av.setSpacing(5)
        self._n_iter = _NoScrollSpinBox(); self._n_iter.setRange(1, 20); self._n_iter.setValue(4)
        self._lm_iter = _NoScrollSpinBox(); self._lm_iter.setRange(10, 2000); self._lm_iter.setValue(200)
        self._device = QtWidgets.QComboBox(); self._device.addItems(["cpu", "cuda"])
        av.addLayout(S.Form().row(("E-M iters:", self._n_iter), ("LM iters:", self._lm_iter)))
        self._out_ed = QtWidgets.QLineEdit(); self._out_ed.setPlaceholderText("Output dir…")
        bou = _br(); bou.clicked.connect(lambda: self._out_ed.setText(
            QtWidgets.QFileDialog.getExistingDirectory(self, "Output dir") or ""))
        outr = QtWidgets.QHBoxLayout(); outr.setSpacing(4); outr.addWidget(self._out_ed, 1); outr.addWidget(bou)
        av.addLayout(S.Form().row(("Device:", self._device)))
        av.addLayout(S.Form().row(("Output:", outr)))
        lv.addWidget(grp_adv)

        # ── Multi-panel ──
        grp_panel = QtWidgets.QGroupBox("Multi-panel detector")
        grp_panel.setCheckable(True); grp_panel.setChecked(False)
        grp_panel.setToolTip("Refine per-module rigid shifts for tiled detectors (px).")
        pv = QtWidgets.QVBoxLayout(grp_panel); pv.setContentsMargins(8, 6, 8, 6); pv.setSpacing(5)
        self._pn_y = _NoScrollSpinBox(); self._pn_y.setRange(1, 50); self._pn_y.setValue(3)
        self._pn_z = _NoScrollSpinBox(); self._pn_z.setRange(1, 50); self._pn_z.setValue(8)
        self._ps_y = _NoScrollSpinBox(); self._ps_y.setRange(1, 10000); self._ps_y.setValue(487)
        self._ps_z = _NoScrollSpinBox(); self._ps_z.setRange(1, 10000); self._ps_z.setValue(195)
        self._pg_y = _NoScrollSpinBox(); self._pg_y.setRange(0, 1000); self._pg_y.setValue(7)
        self._pg_z = _NoScrollSpinBox(); self._pg_z.setRange(0, 1000); self._pg_z.setValue(17)
        pf2 = S.Form()
        pf2.row(("panels Y:", self._pn_y), ("panels Z:", self._pn_z))
        pf2.row(("size Y:", self._ps_y), ("size Z:", self._ps_z))
        pf2.row(("gap Y:", self._pg_y), ("gap Z:", self._pg_z))
        pv.addLayout(pf2)
        self._panel_grp = grp_panel
        lv.addWidget(grp_panel)

        # ── Run + Save ──
        self._run_btn = S.primary_btn("Run Calibration")
        self._run_btn.clicked.connect(self._run)
        self._abort_btn = QtWidgets.QPushButton("Abort")
        self._abort_btn.setEnabled(False)
        self._abort_btn.setToolTip("Cancel: returns control immediately and discards the "
                                   "result. The running computation finishes in the background.")
        self._abort_btn.clicked.connect(self._abort)
        run_row = QtWidgets.QHBoxLayout(); run_row.setSpacing(6)
        run_row.addWidget(self._run_btn, 1); run_row.addWidget(self._abort_btn)
        lv.addLayout(run_row)
        self._prog = QtWidgets.QProgressBar(); self._prog.setRange(0, 0); self._prog.setVisible(False)
        lv.addWidget(self._prog)
        self._save_json_btn = QtWidgets.QPushButton("Save .json"); self._save_json_btn.setEnabled(False)
        self._save_json_btn.clicked.connect(self._save_json)
        self._save_ps_btn = QtWidgets.QPushButton("Save paramstest.txt"); self._save_ps_btn.setEnabled(False)
        self._save_ps_btn.clicked.connect(self._save_paramstest)
        lv.addLayout(S.button_grid([self._save_json_btn, self._save_ps_btn], 2))

        lv.addStretch(1)
        root.addWidget(scroll)

        # Right: image + bottom tabs
        right = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self._img_view = PickableImageViewer()
        self._img_view.bcPicked.connect(self._on_bc_picked)
        self._img_view.ringFitBC.connect(self._on_ring_fit_bc)
        tb = self._img_view._toolbar_layout
        self._show_rings_check = QtWidgets.QCheckBox("Show rings"); self._show_rings_check.setChecked(True)
        self._show_rings_check.toggled.connect(self._on_show_rings_toggled)
        tb.addWidget(self._show_rings_check)
        self._corrected_check = QtWidgets.QCheckBox("Corrected")
        self._corrected_check.setToolTip("Draw rings using the full forward model (tilts + distortion, cyan).")
        self._corrected_check.toggled.connect(self._on_corrected_rings_toggled)
        tb.addWidget(self._corrected_check)
        self._corr_status = QtWidgets.QLabel("")
        self._corr_status.setStyleSheet(f"color:{S.ACCENT};font-size:10px")
        tb.addWidget(self._corr_status)
        right.addWidget(self._img_view)

        bot = QtWidgets.QTabWidget(); bot.setMaximumHeight(310)
        self._prof_view = ProfileViewer()
        ptb = self._prof_view._toolbar_layout
        self._cal_r_bin = _fspin(0.1, 20.0, 2, 1.0, "px"); self._cal_r_bin.setFixedWidth(78)
        self._cal_eta_bin = _fspin(0.5, 30.0, 1, 5.0, "°"); self._cal_eta_bin.setFixedWidth(64)
        reint_btn = QtWidgets.QPushButton("Re-integrate"); reint_btn.clicked.connect(self._reintegrate)
        ptb.insertWidget(3, reint_btn)
        ptb.insertWidget(3, self._cal_eta_bin)
        ptb.insertWidget(3, QtWidgets.QLabel("η:"))
        ptb.insertWidget(3, self._cal_r_bin)
        ptb.insertWidget(3, QtWidgets.QLabel("  R bin:"))
        bot.addTab(self._prof_view, "Radial Profile")
        self._resid_chart = ResidualBarChart()
        bot.addTab(self._resid_chart, "Ring Residuals")
        # Results tab (relocated off the left panel): geometry readout + distortion table
        res_w = QtWidgets.QWidget(); rl = QtWidgets.QVBoxLayout(res_w); rl.setContentsMargins(8, 6, 8, 6)

        def _rlbl():
            l = QtWidgets.QLabel("—"); l.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse); return l
        self._r_lsd = _rlbl(); self._r_bc = _rlbl(); self._r_strain = _rlbl(); self._r_time = _rlbl()
        rfrm = S.Form()
        rfrm.row(("Lsd:", self._r_lsd), ("Strain:", self._r_strain))
        rfrm.row(("BC:", self._r_bc), ("Time:", self._r_time))
        rl.addLayout(rfrm)
        rl.addWidget(QtWidgets.QLabel("Distortion coefficients:"))
        self._dist_table = DistortionTable()
        rl.addWidget(self._dist_table)
        bot.addTab(res_w, "Results")
        self._log = LogPanel()
        bot.addTab(self._log, "Log")
        right.addWidget(bot)
        right.setStretchFactor(0, 3); right.setStretchFactor(1, 1)
        self._bot_tabs = bot
        root.addWidget(right, stretch=1)

    # ── File loaders ──────────────────────────────────────────────

    def _browse_img(self):
        p = _browse(self, "Open Calibrant Image",
                    "Images (*.tif *.tiff *.h5 *.hdf5 *.ge*);;All (*)")
        if p: self._img_ed.setText(p); self._load_img()

    def _browse_mask_file(self):
        p = _browse(self, "Open Mask", "TIFF (*.tif *.tiff);;All (*)")
        if p: self._mask_file_ed.setText(p); self._load_mask_file()

    def _load_img(self):
        path = self._img_ed.text().strip()
        if not path or not Path(path).exists():
            QtWidgets.QMessageBox.warning(self, "Error", "Image file not found."); return
        try:
            data_loc = self._img_h5_ed.text().strip() or "exchange/data"
            self._image = _load_image(path, data_loc=data_loc, frame=self._frame_spin.value())
            # Initialise the threshold slider range from the loaded image
            lo, hi = float(np.nanmin(self._image)), float(np.nanmax(self._image))
            for w in (self._thr_min, self._thr_max, self._thr_slider):
                w.blockSignals(True)
            self._thr_min.setValue(max(0.0, lo)); self._thr_max.setValue(hi)
            self._thr_slider.setValue(0)   # bottom = no pixels removed
            for w in (self._thr_min, self._thr_max, self._thr_slider):
                w.blockSignals(False)
            self._update_threshold_label()
            self._show_calib_image(autorange=True)
            self._log.append(f"Image loaded: {Path(path).name}  {self._image.shape}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Load error", str(e))

    # ── Threshold (calibration image only) ────────────────────────

    def _threshold_value(self) -> float:
        lo, hi = self._thr_min.value(), self._thr_max.value()
        if hi <= lo:
            return hi
        return lo + (self._thr_slider.value() / 1000.0) * (hi - lo)

    def _update_threshold_label(self):
        self._thr_val.setText(f"< {self._threshold_value():.4g} → 0")

    def _calib_image(self):
        """Image fed to the calibration pipeline: thresholded copy if enabled."""
        if self._image is None:
            return None
        if self._thr_check.isChecked():
            thr = self._threshold_value()
            out = self._image.copy()
            out[self._image < thr] = 0.0
            return out
        return self._image

    def _show_calib_image(self, autorange: bool = False):
        if self._image is not None:
            self._img_view.set_image(self._calib_image(), autorange=autorange)

    def _on_threshold_toggled(self, on: bool):
        for w in (self._thr_min, self._thr_max, self._thr_slider, self._thr_val):
            w.setEnabled(on)
        self._update_threshold_label()
        self._show_calib_image(autorange=False)

    def _on_threshold_changed(self, *_):
        self._update_threshold_label()
        if self._thr_check.isChecked():
            self._show_calib_image(autorange=False)

    def _load_mask_file(self):
        path = self._mask_file_ed.text().strip()
        if not path or not Path(path).exists(): return
        try:
            import tifffile
            self._mask = (tifffile.imread(path) != 0).astype(np.uint8)
            self._mask_lbl.setText(f"File: {int(self._mask.sum()):,} bad px")
            self._log.append(f"Mask loaded: {Path(path).name}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Load error", str(e))

    # ── Seed from picks ───────────────────────────────────────────

    def _on_bc_picked(self, bc_y, bc_z):
        self._manual_seed_check.setChecked(True)
        self._seed_bcy.setValue(bc_y); self._seed_bcz.setValue(bc_z)
        self._seed_note.setText("BC set from click — also set Lsd before running.")
        self._log.append(f"BC set by click: ({bc_y:.2f}, {bc_z:.2f}) px — manual seed enabled")

    def _on_ring_fit_bc(self, bc_y, bc_z, r_px):
        self._manual_seed_check.setChecked(True)
        self._seed_bcy.setValue(bc_y); self._seed_bcz.setValue(bc_z)
        self._seed_note.setText(f"BC from ring fit (R={r_px:.1f} px). Set Lsd before running.")
        self._log.append(
            f"Ring fit: BC=({bc_y:.2f}, {bc_z:.2f}) px  R={r_px:.1f} px — manual seed enabled")

    # ── Run ────────────────────────────────────────────────────────

    def _refine_flags(self) -> dict:
        return {
            "Lsd": self._ref_lsd.isChecked(),
            "BC": self._ref_bc.isChecked(),
            "ty": self._ref_ty.isChecked(),
            "tz": self._ref_tz.isChecked(),
            "tx": self._ref_tx.isChecked(),
            "Wavelength": self._ref_wl.isChecked(),
            "Distortion": self._ref_dist.isChecked(),
        }

    def _run(self):
        if self._image is None:
            QtWidgets.QMessageBox.warning(self, "No image", "Load a calibrant image first."); return
        if self._worker and self._worker.isRunning():
            return
        self._orphans = [o for o in self._orphans if o.isRunning()]   # drop finished ones
        # Dark / bright / background fields
        for sel in (self._dark_sel, self._bright_sel, self._bg_sel):
            if sel.has_pending():
                QtWidgets.QMessageBox.warning(
                    self, "Field not computed",
                    f"'{sel.title()}' is enabled but not computed. "
                    "Click 'Compute field' in that box first."); return
        self._dark = self._dark_sel.get_field()
        bright = self._bright_sel.get_field()
        background = self._bg_sel.get_field()
        bright_mode = self._bright_sel.get_mode()

        mode = self._pipeline.currentData()
        self._calib_cancelled = False
        self._run_btn.setEnabled(False); self._abort_btn.setEnabled(True)
        self._prog.setVisible(True)
        self._bot_tabs.setCurrentWidget(self._log)
        self._log.append("─" * 40 + f"\nStarting calibration ({mode})…")

        trans = []
        if self._flip_y.isChecked(): trans.append(1)
        if self._flip_z.isChecked(): trans.append(2)
        if self._transp.isChecked(): trans.append(3)

        cfg = {
            "wavelength": self._wl.value(),
            "pxY": self._pxY.value(),
            "pxZ": self._pxZ_spin.value() if self._pxZ_check.isChecked() else None,
            "calibrant": self._cal.currentText(),
            "refine": self._refine_flags(),
            "n_iter": self._n_iter.value(),
            "lm_max_iter": self._lm_iter.value(),
            "device": self._device.currentText(),
            "build_residual_corr": self._build_rc.isChecked(),
            "output_dir": self._out_ed.text().strip() or None,
            "im_trans": trans,
            "mask": self._mask if self._use_mask_check.isChecked() else None,
        }
        if self._manual_seed_check.isChecked():
            cfg["manual_seed"] = {
                "BC_y": self._seed_bcy.value(),
                "BC_z": self._seed_bcz.value(),
                "Lsd":  self._seed_lsd.value(),
            }
        if self._panel_grp.isChecked():
            cfg["panel_layout"] = {
                "n_y": self._pn_y.value(), "n_z": self._pn_z.value(),
                "sy": self._ps_y.value(), "sz": self._ps_z.value(),
                "gap_y": self._pg_y.value(), "gap_z": self._pg_z.value(),
            }

        self._worker = CalibrationWorker(
            mode, self._calib_image(), self._dark, cfg, parent=self,
            bright=bright, background=background, bright_mode=bright_mode)
        self._worker.log_line.connect(self._log.append)
        self._worker.finished.connect(self._on_done)
        self._worker.failed.connect(self._on_fail)
        self._worker.start()

    def _abort(self):
        """Abort the running calibration and free the slot immediately.

        The pipeline is one uninterruptible library call: ``terminate()`` only takes
        effect when that call next yields, which can be a while.  So rather than
        block on it (which would leave the worker "running" and prevent a new run),
        we detach its signals, request termination, orphan the thread (kept alive so
        the QThread object isn't GC'd while its C thread winds down), and clear
        ``self._worker`` so the user can start a fresh run right away."""
        import sys
        w = self._worker
        if not (w and w.isRunning()):
            return
        self._calib_cancelled = True
        for sig in (w.log_line, w.finished, w.failed):
            try:
                sig.disconnect()
            except Exception:
                pass
        w.requestInterruption()
        w.terminate()                 # best-effort; may only take effect later
        self._orphans.append(w)
        self._worker = None           # free the slot so _run can start again now
        # The worker redirected sys.stdout/err and its finally never ran — restore them.
        sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
        self._run_btn.setEnabled(True)
        self._abort_btn.setEnabled(False); self._abort_btn.setText("Abort")
        self._prog.setVisible(False)
        self._log.append("Calibration aborted — you can start a new run now "
                         "(a background thread may still be winding down).")

    def _on_done(self, result):
        if self._calib_cancelled:
            return   # user aborted — ignore the late result
        self._result = result
        self._run_btn.setEnabled(True); self._abort_btn.setEnabled(False)
        self._prog.setVisible(False)
        self._r_lsd.setText(f"{result.Lsd/1000:.4f} mm")
        self._r_bc.setText(f"({result.BC_y:.2f}, {result.BC_z:.2f}) px")
        s = result.post_residual_strain_uE
        self._r_strain.setText(f"{s:.1f} µε" if s else "n/a")
        seed_s = getattr(result, "seed_seconds", 0.0) or 0.0
        ref_s  = getattr(result, "refine_seconds", 0.0) or 0.0
        self._r_time.setText(f"seed={seed_s:.1f}s  refine={ref_s:.1f}s")
        self._dist_table.set_distortion(result.distortion or {})
        self._save_json_btn.setEnabled(True)
        self._save_ps_btn.setEnabled(True)
        self._log.append(f"Done — Lsd={result.Lsd/1000:.3f} mm"
                         + (f"  strain={s:.0f} µε" if s else ""))
        # Bayesian: report per-parameter σ if present
        lap = getattr(result, "_laplace_sigma", None)
        if lap:
            self._log.append("Laplace 1σ per parameter:")
            for name, sigma in lap.items():
                self._log.append(f"    {name:12s} ± {sigma:.4g}")
        self._draw_rings(result)
        self._bot_tabs.setCurrentWidget(self._prof_view)
        self._run_integration(result)
        self.calibrationDone.emit(result)

    def _on_fail(self, msg):
        if self._calib_cancelled:
            return   # user aborted — ignore the late failure
        self._run_btn.setEnabled(True); self._abort_btn.setEnabled(False)
        self._prog.setVisible(False)
        self._log.append(f"\nERROR:\n{msg[:600]}")
        QtWidgets.QMessageBox.critical(self, "Calibration failed", msg[:400])

    # ── Rings ──────────────────────────────────────────────────────

    def _draw_rings(self, result):
        self._calib_result = result
        for item in self._ring_items:
            self._img_view._iv.removeItem(item)
        self._ring_items.clear()
        self._clear_corrected_rings()
        radii = _predict_ring_radii(result)
        visible = self._show_rings_check.isChecked()
        th = np.linspace(0, 2 * math.pi, 512)
        pen = pg.mkPen("lime", width=1.2)
        max_r = max(result.NrPixelsY, result.NrPixelsZ)
        for r in radii:
            if 0 < r < max_r:
                item = pg.PlotDataItem(result.BC_y + r * np.cos(th),
                                       result.BC_z + r * np.sin(th), pen=pen)
                item.setVisible(visible)
                self._img_view._iv.addItem(item); self._ring_items.append(item)
        bc = pg.ScatterPlotItem([result.BC_y], [result.BC_z], symbol="o", size=10,
                                pen=pg.mkPen("yellow", width=2), brush=pg.mkBrush("red"))
        bc.setVisible(visible)
        self._img_view._iv.addItem(bc); self._ring_items.append(bc)
        if self._corrected_check.isChecked():
            self._start_corrected_rings(radii)

    def _on_show_rings_toggled(self, visible):
        active = (self._corrected_ring_items
                  if (self._corrected_check.isChecked() and self._corrected_ring_items)
                  else self._ring_items)
        for item in active:
            item.setVisible(visible)

    def _on_corrected_rings_toggled(self, checked):
        if self._calib_result is None:
            return
        if checked:
            for item in self._ring_items:
                item.setVisible(False)
            if self._corrected_ring_items:
                vis = self._show_rings_check.isChecked()
                for item in self._corrected_ring_items:
                    item.setVisible(vis)
            else:
                self._start_corrected_rings(_predict_ring_radii(self._calib_result))
        else:
            for item in self._corrected_ring_items:
                item.setVisible(False)
            vis = self._show_rings_check.isChecked()
            for item in self._ring_items:
                item.setVisible(vis)
            self._corr_status.setText("")

    def _clear_corrected_rings(self):
        for item in self._corrected_ring_items:
            self._img_view._iv.removeItem(item)
        self._corrected_ring_items.clear()

    def _start_corrected_rings(self, radii_px):
        if self._corrected_rings_worker and self._corrected_rings_worker.isRunning():
            return
        self._corr_status.setText("Computing corrected rings…")
        self._corrected_check.setEnabled(False)
        self._corrected_rings_worker = CorrectedRingsWorker(
            self._calib_result, radii_px, parent=self)
        self._corrected_rings_worker.finished.connect(self._on_corrected_rings_done)
        self._corrected_rings_worker.failed.connect(self._on_corrected_rings_failed)
        self._corrected_rings_worker.start()

    def _on_corrected_rings_done(self, ring_data):
        self._corrected_check.setEnabled(True)
        self._clear_corrected_rings()
        vis = self._show_rings_check.isChecked()
        for pts in ring_data:
            if pts is None:
                continue
            xs, ys = pts
            item = pg.ScatterPlotItem(xs, ys, symbol="o", size=2,
                                      pen=pg.mkPen(None), brush=pg.mkBrush("#00cfff"))
            item.setVisible(vis)
            self._img_view._iv.addItem(item); self._corrected_ring_items.append(item)
        n = sum(1 for r in ring_data if r is not None)
        self._corr_status.setText(f"Corrected rings: {n} shown (cyan)")
        for item in self._ring_items:
            item.setVisible(False)

    def _on_corrected_rings_failed(self, msg):
        self._corrected_check.setEnabled(True); self._corrected_check.setChecked(False)
        self._corr_status.setText("Failed — see log")
        self._log.append(f"Corrected rings error:\n{msg[:300]}")

    # ── Integration / residual chart ───────────────────────────────

    def _run_integration(self, result):
        if self._int_worker and self._int_worker.isRunning():
            return
        im_trans = tuple(t for flag, t in [
            (self._flip_y.isChecked(), 1), (self._flip_z.isChecked(), 2),
            (self._transp.isChecked(), 3)] if flag)
        self._int_worker = IntegrationWorker(
            result, self._calib_image(), self._dark, im_trans,
            r_bin=self._cal_r_bin.value(), eta_bin=self._cal_eta_bin.value(),
            mask=self._mask if self._use_mask_check.isChecked() else None, parent=self,
            bright=self._bright_sel.get_field(), background=self._bg_sel.get_field(),
            bright_mode=self._bright_sel.get_mode())
        self._int_worker.log_line.connect(self._log.append)
        self._int_worker.finished.connect(self._on_int_done)
        self._int_worker.failed.connect(
            lambda m: self._log.append(f"Integration error: {m[:200]}"))
        self._int_worker.start()

    def _reintegrate(self):
        if self._result is not None:
            self._run_integration(self._result)

    def _on_int_done(self, data):
        self._prof_view.set_profile(
            data["r_axis_px"], data["profile"],
            wavelength_A=data["wavelength_A"], lsd_um=data["lsd_um"], px_um=data["px_um"])
        if self._result:
            radii = _predict_ring_radii(self._result)
            self._prof_view.set_ring_markers(
                radii, data["lsd_um"], data["px_um"], data["wavelength_A"])
            self._resid_chart.set_data(data["r_axis_px"], data["profile"], radii)

    # ── Save ───────────────────────────────────────────────────────

    def _save_json(self):
        if not self._result: return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save calibration.json", "calibration.json", "JSON (*.json)")
        if not path: return
        import json
        d = {k: v for k, v in vars(self._result).items()
             if not k.startswith("_") and not hasattr(v, "numpy")}
        d.pop("residual_corr_map", None); d.pop("iter_history", None)
        Path(path).write_text(json.dumps(d, indent=2, default=str))
        self._log.append(f"Saved: {path}")
        QtWidgets.QMessageBox.information(self, "Saved", f"calibration.json saved:\n{path}")

    def _save_paramstest(self):
        if not self._result:
            return
        dlg = _SaveParamstestDialog(self)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        out_path = dlg.out_path()
        if not out_path:
            QtWidgets.QMessageBox.warning(self, "No output", "Please specify an output file."); return
        tmpl_path = dlg.template_path()
        panel_u = getattr(self._result, "_panel_unpacked", None)
        ps_path = Path(out_path).parent / "panel_shifts.txt" if panel_u else None
        try:
            if tmpl_path:
                if not Path(tmpl_path).exists():
                    raise FileNotFoundError(f"Template not found: {tmpl_path}")
                from midas_calibrate_v2.compat.to_v1 import ff_paramstest_from_auto_result
                ff_paramstest_from_auto_result(self._result, tmpl_path, out_path)
                # Append PanelShiftsFile so downstream tools find the companion file
                if ps_path:
                    with open(out_path, "a") as _f:
                        _f.write(f"PanelShiftsFile {ps_path}\n")
                mode = "from template"
            else:
                from midas_calibrate.params import CalibrationParams
                result = self._result
                cal_name = getattr(result, "_calibrant_name", "CeO2")
                _V2V1 = {
                    "iso_R2": "p2", "iso_R4": "p5", "iso_R6": "p4",
                    "a1": "p7", "phi1": "p8", "a2": "p0", "phi2": "p6",
                    "a3": "p9", "phi3": "p10", "a4": "p1", "phi4": "p3",
                    "a5": "p11", "phi5": "p12", "a6": "p13", "phi6": "p14",
                }
                NY, NZ = result.NrPixelsY, result.NrPixelsZ
                pxY = float(result.pxY)
                pxZ = float(result.pxZ) if result.pxZ else pxY
                RhoD = math.sqrt(max(result.BC_y, NY - result.BC_y) ** 2 +
                                 max(result.BC_z, NZ - result.BC_z) ** 2)
                p = CalibrationParams(
                    NrPixelsY=NY, NrPixelsZ=NZ, pxY=pxY, pxZ=pxZ,
                    Lsd=result.Lsd, BC_y=result.BC_y, BC_z=result.BC_z,
                    tx=result.tx, ty=result.ty, tz=result.tz,
                    Wavelength=result.wavelength_A,
                    SpaceGroup=_SG.get(cal_name, 225),
                    LatticeConstant=_LC.get(cal_name, _LC["CeO2"]),
                    RhoD=RhoD, MaxRingRad=RhoD * 0.97)
                for v2n, v1n in _V2V1.items():
                    val = (result.distortion or {}).get(v2n)
                    if val is not None:
                        setattr(p, v1n, float(val))
                rcm = getattr(result, "residual_corr_bin_path", None)
                if rcm and getattr(result, "residual_corr_map", None) is not None:
                    p.extra["ResidualCorrectionMap"] = rcm
                if ps_path:
                    p.extra["PanelShiftsFile"] = str(ps_path)
                p.write(out_path)
                mode = "standalone"
            self._log.append(f"paramstest.txt saved ({mode}): {out_path}")
            # Save companion panel_shifts.txt if calibration refined panel shifts
            ps_saved = ""
            if panel_u and ps_path:
                try:
                    from midas_calibrate_v2.compat.to_v1 import write_panel_shifts_file
                    write_panel_shifts_file(panel_u, ps_path)
                    self._log.append(f"Panel shifts saved: {ps_path}")
                    ps_saved = f"\npanel_shifts.txt: {ps_path}"
                except Exception:
                    import traceback
                    self._log.append(f"Panel shifts save error:\n{traceback.format_exc()[:300]}")
            QtWidgets.QMessageBox.information(
                self, "Saved",
                f"paramstest.txt saved ({mode}):\n{out_path}{ps_saved}")
        except Exception as e:
            import traceback
            self._log.append(f"Save paramstest error:\n{traceback.format_exc()[:400]}")
            QtWidgets.QMessageBox.critical(self, "Save failed", str(e))

    def get_result(self):
        return self._result
