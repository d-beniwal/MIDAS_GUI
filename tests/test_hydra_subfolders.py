"""A Hydra batch is a FOLDER, and its output is one directory.

Reported at 1-ID-E, looking at connolly_oct26_bc/APS_4130_C_load_step_0:
"In this folder there should be ge1, ge2, ge3, and ge4 folder ... There
should also be a ge1234 zarr file in the APS_4130_C_load_step_0 folder.
There should not be files / folders related to other froot / file prefix."

What the first version of the fan-out actually produced there:

    APS_4130_C_load_step_0/
      APS_4130_C_load_step_0_APS_4130_C_load_step_0_APS_4130_C_load_step_0_004057.ge1/
      ... one per file number ...
      APS_4130_C_load_step_1_APS_4130_C_load_step_1_..._004072.ge1/

Three faults, all from grouping per ge1-ge4 QUARTET instead of per source
folder, and from deriving names by disambiguating them against each other
instead of from the convention:

  * thirteen output directories for one load step, one per frame file
  * names repeated three times over
  * load_step_1's output nested inside load_step_0, because the Output box
    held a value the grouper could not recognise as its own derived dir
    and so read as a chosen root

helpers.group_paths_by_output_dir already got all three right for the
single-detector tab. Hydra now uses it, passing the panel-base deriver
because each of its panels appends its own ge{n}/.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from midas_gui.hydra import panel_for_file, split_by_panel

pytestmark = pytest.mark.forked


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _tree(root, steps=((0, range(4057, 4070)), (1, range(4072, 4078)))):
    """connolly_oct26's real shape: load-step folders of many frame files."""
    h5py = pytest.importorskip("h5py")
    import numpy as np
    files = []
    for step, nums in steps:
        d = Path(root) / f"APS_4130_C_load_step_{step}"
        d.mkdir(parents=True, exist_ok=True)
        for num in nums:
            for p in (1, 2, 3, 4):
                f = d / f"APS_4130_C_load_step_{step}_{num:06d}.ge{p}.h5"
                with h5py.File(f, "w") as h:
                    h.create_dataset("exchange/data",
                                     data=np.zeros((1, 4, 4), "f4"))
                files.append(str(f))
    return files


# ── a folder's files split into four parallel frame lists ──────────────
def test_every_file_of_a_panel_is_a_frame_of_one_scan(tmp_path):
    files = _tree(tmp_path)
    step0 = [f for f in files if "load_step_0" in f]
    panels = split_by_panel(step0)
    assert sorted(panels) == [1, 2, 3, 4]
    assert all(len(v) == 13 for v in panels.values()), \
        "thirteen frame files per panel, not thirteen scans"


def test_files_with_no_panel_token_are_ignored(tmp_path):
    panels = split_by_panel(["/r/a_ge1.h5", "/r/notes.txt"])
    assert sorted(panels) == [1]


def test_the_frames_of_a_panel_come_back_in_order(tmp_path):
    files = _tree(tmp_path, steps=((0, range(4057, 4062)),))
    got = split_by_panel(files)[1]
    assert got == sorted(got)


# ── one output directory per folder, named by the convention ───────────
@pytest.fixture
def planned(app, tmp_path):
    from midas_gui.hydra_batch_page import HydraBatchPage
    files = _tree(tmp_path)
    page = HydraBatchPage()
    page._loader.set_selection(files)
    return page


def test_one_output_directory_per_load_step(planned):
    plan = planned._scan_plan()
    assert len(plan) == 2, "two folders, two batches — not one per file"
    assert len({out for out, _ in plan}) == 2


def test_the_directory_is_named_for_its_own_load_step(planned):
    for out_dir, panels in planned._scan_plan():
        src = Path(panels[1][0]).parent.name
        assert Path(out_dir).name == src, (out_dir, src)


def test_the_name_is_not_repeated_three_times_over(planned):
    """APS_4130_C_load_step_0_APS_4130_C_load_step_0_APS_4130_C_load_step_0_…"""
    for out_dir, _ in planned._scan_plan():
        name = Path(out_dir).name
        assert name.count("APS_4130_C_load_step") == 1, name
        assert ".ge" not in name, "a file name must not become a directory"


