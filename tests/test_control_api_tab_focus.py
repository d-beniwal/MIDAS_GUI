"""GuiController.ensure_tab_visible: namespace-tab switching plus the
per-op sub-focus (Radial Profile / Eta vs R Cake) added so a human watching
the live GUI can see exactly which result an MCP-driven op just produced,
not just that the Data Viewer tab is active.

Not fork-isolated, for the same reason as test_view_tab_controls.py: this
file only builds one DataViewerTab, shared module-wide — see that file's own
docstring for why building several in one process is unsafe.
"""
from __future__ import annotations

import types

import pytest
from PyQt5 import QtWidgets

from midas_gui.control_api import GuiController
from midas_gui.tab_view import DataViewerTab


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _FakeCalTab(QtWidgets.QWidget):
    """Stand-in for CalibrationTab: GuiController only needs it to expose
    set_agent_mode and to be a distinct tab widget."""
    def set_agent_mode(self, on):
        pass


@pytest.fixture(scope="module")
def win(app):
    """A minimal MainWindow-shaped object: one real DataViewerTab + a fake
    Calibrate tab, both added to a real QTabWidget — enough for
    ensure_tab_visible to switch between them and into the Data Viewer's own
    bottom sub-tabs."""
    view_tab = DataViewerTab()
    cal_tab = _FakeCalTab()
    tabs = QtWidgets.QTabWidget()
    tabs.addTab(view_tab, "Data Viewer")
    tabs.addTab(cal_tab, "Calibrate")
    return types.SimpleNamespace(_view_tab=view_tab, _cal_tab=cal_tab, _tabs=tabs)


@pytest.fixture(scope="module")
def ctrl(win):
    return GuiController(win)


def test_data_viewer_op_switches_the_namespace_tab(ctrl, win):
    win._tabs.setCurrentWidget(win._cal_tab)
    ctrl.ensure_tab_visible("data_viewer.load_file")
    assert win._tabs.currentWidget() is win._view_tab


def test_unrelated_data_viewer_op_leaves_the_bottom_subtab_alone(ctrl, win):
    win._view_tab._bottom_tabs.setCurrentWidget(win._view_tab._cake_view)
    ctrl.ensure_tab_visible("data_viewer.set_geometry")
    assert win._view_tab._bottom_tabs.currentWidget() is win._view_tab._cake_view


def test_get_radial_profile_switches_to_the_radial_profile_subtab(ctrl, win):
    win._tabs.setCurrentWidget(win._cal_tab)
    win._view_tab._bottom_tabs.setCurrentWidget(win._view_tab._cake_view)
    ctrl.ensure_tab_visible("data_viewer.get_radial_profile")
    assert win._tabs.currentWidget() is win._view_tab
    assert win._view_tab._bottom_tabs.currentWidget() is win._view_tab._profile_view


def test_get_cake_switches_to_the_cake_subtab(ctrl, win):
    win._tabs.setCurrentWidget(win._cal_tab)
    win._view_tab._bottom_tabs.setCurrentWidget(win._view_tab._profile_view)
    ctrl.ensure_tab_visible("data_viewer.get_cake")
    assert win._tabs.currentWidget() is win._view_tab
    assert win._view_tab._bottom_tabs.currentWidget() is win._view_tab._cake_view


def test_calibrate_namespace_switches_tab_without_touching_data_viewer_subtabs(ctrl, win):
    win._view_tab._bottom_tabs.setCurrentWidget(win._view_tab._cake_view)
    ctrl.ensure_tab_visible("calibrate.run")
    assert win._tabs.currentWidget() is win._cal_tab
    assert win._view_tab._bottom_tabs.currentWidget() is win._view_tab._cake_view


def test_unknown_namespace_is_a_no_op(ctrl, win):
    win._tabs.setCurrentWidget(win._cal_tab)
    ctrl.ensure_tab_visible("not_a_real_namespace.whatever")
    assert win._tabs.currentWidget() is win._cal_tab
