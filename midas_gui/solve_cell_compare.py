"""Solve Cell "Compare domains" view (V13): per-lattice cell table, pairwise
basis-aware comparison (metric match -> basis change -> misorientation) with a
reviewer conclusion column, an angle heat-map and two side-by-side reciprocal
scenes (optionally camera-linked).

Nothing here changes domain claims: the automatic conclusion is a heuristic
suggestion and the reviewer's choice is stored separately (``domain_review``
product) with the UB fingerprints it was made against.
"""
from __future__ import annotations

from typing import Callable, Optional

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PyQt5 import QtCore, QtGui, QtWidgets

from midas_gui.plot_limits import limit_to_data
from midas_gui.pyvista_views import PyVistaScatter3DView
from midas_gui.solve_cell import domain_compare as dc

_CONCL_COLOR = {"same lattice relabeled": (60, 170, 120), "compatible distinct orientation": (230, 160, 60),
                "different metric": (200, 80, 80), "insufficient evidence": (110, 110, 120)}
_REVIEW_CHOICES = ["(unreviewed)"] + list(dc.CONCLUSIONS)
_PANEL_COLORS = ["#4da3ff", "#ffa94d", "#69db7c", "#da77f2", "#ffd43b", "#63e6be"]


class DomainCompareView(QtWidgets.QWidget):
    reviewChanged = QtCore.pyqtSignal(str, str, str)       # key_a, key_b, conclusion
    spotClicked = QtCore.pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.entries: list = []
        self.tables: dict = {}
        self.reviews: dict = {}                      # (a, b) -> reviewer conclusion
        self._scene_data: Optional[Callable] = None
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.status = QtWidgets.QLabel("Needs at least one refined domain or an ab-initio seed.")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        lay.addWidget(split, stretch=1)

        top = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.cell_table = QtWidgets.QTableWidget()
        self.pair_table = QtWidgets.QTableWidget()
        left = QtWidgets.QWidget(); lv = QtWidgets.QVBoxLayout(left); lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(QtWidgets.QLabel("<b>Lattices</b> (candidate ≠ accepted domain)"))
        lv.addWidget(self.cell_table)
        lv.addWidget(QtWidgets.QLabel("<b>Pairwise comparison</b> — automatic suggestion vs reviewer conclusion"))
        lv.addWidget(self.pair_table)
        top.addWidget(left)
        self.glw = pg.GraphicsLayoutWidget()
        self.heat_plot = self.glw.addPlot(title="Misorientation (°, modulo lattice symmetry)")
        self.heat_img = pg.ImageItem(axisOrder="row-major")
        self.heat_plot.addItem(self.heat_img)
        self.heat_plot.invertY(True)
        top.addWidget(self.glw)
        top.setSizes([760, 380])
        split.addWidget(top)

        bottom = QtWidgets.QWidget(); bl = QtWidgets.QVBoxLayout(bottom); bl.setContentsMargins(0, 0, 0, 0)
        bar = QtWidgets.QHBoxLayout()
        self.combo_a, self.combo_b = QtWidgets.QComboBox(), QtWidgets.QComboBox()
        self.link = QtWidgets.QCheckBox("link cameras")
        self.link.setChecked(True)
        for w in (QtWidgets.QLabel("Left:"), self.combo_a, QtWidgets.QLabel("Right:"), self.combo_b, self.link):
            bar.addWidget(w)
        bar.addStretch(1)
        bl.addLayout(bar)
        scenes = QtWidgets.QHBoxLayout()
        self.view_a, self.view_b = PyVistaScatter3DView(), PyVistaScatter3DView()
        scenes.addWidget(self.view_a); scenes.addWidget(self.view_b)
        bl.addLayout(scenes, stretch=1)
        split.addWidget(bottom)
        split.setSizes([360, 420])
        for v in (self.view_a, self.view_b):
            v.spotClicked.connect(self.spotClicked)
        self.combo_a.currentIndexChanged.connect(lambda _=None: self._draw_scene(self.view_a, self.combo_a))
        self.combo_b.currentIndexChanged.connect(lambda _=None: self._draw_scene(self.view_b, self.combo_b))
        self._syncing = False
        for src, dst in ((self.view_a, self.view_b), (self.view_b, self.view_a)):
            try:
                src.interactor.iren.add_observer("EndInteractionEvent", lambda *_a, s=src, d=dst: self._sync(s, d))
            except Exception:
                pass                                   # camera link is a convenience only

    # ── data ────────────────────────────────────────────────────────
    def set_data(self, entries: list, tables: Optional[dict], reviews: Optional[dict], scene_data: Callable):
        self.entries, self.tables, self._scene_data = entries, tables or {}, scene_data
        self.reviews = dict(reviews or {})
        for c in (self.combo_a, self.combo_b):
            c.blockSignals(True); c.clear()
            for e in entries:
                c.addItem(e["label"], e["key"])
            c.blockSignals(False)
        if len(entries) > 1:
            self.combo_b.setCurrentIndex(1)
        self._fill_tables()
        self._draw_scene(self.view_a, self.combo_a)
        self._draw_scene(self.view_b, self.combo_b)
        n = len(entries)
        self.status.setText(
            f"{n} lattice(s)." + ("" if n > 1 else " A comparison needs at least two (add a domain with Index Remaining Spots).")
            + " Metric match is tested first, then orientation through the matching basis change; "
              "thresholds are heuristics and no claim is ever changed from this view.")

    def _fill_tables(self):
        d = self.tables.get("domains")
        t = self.cell_table
        t.clear()
        if d is None or not len(d):
            t.setRowCount(0); self.heat_img.clear(); self.pair_table.setRowCount(0)
            return
        cols = ["label", "kind", "n_reflections", "volume_A3", "cell_a", "cell_b", "cell_c", "cell_alpha",
                "cell_beta", "cell_gamma", "rms_drlv", "conventional_system", "conventional_cell", "panel_counts"]
        t.setColumnCount(len(cols)); t.setRowCount(len(d)); t.setHorizontalHeaderLabels(cols)
        for r, row in enumerate(d.itertuples(index=False)):
            for c, name in enumerate(cols):
                v = getattr(row, name)
                t.setItem(r, c, QtWidgets.QTableWidgetItem(f"{v:.5g}" if isinstance(v, (float, np.floating)) else str(v)))
        t.resizeColumnsToContents()
        self._fill_pairs()

    def _fill_pairs(self):
        p = self.tables.get("pairs")
        t = self.pair_table
        t.clear()
        keys = self.tables["domains"].key.tolist()
        n = len(keys)
        ang = np.full((n, n), np.nan)
        if p is None or not len(p):
            t.setRowCount(0); self.heat_img.clear()
            return
        cols = ["pair", "metric_match", "angle_deg", "n_matches", "basis change M", "B∈A", "A∈B", "suggested", "reviewer"]
        t.setColumnCount(len(cols)); t.setRowCount(len(p)); t.setHorizontalHeaderLabels(cols)
        for r, row in enumerate(p.itertuples(index=False)):
            i, j = keys.index(row.a), keys.index(row.b)
            ang[i, j] = ang[j, i] = row.angle_deg
            vals = [f"{row.a} ↔ {row.b}", "yes" if row.metric_match else "no",
                    "—" if not np.isfinite(row.angle_deg) else f"{row.angle_deg:.2f}", str(row.n_matches), row.M or "—",
                    str(row.b_spots_fit_by_a), str(row.a_spots_fit_by_b), row.conclusion]
            for c, v in enumerate(vals):
                it = QtWidgets.QTableWidgetItem(v)
                if c == 7:
                    it.setBackground(QtGui.QColor(*_CONCL_COLOR[row.conclusion]))
                t.setItem(r, c, it)
            combo = QtWidgets.QComboBox(); combo.addItems(_REVIEW_CHOICES)
            cur = self.reviews.get((row.a, row.b))
            if cur in _REVIEW_CHOICES:
                combo.setCurrentIndex(_REVIEW_CHOICES.index(cur))
            combo.currentTextChanged.connect(
                lambda txt, a=row.a, b=row.b: self._on_review(a, b, txt))
            t.setCellWidget(r, 8, combo)
        t.resizeColumnsToContents()
        # heat-map: angle where metrics match, grey where they don't
        img = np.nan_to_num(ang, nan=-1.0)
        np.fill_diagonal(img, 0.0)
        rgb = pg.colormap.get("viridis").getLookupTable(nPts=256, alpha=False)[
            np.clip((img / 63.0) * 255, 0, 255).astype(np.uint8)]
        rgb[img < 0] = (60, 60, 66)
        self.heat_img.setImage(rgb, autoLevels=False)
        self.heat_img.setRect(QtCore.QRectF(0, 0, n, n))
        ax = self.heat_plot.getAxis("bottom"); ay = self.heat_plot.getAxis("left")
        ticks = [[(k + 0.5, lab) for k, lab in enumerate(self.tables["domains"].label.tolist())]]
        ax.setTicks(ticks); ay.setTicks(ticks)
        for it in getattr(self, "_heat_text", []):
            self.heat_plot.removeItem(it)
        self._heat_text = []
        for i in range(n):
            for j in range(n):
                txt = "0" if i == j else ("n/a" if not np.isfinite(ang[i, j]) else f"{ang[i, j]:.1f}°")
                ti = pg.TextItem(txt, color="w", anchor=(0.5, 0.5)); ti.setPos(j + 0.5, i + 0.5)
                self.heat_plot.addItem(ti); self._heat_text.append(ti)
        limit_to_data(self.heat_plot)

    def _on_review(self, a, b, txt):
        self.reviews[(a, b)] = None if txt == _REVIEW_CHOICES[0] else txt
        self.reviewChanged.emit(a, b, "" if txt == _REVIEW_CHOICES[0] else txt)

    # ── scenes ──────────────────────────────────────────────────────
    def _draw_scene(self, view: PyVistaScatter3DView, combo: QtWidgets.QComboBox):
        key = combo.currentData()
        if key is None or self._scene_data is None:
            view.set_groups([]); view.set_basis(None)
            return
        groups, UB = self._scene_data(key)
        view.set_groups(groups)
        view.set_basis(2 * np.pi * np.asarray(UB, float) if UB is not None else None)

    def _sync(self, src, dst):
        if self.link.isChecked() and not self._syncing:
            self._syncing = True
            try:
                dst.camera_position = src.camera_position
                dst.interactor.render()
            finally:
                self._syncing = False
