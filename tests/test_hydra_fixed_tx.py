"""Hydra panels have a FIXED tx (``hydra.HYDRA_PANEL_TX``).

tx is each panel's installation azimuth about the beam. It is mounting
geometry, not a fitted value (a powder pattern is unchanged by rotating a panel
about the beam), so it is pinned everywhere a Hydra panel's geometry is read or
written, whatever a loaded calibration file says, and never refined.
"""
from __future__ import annotations

import json

import pytest

from midas_gui import hydra
from midas_gui.helpers import geometry_fields_from_file

TX = {1: 300.0, 2: 30.0, 3: 120.0, 4: 210.0}


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _calib_json(tmp_path, n, tx=0.0):
    d = {"Lsd": 2400000.0, "BC_y": 2275.0, "BC_z": 2166.0, "tx": tx, "ty": 0.1,
         "tz": 0.05, "distortion": {}, "pxY": 200.0, "pxZ": 200.0,
         "NrPixelsY": 2048, "NrPixelsZ": 2048, "wavelength_A": 0.17298, "im_trans": []}
    p = tmp_path / f"c.ge{n}.json"
    p.write_text(json.dumps(d))
    return str(p)


def test_the_constants_are_the_agreed_values():
    assert hydra.HYDRA_PANEL_TX == TX
    assert hydra.fixed_tx(3) == 120.0 and hydra.fixed_tx("2") == 30.0
    assert hydra.fixed_tx(0) is None and hydra.fixed_tx(5) is None
    assert hydra.fixed_tx(None) is None


def test_pin_tx_copies_and_leaves_other_panels_alone():
    src = {"tx": 0.0, "BC_y": 1.0}
    assert hydra.pin_tx(src, 4) == {"tx": 210.0, "BC_y": 1.0}
    assert src["tx"] == 0.0, "must not mutate the caller's dict"
    assert hydra.pin_tx(src, 0) is src
    assert hydra.pin_tx({}, 1) == {}


@pytest.mark.parametrize("n", [1, 2, 3, 4])
def test_the_bundled_default_geometry_carries_the_fixed_tx(n):
    f = geometry_fields_from_file(str(hydra.default_param_file(n)))
    assert f["tx"] == pytest.approx(TX[n])
    st = hydra.DetectorState()
    st.load_default(n)
    assert st.panel == n and st.tx == pytest.approx(TX[n])


def test_a_detector_state_ignores_the_tx_in_a_loaded_file():
    st = hydra.DetectorState(panel=2)
    st.load_from_geometry_dict({"BC_y": 10.0, "BC_z": 20.0, "tx": 77.0})
    assert st.tx == pytest.approx(30.0) and st.bc_y == 10.0
    other = hydra.DetectorState()           # panel unknown: untouched
    other.load_from_geometry_dict({"tx": 77.0})
    assert other.tx == pytest.approx(77.0)


@pytest.mark.parametrize("n", [1, 2, 3, 4])
def test_the_batch_card_integrates_with_the_fixed_tx_whatever_the_file_says(app, tmp_path, n):
    from midas_gui.hydra_batch_widgets import HydraBatchPanelCard
    card = HydraBatchPanelCard(n)
    card.set_calibration_file(_calib_json(tmp_path, n, tx=0.0))   # never-fitted tx
    spec = card.resolved_spec()
    assert float(spec.tx) == pytest.approx(TX[n])
    fields, _ = card._calib_fields_in_use()
    assert fields["tx"] == pytest.approx(TX[n])
    snap, _ = card.full_calib_snapshot()
    assert snap["tx"] == pytest.approx(TX[n])


def test_the_viewer_card_pins_tx_on_every_load_path(app, tmp_path):
    from midas_gui.hydra_geometry_card import DetectorGeometryCard
    card = DetectorGeometryCard(show_calib_handoff=False)
    card.set_fixed_tx(120.0)
    assert not card._tx.isEnabled() and card._tx.value() == pytest.approx(120.0)
    card.set_geometry({"tx": 5.0, "BC_y": 1.0, "BC_z": 2.0, "Lsd": 2.4e6,
                       "pxY": 200.0, "wavelength_A": 0.17, "NrPixelsY": 8,
                       "NrPixelsZ": 8, "ty": 0.1})
    assert card._tx.value() == pytest.approx(120.0)
    assert card._calib_geom["tx"] == pytest.approx(120.0)
    assert card._pin_tx({"tx": 1.0})["tx"] == 120.0
    # releasing the pin makes it an ordinary editable box again
    card.set_fixed_tx(None)
    assert card._tx.isEnabled() and card._pin_tx({"tx": 1.0})["tx"] == 1.0


def test_tx_is_never_a_refinement_option(app, tmp_path):
    """The refine tick is disabled AND the flag sent to the backend is False,
    even if a restored project had ticked the (hidden-state) box."""
    h5py = pytest.importorskip("h5py")
    import numpy as np
    from midas_gui.hydra_calib_page import HydraCalibrationPage
    with h5py.File(str(tmp_path / "c_00001.ge1.h5"), "w") as f:
        f.create_dataset("exchange/data", data=np.zeros((2, 32, 32), np.float32))
    page = HydraCalibrationPage()
    page._loader._set_path(str(tmp_path / "c_00001.ge1.h5"))
    assert not page._ref_tx.isEnabled()
    page._ref_tx.setChecked(True)
    assert page._refine_flags()["tx"] is False
    for n, card in page._cards.items():
        assert card._seed_tx.value() == pytest.approx(TX[n])
        assert card.manual_seed()["tx"] == pytest.approx(TX[n])
