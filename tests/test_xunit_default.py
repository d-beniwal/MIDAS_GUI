"""A Q/2θ-rebinned run labels its waterfall / stack x-axis in that unit."""
import pytest

pytest.importorskip("PyQt5")
from PyQt5 import QtWidgets   # noqa: E402

from midas_gui.widgets import StackedProfileViewer, WaterfallViewer  # noqa: E402


_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.mark.parametrize("cls", [WaterfallViewer, StackedProfileViewer])
def test_display_unit_preselects_axis(cls):
    v = cls()
    for unit, label in (("Q", "Q (Å⁻¹)"), ("2th", "2θ (°)"), ("R", "R (px)")):
        v.set_axis_context(599928.0, 150.0, 0.1958, "R", unit)
        assert v._xunit_combo.currentData() == unit
        assert v._plot.getPlotItem().getAxis("bottom").labelText == label
    v.set_axis_context(599928.0, 150.0, 0.1958, "R", None)   # None keeps choice
    assert v._xunit_combo.currentData() == "R"
