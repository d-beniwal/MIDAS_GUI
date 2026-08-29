"""calib._panel_spec must actually register per-panel rigid-shift
parameters as refinable.

This is the fact the whole multi-panel calibration feature hinges on:
autocalibrate_four_stage/_bayesian/_joint build their own spec via
midas_calibrate_v2's spec_from_v1_params() when none is passed, which never
registers panel_delta_yz/panel_delta_theta — panel_layout alone only
affects the fixed forward projection (which panel a pixel belongs to),
never what gets refined. Without an explicit panel-aware spec, no per-panel
shift is ever fit, so a calibration result's ``_panel_unpacked`` stays
empty and there is nothing for the save/persistence machinery
(tab_calibrate.py, project.py) to write.
"""
from midas_calibrate.params import CalibrationParams
from midas_calibrate_v2.forward.panels import PanelLayout

from midas_gui import calib


def _v1_params():
    return CalibrationParams(
        NrPixelsY=200, NrPixelsZ=200, pxY=200.0, pxZ=200.0,
        Lsd=200000.0, BC_y=100.0, BC_z=100.0, tx=0.0, ty=0.0, tz=0.0,
        Wavelength=0.1729, SpaceGroup=225,
        LatticeConstant=(5.41, 5.41, 5.41, 90.0, 90.0, 90.0),
        RhoD=141421.0, MaxRingRad=137.0, MinRingRad=10.0, nIterations=1,
    )


def test_panel_spec_registers_refinable_panel_shift_parameters():
    layout = PanelLayout.regular(1, 2, 200, 100, gap_y=0, gap_z=0)
    spec = calib._panel_spec(_v1_params(), layout)

    assert "panel_delta_yz" in spec.parameters
    assert spec.parameters["panel_delta_yz"].refined is True
    assert "panel_delta_theta" in spec.parameters
    assert spec.parameters["panel_delta_theta"].refined is True

    # Tolerances mirror midas_calibrate_v2.calibrate()'s own
    # panel_mode="shift" defaults (panel_tol_shift_px=3.0,
    # panel_tol_rot_deg=1.0) — the one place in the package already known
    # to do per-panel rigid-shift refinement correctly.
    assert spec.parameters["panel_delta_yz"].bounds == (-3.0, 3.0)
    assert spec.parameters["panel_delta_theta"].bounds == (-1.0, 1.0)


def test_panel_spec_shape_matches_panel_count():
    layout = PanelLayout.regular(2, 2, 100, 100, gap_y=5, gap_z=5)
    spec = calib._panel_spec(_v1_params(), layout)

    assert tuple(spec.parameters["panel_delta_yz"].init.shape) == (4, 2)
    assert tuple(spec.parameters["panel_delta_theta"].init.shape) == (4,)
