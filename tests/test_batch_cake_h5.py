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
        cake = np.asarray(f["cake"])
        assert cake.shape == (1, spec.n_r_bins, spec.n_eta_bins)
        assert np.asarray(f["cake_sigma"]).shape == cake.shape
        assert np.asarray(f["REtaMap"]).shape == (5, spec.n_r_bins, spec.n_eta_bins)
        assert np.asarray(f["SumFrames"]).shape == (spec.n_r_bins, spec.n_eta_bins)
        assert np.asarray(f["Omegas"]).shape == (1,)
        assert np.asarray(f["InstrumentParameters"]["Lam"]).shape == (1,)
        profiles = np.asarray(f["profiles"])
        assert profiles.shape == (1, spec.n_r_bins)
        calib_json = json.loads(f["entry"].attrs["calibration_snapshot_json"])
        assert calib_json["_calibrant_name"] == "CeO2"


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
    paths = wk.write_all_profiles(
        out_dir, ["h5"], r_axis, profiles, sigmas, frame_ids,
        float(spec.Lsd), float(spec.pxY), float(spec.Wavelength),
        eta_axis=eta_axis, spec=spec)
    assert paths == [str(out_dir / "integrated.h5")]

    with h5py.File(out_dir / "integrated.h5", "r") as f:
        assert np.asarray(f["cake"]).shape == (3, n_r, n_eta)
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
        bin_area = np.asarray(f["REtaMap"])[3]
        assert np.any(bin_area > 0), "BinArea row should be populated, not left at zero"
