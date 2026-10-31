"""``project.*`` control-API ops (File ▸ Open/Save Project for an MCP agent).

One real ``MainWindow`` for the file (construction is expensive — see
``test_workspace_ux.py``). Every ``QMessageBox`` entry point is patched to
fail the test: the ops must never reach a modal dialog, since that would
block the GUI event loop and the control socket with it.
"""
from __future__ import annotations

import pytest

from midas_gui import project


@pytest.fixture(scope="module")
def qapp():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def win(qapp):
    import midas_gui.app as app_mod
    return app_mod.MainWindow()


@pytest.fixture
def ctrl(win, monkeypatch):
    from PyQt5 import QtWidgets
    from midas_gui.control_api import GuiController

    def _boom(*a, **k):
        import traceback
        stack = [f"{f.name}:{f.lineno}" for f in traceback.extract_stack()
                 if "midas_gui" in f.filename]
        raise AssertionError("a modal QMessageBox was opened from " + " > ".join(stack))
    for name in ("information", "critical", "warning", "question"):
        monkeypatch.setattr(QtWidgets.QMessageBox, name, staticmethod(_boom))
    c = GuiController(win)
    yield c
    win._set_project_path(None)
    win._set_workspace_dirty(False)


def test_get_info_without_a_project(ctrl):
    res = ctrl.dispatch("project.get_info", {})
    assert res["ok"] is True
    assert res["result"]["path"] is None


def test_save_without_path_or_open_project_is_refused(ctrl):
    res = ctrl.dispatch("project.save", {})
    assert res["ok"] is False
    assert res["error"]["type"] == "precondition_failed"


def test_save_as_then_save_open_roundtrip(ctrl, win, tmp_path):
    path = str(tmp_path / "agent_proj")           # ".h5" is appended
    res = ctrl.dispatch("project.save", {"path": path})
    assert res["ok"] is True, res
    assert res["result"]["save_as"] is True
    saved = path + ".h5"
    assert project.open_project(saved) == saved
    assert win._project_ctx.path == saved

    info = ctrl.dispatch("project.get_info", {})["result"]
    assert info["path"] == saved and info["unsaved_changes"] is False

    # Plain save (no path) overwrites the open project.
    res = ctrl.dispatch("project.save", {})
    assert res["ok"] is True and res["result"]["save_as"] is False

    # Save As onto an existing file needs overwrite.
    other = str(tmp_path / "other.h5")
    project.create_project(other)
    res = ctrl.dispatch("project.save", {"path": other})
    assert res["ok"] is False and res["error"]["type"] == "precondition_failed"
    assert ctrl.dispatch("project.save", {"path": other, "overwrite": True})["ok"] is True

    # Open the first one again.
    res = ctrl.dispatch("project.open", {"path": saved})
    assert res["ok"] is True, res
    assert win._project_ctx.path == saved
    assert res["result"]["selection"]["workspace_tabs"] is True


def test_open_refuses_unsaved_changes_unless_discarded(ctrl, win, tmp_path):
    path = str(tmp_path / "p.h5")
    project.create_project(path)
    assert ctrl.dispatch("project.save", {"path": path, "overwrite": True})["ok"] is True
    win._set_workspace_dirty(True)
    res = ctrl.dispatch("project.open", {"path": path})
    assert res["ok"] is False and res["error"]["type"] == "precondition_failed"
    res = ctrl.dispatch("project.open", {"path": path, "discard_unsaved": True})
    assert res["ok"] is True, res


def test_open_bad_args(ctrl, tmp_path):
    assert ctrl.dispatch("project.open", {"path": str(tmp_path / "nope.h5")})["ok"] is False
    p = tmp_path / "x.h5"
    project.create_project(str(p))
    res = ctrl.dispatch("project.open", {"path": str(p), "exclude": ["bogus"]})
    assert res["ok"] is False and res["error"]["type"] == "bad_args"


def test_open_rejects_a_non_project_file(ctrl, tmp_path):
    p = tmp_path / "junk.h5"
    p.write_bytes(b"not hdf5")
    res = ctrl.dispatch("project.open", {"path": str(p)})
    assert res["ok"] is False
