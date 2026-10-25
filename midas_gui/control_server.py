"""Local-socket control channel for an external agent/MCP server to drive
this GUI — see ``midas_gui_mcp/README.md`` for the wire protocol and
``control_api.GuiController`` for what each operation actually does.

Generalizes ``bridge_server.BridgeServer``'s pattern (a ``QLocalServer``
listening on the GUI thread) from a one-way, fire-and-forget notification
into a full request/response channel: one JSON object per line in
(``{"id","op","args"}``), one JSON object per line back
(``{"id","ok","result"|"error"}``). Also pushes unsolicited
``{"event":...,"data":...}`` lines to every connected client — see
:meth:`ControlServer.broadcast_event`.

Runs entirely on the GUI thread, exactly like ``BridgeServer`` and
``AutoAttenuationRefreshServer`` already do — no new threading model, no
``QMetaObject.invokeMethod`` marshaling needed.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from PyQt5 import QtCore, QtNetwork, sip

from midas_gui.control_api import GuiController, _err

SERVER_NAME = "midas_gui_control_v1"

# How often the QTimer-based job-wait polls check status. Short enough that
# "wait" feels immediate once a job finishes, long enough not to spin the
# event loop pointlessly between checks.
_POLL_INTERVAL_MS = 100

# Ops that need to wait for something asynchronous (a background
# CalibrationWorker) rather than return immediately. These are *not*
# forwarded to GuiController.dispatch() like every other op — see
# _dispatch_async and its docstring for why a nested event-loop wait inside
# a single dispatch() call is unsafe.
_ASYNC_OPS = frozenset({"calibrate.wait", "calibrate.run_frozen_point_then_oneshot",
                        "solve_cell.wait"})

# QLocalServer's underlying Unix-domain-socket path is a Qt implementation
# detail (and varies by platform/version), so rather than have
# midas_gui_mcp/client.py guess it, we publish the real path here once
# listen() succeeds — a plain-text discovery file a PyQt-free client can
# read. Deliberately under $HOME, not a tempdir: an MCP client's stdio
# subprocess (this file's actual reader, in midas_gui_mcp/server.py) only
# inherits a small fixed env-var allowlist (HOME/LOGNAME/PATH/SHELL/TERM/
# USER — see mcp.client.stdio.DEFAULT_INHERITED_ENV_VARS), which does NOT
# include TMPDIR; on macOS tempfile.gettempdir() depends on TMPDIR, so a
# tempdir-based path here resolved to a different, wrong directory inside
# that subprocess and the discovery file could never be found there —
# confirmed by actually driving this through a real MCP client, not
# guessed. HOME is in that allowlist, so this works from any MCP launcher.
DISCOVERY_FILE = Path.home() / ".midas_gui" / f"{SERVER_NAME}.sock_path"


class ControlServer(QtCore.QObject):
    #: Fires True on the first client connecting (agent control begins) and
    #: False once the last one disconnects (agent control ends) — the signal
    #: behind the header ribbon's "agent-controlled" badge in app.py. Not
    #: just "the socket is listening": MIDAS_GUI_CONTROL=1 can be set with no
    #: agent actually attached, which shouldn't claim control is active.
    agentConnected = QtCore.pyqtSignal(bool)

    def __init__(self, main_window, log_fn=None, parent=None):
        super().__init__(parent)
        self._log = log_fn or (lambda _msg: None)
        self.controller = GuiController(main_window)
        self._server = QtNetwork.QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        self._buffers: dict = {}   # QLocalSocket -> bytes (partial line so far)
        self._sockets: list = []   # connected sockets, for broadcast_event

    def start(self, server_name: str = SERVER_NAME) -> bool:
        """Start listening; returns False (and logs) on failure. Always
        removes a same-named stale socket file first — see
        ``BridgeServer.start``'s identical reasoning."""
        QtNetwork.QLocalServer.removeServer(server_name)
        if not self._server.listen(server_name):
            self._log(f"MIDAS control: failed to listen on {server_name!r}: "
                      f"{self._server.errorString()}")
            return False
        full_name = self._server.fullServerName()
        try:
            DISCOVERY_FILE.parent.mkdir(parents=True, exist_ok=True)
            DISCOVERY_FILE.write_text(full_name, encoding="utf-8")
        except OSError as e:
            self._log(f"MIDAS control: could not write discovery file: {e}")
        self._log(f"MIDAS control: listening on {full_name!r}")
        return True

    def stop(self) -> None:
        self._server.close()
        try:
            DISCOVERY_FILE.unlink(missing_ok=True)
        except OSError:
            pass

    def broadcast_event(self, event: str, data) -> None:
        """Push an unsolicited ``{"event": event, "data": data}`` line to
        every currently-connected client — used for e.g. the live
        radial-profile-updated notification (see ``tab_view.py``'s
        ``liveProfileUpdated`` signal)."""
        line = (json.dumps({"event": event, "data": data}) + "\n").encode("utf-8")
        for sock in list(self._sockets):
            if sock.state() == QtNetwork.QLocalSocket.ConnectedState:
                sock.write(line)

    def _on_new_connection(self) -> None:
        sock = self._server.nextPendingConnection()
        if sock is None:
            return
        was_empty = not self._sockets
        self._sockets.append(sock)
        self._buffers[sock] = b""
        sock.readyRead.connect(lambda: self._on_data(sock))
        sock.disconnected.connect(lambda: self._on_disconnected(sock))
        if was_empty:
            self.agentConnected.emit(True)

    def _on_disconnected(self, sock) -> None:
        self._buffers.pop(sock, None)
        if sock in self._sockets:
            self._sockets.remove(sock)
        sock.deleteLater()
        if not self._sockets:
            self.agentConnected.emit(False)

    def _on_data(self, sock: QtNetwork.QLocalSocket) -> None:
        self._buffers[sock] = self._buffers.get(sock, b"") + bytes(sock.readAll())
        while b"\n" in self._buffers[sock]:
            line, self._buffers[sock] = self._buffers[sock].split(b"\n", 1)
            if line.strip():
                self._handle_line(sock, line)

    def _handle_line(self, sock: QtNetwork.QLocalSocket, line: bytes) -> None:
        req_id = None
        try:
            msg = json.loads(line.decode("utf-8"))
            req_id = msg.get("id")
            op = msg.get("op")
            args = msg.get("args") or {}
        except Exception as e:  # noqa: BLE001 — malformed input, never fatal
            self._log(f"MIDAS control: malformed message: {e}")
            self._reply(sock, {"id": req_id, "ok": False,
                               "error": {"message": str(e), "type": "malformed"}})
            return
        # Bring the relevant tab (and, for a handful of ops, the specific
        # sub-view within it — see GuiController._OP_SUBFOCUS) to the front
        # *before* dispatching, for both the sync and async paths below — a
        # human watching should see what the agent is about to do, not have
        # it happen off-screen on a tab that was never shown. Covers
        # calibrate.wait/run_frozen_point_then_oneshot too, which bypass
        # dispatch() below (see _ASYNC_OPS).
        self.controller.ensure_tab_visible(op or "")
        if op in _ASYNC_OPS:
            self._dispatch_async(sock, req_id, op, args)
            return
        result = self.controller.dispatch(op, args)
        result["id"] = req_id
        self._reply(sock, result)

    # ── async ops: wait for a job without nesting the event loop ───────
    #
    # calibrate.wait and calibrate.run_frozen_point_then_oneshot both need to
    # wait for a background CalibrationWorker to finish. An earlier version
    # did this with GuiController blocking in a QCoreApplication.
    # processEvents() loop — found (by actually driving this over a real
    # socket) to be unsafe: a nested processEvents() call lets Qt process the
    # very socket we're mid-handler for (e.g. its disconnected signal, if the
    # client's own read times out), deleting the C++ QLocalSocket object
    # while this handler still means to write a reply to it later. These two
    # ops are instead driven by QTimer.singleShot polling, which always
    # returns to the *real* event loop between checks — no nesting — and
    # every reply goes through _safe_reply, which no-ops instead of crashing
    # if the socket is gone by the time an answer is ready.

    def _dispatch_async(self, sock, req_id, op: str, args: dict) -> None:
        if op == "calibrate.wait":
            job_id = args.get("job_id")
            deadline = time.monotonic() + float(args.get("timeout", 120.0))
            self._poll_job(sock, req_id, job_id, deadline)
        elif op == "solve_cell.wait":
            job_id = args.get("job_id")
            deadline = time.monotonic() + float(args.get("timeout", 600.0))
            self._poll_job(sock, req_id, job_id, deadline,
                           getter=self.controller.op_solve_cell_get_job)
        elif op == "calibrate.run_frozen_point_then_oneshot":
            self._run_frozen_point_then_oneshot(sock, req_id, args)

    def _poll_job(self, sock, req_id, job_id, deadline: float, getter=None) -> None:
        if sip.isdeleted(sock):
            return
        getter = getter or self.controller.op_calibrate_get_job
        job = getter(job_id=job_id)
        status = (job.get("result") or {}).get("status") if job.get("ok") else None
        if job.get("ok") and status == "running" and time.monotonic() < deadline:
            QtCore.QTimer.singleShot(
                _POLL_INTERVAL_MS,
                lambda: self._poll_job(sock, req_id, job_id, deadline, getter))
            return
        job["id"] = req_id
        self._safe_reply(sock, job)

    def _run_frozen_point_then_oneshot(self, sock, req_id, args: dict) -> None:
        frozen_point_cfg = args.get("frozen_point_cfg") or {}
        one_shot_cfg = args.get("one_shot_cfg") or {}
        deadline = time.monotonic() + float(args.get("timeout", 300.0))

        fp = self.controller.op_calibrate_run("frozen_point", **frozen_point_cfg)
        if not fp.get("ok"):
            fp["id"] = req_id
            self._safe_reply(sock, fp)
            return
        job_id = fp["result"]["job_id"]

        def _after_frozen_point() -> None:
            if sip.isdeleted(sock):
                return
            job = self.controller.op_calibrate_get_job(job_id=job_id)
            status = (job.get("result") or {}).get("status") if job.get("ok") else None
            if job.get("ok") and status == "running" and time.monotonic() < deadline:
                QtCore.QTimer.singleShot(_POLL_INTERVAL_MS, _after_frozen_point)
                return
            if not job.get("ok") or status != "done":
                err = _err(f"frozen-point stage did not complete: {job}", kind="composite_failed")
                err["id"] = req_id
                self._safe_reply(sock, err)
                return
            seeded = self.controller.op_calibrate_seed_from_last_run()
            if not seeded.get("ok"):
                seeded["id"] = req_id
                self._safe_reply(sock, seeded)
                return
            one_shot = self.controller.op_calibrate_run("one_shot", **one_shot_cfg)
            one_shot["id"] = req_id
            if one_shot.get("ok"):
                one_shot["result"] = {"frozen_point_job_id": job_id,
                                      "one_shot_job_id": one_shot["result"]["job_id"]}
            self._safe_reply(sock, one_shot)

        _after_frozen_point()

    def _safe_reply(self, sock, payload: dict) -> None:
        """Like _reply, but a no-op if the socket is already gone (the
        client disconnected while an async op above was still waiting) —
        there is no one left to write to, and nothing to crash over."""
        if sip.isdeleted(sock) or sock.state() != QtNetwork.QLocalSocket.ConnectedState:
            return
        self._reply(sock, payload)

    def _reply(self, sock: QtNetwork.QLocalSocket, payload: dict) -> None:
        try:
            line = json.dumps(payload)
        except TypeError:
            # GuiController.dispatch always returns JSON-safe dicts — this is
            # a last-resort guard so a serialization bug never leaves the
            # client hanging on a request that will never get a reply.
            line = json.dumps({"id": payload.get("id"), "ok": False,
                               "error": {"message": "result not JSON-serializable",
                                         "type": "internal"}})
        sock.write((line + "\n").encode("utf-8"))
