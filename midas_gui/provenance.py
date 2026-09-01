"""Provenance stamping for MIDAS_GUI outputs (zarr cakes and HDF5 files).

Ported from mpe_wf_saxs_waxs's ``provenance.py`` (see
QUESTIONS_FOR_COLLEAGUES.md / the convergence roadmap) — same entry schema
and the same ``provenance_history`` list-at-the-root convention, so a file
produced by either project reads the same way. Adapted for two differences
from the source environment: MIDAS_GUI doesn't vendor a git checkout of
MIDAS (it calls PyPI-published `midas-calibrate-v2`/`midas-integrate-v2`
etc. — see this repo's own CLAUDE.md), so backend identity is recorded as
installed package versions instead of a second repo's git info; and MIDAS_GUI
writes to both zarr (new cake output) and HDF5 (existing project/Batch
Integrate output), so there are two storage adapters instead of one.

Public entry points
--------------------
build_entry(tool, *, inputs=(), cake_params=None, instrument_params=None,
            command=None, extra=None, compute_checksums=True)
    Construct a provenance dict for one writer.

append_to_zarr_group(root_group, entry)
    Append entry to root_group.attrs['provenance_history']. Use while the
    store is still mutable (e.g. building a fresh zarr.ZipStore group).

append_to_zip(zarr_zip_path, entry)
    Append entry to an existing .zarr.zip on disk (extract -> modify ->
    re-zip -> atomic rename). For stamping a zarr file after it's already
    been finalized elsewhere.

append_to_hdf5_attrs(h5_group, entry)
    Append entry to h5_group.attrs['provenance_history'] (JSON-encoded,
    since h5py attrs don't support native list-of-dict).

read_instrument_params(path) -> dict | None
    Parse a MIDAS paramstest.txt-style file into a flat dict, for embedding
    a snapshot of the geometry alongside a provenance entry.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path


# ── Entry construction ─────────────────────────────────────────────────

def build_entry(tool: str,
                *,
                inputs: list | tuple = (),
                cake_params: dict | None = None,
                instrument_params: dict | None = None,
                command: list | tuple | str | None = None,
                extra: dict | None = None,
                compute_checksums: bool = True) -> dict:
    """Build one provenance entry.

    ``tool`` is a short, stable identifier for the writer (e.g.
    ``"batch_integrate"``, ``"calibrate"``).

    ``inputs`` is a list of file paths; each is recorded with size, mtime,
    and (if ``compute_checksums``) sha256.

    ``cake_params`` / ``instrument_params`` are dicts embedded verbatim —
    typically the R/eta bin config and a paramstest-style geometry snapshot.
    """
    if command is None:
        command = sys.argv
    if isinstance(command, (list, tuple)):
        command_str = ' '.join(str(c) for c in command)
    else:
        command_str = str(command)

    entry = {
        'tool':         tool,
        'utc_time':     datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'host':         socket.gethostname(),
        'user':         _safe_user(),
        'cwd':          os.getcwd(),
        'command':      command_str,
        'midas_gui':    _repo_info(_resolve_midas_gui_dir()),
        'backends':     _backend_versions(),
        'python':       sys.version.split()[0],
        'zarr':         _zarr_version(),
        'inputs':       [_file_metadata(p, compute_checksums) for p in inputs],
    }
    if cake_params is not None:
        entry['cake_params'] = dict(cake_params)
    if instrument_params is not None:
        entry['instrument_params'] = dict(instrument_params)
    if extra:
        entry['extra'] = dict(extra)
    return entry


# ── Writing into a live zarr group ─────────────────────────────────────

def append_to_zarr_group(root, entry: dict) -> None:
    """Append ``entry`` to ``root.attrs['provenance_history']``.

    Works for any zarr store that allows attrs updates (DirectoryStore,
    MemoryStore, freshly-opened ZipStore in 'w' mode). For an existing
    .zarr.zip on disk that needs to be updated, use ``append_to_zip``.
    """
    history = list(root.attrs.get('provenance_history', []))
    history.append(entry)
    root.attrs['provenance_history'] = history


# ── Writing into an existing .zarr.zip ─────────────────────────────────

def append_to_zip(zarr_zip_path: str | Path, entry: dict) -> None:
    """Append ``entry`` to the provenance_history of an existing zip-backed
    zarr store on disk. Extracts the archive into a temp directory,
    rewrites .zattrs at the root, then repacks deterministically.

    Atomic on the destination: writes to ``<path>.provtmp`` then renames.
    """
    path = Path(zarr_zip_path)
    if not path.is_file():
        raise FileNotFoundError(path)

    with tempfile.TemporaryDirectory(prefix='provstamp_') as tmp:
        tmp = Path(tmp)
        extracted = tmp / 'extracted'
        extracted.mkdir()
        with zipfile.ZipFile(path, 'r') as zf:
            zf.extractall(extracted)

        zattrs_path = extracted / '.zattrs'
        attrs = {}
        if zattrs_path.is_file():
            try:
                with open(zattrs_path) as f:
                    attrs = json.load(f)
            except (OSError, json.JSONDecodeError):
                attrs = {}
        history = list(attrs.get('provenance_history', []))
        history.append(entry)
        attrs['provenance_history'] = history
        zgroup_path = extracted / '.zgroup'
        if not zgroup_path.is_file():
            with open(zgroup_path, 'w') as f:
                json.dump({'zarr_format': 2}, f)
        with open(zattrs_path, 'w') as f:
            json.dump(attrs, f, indent=2)

        new_zip = path.with_suffix(path.suffix + '.provtmp')
        with zipfile.ZipFile(new_zip, 'w',
                              compression=zipfile.ZIP_DEFLATED,
                              allowZip64=True) as zf:
            for root_dir, _dirs, files in os.walk(extracted):
                for fname in files:
                    abs_path = Path(root_dir) / fname
                    arcname = abs_path.relative_to(extracted).as_posix()
                    zf.write(abs_path, arcname)
        os.replace(new_zip, path)


# ── Writing into an HDF5 file/group ─────────────────────────────────────

def append_to_hdf5_attrs(h5_group, entry: dict) -> None:
    """Append ``entry`` to ``h5_group.attrs['provenance_history']``.

    h5py attrs don't support a native list-of-dict, so the history is kept
    as a JSON string (matching mpe_wf_saxs_waxs's repair_hdf5_frames.py
    precedent, generalized from one entry to an appended list for
    consistency with the zarr side above).
    """
    raw = h5_group.attrs.get('provenance_history')
    history = json.loads(raw) if raw else []
    history.append(entry)
    h5_group.attrs['provenance_history'] = json.dumps(history, default=str)


# ── Configuration snapshot parser ──────────────────────────────────────

def read_instrument_params(path: str | Path | None) -> dict | None:
    """Parse a MIDAS-style paramstest (key value [#comment]) into a flat
    dict. Values are cast to float when possible, otherwise kept as
    strings. Returns None if the file is missing / unparseable."""
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    out: dict = {}
    try:
        with open(p) as f:
            for raw in f:
                line = raw.split('#', 1)[0].strip().rstrip(';').strip()
                if not line:
                    continue
                tokens = line.split()
                if len(tokens) < 2:
                    continue
                key = tokens[0]
                rest = tokens[1:]
                cast = [_maybe_float(t) for t in rest]
                out[key] = cast[0] if len(cast) == 1 else cast
    except OSError:
        return None
    return out or None


# ── Internal helpers ──────────────────────────────────────────────────

def _maybe_float(tok: str):
    try:
        return float(tok)
    except ValueError:
        return tok


def _safe_user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return os.environ.get('USER', 'unknown')


def _file_metadata(path: str | Path, compute_checksum: bool) -> dict:
    """Metadata for one recorded input. MIDAS_GUI's Batch Integrate inputs
    are sometimes a directory/glob (a tiff_glob folder), unlike mpe_wf's own
    inputs which are always individual files — record that plainly instead
    of a raw 'sha256: Is a directory' OSError message."""
    p = Path(path)
    meta: dict = {'path': str(p)}
    if p.is_dir():
        meta['kind'] = 'directory'
        return meta
    try:
        st = p.stat()
        meta['size'] = int(st.st_size)
        meta['mtime'] = datetime.fromtimestamp(
            st.st_mtime, timezone.utc).isoformat(timespec='seconds')
    except OSError as e:
        meta['error'] = f'stat: {e}'
        return meta
    if compute_checksum:
        try:
            meta['sha256'] = _file_sha256(p)
        except OSError as e:
            meta['error'] = f'sha256: {e}'
    return meta


def _file_sha256(path: str | Path, block: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(block), b''):
            h.update(chunk)
    return h.hexdigest()


def _git_rev(repo_dir: str) -> dict | None:
    """Best-effort git rev + dirty flag + describe for ``repo_dir``.
    Returns None if not a git repo or git is missing."""
    if not repo_dir or not os.path.isdir(repo_dir):
        return None
    try:
        rev = subprocess.run(
            ['git', '-C', repo_dir, 'rev-parse', 'HEAD'],
            capture_output=True, text=True, timeout=2)
        if rev.returncode != 0:
            return None
        short = subprocess.run(
            ['git', '-C', repo_dir, 'rev-parse', '--short', 'HEAD'],
            capture_output=True, text=True, timeout=2).stdout.strip()
        dirty = subprocess.run(
            ['git', '-C', repo_dir, 'status', '--porcelain'],
            capture_output=True, text=True, timeout=2).stdout.strip()
        branch = subprocess.run(
            ['git', '-C', repo_dir, 'rev-parse', '--abbrev-ref', 'HEAD'],
            capture_output=True, text=True, timeout=2).stdout.strip()
        describe = subprocess.run(
            ['git', '-C', repo_dir, 'describe', '--tags', '--always', '--dirty'],
            capture_output=True, text=True, timeout=2).stdout.strip()
        return {
            'commit':   rev.stdout.strip(),
            'short':    short,
            'branch':   branch or None,
            'dirty':    bool(dirty),
            'describe': describe or None,
        }
    except (OSError, subprocess.TimeoutExpired):
        return None


def _resolve_midas_gui_dir() -> str | None:
    """The on-disk root of the running MIDAS_GUI checkout — two levels up
    from this file (midas_gui/provenance.py -> repo root). Honors
    MIDAS_GUI_DIR for a caller that's relocated the package."""
    env = os.environ.get('MIDAS_GUI_DIR')
    if env and os.path.isdir(env):
        return env
    here = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    return here if os.path.isdir(here) else None


def _repo_info(repo_dir: str | None) -> dict | None:
    """Like ``_git_rev`` but also records the resolved path."""
    if not repo_dir:
        return None
    rev = _git_rev(repo_dir)
    if rev is None:
        return {'path': repo_dir}
    rev['path'] = repo_dir
    return rev


def _backend_versions() -> dict:
    """Installed versions of the PyPI-published MIDAS backend packages
    MIDAS_GUI calls into (calib.py/workers.py) — see this repo's CLAUDE.md
    on why these are pip packages, not a vendored git checkout."""
    import importlib.metadata as _md
    names = ('midas-calibrate-v2', 'midas-integrate-v2',
             'midas-calibrate', 'midas-integrate',
             'midas-hkls', 'midas-distortion')
    out = {}
    for name in names:
        try:
            out[name] = _md.version(name)
        except _md.PackageNotFoundError:
            pass
    return out


def _zarr_version() -> str | None:
    try:
        import zarr
        return getattr(zarr, '__version__', None)
    except ImportError:
        return None
