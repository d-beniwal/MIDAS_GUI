"""Contaminant-material filter: lattices, extinctions, 2-theta / orientation modes,
protection of spots that index on a solved domain, and the stage wrapper."""
import numpy as np
import pandas as pd
import pytest

from midas_gui.solve_cell import material_filter as mf
from midas_gui.solve_cell import pipeline as P

WL = 0.49594
TWO_PI = 2 * np.pi


def _spots_from_vectors(g_1d, counts=None):
    """Spot table with the columns the filter reads, from sample-frame g (1/d)."""
    g_1d = np.asarray(g_1d, float)
    gn = np.linalg.norm(g_1d, axis=1)
    tth = np.degrees(2 * np.arcsin(gn * WL / 2))
    q = g_1d * TWO_PI
    return pd.DataFrame({
        "spot_id": np.arange(len(g_1d)), "panel_id": 1, "two_theta_deg": tth,
        "qsample_x": q[:, 0], "qsample_y": q[:, 1], "qsample_z": q[:, 2],
        "peak_counts": np.full(len(g_1d), 1000.0) if counts is None else counts,
    })


def _rotation(seed=1):
    from scipy.spatial.transform import Rotation
    return Rotation.random(random_state=seed).as_matrix()


def _diamond_vectors(rot, hkls):
    B = mf.bmatrix(mf.preset("Diamond").cell)
    return (rot @ (B @ np.array(hkls, float).T)).T


def test_diamond_preset_reproduces_the_legacy_line_table():
    lines = mf.material_lines(mf.preset("Diamond"), WL)
    assert np.allclose(lines["tth_deg"].to_numpy(), P._diamond_tth_lines_deg(WL, 3.5667))


def test_extinctions_follow_space_group_and_diamond_basis():
    fcc = mf.Material(name="Au", cell=(4.0782,) * 3 + (90.0,) * 3, space_group=225)
    hkl, _ = mf.allowed_reflections(fcc, WL)
    keys = {tuple(abs(x) for x in h) for h in hkl}
    assert (1, 1, 1) in keys and (2, 0, 0) in keys and (1, 0, 0) not in keys   # F: all odd / all even
    sg_only = mf.Material(cell=(3.5667,) * 3 + (90.0,) * 3, space_group=227, extinction="space_group")
    dia = mf.preset("Diamond")
    keys_sg = {tuple(sorted(abs(x) for x in h)) for h in mf.allowed_reflections(sg_only, WL)[0]}
    keys_dia = {tuple(sorted(abs(x) for x in h)) for h in mf.allowed_reflections(dia, WL)[0]}
    assert (2, 2, 2) in keys_sg and (2, 2, 2) not in keys_dia      # 222: Fd-3m allows it, diamond's atoms do not
    none = mf.Material(cell=(4.0,) * 3 + (90.0,) * 3, extinction="none")
    assert (1, 0, 0) in {tuple(abs(x) for x in h) for h in mf.allowed_reflections(none, WL)[0]}


def test_material_from_dict_is_tolerant_and_validates():
    m = mf.Material.from_dict({"name": "X", "cell": [3, 3, 3, 90, 90, 90], "bogus": 1})
    assert m.name == "X" and m.mode == "2theta"
    with pytest.raises(ValueError):
        mf.Material.from_dict({"cell": [1, 2, 3]})
    with pytest.raises(ValueError):
        mf.Material.from_dict({"mode": "nope"})
    assert mf.Material.from_dict(m.to_dict()) == m


def test_two_theta_mode_flags_on_line_and_respects_min_counts():
    dia = mf.preset("Diamond")
    lines = mf.material_lines(dia, WL)["tth_deg"].to_numpy()
    spots = pd.DataFrame({"two_theta_deg": [lines[3], lines[3] + 5.0, lines[3]], "peak_counts": [500.0, 500.0, 5.0]})
    out, s = mf.apply_material_filter(spots, WL, [dia], n_mc=200)
    assert out["is_diamond"].tolist() == [True, False, True]
    dia.min_peak_counts = 100.0
    out, s = mf.apply_material_filter(spots, WL, [dia], n_mc=200)
    assert out["is_diamond"].tolist() == [True, False, False] and s["n_kept"] == 2


def test_disabled_filter_passes_every_spot_through():
    spots = _spots_from_vectors(_diamond_vectors(_rotation(), [(2, 2, 0), (0, 2, 2), (2, 0, 2)]))
    out, s = mf.apply_material_filter(spots, WL, mf.default_materials(), enabled=False)
    assert not out["is_diamond"].any() and s["n_kept"] == 3 and s["enabled"] is False
    # no enabled material behaves the same
    off = mf.preset("Diamond"); off.enabled = False
    out, s = mf.apply_material_filter(spots, WL, [off])
    assert not out["is_diamond"].any()


def test_several_materials_each_claim_their_own_lines():
    dia, au = mf.preset("Diamond"), mf.preset("Gold (marker)")
    dl = mf.material_lines(dia, WL)["tth_deg"].to_numpy()
    al = mf.material_lines(au, WL)["tth_deg"].to_numpy()
    far = np.min(np.abs(al[:, None] - dl[None, :]), axis=1)
    pick = al[np.argmax(far)]                     # a gold line well away from every diamond line
    spots = pd.DataFrame({"two_theta_deg": [dl[2], pick, 3.3], "peak_counts": 1.0})
    out, s = mf.apply_material_filter(spots, WL, [dia, au], n_mc=200)
    assert out["contaminant_id"].tolist() == [0, 1, -1]
    assert set(s["per_material"]) == {"Diamond", "Gold"}


