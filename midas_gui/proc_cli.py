"""Generic worker runner — ``python -m midas_gui.proc_cli --job-dir <dir>``.

Child-process half of ``proc_worker.process_isolated``: instantiates one of
``workers.py``'s QThread worker classes *without starting a thread*, runs its
``run()`` synchronously on this process's main thread, and ships every signal
the worker emits back to the GUI.

Why: a worker that calls into torch / OpenMP / HDF5 / scipy from inside the GUI
process can take the *whole* GUI down on a native fault (segfault, abort,
out-of-memory kill) — and with it every other tab's work, including a
calibration that was already running. In a child process the same fault only
kills that one job, which the GUI reports as an ordinary failure.

Protocol (child → GUI), over stdout:
  each emitted signal is pickled to ``<job-dir>/msg_<n>.pkl`` as
  ``(signal_name, args_tuple)`` and announced by one line
  ``MSG_PREFIX + <filename>``. Anything else on stdout (the backends' own
  ``print()``) is passed through as plain log text. Payloads go through files
  rather than the pipe because a mask / cake / volume can be many megabytes.

Input: ``<job-dir>/job.pkl`` = ``{"module", "qualname", "args", "kwargs"}``.
Exit status: 0 when ``run()`` returned (the worker reports its own failures
through its ``failed`` signal), 1 when ``run()`` itself raised (traceback
printed), anything else / a signal = native crash.
"""
from __future__ import annotations

import argparse
import importlib
import pickle
import sys
import traceback
from pathlib import Path

import midas_gui._paths  # noqa: F401  (KMP_DUPLICATE_LIB_OK / HDF5_USE_FILE_LOCKING — this
# entry point bypasses app.py's import chain, same reason calib_cli.py imports it)

JOB_PKL = "job.pkl"
MSG_PREFIX = "\x1eMIDASMSG\x1e"   # record-separator framed: cannot occur in normal log text


def own_signal_names(cls) -> list:
    """Names of the ``pyqtSignal`` attributes a worker class declares itself
    (Qt's built-in QThread ``started``/``finished`` are not in any Python
    class ``__dict__``, so a class that re-declares ``finished`` is the only
    way it appears here)."""
    from PyQt5 import QtCore
    names = []
    for klass in cls.__mro__:
        if klass.__module__.startswith("PyQt5"):    # QObject's own `destroyed`, etc.
            continue
        for name, val in vars(klass).items():
            if isinstance(val, QtCore.pyqtSignal) and name not in names:
                names.append(name)
    return names


def resolve_class(module: str, qualname: str):
    obj = importlib.import_module(module)
    for part in qualname.split("."):
        obj = getattr(obj, part)
    return obj


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m midas_gui.proc_cli")
    p.add_argument("--job-dir", required=True)
    job_dir = Path(p.parse_args(argv).job_dir)

    with open(job_dir / JOB_PKL, "rb") as fh:
        job = pickle.load(fh)

    from PyQt5 import QtCore
    app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])  # noqa: F841

    cls = resolve_class(job["module"], job["qualname"])
    worker = cls(*job["args"], **job["kwargs"])

    counter = [0]

    def ship(name, args):
        counter[0] += 1
        fname = f"msg_{counter[0]:06d}.pkl"
        tmp = job_dir / (fname + ".part")
        with open(tmp, "wb") as fh:
            pickle.dump((name, tuple(args)), fh, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.rename(job_dir / fname)          # atomic: the GUI never reads a half-written file
        sys.stdout.write(MSG_PREFIX + fname + "\n")
        sys.stdout.flush()

    for name in own_signal_names(cls):
        getattr(worker, name).connect(lambda *a, _n=name: ship(_n, a))

    try:
        worker.run()
    except BaseException:
        traceback.print_exc()
        sys.stdout.flush()
        return 1
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