def test_a_stale_autofilled_output_box_does_not_nest_the_others_in_it(planned):
    """The box auto-fills from the first file, so it holds load_step_0's own
    directory. Reading that as a chosen root is what put load_step_1's
    output inside load_step_0."""
    auto = planned._suggest_output_dir()
    planned._out_ed.setText(str(auto))
    outs = [str(o) for o, _ in planned._scan_plan()]
    assert len({Path(o).parent for o in outs}) == 1, \
        "the two load steps must be siblings, not nested"
    assert not any(Path(o) != Path(str(auto)) and str(auto) in o for o in outs)


def test_a_genuinely_custom_root_still_fans_out_under_it(planned, tmp_path):
    root = tmp_path / "elsewhere"
    planned._out_ed.setText(str(root))
    outs = [str(o) for o, _ in planned._scan_plan()]
    assert all(str(root) in o for o in outs)
    assert len(set(outs)) == 2


# ── the panels' sources ─────────────────────────────────────────────────
def test_a_panel_with_many_files_becomes_a_stack_glob(planned):
    plan = planned._scan_plan()
    planned._active_scan = plan[0][1]
    cfg = planned._source_cfg_for(1)
    assert cfg["type"] == "hdf5_stack_glob"
    assert len(cfg["paths"]) == 13


def test_a_single_file_panel_stays_a_plain_hdf5_source(planned, tmp_path):
    planned._active_scan = {1: [str(next(tmp_path.rglob("*_004057.ge1.h5")))]}
    assert planned._source_cfg_for(1)["type"] == "hdf5"


def test_the_chunk_settings_reach_a_multi_file_source(planned):
    planned._loader._combine_chunk.setValue(3)
    plan = planned._scan_plan()
    planned._active_scan = plan[0][1]
    assert planned._source_cfg_for(1)["chunk_size"] == 3


def test_a_panel_absent_from_the_scan_yields_no_path(planned):
    planned._active_scan = {1: ["/r/a_ge1.h5"]}
    assert planned._source_cfg_for(3) == {"path": ""}


# ── three faults from one real run, all silent ─────────────────────────
#     Starting Hydra batch integration (parallel)…
#     [ge1] no data — skipped
#     [combine] no frame had an archive from every panel — nothing to combine.
#     Hydra batch integration run complete.
#
# One panel named, three never mentioned, and a "complete" run that wrote
# nothing.

def test_picking_one_file_still_runs_all_four_panels(app, tmp_path):
    """A pick of one ge1 file IS a four-panel scan — the premise of Hydra
    mode. Handing the run the raw selection instead of its siblings gave a
    plan containing only ge1."""
    from midas_gui.hydra_batch_page import HydraBatchPage
    files = _tree(tmp_path, steps=((0, range(4057, 4060)),))
    one = [f for f in files if f.endswith("_004057.ge1.h5")]
    page = HydraBatchPage()
    page._loader.set_selection(one)
    plan = page._scan_plan()
    assert len(plan) == 1
    assert sorted(plan[0][1]) == [1, 2, 3, 4], "siblings must be expanded"


def test_expanding_an_already_complete_pick_is_idempotent(app, tmp_path):
    from midas_gui.hydra_batch_page import HydraBatchPage
    files = _tree(tmp_path, steps=((0, range(4057, 4060)),))
    page = HydraBatchPage()
    page._loader.set_selection(files)
    panels = page._scan_plan()[0][1]
    assert [len(panels[k]) for k in sorted(panels)] == [3, 3, 3, 3], \
        "re-expanding must not duplicate files"


def test_a_multi_file_panel_is_not_mistaken_for_no_data(app, tmp_path):
    """hdf5_stack_glob carries `paths` and no `path` at all. Testing only
    `path` skipped every multi-file panel as "no data" — i.e. exactly the
    folder fan-out this feature exists for."""
    from midas_gui.hydra_batch_page import HydraBatchPage
    files = _tree(tmp_path, steps=((0, range(4057, 4062)),))
    page = HydraBatchPage()
    page._loader.set_selection(files)
    page._active_scan = page._scan_plan()[0][1]
    for n in (1, 2, 3, 4):
        cfg = page._source_cfg_for(n)
        assert cfg.get("path") or cfg.get("paths"), f"ge{n} read as no data"


