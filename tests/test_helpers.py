"""Unit tests for lightweight, profile-scoped widget helpers in helpers.py.

Covers the pieces added to fix "profile switch doesn't refresh option lists":
refresh_combo_items (used by the Calibrant dropdown) and the pixel-size /
K-edge-foil popup menus rebuilding their entries from live constants each
time they're opened, instead of freezing them at construction.
"""
import numpy as np
import pytest


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_refresh_combo_items_preserves_existing_selection(app):
    from midas_gui.helpers import _NoScrollComboBox, refresh_combo_items

    combo = _NoScrollComboBox()
    combo.addItems(["A", "B", "C"])
    combo.setCurrentText("B")

    refresh_combo_items(combo, ["X", "B", "Y"])

    assert [combo.itemText(i) for i in range(combo.count())] == ["X", "B", "Y"]
    assert combo.currentText() == "B"


def test_refresh_combo_items_falls_back_when_selection_gone(app):
    from midas_gui.helpers import _NoScrollComboBox, refresh_combo_items

    combo = _NoScrollComboBox()
    combo.addItems(["A", "B"])
    combo.setCurrentText("B")

    refresh_combo_items(combo, ["X", "Y"])

    assert combo.currentText() == "X"


def test_pixel_label_menu_rebuilds_from_current_constants(app, monkeypatch):
    """make_pixel_label's popup menu must reflect constants.PIXEL_PRESETS as
    of when it's opened, not as of when the label was constructed — this is
    what makes it survive a profile switch with no extra wiring."""
    import midas_gui.constants as C
    from midas_gui.helpers import make_pixel_label, _fspin

    monkeypatch.setattr(C, "PIXEL_PRESETS", [("Before", 100.0)])
    px_spin = _fspin(1.0, 1000.0, 3, 50.0)
    btn = make_pixel_label(px_spin)
    assert [a.text() for a in btn.menu().actions()] == ["Before  (100 µm)"]

    monkeypatch.setattr(C, "PIXEL_PRESETS", [("After1", 75.0), ("After2", 150.0)])
    btn.menu().aboutToShow.emit()
    labels = [a.text() for a in btn.menu().actions()]
    assert labels == ["After1  (75 µm)", "After2  (150 µm)"]

    btn.menu().actions()[1].trigger()
    assert px_spin.value() == 150.0


def test_kedge_label_menu_rebuilds_from_current_constants(app, monkeypatch):
    import midas_gui.constants as C
    from midas_gui.helpers import make_kedge_label, _fspin

    monkeypatch.setattr(C, "K_EDGE_FOILS", [("Fe", 7.11)])
    wl_spin = _fspin(0.01, 5.0, 5, 0.2)
    btn = make_kedge_label(wl_spin)
    foil_labels_before = [a.text() for a in btn.menu().actions()
                          if a.text() and not a.isSeparator()]
    assert len(foil_labels_before) == 1
    assert foil_labels_before[0].startswith("Fe")

    monkeypatch.setattr(C, "K_EDGE_FOILS", [("Cu", 8.98), ("Ni", 8.33)])
    btn.menu().aboutToShow.emit()
    foil_labels_after = [a.text() for a in btn.menu().actions()
                         if a.text() and not a.isSeparator()]
    assert len(foil_labels_after) == 2
    assert foil_labels_after[0].startswith("Cu")
    assert foil_labels_after[1].startswith("Ni")


# ── detect_geometry_from_path (auto pxY/wavelength_A on load) ─────────────────
# Detector-from-filename and the HDF5 energy-metadata location are specific to
# the APS 1-ID-E / 20-ID-D / 20-ID-E beamlines, so every case below pins an
# explicit `profile=` rather than depending on whatever profile is active on
# the machine running the tests.

@pytest.mark.parametrize("name,expected", [
    ("scan.ge1", "ge"), ("scan.GE3.h5", "ge"), ("scan_ge4_001.ge4", "ge"),
    ("scan.vrx", "vrx"), ("scan.VRX.h5", "vrx"),
    ("scan.pxrd", "pxrd"),
    ("scan.tif", None), ("scan.h5", None),
])
def test_detect_detector_from_filename(name, expected):
    from midas_gui.helpers import detect_detector_from_filename
    assert detect_detector_from_filename(name) == expected


def test_detect_geometry_gated_to_known_beamline_profiles():
    from midas_gui.helpers import detect_geometry_from_path

    assert detect_geometry_from_path("scan.ge2", profile="Default") == {}
    assert detect_geometry_from_path("scan.ge2", profile="Some Other Profile") == {}


