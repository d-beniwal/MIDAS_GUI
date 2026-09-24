"""Local-socket server letting the detached Auto Attenuation popup ask the
main GUI for a fresh data snapshot.

The popup (``auto_attenuation/dialog.py``) never touches the main GUI's live
objects directly — see ``snapshot.py`` — but it can ask, over
:data:`SERVER_NAME`, for the snapshot file it was launched with to be
rewritten with whatever the Data Viewer currently holds (a new buffer, newly
loaded data, a changed mask, ...), then reload that same file. One JSON
request::

    {"type": "refresh_request", "version": 1, "path": "/tmp/midas_auto_att_....npz"}

gets one JSON reply::

    {"type": "refresh_done", "ok": true, "message": null}

Modeled directly on ``midas_gui/bridge_server.py`` (same isolation rationale:
if the main GUI isn't running, or this server never started, nothing
connects and the popup just reports "could not reach the main GUI").
"""
from __future__ import annotations

import json

from PyQt5 import QtCore, QtNetwork

SERVER_NAME = "midas_gui_auto_attenuation_refresh_v1"


class AutoAttenuationRefreshServer(QtCore.QObject):
    """Listens on :data:`SERVER_NAME` and forwards refresh requests.

    ``on_refresh_request(path)`` is called with the snapshot path from a
    valid ``refresh_request`` message and must return ``(ok, message)`` —
    actually rewriting that path (reading the live Data Viewer state) is the
    caller's job, kept out of this module so it stays testable without a
    real MainWindow.
    """

    def __init__(self, on_refresh_request, log_fn=None, parent=None):
        super().__init__(parent)
        self._on_refresh_request = on_refresh_request
        self._log = log_fn or (lambda _msg: None)
        self._server = QtNetwork.QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)

    def start(self, server_name: str = SERVER_NAME) -> bool:
        """Start listening; returns False (and logs) on failure.

        Always removes a same-named stale socket file first — see
        ``BridgeServer.start`` for why.
        """
        QtNetwork.QLocalServer.removeServer(server_name)
        if not self._server.listen(server_name):
            self._log(
                f"Auto Attenuation refresh: failed to listen on "
                f"{server_name!r}: {self._server.errorString()}"
            )
            return False
        return True

    def stop(self) -> None:
        self._server.close()

    def _on_new_connection(self) -> None:
        sock = self._server.nextPendingConnection()
        if sock is None:
            return
        sock.readyRead.connect(lambda: self._on_refresh_data(sock))
        sock.disconnected.connect(sock.deleteLater)

    def _on_refresh_data(self, sock: QtNetwork.QLocalSocket) -> None:
        try:
            msg = json.loads(bytes(sock.readAll()).decode("utf-8"))
        except Exception as e:  # noqa: BLE001 — malformed input, never fatal
            self._reply(sock, ok=False, message=f"malformed request: {e}")
            return
        if msg.get("type") != "refresh_request" or msg.get("version") != 1:
            self._reply(sock, ok=False, message=f"unrecognized request: {msg!r}")
            return
        path = msg.get("path")
        if not path:
            self._reply(sock, ok=False, message="refresh_request missing path")
            return
        try:
            ok, message = self._on_refresh_request(path)
        except Exception as e:  # noqa: BLE001 — report back, don't crash the GUI
            self._log(f"Auto Attenuation refresh: handler raised: {e}")
            ok, message = False, str(e)
        self._reply(sock, ok=bool(ok), message=message)

    @staticmethod
    def _reply(sock: QtNetwork.QLocalSocket, *, ok: bool, message) -> None:
        payload = json.dumps(
            {"type": "refresh_done", "ok": ok, "message": message}
        ).encode("utf-8")
        sock.write(payload)
        sock.flush()
        sock.disconnectFromServer()
