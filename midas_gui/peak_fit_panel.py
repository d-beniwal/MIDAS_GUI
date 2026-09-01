"""GSAS-2 sequential peak-fit CSV viewer — ``PeakFitPanel``.

Ported from ``mpe_wf_saxs_waxs/gui_data_explorer.py``'s ``PeakFitPanel`` +
module-level ``parse_peak_fit_csv()``. This is MIDAS_GUI's first embedded
matplotlib canvas (every other view uses pyqtgraph) — accepted as the
lower-effort path since there's no existing GSAS-2 peak-fit CSV producer or
consumer to build on, and matplotlib is already an environment dependency.

Deliberate simplifications vs. the source:
  - Drops ``MetadataStreamPicker``/"Real-space color" override (a large,
    mpe_wf-specific custom-color-stream picker) — the Real-space view keeps
    only its default path: aggregate the selected peak/param per frame.
  - ``PeakFitCatalog`` here is a small purpose-built adapter (peak-fit CSV
    path + optional primary/aux HDF5 path, with a lazily-parsed/memoized
    cache), not a port of mpe_wf's much larger, general-purpose
    ``MetadataCatalog`` — ``PeakFitPanel`` only ever touches
    ``catalogChanged``/``csv_cache()``/``primary_path``/``aux_h5_path``.
  - The Real-space view's source reads are HDF5-only (``read_h5_dataset``) —
    no zarr support in this pass (the zarr *cake* output from Phase 1 isn't
    a *source* for samX/samY motor positions).
"""
from __future__ import annotations

import csv
import os
import re
from typing import Optional

import numpy as np
from PyQt5 import QtCore, QtWidgets

import matplotlib
matplotlib.use("Qt5Agg")
import matplotlib.cm  # noqa: E402
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

# ── CSV parsing (verbatim from mpe_wf_saxs_waxs/gui_data_explorer.py) ──────

_PEAK_FIT_NAME_RX = re.compile(
    r'img=(?P<img>\d+)\s+omega=(?P<omega>[-+]?\d+\.?\d*)\s+eta=(?P<eta>[-+]?\d+\.?\d*)')

# Pulls the sibling-file number out of the leading file basename in the CSV
# "name" column, e.g. "C611_017Fe_1_load9_010128.vrx.zarr.zip img=0 omega=..."
# -> 10128.
_PEAK_FIT_FILE_RX = re.compile(
    r'^(?P<base>.+?)_(?P<num>\d{4,7})(?:\.[^.]+)?(?:\.h5|\.zarr\.zip|\.zarr)\b')

_PEAK_FIT_PARAM_KEYS = (
    "int", "pos", "sig", "gam",
    "esd-int", "esd-pos", "esd-sig", "esd-gam",
)
_PEAK_FIT_PARAM_LABELS = {
    "int": "Intensity",
    "pos": "Position (2θ °)",
    "sig": "Width σ",
    "gam": "Width γ",
    "esd-int": "Intensity ESD",
    "esd-pos": "Position ESD",
    "esd-sig": "Width σ ESD",
    "esd-gam": "Width γ ESD",
}
_PEAK_FIT_EXTRA_COLS = (("Rwp", "Rwp"), ("GOF", "GOF"))


