"""cell_report: lattice interpretation of a refined cell (no Qt)."""
import numpy as np
import pytest

from midas_gui.solve_cell import cell_report as cr

RUBY_FIT = (4.7096, 4.7889, 5.0422, 90.984, 63.411, 61.64)     # a real refined cell (Oct 2026)


def test_basis_roundtrip_and_volume():
    cell = (5.1, 6.2, 7.3, 80.0, 95.0, 102.0)
    A = cr.basis_rows(cell)
    assert np.allclose(cr.cell_from_rows(A), cell)
    assert cr.cell_volume(cell) == pytest.approx(abs(np.linalg.det(A)))


def test_primitive_basis_volumes():
    a = 8.0
    cub = (a, a, a, 90, 90, 90)
    assert cr.cell_volume(cub) / abs(np.linalg.det(cr.primitive_basis(cub, "F"))) == pytest.approx(4)
    assert cr.cell_volume(cub) / abs(np.linalg.det(cr.primitive_basis(cub, "I"))) == pytest.approx(2)
    hexc = (4.7587, 4.7587, 12.9929, 90, 90, 120)
    assert cr.cell_volume(hexc) / abs(np.linalg.det(cr.primitive_basis(hexc, "R"))) == pytest.approx(3)
    # a rhombohedral cell labelled R is already primitive
    rh = (5.13, 5.13, 5.13, 55.3, 55.3, 55.3)
    assert abs(np.linalg.det(cr.primitive_basis(rh, "R"))) == pytest.approx(cr.cell_volume(rh))


def test_relabelled_known_lattice_matches_exactly():
    A = cr.primitive_basis((8.0831,) * 3 + (90.0,) * 3, "F")
    other_setting = cr.cell_from_rows(np.array([[1, 1, 0], [0, 1, 0], [0, 1, 1]]) @ A)
    best = cr.rank_references(other_setting)[0]
    assert best["name"].startswith("Spinel") and best["score"] < 1e-6 and best["verdict"] == "matches"
    assert abs(best["d_vol_pct"]) < 1e-6


def test_distorted_corundum_is_still_closest_and_deviations_have_sign():
    # real ruby fit: the c-type edge is 1.7 % short of corundum's 5.129 A rhombohedral edge
    rows = cr.rank_references(RUBY_FIT)
    best = rows[0]
    assert best["name"].startswith("Corundum")
    assert best["verdict"] in ("matches", "close")
    assert min(best["d_len_pct"]) == pytest.approx(-1.69, abs=0.1)      # observed - reference
    assert abs(best["d_vol_pct"]) < 1.5
    assert all(r["score"] > 3 * best["score"] for r in rows[1:3])       # clearly not the others


def test_unrelated_cell_has_no_close_match():
    rows = cr.rank_references((3.1, 7.7, 9.3, 80.0, 95.0, 101.0))
    assert not rows or all(r["verdict"] == "no match" for r in rows)


def test_match_overlay_axes_are_a_rotation_of_the_reference():
    m = cr.rank_references(RUBY_FIT)[0]
    rows = np.asarray(m["ref_rows"])
    assert np.allclose(cr.cell_from_rows(rows), m["ref_cell"], atol=1e-6)


def test_reduced_cell_and_ladder_show_symmetry_emerging():
    assert cr.reduced_cell(RUBY_FIT)[0] == pytest.approx(4.7096, abs=1e-3)
    systems = [r["system"] for r in cr.symmetry_ladder(RUBY_FIT)]
    assert systems[0] == "triclinic"
    assert systems[-1] != "triclinic"                                   # higher symmetry only when loose
    assert [r["tol_len_pct"] for r in cr.symmetry_ladder(RUBY_FIT)] == sorted(
        r["tol_len_pct"] for r in cr.symmetry_ladder(RUBY_FIT))


def test_build_report_and_notes():
    rep = cr.build_report({"cell": RUBY_FIT, "cell_sigma": (0.001,) * 6})
    assert rep["volume"] == pytest.approx(85.6, abs=0.2)
    assert any("statistical only" in n for n in rep["notes"])
    assert "Corundum" in cr.summary_line(rep)


def test_extra_refs_from_materials_and_centring():
    from midas_gui.solve_cell.material_filter import preset
    assert cr.centring_from_space_group(227) == "F"
    assert cr.centring_from_space_group(194) == "P"
    assert cr.centring_from_space_group(229) == "I"
    refs = cr.extra_refs_from_materials([preset("Gold (marker)")])
    assert refs and refs[0][2] == "F"
    top = cr.rank_references(cr.cell_from_rows(cr.primitive_basis((4.0782,) * 3 + (90,) * 3, "F")), refs)[0]
    assert top["name"] in ("Gold", "Gold (marker)") and top["score"] < 1e-6
