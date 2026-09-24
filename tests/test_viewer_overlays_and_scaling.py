"""Data Viewer overlay + axis presentation: the cake's axes, ring arcs clipped
to the detector, hkl labels on the profile's marker lines, and the pixel-based
font sizing that keeps overlay text in proportion at any interface scale.

Not fork-isolated, for the same reason as ``test_view_bottom_tabs.py``: a
forked child dies with SIGSEGV once a ``DataViewerTab``-scale widget tree is
built. Run plain, these pass — see ``.context/STATE.md``.
"""
from __future__ import annotations

import numpy as np
import pytest
import pyqtgraph as pg
from PyQt5 import QtWidgets

from midas_gui import style as S
from midas_gui.hydra_geometry_card import (DetectorGeometryCard, _ring_label_pos,
                                           _ring_on_image_mask)
from midas_gui.widgets import (CakeViewer, ETA_VIEW_LIMIT_DEG, IntensityStatsPanel,
                               PickableImageViewer, ProfileViewer)


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


# ═══════════════════════════════════════════════════════════════════════
#  Eta-vs-R cake axes
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def cake(app):
    cv = CakeViewer()
    cv.resize(600, 400)
    cv.show()
    return cv


def _fill(cv, r_max=2400.0, n_r=300, n_eta=72):
    r = np.linspace(0.5, r_max, n_r)
    e = np.linspace(-180.0, 180.0, n_eta)
    cv.set_cake(np.random.rand(n_eta, n_r).astype(np.float32), r, e)
    return r, e


def test_cake_axes_show_raw_px_and_degrees_not_si_prefixes(cake, app):
    """A 2400-px R axis was relabelled "kpx" by pyqtgraph's automatic SI
    prefixing, which is meaningless for detector pixels."""
    _fill(cake, r_max=2400.0)
    app.processEvents()
    for axis, unit in (("bottom", "px"), ("left", "°")):
        ax = cake._iv.getView().getAxis(axis)
        assert ax.autoSIPrefix is False
        assert "k" + unit not in ax.labelString()
        assert unit in ax.labelString()


def test_cake_eta_axis_is_bounded_to_one_turn(cake, app):
    """η spans a full turn by construction, so the pan/zoom bound is the fixed
    ±185° rather than a fraction of whatever the cake happened to cover."""
    _fill(cake)
    app.processEvents()
    lim = cake._iv.getView().getViewBox().state["limits"]
    assert tuple(lim["yLimits"]) == (-ETA_VIEW_LIMIT_DEG, ETA_VIEW_LIMIT_DEG)


def test_cake_eta_bound_never_clips_data_that_ran_past_a_turn(cake, app):
    """The fixed bound is a floor, not a cap — a cake somehow wider than ±185°
    is still fully reachable."""
    r = np.linspace(0.5, 100.0, 20)
    e = np.linspace(-200.0, 200.0, 40)
    cake.set_cake(np.random.rand(40, 20).astype(np.float32), r, e)
    app.processEvents()
    lo, hi = cake._iv.getView().getViewBox().state["limits"]["yLimits"]
    assert lo <= -200.0 and hi >= 200.0


def test_cake_r_axis_is_pinned_the_same_way_the_radial_profile_is(cake, app):
    """Both plots put R on X, stacked in the same bottom-tab strip, so they
    bound it identically: ProfileViewer's 15% margin, clamped at zero."""
    r, _ = _fill(cake, r_max=2400.0)
    app.processEvents()
    r0, r1 = float(r[0]), float(r[-1])
    pad = 0.15 * (r1 - r0)
    xlo, xhi = cake._iv.getView().getViewBox().state["limits"]["xLimits"]
    assert xlo == pytest.approx(max(0.0, r0 - pad))
    assert xhi == pytest.approx(r1 + pad)
    # ...and it opens on the data, not zoomed out to the padded bound.
    (vx0, vx1), _ = cake._iv.getView().getViewBox().viewRange()
    assert vx0 <= r0 and vx1 >= r1                 # nothing cut off
    assert (vx1 - vx0) <= 1.15 * (r1 - r0)         # nor a screenful of void


