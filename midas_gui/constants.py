"""Shared constants: calibrants, colormaps, dtype sentinels, distortion mapping,
lattice / space-group tables, and output-format lists."""
from __future__ import annotations

CALIBRANTS = ["CeO2", "LaB6", "Si", "Al2O3"]
COLORMAPS  = ["hot", "gray", "viridis", "inferno", "plasma", "turbo"]

# Output formats for the batch tab (label → short key)
OUTPUT_FORMATS = {
    "CSV  (R, I, σ)":          "csv",
    "XYE  (2θ, I, σ)":        "xye",
    "FXYE (centideg, I, σ)":   "fxye",
    "DAT  (Q, I, σ)":          "dat",
    "HDF5 (full stack)":        "h5",
    "2D CSV (cake, η×R)":       "2d_csv",
}

# Integration kernels (label → key)
KERNELS = {
    "Subpixel K=2 (default)": "subpixel2",
    "Subpixel K=4":           "subpixel4",
    "Hard bin (fastest)":     "hard",
    "Polygon (exact, slow)":  "polygon",
}

# Calibration pipelines (label → key, enabled?)
PIPELINES = [
    ("One-shot (default)",       "one_shot",   True),
    ("First-time (no prior)",    "first_time", True),
    ("Four-stage (patchy det.)", "four_stage", True),
    ("Bayesian MAP+Laplace",     "bayesian",   True),    # Phase 2
    ("Joint cake",               "joint",      True),    # Phase 3
    ("Multi-distance",           "multi",      False),   # multi-image, deferred
]

ERROR_MODELS = ["poisson", "azimuthal", "hybrid"]

# HDF5-like extensions (auto-show dataset field)
H5_EXTS = {".h5", ".hdf5", ".hdf", ".nxs"}

# Dtype → saturation sentinel (pixels at or above are considered dead/saturated)
_SENTINELS = {
    "uint8":  2**8  - 1,
    "uint16": 2**16 - 1,
    "uint32": 2**32 - 2,   # 2^32-1 is the Eiger dead-pixel value
    "int16":  2**15 - 1,
    "int32":  2**31 - 1,
}

# v2 distortion name → v1 paramstest p-slot
_V2_TO_V1 = {
    "iso_R2": "p2", "iso_R4": "p5", "iso_R6": "p4",
    "a1": "p7", "phi1": "p8", "a2": "p0", "phi2": "p6",
    "a3": "p9", "phi3": "p10", "a4": "p1", "phi4": "p3",
    "a5": "p11", "phi5": "p12", "a6": "p13", "phi6": "p14",
}

# Canonical ordering of the 15 distortion coefficients for display
DISTORTION_NAMES = [
    "iso_R2", "iso_R4", "iso_R6",
    "a1", "phi1", "a2", "phi2", "a3", "phi3",
    "a4", "phi4", "a5", "phi5", "a6", "phi6",
]

# Lattice + space group per built-in calibrant.
# _LATT used for ring prediction; _SG / _LC used for paramstest export.
_LATT = {
    "CeO2":  dict(a=5.4116, b=5.4116, c=5.4116, alpha=90, beta=90, gamma=90,  sg=225),
    "LaB6":  dict(a=4.1569, b=4.1569, c=4.1569, alpha=90, beta=90, gamma=90,  sg=221),
    "Si":    dict(a=5.4310, b=5.4310, c=5.4310, alpha=90, beta=90, gamma=90,  sg=227),
    "Al2O3": dict(a=4.7589, b=4.7589, c=12.992, alpha=90, beta=90, gamma=120, sg=167),
}

_SG = {"CeO2": 225, "LaB6": 221, "Si": 227, "Al2O3": 167}

_LC = {
    "CeO2":  (5.4116, 5.4116, 5.4116, 90.0, 90.0,  90.0),
    "LaB6":  (4.1569, 4.1569, 4.1569, 90.0, 90.0,  90.0),
    "Si":    (5.4310, 5.4310, 5.4310, 90.0, 90.0,  90.0),
    "Al2O3": (4.7589, 4.7589, 12.992, 90.0, 90.0, 120.0),
}

# Default detector parameters — set to the synthetic test_data_gui geometry
# (Eiger2 500K: 75 µm pixels, λ=0.39 Å, Lsd=121 mm) for easy out-of-the-box testing.
DEFAULT_WAVELENGTH = 0.39      # Å
DEFAULT_PIXEL_UM   = 75.0      # µm
DEFAULT_LSD_UM     = 121_000.0 # µm
DEFAULT_BC_Y       = 10.0      # px (test data beam centre)
DEFAULT_BC_Z       = 10.0      # px

# ── Default test-data paths (scratch_gui/test_data_gui) ─────────────────────────
from pathlib import Path as _Path
_TEST_DATA = _Path(__file__).resolve().parent.parent / "test_data_gui"
DEFAULT_CALIBRANT_TIF = str(_TEST_DATA / "calibrant_ceria.tif")
DEFAULT_CALIBRANT_H5  = str(_TEST_DATA / "calibrant_ceria.h5")
DEFAULT_NICKEL_H5     = str(_TEST_DATA / "nickel_stack.h5")
DEFAULT_NICKEL_DIR    = str(_TEST_DATA / "nickel_tifs")
DEFAULT_NICKEL_FRAME0 = str(_TEST_DATA / "nickel_tifs" / "nickel_000.tif")


# ── Materials database for ring simulation (Tab 0) ──────────────────────────────
# name → dict(a, b, c, alpha, beta, gamma [Å, deg], sg [space-group number]).
# Calibrants first, then common cubic metals / phases.
MATERIALS = {
    "CeO2 (calibrant)":  dict(a=5.4116, b=5.4116, c=5.4116,  alpha=90, beta=90, gamma=90,  sg=225),
    "LaB6 (calibrant)":  dict(a=4.15692, b=4.15692, c=4.15692, alpha=90, beta=90, gamma=90, sg=221),
    "Si (calibrant)":    dict(a=5.43102, b=5.43102, c=5.43102, alpha=90, beta=90, gamma=90, sg=227),
    "Al2O3 (corundum)":  dict(a=4.7589, b=4.7589, c=12.992,  alpha=90, beta=90, gamma=120, sg=167),
    "Cu (FCC)":          dict(a=3.6149, b=3.6149, c=3.6149,  alpha=90, beta=90, gamma=90,  sg=225),
    "Ni (FCC)":          dict(a=3.5238, b=3.5238, c=3.5238,  alpha=90, beta=90, gamma=90,  sg=225),
    "FCC steel (γ-Fe)":  dict(a=3.595,  b=3.595,  c=3.595,   alpha=90, beta=90, gamma=90,  sg=225),
    "BCC steel (α-Fe)":  dict(a=2.8665, b=2.8665, c=2.8665,  alpha=90, beta=90, gamma=90,  sg=229),
    "Au (FCC)":          dict(a=4.0782, b=4.0782, c=4.0782,  alpha=90, beta=90, gamma=90,  sg=225),
    "Ag (FCC)":          dict(a=4.0853, b=4.0853, c=4.0853,  alpha=90, beta=90, gamma=90,  sg=225),
    "Pt (FCC)":          dict(a=3.9242, b=3.9242, c=3.9242,  alpha=90, beta=90, gamma=90,  sg=225),
    "W (BCC)":           dict(a=3.16525, b=3.16525, c=3.16525, alpha=90, beta=90, gamma=90, sg=229),
    "Ti (HCP)":          dict(a=2.9508, b=2.9508, c=4.6855,  alpha=90, beta=90, gamma=120, sg=194),
}
