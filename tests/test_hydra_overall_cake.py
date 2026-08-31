"""Unit tests for the Hydra Calibrate tab's Overall (η, R) cake compositing
(``hydra_calib_page._resample_rows_to_eta_grid``/``_compose_overall_cake``)
— pure array math, no Qt widgets, so unlike tests/test_hydra_calib_ui.py this
doesn't need pytest-forked isolation.

See .context/DECISIONS.md for the physics: a panel's own per-pixel η is
computed by the backend with that panel's real (fixed, even if unrefined)
``tx`` already baked in (``helpers._build_spec`` always sets ``spec.tx =
result.tx``, and calibration never refines tx at all — see
``calib._refine_dict``). So a panel calibrated with its true, distinct
installation ``tx`` already lands in the shared/world η frame — the same
one ``hydra.compute_inv_coords`` uses for the Data Viewer's composite image
— and ``_compose_overall_cake`` must NOT rotate it again. Only when every
panel is left at the 0.0 tx default (no placement information ever given)
do all 4 panels' cakes genuinely land on the same local wedge; that's
expected, not a bug ``_compose_overall_cake`` can fix after the fact.
"""
from __future__ import annotations

import numpy as np
import pytest

from midas_gui.hydra_calib_page import _compose_overall_cake, _resample_rows_to_eta_grid


def _eta_axis(n_eta: int) -> np.ndarray:
    bin_size = 360.0 / n_eta
    return -180.0 + bin_size * (np.arange(n_eta) + 0.5)


# ── _resample_rows_to_eta_grid ───────────────────────────────────────────

def test_resample_rows_identity_when_axes_already_match():
    eta = _eta_axis(8)
    cake = np.arange(8 * 3, dtype=np.float64).reshape(8, 3)
    out = _resample_rows_to_eta_grid(cake, eta, eta)
    np.testing.assert_allclose(out, cake, atol=1e-6)


def test_resample_rows_handles_wraparound_seam():
    # Two source rows straddling the +/-180 seam; a query exactly AT the
    # seam is equidistant (1 deg either way, via wrap-around) from both, so
    # periodic interpolation should average them -- a naive, non-periodic
    # np.interp would instead see them as ~358 deg apart and extrapolate
    # from just the nearer (larger-eta) one, or clip to an edge value.
    src_eta = np.array([-179.0, 179.0])
    cake = np.array([[0.0], [10.0]])
    dst_eta = np.array([-180.0])
    out = _resample_rows_to_eta_grid(cake, src_eta, dst_eta)
    assert out[0, 0] == pytest.approx(5.0, abs=1e-4)


# ── _compose_overall_cake ────────────────────────────────────────────────

def _make_panel(n_eta: int, n_r: int, *, hot_row: int,
                lsd=1_000_000.0, px=200.0, wl=0.2):
    """A synthetic panel cake as ``IntegrationWorker`` would actually hand
    back: ``hot_row`` is wherever the backend already placed this panel's
    signal in the shared η frame (real tx already baked in, per the module
    docstring) -- ``_compose_overall_cake`` should use it as-is, with no
    further rotation."""
    eta = _eta_axis(n_eta)
    r_px = np.linspace(50.0, 500.0, n_r)
    cake = np.zeros((n_eta, n_r), dtype=np.float64)
    cake[hot_row, :] = 10.0
    return cake, r_px, eta, lsd, px, wl


def test_compose_overall_cake_none_when_empty():
    assert _compose_overall_cake({}) is None


def test_compose_overall_cake_unseeded_tx_piles_up_on_one_row():
    """Every panel left at the 0.0 tx default (never told its real
    installation angle) genuinely has no placement information -- all 4
    panels' backend-computed cakes land on the same local row, and Overall
    correctly piles them there. Not a bug: nothing in the compose step can
    invent geometry that was never given to the fit."""
    n_eta = 8
    panels = {n: _make_panel(n_eta, 4, hot_row=0) for n in (1, 2, 3, 4)}
    cake, r_axis, eta_axis = _compose_overall_cake(panels)
    populated_rows = np.where(np.nansum(np.nan_to_num(cake), axis=1) > 0)[0]
    assert list(populated_rows) == [0]
    # 4 panels x 10.0/bin x 4 R-bins, all stacked onto the one shared row.
    assert cake[0, :].sum() == pytest.approx(160.0)


def test_compose_overall_cake_distinct_placements_span_full_circle():
    """4 panels correctly calibrated with their real, distinct installation
    tx (~90 deg apart on a real Hydra windmill) already have their signal
    on 4 different rows by the time IntegrationWorker hands them back --
    composing must sum them as-is, not shift them again."""
    n_eta = 8
    panels = {
        1: _make_panel(n_eta, 4, hot_row=0),
        2: _make_panel(n_eta, 4, hot_row=2),
        3: _make_panel(n_eta, 4, hot_row=4),
        4: _make_panel(n_eta, 4, hot_row=6),
    }
    cake, r_axis, eta_axis = _compose_overall_cake(panels)
    row_totals = np.nansum(np.nan_to_num(cake), axis=1)
    populated_rows = np.where(row_totals > 1.0)[0]
    assert list(populated_rows) == [0, 2, 4, 6]
    # Each row carries exactly one panel's worth (10.0/bin x 4 R-bins = 40)
    # -- not four panels' worth (160, the unseeded-tx pile-up) on one row.
    assert row_totals.max() == pytest.approx(40.0, rel=1e-3)
    total_signal = row_totals.sum()
    assert total_signal == pytest.approx(160.0, rel=1e-3)  # energy conserved, just distributed


def test_compose_overall_cake_sums_overlapping_coverage():
    """Two panels whose real coverage genuinely overlaps (e.g. both really
    do have signal at the same world eta) should still add, not overwrite."""
    n_eta = 8
    panels = {
        1: _make_panel(n_eta, 4, hot_row=2),
        2: _make_panel(n_eta, 4, hot_row=2),
    }
    cake, r_axis, eta_axis = _compose_overall_cake(panels)
    # 2 panels x 10.0/bin x 4 R-bins, both landing on the same real row.
    assert cake[2, :].sum() == pytest.approx(80.0)
