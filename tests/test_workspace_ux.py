"""Tests for the Workspace/Project UX additions (branch: workspace_ux):
the global recent-files list, the dirty-state indicator + autosave/
crash-recovery, and the read-only Project History viewer. See
.context/DECISIONS.md for the design rationale.

Qt MainWindow construction is expensive and, per this suite's own
documented flakiness (STATE.md), not safe to repeat many times in one
process. This file builds exactly ONE MainWindow (module-scoped fixture),
mirroring how the rest of the suite is run per-file-isolated.
"""
from types import SimpleNamespace

import numpy as np
import pytest

from midas_gui import project, settings


# ── settings.py: recent-files store (no Qt) ──────────────────────────────────
def test_record_and_get_recent_roundtrip(tmp_path, monkeypatch):
    import sys
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    a = tmp_path / "a.h5"; a.write_bytes(b"")
    b = tmp_path / "b.h5"; b.write_bytes(b"")

    assert settings.get_recent("project") == []
    settings.record_recent(str(a), "project")
    settings.record_recent(str(b), "project")
    entries = settings.get_recent("project")
    assert [e["name"] for e in entries] == ["b.h5", "a.h5"]   # MRU order

    # Re-recording an existing path moves it to the front rather than
    # duplicating it.
    settings.record_recent(str(a), "project")
    entries = settings.get_recent("project")
    assert [e["name"] for e in entries] == ["a.h5", "b.h5"]

    # Workspaces and projects are tracked independently.
    assert settings.get_recent("workspace") == []

    with pytest.raises(ValueError):
        settings.record_recent(str(a), "bogus_kind")


def test_get_recent_prunes_deleted_files(tmp_path, monkeypatch):
    import sys
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    gone = tmp_path / "gone.json"
    gone.write_text("{}")
    settings.record_recent(str(gone), "workspace")
    gone.unlink()

    assert settings.get_recent("workspace") == []


def test_recent_files_capped_at_max(tmp_path, monkeypatch):
    import sys
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    paths = []
    for i in range(settings.RECENT_MAX + 5):
        p = tmp_path / f"f{i}.json"
        p.write_text("{}")
        paths.append(p)
        settings.record_recent(str(p), "workspace")

    entries = settings.get_recent("workspace")
    assert len(entries) == settings.RECENT_MAX
    # Most-recently-recorded survive; oldest are dropped.
    assert entries[0]["name"] == f"f{len(paths) - 1}.json"


