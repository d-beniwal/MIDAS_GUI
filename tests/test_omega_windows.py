"""``raw_window_for_index`` — the raw sub-frame span behind each output frame.

This is the input to ``cake_params.omega_for_window``, and it is the part of
the omega feature that can be wrong without anything looking wrong: a window
that restarts at 0 on the second file, or rebases when a raw-frame filter is
applied, still produces a plausible-looking monotonic angle series. Only the
absolute values give it away, so they are pinned here explicitly.

Two properties matter beyond "it returns a range":

* **Global, not file-local.** A rotation scan split across files keeps
  rotating; file 2's frames are at higher angles than file 1's, so its raw
  indices must continue rather than restart. (The measured-omega path wants
  the opposite — a per-file 1-D dataset is indexed file-locally — which is
  why ``omega_channel_window`` exists separately and is pinned here too.)
* **Absolute, not per-worker.** Batch-Parallel hands each worker a slice of
  the run, and ``abs_i`` is the absolute output-frame index in both
  ``_iter_frames`` branches, so the same frame must get the same window
  regardless of which chunk computed it.

No Qt here — these are plain source objects — so the file is unforked.
"""
from pathlib import Path

import numpy as np
import pytest

from midas_gui.workers import _HDF5StackGlobSource, _ChunkCombinedFileSource

h5py = pytest.importorskip("h5py")

DSET = "exchange/data"
N_RAW = 6          # raw sub-frames per file
SHAPE = (4, 4)


@pytest.fixture
def stack(tmp_path):
    """Two files of ``N_RAW`` raw sub-frames each, values encoding the raw
    index so a mis-built window shows up as wrong pixels too."""
    paths = []
    for f, stem in enumerate(("a", "b")):
        p = tmp_path / f"{stem}.h5"
        data = np.stack([np.full(SHAPE, 100.0 * f + i, dtype=np.float32)
                         for i in range(N_RAW)])
        with h5py.File(p, "w") as h:
            h.create_dataset(DSET, data=data)
        paths.append(p)
    return paths


def _windows(src):
    return [src.raw_window_for_index(i) for i in range(src.n_frames)]


# ── _HDF5StackGlobSource: global raw windows ─────────────────────────────────

def test_windows_are_global_across_files_rather_than_restarting(stack):
    """The headline property. With ``chunk_size=2`` and two 6-sub-frame files
    the run is one continuous 0…11 sweep — file b's first chunk is raw 6-7,
    not raw 0-1 again."""
    src = _HDF5StackGlobSource(stack, DSET, chunk_size=2)
    assert _windows(src) == [(0, 1), (2, 3), (4, 5), (6, 7), (8, 9), (10, 11)]


def test_windows_are_contiguous_and_gapless_for_every_chunk_size(stack):
    """Whatever the chunking, consecutive frames must abut: a gap or an
    overlap means some raw sub-frame contributed to no angle, or to two."""
    for chunk in (1, 2, 3, 6):
        src = _HDF5StackGlobSource(stack, DSET, chunk_size=chunk)
        wins = _windows(src)
        assert wins[0][0] == 0
        assert wins[-1][1] == 2 * N_RAW - 1
        for (_, prev_hi), (next_lo, _) in zip(wins, wins[1:]):
            assert next_lo == prev_hi + 1, (chunk, wins)


def test_a_chunk_size_that_does_not_divide_the_file_leaves_a_short_last_chunk(stack):
    """``chunk_size=4`` over 6 sub-frames gives 4 + 2 per file — and the short
    chunk's angle is the mean of the two it actually holds, so the window has
    to report the true length rather than a nominal one."""
    src = _HDF5StackGlobSource(stack, DSET, chunk_size=4)
    assert _windows(src) == [(0, 3), (4, 5), (6, 9), (10, 11)]


def test_no_chunking_gives_one_raw_sub_frame_per_window(stack):
    src = _HDF5StackGlobSource(stack, DSET, chunk_size=1)
    assert _windows(src) == [(i, i) for i in range(2 * N_RAW)]


def test_chunk_size_zero_collapses_each_file_to_its_whole_range(stack):
    """Falsy ``chunk_size`` is ``read_hdf5_stack_combined``'s "whole file"
    convention (this app's ``OME_SUM = 0``), so each file yields one frame
    whose window spans it — and the second file's still doesn't restart."""
    src = _HDF5StackGlobSource(stack, DSET, chunk_size=0)
    assert src.n_frames == 2
    assert _windows(src) == [(0, 5), (6, 11)]


def test_a_raw_start_filter_shifts_the_window_rather_than_rebasing_it(stack):
    """The subtle one. ``raw_start=2`` drops the first two sub-frames of each
    file; the survivors are still physically at raw 2-3, so the angle they get
    must be OME_START + 2.5·OME_STEP, not OME_START + 0.5·OME_STEP. A rebase
    here would quietly misreport every angle in the run by a constant."""
    src = _HDF5StackGlobSource(stack, DSET, chunk_size=2, raw_start=2)
    assert _windows(src) == [(2, 3), (4, 5), (8, 9), (10, 11)]


