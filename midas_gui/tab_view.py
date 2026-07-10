"""Tab 0 — Data Viewer.

Plot single/stacked TIFF, HDF5 (2-D or 3-D), or a folder/glob of frames, with a
frame navigator for stacks.  Define a material (dropdown of common calibrants and
metals) or a custom lattice + space group + wavelength + Lsd + pixel size, and
overlay the simulated Debye-Scherrer ring positions on the image.

This tab is purely for inspection — it produces no shared state for other tabs.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

import numpy as np
from PyQt5 import QtCore, QtWidgets
import pyqtgraph as pg

from midas_gui.constants import (MATERIALS, DEFAULT_WAVELENGTH, DEFAULT_PIXEL_UM,
                           DEFAULT_LSD_UM, DEFAULT_BC_Y, DEFAULT_BC_Z,
                           DEFAULT_NICKEL_H5)
from midas_gui.helpers import (_fspin, _NoScrollSpinBox, _browse,
                         simulate_rings, read_geometry, _NoScrollComboBox,
                         make_kedge_label)
from midas_gui.widgets import PickableImageViewer, ProfileViewer, DataLoaderPanel
from midas_gui import style as S


class DataViewerTab(QtWidgets.QWidget):
    pushGeometry = QtCore.pyqtSignal(dict)   # λ/px/Lsd/BC → Calibrate tab

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cur: Optional[np.ndarray] = None     # current 2-D frame (corrected, for display)
        self._disp_shape = None                    # last displayed shape (fresh-load detection)
        self._ring_items: list = []
        self._label_items: list = []
        self._pick_ring_item = None                # arc drawn from a profile click
        self._picked_r: Optional[float] = None
        self._build_ui()
        self._loader.set_path(DEFAULT_NICKEL_H5, dataset="exchange/data")

    def set_mask_from_tab1(self, mask):
        self._loader.set_tab1_mask(mask)

    def get_geometry(self) -> dict:
        """Current manual geometry — λ (Å), pixel (µm), Lsd (µm), beam centre (px)."""
        return {
            "wavelength_A": self._wl.value(),
            "pxY": self._px.value(),
            "Lsd": self._lsd.value(),
            "BC_y": self._bcy.value(),
            "BC_z": self._bcz.value(),
        }

    # ── UI ────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6); root.setSpacing(0)
        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        split.setChildrenCollapsible(False); split.setHandleWidth(6)
        root.addWidget(split); self._hsplit = split

        # ── LEFT: data loader (stack mode) ──
        self._loader = DataLoaderPanel(mode="stack")
        self._loader.setMinimumWidth(200)
        self._loader.dataChanged.connect(self._on_loader_data)
        self._loader.fieldsChanged.connect(self._on_fields_changed)
        split.addWidget(self._loader)

        # ── MIDDLE: parameters ──
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True); scroll.setMinimumWidth(260)
        inner = QtWidgets.QWidget(); lv = QtWidgets.QVBoxLayout(inner)
        lv.setContentsMargins(2, 2, 2, 2); lv.setSpacing(8)
        scroll.setWidget(inner)

        def _br(w=30):
            b = QtWidgets.QPushButton("…"); b.setFixedWidth(w); return b

        def _frow(ed, slot):
            r = QtWidgets.QHBoxLayout(); r.setSpacing(4)
            r.addWidget(ed); b = _br(); b.clicked.connect(slot); r.addWidget(b); return r

        self._info_lbl = QtWidgets.QLabel("")
        self._info_lbl.setStyleSheet(f"color:{S.MUTED};font-size:10px"); self._info_lbl.setWordWrap(True)

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
        self._proj_axis.setFixedWidth(56)
        self._proj_skip = _NoScrollSpinBox(); self._proj_skip.setRange(0, 1000000); self._proj_skip.setValue(1)
        self._proj_skip.setFixedWidth(72)
        self._proj_skip.setToolTip(
            "Ignore this many leading frames before projecting\n"
            "(1 = skip the first frame, 4 = skip the first four).")
        ax = S.Form()
        ax.row(("Axis (0=frames):", self._proj_axis), ("Skip frames:", self._proj_skip))
        self._proj_grp.body.addLayout(ax)
        self._proj_btn = QtWidgets.QPushButton("Project stack")
        self._proj_btn.clicked.connect(self._project)
        self._frame_btn = QtWidgets.QPushButton("Back to frames")
        self._frame_btn.clicked.connect(self._on_loader_data)
        self._proj_grp.body.addLayout(S.button_grid([self._proj_btn, self._frame_btn], 2))
        self._proj_grp.body.addWidget(self._info_lbl)
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
        self._imask_lo.setFixedWidth(84)
        self._imask_hi = _fspin(0, 5e9, 0, 1_048_575)
        self._imask_hi.setToolTip("Pixels > this value are masked (hot / overflow).")
        self._imask_hi.setFixedWidth(84)
        _imr = QtWidgets.QHBoxLayout(); _imr.setSpacing(3); _imr.setContentsMargins(0, 0, 0, 0)
        _imr.addWidget(QtWidgets.QLabel("pixel ≤")); _imr.addWidget(self._imask_lo)
        _imr.addSpacing(8)
        _imr.addWidget(QtWidgets.QLabel("pixel >")); _imr.addWidget(self._imask_hi)
        _imr.addStretch(1)
        imc.body.addLayout(_imr)
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
        self._mat = _NoScrollComboBox()
        self._mat.setMaximumWidth(150)
        for name in MATERIALS:
            self._mat.addItem(name)
        self._mat.addItem("Custom")
        ni_idx = self._mat.findText("Ni (FCC)")
        if ni_idx >= 0:
            self._mat.setCurrentIndex(ni_idx)
        self._mat.currentTextChanged.connect(self._on_material)
        ring.body.addLayout(S.Form().row(("Material:", self._mat)))

        _LW, _AW = 78, 66     # compact lattice / angle-SG cell widths
        self._a = _fspin(0.1, 100.0, 5, 5.4116); self._a.setFixedWidth(_LW)
        self._b = _fspin(0.1, 100.0, 5, 5.4116); self._b.setFixedWidth(_LW)
        self._c = _fspin(0.1, 100.0, 5, 5.4116); self._c.setFixedWidth(_LW)
        self._al = _fspin(1.0, 179.0, 3, 90.0); self._al.setFixedWidth(_AW)
        self._be = _fspin(1.0, 179.0, 3, 90.0); self._be.setFixedWidth(_AW)
        self._ga = _fspin(1.0, 179.0, 3, 90.0); self._ga.setFixedWidth(_AW)
        self._sg = _NoScrollSpinBox(); self._sg.setRange(1, 230); self._sg.setValue(225)
        self._sg.setFixedWidth(_AW)
        self._cubic = QtWidgets.QCheckBox("Cubic (a=b=c, α=β=γ=90°)")
        self._cubic.setToolTip("Enter only a — b and c mirror it and all angles are fixed at 90°.")
        self._cubic.toggled.connect(self._apply_lattice_enabled)
        self._a.valueChanged.connect(self._on_a_changed)
        latt = S.Form()
        latt.row(("a:", self._a), ("b:", self._b), ("c:", self._c))
        latt.row(("α:", self._al), ("β:", self._be), ("γ:", self._ga))
        latt.row(("SG #:", self._sg))
        ring.body.addLayout(latt)
        ring.body.addWidget(self._cubic)
        ring.body.addWidget(S.hline())

        self._wl = _fspin(0.001, 10.0, 5, DEFAULT_WAVELENGTH, "Å")
        self._lsd = _fspin(1e3, 1e8, 1, DEFAULT_LSD_UM, "µm")
        self._lsd.setLocale(QtCore.QLocale(QtCore.QLocale.English, QtCore.QLocale.UnitedStates))
        self._lsd.setGroupSeparatorShown(True)   # show "121,000.0" thousands separators
        self._lsd.setFixedWidth(150)
        self._px = _fspin(1.0, 5000.0, 2, DEFAULT_PIXEL_UM, "µm")
        self._max2t = _fspin(1.0, 90.0, 1, 25.0, "°")
        geo = S.Form()
        geo.row((make_kedge_label(self._wl, "λ:"), self._wl), ("max 2θ:", self._max2t))
        geo.row(("Lsd:", self._lsd), ("px:", self._px))
        ring.body.addLayout(geo)

        self._bc_auto = QtWidgets.QCheckBox("Beam centre = image centre"); self._bc_auto.setChecked(True)
        ring.body.addWidget(self._bc_auto)
        self._bcy = _fspin(-1e5, 1e5, 2, DEFAULT_BC_Y, "px")
        self._bcz = _fspin(-1e5, 1e5, 2, DEFAULT_BC_Z, "px")
        self._bcy.setEnabled(False); self._bcz.setEnabled(False)
        self._bc_auto.toggled.connect(lambda c: (self._bcy.setEnabled(not c), self._bcz.setEnabled(not c)))
        ring.body.addLayout(S.Form().row(("BC_y:", self._bcy), ("BC_z:", self._bcz)))

        # Send λ / pixel / Lsd / beam-centre to the Calibrate tab (seed values).
        self._to_calib_btn = QtWidgets.QPushButton("→ Send geometry to Calibrate")
        self._to_calib_btn.setToolTip(
            "Copy λ, pixel size, Lsd and beam centre from here into the Calibrate "
            "tab's detector + seed fields.")
        self._to_calib_btn.clicked.connect(
            lambda: self.pushGeometry.emit(self.get_geometry()))
        ring.body.addWidget(self._to_calib_btn)

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
        split.addWidget(scroll)

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
        right.setMinimumWidth(320)
        split.addWidget(right)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 0); split.setStretchFactor(2, 1)
        split.setSizes([286, 361, 950])

        # Recompute rings / radial profile when the beam centre is edited manually.
        self._bcy.valueChanged.connect(self._on_bc_changed)
        self._bcz.valueChanged.connect(self._on_bc_changed)

        self._on_material(self._mat.currentText())

    # ── Material dropdown ─────────────────────────────────────────

    def _on_material(self, name: str):
        if name != "Custom" and name in MATERIALS:
            m = MATERIALS[name]
            for w, k in ((self._a, "a"), (self._b, "b"), (self._c, "c"),
                         (self._al, "alpha"), (self._be, "beta"), (self._ga, "gamma")):
                w.blockSignals(True); w.setValue(m[k]); w.blockSignals(False)
            self._sg.setValue(m["sg"])
        self._apply_lattice_enabled()

    def _apply_lattice_enabled(self, *_):
        """Enable lattice fields only for a custom material; under 'Cubic' only a is
        editable (b, c mirror a and the angles are fixed at 90°)."""
        custom = self._mat.currentText() == "Custom"
        self._cubic.setEnabled(custom)
        cubic = custom and self._cubic.isChecked()
        self._a.setEnabled(custom); self._sg.setEnabled(custom)
        for w in (self._b, self._c, self._al, self._be, self._ga):
            w.setEnabled(custom and not cubic)
        if cubic:
            self._sync_cubic()

    def _sync_cubic(self):
        v = self._a.value()
        for w in (self._b, self._c):
            w.blockSignals(True); w.setValue(v); w.blockSignals(False)
        for w in (self._al, self._be, self._ga):
            w.blockSignals(True); w.setValue(90.0); w.blockSignals(False)

    def _on_a_changed(self, *_):
        if self._cubic.isEnabled() and self._cubic.isChecked():
            self._sync_cubic()

    # ── Loading ───────────────────────────────────────────────────

    def _on_loader_data(self):
        """Data loaded or frame changed in the loader — refresh the display,
        applying dark/bright/background corrections."""
        raw = self._loader.current_frame()
        if raw is None:
            return
        self._proj_grp.setEnabled(self._loader.n_frames() > 1)
        fresh = (self._disp_shape != raw.shape)
        self._disp_shape = raw.shape
        self._cur = self._loader.corrected(raw)
        self._viewer.set_image(self._cur, autorange=fresh)
        if fresh:
            self._autofill_imask_max()
        if self._bc_auto.isChecked():
            NZ, NY = self._cur.shape
            for w, v in ((self._bcy, NY / 2.0), (self._bcz, NZ / 2.0)):
                w.blockSignals(True); w.setValue(v); w.blockSignals(False)
        if self._ring_items or self._label_items:
            self._redraw_rings()
        self._redraw_picked_ring()
        self._update_intensity_overlay()
        if self._rad_auto.isChecked():
            self._radial_integrate()

    def _on_fields_changed(self):
        """Dark/bright/background/mask changed — recompute the corrected image and
        refresh the overlay + radial integration (no autorange, no autofill)."""
        raw = self._loader.current_frame()
        if raw is None:
            return
        self._cur = self._loader.corrected(raw)
        self._viewer.set_image(self._cur, autorange=False)
        self._update_intensity_overlay()
        if self._rad_auto.isChecked():
            self._radial_integrate()

    def _project(self):
        if self._loader.n_frames() <= 1:
            QtWidgets.QMessageBox.warning(self, "No stack", "Projection needs a stack."); return
        method = next(m for m, b in self._proj_method.items() if b.isChecked())
        axis = self._proj_axis.value()
        skip = self._proj_skip.value()
        try:
            self._info_lbl.setText("Loading stack for projection…")
            QtWidgets.QApplication.processEvents()
            data = self._loader.full_stack()
            if axis >= data.ndim:
                QtWidgets.QMessageBox.critical(
                    self, "Bad axis", f"Axis {axis} invalid for {data.ndim}-D data."); return
            # Drop the first `skip` frames along the stack (frame) axis.
            if skip > 0:
                if skip >= data.shape[0]:
                    QtWidgets.QMessageBox.warning(
                        self, "Too many skipped",
                        f"Skip frames ({skip}) ≥ stack size ({data.shape[0]}). "
                        "Reduce the skip count."); return
                data = data[skip:]
            fn = {"max": np.max, "sum": np.sum, "average": np.mean}[method]
            proj = np.squeeze(fn(data, axis=axis))
            if proj.ndim != 2:
                QtWidgets.QMessageBox.critical(
                    self, "Not 2-D", f"Result is {proj.ndim}-D after projecting axis {axis}. "
                    "Pick an axis that leaves a 2-D image."); return
            self._cur = self._loader.corrected(proj.astype(np.float32))
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
                f"{method.capitalize()} projection (axis {axis}"
                f"{f', skipped {skip}' if skip else ''}) → {proj.shape}  "
                f"[{np.nanmin(proj):.3g}, {np.nanmax(proj):.3g}]")
        except Exception:
            import traceback
            QtWidgets.QMessageBox.critical(self, "Projection error", traceback.format_exc()[:500])

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
            mask=self._combined_bad_mask(self._cur))
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
        """Default the intensity-mask upper bound to max(99.99th percentile, 100000)."""
        if self._cur is None:
            return
        fin = self._cur[np.isfinite(self._cur)]
        if not fin.size:
            return
        hi = max(float(np.percentile(fin, 99.99)), 100_000.0)
        self._imask_hi.blockSignals(True)
        self._imask_hi.setValue(hi)
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

    def _combined_bad_mask(self, img):
        """Union of the intensity-range mask and the loader's composite mask files."""
        if img is None:
            return None
        parts = []
        im = self._intensity_bad_mask(img)
        if im is not None:
            parts.append(im)
        cm = self._loader.composite_mask()
        if cm is not None and cm.shape == img.shape:
            parts.append(cm != 0)
        if not parts:
            return None
        out = parts[0]
        for p in parts[1:]:
            out = out | p
        return out

    def _update_intensity_overlay(self):
        bad = self._combined_bad_mask(self._cur)
        if bad is None or not bad.any():
            self._viewer.clear_overlay()
        else:
            self._viewer.set_mask_overlay(bad)

    def _on_imask_changed(self, *_):
        self._update_intensity_overlay()
        if self._rad_auto.isChecked():
            self._radial_integrate()
