"""Phase-1 solve-cell pipeline: single detector panel, geometry supplied
directly (no geometry-resolution tiers), blind ab-initio indexing + free
refinement. See documentation/solve_cell_handoff.md for the full
specification this is Phase 1 of.

No ``midas_gui``/PyQt5 imports anywhere in this module -- only numpy/pandas/
torch/midas_defect/midas_hkls/stdlib -- so it can be lifted into a standalone
``midas_solve_cell`` package later with a plain directory move. Progress is
reported via plain ``print()`` (no logging framework): the GUI worker
redirects stdout/stderr into its log signal, exactly like every other
long-running operation in this app.

g-vector convention (the single most common bug class per the handoff --
read this before touching any function below):
    * ``midas_defect.geometry.pixel_to_qlab``/``qlab_to_qsample`` natively
      produce ``q = 2*pi/d``.
    * ``midas_hkls.ab_initio.index_ab_initio`` accepts that via
      ``two_pi=True``, but its ``.UB``/``.cell`` output is ALWAYS in ``1/d``
      convention regardless of the flag.
    * ``midas_hkls.ub_refine.refine_ub_from_gvectors`` has NO ``two_pi``
      parameter at all -- it returns whatever convention its input ``g`` was
      in.
Rule followed throughout this file: ingest and diamond-filtering work in
``2*pi/d`` throughout (matching ``midas_defect``'s native convention);
immediately before calling ``refine_ub_from_gvectors``, both ``g`` and
``sigma_g`` are converted to ``1/d`` by ``_to_inverse_d`` below.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

SENTINEL = 4294967295
TWO_PI = 2.0 * math.pi

# Defaults mirror the validated reference analyses (handoff §5.0's threshold
# table) -- every one of these is also an exposed, overridable GUI field.
SECTOR_CANDIDATES_DEFAULT = (1, 8, 24, 48, 96)   # matches midas_defect.ingest.choose_sectors's own default
LIVE_FRAMES_FRAC_DEFAULT = 0.002
LOW_COUNT_THRESHOLD_DEFAULT = 0.0   # NOT midas_defect's own default of 20 --
                                     # see _stage_ingest docstring.
MASK_GROW_DEFAULT = 2
BLOB_THRESHOLD_DEFAULT = 15.0
BLOB_MIN_VOL_DEFAULT = 6
SPLIT_RATIO_DEFAULT = 3.0
GAP_BRIDGE_DEFAULT = 21
CORE_FRAC_DEFAULT = 0.5
DIAMOND_A_ANGSTROM_DEFAULT = 3.5667
CONTAM_TOL_DEG_DEFAULT = 0.07
N_MC_DEFAULT = 200_000
RNG_SEED_DEFAULT = 0
SIGMA_G_DEFAULT = 5e-3
MIN_REFLECTIONS_DEFAULT = 20

# Stage "index_known_cell" (handoff §5.4.2/§5.6: re-index a leftover g-vector
# pool against an already-known cell at an unknown orientation).
RANDOM_SEARCH_N_DEFAULT = 200_000
RANDOM_SEARCH_CHUNK_DEFAULT = 2_000
RANDOM_SEARCH_TOL_DEFAULT = 0.15
RANDOM_SEARCH_MIN_ACCEPT_DEFAULT = 20
RANDOM_SEARCH_N_NULL_DEFAULT = 20
RANDOM_SEARCH_Z_THRESH_DEFAULT = 5.0


def panel_dir_name(panel_id: int) -> str:
    """Canonical per-panel output folder name.

    Phase 1 drops the handoff §6 data contract's stage-position suffix
    (``det_x``/``eiger_y``/``eiger_z``) -- those motor positions only matter
    for the Phase 2+ geometry-resolution tiers (handoff §5.1: exact-anchor
    lookup / regression against a calibration sweep table keyed by stage
    position), which this phase does not implement. Every panel here carries
    its own full calibration file, so the stage position that produced that
    calibration is not needed to identify or use it.
    """
    return f"panel_{int(panel_id):02d}"


def _apply_stack_corrections(frames: np.ndarray, corr: dict) -> np.ndarray:
    """Dark-subtract / bright-correct / background-subtract a whole
    ``(n_frames, nz, ny)`` stack against one shared per-pixel field each
    (``corr`` keys: ``dark``, ``bright``, ``bright_mode`` ("divide"|
    "subtract"), ``background`` -- any may be ``None``). Called from
    ``_stage_ingest`` AFTER sentinel-pixel zeroing so a raw sentinel value is
    never arithmetically altered before being recognized as one.

    Mirrors ``midas_gui.helpers.apply_field_corrections`` (same order: dark
    -> bright -> background -> clip>=0) but is duplicated here, vectorized
    over the frame axis instead of per-2D-frame, rather than imported --
    this module must stay free of ``midas_gui`` imports (see module
    docstring) to stay liftable into a standalone package.

    Operates in-place on ``frames`` (returned, not copied) and keeps
    everything in ``float32`` -- at full detector resolution and a few
    hundred frames, each `out = out - x`-style reassignment used to allocate
    a brand-new full-stack-sized array; with dark+bright+background all
    configured that was 3-4 extra multi-GB allocations per ingest run for no
    reason, on top of doubling the footprint via float64. See
    DECISIONS 2026-10-04 (ingest performance).
    """
    dark, bright, background = corr.get("dark"), corr.get("bright"), corr.get("background")
    if dark is None and bright is None and background is None:
        return frames
    frame_shape = frames.shape[1:]

    def _checked(field, label):
        if field is None:
            return None
        arr = np.asarray(field, dtype=np.float32)
        if arr.shape != frame_shape:
            print(f"[solve-cell] ingest: {label} shape {arr.shape} != frame shape "
                  f"{frame_shape} -- skipped")
            return None
        return arr

    out = frames
    d = _checked(dark, "dark")
    if d is not None:
        out -= d[None, :, :]
    b = _checked(bright, "bright")
    if b is not None:
        if d is not None:
            b = b - d
        if corr.get("bright_mode", "divide") == "subtract":
            out -= b[None, :, :]
        else:
            b = np.clip(b, 1e-9, None)
            out /= b[None, :, :]
            out *= float(np.mean(b))
    g = _checked(background, "background")
    if g is not None:
        out -= g[None, :, :]
    np.clip(out, 0.0, None, out=out)
    return out


def run_stage(stage: str, cfg: dict) -> dict:
    """Dispatch to one pipeline stage -- the sole entry point a worker thread
    calls (handoff §4.3's ``SolveCellWorker`` template)."""
    stages = {
        "ingest": _stage_ingest,
        "ingest_pooled": _stage_ingest_pooled,
        "diamond_filter": _stage_diamond_filter,
        "ab_initio": _stage_ab_initio,
        "refine": _stage_refine,
        "index_known_cell": _stage_index_known_cell,
    }
    if stage not in stages:
        raise ValueError(f"unknown solve-cell stage {stage!r}")
    return stages[stage](cfg)


# ═════════════════════════════════════════════════════════════════════════════
#  Stage 1: ingest -- raw frames -> background-subtracted 3-D blobs -> g-vectors
# ═════════════════════════════════════════════════════════════════════════════

def _stage_ingest(cfg: dict) -> dict:
    """``cfg`` keys: ``frames`` (raw (n_loaded_frames, n_pix_z, n_pix_y)
    array), ``geometry`` (dict: Lsd, BC_y, BC_z, ty, tz, wavelength_A, px_um,
    nrpixels_y, nrpixels_z, omega_ref_frame_idx, omega_ref_deg,
    omega_last_frame_idx, omega_step_deg), ``mask`` ({low_count_threshold,
    grow, optional user_mask}), ``blobs`` ({threshold, min_vol, split_ratio,
    gap_bridge, core_frac, optional sector_candidates -- passed to
    ``choose_sectors`` as its azimuth-sector grid search, defaults to its own
    ``SECTOR_CANDIDATES_DEFAULT``; narrowing this once a panel/geometry's
    winning ``n_sectors`` is known skips the other candidates' full-stack
    passes on later runs}), optional ``live_frames`` ({frac_of_median}),
    optional ``sentinel``, optional ``panel_dir`` (Path -- if given, writes
    ``spots_g.csv``/``ingest_summary.json`` there), optional
    ``preview_frame_index`` (int, default 0, clamped to the live/kept stack --
    which single frame's background-subtracted image + the detector mask are
    captured into the returned ``preview`` key, for a GUI's Detector view;
    only one frame is kept, not the whole subtracted stack, to avoid holding
    a second full-size copy in memory).

    Frame/omega pairing: frame index ``omega_ref_frame_idx`` is defined to be
    at ``omega_ref_deg``, with every frame stepping by ``omega_step_deg``.
    Frames outside ``[omega_ref_frame_idx, omega_last_frame_idx]`` have no
    defined omega and are dropped before any other processing (not
    extrapolated) -- this is the frame/omega window a GUI panel configures.

    Returned ``spots_df`` carries a ``loaded_frame_idx`` column (each spot's
    blob-centroid frame, rounded and mapped back to the ORIGINALLY loaded
    stack's indexing, i.e. ``cfg["frames"]``'s own axis-0 index before the
    omega window/``live_frames`` filtering above) and the returned
    ``preview`` dict carries the matching ``loaded_frame_index`` for
    ``preview_frame_index`` -- together these let a GUI compare "which spot
    is closest to the frame I'm currently showing" on one consistent basis,
    since the kept/live stack ``spots_df["frame"]`` otherwise indexes into is
    a different, shorter axis than what a frame-nav scrub bar shows.

    ``mask.user_mask``, when given, is a (n_pix_z, n_pix_y) boolean/0-1 array
    (1 = masked) from the panel's own mask sources (e.g. a GUI
    ``DataLoaderPanel``'s ``composite_mask()``) -- unioned into the mask this
    stage builds internally, same as the sentinel-pixel mask already is.

    Optional ``corrections`` ({dark, bright, bright_mode, background}, any
    may be absent/None) -- the panel's own dark/bright/background fields
    (e.g. a GUI ``DataLoaderPanel``'s ``dark()``/``bright()``/
    ``bright_mode()``/``background()``), applied via
    ``_apply_stack_corrections`` right after sentinel zeroing -- frames must
    stay in their genuinely raw form up to that point so the sentinel
    equality check isn't broken by correction arithmetic or a float32 cast.

    Deliberate deviation from midas_defect's own ``build_mask`` default
    (``low_count_threshold=20``): the reference analysis found that default
    masks 80-99%+ of a single-crystal-in-DAC chip with no diffuse baseline to
    set a "low" plateau against, so this stage defaults to 0.0 instead --
    exposed, not silently hardcoded either way.
    """
    import torch
    from midas_defect.ingest import build_mask, choose_sectors, find_blobs_3d, live_frames
    from midas_defect.geometry import Geometry, detector_angle_maps, pixel_to_qlab, qlab_to_qsample

    geom_cfg = cfg["geometry"]
    frames_loaded = np.asarray(cfg["frames"])
    sentinel = cfg.get("sentinel", SENTINEL)
    n_frames_loaded = frames_loaded.shape[0]

    ref_idx = int(geom_cfg.get("omega_ref_frame_idx", 0))
    ref_deg = geom_cfg.get("omega_ref_deg", 0.0)
    last_idx = int(geom_cfg.get("omega_last_frame_idx", n_frames_loaded - 1))
    step = geom_cfg.get("omega_step_deg", 0.0)
    last_idx = min(last_idx, n_frames_loaded - 1)
    ref_idx = max(ref_idx, 0)
    if ref_idx > last_idx:
        raise ValueError(
            f"omega window invalid: first frame idx {ref_idx} > last frame idx {last_idx}")
    idx_loaded = np.arange(n_frames_loaded)
    omega_window_mask = (idx_loaded >= ref_idx) & (idx_loaded <= last_idx)
    n_outside_omega_window = int((~omega_window_mask).sum())
    frames_raw = frames_loaded[omega_window_mask]
    n_frames_total = frames_raw.shape[0]

    print(f"[solve-cell] ingest: {n_frames_total}/{n_frames_loaded} frames in omega window "
          f"[{ref_idx}, {last_idx}] ({n_outside_omega_window} excluded), "
          f"Lsd={geom_cfg['Lsd']:.2f} um, wavelength={geom_cfg['wavelength_A']:.6f} A")

    frames = frames_raw.astype(np.float32)
    is_sentinel = frames_raw == sentinel
    is_sentinel_persistent = is_sentinel.all(axis=0)
    frames[is_sentinel] = 0.0
    frames = _apply_stack_corrections(frames, cfg.get("corrections", {}))

    omega_deg_all = ref_deg + np.arange(n_frames_total) * step

    live_cfg = cfg.get("live_frames", {})
    live_mask, _maxima = live_frames(frames, frac_of_median=live_cfg.get(
        "frac_of_median", LIVE_FRAMES_FRAC_DEFAULT))
    n_live = int(live_mask.sum())
    print(f"[solve-cell] live_frames: {n_live}/{n_frames_total} frames kept")
    frames = frames[live_mask]
    omega_deg = omega_deg_all[live_mask]
    is_sentinel_persistent_live = is_sentinel[live_mask].all(axis=0) if n_live else is_sentinel_persistent
    # Maps a kept-stack position (what spots["frame"] and preview_idx index
    # into, after the omega-window + live_frames filtering above) back to its
    # ORIGINALLY loaded-stack frame index (what a GUI's DataLoaderPanel/
    # frame-nav scrub bar indexes into) -- needed so a Detector view can tell
    # which loaded frame a given spot is closest to.
    kept_loaded_idx = idx_loaded[omega_window_mask][live_mask]

    mask_cfg = cfg.get("mask", {})
    mres = build_mask(frames, low_count_threshold=mask_cfg.get(
        "low_count_threshold", LOW_COUNT_THRESHOLD_DEFAULT), grow=mask_cfg.get("grow", MASK_GROW_DEFAULT))
    mask = mres.mask | is_sentinel_persistent_live
    user_mask = mask_cfg.get("user_mask")
    if user_mask is not None:
        user_mask = np.asarray(user_mask).astype(bool)
        if user_mask.shape != mask.shape:
            raise ValueError(
                f"mask.user_mask shape {user_mask.shape} does not match detector shape {mask.shape}")
        mask = mask | user_mask
    n_masked = int(mask.sum())
    print(f"[solve-cell] build_mask: {n_masked}/{mask.size} px masked "
          f"({100.0 * n_masked / mask.size:.3f}%)")

    geom = Geometry(
        lsd_um=geom_cfg["Lsd"], bcy_px=geom_cfg["BC_y"], bcz_px=geom_cfg["BC_z"],
        px_um=geom_cfg["px_um"], wavelength_A=geom_cfg["wavelength_A"],
        n_pix_y=geom_cfg["nrpixels_y"], n_pix_z=geom_cfg["nrpixels_z"],
        omega_first_deg=ref_deg, omega_step_deg=step,
        n_frames=len(frames), tx_deg=0.0,
        ty_deg=geom_cfg.get("ty", 0.0), tz_deg=geom_cfg.get("tz", 0.0),
    )
    tth_deg, az_deg = detector_angle_maps(geom)

    blob_cfg = cfg.get("blobs", {})
    threshold = blob_cfg.get("threshold", BLOB_THRESHOLD_DEFAULT)
    min_vol = blob_cfg.get("min_vol", BLOB_MIN_VOL_DEFAULT)
    sector_candidates = tuple(blob_cfg.get("sector_candidates", SECTOR_CANDIDATES_DEFAULT))

    # choose_sectors grid-searches `sector_candidates`, each a full per-frame
    # subtract_background + count_signed_blobs pass -- the dominant ingest
    # cost at full detector resolution (measured: minutes per candidate).
    # Its winning BackgroundChoice.stack IS subtract_background's output at
    # that n_sectors (verified bit-identical) -- previously this stage threw
    # that away and reran subtract_background a 6th time "to match the
    # reference scripts verbatim"; that rerun is redundant, not a behavior
    # difference, so it's gone. See DECISIONS 2026-10-04 (ingest performance).
    bg_choice = choose_sectors(frames, tth_deg, az_deg, mask, threshold=threshold, min_vol=min_vol,
                               candidates=sector_candidates)
    print(f"[solve-cell] choose_sectors: n_sectors={bg_choice.n_sectors} "
          f"(candidates={sector_candidates})")
    sub = bg_choice.stack

    preview_idx = int(cfg.get("preview_frame_index", 0))
    preview_idx = min(max(preview_idx, 0), len(sub) - 1) if len(sub) else 0
    preview = {
        "frame_index": preview_idx,
        # The ORIGINALLY loaded-stack index this kept-stack frame corresponds
        # to -- lets a GUI compare against panel.loader.frame_index() on one
        # consistent basis. None when there is no kept stack at all.
        "loaded_frame_index": int(kept_loaded_idx[preview_idx]) if len(kept_loaded_idx) else None,
        "background_subtracted": np.array(sub[preview_idx]) if len(sub) else None,
        # A single frame is a poor background-subtraction diagnostic for a
        # rotation series: a real Bragg reflection only satisfies the
        # diffraction condition for a handful of frames out of hundreds, so
        # an arbitrary single frame is, more often than not, almost entirely
        # at the background floor even when the subtraction worked
        # correctly -- confirmed on real data (Ge-oP32 c1 panel 06): a
        # prominent spot's frame still has a 90th-percentile unmasked value
        # of ~3 counts, reading as "no background" to the eye even though a
        # ~1800-count spot is present a few pixels wide. The max projection
        # collapses every kept/live frame's spots (and any residual
        # structure) into one image. `sub` is already the full subtracted
        # stack fully materialized by `choose_sectors` above -- this is one
        # extra reduction over an array already in memory, not a new
        # full-stack copy.
        "background_subtracted_max_projection": np.asarray(sub.max(axis=0)) if len(sub) else None,
        "mask": np.array(mask),
    }

    spots, counts = find_blobs_3d(
        sub, mask, threshold=threshold, min_vol=min_vol,
        split_ratio=blob_cfg.get("split_ratio", SPLIT_RATIO_DEFAULT),
        gap_bridge=blob_cfg.get("gap_bridge", GAP_BRIDGE_DEFAULT),
        core_frac=blob_cfg.get("core_frac", CORE_FRAC_DEFAULT),
        return_counts=True)
    print(f"[solve-cell] find_blobs_3d counts: {counts}")
    spots = spots[spots["n_frames"] >= 2].reset_index(drop=True)
    print(f"[solve-cell] {len(spots)} spots with n_frames>=2")

    if len(spots):
        rows = spots["row"].to_numpy()
        cols = spots["col"].to_numpy()
        spot_frame = spots["frame"].to_numpy()
        spot_omega_deg = np.interp(spot_frame, np.arange(len(omega_deg)), omega_deg)
        # Nearest ORIGINALLY loaded-stack frame index for each spot (its
        # blob centroid, rounded, mapped through kept_loaded_idx) -- lets a
        # GUI's Detector view show a spot's circle on the one loaded frame
        # closest to it.
        spot_frame_round = np.clip(
            np.round(spot_frame).astype(int), 0, len(kept_loaded_idx) - 1)
        spots["loaded_frame_idx"] = kept_loaded_idx[spot_frame_round]

        qlab = pixel_to_qlab(rows, cols, geom, device="cpu")
        omega_rad = torch.deg2rad(torch.as_tensor(spot_omega_deg, dtype=qlab.dtype))
        qsample = qlab_to_qsample(qlab, omega_rad).detach().cpu().numpy()
        qlab_np = qlab.detach().cpu().numpy()

        q_norm = np.linalg.norm(qsample, axis=1)
        spots["omega_deg"] = spot_omega_deg
        spots["qlab_x"], spots["qlab_y"], spots["qlab_z"] = qlab_np[:, 0], qlab_np[:, 1], qlab_np[:, 2]
        spots["qsample_x"], spots["qsample_y"], spots["qsample_z"] = (
            qsample[:, 0], qsample[:, 1], qsample[:, 2])
        spots["q_norm"] = q_norm
        spots["two_theta_deg"] = np.degrees(2.0 * np.arcsin(
            np.clip(q_norm * geom_cfg["wavelength_A"] / (4.0 * math.pi), -1.0, 1.0)))
    else:
        spots["loaded_frame_idx"] = np.array([], dtype=np.int64)
        for col in ("omega_deg", "qlab_x", "qlab_y", "qlab_z",
                    "qsample_x", "qsample_y", "qsample_z", "q_norm", "two_theta_deg"):
            spots[col] = np.array([], dtype=np.float64)

    summary = {
        "n_frames_loaded": n_frames_loaded, "n_outside_omega_window": n_outside_omega_window,
        "n_frames": n_frames_total, "n_live": n_live,
        "n_masked_px": n_masked, "n_masked_px_total": int(mask.size),
        "n_sectors": int(bg_choice.n_sectors), "counts": counts, "n_spots": len(spots),
    }

    panel_dir = cfg.get("panel_dir")
    if panel_dir is not None:
        panel_dir = Path(panel_dir)
        panel_dir.mkdir(parents=True, exist_ok=True)
        spots.to_csv(panel_dir / "spots_g.csv", index=False)
        (panel_dir / "ingest_summary.json").write_text(json.dumps(summary, indent=2))
        print(f"[solve-cell] wrote {panel_dir / 'spots_g.csv'}")

    return {"spots_df": spots, "summary": summary, "panel_dir": panel_dir, "preview": preview}


def _stage_ingest_pooled(cfg: dict) -> dict:
    """Multi-panel counterpart of :func:`_stage_ingest` (handoff §5.2/§10
    "multi-panel pooling"): run ingest independently on each configured
    panel, then concatenate every panel's own ``spots_df`` into one pooled
    g-vector set tagged by ``panel_id``.

    This is sound, not just convenient: each panel's ``_stage_ingest`` already
    converts its blobs to ``qsample`` via ``qlab_to_qsample`` at the panel's
    own geometry and the shared omega axis -- i.e. into the ONE sample-frame
    coordinate system every panel of the same rotation series shares,
    regardless of where that panel physically sits. So panels combine by
    plain concatenation once each is independently ingested; nothing about
    diamond filtering/ab-initio/refinement downstream needs to know how many
    panels contributed a given row. (This is exactly what the two-panel
    spinel regression test already demonstrates, one stage later, by
    concatenating two panels' own post-ingest ``spots_g_candidate.csv``
    files and recovering a cell closer to the known 6-panel pooled result
    than either panel alone.)

    ``cfg`` keys: ``panels`` (list of per-panel cfg dicts -- each exactly
    what :func:`_stage_ingest` itself expects, PLUS its own ``panel_id``),
    optional ``pooled_dir`` (Path -- if given, writes ``spots_g_pooled.csv``
    and ``ingest_summary.json`` there, alongside each panel's own
    ``panel_dir`` outputs, which :func:`_stage_ingest` still writes itself).

    Panels are processed ONE AT A TIME, and each panel's own resolved raw
    frame stack (``frames_loader()`` or an already-resolved ``frames``
    array) is dropped before the next panel starts. Holding every
    configured panel's full raw stack in memory at once would multiply the
    exact memory pressure the single-panel ingest performance fix already
    had to solve (see DECISIONS 2026-10-04) by the panel count -- this is
    why ``frames_loader`` resolution happens HERE, per panel, rather than
    once up front by the caller the way the single-panel ``ingest`` stage's
    GUI worker does it before calling ``run_stage``.
    """
    import pandas as pd

    panels_cfg = cfg["panels"]
    if not panels_cfg:
        raise ValueError("ingest_pooled: no panels given")

    per_panel: dict = {}
    combined_frames = []
    for pcfg in panels_cfg:
        panel_id = pcfg.pop("panel_id")
        if "frames_loader" in pcfg and "frames" not in pcfg:
            loader = pcfg.pop("frames_loader")
            print(f"[solve-cell] ingest_pooled: panel {panel_id} loading raw frames…")
            pcfg["frames"] = loader()
        print(f"[solve-cell] ingest_pooled: panel {panel_id} ingest starting")
        result = dict(_stage_ingest(pcfg))
        pcfg.clear()   # drop the resolved frame-stack reference ASAP, before the next panel loads
        spots = result["spots_df"].copy()
        spots.insert(0, "panel_id", panel_id)
        result["spots_df"] = spots
        per_panel[panel_id] = result
        combined_frames.append(spots)
        print(f"[solve-cell] ingest_pooled: panel {panel_id} done, {len(spots)} spots")

    spots_df = pd.concat(combined_frames, ignore_index=True)
    n_total = len(spots_df)
    summary = {
        "n_panels": len(panels_cfg),
        "panel_ids": list(per_panel.keys()),
        "n_spots_total": n_total,
        "per_panel": {str(pid): r["summary"] for pid, r in per_panel.items()},
    }
    print(f"[solve-cell] ingest_pooled: {n_total} spots total across {len(per_panel)} panels")

    pooled_dir = cfg.get("pooled_dir")
    if pooled_dir is not None:
        pooled_dir = Path(pooled_dir)
        pooled_dir.mkdir(parents=True, exist_ok=True)
        spots_df.to_csv(pooled_dir / "spots_g_pooled.csv", index=False)
        (pooled_dir / "ingest_summary.json").write_text(json.dumps(summary, indent=2))
        print(f"[solve-cell] wrote {pooled_dir / 'spots_g_pooled.csv'}")

    return {"spots_df": spots_df, "summary": summary, "per_panel": per_panel, "pooled_dir": pooled_dir}


# ═════════════════════════════════════════════════════════════════════════════
#  Stage 2: diamond/anvil filter -- pure 2-theta proximity test
# ═════════════════════════════════════════════════════════════════════════════

def _diamond_tth_lines_deg(wavelength_A: float, a_angstrom: float = DIAMOND_A_ANGSTROM_DEFAULT,
                            hkl_max: int = 8) -> np.ndarray:
    """2-theta positions of diamond-allowed reflections at this wavelength.

    Computed from diamond's own well-known cubic cell + Fd-3m selection rule
    (h,k,l all odd, or all even with h+k+l == 0 mod 4 -- e.g. (111)/(220)/
    (311)/(400) allowed, (200)/(222) forbidden), rather than reusing a
    different experiment's precomputed line-table CSV as the reference
    scripts did -- a deliberate, physics-only, wavelength-correct
    substitution (handoff §5.3 calls this "physics only, reusable across
    every dataset at this facility that uses the same anvil type", which only
    holds if the wavelength matches; computing it here makes that true for
    any wavelength).
    """
    lines = set()
    for h in range(0, hkl_max + 1):
        for k in range(0, hkl_max + 1):
            for l in range(0, hkl_max + 1):
                if h == k == l == 0:
                    continue
                all_odd = (h % 2) and (k % 2) and (l % 2)
                all_even = not (h % 2) and not (k % 2) and not (l % 2)
                if not (all_odd or (all_even and (h + k + l) % 4 == 0)):
                    continue
                d = a_angstrom / math.sqrt(h * h + k * k + l * l)
                sin_theta = wavelength_A / (2.0 * d)
                if sin_theta >= 1.0:
                    continue
                tth = math.degrees(2.0 * math.asin(sin_theta))
                lines.add(round(tth, 6))
    return np.array(sorted(lines))


def _nearest_line_distance(values: np.ndarray, lines: np.ndarray) -> np.ndarray:
    """``min(|values - lines|)`` per value, via searchsorted (avoids an
    O(n_values * n_lines) broadcast -- needed since the Monte-Carlo null test
    below calls this on arrays with ~1e7 entries)."""
    if len(lines) == 0 or len(values) == 0:
        return np.full(values.shape, np.inf)
    idx = np.clip(np.searchsorted(lines, values), 1, len(lines) - 1)
    left, right = lines[idx - 1], lines[idx]
    return np.minimum(np.abs(values - left), np.abs(values - right))


def _stage_diamond_filter(cfg: dict) -> dict:
    """``cfg`` keys: ``spots_df``, ``wavelength_A``, optional
    ``diamond_a_angstrom``, ``contam_tol_deg``, ``n_mc``, ``rng_seed``,
    optional ``panel_dir`` / ``pooled_dir`` for writing outputs."""
    spots = cfg["spots_df"].copy()
    wavelength_A = cfg["wavelength_A"]
    a_angstrom = cfg.get("diamond_a_angstrom", DIAMOND_A_ANGSTROM_DEFAULT)
    tol_deg = cfg.get("contam_tol_deg", CONTAM_TOL_DEG_DEFAULT)

    lines = _diamond_tth_lines_deg(wavelength_A, a_angstrom)
    tth = spots["two_theta_deg"].to_numpy()
    delta = _nearest_line_distance(tth, lines)
    spots["delta_to_diamond_deg"] = delta
    spots["is_diamond"] = delta < tol_deg

    n_total = len(spots)
    n_diamond = int(spots["is_diamond"].sum())
    candidate = spots[~spots["is_diamond"]].reset_index(drop=True)

    null_mean = null_std = z_score = float("nan")
    if n_total and len(lines):
        n_mc = cfg.get("n_mc", N_MC_DEFAULT)
        rng = np.random.default_rng(cfg.get("rng_seed", RNG_SEED_DEFAULT))
        tth_lo, tth_hi = float(tth.min()), float(tth.max())
        draws = rng.uniform(tth_lo, tth_hi, size=(n_mc, n_total))
        null_delta = _nearest_line_distance(draws, lines)
        null_match = (null_delta < tol_deg).sum(axis=1)
        null_mean, null_std = float(null_match.mean()), float(null_match.std())
        z_score = (n_diamond - null_mean) / null_std if null_std > 0 else float("nan")

    summary = {
        "n_spots": n_total, "n_diamond_flagged": n_diamond, "n_kept": len(candidate),
        "null_mean": null_mean, "null_std": null_std, "z_score": z_score,
        "tolerance_deg": tol_deg, "diamond_a_angstrom": a_angstrom,
    }
    z_text = f"{z_score:.1f}" if z_score == z_score else "n/a"
    print(f"[solve-cell] diamond_filter: {n_diamond}/{n_total} flagged (z={z_text})")

    panel_dir = cfg.get("panel_dir")
    if panel_dir is not None:
        panel_dir = Path(panel_dir)
        spots.to_csv(panel_dir / "spots_g_diamond_flagged.csv", index=False)
        candidate.to_csv(panel_dir / "spots_g_candidate.csv", index=False)
    pooled_dir = cfg.get("pooled_dir")
    if pooled_dir is not None:
        pooled_dir = Path(pooled_dir)
        pooled_dir.mkdir(parents=True, exist_ok=True)
        (pooled_dir / "diamond_filter_summary.json").write_text(json.dumps(summary, indent=2))

    return {"flagged_df": spots, "candidate_df": candidate, "summary": summary}


# ═════════════════════════════════════════════════════════════════════════════
#  Stage 3: blind ab-initio indexing
# ═════════════════════════════════════════════════════════════════════════════

def _stage_ab_initio(cfg: dict) -> dict:
    """``cfg`` keys: ``candidate_df``, optional ``sigma_g``,
    ``min_reflections``, ``tol``, optional ``pooled_dir``."""
    from midas_hkls.ab_initio import index_ab_initio

    candidate = cfg["candidate_df"]
    g = candidate[["qsample_x", "qsample_y", "qsample_z"]].to_numpy()
    sigma_g = cfg.get("sigma_g", SIGMA_G_DEFAULT)
    min_reflections = cfg.get("min_reflections", MIN_REFLECTIONS_DEFAULT)
    kwargs = {}
    if cfg.get("tol") is not None:
        kwargs["tol"] = cfg["tol"]

    print(f"[solve-cell] ab_initio: indexing {len(g)} candidate reflections "
          f"(sigma_g={sigma_g:g}, min_reflections={min_reflections})")
    res = index_ab_initio(g, two_pi=True, sigma_g=sigma_g, min_reflections=min_reflections, **kwargs)

    result = {
        "success": bool(res.success),
        "n_reflections": int(res.n_reflections),
        "n_indexed": int(res.n_indexed),
        "indexed_fraction": float(res.indexed_fraction),
        "notes": list(res.notes),
        "cell": list(res.cell) if res.cell is not None else None,
    }
    print(f"[solve-cell] ab_initio: success={result['success']} "
          f"indexed={result['n_indexed']}/{result['n_reflections']} "
          f"({result['indexed_fraction'] * 100:.1f}%)")

    pooled_dir = cfg.get("pooled_dir")
    if pooled_dir is not None:
        pooled_dir = Path(pooled_dir)
        pooled_dir.mkdir(parents=True, exist_ok=True)
        (pooled_dir / "ab_initio_result.json").write_text(json.dumps(result, indent=2))

    return {"ab_initio_result": result, "ab_initio_raw": res, "g_2pi": g, "sigma_g": sigma_g}


# ═════════════════════════════════════════════════════════════════════════════
#  Stage 4: free UB/cell refinement
# ═════════════════════════════════════════════════════════════════════════════

def _to_inverse_d(g_2pi: np.ndarray, sigma_g_2pi: float):
    """Boundary helper: ``midas_defect``/``index_ab_initio``'s ``2*pi/d``
    convention -> the plain ``1/d`` convention ``refine_ub_from_gvectors``
    expects (it has no ``two_pi`` flag and returns whatever convention its
    input carries -- see module docstring)."""
    return g_2pi / TWO_PI, sigma_g_2pi / TWO_PI


def _stage_refine(cfg: dict) -> dict:
    """``cfg`` keys: ``ab_initio_raw`` (the ``AbInitioResult`` from
    ``_stage_ab_initio``), ``g_2pi`` (that stage's full candidate g array),
    optional ``sigma_g``, optional ``pooled_dir``."""
    from midas_hkls.ub_refine import refine_ub_from_gvectors
    from midas_hkls.lattice_symmetry import holohedry_from_fit
    from midas_hkls.conventional import to_conventional

    ab = cfg["ab_initio_raw"]
    if not ab.success:
        notes = "; ".join(ab.notes) if ab.notes else "no notes"
        raise ValueError(f"Cannot refine: ab-initio indexing did not succeed ({notes})")

    g_2pi = cfg["g_2pi"][ab.indexed_mask]
    sigma_g_2pi = cfg.get("sigma_g", SIGMA_G_DEFAULT)
    g_1d, sigma_g_1d = _to_inverse_d(g_2pi, sigma_g_2pi)

    print(f"[solve-cell] refine: free-refining UB from {len(ab.hkl)} indexed reflections")
    fit = refine_ub_from_gvectors(ab.hkl, g_1d, sigma_g=sigma_g_1d)
    holo = holohedry_from_fit(fit)
    # Matches the validated reference scripts' own call exactly
    # (to_conventional(fit.cell), not the fit-covariance-aware
    # to_conventional_from_fit) -- the covariance-derived tolerance was found
    # during implementation to be tighter than the data can actually support
    # here, missing the cubic symmetry the plain default-window search
    # correctly detects.
    conv = to_conventional(fit.cell)

    result = {
        "n_reflections": int(fit.n_reflections),
        "cell": [float(v) for v in fit.cell],
        "cell_sigma": [float(v) for v in fit.cell_sigma],
        "rms_drlv": float(fit.rms_drlv),
        "determined": dict(fit.determined),
        "holohedry_system": holo.system, "holohedry_order": int(holo.order),
        "conventional_cell": [float(v) for v in conv.cell],
        "conventional_system": conv.system,
        "conventional_is_standard": bool(conv.is_standard),
        "conventional_centring_index": int(conv.centring_index),
    }
    print(f"[solve-cell] refine: cell={tuple(round(v, 4) for v in result['cell'])} "
          f"rms_drlv={result['rms_drlv']:.5f} holohedry={holo.system}")

    pooled_dir = cfg.get("pooled_dir")
    if pooled_dir is not None:
        pooled_dir = Path(pooled_dir)
        pooled_dir.mkdir(parents=True, exist_ok=True)
        (pooled_dir / "refine_result.json").write_text(json.dumps(result, indent=2))

    return {"refine_result": result}


# ═════════════════════════════════════════════════════════════════════════════
#  Stage 5: index a leftover g-vector pool against a KNOWN cell, unknown
#  orientation -- a second domain/grain of an already-solved material, not a
#  fresh ab-initio search. See handoff §5.4.2 (method) / §5.6 (when this is
#  used, iteratively, to find successive domains in a leftover pool).
# ═════════════════════════════════════════════════════════════════════════════

def _bmatrix_from_cell(cell, two_pi: bool = False) -> np.ndarray:
    """Columns-convention reciprocal-lattice B-matrix (``g = B @ hkl``) from
    ``(a, b, c, alpha, beta, gamma)``. Built from ``midas_hkls``'s own public
    ``Lattice.reciprocal_cartesian_vectors()`` -- the same construction
    ``midas_hkls.cell_constrained``'s private ``_B_of`` uses internally --
    rather than re-deriving the triclinic formula locally, so this module
    doesn't carry a second, unvalidated copy of that crystallography."""
    from midas_hkls import Lattice

    lat = Lattice(a=cell[0], b=cell[1], c=cell[2], alpha=cell[3], beta=cell[4], gamma=cell[5])
    B = np.asarray(lat.reciprocal_cartesian_vectors(), float).T
    return B * TWO_PI if two_pi else B


def _random_rotation_search(B0: np.ndarray, g_work: np.ndarray, n: int,
                             rng: np.random.Generator, tol: float,
                             chunk: int = RANDOM_SEARCH_CHUNK_DEFAULT):
    """Handoff §5.4.2: try ``n`` random orientations of the fixed basis
    ``B0`` (chunked so the whole search is never materialized at once) and
    keep whichever puts the most ``g_work`` rows within ``tol`` of an integer
    hkl. Returns ``(best_n, best_UB)`` -- ``best_UB`` is ``None`` only when
    ``g_work`` has no rows at all."""
    from scipy.spatial.transform import Rotation

    best_n, best_UB = -1, None
    done = 0
    while done < n:
        m = min(chunk, n - done)
        rots = Rotation.random(m, random_state=rng).as_matrix()
        UB = rots @ B0[None, :, :]
        hkl_float = np.einsum('mij,gj->mgi', np.linalg.inv(UB), g_work)
        resid = np.max(np.abs(hkl_float - np.round(hkl_float)), axis=2)
        n_match = (resid < tol).sum(axis=1)
        i = int(np.argmax(n_match))
        if n_match[i] > best_n:
            best_n, best_UB = int(n_match[i]), UB[i]
        done += m
    return best_n, best_UB


def _stage_index_known_cell(cfg: dict) -> dict:
    """``cfg`` keys: ``g_2pi`` (leftover candidate pool, Nx3, 2*pi/d
    convention -- same as every other stage's ``g_2pi``), ``known_cell``
    (a, b, c, alpha, beta, gamma -- typically a previously solved domain's
    own refined cell), optional ``tol``, ``n_search``, ``chunk``,
    ``min_accept``, ``n_null_draws``, ``z_thresh``, ``rng_seed``, optional
    ``sigma_g``, optional ``pooled_dir``.

    No ``midas_hkls`` function does this already (confirmed:
    ``cell_constrained``/``cell_series`` both require ``hkl`` already
    assigned to each g-vector) -- this is a from-scratch random-orientation
    search against the fixed, known cell, accepted only if it clearly beats
    a null test run against the same search on direction-scrambled g-vectors
    (same magnitudes, randomized directions), then free-refined (cell AND
    orientation both open -- the seed is a hypothesis, never a constraint).
    A negative result (``success=False``) is a normal, expected outcome, not
    an exception -- handoff §5.6 step 4 insists a domain search ending
    without a match be reported plainly, not silently swallowed.
    """
    from midas_hkls.ub_refine import refine_ub_from_gvectors
    from midas_hkls.lattice_symmetry import holohedry_from_fit
    from midas_hkls.conventional import to_conventional

    g_2pi = np.asarray(cfg["g_2pi"], float)
    known_cell = tuple(float(v) for v in cfg["known_cell"])
    tol = cfg.get("tol", RANDOM_SEARCH_TOL_DEFAULT)
    n_search = cfg.get("n_search", RANDOM_SEARCH_N_DEFAULT)
    chunk = cfg.get("chunk", RANDOM_SEARCH_CHUNK_DEFAULT)
    min_accept = cfg.get("min_accept", RANDOM_SEARCH_MIN_ACCEPT_DEFAULT)
    n_null_draws = cfg.get("n_null_draws", RANDOM_SEARCH_N_NULL_DEFAULT)
    z_thresh = cfg.get("z_thresh", RANDOM_SEARCH_Z_THRESH_DEFAULT)
    sigma_g_2pi = cfg.get("sigma_g", SIGMA_G_DEFAULT)
    rng = np.random.default_rng(cfg.get("rng_seed", RNG_SEED_DEFAULT))

    n_candidates = len(g_2pi)
    print(f"[solve-cell] index_known_cell: {n_candidates} leftover reflections vs. cell "
          f"{tuple(round(v, 4) for v in known_cell)} ({n_search} orientations searched)")

    if n_candidates < min_accept:
        note = (f"{n_candidates} leftover reflections is below the floor of {min_accept} -- "
                "refusing rather than searching.")
        print(f"[solve-cell] index_known_cell: {note}")
        return {"success": False, "n_reflections": n_candidates, "notes": [note],
                "indexed_mask": np.zeros(n_candidates, dtype=bool)}

    g_work = g_2pi / TWO_PI
    B0 = _bmatrix_from_cell(known_cell, two_pi=False)
    best_n, best_UB = _random_rotation_search(B0, g_work, n_search, rng, tol, chunk)

    # Null test: identical search against direction-scrambled g-vectors (same
    # |g| per row, randomized direction) -- a real lattice match must clear
    # what chance alone achieves against the same vector count/magnitudes.
    g_norm = np.linalg.norm(g_work, axis=1)
    null_best = np.empty(n_null_draws, dtype=int)
    for i in range(n_null_draws):
        directions = rng.normal(size=g_work.shape)
        directions /= np.linalg.norm(directions, axis=1, keepdims=True)
        g_scrambled = directions * g_norm[:, None]
        null_best[i], _ = _random_rotation_search(B0, g_scrambled, n_search, rng, tol, chunk)
    null_mean, null_std = float(null_best.mean()), float(null_best.std())
    z_score = (best_n - null_mean) / null_std if null_std > 0 else float("inf")
    diagnostics = {"best_n": best_n, "null_mean": null_mean, "null_std": null_std,
                   "z_score": z_score, "n_null_draws": n_null_draws}
    print(f"[solve-cell] index_known_cell: best_n={best_n} null_mean={null_mean:.1f} "
          f"null_std={null_std:.2f} z={z_score:.1f}")

    if best_n < min_accept or not (z_score > z_thresh):
        note = (f"best match {best_n}/{n_candidates} did not clear both the floor of "
                f"{min_accept} and a z-score of {z_thresh} over the null (z={z_score:.1f}) -- "
                "no second domain found in this pool.")
        print(f"[solve-cell] index_known_cell: {note}")
        return {"success": False, "n_reflections": n_candidates, "notes": [note],
                "indexed_mask": np.zeros(n_candidates, dtype=bool), "diagnostics": diagnostics}

    UBI_seed = np.linalg.inv(best_UB)
    hkl_float = (UBI_seed @ g_work.T).T
    hkl_round = np.round(hkl_float)
    indexed_mask = np.max(np.abs(hkl_float - hkl_round), axis=1) < tol
    hkl_int = hkl_round[indexed_mask]
    n_indexed = int(indexed_mask.sum())
    print(f"[solve-cell] index_known_cell: seed accepted, {n_indexed}/{n_candidates} "
          f"reflections assigned -- free-refining")

    sigma_g_1d = sigma_g_2pi / TWO_PI
    fit = refine_ub_from_gvectors(hkl_int, g_work[indexed_mask], sigma_g=sigma_g_1d)
    holo = holohedry_from_fit(fit)
    conv = to_conventional(fit.cell)

    result = {
        "success": True,
        "n_reflections": n_candidates,
        "n_indexed": n_indexed,
        "indexed_fraction": n_indexed / n_candidates if n_candidates else 0.0,
        "indexed_mask": indexed_mask,
        "cell": [float(v) for v in fit.cell],
        "cell_sigma": [float(v) for v in fit.cell_sigma],
        "rms_drlv": float(fit.rms_drlv),
        "determined": dict(fit.determined),
        "holohedry_system": holo.system, "holohedry_order": int(holo.order),
        "conventional_cell": [float(v) for v in conv.cell],
        "conventional_system": conv.system,
        "conventional_is_standard": bool(conv.is_standard),
        "conventional_centring_index": int(conv.centring_index),
        "diagnostics": diagnostics, "notes": [],
    }
    print(f"[solve-cell] index_known_cell: cell={tuple(round(v, 4) for v in result['cell'])} "
          f"rms_drlv={result['rms_drlv']:.5f} holohedry={holo.system}")

    pooled_dir = cfg.get("pooled_dir")
    if pooled_dir is not None:
        pooled_dir = Path(pooled_dir)
        pooled_dir.mkdir(parents=True, exist_ok=True)
        (pooled_dir / "index_known_cell_result.json").write_text(
            json.dumps({k: v for k, v in result.items() if k != "indexed_mask"}, indent=2))

    return result
