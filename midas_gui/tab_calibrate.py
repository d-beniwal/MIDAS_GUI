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
    CALIBRANTS, PIPELINES, DEFAULT_PIPELINE, _SG, _LC, DEFAULT_WAVELENGTH, DEFAULT_PIXEL_UM,
    DEFAULT_LSD_UM, DEFAULT_BC_Y, DEFAULT_BC_Z, DEFAULT_CALIBRANT_TIF)
from midas_gui.helpers import (
    _fspin, _NoScrollSpinBox, _predict_ring_radii, _NoScrollComboBox,
    make_kedge_label, make_pixel_label)
from midas_gui.widgets import (
    PickableImageViewer, ProfileViewer, LogPanel, ResidualBarChart, DistortionTable,
    DataLoaderPanel)
from midas_gui.workers import CalibrationWorker, IntegrationWorker, CorrectedRingsWorker
from midas_gui.dialogs import _SaveParamstestDialog
from midas_gui import style as S


class CalibrationTab(QtWidgets.QWidget):
    calibrationDone = QtCore.pyqtSignal(object)   # AutoCalibrationResult
    pullGeometry = QtCore.pyqtSignal()            # request geometry from Data Viewer
    sendGeometryToViewer = QtCore.pyqtSignal(dict)  # push calibrated geometry → Data Viewer

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
        self._loader.set_path(DEFAULT_CALIBRANT_TIF)

    def set_mask_from_tab1(self, mask: Optional[np.ndarray]):
        self._loader.set_tab1_mask(mask)

    # ── UI ────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6); root.setSpacing(0)
        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        split.setChildrenCollapsible(False); split.setHandleWidth(6)
        root.addWidget(split); self._hsplit = split

        # ── LEFT: data loader ──
        self._loader = DataLoaderPanel(mode="single")
        self._loader.setMinimumWidth(200)
        self._loader.dataChanged.connect(self._on_loader_data)
        split.addWidget(self._loader)

        # ── MIDDLE: parameters ──
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True); scroll.setMinimumWidth(260)
        inner = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(inner); lv.setContentsMargins(2, 2, 2, 2); lv.setSpacing(8)
        scroll.setWidget(inner)

        def _br(w=30):
            b = QtWidgets.QPushButton("…"); b.setFixedWidth(w); return b

        # ── Pipeline ──
        pipe = S.make_card("Pipeline")
        self._pipeline = _NoScrollComboBox()
        for label, key, enabled in PIPELINES:
            self._pipeline.addItem(label, key)
            if not enabled:
                self._pipeline.model().item(self._pipeline.count() - 1).setEnabled(False)
        _pi = self._pipeline.findData(DEFAULT_PIPELINE)
        if _pi >= 0 and self._pipeline.model().item(_pi).isEnabled():
            self._pipeline.setCurrentIndex(_pi)
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

        # ── Detector & Calibrant ──
        det = S.make_card("Detector & Calibrant")
        self._load_calib_btn = QtWidgets.QPushButton("Load calibration file…")
        self._load_calib_btn.setToolTip(
            "Load geometry from a MIDAS paramstest (.txt), a calibration .json, "
            "or a pyFAI .poni — sets λ, pixel size, and the seed BC + Lsd.")
        self._load_calib_btn.clicked.connect(self._load_calib_file)
        self._from_view_btn = QtWidgets.QPushButton("← Data Viewer")
        self._from_view_btn.setToolTip(
            "Pull λ, pixel size, Lsd and beam centre from the Data Viewer tab "
            "into the detector + seed fields here.")
        self._from_view_btn.clicked.connect(self.pullGeometry.emit)
        _lrow = QtWidgets.QHBoxLayout(); _lrow.setSpacing(4)
        _lrow.addWidget(self._load_calib_btn); _lrow.addWidget(self._from_view_btn)
        _lrow.addStretch(1)
        det.body.addLayout(_lrow)
        self._wl = _fspin(0.001, 10.0, 5, DEFAULT_WAVELENGTH, "Å")
        self._cal = _NoScrollComboBox(); self._cal.addItems(CALIBRANTS); self._cal.setMaximumWidth(150)
        det.body.addLayout(S.Form().row(
            (make_kedge_label(self._wl, "λ:"), self._wl), ("Calibrant:", self._cal)))
        self._pxY = _fspin(1.0, 5000.0, 2, DEFAULT_PIXEL_UM, "µm")
        self._pxZ_check = QtWidgets.QCheckBox("pxZ")
        self._pxZ_spin = _fspin(1.0, 5000.0, 2, DEFAULT_PIXEL_UM, "µm"); self._pxZ_spin.setEnabled(False)
        self._pxZ_check.toggled.connect(self._pxZ_spin.setEnabled)
        prow = QtWidgets.QHBoxLayout(); prow.setSpacing(4)
        prow.addWidget(self._pxY, 1); prow.addWidget(self._pxZ_check); prow.addWidget(self._pxZ_spin, 1)
        det.body.addLayout(S.Form().row(
            (make_pixel_label(self._pxY, "Pixel:", also=self._pxZ_spin), prow)))
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
        # Lsd shown/entered in mm; calculations & files use µm.
        self._seed_lsd = _fspin(0.001, 1e5, 4, DEFAULT_LSD_UM / 1000.0, " mm")
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
        self._device = _NoScrollComboBox(); self._device.addItems(["cpu", "cuda"])
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
        split.addWidget(scroll)

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
        self._cal_azim = _NoScrollComboBox()
        self._cal_azim.addItem("Pixel-weighted", True)
        self._cal_azim.addItem("η-bin mean", False)
        self._cal_azim.setToolTip(
            "1-D profile from the (η, R) cake: pixel-weighted mean (robust to partial\n"
            "azimuthal coverage / off-detector beam centre) vs unweighted η-bin mean.")
        reint_btn = QtWidgets.QPushButton("Re-integrate"); reint_btn.clicked.connect(self._reintegrate)
        ptb.insertWidget(3, reint_btn)
        ptb.insertWidget(3, self._cal_azim)
        ptb.insertWidget(3, self._cal_eta_bin)
        ptb.insertWidget(3, QtWidgets.QLabel("η:"))
        ptb.insertWidget(3, self._cal_r_bin)
        ptb.insertWidget(3, QtWidgets.QLabel("  R bin:"))
        bot.addTab(self._prof_view, "Radial Profile")
        self._resid_chart = ResidualBarChart()
        bot.addTab(self._resid_chart, "Ring Residuals")
        # Results tab: the full parameter set exactly as written to paramstest.txt,
        # laid out across several columns (the panel is wide but short), plus a
        # named-distortion table and a button to push the geometry to the Data Viewer.
        res_w = QtWidgets.QWidget(); rl = QtWidgets.QVBoxLayout(res_w)
        rl.setContentsMargins(8, 6, 8, 6); rl.setSpacing(6)
        hdr = QtWidgets.QHBoxLayout()
        hdr.addWidget(QtWidgets.QLabel("<b>Calibration parameters</b> "
                                       "(as written to <code>paramstest.txt</code>)"))
        hdr.addStretch(1)
        self._to_view_btn = QtWidgets.QPushButton("→ Send to Data Viewer")
        self._to_view_btn.setEnabled(False)
        self._to_view_btn.setToolTip(
            "Replace the Data Viewer tab's geometry fields (λ, pixel size, Lsd, beam "
            "centre) with these calibrated values.")
        self._to_view_btn.clicked.connect(self._send_to_viewer)
        hdr.addWidget(self._to_view_btn)
        rl.addLayout(hdr)

        self._param_grid = QtWidgets.QGridLayout()
        self._param_grid.setHorizontalSpacing(18); self._param_grid.setVerticalSpacing(3)
        _pg_host = QtWidgets.QWidget(); _pg_host.setLayout(self._param_grid)
        rl.addWidget(_pg_host)

        self._r_diag = QtWidgets.QLabel("Run a calibration to see the parameters.")
        self._r_diag.setStyleSheet(f"color:{S.MUTED};font-size:11px")
        self._r_diag.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        rl.addWidget(self._r_diag)

        rl.addWidget(S.hline())
        rl.addWidget(QtWidgets.QLabel("Distortion coefficients (named):"))
        self._dist_table = DistortionTable()
        rl.addWidget(self._dist_table)
        rl.addStretch(1)
        bot.addTab(res_w, "Results")
        self._log = LogPanel()
        bot.addTab(self._log, "Log")
        right.addWidget(bot)
        right.setStretchFactor(0, 3); right.setStretchFactor(1, 1)
        self._bot_tabs = bot
        right.setMinimumWidth(320)
        split.addWidget(right)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 0); split.setStretchFactor(2, 1)
        split.setSizes([286, 361, 950])

    # ── Data (from the loader panel) ──────────────────────────────

    def _on_loader_data(self):
        """New frame / data from the loader — refresh the calibration image, the
        threshold-slider range, and the display."""
        self._image = self._loader.current_frame()
        if self._image is None:
            return
        lo, hi = float(np.nanmin(self._image)), float(np.nanmax(self._image))
        for w in (self._thr_min, self._thr_max, self._thr_slider):
            w.blockSignals(True)
        self._thr_min.setValue(max(0.0, lo)); self._thr_max.setValue(hi)
        self._thr_slider.setValue(0)
        for w in (self._thr_min, self._thr_max, self._thr_slider):
            w.blockSignals(False)
        self._update_threshold_label()
        self._show_calib_image(autorange=True)

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

    # ── Seed from picks ───────────────────────────────────────────

    def _load_calib_file(self):
        """Load geometry from a paramstest/.json/.poni into the seed + detector fields."""
        from midas_gui.helpers import geometry_fields_from_file
        from midas_gui.constants import DEFAULT_CALIB_FILE
        start = DEFAULT_CALIB_FILE if Path(DEFAULT_CALIB_FILE).exists() else ""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load calibration file", start,
            "Calibration (*.json *.txt *.poni);;All files (*)")
        if not path:
            return
        try:
            g = geometry_fields_from_file(path)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Load failed", str(e)); return
        self._wl.setValue(float(g["wavelength_A"]))
        self._pxY.setValue(float(g["pxY"]))
        if abs(float(g["pxZ"]) - float(g["pxY"])) > 1e-9:
            self._pxZ_check.setChecked(True); self._pxZ_spin.setValue(float(g["pxZ"]))
        else:
            self._pxZ_check.setChecked(False)
        self._manual_seed_check.setChecked(True)
        self._seed_bcy.setValue(float(g["BC_y"]))
        self._seed_bcz.setValue(float(g["BC_z"]))
        self._seed_lsd.setValue(float(g["Lsd"]) / 1000.0)   # µm → mm display
        self._seed_note.setText(
            f"Loaded {Path(path).name}: λ={g['wavelength_A']:.5f} Å, px={g['pxY']:.2f} µm, "
            f"BC=({g['BC_y']:.2f}, {g['BC_z']:.2f}), Lsd={g['Lsd']/1000:.3f} mm.")
        self._log.append(f"Calibration file loaded: {path}")

    def apply_geometry(self, g: dict):
        """Set λ / pixel size / seed BC + Lsd from a geometry dict (Data Viewer)."""
        if not g:
            return
        if g.get("wavelength_A"):
            self._wl.setValue(float(g["wavelength_A"]))
        if g.get("pxY"):
            self._pxY.setValue(float(g["pxY"]))
        self._manual_seed_check.setChecked(True)
        if g.get("BC_y") is not None:
            self._seed_bcy.setValue(float(g["BC_y"]))
        if g.get("BC_z") is not None:
            self._seed_bcz.setValue(float(g["BC_z"]))
        if g.get("Lsd"):
            self._seed_lsd.setValue(float(g["Lsd"]) / 1000.0)   # µm → mm display
        self._seed_note.setText(
            f"Geometry from Data Viewer: λ={g.get('wavelength_A', 0):.5f} Å, "
            f"px={g.get('pxY', 0):.2f} µm, "
            f"BC=({g.get('BC_y', 0):.2f}, {g.get('BC_z', 0):.2f}), "
            f"Lsd={g.get('Lsd', 0)/1000:.3f} mm.")
        self._log.append("Geometry pulled from Data Viewer tab.")

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
        self._image = self._loader.current_frame()
        if self._image is None:
            QtWidgets.QMessageBox.warning(self, "No image", "Load a calibrant image first."); return
        if self._worker and self._worker.isRunning():
            return
        self._orphans = [o for o in self._orphans if o.isRunning()]   # drop finished ones
        # Dark / bright / background fields (from the loader)
        for sel in self._loader.has_pending_fields():
            QtWidgets.QMessageBox.warning(
                self, "Field not computed",
                f"'{sel.title()}' is enabled but not computed. "
                "Click 'Compute field' in that box first."); return
        self._dark = self._loader.dark()
        bright = self._loader.bright()
        background = self._loader.background()
        bright_mode = self._loader.bright_mode()

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
            "mask": self._loader.composite_mask(),
        }
        if self._manual_seed_check.isChecked():
            cfg["manual_seed"] = {
                "BC_y": self._seed_bcy.value(),
                "BC_z": self._seed_bcz.value(),
                "Lsd":  self._seed_lsd.value() * 1000.0,   # mm display → µm
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

        The pipeline is one uninterruptible library call, so we cannot stop it
        cleanly mid-flight — and ``terminate()`` on a thread inside native
        torch/scipy code can corrupt the interpreter.  So we *detach* instead:
        disconnect the worker's signals (its result is discarded), orphan the thread
        (kept alive so its QObject isn't GC'd while the C thread winds down on its
        own), and clear ``self._worker`` so a fresh run can start right away. The
        worker restores stdout/stderr itself, guarded so it won't clobber a new run."""
        w = self._worker
        if not (w and w.isRunning()):
            return
        self._calib_cancelled = True
        for sig in (w.log_line, w.finished, w.failed):
            try:
                sig.disconnect()
            except Exception:
                pass
        w.requestInterruption()       # honoured if/when the library call yields
        self._orphans.append(w)
        self._worker = None           # free the slot so _run can start again now
        self._run_btn.setEnabled(True)
        self._abort_btn.setEnabled(False); self._abort_btn.setText("Abort")
        self._prog.setVisible(False)
        self._log.append("Calibration aborted — you can start a new run now "
                         "(a background thread may still be winding down).")

    @staticmethod
    def _paramstest_pairs(result):
        """(key, value) pairs exactly as written to paramstest.txt — generated by
        writing a temp file with the shared writer, so the readout matches the file."""
        import os, tempfile
        from pathlib import Path
        from midas_gui.helpers import write_standalone_paramstest
        fd, tmp = tempfile.mkstemp(suffix=".txt"); os.close(fd)
        try:
            write_standalone_paramstest(result, tmp)
            lines = Path(tmp).read_text().splitlines()
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        pairs = []
        for ln in lines:
            parts = ln.split()
            if parts:
                pairs.append((parts[0], " ".join(parts[1:])))
        return pairs

    def _populate_param_grid(self, pairs, ncols=3):
        """Lay (key, value) pairs into ``ncols`` columns, filled column-major so each
        column reads top-to-bottom in file order."""
        import math
        from midas_gui.widgets import _mono_font
        grid = self._param_grid
        while grid.count():                       # clear previous run
            item = grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        mono = _mono_font(10)
        n = len(pairs); nrows = max(1, math.ceil(n / ncols))
        for idx, (key, val) in enumerate(pairs):
            col, row = idx // nrows, idx % nrows
            k = QtWidgets.QLabel(f"{key}:"); k.setStyleSheet("font-weight:600;")
            v = QtWidgets.QLabel(val); v.setFont(mono)
            v.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            grid.addWidget(k, row, col * 2, QtCore.Qt.AlignRight)
            grid.addWidget(v, row, col * 2 + 1)
        grid.setColumnStretch(ncols * 2 + 1, 1)

    def _send_to_viewer(self):
        """Push the calibrated geometry to the Data Viewer (µm internal; the Viewer
        converts Lsd to its mm display)."""
        r = self._result
        if r is None:
            return
        self.sendGeometryToViewer.emit({
            "wavelength_A": float(r.wavelength_A), "pxY": float(r.pxY),
            "Lsd": float(r.Lsd), "BC_y": float(r.BC_y), "BC_z": float(r.BC_z)})
        self._log.append("Sent calibrated geometry to the Data Viewer.")

    def _on_done(self, result):
        if self._calib_cancelled:
            return   # user aborted — ignore the late result
        self._result = result
        self._run_btn.setEnabled(True); self._abort_btn.setEnabled(False)
        self._prog.setVisible(False)
        try:
            self._populate_param_grid(self._paramstest_pairs(result))
        except Exception:
            import traceback as _tb
            self._log.append("Could not render parameter grid:\n" + _tb.format_exc()[:400])
        s = result.post_residual_strain_uE
        seed_s = getattr(result, "seed_seconds", 0.0) or 0.0
        ref_s  = getattr(result, "refine_seconds", 0.0) or 0.0
        strain_txt = f"{s:.1f} µε" if s else "n/a"
        self._r_diag.setText(f"Post-refine strain: {strain_txt}    ·    "
                             f"timing: seed={seed_s:.1f} s, refine={ref_s:.1f} s")
        self._dist_table.set_distortion(result.distortion or {})
        self._to_view_btn.setEnabled(True)
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
            result, self._calib_image(), self._loader.dark(), im_trans,
            r_bin=self._cal_r_bin.value(), eta_bin=self._cal_eta_bin.value(),
            mask=self._loader.composite_mask(), parent=self,
            bright=self._loader.bright(), background=self._loader.background(),
            bright_mode=self._loader.bright_mode(),
            weighted=bool(self._cal_azim.currentData()))
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
                from midas_gui.helpers import write_standalone_paramstest
                result = self._result
                extra = {}
                rcm = getattr(result, "residual_corr_bin_path", None)
                if rcm and getattr(result, "residual_corr_map", None) is not None:
                    extra["ResidualCorrectionMap"] = rcm
                if ps_path:
                    extra["PanelShiftsFile"] = str(ps_path)
                write_standalone_paramstest(result, out_path, extra=extra)
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
