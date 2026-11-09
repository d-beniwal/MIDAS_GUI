"""Headless single-peak fitting — ``python -m midas_gui.peak_fit_cli``.

Fits one pseudo-Voigt plus a linear background to every (frame, eta)
lineout of a combined Hydra scan and writes a CSV in the GSAS-II
sequential peak-fit format, so the result loads straight into the Data
Explorer and Texture tabs beside a GSAS-II fit of the same scan.

The point is comparability. ``midas_gui.peak_fit_midas`` explains the
profile convention and -- more importantly -- which columns may be
compared across the two codes and which may not.

Work is batched BY ETA, not by file: every archive in a scan shares one
REtaMap, so a given eta column has a single 2theta grid, and all
``n_files * n_frames`` lineouts at that eta solve in one LM call. That is
what makes 29,120 fits a few minutes rather than an afternoon. It also
means peak memory scales with one eta, not the scan.

Prints structured lines to stdout, unbuffered, in the shape
``JobQueuePanel`` already parses:
  ``[peakfit] PROGRESS <done>/<total>``  -- one per eta column
  ``[peakfit] ...``                      -- human-readable notes

Exit code 0 when every eta column fitted, 1 if any failed (one bad column
does not stop the rest -- the same policy the combine CLI has).
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import re
import sys
import time

import midas_gui._paths  # noqa: F401  (KMP_DUPLICATE_LIB_OK / HDF5_USE_FILE_LOCKING —
# same reasoning as hydra_combine_cli: this entry point never goes through
# app.py's import chain, and it reads zarr archives off an NFS share.)

import numpy as np

from midas_gui.peak_fit_midas import (fit_lineouts, row_name,
                                      write_peak_fit_csv)

#: Trailing file number in an archive name, e.g. ``..._005180.ge1234...``.
_NUM_RX = re.compile(r"_(\d{4,7})\.")


def _archives(scan_dir, pattern):
    """Scan archives, ordered by file number.

    Dark-field archives are excluded by name: they are not scan points,
    and a dark in the middle of the list would silently shift every
    subsequent ``img`` index relative to a GSAS-II fit of the same scan.
    """
    out = []
    for n in sorted(os.listdir(scan_dir)):
        if not fnmatch.fnmatch(n, pattern) or "dark" in n.lower():
            continue
        m = _NUM_RX.search(n)
        out.append((int(m.group(1)) if m else -1, os.path.join(scan_dir, n)))
    out.sort(key=lambda t: (t[0], t[1]))
    return out


def _open(path):
    import zarr
    store = zarr.ZipStore(path, mode="r") if path.endswith(".zip") \
        else zarr.DirectoryStore(path)
    return store, zarr.group(store)


def _frame_keys(g):
    return sorted(g["OmegaSumFrame"].keys(),
                  key=lambda k: int(k.rsplit("_", 1)[1]))


def build_parser():
    p = argparse.ArgumentParser(
        prog="python -m midas_gui.peak_fit_cli",
        description="Fit one peak per (frame, eta) lineout of a combined "
                    "Hydra scan; write a GSAS-II-format peak-fit CSV.")
    p.add_argument("--scan-dir", required=True,
                   help="directory of combined archives (one per file)")
    p.add_argument("--out", required=True, help="output CSV path")
    p.add_argument("--pattern", default="*.ge1234.ave.zarr.zip",
                   help="archive glob within --scan-dir")
    p.add_argument("--center", type=float, required=True,
                   help="peak centre in 2theta degrees")
    p.add_argument("--halfwidth", type=float, default=0.065,
                   help="half-width of the fit window, degrees (default "
                        "0.065; must exclude neighbouring reflections)")
    p.add_argument("--eta", type=float, nargs="*", default=None,
                   help="fit only these eta values (default: all columns)")
    p.add_argument("--max-iter", type=int, default=200)
    p.add_argument("--device", default="cpu", help="torch device")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    t0 = time.time()

    files = _archives(args.scan_dir, args.pattern)
    if not files:
        print(f"[peakfit] no archives matching {args.pattern!r} in "
              f"{args.scan_dir}", flush=True)
        return 1
    print(f"[peakfit] {len(files)} archives in {args.scan_dir}", flush=True)

    # Geometry and frame layout come from the first archive; the rest are
    # checked against it rather than trusted, because a scan assembled
    # from two calibrations would otherwise produce a CSV whose 2theta
    # column means two different things in different rows.
    store, g = _open(files[0][1])
    try:
        rmap = np.asarray(g["REtaMap"])
        eta_axis = rmap[2].mean(axis=0)
        tth_map = rmap[1]
        n0 = len(_frame_keys(g))
    finally:
        store.close()

    cols = list(range(eta_axis.size))
    if args.eta:
        cols = [int(np.argmin(np.abs(eta_axis - e))) for e in args.eta]
    lo_w, hi_w = args.center - args.halfwidth, args.center + args.halfwidth
    print(f"[peakfit] {eta_axis.size} eta columns ({len(cols)} selected), "
          f"{n0} frames/file, window {lo_w:.4f}-{hi_w:.4f} deg", flush=True)

    # Read only the R bins any selected eta column needs. The window is
    # ~55 of 3520 bins, so reading the lot would hold ~800 MB of a scan
    # this size in memory for no benefit -- and a 2theta map that shifts
    # with eta means the union, not one column's slice.
    need = np.zeros(tth_map.shape[0], dtype=bool)
    for j in cols:
        c = tth_map[:, j]
        need |= (c >= lo_w) & (c <= hi_w)
    rsl = np.flatnonzero(need)
    if rsl.size == 0:
        print(f"[peakfit] no R bin falls in {lo_w:.4f}-{hi_w:.4f} deg; the "
              f"archive covers {tth_map.min():.4f}-{tth_map.max():.4f}",
              flush=True)
        return 1
    r0, r1 = int(rsl[0]), int(rsl[-1]) + 1
    print(f"[peakfit] reading R bins {r0}:{r1} of {tth_map.shape[0]}",
          flush=True)

    # Read every lineout once, into [n_file][frame, R_window, eta_selected].
    per_file, omegas, counts = [], [], []
    for num, path in files:
        store, g = _open(path)
        try:
            rm = np.asarray(g["REtaMap"])
            if rm.shape != rmap.shape or not np.allclose(
                    rm[2].mean(axis=0), eta_axis, atol=1e-6):
                print(f"[peakfit] SKIP {os.path.basename(path)}: REtaMap "
                      f"differs from the first archive", flush=True)
                continue
            keys = _frame_keys(g)
            blk = np.stack([np.asarray(g["OmegaSumFrame"][k])[r0:r1][:, cols]
                            for k in keys]).astype(np.float64)
            om = (np.asarray(g["Omegas"]).astype(float)
                  if "Omegas" in g else np.full(len(keys), 999.0))
        finally:
            store.close()
        per_file.append((num, os.path.basename(path), blk))
        omegas.append(om)
        counts.append(blk.shape[0])
    if not per_file:
        print("[peakfit] every archive was skipped", flush=True)
        return 1
    total_pts = int(sum(counts))
    print(f"[peakfit] {total_pts} scan points x {len(cols)} eta = "
          f"{total_pts*len(cols)} fits", flush=True)

    # results[eta_slot] -> FitResult over the concatenated scan points
    results, failed = {}, []
    for slot, j in enumerate(cols):
        x = tth_map[r0:r1, j].astype(np.float64)
        w = (x >= lo_w) & (x <= hi_w)
        if w.sum() < 12:
            print(f"[peakfit] SKIP eta {eta_axis[j]:+.1f}: only {int(w.sum())} "
                  f"points in the window", flush=True)
            failed.append(j)
            print(f"[peakfit] PROGRESS {slot+1}/{len(cols)}", flush=True)
            continue
        Y = np.concatenate([blk[:, w, slot] for _n, _b, blk in per_file], axis=0)
        try:
            results[j] = fit_lineouts(x[w], Y, max_iter=args.max_iter,
                                      device=args.device)
        except Exception as exc:                      # pragma: no cover
            print(f"[peakfit] FAIL eta {eta_axis[j]:+.1f}: {exc}", flush=True)
            failed.append(j)
        print(f"[peakfit] PROGRESS {slot+1}/{len(cols)}", flush=True)

    # Row order matches GSAS-II's: file, then frame, then eta.
    rows, base = [], 0
    for fi, (num, name, blk) in enumerate(per_file):
        for img in range(blk.shape[0]):
            k = base + img
            for j in cols:
                r = results.get(j)
                if r is None:
                    continue
                rows.append(dict(
                    name=row_name(name, img, omegas[fi][img], eta_axis[j]),
                    use=bool(r.rc[k] == 0),
                    rwp=float(r.rwp[k]), gof=float(r.gof[k]),
                    params=r.params[k], esds=r.esds[k]))
        base += blk.shape[0]

    rc_all = np.concatenate([r.rc for r in results.values()]) if results \
        else np.zeros(0, int)
    meta = {
        "producer": "midas_gui.peak_fit_cli",
        "solver": "midas_peakfit.lm_generic.lm_solve_generic",
        "profile": "pseudo-Voigt, Thompson-Cox-Hastings mixing (GSAS PSVOIGT)",
        "units": {"pos": "2theta degrees",
                  "sig": "Gaussian variance, centidegrees^2",
                  "gam": "Lorentzian FWHM, centidegrees",
                  "int": "integrated area, counts x centidegrees"},
        "weights": "Poisson, w = 1 / max(y, 1)",
        "comparable_across_codes": ["pos0", "int0", "sig0", "gam0"],
        "not_comparable_across_codes": ["Rwp", "GOF", "esd-*"],
        "fit_window_deg": [lo_w, hi_w],
        "r_bin_slice": [r0, r1],
        "center_deg": args.center,
        "scan_dir": os.path.abspath(args.scan_dir),
        "archives": [n for _num, n, _b in per_file],
        "n_rows": len(rows),
        "converged": int((rc_all == 0).sum()),
        "max_iter_hit": int((rc_all == 1).sum()),
        "lambda_saturated": int((rc_all == 2).sum()),
        "seconds": round(time.time() - t0, 1),
    }
    write_peak_fit_csv(args.out, rows, meta)
    print(f"[peakfit] wrote {args.out}  ({len(rows)} rows, "
          f"{meta['converged']} converged, {meta['max_iter_hit']} hit max-iter, "
          f"{meta['lambda_saturated']} lambda-saturated)", flush=True)
    print(f"[peakfit] sidecar {os.path.splitext(args.out)[0]}.meta.json",
          flush=True)
    print(f"[peakfit] done in {meta['seconds']}s", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":                            # pragma: no cover
    sys.exit(main())
