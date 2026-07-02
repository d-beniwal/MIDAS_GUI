"""Tab 0 — Data Viewer.

Plot single/stacked TIFF, HDF5 (2-D or 3-D), or a folder/glob of frames, with a
frame navigator for stacks.  Define a material (dropdown of common calibrants and
metals) or a custom lattice + space group + wavelength + Lsd + pixel size, and
overlay the simulated Debye-Scherrer ring positions on the image.

This tab is purely for inspection — it produces no shared state for other tabs.
"""
from __future__ import annotations

import glob as _glob
import math
from pathlib import Path
from typing import Optional

import numpy as np
from PyQt5 import QtCore, QtWidgets
import pyqtgraph as pg

from midas_gui.constants import (MATERIALS, DEFAULT_WAVELENGTH, DEFAULT_PIXEL_UM,
                           DEFAULT_LSD_UM, DEFAULT_BC_Y, DEFAULT_BC_Z,
                           DEFAULT_NICKEL_H5, H5_EXTS)
from midas_gui.helpers import (_load_image, _fspin, _NoScrollSpinBox, _browse,
                         is_h5, simulate_rings, read_geometry)
from midas_gui.widgets import PickableImageViewer, ProfileViewer
from midas_gui import style as S


class DataViewerTab(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        # Frame storage modes (exactly one active):
        self._stack: Optional[np.ndarray] = None   # in-memory 3-D stack
        self._paths: Optional[list] = None         # on-demand file list
        self._h5: Optional[tuple] = None           # (path, dataset, nframes)
        self._nframes = 0
        self._cur: Optional[np.ndarray] = None     # current 2-D frame
        self._ring_items: list = []
        self._label_items: list = []
        self._pick_ring_item = None                # arc drawn from a profile click
        self._picked_r: Optional[float] = None
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6); root.setSpacing(8)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True); scroll.setFixedWidth(468)
        inner = QtWidgets.QWidget(); lv = QtWidgets.QVBoxLayout(inner)
        lv.setContentsMargins(2, 2, 2, 2); lv.setSpacing(8)
        scroll.setWidget(inner)

        def _br(w=30):
            b = QtWidgets.QPushButton("…"); b.setFixedWidth(w); return b

        def _frow(ed, slot):
            r = QtWidgets.QHBoxLayout(); r.setSpacing(4)
            r.addWidget(ed); b = _br(); b.clicked.connect(slot); r.addWidget(b); return r

        # ── Data card ──
        data = S.make_card("Data")
        self._path_ed = QtWidgets.QLineEdit()
        self._path_ed.setPlaceholderText("file, folder, or *.tif glob…")
        data.body.addLayout(_frow(self._path_ed, self._browse))
        self._h5_lbl = S.LabelRight("Dataset:")
        self._h5_combo = QtWidgets.QComboBox(); self._h5_combo.setEditable(True)
        self._h5_combo.setEditText("exchange/data")
        self._h5_combo.setToolTip("HDF5 datasets in the file (≥2-D). Auto-populated on selection.")
        self._h5_lbl.setVisible(False); self._h5_combo.setVisible(False)
        ds_row = QtWidgets.QHBoxLayout(); ds_row.setSpacing(4)
        ds_row.addWidget(self._h5_lbl); ds_row.addWidget(self._h5_combo, 1)
        data.body.addLayout(ds_row)
        self._path_ed.textChanged.connect(self._on_path_changed)
        self._path_ed.returnPressed.connect(self._load)
        self._h5_combo.currentIndexChanged.connect(
            lambda _=0: self._nframes and self._load())
        ld = QtWidgets.QPushButton("Browse folder…"); ld.clicked.connect(self._browse_folder)
        data.body.addWidget(ld)
        self._info_lbl = QtWidgets.QLabel("No data loaded.")
        self._info_lbl.setStyleSheet(f"color:{S.MUTED};font-size:10px"); self._info_lbl.setWordWrap(True)
        data.body.addWidget(self._info_lbl)
        lv.addWidget(data)

        # ── Frame navigator card ──
        self._nav_grp = S.make_card("Frame navigator")
        nv = QtWidgets.QHBoxLayout(); nv.setSpacing(4)
        self._prev_btn = QtWidgets.QPushButton("◀"); self._prev_btn.setFixedWidth(32)
        self._prev_btn.clicked.connect(lambda: self._set_frame(self._frame_spin.value() - 1))
        self._slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._slider.valueChanged.connect(self._set_frame)
        self._frame_spin = _NoScrollSpinBox(); self._frame_spin.setFixedWidth(64)
        self._frame_spin.valueChanged.connect(self._set_frame)
        self._next_btn = QtWidgets.QPushButton("▶"); self._next_btn.setFixedWidth(32)
        self._next_btn.clicked.connect(lambda: self._set_frame(self._frame_spin.value() + 1))
        self._nframes_lbl = QtWidgets.QLabel("/ 0")
        nv.addWidget(self._prev_btn); nv.addWidget(self._slider, 1)
        nv.addWidget(self._frame_spin); nv.addWidget(self._nframes_lbl); nv.addWidget(self._next_btn)
        self._nav_grp.body.addLayout(nv)
        self._nav_grp.setEnabled(False)
        lv.addWidget(self._nav_grp)

        # ── Projection card ──
        self._proj_grp = S.make_card("Projection")
        m_row = QtWidgets.QHBoxLayout(); m_row.setSpacing(8)
        m_row.addWidget(S.LabelRight("Method:"))
        self._proj_method = {}
        for meth in ("max", "sum", "average"):
            rb = QtWidgets.QRadioButton(meth.capitalize()); m_row.addWidget(rb)
            self._proj_method[meth] = rb
        self._proj_method["max"].setChecked(True)
        m_row.addStretch(1)
        self._proj_grp.body.addLayout(m_row)
        self._proj_axis = _NoScrollSpinBox(); self._proj_axis.setRange(0, 5); self._proj_axis.setValue(0)
        self._proj_axis.setToolTip("Axis to collapse. 0 = across the stack of frames.")
        ax = S.Form(); ax.row(("Axis (0=frames):", self._proj_axis))
        self._proj_grp.body.addLayout(ax)
        self._proj_btn = QtWidgets.QPushButton("Project stack")
        self._proj_btn.clicked.connect(self._project)
        self._frame_btn = QtWidgets.QPushButton("Back to frames")
        self._frame_btn.clicked.connect(lambda: self._set_frame(self._frame_spin.value()))
        self._proj_grp.body.addLayout(S.button_grid([self._proj_btn, self._frame_btn], 2))
        self._proj_grp.setEnabled(False)
        lv.addWidget(self._proj_grp)

        # ── Calibration card ──
        calc = S.make_card("Calibration  (optional)")
        self._calib_ed = QtWidgets.QLineEdit()
        self._calib_ed.setPlaceholderText("calibration.json / paramstest.txt / .poni…")
        calc.body.addLayout(_frow(self._calib_ed, self._browse_calib))
        self._calib_ed.returnPressed.connect(self._load_calibration)
        self._calib_lbl = QtWidgets.QLabel("No calibration loaded — using manual geometry / BC.")
        self._calib_lbl.setStyleSheet(f"color:{S.MUTED};font-size:10px")
        self._calib_lbl.setWordWrap(True)
        calc.body.addWidget(self._calib_lbl)
        lv.addWidget(calc)

        # ── Intensity range mask card ──
        imc = S.make_card("Intensity range  (radial integration)")
        self._imask_on = QtWidgets.QCheckBox("Exclude out-of-range pixels")
        self._imask_on.setToolTip(
            "Pixels ≤ min or > max are masked: drawn as a red overlay on the image\n"
            "and excluded from the radial integration (removes gaps / hot / overflow).")
        imc.body.addWidget(self._imask_on)
        self._imask_lo = _fspin(-1e9, 1e9, 1, 0.0)
        self._imask_lo.setToolTip("Pixels ≤ this value are masked (dead / gap / beam-stop).")
        self._imask_hi = _fspin(0, 5e9, 0, 1_048_575)
        self._imask_hi.setToolTip("Pixels > this value are masked (hot / overflow).")
        imc.body.addLayout(S.Form().row(("pixel ≤", self._imask_lo), ("pixel >", self._imask_hi)))
        for w in (self._imask_lo, self._imask_hi):
            w.setEnabled(False)
        self._imask_on.toggled.connect(
            lambda c: (self._imask_lo.setEnabled(c), self._imask_hi.setEnabled(c)))
        self._imask_on.toggled.connect(self._on_imask_changed)
        self._imask_lo.valueChanged.connect(self._on_imask_changed)
        self._imask_hi.valueChanged.connect(self._on_imask_changed)
        lv.addWidget(imc)

        # ── Ring simulation card ──
        ring = S.make_card("Ring simulation")
        self._mat = QtWidgets.QComboBox()
        for name in MATERIALS:
            self._mat.addItem(name)
        self._mat.addItem("Custom")
        ni_idx = self._mat.findText("Ni (FCC)")
        if ni_idx >= 0:
            self._mat.setCurrentIndex(ni_idx)
        self._mat.currentTextChanged.connect(self._on_material)
        ring.body.addLayout(S.Form().row(("Material:", self._mat)))

        self._a = _fspin(0.1, 100.0, 5, 5.4116, "Å"); self._b = _fspin(0.1, 100.0, 5, 5.4116, "Å")
        self._c = _fspin(0.1, 100.0, 5, 5.4116, "Å")
        self._al = _fspin(1.0, 179.0, 3, 90.0, "°"); self._be = _fspin(1.0, 179.0, 3, 90.0, "°")
        self._ga = _fspin(1.0, 179.0, 3, 90.0, "°")
        self._sg = _NoScrollSpinBox(); self._sg.setRange(1, 230); self._sg.setValue(225)
        latt = S.Form()
        latt.row(("a:", self._a), ("b:", self._b))
        latt.row(("c:", self._c), ("SG #:", self._sg))
        latt.row(("α:", self._al), ("β:", self._be))
        latt.row(("γ:", self._ga), (None, QtWidgets.QWidget()))
        ring.body.addLayout(latt)
        ring.body.addWidget(S.hline())

        self._wl = _fspin(0.001, 10.0, 5, DEFAULT_WAVELENGTH, "Å")
        self._lsd = _fspin(1e3, 1e8, 1, DEFAULT_LSD_UM, "µm")
        self._px = _fspin(1.0, 5000.0, 2, DEFAULT_PIXEL_UM, "µm")
        self._max2t = _fspin(1.0, 90.0, 1, 25.0, "°")
        geo = S.Form()
        geo.row(("λ:", self._wl), ("max 2θ:", self._max2t))
        geo.row(("Lsd:", self._lsd), ("px:", self._px))
        ring.body.addLayout(geo)

        self._bc_auto = QtWidgets.QCheckBox("Beam centre = image centre"); self._bc_auto.setChecked(True)
        ring.body.addWidget(self._bc_auto)
        self._bcy = _fspin(-1e5, 1e5, 2, DEFAULT_BC_Y, "px")
        self._bcz = _fspin(-1e5, 1e5, 2, DEFAULT_BC_Z, "px")
        self._bcy.setEnabled(False); self._bcz.setEnabled(False)
        self._bc_auto.toggled.connect(lambda c: (self._bcy.setEnabled(not c), self._bcz.setEnabled(not c)))
        ring.body.addLayout(S.Form().row(("BC_y:", self._bcy), ("BC_z:", self._bcz)))

        ctl = QtWidgets.QHBoxLayout()
        self._show_rings = QtWidgets.QCheckBox("Show rings"); self._show_rings.setChecked(True)
        self._show_rings.toggled.connect(self._set_rings_visible)
        self._show_labels = QtWidgets.QCheckBox("Labels"); self._show_labels.setChecked(True)
        self._show_labels.toggled.connect(self._set_rings_visible)
        ctl.addWidget(self._show_rings); ctl.addWidget(self._show_labels); ctl.addStretch(1)
        ring.body.addLayout(ctl)
        self._sim_btn = S.primary_btn("Simulate rings")
        self._sim_btn.clicked.connect(self._simulate)
        ring.body.addWidget(self._sim_btn)
        self._ring_info = QtWidgets.QPlainTextEdit(); self._ring_info.setReadOnly(True)
        self._ring_info.setMaximumHeight(140)
        self._ring_info.setStyleSheet("font-family:monospace;font-size:10px")
        ring.body.addWidget(self._ring_info)
        lv.addWidget(ring)
        lv.addStretch(1)
        root.addWidget(scroll)

        # Right: image (top) + radial-integration plot (bottom) in a splitter.
        right = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self._viewer = PickableImageViewer()
        self._viewer.bcPicked.connect(self._on_bc_picked)
        self._viewer.ringFitBC.connect(self._on_ring_fit_bc)
        right.addWidget(self._viewer)

        # Radial integration (azimuthal average around the beam centre).
        self._profile_view = ProfileViewer()
        self._profile_view.radiusClicked.connect(self._on_radius_clicked)
        ptb = self._profile_view._toolbar_layout
        self._rad_r_bin = _fspin(0.1, 20.0, 2, 1.0, "px"); self._rad_r_bin.setFixedWidth(86)
        self._rad_r_bin.setToolTip("Radial bin size for the azimuthal average.")
        self._rad_r_bin.valueChanged.connect(self._on_rad_param_changed)
        self._rad_auto = QtWidgets.QCheckBox("Auto"); self._rad_auto.setChecked(True)
        self._rad_auto.setToolTip("Recompute the radial integration when the beam "
                                  "centre or frame changes.")
        self._rad_btn = QtWidgets.QPushButton("Integrate")
        self._rad_btn.clicked.connect(self._radial_integrate)
        ptb.insertWidget(3, self._rad_btn)
        ptb.insertWidget(3, self._rad_auto)
        ptb.insertWidget(3, self._rad_r_bin)
        ptb.insertWidget(3, QtWidgets.QLabel("  R bin:"))
        right.addWidget(self._profile_view)
        right.setStretchFactor(0, 3); right.setStretchFactor(1, 1)
        root.addWidget(right, stretch=1)

        # Recompute rings / radial profile when the beam centre is edited manually.
        self._bcy.valueChanged.connect(self._on_bc_changed)
        self._bcz.valueChanged.connect(self._on_bc_changed)

        self._on_material(self._mat.currentText())
        # Pre-fill the default test dataset (auto-populates the HDF5 dataset dropdown)
        # and load it if present, so the tab is usable out-of-the-box without a Load button.
        self._path_ed.setText(DEFAULT_NICKEL_H5)
        if Path(DEFAULT_NICKEL_H5).exists():
            self._load()

    # ── Material dropdown ─────────────────────────────────────────

    def _on_material(self, name: str):
        custom = (name == "Custom")
        for w in (self._a, self._b, self._c, self._al, self._be, self._ga, self._sg):
            w.setEnabled(custom)
        if not custom and name in MATERIALS:
            m = MATERIALS[name]
            self._a.setValue(m["a"]); self._b.setValue(m["b"]); self._c.setValue(m["c"])
            self._al.setValue(m["alpha"]); self._be.setValue(m["beta"]); self._ga.setValue(m["gamma"])
            self._sg.setValue(m["sg"])

    # ── Loading ───────────────────────────────────────────────────

    def _browse(self):
        p = _browse(self, "Open data",
                    "Data (*.tif *.tiff *.h5 *.hdf5 *.hdf *.nxs *.ge*);;All (*)")
        if p:
            self._path_ed.setText(p); self._load()

    def _on_path_changed(self, p: str):
        h5 = is_h5(p)
        self._h5_lbl.setVisible(h5); self._h5_combo.setVisible(h5)
        if h5 and Path(p).exists():
            self._populate_h5_datasets(p)

    def _populate_h5_datasets(self, path: str):
        """List all ≥2-D datasets in the file into the dataset combo."""
        try:
            import h5py
            items = []

            def visit(name, obj):
                if isinstance(obj, h5py.Dataset) and obj.ndim >= 2:
                    items.append((name, obj.shape))

            with h5py.File(path, "r") as f:
                f.visititems(visit)
            if not items:
                return
            keep = self._h5_combo.currentText().strip()
            self._h5_combo.blockSignals(True)
            self._h5_combo.clear()
            for name, shape in items:
                self._h5_combo.addItem(f"{name}   {tuple(shape)}", name)
            # Restore a sensible selection: prior text if still present, else first 3-D
            idx = next((i for i in range(self._h5_combo.count())
                        if self._h5_combo.itemData(i) == keep), -1)
            if idx < 0:
                idx = next((i for i, (n, s) in enumerate(items) if len(s) >= 3), 0)
            self._h5_combo.setCurrentIndex(idx)
            self._h5_combo.blockSignals(False)
            self._info_lbl.setText(f"HDF5: {len(items)} dataset(s) found — pick one and Load.")
        except Exception as e:
            self._info_lbl.setText(f"Could not list HDF5 datasets: {e}")

    def _h5_dataset(self) -> str:
        """Current dataset path. List items read 'name   (shape)'; manual entries
        are just 'name' — splitting on the separator handles both."""
        return self._h5_combo.currentText().split("   ")[0].strip() or "exchange/data"

    def _browse_folder(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Select folder of frames")
        if d:
            self._path_ed.setText(d); self._load()

    def _reset_storage(self):
        self._stack = self._paths = self._h5 = None
        self._nframes = 0

    def _load(self):
        raw = self._path_ed.text().strip()
        if not raw:
            return
        try:
            self._reset_storage()
            p = Path(raw)
            if p.is_dir() or any(ch in raw for ch in "*?"):
                paths = self._collect_paths(raw)
                if not paths:
                    QtWidgets.QMessageBox.warning(self, "Empty", "No frames found."); return
                self._paths = paths; self._nframes = len(paths)
                kind = f"folder/glob ({self._nframes} files)"
            elif is_h5(raw):
                import h5py
                dset = self._h5_dataset()
                with h5py.File(raw, "r") as f:
                    if dset not in f:
                        raise KeyError(f"dataset '{dset}' not in file")
                    shape = f[dset].shape
                n = shape[0] if len(shape) >= 3 else 1
                self._h5 = (raw, dset, n); self._nframes = n
                kind = f"HDF5 [{dset}] {shape}"
            else:
                import tifffile
                arr = np.asarray(tifffile.imread(raw))
                if arr.ndim >= 3:
                    self._stack = arr; self._nframes = arr.shape[0]
                    kind = f"TIFF stack {arr.shape}"
                else:
                    self._stack = arr[None, ...]; self._nframes = 1
                    kind = f"TIFF {arr.shape}"
            self._info_lbl.setText(f"Loaded: {kind}")
            self._setup_navigator()
            self._set_frame(0, autorange=True)
            self._autofill_imask_max()
        except Exception as e:
            import traceback
            QtWidgets.QMessageBox.critical(self, "Load error", traceback.format_exc()[:500])

    def _collect_paths(self, raw: str) -> list:
        p = Path(raw)
        if p.is_dir():
            out = []
            for ext in ("*.tif", "*.tiff", "*.h5", "*.hdf5", "*.ge*", "*.cbf", "*.edf"):
                out.extend(sorted(p.glob(ext)))
            return [str(x) for x in out]
        return sorted(_glob.glob(raw))

    def _setup_navigator(self):
        multi = self._nframes > 1
        self._nav_grp.setEnabled(multi)
        self._proj_grp.setEnabled(multi)
        for w in (self._slider, self._frame_spin):
            w.blockSignals(True)
        self._slider.setRange(0, max(0, self._nframes - 1))
        self._frame_spin.setRange(0, max(0, self._nframes - 1))
        self._nframes_lbl.setText(f"/ {self._nframes - 1}")
        for w in (self._slider, self._frame_spin):
            w.blockSignals(False)

    def _get_frame(self, i: int) -> np.ndarray:
        i = max(0, min(i, self._nframes - 1))
        if self._stack is not None:
            return np.asarray(self._stack[i], dtype=np.float32)
        if self._paths is not None:
            arr = _load_image(self._paths[i]).astype(np.float32)
            return arr[0] if arr.ndim == 3 else arr   # guard multi-page file in a folder
        if self._h5 is not None:
            path, dset, _ = self._h5
            return _load_image(path, data_loc=dset, frame=i).astype(np.float32)
        raise RuntimeError("No data loaded")

    def _full_stack(self) -> np.ndarray:
        """Return the full N-D stack for projection (loads on demand)."""
        if self._stack is not None:
            return np.asarray(self._stack)
        if self._h5 is not None:
            import h5py
            path, dset, _ = self._h5
            with h5py.File(path, "r") as f:
                return np.asarray(f[dset][()])
        if self._paths is not None:
            frames = []
            for i in range(self._nframes):
                a = _load_image(self._paths[i]).astype(np.float32)
                frames.append(a[0] if a.ndim == 3 else a)
            return np.stack(frames, axis=0)
        raise RuntimeError("No data loaded")

    def _project(self):
        if self._nframes <= 1:
            QtWidgets.QMessageBox.warning(self, "No stack", "Projection needs a stack."); return
        method = next(m for m, b in self._proj_method.items() if b.isChecked())
        axis = self._proj_axis.value()
        try:
            self._info_lbl.setText("Loading stack for projection…")
            QtWidgets.QApplication.processEvents()
            data = self._full_stack()
            if axis >= data.ndim:
                QtWidgets.QMessageBox.critical(
                    self, "Bad axis", f"Axis {axis} invalid for {data.ndim}-D data."); return
            fn = {"max": np.max, "sum": np.sum, "average": np.mean}[method]
            proj = np.squeeze(fn(data, axis=axis))
            if proj.ndim != 2:
                QtWidgets.QMessageBox.critical(
                    self, "Not 2-D", f"Result is {proj.ndim}-D after projecting axis {axis}. "
                    "Pick an axis that leaves a 2-D image."); return
            self._cur = proj.astype(np.float32)
            self._viewer.set_image(self._cur)
            if self._bc_auto.isChecked():
                NZ, NY = self._cur.shape
                for w, v in ((self._bcy, NY / 2.0), (self._bcz, NZ / 2.0)):
                    w.blockSignals(True); w.setValue(v); w.blockSignals(False)
            if getattr(self, "_rings", None):
                self._redraw_rings()
            self._update_intensity_overlay()
            if self._rad_auto.isChecked():
                self._radial_integrate()
            self._info_lbl.setText(
                f"{method.capitalize()} projection (axis {axis}) → {proj.shape}  "
                f"[{np.nanmin(proj):.3g}, {np.nanmax(proj):.3g}]")
        except Exception:
            import traceback
            QtWidgets.QMessageBox.critical(self, "Projection error", traceback.format_exc()[:500])

    def _set_frame(self, i: int, autorange: bool = False):
        if self._nframes == 0:
            return
        i = max(0, min(int(i), self._nframes - 1))
        for w in (self._slider, self._frame_spin):
            w.blockSignals(True); w.setValue(i); w.blockSignals(False)
        self._cur = self._get_frame(i)
        # autorange only on a fresh load — navigating a stack keeps the zoom/pan.
        self._viewer.set_image(self._cur, autorange=autorange)
        if self._bc_auto.isChecked():
            NZ, NY = self._cur.shape
            for w, v in ((self._bcy, NY / 2.0), (self._bcz, NZ / 2.0)):
                w.blockSignals(True); w.setValue(v); w.blockSignals(False)
        # redraw existing rings on the new frame
        if self._ring_items or self._label_items:
            self._redraw_rings()
        self._redraw_picked_ring()
        self._update_intensity_overlay()
        if self._rad_auto.isChecked():
            self._radial_integrate()

    # ── Ring simulation ───────────────────────────────────────────

    def _simulate(self):
        if self._cur is None:
            QtWidgets.QMessageBox.warning(self, "No image", "Load data first."); return
        try:
            lattice = dict(a=self._a.value(), b=self._b.value(), c=self._c.value(),
                           alpha=self._al.value(), beta=self._be.value(), gamma=self._ga.value())
            rings = simulate_rings(lattice, self._sg.value(), self._wl.value(),
                                   self._lsd.value(), self._px.value(), self._max2t.value())
        except Exception as e:
            import traceback
            QtWidgets.QMessageBox.critical(self, "Simulation error", traceback.format_exc()[:500]); return
        self._rings = rings
        self._redraw_rings()
        if self._rad_auto.isChecked():
            self._radial_integrate()
        else:
            self._refresh_profile_markers()
        lines = [f"{len(rings)} rings  (material: {self._mat.currentText()})",
                 f"{'hkl':>10}  {'2θ(°)':>7}  {'d(Å)':>7}  {'R(px)':>8}"]
        for r in rings:
            h, k, l = r["hkl"]
            lines.append(f"{str((h,k,l)):>10}  {r['two_theta_deg']:7.3f}  "
                         f"{r['d_spacing']:7.4f}  {r['radius_px']:8.1f}")
        self._ring_info.setPlainText("\n".join(lines))

    def _clear_rings(self):
        for it in self._ring_items + self._label_items:
            self._viewer._iv.removeItem(it)
        self._ring_items.clear(); self._label_items.clear()

    def _redraw_rings(self):
        self._clear_rings()
        rings = getattr(self, "_rings", None)
        if not rings or self._cur is None:
            return
        bc_y, bc_z = self._bcy.value(), self._bcz.value()
        NZ, NY = self._cur.shape
        max_r = math.hypot(NY, NZ)
        th = np.linspace(0, 2 * math.pi, 400)
        pen = pg.mkPen("#f0c060", width=1.3, style=QtCore.Qt.DotLine)
        vis_r = self._show_rings.isChecked()
        vis_l = self._show_labels.isChecked() and vis_r
        for r in rings:
            rad = r["radius_px"]
            if not (0 < rad < max_r):
                continue
            item = pg.PlotDataItem(bc_y + rad * np.cos(th), bc_z + rad * np.sin(th), pen=pen)
            item.setVisible(vis_r)
            self._viewer._iv.addItem(item); self._ring_items.append(item)
            h, k, l = r["hkl"]
            txt = pg.TextItem(f"{h}{k}{l}", color="#f0c060", anchor=(0.5, 1.0))
            txt.setPos(bc_y, bc_z - rad)
            txt.setVisible(vis_l)
            self._viewer._iv.addItem(txt); self._label_items.append(txt)
        # beam-centre marker
        bc = pg.ScatterPlotItem([bc_y], [bc_z], symbol="+", size=16,
                                pen=pg.mkPen("#00cfff", width=2), brush=pg.mkBrush(0, 0, 0, 0))
        bc.setVisible(vis_r)
        self._viewer._iv.addItem(bc); self._ring_items.append(bc)

    def _set_rings_visible(self, *_):
        vis_r = self._show_rings.isChecked()
        vis_l = self._show_labels.isChecked() and vis_r
        for it in self._ring_items:
            it.setVisible(vis_r)
        for it in self._label_items:
            it.setVisible(vis_l)

    # ── Beam-centre picking / radial integration ──────────────────

    def _on_bc_picked(self, bc_y, bc_z):
        """Single-click BC pick from the image (PickableImageViewer)."""
        self._bc_auto.setChecked(False)
        self._bcy.setValue(bc_y); self._bcz.setValue(bc_z)   # triggers _on_bc_changed

    def _on_ring_fit_bc(self, bc_y, bc_z, r_px):
        """BC from a 3+ point circle fit on a ring (PickableImageViewer)."""
        self._bc_auto.setChecked(False)
        self._bcy.setValue(bc_y); self._bcz.setValue(bc_z)   # triggers _on_bc_changed

    def _on_bc_changed(self, *_):
        """Beam centre edited (manually or by a pick) — refresh overlays/plot."""
        if getattr(self, "_rings", None):
            self._redraw_rings()
        self._redraw_picked_ring()
        if self._rad_auto.isChecked():
            self._radial_integrate()

    def _on_radius_clicked(self, r_px: float):
        """A radius was clicked on the profile — draw its ring on the image."""
        self._picked_r = float(r_px)
        self._redraw_picked_ring()
        self._info_lbl.setText(f"Picked radius: {r_px:.1f} px  (magenta ring)")

    def _redraw_picked_ring(self):
        """(Re)draw the click-picked ring (magenta) about the current beam centre."""
        if self._pick_ring_item is not None:
            self._viewer._iv.removeItem(self._pick_ring_item)
            self._pick_ring_item = None
        r = self._picked_r
        if r is None or self._cur is None:
            return
        bc_y, bc_z = self._bcy.value(), self._bcz.value()
        th = np.linspace(0, 2 * math.pi, 512)
        self._pick_ring_item = pg.PlotDataItem(
            bc_y + r * np.cos(th), bc_z + r * np.sin(th),
            pen=pg.mkPen("#ff30ff", width=1.8))
        self._viewer._iv.addItem(self._pick_ring_item)

    def _on_rad_param_changed(self, *_):
        if self._rad_auto.isChecked():
            self._radial_integrate()

    def _refresh_profile_markers(self):
        rings = getattr(self, "_rings", None)
        if rings:
            self._profile_view.set_ring_markers(
                [r["radius_px"] for r in rings],
                self._lsd.value(), self._px.value(), self._wl.value())
        else:
            self._profile_view.set_ring_markers([])

    def _radial_integrate(self):
        """Azimuthal average of the current frame about the beam centre."""
        if self._cur is None:
            return
        r_axis, prof = self._radial_profile(
            self._cur, self._bcy.value(), self._bcz.value(), self._rad_r_bin.value(),
            mask=self._intensity_bad_mask(self._cur))
        self._profile_view.set_profile(
            r_axis, prof, wavelength_A=self._wl.value(),
            lsd_um=self._lsd.value(), px_um=self._px.value())
        self._refresh_profile_markers()

    @staticmethod
    def _radial_profile(img: np.ndarray, bc_y: float, bc_z: float,
                        r_bin: float = 1.0, mask: Optional[np.ndarray] = None):
        """Mean intensity vs radius (px) about (bc_y, bc_z).

        bc_y is the column (Y/x) and bc_z the row (Z/y); image shape is (NZ, NY).
        ``mask`` (bool, True = exclude) drops pixels from the average; together with
        non-finite pixels these are ignored. Returns (r_axis_px, profile), NaN in
        empty bins.
        """
        NZ, NY = img.shape
        zz, yy = np.indices((NZ, NY))
        r = np.hypot(yy - bc_y, zz - bc_z)
        r_bin = max(float(r_bin), 1e-6)
        nbins = max(1, int(r.max() / r_bin) + 1)
        which = np.minimum((r / r_bin).astype(np.int64), nbins - 1).ravel()
        vals = img.ravel()
        good = np.isfinite(vals)
        if mask is not None:
            good &= ~mask.ravel()
        sums = np.bincount(which[good], weights=vals[good], minlength=nbins)
        counts = np.bincount(which[good], minlength=nbins)
        prof = np.full(nbins, np.nan, dtype=np.float64)
        nz = counts > 0
        prof[nz] = sums[nz] / counts[nz]
        r_axis = (np.arange(nbins) + 0.5) * r_bin
        return r_axis, prof

    # ── Calibration file / intensity mask ─────────────────────────

    def _browse_calib(self):
        p = _browse(self, "Open calibration file",
                    "Calibration (*.json *.poni *.txt);;All (*)")
        if p:
            self._calib_ed.setText(p)
            self._load_calibration()

    def _load_calibration(self):
        """Read geometry (BC, Lsd, pixel size, wavelength) from a MIDAS paramstest,
        pyFAI .poni, or calibration.json file and apply it to the ring overlay and
        the radial integration."""
        path = self._calib_ed.text().strip()
        if not path or not Path(path).exists():
            QtWidgets.QMessageBox.warning(self, "No file", "Select a calibration file first.")
            return
        try:
            geo = read_geometry(path)
            wl, lsd, px = geo["wavelength_A"], geo["Lsd_um"], geo["px_um"]
            bcy, bcz = geo["BC_y"], geo["BC_z"]
            if all(v is None for v in geo.values()):
                self._calib_lbl.setText("No recognised geometry in file.")
                return
            self._bc_auto.setChecked(False)   # geometry now comes from the file
            for w, v in ((self._wl, wl), (self._lsd, lsd), (self._px, px),
                         (self._bcy, bcy), (self._bcz, bcz)):
                if v is not None:
                    w.blockSignals(True); w.setValue(float(v)); w.blockSignals(False)
            parts = []
            if lsd is not None: parts.append(f"Lsd={float(lsd)/1000:.2f} mm")
            if bcy is not None and bcz is not None:
                parts.append(f"BC=({float(bcy):.1f}, {float(bcz):.1f})")
            if wl is not None: parts.append(f"λ={float(wl):.5g} Å")
            if px is not None: parts.append(f"px={float(px):.4g} µm")
            self._calib_lbl.setText(f"Loaded {Path(path).suffix or 'file'} — " + "  ".join(parts))
            self._redraw_rings()
            if self._rad_auto.isChecked():
                self._radial_integrate()
            else:
                self._refresh_profile_markers()
        except Exception:
            import traceback
            QtWidgets.QMessageBox.critical(self, "Calibration load error",
                                           traceback.format_exc()[:500])

    def _autofill_imask_max(self):
        """Default the intensity-mask upper bound to the 99.9th percentile of the frame."""
        if self._cur is None:
            return
        fin = self._cur[np.isfinite(self._cur)]
        if not fin.size:
            return
        p999 = float(np.percentile(fin, 99.9))
        self._imask_hi.blockSignals(True)
        self._imask_hi.setValue(p999)
        self._imask_hi.blockSignals(False)
        if self._imask_on.isChecked():
            self._update_intensity_overlay()
            if self._rad_auto.isChecked():
                self._radial_integrate()

    def _intensity_bad_mask(self, img: np.ndarray) -> Optional[np.ndarray]:
        """Boolean mask (True = excluded) from the intensity-range controls, or None."""
        if not self._imask_on.isChecked() or img is None:
            return None
        lo, hi = self._imask_lo.value(), self._imask_hi.value()
        bad = ~np.isfinite(img)
        if lo > -1e9:
            bad |= (img <= lo)
        if hi > 0:
            bad |= (img > hi)
        return bad

    def _update_intensity_overlay(self):
        bad = self._intensity_bad_mask(self._cur)
        if bad is None or not bad.any():
            self._viewer.clear_overlay()
        else:
            self._viewer.set_mask_overlay(bad)

    def _on_imask_changed(self, *_):
        self._update_intensity_overlay()
        if self._rad_auto.isChecked():
            self._radial_integrate()
