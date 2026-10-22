"""MCP server for midas-gui's Data Viewer and Calibrate tabs.

A thin proxy: every tool here does nothing but serialize its arguments,
send them to the running midas-gui instance's control socket (via
``client.ControlClient``), and return the result. All the actual logic
lives in ``midas_gui/control_api.py`` inside the GUI process itself — see
that module and ``README.md`` for the wire protocol this speaks.

Run with midas-gui already running with ``MIDAS_GUI_CONTROL=1`` set, then:

    python midas_gui_mcp/server.py

(stdio transport — register it with an MCP client / Claude Code the normal
way; see README.md.)
"""
from __future__ import annotations

from typing import Any, Optional

from mcp.server.mcpserver import MCPServer

from client import ControlClient, ControlError

mcp = MCPServer("midas-gui")

_client: Optional[ControlClient] = None


def _call(op: str, **args: Any) -> Any:
    """Shared dispatch for every tool below: lazily connect (or reconnect
    after the GUI restarted/dropped the socket), call ``op``, and surface a
    control-side error as a plain dict instead of raising — an MCP tool
    result should describe the failure, not crash the tool call."""
    global _client
    for attempt in range(2):
        try:
            if _client is None:
                _client = ControlClient()
            return _client.call(op, {k: v for k, v in args.items() if v is not None})
        except ControlError as e:
            return {"ok": False, "error": e.error}
        except (ConnectionError, OSError):
            _client = None
            if attempt == 1:
                return {"ok": False, "error": {
                    "message": "could not reach midas-gui's control socket — is it "
                               "running with MIDAS_GUI_CONTROL=1?",
                    "type": "connection_failed"}}


# ── Data Viewer ──────────────────────────────────────────────────────

@mcp.tool()
def data_viewer_load_file(path: str, dataset: Optional[str] = None) -> Any:
    """Load a data file into the Data Viewer tab (HDF5 dataset optional)."""
    return _call("data_viewer.load_file", path=path, dataset=dataset)


@mcp.tool()
def data_viewer_start_live(pv: str, backend: str = "pva") -> Any:
    """Start live-streaming a detector's EPICS PV into the Data Viewer."""
    return _call("data_viewer.start_live", pv=pv, backend=backend)


@mcp.tool()
def data_viewer_stop_live() -> Any:
    """Stop the Data Viewer's active live stream, if any."""
    return _call("data_viewer.stop_live")


@mcp.tool()
def data_viewer_set_geometry(
    wavelength_A: Optional[float] = None, pxY: Optional[float] = None,
    Lsd: Optional[float] = None, BC_y: Optional[float] = None,
    BC_z: Optional[float] = None, ty: Optional[float] = None,
    tz: Optional[float] = None, im_trans: Optional[list] = None,
) -> Any:
    """Set the Data Viewer's manual detector geometry (µm/Å/px; Lsd in µm)."""
    return _call("data_viewer.set_geometry", wavelength_A=wavelength_A, pxY=pxY, Lsd=Lsd,
                 BC_y=BC_y, BC_z=BC_z, ty=ty, tz=tz, im_trans=im_trans)


@mcp.tool()
def data_viewer_get_geometry() -> Any:
    """Read the Data Viewer's current manual detector geometry."""
    return _call("data_viewer.get_geometry")


@mcp.tool()
def data_viewer_load_calibration_file(path: str) -> Any:
    """Load a calibration file (MIDAS paramstest.txt, calibration.json, or
    pyFAI .poni — format is auto-detected) into the Data Viewer's geometry,
    the same as typing its path into the Ring-simulation card's calibration
    field. Returns the geometry found in the file."""
    return _call("data_viewer.load_calibration_file", path=path)


@mcp.tool()
def data_viewer_set_materials(materials: list) -> Any:
    """Replace the Data Viewer's ring-simulation material list. Each material
    is a dict with ``name`` and either ``a,b,c,alpha,beta,gamma,sg`` (a
    crystalline lattice + space group) or ``d_list`` (explicit d-spacings,
    Å, for a non-crystalline standard)."""
    return _call("data_viewer.set_materials", materials=materials)


@mcp.tool()
def data_viewer_simulate_rings(require_image: bool = False) -> Any:
    """(Re)compute ring positions for every enabled material from the
    current geometry. Set require_image=True to fail instead of computing
    rings with no image bound (no on-image overlay to draw them on)."""
    return _call("data_viewer.simulate_rings", require_image=require_image)


@mcp.tool()
def data_viewer_get_radial_profile(mode: str = "fast") -> Any:
    """Compute and return the current frame's radial (azimuthal) profile.
    mode="fast" (default, live-stream-safe) or "accurate" (full subpixel
    integration). Returns file references for the r/profile arrays, not the
    arrays inline — read them with your own file-reading tool."""
    return _call("data_viewer.get_radial_profile", mode=mode)


