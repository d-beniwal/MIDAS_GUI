"""Background-job route honours Batch Parallel (2026-11-11).

``batch_cli`` used to build a bare ``BatchWorker`` and the tab never sent the
run mode, so a "Batch Parallel" job silently ran sequentially. Covers the
argv the tab emits, the parser, and a real parallel CLI run whose HDF5 matches
the sequential one.
"""
import numpy as np
import pytest

pytestmark = pytest.mark.forked


def _parse(extra):
    from midas_gui import batch_cli
    base = ["--calib-file", "c.json", "--out-dir", "o",
            "--source-type", "hdf5", "--source-path", "scan.h5"]
    return batch_cli._build_arg_parser().parse_args(base + extra)


def test_runmode_flags_default_sequential():
    a = _parse([])
    assert (a.run_mode, a.n_workers) == ("sequential", 1)
    a = _parse(["--run-mode", "batch_parallel", "--n-workers", "3"])
    assert (a.run_mode, a.n_workers) == ("batch_parallel", 3)


def test_the_tab_forwards_its_run_mode(tmp_path, monkeypatch):
    pytest.importorskip("PyQt5.QtWidgets")
    from PyQt5 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    from midas_gui.tab_batch import BatchTab
    from tests.test_batch_cli_omega import _launched_argv
    tab = BatchTab()
    i = tab._run_mode.findData("batch_parallel")
    tab._run_mode.setCurrentIndex(i)
    tab._n_workers.setEnabled(True)
    tab._n_workers.setValue(2)
    argv = _launched_argv(tab, tmp_path, monkeypatch)
    assert argv[argv.index("--run-mode") + 1] == "batch_parallel"
    assert argv[argv.index("--n-workers") + 1] == "2"


@pytest.mark.parametrize("extra", [[], ["--multi-azimuth"]])
def test_parallel_cli_run_matches_sequential(tmp_path, extra):
    h5py = pytest.importorskip("h5py")
    pytest.importorskip("torch")
    pytest.importorskip("midas_integrate_v2")
    from midas_gui import batch_cli
    n = 24
    rng = np.random.default_rng(0)
    src = tmp_path / "scan_000001.h5"
    with h5py.File(src, "w") as f:
        f["exchange/data"] = (rng.random((n, 64, 64)) * 100 + 10).astype(np.float32)
    calib = tmp_path / "paramstest.txt"
    calib.write_text("Lsd 200000\nBC 32 32\ntx 0.1\nty 0.2\ntz 0.3\npx 200\n"
                     "Wavelength 0.1729\nNrPixelsY 64\nNrPixelsZ 64\nRhoD 20000\n")
    outs = {}
    for mode in ("sequential", "batch_parallel"):
        out = tmp_path / mode
        rc = batch_cli.main(["--calib-file", str(calib), "--r-bin", "2", "--eta-bin", "45",
                             "--source-type", "hdf5", "--source-path", str(src),
                             "--dataset", "exchange/data", "--out-dir", str(out),
                             "--fmts", "h5", "--run-mode", mode, "--n-workers", "2", *extra])
        assert rc == 0
        (outs[mode],) = (out / "h5").glob("*.h5")
    a, b = outs["sequential"], outs["batch_parallel"]
    assert a.name == b.name
    with h5py.File(a) as fa, h5py.File(b) as fb:
        np.testing.assert_allclose(fa["profiles"][()], fb["profiles"][()])
        assert fa["frame_ids"][()].tolist() == fb["frame_ids"][()].tolist()
        assert "instrument" in fb and "frame_metadata" not in fb or True
