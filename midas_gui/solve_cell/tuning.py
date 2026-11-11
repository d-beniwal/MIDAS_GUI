"""Solve-cell ingest parameter tuning -- deterministic, Qt-free.

Implements ``.context/reference/solve_cell_ingest_tuning.md``: from the raw
frame stack alone, suggest ``low_count_threshold`` / ``sector_candidates`` /
``blob_thresh`` / ``blob_minvol`` / ``blob_minframes`` and report the evidence
table behind them plus a confidence grade. Advisory only -- it never edits a
project; a caller decides whether to apply the suggestion.

Pure function of its inputs (no RNG): the same frames + geometry give the same
suggestion, with any worker count. Parallelism is process-based, the stacks live
in temp-file memmaps shared by the workers (never pickled), and the scratch dir
is removed on exit.

Entry points: ``suggest_ingest_params(...)`` and the CLI
``python -m midas_gui.solve_cell.tuning --help``.

All numeric thresholds in the selection rules (15 % / 25 % plateau width, 5 %
negative ratio, noise multipliers) were derived from ONE dataset; they are
module constants so they can be re-tuned against a validation set.
"""
from __future__ import annotations

import contextlib
import math
import multiprocessing as mp
import os
import shutil
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from typing import Callable, Optional, Sequence

import numpy as np

from .pipeline import (BLOB_MIN_FRAMES_DEFAULT, BLOB_MIN_VOL_DEFAULT, BLOB_THRESHOLD_DEFAULT,
                       LIVE_FRAMES_FRAC_DEFAULT, SENTINEL, _apply_stack_corrections,
                       _calib_map_to_raw, _codes)

# ── tunable constants (see module docstring) ────────────────────────────────
SECTORS_DEFAULT = (1, 24, 96)          # candidate azimuth-sector counts
T_SIGMA_MULTIPLIERS = (5.5, 6.5, 8.0, 10.0, 13.5)   # blob thresholds, x noise sigma
CUTS = ((2, 1), (2, 3), (4, 3), (8, 3))             # (min_vol, min_frames) tabulated
MIN_FRAMES = 3                         # suggested min_frames (assumes >=3 frames/reflection)
NOISE_CUT = (2, 3)                     # cut used to rank sector candidates at T_lo
NEG_GROW_FACTOR, NEG_GROW_ABS = 1.5, 2     # rule 1: "negatives grew" margin
POS_NOT_FALLEN = 0.9                   # rule 1: "positives did not fall" (>= 90 % of ref)
TIE_FRAC = 0.05                        # rule 2: counts within 5 % tie -> fewer sectors
PLATEAU_RATIO = 1.3                    # max/min of the strict-corner counts
REAL_BAND = (0.9, 1.25)                # acceptable pos count / N_real
NEG_OVER_POS_MAX = 0.05
MASKED_FRAC_WARN = 0.02
PHOTON_COUNTING_MEDIAN = 20.0          # typical per-pixel median below this -> photon counting
OMEGA_STEP_WARN_DEG = 0.5


def _noop(*_a, **_k):
    return None


@contextlib.contextmanager
def _children_without_main():
    """Hide ``__main__`` from spawned workers for the duration of the pool.

    ``spawn`` re-imports the parent's ``__main__`` in every child. Under the GUI that is the
    whole Qt application (seconds and a few hundred MB per worker, for nothing: the workers
    only need this module, which is pickled by reference), and it fails outright when the
    parent was started from stdin/``-c``. Removing ``__file__`` and setting ``__spec__`` to
    None makes ``multiprocessing`` skip that import; both are restored on exit.
    """
    main = sys.modules.get("__main__")
    saved = {}
    if main is not None:
        for attr in ("__file__", "__spec__"):
            if attr in vars(main):
                saved[attr] = vars(main)[attr]
        vars(main).pop("__file__", None)
        main.__spec__ = None
    try:
        yield
    finally:
        if main is not None:
            vars(main).pop("__spec__", None)
            vars(main).update(saved)


