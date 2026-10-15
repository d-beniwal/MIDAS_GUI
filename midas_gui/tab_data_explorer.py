"""Tab — Data Explorer: plot metadata and fit results against each other.

Three sub-tabs over one ``MetadataCatalog``:

  Sources           the four paths everything else reads from
  Real space        where on the sample is this signal?
  Pole figure (3-D) what orientations did this scan sample?

Ported from ``mpe_wf_saxs_waxs/gui_data_explorer.py``. Deliberately NOT
ported, following ``tab_zarrviewer``'s precedent for the same source
project: the QMainWindow shell, the font-size strip and the
``rcParams``/widget bookkeeping behind it (this app's own scaling already
applies, and a global rcParams write would silently restyle the two other
matplotlib views in the process), and the Sinogram tab, which upstream
leaves as a 25-line "not yet implemented" placeholder. ``PeakFitPanel``
is not here either -- it was already ported and lives in the Texture tab.

GENERIC, not MIDAS-aware. It walks a container and plots whatever numeric
arrays it finds. ``tab_zarrviewer`` is the MIDAS-schema reader and does
that job properly; a weaker second implementation here would be the
mistake. Worth knowing before concluding the tab is broken on a given
file: ``REtaMap`` (3-D) and ``InstrumentParameters`` (length 1) are
invisible to the walkers by design, and an archive built from TIFF
sources has no ``instrument/`` motor tree at all, so its axis combos come
up empty -- which is what the Real space panel's manual start/step/points
grid is for.
"""
from __future__ import annotations

import os
from typing import Optional

from PyQt5 import QtCore, QtWidgets

from midas_gui import style as S
from midas_gui.metadata_catalog import MetadataCatalog
from midas_gui.pole_figure_3d_panel import PoleFigure3DPanel
from midas_gui.real_space_panel import RealSpacePanel
from midas_gui.stream_sources import (
    discover_cake_siblings, is_zarr, parse_cake_csv, source_exists)


