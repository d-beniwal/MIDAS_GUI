"""The Calibrate tab's Working dir field.

One ``CalibrationTab`` for the whole module, deliberately: each one is an
expensive widget tree and building a fourth in a single process has segfaulted
before (see tests/test_calibrate_panel_save.py, which already builds three).
Every test here resets the field it touches instead.
"""
import os
import stat
import tempfile
from pathlib import Path

import pytest

from conftest import force_rmtree

_SCRATCH: list = []


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def tab(app):
    from midas_gui.tab_calibrate import CalibrationTab
    return CalibrationTab()


@pytest.fixture(autouse=True)
def _clean_scratch(tab):
    tab._out_ed.setText("")
    tab._wd_declined = ""
    yield
    while _SCRATCH:
        force_rmtree(_SCRATCH.pop())


def _tmpdir() -> Path:
    d = tempfile.mkdtemp(prefix="mg_calwd_")
    _SCRATCH.append(d)
    return Path(d)


def _load(tab, path):
    """What a data load does, minus actually decoding an image."""
    tab._loader.set_path(str(path), load=False)
    tab._loader.dataChanged.emit()


def test_the_bundled_demo_image_does_not_autofill_a_working_dir():
    """Cold start: ``__init__`` loads DEFAULT_CALIBRANT_TIF from inside the
    installed package, so autofilling would propose writing next to the source
    tree on every launch — a path nobody asked for."""
    from midas_gui.tab_calibrate import CalibrationTab
    t = CalibrationTab()
    assert t._out_ed.text().strip() == ""


def test_loading_data_in_a_bc_dir_fills_the_field(tab):
    work = _tmpdir() / "park_may26_bc"
    work.mkdir()
    img = work / "ceo2.tif"
    img.write_bytes(b"")
    _load(tab, img)
    assert Path(tab._out_ed.text()) == work


def test_a_directory_the_user_typed_is_never_overwritten(tab):
    mine = _tmpdir()
    tab._out_ed.setText(str(mine))
    other = _tmpdir() / "park_may26_bc"
    other.mkdir()
    img = other / "ceo2.tif"
    img.write_bytes(b"")
    _load(tab, img)
    assert Path(tab._out_ed.text()) == mine


def test_an_unwritable_candidate_is_left_blank_with_a_reason(tab):
    """Pre-filling a path that fails at Run time is worse than filling
    nothing — the field stays empty and the log says why."""
    base = _tmpdir()
    work = base / "park_may26_bc"
    work.mkdir()
    img = work / "ceo2.tif"
    img.write_bytes(b"")
    os.chmod(work, stat.S_IRUSR | stat.S_IXUSR)
    tab._log.clear()
    _load(tab, img)
    assert tab._out_ed.text().strip() == ""
    assert "No working directory filled in" in tab._log.toPlainText()


def test_the_unwritable_reason_is_logged_once_not_once_per_frame(tab):
    base = _tmpdir()
    work = base / "park_may26_bc"
    work.mkdir()
    img = work / "ceo2.tif"
    img.write_bytes(b"")
    os.chmod(work, stat.S_IRUSR | stat.S_IXUSR)
    tab._log.clear()
    for _ in range(3):
        _load(tab, img)
    assert tab._log.toPlainText().count("No working directory filled in") == 1


def test_suggest_fills_even_an_unwritable_path_but_warns(tab):
    """Unlike autofill: the user pressed the button, so they get the answer
    and the reason it won't work, rather than a silently empty field."""
    base = _tmpdir()
    work = base / "park_may26_bc"
    work.mkdir()
    img = work / "ceo2.tif"
    img.write_bytes(b"")
    tab._loader.set_path(str(img), load=False)
    os.chmod(work, stat.S_IRUSR | stat.S_IXUSR)
    tab._log.clear()
    tab._apply_suggested_working_dir()
    assert Path(tab._out_ed.text()) == work
    assert "Warning" in tab._log.toPlainText()


def test_suggest_overwrites_a_declined_default(tab):
    """The point of the button: a default the user cleared is recoverable."""
    work = _tmpdir() / "park_may26_bc"
    work.mkdir()
    img = work / "ceo2.tif"
    img.write_bytes(b"")
    tab._loader.set_path(str(img), load=False)
    tab._out_ed.setText(str(_tmpdir()))
    tab._apply_suggested_working_dir()
    assert Path(tab._out_ed.text()) == work


def test_suggest_with_nothing_to_go_on_says_so_rather_than_guessing(tab):
    """No data loaded, so there is no path to derive from. Says so out loud —
    autofill stays silent in the same situation because it fires on every
    frame change, but the button was pressed on purpose."""
    tab.set_expid_provider(lambda: "")
    tab._loader.set_path("", load=False)
    tab._log.clear()
    tab._apply_suggested_working_dir()
    assert tab._out_ed.text().strip() == ""
    assert "Can't derive a working directory" in tab._log.toPlainText()


# ── Project state ────────────────────────────────────────────────────────────

def test_a_project_saved_before_the_rename_still_restores(tab):
    """The widget was relabelled Output -> Working dir, but the state key
    stayed ``out_ed`` precisely so older projects keep loading."""
    work = _tmpdir()
    tab._set_state({"fields": {"out_ed": str(work)}})
    assert Path(tab._out_ed.text()) == work


def test_a_restored_directory_that_went_stale_warns_at_open_time(tab):
    """Project opened on a host without that mount: better to hear about it
    now than when a fit has already run. The stored choice is *not* silently
    rewritten to the current default."""
    gone = "/nonexistent-mount-xyz/park_may26_bc"
    tab._log.clear()
    tab._set_state({"fields": {"out_ed": gone}})
    assert tab._out_ed.text() == gone
    assert "restored working directory" in tab._log.toPlainText()


# ── The Run gate ─────────────────────────────────────────────────────────────

def test_run_refuses_an_unwritable_working_dir_and_stays_runnable(tab,
                                                                  monkeypatch):
    """Blocking beats warning-and-carrying-on, but the tab must not be left
    looking like a run is in flight when none started."""
    from PyQt5 import QtWidgets
    import numpy as np

    work = _tmpdir()
    os.chmod(work, stat.S_IRUSR | stat.S_IXUSR)
    tab._out_ed.setText(str(work))
    monkeypatch.setattr(tab, "_source_image",
                        lambda *a, **k: np.zeros((8, 8), dtype=np.float32))
    seen = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical",
                        staticmethod(lambda *a, **k: seen.append(a[2])))

    tab._run()

    assert seen and "writable" in seen[0]
    assert tab._worker is None
    assert tab._run_btn.isEnabled()
    assert not tab._abort_btn.isEnabled()
