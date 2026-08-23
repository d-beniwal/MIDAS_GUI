#!/usr/bin/env python3
"""Generate synthetic GUI test data.

Detector : Eiger2 500K  (1028 × 512, 75 µm pixels)
Geometry : beam centre (10, 10) px, wavelength 0.39 Å, Lsd 121000 µm
Calibrant: CeO2 (single frame)
Sample   : pure Nickel, 10 frames, lattice parameter expanding ~0.1%/frame so the
           Debye–Scherrer rings shift inward frame-to-frame.

Realism added on top of the rings:
  * Radial background — bright near the beam centre, decaying with radius.
  * A FIXED detector defect map applied identically to every frame (and the
    calibrant), i.e. it stays the same throughout the stack:
      - dead pixels  (stuck at 0)
      - hot pixels   (stuck at a high count)
      - bad pixels   (stuck at the uint32 Eiger dead-pixel sentinel, 2**32-1)
      - small patches (dead, saturated, or a persistent low-count shadow)

Outputs (in this folder):
  calibrant_ceria.tif / .h5     — CeO2 calibrant image
  nickel_stack.h5               — (10, 512, 1028) uint32 stack, dataset 'exchange/data'
  nickel_tifs/nickel_000..009.tif
  info.txt

Re-run with:  python test_data_gui/make_test_data.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # scratch_gui/test_data_gui
sys.path.insert(0, str(HERE.parent))            # scratch_gui  (so `import gui` works)

import numpy as np
import tifffile
import h5py

import gui._paths            # noqa: F401  — puts MIDAS packages on sys.path
from gui.helpers import simulate_rings

# ── Geometry (Eiger2 500K) ──────────────────────────────────────────────────────
NZ, NY = 512, 1028          # (rows = Z, cols = Y)
PX_UM = 75.0
LSD_UM = 121000.0
WAVELENGTH = 0.39
BC_Y, BC_Z = 10.0, 10.0     # beam centre (col, row) — near the corner
MAX_2THETA = 35.0

# Materials
CEO2 = dict(a=5.4116, b=5.4116, c=5.4116, alpha=90, beta=90, gamma=90, sg=225)
NI_A0 = 3.5238              # Å, FCC
NI_SG = 225
NI_EXPANSION_PER_FRAME = 0.001   # +0.1 % lattice parameter per frame
N_NICKEL_FRAMES = 10

# Background (higher near the beam centre, decaying with radius)
BG_AMP = 220.0             # counts at the beam centre, on top of the floor
BG_SCALE = 230.0          # px decay length
BG_FLOOR = 8.0            # baseline counts far from the centre

SENTINEL = 2 ** 32 - 1     # uint32 Eiger dead-pixel value (bad pixels stuck here)

rng = np.random.default_rng(12345)            # per-frame Poisson noise
_ZZ, _YY = np.mgrid[0:NZ, 0:NY]
_R = np.sqrt((_YY - BC_Y) ** 2 + (_ZZ - BC_Z) ** 2)   # radius from BC, in px
_BACKGROUND = BG_AMP * np.exp(-_R / BG_SCALE) + BG_FLOOR


def make_defects(seed: int = 2024) -> dict:
    """Build ONE detector defect map, reused for every frame (constant in time)."""
    d = np.random.default_rng(seed)
    npix = NZ * NY

    def rand_px(n):
        idx = d.choice(npix, size=n, replace=False)
        return np.unravel_index(idx, (NZ, NY))     # (rows, cols)

    dead = rand_px(180)                                    # stuck at 0
    hr, hc = rand_px(180)
    hot = (hr, hc, d.integers(30_000, 90_000, size=180).astype(np.uint64))  # stuck high
    bad = rand_px(60)                                      # stuck at sentinel

    patches = []                                           # (z0, z1, y0, y1, value)
    for _ in range(3):                                     # dead patches
        z0 = int(d.integers(30, NZ - 30)); y0 = int(d.integers(30, NY - 30))
        patches.append((z0, z0 + int(d.integers(4, 11)), y0, y0 + int(d.integers(4, 13)), 0))
    for _ in range(2):                                     # saturated/hot patches
        z0 = int(d.integers(30, NZ - 30)); y0 = int(d.integers(30, NY - 30))
        patches.append((z0, z0 + int(d.integers(4, 9)), y0, y0 + int(d.integers(4, 9)), 65_000))
    # one persistent low-count "shadow" patch (e.g. a speck on the window)
    z0 = int(d.integers(30, NZ - 30)); y0 = int(d.integers(30, NY - 30))
    patches.append((z0, z0 + 14, y0, y0 + 22, 3))
    return dict(dead=dead, hot=hot, bad=bad, patches=patches)


def apply_defects(img: np.ndarray, d: dict) -> np.ndarray:
    """Stamp the fixed defect map onto a frame (in place)."""
    img[d["dead"]] = 0
    hr, hc, hv = d["hot"]
    img[hr, hc] = hv
    img[d["bad"]] = SENTINEL
    for z0, z1, y0, y1, val in d["patches"]:
        img[z0:z1, y0:y1] = val
    return img


def ring_image(lattice: dict, sg: int, defects: dict, *, sigma=2.0, scale=2600.0,
               first_amp=1.0, decay=0.82) -> np.ndarray:
    """Render rings + radial background + Poisson noise, then stamp the defects."""
    rings = simulate_rings(lattice, sg, WAVELENGTH, LSD_UM, PX_UM, MAX_2THETA)
    clean = _BACKGROUND.copy()
    for k, ring in enumerate(rings):
        r_k = ring["radius_px"]
        if r_k <= 0:
            continue
        amp = first_amp * (decay ** k) * np.exp(-r_k / 700.0)
        clean += scale * amp * np.exp(-((_R - r_k) ** 2) / (2.0 * sigma ** 2))
    noisy = rng.poisson(clean).astype(np.uint32)
    apply_defects(noisy, defects)
    return noisy


def main():
    out = HERE
    (out / "nickel_tifs").mkdir(exist_ok=True)
    defects = make_defects()
    n_def = (len(defects["dead"][0]) + len(defects["hot"][0]) + len(defects["bad"][0]))
    print(f"Defect map: {n_def} bad single pixels + {len(defects['patches'])} patches", flush=True)

    # ── CeO2 calibrant ──
    print("Rendering CeO2 calibrant…", flush=True)
    cal = ring_image(CEO2, 225, defects, scale=3000.0, sigma=2.0)
    tifffile.imwrite(str(out / "calibrant_ceria.tif"), cal)
    with h5py.File(out / "calibrant_ceria.h5", "w") as f:
        f.create_dataset("exchange/data", data=cal, compression="gzip")
    print(f"  calibrant_ceria.tif/.h5  shape={cal.shape}", flush=True)

    # ── Nickel stack (expanding lattice, same defect map every frame) ──
    print(f"Rendering {N_NICKEL_FRAMES} Nickel frames…", flush=True)
    frames = []
    for i in range(N_NICKEL_FRAMES):
        a = NI_A0 * (1.0 + NI_EXPANSION_PER_FRAME * i)
        latt = dict(a=a, b=a, c=a, alpha=90, beta=90, gamma=90)
        img = ring_image(latt, NI_SG, defects, scale=2400.0, sigma=2.0)
        frames.append(img)
        tifffile.imwrite(str(out / "nickel_tifs" / f"nickel_{i:03d}.tif"), img)
        print(f"  frame {i}: a={a:.5f} Å", flush=True)
    stack = np.stack(frames, axis=0)
    with h5py.File(out / "nickel_stack.h5", "w") as f:
        f.create_dataset("exchange/data", data=stack, compression="gzip")
    print(f"  nickel_stack.h5  shape={stack.shape}  dataset='exchange/data'", flush=True)

    # sanity: defects identical across the stack
    same = all(np.array_equal(frames[0][defects["dead"]], frames[k][defects["dead"]])
               for k in range(1, N_NICKEL_FRAMES))
    print(f"  defects constant across stack: {same}", flush=True)

    # ── info.txt ──
    (out / "info.txt").write_text(
        "Synthetic GUI test data\n"
        "=======================\n"
        "Detector   : Eiger2 500K  (NrPixelsY=1028, NrPixelsZ=512), 75 um pixels\n"
        f"Wavelength : {WAVELENGTH} A\n"
        f"Lsd        : {LSD_UM} um\n"
        f"BeamCentre : BC_y={BC_Y}, BC_z={BC_Z} px\n"
        "Calibrant  : CeO2 (calibrant_ceria.tif / .h5)\n"
        "Sample     : Nickel FCC, a0=3.5238 A, +0.1%/frame expansion, 10 frames\n"
        "             nickel_tifs/nickel_000..009.tif  and  nickel_stack.h5 [exchange/data]\n"
        "\n"
        "Artifacts (added on top of the rings):\n"
        f"  Background : radial, ~{BG_AMP+BG_FLOOR:.0f} counts near BC decaying to ~{BG_FLOOR:.0f}\n"
        "  Dead px    : 180 pixels stuck at 0           (fixed positions, constant in time)\n"
        "  Hot px     : 180 pixels stuck at 30k-90k     (fixed positions)\n"
        f"  Bad px     : 60 pixels stuck at sentinel {SENTINEL}  (uint32 Eiger dead value)\n"
        "  Patches    : 3 dead + 2 saturated + 1 low-count shadow, same on every frame\n"
    )

    # ── ground-truth calibration files (loadable in the Calibrate / PDF tabs) ──
    import json
    from midas_integrate_v2 import bc_to_poni
    _dist_keys = ["iso_R2", "iso_R4", "iso_R6", "a1", "phi1", "a2", "phi2",
                  "a3", "phi3", "a4", "phi4", "a5", "phi5", "a6", "phi6"]
    (out / "calibration_synthetic.json").write_text(json.dumps({
        "Lsd": LSD_UM, "BC_y": BC_Y, "BC_z": BC_Z, "tx": 0.0, "ty": 0.0, "tz": 0.0,
        "distortion": {k: 0.0 for k in _dist_keys},
        "pxY": PX_UM, "pxZ": PX_UM, "NrPixelsY": NY, "NrPixelsZ": NZ,
        "wavelength_A": WAVELENGTH,
        "_note": "Ground-truth geometry for this synthetic test data.",
    }, indent=2) + "\n")
    _pt = [f"Lsd {LSD_UM:.6f}", f"BC {BC_Y:.6f} {BC_Z:.6f}",
           "tx 0.000000", "ty 0.000000", "tz 0.000000"] + [f"p{i} 0" for i in range(15)] + [
           "Parallax 0.000000", f"Wavelength {WAVELENGTH:.6f}", f"px {PX_UM:.6f}",
           f"NrPixelsY {NY}", f"NrPixelsZ {NZ}", "SpaceGroup 225",
           "LatticeConstant 5.411600 5.411600 5.411600 90.000000 90.000000 90.000000"]
    (out / "calibration_synthetic.txt").write_text("\n".join(_pt) + "\n")
    _poni1, _poni2 = bc_to_poni(BC_Y, BC_Z, PX_UM, PX_UM)
    _det = {"pixel1": PX_UM * 1e-6, "pixel2": PX_UM * 1e-6,
            "max_shape": [NZ, NY]}
    (out / "calibration_synthetic.poni").write_text("\n".join([
        "# pyFAI Calibration file — synthetic midas-gui test data",
        "poni_version: 2.1", "Detector: Detector",
        f"Detector_config: {json.dumps(_det)}",
        f"Distance: {LSD_UM * 1e-6:.9f}", f"Poni1: {_poni1:.9f}", f"Poni2: {_poni2:.9f}",
        "Rot1: 0.0", "Rot2: 0.0", "Rot3: 0.0",
        f"Wavelength: {WAVELENGTH * 1e-10:.6e}",
    ]) + "\n")
    print("  calibration_synthetic.json / .txt / .poni  (ground-truth geometry)", flush=True)

    print("Done. Wrote", out, flush=True)


if __name__ == "__main__":
    main()
