"""Offscreen UI test for the Data Viewer tab's Hydra page: mode switching,
sibling loading, per-panel/composite display, per-panel calibration wiring,
and GUI-state round-trip. Uses the small synthetic fixture at
test_data/gui_synthetic/hydra/ (see make_hydra_test_data.py).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PyQt5 import QtWidgets

from midas_gui.helpers import geometry_fields_from_file
from midas_gui.tab_view import DataViewerTab

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "test_data" / "gui_synthetic" / "hydra"


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture()
def fixture_available():
    if not FIXTURE_DIR.exists():
        pytest.skip("test_data/gui_synthetic/hydra/ fixture not present — "
                    "run make_hydra_test_data.py")
    return FIXTURE_DIR


def test_mode_ribbon_switches_pages(app):
    tab = DataViewerTab()
    assert tab._mode_ribbon.mode() == "single"
    assert tab._mode_stack.currentWidget() is tab._hsplit

    tab._mode_ribbon.set_mode("hydra")
    assert tab._mode_stack.currentWidget() is tab._hydra_page

    tab._mode_ribbon.set_mode("single")
    assert tab._mode_stack.currentWidget() is tab._hsplit


def test_hydra_page_loads_siblings_and_shows_panels(app, fixture_available):
    tab = DataViewerTab()
    tab._mode_ribbon.set_mode("hydra")
    hp = tab._hydra_page

    hp._loader.set_path(str(fixture_available / "ge1" / "panel.ge1.h5"))
    app.processEvents()
    assert sorted(hp._loader.siblings().keys()) == [1, 2, 3, 4]
    assert hp._toolbar.current() == "ge1"
    for n in (1, 2, 3, 4):
        assert hp._toolbar._buttons[f"ge{n}"].isEnabled()
    assert hp._toolbar._buttons["composite"].isEnabled()

    for key in ("ge2", "ge3", "ge4", "composite", "ge1"):
        hp._toolbar.set_current(key)
        app.processEvents()
        assert hp._toolbar.current() == key
        assert hp._active_card is hp._cards[key]


def test_hydra_composite_builds_with_matched_calibration(app, fixture_available):
    tab = DataViewerTab()
    tab._mode_ribbon.set_mode("hydra")
    hp = tab._hydra_page
    hp._loader.set_path(str(fixture_available / "ge1" / "panel.ge1.h5"))
    app.processEvents()

    for n in (1, 2, 3, 4):
        fields = geometry_fields_from_file(str(fixture_available / f"ge{n}" / f"ps_ge{n}.txt"))
        hp._cards[f"ge{n}"].set_geometry(fields)
    app.processEvents()

    hp._toolbar.set_current("composite")
    app.processEvents()
    assert hp._composite_img is not None
    assert hp._composite_img.shape == (512, 512)
    # composite card auto-seeded at the canvas centre
    assert hp._cards["composite"]._bcy.value() == pytest.approx(256.0)
    assert hp._cards["composite"]._bcz.value() == pytest.approx(256.0)


def test_hydra_state_round_trips(app, fixture_available):
    tab = DataViewerTab()
    tab._mode_ribbon.set_mode("hydra")
    hp = tab._hydra_page
    hp._loader.set_path(str(fixture_available / "ge1" / "panel.ge1.h5"))
    app.processEvents()
    hp._toolbar.set_current("ge3")
    app.processEvents()

    state = tab.get_state()
    assert state["hydra"]["active_mode"] == "hydra"
    assert state["hydra"]["page"]["active_panel"] == "ge3"

    tab2 = DataViewerTab()
    tab2.set_state(state)
    app.processEvents()
    assert tab2._mode_ribbon.mode() == "hydra"
    assert tab2._hydra_page._toolbar.current() == "ge3"
