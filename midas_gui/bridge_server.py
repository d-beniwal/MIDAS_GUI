"""Local-socket server letting another app (B-PILOT) auto-start Live Data.

B-PILOT (a separate Bluesky plan-runner GUI, github.com/d-beniwal/B-PILOT)
connects to :data:`SERVER_NAME` as a ``QLocalSocket`` and writes one JSON
line whenever it dispatches a scan involving a detector, e.g.::

    {"type": "live_pv", "version": 1, "prefix": "20IDFF:"}

We resolve ``prefix`` against our own :data:`midas_gui.constants.DEVICES`
registry (which already knows each device's PVA suffix) and start the Data
Viewer's Live Data stream on the resulting PV — matching a plain-string
prefix is more robust than trusting a full PV name assembled by the other
side, since suffix conventions are defined and maintained here, not there.

If B-PILOT isn't running, or isn't configured to use this bridge, nothing
connects and this server just sits idle — MIDAS GUI's own behavior is
unaffected either way. See B-PILOT's ``gui_qt/midas_bridge.py`` for the
client half of this protocol; ``SERVER_NAME`` must match on both sides.
"""
from __future__ import annotations

import json

from PyQt5 import QtCore, QtNetwork

SERVER_NAME = "midas_gui_live_bridge_v1"


class BridgeServer(QtCore.QObject):
    """Listens on :data:`SERVER_NAME` and forwards resolved PV prefixes.

    ``on_live_pv_prefix`` is called with the raw prefix string from a valid
    ``live_pv`` message; resolving it against ``constants.DEVICES`` and
    actually starting the live view is the caller's job (kept out of this
    module so it stays testable without a real MainWindow/DataViewerTab).
    """

    def __init__(self, on_live_pv_prefix, log_fn=None, parent=None):
        super().__init__(parent)
        self._on_live_pv_prefix = on_live_pv_prefix
        self._log = log_fn or (lambda _msg: None)
        self._server = QtNetwork.QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)

    def start(self, server_name: str = SERVER_NAME) -> bool:
        """Start listening; returns False (and logs) on failure.

        Always removes a same-named stale socket file first — on macOS/Linux
        a ``QLocalServer`` whose process was killed without a clean
        ``closeEvent`` leaves its socket file behind, which would otherwise
        make every future ``listen()`` fail with "address already in use"
        even though nothing is actually listening.
        """
        QtNetwork.QLocalServer.removeServer(server_name)
        if not self._server.listen(server_name):
            self._log(
                f"MIDAS bridge: failed to listen on {server_name!r}: "
                f"{self._server.errorString()}"
            )
            return False
        return True

    def stop(self) -> None:
        self._server.close()

    def _on_new_connection(self) -> None:
        sock = self._server.nextPendingConnection()
        if sock is None:
            return
        sock.readyRead.connect(lambda: self._on_bridge_data(sock))
        sock.disconnected.connect(sock.deleteLater)

    def _on_bridge_data(self, sock: QtNetwork.QLocalSocket) -> None:
        try:
            msg = json.loads(bytes(sock.readAll()).decode("utf-8"))
        except Exception as e:  # noqa: BLE001 — malformed input, never fatal
            self._log(f"MIDAS bridge: malformed message: {e}")
            return
        if msg.get("type") != "live_pv" or msg.get("version") != 1:
            self._log(f"MIDAS bridge: ignoring message {msg!r}")
            return
        prefix = msg.get("prefix")
        if not prefix:
            self._log(f"MIDAS bridge: live_pv message missing prefix: {msg!r}")
            return
        self._on_live_pv_prefix(prefix)


def resolve_pv(prefix: str, devices) -> str | None:
    """Return ``prefix + pva_suffix`` for the ``devices`` entry matching
    ``prefix``, or None if no entry matches (caller should log + no-op)."""
    match = next((d for d in devices if d.get("prefix") == prefix), None)
    if match is None:
        return None
    return prefix + match.get("pva_suffix", "")
