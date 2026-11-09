"""Discovering and reading 1-D / 2-D streams out of HDF5 and zarr sources.

The Qt-free IO layer behind the Data Explorer tab, ported from
``mpe_wf_saxs_waxs/gui_data_explorer.py`` (lines 64-367 and 396-448). No
``QApplication`` is needed to use or test any of it, which is why
``tests/test_stream_sources.py`` is a plain pytest file.

Deliberately FORMAT-AGNOSTIC. It walks a container and reports whatever
numeric arrays it finds; it knows nothing about MIDAS's own zarr schema.
``tab_zarrviewer`` is the MIDAS-schema reader and does that job properly --
a weaker second implementation here would be the mistake. Two consequences
worth knowing before wondering whether this is broken:

  * ``REtaMap`` is 3-D ``(5, nR, nEta)`` and ``InstrumentParameters/<key>``
    are length-1, so neither is visible to these walkers (ndim 1-2, and
    ``shape[0] >= 2``). That is correct: neither is a per-frame stream.
  * What IS visible, and is the reason this is useful against this app's
    own output, is ``instrument/<pv>`` -- ``h5_metadata.align()`` writes the
    source HDF5's EPICS PV tree into every produced archive, averaged per
    output frame. Verified on a real archive: 12 discoverable streams.
    A TIFF-sourced archive has no such tree and will look empty here.

``parse_cake_csv`` and ``CAKE_KEYS`` are NOT re-ported -- ``cake_params``
already carries a verbatim copy. They are re-exported below so callers
have one import to reach the whole source layer.
"""
from __future__ import annotations

import os
import re

import numpy as np

try:
    import h5py
except ImportError:                                  # pragma: no cover
    h5py = None

try:
    import zarr
except ImportError:                                  # pragma: no cover
    zarr = None

# Re-exported, not re-implemented: cake_params owns these already.
from midas_gui.cake_params import CAKE_KEYS, parse_cake_csv  # noqa: F401


_MOTOR_HINT_RX = re.compile(
    r'(sam[rR]?[xyz_]|^[xyz]$|_[xyz]$|posn|motor|stage|enc|encoder)',
    re.IGNORECASE)


def discover_motor_datasets(h5_path: str, max_results: int | None = None
                            ) -> list[tuple[str, int, float, float]]:
    """Walk an HDF5 file and return 1-D numeric datasets that look like motors.

    Returns list of (path, length, min, max). Hits motor-named datasets first,
    then falls back to any 1-D numeric dataset of length > 1. ``max_results``
    caps the returned list; ``None`` (default) returns every match.
    """
    if not h5_path or not os.path.isfile(h5_path) or h5py is None:
        return []
    hits: list[tuple[str, int, float, float]] = []
    fallback: list[tuple[str, int, float, float]] = []

    def _walk(group: 'h5py.Group', prefix: str) -> None:
        """Key-based recursive walk; visits every named path including link duplicates."""
        try:
            keys = list(group.keys())
        except Exception:
            return
        for key in keys:
            name = f'{prefix}/{key}'
            try:
                obj = group[key]
            except Exception:
                continue
            if isinstance(obj, h5py.Dataset):
                if obj.ndim != 1 or obj.shape[0] < 2:
                    continue
                if not np.issubdtype(obj.dtype, np.number):
                    continue
                try:
                    arr = np.asarray(obj[...])
                    rec = (name, int(arr.size),
                           float(np.nanmin(arr)), float(np.nanmax(arr)))
                except Exception:
                    continue
                if _MOTOR_HINT_RX.search(name):
                    hits.append(rec)
                else:
                    fallback.append(rec)
            elif isinstance(obj, h5py.Group):
                _walk(obj, name)

    try:
        with h5py.File(h5_path, 'r') as f:
            _walk(f, '')
    except Exception:
        return []
    return (hits + fallback)[:max_results]


