"""Run a ``workers.py`` QThread worker in its own OS process.

``process_isolated(Impl)`` returns a drop-in replacement for the QThread
subclass ``Impl``: same constructor arguments, same signals (same names and
argument types), same ``start()/isRunning()/requestInterruption()/wait()``
surface — but ``run()`` executes in a child process (``proc_cli.py``) and every
signal it emits is relayed back to the GUI thread.

Why: workers that call torch / OpenMP / HDF5 / scipy inside the GUI process can
take the whole GUI down on a native fault (segfault, abort, out-of-memory kill),
losing every other tab's running work with it. In a child process the same
fault kills only that one job, and the GUI reports it through the worker's own
``failed`` signal like any other error. ``calib_cli``/``CalibrationWorker``
already did this for calibration; this is the generic version.

When *not* to use it: workers that hand the GUI a live object (a callback, a
Qt widget, an open file), that stream results faster than a pickle round-trip
makes sense, or whose abort must stop *cooperatively* and still deliver a
partial result (``requestInterruption()`` here is a hard kill, exactly like
``CalibrationWorker``). Arguments and every emitted payload must be picklable.

Debug escape hatch: set ``MIDAS_GUI_INPROCESS_WORKERS=1`` to get the original
in-process QThread classes back (e.g. to attach a debugger).
"""
from __future__ import annotations

import os
import pickle
import re
import shutil
import sys
import tempfile
import traceback
from collections import deque
from pathlib import Path
from typing import Optional

from PyQt5 import QtCore

from midas_gui.proc_cli import JOB_PKL, MSG_PREFIX, own_signal_names

INPROC_ENV = "MIDAS_GUI_INPROCESS_WORKERS"

_QT_TO_PY = {"QString": str, "PyQt_PyObject": object, "int": int,
             "double": float, "float": float, "bool": bool}


def _signal_types(sig) -> tuple:
    """Python argument types of an unbound ``pyqtSignal`` — recovered from its
    repr (``<unbound PYQT_SIGNAL name(QString,PyQt_PyObject)>``), which is the
    only place PyQt exposes the signature of a signal declared on a class."""
    m = re.search(r"\((.*)\)>?$", str(sig))
    inner = (m.group(1) if m else "").strip()
    if not inner:
        return ()
    return tuple(_QT_TO_PY.get(t.strip(), object) for t in inner.split(","))


