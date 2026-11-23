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
import time
import traceback
import uuid
from pathlib import Path
from typing import Optional

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
                "frame_patches": "_ov_patch"}
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
    def _sc_launch(self, stage: str, starter) -> Optional[dict]:
        """Run ``starter()`` (a tab ``_run_*`` method). Returns None when a worker
        started, else a structured error (a recorded guard failure / no-worker
        outcome / a stage already running)."""
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
        return None

    def _sc_start(self, stage: str, starter) -> dict:
        """Launch one stage and register the job that ``stageFinished`` resolves."""
        if self._sc_chain is not None:
            return _err("a domain loop is running; wait for it (or cancel it) first",
                        kind="precondition_failed")
        err = self._sc_launch(stage, starter)
        if err is not None:
            return err
        job_id = str(uuid.uuid4())
        self._jobs[job_id] = {"status": "running", "stage": stage, "result": None, "error": None}
        self._sc_job_id = job_id
        return _ok({"job_id": job_id, "stage": stage})

    def _on_sc_stage_finished(self, stage: str, outcome: dict) -> None:
        chain = self._sc_chain
        if chain is not None and chain["awaiting"]:
            chain["awaiting"] = False
            self._sc_chain_after_stage(stage, outcome)
            return
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
            if stage == "tune":
                result.update(self._sc_tune_payload())
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
        out = {"index": i, "label": d["label"], "method": d["method"],
               "n_spots": int(len(d["claimed_index"])),
               "selected": i == self._sc._current_domain_index(),
               **_jsonable(keep, stem=f"domain{i}")}
        try:
            from midas_gui.solve_cell import cell_report as cell_rep
            out["volume_A3"] = cell_rep.cell_volume(r["cell"])
            best = cell_rep.closest_known(r["cell"])
            if best:
                out["closest_known_lattice"] = {
                    "name": best["name"], "verdict": best["verdict"], "score_pct": best["score"],
                    "d_len_pct": best["d_len_pct"], "d_ang_deg": best["d_ang_deg"],
                    "d_vol_pct": best["d_vol_pct"],
                    "note": "lattice agreement only (no intensities); deviations = refined - reference"}
        except Exception:
            pass
        return out

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
                          "last_idx": card._last_idx() if card._last_idx() < card._LAST_IDX_OPEN else None,
                          "field": card._ome_field or None},
                "fields": {"dark": self._sc_field_state(card.loader._dark_sel),
                           "bright": self._sc_field_state(card.loader._bright_sel),
                           "background": self._sc_field_state(card.loader._bg_sel)},
                "masks": self._sc_mask_state(card.loader, load=False)["sources"],
            }
        params = {
            "project_dir": sc._proj_ed.text().strip() or None,
            "ingest": {"low_count_threshold": sc._low_count.value(), "mask_grow": sc._mask_grow.value(),
                       "blob_threshold": sc._blob_thresh.value(), "blob_min_vol": sc._blob_minvol.value(),
                       "blob_min_frames": sc._blob_minframes.value(),
                       "split_ratio": sc._split_ratio.value(),
                       "gap_bridge": sc._gap_bridge.value() if sc._gap_bridge_on.isChecked() else 0,
                       "sector_candidates": list(sc._sector_candidates_cfg()),
                       "max_workers": sc._ingest_max_workers.value()},
            "material_filter": sc._material_panel.get_state(),
            "index": {"method": "known_structure" if sc._index_method.currentIndex() else "free",
                      "sigma_g": sc._sigma_g.value(), "min_reflections": sc._min_refl.value(),
                      "tol": sc._tol_override.value(), "known_structure": _sc_ks_state(sc._ks3)},
            "index_remaining": {"method": "known_structure" if sc._leftover_method.currentIndex() else "free",
                                "known_structure": _sc_ks_state(sc._ks5)},
        }
        running = sc._worker is not None and sc._worker.isRunning()
        chain = self._sc_chain
        return _ok({"panels": panels, "params": params, "running": running,
                    "tuning": {"has_suggestion": sc._tune_result is not None,
                               "confidence": (sc._tune_result or {}).get("confidence"),
                               "suggestion": (sc._tune_result or {}).get("suggestion")},
                    "domain_loop": None if chain is None else {
                        "job_id": chain["job_id"], "steps_done": len(chain["history"]),
                        "cancel_requested": chain["cancel"]},
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

    # ── data inputs: fields (dark/bright/background), masks, read-back ──
    @staticmethod
    def _sc_field_state(sel) -> dict:
        """A FieldSelector's saved state plus whether its field is computed. Computing
        runs in a background worker, so a caller polls ``pending`` (via
        solve_cell.panel_get_inputs) until it is False before ingesting."""
        st = sel.get_state()
        st["pending"] = bool(sel.has_pending())
        st["computed"] = bool(sel.isChecked() and not sel.has_pending())
        st["status"] = sel._status.text()
        return st

    @staticmethod
    def _sc_mask_state(loader, *, load: bool) -> dict:
        ms = loader._mask_sel
        out = {"sources": [{"kind": e["kind"], "path": e["path"],
                            "enabled": bool(e.get("enabled", True))} for e in ms._sources]}
        if load:   # composite_mask() loads every enabled file/folder: only when asked
            m = loader.composite_mask()
            out["composite_masked_px"] = None if m is None else int((m != 0).sum())
            out["shape"] = None if m is None else list(m.shape)
            out["warning"] = ms._mask_warning
        return out

    @staticmethod
    def _sc_omega_state(card) -> dict:
        last = card._last_idx()
        return {"start": card._ome_start.value(), "end": card._ome_end.value(),
                "step": card._ome_step.value(), "first_idx": card._ome_first_idx.value(),
                "last_idx": last if last < card._LAST_IDX_OPEN else None,
                "field": card._ome_field or None,
                "span_without_step": bool(card.omega_span_without_step())}

    def _sc_panel_inputs(self, card, *, load_mask: bool) -> dict:
        loader = card.loader
        st = loader.get_state()
        return {
            "panel_id": card.panel_id(),
            "data": {"path": loader.data_path(), "dataset": str(st.get("dataset", "")).split("   ")[0].strip(),
                     "n_frames": loader.n_frames(), "source_kind": loader.data_source_kind()},
            "fields": {"dark": self._sc_field_state(loader._dark_sel),
                       "bright": self._sc_field_state(loader._bright_sel),
                       "background": self._sc_field_state(loader._bg_sel)},
            "masks": self._sc_mask_state(loader, load=load_mask),
            "calibration": {"path": card._calib_path, "loaded": card.has_calibration(),
                            "geometry": _jsonable(card.geometry_cfg(), stem="geometry")
                            if card.has_calibration() else None},
            "omega": self._sc_omega_state(card),
        }

    def op_solve_cell_panel_get_inputs(self, panel_id=None) -> dict:
        """Everything that feeds a panel's ingest: data file/dataset, dark/bright/
        background (path, dataset, mean range, mode, scale, computed?), mask sources and the
        composite mask size, calibration, and the frame↔omega mapping."""
        try:
            card = self._sc_panel(panel_id)
        except ValueError as e:
            return _err(e, kind="bad_args")
        return _ok(self._sc_panel_inputs(card, load_mask=True))

    def op_solve_cell_panel_set_field(self, role, path, checked: bool = True, mode=None,
                                      panel_id=None, dataset=None, start=None, end=None,
                                      scale=None) -> dict:
        try:
            card = self._sc_panel(panel_id)
        except ValueError as e:
            return _err(e, kind="bad_args")
        if scale is not None and role != "background":
            return _err("scale applies to the background field only", kind="bad_args")
        try:
            extra = {"dataset": None if dataset is None else str(dataset),
                     "start": None if start is None else int(start),
                     "end": None if end is None else int(end),
                     "scale": None if scale is None else float(scale)}
        except (TypeError, ValueError) as e:
            return _err(e, kind="bad_args")
        res = self._set_field(card.loader, role, path, checked, mode, **extra)
        if res.get("ok"):
            sel = {"dark": card.loader._dark_sel, "bright": card.loader._bright_sel,
                   "background": card.loader._bg_sel}[role]
            res["result"].update({"panel_id": card.panel_id(), "field": self._sc_field_state(sel)})
        return res

    def op_solve_cell_panel_set_masks(self, sources, mode: str = "replace", panel_id=None) -> dict:
        """Set a panel's detector-mask sources (OR-ed into one mask, as in the GUI).
        ``sources``: list of {kind: "file"|"folder", path, enabled} (a bare string is a
        file). ``mode`` "replace" (default; [] clears every mask) or "append"."""
        try:
            card = self._sc_panel(panel_id)
        except ValueError as e:
            return _err(e, kind="bad_args")
        if mode not in ("replace", "append"):
            return _err("mode must be 'replace' or 'append'", kind="bad_args")
        if not isinstance(sources, (list, tuple)):
            return _err("sources must be a list", kind="bad_args")
        norm = []
        for item in sources:
            item = {"kind": "file", "path": item} if isinstance(item, str) else dict(item or {})
            kind, path = item.get("kind", "file"), item.get("path")
            if kind not in ("file", "folder") or not path:
                return _err(f"bad mask source {item!r}: need kind 'file'|'folder' and a path",
                            kind="bad_args")
            p = Path(str(path)).expanduser()
            if not (p.is_file() if kind == "file" else p.is_dir()):
                return _err(f"mask {kind} not found: {path}", kind="bad_args")
            norm.append({"kind": kind, "path": str(p), "enabled": bool(item.get("enabled", True))})
        ms = card.loader._mask_sel
        if mode == "replace":
            for e in [e for e in ms._sources if e["kind"] != "tab1"]:
                ms._remove(e)
        for n in norm:
            have = next((e for e in ms._sources
                         if e["kind"] == n["kind"] and e["path"] == n["path"]), None)
            if have is None:
                ms._add_source(n["kind"], n["path"], enabled=n["enabled"])
            elif bool(have.get("enabled", True)) != n["enabled"]:
                have["checkbox"].setChecked(n["enabled"])
        out = self._sc_mask_state(card.loader, load=True)
        out["panel_id"] = card.panel_id()
        return _ok(out)

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
                                      panel_id=None, field=None, auto_first_idx=False) -> dict:
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
        if field is not None:
            card._ome_field = str(field)
        if auto_first_idx:        # needs start/end omega (above) and the HDF5 omega field
            card._last_agent_error = None
            if not card.detect_first_idx(card._ome_field):
                return _err(card._last_agent_error or "first-frame detection failed",
                            kind="precondition_failed")
        out = self._sc_omega_state(card)
        out["panel_id"] = card.panel_id()
        out["omega_span_without_step"] = out["span_without_step"]
        return _ok(out)

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
                                        blob_threshold=None, blob_min_vol=None, blob_min_frames=None,
                                        split_ratio=None,
                                        gap_bridge=None, sector_candidates=None,
                                        max_workers=None) -> dict:
        sc = self._sc
        for value, widget, cast in (
                (low_count_threshold, sc._low_count, float), (mask_grow, sc._mask_grow, int),
                (blob_threshold, sc._blob_thresh, float), (blob_min_vol, sc._blob_minvol, int),
                (blob_min_frames, sc._blob_minframes, int),
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

    def op_solve_cell_set_material_filter(self, enabled=None, materials=None, n_mc=None, rng_seed=None) -> dict:
        """Stage 2 inputs. ``materials`` replaces the whole list: dicts with ``name``, ``cell``
        [a,b,c,alpha,beta,gamma], ``space_group``, ``extinction`` (space_group|none|diamond_basis),
        ``tol_deg``, ``hkl_max``, ``mode`` (2theta|orientation), ``orient_tol_deg``, ``min_spots``,
        ``max_crystals``, ``min_peak_counts``, ``protect_if_indexed``, ``protect_tol``, ``enabled``
        (omitted keys keep their defaults); a string entry names a preset."""
        from midas_gui.solve_cell import material_filter as mf
        panel = self._sc._material_panel
        if materials is not None:
            try:
                mats = [(mf.preset(m).to_dict() if isinstance(m, str) else mf.Material.from_dict(m).to_dict())
                        for m in materials]
            except (KeyError, ValueError, TypeError) as exc:
                return _err(f"bad material: {exc}", kind="bad_args")
            panel.set_materials(mats)
        state = panel.get_state()
        if n_mc is not None:
            state["n_mc"] = int(n_mc)
        if rng_seed is not None:
            state["rng_seed"] = int(rng_seed)
        if enabled is not None:
            state["enabled"] = bool(enabled)
        panel.set_state(state)
        return _ok({"material_filter": panel.get_state()})

    def op_solve_cell_set_diamond_params(self, diamond_a=None, tol_deg=None) -> dict:
        """Kept for older callers: edits the entry named "Diamond" (adds the default one if absent)."""
        from midas_gui.solve_cell import material_filter as mf
        panel = self._sc._material_panel
        mats = panel.materials()
        i = next((k for k, m in enumerate(mats) if m["name"] == "Diamond"), None)
        if i is None:
            mats.insert(0, mf.preset("Diamond").to_dict()); i = 0
        if diamond_a is not None:
            mats[i]["cell"] = [float(diamond_a)] * 3 + [90.0] * 3
        if tol_deg is not None:
            mats[i]["tol_deg"] = float(tol_deg)
        panel.set_materials(mats)
        return _ok({"diamond_a": mats[i]["cell"][0], "tol_deg": mats[i]["tol_deg"]})

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

    def op_solve_cell_run_material_filter(self) -> dict:
        return self.op_solve_cell_run_diamond_filter()

    def op_solve_cell_run_index(self) -> dict:
        return self._sc_start("index", self._sc._run_index)

    def op_solve_cell_run_refine(self) -> dict:
        return self._sc_start("refine", self._sc._run_refine)

    def op_solve_cell_run_index_remaining(self) -> dict:
        return self._sc_start("index_remaining", self._sc._run_index_remaining)

    # ── ingest-parameter suggestion (advisory; see solve_cell/tuning.py) ──
    def _sc_tune_payload(self) -> dict:
        r = self._sc._tune_result
        if not r:
            return {}
        from midas_gui.solve_cell import tuning
        return {"suggestion": r["suggestion"], "confidence": r["confidence"],
                "warnings": list(r["warnings"]), "report": tuning.format_report(r),
                "evidence": _jsonable(r["evidence"], stem="tune"), "timings": r["timings"],
                "n_frames": r.get("n_frames")}

    def op_solve_cell_suggest_parameters(self, panel_id=None) -> dict:
        """Start the parameter search on a panel (the active one by default). Async: the job
        result carries the suggestion, confidence, warnings, evidence and a text report.
        Nothing is changed until solve_cell.apply_suggested_parameters."""
        sc = self._sc
        if panel_id is not None:
            if int(panel_id) not in sc._panels:
                return _err(f"no panel {panel_id}", kind="bad_args")
            if not (sc._worker is not None and sc._worker.isRunning()):
                sc._panel_tabs.setCurrentWidget(sc._panels[int(panel_id)])
        return self._sc_start("tune", sc._run_tune)

    def op_solve_cell_get_suggestion(self) -> dict:
        payload = self._sc_tune_payload()
        if not payload:
            return _err("no suggestion yet — run solve_cell.suggest_parameters first", kind="no_data")
        return _ok(payload)

    def op_solve_cell_apply_suggested_parameters(self) -> dict:
        sc = self._sc
        applied = sc.apply_suggested_parameters()
        if not applied:
            return _err("no suggestion yet — run solve_cell.suggest_parameters first", kind="no_data")
        return _ok({"applied": applied,
                    "ingest": self.op_solve_cell_get_state()["result"]["params"]["ingest"]})

    # ── domain loop: index -> refine Domain 1, then index-remaining until nothing new ──
    _CHAIN_RETRY_S = 20.0

    def op_solve_cell_run_domain_loop(self, max_domains: int = 5, include_first_domain: bool = True,
                                      fill_from_latest: bool = True,
                                      min_remaining_spots=None) -> dict:
        """Run the solve sequence as ONE async job: (when no domain exists) index → refine
        Domain 1, then repeat "index remaining spots" — each found domain is refined as part
        of that stage — until no new domain is found, ``max_domains`` is reached, or too few
        spots remain. With a known-structure "remaining" method, ``fill_from_latest`` copies the
        latest domain's cell into it before each pass. Needs a finished ingest + material
        filter. The job result lists every step and the stop reason."""
        sc = self._sc
        if self._sc_chain is not None:
            return _err("a domain loop is already running", kind="precondition_failed")
        try:
            max_domains = int(max_domains)
            floor = None if min_remaining_spots is None else int(min_remaining_spots)
        except (TypeError, ValueError) as e:
            return _err(e, kind="bad_args")
        if max_domains < 1:
            return _err("max_domains must be >= 1", kind="bad_args")
        if sc._candidate_df is None:
            return _err("no candidate pool: run ingest and the material filter first",
                        kind="precondition_failed")
        n0 = len(sc._domains)
        if n0 == 0 and not include_first_domain:
            return _err("no solved domain yet; set include_first_domain=True", kind="bad_args")
        job_id = str(uuid.uuid4())
        self._sc_chain = {"job_id": job_id, "max_domains": max_domains, "fill": bool(fill_from_latest),
                          "min_remaining": floor, "need_first": n0 == 0 and bool(include_first_domain),
                          "history": [], "awaiting": False, "cancel": False, "n_domains_start": n0,
                          "retry_until": time.monotonic() + self._CHAIN_RETRY_S}
        self._jobs[job_id] = {"status": "running", "stage": "domain_loop", "result": None, "error": None}
        err = self._sc_chain_launch()
        if err is not None:
            self._sc_chain = None
            self._jobs.pop(job_id, None)
            return err
        return _ok({"job_id": job_id, "stage": "domain_loop"})

    def op_solve_cell_cancel_domain_loop(self) -> dict:
        """Stop the domain loop after the stage in flight finishes (a running stage cannot
        be interrupted)."""
        c = self._sc_chain
        if c is None:
            return _err("no domain loop is running", kind="precondition_failed")
        c["cancel"] = True
        if not c["awaiting"]:
            self._sc_chain_finish(stop_reason="cancelled")
            return _ok({"job_id": c["job_id"], "note": "cancelled"})
        return _ok({"job_id": c["job_id"], "note": "will stop after the current stage finishes"})

    def _sc_chain_plan(self):
        """-> (stage, None) to run next, or (None, stop_reason)."""
        c, sc = self._sc_chain, self._sc
        if c["cancel"]:
            return None, "cancelled"
        hist = c["history"]
        last = hist[-1] if hist else None
        n_dom = len(sc._domains)
        if c["need_first"] and n_dom == 0:
            if last is None:
                return "index", None
            ab = sc._ab_initio_raw
            if (last["stage"] == "index" and sc._index_method.currentIndex() == 0
                    and ab is not None and getattr(ab, "success", False)):
                return "refine", None
            return None, "first_domain_not_found"
        if n_dom >= c["max_domains"]:
            return None, "max_domains_reached"
        if last is not None and last["stage"] == "index_remaining" and n_dom <= last["n_domains_before"]:
            return None, "no_new_domain"
        leftover = sc._leftover_df()
        n_left = 0 if leftover is None else len(leftover)
        floor = c["min_remaining"]
        if floor is None:
            floor = (sc._min_refl.value() if sc._leftover_method.currentIndex() == 0
                     else sc._ks5["min_accept"].value())
        if n_left < max(1, int(floor)):
            return None, "too_few_remaining_spots"
        return "index_remaining", None

    def _sc_chain_launch(self) -> Optional[dict]:
        """Start the next step. Returns an error dict if the launch was refused, else None
        (a stage was started, or the loop ended and its job is already resolved)."""
        c, sc = self._sc_chain, self._sc
        stage, stop = self._sc_chain_plan()
        if stage is None:
            self._sc_chain_finish(stop_reason=stop)
            return None
        starters = {"index": sc._run_index, "refine": sc._run_refine,
                    "index_remaining": sc._run_index_remaining}
        if stage == "index_remaining" and c["fill"] and sc._leftover_method.currentIndex() == 1:
            sc._fill_ks5_from_latest_domain()
        c["history"].append({"stage": stage, "n_domains_before": len(sc._domains)})
        c["awaiting"] = True
        err = self._sc_launch(stage, starters[stage])
        if err is not None:
            c["awaiting"] = False
            c["history"].pop()
        return err

    def _sc_chain_step(self) -> None:
        """Timer-driven next step. The finished stage's thread may still be exiting when its
        ``stageFinished`` arrives, so wait (bounded) for it instead of failing the launch."""
        from PyQt5 import QtCore
        c, sc = self._sc_chain, self._sc
        if c is None:
            return
        if sc._worker is not None and sc._worker.isRunning():
            if time.monotonic() < c["retry_until"]:
                QtCore.QTimer.singleShot(50, self._sc_chain_step)
            else:
                self._sc_chain_finish(error="the previous stage's worker did not stop")
            return
        err = self._sc_chain_launch()
        if err is not None:
            self._sc_chain_finish(error=err["error"]["message"])

    def _sc_chain_after_stage(self, stage: str, outcome: dict) -> None:
        from PyQt5 import QtCore
        c, sc = self._sc_chain, self._sc
        h = c["history"][-1]
        leftover = sc._leftover_df()
        h.update({"ok": bool(outcome.get("ok")), "note": outcome.get("note", ""),
                  "n_domains_after": len(sc._domains),
                  "n_remaining": None if leftover is None else int(len(leftover))})
        if not outcome.get("ok"):
            h["error"] = outcome.get("error")
            self._sc_chain_finish(error=f"{h['stage']} failed: {outcome.get('error')}")
            return
        c["retry_until"] = time.monotonic() + self._CHAIN_RETRY_S
        QtCore.QTimer.singleShot(0, self._sc_chain_step)

    def _sc_chain_finish(self, *, stop_reason: Optional[str] = None, error: Optional[str] = None) -> None:
        c, self._sc_chain = self._sc_chain, None
        if c is None:
            return
        try:
            result = self._sc_results_summary()
        except Exception:
            result = {}
        n_new = len(self._sc._domains) - c["n_domains_start"]
        result.update({"stop_reason": stop_reason, "steps": c["history"], "n_new_domains": n_new})
        if error:
            self._jobs[c["job_id"]] = {"status": "failed", "stage": "domain_loop",
                                       "result": result, "error": error}
        else:
            result["note"] = f"{n_new} new domain(s); stopped: {stop_reason}"
            self._jobs[c["job_id"]] = {"status": "done", "stage": "domain_loop",
                                       "result": result, "error": None}

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


class _ProjectOps:
    """``project.*`` ops: File ▸ Open Project / Save Project (As) without the
    dialogs. ``MainWindow._project_msg`` records its message boxes instead of
    showing them while ``_agent_quiet`` is set, so nothing here can block."""

    #: ``project.open`` ``exclude`` key -> the value that un-selects it.
    _OPEN_EXCLUDABLE = {"workspace_tabs": [], "calib_refs": {}, "integrate_refs": {},
                        "solve_cell": False, "mask_ref": None}

    def _project_quiet(self, fn):
        """Run ``fn()`` with the window's project dialogs recorded, not shown.
        Returns ``(errors, infos)`` — the recorded message texts by kind."""
        win = self._win
        win._agent_quiet, win._agent_messages = True, []
        try:
            fn()
            msgs = list(win._agent_messages)
        finally:
            win._agent_quiet, win._agent_messages = False, []
        return ([f"{t}: {x}" for k, t, x in msgs if k == "error"],
                [x for k, _t, x in msgs if k == "info"])

    def op_project_get_info(self) -> dict:
        from midas_gui import project
        win = self._win
        path = getattr(getattr(win, "_project_ctx", None), "path", None)
        info = {"path": str(path) if path else None,
                "name": Path(path).name if path else None,
                "unsaved_changes": bool(getattr(win, "_workspace_dirty", False))}
        if path and Path(path).exists():
            info["analysis_summary"] = project.analysis_summary(path)
        return _ok(info)

    def op_project_save(self, path=None, overwrite=False, history="none") -> dict:
        """Save the session. No ``path``: overwrite the open project (same as
        Ctrl+S). With ``path``: Save As — a fresh project file there (an
        existing file is refused unless ``overwrite``, which keeps a ``.bak``).
        ``history`` (none/latest/all) is how much of the previously open
        project's analysis history a Save As carries over."""
        from midas_gui import project
        win = self._win
        current = getattr(getattr(win, "_project_ctx", None), "path", None)
        if history not in ("none", "latest", "all"):
            return _err(f"unknown history {history!r} (none/latest/all)", kind="bad_args")
        if path is not None:
            path = str(path)
            if not path.lower().endswith(".h5"):
                path += ".h5"
        target = path or current
        if not target:
            return _err("no project is open — pass path", kind="precondition_failed")
        is_save_as = bool(path) and str(path) != str(current)
        if is_save_as:
            exists = Path(path).exists()
            if exists and not overwrite:
                return _err(f"{path} already exists — pass overwrite=true to replace it",
                            kind="precondition_failed")
            try:
                project.create_project(path, overwrite=exists)
            except Exception as e:
                return _err(f"could not create {path}: {e}", kind="precondition_failed")
        errors, infos = self._project_quiet(lambda: win.save_project(target))
        if errors:
            return _err("; ".join(errors), kind="precondition_failed")
        copied = {}
        if is_save_as and current and history != "none" and Path(current).exists():
            try:
                copied = project.copy_analysis_history(current, path, history)
            except Exception as e:
                return _err(f"saved, but copying analysis history failed: {e}",
                            kind="exception")
        return _ok({"path": str(target), "save_as": is_save_as, "history_copied": copied,
                    "note": infos[-1] if infos else None})

    def op_project_open(self, path, discard_unsaved=False, exclude=None) -> dict:
        """Open a project ``.h5`` and restore everything in it (workspace
        tabs, calibrate/integrate attempts, Solve Cell, mask) — same as File ▸
        Open Project with every box ticked. ``exclude`` lists parts to leave
        out: workspace_tabs, calib_refs, integrate_refs, solve_cell, mask_ref.
        Refused while the session has unsaved changes unless
        ``discard_unsaved`` (save first with ``project.save`` to keep them)."""
        from midas_gui.dialogs import ProjectContentsPicker
        win = self._win
        path = str(path)
        if not Path(path).is_file():
            return _err(f"no such file: {path}", kind="bad_args")
        exclude = list(exclude or [])
        bad = [k for k in exclude if k not in self._OPEN_EXCLUDABLE]
        if bad:
            return _err(f"unknown exclude key(s) {bad} (one of "
                        f"{sorted(self._OPEN_EXCLUDABLE)})", kind="bad_args")
        if getattr(win, "_workspace_dirty", False):
            if not discard_unsaved:
                return _err("the session has unsaved changes — save them with "
                            "project.save, or pass discard_unsaved=true",
                            kind="precondition_failed")
            win._clear_autosave_draft()
        picker = ProjectContentsPicker()
        try:
            if not (picker.set_project(path) and picker.has_content()):
                return _err(picker.error_message()
                            or f"{path} has nothing this version can restore",
                            kind="precondition_failed")
            sel = picker.selection()
        finally:
            picker.deleteLater()
        for key in exclude:
            sel[key] = self._OPEN_EXCLUDABLE[key]
        errors, infos = self._project_quiet(lambda: win._open_project_selection(path, sel))
        if errors:
            return _err("; ".join(errors), kind="precondition_failed")
        return _ok({"path": path, "restored": infos,
                    "selection": {k: bool(v) for k, v in sel.items()}})


# ── Hydra Batch Integrate ────────────────────────────────────────────
# Drives the Batch Integrate tab's Hydra page (``HydraBatchPage``) through its
# own widgets and ``_run_all``, so an agent run and a click run are the same
# code. Settings persist in the page between calls (like every other tab);
# ``run_queue`` is for several datasets that each need their own calibration
# set, because the page itself holds one calibration per panel for a whole
# multi-scan run.

_HB_PANELS = (1, 2, 3, 4)
_HB_CAKE_KEYS = {"r_min": "R_MIN", "r_max": "R_MAX", "r_bin": "R_STEP",
                 "eta_min": "ETA_MIN", "eta_max": "ETA_MAX", "eta_bin": "ETA_STEP"}


def _hb_panel_list(panels) -> list:
    """``None`` -> all four; else ints from ``[1,2]`` / ``["ge1","ge2"]``."""
    if panels is None:
        return list(_HB_PANELS)
    out = []
    for p in panels:
        n = int(str(p).lower().replace("ge", ""))
        if n not in _HB_PANELS:
            raise ValueError(f"unknown panel {p!r} (ge1..ge4)")
        out.append(n)
    return out


class _HydraBatchOps:
    @property
    def _hb_tab(self):
        return self._win._batch_tab

    def _hb_page(self):
        """The Hydra page, with the tab switched into Hydra mode (built lazily)."""
        tab = self._hb_tab
        page = tab._ensure_hydra_page()
        tab._mode_ribbon.set_mode("hydra")
        return page

    # ── applying settings (shared by the ops and run_queue) ─────────
    def _hb_apply_scans(self, page, paths, output_dir=None, dark=None) -> list:
        from midas_gui import hydra
        if not paths:
            raise ValueError("paths must be a non-empty list of ge1..ge4 panel files "
                             "(any one panel per scan; siblings are found automatically)")
        bad = [str(q) for q in paths if not Path(q).is_file()]
        if bad:
            raise ValueError(f"file(s) not found: {bad[:3]}")
        notpanel = [str(q) for q in paths if hydra.panel_for_file(q) is None]
        if notpanel:
            raise ValueError(f"no ge1..ge4 token in path, cannot place on a panel: {notpanel[:3]}")
        page._loader.set_selection([str(q) for q in paths])
        if output_dir is not None:
            page._out_ed.setText(str(output_dir))
        if dark is not None:
            sel = page._loader._dark_sel
            if dark == "" or dark is False:
                sel.set_state({"checked": False})
            else:
                if not Path(dark).is_file():
                    raise ValueError(f"dark file not found: {dark}")
                sel.set_state({"checked": True, "path": str(dark),
                               "dataset": "exchange/data"})
        return self._hb_plan(page)

    def _hb_apply_calibration(self, page, files=None, from_file=None) -> dict:
        from midas_gui import hydra
        if (files is None) == (from_file is None):
            raise ValueError("give exactly one of files={'ge1': path, ...} or from_file=<one panel's file>")
        if from_file is not None:
            picked = hydra.panel_for_file(from_file)
            if picked is None:
                raise ValueError(f"{from_file}: no ge1..ge4 token to derive the other panels from")
            files = {}
            for n in _HB_PANELS:
                cand = from_file if n == picked else hydra.sibling_for_panel(from_file, n)
                if cand and Path(cand).exists():
                    files[f"ge{n}"] = cand
        out = {}
        for key, path in files.items():
            n = _hb_panel_list([key])[0]
            if not Path(path).is_file():
                raise ValueError(f"ge{n}: calibration file not found: {path}")
            page._cards[n].set_calibration_file(str(path))
            out[f"ge{n}"] = str(path)
        return out

    def _hb_apply_caking(self, page, caking: dict, panels=None) -> dict:
        bad = sorted(set(caking) - set(_HB_CAKE_KEYS))
        if bad:
            raise ValueError(f"unknown caking key(s) {bad} (allowed: {sorted(_HB_CAKE_KEYS)})")
        vals = {_HB_CAKE_KEYS[k]: float(v) for k, v in caking.items() if v is not None}
        for n in _hb_panel_list(panels):
            page._cards[n].set_caking(vals)
        return {f"ge{n}": page._cards[n].caking() for n in sorted(page._cards)}

    def _hb_apply_options(self, page, o: dict) -> dict:
        o = dict(o)
        if "kernel" in o:
            i = page._kernel.findData(o.pop("kernel"))
            if i < 0:
                raise ValueError("kernel must be one of "
                                 f"{[page._kernel.itemData(j) for j in range(page._kernel.count())]}")
            page._kernel.setCurrentIndex(i)
        if "formats" in o:
            want = set(o.pop("formats"))
            checks = page._fmt._checks
            unknown = sorted(want - set(checks))
            if unknown:
                raise ValueError(f"unknown output format(s) {unknown} (one of {sorted(checks)})")
            for k, cb in checks.items():
                cb.setChecked(k in want)
        for key, combo in (("run_mode", page._run_mode_combo),
                           ("frame_run_mode", page._frame_run_mode),
                           ("zarr_grouping", page._zarr_grouping)):
            if key in o:
                i = combo.findData(o.pop(key))
                if i < 0:
                    raise ValueError(f"unknown {key}; one of "
                                     f"{[combo.itemData(j) for j in range(combo.count())]}")
                combo.setCurrentIndex(i)
        if "combine_zarr" in o:
            page._combine_zarr_chk.setChecked(bool(o.pop("combine_zarr")))
        if "n_workers" in o:
            page._n_workers.setValue(int(o.pop("n_workers")))
        ld = page._loader
        for key, spin in (("frame_start", "_fr_start"), ("frame_end", "_fr_end"),
                          ("frame_stride", "_fr_stride")):
            if key in o:
                w = getattr(ld, spin)
                v = int(o.pop(key))
                w.setMaximum(max(w.maximum(), v))
                w.setValue(v)
        if o:
            raise ValueError(f"unknown option(s) {sorted(o)} (allowed: kernel, formats, run_mode, "
                             "frame_run_mode, zarr_grouping, combine_zarr, n_workers, "
                             "frame_start, frame_end, frame_stride)")
        return self._hb_options(page)

    # ── reading state ───────────────────────────────────────────────
    def _hb_plan(self, page) -> list:
        return [{"out_dir": out, "panels": {f"ge{n}": [str(x) for x in ps]
                                            for n, ps in sorted(panels.items())}}
                for out, panels in page._scan_plan()]

    def _hb_options(self, page) -> dict:
        ld = page._loader
        return {"kernel": page._kernel.currentData(),
                "formats": list(page._fmt.checked_keys()),
                "run_mode": page._run_mode_combo.currentData(),
                "frame_run_mode": page._frame_run_mode.currentData(),
                "zarr_grouping": page._zarr_grouping.currentData(),
                "combine_zarr": page._combine_zarr_chk.isChecked(),
                "n_workers": page._n_workers.value(),
                "frame_start": ld._fr_start.value(), "frame_end": ld._fr_end.value(),
                "frame_stride": ld._fr_stride.value(),
                "output_dir": page._out_ed.text().strip()}

    def _hb_panels_state(self, page) -> dict:
        shared = page.shared_auto_rmax()
        out = {}
        for n, card in sorted(page._cards.items()):
            row = {"calibration_source": "file" if card.using_file() else "calibrate_tab",
                   "calibration_file": card.file_path() if card.using_file() else None,
                   "caking": card.caking()}
            try:
                spec = page._spec_for(n)
                row.update(ok=True, Lsd=float(spec.Lsd), pxY=float(spec.pxY),
                           tx=float(spec.tx),
                           wavelength_A=float(spec.Wavelength),
                           r_max_effective=float(getattr(spec, "RMax", 0) or 0) or shared)
            except Exception as e:
                row.update(ok=False, error=str(e))
            out[f"ge{n}"] = row
        return out

    # ── preflight (mirrors the guards in _run_all, which would pop a dialog) ──
    def _hb_preflight(self, page) -> Optional[str]:
        from midas_gui.helpers import check_output_dir_writable
        if page._workers:
            return "a Hydra batch run is already in progress; wait for it or abort"
        plan = page._scan_plan()
        if not plan:
            return "no scans loaded: call hydra_batch_set_scans first"
        if not page._fmt.checked_keys():
            return "no output format selected (hydra_batch_set_options formats=[...])"
        if page._multi_azimuth_chk.isChecked() and page._rebin_cfg():
            return "multi-azimuth output is not supported together with Q/2theta-uniform bins"
        for out, panels in plan:
            if not out:
                return "no output directory: pass output_dir to hydra_batch_set_scans"
            reason = check_output_dir_writable(str(out))
            if reason:
                return f"output folder not writable: {reason}"
            usable = []
            for n in panels:
                try:
                    page._spec_for(n)
                    usable.append(n)
                except Exception as e:
                    return f"ge{n}: {e}"
            if not usable:
                return "no panel has a usable calibration"
        return None

    def _hb_status(self, report: dict) -> str:
        if report.get("running"):
            return "running"
        if report.get("cancelled"):
            return "aborted"
        bad = []
        for sc in report.get("scans", []):
            bad += [k for k, v in sc["panels"].items() if v["status"] != "done"]
            c = sc.get("combine")
            if c and (c["errors"] or c["wrote"] != c["total"]):
                bad.append("combine")
        return "failed" if bad or not report.get("scans") else "done"

    # ── ops ─────────────────────────────────────────────────────────
    def op_hydra_batch_get_state(self) -> dict:
        page = self._hb_page()
        return _ok({"panels": self._hb_panels_state(page),
                    "options": self._hb_options(page),
                    "scans": self._hb_plan(page),
                    "shared_auto_rmax": page.shared_auto_rmax(),
                    "running": bool(page._workers),
                    "report": page.run_report()})

    def op_hydra_batch_set_scans(self, paths, output_dir=None, dark=None) -> dict:
        page = self._hb_page()
        try:
            return _ok({"scans": self._hb_apply_scans(page, paths, output_dir, dark)})
        except ValueError as e:
            return _err(e, kind="bad_args")

    def op_hydra_batch_set_calibration(self, files=None, from_file=None) -> dict:
        page = self._hb_page()
        try:
            return _ok({"calibration": self._hb_apply_calibration(page, files, from_file),
                        "panels": self._hb_panels_state(page)})
        except ValueError as e:
            return _err(e, kind="bad_args")

    def op_hydra_batch_set_caking(self, r_min=None, r_max=None, r_bin=None, eta_min=None,
                                  eta_max=None, eta_bin=None, panels=None) -> dict:
        page = self._hb_page()
        try:
            c = {"r_min": r_min, "r_max": r_max, "r_bin": r_bin,
                 "eta_min": eta_min, "eta_max": eta_max, "eta_bin": eta_bin}
            return _ok({"caking": self._hb_apply_caking(
                page, {k: v for k, v in c.items() if v is not None}, panels),
                "shared_auto_rmax": page.shared_auto_rmax()})
        except ValueError as e:
            return _err(e, kind="bad_args")

    def op_hydra_batch_set_options(self, **options) -> dict:
        page = self._hb_page()
        try:
            return _ok({"options": self._hb_apply_options(page, options)})
        except ValueError as e:
            return _err(e, kind="bad_args")

    def op_hydra_batch_run(self) -> dict:
        page = self._hb_page()
        problem = self._hb_preflight(page)
        if problem:
            return _err(problem, kind="precondition_failed")
        job_id = str(uuid.uuid4())
        self._jobs[job_id] = {"status": "running", "result": None, "error": None, "_hb": True}
        self._hb_job_id = job_id

        def _done(report, job_id=job_id):
            try:                       # one-shot: later runs must not rewrite this job
                page.runFinished.disconnect(_done)
            except Exception:
                pass
            st = self._hb_status(report)
            self._jobs[job_id] = {"status": st, "result": report,
                                  "error": None if st == "done" else f"run {st}", "_hb": True}

        # Connect BEFORE starting: a run whose panels all skip synchronously
        # finishes inside _run_all.
        page.runFinished.connect(_done)
        page._run_all()
        if not page._report.get("running") and self._jobs[job_id]["status"] == "running":
            _done(page.run_report())
        return _ok({"job_id": job_id, "scans": self._hb_plan(page)})

    def op_hydra_batch_get_job(self, job_id) -> dict:
        job = self._jobs.get(job_id)
        if job is None:
            return _err(f"unknown job_id {job_id!r}", kind="bad_args")
        page = self._hb_page()
        out = {k: v for k, v in job.items() if not k.startswith("_")}
        if "queue" in job:
            out["queue"] = job["queue"]
        if job["status"] == "running":
            out["report"] = page.run_report()
            try:
                out["log_tail"] = page._log.toPlainText().splitlines()[-12:]
            except Exception:
                pass
        return _ok(out)

    def op_hydra_batch_abort(self) -> dict:
        page = self._hb_page()
        if self._hb_queue is not None:
            self._hb_queue["abort"] = True
        was = bool(page._workers)
        page._abort_all()
        return _ok({"aborted": was})

    def op_hydra_batch_run_queue(self, jobs, stop_on_error: bool = False) -> dict:
        """Run several datasets back to back, each with its own calibration set.

        Each job: ``{"name", "scans": [panel file, ...], "calibration":
        {"files": {...}} | {"from_file": path}, "output_dir", "dark"?,
        "caking"?, "options"?}``. Everything is validated before anything
        starts; jobs then run one after another through the page."""
        if self._hb_queue is not None and not self._hb_queue.get("finished"):
            return _err("a hydra_batch queue is already running", kind="precondition_failed")
        if not jobs:
            return _err("jobs must be a non-empty list", kind="bad_args")
        page = self._hb_page()
        if page._workers:
            return _err("a Hydra batch run is already in progress", kind="precondition_failed")
        from midas_gui import hydra
        for i, j in enumerate(jobs):
            for key in ("scans", "calibration", "output_dir"):
                if not j.get(key):
                    return _err(f"jobs[{i}] is missing {key!r}", kind="bad_args")
            cal = j["calibration"]
            if (("files" in cal) == ("from_file" in cal)):
                return _err(f"jobs[{i}].calibration needs exactly one of files / from_file",
                            kind="bad_args")
            for q in j["scans"]:
                if not Path(q).is_file() or hydra.panel_for_file(q) is None:
                    return _err(f"jobs[{i}]: bad scan file {q!r}", kind="bad_args")
        qid = str(uuid.uuid4())
        names = [j.get("name") or f"job{i}" for i, j in enumerate(jobs)]
        q = {"id": qid, "jobs": list(jobs), "names": names, "pos": -1, "stop": bool(stop_on_error),
             "abort": False, "finished": False,
             "results": [{"name": n, "status": "pending"} for n in names]}
        self._hb_queue = q
        self._jobs[qid] = {"status": "running", "result": None, "error": None, "_hb": True,
                           "queue": q["results"]}
        page.runFinished.connect(self._hb_queue_on_finished)
        self._hb_queue_next()
        return _ok({"job_id": qid, "jobs": names})

    # queue engine: advance from a QTimer so we never start the next run
    # re-entrantly from inside the previous run's runFinished emission.
    def _hb_queue_on_finished(self, report: dict) -> None:
        q = self._hb_queue
        if q is None or q["finished"] or q["pos"] < 0:
            return
        res = q["results"][q["pos"]]
        res["status"] = self._hb_status(report)
        res["report"] = report
        from PyQt5 import QtCore
        QtCore.QTimer.singleShot(0, self._hb_queue_next)

    def _hb_queue_finish(self) -> None:
        q = self._hb_queue
        q["finished"] = True
        sts = [r["status"] for r in q["results"]]
        st = ("aborted" if q["abort"] else "done" if all(s == "done" for s in sts) else "failed")
        self._jobs[q["id"]] = {"status": st, "result": {"jobs": q["results"]},
                               "error": None if st == "done" else f"queue {st}",
                               "_hb": True, "queue": q["results"]}
        try:
            self._hb_page().runFinished.disconnect(self._hb_queue_on_finished)
        except Exception:
            pass

    def _hb_queue_next(self) -> None:
        q = self._hb_queue
        if q is None or q["finished"]:
            return
        if q["pos"] >= 0 and q["stop"] and q["results"][q["pos"]]["status"] != "done":
            for r in q["results"][q["pos"] + 1:]:
                r["status"] = "skipped"
            return self._hb_queue_finish()
        q["pos"] += 1
        if q["abort"] or q["pos"] >= len(q["jobs"]):
            for r in q["results"][q["pos"]:]:
                if r["status"] == "pending":
                    r["status"] = "skipped"
            return self._hb_queue_finish()
        job, res = q["jobs"][q["pos"]], q["results"][q["pos"]]
        page = self._hb_page()
        try:
            self._hb_apply_scans(page, job["scans"], job["output_dir"], job.get("dark"))
            self._hb_apply_calibration(page, **job["calibration"])
            if job.get("options"):
                self._hb_apply_options(page, job["options"])
            if job.get("caking"):
                self._hb_apply_caking(page, job["caking"])
            problem = self._hb_preflight(page)
        except Exception as e:                      # noqa: BLE001 - reported per job
            problem = str(e)
        if problem:
            res.update(status="failed", error=problem)
            from PyQt5 import QtCore
            QtCore.QTimer.singleShot(0, self._hb_queue_next)
            return
        res["status"] = "running"
        page._run_all()
        if not page._report.get("running") and res["status"] == "running":
            self._hb_queue_on_finished(page.run_report())


# ── Mask Builder ─────────────────────────────────────────────────────
# Mixed into GuiController below. Every op sets the same widget a click would
# and runs the tab's own methods (``_load_image``, ``_compute``, ``_save`` ...),
# so the GUI and an agent cannot diverge. ``MaskTab.set_agent_mode`` turns its
# message boxes into recorded messages (``_agent_msgs``), returned as errors /
# warnings here.

_MASK_PROJECTIONS = ("Current frame", "Average", "Sum", "Max")


class _MaskOps:
    @property
    def _mt(self):
        return self._win._mask_tab

    def _mask_take_msgs(self) -> list:
        msgs = [f"{title}: {text}" for _k, title, text in self._mt._agent_msgs]
        self._mt._agent_msgs.clear()
        return msgs

    @staticmethod
    def _mask_pick_dataset(combo, dataset: str) -> None:
        """Select ``dataset`` in an HDF5 dataset combo (items read "name   (shape)")."""
        for i in range(combo.count()):
            if combo.itemData(i) == dataset:
                combo.setCurrentIndex(i)
                return
        combo.setEditText(dataset)

    def _mask_summary(self) -> dict:
        t = self._mt
        m = t._mask
        out = {"has_mask": m is not None, "status": t._stat_lbl.text()}
        if m is not None:
            n = int(np.count_nonzero(m))
            out.update({"shape": list(m.shape), "n_masked": n, "n_total": int(m.size),
                        "percent_masked": round(100.0 * n / m.size, 4),
                        "n_computed": (int(np.count_nonzero(t._computed_mask))
                                       if t._computed_mask is not None else 0),
                        "n_hand_drawn": (int(np.count_nonzero(t._drawn_mask))
                                         if t._drawn_mask is not None else 0)})
        return out

    def _mask_state(self) -> dict:
        t = self._mt
        img = t._image
        multi = t._img_frames is not None
        image = {"path": t._img_edit.text().strip() or None,
                 "dataset": (t._h5loc_edit.currentText().split("   ")[0].strip()
                             if t._h5loc_edit.isVisibleTo(t) else None),
                 "loaded": img is not None, "n_frames": t._img_frames["n"] if multi else 1,
                 "frame": (t._img_frame_idx + 1) if multi else 1}
        if img is not None:
            image.update({"shape": [int(img.shape[0]), int(img.shape[1])],
                          "dtype": str(t._orig_dtype),
                          "min": float(np.nanmin(img)), "max": float(np.nanmax(img))})
        r = t._calib_result
        proj = getattr(self._win, "_project_ctx", None)
        return {
            "image": image,
            "threshold": {"lower": t._lower.value(), "upper": t._upper.value(),
                          "projection": t._thresh_proj_combo.currentText(),
                          "skip_frames": t._thresh_skip.value(),
                          "note": "lower <= -1e9 disables the lower test, upper <= 0 the upper one"},
            "statistical": {"spatial": t._spatial_check.isChecked(),
                            "temporal": t._temporal_check.isChecked(),
                            "k_sigma": t._k_sigma.value(), "hot_factor": t._hot_factor.value(),
                            "dead_factor": t._dead_factor.value(),
                            "frozen_frac": t._frozen_frac.value(),
                            "gap_guard_px": t._gap_guard.value(),
                            "gap_guard_factor": t._gap_factor.value()},
            "stack": {"path": t._stack_ed.text().strip() or None,
                      "dataset": (t._stack_ds_combo.currentText().split("   ")[0].strip()
                                  if t._stack_ds_row.isVisibleTo(t) else None),
                      "stride": t._stride_spin.value(),
                      "explicit_files": len(t._stack_files) if t._stack_files else 0},
            "spike": {"enabled": t._spike_check.isChecked(), "n_sigma": t._spike_sigma.value()},
            "cosmic_ray": {"enabled": t._cosmic_check.isChecked(), "n_sigma": t._cosmic_sigma.value()},
            "calibration": {"available": r is not None,
                            "Lsd": getattr(r, "Lsd", None) if r is not None else None,
                            "NrPixelsY": getattr(r, "NrPixelsY", None) if r is not None else None,
                            "NrPixelsZ": getattr(r, "NrPixelsZ", None) if r is not None else None,
                            "im_trans": _jsonable(getattr(r, "im_trans", None)) if r is not None else None},
            "azimuthal": {"enabled": t._azim_check.isChecked(), "n_sigma": t._azim_sigma.value()},
            "learnable": {"enabled": t._learn_check.isChecked(), "steps": t._learn_steps.value(),
                          "lr": t._learn_lr.value(), "sparsity": t._learn_sparsity.value()},
            "postprocess": {"dilation": t._dilation_spin.value()},
            "display": {"show_overlay": t._show_overlay_check.isChecked(),
                        "draw_mask": t._draw_check.isChecked(),
                        "log": t._viewer._log.isChecked(), "cmap": t._viewer._cmap.currentText()},
            "eraser": {"enabled": t._erase_check.isChecked(), "size": t._erase_slider.value(),
                       "undo_depth": len(t._erase_undo)},
            "save_path": t._save_edit.text().strip() or None,
            "mask": self._mask_summary(),
            "shapes": t.agent_list_shapes(),
            "running": bool(t._mask_worker is not None and t._mask_worker.isRunning()),
            "project": str(proj.path) if proj and getattr(proj, "path", None) else None,
        }

    def _mask_reply(self, section: str | None = None, **extra) -> dict:
        """Success reply: the full state (or one section), plus any recorded dialogs."""
        st = self._mask_state()
        out = {section: st[section]} if section else st
        out.update(extra)
        msgs = self._mask_take_msgs()
        if msgs:
            out["messages"] = msgs
        return _ok(out)

    def _mask_fail(self, message: str, kind: str = "precondition_failed") -> dict:
        msgs = self._mask_take_msgs()
        if msgs:
            message = f"{message} ({'; '.join(msgs)})"
        return _err(message, kind=kind)

    # ── state ──
    def op_mask_get_state(self) -> dict:
        return self._mask_reply()

    # ── image / frame ──
    def op_mask_set_image(self, path, dataset=None, frame=None) -> dict:
        """Image field: a TIFF/HDF5/.ge file, a folder of single-frame files, or a
        multi-frame file. ``frame`` is 1-based (the Frame spinner). Loading resets the
        upper threshold to the dtype sentinel, so set the threshold AFTER the image."""
        t = self._mt
        path = str(path)
        if not Path(path).exists():
            return _err(f"not found: {path}", kind="bad_args")
        t._agent_msgs.clear()
        t._img_edit.setText(path)
        if dataset:
            self._mask_pick_dataset(t._h5loc_edit, str(dataset))
        t._load_image()
        if t._agent_msgs or t._image is None:
            return self._mask_fail(f"could not load {path}")
        if frame is not None:
            n = t._img_frames["n"] if t._img_frames is not None else 1
            if not 1 <= int(frame) <= n:
                return self._mask_fail(f"frame {frame} out of range 1..{n}", kind="bad_args")
            if t._img_frames is not None:
                t._load_frame(int(frame) - 1)
        return self._mask_reply("image")

    def op_mask_set_frame(self, frame) -> dict:
        t = self._mt
        if t._img_frames is None:
            return _err("the Image is a single frame (no frame navigator)", kind="precondition_failed")
        n = t._img_frames["n"]
        if not 1 <= int(frame) <= n:
            return _err(f"frame {frame} out of range 1..{n}", kind="bad_args")
        t._load_frame(int(frame) - 1)
        return self._mask_reply("image")

    # ── 1 · threshold ──
    def op_mask_set_threshold(self, lower=None, upper=None, projection=None, skip_frames=None) -> dict:
        t = self._mt
        if projection is not None:
            if projection not in _MASK_PROJECTIONS:
                return _err(f"unknown projection {projection!r} (use {list(_MASK_PROJECTIONS)})",
                            kind="bad_args")
            if projection != "Current frame" and t._img_frames is None:
                return _err("a projection needs a multi-frame Image (folder, stack file); "
                            "this Image is a single frame", kind="precondition_failed")
            t._thresh_proj_combo.setCurrentText(projection)
        if skip_frames is not None:
            if int(skip_frames) < 0:
                return _err("skip_frames must be >= 0", kind="bad_args")
            t._thresh_skip.setValue(int(skip_frames))
        if lower is not None:
            t._lower.setValue(float(lower))
        if upper is not None:
            t._upper.setValue(float(upper))
        return self._mask_reply("threshold")

    # ── 2 · statistical ──
    def op_mask_set_statistical(self, spatial=None, temporal=None, k_sigma=None, hot_factor=None,
                                dead_factor=None, frozen_frac=None, gap_guard_px=None,
                                gap_guard_factor=None) -> dict:
        t = self._mt
        for val, cb in ((spatial, t._spatial_check), (temporal, t._temporal_check)):
            if val is not None:
                cb.setChecked(bool(val))
        if gap_guard_px is not None:
            if not 0 <= int(gap_guard_px) <= 10:
                return _err("gap_guard_px must be 0..10", kind="bad_args")
            t._gap_guard.setValue(int(gap_guard_px))
        if gap_guard_factor is not None:
            if float(gap_guard_factor) < 1:
                return _err("gap_guard_factor must be >= 1", kind="bad_args")
            t._gap_factor.setValue(float(gap_guard_factor))
        for val, w in ((k_sigma, t._k_sigma), (hot_factor, t._hot_factor),
                       (dead_factor, t._dead_factor), (frozen_frac, t._frozen_frac)):
            if val is not None:
                if float(val) < 0:
                    return _err("parameters must be >= 0", kind="bad_args")
                w.setValue(float(val))
        return self._mask_reply("statistical")

    def op_mask_set_stack(self, path=None, dataset=None, stride=None, files=None) -> dict:
        """Stack used by spatial-outlier (temporal median), temporal-constancy and
        cosmic-ray: a folder, a glob (``*.tif``), one TIFF/HDF5 file (3-D dataset), or an
        explicit ``files`` list. ``path=""`` clears it (spatial then uses the single image)."""
        import glob as _g
        from midas_gui.helpers import display_text_for_paths
        t = self._mt
        if files is not None:
            files = sorted(str(f) for f in files)
            if not files:
                return _err("files is empty (use path='' to clear)", kind="bad_args")
            missing = [f for f in files if not Path(f).exists()]
            if missing:
                return _err(f"not found: {missing[:3]}", kind="bad_args")
            t._stack_ed.setText(display_text_for_paths(files))
            t._stack_ed.setToolTip("\n".join(files))
            t._stack_files = files          # after setText, which clears it
        elif path is not None:
            path = str(path)
            if path and not Path(path).exists() and not _g.glob(path):
                return _err(f"not found: {path}", kind="bad_args")
            t._stack_ed.setText(path)
            if dataset and path:
                self._mask_pick_dataset(t._stack_ds_combo, str(dataset))
        elif dataset:
            self._mask_pick_dataset(t._stack_ds_combo, str(dataset))
        if stride is not None:
            if int(stride) < 1:
                return _err("stride must be >= 1", kind="bad_args")
            t._stride_spin.setValue(int(stride))
        return self._mask_reply("stack")

    # ── 3 / 3b · spike and cosmic-ray ──
    def op_mask_set_spike(self, enabled=None, n_sigma=None) -> dict:
        t = self._mt
        if enabled is not None:
            t._spike_check.setChecked(bool(enabled))
        if n_sigma is not None:
            t._spike_sigma.setValue(float(n_sigma))
        return self._mask_reply("spike")

    def op_mask_set_cosmic_ray(self, enabled=None, n_sigma=None) -> dict:
        t = self._mt
        if enabled is not None:
            t._cosmic_check.setChecked(bool(enabled))
        if n_sigma is not None:
            t._cosmic_sigma.setValue(float(n_sigma))
        return self._mask_reply("cosmic_ray")

    # ── 4 · calibration-based ──
    def op_mask_set_calibration(self, path=None, from_calibrate: bool = False) -> dict:
        """Give the Mask tab a calibration (enables azimuthal / learnable): a geometry file
        (MIDAS paramstest, calibration .json or pyFAI .poni), or the Calibrate tab's latest result."""
        from midas_gui.helpers import result_ns_from_geometry_file
        t = self._mt
        if path:
            if not Path(str(path)).exists():
                return _err(f"not found: {path}", kind="bad_args")
            try:
                res = result_ns_from_geometry_file(str(path))
            except Exception as e:
                return _err(f"unreadable calibration {path!r}: {e}", kind="bad_args")
        elif from_calibrate:
            res = self._cal._result
            if res is None:
                return _err("the Calibrate tab has no result yet", kind="no_data")
        else:
            return _err("pass path=<geometry file> or from_calibrate=true", kind="bad_args")
        t.set_calibration(res)
        return self._mask_reply("calibration")

    def op_mask_set_calibration_methods(self, azimuthal=None, azimuthal_n_sigma=None, learnable=None,
                                        learn_steps=None, learn_lr=None, learn_sparsity=None) -> dict:
        t = self._mt
        if (azimuthal or learnable) and t._calib_result is None:
            return _err("azimuthal / learnable need a calibration — call mask_set_calibration first",
                        kind="precondition_failed")
        for val, cb in ((azimuthal, t._azim_check), (learnable, t._learn_check)):
            if val is not None:
                cb.setChecked(bool(val))
        if azimuthal_n_sigma is not None:
            t._azim_sigma.setValue(float(azimuthal_n_sigma))
        if learn_steps is not None:
            t._learn_steps.setValue(int(learn_steps))
        if learn_lr is not None:
            t._learn_lr.setValue(float(learn_lr))
        if learn_sparsity is not None:
            t._learn_sparsity.setValue(float(learn_sparsity))
        st = self._mask_state()
        return _ok({"azimuthal": st["azimuthal"], "learnable": st["learnable"],
                    "calibration": st["calibration"]})

    # ── 5 · post-processing ──
    def op_mask_set_postprocess(self, dilation=None) -> dict:
        if dilation is not None:
            if not 0 <= int(dilation) <= 50:
                return _err("dilation must be 0..50", kind="bad_args")
            self._mt._dilation_spin.setValue(int(dilation))
        return self._mask_reply("postprocess")

    # ── display ──
    def op_mask_set_display(self, show_overlay=None, draw_mask=None, log=None, cmap=None) -> dict:
        t = self._mt
        if cmap is not None and t._viewer._cmap.findText(str(cmap)) < 0:
            return _err(f"unknown colormap {cmap!r}", kind="bad_args")
        if draw_mask and t._image is None:
            return _err("load an image first", kind="precondition_failed")
        if show_overlay is not None:
            t._show_overlay_check.setChecked(bool(show_overlay))
        if draw_mask is not None:
            t._draw_check.setChecked(bool(draw_mask))
        if log is not None or cmap is not None:
            ds = {}
            if log is not None:
                ds["log"] = bool(log)
            if cmap is not None:
                ds["cmap"] = str(cmap)
            t._viewer.set_display_state(ds)
            if t._image is not None:
                t._viewer.set_raw_frame(t._image, t._disp_codes)
                t._paint_overlay()
        return self._mask_reply("display")

    # ── eraser ──
    def op_mask_set_eraser(self, enabled=None, size=None) -> dict:
        """The Eraser checkbox and its size slider (diameter in pixels, 1-100, default 2).
        Switching it on shows the brush circle for a human watching; to erase from an agent
        use ``mask_erase`` (the mouse is not driven)."""
        t = self._mt
        if size is not None:
            if not 1 <= int(size) <= 100:
                return _err("size must be 1..100 pixels", kind="bad_args")
            t._erase_slider.setValue(int(size))
        if enabled is not None:
            if enabled and t._image is None:
                return _err("load an image first", kind="precondition_failed")
            t._erase_check.setChecked(bool(enabled))
        return self._mask_reply("eraser")

    def op_mask_erase(self, centers, size=None) -> dict:
        """Clear mask pixels inside circles of ``size`` px diameter (default: the slider)
        centred at each [x, y] in RAW detector coordinates (x = column, y = row; pixel
        (c, r) covers [c, c+1], so a pixel's centre is c + 0.5). One undoable stroke."""
        t = self._mt
        try:
            pts = [(float(c[0]), float(c[1])) for c in (centers or [])]
        except Exception:
            return _err("centers must be [[x, y], ...]", kind="bad_args")
        if not pts:
            return _err("centers is empty", kind="bad_args")
        before = int(np.count_nonzero(t._mask)) if t._mask is not None else 0
        try:
            cleared = t.agent_erase(pts, size)
        except ValueError as e:
            kind = "bad_args" if "size" in str(e) else "precondition_failed"
            return _err(e, kind=kind)
        out = {"cleared": cleared, "masked_before": before}
        out.update(self._mask_summary())
        out["undo_depth"] = len(t._erase_undo)
        return _ok(out)

    def op_mask_undo_erase(self) -> dict:
        """Put back the pixels of the last erase stroke (the Undo button)."""
        t = self._mt
        if not t._undo_erase():
            return _err("nothing to undo", kind="precondition_failed")
        out = self._mask_summary()
        out["undo_depth"] = len(t._erase_undo)
        return _ok(out)

    # ── compute (async) ──
    def op_mask_compute(self) -> dict:
        """Compute Mask. Returns a ``job_id`` for ``mask_wait`` / ``mask_get_job`` (a
        threshold-only run is already done on return). Runs the tab's own ``_compute``."""
        t = self._mt
        if t._image is None:
            return _err("load an image first (mask_set_image)", kind="precondition_failed")
        prev = self._jobs.get(self._mask_job_id)
        if prev is not None and prev["status"] == "running":
            return _err("a mask computation is already running", kind="precondition_failed")
        job_id = str(uuid.uuid4())
        self._jobs[job_id] = {"status": "running", "result": None, "error": None, "_log": []}
        self._mask_job_id = job_id
        t._agent_msgs.clear()
        self._mask_launch(job_id, 0)
        if self._jobs[job_id]["status"] == "failed":
            return _err(self._jobs[job_id]["error"], kind="exception")
        return _ok({"job_id": job_id})

    def _mask_launch(self, job_id: str, tries: int) -> None:
        """Start the tab's compute for ``job_id``. The previous worker's ``finished``
        arrives a moment before the worker reports not-running, and ``_compute`` silently
        does nothing while it is — so retry from a timer (never a nested event loop)
        instead of leaving the job running forever."""
        from PyQt5 import QtCore
        t = self._mt
        job = self._jobs[job_id]
        if t._mask_worker is not None and t._mask_worker.isRunning():
            if tries > 200:
                job.update(status="failed", error="the previous mask worker did not exit")
                return
            QtCore.QTimer.singleShot(50, lambda: self._mask_launch(job_id, tries + 1))
            return
        try:
            t._compute()
            w = t._mask_worker
            if job["status"] == "running" and w is not None:
                w.progress.connect(lambda s, j=job_id: self._jobs[j]["_log"].append(str(s)))
        except Exception:
            job.update(status="failed", error=traceback.format_exc())

    def _on_mask_compute_finished(self, ok: bool, message: str) -> None:
        job = self._jobs.get(getattr(self, "_mask_job_id", None))
        if not job or job["status"] != "running":
            return
        if not ok:
            job.update(status="failed", error=message)
            return
        log = [x for x in job.get("_log", []) if x]
        job.update(status="done", result={
            **self._mask_summary(), "message": message, "log": log[-30:],
            "warnings": [x for x in log if "skipped" in x]})

    def op_mask_get_job(self, job_id) -> dict:
        job = self._jobs.get(job_id)
        if job is None:
            return _err(f"unknown job_id {job_id!r}", kind="bad_args")
        return _ok({k: v for k, v in job.items() if not k.startswith("_")})

    # ── load / save / log ──
    def op_mask_load_existing(self, path) -> dict:
        """Load a mask TIFF (non-zero = bad) as the computed mask, replacing it (drawn shapes stay)."""
        import tifffile
        t = self._mt
        path = str(path)
        if not Path(path).exists():
            return _err(f"not found: {path}", kind="bad_args")
        try:
            shape = tifffile.imread(path).shape
        except Exception as e:
            return _err(f"unreadable mask {path!r}: {e}", kind="bad_args")
        if t._image is not None and tuple(shape) != tuple(t._image.shape):
            return _err(f"mask is {shape[1]}x{shape[0]} but the image is "
                        f"{t._image.shape[1]}x{t._image.shape[0]}", kind="bad_args")
        t._agent_msgs.clear()
        t._load_mask_edit.setText(path)
        t._load_existing_mask()
        if t._agent_msgs:
            return self._mask_fail(f"could not load {path}")
        return self._mask_reply("mask")

    def op_mask_save(self, path=None) -> dict:
        """Save the final mask (computed OR drawn; 1 = bad, uint8) as a TIFF."""
        t = self._mt
        if t._mask is None:
            return _err("no mask yet — mask_compute, mask_load_existing or mask_apply_shapes first",
                        kind="precondition_failed")
        if path is not None:
            t._save_edit.setText(str(path))
        target = t._save_edit.text().strip()
        if not target:
            return _err("pass path", kind="bad_args")
        t._agent_msgs.clear()
        t._save()
        if not Path(target).is_file() or any(k == "error" for k, _t, _x in t._agent_msgs):
            return self._mask_fail(f"could not save {target}")
        t._agent_msgs.clear()
        return _ok({"path": target, **self._mask_summary()})

    def op_mask_log_to_project(self) -> dict:
        """Record this mask (parameters + array) as a new attempt in the open project."""
        t = self._mt
        if t._mask is None:
            return _err("no mask yet", kind="precondition_failed")
        if not t._project_ctx or not t._project_ctx.path:
            return _err("no project open — project_save(path) first", kind="precondition_failed")
        t._agent_msgs.clear()
        t._log_to_project()
        msgs = [(k, x) for k, _t, x in t._agent_msgs]
        t._agent_msgs.clear()
        if any(k != "info" for k, _x in msgs):
            return _err("; ".join(x for _k, x in msgs), kind="exception")
        return _ok({"attempt": msgs[-1][1].split(":\n")[-1] if msgs else None,
                    "project": str(t._project_ctx.path)})

    def op_mask_get_array(self, which: str = "mask") -> dict:
        """The final mask (uint8, 1 = bad) or the current image frame, as a ``.npy`` file reference."""
        t = self._mt
        if which == "mask":
            if t._mask is None:
                return _err("no mask yet", kind="no_data")
            return _ok({**self._mask_summary(), "array": _array_ref(t._mask, "mask")})
        if which == "image":
            if t._image is None:
                return _err("no image loaded", kind="no_data")
            return _ok({"array": _array_ref(t._image, "mask_image")})
        return _err(f"unknown which {which!r} (mask/image)", kind="bad_args")

    # ── hand-drawn shapes ──
    def op_mask_add_shape(self, kind, center=None, size=None, angle=0.0, radius=None,
                          r_outer=None, r_inner=None, points=None) -> dict:
        """Add a drawn shape in RAW detector pixel coordinates (x = column, y = row; pixel
        (c, r) covers [c, c+1]). kind: rectangle/oval (center, size [w, h], angle deg),
        circle (center, radius), annulus (center, r_outer, r_inner), polygon (points).
        Shapes only count once mask_apply_shapes runs."""
        try:
            idx = self._mt.agent_add_shape(kind, center=center, size=size, angle=angle, radius=radius,
                                           r_outer=r_outer, r_inner=r_inner, points=points)
        except ValueError as e:
            return _err(e, kind="bad_args" if "load an image" not in str(e) else "precondition_failed")
        return _ok({"index": idx, **self._mt.agent_list_shapes()})

    def op_mask_add_points(self, points) -> dict:
        """Pick single pixels to mask: [[col, row], ...] in raw detector pixels (needs mask_apply_shapes)."""
        try:
            n = self._mt.agent_add_points(points)
        except ValueError as e:
            return _err(e, kind="bad_args" if "load an image" not in str(e) else "precondition_failed")
        return _ok({"added": n, **self._mt.agent_list_shapes()})

    def op_mask_list_shapes(self) -> dict:
        return _ok(self._mt.agent_list_shapes())

    def op_mask_remove_shape(self, index) -> dict:
        try:
            self._mt.agent_remove_shape(int(index))
        except ValueError as e:
            return _err(e, kind="bad_args")
        return _ok(self._mt.agent_list_shapes())

    def op_mask_clear_shapes(self) -> dict:
        """Clear Shapes: removes every drawn shape/point and the hand-drawn part of the mask."""
        self._mt._clear_shapes()
        return self._mask_reply("mask")

    def op_mask_apply_shapes(self) -> dict:
        """Apply shapes -> mask: rasterise every drawn shape/point into the mask (OR-ed with the computed one)."""
        t = self._mt
        if t._image is None:
            return _err("load an image first", kind="precondition_failed")
        if not t._shapes and not t._points:
            return _err("nothing drawn — mask_add_shape / mask_add_points first", kind="precondition_failed")
        t._apply_shapes()
        return self._mask_reply("mask")



class GuiController(_SolveCellOps, _ProjectOps, _HydraBatchOps, _MaskOps):
    """Dispatches ``"namespace.verb"`` ops (see the module docstring) against
    the live ``main_window``'s Data Viewer and Calibrate tabs."""

    def __init__(self, main_window):
        self._win = main_window
        self._jobs: dict = {}   # job_id -> {"status","result","error"}
        self._cal_run_job_id = None   # latest calibrate.run job, for calibrate.abort
        self._cal.set_agent_mode(True)
        self._geom.set_agent_mode(True)
        sc = getattr(main_window, "_solve_cell_tab", None)
        if sc is not None:
            sc.set_agent_mode(True)
            sc.stageFinished.connect(self._on_sc_stage_finished)
        mask = getattr(main_window, "_mask_tab", None)
        if mask is not None:
            mask.set_agent_mode(True)
            mask.computeFinished.connect(self._on_mask_compute_finished)
        self._mask_job_id = None   # the latest mask.compute job
        self._hb_queue = None    # the running/last hydra_batch.run_queue, if any
        self._hb_job_id = None
        self._sc_job_id = None   # the one in-flight Solve Cell job, if any
        self._sc_chain = None    # the running domain loop (see op_solve_cell_run_domain_loop), if any

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
                       "solve_cell": "_solve_cell_tab", "hydra_batch": "_batch_tab",
                       "mask": "_mask_tab"}

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
    def _set_field(self, loader, role: str, path: str, checked: bool, mode, **extra) -> dict:
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
        state.update({k: v for k, v in extra.items() if v is not None})
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
                         build_residual_corr=None, clear_seed=False) -> dict:
        tab = self._cal
        if clear_seed:
            # Untick every seed-enable flag (spin values are kept but unused);
            # a manual_seed given alongside is applied afterwards.
            from PyQt5 import QtCore
            tab._manual_seed_check.setCheckState(QtCore.Qt.Unchecked)
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
        self._cal_run_job_id = job_id

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

    def op_calibrate_abort(self) -> dict:
        """Same as the Calibrate tab's Abort button. ``tab._abort`` disconnects
        the worker's signals, so the ``_done``/``_fail`` hooks ``op_calibrate_run``
        attached never fire — mark the in-flight run job ``aborted`` here or it
        would stay ``running`` forever (and ``calibrate.wait`` would hang to its
        timeout). Projection jobs are not affected."""
        tab = self._cal
        w = tab._worker
        if not (w and w.isRunning()):
            return _ok({"aborted": False, "note": "no calibration run in progress"})
        tab._abort()
        job_id = self._cal_run_job_id
        if job_id and self._jobs.get(job_id, {}).get("status") == "running":
            self._jobs[job_id] = {"status": "aborted", "result": None,
                                  "error": "aborted by calibrate.abort"}
        return _ok({"aborted": True, "job_id": job_id})

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

