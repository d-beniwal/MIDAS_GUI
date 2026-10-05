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
    """
    dark, bright, background = corr.get("dark"), corr.get("bright"), corr.get("background")
    if dark is None and bright is None and background is None:
        return frames
    frame_shape = frames.shape[1:]

    def _checked(field, label):
        if field is None:
            return None
        arr = np.asarray(field, dtype=np.float64)
        if arr.shape != frame_shape:
            print(f"[solve-cell] ingest: {label} shape {arr.shape} != frame shape "
                  f"{frame_shape} -- skipped")
            return None
        return arr

    out = frames
    d = _checked(dark, "dark")
    if d is not None:
        out = out - d[None, :, :]
    b = _checked(bright, "bright")
    if b is not None:
        if d is not None:
            b = b - d
        if corr.get("bright_mode", "divide") == "subtract":
            out = out - b[None, :, :]
        else:
            b = np.clip(b, 1e-9, None)
            out = out / b[None, :, :] * float(np.mean(b))
    g = _checked(background, "background")
    if g is not None:
        out = out - g[None, :, :]
    return np.clip(out, 0.0, None)


def run_stage(stage: str, cfg: dict) -> dict:
    """Dispatch to one pipeline stage -- the sole entry point a worker thread
    calls (handoff §4.3's ``SolveCellWorker`` template)."""
    stages = {
        "ingest": _stage_ingest,
        "diamond_filter": _stage_diamond_filter,
        "ab_initio": _stage_ab_initio,
        "refine": _stage_refine,
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
    gap_bridge, core_frac}), optional ``live_frames`` ({frac_of_median}),
    optional ``sentinel``, optional ``panel_dir`` (Path -- if given, writes
    ``spots_g.csv``/``ingest_summary.json`` there).

    Frame/omega pairing: frame index ``omega_ref_frame_idx`` is defined to be
    at ``omega_ref_deg``, with every frame stepping by ``omega_step_deg``.
    Frames outside ``[omega_ref_frame_idx, omega_last_frame_idx]`` have no
    defined omega and are dropped before any other processing (not
    extrapolated) -- this is the frame/omega window a GUI panel configures.

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
    from midas_defect.ingest import build_mask, choose_sectors, find_blobs_3d, live_frames, subtract_background
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

    frames = frames_raw.astype(np.float64)
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

    # choose_sectors already returns a subtracted .stack, but the reference
    # analysis re-runs subtract_background explicitly at the chosen
    # n_sectors -- matched here verbatim rather than "optimized" away.
    bg_choice = choose_sectors(frames, tth_deg, az_deg, mask, threshold=threshold, min_vol=min_vol)
    print(f"[solve-cell] choose_sectors: n_sectors={bg_choice.n_sectors}")
    sub = subtract_background(frames, tth_deg, az_deg, mask, n_sectors=bg_choice.n_sectors)

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

    return {"spots_df": spots, "summary": summary, "panel_dir": panel_dir}


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
