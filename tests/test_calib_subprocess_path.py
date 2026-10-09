"""A spawned `python -m midas_gui.calib_cli` must import from any cwd.

Reported at 1-ID-E: every calibration died with

    Error while finding module specification for 'midas_gui.calib_cli'
    (ModuleNotFoundError: No module named 'midas_gui')

The GUI had been started by absolute path from a data directory
(`python /opt/MIDAS_GUI/launch.py` run in `connolly_oct26_bc`). That works
for the GUI itself -- launch.py's own directory goes on sys.path -- but the
calibration subprocess inherits the cwd, and `-m` resolves the package off
sys.path[0], which for -m is the current directory. So the package was
invisible to the child and visible to the parent.
"""
import os
import subprocess
import sys
from pathlib import Path

from midas_gui.workers import _pythonpath_with_repo_root

REPO_ROOT = str(Path(__file__).resolve().parent.parent)


def test_the_repo_root_is_what_gets_prepended():
    assert _pythonpath_with_repo_root("").split(os.pathsep)[0] == REPO_ROOT


def test_an_empty_pythonpath_yields_no_stray_separator():
    """A trailing separator reads as "the current directory" to the
    interpreter -- precisely the assumption that broke."""
    out = _pythonpath_with_repo_root("")
    assert out == REPO_ROOT
    assert not out.endswith(os.pathsep)


def test_an_existing_pythonpath_is_kept_after_the_root():
    out = _pythonpath_with_repo_root(f"/a{os.pathsep}/b")
    assert out.split(os.pathsep) == [REPO_ROOT, "/a", "/b"]


def test_the_root_is_not_duplicated():
    out = _pythonpath_with_repo_root(f"/a{os.pathsep}{REPO_ROOT}")
    assert out.split(os.pathsep).count(REPO_ROOT) == 1


def test_empty_entries_are_dropped():
    out = _pythonpath_with_repo_root(f"{os.pathsep}/a{os.pathsep}")
    assert out.split(os.pathsep) == [REPO_ROOT, "/a"]


def test_the_module_really_imports_from_an_unrelated_cwd(tmp_path):
    """The actual failure, end to end: run the import from a directory that
    is not the repo root, exactly as the GUI's subprocess does."""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    cmd = [sys.executable, "-c", "import midas_gui.calib_cli"]

    bare = subprocess.run(cmd, cwd=str(tmp_path), env=env,
                          capture_output=True, text=True)
    assert bare.returncode != 0, (
        "fixture is not exercising the bug -- midas_gui is importable from "
        "an unrelated cwd even without PYTHONPATH (installed into the env?)")
    assert "No module named 'midas_gui'" in bare.stderr

    env["PYTHONPATH"] = _pythonpath_with_repo_root("")
    fixed = subprocess.run(cmd, cwd=str(tmp_path), env=env,
                           capture_output=True, text=True)
    assert fixed.returncode == 0, fixed.stderr
