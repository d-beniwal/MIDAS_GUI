"""Panel-shifts sidecar resolution.

``geometry_fields_from_file`` must still find a calibration's
``_panelshifts.txt`` sidecar when the recorded ``panel_shifts_path`` no
longer exists as given (a stale absolute path from a different machine, or
a bare filename) as long as the sidecar is co-located with the geometry
file itself — the convention every midas-gui writer (Save .json, Save
paramstest, project-attempt materialization) now follows.
"""
import json
from pathlib import Path

from midas_gui.helpers import geometry_fields_from_file


def _write_shifts(path: Path):
    path.write_text(
        "    0  +0.100000  -0.200000  +1.000000e-03  +1.5000  +0.000000e+00\n"
        "    1  +0.300000  +0.400000  -2.000000e-03  -1.5000  +0.000000e+00\n"
    )


def _calib_json(shifts_path_value: str) -> dict:
    return {
        "NrPixelsY": 2048, "NrPixelsZ": 2048, "pxY": 200.0, "Lsd": 200000.0,
        "BC_y": 1024.0, "BC_z": 1024.0, "wavelength_A": 0.1729,
        "panel_layout": {"n_y": 1, "n_z": 2, "sy": 100, "sz": 100},
        "panel_shifts_path": shifts_path_value,
    }


def test_panel_shifts_path_resolved_relative_to_moved_json(tmp_path):
    json_path = tmp_path / "calibration.json"
    shifts_path = tmp_path / "calibration_panelshifts.txt"
    _write_shifts(shifts_path)
    json_path.write_text(json.dumps(
        _calib_json("/nonexistent/other/machine/calibration_panelshifts.txt")))

    fields = geometry_fields_from_file(str(json_path))
    assert fields["panel_shifts_path"] == str(shifts_path)


def test_panel_shifts_path_kept_as_is_when_it_already_exists(tmp_path):
    json_path = tmp_path / "calibration.json"
    shifts_path = tmp_path / "elsewhere_panelshifts.txt"
    _write_shifts(shifts_path)
    json_path.write_text(json.dumps(_calib_json(str(shifts_path))))

    fields = geometry_fields_from_file(str(json_path))
    assert fields["panel_shifts_path"] == str(shifts_path)


def test_panel_shifts_path_unresolvable_left_unchanged(tmp_path):
    json_path = tmp_path / "calibration.json"
    stale = "/nonexistent/other/machine/gone_panelshifts.txt"
    json_path.write_text(json.dumps(_calib_json(stale)))

    fields = geometry_fields_from_file(str(json_path))
    assert fields["panel_shifts_path"] == stale
