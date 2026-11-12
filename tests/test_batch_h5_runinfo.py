"""Batch Integrate combined-HDF5 regressions (2026-11-11 audit).

Covers: the <lo>_<hi> filename token in the plain 1-D layout, Batch-Parallel
writing exactly one file (same name/place as sequential, equal content),
rebin attrs describing the stored axis, the /instrument, /frame_metadata and
/monitor_values additions, and the parallel monitor-file offset.
"""
import re
from types import SimpleNamespace

import numpy as np
import pytest

pytestmark = pytest.mark.forked


def _calib():
    return SimpleNamespace(
        Lsd=200000.0, BC_y=32.0, BC_z=32.0, tx=0.1, ty=0.2, tz=0.3,
        distortion={}, pxY=200.0, pxZ=200.0, NrPixelsY=64, NrPixelsZ=64,
        wavelength_A=0.1729)


@pytest.fixture(scope="module")
def app():
    from PyQt5 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _frames(tmp_path, n):
    tifffile = pytest.importorskip("tifffile")
    rng = np.random.default_rng(0)
    out = []
    for i in range(n):
        p = tmp_path / f"frame_{i:04d}.tif"
        tifffile.imwrite(str(p), (rng.random((64, 64)) * 100 + 10).astype(np.float32))
        out.append(str(p))
    return out


def _args(tmp_path, n, **kw):
    pytest.importorskip("torch")
    pytest.importorskip("midas_integrate_v2")
    from midas_gui.helpers import _build_spec
    spec = _build_spec(_calib(), r_bin=2.0, eta_bin=45.0)
    return dict(spec=spec, source_cfg={"type": "tiff_list", "paths": _frames(tmp_path, n)},
                mask=None, out_dir=tmp_path / "out", fmts=["h5"], kernel="subpixel2",
                corrections=(None, None), variance_cfg=None, **kw)


def _run_seq(args):
    import midas_gui.workers as wk
    w = wk.BatchWorker(**args)
    res, fails = {}, []
    w.finished.connect(res.update)
    w.failed.connect(fails.append)
    w.run()
    assert not fails, fails[0]
    return res


def _run_par(app, args, nw=2):
    from PyQt5 import QtCore
    import midas_gui.workers as wk
    loop = QtCore.QEventLoop()
    c = wk.BatchRunCoordinator(run_mode="batch_parallel", n_workers=nw, **args)
    res, fails = {}, []
    c.finished.connect(lambda r: (res.update(r), loop.quit()))
    c.failed.connect(lambda m: (fails.append(m), loop.quit()))
    QtCore.QTimer.singleShot(240000, loop.quit)
    c.start()
    loop.exec_()
    c.wait(20000)
    assert not fails, fails[0]
    assert res
    return res


def test_plain_1d_filename_carries_the_processed_range(app, tmp_path):
    h5py = pytest.importorskip("h5py")
    _run_seq(_args(tmp_path, 8, frame_range=(2, 7, 2)))
    (p,) = (tmp_path / "out" / "h5").glob("*.h5")
    m = re.search(r"\.(\d{6})_(\d{6})\.cake", p.name)
    assert m and (int(m[1]), int(m[2])) == (2, 6), p.name


def test_run_info_groups_and_rebin_attrs(app, tmp_path):
    h5py = pytest.importorskip("h5py")
    cfg = {"unit": "Q", "min": 0.5, "max": 5.0, "step": 0.05}
    res = _run_seq(_args(tmp_path, 3, q_cfg=cfg))
    (p,) = (tmp_path / "out" / "h5").glob("*.h5")
    with h5py.File(p) as f:
        n_axis = len(f["r_axis_px"])
        assert n_axis == 90 and int(f.attrs["n_r_bins"]) == n_axis
        assert f.attrs["rebin_unit"] == "Q" and f.attrs["rebin_step"] == 0.05
        assert float(f["instrument"].attrs["Lsd"]) == 200000.0
        assert float(f["instrument"].attrs["tx"]) == pytest.approx(0.1)
        assert "distortion" in f["instrument"]
        assert len(f["omegas"]) == len(res["frame_ids"]) == 3


def test_monitor_values_recorded(app, tmp_path):
    h5py = pytest.importorskip("h5py")
    mon = tmp_path / "mon.txt"
    mon.write_text("2\n4\n8\n")
    _run_seq(_args(tmp_path, 3, monitor_file=str(mon)))
    (p,) = (tmp_path / "out" / "h5").glob("*.h5")
    with h5py.File(p) as f:
        np.testing.assert_array_equal(f["monitor_values"][()], [2.0, 4.0, 8.0])


@pytest.mark.parametrize("multi", [False, True])
def test_parallel_writes_one_file_equal_to_sequential(app, tmp_path, multi):
    h5py = pytest.importorskip("h5py")
    n = 24
    mon = tmp_path / "mon.txt"
    mon.write_text("\n".join(str(1.0 + i) for i in range(n)))
    frames_dir = tmp_path / "src"
    frames_dir.mkdir()
    seq_dir, par_dir = tmp_path / "seq", tmp_path / "par"
    base = _args(frames_dir, n, multi_azimuth=multi, monitor_file=str(mon))
    _run_seq({**base, "out_dir": seq_dir})
    _run_par(app, {**base, "out_dir": par_dir})
    (a,) = (seq_dir / "h5").glob("*.h5")
    (b,) = (par_dir / "h5").glob("*.h5")           # exactly one, same place
    assert a.name == b.name
    with h5py.File(a) as fa, h5py.File(b) as fb:
        assert fa["frame_ids"][()].tolist() == fb["frame_ids"][()].tolist()
        np.testing.assert_allclose(fa["profiles"][()], fb["profiles"][()])
        np.testing.assert_allclose(fa["omegas"][()], fb["omegas"][()])
        # chunk 2 must index the monitor file from its own offset, not 0
        np.testing.assert_array_equal(fb["monitor_values"][()], fa["monitor_values"][()])
        if multi:
            np.testing.assert_allclose(fa["cake"][()], fb["cake"][()])
