"""Tests for MIDAS ``ImTransOpt`` (image flip/transpose) support: parsing from
and writing to paramstest files, and the checkbox->codes helper shared by the
Data Viewer, Mask, and Calibrate tabs.
"""
from types import SimpleNamespace

import numpy as np
import pytest

from midas_gui.helpers import (
    _apply_im_trans, parse_im_trans, im_trans_codes_from_checkboxes,
    geometry_fields_from_file, read_geometry, write_standalone_paramstest,
)

_SAMPLE_PARAMSTEST = """\
NrPixelsY 100
NrPixelsZ 120
px 200.0
Lsd 500000.0
BC 50.0 60.0
Wavelength 0.1729
ImTransOpt 2
"""


def test_parse_im_trans_repeated_lines():
    text = "ImTransOpt 1\nImTransOpt 3\n"
    assert parse_im_trans(text) == [1, 3]


def test_parse_im_trans_drops_explicit_noop():
    assert parse_im_trans("ImTransOpt 0\n") == []


def test_parse_im_trans_absent():
    assert parse_im_trans("Lsd 500000.0\n") == []


def test_geometry_fields_from_file_paramstest(tmp_path):
    p = tmp_path / "paramstest.txt"
    p.write_text(_SAMPLE_PARAMSTEST)
    fields = geometry_fields_from_file(str(p))
    assert fields["im_trans"] == [2]


def test_read_geometry_paramstest(tmp_path):
    p = tmp_path / "paramstest.txt"
    p.write_text(_SAMPLE_PARAMSTEST)
    geo = read_geometry(str(p))
    assert geo["im_trans"] == [2]


def test_geometry_fields_from_file_json_round_trips_im_trans(tmp_path):
    import json
    p = tmp_path / "calibration.json"
    p.write_text(json.dumps({
        "NrPixelsY": 100, "NrPixelsZ": 120, "pxY": 200.0, "Lsd": 500000.0,
        "BC_y": 50.0, "BC_z": 60.0, "wavelength_A": 0.1729, "im_trans": [1, 3],
    }))
    fields = geometry_fields_from_file(str(p))
    assert fields["im_trans"] == [1, 3]


def test_geometry_fields_from_file_defaults_im_trans_empty(tmp_path):
    import json
    p = tmp_path / "calibration.json"
    p.write_text(json.dumps({
        "NrPixelsY": 100, "NrPixelsZ": 120, "pxY": 200.0, "Lsd": 500000.0,
        "BC_y": 50.0, "BC_z": 60.0, "wavelength_A": 0.1729,
    }))
    fields = geometry_fields_from_file(str(p))
    assert fields["im_trans"] == []


def test_write_standalone_paramstest_emits_im_trans_lines(tmp_path):
    result = SimpleNamespace(
        NrPixelsY=100, NrPixelsZ=120, pxY=200.0, pxZ=200.0,
        Lsd=500000.0, BC_y=50.0, BC_z=60.0, tx=0.0, ty=0.0, tz=0.0,
        wavelength_A=0.1729, distortion={}, im_trans=[1, 3],
    )
    out = tmp_path / "paramstest.txt"
    write_standalone_paramstest(result, out)
    lines = out.read_text().splitlines()
    im_lines = [ln for ln in lines if ln.startswith("ImTransOpt")]
    assert im_lines == ["ImTransOpt 1", "ImTransOpt 3"]


def test_write_standalone_paramstest_no_im_trans_attr(tmp_path):
    """A result without an ``im_trans`` attribute writes no ImTransOpt lines."""
    result = SimpleNamespace(
        NrPixelsY=100, NrPixelsZ=120, pxY=200.0, pxZ=200.0,
        Lsd=500000.0, BC_y=50.0, BC_z=60.0, tx=0.0, ty=0.0, tz=0.0,
        wavelength_A=0.1729, distortion={},
    )
    out = tmp_path / "paramstest.txt"
    write_standalone_paramstest(result, out)
    assert "ImTransOpt" not in out.read_text()


def test_write_then_read_round_trip(tmp_path):
    result = SimpleNamespace(
        NrPixelsY=100, NrPixelsZ=120, pxY=200.0, pxZ=200.0,
        Lsd=500000.0, BC_y=50.0, BC_z=60.0, tx=0.0, ty=0.0, tz=0.0,
        wavelength_A=0.1729, distortion={}, im_trans=[2],
    )
    out = tmp_path / "paramstest.txt"
    write_standalone_paramstest(result, out)
    assert geometry_fields_from_file(str(out))["im_trans"] == [2]


@pytest.mark.parametrize("flip_y,flip_z,transp,expected", [
    (False, False, False, []),
    (True, False, False, [1]),
    (False, True, False, [2]),
    (False, False, True, [3]),
    (True, True, False, [1, 2]),
    (True, False, True, [1, 3]),
    (False, True, True, [2, 3]),
    (True, True, True, [1, 2, 3]),
])
def test_im_trans_codes_from_checkboxes(flip_y, flip_z, transp, expected):
    class _FakeCheckbox:
        def __init__(self, checked): self._c = checked
        def isChecked(self): return self._c

    codes = im_trans_codes_from_checkboxes(
        _FakeCheckbox(flip_y), _FakeCheckbox(flip_z), _FakeCheckbox(transp))
    assert codes == expected


def test_apply_im_trans_composition_order():
    img = np.arange(6).reshape(2, 3)
    flip_y = img[:, ::-1]
    flip_then_transpose = flip_y.T
    assert np.array_equal(_apply_im_trans(img, (1, 3)), flip_then_transpose)
