from midas_gui.tab_batch import progress_percent


def test_progress_percent_rounds_and_caps():
    assert progress_percent(0, 0) == 0
    assert progress_percent(0, 10) == 0
    assert progress_percent(1, 3) == 33
    assert progress_percent(2, 3) == 67          # rounded, not truncated (66)
    assert progress_percent(199, 200) == 99      # never 100 with a frame left
    assert progress_percent(999, 1000) == 99
    assert progress_percent(200, 200) == 100
    assert progress_percent(201, 200) == 100


# ── "finalizing" log line, sequential and parallel ───────────────────────

import numpy as np
import pytest
from types import SimpleNamespace

_FINAL = "finalizing outputs"


@pytest.fixture(scope="module")
def app():
    from PyQt5 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _frames(tmp_path, n, size=64):
    tifffile = pytest.importorskip("tifffile")
    rng = np.random.default_rng(0)
    out = []
    for i in range(n):
        p = tmp_path / f"frame_{i:04d}.tif"
        tifffile.imwrite(str(p), (rng.random((size, size)) * 100 + 10).astype(np.float32))
        out.append(str(p))
    return out


def _spec():
    from midas_gui.helpers import _build_spec
    calib = SimpleNamespace(
        Lsd=200000.0, BC_y=32.0, BC_z=32.0, tx=0.0, ty=0.0, tz=0.0,
        distortion={}, pxY=200.0, pxZ=200.0, NrPixelsY=64, NrPixelsZ=64,
        wavelength_A=0.1729)
    return _build_spec(calib, r_bin=2.0, eta_bin=360.0)


def test_sequential_logs_finalizing_after_last_progress(app, tmp_path):
    pytest.importorskip("torch"); pytest.importorskip("midas_integrate_v2")
    import midas_gui.workers as wk
    events = []
    w = wk.BatchWorker(_spec(), {"type": "tiff_list", "paths": _frames(tmp_path, 3)},
                       None, None, [], "subpixel2", (None, None), None)
    w.progress.connect(lambda d, t: events.append(("p", d, t)))
    w.log_line.connect(lambda s: events.append(("l", s)))
    w.run()
    log_idx = [i for i, e in enumerate(events) if e[0] == "l" and _FINAL in e[1]]
    last_p = max(i for i, e in enumerate(events) if e[0] == "p")
    assert len(log_idx) == 1 and log_idx[0] > last_p


def test_parallel_logs_finalizing_once_for_the_merge(app, tmp_path):
    pytest.importorskip("torch"); pytest.importorskip("midas_integrate_v2")
    from PyQt5 import QtCore
    import midas_gui.workers as wk
    n = 2 * wk.BatchRunCoordinator.MIN_FRAMES_PER_WORKER
    c = wk.BatchRunCoordinator(
        _spec(), {"type": "tiff_list", "paths": _frames(tmp_path, n)}, None,
        None, [], "subpixel2", (None, None), None,
        run_mode="batch_parallel", n_workers=2)
    logs, fails, done = [], [], []
    loop = QtCore.QEventLoop()
    c.log_line.connect(logs.append)
    c.failed.connect(lambda m: (fails.append(m), loop.quit()))
    c.finished.connect(lambda d: (done.append(d), loop.quit()))
    QtCore.QTimer.singleShot(120_000, loop.quit)
    c.start()
    loop.exec_()
    assert not fails, fails[0]
    assert done, "parallel run did not finish"
    assert sum(_FINAL in s for s in logs) == 1
