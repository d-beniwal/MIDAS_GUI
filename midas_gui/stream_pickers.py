"""Widgets for choosing a stream, and for bounding a colour scale.

A *stream* is anything ``MetadataCatalog`` can turn into one value per
frame: a 1-D dataset in the source, a 2-D array collapsed along its last
axis, or a column of the peak-fit CSV. These are the widgets that pick one.

  ``MetadataStreamPicker``  the stream combo, plus two companions that
                            enable themselves from the selected stream's
                            kind -- a 2-D reducer (mean/sum/max/min,
                            meaningless for a 1-D stream) and an eta
                            aggregation (only for peak-fit streams).
  ``FitStreamPicker``       peak + parameter + eta aggregation, scoped to
                            the peak-fit CSV. Deliberately separate so a
                            user is not scrolling past every motor PV in
                            the file to reach "peak 2 position".
  ``ColorSourceRow``        the two above behind a Metadata / Peak fit
                            radio, inactive side greyed rather than hidden.

Ported from ``mpe_wf_saxs_waxs/gui_data_explorer.py`` (795-861, 1134-1705).
Qt only -- no matplotlib here, even though the colour-range helpers feed
one: ``apply_color_limits_to_mappable`` duck-types ``set_clim`` rather
than importing anything, so this module stays cheap to import and test.

Added, not in the source: ``set_key`` on ``MetadataStreamPicker`` and
``ColorSourceRow``. The source can read the current selection
(``current_key``) but never had to restore one; this project persists tab
state into a Project file, and a combo cannot be restored by display text
because the item labels embed data-derived counts like ``[N=200]`` that
change with the file.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets

from midas_gui.metadata_catalog import (
    GROUP_BG_COLORS, MetadataCatalog, StreamInfo)
from midas_gui.peak_fit_data import (
    _PEAK_FIT_PARAM_LABELS,
    peak_fit_eta_choices, peak_fit_omega_eta_grid, peak_fit_per_frame,
    peak_fit_per_frame_at_eta)


def resolve_color_limits(vmin_edit, vmax_edit,
                          default_vmin: float, default_vmax: float
                          ) -> tuple:
    """Return (vmin, vmax) from a Color-range widget pair. Blank / garbage
    field → default for that side. Inverted or non-finite pair → both
    defaults (fall back to auto)."""
    def _parse(edit):
        txt = edit.text().strip() if edit is not None else ''
        if not txt:
            return None
        try:
            return float(txt)
        except ValueError:
            return None
    lo = _parse(vmin_edit); hi = _parse(vmax_edit)
    vmin = lo if lo is not None else default_vmin
    vmax = hi if hi is not None else default_vmax
    if not np.isfinite(vmin) or not np.isfinite(vmax) or vmax <= vmin:
        return default_vmin, default_vmax
    return vmin, vmax


def apply_color_limits_to_mappable(mappable, c_data,
                                    vmin_edit, vmax_edit) -> None:
    """Clamp ``mappable`` (scatter/pcolormesh/imshow) to the range in the
    widget pair, defaulting each side to the data extremum."""
    try:
        arr = np.asarray(c_data, dtype=float).ravel()
        arr = arr[np.isfinite(arr)]
        if arr.size == 0:
            return
        d_lo = float(arr.min()); d_hi = float(arr.max())
    except (TypeError, ValueError):
        return
    if d_hi <= d_lo:
        return
    vmin, vmax = resolve_color_limits(vmin_edit, vmax_edit, d_lo, d_hi)
    try:
        mappable.set_clim(vmin, vmax)
    except AttributeError:
        pass


def make_color_range_row(on_change) -> tuple:
    """Build a horizontal 'Color range: <min> to <max>' row, wire the
    edits' editingFinished to ``on_change``, and return (layout, vmin_edit,
    vmax_edit) so the caller can splice the layout in and stash the edits.
    """
    row = QtWidgets.QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    row.addWidget(QtWidgets.QLabel('Color range:'))
    vmin_edit = QtWidgets.QLineEdit()
    vmin_edit.setPlaceholderText('auto')
    vmin_edit.setFixedWidth(80)
    vmin_edit.setToolTip('Lower colorbar limit. Blank = data min.')
    vmin_edit.editingFinished.connect(on_change)
    row.addWidget(vmin_edit)
    row.addWidget(QtWidgets.QLabel('to'))
    vmax_edit = QtWidgets.QLineEdit()
    vmax_edit.setPlaceholderText('auto')
    vmax_edit.setFixedWidth(80)
    vmax_edit.setToolTip('Upper colorbar limit. Blank = data max.')
    vmax_edit.editingFinished.connect(on_change)
    row.addWidget(vmax_edit)
    return row, vmin_edit, vmax_edit


class MetadataStreamPicker(QtWidgets.QWidget):
    """Combo + companion controls bound to a ``MetadataCatalog``.

    A single stream-choice combo lists every catalog stream (plus optional
    ``(none)`` / extra-default entries). Two companion combos sit next to
    it but are enabled only when the active stream needs them: a Reduce
    combo for 2-D streams and an Agg combo for CSV streams that fold η.
    Disabled-not-hidden so the user always sees what is overridden.

    Stays in sync with the catalog via ``catalogChanged``: the combo
    repopulates while preserving the current selection when still valid.
    """

    streamChanged = QtCore.pyqtSignal()

    def __init__(self, catalog: 'MetadataCatalog',
                 role: str = 'Value', *,
                 kinds: Optional[tuple] = None,
                 extra_default: Optional[tuple] = None,
                 allow_none: bool = True,
                 default_match=None,
                 show_label: bool = True,
                 parent=None):
        """``role`` is the chip label; ``kinds`` filters stream kinds;
        ``extra_default`` injects an extra ``(key, label)`` entry at the top
        (its key starts with ``extra:`` and is reported by ``is_extra``);
        ``default_match`` is an optional ``callable(StreamInfo) -> bool``
        used to auto-select a sensible default when no prior choice exists.
        """
        super().__init__(parent)
        self._catalog = catalog
        self._kinds = kinds
        self._extra_default = extra_default
        self._allow_none = allow_none
        self._role = role
        self._default_match = default_match
        self._show_label = show_label
        self._suppress = False
        self._build_ui()
        catalog.catalogChanged.connect(self._populate)
        self._populate()

    def _build_ui(self):
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        if self._show_label:
            lay.addWidget(QtWidgets.QLabel(f'{self._role}:'))
        self.combo = QtWidgets.QComboBox()
        self.combo.setMinimumWidth(220)
        self.combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToContents)
        self.combo.currentIndexChanged.connect(self._on_changed)
        lay.addWidget(self.combo, 1)

        lay.addSpacing(6)
        lay.addWidget(QtWidgets.QLabel('Reduce:'))
        self.reduce_combo = QtWidgets.QComboBox()
        self.reduce_combo.addItems(['mean', 'sum', 'max', 'min'])
        self.reduce_combo.setToolTip(
            'Reduction along the last axis when the selected stream is 2-D.')
        self.reduce_combo.setEnabled(False)
        self.reduce_combo.currentIndexChanged.connect(
            lambda _i: self.streamChanged.emit())
        lay.addWidget(self.reduce_combo)

        lay.addSpacing(6)
        lay.addWidget(QtWidgets.QLabel('η:'))
        # See FitStreamPicker for the userData scheme: either
        # ('agg', name) or ('eta', degrees).  Repopulated on
        # catalogChanged so freshly-loaded CSV η values appear.
        self.agg_combo = QtWidgets.QComboBox()
        self.agg_combo.setToolTip(
            'Either an η aggregation (mean/std/min/max across all η rows '
            'per frame) or a specific η value present in the CSV.')
        self.agg_combo.setEnabled(False)
        self.agg_combo.currentIndexChanged.connect(
            lambda _i: self.streamChanged.emit())
        lay.addWidget(self.agg_combo)
        self._populate_eta_combo()
        self._catalog.catalogChanged.connect(self._populate_eta_combo)

    def _populate_eta_combo(self) -> None:
        """(Re)build the η combo with aggregations + the CSV's unique η values."""
        cache = self._catalog.csv_cache()
        prev = (self.agg_combo.currentData()
                if self.agg_combo.count() > 0 else None)
        self.agg_combo.blockSignals(True)
        self.agg_combo.clear()
        for label in ('mean', 'std', 'min', 'max'):
            self.agg_combo.addItem(f'all η ({label})',
                                   userData=('agg', label))
        if cache is not None:
            for v in peak_fit_eta_choices(cache):
                self.agg_combo.addItem(f'η = {v:.2f}°',
                                       userData=('eta', float(v)))
        if prev is not None:
            for i in range(self.agg_combo.count()):
                if self.agg_combo.itemData(i) == prev:
                    self.agg_combo.setCurrentIndex(i)
                    break
        self.agg_combo.blockSignals(False)

    # ── combo population ─────────────────────────────────────────────
    def _populate(self):
        prev_key = self.current_key()
        self._suppress = True
        self.combo.clear()
        if self._extra_default is not None:
            ek, el = self._extra_default
            self.combo.addItem(el, userData=('extra', ek))
        if self._allow_none:
            self.combo.addItem('(none)', userData=('none', None))
        streams = self._catalog.list_streams(kinds=self._kinds)
        last_group = None
        color_idx = -1
        for s in streams:
            if s.group != last_group:
                if last_group is not None:
                    self.combo.insertSeparator(self.combo.count())
                color_idx = (color_idx + 1) % len(GROUP_BG_COLORS)
                last_group = s.group
            self.combo.addItem(s.label, userData=('stream', s))
            brush = QtGui.QBrush(
                QtGui.QColor(GROUP_BG_COLORS[color_idx]))
            idx = self.combo.count() - 1
            self.combo.setItemData(idx, brush, QtCore.Qt.BackgroundRole)
            self.combo.setItemData(idx, s.group, QtCore.Qt.ToolTipRole)

        # Restore previous selection when still available; otherwise try the
        # caller-supplied default-match predicate; otherwise fall to the first
        # item (extra or none).
        restored = False
        if prev_key:
            idx = self._find_index_for_key(prev_key)
            if idx >= 0:
                self.combo.setCurrentIndex(idx)
                restored = True
        if not restored and self._default_match is not None:
            for i in range(self.combo.count()):
                data = self.combo.itemData(i)
                if data is None or data[0] != 'stream':
                    continue
                try:
                    if self._default_match(data[1]):
                        self.combo.setCurrentIndex(i)
                        restored = True
                        break
                except Exception:
                    continue
        self._suppress = False
        self._sync_companions()

    def _find_index_for_key(self, key: str) -> int:
        for i in range(self.combo.count()):
            data = self.combo.itemData(i)
            if data is None:
                continue
            tag, val = data
            if tag == 'extra' and key == f'extra:{val}':
                return i
            if tag == 'none' and key == '':
                return i
            if tag == 'stream' and val.key == key:
                return i
        return -1

    def _on_changed(self, _idx):
        if self._suppress:
            return
        self._sync_companions()
        self.streamChanged.emit()

    def _sync_companions(self):
        si = self.current_stream_info()
        is_2d = (si is not None and si.kind == '2d')
        needs_agg = (si is not None and si.needs_agg)
        self.reduce_combo.setEnabled(is_2d)
        self.agg_combo.setEnabled(needs_agg)

    # ── accessors ────────────────────────────────────────────────────
    def current_key(self) -> str:
        data = self.combo.currentData()
        if not data:
            return ''
        tag, val = data
        if tag == 'extra':
            return f'extra:{val}'
        if tag == 'none':
            return ''
        return val.key

    def set_key(self, key: str) -> bool:
        """Select the entry whose ``current_key()`` is ``key``. True if found.

        Not in the source, which only ever read the selection. Restoring
        one cannot go through the combo's display text: item labels embed
        data-derived counts (``[N=200]``, ``[2D 834x9]``) that change with
        the file, so ``helpers.apply_dict_to_widgets``'s findText matching
        would silently miss and leave the wrong stream selected. The key
        (``prim:/instrument/samX``) is stable.

        Returns False rather than raising when the key is absent -- the
        usual cause is a project restored against a file that no longer
        has that stream, which is a note for the caller, not an error.
        """
        key = (key or "").strip()
        for i in range(self.combo.count()):
            data = self.combo.itemData(i)
            if not data:
                continue
            tag, val = data
            found = (f"extra:{val}" if tag == "extra"
                     else "" if tag == "none" else val.key)
            if found == key:
                self.combo.setCurrentIndex(i)
                return True
        return False


    def current_stream_info(self) -> Optional[StreamInfo]:
        data = self.combo.currentData()
        if not data:
            return None
        tag, val = data
        return val if tag == 'stream' else None

    def current_label(self) -> str:
        si = self.current_stream_info()
        if si is not None:
            return si.label.split('  [', 1)[0]
        return self.combo.currentText()

    def is_active(self) -> bool:
        key = self.current_key()
        return bool(key) and not key.startswith('extra:')

    def is_extra(self, extra_key: Optional[str] = None) -> bool:
        key = self.current_key()
        if not key.startswith('extra:'):
            return False
        if extra_key is None:
            return True
        return key == f'extra:{extra_key}'

    def _eta_kwargs(self) -> dict:
        """Resolve the η combo's userData to catalog.get kwargs."""
        data = (self.agg_combo.currentData()
                if self.agg_combo.count() > 0 else None)
        if data is None:
            return {'agg': 'mean'}
        kind, val = data
        if kind == 'eta':
            return {'eta_deg': float(val)}
        return {'agg': str(val)}

    def current_eta(self) -> Optional[float]:
        """Selected η in degrees, or None when in all-η aggregation mode."""
        data = (self.agg_combo.currentData()
                if self.agg_combo.count() > 0 else None)
        if data is None:
            return None
        kind, val = data
        return float(val) if kind == 'eta' else None

    def current_agg(self) -> str:
        """η aggregation operator (mean/std/min/max) — only meaningful
        when ``current_eta()`` is None."""
        data = (self.agg_combo.currentData()
                if self.agg_combo.count() > 0 else None)
        if data is None or data[0] != 'agg':
            return 'mean'
        return str(data[1])

    def value(self, n_frames: Optional[int]) -> Optional[np.ndarray]:
        if not self.is_active():
            return None
        return self._catalog.get(
            self.current_key(), n_frames=n_frames,
            reducer=self.reduce_combo.currentText(),
            **self._eta_kwargs())

    def grid_value(self, omegas_deg: np.ndarray,
                   etas_deg: np.ndarray) -> Optional[np.ndarray]:
        if not self.is_active():
            return None
        # Pole-figure grid path: agg only — specific-η rendering on the
        # pole figure surface would be a single ring, not a 2-D surface.
        data = (self.agg_combo.currentData()
                if self.agg_combo.count() > 0 else None)
        agg = data[1] if (data is not None and data[0] == 'agg') else 'mean'
        return self._catalog.get_omega_eta_grid(
            self.current_key(), omegas_deg, etas_deg, agg=agg)


