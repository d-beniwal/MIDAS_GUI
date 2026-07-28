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

from midas_gui.constants import _SENTINELS, _LATT, H5_EXTS, _V2_TO_V1

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


def _tilt_matrix_np(tx_deg: float, ty_deg: float, tz_deg: float) -> np.ndarray:
    """Pure-numpy ``Rx(tx) @ Ry(ty) @ Rz(tz)`` (degrees), matching the rotation
    convention of ``midas_calibrate_v2.forward.geometry.build_tilt_matrix``."""
    tx, ty, tz = math.radians(tx_deg), math.radians(ty_deg), math.radians(tz_deg)
    cx, sx = math.cos(tx), math.sin(tx)
    cy, sy = math.cos(ty), math.sin(ty)
    cz, sz = math.cos(tz), math.sin(tz)
    Rx = np.array([[1.0, 0.0, 0.0], [0.0, cx, -sx], [0.0, sx, cx]])
    Ry = np.array([[cy, 0.0, sy], [0.0, 1.0, 0.0], [-sy, 0.0, cy]])
    Rz = np.array([[cz, -sz, 0.0], [sz, cz, 0.0], [0.0, 0.0, 1.0]])
    return Rx @ Ry @ Rz


def tilted_ring_xy(two_theta_deg: float, tx: float, ty: float, tz: float,
                    Lsd_um: float, bc_y: float, bc_z: float,
                    pxY_um: float, pxZ_um: float, n: int = 400):
    """Forward-project a diffraction ring at ``two_theta_deg`` through the tilt
    geometry (tx/ty/tz, degrees) onto detector pixel coordinates.

    Returns ``(Y_px, Z_px)`` arrays of length ``n`` tracing the ring. Reduces
    exactly to the plain circle ``bc + r*(sin η, cos η)`` when tx=ty=tz=0, since
    it inverts the same ray/tilt-plane geometry as
    ``midas_integrate_v2.forward.pixels.pixel_to_REta_from_spec``.
    """
    eta = np.linspace(0.0, 2.0 * math.pi, n, endpoint=False)
    tt = math.radians(two_theta_deg)
    u = np.stack([
        np.full_like(eta, math.cos(tt)),
        -math.sin(tt) * np.sin(eta),
        math.sin(tt) * np.cos(eta),
    ], axis=1)                                       # (n, 3) unit ray directions
    TRs = _tilt_matrix_np(tx, ty, tz)
    n_hat = TRs[:, 0]
    denom = u @ n_hat
    denom = np.where(np.abs(denom) < 1e-12, 1e-12, denom)
    t = Lsd_um * TRs[0, 0] / denom
    off = t[:, None] * u - np.array([Lsd_um, 0.0, 0.0])
    Yc = off @ TRs[:, 1]
    Zc = off @ TRs[:, 2]
    Y_px = bc_y - Yc / pxY_um
    Z_px = bc_z + Zc / pxZ_um
    return Y_px, Z_px


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
        # MIDAS convention (matches midas_integrate_v2.poni_to_bc):
        # BC_y = Poni1/pxY, BC_z = Poni2/pxZ.
        if poni1 is not None and px1:
            out["BC_y"] = poni1 / px1
        if poni2 is not None and px2:
            out["BC_z"] = poni2 / px2
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


def write_poni(geom: dict, path: str | Path) -> None:
    """Write a pyFAI ``.poni`` file from a normalized geometry dict (the same
    shape ``geometry_fields_from_file``/``get_geometry`` produce: ``Lsd``,
    ``BC_y``, ``BC_z``, ``pxY``, ``pxZ`` in µm/px, ``wavelength_A`` in Å).

    Inverts the MIDAS convention used by ``read_geometry``/
    ``geometry_fields_from_file`` (``BC_y = Poni1/pxY``, ``BC_z = Poni2/pxZ``).
    MIDAS ``tx``/``ty``/``tz`` tilts have no equivalent in PONI's Rot1-3
    convention and are **not** exported (Rot1/2/3 are written as 0.0) —
    matching the existing reader's documented limitation.
    """
    px_y_um = float(geom["pxY"])
    px_z_um = float(geom.get("pxZ") or geom["pxY"])
    px1_m, px2_m = px_y_um * 1e-6, px_z_um * 1e-6
    distance_m = float(geom["Lsd"]) * 1e-6
    poni1 = float(geom["BC_y"]) * px1_m
    poni2 = float(geom["BC_z"]) * px2_m
    wavelength_m = float(geom["wavelength_A"]) * 1e-10
    ny, nz = geom.get("NrPixelsY"), geom.get("NrPixelsZ")
    max_shape = f"[{int(nz)}, {int(ny)}]" if (ny and nz) else "null"
    lines = [
        "# MIDAS GUI — Data Viewer calibration export",
        "poni_version: 2.1",
        "Detector: Detector",
        f'Detector_config: {{"pixel1": {px1_m!r}, "pixel2": {px2_m!r}, "max_shape": {max_shape}}}',
        f"Distance: {distance_m!r}",
        f"Poni1: {poni1!r}",
        f"Poni2: {poni2!r}",
        "Rot1: 0.0",
        "Rot2: 0.0",
        "Rot3: 0.0",
        f"Wavelength: {wavelength_m!r}",
    ]
    Path(path).write_text("\n".join(lines) + "\n")