def test_a_raw_end_filter_truncates_the_last_window(stack):
    src = _HDF5StackGlobSource(stack, DSET, chunk_size=2, raw_end=3)
    assert _windows(src) == [(0, 1), (2, 3), (6, 7), (8, 9)]


def test_an_out_of_range_frame_raises_rather_than_returning_a_wrong_window(stack):
    src = _HDF5StackGlobSource(stack, DSET, chunk_size=2)
    with pytest.raises(IndexError):
        src.raw_window_for_index(src.n_frames)


# ── _HDF5StackGlobSource: file-local windows for a measured channel ──────────

def test_the_measured_channel_window_is_file_local_and_names_its_file(stack):
    """A measured omega array is a per-file 1-D dataset, so reducing it needs
    indices into THAT file — the mirror image of the global windows above.
    Getting these two confused reads past the end of file 2's array."""
    src = _HDF5StackGlobSource(stack, DSET, chunk_size=2)
    got = [src.omega_channel_window(i) for i in range(src.n_frames)]
    assert [(Path(p).stem, lo, hi) for p, lo, hi in got] == [
        ("a", 0, 1), ("a", 2, 3), ("a", 4, 5),
        ("b", 0, 1), ("b", 2, 3), ("b", 4, 5),
    ]


def test_the_measured_channel_window_keeps_a_raw_start_offset(stack):
    """File-local, but still not rebased: raw_start=2 means element 2 of the
    file's own omega array, not element 0."""
    src = _HDF5StackGlobSource(stack, DSET, chunk_size=2, raw_start=2)
    assert [(lo, hi) for _, lo, hi in
            (src.omega_channel_window(i) for i in range(src.n_frames))] == \
        [(2, 3), (4, 5), (2, 3), (4, 5)]


# ── _ChunkCombinedFileSource: one raw frame per file ─────────────────────────

def test_a_tiff_chunk_window_counts_files(tmp_path):
    """Chunking here groups whole FILES (each holding one raw frame), and
    crosses file boundaries freely, so the window is just the file-index
    range — global by construction, since ``paths`` is already filtered."""
    paths = [tmp_path / f"f_{i:03d}.tif" for i in range(7)]
    src = _ChunkCombinedFileSource(paths, chunk_size=3)
    assert _windows(src) == [(0, 2), (3, 5), (6, 6)]


def test_an_unchunked_tiff_source_gives_one_index_per_frame(tmp_path):
    paths = [tmp_path / f"f_{i:03d}.tif" for i in range(4)]
    src = _ChunkCombinedFileSource(paths, chunk_size=1)
    assert _windows(src) == [(0, 0), (1, 1), (2, 2), (3, 3)]


def test_a_falsy_chunk_size_collapses_every_tiff_into_one_window(tmp_path):
    paths = [tmp_path / f"f_{i:03d}.tif" for i in range(5)]
    src = _ChunkCombinedFileSource(paths, chunk_size=0)
    assert src.n_frames == 1
    assert src.raw_window_for_index(0) == (0, 4)


# ── write_all_profiles: the Save button's copy carries them too ─────────────

def _save(tmp_path, n_frames=3, **kw):
    pytest.importorskip("midas_integrate_v2")
    import midas_gui.workers as wk
    n_r = 5
    profiles = np.abs(np.random.rand(n_frames, n_r)) + 1.0
    wk.write_all_profiles(
        tmp_path, ["h5"], np.linspace(1.0, 10.0, n_r), profiles,
        np.sqrt(profiles), [f"f{i}" for i in range(n_frames)],
        lsd=200000.0, px=200.0, wl=0.2, **kw)
    return tmp_path / "integrated.h5"


def _omegas_in_h5(path):
    with h5py.File(path, "r") as f:
        key = next((k for k in f if k.lower() == "omegas"), None)
        return None if key is None else np.asarray(f[key]).ravel().tolist()


def test_save_writes_the_omegas_it_was_given(tmp_path):
    """Batch Integrate's own run writes ``omegas`` into its HDF5; the Save
    button re-writes the same in-memory results and must not produce a
    quietly omega-less copy of them."""
    assert _omegas_in_h5(_save(tmp_path, omegas=[1.0, 3.0, 5.0])) == \
        pytest.approx([1.0, 3.0, 5.0])


def test_save_omits_the_dataset_rather_than_inventing_one(tmp_path):
    """A caller with no angles to give (an older in-memory result restored
    from a project logged before this feature) writes the profiles and no
    ``omegas`` — absent is readable as "not recorded"; zeros would not be."""
    assert _omegas_in_h5(_save(tmp_path)) is None


def test_save_drops_a_mismatched_omega_list_instead_of_padding_it(tmp_path):
    """Angles are matched to profiles positionally. A short list padded to
    length would misfile every frame past the gap — silently, and in the
    file a peak fit reads. Better to write none."""
    assert _omegas_in_h5(_save(tmp_path, n_frames=3, omegas=[1.0, 3.0])) is None
