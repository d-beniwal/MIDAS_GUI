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
    for col in ("qsample_x", "qsample_y", "qsample_z", "two_theta_deg", "loaded_frame_idx"):
        assert col in out["spots_df"].columns


def test_ingest_loaded_frame_idx_maps_kept_index_back_to_originally_loaded_index(tmp_path):
    """spots_df['frame'] indexes the KEPT stack (after the omega window +
    live_frames filtering); 'loaded_frame_idx' must map each spot back to
    the index a GUI's DataLoaderPanel.frame_index() uses -- the ORIGINALLY
    loaded stack. A window with ref_idx > 0 makes the two diverge, so an
    accidental identity mapping (the bug this column exists to fix) would be
    caught here."""
    rng = np.random.default_rng(42)
    n_frames, nz, ny = 20, 64, 64
    frames = rng.poisson(2.0, size=(n_frames, nz, ny)).astype(np.uint32)
    frames[8:12, 30:34, 30:34] += 500   # spot lives in ORIGINALLY loaded frames 8-11

    geometry = {
        "Lsd": 150000.0, "BC_y": 32.0, "BC_z": 32.0, "ty": 0.0, "tz": 0.0,
        "wavelength_A": 0.5, "px_um": 75.0, "nrpixels_y": ny, "nrpixels_z": nz,
        "omega_ref_frame_idx": 5, "omega_ref_deg": 0.0,
        "omega_last_frame_idx": 14, "omega_step_deg": 1.0,
    }
    out = P._stage_ingest({
        "frames": frames, "geometry": geometry,
        "mask": {"low_count_threshold": 0.0, "grow": 1},
        "blobs": {"threshold": 100.0, "min_vol": 4},
        "preview_frame_index": 3,   # a KEPT-stack index (see _stage_ingest's own docstring)
    })
    spots = out["spots_df"]
    assert len(spots) > 0
    assert out["summary"]["n_live"] == out["summary"]["n_frames"], (
        "this test assumes live_frames keeps every windowed frame on this "
        "synthetic stack -- if that ever changes the shift-by-ref_idx "
        "arithmetic below no longer holds")
    assert "loaded_frame_idx" in spots.columns
    # No live-frame filtering here, so kept index -> loaded index is a plain
    # +5 shift (the window's ref_idx) -- not the identity, and in-range.
    expected = spots["frame"].round().astype(int) + 5
    assert list(spots["loaded_frame_idx"]) == list(expected)
    assert spots["loaded_frame_idx"].between(8, 11).all()

    # kept index 3 -> loaded index 3 + ref_idx(5) == 8.
    assert out["preview"]["loaded_frame_index"] == 8


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


def test_ingest_honors_sector_candidates_override(tmp_path):
    """A narrowed `blobs.sector_candidates` should be the only count
    choose_sectors considers -- cuts the full-stack search proportionally
    once a panel/geometry's winning n_sectors is already known."""
    rng = np.random.default_rng(5)
    n_frames, nz, ny = 20, 64, 64
    frames = rng.poisson(2.0, size=(n_frames, nz, ny)).astype(np.uint32)
    frames[8:12, 30:34, 30:34] += 500
    geometry = {
        "Lsd": 150000.0, "BC_y": 32.0, "BC_z": 32.0, "ty": 0.0, "tz": 0.0,
        "wavelength_A": 0.5, "px_um": 75.0, "nrpixels_y": ny, "nrpixels_z": nz,
        "omega_ref_frame_idx": 0, "omega_ref_deg": -10.0,
        "omega_last_frame_idx": n_frames - 1, "omega_step_deg": 1.0,
    }
    out = P._stage_ingest({
        "frames": frames, "geometry": geometry,
        "mask": {"low_count_threshold": 0.0, "grow": 1},
        "blobs": {"threshold": 100.0, "min_vol": 4, "sector_candidates": (8,)},
    })
    assert out["summary"]["n_sectors"] == 8


def test_ingest_preview_captures_requested_frame_background_subtracted_and_mask(tmp_path):
    rng = np.random.default_rng(6)
    n_frames, nz, ny = 20, 64, 64
    frames = rng.poisson(2.0, size=(n_frames, nz, ny)).astype(np.uint32)
    frames[8:12, 30:34, 30:34] += 500
    geometry = {
        "Lsd": 150000.0, "BC_y": 32.0, "BC_z": 32.0, "ty": 0.0, "tz": 0.0,
        "wavelength_A": 0.5, "px_um": 75.0, "nrpixels_y": ny, "nrpixels_z": nz,
        "omega_ref_frame_idx": 0, "omega_ref_deg": -10.0,
        "omega_last_frame_idx": n_frames - 1, "omega_step_deg": 1.0,
    }
    out = P._stage_ingest({
        "frames": frames, "geometry": geometry,
        "mask": {"low_count_threshold": 0.0, "grow": 1},
        "blobs": {"threshold": 100.0, "min_vol": 4},
        "preview_frame_index": 3,
    })
    preview = out["preview"]
    assert preview["frame_index"] == 3
    assert preview["background_subtracted"].shape == (nz, ny)
    assert preview["background_subtracted_max_projection"].shape == (nz, ny)
    assert preview["mask"].shape == (nz, ny)
    assert preview["mask"].dtype == bool


