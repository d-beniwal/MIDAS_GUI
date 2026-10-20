"""In-process control API for midas-gui's Data Viewer and Calibrate tabs.

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

Two tabs enable an explicit ``agent_mode`` flag (``CalibrationTab``,
``DetectorGeometryCard``) so their normal blocking `QMessageBox` guards
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


class GuiController:
    """Dispatches ``"namespace.verb"`` ops (see the module docstring) against
    the live ``main_window``'s Data Viewer and Calibrate tabs."""

    def __init__(self, main_window):
        self._win = main_window
        self._jobs: dict = {}   # job_id -> {"status","result","error"}
        self._cal.set_agent_mode(True)
        self._geom.set_agent_mode(True)

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
    _NAMESPACE_TABS = {"data_viewer": "_view_tab", "calibrate": "_cal_tab"}

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

    def op_calibrate_run(self, mode, refine=None, manual_seed=None) -> dict:
        tab = self._cal
        idx = tab._pipeline.findData(mode)
        if idx < 0:
            return _err(f"unknown pipeline mode {mode!r}", kind="bad_args")
        tab._pipeline.setCurrentIndex(idx)
        if refine:
            tab._set_refine_box_state(refine)
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
        return _ok({k: v for k, v in job.items() if not k.startswith("_")})

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

    def op_calibrate_get_state(self) -> dict:
        return _ok(_jsonable(self._cal.get_state(), stem="cal_state"))
