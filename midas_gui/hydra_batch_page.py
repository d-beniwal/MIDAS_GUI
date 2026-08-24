"""Hydra (4-panel GE detector) page for the Batch Integrate tab.

``HydraBatchPage`` mirrors ``hydra_calib_page.HydraCalibrationPage``'s
composition: a shared ``HydraLoaderPanel`` (in streaming mode) for data, one
small ``HydraBatchPanelCard`` per GE panel (its own calibration source +
values + progress — each panel is integrated with its own independently
fitted geometry), and shared "recipe" cards (Integration, Corrections,
Monitor normalisation, Output) applied identically to every panel's run,
since it's the same integration settings for all 4.

Runs currently-present panels Sequentially (one ``BatchWorker`` at a time)
or in Parallel (all workers started at once) — ``BatchWorker`` never touches
process-global state (it logs via the ``log_line`` signal only, unlike
``CalibrationWorker``'s stdout redirect), so no ``capture_stdout``-style flag
is needed for safe concurrent runs.

Deliberately has no Drift-correction or live-MONITOR (folder-watch) support
for Hydra mode in this first pass — both exist on the single-detector Batch
tab and can be added later if needed; see ``.context/DECISIONS.md``.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PyQt5 import QtCore, QtWidgets

from midas_gui.constants import (KERNELS, OUTPUT_FORMATS, ERROR_MODELS,
                                 DEFAULT_KERNEL, DEFAULT_OUTPUT_FORMAT, DEFAULT_ERROR_MODEL)
from midas_gui.helpers import (
    _fspin, _browse, _NoScrollComboBox,
    widgets_to_dict, apply_dict_to_widgets)
from midas_gui.widgets import LogPanel, CorrectionFlagsWidget, WaterfallViewer, StackedProfileViewer
from midas_gui.hydra_widgets import HydraLoaderPanel, HydraDetectorToolbar
from midas_gui.hydra_batch_widgets import HydraBatchPanelCard
from midas_gui.workers import BatchWorker
from midas_gui import project
from midas_gui import style as S


class _PanelViewerPair(QtWidgets.QWidget):
    """One panel's own Waterfall/Stacked-profiles tab pair — a small copy of
    the single-detector Batch tab's RIGHT-pane viewer, one instance per GE
    panel so each panel's results display independently."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lv = QtWidgets.QVBoxLayout(self)
        lv.setContentsMargins(0, 0, 0, 0)
        tabs = QtWidgets.QTabWidget()
        self.waterfall = WaterfallViewer()
        self.stack_view = StackedProfileViewer()
        tabs.addTab(self.waterfall, "Waterfall")
        tabs.addTab(self.stack_view, "Stacked profiles")
        lv.addWidget(tabs)
        self.wf_started = False


