"""Tab — Pump Probe (time-resolved / TR-XRD).

Analyses time-resolved diffraction the way the TRR group does: a folder of raw
detector frames named ``PREFIX-<fshw>fshw<delay>delay<id>.tif`` is pooled by a
prefix glob, the pump-probe **delay** (seconds) is parsed from each filename, every
frame is integrated to I(q) with the MIDAS engine (the same core as Batch Integrate,
driven by a calibration), repeats per delay are averaged, and a reference (mean of
the pre-time-zero / negative delays) is subtracted → ΔI(q, delay).  Four views:

  1. ΔI heatmap        — q (or 2θ / R) vs delay, diverging colour, + reference lineout
  2. ΔI vs q lines     — one curve per delay (rainbow)
  3. Kinetics          — ΔI vs delay for user-chosen q-bands
  4. Mean patterns     — I(q) per delay + reference (stability / signal-level check)

Integration uses ``PumpProbeWorker`` (workers.py), which reuses
``build_integration_context`` + ``integrate_frame`` verbatim, so results match Batch.
"""
from __future__ import annotations

import os
import re
from glob import glob
from pathlib import Path
from typing import Optional

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from midas_gui.constants import (KERNELS, DEFAULT_KERNEL, DEFAULT_TRXRD_DIR,
                                 DEFAULT_TRXRD_PREFIX, DEFAULT_TRXRD_CALIB)
from midas_gui.helpers import (_fspin, _browse, _build_spec, spec_from_geometry_file,
                               _NoScrollComboBox)
from midas_gui.widgets import (LogPanel, CorrectionFlagsWidget, DataLoaderPanel,
                               _convert_radial, _UnitAxis)
from midas_gui.workers import PumpProbeWorker
from midas_gui import style as S


# ── TRR filename parsing ─────────────────────────────────────────────────────────
# A float token is a signed integer/decimal with optional scientific exponent.
_FLOAT = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_TRR_RE = re.compile(rf"(?P<fshw>{_FLOAT})fshw(?P<delay>{_FLOAT})delay(?P<id>\d+)")


def parse_trr_filename(name: str, prefix: str = ""):
    """Parse ``(fshw, delay, id)`` from a TRR filename, or None if it doesn't match.

    The delay is returned with the TRR sign convention already applied (negated, so
    positive delay = after the pump). ``prefix`` is stripped first so digits in the
    prefix cannot be swallowed by the fshw token.
    """
    base = os.path.basename(str(name))
    rem = base[len(prefix):] if (prefix and base.startswith(prefix)) else base
    m = _TRR_RE.search(rem)
    if not m:
        return None
    try:
        return (float(m.group("fshw")), -float(m.group("delay")), int(m.group("id")))
    except Exception:
        return None


# ── colour helpers ───────────────────────────────────────────────────────────────
_FG = "#111111"          # foreground (axes/labels/legend) — readable on white


def _diverging_cmap(name="bwr"):
    for cand in (name, "CET-D1A", "CET-D1", "bwr"):
        try:
            cm = pg.colormap.get(cand)
            if cm is not None:
                return cm
        except Exception:
            pass
    # manual blue → white → red fallback
    return pg.ColorMap([0.0, 0.5, 1.0],
                       [(0, 0, 255, 255), (255, 255, 255, 255), (255, 0, 0, 255)])


def _rainbow_colors(n):
    n = max(1, int(n))
    cm = None
    for cand in ("CET-R4", "turbo"):
        try:
            cm = pg.colormap.get(cand)
            if cm is not None:
                break
        except Exception:
            cm = None
    if cm is not None:
        return [cm.map(i / max(1, n - 1), mode="qcolor") for i in range(n)]
    import colorsys
    return [pg.mkColor(*(int(c * 255) for c in
                         colorsys.hsv_to_rgb(0.8 * i / max(1, n - 1), 1, 1)))
            for i in range(n)]


