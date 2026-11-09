"""Solve Cell visualization products: geometry adapter, forward prediction,
residuals, project persistence of cached viz products (no Qt except the last
block, which is forked like the other tab tests)."""
import numpy as np
import pandas as pd
import pytest

from midas_gui.solve_cell import geometry_adapter as ga
from midas_gui.solve_cell import viz_products as vp

WL = 0.49594


def _snap(im_trans=(2,), shape=(512, 1028), bcy=-268.5, bcz=385.7):
    cfg = {"Lsd": 136744.7, "BC_y": bcy, "BC_z": bcz, "ty": 0.4, "tz": -1.2, "wavelength_A": WL,
           "px_um": 75.0, "nrpixels_y": shape[1], "nrpixels_z": shape[0],
           "im_trans": list(im_trans), "omega_ref_frame_idx": 10, "omega_ref_deg": -30.0,
           "omega_step_deg": 0.1, "omega_last_frame_idx": 610}
    return ga.PanelSnapshot(1, cfg, shape, 650, 10, -30.0, 0.1, 610)


@pytest.mark.parametrize("codes", [(), (1,), (2,), (3,), (3, 2), (2, 3), (1, 3, 2)])
def test_pixel_qlab_roundtrip_with_im_trans(codes):
    snap = _snap(im_trans=codes, shape=(512, 1028))
    # rectangular + asymmetric: a few interior pixels
    rows = np.array([100.0, 250.5, 400.0])
    cols = np.array([900.0, 300.0, 120.25])
    q = ga.raw_pixels_to_qlab(snap, rows, cols)
    r, c = ga.qlab_to_raw_pixels(snap, q)
    np.testing.assert_allclose(r, rows, atol=2e-3)
    np.testing.assert_allclose(c, cols, atol=2e-3)


def test_ewald_cap_lies_on_sphere_and_through_origin():
    for om in (-20.0, 0.0, 37.0):
        cap = vp.ewald_cap(WL, om, 3.0)
        k0 = 2 * np.pi / WL
        pts = cap["patch"].reshape(-1, 3)
        np.testing.assert_allclose(np.linalg.norm(pts + cap["ki"], axis=1), k0, atol=1e-9)
        np.testing.assert_allclose(cap["patch"][0, 0], 0.0, atol=1e-12)
        assert np.linalg.norm(pts, axis=1).max() == pytest.approx(3.0, rel=1e-9)
        # |q| of every pixel is independent of omega (only the frame rotates)
    a = np.linalg.norm(vp.ewald_cap(WL, 0.0, 3.0)["patch"], axis=2)
    b = np.linalg.norm(vp.ewald_cap(WL, 50.0, 3.0)["patch"], axis=2)
    np.testing.assert_allclose(a, b, atol=1e-9)


def test_prediction_matches_synthetic_observations():
    """Observations generated FROM a UB's own crossings must come back with ~0 residual."""
    snap = _snap()
    UB = np.array([[0.13, -0.12, -0.14], [0.127, 0.145, -0.104], [0.119, 0.107, 0.130]])
    rng = (-30.0, 30.0)
    pred = ga.predict_reflections(UB, {1: snap}, {1: rng}, q_max=4.0)
    vis = [p for p in pred if p["visibility"] == "visible"]
    assert vis, "synthetic cell should put some reflections on the panel"
    rows = [{"spot_id": i, "panel_id": 1, "row": p["row"], "col": p["col"], "omega_deg": p["omega_deg"],
             "hkl": (p["h"], p["k"], p["l"])} for i, p in enumerate(vis)]
    res = ga.match_predictions(rows, pred)
    assert len(res) == len(rows)
    assert max(abs(r["d_row"]) for r in res) < 1e-9
    assert max(abs(r["d_omega_deg"]) for r in res) < 1e-9


