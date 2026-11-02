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


def _toggles(tab, key):
    """The splitter + PanelToggles with this key (a tab may also host a Hydra
    page whose own, hidden, splitter carries a different key)."""
    from PyQt5.QtWidgets import QSplitter
    split = next(w for w in tab.findChildren(QSplitter)
                 if getattr(getattr(w, "panel_toggles", None), "_key", None) == key)
    return split, split.panel_toggles


@pytest.mark.parametrize("name", ["view", "calibrate", "batch", "pumpprobe"])
def test_minimize_restore(app, name):
    tab = _make(name)
    tab.resize(1600, 900); tab.show(); app.processEvents()
    split, pt = _toggles(tab, name)
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
    # The offscreen screen is tiny; don't let the 45%-of-screen cap clamp widths.
    monkeypatch.setattr(panel_toggle.PanelToggles, "_screen_width",
                        staticmethod(lambda: 4000))
    QtCore.QSettings.setDefaultFormat(QtCore.QSettings.IniFormat)
    QtCore.QSettings.setPath(QtCore.QSettings.IniFormat, QtCore.QSettings.UserScope,
                             str(tmp_path))
    yield




def test_widths_persist_and_reset(app, isolated_settings):
    tab = _make("calibrate")
    tab.resize(1600, 900); tab.show(); app.processEvents()
    split, pt = _toggles(tab, "calibrate")
    default = split.sizes()[1]
    sizes = split.sizes(); sizes[1] += 90; sizes[2] -= 90
    split.setSizes(sizes); app.processEvents()
    pt._save_widths()
    wide = split.sizes()[1]
    tab.deleteLater(); app.processEvents()

    tab2 = _make("calibrate")                       # fresh tab = new session
    tab2.resize(1600, 900); tab2.show(); app.processEvents()
    split2, pt2 = _toggles(tab2, "calibrate")
    assert abs(split2.sizes()[1] - wide) <= 2
    pt2.reset_widths(); app.processEvents()
    assert abs(split2.sizes()[1] - default) <= 2
    tab2.deleteLater(); app.processEvents()


def test_controls_minimum_follows_content(app, isolated_settings):
    tab = _make("batch")
    tab.resize(1600, 900); tab.show(); app.processEvents()
    split, pt = _toggles(tab, "batch")
    from midas_gui.panel_toggle import _content_width, _MAX_AUTO_MIN
    need = min(_content_width(pt._panel("controls")), _MAX_AUTO_MIN)
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
    from midas_gui.panel_toggle import _content_width, _MAX_AUTO_MIN
    need = min(_content_width(pt._panel("controls")), _MAX_AUTO_MIN)
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

    sizes = split.sizes(); sizes[1] += 70; sizes[2] -= 70
    split.setSizes(sizes); app.processEvents()
    pt._save_widths(); wide = split.sizes()[1]
    page.deleteLater(); app.processEvents()

    page2 = _make_hydra(name)
    page2.resize(1600, 900); page2.show(); _settle(app)
    split2, _ = _toggles(page2, name)
    assert abs(split2.sizes()[1] - wide) <= 2
    page2.deleteLater(); app.processEvents()
