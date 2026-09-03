"""Tests for the Calibrate tab's ring x azimuth strain map.

``ring_azimuth_residual`` is the numeric core: it recovers, per η row, how far
each calibrant ring actually sits from where the fitted geometry predicts it.
The subpixel refinement is the load-bearing part — see the function's own
docstring on why a plain argmax produces a flat, quantized map that *looks*
like a clean result but carries no azimuthal information.
"""
import numpy as np
import pytest

from midas_gui.widgets import (
    ring_azimuth_residual, collapsed_profile_ring_residual)

R_AXIS = np.arange(0.0, 120.0, 1.0)      # 1 px bins
RINGS = [30.0, 60.0, 90.0]
N_ETA = 36


def _cake_with_peaks(deltas, radii=RINGS, sigma=1.5, amp=100.0):
    """(n_eta, n_r) cake with a Gaussian ring peak per (η row, ring), offset
    from the predicted radius by ``deltas[eta_index, ring_index]``."""
    deltas = np.atleast_2d(deltas)
    cake = np.zeros((deltas.shape[0], R_AXIS.size))
    for i in range(deltas.shape[0]):
        for k, r_pred in enumerate(radii):
            centre = r_pred + deltas[i, k]
            cake[i] += amp * np.exp(-0.5 * ((R_AXIS - centre) / sigma) ** 2)
    return cake


def test_recovers_a_constant_offset_per_ring():
    """A uniform radial offset (e.g. a slightly wrong sample distance) must
    come back as the same Δr on every η row."""
    true = np.tile([0.4, -0.35, 0.25], (N_ETA, 1))
    grid, kept = ring_azimuth_residual(_cake_with_peaks(true), R_AXIS, RINGS)
    assert kept == RINGS
    assert grid.shape == (N_ETA, len(RINGS))
    np.testing.assert_allclose(grid, true, atol=0.05)


def test_recovers_azimuthal_structure():
    """The whole point of the feature: a sin(η) variation in ring radius —
    what a detector tilt or a strained sample produces — must survive as
    real per-row structure, not be flattened to one value."""
    eta = np.linspace(-180.0, 180.0, N_ETA, endpoint=False)
    true = np.stack([0.5 * np.sin(np.radians(eta)),
                     0.5 * np.sin(np.radians(eta)),
                     0.5 * np.sin(np.radians(eta))], axis=1)
    grid, _ = ring_azimuth_residual(_cake_with_peaks(true), R_AXIS, RINGS)
    np.testing.assert_allclose(grid, true, atol=0.06)
    # Genuinely resolved in azimuth, not a constant map.
    assert grid[:, 0].ptp() > 0.8


def test_subpixel_refinement_beats_bin_quantization():
    """Guard against a regression to plain argmax: sub-bin offsets must not
    all collapse onto the same R bin centre."""
    offsets = np.linspace(-0.45, 0.45, N_ETA)
    true = np.stack([offsets] * len(RINGS), axis=1)
    grid, _ = ring_azimuth_residual(_cake_with_peaks(true), R_AXIS, RINGS)
    # A raw argmax on 1 px bins would give at most ~2 distinct values here.
    assert len(np.unique(np.round(grid[:, 0], 3))) > N_ETA // 2
    np.testing.assert_allclose(grid[:, 0], offsets, atol=0.05)


def test_sign_convention_is_observed_minus_predicted():
    true = np.full((N_ETA, len(RINGS)), 0.6)
    grid, _ = ring_azimuth_residual(_cake_with_peaks(true), R_AXIS, RINGS)
    assert np.nanmean(grid) > 0, "peak outside the prediction must give Δr > 0"


def test_empty_rows_come_back_as_nan_not_zero():
    """A cake bin with no pixel coverage reads as exact 0. Those η rows must
    be NaN (unknown), never 0.0 (a perfectly-on-prediction ring)."""
    true = np.zeros((N_ETA, len(RINGS)))
    cake = _cake_with_peaks(true)
    cake[5:9, :] = 0.0                      # four azimuths off the detector
    grid, _ = ring_azimuth_residual(cake, R_AXIS, RINGS)
    assert np.isnan(grid[5:9, :]).all()
    assert np.isfinite(np.delete(grid, np.s_[5:9], axis=0)).all()


def test_rings_with_no_in_window_samples_are_dropped():
    """Mirrors ResidualBarChart's ring-dropping: a ring off the R axis is
    removed from both the grid and kept_radii, keeping them aligned."""
    cake = _cake_with_peaks(np.zeros((N_ETA, len(RINGS))))
    grid, kept = ring_azimuth_residual(cake, R_AXIS, RINGS + [500.0])
    assert kept == RINGS
    assert grid.shape[1] == len(kept)


def test_no_matching_rings_returns_an_empty_grid():
    cake = np.zeros((N_ETA, R_AXIS.size))
    grid, kept = ring_azimuth_residual(cake, R_AXIS, [500.0, 900.0])
    assert kept == []
    assert grid.shape == (N_ETA, 0)


def test_narrow_window_falls_back_to_plain_argmax():
    """With fewer than 3 in-window bins there is no parabola to fit; the
    function must degrade to the bin centre instead of raising."""
    true = np.full((N_ETA, len(RINGS)), 0.3)
    grid, kept = ring_azimuth_residual(_cake_with_peaks(true), R_AXIS, RINGS,
                                       window_px=1.0)
    assert kept == RINGS
    assert np.isfinite(grid).all()
    np.testing.assert_allclose(grid, 0.0, atol=1.0)   # quantized to whole bins


def test_offsets_are_clipped_to_one_bin():
    """A flat/noisy local maximum must not let the parabolic fit throw the
    refined position far outside its own window."""
    rng = np.random.default_rng(0)
    cake = rng.random((N_ETA, R_AXIS.size)) * 1e-6   # no real peak anywhere
    grid, _ = ring_azimuth_residual(cake, R_AXIS, RINGS, window_px=8.0)
    finite = grid[np.isfinite(grid)]
    assert np.all(np.abs(finite) <= 9.0)


# ── collapsed (1-D) counterpart ──────────────────────────────────────────────

def test_collapsed_profile_residual_matches_the_cake_mean():
    """The 1-D "Peak of collapsed profile" method must agree with the
    η-resolved one when there is no azimuthal variation."""
    true = np.full((N_ETA, len(RINGS)), 0.0)
    cake = _cake_with_peaks(true)
    resid, kept = collapsed_profile_ring_residual(R_AXIS, cake.mean(axis=0), RINGS)
    assert kept == RINGS
    np.testing.assert_allclose(resid, 0.0, atol=1.0)


def test_collapsed_profile_residual_drops_unmatched_rings():
    prof = _cake_with_peaks(np.zeros((1, len(RINGS))))[0]
    resid, kept = collapsed_profile_ring_residual(R_AXIS, prof, RINGS + [500.0])
    assert kept == RINGS
    assert len(resid) == len(kept)


@pytest.mark.parametrize("r_axis, rings", [
    (np.array([]), RINGS),
    (R_AXIS, []),
])
def test_collapsed_profile_residual_handles_empty_input(r_axis, rings):
    resid, kept = collapsed_profile_ring_residual(r_axis, np.zeros(r_axis.size),
                                                  rings)
    assert len(resid) == 0 and kept == []
