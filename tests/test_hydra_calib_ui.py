"""Offscreen UI test for the Calibrate tab's Hydra page: mode wiring, panel
switching (incl. the Pick BC/Pick Ring cross-panel leak regression, same
shape as `eadb14d`'s fix for the Data Viewer's Hydra page), and Sequential/
Parallel run orchestration.

Each ``HydraCalibrationPage`` builds 1 ``PickableImageViewer`` (pg.ImageView)
plus 5 pg.PlotWidgets (4 ``ResidualBarChart`` + 1 ``HydraProfileViewer``) —
enough pyqtgraph instances per page that, per .context/DECISIONS.md's
pyqtgraph-teardown-crash entry, building one per test function reliably
segfaults the whole file's run (reproduced while writing this file: 2 tests
in, then a hard abort in teardown). Folded into just 2 test functions, each
building exactly ONE page and reusing it for every assertion, mirroring
that entry's fix for the Data Viewer's own Hydra UI tests.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PyQt5 import QtCore, QtWidgets

import midas_gui.hydra_calib_page as hydra_calib_page_mod
from midas_gui.helpers import geometry_fields_from_file
from midas_gui.hydra_calib_page import HydraCalibrationPage

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "test_data" / "gui_synthetic" / "hydra"


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(autouse=True)
def _qt_teardown(app):
    yield
    app.processEvents()


@pytest.fixture()
def fixture_available():
    if not FIXTURE_DIR.exists():
        pytest.skip("test_data/gui_synthetic/hydra/ fixture not present — "
                    "run make_hydra_test_data.py")
    return FIXTURE_DIR


class _FakeWorker(QtCore.QObject):
    """Shared no-op-thread shape for both CalibrationWorker and
    IntegrationWorker fakes: finishes on the next event-loop tick instead of
    a real background thread. The synthetic Hydra fixture wasn't built to
    produce enough calibrant rings for a real fit to converge (verified
    directly against midas_gui.calib — a pre-existing fixture/data
    characteristic, not a page bug), so these tests exercise the GUI's
    sequencing/routing logic rather than the fit numerics."""
    log_line = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)

    def start(self):
        QtCore.QTimer.singleShot(0, self._finish)

    def isRunning(self) -> bool:
        return False

    def requestInterruption(self):
        pass


class _FakeCalibrationWorker(_FakeWorker):
    def __init__(self, mode, image, dark, cfg, parent=None, bright=None, background=None,
                 bright_mode="divide", capture_stdout=True):
        super().__init__(parent)
        self._cfg = cfg

    def _finish(self):
        seed = self._cfg.get("manual_seed") or {}
        result = SimpleNamespace(
            BC_y=seed.get("BC_y", 128.0), BC_z=seed.get("BC_z", 128.0),
            Lsd=seed.get("Lsd", 1_000_000.0), wavelength_A=self._cfg["wavelength"],
            pxY=self._cfg["pxY"], pxZ=self._cfg["pxY"], NrPixelsY=256, NrPixelsZ=256,
            tx=0.0, ty=0.0, tz=0.0, distortion={}, post_residual_strain_uE=0.0)
        self.finished.emit(result)


class _FakeIntegrationWorker(_FakeWorker):
    def __init__(self, result, image, dark, im_trans, r_bin, eta_bin, mask=None, parent=None,
                 bright=None, background=None, bright_mode="divide", weighted=True):
        super().__init__(parent)
        self._result = result

    def _finish(self):
        r_axis = np.linspace(0, 100, 50)
        profile = np.ones_like(r_axis)
        self.finished.emit({"r_axis_px": r_axis, "profile": profile,
                            "wavelength_A": self._result.wavelength_A,
                            "lsd_um": self._result.Lsd, "px_um": self._result.pxY})


@pytest.fixture(autouse=True)
def _stub_workers(monkeypatch):
    monkeypatch.setattr(hydra_calib_page_mod, "CalibrationWorker", _FakeCalibrationWorker)
    monkeypatch.setattr(hydra_calib_page_mod, "IntegrationWorker", _FakeIntegrationWorker)


def _load_and_seed(page: HydraCalibrationPage, fixture: Path):
    page._loader.set_path(str(fixture / "ge1" / "panel.ge1.h5"))
    last = None
    for n in (1, 2, 3, 4):
        g = geometry_fields_from_file(str(fixture / f"ge{n}" / f"ps_ge{n}.txt"))
        page._cards[n].seed_from_geometry(g)
        last = g
    page._wl.setValue(last["wavelength_A"])
    page._pxY.setValue(last["pxY"])


def _pump(app, condition, max_iters=200):
    for _ in range(max_iters):
        if condition():
            return True
        app.processEvents()
    return False


def test_hydra_calib_page_wiring_and_pick_isolation(app, fixture_available):
    """Toolbar has no Composite button, siblings load correctly, and Pick
    BC/Pick Ring in-progress state on the shared viewer is cleared when the
    active panel switches (the eadb14d regression shape)."""
    page = HydraCalibrationPage()
    assert "composite" not in page._toolbar._buttons
    _load_and_seed(page, fixture_available)
    assert set(page._loader.siblings()) == {1, 2, 3, 4}

    page._toolbar.set_current("ge1")
    page._img_view._ring_pts.append((1.0, 2.0))
    page._toolbar.set_current("ge2")
    assert page._img_view._ring_pts == []
    assert page._active_card.panel_number == 2


def test_hydra_calib_run_orchestration_and_results_switching(app, fixture_available):
    """Sequential run: independent per-panel BCs. Parallel run: all 4
    workers started up-front. Results/Ring-Residuals tabs switch with the
    active panel. All against ONE page instance (see module docstring)."""
    page = HydraCalibrationPage()
    _load_and_seed(page, fixture_available)
    # The synthetic fixture's ps_ge{1..4}.txt files all share the same
    # nominal BC (128, 128) by design (built for geometry/composite tests,
    # not distinct panel positions) — give each panel a distinct seed BC
    # here so the fake worker's echoed-back result can actually distinguish
    # "routed to the right panel" from "coincidentally identical seed data".
    for n in (1, 2, 3, 4):
        page._cards[n]._seed_bcy.setValue(100.0 + n)
        page._cards[n]._seed_bcz.setValue(100.0 + n)

    page._run_mode_combo.setCurrentIndex(0)   # Sequential
    page._run_all()
    ok = _pump(app, lambda: not page._workers and not page._pending_panels)
    assert ok, "sequential run did not complete"
    results = {n: page._cards[n].result for n in (1, 2, 3, 4)}
    assert all(r is not None for r in results.values())
    bcs = {n: (r.BC_y, r.BC_z) for n, r in results.items()}
    assert len(set(bcs.values())) > 1, "panels should not all share the same fitted BC"
    assert page._run_btn.isEnabled() and not page._abort_btn.isEnabled()

    page._toolbar.set_current("ge1")
    assert page._results_stack.currentWidget() is page._cards[1].results_widget
    assert page._resid_stack.currentWidget() is page._cards[1].residual_chart
    page._toolbar.set_current("ge3")
    assert page._results_stack.currentWidget() is page._cards[3].results_widget
    assert page._resid_stack.currentWidget() is page._cards[3].residual_chart

    for n in (1, 2, 3, 4):
        page._cards[n].result = None
    page._run_mode_combo.setCurrentIndex(1)   # Parallel
    page._run_all()
    assert len(page._workers) == 4, "parallel mode should start every panel at once"
    ok = _pump(app, lambda: not page._workers and not page._pending_panels)
    assert ok, "parallel run did not complete"
    assert all(page._cards[n].result is not None for n in (1, 2, 3, 4))
