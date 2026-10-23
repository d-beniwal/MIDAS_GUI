"""Locks in the empirical findings behind the `pyvista` branch's renderer
choice for Solve Cell (see .context/DECISIONS.md 2026-10-23).

VisPy's Qt-embedded canvas segfaults under this project's
QT_QPA_PLATFORM=offscreen test convention (confirmed, not reproduced here --
that branch was deleted). PyVista/VTK does not: a `pyvistaqt.QtInteractor`
constructs and supports every scene-building/picking/camera operation the
new Solve Cell widgets need under `offscreen`, with no crash. Only pulling an
actual rendered pixel buffer off it (`.screenshot()`/`.image`) raises a
catchable `RenderWindowUnavailable` there -- `pv.Plotter(off_screen=True)`
(VTK's own off-screen render window, independent of Qt's platform plugin)
covers that case instead. Forked like the other Solve Cell UI tests: this
touches native Qt/VTK widgets (see test_solve_cell_ui.py's own docstring).
"""
import numpy as np
import pytest

pytestmark = pytest.mark.forked


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_qtinteractor_scene_building_works_offscreen(app):
    """Every operation the new widgets need -- construction, points, a
    scalar bar, a legend, camera get/set, point picking, actor removal --
    must not raise under the offscreen platform the whole test suite runs
    under (see tests/conftest.py)."""
    from PyQt5 import QtWidgets
    from pyvistaqt import QtInteractor

    w = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(w)
    plotter = QtInteractor(w)
    layout.addWidget(plotter)
    try:
        pts = np.random.rand(50, 3)
        actor = plotter.add_points(pts, color="red", render_points_as_spheres=True)
        plotter.add_points(pts + 2, scalars=np.random.rand(50), cmap="viridis")
        plotter.add_scalar_bar(title="q residual")
        plotter.add_legend([("group a", "red"), ("group b", "blue")])
        plotter.reset_camera()
        camera_position = plotter.camera_position
        plotter.camera_position = camera_position
        plotter.enable_point_picking(callback=lambda *a, **k: None, use_picker=True)
        plotter.remove_actor(actor)
    finally:
        plotter.close()


def test_qtinteractor_screenshot_raises_cleanly_offscreen(app):
    """The one operation that IS unsupported offscreen must fail as a
    catchable exception, not a crash -- this is the constraint the new
    widgets' tests design around (they verify state/actors, not pixels)."""
    from PyQt5 import QtWidgets
    from pyvistaqt import QtInteractor
    from pyvista.plotting.errors import RenderWindowUnavailable

    w = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(w)
    plotter = QtInteractor(w)
    layout.addWidget(plotter)
    try:
        plotter.add_points(np.random.rand(10, 3))
        plotter.reset_camera()
        with pytest.raises(RenderWindowUnavailable):
            plotter.screenshot(return_img=True)
    finally:
        plotter.close()


def test_off_screen_plotter_renders_real_pixels_offscreen(app):
    """VTK's own off-screen render window (no Qt widget at all) is the
    fallback for tests that do want genuine rendered-pixel verification."""
    import pyvista as pv

    pl = pv.Plotter(off_screen=True, window_size=(100, 100))
    try:
        pl.add_points(np.random.rand(50, 3), color="red", point_size=20)
        pl.reset_camera()
        img = pl.screenshot(return_img=True)
        assert img.shape == (100, 100, 3)
        assert (img[..., :3] != 255).any()
    finally:
        pl.close()