class _SourcesPanel(QtWidgets.QWidget):
    """Tab 0: single place to choose every source file in the explorer.

    Each row mirrors one of the catalog's setters and shows a one-line
    status label so the user can see at a glance whether the file resolves,
    what kind it is, and how many streams it contributed.
    """

    _ROW_SPECS = [
        ('primary', 'Primary source (HDF5 / zarr)',
         'path to HDF5 file or zarr container',
         'Data sources (*.h5 *.hdf5 *.zarr *.zarr.zip);;'
         'HDF5 (*.h5 *.hdf5);;Zarr (*.zarr *.zarr.zip);;All files (*)',
         'Select primary source'),
        ('aux', 'Aux HDF5 (motor fallback)',
         'optional HDF5 for /SMS/* etc. when primary is zarr',
         'HDF5 (*.h5 *.hdf5);;All files (*)',
         'Select auxiliary HDF5'),
        ('cake', 'Cake parameters CSV',
         'cake_parameters CSV (sets pole-figure geometry)',
         'CSV files (*.csv);;All files (*)',
         'Select cake parameters CSV'),
        ('peak', 'Peak-fit CSV (GSAS-2)',
         'optional peak-fit CSV — its columns become streams',
         'CSV files (*.csv);;All files (*)',
         'Select peak-fit CSV'),
    ]

    def __init__(self, catalog: 'MetadataCatalog', parent=None):
        super().__init__(parent)
        self._catalog = catalog
        self._rows: dict = {}
        self._build_ui()
        catalog.catalogChanged.connect(self._refresh_status)
        self._refresh_status()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        intro = QtWidgets.QLabel(
            'Source files for every visualization. Edit a path and every '
            'tab refreshes automatically — these inputs are now shared '
            'instead of duplicated per tab.')
        intro.setWordWrap(True)
        intro.setStyleSheet(f'color:{S.MUTED};font-size:10px;')
        layout.addWidget(intro)

        for slug, label, ph, filt, title in self._ROW_SPECS:
            box = QtWidgets.QGroupBox(label)
            box_lay = QtWidgets.QVBoxLayout(box)
            box_lay.setContentsMargins(8, 6, 8, 6)
            box_lay.setSpacing(4)
            row = QtWidgets.QHBoxLayout()
            edit = QtWidgets.QLineEdit()
            edit.setPlaceholderText(ph)
            edit.editingFinished.connect(
                lambda s=slug, e=edit: self._on_path_changed(s, e.text()))
            browse = QtWidgets.QPushButton('Browse…')
            browse.clicked.connect(
                lambda _checked=False, s=slug, e=edit, f=filt, t=title:
                    self._on_browse(s, e, f, t))
            row.addWidget(edit, 1)
            row.addWidget(browse)
            box_lay.addLayout(row)
            status = QtWidgets.QLabel('(empty)')
            status.setStyleSheet(f'color:{S.MUTED};font-size:10px;')
            box_lay.addWidget(status)
            layout.addWidget(box)
            self._rows[slug] = (edit, status)

        # ── Multi-file span (applies to the Primary source) ─────────
        span_box = QtWidgets.QGroupBox('Multi-file span')
        span_lay = QtWidgets.QHBoxLayout(span_box)
        span_lay.setContentsMargins(8, 6, 8, 6)
        self.span_files_cb = QtWidgets.QCheckBox('Span sibling H5 / zarr files')
        self.span_files_cb.setToolTip(
            'Walk every <base>_NNNNNN.<ext>.h5 (or .zarr / .zarr.zip) in the '
            'primary source folder (restricted to the provided file # range '
            'when set) and read the chosen dataset from each one — '
            'one point per file.')
        self.span_files_cb.setChecked(self._catalog.span_enabled)
        self.span_files_cb.toggled.connect(self._catalog.set_span_enabled)
        span_lay.addWidget(self.span_files_cb)
        span_lay.addSpacing(12)
        span_lay.addWidget(QtWidgets.QLabel('reduction:'))
        self.span_reduce_combo = QtWidgets.QComboBox()
        self.span_reduce_combo.addItems(['full', 'mean', 'first', 'last',
                                         'min', 'max'])
        self.span_reduce_combo.setToolTip(
            'How to collapse a multi-valued dataset within one file. '
            '"full" keeps every value and concatenates across files.')
        idx = self.span_reduce_combo.findText(self._catalog.span_reducer)
        if idx >= 0:
            self.span_reduce_combo.setCurrentIndex(idx)
        self.span_reduce_combo.currentTextChanged.connect(
            self._catalog.set_span_reducer)
        span_lay.addWidget(self.span_reduce_combo)
        span_lay.addSpacing(12)
        self.span_status = QtWidgets.QLabel('')
        self.span_status.setStyleSheet(f'color:{S.MUTED};font-size:10px;')
        span_lay.addWidget(self.span_status, 1)
        layout.addWidget(span_box)

        layout.addStretch(1)

    def set_span_status(self, text: str) -> None:
        """Real-space tab calls this after each redraw to surface the file/frame
        count next to the toggle (so the status lives with the control)."""
        self.span_status.setText(text or '')

    def _on_path_changed(self, slug: str, path: str):
        path = path.strip()
        setter = {
            'primary': self._catalog.set_primary_source,
            'aux': self._catalog.set_aux_source,
            'cake': self._catalog.set_cake_csv,
            'peak': self._catalog.set_peak_fit_csv,
        }[slug]
        setter(path)

    def _on_browse(self, slug: str, edit: QtWidgets.QLineEdit,
                   filter_str: str, title: str):
        # Default to cwd (where the GUI was launched from) when the field
        # is empty — beamline users typically `cd` into the scan folder
        # before launching, so cwd is the right "I just want to grab a
        # file from here" anchor.  Falls through to ~ only if cwd vanished.
        current = edit.text().strip()
        if current:
            start = current
        else:
            try:
                start = os.getcwd()
            except OSError:
                start = os.path.expanduser('~')
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, title, start, filter_str)
        if path:
            edit.setText(path)
            self._on_path_changed(slug, path)

    def _refresh_status(self):
        statuses = {
            'primary': self._describe_primary(),
            'aux': self._describe_aux(),
            'cake': self._describe_cake(),
            'peak': self._describe_peak(),
        }
        paths = {
            'primary': self._catalog.primary_path,
            'aux': self._catalog.aux_h5_path,
            'cake': self._catalog.cake_csv_path,
            'peak': self._catalog.peak_fit_csv_path,
        }
        for slug, (edit, lbl) in self._rows.items():
            expected = paths[slug]
            if expected != edit.text().strip():
                edit.blockSignals(True)
                edit.setText(expected)
                edit.blockSignals(False)
            lbl.setText(statuses[slug])

    def _describe_primary(self) -> str:
        p = self._catalog.primary_path
        if not p:
            return '(empty)'
        if not source_exists(p):
            return '(not found)'
        kind = 'zarr' if is_zarr(p) else 'HDF5'
        # stream_counts(), not the private lists: it runs _ensure_scanned,
        # so the status text cannot describe a file that is no longer
        # selected. It also lets us say when the 2-D list was capped.
        n_1d, n_2d, _n_aux = self._catalog.stream_counts()
        suffix = f', {n_2d} 2-D' if n_2d else ''
        return f'{kind}: {n_1d} 1-D datasets{suffix}'

    def _describe_aux(self) -> str:
        p = self._catalog.aux_h5_path
        if not p:
            return '(empty)'
        if not os.path.isfile(p):
            return '(not found)'
        return f'HDF5: {self._catalog.stream_counts()[2]} 1-D datasets'

    def _describe_cake(self) -> str:
        p = self._catalog.cake_csv_path
        if not p:
            return '(empty)'
        if not os.path.isfile(p):
            return '(not found)'
        d = parse_cake_csv(p)
        if d is None:
            return 'unreadable'
        siblings = discover_cake_siblings(p)
        return (f'OME_SUM={d.get("OME_SUM", "?")}, '
                f'OME_STEP={d.get("OME_STEP", "?")}, '
                f'{len(siblings)} panel(s)')

    def _describe_peak(self) -> str:
        p = self._catalog.peak_fit_csv_path
        if not p:
            return '(empty)'
        if not os.path.isfile(p):
            return '(not found)'
        cache = self._catalog.csv_cache()
        if cache is None:
            return 'unreadable'
        peaks = cache.get('peak_indices') or [0]
        return f'{cache["rows"]} rows, {len(peaks)} peak(s)'



