"""One-command, cross-platform setup for the midas-gui MCP server.

    python midas_gui_mcp/install.py [--register] [--skills]

Stdlib only (run it with any Python >= 3.10, on macOS, Linux or Windows).

**Never writes inside the midas-gui checkout.** Everything lands in the user's
own profile, so ``git pull`` on the repo stays clean:

* the server's virtualenv  -> ``~/.midas_gui/mcp-venv`` (override: ``--venv``)
* ``--register``           -> ``claude mcp add --scope user`` (user scope =
  ``~/.claude.json``; the default *project* scope would write ``.mcp.json``
  into whatever directory you ran it from)
* ``--skills``             -> copies ``skills/*`` to ``~/.claude/skills``
  (copies, not symlinks: symlinks need admin rights / Developer Mode on Windows)

Without ``--register`` it only prints the exact command for your OS, so it also
works for MCP clients other than Claude Code.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import venv
from pathlib import Path
from typing import List

HERE = Path(__file__).resolve().parent
SERVER_NAME = "midas-gui"


def venv_python(venv_dir: Path, platform: str = sys.platform) -> Path:
    """Interpreter inside a venv: ``Scripts\\python.exe`` on Windows, else ``bin/python``."""
    if platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def register_command(python: Path, server: Path = HERE / "server.py") -> List[str]:
    """The ``claude mcp add`` argv. ``--scope user`` keeps it out of the repo."""
    return ["claude", "mcp", "add", "--transport", "stdio", "--scope", "user",
            SERVER_NAME, "--", str(python), str(server)]


def ensure_venv(venv_dir: Path) -> Path:
    py = venv_python(venv_dir)
    if not py.exists():
        print(f"Creating virtualenv: {venv_dir}")
        venv.EnvBuilder(with_pip=True).create(str(venv_dir))
    subprocess.check_call([str(py), "-m", "pip", "install", "--quiet", "-r",
                           str(HERE / "requirements.txt")])
    return py


def install_skills(dest: Path) -> List[str]:
    dest.mkdir(parents=True, exist_ok=True)
    done = []
    for src in sorted((HERE / "skills").iterdir()):
        if src.is_dir():
            target = dest / src.name
            if target.is_symlink():
                target.unlink()
            shutil.copytree(src, target, dirs_exist_ok=True)
            done.append(src.name)
    return done


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--venv", type=Path, default=Path.home() / ".midas_gui" / "mcp-venv")
    ap.add_argument("--register", action="store_true",
                    help="run `claude mcp add --scope user` (needs the claude CLI on PATH)")
    ap.add_argument("--skills", action="store_true",
                    help="copy the agent skills into ~/.claude/skills")
    args = ap.parse_args(argv)

    py = ensure_venv(args.venv.expanduser())
    cmd = register_command(py)
    if args.skills:
        names = install_skills(Path.home() / ".claude" / "skills")
        print(f"Copied skills to ~/.claude/skills: {', '.join(names)}")
    if args.register:
        if not shutil.which("claude"):
            print("`claude` not found on PATH; run this yourself:\n  " + subprocess.list2cmdline(cmd))
            return 1
        return subprocess.call(cmd)
    print("Register with your MCP client (stdio transport):\n  " + subprocess.list2cmdline(cmd))
    print("Then restart the client session and start midas-gui (control socket is on by default).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