# ═══════════════════════════════════════════════════════════════════════
#  Ring arcs and hkl labels stay on the detector
# ═══════════════════════════════════════════════════════════════════════

def test_a_ring_that_misses_the_detector_gets_no_label():
    th = np.linspace(0, 2 * np.pi, 400)
    ys, zs = 129.0 + 9000 * np.cos(th), 124.0 + 9000 * np.sin(th)
    assert _ring_label_pos(ys, zs, (512, 3072)) is None


def test_label_box_folds_inward_when_the_arc_tops_out_at_the_edge():
    """Anchor (0.5, 1.0) grows the box upward off the anchor point; at the top
    edge that puts it over the empty canvas, so it flips to hang downward."""
    th = np.linspace(0, 2 * np.pi, 400)
    shape = (512, 3072)
    # Arc peaks well inside the frame: box has room above it.
    ys, zs = 1500.0 + 40 * np.cos(th), 250.0 + 40 * np.sin(th)
    _y, _z, anchor = _ring_label_pos(ys, zs, shape, box_w=30, box_h=16)
    assert anchor == (0.5, 1.0)
    # Arc peaks past the top edge: box must fold back over the arc.
    ys, zs = 1500.0 + 40 * np.cos(th), 500.0 + 40 * np.sin(th)
    _y, z, anchor = _ring_label_pos(ys, zs, shape, box_w=30, box_h=16)
    assert anchor == (0.5, 0.0)
    assert z <= shape[0] - 1


def test_label_box_slides_off_the_left_and_right_borders():
    th = np.linspace(0, 2 * np.pi, 400)
    shape = (512, 300)
    ys, zs = 5.0 + 30 * np.cos(th), 100.0 + 30 * np.sin(th)
    assert _ring_label_pos(ys, zs, shape, box_w=80, box_h=16)[2][0] == 0.0
    ys, zs = 295.0 + 30 * np.cos(th), 100.0 + 30 * np.sin(th)
    assert _ring_label_pos(ys, zs, shape, box_w=80, box_h=16)[2][0] == 1.0


def test_anchor_flips_with_an_inverted_display_but_the_position_does_not():
    """Anchors are resolved in screen space, so a top-left display origin
    (inverted Y) needs the opposite one to keep the box on the same side."""
    th = np.linspace(0, 2 * np.pi, 400)
    ys, zs = 1500.0 + 40 * np.cos(th), 250.0 + 40 * np.sin(th)
    up = _ring_label_pos(ys, zs, (512, 3072), 30, 16, y_up=True)
    down = _ring_label_pos(ys, zs, (512, 3072), 30, 16, y_up=False)
    assert up[:2] == down[:2]
    assert up[2] == (0.5, 1.0) and down[2] == (0.5, 0.0)


@pytest.fixture
def card_with_corner_bc(app):
    """A geometry card whose beam centre sits in a corner, so most of every
    simulated ring falls outside the frame."""
    img = np.zeros((300, 400), dtype=np.float32)
    viewer = PickableImageViewer()
    viewer.resize(600, 500)
    viewer.show()
    viewer.set_raw_frame(img, [])
    card = DetectorGeometryCard()
    card.set_viewer(viewer)
    card.set_image_source(lambda: img, None)
    card._bc_auto.setChecked(False)
    card._bcy.setValue(20.0)
    card._bcz.setValue(20.0)
    card._sim_btn.click()
    app.processEvents()
    return card, img


def test_ring_arcs_are_clipped_to_the_detector(card_with_corner_bc):
    card, img = card_with_corner_bc
    nz, ny = img.shape
    arcs = [it for it in card._ring_items if isinstance(it, pg.PlotDataItem)]
    assert arcs, "no rings were drawn at all"
    for arc in arcs:
        x, y = arc.getData()
        fin = np.isfinite(x) & np.isfinite(y)
        assert fin.any(), "an arc was added with nothing visible on it"
        assert (x[fin] >= 0).all() and (x[fin] <= ny - 1).all()
        assert (y[fin] >= 0).all() and (y[fin] <= nz - 1).all()
        # NaN gaps only mean anything if the polyline breaks at them.
        assert arc.opts["connect"] == "finite"


