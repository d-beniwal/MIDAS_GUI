"""Headless Batch Integrate runner — ``python -m midas_gui.batch_cli``.

Runs exactly one Batch Integrate job with no GUI, so it can be supervised by
an external ``screen`` session (see ``job_queue.JobQueuePanel``) and outlive
the GUI process that launched it. Builds the same ``IntegrationSpec`` /
``BatchWorker`` the Batch Integrate tab uses, calling ``BatchWorker.run()``
synchronously (no Qt event loop needed — a ``QApplication`` instance is only
required so PyQt will let us construct the QThread-derived worker object).

Prints two kinds of structured lines to stdout, unbuffered:
  ``[batch] PROGRESS <done>/<total>``   — one per frame, parsed by JobQueuePanel
  ``[launcher] DONE exit=<code>``       — printed by the launching shell
                                          command after this process exits,
                                          not by this module itself.
"""
from __future__ import annotations

import argparse
import sys


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m midas_gui.batch_cli",
        description="Run one Batch Integrate job with no GUI.")
    p.add_argument("--calib-file", required=True,
                   help="Calibration source: paramstest.txt / calibration.json / .poni")
    p.add_argument("--r-bin", type=float, default=1.0, help="R bin size (px)")
    p.add_argument("--eta-bin", type=float, default=5.0, help="eta bin size (deg)")
    p.add_argument("--r-min", type=float, default=None, help="Rmin (px); default backend auto")
    p.add_argument("--r-max", type=float, default=None, help="Rmax (px); default backend auto")

    p.add_argument("--source-type", required=True, choices=["tiff_glob", "hdf5", "tiff_list"])
    p.add_argument("--source-path", help="Folder/glob (tiff_glob) or file path (hdf5)")
    p.add_argument("--source-paths", nargs="+", help="Explicit file list (tiff_list)")
    p.add_argument("--dataset", default="frames", help="HDF5 dataset path (hdf5 source only)")

    p.add_argument("--out-dir", required=True)
    p.add_argument("--fmts", default="csv",
                   help="Comma-separated output format keys, e.g. csv,h5,zarr "
                        "(see constants.OUTPUT_FORMATS values)")
    p.add_argument("--kernel", default="subpixel2",
                   choices=["hard", "subpixel2", "subpixel4", "polygon"])

    p.add_argument("--frame-start", type=int, default=0)
    p.add_argument("--frame-end", type=int, default=None)
    p.add_argument("--frame-stride", type=int, default=1)

    p.add_argument("--multi-azimuth", action="store_true")
    p.add_argument("--weighted", dest="weighted", action="store_true", default=True)
    p.add_argument("--no-weighted", dest="weighted", action="store_false")

    p.add_argument("--polarization", action="store_true")
    p.add_argument("--pol-fraction", type=float, default=0.99)
    p.add_argument("--pol-plane", type=float, default=0.0)
    p.add_argument("--solid-angle", action="store_true")

    p.add_argument("--variance", action="store_true", help="Compute per-bin sigma")
    p.add_argument("--error-model", default="poisson",
                   choices=["poisson", "azimuthal", "hybrid"])

    p.add_argument("--mask", default=None, help="Mask image (tif/h5); nonzero = masked")
    p.add_argument("--dark", default=None, help="Dark field image (tif/h5)")
    p.add_argument("--bright", default=None, help="Bright field image (tif/h5)")
    p.add_argument("--background", default=None, help="Background image (tif/h5)")
    p.add_argument("--bright-mode", default="divide", choices=["divide", "subtract"])

    p.add_argument("--monitor-file", default=None,
                   help="Text file, one monitor value per line")
    return p


def _source_cfg(args) -> dict:
    if args.source_type == "tiff_glob":
        if not args.source_path:
            raise SystemExit("--source-path is required for --source-type tiff_glob")
        return {"type": "tiff_glob", "path": args.source_path}
    if args.source_type == "hdf5":
        if not args.source_path:
            raise SystemExit("--source-path is required for --source-type hdf5")
        return {"type": "hdf5", "path": args.source_path, "dataset": args.dataset}
    if args.source_type == "tiff_list":
        if not args.source_paths:
            raise SystemExit("--source-paths is required for --source-type tiff_list")
        return {"type": "tiff_list", "paths": list(args.source_paths)}
    raise SystemExit(f"Unknown --source-type: {args.source_type}")


def _load_field(path):
    if not path:
        return None
    from midas_gui.helpers import _load_image
    return _load_image(path)


def _build_corrections(args):
    pol = sa = None
    if args.polarization:
        from midas_integrate_v2 import PolarizationCorrection
        pol = PolarizationCorrection(pol_fraction=args.pol_fraction,
                                     pol_plane_eta_deg=args.pol_plane)
    if args.solid_angle:
        from midas_integrate_v2 import SolidAngleCorrection
        sa = SolidAngleCorrection()
    return (pol, sa)


def main(argv=None) -> int:
    args = _build_arg_parser().parse_args(argv)

    from midas_gui.helpers import check_output_dir_writable
    reason = check_output_dir_writable(args.out_dir)
    if reason:
        print(f"[batch] ERROR: {reason}", flush=True)
        return 1

    # A QApplication instance is required to construct QThread/QObject
    # subclasses (BatchWorker) even though we never call .start() or run an
    # event loop — .run() is invoked directly, synchronously, on this thread.
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([sys.argv[0]])

    from midas_gui.helpers import spec_from_geometry_file
    from midas_gui.workers import BatchWorker

    spec = spec_from_geometry_file(args.calib_file, args.r_bin, args.eta_bin,
                                   r_min=args.r_min, r_max=args.r_max)
    src_cfg = _source_cfg(args)
    mask = _load_field(args.mask)
    if mask is not None:
        mask = (mask != 0)
    dark = _load_field(args.dark)
    bright = _load_field(args.bright)
    background = _load_field(args.background)
    fmts = [f.strip() for f in args.fmts.split(",") if f.strip()]
    corrections = _build_corrections(args)
    variance_cfg = {"error_model": args.error_model} if args.variance else None
    frame_range = (args.frame_start, args.frame_end, args.frame_stride)

    worker = BatchWorker(
        spec, src_cfg, mask, args.out_dir, fmts, args.kernel,
        corrections, variance_cfg,
        frame_range=frame_range, monitor_file=args.monitor_file,
        dark=dark, bright=bright, background=background, bright_mode=args.bright_mode,
        weighted=args.weighted, multi_azimuth=args.multi_azimuth,
        im_trans=tuple(spec.TransOpt or ()))

    exit_code = [0]

    def _on_progress(done, total):
        print(f"[batch] PROGRESS {done}/{total}", flush=True)

    def _on_log(line):
        print(line, flush=True)

    def _on_failed(msg):
        print(f"[batch] ERROR: {msg}", flush=True)
        exit_code[0] = 1

    def _on_finished(data):
        n = data.get("n", 0)
        out = data.get("out_paths") or []
        print(f"[batch] FINISHED n={n} out_paths={len(out)}", flush=True)

    worker.progress.connect(_on_progress)
    worker.log_line.connect(_on_log)
    worker.failed.connect(_on_failed)
    worker.finished.connect(_on_finished)
    worker.run()

    del app
    return exit_code[0]


if __name__ == "__main__":
    sys.exit(main())
