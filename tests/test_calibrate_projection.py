"""The Calibrate tab's "Projection" card (replaces the old "Mean of frames"
card): Max/Sum/Mean over a range of frames, same mechanics as the Data
Viewer's own Projection card (``tab_view.py``), whose output feeds the
calibration pipeline directly. See .context/DECISIONS.md for the
correct-each-frame-before-reducing rationale.

Its own module (and ``forked``) because building a second ``CalibrationTab``
in the same process segfaults inside pyqtgraph's ViewBox teardown — see
``tests/test_calibrate_state_restore.py``.
"""
from __future__ import annotations

import numpy as np
import pytest
import tifffile


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.mark.forked
def test_projection_card_disabled_for_single_frame_enabled_for_stack(app, tmp_path):
    from midas_gui.tab_calibrate import CalibrationTab

    tab = CalibrationTab()
    assert tab._proj_card.isEnabled() is False   # bundled single-frame demo image

    stack_path = tmp_path / "stack.tif"
    tifffile.imwrite(stack_path, np.zeros((3, 8, 8), dtype=np.float32))
    tab._loader.set_path(str(stack_path))
    assert tab._loader.n_frames() == 3
    assert tab._proj_card.isEnabled() is True


@pytest.mark.forked
def test_source_image_returns_projection_only_while_active(app):
    from midas_gui.tab_calibrate import CalibrationTab

    tab = CalibrationTab()
    raw = tab._loader.current_frame()
    assert np.array_equal(tab._source_image(), raw)   # not active: raw current frame

    proj = np.full_like(raw, 42.0)
    tab._proj_active = True
    tab._proj_image = proj
    assert np.array_equal(tab._source_image(), proj)

    tab._on_project_clear()
    assert tab._proj_active is False
    assert tab._proj_image is None
    assert np.array_equal(tab._source_image(), raw)


@pytest.mark.forked
def test_new_data_or_frame_nav_drops_a_stale_projection(app, tmp_path):
    from midas_gui.tab_calibrate import CalibrationTab

    tab = CalibrationTab()
    stack_path = tmp_path / "stack.tif"
    tifffile.imwrite(stack_path, np.zeros((3, 8, 8), dtype=np.float32))
    tab._loader.set_path(str(stack_path))

    tab._proj_active = True
    tab._proj_image = np.ones((8, 8), dtype=np.float32)

    tab._sync_proj_controls()   # same call _on_loader_data makes on every reload

    assert tab._proj_active is False
    assert tab._proj_image is None


@pytest.mark.forked
def test_show_calib_image_does_not_recorrect_an_active_projection(app, monkeypatch):
    """Preview must not re-apply dark/bright/background to an already-
    corrected projection (``_loader.corrected`` must not be called)."""
    from midas_gui.tab_calibrate import CalibrationTab

    tab = CalibrationTab()
    tab._image = np.full((20, 20), 7.0, dtype=np.float32)
    tab._proj_active = True
    tab._proj_image = tab._image

    called = []
    monkeypatch.setattr(tab._loader, "corrected", lambda img: called.append(img) or img)
    tab._show_calib_image(autorange=False)
    assert not called   # corrected() skipped while a projection is active

    tab._proj_active = False
    tab._show_calib_image(autorange=False)
    assert called   # normal (raw current-frame) path still corrects for preview


@pytest.mark.forked
def test_run_passes_no_field_corrections_when_projection_is_active(app, monkeypatch):
    """CalibrationWorker must receive dark=bright=background=None when the
    source image is an already-corrected projection — otherwise the pipeline
    would double-correct it."""
    from PyQt5 import QtCore
    import midas_gui.tab_calibrate as tab_calibrate_mod
    from midas_gui.tab_calibrate import CalibrationTab

    captured = {}

    class _FakeCalibrationWorker(QtCore.QObject):
        log_line = QtCore.pyqtSignal(str)
        finished = QtCore.pyqtSignal(object)
        failed = QtCore.pyqtSignal(str)

        def __init__(self, mode, image, dark, cfg, parent=None, bright=None,
                     background=None, bright_mode="divide"):
            super().__init__(parent)
            captured.update(dark=dark, bright=bright, background=background)

        def start(self):
            pass

        def isRunning(self):
            return False

        def requestInterruption(self):
            pass

    monkeypatch.setattr(tab_calibrate_mod, "CalibrationWorker", _FakeCalibrationWorker)

    tab = CalibrationTab()
    tab._out_ed.setText("")   # no custom working dir — falls back to session scratch
    tab._proj_active = True
    tab._proj_image = np.full((tab._loader.current_frame().shape), 3.0, dtype=np.float32)

    tab._run()

    assert captured == {"dark": None, "bright": None, "background": None}


@pytest.mark.forked
def test_project_stack_end_to_end_drives_the_real_worker(app, tmp_path):
    """Click "Project stack" on a real multi-frame TIFF, let the real
    ``ProjectionWorker`` run to completion, and confirm the result becomes
    the pipeline's source image."""
    from midas_gui.tab_calibrate import CalibrationTab

    tab = CalibrationTab()
    stack = np.stack([np.full((6, 6), float(i) + 1.0, dtype=np.float32)
                      for i in range(4)], axis=0)
    stack_path = tmp_path / "stack.tif"
    tifffile.imwrite(stack_path, stack)
    tab._loader.set_path(str(stack_path))
    assert tab._loader.n_frames() == 4

    tab._proj_method["sum"].setChecked(True)
    tab._project()
    assert tab._proj_worker is not None
    tab._proj_worker.wait()
    for _ in range(20):
        app.processEvents()

    assert tab._proj_active is True
    # No dark/bright/background configured, so sum-projection is just the
    # elementwise sum of the 4 frames: 1+2+3+4 = 10.
    assert np.allclose(tab._proj_image, 10.0)
    assert np.array_equal(tab._source_image(), tab._proj_image)


@pytest.mark.forked
def test_run_integration_passes_no_field_corrections_when_projection_is_active(app, monkeypatch):
    from PyQt5 import QtCore
    from types import SimpleNamespace
    import midas_gui.tab_calibrate as tab_calibrate_mod
    from midas_gui.tab_calibrate import CalibrationTab

    captured = {}

    class _FakeIntegrationWorker(QtCore.QObject):
        log_line = QtCore.pyqtSignal(str)
        finished = QtCore.pyqtSignal(object)
        failed = QtCore.pyqtSignal(str)

        def __init__(self, result, image, dark, im_trans, r_bin, eta_bin, mask=None,
                     parent=None, bright=None, background=None, bright_mode="divide",
                     weighted=True):
            super().__init__(parent)
            captured.update(dark=dark, bright=bright, background=background)

        def start(self):
            pass

        def isRunning(self):
            return False

        def requestInterruption(self):
            pass

    monkeypatch.setattr(tab_calibrate_mod, "IntegrationWorker", _FakeIntegrationWorker)

    tab = CalibrationTab()
    tab._proj_active = True
    tab._proj_image = tab._loader.current_frame()
    tab._image = tab._proj_image

    result = SimpleNamespace(
        BC_y=128.0, BC_z=128.0, Lsd=1_000_000.0, wavelength_A=0.1729,
        pxY=200.0, pxZ=200.0, NrPixelsY=256, NrPixelsZ=256,
        tx=0.0, ty=0.0, tz=0.0, distortion={})

    tab._run_integration(result)

    assert captured == {"dark": None, "bright": None, "background": None}