class HydraBatchPage(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cards: dict = {}           # panel_num -> HydraBatchPanelCard
        self._viewer_pairs: dict = {}    # panel_num -> _PanelViewerPair
        self._workers: dict = {}         # panel_num -> BatchWorker (running)
        self._orphans: list = []         # aborted workers kept alive until they wind down
        self._pending_panels: list = []  # sequential-mode queue
        self._run_cancelled = False
        self._integrated_fids: dict = {}  # panel_num -> set of frame ids
        self._geom_cache: dict = {}      # panel_num -> cached integration context
        self._geom_sig: dict = {}        # panel_num -> signature the cache was built for
        self._last_run_inputs: dict = {}  # panel_num -> JSON-safe run inputs (provenance)
        self._last_run_fields: dict = {}  # panel_num -> {mask,dark,bright,background} arrays
        self._project_ctx = None
        self._build_ui()
        self._on_panel_changed(self._toolbar.current())

    def set_project_context(self, ctx):
        self._project_ctx = ctx

    # ── UI ────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6); root.setSpacing(0)
        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        split.setChildrenCollapsible(False); split.setHandleWidth(6)
        root.addWidget(split)

        # ── LEFT: the Hydra data loader (streaming: frame range/stride +
        #    per-panel dark/bright/background/mask) ──
        self._loader = HydraLoaderPanel(mode="stream")
        self._loader.setMinimumWidth(200)
        self._loader.siblingsChanged.connect(self._on_siblings_changed)
        split.addWidget(self._loader)

        # ── MIDDLE: shared "recipe" cards + per-panel card stack ──
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True); scroll.setMinimumWidth(260)
        inner = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(inner); lv.setContentsMargins(2, 2, 2, 2); lv.setSpacing(8)
        scroll.setWidget(inner)

        # Integration (shared)
        integ = S.make_card("Integration  (shared across ge1–ge4)")
        self._kernel = _NoScrollComboBox()
        for label, key in KERNELS.items():
            self._kernel.addItem(label, key)
        _ki = self._kernel.findData(DEFAULT_KERNEL)
        if _ki >= 0:
            self._kernel.setCurrentIndex(_ki)
        self._r_bin = _fspin(0.1, 20.0, 2, 1.0, "px")
        self._e_bin = _fspin(0.5, 30.0, 1, 5.0, "°")
        self._azim = _NoScrollComboBox()
        self._azim.addItem("Pixel-weighted", True)
        self._azim.addItem("η-bin mean (legacy)", False)
        intf = S.Form()
        intf.row(("Kernel:", self._kernel))
        intf.row(("R bin:", self._r_bin), ("η bin:", self._e_bin))
        intf.row(("Azim. avg:", self._azim))
        integ.body.addLayout(intf)
        self._var_check = QtWidgets.QCheckBox("Per-bin variance (σ)")
        self._var_check.setToolTip(
            "Compute per-bin σ via the chosen error model.\n"
            "Mutually exclusive with corrections (corrections win; σ→√I).")
        self._err_model = _NoScrollComboBox(); self._err_model.addItems(ERROR_MODELS)
        self._err_model.setEnabled(False)
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

        # Corrections (shared)
        self._corr_widget = CorrectionFlagsWidget()
        lv.addWidget(self._corr_widget)

        # Monitor normalisation (shared — a property of the beam/scan, not the panel)
        mon = S.make_card("Monitor normalisation (optional, shared)")
        self._mon_ed = QtWidgets.QLineEdit()
        self._mon_ed.setPlaceholderText("monitor.txt  (one value per line)")
        monr = QtWidgets.QHBoxLayout(); monr.setSpacing(4); monr.addWidget(self._mon_ed, 1)
        bmon = QtWidgets.QPushButton("…"); bmon.setFixedWidth(30)
        bmon.clicked.connect(lambda: self._mon_ed.setText(
            _browse(self, "Open monitor file", "Text (*.txt *.dat *.csv);;All (*)") or ""))
        monr.addWidget(bmon)
        mon.body.addLayout(monr)
        lv.addWidget(mon)

        # Output (shared base dir — each panel writes to its own ge{n}/ subfolder)
        out = S.make_card("Output  (shared — each panel writes to its own ge{n}/ subfolder)")
        self._out_ed = QtWidgets.QLineEdit(); self._out_ed.setPlaceholderText("Output directory…")
        orow = QtWidgets.QHBoxLayout(); orow.setSpacing(4); orow.addWidget(self._out_ed, 1)
        bou = QtWidgets.QPushButton("…"); bou.setFixedWidth(30)
        bou.clicked.connect(lambda: self._out_ed.setText(
            QtWidgets.QFileDialog.getExistingDirectory(self, "Output directory") or "")); orow.addWidget(bou)
        out.body.addLayout(S.Form().row(("Folder:", orow)))
        self._fmt = _NoScrollComboBox()
        for label in OUTPUT_FORMATS:
            self._fmt.addItem(label, OUTPUT_FORMATS[label])
        _fi = self._fmt.findData(DEFAULT_OUTPUT_FORMAT)
        if _fi >= 0:
            self._fmt.setCurrentIndex(_fi)
        out.body.addLayout(S.Form().row(("Format:", self._fmt)))
        lv.addWidget(out)

        # Run controls
        run_card = S.make_card("Run  (Hydra: ge1–ge4, one recipe)")
        mode_row = QtWidgets.QHBoxLayout(); mode_row.setSpacing(6)
        mode_row.addWidget(S.LabelRight("Run mode:"))
        self._run_mode_combo = _NoScrollComboBox()
        self._run_mode_combo.addItem("Sequential", "sequential")
        self._run_mode_combo.addItem("Parallel", "parallel")
        self._run_mode_combo.setToolTip(
            "Sequential: one panel integrated at a time.\n"
            "Parallel: all present panels integrated at once — faster, but "
            "shares CPU/GPU across concurrent runs.")
        mode_row.addWidget(self._run_mode_combo); mode_row.addStretch(1)
        run_card.body.addLayout(mode_row)
        self._run_btn = S.primary_btn("Start Integration")
        self._run_btn.clicked.connect(self._run_all)
        self._abort_btn = QtWidgets.QPushButton("Abort")
        self._abort_btn.setEnabled(False)
        self._abort_btn.setToolTip("Stop each running panel after its current frame, "
                                   "keeping frames already integrated.")
        self._abort_btn.clicked.connect(self._abort_all)
        run_row = QtWidgets.QHBoxLayout(); run_row.setSpacing(6)
        run_row.addWidget(self._run_btn, 1); run_row.addWidget(self._abort_btn)
        run_card.body.addLayout(run_row)
        self._prog = QtWidgets.QProgressBar(); self._prog.setRange(0, 0); self._prog.setVisible(False)
        run_card.body.addWidget(self._prog)
        lv.addWidget(run_card)

        # Per-panel toggle + card stack (calibration source/values/progress)
        self._toolbar = HydraDetectorToolbar(include_composite=False)
        self._toolbar.panelChanged.connect(self._on_panel_changed)
        lv.addWidget(self._toolbar)
        self._card_stack = QtWidgets.QStackedWidget()
        for n in (1, 2, 3, 4):
            card = HydraBatchPanelCard(n)
            self._cards[n] = card
            self._card_stack.addWidget(card)
        lv.addWidget(self._card_stack)
        lv.addStretch(1)
        split.addWidget(scroll)

        # ── RIGHT: per-panel Waterfall/Stacked-profiles, switched with the
        #    same active-panel toggle, + a shared Log ──
        right = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self._viewer_stack = QtWidgets.QStackedWidget()
        for n in (1, 2, 3, 4):
            pair = _PanelViewerPair()
            self._viewer_pairs[n] = pair
            self._viewer_stack.addWidget(pair)
        right.addWidget(self._viewer_stack)
        self._log = LogPanel()
        self._log.setMaximumHeight(16_777_215)
        right.addWidget(self._log)
        right.setStretchFactor(0, 4); right.setStretchFactor(1, 1)
        right.setMinimumWidth(320)
        split.addWidget(right)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 0); split.setStretchFactor(2, 1)
        split.setSizes([286, 361, 950])

    # ── Loader signal handlers ──────────────────────────────────────

    def _on_siblings_changed(self, siblings: dict):
        self._toolbar.set_available(siblings.keys())

    def _on_panel_changed(self, key: str):
        n = int(key[2])
        self._card_stack.setCurrentWidget(self._cards[n])
        self._viewer_stack.setCurrentWidget(self._viewer_pairs[n])

    # ── Run ────────────────────────────────────────────────────────

    def _run_mode(self) -> str:
        return self._run_mode_combo.currentData() or "sequential"

    def _integration_signature(self, n, src_cfg, kernel, corrections, weighted, mask):
        """Signature identifying a reusable detector map for panel ``n``'s
        current settings — mirrors ``BatchTab._integration_signature``."""
        card = self._cards[n]
        if not card.using_file():
            calib = ("calib", id(card.result))
        else:
            calib = ("file", card.file_path())
        mask_id = None if mask is None else (tuple(mask.shape), int(np.count_nonzero(mask)))
        pol, sa = corrections
        return (calib, kernel, round(self._r_bin.value(), 4), round(self._e_bin.value(), 4),
                bool(weighted), pol is not None, sa is not None, mask_id,
                src_cfg.get("path"), src_cfg.get("type"), src_cfg.get("dataset"))

    def _cache_geom(self, n, sig, ctx):
        self._geom_cache[n] = ctx
        self._geom_sig[n] = sig

    def _run_all(self):
        siblings = self._loader.siblings()
        if not siblings:
            QtWidgets.QMessageBox.warning(self, "No data", "Load Hydra panel data first.")
            return
        if self._workers:
            return
        self._orphans = [o for o in self._orphans if o.isRunning()]
        self._run_cancelled = False
        self._run_btn.setEnabled(False); self._abort_btn.setEnabled(True)
        self._prog.setVisible(True)
        mode = self._run_mode()
        self._log.append("─" * 40 + f"\nStarting Hydra batch integration ({mode})…")
        panels = sorted(siblings)
        self._pending_panels = list(panels)
        if mode == "parallel":
            pending, self._pending_panels = list(self._pending_panels), []
            for n in pending:
                self._start_panel_worker(n)
        else:
            self._start_next_sequential()

    def _start_next_sequential(self):
        if not self._pending_panels:
            self._maybe_finish_run()
            return
        n = self._pending_panels.pop(0)
        self._start_panel_worker(n)

    def _skip_panel(self, n: int, reason: str):
        self._log.append(f"[ge{n}] {reason} — skipped")
        self._cards[n].set_status(reason)
        if self._run_mode() == "sequential":
            self._start_next_sequential()
        self._maybe_finish_run()

    def _start_panel_worker(self, n: int):
        card = self._cards[n]
        try:
            spec = card.resolved_spec(self._r_bin.value(), self._e_bin.value())
        except Exception as e:
            self._skip_panel(n, f"calibration error: {e}")
            return
        src_cfg = self._loader.source_cfg(n)
        if not src_cfg.get("path"):
            self._skip_panel(n, "no data")
            return

        kernel = self._kernel.currentData()
        corrections = self._corr_widget.build_corrections()
        variance_cfg = ({"error_model": self._err_model.currentText()}
                        if self._var_check.isChecked() else None)
        if variance_cfg and self._corr_widget.any_enabled():
            variance_cfg = None
        base_out = self._out_ed.text().strip()
        out_dir = str(Path(base_out) / f"ge{n}") if base_out else None
        fmt = self._fmt.currentData()
        q_cfg = ({"QMin": self._q_min.value(), "QMax": self._q_max.value(),
                  "QBinSize": self._q_bin.value()} if self._q_check.isChecked() else None)

        pair = self._viewer_pairs[n]
        _axctx = (float(spec.Lsd), float(spec.pxY), float(spec.Wavelength),
                  "Q" if q_cfg else "R")
        pair.waterfall.set_axis_context(*_axctx)
        pair.stack_view.set_axis_context(*_axctx)
        pair.wf_started = False
        self._integrated_fids[n] = set()

        dark = self._loader.dark(n); bright = self._loader.bright(n)
        background = self._loader.background(n); bright_mode = self._loader.bright_mode()
        mask = self._loader.composite_mask(n)
        frame_range = self._loader.frame_range()
        monitor_file = self._mon_ed.text().strip() or None
        weighted = bool(self._azim.currentData())

        sig = self._integration_signature(n, src_cfg, kernel, corrections, weighted, mask)
        context = self._geom_cache.get(n) if self._geom_sig.get(n) == sig else None

        self._last_run_inputs[n] = {
            "src_cfg": src_cfg, "kernel": kernel, "fmt": fmt,
            "frame_range": frame_range, "monitor_file": monitor_file,
            "q_cfg": q_cfg, "weighted": weighted, "bright_mode": bright_mode,
        }
        self._last_run_fields[n] = {"mask": mask, "dark": dark, "bright": bright,
                                     "background": background}

        worker = BatchWorker(
            spec, src_cfg, mask, out_dir, fmt, kernel, corrections, variance_cfg,
            q_cfg=q_cfg, frame_range=frame_range, monitor_file=monitor_file, parent=self,
            dark=dark, bright=bright, background=background, bright_mode=bright_mode,
            weighted=weighted, context=context)
        worker.progress.connect(lambda done, total, n=n: self._cards[n].set_progress(done, total))
        worker.frame_done.connect(
            lambda fid, r_ax, prof, sigma, n=n: self._on_frame(n, fid, r_ax, prof, sigma))
        worker.finished.connect(lambda data, n=n: self._on_panel_done(n, data))
        worker.failed.connect(lambda msg, n=n: self._on_panel_fail(n, msg))
        worker.log_line.connect(lambda line, n=n: self._log.append(f"[ge{n}] {line}"))
        worker.geom_ready.connect(lambda ctx, n=n, s=sig: self._cache_geom(n, s, ctx))
        self._workers[n] = worker
        card.set_status("Starting…")
        self._log.append(f"[ge{n}] starting…")
        worker.start()

    def _on_frame(self, n, fid, r_ax, prof, sigma):
        pair = self._viewer_pairs[n]
        if not pair.wf_started:
            pair.waterfall.reset(r_ax)
            pair.stack_view.reset(r_ax)
            pair.wf_started = True
        pair.waterfall.add_profile(prof)
        pair.stack_view.add_profile(r_ax, prof, label=fid)
        self._integrated_fids[n].add(str(fid))

    def _on_panel_done(self, n: int, data: dict):
        if self._run_cancelled:
            return
        count = data["n"]; out = data.get("out_paths", [])
        aborted = data.get("aborted", False)
        self._cards[n].set_status(f"{'Aborted' if aborted else 'Complete'}: {count} frames")
        verb = "aborted after" if aborted else "done —"
        msg = f"[ge{n}] {verb} {count} frames integrated"
        if out:
            msg += f"\n  saved to: {Path(out[0]).parent}"
        self._log.append(msg)
        self._log_to_project(n, data)
        self._workers.pop(n, None)
        if self._run_mode() == "sequential":
            self._start_next_sequential()
        self._maybe_finish_run()

    def _log_to_project(self, n: int, data: dict):
        if not self._project_ctx or not self._project_ctx.path:
            return
        card = self._cards[n]
        calib_fields, _note = card._calib_fields_in_use()
        calib_ref = None
        if not card.using_file():
            calib_ref = getattr(card.result, "_project_attempt_ref", None)
        try:
            ref = project.append_integration_attempt(
                self._project_ctx.path, f"ge{n}",
                inputs=self._last_run_inputs.get(n, {}), finished_payload=data,
                calibration_snapshot=calib_fields, calib_attempt_ref=calib_ref,
                **self._last_run_fields.get(n, {}))
            self._log.append(f"[ge{n}] logged to project: {ref}")
        except Exception:
            import traceback as _tb
            self._log.append(f"[ge{n}] could not log to project file:\n" + _tb.format_exc()[:400])

    def _on_panel_fail(self, n: int, msg: str):
        if self._run_cancelled:
            return
        self._cards[n].set_status("Error")
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
        self._log.append("Hydra batch integration run complete.")

    def _abort_all(self):
        """Stop every running panel. Mirrors ``HydraCalibrationPage._abort_all``:
        disconnect + orphan immediately (no per-worker wait), since waiting on
        several concurrent (Parallel-mode) workers in turn would freeze the UI
        for their combined grace periods. Frames already integrated were
        written to disk as each panel's run went."""
        if not self._workers:
            return
        self._run_cancelled = True
        for n, w in list(self._workers.items()):
            for sig in (w.progress, w.frame_done, w.finished, w.failed, w.log_line, w.geom_ready):
                try:
                    sig.disconnect()
                except Exception:
                    pass
            w.requestInterruption()
            self._cards[n].set_status("Aborting…")
            self._orphans.append(w)
        self._workers.clear()
        self._pending_panels = []
        self._run_btn.setEnabled(True); self._abort_btn.setEnabled(False)
        self._prog.setVisible(False)
        self._log.append("Hydra batch integration aborted — a background thread per panel "
                         "may still be winding down. Frames already integrated were saved.")

    def shutdown(self):
        """Interrupt + orphan any still-running per-panel workers on app
        close (picked up by ``MainWindow._stop_all_workers``'s generic
        ``tab.shutdown()`` hook via ``BatchTab.shutdown``)."""
        self._abort_all()

    # ── GUI state ────────────────────────────────────────────────────

    def _state_widgets(self) -> dict:
        return {
            "kernel": self._kernel, "r_bin": self._r_bin, "e_bin": self._e_bin,
            "azim": self._azim, "var_check": self._var_check, "err_model": self._err_model,
            "q_check": self._q_check, "q_min": self._q_min, "q_max": self._q_max,
            "q_bin": self._q_bin, "mon_ed": self._mon_ed, "out_ed": self._out_ed,
            "fmt": self._fmt, "run_mode": self._run_mode_combo,
        }

    def get_state(self) -> dict:
        return {
            "anchor_path": self._loader.current_path(),
            "active_panel": self._toolbar.current(),
            "fields": widgets_to_dict(self._state_widgets()),
            "corr": self._corr_widget.get_state(),
            "loader": self._loader.get_state(),
            "cards": {n: widgets_to_dict(card.state_widgets())
                     for n, card in self._cards.items()},
        }

    def set_state(self, state: dict):
        if not state:
            return
        apply_dict_to_widgets(self._state_widgets(), state.get("fields", {}))
        self._corr_widget.set_state(state.get("corr") or {})
        self._loader.set_state(state.get("loader") or {})
        for n_key, fields in (state.get("cards") or {}).items():
            card = self._cards.get(int(n_key))
            if card is None:
                continue
            apply_dict_to_widgets(card.state_widgets(), fields)
            card.refresh_calib_values()
        anchor = state.get("anchor_path")
        if anchor and Path(anchor).exists():
            self._loader.set_path(anchor)
        panel = state.get("active_panel")
        if panel:
            self._toolbar.set_current(panel)

    # ── Cross-tab hand-off ───────────────────────────────────────────

    def set_panel_calibration(self, n: int, result):
        """A Hydra panel's fit finished on the Calibrate tab — auto-populate
        that panel's calibration source here (mirrors ``BatchTab.set_calibration``)."""
        card = self._cards.get(n)
        if card is not None:
            card.set_calibration(result)
