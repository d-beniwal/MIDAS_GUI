"""Run slow file I/O off the GUI thread without changing fast-path behaviour.

``AdaptiveCall(fn).run(budget)`` starts ``fn`` on a worker thread and waits up
to ``budget`` seconds for it:

* finished in time (any local file) → ``run`` returns ``(result, exc)`` and the
  caller carries on synchronously, exactly as if ``fn`` had run inline;
* not finished (slow network share) → ``run`` returns ``None`` immediately, the
  GUI stays responsive, and ``finished(result, exc)`` fires later on the GUI
  thread (queued signal) — the caller applies the result there.

``budget=None`` waits for completion (always synchronous) — for programmatic
callers that read state right after the call returns.  Exactly one of the two
delivery paths is used per call.  Plain ``threading`` + a Qt signal, so it
behaves the same on Linux, macOS and Windows.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

from PyQt5 import QtCore


class AdaptiveCall(QtCore.QObject):
    finished = QtCore.pyqtSignal(object, object)     # result, exception|None

    def __init__(self, fn: Callable, parent: Optional[QtCore.QObject] = None):
        super().__init__(parent)
        self._fn = fn
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._res = (None, None)
        self._detached = False

    def _work(self) -> None:
        try:
            res = (self._fn(), None)
        except BaseException as e:               # delivered to the caller, not raised here
            res = (None, e)
        with self._lock:
            self._res = res
            self._done.set()
            detached = self._detached
        if detached:
            self.finished.emit(*res)

    def run(self, budget: Optional[float]):
        threading.Thread(target=self._work, daemon=True).start()
        if budget is None:
            self._done.wait()
            return self._res
        if self._done.wait(max(0.0, float(budget))):
            return self._res
        with self._lock:
            if self._done.is_set():              # finished in the gap
                return self._res
            self._detached = True
        return None
