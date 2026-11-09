"""Pole figure (3-D): where this scan's scattering vectors point.

For a chosen 2theta, every (omega, eta) the scan sampled maps to one point
on the unit sphere -- the tip of the scattering vector in sample
coordinates. Drawn as a 3-D scatter or mesh, optionally coloured by any
catalog stream, with all four GE panels overlaid.

Inputs are a cake_parameters CSV (for the eta range and step) and omega
from either a chosen metadata stream or OME_START/OME_STEP/OME_SUM. Sibling
per-panel CSVs are discovered automatically (ge1 -> ge2/3/4).

NOT the same thing as the Texture tab's pole figure, and the two are
deliberately told apart. Texture draws a stereographic alpha/beta
projection from ONE frame's azimuthal intensity for one ring; this draws
the 3-D locus of sampled orientations ACROSS omega for a whole scan. The
class name, the "Pole figure (3-D)" tab label and the panel's own intro
line all say which is which, because "two pole figures" is otherwise a
reasonable thing to file as duplication.

Ported from ``mpe_wf_saxs_waxs/gui_data_explorer.py`` (1942-2659), with the
frame-count fallback corrected (see ``_infer_raw_frame_count``) and the
deprecated ``cm.get_cmap`` call made version-proof.

Matplotlib rather than pyqtgraph, as the third such view in this project
after ``peak_fit_panel`` and ``tab_zarrviewer``: the 3-D axes, the
depth-aware backside masking and the colorbar/legend machinery have no
pyqtgraph equivalent short of a GL rewrite. NO module-level rcParams
mutation -- the source had one, and two other host modules use matplotlib
and would silently inherit it.
"""
from __future__ import annotations

import math
import os
import re
from typing import Optional

import numpy as np
import matplotlib
matplotlib.use("Qt5Agg")
import matplotlib.cm  # noqa: E402
import matplotlib.colors  # noqa: E402
from matplotlib.backends.backend_qt5agg import (  # noqa: E402
    FigureCanvasQTAgg as FigureCanvas,
    NavigationToolbar2QT as NavigationToolbar)
from matplotlib.figure import Figure  # noqa: E402
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401, E402  (registers '3d')
from PyQt5 import QtCore, QtWidgets  # noqa: E402

from midas_gui.metadata_catalog import MetadataCatalog, StreamInfo  # noqa: E402
from midas_gui.peak_fit_data import peak_fit_omega_eta_grid  # noqa: E402
from midas_gui.stream_pickers import (  # noqa: E402
    ColorSourceRow, MetadataStreamPicker)
from midas_gui.stream_sources import (  # noqa: E402
    discover_cake_siblings, infer_frame_count, is_zarr, parse_cake_csv,
    read_dataset_1d, source_exists)

try:
    import h5py
except ImportError:                                  # pragma: no cover
    h5py = None