class FitStreamPicker(QtWidgets.QWidget):
    """Compact picker for a peak-fit CSV column: peak + param + η-aggregation.

    Returns one scalar per frame via :func:`peak_fit_per_frame`. Separated
    from :class:`MetadataStreamPicker` so the user can pick a fit attribute
    without scrolling past every aux / motor stream in the catalog.

    The widget is always visible; it disables (greys out) its combos when
    no peak-fit CSV is loaded — per [[feedback-disable-vs-hide]] so users
    can see what's overridden rather than wonder where the controls went.
    """

    streamChanged = QtCore.pyqtSignal()

    _PARAM_CHOICES = (
        ('int', _PEAK_FIT_PARAM_LABELS['int']),
        ('pos', _PEAK_FIT_PARAM_LABELS['pos']),
        ('sig', _PEAK_FIT_PARAM_LABELS['sig']),
        ('gam', _PEAK_FIT_PARAM_LABELS['gam']),
        ('esd-int', _PEAK_FIT_PARAM_LABELS['esd-int']),
        ('esd-pos', _PEAK_FIT_PARAM_LABELS['esd-pos']),
        ('esd-sig', _PEAK_FIT_PARAM_LABELS['esd-sig']),
        ('esd-gam', _PEAK_FIT_PARAM_LABELS['esd-gam']),
        ('Rwp', 'Rwp (per frame)'),
        ('GOF', 'GOF (per frame)'),
    )

    def __init__(self, catalog: 'MetadataCatalog', *,
                 show_label: bool = True, parent=None):
        super().__init__(parent)
        self._catalog = catalog
        self._show_label = show_label
        # When True, the η aggregation combo is held disabled regardless
        # of CSV state — used by the pole-figure surface picker where η
        # is a coordinate on the sphere, not a per-frame aggregation.
        self._eta_locked = False
        self._build_ui()
        catalog.catalogChanged.connect(self._refresh_choices)
        self._refresh_choices()

    def set_eta_locked(self, locked: bool, tooltip: str = '') -> None:
        """Permanently disable the η aggregation combo (survives
        catalogChanged re-enables)."""
        self._eta_locked = bool(locked)
        if locked:
            self.agg_combo.setEnabled(False)
            if tooltip:
                self.agg_combo.setToolTip(tooltip)
        self._refresh_choices()

    def _build_ui(self):
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        if self._show_label:
            lay.addWidget(QtWidgets.QLabel('Fit:'))
        lay.addWidget(QtWidgets.QLabel('peak'))
        self.peak_combo = QtWidgets.QComboBox()
        self.peak_combo.currentIndexChanged.connect(
            lambda _i: self.streamChanged.emit())
        lay.addWidget(self.peak_combo)
        lay.addSpacing(4)
        lay.addWidget(QtWidgets.QLabel('param'))
        self.param_combo = QtWidgets.QComboBox()
        for key, label in self._PARAM_CHOICES:
            self.param_combo.addItem(label, userData=key)
        self.param_combo.currentIndexChanged.connect(
            lambda _i: self.streamChanged.emit())
        lay.addWidget(self.param_combo)
        lay.addSpacing(4)
        lay.addWidget(QtWidgets.QLabel('η'))
        # Combo entries are tagged via userData as either
        #   ('agg', 'mean'|'std'|'min'|'max')  → aggregate across all η rows
        #   ('eta', float_degrees)             → pick rows at this η only
        # populated from the CSV cache on every catalogChanged.
        self.agg_combo = QtWidgets.QComboBox()
        self.agg_combo.setToolTip(
            'Either an η aggregation (mean/std/min/max across all η rows '
            'per frame) or a specific η value present in the CSV.')
        self.agg_combo.currentIndexChanged.connect(
            lambda _i: self.streamChanged.emit())
        lay.addWidget(self.agg_combo)
        self._populate_agg_combo()

    def _populate_agg_combo(self) -> None:
        """(Re)build the η combo with aggregations + the CSV's unique η values."""
        cache = self._catalog.csv_cache()
        prev = self._current_agg_userdata()
        self.agg_combo.blockSignals(True)
        self.agg_combo.clear()
        for label in ('mean', 'std', 'min', 'max'):
            self.agg_combo.addItem(f'all η ({label})',
                                   userData=('agg', label))
        if cache is not None:
            for v in peak_fit_eta_choices(cache):
                self.agg_combo.addItem(f'η = {v:.2f}°',
                                       userData=('eta', float(v)))
        # Restore prior selection by userData if still present.
        if prev is not None:
            for i in range(self.agg_combo.count()):
                if self.agg_combo.itemData(i) == prev:
                    self.agg_combo.setCurrentIndex(i)
                    break
        self.agg_combo.blockSignals(False)

    def _current_agg_userdata(self):
        if self.agg_combo.count() == 0:
            return None
        return self.agg_combo.currentData()

    def _refresh_choices(self):
        cache = self._catalog.csv_cache()
        # Repopulate peak combo from cache (preserving selection).
        prev_peak = self.current_peak()
        self.peak_combo.blockSignals(True)
        self.peak_combo.clear()
        if cache is not None:
            for pi in cache.get('peak_indices') or [0]:
                self.peak_combo.addItem(f'peak {pi}', userData=int(pi))
            if prev_peak is not None:
                for i in range(self.peak_combo.count()):
                    if self.peak_combo.itemData(i) == prev_peak:
                        self.peak_combo.setCurrentIndex(i)
                        break
        self.peak_combo.blockSignals(False)
        self._populate_agg_combo()
        ok = cache is not None and self.peak_combo.count() > 0
        for w in (self.peak_combo, self.param_combo, self.agg_combo):
            w.setEnabled(ok)
        if self._eta_locked:
            self.agg_combo.setEnabled(False)
        self.streamChanged.emit()

    # ── public API ───────────────────────────────────────────────────
    def current_peak(self) -> Optional[int]:
        if self.peak_combo.count() == 0:
            return None
        return int(self.peak_combo.currentData())

    def current_param(self) -> str:
        return str(self.param_combo.currentData() or 'int')

    def current_agg(self) -> tuple:
        """Returns ('agg', name) or ('eta', value_deg). Falls back to mean."""
        data = self._current_agg_userdata()
        if data is None:
            return ('agg', 'mean')
        return data

    def is_active(self) -> bool:
        return (self._catalog.csv_cache() is not None
                and self.current_peak() is not None)

    def current_label(self) -> str:
        if not self.is_active():
            return ''
        param_label = next((l for k, l in self._PARAM_CHOICES
                            if k == self.current_param()),
                           self.current_param())
        if self.current_param() in ('Rwp', 'GOF'):
            return f'{param_label}'
        kind, val = self.current_agg()
        eta_part = f'η-{val}' if kind == 'agg' else f'η={val:.2f}°'
        return (f'peak{self.current_peak()} / {param_label} '
                f'({eta_part})')

    def value(self, n_frames: Optional[int]) -> Optional[np.ndarray]:
        if not self.is_active() or not n_frames:
            return None
        cache = self._catalog.csv_cache()
        kind, val = self.current_agg()
        if kind == 'eta':
            return peak_fit_per_frame_at_eta(
                cache, self.current_peak(), self.current_param(),
                float(val), int(n_frames))
        return peak_fit_per_frame(cache, self.current_peak(),
                                  self.current_param(),
                                  str(val), int(n_frames))

    def grid_value(self, omegas_deg: np.ndarray,
                   etas_deg: np.ndarray) -> Optional[np.ndarray]:
        """Return an (n_omega, n_eta) grid of the selected peak-fit column,
        sampled directly from the CSV rows via
        :func:`peak_fit_omega_eta_grid`. Used by the pole-figure surface
        to color the sphere by any peak-fit attribute."""
        if not self.is_active():
            return None
        return self._catalog.get_omega_eta_grid(
            f'csv:{self.current_param()}{self.current_peak()}',
            omegas_deg, etas_deg)


