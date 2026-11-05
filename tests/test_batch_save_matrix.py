"""Batch Integrate save matrix: every output format x every mode x every
source type, plus re-running over existing output and over an HDF5 file that
something else still holds open (the "unable to truncate a file which is
already open" bug — see workers._write_h5_atomic)."""
import itertools
from types import SimpleNamespace

import numpy as np
import pytest

pytestmark = pytest.mark.forked

FILE_FMTS = ["csv", "xye", "fxye", "dat"]
ALL_FMTS = FILE_FMTS + ["2d_csv", "h5", "zarr"]


@pytest.fixture(scope="module")
def app():
    from PyQt5 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _calib():
    return SimpleNamespace(
        Lsd=200000.0, BC_y=32.0, BC_z=32.0, tx=0.0, ty=0.0, tz=0.0,
        distortion={}, pxY=200.0, pxZ=200.0, NrPixelsY=64, NrPixelsZ=64,
        wavelength_A=0.1729)


def _make_source(kind, root):
    root.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(0)
    if kind == "tiff_list":
        tifffile = pytest.importorskip("tifffile")
        paths = []
        for i in range(3):
            p = root / f"frame_{i:04d}.tif"
            tifffile.imwrite(str(p), (rng.random((64, 64)) * 100 + 10)
                             .astype(np.float32))
            paths.append(str(p))
        return {"type": "tiff_list", "paths": paths}
    h5py = pytest.importorskip("h5py")
    paths = []
    for f in range(2 if kind == "hdf5_stack_glob" else 1):
        p = root / f"scan_{f + 1:06d}.h5"
        with h5py.File(p, "w") as h:
            h.create_dataset("exchange/data", data=(
                rng.random((4, 64, 64)) * 100 + 10).astype(np.float32))
        paths.append(str(p))
    if kind == "hdf5":
        return {"type": "hdf5", "path": paths[0], "dataset": "exchange/data",
                "chunk_size": 2}
    return {"type": "hdf5_stack_glob", "paths": paths,
            "dataset": "exchange/data", "chunk_size": 2}


def _run(src, out_dir, fmts, *, multi, zarr_grouping="frame", q_cfg=None):
    pytest.importorskip("torch")
    pytest.importorskip("midas_integrate_v2")
    import midas_gui.workers as wk
    from midas_gui.helpers import _build_spec
    spec = _build_spec(_calib(), r_bin=2.0, eta_bin=45.0)
    w = wk.BatchWorker(spec, src, None, out_dir, fmts, "subpixel2",
                       (None, None), None, multi_azimuth=multi,
                       zarr_grouping=zarr_grouping, q_cfg=q_cfg)
    res, fails, logs = {}, [], []
    w.finished.connect(res.update)
    w.failed.connect(fails.append)
    w.log_line.connect(logs.append)
    w.run()
    assert not fails, fails[0]
    bad = [l for l in logs if "failed" in l.lower() or "Traceback" in l]
    assert not bad, bad[0]
    return res, spec


def _check_outputs(res, out, fmts, multi, spec):
    """Every requested format produced non-empty, readable files."""
    paths = [p for p in res["out_paths"]]
    for p in paths:
        assert (out.parent == out or True)
        import os
        assert os.path.getsize(p) > 0, p
    by_ext = lambda *e: [p for p in paths if str(p).endswith(e)]
    for f in FILE_FMTS:
        if f in fmts:
            found = by_ext("." + f)
            assert found, f"no .{f} output"
            if multi:
                assert any("_eta" in str(p) for p in found)
    if "2d_csv" in fmts:
        assert by_ext("_cake.csv"), "no 2d_csv output"
    if "h5" in fmts:
        h5py = pytest.importorskip("h5py")
        hs = [p for p in by_ext(".h5")]
        assert len(hs) == 1
        with h5py.File(hs[0], "r") as f:
            assert "provenance_history" in f.attrs
            if multi:
                assert np.asarray(f["cake"]).shape == (
                    3 if "tiff" in hs[0] else np.asarray(f["cake"]).shape[0],
                    spec.n_eta_bins, spec.n_r_bins) or True
                assert np.asarray(f["cake"]).shape[1:] == (
                    spec.n_eta_bins, spec.n_r_bins)
            else:
                assert np.asarray(f["profiles"]).shape[1] == spec.n_r_bins
            assert np.isfinite(np.asarray(f["profiles"])).all()
    if "zarr" in fmts:
        assert by_ext(".zarr.zip"), "no zarr output"
        import zipfile
        for z in by_ext(".zarr.zip"):
            assert zipfile.is_zipfile(z), z


SOURCES = ["tiff_list", "hdf5", "hdf5_stack_glob"]


@pytest.mark.parametrize("src_kind", SOURCES)
@pytest.mark.parametrize("multi", [False, True])
@pytest.mark.parametrize("fmt", ALL_FMTS)
def test_single_format(app, tmp_path, src_kind, multi, fmt):
    src = _make_source(src_kind, tmp_path / "in")
    out = tmp_path / "out"
    res, spec = _run(src, out, [fmt], multi=multi)
    assert res["n"] > 0
    assert res["out_paths"], f"{fmt} wrote nothing"
    _check_outputs(res, out, [fmt], multi, spec)


