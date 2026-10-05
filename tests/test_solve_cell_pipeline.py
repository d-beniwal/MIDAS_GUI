"""Tests for midas_gui.solve_cell.pipeline (Phase 1: single panel, known
geometry). No Qt dependency -- pipeline.py is pure Python.

Several tests regress against real, already-computed reference data from the
validated spinel_DAC_solve_cell analysis (raw HDF5 frames for that analysis
live only on the beamline cluster and aren't available on this machine, but
its per-panel spots_g*.csv outputs are present locally) -- see
documentation/solve_cell_handoff.md and .context/STATE.md for provenance.
Skipped automatically if that data isn't present on the running machine.
"""
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

midas_defect = pytest.importorskip("midas_defect")
midas_hkls = pytest.importorskip("midas_hkls")

from midas_gui.solve_cell import pipeline as P

REF_ROOT = Path("/Users/dbeniwal/ANL-research/S3ID_data/2026-2/analysis/spinel_DAC_solve_cell")
PANEL1_DIR = REF_ROOT / "panel_01_-195_10_-40" / "data"
PANEL2_DIR = REF_ROOT / "panel_02_-70_10_-40" / "data"
WAVELENGTH_A = 0.486213
LITERATURE_A_SPINEL = 8.08

_have_ref_data = PANEL1_DIR.exists() and (PANEL1_DIR / "spots_g.csv").exists()
requires_ref_data = pytest.mark.skipif(not _have_ref_data, reason="reference spinel analysis data not present on this machine")


# ═════════════════════════════════════════════════════════════════════════════
#  g-vector convention boundary
# ═════════════════════════════════════════════════════════════════════════════

def test_to_inverse_d_divides_by_two_pi():
    g_2pi = np.array([[1.0, 2.0, 3.0], [0.0, -1.0, 4.0]])
    g_1d, sigma_1d = P._to_inverse_d(g_2pi, 5e-3)
    assert np.allclose(g_1d, g_2pi / P.TWO_PI)
    assert sigma_1d == pytest.approx(5e-3 / P.TWO_PI)


def test_index_ab_initio_ub_is_always_one_over_d_convention():
    """Confirms the handoff's claim (and the API survey's finding) directly:
    index_ab_initio's UB/cell come back in 1/d regardless of two_pi."""
    from midas_hkls.ab_initio import index_ab_initio
    from midas_hkls import Lattice

    rng = np.random.default_rng(0)
    cell = (5.0, 5.0, 5.0, 90.0, 90.0, 90.0)
    B = np.asarray(Lattice(*cell).reciprocal_cartesian_vectors())
    hkl = rng.integers(-4, 5, size=(80, 3))
    hkl = hkl[np.any(hkl != 0, axis=1)]
    g_1d = (B @ hkl.T).T

    res_1d = index_ab_initio(g_1d, two_pi=False, sigma_g=1e-3, min_reflections=20)
    res_2pi = index_ab_initio(g_1d * P.TWO_PI, two_pi=True, sigma_g=1e-3 * P.TWO_PI, min_reflections=20)
    assert res_1d.success and res_2pi.success
    # Same physical lattice fed in two conventions -> same recovered cell length scale.
    assert res_1d.cell[0] == pytest.approx(res_2pi.cell[0], rel=0.05)
    assert res_1d.cell[0] == pytest.approx(5.0, rel=0.1)


# ═════════════════════════════════════════════════════════════════════════════
#  Diamond selection rule / filter
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("hkl,allowed", [
    ((1, 1, 1), True), ((2, 0, 0), False), ((2, 2, 0), True),
    ((3, 1, 1), True), ((4, 0, 0), True), ((2, 2, 2), False),
])
def test_diamond_selection_rule_matches_textbook_lines(hkl, allowed):
    h, k, l = hkl
    a = P.DIAMOND_A_ANGSTROM_DEFAULT
    d = a / math.sqrt(h * h + k * k + l * l)
    wavelength = 0.5
    tth = math.degrees(2.0 * math.asin(wavelength / (2.0 * d)))
    lines = P._diamond_tth_lines_deg(wavelength, a)
    nearest = np.min(np.abs(lines - tth)) if len(lines) else np.inf
    is_present = nearest < 1e-4
    assert is_present == allowed


