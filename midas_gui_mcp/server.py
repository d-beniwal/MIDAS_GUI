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
def calibrate_run(mode: str, refine: Optional[dict] = None,
                  manual_seed: Optional[dict] = None) -> Any:
    """Start a calibration run. mode is one of: one_shot, first_time,
    four_stage, bayesian, joint, frozen_point. refine is a subset of
    {Lsd,BC,tx,ty,tz,Wavelength: bool}. manual_seed is a subset of
    {BC_y,BC_z,Lsd,tx,ty,tz: float} (Lsd in µm). Returns a job_id immediately
    — poll with calibrate_wait/calibrate_get_job."""
    return _call("calibrate.run", mode=mode, refine=refine, manual_seed=manual_seed)


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
def calibrate_get_state() -> Any:
    """Full Calibrate tab state snapshot."""
    return _call("calibrate.get_state")


if __name__ == "__main__":
    mcp.run()
