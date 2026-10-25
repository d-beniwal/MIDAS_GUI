"""In-process control API for midas-gui's Data Viewer, Calibrate and Solve Cell tabs.

``GuiController`` is the thing a control channel (``control_server.py``, or
a direct Python caller in a test) dispatches operation requests to. It is
the layer described as "GuiController" in the mcp-control-layer design: it
holds a reference to the live ``MainWindow`` and calls straight into the
existing tab/widget methods rather than reimplementing their logic, so the
GUI and an agent driving it never have two different sources of truth.

Every public ``op_*`` method:
  - takes plain, JSON-decoded keyword arguments (whatever the wire protocol
    handed over as ``args``),
  - returns a plain, JSON-encodable dict — either ``{"ok": True, "result": ...}``
    or ``{"ok": False, "error": {"message", "type"}}`` — and never raises or
    pops a modal dialog, regardless of what the underlying GUI call does.

Three tabs enable an explicit ``agent_mode`` flag (``CalibrationTab``,
``DetectorGeometryCard``, ``SolveCellTab``) so their normal blocking `QMessageBox` guards
become structured errors instead of a dialog nothing can click — see each
class's own ``set_agent_mode`` docstring. Interactive use (agent mode off,
the default) is completely unaffected.

See ``midas_gui_mcp/README.md`` for the wire-protocol op names
(``data_viewer.load_file`` etc.) that map to the ``op_data_viewer_load_file``
style method names here (dots become underscores).
"""
from __future__ import annotations

import tempfile
import traceback
import uuid
from pathlib import Path

import numpy as np

from midas_gui.constants import DISTORTION_NAMES, DISTORTION_PRESETS

_BRIGHT_MODE_INDEX = {"divide": 0, "subtract": 1}


def _ok(result=None) -> dict:
    return {"ok": True, "result": result if result is not None else {}}


def _err(message, kind: str = "error") -> dict:
    return {"ok": False, "error": {"message": str(message), "type": kind}}


def _array_ref(arr, stem: str) -> dict:
    """Write a numpy array to a temp ``.npy`` file and return a reference —
    large frames/profiles/residual maps never go inline into a control
    response; the caller reads the file itself."""
    arr = np.asarray(arr)
    path = Path(tempfile.gettempdir()) / f"midas_gui_control_{stem}_{uuid.uuid4().hex}.npy"
    np.save(path, arr)
    return {"path": str(path), "shape": list(arr.shape), "dtype": str(arr.dtype)}


