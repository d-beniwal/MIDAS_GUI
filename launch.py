#!/usr/bin/env python
"""Standalone launcher: python /path/to/midas-gui/launch.py

Wrapped so that a startup failure is written to a log file and kept on screen
instead of the window silently vanishing (common on Windows double-click).
"""
import sys
import traceback
from datetime import datetime
from pathlib import Path

_LOG_FILE = Path.home() / "midas_gui_error.log"
_HANG_FILE = Path.home() / "midas_gui_hang.log"


def _install_hang_dump() -> None:
    """``kill -USR1 <pid>`` writes every thread's stack to ``_HANG_FILE``.

    A frozen Qt GUI gives no clue which call is blocking the event loop, and
    the freeze is the one moment you cannot ask the app anything. This costs
    nothing until the signal arrives, does not interrupt or kill the process
    (unlike faulthandler's fatal-error handlers), and survives the freeze
    precisely because the signal is handled by the C runtime rather than by
    the stalled event loop. Unavailable on Windows, where SIGUSR1 does not
    exist — hence the guard rather than a hard import."""
    try:
        import faulthandler
        import signal
        if not hasattr(signal, "SIGUSR1"):
            return
        fh = open(_HANG_FILE, "a", buffering=1, encoding="utf-8")
        fh.write(f"\n===== {datetime.now().isoformat()} (hang dump armed, "
                 f"pid {__import__('os').getpid()}) =====\n")
        faulthandler.register(signal.SIGUSR1, file=fh, all_threads=True,
                              chain=False)
    except Exception:
        pass    # a debugging aid is never worth failing startup for


def _fatal(text: str) -> None:
    stamp = f"\n===== {datetime.now().isoformat()} (launch) =====\n{text}\n"
    try:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(stamp)
    except Exception:
        pass
    sys.stderr.write(stamp)
    # Try a GUI dialog; fall back to a console pause so the message is readable.
    try:
        from PyQt5 import QtWidgets
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        QtWidgets.QMessageBox.critical(
            None, "MIDAS GUI — failed to start",
            f"{text.strip().splitlines()[-1]}\n\nFull traceback written to:\n{_LOG_FILE}")
    except Exception:
        try:
            input("\nMIDAS GUI failed to start. Press Enter to exit…")
        except Exception:
            pass


if __name__ == "__main__":
    try:
        _install_hang_dump()
        from midas_gui.app import main
        main()
    except SystemExit:
        raise
    except Exception:
        _fatal(traceback.format_exc())
        sys.exit(1)