def test_residual_sign_is_observed_minus_predicted():
    snap = _snap()
    UB = np.array([[0.13, -0.12, -0.14], [0.127, 0.145, -0.104], [0.119, 0.107, 0.130]])
    pred = ga.predict_reflections(UB, {1: snap}, {1: (-30.0, 30.0)}, q_max=3.0)
    p = next(p for p in pred if p["visibility"] == "visible")
    obs = {"spot_id": 0, "panel_id": 1, "row": p["row"] + 1.5, "col": p["col"] - 2.0,
           "omega_deg": p["omega_deg"] + 0.05, "hkl": (p["h"], p["k"], p["l"])}
    r = ga.match_predictions([obs], pred)[0]
    assert (r["d_row"], r["d_col"], r["d_omega_deg"]) == pytest.approx((1.5, -2.0, 0.05))


def test_scan_longer_than_360_lifts_every_turn():
    assert ga._lift_into_window(10.0, -400.0, 400.0) == [-350.0, 10.0, 370.0]
    assert ga._lift_into_window(10.0, 20.0, 30.0) == []


def test_classify_and_near_omega():
    pred = pd.DataFrame({
        "h": [1, 1, 2, 3], "k": [0, 0, 0, 0], "l": [0, 0, 0, 0],
        "qx": [1.0, 1.0, 2.0, 3.0], "qy": [0.0] * 4, "qz": [0.0] * 4,
        "omega_deg": [10.0, 200.0, 20.0, 30.0],
        "visibility": ["visible", "off_panel", "visible", "off_panel"],
        "observed_spot_id": [5, -1, -1, -1]})
    c = vp.classify_lattice_points(pred)
    assert len(c["observed"]) == 1 and len(c["missing"]) == 1 and len(c["hidden"]) == 1
    near = vp.lattice_points_near_omega(pred, 199.7, 0.5)
    assert near.shape == (1, 3) and near[0, 0] == 1.0
    # wraps through +-180
    assert len(vp.lattice_points_near_omega(pred.assign(omega_deg=[179.8, 0, 0, 0]), -179.9, 0.5)) >= 1


def test_frame_manifest_marks_window():
    meta = {"summary": {"per_panel": {"1": {"n_frames_loaded": 20, "n_frames": 11, "n_live": 11}}},
            "panels": {"1": {"panel_id": 1, "geometry_cfg": _snap().geom_cfg | {
                "omega_ref_frame_idx": 4, "omega_last_frame_idx": 14}}}}
    df = vp.build_frame_manifest(meta)["tables"]["frames"]
    assert len(df) == 20 and int(df.in_window.sum()) == 11
    row = df[df.loaded_idx == 4].iloc[0]
    assert row.omega_start_deg == pytest.approx(-30.0)
    assert row.omega_mid_deg == pytest.approx(-29.95)


def test_viz_roundtrip_in_project(tmp_path):
    from midas_gui import project
    path = tmp_path / "p.h5"
    project.create_project(path, "t")
    spots = pd.DataFrame({"spot_id": [0, 1], "panel_id": [1, 1]})
    project.write_solve_cell_ingest(path, meta={"panels": {}}, spots_df=spots)
    tab = pd.DataFrame({"a": [1.0, 2.0], "visibility": ["visible", "off_panel"]})
    project.write_solve_cell_viz(path, "prod", meta={"inputs": {"k": 1}}, tables={"t": tab},
                                 arrays={"x": np.arange(6).reshape(2, 3)})
    rec = project.read_solve_cell(path)
    got = rec["viz"]["prod"]
    assert got["meta"]["inputs"] == {"k": 1}
    assert got["tables"]["t"].visibility.tolist() == ["visible", "off_panel"]
    np.testing.assert_array_equal(got["arrays"]["x"], np.arange(6).reshape(2, 3))
    # re-indexing must not drop viz; a new ingest (whole-record swap) does
    project.write_solve_cell_diamond(path, meta={}, flagged_df=spots.assign(is_diamond=False))
    assert "prod" in project.read_solve_cell(path)["viz"]
    project.write_solve_cell_ingest(path, meta={"panels": {}}, spots_df=spots)
    assert project.read_solve_cell(path)["viz"] is None


