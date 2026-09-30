"""A frame you ask for costs one chunk, not the whole file.

``_HDF5StackGlobSource`` already stopped *counting* by decoding (its class
docstring records that fix), but reading kept the same shape: ``get(idx)``
called ``read_hdf5_stack_combined``, which decodes every chunk and returns
the list, then threw away all but one. On a real 1442-sub-frame VAREX file
(23.9 GB, uncompressed uint16 over NFS) that was ~230 s and ~1.9 GB resident
to produce one 33 MB frame — paid by the Detector-view preview, which asks
for exactly one frame, and again by each parallel ``BatchWorker`` chunk,
which starts at a different offset so none of them shares the cache.

Wall-clock and byte counts are not assertable in a unit test, so what these
pin instead is the thing that causes them: which raw sub-frames get sliced
out of the dataset at all. ``_SpyFile`` records every slice h5py is asked
for, so "reads one chunk" becomes an exact set comparison.
"""
import numpy as np
import pytest


def _stack_file(tmp_path, n, h=3, w=4, name="scan.h5"):
    h5py = pytest.importorskip("h5py")
    data = np.arange(n * h * w, dtype=np.float32).reshape(n, h, w)
    with h5py.File(tmp_path / name, "w") as f:
        f.create_dataset("exchange/data", data=data)
    return str(tmp_path / name), data


class _SpyDataset:
    def __init__(self, real, log):
        self._real, self._log = real, log

    def __getitem__(self, key):
        if isinstance(key, slice):
            self._log.append((key.start, key.stop))
        return self._real[key]

    def __getattr__(self, name):
        return getattr(self._real, name)


class _SpyFile:
    """h5py.File proxy recording every slice taken out of a dataset."""

    def __init__(self, real, log):
        self._real, self._log = real, log

    def __getitem__(self, name):
        return _SpyDataset(self._real[name], self._log)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return self._real.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.fixture
def slices(monkeypatch):
    """Every ``dset[a:b]`` taken while this fixture is active, as (a, b)."""
    h5py = pytest.importorskip("h5py")
    log = []
    real_file = h5py.File
    monkeypatch.setattr(h5py, "File",
                        lambda *a, **k: _SpyFile(real_file(*a, **k), log))
    return log


def _source(path, **over):
    from midas_gui.workers import _open_source_cfg
    cfg = {"type": "hdf5", "path": path, "dataset": "exchange/data",
           "chunk_size": 5, "combine_op": "mean",
           "frame_start": None, "frame_end": None}
    cfg.update(over)
    return _open_source_cfg(cfg)


# ── the regression itself ──────────────────────────────────────────────

def test_getting_one_frame_reads_only_that_chunk(tmp_path, slices):
    """The whole point: 40 sub-frames in 8 chunks of 5, ask for frame 0,
    and sub-frames 5..39 must never be touched."""
    path, _ = _stack_file(tmp_path, 40)
    src = _source(path)
    slices.clear()
    src.get(0)
    assert slices == [(0, 5)]


def test_a_mid_file_frame_does_not_decode_what_precedes_it(tmp_path, slices):
    """Batch Parallel's whole problem: four workers start at four different
    offsets, so a cache keyed on "the current file" helps none of them."""
    path, _ = _stack_file(tmp_path, 40)
    src = _source(path)
    slices.clear()
    src.get(6)
    assert slices == [(30, 35)]


def test_n_frames_still_reads_no_pixels_at_all(tmp_path, slices):
    """The earlier half of this fix — pinned here so a future change to the
    read path can't quietly reintroduce decode-to-count alongside it."""
    path, _ = _stack_file(tmp_path, 40)
    src = _source(path)
    slices.clear()
    assert src.n_frames == 8
    assert slices == []


def test_iterating_reads_each_chunk_exactly_once(tmp_path, slices):
    """Chunk-at-a-time must not turn a sequential pass into a re-read: the
    fix would be worse than the bug if it cost the file several times over."""
    path, _ = _stack_file(tmp_path, 40)
    src = _source(path)
    slices.clear()
    list(src)
    assert slices == [(k * 5, k * 5 + 5) for k in range(8)]


def test_the_pixel_cache_holds_one_chunk_not_one_file(tmp_path):
    path, _ = _stack_file(tmp_path, 40)
    src = _source(path)
    src.get(0)
    src.get(3)
    assert len(src._cache) == 1


# ── the frames themselves are unchanged ────────────────────────────────

def test_every_frame_matches_the_whole_file_read(tmp_path):
    """Equivalence with the implementation this replaced, frame for frame
    and id for id — a faster wrong answer is not the deliverable."""
    from midas_gui.helpers import read_hdf5_stack_combined
    path, _ = _stack_file(tmp_path, 40)
    expect = read_hdf5_stack_combined(path, "exchange/data", chunk_size=5)
    src = _source(path)
    assert src.n_frames == len(expect)
    for i, want in enumerate(expect):
        _fid, got = src.get(i)
        np.testing.assert_allclose(got, want, rtol=1e-6)


def test_a_ragged_last_chunk_is_short_not_padded(tmp_path, slices):
    path, _ = _stack_file(tmp_path, 12)
    src = _source(path)
    assert src.n_frames == 3
    slices.clear()
    fid, img = src.get(2)
    # 2 sub-frames, not a full 5: the reader clamps the slice to the real
    # end itself rather than over-asking and leaving h5py to truncate.
    assert slices == [(10, 12)]
    assert fid.endswith("frame_10_11")
    assert img.shape == (3, 4)


def test_a_raw_start_filter_shifts_the_chunk_it_reads(tmp_path, slices):
    """frame_start/frame_end on a single-file HDF5 are raw sub-frame bounds,
    applied before chunking — so chunk 0 starts at the filter, not at 0."""
    path, _ = _stack_file(tmp_path, 40)
    src = _source(path, frame_start=7, frame_end=26)
    assert src.n_frames == 4
    slices.clear()
    src.get(0)
    assert slices == [(7, 12)]


def test_chunk_size_zero_still_means_the_whole_filtered_file(tmp_path, slices):
    path, _ = _stack_file(tmp_path, 12)
    src = _source(path, chunk_size=None)
    assert src.n_frames == 1
    slices.clear()
    src.get(0)
    assert slices == [(0, 12)]


def test_a_2d_dataset_is_still_one_passthrough_frame(tmp_path):
    h5py = pytest.importorskip("h5py")
    p = tmp_path / "flat.h5"
    flat = np.arange(12, dtype=np.float32).reshape(3, 4)
    with h5py.File(p, "w") as f:
        f.create_dataset("exchange/data", data=flat)
    src = _source(str(p))
    assert src.n_frames == 1
    _fid, img = src.get(0)
    np.testing.assert_allclose(img, flat)


def test_an_index_past_the_end_still_raises(tmp_path):
    path, _ = _stack_file(tmp_path, 12)
    src = _source(path)
    with pytest.raises(IndexError):
        src.get(3)