def test_diamond_filter_flags_known_line_and_passes_others():
    a = P.DIAMOND_A_ANGSTROM_DEFAULT
    wavelength = 0.486213
    lines = P._diamond_tth_lines_deg(wavelength, a)
    on_line = lines[0]
    off_line = on_line + 5.0  # far from any diamond line
    spots = pd.DataFrame({"two_theta_deg": [on_line, off_line]})
    out = P._stage_diamond_filter({"spots_df": spots, "wavelength_A": wavelength, "n_mc": 1000})
    assert out["summary"]["n_diamond_flagged"] == 1
    assert list(out["flagged_df"]["is_diamond"]) == [True, False]
    assert len(out["candidate_df"]) == 1


@requires_ref_data
def test_diamond_filter_against_real_panel1_data():
    spots = pd.read_csv(PANEL1_DIR / "spots_g.csv")
    reference_flagged = pd.read_csv(PANEL1_DIR / "spots_g_diamond_flagged.csv")

    out = P._stage_diamond_filter({
        "spots_df": spots, "wavelength_A": WAVELENGTH_A, "contam_tol_deg": 0.07, "n_mc": 2000,
    })
    n_diamond_ours = out["summary"]["n_diamond_flagged"]
    n_diamond_ref = int(reference_flagged["is_diamond"].sum())
    # Our line table is brute-force-computed, not the reference's precomputed
    # CSV -- expect close agreement, not necessarily byte-identical.
    assert abs(n_diamond_ours - n_diamond_ref) <= max(2, int(0.2 * max(n_diamond_ref, 1)))


# ═════════════════════════════════════════════════════════════════════════════
#  Ab-initio + refine against real reference data
# ═════════════════════════════════════════════════════════════════════════════

def _load_real_candidate(*panel_dirs):
    frames = [pd.read_csv(d / "spots_g_spinel_candidate.csv") for d in panel_dirs]
    return pd.concat(frames, ignore_index=True)


@requires_ref_data
def test_ab_initio_and_refine_reproduce_known_spinel_cell_single_panel():
    candidate = _load_real_candidate(PANEL1_DIR)

    ab_out = P._stage_ab_initio({"candidate_df": candidate, "sigma_g": 5e-3, "min_reflections": 20})
    assert ab_out["ab_initio_result"]["success"], ab_out["ab_initio_result"]["notes"]
    assert ab_out["ab_initio_result"]["indexed_fraction"] > 0.5

    refine_out = P._stage_refine({
        "ab_initio_raw": ab_out["ab_initio_raw"], "g_2pi": ab_out["g_2pi"], "sigma_g": 5e-3,
    })
    result = refine_out["refine_result"]
    a_conventional = max(result["conventional_cell"][:3])
    # Single-panel-only run; the documented a=7.9672 A is the 6-panel pooled
    # result -- a generous band confirms we're in the right physical regime,
    # not an exact reproduction of the pooled number.
    assert 7.0 <= a_conventional <= 8.3


@requires_ref_data
@pytest.mark.skipif(not PANEL2_DIR.exists(), reason="panel 2 reference data not present")
def test_ab_initio_and_refine_reproduce_known_spinel_cell_two_panels():
    candidate = _load_real_candidate(PANEL1_DIR, PANEL2_DIR)

    ab_out = P._stage_ab_initio({"candidate_df": candidate, "sigma_g": 5e-3, "min_reflections": 20})
    assert ab_out["ab_initio_result"]["success"], ab_out["ab_initio_result"]["notes"]

    refine_out = P._stage_refine({
        "ab_initio_raw": ab_out["ab_initio_raw"], "g_2pi": ab_out["g_2pi"], "sigma_g": 5e-3,
    })
    result = refine_out["refine_result"]
    a_conventional = max(result["conventional_cell"][:3])
    # Closer to the full 6-panel pooled a=7.9672 A with a second panel added.
    assert 7.5 <= a_conventional <= 8.2


