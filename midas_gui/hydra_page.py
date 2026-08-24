"""Hydra (4-panel GE detector) page for the Data Viewer tab.

``HydraViewerPage`` wires together the pieces built in earlier phases:
``HydraLoaderPanel`` (sibling auto-discovery + frame navigation),
``HydraDetectorToolbar`` (ge1/ge2/ge3/ge4/composite selector),
``midas_gui.hydra`` (the windmill-compositing engine), and one
``DetectorGeometryCard`` per panel + one for the composite — each panel's
beam-centre/ring calibration is independent, matching the physical reality
that the 4 GE panels are separate detectors.

Scope for this first version (see the approved implementation plan):
- No dark/bright/background correction or intensity-range masking for Hydra
  frames (single-detector mode already has this; Hydra reuses raw frames).
- The R-bin/Auto/Integrate radial-toolbar controls are shared across all 5
  panel cards (one setting, not duplicated 5x) — same simplification the
  plan calls out for v1.

The radial-integration plot (``HydraProfileViewer``) shows all 4 panels'
profiles at once (each computed independently, from its own beam-centre/
geometry) plus a toggleable "Composite" curve — the NaN-aware sum of the
four, each resampled onto a shared 2theta axis first (not a radial
integration of the already-composited image, which would double-count any
panel overlap and mix registration error into the profile).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
from PyQt5 import QtCore, QtWidgets

from midas_gui import hydra
from midas_gui.helpers import (_load_image, _apply_im_trans, _fspin,
                         geometry_fields_from_file, widgets_to_dict, apply_dict_to_widgets)
from midas_gui.hydra_geometry_card import DetectorGeometryCard
from midas_gui.hydra_widgets import HydraLoaderPanel, HydraDetectorToolbar, HydraProfileViewer
from midas_gui.roi_tools import ROIImageViewer, ROIRibbon
from midas_gui.widgets import _convert_radial
from midas_gui import style as S


class _ProfileSinkAdapter:
    """Adapts a DetectorGeometryCard's ProfileViewer-shaped calls
    (``set_profile``/``set_ring_markers``) into one named curve on a shared
    ``HydraProfileViewer`` — each card thinks it owns a normal profile
    plot, but all 5 draw onto the same multi-curve widget."""

    def __init__(self, viewer: HydraProfileViewer, key: str):
        self._viewer = viewer
        self._key = key

    def set_profile(self, r_px, profile, *, sigma=None, wavelength_A=None,
                    lsd_um=None, px_um=None):
        self._viewer.set_curve(self._key, r_px, profile,
                               lsd_um=lsd_um, px_um=px_um, wavelength_A=wavelength_A)

    def set_ring_markers(self, groups, lsd_um=None, px_um=None, wl=None):
        # Ring markers (vertical lines at each material's ring 2theta) aren't
        # well-defined on a plot overlaying 4 independently-calibrated
        # panels at once — skipped for this shared multi-curve plot.
        pass


class HydraViewerPage(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._states: dict = {}                 # panel number -> hydra.DetectorState
        self._raw_frames: dict = {}              # panel number -> last-loaded frame (post ImTrans)
        self._composite_img: Optional[np.ndarray] = None
        self._big_det_size: Optional[int] = None
        self._composite_seeded_size: Optional[int] = None
        self._disp_key = None                    # (shape, active key) — fresh-display detection
        self._active_card: Optional[DetectorGeometryCard] = None
        self._build_ui()
        self._on_panel_changed(self._toolbar.current())

    # ── UI ────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6); root.setSpacing(0)
        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        split.setChildrenCollapsible(False); split.setHandleWidth(6)
        root.addWidget(split)

        # ── LEFT: Hydra data loader ──
        self._loader = HydraLoaderPanel()
        self._loader.setMinimumWidth(200)
        self._loader.siblingsChanged.connect(self._on_siblings_changed)
        self._loader.frameChanged.connect(self._on_frame_changed)
        split.addWidget(self._loader)

        # ── MIDDLE: one geometry card per panel + one for the composite ──
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True); scroll.setMinimumWidth(260)
        self._card_stack = QtWidgets.QStackedWidget()
        scroll.setWidget(self._card_stack)
        # The multi-curve radial plot and shared R-bin/Auto controls are
        # built below (right side) before this loop's set_profile_view/
        # set_radial_controls calls, so build them first and reorder the
        # widget insertion into `right`/`split` afterward.
        self._profile_view = HydraProfileViewer()
        self._rad_r_bin = _fspin(0.1, 20.0, 2, 1.0, "px"); self._rad_r_bin.setFixedWidth(56)
        self._rad_r_bin.setToolTip(
            "Radial bin size for the azimuthal average — shared across all "
            "Hydra panels and the composite.")
        self._rad_auto = QtWidgets.QCheckBox("Auto"); self._rad_auto.setChecked(True)
        self._rad_auto.setToolTip("Recompute each panel's radial integration when "
                                  "its beam centre, tilt, or the frame changes.")

        self._cards: dict = {}
        for key in ("ge1", "ge2", "ge3", "ge4", "composite"):
            card = DetectorGeometryCard()
            card.set_image_source(self._make_image_provider(key), None)
            if key == "composite":
                # The "Composite" curve on the shared plot is the DERIVED
                # resample-and-sum of ge1-4's own curves (see
                # _refresh_composite_curve) — deliberately NOT this card's
                # own radial_integrate() (which would instead integrate the
                # already-composited image and reintroduce the
                # double-counting/registration-error problem that design
                # explicitly avoids). Leaving its profile/radial-control
                # bindings unset makes its own radial_integrate() a no-op;
                # its ring-simulation/BC fields are still fully functional
                # for the on-image ring overlay.
                pass
            else:
                # Every ge-card's own curve is bound once, permanently —
                # unlike the viewer (only one image shown at a time), all 4
                # profiles should stay visible on the shared plot regardless
                # of which panel is the currently *active* one for display.
                card.set_profile_view(_ProfileSinkAdapter(self._profile_view, key))
                card.set_radial_controls(self._rad_r_bin, self._rad_auto)
                n = int(key[2])
                card.geometryChanged.connect(lambda n=n: self._on_card_geometry_changed(n))
            self._cards[key] = card
            self._card_stack.addWidget(card)
        split.addWidget(scroll)

        # ── RIGHT: image viewer (with its own toolbar row) + radial plot ──
        right = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        right.setHandleWidth(8)
        self._viewer = ROIImageViewer()
        self._toolbar = HydraDetectorToolbar()
        self._toolbar.panelChanged.connect(self._on_panel_changed)
        self._viewer._toolbar_layout.addWidget(self._toolbar)
        self._roi_ribbon = ROIRibbon()
        self._viewer.set_ribbon(self._roi_ribbon)
        viewer_container = QtWidgets.QWidget()
        vc_layout = QtWidgets.QHBoxLayout(viewer_container)
        vc_layout.setContentsMargins(0, 0, 0, 0); vc_layout.setSpacing(0)
        vc_layout.addWidget(self._roi_ribbon)
        vc_layout.addWidget(self._viewer, 1)
        right.addWidget(viewer_container)

        ptb = self._profile_view._toolbar_layout
        self._rad_btn = QtWidgets.QPushButton("Integrate")
        self._rad_btn.setToolTip("Recompute all 4 panels' (and the composite's) "
                                 "radial integration now, regardless of Auto.")
        self._rad_btn.clicked.connect(lambda: self._refresh_profile_curves(force=True))
        # Insert right before the toolbar's trailing stretch, whatever index
        # that currently is (robust to HydraProfileViewer's own toolbar
        # layout, rather than a hardcoded position).
        insert_at = ptb.count() - 1
        ptb.insertWidget(insert_at, self._rad_btn)
        ptb.insertWidget(insert_at, self._rad_auto)
        ptb.insertWidget(insert_at, self._rad_r_bin)
        ptb.insertWidget(insert_at, QtWidgets.QLabel("  R bin:"))
        # R-bin edits already trigger each of the 5 cards' own auto-radial
        # (wired inside set_radial_controls); refresh the derived Composite
        # curve afterward since it depends on all 4 panels' fresh curves.
        self._rad_r_bin.valueChanged.connect(lambda *_: self._refresh_composite_curve())
        self._profile_view.compositeVisibilityChanged.connect(
            lambda *_: self._refresh_composite_curve())
        right.addWidget(self._profile_view)
        right.setStretchFactor(0, 3); right.setStretchFactor(1, 1)
        right.setMinimumWidth(320)
        split.addWidget(right)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 0); split.setStretchFactor(2, 1)
        split.setSizes([286, 361, 950])

    # ── Per-panel / composite frame sourcing ────────────────────────

    def _make_image_provider(self, key: str):
        """Each card's image_provider is bound once, at construction, to
        whichever panel/composite it represents — independent of which card
        is currently the *active* one (that only controls the viewer/plot
        binding, via set_viewer/set_profile_view in _on_panel_changed)."""
        if key == "composite":
            return lambda: self._composite_img
        n = int(key[2])
        return lambda: self._raw_frames.get(n)

    def _ensure_states_for_siblings(self, siblings: dict):
        for n in siblings:
            if n in self._states:
                continue
            st = hydra.DetectorState()
            st.load_default(n)
            self._states[n] = st
            fields = geometry_fields_from_file(str(hydra.default_param_file(n)))
            self._cards[f"ge{n}"].set_geometry(fields)

    def _load_panel_frame(self, n: int) -> Optional[np.ndarray]:
        path = self._loader.siblings().get(n)
        st = self._states.get(n)
        if path is None or st is None:
            return None
        st.data_file = path
        try:
            img = _load_image(path, self._loader.dataset(), self._loader.frame_index())
            img = _apply_im_trans(img, tuple(st.im_trans_opts))
        except Exception:
            return None
        self._raw_frames[n] = img
        return img

    def _build_composite_if_needed(self):
        if self._composite_img is not None:
            return
        siblings = self._loader.siblings()
        active = {n: p for n, p in siblings.items() if n in self._states}
        if len(active) < 2:
            self._composite_img = None
            return
        for n, p in active.items():
            self._states[n].data_file = p
        try:
            comp, big_det_size = hydra.build_windmill_composite(
                active, self._loader.frame_index(), self._loader.dataset(),
                self._states, op="max")
        except Exception:
            self._composite_img = None
            return
        self._composite_img = comp
        self._big_det_size = big_det_size
        self._reseed_composite_card_if_needed(big_det_size, active)

    def _reseed_composite_card_if_needed(self, big_det_size: int, active_panels: dict):
        """Seed the composite card's beam centre at the canvas centre (the
        composite is registered so its own geometric centre IS BigDetSize/2)
        the first time this canvas size is seen. Lsd is averaged across the
        contributing panels (they're all roughly the same sample-to-detector
        distance on a real Hydra rig); wavelength comes from whichever ge
        card was loaded first, since DetectorState itself has no wavelength
        (it's a ring-simulation-only parameter, not part of the compositing
        math). A user can still hand-edit the composite card afterward —
        this only fires once per distinct canvas size."""
        if self._composite_seeded_size == big_det_size or not active_panels:
            return
        self._composite_seeded_size = big_det_size
        states = [self._states[n] for n in active_panels]
        lsd = sum(s.lsd for s in states) / len(states)
        px = states[0].px
        wl = 0.172973
        for n in active_panels:
            g = self._cards[f"ge{n}"].get_geometry()
            if g.get("wavelength_A"):
                wl = g["wavelength_A"]
                break
        half = big_det_size / 2.0
        self._cards["composite"].set_geometry({
            "wavelength_A": wl, "pxY": px, "Lsd": lsd,
            "BC_y": half, "BC_z": half, "tx": 0.0, "ty": 0.0, "tz": 0.0,
            "NrPixelsY": big_det_size, "NrPixelsZ": big_det_size,
            "distortion": {}, "im_trans": [],
        })

    # ── Signal handlers ──────────────────────────────────────────

    def _on_siblings_changed(self, siblings: dict):
        self._ensure_states_for_siblings(siblings)
        self._toolbar.set_available(siblings.keys())
        self._composite_img = None
        self._composite_seeded_size = None
        self._refresh_display()
        self._refresh_profile_curves()

    def _on_frame_changed(self, _idx: int):
        self._composite_img = None
        self._refresh_display()
        self._refresh_profile_curves()

    def _on_panel_changed(self, key: str):
        """Only the image viewer (one image at a time) rebinds when the
        active panel changes — the profile-plot/radial-controls bindings
        are permanent (set once per card in _build_ui) so all 5 curves stay
        live regardless of which panel is currently displayed."""
        if self._active_card is not None:
            self._active_card.set_viewer(None)
        self._card_stack.setCurrentWidget(self._cards[key])
        self._active_card = self._cards[key]
        self._active_card.set_viewer(self._viewer)
        self._refresh_display()

    def _on_card_geometry_changed(self, n: int):
        """A ge1-4 card's geometry changed (BC edit/pick or calibration
        file load) — sync it into the matching DetectorState, force the
        composite (which depends on every panel's geometry) to rebuild, and
        refresh the derived Composite curve (that panel's own curve already
        refreshed itself via the card's normal geometry-change handling)."""
        card = self._cards.get(f"ge{n}")
        if card is None:
            return
        fields = card.get_full_geometry()
        if fields is None:
            return
        st = self._states.setdefault(n, hydra.DetectorState())
        st.load_from_geometry_dict(fields)
        self._composite_img = None
        if self._toolbar.current() == "composite":
            self._refresh_display()
        self._refresh_composite_curve()

    def _refresh_profile_curves(self, force: bool = False):
        """(Re)load every available panel's frame and — if Auto is on (or
        `force`, from the Integrate button) — reintegrate it, independent
        of which panel is currently the *active* one for image display.
        Then refresh the derived Composite curve."""
        siblings = self._loader.siblings()
        for n in (1, 2, 3, 4):
            if n not in siblings:
                self._profile_view.clear_curve(f"ge{n}")
                continue
            img = self._load_panel_frame(n)
            card = self._cards[f"ge{n}"]
            if img is not None and (force or self._rad_auto.isChecked()):
                card.radial_integrate()
        self._refresh_composite_curve()

    def _refresh_composite_curve(self):
        """Recompute the toggleable "Composite" curve: each available
        panel's own (already-computed) profile, converted to a shared
        2theta axis, resampled onto one common grid, and NaN-aware summed —
        not a radial integration of the composited image (that would
        double-count any panel overlap and mix registration error into the
        profile). Deliberately reads back the per-panel curves already
        pushed into HydraProfileViewer rather than recomputing them here."""
        if not self._profile_view.composite_visible():
            self._profile_view.clear_curve("composite")
            return
        natives = []
        for n in (1, 2, 3, 4):
            data = self._profile_view.get_native(f"ge{n}")
            if data is not None and None not in data[2:]:   # need lsd, px, wl
                natives.append(data)
        if not natives:
            self._profile_view.clear_curve("composite")
            return
        grids = []
        for r_px, profile, lsd, px, wl in natives:
            tth = _convert_radial(r_px, lsd, px, wl, "R", "2th")
            order = np.argsort(tth)
            grids.append((tth[order], profile[order]))
        lo = min(g[0].min() for g in grids)
        hi = max(g[0].max() for g in grids)
        common = np.linspace(lo, hi, 500)
        resampled = [np.interp(common, tth, profile, left=np.nan, right=np.nan)
                    for tth, profile in grids]
        stacked = np.vstack(resampled)
        all_nan = np.all(np.isnan(stacked), axis=0)
        summed = np.where(all_nan, np.nan, np.nansum(stacked, axis=0))
        # Push through the normal set_curve(r_px, ...) API using the first
        # contributing panel's geometry as an arbitrary-but-consistent
        # reference — round-trips correctly for any X-axis unit selection
        # since it's the exact inverse of the R -> 2theta conversion above.
        ref_lsd, ref_px, ref_wl = natives[0][2], natives[0][3], natives[0][4]
        r_ref = ref_lsd * np.tan(np.radians(common)) / ref_px
        self._profile_view.set_curve("composite", r_ref, summed,
                                     lsd_um=ref_lsd, px_um=ref_px, wavelength_A=ref_wl)

    def _refresh_display(self):
        key = self._toolbar.current()
        if key == "composite":
            self._build_composite_if_needed()
            img = self._composite_img
        else:
            img = self._load_panel_frame(int(key[2]))
        if img is None:
            return
        disp_key = (img.shape, key)
        fresh = (self._disp_key != disp_key)
        self._disp_key = disp_key
        self._viewer.set_image(img, autorange=fresh)
        if self._active_card is not None:
            self._active_card.refresh_rings_and_radial()

    # ── GUI state ────────────────────────────────────────────────

    def get_state(self) -> dict:
        cards = {}
        for key, card in self._cards.items():
            fields = widgets_to_dict(card.state_widgets())
            fields["materials"] = card.materials_state()
            cards[key] = fields
        return {
            "anchor_path": self._loader.current_path(),
            "active_panel": self._toolbar.current(),
            "rad_r_bin": self._rad_r_bin.value(),
            "rad_auto": self._rad_auto.isChecked(),
            "cards": cards,
        }

    def set_state(self, state: dict):
        if not state:
            return
        for key, fields in (state.get("cards") or {}).items():
            card = self._cards.get(key)
            if card is None:
                continue
            calib_path = fields.get("calib_ed")
            if calib_path:
                card.set_calib_path(calib_path)
            materials = fields.get("materials")
            if materials:
                card.set_materials(materials)
            apply_dict_to_widgets(card.state_widgets(), fields)
            card.refresh_geometry()
        self._rad_r_bin.setValue(state.get("rad_r_bin", self._rad_r_bin.value()))
        self._rad_auto.setChecked(state.get("rad_auto", self._rad_auto.isChecked()))
        anchor = state.get("anchor_path")
        if anchor and Path(anchor).exists():
            self._loader.set_path(anchor)
        panel = state.get("active_panel")
        if panel:
            self._toolbar.set_current(panel)