def test_ingest_preview_max_projection_sees_a_spot_absent_from_the_requested_frame(tmp_path):
    """The single-frame preview is a poor background-subtraction diagnostic:
    a spot present in frame 9 is invisible in a single-frame preview of frame
    3 (which has no spot), but the max projection must still show it -- this
    is the real-data failure mode reported against Ge-oP32 c1 (STATE.md/
    DECISIONS 2026-10-04): a near-empty single-frame view reading as "no
    background computed" even though the subtraction worked."""
    rng = np.random.default_rng(8)
    n_frames, nz, ny = 20, 64, 64
    frames = rng.poisson(2.0, size=(n_frames, nz, ny)).astype(np.uint32)
    frames[9, 30:34, 30:34] += 500   # spot only on frame 9
    geometry = {
        "Lsd": 150000.0, "BC_y": 32.0, "BC_z": 32.0, "ty": 0.0, "tz": 0.0,
        "wavelength_A": 0.5, "px_um": 75.0, "nrpixels_y": ny, "nrpixels_z": nz,
        "omega_ref_frame_idx": 0, "omega_ref_deg": -10.0,
        "omega_last_frame_idx": n_frames - 1, "omega_step_deg": 1.0,
    }
    out = P._stage_ingest({
        "frames": frames, "geometry": geometry,
        "mask": {"low_count_threshold": 0.0, "grow": 1},
        "blobs": {"threshold": 100.0, "min_vol": 4},
        "preview_frame_index": 3,
    })
    preview = out["preview"]
    assert preview["frame_index"] == 3
    assert preview["background_subtracted"].max() < 100.0   # no spot on frame 3
    proj = preview["background_subtracted_max_projection"]
    assert proj.shape == (nz, ny)
    assert proj.max() > 100.0   # frame 9's spot survives into the projection


def test_ingest_preview_clamps_out_of_range_frame_index(tmp_path):
    rng = np.random.default_rng(7)
    n_frames, nz, ny = 10, 32, 32
    frames = rng.poisson(1.0, size=(n_frames, nz, ny)).astype(np.uint32)
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
        "preview_frame_index": 9999,
    })
    assert 0 <= out["preview"]["frame_index"] < n_frames


# ═════════════════════════════════════════════════════════════════════════════
#  Multi-panel pooled ingest
# ═════════════════════════════════════════════════════════════════════════════

def _synthetic_panel_cfg(rng, spot_at=(8, 12, 30, 34), panel_dir=None):
    n_frames, nz, ny = 20, 64, 64
    frames = rng.poisson(2.0, size=(n_frames, nz, ny)).astype(np.uint32)
    f0, f1, p0, p1 = spot_at
    frames[f0:f1, p0:p1, p0:p1] += 500
    geometry = {
        "Lsd": 150000.0, "BC_y": 32.0, "BC_z": 32.0, "ty": 0.0, "tz": 0.0,
        "wavelength_A": 0.5, "px_um": 75.0, "nrpixels_y": ny, "nrpixels_z": nz,
        "omega_ref_frame_idx": 0, "omega_ref_deg": -10.0,
        "omega_last_frame_idx": n_frames - 1, "omega_step_deg": 1.0,
    }
    cfg = {
        "frames": frames, "geometry": geometry,
        "mask": {"low_count_threshold": 0.0, "grow": 1},
        "blobs": {"threshold": 100.0, "min_vol": 4},
    }
    if panel_dir is not None:
        cfg["panel_dir"] = panel_dir
    return cfg


