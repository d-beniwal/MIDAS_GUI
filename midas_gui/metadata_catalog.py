"""The stream catalog: four source paths, and what can be plotted from them.

The hub the Data Explorer's panels talk to. It holds the primary
HDF5/zarr source, an optional aux HDF5, a cake_parameters CSV and a
GSAS-II peak-fit CSV, discovers what each contains, and emits ONE
``catalogChanged`` when any of them moves. Every panel listens to that and
re-reads through ``list_streams`` / ``get`` / ``get_omega_eta_grid``, so
there is no panel-to-panel wiring at all.

Ported from ``mpe_wf_saxs_waxs/gui_data_explorer.py`` (863-1132) with one
behavioural change, which is load-bearing enough to state up front:

  **Scanning is lazy and memoized.** In the source, ``set_primary_source``
  walked the whole container and read every 1-D candidate array IN FULL to
  compute min/max for its combo label -- synchronously, on the GUI thread,
  inside the setter. That was tolerable there because the only consumer
  was a tab built around those combos. Here it is not: ``PeakFitPanel``
  uses this class (see ``peak_fit_panel.PeakFitCatalog``) and never calls
  ``list_streams`` at all, so a setter that scans would charge the Texture
  tab a full file walk it has no use for, every time a path changes.

  Setters now only invalidate. The walk happens in ``_ensure_scanned``,
  called from the three readers that actually need it. This is also
  strictly better for the Data Explorer itself: setting four paths used to
  mean four walks, and is now one.

``PeakFitCatalog`` in ``peak_fit_panel`` is an alias for this class. It
used to be a separate, smaller adapter; two QObjects with the same signal
name and overlapping method names is a trap where wiring the wrong one
gives you a silently empty combo rather than an error.
"""
from __future__ import annotations

import os
import re
from typing import NamedTuple, Optional

import numpy as np
from PyQt5 import QtCore

from midas_gui.peak_fit_data import (
    parse_peak_fit_csv, peak_fit_omega_eta_grid, peak_fit_per_frame,
    peak_fit_per_frame_at_eta)
from midas_gui.stream_sources import (
    discover_datasets, discover_motor_datasets, discover_zarr_2d_attrs,
    is_zarr, read_dataset_1d, read_zarr_2d_reduced, source_exists)

#: Cap on 2-D streams surfaced from one container. A MIDAS archive holds
#: one (nR, nEta) array per frame under IntegrationResult/ and another
#: under OmegaSumFrame/, so an 834-frame scan offers ~1,669 of them -- each
#: reducing to a per-R-bin vector for ONE frame, which is not a per-frame
#: stream and is meaningless as a colour source. Showing all of them would
#: bury the handful of real motor streams in the same combo.
MAX_2D_STREAMS = 200


class StreamInfo(NamedTuple):
    """Description of one metadata stream in the catalog.

    ``kind`` is one of ``'1d' | '2d' | 'aux' | 'csv'`` (the catalog uses it
    to dispatch reads and enable companion combos in the picker).
    ``length_hint`` is the per-frame length when known (None for CSV
    streams whose row count differs from the frame count).
    ``needs_agg`` flags streams that fold an extra axis (η for CSV columns).
    ``group`` is the parent-path label used to visually cluster combo items.
    """

    key: str
    label: str
    kind: str
    length_hint: Optional[int]
    needs_agg: bool
    group: str


_CSV_PARAM_RX = re.compile(r'^([A-Za-z][A-Za-z\-]*?)(\d+)$')


# Soft alternating tints used to visually cluster combo-box items by their
# parent path/group.  Shared between axis combos and stream pickers so the
# colour vocabulary is consistent across the Real-space tab.
GROUP_BG_COLORS = (
    '#ffe0b3',  # peach
    '#b3e5d1',  # mint
    '#cfd8ff',  # periwinkle
    '#ffd6e7',  # pink
    '#fff5b3',  # butter
    '#d5c1ff',  # lavender
)