# ── project.py fixture used by the ProjectHistoryDialog test ─────────────────
def _fake_result(**overrides):
    fields = dict(
        Lsd=200000.0, BC_y=1024.0, BC_z=1024.0, tx=0.0, ty=0.0, tz=0.0,
        distortion={}, pxY=200.0, pxZ=200.0,
        NrPixelsY=2048, NrPixelsZ=2048, wavelength_A=0.1729,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


@pytest.fixture
def project_with_attempts(tmp_path):
    path = str(tmp_path / "proj.h5")
    project.create_project(path, name="fixture-project")
    project.append_calibration_attempt(
        path, "single", cfg={"calibrant": "CeO2"}, result=_fake_result(),
        loader_state={"path": None})
    project.append_integration_attempt(
        path, "single",
        inputs={"kernel": "subpixel4"},
        finished_payload={"n": 2, "profiles": np.zeros((2, 8), dtype=np.float32),
                           "r_axis_px": np.arange(8, dtype=np.float32),
                           "sigmas": np.ones((2, 8), dtype=np.float32),
                           "frame_ids": ["f0", "f1"], "out_paths": []})
    return path


# ── Qt-level tests: one shared MainWindow for the whole file ─────────────────
@pytest.fixture(scope="module")
def qapp():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def win(qapp):
    import midas_gui.app as app_mod
    return app_mod.MainWindow()


@pytest.fixture
def no_modal_dialogs(monkeypatch):
    """save_gui_state/load_gui_state end with an informational QMessageBox —
    fine interactively, but exec_() blocks forever with no user to click it
    under the offscreen QPA platform. Existing tests elsewhere in the suite
    avoid this by calling tab-level apply_project_* methods directly instead
    of the MainWindow wrappers that pop these dialogs (see test_project.py);
    the tests here need the MainWindow wrappers themselves, so stub the
    dialogs instead."""
    from PyQt5 import QtWidgets
    monkeypatch.setattr(QtWidgets.QMessageBox, "information",
                         staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical",
                         staticmethod(lambda *a, **k: None))
    return QtWidgets.QMessageBox


def test_fresh_window_is_not_dirty(win):
    """A just-built window must never report unsaved changes (the baseline
    hash is computed once, right after _build_ui — see MainWindow.__init__)."""
    win._check_workspace_dirty()
    assert win._workspace_dirty is False
    assert win.isWindowModified() is False


def test_editing_a_field_marks_workspace_dirty(win, tmp_path, no_modal_dialogs):
    # Calibrate's own wavelength spinbox (self._wl) is a simple, always-present
    # field reflected by get_state() — editing it is enough to prove the
    # hash-diff dirty-check reacts to a real edit.
    win._cal_tab._wl.setValue(win._cal_tab._wl.value() + 0.01)

    win._check_workspace_dirty()
    assert win._workspace_dirty is True
    assert win.isWindowModified() is True

    # Saving clears the dirty flag and updates the baseline.
    save_path = str(tmp_path / "session.json")
    win.save_gui_state(save_path)
    assert win._workspace_dirty is False
    assert win.isWindowModified() is False
    win._check_workspace_dirty()
    assert win._workspace_dirty is False   # unchanged since the save


def test_autosave_tick_writes_and_is_cleared_by_save(win, tmp_path, monkeypatch):
    draft = tmp_path / "autosave" / "workspace_draft.json"
    monkeypatch.setattr(settings, "autosave_draft_path", lambda: draft)

    win._set_workspace_dirty(False)
    win._autosave_tick()
    assert not draft.exists(), "must not write a draft while not dirty"

    win._set_workspace_dirty(True)
    win._autosave_tick()
    assert draft.is_file()

    win._clear_autosave_draft()
    assert not draft.exists()


def test_maybe_offer_restore_autosave_decline_and_accept(
        win, tmp_path, monkeypatch, no_modal_dialogs):
    import json as _json
    MB = no_modal_dialogs   # the (stubbed) QMessageBox class
    draft = tmp_path / "draft.json"
    state, _errors = win._serialize_workspace()
    monkeypatch.setattr(settings, "autosave_draft_path", lambda: draft)

    # Decline: draft is discarded, nothing else about the window changes.
    draft.write_text(_json.dumps(state))
    monkeypatch.setattr(MB, "question", staticmethod(lambda *a, **k: MB.No))
    win._gui_state_path = "/tmp/whatever.json"
    win.maybe_offer_restore_autosave()
    assert not draft.exists()
    assert win._gui_state_path == "/tmp/whatever.json"

    # Accept: draft is loaded, and _gui_state_path is reset to None (so the
    # next Ctrl+S prompts for a real destination rather than silently
    # overwriting the hidden autosave file).
    draft.write_text(_json.dumps(state))
    monkeypatch.setattr(MB, "question", staticmethod(lambda *a, **k: MB.Yes))
    win.maybe_offer_restore_autosave()
    assert win._gui_state_path is None
    assert win._workspace_dirty is True
    assert not draft.exists()

    win._set_workspace_dirty(False)   # leave a clean baseline for later tests
    win._gui_state_path = None


def test_project_history_dialog_lists_attempts(qapp, project_with_attempts):
    from midas_gui.dialogs import ProjectHistoryDialog
    dlg = ProjectHistoryDialog(project_with_attempts)
    try:
        assert dlg._table.rowCount() == 2
        kinds = {dlg._table.item(r, 1).text() for r in range(dlg._table.rowCount())}
        assert kinds == {"Calibrate", "Batch Integrate"}
    finally:
        dlg.close()


def test_project_history_dialog_handles_empty_project(qapp, tmp_path):
    from midas_gui.dialogs import ProjectHistoryDialog
    path = str(tmp_path / "empty.h5")
    project.create_project(path)
    dlg = ProjectHistoryDialog(path)
    try:
        assert dlg._table.rowCount() == 0
    finally:
        dlg.close()