def test_ingest_pooled_concatenates_every_panel_tagged_by_panel_id(tmp_path):
    rng = np.random.default_rng(10)
    panel1 = _synthetic_panel_cfg(rng, panel_dir=tmp_path / "panel_01" / "data")
    panel1["panel_id"] = 1
    panel2 = _synthetic_panel_cfg(rng, panel_dir=tmp_path / "panel_02" / "data")
    panel2["panel_id"] = 2

    out = P._stage_ingest_pooled({"panels": [panel1, panel2], "pooled_dir": tmp_path / "pooled"})

    spots = out["spots_df"]
    assert "panel_id" in spots.columns
    assert set(spots["panel_id"].unique()) == {1, 2}
    assert len(spots) == len(out["per_panel"][1]["spots_df"]) + len(out["per_panel"][2]["spots_df"])
    assert out["summary"]["n_panels"] == 2
    assert out["summary"]["n_spots_total"] == len(spots)
    # Each panel still writes its own per-panel outputs (unchanged _stage_ingest behavior)...
    assert (tmp_path / "panel_01" / "data" / "spots_g.csv").exists()
    assert (tmp_path / "panel_02" / "data" / "spots_g.csv").exists()
    # ...plus the new pooled combined output.
    assert (tmp_path / "pooled" / "spots_g_pooled.csv").exists()
    assert (tmp_path / "pooled" / "ingest_summary.json").exists()


def test_ingest_pooled_drops_each_panels_frame_stack_before_loading_the_next():
    """frames_loader resolution must happen ONE PANEL AT A TIME -- a pooled
    run that resolved every panel's frames_loader up front would hold every
    panel's full raw stack in memory simultaneously, exactly the memory
    pressure the single-panel ingest performance fix already had to solve."""
    rng = np.random.default_rng(11)
    live_loaders = []

    def _make_loader(frames):
        def _loader():
            live_loaders.append(frames)
            return frames

        return _loader

    cfg1 = _synthetic_panel_cfg(rng)
    frames1 = cfg1.pop("frames")
    cfg1["frames_loader"] = _make_loader(frames1)
    cfg1["panel_id"] = 1
    cfg2 = _synthetic_panel_cfg(rng)
    frames2 = cfg2.pop("frames")
    cfg2["frames_loader"] = _make_loader(frames2)
    cfg2["panel_id"] = 2

    out = P._stage_ingest_pooled({"panels": [cfg1, cfg2]})
    assert len(live_loaders) == 2   # both loaders WERE called...
    assert "panel_id" in out["spots_df"].columns
    # ...but neither panel's cfg dict still references its resolved stack
    # afterward (cleared by _stage_ingest_pooled right after that panel's
    # own _stage_ingest call returns).
    assert "frames" not in cfg1 and "frames" not in cfg2


def test_ingest_pooled_raises_on_empty_panel_list():
    with pytest.raises(ValueError, match="no panels"):
        P._stage_ingest_pooled({"panels": []})


def test_pooling_by_concatenation_recovers_known_cell_without_real_reference_data():
    """Confirms, with synthetic data that needs no gated local reference
    files, the same invariant ``test_ab_initio_and_refine_reproduce_known_
    spinel_cell_two_panels`` already confirms with real data: concatenating
    two panels' own g-vector sets and refining once recovers the shared
    known cell -- this is the physical justification
    ``pipeline._stage_ingest_pooled``'s own docstring gives for why plain
    concatenation of independently-ingested panels is correct (qsample is
    already in the one shared sample frame regardless of panel position)."""
    from midas_hkls import Lattice

    rng = np.random.default_rng(12)
    cell = (5.43, 5.43, 5.43, 90.0, 90.0, 90.0)
    B = np.asarray(Lattice(*cell).reciprocal_cartesian_vectors())
    hkl = rng.integers(-5, 6, size=(400, 3))
    hkl = hkl[np.any(hkl != 0, axis=1)]
    g_1d = (B @ hkl.T).T
    g_2pi = g_1d * P.TWO_PI

    def _candidate_df(g):
        return pd.DataFrame({
            "qsample_x": g[:, 0], "qsample_y": g[:, 1], "qsample_z": g[:, 2],
        })

    # Split the same lattice's reflections across two "panels" -- stands in
    # for two independently-ingested panels of the SAME crystal/orientation,
    # which is what pooling assumes (handoff §5.5, not §5.6's multi-domain
    # case).
    half = len(g_2pi) // 2
    panel1 = {"spots_df": _candidate_df(g_2pi[:half])}
    panel2 = {"spots_df": _candidate_df(g_2pi[half:])}
    pooled = pd.concat([panel1["spots_df"], panel2["spots_df"]], ignore_index=True)

    ab_out = P._stage_ab_initio({"candidate_df": pooled, "sigma_g": 5e-3, "min_reflections": 20})
    assert ab_out["ab_initio_result"]["success"], ab_out["ab_initio_result"]["notes"]
    refine_out = P._stage_refine({
        "ab_initio_raw": ab_out["ab_initio_raw"], "g_2pi": ab_out["g_2pi"], "sigma_g": 5e-3,
    })
    a_conventional = max(refine_out["refine_result"]["conventional_cell"][:3])
    assert a_conventional == pytest.approx(5.43, rel=0.05)