class ProcessWorker(QtCore.QObject):
    """Base of every proxy built by :func:`process_isolated` (not used directly)."""

    _impl_cls = None          # the original QThread subclass, set per proxy
    _signal_names: tuple = ()

    def __init__(self, *args, parent=None, **kwargs):
        super().__init__(parent)
        self._args = args
        self._kwargs = kwargs
        self._proc: Optional[QtCore.QProcess] = None
        self._job_dir: Optional[Path] = None
        self._buf = ""
        self._tail = deque(maxlen=200)
        self._cancelled = False
        self._terminal_seen = False   # a worker-emitted failed/finished was relayed
        self._finished_flag = False

    # ── QThread-compatible surface ───────────────────────────────────────
    def isRunning(self) -> bool:
        return self._proc is not None and self._proc.state() != QtCore.QProcess.NotRunning

    def isFinished(self) -> bool:
        return self._finished_flag

    def isInterruptionRequested(self) -> bool:
        return self._cancelled

    def requestInterruption(self):
        """Hard-stop the job (SIGKILL the child). Whatever it had not yet
        reported is discarded; no further signal reaches the GUI."""
        self._cancelled = True
        if self._proc is not None and self.isRunning():
            self._proc.kill()

    def terminate(self):
        self.requestInterruption()

    def quit(self):
        self.requestInterruption()

    def wait(self, msecs: int = 2 ** 31 - 1) -> bool:
        if self._proc is None:
            return True
        return bool(self._proc.waitForFinished(int(msecs)))

    def run(self):
        """Synchronous, *in-process* execution — for callers (tests, scripts)
        that invoke ``worker.run()`` directly instead of ``start()``. Builds
        the original worker and forwards its signals to ours."""
        impl = type(self)._impl_cls(*self._args, **self._kwargs)
        for name in self._signal_names:
            getattr(impl, name).connect(getattr(self, name))
        impl.run()

    def start(self):
        from midas_gui.workers import PROJECT_ROOT, _pythonpath_with_repo_root
        impl = type(self)._impl_cls
        self._cancelled = False
        self._terminal_seen = False
        self._finished_flag = False
        try:
            self._job_dir = Path(tempfile.mkdtemp(prefix="midas_job_"))
            with open(self._job_dir / JOB_PKL, "wb") as fh:
                pickle.dump({"module": impl.__module__, "qualname": impl.__qualname__,
                             "args": self._args, "kwargs": self._kwargs},
                            fh, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception:
            msg = traceback.format_exc()
            self._cleanup()
            QtCore.QTimer.singleShot(0, lambda: self._report_failure(
                "could not hand the job to a worker process:\n" + msg))
            return

        proc = QtCore.QProcess(self)
        env = QtCore.QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONPATH", _pythonpath_with_repo_root(env.value("PYTHONPATH", "")))
        env.insert("PYTHONUTF8", "1")
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PYTHONUNBUFFERED", "1")
        proc.setProcessEnvironment(env)
        proc.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        proc.setWorkingDirectory(str(PROJECT_ROOT))
        proc.readyReadStandardOutput.connect(self._on_stdout)
        proc.finished.connect(self._on_process_finished)
        proc.errorOccurred.connect(self._on_process_error)
        self._proc = proc
        self._buf = ""
        proc.start(sys.executable, ["-m", "midas_gui.proc_cli",
                                    "--job-dir", str(self._job_dir)])

    # ── child → GUI relay ────────────────────────────────────────────────
    def _on_stdout(self):
        if self._proc is None:
            return
        self._buf += bytes(self._proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._handle_line(line)

    def _handle_line(self, line: str):
        if not line.startswith(MSG_PREFIX):
            if line.strip():
                self._tail.append(line)
            return
        if self._cancelled or self._job_dir is None:
            return
        path = self._job_dir / line[len(MSG_PREFIX):].strip()
        try:
            with open(path, "rb") as fh:
                name, args = pickle.load(fh)
            path.unlink(missing_ok=True)
        except Exception:
            self._tail.append("could not read worker message: " + traceback.format_exc())
            return
        if name in ("failed", "finished"):
            self._terminal_seen = True
        elif name == "log_line" and args:
            self._tail.append(str(args[0]))     # context for a later crash report
        getattr(self, name).emit(*args)

    def _report_failure(self, msg: str):
        if self._cancelled:
            return
        if "failed" in self._signal_names:
            self.failed.emit(msg)
        else:
            sys.stderr.write(msg + "\n")

    def _on_process_error(self, _err):
        # FailedToStart never emits finished(); report from here.
        if self._proc is not None and self._proc.error() == QtCore.QProcess.FailedToStart:
            self._finished_flag = True
            self._cleanup()
            self._report_failure(f"could not start the worker process "
                                 f"({sys.executable} -m midas_gui.proc_cli)")

    def _on_process_finished(self, exit_code: int, exit_status):
        self._on_stdout()
        if self._buf:
            self._handle_line(self._buf)
            self._buf = ""
        self._finished_flag = True
        try:
            if self._cancelled:
                return
            tail = "\n".join(self._tail)
            crashed = exit_status == QtCore.QProcess.CrashExit
            if crashed or exit_code != 0:
                if not self._terminal_seen:
                    what = ("the worker process crashed (killed or aborted by the OS)"
                            if crashed else f"the worker process exited with code {exit_code}")
                    self._report_failure(what + ("\n" + tail if tail else ""))
                return
            if "finished" not in self._own_names():   # plain no-arg QThread.finished
                self.finished.emit()
        finally:
            self._cleanup()

    def _own_names(self):
        return self._signal_names

    def _cleanup(self):
        d, self._job_dir = self._job_dir, None
        if d is not None:
            shutil.rmtree(d, ignore_errors=True)


def process_isolated(impl):
    """Return the process-isolated proxy for the QThread worker class ``impl``.

    ``impl`` must be reachable as ``sys.modules[impl.__module__].<impl.__qualname__>``
    (the child re-imports it by name), so name it with a leading underscore and
    bind the proxy to the public name::

        class _FooWorkerImpl(QtCore.QThread): ...
        FooWorker = process_isolated(_FooWorkerImpl)
    """
    if os.environ.get(INPROC_ENV):
        return impl
    names = tuple(own_signal_names(impl))
    attrs = {n: QtCore.pyqtSignal(*_signal_types(getattr(impl, n))) for n in names}
    if "finished" not in attrs:
        attrs["finished"] = QtCore.pyqtSignal()
    public = impl.__name__.lstrip("_")
    if public.endswith("Impl"):
        public = public[:-4]
    attrs.update(_impl_cls=impl, _signal_names=names, __module__=impl.__module__,
                 __doc__=impl.__doc__)
    return type(public, (ProcessWorker,), attrs)