@mcp.tool()
def data_viewer_get_cake(r_bin: Optional[float] = None, eta_bin: Optional[float] = None) -> Any:
    """Compute and return the current frame's (eta, R) cake — always the
    full/accurate integration pipeline (tilts + distortion honoured when a
    calibration is loaded), same as clicking "Calculate" above the Eta vs R
    Cake plot. Optionally set the R-bin (px) and eta-bin (deg) sizes first;
    omit either to keep whatever is currently set in the GUI. Returns file
    references for the 2-D cake array and its r/eta axis arrays, not the
    arrays inline — read them with your own file-reading tool."""
    return _call("data_viewer.get_cake", r_bin=r_bin, eta_bin=eta_bin)


@mcp.tool()
def data_viewer_get_statistics(scope: str = "current") -> Any:
    """Intensity statistics (N/min/max/mean/std/percentiles) for the current
    frame (scope="current") or the whole stack (scope="all" — returns a
    job_id to poll with calibrate_get_job-style polling, since it runs in
    the background for a large stack)."""
    return _call("data_viewer.get_statistics", scope=scope)


@mcp.tool()
def data_viewer_set_field(role: str, path: str, checked: bool = True,
                          mode: Optional[str] = None) -> Any:
    """Set the Data Viewer's dark/bright/background field. role is
    "dark"/"bright"/"background"; mode ("divide"/"subtract") only applies to
    bright."""
    return _call("data_viewer.set_field", role=role, path=path, checked=checked, mode=mode)


@mcp.tool()
def data_viewer_get_state() -> Any:
    """Full Data Viewer tab state snapshot (materials, geometry, loader,
    display state)."""
    return _call("data_viewer.get_state")


# ── Calibrate ────────────────────────────────────────────────────────

@mcp.tool()
def calibrate_load(path: str, dataset: Optional[str] = None,
                   calibrant: Optional[str] = None) -> Any:
    """Load a calibrant image (and optionally select the calibrant
    material/standard) into the Calibrate tab."""
    return _call("calibrate.load", path=path, dataset=dataset, calibrant=calibrant)


@mcp.tool()
def calibrate_set_field(role: str, path: str, checked: bool = True,
                        mode: Optional[str] = None) -> Any:
    """Set the Calibrate tab's dark/bright/background field. role is
    "dark"/"bright"/"background"; mode ("divide"/"subtract") only applies to
    bright."""
    return _call("calibrate.set_field", role=role, path=path, checked=checked, mode=mode)


@mcp.tool()
def calibrate_set_projection(method: Optional[str] = None, skip: Optional[int] = None,
                             n_frames: Optional[int] = None) -> Any:
    """Set the Calibrate tab's stack-Projection controls: method is one of
    max/sum/mean, skip is frames to skip at the start, n_frames is how many
    frames to project (0 = all). Omit any argument to leave it as-is. Does
    not itself run the projection — call calibrate_project to do that."""
    return _call("calibrate.set_projection", method=method, skip=skip, n_frames=n_frames)


@mcp.tool()
def calibrate_project() -> Any:
    """Run the stack projection (using whatever Method/Skip/N-frames is
    currently set — see calibrate_set_projection) and feed the resulting
    image into the calibration pipeline in place of the current raw frame.
    Requires a multi-frame source. Async like calibrate_run: returns a
    job_id — poll with calibrate_wait/calibrate_get_job."""
    return _call("calibrate.project")


@mcp.tool()
def calibrate_clear_projection() -> Any:
    """Drop the active projection — the pipeline reverts to using the
    current raw frame as its input image."""
    return _call("calibrate.clear_projection")


