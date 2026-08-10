"""Tests for the B-PILOT live-view bridge (midas_gui/bridge_server.py).

No real socket I/O here: `BridgeServer._on_bridge_data` takes anything with a
`.readAll()` so a fake stand-in is enough to exercise the JSON-validation
branches. `_resolve_and_start_live` (app.py) needs a real MainWindow, so that
test is skipped (like the rest of test_smoke.py) when the full MIDAS backend
stack isn't installed.
"""
import pytest

from midas_gui import bridge_server


class _FakeSocket:
    """Stand-in for QtNetwork.QLocalSocket -- only `.readAll()` is used."""

    def __init__(self, payload: bytes):
        self._payload = payload

    def readAll(self):
        return self._payload


def test_resolve_pv_matches_prefix():
    devices = [
        {"name": "a", "prefix": "1idPG4:", "pva_suffix": "Pva1:Image"},
        {"name": "b", "prefix": "20IDFF:", "pva_suffix": "Pva1:Image"},
    ]
    assert bridge_server.resolve_pv("20IDFF:", devices) == "20IDFF:Pva1:Image"


def test_resolve_pv_no_match_returns_none():
    devices = [{"name": "a", "prefix": "1idPG4:", "pva_suffix": "Pva1:Image"}]
    assert bridge_server.resolve_pv("nope:", devices) is None


def test_on_bridge_data_dispatches_valid_message():
    received = []
    srv = bridge_server.BridgeServer(received.append)
    sock = _FakeSocket(b'{"type": "live_pv", "version": 1, "prefix": "20IDFF:"}')
    srv._on_bridge_data(sock)
    assert received == ["20IDFF:"]


@pytest.mark.parametrize("payload", [
    b"not json",
    b'{"type": "something_else", "version": 1, "prefix": "20IDFF:"}',
    b'{"type": "live_pv", "version": 2, "prefix": "20IDFF:"}',
    b'{"type": "live_pv", "version": 1}',
    b'{"type": "live_pv", "version": 1, "prefix": ""}',
])
def test_on_bridge_data_ignores_invalid_messages(payload):
    received = []
    srv = bridge_server.BridgeServer(received.append)
    srv._on_bridge_data(_FakeSocket(payload))
    assert received == []


def test_start_removes_stale_socket_and_listens():
    import uuid
    name = f"bridge_server_test_{uuid.uuid4().hex[:8]}"
    srv = bridge_server.BridgeServer(lambda prefix: None)
    try:
        assert srv.start(name) is True
    finally:
        srv.stop()


def test_resolve_and_start_live_wired_in_mainwindow(monkeypatch):
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    try:
        import midas_gui.app as app_mod
        import midas_gui.constants as C
    except Exception as exc:
        pytest.skip(f"midas_gui.app needs the full MIDAS stack: {exc}")

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = app_mod.MainWindow()
    try:
        monkeypatch.setattr(C, "DEVICES", [
            {"name": "20iddFF", "prefix": "20IDFF:", "pva_suffix": "Pva1:Image"},
        ])
        calls = []
        monkeypatch.setattr(win._view_tab, "start_live_pv", calls.append)

        win._resolve_and_start_live("20IDFF:")
        assert calls == ["20IDFF:Pva1:Image"]

        calls.clear()
        win._resolve_and_start_live("no_such_prefix:")
        assert calls == []
    finally:
        win._bridge_server.stop()
