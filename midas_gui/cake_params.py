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


def write_cake_csv(path: str, values: dict) -> None:
    """Write a cake_parameters CSV: header row + one data row, all nine
    ``CAKE_KEYS`` in order.

    Deliberately the same shape mpe_wf's own editor produces (a
    ``csv.DictWriter`` over its ``PARAMS`` keys, ``writeheader()`` then one
    row, opened ``"w"``), so a file written here is interchangeable with one
    written there — this app can only claim to speak the format if the round
    trip goes both ways.

    Every key is written, defaulting to ``0``: mpe_wf's editor refuses an
    empty field on save and its reader would turn a blank cell into a
    ``ValueError``, so an omitted key has to become a number rather than an
    empty string. Values go through ``%g``, which keeps ``1.0`` as ``1`` and
    ``-180.0`` as ``-180``, matching the hand-written files in circulation
    rather than decorating them with trailing zeros.

    Overwrites: ``parse_cake_csv`` reads the *last* data row precisely so a
    file can be appended to as a running log, but the mpe_wf editor rewrites
    rather than appends, and a config the user just saved should read back as
    what they saved.
    """
    row = {}
    for key in CAKE_KEYS:
        v = values.get(key, 0)
        try:
            row[key] = "%g" % float(v)
        except (TypeError, ValueError):
            row[key] = "0"
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(CAKE_KEYS))
        writer.writeheader()
        writer.writerow(row)
