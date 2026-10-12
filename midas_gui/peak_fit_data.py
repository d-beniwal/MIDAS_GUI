"""GSAS-II sequential peak-fit CSV: parsing and the per-frame reducers.

The Qt-free data layer behind ``peak_fit_panel.PeakFitPanel`` and the Data
Explorer tab. Split out so both can use it without either importing the
other — and so it can be unit-tested with no ``QApplication``, which is why
``tests/test_peak_fit_data.py`` is a plain pytest file rather than a forked
Qt one.

``parse_peak_fit_csv`` and its regexes were MOVED here from
``peak_fit_panel.py`` (where they arrived in that panel's own port) rather
than ported a second time from
``mpe_wf_saxs_waxs/gui_data_explorer.py``. The two copies were verbatim
identical; one of them had to stop existing. ``peak_fit_panel`` re-exports
the name so existing imports keep working.

The five reducers below are new to this project, ported from the same
source (lines 577-793). They are what turns a CSV of per-(frame, eta) fit
rows into something plottable: a value per frame, per eta, per file, or a
full omega x eta grid.

Nothing here imports Qt or matplotlib. Keep it that way.
"""
from __future__ import annotations

import csv
import os
import re

import numpy as np


_PEAK_FIT_NAME_RX = re.compile(
    r'img=(?P<img>\d+)\s+omega=(?P<omega>[-+]?\d+\.?\d*)\s+eta=(?P<eta>[-+]?\d+\.?\d*)'
)

# Pulls the sibling-file number out of the leading file basename in the CSV
# "name" column, e.g. ``C611_017Fe_1_load9_010128.vrx.zarr.zip img=0 omega=…``
# → 10128. Mirrors DataExplorerWidget._SIB_RX so peak-fit rows can be matched
# to spanned files even when some files have no fits (skipped frames).
_PEAK_FIT_FILE_RX = re.compile(
    r'^(?P<base>.+?)_(?P<num>\d{4,7})(?:\.[^.]+)?'
    r'(?:\.h5|\.zarr\.zip|\.zarr)\b'
)

_PEAK_FIT_PARAM_KEYS = (
    'int', 'pos', 'sig', 'gam',
    'esd-int', 'esd-pos', 'esd-sig', 'esd-gam',
)
_PEAK_FIT_PARAM_LABELS = {
    'int': 'Intensity',
    'pos': 'Position (2θ °)',
    'sig': 'Width σ',
    'gam': 'Width γ',
    'esd-int': 'Intensity ESD',
    'esd-pos': 'Position ESD',
    'esd-sig': 'Width σ ESD',
    'esd-gam': 'Width γ ESD',
}
_PEAK_FIT_EXTRA_COLS = (('Rwp', 'Rwp'), ('GOF', 'GOF'))


