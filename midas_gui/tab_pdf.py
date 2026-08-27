"""Tab 6 — PDF Analysis (polyatomic total scattering).

I(Q) — integrated from a detector frame or loaded from a ``Q,I,σ`` file — is
reduced to a Faber-Ziman structure function S(Q) and pair-distribution G(r) via
the ``midas_pdf`` backend: real composition-weighted normalization, Compton
subtraction, end-to-end σ propagation, optional differentiable scale/background
refinement, and the G/g/T/R convention family.

Stage 2-3 workflow, all opt-in via checkboxes (off leaves the Stage-1 pipeline
byte-for-byte unchanged): empty-cell/Paalman-Pings absorption subtraction,
detector-efficiency correction, absolute (electron-unit) normalization,
differentiable multiple-scattering correction, display-only S(Q) tail
flattening, a fluorescence diagnostic, CIF-driven small-box structure
refinement (PDFfit-style, error-aware), and Δ-PDF significance testing between
two saved G(r) snapshots.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
from PyQt5 import QtCore, QtWidgets
import pyqtgraph as pg

from midas_gui.helpers import (_load_image, _fspin, _twocol, _browse, is_h5,
                               _NoScrollComboBox, make_kedge_label,
                               widgets_to_dict, apply_dict_to_widgets)
from midas_gui.constants import (DEFAULT_NICKEL_FRAME0, DEFAULT_PDF_IQ_FILE,
                                 DEFAULT_PDF_MASK, DEFAULT_PDF_EMPTY_IQ,
                                 DEFAULT_PDF_CIF)
from midas_gui.widgets import LogPanel
from midas_gui.dialogs import show_error
from midas_gui.workers import PDFWorker, PDFStructureFitWorker, _parse_composition
from midas_gui import style as S

_ATOM_COLS = ["Element", "x", "y", "z", "Occupancy", "B_iso"]


class PDFTab(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._result = None
        self._image: Optional[np.ndarray] = None
        self._mask: Optional[np.ndarray] = None
        self._worker = None
        self._fit_worker = None
        self._last = None          # last PDFWorker result dict
        self._fit_result = None    # last PDFStructureFitWorker result dict
        self._dpdf_result = None   # last Δ-PDF computation
        self._state_a = None       # saved G(r) snapshot for Δ-PDF
        self._state_b = None
        self._build_ui()
        if Path(self._img_ed.text().strip() or "x").exists():
            self._load_img()
        # Default to the ready real I(Q) file when it exists (quick PDF test).
        if Path(DEFAULT_PDF_IQ_FILE).exists():
            self._source.setCurrentIndex(self._source.findData("file"))
        if Path(DEFAULT_PDF_MASK).exists():
            self._mask_ed.setText(DEFAULT_PDF_MASK)
            self._load_mask()
        self._on_source_changed()
        self._on_refine_toggled()
        self._on_bg_mode_changed()
        self._on_bg_pp_toggled()
        self._on_ms_mu_mode_changed()
        self._on_crystal_source_changed()
        self._on_fit_bg_toggled()

    # ── cross-tab wiring ─────────────────────────────────────────────────────
    def set_calibration(self, result, source="Tab 2"):
        self._result = result
        msg = f"{source}: λ={result.wavelength_A:.5f} Å  Lsd={result.Lsd/1000:.3f} mm"
        self._src_lbl.setText(msg)
        self._calib_status.setText(msg)
        self._wl.setValue(float(result.wavelength_A))
        self._update_run_enabled()

    def _load_calib_file(self):
        """Load geometry + wavelength from a paramstest/.json/.poni file."""
        from midas_gui.helpers import result_ns_from_geometry_file
        from midas_gui.constants import DEFAULT_CALIB_FILE
        start = DEFAULT_CALIB_FILE if Path(DEFAULT_CALIB_FILE).exists() else ""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load calibration file", start,
            "Calibration (*.json *.txt *.poni);;All files (*)")
        if not path:
            return
        try:
            res = result_ns_from_geometry_file(path)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Load failed", str(e)); return
        self.set_calibration(res, source=Path(path).name)
        self._log.append(f"Calibration file loaded: {path}")

    def set_mask_from_tab1(self, mask):
        self._mask = mask

    def _effective_wavelength(self) -> float:
        if self._source.currentData() == "image" and self._result is not None:
            return float(self._result.wavelength_A)
        return self._wl.value()

    # ── GUI state (Save/Load GUI State) ─────────────────────────────
    def _state_widgets(self) -> dict:
        return {
            "source": self._source,
            "img_ed": self._img_ed,
            "img_h5_ed": self._img_h5_ed,
            "iq_ed": self._iq_ed,
            "mask_ed": self._mask_ed,
            "comp_ed": self._comp_ed,
            "rho0": self._rho0,
            "wl": self._wl,
            "compton": self._compton,
            "refine": self._refine,
            "bg_order": self._bg_order,
            "r_min_phys": self._r_min_phys,
            "steps": self._steps,
            "qmin": self._qmin, "qmax": self._qmax,
            "rmin": self._rmin, "rmax": self._rmax,
            "rstep": self._rstep,
            "window": self._window,
            "binning": self._binning,
            "out_fn": self._out_fn,
            "mid_fn": self._mid_fn,
            "tf_show_chk": self._tf_show_chk,
            # Background subtraction
            "bg_enable": self._bg_enable,
            "bg_iq_ed": self._bg_iq_ed,
            "bg_mode": self._bg_mode,
            "bg_scale": self._bg_scale,
            "bg_fit_qmin": self._bg_fit_qmin, "bg_fit_qmax": self._bg_fit_qmax,
            "bg_pp": self._bg_pp,
            "bg_mu_s": self._bg_mu_s, "bg_mu_c": self._bg_mu_c,
            "bg_r_s": self._bg_r_s, "bg_r_c": self._bg_r_c,
            "bg_container_comp": self._bg_container_comp,
            "bg_container_density": self._bg_container_density,
            # Corrections
            "deteff_enable": self._deteff_enable,
            "de_material": self._de_material,
            "de_thickness": self._de_thickness,
            "de_density": self._de_density,
            "absnorm_enable": self._absnorm_enable,
            "an_qmin": self._an_qmin, "an_qmax": self._an_qmax,
            "an_anomalous": self._an_anomalous,
            "ms_enable": self._ms_enable,
            "ms_mu_mode": self._ms_mu_mode,
            "ms_mu": self._ms_mu,
            "ms_density": self._ms_density,
            "ms_r": self._ms_r, "ms_albedo": self._ms_albedo, "ms_qmax": self._ms_qmax,
            "ms_nmu": self._ms_nmu, "ms_ntau": self._ms_ntau,
            "tf_enable": self._tf_enable,
            "tf_qmin": self._tf_qmin, "tf_qmax": self._tf_qmax,
            "tf_polydeg": self._tf_polydeg, "tf_madk": self._tf_madk, "tf_niter": self._tf_niter,
            "fluor_container_ed": self._fluor_container_ed,
            "fluor_min_yield": self._fluor_min_yield,
            # Structure fit
            "crystal_source": self._crystal_source,
            "cif_ed": self._cif_ed,
            "lat_a": self._lat_a, "lat_b": self._lat_b, "lat_c": self._lat_c,
            "lat_alpha": self._lat_alpha, "lat_beta": self._lat_beta, "lat_gamma": self._lat_gamma,
            "sg_number": self._sg_number,
            "fit_rmax": self._fit_rmax,
            "fit_r_min": self._fit_r_min, "fit_r_max": self._fit_r_max,
            "sigma_inflate": self._sigma_inflate,
            "init_a": self._init_a, "init_u_iso": self._init_u_iso, "init_scale": self._init_scale,
            "fit_bg_enable": self._fit_bg_enable, "fit_bg_order": self._fit_bg_order,
            "fit_steps": self._fit_steps, "fit_lr": self._fit_lr,
            "fit_nposterior": self._fit_nposterior,
            # Delta-PDF
            "dpdf_nsigma": self._dpdf_nsigma,
        }

    def get_state(self) -> dict:
        atoms = []
        for row in range(self._atom_table.rowCount()):
            atoms.append([
                self._atom_table.item(row, c).text() if self._atom_table.item(row, c) else ""
                for c in range(self._atom_table.columnCount())
            ])
        return {"fields": widgets_to_dict(self._state_widgets()), "atoms": atoms}

    def set_state(self, state: dict):
        fields = state.get("fields", {})
        apply_dict_to_widgets(self._state_widgets(), fields)
        atoms = state.get("atoms")
        if atoms:
            self._atom_table.setRowCount(0)
            for row_vals in atoms:
                self._add_atom_row(row_vals)
        self._on_source_changed()
        self._on_refine_toggled()
        self._on_bg_mode_changed()
        self._on_bg_pp_toggled()
        self._on_ms_mu_mode_changed()
        self._on_crystal_source_changed()
        self._on_fit_bg_toggled()
        img_path = fields.get("img_ed")
        if img_path and Path(img_path).exists():
            self._load_img()
        mask_path = fields.get("mask_ed")
        if mask_path and Path(mask_path).exists():
            self._load_mask()
        self._update_run_enabled()

    # ── UI ───────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6); root.setSpacing(8)

        left = QtWidgets.QTabWidget()
        left.setFixedWidth(560)
        left.addTab(self._build_page_data_reduction(), "Data && Reduction")
        left.addTab(self._build_page_corrections(), "Corrections")
        left.addTab(self._build_page_structure_fit(), "Structure Fit")
        left.addTab(self._build_page_delta_pdf(), "Δ-PDF")
        root.addWidget(left)

        right = QtWidgets.QTabWidget()
        right.addTab(self._build_reduction_plots(), "Reduction")
        right.addTab(self._build_structure_fit_plots(), "Structure Fit")
        right.addTab(self._build_delta_pdf_plots(), "Δ-PDF")
        self._log = LogPanel()
        right.addTab(self._log, "Log")
        self._log.setMaximumHeight(16_777_215)
        root.addWidget(right, stretch=1)

    @staticmethod
    def _scroll_page():
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(inner); lv.setSpacing(6)
        scroll.setWidget(inner)
        return scroll, lv

    @staticmethod
    def _br(w=28):
        b = QtWidgets.QPushButton("…"); b.setFixedWidth(w); return b

    def _frow(self, ed, slot):
        r = QtWidgets.QHBoxLayout(); r.setSpacing(3)
        r.addWidget(ed); b = self._br(); b.clicked.connect(slot); r.addWidget(b); return r

    # ── Page 1: Data & Reduction ────────────────────────────────────────────
    def _build_page_data_reduction(self):
        scroll, lv = self._scroll_page()

        # I(Q) source ------------------------------------------------------------
        grp_src_sel = QtWidgets.QGroupBox("I(Q) source")
        ssv = QtWidgets.QFormLayout(grp_src_sel); ssv.setSpacing(4)
        self._source = _NoScrollComboBox()
        self._source.addItem("Integrate detector frame", "image")
        self._source.addItem("Load I(Q) file (Q, I, σ)", "file")
        self._source.currentIndexChanged.connect(self._on_source_changed)
        ssv.addRow("Source:", self._source)
        self._load_calib_btn = QtWidgets.QPushButton("Load calibration file…")
        self._load_calib_btn.setToolTip(
            "Load geometry + wavelength from a MIDAS paramstest (.txt), a "
            "calibration .json, or a pyFAI .poni. Required for 'Integrate detector "
            "frame'; also sets λ used in file mode.")
        self._load_calib_btn.clicked.connect(self._load_calib_file)
        ssv.addRow("Calibration:", self._load_calib_btn)
        self._calib_status = QtWidgets.QLabel("(from Tab 2, or load a file)")
        self._calib_status.setStyleSheet("color:#aaa;font-size:10px")
        self._calib_status.setWordWrap(True)
        ssv.addRow("", self._calib_status)
        lv.addWidget(grp_src_sel)

        # Sample frame (image mode) ---------------------------------------------
        self._grp_img = QtWidgets.QGroupBox("Sample frame")
        gf = QtWidgets.QFormLayout(self._grp_img); gf.setSpacing(4)
        self._img_ed = QtWidgets.QLineEdit(DEFAULT_NICKEL_FRAME0)
        self._img_ed.setPlaceholderText("Sample frame…")
        gf.addRow("Image:", self._frow(self._img_ed, self._browse_img))
        self._img_h5_lbl = QtWidgets.QLabel("  Dataset:")
        self._img_h5_ed = QtWidgets.QLineEdit("exchange/data")
        self._img_h5_lbl.setVisible(False); self._img_h5_ed.setVisible(False)
        gf.addRow(self._img_h5_lbl, self._img_h5_ed)
        self._img_ed.textChanged.connect(lambda p: (
            self._img_h5_lbl.setVisible(is_h5(p)), self._img_h5_ed.setVisible(is_h5(p))))
        self._img_ed.returnPressed.connect(self._load_img)
        self._img_h5_ed.editingFinished.connect(
            lambda: self._image is not None and self._load_img())
        self._src_lbl = QtWidgets.QLabel("(run Tab 2 calibration first)")
        self._src_lbl.setStyleSheet("color:#aaa;font-size:10px"); self._src_lbl.setWordWrap(True)
        gf.addRow("Calib:", self._src_lbl)
        self._mask_ed = QtWidgets.QLineEdit("")
        self._mask_ed.setPlaceholderText("Optional mask .tif (nonzero = masked)…")
        self._mask_ed.returnPressed.connect(self._load_mask)
        gf.addRow("Mask:", self._frow(self._mask_ed, self._browse_mask))
        lv.addWidget(self._grp_img)

        # I(Q) file (file mode) --------------------------------------------------
        self._grp_file = QtWidgets.QGroupBox("I(Q) file")
        ff = QtWidgets.QFormLayout(self._grp_file); ff.setSpacing(4)
        self._iq_ed = QtWidgets.QLineEdit(
            DEFAULT_PDF_IQ_FILE if Path(DEFAULT_PDF_IQ_FILE).exists() else "")
        self._iq_ed.setPlaceholderText("Q,I,σ text/CSV (2- or 3-column)…")
        self._iq_ed.textChanged.connect(lambda _: self._update_run_enabled())
        ff.addRow("File:", self._frow(self._iq_ed, self._browse_iq))
        lv.addWidget(self._grp_file)

        # Sample -----------------------------------------------------------------
        grp_s = QtWidgets.QGroupBox("Sample")
        sf = QtWidgets.QFormLayout(grp_s); sf.setSpacing(4)
        self._comp_ed = QtWidgets.QLineEdit("Ni")
        self._comp_ed.setPlaceholderText("e.g. Ni  or  C:3,H:8,O:1  (ions ok: Ni2+)")
        sf.addRow("Composition:", self._comp_ed)
        self._rho0 = _fspin(0.0, 1.0, 4, 0.0913)
        self._rho0.setToolTip("Number density ρ₀ (atoms/Å³). 0 = unset "
                              "(needed for refine and g/T/R).")
        self._wl = _fspin(0.0, 5.0, 5, 0.1839)
        self._wl.setToolTip("X-ray wavelength (Å). Auto-filled from Tab 2 calibration.")
        sf.addRow(_twocol("ρ₀ (at/Å³):", self._rho0,
                          make_kedge_label(self._wl, "λ (Å):"), self._wl))
        self._compton = QtWidgets.QCheckBox("Subtract Compton (incoherent) scattering")
        self._compton.setChecked(True)
        sf.addRow(self._compton)
        lv.addWidget(grp_s)

        # Background subtraction (empty-cell) ------------------------------------
        grp_bg = QtWidgets.QGroupBox("Background subtraction (empty-cell)")
        bgf = QtWidgets.QFormLayout(grp_bg); bgf.setSpacing(4)
        self._bg_enable = QtWidgets.QCheckBox("Enable")
        bgf.addRow(self._bg_enable)
        self._bg_iq_ed = QtWidgets.QLineEdit(
            DEFAULT_PDF_EMPTY_IQ if Path(DEFAULT_PDF_EMPTY_IQ).exists() else "")
        self._bg_iq_ed.setPlaceholderText("Empty-cell / container Q,I,σ file…")
        bgf.addRow("Empty I(Q):", self._frow(self._bg_iq_ed, self._browse_bg_iq))
        self._bg_mode = _NoScrollComboBox()
        self._bg_mode.addItem("Manual scale", "manual")
        self._bg_mode.addItem("Fit high-Q", "fit")
        self._bg_mode.currentIndexChanged.connect(self._on_bg_mode_changed)
        bgf.addRow("Mode:", self._bg_mode)
        self._bg_scale = _fspin(0.0, 10.0, 4, 1.0)
        self._bg_scale.setToolTip(
            "Physical/manual transmission scale s (e.g. attenuator factor). "
            "I_corr = I − s·I_empty.")
        bgf.addRow("Scale s:", self._bg_scale)
        self._bg_fit_qmin = _fspin(0.0, 50.0, 2, 15.0)
        self._bg_fit_qmax = _fspin(0.0, 50.0, 2, 21.5)
        self._bg_fit_row = _twocol("fit Qmin:", self._bg_fit_qmin, "fit Qmax:", self._bg_fit_qmax)
        bgf.addRow(self._bg_fit_row)
        self._bg_pp = QtWidgets.QCheckBox("Paalman-Pings cylinder-in-cylinder correction")
        self._bg_pp.toggled.connect(self._on_bg_pp_toggled)
        bgf.addRow(self._bg_pp)
        self._bg_mu_s = _fspin(0.0, 10.0, 6, 0.0, "1/µm")
        self._bg_mu_c = _fspin(0.0, 10.0, 6, 0.0, "1/µm")
        self._bg_mu_row = _twocol("μ sample:", self._bg_mu_s, "μ container:", self._bg_mu_c)
        bgf.addRow(self._bg_mu_row)
        self._bg_r_s = _fspin(0.0, 5000.0, 1, 500.0, "µm")
        self._bg_r_c = _fspin(0.0, 5000.0, 1, 530.0, "µm")
        self._bg_r_row = _twocol("R sample:", self._bg_r_s, "R container:", self._bg_r_c)
        bgf.addRow(self._bg_r_row)
        self._bg_container_comp = QtWidgets.QLineEdit("C:22,H:10,N:2,O:5")
        self._bg_container_comp.setToolTip("Container composition (e.g. Kapton), for μ estimation.")
        self._bg_container_row = QtWidgets.QWidget()
        _bcr = QtWidgets.QHBoxLayout(self._bg_container_row); _bcr.setContentsMargins(0, 0, 0, 0)
        _bcr.addWidget(QtWidgets.QLabel("Container:")); _bcr.addWidget(self._bg_container_comp)
        bgf.addRow(self._bg_container_row)
        self._bg_container_density = _fspin(0.0, 30.0, 3, 1.42, "g/cm³")
        self._bg_density_row = _twocol("Container ρ:", self._bg_container_density, "", QtWidgets.QLabel(""))
        bgf.addRow(self._bg_density_row)
        self._bg_mu_btn = QtWidgets.QPushButton("Estimate μ from composition")
        self._bg_mu_btn.clicked.connect(self._estimate_mu)
        bgf.addRow(self._bg_mu_btn)
        lv.addWidget(grp_bg)

        # Normalization ----------------------------------------------------------
        grp_n = QtWidgets.QGroupBox("Normalization")
        nf = QtWidgets.QFormLayout(grp_n); nf.setSpacing(4)
        self._refine = QtWidgets.QCheckBox("Refine scale + background (needs ρ₀ > 0)")
        self._refine.toggled.connect(self._on_refine_toggled)
        nf.addRow(self._refine)
        self._bg_order = QtWidgets.QSpinBox(); self._bg_order.setRange(0, 3); self._bg_order.setValue(0)
        self._bg_order.setToolTip("Background polynomial degree b(Q)=Σcⱼ(Q/Qmax)ʲ (0 = constant).")
        self._r_min_phys = _fspin(0.0, 5.0, 2, 1.5)
        self._r_min_phys.setToolTip("Low-r cutoff (Å) below the nearest-neighbour distance, "
                                    "where G(r) = −4πρ₀r is enforced.")
        self._rmp_row = _twocol("bg order:", self._bg_order, "r_min (Å):", self._r_min_phys)
        nf.addRow(self._rmp_row)
        self._steps = QtWidgets.QSpinBox(); self._steps.setRange(20, 800); self._steps.setValue(150)
        self._steps.setToolTip("L-BFGS iterations for the normalization refinement.")
        self._steps_row = QtWidgets.QHBoxLayout()
        self._steps_row.addWidget(QtWidgets.QLabel("refine steps:")); self._steps_row.addWidget(self._steps)
        self._steps_row.addStretch(1)
        nf.addRow(self._steps_row)
        lv.addWidget(grp_n)

        # Q range ----------------------------------------------------------------
        grp_q = QtWidgets.QGroupBox("Q range (Å⁻¹)")
        qf = QtWidgets.QFormLayout(grp_q); qf.setSpacing(4)
        self._qmin = _fspin(0.0, 50.0, 3, 1.5); self._qmax = _fspin(0.0, 50.0, 3, 22.0)
        qf.addRow(_twocol("Qmin:", self._qmin, "Qmax:", self._qmax))
        lv.addWidget(grp_q)

        # r range + FT -----------------------------------------------------------
        grp_r = QtWidgets.QGroupBox("r range (Å) + FT")
        rf = QtWidgets.QFormLayout(grp_r); rf.setSpacing(4)
        self._rmin = _fspin(0.0, 50.0, 2, 0.5); self._rmax = _fspin(1.0, 100.0, 2, 20.0)
        rf.addRow(_twocol("rmin:", self._rmin, "rmax:", self._rmax))
        self._rstep = _fspin(0.001, 1.0, 3, 0.02)
        rf.addRow("Δr:", self._rstep)
        self._window = _NoScrollComboBox(); self._window.addItems(["lorch", "none"])
        self._binning = _NoScrollComboBox(); self._binning.addItems(["hard", "polygon"])
        rf.addRow(_twocol("window:", self._window, "binning:", self._binning))
        lv.addWidget(grp_r)

        # Output -----------------------------------------------------------------
        grp_o = QtWidgets.QGroupBox("Output")
        of = QtWidgets.QFormLayout(grp_o); of.setSpacing(4)
        self._out_fn = _NoScrollComboBox()
        self._out_fn.addItem("G(r) — reduced PDF", "G")
        self._out_fn.addItem("g(r) — pair distribution", "g")
        self._out_fn.addItem("T(r) — total correlation", "T")
        self._out_fn.addItem("R(r) — radial distribution", "R")
        self._mid_fn = _NoScrollComboBox()
        self._mid_fn.addItem("S(Q)", "S"); self._mid_fn.addItem("F(Q) = Q(S−1)", "F")
        self._mid_fn.currentIndexChanged.connect(self._redraw_mid)
        self._out_fn.currentIndexChanged.connect(self._redraw_bottom)
        of.addRow(_twocol("bottom:", self._out_fn, "middle:", self._mid_fn))
        lv.addWidget(grp_o)

        # Buttons ----------------------------------------------------------------
        self._run_btn = S.primary_btn("Compute G(r)"); self._run_btn.setEnabled(False)
        self._run_btn.clicked.connect(self._run)
        lv.addWidget(self._run_btn)
        brow = QtWidgets.QHBoxLayout()
        self._save_gr = QtWidgets.QPushButton("Save G(r)…"); self._save_gr.setEnabled(False)
        self._save_gr.clicked.connect(self._save_gr_file)
        self._save_sq = QtWidgets.QPushButton("Save S(Q)…"); self._save_sq.setEnabled(False)
        self._save_sq.clicked.connect(self._save_sq_file)
        brow.addWidget(self._save_gr); brow.addWidget(self._save_sq)
        lv.addLayout(brow)
        lv.addStretch(1)
        return scroll

    # ── Page 2: Corrections ─────────────────────────────────────────────────
    def _build_page_corrections(self):
        scroll, lv = self._scroll_page()

        grp_de = QtWidgets.QGroupBox("Detector efficiency")
        def_f = QtWidgets.QFormLayout(grp_de); def_f.setSpacing(4)
        self._deteff_enable = QtWidgets.QCheckBox("Enable")
        def_f.addRow(self._deteff_enable)
        self._de_material = QtWidgets.QLineEdit("Si")
        self._de_material.setToolTip("Detector sensor element, e.g. Si, Ge, CdTe.")
        def_f.addRow("Material:", self._de_material)
        self._de_thickness = _fspin(0.0, 10000.0, 1, 500.0, "µm")
        self._de_density = _fspin(0.0, 30.0, 3, 0.0, "g/cm³")
        self._de_density.setToolTip("0 = use tabulated elemental density.")
        def_f.addRow(_twocol("Thickness:", self._de_thickness, "ρ override:", self._de_density))
        lv.addWidget(grp_de)

        grp_ms = QtWidgets.QGroupBox("Multiple scattering (differentiable cylinder transport)")
        msf = QtWidgets.QFormLayout(grp_ms); msf.setSpacing(4)
        self._ms_enable = QtWidgets.QCheckBox("Enable")
        msf.addRow(self._ms_enable)
        self._ms_mu_mode = _NoScrollComboBox()
        self._ms_mu_mode.addItem("Auto (from composition)", "auto")
        self._ms_mu_mode.addItem("Manual μ", "manual")
        self._ms_mu_mode.currentIndexChanged.connect(self._on_ms_mu_mode_changed)
        msf.addRow("μ source:", self._ms_mu_mode)
        self._ms_mu = _fspin(0.0, 10.0, 6, 0.0, "1/µm")
        msf.addRow("μ (manual):", self._ms_mu)
        self._ms_density = _fspin(0.0, 30.0, 3, 0.0, "g/cm³")
        self._ms_density.setToolTip("Required to auto-estimate μ for a compound composition.")
        msf.addRow("ρ (for auto μ):", self._ms_density)
        self._ms_r = _fspin(0.0, 5000.0, 1, 500.0, "µm")
        msf.addRow("Cylinder radius R:", self._ms_r)
        self._ms_albedo = _fspin(0.0, 1.0, 3, 0.9)
        self._ms_albedo.setToolTip("Single-scattering albedo (coherent+Compton / total).")
        self._ms_qmax = _fspin(0.0, 50.0, 2, 22.0)
        msf.addRow(_twocol("Albedo:", self._ms_albedo, "Qmax:", self._ms_qmax))
        self._ms_nmu = QtWidgets.QSpinBox(); self._ms_nmu.setRange(4, 256); self._ms_nmu.setValue(32)
        self._ms_ntau = QtWidgets.QSpinBox(); self._ms_ntau.setRange(4, 500); self._ms_ntau.setValue(100)
        msf.addRow(_twocol("n_mu:", self._ms_nmu, "n_tau:", self._ms_ntau))
        lv.addWidget(grp_ms)

        grp_an = QtWidgets.QGroupBox("Absolute (electron-unit) normalization")
        anf = QtWidgets.QFormLayout(grp_an); anf.setSpacing(4)
        self._absnorm_enable = QtWidgets.QCheckBox("Enable")
        anf.addRow(self._absnorm_enable)
        self._an_qmin = _fspin(0.0, 50.0, 2, 18.0)
        self._an_qmax = _fspin(0.0, 50.0, 2, 21.5)
        anf.addRow(_twocol("anchor Qmin:", self._an_qmin, "anchor Qmax:", self._an_qmax))
        self._an_anomalous = QtWidgets.QCheckBox("Use anomalous (f′,f″) form factors")
        self._an_anomalous.setChecked(True)
        anf.addRow(self._an_anomalous)
        lv.addWidget(grp_an)

        grp_tf = QtWidgets.QGroupBox("S(Q) tail-flatten (display only)")
        tff = QtWidgets.QFormLayout(grp_tf); tff.setSpacing(4)
        self._tf_enable = QtWidgets.QCheckBox("Enable")
        tff.addRow(self._tf_enable)
        self._tf_qmin = _fspin(0.0, 50.0, 2, 2.0)
        self._tf_qmax = _fspin(0.0, 50.0, 2, 22.0)
        tff.addRow(_twocol("window Qmin:", self._tf_qmin, "window Qmax:", self._tf_qmax))
        self._tf_polydeg = QtWidgets.QSpinBox(); self._tf_polydeg.setRange(0, 8); self._tf_polydeg.setValue(3)
        self._tf_madk = _fspin(0.5, 20.0, 2, 3.0)
        tff.addRow(_twocol("poly degree:", self._tf_polydeg, "MAD-k:", self._tf_madk))
        self._tf_niter = QtWidgets.QSpinBox(); self._tf_niter.setRange(1, 20); self._tf_niter.setValue(3)
        tff.addRow("iterations:", self._tf_niter)
        lv.addWidget(grp_tf)

        grp_fl = QtWidgets.QGroupBox("Fluorescence diagnostic")
        flf = QtWidgets.QFormLayout(grp_fl); flf.setSpacing(4)
        self._fluor_container_ed = QtWidgets.QLineEdit("")
        self._fluor_container_ed.setPlaceholderText("Container composition (optional), e.g. C:22,H:10,N:2,O:5")
        flf.addRow("Container:", self._fluor_container_ed)
        self._fluor_min_yield = _fspin(0.0, 1.0, 3, 0.05)
        flf.addRow("min yield:", self._fluor_min_yield)
        fluor_btn = QtWidgets.QPushButton("Check fluorescence")
        fluor_btn.clicked.connect(self._check_fluorescence)
        flf.addRow(fluor_btn)
        lv.addWidget(grp_fl)

        lv.addStretch(1)
        return scroll

    # ── Page 3: Structure Fit ────────────────────────────────────────────────
    def _build_page_structure_fit(self):
        scroll, lv = self._scroll_page()

        grp_cr = QtWidgets.QGroupBox("Crystal")
        crf = QtWidgets.QFormLayout(grp_cr); crf.setSpacing(4)
        self._crystal_source = _NoScrollComboBox()
        self._crystal_source.addItem("CIF file", "cif")
        self._crystal_source.addItem("Manual lattice", "manual")
        self._crystal_source.currentIndexChanged.connect(self._on_crystal_source_changed)
        crf.addRow("Source:", self._crystal_source)
        self._cif_ed = QtWidgets.QLineEdit(DEFAULT_PDF_CIF if Path(DEFAULT_PDF_CIF).exists() else "")
        crf.addRow("CIF file:", self._frow(self._cif_ed, self._browse_cif))
        lv.addWidget(grp_cr)

        self._grp_manual = QtWidgets.QGroupBox("Manual lattice + atoms")
        mlf = QtWidgets.QFormLayout(self._grp_manual); mlf.setSpacing(4)
        self._lat_a = _fspin(0.1, 100.0, 4, 3.524, "Å")
        self._lat_b = _fspin(0.1, 100.0, 4, 3.524, "Å")
        self._lat_c = _fspin(0.1, 100.0, 4, 3.524, "Å")
        mlf.addRow(_twocol("a:", self._lat_a, "b:", self._lat_b))
        mlf.addRow("c:", self._lat_c)
        self._lat_alpha = _fspin(1.0, 179.0, 2, 90.0, "°")
        self._lat_beta = _fspin(1.0, 179.0, 2, 90.0, "°")
        self._lat_gamma = _fspin(1.0, 179.0, 2, 90.0, "°")
        mlf.addRow(_twocol("α:", self._lat_alpha, "β:", self._lat_beta))
        mlf.addRow("γ:", self._lat_gamma)
        self._sg_number = QtWidgets.QSpinBox(); self._sg_number.setRange(1, 230); self._sg_number.setValue(225)
        mlf.addRow("Space group #:", self._sg_number)
        self._atom_table = QtWidgets.QTableWidget(0, len(_ATOM_COLS))
        self._atom_table.setHorizontalHeaderLabels(_ATOM_COLS)
        self._atom_table.horizontalHeader().setStretchLastSection(True)
        self._atom_table.setMaximumHeight(140)
        mlf.addRow(self._atom_table)
        atom_btns = QtWidgets.QHBoxLayout()
        add_btn = QtWidgets.QPushButton("+ Atom"); add_btn.clicked.connect(lambda: self._add_atom_row())
        rm_btn = QtWidgets.QPushButton("− Atom"); rm_btn.clicked.connect(self._remove_atom_row)
        atom_btns.addWidget(add_btn); atom_btns.addWidget(rm_btn); atom_btns.addStretch(1)
        mlf.addRow(atom_btns)
        self._add_atom_row()  # default: single Ni-at-origin row
        lv.addWidget(self._grp_manual)

        grp_fit = QtWidgets.QGroupBox("Fit range + parameters")
        fpf = QtWidgets.QFormLayout(grp_fit); fpf.setSpacing(4)
        self._fit_rmax = _fspin(1.0, 50.0, 2, 10.0, "Å")
        self._fit_rmax.setToolTip("Pair-list cutoff r_max for build_pair_list.")
        fpf.addRow("pair r_max:", self._fit_rmax)
        self._fit_r_min = _fspin(0.0, 50.0, 2, 1.8, "Å")
        self._fit_r_max = _fspin(0.0, 50.0, 2, 9.0, "Å")
        fpf.addRow(_twocol("fit rmin:", self._fit_r_min, "fit rmax:", self._fit_r_max))
        self._sigma_inflate = _fspin(0.1, 200.0, 2, 1.0)
        self._sigma_inflate.setToolTip(
            "Multiply σ(G) by this factor before fitting — inflate when χ² is "
            "dominated by systematic shape mismatch rather than counting statistics.")
        fpf.addRow("σ inflate:", self._sigma_inflate)
        self._init_a = _fspin(0.0, 100.0, 4, 0.0, "Å")
        self._init_a.setToolTip("0 = use the crystal's own lattice constant as the initial guess.")
        self._init_u_iso = _fspin(0.0001, 1.0, 5, 0.005)
        self._init_scale = _fspin(0.0, 100.0, 4, 1.0)
        fpf.addRow(_twocol("init a:", self._init_a, "init u_iso:", self._init_u_iso))
        fpf.addRow("init scale:", self._init_scale)
        self._fit_bg_enable = QtWidgets.QCheckBox("Fit background polynomial")
        self._fit_bg_enable.toggled.connect(self._on_fit_bg_toggled)
        self._fit_bg_order = QtWidgets.QSpinBox(); self._fit_bg_order.setRange(0, 5); self._fit_bg_order.setValue(0)
        fpf.addRow(self._fit_bg_enable, self._fit_bg_order)
        self._fit_steps = QtWidgets.QSpinBox(); self._fit_steps.setRange(10, 5000); self._fit_steps.setValue(200)
        self._fit_lr = _fspin(0.001, 5.0, 3, 0.1)
        fpf.addRow(_twocol("steps:", self._fit_steps, "lr:", self._fit_lr))
        self._fit_nposterior = QtWidgets.QSpinBox(); self._fit_nposterior.setRange(0, 5000); self._fit_nposterior.setValue(0)
        self._fit_nposterior.setToolTip("0 = skip posterior sampling (Hessian-derived uncertainties only).")
        fpf.addRow("n posterior samples:", self._fit_nposterior)
        lv.addWidget(grp_fit)

        self._fit_run_btn = S.primary_btn("Run structure fit")
        self._fit_run_btn.clicked.connect(self._run_structure_fit)
        lv.addWidget(self._fit_run_btn)
        lv.addStretch(1)
        return scroll

    # ── Page 4: Δ-PDF ────────────────────────────────────────────────────────
    def _build_page_delta_pdf(self):
        scroll, lv = self._scroll_page()
        grp = QtWidgets.QGroupBox("Δ-PDF significance test")
        f = QtWidgets.QFormLayout(grp); f.setSpacing(4)
        self._dpdf_nsigma = _fspin(0.5, 20.0, 2, 3.0)
        f.addRow("n_sigma threshold:", self._dpdf_nsigma)
        lv.addWidget(grp)

        save_a_btn = QtWidgets.QPushButton("Save current result as State A")
        save_a_btn.clicked.connect(lambda: self._save_state("a"))
        lv.addWidget(save_a_btn)
        self._state_a_lbl = QtWidgets.QLabel("State A: (empty)")
        self._state_a_lbl.setStyleSheet("color:#aaa;font-size:10px")
        lv.addWidget(self._state_a_lbl)

        save_b_btn = QtWidgets.QPushButton("Save current result as State B")
        save_b_btn.clicked.connect(lambda: self._save_state("b"))
        lv.addWidget(save_b_btn)
        self._state_b_lbl = QtWidgets.QLabel("State B: (empty)")
        self._state_b_lbl.setStyleSheet("color:#aaa;font-size:10px")
        lv.addWidget(self._state_b_lbl)

        compute_btn = S.primary_btn("Compute ΔG(r) = B − A")
        compute_btn.clicked.connect(self._run_delta_pdf)
        lv.addWidget(compute_btn)
        lv.addStretch(1)
        return scroll

    # ── Right panel: plot pages ─────────────────────────────────────────────
    def _build_reduction_plots(self):
        w = QtWidgets.QWidget()
        pv = QtWidgets.QVBoxLayout(w); pv.setContentsMargins(4, 4, 4, 4)

        self._top = pg.PlotWidget(background="k")
        self._top.setLabel("left", "I(Q)"); self._top.setLabel("bottom", "Q (Å⁻¹)")
        self._top.showGrid(x=True, y=True, alpha=0.2); self._top.addLegend()
        self._c_iq = self._top.plot([], [], pen=pg.mkPen("#88ccff", width=2), name="I(Q)")
        self._c_bg = self._top.plot([], [], pen=pg.mkPen("#f0a030", width=1,
                                    style=QtCore.Qt.DashLine), name="background")
        pv.addWidget(self._top, stretch=1)

        mid_toolbar = QtWidgets.QHBoxLayout()
        self._tf_show_chk = QtWidgets.QCheckBox("Show tail-flattened S(Q)")
        self._tf_show_chk.toggled.connect(self._redraw_mid)
        mid_toolbar.addWidget(self._tf_show_chk); mid_toolbar.addStretch(1)
        pv.addLayout(mid_toolbar)

        self._mid = pg.PlotWidget(background="k")
        self._mid.setLabel("left", "S(Q)"); self._mid.setLabel("bottom", "Q (Å⁻¹)")
        self._mid.showGrid(x=True, y=True, alpha=0.2)
        self._c_mid = self._mid.plot([], [], pen=pg.mkPen("#ff8844", width=1.5))
        self._s1_line = pg.InfiniteLine(pos=1.0, angle=0,
                                        pen=pg.mkPen("#666", style=QtCore.Qt.DashLine))
        self._mid.addItem(self._s1_line)
        pv.addWidget(self._mid, stretch=1)

        self._bot = pg.PlotWidget(background="k")
        self._bot.setLabel("left", "G(r)"); self._bot.setLabel("bottom", "r (Å)")
        self._bot.showGrid(x=True, y=True, alpha=0.2)
        self._gr_up = self._bot.plot([], [], pen=None)
        self._gr_lo = self._bot.plot([], [], pen=None)
        self._gr_band = pg.FillBetweenItem(self._gr_up, self._gr_lo,
                                           brush=pg.mkBrush(124, 252, 0, 60))
        self._bot.addItem(self._gr_band)
        self._c_gr = self._bot.plot([], [], pen=pg.mkPen("#7CFC00", width=2))
        pv.addWidget(self._bot, stretch=1)
        return w

    def _build_structure_fit_plots(self):
        w = QtWidgets.QWidget()
        pv = QtWidgets.QVBoxLayout(w); pv.setContentsMargins(4, 4, 4, 4)

        self._fit_plot = pg.PlotWidget(background="k")
        self._fit_plot.setLabel("left", "G(r)"); self._fit_plot.setLabel("bottom", "r (Å)")
        self._fit_plot.showGrid(x=True, y=True, alpha=0.2); self._fit_plot.addLegend()
        self._fit_up = self._fit_plot.plot([], [], pen=None)
        self._fit_lo = self._fit_plot.plot([], [], pen=None)
        self._fit_band = pg.FillBetweenItem(self._fit_up, self._fit_lo,
                                            brush=pg.mkBrush(124, 252, 0, 60))
        self._fit_plot.addItem(self._fit_band)
        self._fit_obs = self._fit_plot.plot([], [], pen=pg.mkPen("#7CFC00", width=2), name="observed")
        self._fit_calc = self._fit_plot.plot([], [], pen=pg.mkPen("#ff5050", width=2,
                                             style=QtCore.Qt.DashLine), name="model")
        pv.addWidget(self._fit_plot, stretch=3)

        self._fit_resid_plot = pg.PlotWidget(background="k")
        self._fit_resid_plot.setLabel("left", "(obs−calc)/σ"); self._fit_resid_plot.setLabel("bottom", "r (Å)")
        self._fit_resid_plot.showGrid(x=True, y=True, alpha=0.2)
        self._fit_resid = self._fit_resid_plot.plot([], [], pen=pg.mkPen("#4da3ff", width=1))
        pv.addWidget(self._fit_resid_plot, stretch=1)

        bottom_row = QtWidgets.QHBoxLayout()
        self._fit_table = QtWidgets.QTableWidget(0, 3)
        self._fit_table.setHorizontalHeaderLabels(["Parameter", "Value", "σ"])
        self._fit_table.horizontalHeader().setStretchLastSection(True)
        self._fit_table.setMaximumHeight(160)
        bottom_row.addWidget(self._fit_table, stretch=1)
        self._fit_chi2_lbl = QtWidgets.QLabel("χ²/ndof = —")
        self._fit_chi2_lbl.setStyleSheet(f"color:{S.ACCENT};font-size:13px;font-weight:bold")
        self._fit_chi2_lbl.setAlignment(QtCore.Qt.AlignTop)
        bottom_row.addWidget(self._fit_chi2_lbl)
        pv.addLayout(bottom_row)
        return w

    def _build_delta_pdf_plots(self):
        w = QtWidgets.QWidget()
        pv = QtWidgets.QVBoxLayout(w); pv.setContentsMargins(4, 4, 4, 4)

        self._dpdf_plot = pg.PlotWidget(background="k")
        self._dpdf_plot.setLabel("left", "ΔG(r)"); self._dpdf_plot.setLabel("bottom", "r (Å)")
        self._dpdf_plot.showGrid(x=True, y=True, alpha=0.2)
        self._dpdf_up = self._dpdf_plot.plot([], [], pen=None)
        self._dpdf_lo = self._dpdf_plot.plot([], [], pen=None)
        self._dpdf_band = pg.FillBetweenItem(self._dpdf_up, self._dpdf_lo,
                                             brush=pg.mkBrush(255, 120, 0, 50))
        self._dpdf_plot.addItem(self._dpdf_band)
        self._dpdf_curve = self._dpdf_plot.plot([], [], pen=pg.mkPen("#4da3ff", width=2))
        self._dpdf_scatter = pg.ScatterPlotItem([], [], symbol="o", size=7,
                                                brush=pg.mkBrush("#ff3030"), pen=None)
        self._dpdf_plot.addItem(self._dpdf_scatter)
        pv.addWidget(self._dpdf_plot, stretch=1)

        self._dpdf_count_lbl = QtWidgets.QLabel("— / — points significant")
        self._dpdf_count_lbl.setStyleSheet(f"color:{S.ACCENT};font-size:12px")
        pv.addWidget(self._dpdf_count_lbl)
        return w

    # ── UI reactions ──────────────────────────────────────────────────────────
    def _on_source_changed(self):
        image_mode = self._source.currentData() == "image"
        self._grp_img.setVisible(image_mode)
        self._grp_file.setVisible(not image_mode)
        self._binning.setEnabled(image_mode)
        self._update_run_enabled()

    def _on_refine_toggled(self):
        on = self._refine.isChecked()
        for i in range(self._rmp_row.count()):
            w = self._rmp_row.itemAt(i).widget()
            if w: w.setEnabled(on)
        for i in range(self._steps_row.count()):
            w = self._steps_row.itemAt(i).widget()
            if w: w.setEnabled(on)

    def _on_bg_mode_changed(self):
        fit_mode = self._bg_mode.currentData() == "fit"
        for i in range(self._bg_fit_row.count()):
            w = self._bg_fit_row.itemAt(i).widget()
            if w: w.setEnabled(fit_mode)
        self._bg_scale.setEnabled(not fit_mode)

    def _on_bg_pp_toggled(self):
        on = self._bg_pp.isChecked()
        for row in (self._bg_mu_row, self._bg_r_row, self._bg_density_row):
            for i in range(row.count()):
                w = row.itemAt(i).widget()
                if w: w.setEnabled(on)
        self._bg_container_comp.setEnabled(on)
        self._bg_mu_btn.setEnabled(on)

    def _on_ms_mu_mode_changed(self):
        manual = self._ms_mu_mode.currentData() == "manual"
        self._ms_mu.setEnabled(manual)
        self._ms_density.setEnabled(not manual)

    def _on_crystal_source_changed(self):
        cif_mode = self._crystal_source.currentData() == "cif"
        self._cif_ed.setEnabled(cif_mode)
        self._grp_manual.setEnabled(not cif_mode)

    def _on_fit_bg_toggled(self):
        self._fit_bg_order.setEnabled(self._fit_bg_enable.isChecked())

    def _update_run_enabled(self):
        if self._source.currentData() == "image":
            ok = self._result is not None and self._image is not None
        else:
            ok = bool(self._iq_ed.text().strip()) and Path(self._iq_ed.text().strip()).exists()
        self._run_btn.setEnabled(ok)

    def _browse_img(self):
        p = _browse(self, "Open frame", "Images (*.tif *.tiff *.h5 *.hdf5 *.ge*);;All (*)")
        if p: self._img_ed.setText(p); self._load_img()

    def _browse_iq(self):
        p = _browse(self, "Open I(Q) file", "I(Q) (*.csv *.txt *.dat *.xy *.chi);;All (*)")
        if p: self._iq_ed.setText(p)

    def _browse_mask(self):
        p = _browse(self, "Open mask", "Mask (*.tif *.tiff);;All (*)")
        if p: self._mask_ed.setText(p); self._load_mask()

    def _browse_bg_iq(self):
        p = _browse(self, "Open empty-cell I(Q) file", "I(Q) (*.csv *.txt *.dat *.xy *.chi);;All (*)")
        if p: self._bg_iq_ed.setText(p)

    def _browse_cif(self):
        p = _browse(self, "Open CIF file", "CIF (*.cif);;All (*)")
        if p: self._cif_ed.setText(p)

    def _load_img(self):
        path = self._img_ed.text().strip()
        if not path or not Path(path).exists():
            QtWidgets.QMessageBox.warning(self, "Error", "Image not found."); return
        try:
            self._image = _load_image(path, data_loc=self._img_h5_ed.text().strip() or "exchange/data")
            self._log.append(f"Image loaded: {Path(path).name} {self._image.shape}")
            self._update_run_enabled()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Load error", str(e))

    def _load_mask(self):
        path = self._mask_ed.text().strip()
        if not path or not Path(path).exists():
            return
        try:
            import tifffile
            raw = tifffile.imread(path)
            self._mask = (raw != 0).astype(np.uint8)
            self._log.append(f"Mask loaded: {Path(path).name} {self._mask.shape}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Mask load error", str(e))

    def _add_atom_row(self, values=None):
        row = self._atom_table.rowCount()
        self._atom_table.insertRow(row)
        defaults = values or ["Ni", "0.0", "0.0", "0.0", "1.0", "0.0"]
        for c, val in enumerate(defaults):
            self._atom_table.setItem(row, c, QtWidgets.QTableWidgetItem(str(val)))

    def _remove_atom_row(self):
        row = self._atom_table.currentRow()
        if row >= 0:
            self._atom_table.removeRow(row)

    def _estimate_mu(self):
        import midas_gui.pdf_backend as pdf
        try:
            wl = self._effective_wavelength()
            comp_dict = _parse_composition(self._comp_ed.text().strip())
            material = next(iter(comp_dict)) if len(comp_dict) == 1 else comp_dict
            mu_s = pdf.linear_attenuation_um(material, wl)
            self._bg_mu_s.setValue(float(mu_s))
            cont_txt = self._bg_container_comp.text().strip()
            if cont_txt:
                cont_dict = _parse_composition(cont_txt)
                mu_c = pdf.linear_attenuation_um(
                    cont_dict, wl, density_g_cm3=self._bg_container_density.value())
                self._bg_mu_c.setValue(float(mu_c))
            self._log.append(
                f"[pdf] μ estimated: sample={self._bg_mu_s.value():.4g} /µm, "
                f"container={self._bg_mu_c.value():.4g} /µm")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "μ estimate failed", str(e))

    def _check_fluorescence(self):
        import midas_gui.pdf_backend as pdf
        try:
            wl = self._effective_wavelength()
            sample_els = list(_parse_composition(self._comp_ed.text().strip()).keys())
            cont_txt = self._fluor_container_ed.text().strip()
            cont_els = list(_parse_composition(cont_txt).keys()) if cont_txt else None
            rep = pdf.fluorescence_report_sample_and_container(
                sample_els, cont_els, wavelength_A=wl,
                min_yield=self._fluor_min_yield.value())
            lines = [f"Clean: {rep['clean']}"]
            for label, key in (("Sample", "sample_lines"), ("Container", "container_lines")):
                lines.append(f"{label}:")
                if not rep[key]:
                    lines.append("  (none above min yield)")
                for ln in rep[key]:
                    lines.append(f"  {ln['element']} {ln['shell']}  "
                                 f"{ln['line_keV']:.3f} keV  yield={ln['yield']:.3g}")
            msg = "\n".join(lines)
            self._log.append("[pdf] fluorescence check:\n" + msg)
            QtWidgets.QMessageBox.information(self, "Fluorescence diagnostic", msg)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Fluorescence check failed", str(e))

    # ── run / results ─────────────────────────────────────────────────────────
    def _run(self):
        if self._worker and self._worker.isRunning():
            return
        image_mode = self._source.currentData() == "image"
        if image_mode and (self._result is None or self._image is None):
            QtWidgets.QMessageBox.warning(self, "Missing", "Need calibration + image."); return
        if not image_mode and not Path(self._iq_ed.text().strip() or "x").exists():
            QtWidgets.QMessageBox.warning(self, "Missing", "Select an I(Q) file."); return
        if not self._comp_ed.text().strip():
            QtWidgets.QMessageBox.warning(self, "Missing", "Enter a composition."); return
        if self._refine.isChecked() and self._rho0.value() <= 0:
            QtWidgets.QMessageBox.warning(self, "ρ₀ required",
                                          "Refinement needs a number density ρ₀ > 0."); return
        if self._bg_enable.isChecked() and not Path(self._bg_iq_ed.text().strip() or "x").exists():
            QtWidgets.QMessageBox.warning(self, "Missing", "Select an empty-cell I(Q) file."); return

        wl = self._effective_wavelength()
        cfg = {
            "iq_source": "image" if image_mode else "file",
            "iq_file": self._iq_ed.text().strip(),
            "composition": self._comp_ed.text().strip(),
            "rho0": self._rho0.value(), "wavelength": wl,
            "compton": self._compton.isChecked(),
            "q_min": self._qmin.value(), "q_max": self._qmax.value(),
            "r_min": self._rmin.value(), "r_max": self._rmax.value(), "r_step": self._rstep.value(),
            "window": self._window.currentText(), "binning": self._binning.currentText(),
            "refine": self._refine.isChecked(), "bg_order": self._bg_order.value(),
            "r_min_phys": self._r_min_phys.value(), "refine_steps": self._steps.value(),
            "output_fn": self._out_fn.currentData(),
        }
        if self._bg_enable.isChecked():
            bg = {"iq_file": self._bg_iq_ed.text().strip(),
                 "mode": self._bg_mode.currentData(),
                 "scale": self._bg_scale.value(),
                 "fit_q": (self._bg_fit_qmin.value(), self._bg_fit_qmax.value()),
                 "paalman_pings": self._bg_pp.isChecked()}
            if self._bg_pp.isChecked():
                bg.update(mu_sample_um=self._bg_mu_s.value(), mu_container_um=self._bg_mu_c.value(),
                         r_sample_um=self._bg_r_s.value(), r_container_um=self._bg_r_c.value())
            cfg["bg_enabled"] = True
            cfg["bg"] = bg
        if self._deteff_enable.isChecked():
            cfg["det_eff_enabled"] = True
            cfg["det_eff"] = {"material": self._de_material.text().strip() or "Si",
                              "thickness_um": self._de_thickness.value(),
                              "density_g_cm3": (self._de_density.value() or None)}
        if self._absnorm_enable.isChecked():
            cfg["absnorm_enabled"] = True
            cfg["absnorm"] = {"q_window": (self._an_qmin.value(), self._an_qmax.value()),
                              "anomalous": self._an_anomalous.isChecked()}
        if self._ms_enable.isChecked():
            ms = {"r_um": self._ms_r.value(), "albedo": self._ms_albedo.value(),
                 "q_max": self._ms_qmax.value(), "n_mu": self._ms_nmu.value(),
                 "n_tau": self._ms_ntau.value()}
            if self._ms_mu_mode.currentData() == "manual":
                ms["mu_um"] = self._ms_mu.value()
            else:
                ms["density_g_cm3"] = (self._ms_density.value() or None)
            cfg["ms_enabled"] = True
            cfg["ms"] = ms
        if self._tf_enable.isChecked():
            cfg["tail_flatten_enabled"] = True
            cfg["tail_flatten"] = {"window": (self._tf_qmin.value(), self._tf_qmax.value()),
                                   "poly_deg": self._tf_polydeg.value(),
                                   "mad_k": self._tf_madk.value(),
                                   "n_iter": self._tf_niter.value()}

        self._run_btn.setEnabled(False)
        self._log.append("─" * 40 + "\nComputing PDF…")
        self._worker = PDFWorker(self._result, self._image, None, self._mask, cfg, parent=self)
        self._worker.log_line.connect(self._log.append)
        self._worker.finished.connect(self._on_done)
        self._worker.failed.connect(self._on_fail)
        self._worker.start()

    def _on_done(self, d):
        self._run_btn.setEnabled(True); self._save_gr.setEnabled(True); self._save_sq.setEnabled(True)
        self._last = d
        self._c_iq.setData(d["q"], d["Iq"])
        if d.get("background") is not None:
            self._c_bg.setData(d["q"], d["background"])
        else:
            self._c_bg.setData([], [])
        self._redraw_mid(); self._redraw_bottom()
        extra = ""
        if d.get("refine_loss") is not None:
            extra += f"  scale={d['scale']:.4g}  loss={d['refine_loss']:.4g}"
        if d.get("bg_scale_used") is not None:
            extra += f"  bg_s={d['bg_scale_used']:.4g}"
        if d.get("ms_beta_median") is not None:
            extra += f"  β̄={d['ms_beta_median']:.4g}"
        self._log.append(f"Done — {len(d['r'])} r-points, "
                         f"max |G| = {np.nanmax(np.abs(d['Gr'])):.3g}{extra}")

    def _redraw_mid(self):
        d = self._last
        if not d: return
        show_flat = self._tf_show_chk.isChecked() and d.get("S_flat") is not None
        if self._mid_fn.currentData() == "S":
            y = d["S_flat"] if show_flat else d["S"]
            self._c_mid.setData(d["q"], y)
            self._mid.setLabel("left", "S(Q)" + (" (flat)" if show_flat else ""))
            self._s1_line.setVisible(True)
        else:
            self._c_mid.setData(d["q"], d["Fq"])
            self._mid.setLabel("left", "F(Q) = Q(S−1)"); self._s1_line.setVisible(False)

    def _redraw_bottom(self):
        d = self._last
        if not d: return
        # Show the selected convention family if the worker produced it, else G(r).
        fam = d.get("Gr_family") or {}
        want = self._out_fn.currentData()
        if fam.get("name") == want and fam.get("y") is not None:
            y, sig, name = fam["y"], fam.get("sigma"), want
        else:
            y, sig, name = d["Gr"], d["sigma_Gr"], "G"
        r = d["r"]
        self._c_gr.setData(r, y)
        if sig is not None and np.isfinite(sig).any():
            self._gr_up.setData(r, y + sig); self._gr_lo.setData(r, y - sig)
            self._gr_band.setVisible(True)
        else:
            self._gr_band.setVisible(False)
        self._bot.setLabel("left", f"{name}(r)")

    def _on_fail(self, msg):
        self._run_btn.setEnabled(True)
        show_error(self, "PDF failed", msg, log=self._log, log_prefix="\nERROR:\n")

    # ── structure fit ────────────────────────────────────────────────────────
    def _collect_fit_cfg(self):
        cfg = {}
        if self._crystal_source.currentData() == "cif":
            path = self._cif_ed.text().strip()
            if not path or not Path(path).exists():
                QtWidgets.QMessageBox.warning(self, "Missing", "Select a CIF file."); return None
            cfg["crystal_source"] = "cif"; cfg["cif_path"] = path
        else:
            cfg["crystal_source"] = "manual"
            cfg["manual_lattice"] = (self._lat_a.value(), self._lat_b.value(), self._lat_c.value(),
                                     self._lat_alpha.value(), self._lat_beta.value(), self._lat_gamma.value())
            cfg["space_group_number"] = self._sg_number.value()
            atoms = []
            for row in range(self._atom_table.rowCount()):
                def cell(col):
                    item = self._atom_table.item(row, col)
                    return item.text().strip() if item else ""
                el = cell(0)
                if not el:
                    continue
                try:
                    atoms.append({"element": el,
                                 "x": float(cell(1) or 0), "y": float(cell(2) or 0), "z": float(cell(3) or 0),
                                 "occupancy": float(cell(4) or 1.0), "B_iso": float(cell(5) or 0.0)})
                except ValueError:
                    QtWidgets.QMessageBox.warning(self, "Bad atom row",
                                                  f"Row {row+1}: fractional coords/occupancy/B_iso "
                                                  "must be numbers."); return None
            if not atoms:
                QtWidgets.QMessageBox.warning(self, "Missing", "Add at least one atom."); return None
            cfg["manual_atoms"] = atoms
        cfg["r_max"] = self._fit_rmax.value()
        cfg["fit_r_min"] = self._fit_r_min.value()
        cfg["fit_r_max"] = self._fit_r_max.value()
        cfg["sigma_inflate"] = self._sigma_inflate.value()
        cfg["init_a"] = self._init_a.value() if self._init_a.value() > 0 else None
        cfg["init_u_iso"] = self._init_u_iso.value()
        cfg["init_scale"] = self._init_scale.value()
        cfg["bg_order"] = self._fit_bg_order.value() if self._fit_bg_enable.isChecked() else None
        cfg["steps"] = self._fit_steps.value()
        cfg["lr"] = self._fit_lr.value()
        cfg["n_posterior_samples"] = self._fit_nposterior.value()
        return cfg

    def _run_structure_fit(self):
        if self._fit_worker and self._fit_worker.isRunning():
            return
        if not self._last:
            QtWidgets.QMessageBox.warning(self, "Missing", "Compute G(r) first."); return
        cfg = self._collect_fit_cfg()
        if cfg is None:
            return
        r, G, sigma_G = self._last["r"], self._last["Gr"], self._last["sigma_Gr"]
        self._fit_run_btn.setEnabled(False)
        self._log.append("─" * 40 + "\nRunning structure fit…")
        self._fit_worker = PDFStructureFitWorker(r, G, sigma_G, cfg, parent=self)
        self._fit_worker.log_line.connect(self._log.append)
        self._fit_worker.finished.connect(self._on_fit_done)
        self._fit_worker.failed.connect(self._on_fit_fail)
        self._fit_worker.start()

    def _on_fit_done(self, d):
        self._fit_run_btn.setEnabled(True)
        self._fit_result = d
        self._redraw_fit()
        fitted, unc = d["fitted"], d["uncertainty"]
        parts = []
        for k, v in fitted.items():
            if isinstance(v, (list, tuple)):
                continue
            u = unc.get(k)
            parts.append(f"{k}={v:.5g}" + (f"±{u:.2g}" if u is not None else ""))
        self._log.append("Structure fit done — " + ", ".join(parts) +
                         f"  χ²/ndof={d['chi2_reduced']:.4g}")

    def _on_fit_fail(self, msg):
        self._fit_run_btn.setEnabled(True)
        show_error(self, "Structure fit failed", msg, log=self._log, log_prefix="\nERROR:\n")

    def _redraw_fit(self):
        d = self._fit_result
        if not d: return
        r, G_obs, G_calc = d["r_fit"], d["G_obs"], d["G_calc"]
        sig = d.get("sigma_fit")
        self._fit_obs.setData(r, G_obs)
        self._fit_calc.setData(r, G_calc)
        if sig is not None and np.isfinite(sig).any():
            self._fit_up.setData(r, G_obs + sig); self._fit_lo.setData(r, G_obs - sig)
            self._fit_band.setVisible(True)
            resid = (G_obs - G_calc) / np.where(sig != 0, sig, np.nan)
        else:
            self._fit_band.setVisible(False)
            resid = G_obs - G_calc
        self._fit_resid.setData(r, resid)

        self._fit_table.setRowCount(0)
        fitted, unc = d["fitted"], d["uncertainty"]
        for k, v in fitted.items():
            row = self._fit_table.rowCount(); self._fit_table.insertRow(row)
            self._fit_table.setItem(row, 0, QtWidgets.QTableWidgetItem(k))
            if isinstance(v, (list, tuple)):
                vs = ", ".join(f"{x:.5g}" for x in v)
                self._fit_table.setItem(row, 1, QtWidgets.QTableWidgetItem(vs))
                self._fit_table.setItem(row, 2, QtWidgets.QTableWidgetItem(""))
            else:
                self._fit_table.setItem(row, 1, QtWidgets.QTableWidgetItem(f"{v:.6g}"))
                u = unc.get(k)
                self._fit_table.setItem(row, 2, QtWidgets.QTableWidgetItem(f"{u:.3g}" if u is not None else ""))
        self._fit_chi2_lbl.setText(f"χ²/ndof = {d['chi2_reduced']:.4g}")

    # ── Δ-PDF ────────────────────────────────────────────────────────────────
    def _save_state(self, slot):
        if not self._last:
            QtWidgets.QMessageBox.warning(self, "Missing", "Compute G(r) first."); return
        sigma = self._last["sigma_Gr"]
        snap = {"r": self._last["r"].copy(), "Gr": self._last["Gr"].copy(),
               "sigma_Gr": (sigma.copy() if sigma is not None else None)}
        label = f"{len(snap['r'])} pts, max|G|={np.nanmax(np.abs(snap['Gr'])):.3g}"
        if slot == "a":
            self._state_a = snap; self._state_a_lbl.setText(f"State A: {label}")
        else:
            self._state_b = snap; self._state_b_lbl.setText(f"State B: {label}")
        self._log.append(f"Saved current result as State {slot.upper()} ({label})")

    def _run_delta_pdf(self):
        if self._state_a is None or self._state_b is None:
            QtWidgets.QMessageBox.warning(self, "Missing",
                                          "Save both State A and State B first."); return
        import torch
        import midas_gui.pdf_backend as pdf
        a, b = self._state_a, self._state_b
        ra, rb = a["r"], b["r"]
        if ra.shape == rb.shape and np.allclose(ra, rb):
            Gb = b["Gr"]
            sigb = b["sigma_Gr"]
        else:
            Gb = np.interp(ra, rb, b["Gr"])
            sigb = np.interp(ra, rb, b["sigma_Gr"]) if b["sigma_Gr"] is not None else None
        Ga, siga = a["Gr"], a["sigma_Gr"]

        G_a_t = torch.as_tensor(Ga, dtype=torch.float64)
        G_b_t = torch.as_tensor(Gb, dtype=torch.float64)
        sig_a_t = torch.as_tensor(siga, dtype=torch.float64) if siga is not None else None
        sig_b_t = torch.as_tensor(sigb, dtype=torch.float64) if sigb is not None else None

        dG, sig_dG = pdf.delta_pdf(G_a_t, G_b_t, sigma_a=sig_a_t, sigma_b=sig_b_t)
        n_sigma = self._dpdf_nsigma.value()
        mask = pdf.significant_mask(dG, sig_dG, n_sigma=n_sigma)

        def _np(x):
            return x.numpy() if hasattr(x, "numpy") else np.asarray(x)

        self._dpdf_result = {"r": ra, "dG": _np(dG), "sigma_dG": _np(sig_dG),
                             "mask": _np(mask).astype(bool), "n_sigma": n_sigma}
        self._redraw_delta_pdf()
        n_sig = int(self._dpdf_result["mask"].sum())
        total = self._dpdf_result["mask"].size
        self._log.append(f"[pdf] Δ-PDF (B − A): {n_sig}/{total} points > {n_sigma:.2g}σ")
        self._dpdf_count_lbl.setText(f"{n_sig} / {total} points > {n_sigma:.2g}σ")

    def _redraw_delta_pdf(self):
        d = self._dpdf_result
        if not d: return
        r, dG, sig, mask = d["r"], d["dG"], d["sigma_dG"], d["mask"]
        self._dpdf_curve.setData(r, dG)
        if sig is not None and np.isfinite(sig).any():
            band = d["n_sigma"] * sig
            self._dpdf_up.setData(r, dG + band); self._dpdf_lo.setData(r, dG - band)
            self._dpdf_band.setVisible(True)
        else:
            self._dpdf_band.setVisible(False)
        if mask is not None and mask.any():
            self._dpdf_scatter.setData(r[mask], dG[mask])
        else:
            self._dpdf_scatter.setData([], [])

    # ── save ──────────────────────────────────────────────────────────────────
    def _save_gr_file(self):
        if not self._last: return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save G(r)", "Gr.gr",
                                                        "Text (*.gr *.dat *.txt)")
        if not path: return
        d = self._last
        arr = np.column_stack([d["r"], d["Gr"], d["sigma_Gr"]])
        np.savetxt(path, arr, header="r(A)  G(r)  sigma", comments="#")
        self._log.append(f"Saved G(r): {path}")

    def _save_sq_file(self):
        if not self._last: return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save S(Q)", "SQ.dat",
                                                        "Text (*.dat *.txt *.sq)")
        if not path: return
        d = self._last
        arr = np.column_stack([d["q"], d["S"]])
        np.savetxt(path, arr, header="Q(1/A)  S(Q)", comments="#")
        self._log.append(f"Saved S(Q): {path}")