def _omega_centers_from_array(vals: np.ndarray, n_frames: int,
                              ome_sum: int) -> Optional[np.ndarray]:
    """Reduce raw per-frame omega values into per-output-frame centers."""
    try:
        vals = np.asarray(vals, dtype=float).ravel()
    except Exception:
        return None
    if vals.size == 0:
        return None
    n_raw = max(1, int(n_frames))
    n_out = max(1, n_raw // max(1, ome_sum))
    if ome_sum > 1 and vals.size >= n_raw:
        raw = vals[:n_raw]
        trim = (raw.size // ome_sum) * ome_sum
        if trim <= 0:
            return None
        return raw[:trim].reshape(-1, ome_sum).mean(axis=1)
    if vals.size == n_out:
        return vals
    if vals.size >= n_frames:
        return vals[:n_frames]
    if vals.size > n_out:
        return vals[:n_out]
    return vals


# Substrings (case-insensitive) hinting that a 1-D stream contains omega values.
_OMEGA_HINTS = ('/omegas', 'samry', 'omega')


def _looks_like_omega(stream: StreamInfo) -> bool:
    if stream.kind not in ('1d', 'aux'):
        return False
    label_lower = stream.label.lower()
    return any(h in label_lower for h in _OMEGA_HINTS)


class PoleFigure3DPanel(QtWidgets.QWidget):
    """Pole-figure preview driven by the shared ``MetadataCatalog``.

    Reads η from the cake CSV in the catalog and ω from the
    user-selected metadata stream (defaulting to a 1-D stream whose
    path contains ``/Omegas`` / ``samRy`` / ``omega``). The surface
    colour picker can paint each (ω, η) cell with any catalog stream
    (CSV columns → exact per-cell sample; 1-D → broadcast across η).
    Default is solid per-panel colours, preserving today's render.
    """

    def __init__(self, catalog: 'MetadataCatalog', parent=None):
        super().__init__(parent)
        self._catalog = catalog
        self._view_axis = 'xp'
        self._free_view_elev: Optional[float] = None
        self._free_view_azim: Optional[float] = None
        self._last_view_elev: Optional[float] = None
        self._last_view_azim: Optional[float] = None
        self._title_fontsize = 15
        self._anno_fontsize = 14
        self._view_buttons: dict = {}
        self._nframes_auto = True
        self._build_ui()
        catalog.catalogChanged.connect(self._on_catalog_changed)

    def _build_ui(self):
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(8)

        # Omega-source stream picker (no extras; default to a stream
        # whose path looks like omega data).
        omega_box = QtWidgets.QGroupBox('Omega centers (per-frame ω source)')
        omega_lay = QtWidgets.QVBoxLayout(omega_box)
        omega_lay.setContentsMargins(8, 4, 8, 4)
        self.omega_picker = MetadataStreamPicker(
            self._catalog, role='Omega', kinds=('1d', 'aux'),
            allow_none=True, default_match=_looks_like_omega)
        self.omega_picker.streamChanged.connect(self._refresh)
        omega_lay.addWidget(self.omega_picker)
        omega_help = QtWidgets.QLabel(
            'Blank → fall back to OME_START/OME_STEP from the cake CSV.')
        omega_help.setStyleSheet('color: gray;')
        omega_lay.addWidget(omega_help)
        v.addWidget(omega_box)

        # Surface-color stream picker. Default = '(panel colors)' so the
        # initial render matches today (solid colour per panel).
        surface_box = QtWidgets.QGroupBox('Surface color')
        surface_lay = QtWidgets.QVBoxLayout(surface_box)
        surface_lay.setContentsMargins(8, 4, 8, 4)
        # Compound picker (Metadata / Peak fit radio) — same shape as the
        # Real-space Color/Size pickers. The extra_default (panel colors)
        # sits in Metadata mode; switching to Peak fit exposes the
        # peak/param/η selectors so the sphere can be colored by any
        # peak-fit CSV column, sampled on the (ω, η) lattice via
        # peak_fit_omega_eta_grid.
        self.surface_picker = ColorSourceRow(
            self._catalog, role='Color',
            allow_none=False,
            extra_default=('panels', '(panel colors — default)'),
            fit_eta_disabled=True)
        self.surface_picker.streamChanged.connect(self._refresh)
        surface_lay.addWidget(self.surface_picker)
        v.addWidget(surface_box)

        # 2θ + view controls + frame count.
        param_row = QtWidgets.QHBoxLayout()
        param_row.addWidget(QtWidgets.QLabel('2θ (deg):'))
        self.twotheta_edit = QtWidgets.QLineEdit('10.0')
        self.twotheta_edit.setFixedWidth(60)
        self.twotheta_edit.editingFinished.connect(self._refresh)
        param_row.addWidget(self.twotheta_edit)
        param_row.addSpacing(12)
        param_row.addWidget(QtWidgets.QLabel('View:'))
        self._view_btn_group = QtWidgets.QButtonGroup(self)
        self._view_btn_group.setExclusive(True)
        view_specs = [
            ('+X', 'xp', 'Front view (+X toward viewer)'),
            ('-X', 'xn', 'Back view (-X toward viewer)'),
            ('+Y', 'yp', 'Right view (+Y toward viewer)'),
            ('-Y', 'yn', 'Left view (-Y toward viewer)'),
            ('+Z', 'zp', 'Top view (+Z toward viewer)'),
            ('-Z', 'zn', 'Bottom view (-Z toward viewer)'),
            ('ISO', 'iso', 'Isometric view'),
            ('Free', 'free', 'Free spin mode (manual rotation)'),
        ]
        for label, code, tip in view_specs:
            btn = QtWidgets.QPushButton(label)
            btn.setCheckable(True)
            btn.setToolTip(tip)
            btn.clicked.connect(
                lambda _checked=False, c=code: self._set_projection_view(c))
            self._view_btn_group.addButton(btn)
            self._view_buttons[code] = btn
            param_row.addWidget(btn)
        if 'xp' in self._view_buttons:
            self._view_buttons['xp'].setChecked(True)
        param_row.addSpacing(12)
        self.clean_view_cb = QtWidgets.QCheckBox('Hide axes/grid')
        self.clean_view_cb.setChecked(True)
        self.clean_view_cb.toggled.connect(self._refresh)
        param_row.addWidget(self.clean_view_cb)
        self.hide_backside_cb = QtWidgets.QCheckBox('Hide backside')
        self.hide_backside_cb.setToolTip(
            'Only draw points on the camera-facing hemisphere.')
        self.hide_backside_cb.toggled.connect(self._on_hide_backside_toggled)
        param_row.addWidget(self.hide_backside_cb)
        param_row.addSpacing(12)
        param_row.addWidget(QtWidgets.QLabel('Style:'))
        self.pf_style_combo = QtWidgets.QComboBox()
        self.pf_style_combo.addItems(['Scatter', 'Mesh'])
        self.pf_style_combo.setToolTip(
            'Scatter: one dot per (ω, η) cell (marker size below).\n'
            'Mesh: continuous colored surface over the sphere '
            '(requires a Surface color stream).')
        self.pf_style_combo.currentIndexChanged.connect(
            self._on_pf_style_changed)
        param_row.addWidget(self.pf_style_combo)
        param_row.addSpacing(12)
        param_row.addWidget(QtWidgets.QLabel('Marker size:'))
        self.glyph_size_spin = QtWidgets.QDoubleSpinBox()
        self.glyph_size_spin.setRange(1.0, 400.0)
        self.glyph_size_spin.setDecimals(1)
        self.glyph_size_spin.setSingleStep(1.0)
        self.glyph_size_spin.setValue(30.0)
        self.glyph_size_spin.setToolTip(
            'Scatter marker base size for pole-figure points.')
        self.glyph_size_spin.valueChanged.connect(lambda _v: self._refresh())
        param_row.addWidget(self.glyph_size_spin)
        param_row.addSpacing(12)
        param_row.addWidget(QtWidgets.QLabel('# raw frames:'))
        self.nframes_edit = QtWidgets.QLineEdit('1')
        self.nframes_edit.setFixedWidth(70)
        self.nframes_edit.setToolTip(
            'Auto-filled from source and snapped to OME_SUM. '
            'Editing this value disables auto mode.')
        self.nframes_edit.textEdited.connect(self._on_nframes_text_edited)
        self.nframes_edit.editingFinished.connect(self._on_nframes_editing_finished)
        param_row.addWidget(self.nframes_edit)
        self.nframes_total_lbl = QtWidgets.QLabel('/ —')
        param_row.addWidget(self.nframes_total_lbl)
        self.nframes_auto_cb = QtWidgets.QCheckBox('Auto')
        self.nframes_auto_cb.setChecked(True)
        self.nframes_auto_cb.toggled.connect(self._on_nframes_auto_toggled)
        param_row.addWidget(self.nframes_auto_cb)
        param_row.addStretch(1)
        v.addLayout(param_row)

        # No tight_layout: we place ax_pf + colorbar with explicit
        # add_axes rects in _refresh so the sphere stays centered under
        # font scaling and colorbar toggling.
        self.fig = Figure(figsize=(5, 5))
        self.canvas = FigureCanvas(self.fig)
        self.canvas.setMinimumHeight(360)
        self.canvas.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                  QtWidgets.QSizePolicy.Expanding)
        self.toolbar = NavigationToolbar(self.canvas, self)
        v.addWidget(self.toolbar)
        v.addWidget(self.canvas, 1)

        self.canvas.mpl_connect('button_release_event',
                                self._on_canvas_mouse_release)
        self._refresh()

    # ── catalog reactivity ──────────────────────────────────────────
    def _on_catalog_changed(self):
        self._refresh()

    # ── nframes plumbing ────────────────────────────────────────────
    def _on_nframes_text_edited(self, _text: str):
        if not self.nframes_auto_cb.isChecked():
            self._nframes_auto = False

    def _on_nframes_editing_finished(self):
        if not self.nframes_edit.text().strip():
            self._nframes_auto = True
            self.nframes_auto_cb.setChecked(True)
        self._refresh()

    def _on_nframes_auto_toggled(self, checked: bool):
        self._nframes_auto = bool(checked)
        self.nframes_edit.setReadOnly(bool(checked))
        self._refresh()

    def _n_frames(self) -> int:
        try:
            return max(1, int(self.nframes_edit.text().strip()))
        except ValueError:
            return 1

    # ── External entry points (used by gui_caking_launcher) ─────────
    def set_n_frames(self, n: int) -> None:
        """Public setter for the frame-count field; turns off auto-detect."""
        try:
            n_int = max(1, int(n))
        except (TypeError, ValueError):
            return
        self._nframes_auto = False
        if self.nframes_auto_cb.isChecked():
            # Block so the toggle doesn't re-enter _refresh before we set text.
            blocker = QtCore.QSignalBlocker(self.nframes_auto_cb)
            self.nframes_auto_cb.setChecked(False)
            del blocker
        self.nframes_edit.setReadOnly(False)
        if self.nframes_edit.text().strip() != str(n_int):
            self.nframes_edit.setText(str(n_int))
        self._refresh()

    def set_cake_csv(self, path: str) -> None:
        """Public setter for the cake-CSV path (delegates to the catalog)."""
        self._catalog.set_cake_csv((path or '').strip())

    # ── view buttons ────────────────────────────────────────────────
    def _set_projection_view(self, axis: str):
        if axis in ('xp', 'xn', 'yp', 'yn', 'zp', 'zn', 'iso', 'free'):
            self._view_axis = axis
            btn = self._view_buttons.get(axis)
            if btn is not None and not btn.isChecked():
                btn.setChecked(True)
            self._refresh()

    def _on_canvas_mouse_release(self, event):
        ax = getattr(event, 'inaxes', None)
        if ax is None or not hasattr(ax, 'elev') or not hasattr(ax, 'azim'):
            return
        elev = float(getattr(ax, 'elev', 0.0))
        azim = float(getattr(ax, 'azim', 0.0))
        if (self._last_view_elev is not None and self._last_view_azim is not None
                and abs(elev - self._last_view_elev) < 1e-6
                and abs(azim - self._last_view_azim) < 1e-6):
            return
        self._free_view_elev = elev
        self._free_view_azim = azim
        self._last_view_elev = elev
        self._last_view_azim = azim
        if self._view_axis != 'free':
            self._view_axis = 'free'
            btn = self._view_buttons.get('free')
            if btn is not None and not btn.isChecked():
                btn.setChecked(True)

    def _on_hide_backside_toggled(self, checked: bool):
        if checked and self._view_axis == 'free':
            snap = self._nearest_standard_view()
            if snap is not None:
                self._set_projection_view(snap)
                return
        self._refresh()

    def _on_pf_style_changed(self, *_):
        """Mesh only makes sense when the surface has continuous colors —
        grey the marker-size spinbox for Mesh and refresh."""
        is_mesh = self.pf_style_combo.currentText() == 'Mesh'
        self.glyph_size_spin.setEnabled(not is_mesh)
        self._refresh()

    def _nearest_standard_view(self) -> Optional[str]:
        elev = self._free_view_elev if self._free_view_elev is not None else self._last_view_elev
        azim = self._free_view_azim if self._free_view_azim is not None else self._last_view_azim
        if elev is None or azim is None:
            return None
        e = np.deg2rad(float(elev))
        a = np.deg2rad(float(azim))
        vx = np.cos(e) * np.cos(a)
        vy = np.cos(e) * np.sin(a)
        vz = np.sin(e)
        iso_d = np.cos(np.deg2rad(30.0))
        candidates = {
            'xp': (1.0, 0.0, 0.0),
            'xn': (-1.0, 0.0, 0.0),
            'yp': (0.0, 1.0, 0.0),
            'yn': (0.0, -1.0, 0.0),
            'zp': (0.0, 0.0, 1.0),
            'zn': (0.0, 0.0, -1.0),
            'iso': (iso_d * np.cos(np.deg2rad(45.0)),
                    iso_d * np.sin(np.deg2rad(45.0)),
                    np.sin(np.deg2rad(30.0))),
        }
        best_code, _ = max(
            candidates.items(),
            key=lambda kv: vx * kv[1][0] + vy * kv[1][1] + vz * kv[1][2])
        return best_code

    def _apply_projection_view(self, ax):
        views = {
            'xp': (0, 0), 'xn': (0, 180),
            'yp': (0, 90), 'yn': (0, -90),
            'zp': (90, -90), 'zn': (-90, -90),
            'iso': (30, 45),
        }
        if self._view_axis == 'free':
            if self._free_view_elev is not None and self._free_view_azim is not None:
                ax.view_init(elev=self._free_view_elev, azim=self._free_view_azim)
        else:
            elev, azim = views.get(self._view_axis, views['xp'])
            ax.view_init(elev=elev, azim=azim)
        self._last_view_elev = float(getattr(ax, 'elev', 0.0))
        self._last_view_azim = float(getattr(ax, 'azim', 0.0))

    def _visible_front_mask(self, qx, qy, qz, ax) -> np.ndarray:
        elev = np.deg2rad(float(getattr(ax, 'elev', 0.0)))
        azim = np.deg2rad(float(getattr(ax, 'azim', 0.0)))
        vx = np.cos(elev) * np.cos(azim)
        vy = np.cos(elev) * np.sin(azim)
        vz = np.sin(elev)
        return (qx * vx + qy * vy + qz * vz) >= 0.0

    # ── omega-centers resolution via catalog ────────────────────────
    def _resolve_omega_centers(self, cake: dict, n_frames: int
                               ) -> tuple[np.ndarray, int, bool]:
        ome_sum = max(1, int(cake.get('OME_SUM', 1) or 1))
        if self.omega_picker.is_active():
            raw = self.omega_picker.value(n_frames=n_frames)
            if raw is not None:
                centers = _omega_centers_from_array(raw, n_frames, ome_sum)
                if centers is not None and centers.size > 0:
                    return centers, ome_sum, True
        ome_start = cake.get('OME_START', 0.0)
        ome_step = cake.get('OME_STEP', 0.0)
        n_ome = max(1, n_frames // ome_sum)
        idx = np.arange(n_ome)
        centers = (ome_start
                   + (idx * ome_sum + (ome_sum - 1) / 2.0) * ome_step)
        return centers, ome_sum, False

    def _infer_raw_frame_count(self) -> Optional[int]:
        """Frames in the primary source, for the "# raw frames" auto-fill.

        The picker-selected omega stream wins when there is one -- its
        length IS the frame count by construction. Otherwise defer to
        stream_sources.infer_frame_count, which is the corrected version of
        what used to be inlined here: the original read
        IntegrationResult/FrameNr_0's shape[0], and in a MIDAS archive that
        array is (nR, nEta), so it returned the radial bin count -- 3520
        instead of 64 on a real file.
        """
        if self.omega_picker.is_active():
            arr = self.omega_picker.value(n_frames=None)
            if arr is not None and arr.size > 0:
                return int(arr.size)
        return infer_frame_count(self._catalog.primary_path)
    def _autopopulate_n_frames(self, cake: dict) -> None:
        raw = self._infer_raw_frame_count()
        self.nframes_total_lbl.setText(
            f'/ {raw}' if raw and raw > 0 else '/ —')
        if not self._nframes_auto or raw is None or raw <= 0:
            return
        ome_sum = max(1, int(cake.get('OME_SUM', 1) or 1))
        snapped = max(1, (raw // ome_sum) * ome_sum)
        if snapped != self._n_frames():
            self.nframes_edit.setText(str(snapped))
        if snapped != raw:
            self.nframes_edit.setToolTip(
                f'Auto-filled: raw={raw}, snapped={snapped} (OME_SUM={ome_sum}).')
        else:
            self.nframes_edit.setToolTip(
                f'Auto-filled from source: {snapped} frames (OME_SUM={ome_sum}).')

    # ── main draw ───────────────────────────────────────────────────
    def _refresh(self):
        cake_path = self._catalog.cake_csv_path
        siblings = discover_cake_siblings(cake_path) if cake_path else []

        if self._view_axis == 'free' and self.fig.axes:
            old_ax = self.fig.axes[0]
            self._free_view_elev = float(getattr(old_ax, 'elev', 0.0))
            self._free_view_azim = float(getattr(old_ax, 'azim', 0.0))
            self._last_view_elev = self._free_view_elev
            self._last_view_azim = self._free_view_azim

        self.fig.clear()
        # Explicit placement — tight_layout shifts the 3D axis around
        # when a colorbar is present, and at larger font sizes the sphere
        # ends up drifting to the corner. Fixed rect keeps it centered in
        # the left band regardless of font scale.
        ax_pf = self.fig.add_axes(
            [0.02, 0.04, 0.78, 0.86], projection='3d')

        if not siblings:
            ax_pf.text2D(0.5, 0.5,
                         'No cake CSV loaded.\nSet one in the Sources tab.',
                         ha='center', va='center', transform=ax_pf.transAxes,
                         color='gray', fontsize=self._anno_fontsize)
            ax_pf.set_axis_off()
            self.canvas.draw_idle()
            return

        panels: list = []
        for lbl, p in siblings:
            d = parse_cake_csv(p)
            if d:
                panels.append((lbl, d))
        if not panels:
            ax_pf.text2D(0.5, 0.5, 'Cake CSV unreadable.',
                         ha='center', va='center', transform=ax_pf.transAxes,
                         color='gray', fontsize=self._anno_fontsize)
            ax_pf.set_axis_off()
            self.canvas.draw_idle()
            return

        try:
            two_theta = math.radians(float(self.twotheta_edit.text() or '10'))
        except ValueError:
            two_theta = math.radians(10.0)

        self._autopopulate_n_frames(panels[0][1])
        n_frames = self._n_frames()
        th = two_theta / 2.0

        # Set the camera BEFORE drawing the wireframe so
        # _draw_sample_frame can mask the backside grid against the
        # actual view direction.
        self._apply_projection_view(ax_pf)
        self._draw_sample_frame(ax_pf)

        colors = ('tab:blue', 'tab:orange', 'tab:green', 'tab:red',
                  'tab:purple', 'tab:brown')
        eta_lo_all, eta_hi_all = math.inf, -math.inf
        ome_lo_all, ome_hi_all = math.inf, -math.inf
        ome_sum_all: set = set()
        n_omega_all: list = []
        used_metadata_omega = False

        surface_active = self.surface_picker.is_active()
        surface_label = (self.surface_picker.current_label()
                         if surface_active else None)
        style = self.pf_style_combo.currentText()
        # Mesh needs a color source to paint faces with — silently fall back
        # to scatter for the "(panel colors)" default.
        if style == 'Mesh' and not surface_active:
            style = 'Scatter'
        last_sc = None
        glyph_size = self.glyph_size_spin.value()

        # For Mesh mode we normalize colors across ALL panels so the
        # colorbar spans the full data range. Collect finite values in a
        # first pass, then draw in a second pass with that shared norm.
        mesh_norm = None
        if style == 'Mesh':
            all_vals: list = []
            for _lbl, cake in panels:
                oc, _, _ = self._resolve_omega_centers(cake, n_frames)
                emin = cake.get('ETA_MIN', -180.0)
                emax = cake.get('ETA_MAX', 180.0)
                estep = cake.get('ETA_STEP', 1.0)
                estep_abs = max(abs(estep), 1e-9)
                nbins = max(1, int(round(abs(emax - emin) / estep_abs)))
                esign = 1.0 if estep >= 0 else -1.0
                ec = emin + (np.arange(nbins) + 0.5) * estep_abs * esign
                vals = self.surface_picker.grid_value(oc, ec)
                if vals is not None:
                    v = np.asarray(vals, dtype=float).ravel()
                    v = v[np.isfinite(v)]
                    if v.size:
                        all_vals.append(v)
            if all_vals:
                cat = np.concatenate(all_vals)
                vmin = float(cat.min()); vmax = float(cat.max())
                if vmax <= vmin:
                    vmax = vmin + 1e-9
                mesh_norm = matplotlib.colors.Normalize(vmin=vmin, vmax=vmax)
        # matplotlib.colormaps is the 3.7+ API; cm.get_cmap is deprecated
        # in 3.9 and removed later. The env pins 3.8.4, where both work.
        mesh_cmap = (matplotlib.colormaps['viridis']
                     if hasattr(matplotlib, 'colormaps')
                     else matplotlib.cm.get_cmap('viridis'))

        for i, (lbl, cake) in enumerate(panels):
            eta_min = cake.get('ETA_MIN', -180.0)
            eta_max = cake.get('ETA_MAX', 180.0)
            eta_step = cake.get('ETA_STEP', 1.0)

            ome_centers_deg, ome_sum, used_meta = self._resolve_omega_centers(
                cake, n_frames)
            ome_sum_all.add(ome_sum)
            n_omega_all.append(int(ome_centers_deg.size))
            used_metadata_omega = used_metadata_omega or used_meta

            eta_step_abs = max(abs(eta_step), 1e-9)
            n_eta_bins = max(1, int(round(abs(eta_max - eta_min) / eta_step_abs)))
            eta_idx = np.arange(n_eta_bins)
            eta_sign = 1.0 if eta_step >= 0 else -1.0
            eta_centers_deg = eta_min + (eta_idx + 0.5) * eta_step_abs * eta_sign

            eta_arr = np.deg2rad(eta_centers_deg)
            ome_arr = np.deg2rad(ome_centers_deg)
            E, O = np.meshgrid(eta_arr, ome_arr, indexing='xy')

            qx_lab = -np.sin(th) * np.ones_like(E)
            qy_lab = np.cos(th) * np.cos(E)
            qz_lab = np.cos(th) * np.sin(E)
            co = np.cos(O); so = np.sin(O)
            qx_s = co * qx_lab + so * qy_lab
            qy_s = -so * qx_lab + co * qy_lab
            qz_s = qz_lab

            surface_vals = None
            if surface_active:
                surface_vals = self.surface_picker.grid_value(
                    ome_centers_deg, eta_centers_deg)

            if style == 'Mesh' and surface_vals is not None and mesh_norm is not None:
                # Continuous colored surface. Each face is one (ω, η) cell
                # bordered by 4 grid vertices; matplotlib's plot_surface
                # accepts a per-face facecolors array of shape
                # (n_omega-1, n_eta-1, 4). We compute it from the
                # cell-center values by taking the (i,j) sample.
                Z = np.asarray(surface_vals, dtype=float)
                if Z.shape != qx_s.shape:
                    # Grid mismatch (shouldn't happen) — fall through to
                    # scatter for this panel.
                    ax_pf.scatter(
                        qx_s.ravel(), qy_s.ravel(), qz_s.ravel(),
                        s=glyph_size, c=colors[i % len(colors)],
                        alpha=0.75, depthshade=False)
                else:
                    face_vals = Z[:-1, :-1] if Z.shape[0] > 1 and Z.shape[1] > 1 else Z
                    rgba = mesh_cmap(mesh_norm(face_vals))
                    finite = np.isfinite(face_vals)
                    rgba[..., 3] = np.where(finite, 0.95, 0.0)
                    if self.hide_backside_cb.isChecked():
                        cx = 0.25 * (qx_s[:-1, :-1] + qx_s[1:, :-1]
                                     + qx_s[:-1, 1:] + qx_s[1:, 1:])
                        cy = 0.25 * (qy_s[:-1, :-1] + qy_s[1:, :-1]
                                     + qy_s[:-1, 1:] + qy_s[1:, 1:])
                        cz = 0.25 * (qz_s[:-1, :-1] + qz_s[1:, :-1]
                                     + qz_s[:-1, 1:] + qz_s[1:, 1:])
                        front = self._visible_front_mask(cx, cy, cz, ax_pf)
                        rgba[..., 3] = np.where(front, rgba[..., 3], 0.0)
                    ax_pf.plot_surface(
                        qx_s, qy_s, qz_s,
                        facecolors=rgba, shade=False, antialiased=False,
                        linewidth=0.0, rcount=qx_s.shape[0],
                        ccount=qx_s.shape[1])
                    if last_sc is None:
                        # Build a mappable so the shared colorbar can render.
                        last_sc = matplotlib.cm.ScalarMappable(
                            norm=mesh_norm, cmap=mesh_cmap)
                        last_sc.set_array(face_vals)
            else:
                # Scatter (default). Same hide_backside path as before.
                if self.hide_backside_cb.isChecked():
                    front = self._visible_front_mask(qx_s, qy_s, qz_s, ax_pf)
                    qx_s = qx_s[front]
                    qy_s = qy_s[front]
                    qz_s = qz_s[front]
                    if surface_vals is not None:
                        surface_vals = surface_vals[front]

                if surface_vals is not None:
                    sc = ax_pf.scatter(
                        qx_s.ravel(), qy_s.ravel(), qz_s.ravel(),
                        s=glyph_size,
                        c=np.asarray(surface_vals).ravel(),
                        cmap='viridis', alpha=0.9, depthshade=False,
                        label=f'{lbl}  η∈[{eta_min:g},{eta_max:g}]')
                    last_sc = sc
                else:
                    ax_pf.scatter(
                        qx_s.ravel(), qy_s.ravel(), qz_s.ravel(),
                        s=glyph_size, c=colors[i % len(colors)],
                        alpha=0.75, depthshade=False,
                        label=f'{lbl}  η∈[{eta_min:g},{eta_max:g}]')

            eta_lo_all = min(eta_lo_all, eta_min)
            eta_hi_all = max(eta_hi_all, eta_max)
            ome_lo_all = min(ome_lo_all, float(ome_centers_deg[0]))
            ome_hi_all = max(ome_hi_all, float(ome_centers_deg[-1]))

        if surface_active and last_sc is not None and surface_label:
            # Fixed cax to the right of the sphere axes — mirrors the
            # rect chosen for ax_pf so the colorbar doesn't fight with
            # tight_layout for space.
            cax = self.fig.add_axes([0.86, 0.15, 0.03, 0.65])
            self.fig.colorbar(last_sc, cax=cax, label=surface_label)

        ax_pf.set_xlim(-1.1, 1.1)
        ax_pf.set_ylim(-1.1, 1.1)
        ax_pf.set_zlim(-1.1, 1.1)
        try:
            ax_pf.set_box_aspect((1, 1, 1))
        except Exception:
            pass
        if self.clean_view_cb.isChecked():
            self._hide_pole_axes_decoration(ax_pf)
        ax_pf.set_title(
            f'Pole figure  2θ = {math.degrees(two_theta):g}°  '
            f'({len(panels)} panel{"s" if len(panels) != 1 else ""})\n'
            f'η ∈ [{eta_lo_all:g}, {eta_hi_all:g}]   '
            f'ω ∈ [{ome_lo_all:g}, {ome_hi_all:g}]  '
            f'({n_frames} raw frames, ω frames='
            f'{"/".join(str(v) for v in sorted(set(n_omega_all)))}; OME_SUM='
            f'{"/".join(str(v) for v in sorted(ome_sum_all))})\n'
            f'ω source: {"metadata" if used_metadata_omega else "OME_START/STEP"}  '
            f'left-drag to rotate',
            fontsize=self._title_fontsize)
        if len(panels) > 1:
            ax_pf.legend(loc='upper left',
                         fontsize=max(9, self._anno_fontsize - 2),
                         framealpha=0.8, bbox_to_anchor=(0.0, 1.0))

        self.canvas.draw_idle()

    def _hide_pole_axes_decoration(self, ax):
        ax.grid(False)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_zticks([])
        ax.set_axis_off()

    def _draw_sample_frame(self, ax):
        # When Hide backside is checked, drop grid vertices on the far
        # hemisphere so the wireframe/equator/meridian match the point
        # cloud. Vertices with NaN break line segments in matplotlib,
        # so setting the backside to NaN is enough — no per-segment mask.
        hide_back = (hasattr(self, 'hide_backside_cb')
                     and self.hide_backside_cb.isChecked())

        def _mask_back(x, y, z):
            if not hide_back:
                return x, y, z
            front = self._visible_front_mask(x, y, z, ax)
            xm = np.where(front, x, np.nan)
            ym = np.where(front, y, np.nan)
            zm = np.where(front, z, np.nan)
            return xm, ym, zm

        u = np.linspace(0, 2 * np.pi, 36)
        v = np.linspace(0, np.pi, 18)
        xs = np.outer(np.cos(u), np.sin(v))
        ys = np.outer(np.sin(u), np.sin(v))
        zs = np.outer(np.ones_like(u), np.cos(v))
        xs, ys, zs = _mask_back(xs, ys, zs)
        ax.plot_wireframe(xs, ys, zs, color='lightgray',
                          linewidth=0.4, alpha=0.7)

        t = np.linspace(0, 2 * np.pi, 180)
        eq_x, eq_y, eq_z = _mask_back(np.cos(t), np.sin(t), np.zeros_like(t))
        ax.plot(eq_x, eq_y, eq_z, color='gray', lw=0.7)
        m1_x, m1_y, m1_z = _mask_back(np.cos(t), np.zeros_like(t), np.sin(t))
        ax.plot(m1_x, m1_y, m1_z, color='gray', lw=0.5, alpha=0.5)
        m2_x, m2_y, m2_z = _mask_back(np.zeros_like(t), np.cos(t), np.sin(t))
        ax.plot(m2_x, m2_y, m2_z, color='gray', lw=0.5, alpha=0.5)

        L = 1.25
        ax.quiver(0, 0, 0, L, 0, 0, color='red', arrow_length_ratio=0.08, lw=1.2)
        ax.quiver(0, 0, 0, 0, L, 0, color='green', arrow_length_ratio=0.08, lw=1.2)
        ax.quiver(0, 0, 0, 0, 0, L, color='blue', arrow_length_ratio=0.08, lw=1.2)
        ax.text(L * 1.05, 0, 0, 'Xs', color='red', fontsize=self._anno_fontsize)
        ax.text(0, L * 1.05, 0, 'Ys', color='green', fontsize=self._anno_fontsize)
        ax.text(0, 0, L * 1.05, 'Zs', color='blue', fontsize=self._anno_fontsize)

        ax.plot([-L, L], [0, 0], [0, 0], color='orange',
                lw=1.0, ls='--', alpha=0.7)
        ax.text(-L * 1.1, 0, 0, 'beam', color='orange',
                fontsize=max(10, self._anno_fontsize - 1))