class DataExplorerTab(QtWidgets.QWidget):
    """The tab itself: one catalog, three panels listening to it.

    No cross-panel wiring. Every panel reacts to ``catalogChanged`` and
    re-reads, which is why they could be ported one at a time.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._catalog = MetadataCatalog(self)
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(6)
        self._tabs = QtWidgets.QTabWidget()

        self._sources = _SourcesPanel(self._catalog)
        # The Real space panel reports its multi-file span upward rather
        # than reaching into the Sources panel, which is what let it be
        # lifted out of the source's monolithic widget at all.
        self._real = RealSpacePanel(
            self._catalog, span_status_cb=self._sources.set_span_status)
        self._pole = PoleFigure3DPanel(self._catalog)

        self._tabs.addTab(self._sources, "Sources")
        self._tabs.addTab(self._real, "Real space")
        # "(3-D)" on purpose: the Texture tab has a pole figure too, and it
        # answers a different question -- a stereographic projection from
        # ONE frame's azimuthal intensity, versus this one's 3-D locus of
        # sampled orientations across omega.
        self._tabs.addTab(self._pole, "Pole figure (3-D)")
        v.addWidget(self._tabs)

    # ── public entry points ────────────────────────────────────────

    def set_source(self, path: str) -> None:
        """Point the catalog at a primary source (file or directory)."""
        self._catalog.set_primary_source(path or "")

    def refresh(self) -> None:
        self._real.refresh()
        self._pole.refresh() if hasattr(self._pole, "refresh") else None
