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
