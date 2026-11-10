"""Overlays + Diagnostics view of the Solve Cell tab against the local real
spinel project (skipped where that gitignored file is absent). Forked, see
test_solve_cell_ui.py for why."""
from pathlib import Path

import pytest

pytestmark = pytest.mark.forked
PROJECT = Path(__file__).resolve().parents[1] / "test_data" / "projects" / "s3idc_oct8_spinel.h5"


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def tab(app):
    from midas_gui.tab_solve_cell import SolveCellTab
    t = SolveCellTab()
    yield t
    t.deleteLater()
    app.processEvents()


@pytest.mark.skipif(not PROJECT.exists(), reason="local spinel project not present")
def test_overlays_and_diagnostics_from_cached_viz(tab):
    from midas_gui import project
    rec = project.read_solve_cell(PROJECT)
    if not rec.get("viz"):
        pytest.skip("project has no cached viz products")
    tab.apply_project_solve_cell(rec)
    assert "domain_01_predictions" in tab._viz_products
    assert not tab._viz_unsaved                       # cache hit, nothing recomputed
    for w in (tab._ov_ewald, tab._ov_basis, tab._ov_pred):
        w.setChecked(True)
    assert {"basis", "ewald"} <= set(tab._recip_view.overlay_names())
    assert tab._lattice_rows and all(r["spot_id"] in tab._spot_rows_by_id for r in tab._lattice_rows)
    tab._ov_pred.setChecked(False)
    assert tab._lattice_rows is None and not any(str(k).startswith("lp:") for k in tab._spot_rows_by_id)
    # reference overlays must never be pickable
    for actors in tab._recip_view._overlay_actors.values():
        assert all(not a.GetPickable() for a in actors)
    tab._view_tabs.setCurrentWidget(tab._diag_view)
    plots = tab._diag_view.plots
    assert {"vectors_1", "vectors_3", "vectors_4", "timeline", "coverage"} <= set(plots)
    assert "74 matched" in tab._diag_view.status.text()
    tab._recip_view.interactor.close()


@pytest.mark.skipif(not PROJECT.exists(), reason="local spinel project not present")
def test_volume_tab_restores_cached_volume_and_links_spots(tab):
    import numpy as np
    from midas_gui import project
    rec = project.read_solve_cell(PROJECT)
    if "reciprocal_volume" not in (rec.get("viz") or {}):
        pytest.skip("project has no cached reciprocal volume")
    tab.apply_project_solve_cell(rec)
    v = tab._volume_view
    assert v.vol is not None and v.spec.spacing == pytest.approx(0.06)
    # the brightest voxel's slab contains the brightest peak AND a stable-id centroid marker
    i = np.unravel_index(np.nanargmax(v.vol["max"]), v.vol["max"].shape)
    v.axis.setCurrentIndex(2)
    v.slice_slider.setValue(int(i[2]))
    disp, raw, cov = v.slab_image()
    assert np.nanmax(raw) == np.nanmax(v.vol["max"])
    assert (cov == 0).any() and np.isnan(raw[cov == 0]).all()      # unmeasured stays NaN, not zero
    ids = [d for d in v.spot_item.data["data"]]
    assert ids and all(isinstance(int(x), int) for x in ids)
    assert "iso" in v._iso_actors and "plane" in v._iso_actors
    assert all(not a.GetPickable() for a in v._iso_actors.values())
    v.iv.close()
    tab._recip_view.interactor.close()


@pytest.mark.skipif(not PROJECT.exists(), reason="local spinel project not present")
def test_spot_inspector_from_cached_shoebox(tab):
    import numpy as np
    from midas_gui import project
    rec = project.read_solve_cell(PROJECT)
    if "spot_shoeboxes" not in (rec.get("viz") or {}):
        pytest.skip("project has no cached shoeboxes")
    tab.apply_project_solve_cell(rec)
    tab._select_spot(1)
    ins = tab._inspector
    assert {"roi", "om_row", "om_col", "rocking", "profile_col", "profile_row"} <= set(ins.plots)
    k = ins._k
    cube = ins._cubes["bgsub"][k]
    row = tab._spot_rows_by_id[1]
    ix = ins._index.iloc[k]
    # ROI geometry is in original pixel coordinates: the centroid sits inside the cube and
    # the brightest pixel lies within a pixel or two of it
    assert ix.row0 <= row["row"] < ix.row0 + cube.shape[1] and ix.col0 <= row["col"] < ix.col0 + cube.shape[2]
    f, r, c = np.unravel_index(np.nanargmax(cube), cube.shape)
    assert abs((ix.row0 + r) - row["row"]) <= 2 and abs((ix.col0 + c) - row["col"]) <= 2
    assert abs(int(ix.frame0) + f - row["loaded_frame_idx"]) <= 1
    assert "predicted" in ins.info.text()       # residual from the cached domain product
    tab._recip_view.interactor.close(); tab._volume_view.iv.close()


