"""Delete / hide / re-assign of Solve Cell domains, through the real tab."""
import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.forked


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _result(a=5.0):
    return {"cell": [a, a, a, 90.0, 90.0, 90.0], "cell_sigma": [0.01] * 6, "conventional_cell": [a] * 3 + [90.0] * 3,
            "conventional_system": "cubic", "holohedry_system": "cubic", "holohedry_order": 48,
            "rms_drlv": 0.02, "n_reflections": 5}


@pytest.fixture
def tab(app):
    from midas_gui.tab_solve_cell import SolveCellTab
    t = SolveCellTab()
    t._agent_mode = True                      # no confirmation dialogs
    n = 12
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"qsample_x": rng.normal(size=n), "qsample_y": rng.normal(size=n),
                       "qsample_z": rng.normal(size=n), "spot_id": np.arange(n), "panel_id": 1,
                       "row": 10.0 * np.arange(n), "col": 5.0 * np.arange(n), "omega_deg": np.arange(n) * 1.0})
    t._candidate_df = df
    xyz = lambda ix: tuple(df.loc[ix, c].to_numpy() for c in ("qsample_x", "qsample_y", "qsample_z"))
    t._add_domain("free (ab-initio)", _result(), df.index[:4], xyz(df.index[:4]), round1=True)
    t._add_domain("free (ab-initio)", _result(6.0), df.index[4:8], xyz(df.index[4:8]))
    t._add_domain("free (ab-initio)", _result(7.0), df.index[8:11], xyz(df.index[8:11]))
    yield t
    t._cell_view.shutdown()
    t.deleteLater()
    app.processEvents()


def test_delete_releases_spots_and_renumbers_state(tab):
    assert tab._domain_list.count() == 3 and len(tab._leftover_df()) == 1
    tab._delete_domain(1)
    assert tab._domain_list.count() == 2 and len(tab._domains) == 2
    assert [d["result"]["cell"][0] for d in tab._domains] == [5.0, 7.0]
    assert len(tab._leftover_df()) == 5                        # spots 4-7 are unassigned again
    assert tab._round1_domain_index == 0                       # unaffected
    tab._delete_domain(0)                                      # the round-1 domain
    assert tab._round1_domain_index is None and len(tab._domains) == 1
    tab._delete_domain(0)
    assert tab._domains == [] and tab._claimed_index is None and tab._domain_list.count() == 0
    assert len(tab._leftover_df()) == 12


def test_hide_hides_spots_everywhere_but_keeps_them_assigned(tab):
    ids = set(tab._domains[0]["claimed_index"])
    n_all = len(tab._display_spot_rows())
    tab._set_domain_visibility({1, 2})                         # hide domain 1 only
    assert tab._domains[0]["hidden"] and not tab._domains[1]["hidden"]
    shown = {r["spot_id"] for r in tab._display_spot_rows()}
    assert not (ids & shown) and len(shown) == n_all - 4
    assert ids <= {r["spot_id"] for r in tab._display_spot_rows(include_hidden=True)}
    assert tab._hidden_spot_ids() == ids
    assert len(tab._leftover_df()) == 1                        # hidden is not unassigned
    # via the checkbox, one domain at a time
    item = tab._domain_list.item(1)
    item.setCheckState(0)
    assert tab._domains[1]["hidden"]
    item.setCheckState(2)
    assert not tab._domains[1]["hidden"]
    tab._set_domain_visibility({0, 1, 2})
    assert not tab._hidden_spot_ids()


def test_hidden_flag_survives_replace_and_is_persisted_shape(tab):
    tab._set_domain_visibility({0, 2})
    d = tab._domains[1]
    tab._replace_domain(1, d["method"], _result(6.1), d["claimed_index"], d["xyz"], None)
    assert tab._domains[1]["hidden"]
    assert tab._domain_list.item(1).checkState() == 0
    assert all(isinstance(x.get("hidden"), bool) for x in tab._domains)


# ── phase identification / panel alignment wiring ────────────────────────────

