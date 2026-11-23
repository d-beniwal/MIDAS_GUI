#!/usr/bin/env python3
"""Pre-flight for dependency bumps: can requirements.txt install on every OS
using binary wheels only (no compiler)?

    python check_platform_wheels.py           # needs `uv` on PATH (pip install uv)

Resolves the full tree (transitive deps included) for Windows, Linux x86_64 and
macOS arm64 with ``--only-binary :all:``. This is how a source-only dependency
like ``midas-index`` (CMake + C, fails on Windows without MSVC) is caught before
users hit it. Run from the repo root. Exit code 1 if Windows or Linux x86_64
fails; other platforms are reported as warnings (known gaps are in the README).
"""
import os
import shutil
import subprocess
import sys
import tempfile

REQUIRED = ["windows", "x86_64-manylinux_2_28"]
OPTIONAL = ["aarch64-apple-darwin", "aarch64-manylinux_2_28", "x86_64-apple-darwin"]


def main() -> int:
    if not shutil.which("uv"):
        print("uv not found (pip install uv)")
        return 2
    failed = False
    for plat in REQUIRED + OPTIONAL:
        out = os.path.join(tempfile.gettempdir(), f"midas_gui_lock_{plat}.txt")
        try:
            r = subprocess.run(
                ["uv", "pip", "compile", "requirements.txt", "--python-version", "3.12",
                 "--python-platform", plat, "--only-binary", ":all:", "-q", "--no-header",
                 "-o", out], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                timeout=300)
        except subprocess.TimeoutExpired:
            r = subprocess.CompletedProcess([], 1, "", "timed out (network?)")
        ok = r.returncode == 0
        print(f"{'OK  ' if ok else 'FAIL' if plat in REQUIRED else 'warn'} {plat}")
        if not ok:
            print("     " + (r.stderr.strip().splitlines() or ["?"])[0][:200])
            failed |= plat in REQUIRED
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