def test_ab_initio_reports_failure_without_raising_on_too_few_reflections():
    candidate = pd.DataFrame({
        "qsample_x": [0.1, 0.2, 0.3], "qsample_y": [0.0, 0.1, 0.2], "qsample_z": [0.0, 0.0, 0.1],
    })
    out = P._stage_ab_initio({"candidate_df": candidate, "min_reflections": 20})
    assert out["ab_initio_result"]["success"] is False


def test_refine_raises_clear_error_when_ab_initio_did_not_succeed():
    class _Fake:
        success = False
        notes = ["too few reflections"]

    with pytest.raises(ValueError, match="too few reflections"):
        P._stage_refine({"ab_initio_raw": _Fake(), "g_2pi": np.zeros((0, 3))})


# ═════════════════════════════════════════════════════════════════════════════
#  Ingest plumbing (synthetic frames -- no real raw HDF5 available locally)
# ═════════════════════════════════════════════════════════════════════════════

def test_ingest_runs_end_to_end_on_synthetic_frames_and_writes_outputs(tmp_path):
    rng = np.random.default_rng(0)
    n_frames, nz, ny = 20, 64, 64
    frames = rng.poisson(2.0, size=(n_frames, nz, ny)).astype(np.uint32)
    # Plant a compact, multi-frame "spot" well above background.
    frames[8:12, 30:34, 30:34] += 500

    geometry = {
        "Lsd": 150000.0, "BC_y": 32.0, "BC_z": 32.0, "ty": 0.0, "tz": 0.0,
        "wavelength_A": 0.5, "px_um": 75.0, "nrpixels_y": ny, "nrpixels_z": nz,
        "omega_ref_frame_idx": 0, "omega_ref_deg": -10.0,
        "omega_last_frame_idx": n_frames - 1, "omega_step_deg": 1.0,
    }
    panel_dir = tmp_path / "panel_01_0_0_0"
    out = P._stage_ingest({
        "frames": frames, "geometry": geometry,
        "mask": {"low_count_threshold": 0.0, "grow": 1},
        "blobs": {"threshold": 100.0, "min_vol": 4},
        "panel_dir": panel_dir,
    })
    assert isinstance(out["spots_df"], pd.DataFrame)
    assert "qsample_x" in out["spots_df"].columns
    assert "two_theta_deg" in out["spots_df"].columns
    assert out["summary"]["n_outside_omega_window"] == 0
    assert (panel_dir / "spots_g.csv").exists()
    assert (panel_dir / "ingest_summary.json").exists()


def test_ingest_handles_zero_spots_without_crashing(tmp_path):
    rng = np.random.default_rng(1)
    n_frames, nz, ny = 10, 32, 32
    frames = rng.poisson(1.0, size=(n_frames, nz, ny)).astype(np.uint32)  # no planted spot
    geometry = {
        "Lsd": 150000.0, "BC_y": 16.0, "BC_z": 16.0, "ty": 0.0, "tz": 0.0,
        "wavelength_A": 0.5, "px_um": 75.0, "nrpixels_y": ny, "nrpixels_z": nz,
        "omega_ref_frame_idx": 0, "omega_ref_deg": 0.0,
        "omega_last_frame_idx": n_frames - 1, "omega_step_deg": 1.0,
    }
    out = P._stage_ingest({
        "frames": frames, "geometry": geometry,
        "mask": {"low_count_threshold": 0.0, "grow": 1},
        "blobs": {"threshold": 1000.0, "min_vol": 4},
    })
    assert len(out["spots_df"]) == 0
    for col in ("qsample_x", "qsample_y", "qsample_z", "two_theta_deg"):
        assert col in out["spots_df"].columns


