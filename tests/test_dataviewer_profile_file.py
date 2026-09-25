"""Data Viewer Radial Profile tab: loading a pre-integrated profile file
(csv/xye/dat/fxye — Batch Integrate's own output formats) straight into the
plot, with the image viewer left empty, and calibration-based ring
simulation still available on top of it.

Not fork-isolated, for the same reason as test_view_tab_controls.py: every
test in a forked file that builds a DataViewerTab dies with SIGSEGV in the
child on this machine. Run plain, these pass — see .context/STATE.md.
"""
from __future__ import annotations

import math

import numpy as np
import pytest
from PyQt5 import QtWidgets

from midas_gui.helpers import load_profile_file, native_axis_to_r_px, profile_file_axis_kind
from midas_gui.tab_view import DataViewerTab
from midas_gui.workers import write_profile


# ── Pure-logic: reading + axis conversion (no Qt) ────────────────────────

def test_axis_kind_from_extension():
    assert profile_file_axis_kind("scan.csv") == "r_px"
    assert profile_file_axis_kind("scan.xye") == "two_theta_deg"
    assert profile_file_axis_kind("scan.fxye") == "two_theta_deg"
    assert profile_file_axis_kind("scan.dat") == "q_invA"


@pytest.mark.parametrize("fmt", ["csv", "xye", "dat"])
def test_round_trip_each_written_format(tmp_path, fmt):
    r = np.linspace(1.0, 200.0, 40)
    prof = np.exp(-((r - 100.0) ** 2) / 500.0) * 1000.0
    sigma = np.sqrt(np.abs(prof))
    base = str(tmp_path / "profile")
    write_profile(base, fmt, r, prof, sigma, lsd=200_000.0, px=200.0, wl=0.1729)
    path = f"{base}.{fmt}"

    x, y, sig = load_profile_file(path)
    assert y.shape == r.shape
    assert sig is not None
    np.testing.assert_allclose(y, prof, rtol=1e-4)

    kind = profile_file_axis_kind(path)
    r_back = native_axis_to_r_px(x, kind, lsd_um=200_000.0, px_um=200.0, wavelength_A=0.1729)
    # csv's x is already r_px; xye/dat round-trip through the same forward
    # geometry write_profile used, so converting back should recover r.
    np.testing.assert_allclose(r_back, r, rtol=1e-3)


def test_native_axis_to_r_px_identity_for_r_px():
    x = np.array([1.0, 50.0, 100.0])
    out = native_axis_to_r_px(x, "r_px", lsd_um=1.0, px_um=1.0, wavelength_A=None)
    np.testing.assert_array_equal(out, x)


def test_native_axis_to_r_px_two_theta():
    lsd_um, px_um = 200_000.0, 200.0
    two_theta_deg = np.array([1.0, 5.0, 10.0])
    r_px = native_axis_to_r_px(two_theta_deg, "two_theta_deg", lsd_um, px_um)
    expected = (lsd_um / px_um) * np.tan(np.radians(two_theta_deg))
    np.testing.assert_allclose(r_px, expected)


def test_native_axis_to_r_px_q_requires_wavelength():
    with pytest.raises(ValueError):
        native_axis_to_r_px(np.array([1.0]), "q_invA", lsd_um=1.0, px_um=1.0)


# ── Qt: Radial Profile tab's "Profile file…" source ──────────────────────

@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def tab(app):
    return DataViewerTab()


@pytest.fixture(autouse=True)
def _reset_profile_mode(tab):
    """Every test starts from "Detector frame" with no calibration, and
    leaves the shared tab that way for the next test."""
    tab._exit_profile_file_mode()
    tab._geom_card._calib_geom = None
    yield
    tab._exit_profile_file_mode()
    tab._geom_card._calib_geom = None


def _write(tmp_path, fmt, r, prof, sigma):
    base = str(tmp_path / "profile")
    write_profile(base, fmt, r, prof, sigma, lsd=200_000.0, px=200.0, wl=0.1729)
    return f"{base}.{fmt}"


