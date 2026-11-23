"""Cross-platform plumbing for midas_gui_mcp: install helper + control-client transport."""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "midas_gui_mcp"))

import client  # noqa: E402
import install  # noqa: E402


def test_venv_python_per_platform():
    v = Path("v")
    assert install.venv_python(v, "win32") == v / "Scripts" / "python.exe"
    assert install.venv_python(v, "darwin") == v / "bin" / "python"
    assert install.venv_python(v, "linux") == v / "bin" / "python"


def test_register_command_is_user_scope_never_project():
    cmd = install.register_command(Path("py"), Path("srv.py"))
    assert cmd[:3] == ["claude", "mcp", "add"]
    assert cmd[cmd.index("--scope") + 1] == "user"
    assert cmd[-3:] == ["--", "py", "srv.py"]


def test_install_helper_default_paths_are_outside_the_repo():
    repo = Path(install.HERE).parent
    default_venv = Path.home() / ".midas_gui" / "mcp-venv"
    assert repo not in default_venv.parents


def test_unix_transport_is_a_socket(tmp_path, monkeypatch):
    import socket
    p = str(tmp_path / "s.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(p)
    srv.listen(1)
    monkeypatch.setattr(client.sys, "platform", "linux")
    t = client._open_transport(p, 1.0)
    conn, _ = srv.accept()
    t.sendall(b"hi\n")
    assert conn.recv(10) == b"hi\n"
    t.close(); conn.close(); srv.close()


def test_windows_transport_selected_and_pipe_name_normalised(monkeypatch, tmp_path):
    seen = {}

    def fake_open(path, mode, buffering):
        seen["args"] = (path, mode, buffering)
        return io.open(tmp_path / "pipe", "w+b", buffering=0)

    monkeypatch.setattr(client.sys, "platform", "win32")
    monkeypatch.setattr("builtins.open", fake_open)
    t = client._open_transport("midas_gui_control_v1", 1.0)
    assert isinstance(t, client._PipeTransport)
    assert seen["args"] == (r"\\.\pipe\midas_gui_control_v1", "r+b", 0)
    t.sendall(b"x")
    t.close()