def test_ingest_excludes_frames_outside_omega_window(tmp_path):
    """A spot planted only in frames outside [ref_idx, last_idx] must not be
    found -- those frames are dropped before any other processing, not
    merely given an extrapolated omega."""
    rng = np.random.default_rng(2)
    n_frames, nz, ny = 20, 64, 64
    frames = rng.poisson(2.0, size=(n_frames, nz, ny)).astype(np.uint32)
    # Spot lives only in frames 14-17 -- outside the [0, 9] window below.
    frames[14:18, 30:34, 30:34] += 500

    geometry = {
        "Lsd": 150000.0, "BC_y": 32.0, "BC_z": 32.0, "ty": 0.0, "tz": 0.0,
        "wavelength_A": 0.5, "px_um": 75.0, "nrpixels_y": ny, "nrpixels_z": nz,
        "omega_ref_frame_idx": 0, "omega_ref_deg": 0.0,
        "omega_last_frame_idx": 9, "omega_step_deg": 1.0,
    }
    out = P._stage_ingest({
        "frames": frames, "geometry": geometry,
        "mask": {"low_count_threshold": 0.0, "grow": 1},
        "blobs": {"threshold": 100.0, "min_vol": 4},
    })
    assert out["summary"]["n_frames_loaded"] == n_frames
    assert out["summary"]["n_frames"] == 10
    assert out["summary"]["n_outside_omega_window"] == n_frames - 10
    assert len(out["spots_df"]) == 0


def test_ingest_unions_user_mask_into_internal_mask(tmp_path):
    """A spot that falls entirely under a user-supplied mask must not be
    found -- ``mask.user_mask`` is unioned with the stage's own mask."""
    rng = np.random.default_rng(3)
    n_frames, nz, ny = 20, 64, 64
    frames = rng.poisson(2.0, size=(n_frames, nz, ny)).astype(np.uint32)
    frames[8:12, 30:34, 30:34] += 500

    geometry = {
        "Lsd": 150000.0, "BC_y": 32.0, "BC_z": 32.0, "ty": 0.0, "tz": 0.0,
        "wavelength_A": 0.5, "px_um": 75.0, "nrpixels_y": ny, "nrpixels_z": nz,
        "omega_ref_frame_idx": 0, "omega_ref_deg": -10.0,
        "omega_last_frame_idx": n_frames - 1, "omega_step_deg": 1.0,
    }
    user_mask = np.zeros((nz, ny), dtype=bool)
    user_mask[28:36, 28:36] = True
    out = P._stage_ingest({
        "frames": frames, "geometry": geometry,
        "mask": {"low_count_threshold": 0.0, "grow": 1, "user_mask": user_mask},
        "blobs": {"threshold": 100.0, "min_vol": 4},
    })
    assert len(out["spots_df"]) == 0


def test_ingest_applies_dark_correction_before_blob_finding(tmp_path):
    """A uniform hot offset that would otherwise read as a spot everywhere
    is removed by dark subtraction -- the corrected stack should find the
    one real planted spot, not a blanket false-positive."""
    rng = np.random.default_rng(4)
    n_frames, nz, ny = 20, 64, 64
    dark_level = 300.0
    frames = rng.poisson(2.0, size=(n_frames, nz, ny)).astype(np.float64) + dark_level
    frames[8:12, 30:34, 30:34] += 500

    geometry = {
        "Lsd": 150000.0, "BC_y": 32.0, "BC_z": 32.0, "ty": 0.0, "tz": 0.0,
        "wavelength_A": 0.5, "px_um": 75.0, "nrpixels_y": ny, "nrpixels_z": nz,
        "omega_ref_frame_idx": 0, "omega_ref_deg": -10.0,
        "omega_last_frame_idx": n_frames - 1, "omega_step_deg": 1.0,
    }
    dark = np.full((nz, ny), dark_level)
    out = P._stage_ingest({
        "frames": frames, "geometry": geometry,
        "corrections": {"dark": dark},
        "mask": {"low_count_threshold": 0.0, "grow": 1},
        "blobs": {"threshold": 100.0, "min_vol": 4},
    })
    assert len(out["spots_df"]) >= 1


def test_run_stage_dispatches_and_rejects_unknown_stage():
    with pytest.raises(ValueError, match="unknown solve-cell stage"):
        P.run_stage("not_a_real_stage", {})


def test_panel_dir_name_matches_data_contract_convention():
    assert P.panel_dir_name(1) == "panel_01"
    assert P.panel_dir_name(12) == "panel_12"
