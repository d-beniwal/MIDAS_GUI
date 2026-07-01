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
        from midas_gui.app import main
        main()
    except SystemExit:
        raise
    except Exception:
        _fatal(traceback.format_exc())
        sys.exit(1)
