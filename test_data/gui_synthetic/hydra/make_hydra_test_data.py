#!/usr/bin/env python3
"""Generate synthetic Hydra (4-panel GE detector) test data.

Small (256x256), fast, deterministic — for automated tests only (real,
uncalibrated/calibrated Hydra data is far larger and lives outside the repo).

Each panel gets an idealized windmill geometry (tx = 0/90/180/270 deg, shared
beam centre at the panel's own image centre) and a single bright square
marker at the same *local* raw-pixel offset from its own beam centre
(60 px along +Y). Because the four panels share one relative marker offset
but different tx rotations, the marker should land at four different,
analytically-predictable positions around the shared composite canvas —
this is what tests/test_hydra_geometry.py and tests/test_hydra_chirality.py
check.

Panel file naming matches the real beamline convention exercised by
helpers.hydra_siblings: both a `geN/` folder segment AND a `.geN.` filename
infix (e.g. `ge1/panel.ge1.h5`).

Re-run with: python test_data/gui_synthetic/hydra/make_hydra_test_data.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import h5py

HERE = Path(__file__).resolve().parent

NY = NZ = 256           # panel size (px)
PX_UM = 200.0
LSD_UM = 1_000_000.0    # arbitrary — unused by the compositing math
WAVELENGTH = 1.0        # arbitrary — unused by the compositing math
BC = NY / 2.0           # beam centre at the panel's own image centre
# Deliberately NOT exact multiples of 90 deg (unlike an idealized windmill):
# at 0/90/180/270 a sign error in the rotation direction aliases one panel's
# marker exactly onto another panel's real marker position, which would let
# a chirality bug slip past a "does the expected position light up" check.
# These match the real bundled 1-ID-E default geometry's spacing (~90 deg
# apart, not round numbers) closely enough to be representative.
TX_BY_PANEL = {1: 15.0, 2: 105.0, 3: 195.0, 4: 285.0}
MARKER_OFFSET_Y = 60.0   # local raw-pixel offset from BC, along +Y
MARKER_HALF = 5          # marker is a (2*MARKER_HALF)x(2*MARKER_HALF) square
MARKER_VALUE = 1000.0


def marker_center_rc():
    """(row, col) of the marker's centre in raw panel-pixel space — same for
    every panel, since the offset is defined relative to each panel's own BC."""
    col = BC + MARKER_OFFSET_Y
    row = BC
    return row, col


def make_panel_image() -> np.ndarray:
    img = np.zeros((NZ, NY), dtype=np.float32)
    row, col = marker_center_rc()
    r0, r1 = int(row - MARKER_HALF), int(row + MARKER_HALF)
    c0, c1 = int(col - MARKER_HALF), int(col + MARKER_HALF)
    img[r0:r1, c0:c1] = MARKER_VALUE
    return img


def write_param_file(panel: int, path: Path):
    tx = TX_BY_PANEL[panel]
    lines = [
        f"Wavelength {WAVELENGTH}",
        "SpaceGroup 225",
        f"Lsd {LSD_UM}",
        f"BC {BC} {BC}",
        "px 200.0",
        "NrPixelsY 256",
        "NrPixelsZ 256",
        f"tx {tx}",
        "ty 0.0",
        "tz 0.0",
        "ImTransOpt 0",
    ]
    path.write_text("\n".join(lines) + "\n")


def main():
    img = make_panel_image()
    for panel in (1, 2, 3, 4):
        pdir = HERE / f"ge{panel}"
        pdir.mkdir(exist_ok=True)
        h5_path = pdir / f"panel.ge{panel}.h5"
        with h5py.File(h5_path, "w") as f:
            f.create_dataset("exchange/data", data=img[None, :, :])
        write_param_file(panel, HERE / f"ge{panel}" / f"ps_ge{panel}.txt")
        print(f"wrote {h5_path} + ps_ge{panel}.txt (tx={TX_BY_PANEL[panel]})")
    print("Done. Wrote", HERE)


if __name__ == "__main__":
    main()
