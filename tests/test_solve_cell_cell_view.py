"""Unit cell tab: tables, reference overlay selection, and wiring into the Solve Cell tab."""
import pytest

pytestmark = pytest.mark.forked

RUBY = {"cell": [4.7096, 4.7889, 5.0422, 90.984, 63.411, 61.64], "cell_sigma": [0.001] * 6,
        "conventional_cell": [5.1329, 4.8669, 11.758, 90.33, 35.675, 89.028],
        "conventional_system": "monoclinic", "holohedry_system": "triclinic", "holohedry_order": 2,
        "rms_drlv": 0.0868, "n_reflections": 161}


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _teardown(w, app):
    getattr(w, "_cell_view", w).shutdown()
    w.deleteLater()
    app.processEvents()


def test_view_fills_tables_and_selects_best_match(app):
    from midas_gui.solve_cell_cell_view import UnitCellView
    v = UnitCellView()
    v.set_result("Domain 1", RUBY)
    assert v.wait()
    assert v.report is not None
    assert v.cell_table.rowCount() == 3                      # refined, reduced, conventional
    assert v.match_table.item(0, 0).text().startswith("Corundum")
    assert v.match_table.item(0, 1).text() in ("matches", "close")
    assert v.selected_match()["name"].startswith("Corundum")
    assert "V = 85." in v.status.text()
    assert v.ladder_table.rowCount() >= 2
    assert "statistical only" in v.notes.text()
    assert "orange" in v.legend.text()
    v.show_reduced.setChecked(True)                          # redraw with every overlay on
    assert "grey" in v.legend.text()
    _teardown(v, app)


def test_stale_report_is_dropped_and_clear_works(app):
    from midas_gui.solve_cell_cell_view import UnitCellView
    v = UnitCellView()
    v.set_result("first", RUBY)
    assert "open this tab" in v.status.text()                # nothing computed while hidden
    assert v._worker is None
    v._start()
    v._seq += 1                                              # a newer selection supersedes it
    v.wait()
    assert v.report is None
    v.set_result("", None)
    assert v.match_table.rowCount() == 0
    _teardown(v, app)


def test_tab_domain_selection_populates_cell_view_and_grid(app):
    import pandas as pd
    from midas_gui.tab_solve_cell import SolveCellTab
    t = SolveCellTab()
    df = pd.DataFrame({"qsample_x": [0.1, 0.2], "qsample_y": [0.0, 0.1], "qsample_z": [0.3, 0.2]})
    t._candidate_df = df
    xyz = tuple(df[c].to_numpy() for c in ("qsample_x", "qsample_y", "qsample_z"))
    t._add_domain("free (ab-initio)", RUBY, df.index, xyz)
    assert t._cell_view.wait()
    assert t._cell_view.report is not None
    texts = []
    for i in range(t._param_grid.count()):
        w = t._param_grid.itemAt(i).widget()
        texts.append(w.text() if hasattr(w, "text") else "")
    assert "volume:" in texts and any("85." in x and "Å³" in x for x in texts)
    t._reset_ingest_results()
    assert t._cell_view.match_table.rowCount() == 0
    _teardown(t, app)
