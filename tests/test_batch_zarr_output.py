"""End-to-end tests for Batch Integrate's zarr cake output wiring.

Complements ``test_batch_zarr_gsas.py`` (which pins the GSAS-II-facing file
schema) by driving a real ``BatchWorker`` run: the interesting part here is the
wiring — zarr forces the cake to be computed even when multi-azimuth output is
off, is excluded from the per-frame text-format loop, and must not disturb the
existing profile/HDF5 paths.

Output layout (since the zarr writer was rewired onto ``write_gsas_zarr_zip``):
one zarr **per combined output frame** under ``<out>/zarr/<fid>.ave.zarr.zip``,
lineouts under ``<out>/<fmt>/``, and the single whole-run HDF5 under
``<out>/h5/``.
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


def test_zarr_output_is_written_once_per_frame(app, in_dir):
    """One zarr per combined output frame, named from that frame's id — not
    one bundled store for the whole run."""
    pytest.importorskip("zarr")
    out = in_dir / "out"
    _run(app, in_dir, ["zarr"], n_frames=3, out_dir=out)

    zips = sorted(p.name for p in (out / "zarr").glob("*.zarr.zip"))
    assert zips == [f"frame_{i:04d}.ave.zarr.zip" for i in range(3)]


def test_zarr_output_works_with_multi_azimuth_off(app, in_dir):
    """want_cake has to be forced on by zarr alone — multi_azimuth defaults
    off, and without this the cake would never be computed and the store
    would come out empty."""
    zarr = pytest.importorskip("zarr")
    out = in_dir / "out"
    results, _ = _run(app, in_dir, ["zarr"], multi_azimuth=False, out_dir=out)

    # The returned profiles stay 1-D per frame (multi-azimuth is still off)...
    assert np.asarray(results["profiles"]).ndim == 2
    # ...but the zarr store still holds a real 2-D cake.
    path = next((out / "zarr").glob("*.zarr.zip"))
    root = zarr.open(zarr.ZipStore(str(path), mode="r"), mode="r")
    cake = np.asarray(root["OmegaSumFrame"]["LastFrameNumber_0"])
    assert cake.ndim == 2 and min(cake.shape) > 1


def test_zarr_is_stamped_with_batch_provenance(app, in_dir):
    zarr = pytest.importorskip("zarr")
    out = in_dir / "out"
    _run(app, in_dir, ["zarr"], out_dir=out)
    path = next((out / "zarr").glob("*.zarr.zip"))
    root = zarr.open(zarr.ZipStore(str(path), mode="r"), mode="r")
    history = root.attrs["provenance_history"]
    assert history[0]["tool"] == "midas_gui.batch_integrate"
    assert "cake_params" in history[0]


def test_zarr_is_excluded_from_the_per_frame_text_writer(app, in_dir):
    """"zarr" is handled by its own writer; it must not also fall through to
    the lineout loop and produce a `<frame>.zarr` text file in csv/."""
    out = in_dir / "out"
    _run(app, in_dir, ["zarr", "csv"], out_dir=out)
    assert list((out / "zarr").glob("*.zarr.zip"))
    assert list((out / "csv").glob("*.csv")), "csv still written"
    assert not list(out.rglob("*.zarr")), "no bare .zarr from the text writer"


def test_h5_output_is_stamped_with_provenance(app, in_dir):
    h5py = pytest.importorskip("h5py")
    import json
    out = in_dir / "out"
    _run(app, in_dir, ["h5"], out_dir=out)
    h5_path = next((out / "h5").glob("*.h5"))
    with h5py.File(h5_path, "r") as f:
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
    assert not list(out.rglob("*.zarr*"))
