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


@pytest.mark.parametrize("name", ["view", "calibrate", "batch", "pumpprobe"])
def test_minimize_restore(app, name):
    tab = _make(name)
    tab.resize(1600, 900); tab.show(); app.processEvents()
    from PyQt5.QtWidgets import QSplitter
    split = next(w for w in tab.findChildren(QSplitter) if hasattr(w, "panel_toggles"))
    pt = split.panel_toggles
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