@pytest.mark.parametrize("profile", ["1-ID-E", "20-ID-D", "20-ID-E"])
def test_detect_geometry_pixel_size_from_filename(profile):
    from midas_gui.helpers import detect_geometry_from_path

    assert detect_geometry_from_path("scan.ge1", profile=profile) == {"pxY": 200.0}
    assert detect_geometry_from_path("scan.vrx", profile=profile) == {"pxY": 150.0}
    # Pixirad is identified but has no known pixel size to auto-populate.
    assert detect_geometry_from_path("scan.pxrd", profile=profile) == {}
    assert detect_geometry_from_path("scan.tif", profile=profile) == {}


def test_detect_geometry_wavelength_from_h5_energy_metadata(tmp_path):
    h5py = pytest.importorskip("h5py")
    from midas_gui import constants as C
    from midas_gui.helpers import detect_geometry_from_path

    path = tmp_path / "scan.h5"
    with h5py.File(path, "w") as f:
        f.create_dataset("instrument/HEM/Energy", data=[10.0])

    detected = detect_geometry_from_path(str(path), profile="20-ID-D")
    assert detected == pytest.approx({"wavelength_A": C.HC_KEV_A / 10.0})


def test_detect_geometry_wavelength_absent_when_dataset_missing(tmp_path):
    h5py = pytest.importorskip("h5py")
    from midas_gui.helpers import detect_geometry_from_path

    path = tmp_path / "scan.h5"
    with h5py.File(path, "w") as f:
        f.create_dataset("exchange/data", data=[[1, 2], [3, 4]])

    assert detect_geometry_from_path(str(path), profile="1-ID-E") == {}


def test_detect_geometry_combines_filename_and_h5_metadata(tmp_path):
    """A real Hydra frame file's name carries the detector tag AND its own
    HDF5 metadata carries the energy — both should be detected together."""
    h5py = pytest.importorskip("h5py")
    from midas_gui import constants as C
    from midas_gui.helpers import detect_geometry_from_path

    path = tmp_path / "dark_scan_002030.ge1.h5"
    with h5py.File(path, "w") as f:
        f.create_dataset("instrument/HEM/Energy", data=[80.61])

    detected = detect_geometry_from_path(str(path), profile="1-ID-E")
    assert detected == pytest.approx({"pxY": 200.0, "wavelength_A": C.HC_KEV_A / 80.61})


# ── Integration tab Rmin/Rmax presets + bin-grid thinning (pure logic, no Qt) ──

def test_rmax_corner_px_centered_beam():
    from midas_gui.helpers import rmax_corner_px
    import math
    # Centered beam on a 100x100 detector: corner distance is the half-diagonal.
    assert rmax_corner_px(49.5, 49.5, 100, 100) == pytest.approx(
        math.hypot(49.5, 49.5))


def test_rmax_corner_px_off_center_beam():
    from midas_gui.helpers import rmax_corner_px
    import math
    # Beam near the bottom-left corner of a 100x100 detector: farthest corner
    # is the top-right one, at distance (99-10, 99-20) away.
    assert rmax_corner_px(10, 20, 100, 100) == pytest.approx(math.hypot(89, 79))


def test_rmax_edge_px_off_center_beam():
    from midas_gui.helpers import rmax_edge_px
    # Farthest straight edge is whichever perpendicular distance is largest:
    # left=10, right=89, bottom=20, top=79 -> the right edge, at 89.
    assert rmax_edge_px(10, 20, 100, 100) == pytest.approx(89)


def test_rmax_edge_never_exceeds_corner():
    from midas_gui.helpers import rmax_corner_px, rmax_edge_px
    for bc_y, bc_z in [(10, 20), (49.5, 49.5), (5, 990)]:
        assert rmax_edge_px(bc_y, bc_z, 1000, 1000) <= rmax_corner_px(bc_y, bc_z, 1000, 1000)


def test_thinned_bin_edges_no_thinning_needed():
    from midas_gui.helpers import _thinned_bin_edges
    edges = _thinned_bin_edges(0.0, 10.0, 2.0, max_count=50)
    np.testing.assert_allclose(edges, [0.0, 2.0, 4.0, 6.0, 8.0, 10.0])


def test_thinned_bin_edges_caps_dense_bins():
    from midas_gui.helpers import _thinned_bin_edges
    edges = _thinned_bin_edges(0.0, 1000.0, 0.5, max_count=50)
    assert len(edges) <= 50
    assert edges[0] == pytest.approx(0.0)


def test_thinned_bin_edges_degenerate_range_is_empty():
    from midas_gui.helpers import _thinned_bin_edges
    assert len(_thinned_bin_edges(10.0, 10.0, 1.0, max_count=50)) == 0
    assert len(_thinned_bin_edges(10.0, 0.0, 1.0, max_count=50)) == 0
    assert len(_thinned_bin_edges(0.0, 10.0, 0.0, max_count=50)) == 0


