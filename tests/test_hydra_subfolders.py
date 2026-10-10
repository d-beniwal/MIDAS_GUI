"""Hydra can take a tree of scans, not just one file.

Reported at 1-ID-E: "In hydra mode, I do not get an option to look at all
the subfolders like in the case of single panel mode."

It was deliberate but narrow — the Hydra picker was modes=("file",) because
one scan is ONE anchor file whose frames come from its own internal count,
not from separate per-frame files. But a beamline root is often nothing but
per-load-step subfolders, each holding its own ge1–ge4 set, and there was
no way to say "all of them". The single-detector tab learned the same
lesson in 9302e64, where 23 load steps landed in one output directory.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from midas_gui.hydra import group_into_scans, panel_for_file, scan_key


def _tree(n_steps=3, panels=(1, 2, 3, 4)):
    out = []
    for s in range(n_steps):
        for p in panels:
            out.append(f"/root/load_step_{s}/scan_{s:06d}.ge{p}.h5")
    return sorted(out)


# ── grouping a flat list back into scans ────────────────────────────────
def test_four_files_per_folder_are_one_scan_not_four():
    scans = group_into_scans(_tree())
    assert len(scans) == 3
    assert all(sorted(sc) == [1, 2, 3, 4] for sc in scans)


def test_scans_come_back_in_path_order():
    """The dialog hands over a sorted list; load_step_0 must run first."""
    scans = group_into_scans(_tree())
    firsts = [Path(sc[1]).parent.name for sc in scans]
    assert firsts == ["load_step_0", "load_step_1", "load_step_2"]


def test_every_panel_of_a_scan_shares_one_key():
    keys = {scan_key(f"/r/s_000001.ge{n}.h5") for n in (1, 2, 3, 4)}
    assert len(keys) == 1


def test_a_file_with_no_panel_token_is_ignored():
    scans = group_into_scans(_tree() + ["/root/notes.txt", "/root/README"])
    assert len(scans) == 3


def test_a_partial_set_is_kept_as_partial_not_completed_from_disk():
    """Built from the SELECTION: a scan whose ge3 was not picked comes back
    with three panels rather than quietly gaining a fourth nobody chose."""
    scans = group_into_scans(["/r/a_ge1.h5", "/r/a_ge2.h5", "/r/b_ge1.h5"])
    assert sorted(len(sc) for sc in scans) == [1, 2]


def test_an_empty_or_junk_selection_yields_nothing():
    assert group_into_scans([]) == []
    assert group_into_scans(["/r/notes.txt"]) == []


def test_one_file_still_yields_exactly_one_scan():
    """The single-pick path must stay a queue of one, so the run never has
    to branch on how the data was chosen."""
    scans = group_into_scans(["/r/s_000001.ge2.h5"])
    assert len(scans) == 1 and scans[0] == {2: "/r/s_000001.ge2.h5"}


# ── the page's per-scan output layout ───────────────────────────────────
pytestmark = pytest.mark.forked


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_each_scan_gets_its_own_output_folder(app, tmp_path):
    """The <expid>_bc/<froot> convention reads froot off each file's own
    path, so many folders mean many output dirs. Applying it once is the
    bug 9302e64 fixed for single detectors."""
    from midas_gui.hydra_batch_page import HydraBatchPage
    page = HydraBatchPage()
    page._out_ed.setText(str(tmp_path / "out"))
    bases = page._out_bases_for_scans(group_into_scans(_tree()))
    assert len(set(bases)) == 3, "three scans must not share one output dir"
    assert all(str(tmp_path / "out") in b for b in bases), \
        "the typed Output box is the root they fan out beneath"


def test_scans_whose_froot_is_identical_are_still_kept_apart(app, tmp_path):
    """froot deliberately excludes the file number (499a250), so two load
    steps of the same stem derive the SAME froot. Left alone that is the
    original bug again: everything in one directory."""
    from midas_gui.hydra_batch_page import HydraBatchPage
    page = HydraBatchPage()
    page._out_ed.setText(str(tmp_path / "out"))
    sel = []
    for step, num in ((0, 3502), (1, 3503), (2, 3504)):
        for p in (1, 2, 3, 4):
            sel.append(f"/root/load_step_{step}/CeO2_1s_{num:06d}.ge{p}.h5")
    bases = page._out_bases_for_scans(group_into_scans(sorted(sel)))
    assert len(set(bases)) == 3, bases
    assert any("load_step_1" in b for b in bases), \
        "the source folder is what tells them apart"


def test_two_scans_in_one_folder_are_told_apart_by_file_number(app, tmp_path):
    """Same folder AND same froot — only the number is left."""
    from midas_gui.hydra_batch_page import HydraBatchPage
    page = HydraBatchPage()
    page._out_ed.setText(str(tmp_path / "out"))
    sel = [f"/root/run/CeO2_1s_{num:06d}.ge{p}.h5"
           for num in (3502, 3503) for p in (1, 2, 3, 4)]
    bases = page._out_bases_for_scans(group_into_scans(sorted(sel)))
    assert len(set(bases)) == 2, bases


def test_a_single_scan_uses_the_output_box_exactly_as_typed(app, tmp_path):
    from midas_gui.hydra_batch_page import HydraBatchPage
    page = HydraBatchPage()
    page._out_ed.setText(str(tmp_path / "out"))
    one = group_into_scans(["/r/s_000001.ge1.h5"])
    assert page._out_bases_for_scans(one) == [str(tmp_path / "out")]


def test_the_source_descriptor_follows_the_active_scan(app):
    from midas_gui.hydra_batch_page import HydraBatchPage
    page = HydraBatchPage()
    page._active_scan = {1: "/r/load_step_1/s.ge1.h5", 2: "/r/load_step_1/s.ge2.h5"}
    cfg = page._source_cfg_for(1)
    assert cfg["path"] == "/r/load_step_1/s.ge1.h5"
    assert cfg["type"] == "hdf5"
    assert page._source_cfg_for(3) == {"path": ""}, "a panel not in this scan"


def test_with_no_active_scan_the_loader_is_still_the_authority(app):
    from midas_gui.hydra_batch_page import HydraBatchPage
    page = HydraBatchPage()
    page._active_scan = None
    assert page._source_cfg_for(1) == page._loader.source_cfg(1)