# ═════════════════════════════════════════════════════════════════════════════
#  Pure selection rules (unit-testable, no data)
# ═════════════════════════════════════════════════════════════════════════════

def select_sectors(rows: Sequence[dict]) -> dict:
    """Pick the sector count from per-candidate rows.

    ``rows``: dicts with ``n_sectors`` and ``cuts`` = {(min_vol, min_frames):
    (pos, neg)} measured at the low (noise-dominated) threshold.

    Rule 1: reject a candidate if, against an already-accepted one, its negative
    count grew (> ``NEG_GROW_FACTOR`` * neg_ref + ``NEG_GROW_ABS``) while its
    positive count did not fall (>= ``POS_NOT_FALLEN`` * pos_ref) -- the model is
    fitting spots and leaving negative halos. Rule 2: among the rest, fewest
    noise-dominated positives; counts within ``TIE_FRAC`` tie -> fewer sectors.
    A zero negative count is NOT evidence of a good model (photon counting:
    negatives are bounded by the background level), so it is not used to rank.
    """
    ordered = sorted(rows, key=lambda r: r["n_sectors"])
    accepted, rejected = [], []
    for r in ordered:
        pos, neg = r["cuts"][NOISE_CUT]
        reason = None
        for a in accepted:
            apos, aneg = a["cuts"][NOISE_CUT]
            if neg > NEG_GROW_FACTOR * aneg + NEG_GROW_ABS and pos >= POS_NOT_FALLEN * apos:
                reason = (f"negatives grew ({aneg}->{neg}) while positives did not fall "
                          f"({apos}->{pos}) vs {a['n_sectors']} sectors")
                break
        (rejected if reason else accepted).append({**r, "reason": reason} if reason else r)
    if not accepted:                       # cannot happen (first row is always accepted), be safe
        accepted = [ordered[0]]
    best = min(a["cuts"][NOISE_CUT][0] for a in accepted)
    tied = [a for a in accepted if a["cuts"][NOISE_CUT][0] <= best * (1.0 + TIE_FRAC)]
    winner = min(tied, key=lambda a: a["n_sectors"])
    return {"n_sectors": winner["n_sectors"], "noise_positives": winner["cuts"][NOISE_CUT][0],
            "rejected": [{"n_sectors": r["n_sectors"], "reason": r["reason"]} for r in rejected]}


def select_blob_params(grid: dict, t_list: Sequence[int]) -> dict:
    """Pick (threshold, min_vol, min_frames) from ``grid[(T, mv, mf)] = (pos, neg)``.

    ``N_real`` = median positive count over the *strict corner* (the two highest
    thresholds x the (4,3) and (8,3) cuts); a plateau exists if that corner's
    max/min is <= ``PLATEAU_RATIO``. Suggestion = the loosest setting (lowest T,
    then lowest min_vol) whose positive count is within ``REAL_BAND`` x N_real and
    whose neg/pos <= ``NEG_OVER_POS_MAX``. No plateau -> low confidence and the
    app defaults are returned instead of a guess.
    """
    t_sorted = sorted(t_list)
    corner_T = t_sorted[-2:] if len(t_sorted) >= 2 else t_sorted
    corner = [grid[(T, mv, MIN_FRAMES)][0] for T in corner_T for mv in (4, 8)]
    lo, hi = min(corner), max(corner)
    n_real = float(np.median(corner))
    plateau = lo > 0 and hi / lo <= PLATEAU_RATIO
    out = {"n_real": n_real, "plateau": bool(plateau), "corner_counts": corner,
           "confidence": "high" if plateau else "low"}
    if not plateau:
        out["suggestion"] = {"blob_thresh": BLOB_THRESHOLD_DEFAULT,
                             "blob_minvol": BLOB_MIN_VOL_DEFAULT,
                             "blob_minframes": BLOB_MIN_FRAMES_DEFAULT}
        out["reason"] = ("no plateau: positive counts at the strict corner span "
                         f"{lo}..{hi} (max/min > {PLATEAU_RATIO}); returning app defaults")
        return out
    ok = []
    for T in t_sorted:
        for mv in (2, 4, 8):
            pos, neg = grid[(T, mv, MIN_FRAMES)]
            if pos <= 0:
                continue
            if REAL_BAND[0] * n_real <= pos <= REAL_BAND[1] * n_real and neg / pos <= NEG_OVER_POS_MAX:
                ok.append((T, mv, pos, neg))
    if not ok:
        out["confidence"] = "low"
        out["suggestion"] = {"blob_thresh": BLOB_THRESHOLD_DEFAULT,
                             "blob_minvol": BLOB_MIN_VOL_DEFAULT,
                             "blob_minframes": BLOB_MIN_FRAMES_DEFAULT}
        out["reason"] = "plateau found but no setting meets the real-count band and neg/pos limit"
        return out
    T, mv, pos, neg = ok[0]                # t_sorted then mv ascending == loosest first
    out["suggestion"] = {"blob_thresh": float(T), "blob_minvol": int(mv),
                         "blob_minframes": MIN_FRAMES}
    out["expected_spots"] = int(pos)
    out["expected_negatives"] = int(neg)
    return out


