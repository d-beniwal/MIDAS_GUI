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

from typing import Dict, List, Optional

from PyQt5 import QtCore, QtWidgets
from midas_gui import style as S

DATA = "data"
CONTROLS = "controls"
_TITLES = {DATA: "Data", CONTROLS: "Controls"}

# Layout persistence / content-fit tuning.
_SETTINGS_ORG, _SETTINGS_APP = "MIDAS", "midas-gui"
_MAX_AUTO_MIN = 520      # content-derived minimum width never exceeds this (px)
_REFIT_MS = 1000         # how often content-derived minimums are re-checked

# Default proportions (share of the splitter width).  Every launch, on every
# machine, starts from these, so Results (the plots) always keeps the largest
# share: Data ~14 %, Controls ~27 %, Results the rest (~59 %).  A panel's width
# may grow to fit its content, but never past the cap share, so Results keeps
# at least ~48 % however long a panel's rows are.
_DEFAULT_FRAC = {DATA: 0.14, CONTROLS: 0.27}
_CAP_FRAC = {DATA: 0.22, CONTROLS: 0.30}
_MIN_TOTAL = 600         # ignore a splitter narrower than this (not laid out yet)
# Remembered widths are stored as a share of the splitter width (portable
# across screens/OSes).  "v2" drops the older absolute-pixel values, which
# could be stale or oversized.
_SKEY_VERSION = "layout2"


def _settings() -> QtCore.QSettings:
    return QtCore.QSettings(_SETTINGS_ORG, _SETTINGS_APP)


def _content_width(panel: QtWidgets.QWidget) -> int:
    """Width a panel needs to show its content without clipping or a
    horizontal scrollbar.  For a scroll area that is the inner widget's
    minimum width plus frame and vertical-scrollbar allowance."""
    if isinstance(panel, QtWidgets.QScrollArea) and panel.widget() is not None:
        extra = 2 * panel.frameWidth() + panel.verticalScrollBar().sizeHint().width()
        return panel.widget().minimumSizeHint().width() + extra
    return panel.minimumSizeHint().width()

_HEADER_STYLE = f"background:{S.STRIP_BG}; border-bottom:1px solid {S.STRIP_BORDER};"
_MIN_BTN_STYLE = (
    f"QToolButton {{ border:none; color:{S.SUBTLE}; padding:0 6px; font-size:12px; }}"
    "QToolButton:hover { background:#3d5a80; color:#fff; }"
)
_TAB_STYLE = (
    f"QToolButton {{ background:{S.STRIP_TAB_BG}; color:{S.STRIP_TAB_FG}; border:1px solid {S.STRIP_TAB_BORDER};"
    " border-bottom:none; border-top-left-radius:4px; border-top-right-radius:4px;"
    " padding:2px 12px; font-size:11px; }"
    "QToolButton:hover { background:#3d5a80; color:#fff; }"
)


class _HelpPopup(QtWidgets.QFrame):
    """Click-away popup carrying a short explanatory text (word-wrapped)."""

    def __init__(self, html: str, parent=None):
        super().__init__(parent, QtCore.Qt.Popup)
        self.setStyleSheet(
            f"QFrame {{ background:{S.STRIP_BG}; border:1px solid {S.ACCENT};"
            " border-radius:3px; }"
            f"QLabel {{ color:{S.MUTED}; border:none; font-size:11px; }}")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        self.label = QtWidgets.QLabel(html)
        self.label.setTextFormat(QtCore.Qt.RichText)
        self.label.setWordWrap(True)
        self.label.setFixedWidth(340)
        lay.addWidget(self.label)

    def show_below(self, widget: QtWidgets.QWidget) -> None:
        self.adjustSize()
        pos = widget.mapToGlobal(QtCore.QPoint(widget.width() - self.width(),
                                               widget.height()))
        self.move(max(pos.x(), 0), pos.y())
        self.show()


