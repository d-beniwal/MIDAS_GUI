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
                         simulate_rings, read_geometry, geometry_fields_from_file,
                         _spec_from_result_ns, _NoScrollComboBox,
                         make_kedge_label, make_pixel_label)
from midas_gui.widgets import PickableImageViewer, ProfileViewer, DataLoaderPanel
from midas_gui.workers import ProjectionWorker, build_integration_context, integrate_frame
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
        self._is_projection = False                # showing a projected stack?
        self._proj_worker = None
        self._rad_grid_cache = None                # (key, which, nbins, r_axis)
        # Full calibration geometry (tilts + distortion) from a loaded calibration
        # file → proper MIDAS-engine radial integration; None = simple circle binning.
        self._calib_geom = None                    # geometry dict, or None
        self._calib_ctx = None                     # cached (spec, integration context)
        self._calib_ctx_sig = None                 # signature the context was built for
        self._topn_items: list = []                # scatter overlays for Top-N pixels
        # Debounce the frame slider: coalesce rapid ticks into one heavy refresh.
        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setSingleShot(True); self._refresh_timer.setInterval(60)
        self._refresh_timer.timeout.connect(self._on_loader_data)
        self._build_ui()
        if self._loader.stats_panel is not None:
            self._loader.stats_panel.scopeChanged.connect(self._update_stats)
        self._loader.set_path(DEFAULT_NICKEL_H5, dataset="exchange/data")

    def set_mask_from_tab1(self, mask):
        self._loader.set_tab1_mask(mask)

    def shutdown(self):
        """Called by MainWindow on app close to stop any live PV stream."""
        self._loader.stop_live()

    def get_geometry(self) -> dict:
        """Current manual geometry — λ (Å), pixel (µm), Lsd (µm), beam centre (px).

        Lsd is entered in mm (display) but always returned/used in µm."""
        return {
            "wavelength_A": self._wl.value(),
            "pxY": self._px.value(),
            "Lsd": self._lsd_um(),
            "BC_y": self._bcy.value(),
            "BC_z": self._bcz.value(),
        }

    def _lsd_um(self) -> float:
        """Lsd in µm (internal unit) from the mm display field."""
        return self._lsd.value() * 1000.0

    def set_geometry(self, g: dict):
        """Replace the manual-geometry fields from a geometry dict (e.g. the Calibrate
        tab's result). Values are µm/Å/px; the Lsd field displays mm."""
        if not g:
            return
        # (widget, dict key, µm→display scale)
        for w, key, scale in ((self._wl, "wavelength_A", 1.0), (self._px, "pxY", 1.0),
                              (self._lsd, "Lsd", 0.001), (self._bcy, "BC_y", 1.0),
                              (self._bcz, "BC_z", 1.0)):
            v = g.get(key)
            if v is not None:
                w.blockSignals(True); w.setValue(float(v) * scale); w.blockSignals(False)
        if g.get("BC_y") is not None or g.get("BC_z") is not None:
            self._bc_auto.setChecked(False)   # use the supplied beam centre
        # If tilts / distortion / detector size accompany the geometry (e.g. a
        # calibration result sent from the Calibrate tab), capture the FULL
        # geometry so radial integration goes through the MIDAS engine (tilts +
        # distortion), not concentric-circle binning.
        has_full = any(g.get(k) not in (None, 0, 0.0) for k in ("tx", "ty", "tz")) \
            or bool(g.get("distortion")) \
            or (g.get("NrPixelsY") and g.get("NrPixelsZ"))
        if has_full:
            self._apply_full_geometry_dict(g)
        self._redraw_rings()
        if self._rad_auto.isChecked():
            self._radial_integrate()
        else:
            self._refresh_profile_markers()

    def _apply_full_geometry_dict(self, g: dict):
        """Build ``self._calib_geom`` from a full geometry dict (tilts/distortion/
        detector size) so the tilt/distortion-aware radial engine is used."""
        px = float(g.get("pxY") or 0.0)
        geom = {
            "wavelength_A": g.get("wavelength_A"),
            "Lsd": g.get("Lsd"), "BC_y": g.get("BC_y"), "BC_z": g.get("BC_z"),
            "tx": float(g.get("tx", 0.0) or 0.0),
            "ty": float(g.get("ty", 0.0) or 0.0),
            "tz": float(g.get("tz", 0.0) or 0.0),
            "pxY": px, "pxZ": float(g.get("pxZ") or px),
            "NrPixelsY": g.get("NrPixelsY"), "NrPixelsZ": g.get("NrPixelsZ"),
            "distortion": dict(g.get("distortion") or {}),
        }
        # Need the detector size for the integration grid; fall back to the
        # current image shape if the sender did not supply it.
        if not (geom["NrPixelsY"] and geom["NrPixelsZ"]) and self._cur is not None:
            nz, ny = self._cur.shape
            geom["NrPixelsY"], geom["NrPixelsZ"] = ny, nz
        required = ("wavelength_A", "Lsd", "BC_y", "BC_z", "pxY",
                    "NrPixelsY", "NrPixelsZ")
        if any(not geom.get(k) for k in required):
            return   # incomplete — keep circle binning
        self._calib_geom = geom
        self._calib_ctx = self._calib_ctx_sig = None
        tilt = any(abs(geom[k]) > 1e-9 for k in ("tx", "ty", "tz"))
        mode = ("full integration: tilts"
                + ("+distortion" if geom["distortion"] else "")
                if (tilt or geom["distortion"]) else "full integration")
        self._calib_lbl.setText(f"Geometry from Calibrate tab  ·  {mode}")

    # ── UI ────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6); root.setSpacing(0)
        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        split.setChildrenCollapsible(False); split.setHandleWidth(6)
        root.addWidget(split); self._hsplit = split

        # ── LEFT: data loader (stack mode) ──
        self._loader = DataLoaderPanel(mode="stack", allow_live=True)
        self._loader.setMinimumWidth(200)
        self._loader.dataChanged.connect(lambda: self._refresh_timer.start())
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
        # Lsd is shown/entered in mm (calculations & files still use µm).
        self._lsd = _fspin(0.001, 1e5, 4, DEFAULT_LSD_UM / 1000.0, " mm")
        self._lsd.setFixedWidth(120)
        self._px = _fspin(1.0, 5000.0, 2, DEFAULT_PIXEL_UM, "µm")
        self._max2t = _fspin(1.0, 90.0, 1, 25.0, "°")
        geo = S.Form()
        geo.row((make_kedge_label(self._wl, "λ:"), self._wl), ("max 2θ:", self._max2t))
        geo.row(("Lsd:", self._lsd), (make_pixel_label(self._px, "px:"), self._px))
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
        self._ring_info.setStyleSheet(f"font-family:{S.MONO_CSS};font-size:10px")
        ring.body.addWidget(self._ring_info)
        lv.addWidget(ring)
        lv.addStretch(1)
        split.addWidget(scroll)

        # Right: image (top) + radial-integration plot (bottom) in a splitter.
        right = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        self._viewer = PickableImageViewer()
        self._viewer.bcPicked.connect(self._on_bc_picked)
        self._viewer.ringFitBC.connect(self._on_ring_fit_bc)
        # Top-N brightest-pixel locator (toggle on the image toolbar).
        vtb = self._viewer._toolbar_layout
        self._topn_btn = QtWidgets.QPushButton("Top-N pixels"); self._topn_btn.setCheckable(True)
        self._topn_btn.setToolTip(
            "Mark the N highest-intensity pixels on the image (crosshair + circle) "
            "and show their statistics. Click again to turn off.")
        self._topn_spin = _NoScrollSpinBox(); self._topn_spin.setRange(1, 100000)
        self._topn_spin.setValue(20); self._topn_spin.setFixedWidth(70)
        self._topn_spin.setToolTip("Number of highest-intensity pixels to mark.")
        vtb.addWidget(self._topn_btn)
        vtb.addWidget(QtWidgets.QLabel("N:"))
        vtb.addWidget(self._topn_spin)
        self._topn_btn.toggled.connect(self._on_topn_toggled)
        self._topn_spin.valueChanged.connect(
            lambda *_: self._show_topn() if self._topn_btn.isChecked() else None)
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
        self._is_projection = False
        if self._loader.stats_panel is not None:
            self._loader.stats_panel.set_scope_enabled(True)
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
        # "All frames" stats don't depend on the current frame — skip on frame change
        # (avoids re-reading + re-correcting the whole stack on every slider tick).
        sp = self._loader.stats_panel
        if getattr(self, "_topn_btn", None) is not None and self._topn_btn.isChecked():
            self._show_topn()   # re-locate brightest pixels + refresh their stats
        elif sp is None or sp.scope() != "all":
            self._update_stats()
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
        if getattr(self, "_topn_btn", None) is not None and self._topn_btn.isChecked():
            self._show_topn()
        else:
            self._update_stats()
        if self._rad_auto.isChecked():
            self._radial_integrate()

    def _project(self):
        if self._loader.n_frames() <= 1:
            QtWidgets.QMessageBox.warning(self, "No stack", "Projection needs a stack."); return
        if self._proj_worker and self._proj_worker.isRunning():
            return
        method = next(m for m, b in self._proj_method.items() if b.isChecked())
        axis = self._proj_axis.value()
        skip = self._proj_skip.value()
        self._info_lbl.setText("Projecting stack…")
        self._proj_btn.setEnabled(False)
        self._proj_worker = ProjectionWorker(
            self._loader.full_stack, method, axis, skip,
            dark=self._loader.dark(), bright=self._loader.bright(),
            background=self._loader.background(), bright_mode=self._loader.bright_mode(),
            parent=self)
        self._proj_worker.finished.connect(self._on_projection_done)
        self._proj_worker.failed.connect(self._on_projection_fail)
        self._proj_worker.start()

    def _on_projection_done(self, img, info):
        self._proj_btn.setEnabled(True)
        self._cur = img
        self._is_projection = True
        if self._loader.stats_panel is not None:
            self._loader.stats_panel.set_scope_enabled(False)
        self._viewer.set_image(self._cur)
        if self._bc_auto.isChecked():
            NZ, NY = self._cur.shape
            for w, v in ((self._bcy, NY / 2.0), (self._bcz, NZ / 2.0)):
                w.blockSignals(True); w.setValue(v); w.blockSignals(False)
        if getattr(self, "_rings", None):
            self._redraw_rings()
        self._update_intensity_overlay()
        self._update_stats()
        if self._rad_auto.isChecked():
            self._radial_integrate()
        self._info_lbl.setText(info)

    def _on_projection_fail(self, msg):
        self._proj_btn.setEnabled(True)
        self._info_lbl.setText("Projection failed.")
        QtWidgets.QMessageBox.critical(self, "Projection error", msg[:500])

    # ── Ring simulation ───────────────────────────────────────────

    def _simulate(self):
        if self._cur is None:
            QtWidgets.QMessageBox.warning(self, "No image", "Load data first."); return
        try:
            lattice = dict(a=self._a.value(), b=self._b.value(), c=self._c.value(),
                           alpha=self._al.value(), beta=self._be.value(), gamma=self._ga.value())
            rings = simulate_rings(lattice, self._sg.value(), self._wl.value(),
                                   self._lsd_um(), self._px.value(), self._max2t.value())
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
                self._lsd_um(), self._px.value(), self._wl.value())
        else:
            self._profile_view.set_ring_markers([])

    def _radial_integrate(self):
        """Azimuthal average of the current frame.

        With a loaded calibration file the full geometry (tilts + distortion) is used
        via the MIDAS integration engine; otherwise a fast circle-binning about the
        beam centre is used."""
        if self._cur is None:
            return
        if self._calib_geom is not None:
            try:
                r_axis, prof = self._midas_radial(self._cur)
            except Exception:
                import traceback
                self._calib_lbl.setText(
                    "Full-geometry integration failed — using circle binning. "
                    "See error log.")
                self._log_error(traceback.format_exc())
                r_axis, prof = self._radial_profile(
                    self._cur, self._bcy.value(), self._bcz.value(),
                    self._rad_r_bin.value(), mask=self._combined_bad_mask(self._cur))
        else:
            r_axis, prof = self._radial_profile(
                self._cur, self._bcy.value(), self._bcz.value(), self._rad_r_bin.value(),
                mask=self._combined_bad_mask(self._cur))
        self._profile_view.set_profile(
            r_axis, prof, wavelength_A=self._wl.value(),
            lsd_um=self._lsd_um(), px_um=self._px.value())
        self._refresh_profile_markers()

    def _midas_radial(self, img):
        """Radial profile via the MIDAS engine, honouring the loaded calibration's
        tilts + distortion (not just concentric circles). The binning geometry is
        built once per (geometry, R-bin, image shape, mask) and reused across
        frames; only the per-frame integration runs on a frame change."""
        import torch
        g = self._calib_geom
        r_bin = max(float(self._rad_r_bin.value()), 0.1)
        eta_bin = 5.0
        nz, ny = img.shape
        # Union of the static (file + Tab-1) mask with the per-frame intensity-range
        # mask, so out-of-range pixels are excluded from the integration too — this
        # matches the circle-binning fallback below.
        mask = self._combined_bad_mask(img)
        mask = None if mask is None else np.ascontiguousarray(mask, dtype=bool)
        # Fingerprint the mask by its *content* (not just its nonzero count) so the
        # cached binning context is rebuilt whenever the mask actually changes
        # (e.g. when the intensity-range thresholds are edited).
        mask_fp = None if mask is None else hash(mask.tobytes())
        sig = (id(g), round(r_bin, 4), (nz, ny), mask_fp)
        if self._calib_ctx is None or self._calib_ctx_sig != sig:
            spec = _spec_from_result_ns(
                r_bin, eta_bin, NrPixelsY=ny, NrPixelsZ=nz,
                pxY=g["pxY"], pxZ=g.get("pxZ") or g["pxY"], Lsd=g["Lsd"],
                BC_y=g["BC_y"], BC_z=g["BC_z"], tx=g.get("tx") or 0.0,
                ty=g.get("ty") or 0.0, tz=g.get("tz") or 0.0,
                wavelength_A=g["wavelength_A"], distortion=g.get("distortion") or {})
            # "hard" kernel keeps the interactive preview fast; the geometry (tilts +
            # distortion) is what makes the integration proper, not the sub-pixel kernel.
            ctx = build_integration_context(spec, "hard", mask, (None, None), weighted=True)
            self._calib_ctx = (spec, ctx); self._calib_ctx_sig = sig
        spec, ctx = self._calib_ctx
        img_t = torch.from_numpy(np.ascontiguousarray(img, dtype=np.float64))
        prof, _ = integrate_frame(
            img_t, spec, ctx["geom"], "hard", (None, None), None, False,
            corr_counts=ctx["corr_counts"], weighted=True, cnt_cake=ctx["cnt"])
        return ctx["r_ax"], prof

    def _log_error(self, text):
        """Append a traceback to the crash log (no LogPanel on this tab)."""
        try:
            from midas_gui.app import _log
            _log(text)
        except Exception:
            pass

    def _radial_grid(self, shape, bc_y, bc_z, r_bin):
        """Cached per-pixel radial bin index + axis, keyed on (shape, BC, r_bin).

        The pixel→radius grid only changes when the shape / beam centre / bin size
        change — not frame-to-frame — so scrubbing frames reuses it (one bincount
        instead of rebuilding indices+hypot each tick)."""
        r_bin = max(float(r_bin), 1e-6)
        key = (tuple(shape), round(float(bc_y), 4), round(float(bc_z), 4), round(r_bin, 6))
        cache = self._rad_grid_cache
        if cache is not None and cache[0] == key:
            return cache[1], cache[2], cache[3]
        NZ, NY = shape
        zz, yy = np.indices((NZ, NY))
        r = np.hypot(yy - bc_y, zz - bc_z)
        nbins = max(1, int(r.max() / r_bin) + 1)
        which = np.minimum((r / r_bin).astype(np.int64), nbins - 1).ravel()
        r_axis = (np.arange(nbins) + 0.5) * r_bin
        self._rad_grid_cache = (key, which, nbins, r_axis)
        return which, nbins, r_axis

    def _radial_profile(self, img: np.ndarray, bc_y: float, bc_z: float,
                        r_bin: float = 1.0, mask: Optional[np.ndarray] = None):
        """Mean intensity vs radius (px) about (bc_y, bc_z), using the cached grid.

        bc_y is the column (Y/x) and bc_z the row (Z/y); image shape is (NZ, NY).
        ``mask`` (bool, True = exclude) drops pixels; non-finite pixels are ignored.
        Returns (r_axis_px, profile), NaN in empty bins.
        """
        which, nbins, r_axis = self._radial_grid(img.shape, bc_y, bc_z, r_bin)
        vals = img.ravel()
        good = np.isfinite(vals)
        if mask is not None:
            good &= ~mask.ravel()
        sums = np.bincount(which[good], weights=vals[good], minlength=nbins)
        counts = np.bincount(which[good], minlength=nbins)
        prof = np.full(nbins, np.nan, dtype=np.float64)
        nz = counts > 0
        prof[nz] = sums[nz] / counts[nz]
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
            # Lsd arrives in µm; the field displays mm.
            lsd_mm = (lsd / 1000.0) if lsd is not None else None
            for w, v in ((self._wl, wl), (self._lsd, lsd_mm), (self._px, px),
                         (self._bcy, bcy), (self._bcz, bcz)):
                if v is not None:
                    w.blockSignals(True); w.setValue(float(v)); w.blockSignals(False)
            parts = []
            if lsd is not None: parts.append(f"Lsd={float(lsd)/1000:.2f} mm")
            if bcy is not None and bcz is not None:
                parts.append(f"BC=({float(bcy):.1f}, {float(bcz):.1f})")
            if wl is not None: parts.append(f"λ={float(wl):.5g} Å")
            if px is not None: parts.append(f"px={float(px):.4g} µm")
            # Capture the FULL geometry (tilts + distortion) so radial integration
            # goes through the MIDAS engine instead of concentric-circle binning.
            # Fall back to scalar/circle mode if the file lacks full geometry.
            self._calib_ctx = self._calib_ctx_sig = None
            try:
                self._calib_geom = geometry_fields_from_file(path)
                d = self._calib_geom
                tilt = any(abs(float(d.get(k) or 0.0)) > 1e-9 for k in ("tx", "ty", "tz"))
                mode = ("full integration: tilts"
                        + ("+distortion" if d.get("distortion") else "")
                        if (tilt or d.get("distortion"))
                        else "full integration (geometry-correct)")
            except Exception:
                self._calib_geom = None
                mode = "scalar geometry (circle binning)"
            self._calib_lbl.setText(
                f"Loaded {Path(path).suffix or 'file'} — " + "  ".join(parts)
                + f"  ·  {mode}")
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
        if getattr(self, "_topn_btn", None) is not None and self._topn_btn.isChecked():
            self._show_topn()   # re-rank excluding the updated intensity mask
        else:
            self._update_stats()
        if self._rad_auto.isChecked():
            self._radial_integrate()

    # ── Top-N brightest pixels ────────────────────────────────────────
    def _on_topn_toggled(self, on):
        if on:
            self._show_topn()
        else:
            self._clear_topn()
            self._update_stats()   # restore the normal stats readout

    def _clear_topn(self):
        for it in self._topn_items:
            self._viewer._iv.removeItem(it)
        self._topn_items.clear()

    def _show_topn(self):
        """Mark the N highest-intensity pixels and show their stats."""
        self._clear_topn()
        img = self._cur
        if img is None:
            return
        flat = np.nan_to_num(np.asarray(img, dtype=np.float64),
                             nan=-np.inf, posinf=-np.inf).ravel()
        # Exclude masked pixels (mask file + intensity-range mask) so they can
        # never rank in the Top-N or skew its statistics/plot.
        bad = self._combined_bad_mask(img)
        n_valid = flat.size
        if bad is not None:
            bad_flat = np.asarray(bad).ravel()
            flat[bad_flat] = -np.inf
            n_valid = int(np.count_nonzero(~bad_flat))
        n = min(int(self._topn_spin.value()), n_valid)
        if n <= 0:
            return
        idx = np.argpartition(flat, -n)[-n:]
        rows, cols = np.unravel_index(idx, img.shape)   # rows=Z, cols=Y
        # Image is displayed transposed, so plot (x=col=Y, y=row=Z); +0.5 centres.
        x = cols.astype(float) + 0.5
        y = rows.astype(float) + 0.5
        circ = pg.ScatterPlotItem(x=x, y=y, symbol="o", size=26,
                                  pen=pg.mkPen("#00cfff", width=1.5),
                                  brush=pg.mkBrush(0, 207, 255, 60))
        cross = pg.ScatterPlotItem(x=x, y=y, symbol="+", size=10,
                                   pen=pg.mkPen("#00cfff", width=1.5),
                                   brush=pg.mkBrush(0, 0, 0, 0))
        for it in (circ, cross):
            self._viewer._iv.addItem(it); self._topn_items.append(it)
        sp = getattr(self._loader, "stats_panel", None)
        if sp is not None:
            sp.set_data(flat[idx], scope=f"Top {n} pixels")

    # ── intensity statistics (left panel) ─────────────────────────────
    def _update_stats(self, *_):
        """Recompute the intensity-stats panel for the current scope/state."""
        sp = getattr(self._loader, "stats_panel", None)
        if sp is None:
            return
        if getattr(self, "_topn_btn", None) is not None and self._topn_btn.isChecked():
            return   # Top-N owns the stats panel while active
        try:
            if self._is_projection:
                img = self._cur
                if img is None:
                    return
                sp.set_data(self._unmasked_values(img), scope="Projected stack")
            elif sp.scope() == "all":
                vals, n = self._all_frame_values()
                sp.set_data(vals, scope=f"All frames ({n})")
            else:
                img = self._cur
                if img is None:
                    return
                sp.set_data(self._unmasked_values(img),
                            scope=f"Frame {self._loader.frame_index()}")
        except Exception:
            import traceback
            traceback.print_exc()

    def _unmasked_values(self, img):
        """1-D array of an image's pixels, excluding intensity-range + file masks."""
        bad = self._combined_bad_mask(img)
        return img[~bad] if bad is not None else np.asarray(img).ravel()

    def _all_frame_values(self):
        """Combined unmasked pixel values across all frames (corrections applied)."""
        stack = np.asarray(self._loader.full_stack())
        if stack.ndim == 2:
            stack = stack[None, ...]
        corr = np.asarray(self._loader.corrected(stack), dtype=np.float32)
        bad = ~np.isfinite(corr)
        if self._imask_on.isChecked():
            lo, hi = self._imask_lo.value(), self._imask_hi.value()
            if lo > -1e9:
                bad |= (corr <= lo)
            if hi > 0:
                bad |= (corr > hi)
        cm = self._loader.composite_mask()
        if cm is not None and cm.shape == corr.shape[1:]:
            bad |= (cm != 0)[None, :, :]
        return corr[~bad], corr.shape[0]