def parse_peak_fit_csv(path: str) -> dict | None:
    """Parse a GSAS-2 sequential peak-fit CSV into numpy arrays.

    Returns a dict with the common per-row arrays plus only the
    ``<param><peak>`` arrays whose columns actually exist in the header.
    ``peak_indices`` lists the integer peak indices detected from ``pos<N>``
    columns; ``params`` flags which of int/pos/sig/gam appear.
    Returns None when the file is missing or unparseable.
    """
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, newline='') as fh:
            reader = csv.DictReader(fh)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)
    except Exception:
        return None
    if not rows or not fieldnames:
        return None

    # Detect peak indices from pos<N> columns (fallback to int<N> if pos missing).
    pk_rx = re.compile(r'^pos(\d+)$')
    peak_indices = sorted({int(m.group(1))
                           for h in fieldnames
                           for m in [pk_rx.match(h.strip())] if m})
    if not peak_indices:
        pk_rx_int = re.compile(r'^int(\d+)$')
        peak_indices = sorted({int(m.group(1))
                               for h in fieldnames
                               for m in [pk_rx_int.match(h.strip())] if m})
    if not peak_indices:
        peak_indices = [0]  # degenerate fallback

    header_set = {h.strip() for h in fieldnames}
    params = {k: any(f'{k}{i}' in header_set for i in peak_indices)
              for k in _PEAK_FIT_PARAM_KEYS}

    n = len(rows)
    omega = np.full(n, np.nan)
    eta = np.full(n, np.nan)
    img_idx = np.full(n, -1, dtype=int)
    file_num = np.full(n, -1, dtype=int)
    use = np.ones(n, dtype=bool)
    rwp = np.full(n, np.nan)
    gof = np.full(n, np.nan)
    arrays: dict[str, np.ndarray] = {
        f'{p}{i}': np.full(n, np.nan)
        for p in _PEAK_FIT_PARAM_KEYS if params[p]
        for i in peak_indices
    }

    for k, row in enumerate(rows):
        nm = row.get('name', '')
        m = _PEAK_FIT_NAME_RX.search(nm)
        if m:
            try:
                omega[k] = float(m.group('omega'))
                eta[k] = float(m.group('eta'))
                img_idx[k] = int(m.group('img'))
            except ValueError:
                pass
        fm = _PEAK_FIT_FILE_RX.match(nm)
        if fm:
            try:
                file_num[k] = int(fm.group('num'))
            except ValueError:
                pass
        try:
            use[k] = row.get('Use', 'True').strip().lower() not in ('false', '0', '')
        except (AttributeError, ValueError):
            pass
        try:
            rwp[k] = float(row.get('Rwp', 'nan') or 'nan')
        except ValueError:
            pass
        try:
            gof[k] = float(row.get('GOF', 'nan') or 'nan')
        except ValueError:
            pass
        for key in arrays:
            try:
                arrays[key][k] = float(row.get(key, 'nan') or 'nan')
            except ValueError:
                pass

    return {
        'rows': n,
        'omega': omega, 'eta': eta, 'img_idx': img_idx,
        'file_num': file_num,
        'use': use, 'Rwp': rwp, 'GOF': gof,
        'peak_indices': peak_indices,
        'params': params,
        **arrays,
    }


def peak_fit_per_frame(cache: dict, peak: int, param: str,
                       agg: str, n: int) -> np.ndarray | None:
    """Collapse a peak-fit CSV cache to one scalar per HDF5 frame index.

    Groups CSV rows by ``img_idx`` and applies ``agg`` (mean/std/min/max)
    across all η rows for that frame. Returns a length-``n`` float array
    (NaN where the frame has no fit row). Returns None when the requested
    column isn't in the cache.
    """
    key = param if param in ('Rwp', 'GOF') else f'{param}{peak}'
    vals = cache.get(key)
    if vals is None:
        return None
    img = cache.get('img_idx')
    use = cache.get('use')
    if img is None:
        return None
    out = np.full(n, np.nan)
    valid = (img >= 0) & (img < n) & use & ~np.isnan(vals)
    if not np.any(valid):
        return out
    img_v = img[valid]
    val_v = vals[valid]
    fn = {'mean': np.nanmean, 'std': np.nanstd,
          'min': np.nanmin, 'max': np.nanmax}.get(agg, np.nanmean)
    order = np.argsort(img_v)
    img_s = img_v[order]
    val_s = val_v[order]
    # Group by frame index.
    boundaries = np.concatenate(([0], np.flatnonzero(np.diff(img_s)) + 1, [len(img_s)]))
    for b0, b1 in zip(boundaries[:-1], boundaries[1:]):
        i = int(img_s[b0])
        if 0 <= i < n:
            out[i] = fn(val_s[b0:b1])
    return out