def test_a_parallel_scan_does_not_finish_while_it_is_still_launching(app,
                                                                     tmp_path):
    """_start_panel_worker can finish synchronously (a skip), and
    _maybe_finish_run then saw no workers and no pending and called the
    scan done — combining, and with a queue behind it starting the NEXT
    scan while this one's other panels had not been started."""
    from midas_gui.hydra_batch_page import HydraBatchPage
    page = HydraBatchPage()
    finished = []
    page._finish_whole_run = lambda: finished.append(1)
    page._launching = True
    page._workers = {}
    page._pending_panels = []
    page._scan_queue = []
    page._maybe_finish_run()
    assert not finished, "must not finish mid-launch"
    page._launching = False
    page._maybe_finish_run()
    assert finished, "and must finish once launching is done"


# ── the per-panel-root layout, and what the status line claims ─────────
# "chose 1x folder but the files in it were not processed."
#
# The real tree puts the panel ABOVE the scan folder:
#     connolly_oct26/ge1/APS_4130_C_load_step_0/..._004057.ge1.h5
# so a "Full folder" pick lands inside ge1 and names 14 files.

def _panel_root_tree(root, n_files=14, n_sub=3):
    h5py = pytest.importorskip("h5py")
    import numpy as np
    picked = []
    for p in (1, 2, 3, 4):
        d = Path(root) / f"ge{p}" / "APS_4130_C_load_step_0"
        d.mkdir(parents=True, exist_ok=True)
        for num in range(4057, 4057 + n_files):
            f = d / f"APS_4130_C_load_step_0_{num:06d}.ge{p}.h5"
            with h5py.File(f, "w") as h:
                h.create_dataset("exchange/data",
                                 data=np.zeros((n_sub, 4, 4), "f4"))
            if p == 1:
                picked.append(str(f))
    return picked


def test_a_pick_inside_ge1_still_finds_the_other_three_panels(app, tmp_path):
    """The ge token is in the FOLDER here, not only the filename."""
    from midas_gui.hydra_batch_page import HydraBatchPage
    picked = _panel_root_tree(tmp_path)
    page = HydraBatchPage()
    page._loader.set_selection(picked)
    plan = page._scan_plan()
    assert len(plan) == 1, "four panel folders are ONE scan, not four"
    panels = plan[0][1]
    assert sorted(panels) == [1, 2, 3, 4]
    assert [len(panels[k]) for k in sorted(panels)] == [14, 14, 14, 14]


def test_that_pick_opens_as_one_source_of_every_frame(app, tmp_path):
    from midas_gui.workers import _open_source_cfg
    from midas_gui.hydra_batch_page import HydraBatchPage
    picked = _panel_root_tree(tmp_path, n_files=5, n_sub=3)
    page = HydraBatchPage()
    page._loader.set_selection(picked)
    page._active_scan = page._scan_plan()[0][1]
    src = _open_source_cfg(page._source_cfg_for(1))
    assert src.n_frames == 15, "5 files x 3 sub-frames"


def test_the_status_line_counts_the_selection_not_the_anchor(app, tmp_path):
    """_n_frames is the ANCHOR file's own sub-frame count — right for the
    frame slider, wrong as a status line the moment a pick covers more
    than one file. It read "3 frame(s)" for fourteen 3-frame files, which
    reads exactly like the other thirteen were ignored."""
    from midas_gui.hydra_batch_page import HydraBatchPage
    picked = _panel_root_tree(tmp_path, n_files=14, n_sub=3)
    page = HydraBatchPage()
    page._loader.set_selection(picked)
    txt = page._loader._info_lbl.text()
    assert "14 files" in txt and "42 frame(s)" in txt, txt


def test_a_single_file_pick_still_reads_as_before(app, tmp_path):
    from midas_gui.hydra_batch_page import HydraBatchPage
    picked = _panel_root_tree(tmp_path, n_files=2, n_sub=3)
    page = HydraBatchPage()
    page._loader.set_path(picked[0])
    txt = page._loader._info_lbl.text()
    assert "3 frame(s)" in txt and "files" not in txt, txt


# ── a stale Output box silently redirects the run ──────────────────────
# "the run produced APS_4130_C_load_step_0 even though I specified
#  .../ge1/4130_D_load_step_0/4130_D_load_step_0_005120.ge1.h5"
#
# The box is AUTHORITATIVE for a single scan, so a leftover value does not
# merely look wrong — it writes this dataset's output into another's
# folder. _autofilled_out (the "did WE fill this in" marker) was a plain
# attribute while out_ed was persisted, so after any restore the box read
# as a deliberate choice and _maybe_autofill_output_dir returned early
# forever.

