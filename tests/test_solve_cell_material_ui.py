"""Qt tests for the Solve Cell material-filter panel and its wiring into the tab.

Forked + deferred Qt imports, same rules as tests/test_solve_cell_ui.py."""
import pytest

pytestmark = pytest.mark.forked


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def panel(app):
    from midas_gui.solve_cell_material_panel import MaterialFilterPanel
    p = MaterialFilterPanel()
    yield p
    p.deleteLater(); app.processEvents()


@pytest.fixture
def tab(app):
    from midas_gui.tab_solve_cell import SolveCellTab
    t = SolveCellTab()
    yield t
    t.deleteLater(); app.processEvents()


def test_default_panel_is_on_with_a_diamond_entry(panel):
    mats = panel.materials()
    assert panel.filter_enabled() and not panel.body.isHidden()
    assert [m["name"] for m in mats] == ["Diamond"]
    assert mats[0]["cell"][0] == pytest.approx(3.5667) and mats[0]["space_group"] == 227
    assert mats[0]["mode"] == "2theta" and mats[0]["tol_deg"] == pytest.approx(0.07)


def test_unchecking_hides_all_options_and_reports_disabled(panel):
    seen = []
    panel.enabledChanged.connect(seen.append)
    panel.enable_cb.setChecked(False)
    assert panel.body.isHidden() and not panel.filter_enabled() and seen == [False]
    panel.enable_cb.setChecked(True)
    assert not panel.body.isHidden() and seen == [False, True]


def test_add_preset_custom_edit_and_remove(panel):
    panel._add_preset("Rhenium (gasket)")
    panel._add_custom()
    assert [m["name"] for m in panel.materials()] == ["Diamond", "Rhenium", "Custom"]
    panel._add_custom()
    assert panel.materials()[-1]["name"] == "Custom 2"                  # names stay unique
    # edit the custom entry (row 2): lattice, space group, tolerance, name
    panel.list.setCurrentRow(2)
    panel.name_ed.setText("Pt foil"); panel.name_ed.textEdited.emit("Pt foil")
    panel.cell[0].setValue(3.9242); panel.cell[1].setValue(3.9242); panel.cell[2].setValue(3.9242)
    panel.sg.setValue(225); panel.tol.setValue(0.12)
    m = panel.materials()[2]
    assert m["name"] == "Pt foil" and m["cell"][:3] == pytest.approx([3.9242] * 3)
    assert m["space_group"] == 225 and m["tol_deg"] == pytest.approx(0.12)
    assert panel.list.item(2).text() == "Pt foil"
    # editing one material leaves the others alone
    assert panel.materials()[0]["tol_deg"] == pytest.approx(0.07)
    panel.list.setCurrentRow(1)
    panel._remove()
    assert [m["name"] for m in panel.materials()] == ["Diamond", "Pt foil", "Custom 2"]


def test_untick_a_material_and_orientation_controls_appear_only_in_that_mode(panel):
    from PyQt5 import QtCore
    panel.list.item(0).setCheckState(QtCore.Qt.Unchecked)
    assert panel.materials()[0]["enabled"] is False
    assert panel.orient_box.isHidden()
    panel.mode.setCurrentIndex(panel.mode.findData("orientation"))
    assert not panel.orient_box.isHidden() and panel.materials()[0]["mode"] == "orientation"
    panel.orient_tol.setValue(0.8); panel.min_spots.setValue(4); panel.max_crystals.setValue(3)
    m = panel.materials()[0]
    assert (m["orient_tol_deg"], m["min_spots"], m["max_crystals"]) == (pytest.approx(0.8), 4, 3)
    assert panel.protect_tol.isHidden()
    panel.protect.setChecked(True)
    assert not panel.protect_tol.isHidden() and panel.materials()[0]["protect_if_indexed"] is True


