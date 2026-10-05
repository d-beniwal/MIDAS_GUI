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

import matplotlib
matplotlib.use("Qt5Agg")
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas  # noqa: E402
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavToolbar  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
# matplotlib >=3.4 auto-registers the '3d' projection on import; no explicit
# mpl_toolkits.mplot3d import needed (verified against the pinned 3.8.4).

from midas_gui.helpers import (
    _fspin, _twocol, _NoScrollSpinBox, widgets_to_dict, apply_dict_to_widgets,
)
from midas_gui.widgets import DataLoaderPanel, ImageViewer, LogPanel
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
        self._calib_path: Optional[str] = None
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
        }
        self._calib_path = path

        tx = float(g.get("tx") or 0.0)
        note = (f"Loaded {Path(path).name}: λ={g['wavelength_A']:.5f} Å, "
                f"px={g['pxY']:.2f} µm, BC=({g['BC_y']:.2f}, {g['BC_z']:.2f}), "
                f"Lsd={g['Lsd']:.1f} µm, ty={g.get('ty') or 0.0:.3f}°, "
                f"tz={g.get('tz') or 0.0:.3f}°"
                + (f", {nry}×{nrz} px" if nry and nrz else "") + ".")
        if tx != 0.0:
            note += f" Note: file's tx={tx:.3f}° is not used by the Phase 1 pipeline (fixed at 0)."
        self._calib_note.setText(note)
        return True

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

    # ── GUI state (project save/restore) ───────────────────────────

    def _state_widgets(self) -> dict:
        return {
            "panel_id": self._panel_id,
            "ome_first_idx": self._ome_first_idx, "ome_first_deg": self._ome_first_deg,
            "ome_last_idx": self._ome_last_idx, "ome_step": self._ome_step,
        }

    def get_state(self) -> dict:
        state = {"fields": widgets_to_dict(self._state_widgets()), "loader": self.loader.get_state()}
        if self._calib_path:
            state["calib_path"] = self._calib_path
        return state

    def set_state(self, state: dict):
        if not state:
            return
        apply_dict_to_widgets(self._state_widgets(), state.get("fields", {}))
        self.loader.set_state(state.get("loader") or {})
        calib_path = state.get("calib_path")
        if calib_path and Path(calib_path).exists():
            self._load_calib_from_path(calib_path)


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
        self._ingest_preview: Optional[dict] = None
        self._det_view_framed_for = None
        self._det_view_shape = None
        self._build_ui()
        self._refresh_detector_preview()

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
            "unparseable falls back to the default search.")
        iv.addRow("sector candidates:", self._sector_candidates_ed)
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

        # ── RIGHT: tabbed views (3-D reciprocal-space map, Detector) + log ──
        right = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self._view_tabs = QtWidgets.QTabWidget()
        self._build_recip_map_tab()
        self._build_detector_tab()
        right.addWidget(self._view_tabs)

        self._log = LogPanel()
        self._log.setMaximumHeight(16_777_215)
        right.addWidget(self._log)
        right.setStretchFactor(0, 2); right.setStretchFactor(1, 1)
        right.setMinimumWidth(320)
        split.addWidget(right)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 0); split.setStretchFactor(2, 1)
        split.setSizes([360, 400, 900])

        # Connected last, once self._det_stage/_det_view etc. already exist --
        # setCurrentIndex() below fires this synchronously, and the first
        # panel tab is added (via _add_panel(), which calls setCurrentIndex)
        # earlier in this same method, before the Detector tab is built.
        self._panel_tabs.currentChanged.connect(self._refresh_detector_preview)

    def _build_recip_map_tab(self):
        """3-D qx/qy/qz reciprocal-space scatter. pyqtgraph has no 3-D
        equivalent without the ``PyOpenGL`` dependency (not installed, and a
        new native dependency this project has reason to be cautious about --
        see the unresolved Windows midas_calibrate_v2 import issue in
        STATE.md); matplotlib is already pinned and already used this way
        twice (``peak_fit_panel.py``, ``tab_zarrviewer.py``) -- this is the
        third embedded matplotlib canvas, same pattern."""
        self._recip_fig = Figure(figsize=(5, 4))
        self._recip_canvas = FigureCanvas(self._recip_fig)
        self._recip_ax = self._recip_fig.add_subplot(111, projection="3d")
        self._reset_recip_axes()
        toolbar = NavToolbar(self._recip_canvas, self)

        tab = QtWidgets.QWidget()
        tv = QtWidgets.QVBoxLayout(tab); tv.setContentsMargins(0, 0, 0, 0); tv.setSpacing(0)
        tv.addWidget(toolbar)
        tv.addWidget(self._recip_canvas, stretch=1)
        self._view_tabs.addTab(tab, "Reciprocal space map")

    def _reset_recip_axes(self):
        ax = self._recip_ax
        ax.clear()
        ax.set_xlabel("qx (Å⁻¹)"); ax.set_ylabel("qy (Å⁻¹)"); ax.set_zlabel("qz (Å⁻¹)")
        # Origin marker -- always drawn (even with no spots yet) so the q=0
        # reference point is never ambiguous once real data is scattered on
        # top of it.
        ax.scatter([0], [0], [0], c="red", marker="+", s=160, linewidths=2, depthshade=False)

    def _update_recip_plot(self, all_xyz=None, diamond_xyz=None, indexed_xyz=None):
        """Each ``*_xyz`` is an optional ``(x, y, z)`` tuple of 1-D arrays.
        Full replot per call -- cheap at the spot counts this tab deals with,
        same redraw-on-update pattern as the other two embedded-matplotlib
        views in this codebase."""
        self._reset_recip_axes()
        for xyz, color, size in (
            (all_xyz, "#969696", 8), (diamond_xyz, "#ff3030", 14), (indexed_xyz, "#4da3ff", 14),
        ):
            if xyz is not None and len(xyz[0]):
                self._recip_ax.scatter(xyz[0], xyz[1], xyz[2], c=color, s=size, depthshade=True)
        self._recip_canvas.draw_idle()

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
            "Raw", "Corrected", "Calculated background (after Ingest)",
            "Calculated background -- max over rotation (after Ingest)",
            "Mask (after Ingest)",
        ])
        self._det_stage.setToolTip(
            "Raw/Corrected update live from this panel's loaded data and "
            "dark/bright/background fields, no Ingest run needed. The "
            "single-frame Calculated background view is usually near-empty "
            "-- a real reflection only satisfies the diffraction condition "
            "for a handful of frames out of hundreds -- so prefer the max-"
            "over-rotation view to actually judge whether the background "
            "model worked. Both (and Mask) show what the last Ingest run "
            "captured; the single-frame one uses the frame index from the "
            "scrub bar below at the moment Run Ingest was clicked.")
        self._det_stage.currentIndexChanged.connect(self._refresh_detector_preview)
        toolbar.addWidget(self._det_stage)
        toolbar.addStretch(1)
        dv.addLayout(toolbar)

        self._det_view = ImageViewer(title="")
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
        else:
            self._det_slider.setEnabled(False)
            preview = self._ingest_preview
            if preview is None:
                self._det_frame_lbl.setText("Run Ingest to populate")
                self._set_detector_image(None, stage)
                return
            if stage == 2:
                arr = preview["background_subtracted"]
                self._det_frame_lbl.setText(
                    f"frame {preview['frame_index']} of the kept stack, from the last Ingest run "
                    "-- a single frame is usually near-empty, see the max-projection view")
            elif stage == 3:
                arr = preview["background_subtracted_max_projection"]
                self._det_frame_lbl.setText("max over every kept/live frame, from the last Ingest run")
            else:
                arr = preview["mask"].astype(np.float32)
                self._det_frame_lbl.setText("detector mask, from the last Ingest run")
            self._set_detector_image(arr, stage)

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

    def _add_panel(self, panel_id: Optional[int] = None) -> PanelCard:
        """``panel_id`` is normally auto-assigned (the "+" button); project
        restore (:meth:`set_state`) passes the saved id explicitly so a
        reopened project's panel numbering matches what was saved."""
        if panel_id is None:
            panel_id = self._next_panel_id
        self._next_panel_id = max(self._next_panel_id, panel_id + 1)
        card = PanelCard(panel_id)
        # Live-refresh the Detector tab (Raw/Corrected only) as this panel's
        # data/frame-index or dark/bright/background/mask fields change --
        # same signals tab_batch.py's own Detector view refreshes from.
        card.loader.dataChanged.connect(self._refresh_detector_preview)
        card.loader.fieldsChanged.connect(self._refresh_detector_preview)
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
        self._refresh_detector_preview()

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

    # ── GUI state (project save/restore) ───────────────────────────

    def _stage_state_widgets(self) -> dict:
        return {
            "proj_dir": self._proj_ed,
            "low_count": self._low_count, "mask_grow": self._mask_grow,
            "blob_thresh": self._blob_thresh, "blob_minvol": self._blob_minvol,
            "split_ratio": self._split_ratio, "gap_bridge": self._gap_bridge,
            "sector_candidates": self._sector_candidates_ed,
            "diamond_a": self._diamond_a, "contam_tol": self._contam_tol,
            "sigma_g": self._sigma_g, "min_refl": self._min_refl, "tol_override": self._tol_override,
        }

    def get_state(self) -> dict:
        """Panel configuration + stage parameters only. Like every other
        tab's project save (see ``MainWindow._apply_workspace_state``'s own
        docstring), Ingest/Diamond filter/Ab-initio/Refine results are not
        recomputed on restore -- their inputs are restored so a single click
        of each stage's own Run button reproduces them."""
        active_id = next((pid for pid, c in self._panels.items() if c is self._active_panel()), None)
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
                idx = self._panel_tabs.indexOf(card)
                if idx >= 0:
                    self._panel_tabs.removeTab(idx)
                card.deleteLater()
            self._panels.clear()
            for pid_key, pstate in sorted(panels_state.items(), key=lambda kv: int(kv[0])):
                pid = int(pid_key)
                card = self._add_panel(panel_id=pid)
                card.set_state(pstate)
        if "next_panel_id" in state:
            self._next_panel_id = max(self._next_panel_id, int(state["next_panel_id"]))
        active = state.get("active_panel")
        if active is not None and int(active) in self._panels:
            idx = self._panel_tabs.indexOf(self._panels[int(active)])
            if idx >= 0:
                self._panel_tabs.setCurrentIndex(idx)
        self._refresh_detector_preview()

    # ── stage 1: ingest ─────────────────────────────────────────────

    def _sector_candidates_cfg(self) -> tuple:
        text = self._sector_candidates_ed.text().strip()
        try:
            vals = tuple(int(x.strip()) for x in text.split(",") if x.strip())
        except ValueError:
            vals = ()
        return vals if vals else solve_pipeline.SECTOR_CANDIDATES_DEFAULT

    def _run_ingest(self):
        if self._worker is not None and self._worker.isRunning():
            return
        panel = self._active_panel()
        if not panel.has_calibration():
            QtWidgets.QMessageBox.warning(
                self, "No calibration loaded",
                "Load a calibration file for this panel first."); return
        # Cheap, metadata-only check -- the real read (full_stack(), ~10 GB for
        # an HDF5-backed stack at full detector resolution) happens inside the
        # worker thread below, not here on the GUI thread, so the window
        # doesn't freeze with no progress shown while it runs.
        if panel.loader.data_source_kind() == "none":
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
            "frames_loader": panel.loader.full_stack, "geometry": panel.geometry_cfg(),
            "corrections": panel.corrections_cfg(),
            "mask": {"low_count_threshold": self._low_count.value(), "grow": self._mask_grow.value(),
                     "user_mask": panel.loader.composite_mask()},
            "blobs": {"threshold": self._blob_thresh.value(), "min_vol": self._blob_minvol.value(),
                      "split_ratio": self._split_ratio.value(), "gap_bridge": self._gap_bridge.value(),
                      "sector_candidates": self._sector_candidates_cfg()},
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
        if n:
            df = self._spots_df
            self._update_recip_plot(all_xyz=(df["qsample_x"].to_numpy(), df["qsample_y"].to_numpy(),
                                             df["qsample_z"].to_numpy()))
        else:
            self._update_recip_plot()
        self._ingest_preview = result.get("preview")
        self._refresh_detector_preview()

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
        self._update_recip_plot(
            diamond_xyz=(diamond_rows["qsample_x"].to_numpy(), diamond_rows["qsample_y"].to_numpy(),
                        diamond_rows["qsample_z"].to_numpy()),
            indexed_xyz=(candidate_rows["qsample_x"].to_numpy(), candidate_rows["qsample_y"].to_numpy(),
                        candidate_rows["qsample_z"].to_numpy()))

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
        self._update_recip_plot(
            all_xyz=(g[~mask, 0], g[~mask, 1], g[~mask, 2]),
            indexed_xyz=(g[mask, 0], g[mask, 1], g[mask, 2]))

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