def _aligned_tab(app):
    from midas_gui.solve_cell.geometry_adapter import qlab_to_sample
    from midas_gui.tab_solve_cell import SolveCellTab
    from midas_gui.solve_cell.pipeline import _bmatrix_from_cell
    t = SolveCellTab()
    t._agent_mode = True
    t._add_panel()                                           # panels 1, 2
    rng = np.random.default_rng(0)
    cell = (5.0, 5.4, 6.1, 80.0, 95.0, 100.0)
    rot = np.linalg.qr(rng.normal(size=(3, 3)))[0]; rot *= np.sign(np.linalg.det(rot))
    UB = rot @ _bmatrix_from_cell(cell)
    parts = []
    for p, delta in ((1, 0.0), (2, 2.0)):
        hkl = rng.integers(-3, 4, size=(45, 3)); hkl = hkl[np.abs(hkl).sum(1) > 0]
        w = rng.uniform(-30, 30, len(hkl))
        ql = qlab_to_sample(2 * np.pi * (hkl @ UB.T), -w)
        rep = w - delta
        qs = qlab_to_sample(ql, rep)
        parts.append(pd.DataFrame({"panel_id": p, "omega_deg": rep, "qlab_x": ql[:, 0], "qlab_y": ql[:, 1],
                                   "qlab_z": ql[:, 2], "qsample_x": qs[:, 0], "qsample_y": qs[:, 1],
                                   "qsample_z": qs[:, 2], "row": 1.0, "col": 1.0}))
    df = pd.concat(parts, ignore_index=True)
    df["spot_id"] = np.arange(len(df))
    t._spots_df = df
    t._candidate_df = df.copy()
    t._flagged_df = df.assign(is_diamond=False)
    return t, cell, UB


def test_align_panels_end_to_end_through_the_tab(app):
    import time
    t, cell, UB = _aligned_tab(app)
    t._panels[2]._ome_start.setValue(-30.0); t._panels[2]._ome_end.setValue(29.0)
    t._run_align_panels(cell, UB, "test lattice")
    t._worker.wait(120000)
    for _ in range(50):
        app.processEvents()
    res = t._align_result
    assert res is not None and res["n_matched_after"] >= 0.9 * res["n_spots"]
    ref = res["reference_panel"]
    other = 2 if ref == 1 else 1
    assert abs(abs(res["offsets"][other]) - 2.0) < 0.4
    before = t._spots_df.omega_deg.to_numpy().copy()
    applied = t.apply_panel_offsets()
    assert applied == {int(k): float(v) for k, v in res["offsets"].items()}
    shift = t._spots_df.omega_deg.to_numpy() - before
    assert np.allclose(shift[t._spots_df.panel_id.to_numpy() == other], res["offsets"][other])
    assert np.allclose(shift[t._spots_df.panel_id.to_numpy() == ref], 0.0)
    assert t._panels[2]._ome_start.value() == pytest.approx(-30.0 + res["offsets"].get(2, 0.0))
    assert t._panels[2]._ome_end.value() == pytest.approx(29.0 + res["offsets"].get(2, 0.0))
    assert t._domains == [] and t._candidate_df is None or len(t._domains) == 0
    t._cell_view.shutdown(); t.deleteLater(); app.processEvents()


def test_apply_offsets_without_a_result_is_a_noop(app):
    t, _cell, _UB = _aligned_tab(app)
    before = t._spots_df.omega_deg.to_numpy().copy()
    assert t.apply_panel_offsets() == {}
    assert np.array_equal(before, t._spots_df.omega_deg.to_numpy())
    t._cell_view.shutdown(); t.deleteLater(); app.processEvents()


def test_phase_screen_result_fills_known_structure_form(app):
    from midas_gui.tab_solve_cell import SolveCellTab
    t = SolveCellTab()
    t._agent_mode = True
    row = {"name": "Corundum", "primitive_cell": (5.1289, 5.1289, 5.1289, 55.28, 55.28, 55.28), "best_n": 118,
           "n_spots": 337, "decoy_mean": 46.0, "decoy_sd": 5.7, "decoy_max": 58, "z": 12.6, "verdict": "supported",
           "volume": 84.9, "UB": np.eye(3)}
    t._on_phase_screen_done({"rows": [row]})
    assert "Corundum" in t._ab_status.text() and "supported" in t._ab_status.text()
    assert t.use_phase_as_known_structure("Corundum")
    assert t._ks3["a"].value() == pytest.approx(5.1289) and t._ks3["alpha"].value() == pytest.approx(55.28)
    assert t._index_method.currentIndex() == 1
    assert not t.use_phase_as_known_structure("nope")
    t._cell_view.shutdown(); t.deleteLater(); app.processEvents()