def _jsonable(value, *, stem: str = "field"):
    """Best-effort conversion of a GUI value into something ``json.dumps``
    can handle directly: numpy scalars inline, small numpy arrays inline,
    large ones become an ``_array_ref``, plain containers recurse,
    everything else falls back to ``str()``."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist() if value.size <= 64 else _array_ref(value, stem)
    if isinstance(value, dict):
        return {k: _jsonable(v, stem=f"{stem}_{k}") for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v, stem=stem) for v in value]
    return str(value)


_CALIB_RESULT_FIELDS = (
    "Lsd", "BC_y", "BC_z", "tx", "ty", "tz", "wavelength_A", "pxY", "pxZ",
    "NrPixelsY", "NrPixelsZ", "distortion", "im_trans",
    "post_residual_strain_uE", "fit_sigma", "fit_at_limit",
)


def _serialize_calib_result(r) -> dict:
    out = {k: _jsonable(getattr(r, k, None), stem=k) for k in _CALIB_RESULT_FIELDS}
    rc = getattr(r, "residual_corr_map", None)
    if rc is not None:
        try:
            out["residual_corr_map"] = _array_ref(np.asarray(rc), "residual_corr")
        except Exception:
            pass
    return out


# ── Solve Cell ───────────────────────────────────────────────────────
# Mixed into GuiController below (kept in this module so the dispatch
# ``op_<namespace>_<verb>`` lookup finds it). Every op drives the same tab
# methods/widgets a click would, never a re-implementation of the pipeline.

_SC_VIEW_TABS = {"reciprocal": 0, "detector": 1, "diagnostics": 2, "volume": 3,
                 "compare": 4, "contamination": 5, "inspector": 6}
_SC_COLOR_MODES = {"panel": 0, "crystal": 1, "q_residual": 2}
_SC_SIZE_MODES = {"equal": 0, "intensity": 1}
_SC_OVERLAYS = {"ewald": "_ov_ewald", "basis": "_ov_basis", "predicted": "_ov_pred",
                "predicted_offpanel": "_ov_pred_hidden", "frame_patches": "_ov_patch",
                "shells": "_ov_shells", "highlight_now": "_ov_now"}
_SC_KS_KEYS = ("a", "b", "c", "alpha", "beta", "gamma", "sg", "n_search", "tol", "min_accept")


def _sc_set_known_structure(ks: dict, params: dict) -> None:
    """Fill a known-structure form (``_build_known_structure_form`` dict)
    from ``{a,b,c,alpha,beta,gamma,sg,n_search,tol,min_accept}``."""
    bad = sorted(set(params) - set(_SC_KS_KEYS))
    if bad:
        raise ValueError(f"unknown known-structure field(s) {bad} (allowed: {list(_SC_KS_KEYS)})")
    for key, val in params.items():
        w = ks[key]
        w.setValue(int(val) if key in ("sg", "n_search", "min_accept") else float(val))


def _sc_ks_state(ks: dict) -> dict:
    return {k: ks[k].value() for k in _SC_KS_KEYS}


class _SolveCellOps:
    @property
    def _sc(self):
        return self._win._solve_cell_tab

    def _sc_panel(self, panel_id):
        """The PanelCard for ``panel_id`` (the active one when None); raises
        ValueError (turned into a bad_args error by the caller) if absent."""
        sc = self._sc
        if panel_id is None:
            return sc._active_panel()
        card = sc._panels.get(int(panel_id))
        if card is None:
            raise ValueError(f"no panel {panel_id} (panels: {sorted(sc._panels)})")
        return card

    # ── job tracking ─────────────────────────────────────────────────
    def _sc_start(self, stage: str, starter) -> dict:
        """Run ``starter()`` (a tab ``_run_*`` method), converting a recorded
        guard failure / no-worker outcome into a structured error, else
        register the job that ``stageFinished`` will resolve."""
        sc = self._sc
        if sc._worker is not None and sc._worker.isRunning():
            return _err("a Solve Cell stage is already running; wait for it first",
                        kind="precondition_failed")
        sc._last_agent_error = None
        for card in sc._panels.values():
            card._last_agent_error = None
        before = sc._worker
        starter()
        if sc._last_agent_error is not None:
            err, sc._last_agent_error = sc._last_agent_error, None
            return _err(err, kind="precondition_failed")
        if sc._worker is None or sc._worker is before:
            return _err(f"{stage} did not start for an unknown reason", kind="exception")
        job_id = str(uuid.uuid4())
        self._jobs[job_id] = {"status": "running", "stage": stage, "result": None, "error": None}
        self._sc_job_id = job_id
        return _ok({"job_id": job_id, "stage": stage})

    def _on_sc_stage_finished(self, stage: str, outcome: dict) -> None:
        job_id = self._sc_job_id
        job = self._jobs.get(job_id) if job_id else None
        if job is None or job["status"] != "running":
            return
        self._sc_job_id = None
        if outcome.get("ok"):
            try:
                result = self._sc_results_summary()
            except Exception:
                result = {}
            result["note"] = outcome.get("note", "")
            self._jobs[job_id] = {"status": "done", "stage": stage, "result": result, "error": None}
        else:
            self._jobs[job_id] = {"status": "failed", "stage": stage, "result": None,
                                  "error": outcome.get("error")}

    def op_solve_cell_get_job(self, job_id) -> dict:
        job = self._jobs.get(job_id)
        if job is None:
            return _err(f"unknown job_id {job_id!r}", kind="bad_args")
        return _ok({k: v for k, v in job.items() if not k.startswith("_")})

    # ── state / results ──────────────────────────────────────────────
    def _sc_domain_summary(self, i: int, d: dict) -> dict:
        r = d["result"]
        keep = {k: r[k] for k in ("cell", "cell_sigma", "rms_drlv", "holohedry_system",
                                  "conventional_cell", "conventional_system", "UB", "notes",
                                  "n_reflections", "diagnostics") if k in r}
        return {"index": i, "label": d["label"], "method": d["method"],
                "n_spots": int(len(d["claimed_index"])),
                "selected": i == self._sc._current_domain_index(),
                **_jsonable(keep, stem=f"domain{i}")}

    def _sc_results_summary(self) -> dict:
        sc = self._sc
        leftover = sc._leftover_df()
        return {
            "n_spots": None if sc._spots_df is None else int(len(sc._spots_df)),
            "n_diamond_flagged": None if sc._flagged_df is None else int(sc._flagged_df["is_diamond"].sum()),
            "n_candidates": None if sc._candidate_df is None else int(len(sc._candidate_df)),
            "ab_initio_success": None if sc._ab_initio_raw is None else bool(sc._ab_initio_raw.success),
            "n_remaining": None if leftover is None else int(len(leftover)),
            "domains": [self._sc_domain_summary(i, d) for i, d in enumerate(sc._domains)],
        }

    def op_solve_cell_get_state(self) -> dict:
        sc = self._sc
        panels = {}
        for pid, card in sc._panels.items():
            panels[pid] = {
                "active": card is sc._active_panel(),
                "data_path": card.loader.data_path(), "n_frames": card.loader.n_frames(),
                "calibration": card._calib_path, "has_calibration": card.has_calibration(),
                "omega": {"start": card._ome_start.value(), "end": card._ome_end.value(),
                          "step": card._ome_step.value(), "first_idx": card._ome_first_idx.value(),
                          "last_idx": card._last_idx() if card._last_idx() < card._LAST_IDX_OPEN else None},
            }
        params = {
            "project_dir": sc._proj_ed.text().strip() or None,
            "ingest": {"low_count_threshold": sc._low_count.value(), "mask_grow": sc._mask_grow.value(),
                       "blob_threshold": sc._blob_thresh.value(), "blob_min_vol": sc._blob_minvol.value(),
                       "split_ratio": sc._split_ratio.value(),
                       "gap_bridge": sc._gap_bridge.value() if sc._gap_bridge_on.isChecked() else 0,
                       "sector_candidates": list(sc._sector_candidates_cfg()),
                       "max_workers": sc._ingest_max_workers.value()},
            "diamond": {"diamond_a": sc._diamond_a.value(), "tol_deg": sc._contam_tol.value()},
            "index": {"method": "known_structure" if sc._index_method.currentIndex() else "free",
                      "sigma_g": sc._sigma_g.value(), "min_reflections": sc._min_refl.value(),
                      "tol": sc._tol_override.value(), "known_structure": _sc_ks_state(sc._ks3)},
            "index_remaining": {"method": "known_structure" if sc._leftover_method.currentIndex() else "free",
                                "known_structure": _sc_ks_state(sc._ks5)},
        }
        running = sc._worker is not None and sc._worker.isRunning()
        return _ok({"panels": panels, "params": params, "running": running,
                    "results": self._sc_results_summary(),
                    "view": next((k for k, v in _SC_VIEW_TABS.items()
                                  if v == sc._view_tabs.currentIndex()), None)})

    def op_solve_cell_get_log(self, tail: int = 80) -> dict:
        text = self._sc._log.toPlainText()
        return _ok({"log": "\n".join(text.splitlines()[-int(tail):])})

    # ── panels ───────────────────────────────────────────────────────
    def op_solve_cell_add_panel(self) -> dict:
        card = self._sc._add_panel()
        return _ok({"panel_id": card.panel_id(), "panels": sorted(self._sc._panels)})

    def op_solve_cell_remove_panel(self, panel_id) -> dict:
        sc = self._sc
        if int(panel_id) not in sc._panels:
            return _err(f"no panel {panel_id}", kind="bad_args")
        sc._last_agent_error = None
        sc._close_panel_tab(int(panel_id) - 1)
        if sc._last_agent_error is not None:
            err, sc._last_agent_error = sc._last_agent_error, None
            return _err(err, kind="precondition_failed")
        return _ok({"panels": sorted(sc._panels)})

    def op_solve_cell_select_panel(self, panel_id) -> dict:
        sc = self._sc
        if int(panel_id) not in sc._panels:
            return _err(f"no panel {panel_id}", kind="bad_args")
        sc._panel_tabs.setCurrentWidget(sc._panels[int(panel_id)])
        return _ok({"active_panel": int(panel_id)})

    def op_solve_cell_panel_load_data(self, path, dataset=None, panel_id=None) -> dict:
        try:
            card = self._sc_panel(panel_id)
        except ValueError as e:
            return _err(e, kind="bad_args")
        card.loader.set_path(path, dataset=dataset)
        return _ok({"panel_id": card.panel_id(), "path": card.loader.data_path(),
                    "n_frames": card.loader.n_frames()})

    def op_solve_cell_panel_set_field(self, role, path, checked: bool = True, mode=None,
                                      panel_id=None) -> dict:
        try:
            card = self._sc_panel(panel_id)
        except ValueError as e:
            return _err(e, kind="bad_args")
        return self._set_field(card.loader, role, path, checked, mode)

    def op_solve_cell_panel_load_calibration(self, path, panel_id=None) -> dict:
        try:
            card = self._sc_panel(panel_id)
        except ValueError as e:
            return _err(e, kind="bad_args")
        card._last_agent_error = None
        if not card._load_calib_from_path(path):
            return _err(card._last_agent_error or f"could not load calibration {path!r}",
                        kind="precondition_failed")
        return _ok({"panel_id": card.panel_id(),
                    "geometry": _jsonable(card.geometry_cfg(), stem="geometry"),
                    "notes": (card._calib_info or {}).get("notes", [])})

    def op_solve_cell_panel_set_omega(self, start=None, end=None, step=None, first_idx=None,
                                      panel_id=None) -> dict:
        try:
            card = self._sc_panel(panel_id)
        except ValueError as e:
            return _err(e, kind="bad_args")
        for value, widget in ((start, card._ome_start), (end, card._ome_end),
                              (step, card._ome_step)):
            if value is not None:
                widget.setValue(float(value))
        if first_idx is not None:
            card._ome_first_idx.setValue(int(first_idx))
        return _ok({"panel_id": card.panel_id(), "start": card._ome_start.value(),
                    "end": card._ome_end.value(), "step": card._ome_step.value(),
                    "first_idx": card._ome_first_idx.value(),
                    "omega_span_without_step": card.omega_span_without_step()})

    def op_solve_cell_panel_detect_first_idx(self, field, panel_id=None) -> dict:
        try:
            card = self._sc_panel(panel_id)
        except ValueError as e:
            return _err(e, kind="bad_args")
        card._last_agent_error = None
        if not card.detect_first_idx(field):
            return _err(card._last_agent_error or "detection failed", kind="precondition_failed")
        return _ok({"panel_id": card.panel_id(), "first_idx": card._ome_first_idx.value()})

    def op_solve_cell_panel_list_omega_fields(self, panel_id=None) -> dict:
        try:
            card = self._sc_panel(panel_id)
        except ValueError as e:
            return _err(e, kind="bad_args")
        return _ok({"fields": card._omega_fields()})

    # ── stage parameters ─────────────────────────────────────────────
    def op_solve_cell_set_project_dir(self, path) -> dict:
        self._sc._proj_ed.setText(path or "")
        return _ok({"project_dir": self._sc._proj_ed.text().strip() or None})

    def op_solve_cell_set_ingest_params(self, low_count_threshold=None, mask_grow=None,
                                        blob_threshold=None, blob_min_vol=None, split_ratio=None,
                                        gap_bridge=None, sector_candidates=None,
                                        max_workers=None) -> dict:
        sc = self._sc
        for value, widget, cast in (
                (low_count_threshold, sc._low_count, float), (mask_grow, sc._mask_grow, int),
                (blob_threshold, sc._blob_thresh, float), (blob_min_vol, sc._blob_minvol, int),
                (split_ratio, sc._split_ratio, float), (max_workers, sc._ingest_max_workers, int)):
            if value is not None:
                widget.setValue(cast(value))
        if gap_bridge is not None:
            sc._gap_bridge_on.setChecked(int(gap_bridge) > 0)
            if int(gap_bridge) > 0:
                sc._gap_bridge.setValue(int(gap_bridge))
        if sector_candidates is not None:
            sc._sector_candidates_ed.setText(",".join(str(int(n)) for n in sector_candidates))
        return _ok({"ingest": self.op_solve_cell_get_state()["result"]["params"]["ingest"]})

    def op_solve_cell_set_diamond_params(self, diamond_a=None, tol_deg=None) -> dict:
        sc = self._sc
        if diamond_a is not None:
            sc._diamond_a.setValue(float(diamond_a))
        if tol_deg is not None:
            sc._contam_tol.setValue(float(tol_deg))
        return _ok({"diamond_a": sc._diamond_a.value(), "tol_deg": sc._contam_tol.value()})

    def op_solve_cell_set_index_params(self, method=None, sigma_g=None, min_reflections=None,
                                       tol=None, known_structure=None) -> dict:
        sc = self._sc
        if method is not None:
            if method not in ("free", "known_structure"):
                return _err("method must be 'free' or 'known_structure'", kind="bad_args")
            sc._index_method.setCurrentIndex(1 if method == "known_structure" else 0)
        if sigma_g is not None:
            sc._sigma_g.setValue(float(sigma_g))
        if min_reflections is not None:
            sc._min_refl.setValue(int(min_reflections))
        if tol is not None:
            sc._tol_override.setValue(float(tol))
        if known_structure:
            try:
                _sc_set_known_structure(sc._ks3, known_structure)
            except ValueError as e:
                return _err(e, kind="bad_args")
        return _ok({"method": "known_structure" if sc._index_method.currentIndex() else "free",
                    "sigma_g": sc._sigma_g.value(), "min_reflections": sc._min_refl.value(),
                    "tol": sc._tol_override.value(), "known_structure": _sc_ks_state(sc._ks3)})

    def op_solve_cell_set_remaining_params(self, method=None, known_structure=None) -> dict:
        sc = self._sc
        if method is not None:
            if method not in ("free", "known_structure"):
                return _err("method must be 'free' or 'known_structure'", kind="bad_args")
            sc._leftover_method.setCurrentIndex(1 if method == "known_structure" else 0)
        if known_structure:
            try:
                _sc_set_known_structure(sc._ks5, known_structure)
            except ValueError as e:
                return _err(e, kind="bad_args")
        return _ok({"method": "known_structure" if sc._leftover_method.currentIndex() else "free",
                    "known_structure": _sc_ks_state(sc._ks5)})

    def op_solve_cell_fill_remaining_from_latest_domain(self) -> dict:
        sc = self._sc
        if not sc._domains:
            return _err("no solved domain yet", kind="precondition_failed")
        sc._fill_ks5_from_latest_domain()
        return _ok({"known_structure": _sc_ks_state(sc._ks5)})

    # ── stage runs (async: poll solve_cell.wait / get_job) ───────────
    def op_solve_cell_run_ingest(self, acknowledge_warnings: bool = False) -> dict:
        sc = self._sc
        sc._agent_ack_warnings = bool(acknowledge_warnings)
        try:
            return self._sc_start("ingest", sc._run_ingest)
        finally:
            sc._agent_ack_warnings = False

    def op_solve_cell_run_diamond_filter(self) -> dict:
        return self._sc_start("diamond", self._sc._run_diamond_filter)

    def op_solve_cell_run_index(self) -> dict:
        return self._sc_start("index", self._sc._run_index)

    def op_solve_cell_run_refine(self) -> dict:
        return self._sc_start("refine", self._sc._run_refine)

    def op_solve_cell_run_index_remaining(self) -> dict:
        return self._sc_start("index_remaining", self._sc._run_index_remaining)

    # ── domains / spots ──────────────────────────────────────────────
    def op_solve_cell_get_domains(self) -> dict:
        return _ok({"domains": self._sc_results_summary()["domains"]})

    def op_solve_cell_select_domain(self, index) -> dict:
        sc = self._sc
        if not 0 <= int(index) < len(sc._domains):
            return _err(f"no domain {index} ({len(sc._domains)} solved)", kind="bad_args")
        sc._domain_list.setCurrentRow(int(index))
        return _ok({"selected": int(index), "label": sc._domains[int(index)]["label"]})

    def op_solve_cell_get_spots(self, which: str = "all") -> dict:
        """Write a spot table to a temp CSV and return its path + shape.
        which: all (every ingested spot), candidates (diamond-filtered
        pool), diamond (flagged spots only)."""
        import pandas as pd  # noqa: F401  (frames below are DataFrames)
        sc = self._sc
        df = {"all": sc._spots_df,
              "candidates": sc._candidate_df,
              "diamond": None if sc._flagged_df is None else sc._flagged_df[sc._flagged_df["is_diamond"]],
              }.get(which, "bad")
        if isinstance(df, str):
            return _err("which must be all/candidates/diamond", kind="bad_args")
        if df is None:
            return _err(f"no {which!r} spots yet — run the earlier stages first",
                        kind="precondition_failed")
        path = Path(tempfile.gettempdir()) / f"midas_gui_control_spots_{which}_{uuid.uuid4().hex}.csv"
        df.to_csv(path, index=False)
        return _ok({"path": str(path), "n_rows": int(len(df)), "columns": list(df.columns)})

    def op_solve_cell_select_spot(self, spot_id) -> dict:
        sc = self._sc
        sc._select_spot(int(spot_id))
        row = sc._spot_rows_by_id.get(int(spot_id))
        return _ok({"spot_id": int(spot_id), "found": row is not None,
                    "description": sc._spot_description(row) if row else None})

    def op_solve_cell_show_selected_on_detector(self) -> dict:
        sc = self._sc
        if sc._selected_spot_id is None:
            return _err("no spot selected — call solve_cell.select_spot first",
                        kind="precondition_failed")
        sc._show_selected_on_detector()
        return _ok({"spot_id": sc._selected_spot_id, "active_panel": sc._active_panel_id(),
                    "details": sc._spot_details.text()})

    # ── views ────────────────────────────────────────────────────────
    def op_solve_cell_set_view(self, view) -> dict:
        idx = _SC_VIEW_TABS.get(view)
        if idx is None:
            return _err(f"unknown view {view!r} (one of {sorted(_SC_VIEW_TABS)})", kind="bad_args")
        self._sc._view_tabs.setCurrentIndex(idx)
        return _ok({"view": view})

    def op_solve_cell_set_reciprocal_options(self, color_by=None, show_unindexed=None,
                                             point_size=None, size_mode=None) -> dict:
        sc = self._sc
        if color_by is not None:
            if color_by not in _SC_COLOR_MODES:
                return _err(f"color_by must be one of {sorted(_SC_COLOR_MODES)}", kind="bad_args")
            sc._recip_mode.setCurrentIndex(_SC_COLOR_MODES[color_by])
        if size_mode is not None:
            if size_mode not in _SC_SIZE_MODES:
                return _err(f"size_mode must be one of {sorted(_SC_SIZE_MODES)}", kind="bad_args")
            sc._recip_size_mode.setCurrentIndex(_SC_SIZE_MODES[size_mode])
        if show_unindexed is not None:
            sc._recip_show_unindexed.setChecked(bool(show_unindexed))
        if point_size is not None:
            sc._recip_point_size.setValue(int(point_size))
        return _ok({"stats": sc._recip_stats.text()})

    def op_solve_cell_set_overlays(self, omega_deg=None, **flags) -> dict:
        sc = self._sc
        bad = sorted(set(flags) - set(_SC_OVERLAYS))
        if bad:
            return _err(f"unknown overlay(s) {bad} (allowed: {sorted(_SC_OVERLAYS)}, omega_deg)",
                        kind="bad_args")
        for key, value in flags.items():
            if value is not None:
                getattr(sc, _SC_OVERLAYS[key]).setChecked(bool(value))
        if omega_deg is not None:
            sc._refresh_overlays()   # re-ranges the slider to the scan first
            sc._ov_omega.setValue(int(round(float(omega_deg) * 10)))
        return _ok({"omega_deg": sc._ov_omega.value() / 10.0, "status": sc._ov_status.text(),
                    "overlays": {k: getattr(sc, a).isChecked() for k, a in _SC_OVERLAYS.items()}})

    def op_solve_cell_build_volume(self, pitch: float = None) -> dict:
        """Start the reciprocal-volume build (Volume tab; pitch defaults to
        the tab's own voxel-pitch field). Async: poll
        solve_cell.get_volume_status."""
        sc = self._sc
        if pitch is None:
            pitch = sc._volume_view.pitch.value()
        sc._start_volume_build(float(pitch))
        if sc._vol_worker is None or not sc._vol_worker.isRunning():
            return _err(sc._volume_view.info.text() or "volume build did not start",
                        kind="precondition_failed")
        return _ok({"started": True, "info": sc._volume_view.info.text()})

    def op_solve_cell_get_volume_status(self) -> dict:
        sc = self._sc
        building = sc._vol_worker is not None and sc._vol_worker.isRunning()
        return _ok({"building": building, "has_volume": "reciprocal_volume" in sc._viz_products,
                    "info": sc._volume_view.info.text()})

    def op_solve_cell_review_domains(self, a, b, conclusion) -> dict:
        """Record a reviewer conclusion for a domain pair (Compare domains
        tab). ``a``/``b`` are the comparison keys shown in that tab; an empty
        conclusion clears it. Never changes domain claims."""
        sc = self._sc
        entries = sc._comparison_entries() if sc._candidate_df is not None else []
        keys = [e["key"] for e in entries]
        if a not in keys or b not in keys:
            return _err(f"unknown comparison key(s); available: {keys}", kind="bad_args")
        sc._comparison_product(entries)
        sc._on_compare_review(a, b, conclusion)
        return _ok({"a": a, "b": b, "conclusion": conclusion})

    def op_solve_cell_save_project(self, path=None) -> dict:
        """Save the open project (or to ``path``), including the Solve Cell
        analysis record — same as File ▸ Save Project."""
        win = self._win
        target = path or getattr(getattr(win, "_project_ctx", None), "path", None)
        if not target:
            return _err("no project is open — pass path", kind="precondition_failed")
        win.save_project(target)
        return _ok({"path": str(target)})


class GuiController(_SolveCellOps):
    """Dispatches ``"namespace.verb"`` ops (see the module docstring) against
    the live ``main_window``'s Data Viewer and Calibrate tabs."""

    def __init__(self, main_window):
        self._win = main_window
        self._jobs: dict = {}   # job_id -> {"status","result","error"}
        self._cal.set_agent_mode(True)
        self._geom.set_agent_mode(True)
        sc = getattr(main_window, "_solve_cell_tab", None)
        if sc is not None:
            sc.set_agent_mode(True)
            sc.stageFinished.connect(self._on_sc_stage_finished)
        self._sc_job_id = None   # the one in-flight Solve Cell job, if any

    @property
    def _view(self):
        return self._win._view_tab

    @property
    def _geom(self):
        return self._win._view_tab._geom_card

    @property
    def _cal(self):
        return self._win._cal_tab

    # Namespace -> MainWindow attribute, for ensure_tab_visible below. Keeps
    # "which attribute backs which tab" as GuiController's own domain
    # knowledge rather than control_server.py's, even though the *call* to
    # switch happens there (see that module's _handle_line).
    _NAMESPACE_TABS = {"data_viewer": "_view_tab", "calibrate": "_cal_tab",
                       "solve_cell": "_solve_cell_tab"}

    # Specific op -> a no-arg method on that op's own namespace tab widget
    # that brings the right *sub*-view to the front within it (e.g. the Data
    # Viewer's own Radial Profile / Eta vs R Cake bottom tabs) — so a human
    # watching sees not just "the agent is on the Data Viewer" but exactly
    # which result it just produced, the same way switching the namespace tab
    # already shows which top-level tab it's driving.
    _OP_SUBFOCUS = {
        "data_viewer.get_radial_profile": "show_radial_profile_tab",
        "data_viewer.get_cake": "show_cake_tab",
    }

    def ensure_tab_visible(self, op: str) -> None:
        """Bring the tab `op`'s namespace operates on to the front, so a
        human watching the GUI actually sees what the agent is doing instead
        of it silently happening on a tab that isn't on screen — e.g.
        starting a calibration run switches to the Calibrate tab. A no-op if
        that tab is already showing (setCurrentWidget on the current widget
        doesn't flicker/refocus). Also focuses the specific sub-view a
        handful of ops produce (see ``_OP_SUBFOCUS``)."""
        namespace = (op or "").split(".", 1)[0]
        attr = self._NAMESPACE_TABS.get(namespace)
        tab_widget = getattr(self._win, attr, None) if attr else None
        tabs = getattr(self._win, "_tabs", None)
        if tab_widget is not None and tabs is not None:
            tabs.setCurrentWidget(tab_widget)
        subfocus = self._OP_SUBFOCUS.get(op)
        if subfocus is not None and tab_widget is not None:
            method = getattr(tab_widget, subfocus, None)
            if method is not None:
                method()

    # ── dispatch ─────────────────────────────────────────────────────
    def dispatch(self, op: str, args: dict) -> dict:
        if not op:
            return _err("missing 'op'", kind="bad_request")
        method = getattr(self, "op_" + op.replace(".", "_"), None)
        if method is None:
            return _err(f"unknown op {op!r}", kind="unknown_op")
        try:
            return method(**(args or {}))
        except TypeError as e:
            return _err(f"bad arguments for {op!r}: {e}", kind="bad_args")
        except Exception:
            return _err(traceback.format_exc(), kind="exception")

    # ── shared: dark/bright/background field setters ───────────────────
    def _set_field(self, loader, role: str, path: str, checked: bool, mode) -> dict:
        sel = {"dark": getattr(loader, "_dark_sel", None),
               "bright": getattr(loader, "_bright_sel", None),
               "background": getattr(loader, "_bg_sel", None)}.get(role)
        if sel is None:
            return _err(f"unknown field role {role!r} (dark/bright/background)", kind="bad_args")
        state = {"path": path, "checked": bool(checked)}
        if mode is not None:
            if mode not in _BRIGHT_MODE_INDEX:
                return _err(f"unknown mode {mode!r} (divide/subtract)", kind="bad_args")
            state["mode"] = _BRIGHT_MODE_INDEX[mode]
        sel.set_state(state)
        return _ok({"role": role, "path": path})

    # ── Data Viewer ──────────────────────────────────────────────────
    def op_data_viewer_load_file(self, path, dataset=None) -> dict:
        self._view._loader.set_path(path, dataset=dataset)
        # set_path's own load is synchronous, but the tab only refreshes its
        # own self._cur (what ring simulation/radial profile/stats actually
        # read) via a 60ms-debounced dataChanged -> timer -> _on_loader_data
        # chain, meant to coalesce interactive bursts. A control-API caller
        # has no reason to wait on that debounce, so force the refresh now.
        self._view._on_loader_data()
        return _ok({"path": self._view._loader.data_path(),
                    "n_frames": self._view._loader.n_frames()})

    def op_data_viewer_start_live(self, pv, backend="pva") -> dict:
        started = self._view.start_live_pv(pv, backend)
        if not started:
            return _err(f"could not start live stream on {pv!r} ({backend})",
                        kind="live_start_failed")
        return _ok({"started": True})

    def op_data_viewer_stop_live(self) -> dict:
        self._view._loader.stop_live()
        return _ok()

    def op_data_viewer_set_geometry(self, **params) -> dict:
        self._geom.set_geometry(params)
        return _ok(self._geom.get_geometry())

    def op_data_viewer_load_calibration_file(self, path) -> dict:
        try:
            summary = self._geom.load_calibration_file(path)
        except FileNotFoundError as e:
            return _err(str(e), kind="no_data")
        except ValueError as e:
            return _err(str(e), kind="bad_args")
        return _ok(_jsonable(summary, stem="calib_file"))

    def op_data_viewer_get_geometry(self) -> dict:
        return _ok(self._geom.get_geometry())

    def op_data_viewer_set_materials(self, materials) -> dict:
        self._geom.set_materials(materials)
        return _ok({"materials": self._geom.materials_state()})

    def op_data_viewer_simulate_rings(self, require_image: bool = False) -> dict:
        errors = self._geom.simulate_rings(require_image=require_image)
        # materials_state() deliberately strips every "_"-prefixed key
        # (it's the GUI-state save/restore shape) — that includes "_rings",
        # the very thing this op exists to return, so read the live
        # materials list directly instead.
        rings = [{"name": m["name"], "rings": m.get("_rings", [])} for m in self._geom._materials]
        return _ok({"errors": errors, "rings": _jsonable(rings, stem="rings")})

    def op_data_viewer_get_radial_profile(self, mode: str = "fast") -> dict:
        accurate_box = self._geom._rad_accurate
        if accurate_box is not None:
            accurate_box.setChecked(mode == "accurate")
        self._geom.radial_integrate()
        prof = self._view._profile_view.get_profile()
        if prof is None:
            return _err("no radial profile available — load data first", kind="no_data")
        return _ok({
            "r_px": _array_ref(prof["r_px"], "r_px"),
            "profile": _array_ref(prof["profile"], "profile"),
            "wavelength_A": prof["wavelength_A"], "lsd_um": prof["lsd_um"],
            "px_um": prof["px_um"],
        })

    def op_data_viewer_get_cake(self, r_bin=None, eta_bin=None) -> dict:
        if r_bin is not None and self._geom._cake_r_bin is not None:
            self._geom._cake_r_bin.setValue(float(r_bin))
        if eta_bin is not None and self._geom._cake_eta_bin is not None:
            self._geom._cake_eta_bin.setValue(float(eta_bin))
        self._geom.cake_integrate()
        cake = self._view._cake_view.get_cake()
        if cake is None:
            return _err("no cake available — load data first", kind="no_data")
        return _ok({
            "cake": _array_ref(cake["cake_2d"], "cake"),
            "r_axis_px": _array_ref(cake["r_axis_px"], "cake_r_axis"),
            "eta_axis_deg": _array_ref(cake["eta_axis_deg"], "cake_eta_axis"),
            "wavelength_A": cake["wavelength_A"], "lsd_um": cake["lsd_um"],
            "px_um": cake["px_um"],
        })

    def op_data_viewer_get_statistics(self, scope: str = "current") -> dict:
        from midas_gui.widgets import IntensityStatsPanel
        if scope == "current":
            img = self._view._cur
            if img is None:
                return _err("no frame loaded", kind="no_data")
            return _ok(IntensityStatsPanel.compute_stats(self._view._unmasked_values(img)))
        if scope == "all":
            return self._start_all_frame_stats_job()
        return _err(f"unknown scope {scope!r} (current/all)", kind="bad_args")

    def _start_all_frame_stats_job(self) -> dict:
        from midas_gui.workers import AllFrameStatsWorker
        from midas_gui.widgets import IntensityStatsPanel
        job_id = str(uuid.uuid4())
        loader = self._view._loader
        worker = AllFrameStatsWorker(
            loader.full_stack, dark=loader.dark(), bright=loader.bright(),
            background=loader.background(), bright_mode=loader.bright_mode(),
            composite_mask=loader.composite_mask(),
            imask_on=self._view._imask_on.isChecked(),
            imask_lo=self._view._imask_lo.value(), imask_hi=self._view._imask_hi.value(),
            parent=self._win)
        self._jobs[job_id] = {"status": "running", "result": None, "error": None,
                              "_keepalive": worker}

        def _done(vals, n):
            self._jobs[job_id] = {"status": "done",
                                  "result": IntensityStatsPanel.compute_stats(vals), "error": None}

        def _fail(msg):
            self._jobs[job_id] = {"status": "failed", "result": None, "error": msg}

        worker.finished.connect(_done)
        worker.failed.connect(_fail)
        worker.start()
        return _ok({"job_id": job_id})

    def op_data_viewer_set_field(self, role, path, checked: bool = True, mode=None) -> dict:
        return self._set_field(self._view._loader, role, path, checked, mode)

    def op_data_viewer_get_state(self) -> dict:
        return _ok(_jsonable(self._view.get_state(), stem="view_state"))

    # ── Calibrate ────────────────────────────────────────────────────
    def op_calibrate_load(self, path, dataset=None, calibrant=None) -> dict:
        self._cal._loader.set_path(path, dataset=dataset)
        if calibrant is not None:
            self._cal._cal.setCurrentText(calibrant)
        return _ok({"path": self._cal._loader.data_path(),
                    "calibrant": self._cal._cal.currentText()})

    def op_calibrate_set_field(self, role, path, checked: bool = True, mode=None) -> dict:
        return self._set_field(self._cal._loader, role, path, checked, mode)

    # ── Calibrate: projection (reduce a stack to one image before fitting) ──
    def op_calibrate_set_projection(self, method=None, skip=None, n_frames=None) -> dict:
        tab = self._cal
        if method is not None:
            if method not in tab._proj_method:
                return _err(f"unknown projection method {method!r} (max/sum/mean)",
                           kind="bad_args")
            tab._proj_method[method].setChecked(True)
        if skip is not None:
            tab._proj_skip.setValue(int(skip))
        if n_frames is not None:
            tab._proj_nframes.setValue(int(n_frames))
        return _ok({"method": next(m for m, b in tab._proj_method.items() if b.isChecked()),
                    "skip": tab._proj_skip.value(), "n_frames": tab._proj_nframes.value()})

    def op_calibrate_project(self) -> dict:
        """Run the stack projection (Method/Skip/N-frames as currently set —
        see ``calibrate.set_projection``) and feed the result into the
        pipeline as its input image. Async like a calibration run: returns a
        job_id pollable with the same ``calibrate.get_job``/``calibrate.wait``
        used for ``calibrate.run``.

        Deliberately does NOT connect its own ``finished``/``failed`` handler
        to the worker the way ``op_calibrate_run`` does for a
        ``CalibrationWorker`` — ``ProjectionWorker`` is an in-process
        ``QThread`` that can finish a small reduction in well under a
        millisecond, and a connection made here (necessarily *after*
        ``tab._project()`` has already called ``.start()``) can lose the race
        against an already-emitted signal. ``CalibrationWorker`` is a
        separate OS process, whose spawn latency alone makes that race
        unobservable in practice. Instead, ``op_calibrate_get_job`` lazily
        reads the tab's own canonical post-projection state (set by
        ``CalibrationTab._on_project_done``/``_on_project_failed``, which
        *are* connected before ``.start()`` and so always fire) once the
        worker is no longer running — see that method."""
        tab = self._cal
        if tab._loader.n_frames() <= 1:
            return _err("projection needs a multi-frame source", kind="precondition_failed")
        if tab._proj_worker is not None and tab._proj_worker.isRunning():
            return _err("a projection is already running", kind="precondition_failed")
        tab._last_proj_error = None
        tab._project()
        worker = tab._proj_worker
        if worker is None:
            return _err("projection did not start for an unknown reason", kind="exception")
        job_id = str(uuid.uuid4())
        self._jobs[job_id] = {"status": "running", "result": None, "error": None,
                              "_proj_worker": worker, "_keepalive": worker}
        return _ok({"job_id": job_id})

    def op_calibrate_clear_projection(self) -> dict:
        """Drop the active projection — the pipeline reverts to the current
        raw frame as its input image."""
        self._cal._on_project_clear()
        return _ok()

    def op_calibrate_run(self, mode, refine=None, manual_seed=None, limits=None,
                         build_residual_corr=None) -> dict:
        tab = self._cal
        idx = tab._pipeline.findData(mode)
        if idx < 0:
            return _err(f"unknown pipeline mode {mode!r}", kind="bad_args")
        tab._pipeline.setCurrentIndex(idx)
        if refine:
            box_flags = {k: v for k, v in refine.items() if k in tab._REFINE_BOXES}
            if box_flags:
                tab._set_refine_box_state(box_flags)
            if "Distortion" in refine:
                tab._ref_dist.setChecked(bool(refine["Distortion"]))
            dist_mode = refine.get("distortion_mode")
            dist_coeffs = refine.get("distortion_coeffs")
            if dist_mode is not None:
                if dist_mode not in DISTORTION_PRESETS:
                    return _err(f"unknown distortion_mode {dist_mode!r} "
                               f"(one of {sorted(DISTORTION_PRESETS)})", kind="bad_args")
                tab._dist_coeffs = set(DISTORTION_PRESETS[dist_mode])
                tab._update_dist_label()
            elif dist_coeffs is not None:
                bad = sorted(set(dist_coeffs) - set(DISTORTION_NAMES))
                if bad:
                    return _err(f"unknown distortion coefficient(s): {bad}", kind="bad_args")
                tab._dist_coeffs = set(dist_coeffs)
                tab._update_dist_label()
        if limits:
            for name, row in limits.items():
                widgets = tab._limit_widgets.get(name)
                if widgets is None:
                    return _err(f"unknown limit row {name!r}", kind="bad_args")
                cb, spin, combo = widgets
                if isinstance(row, dict):
                    if row.get("value") is not None:
                        spin.setValue(float(row["value"]))
                    if row.get("unit"):
                        ix = combo.findText(str(row["unit"]))
                        if ix >= 0:
                            combo.setCurrentIndex(ix)
                    if "on" in row:
                        cb.setChecked(bool(row["on"]))
            tab._on_limits_changed()
        if build_residual_corr is not None:
            tab._build_rc.setChecked(bool(build_residual_corr))
        if manual_seed:
            spins = {"BC_y": tab._seed_bcy, "BC_z": tab._seed_bcz, "Lsd": tab._seed_lsd,
                     "tx": tab._seed_tx, "ty": tab._seed_ty, "tz": tab._seed_tz}
            for key, value in manual_seed.items():
                spin = spins.get(key)
                if spin is not None:
                    spin.setValue(float(value) / (1000.0 if key == "Lsd" else 1.0))
            seed_flag = {"BC_y": "BC", "BC_z": "BC", "Lsd": "Lsd",
                        "tx": "tx", "ty": "ty", "tz": "tz"}
            flags = {seed_flag[k]: True for k in manual_seed if k in seed_flag}
            if flags:
                tab._enable_seed(**flags)
            dist_seed = manual_seed.get("distortion")
            if dist_seed:
                tab._seed_dist = {k: float(v) for k, v in dist_seed.items()}
                tab._enable_seed(Distortion=True)
                tab._update_seed_dist_label()
        tab._last_agent_error = None
        tab._run()
        if tab._last_agent_error is not None:
            err, tab._last_agent_error = tab._last_agent_error, None
            return _err(err, kind="precondition_failed")
        worker = tab._worker
        if worker is None:
            return _err("calibration did not start for an unknown reason", kind="exception")
        job_id = str(uuid.uuid4())
        self._jobs[job_id] = {"status": "running", "result": None, "error": None}

        def _done(result):
            self._jobs[job_id] = {"status": "done",
                                  "result": _serialize_calib_result(result), "error": None}

        def _fail(msg):
            self._jobs[job_id] = {"status": "failed", "result": None, "error": msg}

        worker.finished.connect(_done)
        worker.failed.connect(_fail)
        return _ok({"job_id": job_id, "mode": mode})

    def op_calibrate_get_job(self, job_id) -> dict:
        job = self._jobs.get(job_id)
        if job is None:
            return _err(f"unknown job_id {job_id!r}", kind="bad_args")
        worker = job.get("_proj_worker")
        if worker is not None and job["status"] == "running" and not worker.isRunning():
            job = self._jobs[job_id] = self._finalize_projection_job()
        return _ok({k: v for k, v in job.items() if not k.startswith("_")})

    def _finalize_projection_job(self) -> dict:
        """Read back a finished projection's outcome from the tab's own
        canonical state — see ``op_calibrate_project``'s docstring for why
        this is a lazy poll-and-read instead of a second signal connection."""
        tab = self._cal
        if tab._last_proj_error is not None:
            return {"status": "failed", "result": None, "error": tab._last_proj_error}
        if tab._proj_active and tab._proj_image is not None:
            return {"status": "done",
                    "result": {"info": tab._proj_note.text(),
                              "shape": list(tab._proj_image.shape)},
                    "error": None}
        return {"status": "failed", "result": None,
               "error": "projection finished with no result and no recorded error"}

    # NOTE: there is deliberately no blocking op_calibrate_wait here. An
    # earlier version busy-waited with QCoreApplication.processEvents() in a
    # loop — found (by actually driving this over the socket) to be unsafe:
    # a nested processEvents() call can let Qt process the *same* socket's
    # disconnected signal (e.g. the client's own read timing out) while this
    # method is still holding a reference to it, deleting the C++ QLocalSocket
    # object out from under the handler that will try to write a reply to it
    # once the wait returns ("wrapped C/C++ object ... has been deleted").
    # Waiting for a job (plain "calibrate.wait") and the
    # "calibrate.run_frozen_point_then_oneshot" composite are instead
    # implemented in control_server.py using QTimer.singleShot polling, which
    # returns control to the real event loop between checks instead of
    # nesting a second one inside this call — see ControlServer._poll_job /
    # ._run_frozen_point_then_oneshot.

    def op_calibrate_seed_from_last_run(self) -> dict:
        tab = self._cal
        if tab._result is None:
            return _err("no completed calibration run yet", kind="no_data")
        tab._seed_from_result(tab._result)
        return _ok({"seed_note": tab._seed_note.text()})

    def op_calibrate_import_from_data_viewer(self) -> dict:
        g = self._view.get_geometry()
        self._cal.apply_geometry(g)
        return _ok({"applied": g})

    def op_calibrate_export_to_data_viewer(self) -> dict:
        g = self._cal.geometry_for_viewer()
        if g is None:
            return _err("no completed calibration run yet", kind="no_data")
        self._view.set_geometry(g)
        return _ok({"applied": g})

    # ── Calibrate: working directory ────────────────────────────────
    def op_calibrate_get_working_dir(self) -> dict:
        return _ok({"path": self._cal._out_ed.text()})

    def op_calibrate_set_working_dir(self, path) -> dict:
        self._cal._out_ed.setText(path)
        return _ok({"path": self._cal._out_ed.text()})

    def op_calibrate_suggest_working_dir(self) -> dict:
        """Fill in the ``<expid>_bc`` analysis folder derived from the
        currently loaded data path — same as clicking "Suggest" next to the
        Working dir field."""
        self._cal._apply_suggested_working_dir()
        return _ok({"path": self._cal._out_ed.text()})

    # ── Calibrate: save ──────────────────────────────────────────────
    def op_calibrate_save_json(self, path) -> dict:
        tab = self._cal
        if tab._result is None:
            return _err("no completed calibration run yet", kind="no_data")
        return _ok({"path": tab._save_json_to(path)})

    def op_calibrate_save_paramstest(self, out_path, template_path=None) -> dict:
        tab = self._cal
        if tab._result is None:
            return _err("no completed calibration run yet", kind="no_data")
        tab._save_paramstest_to(out_path, template_path)
        return _ok({"out_path": out_path})

    def op_calibrate_get_state(self) -> dict:
        return _ok(_jsonable(self._cal.get_state(), stem="cal_state"))

