"""A reopened project's saved geometry must win over the data file's own
metadata header.

Its own module (and ``forked``, per .context/DECISIONS.md) because building a
third CalibrationTab in one process segfaults inside pyqtgraph's
ViewBox/PlotItem construction.
"""
import pytest


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.mark.forked
def test_restored_geometry_survives_the_reloaded_files_metadata(app, tmp_path, monkeypatch):
    """A project reload must not let the data file's own header overwrite the
    wavelength/pixel size the user saved.

    ``set_state()`` applies the saved fields first, then restores the loader —
    which re-loads the file and re-fires ``metadataDetected``. That header is a
    best-effort hint for a *fresh* interactive load and is routinely stale (the
    reported case: an AgBH frame collected at 72 keV whose
    ``instrument/HEM/Energy`` still reads ~51 keV, so every reopen silently
    replaced the saved λ=0.1722 Å with 0.2427 Å). The explicitly saved value
    wins.
    """
    import h5py
    import numpy as np
    from midas_gui import widgets as widgets_mod
    from midas_gui.tab_calibrate import CalibrationTab

    h5_path = tmp_path / "AgBehenate_72keV_001027.h5"
    with h5py.File(h5_path, "w") as f:
        f.create_dataset("exchange/data", data=np.zeros((3, 32, 64), dtype=np.uint16))

    # Stand in for the stale header, independent of which beamline profile
    # happens to be active in the test environment.
    monkeypatch.setattr(widgets_mod, "detect_geometry_from_path",
                        lambda *a, **k: {"wavelength_A": 0.24268, "pxY": 200.0})

    loader_state = {"path": str(h5_path), "dataset": "exchange/data", "frame": 0}

    # One tab reused throughout: building a second CalibrationTab in the same
    # process segfaults inside pyqtgraph's ViewBoxMenu teardown (see
    # .context/DECISIONS.md), and that is orthogonal to what this asserts.
    tab = CalibrationTab()
    tab._loader.set_state(loader_state)
    # A fresh interactive load *should* apply the hint.
    assert tab._wl.value() == pytest.approx(0.24268, abs=1e-5)

    tab._wl.setValue(0.17220)      # what the user actually ran at
    tab._pxY.setValue(55.0)
    state = tab.get_state()

    # Simulate reopening the project into a tab still holding the header values.
    tab._wl.setValue(0.24268)
    tab._pxY.setValue(200.0)
    tab.set_state(state)

    assert tab._wl.value() == pytest.approx(0.17220, abs=1e-5)
    assert tab._pxY.value() == pytest.approx(55.0, abs=1e-3)
    # ...and the suppression is scoped to the restore, not sticky.
    assert tab._restoring_state is False
    tab._loader.set_state(loader_state)
    assert tab._wl.value() == pytest.approx(0.24268, abs=1e-5)
