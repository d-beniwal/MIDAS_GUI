"""Tab — Solve Cell (work in progress, Phase 1).

Single detector panel, geometry supplied directly (no geometry-resolution
tiers): raw frames -> ingest -> diamond filter -> blind ab-initio index ->
free refine. See documentation/solve_cell_handoff.md for the full
specification this is Phase 1 of, and midas_gui/solve_cell/pipeline.py for
the actual computation -- this tab only builds cfg dicts, runs them on a
SolveCellWorker (one worker per stage, sequential), and renders the
JSON/CSV it gets back.

Panels: the left column is a QTabWidget of ``PanelCard``s, one per detector
panel -- each with its own calibration (loaded from a Calibrate-tab-written
file; Phase 1 takes geometry only from a loaded file, never typed in -- see
DECISIONS 2026-10-04), Data/Dark/Bright/Background/Mask (``DataLoaderPanel``),
and frame<->omega mapping. A single panel works exactly as before. When more
than one panel is configured (has its own calibration + data loaded), "Run
Ingest" pools them: each panel is ingested independently against its own
geometry (handoff §5.2), then every panel's own g-vectors are concatenated
into one combined set for Diamond Filter/Ab-initio/Refine downstream --
``solve_pipeline._stage_ingest_pooled`` does the actual work, see its
docstring for why plain concatenation is correct here. Diamond filter/
ab-initio/refine/index-remaining-spots themselves are unchanged and already
panel-agnostic -- they only ever see the combined spot table, however many
panels produced it. The rest of handoff §10 Phase 2 (geometry-resolution
tiers, self-cal) is still open future work.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PyQt5 import QtCore, QtWidgets

import matplotlib
from matplotlib.colors import Normalize

from midas_gui.helpers import (
    _fspin, _twocol, _NoScrollSpinBox, widgets_to_dict, apply_dict_to_widgets,
    is_h5, list_h5_1d_datasets, detect_omega_frame_range,
)
from midas_gui.widgets import DataLoaderPanel, ImageViewer, LogPanel
from midas_gui.pyvista_views import (
    PyVistaScatter3DView, SIZE_MODE_EQUAL, SIZE_MODE_INTENSITY,
)
from midas_gui.workers import SolveCellWorker
from midas_gui.panel_toggle import install_panel_toggles
from midas_gui.dialogs import show_error
from midas_gui.solve_cell import pipeline as solve_pipeline
from midas_gui.solve_cell import geometry_adapter as geom_adapter
from midas_gui.solve_cell import viz_products
from midas_gui.solve_cell_diagnostics import DiagnosticsView
from midas_gui.solve_cell_volume import ReciprocalVolumeView, VolumeBuildWorker
from midas_gui.solve_cell_inspector import SpotInspector
from midas_gui.solve_cell_contamination import ContaminationView
from midas_gui.solve_cell_compare import DomainCompareView
from midas_gui.solve_cell import domain_compare
from midas_gui.solve_cell import reciprocal_volume
from midas_gui import project
from midas_gui import style as S


def _tight2(lbl1, w1, lbl2, w2):
    """_twocol with a narrower (8 px) gap between the pairs, for the compact
    middle controls column."""
    h = _twocol(lbl1, w1, lbl2, w2)
    for i in range(h.count()):
        it = h.itemAt(i)
        if it.spacerItem() is not None and it.spacerItem().sizeHint().width() == 20:
            h.removeItem(it); h.insertSpacing(i, 6); break
    return h


def _left(w: QtWidgets.QWidget) -> QtWidgets.QHBoxLayout:
    """Row layout that keeps a width-capped widget flush left instead of
    letting a QFormLayout spanning row centre or stretch it."""
    h = QtWidgets.QHBoxLayout()
    h.setContentsMargins(0, 0, 0, 0)
    h.addWidget(w); h.addStretch(1)
    return h


# Detector-view preview arrays stored per panel in the project record.
_PREVIEW_ARRAYS = ("mask", "background_subtracted", "background_subtracted_max_projection")


class _VerticalTag(QtWidgets.QLabel):
    """Text rotated to read bottom-to-top, for the ribbon's "Panels" tag."""

    def sizeHint(self):
        fm = self.fontMetrics()
        return QtCore.QSize(fm.height() + 4, fm.horizontalAdvance(self.text()) + 8)

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, _event):
        from PyQt5 import QtGui
        p = QtGui.QPainter(self)
        p.setPen(QtGui.QColor(S.MUTED))
        p.translate(0, self.height())
        p.rotate(-90)
        p.drawText(QtCore.QRect(0, 0, self.height(), self.width()), QtCore.Qt.AlignCenter, self.text())


class PanelRibbon(QtWidgets.QFrame):
    """Slim vertical ribbon at the window's left edge: a "Panels" tag over
    one numbered button per detector panel (1, 2, 3, …) and a "+" button.
    Right-click a number to remove that panel."""

    panelSelected = QtCore.pyqtSignal(int)    # zero-based index
    addRequested = QtCore.pyqtSignal()
    removeRequested = QtCore.pyqtSignal(int)  # zero-based index

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(34)
        self.setObjectName("panelRibbon")
        self.setStyleSheet(
            f"#panelRibbon {{ border-right: 1px solid {S.MUTED}; }}"
            "#panelRibbon QToolButton { min-width:24px; max-width:24px; min-height:24px; "
            "max-height:24px; border:1px solid transparent; border-radius:3px }"
            f"#panelRibbon QToolButton:checked {{ background:{S.ACCENT}; color:white }}")
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(3, 6, 3, 6); v.setSpacing(4)
        tag = _VerticalTag("Panels")
        tag.setAlignment(QtCore.Qt.AlignHCenter)
        v.addWidget(tag, alignment=QtCore.Qt.AlignHCenter)
        self._btn_box = QtWidgets.QVBoxLayout(); self._btn_box.setSpacing(4)
        v.addLayout(self._btn_box)
        add = QtWidgets.QToolButton(); add.setText("+")
        add.setToolTip("Add another detector panel")
        add.clicked.connect(self.addRequested)
        v.addWidget(add, alignment=QtCore.Qt.AlignHCenter)
        v.addStretch(1)
        self._buttons: list = []
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)

    def set_count(self, n: int):
        while len(self._buttons) > n:
            b = self._buttons.pop()
            self._group.removeButton(b)
            self._btn_box.removeWidget(b)
            b.deleteLater()
        while len(self._buttons) < n:
            i = len(self._buttons)
            b = QtWidgets.QToolButton()
            b.setText(str(i + 1)); b.setCheckable(True)
            b.setToolTip(f"Panel {i + 1} (right-click to remove)")
            b.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
            b.clicked.connect(lambda _c=False, k=i: self.panelSelected.emit(k))
            b.customContextMenuRequested.connect(lambda _pos, k=i, btn=b: self._menu(k, btn))
            self._group.addButton(b)
            self._btn_box.addWidget(b, alignment=QtCore.Qt.AlignHCenter)
            self._buttons.append(b)

    def set_current(self, index: int):
        if 0 <= index < len(self._buttons):
            self._buttons[index].setChecked(True)

    def _menu(self, index: int, btn):
        m = QtWidgets.QMenu(self)
        act = m.addAction(f"Remove panel {index + 1}")
        if m.exec_(btn.mapToGlobal(btn.rect().bottomLeft())) is act:
            self.removeRequested.emit(index)


class PanelCard(QtWidgets.QWidget):
    """One detector panel's calibration + data + frame/omega mapping.

    ``geometry_cfg()`` returns the dict ``solve_pipeline._stage_ingest``
    expects under its ``geometry`` key; ``corrections_cfg()``/``mask_cfg()``
    pull straight from the embedded ``DataLoaderPanel``. The panel's number
    *is* its id (output folder ``panel_NN/``, ``panel_id`` column in the spot
    table); the owning tab keeps the numbers a gap-free 1, 2, 3, … sequence
    via :meth:`set_panel_id`.
    """

    # Emitted with this panel's number when its loaded-calibration link is clicked.
    calibrationClicked = QtCore.pyqtSignal(int)

    # "last frame idx" when the omega step is 0 -- no span can be derived, so
    # the range runs through the end of the stack (ingest clamps it).
    _LAST_IDX_OPEN = 1_000_000

    def __init__(self, panel_id: int, parent=None):
        super().__init__(parent)
        self.loader = DataLoaderPanel(mode="stack", hide_frame_field=True, compact_info=True)
        self.loader.setMinimumWidth(200)
        self._geometry: Optional[dict] = None
        self._calib_path: Optional[str] = None
        self._calib_info: Optional[dict] = None
        self._ome_field = ""
        self._panel_id = int(panel_id)
        self._build_ui()

    def _build_ui(self):
        lv = QtWidgets.QVBoxLayout(self)
        lv.setContentsMargins(0, 4, 0, 0); lv.setSpacing(6)

        grp_calib = QtWidgets.QGroupBox("Calibration")
        cv = QtWidgets.QVBoxLayout(grp_calib)
        self._load_calib_btn = QtWidgets.QPushButton("Load calibration…")
        self._load_calib_btn.setToolTip(
            "Load Lsd/BC/tilts/wavelength/pixel size/detector size from a "
            "calibration file written by the Calibrate tab (.json/.txt/.poni). "
            "Required before Ingest can run for this panel -- Phase 1 takes "
            "geometry only from a loaded calibration file, never typed in.")
        self._load_calib_btn.clicked.connect(self._load_calib_file)
        cv.addWidget(self._load_calib_btn)
        self._calib_link = QtWidgets.QPushButton("No calibration loaded")
        self._calib_link.setFlat(True)
        self._calib_link.setEnabled(False)
        self._calib_link.setCursor(QtCore.Qt.PointingHandCursor)
        self._calib_link.setStyleSheet(
            f"QPushButton {{ text-align:left; border:none; padding:0; color:{S.MUTED}; font-size:11px }}"
            f"QPushButton:enabled {{ color:{S.ACCENT}; text-decoration:underline }}")
        self._calib_link.clicked.connect(lambda: self.calibrationClicked.emit(self._panel_id))
        cv.addWidget(self._calib_link)
        lv.addWidget(grp_calib)

        lv.addWidget(self.loader)

        grp_ome = QtWidgets.QGroupBox("Frame ↔ omega")
        of = QtWidgets.QFormLayout(grp_ome); of.setSpacing(4)
        # Slightly smaller type keeps the rows inside the narrow left column.
        f = grp_ome.font(); f.setPointSizeF(f.pointSizeF() * 0.9); grp_ome.setFont(f)
        self._ome_start = _fspin(-7200.0, 7200.0, 3, 0.0)
        self._ome_start.setToolTip(
            "Omega (degrees) at 'first frame idx'. Every other kept frame's "
            "omega is this value plus (frame − first idx) × omega step.")
        self._ome_end = _fspin(-7200.0, 7200.0, 3, 0.0)
        self._ome_end.setToolTip(
            "Omega (degrees) at the last frame of the scan, as run. Together "
            "with start omega and omega step it fixes the last frame idx.")
        self._ome_step = _fspin(-10.0, 10.0, 4, 0.0)
        self._ome_step.setToolTip(
            "Degrees of rotation per frame, used to extend omega away from "
            "the first frame in both directions. Default: 0.0°.")
        self._ome_first_idx = _NoScrollSpinBox(); self._ome_first_idx.setRange(0, 1_000_000)
        self._ome_first_idx.setToolTip(
            "Loaded-frame index that carries 'start omega'. Frames before it "
            "have no defined omega and are dropped, not extrapolated. "
            "Default: 0 (first loaded frame).")
        self._ome_first_btn = QtWidgets.QPushButton("first frame idx:")
        self._ome_first_btn.setFlat(True)
        self._ome_first_btn.setCursor(QtCore.Qt.PointingHandCursor)
        self._ome_first_btn.setStyleSheet(
            f"QPushButton {{ text-align:left; border:none; padding:0; color:{S.ACCENT}; "
            "text-decoration:underline }")
        self._ome_first_btn.setToolTip(
            "Click to auto-detect the first frame idx from the file's own "
            "per-frame omega values (pick the omega field in the dialog).")
        self._ome_first_btn.clicked.connect(self._auto_detect_first_idx)
        self._ome_last_lbl = QtWidgets.QLabel()
        self._ome_last_lbl.setWordWrap(True)
        for w in (self._ome_start, self._ome_end, self._ome_step, self._ome_first_idx):
            w.setFixedWidth(78)
        of.addRow("start omega:", self._ome_start)
        of.addRow("end omega:", self._ome_end)
        of.addRow("omega step:", self._ome_step)
        of.addRow(self._ome_first_btn, self._ome_first_idx)
        of.addRow("last frame idx:", self._ome_last_lbl)
        note = QtWidgets.QLabel(
            "Angles in degrees. omega(frame) = start omega + (frame − first idx) "
            "× step. Frames outside [first, last] are excluded from ingest.")
        note.setWordWrap(True); note.setStyleSheet(f"color:{S.MUTED};font-size:11px")
        of.addRow(note)
        for w in (self._ome_start, self._ome_end, self._ome_step, self._ome_first_idx):
            w.valueChanged.connect(self._update_last_idx)
        self._update_last_idx()

        lv.addWidget(grp_ome)
        lv.addStretch(1)
        self.loader.dataChanged.connect(self._update_last_idx)

    def _last_idx(self) -> int:
        """Last frame idx implied by start/end omega, step and first idx
        (closed span, matching the auto-detect's in-range test)."""
        step = self._ome_step.value()
        first = self._ome_first_idx.value()
        if step == 0.0:
            return self._LAST_IDX_OPEN
        n = int(round((self._ome_end.value() - self._ome_start.value()) / step))
        return first + max(n, 0)

    def omega_span_without_step(self) -> bool:
        return self._ome_step.value() == 0.0 and self._ome_end.value() != self._ome_start.value()

    def _update_last_idx(self, *_args):
        step = self._ome_step.value()
        n_loaded = self.loader.n_frames()
        if step == 0.0:
            text = "end of stack (set omega step)"
        else:
            span = (self._ome_end.value() - self._ome_start.value()) / step
            last = self._last_idx()
            text = str(last)
            if span < -0.5:
                text += "  ⚠ end omega is before start omega for this step sign"
            elif n_loaded and last > n_loaded - 1:
                text += f"  (stack ends at {n_loaded - 1})"
        self._ome_last_lbl.setText(text)

    def _omega_fields(self) -> list:
        path = self.loader.data_path()
        if path and is_h5(path) and Path(path).exists():
            try:
                return [name for name, _n in list_h5_1d_datasets(path)]
            except Exception:
                pass
        return []

    def _auto_detect_first_idx(self):
        path = self.loader.data_path()
        if not path or not is_h5(path):
            QtWidgets.QMessageBox.warning(
                self, "Solve Cell", "Load an HDF5 data file for this panel first.")
            return
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Auto-detect first frame idx")
        dv = QtWidgets.QVBoxLayout(dlg)
        form = QtWidgets.QFormLayout()
        combo = QtWidgets.QComboBox(); combo.setEditable(True)
        combo.setToolTip(
            "HDF5 field holding one omega value per raw frame, e.g. "
            "entry/instrument/sample_stage/omega. Omega-looking names are listed first.")
        combo.addItems(self._omega_fields())
        if self._ome_field:
            i = combo.findText(self._ome_field)
            if i >= 0:
                combo.setCurrentIndex(i)
            else:
                combo.setEditText(self._ome_field)
        form.addRow("omega field:", combo)
        dv.addLayout(form)
        info = QtWidgets.QLabel(
            "Sets first frame idx to the first loaded frame whose omega lies "
            "between start omega and end omega.")
        info.setWordWrap(True); info.setStyleSheet(f"color:{S.MUTED};font-size:11px")
        dv.addWidget(info)
        bb = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        dv.addWidget(bb)
        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return
        self._ome_field = combo.currentText().strip()
        self.detect_first_idx(self._ome_field)

    def detect_first_idx(self, field: str) -> bool:
        """Fill 'first frame idx' from *field*'s per-frame omega values."""
        path = self.loader.data_path()
        if not path or not is_h5(path):
            QtWidgets.QMessageBox.warning(
                self, "Solve Cell", "Load an HDF5 data file for this panel first.")
            return False
        if not field:
            QtWidgets.QMessageBox.warning(self, "Solve Cell", "Pick an omega field first.")
            return False
        try:
            first_idx, _last, _deg = detect_omega_frame_range(
                path, field, self._ome_start.value(), self._ome_end.value())
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Detect failed", str(e)); return False
        self._ome_field = field
        self._ome_first_idx.setValue(first_idx)
        return True

    def set_panel_id(self, panel_id: int):
        self._panel_id = int(panel_id)

    def _load_calib_file(self):
        from midas_gui.constants import DEFAULT_CALIB_FILE
        start = DEFAULT_CALIB_FILE if Path(DEFAULT_CALIB_FILE).exists() else ""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load calibration file", start,
            "Calibration (*.json *.txt *.poni);;All files (*)")
        if not path:
            return
        self._load_calib_from_path(path)

    def _load_calib_from_path(self, path: str) -> bool:
        """Shared by the file-dialog handler above and :meth:`set_state`'s
        project restore -- both end up loading a calibration file the same
        way. Returns True on success (and remembers *path* so a project save
        can reload it later)."""
        from midas_gui.helpers import geometry_fields_from_file
        try:
            g = geometry_fields_from_file(path)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Load failed", str(e)); return False

        nry = int(g["NrPixelsY"]) if g.get("NrPixelsY") else None
        nrz = int(g["NrPixelsZ"]) if g.get("NrPixelsZ") else None
        self._geometry = {
            "Lsd": float(g["Lsd"]), "BC_y": float(g["BC_y"]), "BC_z": float(g["BC_z"]),
            "ty": float(g.get("ty") or 0.0), "tz": float(g.get("tz") or 0.0),
            "wavelength_A": float(g["wavelength_A"]), "px_um": float(g["pxY"]),
            "nrpixels_y": nry, "nrpixels_z": nrz,
            # The calibration's BC/tilts live in the frame AFTER these codes;
            # ingest must carry them (pipeline.check_im_trans).
            "im_trans": [int(c) for c in g.get("im_trans") or []],
            "im_trans_in_file": bool(g.get("im_trans_in_file", False)),
        }
        self._calib_path = path

        tx = float(g.get("tx") or 0.0)
        self._calib_info = {
            "file": Path(path).name, "source": "file", "tx": tx,
            "notes": ([] if self._geometry["im_trans"] or self._geometry["im_trans_in_file"]
                      else ["File does not declare im_trans -- assuming none."])
                     + ([f"File's tx={tx:.3f}° is not used by the Phase 1 pipeline (fixed at 0)."]
                        if tx != 0.0 else []),
        }
        self._show_calib_link()
        return True

    def _show_calib_link(self):
        info = self._calib_info or {}
        self._calib_link.setText(info.get("file") or "calibration")
        self._calib_link.setToolTip("Click to show this calibration's parameters.")
        self._calib_link.setEnabled(True)

    def calibration_summary(self) -> Optional[list]:
        """``(label, value)`` rows describing the loaded calibration, for the
        parameters popup; None when nothing is loaded."""
        g = self._geometry
        if g is None:
            return None
        info = self._calib_info or {}
        rows = [
            ("file", info.get("file") or "—"),
            ("source", "calibration file" if info.get("source", "file") == "file"
             else "project's embedded copy"),
            ("wavelength (Å)", f"{g['wavelength_A']:.5f}"),
            ("pixel size (µm)", f"{g['px_um']:.3f}"),
            ("Lsd (µm)", f"{g['Lsd']:.2f}"),
            ("BC y (px)", f"{g['BC_y']:.3f}"),
            ("BC z (px)", f"{g['BC_z']:.3f}"),
            ("ty (°)", f"{g.get('ty', 0.0):.4f}"),
            ("tz (°)", f"{g.get('tz', 0.0):.4f}"),
        ]
        if info.get("tx"):
            rows.append(("tx (°, file only)", f"{info['tx']:.4f}"))
        ny, nz = g.get("nrpixels_y"), g.get("nrpixels_z")
        rows.append(("detector (px)", f"{ny} × {nz}" if ny and nz else "—"))
        rows.append(("im_trans", str(g["im_trans"]) if g.get("im_trans") else "none"))
        for n in info.get("notes", []):
            rows.append(("note", n))
        return rows

    def panel_id(self) -> int:
        return self._panel_id

    def has_calibration(self) -> bool:
        return self._geometry is not None

    def geometry_cfg(self) -> dict:
        if self._geometry is None:
            raise RuntimeError("No calibration file loaded for this panel.")
        cfg = dict(self._geometry)
        cfg.update({
            "omega_ref_frame_idx": self._ome_first_idx.value(),
            "omega_ref_deg": self._ome_start.value(),
            "omega_last_frame_idx": self._last_idx(),
            "omega_step_deg": self._ome_step.value(),
        })
        return cfg

    def corrections_cfg(self) -> dict:
        return {
            "dark": self.loader.dark(), "bright": self.loader.bright(),
            "bright_mode": self.loader.bright_mode(), "background": self.loader.background(),
        }

    # ── calibration record (project analysis provenance) ────────────

    _CALIB_TEXT_MAX_BYTES = 1_000_000

    def calibration_record(self) -> dict:
        """Everything needed to rerun this panel's geometry without the
        original calibration file: its path (hashed by the project writer),
        the parsed geometry, and the file's own text when still readable."""
        rec = {"path": self._calib_path, "geometry": dict(self._geometry) if self._geometry else None}
        if self._calib_path:
            try:
                p = Path(self._calib_path)
                if p.is_file() and p.stat().st_size <= self._CALIB_TEXT_MAX_BYTES:
                    rec["file_name"] = p.name
                    rec["file_text"] = p.read_text(errors="replace")
            except OSError:
                pass
        return rec

    def restore_calibration_record(self, rec: dict) -> bool:
        """Adopt a recorded calibration whose original file is not available
        (e.g. a project opened on another machine). Returns False if the
        record carries no geometry."""
        geom = (rec or {}).get("geometry")
        if not geom:
            return False
        self._geometry = dict(geom)
        self._calib_path = rec.get("path")
        self._calib_info = {"file": rec.get("file_name") or "embedded calibration",
                            "source": "embedded", "tx": 0.0,
                            "notes": ["The original file was not found, so this embedded copy is used."]}
        self._show_calib_link()
        return True

    # ── GUI state (project save/restore) ───────────────────────────

    def _state_widgets(self) -> dict:
        return {
            "ome_start": self._ome_start, "ome_end": self._ome_end,
            "ome_step": self._ome_step, "ome_first_idx": self._ome_first_idx,
        }

    def get_state(self) -> dict:
        state = {"fields": widgets_to_dict(self._state_widgets()), "loader": self.loader.get_state()}
        state["fields"]["ome_field"] = self._ome_field
        if self._calib_path:
            state["calib_path"] = self._calib_path
        return state

    def set_state(self, state: dict):
        if not state:
            return
        fields = dict(state.get("fields", {}))
        # Projects saved before the start/end omega layout: "its omega" was
        # the omega at first idx, and the span was given as a last frame idx.
        if "ome_start" not in fields and "ome_first_deg" in fields:
            fields["ome_start"] = fields["ome_first_deg"]
            last = fields.get("ome_last_idx")
            step = fields.get("ome_step") or 0.0
            if last is not None and last < self._LAST_IDX_OPEN and step:
                fields["ome_end"] = (fields["ome_first_deg"]
                                     + (last - fields.get("ome_first_idx", 0)) * step)
        apply_dict_to_widgets(self._state_widgets(), fields)
        self._ome_field = str(fields.get("ome_field") or fields.get("ome_field_combo") or "")
        self._update_last_idx()
        self.loader.set_state(state.get("loader") or {})
        calib_path = state.get("calib_path")
        if calib_path and Path(calib_path).exists():
            self._load_calib_from_path(calib_path)


