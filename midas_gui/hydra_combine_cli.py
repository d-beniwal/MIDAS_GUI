"""Headless four-panel zarr combine — ``python -m midas_gui.hydra_combine_cli``.

The combine half of a background Hydra Batch Integrate job. A Hydra job runs
one ``batch_cli`` invocation per panel and then this, all chained into the one
``screen`` session (see ``hydra_batch_page.HydraBatchPage._run_as_job``), so
the merge happens on the compute side rather than waiting for a GUI to be
open when the panels finish.

In-process, the same work is done by
``HydraBatchPage._combine_panel_zarrs_if_wanted`` calling
``hydra_zarr_combine`` directly; this module is a thin CLI around the same two
functions so both paths produce byte-identical archives.

Prints structured lines to stdout, unbuffered, in the shape JobQueuePanel
already parses:
  ``[combine] PROGRESS <done>/<total>``  — one per merged frame
  ``[combine] ...``                      — human-readable notes

Exit code is 0 when every set merged, 1 when any failed (a bad frame does not
stop the rest — the same policy the in-process path has).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import midas_gui._paths  # noqa: F401  (KMP_DUPLICATE_LIB_OK / HDF5_USE_FILE_LOCKING —
# same reasoning as batch_cli: this entry point never goes through app.py's
# import chain, and it reads zarr archives off the same NFS beamline share.)


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m midas_gui.hydra_combine_cli",
        description="Merge each frame's per-panel zarr archives into one "
                    "eta-concatenated archive.")
    p.add_argument("--out-dir", required=True,
                   help="Scan-level output directory — the one holding the "
                        "ge1/ ge2/ ... subfolders. Merged archives are "
                        "written here, beside them.")
    p.add_argument("--panels", nargs="+", default=["ge1", "ge2", "ge3", "ge4"],
                   help="Panel subfolder names, in eta order (default: "
                        "ge1 ge2 ge3 ge4). A frame is merged only when every "
                        "one of these wrote an archive for it. Space- or "
                        "comma-separated; the MPE SAXS/WAXS workflow's "
                        "combine_hydra_zarr.py spells this list with commas, "
                        "and typing it that way here should not silently "
                        "look for a panel called 'ge1,ge2,ge3,ge4'.")
    return p


def _panels(raw) -> list:
    """Flatten ``--panels`` however it was spelled, dropping empties so a
    trailing comma is not a panel named ''."""
    out = []
    for item in raw:
        out.extend(p.strip() for p in str(item).split(",") if p.strip())
    return out


def main(argv=None) -> int:
    args = _build_arg_parser().parse_args(argv)
    from midas_gui import hydra_zarr_combine

    panels = _panels(args.panels)
    if not panels:
        print("[combine] ERROR: --panels is empty", flush=True)
        return 1

    out_dir = args.out_dir
    if not Path(out_dir).is_dir():
        print(f"[combine] ERROR: not a directory: {out_dir}", flush=True)
        return 1

    try:
        sets = hydra_zarr_combine.find_panel_zarr_sets(out_dir, panels)
    except Exception as exc:
        print(f"[combine] ERROR: could not scan for panel zarrs: {exc}", flush=True)
        return 1

    if not sets:
        # Not an error: an aborted or partial scan legitimately has no frame
        # with all four panels, and the per-panel output is still good. The
        # in-process path says exactly this and carries on.
        print("[combine] no frame had an archive from every panel — "
              "nothing to combine.", flush=True)
        return 0

    total = len(sets)
    print(f"[combine] merging {total} frame(s) across "
          f"{len(panels)} panels…", flush=True)
    done = 0
    failed = 0
    # Collected rather than printed per frame: the caking is one setting for
    # the whole run, so a non-tiling set would otherwise repeat the same
    # remark once per frame and bury the rest of the log.
    warned: set = set()
    for group in sets:
        try:
            out = hydra_zarr_combine.combine_panel_zarrs(
                group,
                hydra_zarr_combine.default_combined_path(group, out_dir),
                warn=lambda m: warned.add(m))
        except Exception as exc:
            failed += 1
            name = Path(group[sorted(group)[0]]).name
            print(f"[combine] ERROR: {name}: {exc}", flush=True)
            continue
        done += 1
        print(f"[combine] PROGRESS {done}/{total}", flush=True)
        if done == 1 or done == total:
            print(f"[combine]   → {out}", flush=True)

    print(f"[combine] wrote {done}/{total} combined archive(s).", flush=True)
    for msg in sorted(warned):
        # A note, not a warning — see the in-process counterpart: eta travels
        # per column in REtaMap, so an uncovered wedge is absent rather than
        # misindexed, and I vs 2theta/q/d is unaffected for every eta the
        # file does have.
        print(f"[combine] note: {msg}. Merged as usual; cake_params records "
              f"the per-panel eta ranges rather than one span.", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
