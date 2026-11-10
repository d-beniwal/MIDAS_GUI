"""A project written by the headless Solve Cell runner opens in the live GUI
through the same control-API op an agent/TRACE uses (``project.open``) and
restores the Solve Cell tab (Qt/offscreen; the headless run itself is Qt-free,
see test_solve_cell_headless.py)."""
import pytest

pytest.importorskip("midas_defect")
pytest.importorskip("midas_hkls")

from midas_gui.solve_cell import headless as H
from test_solve_cell_headless import CELL_A, _spec, synth  # noqa: F401  (synth is a fixture)


@pytest.fixture(scope="module")
def qapp():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def win(qapp):
    import midas_gui.app as app_mod
    return app_mod.MainWindow()


@pytest.fixture
def ctrl(win, monkeypatch):
    from PyQt5 import QtWidgets
    from midas_gui.control_api import GuiController

    def _boom(*a, **k):
        raise AssertionError("a modal QMessageBox was opened")
    for name in ("information", "critical", "warning", "question"):
        monkeypatch.setattr(QtWidgets.QMessageBox, name, staticmethod(_boom))
    c = GuiController(win)
    yield c
    win._set_project_path(None)
    win._set_workspace_dirty(False)


def test_project_open_restores_headless_solve_cell(ctrl, win, tmp_path, synth):  # noqa: F811
    out = tmp_path / "headless.h5"
    res = H.run_solve_cell_headless(_spec(synth, out))
    assert res["status"] == "ok", res["error"]

    reply = ctrl.dispatch("project.open", {"path": str(out)})
    assert reply["ok"] is True, reply
    assert reply["result"]["selection"]["solve_cell"] is True
    assert win._project_ctx.path == str(out)

    tab = win._solve_cell_tab
    assert tab._spots_df is not None and len(tab._spots_df) == res["n_spots"]
    assert len(tab._domains) == 1
    assert tab._domains[0]["result"]["cell"] == pytest.approx(res["cell"])
    assert sorted(tab._domains[0]["result"]["conventional_cell"][:3])[0] == pytest.approx(CELL_A, rel=0.01)
    assert tab._candidate_df is not None and tab._ab_initio_raw is not None
    assert tab._panels[1].geometry_cfg()["omega_step_deg"] == res["omega"]["1"]["step"]