# ── reciprocal volume (V03/V04/V05) ──────────────────────────────────────────
from midas_gui.solve_cell import reciprocal_volume as rv


def _small_snap():
    cfg = {"Lsd": 136744.7, "BC_y": 60.0, "BC_z": 30.0, "ty": 0.0, "tz": 0.0, "wavelength_A": WL,
           "px_um": 75.0, "nrpixels_y": 128, "nrpixels_z": 64, "im_trans": [2],
           "omega_ref_frame_idx": 0, "omega_ref_deg": -2.0, "omega_step_deg": 0.5,
           "omega_last_frame_idx": 7}
    return ga.PanelSnapshot(1, cfg, (64, 128), 8, 0, -2.0, 0.5, 7)


def _run(frames, mask=None, pitch=0.05, snap=None):
    snap = snap or _small_snap()
    spec = rv.auto_grid({1: snap}, pitch)
    src = {"snap": snap, "frames": lambda a, b: frames[a:b], "mask": mask, "n_frames": len(frames)}
    return rv.build_volume([src], spec, bg_stride=0), spec


def test_uniform_signal_stays_uniform_and_holes_are_unmeasured():
    frames = np.full((8, 64, 128), 5, dtype=np.uint32)
    vol, spec = _run(frames)
    cov = vol["count"] > 0
    assert cov.any() and not cov.all()                       # a swept patch, not the whole box
    np.testing.assert_allclose(rv.mean_field(vol)[cov], 5.0)  # overlaps don't change the mean
    np.testing.assert_allclose(vol["max"][cov], 5.0)
    assert np.isnan(vol["max"][~cov]).all() and np.isnan(rv.mean_field(vol)[~cov]).all()
    assert vol["count"].sum() == 8 * 64 * 128                 # every sample landed in the grid


def test_masked_and_sentinel_pixels_do_not_count_and_negatives_survive():
    frames = np.full((8, 64, 128), 10, dtype=np.uint32)
    frames[:, :, :10] = rv.SENTINEL_U32
    mask = np.zeros((64, 128), bool)
    mask[:5] = True
    vol, _ = _run(frames, mask=mask)
    assert vol["count"].sum() == 8 * (64 - 5) * (128 - 10)
    # background-subtracted negatives are kept in the stored sums
    snap = _small_snap()
    spec = rv.auto_grid({1: snap}, 0.05)
    dark = np.zeros((8, 64, 128), np.uint32)
    dark[0] = 0
    lo = np.full((8, 64, 128), 3, np.uint32)
    src = {"snap": snap, "frames": lambda a, b: lo[a:b], "mask": None, "n_frames": 8}
    out = rv.build_volume([src], spec, bg_stride=1)           # median bg == 3 -> signal 0
    assert np.allclose(out["sum"], 0.0)


def test_known_peak_lands_in_its_voxel():
    snap = _small_snap()
    frames = np.zeros((8, 64, 128), np.uint32)
    frames[3, 20, 90] = 777
    vol, spec = _run(frames, snap=snap)
    q_lab = ga.raw_pixels_to_qlab(snap, [20.0], [90.0])
    q_s = ga.qlab_to_sample(q_lab, float(snap.omega_of_loaded_frame(3)))[0]
    ix = np.floor((q_s - np.array(spec.origin)) / spec.spacing).astype(int)
    assert vol["max"][tuple(ix)] == 777
    assert np.nanmax(vol["max"]) == 777 and np.nansum(vol["max"] == 777) == 1


