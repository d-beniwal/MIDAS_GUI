"""Agent control (the MCP control socket) is ON by default at the real entry point and has two
opt-outs; a bare ``MainWindow()`` never opens the well-known socket. Every socket here uses a
private name and discovery file so a running midas-gui is never disturbed."""
from __future__ import annotations

import os
import stat
import sys
import uuid

import pytest


def _req(argv, env=None):
    pytest.importorskip("PyQt5")
    from midas_gui.app import agent_control_requested
    return agent_control_requested(argv, env or {})


def test_on_by_default():
    assert _req(["midas-gui"]) is True


def test_flag_turns_it_off_and_is_removed_before_qt_sees_it():
    argv = ["midas-gui", "--no-agent-control", "-style", "fusion"]
    assert _req(argv) is False
    assert argv == ["midas-gui", "-style", "fusion"]


@pytest.mark.parametrize("val", ["0", "false", "False", "no", "off", " OFF "])
def test_env_zero_turns_it_off(val):
    assert _req(["midas-gui"], {"MIDAS_GUI_CONTROL": val}) is False


@pytest.mark.parametrize("val", ["1", "", "true", "yes"])
def test_env_one_or_other_keeps_it_on(val):
    assert _req(["midas-gui"], {"MIDAS_GUI_CONTROL": val}) is True


def test_flag_beats_env_one():
    assert _req(["midas-gui", "--no-agent-control"], {"MIDAS_GUI_CONTROL": "1"}) is False


@pytest.fixture(scope="module")
def qapp():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def win(qapp):
    os.environ.pop("MIDAS_GUI_CONTROL", None)
    import midas_gui.app as app_mod
    return app_mod.MainWindow()


@pytest.fixture
def private_socket(monkeypatch, tmp_path):
    from midas_gui import control_server as cs
    name = f"midas_test_{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(cs, "DISCOVERY_FILE", tmp_path / "ctl.sock_path")
    orig = cs.ControlServer.start
    monkeypatch.setattr(cs.ControlServer, "start", lambda self, server_name=name: orig(self, server_name))
    return name


def test_bare_window_opens_no_socket(win):
    assert win._control_server is None
    assert win._agent_status_lbl.text() == "Agent control: off"


def test_ensure_control_server_starts_and_labels_the_status_bar(win, private_socket):
    try:
        assert win._ensure_control_server() is True
        assert win._agent_status_lbl.text() == "Agent control: ready"
        assert "--no-agent-control" in win._agent_status_lbl.toolTip()
        assert win._ensure_control_server() is True       # idempotent
    finally:
        win._control_server.stop()
        win._control_server = None
        win._update_agent_status()


@pytest.mark.skipif(sys.platform == "win32", reason="unix socket permissions")
def test_socket_is_owner_only(win, private_socket):
    try:
        assert win._ensure_control_server()
        mode = stat.S_IMODE(os.stat(win._control_server.socket_path).st_mode)
        assert mode & 0o077 == 0, oct(mode)
    finally:
        win._control_server.stop()
        win._control_server = None
        win._update_agent_status()


def test_second_window_does_not_steal_a_live_socket(win, private_socket):
    from midas_gui import control_server as cs
    first = cs.ControlServer(win)
    second = cs.ControlServer(win)
    try:
        assert first.start() is True
        assert second.start() is False and second.already_running
        # the first one still answers
        from PyQt5 import QtNetwork
        probe = QtNetwork.QLocalSocket()
        probe.connectToServer(private_socket)
        assert probe.waitForConnected(500)
        probe.abort()
    finally:
        first.stop()
    third = cs.ControlServer(win)
    try:
        assert third.start() is True                      # free again once the owner is gone
    finally:
        third.stop()


def test_failed_start_because_another_window_owns_it_is_labelled(win, private_socket):
    from midas_gui import control_server as cs
    owner = cs.ControlServer(win)
    try:
        assert owner.start()
        assert win._ensure_control_server() is False
        assert win._control_server is None
        assert win._agent_status_lbl.text() == "Agent control: other window"
    finally:
        owner.stop()
        win._update_agent_status()
