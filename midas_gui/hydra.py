"""Geometry-aware compositing for the 1-ID-E Hydra detector (4x GE panels).

Ported from a sibling project's ``multidet.py``/``hydra.py`` — the BC/tilt-aware
inverse-coordinate remap that places each panel's raw frame into a shared
"BigDet" composite frame matching the physical windmill arrangement of the
real detector. Frame IO/ImTransOpt reuse this package's own
``helpers._load_image``/``_apply_im_trans``, and per-panel geometry reuses
this package's own ``helpers.geometry_fields_from_file`` (a MIDAS paramstest/
PONI/calibration.json parser already used everywhere else in the GUI) rather
than a bespoke per-panel-file parser.

Default geometry (BC, Lsd, tx/ty/tz, px) is bundled under
``hydra_default_geometry/`` — a generic 1-ID-E-style example windmill layout
(the tx tilts are ~90° apart, a real fitted geometry, not idealized round
numbers), used only until a real per-panel calibration file is loaded.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from scipy.ndimage import map_coordinates

from midas_gui.helpers import (_load_image, _apply_im_trans, geometry_fields_from_file,
                         apply_field_corrections)

_GEOMETRY_DIR = Path(__file__).parent / "hydra_default_geometry"


def default_param_file(panel: int) -> Path:
    return _GEOMETRY_DIR / f"ps_ge{panel}.txt"


def compute_inv_coords(bc_y: float, bc_z: float, tx_deg: float,
                       big_det_size: int, px: float):
    """Inverse-mapping coordinate grids: for each composite output pixel,
    the detector pixel that should be sampled — a rigid rotation by
    ``tx_deg`` about the panel's own beam centre, re-anchored to the
    composite canvas centre, plus a mirror about the composite's vertical
    axis (see below).

    Rotation direction is COUNTERCLOCKWISE by ``tx_deg`` (in this app's
    Y-right/Z-up, bottom-left-origin display convention). A same-day session
    briefly "corrected" this to clockwise (negating ``tx_deg`` here) based on
    a plausible-looking but wrong argument that the physical Tx sign implied
    it; that version was reverted after a real windowed comparison against
    `test_data/s1ide` (real CeO2 Hydra data) showed it visibly mis-rotates
    the composite relative to the known-correct reference arrangement — see
    the 2026-08-23 "Hydra composite rotation direction: reverted back to
    counterclockwise" DECISIONS.md entry.

    **Vertical-axis mirror**: a real windowed comparison (same session as
    the rotation-direction revert, next DECISIONS.md entry) found the
    composite still didn't match the known-correct reference until the
    whole canvas was additionally mirrored left-right (ge2/ge4 land on the
    wrong side of the canvas otherwise). Implemented here as ``half - Yo``
    instead of ``Yo - half`` for ``Y_lab`` — mirrors the output canvas
    without touching the per-panel rotation math above, and without
    affecting any other viewer (this coordinate map is only ever used by
    the windmill-composite build, not the per-panel ge1-4 raw displays)."""
    bds = int(big_det_size)
    half = bds * 0.5
    tx_rad = math.radians(tx_deg)
    c, s = math.cos(tx_rad), math.sin(tx_rad)

    yo = np.arange(bds, dtype=np.float32)
    zo = np.arange(bds, dtype=np.float32)
    Yo, Zo = np.meshgrid(yo, zo)

    Y_lab = (half - Yo) * px
    Z_lab = (Zo - half) * px
    Y_det = Y_lab * c + Z_lab * s
    Z_det = -Y_lab * s + Z_lab * c
    y_pix = bc_y - Y_det / px
    z_pix = bc_z + Z_det / px
    return z_pix.astype(np.float32), y_pix.astype(np.float32)


def remap_to_composite(image: np.ndarray, row_coords: np.ndarray,
                       col_coords: np.ndarray, cval: float = np.nan) -> np.ndarray:
    return map_coordinates(image, [row_coords, col_coords],
                           order=1, mode="constant", cval=cval, prefilter=False)


def composite(images: list, op: str = "max") -> np.ndarray:
    if not images:
        raise ValueError("composite() needs at least one image")
    stack = np.stack(images, axis=0)
    if op == "sum":
        out = np.nansum(stack, axis=0)
    else:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=RuntimeWarning)
            out = np.nanmax(stack, axis=0)
        out = np.where(np.isnan(out), 0.0, out)
    return out.astype(np.float32)


def autopick_big_det_size(states: list) -> int:
    """2x the farthest BC-to-corner distance across all panels, rounded up
    to the next 256px — BC commonly sits outside the panel's active area on
    Hydra, so this can far exceed 2x(NrPixels)."""
    max_extent = 0.0
    for s in states:
        corners = ((0, 0), (0, s.nz - 1), (s.ny - 1, 0), (s.ny - 1, s.nz - 1))
        for (i, j) in corners:
            d = math.hypot(j - s.bc_y, i - s.bc_z)
            if d > max_extent:
                max_extent = d
    if max_extent == 0.0:
        sizes = [max(s.ny, s.nz) for s in states]
        return 2 * max(sizes) if sizes else 4096
    raw = 2.0 * max_extent + 64.0
    return int(math.ceil(raw / 256.0)) * 256


@dataclass
class DetectorState:
    """One Hydra panel's geometry + loaded-frame source. ``load_from_geometry_dict``
    consumes the dict shape returned by ``helpers.geometry_fields_from_file``
    (or ``helpers.read_geometry``-derived callers), so the same calibration-file
    parser used everywhere else in the GUI drives per-panel geometry here too."""
    data_file: str = ""
    calib_path: str = ""
    bc_y: float = 0.0
    bc_z: float = 0.0
    tx: float = 0.0
    ty: float = 0.0
    tz: float = 0.0
    lsd: float = 0.0
    px: float = 200.0
    ny: int = 2048
    nz: int = 2048
    im_trans_opts: list = field(default_factory=list)
    dark: Optional[np.ndarray] = None
    bright: Optional[np.ndarray] = None
    bright_mode: str = "divide"
    background: Optional[np.ndarray] = None
    _inv_coords: Optional[tuple] = None
    _inv_cache_key: tuple = ()

    def load_from_geometry_dict(self, fields: dict, calib_path: str = "") -> None:
        self.calib_path = calib_path
        if fields.get("BC_y") is not None: self.bc_y = float(fields["BC_y"])
        if fields.get("BC_z") is not None: self.bc_z = float(fields["BC_z"])
        self.tx = float(fields.get("tx") or 0.0)
        self.ty = float(fields.get("ty") or 0.0)
        self.tz = float(fields.get("tz") or 0.0)
        if fields.get("Lsd") is not None: self.lsd = float(fields["Lsd"])
        if fields.get("pxY") is not None: self.px = float(fields["pxY"])
        if fields.get("NrPixelsY") is not None: self.ny = int(fields["NrPixelsY"])
        if fields.get("NrPixelsZ") is not None: self.nz = int(fields["NrPixelsZ"])
        self.im_trans_opts = list(fields.get("im_trans") or [])
        self._inv_coords = None
        self._inv_cache_key = ()

    def load_default(self, panel: int) -> None:
        self.load_from_geometry_dict(geometry_fields_from_file(str(default_param_file(panel))))

    def get_inv_coords(self, big_det_size: int) -> tuple:
        key = (self.bc_y, self.bc_z, self.tx, int(big_det_size), float(self.px))
        if self._inv_cache_key == key and self._inv_coords is not None:
            return self._inv_coords
        rows, cols = compute_inv_coords(self.bc_y, self.bc_z, self.tx,
                                        big_det_size, self.px)
        self._inv_coords = (rows, cols)
        self._inv_cache_key = key
        return self._inv_coords

    def get_remapped_frame(self, frame_idx: int, dataset: str,
                           big_det_size: int) -> np.ndarray:
        img = _load_image(self.data_file, dataset, frame_idx)
        if self.dark is not None or self.bright is not None or self.background is not None:
            # Same order as the single-detector tab (DataLoaderPanel.corrected,
            # then _apply_im_trans) — correction on the raw frame, before ImTransOpt.
            img = apply_field_corrections(img, dark=self.dark, bright=self.bright,
                                          bright_mode=self.bright_mode,
                                          background=self.background)
        img = _apply_im_trans(img, tuple(self.im_trans_opts))
        rows, cols = self.get_inv_coords(big_det_size)
        return remap_to_composite(img, rows, cols)


_big_det_size_cache: dict = {}


def build_windmill_composite(siblings: dict, frame_idx: int, dataset: str,
                             states: dict, op: str = "max"):
    """Composite `frame_idx` from every panel in `siblings` (panel number ->
    file path) into one BC/tilt-registered BigDet frame, using `states` as a
    persistent cache of DetectorState per panel (geometry + inverse-
    coordinate maps only need computing once). A panel not already present in
    `states` is seeded from its bundled default geometry — call
    ``states[m].load_from_geometry_dict(...)`` beforehand to use a real
    per-panel calibration instead. Returns (composite, big_det_size)."""
    for m, path in siblings.items():
        st = states.get(m)
        if st is None:
            st = DetectorState()
            st.load_default(m)
            states[m] = st
        st.data_file = path

    active = [states[m] for m in siblings]
    # Keyed by each panel's actual geometry, not just which panel numbers are
    # present — a stale panel-number-only key would keep returning the first
    # canvas size ever computed even after a panel's beam centre/tx changes
    # (e.g. loading a different calibration file for one panel).
    key = tuple(sorted((m, round(st.bc_y, 3), round(st.bc_z, 3), round(st.tx, 4),
                       round(st.px, 4), st.ny, st.nz)
                      for m, st in zip(siblings, active)))
    big_det_size = _big_det_size_cache.get(key)
    if big_det_size is None:
        big_det_size = autopick_big_det_size(active)
        _big_det_size_cache[key] = big_det_size

    remapped = [st.get_remapped_frame(frame_idx, dataset, big_det_size)
               for st in active]
    return composite(remapped, op=op), big_det_size


def n_frames_in(path: str, dataset: str) -> int:
    """Frame count of an HDF5 dataset (1 if it isn't a 3-D stack)."""
    import h5py
    with h5py.File(str(path), "r") as f:
        dset = f[dataset]
        return int(dset.shape[0]) if dset.ndim >= 3 else 1