def peak_fit_eta_choices(cache: dict, *, bin_deg: float = 0.5) -> list[float]:
    """Return the sorted unique η centres present in a peak-fit CSV cache.

    Values are snapped to a ``bin_deg``-degree grid so floating-point noise
    in the recorded η doesn't fragment one logical bin into many combo
    entries. Returns ``[]`` when the cache is missing the η column.
    """
    if cache is None:
        return []
    et = cache.get('eta')
    if et is None:
        return []
    et = np.asarray(et, dtype=float).ravel()
    et = et[~np.isnan(et)]
    if et.size == 0:
        return []
    bin_deg = max(float(bin_deg), 1e-6)
    snapped = np.round(et / bin_deg) * bin_deg
    return sorted(set(round(float(v), 4) for v in snapped))


def peak_fit_per_frame_at_eta(cache: dict, peak: int, param: str,
                              eta_target_deg: float, n: int,
                              atol_deg: float = 0.5) -> np.ndarray | None:
    """One scalar per HDF5 frame, restricted to rows whose η is within
    ``atol_deg`` of ``eta_target_deg``. Same NaN/missing-column rules as
    :func:`peak_fit_per_frame`. If multiple rows for a frame fall inside
    the η window, their values are averaged.
    """
    key = param if param in ('Rwp', 'GOF') else f'{param}{peak}'
    vals = cache.get(key)
    img = cache.get('img_idx')
    use = cache.get('use')
    et = cache.get('eta')
    if vals is None or img is None or et is None:
        return None
    out = np.full(n, np.nan)
    valid = ((img >= 0) & (img < n) & use & ~np.isnan(vals)
             & ~np.isnan(et) & (np.abs(et - eta_target_deg) <= atol_deg))
    if not np.any(valid):
        return out
    img_v = img[valid]; val_v = vals[valid]
    order = np.argsort(img_v)
    img_s = img_v[order]; val_s = val_v[order]
    boundaries = np.concatenate(([0],
                                 np.flatnonzero(np.diff(img_s)) + 1,
                                 [len(img_s)]))
    for b0, b1 in zip(boundaries[:-1], boundaries[1:]):
        i = int(img_s[b0])
        if 0 <= i < n:
            out[i] = float(np.nanmean(val_s[b0:b1]))
    return out


def peak_fit_per_file_at_eta(cache: dict, peak: int, param: str,
                             eta_target_deg: float,
                             file_nums: 'list[int] | np.ndarray',
                             agg: str = 'mean',
                             atol_deg: float = 0.5) -> np.ndarray | None:
    """One scalar per spanned sibling file, matched by the ``file_num``
    extracted from the CSV's ``name`` column.

    Returns an array aligned 1:1 with ``file_nums``: NaN for files with
    no surviving fit rows (e.g. lineouts where peak fitting failed).
    ``agg`` collapses every row matching (file, η-window) into one scalar.
    """
    key = param if param in ('Rwp', 'GOF') else f'{param}{peak}'
    vals = cache.get(key)
    fn_arr = cache.get('file_num')
    use = cache.get('use')
    et = cache.get('eta')
    if vals is None or fn_arr is None or et is None:
        return None
    fns = np.asarray(list(file_nums), dtype=int)
    out = np.full(fns.size, np.nan)
    valid_mask = (use & ~np.isnan(vals) & ~np.isnan(et)
                  & (np.abs(et - eta_target_deg) <= atol_deg)
                  & (fn_arr >= 0))
    if not np.any(valid_mask):
        return out
    fn_v = fn_arr[valid_mask]
    val_v = vals[valid_mask]
    reducer = {'mean': np.nanmean, 'std': np.nanstd,
               'min': np.nanmin, 'max': np.nanmax}.get(agg, np.nanmean)
    # Bucket by file_num once, then look up each spanned file.
    order = np.argsort(fn_v)
    fn_s = fn_v[order]; val_s = val_v[order]
    boundaries = np.concatenate(([0],
                                 np.flatnonzero(np.diff(fn_s)) + 1,
                                 [len(fn_s)]))
    bucket: dict = {}
    for b0, b1 in zip(boundaries[:-1], boundaries[1:]):
        bucket[int(fn_s[b0])] = float(reducer(val_s[b0:b1]))
    for i, fn_i in enumerate(fns):
        if fn_i in bucket:
            out[i] = bucket[fn_i]
    return out