def _one_scan(tmp_path, step):
    h5py = pytest.importorskip("h5py")
    import numpy as np
    for p in (1, 2, 3, 4):
        d = Path(tmp_path) / f"ge{p}" / step
        d.mkdir(parents=True, exist_ok=True)
        with h5py.File(d / f"{step}_005120.ge{p}.h5", "w") as h:
            h.create_dataset("exchange/data", data=np.zeros((2, 4, 4), "f4"))
    return str(Path(tmp_path) / "ge1" / step / f"{step}_005120.ge1.h5")


def test_the_autofill_marker_survives_a_restore(app, tmp_path):
    from midas_gui.hydra_batch_page import HydraBatchPage
    old = _one_scan(tmp_path, "APS_4130_C_load_step_0")
    new = _one_scan(tmp_path, "4130_D_load_step_0")
    a = HydraBatchPage()
    a._loader.set_path(old)
    assert "APS_4130_C_load_step_0" in a._out_ed.text()
    state = a.get_state()
    assert "autofilled_out" in state, "the marker is not a widget"

    b = HydraBatchPage()
    b.set_state(state)
    b._loader.set_path(new)
    assert Path(b._out_ed.text()).name == "4130_D_load_step_0", \
        "a restored autofill must still re-fill for new data"


def test_a_hand_typed_box_is_respected_but_flagged(app, tmp_path):
    """A path the user chose is never overwritten — so the warning is the
    only thing standing between them and output in the wrong dataset."""
    from midas_gui.hydra_batch_page import HydraBatchPage
    new = _one_scan(tmp_path, "4130_D_load_step_0")
    page = HydraBatchPage()
    page._out_ed.setText("/somewhere/APS_4130_C_load_step_0")
    page._loader.set_path(new)
    assert page._out_ed.text() == "/somewhere/APS_4130_C_load_step_0"
    assert not page._out_mismatch_lbl.isHidden()
    txt = page._out_mismatch_lbl.text()
    assert "APS_4130_C_load_step_0" in txt and "4130_D_load_step_0" in txt


def test_clearing_the_box_re_fills_it_and_clears_the_warning(app, tmp_path):
    from midas_gui.hydra_batch_page import HydraBatchPage
    new = _one_scan(tmp_path, "4130_D_load_step_0")
    page = HydraBatchPage()
    page._out_ed.setText("/somewhere/APS_4130_C_load_step_0")
    page._loader.set_path(new)
    page._out_ed.setText("")
    page._loader.set_path(new)
    assert Path(page._out_ed.text()).name == "4130_D_load_step_0"
    assert page._out_mismatch_lbl.isHidden()


def test_a_matching_box_says_nothing(app, tmp_path):
    from midas_gui.hydra_batch_page import HydraBatchPage
    new = _one_scan(tmp_path, "4130_D_load_step_0")
    page = HydraBatchPage()
    page._loader.set_path(new)
    assert page._out_mismatch_lbl.isHidden()


def test_only_a_legacy_restore_lets_the_box_be_replaced(app, tmp_path):
    """A project saved before _autofilled_out existed has no key at all,
    and that ABSENCE is what marks it legacy. A fresh page also starts
    with no marker, so keying on the marker alone overwrote a path typed
    into one — the opposite of the rule."""
    from midas_gui.hydra_batch_page import HydraBatchPage
    new = _one_scan(tmp_path, "4130_D_load_step_0")

    legacy = HydraBatchPage()
    legacy.set_state({"fields": {"out_ed": "/x/APS_4130_C_load_step_0"}})
    assert legacy._out_state_was_legacy
    legacy._loader.set_path(new)
    assert Path(legacy._out_ed.text()).name == "4130_D_load_step_0"

    modern = HydraBatchPage()
    modern.set_state({"fields": {"out_ed": "/x/APS_4130_C_load_step_0"},
                      "autofilled_out": None})
    assert not modern._out_state_was_legacy
    modern._loader.set_path(new)
    assert modern._out_ed.text() == "/x/APS_4130_C_load_step_0", \
        "a recorded choice is never overwritten"
