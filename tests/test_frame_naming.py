"""Tests for Batch Integrate's output-file naming and the VAREX-style
multi-frame HDF5 frame source added alongside it.

Both live in ``workers.py`` and are coupled: ``_HDF5StackGlobSource`` mints the
``<stem>_c<NN>`` chunk ids that ``froot_and_frame_num`` has to parse, so they
are covered together here.

The naming convention itself (``<froot>_<NNNNNN><tag>``, mirroring
mpe_wf_saxs_waxs's output names) is a deliberate change from the older
"write the frame id verbatim" behaviour — see ``frame_output_base``.
"""
from pathlib import Path

import numpy as np
import pytest

from midas_gui.workers import (
    froot_and_frame_num, frame_output_base, _HDF5StackGlobSource,
    _ChunkCombinedFileSource, _filter_paths_by_frame_number)


# ── froot_and_frame_num: parsing ─────────────────────────────────────────────

@pytest.mark.parametrize("fid, expected", [
    # Plain <stem>_<digits>.
    ("frame_000",                      ("frame", 0, "")),
    ("det_12",                         ("det", 12, "")),
    # Frame number mid-stem with a trailing non-numeric detector tag, because
    # Path.stem only strips the LAST suffix off "..._009243.vrx.h5".
    ("C611_load3_009243.vrx",          ("C611_load3", 9243, ".vrx")),
    # Multi-underscore stems: the LAST underscore-digit run is the frame no.
    ("sample_25C_003",                 ("sample_25C", 3, "")),
    ("scan_2_5",                       ("scan_2", 5, "")),
])
def test_froot_and_frame_num_parses_common_ids(fid, expected):
    assert froot_and_frame_num(fid, fallback_idx=7) == expected


@pytest.mark.parametrize("fid, expected", [
    # A stem that already carries its own frame number keeps it, and the
    # chunk suffix rides along in `tag` so sibling chunks stay distinct.
    ("x_009243.vrx_c00", ("x", 9243, ".vrx_c00")),
    ("x_009243.vrx_c01", ("x", 9243, ".vrx_c01")),
    # A stem with no number of its own: the chunk index IS the frame index,
    # which sorts correctly instead of falling back to a loop counter.
    ("run_c00",          ("run", 0, "")),
    ("run_c01",          ("run", 1, "")),
])
def test_froot_and_frame_num_handles_chunk_suffixes(fid, expected):
    """``_HDF5StackGlobSource`` emits ``<stem>_c<NN>`` when one multi-frame
    HDF5 file is split into several combined chunks."""
    assert froot_and_frame_num(fid, fallback_idx=7) == expected


def test_froot_and_frame_num_falls_back_when_no_number():
    """A caller-supplied id with no underscore-digit run keeps the id and
    borrows the loop index, so the result is still unique per frame."""
    assert froot_and_frame_num("scan1", 7) == ("scan1", 7, "")
    assert froot_and_frame_num("baseline", 3) == ("baseline", 3, "")


# ── frame_output_base: uniqueness ────────────────────────────────────────────

def test_frame_output_base_formats_six_digits(tmp_path):
    used = set()
    base = frame_output_base(tmp_path, "frame_7", 0, used)
    assert base == tmp_path / "frame_000007"
    assert used == {"frame_000007"}


def test_frame_output_base_never_collapses_distinct_frames(tmp_path):
    """Zero-padding is normalised, so "scan_1"/"scan_01"/"scan_001" all parse
    to frame 1. Writing them all to "scan_000001" would silently overwrite
    earlier frames, so a clash falls back to the raw (unique) frame id."""
    used = set()
    fids = ["scan_1", "scan_01", "scan_001"]
    bases = [frame_output_base(tmp_path, f, i, used) for i, f in enumerate(fids)]
    names = [b.name for b in bases]
    assert len(set(names)) == len(fids), f"collision: {names}"
    assert names[0] == "scan_000001"          # first claim gets the canonical name
    assert set(names[1:]) == {"scan_01", "scan_001"}


def test_frame_output_base_last_resort_index_suffix(tmp_path):
    """If even the raw id is taken, the loop index disambiguates rather than
    the writer clobbering an existing file."""
    used = {"scan_000001", "scan_1"}
    base = frame_output_base(tmp_path, "scan_1", 4, used)
    assert base.name == "scan_1_000004"