def test_orientation_mode_keeps_same_radius_spots_that_fit_no_crystal():
    rot = _rotation(3)
    real = _diamond_vectors(rot, [(2, 2, 0), (0, 2, 2), (2, 0, 2), (4, 0, 0)])
    # decoy: same |g| as (220) but pointing somewhere no diamond orientation puts it
    dirv = np.cross(real[0], real[3]); dirv /= np.linalg.norm(dirv)
    decoy = dirv * np.linalg.norm(real[0])
    spots = _spots_from_vectors(np.vstack([real, decoy]))
    dia = mf.preset("Diamond")
    out_2t, _ = mf.apply_material_filter(spots, WL, [dia], n_mc=200)
    assert out_2t["is_diamond"].tolist() == [True] * 4 + [True]          # radial test flags the decoy too
    dia.mode = "orientation"; dia.orient_tol_deg = 0.5
    out, s = mf.apply_material_filter(spots, WL, [dia], n_mc=200)
    assert out["is_diamond"].tolist() == [True] * 4 + [False]
    crystals = s["per_material"]["Diamond"]["crystals"]
    assert len(crystals) == 1 and crystals[0]["n_spots"] == 4 and crystals[0]["rms_deg"] < 0.05


def test_orientation_mode_finds_two_crystals_and_needs_min_spots():
    a = _diamond_vectors(_rotation(5), [(2, 2, 0), (0, 2, 2), (2, 0, 2)])
    b = _diamond_vectors(_rotation(11), [(2, 2, 0), (0, 2, 2), (2, 0, 2), (4, 0, 0)])
    spots = _spots_from_vectors(np.vstack([a, b]))
    dia = mf.preset("Diamond", mode="orientation", max_crystals=2)
    out, s = mf.apply_material_filter(spots, WL, [dia], n_mc=200)
    assert out["is_diamond"].all()
    assert sorted(c["n_spots"] for c in s["per_material"]["Diamond"]["crystals"]) == [3, 4]
    one = mf.preset("Diamond", mode="orientation", max_crystals=1)
    out, _ = mf.apply_material_filter(spots, WL, [one], n_mc=200)
    assert out["is_diamond"].sum() == 4
    strict = mf.preset("Diamond", mode="orientation", min_spots=5)
    out, _ = mf.apply_material_filter(spots, WL, [strict], n_mc=200)
    assert not out["is_diamond"].any()


def test_protect_if_indexed_keeps_spots_that_index_on_a_solved_domain():
    dia_vecs = _diamond_vectors(_rotation(2), [(2, 2, 0), (0, 2, 2), (2, 0, 2)])
    n220 = np.linalg.norm(dia_vecs[0])
    # a 'sample' lattice whose (1,1,1) reflection has exactly diamond-220's |g|:
    # that spot is on a diamond line AND indexes on the domain
    s = n220 / np.sqrt(3.0)
    UB = np.diag([s, s, s])
    sample_spot = np.array([[s, s, s]])
    spots = _spots_from_vectors(np.vstack([dia_vecs, sample_spot]))
    dom = [{"label": "Domain 1", "result": {"UB": UB.tolist()}}]

    plain, _ = mf.apply_material_filter(spots, WL, [mf.preset("Diamond")], n_mc=100)
    assert plain["is_diamond"].tolist() == [True] * 4                      # radial test flags it
    prot, sm = mf.apply_material_filter(spots, WL, [mf.preset("Diamond", protect_if_indexed=True)],
                                        n_mc=100, known_domains=dom)
    assert prot["protected_by"].tolist() == [0, 0, 0, 1]                    # only the on-lattice spot, by domain 1
    assert prot["is_diamond"].tolist() == [True, True, True, False] and sm["n_protected"] == 1
    # without a solved domain, or with the option off, nothing is rescued
    none, _ = mf.apply_material_filter(spots, WL, [mf.preset("Diamond", protect_if_indexed=True)], n_mc=100)
    assert none["is_diamond"].sum() == 4
    off, _ = mf.apply_material_filter(spots, WL, [mf.preset("Diamond")], n_mc=100, known_domains=dom)
    assert off["is_diamond"].sum() == 4


def test_stage_wrapper_accepts_legacy_keys_and_materials_and_off_switch():
    dia = mf.preset("Diamond")
    lines = mf.material_lines(dia, WL)["tth_deg"].to_numpy()
    spots = pd.DataFrame({"two_theta_deg": [lines[4], lines[4] + 4.0], "peak_counts": 1.0})
    legacy = P._stage_diamond_filter({"spots_df": spots, "wavelength_A": WL, "n_mc": 100})
    assert legacy["summary"]["n_diamond_flagged"] == 1 and legacy["summary"]["tolerance_deg"] == 0.07
    new = P.run_stage("material_filter", {"spots_df": spots, "wavelength_A": WL, "n_mc": 100,
                                          "materials": [dia.to_dict()]})
    assert new["flagged_df"]["is_diamond"].tolist() == [True, False] and len(new["candidate_df"]) == 1
    off = P._stage_diamond_filter({"spots_df": spots, "wavelength_A": WL, "materials": [dia.to_dict()],
                                   "filter_enabled": False})
    assert len(off["candidate_df"]) == 2 and off["summary"]["n_flagged"] == 0
    assert list(off["candidate_df"]["spot_id"]) == [0, 1]
