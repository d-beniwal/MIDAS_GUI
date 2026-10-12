"""A detector tag ending in a digit must not defeat the frame-number parse.

Reported at 1-ID-E: "for caking output folder, we should still use the froot
and not include the file number." The Hydra Output field read
``..._1s_120x120um_1s_003502`` -- the file number baked into the folder name.

``_FID_NUM_RE``'s tag group excluded digits, so ``.ge1`` could not match it
and the whole pattern failed; froot_and_frame_num fell back to
``(whole stem, fallback_idx, "")``. ``.pixi``/``.vrx`` carry no digit and
were always parsed correctly, which is why this only ever showed on GE data.
"""
import pytest

from midas_gui.workers import froot_and_frame_num

GE = "connolly_oct26_CeO2_80p725keV_2300mm_1s_120x120um_1s_003502.ge1"
FROOT = "connolly_oct26_CeO2_80p725keV_2300mm_1s_120x120um_1s"


def test_a_ge_panel_tag_still_yields_froot_and_number():
    assert froot_and_frame_num(GE, 0) == (FROOT, 3502, ".ge1")


@pytest.mark.parametrize("n", [1, 2, 3, 4])
def test_every_hydra_panel_parses_the_same_way(n):
    froot, num, tag = froot_and_frame_num(f"{FROOT}_003502.ge{n}", 0)
    assert (froot, num) == (FROOT, 3502)
    assert tag == f".ge{n}"


# ── the cases that already worked must be unchanged ──────────────────────
@pytest.mark.parametrize("fid,expected", [
    ("APS_4130_C_load_step_0_004158.pixi", ("APS_4130_C_load_step_0", 4158, ".pixi")),
    ("C611_017Fe_1_load3_009243.vrx", ("C611_017Fe_1_load3", 9243, ".vrx")),
    (f"{FROOT}_003502", (FROOT, 3502, "")),
    ("scan_000045", ("scan", 45, "")),
])
def test_existing_conventions_are_untouched(fid, expected):
    assert froot_and_frame_num(fid, 0) == expected


def test_the_last_number_group_still_wins():
    """Excluding '_' from the tag is what anchors the match to the LAST
    underscore-number group; allowing digits must not relax that."""
    assert froot_and_frame_num("run_12_34", 0) == ("run_12", 34, "")


def test_a_chunk_suffix_still_keeps_the_stem_number():
    froot, num, tag = froot_and_frame_num(f"{FROOT}_003502.ge1.frame_0_9", 0)
    assert (froot, num) == (FROOT, 3502)
    assert tag == ".ge1.frame_0_9"


def test_a_non_numeric_id_still_falls_back():
    assert froot_and_frame_num("no_digits_here", 7) == ("no_digits_here", 7, "")
