"""Build the Hydra MCP benchmark from test_data/s1ide/connoly_oct26.

Run:  python -I make_bench.py   (midas-gui env; needs h5py, numpy)

Output (all under this directory, gitignored except this script):
  data/<scan>/geN/<name>/<name>_<id>.geN.h5      20-frame CeO2 stacks, 4 panels
  data/<scan>/geN/dark_<name>/dark_<name>_<id+1>.geN.h5
  calib_set1/<name>.geN.instr.{json,txt}         the real Oct-8 11:48 calibration
  calib_set2/<name>.geN.instr.{json,txt}         perturbed (Lsd, BC, ty, tz) copy
  manifest.json                                  scan -> files + calibration set

Source has 10 real frames per panel; each benchmark stack cycles them to 20.
Scans B and C add seeded Poisson resampling so the three datasets differ.
"""
import json
import re
import shutil
from pathlib import Path

import h5py
import numpy as np

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "s1ide" / "connoly_oct26"
SRC_BC = HERE.parent / "s1ide" / "connoly_oct26_bc"
NFRAMES = 20
PANELS = ["ge1", "ge2", "ge3", "ge4"]
# (scan label, scan id, calibration set, noise seed or None)
SCANS = [("scanA", 3502, "calib_set1", None),
         ("scanB", 3510, "calib_set1", 11),
         ("scanC", 3520, "calib_set2", 22)]
BASE = "CeO2_80p725keV_2300mm_1s_120x120um_1s"
DARK = "dark_80p725keV_2300mm_1s_120x120um_1s"


def _src(panel, kind):
    d = SRC / panel / (BASE if kind == "data" else DARK)
    return next(d.glob("*.h5"))


def write_stack(dst, panel, rng):
    src = _src(panel, "data")
    dst.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(src, "r") as fi, h5py.File(dst, "w") as fo:
        for k in fi.keys():
            if k == "exchange":
                continue
            fi.copy(k, fo)
        ex = fo.create_group("exchange")
        raw = fi["exchange/data"][...]
        out = ex.create_dataset("data", shape=(NFRAMES,) + raw.shape[1:],
                                dtype=raw.dtype, chunks=(1,) + raw.shape[1:],
                                compression="gzip", compression_opts=1,
                                shuffle=True)
        for i in range(NFRAMES):
            fr = raw[i % raw.shape[0]]
            if rng is not None:
                fr = np.clip(rng.poisson(fr.astype(np.float64)), 0,
                             65535).astype(raw.dtype)
            out[i] = fr
        for k in ("data_dark", "data_white"):
            if k in fi["exchange"]:
                fi["exchange"].copy(k, ex)


def write_dark(dst, panel):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_src(panel, "dark"), dst)


def calib_sets():
    stem = "%s_003502.ge1" % BASE
    for n, panel in enumerate(PANELS, 1):
        j = json.loads((SRC_BC / ("%s_%s.instr.json" % (stem, panel))).read_text())
        t = (SRC_BC / ("%s_%s.instr.txt" % (stem, panel))).read_text()
        name = "%s.%s.instr" % (BASE, panel)
        d1 = HERE / "calib_set1"
        d1.mkdir(exist_ok=True)
        (d1 / (name + ".json")).write_text(json.dumps(j, indent=2))
        (d1 / (name + ".txt")).write_text(t)
        # set 2: deterministic, per-panel perturbation of a few parameters
        dl = 1200.0 * (1 + 0.1 * n)           # um
        dby, dbz = 3.0 * n, -2.0 * n          # px
        dty, dtz = 0.05 * n, -0.03 * n        # deg
        j2 = dict(j)
        j2["Lsd"] = j["Lsd"] + dl
        j2["BC_y"] = j["BC_y"] + dby
        j2["BC_z"] = j["BC_z"] + dbz
        j2["ty"] = j["ty"] + dty
        j2["tz"] = j["tz"] + dtz
        t2 = t
        for key, val in (("Lsd", j2["Lsd"]), ("ty", j2["ty"]),
                         ("tz", j2["tz"])):
            t2 = re.sub(r"(?m)^%s .*$" % key, "%s %.6f" % (key, val), t2)
        t2 = re.sub(r"(?m)^BC .*$", "BC %.6f %.6f" % (j2["BC_y"], j2["BC_z"]), t2)
        d2 = HERE / "calib_set2"
        d2.mkdir(exist_ok=True)
        (d2 / (name + ".json")).write_text(json.dumps(j2, indent=2))
        (d2 / (name + ".txt")).write_text(t2)


def main():
    manifest = {}
    calib_sets()
    for label, sid, cset, seed in SCANS:
        rng = np.random.default_rng(seed) if seed is not None else None
        files = {}
        for panel in PANELS:
            name = "%s_%06d" % (BASE, sid)
            p = HERE / "data" / label / panel / BASE / ("%s.%s.h5" % (name, panel))
            write_stack(p, panel, rng)
            dn = "%s_%06d" % (DARK, sid + 1)
            write_dark(HERE / "data" / label / panel / DARK / ("%s.%s.h5" % (dn, panel)), panel)
            files[panel] = str(p)
        manifest[label] = {"scan_id": sid, "calibration_set": cset,
                           "frames": NFRAMES, "files": files}
        print("built", label, cset)
    (HERE / "manifest.json").write_text(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