def test_write_all_profiles_writes_one_file_per_frame_on_mixed_padding(tmp_path):
    """End-to-end guard for the same bug: N frames in must be N files out."""
    pytest.importorskip("midas_integrate_v2")
    import midas_gui.workers as wk

    fids = ["scan_1", "scan_01", "scan_001"]
    r_axis = np.linspace(1.0, 10.0, 5)
    profiles = np.random.rand(len(fids), 5)
    wk.write_all_profiles(tmp_path, ["csv"], r_axis, profiles,
                          np.sqrt(profiles), fids,
                          lsd=200000.0, px=200.0, wl=0.2)
    written = sorted(p.name for p in tmp_path.iterdir())
    assert len(written) == len(fids), written


# ── _HDF5StackGlobSource ─────────────────────────────────────────────────────

def _write_stack(path, n, h=4, w=5):
    h5py = pytest.importorskip("h5py")
    data = np.arange(n * h * w, dtype=np.float32).reshape(n, h, w)
    with h5py.File(path, "w") as f:
        f.create_dataset("exchange/data", data=data)
    return data


def test_hdf5_stack_glob_source_combines_whole_file_by_default(tmp_path):
    """chunk_size=None combines every raw sub-frame into one frame per file —
    the VAREX case where the whole stack is one scan point."""
    a = _write_stack(tmp_path / "a.h5", 4)
    b = _write_stack(tmp_path / "b.h5", 4)

    src = _HDF5StackGlobSource([tmp_path / "a.h5", tmp_path / "b.h5"],
                               "exchange/data")
    assert src.n_frames == 2
    ids = [fid for fid, _ in src]
    assert ids == ["a", "b"], "one frame per file gets the plain stem, no _cNN"

    _, img = src.get(0)
    np.testing.assert_allclose(img, a.mean(axis=0), rtol=1e-6)
    _, img = src.get(1)
    np.testing.assert_allclose(img, b.mean(axis=0), rtol=1e-6)


def test_hdf5_stack_glob_source_chunks_and_labels(tmp_path):
    """A positive chunk_size splits one file into several combined frames,
    each labelled ``<stem>.frame_<start>_<end>`` by the raw 0-based sub-frame
    range it combines — ids that froot_and_frame_num can parse."""
    data = _write_stack(tmp_path / "run.h5", 6)

    src = _HDF5StackGlobSource([tmp_path / "run.h5"], "exchange/data",
                               chunk_size=2, op="sum")
    assert src.n_frames == 3
    ids = [fid for fid, _ in src]
    assert ids == ["run.frame_0_1", "run.frame_2_3", "run.frame_4_5"]

    _, img = src.get(1)
    np.testing.assert_allclose(img, data[2:4].sum(axis=0), rtol=1e-6)

    # The chunk ids round-trip through the naming helper into distinct,
    # correctly-ordered output names.
    used = set()
    names = [frame_output_base(tmp_path, f, i, used).name
             for i, f in enumerate(ids)]
    # The stem carries no frame number of its own, so the chunk's start index
    # becomes the frame number — distinct and correctly ordered per chunk.
    assert names == ["run_000000", "run_000002", "run_000004"]


def test_range_chunk_suffix_is_split_off_before_the_numeric_parse():
    """``.frame_<start>_<end>`` is _HDF5StackGlobSource's current chunk id.
    Without splitting it off first the trailing ``_<end>`` is mistaken for the
    stem's own frame number, so every chunk of one file would be renamed by
    its end index and a real detector frame number would be lost."""
    assert froot_and_frame_num("run_009243.vrx.frame_0_9", -1) == \
        ("run", 9243, ".vrx.frame_0_9")
    assert froot_and_frame_num("run_009243.vrx.frame_10_19", -1) == \
        ("run", 9243, ".vrx.frame_10_19")


def test_range_chunks_of_one_numbered_file_stay_distinct(tmp_path):
    """Chunks of a single numbered source share a froot AND a frame number,
    so only the preserved range tag keeps their output names apart."""
    used = set()
    names = [frame_output_base(tmp_path, f, i, used).name for i, f in enumerate(
        ["run_009243.vrx.frame_0_9", "run_009243.vrx.frame_10_19"])]
    assert names == ["run_009243.vrx.frame_0_9", "run_009243.vrx.frame_10_19"]
    assert len(set(names)) == 2


