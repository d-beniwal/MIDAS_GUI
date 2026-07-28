"""Tab 6 — PDF Analysis (polyatomic total scattering).

I(Q) — integrated from a detector frame or loaded from a ``Q,I,σ`` file — is
reduced to a Faber-Ziman structure function S(Q) and pair-distribution G(r) via
the ``midas_pdf`` backend: real composition-weighted normalization, Compton
subtraction, end-to-end σ propagation, optional differentiable scale/background
refinement, and the G/g/T/R convention family.  Export G(r) and S(Q).
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
from midas_gui.constants import DEFAULT_NICKEL_FRAME0, DEFAULT_PDF_IQ_FILE
from midas_gui.widgets import LogPanel
from midas_gui.workers import PDFWorker
from midas_gui import style as S


class PDFTab(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._result = None
        self._image: Optional[np.ndarray] = None
        self._mask: Optional[np.ndarray] = None
        self._worker = None
        self._last = None
        self._build_ui()
        if Path(self._img_ed.text().strip() or "x").exists():
            self._load_img()
        # Default to the ready real I(Q) file when it exists (quick PDF test).
        if Path(DEFAULT_PDF_IQ_FILE).exists():
            self._source.setCurrentIndex(self._source.findData("file"))
        self._on_source_changed()
        self._on_refine_toggled()

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

    # ── GUI state (Save/Load GUI State) ─────────────────────────────
    def _state_widgets(self) -> dict:
        return {
            "source": self._source,
            "img_ed": self._img_ed,
            "img_h5_ed": self._img_h5_ed,
            "iq_ed": self._iq_ed,
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
        }

    def get_state(self) -> dict:
        return {"fields": widgets_to_dict(self._state_widgets())}

    def set_state(self, state: dict):
        fields = state.get("fields", {})
        apply_dict_to_widgets(self._state_widgets(), fields)
        self._on_source_changed()
        self._on_refine_toggled()
        img_path = fields.get("img_ed")
        if img_path and Path(img_path).exists():
            self._load_img()
        self._update_run_enabled()

    # ── UI ───────────────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6); root.setSpacing(8)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True); scroll.setFixedWidth(546)
        inner = QtWidgets.QWidget(); lv = QtWidgets.QVBoxLayout(inner); lv.setSpacing(6)
        scroll.setWidget(inner)

        def _br(w=28):
            b = QtWidgets.QPushButton("…"); b.setFixedWidth(w); return b

        def _frow(ed, slot):
            r = QtWidgets.QHBoxLayout(); r.setSpacing(3)
            r.addWidget(ed); b = _br(); b.clicked.connect(slot); r.addWidget(b); return r

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
        gf.addRow("Image:", _frow(self._img_ed, self._browse_img))
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
        lv.addWidget(self._grp_img)

        # I(Q) file (file mode) --------------------------------------------------
        self._grp_file = QtWidgets.QGroupBox("I(Q) file")
        ff = QtWidgets.QFormLayout(self._grp_file); ff.setSpacing(4)
        self._iq_ed = QtWidgets.QLineEdit(
            DEFAULT_PDF_IQ_FILE if Path(DEFAULT_PDF_IQ_FILE).exists() else "")
        self._iq_ed.setPlaceholderText("Q,I,σ text/CSV (2- or 3-column)…")
        self._iq_ed.textChanged.connect(lambda _: self._update_run_enabled())
        ff.addRow("File:", _frow(self._iq_ed, self._browse_iq))
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
        root.addWidget(scroll)

        # Plots ------------------------------------------------------------------
        right = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        plots = QtWidgets.QWidget(); pv = QtWidgets.QVBoxLayout(plots); pv.setContentsMargins(0, 0, 0, 0)

        self._top = pg.PlotWidget(background="k")
        self._top.setLabel("left", "I(Q)"); self._top.setLabel("bottom", "Q (Å⁻¹)")
        self._top.showGrid(x=True, y=True, alpha=0.2); self._top.addLegend()
        self._c_iq = self._top.plot([], [], pen=pg.mkPen("#88ccff", width=2), name="I(Q)")
        self._c_bg = self._top.plot([], [], pen=pg.mkPen("#f0a030", width=1,
                                    style=QtCore.Qt.DashLine), name="background")
        pv.addWidget(self._top, stretch=1)

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

        right.addWidget(plots)
        self._log = LogPanel()
        right.addWidget(self._log)
        right.setStretchFactor(0, 5); right.setStretchFactor(1, 1)
        root.addWidget(right, stretch=1)

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
        wl = float(self._result.wavelength_A) if (image_mode and self._result) else self._wl.value()
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
            extra = f"  scale={d['scale']:.4g}  loss={d['refine_loss']:.4g}"
        self._log.append(f"Done — {len(d['r'])} r-points, "
                         f"max |G| = {np.nanmax(np.abs(d['Gr'])):.3g}{extra}")

    def _redraw_mid(self):
        d = self._last
        if not d: return
        if self._mid_fn.currentData() == "S":
            self._c_mid.setData(d["q"], d["S"])
            self._mid.setLabel("left", "S(Q)"); self._s1_line.setVisible(True)
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
        self._log.append(f"\nERROR:\n{msg[:600]}")
        QtWidgets.QMessageBox.critical(self, "PDF failed", msg[:400])

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