def _build_spec(result, r_bin: float, eta_bin: float):
    from midas_calibrate_v2.compat.to_integrate import spec_from_calibration_result
    return spec_from_calibration_result(result, RBinSize=r_bin, EtaBinSize=eta_bin)


def _spec_from_json(path: str, r_bin: float, eta_bin: float):
    from midas_calibrate_v2.compat.to_integrate import spec_from_calibration_json
    return spec_from_calibration_json(path, RBinSize=r_bin, EtaBinSize=eta_bin)


# v1 paramstest index (p#) → v2 harmonic name — the inverse of the single source
# of truth ``constants._V2_TO_V1`` (avoids a hand-maintained second copy).
_PARAMSTEST_DISTORTION = {v1: v2 for v2, v1 in _V2_TO_V1.items()}


def write_standalone_paramstest(result, path, *, extra=None):
    """Write a v1 ``paramstest.txt`` from an AutoCalibrationResult (no geometry
    dependency on a live pipeline). Single implementation shared by the Calibrate
    and Export tabs. ``extra`` merges extra key/values into ``params.extra``."""
    import math
    from midas_calibrate.params import CalibrationParams
    from midas_gui.constants import _SG, _LC
    cal = getattr(result, "_calibrant_name", "CeO2")
    NY, NZ = result.NrPixelsY, result.NrPixelsZ
    pxY = float(result.pxY); pxZ = float(result.pxZ) if result.pxZ else pxY
    RhoD = math.sqrt(max(result.BC_y, NY - result.BC_y) ** 2 +
                     max(result.BC_z, NZ - result.BC_z) ** 2)
    p = CalibrationParams(
        NrPixelsY=NY, NrPixelsZ=NZ, pxY=pxY, pxZ=pxZ, Lsd=result.Lsd,
        BC_y=result.BC_y, BC_z=result.BC_z, tx=result.tx, ty=result.ty, tz=result.tz,
        Wavelength=result.wavelength_A, SpaceGroup=_SG.get(cal, 225),
        LatticeConstant=_LC.get(cal, _LC["CeO2"]), RhoD=RhoD, MaxRingRad=RhoD * 0.97)
    for v2n, v1n in _V2_TO_V1.items():
        val = (result.distortion or {}).get(v2n)
        if val is not None:
            setattr(p, v1n, float(val))
    for k, v in (extra or {}).items():
        p.extra[k] = v
    p.write(str(path))
    return p


def _spec_from_result_ns(r_bin, eta_bin, **fields):
    """Build an IntegrationSpec from geometry fields via a duck-typed result.

    Routes through ``spec_from_calibration_result`` so RhoD, RMax and the bin
    counts are derived exactly as for a live calibration result.
    """
    from types import SimpleNamespace
    from midas_calibrate_v2.compat.to_integrate import spec_from_calibration_result
    ns = SimpleNamespace(
        NrPixelsY=int(fields["NrPixelsY"]), NrPixelsZ=int(fields["NrPixelsZ"]),
        pxY=float(fields["pxY"]), pxZ=float(fields.get("pxZ") or fields["pxY"]),
        Lsd=float(fields["Lsd"]), BC_y=float(fields["BC_y"]), BC_z=float(fields["BC_z"]),
        tx=float(fields.get("tx") or 0.0), ty=float(fields.get("ty") or 0.0),
        tz=float(fields.get("tz") or 0.0), wavelength_A=float(fields["wavelength_A"]),
        distortion=fields.get("distortion") or {}, residual_corr_bin_path=None)
    return spec_from_calibration_result(ns, RBinSize=float(r_bin), EtaBinSize=float(eta_bin))


