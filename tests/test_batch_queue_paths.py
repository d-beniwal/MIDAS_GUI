"""Batch Queue input→output path mirroring.

Every sample gets its own output directory, at the same position in the output
tree that it occupies in the input tree. Getting this wrong is expensive in a
way tests are cheap: two samples silently mapping to one directory means the
second overwrites the first's csv/zarr files hours into an overnight run, and
nothing errors. So the collision case is pinned as hard as the happy path.

Pure logic, no Qt and no filesystem access — ``mirror_output_dir`` uses
``os.path.abspath`` rather than ``Path.resolve`` precisely so it can be reasoned
about for an output tree that does not exist yet.
"""
import os
from pathlib import Path

import pytest

from midas_gui.batch_queue import (
    BatchQueue, CalibrationNode, CorrectionsNode, Sample, UNROOTED_DIR,
    infer_data_root, is_unrooted, mirror_output_dir, plan_output_dirs,
    project_path_for, sample_target,
)


def _q(samples, *, data_root="/data/bt", out_root="/results/bt"):
    return BatchQueue(
        calibrations=[CalibrationNode(corrections=[CorrectionsNode(samples=samples)])],
        data_root=data_root, out_root=out_root)


# ── where a sample sits in the input tree ────────────────────────────

@pytest.mark.parametrize("path, target", [
    ("/data/bt/s1/scan_001.h5", "/data/bt/s1/scan_001"),
    ("/data/bt/s1/scan_001.vrx.h5", "/data/bt/s1/scan_001.vrx"),
    ("/data/bt/tiffs/sampleA", "/data/bt/tiffs/sampleA"),
    ("/data/bt/tiffs/sampleA/", "/data/bt/tiffs/sampleA"),
])
def test_sample_target(path, target):
    """An HDF5 file's output dir is named after it with the suffix dropped; a
    folder sample is already directory-shaped."""
    assert sample_target(path) == Path(target)


def test_sample_target_collapses_dot_dot_without_touching_disk():
    assert sample_target("/data/bt/s1/../s2/scan.h5") == Path("/data/bt/s2/scan")


# ── the mapping ──────────────────────────────────────────────────────

def test_hdf5_sample_mirrors_into_its_own_directory():
    assert mirror_output_dir("/data/bt/s1/scan_001.h5", "/data/bt", "/results/bt") == (
        Path("/results/bt/s1/scan_001"))


def test_folder_sample_mirrors_to_the_same_relative_position():
    assert mirror_output_dir("/data/bt/tiffs/sampleA", "/data/bt", "/results/bt") == (
        Path("/results/bt/tiffs/sampleA"))


def test_deep_hierarchy_is_preserved():
    assert mirror_output_dir("/data/bt/a/b/c/scan.h5", "/data/bt", "/out") == (
        Path("/out/a/b/c/scan"))


def test_sample_directly_under_the_data_root():
    assert mirror_output_dir("/data/bt/scan.h5", "/data/bt", "/out") == Path("/out/scan")


def test_trailing_slash_on_the_roots_does_not_change_the_result():
    assert mirror_output_dir("/data/bt/s1/scan.h5", "/data/bt/", "/out/") == (
        Path("/out/s1/scan"))


# ── the fallback ─────────────────────────────────────────────────────

def test_sample_outside_the_data_root_goes_to_unrooted():
    """A path dragged in from another volume must not be silently flattened
    next to the properly-mirrored samples."""
    out = mirror_output_dir("/elsewhere/scan.h5", "/data/bt", "/results/bt")
    assert out == Path(f"/results/bt/{UNROOTED_DIR}/scan")
    assert is_unrooted(out, "/results/bt")


def test_a_sample_that_is_itself_the_data_root_goes_to_unrooted():
    """It would otherwise mirror onto the output root itself and collide with
    every other sample."""
    out = mirror_output_dir("/data/bt", "/data/bt", "/out")
    assert out == Path(f"/out/{UNROOTED_DIR}/bt")


def test_no_data_root_at_all_goes_to_unrooted():
    assert mirror_output_dir("/data/bt/s1/scan.h5", None, "/out") == (
        Path(f"/out/{UNROOTED_DIR}/scan"))


def test_properly_mirrored_samples_are_not_flagged_unrooted():
    out = mirror_output_dir("/data/bt/s1/scan.h5", "/data/bt", "/out")
    assert not is_unrooted(out, "/out")


def test_unrooted_uses_the_custom_label_when_one_is_set():
    out = mirror_output_dir("/elsewhere/scan.h5", "/data/bt", "/out", label="my sample")
    assert out == Path(f"/out/{UNROOTED_DIR}/my sample")


# ── data-root inference ──────────────────────────────────────────────

def test_data_root_is_the_common_ancestor_of_the_samples():
    assert infer_data_root(["/data/bt/s1/scan_001.h5",
                            "/data/bt/s1/scan_002.h5",
                            "/data/bt/tiffs/sampleA"]) == "/data/bt"


def test_one_sample_infers_its_parent_so_its_own_name_survives():
    """Inferring the sample itself would leave an empty relative part."""
    root = infer_data_root(["/data/bt/s1/scan_001.h5"])
    assert root == "/data/bt/s1"
    assert mirror_output_dir("/data/bt/s1/scan_001.h5", root, "/out") == Path("/out/scan_001")


