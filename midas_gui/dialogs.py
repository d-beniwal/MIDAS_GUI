"""Dialogs.  _SaveParamstestDialog ported verbatim from the v3 template."""
from __future__ import annotations

import json
from pathlib import Path

from PyQt5 import QtCore, QtWidgets

from .constants import DISTORTION_NAMES, DISTORTION_ISO, DISTORTION_PRESETS

_PANEL_LABELS = {"single": "Single detector", "ge1": "ge1", "ge2": "ge2",
                 "ge3": "ge3", "ge4": "ge4", "hydra_composite": "Hydra Overall"}


def show_error(parent, title: str, full_text: str, log=None, log_prefix: str = ""):
    """Critical-error dialog that never truncates ``full_text``.

    Qt's built-in "Show Details..." panel is scrollable, so the dialog's
    headline is a short summary (the traceback's last non-blank line) while
    the complete text is always available via ``setDetailedText``. ``log``,
    if given, is a widget with ``.append()`` (LogPanel/QTextEdit) or a plain
    ``callable(str)`` — the full text (with ``log_prefix``) is recorded there
    too, untruncated.
    """
    if log is not None:
        text = log_prefix + full_text
        (log.append if hasattr(log, "append") else log)(text)
    lines = [ln for ln in full_text.strip().splitlines() if ln.strip()]
    summary = (lines[-1] if lines else full_text)[:300]
    box = QtWidgets.QMessageBox(QtWidgets.QMessageBox.Critical, title, summary,
                                 QtWidgets.QMessageBox.Ok, parent)
    box.setDetailedText(full_text)
    box.exec_()


