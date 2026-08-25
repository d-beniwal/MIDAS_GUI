"""Hydra (4-panel GE detector) page for the Calibrate tab.

``HydraCalibrationPage`` mirrors ``hydra_page.HydraViewerPage``'s
composition: a shared ``HydraLoaderPanel`` for data, one small
``HydraCalibPanelCard`` per GE panel (Transforms + seed + fitted result,
each genuinely independent per physical panel — see that module's
docstring), and shared "recipe" cards (Pipeline, Detector & Calibrant,
Threshold, Average frames, Refine parameters, Advanced) applied identically
to every panel's fit, since it's the same beam and the same choice of what
to refine for all 4.

Calibration for the panels currently loaded can run Sequentially (one
``CalibrationWorker`` at a time — full per-line log capture, safe) or in
Parallel (all workers started at once — see ``workers.CalibrationWorker``'s
``capture_stdout`` flag for why parallel runs skip fine-grained log capture).

Deliberately does NOT surface ``HydraLoaderPanel.projection_card()`` — the
single-detector Calibrate tab has no stack-projection feature either (it
only offers frame averaging), and ``HydraLoaderPanel.projected(n)`` returns
an *already* dark/bright/background-corrected frame, which would be
double-corrected if handed to ``CalibrationWorker`` (which expects a raw
frame and applies bright/background itself). Averaging frames instead
(the "Average frames" card below) mirrors the single-detector tab exactly.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
from PyQt5 import QtCore, QtWidgets

from midas_gui.constants import (
    CALIBRANTS, PIPELINES, DEFAULT_PIPELINE, DEFAULT_WAVELENGTH, DEFAULT_PIXEL_UM,
    DISTORTION_NAMES)
from midas_gui.helpers import (
    _fspin, _NoScrollSpinBox, _NoScrollComboBox, make_kedge_label, make_pixel_label,
    _load_image, _apply_im_trans, apply_field_corrections, average_field, source_kind,
    widgets_to_dict, apply_dict_to_widgets, _predict_ring_radii, refresh_combo_items)
from midas_gui.widgets import PickableImageViewer, LogPanel, CakeViewer
from midas_gui.hydra_widgets import HydraLoaderPanel, HydraDetectorToolbar, HydraProfileViewer
from midas_gui.hydra_calib_widgets import HydraCalibPanelCard
from midas_gui.workers import CalibrationWorker, IntegrationWorker
from midas_gui.dialogs import DistortionRefineDialog
from midas_gui import project
from midas_gui import style as S


class HydraCalibrationPage(QtWidgets.QWidget):
    pullFromViewer = QtCore.pyqtSignal()               # "← Data Viewer" clicked
    sendGeometryToViewer = QtCore.pyqtSignal(int, dict)  # panel_num, geometry
    panelCalibrationDone = QtCore.pyqtSignal(int, object)  # panel_num, AutoCalibrationResult

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cards: dict = {}          # panel_num -> HydraCalibPanelCard
        self._workers: dict = {}        # panel_num -> CalibrationWorker (running)
        self._int_workers: dict = {}    # panel_num -> IntegrationWorker (running)
        self._orphans: list = []        # aborted workers kept alive until they wind down
        self._pending_panels: list = []  # sequential-mode queue
        self._calib_cancelled = False
        self._dist_coeffs = set(DISTORTION_NAMES)
        self._last_dist_coeffs: Optional[set] = None
        self._active_card: Optional[HydraCalibPanelCard] = None
        self._disp_key = None
        self._last_cfgs: dict = {}      # panel_num -> cfg used for its last run (provenance)
        self._last_fields: dict = {}    # panel_num -> (dark, bright, background) arrays
        self._project_ctx: Optional[project.ProjectContext] = None
        self._build_ui()
        self._on_panel_changed(self._toolbar.current())

    def set_project_context(self, ctx: "project.ProjectContext"):
        self._project_ctx = ctx

    def refresh_calibrants(self) -> None:
        """Repopulate this page's shared Calibrant dropdown from the
        just-activated profile's constants.CALIBRANTS."""
        refresh_combo_items(self._cal, CALIBRANTS)

    # ── UI ────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6); root.setSpacing(0)
        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        split.setChildrenCollapsible(False); split.setHandleWidth(6)
        root.addWidget(split)

        # ── LEFT: "← Data Viewer" + the Hydra data loader ──
        left = QtWidgets.QWidget()
        ll = QtWidgets.QVBoxLayout(left); ll.setContentsMargins(0, 0, 0, 0); ll.setSpacing(4)
        self._from_view_btn = QtWidgets.QPushButton("← Data Viewer")
        self._from_view_btn.setToolTip(
            "Pull the Hydra panel data path and each panel's fitted geometry "
            "(BC/Lsd/tilts/transforms) from the Data Viewer's Hydra page.")
        self._from_view_btn.clicked.connect(self.pullFromViewer.emit)
        ll.addWidget(self._from_view_btn)
        self._loader = HydraLoaderPanel()
        self._loader.setMinimumWidth(200)
        self._loader.siblingsChanged.connect(self._on_siblings_changed)
        self._loader.frameChanged.connect(self._on_frame_changed)
        self._loader.fieldsChanged.connect(self._on_fields_changed)
        ll.addWidget(self._loader, 1)
        split.addWidget(left)

        # ── MIDDLE: shared "recipe" cards + per-panel card stack ──
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True); scroll.setMinimumWidth(260)
        inner = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(inner); lv.setContentsMargins(2, 2, 2, 2); lv.setSpacing(8)
        scroll.setWidget(inner)

        # Pipeline
        pipe = S.make_card("Pipeline")
        self._pipeline = _NoScrollComboBox()
        for label, key, enabled in PIPELINES:
            self._pipeline.addItem(label, key)
            if not enabled:
                self._pipeline.model().item(self._pipeline.count() - 1).setEnabled(False)
        _pi = self._pipeline.findData(DEFAULT_PIPELINE)
        if _pi >= 0 and self._pipeline.model().item(_pi).isEnabled():
            self._pipeline.setCurrentIndex(_pi)
        pipe.body.addWidget(self._pipeline)
        lv.addWidget(pipe)

        # Detector & Calibrant (shared — same beam/detector model for all 4 panels)
        det = S.make_card("Detector & Calibrant  (shared across ge1–ge4)")
        self._wl = _fspin(0.001, 10.0, 5, DEFAULT_WAVELENGTH, "Å")
        self._cal = _NoScrollComboBox(); self._cal.addItems(CALIBRANTS); self._cal.setMaximumWidth(150)
        det.body.addLayout(S.Form().row(
            (make_kedge_label(self._wl, "λ:"), self._wl), ("Calibrant:", self._cal)))
        self._pxY = _fspin(1.0, 5000.0, 2, DEFAULT_PIXEL_UM, "µm")
        self._pxZ_check = QtWidgets.QCheckBox("pxZ")
        self._pxZ_spin = _fspin(1.0, 5000.0, 2, DEFAULT_PIXEL_UM, "µm"); self._pxZ_spin.setEnabled(False)
        self._pxZ_check.toggled.connect(self._pxZ_spin.setEnabled)
        prow = QtWidgets.QHBoxLayout(); prow.setSpacing(4)
        prow.addWidget(self._pxY, 1); prow.addWidget(self._pxZ_check); prow.addWidget(self._pxZ_spin, 1)
        det.body.addLayout(S.Form().row(
            (make_pixel_label(self._pxY, "Pixel:", also=self._pxZ_spin), prow)))
        lv.addWidget(det)

        # Threshold (shared value; applied to whichever panel's own image is active/fit)
        thr = S.make_card("Threshold  (pixels below → 0, shared)")
        self._thr_check = QtWidgets.QCheckBox("Apply threshold to calibration image")
        thr.body.addWidget(self._thr_check)
        self._thr_min = _fspin(-1e9, 1e9, 1, 0.0)
        self._thr_max = _fspin(-1e9, 1e9, 1, 65535.0)
        thr.body.addLayout(S.Form().row(("slider min:", self._thr_min), ("max:", self._thr_max)))
        self._thr_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._thr_slider.setRange(0, 1000); self._thr_slider.setValue(0)
        self._thr_val = QtWidgets.QLabel("threshold = —")
        self._thr_val.setStyleSheet(f"color:{S.ACCENT};font-size:11px")
        srow = QtWidgets.QHBoxLayout(); srow.setSpacing(6)
        srow.addWidget(self._thr_slider, 1); srow.addWidget(self._thr_val)
        thr.body.addLayout(srow)
        for w in (self._thr_min, self._thr_max, self._thr_slider, self._thr_val):
            w.setEnabled(False)
        self._thr_check.toggled.connect(self._on_threshold_toggled)
        self._thr_slider.valueChanged.connect(self._on_threshold_changed)
        self._thr_min.valueChanged.connect(self._on_threshold_changed)
        self._thr_max.valueChanged.connect(self._on_threshold_changed)
        lv.addWidget(thr)

        # Average frames (shared range — panels are synchronized frames of one scan)
        avgc = S.make_card("Average frames  (shared range)")
        self._avg_check = QtWidgets.QCheckBox("Average frames into a single image")
        avgc.body.addWidget(self._avg_check)
        self._avg_start = _NoScrollSpinBox(); self._avg_start.setRange(0, 999999)
        self._avg_end = _NoScrollSpinBox(); self._avg_end.setRange(0, 999999)
        self._avg_end.setToolTip("Last frame (exclusive). 0 = all frames.")
        for w in (self._avg_start, self._avg_end):
            w.setEnabled(False)
        afm = S.Form(); afm.row(("start:", self._avg_start), ("end(0=all):", self._avg_end))
        avgc.body.addLayout(afm)
        self._avg_note = QtWidgets.QLabel("")
        self._avg_note.setStyleSheet("color:#9a9a9a;font-size:10px"); self._avg_note.setWordWrap(True)
        avgc.body.addWidget(self._avg_note)
        self._avg_card = avgc
        self._avg_check.toggled.connect(self._on_avg_toggled)
        for w in (self._avg_start, self._avg_end):
            w.valueChanged.connect(self._on_avg_changed)
        lv.addWidget(avgc)

        # Refine parameters (shared)
        refc = S.make_card("Refine parameters  (shared)")
        rfl = QtWidgets.QGridLayout(); rfl.setSpacing(4)
        self._ref_lsd = QtWidgets.QCheckBox("Lsd"); self._ref_lsd.setChecked(True)
        self._ref_bc = QtWidgets.QCheckBox("BC"); self._ref_bc.setChecked(True)
        self._ref_ty = QtWidgets.QCheckBox("ty"); self._ref_ty.setChecked(True)
        self._ref_tz = QtWidgets.QCheckBox("tz"); self._ref_tz.setChecked(True)
        self._ref_tx = QtWidgets.QCheckBox("tx")
        self._ref_wl = QtWidgets.QCheckBox("Wavelength")
        self._ref_dist = QtWidgets.QCheckBox("Distortion"); self._ref_dist.setChecked(True)
        self._build_rc = QtWidgets.QCheckBox("Residual map"); self._build_rc.setChecked(True)
        for i, w in enumerate((self._ref_lsd, self._ref_bc, self._ref_ty, self._ref_tz,
                               self._ref_tx, self._ref_wl)):
            rfl.addWidget(w, i // 2, i % 2)
        self._dist_btn = QtWidgets.QToolButton(); self._dist_btn.setText("…")
        self._dist_btn.setToolTip("Choose which distortion coefficients to refine.")
        self._dist_btn.clicked.connect(self._edit_distortion_coeffs)
        drow = QtWidgets.QHBoxLayout(); drow.setSpacing(4)
        drow.addWidget(self._ref_dist); drow.addWidget(self._dist_btn); drow.addStretch(1)
        rfl.addLayout(drow, 3, 0)
        rfl.addWidget(self._build_rc, 3, 1)
        self._ref_dist.toggled.connect(lambda _=0: self._update_dist_label())
        refc.body.addLayout(rfl)
        lv.addWidget(refc)
        self._update_dist_label()

        # Advanced (shared)
        grp_adv = QtWidgets.QGroupBox("Advanced")
        grp_adv.setCheckable(True); grp_adv.setChecked(False)
        av = QtWidgets.QVBoxLayout(grp_adv); av.setContentsMargins(8, 6, 8, 6); av.setSpacing(5)
        self._n_iter = _NoScrollSpinBox(); self._n_iter.setRange(1, 1_000_000); self._n_iter.setValue(4)
        self._lm_iter = _NoScrollSpinBox(); self._lm_iter.setRange(1, 1_000_000); self._lm_iter.setValue(200)
        self._device = _NoScrollComboBox(); self._device.addItems(["cpu", "cuda"])
        av.addLayout(S.Form().row(("E-M iters:", self._n_iter), ("LM iters:", self._lm_iter)))
        self._out_ed = QtWidgets.QLineEdit(); self._out_ed.setPlaceholderText("Output dir…")
        bou = QtWidgets.QPushButton("…"); bou.setFixedWidth(30)
        bou.clicked.connect(lambda: self._out_ed.setText(
            QtWidgets.QFileDialog.getExistingDirectory(self, "Output dir") or ""))
        outr = QtWidgets.QHBoxLayout(); outr.setSpacing(4); outr.addWidget(self._out_ed, 1); outr.addWidget(bou)
        av.addLayout(S.Form().row(("Device:", self._device)))
        av.addLayout(S.Form().row(("Output:", outr)))
        lv.addWidget(grp_adv)

        # Run controls
        run_card = S.make_card("Run  (Hydra: ge1–ge4, one recipe)")
        mode_row = QtWidgets.QHBoxLayout(); mode_row.setSpacing(6)
        mode_row.addWidget(S.LabelRight("Run mode:"))
        self._run_mode_combo = _NoScrollComboBox()
        self._run_mode_combo.addItem("Sequential", "sequential")
        self._run_mode_combo.addItem("Parallel", "parallel")
        self._run_mode_combo.setToolTip(
            "Sequential: one panel fit at a time — full per-line log capture.\n"
            "Parallel: all present panels fit at once — faster, but each panel's "
            "fine-grained progress prints go to the console rather than the Log "
            "tab (only start/finish/error lines appear there); a fit's stdout "
            "redirect is process-global, so it can't safely be shared across "
            "concurrent threads.")
        mode_row.addWidget(self._run_mode_combo); mode_row.addStretch(1)
        run_card.body.addLayout(mode_row)
        self._run_btn = S.primary_btn("Run Calibration")
        self._run_btn.clicked.connect(self._run_all)
        self._abort_btn = QtWidgets.QPushButton("Abort")
        self._abort_btn.setEnabled(False)
        self._abort_btn.clicked.connect(self._abort_all)
        run_row = QtWidgets.QHBoxLayout(); run_row.setSpacing(6)
        run_row.addWidget(self._run_btn, 1); run_row.addWidget(self._abort_btn)
        run_card.body.addLayout(run_row)
        self._prog = QtWidgets.QProgressBar(); self._prog.setRange(0, 0); self._prog.setVisible(False)
        run_card.body.addWidget(self._prog)
        lv.addWidget(run_card)

        # Per-panel: Transforms + Initial seed, switched with the active panel
        self._card_stack = QtWidgets.QStackedWidget()
        for n in (1, 2, 3, 4):
            card = HydraCalibPanelCard(n)
            card.set_log_fn(self._log_append_raw)
            card.imTransChanged.connect(lambda n=n: self._on_card_transform_changed(n))
            card.calibFileLoaded.connect(self._on_card_calib_file_loaded)
            card.sendToViewer.connect(self.sendGeometryToViewer.emit)
            card._manual_seed_check.toggled.connect(
                lambda checked, n=n: self._sync_seed_checkbox("_manual_seed_check", n, checked))
            card._feedback_check.toggled.connect(
                lambda checked, n=n: self._sync_seed_checkbox("_feedback_check", n, checked))
            self._cards[n] = card
            self._card_stack.addWidget(card)
        lv.addWidget(self._card_stack)
        lv.addStretch(1)
        split.addWidget(scroll)

        # ── RIGHT: image viewer + bottom tabs ──
        right = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self._img_view = PickableImageViewer()
        self._toolbar = HydraDetectorToolbar(include_composite=False)
        self._toolbar.panelChanged.connect(self._on_panel_changed)
        tb = self._img_view._toolbar_layout
        tb.addWidget(self._toolbar)
        self._show_rings_check = QtWidgets.QCheckBox("Show rings"); self._show_rings_check.setChecked(True)
        self._show_rings_check.toggled.connect(
            lambda c: self._active_card and self._active_card.set_show_rings(c))
        tb.addWidget(self._show_rings_check)
        self._corrected_check = QtWidgets.QCheckBox("Corrected")
        self._corrected_check.setToolTip("Redraw the active panel's rings reflecting its fitted tilt.")
        self._corrected_check.toggled.connect(
            lambda c: self._active_card and self._active_card.set_corrected(c))
        tb.addWidget(self._corrected_check)
        right.addWidget(self._img_view)

        bot = QtWidgets.QTabWidget()
        self._profile_view = HydraProfileViewer()
        ptb = self._profile_view._toolbar_layout
        self._cal_r_bin = _fspin(0.1, 20.0, 2, 1.0, "px"); self._cal_r_bin.setFixedWidth(78)
        self._cal_eta_bin = _fspin(0.5, 30.0, 1, 5.0, "°"); self._cal_eta_bin.setFixedWidth(64)
        self._cal_azim = _NoScrollComboBox()
        self._cal_azim.addItem("Pixel-weighted", True)
        self._cal_azim.addItem("η-bin mean", False)
        reint_btn = QtWidgets.QPushButton("Re-integrate"); reint_btn.clicked.connect(self._reintegrate_all)
        insert_at = ptb.count() - 1
        ptb.insertWidget(insert_at, reint_btn)
        ptb.insertWidget(insert_at, self._cal_azim)
        ptb.insertWidget(insert_at, self._cal_eta_bin)
        ptb.insertWidget(insert_at, QtWidgets.QLabel("η:"))
        ptb.insertWidget(insert_at, self._cal_r_bin)
        ptb.insertWidget(insert_at, QtWidgets.QLabel("  R bin:"))
        bot.addTab(self._profile_view, "Radial Profile")

        self._cake_views: dict = {}     # panel_num -> CakeViewer
        self._cake_stack = QtWidgets.QStackedWidget()
        for n in (1, 2, 3, 4):
            cake_view = CakeViewer()
            self._cake_views[n] = cake_view
            self._cake_stack.addWidget(cake_view)
        bot.addTab(self._cake_stack, "Eta vs R Cake")

        self._resid_stack = QtWidgets.QStackedWidget()
        for n in (1, 2, 3, 4):
            self._resid_stack.addWidget(self._cards[n].residual_chart)
        bot.addTab(self._resid_stack, "Ring Residuals")

        self._results_stack = QtWidgets.QStackedWidget()
        for n in (1, 2, 3, 4):
            self._results_stack.addWidget(self._cards[n].results_widget)
        bot.addTab(self._results_stack, "Results")

        self._log = LogPanel()
        bot.addTab(self._log, "Log")
        self._log.setMaximumHeight(16_777_215)
        right.addWidget(bot)
        right.setChildrenCollapsible(False)
        right.setStretchFactor(0, 3); right.setStretchFactor(1, 1)
        right.setSizes([680, 320])
        self._bot_tabs = bot
        right.setMinimumWidth(320)
        split.addWidget(right)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 0); split.setStretchFactor(2, 1)
        split.setSizes([286, 361, 950])

    # ── Loader signal handlers ──────────────────────────────────────

    def _on_siblings_changed(self, siblings: dict):
        self._toolbar.set_available(siblings.keys())
        self._sync_avg_controls()
        detected = self._loader.detected_geometry()
        if "wavelength_A" in detected:
            self._wl.setValue(float(detected["wavelength_A"]))
        if "pxY" in detected:
            self._pxY.setValue(float(detected["pxY"]))
        self._refresh_display()

    def _on_frame_changed(self, _idx: int):
        self._refresh_display()

    def _on_fields_changed(self):
        self._refresh_display()

    def _on_panel_changed(self, key: str):
        n = int(key[2])
        if self._active_card is not None:
            self._active_card.bind_viewer(None)
        self._card_stack.setCurrentWidget(self._cards[n])
        self._resid_stack.setCurrentWidget(self._cards[n].residual_chart)
        self._results_stack.setCurrentWidget(self._cards[n].results_widget)
        self._cake_stack.setCurrentWidget(self._cake_views[n])
        self._active_card = self._cards[n]
        self._active_card.bind_viewer(self._img_view)
        for chk, getter in ((self._show_rings_check, self._active_card.show_rings_checked),
                            (self._corrected_check, self._active_card.corrected_checked)):
            chk.blockSignals(True); chk.setChecked(getter()); chk.blockSignals(False)
        self._refresh_display()

    def _on_card_transform_changed(self, n: int):
        if self._active_card is self._cards.get(n):
            self._refresh_display()

    def _on_card_calib_file_loaded(self, g: dict):
        if g.get("wavelength_A"):
            self._wl.setValue(float(g["wavelength_A"]))
        if g.get("pxY"):
            self._pxY.setValue(float(g["pxY"]))

    def _sync_seed_checkbox(self, attr: str, src_panel: int, checked: bool):
        """"Use manual seed" / "Feed result back to seed" are one shared
        choice across all 4 GE panels (only the seed VALUES — BC/Lsd/tilts —
        stay independent per panel), so mirror a change on one panel's
        checkbox onto the other three without re-triggering their own
        ``toggled`` handlers."""
        for n, card in self._cards.items():
            if n == src_panel:
                continue
            cb = getattr(card, attr)
            if cb.isChecked() != checked:
                cb.blockSignals(True)
                cb.setChecked(checked)
                cb.blockSignals(False)

    # ── Per-panel frame sourcing ─────────────────────────────────────

    def _avg_index_range(self, n_frames: int) -> tuple:
        """(start, end_inclusive) for ``helpers.average_field``, translated
        from the start/end(0=all, exclusive) spinbox convention shared with
        the single-detector Calibrate tab's Average-frames card."""
        end = n_frames if self._avg_end.value() <= 0 else min(self._avg_end.value(), n_frames)
        start = max(0, self._avg_start.value())
        return start, max(start, end - 1)

    def _panel_raw_image(self, n: int) -> Optional[np.ndarray]:
        """Raw (uncorrected, untransformed) source image for panel ``n`` —
        averaged over a frame range if enabled, else the current frame.
        Deliberately raw: ``CalibrationWorker`` applies bright/background/
        transforms itself (see module docstring)."""
        path = self._loader.siblings().get(n)
        if path is None:
            return None
        ds = self._loader.dataset()
        try:
            if self._avg_check.isChecked() and self._loader.n_frames() > 1:
                start, end = self._avg_index_range(self._loader.n_frames())
                return average_field(source_kind(path), path, ds, start, end)
            return _load_image(path, ds, self._loader.frame_index())
        except Exception:
            return None

    def _threshold_value(self) -> float:
        lo, hi = self._thr_min.value(), self._thr_max.value()
        if hi <= lo:
            return hi
        return lo + (self._thr_slider.value() / 1000.0) * (hi - lo)

    def _update_threshold_label(self):
        self._thr_val.setText(f"< {self._threshold_value():.4g} → 0")

    def _calib_image_for(self, img):
        if img is None:
            return None
        if self._thr_check.isChecked():
            thr = self._threshold_value()
            out = img.copy()
            out[img < thr] = 0.0
            return out
        return img

    def _sync_avg_controls(self):
        n = self._loader.n_frames()
        multi = n > 1
        self._avg_card.setEnabled(multi)
        if not multi and self._avg_check.isChecked():
            self._avg_check.blockSignals(True); self._avg_check.setChecked(False)
            self._avg_check.blockSignals(False)
        hi = max(0, n)
        for w in (self._avg_start, self._avg_end):
            w.blockSignals(True); w.setRange(0, hi); w.blockSignals(False)
        self._update_avg_note()

    def _update_avg_note(self):
        n = self._loader.n_frames()
        if n <= 1:
            self._avg_note.setText("Single-frame source — averaging unavailable.")
            return
        start = self._avg_start.value(); end = self._avg_end.value() or n; end = min(end, n)
        cnt = len(range(max(0, start), end))
        self._avg_note.setText(f"{cnt} of {n} frames averaged (start={start}, end={end}).")

    def _on_threshold_toggled(self, on: bool):
        for w in (self._thr_min, self._thr_max, self._thr_slider, self._thr_val):
            w.setEnabled(on)
        self._update_threshold_label()
        self._refresh_display()

    def _on_threshold_changed(self, *_):
        self._update_threshold_label()
        if self._thr_check.isChecked():
            self._refresh_display()

    def _on_avg_toggled(self, on):
        for w in (self._avg_start, self._avg_end):
            w.setEnabled(on)
        self._update_avg_note()
        self._refresh_display()

    def _on_avg_changed(self, *_):
        self._update_avg_note()
        if self._avg_check.isChecked():
            self._refresh_display()

    def _refresh_display(self):
        if self._active_card is None:
            return
        n = self._active_card.panel_number
        raw = self._panel_raw_image(n)
        if raw is None:
            return
        lo, hi = float(np.nanmin(raw)), float(np.nanmax(raw))
        for w in (self._thr_min, self._thr_max, self._thr_slider):
            w.blockSignals(True)
        self._thr_min.setValue(max(0.0, lo)); self._thr_max.setValue(hi)
        for w in (self._thr_min, self._thr_max, self._thr_slider):
            w.blockSignals(False)
        self._update_threshold_label()
        img = self._calib_image_for(raw)
        img = apply_field_corrections(
            img, dark=self._loader.dark(n), bright=self._loader.bright(n),
            bright_mode=self._loader.bright_mode(), background=self._loader.background(n))
        img = _apply_im_trans(img, tuple(self._active_card.im_trans_codes()))
        fresh = (self._disp_key != (img.shape, n))
        self._disp_key = (img.shape, n)
        self._img_view.set_image(img, autorange=fresh, reset_levels=fresh)

    # ── Distortion coefficient selection ──────────────────────────

    def _edit_distortion_coeffs(self):
        dlg = DistortionRefineDialog(self._dist_coeffs, self)
        if dlg.exec_():
            self._dist_coeffs = dlg.selected()
            if self._dist_coeffs and not self._ref_dist.isChecked():
                self._ref_dist.setChecked(True)
            self._update_dist_label()

    def _update_dist_label(self):
        n = len(self._dist_coeffs) if self._ref_dist.isChecked() else 0
        self._ref_dist.setText(f"Distortion ({n}/15)")

    # ── Run ────────────────────────────────────────────────────────

    def _run_mode(self) -> str:
        return self._run_mode_combo.currentData() or "sequential"

    def _refine_flags(self) -> dict:
        coeffs = set(self._dist_coeffs) if self._ref_dist.isChecked() else set()
        return {
            "Lsd": self._ref_lsd.isChecked(), "BC": self._ref_bc.isChecked(),
            "ty": self._ref_ty.isChecked(), "tz": self._ref_tz.isChecked(),
            "tx": self._ref_tx.isChecked(), "Wavelength": self._ref_wl.isChecked(),
            "Distortion": self._ref_dist.isChecked(), "distortion_coeffs": coeffs,
        }

    def _build_cfg(self, card: HydraCalibPanelCard) -> dict:
        cfg = {
            "wavelength": self._wl.value(),
            "pxY": self._pxY.value(),
            "pxZ": self._pxZ_spin.value() if self._pxZ_check.isChecked() else None,
            "calibrant": self._cal.currentText(),
            "refine": self._refine_flags(),
            "n_iter": self._n_iter.value(),
            "lm_max_iter": self._lm_iter.value(),
            "device": self._device.currentText(),
            "build_residual_corr": self._build_rc.isChecked(),
            "output_dir": self._out_ed.text().strip() or None,
            "im_trans": card.im_trans_codes(),
            "mask": None,
        }
        seed = card.manual_seed()
        if seed is not None:
            seed = dict(seed); seed["distortion"] = {}
            cfg["manual_seed"] = seed
        return cfg

    def _log_append_raw(self, line: str):
        self._log.append(line)

    def _run_all(self):
        siblings = self._loader.siblings()
        if not siblings:
            QtWidgets.QMessageBox.warning(self, "No data", "Load Hydra panel data first.")
            return
        if self._workers:
            return
        self._orphans = [o for o in self._orphans if o.isRunning()]
        self._calib_cancelled = False
        self._last_dist_coeffs = set(self._dist_coeffs) if self._ref_dist.isChecked() else set()
        self._run_btn.setEnabled(False); self._abort_btn.setEnabled(True)
        self._prog.setVisible(True)
        self._bot_tabs.setCurrentWidget(self._log)
        mode = self._run_mode()
        self._log.append("─" * 40 + f"\nStarting Hydra calibration ({mode}, "
                         f"{self._pipeline.currentData()})…")
        panels = sorted(siblings)
        self._pending_panels = list(panels)
        if mode == "parallel":
            pending, self._pending_panels = list(self._pending_panels), []
            for n in pending:
                self._start_panel_worker(n, capture_stdout=False)
        else:
            self._start_next_sequential()

    def _start_next_sequential(self):
        if not self._pending_panels:
            self._maybe_finish_run()
            return
        n = self._pending_panels.pop(0)
        self._start_panel_worker(n, capture_stdout=True)

    def _start_panel_worker(self, n: int, capture_stdout: bool):
        card = self._cards[n]
        raw = self._panel_raw_image(n)
        if raw is None:
            self._log.append(f"[ge{n}] no data — skipped")
            if self._run_mode() == "sequential":
                self._start_next_sequential()
            return
        image = self._calib_image_for(raw)
        dark = self._loader.dark(n)
        bright = self._loader.bright(n)
        background = self._loader.background(n)
        bright_mode = self._loader.bright_mode()
        cfg = self._build_cfg(card)
        self._last_cfgs[n] = dict(cfg)
        self._last_fields[n] = (dark, bright, background)
        mode = self._pipeline.currentData()
        worker = CalibrationWorker(
            mode, image, dark, cfg, parent=self, bright=bright, background=background,
            bright_mode=bright_mode, capture_stdout=capture_stdout)
        worker.log_line.connect(lambda line, n=n: self._log.append(f"[ge{n}] {line}"))
        worker.finished.connect(lambda result, n=n: self._on_panel_done(n, result))
        worker.failed.connect(lambda msg, n=n: self._on_panel_fail(n, msg))
        self._workers[n] = worker
        self._log.append(f"[ge{n}] starting…")
        worker.start()

    def _on_panel_done(self, n: int, result):
        if self._calib_cancelled:
            return
        card = self._cards[n]
        result.im_trans = card.im_trans_codes()
        result._calibrant_name = self._cal.currentText()
        card.on_result(result)
        self._log.append(f"[ge{n}] done — Lsd={result.Lsd/1000:.3f} mm")
        self._log_to_project(n, result)
        self._run_integration(n, result)
        self.panelCalibrationDone.emit(n, result)
        self._workers.pop(n, None)
        if self._run_mode() == "sequential":
            self._start_next_sequential()
        self._maybe_finish_run()

    def _log_to_project(self, n: int, result):
        if not self._project_ctx or not self._project_ctx.path:
            return
        dark, bright, background = self._last_fields.get(n, (None, None, None))
        siblings = self._loader.siblings()
        loader_state = {
            "path": siblings.get(n), "dataset": self._loader.dataset(),
            "frame_index": self._loader.frame_index(),
        }
        try:
            ref = project.append_calibration_attempt(
                self._project_ctx.path, f"ge{n}",
                cfg=self._last_cfgs.get(n, {}), result=result,
                loader_state=loader_state,
                dark=dark, bright=bright, background=background)
            result._project_attempt_ref = ref
            self._log.append(f"[ge{n}] logged to project: {ref}")
        except Exception:
            import traceback as _tb
            self._log.append(f"[ge{n}] could not log to project file:\n" + _tb.format_exc()[:400])

    def _on_panel_fail(self, n: int, msg: str):
        if self._calib_cancelled:
            return
        self._log.append(f"[ge{n}] ERROR:\n{msg[:400]}")
        self._workers.pop(n, None)
        if self._run_mode() == "sequential":
            self._start_next_sequential()
        self._maybe_finish_run()

    def _maybe_finish_run(self):
        if self._workers or self._pending_panels:
            return
        self._run_btn.setEnabled(True); self._abort_btn.setEnabled(False)
        self._prog.setVisible(False)
        self._log.append("Hydra calibration run complete.")

    def _abort_all(self):
        if not self._workers:
            return
        self._calib_cancelled = True
        for n, w in list(self._workers.items()):
            for sig in (w.log_line, w.finished, w.failed):
                try:
                    sig.disconnect()
                except Exception:
                    pass
            w.requestInterruption()
            self._orphans.append(w)
        self._workers.clear()
        self._pending_panels = []
        self._run_btn.setEnabled(True); self._abort_btn.setEnabled(False)
        self._prog.setVisible(False)
        self._log.append("Hydra calibration aborted — a background thread per panel "
                         "may still be winding down.")

    # ── Integration / residual chart ───────────────────────────────

    def _run_integration(self, n: int, result):
        image = self._calib_image_for(self._panel_raw_image(n))
        if image is None:
            return
        if self._int_workers.get(n) is not None and self._int_workers[n].isRunning():
            return
        card = self._cards[n]
        im_trans = tuple(card.im_trans_codes())
        w = IntegrationWorker(
            result, image, self._loader.dark(n), im_trans,
            r_bin=self._cal_r_bin.value(), eta_bin=self._cal_eta_bin.value(),
            mask=None, parent=self, bright=self._loader.bright(n),
            background=self._loader.background(n), bright_mode=self._loader.bright_mode(),
            weighted=bool(self._cal_azim.currentData()))
        w.finished.connect(lambda data, n=n, result=result: self._on_int_done(n, result, data))
        w.failed.connect(lambda m, n=n: self._log.append(f"[ge{n}] integration error: {m[:200]}"))
        self._int_workers[n] = w
        w.start()

    def _reintegrate_all(self):
        for n, card in self._cards.items():
            if card.result is not None:
                self._run_integration(n, card.result)

    def _on_int_done(self, n: int, result, data: dict):
        self._profile_view.set_curve(
            f"ge{n}", data["r_axis_px"], data["profile"],
            lsd_um=data["lsd_um"], px_um=data["px_um"], wavelength_A=data["wavelength_A"])
        radii = _predict_ring_radii(result)
        self._cards[n].residual_chart.set_data(data["r_axis_px"], data["profile"], radii)
        if data.get("cake_2d") is not None:
            self._cake_views[n].set_cake(data["cake_2d"], data["r_axis_px"], data["eta_axis_deg"])
        self._int_workers.pop(n, None)

    # ── Import from Data Viewer ──────────────────────────────────────

    def import_from_viewer(self, data: dict):
        data = data or {}
        anchor = data.get("anchor_path")
        if anchor:
            self._loader.set_path(anchor)
        for n_key, g in (data.get("geometries") or {}).items():
            n = int(n_key)
            card = self._cards.get(n)
            if card is None or not g:
                continue
            card.seed_from_geometry(g)
            if g.get("wavelength_A"):
                self._wl.setValue(float(g["wavelength_A"]))
            if g.get("pxY"):
                self._pxY.setValue(float(g["pxY"]))
        self._log.append("Geometry imported from Data Viewer (Hydra).")

    # ── GUI state ────────────────────────────────────────────────────

    def _state_widgets(self) -> dict:
        return {
            "pipeline": self._pipeline, "wl": self._wl, "cal": self._cal,
            "pxY": self._pxY, "pxZ_check": self._pxZ_check, "pxZ_spin": self._pxZ_spin,
            "thr_check": self._thr_check, "thr_min": self._thr_min, "thr_max": self._thr_max,
            "avg_check": self._avg_check, "avg_start": self._avg_start, "avg_end": self._avg_end,
            "ref_lsd": self._ref_lsd, "ref_bc": self._ref_bc, "ref_ty": self._ref_ty,
            "ref_tz": self._ref_tz, "ref_tx": self._ref_tx, "ref_wl": self._ref_wl,
            "ref_dist": self._ref_dist, "build_rc": self._build_rc,
            "n_iter": self._n_iter, "lm_iter": self._lm_iter, "device": self._device,
            "out_ed": self._out_ed, "run_mode": self._run_mode_combo,
            "cal_r_bin": self._cal_r_bin, "cal_eta_bin": self._cal_eta_bin,
            "cal_azim": self._cal_azim,
        }

    def get_state(self) -> dict:
        cards = {}
        for n, card in self._cards.items():
            fields = widgets_to_dict(card.state_widgets())
            fields["show_rings"] = card.show_rings_checked()
            fields["corrected"] = card.corrected_checked()
            cards[n] = fields
        return {
            "anchor_path": self._loader.current_path(),
            "active_panel": self._toolbar.current(),
            "fields": widgets_to_dict(self._state_widgets()),
            "cards": cards,
        }

    def set_state(self, state: dict):
        if not state:
            return
        apply_dict_to_widgets(self._state_widgets(), state.get("fields", {}))
        for n_key, fields in (state.get("cards") or {}).items():
            card = self._cards.get(int(n_key))
            if card is None:
                continue
            apply_dict_to_widgets(card.state_widgets(), fields)
            if "show_rings" in fields:
                card.set_show_rings(bool(fields["show_rings"]))
            if "corrected" in fields:
                card.set_corrected(bool(fields["corrected"]))
        anchor = state.get("anchor_path")
        if anchor and Path(anchor).exists():
            self._loader.set_path(anchor)
        panel = state.get("active_panel")
        if panel:
            self._toolbar.set_current(panel)