def test_run_stage_dispatches_ingest_pooled():
    rng = np.random.default_rng(13)
    panel = _synthetic_panel_cfg(rng)
    panel["panel_id"] = 1
    out = P.run_stage("ingest_pooled", {"panels": [panel]})
    assert "panel_id" in out["spots_df"].columns


# ═════════════════════════════════════════════════════════════════════════════
#  Stage 5: index a leftover pool against a known cell (unknown orientation)
# ═════════════════════════════════════════════════════════════════════════════

def _synthetic_known_cell_leftover_pool(rng, cell=(5.43, 5.43, 5.43, 90.0, 90.0, 90.0),
                                         n_noise=150, hkl_range=6):
    """A leftover g-vector pool shaped like handoff §5.6's trigger case: a
    second domain of a KNOWN cell at a random, unrelated orientation, mixed
    in with pure-noise g-vectors (spots genuinely not from any lattice)."""
    from scipy.spatial.transform import Rotation

    B0 = P._bmatrix_from_cell(cell, two_pi=False)
    R_true = Rotation.random(random_state=rng).as_matrix()
    UB_true = R_true @ B0
    hkl = rng.integers(-hkl_range, hkl_range + 1, size=(500, 3)).astype(float)
    hkl = hkl[np.any(hkl != 0, axis=1)]
    g_domain = (UB_true @ hkl.T).T
    g_domain = g_domain[np.linalg.norm(g_domain, axis=1) < 1.2]

    dirs = rng.normal(size=(n_noise, 3))
    dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
    g_noise = dirs * rng.uniform(0.1, 1.2, size=n_noise)[:, None]

    g_2pi = np.vstack([g_domain, g_noise]) * P.TWO_PI
    rng.shuffle(g_2pi)
    return g_2pi, cell, len(g_domain)


def test_index_known_cell_recovers_second_domain_orientation():
    rng = np.random.default_rng(0)
    g_2pi, cell, n_domain = _synthetic_known_cell_leftover_pool(rng)

    out = P._stage_index_known_cell({
        "g_2pi": g_2pi, "known_cell": cell, "tol": 0.1,
        "n_search": 20_000, "min_accept": 15, "n_null_draws": 5,
        "rng_seed": 1,
    })
    assert out["success"], out.get("notes")
    assert out["cell"][0] == pytest.approx(cell[0], rel=0.02)
    # The random-orientation seed is only approximately right, so the coarse
    # residual filter typically catches a plausible fraction (not all) of
    # the domain's reflections before the free refine snaps onto the exact
    # orientation -- a meaningfully sized chunk, well above the acceptance
    # floor, not just the bare minimum needed to pass.
    assert out["n_indexed"] >= 0.25 * n_domain
    assert out["diagnostics"]["z_score"] > P.RANDOM_SEARCH_Z_THRESH_DEFAULT


def test_index_known_cell_rejects_pure_noise_pool():
    """Negative-result case (handoff §5.6 step 4): a leftover pool with no
    second domain at all must come back success=False, not a spurious cell."""
    rng = np.random.default_rng(0)
    n_noise = 200
    dirs = rng.normal(size=(n_noise, 3))
    dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
    g_2pi = (dirs * rng.uniform(0.1, 1.2, size=n_noise)[:, None]) * P.TWO_PI

    out = P._stage_index_known_cell({
        "g_2pi": g_2pi, "known_cell": (5.43, 5.43, 5.43, 90.0, 90.0, 90.0),
        "tol": 0.1, "n_search": 20_000, "min_accept": 15, "n_null_draws": 5,
        "rng_seed": 2,
    })
    assert out["success"] is False
    assert out["notes"]


def test_index_known_cell_refuses_below_min_accept_floor_without_searching():
    out = P._stage_index_known_cell({
        "g_2pi": np.zeros((5, 3)), "known_cell": (5.0, 5.0, 5.0, 90.0, 90.0, 90.0),
        "min_accept": 15,
    })
    assert out["success"] is False
    assert out["indexed_mask"].sum() == 0
    assert "below the floor" in out["notes"][0]


def test_run_stage_dispatches_and_rejects_unknown_stage():
    with pytest.raises(ValueError, match="unknown solve-cell stage"):
        P.run_stage("not_a_real_stage", {})


def test_panel_dir_name_matches_data_contract_convention():
    assert P.panel_dir_name(1) == "panel_01"
    assert P.panel_dir_name(12) == "panel_12"