class ColorSourceRow(QtWidgets.QWidget):
    """Compound color/size picker with two parallel sources: a scalar
    metadata stream picker and a peak-fit picker, plus a Metadata/Fit
    radio that decides which one drives the render.

    The non-active source is greyed (not hidden) per [[feedback-disable-vs-hide]]
    so the user can see exactly what was overridden.
    """

    streamChanged = QtCore.pyqtSignal()

    def __init__(self, catalog: 'MetadataCatalog', role: str = 'Color',
                 *, allow_none: bool = True,
                 extra_default: Optional[tuple] = None,
                 fit_eta_disabled: bool = False,
                 parent=None):
        super().__init__(parent)
        self._catalog = catalog
        self._role = role
        self._extra_default = extra_default
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        # Header row: role label + source radios.
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(8)
        header.addWidget(QtWidgets.QLabel(f'{role}:'))
        self.meta_radio = QtWidgets.QRadioButton('Metadata')
        self.fit_radio = QtWidgets.QRadioButton('Peak fit')
        self.meta_radio.setChecked(True)
        for r in (self.meta_radio, self.fit_radio):
            r.toggled.connect(self._on_mode_changed)
            header.addWidget(r)
        header.addStretch(1)
        outer.addLayout(header)

        # Metadata picker (only non-CSV streams; CSV is the fit picker's job).
        self.meta_picker = MetadataStreamPicker(
            catalog, role=role, kinds=('1d', '2d', 'aux'),
            allow_none=allow_none, extra_default=extra_default,
            show_label=False)
        self.meta_picker.streamChanged.connect(self.streamChanged.emit)
        meta_row = QtWidgets.QHBoxLayout()
        meta_row.setContentsMargins(20, 0, 0, 0)
        meta_row.addWidget(self.meta_picker, 1)
        outer.addLayout(meta_row)

        # Fit picker.
        self.fit_picker = FitStreamPicker(catalog, show_label=False)
        self.fit_picker.streamChanged.connect(self.streamChanged.emit)
        if fit_eta_disabled:
            # Pole figure uses η as a coordinate on the sphere — the
            # per-frame aggregation combo doesn't apply. Locked (not
            # hidden) per [[feedback-disable-vs-hide]] so the user can
            # see the control that would otherwise be relevant.
            self.fit_picker.set_eta_locked(
                True,
                tooltip=('Not used here — η is a coordinate on the pole '
                         'sphere, so peak-fit rows are placed per (ω, η) '
                         'directly.'))
        fit_row = QtWidgets.QHBoxLayout()
        fit_row.setContentsMargins(20, 0, 0, 0)
        fit_row.addWidget(self.fit_picker, 1)
        outer.addLayout(fit_row)

        self._on_mode_changed()

    def _on_mode_changed(self, *_):
        use_fit = self.fit_radio.isChecked()
        self.meta_picker.setEnabled(not use_fit)
        self.fit_picker.setEnabled(use_fit)
        self.streamChanged.emit()

    # ── public API ───────────────────────────────────────────────────
    def state(self) -> dict:
        """What this row has selected, in a form that survives a restore.

        Keys, not display text: the metadata combo's labels carry
        data-derived counts that change with the file (see
        MetadataStreamPicker.set_key), and which RADIO is active cannot be
        read back off either picker at all.
        """
        return {"using_fit": self.using_fit(),
                "meta_key": self.meta_picker.current_key()}

    def set_state(self, st: dict) -> None:
        """Restore :meth:`state`. Best effort, and order matters.

        The radio is set FIRST: flipping it re-enables the other picker,
        and a key set before that lands on a disabled widget whose
        contents may not have been populated yet.
        """
        if not st:
            return
        try:
            if st.get("using_fit"):
                self.fit_radio.setChecked(True)
            else:
                self.meta_radio.setChecked(True)
            self.meta_picker.set_key(st.get("meta_key") or "")
        except Exception:
            # A project restored against a file that no longer has the
            # stream should open with an empty picker, not a traceback.
            pass


    def using_fit(self) -> bool:
        return self.fit_radio.isChecked()

    def is_active(self) -> bool:
        return (self.fit_picker.is_active() if self.using_fit()
                else self.meta_picker.is_active())

    def current_label(self) -> str:
        return (self.fit_picker.current_label() if self.using_fit()
                else self.meta_picker.current_label())

    def current_stream_info(self) -> Optional[StreamInfo]:
        """For non-fit mode, expose the underlying StreamInfo so callers
        can decide whether to span (1-D/aux) or just read once (2-D/CSV)."""
        if self.using_fit():
            return None
        return self.meta_picker.current_stream_info()

    def value(self, n_frames: Optional[int]) -> Optional[np.ndarray]:
        return (self.fit_picker.value(n_frames) if self.using_fit()
                else self.meta_picker.value(n_frames))

    def grid_value(self, omegas_deg: np.ndarray,
                   etas_deg: np.ndarray) -> Optional[np.ndarray]:
        """(n_omega × n_eta) grid used by the pole-figure surface.
        Delegates to whichever source is active."""
        return (self.fit_picker.grid_value(omegas_deg, etas_deg)
                if self.using_fit()
                else self.meta_picker.grid_value(omegas_deg, etas_deg))

    def is_extra(self, extra_key: Optional[str] = None) -> bool:
        """True when Metadata mode's picker sits on its extra_default
        entry (e.g. pole-figure's '(panels)' fallback). Peak-fit mode is
        never on an extra entry."""
        if self.using_fit():
            return False
        return self.meta_picker.is_extra(extra_key)


