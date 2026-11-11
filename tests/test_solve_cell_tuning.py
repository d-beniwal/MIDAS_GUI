"""Selection rules of solve_cell.tuning, on the measured Ruby7 counts
(.context/reference/solve_cell_ingest_tuning.md, worked example)."""
from midas_gui.solve_cell import tuning as t


def _row(n, v21, v23, v43, v83, n21=0, n23=0, n43=0, n83=0):
    return {"n_sectors": n, "cuts": {(2, 1): (v21, n21), (2, 3): (v23, n23),
                                     (4, 3): (v43, n43), (8, 3): (v83, n83)}}


# T=8 (noise-dominated) pos/neg for 1, 24, 96 sectors
RUBY_ROWS = [
    _row(1, 29187, 2396, 1418, 354),
    _row(24, 23574, 788, 308, 113, 175, 19, 15, 5),
    _row(96, 25646, 795, 256, 100, 281, 37, 31, 20),
]


def test_sector_choice_ruby7_picks_24_and_rejects_96():
    r = t.select_sectors(RUBY_ROWS)
    assert r["n_sectors"] == 24
    assert [x["n_sectors"] for x in r["rejected"]] == [96]


def test_zero_negatives_is_not_evidence():
    # 1 sector has 0 negatives but 3x the noise positives -> must not win
    assert t.select_sectors(RUBY_ROWS[:2])["n_sectors"] == 24


def test_sector_ties_prefer_fewer_sectors():
    rows = [_row(1, 0, 100, 0, 0), _row(8, 0, 103, 0, 0)]
    assert t.select_sectors(rows)["n_sectors"] == 1


def _grid(T_counts):
    g = {}
    for T, (a, b, c) in T_counts.items():       # pos for v2f3, v4f3, v8f3 ; neg ~ 2 %
        for mv, pos in zip((2, 4, 8), (a, b, c)):
            g[(T, mv, 3)] = (pos, int(pos * 0.02))
    return g


def test_blob_params_plateau():
    g = _grid({8: (788, 308, 113), 10: (200, 120, 70), 12: (78, 67, 54),
               15: (62, 58, 52), 20: (54, 53, 50)})
    r = t.select_blob_params(g, [8, 10, 12, 15, 20])
    assert r["plateau"] and r["confidence"] == "high"
    s = r["suggestion"]
    assert s["blob_minframes"] == 3
    assert 10 <= s["blob_thresh"] <= 20 and s["blob_minvol"] >= 2


def test_blob_params_no_plateau_returns_defaults():
    g = _grid({8: (5000, 2000, 900), 10: (3000, 1200, 500), 12: (1500, 600, 250),
               15: (700, 300, 120), 20: (300, 120, 40)})
    r = t.select_blob_params(g, [8, 10, 12, 15, 20])
    assert not r["plateau"] and r["confidence"] == "low"
    assert r["suggestion"]["blob_thresh"] == t.BLOB_THRESHOLD_DEFAULT


# ── end to end: the real pipeline stage on a small synthetic stack ──────────

def _synthetic_stack(n=40, nz=128, ny=256, n_spots=15, seed=3):
    """Poisson background (~4 counts) + n_spots Gaussian blobs, each ~60 counts
    above background for 5 frames."""
    import numpy as np
    rng = np.random.default_rng(seed)
    a = rng.poisson(4.0, size=(n, nz, ny)).astype(np.uint32)
    zz, yy = np.mgrid[0:nz, 0:ny]
    for _ in range(n_spots):
        f0 = int(rng.integers(2, n - 8)); cz, cy = rng.uniform(10, nz - 10), rng.uniform(10, ny - 10)
        blob = 60.0 * np.exp(-((zz - cz) ** 2 + (yy - cy) ** 2) / (2 * 1.2 ** 2))
        for k in range(5):
            a[f0 + k] += rng.poisson(blob * (1.0 - abs(k - 2) * 0.2)).astype(np.uint32)
    return a


def _geometry(n, nz=128, ny=256):
    return {"Lsd": 60000.0, "BC_y": 120.0, "BC_z": 60.0, "ty": 0.0, "tz": 0.0, "px_um": 75.0,
            "wavelength_A": 0.4959, "nrpixels_y": ny, "nrpixels_z": nz, "im_trans": [],
            "omega_ref_frame_idx": 2, "omega_last_frame_idx": n - 1,
            "omega_ref_deg": 0.0, "omega_step_deg": 0.1}


def test_tune_stage_end_to_end_and_independent_of_worker_count():
    import pytest
    pytest.importorskip("midas_defect")
    from midas_gui.solve_cell import pipeline
    a = _synthetic_stack()
    out = {}
    for w in (1, 2):
        out[w] = pipeline.run_stage("tune_ingest", {"frames": a, "geometry": _geometry(len(a)),
                                                    "workers": w})
    r = out[2]
    assert set(r["suggestion"]) >= {"low_count_threshold", "sector_candidates", "blob_thresh",
                                    "blob_minvol", "blob_minframes", "mask_grow"}
    assert r["n_frames"] == len(a) - 2                      # omega window applied like ingest
    assert [s["n_sectors"] for s in r["evidence"]["sectors"]] == [1, 24, 96]
    assert r["evidence"]["regime"] == "photon_counting"
    # deterministic: same suggestion AND same evidence for any worker count
    assert out[1]["suggestion"] == out[2]["suggestion"]
    assert out[1]["evidence"] == out[2]["evidence"]
    # ground truth: 15 spots were injected into uniform noise
    assert r["suggestion"]["sector_candidates"] == "1"
    assert abs(r["evidence"]["blob"]["expected_spots"] - 15) <= 2
    # the report renders without error and names the choice
    from midas_gui.solve_cell import tuning
    assert "sectors" in tuning.format_report(r)