@mcp.tool()
def calibrate_run(mode: str, refine: Optional[dict] = None,
                  manual_seed: Optional[dict] = None, limits: Optional[dict] = None,
                  build_residual_corr: Optional[bool] = None) -> Any:
    """Start a calibration run. mode is one of: one_shot, first_time,
    four_stage, bayesian, joint, frozen_point.

    refine is a subset of {Lsd,BC,tx,ty,tz,Wavelength,Distortion: bool} plus
    (crystalline calibrants only) one of:
      - distortion_mode: one of "None", "Isotropic only", "Iso + up to
        2-fold", "Iso + up to 4-fold", "All (15)" — selects a preset set of
        the 15 distortion coefficients to refine.
      - distortion_coeffs: an explicit list of coefficient names (from
        iso_R2, iso_R4, iso_R6, a1, phi1, a2, phi2, a3, phi3, a4, phi4, a5,
        phi5, a6, phi6) — each fold's amplitude/phase must be given together.
    Distortion itself still needs refine["Distortion"]=True to actually be
    refined; distortion_mode/distortion_coeffs only choose which coefficients
    that applies to.

    manual_seed is a subset of {BC_y,BC_z,Lsd,tx,ty,tz: float} (Lsd in µm)
    plus optionally "distortion": {coeff_name: value} to seed individual
    distortion coefficients (enables Distortion seeding automatically).

    limits is {row_name: {"on": bool, "value": float, "unit": str}} for rows
    named Lsd/BC_y/BC_z/ty/tz/tx/wavelength_A (manual d-spacing fits: an
    opt-in ± window around the seed) or Lsd/BC_y/ty/wavelength_A/distortion
    (crystalline fits: the ± tolerance window always applied around the
    seed/auto-seed — "on" is ignored there). Units: Lsd in "mm" or "%", BC_y/
    BC_z in "px" or "%", tilts in "°" or "%", wavelength_A in "Å" or "%",
    distortion has no unit (absolute only).

    build_residual_corr toggles the "Residual map" checkbox (crystalline
    fits only).

    Returns a job_id immediately — poll with calibrate_wait/calibrate_get_job."""
    return _call("calibrate.run", mode=mode, refine=refine, manual_seed=manual_seed,
                 limits=limits, build_residual_corr=build_residual_corr)


@mcp.tool()
def calibrate_get_job(job_id: str) -> Any:
    """Check a calibration job's status without blocking."""
    return _call("calibrate.get_job", job_id=job_id)


@mcp.tool()
def calibrate_wait(job_id: str, timeout: float = 120.0) -> Any:
    """Block (up to timeout seconds) until a calibration job finishes, then
    return its status/result/error."""
    return _call("calibrate.wait", job_id=job_id, timeout=timeout)


@mcp.tool()
def calibrate_seed_from_last_run() -> Any:
    """Copy the last completed run's refined parameters into the manual-seed
    fields for the next run (only parameters that run actually refined)."""
    return _call("calibrate.seed_from_last_run")


@mcp.tool()
def calibrate_run_frozen_point_then_oneshot(
    frozen_point_cfg: Optional[dict] = None, one_shot_cfg: Optional[dict] = None,
    timeout: float = 300.0,
) -> Any:
    """Composite workflow: run frozen-point (high-tilt) calibration, wait for
    it, seed the result forward, then run one-shot calibration. Each *_cfg is
    the same {refine, manual_seed} shape calibrate_run takes."""
    return _call("calibrate.run_frozen_point_then_oneshot", frozen_point_cfg=frozen_point_cfg,
                 one_shot_cfg=one_shot_cfg, timeout=timeout)


@mcp.tool()
def calibrate_import_from_data_viewer() -> Any:
    """Seed the Calibrate tab's geometry from the Data Viewer's current
    manual geometry."""
    return _call("calibrate.import_from_data_viewer")


@mcp.tool()
def calibrate_export_to_data_viewer() -> Any:
    """Push the Calibrate tab's last completed result into the Data
    Viewer's manual geometry."""
    return _call("calibrate.export_to_data_viewer")


@mcp.tool()
def calibrate_get_working_dir() -> Any:
    """Read the Calibrate tab's current working directory."""
    return _call("calibrate.get_working_dir")


@mcp.tool()
def calibrate_set_working_dir(path: str) -> Any:
    """Set the Calibrate tab's working directory — where a run's
    intermediates (residual_corr.bin, etc.) and Save-button dialogs land."""
    return _call("calibrate.set_working_dir", path=path)


@mcp.tool()
def calibrate_suggest_working_dir() -> Any:
    """Fill in the working directory with the <expid>_bc analysis folder
    derived from the currently loaded data path (same as clicking
    "Suggest" next to the Working dir field)."""
    return _call("calibrate.suggest_working_dir")


@mcp.tool()
def calibrate_save_json(path: str) -> Any:
    """Save the last completed calibration run's result as a calibration.json
    at the given path (same content as the "Save .json" button, without the
    file dialog). Requires a completed run."""
    return _call("calibrate.save_json", path=path)


@mcp.tool()
def calibrate_save_paramstest(out_path: str, template_path: Optional[str] = None) -> Any:
    """Save the last completed calibration run's result as a MIDAS
    paramstest.txt at out_path (same as the "Save paramstest.txt" button,
    without the dialog). template_path, if given, injects only the fitted
    geometry/distortion into that existing paramstest.txt, keeping every
    other parameter verbatim; omit it for a self-contained file. Requires a
    completed run."""
    return _call("calibrate.save_paramstest", out_path=out_path, template_path=template_path)


@mcp.tool()
def calibrate_get_state() -> Any:
    """Full Calibrate tab state snapshot."""
    return _call("calibrate.get_state")


if __name__ == "__main__":
    mcp.run()
