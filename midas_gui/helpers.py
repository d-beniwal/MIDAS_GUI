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


# ── Dark / bright / background field building ───────────────────────────────────

def list_h5_datasets(path: str | Path) -> list:
    """Return [(name, shape), …] for every ≥2-D dataset in an HDF5 file."""
    import h5py
    items: list = []

    def _visit(name, obj):
        if isinstance(obj, h5py.Dataset) and obj.ndim >= 2:
            items.append((name, tuple(obj.shape)))

    with h5py.File(str(path), "r") as f:
        f.visititems(_visit)
    return items


def _collect_frame_paths(raw: str) -> list:
    """Frames from a folder or a *.tif glob (sorted).  Mirrors tab_view logic."""
    import glob as _glob
    p = Path(raw)
    if p.is_dir():
        out = []
        for ext in ("*.tif", "*.tiff", "*.h5", "*.hdf5", "*.ge*", "*.cbf", "*.edf"):
            out.extend(sorted(p.glob(ext)))
        return [str(x) for x in out]
    return sorted(_glob.glob(raw))


def average_field(kind: str, path: str, dataset: str = "exchange/data",
                  idx_start: int = 0, idx_end: int = -1) -> np.ndarray:
    """Build a single 2-D field by averaging over an index range.

    kind:
      "file"   — a single image file; if it holds a 3-D stack, average [start..end].
      "folder" — a folder or *.tif glob; average frames [start..end] across files.
      "hdf5"   — average dataset[start..end+1] if 3-D, else the 2-D dataset.

    idx_end = -1 means "through the last frame" (inclusive).
    """
    def _slice(n: int) -> tuple:
        s = max(0, int(idx_start))
        e = n - 1 if idx_end is None or int(idx_end) < 0 else min(int(idx_end), n - 1)
        return s, e

    if kind == "hdf5":
        import h5py
        with h5py.File(str(path), "r") as f:
            dset = f[dataset]
            if dset.ndim >= 3:
                s, e = _slice(dset.shape[0])
                return np.asarray(dset[s:e + 1], dtype=np.float64).mean(axis=0)
            return np.asarray(dset[...], dtype=np.float64)

    if kind == "folder":
        paths = _collect_frame_paths(path)
        if not paths:
            raise ValueError(f"No frames found for '{path}'")
        s, e = _slice(len(paths))
        acc, n = None, 0
        for p in paths[s:e + 1]:
            a = _load_image(p).astype(np.float64)
            a = a[0] if a.ndim == 3 else a       # guard multi-page file in a folder
            acc = a if acc is None else acc + a
            n += 1
        return acc / max(n, 1)

    # single file
    arr = _load_image(path).astype(np.float64)
    if arr.ndim >= 3:
        s, e = _slice(arr.shape[0])
        return arr[s:e + 1].mean(axis=0)
    return arr


def apply_field_corrections(img: np.ndarray, *, dark=None, bright=None,
                            bright_mode: str = "divide", background=None,
                            clip_negative: bool = True) -> np.ndarray:
    """Apply dark subtraction, bright (flat-field divide OR subtract) and background.

    Order: (img − dark) → bright → (− background) → clip≥0.  For divide mode the
    flat field is dark-corrected too: out / (bright − dark) × mean(bright − dark).
    Returns float64.  Any field may be None.
    """
    out = np.asarray(img, dtype=np.float64)
    d = None if dark is None else np.asarray(dark, dtype=np.float64)
    if d is not None:
        out = out - d
    if bright is not None:
        b = np.asarray(bright, dtype=np.float64)
        if d is not None:
            b = b - d
        if bright_mode == "subtract":
            out = out - b
        else:  # flat-field divide, rescaled to preserve counts
            b = np.clip(b, 1e-9, None)
            out = out / b * float(np.mean(b))
    if background is not None:
        out = out - np.asarray(background, dtype=np.float64)
    if clip_negative:
        out = np.clip(out, 0.0, None)
    return out


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


def read_geometry(path: str | Path) -> dict:
    """Parse beam-centre / distance / pixel / wavelength from a calibration file.

    Supports three formats, auto-detected by extension then content:
      - MIDAS ``paramstest`` text  (``Lsd``, ``BC y z``, ``Wavelength``, ``px`` — µm/Å)
      - pyFAI ``.poni``            (SI units: Distance/Poni1/Poni2 in m, Wavelength in m)
      - calibration ``.json``      (as saved by the Calibrate tab)

    Returns a dict with keys ``wavelength_A``, ``Lsd_um``, ``px_um``, ``BC_y``,
    ``BC_z`` — any of which may be ``None`` if the file does not carry it.
    Note: PONI tilts (Rot1/2/3) are ignored — only the beam-centre projection is used.
    """
    p = Path(path)
    text = p.read_text()
    suf = p.suffix.lower()
    out = {"wavelength_A": None, "Lsd_um": None, "px_um": None,
           "BC_y": None, "BC_z": None}

    # ── calibration.json ──
    if suf == ".json" or text.lstrip().startswith("{"):
        import json
        d = json.loads(text)
        out["wavelength_A"] = d.get("wavelength_A")
        out["Lsd_um"] = d.get("Lsd")
        out["px_um"] = d.get("pxY") if d.get("pxY") is not None else d.get("px")
        out["BC_y"] = d.get("BC_y")
        out["BC_z"] = d.get("BC_z")
        return out

    # ── pyFAI .poni ──
    if suf == ".poni" or "poni_version" in text or "Poni1" in text:
        vals, det_cfg = {}, {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            key, _, val = line.partition(":")
            key, val = key.strip().lower(), val.strip()
            if key == "detector_config":
                try:
                    import json
                    det_cfg = json.loads(val)
                except Exception:
                    det_cfg = {}
            else:
                vals[key] = val

        def _f(k):
            try:
                return float(vals[k])
            except (KeyError, ValueError):
                return None

        dist, poni1, poni2, wl_m = _f("distance"), _f("poni1"), _f("poni2"), _f("wavelength")
        px1 = det_cfg.get("pixel1"); px2 = det_cfg.get("pixel2")
        px1 = float(px1) if px1 is not None else None
        px2 = float(px2) if px2 is not None else px1
        out["Lsd_um"] = dist * 1e6 if dist is not None else None
        out["px_um"] = px1 * 1e6 if px1 is not None else None
        out["wavelength_A"] = wl_m * 1e10 if wl_m is not None else None
        # pyFAI axis-1 = slow (rows, Z); axis-2 = fast (cols, Y)
        if poni1 is not None and px1:
            out["BC_z"] = poni1 / px1
        if poni2 is not None and px2:
            out["BC_y"] = poni2 / px2
        return out

    # ── MIDAS paramstest key-value text ──
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        key = parts[0]
        try:
            if key == "Lsd" and len(parts) >= 2:
                out["Lsd_um"] = float(parts[1])
            elif key == "BC" and len(parts) >= 3:
                out["BC_y"], out["BC_z"] = float(parts[1]), float(parts[2])
            elif key == "Wavelength" and len(parts) >= 2:
                out["wavelength_A"] = float(parts[1])
            elif key in ("px", "pxY") and len(parts) >= 2:
                out["px_um"] = float(parts[1])
        except ValueError:
            continue
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