def read_h5_dataset(h5_path: str, ds_path: str) -> np.ndarray | None:
    """Read a 1-D dataset by HDF5 path; return None on failure."""
    if not (h5_path and ds_path and os.path.isfile(h5_path) and h5py is not None):
        return None
    try:
        with h5py.File(h5_path, 'r') as f:
            obj = f[ds_path]
            if not isinstance(obj, h5py.Dataset) or obj.ndim != 1:
                return None
            return np.asarray(obj[...])
    except Exception:
        return None


# ── Zarr backend (parallel to the HDF5 helpers above) ────────────────────
try:
    import zarr  # type: ignore
    try:
        from zarr.storage import ZipStore as _ZarrZipStore  # zarr v3
    except ImportError:
        from zarr import ZipStore as _ZarrZipStore  # zarr v2 fallback
except ImportError:
    zarr = None
    _ZarrZipStore = None


_ZARR_DIR_EXT = '.zarr'
_ZARR_ZIP_EXT = '.zarr.zip'


def is_zarr(path: str) -> bool:
    """True if path looks like a zarr container (.zarr directory or .zarr.zip)."""
    if not path:
        return False
    p = path.lower().rstrip('/')
    return p.endswith(_ZARR_ZIP_EXT) or p.endswith(_ZARR_DIR_EXT)


def _open_zarr(path: str):
    """Open a zarr container read-only; return (root, close_fn) or (None, lambda: None).

    For .zarr.zip the caller must invoke close_fn to release the ZipStore handle;
    for .zarr directories close_fn is a no-op.
    """
    if zarr is None or not path:
        return None, (lambda: None)
    try:
        if path.lower().endswith(_ZARR_ZIP_EXT):
            if _ZarrZipStore is None or not os.path.isfile(path):
                return None, (lambda: None)
            store = _ZarrZipStore(path, mode='r')
            root = zarr.open(store, mode='r')
            return root, store.close
        if not os.path.isdir(path):
            return None, (lambda: None)
        root = zarr.open(path, mode='r')
        return root, (lambda: None)
    except Exception:
        return None, (lambda: None)


def _zarr_is_array(node) -> bool:
    """Duck-type: arrays expose shape + dtype; groups don't."""
    return hasattr(node, 'shape') and hasattr(node, 'dtype')


def _zarr_child_keys(group) -> list[str]:
    """Return immediate child keys for a zarr group (v2 or v3 compatible)."""
    try:
        keys = list(group.array_keys()) + list(group.group_keys())
    except AttributeError:
        try:
            keys = list(group.keys())
        except Exception:
            return []
    except Exception:
        return []
    # Stable order.
    return sorted(set(keys))


def discover_zarr_datasets(zarr_path: str, max_results: int | None = None
                           ) -> list[tuple[str, int, float, float]]:
    """Walk a zarr container; return 1-D numeric arrays as (path, len, min, max).

    Motor-named hits ranked first via the existing ``_MOTOR_HINT_RX``, mirroring
    ``discover_motor_datasets``.
    """
    if zarr is None or not zarr_path:
        return []
    root, close = _open_zarr(zarr_path)
    if root is None:
        close()
        return []
    hits: list[tuple[str, int, float, float]] = []
    fallback: list[tuple[str, int, float, float]] = []

    def _walk(group, prefix: str) -> None:
        for key in _zarr_child_keys(group):
            name = f'{prefix}/{key}'
            try:
                obj = group[key]
            except Exception:
                continue
            if _zarr_is_array(obj):
                try:
                    if obj.ndim != 1 or obj.shape[0] < 2:
                        continue
                    if not np.issubdtype(obj.dtype, np.number):
                        continue
                    arr = np.asarray(obj[...])
                    import warnings as _warnings
                    with _warnings.catch_warnings():
                        _warnings.simplefilter('ignore', RuntimeWarning)
                        rec = (name, int(arr.size),
                               float(np.nanmin(arr)), float(np.nanmax(arr)))
                except Exception:
                    continue
                if _MOTOR_HINT_RX.search(name):
                    hits.append(rec)
                else:
                    fallback.append(rec)
            else:
                _walk(obj, name)

    try:
        _walk(root, '')
    finally:
        close()
    return (hits + fallback)[:max_results]


