"""Per-panel calibration-source widget for the Batch Integrate tab's Hydra
mode.

``HydraBatchPanelCard`` holds everything specific to integrating ONE GE
panel: which geometry to use (the fit that just completed on the Calibrate
tab's Hydra page, or a browsed geometry file), the read-only display of that
geometry, and this panel's own run progress. Everything shared across all 4
panels (integration settings, corrections, monitor normalisation, output)
lives on ``HydraBatchPage`` instead — mirrors the split already established
between ``HydraCalibrationPage`` and ``HydraCalibPanelCard``.
"""
from __future__ import annotations

from pathlib import Path

from PyQt5 import QtCore, QtWidgets

from midas_gui.helpers import (
    _browse, _build_spec, spec_from_geometry_file, resolve_calibration_fields,
    make_calib_values_button)
from midas_gui import style as S


class HydraBatchPanelCard(QtWidgets.QWidget):
    """One GE panel's calibration source + values + run progress."""

    def __init__(self, panel_number: int, parent=None):
        super().__init__(parent)
        self.panel_number = panel_number
        self.result = None
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────

    def _build_ui(self):
        lv = QtWidgets.QVBoxLayout(self)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(8)

        # ── Calibration source ──
        cal = S.make_card(f"ge{self.panel_number} — Calibration source")
        src_row = QtWidgets.QHBoxLayout(); src_row.setSpacing(10)
        self._use_calib_btn = QtWidgets.QRadioButton("From Calibrate tab")
        self._use_file_btn = QtWidgets.QRadioButton("From file")
        self._use_calib_btn.setChecked(True)
        src_row.addWidget(self._use_calib_btn); src_row.addWidget(self._use_file_btn)
        src_row.addStretch(1)
        cal.body.addLayout(src_row)
        self._calib_src_lbl = QtWidgets.QLabel(
            f"(run Calibrate tab's Hydra fit for ge{self.panel_number} first)")
        self._calib_src_lbl.setStyleSheet(f"color:{S.MUTED};font-size:10px")
        self._calib_src_lbl.setWordWrap(True)
        cal.body.addWidget(self._calib_src_lbl)
        self._json_ed = QtWidgets.QLineEdit()
        self._json_ed.setPlaceholderText("calibration.json / paramstest.txt / .poni…")
        jr = QtWidgets.QHBoxLayout(); jr.setSpacing(4); jr.addWidget(self._json_ed, 1)
        bj = QtWidgets.QPushButton("…"); bj.setFixedWidth(30)
        bj.clicked.connect(lambda: self._json_ed.setText(
            _browse(self, f"Open ge{self.panel_number} calibration file",
                    "Calibration (*.json *.txt *.poni);;All (*)") or ""))
        jr.addWidget(bj)
        self._json_ed.textChanged.connect(
            lambda t: self._use_file_btn.setChecked(True) if t.strip() else None)
        cal.body.addLayout(jr)
        # "Calibration values" used to be an always-visible grid here — with
        # 4 of these panels stacked it cost even more space than the
        # single-detector tab's version; now a popup, opened on click,
        # showing the same fields — see helpers.make_calib_values_button.
        calib_view_btn = make_calib_values_button(self._calib_fields_in_use)
        cal.body.addWidget(calib_view_btn, 0, QtCore.Qt.AlignLeft)
        lv.addWidget(cal)

        # ── Run progress (this panel only) ──
        prog_card = S.make_card(f"ge{self.panel_number} — progress")
        self._prog = QtWidgets.QProgressBar(); self._prog.setRange(0, 100)
        prog_card.body.addWidget(self._prog)
        self._status_lbl = QtWidgets.QLabel("Idle")
        self._status_lbl.setStyleSheet(f"font-size:10px;color:{S.MUTED}")
        prog_card.body.addWidget(self._status_lbl)
        lv.addWidget(prog_card)

        lv.addStretch(1)

    # ── Calibration source ──────────────────────────────────────────

    def set_calibration(self, result):
        """A Hydra panel fit finished on the Calibrate tab — adopt it as
        this panel's geometry (mirrors ``BatchTab.set_calibration``)."""
        self.result = result
        self._calib_src_lbl.setText(
            f"From Calibrate tab: Lsd={result.Lsd/1000:.3f} mm  "
            f"λ={result.wavelength_A:.5f} Å  {result.NrPixelsY}×{result.NrPixelsZ} px")
        self._use_calib_btn.setChecked(True)

    def _calib_fields_in_use(self):
        """Resolve this panel's active geometry as a dict of display fields —
        or ``(None, note)`` if unavailable. Also backs the "View calibration"
        popup (see helpers.make_calib_values_button), called fresh each time
        it's opened."""
        return resolve_calibration_fields(
            self.result, self._use_file_btn.isChecked(), self._json_ed.text(),
            source_label=f"Calibrate tab (ge{self.panel_number})")

    def resolved_im_trans(self) -> tuple:
        """ImTransOpt codes from this panel's active calibration source — see
        ``BatchTab._resolved_im_trans`` (single-detector counterpart)."""
        fields, _ = self._calib_fields_in_use()
        return tuple(fields.get("im_trans") or []) if fields else ()

    def using_file(self) -> bool:
        return self._use_file_btn.isChecked()

    def file_path(self) -> str:
        return self._json_ed.text().strip()

    def resolved_spec(self, r_bin: float, e_bin: float, r_min=None, r_max=None):
        """Build an ``IntegrationSpec`` from whichever source is active —
        raises if neither a Calibrate-tab result nor a valid file is set.
        ``r_min``/``r_max`` — see ``helpers._build_spec``."""
        if not self._use_file_btn.isChecked():
            if self.result is None:
                raise RuntimeError(
                    f"ge{self.panel_number}: no calibration from the Calibrate tab. "
                    "Run its Hydra fit first.")
            return _build_spec(self.result, r_bin, e_bin, r_min=r_min, r_max=r_max)
        path = self._json_ed.text().strip()
        if not path or not Path(path).exists():
            raise FileNotFoundError(
                f"ge{self.panel_number}: calibration file not found: {path}")
        return spec_from_geometry_file(path, r_bin, e_bin, r_min=r_min, r_max=r_max)

    # ── Progress ─────────────────────────────────────────────────────

    def set_progress(self, done: int, total: int):
        self._prog.setValue(int(100 * done / total) if total else 0)
        self._status_lbl.setText(f"Integrated {done} / {total} frames")

    def set_status(self, text: str):
        self._status_lbl.setText(text)

    def reset_progress(self):
        self._prog.setValue(0)
        self._status_lbl.setText("Idle")

    # ── GUI state ────────────────────────────────────────────────────

    def state_widgets(self) -> dict:
        return {
            "use_calib_btn": self._use_calib_btn, "use_file_btn": self._use_file_btn,
            "json_ed": self._json_ed,
        }
