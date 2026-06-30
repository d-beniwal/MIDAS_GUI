"""Module-level helpers: image IO, transforms, ring prediction, spec building,
log stream, and the no-scroll spinbox / two-column layout widgets used everywhere.

These are ported verbatim from midas_workflow_gui_v3.py (the frozen template) so
the established conventions in context/design_rules.md are preserved exactly.
"""
from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Optional

import numpy as np
from PyQt5 import QtCore, QtWidgets

from midas_gui.constants import _SENTINELS, _LATT, H5_EXTS

# checkmark SVG written to a temp file so the QSS image: property can use it
import tempfile as _tf
import atexit as _atexit
import os as _os


def _make_checkmark_svg() -> str:
    """White tick SVG → temp file.  Returns forward-slash path for Qt QSS."""
    _svg = (
        b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 14 14'>"
        b"<polyline points='2,7 5.5,11 12,3' stroke='white' stroke-width='2.2'"
        b" fill='none' stroke-linecap='round' stroke-linejoin='round'/>"
        b"</svg>"
    )
    f = _tf.NamedTemporaryFile(suffix=".svg", delete=False)
    f.write(_svg); f.close()
    _atexit.register(_os.unlink, f.name)
    return f.name.replace("\\", "/")   # Qt QSS needs forward slashes on Windows


def _make_arrow_svg(direction: str = "down", color: str = "#333333") -> str:
    """Small filled triangle arrow → temp file, for spinbox/combo sub-controls.

    direction: 'up' or 'down'. Returns a forward-slash path for Qt QSS.
    """
    pts = "2,7 8,7 5,2" if direction == "up" else "2,3 8,3 5,8"
    svg = (
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'>"
        f"<polygon points='{pts}' fill='{color}'/></svg>"
    ).encode()
    f = _tf.NamedTemporaryFile(suffix=".svg", delete=False)
    f.write(svg); f.close()
    _atexit.register(_os.unlink, f.name)
    return f.name.replace("\\", "/")


# ── Image IO ──────────────────────────────────────────────────────────────────

def _load_image(path: str | Path, data_loc: str = "exchange/data",
                frame: int = 0) -> np.ndarray:
    p = Path(path)
    ext = p.suffix.lower()
    if ext in (".tif", ".tiff"):
        import tifffile
        return np.asarray(tifffile.imread(str(p)), dtype=np.float32)
    if ext in H5_EXTS:
        import h5py
        with h5py.File(str(p), "r") as f:
            dset = f[data_loc]
            data = dset[frame] if dset.ndim >= 3 else dset[...]
        return np.asarray(data, dtype=np.float32)
    if ".ge" in p.name.lower():
        arr = np.fromfile(str(p), dtype=np.uint16, offset=8192)
        for side in (2048, 4096, 1024, 512):
            if arr.size >= side * side and arr.size % (side * side) == 0:
                return arr.reshape(-1, side, side)[frame].astype(np.float32)
        raise ValueError(f"Cannot reshape GE file {p}")
    raise ValueError(f"Unsupported format: {p.suffix}")


def _apply_im_trans(image: np.ndarray, codes: tuple) -> np.ndarray:
    """Apply MIDAS image transform codes: 1=flipY, 2=flipZ, 3=transpose."""
    for c in codes:
        if c == 1:
            image = image[:, ::-1]
        elif c == 2:
            image = image[::-1, :]
        elif c == 3:
            image = image.T
    return np.ascontiguousarray(image)


def is_h5(path: str) -> bool:
    return Path(path).suffix.lower() in H5_EXTS


# ── Ring prediction (calibrant → ring radii in px) ──────────────────────────────

def _predict_ring_radii(result) -> list:
    """Predicted ring radii (px) for the result's calibrant geometry."""
    try:
        from midas_hkls import SpaceGroup, Lattice, generate_hkls
        cal = getattr(result, "_calibrant_name", "CeO2")
        lp  = _LATT.get(cal, _LATT["CeO2"])
        lat = Lattice(a=lp["a"], b=lp["b"], c=lp["c"],
                      alpha=lp["alpha"], beta=lp["beta"], gamma=lp["gamma"])
        refs = generate_hkls(SpaceGroup.from_number(lp["sg"]), lat,
                             wavelength_A=result.wavelength_A, two_theta_max_deg=30.0)
        return sorted({round(result.Lsd * math.tan(math.radians(r.two_theta_deg))
                             / result.pxY, 3) for r in refs})
    except Exception:
        return []