def read_zarr_dataset(zarr_path: str, ds_path: str) -> np.ndarray | None:
    """Read a 1-D numeric array by zarr path; return None on failure."""
    if zarr is None or not (zarr_path and ds_path):
        return None
    root, close = _open_zarr(zarr_path)
    if root is None:
        close()
        return None
    try:
        key = ds_path.lstrip('/')
        obj = root[key]
        if not _zarr_is_array(obj) or obj.ndim != 1:
            return None
        return np.asarray(obj[...])
    except Exception:
        return None
    finally:
        close()


def discover_zarr_2d_attrs(zarr_path: str, max_results: int | None = None
                           ) -> list[tuple[str, int, int]]:
    """List 2-D numeric arrays as (path, n_rows, n_cols).

    Used to surface arrays like ``/IntegrationResult/FrameNr_*`` in the Color
    combo, where the last axis is reduced to a per-frame scalar at read time.
    """
    if zarr is None or not zarr_path:
        return []
    root, close = _open_zarr(zarr_path)
    if root is None:
        close()
        return []
    hits: list[tuple[str, int, int]] = []

    def _walk(group, prefix: str) -> None:
        for key in _zarr_child_keys(group):
            name = f'{prefix}/{key}'
            try:
                obj = group[key]
            except Exception:
                continue
            if _zarr_is_array(obj):
                try:
                    if obj.ndim != 2:
                        continue
                    if not np.issubdtype(obj.dtype, np.number):
                        continue
                    hits.append((name, int(obj.shape[0]), int(obj.shape[1])))
                except Exception:
                    continue
            else:
                _walk(obj, name)

    try:
        _walk(root, '')
    finally:
        close()
    return hits[:max_results]


_ZARR_2D_REDUCERS = {
    'mean': np.nanmean, 'sum': np.nansum,
    'max': np.nanmax, 'min': np.nanmin,
}


def read_zarr_2d_reduced(zarr_path: str, ds_path: str,
                         reducer: str = 'mean') -> np.ndarray | None:
    """Read a 2-D zarr array and collapse its last axis to a (n_rows,) scalar."""
    if zarr is None or not (zarr_path and ds_path):
        return None
    fn = _ZARR_2D_REDUCERS.get(reducer, np.nanmean)
    root, close = _open_zarr(zarr_path)
    if root is None:
        close()
        return None
    try:
        key = ds_path.lstrip('/')
        obj = root[key]
        if not _zarr_is_array(obj) or obj.ndim != 2:
            return None
        arr = np.asarray(obj[...], dtype=float)
        with np.errstate(invalid='ignore', divide='ignore'):
            return fn(arr, axis=-1)
    except Exception:
        return None
    finally:
        close()


# ── Extension-dispatched facade ──────────────────────────────────────────

def discover_datasets(path: str, max_results: int | None = None
                      ) -> list[tuple[str, int, float, float]]:
    """Discover 1-D numeric datasets, dispatching on container extension."""
    if is_zarr(path):
        return discover_zarr_datasets(path, max_results)
    return discover_motor_datasets(path, max_results)


def read_dataset_1d(path: str, ds_path: str) -> np.ndarray | None:
    """Read a 1-D numeric dataset, dispatching on container extension."""
    if is_zarr(path):
        return read_zarr_dataset(path, ds_path)
    return read_h5_dataset(path, ds_path)


def source_exists(path: str) -> bool:
    """File for HDF5/zip; directory for .zarr."""
    if not path:
        return False
    if is_zarr(path) and path.lower().endswith(_ZARR_DIR_EXT):
        return os.path.isdir(path)
    return os.path.isfile(path)