def test_hkl_labels_are_all_on_the_detector(card_with_corner_bc):
    card, img = card_with_corner_bc
    nz, ny = img.shape
    assert card._label_items
    for txt in card._label_items:
        assert 0 <= txt.pos().x() <= ny - 1
        assert 0 <= txt.pos().y() <= nz - 1


def test_the_beam_centre_marker_is_exempt_from_the_clipping(app):
    """Everything else is confined to the image; where the beam centre lands
    is worth seeing even when that is off the frame."""
    img = np.zeros((100, 100), dtype=np.float32)
    viewer = PickableImageViewer()
    viewer.resize(400, 400)
    viewer.show()
    viewer.set_raw_frame(img, [])
    card = DetectorGeometryCard()
    card.set_viewer(viewer)
    card.set_image_source(lambda: img, None)
    card._bc_auto.setChecked(False)
    card._bcy.setValue(-250.0)
    card._bcz.setValue(-250.0)
    card._sim_btn.click()
    app.processEvents()
    markers = [it for it in card._ring_items if isinstance(it, pg.ScatterPlotItem)]
    assert len(markers) == 1
    pt = markers[0].getData()
    assert float(pt[0][0]) == -250.0 and float(pt[1][0]) == -250.0


def test_on_image_mask_is_inclusive_of_the_last_row_and_column():
    ys = np.array([-0.5, 0.0, 99.0, 99.5])
    zs = np.array([50.0, 50.0, 50.0, 50.0])
    assert list(_ring_on_image_mask(ys, zs, (100, 100))) == [False, True, True, False]


# ═══════════════════════════════════════════════════════════════════════
#  hkl labels on the radial profile's ring markers
# ═══════════════════════════════════════════════════════════════════════

@pytest.fixture
def profile(app):
    pv = ProfileViewer()
    pv.resize(700, 350)
    pv.show()
    pv.set_profile(np.arange(500.0), np.random.rand(500) * 100.0,
                   wavelength_A=0.17, lsd_um=1e6, px_um=200.0)
    return pv


def test_ring_markers_carry_their_hkl_written_along_the_line(profile, app):
    profile.set_ring_markers(
        [{"radii": [100.0, 220.0], "labels": ["111", "200"], "color": "#f0c060"}],
        1e6, 200.0, 0.17)
    app.processEvents()
    assert len(profile._ring_lines) == 2
    assert [ln.label.toPlainText() for ln in profile._ring_lines] == ["111", "200"]
    for ln in profile._ring_lines:
        # rotateAxis along the line turns the text vertical: taller than wide.
        assert tuple(ln.label.rotateAxis) == (1.0, 0.0)
        box = ln.label.mapRectToScene(ln.label.boundingRect())
        assert box.height() > box.width()


def test_rotated_labels_stay_inside_the_plot_area(profile, app):
    profile.set_ring_markers(
        [{"radii": [100.0], "labels": ["10 10 10"], "color": "#f0c060"}],
        1e6, 200.0, 0.17)
    app.processEvents()
    vb = profile._plot.getPlotItem().getViewBox()
    area = vb.mapRectToScene(vb.boundingRect())
    box = profile._ring_lines[0].label.mapRectToScene(
        profile._ring_lines[0].label.boundingRect())
    assert box.top() >= area.top() and box.bottom() <= area.bottom()


def test_a_group_without_labels_still_draws_bare_lines(profile, app):
    """Calibrate feeds radii with no hkl; those markers must not change."""
    profile.set_ring_markers([{"radii": [80.0, 160.0], "color": "#4fc3f7"}],
                             1e6, 200.0, 0.17)
    app.processEvents()
    assert len(profile._ring_lines) == 2
    assert all(getattr(ln, "label", None) is None for ln in profile._ring_lines)


def test_a_short_label_list_labels_what_it_can(profile, app):
    profile.set_ring_markers(
        [{"radii": [80.0, 160.0], "labels": ["111"], "color": "#4fc3f7"}],
        1e6, 200.0, 0.17)
    app.processEvents()
    labelled = [getattr(ln, "label", None) for ln in profile._ring_lines]
    assert labelled[0] is not None and labelled[0].toPlainText() == "111"
    assert labelled[1] is None


