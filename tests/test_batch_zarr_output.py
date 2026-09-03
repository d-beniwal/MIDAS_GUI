"""End-to-end tests for Batch Integrate's zarr cake output format.

Complements ``test_zarr_cake.py`` (which pins the file schema in isolation) by
driving a real ``BatchWorker`` run: the interesting part is the wiring — zarr
forces the cake to be computed even when multi-azimuth output is off, is
excluded from the per-frame text-format loop, and must not disturb the
existing profile/HDF5 paths.
"""
from types import SimpleNamespace

import numpy as np
import pytest


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


@pytest.fixture(scope="module")
def app():
    from PyQt5 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _run(app, tmp_path, fmts, *, multi_azimuth=False, n_frames=2, out_dir=None):
    pytest.importorskip("torch")
    pytest.importorskip("midas_integrate_v2")
    import midas_gui.workers as wk
    from midas_gui.helpers import _build_spec

    paths = _make_tiff_frames(tmp_path / "in", n=n_frames)
    spec = _build_spec(_tiny_calib_result(), r_bin=2.0, eta_bin=45.0)
    worker = wk.BatchWorker(
        spec, {"type": "tiff_list", "paths": paths}, None, out_dir, fmts,
        "subpixel2", (None, None), None, multi_azimuth=multi_azimuth)
    results, failures, logs = {}, [], []
    worker.finished.connect(results.update)
    worker.failed.connect(failures.append)
    worker.log_line.connect(logs.append)
    worker.run()   # direct call, not .start() — no real QThread spawned
    assert not failures, failures[0]
    return results, logs


@pytest.fixture
def in_dir(tmp_path):
    (tmp_path / "in").mkdir()
    return tmp_path


def test_zarr_output_is_written_with_every_frame(app, in_dir):
    zarr = pytest.importorskip("zarr")
    out = in_dir / "out"
    _run(app, in_dir, ["zarr"], n_frames=3, out_dir=out)

    path = out / "integrated.zarr.zip"
    assert path.is_file()
    root = zarr.open(zarr.ZipStore(str(path), mode="r"), mode="r")
    assert sorted(root["IntegrationResult"].array_keys()) == \
        [f"FrameNr_{i}" for i in range(3)]
    assert np.asarray(root["Omegas"]).size == 3


def test_zarr_output_works_with_multi_azimuth_off(app, in_dir):
    """want_cake has to be forced on by zarr alone — multi_azimuth defaults
    off, and without this the cake would never be computed and the store
    would come out empty."""
    zarr = pytest.importorskip("zarr")
    out = in_dir / "out"
    results, _ = _run(app, in_dir, ["zarr"], multi_azimuth=False, out_dir=out)

    # The returned profiles stay 1-D per frame (multi-azimuth is still off)...
    assert np.asarray(results["profiles"]).ndim == 2
    # ...but the zarr store still holds real 2-D cakes.
    root = zarr.open(zarr.ZipStore(str(out / "integrated.zarr.zip"), mode="r"),
                     mode="r")
    cake = np.asarray(root["IntegrationResult"]["FrameNr_0"])
    assert cake.ndim == 2 and min(cake.shape) > 1


def test_zarr_is_stamped_with_batch_provenance(app, in_dir):
    zarr = pytest.importorskip("zarr")
    out = in_dir / "out"
    _run(app, in_dir, ["zarr"], out_dir=out)
    root = zarr.open(zarr.ZipStore(str(out / "integrated.zarr.zip"), mode="r"),
                     mode="r")
    history = root.attrs["provenance_history"]
    assert history[0]["tool"] == "midas_gui.batch_integrate"
    assert "cake_params" in history[0]
    assert history[0]["extra"]["n_frames"] == 2


def test_zarr_is_excluded_from_the_per_frame_text_writer(app, in_dir):
    """"zarr" is a whole-run format like "h5"; it must not also produce a
    per-frame `<frame>.zarr` file."""
    out = in_dir / "out"
    _run(app, in_dir, ["zarr", "csv"], out_dir=out)
    names = sorted(p.name for p in out.iterdir())
    assert "integrated.zarr.zip" in names
    assert [n for n in names if n.endswith(".csv")], "csv still written"
    assert not [n for n in names if n.endswith(".zarr") and n != "integrated.zarr.zip"]


def test_h5_output_is_stamped_with_provenance(app, in_dir):
    h5py = pytest.importorskip("h5py")
    import json
    out = in_dir / "out"
    _run(app, in_dir, ["h5"], out_dir=out)
    with h5py.File(out / "integrated.h5", "r") as f:
        history = json.loads(f.attrs["provenance_history"])
    assert history[0]["tool"] == "midas_gui.batch_integrate"


def test_no_out_dir_writes_nothing_and_still_succeeds(app, in_dir):
    """Live-preview runs pass out_dir=None; zarr must be skipped, not crash."""
    results, _ = _run(app, in_dir, ["zarr"], out_dir=None)
    assert results["n"] == 2


def test_default_formats_produce_no_zarr(app, in_dir):
    """Regression guard: a run that didn't ask for zarr gets none."""
    out = in_dir / "out"
    _run(app, in_dir, ["csv"], out_dir=out)
    assert not list(out.glob("*.zarr*"))