def discover_cake_siblings(path: str) -> list[tuple[str, str]]:
    """Given a cake_parameters CSV path, return [(label, sibling_path), …] for
    all per-panel siblings that exist on disk (including the seed itself).

    Detects a detector tag like 'ge1' / 'det2' / 'panel3' in the path and
    substitutes digits 1–4. If no tag is found or no siblings exist, returns
    just [(seed_label, path)]. The label is the matched tag (e.g. 'ge1') or
    the basename when no tag could be identified.
    """
    if not path or not os.path.isfile(path):
        return []
    # Try each <alpha+digit> candidate; pick the prefix with the most
    # existing siblings on disk (digits 1..4).
    candidates: set[tuple[str, str]] = set()
    for m in re.finditer(r'([A-Za-z]+)([1-4])(?![0-9])', path):
        prefix = m.group(1)
        digit = m.group(2)
        for n in range(1, min(len(prefix), 6) + 1):
            candidates.add((prefix[-n:], digit))

    def _sub(p: str, src: str, tgt: str) -> str:
        from pathlib import Path as _P
        parts = [re.sub(re.escape(src), tgt, part, flags=re.IGNORECASE)
                 for part in _P(p).parts]
        return str(_P(*parts))

    # Prefer longer prefixes when tied on hit count ('ge1' beats 'e1').
    best_hits: list[tuple[str, str]] = []
    best_prefix_len = 0
    for prefix, digit in sorted(candidates, key=lambda pd: -len(pd[0])):
        hits: list[tuple[str, str]] = []
        for d in '1234':
            src = prefix + digit
            tgt = prefix + d
            cand = path if d == digit else _sub(path, src, tgt)
            if cand and os.path.isfile(cand):
                hits.append((tgt, cand))
        if len(hits) > len(best_hits) or (
                len(hits) == len(best_hits) and len(prefix) > best_prefix_len):
            best_hits = hits
            best_prefix_len = len(prefix)

    if len(best_hits) >= 2:
        # Dedup by path while preserving order.
        seen = set()
        out = []
        for lbl, p in best_hits:
            if p not in seen:
                seen.add(p)
                out.append((lbl, p))
        return out
    return [(os.path.basename(path), path)]



def infer_frame_count(path: str):
    """Best-effort number of output frames in ``path``, or ``None``.

    Hoisted from two near-identical copies in the source
    (``PoleFigurePanel._infer_raw_frame_count`` 2306 and
    ``DataExplorerWidget._frames_per_primary_file`` 3670), and CORRECTED on
    the way: both read ``IntegrationResult/FrameNr_0.shape[0]`` as the frame
    count. In a MIDAS archive that array is ``(nR, nEta)``, so ``shape[0]``
    is the radial bin count -- typically 1000-3000 instead of a few tens.
    The Pole figure's "# raw frames" auto-fill would be wrong by orders of
    magnitude on exactly the files this app produces.

    Order, and why:

    1. ``/Omegas`` -- one entry per output frame, and part of the written
       schema's required set, so it is authoritative when present.
    2. The NUMBER OF KEYS under ``IntegrationResult`` -- one per frame.
       Not one key's row count. (Verified on a real archive where
       ``IntegrationResult`` is present but EMPTY, so this must fall
       through rather than return 0.)
    3. ``exchange/data``'s leading axis, for a raw HDF5 stack.
    """
    if not path or not source_exists(path):
        return None

    vals = read_dataset_1d(path, '/Omegas')
    if vals is not None and vals.size > 0:
        return int(vals.size)

    if is_zarr(path):
        root, close = _open_zarr(path)
        try:
            if root is not None:
                try:
                    group = root['IntegrationResult']
                except Exception:
                    group = None
                if group is not None and not _zarr_is_array(group):
                    n = len(_zarr_child_keys(group))
                    if n > 0:
                        return n
        finally:
            close()
        return None

    if h5py is None:
        return None
    try:
        with h5py.File(path, 'r') as f:
            for data_path in ('/exchange/data', 'exchange/data'):
                if data_path in f:
                    obj = f[data_path]
                    if isinstance(obj, h5py.Dataset) and obj.ndim >= 1:
                        return int(obj.shape[0])
    except Exception:
        return None
    return None
