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
and frame<->omega mapping. Only one panel is needed today; the stage buttons
on the right always operate on whichever panel tab is currently active.
Multi-panel pooling across panels is Phase 2 (handoff §10) and not
implemented here -- this just lets each panel's own inputs be configured
independently, ready for that later step.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
from PyQt5 import QtCore, QtWidgets
import pyqtgraph as pg

from midas_gui.helpers import _fspin, _twocol, _NoScrollSpinBox
from midas_gui.widgets import DataLoaderPanel, LogPanel
from midas_gui.workers import SolveCellWorker
from midas_gui.dialogs import show_error
from midas_gui.solve_cell import pipeline as solve_pipeline
from midas_gui import style as S


class PanelCard(QtWidgets.QWidget):
    """One detector panel's calibration + data + frame/omega mapping.

    ``geometry_cfg()`` returns the dict ``solve_pipeline._stage_ingest``
    expects under its ``geometry`` key; ``corrections_cfg()``/``mask_cfg()``
    pull straight from the embedded ``DataLoaderPanel``.
    """

    def __init__(self, panel_id: int, parent=None):
        super().__init__(parent)
        self.loader = DataLoaderPanel(mode="stack")
        self.loader.setMinimumWidth(200)
        self._geometry: Optional[dict] = None
        self._build_ui(panel_id)

    def _build_ui(self, panel_id: int):
        lv = QtWidgets.QVBoxLayout(self)
        lv.setContentsMargins(0, 4, 0, 0); lv.setSpacing(6)

        grp_calib = QtWidgets.QGroupBox("Calibration")
        cv = QtWidgets.QVBoxLayout(grp_calib)
        hdr = QtWidgets.QHBoxLayout()
        self._panel_id = _NoScrollSpinBox(); self._panel_id.setRange(1, 99); self._panel_id.setValue(panel_id)
        hdr.addWidget(QtWidgets.QLabel("panel id:")); hdr.addWidget(self._panel_id)
        hdr.addStretch(1)
        self._load_calib_btn = QtWidgets.QPushButton("Load calibration file…")
        self._load_calib_btn.setToolTip(
            "Load Lsd/BC/tilts/wavelength/pixel size/detector size from a "
            "calibration file written by the Calibrate tab (.json/.txt/.poni). "
            "Required before Ingest can run for this panel -- Phase 1 takes "
            "geometry only from a loaded calibration file, never typed in.")
        self._load_calib_btn.clicked.connect(self._load_calib_file)
        hdr.addWidget(self._load_calib_btn)
        cv.addLayout(hdr)
        self._calib_note = QtWidgets.QLabel(
            "No calibration file loaded — required before Ingest can run.")
        self._calib_note.setWordWrap(True)
        self._calib_note.setStyleSheet(f"color:{S.MUTED};font-size:11px")
        cv.addWidget(self._calib_note)
        lv.addWidget(grp_calib)

        lv.addWidget(self.loader)

        grp_ome = QtWidgets.QGroupBox("Frame ↔ omega mapping")
        of = QtWidgets.QFormLayout(grp_ome); of.setSpacing(4)
        self._ome_first_idx = _NoScrollSpinBox(); self._ome_first_idx.setRange(0, 1_000_000)
        self._ome_first_deg = _fspin(-7200.0, 7200.0, 4, 0.0, "°")
        self._ome_last_idx = _NoScrollSpinBox(); self._ome_last_idx.setRange(0, 1_000_000)
        self._ome_last_idx.setValue(1_000_000)
        self._ome_last_idx.setToolTip(
            "Last loaded-frame index whose omega is defined. Clamped to the "
            "last frame actually loaded when Ingest runs, so the default "
            "(maximum) means 'through the end of the stack'.")
        self._ome_step = _fspin(-10.0, 10.0, 5, 0.0, "°")
        of.addRow(_twocol("first frame idx:", self._ome_first_idx, "its omega:", self._ome_first_deg))
        of.addRow(_twocol("last frame idx:", self._ome_last_idx, "omega step:", self._ome_step))
        note = QtWidgets.QLabel(
            "omega(frame) = <its omega> + (frame − <first idx>) × <step>. Loaded "
            "frames outside [first, last] are excluded from ingest.")
        note.setWordWrap(True); note.setStyleSheet(f"color:{S.MUTED};font-size:11px")
        of.addRow(note)
        lv.addWidget(grp_ome)
        lv.addStretch(1)

    def _load_calib_file(self):
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

        nry = int(g["NrPixelsY"]) if g.get("NrPixelsY") else None
        nrz = int(g["NrPixelsZ"]) if g.get("NrPixelsZ") else None
        self._geometry = {
            "Lsd": float(g["Lsd"]), "BC_y": float(g["BC_y"]), "BC_z": float(g["BC_z"]),
            "ty": float(g.get("ty") or 0.0), "tz": float(g.get("tz") or 0.0),
            "wavelength_A": float(g["wavelength_A"]), "px_um": float(g["pxY"]),
            "nrpixels_y": nry, "nrpixels_z": nrz,
        }

        tx = float(g.get("tx") or 0.0)
        note = (f"Loaded {Path(path).name}: λ={g['wavelength_A']:.5f} Å, "
                f"px={g['pxY']:.2f} µm, BC=({g['BC_y']:.2f}, {g['BC_z']:.2f}), "
                f"Lsd={g['Lsd']:.1f} µm, ty={g.get('ty') or 0.0:.3f}°, "
                f"tz={g.get('tz') or 0.0:.3f}°"
                + (f", {nry}×{nrz} px" if nry and nrz else "") + ".")
        if tx != 0.0:
            note += f" Note: file's tx={tx:.3f}° is not used by the Phase 1 pipeline (fixed at 0)."
        self._calib_note.setText(note)

    def panel_id(self) -> int:
        return self._panel_id.value()

    def has_calibration(self) -> bool:
        return self._geometry is not None

    def geometry_cfg(self) -> dict:
        if self._geometry is None:
            raise RuntimeError("No calibration file loaded for this panel.")
        cfg = dict(self._geometry)
        cfg.update({
            "omega_ref_frame_idx": self._ome_first_idx.value(),
            "omega_ref_deg": self._ome_first_deg.value(),
            "omega_last_frame_idx": self._ome_last_idx.value(),
            "omega_step_deg": self._ome_step.value(),
        })
        return cfg

    def corrections_cfg(self) -> dict:
        return {
            "dark": self.loader.dark(), "bright": self.loader.bright(),
            "bright_mode": self.loader.bright_mode(), "background": self.loader.background(),
        }