def test_accumulator_is_chunking_invariant():
    rng = np.random.default_rng(0)
    q = rng.uniform(-1, 1, (5000, 3))
    v = rng.normal(size=5000)
    ok = np.ones(5000, bool)
    spec = rv.GridSpec((-1.0, -1.0, -1.0), 0.1, (20, 20, 20))
    a, b = rv.VolumeAccumulator(spec), rv.VolumeAccumulator(spec)
    a.add(q, v, ok)
    for sl in np.array_split(np.arange(5000), 7):
        b.add(q[sl], v[sl], ok[sl])
    ra, rb = a.result(), b.result()
    np.testing.assert_allclose(ra["sum"], rb["sum"], atol=1e-3)
    np.testing.assert_array_equal(ra["count"], rb["count"])
    np.testing.assert_array_equal(np.nan_to_num(ra["max"]), np.nan_to_num(rb["max"]))


def test_axis_order_with_unequal_dims_and_offcenter_impulse():
    shape = (5, 9, 13)
    f = np.zeros(shape, np.float32)
    f[3, 7, 11] = 9.0
    cnt = np.ones(shape, np.float32)
    for ax, idx, want in ((2, 11, (3, 7)), (1, 7, (3, 11)), (0, 3, (7, 11))):
        img, _ = rv.axis_slab(f, cnt, ax, idx, 0, "max")
        assert np.argwhere(img == 9.0).tolist() == [list(want)]
    # slab never manufactures intensity in unmeasured voxels
    cnt[:, :, 5] = 0
    f[:, :, 5] = 123
    img, cov = rv.axis_slab(f, cnt, 2, 5, 0, "max")
    assert np.isnan(img).all() and (cov == 0).all()
    img, _ = rv.axis_slab(f, cnt, 2, 5, 1, "max")             # thicker slab uses only measured planes
    assert np.nanmax(img) < 123


def test_vtk_imagedata_voxel_centre_mapping():
    """The 3-D renderer places voxel (i,j,k) at origin + (idx+0.5)*pitch (no half-voxel shift)."""
    pv = pytest.importorskip("pyvista")
    spec = rv.GridSpec((-1.0, 2.0, 3.0), 0.5, (5, 9, 13))
    arr = np.zeros(spec.shape, np.float32)
    arr[3, 7, 11] = 1.0
    g = pv.ImageData(dimensions=spec.shape, origin=tuple(o + 0.5 * spec.spacing for o in spec.origin),
                     spacing=(spec.spacing,) * 3)
    g.point_data["v"] = arr.ravel(order="F")
    pt = g.points[int(np.argmax(g.point_data["v"]))]
    np.testing.assert_allclose(pt, [spec.origin[0] + 3.5 * 0.5, spec.origin[1] + 7.5 * 0.5, spec.origin[2] + 11.5 * 0.5])


def test_volume_product_roundtrip(tmp_path):
    from midas_gui import project
    from midas_gui.solve_cell import viz_products as vpx
    frames = np.full((8, 64, 128), 5, dtype=np.uint32)
    vol, spec = _run(frames)
    vol, spec = rv.crop_to_coverage(vol, spec)
    prod = vpx.volume_product(vol, spec, {"timestamp": "t"})
    path = tmp_path / "p.h5"
    project.create_project(path, "t")
    project.write_solve_cell_ingest(path, meta={"panels": {}}, spots_df=pd.DataFrame({"spot_id": [0]}))
    project.write_solve_cell_viz(path, "reciprocal_volume", meta=prod["meta"], arrays=prod["arrays"])
    got = project.read_solve_cell(path)["viz"]["reciprocal_volume"]
    v2, s2 = vpx.volume_from_product(got)
    assert s2.shape == spec.shape and s2.origin == pytest.approx(spec.origin)
    np.testing.assert_array_equal(v2["count"], vol["count"])
    np.testing.assert_allclose(v2["sum"], vol["sum"])


