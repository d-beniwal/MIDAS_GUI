"""Tab 0 — Data Viewer.

Plot single/stacked TIFF, HDF5 (2-D or 3-D), or a folder/glob of frames, with a
frame navigator for stacks.  Define one or more materials (each a dropdown-preset
or custom lattice + space group, independently toggled and colored) alongside a
shared wavelength + Lsd + pixel size, and overlay the simulated Debye-Scherrer
ring positions from every enabled material on the image and radial profile.

This tab is purely for inspection — it produces no shared state for other tabs.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from midas_gui.constants import (MATERIALS, DEFAULT_WAVELENGTH, DEFAULT_PIXEL_UM,
                           DEFAULT_LSD_UM, DEFAULT_BC_Y, DEFAULT_BC_Z, DEFAULT_RING_WIDTH,
                           DEFAULT_NICKEL_H5, DEFAULT_STEP_WAVELENGTH,
                           DEFAULT_STEP_TWO_THETA, DEFAULT_STEP_LSD_MM,
                           DEFAULT_STEP_PIXEL, DEFAULT_STEP_BC, DEFAULT_STEP_TILT)
from midas_gui.helpers import (_fspin, _NoScrollSpinBox, _browse,
                         simulate_rings, read_geometry, geometry_fields_from_file,
                         _spec_from_result_ns, _NoScrollComboBox,
                         make_kedge_label, make_pixel_label, tilted_ring_xy,
                         widgets_to_dict, apply_dict_to_widgets,
                         write_poni, write_standalone_paramstest)
from midas_gui.widgets import ProfileViewer, DataLoaderPanel
from midas_gui.roi_tools import ROIImageViewer
from midas_gui.workers import (ProjectionWorker, AllFrameStatsWorker,
                               build_integration_context, integrate_frame)
from midas_gui import style as S

# Default ring colors assigned to new materials, cycled by row count. First
# entry matches the single hardcoded ring color the old single-material UI used.
_MATERIAL_COLORS = ("#f0c060", "#4fc3f7", "#ab47bc", "#66bb6a", "#ef5350",
                     "#ffca28", "#26a69a", "#ec407a", "#7e57c2", "#8d6e63")

# Bounds for the intensity-mask pixel-< / pixel-> spin boxes (integer counts).
# Upper bound covers common 32-bit-detector overflow/dead-pixel sentinels
# (e.g. 2**32-1 = 4294967295), which a plain 32-bit QSpinBox cannot hold.
_IMASK_MIN = -1_000_000_000
_IMASK_MAX = 5_000_000_000


class MaterialDialog(QtWidgets.QDialog):
    """Edit one ring-simulation material: name, preset, lattice, space group."""

    def __init__(self, material: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Material")
        v = QtWidgets.QVBoxLayout(self)

        self._name = QtWidgets.QLineEdit(material["name"])
        v.addLayout(S.Form().row(("Name:", self._name)))

        self._preset = _NoScrollComboBox()
        for name in MATERIALS:
            self._preset.addItem(name)
        self._preset.addItem("Custom")
        idx = self._preset.findText(material.get("preset", "Custom"))
        self._preset.setCurrentIndex(idx if idx >= 0 else self._preset.findText("Custom"))
        self._preset.currentTextChanged.connect(self._on_preset)
        v.addLayout(S.Form().row(("Preset:", self._preset)))

        _LW, _AW = 78, 66     # compact lattice / angle-SG cell widths
        self._a = _fspin(0.1, 100.0, 3, material["a"]); self._a.setFixedWidth(_LW)
        self._b = _fspin(0.1, 100.0, 3, material["b"]); self._b.setFixedWidth(_LW)
        self._c = _fspin(0.1, 100.0, 3, material["c"]); self._c.setFixedWidth(_LW)
        self._al = _fspin(1.0, 179.0, 2, material["alpha"]); self._al.setFixedWidth(_AW)
        self._be = _fspin(1.0, 179.0, 2, material["beta"]); self._be.setFixedWidth(_AW)
        self._ga = _fspin(1.0, 179.0, 2, material["gamma"]); self._ga.setFixedWidth(_AW)
        self._sg = _NoScrollSpinBox(); self._sg.setRange(1, 230); self._sg.setValue(material["sg"])
        self._sg.setFixedWidth(_AW)
        self._cubic = QtWidgets.QCheckBox("Cubic (a=b=c, α=β=γ=90°)")
        self._cubic.setToolTip("Enter only a — b and c mirror it and all angles are fixed at 90°.")
        self._cubic.setChecked(bool(material.get("cubic", False)))
        self._cubic.toggled.connect(self._apply_lattice_enabled)
        self._a.valueChanged.connect(self._on_a_changed)
        latt = S.Form()
        latt.row(("a:", self._a), ("b:", self._b), ("c:", self._c))
        latt.row(("α:", self._al), ("β:", self._be), ("γ:", self._ga))
        latt.row(("SG #:", self._sg))
        v.addLayout(latt)
        v.addWidget(self._cubic)
        self._apply_lattice_enabled()

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        v.addWidget(buttons)

    def _on_preset(self, name: str):
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
        custom = self._preset.currentText() == "Custom"
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

    def apply_to(self, material: dict):
        """Write the dialog's current values back into ``material``."""
        material["name"] = self._name.text().strip() or material["name"]
        material["preset"] = self._preset.currentText()
        material["a"] = self._a.value(); material["b"] = self._b.value(); material["c"] = self._c.value()
        material["alpha"] = self._al.value(); material["beta"] = self._be.value()
        material["gamma"] = self._ga.value()
        material["sg"] = self._sg.value()
        material["cubic"] = self._cubic.isChecked()


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
        self._stats_worker = None                  # AllFrameStatsWorker for "All frames" scope
        self._stats_all_frames_dirty = False        # a request arrived while one was running
        self._rad_grid_cache = None                # (key, which, nbins, r_axis)
        # Full calibration geometry (tilts + distortion) from a loaded calibration
        # file → proper MIDAS-engine radial integration; None = simple circle binning.
        self._calib_geom = None                    # geometry dict, or None
        self._calib_ctx = None                     # cached (spec, integration context)
        self._calib_ctx_sig = None                 # signature the context was built for
        self._topn_items: list = []                # scatter overlays for Top-N pixels
        # Throttle rapid dataChanged bursts (slider drag, fast live streaming)
        # into one heavy refresh per interval. A single-shot QTimer is used as
        # a throttle, not a trailing-edge debounce: _on_data_changed() below
        # only (re)starts it while idle, so once armed it fires on schedule
        # instead of being restarted indefinitely and starved by a continuous
        # stream of events (e.g. live frames arriving faster than every 60ms).
        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setSingleShot(True); self._refresh_timer.setInterval(60)
        self._refresh_timer.timeout.connect(self._on_loader_data)
        # Debounce the intensity-mask lo/hi spinboxes: a bounded human gesture
        # (typing a value, holding the spin arrows), so restarting on every
        # valueChanged and only recomputing once input settles is correct
        # here (unlike _refresh_timer above, which must guarantee periodic
        # firing against a continuous external stream).
        self._imask_debounce_timer = QtCore.QTimer(self)
        self._imask_debounce_timer.setSingleShot(True)
        self._imask_debounce_timer.setInterval(120)
        self._imask_debounce_timer.timeout.connect(self._on_imask_changed)
        self._build_ui()
        if self._loader.stats_panel is not None:
            self._loader.stats_panel.scopeChanged.connect(self._update_stats)
        self._loader.set_path(DEFAULT_NICKEL_H5, dataset="exchange/data")

    def set_mask_from_tab1(self, mask):
        self._loader.set_tab1_mask(mask)

    def shutdown(self):
        """Called by MainWindow on app close to stop any live PV stream
        (and the Sim Detector server, if it was ever started)."""
        self._loader.stop_live()
        from midas_gui import sim_detector
        sim_detector.stop_all()

    def get_geometry(self) -> dict:
        """Current manual geometry — λ (Å), pixel (µm), Lsd (µm), beam centre (px).

        Lsd is entered in mm (display) but always returned/used in µm."""
        return {
            "wavelength_A": self._wl.value(),
            "pxY": self._px.value(),
            "Lsd": self._lsd_um(),
            "BC_y": self._bcy.value(),
            "BC_z": self._bcz.value(),
            "ty": self._ty.value(),
            "tz": self._tz.value(),
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
                              (self._bcz, "BC_z", 1.0), (self._ty, "ty", 1.0),
                              (self._tz, "tz", 1.0)):
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

    # ── GUI state (Save/Load GUI State) ─────────────────────────────
    def _state_widgets(self) -> dict:
        return {
            "proj_skip": self._proj_skip,
            "proj_nframes": self._proj_nframes,
            "calib_ed": self._calib_ed,
            "imask_on": self._imask_on,
            "imask_lo": self._imask_lo,
            "imask_hi": self._imask_hi,
            "wl": self._wl,
            "lsd": self._lsd,
            "px": self._px,
            "max2t": self._max2t,
            "bc_auto": self._bc_auto,
            "bcy": self._bcy,
            "bcz": self._bcz,
            "ty": self._ty,
            "tz": self._tz,
            "show_rings": self._show_rings,
            "show_labels": self._show_labels,
            "ring_width": self._ring_width,
            "topn_spin": self._topn_spin,
            "rad_r_bin": self._rad_r_bin,
            "rad_auto": self._rad_auto,
        }

    def get_state(self) -> dict:
        fields = widgets_to_dict(self._state_widgets())
        fields["materials"] = [{k: v for k, v in m.items() if not k.startswith("_")}
                                for m in self._materials]
        return {"fields": fields, "loader": self._loader.get_state()}

    def set_state(self, state: dict):
        """Restores the loader (which re-triggers its own data reload) and any
        calibration file (re-triggering ``_load_calibration``) *before* applying
        the saved field values, so an explicitly saved value always wins over
        whatever a re-triggered load computed as a default."""
        self._loader.set_state(state.get("loader") or {})
        fields = state.get("fields", {})
        calib_path = fields.get("calib_ed")
        if calib_path:
            self._calib_ed.setText(calib_path)
            if Path(calib_path).exists():
                self._load_calibration()
        materials = fields.get("materials")
        if materials:
            self._set_materials(materials)
        apply_dict_to_widgets(self._state_widgets(), fields)
        self._redraw_rings()
        if self._rad_auto.isChecked():
            self._radial_integrate()
        else:
            self._refresh_profile_markers()

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
        self._loader.dataChanged.connect(self._on_data_changed)
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
        self._proj_skip = _NoScrollSpinBox(); self._proj_skip.setRange(0, 1000000); self._proj_skip.setValue(1)
        self._proj_skip.setFixedWidth(72)
        self._proj_skip.setToolTip(
            "Ignore this many leading frames before projecting\n"
            "(1 = skip the first frame, 4 = skip the first four).")
        self._proj_nframes = _NoScrollSpinBox(); self._proj_nframes.setRange(0, 1000000)
        self._proj_nframes.setValue(0)
        self._proj_nframes.setFixedWidth(72)
        self._proj_nframes.setToolTip(
            "Number of frames to project after Skip frames\n"
            "(0 = use all remaining frames).")
        ax = S.Form()
        ax.row(("Skip frames:", self._proj_skip), ("N frames:", self._proj_nframes))
        self._proj_grp.body.addLayout(ax)
        self._proj_btn = QtWidgets.QPushButton("Project stack")
        self._proj_btn.clicked.connect(self._project)
        self._frame_btn = QtWidgets.QPushButton("Back to frames")
        self._frame_btn.clicked.connect(self._on_loader_data)
        self._proj_grp.body.addLayout(S.button_grid([self._proj_btn, self._frame_btn], 2))
        self._proj_grp.body.addWidget(self._info_lbl)
        self._proj_grp.setEnabled(False)
        lv.addWidget(self._proj_grp)

        # ── Intensity range mask (compact — lives in the radial-plot toolbar,
        # see below where self._profile_view is built) ──
        self._imask_on = QtWidgets.QCheckBox("Exclude range")
        self._imask_on.setToolTip(
            "Pixels < min or > max are masked: drawn as a red overlay on the image\n"
            "and excluded from the radial integration (removes gaps / hot / overflow).")
        # Plain QSpinBox is 32-bit-int limited (overflows on detector sentinel
        # values like 2**32-1 for dead/overflow pixels), so these stay double
        # spin boxes with decimals=0 — displays/steps as a whole number with no
        # decimal point, but the range comfortably covers such sentinels.
        self._imask_lo = _fspin(_IMASK_MIN, _IMASK_MAX, 0, 0.0)
        self._imask_lo.setToolTip("Pixels < this value are masked (dead / gap / beam-stop).")
        self._imask_lo.setFixedWidth(112)
        self._imask_hi = _fspin(0, _IMASK_MAX, 0, 1_048_575)
        self._imask_hi.setToolTip("Pixels > this value are masked (hot / overflow).")
        self._imask_hi.setFixedWidth(112)
        for w in (self._imask_lo, self._imask_hi):
            w.setEnabled(False)
        self._imask_on.toggled.connect(
            lambda c: (self._imask_lo.setEnabled(c), self._imask_hi.setEnabled(c)))
        self._imask_on.toggled.connect(self._on_imask_changed)
        self._imask_lo.valueChanged.connect(self._on_imask_value_changed)
        self._imask_hi.valueChanged.connect(self._on_imask_value_changed)

        # ── Ring simulation card ──
        ring = S.make_card("Ring simulation")
        self._materials: list = []
        self._materials_box = QtWidgets.QVBoxLayout()
        self._materials_box.setSpacing(3)
        ring.body.addLayout(self._materials_box)
        self._add_material("Ni (FCC)")
        add_mat_btn = QtWidgets.QPushButton("+ Add material")
        add_mat_btn.setToolTip("Overlay rings from another material simultaneously.")
        add_mat_btn.clicked.connect(lambda: self._add_material())
        ring.body.addWidget(add_mat_btn)
        ring.body.addWidget(S.hline())

        self._wl = _fspin(0.0001, 1e6, 4, DEFAULT_WAVELENGTH, "Å", step=DEFAULT_STEP_WAVELENGTH)
        # Lsd is shown/entered in mm (calculations & files still use µm).
        self._lsd = _fspin(0.001, 1e6, 3, DEFAULT_LSD_UM / 1000.0, " mm", step=DEFAULT_STEP_LSD_MM)
        self._lsd.setFixedWidth(120)
        self._px = _fspin(0.1, 1e6, 2, DEFAULT_PIXEL_UM, "µm", step=DEFAULT_STEP_PIXEL)
        self._max2t = _fspin(0.001, 180.0, 1, 25.0, "°", step=DEFAULT_STEP_TWO_THETA)
        geo = S.Form()
        geo.row((make_kedge_label(self._wl, "λ:"), self._wl), ("max 2θ:", self._max2t))
        geo.row(("Lsd:", self._lsd), (make_pixel_label(self._px, "px:"), self._px))
        ring.body.addLayout(geo)

        self._bc_auto = QtWidgets.QCheckBox("Beam centre = image centre"); self._bc_auto.setChecked(True)
        ring.body.addWidget(self._bc_auto)
        self._bcy = _fspin(-1e5, 1e5, 1, DEFAULT_BC_Y, "px", step=DEFAULT_STEP_BC)
        self._bcz = _fspin(-1e5, 1e5, 1, DEFAULT_BC_Z, "px", step=DEFAULT_STEP_BC)
        self._bcy.setEnabled(False); self._bcz.setEnabled(False)
        self._bc_auto.toggled.connect(lambda c: (self._bcy.setEnabled(not c), self._bcz.setEnabled(not c)))
        ring.body.addLayout(S.Form().row(("BC_y:", self._bcy), ("BC_z:", self._bcz)))

        self._ty = _fspin(-180.0, 180.0, 2, 0.0, "°", step=DEFAULT_STEP_TILT)
        self._tz = _fspin(-180.0, 180.0, 2, 0.0, "°", step=DEFAULT_STEP_TILT)
        self._ty.setToolTip("Detector tilt about the Y axis — bends the simulated rings.")
        self._tz.setToolTip("Detector tilt about the Z axis — bends the simulated rings.")
        ring.body.addLayout(S.Form().row(("ty:", self._ty), ("tz:", self._tz)))

        # Send λ / pixel / Lsd / beam-centre to the Calibrate tab (seed values).
        self._to_calib_btn = QtWidgets.QPushButton("→ Send geometry to Calibrate")
        self._to_calib_btn.setToolTip(
            "Copy λ, pixel size, Lsd and beam centre from here into the Calibrate "
            "tab's detector + seed fields.")
        self._to_calib_btn.clicked.connect(
            lambda: self.pushGeometry.emit(self.get_geometry()))
        ring.body.addWidget(self._to_calib_btn)

        ctl = QtWidgets.QHBoxLayout()
        self._show_rings = QtWidgets.QCheckBox("Rings"); self._show_rings.setChecked(True)
        self._show_rings.toggled.connect(self._set_rings_visible)
        self._show_labels = QtWidgets.QCheckBox("Labels"); self._show_labels.setChecked(True)
        self._show_labels.toggled.connect(self._set_rings_visible)
        self._ring_width = _fspin(0.5, 10.0, 1, DEFAULT_RING_WIDTH, "px")
        self._ring_width.setToolTip("Line thickness of the simulated rings on the image.")
        self._ring_width.setMaximumWidth(80)
        self._ring_width.valueChanged.connect(self._redraw_rings)
        ctl.addWidget(self._show_rings); ctl.addWidget(self._show_labels)
        ctl.addSpacing(8)
        ctl.addWidget(QtWidgets.QLabel("thickness:"))
        ctl.addWidget(self._ring_width)
        ctl.addStretch(1)
        ring.body.addLayout(ctl)
        self._sim_btn = S.primary_btn("Simulate rings")
        self._sim_btn.setCheckable(True)
        self._sim_btn.setToolTip(
            "Toggle live ring simulation — while on, rings recompute automatically "
            "whenever material, lattice, or geometry parameters change.")
        self._sim_btn.toggled.connect(self._on_sim_toggled)
        ring.body.addWidget(self._sim_btn)
        for w in (self._wl, self._lsd, self._px, self._max2t):
            w.valueChanged.connect(self._on_sim_param_changed)
        self._ring_info = QtWidgets.QPlainTextEdit(); self._ring_info.setReadOnly(True)
        self._ring_info.setMaximumHeight(140)
        self._ring_info.setStyleSheet(f"font-family:{S.MONO_CSS};font-size:10px")
        ring.body.addWidget(self._ring_info)
        lv.addWidget(ring)

        # ── Calibration card ──
        calc = S.make_card("Load/save calibration (optional)")
        self._calib_ed = QtWidgets.QLineEdit()
        self._calib_ed.setPlaceholderText("calibration.json / paramstest.txt / .poni…")
        calc.body.addLayout(_frow(self._calib_ed, self._browse_calib))
        self._calib_ed.returnPressed.connect(self._load_calibration)
        self._calib_lbl = QtWidgets.QLabel("No calibration loaded — using manual geometry / BC.")
        self._calib_lbl.setStyleSheet(f"color:{S.MUTED};font-size:10px")
        self._calib_lbl.setWordWrap(True)
        calc.body.addWidget(self._calib_lbl)
        save_row = QtWidgets.QHBoxLayout(); save_row.setSpacing(4)
        self._save_json_btn = QtWidgets.QPushButton("Save JSON")
        self._save_json_btn.setToolTip(
            "Save the current geometry (manual fields, or the loaded calibration's\n"
            "full geometry) as a calibration.json.")
        self._save_json_btn.clicked.connect(lambda: self._save_calibration("json"))
        self._save_params_btn = QtWidgets.QPushButton("Save params (.txt)")
        self._save_params_btn.setToolTip(
            "Save the current geometry as a MIDAS parameter file (paramstest.txt).")
        self._save_params_btn.clicked.connect(lambda: self._save_calibration("paramstest"))
        self._save_poni_btn = QtWidgets.QPushButton("Save PONI")
        self._save_poni_btn.setToolTip(
            "Save the current geometry as a pyFAI .poni file.\n"
            "Note: ty/tz tilts have no PONI equivalent and are not exported.")
        self._save_poni_btn.clicked.connect(lambda: self._save_calibration("poni"))
        save_row.addWidget(self._save_json_btn)
        save_row.addWidget(self._save_params_btn)
        save_row.addWidget(self._save_poni_btn)
        calc.body.addLayout(save_row)
        lv.addWidget(calc)
        lv.addStretch(1)
        split.addWidget(scroll)

        # Right: image (top) + radial-integration plot (bottom) in a splitter.
        right = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        right.setHandleWidth(8)
        self._viewer = ROIImageViewer()
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
        self._topn_thresh_on = QtWidgets.QCheckBox("I >")
        self._topn_thresh_on.setToolTip(
            "Only consider pixels above this intensity for Top-N — pixels at or "
            "below it are excluded from ranking and never marked.")
        self._topn_thresh = _fspin(-1e9, 1e9, 1, 0.0)
        self._topn_thresh.setFixedWidth(84)
        self._topn_thresh.setEnabled(False)
        vtb.addWidget(self._topn_btn)
        vtb.addWidget(QtWidgets.QLabel("N:"))
        vtb.addWidget(self._topn_spin)
        vtb.addWidget(self._topn_thresh_on)
        vtb.addWidget(self._topn_thresh)
        self._topn_btn.toggled.connect(self._on_topn_toggled)
        self._topn_spin.valueChanged.connect(
            lambda *_: self._show_topn() if self._topn_btn.isChecked() else None)
        self._topn_thresh_on.toggled.connect(self._topn_thresh.setEnabled)
        self._topn_thresh_on.toggled.connect(
            lambda *_: self._show_topn() if self._topn_btn.isChecked() else None)
        self._topn_thresh.valueChanged.connect(
            lambda *_: self._show_topn() if self._topn_btn.isChecked() else None)
        right.addWidget(self._viewer)

        # Radial integration (azimuthal average around the beam centre).
        self._profile_view = ProfileViewer()
        self._profile_view.radiusClicked.connect(self._on_radius_clicked)
        ptb = self._profile_view._toolbar_layout
        self._rad_r_bin = _fspin(0.1, 20.0, 2, 1.0, "px"); self._rad_r_bin.setFixedWidth(56)
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
        self._radial_help_btn = QtWidgets.QToolButton()
        self._radial_help_btn.setText("?")
        self._radial_help_btn.setFixedSize(18, 18)
        self._radial_help_btn.setToolTip("How is this profile calculated?")
        self._radial_help_btn.setStyleSheet(
            "QToolButton { border-radius: 9px; border: 1px solid #777; "
            "background: #333; color: #ddd; font-weight: bold; }"
            "QToolButton:hover { background: #444; }")
        self._radial_help_btn.clicked.connect(self._show_radial_help)
        ptb.insertWidget(ptb.indexOf(self._rad_btn) + 1, self._radial_help_btn)
        # Exclude-out-of-range-pixels controls (moved here from the left panel
        # to share this row; the bins/max stats printout is hidden to make room).
        # Appended after everything else (including the row's stretch) so the
        # group sits pinned to the far right of the toolbar.
        self._profile_view._stat.hide()
        ptb.addWidget(self._imask_on)
        ptb.addWidget(QtWidgets.QLabel(" <"))
        ptb.addWidget(self._imask_lo)
        ptb.addWidget(QtWidgets.QLabel(">"))
        ptb.addWidget(self._imask_hi)
        right.addWidget(self._profile_view)
        right.setStretchFactor(0, 3); right.setStretchFactor(1, 1)
        right.setMinimumWidth(320)
        split.addWidget(right)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 0); split.setStretchFactor(2, 1)
        split.setSizes([286, 361, 950])

        # Recompute rings / radial profile when the beam centre is edited manually.
        self._bcy.valueChanged.connect(self._on_bc_changed)
        self._bcz.valueChanged.connect(self._on_bc_changed)
        self._ty.valueChanged.connect(self._on_bc_changed)
        self._tz.valueChanged.connect(self._on_bc_changed)

    # ── Materials list ───────────────────────────────────────────

    @staticmethod
    def _swatch_style(color: str) -> str:
        return f"background-color:{color}; border:1px solid #555; border-radius:2px;"

    def _new_material_defaults(self, name: Optional[str] = None) -> dict:
        if name is None:
            name = f"Material {len(self._materials) + 1}"
        base = MATERIALS.get(name)
        if base is not None:
            m = dict(a=base["a"], b=base["b"], c=base["c"],
                      alpha=base["alpha"], beta=base["beta"], gamma=base["gamma"],
                      sg=base["sg"])
            preset = name
        else:
            m = dict(a=5.4116, b=5.4116, c=5.4116, alpha=90.0, beta=90.0, gamma=90.0, sg=225)
            preset = "Custom"
        m.update(name=name, preset=preset, enabled=True, cubic=False,
                  color=_MATERIAL_COLORS[len(self._materials) % len(_MATERIAL_COLORS)])
        return m

    def _build_material_row(self, material: dict) -> QtWidgets.QWidget:
        row = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0); h.setSpacing(4)
        chk = QtWidgets.QCheckBox()
        chk.setChecked(material["enabled"])
        chk.setToolTip("Show this material's rings")
        chk.toggled.connect(lambda checked, m=material: self._on_material_enabled(m, checked))
        swatch = QtWidgets.QPushButton()
        swatch.setFixedSize(18, 18)
        swatch.setToolTip("Ring color for this material (image + integration plot)")
        swatch.setStyleSheet(self._swatch_style(material["color"]))
        swatch.clicked.connect(lambda _, m=material, sw=swatch: self._pick_material_color(m, sw))
        name_btn = QtWidgets.QPushButton(material["name"])
        name_btn.setFlat(True)
        name_btn.setCursor(QtCore.Qt.PointingHandCursor)
        name_btn.setStyleSheet(
            "QPushButton{text-align:left; color:#8ecdf7; text-decoration:underline; "
            "border:none; padding:0;}")
        name_btn.setToolTip("Edit this material's lattice, space group, and name")
        name_btn.clicked.connect(lambda _, m=material, nb=name_btn: self._edit_material(m, nb))
        del_btn = QtWidgets.QPushButton("✕")
        del_btn.setFixedSize(20, 20)
        del_btn.setToolTip("Remove this material")
        del_btn.clicked.connect(lambda _, m=material, r=row: self._delete_material(m, r))
        h.addWidget(chk); h.addWidget(swatch); h.addWidget(name_btn, 1); h.addWidget(del_btn)
        row._del_btn = del_btn
        return row

    def _update_material_delete_buttons(self):
        many = len(self._materials) > 1
        for i in range(self._materials_box.count()):
            item = self._materials_box.itemAt(i)
            row = item.widget() if item is not None else None
            if row is not None:
                row._del_btn.setEnabled(many)

    def _add_material(self, name: Optional[str] = None):
        material = self._new_material_defaults(name)
        self._materials.append(material)
        self._materials_box.addWidget(self._build_material_row(material))
        self._update_material_delete_buttons()
        self._on_sim_param_changed()

    def _set_materials(self, materials: list):
        """Replace the whole materials list (e.g. from a loaded GUI state)."""
        for i in reversed(range(self._materials_box.count())):
            item = self._materials_box.takeAt(i)
            w = item.widget() if item is not None else None
            if w is not None:
                w.deleteLater()
        self._materials = []
        for md in materials:
            m = dict(md)
            m.setdefault("preset", "Custom")
            m.setdefault("cubic", False)
            m.setdefault("enabled", True)
            m.setdefault("color", _MATERIAL_COLORS[len(self._materials) % len(_MATERIAL_COLORS)])
            self._materials.append(m)
            self._materials_box.addWidget(self._build_material_row(m))
        if not self._materials:
            self._add_material("Ni (FCC)")
        self._update_material_delete_buttons()

    def _on_material_enabled(self, material: dict, checked: bool):
        material["enabled"] = checked
        self._on_sim_param_changed()

    def _pick_material_color(self, material: dict, swatch_btn: QtWidgets.QPushButton):
        col = QtWidgets.QColorDialog.getColor(QtGui.QColor(material["color"]), self, "Ring color")
        if not col.isValid():
            return
        material["color"] = col.name()
        swatch_btn.setStyleSheet(self._swatch_style(material["color"]))
        self._redraw_rings()
        self._refresh_profile_markers()

    def _edit_material(self, material: dict, name_btn: QtWidgets.QPushButton):
        dlg = MaterialDialog(material, self)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            dlg.apply_to(material)
            name_btn.setText(material["name"])
            self._on_sim_param_changed()

    def _delete_material(self, material: dict, row: QtWidgets.QWidget):
        if len(self._materials) <= 1:
            return
        self._materials.remove(material)
        self._materials_box.removeWidget(row)
        row.deleteLater()
        self._update_material_delete_buttons()
        self._on_sim_param_changed()

    def _any_material_rings(self) -> bool:
        return any(m.get("_rings") for m in self._materials)

    def _primary_material_name(self) -> str:
        for m in self._materials:
            if m["enabled"]:
                return m["name"]
        return self._materials[0]["name"] if self._materials else "Custom"

    # ── Loading ───────────────────────────────────────────────────

    def _on_data_changed(self):
        """Loader's dataChanged fired — throttle into at most one refresh per
        interval instead of restarting (and so indefinitely deferring) the
        timer on every event, which would starve the display during a fast
        continuous burst (e.g. live streaming faster than the timer interval)."""
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

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
        is_live = self._loader.is_live_frame_update()
        self._viewer.set_image(self._cur, autorange=fresh, reset_levels=not is_live)
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
        skip = self._proj_skip.value()
        nframes = self._proj_nframes.value()
        self._info_lbl.setText("Projecting stack…")
        self._proj_btn.setEnabled(False)
        self._proj_worker = ProjectionWorker(
            self._loader.full_stack, method, 0, skip, nframes,
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
        if self._any_material_rings():
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

    def _on_sim_toggled(self, checked: bool):
        """"Simulate rings" is now a live mode, not a one-shot action."""
        self._sim_btn.setText("Simulate rings (live)" if checked else "Simulate rings")
        if checked:
            self._simulate()

    def _on_sim_param_changed(self, *_):
        """Material/lattice/geometry field edited — resimulate while live mode is on.

        Guarded with ``getattr`` because materials are seeded (via
        ``_add_material``) before ``self._sim_btn`` exists during ``_build_ui``."""
        sim_btn = getattr(self, "_sim_btn", None)
        if sim_btn is not None and sim_btn.isChecked() and self._cur is not None:
            self._simulate()

    def _simulate(self):
        if self._cur is None:
            QtWidgets.QMessageBox.warning(self, "No image", "Load data first."); return
        lines, errors, any_rings = [], [], False
        for m in self._materials:
            m["_rings"] = []
            if not m["enabled"]:
                continue
            try:
                lattice = dict(a=m["a"], b=m["b"], c=m["c"],
                               alpha=m["alpha"], beta=m["beta"], gamma=m["gamma"])
                rings = simulate_rings(lattice, m["sg"], self._wl.value(),
                                       self._lsd_um(), self._px.value(), self._max2t.value())
            except Exception:
                import traceback
                errors.append(f"{m['name']}: {traceback.format_exc().splitlines()[-1]}")
                continue
            m["_rings"] = rings
            any_rings = True
            lines.append(f"{m['name']}: {len(rings)} rings")
            lines.append(f"{'hkl':>10}  {'2θ(°)':>7}  {'d(Å)':>7}  {'R(px)':>8}")
            for r in rings:
                h, k, l = r["hkl"]
                lines.append(f"{str((h,k,l)):>10}  {r['two_theta_deg']:7.3f}  "
                             f"{r['d_spacing']:7.4f}  {r['radius_px']:8.1f}")
            lines.append("")
        self._redraw_rings()
        if self._rad_auto.isChecked():
            self._radial_integrate()
        else:
            self._refresh_profile_markers()
        if errors:
            lines.append("Errors:"); lines.extend(errors)
        if not any_rings and not errors:
            lines = ["No enabled materials."]
        self._ring_info.setPlainText("\n".join(lines).rstrip())
        if errors and not any_rings:
            QtWidgets.QMessageBox.critical(self, "Simulation error", "\n".join(errors)[:500])

    def _clear_rings(self):
        for it in self._ring_items + self._label_items:
            self._viewer._iv.removeItem(it)
        self._ring_items.clear(); self._label_items.clear()

    def _redraw_rings(self):
        self._clear_rings()
        if self._cur is None or not self._any_material_rings():
            return
        bc_y, bc_z = self._bcy.value(), self._bcz.value()
        ty, tz = self._ty.value(), self._tz.value()
        tilted = abs(ty) > 1e-9 or abs(tz) > 1e-9
        px = self._px.value()
        th = np.linspace(0, 2 * math.pi, 400)
        vis_r = self._show_rings.isChecked()
        vis_l = self._show_labels.isChecked() and vis_r
        for m in self._materials:
            rings = m.get("_rings")
            if not m["enabled"] or not rings:
                continue
            pen = pg.mkPen(m["color"], width=self._ring_width.value(), style=QtCore.Qt.DotLine)
            for r in rings:
                rad = r["radius_px"]
                # Only drop non-physical radii — the ViewBox already clips rings
                # that extend past the visible image, so there's no need to cap
                # at the image diagonal (that silently hid high-2theta rings).
                if not (rad > 0 and math.isfinite(rad)):
                    continue
                if tilted:
                    ys, zs = tilted_ring_xy(r["two_theta_deg"], 0.0, ty, tz,
                                             self._lsd_um(), bc_y, bc_z, px, px)
                    label_y, label_z = ys[len(ys) // 2], zs[len(zs) // 2]
                else:
                    ys = bc_y + rad * np.cos(th); zs = bc_z + rad * np.sin(th)
                    label_y, label_z = bc_y, bc_z - rad
                item = pg.PlotDataItem(ys, zs, pen=pen)
                item.setVisible(vis_r)
                self._viewer._iv.addItem(item); self._ring_items.append(item)
                h, k, l = r["hkl"]
                txt = pg.TextItem(f"{h}{k}{l}", color=m["color"], anchor=(0.5, 1.0))
                txt.setPos(label_y, label_z)
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
        if self._any_material_rings():
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
        groups = [{"radii": [r["radius_px"] for r in m["_rings"]], "color": m["color"]}
                  for m in self._materials if m["enabled"] and m.get("_rings")]
        self._profile_view.set_ring_markers(
            groups, self._lsd_um(), self._px.value(), self._wl.value())

    def _effective_calib_geom(self) -> Optional[dict]:
        """Geometry used for radial integration: the loaded calibration's full
        geometry if present, otherwise one synthesized from the Ring-simulation
        widgets when a tilt is set — so the profile stays tilt-consistent with
        the on-image ring overlay even without a loaded calibration file."""
        if self._calib_geom is not None:
            return self._calib_geom
        ty, tz = self._ty.value(), self._tz.value()
        if abs(ty) < 1e-9 and abs(tz) < 1e-9:
            return None
        if self._cur is None:
            return None
        nz, ny = self._cur.shape
        px = self._px.value()
        return {
            "wavelength_A": self._wl.value(), "Lsd": self._lsd_um(),
            "BC_y": self._bcy.value(), "BC_z": self._bcz.value(),
            "tx": 0.0, "ty": ty, "tz": tz,
            "pxY": px, "pxZ": px,
            "NrPixelsY": ny, "NrPixelsZ": nz, "distortion": {},
        }

    def _show_radial_help(self):
        """Explain how the radial-integration plot's profile is computed."""
        QtWidgets.QMessageBox.information(
            self, "Radial integration — how it's calculated",
            "The plot shows intensity vs. radius: the azimuthal (angular) average "
            "of the image about the beam centre, grouped into rings of width "
            "\"R bin\".\n\n"
            "• Calibration loaded, or a tilt (ty/tz) set on the Ring-simulation "
            "card: the full MIDAS geometry engine is used. Pixels are binned into "
            "(η, R) cells honouring detector tilt and distortion, and each R-bin's "
            "value is a pixel-count-weighted mean across η — "
            "Σ(cell_mean·count) / Σ(count) — robust to partial or uneven azimuthal "
            "coverage.\n\n"
            "• Otherwise: a fast circle-binning fallback is used. Pixels are "
            "grouped purely by distance from the beam centre (BC_y, BC_z) into "
            "R-bins, and each bin's value is Σintensity / Σpixels — a plain "
            "per-bin mean, with no tilt correction.\n\n"
            "If full-geometry integration fails, the plot automatically falls "
            "back to circle binning and a warning is shown above the "
            "calibration card.")

    def _radial_integrate(self):
        """Azimuthal average of the current frame.

        With a loaded calibration file, or a tilt dialled into the Ring-simulation
        card, the full geometry (tilts + distortion) is used via the MIDAS
        integration engine; otherwise a fast circle-binning about the beam centre
        is used."""
        if self._cur is None:
            return
        geom = self._effective_calib_geom()
        if geom is not None:
            try:
                r_axis, prof = self._midas_radial(self._cur, geom)
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

    def _midas_radial(self, img, g):
        """Radial profile via the MIDAS engine, honouring the given geometry's
        tilts + distortion (not just concentric circles). ``g`` is either the
        loaded calibration's geometry or one synthesized from the live Ring-sim
        widgets (see ``_effective_calib_geom``). The binning geometry is built
        once per (geometry, R-bin, image shape, mask) and reused across frames;
        only the per-frame integration runs on a frame change."""
        import json
        import torch
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
        # Value-based signature (not id(g)) — a synthesized geometry is a fresh
        # dict on every call, so identity would defeat the cache entirely.
        sig = (round(float(g["Lsd"]), 3), round(float(g["BC_y"]), 3),
               round(float(g["BC_z"]), 3), round(float(g.get("tx") or 0.0), 4),
               round(float(g.get("ty") or 0.0), 4), round(float(g.get("tz") or 0.0), 4),
               round(float(g["pxY"]), 4), round(float(g.get("pxZ") or g["pxY"]), 4),
               round(float(g["wavelength_A"]), 6), g.get("NrPixelsY"), g.get("NrPixelsZ"),
               round(r_bin, 4), (nz, ny), mask_fp,
               json.dumps(g.get("distortion") or {}, sort_keys=True))
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
                for w, key in ((self._ty, "ty"), (self._tz, "tz")):
                    v = d.get(key)
                    if v is not None:
                        w.blockSignals(True); w.setValue(float(v)); w.blockSignals(False)
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

    def _export_geom(self) -> Optional[dict]:
        """Full geometry dict for calibration export: the loaded calibration's
        geometry if present (carries tilts/distortion/detector size from the
        file), otherwise one built from the current manual / Ring-simulation
        fields (tx=0, distortion empty)."""
        if self._calib_geom is not None:
            return dict(self._calib_geom)
        if self._cur is None:
            return None
        nz, ny = self._cur.shape
        px = self._px.value()
        return {
            "wavelength_A": self._wl.value(), "Lsd": self._lsd_um(),
            "BC_y": self._bcy.value(), "BC_z": self._bcz.value(),
            "tx": 0.0, "ty": self._ty.value(), "tz": self._tz.value(),
            "pxY": px, "pxZ": px, "NrPixelsY": ny, "NrPixelsZ": nz,
            "distortion": {},
        }

    def _save_calibration(self, kind: str):
        """Save the current geometry (see ``_export_geom``) as a calibration
        file — JSON (GUI bare-key format), MIDAS paramstest.txt, or pyFAI .poni."""
        geom = self._export_geom()
        if geom is None:
            QtWidgets.QMessageBox.warning(self, "No geometry", "Load data first.")
            return
        specs = {
            "json": ("Save calibration.json", "calibration.json", "JSON (*.json)"),
            "paramstest": ("Save MIDAS parameter file", "paramstest.txt", "Text (*.txt)"),
            "poni": ("Save calibration.poni", "calibration.poni", "PONI (*.poni)"),
        }
        title, default_name, filt = specs[kind]
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, title, default_name, filt)
        if not path:
            return
        try:
            if kind == "json":
                import json
                Path(path).write_text(json.dumps(geom, indent=2, default=str))
            elif kind == "paramstest":
                from types import SimpleNamespace
                ns = SimpleNamespace(
                    NrPixelsY=int(geom["NrPixelsY"]), NrPixelsZ=int(geom["NrPixelsZ"]),
                    pxY=float(geom["pxY"]), pxZ=float(geom.get("pxZ") or geom["pxY"]),
                    Lsd=float(geom["Lsd"]), BC_y=float(geom["BC_y"]), BC_z=float(geom["BC_z"]),
                    tx=float(geom.get("tx") or 0.0), ty=float(geom.get("ty") or 0.0),
                    tz=float(geom.get("tz") or 0.0), wavelength_A=float(geom["wavelength_A"]),
                    distortion=geom.get("distortion") or {},
                    _calibrant_name=self._primary_material_name())
                write_standalone_paramstest(ns, path)
            elif kind == "poni":
                write_poni(geom, path)
        except Exception:
            import traceback
            QtWidgets.QMessageBox.critical(self, "Save failed", traceback.format_exc()[:500])
            return
        QtWidgets.QMessageBox.information(self, "Saved", f"Calibration saved to:\n{path}")

    def _autofill_imask_max(self):
        """Default the intensity-mask upper bound to max(99.99th percentile, 100000)."""
        if self._cur is None:
            return
        fin = self._cur[np.isfinite(self._cur)]
        if not fin.size:
            return
        hi = max(int(round(np.percentile(fin, 99.99))), 100_000)
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
        if lo > _IMASK_MIN:
            bad |= (img < lo)
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

    def _on_imask_value_changed(self, *_):
        """Spinbox valueChanged fires on every keystroke/arrow-click; give
        immediate visual feedback but debounce the heavier stats/radial
        recompute until the value settles."""
        self._update_intensity_overlay()
        self._imask_debounce_timer.start()

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
        if bad is not None:
            flat[np.asarray(bad).ravel()] = -np.inf
        # Optional intensity floor: pixels at/below it are excluded from ranking
        # (and so never marked), regardless of whether they'd otherwise be Top-N.
        if self._topn_thresh_on.isChecked():
            flat[flat <= self._topn_thresh.value()] = -np.inf
        n_valid = int(np.count_nonzero(np.isfinite(flat)))
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
                self._update_stats_all_frames()
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

    def _update_stats_all_frames(self):
        """Kick off (or flag for re-run) a background computation of the "All
        frames" stats scope. Reading + correcting an entire stack/live buffer
        synchronously here would freeze the UI for large data, unlike the
        per-frame scope which already has a corrected frame in hand."""
        if self._stats_worker is not None and self._stats_worker.isRunning():
            self._stats_all_frames_dirty = True
            return
        self._stats_all_frames_dirty = False
        self._stats_worker = AllFrameStatsWorker(
            self._loader.full_stack,
            dark=self._loader.dark(), bright=self._loader.bright(),
            background=self._loader.background(), bright_mode=self._loader.bright_mode(),
            composite_mask=self._loader.composite_mask(),
            imask_on=self._imask_on.isChecked(),
            imask_lo=self._imask_lo.value(), imask_hi=self._imask_hi.value(),
            parent=self)
        self._stats_worker.finished.connect(self._on_all_frame_stats_done)
        self._stats_worker.failed.connect(self._on_all_frame_stats_fail)
        self._stats_worker.start()

    def _on_all_frame_stats_done(self, vals, n):
        sp = getattr(self._loader, "stats_panel", None)
        if sp is not None and sp.scope() == "all" and not self._is_projection:
            sp.set_data(vals, scope=f"All frames ({n})")
        if self._stats_all_frames_dirty:
            self._update_stats_all_frames()

    def _on_all_frame_stats_fail(self, msg):
        print(msg)   # background stats readout — mirror the try/except's
                      # silent-to-the-user, printed-to-console handling.
        if self._stats_all_frames_dirty:
            self._update_stats_all_frames()