# ═════════════════════════════════════════════════════════════════════════════
#  Workers (module-level so a spawn pool can import them)
# ═════════════════════════════════════════════════════════════════════════════

def _mm(path, shape, mode):
    return np.memmap(path, dtype=np.float32, mode=mode, shape=shape)


def _sub_chunk(frames_path, out_path, shape, lo, hi, tth, az, mask, n_sectors):
    from midas_defect.ingest import subtract_background
    fr = _mm(frames_path, shape, "r")
    out = _mm(out_path, shape, "r+")
    out[lo:hi] = subtract_background(np.asarray(fr[lo:hi]), tth, az, mask, n_sectors=n_sectors)
    out.flush()
    return hi - lo


def _blob_task(stack_path, shape, mask, sign, threshold, cuts):
    """Blob counts for one (sign, threshold): {cut: count}. gap_bridge=0 on purpose:
    bridging is ~25x slower and only matters for blobs straddling a masked gap."""
    from midas_defect.ingest import find_blobs_3d
    s = _mm(stack_path, shape, "r")
    arr = np.asarray(s) if sign > 0 else -np.asarray(s)
    df = find_blobs_3d(arr, mask, threshold=float(threshold), min_vol=2, gap_bridge=0)
    return {cut: int(((df.volume_vox >= cut[0]) & (df.n_frames >= cut[1])).sum()) for cut in cuts}