class DistortionRefineDialog(QtWidgets.QDialog):
    """Pick which of the 15 distortion coefficients to refine.

    Coefficients are grouped by η-fold (isotropic radial + folds 1..6).  Named
    preset buttons auto-select a coefficient set (see
    :data:`midas_gui.constants.DISTORTION_PRESETS`).  ``selected()`` returns the
    set of checked v2 coefficient names.
    """

    def __init__(self, selected=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Distortion parameters to refine")
        self.setMinimumWidth(360)
        selected = set(selected or [])

        layout = QtWidgets.QVBoxLayout(self)
        info = QtWidgets.QLabel(
            "Choose which distortion harmonics the calibration refines.  Use a "
            "preset to select a whole η-fold ladder, or tick coefficients "
            "individually.")
        info.setWordWrap(True)
        info.setStyleSheet("color:#bbb;font-size:11px;padding-bottom:6px;")
        layout.addWidget(info)

        # ── Preset buttons ──
        preset_row = QtWidgets.QHBoxLayout(); preset_row.setSpacing(4)
        preset_row.addWidget(QtWidgets.QLabel("Mode:"))
        for name, coeffs in DISTORTION_PRESETS.items():
            b = QtWidgets.QPushButton(name)
            b.setToolTip(f"Select: {', '.join(coeffs) if coeffs else '(none)'}")
            b.clicked.connect(lambda _=0, c=coeffs: self._apply_preset(c))
            preset_row.addWidget(b)
        preset_row.addStretch(1)
        layout.addLayout(preset_row)

        # ── Per-coefficient checkboxes grouped by η-fold ──
        self._boxes: dict = {}
        grid = QtWidgets.QGridLayout(); grid.setSpacing(4)
        groups = [("Isotropic (fold 0)", DISTORTION_ISO)]
        groups += [(f"Fold {k}", [f"a{k}", f"phi{k}"]) for k in range(1, 7)]
        for r, (title, names) in enumerate(groups):
            lbl = QtWidgets.QLabel(f"<b>{title}</b>")
            grid.addWidget(lbl, r, 0)
            col = 1
            for nm in names:
                cb = QtWidgets.QCheckBox(nm)
                cb.setChecked(nm in selected)
                self._boxes[nm] = cb
                grid.addWidget(cb, r, col)
                col += 1
        layout.addLayout(grid)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _apply_preset(self, coeffs):
        want = set(coeffs)
        for nm, cb in self._boxes.items():
            cb.setChecked(nm in want)

    def selected(self) -> set:
        """Set of checked coefficient names (v2 harmonic names)."""
        return {nm for nm, cb in self._boxes.items() if cb.isChecked()}


class _SaveParamstestDialog(QtWidgets.QDialog):
    """Single dialog exposing output path + optional template path.

    Leave the template blank for a self-contained file, or browse to an existing
    paramstest.txt to inject only the calibration geometry/distortion while
    keeping all other parameters verbatim.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Save paramstest.txt")
        self.setMinimumWidth(540)
        layout = QtWidgets.QVBoxLayout(self)

        info = QtWidgets.QLabel(
            "<b>Standalone</b> (no template): writes geometry + p0…p14 distortion "
            "from the calibration result only.  Scan / threshold / ring-number "
            "fields are left at safe defaults — fill them in before running "
            "FF reconstruction.<br><br>"
            "<b>From template</b>: injects <i>only</i> the refined Lsd, BC, "
            "tilts, and distortion into the chosen paramstest.txt; every other "
            "line (scan range, omega step, RingThresh, MinNrSpots, …) is "
            "carried verbatim.")
        info.setWordWrap(True)
        info.setStyleSheet("color:#bbb;font-size:11px;padding-bottom:8px;")
        layout.addWidget(info)

        form = QtWidgets.QFormLayout(); form.setSpacing(8)

        out_row = QtWidgets.QHBoxLayout()
        self._out_edit = QtWidgets.QLineEdit()
        self._out_edit.setPlaceholderText("paramstest.txt")
        out_row.addWidget(self._out_edit)
        b_out = QtWidgets.QPushButton("Browse…"); b_out.setFixedWidth(80)
        b_out.clicked.connect(self._browse_out)
        out_row.addWidget(b_out)
        form.addRow("Output file:", out_row)

        tmpl_row = QtWidgets.QHBoxLayout()
        self._tmpl_edit = QtWidgets.QLineEdit()
        self._tmpl_edit.setPlaceholderText(
            "(leave blank for standalone — only calibration parameters written)")
        tmpl_row.addWidget(self._tmpl_edit)
        b_tmpl = QtWidgets.QPushButton("Browse…"); b_tmpl.setFixedWidth(80)
        b_tmpl.clicked.connect(self._browse_tmpl)
        tmpl_row.addWidget(b_tmpl)
        form.addRow("Template (optional):", tmpl_row)

        layout.addLayout(form)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _browse_out(self):
        p, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save paramstest.txt", "paramstest.txt",
            "Text files (*.txt);;All files (*)")
        if p:
            self._out_edit.setText(p)

    def _browse_tmpl(self):
        p, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open template paramstest.txt", "",
            "Text files (*.txt);;All files (*)")
        if p:
            self._tmpl_edit.setText(p)

    def out_path(self) -> str:
        return self._out_edit.text().strip()

    def template_path(self) -> str:
        return self._tmpl_edit.text().strip()


class ProjectLoadDialog(QtWidgets.QDialog):
    """Shown right after File → Open Project…: lets the user choose which of
    the project's recorded attempts to populate into the Calibrate / Batch
    Integrate tabs, per panel (single-detector, or each present Hydra GE
    panel), rather than silently leaving the GUI untouched.

    One row per panel; each of the two "populate ___ from" checkboxes is
    paired with an attempt picker (defaulting to the latest) and disabled
    if that panel has no attempts of that kind. ``calib_selection()`` /
    ``integrate_selection()`` return ``{panel_key: attempt_ref}`` for the
    checked rows only.
    """

    def __init__(self, panels: dict, parent=None):
        """``panels`` is ``{panel_key: {"calib": [attempts], "integrate": [attempts]}}``,
        each attempt list as returned by ``project.list_attempts`` (newest first)."""
        super().__init__(parent)
        self.setWindowTitle("Populate GUI from project")
        self.setMinimumWidth(560)
        self._rows: dict = {}   # panel_key -> {"calib": (check, combo, refs), "integrate": (...)}

        layout = QtWidgets.QVBoxLayout(self)
        info = QtWidgets.QLabel(
            "This project has recorded calibration/integration attempts. Choose "
            "which ones to load into the Calibrate and Batch Integrate tabs so "
            "you can pick up where this project left off. Leave a box unchecked "
            "to leave that tab as-is.")
        info.setWordWrap(True)
        info.setStyleSheet("color:#bbb;font-size:11px;padding-bottom:8px;")
        layout.addWidget(info)

        grid = QtWidgets.QGridLayout(); grid.setHorizontalSpacing(12); grid.setVerticalSpacing(6)
        grid.addWidget(QtWidgets.QLabel("<b>Panel</b>"), 0, 0)
        grid.addWidget(QtWidgets.QLabel("<b>Calibrate</b>"), 0, 1)
        grid.addWidget(QtWidgets.QLabel("<b>Batch Integrate</b>"), 0, 2)

        for r, panel_key in enumerate(panels, start=1):
            entry = panels[panel_key]
            grid.addWidget(QtWidgets.QLabel(_PANEL_LABELS.get(panel_key, panel_key)), r, 0)
            self._rows[panel_key] = {}
            for col, kind in ((1, "calib"), (2, "integrate")):
                attempts = entry.get(kind) or []
                cell = QtWidgets.QHBoxLayout(); cell.setSpacing(4)
                check = QtWidgets.QCheckBox()
                combo = QtWidgets.QComboBox()
                for a in attempts:
                    combo.addItem(f"{a['name']}  ({a['timestamp_utc'][:19]})", a["ref"])
                has_attempts = bool(attempts)
                check.setChecked(has_attempts)
                check.setEnabled(has_attempts)
                combo.setEnabled(has_attempts)
                if not has_attempts:
                    combo.addItem("(none recorded)")
                cell.addWidget(check); cell.addWidget(combo, 1)
                w = QtWidgets.QWidget(); w.setLayout(cell)
                grid.addWidget(w, r, col)
                self._rows[panel_key][kind] = (check, combo)
        layout.addLayout(grid)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btns.button(QtWidgets.QDialogButtonBox.Ok).setText("Populate")
        btns.button(QtWidgets.QDialogButtonBox.Cancel).setText("Skip")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _selection(self, kind: str) -> dict:
        out = {}
        for panel_key, kinds in self._rows.items():
            check, combo = kinds[kind]
            if check.isChecked() and combo.isEnabled():
                out[panel_key] = combo.currentData()
        return out

    def calib_selection(self) -> dict:
        """``{panel_key: attempt_ref}`` for every checked Calibrate row."""
        return self._selection("calib")

    def integrate_selection(self) -> dict:
        """``{panel_key: attempt_ref}`` for every checked Batch Integrate row."""
        return self._selection("integrate")


class ProjectHistoryDialog(QtWidgets.QDialog):
    """Read-only browser for File → Project History… — every recorded
    Calibrate/Batch-Integrate attempt across all panels in one place, so
    inspecting a project's FAIR record doesn't require an external tool
    (``h5dump``/HDFView). Built entirely from ``project.py``'s existing
    read-side API (``discover_panels``/``list_attempts``/``read_attempt``) —
    no new provenance logic, no writes, so it can't drift from what
    ``append_*_attempt`` actually stores.
    """

    def __init__(self, project_path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Project History — {Path(project_path).name}")
        self.setMinimumSize(720, 480)
        self._project_path = project_path

        from . import project as _project
        rows = []
        load_error = None
        try:
            for panel_key in _project.discover_panels(project_path):
                for kind, label in (("calib", "Calibrate"), ("integrate", "Batch Integrate")):
                    for a in _project.list_attempts(project_path, panel_key, kind):
                        rows.append({
                            "panel": _PANEL_LABELS.get(panel_key, panel_key),
                            "kind": label,
                            "name": a["name"],
                            "timestamp": a.get("timestamp_utc", ""),
                            "ref": a["ref"],
                        })
        except Exception as e:
            load_error = str(e)
        rows.sort(key=lambda r: r["timestamp"], reverse=True)
        self._rows = rows

        layout = QtWidgets.QVBoxLayout(self)
        if load_error:
            err = QtWidgets.QLabel(f"Could not read this project's records:\n{load_error}")
            err.setStyleSheet("color:#e66;")
            err.setWordWrap(True)
            layout.addWidget(err)
        elif not rows:
            layout.addWidget(QtWidgets.QLabel("This project has no recorded attempts yet."))

        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self._table = QtWidgets.QTableWidget(len(rows), 4)
        self._table.setHorizontalHeaderLabels(["Panel", "Kind", "Attempt", "Timestamp (UTC)"])
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self._table.horizontalHeader().setStretchLastSection(True)
        for r, row in enumerate(rows):
            for c, key in enumerate(("panel", "kind", "name", "timestamp")):
                self._table.setItem(r, c, QtWidgets.QTableWidgetItem(str(row[key])))
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        splitter.addWidget(self._table)

        self._detail = QtWidgets.QPlainTextEdit()
        self._detail.setReadOnly(True)
        self._detail.setPlaceholderText(
            "Select an attempt above to see its full recorded parameters.")
        splitter.addWidget(self._detail)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter)

        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        if rows:
            self._table.selectRow(0)

    def _on_selection_changed(self) -> None:
        selected = self._table.selectionModel().selectedRows()
        if not selected:
            self._detail.setPlainText("")
            return
        row = self._rows[selected[0].row()]
        from . import project as _project
        try:
            meta = _project.read_attempt(self._project_path, row["ref"])
            self._detail.setPlainText(json.dumps(meta, indent=2, default=str))
        except Exception as e:
            self._detail.setPlainText(f"Could not read this attempt's metadata:\n{e}")
