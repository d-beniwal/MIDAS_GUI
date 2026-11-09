"""Transforms checkboxes record tick order = ImTransOpt sequence order."""
import numpy as np
import pytest
from PyQt5 import QtWidgets

from midas_gui.helpers import (
    _apply_im_trans, apply_dict_to_widgets, im_trans_codes_from_checkboxes,
    link_im_trans_checkboxes, set_im_trans_codes, widgets_to_dict,
)


_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])  # keep a reference


@pytest.fixture
def boxes():
    b = [QtWidgets.QCheckBox(n) for n in ("Flip Y", "Flip Z", "Transpose")]
    link_im_trans_checkboxes(*b)
    return b


def _keys(b):
    return {"flip_y": b[0], "flip_z": b[1], "transp": b[2]}


def test_tick_order_is_sequence_and_labels_are_numbered(boxes):
    boxes[2].setChecked(True)
    boxes[0].setChecked(True)
    assert im_trans_codes_from_checkboxes(*boxes) == [3, 1]
    assert [b.text() for b in boxes] == ["Flip Y (2)", "Flip Z", "Transpose (1)"]
    boxes[2].setChecked(False)                     # untick renumbers the rest
    assert [b.text() for b in boxes] == ["Flip Y (1)", "Flip Z", "Transpose"]


def test_order_survives_state_roundtrip_and_old_states_use_fixed_order(boxes):
    boxes[2].setChecked(True); boxes[0].setChecked(True)
    state = widgets_to_dict(_keys(boxes))
    assert state["im_trans_seq"] == [3, 1]
    other = [QtWidgets.QCheckBox(n) for n in ("Flip Y", "Flip Z", "Transpose")]
    link_im_trans_checkboxes(*other)
    apply_dict_to_widgets(_keys(other), state)
    assert im_trans_codes_from_checkboxes(*other) == [3, 1]
    old = [QtWidgets.QCheckBox(n) for n in ("Flip Y", "Flip Z", "Transpose")]
    link_im_trans_checkboxes(*old)
    apply_dict_to_widgets(_keys(old), {"flip_y": True, "transp": True})
    assert im_trans_codes_from_checkboxes(*old) == [1, 3]


def test_set_codes_reorder_notifies_once_and_noop_is_silent(boxes):
    fired = []
    boxes[0].toggled.connect(lambda c: fired.append(c))
    set_im_trans_codes(*boxes, [3, 1])
    assert im_trans_codes_from_checkboxes(*boxes) == [3, 1] and len(fired) == 1
    set_im_trans_codes(*boxes, [1, 3])             # pure re-order, same checked set
    assert im_trans_codes_from_checkboxes(*boxes) == [1, 3] and len(fired) == 2
    set_im_trans_codes(*boxes, [1, 3, 1])          # repeat collapses; nothing changed
    assert len(fired) == 2


def test_rotations_are_expressible_on_a_nonsquare_array():
    a = np.arange(12).reshape(3, 4)
    assert (_apply_im_trans(a, (2, 3)) == np.rot90(a, -1)).all()    # 90° cw
    assert (_apply_im_trans(a, (1, 3)) == np.rot90(a, 1)).all()     # 90° ccw
    assert (_apply_im_trans(a, (1, 2)) == np.rot90(a, 2)).all()     # 180°


# ── the written parameter file ──────────────────────────────────────────────

def _result(codes):
    import types
    return types.SimpleNamespace(
        NrPixelsY=1024, NrPixelsZ=402, pxY=172., pxZ=172., Lsd=1e6, BC_y=500., BC_z=200.,
        tx=0., ty=.1, tz=.2, wavelength_A=.17, distortion={}, _calibrant_name="CeO2",
        im_trans=codes)


def _lines(path):
    from pathlib import Path
    return [ln for ln in Path(path).read_text().splitlines() if ln.startswith("ImTransOpt")]


def test_standalone_paramstest_keeps_sequence_and_integrate_reads_it_back(tmp_path):
    from midas_gui.helpers import write_standalone_paramstest, parse_im_trans
    p = tmp_path / "ps.txt"
    write_standalone_paramstest(_result([3, 1]), str(p))
    assert _lines(p) == ["ImTransOpt 3", "ImTransOpt 1"]          # one line per op, in order
    assert parse_im_trans(p.read_text()) == [3, 1]
    from midas_integrate_v2.compat.from_v1 import spec_from_v1_paramstest
    assert list(spec_from_v1_paramstest(str(p)).TransOpt) == [3, 1]
    from midas_integrate.params import parse_params
    assert list(parse_params(str(p)).TransOpt) == [3, 1]


def test_template_output_replaces_template_im_trans_instead_of_doubling_it(tmp_path):
    from midas_calibrate_v2.compat.to_v1 import ff_paramstest_from_auto_result
    from midas_gui.helpers import set_paramstest_im_trans
    tmpl, out = tmp_path / "t.txt", tmp_path / "o.txt"
    tmpl.write_text("NrPixelsY 1024\nNrPixelsZ 402\nImTransOpt 2\nImTransOpt 2\nSpaceGroup 225\n")
    res = _result((3, 1))
    ff_paramstest_from_auto_result(res, str(tmpl), str(out))
    set_paramstest_im_trans(str(out), res.im_trans)
    assert _lines(out) == ["ImTransOpt 3", "ImTransOpt 1"]
    set_paramstest_im_trans(str(out), res.im_trans)               # idempotent
    assert _lines(out) == ["ImTransOpt 3", "ImTransOpt 1"]
    set_paramstest_im_trans(str(out), ())                         # no transform -> no stale lines
    assert _lines(out) == []
