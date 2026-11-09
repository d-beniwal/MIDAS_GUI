"""Solve Cell "Material filter" panel (stage 2 inputs).

A checkbox turns contaminant filtering on. Off: the filter is skipped and every
ingested spot is a candidate. On: a list of materials (diamond by default; any
number can be added from presets or defined from scratch), each with its own
lattice and filter parameters -- see ``solve_cell.material_filter.Material`` for
what each parameter does. The panel only edits state; running the filter and
showing its result stay with the tab.
"""
from __future__ import annotations

from PyQt5 import QtCore, QtWidgets

from midas_gui.helpers import _fspin, _NoScrollSpinBox
from midas_gui.solve_cell import material_filter as mf

_EXTINCTION_TEXT = {
    "space_group": "space-group conditions",
    "none": "none (allow every hkl)",
    "diamond_basis": "space group + diamond basis",
}
_MODE_TEXT = {"2theta": "2θ only (powder-like)", "orientation": "single-crystal orientation"}


def _combo(options: dict) -> QtWidgets.QComboBox:
    c = QtWidgets.QComboBox()
    for key, text in options.items():
        c.addItem(text, key)
    return c


class MaterialFilterPanel(QtWidgets.QWidget):
    """Edits ``enabled`` + the list of :class:`Material` + the Monte-Carlo controls."""

    changed = QtCore.pyqtSignal()
    enabledChanged = QtCore.pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._materials: list = mf.default_materials()
        self._loading = False

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(4)
        self.enable_cb = QtWidgets.QCheckBox("Filter contaminant materials (anvils, gasket, markers)")
        self.enable_cb.setChecked(True)
        self.enable_cb.setToolTip(
            "On: spots that belong to the materials listed below are flagged and removed from "
            "the candidate pool passed to indexing.\nOff: the whole material filter is skipped "
            "and every ingested spot goes on to indexing.")
        lay.addWidget(self.enable_cb)

        self.body = QtWidgets.QWidget()
        bl = QtWidgets.QVBoxLayout(self.body)
        bl.setContentsMargins(0, 0, 0, 0); bl.setSpacing(4)
        lay.addWidget(self.body)

        # ── materials list + add/remove ──
        self.list = QtWidgets.QListWidget()
        self.list.setMaximumHeight(84)
        self.list.setToolTip("Tick a material to include it in the filter; select one to edit it below.")
        bl.addWidget(self.list)
        row = QtWidgets.QHBoxLayout(); row.setSpacing(6)
        self.add_btn = QtWidgets.QPushButton("Add ▾")
        menu = QtWidgets.QMenu(self.add_btn)
        for name in mf.PRESETS:
            menu.addAction(name, lambda n=name: self._add_preset(n))
        menu.addSeparator()
        menu.addAction("Custom material…", self._add_custom)
        self.add_btn.setMenu(menu)
        self.add_btn.setToolTip("Add a material from a preset lattice, or define one from scratch.")
        self.remove_btn = QtWidgets.QPushButton("Remove")
        row.addWidget(self.add_btn); row.addWidget(self.remove_btn); row.addStretch(1)
        bl.addLayout(row)

        # ── per-material editor ──
        self.editor = QtWidgets.QGroupBox("Selected material")
        ef = QtWidgets.QFormLayout(self.editor); ef.setSpacing(4)
        self.name_ed = QtWidgets.QLineEdit()
        ef.addRow("name:", self.name_ed)
        self.cell = []
        for rowlabels, ranges in ((("a", "b", "c"), (0.5, 200.0)), (("α", "β", "γ"), (20.0, 180.0))):
            h = QtWidgets.QHBoxLayout(); h.setSpacing(4)
            for lab in rowlabels:
                sp = _fspin(ranges[0], ranges[1], 4, 4.0 if lab in "abc" else 90.0)
                sp.setMinimumWidth(78); sp.setMaximumWidth(100)
                self.cell.append(sp)
                h.addWidget(QtWidgets.QLabel(lab)); h.addWidget(sp)
            h.addStretch(1)
            ef.addRow("lattice (Å):" if lab == "c" else "angles (°):", h)
        self.sg = _NoScrollSpinBox(); self.sg.setRange(1, 230); self.sg.setMaximumWidth(70)
        self.sg.setToolTip("International Tables space-group number (1-230). Selects which reflections "
                           "are systematically absent, so only reflections the material can show are "
                           "used as filter lines.")
        self.ext = _combo(_EXTINCTION_TEXT)
        self.ext.setToolTip("Which reflections the material may show. 'Space group + diamond basis' also "
                            "removes the diamond-structure absences (e.g. 222) that Fd-3m alone allows.")
        sgrow = QtWidgets.QHBoxLayout(); sgrow.setSpacing(6)
        sgrow.addWidget(self.sg); sgrow.addWidget(self.ext, 1)
        ef.addRow("space group:", sgrow)
        self.mode = _combo(_MODE_TEXT)
        self.mode.setToolTip(
            "2θ only: flag any spot whose 2θ sits on an allowed line (right for gasket/powder rings; "
            "a sample reflection at the same radius is flagged too).\nSingle-crystal orientation: also "
            "require the spots to belong to one orientation of this material's lattice -- coincidences "
            "that fit no orientation are kept.")
        ef.addRow("match mode:", self.mode)
        self.tol = _fspin(0.001, 5.0, 4, 0.07, "°"); self.tol.setMaximumWidth(96)
        self.tol.setToolTip("2θ window around each allowed reflection.")
        self.hkl_max = _NoScrollSpinBox(); self.hkl_max.setRange(1, 20); self.hkl_max.setValue(8)
        self.hkl_max.setMaximumWidth(60)
        self.hkl_max.setToolTip("Largest |h|, |k|, |l| considered when listing the material's reflections.")
        r1 = QtWidgets.QHBoxLayout(); r1.setSpacing(6)
        r1.addWidget(self.tol); r1.addWidget(QtWidgets.QLabel("hkl max:")); r1.addWidget(self.hkl_max); r1.addStretch(1)
        ef.addRow("2θ tol:", r1)
        # orientation-mode extras (two form rows, shown only in that mode)
        self.orient_tol = _fspin(0.05, 10.0, 2, 0.5, "°"); self.orient_tol.setMaximumWidth(96)
        self.orient_tol.setToolTip("Angular tolerance between a spot's scattering-vector direction and the "
                                   "predicted reflection under a fitted orientation.")
        self.min_spots = _NoScrollSpinBox(); self.min_spots.setRange(2, 100); self.min_spots.setValue(3)
        self.min_spots.setMaximumWidth(60)
        self.min_spots.setToolTip("An orientation must own at least this many spots to count as a crystal "
                                  "of this material (two spots always fit some orientation).")
        self.max_crystals = _NoScrollSpinBox(); self.max_crystals.setRange(1, 10); self.max_crystals.setValue(2)
        self.max_crystals.setMaximumWidth(60)
        self.max_crystals.setToolTip("How many separate orientations to look for (e.g. 2 for a top and a "
                                     "bottom anvil).")
        self.orient_lbl = QtWidgets.QLabel("orientation tol:")
        ef.addRow(self.orient_lbl, self.orient_tol)
        self.orient_box = QtWidgets.QWidget()
        og = QtWidgets.QHBoxLayout(self.orient_box); og.setContentsMargins(0, 0, 0, 0); og.setSpacing(6)
        og.addWidget(self.min_spots); og.addWidget(QtWidgets.QLabel("crystals:")); og.addWidget(self.max_crystals)
        og.addStretch(1)
        self.orient_lbl2 = QtWidgets.QLabel("min spots:")
        ef.addRow(self.orient_lbl2, self.orient_box)
        self.min_counts = _fspin(0.0, 1e9, 1, 0.0); self.min_counts.setMaximumWidth(96)
        self.min_counts.setToolTip("A spot fainter than this (peak counts) is never flagged as this material. "
                                   "0 = no intensity condition. Anvil reflections are usually far brighter "
                                   "than the sample's.")
        ef.addRow("min peak counts:", self.min_counts)
        self.protect = QtWidgets.QCheckBox("keep spots that index on a solved domain")
        self.protect.setToolTip(
            "Takes effect when the filter is re-run after a domain has been solved: a spot this material "
            "claims is kept (and joins that domain) if it indexes on the domain's refined lattice within "
            "the tolerance. Rescues sample reflections that share a radius with this material.")
        self.protect_tol = _fspin(0.01, 0.5, 2, 0.15); self.protect_tol.setMaximumWidth(70)
        self.protect_tol.setToolTip("Largest distance from an integer hkl (in hkl units) that still counts as indexed.")
        pr = QtWidgets.QHBoxLayout(); pr.setSpacing(6)
        pr.addWidget(self.protect); pr.addWidget(self.protect_tol); pr.addStretch(1)
        ef.addRow(pr)
        bl.addWidget(self.editor)

        # ── global (advanced) ──
        adv = QtWidgets.QHBoxLayout(); adv.setSpacing(6)
        self.n_mc = _NoScrollSpinBox(); self.n_mc.setRange(100, 5_000_000); self.n_mc.setSingleStep(10000)
        self.n_mc.setValue(mf.N_MC_DEFAULT); self.n_mc.setMaximumWidth(92)
        self.n_mc.setToolTip("Monte-Carlo draws for the chance-coincidence z-score (how many flags a random "
                             "spot list would get).")
        self.seed = _NoScrollSpinBox(); self.seed.setRange(0, 2_000_000_000); self.seed.setValue(mf.RNG_SEED_DEFAULT)
        self.seed.setMaximumWidth(92); self.seed.setToolTip("Random seed of that Monte-Carlo (reproducibility).")
        adv.addWidget(QtWidgets.QLabel("MC draws:")); adv.addWidget(self.n_mc)
        adv.addWidget(QtWidgets.QLabel("seed:")); adv.addWidget(self.seed); adv.addStretch(1)
        bl.addLayout(adv)

        self.enable_cb.toggled.connect(self._on_enable)
        self.list.currentRowChanged.connect(self._load_editor)
        self.list.itemChanged.connect(self._on_item_changed)
        self.remove_btn.clicked.connect(self._remove)
        self.mode.currentIndexChanged.connect(self._sync_visibility)
        self.protect.toggled.connect(self._sync_visibility)
        for w in (self.name_ed,):
            w.textEdited.connect(self._commit)
        for w in (*self.cell, self.sg, self.tol, self.hkl_max, self.orient_tol, self.min_spots,
                  self.max_crystals, self.min_counts, self.protect_tol, self.n_mc, self.seed):
            w.valueChanged.connect(self._commit)
        for w in (self.ext, self.mode):
            w.currentIndexChanged.connect(self._commit)
        self.protect.toggled.connect(self._commit)

        self._refill_list(select=0)
        self._on_enable(True)

    # ── public API ────────────────────────────────────────────────────

    def filter_enabled(self) -> bool:
        return self.enable_cb.isChecked()

    def set_filter_enabled(self, on: bool):
        self.enable_cb.setChecked(bool(on))

    def materials(self) -> list:
        """The materials as plain dicts (what the pipeline stage takes)."""
        return [m.to_dict() for m in self._materials]

    def set_materials(self, materials: list):
        mats = []
        for d in materials or []:
            try:
                mats.append(mf.Material.from_dict(d))
            except Exception:
                continue            # a stale / hand-edited entry never blocks a restore
        self._materials = mats
        self._refill_list(select=0 if mats else -1)
        self.changed.emit()

    def get_state(self) -> dict:
        return {"enabled": self.filter_enabled(), "materials": self.materials(),
                "n_mc": self.n_mc.value(), "rng_seed": self.seed.value()}

    def set_state(self, st: dict):
        if not st:
            return
        self._loading = True
        try:
            if "materials" in st:
                self.set_materials(st["materials"])
            if "n_mc" in st:
                self.n_mc.setValue(int(st["n_mc"]))
            if "rng_seed" in st:
                self.seed.setValue(int(st["rng_seed"]))
            if "enabled" in st:
                self.set_filter_enabled(bool(st["enabled"]))
        finally:
            self._loading = False
        self.changed.emit()

    def n_mc_value(self) -> int:
        return int(self.n_mc.value())

    def seed_value(self) -> int:
        return int(self.seed.value())

    # ── internals ─────────────────────────────────────────────────────

    def _on_enable(self, on: bool):
        self.body.setVisible(bool(on))
        self.enabledChanged.emit(bool(on))
        if not self._loading:
            self.changed.emit()

    def _unique_name(self, base: str) -> str:
        names = {m.name for m in self._materials}
        if base not in names:
            return base
        k = 2
        while f"{base} {k}" in names:
            k += 1
        return f"{base} {k}"

    def _refill_list(self, select: int = -1):
        self._loading = True
        try:
            self.list.clear()
            for m in self._materials:
                it = QtWidgets.QListWidgetItem(m.name)
                it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
                it.setCheckState(QtCore.Qt.Checked if m.enabled else QtCore.Qt.Unchecked)
                self.list.addItem(it)
        finally:
            self._loading = False
        if self._materials and select >= 0:
            self.list.setCurrentRow(min(select, len(self._materials) - 1))
        else:
            self._load_editor(-1)

    def _add(self, m: "mf.Material"):
        m.name = self._unique_name(m.name)
        self._materials.append(m)
        self._refill_list(select=len(self._materials) - 1)
        self.changed.emit()

    def _add_preset(self, name: str):
        self._add(mf.preset(name))

    def _add_custom(self):
        self._add(mf.Material(name="Custom"))

    def _remove(self):
        r = self.list.currentRow()
        if 0 <= r < len(self._materials):
            del self._materials[r]
            self._refill_list(select=min(r, len(self._materials) - 1))
            self.changed.emit()

    def _on_item_changed(self, item):
        if self._loading:
            return
        r = self.list.row(item)
        if 0 <= r < len(self._materials):
            self._materials[r].enabled = item.checkState() == QtCore.Qt.Checked
            self.changed.emit()

    def _load_editor(self, row: int):
        has = 0 <= row < len(self._materials)
        self.editor.setEnabled(has)
        self.remove_btn.setEnabled(has)
        if not has:
            return
        m = self._materials[row]
        self._loading = True
        try:
            self.name_ed.setText(m.name)
            for sp, v in zip(self.cell, m.cell):
                sp.setValue(float(v))
            self.sg.setValue(int(m.space_group))
            self.ext.setCurrentIndex(max(0, self.ext.findData(m.extinction)))
            self.mode.setCurrentIndex(max(0, self.mode.findData(m.mode)))
            self.tol.setValue(m.tol_deg); self.hkl_max.setValue(int(m.hkl_max))
            self.orient_tol.setValue(m.orient_tol_deg); self.min_spots.setValue(int(m.min_spots))
            self.max_crystals.setValue(int(m.max_crystals)); self.min_counts.setValue(m.min_peak_counts)
            self.protect.setChecked(bool(m.protect_if_indexed)); self.protect_tol.setValue(m.protect_tol)
        finally:
            self._loading = False
        self._sync_visibility()

    def _sync_visibility(self, *_):
        orient = self.mode.currentData() == "orientation"
        for w in (self.orient_tol, self.orient_lbl, self.orient_box, self.orient_lbl2):
            w.setVisible(orient)
        self.protect_tol.setVisible(self.protect.isChecked())

    def _commit(self, *_):
        if self._loading:
            return
        r = self.list.currentRow()
        if not (0 <= r < len(self._materials)):
            return
        m = self._materials[r]
        m.name = self.name_ed.text().strip() or m.name
        m.cell = tuple(float(sp.value()) for sp in self.cell)
        m.space_group = int(self.sg.value()); m.extinction = self.ext.currentData()
        m.mode = self.mode.currentData(); m.tol_deg = float(self.tol.value()); m.hkl_max = int(self.hkl_max.value())
        m.orient_tol_deg = float(self.orient_tol.value()); m.min_spots = int(self.min_spots.value())
        m.max_crystals = int(self.max_crystals.value()); m.min_peak_counts = float(self.min_counts.value())
        m.protect_if_indexed = self.protect.isChecked(); m.protect_tol = float(self.protect_tol.value())
        it = self.list.item(r)
        if it is not None and it.text() != m.name:
            self._loading = True
            it.setText(m.name)
            self._loading = False
        self.changed.emit()