class SolveCellTab(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: Optional[SolveCellWorker] = None
        self._selected_spot_id = None
        self._spot_rows_by_id = {}
        self._recip_has_data = False
        self._spots_df = None
        self._flagged_df = None
        self._candidate_df = None
        self._ab_initio_raw = None
        self._g_2pi: Optional[np.ndarray] = None
        self._sigma_g_used: Optional[float] = None
        self._panel_dir: Optional[Path] = None
        self._pooled_dir: Optional[Path] = None
        # Multi-domain bookkeeping (round 1's refine result becomes "Domain 1";
        # "Index Remaining Spots" appends further domains from whatever is
        # still unclaimed). _claimed_index is the union, across every domain,
        # of the candidate_df row labels that domain has taken -- a later
        # round's leftover pool is always candidate_df minus this, so no two
        # domains can ever claim the same spot (handoff §5.6 step 2).
        self._domains: list[dict] = []
        self._claimed_index = None
        self._round1_domain_index: Optional[int] = None
        self._leftover_pending_df = None
        self._leftover_g_2pi: Optional[np.ndarray] = None
        self._pending_leftover_ab = None
        self._panels: dict[int, PanelCard] = {}
        self._ingest_preview: Optional[dict] = None
        # Per-panel Detector-view preview (Calculated background/mask), keyed
        # by panel_id -- lets switching the active panel tab after a pooled
        # ingest show THAT panel's own preview rather than whichever panel's
        # ingest happened to run last. _ingest_preview above is kept as a
        # legacy single-value fallback (still settable directly, as existing
        # tests do).
        self._ingest_preview_by_panel: dict[int, dict] = {}
        self._det_view_framed_for = None
        self._det_view_shape = None
        self._ingest_source_panel_id = None
        self._project_ctx = None
        # Project record bookkeeping (see flush_project_record): the as-run
        # description of the last ingest (+ the user masks it ran with), each
        # later stage's parameter snapshot, which record sections still need
        # writing, and which
        # project file the in-memory results were last written to / read from.
        self._sc_ingest_meta: Optional[dict] = None
        self._sc_user_masks: dict = {}
        self._sc_stage_meta: dict = {}
        self._sc_dirty: set = set()
        self._sc_record_path: Optional[str] = None
        # Cached visualization products (viz_products.*), keyed by product
        # name; ``_viz_unsaved`` are those computed since the project last
        # stored them. Derived from ingest+index, so a new ingest drops them.
        self._viz_products: dict = {}
        self._viz_unsaved: set = set()
        self._vol_worker: Optional[VolumeBuildWorker] = None
        self._build_ui()
        self._refresh_detector_preview()

    # ── UI ──────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6); root.setSpacing(0)
        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        split.setChildrenCollapsible(False); split.setHandleWidth(6)
        # Splitter on top, bottom ribbon (minimised Data / Controls tabs) below.
        body = QtWidgets.QWidget()
        self._body_lay = QtWidgets.QVBoxLayout(body)
        self._body_lay.setContentsMargins(0, 0, 0, 0); self._body_lay.setSpacing(2)
        self._body_lay.addWidget(split, 1)
        root.addWidget(body)

        # ── LEFT EDGE: vertical "Panels" ribbon ──
        self._panel_ribbon = PanelRibbon()
        self._panel_ribbon.panelSelected.connect(self._on_ribbon_selected)
        self._panel_ribbon.addRequested.connect(lambda: self._add_panel())
        self._panel_ribbon.removeRequested.connect(self._close_panel_tab)
        root.insertWidget(0, self._panel_ribbon)

        # ── LEFT: project folder + the selected panel's card ──
        left_scroll = QtWidgets.QScrollArea()
        left_scroll.setWidgetResizable(True); left_scroll.setMinimumWidth(208)
        left = QtWidgets.QWidget(); lv = QtWidgets.QVBoxLayout(left); lv.setSpacing(6)
        lv.setContentsMargins(4, 4, 4, 4)
        left_scroll.setWidget(left)

        grp_proj = QtWidgets.QGroupBox("Project folder")
        pf = QtWidgets.QHBoxLayout(grp_proj)
        self._proj_ed = QtWidgets.QLineEdit()
        self._proj_ed.setPlaceholderText("Where panel_NN/ and pooled/ outputs are written (optional)")
        self._proj_ed.setToolTip(
            "Folder where each panel's panel_NN/ outputs and (if pooled) the "
            "pooled/ combined outputs are written as stages run. Optional -- "
            "default is blank, meaning results stay in memory only and "
            "nothing is written to disk.")
        self._proj_ed.textChanged.connect(self._proj_ed.setToolTip)
        pf.addWidget(self._proj_ed, stretch=1)
        proj_btn = QtWidgets.QPushButton("Browse…")
        proj_btn.clicked.connect(self._browse_project_dir)
        pf.addWidget(proj_btn)
        lv.addWidget(grp_proj)

        self._panel_tabs = QtWidgets.QStackedWidget()
        self._panel_tabs.currentChanged.connect(self._panel_ribbon.set_current)
        self._add_panel()
        lv.addWidget(self._panel_tabs, stretch=1)
        split.addWidget(left_scroll)

        # ── MIDDLE: stage parameters + run buttons ──
        mid_scroll = QtWidgets.QScrollArea()
        mid_scroll.setWidgetResizable(True); mid_scroll.setMinimumWidth(345)
        mid = QtWidgets.QWidget(); mv = QtWidgets.QVBoxLayout(mid); mv.setSpacing(6)
        mid_scroll.setWidget(mid)

        grp_ing = QtWidgets.QGroupBox("1. Ingest")
        iv = QtWidgets.QFormLayout(grp_ing); iv.setSpacing(4)
        # The tuning parameters live in a dialog opened by "Parameters…" (the
        # widgets persist while it is closed, so get_state/_panel_ingest_cfg
        # read them as before); only Run + Nr. of CPUs stay on the panel.
        self._ingest_params_dlg = QtWidgets.QDialog(self)
        self._ingest_params_dlg.setWindowTitle("Ingest parameters")
        pv = QtWidgets.QFormLayout(self._ingest_params_dlg); pv.setSpacing(6)
        self._low_count = _fspin(0.0, 1e6, 2, solve_pipeline.LOW_COUNT_THRESHOLD_DEFAULT)
        self._low_count.setToolTip(
            "midas_defect's own default (20) can mask 80-99%+ of a "
            "single-crystal-in-DAC chip with no diffuse baseline -- sweep "
            "this on your own data before trusting the default of 0.")
        self._mask_grow = _NoScrollSpinBox(); self._mask_grow.setRange(0, 50)
        self._mask_grow.setValue(solve_pipeline.MASK_GROW_DEFAULT)
        self._mask_grow.setToolTip(
            "Binary-dilate the finished mask by this many pixels. Pixels "
            "bordering a module gap have anomalous response and, after "
            "background subtraction, produce spurious negative structures -- "
            "growing removes them at negligible cost to real signal. "
            f"Default: {solve_pipeline.MASK_GROW_DEFAULT} px.")
        pv.addRow(_twocol("low_count_threshold:", self._low_count, "mask grow:", self._mask_grow))
        self._blob_thresh = _fspin(0.0, 1e7, 2, solve_pipeline.BLOB_THRESHOLD_DEFAULT)
        self._blob_thresh.setToolTip(
            "Intensity cut above which a background-subtracted voxel counts "
            "as part of a candidate blob. Also used inside the sector-"
            "candidate search's own scoring pass. "
            f"Default: {solve_pipeline.BLOB_THRESHOLD_DEFAULT:g}.")
        self._blob_minvol = _NoScrollSpinBox(); self._blob_minvol.setRange(1, 100000)
        self._blob_minvol.setValue(solve_pipeline.BLOB_MIN_VOL_DEFAULT)
        self._blob_minvol.setToolTip(
            "Minimum connected voxel count (26-connectivity across row/col/"
            "frame) for a blob to survive -- smaller connected components "
            "are dropped as noise. "
            f"Default: {solve_pipeline.BLOB_MIN_VOL_DEFAULT} voxels.")
        pv.addRow(_twocol("blob threshold:", self._blob_thresh, "blob min_vol:", self._blob_minvol))
        self._split_ratio = _fspin(1.0, 100.0, 2, solve_pipeline.SPLIT_RATIO_DEFAULT)
        self._split_ratio.setToolTip(
            "Watershed sensitivity for splitting one connected blob into "
            "multiple sub-peaks: how far a local maximum must rise above its "
            "saddle, as a multiple of the saddle (scale-free, via log-"
            "intensity h-maxima). Values <=1 disable splitting (plain "
            "local-maximum seeding). "
            f"Default: {solve_pipeline.SPLIT_RATIO_DEFAULT:g}.")
        self._gap_bridge_on = QtWidgets.QCheckBox("gap_bridge:")
        self._gap_bridge_on.setChecked(True)
        self._gap_bridge_on.setToolTip(
            "Enable gap bridging. Only needed for tiled detectors with gaps "
            "between sensors; uncheck for a single-sensor detector (passes "
            "gap_bridge=0, i.e. no bridging).")
        self._gap_bridge = _NoScrollSpinBox(); self._gap_bridge.setRange(0, 1000)
        self._gap_bridge.setValue(solve_pipeline.GAP_BRIDGE_DEFAULT)
        self._gap_bridge.setToolTip(
            "Size (pixels) of a binary-closing kernel used to reconnect a "
            "real feature that a detector module gap cut in two. Only "
            "bridges where signal is masked and continuous on both sides -- "
            "nothing is invented in blank regions. <=1 disables bridging. "
            f"Default: {solve_pipeline.GAP_BRIDGE_DEFAULT} px.")
        self._gap_bridge_on.toggled.connect(self._gap_bridge.setEnabled)
        pv.addRow(_twocol("split_ratio:", self._split_ratio, self._gap_bridge_on, self._gap_bridge))
        self._sector_candidates_ed = QtWidgets.QLineEdit(
            ",".join(str(n) for n in solve_pipeline.SECTOR_CANDIDATES_DEFAULT))
        self._sector_candidates_ed.setToolTip(
            "Azimuth-sector counts choose_sectors() grid-searches to pick a "
            "background model -- each candidate is a full per-frame pass "
            "over the whole stack (the dominant cost of Ingest at full "
            "detector resolution). Once a prior run's log line "
            "('choose_sectors: n_sectors=N') tells you what a given "
            "panel/geometry wants, narrow this to just that one value to "
            "skip the other candidates on every later ingest. Blank or "
            "unparseable falls back to the default search. "
            f"Default: {', '.join(str(n) for n in solve_pipeline.SECTOR_CANDIDATES_DEFAULT)}.")
        pv.addRow("sector candidates:", self._sector_candidates_ed)
        _close = QtWidgets.QPushButton("Close")
        _close.clicked.connect(self._ingest_params_dlg.accept)
        pv.addRow(_close)
        self._ingest_params_btn = QtWidgets.QPushButton("Parameters…")
        self._ingest_params_btn.setToolTip(
            "Open the ingest parameters (mask, blob finding, gap bridging, "
            "sector candidates). Defaults are pre-filled.")
        self._ingest_params_btn.clicked.connect(self._ingest_params_dlg.exec_)
        self._ingest_btn = S.primary_btn("Run Ingest")
        self._ingest_btn.clicked.connect(self._run_ingest)
        self._ingest_max_workers = _NoScrollSpinBox()
        self._ingest_max_workers.setRange(1, max(1, os.cpu_count() or 1))
        self._ingest_max_workers.setValue(self._ingest_max_workers.maximum())
        self._ingest_max_workers.setMaximumWidth(56)
        self._ingest_max_workers.setToolTip(
            "Thread-pool width for ingest's sector-search/blob-finding hot "
            "loops (perf_solve_cell's opt-in parallelism). 1 = serial; "
            "default is every CPU detected on this machine. Output is "
            "bit-identical at every value -- "
            "higher only trades CPU for wall-clock time.")
        ingest_row = QtWidgets.QHBoxLayout()
        ingest_row.setSpacing(6)
        ingest_row.setContentsMargins(0, 0, 0, 0)
        self._ingest_btn.setMaximumWidth(100)
        ingest_row.addWidget(self._ingest_btn)
        ingest_row.addWidget(self._ingest_params_btn)
        ingest_row.addStretch(1)
        cpu_row = QtWidgets.QHBoxLayout()
        cpu_row.setSpacing(6); cpu_row.setContentsMargins(0, 0, 0, 0)
        cpu_lbl = QtWidgets.QLabel("Nr. of CPUs:")
        cpu_lbl.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        cpu_row.addWidget(cpu_lbl)
        cpu_row.addWidget(self._ingest_max_workers)
        cpu_row.addStretch(1)
        iv.addRow(ingest_row)
        iv.addRow(cpu_row)
        self._ingest_status = QtWidgets.QLabel("—")
        self._ingest_status.setStyleSheet(f"color:{S.MUTED};font-size:11px")
        self._ingest_status.setWordWrap(True)
        iv.addRow(self._ingest_status)
        mv.addWidget(grp_ing)

        grp_dia = QtWidgets.QGroupBox("2. Diamond filter")
        dv = QtWidgets.QFormLayout(grp_dia); dv.setSpacing(4)
        self._diamond_a = _fspin(1.0, 10.0, 4, solve_pipeline.DIAMOND_A_ANGSTROM_DEFAULT, "Å")
        self._diamond_a.setToolTip(
            f"Diamond anvil cubic cell edge, used to predict where diamond's "
            f"own powder rings fall in 2θ so those spots can be flagged and "
            f"excluded from indexing — advanced override. "
            f"Default: {solve_pipeline.DIAMOND_A_ANGSTROM_DEFAULT:g} Å.")
        self._contam_tol = _fspin(0.001, 5.0, 4, solve_pipeline.CONTAM_TOL_DEG_DEFAULT, "°")
        self._contam_tol.setToolTip(
            f"2θ window (degrees) around each predicted diamond ring: a spot "
            f"closer than this to any ring is flagged 'is_diamond' and "
            f"removed from the candidate pool passed to indexing. "
            f"Default: {solve_pipeline.CONTAM_TOL_DEG_DEFAULT:g}°.")
        self._diamond_a.setMaximumWidth(88); self._contam_tol.setMaximumWidth(80)
        dv.addRow(_tight2("diamond a:", self._diamond_a, "tol:", self._contam_tol))
        self._diamond_btn = S.primary_btn("Run Diamond Filter")
        self._diamond_btn.setEnabled(False)
        self._diamond_btn.clicked.connect(self._run_diamond_filter)
        self._diamond_btn.setMaximumWidth(165)
        dv.addRow(_left(self._diamond_btn))
        self._diamond_status = QtWidgets.QLabel("—")
        self._diamond_status.setStyleSheet(f"color:{S.MUTED};font-size:11px")
        self._diamond_status.setWordWrap(True)
        dv.addRow(self._diamond_status)
        mv.addWidget(grp_dia)

        grp_ab = QtWidgets.QGroupBox("3. Index")
        av = QtWidgets.QFormLayout(grp_ab); av.setSpacing(4)
        self._index_method = QtWidgets.QComboBox()
        self._index_method.addItems(["Free (ab-initio)", "Known structure"])
        self._index_method.setToolTip(
            "Free: blind ab-initio indexing (no cell/space-group hint), "
            "followed by a free refinement in step 4. Known structure: "
            "search for an orientation of a lattice cell you supply among "
            "the diamond-filtered candidates -- indexing and refinement "
            "happen together in one run, so step 4 is not needed for this "
            "mode. The declared space group is used only as a post-hoc "
            "systematic-absence sanity check, never to constrain the "
            "search itself. Default: Free (ab-initio).")
        self._index_method.currentIndexChanged.connect(self._on_index_method_changed)
        self._index_method.setMaximumWidth(118)

        self._ab_free_params = QtWidgets.QWidget()
        afp = QtWidgets.QFormLayout(self._ab_free_params)
        afp.setContentsMargins(0, 0, 0, 0); afp.setSpacing(4)
        self._sigma_g = _fspin(1e-5, 1.0, 5, solve_pipeline.SIGMA_G_DEFAULT, "Å⁻¹")
        self._sigma_g.setToolTip(
            f"Estimated 1-sigma uncertainty on each g-vector component (Å⁻¹), "
            f"used to weight ab-initio candidate-vector scoring. Default: "
            f"{solve_pipeline.SIGMA_G_DEFAULT:g} Å⁻¹.")
        self._min_refl = _NoScrollSpinBox(); self._min_refl.setRange(4, 100000)
        self._min_refl.setValue(solve_pipeline.MIN_REFLECTIONS_DEFAULT)
        self._min_refl.setToolTip(
            f"Minimum number of diamond-filtered candidate reflections "
            f"required before ab-initio indexing will even attempt a search. "
            f"Default: {solve_pipeline.MIN_REFLECTIONS_DEFAULT}.")
        self._tol_override = _fspin(0.01, 1.0, 3, solve_pipeline.AB_INITIO_TOL_DEFAULT)
        self._tol_override.setToolTip(
            "Match tolerance (in fractional-HKL units) for ab-initio indexing: "
            "how close a reflection's HKL must land to an integer to count as "
            "indexed on a trial lattice, both when scoring candidate lattice "
            "vectors and when deciding final indexed/unindexed status. "
            f"Pre-filled with midas_hkls.ab_initio's own default "
            f"({solve_pipeline.AB_INITIO_TOL_DEFAULT:g}) -- adjust up to accept "
            "looser matches or down to tighten them; too tight a tolerance can "
            "make indexing fail outright.")
        for w in (self._sigma_g, self._min_refl, self._tol_override):
            w.setMaximumWidth(60)
        # Row 1: method + sigma_g (sigma_g only applies to the free mode);
        # row 2: min_reflections + tol_override.
        self._sigma_g_box = QtWidgets.QWidget()
        sgl = QtWidgets.QHBoxLayout(self._sigma_g_box)
        sgl.setContentsMargins(0, 0, 0, 0); sgl.setSpacing(4)
        sgl.addWidget(QtWidgets.QLabel("sigma_g:")); sgl.addWidget(self._sigma_g)
        mrow = QtWidgets.QHBoxLayout(); mrow.setSpacing(4); mrow.setContentsMargins(0, 0, 0, 0)
        mlbl = QtWidgets.QLabel("method:")
        mlbl.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        mrow.addWidget(mlbl); mrow.addWidget(self._index_method)
        mrow.addSpacing(6); mrow.addWidget(self._sigma_g_box); mrow.addStretch(1)
        av.addRow(mrow)
        afp.addRow(_tight2("min_refl:", self._min_refl, "tol_override:", self._tol_override))
        av.addRow(self._ab_free_params)

        self._ks3 = self._build_known_structure_form()
        av.addRow(self._ks3["widget"])
        self._on_index_method_changed(0)

        self._ab_btn = S.primary_btn("Run Indexing")
        self._ab_btn.setEnabled(False)
        self._ab_btn.clicked.connect(self._run_index)
        self._ab_btn.setMaximumWidth(120)
        av.addRow(_left(self._ab_btn))
        self._ab_status = QtWidgets.QLabel("—")
        self._ab_status.setStyleSheet(f"color:{S.MUTED};font-size:11px")
        self._ab_status.setWordWrap(True)
        av.addRow(self._ab_status)
        mv.addWidget(grp_ab)

        grp_ref = QtWidgets.QGroupBox("4. Refine")
        rv = QtWidgets.QVBoxLayout(grp_ref)
        self._refine_btn = S.primary_btn("Run Refine")
        self._refine_btn.setEnabled(False)
        self._refine_btn.clicked.connect(self._run_refine)
        self._refine_btn.setMaximumWidth(120)
        rv.addWidget(self._refine_btn, 0, QtCore.Qt.AlignLeft)
        self._domain_list = QtWidgets.QListWidget()
        self._domain_list.setToolTip(
            "Every solved domain so far -- round 1's refine result is "
            "'Domain 1'; each successful 'Index Remaining Spots' round below "
            "appends another. Select one to see its full cell/refine detail.")
        self._domain_list.setMaximumHeight(90)
        self._domain_list.currentRowChanged.connect(self._on_domain_selected)
        self._domain_list.setMaximumWidth(170)
        rv.addWidget(self._domain_list, 0, QtCore.Qt.AlignLeft)
        self._param_grid = QtWidgets.QGridLayout()
        self._param_grid.setHorizontalSpacing(20); self._param_grid.setVerticalSpacing(6)
        _pg_host = QtWidgets.QWidget(); _pg_host.setLayout(self._param_grid)
        rv.addWidget(_pg_host)
        mv.addWidget(grp_ref)

        grp_leftover = QtWidgets.QGroupBox("5. Index remaining spots")
        lv2 = QtWidgets.QFormLayout(grp_leftover); lv2.setSpacing(4)
        self._leftover_status = QtWidgets.QLabel("—")
        self._leftover_status.setStyleSheet(f"color:{S.MUTED};font-size:11px")
        lv2.addRow("remaining:", self._leftover_status)
        self._leftover_method = QtWidgets.QComboBox()
        self._leftover_method.addItems(["Free (ab-initio)", "Known structure"])
        self._leftover_method.setToolTip(
            "Free: re-run blind ab-initio indexing (step 3) + refine (step 4) "
            "on whatever is still unassigned -- for a genuinely different, "
            "unknown structure. Known structure: search for an orientation "
            "of a lattice cell you supply (type it in, or click 'Fill from "
            "latest domain' to reuse the most recently solved domain's own "
            "cell) among the leftover spots -- for a second grain/domain of "
            "the same material at a different orientation (handoff "
            "§5.4.2/§5.6). A match is only accepted if it clearly beats a "
            "null test against scrambled spots; a negative result just "
            "means no further domain was found. Default: Free (ab-initio).")
        self._leftover_method.currentIndexChanged.connect(self._on_leftover_method_changed)
        self._leftover_method.setMaximumWidth(130)
        lv2.addRow("method:", self._leftover_method)
        self._ks5 = self._build_known_structure_form()
        self._ks5_fill_btn = QtWidgets.QPushButton("Fill from latest domain")
        self._ks5_fill_btn.setEnabled(False)
        self._ks5_fill_btn.setToolTip(
            "Copy the most recently solved domain's own refined cell into "
            "the fields below (space group is left as-is -- a free "
            "refinement carries no space-group information).")
        self._ks5_fill_btn.clicked.connect(self._fill_ks5_from_latest_domain)
        self._ks5["form"].insertRow(0, self._ks5_fill_btn)
        lv2.addRow(self._ks5["widget"])
        self._on_leftover_method_changed(0)
        self._leftover_btn = S.primary_btn("Index Remaining Spots")
        self._leftover_btn.setEnabled(False)
        self._leftover_btn.clicked.connect(self._run_index_remaining)
        self._leftover_btn.setMaximumWidth(190)
        lv2.addRow(_left(self._leftover_btn))
        self._leftover_run_status = QtWidgets.QLabel("—")
        self._leftover_run_status.setStyleSheet(f"color:{S.MUTED};font-size:11px")
        self._leftover_run_status.setWordWrap(True)
        lv2.addRow(self._leftover_run_status)
        mv.addWidget(grp_leftover)

        self._prog = QtWidgets.QProgressBar(); self._prog.setVisible(False); self._prog.setRange(0, 0)
        mv.addWidget(self._prog)
        mv.addStretch(1)
        split.addWidget(mid_scroll)

        # ── RIGHT: tabbed views (3-D reciprocal-space map, Detector) + log ──
        right = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self._view_tabs = QtWidgets.QTabWidget()
        self._build_recip_map_tab()
        self._build_detector_tab()
        self._diag_view = DiagnosticsView()
        self._diag_view.spotClicked.connect(self._on_recip_spot_clicked)
        self._view_tabs.addTab(self._diag_view, "Diagnostics")
        self._volume_view = ReciprocalVolumeView()
        self._volume_view.buildRequested.connect(self._start_volume_build)
        self._volume_view.cancelRequested.connect(lambda: self._vol_worker and self._vol_worker.cancel())
        self._volume_view.spotClicked.connect(self._on_recip_spot_clicked)
        self._view_tabs.addTab(self._volume_view, "Volume")
        self._compare_view = DomainCompareView()
        self._compare_view.reviewChanged.connect(self._on_compare_review)
        self._compare_view.spotClicked.connect(self._on_recip_spot_clicked)
        self._view_tabs.addTab(self._compare_view, "Compare domains")
        self._view_tabs.currentChanged.connect(self._refresh_compare)
        self._contam_view = ContaminationView()
        self._contam_view.spotClicked.connect(self._on_recip_spot_clicked)
        self._view_tabs.addTab(self._contam_view, "Contamination")
        self._view_tabs.currentChanged.connect(self._refresh_contamination)
        self._inspector = SpotInspector()
        self._view_tabs.addTab(self._inspector, "Spot inspector")
        self._view_tabs.currentChanged.connect(self._refresh_diagnostics)
        right.addWidget(self._view_tabs)

        self._log = LogPanel()
        self._log.setMaximumHeight(16_777_215)
        right.addWidget(self._log)
        right.setStretchFactor(0, 2); right.setStretchFactor(1, 1)
        right.setMinimumWidth(320)
        split.addWidget(right)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 0); split.setStretchFactor(2, 1)
        split.setSizes([229, 350, 1000])
        # Data | Controls | Results — minimise Data / Controls for more Results room.
        self._hsplit = split
        self._body_lay.addWidget(install_panel_toggles(split).ribbon)

        # Connected last, once self._det_stage/_det_view etc. already exist --
        # setCurrentIndex() below fires this synchronously, and the first
        # panel tab is added (via _add_panel(), which calls setCurrentIndex)
        # earlier in this same method, before the Detector tab is built.
        self._panel_tabs.currentChanged.connect(self._refresh_detector_preview)

    def _build_recip_map_tab(self):
        """3-D qx/qy/qz reciprocal-space scatter, GPU-backed via PyVista/VTK
        (``pyvista_views.PyVistaScatter3DView``) -- replaces an earlier
        matplotlib ``Axes3D`` version (see .context/DECISIONS.md
        2026-10-23). An even earlier VisPy attempt segfaulted under this
        project's QT_QPA_PLATFORM=offscreen test convention; VTK's
        ``QtInteractor`` does not have that problem."""
        self._recip_view = PyVistaScatter3DView()
        self._recip_view.spotClicked.connect(self._on_recip_spot_clicked)
        self._recip_view.spotHovered.connect(self._on_recip_spot_hovered)

        tab = QtWidgets.QWidget()
        tv = QtWidgets.QVBoxLayout(tab); tv.setContentsMargins(0, 0, 0, 0); tv.setSpacing(0)

        mode_row = QtWidgets.QHBoxLayout()
        mode_row.setContentsMargins(6, 2, 6, 2)
        mode_row.addWidget(QtWidgets.QLabel("Color by:"))
        self._recip_mode = QtWidgets.QComboBox()
        self._recip_mode.addItems(["Panel", "Crystal", "Q residual"])
        self._recip_mode.setToolTip(
            "Panel: every spot colored by which detector panel it came from "
            "(useful to sanity-check a pooled multi-panel ingest). Crystal: "
            "diamond/gasket spots, each solved domain, and whatever is still "
            "unassigned each get their own color. Q residual: the magnitude "
            "of observed minus UB-fitted q (Å⁻¹, 2π/d); grey means no fit.")
        self._recip_mode.currentIndexChanged.connect(lambda _=None: self._refresh_recip_view())
        mode_row.addWidget(self._recip_mode)
        self._recip_show_unindexed = QtWidgets.QCheckBox("Show unindexed/spurious spots")
        self._recip_show_unindexed.setChecked(True)
        self._recip_show_unindexed.setToolTip(
            "Plot the grey 'unindexed/spurious' spot population alongside "
            "diamond/gasket and indexed spots. Checked (shown) by default -- "
            "uncheck to declutter the view once you only care about what's "
            "indexed.")
        self._recip_show_unindexed.stateChanged.connect(lambda _=None: self._refresh_recip_view())
        mode_row.addWidget(self._recip_show_unindexed)
        mode_row.addStretch(1)
        tv.addLayout(mode_row)

        size_row = QtWidgets.QHBoxLayout()
        size_row.setContentsMargins(6, 2, 6, 2)
        size_row.addWidget(QtWidgets.QLabel("Spot size:"))
        self._recip_point_size = QtWidgets.QSpinBox()
        self._recip_point_size.setRange(4, 40)
        self._recip_point_size.setValue(14)
        self._recip_point_size.setToolTip("Rendered diameter (px) of each reciprocal-space spot.")
        self._recip_point_size.valueChanged.connect(lambda _=None: self._refresh_recip_view())
        size_row.addWidget(self._recip_point_size)
        size_row.addWidget(QtWidgets.QLabel("Size mode:"))
        self._recip_size_mode = QtWidgets.QComboBox()
        self._recip_size_mode.addItems(["Equal size", "By total intensity"])
        self._recip_size_mode.setToolTip(
            "Equal size: every spot rendered at the same size above. By "
            "total intensity: each spot's size scales with its integrated "
            "blob intensity (midas_defect's summed counts), relative to the "
            "brightest spot currently visible; faint spots still render at "
            "a reduced minimum size rather than disappearing.")
        self._recip_size_mode.currentIndexChanged.connect(lambda _=None: self._refresh_recip_view())
        size_row.addWidget(self._recip_size_mode)
        size_row.addStretch(1)
        tv.addLayout(size_row)

        self._recip_predicted = QtWidgets.QCheckBox("Show fitted positions")
        self._recip_predicted.setToolTip(
            "Crosses and connecting lines show UB-fitted positions for assigned "
            "observed reflections only; this does not predict unobserved reflections.")
        self._recip_predicted.toggled.connect(lambda: self._refresh_recip_view())
        options = QtWidgets.QHBoxLayout()
        options.addWidget(self._recip_predicted)
        fit_btn = QtWidgets.QPushButton("Fit visible spots")
        fit_btn.clicked.connect(self._fit_visible_spots)
        options.addWidget(fit_btn)
        options.addStretch(1)
        tv.addLayout(options)
        tv.addLayout(self._build_overlay_controls())
        tv.addWidget(self._recip_view, stretch=1)
        self._spot_details = QtWidgets.QLabel("Hover a spot for details; click to select.")
        self._spot_details.setWordWrap(True)
        self._spot_details.setTextFormat(QtCore.Qt.PlainText)
        tv.addWidget(self._spot_details)
        self._show_detector_btn = QtWidgets.QPushButton("Show selected spot on detector")
        self._show_detector_btn.setEnabled(False)
        self._show_detector_btn.clicked.connect(self._show_selected_on_detector)
        tv.addWidget(self._show_detector_btn)
        self._recip_stats = QtWidgets.QLabel("—")
        self._recip_stats.setStyleSheet(f"color:{S.MUTED};font-size:11px")
        self._recip_stats.setWordWrap(True)
        self._recip_stats.setContentsMargins(6, 2, 6, 4)
        tv.addWidget(self._recip_stats)
        self._view_tabs.addTab(tab, "Reciprocal space map")

    # ── reference overlays: Ewald cap (V01), basis/cell (V15), predictions (V08) ──
    def _build_overlay_controls(self) -> QtWidgets.QLayout:
        box = QtWidgets.QVBoxLayout()
        box.setContentsMargins(6, 0, 6, 0)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Overlays:"))
        self._ov_ewald = QtWidgets.QCheckBox("Ewald sphere (cap)")
        self._ov_ewald.setToolTip(
            "The Ewald sphere through q=0 at the ω below, in sample-frame q "
            "(centre −R(ω)ᵀ·kᵢ, radius 2π/λ), drawn only as a local cap. Yellow "
            "arrow: incident beam kᵢ ending at q=0; pink line: ω rotation axis. "
            "Not a resolution shell.")
        self._ov_basis = QtWidgets.QCheckBox("a*, b*, c* + cell")
        self._ov_basis.setToolTip("Reciprocal basis (red/green/blue = a*/b*/c*) and unit cell of the selected domain (2π·UB columns).")
        self._ov_pred = QtWidgets.QCheckBox("Predicted lattice points")
        self._ov_pred.setToolTip(
            "Every lattice point of the selected domain that crosses the Ewald "
            "sphere inside the scanned ω range. Green: explained by an assigned "
            "spot; orange: lands on a panel but no assigned spot; grey (optional): "
            "never lands on an active panel. Cached in the project.")
        self._ov_pred_hidden = QtWidgets.QCheckBox("incl. off-panel")
        self._ov_patch = QtWidgets.QCheckBox("Frame patches")
        self._ov_patch.setToolTip(
            "Each panel's frame at the ω below painted onto its measured patch of the Ewald "
            "sphere (sample-frame q). Colour = block-max background-subtracted counts (asinh); "
            "missing cells = masked / sentinel pixels, never zero. Panels outside their ω "
            "window are skipped. Selecting a spot moves ω to that spot's own ω.")
        self._ov_shells = QtWidgets.QCheckBox("|q| shells")
        self._ov_shells.setToolTip("Constant-|q| shells (centred on q=0) at 0.5, 1.0, 1.5… Å⁻¹ — distinct from the Ewald sphere.")
        for w in (self._ov_ewald, self._ov_basis, self._ov_pred, self._ov_pred_hidden, self._ov_patch, self._ov_shells):
            w.toggled.connect(lambda _=None: self._refresh_overlays())
            row.addWidget(w)
        row.addStretch(1)
        box.addLayout(row)
        row2 = QtWidgets.QHBoxLayout()
        row2.addWidget(QtWidgets.QLabel("ω:"))
        self._ov_omega = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._ov_omega.setRange(-1800, 1800)       # tenths of a degree; re-ranged to the scan
        self._ov_omega.valueChanged.connect(lambda _=None: self._refresh_overlays())
        row2.addWidget(self._ov_omega, stretch=1)
        self._ov_omega_label = QtWidgets.QLabel("0.0°")
        self._ov_omega_label.setMinimumWidth(60)
        row2.addWidget(self._ov_omega_label)
        self._ov_now = QtWidgets.QCheckBox("highlight diffracting now (±0.5°)")
        self._ov_now.setChecked(True)
        self._ov_now.toggled.connect(lambda _=None: self._refresh_overlays())
        row2.addWidget(self._ov_now)
        box.addLayout(row2)
        self._ov_status = QtWidgets.QLabel("")
        self._ov_status.setStyleSheet(f"color:{S.MUTED};font-size:11px")
        self._ov_status.setWordWrap(True)
        box.addWidget(self._ov_status)
        return box

    def _current_domain_index(self) -> Optional[int]:
        if not self._domains:
            return None
        row = self._domain_list.currentRow()
        return row if 0 <= row < len(self._domains) else len(self._domains) - 1

    def _ingest_snapshots(self) -> dict:
        if not self._sc_ingest_meta or not self._sc_ingest_meta.get("panels"):
            return {}
        return geom_adapter.snapshots_from_ingest_meta(self._sc_ingest_meta)

    def _domain_viz(self, idx: int) -> Optional[dict]:
        """Prediction/residual product of domain ``idx`` -- the cached one if its
        UB fingerprint still matches, else (re)computed and queued for saving."""
        d = self._domains[idx]
        res = d["result"]
        fit = res.get("reflection_fit") or {}
        if res.get("UB") is None or fit.get("hkl") is None or self._candidate_df is None:
            return None
        name = f"domain_{idx + 1:02d}_predictions"
        fp = viz_products.ub_fingerprint(res["UB"])
        prod = self._viz_products.get(name)
        if prod is not None and prod["meta"]["inputs"].get("ub_fingerprint") == fp:
            return prod
        snaps = self._ingest_snapshots()
        if not snaps:
            return None
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            rows = viz_products.domain_rows(self._candidate_df, d["claimed_index"], fit["hkl"])
            prod = viz_products.build_domain_predictions(d["label"], res["UB"], rows, snaps)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self._viz_products[name] = prod
        self._viz_unsaved.add(name)
        return prod

    def _frame_manifest(self) -> Optional[pd.DataFrame]:
        prod = self._viz_products.get("frame_manifest")
        if prod is None and self._sc_ingest_meta and self._sc_ingest_meta.get("panels"):
            prod = viz_products.build_frame_manifest(self._sc_ingest_meta)
            self._viz_products["frame_manifest"] = prod
            self._viz_unsaved.add("frame_manifest")
        return prod["tables"]["frames"] if prod else None

    def _scan_omega_span(self) -> tuple:
        snaps = self._ingest_snapshots()
        if not snaps:
            return (-180.0, 180.0)
        spans = viz_products.omega_ranges(snaps).values()
        return (min(a for a, _ in spans), max(b for _, b in spans))

    def _refresh_overlays(self):
        lo, hi = self._scan_omega_span()
        v = self._ov_omega
        if (v.minimum(), v.maximum()) != (int(np.floor(lo * 10)), int(np.ceil(hi * 10))):
            v.blockSignals(True)
            cur = v.value()
            v.setRange(int(np.floor(lo * 10)), int(np.ceil(hi * 10)))
            v.setValue(min(max(cur, v.minimum()), v.maximum()))
            v.blockSignals(False)
        omega = v.value() / 10.0
        self._ov_omega_label.setText(f"{omega:.1f}°")
        idx = self._current_domain_index()
        snaps = self._ingest_snapshots()
        notes = []
        view = self._recip_view
        # Ewald cap -- needs only wavelength + omega, no cell
        if self._ov_ewald.isChecked() and snaps:
            snap = next(iter(snaps.values()))
            q_cap = float(np.nanmax(self._spots_df["q_norm"])) * 1.15 if (
                self._spots_df is not None and len(self._spots_df)) else 1.5
            cap = viz_products.ewald_cap(snap.wavelength_A, omega, q_cap)
            view.set_ewald(cap["patch"], ki=cap["ki"], axis=cap["axis"], extent=q_cap)
            notes.append(f"Ewald sphere: R=2π/λ={cap['k0']:.3f} Å⁻¹ (centre {np.round(cap['center'], 2).tolist()}), cap |q|≤{q_cap:.2f}")
        else:
            view.set_ewald(None)
        domain_UB = None
        if idx is not None and self._domains[idx]["result"].get("UB") is not None:
            domain_UB = np.asarray(self._domains[idx]["result"]["UB"], float)
        view.set_basis(viz_products.basis_matrix(domain_UB) if (self._ov_basis.isChecked() and domain_UB is not None) else None)
        if self._ov_basis.isChecked() and domain_UB is None:
            notes.append("a*, b*, c*: needs a solved domain")
        if self._ov_pred.isChecked() and idx is not None:
            prod = self._domain_viz(idx)
            if prod is not None:
                pred = prod["tables"]["predictions"]
                cls = viz_products.classify_lattice_points(pred)
                groups = [(cls["observed"], "#38d9a9", "predicted · observed"),
                          (cls["missing"], "#ffa94d", "predicted · not observed")]
                if self._ov_pred_hidden.isChecked():
                    groups.append((cls["hidden"], "#6b6b6b", "predicted · off panel"))
                if self._ov_now.isChecked():
                    groups.append((viz_products.lattice_points_near_omega(pred, omega, 0.5), "#ffffff", "diffracting now"))
                view.set_predicted(groups)
                st = prod["meta"]["stats"]
                notes.append(f"{self._domains[idx]['label']}: {len(cls['observed'])} observed / {len(cls['missing'])} "
                             f"visible-unobserved / {len(cls['hidden'])} off-panel lattice points; RMS Δrow/Δcol "
                             f"{st.get('rms_d_row', float('nan')):.2f}/{st.get('rms_d_col', float('nan')):.2f} px")
            else:
                view.set_predicted(None)
                notes.append("Predicted points: needs a refined domain with a UB")
        else:
            view.set_predicted(None)
        if self._ov_patch.isChecked():
            notes.extend(self._refresh_frame_patches(omega))
        else:
            view.set_frame_patches(None)
        view.set_shells([0.5, 1.0, 1.5] if self._ov_shells.isChecked() else None)
        self._ov_status.setText(" · ".join(notes))
        self._refresh_diagnostics()
        if self._project_path():
            self._persist_viz()

    # ── reciprocal volume (V03/V04/V05) ─────────────────────────────
    def _volume_source_cfgs(self) -> tuple:
        """Per-panel source descriptions from the as-run ingest record, or
        ``(None, reason)``. Only plain HDF5 stacks are supported."""
        import os
        cfgs = []
        for key, prec in ((self._sc_ingest_meta or {}).get("panels") or {}).items():
            pid = int(key)
            ld = (prec.get("state") or {}).get("loader") or {}
            path = ld.get("path") or ""
            ds = (ld.get("dataset") or "").split()[0] if ld.get("dataset") else ""
            if not (path and ds and os.path.exists(path)):
                return None, f"panel {pid}: source file {path or '(none)'} is not available"
            if not is_h5(path):
                return None, f"panel {pid}: only HDF5 sources are supported"
            mask = (self._ingest_preview_by_panel.get(pid) or {}).get("mask")
            cfgs.append({"panel_id": pid, "path": path, "dataset": ds, "mask": mask})
        return (cfgs, "") if cfgs else (None, "run ingest first")

    def _start_volume_build(self, pitch: float):
        if self._vol_worker is not None and self._vol_worker.isRunning():
            return
        cfgs, why = self._volume_source_cfgs()
        if cfgs is None:
            self._volume_view.info.setText(f"Cannot build volume: {why}.")
            return
        spec = reciprocal_volume.auto_grid(
            {c["panel_id"]: s for c in cfgs for s in [self._ingest_snapshots()[c["panel_id"]]]}, pitch)
        mb = reciprocal_volume.estimate_memory_mb(spec)
        if mb > 1500:
            self._volume_view.info.setText(
                f"Grid {spec.shape} would need ~{mb:.0f} MB of accumulators; raise the voxel pitch.")
            return
        self._volume_view.info.setText(f"Building {spec.shape} grid (~{mb:.0f} MB accumulators)…")
        self._volume_view.set_building(True)
        w = VolumeBuildWorker(cfgs, self._ingest_snapshots(), pitch, self)
        w.progress.connect(self._volume_view.set_progress)
        w.finished_ok.connect(self._on_volume_built)
        w.failed.connect(lambda tb: (self._volume_view.set_building(False),
                                     self._volume_view.info.setText("Volume build failed — see log."),
                                     self._log.append(tb)))
        w.cancelled.connect(lambda: (self._volume_view.set_building(False),
                                     self._volume_view.info.setText("Volume build cancelled; previous volume kept.")))
        self._vol_worker = w
        w.start()

    def _on_volume_built(self, vol: dict, spec):
        self._volume_view.set_building(False)
        prod = viz_products.volume_product(vol, spec, self._sc_ingest_meta)
        self._viz_products["reciprocal_volume"] = prod
        self._viz_unsaved.add("reciprocal_volume")
        self._show_cached_volume()
        self._persist_viz()

    def _show_cached_volume(self):
        prod = self._viz_products.get("reciprocal_volume")
        if prod is None:
            self._volume_view.set_volume(None, None)
            return
        vol, spec = viz_products.volume_from_product(prod)
        self._volume_view.set_volume(vol, spec, prod["meta"])

    def _frame_patch_product(self) -> Optional[dict]:
        prod = self._viz_products.get("frame_patches")
        if prod is not None or not self._sc_ingest_meta or not self._sc_ingest_meta.get("panels"):
            return prod
        cfgs, why = self._volume_source_cfgs()
        if cfgs is None:
            return None
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            masks = {c["panel_id"]: c["mask"] for c in cfgs}
            prod = viz_products.build_frame_patches(self._sc_ingest_meta, masks)
        except Exception as exc:
            self._log.append(f"Frame patch cache could not be built: {exc}")
            return None
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self._viz_products["frame_patches"] = prod
        self._viz_unsaved.add("frame_patches")
        return prod

    def _refresh_frame_patches(self, omega: float) -> list:
        from midas_gui.solve_cell import frame_patch as fp
        prod = self._frame_patch_product()
        if prod is None:
            self._recip_view.set_frame_patches(None)
            return ["Frame patches: need the original frames (or a project that stores the cache)"]
        stride, snaps = int(prod["meta"]["stride"]), self._ingest_snapshots()
        geom = getattr(self, "_patch_geom", {})
        self._patch_geom = geom
        patches, notes, cof = [], [], 10.0
        for pid, snap in sorted(snaps.items()):
            f = fp.loaded_frame_for_omega(snap, omega)
            idx = prod["arrays"].get(f"loaded_idx_p{pid}")
            if f is None or idx is None:
                notes.append(f"panel {pid}: ω outside its window")
                continue
            slot = int(np.searchsorted(idx, f))
            if slot >= len(idx) or idx[slot] != f:
                continue
            if (pid, stride) not in geom:
                geom[(pid, stride)] = fp.corner_qlab(snap, stride)
            om = float(snap.omega_of_loaded_frame(f))
            lo, hi = prod["meta"]["clim"][str(pid)]
            patches.append({"corners": fp.corners_at_omega(geom[(pid, stride)], om),
                            "cells": np.arcsinh(prod["arrays"][f"cells_p{pid}"][slot] / cof)})
            notes.append(f"panel {pid}: frame {f} @ ω {om:+.2f}°")
        top = max([np.arcsinh(max(h, 200.0) / cof) for _l, h in prod["meta"]["clim"].values()] or [1.0])
        self._recip_view.set_frame_patches(patches, clim=(0.0, float(top)))
        return ["Patches: " + "; ".join(notes)]

    def _persist_viz(self):
        """Store newly computed products in the open project (best-effort)."""
        path = self._project_path()
        if not path or not self._viz_unsaved:
            return
        try:
            for name in sorted(self._viz_unsaved):
                prod = self._viz_products[name]
                project.write_solve_cell_viz(path, name, meta=prod["meta"],
                                             tables=prod.get("tables"), arrays=prod.get("arrays"))
            self._log.append(f"Visualization data stored in project: {', '.join(sorted(self._viz_unsaved))}.")
            self._viz_unsaved.clear()
        except Exception as exc:        # the record may not be flushed yet; retried at the next flush
            self._log.append(f"Visualization data kept in memory only ({exc}).")

    def _refresh_diagnostics(self, *_args, force: bool = False):
        # Computing predictions is the slow part; only do it while the tab is shown.
        if not force and self._view_tabs.currentWidget() is not self._diag_view:
            return
        idx = self._current_domain_index()
        frames = self._frame_manifest()
        prod = self._domain_viz(idx) if idx is not None else None
        shapes = {pid: s.raw_shape for pid, s in self._ingest_snapshots().items()}
        self._diag_view.set_data(
            residuals=prod["tables"]["residuals"] if prod else None,
            predictions=prod["tables"]["predictions"] if prod else None,
            frames=frames, spots=self._spots_df, panel_shapes=shapes,
            label=self._domains[idx]["label"] if idx is not None else "")

    # Cycled per domain once more than one exists (_update_multidomain_plot) --
    # kept well clear of the legacy "#ff3030"/"#4da3ff" diamond/indexed colors.
    _DOMAIN_COLORS = ["#4da3ff", "#ffa94d", "#69db7c", "#da77f2",
                      "#ffd43b", "#63e6be", "#ff8787", "#91a7ff"]

    # Index into self._recip_size_mode -> PyVistaScatter3DView size mode.
    _RECIP_SIZE_MODES = [SIZE_MODE_EQUAL, SIZE_MODE_INTENSITY]

    def _update_multidomain_plot(self):
        """Kept for existing callers/tests -- delegates to the unified
        mode-aware redraw."""
        self._refresh_recip_view()

    def _unindexed_df(self):
        """The current 'unindexed/spurious' pool for display purposes:
        ``_leftover_df()`` once a diamond filter has produced a
        ``candidate_df``, else the whole ingested ``spots_df`` (nothing has
        been classified yet)."""
        if self._candidate_df is not None:
            return self._leftover_df()
        return self._spots_df

    @staticmethod
    def _xyz_cols(df):
        if df is None or len(df) == 0:
            return (np.empty(0), np.empty(0), np.empty(0))
        return (df["qsample_x"].to_numpy(), df["qsample_y"].to_numpy(), df["qsample_z"].to_numpy())

    @staticmethod
    def _panel_ids_col(df):
        if df is None or len(df) == 0 or "panel_id" not in df.columns:
            return None
        return df["panel_id"].to_numpy()

    def _bucket_by_panel(self, sources):
        """``sources`` is a list of ``(x, y, z, panel_ids_or_None)`` tuples.
        Returns ``{panel_key: (x, y, z)}``, concatenated across every source
        that shares the same panel id -- ``panel_key`` is ``None`` for points
        with no panel_id info at all (single-panel data), rendered as one
        shared '(single panel)' bucket."""
        buckets: dict = {}

        def _extend(key, x, y, z):
            parts = buckets.setdefault(key, [[], [], []])
            parts[0].append(x); parts[1].append(y); parts[2].append(z)

        for x, y, z, pids in sources:
            if x is None or len(x) == 0:
                continue
            if pids is None:
                _extend(None, x, y, z)
                continue
            pids = np.asarray(pids)
            for pid in np.unique(pids):
                mask = pids == pid
                _extend(int(pid), x[mask], y[mask], z[mask])
        return {key: (np.concatenate(parts[0]), np.concatenate(parts[1]), np.concatenate(parts[2]))
                for key, parts in buckets.items()}

    @staticmethod
    def _panel_label(key) -> str:
        return "(single panel)" if key is None else solve_pipeline.panel_dir_name(key)

    def _display_spot_rows(self):
        """Observed rows with stable ingest identity and optional fit data."""
        rows = []
        def append(df, category, fit=None):
            if df is None:
                return
            fit = fit or {}
            for i, (idx, item) in enumerate(zip(df.index, df.to_dict("records"))):
                item["spot_id"] = item.get("spot_id", idx)
                item["category"] = category
                for key, values in fit.items():
                    if i < len(values):
                        item[key] = values[i]
                rows.append(item)
        if self._flagged_df is not None:
            append(self._flagged_df[self._flagged_df["is_diamond"]], "Diamond/gasket")
        if self._candidate_df is not None:
            for d in self._domains:
                append(self._candidate_df.loc[d["claimed_index"]],
                       f"{d['label']} ({d['method']})", d["result"].get("reflection_fit"))
        unindexed = self._unindexed_df()
        # Retain picking/identity while inspecting an ab-initio seed before
        # refinement has created the first durable domain.
        if (not self._domains and self._candidate_df is not None
                and self._ab_initio_raw is not None and self._ab_initio_raw.success):
            mask = self._ab_initio_raw.indexed_mask
            append(self._candidate_df.loc[mask], "Ab-initio indexed (unrefined)")
            unindexed = self._candidate_df.loc[~mask]
        append(unindexed, "Unindexed/spurious")
        return rows

    def _fit_visible_spots(self):
        self._recip_has_data = False
        self._refresh_recip_view()

    def _refresh_recip_view(self):
        rows = self._display_spot_rows()
        self._spot_rows_by_id = {r["spot_id"]: r for r in rows}
        if getattr(self, "_volume_view", None) is not None:
            self._volume_view.set_spots(rows)
        show = self._recip_show_unindexed.isChecked()
        n_unindexed = sum(r["category"] == "Unindexed/spurious" for r in rows)
        visible = [r for r in rows if show or r["category"] != "Unindexed/spurious"]
        mode = self._recip_mode.currentIndex()
        buckets = {}
        for row in visible:
            key = row.get("panel_id") if mode == 0 else row["category"]
            buckets.setdefault(key, []).append(row)
        finite = [r["q_residual"] for r in visible if np.isfinite(r.get("q_residual", np.nan))]
        norm = Normalize(0, max(finite) if finite and max(finite) > 0 else 1)
        cmap = matplotlib.colormaps["viridis"]
        groups = []
        keys = sorted(buckets, key=lambda k: (k is None, k)) if mode == 0 else list(buckets)
        for i, key in enumerate(keys):
            bucket = buckets[key]
            xyz = tuple(np.array([r[c] for r in bucket]) for c in ("qsample_x", "qsample_y", "qsample_z"))
            label = self._panel_label(key) if mode == 0 else key
            color = self._DOMAIN_COLORS[i % len(self._DOMAIN_COLORS)]
            if mode != 0:
                domain_colors = {f"{d['label']} ({d['method']})": self._DOMAIN_COLORS[j % len(self._DOMAIN_COLORS)]
                                 for j, d in enumerate(self._domains)}
                color = {"Diamond/gasket": "#ff3030", "Unindexed/spurious": "#969696", **domain_colors}.get(key, color)
            if mode == 2:
                color = [cmap(norm(r["q_residual"])) if np.isfinite(r.get("q_residual", np.nan))
                         else (0.59, 0.59, 0.59, 1) for r in bucket]
            groups.append((xyz, color, label, bucket))
        self._recip_view.set_groups(
            groups, point_size=self._recip_point_size.value(),
            size_mode=self._RECIP_SIZE_MODES[self._recip_size_mode.currentIndex()])
        if mode == 2 and finite:
            self._recip_view.set_scalar_bar(
                "|q observed − q fitted| (Å⁻¹; 2π/d)", 0, norm.vmax)
        else:
            self._recip_view.clear_scalar_bar()
        if self._recip_predicted.isChecked():
            fitted = [r for r in visible if "q_predicted" in r]
            if fitted:
                observed = np.array([[r[c] for c in ("qsample_x", "qsample_y", "qsample_z")] for r in fitted])
                predicted = np.array([r["q_predicted"] for r in fitted])
                self._recip_view.set_fitted_overlay(observed, predicted)
            else:
                self._recip_view.set_fitted_overlay(None, None)
        else:
            self._recip_view.set_fitted_overlay(None, None)
        self._update_recip_selection()
        if mode == 0:
            lines = [f"{self._panel_label(k)}: {len(buckets[k])} spots" for k in keys]
            lines.append(f"Total: {len(visible)} spots")
            if not show and n_unindexed:
                lines.append(f"({n_unindexed} unindexed hidden)")
        else:
            lines = [f"{d['label']} ({d['method']}): {len(d['claimed_index'])} spots, a={d['result']['cell'][0]:.4g} Å" for d in self._domains]
            if "Ab-initio indexed (unrefined)" in buckets:
                lines.append(f"Ab-initio indexed (unrefined): {len(buckets['Ab-initio indexed (unrefined)'])} spots")
            if self._flagged_df is not None:
                lines.append(f"Diamond/gasket: {int(self._flagged_df['is_diamond'].sum())} spots")
            lines.append(f"Unindexed: {n_unindexed} spots" + (" (hidden)" if not show else ""))
            if mode == 2:
                lines.append("Grey: no refined fit available" if finite else "Run refinement to obtain per-spot residuals; grey: no fit available")
        self._recip_stats.setText("\n".join(lines))

    @staticmethod
    def _spot_description(row):
        parts = [f"Spot {row['spot_id']}", row['category']]
        for key, label in (("panel_id", "panel"), ("loaded_frame_idx", "frame"),
                           ("omega_deg", "ω (°)"), ("row", "row"), ("col", "col")):
            if key in row:
                parts.append(f"{label}: {row[key]:.5g}")
        if "hkl" in row:
            parts.append(f"hkl: {tuple(row['hkl'])}")
            parts.append(f"Δq: {row['q_residual']:.5g} Å⁻¹ (2π/d)")
            parts.append(f"Δhkl: {row['hkl_residual']:.5g}")
        return " | ".join(parts)

    def _on_recip_spot_hovered(self, spot_id):
        row = self._spot_rows_by_id.get(spot_id) if spot_id is not None else None
        if row is None:
            row = self._spot_rows_by_id.get(self._selected_spot_id)
        self._spot_details.setText(self._spot_description(row) if row else
                                   "Hover a spot for details; click to select.")

    def _on_recip_spot_clicked(self, spot_id):
        if spot_id is not None:
            self._select_spot(spot_id)

    def _update_recip_selection(self):
        """Change only the selection marker; leave the scene/camera intact."""
        row = self._spot_rows_by_id.get(self._selected_spot_id)
        if row is not None and (row["category"] == "Unindexed/spurious"
                                and not self._recip_show_unindexed.isChecked()):
            row = None
        self._recip_view.set_selected(row)

    def _cached_residual_for(self, spot_id) -> Optional[dict]:
        """Observed−predicted record of a spot from any cached domain product."""
        for name, prod in self._viz_products.items():
            if name.endswith("_predictions"):
                res = prod["tables"]["residuals"]
                hit = res[res.spot_id == spot_id] if len(res) else res
                if len(hit):
                    return hit.iloc[0].to_dict()
        return None

    # ── domain comparison (V13) ─────────────────────────────────────
    def _comparison_entries(self) -> list:
        return domain_compare.entries_from_state(self._domains, self._ab_initio_raw, self._candidate_df)

    def _comparison_product(self, entries: list) -> dict:
        """Cached comparison if every lattice's UB fingerprint still matches, else recomputed."""
        want = {e["key"]: viz_products.ub_fingerprint(e["UB"]) for e in entries}
        prod = self._viz_products.get("domain_comparison")
        if prod is not None and prod["meta"]["inputs"].get("entries") == want:
            return prod
        prod = viz_products.build_domain_comparison(self._domains, self._ab_initio_raw, self._candidate_df)
        self._viz_products["domain_comparison"] = prod
        self._viz_unsaved.add("domain_comparison")
        return prod

    def _refresh_compare(self, *_args):
        if self._view_tabs.currentWidget() is not self._compare_view:
            return
        entries = self._comparison_entries() if self._candidate_df is not None else []
        if not entries:
            self._compare_view.set_data([], None, None, lambda k: ([], None))
            return
        prod = self._comparison_product(entries)
        fps = prod["meta"]["inputs"]["entries"]
        rev = self._viz_products.get("domain_review")
        reviews = {}
        if rev is not None:
            for r in rev["tables"]["reviews"].itertuples(index=False):
                if fps.get(r.a) == r.fp_a and fps.get(r.b) == r.fp_b:      # stale reviews are ignored
                    reviews[(r.a, r.b)] = r.conclusion
        by_key = {e["key"]: e for e in entries}

        def scene(key):
            e = by_key[key]
            df = self._candidate_df.loc[e["claimed_index"]]
            groups = []
            for k, (pid, g) in enumerate(df.groupby("panel_id")):
                rows = g.to_dict("records")
                groups.append((tuple(g[c].to_numpy() for c in ("qsample_x", "qsample_y", "qsample_z")),
                               self._DOMAIN_COLORS[k % len(self._DOMAIN_COLORS)], self._panel_label(pid), rows))
            return groups, e["UB"]

        self._compare_view.set_data(entries, prod["tables"], reviews, scene)
        self._persist_viz()

    def _on_compare_review(self, a: str, b: str, conclusion: str):
        """Store the reviewer's call (never touches claims) with the fingerprints it applies to."""
        fps = self._viz_products["domain_comparison"]["meta"]["inputs"]["entries"]
        old = self._viz_products.get("domain_review")
        rows = [] if old is None else [r._asdict() for r in old["tables"]["reviews"].itertuples(index=False)
                                       if (r.a, r.b) != (a, b)]
        if conclusion:
            from datetime import datetime, timezone
            rows.append({"a": a, "b": b, "fp_a": fps[a], "fp_b": fps[b], "conclusion": conclusion,
                         "reviewed_utc": datetime.now(timezone.utc).isoformat()})
        df = pd.DataFrame(rows, columns=["a", "b", "fp_a", "fp_b", "conclusion", "reviewed_utc"])
        self._viz_products["domain_review"] = {"meta": {"inputs": {"kind": "review"},
                                                        "note": "reviewer conclusions; do not change domain claims"},
                                               "tables": {"reviews": df}}
        self._viz_unsaved.add("domain_review")
        self._persist_viz()

    def _refresh_contamination(self, *_args):
        if self._view_tabs.currentWidget() is not self._contam_view:
            return
        snaps = self._ingest_snapshots()
        fields = (self._sc_stage_meta.get("diamond") or {}).get("fields") or {}
        summ = (self._sc_stage_meta.get("diamond") or {}).get("summary") or {}
        wl = next(iter(snaps.values())).wavelength_A if snaps else None
        self._contam_view.set_data(
            self._flagged_df, wl, diamond_a=float(summ.get("diamond_a_angstrom", fields.get("diamond_a", 3.5667))),
            tol_deg=float(summ.get("tolerance_deg", fields.get("contam_tol", 0.07))))
        self._contam_view.select_spot(self._selected_spot_id)

    def _update_inspector(self):
        row = self._spot_rows_by_id.get(self._selected_spot_id)
        self._inspector.show_spot(row, self._cached_residual_for(self._selected_spot_id) if row else None)

    def _inspector_omega_of(self, panel_id, loaded_idx):
        snap = self._ingest_snapshots().get(int(panel_id))
        return snap.omega_of_loaded_frame(loaded_idx) if snap else np.asarray(loaded_idx, float)

    def _show_cached_shoeboxes(self):
        prod = self._viz_products.get("spot_shoeboxes")
        if prod is None:
            self._inspector.set_cache(None, None, self._inspector_omega_of)
        else:
            self._inspector.set_cache(prod["arrays"], prod["tables"]["index"], self._inspector_omega_of)

    def _select_spot(self, spot_id):
        self._selected_spot_id = spot_id
        row = next((r for r in self._display_spot_rows() if r["spot_id"] == spot_id), None)
        if row:
            self._spot_details.setText(self._spot_description(row))
        self._show_detector_btn.setEnabled(row is not None and "loaded_frame_idx" in row)
        if row and self._ov_patch.isChecked() and np.isfinite(row.get("omega_deg", np.nan)):
            self._ov_omega.setValue(int(round(row["omega_deg"] * 10)))   # patch and centroid at the spot's own omega
        self._update_recip_selection()
        self._update_inspector()
        if self._view_tabs.currentWidget() is self._contam_view:
            self._contam_view.select_spot(spot_id)
        self._refresh_detector_preview()

    def _show_selected_on_detector(self):
        row = next((r for r in self._display_spot_rows() if r["spot_id"] == self._selected_spot_id), None)
        if row is None or "loaded_frame_idx" not in row:
            return
        pid = row.get("panel_id")
        if pid is not None:
            panel = self._panels.get(int(pid))
            if panel is None:
                self._spot_details.setText("Source panel is no longer loaded.")
                return
            self._panel_tabs.setCurrentWidget(panel)
        panel = self._active_panel()
        frame = int(row["loaded_frame_idx"])
        if panel is None or not 0 <= frame < panel.loader.n_frames():
            self._spot_details.setText("Source frame is not loaded; load this panel's original data to navigate.")
            return
        self._det_stage.setCurrentIndex(0)
        panel.loader.set_frame(frame)
        self._refresh_detector_preview()
        self._view_tabs.setCurrentIndex(1)

    def _on_detector_spot_clicked(self, _scatter, points, _event):
        if points:
            self._select_spot(points[0].data())

    def _build_detector_tab(self):
        """Shows the active panel's actual 2-D detector frame at a chosen
        ingest processing stage -- Raw/Corrected read live from the panel's
        ``DataLoaderPanel`` (no Ingest run needed). Calculated background/Mask
        show what Ingest captured into its ``preview`` result (see
        ``pipeline._stage_ingest``): a single frame (``preview_frame_index``,
        held because keeping the whole subtracted stack around on the GUI
        side would reintroduce the memory pressure the ingest-performance
        fixes just removed) and a max-over-rotation projection (one 2-D
        reduction over the stack ``choose_sectors`` already fully
        materializes internally, so it costs nothing extra to compute before
        that stack is discarded). The max projection is the one actually
        worth looking at to judge the background model -- a real reflection
        only satisfies the diffraction condition for a handful of frames out
        of hundreds, so the single-frame view is almost always near-empty
        even when the subtraction worked correctly (confirmed on real
        Ge-oP32 c1 data, see DECISIONS)."""
        tab = QtWidgets.QWidget()
        dv = QtWidgets.QVBoxLayout(tab); dv.setContentsMargins(4, 4, 4, 4); dv.setSpacing(4)

        toolbar = QtWidgets.QHBoxLayout()
        toolbar.addWidget(QtWidgets.QLabel("Stage:"))
        self._det_stage = QtWidgets.QComboBox()
        self._det_stage.addItems([
            "Raw", "Corrected", "Background corrected (after ingest)",
            "Max projected (after ingest)",
            "Mask",
        ])
        self._det_stage.setToolTip(
            "Raw/Corrected update live from this panel's loaded data and "
            "dark/bright/background fields, no Ingest run needed. The "
            "single-frame Background corrected view is usually near-empty "
            "-- a real reflection only satisfies the diffraction condition "
            "for a handful of frames out of hundreds -- so prefer the Max "
            "projected view to actually judge whether the background "
            "model worked. Both (and Mask) show what the last Ingest run "
            "captured; the single-frame one uses the frame index from the "
            "scrub bar below at the moment Run Ingest was clicked.")
        self._det_stage.currentIndexChanged.connect(self._refresh_detector_preview)
        toolbar.addWidget(self._det_stage)
        show_recip_btn = QtWidgets.QPushButton("Show reciprocal selection")
        show_recip_btn.clicked.connect(lambda: self._view_tabs.setCurrentIndex(0))
        toolbar.addWidget(show_recip_btn)
        toolbar.addStretch(1)
        dv.addLayout(toolbar)

        self._det_view = ImageViewer(title="")
        # Open circles (no fill) marking ingest-identified spot positions --
        # updated by _update_spot_overlay, never rebuilt (unlike
        # tab_calibrate.py's _draw_rings, there is only ever one overlay
        # item here, so setData() in place is simpler than clear/rebuild).
        self._spot_scatter = pg.ScatterPlotItem(
            pen=pg.mkPen("#ffd400", width=2), brush=None, size=16, symbol="o")
        self._spot_scatter.sigClicked.connect(self._on_detector_spot_clicked)
        self._det_view._iv.addItem(self._spot_scatter)
        self._det_selected = pg.ScatterPlotItem(pen=pg.mkPen("#ff00cc", width=3), brush=None, size=24)
        self._det_view._iv.addItem(self._det_selected)
        dv.addWidget(self._det_view, stretch=1)
        dv.addWidget(self._build_detector_scrub_bar())
        self._view_tabs.addTab(tab, "Detector")

    def _build_detector_scrub_bar(self) -> QtWidgets.QWidget:
        """Same ◀/▶/slider convention as ``tab_view.py``/``tab_calibrate.py``'s
        local ``_build_frame_scrub_bar`` (``objectName`` ``frameNavBtn``/
        ``frameNavSlider``, styled app-wide in ``style.py``)."""
        bar = QtWidgets.QWidget()
        hl = QtWidgets.QHBoxLayout(bar); hl.setContentsMargins(0, 0, 0, 0); hl.setSpacing(4)
        prev_btn = QtWidgets.QToolButton(); prev_btn.setObjectName("frameNavBtn")
        prev_btn.setText("◀"); prev_btn.clicked.connect(lambda: self._step_detector_frame(-1))
        self._det_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._det_slider.setObjectName("frameNavSlider")
        self._det_slider.valueChanged.connect(self._on_detector_slider_changed)
        next_btn = QtWidgets.QToolButton(); next_btn.setObjectName("frameNavBtn")
        next_btn.setText("▶"); next_btn.clicked.connect(lambda: self._step_detector_frame(1))
        self._det_frame_lbl = QtWidgets.QLabel("—")
        self._det_frame_lbl.setStyleSheet(f"color:{S.MUTED};font-size:11px")
        hl.addWidget(prev_btn); hl.addWidget(self._det_slider, stretch=1); hl.addWidget(next_btn)
        hl.addWidget(self._det_frame_lbl)
        return bar

    def _step_detector_frame(self, delta: int):
        self._det_slider.setValue(self._det_slider.value() + delta)

    def _on_detector_slider_changed(self, i: int):
        if self._det_stage.currentIndex() in (0, 1):   # Raw / Corrected
            panel = self._active_panel()
            if panel is not None:
                panel.loader.set_frame(i)
        self._refresh_detector_preview()

    def _refresh_detector_preview(self, *_args):
        panel = self._active_panel()
        if panel is None:
            return
        stage = self._det_stage.currentIndex()
        if stage in (0, 1):
            n = panel.loader.n_frames()
            self._det_slider.setEnabled(n > 1)
            self._det_slider.blockSignals(True)
            self._det_slider.setRange(0, max(n - 1, 0))
            self._det_slider.setValue(panel.loader.frame_index())
            self._det_slider.blockSignals(False)
            frame = panel.loader.current_frame()
            if frame is not None and stage == 1:
                frame = panel.loader.corrected(frame)
            self._det_frame_lbl.setText(f"frame {panel.loader.frame_index()}/{max(n - 1, 0)}"
                                        if n else "no data loaded")
            self._set_detector_image(frame, stage)
            self._update_spot_overlay(stage, panel.loader.frame_index() if n else None)
        else:
            self._det_slider.setEnabled(False)
            pid = self._active_panel_id()
            preview = self._ingest_preview_by_panel.get(pid) if pid is not None else None
            if preview is None:
                preview = self._ingest_preview   # legacy single-value fallback
            if preview is None:
                self._det_frame_lbl.setText("Run Ingest to populate")
                self._set_detector_image(None, stage)
                self._update_spot_overlay(stage, None)
                return
            # Keys fill in progressively while Ingest is still running (see
            # _on_ingest_step) -- a selected stage not computed yet just
            # leaves the previous image up with a "not available yet" label,
            # same as _set_detector_image(None, ...) already does for Raw/
            # Corrected before any data is loaded.
            if stage == 2:
                arr = preview.get("background_subtracted")
                self._det_frame_lbl.setText(
                    f"frame {preview['frame_index']} of the kept stack, from the last Ingest run "
                    "-- a single frame is usually near-empty, see the max-projection view"
                    if arr is not None else "not available yet -- Ingest still running")
            elif stage == 3:
                arr = preview.get("background_subtracted_max_projection")
                self._det_frame_lbl.setText(
                    "max over every kept/live frame, from the last Ingest run"
                    if arr is not None else "not available yet -- Ingest still running")
            else:
                mask_arr = preview.get("mask")
                arr = mask_arr.astype(np.float32) if mask_arr is not None else None
                self._det_frame_lbl.setText(
                    "detector mask, from the last Ingest run"
                    if arr is not None else "not available yet -- Ingest still running")
            self._set_detector_image(arr, stage)
            self._update_spot_overlay(stage, preview.get("loaded_frame_index"))

    def _update_spot_overlay(self, stage: int, current_loaded_idx: Optional[int]):
        """Circle ingest-identified spots (``self._spots_df``) on the frame
        closest to each spot's blob centroid -- see ``loaded_frame_idx``,
        added to ``spots_df`` by ``pipeline._stage_ingest`` precisely for
        this. Raw/Corrected/the single-frame background preview all compare
        against one ``current_loaded_idx`` (the ORIGINALLY loaded-stack frame
        index); the max-over-rotation projection shows every spot since it
        already collapses the whole rotation into one image; the mask has no
        frame to match against at all."""
        self._det_selected.setData([])
        df = self._spots_df
        if df is None or len(df) == 0 or "loaded_frame_idx" not in df.columns:
            self._spot_scatter.setData([])
            return
        if "panel_id" in df.columns:
            # Pooled ingest: row/col are per-panel pixel coordinates, so only
            # the ACTIVE panel's own spots belong on its detector frame.
            df = df[df["panel_id"] == self._active_panel_id()]
        if stage == 3:
            sel = df
        elif stage in (0, 1, 2):
            if current_loaded_idx is None:
                self._spot_scatter.setData([])
                return
            sel = df[df["loaded_frame_idx"] == current_loaded_idx]
        else:
            self._spot_scatter.setData([])
            return
        if len(sel) == 0:
            self._spot_scatter.setData([])
        else:
            ids = sel["spot_id"].to_numpy() if "spot_id" in sel else sel.index.to_numpy()
            self._spot_scatter.setData(x=sel["col"].to_numpy(), y=sel["row"].to_numpy(), data=ids)
            selected = sel[np.asarray(ids) == self._selected_spot_id]
            if len(selected):
                self._det_selected.setData(x=selected["col"].to_numpy(), y=selected["row"].to_numpy())
            if stage != 3:
                self._det_frame_lbl.setText(f"{self._det_frame_lbl.text()} — {len(sel)} spot(s)")

    def _set_detector_image(self, frame, stage: int):
        """``autorange`` (pan/zoom) only resets when the frame *shape*
        changes -- switching Stage combo entries alone (e.g. Raw <-> Corrected,
        same detector, same zoom) must not reset the view the user zoomed
        into. ``reset_levels`` (color window) still resets per-stage, since
        Calculated background/Mask carry a genuinely different data range
        than Raw/Corrected and a stale color window would just look blank."""
        shape = None if frame is None else tuple(frame.shape)
        zoom_fresh = shape != self._det_view_shape
        framed_for = (shape, stage)
        levels_fresh = framed_for != self._det_view_framed_for
        if frame is not None:
            self._det_view.set_raw_frame(frame, None, autorange=zoom_fresh, reset_levels=levels_fresh)
        self._det_view_shape = shape
        self._det_view_framed_for = framed_for

    # ── panel management ───────────────────────────────────────────

    @property
    def _next_panel_id(self) -> int:
        return self._panel_tabs.count() + 1

    def _add_panel(self) -> PanelCard:
        """Append a panel. Its number is its id and is always the next one in
        the 1, 2, 3, … sequence."""
        card = PanelCard(self._panel_tabs.count() + 1)
        # Live-refresh the Detector tab (Raw/Corrected only) as this panel's
        # data/frame-index or dark/bright/background/mask fields change --
        # same signals tab_batch.py's own Detector view refreshes from.
        card.loader.dataChanged.connect(self._refresh_detector_preview)
        card.loader.fieldsChanged.connect(self._refresh_detector_preview)
        card.calibrationClicked.connect(self._show_calibrations)
        idx = self._panel_tabs.addWidget(card)
        self._panels[idx + 1] = card
        self._panel_ribbon.set_count(self._panel_tabs.count())
        self._panel_tabs.setCurrentIndex(idx)
        self._panel_ribbon.set_current(idx)
        return card

    def _on_ribbon_selected(self, index: int):
        if 0 <= index < self._panel_tabs.count():
            self._panel_tabs.setCurrentIndex(index)

    def _close_panel_tab(self, index: int):
        """Remove panel number ``index + 1``; later panels shift down so the
        numbering stays 1, 2, 3, …. Shifting changes their ids, which the
        spot table and previews are keyed by, so an existing ingest is
        discarded in that case (after asking)."""
        n = self._panel_tabs.count()
        if n <= 1:
            QtWidgets.QMessageBox.information(
                self, "Solve Cell", "At least one panel is required.")
            return
        shifts = index < n - 1
        if shifts and self._spots_df is not None:
            resp = QtWidgets.QMessageBox.question(
                self, "Remove panel",
                f"Removing panel {index + 1} renumbers the later panels, which "
                "invalidates the current ingest results (spots, domains). "
                "Discard them and remove the panel?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
            if resp != QtWidgets.QMessageBox.Yes:
                return
        card = self._panel_tabs.widget(index)
        self._panel_tabs.removeWidget(card)
        card.deleteLater()
        self._panels = {}
        for i in range(self._panel_tabs.count()):
            c = self._panel_tabs.widget(i)
            c.set_panel_id(i + 1)
            self._panels[i + 1] = c
        if shifts:
            self._ingest_preview_by_panel.clear()
            self._ingest_preview = None
            self._spots_df = None
            self._reset_ingest_results()
            self._ingest_status.setText("")
            self._diamond_btn.setEnabled(False)
            self._refresh_recip_view()
        else:
            self._ingest_preview_by_panel.pop(index + 1, None)
        self._panel_ribbon.set_count(self._panel_tabs.count())
        self._panel_ribbon.set_current(self._panel_tabs.currentIndex())
        self._refresh_detector_preview()

    def _show_calibrations(self, selected_panel_id: int = 0):
        """Popup listing every panel's loaded calibration parameters."""
        cols = [(pid, c.calibration_summary()) for pid, c in sorted(self._panels.items())]
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Loaded calibrations")
        dv = QtWidgets.QVBoxLayout(dlg)
        labels = []
        for _pid, rows in cols:
            for lab, _v in rows or []:
                if lab != "note" and lab not in labels:
                    labels.append(lab)
        notes = {pid: [v for lab, v in (rows or []) if lab == "note"] for pid, rows in cols}
        table = QtWidgets.QTableWidget(len(labels), len(cols))
        table.setHorizontalHeaderLabels([f"Panel {pid}" for pid, _ in cols])
        table.setVerticalHeaderLabels(labels)
        for c, (_pid, rows) in enumerate(cols):
            vals = dict(rows or [])
            for r, lab in enumerate(labels):
                text = vals.get(lab, "—") if rows else ("no calibration loaded" if r == 0 else "")
                table.setItem(r, c, QtWidgets.QTableWidgetItem(text))
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        table.resizeColumnsToContents()
        dv.addWidget(table)
        lines = [f"Panel {pid}: {n}" for pid, ns in notes.items() for n in ns]
        if lines:
            nl = QtWidgets.QLabel("\n".join(lines))
            nl.setWordWrap(True); nl.setStyleSheet(f"color:{S.MUTED};font-size:11px")
            dv.addWidget(nl)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        bb.rejected.connect(dlg.reject)
        dv.addWidget(bb)
        if selected_panel_id and 1 <= selected_panel_id <= len(cols):
            table.selectColumn(selected_panel_id - 1)
        dlg.resize(min(900, 160 + 170 * len(cols)), 420)
        dlg.exec_()

    ABOUT_TEXT = (
        "Solve Cell determines an unknown crystal lattice from raw diffraction "
        "frames. For each detector panel you load a calibration file, the raw "
        "frames (with optional dark, bright, background and mask), and the "
        "frame-to-omega mapping. Run Ingest finds diffraction spots in every "
        "frame and converts them to reciprocal-space g-vectors using each "
        "panel's own geometry; with more than one panel ready, the panels are "
        "ingested independently and their g-vectors are pooled. The diamond "
        "filter then removes spots from the diamond anvils, ab-initio indexing "
        "searches for a lattice that explains the remaining spots, and the "
        "refinement step fits that lattice freely to the data. Index Remaining "
        "Spots repeats the search on whatever is left, so several crystal "
        "domains in one sample can be solved one after another. The Detector, "
        "Reciprocal, Diagnostics, Volume, Inspector, Contamination and Compare "
        "views help you check each stage. This tab is a work in progress.")

    def show_about(self):
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("About Solve Cell")
        box.setIcon(QtWidgets.QMessageBox.NoIcon)
        box.setTextFormat(QtCore.Qt.PlainText)
        box.setText(self.ABOUT_TEXT)
        box.exec_()

    def _active_panel(self) -> PanelCard:
        return self._panel_tabs.currentWidget()

    def _active_panel_id(self) -> Optional[int]:
        panel = self._active_panel()
        return next((pid for pid, c in self._panels.items() if c is panel), None)

    # ── project context (FAIR provenance) ──────────────────────────

    def set_project_context(self, ctx):
        self._project_ctx = ctx

    # ── helpers ─────────────────────────────────────────────────────

    def _browse_project_dir(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Solve Cell project folder")
        if d:
            self._proj_ed.setText(d)

    def _project_dir(self) -> Optional[Path]:
        text = self._proj_ed.text().strip()
        return Path(text) if text else None

    def _geometry_cfg(self) -> dict:
        return self._active_panel().geometry_cfg()

    def _on_stage_fail(self, stage_label: str, msg: str, btn: QtWidgets.QPushButton):
        btn.setEnabled(True)
        self._prog.setVisible(False)
        show_error(self, f"Solve Cell: {stage_label} failed", msg, log=self._log, log_prefix="\nERROR:\n")

    # ── GUI state (project save/restore) ───────────────────────────

    def _stage_state_widgets(self) -> dict:
        widgets = {
            "proj_dir": self._proj_ed,
            "low_count": self._low_count, "mask_grow": self._mask_grow,
            "blob_thresh": self._blob_thresh, "blob_minvol": self._blob_minvol,
            "split_ratio": self._split_ratio, "gap_bridge": self._gap_bridge,
            "gap_bridge_on": self._gap_bridge_on, "ingest_max_workers": self._ingest_max_workers,
            "sector_candidates": self._sector_candidates_ed,
            "diamond_a": self._diamond_a, "contam_tol": self._contam_tol,
            "sigma_g": self._sigma_g, "min_refl": self._min_refl, "tol_override": self._tol_override,
            "index_method": self._index_method, "leftover_method": self._leftover_method,
        }
        for prefix, ks in (("ks3", self._ks3), ("ks5", self._ks5)):
            for key in ("a", "b", "c", "alpha", "beta", "gamma", "sg", "n_search", "tol", "min_accept"):
                widgets[f"{prefix}_{key}"] = ks[key]
        return widgets

    def get_state(self) -> dict:
        """Panel configuration + stage parameters only. Like every other
        tab's project save (see ``MainWindow._apply_workspace_state``'s own
        docstring), Ingest/Diamond filter/Ab-initio/Refine results are not
        recomputed on restore -- their inputs are restored so a single click
        of each stage's own Run button reproduces them."""
        active_id = self._active_panel_id()
        return {
            "fields": widgets_to_dict(self._stage_state_widgets()),
            "panels": {str(pid): card.get_state() for pid, card in self._panels.items()},
            "active_panel": active_id,
            "next_panel_id": self._next_panel_id,
        }

    def set_state(self, state: dict):
        if not state:
            return
        apply_dict_to_widgets(self._stage_state_widgets(), state.get("fields", {}))
        panels_state = state.get("panels") or {}
        if panels_state:
            for card in list(self._panels.values()):
                self._panel_tabs.removeWidget(card)
                card.deleteLater()
            self._panels.clear()
            # Panels are numbered 1..N in order; a project saved with gaps in
            # its ids is renumbered by rank (active_panel follows below).
            saved_ids = sorted(int(k) for k in panels_state)
            for pid in saved_ids:
                self._add_panel().set_state(panels_state[str(pid)])
        else:
            saved_ids = []
        active = state.get("active_panel")
        if active is not None and saved_ids and int(active) in saved_ids:
            self._panel_tabs.setCurrentIndex(saved_ids.index(int(active)))
        elif active is not None and not saved_ids and int(active) in self._panels:
            self._panel_tabs.setCurrentIndex(int(active) - 1)
        self._refresh_detector_preview()

    # ── stage 1: ingest ─────────────────────────────────────────────

    def _sector_candidates_cfg(self) -> tuple:
        text = self._sector_candidates_ed.text().strip()
        try:
            vals = tuple(int(x.strip()) for x in text.split(",") if x.strip())
        except ValueError:
            vals = ()
        return vals if vals else solve_pipeline.SECTOR_CANDIDATES_DEFAULT

    def _panel_ingest_cfg(self, panel: "PanelCard", panel_dir: Optional[Path]) -> dict:
        """The per-panel cfg dict ``_stage_ingest``/``_stage_ingest_pooled``
        expect -- shared by the single-panel and pooled run paths below so
        they build each panel's inputs identically."""
        return {
            "frames_loader": panel.loader.full_stack, "geometry": panel.geometry_cfg(),
            "corrections": panel.corrections_cfg(),
            "mask": {"low_count_threshold": self._low_count.value(), "grow": self._mask_grow.value(),
                     "user_mask": panel.loader.composite_mask()},
            "blobs": {"threshold": self._blob_thresh.value(), "min_vol": self._blob_minvol.value(),
                      "split_ratio": self._split_ratio.value(), "gap_bridge": self._gap_bridge.value() if self._gap_bridge_on.isChecked() else 0,
                      "sector_candidates": self._sector_candidates_cfg(),
                      "max_workers": self._ingest_max_workers.value()},
            "panel_dir": panel_dir,
            # Best-effort: the loader's frame index is into the ORIGINALLY
            # loaded stack, while the pipeline's preview_frame_index is into
            # the kept stack after the omega window + live-frame filtering --
            # these only coincide exactly when nothing was dropped. Harmless
            # either way: _stage_ingest clamps it into range, and the
            # returned preview["frame_index"] says which kept-stack frame was
            # actually captured, which the Detector tab labels honestly.
            "preview_frame_index": panel.loader.frame_index(),
        }

    def _run_ingest(self):
        if self._worker is not None and self._worker.isRunning():
            return
        active_panel = self._active_panel()
        if not active_panel.has_calibration():
            QtWidgets.QMessageBox.warning(
                self, "No calibration loaded",
                "Load a calibration file for this panel first."); return
        # Cheap, metadata-only check -- the real read (full_stack(), ~10 GB for
        # an HDF5-backed stack at full detector resolution) happens inside the
        # worker thread below, not here on the GUI thread, so the window
        # doesn't freeze with no progress shown while it runs.
        if active_panel.loader.data_source_kind() == "none":
            QtWidgets.QMessageBox.warning(self, "No raw data", "Load raw frames first."); return
        for sel in active_panel.loader.has_pending_fields():
            QtWidgets.QMessageBox.warning(
                self, "Field not computed",
                f"'{sel.title()}' is enabled but not computed. "
                "Click 'Compute field' in that box first."); return

        # Every OTHER configured panel that is itself fully ready (its own
        # calibration + data loaded, nothing pending) joins this run too --
        # handoff §5.2/§10 multi-panel pooling. A panel tab that ISN'T ready
        # yet is silently skipped (logged, not blocked) rather than forcing
        # every scratch/unconfigured extra tab to be filled in first.
        ready: list[tuple[int, PanelCard]] = []
        skipped: list[int] = []
        for pid, card in sorted(self._panels.items()):
            if (card.has_calibration() and card.loader.data_source_kind() != "none"
                    and not card.loader.has_pending_fields()):
                ready.append((pid, card))
            else:
                skipped.append(pid)
        if skipped:
            self._log.append(
                f"Ingest: panel(s) {', '.join(str(p) for p in skipped)} not ready "
                "(no calibration/data, or a pending field) -- excluded from this run.")

        # omega_step_deg == 0.0 means every kept frame gets the SAME omega
        # (pipeline.py: omega_deg_all = ref_deg + arange(n) * step) --
        # silently collapses the whole rotation to one orientation instead of
        # raising, so catch it here before Ingest runs rather than leave a
        # user to puzzle over a badly distorted reciprocal-space result (see
        # .context/DECISIONS.md). Only a panel whose start and end omega
        # differ is flagged: with step 0 the last frame idx can't be derived
        # from them, so a mapping the user actually filled in lacks its step;
        # one never touched (start == end) doesn't trip this.
        zero_step = [pid for pid, card in ready if card.omega_span_without_step()]
        if zero_step:
            resp = QtWidgets.QMessageBox.warning(
                self, "Omega step is 0°",
                f"Panel(s) {', '.join(str(p) for p in zero_step)} have different "
                "start and end omega but 'omega step' (Frame ↔ omega mapping) is 0.0° -- every kept "
                "frame will be assigned the SAME omega, which badly distorts the "
                "reciprocal-space result (each panel needs its own step filled in).\n\n"
                "Run ingest anyway?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
            if resp != QtWidgets.QMessageBox.Yes:
                return

        # The calibration's image transform (ImTransOpt) can't be verified from
        # the data alone; a calibration that doesn't declare it is the one way
        # it gets silently dropped, so make the user confirm (pipeline.py also
        # warns/records this on every ingest regardless).
        undeclared = [pid for pid, card in ready
                      if not card.geometry_cfg().get("im_trans_in_file", False)]
        if undeclared:
            resp = QtWidgets.QMessageBox.warning(
                self, "Calibration image transform not declared",
                f"The calibration file(s) for panel(s) {', '.join(str(p) for p in undeclared)} "
                "don't declare 'im_trans' (flip/transpose applied to the image when "
                "calibrating), so none will be applied. If they were calibrated on a "
                "flipped image, every spot position will be wrong.\n\nRun ingest anyway?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No)
            if resp != QtWidgets.QMessageBox.Yes:
                return

        project_dir = self._project_dir()
        self._pooled_dir = project_dir / "pooled" if project_dir is not None else None

        if len(ready) <= 1:
            panel_dir = None
            if project_dir is not None:
                panel_dir = project_dir / solve_pipeline.panel_dir_name(active_panel.panel_id()) / "data"
            self._panel_dir = panel_dir
            self._ingest_source_panel_id = active_panel.panel_id()
            cfg = self._panel_ingest_cfg(active_panel, panel_dir)
            self._ingest_btn.setEnabled(False)
            self._prog.setVisible(True)
            self._log.append("─" * 40 + "\nRunning ingest…")
            self._worker = SolveCellWorker("ingest", cfg, parent=self)
            self._worker.log_line.connect(self._log.append)
            self._worker.step.connect(self._on_ingest_step)
            self._worker.finished.connect(self._on_ingest_done)
            self._worker.failed.connect(self._on_ingest_fail)
            self._worker.start()
            return

        self._panel_dir = None   # pooled: outputs live per-panel-dir + pooled_dir, no single "the" panel_dir
        panels_cfg = []
        for pid, card in ready:
            panel_dir = None
            if project_dir is not None:
                panel_dir = project_dir / solve_pipeline.panel_dir_name(pid) / "data"
            pcfg = self._panel_ingest_cfg(card, panel_dir)
            pcfg["panel_id"] = pid
            panels_cfg.append(pcfg)
        cfg = {"panels": panels_cfg, "pooled_dir": self._pooled_dir}
        self._ingest_btn.setEnabled(False)
        self._prog.setVisible(True)
        self._log.append(
            "─" * 40 + f"\nRunning pooled ingest across {len(ready)} panels "
            f"({', '.join(str(pid) for pid, _ in ready)})…")
        self._worker = SolveCellWorker("ingest_pooled", cfg, parent=self)
        self._worker.log_line.connect(self._log.append)
        self._worker.step.connect(self._on_ingest_step)
        self._worker.finished.connect(self._on_ingest_pooled_done)
        self._worker.failed.connect(self._on_ingest_fail)
        self._worker.start()

    def _on_ingest_fail(self, msg: str):
        self._ingest_status.setText("Ingest failed -- see log.")
        self._on_stage_fail("Ingest", msg, self._ingest_btn)

    def _on_ingest_step(self, name: str, payload: dict):
        """Live progress during a running Ingest -- ``payload`` is
        ``pipeline._stage_ingest``'s ``on_step`` shape (see its docstring):
        ``preview`` fills in the same keys the final result's ``preview``
        carries, just progressively, so the Detector tab can show whatever
        has been computed so far rather than only the finished result.
        ``panel_id`` is only present for a pooled run (one worker thread,
        many panels) -- routes the update to that panel's own preview slot
        instead of clobbering whichever panel's step fired last."""
        pid = payload.get("panel_id", self._active_panel_id())
        preview = payload["preview"]
        if pid is not None:
            self._ingest_preview_by_panel[pid] = preview
        self._ingest_preview = preview
        stats = ", ".join(f"{k}={v}" for k, v in payload["stats"].items())
        label = f"Ingest running — {name}" + (f" ({stats})" if stats else "") + "…"
        if pid is not None:
            label = f"panel {pid}: {label}"
        self._ingest_status.setText(label)
        if pid is None or pid == self._active_panel_id():
            self._refresh_detector_preview()

    def _reset_ingest_results(self):
        """A new ingest invalidates all assignments into the old spot table."""
        self._flagged_df = self._candidate_df = self._ab_initio_raw = None
        self._viz_products, self._viz_unsaved = {}, set()
        if getattr(self, "_volume_view", None) is not None:
            self._volume_view.set_volume(None, None)
        self._g_2pi = None
        self._domains = []
        self._claimed_index = None
        self._round1_domain_index = None
        self._domain_list.clear()
        self._ab_btn.setEnabled(False)
        self._refine_btn.setEnabled(False)
        self._update_leftover_status()
        self._spot_details.setText("Hover a spot for details; click to select.")

    def _on_ingest_done(self, result: dict):
        self._ingest_btn.setEnabled(True); self._prog.setVisible(False)
        self._reset_ingest_results()
        self._spots_df = result["spots_df"].copy()
        if "spot_id" not in self._spots_df:
            self._spots_df["spot_id"] = np.arange(len(self._spots_df))
        self._selected_spot_id = None
        self._show_detector_btn.setEnabled(False)
        self._recip_has_data = False
        pid = self._ingest_source_panel_id or self._active_panel_id()
        if pid is not None and "panel_id" not in self._spots_df:
            self._spots_df["panel_id"] = pid
        n = len(self._spots_df)
        self._ingest_status.setText(f"{n} spots (n_frames≥2)")
        self._log.append(f"Ingest complete: {n} candidate spots.")
        for w in result.get("summary", {}).get("warnings", []):
            self._log.append(f"⚠ {w}")
        self._diamond_btn.setEnabled(n > 0)
        self._refresh_recip_view()
        self._ingest_preview = result.get("preview")
        if pid is not None:
            self._ingest_preview_by_panel[pid] = self._ingest_preview
        self._refresh_detector_preview()
        self._record_ingest(
            result, {pid: result.get("summary", {})} if pid is not None else {}, pooled=False)

    def _on_ingest_pooled_done(self, result: dict):
        """Multi-panel counterpart of :meth:`_on_ingest_done` -- ``result``
        is ``solve_pipeline._stage_ingest_pooled``'s own return shape
        (``spots_df`` already tagged by ``panel_id``, plus ``per_panel``)."""
        self._ingest_btn.setEnabled(True); self._prog.setVisible(False)
        self._reset_ingest_results()
        self._spots_df = result["spots_df"].copy()
        if "spot_id" not in self._spots_df:
            self._spots_df["spot_id"] = np.arange(len(self._spots_df))
        self._selected_spot_id = None
        self._show_detector_btn.setEnabled(False)
        self._recip_has_data = False
        n = len(self._spots_df)
        summary = result["summary"]
        per_panel = result["per_panel"]
        breakdown = ", ".join(f"panel {pid}: {r['summary']['n_spots']}" for pid, r in per_panel.items())
        self._ingest_status.setText(f"{n} spots across {summary['n_panels']} panels ({breakdown})")
        self._log.append(f"Ingest complete (pooled): {n} candidate spots across "
                         f"{summary['n_panels']} panels ({breakdown}).")
        for w in summary.get("warnings", []):
            self._log.append(f"⚠ {w}")
        self._diamond_btn.setEnabled(n > 0)
        self._refresh_recip_view()
        self._ingest_preview_by_panel = {pid: r.get("preview") for pid, r in per_panel.items()}
        self._refresh_detector_preview()
        self._record_ingest(result, {pid: r["summary"] for pid, r in per_panel.items()}, pooled=True)

    # ── project record: latest analysis stored in the open project ──────
    #
    # Solve Cell keeps ONE record in the project file (``/analysis/solve_cell``,
    # see project.py) holding the latest analysis: ingest inputs as run
    # (calibrations, data references + hashes, omega mapping, parameters) and
    # every result (spots, diamond flags, ab-initio, domains), so reopening
    # the project restores the whole analysis and each stage can be rerun.
    # Re-running a stage replaces its section and everything downstream.
    # Writes happen as each stage finishes while a project is open, and again
    # on project save for results computed before one was open.

    def _project_path(self) -> Optional[str]:
        return getattr(self._project_ctx, "path", None) if self._project_ctx else None

    def _panel_record(self, panel: "PanelCard", summary: dict, preview: Optional[dict]) -> dict:
        """One panel's as-run description: its GUI state (data/dark/bright/
        background/mask references, omega fields), the geometry the ingest
        used, and the calibration itself (parsed + file text)."""
        return {
            "panel_id": panel.panel_id(), "state": panel.get_state(),
            "geometry_cfg": panel.geometry_cfg() if panel.has_calibration() else None,
            "calibration": panel.calibration_record(),
            "summary": summary,
            "preview_meta": {k: (preview or {}).get(k) for k in ("frame_index", "loaded_frame_index")},
        }

    def _record_ingest(self, result: dict, panel_summaries: dict, *, pooled: bool):
        """Snapshot a just-finished ingest as the new record's ``ingest``
        section and write it. ``panel_summaries`` maps panel_id -> that
        panel's own ingest summary."""
        try:
            self._snapshot_ingest(result, panel_summaries, pooled=pooled)
        except Exception:
            import traceback as _tb
            self._log.append("Could not snapshot Solve Cell ingest for the project record:\n"
                             + _tb.format_exc())
            return
        self._sc_stage_meta = {}
        self._sc_dirty = {"ingest"}
        self.flush_project_record()

    def _snapshot_ingest(self, result: dict, panel_summaries: dict, *, pooled: bool):
        panels = {}
        self._sc_user_masks = {}
        for pid, summary in panel_summaries.items():
            card = self._panels.get(pid)
            if card is None:
                continue
            panels[str(pid)] = self._panel_record(
                card, summary, self._ingest_preview_by_panel.get(pid))
            try:
                um = card.loader.composite_mask()
            except Exception:
                um = None
            if um is not None:
                self._sc_user_masks[pid] = np.asarray(um) != 0
        self._sc_ingest_meta = {
            "pooled": pooled, "summary": result.get("summary", {}), "panels": panels,
            "fields": widgets_to_dict(self._stage_state_widgets()),
            "active_panel": self._active_panel_id(), "next_panel_id": self._next_panel_id,
            "source_panel_id": None if pooled else self._ingest_source_panel_id,
            "panel_dir": str(self._panel_dir) if self._panel_dir else None,
            "pooled_dir": str(self._pooled_dir) if self._pooled_dir else None,
            "status_text": self._ingest_status.text(),
        }

    def _mark_sc_dirty(self, stage: str):
        """A diamond/index stage changed in memory: snapshot the parameters it
        ran with and write the record if a project is open."""
        self._sc_stage_meta.setdefault(stage, {})["fields"] = widgets_to_dict(self._stage_state_widgets())
        self._sc_dirty.add(stage)
        self.flush_project_record()

    def flush_project_record(self):
        """Write whichever record sections are out of date to the open
        project (no-op without a project or results). Called as each stage
        completes and by ``MainWindow.save_project`` -- the latter covers
        results computed before a project existed, and a Save As to a new
        file (every section is rewritten whenever the target file changes).
        Never raises: a failed write is logged and retried on the next call."""
        path = self._project_path()
        if not path or self._spots_df is None or self._sc_ingest_meta is None:
            return
        if path != self._sc_record_path:
            self._sc_dirty = set(project.SOLVE_CELL_STAGES)
        if not self._sc_dirty:
            return
        dirty = set(self._sc_dirty)
        try:
            if "ingest" in dirty:
                self._write_sc_ingest(path)
                dirty |= {"diamond", "index"}   # the swap replaced the whole record
            if "diamond" in dirty:
                self._write_sc_diamond(path)
                dirty.add("index")              # built on the candidate pool
            if "index" in dirty:
                self._write_sc_index(path)
        except Exception:
            import traceback as _tb
            self._log.append("Could not write Solve Cell record to project file:\n" + _tb.format_exc())
            return
        self._sc_dirty.clear()
        self._sc_record_path = path
        self._persist_viz()
        self._log.append(f"Solve Cell record updated in project ({', '.join(sorted(dirty))}).")

    def _write_sc_ingest(self, path: str):
        panels = {}
        for k in self._sc_ingest_meta["panels"]:
            pid = int(k)
            arrays = {}
            preview = self._ingest_preview_by_panel.get(pid) or {}
            for name in _PREVIEW_ARRAYS:
                if preview.get(name) is not None:
                    arrays[name] = np.asarray(preview[name])
            if pid in self._sc_user_masks:
                arrays["user_mask"] = self._sc_user_masks[pid]
            panels[solve_pipeline.panel_dir_name(pid)] = arrays
        project.write_solve_cell_ingest(
            path, meta=self._sc_ingest_meta, spots_df=self._spots_df, panels=panels)

    def _write_sc_diamond(self, path: str):
        if self._flagged_df is None:
            project.clear_solve_cell_section(path, "diamond")
            return
        meta = dict(self._sc_stage_meta.get("diamond") or {})
        meta["status_text"] = self._diamond_status.text()
        project.write_solve_cell_diamond(path, meta=meta, flagged_df=self._flagged_df)

    def _write_sc_index(self, path: str):
        if self._ab_initio_raw is None and not self._domains:
            project.clear_solve_cell_section(path, "index")
            return
        ab_meta, ab_arrays = None, None
        raw = self._ab_initio_raw
        if raw is not None:
            ab_meta = {"success": bool(raw.success), "cell": raw.cell, "cell_sigma": raw.cell_sigma,
                       "indexed_fraction": float(raw.indexed_fraction),
                       "n_indexed": int(raw.n_indexed), "n_reflections": int(raw.n_reflections),
                       "notes": list(raw.notes), "diagnostics": dict(raw.diagnostics or {})}
            ab_arrays = {name: getattr(raw, name, None) for name in ("hkl", "indexed_mask", "UB", "UBI")}
        domains_meta, domain_arrays = [], []
        for d in self._domains:
            domains_meta.append({
                "label": d["label"], "method": d["method"],
                # the claimed rows are stored as an array; the mask is redundant
                "result": {k: v for k, v in d["result"].items() if k != "indexed_mask"}})
            arrays = {"claimed_index": np.asarray(d["claimed_index"]),
                      "xyz": np.column_stack([np.asarray(c) for c in d["xyz"]])}
            if d.get("panel_ids") is not None:
                arrays["panel_ids"] = np.asarray(d["panel_ids"])
            domain_arrays.append(arrays)
        meta = dict(self._sc_stage_meta.get("index") or {})
        meta.update({
            "ab_initio": ab_meta, "domains": domains_meta,
            "sigma_g_used": self._sigma_g_used,
            "round1_domain_index": self._round1_domain_index,
            "ab_status_text": self._ab_status.text(),
            "leftover_run_status_text": self._leftover_run_status.text(),
        })
        project.write_solve_cell_index(path, meta=meta, ab_initio=ab_arrays, domains=domain_arrays)

    def apply_project_solve_cell(self, record: dict, *, restore_config: bool = True):
        """Restore the whole recorded analysis (``project.read_solve_cell``)
        into the tab: results, domains, previews and status text, so each
        stage can be rerun from where it left off. ``restore_config`` also
        rebuilds the panel tabs and stage parameters exactly as they were
        when the analysis ran; pass False when the saved GUI workspace has
        already restored them (it then wins -- it may be newer), in which
        case only a panel with no calibration is filled in, from the
        record's embedded copy."""
        import pandas as pd
        from types import SimpleNamespace

        ing = record["ingest"]
        imeta = ing["meta"]
        dia, idx = record.get("diamond"), record.get("index")
        panels_rec = imeta.get("panels") or {}

        if restore_config:
            fields = {}
            for m in (imeta, (dia or {}).get("meta"), (idx or {}).get("meta")):
                fields.update((m or {}).get("fields") or {})
            self.set_state({
                "fields": fields,
                "panels": {k: v["state"] for k, v in panels_rec.items()},
                "active_panel": imeta.get("active_panel"),
                "next_panel_id": imeta.get("next_panel_id", 1),
            })
        for k, prec in panels_rec.items():
            card = self._panels.get(int(k))
            if card is not None and not card.has_calibration():
                card.restore_calibration_record(prec.get("calibration"))

        # ── ingest ──
        self._reset_ingest_results()
        spots = ing["spots_df"].copy()
        if "spot_id" not in spots:
            spots["spot_id"] = np.arange(len(spots))
        self._spots_df = spots
        self._selected_spot_id = None
        self._show_detector_btn.setEnabled(False)
        self._recip_has_data = False
        self._ingest_source_panel_id = imeta.get("source_panel_id")
        self._panel_dir = Path(imeta["panel_dir"]) if imeta.get("panel_dir") else None
        self._pooled_dir = Path(imeta["pooled_dir"]) if imeta.get("pooled_dir") else None
        self._ingest_status.setText(imeta.get("status_text") or f"{len(spots)} spots")
        self._diamond_btn.setEnabled(len(spots) > 0)
        self._ingest_preview_by_panel = {}
        self._sc_user_masks = {}
        for k, prec in panels_rec.items():
            pid = int(k)
            arrays = ing["panels"].get(solve_pipeline.panel_dir_name(pid), {})
            preview = {name: arrays[name] for name in _PREVIEW_ARRAYS if name in arrays}
            if preview:
                preview.update(prec.get("preview_meta") or {})
                self._ingest_preview_by_panel[pid] = preview
            if "user_mask" in arrays:
                self._sc_user_masks[pid] = arrays["user_mask"].astype(bool)
        self._ingest_preview = self._ingest_preview_by_panel.get(
            self._ingest_source_panel_id,
            next(iter(self._ingest_preview_by_panel.values()), None))

        # ── diamond filter ──
        dmeta = (dia or {}).get("meta") or {}
        if dia:
            flagged = dia["flagged_df"]
            self._flagged_df = flagged
            # same derivation as pipeline._stage_diamond_filter, so the
            # restored row labels match the saved domains' claimed_index
            self._candidate_df = flagged[~flagged["is_diamond"]].reset_index(drop=True)
            self._diamond_status.setText(dmeta.get("status_text") or "")
            self._ab_btn.setEnabled(len(self._candidate_df) > 0)

        # ── indexing + domains ──
        xmeta = (idx or {}).get("meta") or {}
        if idx and self._candidate_df is not None:
            ab = xmeta.get("ab_initio")
            if ab:
                arrs = idx["ab_initio"]
                self._ab_initio_raw = SimpleNamespace(
                    **ab, candidates=[], hkl=arrs.get("hkl"), indexed_mask=arrs.get("indexed_mask"),
                    UB=arrs.get("UB"), UBI=arrs.get("UBI"))
                self._g_2pi = self._candidate_df[["qsample_x", "qsample_y", "qsample_z"]].to_numpy()
                self._refine_btn.setEnabled(bool(ab["success"]))
            self._sigma_g_used = xmeta.get("sigma_g_used")
            self._ab_status.setText(xmeta.get("ab_status_text") or "—")
            self._leftover_run_status.setText(xmeta.get("leftover_run_status_text") or "—")
            for dm, arrs in zip(xmeta.get("domains") or [], idx["domains"]):
                claimed = pd.Index(arrs["claimed_index"])
                d = {"label": dm["label"], "method": dm["method"], "result": dm["result"],
                     "xyz": tuple(arrs["xyz"][:, i] for i in range(3)),
                     "claimed_index": claimed, "panel_ids": arrs.get("panel_ids")}
                self._domains.append(d)
                self._claimed_index = (claimed if self._claimed_index is None
                                       else self._claimed_index.union(claimed))
                self._domain_list.addItem(self._domain_text(d))
            self._round1_domain_index = xmeta.get("round1_domain_index")
            if self._domains:
                self._domain_list.setCurrentRow(len(self._domains) - 1)
            self._update_leftover_status()
            self._update_multidomain_plot()

        self._sc_ingest_meta = imeta
        self._viz_products = dict(record.get("viz") or {})
        self._viz_unsaved = set()
        self._show_cached_volume()
        self._show_cached_shoeboxes()
        self._refresh_recip_view()
        self._refresh_detector_preview()
        self._refresh_overlays()

        self._sc_ingest_meta = imeta
        self._sc_stage_meta = {"diamond": {k: dmeta[k] for k in ("fields", "summary") if k in dmeta},
                               "index": {k: xmeta[k] for k in ("fields",) if k in xmeta}}
        self._sc_dirty = set()
        self._sc_record_path = self._project_path()
        self._log.append(
            f"Restored Solve Cell analysis from project ({len(spots)} spots, "
            f"{len(self._domains)} domain(s), last stage: {record.get('last_stage')}).")

    # ── stage 2: diamond filter ─────────────────────────────────────

    def _run_diamond_filter(self):
        if self._worker is not None and self._worker.isRunning():
            return
        if self._spots_df is None or len(self._spots_df) == 0:
            QtWidgets.QMessageBox.warning(self, "No ingest output", "Run Ingest first."); return
        cfg = {
            "spots_df": self._spots_df, "wavelength_A": self._active_panel().geometry_cfg()["wavelength_A"],
            "diamond_a_angstrom": self._diamond_a.value(), "contam_tol_deg": self._contam_tol.value(),
            "panel_dir": self._panel_dir, "pooled_dir": self._pooled_dir,
        }
        self._diamond_btn.setEnabled(False)
        self._prog.setVisible(True)
        self._log.append("─" * 40 + "\nRunning diamond filter…")
        self._worker = SolveCellWorker("diamond_filter", cfg, parent=self)
        self._worker.log_line.connect(self._log.append)
        self._worker.finished.connect(self._on_diamond_done)
        self._worker.failed.connect(lambda msg: self._on_stage_fail("Diamond filter", msg, self._diamond_btn))
        self._worker.start()

    def _on_diamond_done(self, result: dict):
        self._diamond_btn.setEnabled(True); self._prog.setVisible(False)
        # A new candidate_df means every domain claim so far (row labels into
        # the OLD candidate_df, which _stage_diamond_filter always rebuilds
        # with a fresh 0..N-1 index) is stale and would misapply to the new
        # one -- clear the whole domain list rather than risk silently
        # claiming the wrong rows.
        self._domains = []
        self._claimed_index = None
        self._round1_domain_index = None
        self._domain_list.clear()
        self._leftover_btn.setEnabled(False)
        self._ks5_fill_btn.setEnabled(False)
        self._leftover_status.setText("—")
        self._leftover_run_status.setText("—")
        self._ab_initio_raw = None
        self._g_2pi = None
        self._refine_btn.setEnabled(False)
        self._candidate_df = result["candidate_df"]
        self._flagged_df = result["flagged_df"]
        summary = result["summary"]
        self._diamond_status.setText(
            f"{summary['n_diamond_flagged']}/{summary['n_spots']} flagged, "
            f"{summary['n_kept']} candidates remain")
        self._log.append(f"Diamond filter complete: {summary['n_diamond_flagged']} flagged, "
                         f"{summary['n_kept']} candidates kept.")
        self._ab_btn.setEnabled(len(self._candidate_df) > 0)
        self._refresh_recip_view()
        self._sc_stage_meta["diamond"] = {"summary": summary}
        self._mark_sc_dirty("diamond")

    # ── stage 3: index (blind ab-initio, or a user-supplied known cell) ──

    def _build_known_structure_form(self) -> dict:
        """Lattice-parameter + space-group entry, plus the known-cell
        search-tuning fields, shared by stage 3's 'Known structure' index
        mode and stage 5's 'Index remaining spots'. Each call builds a fresh
        set of spinboxes -- stage 3 and stage 5 keep fully independent
        values. Returns ``{"widget": <container>, "form": <its QFormLayout>,
        "a"/"b"/"c"/"alpha"/"beta"/"gamma": cell spinboxes, "sg": space-group
        spinbox, "n_search"/"tol"/"min_accept": search-tuning spinboxes}``."""
        host = QtWidgets.QWidget()
        f = QtWidgets.QFormLayout(host); f.setContentsMargins(0, 0, 0, 0); f.setSpacing(4)

        def _row(pairs):
            h = QtWidgets.QHBoxLayout(); h.setSpacing(4); h.setContentsMargins(0, 0, 0, 0)
            for label, w in pairs:
                lw = QtWidgets.QLabel(label)
                lw.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                h.addWidget(lw); h.addWidget(w)
            h.addStretch(1)
            return h

        a = _fspin(0.1, 1000.0, 4, 5.0, "Å")
        a.setToolTip("Known/candidate cell edge length a. Default: 5.0 Å.")
        b = _fspin(0.1, 1000.0, 4, 5.0, "Å")
        b.setToolTip("Known/candidate cell edge length b. Default: 5.0 Å.")
        c = _fspin(0.1, 1000.0, 4, 5.0, "Å")
        c.setToolTip("Known/candidate cell edge length c. Default: 5.0 Å.")
        f.addRow(_row([("a:", a), ("b:", b), ("c:", c)]))
        alpha = _fspin(1.0, 179.0, 2, 90.0, "°")
        alpha.setToolTip("Known/candidate cell angle α. Default: 90.0°.")
        beta = _fspin(1.0, 179.0, 2, 90.0, "°")
        beta.setToolTip("Known/candidate cell angle β. Default: 90.0°.")
        gamma = _fspin(1.0, 179.0, 2, 90.0, "°")
        gamma.setToolTip("Known/candidate cell angle γ. Default: 90.0°.")
        f.addRow(_row([("α:", alpha), ("β:", beta), ("γ:", gamma)]))

        # Capped to the same width _fspin() uses -- a bare QFormLayout field
        # widget otherwise stretches to fill the whole column, and with
        # right-aligned text (_NoScrollSpinBox's default) the value renders
        # off past the visible edge of this scrollable middle column.
        sg = _NoScrollSpinBox(); sg.setRange(1, 230); sg.setValue(1)
        sg.setMaximumWidth(104)
        sg.setToolTip(
            "International Tables space-group number. Used only as a "
            "post-hoc systematic-absence sanity check on the accepted "
            "match -- it does NOT constrain the orientation search itself, "
            "which uses the cell above only. Default: 1 (P1, no absences "
            "checked).")
        f.addRow("space group:", sg)

        n_search = _NoScrollSpinBox(); n_search.setRange(1_000, 5_000_000)
        n_search.setSingleStep(10_000)
        n_search.setValue(solve_pipeline.RANDOM_SEARCH_N_DEFAULT)
        n_search.setMaximumWidth(104)
        n_search.setToolTip(
            "Number of random trial orientations of the known cell tried "
            "against the measured g-vectors when searching for a match. "
            f"Default: {solve_pipeline.RANDOM_SEARCH_N_DEFAULT:,}.")
        tol = _fspin(0.01, 1.0, 3, solve_pipeline.RANDOM_SEARCH_TOL_DEFAULT)
        tol.setToolTip(
            "Acceptance tolerance (fractional-HKL units) for deciding "
            "whether a g-vector matches an integer HKL under a trial "
            f"orientation. Default: {solve_pipeline.RANDOM_SEARCH_TOL_DEFAULT:g}.")
        f.addRow(_twocol("search count:", n_search, "tol:", tol))
        min_accept = _NoScrollSpinBox(); min_accept.setRange(3, 100_000)
        min_accept.setValue(solve_pipeline.RANDOM_SEARCH_MIN_ACCEPT_DEFAULT)
        min_accept.setMaximumWidth(104)
        min_accept.setToolTip(
            "Minimum number of matched reflections required for a trial "
            "orientation to be accepted as a successful index/match. "
            f"Default: {solve_pipeline.RANDOM_SEARCH_MIN_ACCEPT_DEFAULT}.")
        f.addRow("min accept:", min_accept)

        return {"widget": host, "form": f, "a": a, "b": b, "c": c,
                "alpha": alpha, "beta": beta, "gamma": gamma, "sg": sg,
                "n_search": n_search, "tol": tol, "min_accept": min_accept}

    @staticmethod
    def _known_structure_cell(ks: dict) -> tuple:
        return (ks["a"].value(), ks["b"].value(), ks["c"].value(),
                ks["alpha"].value(), ks["beta"].value(), ks["gamma"].value())

    def _on_index_method_changed(self, index: int):
        self._ab_free_params.setVisible(index == 0)
        self._sigma_g_box.setVisible(index == 0)
        self._ks3["widget"].setVisible(index == 1)

    def _run_index(self):
        if self._index_method.currentIndex() == 0:
            self._run_ab_initio()
        else:
            self._run_known_structure_index()

    def _run_ab_initio(self):
        if self._worker is not None and self._worker.isRunning():
            return
        if self._candidate_df is None or len(self._candidate_df) == 0:
            QtWidgets.QMessageBox.warning(self, "No candidates", "Run Diamond Filter first."); return
        cfg = {
            "candidate_df": self._candidate_df, "sigma_g": self._sigma_g.value(),
            "min_reflections": self._min_refl.value(), "pooled_dir": self._pooled_dir,
            "tol": self._tol_override.value(),
        }
        self._ab_btn.setEnabled(False)
        self._prog.setVisible(True)
        self._log.append("─" * 40 + "\nRunning ab-initio indexing…")
        self._worker = SolveCellWorker("ab_initio", cfg, parent=self)
        self._worker.log_line.connect(self._log.append)
        self._worker.finished.connect(self._on_ab_initio_done)
        self._worker.failed.connect(lambda msg: self._on_stage_fail("Ab-initio index", msg, self._ab_btn))
        self._worker.start()

    def _run_known_structure_index(self):
        if self._worker is not None and self._worker.isRunning():
            return
        if self._candidate_df is None or len(self._candidate_df) == 0:
            QtWidgets.QMessageBox.warning(self, "No candidates", "Run Diamond Filter first."); return
        known_cell = self._known_structure_cell(self._ks3)
        self._known_structure_g_2pi = self._candidate_df[
            ["qsample_x", "qsample_y", "qsample_z"]].to_numpy()
        cfg = {
            "g_2pi": self._known_structure_g_2pi, "known_cell": known_cell,
            "space_group": self._ks3["sg"].value(),
            "tol": self._ks3["tol"].value(), "n_search": self._ks3["n_search"].value(),
            "min_accept": self._ks3["min_accept"].value(),
            "sigma_g": self._sigma_g.value(), "pooled_dir": self._pooled_dir,
        }
        self._ab_btn.setEnabled(False)
        self._refine_btn.setEnabled(False)
        self._prog.setVisible(True)
        self._log.append(
            "─" * 40 + f"\nIndexing (known structure, cell="
            f"{tuple(round(v, 4) for v in known_cell)})…")
        self._worker = SolveCellWorker("index_known_cell", cfg, parent=self)
        self._worker.log_line.connect(self._log.append)
        self._worker.finished.connect(self._on_index_known_structure_done)
        self._worker.failed.connect(lambda msg: self._on_stage_fail("Index", msg, self._ab_btn))
        self._worker.start()

    def _on_index_known_structure_done(self, result: dict):
        self._ab_btn.setEnabled(True); self._prog.setVisible(False)
        if not result.get("success"):
            notes = "; ".join(result.get("notes", [])) or "no notes"
            diag = result.get("diagnostics", {})
            self._ab_status.setText(f"No match found: {notes}")
            self._log.append(f"Known-structure index: negative result -- {notes}")
            if diag:
                self._log.append(
                    f"  best_n={diag.get('best_n')} null_mean={diag.get('null_mean', 0):.1f} "
                    f"z={diag.get('z_score', float('nan')):.2f}")
            return
        mask = result["indexed_mask"]
        claimed_index = self._candidate_df.index[mask]
        g_2pi = self._known_structure_g_2pi
        xyz = (g_2pi[mask, 0], g_2pi[mask, 1], g_2pi[mask, 2])
        panel_ids = (self._candidate_df.loc[claimed_index, "panel_id"].to_numpy()
                     if "panel_id" in self._candidate_df.columns else None)
        self._ab_status.setText(
            f"success=True  indexed {int(mask.sum())}/{len(self._candidate_df)}, "
            f"a={result['cell'][0]:.4g} Å, rms_drlv={result['rms_drlv']:.4g}")
        self._log.append(
            f"Known-structure index: cell={tuple(round(v, 4) for v in result['cell'])} "
            f"rms_drlv={result['rms_drlv']:.5f}")
        for note in result.get("notes", []):
            self._log.append(f"  note: {note}")
        if self._round1_domain_index is not None:
            self._replace_domain(self._round1_domain_index, "known structure (manual)",
                                  result, claimed_index, xyz, panel_ids)
        else:
            self._add_domain("known structure (manual)", result, claimed_index, xyz, panel_ids,
                             round1=True)

    def _on_ab_initio_done(self, result: dict):
        self._ab_btn.setEnabled(True); self._prog.setVisible(False)
        res = result["ab_initio_result"]
        self._ab_initio_raw = result["ab_initio_raw"]
        self._g_2pi = result["g_2pi"]
        self._sigma_g_used = result["sigma_g"]

        self._ab_status.setText(
            f"success={res['success']}  indexed {res['n_indexed']}/{res['n_reflections']} "
            f"({res['indexed_fraction'] * 100:.1f}%)")
        self._log.append(f"Ab-initio: success={res['success']} "
                         f"indexed {res['n_indexed']}/{res['n_reflections']}")
        for note in res["notes"]:
            self._log.append(f"  note: {note}")
        self._refine_btn.setEnabled(bool(res["success"]))
        self._mark_sc_dirty("index")

        if not res["success"]:
            return

        self._refresh_recip_view()

    # ── stage 4: refine ─────────────────────────────────────────────

    def _run_refine(self):
        if self._worker is not None and self._worker.isRunning():
            return
        if self._ab_initio_raw is None or not self._ab_initio_raw.success:
            QtWidgets.QMessageBox.warning(self, "No ab-initio result", "Run Ab-initio Index first."); return
        cfg = {
            "ab_initio_raw": self._ab_initio_raw, "g_2pi": self._g_2pi,
            "sigma_g": self._sigma_g_used, "pooled_dir": self._pooled_dir,
        }
        self._refine_btn.setEnabled(False)
        self._prog.setVisible(True)
        self._log.append("─" * 40 + "\nRunning refinement…")
        self._worker = SolveCellWorker("refine", cfg, parent=self)
        self._worker.log_line.connect(self._log.append)
        self._worker.finished.connect(self._on_refine_done)
        self._worker.failed.connect(lambda msg: self._on_stage_fail("Refine", msg, self._refine_btn))
        self._worker.start()

    def _on_refine_done(self, result: dict):
        self._refine_btn.setEnabled(True); self._prog.setVisible(False)
        r = result["refine_result"]
        self._log.append(
            f"Refine complete: cell={tuple(round(v, 4) for v in r['cell'])} "
            f"rms_drlv={r['rms_drlv']:.5f} holohedry={r['holohedry_system']}")
        mask = self._ab_initio_raw.indexed_mask
        claimed_index = self._candidate_df.index[mask]
        xyz = (self._g_2pi[mask, 0], self._g_2pi[mask, 1], self._g_2pi[mask, 2])
        panel_ids = (self._candidate_df.loc[claimed_index, "panel_id"].to_numpy()
                     if "panel_id" in self._candidate_df.columns else None)
        if self._round1_domain_index is not None:
            self._replace_domain(self._round1_domain_index, "free (ab-initio)", r, claimed_index, xyz, panel_ids)
        else:
            self._add_domain("free (ab-initio)", r, claimed_index, xyz, panel_ids, round1=True)

    def _populate_result_grid(self, r: dict):
        grid = self._param_grid
        while grid.count():
            item = grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        names = ["a", "b", "c", "α", "β", "γ"]
        pairs = []
        for name, val, sig in zip(names, r["cell"], r["cell_sigma"]):
            unit = " Å" if name in ("a", "b", "c") else "°"
            sig_text = f" ± {sig:.4g}" if sig == sig else ""
            pairs.append((name, f"{val:.5g}{unit}{sig_text}"))
        conv_lengths = ", ".join(f"{v:.5g}" for v in r["conventional_cell"][:3])
        pairs.append(("conventional", f"{r['conventional_system']} ({conv_lengths})"))
        pairs.append(("holohedry", f"{r['holohedry_system']} (order {r['holohedry_order']})"))
        pairs.append(("rms_drlv", f"{r['rms_drlv']:.5g}"))
        pairs.append(("n_reflections", str(r["n_reflections"])))
        for row, (key, val) in enumerate(pairs):
            k = QtWidgets.QLabel(f"{key}:")
            k.setStyleSheet("font-weight:600;font-size:12px")
            v = QtWidgets.QLabel(val)
            v.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            grid.addWidget(k, row, 0, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            grid.addWidget(v, row, 1, QtCore.Qt.AlignVCenter)
        grid.setColumnStretch(2, 1)

    # ── domain list (round 1's refine result + every "index remaining" round) ──

    def _on_domain_selected(self, row: int):
        if row < 0 or row >= len(self._domains):
            return
        self._populate_result_grid(self._domains[row]["result"])
        self._refresh_overlays()

    def _add_domain(self, method: str, result: dict, claimed_index, xyz, panel_ids=None,
                    *, round1: bool = False) -> int:
        """Append a new solved domain and return its index in
        ``self._domains``. ``claimed_index`` is the set of ``candidate_df``
        row labels this domain claims (never overlapping an earlier domain's
        claim -- see ``_leftover_df``). ``panel_ids`` is an optional array
        parallel to ``xyz`` (``None`` when the source had no ``panel_id``
        column), used by the Panel color mode. ``round1`` marks it as the
        domain a re-run of round 1 (Refine / known-structure index) replaces;
        set here, before the project record is written, so a restored project
        replaces it too instead of appending a duplicate."""
        label = f"Domain {len(self._domains) + 1}"
        self._domains.append({"label": label, "method": method, "result": result,
                              "xyz": xyz, "claimed_index": claimed_index, "panel_ids": panel_ids})
        self._claimed_index = (claimed_index if self._claimed_index is None
                               else self._claimed_index.union(claimed_index))
        self._domain_list.addItem(self._domain_text(self._domains[-1]))
        self._domain_list.setCurrentRow(len(self._domains) - 1)
        if round1:
            self._round1_domain_index = len(self._domains) - 1
        self._update_leftover_status()
        self._update_multidomain_plot()
        self._mark_sc_dirty("index")
        return len(self._domains) - 1

    def _replace_domain(self, idx: int, method: str, result: dict, claimed_index, xyz, panel_ids=None):
        """Re-running Refine (round 1) re-derives the same domain rather than
        appending a duplicate -- the claimed-index union is rebuilt from
        scratch across every domain so a shrunk ``indexed_mask`` on rerun
        correctly releases rows it no longer claims."""
        label = self._domains[idx]["label"]
        self._domains[idx] = {"label": label, "method": method, "result": result,
                              "xyz": xyz, "claimed_index": claimed_index, "panel_ids": panel_ids}
        self._claimed_index = None
        for d in self._domains:
            self._claimed_index = (d["claimed_index"] if self._claimed_index is None
                                   else self._claimed_index.union(d["claimed_index"]))
        self._domain_list.item(idx).setText(self._domain_text(self._domains[idx]))
        self._domain_list.setCurrentRow(idx)
        self._update_leftover_status()
        self._update_multidomain_plot()
        self._mark_sc_dirty("index")

    @staticmethod
    def _domain_text(d: dict) -> str:
        return (f"{d['label']} ({d['method']}): {len(d['claimed_index'])} spots, "
                f"a={d['result']['cell'][0]:.4g} Å")

    def _leftover_df(self):
        """``candidate_df`` rows no domain has claimed yet -- ``None`` before
        diamond filtering has even produced a candidate pool."""
        if self._candidate_df is None:
            return None
        if self._claimed_index is None:
            return self._candidate_df
        remaining = self._candidate_df.index.difference(self._claimed_index)
        return self._candidate_df.loc[remaining]

    def _update_leftover_status(self):
        df = self._leftover_df()
        n = 0 if df is None else len(df)
        self._leftover_status.setText(f"{n} spot(s) not yet assigned to a domain")
        self._leftover_btn.setEnabled(n > 0 and len(self._domains) > 0)
        self._ks5_fill_btn.setEnabled(len(self._domains) > 0)

    # ── stage 5: index remaining spots ──────────────────────────────

    def _on_leftover_method_changed(self, index: int):
        self._ks5["widget"].setVisible(index == 1)

    def _fill_ks5_from_latest_domain(self):
        if not self._domains:
            return
        cell = self._domains[-1]["result"]["cell"]
        for key, val in zip(("a", "b", "c", "alpha", "beta", "gamma"), cell):
            self._ks5[key].setValue(val)

    def _run_index_remaining(self):
        if self._worker is not None and self._worker.isRunning():
            return
        leftover = self._leftover_df()
        if leftover is None or len(leftover) == 0:
            QtWidgets.QMessageBox.warning(
                self, "No remaining spots", "No unassigned spots left to index."); return
        self._leftover_pending_df = leftover
        self._leftover_btn.setEnabled(False)
        self._prog.setVisible(True)

        if self._leftover_method.currentIndex() == 0:
            self._log.append("─" * 40 + "\nIndexing remaining spots (free ab-initio)…")
            cfg = {
                "candidate_df": leftover, "sigma_g": self._sigma_g.value(),
                "min_reflections": self._min_refl.value(), "pooled_dir": self._pooled_dir,
                "tol": self._tol_override.value(),
            }
            self._worker = SolveCellWorker("ab_initio", cfg, parent=self)
            self._worker.log_line.connect(self._log.append)
            self._worker.finished.connect(self._on_leftover_ab_initio_done)
        else:
            known_cell = self._known_structure_cell(self._ks5)
            self._log.append(
                "─" * 40 + f"\nIndexing remaining spots (known structure, cell="
                f"{tuple(round(v, 4) for v in known_cell)})…")
            self._leftover_g_2pi = leftover[["qsample_x", "qsample_y", "qsample_z"]].to_numpy()
            cfg = {
                "g_2pi": self._leftover_g_2pi, "known_cell": known_cell,
                "space_group": self._ks5["sg"].value(),
                "tol": self._ks5["tol"].value(), "n_search": self._ks5["n_search"].value(),
                "min_accept": self._ks5["min_accept"].value(),
                "sigma_g": self._sigma_g_used or solve_pipeline.SIGMA_G_DEFAULT,
                "pooled_dir": self._pooled_dir,
            }
            self._worker = SolveCellWorker("index_known_cell", cfg, parent=self)
            self._worker.log_line.connect(self._log.append)
            self._worker.finished.connect(self._on_leftover_known_cell_done)
        self._worker.failed.connect(
            lambda msg: self._on_stage_fail("Index remaining spots", msg, self._leftover_btn))
        self._worker.start()

    def _on_leftover_ab_initio_done(self, result: dict):
        """Free method, step 1/2: a plain ab-initio pass on the leftover pool.
        On success, chains straight into the refine stage (step 2/2) so one
        click of "Index Remaining Spots" produces one domain, same as round
        1's two-button sequence collapsed into one action."""
        res = result["ab_initio_result"]
        if not res["success"]:
            self._prog.setVisible(False)
            self._leftover_btn.setEnabled(True)
            notes = "; ".join(res["notes"]) or "no notes"
            self._leftover_run_status.setText(f"No new domain found: {notes}")
            self._log.append(f"Index remaining spots: ab-initio did not succeed ({notes}).")
            return
        self._pending_leftover_ab = (result["ab_initio_raw"], result["g_2pi"], self._leftover_pending_df)
        cfg = {
            "ab_initio_raw": result["ab_initio_raw"], "g_2pi": result["g_2pi"],
            "sigma_g": result["sigma_g"], "pooled_dir": self._pooled_dir,
        }
        self._worker = SolveCellWorker("refine", cfg, parent=self)
        self._worker.log_line.connect(self._log.append)
        self._worker.finished.connect(self._on_leftover_refine_done)
        self._worker.failed.connect(
            lambda msg: self._on_stage_fail("Index remaining spots", msg, self._leftover_btn))
        self._worker.start()

    def _on_leftover_refine_done(self, result: dict):
        self._leftover_btn.setEnabled(True); self._prog.setVisible(False)
        r = result["refine_result"]
        ab_raw, g_2pi, leftover_df = self._pending_leftover_ab
        mask = ab_raw.indexed_mask
        claimed_index = leftover_df.index[mask]
        xyz = (g_2pi[mask, 0], g_2pi[mask, 1], g_2pi[mask, 2])
        panel_ids = (leftover_df.loc[claimed_index, "panel_id"].to_numpy()
                     if "panel_id" in leftover_df.columns else None)
        self._leftover_run_status.setText(
            f"New domain: {int(mask.sum())} spots, a={r['cell'][0]:.4g} Å, rms_drlv={r['rms_drlv']:.4g}")
        self._log.append(f"Index remaining spots: new domain, cell={tuple(round(v, 4) for v in r['cell'])}")
        self._add_domain("free (ab-initio)", r, claimed_index, xyz, panel_ids)

    def _on_leftover_known_cell_done(self, result: dict):
        self._leftover_btn.setEnabled(True); self._prog.setVisible(False)
        if not result.get("success"):
            notes = "; ".join(result.get("notes", [])) or "no notes"
            diag = result.get("diagnostics", {})
            self._leftover_run_status.setText(f"No new domain found: {notes}")
            self._log.append(f"Index remaining spots (known structure): negative result -- {notes}")
            if diag:
                self._log.append(
                    f"  best_n={diag.get('best_n')} null_mean={diag.get('null_mean', 0):.1f} "
                    f"z={diag.get('z_score', float('nan')):.2f}")
            return
        leftover_df = self._leftover_pending_df
        mask = result["indexed_mask"]
        claimed_index = leftover_df.index[mask]
        g_2pi = self._leftover_g_2pi
        xyz = (g_2pi[mask, 0], g_2pi[mask, 1], g_2pi[mask, 2])
        panel_ids = (leftover_df.loc[claimed_index, "panel_id"].to_numpy()
                     if "panel_id" in leftover_df.columns else None)
        self._leftover_run_status.setText(
            f"New domain: {int(mask.sum())} spots, a={result['cell'][0]:.4g} Å, "
            f"rms_drlv={result['rms_drlv']:.4g}")
        self._log.append(
            f"Index remaining spots: new domain (known structure), "
            f"cell={tuple(round(v, 4) for v in result['cell'])}")
        for note in result.get("notes", []):
            self._log.append(f"  note: {note}")
        self._add_domain("known structure", result, claimed_index, xyz, panel_ids)