def parse_peak_fit_csv(path: str) -> Optional[dict]:
    """Parse a GSAS-2 sequential peak-fit CSV into numpy arrays.

    Returns a dict with the common per-row arrays plus only the
    ``<param><peak>`` arrays whose columns actually exist in the header.
    ``peak_indices`` lists the integer peak indices detected from ``pos<N>``
    columns; ``params`` flags which of int/pos/sig/gam appear.
    Returns None when the file is missing or unparseable.
    """
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, newline="") as fh:
            reader = csv.DictReader(fh)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)
    except Exception:
        return None
    if not rows or not fieldnames:
        return None

    pk_rx = re.compile(r"^pos(\d+)$")
    peak_indices = sorted({int(m.group(1))
                           for h in fieldnames
                           for m in [pk_rx.match(h.strip())] if m})
    if not peak_indices:
        pk_rx_int = re.compile(r"^int(\d+)$")
        peak_indices = sorted({int(m.group(1))
                               for h in fieldnames
                               for m in [pk_rx_int.match(h.strip())] if m})
    if not peak_indices:
        peak_indices = [0]

    header_set = {h.strip() for h in fieldnames}
    params = {k: any(f"{k}{i}" in header_set for i in peak_indices)
              for k in _PEAK_FIT_PARAM_KEYS}

    n = len(rows)
    omega = np.full(n, np.nan)
    eta = np.full(n, np.nan)
    img_idx = np.full(n, -1, dtype=int)
    file_num = np.full(n, -1, dtype=int)
    use = np.ones(n, dtype=bool)
    rwp = np.full(n, np.nan)
    gof = np.full(n, np.nan)
    arrays: dict = {
        f"{p}{i}": np.full(n, np.nan)
        for p in _PEAK_FIT_PARAM_KEYS if params[p]
        for i in peak_indices
    }

    for k, row in enumerate(rows):
        nm = row.get("name", "")
        m = _PEAK_FIT_NAME_RX.search(nm)
        if m:
            try:
                omega[k] = float(m.group("omega"))
                eta[k] = float(m.group("eta"))
                img_idx[k] = int(m.group("img"))
            except ValueError:
                pass
        fm = _PEAK_FIT_FILE_RX.match(nm)
        if fm:
            try:
                file_num[k] = int(fm.group("num"))
            except ValueError:
                pass
        try:
            use[k] = row.get("Use", "True").strip().lower() not in ("false", "0", "")
        except (AttributeError, ValueError):
            pass
        try:
            rwp[k] = float(row.get("Rwp", "nan") or "nan")
        except ValueError:
            pass
        try:
            gof[k] = float(row.get("GOF", "nan") or "nan")
        except ValueError:
            pass
        for key in arrays:
            try:
                arrays[key][k] = float(row.get(key, "nan") or "nan")
            except ValueError:
                pass

    return {
        "rows": n,
        "omega": omega, "eta": eta, "img_idx": img_idx,
        "file_num": file_num,
        "use": use, "Rwp": rwp, "GOF": gof,
        "peak_indices": peak_indices,
        "params": params,
        **arrays,
    }


def read_h5_dataset(h5_path: str, ds_path: str):
    """Read a 1-D dataset by HDF5 path; return None on failure."""
    if not (h5_path and ds_path and os.path.isfile(h5_path)):
        return None
    try:
        import h5py
        with h5py.File(h5_path, "r") as f:
            obj = f[ds_path]
            if not isinstance(obj, h5py.Dataset) or obj.ndim != 1:
                return None
            return np.asarray(obj[...])
    except Exception:
        return None


# ── Minimal catalog adapter (replaces mpe_wf's much larger MetadataCatalog) ─

class PeakFitCatalog(QtCore.QObject):
    """Owns exactly what ``PeakFitPanel`` needs: a peak-fit CSV path (lazily
    parsed and memoized via ``csv_cache()``) plus an optional primary HDF5
    source path (for the Real-space view's samX/samY lookup)."""
    catalogChanged = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.primary_path: str = ""
        self.aux_h5_path: str = ""
        self._csv_path: str = ""
        self._cache: Optional[dict] = None
        self._cache_path: Optional[str] = None

    def set_peak_fit_csv(self, path: str) -> None:
        self._csv_path = path or ""
        self._cache = None
        self.catalogChanged.emit()

    def set_primary_source(self, path: str) -> None:
        self.primary_path = path or ""
        self.catalogChanged.emit()

    def set_aux_h5_path(self, path: str) -> None:
        self.aux_h5_path = path or ""
        self.catalogChanged.emit()

    def csv_cache(self) -> Optional[dict]:
        if self._cache is None or self._cache_path != self._csv_path:
            self._cache = parse_peak_fit_csv(self._csv_path)
            self._cache_path = self._csv_path
        return self._cache


# ── Panel ────────────────────────────────────────────────────────────────

