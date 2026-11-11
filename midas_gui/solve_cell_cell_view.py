"""Solve Cell "Unit cell" tab: the refined lattice made readable.

Left: the refined cell next to its Niggli-reduced form, the closest known lattices
with per-edge / per-angle / volume deviations, and the crystal system the lattice
shows at increasing tolerances. Right: a real-space 3D view of the refined cell
(a, b, c arrows) with the matched reference cell overlaid in the same frame.

All numbers come from :mod:`midas_gui.solve_cell.cell_report` (Qt-free). The report
takes a few seconds, so it is computed on a worker thread.
"""
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np
import pyvista as pv
from PyQt5 import QtCore, QtWidgets
from pyvistaqt import QtInteractor

from midas_gui import style as S
from midas_gui.pyvista_views import close_interactor_at_quit
from midas_gui.solve_cell import cell_report as cr

_AXIS_COLORS = ("#ff5c5c", "#5cd65c", "#5c9dff")
_YOURS, _REF, _REDUCED = "#ffffff", "#ffa94d", "#8a8a8a"
_VERDICT_COLOR = {"matches": "#38d9a9", "close": "#a9e34b", "weak": "#ffa94d", "no match": "#ff6b6b"}


def _fmt_cell(cell: Optional[Sequence[float]]) -> str:
    if not cell:
        return "—"
    return (f"{cell[0]:.4f}  {cell[1]:.4f}  {cell[2]:.4f}   "
            f"{cell[3]:.2f}°  {cell[4]:.2f}°  {cell[5]:.2f}°")


class _ReportWorker(QtCore.QThread):
    done = QtCore.pyqtSignal(int, object)

    def __init__(self, seq: int, result: dict, extra_refs: list, parent=None):
        super().__init__(parent)
        self._seq, self._result, self._extra = seq, result, extra_refs

    def run(self):
        try:
            rep = cr.build_report(self._result, self._extra)
        except Exception as exc:                       # never kill the GUI over an advisory view
            rep = {"error": f"{type(exc).__name__}: {exc}"}
        self.done.emit(self._seq, rep)


