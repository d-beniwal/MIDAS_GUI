"""Embedded terminal + dockable panel for APEXA's command-line interface.

``TerminalWidget`` runs a child process on a pseudo-terminal and renders it
with ``pyte`` (a pure-Python VT100/xterm emulator), so a TTY-only program such
as APEXA's ``APEXA>`` prompt can live inside the main window.  ``ApexaDock`` is
a ``QDockWidget`` around it that can be docked left/right/bottom or floated.

POSIX only (``pty``/``termios``); ``available()`` is False on Windows or when
``pyte`` is missing, and the caller falls back to an external terminal window.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from typing import List, Optional

from PyQt5 import QtCore, QtGui, QtWidgets

from . import style as S

try:  # pragma: no cover - import guard
    import fcntl
    import pty
    import struct
    import termios
    import pyte
    from pyte import modes as _pm
    _OK = True
except Exception:  # Windows, or pyte not installed
    _OK = False


def available() -> bool:
    return _OK and not sys.platform.startswith("win")


# xterm-ish 16-colour palette; pyte names the basic colours (brown == yellow).
_ANSI = {
    "black": "#1d1f21", "red": "#cc6666", "green": "#b5bd68", "brown": "#f0c674",
    "blue": "#81a2be", "magenta": "#b294bb", "cyan": "#8abeb7", "white": "#c5c8c6",
    "brightblack": "#666666", "brightred": "#d54e53", "brightgreen": "#b9ca4a",
    "brightbrown": "#e7c547", "brightblue": "#7aa6da", "brightmagenta": "#c397d8",
    "brightcyan": "#70c0b1", "brightwhite": "#eaeaea",
}
_PRIVATE = lambda n: n << 5  # pyte stores DEC private modes shifted by 5


def _color(name: str, default: QtGui.QColor) -> QtGui.QColor:
    if name == "default":
        return default
    if name in _ANSI:
        return QtGui.QColor(_ANSI[name])
    if len(name) == 6:
        try:
            int(name, 16)
            return QtGui.QColor("#" + name)
        except ValueError:
            pass
    return default


class TerminalWidget(QtWidgets.QWidget):
    """A single child process on a pty, drawn from a pyte screen."""

    finished = QtCore.pyqtSignal(int)   # exit code of the child

    _SCROLLBACK = 5000

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setAttribute(QtCore.Qt.WA_InputMethodEnabled, False)
        self.setCursor(QtCore.Qt.IBeamCursor)
        font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        font.setFamilies(["Menlo", "SF Mono", "Consolas", "DejaVu Sans Mono",
                          "Liberation Mono", "Courier New", font.family()])
        font.setPointSize(11)
        font.setStyleHint(QtGui.QFont.Monospace)
        font.setFixedPitch(True)
        self._font = font
        self._bold = QtGui.QFont(font)
        self._bold.setBold(True)
        self._calc_cell()
        self._cols, self._rows = 80, 24
        self._screen = pyte.HistoryScreen(self._cols, self._rows,
                                          history=self._SCROLLBACK, ratio=0.5)
        self._stream = pyte.ByteStream(self._screen)
        self._proc: Optional[subprocess.Popen] = None
        self._fd: Optional[int] = None
        self._notifier: Optional[QtCore.QSocketNotifier] = None
        self._sel: Optional[tuple] = None        # ((col,row),(col,row)) or None
        self._sel_anchor: Optional[tuple] = None
        self._redraw = QtCore.QTimer(self)
        self._redraw.setSingleShot(True)
        self._redraw.setInterval(16)
        self._redraw.timeout.connect(self.update)
        self._poll = QtCore.QTimer(self)          # detects child exit
        self._poll.setInterval(300)
        self._poll.timeout.connect(self._check_exit)

    # ── process ────────────────────────────────────────────────────────────
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, argv: List[str], cwd: Optional[str] = None,
              env: Optional[dict] = None) -> None:
        self.stop()
        self._reset_screen()
        master, slave = pty.openpty()
        e = dict(os.environ)
        e.update({"TERM": "xterm-256color", "COLORTERM": "truecolor"})
        e.setdefault("LANG", "en_US.UTF-8")
        e.update(env or {})
        # Don't leak the GUI's Qt/offscreen settings into a CLI tool.
        for k in ("QT_QPA_PLATFORM", "QT_PLUGIN_PATH"):
            e.pop(k, None)

        def _ctty():  # make the pty the child's controlling terminal (Ctrl-C works)
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        self._set_winsize(slave)
        try:
            self._proc = subprocess.Popen(
                argv, cwd=cwd, env=e, stdin=slave, stdout=slave, stderr=slave,
                start_new_session=True, preexec_fn=_ctty, close_fds=True)
        finally:
            os.close(slave)
        self._fd = master
        os.set_blocking(master, False)
        self._notifier = QtCore.QSocketNotifier(master, QtCore.QSocketNotifier.Read, self)
        self._notifier.activated.connect(self._on_readable)
        self._poll.start()
        self.update()

    def stop(self) -> None:
        """Hang up the child (and its process group) and release the pty."""
        self._poll.stop()
        if self._notifier is not None:
            self._notifier.setEnabled(False)
            self._notifier.deleteLater()
            self._notifier = None
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            for sig in (signal.SIGHUP, signal.SIGTERM):
                try:
                    os.killpg(proc.pid, sig)
                except Exception:
                    pass
                try:
                    proc.wait(timeout=1.0)
                    break
                except Exception:
                    continue
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    pass
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    def _check_exit(self):
        if self._proc is not None and self._proc.poll() is not None:
            self._drain()
            code = self._proc.returncode
            self._poll.stop()
            self._stream.feed(f"\r\n\x1b[2m[process exited with code {code}]\x1b[0m\r\n".encode())
            self.update()
            self.finished.emit(code)

    def _drain(self):
        while self._fd is not None:
            try:
                data = os.read(self._fd, 65536)
            except (BlockingIOError, InterruptedError):
                return
            except OSError:
                return
            if not data:
                return
            self._stream.feed(data)

    def _on_readable(self):
        self._drain()
        if not self._redraw.isActive():
            self._redraw.start()

    def write(self, data: bytes) -> None:
        if self._fd is None or not self.is_running():
            return
        self._snap_to_bottom()
        try:
            os.write(self._fd, data)
        except OSError:
            pass

    # ── geometry ───────────────────────────────────────────────────────────
    def _calc_cell(self):
        fm = QtGui.QFontMetricsF(self._font)
        self._cw = max(1.0, fm.horizontalAdvance("M"))
        self._ch = max(1.0, fm.height())
        self._ascent = fm.ascent()

    def _set_winsize(self, fd=None):
        fd = self._fd if fd is None else fd
        if fd is None:
            return
        try:
            fcntl.ioctl(fd, termios.TIOCSWINSZ,
                        struct.pack("HHHH", self._rows, self._cols, 0, 0))
        except OSError:
            pass

    def _reset_screen(self):
        self._screen.reset()
        self._screen.resize(self._rows, self._cols)
        self._sel = self._sel_anchor = None

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        cols = max(20, int(self.width() / self._cw))
        rows = max(5, int(self.height() / self._ch))
        if (cols, rows) != (self._cols, self._rows):
            self._cols, self._rows = cols, rows
            self._resize_screen(rows, cols)
            self._set_winsize()

    def _resize_screen(self, rows: int, cols: int) -> None:
        """Resize the emulator without losing visible text.  ``pyte`` shrinks
        by deleting rows from the *top*, which wipes freshly printed output
        when the panel is merely made shorter; drop blank rows below the
        cursor first and only then let pyte trim the top."""
        scr = self._screen
        extra = scr.lines - rows
        if extra > 0:
            blank = 0
            for y in range(scr.lines - 1, scr.cursor.y, -1):
                if any(scr.buffer[y][x].data.strip() for x in range(scr.columns)):
                    break
                blank += 1
            drop = min(extra, blank)
            if drop:
                for y in range(scr.lines - drop, scr.lines):
                    scr.buffer.pop(y, None)
                scr.lines -= drop
        scr.resize(rows, cols)
        scr.dirty.update(range(scr.lines))

    def sizeHint(self):
        return QtCore.QSize(int(80 * self._cw), int(30 * self._ch))

    # ── painting ───────────────────────────────────────────────────────────
    def paintEvent(self, ev):
        p = QtGui.QPainter(self)
        bg = QtGui.QColor(S.SURFACE)
        fg = QtGui.QColor(S.SURFACE_FG)
        p.fillRect(self.rect(), bg)
        scr = self._screen
        cw, ch = self._cw, self._ch
        sel = self._norm_sel()
        for y in range(min(scr.lines, self._rows)):
            line = scr.buffer[y]
            x = 0
            while x < min(scr.columns, self._cols):
                c = line[x]
                # extend a run of identical attributes
                key = (c.fg, c.bg, c.bold, c.italics, c.underscore, c.strikethrough, c.reverse)
                x0 = x
                text = []
                while x < min(scr.columns, self._cols):
                    cc = line[x]
                    if (cc.fg, cc.bg, cc.bold, cc.italics, cc.underscore,
                            cc.strikethrough, cc.reverse) != key:
                        break
                    text.append(cc.data or " ")
                    x += 1
                f_, b_ = _color(key[0], fg), _color(key[1], bg)
                if key[6]:
                    f_, b_ = (b_ if key[1] != "default" else bg), \
                             (f_ if key[0] != "default" else fg)
                rect = QtCore.QRectF(x0 * cw, y * ch, (x - x0) * cw, ch)
                if b_ != bg:
                    p.fillRect(rect, b_)
                font = self._bold if key[2] else self._font
                if key[3] or key[4] or key[5]:
                    font = QtGui.QFont(font)
                    font.setItalic(bool(key[3]))
                    font.setUnderline(bool(key[4]))
                    font.setStrikeOut(bool(key[5]))
                p.setFont(font)
                p.setPen(f_)
                s = "".join(text)
                if s.strip():
                    for i, chx in enumerate(s):       # per-cell: keep the grid aligned
                        if chx != " ":
                            p.drawText(QtCore.QPointF((x0 + i) * cw, y * ch + self._ascent), chx)
            if sel:
                (c0, r0), (c1, r1) = sel
                if r0 <= y <= r1:
                    a = c0 if y == r0 else 0
                    b = c1 if y == r1 else self._cols - 1
                    col = QtGui.QColor(S.ACCENT)
                    col.setAlpha(90)
                    p.fillRect(QtCore.QRectF(a * cw, y * ch, (b - a + 1) * cw, ch), col)
        cur = scr.cursor
        if not cur.hidden and scr.history.position == scr.history.size \
                and cur.y < self._rows and cur.x < self._cols:
            r = QtCore.QRectF(cur.x * cw, cur.y * ch, cw, ch)
            if self.hasFocus():
                col = QtGui.QColor(S.ACCENT)
                col.setAlpha(170)
                p.fillRect(r, col)
            else:
                p.setPen(QtGui.QColor(S.ACCENT))
                p.drawRect(r.adjusted(0, 0, -1, -1))
        p.end()

    def focusInEvent(self, ev):
        super().focusInEvent(ev)
        self.update()

    def focusOutEvent(self, ev):
        super().focusOutEvent(ev)
        self.update()

    def focusNextPrevChild(self, nxt):   # Tab belongs to the shell, not Qt
        return False

    # ── keyboard ───────────────────────────────────────────────────────────
    _KEYS = {
        QtCore.Qt.Key_Return: b"\r", QtCore.Qt.Key_Enter: b"\r",
        QtCore.Qt.Key_Backspace: b"\x7f", QtCore.Qt.Key_Tab: b"\t",
        QtCore.Qt.Key_Backtab: b"\x1b[Z", QtCore.Qt.Key_Escape: b"\x1b",
        QtCore.Qt.Key_Home: b"\x1b[H", QtCore.Qt.Key_End: b"\x1b[F",
        QtCore.Qt.Key_Insert: b"\x1b[2~", QtCore.Qt.Key_Delete: b"\x1b[3~",
        QtCore.Qt.Key_PageUp: b"\x1b[5~", QtCore.Qt.Key_PageDown: b"\x1b[6~",
    }
    _ARROWS = {QtCore.Qt.Key_Up: b"A", QtCore.Qt.Key_Down: b"B",
               QtCore.Qt.Key_Right: b"C", QtCore.Qt.Key_Left: b"D"}

    def keyPressEvent(self, ev):
        mod = ev.modifiers()
        mac = sys.platform == "darwin"
        # Qt on macOS swaps Ctrl/Cmd: ControlModifier is Cmd, MetaModifier is Ctrl.
        # Copy/paste: Cmd+C/V on macOS, Ctrl+Shift+C/V elsewhere.
        if mac:
            cmd = bool(mod & QtCore.Qt.ControlModifier)
        else:
            cmd = bool(mod & QtCore.Qt.ControlModifier and mod & QtCore.Qt.ShiftModifier)
        ctrl = bool(mod & (QtCore.Qt.MetaModifier if mac else QtCore.Qt.ControlModifier))
        key = ev.key()
        if cmd and key == QtCore.Qt.Key_C:
            self.copy_selection()
            return
        if cmd and key == QtCore.Qt.Key_V:
            self.paste()
            return
        if key in self._ARROWS:
            app = _PRIVATE(_pm.DECCKM) in self._screen.mode
            pre = b"\x1bO" if app and not mod & QtCore.Qt.ShiftModifier else b"\x1b["
            if mod & (QtCore.Qt.ShiftModifier | QtCore.Qt.AltModifier) and not app:
                pre = b"\x1b[1;" + (b"3" if mod & QtCore.Qt.AltModifier else b"2")
            self.write(pre + self._ARROWS[key])
            return
        if key in self._KEYS:
            self.write(self._KEYS[key])
            return
        if QtCore.Qt.Key_F1 <= key <= QtCore.Qt.Key_F12:
            n = key - QtCore.Qt.Key_F1
            seq = [b"\x1bOP", b"\x1bOQ", b"\x1bOR", b"\x1bOS", b"\x1b[15~", b"\x1b[17~",
                   b"\x1b[18~", b"\x1b[19~", b"\x1b[20~", b"\x1b[21~", b"\x1b[23~", b"\x1b[24~"]
            self.write(seq[n])
            return
        if ctrl and QtCore.Qt.Key_A <= key <= QtCore.Qt.Key_Z:
            self.write(bytes([key - QtCore.Qt.Key_A + 1]))
            return
        if ctrl and key == QtCore.Qt.Key_Space:
            self.write(b"\x00")
            return
        text = ev.text()
        if text and text.isprintable():
            data = text.encode("utf-8")
            if mod & QtCore.Qt.AltModifier and not mac:
                data = b"\x1b" + data
            self.write(data)
            return
        super().keyPressEvent(ev)

    def paste(self):
        text = QtWidgets.QApplication.clipboard().text()
        if not text:
            return
        data = text.replace("\r\n", "\n").replace("\n", "\r").encode("utf-8")
        if _PRIVATE(2004) in self._screen.mode:     # bracketed paste
            data = b"\x1b[200~" + data + b"\x1b[201~"
        self.write(data)

    # ── selection / scrollback ─────────────────────────────────────────────
    def _cell(self, pos):
        return (min(max(int(pos.x() / self._cw), 0), self._cols - 1),
                min(max(int(pos.y() / self._ch), 0), self._rows - 1))

    def _norm_sel(self):
        if not self._sel:
            return None
        a, b = self._sel
        return (a, b) if (a[1], a[0]) <= (b[1], b[0]) else (b, a)

    def mousePressEvent(self, ev):
        self.setFocus()
        if ev.button() == QtCore.Qt.LeftButton:
            self._sel_anchor = self._cell(ev.pos())
            self._sel = None
            self.update()
        elif ev.button() == QtCore.Qt.MiddleButton:
            self.paste()

    def mouseMoveEvent(self, ev):
        if self._sel_anchor is not None and ev.buttons() & QtCore.Qt.LeftButton:
            self._sel = (self._sel_anchor, self._cell(ev.pos()))
            self.update()

    def mouseReleaseEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton:
            if self._sel and self._sel[0] == self._sel[1]:
                self._sel = None
            elif self._sel:
                self.copy_selection()          # copy-on-select, like most terminals
            self._sel_anchor = None
            self.update()

    def copy_selection(self):
        sel = self._norm_sel()
        if not sel:
            return
        (c0, r0), (c1, r1) = sel
        lines = []
        for y in range(r0, r1 + 1):
            a = c0 if y == r0 else 0
            b = c1 if y == r1 else self._cols - 1
            row = self._screen.buffer[y]
            lines.append("".join(row[x].data or " " for x in range(a, b + 1)).rstrip())
        QtWidgets.QApplication.clipboard().setText("\n".join(lines))

    def wheelEvent(self, ev):
        steps = ev.angleDelta().y() / 120.0
        if steps == 0:
            return
        scr = self._screen
        # alternate-screen/app mode programs have no scrollback to show
        n = max(1, int(abs(steps)))
        for _ in range(n):
            (scr.prev_page if steps > 0 else scr.next_page)()
        self._sel = None
        self.update()

    def _snap_to_bottom(self):
        scr = self._screen
        guard = 0
        while scr.history.position < scr.history.size and guard < 1000:
            scr.next_page()
            guard += 1
        if guard:
            self.update()


class ApexaDock(QtWidgets.QDockWidget):
    """Dockable/floatable panel hosting APEXA's CLI in a ``TerminalWidget``."""

    def __init__(self, parent=None):
        super().__init__("APEXA", parent)
        self.setObjectName("apexaDock")
        self.setAllowedAreas(QtCore.Qt.LeftDockWidgetArea | QtCore.Qt.RightDockWidgetArea
                             | QtCore.Qt.BottomDockWidgetArea)
        self.setFeatures(QtWidgets.QDockWidget.DockWidgetClosable
                         | QtWidgets.QDockWidget.DockWidgetMovable
                         | QtWidgets.QDockWidget.DockWidgetFloatable)
        self.term = TerminalWidget(self)
        self.setWidget(self.term)
        self.setMinimumWidth(320)

    def run(self, argv: List[str], cwd: str) -> None:
        """Start (or restart) the CLI and focus the terminal."""
        if not self.term.is_running():
            self.term.start(argv, cwd=cwd)
        self.show()
        self.raise_()
        self.term.setFocus()

    def closeEvent(self, ev):
        # Closing the panel only hides it; the CLI keeps running (re-open via
        # Tools ▸ APEXA (CLI)…). The process is hung up when the main window closes.
        super().closeEvent(ev)

    def shutdown(self) -> None:
        self.term.stop()
