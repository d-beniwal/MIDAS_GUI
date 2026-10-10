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
