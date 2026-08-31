"""Tests for midas_gui.gsas_export — GSAS-II zarr export from a project attempt.

Verifies against GSAS-II's own contract (``G2pwd_MIDAS.py``'s
``ContentsValidator``, read directly from AdvancedPhotonSource/GSAS-II while
designing this feature): the three required groups exist, ``REtaMap``'s
shape/indices are right, and the >20-unmasked-point filtering + weight
formula GSAS-II applies on import don't blow up. Needs only zarr+numpy+torch
(already pinned deps) — not GSAS-II itself.
"""
from types import SimpleNamespace

import numpy as np
import pytest
import zarr

from midas_gui import project
from midas_gui.gsas_export import export_gsas_zarr
from midas_gui.helpers import _build_spec


def _fake_calibration_snapshot(**overrides):
    """A plain, JSON-safe dict — what BatchTab._calib_fields_in_use()/
    resolve_calibration_fields() actually produce and log to a project
    attempt's ``calibration_snapshot``. Small detector (64x64) to keep
    geometry-building fast in tests."""
    fields = dict(
        Lsd=200000.0, BC_y=32.0, BC_z=32.0, tx=0.0, ty=0.0, tz=0.0,
        distortion={}, pxY=200.0, pxZ=200.0,
        NrPixelsY=64, NrPixelsZ=64, wavelength_A=0.1729,
    )
    fields.update(overrides)
    return fields


def _build_test_spec(calib_snapshot, r_bin, e_bin):
    result_ns = project.calibration_namespace(calib_snapshot)
    return _build_spec(result_ns, r_bin, e_bin)


def _open_zarr(path):
    try:
        return zarr.open(str(path), mode="r")
    except Exception:
        store = zarr.storage.ZipStore(str(path), mode="r")
        return zarr.open_group(store, mode="r")


def _replicate_gsas_read(fp):
    """The core of GSAS-II's G2pwd_MIDAS.py::readMidas (read verbatim from
    AdvancedPhotonSource/GSAS-II while designing this feature) — enough to
    confirm our writer's output is genuinely readable by the real importer,
    without needing GSAS-II installed. Returns the first survivable
    (>20-unmasked-point) lineout's normalized intensity."""
    retamap = np.array(fp["REtaMap"])          # [0]=R [1]=2theta [2]=eta [3]=area
    n_bins, n_azim = retamap[1].shape
    unmasked = [(retamap[3][:, i] != 0) for i in range(n_azim)]
    m_azm = [i for i in range(n_azim) if sum(unmasked[i]) > 20]
    assert m_azm, "no azimuth survived GSAS-II's >20-unmasked-point filter"
    i_azm = m_azm[0]
    osf = fp["OmegaSumFrame"]
    k0 = sorted(osf.keys())[0]
    attrs = dict(osf[k0].attrs.items())
    assert "Number Of Frames Summed" in attrs
    n_frame = attrs.get("Number Of Frames Summed", 1.0)
    y = np.array(osf[k0])[:, i_azm][unmasked[i_azm]] / n_frame
    normalization = retamap[3][:, i_azm][unmasked[i_azm]]
    w = np.where(y > 0, np.zeros_like(y), n_frame ** 2 * normalization / y)
    assert np.all(np.isfinite(y)), "non-finite intensity after GSAS-II's own read math"
    return y


def test_export_gsas_zarr_2d_profile_roundtrip(tmp_path):
    """Today's default Batch Integrate output (one full-circle profile per
    frame, profiles.ndim == 2) degenerates to Nazim=1 in the export."""
    proj_path = str(tmp_path / "proj.h5")
    project.create_project(proj_path)

    calib_snapshot = _fake_calibration_snapshot()
    r_bin, e_bin = 1.0, 5.0   # small r_bin so enough radial bins clear GSAS-II's >20 filter
    spec = _build_test_spec(calib_snapshot, r_bin, 360.0)   # export forces e_bin=360 for 2D
    n_r = spec.n_r_bins
    r_axis_px = spec.RMin + spec.RBinSize * (np.arange(n_r) + 0.5)

    n_frames = 3
    profiles = np.abs(np.random.rand(n_frames, n_r)).astype(np.float64) + 1.0
    sigmas = np.sqrt(profiles)
    finished_payload = {
        "n": n_frames, "profiles": profiles, "r_axis_px": r_axis_px,
        "sigmas": sigmas, "frame_ids": [f"frame_{i}" for i in range(n_frames)],
        "aborted": False,
    }
    inputs = {"kernel": "subpixel2", "r_bin": r_bin, "e_bin": e_bin, "q_cfg": None}
    ref = project.append_integration_attempt(
        proj_path, "single", inputs=inputs, finished_payload=finished_payload,
        calibration_snapshot=calib_snapshot,
        extra={"n_eta_bins": 1, "eta_axis_deg": None})

    out_path = tmp_path / "final.zarr.zip"
    written = export_gsas_zarr(proj_path, "single", ref, out_path)
    assert written == out_path
    assert (tmp_path / "final.zarr.zip.provenance.json").exists()

    fp = _open_zarr(written)
    midassections = ("InstrumentParameters", "REtaMap", "OmegaSumFrame")
    assert all(s in fp for s in midassections)

    reta = np.array(fp["REtaMap"])
    assert reta.shape == (5, n_r, 1)   # R, 2theta, eta, area, Q — Nazim=1
    assert np.all(reta[3] >= 0)        # bin area (index 3) never negative

    osf = fp["OmegaSumFrame"]
    assert len(osf) == n_frames
    for k in osf:
        arr = np.array(osf[k])
        assert arr.shape == (n_r, 1)
        attrs = dict(osf[k].attrs.items())
        assert "Number Of Frames Summed" in attrs
        assert "FirstOme" in attrs and "LastOme" in attrs

    ip = fp["InstrumentParameters"]
    for key in ("Lam", "Distance", "Polariz", "SH_L", "U", "V", "W", "X", "Y", "Z"):
        assert key in ip
    assert float(ip["Lam"][0]) == pytest.approx(0.1729, rel=1e-6)
    assert float(ip["Distance"][0]) == pytest.approx(200000.0, rel=1e-6)

    _replicate_gsas_read(fp)


