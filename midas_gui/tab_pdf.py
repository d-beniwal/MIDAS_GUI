"""Tab 6 — PDF Analysis.

Calibrated integration → I(Q) with background estimate → pair-distribution G(r),
using midas_integrate_v2.pdf.  Export G(r) to a two-column text file.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
from PyQt5 import QtCore, QtWidgets
import pyqtgraph as pg

from midas_gui.helpers import _load_image, _fspin, _twocol, _browse, is_h5
from midas_gui.constants import DEFAULT_NICKEL_FRAME0
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

    def set_calibration(self, result):
        self._result = result
        self._src_lbl.setText(f"From Tab 2: λ={result.wavelength_A:.5f} Å  Lsd={result.Lsd/1000:.3f} mm")
        self._run_btn.setEnabled(self._image is not None)

    def set_mask_from_tab1(self, mask):
        self._mask = mask

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

        grp_src = QtWidgets.QGroupBox("Calibration source")
        sv = QtWidgets.QVBoxLayout(grp_src)
        self._src_lbl = QtWidgets.QLabel("(run Tab 2 calibration first)")
        self._src_lbl.setStyleSheet("color:#aaa;font-size:10px"); self._src_lbl.setWordWrap(True)
        sv.addWidget(self._src_lbl)
        lv.addWidget(grp_src)

        grp_img = QtWidgets.QGroupBox("Sample frame")
        gf = QtWidgets.QFormLayout(grp_img); gf.setSpacing(4)
        self._img_ed = QtWidgets.QLineEdit(DEFAULT_NICKEL_FRAME0); self._img_ed.setPlaceholderText("Sample frame…")
        gf.addRow("Image:", _frow(self._img_ed, self._browse_img))
        self._img_h5_lbl = QtWidgets.QLabel("  Dataset:"); self._img_h5_ed = QtWidgets.QLineEdit("exchange/data")
        self._img_h5_lbl.setVisible(False); self._img_h5_ed.setVisible(False)
        gf.addRow(self._img_h5_lbl, self._img_h5_ed)
        self._img_ed.textChanged.connect(lambda p: (
            self._img_h5_lbl.setVisible(is_h5(p)), self._img_h5_ed.setVisible(is_h5(p))))
        lb = QtWidgets.QPushButton("Load Image"); lb.clicked.connect(self._load_img)
        gf.addRow(lb)
        lv.addWidget(grp_img)

        grp_q = QtWidgets.QGroupBox("Q range (Å⁻¹)")
        qf = QtWidgets.QFormLayout(grp_q); qf.setSpacing(4)
        self._qmin = _fspin(0.0, 50.0, 3, 1.0); self._qmax = _fspin(0.0, 50.0, 3, 8.0)
        qf.addRow(_twocol("Qmin:", self._qmin, "Qmax:", self._qmax))
        self._qstep = _fspin(0.001, 1.0, 4, 0.02)
        qf.addRow("ΔQ:", self._qstep)
        lv.addWidget(grp_q)

        grp_r = QtWidgets.QGroupBox("r range (Å) + FT")
        rf = QtWidgets.QFormLayout(grp_r); rf.setSpacing(4)
        self._rmin = _fspin(0.0, 50.0, 2, 0.5); self._rmax = _fspin(1.0, 100.0, 2, 20.0)
        rf.addRow(_twocol("rmin:", self._rmin, "rmax:", self._rmax))
        self._rstep = _fspin(0.001, 1.0, 3, 0.02)
        rf.addRow("Δr:", self._rstep)
        self._window = QtWidgets.QComboBox(); self._window.addItems(["lorch", "none"])
        self._binning = QtWidgets.QComboBox(); self._binning.addItems(["hard", "polygon"])
        rf.addRow(_twocol("window:", self._window, "binning:", self._binning))
        lv.addWidget(grp_r)

        self._run_btn = S.primary_btn("Compute G(r)"); self._run_btn.setEnabled(False)
        self._run_btn.clicked.connect(self._run)
        lv.addWidget(self._run_btn)
        self._save_btn = QtWidgets.QPushButton("Save G(r)…"); self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._save)
        lv.addWidget(self._save_btn)
        lv.addStretch(1)
        root.addWidget(scroll)

        right = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        # Three stacked plots: I(Q)+bg  /  F(Q)  /  G(r)
        plots = QtWidgets.QWidget(); pv = QtWidgets.QVBoxLayout(plots); pv.setContentsMargins(0, 0, 0, 0)
        self._top = pg.PlotWidget(background="k")
        self._top.setLabel("left", "I(Q)"); self._top.setLabel("bottom", "Q (Å⁻¹)")
        self._top.showGrid(x=True, y=True, alpha=0.2); self._top.addLegend()
        self._c_iq = self._top.plot([], [], pen=pg.mkPen("#88ccff", width=2), name="I(Q)")
        self._c_bg = self._top.plot([], [], pen=pg.mkPen("#f0a030", width=1, style=QtCore.Qt.DashLine), name="background")
        pv.addWidget(self._top, stretch=1)
        self._mid = pg.PlotWidget(background="k")
        self._mid.setLabel("left", "F(Q)  [a.u.]"); self._mid.setLabel("bottom", "Q (Å⁻¹)")
        self._mid.showGrid(x=True, y=True, alpha=0.2)
        self._mid.setTitle("F(Q) = Q·(I/bg − 1)  (composition-free approx.)",
                           color="#aaa", size="9pt")
        self._c_fq = self._mid.plot([], [], pen=pg.mkPen("#ff8844", width=1.5))
        pv.addWidget(self._mid, stretch=1)
        self._bot = pg.PlotWidget(background="k")
        self._bot.setLabel("left", "G(r)"); self._bot.setLabel("bottom", "r (Å)")
        self._bot.showGrid(x=True, y=True, alpha=0.2)
        self._c_gr = self._bot.plot([], [], pen=pg.mkPen("#7CFC00", width=2))
        pv.addWidget(self._bot, stretch=1)
        right.addWidget(plots)
        self._log = LogPanel()
        right.addWidget(self._log)
        right.setStretchFactor(0, 5); right.setStretchFactor(1, 1)
        root.addWidget(right, stretch=1)

    def _browse_img(self):
        p = _browse(self, "Open frame", "Images (*.tif *.tiff *.h5 *.hdf5 *.ge*);;All (*)")
        if p: self._img_ed.setText(p)

    def _load_img(self):
        path = self._img_ed.text().strip()
        if not path or not Path(path).exists():
            QtWidgets.QMessageBox.warning(self, "Error", "Image not found."); return
        try:
            self._image = _load_image(path, data_loc=self._img_h5_ed.text().strip() or "exchange/data")
            self._log.append(f"Image loaded: {Path(path).name} {self._image.shape}")
            self._run_btn.setEnabled(self._result is not None)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Load error", str(e))

    def _run(self):
        if self._result is None or self._image is None:
            QtWidgets.QMessageBox.warning(self, "Missing", "Need calibration + image."); return
        if self._worker and self._worker.isRunning():
            return
        cfg = {
            "q_min": self._qmin.value(), "q_max": self._qmax.value(), "q_step": self._qstep.value(),
            "r_min": self._rmin.value(), "r_max": self._rmax.value(), "r_step": self._rstep.value(),
            "window": self._window.currentText(), "binning": self._binning.currentText(),
        }
        self._run_btn.setEnabled(False)
        self._log.append("─" * 40 + "\nComputing PDF…")
        self._worker = PDFWorker(self._result, self._image, None, self._mask, cfg, parent=self)
        self._worker.log_line.connect(self._log.append)
        self._worker.finished.connect(self._on_done)
        self._worker.failed.connect(self._on_fail)
        self._worker.start()

    def _on_done(self, d):
        self._run_btn.setEnabled(True); self._save_btn.setEnabled(True)
        self._last = d
        self._c_iq.setData(d["q"], d["Iq"])
        self._c_bg.setData(d["q"], d["background"])
        if "Fq" in d:
            self._c_fq.setData(d["q"], d["Fq"])
        self._c_gr.setData(d["r"], d["Gr"])
        self._log.append(f"Done — G(r): {len(d['r'])} points, "
                         f"max |G| = {np.nanmax(np.abs(d['Gr'])):.3g}")

    def _on_fail(self, msg):
        self._run_btn.setEnabled(True)
        self._log.append(f"\nERROR:\n{msg[:600]}")
        QtWidgets.QMessageBox.critical(self, "PDF failed", msg[:400])

    def _save(self):
        if not self._last: return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save G(r)", "Gr.gr", "Text (*.gr *.dat *.txt)")
        if not path: return
        d = self._last
        arr = np.column_stack([d["r"], d["Gr"], d["sigma_Gr"]])
        np.savetxt(path, arr, header="r(A)  G(r)  sigma", comments="#")
        self._log.append(f"Saved G(r): {path}")
        QtWidgets.QMessageBox.information(self, "Saved", f"G(r) saved:\n{path}")
