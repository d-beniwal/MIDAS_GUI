"""One pick loads four panel calibrations, and a gap is an alert.

Asked for at 1-ID-E: "we should be able to load the calibration file from
composite. Pick one for one panel and find others automatically by pattern.
If file that matches the pattern does not exist alert."

The Composite view is the one that needs all four calibrations at once --
it cannot be built without them -- and it was the only view with no way to
supply them, the per-panel cards being hidden behind it.

The matching rule lives in midas_gui.hydra so the Data Viewer's existing
"Load 4 panel calibrations…" and this share it rather than drifting apart.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from midas_gui.hydra import panel_for_file, sibling_for_panel


# ── the rule ────────────────────────────────────────────────────────────
def test_the_panel_comes_from_the_filename():
    assert panel_for_file("/d/scan_003502.ge1.instr.txt") == 1
    assert panel_for_file("/d/scan_003502.ge4.instr.txt") == 4


def test_the_last_token_wins_over_an_earlier_one():
    """A run folder that happens to say ge1 must not out-vote the file's
    own name -- a calibration on the wrong panel is a composite that looks
    plausible and is wrong."""
    assert panel_for_file("/ge1_run/scan_ge4.instr.txt") == 4


def test_a_path_with_no_token_is_not_guessed_at():
    assert panel_for_file("/d/scan.instr.txt") is None
    assert sibling_for_panel("/d/scan.instr.txt", 2) is None


def test_siblings_are_named_by_substitution():
    got = sibling_for_panel("/d/run/scan_003502.ge1.instr.txt", 3)
    assert got == "/d/run/scan_003502.ge3.instr.txt"


def test_a_panel_in_both_folder_and_filename_is_rewritten_consistently():
    """Half-rewriting would point at /d/ge3/scan_ge1... which exists for
    nobody."""
    assert sibling_for_panel("/d/ge3/scan_ge3.instr.txt", 1) \
        == "/d/ge1/scan_ge1.instr.txt"


def test_naming_a_sibling_says_nothing_about_it_existing():
    """Deliberately separate claims: the caller checks the disk, because
    "a name can be formed" and "a file is there" are different, and only
    the second one may be acted on."""
    assert sibling_for_panel("/nowhere/x_ge1.txt", 2) == "/nowhere/x_ge2.txt"
    assert not Path("/nowhere/x_ge2.txt").exists()


# ── the page ────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


pytestmark = pytest.mark.forked


def _calib_text():
    return ("Lsd 1000000.0\nBC 128.0 128.0\npx 200.0\n"
            "NrPixelsY 256\nNrPixelsZ 256\ntx 0.0\nty 0.0\ntz 0.0\n"
            "Wavelength 1.0\n")


def test_one_pick_loads_every_panel_that_is_there(app, tmp_path, monkeypatch):
    from PyQt5 import QtWidgets
    from midas_gui.hydra_batch_page import HydraBatchPage
    for n in (1, 2, 3, 4):
        (tmp_path / f"scan_000001.ge{n}.instr.txt").write_text(_calib_text())
    page = HydraBatchPage()
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (str(tmp_path / "scan_000001.ge1.instr.txt"), "")))
    alerts = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: alerts.append(a[2])))
    page._load_four_calibrations()
    for n in (1, 2, 3, 4):
        assert page._cards[n].file_path().endswith(f"ge{n}.instr.txt")
        assert page._cards[n].using_file(), f"ge{n} must switch to the file"
    assert not alerts, "a complete set must not nag"


def test_a_missing_sibling_raises_an_alert_naming_it(app, tmp_path, monkeypatch):
    """The ask was explicit: "If file that matches the pattern does not
    exist alert." A log line would not do -- the panels that did not load
    keep whatever they had, and a composite built from one stale
    calibration still looks entirely plausible."""
    from PyQt5 import QtWidgets
    from midas_gui.hydra_batch_page import HydraBatchPage
    for n in (1, 2, 4):                       # ge3 deliberately absent
        (tmp_path / f"scan_000001.ge{n}.instr.txt").write_text(_calib_text())
    page = HydraBatchPage()
    monkeypatch.setattr(
        QtWidgets.QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (str(tmp_path / "scan_000001.ge1.instr.txt"), "")))
    alerts = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: alerts.append(a[2])))
    page._load_four_calibrations()
    assert len(alerts) == 1
    assert "ge3" in alerts[0]
    assert "scan_000001.ge3.instr.txt" in alerts[0], "name the path it wanted"
    assert page._cards[1].file_path().endswith("ge1.instr.txt")
    assert page._cards[3].file_path() == "", "ge3 must be left as it was"


def test_a_file_with_no_panel_token_is_refused_with_an_alert(app, tmp_path,
                                                             monkeypatch):
    from PyQt5 import QtWidgets
    from midas_gui.hydra_batch_page import HydraBatchPage
    odd = tmp_path / "calibration.instr.txt"
    odd.write_text(_calib_text())
    page = HydraBatchPage()
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(odd), "")))
    alerts = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: alerts.append(a[2])))
    page._load_four_calibrations()
    assert len(alerts) == 1 and "ge1" in alerts[0]
    assert all(page._cards[n].file_path() == "" for n in (1, 2, 3, 4))


def test_cancelling_the_dialog_changes_nothing(app, tmp_path, monkeypatch):
    from PyQt5 import QtWidgets
    from midas_gui.hydra_batch_page import HydraBatchPage
    page = HydraBatchPage()
    monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: ("", "")))
    called = []
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        staticmethod(lambda *a, **k: called.append(1)))
    page._load_four_calibrations()
    assert not called
    assert all(page._cards[n].file_path() == "" for n in (1, 2, 3, 4))
