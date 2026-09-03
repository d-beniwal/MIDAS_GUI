"""Tests for ``zarr_cake.write_cake_zarr`` — Batch Integrate's full (eta, R)
cake output.

The layout is not ours to choose: it mirrors the native MIDAS zarr that
GSAS-II's ``G2pwd_MIDAS.py`` and mpe_wf_saxs_waxs read, so the axis ORDER and
the ``REtaMap`` channel order are load-bearing. A silent transposition here
produces a file that opens fine and is wrong, which is exactly the failure a
test should catch.
"""
import numpy as np
import pytest

from midas_gui import provenance
from midas_gui.workers import axis_conversions
from midas_gui.zarr_cake import write_cake_zarr

N_ETA, N_R, N_FRAMES = 4, 6, 3
LSD_UM, PX_UM, WL_A = 200000.0, 200.0, 0.2


@pytest.fixture
def written(tmp_path):
    """A store written from deliberately asymmetric, fully distinguishable
    inputs so a transposition can't accidentally pass."""
    zarr = pytest.importorskip("zarr")
    r_axis = np.linspace(10.0, 60.0, N_R)
    eta_axis = np.linspace(-180.0, 90.0, N_ETA)
    cakes = [np.arange(N_ETA * N_R, dtype=float).reshape(N_ETA, N_R) + 100 * k
             for k in range(N_FRAMES)]
    bin_area = np.arange(N_ETA * N_R, dtype=float).reshape(N_ETA, N_R)
    path = tmp_path / "integrated.zarr.zip"
    write_cake_zarr(path, cakes, r_axis_px=r_axis, eta_axis_deg=eta_axis,
                    lsd_um=LSD_UM, px_um=PX_UM, wavelength_A=WL_A,
                    omegas=[0.5, 1.5, 2.5], bin_area=bin_area,
                    provenance_entry=provenance.build_entry("test"))
    root = zarr.open(zarr.ZipStore(str(path), mode="r"), mode="r")
    return root, r_axis, eta_axis, cakes, bin_area, path


def test_top_level_layout(written):
    root, *_ = written
    assert set(root.array_keys()) == {"REtaMap", "Omegas"}
    assert set(root.group_keys()) == {"IntegrationResult", "InstrumentParameters"}


def test_reta_map_channel_order_and_orientation(written):
    """REtaMap is (5, nR, nEta) with channels Radius/2Theta/Eta/BinArea/Q.
    Radius/2Theta/Q vary along R and are constant across eta; Eta is the
    other way round."""
    root, r_axis, eta_axis, _, bin_area, _ = written
    reta = np.asarray(root["REtaMap"])
    assert reta.shape == (5, N_R, N_ETA)
    assert root["REtaMap"].attrs["Header"] == "Radius,2Theta,Eta,BinArea,Q"
    assert root["REtaMap"].attrs["nRBins"] == N_R
    assert root["REtaMap"].attrs["nEtaBins"] == N_ETA

    radius, two_theta, eta, binarea, q = reta
    for ch in (radius, two_theta, q):
        assert np.allclose(ch, ch[:, :1]), "must be constant across eta"
    assert np.allclose(eta, eta[:1, :]), "eta must be constant across R"

    np.testing.assert_allclose(radius[:, 0], r_axis)
    np.testing.assert_allclose(eta[0, :], eta_axis)

    exp_tt, _, exp_q = axis_conversions(r_axis, LSD_UM, PX_UM, WL_A)
    np.testing.assert_allclose(two_theta[:, 0], exp_tt)
    np.testing.assert_allclose(q[:, 0], exp_q)

    # BinArea comes in as (n_eta, n_r) and must be stored transposed.
    np.testing.assert_allclose(binarea, bin_area.T)


def test_bin_area_defaults_to_nan_when_not_supplied(tmp_path):
    """A missing pixel-count map must read as "unknown", not as zero area."""
    zarr = pytest.importorskip("zarr")
    path = tmp_path / "c.zarr.zip"
    write_cake_zarr(path, [np.zeros((N_ETA, N_R))],
                    r_axis_px=np.arange(float(N_R)),
                    eta_axis_deg=np.arange(float(N_ETA)),
                    lsd_um=LSD_UM, px_um=PX_UM, wavelength_A=WL_A)
    root = zarr.open(zarr.ZipStore(str(path), mode="r"), mode="r")
    assert np.isnan(np.asarray(root["REtaMap"])[3]).all()


def test_frames_are_stored_transposed_one_per_frame(written):
    """Input cakes are (n_eta, n_r); stored frames are (n_r, n_eta)."""
    root, _, _, cakes, _, _ = written
    ir = root["IntegrationResult"]
    assert sorted(ir.array_keys()) == [f"FrameNr_{i}" for i in range(N_FRAMES)]
    for i, cake in enumerate(cakes):
        ds = ir[f"FrameNr_{i}"]
        assert ds.shape == (N_R, N_ETA)
        np.testing.assert_allclose(np.asarray(ds), cake.T)
        assert ds.attrs["Header"] == "Radius,Eta"


def test_omegas_are_recorded_on_the_array_and_per_frame(written):
    root, *_ = written
    np.testing.assert_allclose(np.asarray(root["Omegas"]), [0.5, 1.5, 2.5])
    for i, om in enumerate([0.5, 1.5, 2.5]):
        assert root["IntegrationResult"][f"FrameNr_{i}"].attrs["omega"] == om


def test_omegas_default_to_the_frame_index(tmp_path):
    zarr = pytest.importorskip("zarr")
    path = tmp_path / "c.zarr.zip"
    write_cake_zarr(path, [np.zeros((2, 3))] * 3, r_axis_px=np.arange(3.0),
                    eta_axis_deg=np.arange(2.0), lsd_um=LSD_UM, px_um=PX_UM,
                    wavelength_A=WL_A)
    root = zarr.open(zarr.ZipStore(str(path), mode="r"), mode="r")
    np.testing.assert_allclose(np.asarray(root["Omegas"]), [0.0, 1.0, 2.0])


def test_instrument_parameters_convert_um_to_mm(written):
    """Distance is written in mm, while the GUI carries Lsd in um."""
    root, *_ = written
    ip = root["InstrumentParameters"]
    np.testing.assert_allclose(np.asarray(ip["Lam"]), [WL_A])
    np.testing.assert_allclose(np.asarray(ip["Distance"]), [LSD_UM * 1e-3])


def test_provenance_is_stamped_on_the_root(written):
    root, *_ = written
    history = root.attrs["provenance_history"]
    assert [e["tool"] for e in history] == ["test"]


def test_no_duplicate_zip_entries(written):
    """A live ZipStore that gets attrs written twice leaves duplicate members
    that confuse readers — the reason this stages in a DirectoryStore first."""
    import zipfile
    *_, path = written
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
    assert len(names) == len(set(names)), "duplicate entries in the zip"


def test_empty_frame_list_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        write_cake_zarr(tmp_path / "c.zarr.zip", [], r_axis_px=np.arange(3.0),
                        eta_axis_deg=np.arange(2.0), lsd_um=LSD_UM,
                        px_um=PX_UM, wavelength_A=WL_A)


def test_parent_directory_is_created(tmp_path):
    path = tmp_path / "nested" / "deeper" / "c.zarr.zip"
    write_cake_zarr(path, [np.zeros((2, 3))], r_axis_px=np.arange(3.0),
                    eta_axis_deg=np.arange(2.0), lsd_um=LSD_UM, px_um=PX_UM,
                    wavelength_A=WL_A)
    assert path.is_file()