# ═════════════════════════════════════════════════════════════════════════════
# VAREX-style multi-frame HDF5 stack combining (read_hdf5_stack_combined)
# ═════════════════════════════════════════════════════════════════════════════

def _stack_file(tmp_path, n, h=3, w=4, name="s.h5"):
    h5py = pytest.importorskip("h5py")
    data = np.arange(n * h * w, dtype=np.float32).reshape(n, h, w)
    with h5py.File(tmp_path / name, "w") as f:
        f.create_dataset("exchange/data", data=data)
    return tmp_path / name, data


def test_read_hdf5_stack_combined_whole_file_by_default(tmp_path):
    """chunk_size=None collapses the whole (N,H,W) stack to one frame — the
    VAREX case where every raw sub-frame is the same scan point."""
    from midas_gui.helpers import read_hdf5_stack_combined
    path, data = _stack_file(tmp_path, 5)
    out = read_hdf5_stack_combined(path, "exchange/data")
    assert len(out) == 1
    np.testing.assert_allclose(out[0], data.mean(axis=0), rtol=1e-6)
    assert out[0].dtype == np.float32


@pytest.mark.parametrize("op, reducer", [
    ("mean", np.mean), ("sum", np.sum), ("max", np.max), ("median", np.median),
])
def test_read_hdf5_stack_combined_ops(tmp_path, op, reducer):
    from midas_gui.helpers import read_hdf5_stack_combined
    path, data = _stack_file(tmp_path, 5)
    out = read_hdf5_stack_combined(path, "exchange/data", op=op)
    np.testing.assert_allclose(out[0], reducer(data, axis=0), rtol=1e-6)


def test_read_hdf5_stack_combined_unknown_op_falls_back_to_mean(tmp_path):
    from midas_gui.helpers import read_hdf5_stack_combined
    path, data = _stack_file(tmp_path, 4)
    out = read_hdf5_stack_combined(path, "exchange/data", op="nonsense")
    np.testing.assert_allclose(out[0], data.mean(axis=0), rtol=1e-6)


def test_read_hdf5_stack_combined_chunks_contiguously(tmp_path):
    from midas_gui.helpers import read_hdf5_stack_combined
    path, data = _stack_file(tmp_path, 6)
    out = read_hdf5_stack_combined(path, "exchange/data", chunk_size=2, op="sum")
    assert len(out) == 3
    for k in range(3):
        np.testing.assert_allclose(out[k], data[2 * k:2 * k + 2].sum(axis=0),
                                   rtol=1e-6)


def test_read_hdf5_stack_combined_ragged_last_chunk(tmp_path):
    """A stack that doesn't divide evenly keeps its short trailing chunk."""
    from midas_gui.helpers import read_hdf5_stack_combined
    path, data = _stack_file(tmp_path, 5)
    out = read_hdf5_stack_combined(path, "exchange/data", chunk_size=2)
    assert len(out) == 3
    np.testing.assert_allclose(out[-1], data[4:].mean(axis=0), rtol=1e-6)


def test_read_hdf5_stack_combined_passes_2d_through(tmp_path):
    """A plain (H,W) dataset is already one frame; chunk_size/op are ignored."""
    h5py = pytest.importorskip("h5py")
    from midas_gui.helpers import read_hdf5_stack_combined
    img = np.arange(12, dtype=np.float32).reshape(3, 4)
    with h5py.File(tmp_path / "flat.h5", "w") as f:
        f.create_dataset("exchange/data", data=img)
    out = read_hdf5_stack_combined(tmp_path / "flat.h5", "exchange/data",
                                   chunk_size=2, op="sum")
    assert len(out) == 1
    np.testing.assert_allclose(out[0], img)


# ═════════════════════════════════════════════════════════════════════════════
# Tilt-aware overlay geometry (tilted_ring_xy / tilted_spoke_xy)
# ═════════════════════════════════════════════════════════════════════════════

_GEO = dict(Lsd_um=500000.0, bc_y=1024.0, bc_z=980.0, pxY_um=200.0, pxZ_um=200.0)


def _expected_radius_px(two_theta_deg):
    """Plain flat-detector radius: Lsd*tan(2θ) converted to pixels."""
    return (_GEO["Lsd_um"] * np.tan(np.radians(two_theta_deg))) / _GEO["pxY_um"]