@pytest.mark.skipif(not PROJECT.exists(), reason="local spinel project not present")
def test_compare_tab_uses_cache_keeps_claims_and_records_review(tab):
    from midas_gui import project
    rec = project.read_solve_cell(PROJECT)
    if "domain_comparison" not in (rec.get("viz") or {}):
        pytest.skip("project has no cached comparison")
    tab.apply_project_solve_cell(rec)
    claims_before = [d["claimed_index"].tolist() for d in tab._domains]
    tab._view_tabs.setCurrentWidget(tab._compare_view)
    c = tab._compare_view
    assert [e["key"] for e in c.entries] == ["domain_01", "ab_initio_seed"]
    assert not tab._viz_unsaved or tab._viz_unsaved == {"domain_comparison"} - {"domain_comparison"}  # cache hit
    assert c.tables["pairs"].iloc[0].conclusion == "same lattice relabeled"
    assert len(c.view_a.groups) == 3 and len(c.view_b.groups) == 3     # one group per panel, both scenes
    c.pair_table.cellWidget(0, 8).setCurrentIndex(2)                   # reviewer: compatible distinct orientation
    rv = tab._viz_products["domain_review"]["tables"]["reviews"]
    assert rv.iloc[0].conclusion == "compatible distinct orientation" and rv.iloc[0].a == "domain_01"
    assert [d["claimed_index"].tolist() for d in tab._domains] == claims_before   # claims untouched
    tab._recip_view.interactor.close(); tab._volume_view.iv.close()
    c.view_a.interactor.close(); c.view_b.interactor.close()


@pytest.mark.skipif(not PROJECT.exists(), reason="local spinel project not present")
def test_frame_patch_overlay_coincides_with_selected_spot(tab):
    import numpy as np
    from midas_gui import project
    from midas_gui.solve_cell import frame_patch as fp
    rec = project.read_solve_cell(PROJECT)
    if "frame_patches" not in (rec.get("viz") or {}):
        pytest.skip("project has no cached frame patches")
    tab.apply_project_solve_cell(rec)
    tab._ov_patch.setChecked(True)
    tab._select_spot(1)                                    # panel 1, omega -25.7254
    assert tab._ov_omega.value() == -257
    assert "patches" in tab._recip_view.overlay_names()
    assert all(not a.GetPickable() for a in tab._recip_view._overlay_actors["patches"])
    row = tab._spot_rows_by_id[1]
    snap = tab._ingest_snapshots()[int(row["panel_id"])]
    prod = tab._viz_products["frame_patches"]
    stride = prod["meta"]["stride"]
    f = fp.loaded_frame_for_omega(snap, row["omega_deg"])
    slot = int(np.searchsorted(prod["arrays"][f"loaded_idx_p{int(row['panel_id'])}"], f))
    cells = prod["arrays"][f"cells_p{int(row['panel_id'])}"][slot]
    i, j = fp.pixel_to_cell(snap, stride, row["row"], row["col"])
    # the cell under the centroid holds the spot's signal (>> noise), within a frame of the centroid's omega
    around = cells[max(i - 1, 0):i + 2, max(j - 1, 0):j + 2]
    assert np.nanmax(around) > 100 * max(np.nanmedian(np.abs(cells)), 1.0)
    # and the centroid's own q lies inside that cell's corner box at that frame's omega
    qc = fp.corners_at_omega(fp.corner_qlab(snap, stride), float(snap.omega_of_loaded_frame(f)))
    corners = qc[i:i + 2, j:j + 2].reshape(-1, 3)
    q = np.array([row["qsample_x"], row["qsample_y"], row["qsample_z"]])
    assert np.linalg.norm(q - corners.mean(axis=0)) < 0.02 * stride
    tab._recip_view.interactor.close(); tab._volume_view.iv.close()


def test_reciprocal_pin_view_is_orthogonal_parallel_and_released_by_beam_view(tab):
    import numpy as np
    view = tab._recip_view
    tab._recip_pin.setCurrentIndex(4)            # -Y (X-Z plane)
    pos, fp, up = view.camera_position
    d = np.array(pos) - np.array(fp)
    assert np.allclose(d / np.linalg.norm(d), [0, -1, 0])
    assert tuple(up) == (0.0, 0.0, 1.0)
    assert view.interactor.camera.GetParallelProjection() and view.interactor.pinned
    tab._on_beam_view_clicked()
    assert view.pinned_view is None and tab._recip_pin.currentIndex() == 0
    assert not view.interactor.camera.GetParallelProjection()


@pytest.mark.skipif(not PROJECT.exists(), reason="local spinel project not present")
def test_save_as_keeps_every_record_section_and_viz_product(tab, tmp_path):
    """Restore -> flush into a different project file -> read back: ingest,
    diamond, index and every cached viz product must survive (the ingest swap
    replaces the whole record, viz/ included)."""
    from types import SimpleNamespace
    from midas_gui import project
    rec = project.read_solve_cell(PROJECT)
    tab.apply_project_solve_cell(rec)
    dst = tmp_path / "saved_as.h5"
    project.create_project(str(dst))
    tab._project_ctx = SimpleNamespace(path=str(dst))
    tab.flush_project_record()
    back = project.read_solve_cell(dst)
    assert back is not None and back["last_stage"] == rec["last_stage"]
    assert set(back["viz"] or {}) == set(rec["viz"] or {})
    assert len(back["index"]["domains"]) == len(rec["index"]["domains"])
    assert len(back["ingest"]["spots_df"]) == len(rec["ingest"]["spots_df"])
    # computed after the last write, with nothing dirty: still stored on the next flush
    tab._viz_products["extra"] = {"meta": {"inputs": {}}, "tables": {}, "arrays": {}}
    tab._viz_unsaved.add("extra")
    tab.flush_project_record()
    assert "extra" in project.read_solve_cell(dst)["viz"]