def test_export_gsas_zarr_3d_cake_roundtrip(tmp_path):
    """Multi-azimuth mode (profiles.ndim == 3) carries every eta sector
    through as a real (n_r, n_eta) array, not collapsed to one column."""
    proj_path = str(tmp_path / "proj.h5")
    project.create_project(proj_path)

    calib_snapshot = _fake_calibration_snapshot()
    r_bin, e_bin = 2.0, 120.0   # 360/120 = 3 azimuthal sectors
    spec = _build_test_spec(calib_snapshot, r_bin, e_bin)
    n_r, n_eta = spec.n_r_bins, spec.n_eta_bins
    assert n_eta == 3
    r_axis_px = spec.RMin + spec.RBinSize * (np.arange(n_r) + 0.5)

    n_frames = 2
    profiles = np.abs(np.random.rand(n_frames, n_eta, n_r)).astype(np.float64) + 1.0
    sigmas = np.sqrt(profiles)
    finished_payload = {
        "n": n_frames, "profiles": profiles, "r_axis_px": r_axis_px,
        "sigmas": sigmas, "frame_ids": [f"frame_{i}" for i in range(n_frames)],
        "aborted": False,
    }
    inputs = {"kernel": "subpixel2", "r_bin": r_bin, "e_bin": e_bin,
             "q_cfg": None, "multi_azimuth": True}
    ref = project.append_integration_attempt(
        proj_path, "single", inputs=inputs, finished_payload=finished_payload,
        calibration_snapshot=calib_snapshot,
        extra={"n_eta_bins": n_eta, "eta_axis_deg": [0.0, 120.0, 240.0]})

    out_path = tmp_path / "final_cake.zarr.zip"
    written = export_gsas_zarr(proj_path, "single", ref, out_path)

    fp = _open_zarr(written)
    reta = np.array(fp["REtaMap"])
    assert reta.shape == (5, n_r, n_eta)
    osf = fp["OmegaSumFrame"]
    assert len(osf) == n_frames
    for k in osf:
        assert np.array(osf[k]).shape == (n_r, n_eta)


def test_export_gsas_zarr_rejects_q_uniform_attempt(tmp_path):
    proj_path = str(tmp_path / "proj.h5")
    project.create_project(proj_path)
    calib_snapshot = _fake_calibration_snapshot()
    finished_payload = {
        "n": 1, "profiles": np.ones((1, 5)), "r_axis_px": np.arange(5, dtype=float),
        "sigmas": np.ones((1, 5)), "frame_ids": ["f0"], "aborted": False,
    }
    inputs = {"kernel": "subpixel2", "r_bin": 1.0, "e_bin": 5.0,
             "q_cfg": {"QMin": 0.5, "QMax": 8.0, "QBinSize": 0.01}}
    ref = project.append_integration_attempt(
        proj_path, "single", inputs=inputs, finished_payload=finished_payload,
        calibration_snapshot=calib_snapshot)

    with pytest.raises(ValueError, match="Q-uniform"):
        export_gsas_zarr(proj_path, "single", ref, tmp_path / "out.zarr.zip")


def test_export_gsas_zarr_rejects_file_backed_mask(tmp_path):
    proj_path = str(tmp_path / "proj.h5")
    project.create_project(proj_path)
    calib_snapshot = _fake_calibration_snapshot()
    finished_payload = {
        "n": 1, "profiles": np.ones((1, 5)), "r_axis_px": np.arange(5, dtype=float),
        "sigmas": np.ones((1, 5)), "frame_ids": ["f0"], "aborted": False,
    }
    inputs = {"kernel": "subpixel2", "r_bin": 1.0, "e_bin": 5.0, "q_cfg": None}
    fake_mask = np.zeros((64, 64), dtype=bool)
    ref = project.append_integration_attempt(
        proj_path, "single", inputs=inputs, finished_payload=finished_payload,
        calibration_snapshot=calib_snapshot,
        mask=fake_mask, mask_is_file_backed=True)

    with pytest.raises(ValueError, match="file-backed"):
        export_gsas_zarr(proj_path, "single", ref, tmp_path / "out.zarr.zip")
