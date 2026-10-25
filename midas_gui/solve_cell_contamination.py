"""Solve Cell "Contamination" view (V12): evidence behind the diamond/gasket
flags. A radial match is only a *candidate* flag -- same-radius sample and
diamond reflections are not distinguished here -- so every plot shows the
spots, the allowed-line markers and the tolerance, and the tolerance box
previews which stable spot IDs a different tolerance WOULD flip without
changing any existing flag.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PyQt5 import QtCore, QtWidgets

from midas_gui.solve_cell import viz_products

_FLAG = "#ff5c5c"
_KEEP = "#4da3ff"


class ContaminationView(QtWidgets.QWidget):
    spotClicked = QtCore.pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(QtWidgets.QLabel("Preview tolerance (° 2θ):"))
        self.tol = QtWidgets.QDoubleSpinBox()
        self.tol.setDecimals(3); self.tol.setRange(0.001, 2.0); self.tol.setSingleStep(0.01); self.tol.setValue(0.07)
        self.tol.setToolTip("Shows which spots would be (un)flagged at this tolerance. Preview only: existing "
                            "flags and domain claims are never changed from here.")
        self.tol.valueChanged.connect(lambda _=None: self._redraw())
        bar.addWidget(self.tol)
        self.status = QtWidgets.QLabel("Run the diamond filter (or open a project that has) to see its evidence.")
        self.status.setWordWrap(True)
        bar.addWidget(self.status, stretch=1)
        lay.addLayout(bar)
        self.evidence = QtWidgets.QLabel("Select a spot to see why it is / is not flagged.")
        self.evidence.setWordWrap(True)
        lay.addWidget(self.evidence)
        self.glw = pg.GraphicsLayoutWidget()
        lay.addWidget(self.glw, stretch=1)
        self.flagged: Optional[pd.DataFrame] = None
        self.lines: Optional[pd.DataFrame] = None
        self.orig_tol = 0.07
        self.plots: dict = {}
        self._selected = None

    def set_data(self, flagged: Optional[pd.DataFrame], wavelength_A: Optional[float],
                 diamond_a: float = 3.5667, tol_deg: float = 0.07):
        self.flagged = flagged
        self.orig_tol = float(tol_deg)
        self.lines = (viz_products.diamond_line_table(wavelength_A, diamond_a)
                      if flagged is not None and wavelength_A else None)
        self.tol.blockSignals(True); self.tol.setValue(self.orig_tol); self.tol.blockSignals(False)
        self._redraw()

    def select_spot(self, spot_id):
        self._selected = spot_id
        self._write_evidence()
        self._redraw()

    @staticmethod
    def _log_delta(v):
        return np.log10(np.maximum(np.asarray(v, float), 1e-6))     # floor: exact line hits stay finite

    def _preview(self):
        f = self.flagged
        return viz_products.flag_preview(f, self.lines.tth_deg.to_numpy(), self.tol.value())

    def _redraw(self):
        self.glw.clear(); self.plots = {}
        f, L = self.flagged, self.lines
        if f is None or L is None or not len(f):
            return
        tth = f.two_theta_deg.to_numpy()
        orig = f.is_diamond.to_numpy()
        prev = self._preview()
        flips = np.flatnonzero(prev != orig)
        self.status.setText(
            f"{int(orig.sum())}/{len(f)} flagged at {self.orig_tol:g}° · at {self.tol.value():g}°: "
            f"{int(prev.sum())} would be flagged ({len(flips)} spot ID(s) change: "
            f"{', '.join(map(str, f.spot_id.to_numpy()[flips][:12])) or 'none'})")
        # 1: 2θ histogram with line markers and tolerance bands
        h = self.glw.addPlot(row=0, col=0, colspan=2, title="2θ distribution vs allowed diamond lines")
        edges = np.linspace(tth.min() - 0.1, tth.max() + 0.1, 80)
        for mask, color, nm in ((~orig, _KEEP, "kept"), (orig, _FLAG, "flagged")):
            c, e = np.histogram(tth[mask], bins=edges)
            h.addItem(pg.BarGraphItem(x=0.5 * (e[1:] + e[:-1]), height=c, width=(e[1] - e[0]) * 0.9, brush=color))
        for _i, ln in L.iterrows():
            if tth.min() - 0.5 <= ln.tth_deg <= tth.max() + 0.5:
                h.addItem(pg.LinearRegionItem([ln.tth_deg - self.tol.value(), ln.tth_deg + self.tol.value()],
                                              movable=False, brush=pg.mkBrush(255, 92, 92, 40), pen=pg.mkPen(None)))
                h.addItem(pg.InfiniteLine(ln.tth_deg, angle=90, pen=pg.mkPen("#ff9f9f", width=1, style=QtCore.Qt.DashLine),
                                          label=ln.hkl, labelOpts={"position": 0.95, "color": "#ff9f9f"}))
        h.setLabel("bottom", "2θ (°)"); h.setLabel("left", "spots")
        self.plots["hist"] = h
        # 2: distance to nearest line vs 2θ
        d = self.glw.addPlot(row=1, col=0, title="Distance to nearest diamond line")
        for mask, color in ((~orig, _KEEP), (orig, _FLAG)):
            s = pg.ScatterPlotItem(x=tth[mask], y=self._log_delta(f.delta_to_diamond_deg.to_numpy()[mask]), size=7,
                                   brush=pg.mkBrush(color), pen=pg.mkPen(None), data=f.spot_id.to_numpy()[mask])
            s.sigClicked.connect(lambda _s, pts, _e: pts and self.spotClicked.emit(pts[0].data()))
            d.addItem(s)
        d.addItem(pg.InfiniteLine(float(np.log10(self.tol.value())), angle=0, pen=pg.mkPen("#ffd43b", width=1)))
        d.setLabel("bottom", "2θ (°)"); d.setLabel("left", "log10 Δ (°)  (yellow = tolerance)")
        self.plots["delta"] = d
        # 3: 2θ vs omega -- a powder shell is a horizontal band over all ω; a single crystal is a point
        w = self.glw.addPlot(row=1, col=1, title="2θ vs ω (powder shells are flat bands)")
        for pid, grp in f.groupby("panel_id"):
            fl = grp.is_diamond.to_numpy()
            s = pg.ScatterPlotItem(x=grp.omega_deg.to_numpy(), y=grp.two_theta_deg.to_numpy(), size=7,
                                   brush=[pg.mkBrush(_FLAG if x else _KEEP) for x in fl],
                                   pen=pg.mkPen(None), data=grp.spot_id.to_numpy())
            s.sigClicked.connect(lambda _s, pts, _e: pts and self.spotClicked.emit(pts[0].data()))
            w.addItem(s)
        w.setLabel("bottom", "ω (°)"); w.setLabel("left", "2θ (°)")
        self.plots["tth_omega"] = w
        if self._selected is not None:
            row = f[f.spot_id == self._selected]
            if len(row):
                r = row.iloc[0]
                d.addItem(pg.ScatterPlotItem(x=[r.two_theta_deg], y=self._log_delta([r.delta_to_diamond_deg]), size=16, symbol="o",
                                             pen=pg.mkPen("#ff3b8d", width=2), brush=pg.mkBrush(None)))
                w.addItem(pg.ScatterPlotItem(x=[r.omega_deg], y=[r.two_theta_deg], size=16, symbol="o",
                                             pen=pg.mkPen("#ff3b8d", width=2), brush=pg.mkBrush(None)))

    def _write_evidence(self):
        f, L = self.flagged, self.lines
        if f is None or L is None or self._selected is None:
            return
        row = f[f.spot_id == self._selected]
        if not len(row):
            self.evidence.setText("Select a spot to see why it is / is not flagged.")
            return
        r = row.iloc[0]
        j = int(np.argmin(np.abs(L.tth_deg.to_numpy() - r.two_theta_deg)))
        ln = L.iloc[j]
        verdict = "FLAGGED as diamond/gasket candidate" if r.is_diamond else "kept as a sample candidate"
        self.evidence.setText(
            f"Spot {int(r.spot_id)} (panel {int(r.panel_id)}): 2θ {r.two_theta_deg:.4f}° · nearest allowed diamond line "
            f"({ln.hkl}) at {ln.tth_deg:.4f}° → Δ {abs(r.two_theta_deg - ln.tth_deg):.4f}° vs tolerance {self.orig_tol:g}° → {verdict}. "
            "A radial match alone does not prove the spot belongs to diamond: a sample reflection at the same radius "
            "would be flagged too.")
