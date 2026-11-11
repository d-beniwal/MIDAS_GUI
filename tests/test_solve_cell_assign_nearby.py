"""assign.reassign_spots and viz_products.nearby_other_spots (pure numpy/pandas)."""
import numpy as np
import pandas as pd

from midas_gui.solve_cell import assign, viz_products as vp


def _crystal(n=60, seed=0, a=5.0, noise=0.003):
    rng = np.random.default_rng(seed)
    UB0 = np.eye(3) / a
    rot = np.linalg.qr(rng.normal(size=(3, 3)))[0]
    rot *= np.sign(np.linalg.det(rot))
    UB = rot @ UB0
    hkl = rng.integers(-4, 5, size=(n, 3))
    hkl = hkl[np.abs(hkl).sum(1) > 0]
    g = 2 * np.pi * (hkl @ UB.T) + rng.normal(scale=noise, size=(len(hkl), 3))
    return UB, hkl, g


def test_reassign_recovers_spots_the_seed_missed():
    UB, hkl, g = _crystal()
    # every spot of the crystal, plus junk: a seed UB that is slightly off misses spots at tight tol
    junk = np.random.default_rng(1).normal(size=(30, 3)) * 1.5
    G = np.vstack([g, junk])
    seed = UB * 1.015                                   # 1.5 % too small a cell
    avail = np.ones(len(G), bool)
    tight = assign.fractional_misfit(seed, G)[0] < 0.05
    out = assign.reassign_spots(seed, G, avail, tol=0.15)
    assert out["result"] is not None
    got = out["mask"][:len(g)].sum()
    assert got > tight[:len(g)].sum()                   # more crystal spots than the seed's tight cut
    assert got >= 0.9 * len(g)
    assert out["mask"][len(g):].sum() <= 6              # junk stays mostly out at this tolerance
    assert out["history"][-1] >= out["history"][0]
    assert np.allclose(sorted(out["result"]["cell"][:3]), [5, 5, 5], atol=0.05)


def test_reassign_never_takes_unavailable_spots_and_reports_failure():
    UB, hkl, g = _crystal()
    avail = np.ones(len(g), bool)
    avail[:10] = False                                  # owned by another domain
    out = assign.reassign_spots(UB, g, avail, tol=0.15)
    assert not out["mask"][:10].any()
    few = assign.reassign_spots(UB, g, np.arange(len(g)) < 3, tol=0.15)
    assert few["result"] is None and few["mask"].sum() <= 3


def test_nearby_other_spots_gates_panels_and_owners():
    pdf = pd.DataFrame({"panel_id": [1, 1, 2, 1], "row": [100., 300., 100., 50.], "col": [200., 200., 200., 50.],
                        "omega_deg": [5., 5., 5., 5.], "visibility": ["visible"] * 3 + ["off_panel"]})
    others = [
        {"spot_id": 10, "panel_id": 1, "row": 103., "col": 198., "omega_deg": 5.4, "owner": "unassigned"},
        {"spot_id": 11, "panel_id": 1, "row": 300., "col": 200., "omega_deg": 9.0, "owner": "Domain 2"},   # omega too far
        {"spot_id": 12, "panel_id": 2, "row": 100., "col": 205., "omega_deg": 5.0, "owner": "Domain 2"},
    ]
    near = vp.nearby_other_spots(pdf, others)
    assert near.nearby_spot_id.tolist() == [10, -1, 12, -1]
    assert near.nearby_owner.tolist() == ["unassigned", "", "Domain 2", ""]
    assert near.loc[0, "nearby_d_row"] == 3.0 and near.loc[0, "nearby_d_col"] == -2.0
    assert vp.nearby_other_spots(pdf, []).nearby_spot_id.tolist() == [-1] * 4


def test_context_fingerprint_tracks_ownership():
    a = [{"spot_id": 1, "owner": "unassigned"}, {"spot_id": 2, "owner": "Domain 1"}]
    b = [{"spot_id": 1, "owner": "Domain 2"}, {"spot_id": 2, "owner": "Domain 1"}]
    assert vp.context_fingerprint(a) != vp.context_fingerprint(b)
    assert vp.context_fingerprint(a) == vp.context_fingerprint(list(reversed(a)))
