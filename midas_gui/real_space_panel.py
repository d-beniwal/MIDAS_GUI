"""Real space: where on the sample is this signal?

Pick up to three streams as X/Y/Z, scatter the points, and colour and size
each one by any other stream -- a motor value, a reduced 2-D array, a
peak-fit column. Scatter / Mesh / Contour / Contourf, in 2-D or 3-D with
drag-rotate and eight canned viewpoints.

Extracted from ``DataExplorerWidget`` in
``mpe_wf_saxs_waxs/gui_data_explorer.py`` (3045-3939). It was the only one
of that app's panels NOT already a standalone class -- Sources, Pole figure
and Peak fit each took ``(catalog, parent)``, while this was fused into the
widget itself. Three things changed to lift it out:

  * ``self.catalog`` -> ``self._catalog``, matching the other panels.
  * The one back-reference into its sibling --
    ``self.sources_panel.set_span_status(...)`` -- became an injected
    ``span_status_cb``. That inversion is what makes the extraction clean
    rather than circular; the panel now reports upward and knows nothing
    about who is listening.
  * ``_frames_per_primary_file`` deleted in favour of
    ``stream_sources.infer_frame_count``, which is the corrected version
    (the original read a (nR, nEta) array's shape[0] as a frame count).

Added here, not in the source: a MANUAL axis mode. The source could only
take coordinates from a discovered dataset, which is useless for the
archives this app produces from TIFF sources -- they carry no
``instrument/`` motor tree at all, so the axis combos come up empty and the
tab looks broken. An axis can now instead be given as start/step/points,
with X the fastest-varying. See ``_manual_axis_values``.
"""
from __future__ import annotations

import os
import re
from typing import Callable, Optional

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
from PyQt5 import QtCore, QtGui, QtWidgets  # noqa: E402

from midas_gui.metadata_catalog import (  # noqa: E402
    GROUP_BG_COLORS, MetadataCatalog, StreamInfo)
from midas_gui.stream_pickers import (  # noqa: E402
    ColorSourceRow, MetadataStreamPicker, apply_color_limits_to_mappable,
    make_color_range_row, resolve_color_limits)
from midas_gui.stream_sources import (  # noqa: E402
    discover_datasets, infer_frame_count, is_zarr, read_dataset_1d,
    read_h5_dataset, source_exists)

try:
    import h5py
except ImportError:                                  # pragma: no cover
    h5py = None


