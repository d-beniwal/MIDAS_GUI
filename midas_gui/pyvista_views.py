"""PyVista/VTK rendering widget for Solve Cell's reciprocal-space view (see
.context/DECISIONS.md 2026-10-23) -- a GPU-backed replacement for the
matplotlib ``Axes3D`` version used there previously. The Detector view was
also ported to PyVista for a time, then reverted back to the shared
pyqtgraph-based ``widgets.ImageViewer`` (see DECISIONS) once it proved the
native version looked worse in practice; this module now only covers the
reciprocal-space scatter.

An earlier attempt used VisPy instead; its Qt-embedded canvas segfaults
under this project's ``QT_QPA_PLATFORM=offscreen`` test convention (see the
deleted ``vispy`` branch). ``pyvistaqt.QtInteractor`` does not have that
problem: every scene-building/picking/camera operation this widget needs
works under ``offscreen`` with no crash (see tests/test_pyvista_spike.py) --
only pulling an actual rendered pixel buffer off it raises a catchable
``RenderWindowUnavailable``, which this widget's own tests simply never do
(they assert on stored group/actor state, the same way the matplotlib
version's tests asserted on artist state).
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pyvista as pv
import vtk
from PyQt5 import QtCore, QtWidgets
from pyvistaqt import QtInteractor

_ORIGIN_MARKER_COLOR = "#ff3b3b"
_SELECTION_COLOR = "#ff00cc"
_FITTED_COLOR = "#e64980"
_DEFAULT_CMAP = "viridis"

SIZE_MODE_EQUAL = "equal"
SIZE_MODE_INTENSITY = "intensity"
_DEFAULT_POINT_SIZE = 14.0
# A spot at the brightest visible intensity renders at the full requested
# size; the faintest still renders at this fraction of it rather than
# shrinking to invisible -- "size relative to intensity" should make bright
# spots stand out, not make faint (but still real) ones disappear.
_MIN_INTENSITY_RADIUS_FRACTION = 0.35


class _PickableInteractor(QtInteractor):
    """``QtInteractor`` with left-click-to-select and hover-for-details on
    top of VTK's default trackball camera, via its own ``vtkPointPicker``.

    Mirrors the click/drag discrimination the matplotlib reciprocal view
    used (``_on_recip_press``/``_on_recip_release``: a left-button press and
    release within ``_DRAG_THRESHOLD_PX`` of each other counts as a click,
    anything else -- a drag used to rotate/pan the camera -- does not), just
    backed by VTK's own ray-cast picker instead of manual screen-space
    projection of every point on every draw.
    """

    spotClicked = QtCore.pyqtSignal(object)   # emits the picked spot_id, or None
    spotHovered = QtCore.pyqtSignal(object)

    _DRAG_THRESHOLD_PX = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self._picker = vtk.vtkPointPicker()
        self._picker.SetTolerance(0.02)
        self._press_pos: Optional[tuple] = None

    def pick_spot_id(self, x: int, y: int):
        """Nearest pickable point under widget-local pixel ``(x, y)`` that
        carries a ``"spot_id"`` point-data array, or ``None``. Qt's y grows
        downward; VTK's display coordinates grow upward from the bottom of
        the render window."""
        renderer = self.renderer
        height = self.size().height()
        if not self._picker.Pick(x, height - y, 0, renderer):
            return None
        dataset = self._picker.GetDataSet()
        if dataset is None:
            return None
        arr = dataset.GetPointData().GetArray("spot_id")
        point_id = self._picker.GetPointId()
        if arr is None or point_id < 0:
            return None
        return arr.GetValue(point_id)

    def mousePressEvent(self, event):
        self._press_pos = (event.x(), event.y()) if event.button() == QtCore.Qt.LeftButton else None
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        press, self._press_pos = self._press_pos, None
        super().mouseReleaseEvent(event)
        if press is None or event.button() != QtCore.Qt.LeftButton:
            return
        if np.hypot(event.x() - press[0], event.y() - press[1]) > self._DRAG_THRESHOLD_PX:
            return
        self.spotClicked.emit(self.pick_spot_id(event.x(), event.y()))

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        if event.buttons() == QtCore.Qt.NoButton:
            self.spotHovered.emit(self.pick_spot_id(event.x(), event.y()))


def _point_cloud(xyz, spot_ids=None) -> pv.PolyData:
    pts = np.column_stack([np.asarray(c, dtype=np.float64) for c in xyz])
    cloud = pv.PolyData(pts)
    if spot_ids is not None:
        cloud["spot_id"] = np.asarray(spot_ids)
    return cloud


class PyVistaScatter3DView(QtWidgets.QWidget):
    """3-D qx/qy/qz reciprocal-space scatter: one point-cloud actor per
    color group, a dedicated selection actor, an optional fitted-position
    overlay, and a native VTK scalar bar / legend -- replacing the
    matplotlib ``Axes3D`` version's ``ax.scatter``/colorbar-axes/
    ``ax.legend`` entirely. See ``_refresh_recip_view``/``_update_recip_plot``
    in ``tab_solve_cell.py`` for how groups are computed; this widget only
    renders them.
    """

    spotClicked = QtCore.pyqtSignal(object)
    spotHovered = QtCore.pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.interactor = _PickableInteractor(self)
        self.interactor.set_background("black")
        layout.addWidget(self.interactor, stretch=1)
        self.interactor.spotClicked.connect(self.spotClicked)
        self.interactor.spotHovered.connect(self.spotHovered)

        self.groups: list = []          # [(xyz, color, label, rows), ...] last drawn
        self._group_actors: list = []
        self.legend_entries: list = []
        self._selected_actor = None
        self._fitted_actors: list = []
        self._has_data = False
        self._point_size = _DEFAULT_POINT_SIZE
        self._size_mode = SIZE_MODE_EQUAL
        self._origin_actor = self.interactor.add_points(
            _point_cloud(([0.0], [0.0], [0.0])), color=_ORIGIN_MARKER_COLOR,
            point_size=18, render_points_as_spheres=False,
            name="solve_cell_recip_origin")

    # ── camera ──────────────────────────────────────────────────────
    @property
    def camera_position(self):
        return self.interactor.camera_position

    @camera_position.setter
    def camera_position(self, value):
        self.interactor.camera_position = value

    # ── groups ──────────────────────────────────────────────────────
    def set_groups(self, groups: list, *, point_size: Optional[float] = None,
                   size_mode: Optional[str] = None):
        """``groups`` is a list of ``(xyz, color, label, rows)`` -- ``xyz``
        an ``(x, y, z)`` tuple of 1-D arrays, ``color`` either a single hex
        string (uniform) or a per-point array of RGBA floats in 0..1
        (residual-coloring mode), ``rows`` the spot records used for
        picking (must each carry a stable ``spot_id``).

        ``point_size``/``size_mode`` (one of ``SIZE_MODE_EQUAL``/
        ``SIZE_MODE_INTENSITY``), if given, replace the widget's current
        settings (sticky across calls that omit them, e.g. a camera-only
        refresh). In ``SIZE_MODE_INTENSITY``, each row's ``"integrated"``
        field (``midas_defect.ingest.find_blobs_3d``'s per-spot summed
        intensity) scales that point's rendered size relative to the
        brightest spot across every group in this call -- not per group --
        so sizes stay comparable, e.g., between diamond/gasket and indexed
        populations. Rendered via ``vtkPointGaussianMapper`` (pyvista's
        ``style="points_gaussian"``) with a per-point ``"point_radius"``
        scale array, since plain ``vtkPoints`` rendering has only one
        point_size per actor; VTK's own point picker still resolves correctly
        off the underlying point coordinates regardless of mapper style.

        Camera is preserved across calls once data has been shown once;
        the very first non-empty call fits the camera to the data, exactly
        mirroring ``_recip_has_data`` in the matplotlib version.
        """
        if point_size is not None:
            self._point_size = float(point_size)
        if size_mode is not None:
            if size_mode not in (SIZE_MODE_EQUAL, SIZE_MODE_INTENSITY):
                raise ValueError(f"unknown spot size mode: {size_mode!r}")
            self._size_mode = size_mode

        camera = self.interactor.camera_position
        for actor in self._group_actors:
            self.interactor.remove_actor(actor)
        self._group_actors = []
        self.clear_scalar_bar()
        self.groups = list(groups)

        max_intensity = 0.0
        if self._size_mode == SIZE_MODE_INTENSITY:
            for _xyz, _color, _label, rows in self.groups:
                for r in (rows or []):
                    v = r.get("integrated")
                    if v is not None and np.isfinite(v):
                        max_intensity = max(max_intensity, float(v))

        legend_entries = []
        any_points = False
        for i, (xyz, color, label, rows) in enumerate(self.groups):
            x = np.asarray(xyz[0])
            if not len(x):
                continue
            any_points = True
            ids = [r["spot_id"] for r in rows] if rows else None
            cloud = _point_cloud(xyz, spot_ids=ids)
            color_arr = np.asarray(color) if not isinstance(color, str) else None
            if color_arr is not None and color_arr.ndim == 2:
                color_kwargs = dict(scalars=color_arr[:, :3], rgb=True)
                legend_color = "white"
            else:
                color_kwargs = dict(color=color)
                legend_color = color
            if self._size_mode == SIZE_MODE_INTENSITY and rows and max_intensity > 0:
                intensity = np.array([float(r.get("integrated") or 0.0) for r in rows])
                norm = np.clip(intensity / max_intensity, 0.0, 1.0)
                factor = _MIN_INTENSITY_RADIUS_FRACTION + (1.0 - _MIN_INTENSITY_RADIUS_FRACTION) * np.sqrt(norm)
                cloud["point_radius"] = self._point_size * factor
                actor = self.interactor.add_points(
                    cloud, style="points_gaussian", point_size=self._point_size,
                    render_points_as_spheres=True, name=f"solve_cell_recip_group_{i}",
                    **color_kwargs)
                actor.mapper.scale_array = "point_radius"
            else:
                actor = self.interactor.add_points(
                    cloud, point_size=self._point_size, render_points_as_spheres=True,
                    name=f"solve_cell_recip_group_{i}", **color_kwargs)
            self._group_actors.append(actor)
            if label:
                legend_entries.append((label, legend_color))

        self.legend_entries = legend_entries
        if legend_entries:
            self.interactor.add_legend(legend_entries, bcolor=None, size=(0.2, 0.2))
        else:
            self.interactor.remove_legend()
        if any_points and not self._has_data:
            self.interactor.reset_camera()
            self._has_data = True
        elif self._has_data:
            self.interactor.camera_position = camera
        self.interactor.render()

    def set_scalar_bar(self, title: str, vmin: float, vmax: float, cmap: str = _DEFAULT_CMAP):
        """A standalone scalar bar for the q-residual coloring mode, keyed
        to the same range used to resolve the per-point colors passed to
        ``set_groups`` -- independent of which actor/mapper it's attached
        to, since those colors are pre-resolved RGBA, not VTK scalar-mapped."""
        self.clear_scalar_bar()
        lut = pv.LookupTable(cmap=cmap, n_values=256)
        lut.SetRange(vmin, vmax)
        # VTK's scalar-bar title/label text defaults to black, invisible
        # against this widget's black background -- explicit white avoids
        # a colorbar that LOOKS present (the gradient strip renders fine)
        # but is actually unreadable (no title, no tick values).
        self.interactor.add_scalar_bar(title=title, mapper=_LutMapper(lut), color="white")

    def clear_scalar_bar(self):
        if self.interactor.scalar_bars.keys():
            self.interactor.remove_scalar_bar()

    # ── selection ───────────────────────────────────────────────────
    def set_selected(self, row: Optional[dict]):
        """Swap only the selection actor -- never rebuilds the groups,
        camera, legend or scalar bar."""
        if self._selected_actor is not None:
            self.interactor.remove_actor(self._selected_actor)
            self._selected_actor = None
        if row is not None:
            xyz = ([row["qsample_x"]], [row["qsample_y"]], [row["qsample_z"]])
            # Always noticeably bigger than the spot it rings, however large
            # the user has turned the base spot size up.
            self._selected_actor = self.interactor.add_points(
                _point_cloud(xyz), color=_SELECTION_COLOR,
                point_size=max(22.0, self._point_size + 8.0),
                render_points_as_spheres=False,
                name="solve_cell_recip_selected")
        self.interactor.render()

    # ── fitted-position overlay ─────────────────────────────────────
    def set_fitted_overlay(self, observed: Optional[np.ndarray], predicted: Optional[np.ndarray]):
        for actor in self._fitted_actors:
            self.interactor.remove_actor(actor)
        self._fitted_actors = []
        if observed is None or predicted is None or not len(observed):
            self.interactor.render()
            return
        cross_cloud = pv.PolyData(predicted)
        self._fitted_actors.append(self.interactor.add_points(
            cross_cloud, color=_FITTED_COLOR, point_size=10, render_points_as_spheres=False,
            name="solve_cell_recip_fitted_points"))
        n = len(observed)
        points = np.empty((2 * n, 3), dtype=np.float64)
        points[0::2] = observed
        points[1::2] = predicted
        lines = pv.PolyData(points)
        lines.lines = np.column_stack(
            [np.full(n, 2), np.arange(0, 2 * n, 2), np.arange(1, 2 * n, 2)]).ravel()
        self._fitted_actors.append(self.interactor.add_mesh(
            lines, color=_FITTED_COLOR, line_width=1, name="solve_cell_recip_fitted_lines"))
        self.interactor.render()


class _LutMapper:
    """Minimal stand-in for a VTK mapper exposing just the ``lookup_table``
    attribute ``Plotter.add_scalar_bar`` reads -- lets a scalar bar be added
    from an explicit range/colormap without requiring an actual scalar-
    mapped mesh/actor to hang it off of."""

    def __init__(self, lookup_table):
        self.lookup_table = lookup_table
        self.scalar_range = lookup_table.GetRange()