def _thin_ticks(values, n=8, fmt="{:.3g}"):
    """[(index, label)] for a set of ~n evenly-spaced indices into ``values``."""
    k = len(values)
    if k == 0:
        return []
    step = max(1, k // max(1, n))
    idx = list(range(0, k, step))
    if idx[-1] != k - 1:
        idx.append(k - 1)
    return [(i, fmt.format(float(values[i]))) for i in idx]


class PumpProbeTab(QtWidgets.QWidget):
    _AXES = [("Q (Å⁻¹)", "Q"), ("2θ (°)", "2th"), ("R (px)", "R")]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._result = None                 # calibration from Tab 2
        self._worker = None
        self._frames = []                   # [(path, delay, fshw)]
        self._delays = []                   # unique parsed delays (pre-integration)
        self._data = None                   # last finished() payload
        self._bands = []                    # [(q_lo, q_hi)] kinetics q-bands
        # Shared plot-style state (draw mode + sizes), like the Batch tab.
        self._draw_mode = "line+sym"        # "line" | "line+sym" | "sym"
        self._line_width = 1.5
        self._symbol_size = 6
        self._font_size = 10
        self._plot_meta = {}                # plot -> {title, xlabel, ylabel}
        self._lines_curves = []             # [(curve, color)] for ΔI vs q
        self._kin_curves = []               # [(curve, color)] for kinetics
        self._means_curves = []             # [(curve, color)] for mean patterns
        self._build_ui()
        # Default the data loader to the shipped TRR folder + auto-scan if present.
        self._loader.set_path(DEFAULT_TRXRD_DIR)
        if Path(DEFAULT_TRXRD_DIR).is_dir():
            self._scan()

    # ── cross-tab wiring ───────────────────────────────────────────
    def set_calibration(self, result):
        self._result = result
        try:
            self._calib_lbl.setText(
                f"From Tab 2: Lsd={result.Lsd/1000:.3f} mm  λ={result.wavelength_A:.5f} Å  "
                f"{result.NrPixelsY}×{result.NrPixelsZ} px")
        except Exception:
            self._calib_lbl.setText("From Tab 2: (calibration loaded)")
        self._use_tab2_btn.setChecked(True)

    def set_mask_from_tab1(self, mask):
        self._loader.set_tab1_mask(mask)

    # ── UI ─────────────────────────────────────────────────────────
    def _build_ui(self):
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6); root.setSpacing(0)
        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        split.setChildrenCollapsible(False); split.setHandleWidth(6)
        root.addWidget(split)

        # ── LEFT: data loader (folder + dark/bright/background + mask) ──
        self._loader = DataLoaderPanel(mode="stream")
        self._loader.setMinimumWidth(200)
        # Live folder monitoring is not part of the pump-probe workflow.
        if getattr(self._loader, "_monitor_btn", None) is not None:
            self._loader._monitor_btn.setVisible(False)
        split.addWidget(self._loader)

        # ── MIDDLE: scrollable parameter column ──
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True); scroll.setMinimumWidth(280)
        scroll.setMaximumWidth(440)
        inner = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(inner); lv.setContentsMargins(2, 2, 2, 2); lv.setSpacing(8)
        scroll.setWidget(inner)

        def _br(w=30):
            b = QtWidgets.QPushButton("…"); b.setFixedWidth(w); return b

        # Calibration source
        cal = S.make_card("Calibration source")
        row = QtWidgets.QHBoxLayout(); row.setSpacing(10)
        self._use_tab2_btn = QtWidgets.QRadioButton("From Tab 2")
        self._use_file_btn = QtWidgets.QRadioButton("From file")
        self._use_tab2_btn.setChecked(True)
        row.addWidget(self._use_tab2_btn); row.addWidget(self._use_file_btn); row.addStretch(1)
        cal.body.addLayout(row)
        self._calib_lbl = QtWidgets.QLabel("(run Tab 2 first, or pick a file)")
        self._calib_lbl.setStyleSheet(f"color:{S.MUTED};font-size:10px")
        self._calib_lbl.setWordWrap(True)
        cal.body.addWidget(self._calib_lbl)
        self._calib_ed = QtWidgets.QLineEdit()
        self._calib_ed.setPlaceholderText("calibration.json / paramstest.txt / .poni…")
        cr = QtWidgets.QHBoxLayout(); cr.setSpacing(4); cr.addWidget(self._calib_ed, 1)
        bc = _br(); bc.clicked.connect(lambda: self._calib_ed.setText(
            _browse(self, "Open calibration file",
                    "Calibration (*.json *.txt *.poni);;All (*)") or ""))
        cr.addWidget(bc)
        self._calib_ed.textChanged.connect(
            lambda t: self._use_file_btn.setChecked(True) if t.strip() else None)
        cal.body.addLayout(cr)
        lv.addWidget(cal)
        # Default to the shipped MIDAS calibration for the TRR data (ready out of the
        # box); setting the text auto-selects "From file". A live Tab 2 result overrides.
        if Path(DEFAULT_TRXRD_CALIB).exists():
            self._calib_ed.setText(DEFAULT_TRXRD_CALIB)

        # Data pooling (TRR) — the folder comes from the data loader on the left;
        # here you set the prefix that pools + parses the frames.
        data = S.make_card("Data pooling (TRR)")
        self._prefix_ed = QtWidgets.QLineEdit(DEFAULT_TRXRD_PREFIX)
        data.body.addLayout(S.Form().row(("Prefix:", self._prefix_ed)))
        note = QtWidgets.QLabel(
            "Pools files in the loaded folder matching  <prefix>*.tif  and parses the "
            "pump-probe delay from …fshw…delay… in each name.")
        note.setWordWrap(True); note.setStyleSheet(f"color:{S.MUTED};font-size:10px")
        data.body.addWidget(note)
        self._scan_btn = QtWidgets.QPushButton("Scan folder")
        self._scan_btn.clicked.connect(self._scan)
        data.body.addWidget(self._scan_btn)
        self._scan_lbl = QtWidgets.QLabel("(not scanned)")
        self._scan_lbl.setStyleSheet(f"color:{S.MUTED};font-size:10px")
        data.body.addWidget(self._scan_lbl)
        lv.addWidget(data)

        # Integration
        integ = S.make_card("Integration")
        self._kernel = _NoScrollComboBox()
        for label, key in KERNELS.items():
            self._kernel.addItem(label, key)
        ki = self._kernel.findData(DEFAULT_KERNEL)
        if ki >= 0:
            self._kernel.setCurrentIndex(ki)
        self._r_bin = _fspin(0.1, 20.0, 2, 1.0, "px")
        self._e_bin = _fspin(0.5, 30.0, 1, 5.0, "°")
        self._axis = _NoScrollComboBox()
        for label, key in self._AXES:
            self._axis.addItem(label, key)
        self._axis.currentIndexChanged.connect(self._replot)
        f = S.Form()
        f.row(("Kernel:", self._kernel))
        f.row(("R bin:", self._r_bin), ("η bin:", self._e_bin))
        f.row(("Plot axis:", self._axis))
        integ.body.addLayout(f)
        self._q_check = QtWidgets.QCheckBox("Q-uniform bins")
        self._q_min = _fspin(0.0, 100.0, 3, 0.5, "Å⁻¹")
        self._q_max = _fspin(0.0, 100.0, 3, 8.0, "Å⁻¹")
        self._q_bin = _fspin(0.0001, 1.0, 4, 0.01, "Å⁻¹")
        for w in (self._q_min, self._q_max, self._q_bin):
            w.setEnabled(False)
        self._q_check.toggled.connect(
            lambda c: [w.setEnabled(c) for w in (self._q_min, self._q_max, self._q_bin)])
        integ.body.addWidget(self._q_check)
        qf = S.Form(); qf.row(("Qmin:", self._q_min), ("Qmax:", self._q_max)); qf.row(("ΔQ:", self._q_bin))
        integ.body.addLayout(qf)
        lv.addWidget(integ)

        # Corrections
        self._corr_widget = CorrectionFlagsWidget()
        lv.addWidget(self._corr_widget)

        # Pump-probe options
        pp = S.make_card("Pump-probe options")
        pp.body.addWidget(QtWidgets.QLabel("Reference delays (subtracted):"))
        self._ref_list = QtWidgets.QListWidget()
        self._ref_list.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)
        self._ref_list.setMaximumHeight(90)
        pp.body.addWidget(self._ref_list)
        ref_note = QtWidgets.QLabel("Default: all negative delays. Select to override.")
        ref_note.setStyleSheet(f"color:{S.MUTED};font-size:10px"); ref_note.setWordWrap(True)
        pp.body.addWidget(ref_note)
        self._norm_check = QtWidgets.QCheckBox("Per-pattern normalise over q-window")
        pp.body.addWidget(self._norm_check)
        self._norm_lo = _fspin(0.0, 100.0, 3, 4.1, "Å⁻¹")
        self._norm_hi = _fspin(0.0, 100.0, 3, 4.2, "Å⁻¹")
        for w in (self._norm_lo, self._norm_hi):
            w.setEnabled(False)
        self._norm_check.toggled.connect(
            lambda c: [w.setEnabled(c) for w in (self._norm_lo, self._norm_hi)])
        nf = S.Form(); nf.row(("q lo:", self._norm_lo), ("q hi:", self._norm_hi))
        pp.body.addLayout(nf)
        self._auto_v = QtWidgets.QCheckBox("Auto colour range (ΔI)"); self._auto_v.setChecked(True)
        pp.body.addWidget(self._auto_v)
        self._vrange = _fspin(0.0, 1e9, 4, 0.12)
        self._vrange.setEnabled(False)
        self._auto_v.toggled.connect(lambda c: self._vrange.setEnabled(not c))
        self._auto_v.toggled.connect(self._replot)
        self._vrange.valueChanged.connect(self._replot)
        vf = S.Form(); vf.row(("±ΔI range:", self._vrange))
        pp.body.addLayout(vf)
        self._cmap = _NoScrollComboBox()
        self._cmap.addItems(["bwr", "CET-D1", "CET-D9", "seismic"])
        self._cmap.currentIndexChanged.connect(self._replot)
        cf = S.Form(); cf.row(("ΔI colormap:", self._cmap))
        pp.body.addLayout(cf)
        lv.addWidget(pp)

        # Run
        self._run_btn = S.primary_btn("Integrate + build ΔI")
        self._run_btn.clicked.connect(self._run)
        self._abort_btn = QtWidgets.QPushButton("Abort"); self._abort_btn.setEnabled(False)
        self._abort_btn.clicked.connect(self._abort)
        rr = QtWidgets.QHBoxLayout(); rr.setSpacing(6)
        rr.addWidget(self._run_btn, 1); rr.addWidget(self._abort_btn)
        lv.addLayout(rr)
        self._prog = QtWidgets.QProgressBar(); self._prog.setRange(0, 100); self._prog.setVisible(False)
        lv.addWidget(self._prog)
        lv.addStretch(1)
        split.addWidget(scroll)

        # RIGHT: style toolbar + plot tabs + log
        right = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        plot_box = QtWidgets.QWidget()
        pv = QtWidgets.QVBoxLayout(plot_box)
        pv.setContentsMargins(0, 0, 0, 0); pv.setSpacing(2)
        pv.addLayout(self._build_style_toolbar())
        self._views = QtWidgets.QTabWidget()
        self._build_heatmap_view()
        self._build_lines_view()
        self._build_kinetics_view()
        self._build_means_view()
        pv.addWidget(self._views, 1)
        right.addWidget(plot_box)
        self._log = LogPanel()
        right.addWidget(self._log)
        right.setStretchFactor(0, 4); right.setStretchFactor(1, 1)
        right.setMinimumWidth(360)
        split.addWidget(right)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 0); split.setStretchFactor(2, 1)
        split.setSizes([260, 380, 960])

    # ── shared plot-style toolbar + chrome helpers ─────────────────
    def _build_style_toolbar(self):
        bar = QtWidgets.QHBoxLayout(); bar.setSpacing(6)
        bar.addWidget(QtWidgets.QLabel("draw:"))
        self._draw_combo = _NoScrollComboBox()
        self._draw_combo.addItem("Lines", "line")
        self._draw_combo.addItem("Lines + points", "line+sym")
        self._draw_combo.addItem("Points", "sym")
        self._draw_combo.setCurrentIndex(1)   # line+sym
        self._draw_combo.setToolTip("Draw the ΔI-vs-q / kinetics / mean-pattern curves "
                                    "as lines, lines+points, or points only.")
        self._draw_combo.currentIndexChanged.connect(self._on_draw_mode)
        bar.addWidget(self._draw_combo)

        def _tbtn(txt, tip, slot):
            b = QtWidgets.QToolButton(); b.setText(txt); b.setToolTip(tip)
            b.setAutoRaise(True); b.clicked.connect(slot); return b

        def _sep():
            s = QtWidgets.QLabel("|"); s.setStyleSheet("color:#888;"); return s

        def _group(label, tip_minus, on_minus, tip_plus, on_plus):
            bar.addWidget(_tbtn("−", tip_minus, on_minus))
            bar.addWidget(QtWidgets.QLabel(label))
            bar.addWidget(_tbtn("+", tip_plus, on_plus))

        bar.addWidget(_sep())
        _group("line", "Thinner lines", lambda: self._adjust_linewidth(-0.5),
               "Thicker lines", lambda: self._adjust_linewidth(0.5))
        bar.addWidget(_sep())
        _group("sym", "Smaller points", lambda: self._adjust_symbolsize(-1),
               "Larger points", lambda: self._adjust_symbolsize(1))
        bar.addWidget(_sep())
        _group("font", "Smaller text", lambda: self._adjust_fontsize(-1),
               "Larger text", lambda: self._adjust_fontsize(1))
        bar.addStretch(1)
        return bar

    def _chrome(self, plot, *, title=None, xlabel=None, ylabel=None):
        """Apply readable (black-on-white) axes/labels/title + the current font size.
        Remembers title/labels so a later font change can re-apply them."""
        plot.setBackground("w")
        meta = self._plot_meta.setdefault(plot, {})
        for k, val in (("title", title), ("xlabel", xlabel), ("ylabel", ylabel)):
            if val is not None:
                meta[k] = val
        pen = pg.mkPen(_FG, width=1)
        tf = QtGui.QFont(); tf.setPointSize(self._font_size)
        for ax_name in ("bottom", "left"):
            ax = plot.getAxis(ax_name)
            ax.setPen(pen); ax.setTextPen(pen); ax.setStyle(tickFont=tf)
        lbl = {"color": _FG, "font-size": f"{self._font_size + 1}pt"}
        if meta.get("xlabel"):
            plot.setLabel("bottom", meta["xlabel"], **lbl)
        if meta.get("ylabel"):
            plot.setLabel("left", meta["ylabel"], **lbl)
        if meta.get("title") is not None:
            plot.setTitle(meta["title"], color=_FG, size=f"{self._font_size + 2}pt")

    def _add_legend(self, plot):
        lg = plot.addLegend(offset=(-8, 8), labelTextColor=_FG)
        try:
            lg.setBrush(pg.mkBrush(255, 255, 255, 200))
            lg.setPen(pg.mkPen("#cccccc"))
            lg.setLabelTextSize(f"{self._font_size}pt")
        except Exception:
            pass
        self._legends.append(lg)
        return lg

    # ── plot view builders ─────────────────────────────────────────
    def _build_heatmap_view(self):
        self._legends = getattr(self, "_legends", [])
        w = QtWidgets.QWidget(); v = QtWidgets.QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0)
        hsplit = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self._hm_yaxis = _UnitAxis(orientation="left")
        self._hm_plot = pg.PlotWidget(background="w", axisItems={"left": self._hm_yaxis})
        self._hm_img = pg.ImageItem()
        self._hm_plot.addItem(self._hm_img)
        self._hm_bar = None
        self._chrome(self._hm_plot, title="ΔI(q, delay)", xlabel="delay (s)", ylabel="Q (Å⁻¹)")
        hsplit.addWidget(self._hm_plot)
        # Reference pattern I(q) beside the map, sharing the (radial) y-axis.
        self._ref_plot = pg.PlotWidget(background="w")
        self._ref_plot.setYLink(self._hm_plot)
        self._ref_plot.showAxis("left", False)        # radial axis shown on the heatmap
        self._ref_plot.setMaximumWidth(200)
        self._ref_plot.getViewBox().setMouseEnabled(y=False)
        self._chrome(self._ref_plot, title="Reference I(q)", xlabel="I (reference)")
        self._ref_curve = self._ref_plot.plot([], [], pen=pg.mkPen("#2ca02c", width=2))
        hsplit.addWidget(self._ref_plot)
        hsplit.setStretchFactor(0, 5); hsplit.setStretchFactor(1, 1)
        v.addWidget(hsplit)
        self._views.addTab(w, "ΔI heatmap")

    def _build_lines_view(self):
        self._lines_plot = pg.PlotWidget(background="w")
        self._chrome(self._lines_plot, title="ΔI vs q (one curve per delay)",
                     xlabel="Q (Å⁻¹)", ylabel="ΔI")
        self._lines_legend = self._add_legend(self._lines_plot)
        self._views.addTab(self._lines_plot, "ΔI vs q")

    def _build_kinetics_view(self):
        w = QtWidgets.QWidget(); v = QtWidgets.QVBoxLayout(w)
        ctl = QtWidgets.QHBoxLayout(); ctl.setSpacing(6)
        ctl.addWidget(QtWidgets.QLabel("q-band:"))
        self._kin_lo = _fspin(0.0, 100.0, 3, 2.0)
        self._kin_hi = _fspin(0.0, 100.0, 3, 2.2)
        ctl.addWidget(self._kin_lo); ctl.addWidget(QtWidgets.QLabel("–")); ctl.addWidget(self._kin_hi)
        add = QtWidgets.QPushButton("Add band"); add.clicked.connect(self._add_band)
        clr = QtWidgets.QPushButton("Clear bands"); clr.clicked.connect(self._clear_bands)
        ctl.addWidget(add); ctl.addWidget(clr)
        ctl.addWidget(QtWidgets.QLabel("x:"))
        self._kin_xmode = _NoScrollComboBox()
        self._kin_xmode.addItem("Delay (linear)", "linear")
        self._kin_xmode.addItem("Delay (rank)", "rank")
        self._kin_xmode.currentIndexChanged.connect(self._replot_kinetics)
        ctl.addWidget(self._kin_xmode); ctl.addStretch(1)
        v.addLayout(ctl)
        self._kin_plot = pg.PlotWidget(background="w")
        self._chrome(self._kin_plot, title="Kinetics — ΔI vs delay",
                     xlabel="delay (s)", ylabel="ΔI (band mean)")
        self._kin_legend = self._add_legend(self._kin_plot)
        v.addWidget(self._kin_plot)
        self._views.addTab(w, "Kinetics")

    def _build_means_view(self):
        self._means_plot = pg.PlotWidget(background="w")
        self._chrome(self._means_plot, title="Mean pattern per delay + reference",
                     xlabel="Q (Å⁻¹)", ylabel="I")
        self._means_legend = self._add_legend(self._means_plot)
        self._means_ref_curve = None
        self._views.addTab(self._means_plot, "Mean patterns")

    # ── data folder scan ───────────────────────────────────────────
    def _folder(self) -> str:
        """The folder to pool from — the data loader's current source path."""
        return (self._loader.source_cfg().get("path") or "").strip()

    def _scan(self):
        folder = self._folder()
        prefix = self._prefix_ed.text().strip()
        if not folder or not Path(folder).is_dir():
            self._scan_lbl.setText("Load a folder in the left panel first."); return
        paths = sorted(glob(os.path.join(folder, prefix + "*")))
        frames = []
        for p in paths:
            if not p.lower().endswith((".tif", ".tiff")):
                continue
            parsed = parse_trr_filename(p, prefix)
            if parsed is None:
                continue
            fshw, delay, _id = parsed
            frames.append((p, delay, fshw))
        self._frames = frames
        self._delays = sorted({d for _, d, _ in frames})
        self._ref_list.clear()
        for d in self._delays:
            it = QtWidgets.QListWidgetItem(f"{d:.4g}")
            it.setData(QtCore.Qt.UserRole, d)
            self._ref_list.addItem(it)
            if d < 0:
                it.setSelected(True)
        self._scan_lbl.setText(
            f"{len(frames)} frames, {len(self._delays)} unique delays"
            + (f"  ({self._delays[0]:.2g} … {self._delays[-1]:.2g} s)" if self._delays else ""))
        self._log.append(f"[pump] scanned {folder}: {len(frames)} frames, "
                         f"{len(self._delays)} delays")

    # ── run integration ────────────────────────────────────────────
    def _build_spec(self):
        r_bin = self._r_bin.value(); e_bin = self._e_bin.value()
        if self._use_tab2_btn.isChecked():
            if self._result is None:
                raise RuntimeError("No calibration from Tab 2. Run Tab 2 or pick a file.")
            return _build_spec(self._result, r_bin, e_bin)
        path = self._calib_ed.text().strip()
        if not path or not Path(path).exists():
            raise FileNotFoundError(f"Calibration file not found: {path}")
        return spec_from_geometry_file(path, r_bin, e_bin)

    def _run(self):
        if self._worker and self._worker.isRunning():
            return
        if not self._frames:
            self._scan()
        if not self._frames:
            QtWidgets.QMessageBox.warning(self, "No data",
                                          "No frames found — check the folder and prefix.")
            return
        try:
            spec = self._build_spec()
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Calibration error", str(e)); return

        # Dark / bright / background must be computed before use (loader guard).
        for sel in self._loader.has_pending_fields():
            QtWidgets.QMessageBox.warning(
                self, "Field not computed",
                f"'{sel.title()}' is enabled but not computed. "
                "Click 'Compute field' in that box first."); return

        # Optional index-range subset of the pooled frames (start, end, stride).
        start, end, stride = self._loader.frame_range()
        frames = self._frames[start:end:max(1, stride)]
        if not frames:
            QtWidgets.QMessageBox.warning(self, "No data",
                                          "The frame range excludes every pooled frame."); return

        kernel = self._kernel.currentData()
        corrections = self._corr_widget.build_corrections()
        q_cfg = ({"QMin": self._q_min.value(), "QMax": self._q_max.value(),
                  "QBinSize": self._q_bin.value()} if self._q_check.isChecked() else None)
        ref_delays = [self._ref_list.item(i).data(QtCore.Qt.UserRole)
                      for i in range(self._ref_list.count())
                      if self._ref_list.item(i).isSelected()] or None
        norm_range = ((self._norm_lo.value(), self._norm_hi.value())
                      if self._norm_check.isChecked() else None)

        self._run_btn.setEnabled(False); self._abort_btn.setEnabled(True)
        self._prog.setVisible(True); self._prog.setValue(0)
        self._log.append("─" * 40 + f"\n[pump] integrating {len(frames)} frames…")
        self._worker = PumpProbeWorker(
            spec, frames, self._loader.composite_mask(), kernel, corrections,
            weighted=True, q_cfg=q_cfg, ref_delays=ref_delays, norm_range=norm_range,
            dark=self._loader.dark(), bright=self._loader.bright(),
            background=self._loader.background(), bright_mode=self._loader.bright_mode(),
            parent=self)
        self._worker.progress.connect(self._on_progress)
        self._worker.log_line.connect(self._log.append)
        self._worker.finished.connect(self._on_done)
        self._worker.failed.connect(self._on_fail)
        self._worker.start()

    def _abort(self):
        w = self._worker
        if w and w.isRunning():
            w.requestInterruption(); self._abort_btn.setEnabled(False)
            self._log.append("[pump] abort requested…")

    def _on_progress(self, done, total):
        self._prog.setValue(int(100 * done / total) if total else 0)

    def _on_done(self, data):
        self._run_btn.setEnabled(True); self._abort_btn.setEnabled(False)
        self._prog.setVisible(False)
        self._data = data
        self._log.append(
            f"[pump] done — {data['n']} frames, {len(data['delays'])} delays; "
            f"reference = {len(data['ref_delays'])} delay(s)")
        self._replot()

    def _on_fail(self, msg):
        self._run_btn.setEnabled(True); self._abort_btn.setEnabled(False)
        self._prog.setVisible(False)
        self._log.append(f"\n[pump] ERROR:\n{msg[:600]}")
        QtWidgets.QMessageBox.critical(self, "Pump Probe failed", msg[:400])

    # ── plotting ───────────────────────────────────────────────────
    def _radial_axis(self):
        """Return (values, label) for the currently-selected plot axis."""
        d = self._data
        target = self._axis.currentData()
        if target == "Q":
            return d["q_axis"], "Q (Å⁻¹)"
        if target == "2th":
            return d["tth_axis"], "2θ (°)"
        return d["r_axis_px"], "R (px)"

    def _vrange_value(self):
        if self._auto_v.isChecked() and self._data is not None:
            v = float(np.nanpercentile(np.abs(self._data["dI"]), 99.0))
            return v if v > 0 else 1.0
        return max(1e-9, self._vrange.value())

    @staticmethod
    def _bound(plot, xmin, xmax, ymin, ymax, *, bound_y=True):
        """Constrain pan/zoom to the data extent (+ small margin) and set the initial
        view there, so zooming out never leaves the plot area behind."""
        vals = [xmin, xmax, ymin, ymax]
        if any(v is None or not np.isfinite(v) for v in vals):
            return
        if xmax <= xmin:
            xmax = xmin + 1.0
        if ymax <= ymin:
            ymax = ymin + 1.0
        padx = 0.03 * (xmax - xmin); pady = 0.05 * (ymax - ymin)
        xlo, xhi = xmin - padx, xmax + padx
        ylo, yhi = ymin - pady, ymax + pady
        vb = plot.getViewBox()
        if bound_y:
            vb.setLimits(xMin=xlo, xMax=xhi, yMin=ylo, yMax=yhi,
                         maxXRange=xhi - xlo, maxYRange=yhi - ylo)
            plot.setRange(xRange=(xlo, xhi), yRange=(ylo, yhi), padding=0)
        else:
            vb.setLimits(xMin=xlo, xMax=xhi, maxXRange=xhi - xlo)
            plot.setXRange(xlo, xhi, padding=0)

    def _replot(self):
        if self._data is None:
            return
        self._replot_heatmap()
        self._replot_lines()
        self._replot_kinetics()
        self._replot_means()

    def _replot_heatmap(self):
        d = self._data
        rad, rad_label = self._radial_axis()
        delays = d["delays"]; dI = d["dI"]           # (n_delay, n_r)
        n_d, n_r = len(delays), len(rad)
        v = self._vrange_value()
        cm = _diverging_cmap(self._cmap.currentText())
        # image: x = delay index, y = radial index
        self._hm_img.setImage(dI, autoLevels=False, levels=(-v, v))
        try:
            self._hm_img.setColorMap(cm)
        except Exception:
            self._hm_img.setLookupTable(cm.getLookupTable(0.0, 1.0, 256))
        self._hm_img.setRect(QtCore.QRectF(0, 0, n_d, n_r))
        # colour bar (create lazily), with readable black chrome + a ΔI label
        if self._hm_bar is None:
            try:
                self._hm_bar = pg.ColorBarItem(values=(-v, v), colorMap=cm, label="ΔI")
                self._hm_bar.setImageItem(self._hm_img, insert_in=self._hm_plot.getPlotItem())
            except Exception:
                self._hm_bar = None
        else:
            try:
                self._hm_bar.setColorMap(cm); self._hm_bar.setLevels((-v, v))
            except Exception:
                pass
        self._style_colorbar()
        # axis titles + ticks (thinned; delays/radials are unevenly spaced → index axis)
        self._chrome(self._hm_plot, ylabel=rad_label)
        self._hm_plot.getAxis("bottom").setTicks([_thin_ticks(delays, 8)])
        self._hm_yaxis.set_convert(None)
        self._hm_yaxis.setTicks([_thin_ticks(rad, 8)])
        self._bound(self._hm_plot, 0, n_d, 0, n_r)
        # reference pattern I(q) beside the map (shared radial y-axis)
        ref = d["reference"]
        yv = np.arange(n_r) + 0.5
        self._ref_curve.setData(ref, yv)
        rmin, rmax = float(np.nanmin(ref)), float(np.nanmax(ref))
        self._bound(self._ref_plot, rmin, rmax, 0, n_r, bound_y=False)

    def _clear_curves(self, plot, store, legend):
        for c, _ in store:
            plot.removeItem(c)
        store.clear()
        if legend is not None:
            legend.clear()

    def _replot_lines(self):
        d = self._data
        self._clear_curves(self._lines_plot, self._lines_curves, self._lines_legend)
        rad, rad_label = self._radial_axis()
        delays = d["delays"]; dI = d["dI"]
        colors = _rainbow_colors(len(delays))
        for i, dl in enumerate(delays):
            curve = self._lines_plot.plot(rad, dI[i], name=f"{dl:.3g} s")
            self._style_line_curve(curve, colors[i])
            self._lines_curves.append((curve, colors[i]))
        self._chrome(self._lines_plot, xlabel=rad_label)
        self._bound(self._lines_plot, float(np.min(rad)), float(np.max(rad)),
                    float(np.nanmin(dI)), float(np.nanmax(dI)))

    def _replot_kinetics(self):
        if self._data is None:
            return
        d = self._data
        self._clear_curves(self._kin_plot, self._kin_curves, self._kin_legend)
        delays = np.asarray(d["delays"], dtype=float)
        q = d["q_axis"]; dI = d["dI"]
        mode = self._kin_xmode.currentData()
        if mode == "rank":
            x = np.arange(len(delays), dtype=float)
            self._kin_plot.getAxis("bottom").setTicks([_thin_ticks(delays, 8)])
            self._chrome(self._kin_plot, xlabel="delay (rank)")
        else:
            x = delays
            self._kin_plot.getAxis("bottom").setTicks(None)
            self._chrome(self._kin_plot, xlabel="delay (s)")
        colors = _rainbow_colors(max(1, len(self._bands)))
        ys = []
        for j, (lo, hi) in enumerate(self._bands):
            sel = (q >= lo) & (q <= hi)
            if not np.any(sel):
                continue
            trace = dI[:, sel].mean(axis=1); ys.append(trace)
            curve = self._kin_plot.plot(x, trace, name=f"q {lo:.3g}–{hi:.3g}")
            self._style_line_curve(curve, colors[j])
            self._kin_curves.append((curve, colors[j]))
        if len(x) and ys:
            allv = np.concatenate(ys)
            self._bound(self._kin_plot, float(np.min(x)), float(np.max(x)),
                        float(np.nanmin(allv)), float(np.nanmax(allv)))

    def _replot_means(self):
        d = self._data
        self._clear_curves(self._means_plot, self._means_curves, self._means_legend)
        if self._means_ref_curve is not None:
            self._means_plot.removeItem(self._means_ref_curve)
            self._means_ref_curve = None
        rad, rad_label = self._radial_axis()
        delays = d["delays"]; I_by = d["I_by_delay"]
        colors = _rainbow_colors(len(delays))
        for i, dl in enumerate(delays):
            curve = self._means_plot.plot(rad, I_by[i], name=f"{dl:.3g} s")
            self._style_line_curve(curve, colors[i])
            self._means_curves.append((curve, colors[i]))
        self._means_ref_curve = self._means_plot.plot(
            rad, d["reference"], name="reference",
            pen=pg.mkPen("#000000", width=max(1.5, self._line_width),
                         style=QtCore.Qt.DashLine))
        self._chrome(self._means_plot, xlabel=rad_label)
        lo = min(float(np.nanmin(I_by)), float(np.nanmin(d["reference"])))
        hi = max(float(np.nanmax(I_by)), float(np.nanmax(d["reference"])))
        self._bound(self._means_plot, float(np.min(rad)), float(np.max(rad)), lo, hi)

    # ── style controls (draw mode / line / symbol / font) ──────────
    def _style_line_curve(self, curve, color):
        m = self._draw_mode
        curve.setPen(pg.mkPen(color, width=self._line_width)
                     if m in ("line", "line+sym") else None)
        if m in ("sym", "line+sym"):
            curve.setSymbol("o"); curve.setSymbolSize(self._symbol_size)
            curve.setSymbolBrush(pg.mkBrush(color)); curve.setSymbolPen(pg.mkPen(color))
        else:
            curve.setSymbol(None)

    def _apply_line_style(self):
        for store in (self._lines_curves, self._kin_curves, self._means_curves):
            for curve, color in store:
                self._style_line_curve(curve, color)
        if self._means_ref_curve is not None:
            self._means_ref_curve.setPen(pg.mkPen(
                "#000000", width=max(1.5, self._line_width), style=QtCore.Qt.DashLine))

    def _style_colorbar(self):
        if self._hm_bar is None:
            return
        try:
            ax = getattr(self._hm_bar, "axis", None)
            if ax is not None:
                pen = pg.mkPen(_FG)
                ax.setPen(pen); ax.setTextPen(pen)
                tf = QtGui.QFont(); tf.setPointSize(self._font_size); ax.setStyle(tickFont=tf)
                ax.setLabel("ΔI", color=_FG)
        except Exception:
            pass

    def _apply_fonts(self):
        for plot in list(self._plot_meta):
            self._chrome(plot)
        for lg in self._legends:
            try:
                lg.setLabelTextSize(f"{self._font_size}pt")
            except Exception:
                pass
        self._style_colorbar()

    def _on_draw_mode(self, _=0):
        self._draw_mode = self._draw_combo.currentData()
        self._apply_line_style()

    def _adjust_linewidth(self, delta):
        self._line_width = min(8.0, max(0.5, self._line_width + delta))
        self._apply_line_style()

    def _adjust_symbolsize(self, delta):
        self._symbol_size = min(20, max(1, self._symbol_size + delta))
        if self._draw_mode == "line":            # make the control always have effect
            self._draw_combo.setCurrentIndex(1)  # → line+sym (fires _on_draw_mode)
        else:
            self._apply_line_style()

    def _adjust_fontsize(self, delta):
        self._font_size = min(24, max(6, self._font_size + delta))
        self._apply_fonts()

    # ── kinetics bands ─────────────────────────────────────────────
    def _add_band(self):
        lo, hi = self._kin_lo.value(), self._kin_hi.value()
        if hi > lo:
            self._bands.append((lo, hi)); self._replot_kinetics()

    def _clear_bands(self):
        self._bands = []; self._replot_kinetics()
