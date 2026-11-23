"""Mask Builder eraser: the checkbox + size slider next to "Draw mask", the brush, the event
filter that drives it from the mouse, undo, display transforms, and the agent tools."""
from __future__ import annotations

import numpy as np
import pytest

tifffile = pytest.importorskip("tifffile")


@pytest.fixture(scope="module")
def qapp():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def win(qapp):
    import midas_gui.app as app_mod
    return app_mod.MainWindow()


@pytest.fixture
def tab(win, tmp_path, monkeypatch):
    from PyQt5 import QtWidgets
    t = win._mask_tab
    t.set_agent_mode(True)
    f = tmp_path / "img.tif"
    tifffile.imwrite(f, np.full((60, 80), 1000, np.int32))
    t._erase_check.setChecked(False)
    t.set_display_transform(())
    t._clear_shapes()
    t._img_edit.setText(str(f)); t._load_image()
    t._computed_mask = None; t._drawn_mask = None; t._mask = None; t._erase_undo.clear()
    t._agent_msgs.clear()
    yield t
    t._erase_check.setChecked(False)
    t.set_display_transform(())
    t._clear_shapes()


def _mask(t, rows, cols):
    m = np.zeros(t._image.shape, bool)
    m[rows, cols] = True
    t._set_mask(m)
    return m


def _row_of(layout, widget):
    """The (nested) layout that directly holds ``widget``."""
    if layout.indexOf(widget) >= 0:
        return layout
    for i in range(layout.count()):
        sub = layout.itemAt(i).layout()
        if sub is not None:
            found = _row_of(sub, widget)
            if found is not None:
                return found
    return None


def test_checkbox_sits_next_to_draw_mask_and_slider_defaults_to_two_pixels(tab):
    row = _row_of(tab.layout(), tab._draw_check) or _row_of(tab.layout().itemAt(1).widget().layout(), tab._draw_check)
    assert row is not None and row.indexOf(tab._erase_check) == row.indexOf(tab._draw_check) + 1
    assert tab._erase_check.text() == "Eraser"
    assert (tab._erase_slider.minimum(), tab._erase_slider.maximum(), tab._erase_slider.value()) == (1, 100, 2)
    assert tab._erase_size_lbl.text() == "2 px"
    assert not tab._erase_bar.isVisibleTo(tab)


def test_checking_opens_the_size_slider_and_the_brush_circle(tab):
    tab._erase_check.setChecked(True)
    assert tab._erase_bar.isVisibleTo(tab) and tab._erase_active
    ring = tab._erase_ring
    assert ring is not None and ring.rect().width() == 2.0          # diameter 2 px
    tab._erase_slider.setValue(11)
    assert tab._erase_size_lbl.text() == "11 px" and ring.rect().width() == 11.0
    tab._erase_check.setChecked(False)
    assert not tab._erase_bar.isVisibleTo(tab) and not tab._erase_active and not ring.isVisible()


def test_eraser_refuses_without_an_image(tab):
    tab._image = None
    tab._erase_check.setChecked(True)
    assert not tab._erase_check.isChecked()
    assert any("No image" in t for _k, t, _x in tab._agent_msgs)


def test_default_two_px_brush_footprint(tab):
    _mask(tab, slice(10, 20), slice(10, 20))
    tab._erase_slider.setValue(2)
    assert tab._erase_at(15.5, 15.5) == 1                           # cursor on a pixel centre: just that pixel
    assert not tab._computed_mask[15, 15] and tab._computed_mask.sum() == 99
    assert tab._erase_at(30.0, 30.0) == 0                           # off the mask: nothing to clear
    tab._erase_slider.setValue(2)
    assert tab._erase_at(13.0, 13.0) == 4                           # cursor on a pixel corner: the 2x2 block


def test_brush_is_a_disc_of_the_chosen_diameter(tab):
    m0 = _mask(tab, slice(0, 60), slice(0, 80))                     # everything masked
    tab._erase_slider.setValue(9)
    cleared = tab._erase_at(40.0, 30.0)
    yy, xx = np.mgrid[0:60, 0:80]
    expect = ((xx + .5 - 40.0) ** 2 + (yy + .5 - 30.0) ** 2) < 4.5 ** 2
    assert cleared == int(expect.sum())
    assert (tab._computed_mask == ~expect).all()
    assert (tab._mask.astype(bool) == ~expect).all()                # the displayed mask follows live