def geometry_fields_from_file(path: str) -> dict:
    """Parse a MIDAS paramstest, a pyFAI ``.poni``, or a calibration ``.json``
    into a normalized full-geometry dict (auto-detected by extension then content).

    Returns keys ``NrPixelsY, NrPixelsZ, pxY, pxZ, Lsd`` (µm), ``BC_y, BC_z`` (px),
    ``tx, ty, tz`` (deg), ``wavelength_A`` (Å), ``distortion`` (dict).  ``pxZ``
    defaults to ``pxY`` and tilts default to 0 when absent.  Raises ``ValueError``
    if a required key is missing.

    PONI tilts (Rot1/2/3) are not mapped to MIDAS ty/tz/tx — only the beam-centre
    translation is used (consistent with MIDAS's own ``poni_to_bc``).
    """
    import json
    p = Path(path)
    text = p.read_text()
    suf = p.suffix.lower()

    def _norm(fields):
        fields["pxZ"] = fields.get("pxZ") or fields["pxY"]
        for t in ("tx", "ty", "tz"):
            fields[t] = fields.get(t) or 0.0
        fields["distortion"] = fields.get("distortion") or {}
        return fields

    # ── calibration.json (GUI bare keys OR pipeline *_um/_px/_deg keys) ──
    if suf == ".json" or text.lstrip().startswith("{"):
        c = json.loads(text)

        def g(*keys):
            for k in keys:
                if k in c and c[k] is not None:
                    return c[k]
            return None

        fields = dict(
            NrPixelsY=g("NrPixelsY"), NrPixelsZ=g("NrPixelsZ"),
            pxY=g("pxY", "pxY_um"), pxZ=g("pxZ", "pxZ_um"),
            Lsd=g("Lsd", "Lsd_um"), BC_y=g("BC_y", "BC_y_px"), BC_z=g("BC_z", "BC_z_px"),
            tx=g("tx", "tx_deg"), ty=g("ty", "ty_deg"), tz=g("tz", "tz_deg"),
            wavelength_A=g("wavelength_A", "Wavelength"), distortion=c.get("distortion", {}))
        missing = [k for k in ("NrPixelsY", "NrPixelsZ", "pxY", "Lsd", "BC_y", "BC_z",
                               "wavelength_A") if fields[k] is None]
        if missing:
            raise ValueError(f"calibration json missing keys: {', '.join(missing)}")
        return _norm(fields)

    # ── pyFAI .poni ──
    if suf == ".poni" or "poni_version" in text or "Poni1" in text:
        from midas_integrate_v2 import poni_to_bc
        vals, det_cfg = {}, {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            k, _, v = line.partition(":")
            k, v = k.strip().lower(), v.strip()
            if k == "detector_config":
                try:
                    det_cfg = json.loads(v)
                except Exception:
                    det_cfg = {}
            else:
                vals[k] = v

        def f(k):
            try:
                return float(vals[k])
            except (KeyError, ValueError):
                return None

        dist_m, poni1, poni2, wl_m = f("distance"), f("poni1"), f("poni2"), f("wavelength")
        px1, px2, shape = det_cfg.get("pixel1"), det_cfg.get("pixel2"), det_cfg.get("max_shape")
        if None in (dist_m, poni1, poni2, wl_m) or px1 is None or px2 is None or not shape:
            raise ValueError(
                "PONI missing Distance/Poni1/Poni2/Wavelength or Detector_config "
                "with pixel1/pixel2 + max_shape — cannot build an integration spec.")
        pxZ_um, pxY_um = float(px1) * 1e6, float(px2) * 1e6      # axis1=slow=Z, axis2=fast=Y
        NrPixelsZ, NrPixelsY = int(shape[0]), int(shape[1])
        bc_y, bc_z = poni_to_bc(float(poni1), float(poni2), pxY_um, pxZ_um)
        return _norm(dict(
            NrPixelsY=NrPixelsY, NrPixelsZ=NrPixelsZ, pxY=pxY_um, pxZ=pxZ_um,
            Lsd=float(dist_m) * 1e6, BC_y=bc_y, BC_z=bc_z,
            tx=0.0, ty=0.0, tz=0.0, wavelength_A=float(wl_m) * 1e10, distortion={}))

    # ── MIDAS paramstest ──
    kv, p_vals = {}, {}
    NY = NZ = None
    for line in text.splitlines():
        parts = line.split()
        if not parts:
            continue
        key = parts[0]
        try:
            if key == "Lsd":
                kv["Lsd"] = float(parts[1])
            elif key == "BC":
                kv["BC_y"], kv["BC_z"] = float(parts[1]), float(parts[2])
            elif key in ("tx", "ty", "tz"):
                kv[key] = float(parts[1])
            elif key == "Wavelength":
                kv["wavelength_A"] = float(parts[1])
            elif key in ("px", "pxY"):
                kv["pxY"] = float(parts[1])
            elif key == "NrPixelsY":
                NY = int(float(parts[1]))
            elif key == "NrPixelsZ":
                NZ = int(float(parts[1]))
            elif len(key) > 1 and key[0] == "p" and key[1:].isdigit():
                p_vals[key] = float(parts[1])
        except (ValueError, IndexError):
            continue
    missing = [n for n in ("Lsd", "BC_y", "pxY", "wavelength_A") if n not in kv]
    if NY is None or NZ is None:
        missing.append("NrPixelsY/NrPixelsZ")
    if missing:
        raise ValueError(f"paramstest missing keys: {', '.join(missing)}")
    dist = {v2: p_vals[p1] for p1, v2 in _PARAMSTEST_DISTORTION.items() if p1 in p_vals}
    return _norm(dict(
        NrPixelsY=NY, NrPixelsZ=NZ, pxY=kv["pxY"], pxZ=kv["pxY"],
        Lsd=kv["Lsd"], BC_y=kv["BC_y"], BC_z=kv["BC_z"], tx=kv.get("tx"), ty=kv.get("ty"),
        tz=kv.get("tz"), wavelength_A=kv["wavelength_A"], distortion=dist))


def spec_from_geometry_file(path: str, r_bin: float, eta_bin: float):
    """Build an IntegrationSpec from a MIDAS paramstest, a pyFAI ``.poni``, or a
    calibration ``.json``.  Thin wrapper over :func:`geometry_fields_from_file`."""
    return _spec_from_result_ns(r_bin, eta_bin, **geometry_fields_from_file(path))


def result_ns_from_geometry_file(path: str):
    """Build a duck-typed calibration *result* (SimpleNamespace) from a paramstest,
    ``.poni`` or ``.json`` geometry file — carries the attributes
    ``spec_from_calibration_result`` / the tabs' ``set_calibration`` expect
    (``wavelength_A, Lsd, BC_y, BC_z, pxY, pxZ, NrPixelsY, NrPixelsZ, tx, ty, tz,
    distortion``)."""
    from types import SimpleNamespace
    f = geometry_fields_from_file(path)
    return SimpleNamespace(
        NrPixelsY=int(f["NrPixelsY"]), NrPixelsZ=int(f["NrPixelsZ"]),
        pxY=float(f["pxY"]), pxZ=float(f["pxZ"]),
        Lsd=float(f["Lsd"]), BC_y=float(f["BC_y"]), BC_z=float(f["BC_z"]),
        tx=float(f["tx"]), ty=float(f["ty"]), tz=float(f["tz"]),
        wavelength_A=float(f["wavelength_A"]), distortion=f["distortion"],
        residual_corr_bin_path=None)


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


# ── GUI-state serialization (Save/Load GUI State) ────────────────────────────────

def widgets_to_dict(widgets: dict) -> dict:
    """Snapshot a ``{key: widget}`` map into a plain JSON-able dict, by widget type:
    spin boxes → ``.value()``, combo boxes → current text, line edits → ``.text()``,
    checkable buttons → ``.isChecked()``. Unrecognized widget types are skipped."""
    out = {}
    for key, w in widgets.items():
        if isinstance(w, QtWidgets.QAbstractSpinBox):
            out[key] = w.value()
        elif isinstance(w, QtWidgets.QComboBox):
            out[key] = w.currentText()
        elif isinstance(w, QtWidgets.QLineEdit):
            out[key] = w.text()
        elif isinstance(w, QtWidgets.QAbstractButton):
            out[key] = w.isChecked()
    return out


def apply_dict_to_widgets(widgets: dict, data: dict) -> None:
    """Inverse of :func:`widgets_to_dict`. Restores each field in its own
    try/except (a stale or missing key can't abort the rest of the restore) and
    blocks signals around each set, same convention as ``set_geometry``. Combo
    boxes are matched by text; a value no longer present in the list is left
    untouched rather than raising."""
    for key, w in widgets.items():
        if key not in data:
            continue
        val = data[key]
        w.blockSignals(True)
        try:
            if isinstance(w, QtWidgets.QAbstractSpinBox):
                w.setValue(val)
            elif isinstance(w, QtWidgets.QComboBox):
                idx = w.findText(str(val))
                if idx >= 0:
                    w.setCurrentIndex(idx)
                elif w.isEditable():
                    w.setEditText(str(val))
            elif isinstance(w, QtWidgets.QLineEdit):
                w.setText(str(val))
            elif isinstance(w, QtWidgets.QAbstractButton):
                w.setChecked(bool(val))
        except Exception:
            pass
        finally:
            w.blockSignals(False)


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


class _NoScrollComboBox(QtWidgets.QComboBox):
    """QComboBox that ignores mouse-wheel scrolls so the selection never changes
    by accident; the event propagates to the parent (e.g. the scroll panel).
    The drop-down popup still scrolls normally when open."""
    def wheelEvent(self, e):
        e.ignore()


def _fspin(lo, hi, dec, val, suf="", step=None):
    """``step`` (if given) fixes the up/down-arrow increment; omit it to keep the
    default adaptive-decimal stepping used everywhere else in the GUI."""
    s = _NoScrollDoubleSpinBox()
    s.setRange(lo, hi); s.setDecimals(dec); s.setValue(val)
    if step is None:
        s.setStepType(QtWidgets.QAbstractSpinBox.AdaptiveDecimalStepType)
    else:
        s.setStepType(QtWidgets.QAbstractSpinBox.DefaultStepType)
        s.setSingleStep(step)
    if suf:
        s.setSuffix(f"  {suf}")
    s.setMaximumWidth(104)   # keep numeric fields compact (don't stretch to fill forms)
    return s


def _clickable_menu_label(text, entries, parent=None):
    """A clickable, form-label-sized widget with a popup menu.

    Looks like a field label (underlined, accent colour) but occupies the same
    space and pops a menu on click. ``entries`` is a list of ``(label, callback)``;
    the callback runs when its item is chosen.
    """
    from PyQt5 import QtWidgets
    btn = QtWidgets.QToolButton(parent)
    btn.setText(text)
    btn.setAutoRaise(True)
    btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
    btn.setCursor(QtCore.Qt.PointingHandCursor)
    btn.setStyleSheet(
        "QToolButton { border: none; padding: 0 2px; color: #4da3ff; }"
        "QToolButton::menu-indicator { image: none; }")
    f = btn.font(); f.setUnderline(True); btn.setFont(f)
    menu = QtWidgets.QMenu(btn)
    for label, cb in entries:
        act = menu.addAction(label)
        act.triggered.connect(lambda _checked=False, c=cb: c())
    btn.setMenu(menu)
    return btn


def make_kedge_label(wl_spin, text="λ:", parent=None):
    """A clickable 'λ' label that pops a K-edge foil menu; selecting an entry sets
    ``wl_spin`` to that element's K-edge wavelength."""
    from midas_gui.constants import K_EDGE_FOILS, HC_KEV_A
    entries = [(f"{sym}   {keV:.2f} keV · {HC_KEV_A / keV:.5f} Å",
                (lambda l=HC_KEV_A / keV: wl_spin.setValue(float(l))))
               for sym, keV in K_EDGE_FOILS]
    btn = _clickable_menu_label(text, entries, parent)
    btn.setToolTip("Click to set λ from a common K-edge foil energy.")
    return btn


def make_pixel_label(px_spin, text="px:", also=None, parent=None):
    """A clickable pixel-size label that pops a common-detector menu; selecting an
    entry sets ``px_spin`` (and ``also``, if given) to that detector's pixel size."""
    from midas_gui.constants import PIXEL_PRESETS

    def _setter(um):
        def _apply():
            px_spin.setValue(float(um))
            if also is not None:
                also.setValue(float(um))
        return _apply

    entries = [(f"{name}  ({um:g} µm)", _setter(um)) for name, um in PIXEL_PRESETS]
    btn = _clickable_menu_label(text, entries, parent)
    btn.setToolTip("Click to set the pixel size from a common detector.")
    return btn


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