class PeakFitPanel(QtWidgets.QWidget):
    """GSAS-2 sequential peak-fit result viewer driven by a ``PeakFitCatalog``."""

    _PARAMS = [(k, _PEAK_FIT_PARAM_LABELS[k]) for k in _PEAK_FIT_PARAM_KEYS]
    _EXTRA_PARAMS = list(_PEAK_FIT_EXTRA_COLS)
    _VIEWS = ["Omega-Eta map", "Real space (samX/samY)", "Azimuthal", "Fit quality"]
    _AGG_METHODS = ["mean", "std", "min", "max"]

    def __init__(self, catalog: PeakFitCatalog, parent=None):
        super().__init__(parent)
        self._catalog = catalog
        self._build_ui()
        catalog.catalogChanged.connect(self._on_catalog_changed)

    # ── UI ───────────────────────────────────────────────────────────
    def _build_ui(self):
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(6)

        src = QtWidgets.QHBoxLayout()
        self._csv_ed = QtWidgets.QLineEdit()
        self._csv_ed.setPlaceholderText("GSAS-2 sequential peak-fit CSV…")
        self._csv_ed.editingFinished.connect(
            lambda: self._catalog.set_peak_fit_csv(self._csv_ed.text().strip()))
        csv_browse = QtWidgets.QPushButton("Browse for peak-fit CSV…")
        csv_browse.clicked.connect(self._browse_csv)
        src.addWidget(self._csv_ed, 1)
        src.addWidget(csv_browse)
        v.addLayout(src)

        src2 = QtWidgets.QHBoxLayout()
        self._primary_ed = QtWidgets.QLineEdit()
        self._primary_ed.setPlaceholderText(
            "Primary HDF5 (samX/samY) — optional, for Real-space view…")
        self._primary_ed.editingFinished.connect(
            lambda: self._catalog.set_primary_source(self._primary_ed.text().strip()))
        primary_browse = QtWidgets.QPushButton("Browse for primary source…")
        primary_browse.clicked.connect(self._browse_primary)
        src2.addWidget(self._primary_ed, 1)
        src2.addWidget(primary_browse)
        v.addLayout(src2)

        ctrl = QtWidgets.QHBoxLayout()
        ctrl.addWidget(QtWidgets.QLabel("Peak:"))
        self.peak_combo = QtWidgets.QComboBox()
        self.peak_combo.addItem("0")
        self.peak_combo.currentIndexChanged.connect(self._refresh)
        ctrl.addWidget(self.peak_combo)

        ctrl.addSpacing(12)
        ctrl.addWidget(QtWidgets.QLabel("Param:"))
        self.param_combo = QtWidgets.QComboBox()
        for key, name in self._PARAMS:
            self.param_combo.addItem(name, userData=key)
        for col, name in self._EXTRA_PARAMS:
            self.param_combo.addItem(name, userData=col)
        self.param_combo.currentIndexChanged.connect(self._refresh)
        ctrl.addWidget(self.param_combo)

        ctrl.addSpacing(12)
        ctrl.addWidget(QtWidgets.QLabel("Agg η:"))
        self.agg_combo = QtWidgets.QComboBox()
        self.agg_combo.addItems(self._AGG_METHODS)
        self.agg_combo.setToolTip(
            "Collapse multiple η bins per scan position "
            "(Real-space view & Azimuthal mean).")
        self.agg_combo.currentIndexChanged.connect(self._refresh)
        ctrl.addWidget(self.agg_combo)

        ctrl.addSpacing(12)
        ctrl.addWidget(QtWidgets.QLabel("View:"))
        self.view_combo = QtWidgets.QComboBox()
        self.view_combo.addItems(self._VIEWS)
        self.view_combo.currentIndexChanged.connect(self._refresh)
        ctrl.addWidget(self.view_combo)
        ctrl.addStretch(1)
        v.addLayout(ctrl)

        self.fig = Figure(figsize=(5, 3.5), tight_layout=True)
        self.canvas = FigureCanvas(self.fig)
        self.canvas.setMinimumHeight(240)
        self.canvas.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                  QtWidgets.QSizePolicy.Expanding)
        v.addWidget(self.canvas, 1)

        self.status_lbl = QtWidgets.QLabel("")
        self.status_lbl.setWordWrap(True)
        v.addWidget(self.status_lbl)

        self._draw_placeholder()

    def _browse_csv(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open peak-fit CSV", "", "CSV (*.csv);;All (*)")
        if path:
            self._csv_ed.setText(path)
            self._catalog.set_peak_fit_csv(path)

    def _browse_primary(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open primary source", "", "HDF5 (*.h5 *.hdf5 *.nxs);;All (*)")
        if path:
            self._primary_ed.setText(path)
            self._catalog.set_primary_source(path)

    def _draw_placeholder(self):
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        ax.text(0.5, 0.5, "Browse for a peak-fit CSV above to begin.",
                ha="center", va="center", transform=ax.transAxes,
                fontsize=10, color="gray")
        ax.axis("off")
        self.canvas.draw_idle()

    # ── Catalog reactivity ──────────────────────────────────────────
    def _on_catalog_changed(self):
        cache = self._catalog.csv_cache()
        if cache is not None:
            self._sync_combos_to_cache(cache)
            n = cache["rows"]
            n_used = int(np.sum(cache["use"]))
            omg = cache["omega"]; eta = cache["eta"]
            n_omega = len(np.unique(omg[~np.isnan(omg)]))
            n_eta = len(np.unique(eta[~np.isnan(eta)]))
            peaks = cache.get("peak_indices") or [0]
            self.status_lbl.setText(
                f"Loaded {n} rows ({n_used} used)  ·  "
                f"{n_omega} ω steps × {n_eta} η bins  ·  "
                f"{len(peaks)} peak{'s' if len(peaks) != 1 else ''}")
        else:
            self.status_lbl.setText("")
        self._refresh()

    def _sync_combos_to_cache(self, cache: dict) -> None:
        peak_indices = cache.get("peak_indices") or [0]
        params = cache.get("params") or {k: True for k in _PEAK_FIT_PARAM_KEYS}

        prev_peak = self.peak_combo.currentText()
        self.peak_combo.blockSignals(True)
        self.peak_combo.clear()
        self.peak_combo.addItems([str(i) for i in peak_indices])
        if prev_peak in [str(i) for i in peak_indices]:
            self.peak_combo.setCurrentText(prev_peak)
        self.peak_combo.blockSignals(False)

        prev_param = self.param_combo.currentData()
        self.param_combo.blockSignals(True)
        self.param_combo.clear()
        for key, name in self._PARAMS:
            if params.get(key):
                self.param_combo.addItem(name, userData=key)
        for col, name in self._EXTRA_PARAMS:
            self.param_combo.addItem(name, userData=col)
        if prev_param is not None:
            idx = self.param_combo.findData(prev_param)
            if idx >= 0:
                self.param_combo.setCurrentIndex(idx)
        self.param_combo.blockSignals(False)

    # ── Column resolution ───────────────────────────────────────────
    def _get_col(self):
        try:
            pidx = int(self.peak_combo.currentText() or "0")
        except ValueError:
            pidx = 0
        key = self.param_combo.currentData()
        label = self.param_combo.currentText()
        if key in ("Rwp", "GOF"):
            return key, label
        if key is None:
            key = "int"
        return f"{key}{pidx}", f"{label} (peak {pidx})"

    # ── Draw dispatch ────────────────────────────────────────────────
    def _refresh(self):
        cache = self._catalog.csv_cache()
        if cache is None:
            self._draw_placeholder()
            return
        col, label = self._get_col()
        if col not in cache:
            self._draw_placeholder()
            return
        self.fig.clear()
        view = self.view_combo.currentText()
        if view == "Omega-Eta map":
            self._draw_omega_eta(cache, col, label)
        elif view == "Real space (samX/samY)":
            self._draw_real_space(cache, col, label)
        elif view == "Azimuthal":
            self._draw_azimuthal(cache, col, label)
        else:
            self._draw_fit_quality(cache)
        self.canvas.draw_idle()

    # ── Views ───────────────────────────────────────────────────────
    def _draw_omega_eta(self, d, col, label):
        vals = d[col]; omega = d["omega"]; eta = d["eta"]
        omegas = np.sort(np.unique(omega[~np.isnan(omega)]))
        etas = np.sort(np.unique(eta[~np.isnan(eta)]))
        if not omegas.size or not etas.size:
            return
        grid = np.full((len(omegas), len(etas)), np.nan)
        o_idx = {v: i for i, v in enumerate(omegas)}
        e_idx = {v: i for i, v in enumerate(etas)}
        for k in range(len(vals)):
            if np.isnan(omega[k]) or np.isnan(eta[k]) or np.isnan(vals[k]):
                continue
            oi = o_idx.get(omega[k]); ei = e_idx.get(eta[k])
            if oi is not None and ei is not None:
                grid[oi, ei] = vals[k]
        ax = self.fig.add_subplot(111)
        im = ax.pcolormesh(etas, omegas, grid, cmap="viridis", shading="auto")
        self.fig.colorbar(im, ax=ax, label=label)
        ax.set_xlabel("η (deg)"); ax.set_ylabel("ω (deg)")
        ax.set_title(f"Omega-Eta map: {label}")

    def _draw_azimuthal(self, d, col, label):
        vals = d[col]; omega = d["omega"]; eta = d["eta"]; use = d["use"]
        omegas = np.sort(np.unique(omega[~np.isnan(omega)]))
        etas_u = np.sort(np.unique(eta[~np.isnan(eta)]))
        ax = self.fig.add_subplot(111)

        cmap = (matplotlib.cm.get_cmap("viridis") if hasattr(matplotlib.cm, "get_cmap")
               else matplotlib.colormaps["viridis"])
        n_om = max(len(omegas) - 1, 1)
        for i, om in enumerate(omegas):
            mask = (~np.isnan(omega)) & (~np.isnan(vals)) & (omega == om) & use
            if np.sum(mask) < 2:
                continue
            order = np.argsort(eta[mask])
            ax.plot(eta[mask][order], vals[mask][order], "-",
                    color=cmap(i / n_om), alpha=0.25, lw=0.8)

        mean_v = np.array([
            np.nanmean(vals[(~np.isnan(omega)) & (~np.isnan(vals)) & (eta == e) & use])
            if np.any((~np.isnan(omega)) & (~np.isnan(vals)) & (eta == e) & use)
            else np.nan
            for e in etas_u])
        std_v = np.array([
            np.nanstd(vals[(~np.isnan(omega)) & (~np.isnan(vals)) & (eta == e) & use])
            if np.any((~np.isnan(omega)) & (~np.isnan(vals)) & (eta == e) & use)
            else np.nan
            for e in etas_u])
        valid = ~np.isnan(mean_v)
        if np.any(valid):
            ax.plot(etas_u[valid], mean_v[valid], "k-", lw=2, label="mean over ω")
            ax.fill_between(etas_u[valid], mean_v[valid] - std_v[valid],
                            mean_v[valid] + std_v[valid], color="k", alpha=0.15, label="±1σ")
            ax.legend(fontsize=8)
        ax.set_xlabel("η (deg)"); ax.set_ylabel(label)
        ax.set_title(f"Azimuthal variation: {label}")
        ax.grid(True, alpha=0.3)

    def _draw_real_space(self, d, col, label):
        h5_path = self._catalog.primary_path
        ax = self.fig.add_subplot(111)

        def _msg(txt, color="gray"):
            ax.text(0.5, 0.5, txt, ha="center", va="center",
                    transform=ax.transAxes, fontsize=9, color=color,
                    wrap=True, multialignment="center")
            ax.axis("off")

        if not h5_path or not os.path.isfile(h5_path):
            _msg("Set the primary source above\nto enable the real-space view.")
            return

        sam_x = sam_y = None
        try:
            for key in ("measurement/samX", "entry/samX", "samX"):
                arr = read_h5_dataset(h5_path, key)
                if arr is None and self._catalog.aux_h5_path:
                    arr = read_h5_dataset(self._catalog.aux_h5_path, key)
                if arr is not None:
                    sam_x = arr; break
            for key in ("measurement/samY", "entry/samY", "samY"):
                arr = read_h5_dataset(h5_path, key)
                if arr is None and self._catalog.aux_h5_path:
                    arr = read_h5_dataset(self._catalog.aux_h5_path, key)
                if arr is not None:
                    sam_y = arr; break
        except Exception as exc:
            _msg(f"Source read error:\n{exc}", color="red")
            return

        if sam_x is None or sam_y is None:
            _msg("samX or samY not found in source.\n"
                "Set an Aux HDF5 with motor positions if the primary lacks them.")
            return

        vals = d[col]; img_idx = d["img_idx"]; use = d["use"]
        agg_name = self.agg_combo.currentText()
        agg_fn = {"mean": np.nanmean, "std": np.nanstd,
                 "min": np.nanmin, "max": np.nanmax}[agg_name]

        xs, ys, cs = [], [], []
        for img in np.unique(img_idx[img_idx >= 0]):
            if img >= len(sam_x) or img >= len(sam_y):
                continue
            mask = (img_idx == img) & (~np.isnan(vals)) & use
            if not np.any(mask):
                continue
            c_val = agg_fn(vals[mask])
            if np.isnan(c_val):
                continue
            xs.append(float(sam_x[img])); ys.append(float(sam_y[img])); cs.append(c_val)

        if not xs:
            _msg("No data to plot after aggregation.")
            return

        xs, ys, cs = np.array(xs), np.array(ys), np.array(cs)
        sc = ax.scatter(xs, ys, c=cs, cmap="viridis", s=20)
        cbar_label = f"{agg_name}({label}) over η"
        self.fig.colorbar(sc, ax=ax, label=cbar_label)
        ax.set_xlabel("samX"); ax.set_ylabel("samY")
        ax.set_title(f"Real space: {cbar_label}")
        ax.set_aspect("equal", adjustable="datalim")
        ax.grid(True, alpha=0.3)

    def _draw_fit_quality(self, d):
        rwp = d["Rwp"]; use = d["use"]
        no = np.arange(len(rwp))
        ax = self.fig.add_subplot(111)
        ax.plot(no[use], rwp[use], "b.", ms=3, alpha=0.7, label="used")
        if np.any(~use):
            ax.plot(no[~use], rwp[~use], "rx", ms=5, label="excluded")
        ax.set_xlabel("Row index"); ax.set_ylabel("Rwp")
        ax.set_title("Fit quality (Rwp)")
        ax.legend(fontsize=8)
        ax.grid(True, alpha=0.3)