def test_both_layers_are_cleared_and_edges_clip(tab):
    m = np.zeros(tab._image.shape, bool); m[2:8, 2:8] = True
    tab._set_mask(m)
    tab._drawn_mask = m.copy(); tab._emit_final()
    tab._erase_slider.setValue(30)
    tab._erase_at(0.0, 0.0)                                         # brush hangs off the corner
    tab._erase_end_stroke()
    assert not tab._computed_mask[2:8, 2:8].any() and not tab._drawn_mask[2:8, 2:8].any()
    assert tab._mask.sum() == 0


def test_undo_restores_the_last_stroke_only_and_new_mask_clears_history(tab):
    m0 = _mask(tab, slice(0, 60), slice(0, 80))
    tab._erase_slider.setValue(5)
    tab._erase_at(20.0, 20.0); tab._erase_end_stroke()
    tab._erase_at(60.0, 40.0); tab._erase_end_stroke()
    assert len(tab._erase_undo) == 2 and tab._erase_undo_btn.isEnabled()
    after_two = tab._mask.copy()
    assert tab._undo_erase()
    assert tab._mask.sum() > after_two.sum() and not tab._mask[20, 20]       # first hole stays
    assert tab._mask[40, 60]                                                 # second hole filled
    assert tab._undo_erase() and (tab._mask.astype(bool) == m0).all()
    assert not tab._undo_erase() and not tab._erase_undo_btn.isEnabled()
    tab._erase_at(10.0, 10.0); tab._erase_end_stroke()
    tab._set_mask(m0)                                                        # rebuilding the mask ...
    assert tab._erase_undo == []                                             # ... drops stale undo


def test_erase_through_a_flipped_transposed_view_hits_the_raw_pixel(tab):
    from midas_gui.helpers import im_trans_map_point
    codes = (1, 3)
    tab.set_display_transform(codes)
    raw_r, raw_c = 12, 53
    _mask(tab, slice(0, 60), slice(0, 80))
    col, row = im_trans_map_point(raw_c, raw_r, tab._image.shape, codes)     # where it is painted
    tab._erase_slider.setValue(1)
    assert tab._erase_at(col + 0.5, row + 0.5) == 1
    holes = np.argwhere(~tab._computed_mask)
    assert holes.tolist() == [[raw_r, raw_c]]


def test_mouse_press_drag_release_erases_a_stroke_and_announces_once(win, tab, qapp):
    from PyQt5 import QtCore, QtGui, QtWidgets
    win.resize(1500, 900); win.show()
    win._tabs.setCurrentWidget(tab) if hasattr(win, "_tabs") else None
    for _ in range(30): qapp.processEvents()
    _mask(tab, slice(0, 60), slice(0, 80))
    tab._erase_slider.setValue(3)
    tab._erase_check.setChecked(True)
    announced = []
    tab.maskReady.connect(lambda m: announced.append(int(np.count_nonzero(m))))
    gv = tab._viewer._iv.ui.graphicsView
    vp = gv.viewport()
    img = tab._viewer._iv.getImageItem()

    def vp_pos(x, y):
        return gv.mapFromScene(img.mapToScene(QtCore.QPointF(x, y)))

    def send(kind, x, y, button, buttons):
        ev = QtGui.QMouseEvent(kind, QtCore.QPointF(vp_pos(x, y)), button, buttons, QtCore.Qt.NoModifier)
        QtWidgets.QApplication.sendEvent(vp, ev)
        return ev

    L, NO = QtCore.Qt.LeftButton, QtCore.Qt.NoButton
    send(QtCore.QEvent.MouseMove, 20.0, 30.0, NO, NO)                         # hover: ring follows, nothing erased
    assert tab._erase_ring.isVisible() and tab._computed_mask.all()
    e = send(QtCore.QEvent.MouseButtonPress, 20.0, 30.0, L, L)
    assert e.isAccepted()                                                     # consumed: the view must not pan
    for x in (22.0, 24.0, 26.0, 28.0):
        send(QtCore.QEvent.MouseMove, x, 30.0, NO, L)
    assert announced == []                                                    # not announced mid-drag
    send(QtCore.QEvent.MouseButtonRelease, 28.0, 30.0, L, NO)
    assert len(announced) == 1 and announced[0] == int(tab._mask.sum())
    holes = ~tab._computed_mask
    assert holes[29:31, 19:29].all() and holes.sum() < 120                    # a thin horizontal stroke
    assert len(tab._erase_undo) == 1
    # moving without the button, or any other button, never erases
    before = tab._computed_mask.copy()
    send(QtCore.QEvent.MouseMove, 60.0, 10.0, NO, NO)
    assert (tab._computed_mask == before).all()
    tab._erase_check.setChecked(False)
    send(QtCore.QEvent.MouseButtonPress, 60.0, 10.0, L, L)
    send(QtCore.QEvent.MouseButtonRelease, 60.0, 10.0, L, NO)
    assert (tab._computed_mask == before).all()                               # eraser off: no effect