# ── contamination fingerprints (V12) ─────────────────────────────────────────
def test_diamond_line_table_matches_pipeline_and_labels_lowest_hkl():
    from midas_gui.solve_cell import pipeline
    tab = vp.diamond_line_table(WL, 3.5667)
    ref = pipeline._diamond_tth_lines_deg(WL, 3.5667)
    np.testing.assert_allclose(tab.tth_deg.to_numpy(), ref)
    assert tab.hkl.tolist()[:4] == ["111", "220", "311", "400"]       # (200),(222) are forbidden
    assert (tab.q * tab.d_A / (2 * np.pi)).round(9).eq(1.0).all()


def test_flag_preview_reports_exactly_the_ids_that_flip_and_changes_nothing():
    lines = vp.diamond_line_table(WL, 3.5667).tth_deg.to_numpy()
    spots = pd.DataFrame({"spot_id": [10, 11, 12], "two_theta_deg": [lines[1] + 0.01, lines[1] + 0.05, lines[2] + 0.5]})
    before = spots.copy()
    tight, loose = vp.flag_preview(spots, lines, 0.02), vp.flag_preview(spots, lines, 0.07)
    assert tight.tolist() == [True, False, False] and loose.tolist() == [True, True, False]
    assert spots.equals(before)


# ── domain comparison (V13) ──────────────────────────────────────────────────
from midas_gui.solve_cell import domain_compare as dc

_FCC_PRIM = np.array([[0, 1, 1], [1, 0, 1], [1, 1, 0]], float).T * 0.5 * 7.95   # direct basis columns


def _ub(A):                      # direct columns -> UB (1/d): UB = inv(A).T
    return np.linalg.inv(A).T


def _rot(axis, deg):
    ax = np.asarray(axis, float); ax /= np.linalg.norm(ax)
    K = np.array([[0, -ax[2], ax[1]], [ax[2], 0, -ax[0]], [-ax[1], ax[0], 0]])
    r = np.radians(deg)
    return np.eye(3) + np.sin(r) * K + (1 - np.cos(r)) * K @ K


def test_relabeled_copy_is_not_a_new_crystal():
    UB = _ub(_FCC_PRIM)
    M = np.array([[1, 1, 0], [0, 1, 0], [0, 1, 1]], float)           # det 1 change of basis
    assert round(np.linalg.det(M)) == 1
    UB2 = _ub(_FCC_PRIM @ M)                                           # same lattice, other primitive setting
    c = dc.compare_lattices(UB, UB2)
    assert c["metric_match"] and c["angle_deg"] < 1e-6
    assert dc.conclude(c, 30, 30) == "same lattice relabeled"
    # a pure 90 deg cubic rotation is a lattice symmetry: still the same lattice, by construction
    assert dc.compare_lattices(UB, _rot([0, 0, 1], 90) @ UB)["angle_deg"] < 1e-6


def test_rotated_sibling_stays_distinct_with_its_angle():
    UB = _ub(_FCC_PRIM)
    for ang in (5.0, 20.0, 37.0):
        c = dc.compare_lattices(UB, _rot([1, 2, 3], ang) @ UB)
        assert c["angle_deg"] == pytest.approx(ang, abs=1e-6)
        assert dc.conclude(c, 30, 30) == "compatible distinct orientation"
    assert c["n_matches"] == 24                                      # proper cubic holohedry


def test_different_metric_and_insufficient_evidence_and_handedness():
    UB = _ub(_FCC_PRIM)
    other = _ub(_FCC_PRIM * np.array([1.0, 1.15, 1.0]))               # 15% stretch of one axis
    c = dc.compare_lattices(UB, other)
    assert not c["metric_match"] and dc.conclude(c, 30, 30) == "different metric"
    assert dc.conclude(dc.compare_lattices(UB, UB), 3, 30) == "insufficient evidence"
    # left-handed UB (improper input) must not yield an improper rotation
    R = dc.polar_rotation(np.diag([1.0, 1.0, -1.0]) @ _rot([0, 1, 0], 10))
    assert np.linalg.det(R) == pytest.approx(1.0)


