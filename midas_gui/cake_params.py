"""Cake-parameters CSV — ``parse_cake_csv`` / ``CAKE_KEYS``.

Ported near-verbatim from ``mpe_wf_saxs_waxs/gui_data_explorer.py``. Reads
a small CSV (header row + one or more data rows — only the *last* data
row is used, so the file can be appended to as a running log) giving the
R/η caking range/bin size and the raw-sub-frame combine parameters:

``R_MIN, R_MAX, R_STEP, ETA_MIN, ETA_MAX, ETA_STEP, OME_SUM, OME_START, OME_STEP``

``OME_SUM`` is mpe_wf's name for what this app calls the "combine
sub-frames" chunk size (see ``helpers.read_hdf5_stack_combined`` /
``widgets.DataLoaderPanel``'s "Combine sub-frames" row) — the number of
consecutive raw sub-frames per HDF5 file to combine into one integrated
frame. ``OME_START``/``OME_STEP`` are omega-series bookkeeping for
mpe_wf's own (different) integration backend and have no equivalent in
``midas_integrate_v2``'s ``IntegrationSpec`` — this app reads them for
completeness but doesn't apply them anywhere.
"""
from __future__ import annotations

import csv
import os
from typing import Optional

CAKE_KEYS = ("R_MIN", "R_MAX", "R_STEP",
            "ETA_MIN", "ETA_MAX", "ETA_STEP",
            "OME_SUM", "OME_START", "OME_STEP")


def parse_cake_csv(path: str) -> Optional[dict]:
    """Read a cake_parameters CSV (header row + last data row) into a dict.
    Keys are upper-cased on read (case-insensitive header). Returns None
    if the file is missing, empty, or has no parseable numeric values."""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, newline="") as f:
            rows = [r for r in csv.reader(f) if r and any(c.strip() for c in r)]
        if len(rows) < 2:
            return None
        header = [h.strip() for h in rows[0]]
        last = [v.strip() for v in rows[-1]]
        d = {}
        for k, v in zip(header, last):
            try:
                d[k.upper()] = float(v)
            except ValueError:
                pass
        return d if d else None
    except Exception:
        return None