class RealSpacePanel(QtWidgets.QWidget):
    """Scatter/mesh of sample position, coloured and sized by any stream.

    ``catalog`` first and positional, matching the other panels.
    ``span_status_cb`` receives a one-line description of the multi-file
    span for display elsewhere; ``fnum_provider`` optionally restricts a
    sibling-file scan to a file-number range.
    """

    def __init__(self, catalog: MetadataCatalog, parent=None, *,
                 fnum_provider: Optional[Callable[[], Optional[tuple]]] = None,
                 span_status_cb: Optional[Callable[[str], None]] = None):
        super().__init__(parent)
        self._catalog = catalog
        self._fnum_provider = fnum_provider
        self._span_status_cb = span_status_cb
        self._candidates: list = []
        self._zarr_2d_paths: list = []
        self._axis_combos: dict = {}
        self._axis_edits: dict = {}
        self._manual_widgets: dict = {}
        self._grid_view_axis = 'xp'
        self._grid_free_view_elev = None
        self._grid_free_view_azim = None
        self._grid_last_view_elev = None
        self._grid_last_view_azim = None
        self._grid_view_buttons: dict = {}
        self._build_ui()
        self._catalog.catalogChanged.connect(self._on_catalog_changed)
        self._draw_empty_grid('No spatial axis selected yet.')

    AXES = ('x', 'y', 'z')

    _SIB_RX = re.compile(
        r'^(?P<base>.+?)_(?P<num>\d{4,7})(?P<ext>\.[^.]+)?'
        r'(?P<container>\.h5|\.zarr\.zip|\.zarr)$'
    )

    def _build_ui(self) -> None:
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(8)

        intro = QtWidgets.QLabel(
            'Set the primary source on the Sources tab; pick spatial axes '
            'below. Color/size stream pickers list every catalog stream.')
        intro.setStyleSheet('color: #555;')
        intro.setWordWrap(True)
        v.addWidget(intro)

        # Span-files controls moved to the Sources tab; the Real-space tab
        # reads `catalog.span_enabled` / `catalog.span_reducer` and reports
        # back via `sources_panel.set_span_status(...)`.

        # Spatial axis pickers (X/Y/Z) — fed from catalog streams.
        axes_box = QtWidgets.QGroupBox('Spatial axes')
        grid = QtWidgets.QGridLayout(axes_box)
        grid.setContentsMargins(6, 4, 6, 4)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(3)
        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(2, 2)
        for row_i, ax in enumerate(self.AXES):
            grid.addWidget(QtWidgets.QLabel(f'{ax.upper()}:'), row_i, 0,
                           QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
            cb = QtWidgets.QComboBox()
            cb.setEditable(False)
            cb.addItem('(none)')
            cb.currentIndexChanged.connect(
                lambda _i, a=ax: self._on_axis_combo_changed(a))
            grid.addWidget(cb, row_i, 1)
            ed = QtWidgets.QLineEdit()
            ed.setPlaceholderText('or type dataset path')
            ed.editingFinished.connect(self._refresh_spatial)
            grid.addWidget(ed, row_i, 2)
            self._axis_combos[ax] = cb
            self._axis_edits[ax] = ed

            # Manual grid: start / step / points. Not in the source, which
            # could only take an axis from a discovered dataset -- useless
            # for an archive built from TIFFs, which carries no motor tree
            # at all and so comes up with empty combos.
            man = {}
            for col, (key, tip, dflt) in enumerate((
                    ('start', 'First value on this axis', 0.0),
                    ('step', 'Increment between points', 1.0)), start=3):
                sp = QtWidgets.QDoubleSpinBox()
                sp.setDecimals(4); sp.setRange(-1e9, 1e9); sp.setValue(dflt)
                sp.setToolTip(tip); sp.setFixedWidth(90)
                sp.valueChanged.connect(self._refresh_spatial)
                grid.addWidget(sp, row_i, col)
                man[key] = sp
            n_sp = QtWidgets.QSpinBox()
            n_sp.setRange(0, 10_000_000); n_sp.setValue(0); n_sp.setFixedWidth(80)
            n_sp.setToolTip(
                'Number of points along this axis. 0 = unused (take the '
                'axis from the dataset chosen at left instead).\n\n'
                'X varies FASTEST, then Y, then Z -- the usual raster '
                'order. Getting that backwards gives a transposed map that '
                'still looks plausible, so the implied total is shown '
                'below and checked against the frame count.')
            n_sp.valueChanged.connect(self._on_manual_changed)
            grid.addWidget(n_sp, row_i, 5)
            man['n'] = n_sp
            self._manual_widgets[ax] = man

        for col, text in ((3, 'start'), (4, 'step'), (5, 'points')):
            lbl = QtWidgets.QLabel(text)
            lbl.setStyleSheet('font-size:10px;')
            grid.addWidget(lbl, len(self.AXES), col,
                           QtCore.Qt.AlignHCenter | QtCore.Qt.AlignTop)
        self._manual_note = QtWidgets.QLabel('')
        self._manual_note.setWordWrap(True)
        self._manual_note.setStyleSheet('font-size:10px;')
        grid.addWidget(self._manual_note, len(self.AXES) + 1, 0, 1, 6)
        v.addWidget(axes_box)

        # Color / size mapping — each is a compound row with two parallel
        # pickers (scalar metadata vs peak-fit) and a radio for precedence.
        pickers_box = QtWidgets.QGroupBox('Color & size mapping')
        pickers_lay = QtWidgets.QVBoxLayout(pickers_box)
        pickers_lay.setContentsMargins(6, 4, 6, 4)
        pickers_lay.setSpacing(6)
        self.color_picker = ColorSourceRow(
            self._catalog, role='Color', allow_none=True)
        self.color_picker.streamChanged.connect(self._refresh_spatial)
        pickers_lay.addWidget(self.color_picker)
        self.size_picker = ColorSourceRow(
            self._catalog, role='Size', allow_none=True)
        self.size_picker.streamChanged.connect(self._refresh_spatial)
        pickers_lay.addWidget(self.size_picker)
        v.addWidget(pickers_box)

        # View / projection / style row.
        view_row = QtWidgets.QHBoxLayout()
        view_row.addWidget(QtWidgets.QLabel('View:'))
        self._grid_view_btn_group = QtWidgets.QButtonGroup(self)
        self._grid_view_btn_group.setExclusive(True)
        grid_view_specs = [
            ('+X', 'xp', 'Front view'),
            ('-X', 'xn', 'Back view'),
            ('+Y', 'yp', 'Right view'),
            ('-Y', 'yn', 'Left view'),
            ('+Z', 'zp', 'Top view'),
            ('-Z', 'zn', 'Bottom view'),
            ('ISO', 'iso', 'Isometric view'),
            ('Free', 'free', 'Free spin mode'),
        ]
        for label, code, tip in grid_view_specs:
            btn = QtWidgets.QPushButton(label)
            btn.setCheckable(True)
            btn.setToolTip(tip)
            btn.clicked.connect(
                lambda _checked=False, c=code: self._set_grid_projection_view(c))
            self._grid_view_btn_group.addButton(btn)
            self._grid_view_buttons[code] = btn
            view_row.addWidget(btn)
        if self._grid_view_axis in self._grid_view_buttons:
            self._grid_view_buttons[self._grid_view_axis].setChecked(True)
        view_row.addStretch(1)
        view_row.addSpacing(12)
        view_row.addWidget(QtWidgets.QLabel('Style:'))
        self.plot_style_combo = QtWidgets.QComboBox()
        self.plot_style_combo.addItems(['Scatter', 'Mesh', 'Contour', 'Contourf'])
        self.plot_style_combo.setToolTip(
            'How to render 2-D spatial data. Mesh/Contour/Contourf require a\n'
            'Color stream and scipy (falls back to Scatter if unavailable).')
        self.plot_style_combo.currentIndexChanged.connect(self._refresh_spatial)
        self.plot_style_combo.currentIndexChanged.connect(
            self._update_size_picker_enabled)
        view_row.addWidget(self.plot_style_combo)
        view_row.addSpacing(6)
        view_row.addWidget(QtWidgets.QLabel('Interp:'))
        self.interp_method_combo = QtWidgets.QComboBox()
        self.interp_method_combo.addItems(['linear', 'nearest', 'cubic'])
        self.interp_method_combo.setToolTip(
            'scipy.interpolate.griddata method for Mesh/Contour/Contourf.')
        self.interp_method_combo.currentIndexChanged.connect(self._refresh_spatial)
        view_row.addWidget(self.interp_method_combo)
        view_row.addSpacing(6)
        view_row.addWidget(QtWidgets.QLabel('Aspect:'))
        self.aspect_combo = QtWidgets.QComboBox()
        # 'equal' is matplotlib's data-unit equal aspect (default for this tab
        # historically); 'square' forces a square plot box regardless of data;
        # 'tight' uses 'auto' aspect + ax.axis('tight') to fit data snugly;
        # 'auto' lets matplotlib stretch freely to fill the panel.
        self.aspect_combo.addItems(['equal', 'square', 'tight', 'auto'])
        self.aspect_combo.setToolTip(
            'equal: same scale on X and Y (data units).\n'
            'square: square plot box regardless of data range.\n'
            'tight: free aspect, snap limits to data.\n'
            'auto: free aspect, default matplotlib limits.')
        self.aspect_combo.currentIndexChanged.connect(self._refresh_spatial)
        view_row.addWidget(self.aspect_combo)
        view_row.addSpacing(6)
        cr_row, self.cbar_vmin_edit, self.cbar_vmax_edit = (
            make_color_range_row(self._refresh_spatial))
        view_row.addLayout(cr_row)
        v.addLayout(view_row)

        self.fig_grid = Figure(figsize=(4, 2.4), tight_layout=True)
        self.canvas_grid = FigureCanvas(self.fig_grid)
        self.canvas_grid.setMinimumHeight(220)
        self.canvas_grid.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                       QtWidgets.QSizePolicy.Expanding)
        self.toolbar_grid = NavigationToolbar(self.canvas_grid, self)
        v.addWidget(self.toolbar_grid)
        v.addWidget(self.canvas_grid, 1)
        self.canvas_grid.mpl_connect(
            'button_release_event', self._on_grid_canvas_mouse_release)

        self._update_size_picker_enabled()

    def _update_size_picker_enabled(self, *_):
        """Grey out the Size picker for grid styles that can't render per-point
        marker sizes (Mesh/Contour/Contourf draw a continuous field)."""
        style = self.plot_style_combo.currentText()
        self.size_picker.setEnabled(style == 'Scatter')

    # ── External entry points ───────────────────────────────────────

    def refresh(self):
        """Public re-draw entry point."""
        self._refresh_spatial()

    # Tint palette is the module-level GROUP_BG_COLORS so the axis combos
    # and the stream-picker combos draw groups in the same colour vocabulary.

    @staticmethod
    def _group_key(ds_path: str) -> str:
        """Parent directory of a dataset path; root-level → '/'."""
        idx = ds_path.rfind('/')
        return ds_path[:idx] if idx > 0 else '/'

    def _populate_axis_combo(self, cb: QtWidgets.QComboBox) -> None:
        """Refill ``cb`` with '(none)' + grouped, colour-tinted candidates.
        Item text stays in the legacy ``<path>  [N=<n>]`` format so the
        downstream ``split('  [', 1)`` parser still works."""
        cb.clear()
        cb.addItem('(none)')
        last_group = None
        color_idx = -1
        for entry in self._candidates:
            # Backwards-tolerant: accept (path, n) or (path, n, lo, hi).
            path, n = entry[0], entry[1]
            grp = self._group_key(path)
            if grp != last_group:
                if last_group is not None:
                    cb.insertSeparator(cb.count())
                color_idx = (color_idx + 1) % len(GROUP_BG_COLORS)
                last_group = grp
            cb.addItem(f'{path}  [N={n}]')
            brush = QtGui.QBrush(QtGui.QColor(GROUP_BG_COLORS[color_idx]))
            cb.setItemData(cb.count() - 1, brush, QtCore.Qt.BackgroundRole)
            cb.setItemData(cb.count() - 1, grp, QtCore.Qt.ToolTipRole)

    # ── Catalog reactivity ──────────────────────────────────────────
    def _on_catalog_changed(self):
        # Repopulate axis combos (X/Y/Z) from catalog 1-D streams.
        streams = self._catalog.list_streams(kinds=('1d', 'aux'))
        self._candidates = [
            (self._key_to_path(s.key), s.length_hint or 0)
            for s in streams
        ]
        # Sort by group so visually-clustered tints map to logical groups
        # rather than discovery order (motor-hit-first interleaving).
        self._candidates.sort(key=lambda e: (self._group_key(e[0]), e[0]))
        self._zarr_2d_paths = {
            self._key_to_path(s.key)
            for s in self._catalog.list_streams(kinds=('2d',))
        }
        for ax, cb in self._axis_combos.items():
            cb.blockSignals(True)
            prev = cb.currentText().split('  [', 1)[0]
            self._populate_axis_combo(cb)
            # Try to restore previous selection by path.
            for i in range(cb.count()):
                if cb.itemText(i).split('  [', 1)[0] == prev:
                    cb.setCurrentIndex(i)
                    break
            cb.blockSignals(False)
        self._refresh_spatial()

    @staticmethod
    def _key_to_path(key: str) -> str:
        _, _, rest = key.partition(':')
        return rest

    # ── Axis handling ────────────────────────────────────────────────
    def _on_axis_combo_changed(self, axis: str):
        cb = self._axis_combos[axis]
        text = cb.currentText()
        if text == '(none)' or not text:
            self._axis_edits[axis].setText('')
        else:
            self._axis_edits[axis].setText(text.split('  [', 1)[0])
        self._refresh_spatial()

    def _axis_path(self, axis: str) -> str:
        return self._axis_edits[axis].text().strip()

    def _manual_shape(self) -> dict:
        """``{axis: n_points}`` for every axis given a manual point count."""
        out = {}
        for ax in self.AXES:
            w = self._manual_widgets.get(ax)
            if w and int(w['n'].value()) > 0:
                out[ax] = int(w['n'].value())
        return out

    def _manual_points(self) -> int:
        """How many scan points a manual grid describes.

        ``max(grid total, frames in the primary source)``, and the first
        term is why: a manual grid is how you describe a scan that SPANS
        FILES, so its total is routinely larger than any one file. A
        13 x 64 raster lives in thirteen 64-frame files, and taking the
        frame count would generate 64 points with the slow axis stuck on
        its first value -- one line instead of a grid.

        The other term matters for the opposite case: a single manual axis
        of 10 points against a 100-frame source means "X cycles every 10",
        not "plot 10 points", so the frame count wins there.
        """
        total = 1
        shape = self._manual_shape()
        for ax in self.AXES:
            total *= shape.get(ax, 1)
        return max(total, infer_frame_count(self._catalog.primary_path) or 0)

    def _manual_axis_values(self, axis: str, n_points: int
                            ) -> Optional[np.ndarray]:
        """A manual axis evaluated over ``n_points`` scan points.

        X is the fastest-varying axis, then Y, then Z -- the usual raster
        order. Each axis cycles with a period equal to the product of the
        point counts of every FASTER axis, so with X=21 and Y=11 the first
        21 points walk X once while Y holds, then Y advances. Getting this
        backwards produces a transposed map that still looks plausible,
        which is why the convention is stated in the UI and the implied
        total is checked against the frame count.
        """
        shape = self._manual_shape()
        if axis not in shape or n_points <= 0:
            return None
        w = self._manual_widgets[axis]
        start, step = float(w['start'].value()), float(w['step'].value())
        period = 1
        for faster in self.AXES:
            if faster == axis:
                break
            period *= shape.get(faster, 1)
        idx = (np.arange(n_points) // max(1, period)) % shape[axis]
        return start + step * idx

    def _on_manual_changed(self, *_args) -> None:
        shape = self._manual_shape()
        if not shape:
            self._manual_note.setText('')
            self._refresh_spatial()
            return
        total = 1
        for ax in self.AXES:
            total *= shape.get(ax, 1)
        parts = ' x '.join(f'{shape[a]}' for a in self.AXES if a in shape)
        frames = infer_frame_count(self._catalog.primary_path)
        if frames is None:
            tail = '  (frame count unknown)'
        elif frames == total:
            tail = f'  (source has {frames} frames)'
        elif total > frames and total % frames == 0:
            # The spanning case, and the normal one for a file-per-row
            # raster: 13 x 64 across thirteen 64-frame files. Not an error.
            tail = (f'  — the source holds {frames} of them, so this grid '
                    f'spans {total // frames} files')
        else:
            tail = (f'  — but the source has {frames} frames, which this '
                    f'grid does not divide evenly')
        self._manual_note.setText(
            f'implies {parts} = {total} points{tail}   •   '
            f'X varies fastest, then Y, then Z')
        self._refresh_spatial()

    def _read_axis(self, axis: str) -> Optional[np.ndarray]:
        # A manual point count wins over the dataset pickers: it is the
        # explicit instruction, and it is the only option at all when the
        # source carries no motor tree.
        if axis in self._manual_shape():
            return self._manual_axis_values(axis, self._manual_points())
        ds_path = self._axis_path(axis)
        if not ds_path:
            return None
        if self._should_span_files():
            return self._read_axis_across_files(ds_path)
        primary = self._catalog.primary_path
        arr = read_dataset_1d(primary, ds_path)
        if arr is None and self._catalog.aux_h5_path:
            arr = read_h5_dataset(self._catalog.aux_h5_path, ds_path)
        return arr

    def _should_span_files(self) -> bool:
        if not self._catalog.span_enabled:
            return False
        return len(self._discover_sibling_files()) >= 2

    def _set_span_status(self, text: str) -> None:
        """Forward status text to the Sources tab where the toggle now lives."""
        if self._span_status_cb is not None:
            self._span_status_cb(text or '')

    # ── Multi-file span ─────────────────────────────────────────────
    def _discover_sibling_files(self) -> list:
        primary = self._catalog.primary_path
        if not primary or not source_exists(primary):
            return []
        folder = os.path.dirname(primary)
        name = os.path.basename(primary)
        m = self._SIB_RX.match(name)
        if not m:
            return []
        base = m.group('base')
        ext = (m.group('ext') or '')
        container = m.group('container')
        try:
            entries = os.listdir(folder)
        except OSError:
            return []
        rng_lo, rng_hi = None, None
        if self._fnum_provider is not None:
            try:
                got = self._fnum_provider()
                if got is not None:
                    lo, hi = int(got[0]), int(got[1])
                    if hi < lo:
                        lo, hi = hi, lo
                    if lo != hi:
                        rng_lo, rng_hi = lo, hi
            except Exception:
                rng_lo = rng_hi = None
        hits: list = []
        for fname in entries:
            mm = self._SIB_RX.match(fname)
            if not mm or mm.group('base') != base:
                continue
            if (mm.group('ext') or '') != ext:
                continue
            if mm.group('container') != container:
                continue
            n = int(mm.group('num'))
            if rng_lo is not None and (n < rng_lo or n > rng_hi):
                continue
            hits.append((n, os.path.join(folder, fname)))
        hits.sort()
        return hits

    def _reduce_scalar(self, arr: np.ndarray) -> float:
        op = self._catalog.span_reducer
        if arr.size == 0:
            return float('nan')
        if arr.size == 1:
            return float(arr.flat[0])
        if op == 'first':
            return float(arr.flat[0])
        if op == 'last':
            return float(arr.flat[-1])
        if op == 'min':
            return float(np.nanmin(arr))
        if op == 'max':
            return float(np.nanmax(arr))
        return float(np.nanmean(arr))

    def _read_axis_across_files(self, ds_path: str) -> Optional[np.ndarray]:
        files = self._discover_sibling_files()
        if not files:
            self._set_span_status('No sibling files matched.')
            return None
        op = self._catalog.span_reducer
        lo, hi = files[0][0], files[-1][0]
        if op == 'full':
            chunks: list = []
            for _n, fpath in files:
                arr = read_dataset_1d(fpath, ds_path)
                if arr is None:
                    continue
                chunks.append(np.asarray(arr, dtype=float).ravel())
            if not chunks:
                self._set_span_status(
                    f'{len(files)} files [{lo}…{hi}], reduction=full — '
                    'dataset missing in every file.')
                return None
            out = np.concatenate(chunks)
            self._set_span_status(
                f'{len(files)} files [{lo}…{hi}], reduction=full, '
                f'{out.size} values total.')
            return out
        vals: list = []
        for _n, fpath in files:
            arr = read_dataset_1d(fpath, ds_path)
            vals.append(self._reduce_scalar(arr) if arr is not None
                        else float('nan'))
        self._set_span_status(
            f'{len(files)} files [{lo}…{hi}], reduction={op}')
        return np.asarray(vals, dtype=float)

    # ── Spatial plotting ────────────────────────────────────────────
    def _refresh_spatial(self):
        if self._catalog.span_enabled and not self._should_span_files():
            rng = None
            if self._fnum_provider is not None:
                try:
                    rng = self._fnum_provider()
                except Exception:
                    pass
            if rng is not None and int(rng[0]) != int(rng[1]):
                lo, hi = int(rng[0]), int(rng[1])
                self._set_span_status(
                    f'No siblings found in file-# range [{lo}–{hi}]; '
                    'check launcher fnum_ini/fnum_fin or folder.')
            else:
                self._set_span_status(
                    'Span mode: no sibling files found in folder; '
                    'showing single-file dataset.')
        arrays = {ax: self._read_axis(ax) for ax in self.AXES}
        active = [(ax, arr) for ax, arr in arrays.items() if arr is not None]
        if not active:
            self._draw_empty_grid('No spatial axis selected yet.')
            return

        n = min(arr.size for _, arr in active)
        active = [(ax, arr[:n]) for ax, arr in active]

        color_arr = self._sample_picker(self.color_picker, n)
        size_arr = self._sample_picker(self.size_picker, n)
        color_label = (self.color_picker.current_label()
                       if self.color_picker.is_active() else '')

        if (self._grid_view_axis == 'free'
                and len(active) >= 3 and self.fig_grid.axes):
            old_ax = self.fig_grid.axes[0]
            if hasattr(old_ax, 'elev') and hasattr(old_ax, 'azim'):
                self._grid_free_view_elev = float(getattr(old_ax, 'elev', 0.0))
                self._grid_free_view_azim = float(getattr(old_ax, 'azim', 0.0))

        self.fig_grid.clear()
        spanning = self._should_span_files()
        if len(active) == 1:
            ax_name, arr = active[0]
            ax = self.fig_grid.add_subplot(111)
            full_span = (spanning
                         and self._catalog.span_reducer == 'full')
            if spanning and not full_span:
                files = self._discover_sibling_files()
                xs = (np.asarray([fn for fn, _ in files[:n]])
                      if files else np.arange(n))
                x_label = 'file number'
            elif full_span:
                xs = np.arange(n)
                x_label = 'global frame index (across files)'
            else:
                xs = np.arange(n)
                x_label = 'frame index'
            ca = self._align_per_point(color_arr, xs.size)
            if ca is not None:
                ax.plot(xs, arr, '-', color='lightgray', lw=0.8, zorder=1)
                sc = ax.scatter(xs, arr, c=ca, cmap='viridis',
                                s=20, zorder=2)
                self._apply_color_limits(sc, ca)
                self.fig_grid.colorbar(sc, ax=ax,
                                       label=color_label or 'color')
            else:
                ax.plot(xs, arr, 'o-', ms=4)
            ax.set_xlabel(x_label)
            ax.set_ylabel(f'{ax_name.upper()}')
            ax.set_title(
                f'{"across files" if spanning else "1-D scan"} ({n} points)')
            ax.grid(True, alpha=0.3)
        elif len(active) == 2:
            (xn, xa), (yn, ya) = active
            c_kw, s_kw = self._resolve_color_size(n, color_arr, size_arr)
            ax = self.fig_grid.add_subplot(111)
            style = self.plot_style_combo.currentText()
            interp = self.interp_method_combo.currentText()
            rendered_scatter = True

            ca_aligned = self._align_per_point(color_arr, n)
            if (style != 'Scatter' and ca_aligned is not None
                    and ca_aligned.size >= 4):
                try:
                    from scipy.interpolate import griddata  # noqa: PLC0415
                    # Drop NaN-valued points before griddata — otherwise a
                    # single NaN propagates through linear/cubic interp and
                    # the whole mesh comes back empty. This is how peak-fit
                    # streams with failed fits (e.g. Width σ on lineouts
                    # without a peak) still produce a visible surface over
                    # the files that did fit.
                    valid = np.isfinite(ca_aligned)
                    if not np.all(valid):
                        ca = ca_aligned[valid]
                        xa_v = xa[valid]; ya_v = ya[valid]
                    else:
                        ca = ca_aligned; xa_v = xa; ya_v = ya
                    if ca.size < 4:
                        raise ValueError('not enough finite color values')
                    points = np.column_stack([xa_v, ya_v])
                    nx = min(300, max(100, int(np.sqrt(n) * 3)))
                    xi = np.linspace(xa.min(), xa.max(), nx)
                    yi = np.linspace(ya.min(), ya.max(), nx)
                    Xi, Yi = np.meshgrid(xi, yi)
                    Zi = griddata(points, ca, (Xi, Yi), method=interp)
                    vmin, vmax = self._color_limits(
                        float(np.nanmin(ca)), float(np.nanmax(ca)))
                    cmap = 'viridis'
                    if style == 'Mesh':
                        im = ax.pcolormesh(Xi, Yi, Zi, cmap=cmap,
                                           vmin=vmin, vmax=vmax,
                                           shading='auto')
                        self.fig_grid.colorbar(im, ax=ax, label=color_label)
                    elif style == 'Contour':
                        levels = np.linspace(vmin, vmax, 16)
                        cs = ax.contour(Xi, Yi, Zi, levels=levels, cmap=cmap)
                        self.fig_grid.colorbar(cs, ax=ax, label=color_label)
                    else:  # Contourf
                        levels = np.linspace(vmin, vmax, 16)
                        csf = ax.contourf(Xi, Yi, Zi, levels=levels, cmap=cmap)
                        ax.contour(Xi, Yi, Zi, levels=levels,
                                   colors='k', linewidths=0.3, alpha=0.4)
                        self.fig_grid.colorbar(csf, ax=ax, label=color_label)
                    ax.scatter(xa, ya, s=4, c='k', alpha=0.15, zorder=2)
                    rendered_scatter = False
                except ImportError:
                    pass
                except Exception:
                    pass

            if rendered_scatter:
                sc = ax.scatter(xa, ya, **s_kw, **c_kw)
                if 'c' in c_kw:
                    self._apply_color_limits(sc, c_kw['c'])
                    self.fig_grid.colorbar(sc, ax=ax, label=color_label)
                self._add_size_legend(ax, n, size_arr)
            ax.set_xlabel(xn.upper()); ax.set_ylabel(yn.upper())
            ax.set_title(f'2-D scan ({n} points)')
            ax.grid(True, alpha=0.3)
            self._apply_aspect(ax)
        else:
            (xn, xa), (yn, ya), (zn, za) = active
            c_kw, s_kw = self._resolve_color_size(n, color_arr, size_arr)
            ax = self.fig_grid.add_subplot(111, projection='3d')
            sc = ax.scatter(xa, ya, za, **s_kw, **c_kw)
            if 'c' in c_kw:
                self._apply_color_limits(sc, c_kw['c'])
                self.fig_grid.colorbar(sc, ax=ax, shrink=0.6,
                                       label=color_label)
            self._add_size_legend(ax, n, size_arr)
            ax.set_xlabel(xn.upper()); ax.set_ylabel(yn.upper()); ax.set_zlabel(zn.upper())
            cx, cy, cz = np.mean(xa), np.mean(ya), np.mean(za)
            sx = (np.nanmax(xa) - np.nanmin(xa)) or 1.0
            sy = (np.nanmax(ya) - np.nanmin(ya)) or 1.0
            sz = (np.nanmax(za) - np.nanmin(za)) or 1.0
            half = max(sx, sy, sz) / 2.0 * 1.1
            ax.set_xlim(cx - half, cx + half)
            ax.set_ylim(cy - half, cy + half)
            ax.set_zlim(cz - half, cz + half)
            try:
                ax.set_box_aspect((1, 1, 1))
            except (AttributeError, TypeError):
                pass
            L = 0.25 * max(sx, sy, sz)
            ax.quiver(cx, cy, cz, L, 0, 0, color='red',
                      arrow_length_ratio=0.15, lw=1.2)
            ax.quiver(cx, cy, cz, 0, L, 0, color='green',
                      arrow_length_ratio=0.15, lw=1.2)
            ax.quiver(cx, cy, cz, 0, 0, L, color='blue',
                      arrow_length_ratio=0.15, lw=1.2)
            ax.set_title(f'3-D scan ({n} points) — drag to rotate')
            self._apply_grid_projection_view(ax)
        self.canvas_grid.draw_idle()

    def _sample_picker(self, picker: 'ColorSourceRow',
                       n: int) -> Optional[np.ndarray]:
        """Read a color/size value array honouring Span sibling files.

        When span is on and the picker's stream is a 1-D / aux dataset, this
        re-reads it across every sibling file (same path as the X/Y/Z
        spatial axes use) so the color stream lines up with the spanned
        scatter — instead of silently falling back to default colour when
        a single-file read returns the wrong length.  For peak-fit (CSV)
        streams under span, we treat ``img_idx`` as a global frame index
        across all spanned files: pull per-frame values for the total
        frame count, then collapse each file's block via ``_reduce_scalar``
        (same op the X/Y axes use) so the result lines up 1:1 with the
        spanned spatial points.
        """
        if not picker.is_active():
            return None
        if picker.using_fit():
            if self._should_span_files():
                return self._sample_peak_fit_across_files(picker, n)
            return picker.value(n_frames=n)
        si = picker.current_stream_info()
        if (self._should_span_files() and si is not None
                and si.kind in ('1d', 'aux')):
            _, ds_path = MetadataCatalog._split_key(si.key)
            spanned = self._read_axis_across_files(ds_path)
            if spanned is not None and spanned.size > 0:
                return spanned
        return picker.value(n_frames=n)


    def _sample_peak_fit_across_files(self, picker, n_files: int
                                      ) -> Optional[np.ndarray]:
        """Return one peak-fit scalar per spanned sibling file.

        Matches each CSV row to its sibling file via the ``file_num``
        parsed from the row's ``name`` column. Robust when some files
        have no surviving fit rows (peak-fitting failed for those
        lineouts) — those files come back as NaN, which keeps the spatial
        scatter intact and just drops colour on those points.
        """
        files = self._discover_sibling_files()
        if not files:
            return picker.value(n_frames=n_files)
        file_nums = [n for n, _ in files]

        cache = self._catalog.csv_cache()
        if (cache is None or cache.get('file_num') is None
                or not np.any(cache['file_num'] >= 0)):
            # CSV lacks file_num (no leading basename). Best-effort
            # fallback: per-frame read via the uniform-grid assumption.
            return self._sample_peak_fit_uniform_grid(picker, n_files)

        # ColorSourceRow exposes its fit picker as ``fit_picker``; reach
        # in for peak/param/η/agg since current_stream_info() returns
        # None in fit mode.
        fit = getattr(picker, 'fit_picker', None)
        if fit is None or not fit.is_active():
            return None
        peak = fit.current_peak()
        param = fit.current_param()
        kind, val = fit.current_agg()
        if kind == 'eta':
            eta_target = float(val); atol = 0.5; agg = 'mean'
        else:
            eta_target = 0.0; atol = 1e9; agg = str(val)
        return peak_fit_per_file_at_eta(
            cache, int(peak), str(param), eta_target,
            file_nums, agg=agg, atol_deg=atol)

    def _sample_peak_fit_uniform_grid(self, picker, n_files: int
                                      ) -> Optional[np.ndarray]:
        per_file = infer_frame_count(self._catalog.primary_path)
        if not per_file or per_file <= 0:
            return picker.value(n_frames=n_files)
        total = int(n_files) * int(per_file)
        per_frame = picker.value(n_frames=total)
        if per_frame is None:
            return None
        a = np.asarray(per_frame, dtype=float).ravel()
        if a.size != total:
            return a
        try:
            grid = a.reshape(n_files, per_file)
        except ValueError:
            return a
        out = np.empty(n_files, dtype=float)
        for i in range(n_files):
            out[i] = self._reduce_scalar(grid[i])
        return out

    def _color_limits(self, default_vmin: float, default_vmax: float
                      ) -> tuple:
        return resolve_color_limits(
            getattr(self, 'cbar_vmin_edit', None),
            getattr(self, 'cbar_vmax_edit', None),
            default_vmin, default_vmax)

    def _apply_color_limits(self, mappable, c_data) -> None:
        apply_color_limits_to_mappable(
            mappable, c_data,
            getattr(self, 'cbar_vmin_edit', None),
            getattr(self, 'cbar_vmax_edit', None))

    def _apply_aspect(self, ax) -> None:
        """Apply the user-selected aspect mode to a 2-D axes.
        equal → same data-unit scale; square → square plot box;
        tight → free aspect snapped to data; auto → free aspect."""
        mode = self.aspect_combo.currentText()
        if mode == 'equal':
            ax.set_aspect('equal', adjustable='datalim')
        elif mode == 'square':
            # 'equal' with box-adjustable + matching axis lengths → square.
            ax.set_aspect('equal', adjustable='box')
            try:
                ax.set_box_aspect(1.0)
            except (AttributeError, TypeError):
                pass
        elif mode == 'tight':
            ax.set_aspect('auto')
            ax.axis('tight')
        else:  # auto
            ax.set_aspect('auto')

    @staticmethod
    def _align_per_point(arr: Optional[np.ndarray],
                         n: int) -> Optional[np.ndarray]:
        """Coerce a metadata stream to length-n.  Truncates when longer,
        tiles when the length divides evenly, otherwise returns None (so
        we drop the mapping rather than draw nothing at all).  This is what
        keeps span-mode-on-Real-space drawable when the user picks a color
        stream that didn't span (e.g. an aux dataset of length 10 against
        145 spanned X/Y points)."""
        if arr is None:
            return None
        a = np.asarray(arr, dtype=float).ravel()
        if a.size == 0:
            return None
        if a.size == n:
            return a
        if a.size > n:
            return a[:n]
        if n % a.size == 0:
            return np.tile(a, n // a.size)
        return None

    def _resolve_color_size(self, n: int,
                            color_arr: Optional[np.ndarray],
                            size_arr: Optional[np.ndarray]
                            ) -> tuple:
        c_kw: dict = {}
        ca = self._align_per_point(color_arr, n)
        if ca is not None:
            if np.all(np.isnan(ca)):
                c_kw = {'color': 'tab:blue'}
            else:
                vmin = float(np.nanmin(ca))
                vmax = float(np.nanmax(ca))
                c_kw = {'c': ca, 'cmap': 'viridis',
                        'vmin': vmin, 'vmax': vmax}
        else:
            c_kw = {'color': 'tab:blue'}

        s_kw: dict = {}
        sa = self._align_per_point(size_arr, n)
        if sa is not None:
            vmin, vmax = np.nanmin(sa), np.nanmax(sa)
            if vmax > vmin:
                s_kw = {'s': 10 + 190 * (sa - vmin) / (vmax - vmin)}
            else:
                s_kw = {'s': np.full(n, 50.0)}
        else:
            s_kw = {'s': 18}
        return c_kw, s_kw

    def _add_size_legend(self, ax, n: int,
                         size_arr: Optional[np.ndarray]):
        sa = self._align_per_point(size_arr, n)
        if sa is None:
            return
        vmin_s = float(np.nanmin(sa))
        vmax_s = float(np.nanmax(sa))
        if vmax_s == vmin_s:
            return
        vmid_s = (vmin_s + vmax_s) / 2.0
        attr_label = self.size_picker.current_label() or 'size'
        handles = [
            ax.scatter([], [], s=10, c='gray', alpha=0.7,
                       label=f'{vmin_s:.4g}'),
            ax.scatter([], [], s=100, c='gray', alpha=0.7,
                       label=f'{vmid_s:.4g}'),
            ax.scatter([], [], s=200, c='gray', alpha=0.7,
                       label=f'{vmax_s:.4g}'),
        ]
        ax.legend(handles=handles, title=attr_label, loc='upper right',
                  fontsize=11, title_fontsize=12, framealpha=0.8,
                  scatterpoints=1, handletextpad=0.8, borderpad=0.9)

    def _set_grid_projection_view(self, axis: str):
        if axis in ('xp', 'xn', 'yp', 'yn', 'zp', 'zn', 'iso', 'free'):
            self._grid_view_axis = axis
            btn = self._grid_view_buttons.get(axis)
            if btn is not None and not btn.isChecked():
                btn.setChecked(True)
            self._refresh_spatial()

    def _apply_grid_projection_view(self, ax):
        views = {
            'xp': (0, 0), 'xn': (0, 180),
            'yp': (0, 90), 'yn': (0, -90),
            'zp': (90, -90), 'zn': (-90, -90),
            'iso': (30, 45),
        }
        if self._grid_view_axis == 'free':
            if (self._grid_free_view_elev is not None
                    and self._grid_free_view_azim is not None):
                ax.view_init(elev=self._grid_free_view_elev,
                             azim=self._grid_free_view_azim)
        else:
            elev, azim = views.get(self._grid_view_axis, views['iso'])
            ax.view_init(elev=elev, azim=azim)
        self._grid_last_view_elev = float(getattr(ax, 'elev', 0.0))
        self._grid_last_view_azim = float(getattr(ax, 'azim', 0.0))

    def _on_grid_canvas_mouse_release(self, event):
        ax = getattr(event, 'inaxes', None)
        if ax is None or not hasattr(ax, 'elev') or not hasattr(ax, 'azim'):
            return
        elev = float(getattr(ax, 'elev', 0.0))
        azim = float(getattr(ax, 'azim', 0.0))
        if (self._grid_last_view_elev is not None
                and self._grid_last_view_azim is not None
                and abs(elev - self._grid_last_view_elev) < 1e-6
                and abs(azim - self._grid_last_view_azim) < 1e-6):
            return
        self._grid_free_view_elev = elev
        self._grid_free_view_azim = azim
        self._grid_last_view_elev = elev
        self._grid_last_view_azim = azim
        if self._grid_view_axis != 'free':
            self._grid_view_axis = 'free'
            btn = self._grid_view_buttons.get('free')
            if btn is not None and not btn.isChecked():
                btn.setChecked(True)

    def _draw_empty_grid(self, msg: str):
        self.fig_grid.clear()
        ax = self.fig_grid.add_subplot(111)
        ax.text(0.5, 0.5, msg, ha='center', va='center',
                transform=ax.transAxes, color='gray')
        ax.set_xticks([]); ax.set_yticks([])
        self.canvas_grid.draw_idle()

    # Baseline rcParam values that _apply_font_scale multiplies. Kept in
    # sync with the module-level rcParams block near the imports.
    _FONT_BASELINE = {
        'font.size': 13.0,
        'axes.titlesize': 15.0,
        'axes.labelsize': 14.0,
        'xtick.labelsize': 12.0,
        'ytick.labelsize': 12.0,
        'legend.fontsize': 11.0,
        'figure.titlesize': 16.0,
    }

