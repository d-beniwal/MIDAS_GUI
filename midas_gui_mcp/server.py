"""MCP server for midas-gui's Data Viewer, Calibrate and Solve Cell tabs.

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


# ── Solve Cell ───────────────────────────────────────────────────────
#
# The tab's pipeline is: configure panels (data + calibration + omega
# mapping) → 1 Ingest → 2 Diamond filter → 3 Index → 4 Refine (free mode
# only; known-structure indexing refines in the same run) → 5 Index
# remaining spots (repeat for further domains). Every run_* tool is async:
# it returns a job_id — block on it with solve_cell_wait (or poll
# solve_cell_get_job). Only one stage runs at a time.

@mcp.tool()
def solve_cell_get_state() -> Any:
    """Full Solve Cell snapshot: every panel (data path, n_frames, calibration,
    omega mapping), every stage's parameters, whether a stage is running, and
    result counts (spots, diamond-flagged, candidates, remaining, solved
    domains with cell/UB/rms). Call this first to see what is already done."""
    return _call("solve_cell.get_state")


@mcp.tool()
def solve_cell_get_log(tail: int = 80) -> Any:
    """The last ``tail`` lines of the Solve Cell log panel (stage progress,
    warnings, indexing notes) — read this when a stage fails or finds nothing."""
    return _call("solve_cell.get_log", tail=tail)


@mcp.tool()
def solve_cell_set_project_dir(path: str) -> Any:
    """Set the Solve Cell project folder where panel_NN/ and pooled/ stage
    outputs are written ("" = keep everything in memory only)."""
    return _call("solve_cell.set_project_dir", path=path)


@mcp.tool()
def solve_cell_add_panel() -> Any:
    """Add a detector panel (a new numbered tab, becomes the active panel).
    Multi-panel setups ingest all fully-configured panels together."""
    return _call("solve_cell.add_panel")


@mcp.tool()
def solve_cell_remove_panel(panel_id: int) -> Any:
    """Remove a panel. Removing a non-last panel renumbers the later ones and
    would invalidate an existing ingest, so that case is refused when results
    exist — remove from the end, or start a fresh ingest state first."""
    return _call("solve_cell.remove_panel", panel_id=panel_id)


@mcp.tool()
def solve_cell_select_panel(panel_id: int) -> Any:
    """Make a panel the active one (the one Detector view / run guards use)."""
    return _call("solve_cell.select_panel", panel_id=panel_id)


@mcp.tool()
def solve_cell_panel_load_data(path: str, dataset: Optional[str] = None,
                               panel_id: Optional[int] = None) -> Any:
    """Load a panel's raw frame stack (HDF5 dataset optional). panel_id
    defaults to the active panel."""
    return _call("solve_cell.panel_load_data", path=path, dataset=dataset, panel_id=panel_id)


@mcp.tool()
def solve_cell_panel_set_field(role: str, path: str, checked: bool = True,
                               mode: Optional[str] = None,
                               panel_id: Optional[int] = None) -> Any:
    """Set a panel's dark/bright/background field (role "dark"/"bright"/
    "background"; mode "divide"/"subtract" applies to bright only)."""
    return _call("solve_cell.panel_set_field", role=role, path=path, checked=checked,
                 mode=mode, panel_id=panel_id)


@mcp.tool()
def solve_cell_panel_load_calibration(path: str, panel_id: Optional[int] = None) -> Any:
    """Load a panel's calibration (MIDAS paramstest.txt / calibration.json /
    pyFAI .poni, auto-detected). Required before Ingest. Returns the geometry
    and notes (e.g. a file that does not declare im_trans)."""
    return _call("solve_cell.panel_load_calibration", path=path, panel_id=panel_id)


@mcp.tool()
def solve_cell_panel_set_omega(start: Optional[float] = None, end: Optional[float] = None,
                               step: Optional[float] = None, first_idx: Optional[int] = None,
                               panel_id: Optional[int] = None) -> Any:
    """Set a panel's frame↔omega mapping (degrees): omega(frame) = start +
    (frame − first_idx) × step; frames outside [first_idx, last] are excluded.
    Omit an argument to leave it as-is. A step of 0 with start != end makes
    every frame share one omega — ingest refuses that unless acknowledged."""
    return _call("solve_cell.panel_set_omega", start=start, end=end, step=step,
                 first_idx=first_idx, panel_id=panel_id)


@mcp.tool()
def solve_cell_panel_list_omega_fields(panel_id: Optional[int] = None) -> Any:
    """List the HDF5 1-D datasets in the panel's data file (omega-looking
    names first) — candidates for solve_cell_panel_detect_first_idx."""
    return _call("solve_cell.panel_list_omega_fields", panel_id=panel_id)


@mcp.tool()
def solve_cell_panel_detect_first_idx(field: str, panel_id: Optional[int] = None) -> Any:
    """Auto-fill the panel's first frame idx from an HDF5 per-frame omega
    field: the first loaded frame whose omega lies between start and end
    omega (set those first with solve_cell_panel_set_omega)."""
    return _call("solve_cell.panel_detect_first_idx", field=field, panel_id=panel_id)


@mcp.tool()
def solve_cell_set_ingest_params(
    low_count_threshold: Optional[float] = None, mask_grow: Optional[int] = None,
    blob_threshold: Optional[float] = None, blob_min_vol: Optional[int] = None,
    split_ratio: Optional[float] = None, gap_bridge: Optional[int] = None,
    sector_candidates: Optional[list] = None, max_workers: Optional[int] = None,
) -> Any:
    """Stage 1 (Ingest) tuning — the "Parameters…" dialog. gap_bridge is the
    closing-kernel size in px (0 disables bridging, for single-sensor
    detectors); sector_candidates is a list of azimuth-sector counts to
    grid-search (narrow it to one value once a prior run's log reports
    choose_sectors); max_workers is the thread-pool width. Omit to keep."""
    return _call("solve_cell.set_ingest_params", low_count_threshold=low_count_threshold,
                 mask_grow=mask_grow, blob_threshold=blob_threshold, blob_min_vol=blob_min_vol,
                 split_ratio=split_ratio, gap_bridge=gap_bridge,
                 sector_candidates=sector_candidates, max_workers=max_workers)


@mcp.tool()
def solve_cell_run_ingest(acknowledge_warnings: bool = False) -> Any:
    """Stage 1: ingest every fully-configured panel (calibration + data, no
    pending field) — pooled if more than one. Async: returns a job_id for
    solve_cell_wait. Refuses (precondition_failed) on a 0° omega step with a
    non-zero omega span, or a calibration that declares no im_trans; pass
    acknowledge_warnings=True only once you have checked those are intended."""
    return _call("solve_cell.run_ingest", acknowledge_warnings=acknowledge_warnings)


@mcp.tool()
def solve_cell_set_diamond_params(diamond_a: Optional[float] = None,
                                  tol_deg: Optional[float] = None) -> Any:
    """Stage 2 settings: diamond cubic cell edge (Å) and the 2θ window (°)
    around each predicted diamond ring within which a spot is flagged."""
    return _call("solve_cell.set_diamond_params", diamond_a=diamond_a, tol_deg=tol_deg)


@mcp.tool()
def solve_cell_run_diamond_filter() -> Any:
    """Stage 2: flag diamond-ring spots and build the candidate pool for
    indexing. Needs a finished ingest. Async (job_id → solve_cell_wait). A new
    diamond filter clears all solved domains."""
    return _call("solve_cell.run_diamond_filter")


@mcp.tool()
def solve_cell_set_index_params(
    method: Optional[str] = None, sigma_g: Optional[float] = None,
    min_reflections: Optional[int] = None, tol: Optional[float] = None,
    known_structure: Optional[dict] = None,
) -> Any:
    """Stage 3 settings. method: "free" (blind ab-initio, then run refine) or
    "known_structure" (index + refine in one run against a cell you supply).
    sigma_g (Å⁻¹), min_reflections and tol (fractional-HKL) apply to free mode.
    known_structure is a subset of {a,b,c,alpha,beta,gamma (Å/°), sg (space-group
    number, a post-hoc absence check only), n_search, tol, min_accept}."""
    return _call("solve_cell.set_index_params", method=method, sigma_g=sigma_g,
                 min_reflections=min_reflections, tol=tol, known_structure=known_structure)


@mcp.tool()
def solve_cell_run_index() -> Any:
    """Stage 3: index the candidate pool using the method set by
    solve_cell_set_index_params. Async. Free mode ends at an unrefined
    ab-initio result — then call solve_cell_run_refine. Known-structure mode
    produces Domain 1 directly (a negative result is ok with a note)."""
    return _call("solve_cell.run_index")


@mcp.tool()
def solve_cell_run_refine() -> Any:
    """Stage 4: refine the successful ab-initio index into Domain 1 (re-running
    replaces Domain 1). Async."""
    return _call("solve_cell.run_refine")


@mcp.tool()
def solve_cell_set_remaining_params(method: Optional[str] = None,
                                    known_structure: Optional[dict] = None) -> Any:
    """Stage 5 settings for indexing the spots no domain has claimed. method:
    "free" (new unknown structure) or "known_structure" (e.g. a second grain of
    the same material; fields as in solve_cell_set_index_params)."""
    return _call("solve_cell.set_remaining_params", method=method,
                 known_structure=known_structure)


@mcp.tool()
def solve_cell_fill_remaining_from_latest_domain() -> Any:
    """Copy the latest solved domain's refined cell into the stage-5 known-
    structure fields (space group is left as-is)."""
    return _call("solve_cell.fill_remaining_from_latest_domain")


@mcp.tool()
def solve_cell_run_index_remaining() -> Any:
    """Stage 5: search the still-unassigned spots for a further domain (free:
    ab-initio then refine in one go; known structure: a null-tested orientation
    search). Async. A clean "no new domain" is ok=true with a note, not an
    error; a found domain is appended to the domain list."""
    return _call("solve_cell.run_index_remaining")


@mcp.tool()
def solve_cell_wait(job_id: str, timeout: float = 600.0) -> Any:
    """Block (up to timeout s) until a Solve Cell job finishes; returns
    status ("done"/"failed"/"running" if timed out), the results summary
    (counts + domains) and a note, or the error."""
    return _call("solve_cell.wait", job_id=job_id, timeout=timeout)


@mcp.tool()
def solve_cell_get_job(job_id: str) -> Any:
    """Check a Solve Cell job's status without blocking."""
    return _call("solve_cell.get_job", job_id=job_id)


