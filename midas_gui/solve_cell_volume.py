"""Solve Cell "Volume" view: build + inspect the quick-look reciprocal intensity
and coverage volume (V03), its 2-D slabs (V04) and a 3-D isosurface (V05).

* ``VolumeBuildWorker`` runs ``reciprocal_volume.build_volume`` off the GUI
  thread from immutable per-panel source descriptions (file path + dataset +
  mask); it opens the files itself and never touches Qt widgets.
* ``ReciprocalVolumeView`` shows a finished volume. Unmeasured voxels
  (``count == 0``) are drawn as a distinct dark grey in 2-D and are outside the
  isosurface -- never conflated with measured-zero. Display transforms
  (asinh/log) apply to the rendered image only, never to the stored data.

Axes: arrays are ``(nx, ny, nz)`` cell data in SAMPLE-frame q (Å⁻¹, 2π/d);
``origin`` is the lower edge of voxel (0,0,0).
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pyqtgraph as pg
import pyvista as pv
from PyQt5 import QtCore, QtGui, QtWidgets
from pyvistaqt import QtInteractor

from midas_gui.solve_cell import reciprocal_volume as rv

_AXES = ("qx", "qy", "qz")
_UNMEASURED_RGB = (38, 38, 42)


class VolumeBuildWorker(QtCore.QThread):
    progress = QtCore.pyqtSignal(float)
    finished_ok = QtCore.pyqtSignal(object, object)     # (volume dict, GridSpec)
    failed = QtCore.pyqtSignal(str)
    cancelled = QtCore.pyqtSignal()

    def __init__(self, source_cfgs: list, snaps: dict, pitch: float, parent=None):
        """``source_cfgs``: ``[{"panel_id", "path", "dataset", "mask"}]``."""
        super().__init__(parent)
        self._cfgs, self._snaps, self._pitch = source_cfgs, snaps, float(pitch)
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        import traceback
        try:
            import h5py
            spec = rv.auto_grid({c["panel_id"]: self._snaps[c["panel_id"]] for c in self._cfgs}, self._pitch)
            files, sources = [], []
            for c in self._cfgs:
                f = h5py.File(c["path"], "r")
                files.append(f)
                d = f[c["dataset"]]
                sources.append({"snap": self._snaps[c["panel_id"]], "frames": (lambda a, b, _d=d: _d[a:b]),
                                "mask": c.get("mask"), "n_frames": int(d.shape[0])})
            try:
                vol = rv.build_volume(sources, spec, progress=self.progress.emit, cancel=lambda: self._cancel)
            finally:
                for f in files:
                    f.close()
            vol, spec = rv.crop_to_coverage(vol, spec)
            self.finished_ok.emit(vol, spec)
        except rv.VolumeCancelled:
            self.cancelled.emit()
        except Exception:
            self.failed.emit(traceback.format_exc())


class ReciprocalVolumeView(QtWidgets.QWidget):
    spotClicked = QtCore.pyqtSignal(object)
    buildRequested = QtCore.pyqtSignal(float)
    cancelRequested = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.vol: Optional[dict] = None
        self.spec: Optional[rv.GridSpec] = None
        self.meta: dict = {}
        self._spots: list = []
        self._mean: Optional[np.ndarray] = None
        self._build_ui()

    # ── UI ──────────────────────────────────────────────────────────
    def _build_ui(self):
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        r1 = QtWidgets.QHBoxLayout()
        r1.addWidget(QtWidgets.QLabel("Voxel pitch (Å⁻¹):"))
        self.pitch = QtWidgets.QDoubleSpinBox()
        self.pitch.setDecimals(3); self.pitch.setRange(0.01, 0.5); self.pitch.setSingleStep(0.01)
        self.pitch.setValue(0.06)
        self.pitch.setToolTip(
            "Cubic voxel size in q (2π/d). One detector pixel spans ≈0.007 Å⁻¹ here, "
            "so a covered voxel holds several hundred pixel samples over the scan (median 730 here); smaller pitch = "
            "more memory/time, sharper peaks.")
        r1.addWidget(self.pitch)
        self.build_btn = QtWidgets.QPushButton("Build reciprocal volume")
        self.build_btn.setToolTip(
            "Map every unmasked pixel of every in-window frame into sample-frame q and "
            "accumulate max / sum / coverage per voxel. Quick look: static median "
            "background, nominal frame omega, nearest voxel; no dark/bright/polarization "
            "corrections. Needs the original HDF5 frames.")
        self.build_btn.clicked.connect(lambda: self.buildRequested.emit(self.pitch.value()))
        r1.addWidget(self.build_btn)
        self.cancel_btn = QtWidgets.QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancelRequested)
        r1.addWidget(self.cancel_btn)
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 1000); self.progress.setMaximumWidth(220)
        r1.addWidget(self.progress)
        r1.addStretch(1)
        lay.addLayout(r1)
        self.info = QtWidgets.QLabel("No volume yet.")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)

        r2 = QtWidgets.QHBoxLayout()
        self.field = QtWidgets.QComboBox(); self.field.addItems(["Max", "Mean", "Coverage (samples)"])
        self.scale = QtWidgets.QComboBox(); self.scale.addItems(["asinh", "log10", "linear"])
        self.reduce = QtWidgets.QComboBox(); self.reduce.addItems(["max", "mean", "sum"])
        self.axis = QtWidgets.QComboBox(); self.axis.addItems([f"normal {a}" for a in _AXES])
        self.axis.setCurrentIndex(2)
        for lab, w in (("Field:", self.field), ("Scale:", self.scale), ("Slab reduce:", self.reduce),
                       ("Slice:", self.axis)):
            r2.addWidget(QtWidgets.QLabel(lab)); r2.addWidget(w)
            if isinstance(w, QtWidgets.QComboBox):
                w.currentIndexChanged.connect(self._on_axis_or_field)
        self.slice_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slice_slider.valueChanged.connect(lambda _=None: self._redraw_slice())
        r2.addWidget(self.slice_slider, stretch=1)
        self.slice_label = QtWidgets.QLabel("—"); self.slice_label.setMinimumWidth(90)
        r2.addWidget(self.slice_label)
        r2.addWidget(QtWidgets.QLabel("± voxels:"))
        self.thick = QtWidgets.QSpinBox(); self.thick.setRange(0, 20)
        self.thick.valueChanged.connect(lambda _=None: self._redraw_slice())
        r2.addWidget(self.thick)
        self.show_spots = QtWidgets.QCheckBox("centroids in slab"); self.show_spots.setChecked(True)
        self.show_spots.toggled.connect(lambda _=None: self._redraw_slice())
        r2.addWidget(self.show_spots)
        lay.addLayout(r2)

        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        lay.addWidget(split, stretch=1)
        self.glw = pg.GraphicsLayoutWidget()
        self.plot = self.glw.addPlot()
        self.plot.setAspectLocked(True)
        self.img_item = pg.ImageItem(axisOrder="col-major")
        self.plot.addItem(self.img_item)
        self.spot_item = pg.ScatterPlotItem(size=11, pen=pg.mkPen("#ff3b8d", width=2), brush=pg.mkBrush(None))
        self.spot_item.sigClicked.connect(lambda _s, pts, _e: pts and self.spotClicked.emit(pts[0].data()))
        self.plot.addItem(self.spot_item)
        self.readout = QtWidgets.QLabel("hover the slice for q / value")
        self.plot.scene().sigMouseMoved.connect(self._on_hover)
        left = QtWidgets.QWidget(); lv = QtWidgets.QVBoxLayout(left); lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(self.glw, stretch=1); lv.addWidget(self.readout)
        split.addWidget(left)

        right = QtWidgets.QWidget(); rvl = QtWidgets.QVBoxLayout(right); rvl.setContentsMargins(0, 0, 0, 0)
        r3 = QtWidgets.QHBoxLayout()
        r3.addWidget(QtWidgets.QLabel("Isosurface level (counts):"))
        self.level = QtWidgets.QSlider(QtCore.Qt.Horizontal); self.level.setRange(0, 1000); self.level.setValue(500)
        self.level.valueChanged.connect(lambda _=None: self._redraw_3d())
        r3.addWidget(self.level, stretch=1)
        self.level_label = QtWidgets.QLabel("—"); self.level_label.setMinimumWidth(80)
        r3.addWidget(self.level_label)
        self.show_cov = QtWidgets.QCheckBox("coverage outline"); self.show_cov.setChecked(True)
        self.show_cov.toggled.connect(lambda _=None: self._redraw_3d())
        r3.addWidget(self.show_cov)
        rvl.addLayout(r3)
        self.iv = QtInteractor(right)
        self.iv.set_background("black")
        rvl.addWidget(self.iv, stretch=1)
        split.addWidget(right)
        split.setSizes([620, 520])
        self._iso_actors: dict = {}

    # ── build-state helpers (called by the tab) ─────────────────────
    def set_building(self, busy: bool):
        self.build_btn.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy)
        if not busy:
            self.progress.setValue(0)

    def set_progress(self, frac: float):
        self.progress.setValue(int(frac * 1000))

    def set_spots(self, rows: list):
        self._spots = rows
        self._redraw_slice()

    # ── data ────────────────────────────────────────────────────────
    def set_volume(self, vol: Optional[dict], spec: Optional[rv.GridSpec], meta: Optional[dict] = None):
        if self.vol is None:
            self._cam_fitted = False
        self.vol, self.spec, self.meta = vol, spec, meta or {}
        self._mean = rv.mean_field(vol) if vol is not None else None
        if vol is None:
            self.info.setText("No volume yet.")
            self.img_item.clear(); self.spot_item.setData([])
            self._clear_3d()
            return
        cnt = vol["count"]
        cov = float((cnt > 0).mean())
        mx = vol["max"]
        self.info.setText(
            f"grid {spec.shape[0]}×{spec.shape[1]}×{spec.shape[2]} @ {spec.spacing:g} Å⁻¹ · "
            f"origin {tuple(round(o, 3) for o in spec.origin)} · measured voxels {cov * 100:.1f}% · "
            f"max {np.nanmax(mx):.4g} counts · {self.meta.get('approximations', 'quick-look approximations apply')}")
        self._on_axis_or_field()
        fin = mx[np.isfinite(mx)]
        self._level_range = (max(float(np.percentile(fin, 99.0)), 1.0), float(np.nanmax(mx)))
        self._redraw_3d()

    def _axis(self) -> int:
        return self.axis.currentIndex()

    def _on_axis_or_field(self, *_):
        if self.vol is None:
            return
        ax = self._axis()
        n = self.spec.shape[ax]
        self.slice_slider.blockSignals(True)
        self.slice_slider.setRange(0, n - 1)
        if not (0 <= self.slice_slider.value() < n):
            self.slice_slider.setValue(n // 2)
        self.slice_slider.blockSignals(False)
        self._redraw_slice()

    # ── 2-D slab ────────────────────────────────────────────────────
    def _field_array(self) -> np.ndarray:
        i = self.field.currentIndex()
        return (self.vol["max"], self._mean, self.vol["count"])[i]

    def slab_image(self):
        """``(display image (n1,n2) in [0,1] w/ NaN, raw reduced image, coverage image)``."""
        ax, idx, h = self._axis(), self.slice_slider.value(), self.thick.value()
        field = self._field_array()
        red = self.reduce.currentText() if self.field.currentIndex() != 2 else "sum"
        raw, cov = rv.axis_slab(field, self.vol["count"], ax, idx, h, red)
        sc = self.scale.currentText()
        disp = raw.copy()
        if self.field.currentIndex() == 2:
            disp = raw
        elif sc == "asinh":
            disp = rv.asinh_scale(raw)
        elif sc == "log10":
            with np.errstate(all="ignore"):
                disp = np.log10(np.where(raw > 0, raw, np.nan))
        return disp, raw, cov

    def _redraw_slice(self):
        if self.vol is None:
            return
        ax, idx = self._axis(), self.slice_slider.value()
        disp, raw, cov = self.slab_image()
        fin = disp[np.isfinite(disp)]
        lo, hi = (np.percentile(fin, 1), np.percentile(fin, 99.9)) if fin.size else (0, 1)
        if hi <= lo:
            hi = lo + 1
        lut = pg.colormap.get("viridis").getLookupTable(nPts=256, alpha=False)
        norm = np.clip((disp - lo) / (hi - lo), 0, 1)
        rgb = lut[np.nan_to_num(norm * 255).astype(np.uint8)]
        unmeasured = ~(cov > 0)
        rgb[unmeasured] = _UNMEASURED_RGB
        rgba = np.concatenate([rgb, np.full(rgb.shape[:2] + (1,), 255, np.uint8)], axis=2)
        self.img_item.setImage(rgba, autoLevels=False)
        a1, a2 = [a for a in range(3) if a != ax]
        s = self.spec
        self.img_item.setRect(QtCore.QRectF(s.origin[a1], s.origin[a2], s.shape[a1] * s.spacing,
                                            s.shape[a2] * s.spacing))
        self.plot.setLabel("bottom", f"{_AXES[a1]} (Å⁻¹)"); self.plot.setLabel("left", f"{_AXES[a2]} (Å⁻¹)")
        c = s.origin[ax] + (idx + 0.5) * s.spacing
        h = self.thick.value()
        self.slice_label.setText(f"{_AXES[ax]} = {c:+.3f}" + (f" ±{h * s.spacing:.2f}" if h else ""))
        self._slab_cache = (ax, a1, a2, raw, cov)
        # observed centroids inside the slab (stable spot ids ride along as data)
        if self.show_spots.isChecked() and self._spots:
            col = ("qsample_x", "qsample_y", "qsample_z")
            half = (h + 0.5) * s.spacing
            sel = [r for r in self._spots if abs(r[col[ax]] - c) <= half]
            self.spot_item.setData(x=[r[col[a1]] for r in sel], y=[r[col[a2]] for r in sel],
                                   data=[r["spot_id"] for r in sel])
        else:
            self.spot_item.setData([])
        self._redraw_3d_plane(ax, c)

    def _on_hover(self, pos):
        if self.vol is None or not hasattr(self, "_slab_cache"):
            return
        pt = self.plot.vb.mapSceneToView(pos)
        ax, a1, a2, raw, cov = self._slab_cache
        s = self.spec
        i = int(np.floor((pt.x() - s.origin[a1]) / s.spacing))
        j = int(np.floor((pt.y() - s.origin[a2]) / s.spacing))
        if 0 <= i < raw.shape[0] and 0 <= j < raw.shape[1]:
            v = raw[i, j]
            self.readout.setText(
                f"{_AXES[a1]}={pt.x():+.3f}  {_AXES[a2]}={pt.y():+.3f}  ·  "
                + ("UNMEASURED" if not cov[i, j] > 0 else f"value {v:.5g}  ({int(cov[i, j])} samples)"))

    # ── 3-D ─────────────────────────────────────────────────────────
    def _clear_3d(self):
        for a in self._iso_actors.values():
            self.iv.remove_actor(a)
        self._iso_actors = {}

    def _image_data(self, arr: np.ndarray) -> "pv.ImageData":
        """Voxel-centre point data (no half-voxel shift); F-order ravel for VTK."""
        s = self.spec
        g = pv.ImageData(dimensions=tuple(s.shape),
                         origin=tuple(o + 0.5 * s.spacing for o in s.origin),
                         spacing=(s.spacing,) * 3)
        g.point_data["v"] = np.asarray(arr, np.float32).ravel(order="F")
        return g

    def level_value(self) -> float:
        lo, hi = self._level_range
        return float(np.exp(np.log(lo) + (np.log(hi) - np.log(lo)) * self.level.value() / 1000.0))

    def _redraw_3d(self):
        if self.vol is None:
            return
        camera = self.iv.camera_position
        had = getattr(self, "_cam_fitted", False)
        for k in ("iso", "cov"):
            if k in self._iso_actors:
                self.iv.remove_actor(self._iso_actors.pop(k))
        lv = self.level_value()
        self.level_label.setText(f"{lv:.4g}")
        render = np.nan_to_num(self.vol["max"], nan=0.0)         # render buffer only
        iso = self._image_data(render).contour([lv], scalars="v")
        if iso.n_points:
            self._iso_actors["iso"] = self.iv.add_mesh(iso, color="#ffd43b", opacity=0.95,
                                                       reset_camera=False, name="solve_cell_vol_iso")
        if self.show_cov.isChecked():
            cov = self._image_data((self.vol["count"] > 0).astype(np.float32)).contour([0.5], scalars="v")
            if cov.n_points:
                self._iso_actors["cov"] = self.iv.add_mesh(cov, color="#7f8c9a", opacity=0.10,
                                                           reset_camera=False, name="solve_cell_vol_cov")
        for a in self._iso_actors.values():
            a.SetPickable(False)
        if not had:
            self.iv.reset_camera()
            self.iv.show_axes()
            self._cam_fitted = True
        else:
            self.iv.camera_position = camera
        self.iv.render()

    def _redraw_3d_plane(self, ax: int, c: float):
        if "plane" in self._iso_actors:
            self.iv.remove_actor(self._iso_actors.pop("plane"))
        s = self.spec
        lo = [s.origin[a] for a in range(3)]; hi = [s.origin[a] + s.shape[a] * s.spacing for a in range(3)]
        center = [(lo[a] + hi[a]) / 2 for a in range(3)]; center[ax] = c
        size = [hi[a] - lo[a] for a in range(3)]
        normal = [0, 0, 0]; normal[ax] = 1
        i_size, j_size = [size[a] for a in range(3) if a != ax]
        plane = pv.Plane(center=center, direction=normal, i_size=i_size, j_size=j_size)
        self._iso_actors["plane"] = self.iv.add_mesh(plane, color="#ff3b8d", opacity=0.18, reset_camera=False,
                                                     name="solve_cell_vol_slice_plane")
        self._iso_actors["plane"].SetPickable(False)
        self.iv.render()