def test_state_round_trip_and_tolerance_to_bad_entries(panel, app):
    from midas_gui.solve_cell_material_panel import MaterialFilterPanel
    panel._add_preset("Tungsten (gasket)")
    panel.n_mc.setValue(5000); panel.seed.setValue(7); panel.set_filter_enabled(False)
    st = panel.get_state()
    other = MaterialFilterPanel()
    other.set_state({**st, "materials": st["materials"] + [{"cell": [1, 2]}, {"mode": "nonsense"}]})   # junk is skipped
    assert other.get_state() == st
    assert not other.filter_enabled() and other.n_mc_value() == 5000 and other.seed_value() == 7
    other.deleteLater(); app.processEvents()


def test_tab_button_follows_the_checkbox_and_filter_off_uses_all_spots(tab):
    import numpy as np, pandas as pd
    assert tab._diamond_btn.text() == "Run Material Filter"
    tab._spots_df = pd.DataFrame({"spot_id": np.arange(5), "panel_id": 1, "two_theta_deg": np.linspace(3, 9, 5),
                                  "qsample_x": 1.0, "qsample_y": 2.0, "qsample_z": 3.0, "peak_counts": 10.0})
    tab._material_panel.set_filter_enabled(False)          # also builds the pass-through pool
    assert tab._diamond_btn.text() == "Use all spots"
    assert len(tab._candidate_df) == 5 and not tab._flagged_df["is_diamond"].any()
    assert tab._ab_btn.isEnabled() and "Filter off" in tab._diamond_status.text()
    # a recorded summary says it was off, and indexing is never blocked on the skipped stage
    assert tab._sc_stage_meta["diamond"]["summary"]["enabled"] is False
    tab._material_panel.set_filter_enabled(True)
    assert tab._diamond_btn.text() == "Run Material Filter"


def test_filter_needs_a_ticked_material_when_on(tab, monkeypatch):
    import numpy as np, pandas as pd
    msgs = []
    monkeypatch.setattr(tab, "_warn_or_record", lambda title, text, **k: msgs.append(title))
    tab._spots_df = pd.DataFrame({"two_theta_deg": [1.0]})
    from PyQt5 import QtCore
    tab._material_panel.list.item(0).setCheckState(QtCore.Qt.Unchecked)
    tab._run_diamond_filter()
    assert msgs == ["No materials selected"]


def test_stage_fields_round_trip_and_old_projects_convert(tab, app):
    from midas_gui.tab_solve_cell import SolveCellTab
    tab._material_panel._add_preset("Gold (marker)")
    fields = tab._stage_fields()
    assert [m["name"] for m in fields["material_filter"]["materials"]] == ["Diamond", "Gold"]
    fresh = SolveCellTab()
    fresh._apply_stage_fields(fields)
    assert [m["name"] for m in fresh._material_panel.materials()] == ["Diamond", "Gold"]
    # a project written before the multi-material filter had diamond_a / contam_tol widgets
    fresh._apply_stage_fields({"diamond_a": 3.6, "contam_tol": 0.11})
    mats = fresh._material_panel.materials()
    assert len(mats) == 1 and mats[0]["cell"][0] == pytest.approx(3.6) and mats[0]["tol_deg"] == pytest.approx(0.11)
    fresh.deleteLater(); app.processEvents()


def _crystal_candidates(n_sample=8):
    """Spots of one cubic crystal (a = 5 A, g = 2*pi*UB@hkl) + the integer hkl."""
    import itertools, numpy as np, pandas as pd
    UB = np.eye(3) / 5.0
    hkl = np.array([h for h in itertools.product(range(-2, 3), repeat=3) if any(h)][:n_sample], float)
    g = 2 * np.pi * (UB @ hkl.T).T
    df = pd.DataFrame({"spot_id": np.arange(100, 100 + len(g)), "panel_id": 1,
                       "qsample_x": g[:, 0], "qsample_y": g[:, 1], "qsample_z": g[:, 2]})
    return df, hkl, UB


