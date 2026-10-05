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

from pathlib import Path
from typing import Optional

import numpy as np
import pyqtgraph as pg
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
from midas_gui import project
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
        self._ome_first_idx.setToolTip(
            "Loaded-frame index used as the omega reference point. Frames "
            "before this index have no defined omega and are dropped, not "
            "extrapolated.")
        self._ome_first_deg = _fspin(-7200.0, 7200.0, 4, 0.0, "°")
        self._ome_first_deg.setToolTip(
            "Omega (degrees) assigned to 'first frame idx'. Every other kept "
            "frame's omega is this value plus (frame − first idx) × omega step.")
        self._ome_last_idx = _NoScrollSpinBox(); self._ome_last_idx.setRange(0, 1_000_000)
        self._ome_last_idx.setValue(1_000_000)
        self._ome_last_idx.setToolTip(
            "Last loaded-frame index whose omega is defined. Clamped to the "
            "last frame actually loaded when Ingest runs, so the default "
            "(maximum) means 'through the end of the stack'.")
        self._ome_step = _fspin(-10.0, 10.0, 5, 0.0, "°")
        self._ome_step.setToolTip(
            "Degrees of rotation per frame, used to extend omega away from "
            "the reference frame in both directions.")
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
        self._next_panel_id = 1
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
        self._project_ctx = None
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
            "<b>Solve Cell</b> (work in progress) — each panel tab below "
            "supplies its own calibration (loaded from a file — required "
            "before Ingest can run), Data/Dark/Bright/Background/Mask, and "
            "frame↔omega mapping. With one panel configured, Ingest runs "
            "against it alone; with more than one panel ready (its own "
            "calibration + data loaded), Run Ingest pools them — each is "
            "ingested against its own geometry, then combined for Diamond "
            "Filter/Ab-initio/Refine. See documentation/solve_cell_handoff.md.")
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
        self._mask_grow.setToolTip(
            "Binary-dilate the finished mask by this many pixels. Pixels "
            "bordering a module gap have anomalous response and, after "
            "background subtraction, produce spurious negative structures -- "
            "growing removes them at negligible cost to real signal.")
        iv.addRow(_twocol("low_count_threshold:", self._low_count, "mask grow:", self._mask_grow))
        self._blob_thresh = _fspin(0.0, 1e7, 2, solve_pipeline.BLOB_THRESHOLD_DEFAULT)
        self._blob_thresh.setToolTip(
            "Intensity cut above which a background-subtracted voxel counts "
            "as part of a candidate blob. Also used inside the sector-"
            "candidate search's own scoring pass.")
        self._blob_minvol = _NoScrollSpinBox(); self._blob_minvol.setRange(1, 100000)
        self._blob_minvol.setValue(solve_pipeline.BLOB_MIN_VOL_DEFAULT)
        self._blob_minvol.setToolTip(
            "Minimum connected voxel count (26-connectivity across row/col/"
            "frame) for a blob to survive -- smaller connected components "
            "are dropped as noise.")
        iv.addRow(_twocol("blob threshold:", self._blob_thresh, "blob min_vol:", self._blob_minvol))
        self._split_ratio = _fspin(1.0, 100.0, 2, solve_pipeline.SPLIT_RATIO_DEFAULT)
        self._split_ratio.setToolTip(
            "Watershed sensitivity for splitting one connected blob into "
            "multiple sub-peaks: how far a local maximum must rise above its "
            "saddle, as a multiple of the saddle (scale-free, via log-"
            "intensity h-maxima). Values <=1 disable splitting (plain "
            "local-maximum seeding).")
        self._gap_bridge = _NoScrollSpinBox(); self._gap_bridge.setRange(0, 1000)
        self._gap_bridge.setValue(solve_pipeline.GAP_BRIDGE_DEFAULT)
        self._gap_bridge.setToolTip(
            "Size (pixels) of a binary-closing kernel used to reconnect a "
            "real feature that a detector module gap cut in two. Only "
            "bridges where signal is masked and continuous on both sides -- "
            "nothing is invented in blank regions. <=1 disables bridging.")
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
        self._domain_list = QtWidgets.QListWidget()
        self._domain_list.setToolTip(
            "Every solved domain so far -- round 1's refine result is "
            "'Domain 1'; each successful 'Index Remaining Spots' round below "
            "appends another. Select one to see its full cell/refine detail.")
        self._domain_list.setMaximumHeight(90)
        self._domain_list.currentRowChanged.connect(self._on_domain_selected)
        rv.addWidget(self._domain_list)
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
        self._leftover_method.addItems([
            "Free (ab-initio)", "Known structure (reuse latest domain's cell)"])
        self._leftover_method.setToolTip(
            "Free: re-run blind ab-initio indexing (step 3) + refine (step 4) "
            "on whatever is still unassigned -- for a genuinely different, "
            "unknown structure. Known structure: search for an orientation of "
            "the most recently solved domain's own cell among the leftover "
            "spots -- for a second grain/domain of the SAME material at a "
            "different orientation (handoff §5.4.2/§5.6). A match is only "
            "accepted if it clearly beats a null test against scrambled "
            "spots; a negative result just means no further domain was found.")
        self._leftover_method.currentIndexChanged.connect(self._on_leftover_method_changed)
        lv2.addRow("method:", self._leftover_method)
        self._kc_params = QtWidgets.QWidget()
        kcf = QtWidgets.QFormLayout(self._kc_params); kcf.setContentsMargins(0, 0, 0, 0); kcf.setSpacing(4)
        self._kc_n_search = _NoScrollSpinBox(); self._kc_n_search.setRange(1_000, 5_000_000)
        self._kc_n_search.setSingleStep(10_000)
        self._kc_n_search.setValue(solve_pipeline.RANDOM_SEARCH_N_DEFAULT)
        self._kc_tol = _fspin(0.01, 1.0, 3, solve_pipeline.RANDOM_SEARCH_TOL_DEFAULT)
        kcf.addRow(_twocol("search count:", self._kc_n_search, "tol:", self._kc_tol))
        self._kc_min_accept = _NoScrollSpinBox(); self._kc_min_accept.setRange(3, 100_000)
        self._kc_min_accept.setValue(solve_pipeline.RANDOM_SEARCH_MIN_ACCEPT_DEFAULT)
        kcf.addRow("min accept:", self._kc_min_accept)
        lv2.addRow(self._kc_params)
        self._on_leftover_method_changed(0)
        self._leftover_btn = S.primary_btn("Index Remaining Spots")
        self._leftover_btn.setEnabled(False)
        self._leftover_btn.clicked.connect(self._run_index_remaining)
        lv2.addRow(self._leftover_btn)
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

        mode_row = QtWidgets.QHBoxLayout()
        mode_row.setContentsMargins(6, 2, 6, 2)
        mode_row.addWidget(QtWidgets.QLabel("Color by:"))
        self._recip_mode = QtWidgets.QComboBox()
        self._recip_mode.addItems(["Panel", "Crystal"])
        self._recip_mode.setToolTip(
            "Panel: every spot colored by which detector panel it came from "
            "(useful to sanity-check a pooled multi-panel ingest). Crystal: "
            "diamond/gasket spots, each solved domain, and whatever is still "
            "unassigned each get their own color.")
        self._recip_mode.currentIndexChanged.connect(lambda _=None: self._refresh_recip_view())
        mode_row.addWidget(self._recip_mode)
        self._recip_show_unindexed = QtWidgets.QCheckBox("Show unindexed/spurious spots")
        self._recip_show_unindexed.setChecked(True)
        self._recip_show_unindexed.stateChanged.connect(lambda _=None: self._refresh_recip_view())
        mode_row.addWidget(self._recip_show_unindexed)
        mode_row.addStretch(1)
        tv.addLayout(mode_row)

        tv.addWidget(self._recip_canvas, stretch=1)
        self._recip_stats = QtWidgets.QLabel("—")
        self._recip_stats.setStyleSheet(f"color:{S.MUTED};font-size:11px")
        self._recip_stats.setWordWrap(True)
        self._recip_stats.setContentsMargins(6, 2, 6, 4)
        tv.addWidget(self._recip_stats)
        self._view_tabs.addTab(tab, "Reciprocal space map")

    def _reset_recip_axes(self):
        ax = self._recip_ax
        ax.clear()
        ax.set_xlabel("qx (Å⁻¹)"); ax.set_ylabel("qy (Å⁻¹)"); ax.set_zlabel("qz (Å⁻¹)")
        # Origin marker -- always drawn (even with no spots yet) so the q=0
        # reference point is never ambiguous once real data is scattered on
        # top of it.
        ax.scatter([0], [0], [0], c="red", marker="+", s=160, linewidths=2, depthshade=False)

    # Cycled per domain once more than one exists (_update_multidomain_plot) --
    # kept well clear of the legacy "#ff3030"/"#4da3ff" diamond/indexed colors.
    _DOMAIN_COLORS = ["#4da3ff", "#ffa94d", "#69db7c", "#da77f2",
                      "#ffd43b", "#63e6be", "#ff8787", "#91a7ff"]

    def _update_recip_plot(self, all_xyz=None, diamond_xyz=None, indexed_xyz=None, groups=None):
        """Each ``*_xyz`` is an optional ``(x, y, z)`` tuple of 1-D arrays.
        ``groups`` is an optional list of ``(xyz, color)`` pairs drawn after
        the three legacy slots -- used once more than one domain has been
        solved (:meth:`_update_multidomain_plot`), one color per domain.
        Full replot per call -- cheap at the spot counts this tab deals with,
        same redraw-on-update pattern as the other two embedded-matplotlib
        views in this codebase."""
        self._reset_recip_axes()
        for xyz, color, size in (
            (all_xyz, "#969696", 8), (diamond_xyz, "#ff3030", 14), (indexed_xyz, "#4da3ff", 14),
        ):
            if xyz is not None and len(xyz[0]):
                self._recip_ax.scatter(xyz[0], xyz[1], xyz[2], c=color, s=size, depthshade=True)
        for xyz, color in (groups or []):
            if xyz is not None and len(xyz[0]):
                self._recip_ax.scatter(xyz[0], xyz[1], xyz[2], c=color, s=14, depthshade=True)
        self._recip_canvas.draw_idle()

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

    def _refresh_recip_view(self):
        """Single source of truth for the 3-D plot: reads the current
        pipeline state plus the Color-by mode and the unindexed/spurious
        toggle, redraws via :meth:`_update_recip_plot`, and updates the
        stats label. Called after every stage completes and whenever either
        control changes."""
        show_unindexed = self._recip_show_unindexed.isChecked()
        diamond_df = None
        if self._flagged_df is not None:
            diamond_df = self._flagged_df[self._flagged_df["is_diamond"]]
        unindexed_df = self._unindexed_df() if show_unindexed else None
        n_unindexed_total = len(self._unindexed_df()) if self._unindexed_df() is not None else 0

        if self._recip_mode.currentIndex() == 1:  # Crystal
            groups = [(d["xyz"], self._DOMAIN_COLORS[i % len(self._DOMAIN_COLORS)])
                      for i, d in enumerate(self._domains)]
            diamond_xyz = self._xyz_cols(diamond_df) if diamond_df is not None and len(diamond_df) else None
            unindexed_xyz = self._xyz_cols(unindexed_df) if unindexed_df is not None and len(unindexed_df) else None
            self._update_recip_plot(all_xyz=unindexed_xyz, diamond_xyz=diamond_xyz, groups=groups)

            lines = [f"{d['label']} ({d['method']}): {len(d['claimed_index'])} spots, "
                     f"a={d['result']['cell'][0]:.4g} Å" for d in self._domains]
            if diamond_df is not None:
                lines.append(f"Diamond/gasket: {len(diamond_df)} spots")
            hidden = "" if show_unindexed else " (hidden)"
            lines.append(f"Unindexed: {n_unindexed_total} spots{hidden}")
            self._recip_stats.setText("\n".join(lines) if lines else "—")
            return

        # Panel mode.
        sources = [(*self._xyz_cols(diamond_df), self._panel_ids_col(diamond_df))] if diamond_df is not None else []
        for d in self._domains:
            sources.append((d["xyz"][0], d["xyz"][1], d["xyz"][2], d.get("panel_ids")))
        if unindexed_df is not None:
            sources.append((*self._xyz_cols(unindexed_df), self._panel_ids_col(unindexed_df)))
        buckets = self._bucket_by_panel(sources)

        groups = [(xyz, self._DOMAIN_COLORS[i % len(self._DOMAIN_COLORS)])
                  for i, (_, xyz) in enumerate(sorted(buckets.items(), key=lambda kv: (kv[0] is None, kv[0])))]
        self._update_recip_plot(groups=groups)

        total = sum(len(xyz[0]) for xyz in buckets.values())
        lines = [f"{self._panel_label(key)}: {len(xyz[0])} spots"
                 for key, xyz in sorted(buckets.items(), key=lambda kv: (kv[0] is None, kv[0]))]
        lines.append(f"Total: {total} spots")
        if not show_unindexed and n_unindexed_total:
            lines.append(f"({n_unindexed_total} unindexed hidden)")
        self._recip_stats.setText("\n".join(lines) if lines else "—")

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
        # Open circles (no fill) marking ingest-identified spot positions --
        # updated by _update_spot_overlay, never rebuilt (unlike
        # tab_calibrate.py's _draw_rings, there is only ever one overlay
        # item here, so setData() in place is simpler than clear/rebuild).
        self._spot_scatter = pg.ScatterPlotItem(
            pen=pg.mkPen("#ffd400", width=2), brush=None, size=16, symbol="o")
        self._det_view._iv.addItem(self._spot_scatter)
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
            self._spot_scatter.setData(x=sel["col"].to_numpy(), y=sel["row"].to_numpy())
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

        project_dir = self._project_dir()
        self._pooled_dir = project_dir / "pooled" if project_dir is not None else None

        if len(ready) <= 1:
            panel_dir = None
            if project_dir is not None:
                panel_dir = project_dir / solve_pipeline.panel_dir_name(active_panel.panel_id()) / "data"
            self._panel_dir = panel_dir
            cfg = self._panel_ingest_cfg(active_panel, panel_dir)
            self._ingest_btn.setEnabled(False)
            self._prog.setVisible(True)
            self._log.append("─" * 40 + "\nRunning ingest…")
            self._worker = SolveCellWorker("ingest", cfg, parent=self)
            self._worker.log_line.connect(self._log.append)
            self._worker.finished.connect(self._on_ingest_done)
            self._worker.failed.connect(lambda msg: self._on_stage_fail("Ingest", msg, self._ingest_btn))
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
        self._worker.finished.connect(self._on_ingest_pooled_done)
        self._worker.failed.connect(lambda msg: self._on_stage_fail("Ingest", msg, self._ingest_btn))
        self._worker.start()

    def _on_ingest_done(self, result: dict):
        self._ingest_btn.setEnabled(True); self._prog.setVisible(False)
        self._spots_df = result["spots_df"]
        n = len(self._spots_df)
        self._ingest_status.setText(f"{n} spots (n_frames≥2)")
        self._log.append(f"Ingest complete: {n} candidate spots.")
        self._diamond_btn.setEnabled(n > 0)
        self._refresh_recip_view()
        self._ingest_preview = result.get("preview")
        pid = self._active_panel_id()
        if pid is not None:
            self._ingest_preview_by_panel[pid] = self._ingest_preview
        self._refresh_detector_preview()
        self._log_ingest_to_project(result)

    def _on_ingest_pooled_done(self, result: dict):
        """Multi-panel counterpart of :meth:`_on_ingest_done` -- ``result``
        is ``solve_pipeline._stage_ingest_pooled``'s own return shape
        (``spots_df`` already tagged by ``panel_id``, plus ``per_panel``)."""
        self._ingest_btn.setEnabled(True); self._prog.setVisible(False)
        self._spots_df = result["spots_df"]
        n = len(self._spots_df)
        summary = result["summary"]
        per_panel = result["per_panel"]
        breakdown = ", ".join(f"panel {pid}: {r['summary']['n_spots']}" for pid, r in per_panel.items())
        self._ingest_status.setText(f"{n} spots across {summary['n_panels']} panels ({breakdown})")
        self._log.append(f"Ingest complete (pooled): {n} candidate spots across "
                         f"{summary['n_panels']} panels ({breakdown}).")
        self._diamond_btn.setEnabled(n > 0)
        self._refresh_recip_view()
        self._ingest_preview_by_panel = {pid: r.get("preview") for pid, r in per_panel.items()}
        self._refresh_detector_preview()
        self._log_pooled_ingest_to_project(result)

    def _panel_ingest_inputs(self, panel: "PanelCard") -> dict:
        """The ``inputs`` metadata dict logged alongside a panel's own ingest
        attempt -- shared by the single-panel and pooled provenance loggers
        below."""
        return {
            "panel_id": panel.panel_id(), "geometry": panel.geometry_cfg(),
            "corrections": {k: (v is not None) for k, v in panel.corrections_cfg().items()},
            "mask": {"low_count_threshold": self._low_count.value(), "grow": self._mask_grow.value()},
            "blobs": {"threshold": self._blob_thresh.value(), "min_vol": self._blob_minvol.value(),
                      "split_ratio": self._split_ratio.value(), "gap_bridge": self._gap_bridge.value(),
                      "sector_candidates": self._sector_candidates_cfg()},
        }

    def _log_panel_ingest_attempt(self, panel: "PanelCard", spots_df, summary: dict,
                                   preview: Optional[dict]):
        panel_key = solve_pipeline.panel_dir_name(panel.panel_id())
        try:
            ref = project.append_solve_cell_ingest_attempt(
                self._project_ctx.path, panel_key, inputs=self._panel_ingest_inputs(panel),
                summary=summary, spots_df=spots_df, mask=(preview or {}).get("mask"))
            self._log.append(f"Logged ingest to project: {ref}")
        except Exception:
            import traceback as _tb
            self._log.append("Could not log ingest to project file:\n" + _tb.format_exc())

    def _log_ingest_to_project(self, result: dict):
        """FAIR provenance: append this ingest run to the open project's
        ``.h5`` (``project.append_solve_cell_ingest_attempt``), same guarded,
        best-effort pattern as ``tab_batch.py``'s own ``_log_to_project``.
        Diamond filter/ab-initio/refine are not logged -- ingest only, per
        the request this follows. ``inputs`` is rebuilt fresh from the
        panel/widgets here rather than reusing the ``cfg`` dict handed to
        ``SolveCellWorker``: that dict is mutated in place by the worker
        thread (its ``frames_loader`` callable is popped and replaced with
        the fully materialized raw stack array), so logging it directly
        would risk dragging a multi-GB array into the metadata path."""
        if not self._project_ctx or not getattr(self._project_ctx, "path", None):
            return
        self._log_panel_ingest_attempt(
            self._active_panel(), self._spots_df, result["summary"], result.get("preview"))

    def _log_pooled_ingest_to_project(self, result: dict):
        """Pooled counterpart of :meth:`_log_ingest_to_project` -- a pooled
        run is just every ready panel's own ingest executed back to back, so
        its provenance is logged the same way: one attempt per panel, under
        that panel's own ``panel_key``, exactly as a single-panel run would
        have logged it."""
        if not self._project_ctx or not getattr(self._project_ctx, "path", None):
            return
        for panel_id, pres in result.get("per_panel", {}).items():
            panel = self._panels.get(panel_id)
            if panel is None:
                continue
            self._log_panel_ingest_attempt(panel, pres["spots_df"], pres["summary"], pres.get("preview"))

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
        self._leftover_status.setText("—")
        self._leftover_run_status.setText("—")
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
        mask = self._ab_initio_raw.indexed_mask
        claimed_index = self._candidate_df.index[mask]
        xyz = (self._g_2pi[mask, 0], self._g_2pi[mask, 1], self._g_2pi[mask, 2])
        panel_ids = (self._candidate_df.loc[claimed_index, "panel_id"].to_numpy()
                     if "panel_id" in self._candidate_df.columns else None)
        if self._round1_domain_index is not None:
            self._replace_domain(self._round1_domain_index, "free (ab-initio)", r, claimed_index, xyz, panel_ids)
        else:
            self._round1_domain_index = self._add_domain("free (ab-initio)", r, claimed_index, xyz, panel_ids)

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

    def _add_domain(self, method: str, result: dict, claimed_index, xyz, panel_ids=None) -> int:
        """Append a new solved domain and return its index in
        ``self._domains``. ``claimed_index`` is the set of ``candidate_df``
        row labels this domain claims (never overlapping an earlier domain's
        claim -- see ``_leftover_df``). ``panel_ids`` is an optional array
        parallel to ``xyz`` (``None`` when the source had no ``panel_id``
        column), used by the Panel color mode."""
        label = f"Domain {len(self._domains) + 1}"
        self._domains.append({"label": label, "method": method, "result": result,
                              "xyz": xyz, "claimed_index": claimed_index, "panel_ids": panel_ids})
        self._claimed_index = (claimed_index if self._claimed_index is None
                               else self._claimed_index.union(claimed_index))
        self._domain_list.addItem(
            f"{label} ({method}): {len(claimed_index)} spots, a={result['cell'][0]:.4g} Å")
        self._domain_list.setCurrentRow(len(self._domains) - 1)
        self._update_leftover_status()
        self._update_multidomain_plot()
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
        self._domain_list.item(idx).setText(
            f"{label} ({method}): {len(claimed_index)} spots, a={result['cell'][0]:.4g} Å")
        self._domain_list.setCurrentRow(idx)
        self._update_leftover_status()
        self._update_multidomain_plot()

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

    # ── stage 5: index remaining spots ──────────────────────────────

    def _on_leftover_method_changed(self, index: int):
        self._kc_params.setVisible(index == 1)

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
            }
            if self._tol_override.value() > 0:
                cfg["tol"] = self._tol_override.value()
            self._worker = SolveCellWorker("ab_initio", cfg, parent=self)
            self._worker.log_line.connect(self._log.append)
            self._worker.finished.connect(self._on_leftover_ab_initio_done)
        else:
            known_cell = self._domains[-1]["result"]["cell"]
            self._log.append(
                "─" * 40 + f"\nIndexing remaining spots (known structure, cell="
                f"{tuple(round(v, 4) for v in known_cell)})…")
            self._leftover_g_2pi = leftover[["qsample_x", "qsample_y", "qsample_z"]].to_numpy()
            cfg = {
                "g_2pi": self._leftover_g_2pi, "known_cell": known_cell,
                "tol": self._kc_tol.value(), "n_search": self._kc_n_search.value(),
                "min_accept": self._kc_min_accept.value(),
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
        self._add_domain("known structure", result, claimed_index, xyz, panel_ids)