class MetadataCatalog(QtCore.QObject):
    """Shared observable index of every available metadata stream.

    Tracks four user-supplied paths — primary source (HDF5/zarr), aux HDF5,
    cake parameters CSV, peak-fit CSV — and exposes a uniform ``list_streams``
    / ``get`` / ``get_omega_eta_grid`` API so every visualization tab can
    color or size markers by the same underlying entries.  Emits
    ``catalogChanged`` once after any setter completes.
    """

    catalogChanged = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.primary_path: str = ''
        self.aux_h5_path: str = ''
        self.cake_csv_path: str = ''
        self.peak_fit_csv_path: str = ''
        #: (primary_path, aux_h5_path) the discovered lists below describe.
        #: None means "not scanned yet / invalidated" -- see _ensure_scanned.
        self._scan_key: Optional[tuple] = None
        self._primary_1d: list[tuple[str, int, float, float]] = []
        self._primary_2d: list[tuple[str, int, int]] = []
        self._aux_1d: list[tuple[str, int, float, float]] = []
        self._csv_cache: Optional[dict] = None
        # Source-level: span sibling files (lives here so future tabs can
        # consume the same flag; today only Real space honors it).
        self.span_enabled: bool = False
        self.span_reducer: str = 'full'

    # ── setters ──────────────────────────────────────────────────────
    def set_primary_source(self, path: str) -> None:
        path = (path or '').strip()
        if path == self.primary_path:
            return
        self.primary_path = path
        self._scan_key = None          # invalidate; the walk is deferred
        self.catalogChanged.emit()

    def set_aux_source(self, path: str) -> None:
        path = (path or '').strip()
        if path == self.aux_h5_path:
            return
        self.aux_h5_path = path
        self._scan_key = None          # invalidate; the walk is deferred
        self.catalogChanged.emit()

    def set_cake_csv(self, path: str) -> None:
        path = (path or '').strip()
        if path == self.cake_csv_path:
            return
        self.cake_csv_path = path
        self.catalogChanged.emit()

    def set_peak_fit_csv(self, path: str) -> None:
        path = (path or '').strip()
        if path == self.peak_fit_csv_path:
            return
        self.peak_fit_csv_path = path
        self._csv_cache = None
        self.catalogChanged.emit()

    def set_span_enabled(self, enabled: bool) -> None:
        v = bool(enabled)
        if v == self.span_enabled:
            return
        self.span_enabled = v
        self.catalogChanged.emit()

    def set_span_reducer(self, reducer: str) -> None:
        r = (reducer or '').strip() or 'full'
        if r == self.span_reducer:
            return
        self.span_reducer = r
        self.catalogChanged.emit()

    # ── rescans ──────────────────────────────────────────────────────
    def _ensure_scanned(self) -> None:
        """Walk the sources if the paths have moved since the last walk.

        Every reader that touches the discovered lists goes through here.
        The setters only invalidate, so setting four paths costs one walk
        instead of four -- and a consumer that never lists streams (the
        Texture tab's peak-fit panel) never pays for one at all.
        """
        key = (self.primary_path, self.aux_h5_path)
        if key == self._scan_key:
            return
        self._rescan_primary()
        self._rescan_aux()
        self._scan_key = key

    def stream_counts(self) -> tuple:
        """``(n_primary_1d, n_primary_2d, n_aux_1d)`` for status lines.

        Public because the Sources panel needs these numbers and reaching
        into the private lists would skip _ensure_scanned, so its status
        text could describe a file that is no longer selected.
        """
        self._ensure_scanned()
        return (len(self._primary_1d), len(self._primary_2d),
                len(self._aux_1d))

    def _rescan_primary(self) -> None:
        p = self.primary_path
        if p and source_exists(p):
            self._primary_1d = discover_datasets(p) or []
            # Capped: see MAX_2D_STREAMS. The Sources panel says when the
            # list was truncated, so a missing stream is explainable rather
            # than mysterious.
            self._primary_2d = (discover_zarr_2d_attrs(p, MAX_2D_STREAMS)
                                if is_zarr(p) else [])
        else:
            self._primary_1d = []
            self._primary_2d = []

    def _rescan_aux(self) -> None:
        p = self.aux_h5_path
        if p and not is_zarr(p) and os.path.isfile(p):
            self._aux_1d = discover_motor_datasets(p) or []
        else:
            self._aux_1d = []

    # ── peak-fit CSV cache ──────────────────────────────────────────
    def csv_cache(self) -> Optional[dict]:
        if self._csv_cache is None and self.peak_fit_csv_path:
            self._csv_cache = parse_peak_fit_csv(self.peak_fit_csv_path)
        return self._csv_cache

    # ── stream catalog ──────────────────────────────────────────────
    def list_streams(self, kinds: Optional[tuple] = None
                     ) -> list[StreamInfo]:
        """Return every available stream; optionally filter by kind tuple."""
        self._ensure_scanned()
        streams: list[StreamInfo] = []
        # Primary 1-D (grouped by parent directory).
        for (path, n, _lo, _hi) in self._primary_1d:
            grp = path.rsplit('/', 1)[0] or '/'
            streams.append(StreamInfo(
                key=f'prim:{path}',
                label=f'{path}  [N={n}]',
                kind='1d', length_hint=int(n),
                needs_agg=False, group=f'primary {grp}'))
        # Primary 2-D (zarr only).
        for (path, n_rows, n_cols) in self._primary_2d:
            grp = path.rsplit('/', 1)[0] or '/'
            streams.append(StreamInfo(
                key=f'prim2d:{path}',
                label=f'{path}  [2D N={n_rows}, last_axis={n_cols}]',
                kind='2d', length_hint=int(n_rows),
                needs_agg=False, group=f'primary {grp}'))
        # Aux 1-D (skip entries that are already in primary).
        primary_paths = {r[0] for r in self._primary_1d}
        for (path, n, _lo, _hi) in self._aux_1d:
            if path in primary_paths:
                continue
            grp = path.rsplit('/', 1)[0] or '/'
            streams.append(StreamInfo(
                key=f'aux:{path}',
                label=f'{path}  [aux, N={n}]',
                kind='aux', length_hint=int(n),
                needs_agg=False, group=f'aux {grp}'))
        # Peak-fit CSV columns: one stream per (peak, param) plus extras.
        cache = self.csv_cache()
        if cache is not None:
            peak_indices = cache.get('peak_indices') or [0]
            params = cache.get('params') or {}
            for pi in peak_indices:
                for k in _PEAK_FIT_PARAM_KEYS:
                    if not params.get(k):
                        continue
                    streams.append(StreamInfo(
                        key=f'csv:{k}{pi}',
                        label=f'peak{pi} / {_PEAK_FIT_PARAM_LABELS[k]}',
                        kind='csv', length_hint=None,
                        needs_agg=True, group=f'peak-fit/peak{pi}'))
            for col, label in _PEAK_FIT_EXTRA_COLS:
                streams.append(StreamInfo(
                    key=f'csv:{col}',
                    label=f'peak-fit / {label}',
                    kind='csv', length_hint=None,
                    needs_agg=True, group='peak-fit/extra'))
        if kinds is not None:
            streams = [s for s in streams if s.kind in kinds]
        return streams

    # ── data getters ────────────────────────────────────────────────
    @staticmethod
    def _split_key(key: str) -> tuple[str, str]:
        kind, _, rest = key.partition(':')
        return kind, rest

    @staticmethod
    def _parse_csv_rest(rest: str) -> tuple[str, int]:
        """``int0`` → (``'int'``, ``0``); ``Rwp`` → (``'Rwp'``, ``0``)."""
        if rest in ('Rwp', 'GOF'):
            return rest, 0
        m = _CSV_PARAM_RX.match(rest)
        if m:
            return m.group(1), int(m.group(2))
        return rest, 0

    def get(self, key: str, n_frames: Optional[int] = None, *,
            reducer: str = 'mean', agg: str = 'mean',
            eta_deg: Optional[float] = None,
            eta_atol_deg: float = 0.5
            ) -> Optional[np.ndarray]:
        """Return a 1-D per-frame array (or None) for the given stream key.

        For CSV streams: pass ``eta_deg`` to restrict rows to that η bin
        (using ``eta_atol_deg`` as a tolerance); otherwise ``agg`` collapses
        across all η rows per frame.
        """
        self._ensure_scanned()
        if not key:
            return None
        kind, rest = self._split_key(key)
        if kind == 'prim':
            arr = read_dataset_1d(self.primary_path, rest)
            if arr is None and self.aux_h5_path:
                arr = read_h5_dataset(self.aux_h5_path, rest)
            return arr
        if kind == 'prim2d':
            return read_zarr_2d_reduced(self.primary_path, rest,
                                        reducer=reducer)
        if kind == 'aux':
            return read_h5_dataset(self.aux_h5_path, rest)
        if kind == 'csv':
            cache = self.csv_cache()
            if cache is None or not n_frames:
                return None
            param, peak = self._parse_csv_rest(rest)
            if eta_deg is not None:
                return peak_fit_per_frame_at_eta(
                    cache, peak, param, float(eta_deg),
                    n_frames, atol_deg=eta_atol_deg)
            return peak_fit_per_frame(cache, peak, param, agg, n_frames)
        return None

    def get_omega_eta_grid(self, key: str,
                           omegas_deg: np.ndarray,
                           etas_deg: np.ndarray,
                           *, agg: str = 'mean'
                           ) -> Optional[np.ndarray]:
        """Return an (n_omega, n_eta) grid sampling the stream on the lattice."""
        if not key:
            return None
        kind, rest = self._split_key(key)
        n_o = int(omegas_deg.size)
        n_e = int(etas_deg.size)
        if kind == 'csv':
            cache = self.csv_cache()
            if cache is None:
                return None
            param, peak = self._parse_csv_rest(rest)
            return peak_fit_omega_eta_grid(
                cache, peak, param, omegas_deg, etas_deg)
        if kind in ('prim', 'prim2d', 'aux'):
            per_frame = self.get(key, n_frames=n_o, reducer=agg, agg=agg)
            if per_frame is None:
                return None
            arr = np.asarray(per_frame, dtype=float).ravel()
            if arr.size < n_o:
                pad = np.full(n_o - arr.size, np.nan)
                arr = np.concatenate([arr, pad])
            arr = arr[:n_o]
            return np.broadcast_to(arr[:, None], (n_o, n_e)).copy()
        return None