class PanelToggles(QtCore.QObject):
    """Headers + bottom ribbon for one Data | Controls | Results splitter."""

    changed = QtCore.pyqtSignal()

    def __init__(self, split: QtWidgets.QSplitter, data_idx: int = 0,
                 controls_idx: int = 1, key: Optional[str] = None):
        super().__init__(split)
        self._split = split
        self._key = key                      # settings key; None = don't persist
        self._base_min: Dict[str, int] = {}   # hard minimum set by the tab
        self._defaults: Dict[str, int] = {}   # tab's initial width per panel
        self._idx = {DATA: data_idx, CONTROLS: controls_idx}
        self._results_idx = split.count() - 1
        self._saved: Dict[str, int] = {}
        self._minimized: Dict[str, bool] = {DATA: False, CONTROLS: False}
        self._containers: Dict[str, QtWidgets.QWidget] = {}
        self._tabs: Dict[str, QtWidgets.QToolButton] = {}
        self._hdr_layouts: Dict[str, QtWidgets.QHBoxLayout] = {}
        self._help_popups: Dict[str, _HelpPopup] = {}

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
            self._base_min[key] = panel.minimumWidth()
            self._defaults[key] = sizes[idx]
        for i in range(split.count()):
            split.setStretchFactor(i, 1 if i == self._results_idx else 0)
        split.setSizes(sizes)
        del stretch

        # Content-fit minimums, then the user's remembered widths.  Content
        # can grow later (cards appear, labels lengthen), so re-fit on a slow
        # timer while the tab is on screen.  (Deliberately no Python
        # eventFilter on the panels: one called during widget teardown
        # segfaulted the Hydra tests.)
        # The splitter has no real width until it is shown, so the proportional
        # widths are applied by the first timer tick after it is visible.
        self._applied = False
        self._refit_all()
        self._fit_timer = QtCore.QTimer(self)
        self._fit_timer.setInterval(40)
        self._fit_timer.timeout.connect(self._refit_visible)
        self._fit_timer.start()
        self._save_timer = QtCore.QTimer(self)
        self._save_timer.setSingleShot(True); self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._save_widths)
        split.splitterMoved.connect(lambda *_: self._save_timer.start())

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
        self._ribbon_lay = rl
        self._extra: Dict[str, QtWidgets.QToolButton] = {}
        self.ribbon.setVisible(False)

    def _make_header(self, key: str) -> QtWidgets.QWidget:
        hdr = QtWidgets.QWidget()
        hdr.setFixedHeight(20); hdr.setStyleSheet(_HEADER_STYLE)
        hl = QtWidgets.QHBoxLayout(hdr)
        hl.setContentsMargins(6, 0, 0, 0); hl.setSpacing(0)
        lbl = QtWidgets.QLabel(_TITLES[key])
        lbl.setStyleSheet(f"color:{S.MUTED}; font-size:10px; font-weight:bold;"
                          " letter-spacing:1px; border:none;")
        hl.addWidget(lbl, 1)
        self._hdr_layouts[key] = hl
        rst = QtWidgets.QToolButton()
        rst.setText("⟲"); rst.setFixedHeight(20)
        rst.setStyleSheet(_MIN_BTN_STYLE)
        rst.setCursor(QtCore.Qt.PointingHandCursor)
        rst.setToolTip("Reset the Data and Controls widths to fit their content "
                       "(forgets the remembered widths)")
        rst.clicked.connect(lambda _=False: self.reset_widths())
        hl.addWidget(rst)
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
    def add_help(self, key: str, html: str) -> QtWidgets.QToolButton:
        """Put a ``?`` button in panel ``key``'s header that pops up ``html``."""
        btn = QtWidgets.QToolButton()
        btn.setText("?"); btn.setFixedHeight(20)
        btn.setStyleSheet(_MIN_BTN_STYLE)
        btn.setCursor(QtCore.Qt.PointingHandCursor)
        btn.setToolTip("What does this page do? (click)")
        pop = _HelpPopup(html, btn)
        self._help_popups[key] = pop
        btn.clicked.connect(lambda _=False: pop.show_below(btn))
        self._hdr_layouts[key].insertWidget(1, btn)
        return btn

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
        self._update_ribbon()
        self.changed.emit()

    def add_ribbon_tab(self, title: str, restore) -> QtWidgets.QToolButton:
        """Add a restore tab for an extra (non-splitter) panel.

        ``restore`` is called when the tab is clicked.  Show/hide the tab with
        :meth:`set_extra_tab_visible`; the ribbon follows automatically.
        """
        tab = QtWidgets.QToolButton()
        tab.setText(f"▲  {title}")
        tab.setStyleSheet(_TAB_STYLE)
        tab.setCursor(QtCore.Qt.PointingHandCursor)
        tab.setToolTip(f"Restore the {title} panel")
        tab.clicked.connect(lambda _=False: restore())
        tab.setVisible(False)
        self._ribbon_lay.insertWidget(self._ribbon_lay.count() - 1, tab)
        self._extra[title] = tab
        return tab

    def set_extra_tab_visible(self, title: str, visible: bool) -> None:
        self._extra[title].setVisible(visible)
        self._update_ribbon()

    def _update_ribbon(self) -> None:
        self.ribbon.setVisible(any(self._minimized.values())
                               or any(t.isVisibleTo(self.ribbon)
                                      for t in self._extra.values()))

    def reset_widths(self) -> None:
        """Return Data / Controls to their fit-to-content widths and forget
        the remembered ones."""
        if self._key:
            s = _settings()
            for k in self._idx:
                s.remove(self._skey(k))
        sizes = list(self._split.sizes())
        r = self._results_idx
        for k, i in self._idx.items():
            if self._minimized[k]:
                self._saved.pop(k, None)
                continue
            w = self._default_for(k)
            sizes[r] += sizes[i] - w; sizes[i] = w
        self._split.setSizes(sizes)

    # ── internals ──
    def _panel(self, key: str) -> QtWidgets.QWidget:
        """The real panel inside a key's header wrapper."""
        return self._containers[key].layout().itemAt(1).widget()

    def _total(self) -> int:
        """Splitter width, or 0 if it has not been laid out yet."""
        w = self._split.width()
        return w if w >= _MIN_TOTAL else 0

    def _auto_min(self, key: str) -> int:
        cap = _MAX_AUTO_MIN
        total = self._total()
        if total:
            cap = min(cap, int(_CAP_FRAC[key] * total))
        return max(self._base_min[key], min(_content_width(self._panel(key)), cap))

    def _default_for(self, key: str) -> int:
        total = self._total()
        if not total:
            return max(self._defaults[key], self._auto_min(key))
        return max(int(_DEFAULT_FRAC[key] * total), self._auto_min(key))

    def _refit_all(self) -> None:
        for k in self._idx:
            m = self._auto_min(k)
            if self._containers[k].minimumWidth() != m:
                self._panel(k).setMinimumWidth(m)
                self._containers[k].setMinimumWidth(m)
            # A panel that caps its own width (content-sized Controls) must
            # cap the header wrapper too, or the splitter still lets the
            # handle be dragged past it and the header bar stretches.
            pmax = self._panel(k).maximumWidth()
            if self._containers[k].maximumWidth() != pmax:
                self._containers[k].setMaximumWidth(pmax)

    def _refit_visible(self) -> None:
        if not self._split.isVisible() or not self._total():
            return
        self._refit_all()
        if not self._applied:
            self._applied = True
            self._restore_widths()
            self._fit_timer.setInterval(_REFIT_MS)

    def _skey(self, key: str) -> str:
        return f"{_SKEY_VERSION}/{self._key}/{key}_frac"

    def _restore_widths(self) -> None:
        sizes = list(self._split.sizes())
        r = self._results_idx
        total = self._total() or sum(sizes)
        s = _settings() if self._key else None
        for k, i in self._idx.items():
            if self._minimized[k]:
                continue
            w = self._default_for(k)
            if s is not None:
                try:
                    frac = float(s.value(self._skey(k), 0) or 0)
                except (TypeError, ValueError):
                    frac = 0.0
                if frac > 0:
                    w = max(self._auto_min(k), min(int(frac * total),
                                                   int(_CAP_FRAC[k] * total)))
            sizes[r] += sizes[i] - w; sizes[i] = w
        self._split.setSizes(sizes)

    def _save_widths(self) -> None:
        if not self._key:
            return
        s = _settings()
        sizes = self._split.sizes()
        total = self._total() or sum(sizes)
        if not total:
            return
        for k, i in self._idx.items():
            if not self._minimized[k] and sizes[i] > 0:
                s.setValue(self._skey(k), round(sizes[i] / total, 4))

    def _default_width(self, i: int) -> int:
        for k, idx in self._idx.items():
            if idx == i:
                return self._default_for(k)
        w = self._split.widget(i)
        return max(w.minimumWidth(), w.sizeHint().width(), 200)


def install_panel_toggles(split: QtWidgets.QSplitter, data_idx: int = 0,
                          controls_idx: int = 1,
                          key: Optional[str] = None) -> PanelToggles:
    """Give a three-panel splitter minimisable Data / Controls panels.

    Call after every panel (Results last) has been added and sized, then add
    the returned object's ``ribbon`` widget below the splitter.  Also kept on
    ``split.panel_toggles``.

    Panel minimums follow their content, and the Data / Controls widths are
    remembered across sessions under ``key`` (a short per-tab name; omit to
    skip persistence).  The ⟲ button in each panel header resets the widths.
    """
    toggles = PanelToggles(split, data_idx, controls_idx, key)
    split.panel_toggles = toggles
    return toggles