# ── Spec building (always via spec_from_calibration_result — RhoD in µm) ─────────

def simulate_rings(lattice: dict, sg: int, wavelength_A: float, lsd_um: float,
                   px_um: float, max_2theta_deg: float = 30.0) -> list:
    """Simulate Debye-Scherrer ring radii (px) for an arbitrary lattice.

    lattice: dict with a,b,c,alpha,beta,gamma.  Returns a list of dicts
    {radius_px, two_theta_deg, hkl, d_spacing} — one entry per distinct ring,
    labelled by the lowest-index reflection contributing to it.
    """
    from midas_hkls import SpaceGroup, Lattice, generate_hkls
    lat = Lattice(a=lattice["a"], b=lattice["b"], c=lattice["c"],
                  alpha=lattice["alpha"], beta=lattice["beta"], gamma=lattice["gamma"])
    refs = generate_hkls(SpaceGroup.from_number(int(sg)), lat,
                         wavelength_A=wavelength_A, two_theta_max_deg=max_2theta_deg)
    by_ring = {}
    for r in refs:
        rn = getattr(r, "ring_nr", None)
        key = rn if rn is not None else round(r.two_theta_deg, 4)
        if key not in by_ring:
            by_ring[key] = r
    out = []
    for r in by_ring.values():
        radius_px = lsd_um * math.tan(math.radians(r.two_theta_deg)) / px_um
        out.append({
            "radius_px": radius_px,
            "two_theta_deg": float(r.two_theta_deg),
            "hkl": (int(r.h), int(r.k), int(r.l)),
            "d_spacing": float(r.d_spacing),
        })
    out.sort(key=lambda d: d["radius_px"])
    return out


def _build_spec(result, r_bin: float, eta_bin: float):
    from midas_calibrate_v2.compat.to_integrate import spec_from_calibration_result
    return spec_from_calibration_result(result, RBinSize=r_bin, EtaBinSize=eta_bin)


def _spec_from_json(path: str, r_bin: float, eta_bin: float):
    from midas_calibrate_v2.compat.to_integrate import spec_from_calibration_json
    return spec_from_calibration_json(path, RBinSize=r_bin, EtaBinSize=eta_bin)


# ── Log stream (redirect verbose stdout to a Qt signal) ─────────────────────────

class _LogStream(io.TextIOBase):
    def __init__(self, sig):
        super().__init__()
        self._sig = sig

    def write(self, s):
        if s.strip():
            self._sig.emit(s.rstrip())
        return len(s)

    def flush(self):
        pass


# ── No-scroll spinboxes (prevent accidental wheel value changes) ────────────────

class _NoScrollSpinBox(QtWidgets.QSpinBox):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

    def wheelEvent(self, e):
        e.ignore()


class _NoScrollDoubleSpinBox(QtWidgets.QDoubleSpinBox):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

    def wheelEvent(self, e):
        e.ignore()


def _fspin(lo, hi, dec, val, suf=""):
    s = _NoScrollDoubleSpinBox()
    s.setRange(lo, hi); s.setDecimals(dec); s.setValue(val)
    s.setStepType(QtWidgets.QAbstractSpinBox.AdaptiveDecimalStepType)
    if suf:
        s.setSuffix(f"  {suf}")
    return s


# ── Layout helpers ──────────────────────────────────────────────────────────────

def _twocol(lbl1, w1, lbl2, w2):
    """Two label+widget pairs on one row: 4 px within a pair, 20 px between pairs.

    Labels passed as strings are auto-converted to right-aligned QLabels.
    """
    def _lbl(x):
        if isinstance(x, str):
            l = QtWidgets.QLabel(x)
            l.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            return l
        return x
    h = QtWidgets.QHBoxLayout()
    h.setSpacing(4)
    h.setContentsMargins(0, 0, 0, 0)
    h.addWidget(_lbl(lbl1))
    h.addWidget(w1)
    h.addSpacing(20)          # clear visual gap between the two pairs
    h.addWidget(_lbl(lbl2))
    h.addWidget(w2)
    h.addStretch(1)
    return h


def _sep():
    f = QtWidgets.QFrame()
    f.setFrameShape(QtWidgets.QFrame.HLine)
    f.setFrameShadow(QtWidgets.QFrame.Sunken)
    return f


def _browse(parent, caption, filt) -> str:
    p, _ = QtWidgets.QFileDialog.getOpenFileName(parent, caption, "", filt)
    return p