def test_siblings_in_one_folder_infer_that_folder():
    root = infer_data_root(["/d/a.h5", "/d/b.h5"])
    assert root == "/d"
    assert mirror_output_dir("/d/a.h5", root, "/out") == Path("/out/a")
    assert mirror_output_dir("/d/b.h5", root, "/out") == Path("/out/b")


def test_no_samples_infers_nothing():
    assert infer_data_root([]) is None


def test_unrelated_absolute_paths_still_share_the_filesystem_root():
    assert infer_data_root(["/a/x.h5", "/b/y.h5"]) == os.sep


# ── the per-sample project file ──────────────────────────────────────

def test_project_file_sits_inside_the_sample_directory():
    assert project_path_for("/out/s1/scan_001", "scan_001") == (
        Path("/out/s1/scan_001/scan_001.h5"))


def test_project_file_name_defaults_to_the_directory_name():
    assert project_path_for("/out/s1/scan_001") == Path("/out/s1/scan_001/scan_001.h5")


@pytest.mark.parametrize("label", ["h5", "csv", "zarr", "H5", "2d_csv"])
def test_a_label_colliding_with_an_output_subfolder_is_renamed(label):
    """BatchWorker writes csv/, zarr/, h5/ … inside the sample directory; a
    sample labelled 'h5' would want its project at exactly the path of the
    h5/ directory."""
    out = project_path_for(f"/out/{label}", label)
    assert out.name == f"{label}_project.h5"


def test_an_ordinary_label_is_left_alone():
    assert project_path_for("/out/h5py_scan", "h5py_scan").name == "h5py_scan.h5"


def test_project_file_never_collides_with_the_h5_output_subfolder():
    out_dir = Path("/out/s1/scan_001")
    for label in ("scan_001", "h5", "csv"):
        assert project_path_for(out_dir, label) != out_dir / "h5"


# ── planning a whole queue ───────────────────────────────────────────

def test_plan_maps_every_enabled_sample():
    q = _q([Sample("/data/bt/s1/scan_001.h5"), Sample("/data/bt/tiffs/sampleA")])
    rows, problems = plan_output_dirs(q)
    assert problems == []
    assert [(s.label, str(d)) for _c, _k, s, d in rows] == [
        ("scan_001", "/results/bt/s1/scan_001"),
        ("sampleA", "/results/bt/tiffs/sampleA")]


def test_plan_skips_disabled_samples():
    q = _q([Sample("/data/bt/a.h5"), Sample("/data/bt/b.h5", enabled=False)])
    rows, problems = plan_output_dirs(q)
    assert problems == [] and [s.label for _c, _k, s, _d in rows] == ["a"]


def test_plan_infers_the_data_root_when_none_is_set():
    q = _q([Sample("/data/bt/s1/scan_001.h5"), Sample("/data/bt/s2/scan_002.h5")],
           data_root=None)
    rows, problems = plan_output_dirs(q)
    assert problems == []
    assert [str(d) for _c, _k, _s, d in rows] == [
        "/results/bt/s1/scan_001", "/results/bt/s2/scan_002"]


def test_plan_blocks_when_two_samples_want_the_same_directory():
    """The expensive silent failure: the second sample overwrites the first."""
    q = _q([Sample("/data/bt/s1/scan.h5"), Sample("/data/bt/s1/scan")])
    rows, problems = plan_output_dirs(q)
    assert len(problems) == 1
    assert "same output directory" in problems[0]
    assert "/results/bt/s1/scan" in problems[0]
    assert problems[0].count("scan") >= 2       # names both offenders


def test_plan_blocks_without_an_output_root():
    rows, problems = plan_output_dirs(_q([Sample("/data/bt/a.h5")], out_root=None))
    assert rows == [] and problems == ["No output root set."]


def test_plan_blocks_on_an_empty_queue():
    _rows, problems = plan_output_dirs(BatchQueue(out_root="/out"))
    assert problems == ["No enabled samples in the queue."]


def test_plan_blocks_when_every_sample_is_disabled():
    q = _q([Sample("/data/bt/a.h5", enabled=False)])
    _rows, problems = plan_output_dirs(q)
    assert problems == ["No enabled samples in the queue."]


def test_plan_allows_unrooted_samples_but_keeps_them_distinguishable():
    """Unrooted is a flag for the UI, not a blocker — the user may well mean it."""
    q = _q([Sample("/data/bt/a.h5"), Sample("/elsewhere/b.h5")])
    rows, problems = plan_output_dirs(q)
    assert problems == []
    assert is_unrooted(rows[1][3], "/results/bt")
    assert not is_unrooted(rows[0][3], "/results/bt")


def test_plan_spans_several_calibrations_and_corrections_nodes():
    q = BatchQueue(
        calibrations=[
            CalibrationNode(name="A", corrections=[
                CorrectionsNode(samples=[Sample("/data/bt/s1/a.h5")]),
                CorrectionsNode(samples=[Sample("/data/bt/s1/b.h5")])]),
            CalibrationNode(name="B", corrections=[
                CorrectionsNode(samples=[Sample("/data/bt/s2/c.h5")])]),
        ], data_root="/data/bt", out_root="/out")
    rows, problems = plan_output_dirs(q)
    assert problems == []
    assert [(c.name, str(d)) for c, _k, _s, d in rows] == [
        ("A", "/out/s1/a"), ("A", "/out/s1/b"), ("B", "/out/s2/c")]
