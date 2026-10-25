"""Minimise / restore for the three-panel tab layout.

GUI terminology (global — use these names in UI text, docs and code):

* **Data** panel     — left-most; loads and selects the data.
* **Controls** panel — middle; analysis parameters plus the run buttons.
* **Results** panel  — right-most; every plot / visualisation.

Each tab splits these with a horizontal ``QSplitter`` (Data | Controls |
Results).  :func:`install_panel_toggles` gives the Data and Controls panels a
slim header with a minimise button.  A minimised panel disappears and a tab
for it appears in a ribbon along the bottom of the tab; clicking that tab
restores the panel.  The width a panel gave up goes to the Results panel,
which has no minimise button because it is the one that grows.
"""
from __future__ import annotations

from typing import Dict, List

from PyQt5 import QtCore, QtWidgets

DATA = "data"
CONTROLS = "controls"
_TITLES = {DATA: "Data", CONTROLS: "Controls"}

_HEADER_STYLE = "background:#262626; border-bottom:1px solid #3a3a3a;"
_MIN_BTN_STYLE = (
    "QToolButton { border:none; color:#aaa; padding:0 6px; font-size:12px; }"
    "QToolButton:hover { background:#3d5a80; color:#fff; }"
)
_TAB_STYLE = (
    "QToolButton { background:#2b2b2b; color:#ccc; border:1px solid #444;"
    " border-bottom:none; border-top-left-radius:4px; border-top-right-radius:4px;"
    " padding:2px 12px; font-size:11px; }"
    "QToolButton:hover { background:#3d5a80; color:#fff; }"
)


class PanelToggles(QtCore.QObject):
    """Headers + bottom ribbon for one Data | Controls | Results splitter."""

    changed = QtCore.pyqtSignal()

    def __init__(self, split: QtWidgets.QSplitter, data_idx: int = 0,
                 controls_idx: int = 1):
        super().__init__(split)
        self._split = split
        self._idx = {DATA: data_idx, CONTROLS: controls_idx}
        self._results_idx = split.count() - 1
        self._saved: Dict[str, int] = {}
        self._minimized: Dict[str, bool] = {DATA: False, CONTROLS: False}
        self._containers: Dict[str, QtWidgets.QWidget] = {}
        self._tabs: Dict[str, QtWidgets.QToolButton] = {}

        # Wrap each panel under its header.  Reparenting the panel into the
        # wrapper pulls it out of the splitter, so remember and re-apply the
        # splitter's stretch factors and sizes afterwards.
        sizes = split.sizes()
        stretch = [split.widget(i).sizePolicy().horizontalStretch() or 0
                   for i in range(split.count())]
        for key, idx in self._idx.items():
            panel = split.widget(idx)
            box = QtWidgets.QWidget()
            lay = QtWidgets.QVBoxLayout(box)
            lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)
            lay.addWidget(self._make_header(key))
            lay.addWidget(panel, 1)
            split.insertWidget(idx, box)
            box.setMinimumWidth(panel.minimumWidth())
            self._containers[key] = box
        for i in range(split.count()):
            split.setStretchFactor(i, 1 if i == self._results_idx else 0)
        split.setSizes(sizes)
        del stretch

        # Bottom ribbon: one restore tab per minimised panel.
        self.ribbon = QtWidgets.QWidget()
        rl = QtWidgets.QHBoxLayout(self.ribbon)
        rl.setContentsMargins(6, 2, 6, 0); rl.setSpacing(4)
        for key in (DATA, CONTROLS):
            tab = QtWidgets.QToolButton()
            tab.setText(f"▲  {_TITLES[key]}")
            tab.setStyleSheet(_TAB_STYLE)
            tab.setCursor(QtCore.Qt.PointingHandCursor)
            tab.setToolTip(f"Restore the {_TITLES[key]} panel")
            tab.clicked.connect(lambda _=False, k=key: self.set_minimized(k, False))
            tab.setVisible(False)
            rl.addWidget(tab)
            self._tabs[key] = tab
        rl.addStretch(1)
        self.ribbon.setVisible(False)

    def _make_header(self, key: str) -> QtWidgets.QWidget:
        hdr = QtWidgets.QWidget()
        hdr.setFixedHeight(20); hdr.setStyleSheet(_HEADER_STYLE)
        hl = QtWidgets.QHBoxLayout(hdr)
        hl.setContentsMargins(6, 0, 0, 0); hl.setSpacing(0)
        lbl = QtWidgets.QLabel(_TITLES[key])
        lbl.setStyleSheet("color:#999; font-size:10px; font-weight:bold;"
                          " letter-spacing:1px; border:none;")
        hl.addWidget(lbl, 1)
        btn = QtWidgets.QToolButton()
        btn.setText("—"); btn.setFixedHeight(20)
        btn.setStyleSheet(_MIN_BTN_STYLE)
        btn.setCursor(QtCore.Qt.PointingHandCursor)
        btn.setToolTip(f"Minimize the {_TITLES[key]} panel to the bottom ribbon "
                       "(the Results panel takes the space)")
        btn.clicked.connect(lambda _=False, k=key: self.set_minimized(k, True))
        hl.addWidget(btn)
        return hdr

    # ── public API ──
    def is_minimized(self, key: str) -> bool:
        return self._minimized[key]

    def set_minimized(self, key: str, minimized: bool) -> None:
        if self._minimized[key] == minimized:
            return
        split, box = self._split, self._containers[key]
        i, r = self._idx[key], self._results_idx
        sizes: List[int] = list(split.sizes())
        if minimized:
            self._saved[key] = sizes[i] or self._default_width(i)
            box.setVisible(False)
            sizes[r] += sizes[i]; sizes[i] = 0
        else:
            w = self._saved.get(key) or self._default_width(i)
            room = sizes[r] - split.widget(r).minimumWidth()
            w = max(1, min(w, room)) if room > 0 else w
            box.setVisible(True)
            sizes[i] = w; sizes[r] = max(sizes[r] - w, 0)
        self._minimized[key] = minimized
        split.setSizes(sizes)
        self._tabs[key].setVisible(minimized)
        self.ribbon.setVisible(any(self._minimized.values()))
        self.changed.emit()

    # ── internals ──
    def _default_width(self, i: int) -> int:
        w = self._split.widget(i)
        return max(w.minimumWidth(), w.sizeHint().width(), 200)


def install_panel_toggles(split: QtWidgets.QSplitter, data_idx: int = 0,
                          controls_idx: int = 1) -> PanelToggles:
    """Give a three-panel splitter minimisable Data / Controls panels.

    Call after every panel (Results last) has been added and sized, then add
    the returned object's ``ribbon`` widget below the splitter.  Also kept on
    ``split.panel_toggles``.
    """
    toggles = PanelToggles(split, data_idx, controls_idx)
    split.panel_toggles = toggles
    return toggles