def test_refilter_keeps_the_solved_domain_and_adds_the_spots_that_index_on_it(tab):
    import numpy as np, pandas as pd
    from midas_gui.solve_cell import pipeline as P
    from midas_gui.solve_cell import material_filter as mf
    allspots, hkl, UB = _crystal_candidates(9)
    old_candidate = allspots.iloc[:8].reset_index(drop=True)            # spot 108 was flagged the first time
    tab._candidate_df = old_candidate
    res = P.refit_domain(hkl[:8], old_candidate[["qsample_x", "qsample_y", "qsample_z"]].to_numpy())
    xyz = tuple(old_candidate[c].to_numpy() for c in ("qsample_x", "qsample_y", "qsample_z"))
    tab._add_domain("free (ab-initio)", res, old_candidate.index, xyz, old_candidate["panel_id"].to_numpy(), round1=True)
    assert len(tab._domains[0]["claimed_index"]) == 8

    # re-filter: spot 108 is now rescued; spot 100 is dropped-from-pool order shuffled to test label remapping
    flagged = allspots.assign(is_diamond=False, protected_by=0, contaminant_id=-1, delta_to_diamond_deg=0.5)
    flagged.loc[flagged.spot_id == 108, "protected_by"] = 1
    flagged = flagged.iloc[::-1].reset_index(drop=True)                 # a different row order than before
    cand = flagged[~flagged["is_diamond"]].reset_index(drop=True)
    dia = mf.preset("Diamond", protect_if_indexed=True).to_dict()
    tab._on_diamond_done({"candidate_df": cand, "flagged_df": flagged,
                          "summary": {"enabled": True, "n_flagged": 0, "n_spots": 9, "n_kept": 9, "n_protected": 1,
                                      "materials": [dia]}})
    assert tab._domain_list.count() == 1
    d = tab._domains[0]
    ids = sorted(int(i) for i in cand.loc[d["claimed_index"], "spot_id"])
    assert ids == list(range(100, 109))                                   # same 8 spots + the rescued one
    assert d["result"]["n_reflections"] == 9
    assert d["result"]["cell"][0] == pytest.approx(5.0, abs=1e-3)
    assert len(d["xyz"][0]) == 9 and tab._claimed_index.equals(d["claimed_index"])
    assert "kept: they index on a solved domain" in tab._diamond_status.text()


def test_refilter_without_protection_still_clears_domains(tab):
    from midas_gui.solve_cell import pipeline as P
    from midas_gui.solve_cell import material_filter as mf
    allspots, hkl, _ = _crystal_candidates(8)
    tab._candidate_df = allspots
    res = P.refit_domain(hkl, allspots[["qsample_x", "qsample_y", "qsample_z"]].to_numpy())
    xyz = tuple(allspots[c].to_numpy() for c in ("qsample_x", "qsample_y", "qsample_z"))
    tab._add_domain("free (ab-initio)", res, allspots.index, xyz, None, round1=True)
    flagged = allspots.assign(is_diamond=False)
    tab._on_diamond_done({"candidate_df": allspots, "flagged_df": flagged,
                          "summary": {"enabled": True, "n_flagged": 0, "n_spots": 8, "n_kept": 8,
                                      "materials": [mf.preset("Diamond").to_dict()]}})
    assert tab._domain_list.count() == 0 and tab._claimed_index is None


def test_contamination_view_draws_lines_of_every_enabled_material(tab):
    import numpy as np, pandas as pd
    from midas_gui.solve_cell import material_filter as mf
    spots = pd.DataFrame({"spot_id": [0, 1, 2], "panel_id": 1, "two_theta_deg": [5.0, 7.0, 9.0],
                          "omega_deg": 0.0, "is_diamond": [True, False, False],
                          "delta_to_diamond_deg": [0.01, 0.4, 0.9]})
    au = mf.preset("Gold (marker)"); off = mf.preset("MgO (medium)"); off.enabled = False
    tab._contam_view.set_data(spots, 0.49594, materials=[mf.preset("Diamond").to_dict(), au.to_dict(), off.to_dict()])
    assert set(tab._contam_view.lines["material"]) == {"Diamond", "Gold"}
    tab._contam_view.select_spot(0)
    assert "Diamond" in tab._contam_view.evidence.text() or "Gold" in tab._contam_view.evidence.text()
    tab._contam_view.set_data(None, 0.49594, materials=[])             # filter off: nothing to draw, no error
    assert tab._contam_view.lines is None