def peak_fit_omega_eta_grid(cache: dict, peak: int, param: str,
                            omega_centers_deg: np.ndarray,
                            eta_centers_deg: np.ndarray,
                            atol_deg: float = 0.0) -> np.ndarray | None:
    """Build an (n_omega, n_eta) grid sampling a peak-fit param at each cell.

    For each (ω_c, η_c) bin center, finds the CSV row with the nearest
    (omega, eta) within ``atol_deg`` (defaults to half the cell pitch);
    cells without a match are NaN. Returns None when the column is missing.
    """
    key = param if param in ('Rwp', 'GOF') else f'{param}{peak}'
    vals = cache.get(key)
    omg = cache.get('omega')
    et = cache.get('eta')
    use = cache.get('use')
    if vals is None or omg is None or et is None:
        return None
    valid = (~np.isnan(omg)) & (~np.isnan(et)) & (~np.isnan(vals)) & use
    if not np.any(valid):
        return np.full((omega_centers_deg.size, eta_centers_deg.size), np.nan)
    omg_v = omg[valid]
    et_v = et[valid]
    val_v = vals[valid]

    def _step(centers: np.ndarray) -> float:
        if centers.size < 2:
            return 1.0
        return float(np.median(np.diff(np.sort(centers))))

    # Peak-fit tools (e.g. sequential GSAS-2 fits) often write a placeholder
    # omega like 999.0 for every row when they don't track per-row ω. In
    # that case snap-matching against the requested ω centers rejects
    # everything and the sphere comes back empty. Detect the placeholder
    # (single unique ω value with no meaningful spread) and broadcast
    # each row across every ω center instead.
    uniq_om = np.unique(np.round(omg_v, 3))
    broadcast_omega = (uniq_om.size == 1)

    tol_om = max(atol_deg, 0.5 * _step(omega_centers_deg))
    tol_et = max(atol_deg, 0.5 * _step(eta_centers_deg))

    # Bucket CSV rows by their rounded grid cell for O(1) lookup.
    e0 = float(eta_centers_deg[0])
    de = _step(eta_centers_deg) or 1.0
    ei = np.round((et_v - e0) / de).astype(int)
    n_o = omega_centers_deg.size
    n_e = eta_centers_deg.size
    grid = np.full((n_o, n_e), np.nan)
    counts = np.zeros((n_o, n_e), dtype=int)
    et_ok = np.abs((et_v - (e0 + ei * de))) <= tol_et
    if broadcast_omega:
        # Build a 1-D (η) array first, then broadcast across every ω row.
        in_range = (ei >= 0) & (ei < n_e) & et_ok
        row = np.full(n_e, np.nan)
        row_counts = np.zeros(n_e, dtype=int)
        for k in np.flatnonzero(in_range):
            j = int(ei[k])
            if row_counts[j] == 0:
                row[j] = val_v[k]
            else:
                row[j] = (row[j] * row_counts[j] + val_v[k]) / (row_counts[j] + 1)
            row_counts[j] += 1
        grid[:] = row[None, :]
    else:
        o0 = float(omega_centers_deg[0])
        do = _step(omega_centers_deg) or 1.0
        oi = np.round((omg_v - o0) / do).astype(int)
        om_ok = np.abs((omg_v - (o0 + oi * do))) <= tol_om
        in_range = ((oi >= 0) & (oi < n_o) & (ei >= 0) & (ei < n_e)
                    & om_ok & et_ok)
        for k in np.flatnonzero(in_range):
            i = int(oi[k]); j = int(ei[k])
            if counts[i, j] == 0:
                grid[i, j] = val_v[k]
            else:
                # Average duplicates landing in the same cell.
                grid[i, j] = (grid[i, j] * counts[i, j] + val_v[k]) / (counts[i, j] + 1)
            counts[i, j] += 1
    return grid