def test_hdf5_stack_glob_source_indexes_across_files(tmp_path):
    """get(idx) walks a flattened index over every file's chunks."""
    _write_stack(tmp_path / "a.h5", 4)
    _write_stack(tmp_path / "b.h5", 4)
    src = _HDF5StackGlobSource([tmp_path / "a.h5", tmp_path / "b.h5"],
                               "exchange/data", chunk_size=2)
    assert src.n_frames == 4
    assert [src.get(i)[0] for i in range(4)] == \
        ["a.frame_0_1", "a.frame_2_3", "b.frame_0_1", "b.frame_2_3"]
    with pytest.raises(IndexError):
        src.get(4)


# ── _ChunkCombinedFileSource / _filter_paths_by_frame_number ────────────────
# The TIFF-family counterpart of _HDF5StackGlobSource for a Batch Integrate
# unify_combine panel — see widgets.DataLoaderPanel.source_cfg's chunk_size/
# frame_start/frame_end on the "tiff_glob"/"tiff_list" types.

def _write_tiff_files(tmp_path, nums):
    tifffile = pytest.importorskip("tifffile")
    paths = []
    for i, n in enumerate(nums):
        p = tmp_path / f"scan_{n:06d}.tif"
        tifffile.imwrite(str(p), np.full((3, 3), i, dtype=np.float32))
        paths.append(str(p))
    return paths


def test_chunk_combined_file_source_default_matches_one_frame_per_file(tmp_path):
    """chunk_size=1 (the default) combines nothing — same ids/values as an
    uncombined per-file source, so a unify_combine panel's default doesn't
    change anything for a plain folder pick."""
    paths = _write_tiff_files(tmp_path, [1, 2, 3])
    src = _ChunkCombinedFileSource(paths, chunk_size=1)
    assert src.n_frames == 3
    ids = [fid for fid, _ in src]
    assert ids == ["scan_000001", "scan_000002", "scan_000003"]


def test_chunk_combined_file_source_groups_consecutive_files(tmp_path):
    paths = _write_tiff_files(tmp_path, [1, 2, 3, 4, 5])
    src = _ChunkCombinedFileSource(paths, chunk_size=2, op="sum")
    assert src.n_frames == 3
    ids = [fid for fid, _ in src]
    # A trailing partial group of 1 keeps the plain stem, like _HDF5StackGlobSource.
    assert ids == ["scan_000001.frame_0_1", "scan_000003.frame_2_3", "scan_000005"]
    _, img = src.get(0)
    np.testing.assert_allclose(img, 0 + 1, rtol=1e-6)   # values 0,1 summed
    _, img = src.get(1)
    np.testing.assert_allclose(img, 2 + 3, rtol=1e-6)


def test_chunk_combined_file_source_falsy_chunk_size_combines_everything(tmp_path):
    paths = _write_tiff_files(tmp_path, [1, 2, 3])
    src = _ChunkCombinedFileSource(paths, chunk_size=None, op="mean")
    assert src.n_frames == 1
    fid, img = src.get(0)
    assert fid == "scan_000001.frame_0_2"
    np.testing.assert_allclose(img, np.mean([0, 1, 2]), rtol=1e-6)


def test_chunk_combined_file_source_get_out_of_range_raises(tmp_path):
    paths = _write_tiff_files(tmp_path, [1, 2])
    src = _ChunkCombinedFileSource(paths, chunk_size=1)
    with pytest.raises(IndexError):
        src.get(2)


def test_filter_paths_by_frame_number_keeps_range_inclusive(tmp_path):
    paths = _write_tiff_files(tmp_path, [9241, 9242, 9243, 9244, 9245])
    kept = _filter_paths_by_frame_number(paths, 9242, 9244)
    assert [Path(p).stem for p in kept] == ["scan_009242", "scan_009243", "scan_009244"]


def test_filter_paths_by_frame_number_unbounded_side_is_none():
    paths = ["scan_000010.tif", "scan_000020.tif", "scan_000030.tif"]
    assert _filter_paths_by_frame_number(paths, 20, None) == paths[1:]
    assert _filter_paths_by_frame_number(paths, None, 20) == paths[:2]


def test_filter_paths_by_frame_number_both_none_is_a_no_op():
    paths = ["a_1.tif", "b_2.tif"]
    assert _filter_paths_by_frame_number(paths, None, None) == paths


def test_filter_paths_by_frame_number_falls_back_on_unparseable_name():
    """Mirrors DataLoaderPanel._file_numbers()'s all-or-nothing rule: if even
    one file's number can't be parsed, filtering is skipped entirely rather
    than silently dropping files with no way to compare them."""
    paths = ["scan_000010.tif", "not_numeric.tif", "scan_000030.tif"]
    assert _filter_paths_by_frame_number(paths, 15, 25) == paths
