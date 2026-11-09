"""Plain-Python client for midas-gui's control socket.

Deliberately has **no PyQt/numpy dependency** — ``QLocalServer``'s Unix
transport is a plain Unix-domain socket, and its exact path is an
implementation detail we don't want to guess, so ``ControlClient`` reads it
from the discovery file ``control_server.py`` writes on ``listen()``
(``~/.midas_gui/midas_gui_control_v1.sock_path`` — under ``$HOME``, not a
tempdir: an MCP client's stdio subprocess only inherits a small env-var
allowlist that excludes ``TMPDIR``, so a tempdir-based path resolved
differently here than in the GUI process and could never be found —
confirmed by actually running this through a real MCP client) and talks to
it with the standard library ``socket`` module. This keeps this whole
package independent of midas-gui's own (heavily pinned) scientific-stack
dependencies, and demonstrates the wire protocol isn't PyQt-locked — any
language/framework that can open a Unix domain socket and speak
newline-delimited JSON can be a client. See README.md for the protocol.
"""
from __future__ import annotations

import itertools
import json
import socket
import time
from pathlib import Path
from typing import Any, Optional

DISCOVERY_FILE = Path.home() / ".midas_gui" / "midas_gui_control_v1.sock_path"


class ControlError(RuntimeError):
    def __init__(self, error: dict):
        super().__init__(error.get("message", str(error)))
        self.kind = error.get("type", "error")
        self.error = error


def discover_socket_path(timeout: float = 5.0) -> str:
    """Wait (briefly) for the discovery file, then return its contents.

    Raises FileNotFoundError if the control server never published one —
    i.e. midas-gui isn't running with MIDAS_GUI_CONTROL=1, or hasn't
    started listening yet."""
    deadline = time.monotonic() + timeout
    while not DISCOVERY_FILE.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not DISCOVERY_FILE.exists():
        raise FileNotFoundError(
            f"{DISCOVERY_FILE} not found — is midas-gui running with "
            "MIDAS_GUI_CONTROL=1?")
    return DISCOVERY_FILE.read_text(encoding="utf-8").strip()


class ControlClient:
    """One TCP-like connection to the control socket. Not thread-safe —
    one in-flight request at a time, matching the simple request/response
    protocol (id is for correlation/logging, not concurrency)."""

    def __init__(self, socket_path: Optional[str] = None, timeout: float = 30.0):
        self._path = socket_path or discover_socket_path()
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.settimeout(timeout)
        self._sock.connect(self._path)
        self._buf = b""
        self._ids = itertools.count(1)

    def close(self) -> None:
        self._sock.close()

    def __enter__(self) -> "ControlClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _read_line(self) -> dict:
        while b"\n" not in self._buf:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ConnectionError("control socket closed by midas-gui")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return json.loads(line.decode("utf-8"))

    def call(self, op: str, args: Optional[dict] = None, *, raise_on_error: bool = True) -> Any:
        """Send one request, wait for its matching response (skipping any
        unsolicited event lines that arrive first), and return ``result`` —
        or raise :class:`ControlError` (unless ``raise_on_error=False``, in
        which case the raw ``{"ok","result"|"error"}`` dict is returned)."""
        req_id = next(self._ids)
        self._sock.sendall((json.dumps({"id": req_id, "op": op, "args": args or {}}) + "\n")
                           .encode("utf-8"))
        while True:
            msg = self._read_line()
            if msg.get("id") == req_id:
                if raise_on_error and not msg.get("ok"):
                    raise ControlError(msg.get("error", {}))
                return msg if not raise_on_error else msg.get("result")
            # else: an {"event":...} line interleaved with our response —
            # a caller that wants events should use iter_events() on a
            # dedicated connection instead of calling at the same time.

    def iter_events(self):
        """Yield every ``{"event","data"}`` line as it arrives — use a
        connection dedicated to this (not one also making ``call()``s), since
        this blocks on each read."""
        while True:
            msg = self._read_line()
            if "event" in msg:
                yield msg