def test_loading_a_csv_profile_plots_it_directly_and_blanks_the_image(tab, tmp_path):
    r = np.linspace(1.0, 100.0, 30)
    prof = np.full_like(r, 5.0)
    path = _write(tmp_path, "csv", r, prof, np.sqrt(prof))
    x_written, _, _ = load_profile_file(path)   # csv's own %.6e round-trip of r

    tab._load_profile_file(path)

    assert tab._profile_file_mode is True
    assert tab._cur is None
    assert tab._viewer._data is None
    assert tab._frame_scrub_bar.isHidden()
    np.testing.assert_allclose(tab._profile_view._r_px, x_written)
    assert tab._profile_view._xaxis.isEnabled()


def test_loading_a_dat_profile_without_calibration_locks_native_axis(tab, tmp_path):
    # write_profile's "dat" format stores Q (not r_px) — its own forward
    # conversion of `r`, given the same lsd/px/wl _write passes.
    r = np.linspace(1.0, 100.0, 30)
    prof = np.full_like(r, 5.0)
    path = _write(tmp_path, "dat", r, prof, np.sqrt(prof))
    q_written, _, _ = load_profile_file(path)

    tab._load_profile_file(path)

    assert not tab._profile_view._xaxis.isEnabled()
    assert tab._profile_view._xaxis.currentText() == "Q (Å⁻¹)"
    np.testing.assert_allclose(tab._profile_view._r_px, q_written)   # plotted as-is (native)


def test_attaching_calibration_upgrades_native_plot_to_converted_r_px(tab, tmp_path):
    lsd_um, px_um, wl = 200_000.0, 200.0, 0.1729
    r = np.linspace(1.0, 100.0, 30)
    prof = np.full_like(r, 5.0)
    path = _write(tmp_path, "xye", r, prof, np.sqrt(prof))

    tab._load_profile_file(path)
    assert not tab._profile_view._xaxis.isEnabled()

    tab._geom_card._calib_geom = {
        "wavelength_A": wl, "Lsd": lsd_um, "BC_y": 512, "BC_z": 512,
        "pxY": px_um, "pxZ": px_um, "NrPixelsY": 1024, "NrPixelsZ": 1024,
        "tx": 0.0, "ty": 0.0, "tz": 0.0, "distortion": {}, "im_trans": [],
    }
    tab._geom_card._wl.setValue(wl)
    tab._geom_card._lsd.setValue(lsd_um / 1000.0)
    tab._geom_card._px.setValue(px_um)
    tab._geom_card.geometryChanged.emit()

    assert tab._profile_view._xaxis.isEnabled()
    # xye stores 2θ (write_profile's own forward conversion of r at the same
    # lsd/px/wl) — converting back with the now-attached calibration should
    # recover the original r_px to within the format's text-precision loss.
    np.testing.assert_allclose(tab._profile_view._r_px, r, rtol=1e-3)


def test_ring_markers_populate_once_calibration_is_attached(tab, tmp_path):
    lsd_um, px_um, wl = 200_000.0, 200.0, 0.1729
    r = np.linspace(1.0, 400.0, 60)
    prof = np.full_like(r, 5.0)
    path = _write(tmp_path, "dat", r, prof, np.sqrt(prof))

    tab._load_profile_file(path)
    tab._geom_card._calib_geom = {
        "wavelength_A": wl, "Lsd": lsd_um, "BC_y": 512, "BC_z": 512,
        "pxY": px_um, "pxZ": px_um, "NrPixelsY": 1024, "NrPixelsZ": 1024,
        "tx": 0.0, "ty": 0.0, "tz": 0.0, "distortion": {}, "im_trans": [],
    }
    tab._geom_card._wl.setValue(wl)
    tab._geom_card._lsd.setValue(lsd_um / 1000.0)
    tab._geom_card._px.setValue(px_um)
    tab._geom_card.geometryChanged.emit()

    assert any(m["enabled"] for m in tab._geom_card._materials)
    assert tab._profile_view._ring_groups
    assert tab._profile_view._ring_lines


def test_switching_back_to_detector_frame_restores_the_image(tab, tmp_path):
    r = np.linspace(1.0, 100.0, 30)
    prof = np.full_like(r, 5.0)
    path = _write(tmp_path, "csv", r, prof, np.sqrt(prof))
    tab._load_profile_file(path)
    assert tab._profile_file_mode

    tab._exit_profile_file_mode()

    assert tab._profile_file_mode is False
    assert tab._loaded_profile is None
    assert tab._cur is not None   # loader's own data is back on display
