"""Batch Integrate's cake (multi-azimuth) HDF5 output — cake_hdf5.write_cake_h5,
wired into BatchWorker.run(), write_all_profiles (Save button / parallel
merge) and BatchRunCoordinator's batch_parallel merge.

Complements test_batch_zarr_output.py/test_batch_zarr_gsas.py (zarr) and
test_batch_cake_stack.py (the in-memory cake-stack/collapse machinery)."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

pytestmark = pytest.mark.forked


def _tiny_calib_result(**overrides):
    fields = dict(
        Lsd=200000.0, BC_y=32.0, BC_z=32.0, tx=0.0, ty=0.0, tz=0.0,
        distortion={}, pxY=200.0, pxZ=200.0,
        NrPixelsY=64, NrPixelsZ=64, wavelength_A=0.1729,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _make_tiff_frames(tmp_path, n=2, size=64):
    tifffile = pytest.importorskip("tifffile")
    rng = np.random.default_rng(0)
    paths = []
    for i in range(n):
        p = tmp_path / f"frame_{i:04d}.tif"
        tifffile.imwrite(str(p),
                         (rng.random((size, size)) * 100 + 10).astype(np.float32))
        paths.append(str(p))
    return paths


def _make_varex_h5(path, n_frames=6, size=64):
    h5py = pytest.importorskip("h5py")
    rng = np.random.default_rng(0)
    with h5py.File(str(path), "w") as f:
        f.create_dataset("exchange/data",
                         data=(rng.random((n_frames, size, size)) * 100 + 10)
                         .astype(np.float32))
    return path


@pytest.fixture(scope="module")
def app():
    from PyQt5 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _run(app, tmp_path, fmts, *, multi_azimuth=True, n_frames=2, out_dir=None,
         src_cfg=None, calibration_snapshot=None):
    pytest.importorskip("torch")
    pytest.importorskip("midas_integrate_v2")
    import midas_gui.workers as wk
    from midas_gui.helpers import _build_spec

    spec = _build_spec(_tiny_calib_result(), r_bin=2.0, eta_bin=45.0)
    if src_cfg is None:
        paths = _make_tiff_frames(tmp_path / "in", n=n_frames)
        src_cfg = {"type": "tiff_list", "paths": paths}
    worker = wk.BatchWorker(
        spec, src_cfg, None, out_dir, fmts,
        "subpixel2", (None, None), None, multi_azimuth=multi_azimuth,
        calibration_snapshot=calibration_snapshot)
    results, failures, logs = {}, [], []
    worker.finished.connect(results.update)
    worker.failed.connect(failures.append)
    worker.log_line.connect(logs.append)
    worker.run()   # direct call, not .start() — no real QThread spawned
    assert not failures, failures[0] if failures else ""
    return results, logs, spec


@pytest.fixture
def in_dir(tmp_path):
    (tmp_path / "in").mkdir()
    return tmp_path


def test_single_frame_cake_h5_layout(app, in_dir):
    h5py = pytest.importorskip("h5py")
    out = in_dir / "out"
    calib = {"Lsd": 200000.0, "_calibrant_name": "CeO2"}
    results, _, spec = _run(app, in_dir, ["h5"], n_frames=1, out_dir=out,
                            calibration_snapshot=calib)

    h5_path = next((out / "h5").glob("*.h5"))
    with h5py.File(h5_path, "r") as f:
        # Flat, one-path-per-dataset layout — no entry/data/extra nesting,
        # no root soft-links, no GSAS-II-flavored REtaMap/SumFrames/Omegas/
        # InstrumentParameters (this file is never opened by GSAS-II; see
        # cake_hdf5.py's module docstring).
        cake = np.asarray(f["cake"])
        # (frame, eta, r) — cake[frame, eta_idx, :] is directly that wedge's
        # radial profile, and (being bin_area-weighted) summing over eta
        # reproduces the full profile exactly.
        assert cake.shape == (1, spec.n_eta_bins, spec.n_r_bins)
        assert np.asarray(f["cake_sigma"]).shape == cake.shape
        assert np.asarray(f["r_px"]).shape == (spec.n_r_bins,)
        assert np.asarray(f["two_theta_deg"]).shape == (spec.n_r_bins,)
        assert np.asarray(f["d_angstrom"]).shape == (spec.n_r_bins,)
        assert np.asarray(f["q_invA"]).shape == (spec.n_r_bins,)
        assert np.asarray(f["eta_deg"]).shape == (spec.n_eta_bins,)
        assert "REtaMap" not in f and "SumFrames" not in f and "Omegas" not in f
        assert "InstrumentParameters" not in f and "entry" not in f
        profiles = np.asarray(f["profiles"])
        assert profiles.shape == (1, spec.n_r_bins)
        np.testing.assert_allclose(cake.sum(axis=1), profiles, rtol=1e-8, atol=1e-8)
        assert f["cake"].attrs["weighting"].startswith("bin_area-weighted")
        history = json.loads(f.attrs["provenance_history"])
        extra = history[-1]["extra"]
        assert extra["calibration_snapshot"]["_calibrant_name"] == "CeO2"
        assert "active_profile" in extra and "aborted" in extra


def test_single_frame_cake_h5_profile_matches_real_engine_collapse(app, in_dir):
    """The stored /profiles must be the real engine-collapsed lineout (from
    integrate_frame's own prof/sigma), not the masked-eta-mean approximation
    — cross-check against an equivalent non-multi-azimuth run of the same
    frame."""
    h5py = pytest.importorskip("h5py")
    src_dir = in_dir / "shared_src"
    src_dir.mkdir()
    paths = _make_tiff_frames(src_dir, n=1)

    out_cake = in_dir / "out_cake"
    results_1d, _, _ = _run(app, in_dir, ["h5"], n_frames=1, out_dir=None,
                            multi_azimuth=False,
                            src_cfg={"type": "tiff_list", "paths": paths})
    _run(app, in_dir, ["h5"], n_frames=1, out_dir=out_cake,
        src_cfg={"type": "tiff_list", "paths": paths})

    h5_path = next((out_cake / "h5").glob("*.h5"))
    with h5py.File(h5_path, "r") as f:
        cake_profile = np.asarray(f["profiles"])[0]
    np.testing.assert_allclose(cake_profile, np.asarray(results_1d["profiles"])[0],
                               rtol=1e-6, atol=1e-8)


def test_multi_frame_tiff_folder_clubs_into_one_file(app, in_dir):
    """Every processed frame lands in ONE HDF5 file, not one per frame (that's
    the zarr convention, not this one)."""
    h5py = pytest.importorskip("h5py")
    out = in_dir / "out"
    _run(app, in_dir, ["h5"], n_frames=3, out_dir=out)

    h5_paths = list((out / "h5").glob("*.h5"))
    assert len(h5_paths) == 1
    with h5py.File(h5_paths[0], "r") as f:
        assert np.asarray(f["cake"]).shape[0] == 3
        assert len(f["frame_ids"]) == 3


def test_combine_subframes_chunking_clubs_chunks_not_raw_frames(app, in_dir):
    """A "Combine sub-frames" chunked HDF5-stack source: cake N == number of
    combined output frames, and frame_ids carry the chunk-range fid."""
    h5py = pytest.importorskip("h5py")
    src_path = in_dir / "in" / "scan_001.h5"
    src_path.parent.mkdir(exist_ok=True)
    _make_varex_h5(src_path, n_frames=6)

    out = in_dir / "out"
    src_cfg = {"type": "hdf5_stack_glob", "paths": [str(src_path)],
              "dataset": "exchange/data", "chunk_size": 3, "combine_op": "mean"}
    _run(app, in_dir, ["h5"], out_dir=out, src_cfg=src_cfg)

    h5_path = next((out / "h5").glob("*.h5"))
    with h5py.File(h5_path, "r") as f:
        frame_ids = [x.decode() if isinstance(x, bytes) else x
                    for x in np.asarray(f["frame_ids"])]
        assert np.asarray(f["cake"]).shape[0] == 2   # 6 raw frames / chunk_size 3
        assert frame_ids == ["scan_001.frame_0_2", "scan_001.frame_3_5"]


def test_provenance_still_stamped(app, in_dir):
    h5py = pytest.importorskip("h5py")
    out = in_dir / "out"
    _run(app, in_dir, ["h5"], out_dir=out)
    h5_path = next((out / "h5").glob("*.h5"))
    with h5py.File(h5_path, "r") as f:
        history = json.loads(f.attrs["provenance_history"])
    assert history[0]["tool"] == "midas_gui.batch_integrate"
    assert "cake_params" in history[0]


def test_provenance_is_a_discoverable_dataset_not_just_an_attr(app, in_dir):
    """h5ls/HDFView-style tree views don't show attributes by default, which
    made the (correct, complete) provenance look missing — stamp_h5_provenance
    now mirrors the same history into a root-level dataset too."""
    h5py = pytest.importorskip("h5py")
    out = in_dir / "out"
    _run(app, in_dir, ["h5"], out_dir=out)
    h5_path = next((out / "h5").glob("*.h5"))
    with h5py.File(h5_path, "r") as f:
        assert "provenance_history" in f.keys()   # a real dataset, not just .attrs
        ds_history = json.loads(np.asarray(f["provenance_history"]).item())
        attr_history = json.loads(f.attrs["provenance_history"])
    assert ds_history == attr_history


def test_write_all_profiles_writes_cake_h5_when_spec_supplied(in_dir):
    """The Save button / parallel-merge path: write_all_profiles's own cake
    branch, exercised directly with a synthetic in-memory stack."""
    pytest.importorskip("torch")
    pytest.importorskip("midas_integrate_v2")
    h5py = pytest.importorskip("h5py")
    import midas_gui.workers as wk
    from midas_gui.helpers import _build_spec, collapse_cake_eta

    spec = _build_spec(_tiny_calib_result(), r_bin=2.0, eta_bin=45.0)
    rng = np.random.default_rng(0)
    n_r, n_eta = spec.n_r_bins, spec.n_eta_bins
    profiles = rng.random((3, n_eta, n_r))
    sigmas = np.sqrt(np.maximum(profiles, 0.0))
    frame_ids = ["f0", "f1", "f2"]
    r_axis = np.arange(n_r, dtype=np.float64)
    eta_axis = np.linspace(-180, 180, n_eta)

    out_dir = in_dir / "save_out"
    # No bin_area supplied (the Save button doesn't cheaply have one) — cake
    # falls back to the unweighted per-bin mean, flagged via a warning.
    with pytest.warns(UserWarning, match="no bin_area supplied"):
        paths = wk.write_all_profiles(
            out_dir, ["h5"], r_axis, profiles, sigmas, frame_ids,
            float(spec.Lsd), float(spec.pxY), float(spec.Wavelength),
            eta_axis=eta_axis, spec=spec)
    assert paths == [str(out_dir / "integrated.h5")]

    with h5py.File(out_dir / "integrated.h5", "r") as f:
        assert np.asarray(f["cake"]).shape == (3, n_eta, n_r)
        np.testing.assert_array_equal(np.asarray(f["cake"]), profiles)
        assert f["cake"].attrs["weighting"].startswith("unweighted")
        expected_profile = collapse_cake_eta(profiles)
        np.testing.assert_allclose(np.asarray(f["profiles"]), expected_profile)


def test_write_all_profiles_skips_cake_h5_without_spec(in_dir):
    """Backward compatible: no spec means the old silent-skip behaviour."""
    import midas_gui.workers as wk
    profiles = np.zeros((2, 4, 3))
    paths = wk.write_all_profiles(
        in_dir / "out2", ["h5"], np.arange(3), profiles, None, ["a", "b"],
        1.0, 1.0, 1.0)
    assert paths == []
    assert not (in_dir / "out2" / "integrated.h5").exists()


def test_batch_run_coordinator_parallel_multi_azimuth_h5_has_bin_area(in_dir):
    """BatchRunCoordinator._on_chunk_finished's own cake-HDF5 branch rebuilds
    geometry so BinArea isn't left at zero for a combined parallel-mode run."""
    pytest.importorskip("torch")
    pytest.importorskip("midas_integrate_v2")
    h5py = pytest.importorskip("h5py")
    import midas_gui.workers as wk
    from midas_gui.helpers import _build_spec

    spec = _build_spec(_tiny_calib_result(), r_bin=2.0, eta_bin=45.0)
    n_r, n_eta = spec.n_r_bins, spec.n_eta_bins
    rng = np.random.default_rng(0)
    out_dir = in_dir / "out3"

    coord = wk.BatchRunCoordinator(
        spec=spec, source_cfg=None, mask=None, out_dir=str(out_dir), fmts=["h5"],
        kernel="subpixel2", corrections=(None, None), variance_cfg=None,
        multi_azimuth=True, weighted=True,
        calibration_snapshot={"Lsd": float(spec.Lsd)})

    class _DummyWorker:
        pass

    w_a, w_b = _DummyWorker(), _DummyWorker()
    coord._chunk_workers = [w_a, w_b]
    r_axis = np.arange(n_r, dtype=np.float64)
    eta_axis = np.linspace(-180, 180, n_eta)
    for w, n in ((w_a, 2), (w_b, 1)):
        cake = rng.random((n, n_eta, n_r))
        coord._chunk_results[id(w)] = {
            "profiles": list(cake), "sigmas": list(np.sqrt(np.maximum(cake, 0.0))),
            "frame_ids": [f"f{id(w)}_{i}" for i in range(n)],
            "out_paths": [], "aborted": False,
            "r_axis_px": r_axis, "eta_axis": eta_axis,
        }

    finished = []
    coord.finished.connect(finished.append)
    coord._on_chunk_finished(w_a, coord._chunk_results[id(w_a)])
    coord._on_chunk_finished(w_b, coord._chunk_results[id(w_b)])

    assert finished and finished[0]["n"] == 3
    h5_path = out_dir / "integrated.h5"
    assert h5_path.is_file()
    with h5py.File(h5_path, "r") as f:
        assert np.asarray(f["cake"]).shape[0] == 3
        bin_area = np.asarray(f["bin_area"])
        assert np.any(bin_area > 0), "bin_area should be populated, not left at zero"


def test_radial_axes_from_r_px_matches_widgets_convert_radial():
    """helpers.radial_axes_from_r_px (used by cake_hdf5.write_cake_h5 for the
    two_theta_deg/d_angstrom/q_invA axes) must agree with widgets'
    R/2theta/d/Q converter — same geometry, same numbers, no drift between
    the two independent call sites."""
    from midas_gui.helpers import radial_axes_from_r_px
    from midas_gui.widgets import _convert_radial

    lsd_um, px_um, wl_A = 1_000_000.0, 150.0, 0.1729
    r_px = np.array([0.0, 100.0, 1000.0, 5000.0])
    axes = radial_axes_from_r_px(r_px, lsd_um, px_um, wl_A)

    assert axes["two_theta_deg"][0] == pytest.approx(0.0)
    assert axes["d_angstrom"][0] == np.inf
    assert axes["q_invA"][0] == pytest.approx(0.0)

    expected_tth = _convert_radial(r_px, lsd_um, px_um, wl_A, "R", "2th")
    expected_d = _convert_radial(r_px, lsd_um, px_um, wl_A, "R", "d")
    expected_q = _convert_radial(r_px, lsd_um, px_um, wl_A, "R", "Q")
    np.testing.assert_allclose(axes["two_theta_deg"], expected_tth)
    np.testing.assert_allclose(axes["d_angstrom"][1:], expected_d[1:])
    np.testing.assert_allclose(axes["q_invA"], expected_q)


def test_radial_axes_from_r_px_without_wavelength():
    """d/Q are undefined without a wavelength — only two_theta_deg is returned."""
    from midas_gui.helpers import radial_axes_from_r_px
    axes = radial_axes_from_r_px(np.array([0.0, 100.0]), 1_000_000.0, 150.0)
    assert set(axes) == {"two_theta_deg"}


def test_project_h5_zarr_axes_and_data_are_consistent(app, in_dir):
    """The "thorough accuracy check": one multi-azimuth run's r-axis,
    eta-axis and raw cake data must agree bit-for-bit across the project
    attempt, the .h5, and the real GSAS-II .zarr.zip's REtaMap — a permanent
    regression test for the mismatch the user suspected (and couldn't be
    reproduced on the current on-disk files during investigation)."""
    h5py = pytest.importorskip("h5py")
    zarr = pytest.importorskip("zarr")
    from midas_gui import project

    proj_path = str(in_dir / "proj.h5")
    project.create_project(proj_path)

    out = in_dir / "out"
    results, _, spec = _run(app, in_dir, ["h5", "zarr"], n_frames=1, out_dir=out)

    calib = {"Lsd": float(spec.Lsd), "pxY": float(spec.pxY), "pxZ": float(spec.pxZ),
            "wavelength_A": float(spec.Wavelength), "NrPixelsY": 64, "NrPixelsZ": 64,
            "BC_y": 32.0, "BC_z": 32.0, "tx": 0.0, "ty": 0.0, "tz": 0.0, "distortion": {}}
    ref = project.append_integration_attempt(
        proj_path, "single",
        inputs={"kernel": "subpixel2", "r_bin": 2.0, "e_bin": 45.0, "multi_azimuth": True},
        finished_payload=results, calibration_snapshot=calib,
        extra={"n_eta_bins": int(spec.n_eta_bins),
              "eta_axis_deg": results["eta_axis"].tolist()})

    proj_results = project.read_attempt_results(proj_path, ref)
    proj_cake = np.asarray(proj_results["profiles"])          # (N, eta, r), raw per-bin mean
    proj_r = np.asarray(proj_results["r_axis_px"])

    h5_path = next((out / "h5").glob("*.h5"))
    with h5py.File(h5_path, "r") as f:
        h5_cake = np.asarray(f["cake"])                       # (N, eta, r), bin_area-weighted
        h5_profiles = np.asarray(f["profiles"])
        h5_bin_area = np.asarray(f["bin_area"])                # (eta, r)
        h5_r = np.asarray(f["r_px"])
        h5_eta = np.asarray(f["eta_deg"])
        h5_tth = np.asarray(f["two_theta_deg"])
        h5_q = np.asarray(f["q_invA"])

    np.testing.assert_allclose(proj_r, h5_r)

    # h5's cake is the project's raw per-bin mean, reweighted by each bin's
    # share of the total pixel-count coverage at that r — recompute that same
    # weight from the h5's own bin_area and check it reproduces h5's cake
    # exactly from the project's raw values, and that summing it over eta
    # reproduces the profile exactly (the property the user asked for).
    area_total = h5_bin_area.sum(axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        weight = np.where(area_total > 0, h5_bin_area / area_total[None, :], 0.0)
    np.testing.assert_allclose(h5_cake, proj_cake * weight[None, :, :], rtol=1e-8, atol=1e-8)
    np.testing.assert_allclose(h5_cake.sum(axis=1), h5_profiles, rtol=1e-8, atol=1e-8)

    zarr_path = next((out / "zarr").glob("*.zarr.zip"))
    try:
        zfp = zarr.open(str(zarr_path), mode="r")
    except Exception:
        store = zarr.storage.ZipStore(str(zarr_path), mode="r")
        zfp = zarr.open_group(store, mode="r")
    reta = np.asarray(zfp["REtaMap"])   # (5, n_r, n_eta): R, 2theta, eta, area, Q
    np.testing.assert_allclose(reta[0][:, 0], h5_r)
    np.testing.assert_allclose(reta[1][:, 0], h5_tth)
    np.testing.assert_allclose(reta[2][0, :], h5_eta)
    np.testing.assert_allclose(reta[4][:, 0], h5_q)