# ═══════════════════════════════════════════════════════════════════════
#  Font sizing: px off the app's base font, never points
# ═══════════════════════════════════════════════════════════════════════

def test_font_px_is_sized_in_pixels_off_the_app_base(app):
    """Point sizes go through a logical DPI that QT_SCALE_FACTOR has already
    moved, so they scale twice; pixels scale exactly once, like every widget."""
    assert S.font_px().pixelSize() == S.BASE_FONT_PX
    assert S.font_px(1.5).pixelSize() == round(S.BASE_FONT_PX * 1.5)
    assert S.font_px().pointSize() == -1        # explicitly not a point size
    assert S.font_px(bold=True).bold()
    assert S.font_px(0.01).pixelSize() >= 1     # never collapses to zero


def test_axis_label_css_is_px_not_pt(app):
    css = S.axis_label_css("#d0d0d0")
    assert css["color"] == "#d0d0d0"
    assert css["font-size"] == f"{S.BASE_FONT_PX}px"
    assert "pt" not in css["font-size"]


def test_statistics_axis_titles_match_the_app_font(app):
    """Reported as axis titles that ballooned out of proportion at ui_scale
    1.5 on a 4K display, unlike every other label around them."""
    panel = IntensityStatsPanel()
    panel.resize(500, 200)
    panel.show()
    app.processEvents()
    for axis in ("bottom", "left"):
        html = panel._plot.getAxis(axis).labelString()
        assert f"font-size: {S.BASE_FONT_PX}px" in html
        assert "pt" not in html


def test_roi_labels_are_sized_off_the_app_font(app):
    """A default-constructed QFont reports the *system* point size, not this
    app's 12 px, so the old "base * 1.2" was ~1.6x too big before any
    interface scaling was applied on top."""
    from midas_gui.roi_tools import _make_roi_text_item
    item = _make_roi_text_item("ROI 1", "#f0c060")
    assert item.textItem.font().pixelSize() == round(S.BASE_FONT_PX * 1.2)


def test_overlay_fonts_track_the_base_size(app, monkeypatch):
    """The one knob: change the base and every derived overlay font follows."""
    monkeypatch.setattr(S, "BASE_FONT_PX", 20)
    assert S.font_px().pixelSize() == 20
    assert S.font_px(1.2).pixelSize() == 24
    assert S.axis_label_css("#fff")["font-size"] == "20px"


def test_compass_labels_use_a_pixel_font(app):
    """The lab-frame overlay lives in a QGraphicsScene too — same rule."""
    from midas_gui.widgets import ImageViewer, build_lab_frame_axes_items
    viewer = ImageViewer()
    viewer.resize(400, 400)
    viewer.show()
    viewer.set_raw_frame(np.zeros((100, 100), dtype=np.float32), [])
    app.processEvents()
    texts = [it for it in build_lab_frame_axes_items(viewer._iv, (100, 100), 50.0, 50.0)
             if isinstance(it, pg.TextItem)]
    assert texts
    for t in texts:
        assert t.textItem.font().pixelSize() > 0
        assert t.textItem.font().pointSize() == -1


def test_no_pt_sized_axis_titles_anywhere():
    """Regression guard. A ``font-size: Npt`` in a pyqtgraph axis title is the
    exact defect reported here, and it is invisible until someone runs at a
    non-1.0 interface scale — so no file may reintroduce one.

    Deliberately *not* covered: ``_mono_font``'s ``setPointSizeF`` and the
    stacked-profile viewer's user-facing font-size spinbox. Those are point
    sizes on purpose, with call sites calibrated to them; converting them is a
    separate change, not this one.
    """
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parent.parent / "midas_gui"
    pattern = re.compile(r"""font-size['"]?\s*:\s*['"]?\d+(\.\d+)?\s*pt""")
    offenders = []
    for path in sorted(root.glob("*.py")):
        if path.name == "style.py":
            continue                      # its module docstring explains the rule
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.name}:{i}: {line.strip()}")
    assert not offenders, ("pt-sized text found; use style.axis_label_css / "
                           "style.font_px instead:\n" + "\n".join(offenders))
