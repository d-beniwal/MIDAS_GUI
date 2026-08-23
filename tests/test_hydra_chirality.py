"""Permanent regression test for the Hydra composite's chirality/orientation.

The reference project this compositing math was ported from (a sibling
project's own JSP-fork viewer) needed an explicit X-mirror to display its
Hydra composite correctly, because of how its custom image viewer's origin
convention interacted with the compositing math. This codebase's viewers
only ever apply `invertY(False)` (see the 2026-08-23 origin-flip decision in
.context/DECISIONS.md) — whether an analogous mirror is needed here was an
open question, resolved by manual verification (see the 2026-08-23 "Hydra
composite needs no chirality/X-mirror correction" entry in DECISIONS.md).

This test is the permanent, automated guard for that conclusion: it renders
the synthetic marker fixture through the REAL display pipeline (an actual
ROIImageViewer, via widget.grab() — not just a raw numpy array check, which
can't prove on-screen direction) and confirms each panel's marker appears
where an independently hand-derived rotation predicts, not at the position
a sign-flipped ("wrong chirality") rotation would predict. The fixture's tx
values are deliberately not exact multiples of 90 deg (see
make_hydra_test_data.py) so a sign error can't alias one panel's correct
marker onto another panel's, which would let a real bug slip past silently.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
from PyQt5 import QtCore, QtWidgets

from midas_gui import hydra
from midas_gui.helpers import geometry_fields_from_file
from midas_gui.roi_tools import ROIImageViewer

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "test_data" / "gui_synthetic" / "hydra"

# Local raw-pixel marker centre used by make_hydra_test_data.py, in every panel.
_MARKER_ROW, _MARKER_COL = 128.0, 188.0
_SEPARATION_MIN_PX = 20.0   # correct vs. wrong-sign predicted positions must
                            # differ by at least this much (guards against a
                            # near-degenerate fixture silently passing)


def _expected_xy(bc_y, bc_z, tx_deg, px, big_det_size, y_pix0, z_pix0):
    """Hand-derived analytic inverse of hydra.compute_inv_coords's rotation
    (typed out independently here, not calling that function) — see
    tests/test_hydra_geometry.py for the same derivation."""
    half = big_det_size * 0.5
    tx = math.radians(tx_deg)
    c, s = math.cos(tx), math.sin(tx)
    Y_det = px * (bc_y - y_pix0)
    Z_det = px * (z_pix0 - bc_z)
    Y_lab = Y_det * c - Z_det * s
    Z_lab = Y_det * s + Z_det * c
    return Y_lab / px + half, Z_lab / px + half


@pytest.fixture(scope="module")
def loaded_states():
    if not FIXTURE_DIR.exists():
        pytest.skip("test_data/gui_synthetic/hydra/ fixture not present — "
                    "run make_hydra_test_data.py")
    states = {}
    for n in (1, 2, 3, 4):
        st = hydra.DetectorState()
        fields = geometry_fields_from_file(str(FIXTURE_DIR / f"ge{n}" / f"ps_ge{n}.txt"))
        st.load_from_geometry_dict(fields)
        st.data_file = str(FIXTURE_DIR / f"ge{n}" / f"panel.ge{n}.h5")
        states[n] = st
    return states


def _sample_pixel(viewer, pixmap_image, x_data: float, y_data: float):
    """Grayscale value (0-255) at data-space (x_data, y_data) in the
    rendered pixmap, via the same public mapSceneToView/mapFromScene family
    of APIs the app's own mouse-click handling already uses (see
    widgets.py::ImageViewer._mouse) — just run in reverse (data -> screen)."""
    vb = viewer._iv.getView().getViewBox()
    gview = vb.scene().views()[0]
    scene_pt = vb.mapViewToScene(QtCore.QPointF(x_data, y_data))
    widget_pt = gview.mapFromScene(scene_pt)
    top_pt = gview.mapTo(viewer, widget_pt)
    px, py = top_pt.x(), top_pt.y()
    if not (0 <= px < pixmap_image.width() and 0 <= py < pixmap_image.height()):
        return None
    return pixmap_image.pixelColor(px, py).value()


def test_composite_markers_render_at_correct_chirality(loaded_states):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    states = loaded_states
    siblings = {n: st.data_file for n, st in states.items()}
    comp, size = hydra.build_windmill_composite(siblings, 0, "exchange/data", states, op="max")

    viewer = ROIImageViewer()
    viewer.resize(700, 700)
    # Full linear range (not the default Log + 30/99 percentile levels) —
    # the marker covers <1% of the frame, so percentile-based auto-levels
    # would leave it indistinguishable from the zero background.
    viewer._log.setChecked(False)
    viewer._vmin.setValue(0)
    viewer._vmax.setValue(100)
    viewer.set_image(comp.astype(np.float32), autorange=True)
    viewer.show()
    for _ in range(10):
        app.processEvents()
    pixmap_image = viewer.grab().toImage()

    for n, st in states.items():
        cx, cy = _expected_xy(st.bc_y, st.bc_z, st.tx, st.px, size,
                              _MARKER_COL, _MARKER_ROW)
        wx, wy = _expected_xy(st.bc_y, st.bc_z, -st.tx, st.px, size,
                              _MARKER_COL, _MARKER_ROW)
        assert math.hypot(cx - wx, cy - wy) >= _SEPARATION_MIN_PX, (
            f"panel {n}: correct/wrong-sign predictions too close to "
            "discriminate — fixture tx values need adjusting")
        correct_val = _sample_pixel(viewer, pixmap_image, cx, cy)
        wrong_val = _sample_pixel(viewer, pixmap_image, wx, wy)
        assert correct_val is not None and correct_val > 200, (
            f"panel {n}: marker not found at the correctly-rotated position "
            f"({cx:.1f}, {cy:.1f}) — brightness={correct_val}")
        assert wrong_val is None or wrong_val < 100, (
            f"panel {n}: marker found at the WRONG-chirality position "
            f"({wx:.1f}, {wy:.1f}) instead of the correct one — a sign "
            f"error in the composite rotation (brightness={wrong_val})")
