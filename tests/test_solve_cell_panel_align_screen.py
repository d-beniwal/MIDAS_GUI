"""panel_align (per-panel omega offsets) and phase_screen (phases vs same-volume decoys)."""
import numpy as np
import pytest

from midas_gui.solve_cell import cell_report as cr, panel_align as pa, phase_screen as ps
from midas_gui.solve_cell.geometry_adapter import qlab_to_sample
from midas_gui.solve_cell.pipeline import _bmatrix_from_cell, _random_rotation_search

TWO_PI = 2 * np.pi
CELL = (5.0, 5.4, 6.1, 80.0, 95.0, 100.0)


def _scan(true_offsets, n_per_panel=45, seed=0, noise=0.0015):
    """Spots of one crystal seen by several panels whose reported omega is off by ``true_offsets``."""
    rng = np.random.default_rng(seed)
    B0 = _bmatrix_from_cell(CELL)
    rot = np.linalg.qr(rng.normal(size=(3, 3)))[0]
    rot *= np.sign(np.linalg.det(rot))
    UB = rot @ B0
    qlab, om, pid = [], [], []
    for p, delta in true_offsets.items():
        hkl = rng.integers(-3, 4, size=(n_per_panel, 3))
        hkl = hkl[np.abs(hkl).sum(1) > 0]
        gs = TWO_PI * (hkl @ UB.T)
        w_true = rng.uniform(-30, 30, len(hkl))
        q = qlab_to_sample(gs, -w_true)                       # inverse rotation: lab-frame q at the true omega
        qlab.append(q + rng.normal(scale=noise, size=q.shape))
        om.append(w_true - delta)                             # what the panel REPORTS (off by -delta)
        pid.append(np.full(len(hkl), p))
    return np.vstack(qlab), np.concatenate(om), np.concatenate(pid), B0


def test_rotation_inverse_convention_used_by_the_fixture():
    x = np.random.default_rng(1).normal(size=(5, 3))
    w = np.linspace(-20, 20, 5)
    assert np.allclose(qlab_to_sample(qlab_to_sample(x, w), -w), x, atol=1e-9)


def test_offsets_recovered_relative_to_the_reference_panel():
    truth = {1: 0.0, 2: 1.8, 3: -2.6}
    qlab, om, pid, B0 = _scan(truth)
    out = pa.fit_panel_omega_offsets(qlab, om, pid, B0, tol=0.08, n_search=60_000)
    ref = out["reference_panel"]
    for p, t in truth.items():
        assert out["offsets"][p] == pytest.approx(t - truth[ref], abs=0.35), (p, out["offsets"])
    assert out["offsets"][ref] == 0.0
    assert out["n_matched_after"] > out["n_matched_before"]
    assert out["n_matched_after"] >= 0.9 * out["n_spots"]
    assert out["rms_after"] < out["rms_before"]


def test_apply_omega_offsets_roundtrip():
    truth = {1: 0.0, 2: 1.8}
    qlab, om, pid, B0 = _scan(truth)
    out = pa.fit_panel_omega_offsets(qlab, om, pid, B0, tol=0.08, n_search=60_000)
    om2, qs = pa.apply_omega_offsets(qlab, om, pid, out["offsets"])
    assert np.allclose(om2 - om, [out["offsets"][int(p)] for p in pid])
    frac = np.abs(np.linalg.solve(out["UB"], (qs / TWO_PI).T).T)
    frac = np.abs(frac - np.round(frac)).max(1)
    assert (frac < 0.08).mean() > 0.9


def test_decoys_have_the_requested_volume():
    cells = ps.random_decoy_cells(85.0, 12, np.random.default_rng(0))
    assert len(cells) == 12
    assert all(cr.cell_volume(c) == pytest.approx(85.0, rel=1e-6) for c in cells)


def test_screen_prefers_the_true_lattice_over_decoys_and_other_phases():
    B0 = _bmatrix_from_cell(CELL)
    rng = np.random.default_rng(3)
    rot = np.linalg.qr(rng.normal(size=(3, 3)))[0]; rot *= np.sign(np.linalg.det(rot))
    hkl = rng.integers(-4, 5, size=(70, 3)); hkl = hkl[np.abs(hkl).sum(1) > 0]
    g = TWO_PI * (hkl @ (rot @ B0).T) + rng.normal(scale=0.002, size=(len(hkl), 3))
    phases = [("truth", CELL, "P", "P1"), ("other", (4.2, 4.2, 4.2, 90, 90, 90), "F", "Fm-3m")]
    rows = ps.screen_phases(g, phases, tol=0.08, n_search=40_000, n_decoys=8)
    assert rows[0]["name"] == "truth" and rows[0]["supported"]
    assert rows[0]["best_n"] >= 0.9 * len(g)
    assert rows[0]["z"] > 4
    other = next(r for r in rows if r["name"] == "other")
    assert not other["supported"] and other["best_n"] < rows[0]["best_n"]


def test_identify_report_on_a_synthetic_record():
    import pandas as pd
    from midas_gui.solve_cell import identify
    rng = np.random.default_rng(4)
    A = cr.primitive_basis((4.7587, 4.7587, 12.9929, 90, 90, 120), "R")
    B0 = _bmatrix_from_cell(cr.cell_from_rows(A))
    rot = np.linalg.qr(rng.normal(size=(3, 3)))[0]; rot *= np.sign(np.linalg.det(rot))
    UB = rot @ B0
    parts = []
    for p, delta in ((1, 0.0), (2, 2.0)):
        hkl = rng.integers(-3, 4, size=(60, 3)); hkl = hkl[np.abs(hkl).sum(1) > 0]
        w = rng.uniform(-30, 30, len(hkl))
        ql = qlab_to_sample(TWO_PI * (hkl @ UB.T), -w)
        rep = w - delta
        qs = qlab_to_sample(ql, rep)
        parts.append(pd.DataFrame({"panel_id": p, "omega_deg": rep, "qlab_x": ql[:, 0], "qlab_y": ql[:, 1], "qlab_z": ql[:, 2],
                                   "qsample_x": qs[:, 0], "qsample_y": qs[:, 1], "qsample_z": qs[:, 2], "is_diamond": False}))
    rec = {"diamond": {"flagged_df": pd.concat(parts, ignore_index=True)}}
    res = identify.identify(rec, n_decoys=3, n_control=0, log=lambda *_: None)
    assert res["best_phase"].startswith("Corundum")
    assert res["align"]["n_matched_after"] >= res["align"]["n_matched_before"]
    assert res["known_structure"]["aligned"]["closest"]["name"].startswith("Corundum")
    md = identify.format_markdown(res, "synthetic")
    assert "Which candidate phase" in md and "Per-panel omega offsets" in md and "Corundum" in md