class SolveCellTab(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: Optional[SolveCellWorker] = None
        self._spots_df = None
        self._candidate_df = None
        self._ab_initio_raw = None
        self._g_2pi: Optional[np.ndarray] = None
        self._sigma_g_used: Optional[float] = None
        self._panel_dir: Optional[Path] = None
        self._pooled_dir: Optional[Path] = None
        self._panels: dict[int, PanelCard] = {}
        self._next_panel_id = 1
        self._build_ui()

    # ── UI ──────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6); root.setSpacing(0)
        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        split.setChildrenCollapsible(False); split.setHandleWidth(6)
        root.addWidget(split)

        # ── LEFT: project folder + per-panel tabs ──
        left_scroll = QtWidgets.QScrollArea()
        left_scroll.setWidgetResizable(True); left_scroll.setMinimumWidth(360)
        left = QtWidgets.QWidget(); lv = QtWidgets.QVBoxLayout(left); lv.setSpacing(6)
        left_scroll.setWidget(left)

        note = QtWidgets.QLabel(
            "<b>Solve Cell</b> (work in progress) — Phase 1: each panel tab "
            "below supplies its own calibration (loaded from a file — "
            "required before Ingest can run), Data/Dark/Bright/Background/"
            "Mask, and frame↔omega mapping. The stages on the right always "
            "run against the currently active panel tab — multi-panel "
            "pooling is future work. See documentation/solve_cell_handoff.md.")
        note.setWordWrap(True); note.setStyleSheet(f"color:{S.MUTED};font-size:11px")
        lv.addWidget(note)

        grp_proj = QtWidgets.QGroupBox("Project folder")
        pf = QtWidgets.QHBoxLayout(grp_proj)
        self._proj_ed = QtWidgets.QLineEdit()
        self._proj_ed.setPlaceholderText("Where panel_NN/ and pooled/ outputs are written (optional)")
        pf.addWidget(self._proj_ed, stretch=1)
        proj_btn = QtWidgets.QPushButton("Browse…")
        proj_btn.clicked.connect(self._browse_project_dir)
        pf.addWidget(proj_btn)
        lv.addWidget(grp_proj)

        self._panel_tabs = QtWidgets.QTabWidget()
        self._panel_tabs.setTabsClosable(True)
        self._panel_tabs.tabCloseRequested.connect(self._close_panel_tab)
        add_btn = QtWidgets.QToolButton()
        add_btn.setText("+")
        add_btn.setToolTip("Add another detector panel")
        add_btn.clicked.connect(lambda: self._add_panel())
        self._panel_tabs.setCornerWidget(add_btn, QtCore.Qt.TopRightCorner)
        self._add_panel()
        lv.addWidget(self._panel_tabs, stretch=1)
        split.addWidget(left_scroll)

        # ── MIDDLE: stage parameters + run buttons ──
        mid_scroll = QtWidgets.QScrollArea()
        mid_scroll.setWidgetResizable(True); mid_scroll.setMinimumWidth(340)
        mid = QtWidgets.QWidget(); mv = QtWidgets.QVBoxLayout(mid); mv.setSpacing(6)
        mid_scroll.setWidget(mid)

        grp_ing = QtWidgets.QGroupBox("1. Ingest")
        iv = QtWidgets.QFormLayout(grp_ing); iv.setSpacing(4)
        self._low_count = _fspin(0.0, 1e6, 2, solve_pipeline.LOW_COUNT_THRESHOLD_DEFAULT)
        self._low_count.setToolTip(
            "midas_defect's own default (20) can mask 80-99%+ of a "
            "single-crystal-in-DAC chip with no diffuse baseline -- sweep "
            "this on your own data before trusting the default of 0.")
        self._mask_grow = _NoScrollSpinBox(); self._mask_grow.setRange(0, 50)
        self._mask_grow.setValue(solve_pipeline.MASK_GROW_DEFAULT)
        iv.addRow(_twocol("low_count_threshold:", self._low_count, "mask grow:", self._mask_grow))
        self._blob_thresh = _fspin(0.0, 1e7, 2, solve_pipeline.BLOB_THRESHOLD_DEFAULT)
        self._blob_minvol = _NoScrollSpinBox(); self._blob_minvol.setRange(1, 100000)
        self._blob_minvol.setValue(solve_pipeline.BLOB_MIN_VOL_DEFAULT)
        iv.addRow(_twocol("blob threshold:", self._blob_thresh, "blob min_vol:", self._blob_minvol))
        self._split_ratio = _fspin(1.0, 100.0, 2, solve_pipeline.SPLIT_RATIO_DEFAULT)
        self._gap_bridge = _NoScrollSpinBox(); self._gap_bridge.setRange(0, 1000)
        self._gap_bridge.setValue(solve_pipeline.GAP_BRIDGE_DEFAULT)
        iv.addRow(_twocol("split_ratio:", self._split_ratio, "gap_bridge:", self._gap_bridge))
        self._ingest_btn = S.primary_btn("Run Ingest")
        self._ingest_btn.clicked.connect(self._run_ingest)
        iv.addRow(self._ingest_btn)
        self._ingest_status = QtWidgets.QLabel("—")
        self._ingest_status.setStyleSheet(f"color:{S.MUTED};font-size:11px")
        self._ingest_status.setWordWrap(True)
        iv.addRow(self._ingest_status)
        mv.addWidget(grp_ing)

        grp_dia = QtWidgets.QGroupBox("2. Diamond filter")
        dv = QtWidgets.QFormLayout(grp_dia); dv.setSpacing(4)
        self._diamond_a = _fspin(1.0, 10.0, 4, solve_pipeline.DIAMOND_A_ANGSTROM_DEFAULT, "Å")
        self._diamond_a.setToolTip("Diamond anvil cubic cell edge — advanced override.")
        self._contam_tol = _fspin(0.001, 5.0, 4, solve_pipeline.CONTAM_TOL_DEG_DEFAULT, "°")
        dv.addRow(_twocol("diamond a:", self._diamond_a, "tol:", self._contam_tol))
        self._diamond_btn = S.primary_btn("Run Diamond Filter")
        self._diamond_btn.setEnabled(False)
        self._diamond_btn.clicked.connect(self._run_diamond_filter)
        dv.addRow(self._diamond_btn)
        self._diamond_status = QtWidgets.QLabel("—")
        self._diamond_status.setStyleSheet(f"color:{S.MUTED};font-size:11px")
        self._diamond_status.setWordWrap(True)
        dv.addRow(self._diamond_status)
        mv.addWidget(grp_dia)

        grp_ab = QtWidgets.QGroupBox("3. Ab-initio index")
        av = QtWidgets.QFormLayout(grp_ab); av.setSpacing(4)
        self._sigma_g = _fspin(1e-5, 1.0, 5, solve_pipeline.SIGMA_G_DEFAULT, "Å⁻¹")
        self._min_refl = _NoScrollSpinBox(); self._min_refl.setRange(4, 100000)
        self._min_refl.setValue(solve_pipeline.MIN_REFLECTIONS_DEFAULT)
        av.addRow(_twocol("sigma_g:", self._sigma_g, "min_reflections:", self._min_refl))
        self._tol_override = _fspin(0.0, 1.0, 3, 0.0)
        self._tol_override.setSpecialValueText("(library default)")
        av.addRow("tol override:", self._tol_override)
        self._ab_btn = S.primary_btn("Run Ab-initio Index")
        self._ab_btn.setEnabled(False)
        self._ab_btn.clicked.connect(self._run_ab_initio)
        av.addRow(self._ab_btn)
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
        rv.addWidget(self._refine_btn)
        self._param_grid = QtWidgets.QGridLayout()
        self._param_grid.setHorizontalSpacing(20); self._param_grid.setVerticalSpacing(6)
        _pg_host = QtWidgets.QWidget(); _pg_host.setLayout(self._param_grid)
        rv.addWidget(_pg_host)
        mv.addWidget(grp_ref)

        self._prog = QtWidgets.QProgressBar(); self._prog.setVisible(False); self._prog.setRange(0, 0)
        mv.addWidget(self._prog)
        mv.addStretch(1)
        split.addWidget(mid_scroll)

        # ── RIGHT: reciprocal-space scatter + log ──
        right = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self._scatter_plot = pg.PlotWidget(background="#111111")
        self._scatter_plot.setLabel("left", "qy (Å⁻¹)")
        self._scatter_plot.setLabel("bottom", "qx (Å⁻¹)")
        self._scatter_plot.showGrid(x=True, y=True, alpha=0.2)
        self._scatter_plot.setAspectLocked(True)
        self._scatter_all = pg.ScatterPlotItem([], [], symbol="o", size=4,
                                               brush=pg.mkBrush(150, 150, 150, 150), pen=None)
        self._scatter_diamond = pg.ScatterPlotItem([], [], symbol="o", size=5,
                                                    brush=pg.mkBrush("#ff3030"), pen=None)
        self._scatter_indexed = pg.ScatterPlotItem([], [], symbol="o", size=5,
                                                    brush=pg.mkBrush("#4da3ff"), pen=None)
        for item in (self._scatter_all, self._scatter_diamond, self._scatter_indexed):
            self._scatter_plot.addItem(item)
        right.addWidget(self._scatter_plot)

        self._log = LogPanel()
        self._log.setMaximumHeight(16_777_215)
        right.addWidget(self._log)
        right.setStretchFactor(0, 2); right.setStretchFactor(1, 1)
        right.setMinimumWidth(320)
        split.addWidget(right)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 0); split.setStretchFactor(2, 1)
        split.setSizes([360, 400, 900])

    # ── panel management ───────────────────────────────────────────

    def _add_panel(self) -> PanelCard:
        panel_id = self._next_panel_id
        self._next_panel_id += 1
        card = PanelCard(panel_id)
        self._panels[panel_id] = card
        idx = self._panel_tabs.addTab(card, f"Panel {panel_id}")
        self._panel_tabs.setCurrentIndex(idx)
        return card

    def _close_panel_tab(self, index: int):
        if self._panel_tabs.count() <= 1:
            QtWidgets.QMessageBox.information(
                self, "Solve Cell", "At least one panel is required.")
            return
        card = self._panel_tabs.widget(index)
        panel_id = next((pid for pid, c in self._panels.items() if c is card), None)
        self._panel_tabs.removeTab(index)
        if panel_id is not None:
            del self._panels[panel_id]
        card.deleteLater()

    def _active_panel(self) -> PanelCard:
        return self._panel_tabs.currentWidget()

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

    # ── stage 1: ingest ─────────────────────────────────────────────

    def _run_ingest(self):
        if self._worker is not None and self._worker.isRunning():
            return
        panel = self._active_panel()
        if not panel.has_calibration():
            QtWidgets.QMessageBox.warning(
                self, "No calibration loaded",
                "Load a calibration file for this panel first."); return
        try:
            frames = panel.loader.full_stack()
        except RuntimeError:
            QtWidgets.QMessageBox.warning(self, "No raw data", "Load raw frames first."); return
        if frames is None or len(frames) == 0:
            QtWidgets.QMessageBox.warning(self, "No raw data", "Load raw frames first."); return
        for sel in panel.loader.has_pending_fields():
            QtWidgets.QMessageBox.warning(
                self, "Field not computed",
                f"'{sel.title()}' is enabled but not computed. "
                "Click 'Compute field' in that box first."); return

        project_dir = self._project_dir()
        panel_dir = None
        if project_dir is not None:
            name = solve_pipeline.panel_dir_name(panel.panel_id())
            panel_dir = project_dir / name / "data"
            self._pooled_dir = project_dir / "pooled"
        self._panel_dir = panel_dir

        cfg = {
            "frames": frames, "geometry": panel.geometry_cfg(),
            "corrections": panel.corrections_cfg(),
            "mask": {"low_count_threshold": self._low_count.value(), "grow": self._mask_grow.value(),
                     "user_mask": panel.loader.composite_mask()},
            "blobs": {"threshold": self._blob_thresh.value(), "min_vol": self._blob_minvol.value(),
                      "split_ratio": self._split_ratio.value(), "gap_bridge": self._gap_bridge.value()},
            "panel_dir": panel_dir,
        }
        self._ingest_btn.setEnabled(False)
        self._prog.setVisible(True)
        self._log.append("─" * 40 + "\nRunning ingest…")
        self._worker = SolveCellWorker("ingest", cfg, parent=self)
        self._worker.log_line.connect(self._log.append)
        self._worker.finished.connect(self._on_ingest_done)
        self._worker.failed.connect(lambda msg: self._on_stage_fail("Ingest", msg, self._ingest_btn))
        self._worker.start()

    def _on_ingest_done(self, result: dict):
        self._ingest_btn.setEnabled(True); self._prog.setVisible(False)
        self._spots_df = result["spots_df"]
        n = len(self._spots_df)
        self._ingest_status.setText(f"{n} spots (n_frames≥2)")
        self._log.append(f"Ingest complete: {n} candidate spots.")
        self._diamond_btn.setEnabled(n > 0)
        self._scatter_diamond.setData([], []); self._scatter_indexed.setData([], [])
        if n:
            self._scatter_all.setData(self._spots_df["qsample_x"].to_numpy(),
                                      self._spots_df["qsample_y"].to_numpy())
        else:
            self._scatter_all.setData([], [])

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
        self._candidate_df = result["candidate_df"]
        summary = result["summary"]
        self._diamond_status.setText(
            f"{summary['n_diamond_flagged']}/{summary['n_spots']} flagged, "
            f"{summary['n_kept']} candidates remain")
        self._log.append(f"Diamond filter complete: {summary['n_diamond_flagged']} flagged, "
                         f"{summary['n_kept']} candidates kept.")
        self._ab_btn.setEnabled(len(self._candidate_df) > 0)

        flagged = result["flagged_df"]
        diamond_rows = flagged[flagged["is_diamond"]]
        candidate_rows = flagged[~flagged["is_diamond"]]
        self._scatter_all.setData([], [])
        self._scatter_diamond.setData(diamond_rows["qsample_x"].to_numpy(), diamond_rows["qsample_y"].to_numpy())
        self._scatter_indexed.setData(candidate_rows["qsample_x"].to_numpy(), candidate_rows["qsample_y"].to_numpy())

    # ── stage 3: ab-initio index ────────────────────────────────────

    def _run_ab_initio(self):
        if self._worker is not None and self._worker.isRunning():
            return
        if self._candidate_df is None or len(self._candidate_df) == 0:
            QtWidgets.QMessageBox.warning(self, "No candidates", "Run Diamond Filter first."); return
        cfg = {
            "candidate_df": self._candidate_df, "sigma_g": self._sigma_g.value(),
            "min_reflections": self._min_refl.value(), "pooled_dir": self._pooled_dir,
        }
        if self._tol_override.value() > 0:
            cfg["tol"] = self._tol_override.value()
        self._ab_btn.setEnabled(False)
        self._prog.setVisible(True)
        self._log.append("─" * 40 + "\nRunning ab-initio indexing…")
        self._worker = SolveCellWorker("ab_initio", cfg, parent=self)
        self._worker.log_line.connect(self._log.append)
        self._worker.finished.connect(self._on_ab_initio_done)
        self._worker.failed.connect(lambda msg: self._on_stage_fail("Ab-initio index", msg, self._ab_btn))
        self._worker.start()

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

        mask = self._ab_initio_raw.indexed_mask
        g = self._g_2pi
        self._scatter_all.setData(g[~mask, 0], g[~mask, 1])
        self._scatter_indexed.setData(g[mask, 0], g[mask, 1])

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
        self._populate_result_grid(r)

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