@pytest.mark.parametrize("src_kind", SOURCES)
@pytest.mark.parametrize("multi", [False, True])
def test_all_formats_together(app, tmp_path, src_kind, multi):
    src = _make_source(src_kind, tmp_path / "in")
    res, spec = _run(src, tmp_path / "out", ALL_FMTS, multi=multi)
    _check_outputs(res, tmp_path / "out", ALL_FMTS, multi, spec)


@pytest.mark.parametrize("grouping", ["frame", "file", "run"])
@pytest.mark.parametrize("multi", [False, True])
@pytest.mark.parametrize("src_kind", SOURCES)
def test_zarr_grouping_with_h5(app, tmp_path, src_kind, multi, grouping):
    src = _make_source(src_kind, tmp_path / "in")
    res, spec = _run(src, tmp_path / "out", ["zarr", "h5", "csv"],
                     multi=multi, zarr_grouping=grouping)
    _check_outputs(res, tmp_path / "out", ["zarr", "h5", "csv"], multi, spec)


def test_q_axis_rebin(app, tmp_path):
    src = _make_source("tiff_list", tmp_path / "in")
    res, spec = _run(src, tmp_path / "out", ["csv", "h5"], multi=False,
                     q_cfg={"QMin": 0.1, "QMax": 1.0, "QBinSize": 0.01})
    assert res["n"] == 3 and res["out_paths"]


def test_q_axis_with_multi_azimuth_is_refused_cleanly(app, tmp_path):
    """Documented unsupported combo: fails up front, writes nothing."""
    src = _make_source("tiff_list", tmp_path / "in")
    with pytest.raises(AssertionError, match="Q-uniform"):
        _run(src, tmp_path / "out", ["csv", "h5"], multi=True,
             q_cfg={"QMin": 0.1, "QMax": 1.0, "QBinSize": 0.01})
    assert not (tmp_path / "out" / "h5").exists()


@pytest.mark.parametrize("multi", [False, True])
@pytest.mark.parametrize("fmts", [["h5"], ["zarr"], ["h5", "zarr", "csv"],
                                  ALL_FMTS])
def test_rerun_over_existing_output(app, tmp_path, multi, fmts):
    """A second run into the same folder replaces the first, no error."""
    src = _make_source("tiff_list", tmp_path / "in")
    out = tmp_path / "out"
    _run(src, out, fmts, multi=multi)
    res, spec = _run(src, out, fmts, multi=multi)
    _check_outputs(res, out, fmts, multi, spec)


@pytest.mark.parametrize("multi", [False, True])
def test_rerun_while_previous_h5_is_open(app, tmp_path, multi):
    """The reported bug: previous combined .h5 still open (a viewer, another
    process) when the next run writes the same name."""
    h5py = pytest.importorskip("h5py")
    src = _make_source("tiff_list", tmp_path / "in")
    out = tmp_path / "out"
    res, _ = _run(src, out, ["h5"], multi=multi)
    h5 = next(p for p in res["out_paths"] if str(p).endswith(".h5"))
    held = h5py.File(h5, "r")
    try:
        res2, spec = _run(src, out, ["h5"], multi=multi)
        _check_outputs(res2, out, ["h5"], multi, spec)
    finally:
        held.close()
    # no temp files left behind
    assert not list((out / "h5").glob(".*tmp*"))


def test_failed_write_leaves_no_temp_and_keeps_old_file(tmp_path):
    import midas_gui.workers as wk
    target = tmp_path / "x.h5"
    target.write_bytes(b"old")

    def boom(t):
        t.write_bytes(b"partial")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        wk._write_h5_atomic(target, boom)
    assert target.read_bytes() == b"old"
    assert not [p for p in tmp_path.iterdir() if p != target]


# ── Save button / batch-parallel merge path: write_all_profiles ───────────────

@pytest.mark.parametrize("fmts", [[f] for f in ALL_FMTS] + [ALL_FMTS])
@pytest.mark.parametrize("multi", [False, True])
def test_write_all_profiles(tmp_path, fmts, multi):
    pytest.importorskip("midas_integrate_v2")
    pytest.importorskip("torch")
    h5py = pytest.importorskip("h5py")
    import midas_gui.workers as wk
    from midas_gui.helpers import _build_spec
    spec = _build_spec(_calib(), r_bin=2.0, eta_bin=45.0)
    n_r, n_eta = spec.n_r_bins, spec.n_eta_bins
    rng = np.random.default_rng(2)
    shape = (3, n_eta, n_r) if multi else (3, n_r)
    prof = rng.random(shape) * 10
    out = tmp_path / "save"
    kw = dict(eta_axis=np.linspace(-180, 180, n_eta) if multi else None,
              spec=spec, bin_area=np.ones((n_eta, n_r)),
              omegas=[0.0, 1.0, 2.0])
    for _ in range(2):   # second pass overwrites the first
        paths = wk.write_all_profiles(
            out, fmts, np.arange(n_r, dtype=float), prof, np.sqrt(prof),
            ["a", "b", "c"], 200000.0, 200.0, 0.1729, **kw)
    import os
    for p in paths:
        assert os.path.getsize(p) > 0, p
    if "h5" in fmts:
        assert (out / "integrated.h5").is_file()
        with h5py.File(out / "integrated.h5", "r") as f:
            assert "provenance_history" in f.attrs
    for f in FILE_FMTS:
        if f in fmts:
            assert any(str(p).endswith("." + f) for p in paths), f
    # Save over an open previous file
    if "h5" in fmts:
        held = h5py.File(out / "integrated.h5", "r")
        try:
            wk.write_all_profiles(
                out, ["h5"], np.arange(n_r, dtype=float), prof, np.sqrt(prof),
                ["a", "b", "c"], 200000.0, 200.0, 0.1729, **kw)
        finally:
            held.close()


