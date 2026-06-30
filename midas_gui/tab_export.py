"""Tab 8 — Results & Export hub.

Collects session state (calibration, mask) and provides a one-click export of
calibration.json, mask.tif, and a human-readable session summary.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import numpy as np
from PyQt5 import QtCore, QtWidgets

from midas_gui import style as S


class ExportTab(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._result = None
        self._mask: Optional[np.ndarray] = None
        self._build_ui()

    def set_calibration(self, result):
        self._result = result
        self._refresh()

    def set_mask_from_tab1(self, mask):
        self._mask = mask
        self._refresh()

    def _build_ui(self):
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8); root.setSpacing(8)

        left = QtWidgets.QWidget(); left.setFixedWidth(430)
        lv = QtWidgets.QVBoxLayout(left); lv.setSpacing(6)

        grp_exp = QtWidgets.QGroupBox("Export artifacts")
        ef = QtWidgets.QVBoxLayout(grp_exp); ef.setSpacing(4)
        self._chk_json = QtWidgets.QCheckBox("calibration.json"); self._chk_json.setChecked(True)
        self._chk_ps   = QtWidgets.QCheckBox("paramstest.txt (standalone)"); self._chk_ps.setChecked(True)
        self._chk_mask = QtWidgets.QCheckBox("mask.tif"); self._chk_mask.setChecked(True)
        self._chk_log  = QtWidgets.QCheckBox("session_summary.txt"); self._chk_log.setChecked(True)
        for c in (self._chk_json, self._chk_ps, self._chk_mask, self._chk_log):
            ef.addWidget(c)
        out_row = QtWidgets.QHBoxLayout(); out_row.setSpacing(3)
        self._out_ed = QtWidgets.QLineEdit(); self._out_ed.setPlaceholderText("Output directory…")
        out_row.addWidget(self._out_ed)
        b = QtWidgets.QPushButton("…"); b.setFixedWidth(28)
        b.clicked.connect(lambda: self._out_ed.setText(
            QtWidgets.QFileDialog.getExistingDirectory(self, "Output directory") or ""))
        out_row.addWidget(b)
        ef.addLayout(out_row)
        self._export_btn = S.primary_btn("Export all checked")
        self._export_btn.clicked.connect(self._export)
        ef.addWidget(self._export_btn)
        lv.addWidget(grp_exp)
        lv.addStretch(1)
        root.addWidget(left)

        right = QtWidgets.QWidget(); rv = QtWidgets.QVBoxLayout(right)
        rv.addWidget(QtWidgets.QLabel("<b>Session summary</b>"))
        self._summary = QtWidgets.QPlainTextEdit(); self._summary.setReadOnly(True)
        self._summary.setStyleSheet("font-family:monospace;font-size:11px")
        rv.addWidget(self._summary)
        copy_btn = QtWidgets.QPushButton("Copy summary to clipboard")
        copy_btn.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(self._summary.toPlainText()))
        rv.addWidget(copy_btn)
        root.addWidget(right, stretch=1)
        self._refresh()

    def _summary_text(self) -> str:
        lines = ["MIDAS GUI v2 — session summary", "=" * 40]
        r = self._result
        if r is not None:
            lines += [
                "Calibration:",
                f"  pipeline calibrant : {getattr(r, '_calibrant_name', '?')}",
                f"  Lsd                : {r.Lsd/1000:.4f} mm",
                f"  BC                 : ({r.BC_y:.2f}, {r.BC_z:.2f}) px",
                f"  tilts ty,tz        : {r.ty:.4f}, {r.tz:.4f} deg",
                f"  wavelength         : {r.wavelength_A:.5f} A",
                f"  pixel              : {r.pxY:.2f} um",
                f"  detector           : {r.NrPixelsY} x {r.NrPixelsZ}",
                f"  post-resid strain  : {getattr(r, 'post_residual_strain_uE', None)}",
            ]
            lap = getattr(r, "_laplace_sigma", None)
            if lap:
                lines.append("  Laplace 1-sigma    : " +
                             ", ".join(f"{k}={v:.3g}" for k, v in list(lap.items())[:6]))
        else:
            lines.append("Calibration: (none yet — run Tab 2)")
        lines.append("")
        if self._mask is not None:
            n = int(self._mask.sum())
            lines += ["Mask:", f"  bad pixels : {n:,} ({100*n/self._mask.size:.3f}%)"]
        else:
            lines.append("Mask: (none)")
        lines.append("")
        try:
            import midas_calibrate_v2, midas_integrate_v2
            lines += ["Provenance:",
                      f"  midas_calibrate_v2 : {getattr(midas_calibrate_v2,'__version__','?')}",
                      f"  midas_integrate_v2 : {getattr(midas_integrate_v2,'__version__','?')}"]
        except Exception:
            pass
        return "\n".join(lines)

    def _refresh(self):
        self._summary.setPlainText(self._summary_text())

    def _export(self):
        out = self._out_ed.text().strip()
        if not out:
            out = QtWidgets.QFileDialog.getExistingDirectory(self, "Output directory")
        if not out:
            return
        out = Path(out); out.mkdir(parents=True, exist_ok=True)
        written = []
        try:
            if self._chk_json.isChecked() and self._result is not None:
                d = {k: v for k, v in vars(self._result).items()
                     if not k.startswith("_") and not hasattr(v, "numpy")}
                d.pop("residual_corr_map", None); d.pop("iter_history", None)
                (out / "calibration.json").write_text(json.dumps(d, indent=2, default=str))
                written.append("calibration.json")
            if self._chk_ps.isChecked() and self._result is not None:
                self._write_paramstest(out / "paramstest.txt")
                written.append("paramstest.txt")
            if self._chk_mask.isChecked() and self._mask is not None:
                import tifffile
                tifffile.imwrite(str(out / "mask.tif"), self._mask)
                written.append("mask.tif")
            if self._chk_log.isChecked():
                (out / "session_summary.txt").write_text(self._summary_text())
                written.append("session_summary.txt")
            QtWidgets.QMessageBox.information(
                self, "Exported", "Wrote:\n  " + "\n  ".join(written) + f"\n\nto {out}")
        except Exception as e:
            import traceback
            QtWidgets.QMessageBox.critical(self, "Export failed", traceback.format_exc()[:500])

    def _write_paramstest(self, path):
        import math
        from midas_calibrate.params import CalibrationParams
        from midas_gui.constants import _SG, _LC
        r = self._result
        cal = getattr(r, "_calibrant_name", "CeO2")
        _V2V1 = {"iso_R2": "p2", "iso_R4": "p5", "iso_R6": "p4", "a1": "p7", "phi1": "p8",
                 "a2": "p0", "phi2": "p6", "a3": "p9", "phi3": "p10", "a4": "p1", "phi4": "p3",
                 "a5": "p11", "phi5": "p12", "a6": "p13", "phi6": "p14"}
        NY, NZ = r.NrPixelsY, r.NrPixelsZ
        pxY = float(r.pxY); pxZ = float(r.pxZ) if r.pxZ else pxY
        RhoD = math.sqrt(max(r.BC_y, NY - r.BC_y) ** 2 + max(r.BC_z, NZ - r.BC_z) ** 2)
        p = CalibrationParams(
            NrPixelsY=NY, NrPixelsZ=NZ, pxY=pxY, pxZ=pxZ, Lsd=r.Lsd,
            BC_y=r.BC_y, BC_z=r.BC_z, tx=r.tx, ty=r.ty, tz=r.tz,
            Wavelength=r.wavelength_A, SpaceGroup=_SG.get(cal, 225),
            LatticeConstant=_LC.get(cal, _LC["CeO2"]), RhoD=RhoD, MaxRingRad=RhoD * 0.97)
        for v2n, v1n in _V2V1.items():
            val = (r.distortion or {}).get(v2n)
            if val is not None:
                setattr(p, v1n, float(val))
        p.write(str(path))
