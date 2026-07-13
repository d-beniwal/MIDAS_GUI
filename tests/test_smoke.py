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
    """The 10-tab MainWindow constructs headless when the full stack is present."""
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    try:
        import midas_gui.app as app_mod
    except Exception as exc:  # MIDAS backends absent → nothing to test here
        pytest.skip(f"midas_gui.app needs the full MIDAS stack: {exc}")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = app_mod.MainWindow()
    # 4 always-on + 6 optional (all shipped visible), incl. the Pump Probe tab.
    assert win.centralWidget().count() == 10


def test_tab_visibility_toggle():
    """Hiding every optional tab leaves exactly the four always-on tabs; restoring
    the shipped set brings them all back. All tabs stay constructed (live apply)."""
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    try:
        import midas_gui.app as app_mod
        import midas_gui.constants as C
    except Exception as exc:
        pytest.skip(f"midas_gui.app needs the full MIDAS stack: {exc}")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = app_mod.MainWindow()
    win.apply_tab_visibility([])
    assert win.centralWidget().count() == len(C.ALWAYS_TABS)
    win.apply_tab_visibility(C.DEFAULT_VISIBLE_TABS)
    assert win.centralWidget().count() == len(C.ALWAYS_TABS) + len(C.OPTIONAL_TABS)


def test_trr_filename_parser():
    """TRR filenames parse to (fshw, delay, id) with the delay sign flipped."""
    from midas_gui.tab_pumpprobe import parse_trr_filename
    fshw, delay, fid = parse_trr_filename(
        "Ex01_Sa01_Sc17-28.0fshw-2e-09delay211898.tif", "Ex01_Sa01_Sc17")
    assert fshw == -28.0 and delay == 2e-09 and fid == 211898
    assert parse_trr_filename("not_a_trr_frame.tif", "Ex01") is None


def test_pumpprobe_grouping():
    """Repeats average per delay and the reference (negative delays) subtracts to ΔI."""
    import numpy as np
    from midas_gui.workers import PumpProbeWorker
    profiles = np.array([[1., 1., 1.], [1.1, 1.1, 1.1],       # delay -1 (reference)
                         [2., 3., 4.], [2.2, 3.2, 4.2]])       # delay +1
    delays = np.array([-1., -1., 1., 1.])
    res = PumpProbeWorker._group_and_difference(profiles, delays, None)
    assert res["delays"] == [-1.0, 1.0]
    assert np.allclose(res["reference"], [1.05, 1.05, 1.05])
    assert np.allclose(res["dI"][1], [1.05, 2.05, 3.05])