def test_point_and_freeform_modes_switch_the_eraser_off(tab):
    tab._erase_check.setChecked(True)
    tab._point_btn.setChecked(True)
    assert not tab._erase_check.isChecked()
    tab._erase_check.setChecked(True)
    assert not tab._point_btn.isChecked()
    tab._freeform_btn.setChecked(True)
    assert not tab._erase_check.isChecked()
    tab._freeform_btn.setChecked(False)


# ── agent tools ────────────────────────────────────────────────────────────

@pytest.fixture
def ctrl(win, tab, monkeypatch):
    from PyQt5 import QtWidgets
    from midas_gui.control_api import GuiController
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", staticmethod(lambda *a, **k: (_ for _ in ()).throw(AssertionError("modal"))))
    return GuiController(win)


def test_mask_erase_tool_and_undo(ctrl, tab):
    m0 = _mask(tab, slice(0, 60), slice(0, 80))
    r = ctrl.dispatch("mask.erase", {"centers": [[10, 10], [40, 30]], "size": 5})
    assert r["ok"], r
    yy, xx = np.mgrid[0:60, 0:80]
    disc = lambda cx, cy: ((xx + .5 - cx) ** 2 + (yy + .5 - cy) ** 2) < 2.5 ** 2
    assert r["result"]["cleared"] == int((disc(10, 10) | disc(40, 30)).sum())   # two 5-px discs
    assert r["result"]["undo_depth"] == 1
    assert not tab._mask[10, 10] and not tab._mask[30, 40]
    u = ctrl.dispatch("mask.undo_erase", {})
    assert u["ok"] and (tab._mask.astype(bool) == m0).all()
    assert ctrl.dispatch("mask.undo_erase", {})["error"]["type"] == "precondition_failed"


def test_mask_erase_tool_errors(ctrl, tab):
    assert ctrl.dispatch("mask.erase", {"centers": [[1, 1]]})["error"]["type"] == "precondition_failed"   # no mask
    _mask(tab, slice(0, 5), slice(0, 5))
    assert ctrl.dispatch("mask.erase", {"centers": []})["error"]["type"] == "bad_args"
    assert ctrl.dispatch("mask.erase", {"centers": [[1, 1]], "size": 0})["error"]["type"] == "bad_args"
    assert ctrl.dispatch("mask.erase", {"centers": "oops"})["error"]["type"] == "bad_args"


def test_set_eraser_tool_drives_the_widgets(ctrl, tab):
    r = ctrl.dispatch("mask.set_eraser", {"enabled": True, "size": 7})
    assert r["ok"] and r["result"]["eraser"] == {"enabled": True, "size": 7, "undo_depth": 0}
    assert tab._erase_check.isChecked() and tab._erase_slider.value() == 7
    assert ctrl.dispatch("mask.set_eraser", {"size": 500})["error"]["type"] == "bad_args"
    ctrl.dispatch("mask.set_eraser", {"enabled": False, "size": 2})
    assert "eraser" in ctrl.dispatch("mask.get_state", {})["result"]
