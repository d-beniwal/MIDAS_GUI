"""Workers that run in their own OS process (``proc_worker.process_isolated``).

The point of the isolation is that a native fault inside a worker (segfault,
abort, OOM-kill) fails that one job instead of the whole GUI — and with it every
other tab's running work. These tests pin that contract, plus signal fidelity.
"""
import os
import sys
import time

import numpy as np
import pytest
from PyQt5 import QtCore

from midas_gui import workers as wk
from midas_gui.proc_worker import ProcessWorker, process_isolated, _signal_types


@pytest.fixture(scope="module", autouse=True)
def _child_can_import_this_module():
    # The child re-imports the test workers below by module name.
    old = os.environ.get("PYTHONPATH", "")
    here = os.path.dirname(os.path.abspath(__file__))
    os.environ["PYTHONPATH"] = here + (os.pathsep + old if old else "")
    yield
    os.environ["PYTHONPATH"] = old


@pytest.fixture(scope="module")
def app():
    return QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])


def _pump(app, worker, timeout=60):
    t0 = time.time()
    while worker.isRunning() and time.time() - t0 < timeout:
        app.processEvents()
        time.sleep(0.01)
    for _ in range(5):
        app.processEvents()


# ── a tiny worker defined at module level so the child can import it by name ──
class _EchoImpl(QtCore.QThread):
    log_line = QtCore.pyqtSignal(str)
    step = QtCore.pyqtSignal(str, object)
    progress = QtCore.pyqtSignal(int, int)
    finished = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)

    def __init__(self, mode, payload=None, parent=None):
        super().__init__(parent)
        self._mode, self._payload = mode, payload

    def run(self):
        if self._mode == "ok":
            self.log_line.emit("hello")
            self.step.emit("s1", {"a": 1})
            self.progress.emit(3, 7)
            self.finished.emit(self._payload)
        elif self._mode == "segv":
            self.log_line.emit("about to die")
            os.kill(os.getpid(), 9)
        elif self._mode == "exit3":
            sys.exit(3)
        elif self._mode == "raise":
            raise RuntimeError("boom")
        elif self._mode == "sleep":
            time.sleep(60)


Echo = process_isolated(_EchoImpl)


class _NoFailImpl(QtCore.QThread):
    done = QtCore.pyqtSignal(object)

    def run(self):
        self.done.emit(1)


NoFail = process_isolated(_NoFailImpl)


def _collect(w):
    ev = []
    w.log_line.connect(lambda s: ev.append(("log", s)))
    w.step.connect(lambda n, p: ev.append(("step", n, p)))
    w.progress.connect(lambda a, b: ev.append(("prog", a, b)))
    w.finished.connect(lambda r: ev.append(("fin", r)))
    w.failed.connect(lambda s: ev.append(("fail", s)))
    return ev


def test_signal_signature_recovery():
    assert _signal_types(_EchoImpl.step) == (str, object)
    assert _signal_types(_EchoImpl.progress) == (int, int)
    assert _signal_types(_EchoImpl.finished) == (object,)
    assert issubclass(Echo, ProcessWorker)
    assert Echo.__name__ == "Echo"


def test_signals_round_trip_with_numpy_payload(app):
    arr = np.arange(12, dtype=np.float32).reshape(3, 4)
    w = Echo("ok", arr)
    ev = _collect(w)
    w.start()
    _pump(app, w)
    assert [e[0] for e in ev] == ["log", "step", "prog", "fin"]
    assert ev[1][1:] == ("s1", {"a": 1})
    assert ev[2][1:] == (3, 7)
    np.testing.assert_array_equal(ev[3][1], arr)
    assert w.isFinished()


def test_native_kill_fails_the_job_not_the_process(app):
    w = Echo("segv")
    ev = _collect(w)
    w.start()
    _pump(app, w)
    kinds = [e[0] for e in ev]
    assert "fin" not in kinds
    assert kinds.count("fail") == 1
    msg = [e for e in ev if e[0] == "fail"][0][1]
    assert "crashed" in msg and "about to die" in msg      # log tail is attached


def test_nonzero_exit_and_uncaught_exception_report_failure(app):
    for mode, needle in (("exit3", "SystemExit: 3"), ("raise", "boom")):
        w = Echo(mode)
        ev = _collect(w)
        w.start()
        _pump(app, w)
        fails = [e for e in ev if e[0] == "fail"]
        assert len(fails) == 1 and needle in fails[0][1], (mode, ev)


def test_abort_kills_child_and_is_silent(app):
    w = Echo("sleep")
    ev = _collect(w)
    w.start()
    t0 = time.time()
    while w.isRunning() is False and time.time() - t0 < 5:
        app.processEvents()
    time.sleep(0.5)
    w.requestInterruption()
    _pump(app, w, timeout=10)
    assert not w.isRunning()
    assert ev == []                                      # no failure noise after an abort


def test_worker_without_failed_signal_gets_plain_finished(app):
    w = NoFail()
    got = []
    w.done.connect(got.append)
    fin = []
    w.finished.connect(lambda: fin.append(1))
    w.start()
    _pump(app, w)
    assert got == [1] and fin == [1]


def test_run_is_synchronous_in_process(app):
    w = Echo("ok", 42)
    ev = _collect(w)
    w.run()
    assert ("fin", 42) in ev


def test_inprocess_escape_hatch(monkeypatch):
    monkeypatch.setenv("MIDAS_GUI_INPROCESS_WORKERS", "1")
    assert process_isolated(_EchoImpl) is _EchoImpl


@pytest.mark.parametrize("name", [
    "MaskComputeWorker", "IntegrationWorker", "RefinementWorker", "RefineCompareWorker",
    "CorrectionPreviewWorker", "PDFWorker", "PDFStructureFitWorker", "PoleFigureWorker",
    "LearnableGainWorker", "DriftWorker", "ManualDspacingCalibWorker"])
def test_real_workers_are_process_isolated(name):
    cls = getattr(wk, name)
    assert issubclass(cls, ProcessWorker)
    impl = cls._impl_cls
    # The child re-imports the implementation by name, so it must resolve.
    assert getattr(sys.modules[impl.__module__], impl.__qualname__) is impl


def test_mask_worker_end_to_end(app):
    rng = np.random.default_rng(0)
    img = rng.poisson(100, (128, 128)).astype(np.float32)
    img[10, 10] = 1e6
    w = wk.MaskComputeWorker(
        img, np.zeros(img.shape, np.uint8),
        {"stat": {"spatial": True, "temporal": False, "k_sigma": 5, "hot_factor": 10,
                  "dead_factor": 0.1, "frozen_frac": 0.05, "overflow": None}})
    out, err = [], []
    w.finished.connect(out.append)
    w.failed.connect(err.append)
    w.start()
    _pump(app, w, timeout=120)
    assert not err, err
    assert out and out[0].dtype == np.uint8 and out[0][10, 10] == 1
