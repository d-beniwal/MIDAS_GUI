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

2026-09-25: brought ``script``/``script_sha256`` (in ``build_entry``) and
``tag`` (in ``_git_rev``) back into parity with the source's fields — a
direct field-by-field diff against mpe_wf_saxs_waxs's ``provenance.py``
turned these up as the only unintentional gaps; everything else that
differs (``midas_gui``/``backends`` replacing ``git``/``mpe_wf``/``midas``,
and no standalone ``git`` field) is the deliberate, already-documented
one-repo/PyPI-backend adaptation above, not a gap. The zarr array/group
schema itself (``REtaMap``, ``InstrumentParameters/<key>``, ``Omegas``,
``provenance_history``) needed no reconciliation: both projects' single-panel
``.zarr.zip`` files go through the same shared backend writer
(``midas_integrate_v2.io.zarr_gsas.write_gsas_zarr_zip`` — see
``midas_gui/gsas_export.py``'s docstring), so it's identical by
construction, verified against mpe_wf's own ``combine_hydra_zarr.py``
(which expects exactly this layout from every panel it merges).

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

instrument_params_from_spec(spec) -> dict | None
    The same snapshot, taken off a live IntegrationSpec instead of a file,
    so a writer records the geometry it actually ran with. Every path that
    writes a .zarr.zip passes this to build_entry's instrument_params, so
    the files carry an identical record whichever path produced them.
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

    script = os.path.realpath(sys.argv[0]) if sys.argv and sys.argv[0] else ''

    entry = {
        'tool':         tool,
        'utc_time':     datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'host':         socket.gethostname(),
        'user':         _safe_user(),
        'cwd':          os.getcwd(),
        'command':      command_str,
        'script':       script,
        'script_sha256': _file_sha256(script) if script and os.path.isfile(script) else None,
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

def rewrite_zip(zarr_zip_path: str | Path, mutate) -> None:
    """Extract a ``.zarr.zip``, hand the directory to ``mutate``, repack it.

    A zip-backed zarr store can't be edited in place, so every after-the-fact
    change to one costs a full extract/repack. ``mutate`` receives the
    extracted root as a ``Path`` and may do anything it likes to it — stamp
    provenance, add whole groups — which is the point: a caller with several
    changes to make pays for one pass instead of one per change.

    Atomic on the destination: writes to ``<path>.provtmp`` then renames, so
    an interrupted run leaves the original intact rather than a half-written
    archive.
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

        mutate(extracted)

        new_zip = path.with_suffix(path.suffix + '.provtmp')
        with zipfile.ZipFile(new_zip, 'w',
                              compression=zipfile.ZIP_DEFLATED,
                              allowZip64=True) as zf:
            for root_dir, _dirs, files in sorted(os.walk(extracted)):
                for fname in sorted(files):
                    abs_path = Path(root_dir) / fname
                    arcname = abs_path.relative_to(extracted).as_posix()
                    zf.write(abs_path, arcname)
        os.replace(new_zip, path)


def stamp_extracted(extracted_dir: str | Path, entry: dict) -> None:
    """Append ``entry`` to the root ``.zattrs`` of an extracted zarr store.

    The in-directory half of :func:`append_to_zip`, split out so it can be
    composed with other edits inside a single :func:`rewrite_zip` pass.
    """
    extracted = Path(extracted_dir)
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


def append_to_zip(zarr_zip_path: str | Path, entry: dict) -> None:
    """Append ``entry`` to the provenance_history of an existing zip-backed
    zarr store on disk. Extracts the archive into a temp directory,
    rewrites .zattrs at the root, then repacks deterministically.

    Atomic on the destination: writes to ``<path>.provtmp`` then renames.
    """
    rewrite_zip(zarr_zip_path, lambda extracted: stamp_extracted(extracted, entry))


# ── Writing into an HDF5 file/group ─────────────────────────────────────

def append_to_hdf5_attrs(h5_group, entry: dict) -> None:
    """Append ``entry`` to ``h5_group.attrs['provenance_history']``.

    h5py attrs don't support a native list-of-dict, so the history is kept
    as a JSON string (matching mpe_wf_saxs_waxs's repair_hdf5_frames.py
    precedent, generalized from one entry to an appended list for
    consistency with the zarr side above). Indented (``indent=2``, matching
    ``project.py``'s own ``metadata`` dataset) rather than one compact line —
    a single unbroken line is unreadable in an HDF5 viewer's string preview
    (confirmed with VS Code's H5Web).
    """
    raw = h5_group.attrs.get('provenance_history')
    history = json.loads(raw) if raw else []
    history.append(entry)
    h5_group.attrs['provenance_history'] = json.dumps(history, indent=2, default=str)


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


# ── Geometry snapshot from a live spec ────────────────────────────────

def instrument_params_from_spec(spec) -> dict | None:
    """Snapshot the calibration geometry carried by an ``IntegrationSpec``.

    The companion to ``read_instrument_params``, which parses the same kind
    of snapshot out of a paramstest file on disk. This one reads it off the
    spec object an integration is actually about to run with, so what gets
    recorded is what was used rather than what some file said earlier.

    Why this is worth recording at all: a ``.zarr.zip`` already contains the
    geometry, but only *applied* — baked into ``REtaMap``'s per-bin
    Radius/2Theta/Eta/Q columns. Recovering ``tx`` from that means inverting
    the map. Only ``Lsd`` and the wavelength survive as readable numbers, in
    ``InstrumentParameters/`` (whose other entries — ``U``/``V``/``W``,
    ``Polariz``, ``SH_L``, ``X``/``Y``/``Z`` — are GSAS-II peak-profile
    defaults from the backend writer, not anything MIDAS refined, and are
    easy to mistake for calibration output).

    Values arrive as 0-dim torch tensors and have to be plain Python before
    they can reach a JSON attr, so everything is coerced on the way out.

    All fifteen distortion harmonics are recorded even when they are zero:
    "no distortion was applied" is a positive statement about the run, and
    absent keys can't distinguish it from "this writer didn't record them".
    Panel fields and the residual-correction map appear only when in use —
    those are genuinely not part of a plain single-panel geometry.

    Returns ``None`` for a spec carrying no recognisable geometry, so a
    caller can pass the result straight to ``build_entry`` and get the key
    omitted rather than an empty dict.
    """
    if spec is None:
        return None

    out: dict = {}
    for key in ("Lsd", "BC_y", "BC_z", "tx", "ty", "tz",
                "pxY", "pxZ", "NrPixelsY", "NrPixelsZ",
                "Wavelength", "RhoD"):
        val = _plain(getattr(spec, key, None))
        if val is not None:
            out[key] = val
    if not out:
        return None

    from midas_gui.constants import DISTORTION_NAMES
    distortion = {}
    for name in DISTORTION_NAMES:
        val = _plain(getattr(spec, name, None))
        if val is not None:
            distortion[name] = val
    if distortion:
        out["distortion"] = distortion

    # im_trans: the flip/transpose the backend applies internally. Part of
    # the geometry in practice — the same Lsd/BC against a transposed frame
    # is a different detector.
    trans = _plain(getattr(spec, "TransOpt", None))
    out["TransOpt"] = list(trans) if trans else []

    if _plain(getattr(spec, "NPanelsY", 0)) or _plain(getattr(spec, "NPanelsZ", 0)):
        panels = {}
        for key in ("NPanelsY", "NPanelsZ", "PanelSizeY", "PanelSizeZ",
                    "PanelGapsY", "PanelGapsZ", "PanelShiftsFile"):
            val = _plain(getattr(spec, key, None))
            if val is not None:
                panels[key] = val
        out["panels"] = panels

    # A calibration product in its own right (Calibrate's "Build residual
    # map"), so the path belongs with the geometry that needs it.
    residual = _plain(getattr(spec, "ResidualCorrectionMap", None))
    if residual:
        out["ResidualCorrectionMap"] = residual

    return out


# ── Internal helpers ──────────────────────────────────────────────────

def _maybe_float(tok: str):
    try:
        return float(tok)
    except ValueError:
        return tok


def _plain(v):
    """Coerce a spec field to something ``json.dumps`` accepts.

    Geometry fields come off an ``IntegrationSpec`` as 0-dim torch tensors;
    panel gaps come off as lists; a few are already plain. ``.item()`` covers
    torch/numpy scalars, ``.tolist()`` covers real arrays, and anything else
    unrecognised degrades to ``str`` rather than blowing up a provenance
    stamp — a stamp is best-effort and must never fail the write it
    describes.
    """
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    for meth in ("item", "tolist"):
        fn = getattr(v, meth, None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                pass
    return str(v)


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
        # Latest annotated tag reachable, if any (separate from describe so
        # callers can distinguish "tagged exactly" vs "N commits past").
        tag = subprocess.run(
            ['git', '-C', repo_dir, 'describe', '--tags', '--abbrev=0'],
            capture_output=True, text=True, timeout=2).stdout.strip()
        return {
            'commit':   rev.stdout.strip(),
            'short':    short,
            'branch':   branch or None,
            'dirty':    bool(dirty),
            'describe': describe or None,
            'tag':      tag or None,
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
