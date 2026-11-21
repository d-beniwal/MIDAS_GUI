"""Data/Controls minimise-to-ribbon works on each 3-panel tab (forked: real Qt tabs)."""
import pytest

pytestmark = pytest.mark.forked


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _make(name):
    if name == "view":
        from midas_gui.tab_view import DataViewerTab as T
    elif name == "calibrate":
        from midas_gui.tab_calibrate import CalibrationTab as T
    elif name == "batch":
        from midas_gui.tab_batch import BatchTab as T
    else:
        from midas_gui.tab_pumpprobe import PumpProbeTab as T
    return T()


def _settle(app, n=30):
    for _ in range(n):
        app.processEvents()


def _wait_until(app, cond, timeout_s=3.0):
    import time
    end = time.time() + timeout_s
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def _toggles(tab, key):
    """The splitter + PanelToggles with this key (a tab may also host a Hydra
    page whose own, hidden, splitter carries a different key)."""
    from PyQt5.QtWidgets import QSplitter
    split = next(w for w in tab.findChildren(QSplitter)
                 if getattr(getattr(w, "panel_toggles", None), "_key", None) == key)
    return split, split.panel_toggles


def _applied(app, pt):
    """Wait for the first proportional layout (applied once the tab is shown)."""
    _wait_until(app, lambda: pt._applied)
    app.processEvents()


@pytest.mark.parametrize("name", ["view", "calibrate", "batch", "pumpprobe"])
def test_minimize_restore(app, name):
    tab = _make(name)
    tab.resize(1600, 900); tab.show(); app.processEvents()
    split, pt = _toggles(tab, name)
    _applied(app, pt)
    before = split.sizes()
    pt.set_minimized("data", True); pt.set_minimized("controls", True); app.processEvents()
    s = split.sizes()
    assert s[0] == 0 and s[1] == 0
    assert s[2] >= before[2] + before[0] + before[1]
    assert pt.ribbon.isVisible()
    pt._tabs["data"].click(); pt._tabs["controls"].click(); app.processEvents()
    after = split.sizes()
    assert abs(after[0] - before[0]) <= 2 and abs(after[1] - before[1]) <= 2
    assert not pt.ribbon.isVisible()
    tab.deleteLater(); app.processEvents()


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    from PyQt5 import QtCore
    from midas_gui import panel_toggle
    QtCore.QSettings.setDefaultFormat(QtCore.QSettings.IniFormat)
    QtCore.QSettings.setPath(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope,
                             str(tmp_path))
    yield




def test_widths_persist_and_reset(app, isolated_settings):
    tab = _make("calibrate")
    tab.resize(1600, 900); tab.show(); app.processEvents()
    split, pt = _toggles(tab, "calibrate")
    _applied(app, pt)
    assert split.sizes()[1] - 40 >= pt._auto_min("controls")
    sizes = split.sizes(); sizes[1] -= 40; sizes[2] += 40
    split.setSizes(sizes); app.processEvents()
    pt._save_widths()
    wide = split.sizes()[1]
    tab.deleteLater(); app.processEvents()

    tab2 = _make("calibrate")                       # fresh tab = new session
    tab2.resize(1600, 900); tab2.show(); app.processEvents()
    split2, pt2 = _toggles(tab2, "calibrate")
    _applied(app, pt2)
    assert abs(split2.sizes()[1] - wide) <= 2
    pt2.reset_widths(); app.processEvents()
    assert abs(split2.sizes()[1] - pt2._default_for("controls")) <= 2
    tab2.deleteLater(); app.processEvents()


def test_controls_minimum_follows_content(app, isolated_settings):
    tab = _make("batch")
    tab.resize(1600, 900); tab.show(); app.processEvents()
    split, pt = _toggles(tab, "batch")
    _applied(app, pt)
    need = pt._auto_min("controls")      # content width, capped by window share
    assert split.sizes()[1] >= need
    assert pt._containers["controls"].minimumWidth() >= need
    tab.deleteLater(); app.processEvents()


def _make_hydra(name):
    if name == "hydra_view":
        from midas_gui.hydra_page import HydraViewerPage as P
    elif name == "hydra_calibrate":
        from midas_gui.hydra_calib_page import HydraCalibrationPage as P
    else:
        from midas_gui.hydra_batch_page import HydraBatchPage as P
    return P()


@pytest.mark.parametrize("name", ["hydra_view", "hydra_calibrate", "hydra_batch"])
def test_hydra_minimize_restore_and_persist(app, isolated_settings, name):
    page = _make_hydra(name)
    page.resize(1600, 900); page.show(); _settle(app)
    split, pt = _toggles(page, name)
    _applied(app, pt)
    need = pt._auto_min("controls")
    # PanelToggles re-checks content width on a 1 s timer while visible, and
    # some Hydra pages widen after first layout, so wait on the condition
    # (bounded) instead of a fixed number of event-loop passes.
    _wait_until(app, lambda: split.sizes()[1] >= need)
    assert split.sizes()[1] >= need

    before = split.sizes()
    pt.set_minimized("data", True); pt.set_minimized("controls", True); app.processEvents()
    s = split.sizes()
    assert s[0] == 0 and s[1] == 0 and pt.ribbon.isVisible()
    assert s[2] >= before[2] + before[0] + before[1]
    pt._tabs["data"].click(); pt._tabs["controls"].click(); app.processEvents()
    after = split.sizes()
    assert abs(after[0] - before[0]) <= 2 and abs(after[1] - before[1]) <= 2
    assert not pt.ribbon.isVisible()

    sizes = split.sizes(); sizes[1] -= 30; sizes[2] += 30
    split.setSizes(sizes); app.processEvents()
    pt._save_widths(); wide = split.sizes()[1]
    page.deleteLater(); app.processEvents()

    page2 = _make_hydra(name)
    page2.resize(1600, 900); page2.show(); _settle(app)
    split2, pt2 = _toggles(page2, name)
    _applied(app, pt2)
    assert abs(split2.sizes()[1] - wide) <= 2
    page2.deleteLater(); app.processEvents()


@pytest.mark.parametrize("width", [1100, 1600, 2400])
@pytest.mark.parametrize("name", ["view", "calibrate", "batch"])
def test_default_proportions_keep_results_large(app, isolated_settings, name, width):
    """Whatever the window width (and whatever any panel's content wants),
    a fresh launch gives Results the largest share and Data/Controls stay
    within their caps."""
    from midas_gui.panel_toggle import _CAP_FRAC
    tab = _make(name)
    tab.resize(width, 900); tab.show(); app.processEvents()
    split, pt = _toggles(tab, name)
    _applied(app, pt)
    d, c, r = split.sizes()
    total = split.width()
    assert r >= 0.45 * total, (d, c, r)
    assert d <= _CAP_FRAC["data"] * total + 2
    assert c <= _CAP_FRAC["controls"] * total + 2
    tab.deleteLater(); app.processEvents()


def test_stale_absolute_pixel_settings_are_ignored(app, isolated_settings):
    from PyQt5 import QtCore
    s = QtCore.QSettings("MIDAS", "midas-gui")
    s.setValue("layout/calibrate/controls_width", 1500)   # legacy oversized value
    tab = _make("calibrate")
    tab.resize(1600, 900); tab.show(); app.processEvents()
    split, pt = _toggles(tab, "calibrate")
    _applied(app, pt)
    d, c, r = split.sizes()
    assert r >= 0.45 * split.width()
    tab.deleteLater(); app.processEvents()
