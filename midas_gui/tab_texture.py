"""Tab 7 — Texture / Pole Figure.

Extracts a stereographic pole figure for a selected ring from one frame's
azimuthal intensity, plus the 1-D I(η) profile.  Export to POPLA .pol.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
from PyQt5 import QtCore, QtWidgets
import pyqtgraph as pg

from midas_gui.helpers import _load_image, _fspin, _twocol, _browse, _predict_ring_radii, is_h5
from midas_gui.constants import DEFAULT_NICKEL_FRAME0
from midas_gui.widgets import LogPanel
from midas_gui.workers import PoleFigureWorker
from midas_gui import style as S


class TextureTab(QtWidgets.QWidget):
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

    def set_calibration(self, result):
        self._result = result
        self._src_lbl.setText(f"From Tab 2: λ={result.wavelength_A:.5f} Å")
        # Populate ring dropdown from predicted radii
        self._ring_combo.clear()
        radii = _predict_ring_radii(result)
        max_r = max(result.NrPixelsY, result.NrPixelsZ)
        for i, rad in enumerate(radii):
            if 0 < rad < max_r:
                self._ring_combo.addItem(f"ring {i}  (R={rad:.1f} px)", rad)
        self._run_btn.setEnabled(self._image is not None and self._ring_combo.count() > 0)

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

        grp_img = QtWidgets.QGroupBox("Frame")
        gf = QtWidgets.QFormLayout(grp_img); gf.setSpacing(4)
        self._img_ed = QtWidgets.QLineEdit(DEFAULT_NICKEL_FRAME0); self._img_ed.setPlaceholderText("Sample frame…")
        gf.addRow("Image:", _frow(self._img_ed, self._browse_img))
        self._img_h5_lbl = QtWidgets.QLabel("  Dataset:"); self._img_h5_ed = QtWidgets.QLineEdit("exchange/data")
        self._img_h5_lbl.setVisible(False); self._img_h5_ed.setVisible(False)
        gf.addRow(self._img_h5_lbl, self._img_h5_ed)
        self._img_ed.textChanged.connect(lambda p: (
            self._img_h5_lbl.setVisible(is_h5(p)), self._img_h5_ed.setVisible(is_h5(p))))
        self._img_ed.returnPressed.connect(self._load_img)
        self._img_h5_ed.editingFinished.connect(
            lambda: self._image is not None and self._load_img())
        lv.addWidget(grp_img)

        grp_pf = QtWidgets.QGroupBox("Pole-figure parameters")
        pf = QtWidgets.QFormLayout(grp_pf); pf.setSpacing(4)
        self._ring_combo = QtWidgets.QComboBox()
        pf.addRow("Ring (hkl):", self._ring_combo)
        self._cap = _fspin(0.5, 50.0, 1, 4.0, "px")
        self._ebin = _fspin(0.5, 10.0, 1, 2.0, "°")
        pf.addRow(_twocol("± Δη(px):", self._cap, "η bin:", self._ebin))
        self._chi = _fspin(-180, 180, 1, 0.0, "°"); self._phi = _fspin(-180, 180, 1, 0.0, "°")
        pf.addRow(_twocol("χ:", self._chi, "φ:", self._phi))
        lv.addWidget(grp_pf)

        self._run_btn = S.primary_btn("Build pole figure"); self._run_btn.setEnabled(False)
        self._run_btn.clicked.connect(self._run)
        lv.addWidget(self._run_btn)
        self._save_btn = QtWidgets.QPushButton("Save .pol…"); self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._save)
        lv.addWidget(self._save_btn)
        lv.addStretch(1)
        root.addWidget(scroll)

        right = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        # Pole figure image
        self._pf_view = pg.PlotWidget(background="k")
        self._pf_view.setLabel("left", "β (pole declination)"); self._pf_view.setLabel("bottom", "α (azimuth)")
        self._pf_view.setTitle("Pole figure")
        self._pf_img = pg.ImageItem()
        self._pf_view.addItem(self._pf_img)
        try:
            self._pf_img.setColorMap(pg.colormap.get("inferno"))
        except Exception:
            pass
        right.addWidget(self._pf_view)
        # I(eta)
        self._eta_view = pg.PlotWidget(background="k")
        self._eta_view.setLabel("left", "I(η)"); self._eta_view.setLabel("bottom", "η (°)")
        self._eta_view.showGrid(x=True, y=True, alpha=0.2)
        self._c_eta = self._eta_view.plot([], [], pen=pg.mkPen("#88ccff", width=2))
        right.addWidget(self._eta_view)
        self._log = LogPanel()
        right.addWidget(self._log)
        right.setStretchFactor(0, 3); right.setStretchFactor(1, 2); right.setStretchFactor(2, 1)
        root.addWidget(right, stretch=1)

    def _browse_img(self):
        p = _browse(self, "Open frame", "Images (*.tif *.tiff *.h5 *.hdf5 *.ge*);;All (*)")
        if p: self._img_ed.setText(p); self._load_img()

    def _load_img(self):
        path = self._img_ed.text().strip()
        if not path or not Path(path).exists():
            QtWidgets.QMessageBox.warning(self, "Error", "Image not found."); return
        try:
            self._image = _load_image(path, data_loc=self._img_h5_ed.text().strip() or "exchange/data")
            self._log.append(f"Image loaded: {Path(path).name} {self._image.shape}")
            self._run_btn.setEnabled(self._result is not None and self._ring_combo.count() > 0)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Load error", str(e))

    def _run(self):
        if self._result is None or self._image is None or self._ring_combo.count() == 0:
            QtWidgets.QMessageBox.warning(self, "Missing", "Need calibration, image, and a ring."); return
        if self._worker and self._worker.isRunning():
            return
        cfg = {
            "ring_px": self._ring_combo.currentData(), "capture_px": self._cap.value(),
            "eta_bin": self._ebin.value(), "chi": self._chi.value(), "phi": self._phi.value(),
        }
        self._run_btn.setEnabled(False)
        self._log.append("─" * 40 + f"\nBuilding pole figure for R={cfg['ring_px']:.1f} px…")
        self._worker = PoleFigureWorker(self._result, self._image, None, self._mask, cfg, parent=self)
        self._worker.log_line.connect(self._log.append)
        self._worker.finished.connect(self._on_done)
        self._worker.failed.connect(self._on_fail)
        self._worker.start()

    def _on_done(self, d):
        self._run_btn.setEnabled(True); self._save_btn.setEnabled(True)
        self._last = d
        inten = d["intensity"]
        self._pf_img.setImage(np.nan_to_num(inten.T, nan=0.0))
        self._c_eta.setData(d["eta_axis"], d["i_eta"])
        self._log.append(f"Done — pole figure {inten.shape}, ring R={d['ring_px']:.1f} px.")

    def _on_fail(self, msg):
        self._run_btn.setEnabled(True)
        self._log.append(f"\nERROR:\n{msg[:600]}")
        QtWidgets.QMessageBox.critical(self, "Pole figure failed", msg[:400])

    def _save(self):
        if not self._last: return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save pole figure", "pole.pol",
                                                        "POPLA (*.pol);;Text (*.txt)")
        if not path: return
        try:
            import midas_integrate_v2 as m
            d = self._last
            m.texture.write_popla_pol(path, d["alpha"], d["beta"],
                                      np.nan_to_num(d["intensity"], nan=0.0), hkl=(1, 1, 1))
            self._log.append(f"Saved pole figure: {path}")
            QtWidgets.QMessageBox.information(self, "Saved", f"Pole figure saved:\n{path}")
        except Exception as e:
            self._log.append(f"Save error: {e}")
            # Fallback: plain CSV of the intensity grid
            np.savetxt(path, np.nan_to_num(self._last["intensity"], nan=0.0), delimiter=",")
            self._log.append(f"Saved as CSV instead: {path}")