@mcp.tool()
def solve_cell_get_domains() -> Any:
    """Every solved domain: label, method, spot count, refined cell (+sigma),
    UB, rms_drlv, holohedry/conventional cell."""
    return _call("solve_cell.get_domains")


@mcp.tool()
def solve_cell_select_domain(index: int) -> Any:
    """Select a domain (0-based) — drives the overlays (a*,b*,c*, predicted
    lattice points), Diagnostics and Compare views."""
    return _call("solve_cell.select_domain", index=index)


@mcp.tool()
def solve_cell_get_spots(which: str = "all") -> Any:
    """Write a spot table to a temp CSV and return its path/columns/row count
    (read it with your own file tool). which: "all" ingested spots,
    "candidates" (diamond-filtered pool), "diamond" (flagged spots)."""
    return _call("solve_cell.get_spots", which=which)


@mcp.tool()
def solve_cell_set_view(view: str) -> Any:
    """Bring a result view to the front: reciprocal, detector, diagnostics,
    volume, compare, contamination, inspector."""
    return _call("solve_cell.set_view", view=view)


@mcp.tool()
def solve_cell_set_reciprocal_options(
    color_by: Optional[str] = None, show_unindexed: Optional[bool] = None,
    point_size: Optional[int] = None, size_mode: Optional[str] = None,
) -> Any:
    """Reciprocal-space map options. color_by: panel/crystal/q_residual;
    size_mode: equal/intensity."""
    return _call("solve_cell.set_reciprocal_options", color_by=color_by,
                 show_unindexed=show_unindexed, point_size=point_size,
                 size_mode=size_mode)