def _pick_workers(requested: Optional[int], stack_bytes: int, n_neg_concurrent: int = 1) -> int:
    n = requested if requested else min(6, os.cpu_count() or 1)
    try:
        import psutil
        avail = psutil.virtual_memory().available
    except Exception:
        return max(1, int(n))
    # each worker handling a negated stack holds ~1 extra stack copy
    cap = int(max(1, (avail * 0.6 - 2 * stack_bytes) // max(stack_bytes, 1)))
    return max(1, min(int(n), cap))


# ═════════════════════════════════════════════════════════════════════════════
#  Main entry
# ═════════════════════════════════════════════════════════════════════════════

def _subtract(pool, workers, frames_path, out_path, shape, tth, az, mask, n_sectors):
    n = shape[0]
    k = max(1, min(workers * 2, n))
    bounds = np.linspace(0, n, k + 1).astype(int)
    futs = [pool.submit(_sub_chunk, frames_path, out_path, shape, int(bounds[i]), int(bounds[i + 1]),
                        tth, az, mask, n_sectors) for i in range(k) if bounds[i + 1] > bounds[i]]
    for f in futs:
        f.result()


def suggest_ingest_params(frames_raw: np.ndarray, geometry: dict, *,
                          sentinel: int = SENTINEL,
                          user_mask: Optional[np.ndarray] = None,
                          corrections: Optional[dict] = None,
                          live_frac_of_median: float = LIVE_FRAMES_FRAC_DEFAULT,
                          sectors: Sequence[int] = SECTORS_DEFAULT,
                          workers: Optional[int] = None,
                          scratch_dir: Optional[str] = None,
                          on_progress: Callable[[str, dict], None] = _noop,
                          cancel: Optional[Callable[[], bool]] = None) -> dict:
    """Run the tuning procedure on ``frames_raw`` (n, rows, cols), the frames
    already restricted to the omega window. ``corrections`` is the pipeline's
    ``{dark, bright, bright_mode, background}`` (applied after sentinel zeroing, as
    ingest does); dead frames are dropped as ingest does. ``geometry`` is the pipeline's
    ``geometry`` cfg (Lsd, BC_y, BC_z, px_um, wavelength_A, nrpixels_y/z,
    omega_ref_deg, omega_step_deg, ty, tz, im_trans).

    Returns a JSON-serialisable dict: ``suggestion`` (GUI field names),
    ``confidence``, ``warnings``, ``evidence``, ``timings``.
    """
    from midas_defect.geometry import Geometry, detector_angle_maps
    from midas_defect.ingest import build_mask

    t_all = time.time()
    timings: dict = {}
    warnings: list = []
    raw = np.asarray(frames_raw)
    n, nz, ny = raw.shape
    shape = (n, nz, ny)
    stack_bytes = n * nz * ny * 4
    w = _pick_workers(workers, stack_bytes)
    cancelled = lambda: bool(cancel and cancel())          # noqa: E731

    scratch = tempfile.mkdtemp(prefix="solvecell_tuning_", dir=scratch_dir)
    try:
        # ── step 1: characterise ────────────────────────────────────────────
        t0 = time.time()
        frames_path = os.path.join(scratch, "frames.f32")
        fr = _mm(frames_path, shape, "w+")
        sent_all = np.ones((nz, ny), bool)
        for lo in range(0, n, 32):                          # bounded temp memory per chunk
            blk = raw[lo:lo + 32]
            s = blk == sentinel
            sent_all &= s.all(axis=0)
            chunk = np.where(s, 0, blk).astype(np.float32)
            if corrections:
                chunk = _apply_stack_corrections(chunk, corrections)
            fr[lo:lo + 32] = chunk
        fr.flush()
        from midas_defect.ingest import live_frames
        live, _ = live_frames(np.asarray(fr), frac_of_median=live_frac_of_median)
        n_dead = int((~live).sum())
        if n_dead:                                          # same dead-frame drop as ingest
            keep = np.flatnonzero(live)
            kept = np.array(fr[keep])
            del fr
            n = len(keep)
            shape = (n, nz, ny)
            fr = _mm(frames_path, shape, "r+")
            fr[:] = kept
            fr.flush()
            del kept
            warnings.append(f"{n_dead} dead frame(s) dropped (as ingest does)")
        step = max(1, n // 64)
        med = np.median(np.asarray(fr[::step]), axis=0)
        pct = {str(p): float(np.percentile(med, p)) for p in (1, 50, 99, 100)}
        regime = "photon_counting" if pct["50"] < PHOTON_COUNTING_MEDIAN else "high_counts"
        timings["load_characterise"] = time.time() - t0
        on_progress("characterise", {"regime": regime, "median_pct": pct})

        # ── step 2: mask ────────────────────────────────────────────────────
        t0 = time.time()
        mres = build_mask(np.asarray(fr), low_count_threshold=0.0, grow=2)
        mask = np.asarray(mres.mask) | sent_all
        if user_mask is not None:
            mask = mask | np.asarray(user_mask).astype(bool)
        masked_frac = float(mask.mean())
        if masked_frac > MASKED_FRAC_WARN:
            warnings.append(f"{100 * masked_frac:.1f}% of pixels masked at low_count_threshold=0 "
                            "(sentinel/gap/user mask) -- check the mask sources")
        timings["mask"] = time.time() - t0

        # ── geometry -> angle maps in the raw frame ─────────────────────────
        codes = _codes(geometry)
        geom = Geometry(lsd_um=geometry["Lsd"], bcy_px=geometry["BC_y"], bcz_px=geometry["BC_z"],
                        px_um=geometry["px_um"], wavelength_A=geometry["wavelength_A"],
                        n_pix_y=geometry["nrpixels_y"], n_pix_z=geometry["nrpixels_z"],
                        omega_first_deg=geometry.get("omega_ref_deg", 0.0),
                        omega_step_deg=geometry.get("omega_step_deg", 0.0),
                        n_frames=n, tx_deg=0.0, ty_deg=geometry.get("ty", 0.0),
                        tz_deg=geometry.get("tz", 0.0))
        tth, az = detector_angle_maps(geom)
        tth, az = _calib_map_to_raw(tth, codes), _calib_map_to_raw(az, codes)
        if abs(geometry.get("omega_step_deg", 0.0)) >= OMEGA_STEP_WARN_DEG:
            warnings.append(f"omega step {geometry['omega_step_deg']} deg is coarse; "
                            f"min_frames={MIN_FRAMES} may reject real reflections")

        sub_path = os.path.join(scratch, "sub.f32")
        _mm(sub_path, shape, "w+").flush()

        # 'spawn', never 'fork': this runs under a Qt GUI process (multi-threaded; forking it
        # is unsafe) and the children must import only numpy/scipy/midas_defect.
        with _children_without_main(), \
                ProcessPoolExecutor(max_workers=w, mp_context=mp.get_context("spawn")) as pool:
            # ── steps 3-4: per-candidate stack + noise-dominated blob counts ─
            sigma = None
            rows = []
            t_lo = None
            for ns in sectors:
                if cancelled():
                    return {"cancelled": True}
                t0 = time.time()
                _subtract(pool, w, frames_path, sub_path, shape, tth, az, mask, int(ns))
                t_sub = time.time() - t0
                if sigma is None:
                    sub = _mm(sub_path, shape, "r")
                    sample = np.asarray(sub[::8])[:, ~mask]
                    sigma = float(1.4826 * np.median(np.abs(sample)))
                    if not sigma > 0:
                        sigma = float(np.std(sample)) or 1.0
                    t_list = sorted({max(1, int(round(k * sigma))) for k in T_SIGMA_MULTIPLIERS})
                    t_lo = t_list[0]
                    on_progress("sigma", {"sigma": sigma, "thresholds": t_list})
                t0 = time.time()
                fp = pool.submit(_blob_task, sub_path, shape, mask, +1, t_lo, CUTS)
                fn = pool.submit(_blob_task, sub_path, shape, mask, -1, t_lo, CUTS)
                pos, neg = fp.result(), fn.result()
                rows.append({"n_sectors": int(ns), "t_subtract_s": t_sub,
                             "t_blobs_s": time.time() - t0,
                             "cuts": {c: (pos[c], neg[c]) for c in CUTS}})
                on_progress("sector", {"n_sectors": int(ns), "cuts": rows[-1]["cuts"]})
            timings["sectors"] = sum(r["t_subtract_s"] + r["t_blobs_s"] for r in rows)
            choice = select_sectors(rows)

            # ── step 5: fine threshold grid on the winning stack ────────────
            t0 = time.time()
            if cancelled():
                return {"cancelled": True}
            _subtract(pool, w, frames_path, sub_path, shape, tth, az, mask, choice["n_sectors"])
            futs = {(sg, T): pool.submit(_blob_task, sub_path, shape, mask, sg, T, CUTS)
                    for T in t_list for sg in (+1, -1)}
            res = {k: f.result() for k, f in futs.items()}
            grid = {(T, mv, mf): (res[(+1, T)][(mv, mf)], res[(-1, T)][(mv, mf)])
                    for T in t_list for (mv, mf) in CUTS}
            timings["grid"] = time.time() - t0
            blob = select_blob_params(grid, t_list)

        # ── step 6: diffuse structure that survives subtraction ─────────────
        t0 = time.time()
        tmean = np.asarray(fr).mean(axis=0)
        star = tmean > 2.0 * max(pct["50"], 1.0)
        star &= ~mask
        diffuse = {"n_px": int(star.sum())}
        if star.any():
            sub = _mm(sub_path, shape, "r")
            sidx = np.asarray(sub[::4])[:, star]
            diffuse["mean_residual"] = float(sidx.mean())
            diffuse["p99_9_residual"] = float(np.percentile(sidx, 99.9))
            diffuse["far_field_mean"] = float(np.asarray(sub[::4])[:, ~star & ~mask].mean())
            if diffuse["mean_residual"] > 5.0 * sigma:
                warnings.append(f"{diffuse['n_px']} px keep a large positive residual after "
                                f"background subtraction (mean {diffuse['mean_residual']:.0f} counts); "
                                "spots there may be diffuse signal -- consider a user mask or "
                                "dropping spots inside that region")
        timings["diffuse_check"] = time.time() - t0

        if not blob["plateau"]:
            warnings.append(blob.get("reason", "low confidence"))
        timings["total"] = time.time() - t_all
        timings["workers"] = w
        sugg = {"low_count_threshold": 0.0, "mask_grow": 2,
                "sector_candidates": str(choice["n_sectors"]), **blob["suggestion"]}
        return {
            "suggestion": sugg, "confidence": blob["confidence"], "warnings": warnings,
            "evidence": {
                "regime": regime, "median_percentiles": pct, "sigma": sigma,
                "masked_fraction": masked_frac, "thresholds": t_list,
                "sectors": [{"n_sectors": r["n_sectors"],
                             "cuts": {f"v{c[0]}f{c[1]}": list(v) for c, v in r["cuts"].items()}}
                            for r in rows],
                "sector_choice": choice,
                "grid": {f"T{T}_v{mv}f{mf}": list(v) for (T, mv, mf), v in grid.items()},
                "blob": {k: v for k, v in blob.items() if k != "suggestion"},
                "diffuse": diffuse,
            },
            "timings": timings,
        }
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def format_report(res: dict) -> str:
    """Plain-text evidence summary of a ``suggest_ingest_params`` result (shared by
    the CLI and the Solve Cell dialog)."""
    ev, sg = res["evidence"], res["suggestion"]
    L = [f"Regime: {ev['regime']}    noise sigma (MAD): {ev['sigma']:.2f}    "
         f"masked: {100 * ev['masked_fraction']:.3f}%    confidence: {res['confidence']}", ""]
    T_lo = ev["thresholds"][0]
    L.append(f"Background model (positive/negative blob counts at T={T_lo}; vNfM = min_vol N, min_frames M):")
    L.append(f"  {'sectors':>7}  {'v2f1':>13} {'v2f3':>11} {'v4f3':>11} {'v8f3':>11}")
    for r in ev["sectors"]:
        c = r["cuts"]
        L.append(f"  {r['n_sectors']:>7}  " + " ".join(
            f"{c[k][0]:>6}/{c[k][1]:<5}" for k in ("v2f1", "v2f3", "v4f3", "v8f3")))
    ch = ev["sector_choice"]
    L.append(f"  -> {ch['n_sectors']} sectors (fewest noise positives: {ch['noise_positives']})")
    for rj in ch["rejected"]:
        L.append(f"     rejected {rj['n_sectors']}: {rj['reason']}")
    L += ["", "Blob cuts on the chosen background (positive/negative, min_frames=3):",
          f"  {'T':>4}  {'min_vol 2':>12} {'min_vol 4':>12} {'min_vol 8':>12}"]
    for T in ev["thresholds"]:
        g = ev["grid"]
        L.append(f"  {T:>4}  " + " ".join(
            f"{g[f'T{T}_v{mv}f3'][0]:>6}/{g[f'T{T}_v{mv}f3'][1]:<5}" for mv in (2, 4, 8)))
    b = ev["blob"]
    L.append(f"  plateau: {b['plateau']}   N_real ~ {b['n_real']:.0f}   "
             f"corner counts {b['corner_counts']}" + (f"   ({b['reason']})" if b.get("reason") else ""))
    if "expected_spots" in b:
        L.append(f"  chosen setting gives ~{b['expected_spots']} spots, ~{b['expected_negatives']} negatives")
    d = ev["diffuse"]
    if d.get("n_px"):
        L.append(f"\nDiffuse region: {d['n_px']} px, mean residual {d.get('mean_residual', 0):.1f} "
                 f"(far field {d.get('far_field_mean', 0):.2f})")
    for w in res["warnings"]:
        L.append(f"WARNING: {w}")
    t = res["timings"]
    L.append(f"\nTook {t['total']:.0f} s on {t['workers']} worker(s). Sector search and thresholds ran "
             "without gap bridging.")
    return "\n".join(L)


# ═════════════════════════════════════════════════════════════════════════════
#  CLI
# ═════════════════════════════════════════════════════════════════════════════

def _main(argv=None) -> int:
    import argparse
    import json
    import h5py
    ap = argparse.ArgumentParser(
        prog="python -m midas_gui.solve_cell.tuning",
        description="Suggest solve-cell ingest parameters for a raw frame stack (advisory).")
    ap.add_argument("--data", required=True, help="raw frames HDF5")
    ap.add_argument("--dataset", default="entry/data/data")
    ap.add_argument("--project", required=True,
                    help="any saved midas-gui project .h5 for this panel (source of geometry+omega)")
    ap.add_argument("--panel", default="1")
    ap.add_argument("--first", type=int, default=None, help="first frame idx (default: from project)")
    ap.add_argument("--last", type=int, default=None, help="last frame idx, inclusive")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--sectors", default=",".join(map(str, SECTORS_DEFAULT)))
    ap.add_argument("--out", default=None, help="write the result JSON here")
    a = ap.parse_args(argv)

    with h5py.File(a.project, "r") as pf:
        meta = json.loads(pf["analysis/solve_cell/ingest/metadata"][()])
    geometry = dict(meta["panels"][a.panel]["geometry_cfg"])
    first = a.first if a.first is not None else int(geometry.get("omega_ref_frame_idx", 0))
    last = a.last if a.last is not None else int(geometry.get("omega_last_frame_idx", -1))
    with h5py.File(a.data, "r") as df:
        d = df[a.dataset]
        last = d.shape[0] - 1 if last < 0 else min(last, d.shape[0] - 1)
        raw = d[first:last + 1]
    print(f"[tuning] {raw.shape} frames [{first}..{last}], workers={a.workers or 'auto'}", flush=True)
    res = suggest_ingest_params(
        raw, geometry, sectors=tuple(int(s) for s in a.sectors.split(",")), workers=a.workers,
        on_progress=lambda k, v: print(f"[tuning] {k}: "
                                       f"{ {kk: vv for kk, vv in v.items() if kk != 'cuts'} }", flush=True))
    txt = json.dumps(res, indent=1, default=str)
    if a.out:
        with open(a.out, "w") as f:
            f.write(txt)
    print(format_report(res))
    print(json.dumps({"suggestion": res["suggestion"], "confidence": res["confidence"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