class UnitCellView(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.report: Optional[dict] = None
        self._result: Optional[dict] = None
        self._label = ""
        self._seq = 0
        self._worker: Optional[_ReportWorker] = None
        self._pending = False
        self._extra: list = []
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        lay.addWidget(split)

        left = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        self.status = QtWidgets.QLabel("Refine a domain to see its unit cell.")
        self.status.setWordWrap(True)
        lv.addWidget(self.status)

        lv.addWidget(QtWidgets.QLabel("<b>Cell</b> — same lattice, different axes"))
        self.cell_table = self._table(["Setting", "a (Å)", "b (Å)", "c (Å)", "α (°)", "β (°)", "γ (°)", "V (Å³)"])
        lv.addWidget(self.cell_table)

        lv.addWidget(QtWidgets.QLabel(
            "<b>Closest known lattices</b> — deviation = yours − reference, in the best-matching setting"))
        self.match_table = self._table(["Phase", "Verdict", "Score %", "Δa %", "Δb %", "Δc %",
                                        "Δα °", "Δβ °", "Δγ °", "ΔV %"])
        self.match_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.match_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.match_table.itemSelectionChanged.connect(self._draw)
        lv.addWidget(self.match_table)

        lv.addWidget(QtWidgets.QLabel("<b>Symmetry vs tolerance</b> — crystal system the lattice shows when "
                                      "edges/angles may differ by this much"))
        self.ladder_table = self._table(["Edges within", "Angles within", "System", "Cell the tolerance allows"])
        lv.addWidget(self.ladder_table)

        self.notes = QtWidgets.QLabel("")
        self.notes.setWordWrap(True)
        self.notes.setStyleSheet(f"color:{S.MUTED};font-size:11px")
        lv.addWidget(self.notes)
        split.addWidget(left)

        right = QtWidgets.QWidget()
        rv = QtWidgets.QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        row = QtWidgets.QHBoxLayout()
        self.show_ref = QtWidgets.QCheckBox("selected reference")
        self.show_ref.setChecked(True)
        self.show_ref.setToolTip("Overlay the selected known lattice in its best-matching setting, "
                                 "rotated onto your cell's axes (orange).")
        self.show_reduced = QtWidgets.QCheckBox("reduced cell")
        self.show_reduced.setToolTip("Overlay the Niggli-reduced cell (grey).")
        for cb in (self.show_ref, self.show_reduced):
            cb.toggled.connect(self._draw)
            row.addWidget(cb)
        reset = QtWidgets.QPushButton("Reset view")
        reset.clicked.connect(lambda: (self.iv.reset_camera(), self.iv.render()))
        row.addStretch(1)
        row.addWidget(reset)
        rv.addLayout(row)
        self.iv = QtInteractor(right)
        self.iv.set_background("black")
        close_interactor_at_quit(self.iv)
        rv.addWidget(self.iv, stretch=1)
        self.legend = QtWidgets.QLabel("")
        self.legend.setWordWrap(True)
        self.legend.setStyleSheet(f"color:{S.MUTED};font-size:11px")
        rv.addWidget(self.legend)
        split.addWidget(right)
        split.setSizes([700, 500])

    @staticmethod
    def _table(headers: list) -> QtWidgets.QTableWidget:
        t = QtWidgets.QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        t.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        t.horizontalHeader().setStretchLastSection(True)
        t.setMaximumHeight(190)
        return t

    # ── data in ─────────────────────────────────────────────────────
    def set_result(self, label: str, result: Optional[dict], extra_refs: Optional[list] = None):
        """Show the cell of one refined domain (``None`` clears the view)."""
        self._seq += 1
        self._result, self._label, self.report = result, label, None
        self._extra = list(extra_refs or [])
        self._pending = result is not None
        if result is None:
            self._clear_tables()
            self.status.setText("Refine a domain to see its unit cell.")
            self.iv.clear()
            return
        self.status.setText(f"{label}: open this tab to compute the symmetry and phase comparison.")
        if self.isVisible():
            self._start()

    def showEvent(self, event):
        super().showEvent(event)
        self._start()

    def _start(self):
        """Compute the report for the current result, once, and only when someone looks
        (it costs a few seconds, and domains change often while indexing)."""
        if not self._pending or self._result is None:
            return
        self._pending = False
        self.status.setText(f"{self._label}: computing symmetry and phase comparison…")
        w = _ReportWorker(self._seq, self._result, self._extra, self)
        w.done.connect(self._on_report)
        self._worker = w
        w.start()

    def shutdown(self):
        w = self._worker
        if w is not None and w.isRunning():
            w.wait(15000)

    def _on_report(self, seq: int, rep: dict):
        if seq != self._seq:
            return                                    # a newer domain was selected meanwhile
        if rep.get("error"):
            self.status.setText(f"Could not interpret this cell: {rep['error']}")
            return
        self.report = rep
        self._fill(rep)
        self._draw()

    def wait(self, ms: int = 30000) -> bool:
        """Block until the current report is ready (tests / headless callers)."""
        self._start()
        w = self._worker
        ok = True if w is None else w.wait(ms)
        QtWidgets.QApplication.processEvents()
        return ok

    # ── tables ──────────────────────────────────────────────────────
    def _clear_tables(self):
        for t in (self.cell_table, self.match_table, self.ladder_table):
            t.setRowCount(0)
        self.notes.setText("")
        self.legend.setText("")

    @staticmethod
    def _item(text: str, color: Optional[str] = None) -> QtWidgets.QTableWidgetItem:
        it = QtWidgets.QTableWidgetItem(text)
        if color:
            from PyQt5 import QtGui
            it.setForeground(QtGui.QColor(color))
        return it

    def _fill(self, rep: dict):
        res = self._result or {}
        rows = [("refined (yours)", rep["cell"])]
        if rep.get("reduced"):
            rows.append(("Niggli-reduced", rep["reduced"]))
        if res.get("conventional_cell") is not None and len(res["conventional_cell"]) == 6:
            rows.append((f"conventional ({res.get('conventional_system', '?')})",
                         tuple(res["conventional_cell"])))
        self.cell_table.setRowCount(len(rows))
        for r, (name, cell) in enumerate(rows):
            self.cell_table.setItem(r, 0, self._item(name))
            for c, v in enumerate(cell):
                self.cell_table.setItem(r, 1 + c, self._item(f"{v:.4f}" if c < 3 else f"{v:.3f}"))
            self.cell_table.setItem(r, 7, self._item(f"{cr.cell_volume(cell):.2f}"))
        self.cell_table.resizeColumnsToContents()

        matches = rep.get("matches") or []
        self.match_table.blockSignals(True)
        self.match_table.setRowCount(len(matches))
        for r, m in enumerate(matches):
            vals = [m["name"], m["verdict"], f"{m['score']:.2f}",
                    *[f"{x:+.2f}" for x in m["d_len_pct"]], *[f"{x:+.2f}" for x in m["d_ang_deg"]],
                    f"{m['d_vol_pct']:+.2f}"]
            for c, v in enumerate(vals):
                self.match_table.setItem(r, c, self._item(
                    v, _VERDICT_COLOR.get(m["verdict"]) if c == 1 else None))
        self.match_table.resizeColumnsToContents()
        self.match_table.blockSignals(False)
        if matches:
            self.match_table.selectRow(0)

        lad = rep.get("ladder") or []
        self.ladder_table.setRowCount(len(lad))
        for r, row in enumerate(lad):
            for c, v in enumerate((f"{row['tol_len_pct']:g} %", f"{row['tol_ang_deg']:g}°", row["system"],
                                   _fmt_cell(row["cell"]))):
                self.ladder_table.setItem(r, c, self._item(v))
        self.ladder_table.resizeColumnsToContents()

        self.status.setText(
            f"{self._label}: V = {rep['volume']:.2f} Å³ · " + cr.summary_line(rep))
        self.notes.setText("\n".join(rep.get("notes") or []))

    def selected_match(self) -> Optional[dict]:
        rows = self.match_table.selectionModel().selectedRows() if self.report else []
        m = (self.report or {}).get("matches") or []
        return m[rows[0].row()] if rows and rows[0].row() < len(m) else None

    # ── 3D ──────────────────────────────────────────────────────────
    def _add_cell(self, A: np.ndarray, color: str, name: str, width: float, opacity: float = 1.0):
        corners, edges = cr.parallelepiped(A)
        poly = pv.PolyData(corners)
        poly.lines = np.array([[2, a, b] for a, b in edges]).ravel()
        self.iv.add_mesh(poly, color=color, line_width=width, opacity=opacity,
                         reset_camera=False, name=name)

    def _draw(self, *_):
        self.iv.clear()
        rep = self.report
        if not rep:
            return
        A = cr.basis_rows(rep["cell"])
        self._add_cell(A, _YOURS, "uc_yours", 3.0)
        legend = [f"white: refined cell ({_fmt_cell(rep['cell'])})"]
        for i, (v, lab) in enumerate(zip(A, "abc")):
            n = float(np.linalg.norm(v))
            self.iv.add_mesh(pv.Arrow(start=(0, 0, 0), direction=v / n, scale=n * 1.12, tip_length=0.12,
                                      tip_radius=0.04, shaft_radius=0.012),
                             color=_AXIS_COLORS[i], reset_camera=False, name=f"uc_arrow_{lab}")
            self.iv.add_point_labels(np.array([v * 1.2]), [f"{lab} = {n:.3f} Å"], text_color=_AXIS_COLORS[i],
                                     font_size=12, show_points=False, shape=None, name=f"uc_label_{lab}",
                                     always_visible=True)
        m = self.selected_match()
        if m is not None and self.show_ref.isChecked():
            self._add_cell(np.asarray(m["ref_rows"]), _REF, "uc_ref", 3.0, 0.9)
            legend.append(f"orange: {m['name']} in the setting closest to yours "
                          f"({_fmt_cell(m['ref_cell'])}); {m['verdict']}, score {m['score']:.2f}%")
        if self.show_reduced.isChecked() and rep.get("reduced"):
            # the reduced cell's axes are lattice vectors of YOUR lattice: find them in your frame
            mr = cr.match_lattice(rep["reduced"], A, len_tol=0.02)
            if mr is not None:
                self._add_cell(np.asarray(mr["ref_rows_native"]), _REDUCED, "uc_reduced", 2.0, 0.8)
                legend.append(f"grey: Niggli-reduced cell ({_fmt_cell(rep['reduced'])}), drawn from "
                              "lattice vectors of the refined cell")
        self.iv.add_point_labels(np.array([[0.0, 0.0, 0.0]]), ["origin"], text_color="#bbbbbb",
                                 font_size=10, show_points=True, point_size=8, shape=None,
                                 name="uc_origin", always_visible=True)
        self.legend.setText("\n".join(legend))
        self.iv.reset_camera()
        self.iv.render()