@mcp.tool()
def solve_cell_set_overlays(
    omega_deg: Optional[float] = None, ewald: Optional[bool] = None,
    basis: Optional[bool] = None, predicted: Optional[bool] = None,
    predicted_offpanel: Optional[bool] = None, frame_patches: Optional[bool] = None,
    shells: Optional[bool] = None, highlight_now: Optional[bool] = None,
) -> Any:
    """Reciprocal-map overlays: Ewald-sphere cap, a*/b*/c*+cell, predicted
    lattice points (+ off-panel ones), frame patches, |q| shells, and the ω (°)
    that the Ewald cap/patches/"diffracting now" highlight use. Most need a
    solved domain / finished ingest; the status field says what was skipped."""
    return _call("solve_cell.set_overlays", omega_deg=omega_deg, ewald=ewald, basis=basis,
                 predicted=predicted, predicted_offpanel=predicted_offpanel,
                 frame_patches=frame_patches, shells=shells, highlight_now=highlight_now)


@mcp.tool()
def solve_cell_build_volume(pitch: Optional[float] = None) -> Any:
    """Start the Volume tab's reciprocal-space volume build (voxel pitch in
    Å⁻¹; default the tab's current value). Needs a finished ingest from plain
    HDF5 sources. Poll solve_cell_get_volume_status."""
    return _call("solve_cell.build_volume", pitch=pitch)


@mcp.tool()
def solve_cell_get_volume_status() -> Any:
    """Whether the volume build is running, a volume is cached, and the tab's info text."""
    return _call("solve_cell.get_volume_status")


@mcp.tool()
def solve_cell_select_spot(spot_id: int) -> Any:
    """Select a spot (as a click would) — updates the reciprocal map marker,
    Spot inspector and Detector view; returns its description (panel, frame,
    ω, hkl, residuals)."""
    return _call("solve_cell.select_spot", spot_id=spot_id)


@mcp.tool()
def solve_cell_show_selected_on_detector() -> Any:
    """Jump the Detector view to the selected spot's source panel and frame."""
    return _call("solve_cell.show_selected_on_detector")


@mcp.tool()
def solve_cell_review_domains(a: str, b: str, conclusion: str) -> Any:
    """Record a reviewer conclusion for a domain pair from the Compare domains
    tab (a/b are its comparison keys; "" clears it). Never alters domain claims."""
    return _call("solve_cell.review_domains", a=a, b=b, conclusion=conclusion)


@mcp.tool()
def solve_cell_save_project(path: Optional[str] = None) -> Any:
    """Save the project (the open one, or to ``path``) including the Solve
    Cell analysis record, same as File ▸ Save Project."""
    return _call("solve_cell.save_project", path=path)


if __name__ == "__main__":
    mcp.run()