def test_compare_all_tables_and_cross_fit_evidence():
    UB = _ub(_FCC_PRIM)
    hkl = np.array([[1, 1, 1], [2, 0, 0], [0, 2, 2], [1, -1, 3]], float)
    UBr = _rot([1, 2, 3], 25) @ UB
    g = np.vstack([(UB @ hkl[:2].T).T, (UBr @ hkl[2:].T).T]) * 2 * np.pi   # spots 0,1 from A; 2,3 from B
    cand = pd.DataFrame({"qsample_x": g[:, 0], "qsample_y": g[:, 1], "qsample_z": g[:, 2], "panel_id": 1})
    mk = lambda key, UBx, idx: {"key": key, "label": key, "kind": "domain", "method": "m", "UB": UBx, "cell": None,
                                "cell_sigma": None, "n_reflections": 40, "claimed_index": np.array(idx),
                                "panel_counts": {1: len(idx)}}
    entries = [mk("A", UB, [0, 1]), mk("B", UBr, [2, 3])]
    t = dc.compare_all(entries, cand)
    p = t["pairs"].iloc[0]
    assert p.conclusion == "compatible distinct orientation" and p.angle_deg == pytest.approx(25.0, abs=1e-6)
    assert p.b_spots_fit_by_a + p.a_spots_fit_by_b < 4                 # a rotated lattice does not explain the other's spots (chance hits possible)
    # a lattice that really is the same explains the other's spots
    cand2 = cand.copy()
    cand2.iloc[2:, :3] = ((UB @ hkl[2:].T).T * 2 * np.pi)
    same = dc.compare_all([mk("A", UB, [0, 1]), mk("C", UB, [2, 3])], cand2)["pairs"].iloc[0]
    assert same.b_spots_fit_by_a == 2 and same.conclusion == "same lattice relabeled"


def test_comparison_product_roundtrip_with_empty_review(tmp_path):
    from midas_gui import project
    path = tmp_path / "p.h5"
    project.create_project(path, "t")
    project.write_solve_cell_ingest(path, meta={"panels": {}}, spots_df=pd.DataFrame({"spot_id": [0]}))
    empty = pd.DataFrame([], columns=["a", "b", "fp_a", "fp_b", "conclusion", "reviewed_utc"])
    project.write_solve_cell_viz(path, "domain_review", meta={}, tables={"reviews": empty})
    assert len(project.read_solve_cell(path)["viz"]["domain_review"]["tables"]["reviews"]) == 0
    full = empty.copy()
    full.loc[0] = ["A", "B", "f1", "f2", "same lattice relabeled", "2026-10-24T00:00:00"]
    project.write_solve_cell_viz(path, "domain_review", meta={}, tables={"reviews": full})
    assert project.read_solve_cell(path)["viz"]["domain_review"]["tables"]["reviews"].conclusion[0] == "same lattice relabeled"


# ── frame patches (V02) ──────────────────────────────────────────────────────
from midas_gui.solve_cell import frame_patch as fpatch


