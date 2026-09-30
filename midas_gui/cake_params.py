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
frame. ``OME_START``/``OME_STEP`` describe the rotation the frames were
collected over: the angle of raw sub-frame 0, and the increment per raw
sub-frame. At the beamline mpe_wf reads them straight off two PVs
(``20idaSoft:userTran9.H``/``.I``, see
``mpe_wf_saxs_waxs/workflow_saxs_waxs/run_midas_for_cakes.sh``).

They have no equivalent in ``midas_integrate_v2``'s ``IntegrationSpec``,
which knows nothing about rotation — but the zarr writer takes a per-frame
``omegas`` sequence, so this module turns the two into angles via
``omega_for_window``/``omega_series`` below and Batch Integrate hands the
result to the writer. See those functions for the arithmetic.
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


def omega_for_window(start: float, step: float,
                     raw_lo: int, raw_hi: int) -> float:
    """Rotation angle of one integrated frame, in degrees.

    ``raw_lo``/``raw_hi`` are the INCLUSIVE 0-based raw sub-frame indices
    that frame was built from, counted from the start of the rotation the
    frame belongs to — for an HDF5 sub-frame stack that is the file the
    frame came from, each file being one rotation; for one-frame-per-file
    data it is the whole selection (see
    ``workers._HDF5StackGlobSource.omega_channel_window`` and
    ``workers._ChunkCombinedFileSource.raw_window_for_index``). The angle is
    the one at the middle of that window::

        omega = OME_START + mean(raw_lo … raw_hi) * OME_STEP

    written as ``start + 0.5 * (lo + hi) * step`` so an inclusive integer
    range never has to be materialised.

    That single expression is all three cases mpe_wf spells out separately.
    With no combining (``OME_SUM = 1``) the window is ``(k, k)`` and this is
    ``start + k*step``; with ``OME_SUM = n`` it is ``(k*n, k*n+n-1)`` and
    this reproduces ``gui_data_explorer.py``'s
    ``ome_start + (idx*ome_sum + (ome_sum-1)/2)*ome_step`` exactly; with
    ``OME_SUM = 0`` (this app's "combine everything selected into one
    frame") the window is the whole run and this is the mean angle the
    collapsed exposure actually covered.

    ``start`` and ``step`` both zero returns a genuine ``0.0``, not a
    sentinel — a stationary sample really is at ω = 0, and that is a much
    better answer for an unconfigured run than the frame index this used to
    write into the zarr's ``/Omegas``.
    """
    return float(start) + 0.5 * (int(raw_lo) + int(raw_hi)) * float(step)


def omega_series(start: float, step: float, windows, *,
                 collapse: bool = False) -> list:
    """``omega_for_window`` over a list of ``(raw_lo, raw_hi)`` windows.

    ``collapse=True`` is the "these images were averaged or summed" override:
    every frame gets ONE angle, computed from the union window
    ``(min lo, max hi)``. It exists for data combined somewhere other than
    this app's loader — pre-averaged TIFFs, or frames that are already sums
    while ``OME_SUM`` still reads 1 — where the per-frame windows describe
    a ramp the pixels no longer have. When the combining happened *here*
    (``OME_SUM = 0``) the single window already spans the run and the
    override is a no-op.
    """
    windows = [(int(lo), int(hi)) for lo, hi in windows]
    if not windows:
        return []
    if collapse:
        one = omega_for_window(start, step,
                               min(lo for lo, _ in windows),
                               max(hi for _, hi in windows))
        return [one] * len(windows)
    return [omega_for_window(start, step, lo, hi) for lo, hi in windows]
