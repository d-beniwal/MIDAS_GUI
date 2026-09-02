"""Tab 3 — Batch Integrate.

Ports the v3 batch tab and adds Phase-1 features:
  - kernel selector (hard / subpixel K=2/4 / polygon)
  - physics corrections (polarization + solid angle)
  - per-bin variance / σ output (error model selectable)
  - native Q-uniform binning
  - output formats CSV / XYE / FXYE / DAT / HDF5
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

import numpy as np
from PyQt5 import QtCore, QtWidgets

from midas_gui.constants import (KERNELS, ERROR_MODELS,
                           DEFAULT_NICKEL_DIR, DEFAULT_KERNEL,
                           DEFAULT_ERROR_MODEL)
from midas_gui.helpers import (_fspin, _browse, _build_spec, spec_from_geometry_file,
                               geometry_fields_from_file,
                               resolve_calibration_fields, make_calib_values_button,
                               rmax_corner_px, rmax_edge_px, draw_polar_bin_overlay,
                               _NoScrollSpinBox, _NoScrollComboBox,
                               widgets_to_dict, apply_dict_to_widgets)
from midas_gui.widgets import (LogPanel, CorrectionFlagsWidget, WaterfallViewer,
                               StackedProfileViewer, DataLoaderPanel, OutputFormatSelector,
                               ImageViewer, build_lab_frame_axes_items)
from midas_gui.workers import (BatchWorker, BatchRunCoordinator, apply_q_uniform,
                               DriftWorker, FolderMonitorWorker, write_all_profiles)
from midas_gui.dialogs import show_error
from midas_gui.hydra_widgets import HydraModeRibbon
from midas_gui.hydra_batch_page import HydraBatchPage
from midas_gui.job_queue import JobQueuePanel
from midas_gui.cake_params import parse_cake_csv
from midas_gui import project
from midas_gui import settings
from midas_gui import style as S


class BatchTab(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._orphans: list = []       # aborted workers kept alive until they wind down
        self._drift_worker = None
        self._drift_traj = None
        self._calib_result = None
        self._wf_started = False
        self._monitor_worker = None
        self._integrated_fids: set = set()   # frame ids already displayed (batch + monitor)
        self._geom_cache = None              # cached integration context (detector map)
        self._geom_sig = None                # signature the cached context was built for
        self._bin_overlay_items: list = []   # Rmin/Rmax + bin-grid overlay on _det_view
        self._axis_items: list = []          # Lab-frame axes overlay on _det_view
        # Built lazily on first switch to Hydra mode: it owns 8 pyqtgraph
        # widgets (4 WaterfallViewer + 4 StackedProfileViewer), and most
        # sessions never touch Hydra Batch Integrate — see .context/DECISIONS.md's
        # pyqtgraph interpreter-teardown / widget-count crash-risk entry.
        self._hydra_page: Optional[HydraBatchPage] = None
        self._hydra_registry = None          # DataSourceRegistry, set by bind_hydra_registry()
        self._hydra_registry_label = ""
        self._last_run_inputs: dict = {}
        self._last_run_fields: dict = {}
        self._last_results: Optional[dict] = None   # for the Save button — see _on_done
        self._last_axis_ctx: Optional[tuple] = None  # (lsd, px, wl) for the Save button
        self._project_ctx: Optional[project.ProjectContext] = None
        self._build_ui()
        self._loader.monitorToggled.connect(self._toggle_monitor)
        self._loader.dataChanged.connect(self._refresh_detector_preview)
        self._use_tab2_btn.toggled.connect(self._refresh_detector_preview)
        self._json_ed.textChanged.connect(lambda *_: self._refresh_detector_preview())
        self._loader.set_path(DEFAULT_NICKEL_DIR)

    def set_project_context(self, ctx: "project.ProjectContext"):
        self._project_ctx = ctx
        if self._hydra_page is not None:
            self._hydra_page.set_project_context(ctx)

    def set_calibration(self, result):
        self._calib_result = result
        self._calib_src_lbl.setText(
            f"From Tab 2: Lsd={result.Lsd/1000:.3f} mm  "
            f"λ={result.wavelength_A:.5f} Å  {result.NrPixelsY}×{result.NrPixelsZ} px")
        self._use_tab2_btn.setChecked(True)
        self._refresh_detector_preview()

    def _calib_fields_in_use(self):
        """Resolve the geometry currently selected (Tab-2 result or file), as a
        dict of display fields — or (None, note) if unavailable. Also backs
        the "View calibration" popup (see helpers.make_calib_values_button),
        called fresh each time it's opened."""
        return resolve_calibration_fields(
            self._calib_result, self._use_json_btn.isChecked(), self._json_ed.text())

    def _apply_rmax_preset(self, formula) -> None:
        """Corner/Edge button handler — ``formula`` is ``rmax_corner_px`` or
        ``rmax_edge_px``; resolves the active calibration and fills Rmax."""
        fields, note = self._calib_fields_in_use()
        if not fields or fields.get("BC_y") is None or fields.get("NrPixelsY") is None:
            self._log.append(f"[batch] Can't set Rmax preset: {note}")
            return
        value = formula(fields["BC_y"], fields["BC_z"], fields["NrPixelsY"], fields["NrPixelsZ"])
        self._r_max.setValue(value)

    def _load_cake_csv(self) -> None:
        """"Load cake parameters CSV…" button — applies R_MIN/R_MAX/R_STEP/
        ETA_MIN/ETA_MAX/ETA_STEP/OME_SUM from an mpe_wf_saxs_waxs-style
        cake_parameters CSV (see cake_params.parse_cake_csv) to the matching
        fields. OME_START/OME_STEP have no equivalent in this pipeline and
        are intentionally not applied to anything (see the button's tooltip)."""
        path = _browse(self, "Open cake parameters CSV", "CSV (*.csv);;All (*)")
        if not path:
            return
        values = parse_cake_csv(path)
        if not values:
            QtWidgets.QMessageBox.warning(
                self, "Cake CSV", f"Could not parse any values from:\n{path}")
            return
        applied = []
        if "R_MIN" in values:
            self._r_min.setValue(values["R_MIN"]); applied.append("R_MIN")
        if "R_MAX" in values:
            self._r_max.setValue(values["R_MAX"]); applied.append("R_MAX")
        if "R_STEP" in values:
            self._r_bin.setValue(values["R_STEP"]); applied.append("R_STEP")
        if "ETA_MIN" in values:
            self._eta_min.setValue(values["ETA_MIN"]); applied.append("ETA_MIN")
        if "ETA_MAX" in values:
            self._eta_max.setValue(values["ETA_MAX"]); applied.append("ETA_MAX")
        if "ETA_STEP" in values:
            self._e_bin.setValue(values["ETA_STEP"]); applied.append("ETA_STEP")
        if "OME_SUM" in values and hasattr(self._loader, "_combine_chunk"):
            self._loader._combine_chunk.setValue(int(values["OME_SUM"]))
            applied.append("OME_SUM -> Combine sub-frames")
        self._log.append(f"[batch] Loaded cake parameters from {path}: {', '.join(applied) or '(nothing recognized)'}")

    def _refresh_detector_preview(self, *_args) -> None:
        """Refresh the Detector-view tab's frame + Rmin/Rmax/bin-grid overlay —
        called on new/changed data, a calibration-source change, or any of
        the Rmin/Rmax/R-bin/η-bin/Show-bin-grid controls changing. The Rmax
        auto-fill and overlay must not depend on a frame already being
        loaded — calibration commonly arrives before data does."""
        frame = self._loader.current_frame()
        if frame is not None:
            self._det_view.set_image(frame, autorange=True, reset_levels=True)
        fields, _ = self._calib_fields_in_use()
        if not fields or fields.get("BC_y") is None or fields.get("NrPixelsY") is None:
            draw_polar_bin_overlay(
                self._det_view, self._bin_overlay_items,
                bc_y=0.0, bc_z=0.0, r_min=0.0, r_max=0.0, r_bin=1.0, e_bin=5.0)
            self._clear_lab_axes()
            return
        if self._r_max.value() == 0.0:
            self._r_max.blockSignals(True)
            self._r_max.setValue(rmax_corner_px(
                fields["BC_y"], fields["BC_z"], fields["NrPixelsY"], fields["NrPixelsZ"]))
            self._r_max.blockSignals(False)
        draw_polar_bin_overlay(
            self._det_view, self._bin_overlay_items,
            bc_y=fields["BC_y"], bc_z=fields["BC_z"],
            r_min=self._r_min.value(), r_max=self._r_max.value(),
            r_bin=self._r_bin.value(), e_bin=self._e_bin.value(),
            eta_min=self._eta_min.value(), eta_max=self._eta_max.value(),
            show_grid=self._grid_chk.isChecked())
        self._redraw_lab_axes_if_on(fields["BC_y"], fields["BC_z"])

    def _on_preview_sum_changed(self, n: int) -> None:
        self._loader.set_preview_sum(n)
        self._refresh_detector_preview()

    # ── Lab-frame axes overlay (same as Data Viewer/Calibrate — see
    # widgets.build_lab_frame_axes_items) ───────────────────────────
    def _on_lab_axes_toggled(self, checked: bool) -> None:
        if checked:
            self._refresh_detector_preview()
        else:
            self._clear_lab_axes()

    def _redraw_lab_axes_if_on(self, bc_y: float, bc_z: float) -> None:
        if not self._lab_axes_chk.isChecked():
            return
        self._clear_lab_axes()
        img = self._det_view._data
        if img is None:
            return
        items = build_lab_frame_axes_items(self._det_view._iv, img.shape, bc_y, bc_z)
        for it in items:
            self._det_view._iv.addItem(it)
        self._axis_items.extend(items)

    def _clear_lab_axes(self) -> None:
        for it in self._axis_items:
            self._det_view._iv.removeItem(it)
        self._axis_items.clear()

    def _resolved_im_trans(self) -> tuple:
        """ImTransOpt codes from the active calibration source — the same
        flip/transpose the geometry (BC/tilts) was fit in, which every raw
        frame streamed into BatchWorker/FolderMonitorWorker must also get."""
        fields, _ = self._calib_fields_in_use()
        return tuple(fields.get("im_trans") or []) if fields else ()

    def set_mask_from_tab1(self, mask):
        self._loader.set_tab1_mask(mask)

    def bind_hydra_registry(self, registry, label: str):
        """Same role as `widgets.DataLoaderPanel.bind_registry`, for this
        tab's Hydra loader — deferred like `set_project_context` since the
        Hydra page (and its loader) is built lazily on first use."""
        self._hydra_registry = registry
        self._hydra_registry_label = label
        if self._hydra_page is not None:
            self._hydra_page._loader.bind_registry(registry, label)

    def _ensure_hydra_page(self) -> HydraBatchPage:
        if self._hydra_page is None:
            self._hydra_page = HydraBatchPage()
            self._mode_stack.addWidget(self._hydra_page)
            if self._project_ctx is not None:
                self._hydra_page.set_project_context(self._project_ctx)
            if self._hydra_registry is not None:
                self._hydra_page._loader.bind_registry(
                    self._hydra_registry, self._hydra_registry_label)
        return self._hydra_page

    def set_hydra_panel_calibration(self, n: int, result):
        """A Hydra panel's fit finished on the Calibrate tab — hand it to
        this tab's own Hydra Batch Integrate page (building it on first use)."""
        self._ensure_hydra_page().set_panel_calibration(n, result)

    # ── GUI state (Save/Load GUI State) ─────────────────────────────
    def _state_widgets(self) -> dict:
        return {
            "use_tab2_btn": self._use_tab2_btn,
            "use_json_btn": self._use_json_btn,
            "json_ed": self._json_ed,
            "kernel": self._kernel,
            "r_bin": self._r_bin,
            "e_bin": self._e_bin,
            "r_min": self._r_min,
            "r_max": self._r_max,
            "eta_min": self._eta_min,
            "eta_max": self._eta_max,
            "grid_chk": self._grid_chk,
            "azim": self._azim,
            "multi_azimuth": self._multi_azimuth_chk,
            "var_check": self._var_check,
            "err_model": self._err_model,
            "q_check": self._q_check,
            "q_min": self._q_min,
            "q_max": self._q_max,
            "q_bin": self._q_bin,
            "mon_ed": self._mon_ed,
            "drift_chk": self._drift_chk,
            "drift_anchor_ed": self._drift_anchor_ed,
            "drift_param": self._drift_param,
            "drift_knots": self._drift_knots,
            "drift_bayesian": self._drift_bayesian,
            "out_ed": self._out_ed,
            "run_mode": self._run_mode,
            "n_workers": self._n_workers,
        }

    def get_state(self) -> dict:
        return {
            "fields": widgets_to_dict(self._state_widgets()),
            "corr": self._corr_widget.get_state(),
            "fmt": self._fmt.get_state(),
            "loader": self._loader.get_state(),
            "hydra": {"active_mode": self._mode_ribbon.mode(),
                      "page": self._hydra_page.get_state() if self._hydra_page else {}},
        }

    def set_state(self, state: dict):
        self._loader.set_state(state.get("loader") or {})
        fields = dict(state.get("fields", {}))
        # A project-attempt's "fmt_keys" (see project.integrate_attempt_gui_fields)
        # rides along in "fields" but isn't a plain widget — pull it out before
        # the generic apply_dict_to_widgets pass (which would just ignore it).
        fmt_keys = fields.pop("fmt_keys", None)
        apply_dict_to_widgets(self._state_widgets(), fields)
        self._corr_widget.set_state(state.get("corr") or {})
        self._fmt.set_state(fmt_keys if fmt_keys is not None else state.get("fmt"))
        hydra_state = state.get("hydra") or {}
        page_state = hydra_state.get("page") or {}
        if page_state:
            self._ensure_hydra_page().set_state(page_state)
        self._mode_ribbon.set_mode(hydra_state.get("active_mode", "single"))

    # ── File > Open Project… ─────────────────────────────────────────
    def apply_project_integration(self, attempts: dict) -> None:
        """``attempts`` maps panel key (``"single"`` or ``"ge1"``..``"ge4"``)
        to that panel's integration-attempt metadata (``project.read_attempt``)
        — called after File > Open Project… when the user opts to populate
        this tab. Unlike plain GUI-state load, this also installs the
        recorded ``calibration_snapshot`` as a live, usable calibration (via
        ``set_calibration``/``set_panel_calibration``) so Run works
        immediately, without needing Tab 2 re-run first."""
        if not attempts:
            return
        single_meta = attempts.get("single")
        hydra_metas = {k: v for k, v in attempts.items() if k != "single"}
        state = {}
        if single_meta is not None:
            state["fields"] = project.integrate_attempt_gui_fields(single_meta)
            state["loader"] = project.integrate_attempt_loader_state(single_meta)
        if hydra_metas:
            shared_fields, loader_state, anchor_path = {}, {}, None
            for panel_key, meta in sorted(hydra_metas.items()):
                shared_fields = project.integrate_attempt_gui_fields(meta)
                loader_state = project.integrate_attempt_loader_state(meta)
                if anchor_path is None:
                    anchor_path = loader_state.get("path")
            state["hydra"] = {"active_mode": "hydra",
                               "page": {"fields": shared_fields, "loader": loader_state,
                                        "anchor_path": anchor_path}}
        elif single_meta is not None:
            state["hydra"] = {"active_mode": "single"}
        self.set_state(state)

        if single_meta is not None:
            snap = single_meta.get("calibration_snapshot")
            if snap:
                self.set_calibration(project.calibration_namespace(snap))
            self._populate_plots_from_attempt(single_meta)
        for panel_key, meta in hydra_metas.items():
            snap = meta.get("calibration_snapshot")
            n = int(panel_key[2:])
            if snap:
                self._ensure_hydra_page().set_panel_calibration(
                    n, project.calibration_namespace(snap))
            self._ensure_hydra_page().populate_panel_plots(n, meta)

    def _populate_plots_from_attempt(self, meta: dict) -> None:
        """Fill the Waterfall/Stacked-profiles views from an integration
        attempt's embedded ``profiles``/``r_axis_px``/``frame_ids`` arrays
        (``project.read_attempt_results``, stashed onto ``meta`` under
        ``_results_arrays`` by the Open Project dialog) — same widget calls
        ``_on_frame`` makes per-frame during a live run, just replayed in one
        shot instead of streamed. Best-effort: a missing/incompatible
        calibration (for the x-axis re-labelling) never blocks the plots."""
        arrays = meta.get("_results_arrays") or {}
        r_axis = arrays.get("r_axis_px")
        profiles = arrays.get("profiles")
        if r_axis is None or profiles is None or len(profiles) == 0:
            return
        try:
            spec = self._build_spec()
            axctx = (float(spec.Lsd), float(spec.pxY), float(spec.Wavelength))
            self._waterfall.set_axis_context(*axctx)
            self._stack_view.set_axis_context(*axctx)
        except Exception:
            pass
        self._waterfall.reset(r_axis)
        self._stack_view.reset(r_axis)
        frame_ids = arrays.get("frame_ids") or list(range(len(profiles)))
        self._integrated_fids = set()
        for fid, prof in zip(frame_ids, profiles):
            self._waterfall.add_profile(prof)
            self._stack_view.add_profile(r_axis, prof, label=fid)
            self._integrated_fids.add(str(fid))
        self._wf_started = True
        self._view_tabs.setCurrentWidget(self._waterfall)

    def shutdown(self):
        """Interrupt + bounded-wait every Hydra-page worker on app close —
        this tab's own workers are already covered by MainWindow's generic
        QThread sweep, but those nested inside ``self._hydra_page`` are not
        (the sweep only inspects this tab's own ``vars()``, not recursively)."""
        if self._hydra_page is not None:
            self._hydra_page.shutdown()
        self._job_queue.shutdown()

    def _on_mode_changed(self, mode: str):
        """Leftmost ribbon switched between "single" and "hydra" — swap the
        visible page (building the Hydra page on first use), mirroring
        CalibrationTab's/DataViewerTab's identical split."""
        self._mode_stack.setCurrentWidget(
            self._ensure_hydra_page() if mode == "hydra" else self._hsplit)

    def set_hydra_available(self, enabled: bool) -> None:
        """Show/hide the Hydra option on the mode ribbon (only meaningful at
        the 1-ID-E beamline profile — see MainWindow.apply_hydra_visibility)."""
        self._mode_ribbon.set_hydra_enabled(enabled)

    def _build_ui(self):
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6); root.setSpacing(0)

        # Leftmost mode ribbon: "Single detector" (this tab's existing view)
        # vs. "Hydra" (4-panel GE detector batch integration) — same pattern
        # as the Data Viewer / Calibrate tabs' splits.
        self._mode_ribbon = HydraModeRibbon()
        self._mode_ribbon.modeChanged.connect(self._on_mode_changed)
        root.addWidget(self._mode_ribbon)

        self._mode_stack = QtWidgets.QStackedWidget()
        root.addWidget(self._mode_stack, 1)

        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        split.setChildrenCollapsible(False); split.setHandleWidth(6)
        self._mode_stack.addWidget(split); self._hsplit = split

        # ── LEFT: data loader (streaming source + dark/bright/bg + mask) ──
        self._loader = DataLoaderPanel(mode="stream")
        self._loader.setMinimumWidth(200)
        split.addWidget(self._loader)

        # ── MIDDLE: parameters ──
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True); scroll.setMinimumWidth(260)
        inner = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(inner); lv.setContentsMargins(2, 2, 2, 2); lv.setSpacing(8)
        scroll.setWidget(inner)

        def _br(w=30):
            b = QtWidgets.QPushButton("…"); b.setFixedWidth(w); return b

        # ── Calibration source ──
        cal = S.make_card("Calibration source")
        src_row = QtWidgets.QHBoxLayout(); src_row.setSpacing(10)
        self._use_tab2_btn = QtWidgets.QRadioButton("From Tab 2")
        self._use_json_btn = QtWidgets.QRadioButton("From file")
        self._use_tab2_btn.setChecked(True)
        src_row.addWidget(self._use_tab2_btn); src_row.addWidget(self._use_json_btn); src_row.addStretch(1)
        cal.body.addLayout(src_row)
        self._calib_src_lbl = QtWidgets.QLabel("(run Tab 2 first)")
        self._calib_src_lbl.setStyleSheet(f"color:{S.MUTED};font-size:10px")
        cal.body.addWidget(self._calib_src_lbl)
        self._json_ed = QtWidgets.QLineEdit()
        self._json_ed.setPlaceholderText("calibration.json / paramstest.txt / .poni…")
        jr = QtWidgets.QHBoxLayout(); jr.setSpacing(4); jr.addWidget(self._json_ed, 1)
        bj = _br(); bj.clicked.connect(lambda: self._json_ed.setText(
            _browse(self, "Open calibration file",
                    "Calibration (*.json *.txt *.poni);;All (*)") or "")); jr.addWidget(bj)
        self._json_ed.textChanged.connect(
            lambda t: self._use_json_btn.setChecked(True) if t.strip() else None)
        cal.body.addLayout(jr)
        # "Calibration values" used to be an always-visible grid here (took a
        # lot of vertical space); it's now a popup, opened on click, showing
        # the same fields — see helpers.make_calib_values_button.
        calib_view_btn = make_calib_values_button(self._calib_fields_in_use)
        cal.body.addWidget(calib_view_btn, 0, QtCore.Qt.AlignLeft)
        cake_csv_btn = QtWidgets.QPushButton("Load cake parameters CSV…")
        cake_csv_btn.setToolTip(
            "Load R_MIN/R_MAX/R_STEP/ETA_MIN/ETA_MAX/ETA_STEP/OME_SUM from a "
            "cake_parameters CSV (mpe_wf_saxs_waxs convention — header row + "
            "last data row wins) into the fields below. OME_SUM fills the "
            "loader's 'Combine sub-frames' chunk size (only meaningful for a "
            "multi-file HDF5 source). OME_START/OME_STEP are omega-series "
            "bookkeeping for a different integration backend and have no "
            "equivalent here — not applied to anything.")
        cake_csv_btn.clicked.connect(self._load_cake_csv)
        cal.body.addWidget(cake_csv_btn, 0, QtCore.Qt.AlignLeft)
        lv.addWidget(cal)

        # ── Integration ──
        integ = S.make_card("Integration")
        self._kernel = _NoScrollComboBox()
        for label, key in KERNELS.items():
            self._kernel.addItem(label, key)
        _ki = self._kernel.findData(DEFAULT_KERNEL)
        if _ki >= 0:
            self._kernel.setCurrentIndex(_ki)
        self._azim = _NoScrollComboBox()
        self._azim.addItem("Pixel-weighted", True)
        self._azim.addItem("η-bin mean (legacy)", False)
        self._azim.setToolTip(
            "How the 2-D (η, R) cake is collapsed to a 1-D profile:\n"
            "• Pixel-weighted — Σ(mean·count)/Σ(count); robust to partial azimuthal\n"
            "  coverage / off-detector beam centres and independent of η-bin size.\n"
            "• η-bin mean — unweighted mean of the per-η-bin means (can distort the\n"
            "  profile with a coarse η bin when the beam centre is off the detector).")
        intf = S.Form()
        intf.row(("Kernel:", self._kernel))
        intf.row(("Azim. avg:", self._azim))
        integ.body.addLayout(intf)

        def _section_label(text):
            lbl = QtWidgets.QLabel(text)
            lbl.setStyleSheet(f"color:{S.MUTED};font-size:10px;font-weight:bold;")
            return lbl

        # Grouped per axis (bin size + range together) so it's clear these
        # are all one thing — the caking geometry, matching a
        # cake_parameters CSV's R_MIN/R_MAX/R_STEP/ETA_MIN/ETA_MAX/ETA_STEP
        # one-for-one (see _load_cake_csv). Rmax 0.0 is the "auto" sentinel:
        # left untouched, it's passed through as RMax=None so the backend's
        # own farthest-corner default stays authoritative (see
        # helpers._build_spec) — auto-filled to that same corner value once
        # calibration resolves. Eta min/max default -180/180 (full circle),
        # matching the backend's own default (same None-means-"leave the
        # backend default" contract as Rmin/Rmax).
        integ.body.addWidget(_section_label("RADIAL RANGE  (R_MIN / R_MAX / R_STEP)"))
        self._r_bin = _fspin(0.1, 20.0, 2, 1.0, "px")
        self._r_min = _fspin(0.0, 1_000_000.0, 2, 0.0, "px")
        self._r_max = _fspin(0.0, 1_000_000.0, 2, 0.0, "px")
        self._r_max.setToolTip(
            "0 = auto (farthest detector corner from the beam centre).\n"
            "Use the Corner/Edge buttons to fill in a value, or type your own.")
        self._rmax_corner_btn = QtWidgets.QPushButton("Corner")
        self._rmax_corner_btn.setToolTip("Set Rmax to the farthest detector CORNER from the beam centre.")
        self._rmax_corner_btn.clicked.connect(lambda: self._apply_rmax_preset(rmax_corner_px))
        self._rmax_edge_btn = QtWidgets.QPushButton("Edge")
        self._rmax_edge_btn.setToolTip("Set Rmax to the farthest detector EDGE from the beam centre.")
        self._rmax_edge_btn.clicked.connect(lambda: self._apply_rmax_preset(rmax_edge_px))
        rmax_row = QtWidgets.QHBoxLayout(); rmax_row.setSpacing(4)
        rmax_row.addWidget(self._r_max)
        rmax_row.addWidget(self._rmax_corner_btn); rmax_row.addWidget(self._rmax_edge_btn)
        rf = S.Form()
        rf.row(("R bin:", self._r_bin))
        rf.row(("Rmin:", self._r_min))
        rf.row(("Rmax:", rmax_row))
        integ.body.addLayout(rf)

        integ.body.addWidget(_section_label("AZIMUTHAL RANGE  (ETA_MIN / ETA_MAX / ETA_STEP)"))
        self._e_bin = _fspin(0.5, 30.0, 1, 5.0, "°")
        self._eta_min = _fspin(-180.0, 180.0, 1, -180.0, "°")
        self._eta_max = _fspin(-180.0, 180.0, 1, 180.0, "°")
        ef = S.Form()
        ef.row(("η bin:", self._e_bin))
        ef.row(("Eta min:", self._eta_min), ("Eta max:", self._eta_max))
        integ.body.addLayout(ef)

        self._grid_chk = QtWidgets.QCheckBox("Show bin grid")
        self._grid_chk.setToolTip(
            "Overlay the full (R, η) integration bin grid on the Detector "
            "view tab — concentric circles at each R-bin edge, spokes at "
            "each η-bin edge (bounded to Eta min/max). Thinned to at most "
            "~50 rings / ~72 spokes for legibility with fine bin sizes.")
        integ.body.addWidget(self._grid_chk)
        for w in (self._r_min, self._r_max, self._eta_min, self._eta_max, self._r_bin, self._e_bin):
            w.valueChanged.connect(self._refresh_detector_preview)
        self._grid_chk.toggled.connect(self._refresh_detector_preview)
        self._multi_azimuth_chk = QtWidgets.QCheckBox("Multi-azimuth output (cake)")
        self._multi_azimuth_chk.setToolTip(
            "Keep every azimuthal (η) sector as a SEPARATE output profile "
            "instead of collapsing to one full-circle-averaged profile per "
            "frame. Reuses the η bin/η range above to define the sectors.\n\n"
            "Off by default — η bin already defaults to 5° over the full "
            "360° (72 internal bins) purely to control collapse-weighting "
            "resolution; turning this on repurposes that same setting to "
            "also define real output granularity, so every existing run's "
            "result size is unaffected unless you opt in.\n\n"
            "Needed for per-azimuth GSAS-II/texture analysis. Not yet "
            "supported together with Q-uniform bins.")
        integ.body.addWidget(self._multi_azimuth_chk)
        self._var_check = QtWidgets.QCheckBox("Per-bin variance (σ)")
        self._var_check.setToolTip(
            "Compute per-bin σ via the chosen error model.\n"
            "Mutually exclusive with corrections (corrections win; σ→√I).")
        self._err_model = _NoScrollComboBox(); self._err_model.addItems(ERROR_MODELS); self._err_model.setEnabled(False)
        if DEFAULT_ERROR_MODEL in ERROR_MODELS:
            self._err_model.setCurrentText(DEFAULT_ERROR_MODEL)
        self._var_check.toggled.connect(self._err_model.setEnabled)
        vrow = QtWidgets.QHBoxLayout(); vrow.setSpacing(6)
        vrow.addWidget(self._var_check); vrow.addWidget(self._err_model, 1)
        integ.body.addLayout(vrow)
        self._q_check = QtWidgets.QCheckBox("Q-uniform bins")
        self._q_check.setToolTip("Bin uniformly in Q (Å⁻¹) instead of R (px).")
        integ.body.addWidget(self._q_check)
        self._q_min = _fspin(0.0, 100.0, 3, 0.5, "Å⁻¹")
        self._q_max = _fspin(0.0, 100.0, 3, 8.0, "Å⁻¹")
        self._q_bin = _fspin(0.0001, 1.0, 4, 0.01, "Å⁻¹")
        for w in (self._q_min, self._q_max, self._q_bin):
            w.setEnabled(False)
        self._q_check.toggled.connect(lambda c: [w.setEnabled(c) for w in
                                                 (self._q_min, self._q_max, self._q_bin)])
        qf = S.Form(); qf.row(("Qmin:", self._q_min), ("Qmax:", self._q_max)); qf.row(("ΔQ:", self._q_bin))
        integ.body.addLayout(qf)
        lv.addWidget(integ)

        # ── Corrections ──
        self._corr_widget = CorrectionFlagsWidget()
        lv.addWidget(self._corr_widget)

        # ── Monitor normalisation ──
        mon = S.make_card("Monitor normalisation (optional)")
        self._mon_ed = QtWidgets.QLineEdit()
        self._mon_ed.setPlaceholderText("monitor.txt  (one value per line)")
        monr = QtWidgets.QHBoxLayout(); monr.setSpacing(4); monr.addWidget(self._mon_ed, 1)
        bmon = _br(); bmon.clicked.connect(lambda: self._mon_ed.setText(
            _browse(self, "Open monitor file", "Text (*.txt *.dat *.csv);;All (*)") or ""))
        monr.addWidget(bmon)
        mon.body.addLayout(monr)
        mon_note = QtWidgets.QLabel(
            "Each profile is divided by the corresponding monitor value.\n"
            "File: one floating-point number per line, one per processed frame.")
        mon_note.setWordWrap(True)
        mon_note.setStyleSheet(f"color:{S.MUTED};font-size:10px")
        mon.body.addWidget(mon_note)
        lv.addWidget(mon)

        # ── Drift correction ──
        drift = S.make_card("Drift correction (long scans)")
        self._drift_chk = QtWidgets.QCheckBox("Enable per-frame geometry drift correction")
        drift.body.addWidget(self._drift_chk)
        self._drift_anchor_ed = QtWidgets.QLineEdit()
        self._drift_anchor_ed.setPlaceholderText("anchors.json  ({frame_idx: {Lsd, BC_y, BC_z}})")
        drow = QtWidgets.QHBoxLayout(); drow.setSpacing(4); drow.addWidget(self._drift_anchor_ed, 1)
        bdrift = QtWidgets.QPushButton("…"); bdrift.setFixedWidth(30)
        bdrift.clicked.connect(lambda: self._drift_anchor_ed.setText(
            _browse(self, "Open anchor JSON", "JSON (*.json);;All (*)") or ""))
        drow.addWidget(bdrift); drift.body.addLayout(drow)
        df = S.Form()
        self._drift_param = _NoScrollComboBox()
        self._drift_param.addItems(["spline", "linear", "constant"])
        self._drift_knots = _NoScrollSpinBox(); self._drift_knots.setRange(2, 1_000_000); self._drift_knots.setValue(5)
        df.row(("Parametrization:", self._drift_param), ("n_knots:", self._drift_knots))
        drift.body.addLayout(df)
        self._drift_bayesian = QtWidgets.QCheckBox("Bayesian σ estimate"); self._drift_bayesian.setChecked(True)
        drift.body.addWidget(self._drift_bayesian)
        self._drift_fit_btn = QtWidgets.QPushButton("Fit trajectory")
        self._drift_fit_btn.clicked.connect(self._fit_drift)
        drift.body.addWidget(self._drift_fit_btn)
        self._drift_status_lbl = QtWidgets.QLabel("No trajectory fitted")
        self._drift_status_lbl.setStyleSheet(f"color:{S.MUTED};font-size:10px")
        drift.body.addWidget(self._drift_status_lbl)
        lv.addWidget(drift)
        # Not used in production yet — hidden from the GUI but left fully wired
        # (DriftWorker/_fit_drift/state save-restore all still work) so it can
        # be shown again by removing this one line.
        drift.setVisible(False)

        # ── Output ──
        out = S.make_card("Output")
        self._out_ed = QtWidgets.QLineEdit(); self._out_ed.setPlaceholderText("Output directory…")
        orow = QtWidgets.QHBoxLayout(); orow.setSpacing(4); orow.addWidget(self._out_ed, 1)
        bou = _br(); bou.clicked.connect(lambda: self._out_ed.setText(
            QtWidgets.QFileDialog.getExistingDirectory(self, "Output directory") or "")); orow.addWidget(bou)
        out.body.addLayout(S.Form().row(("Folder:", orow)))
        self._fmt = OutputFormatSelector()
        out.body.addWidget(self._fmt)
        lv.addWidget(out)

        # ── Run mode ──
        run_mode_card = S.make_card("Run mode")
        mode_row = QtWidgets.QHBoxLayout(); mode_row.setSpacing(6)
        _mode_tip = (
            "Sequential: frames integrated one at a time.\n"
            "Batch Parallel: splits this run's frames across N workers sharing "
            "one detector map (built once). The worker count auto-shrinks so "
            f"each worker gets ≥{BatchRunCoordinator.MIN_FRAMES_PER_WORKER} frames.")
        _mode_lbl = S.LabelRight("Mode:")
        _mode_lbl.setToolTip(_mode_tip)
        mode_row.addWidget(_mode_lbl)
        self._run_mode = _NoScrollComboBox()
        self._run_mode.addItem("Sequential", "sequential")
        self._run_mode.addItem("Batch Parallel", "batch_parallel")
        self._run_mode.setToolTip(_mode_tip)
        mode_row.addWidget(self._run_mode)
        mode_row.addWidget(S.LabelRight("Workers:"))
        _max_workers = os.cpu_count() or 8
        self._n_workers = _NoScrollSpinBox()
        self._n_workers.setRange(1, _max_workers)
        self._n_workers.setValue(min(4, _max_workers))
        self._n_workers.setEnabled(False)
        mode_row.addWidget(self._n_workers)
        self._run_mode.currentIndexChanged.connect(
            lambda *_: self._n_workers.setEnabled(self._run_mode.currentData() == "batch_parallel"))
        run_mode_card.body.addLayout(mode_row)
        lv.addWidget(run_mode_card)

        # ── Run ──
        self._run_btn = S.primary_btn("Start Integration")
        self._run_btn.clicked.connect(self._run)
        self._abort_btn = QtWidgets.QPushButton("Abort")
        self._abort_btn.setEnabled(False)
        self._abort_btn.setToolTip("Stop after the current frame, keeping frames already integrated.")
        self._abort_btn.clicked.connect(self._abort)
        self._abort_btn.setStyleSheet(S.DANGER_BTN_QSS)
        self._save_btn = QtWidgets.QPushButton("Save")
        self._save_btn.setEnabled(False)
        self._save_btn.setToolTip(
            "Write the lineouts already computed this run to disk, in the "
            "checked format(s) above — works even if no Output folder was "
            "set before running.")
        self._save_btn.clicked.connect(self._save_results)
        self._save_btn.setStyleSheet(S.SUCCESS_BTN_QSS)
        run_row = QtWidgets.QHBoxLayout(); run_row.setSpacing(6)
        run_row.addWidget(self._run_btn, 1)
        run_row.addWidget(self._abort_btn, 1)
        run_row.addWidget(self._save_btn, 1)
        lv.addLayout(run_row)
        self._run_job_btn = QtWidgets.QPushButton("Run as background job")
        self._run_job_btn.setToolTip(
            "Linux only. Runs this same integration in a detached `screen` "
            "session (python -m midas_gui.batch_cli) instead of in-process — "
            "survives closing this GUI. Needs an Output folder (results and "
            "a calibration snapshot are written there for the job to read).\n"
            "Progress/log/cancel are tracked in the 'Background jobs' panel below.")
        self._run_job_btn.clicked.connect(self._run_as_job)
        lv.addWidget(self._run_job_btn)
        self._clear_btn = QtWidgets.QPushButton("Clear results")
        self._clear_btn.setToolTip(
            "Remove the integrated profiles/plots computed this session for the "
            "current data so a fresh integration can start. Does NOT delete raw "
            "data or any files on disk.")
        self._clear_btn.clicked.connect(self._clear_results)
        lv.addWidget(self._clear_btn)
        self._prog = QtWidgets.QProgressBar(); self._prog.setRange(0, 100); self._prog.setVisible(False)
        lv.addWidget(self._prog)
        self._prog_lbl = QtWidgets.QLabel(""); self._prog_lbl.setStyleSheet(f"font-size:10px;color:{S.MUTED}")
        lv.addWidget(self._prog_lbl)
        lv.addStretch(1)
        split.addWidget(scroll)

        # Right: waterfall / stacked-profiles / detector-view tabs + log
        right = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self._view_tabs = QtWidgets.QTabWidget()
        self._waterfall = WaterfallViewer()
        self._stack_view = StackedProfileViewer()
        self._det_view = ImageViewer()
        self._lab_axes_chk = QtWidgets.QCheckBox("Lab-frame axes")
        self._lab_axes_chk.setToolTip(
            "Overlay MIDAS lab-frame axes (X_Lab/Y_Lab), the beam-direction "
            "⊗ glyph, and an η sweep arc, anchored at the active "
            "calibration's beam centre — same overlay as the Data "
            "Viewer/Calibrate tabs.")
        self._lab_axes_chk.toggled.connect(self._on_lab_axes_toggled)
        self._det_view._toolbar_layout.addWidget(self._lab_axes_chk)
        self._det_view._toolbar_layout.addWidget(QtWidgets.QLabel("Preview: sum first"))
        self._preview_sum_n = _NoScrollSpinBox()
        self._preview_sum_n.setRange(1, 999); self._preview_sum_n.setValue(1)
        self._preview_sum_n.setFixedWidth(50)
        self._preview_sum_n.setToolTip(
            "Detector-view preview only — never affects the real batch run. "
            "Sums this many of the source's leading frames together before "
            "display, to boost signal enough to see the actual diffraction "
            "pattern beneath detector-readout artifacts (e.g. VAREX "
            "per-column gain non-uniformity) that dominate a single frame.")
        self._preview_sum_n.valueChanged.connect(self._on_preview_sum_changed)
        self._det_view._toolbar_layout.addWidget(self._preview_sum_n)
        self._view_tabs.addTab(self._det_view, "Detector view")
        self._view_tabs.addTab(self._waterfall, "Waterfall")
        self._view_tabs.addTab(self._stack_view, "Stacked profiles")
        right.addWidget(self._view_tabs)
        self._log = LogPanel()
        self._log.setMaximumHeight(16_777_215)   # let the splitter size it
        right.addWidget(self._log)
        self._job_queue = JobQueuePanel()
        right.addWidget(self._job_queue)
        right.setStretchFactor(0, 4); right.setStretchFactor(1, 1); right.setStretchFactor(2, 2)
        right.setMinimumWidth(320)
        split.addWidget(right)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 0); split.setStretchFactor(2, 1)
        split.setSizes([286, 361, 950])

    # ── Run ────────────────────────────────────────────────────────

    def _build_spec(self):
        # Always R-uniform; Q-uniform is handled by rebinning in the worker because the
        # kernels do not implement Q-mode binning (see analyze_workflows/workflow_analysis.md).
        r_bin = self._r_bin.value(); e_bin = self._e_bin.value()
        r_min = self._r_min.value(); r_max = self._r_max.value() or None
        eta_min = self._eta_min.value(); eta_max = self._eta_max.value()
        if self._use_tab2_btn.isChecked():
            if self._calib_result is None:
                raise RuntimeError("No calibration from Tab 2. Run Tab 2 first.")
            return _build_spec(self._calib_result, r_bin, e_bin, r_min=r_min, r_max=r_max,
                               eta_min=eta_min, eta_max=eta_max)
        path = self._json_ed.text().strip()
        if not path or not Path(path).exists():
            raise FileNotFoundError(f"Calibration file not found: {path}")
        return spec_from_geometry_file(path, r_bin, e_bin, r_min=r_min, r_max=r_max,
                                       eta_min=eta_min, eta_max=eta_max)

    def _run(self):
        if self._worker and self._worker.isRunning():
            return
        # A fresh batch run resets the display; stop any active monitor first.
        if self._loader.is_monitoring():
            self._stop_monitor()
            self._loader.set_monitor_active(False)
        self._orphans = [o for o in self._orphans if o.isRunning()]   # drop finished ones
        try:
            spec = self._build_spec()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Calibration error", str(e)); return

        src_cfg = self._loader.source_cfg()
        if not (src_cfg.get("path") or src_cfg.get("paths")):
            QtWidgets.QMessageBox.warning(
                self, "No data",
                "Select a data folder/glob, HDF5 file, or file selection."); return

        kernel = self._kernel.currentData()
        corrections = self._corr_widget.build_corrections()
        variance_cfg = ({"error_model": self._err_model.currentText()}
                        if self._var_check.isChecked() else None)
        if variance_cfg and self._corr_widget.any_enabled():
            self._log.append("[batch] Note: corrections enabled → variance ignored (σ=√I).")
            variance_cfg = None

        out_dir = self._out_ed.text().strip() or None
        fmts = self._fmt.checked_keys()
        q_cfg = ({"QMin": self._q_min.value(), "QMax": self._q_max.value(),
                  "QBinSize": self._q_bin.value()} if self._q_check.isChecked() else None)
        multi_azimuth = self._multi_azimuth_chk.isChecked()
        if multi_azimuth and q_cfg:
            QtWidgets.QMessageBox.warning(
                self, "Incompatible options",
                "Multi-azimuth output isn't supported together with "
                "Q-uniform bins yet. Uncheck one of them."); return
        lsd, px, wl = float(spec.Lsd), float(spec.pxY), float(spec.Wavelength)
        _axctx = (lsd, px, wl, "Q" if q_cfg else "R")
        self._stack_view.set_axis_context(*_axctx)
        self._waterfall.set_axis_context(*_axctx)

        # Dark / bright / background fields (from the loader)
        for sel in self._loader.has_pending_fields():
            QtWidgets.QMessageBox.warning(
                self, "Field not computed",
                f"'{sel.title()}' is enabled but not computed. "
                "Click 'Compute field' in that box first."); return
        dark = self._loader.dark()
        bright = self._loader.bright()
        background = self._loader.background()
        bright_mode = self._loader.bright_mode()

        self._run_btn.setEnabled(False); self._abort_btn.setEnabled(True)
        self._save_btn.setEnabled(False)
        self._last_results = None
        self._prog.setVisible(True); self._prog.setValue(0)
        self._wf_started = False
        self._integrated_fids = set()
        self._view_tabs.setCurrentWidget(self._waterfall)
        self._log.append("─" * 40 + "\nStarting batch integration…")

        # Frame range (from the loader)
        frame_range = self._loader.frame_range()

        # Monitor normalisation file
        monitor_file = self._mon_ed.text().strip() or None

        # Drift trajectory (optional)
        drift_traj = None
        if self._drift_chk.isChecked():
            if self._drift_traj is None:
                QtWidgets.QMessageBox.warning(
                    self, "No trajectory",
                    "Drift correction enabled but no trajectory fitted.\n"
                    "Click 'Fit trajectory' first."); return
            drift_traj = self._drift_traj

        weighted = bool(self._azim.currentData())
        sig = self._integration_signature(src_cfg, kernel, corrections, weighted)
        context = self._geom_cache if (sig == self._geom_sig and
                                       self._geom_cache is not None) else None

        self._last_run_inputs = {
            "src_cfg": src_cfg, "kernel": kernel, "fmt": fmts,
            "frame_range": frame_range, "monitor_file": monitor_file,
            "q_cfg": q_cfg, "weighted": weighted, "bright_mode": bright_mode,
            "mask_sources": self._loader.get_state().get("mask"),
            "r_bin": self._r_bin.value(), "e_bin": self._e_bin.value(),
            "multi_azimuth": multi_azimuth,
        }
        self._last_axis_ctx = (lsd, px, wl)
        mask = self._loader.composite_mask()
        self._last_run_fields = {
            "mask": mask,
            "mask_is_file_backed": mask is not None and not self._loader.has_live_mask_source(),
        }

        self._worker = BatchRunCoordinator(
            spec, src_cfg, self._loader.composite_mask(), out_dir, fmts, kernel,
            corrections, variance_cfg, q_cfg=q_cfg,
            frame_range=frame_range, monitor_file=monitor_file,
            drift_traj=drift_traj, parent=self,
            dark=dark, bright=bright, background=background, bright_mode=bright_mode,
            weighted=weighted, context=context, im_trans=self._resolved_im_trans(),
            multi_azimuth=multi_azimuth,
            run_mode=self._run_mode.currentData(), n_workers=self._n_workers.value())
        self._worker.progress.connect(self._on_progress)
        self._worker.frame_done.connect(self._on_frame)
        self._worker.finished.connect(self._on_done)
        self._worker.failed.connect(self._on_fail)
        self._worker.log_line.connect(self._log.append)
        self._worker.geom_ready.connect(lambda ctx, s=sig: self._cache_geom(s, ctx))
        self._worker.start()

    def _run_as_job(self):
        """Launch this same integration as a detached background `screen`
        job (see job_queue.JobQueuePanel) instead of running in-process —
        survives closing this GUI. Everything the CLI needs is written to
        disk first: the background process (a fresh `python -m
        midas_gui.batch_cli`) has no access to this GUI's live state."""
        try:
            spec = self._build_spec()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Calibration error", str(e)); return

        src_cfg = self._loader.source_cfg()
        if not (src_cfg.get("path") or src_cfg.get("paths")):
            QtWidgets.QMessageBox.warning(
                self, "No data",
                "Select a data folder/glob, HDF5 file, or file selection."); return

        out_dir = self._out_ed.text().strip()
        if not out_dir:
            QtWidgets.QMessageBox.warning(
                self, "Output folder required",
                "Background jobs need an Output folder — the job writes its "
                "calibration snapshot and results there."); return
        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        for sel in self._loader.has_pending_fields():
            QtWidgets.QMessageBox.warning(
                self, "Field not computed",
                f"'{sel.title()}' is enabled but not computed. "
                "Click 'Compute field' in that box first."); return

        fmts = self._fmt.checked_keys()
        if not fmts:
            QtWidgets.QMessageBox.warning(
                self, "No format", "Check at least one output format first."); return
        multi_azimuth = self._multi_azimuth_chk.isChecked()
        if multi_azimuth and self._q_check.isChecked():
            QtWidgets.QMessageBox.warning(
                self, "Incompatible options",
                "Multi-azimuth output isn't supported together with "
                "Q-uniform bins yet. Uncheck one of them."); return
        if self._q_check.isChecked():
            QtWidgets.QMessageBox.warning(
                self, "Not supported in background jobs yet",
                "Q-uniform bins aren't wired into background jobs yet.\n"
                "Uncheck it, or use 'Start Integration' for an in-process run."); return

        import tifffile
        from midas_gui.helpers import write_standalone_paramstest
        # Materialize the calibration this run uses to a standalone file so
        # the background process never needs Tab 2's live in-memory result.
        if self._use_tab2_btn.isChecked():
            if self._calib_result is None:
                QtWidgets.QMessageBox.critical(
                    self, "Calibration error", "No calibration from Tab 2. Run Tab 2 first."); return
            calib_path = out_path / "_bg_job_calibration.txt"
            write_standalone_paramstest(self._calib_result, calib_path)
        else:
            calib_path = self._json_ed.text().strip()
            if not calib_path or not Path(calib_path).exists():
                QtWidgets.QMessageBox.critical(
                    self, "Calibration error",
                    f"Calibration file not found: {calib_path}"); return

        argv = [sys.executable, "-m", "midas_gui.batch_cli",
               "--calib-file", str(calib_path),
               "--r-bin", str(self._r_bin.value()), "--eta-bin", str(self._e_bin.value())]
        if self._r_min.value():
            argv += ["--r-min", str(self._r_min.value())]
        if self._r_max.value():
            argv += ["--r-max", str(self._r_max.value())]

        if src_cfg["type"] == "tiff_glob":
            argv += ["--source-type", "tiff_glob", "--source-path", src_cfg["path"]]
        elif src_cfg["type"] == "hdf5":
            argv += ["--source-type", "hdf5", "--source-path", src_cfg["path"],
                     "--dataset", src_cfg.get("dataset", "frames")]
        else:
            argv += ["--source-type", "tiff_list", "--source-paths", *src_cfg["paths"]]

        argv += ["--out-dir", str(out_path), "--fmts", ",".join(fmts),
                 "--kernel", self._kernel.currentData()]

        frame_range = self._loader.frame_range()
        argv += ["--frame-start", str(frame_range[0]), "--frame-stride", str(frame_range[2])]
        if frame_range[1] is not None:
            argv += ["--frame-end", str(frame_range[1])]

        if multi_azimuth:
            argv += ["--multi-azimuth"]
        argv += ["--weighted"] if bool(self._azim.currentData()) else ["--no-weighted"]

        if self._corr_widget.polar_check.isChecked():
            argv += ["--polarization",
                     "--pol-fraction", str(self._corr_widget.pol_fraction.value()),
                     "--pol-plane", str(self._corr_widget.pol_plane.value())]
        if self._corr_widget.solid_check.isChecked():
            argv += ["--solid-angle"]
        if self._var_check.isChecked() and not self._corr_widget.any_enabled():
            argv += ["--variance", "--error-model", self._err_model.currentText()]

        mask = self._loader.composite_mask()
        if mask is not None:
            mask_path = out_path / "_bg_job_mask.tif"
            tifffile.imwrite(str(mask_path), mask.astype("uint8"))
            argv += ["--mask", str(mask_path)]
        for name, arr in (("dark", self._loader.dark()), ("bright", self._loader.bright()),
                         ("background", self._loader.background())):
            if arr is not None:
                p = out_path / f"_bg_job_{name}.tif"
                tifffile.imwrite(str(p), np.asarray(arr, dtype=np.float32))
                argv += [f"--{name}", str(p)]
        if self._loader.dark() is not None or self._loader.bright() is not None:
            argv += ["--bright-mode", self._loader.bright_mode()]

        monitor_file = self._mon_ed.text().strip()
        if monitor_file:
            argv += ["--monitor-file", monitor_file]

        end = frame_range[1] if frame_range[1] is not None else (self._loader.n_frames() or frame_range[0] + 1)
        total = max(1, (end - frame_range[0] + frame_range[2] - 1) // frame_range[2])

        job = self._job_queue.launch(argv, name=out_path.name or "batch", total_frames=total)
        if job is not None:
            self._log.append(f"[batch] Launched background job: {job.session} "
                             f"(see 'Background jobs' panel below)")

    def _abort(self):
        """Stop the run. First ask the worker to stop cooperatively (clean finish
        with a summary); if it does not stop quickly, detach + terminate it and free
        the slot so a new run can start immediately (the orphaned thread winds down
        on its own). Frames already integrated were written to disk as the run went."""
        w = self._worker
        if not (w and w.isRunning()):
            return
        self._abort_btn.setEnabled(False)
        self._abort_btn.setText("Aborting…")
        self._log.append("[batch] aborting after the current frame…")
        w.requestInterruption()
        if w.wait(3000):
            return   # stopped cooperatively → _on_done fires and resets the UI
        # Still inside a long frame — detach and let it wind down on its own.
        # (No terminate(): killing a thread inside torch/numpy can crash the app.)
        for sig in (w.progress, w.frame_done, w.finished, w.failed, w.log_line):
            try:
                sig.disconnect()
            except Exception:
                pass
        self._orphans.append(w)
        self._worker = None
        self._reset_run_buttons(); self._prog.setVisible(False)
        self._log.append("[batch] aborted (a background thread is finishing the "
                         "current frame). Completed frames were saved.")

    def _on_progress(self, done, total):
        self._prog.setValue(int(100 * done / total) if total else 0)
        self._prog_lbl.setText(f"Integrated {done} / {total} frames")

    def _on_frame(self, fid, r_ax, prof, sigma):
        if not getattr(self, "_wf_started", False):
            self._waterfall.reset(r_ax)
            self._stack_view.reset(r_ax)
            self._wf_started = True
        self._waterfall.add_profile(prof)
        self._stack_view.add_profile(r_ax, prof, label=fid)
        self._integrated_fids.add(str(fid))
        self._log.append(f"  frame {fid}: peak={prof.max():.1f}")

    def _reset_run_buttons(self):
        self._run_btn.setEnabled(True)
        self._abort_btn.setEnabled(False); self._abort_btn.setText("Abort")

    def _on_done(self, data):
        self._reset_run_buttons(); self._prog.setVisible(False)
        n = data["n"]; out = data.get("out_paths", [])
        aborted = data.get("aborted", False)
        verb = "aborted after" if aborted else "Done —"
        msg = f"{verb} {n} frames integrated"
        if out:
            msg += f"\nSaved to: {Path(out[0]).parent}"
        self._log.append(msg)
        self._prog_lbl.setText(f"{'Aborted' if aborted else 'Complete'}: {n} frames")
        if n and data.get("r_axis_px") is not None and self._last_axis_ctx is not None:
            profiles_arr = data.get("profiles")
            n_eta_bins = (int(profiles_arr.shape[1])
                         if profiles_arr is not None and profiles_arr.ndim == 3 else 1)
            self._last_results = {
                "r_axis_px": data["r_axis_px"], "profiles": profiles_arr,
                "sigmas": data.get("sigmas"), "frame_ids": data.get("frame_ids"),
                "n_eta_bins": n_eta_bins, "eta_axis": data.get("eta_axis"),
            }
            self._save_btn.setEnabled(True)
        self._log_to_project(data)
        QtWidgets.QMessageBox.information(self, "Aborted" if aborted else "Done", msg)

    def _log_to_project(self, data):
        if not self._project_ctx or not self._project_ctx.path:
            return
        calib_fields, _note = self._calib_fields_in_use()
        calib_ref = None
        if self._use_tab2_btn.isChecked() and self._calib_result is not None:
            calib_ref = getattr(self._calib_result, "_project_attempt_ref", None)
        profiles_arr = data.get("profiles")
        n_eta_bins = (int(profiles_arr.shape[1])
                     if profiles_arr is not None and profiles_arr.ndim == 3 else 1)
        eta_axis = data.get("eta_axis")
        try:
            ref = project.append_integration_attempt(
                self._project_ctx.path, "single",
                inputs=self._last_run_inputs, finished_payload=data,
                calibration_snapshot=calib_fields, calib_attempt_ref=calib_ref,
                extra={"active_profile": settings.active_profile(),
                       "n_eta_bins": n_eta_bins,
                       "eta_axis_deg": (eta_axis.tolist() if eta_axis is not None else None)},
                **self._last_run_fields)
            self._log.append(f"Logged to project: {ref}")
        except Exception:
            import traceback as _tb
            self._log.append("Could not log to project file:\n" + _tb.format_exc())

    def _on_fail(self, msg):
        self._reset_run_buttons(); self._prog.setVisible(False)
        show_error(self, "Integration failed", msg, log=self._log, log_prefix="\nERROR:\n")

    def _clear_results(self):
        """Clear this session's computed profiles/plots for the current data so a
        fresh integration can start. Only in-session results are cleared — no raw
        data or files on disk are touched."""
        if self._worker and self._worker.isRunning():
            QtWidgets.QMessageBox.warning(
                self, "Run in progress",
                "Abort the running integration before clearing results.")
            return
        if self._loader.is_monitoring():
            self._stop_monitor()
            self._loader.set_monitor_active(False)
        self._waterfall.reset()
        self._stack_view.reset()
        self._integrated_fids = set()
        self._wf_started = False
        self._last_results = None
        self._save_btn.setEnabled(False)
        self._prog.setVisible(False); self._prog.setValue(0)
        self._prog_lbl.setText("")
        self._log.append("Cleared session results — raw data untouched.")

    def _save_results(self):
        """Write the lineouts already computed this run to disk — independent
        of whether an Output folder was set before running (that only wired
        up incremental per-frame writes; this writes everything now, in the
        currently-checked format(s))."""
        if not self._last_results or self._last_axis_ctx is None:
            return
        fmts = self._fmt.checked_keys()
        if not fmts:
            QtWidgets.QMessageBox.warning(
                self, "No format", "Check at least one output format first."); return
        out_dir = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Save lineouts to…", self._out_ed.text().strip())
        if not out_dir:
            return
        if "2d_csv" in fmts and self._last_results.get("n_eta_bins", 1) <= 1:
            self._log.append(
                "[batch] Note: 2D CSV (cake) isn't saved by Save for this run — "
                "per-frame cakes are only kept when 'Multi-azimuth output' was "
                "checked; re-run with that (or an Output folder + 2D CSV "
                "checked) to get that format.")
        lsd, px, wl = self._last_axis_ctx
        try:
            paths = write_all_profiles(
                out_dir, fmts, self._last_results["r_axis_px"],
                self._last_results["profiles"], self._last_results["sigmas"],
                self._last_results["frame_ids"], lsd, px, wl,
                eta_axis=self._last_results.get("eta_axis"))
        except Exception as e:
            show_error(self, "Save failed", str(e), log=self._log, log_prefix="\nERROR:\n")
            return
        self._log.append(f"Saved {len(paths)} file(s) to {out_dir}")

    # ── Folder monitoring (live new-file integration) ──────────────

    def _integration_signature(self, src_cfg, kernel, corrections, weighted):
        """Signature identifying a reusable detector map for the current settings."""
        if self._use_tab2_btn.isChecked():
            calib = ("tab2", id(self._calib_result))
        else:
            calib = ("file", self._json_ed.text().strip())
        mask = self._loader.composite_mask()
        mask_id = None if mask is None else (tuple(mask.shape), int(np.count_nonzero(mask)))
        pol, sa = corrections
        return (calib, kernel, round(self._r_bin.value(), 4), round(self._e_bin.value(), 4),
                round(self._r_min.value(), 4), round(self._r_max.value(), 4),
                bool(weighted), pol is not None, sa is not None, mask_id,
                src_cfg.get("path"), tuple(src_cfg.get("paths") or ()),
                src_cfg.get("type"), src_cfg.get("dataset"))

    def _cache_geom(self, sig, ctx):
        self._geom_cache = ctx
        self._geom_sig = sig

    def _toggle_monitor(self, on):
        if on:
            self._start_monitor()
        else:
            self._stop_monitor()

    def _start_monitor(self):
        if self._monitor_worker and self._monitor_worker.isRunning():
            return
        if self._worker and self._worker.isRunning():
            QtWidgets.QMessageBox.warning(
                self, "Busy", "Wait for the batch run to finish (or Abort) before monitoring.")
            self._loader.set_monitor_active(False); return
        src_cfg = self._loader.source_cfg()
        if src_cfg.get("type") != "tiff_glob" or not src_cfg.get("path"):
            QtWidgets.QMessageBox.warning(
                self, "Folder needed",
                "MONITOR watches a folder (optionally filtered by a filestem) "
                "for new TIFF frames — select a folder or a filestem pick as "
                "the data source (HDF5 sources and an explicit multi-file "
                "pick can't be monitored — there's no folder to watch).")
            self._loader.set_monitor_active(False); return
        try:
            spec = self._build_spec()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Calibration error", str(e))
            self._loader.set_monitor_active(False); return
        for sel in self._loader.has_pending_fields():
            QtWidgets.QMessageBox.warning(
                self, "Field not computed",
                f"'{sel.title()}' is enabled but not computed. Click 'Compute field' first.")
            self._loader.set_monitor_active(False); return

        kernel = self._kernel.currentData()
        corrections = self._corr_widget.build_corrections()
        variance_cfg = ({"error_model": self._err_model.currentText()}
                        if self._var_check.isChecked() else None)
        if variance_cfg and self._corr_widget.any_enabled():
            variance_cfg = None
        q_cfg = ({"QMin": self._q_min.value(), "QMax": self._q_max.value(),
                  "QBinSize": self._q_bin.value()} if self._q_check.isChecked() else None)
        _axctx = (float(spec.Lsd), float(spec.pxY), float(spec.Wavelength),
                  "Q" if q_cfg else "R")
        self._stack_view.set_axis_context(*_axctx)
        self._waterfall.set_axis_context(*_axctx)
        weighted = bool(self._azim.currentData())
        dark = self._loader.dark(); bright = self._loader.bright()
        background = self._loader.background(); bmode = self._loader.bright_mode()
        out_dir = self._out_ed.text().strip() or None
        fmts = self._fmt.checked_keys()

        sig = self._integration_signature(src_cfg, kernel, corrections, weighted)
        context = self._geom_cache if (sig == self._geom_sig and
                                       self._geom_cache is not None) else None

        self._monitor_worker = FolderMonitorWorker(
            spec, src_cfg["path"], self._loader.composite_mask(), kernel, corrections,
            variance_cfg, q_cfg=q_cfg, dark=dark, bright=bright, background=background,
            bright_mode=bmode, weighted=weighted, seen=set(self._integrated_fids),
            context=context, out_dir=out_dir, fmts=fmts, parent=self,
            im_trans=self._resolved_im_trans())
        self._monitor_worker.frame_done.connect(self._on_frame)
        self._monitor_worker.new_count.connect(self._on_monitor_count)
        self._monitor_worker.log_line.connect(self._log.append)
        self._monitor_worker.failed.connect(self._on_monitor_fail)
        self._monitor_worker.geom_ready.connect(lambda ctx, s=sig: self._cache_geom(s, ctx))
        self._view_tabs.setCurrentWidget(self._stack_view)
        self._log.append("─" * 40 + "\nMONITOR active — watching the folder for new frames…")
        self._monitor_worker.start()

    def _stop_monitor(self):
        w = self._monitor_worker
        if w and w.isRunning():
            w.requestInterruption()
            if not w.wait(3000):
                # Detach + orphan rather than terminate() (which can crash the app
                # if the thread is inside a native integration call).
                for s in (w.frame_done, w.new_count, w.status, w.log_line,
                          w.failed, w.geom_ready):
                    try:
                        s.disconnect()
                    except Exception:
                        pass
                self._orphans.append(w)
            self._log.append("[monitor] stopped.")
        self._monitor_worker = None

    def _on_monitor_count(self, n):
        self._prog_lbl.setText(f"Monitoring — {n} new frame(s) integrated")

    def _on_monitor_fail(self, msg):
        self._loader.set_monitor_active(False)
        self._monitor_worker = None
        show_error(self, "Monitor failed", msg, log=self._log, log_prefix="\n[monitor] ERROR:\n")

    # ── Drift correction ───────────────────────────────────────────

    def _fit_drift(self):
        """Parse the anchors JSON and fit the drift trajectory."""
        if self._drift_worker and self._drift_worker.isRunning():
            return
        anchor_path = self._drift_anchor_ed.text().strip()
        if not anchor_path:
            QtWidgets.QMessageBox.warning(self, "Missing", "Specify an anchor JSON file."); return
        from pathlib import Path as _Path
        import json as _json
        try:
            raw = _json.loads(_Path(anchor_path).read_text())
            # JSON keys are strings; convert to int
            anchors = {int(k): v for k, v in raw.items()}
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "JSON error", str(e)); return
        if len(anchors) < 2:
            QtWidgets.QMessageBox.warning(self, "Too few anchors",
                                           "Need at least 2 anchor frames."); return
        try:
            calib_result = self._calib_result
            if calib_result is None:
                raise RuntimeError("Run Tab 2 calibration first.")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Calibration missing", str(e)); return

        # Sample indices: span the anchor range
        idx_min = min(anchors); idx_max = max(anchors)
        sample_indices = list(range(idx_min, idx_max + 1))

        cfg = {
            "parametrization": self._drift_param.currentText(),
            "n_knots": self._drift_knots.value(),
            "bayesian_sigma": self._drift_bayesian.isChecked(),
        }
        self._drift_status_lbl.setText("Fitting…")
        self._drift_fit_btn.setEnabled(False)
        self._log.append("─" * 40 + f"\nFitting drift trajectory ({len(anchors)} anchors)…")
        self._drift_worker = DriftWorker(
            calib_result, anchors, sample_indices, cfg, parent=self)
        self._drift_worker.log_line.connect(self._log.append)
        self._drift_worker.finished.connect(self._on_drift_done)
        self._drift_worker.failed.connect(self._on_drift_fail)
        self._drift_worker.start()

    def _on_drift_done(self, traj):
        self._drift_traj = traj
        self._drift_fit_btn.setEnabled(True)
        Lsd_range = f"{traj.Lsd_t.min()/1000:.3f}–{traj.Lsd_t.max()/1000:.3f} mm"
        self._drift_status_lbl.setText(f"Trajectory ready: Lsd {Lsd_range}  ({len(traj.frame_indices)} knots)")
        self._log.append(f"[drift] trajectory fitted  Lsd {Lsd_range}")

    def _on_drift_fail(self, msg):
        self._drift_fit_btn.setEnabled(True)
        self._drift_status_lbl.setText("Fitting failed")
        show_error(self, "Drift fitting failed", msg, log=self._log, log_prefix="\n[drift] ERROR:\n")
