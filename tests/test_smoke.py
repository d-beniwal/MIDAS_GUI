"""Smoke tests for the midas-gui package.

The version-consistency checks always run (no GUI / MIDAS backend needed). The
build test constructs the full window offscreen and is skipped gracefully when
PyQt5 or the MIDAS analysis backends are not installed in the environment.
"""
import pathlib
import re

import pytest

import midas_gui


def test_version_is_nonempty_string():
    assert isinstance(midas_gui.__version__, str) and midas_gui.__version__


def test_version_matches_pyproject():
    """__version__ in the package must match pyproject.toml (release.sh keeps them in sync)."""
    root = pathlib.Path(__file__).resolve().parent.parent
    text = (root / "pyproject.toml").read_text()
    m = re.search(r'^version = "([^"]+)"', text, re.M)
    assert m is not None, "version not found in pyproject.toml"
    assert m.group(1) == midas_gui.__version__


def test_app_builds_offscreen():
    """The 9-tab MainWindow constructs headless when the full stack is present."""
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    try:
        import midas_gui.app as app_mod
    except Exception as exc:  # MIDAS backends absent → nothing to test here
        pytest.skip(f"midas_gui.app needs the full MIDAS stack: {exc}")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = app_mod.MainWindow()
    assert win.centralWidget().count() == 9
