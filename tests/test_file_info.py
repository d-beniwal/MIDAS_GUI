import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtWidgets

from midas_gui.helpers import (attach_file_info, file_info_text,
                               warn_if_path_missing)

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_info_text_file_and_folder(tmp_path):
    f = tmp_path / "a.h5"
    f.write_bytes(b"x" * 2048)
    t = file_info_text(str(f))
    for key in ("Name", "Size", "2.00 KB", "Created", "Modified", "Owner"):
        assert key in t
    assert "Items" in file_info_text(str(tmp_path))


def test_info_text_missing_or_empty(tmp_path):
    assert file_info_text("") == ""
    assert file_info_text(str(tmp_path / "nope")) == ""
    assert file_info_text("a.tif, b.tif (2 files)") == ""


def test_icon_follows_text(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hi")
    ed = QtWidgets.QLineEdit()
    attach_file_info(ed)
    attach_file_info(ed)                       # idempotent
    acts = [a for a in ed.actions()]
    assert len(acts) == 1
    ed.setText(str(f))
    assert acts[0].isVisible() and "a.txt" in acts[0].toolTip()
    ed.setText(str(tmp_path / "gone"))
    assert not acts[0].isVisible()


def test_warn_if_path_missing_attaches_for_inputs_only():
    a, b = QtWidgets.QLineEdit(), QtWidgets.QLineEdit()
    w = QtWidgets.QWidget()
    warn_if_path_missing(a, w)
    warn_if_path_missing(b, w, is_output_dir=True)
    assert len(a.actions()) == 1 and len(b.actions()) == 0
