"""Unit tests for lightweight, profile-scoped widget helpers in helpers.py.

Covers the pieces added to fix "profile switch doesn't refresh option lists":
refresh_combo_items (used by the Calibrant dropdown) and the pixel-size /
K-edge-foil popup menus rebuilding their entries from live constants each
time they're opened, instead of freezing them at construction.
"""
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