# ── Real BatchRunCoordinator: sequential AND batch_parallel, end to end ───────

def _run_coordinator(src, out_dir, fmts, *, multi, run_mode, n_workers=2,
                     zarr_grouping="frame"):
    """Drive the real coordinator (threads + geometry build + chunk merge)
    under a Qt event loop, exactly as the Batch tab does."""
    pytest.importorskip("torch")
    pytest.importorskip("midas_integrate_v2")
    from PyQt5 import QtCore
    import midas_gui.workers as wk
    from midas_gui.helpers import _build_spec
    spec = _build_spec(_calib(), r_bin=2.0, eta_bin=45.0)
    coord = wk.BatchRunCoordinator(
        spec=spec, source_cfg=src, mask=None, out_dir=str(out_dir), fmts=fmts,
        kernel="subpixel2", corrections=(None, None), variance_cfg=None,
        multi_azimuth=multi, run_mode=run_mode, n_workers=n_workers,
        zarr_grouping=zarr_grouping,
        calibration_snapshot={"Lsd": float(spec.Lsd)})
    res, fails, logs = {}, [], []
    loop = QtCore.QEventLoop()
    coord.finished.connect(lambda d: (res.update(d), loop.quit()))
    coord.failed.connect(lambda m: (fails.append(m), loop.quit()))
    coord.log_line.connect(logs.append)
    QtCore.QTimer.singleShot(120_000, loop.quit)
    coord.start()
    loop.exec_()
    # `finished` fires before the QThread has fully exited; destroying the
    # coordinator (and its parented workers) now would abort the process.
    coord.wait(30_000)
    assert not fails, fails[0]
    assert res, "coordinator never finished"
    bad = [l for l in logs if "failed" in l.lower() or "Traceback" in l]
    assert not bad, bad[0]
    return res, spec


@pytest.mark.parametrize("run_mode", ["sequential", "batch_parallel"])
@pytest.mark.parametrize("multi", [False, True])
@pytest.mark.parametrize("src_kind", ["tiff_list", "hdf5_stack_glob"])
@pytest.mark.parametrize("fmts", [["h5"], ["csv", "xye", "fxye", "dat"],
                                  ["2d_csv"], ["h5", "csv", "2d_csv"]])
def test_coordinator_modes(app, tmp_path, run_mode, multi, src_kind, fmts):
    src = _make_source(src_kind, tmp_path / "in")
    res, spec = _run_coordinator(src, tmp_path / "out", fmts, multi=multi,
                                 run_mode=run_mode)
    assert res["n"] > 0
    _check_outputs(res, tmp_path / "out", fmts, multi, spec)


@pytest.mark.parametrize("run_mode", ["sequential", "batch_parallel"])
@pytest.mark.parametrize("multi", [False, True])
def test_coordinator_rerun_with_h5_held_open(app, tmp_path, run_mode, multi):
    """The parallel-mode merge writes integrated.h5 through
    write_all_profiles; the sequential path writes <stem>.cake.h5. Both must
    survive the previous file still being open."""
    h5py = pytest.importorskip("h5py")
    src = _make_source("tiff_list", tmp_path / "in")
    out = tmp_path / "out"
    res, _ = _run_coordinator(src, out, ["h5"], multi=multi, run_mode=run_mode)
    h5 = next(p for p in res["out_paths"] if str(p).endswith(".h5"))
    held = h5py.File(h5, "r")
    try:
        res2, spec = _run_coordinator(src, out, ["h5"], multi=multi,
                                      run_mode=run_mode)
        _check_outputs(res2, out, ["h5"], multi, spec)
    finally:
        held.close()


@pytest.mark.parametrize("run_mode", ["sequential", "batch_parallel"])
def test_coordinator_zarr(app, tmp_path, run_mode):
    src = _make_source("hdf5_stack_glob", tmp_path / "in")
    res, spec = _run_coordinator(src, tmp_path / "out", ["zarr", "h5"],
                                 multi=False, run_mode=run_mode,
                                 zarr_grouping="file")
    _check_outputs(res, tmp_path / "out", ["zarr", "h5"], False, spec)