def test_patch_corners_are_pixel_edges_and_on_the_ewald_sphere():
    snap = _small_snap()                                   # 64 x 128, im_trans [2]
    stride = 8
    qc = fpatch.corner_qlab(snap, stride)
    assert qc.shape == (64 // stride + 1, 128 // stride + 1, 3)
    # corner (0,0) is the pixel EDGE (-0.5, -0.5), not the centre of pixel (0,0)
    np.testing.assert_allclose(qc[0, 0], ga.raw_pixels_to_qlab(snap, [-0.5], [-0.5])[0], atol=1e-12)
    np.testing.assert_allclose(qc[-1, -1], ga.raw_pixels_to_qlab(snap, [63.5], [127.5])[0], atol=1e-12)
    k0 = 2 * np.pi / WL
    for om in (-1.0, 0.7):
        qs = fpatch.corners_at_omega(qc, om)
        np.testing.assert_allclose(np.linalg.norm(qs + ga.qlab_to_sample(np.array([[k0, 0, 0]]), om)[0], axis=2), k0, atol=1e-8)
        # |q| is independent of omega
        np.testing.assert_allclose(np.linalg.norm(qs, axis=2), np.linalg.norm(qc, axis=2), atol=1e-9)


def test_cell_signal_blocks_masks_and_sentinels_asymmetric():
    stride = 8
    f = np.zeros((64, 128), np.uint32)
    f[10, 100] = 500                                       # lands in cell (1, 12)
    f[40:48, 8:16] = fpatch.SENTINEL_U32                   # one whole cell of sentinels -> missing
    mask = np.zeros((64, 128), bool)
    mask[:8, :8] = True                                    # cell (0, 0) fully masked -> missing
    mask[56, 120] = True                                   # a single masked pixel does NOT blank its cell
    cells = fpatch.cell_signal(f, None, mask, stride)
    assert cells.shape == (8, 16)
    assert cells[1, 12] == 500 and np.isnan(cells[0, 0]) and np.isnan(cells[5, 1])
    assert cells[7, 15] == 0 and np.isfinite(cells[7, 15])  # partially masked cell is measured, zero != missing
    assert int(np.isnan(cells).sum()) == 2
    # negative background-subtracted values survive
    assert np.nanmin(fpatch.cell_signal(np.zeros((64, 128), np.uint32), np.full((64, 128), 4.0, np.float32), None, stride)) == -4.0
    # non-divisible shape: the last block is short but still covered
    c2 = fpatch.cell_signal(np.ones((20, 30), np.float32), None, None, 8)
    assert c2.shape == (3, 4) and np.isfinite(c2).all()


def test_pixel_cell_roundtrip_and_structured_grid_cell_order():
    snap = _small_snap()
    stride = 8
    for (r, c) in [(0, 0), (9, 100), (63, 127), (40.4, 15.6)]:
        i, j = fpatch.pixel_to_cell(snap, stride, r, c)
        r0, r1, c0, c1 = fpatch.cell_pixel_ranges(snap, stride, i, j)
        assert r0 <= round(r) < r1 and c0 <= round(c) < c1
    pv = pytest.importorskip("pyvista")
    qs = fpatch.corners_at_omega(fpatch.corner_qlab(snap, stride), 0.0)
    grid = pv.StructuredGrid(qs[..., 0], qs[..., 1], qs[..., 2])
    vals = np.arange(8 * 16, dtype=float).reshape(8, 16)
    grid.cell_data["v"] = vals.ravel(order="F")           # same ordering the renderer uses
    cid = int(np.flatnonzero(grid.cell_data["v"] == vals[3, 11])[0])
    ctr = grid.cell_centers().points[cid]
    expect = qs[3:5, 11:13].reshape(-1, 3).mean(axis=0)   # mean of cell (3,11)'s 4 corners
    np.testing.assert_allclose(ctr, expect, atol=1e-9)


def test_impulse_cell_sits_at_its_pixels_q_and_omega_to_frame_mapping():
    snap = _small_snap()
    stride = 8
    f = np.zeros((64, 128), np.uint32)
    f[20, 90] = 777
    cells = fpatch.cell_signal(f, None, None, stride)
    i, j = map(int, np.argwhere(cells == 777)[0])
    om = float(snap.omega_of_loaded_frame(3))
    qs = fpatch.corners_at_omega(fpatch.corner_qlab(snap, stride), om)
    centre = qs[i:i + 2, j:j + 2].reshape(-1, 3).mean(axis=0)
    q_pix = ga.qlab_to_sample(ga.raw_pixels_to_qlab(snap, [20.0], [90.0]), om)[0]
    assert np.linalg.norm(centre - q_pix) < 0.01 * stride * 1.5   # within a cell (0.007 A^-1 per px)
    assert fpatch.loaded_frame_for_omega(snap, om) == 3
    assert fpatch.loaded_frame_for_omega(snap, -50.0) is None and fpatch.loaded_frame_for_omega(snap, 50.0) is None
