"""Tests for midas_gui._paths's import-time environment side effects.

HDF5_USE_FILE_LOCKING=FALSE specifically: HDF5's default flock() on every
opened file can hang *indefinitely* (not just slowly) on NFS-style mounts
that don't grant it — confirmed live against a real network-mounted VAREX
HDF5 source in Batch Integrate's Detector-view preview (see
.context/DECISIONS.md, 2026-09-25). Each check spawns a fresh subprocess
(env vars set at import time by module-level code can't be un-set/re-tested
within one already-running interpreter — pytest's own collection may well
have already imported midas_gui._paths before any test here runs).
"""
import os
import subprocess
import sys

import pytest


def _run(snippet: str, env_overrides: dict) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(env_overrides)
    return subprocess.run([sys.executable, "-c", snippet],
                          capture_output=True, text=True, env=env)


def test_hdf5_file_locking_defaults_to_false():
    env = dict(os.environ)
    env.pop("HDF5_USE_FILE_LOCKING", None)
    r = _run("import midas_gui._paths, os; "
             "print(os.environ.get('HDF5_USE_FILE_LOCKING'))", env)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "FALSE"


def test_hdf5_file_locking_respects_a_users_own_override():
    r = _run("import midas_gui._paths, os; "
             "print(os.environ.get('HDF5_USE_FILE_LOCKING'))",
             {"HDF5_USE_FILE_LOCKING": "TRUE"})
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "TRUE"


def test_batch_cli_actually_gets_the_paths_env_setup():
    """Regression test for the gap this session found: batch_cli.py is a
    standalone entry point (python -m midas_gui.batch_cli, launched detached
    by "Run as background job") that never went through app.py's import
    chain, so it got neither KMP_DUPLICATE_LIB_OK nor HDF5_USE_FILE_LOCKING
    — a long-running background job reading HDF5 over the same NFS mount was
    just as exposed to the locking hang as the interactive GUI."""
    pytest.importorskip("PyQt5")
    env = dict(os.environ)
    env.pop("HDF5_USE_FILE_LOCKING", None)
    r = _run("import midas_gui.batch_cli, os; "
             "print(os.environ.get('HDF5_USE_FILE_LOCKING'))", env)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "FALSE"