def test_tilted_ring_reduces_to_a_plain_circle_at_zero_tilt():
    """The documented invariant: with tx=ty=tz=0 the forward projection must
    collapse to bc + r*(sin eta, cos eta), the untilted circle every overlay
    used to draw directly."""
    from midas_gui.helpers import tilted_ring_xy
    tt, n = 8.0, 360
    Y, Z = tilted_ring_xy(tt, 0.0, 0.0, 0.0, n=n, **_GEO)

    r = _expected_radius_px(tt)
    # endpoint=True: the ring grid closes its polyline (last point == first),
    # so the n samples span [0, 360] inclusive at 360/(n-1) spacing.
    eta = np.radians(np.linspace(0.0, 360.0, n, endpoint=True))
    np.testing.assert_allclose(Y, _GEO["bc_y"] + r * np.sin(eta), atol=1e-6)
    np.testing.assert_allclose(Z, _GEO["bc_z"] + r * np.cos(eta), atol=1e-6)


def test_tilted_ring_polyline_is_closed():
    """The ring is fed straight to pg.PlotDataItem, which never auto-closes a
    polyline — so the last sample must repeat the first, or the overlay shows
    a visible seam at eta=0."""
    from midas_gui.helpers import tilted_ring_xy
    for tilt in ((0.0, 0.0, 0.0), (0.5, 2.0, -1.0)):
        Y, Z = tilted_ring_xy(7.0, *tilt, n=180, **_GEO)
        np.testing.assert_allclose([Y[0], Z[0]], [Y[-1], Z[-1]], atol=1e-12)


def test_tilted_ring_is_centred_and_round_at_zero_tilt():
    from midas_gui.helpers import tilted_ring_xy
    tt = 5.0
    Y, Z = tilted_ring_xy(tt, 0.0, 0.0, 0.0, n=180, **_GEO)
    radii = np.hypot(Y - _GEO["bc_y"], Z - _GEO["bc_z"])
    np.testing.assert_allclose(radii, _expected_radius_px(tt), rtol=1e-9)


def test_tilted_ring_becomes_eccentric_under_tilt():
    """A non-zero tilt must actually change the projection — otherwise the
    'overlay offset from the real rings' bug this replaced would be back."""
    from midas_gui.helpers import tilted_ring_xy
    tt = 8.0
    Y0, Z0 = tilted_ring_xy(tt, 0.0, 0.0, 0.0, n=180, **_GEO)
    Y1, Z1 = tilted_ring_xy(tt, 0.0, 3.0, 0.0, n=180, **_GEO)
    assert not np.allclose(Y0, Y1, atol=1e-3) or not np.allclose(Z0, Z1, atol=1e-3)
    # ...and the tilted ring is no longer a constant-radius circle.
    radii = np.hypot(Y1 - _GEO["bc_y"], Z1 - _GEO["bc_z"])
    assert radii.ptp() > 1e-3


def test_tilted_ring_returns_n_points():
    from midas_gui.helpers import tilted_ring_xy
    Y, Z = tilted_ring_xy(6.0, 1.0, 2.0, 3.0, n=57, **_GEO)
    assert Y.shape == Z.shape == (57,)


def test_tilted_spoke_is_radial_at_zero_tilt():
    """A fixed-eta spoke at zero tilt is a straight radial line through the
    beam centre, sampled between the two 2θ limits."""
    from midas_gui.helpers import tilted_spoke_xy
    eta, n = 30.0, 24
    Y, Z = tilted_spoke_xy(2.0, 10.0, eta, 0.0, 0.0, 0.0, n=n, **_GEO)
    assert Y.shape == Z.shape == (n,)

    r = np.hypot(Y - _GEO["bc_y"], Z - _GEO["bc_z"])
    np.testing.assert_allclose(r[0], _expected_radius_px(2.0), rtol=1e-9)
    np.testing.assert_allclose(r[-1], _expected_radius_px(10.0), rtol=1e-9)
    assert np.all(np.diff(r) > 0), "must run outward monotonically"

    # Every sample sits on the same azimuth.
    ang = np.degrees(np.arctan2(Y - _GEO["bc_y"], Z - _GEO["bc_z"]))
    np.testing.assert_allclose(ang, eta, atol=1e-6)


def test_tilted_spoke_endpoints_match_the_ring_at_the_same_eta():
    """Spoke and ring are two slices of one projection, so where they meet
    they must agree exactly — the property that keeps a bin-grid overlay's
    cells closed."""
    from midas_gui.helpers import tilted_ring_xy, tilted_spoke_xy
    tx, ty, tz, tt = 0.5, 2.0, -1.0, 7.0
    n = 360
    Yr, Zr = tilted_ring_xy(tt, tx, ty, tz, n=n, **_GEO)
    idx = 45                                  # some eta on the closed ring grid
    eta = np.linspace(0.0, 360.0, n, endpoint=True)[idx]
    Ys, Zs = tilted_spoke_xy(tt, tt, eta, tx, ty, tz, n=2, **_GEO)
    np.testing.assert_allclose([Ys[0], Zs[0]], [Yr[idx], Zr[idx]], atol=1e-9)
