"""Tests for Batch Integrate's Browse… parity: DataLoaderPanel(mode="stream")'s
filestem-filter/explicit-multi-file source_cfg(), the workers.py source that
backs an explicit file pick (`_ExplicitTIFFSource`), and Batch Parallel's
frame-index resolution / chunk-splitting / worker-count math and the
write_all_profiles() Save-button helper (workers.BatchRunCoordinator).

Mostly dependency-free (PyQt5 + numpy + tifffile); the two write_all_profiles
tests additionally need midas_integrate_v2 and skip if it's unavailable.
"""
import numpy as np
import pytest


def _make_app_and_module():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    import midas_gui.widgets as W
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return W, app


def test_stem_filter_becomes_glob_pattern_in_source_cfg():
    W, _app = _make_app_and_module()
    panel = W.DataLoaderPanel(mode="stream")
    panel._set_stem_filter("/tmp/x", "scan_")
    assert panel.source_cfg() == {"type": "tiff_glob", "path": "/tmp/x/scan_*"}


def test_explicit_multi_file_pick_becomes_tiff_list():
    W, _app = _make_app_and_module()
    panel = W.DataLoaderPanel(mode="stream")
    paths = ["/tmp/x/a.tif", "/tmp/x/b.tif"]
    panel._set_explicit_paths(paths)
    assert panel.source_cfg() == {"type": "tiff_list", "paths": paths}


def test_plain_folder_is_still_tiff_glob():
    W, _app = _make_app_and_module()
    panel = W.DataLoaderPanel(mode="stream")
    panel._path_ed.setText("/tmp/x")
    assert panel.source_cfg() == {"type": "tiff_glob", "path": "/tmp/x"}


def test_manual_edit_clears_stem_filter_and_explicit_paths():
    W, _app = _make_app_and_module()
    panel = W.DataLoaderPanel(mode="stream")
    panel._set_stem_filter("/tmp/x", "scan_")
    panel._path_ed.setText("/tmp/y")   # a real user edit, not a Browse… pick
    assert panel._stem_filter is None
    assert panel.source_cfg() == {"type": "tiff_glob", "path": "/tmp/y"}

    panel._set_explicit_paths(["/tmp/x/a.tif"])
    panel._path_ed.setText("/tmp/z")
    assert panel._explicit_paths is None
    assert panel.source_cfg() == {"type": "tiff_glob", "path": "/tmp/z"}


def test_info_label_reports_filestem_and_file_count(tmp_path):
    W, _app = _make_app_and_module()
    panel = W.DataLoaderPanel(mode="stream")
    panel._set_stem_filter(str(tmp_path), "scan_")
    panel._load()
    assert "filestem: scan_*" in panel._info.text()

    panel2 = W.DataLoaderPanel(mode="stream")
    panel2._set_explicit_paths([str(tmp_path / "a.tif"), str(tmp_path / "b.tif")])
    panel2._load()
    assert "2 file(s)" in panel2._info.text()


def test_stem_filter_roundtrips_through_get_set_state():
    W, _app = _make_app_and_module()
    panel = W.DataLoaderPanel(mode="stream")
    panel._set_stem_filter("/tmp/x", "scan_")
    state = panel.get_state()
    assert state["stem_filter"] == "scan_"
    assert state["path"] == "/tmp/x"

    restored = W.DataLoaderPanel(mode="stream")
    restored.set_state(state)
    assert restored._stem_filter == "scan_"
    assert restored.source_cfg() == {"type": "tiff_glob", "path": "/tmp/x/scan_*"}


def test_explicit_tiff_source_reads_paths_in_order(tmp_path):
    tifffile = pytest.importorskip("tifffile")
    import midas_gui.workers as wk

    paths = []
    for i in range(3):
        p = tmp_path / f"scan_{i:04d}.tif"
        tifffile.imwrite(str(p), np.full((4, 4), i, dtype=np.float32))
        paths.append(str(p))

    src = wk._ExplicitTIFFSource(paths)
    assert src.n_frames == 3
    frames = list(src)
    assert [fid for fid, _ in frames] == ["scan_0000", "scan_0001", "scan_0002"]
    assert frames[2][1].mean() == 2.0


def test_batch_worker_open_source_dispatches_tiff_list(tmp_path):
    tifffile = pytest.importorskip("tifffile")
    torch = pytest.importorskip("torch")
    import midas_gui.workers as wk

    p = tmp_path / "a.tif"
    tifffile.imwrite(str(p), np.zeros((4, 4), dtype=np.float32))

    worker = wk.BatchWorker.__new__(wk.BatchWorker)
    worker._src = {"type": "tiff_list", "paths": [str(p)]}
    source = worker._open_source()
    assert isinstance(source, wk._ExplicitTIFFSource)
    assert source.n_frames == 1


def test_explicit_tiff_source_random_access_matches_iteration(tmp_path):
    tifffile = pytest.importorskip("tifffile")
    import midas_gui.workers as wk

    paths = []
    for i in range(3):
        p = tmp_path / f"scan_{i:04d}.tif"
        tifffile.imwrite(str(p), np.full((4, 4), i, dtype=np.float32))
        paths.append(str(p))

    src = wk._ExplicitTIFFSource(paths)
    for i in range(3):
        fid, img = src.get(i)
        assert fid == f"scan_{i:04d}"
        assert img.mean() == i


# ── Batch Parallel: frame-index resolution / chunk-splitting / worker count ──

def test_resolve_frame_indices_full_range():
    import midas_gui.workers as wk
    assert wk.resolve_frame_indices(25, None) == list(range(25))


def test_resolve_frame_indices_with_stride_and_end():
    import midas_gui.workers as wk
    assert wk.resolve_frame_indices(25, (2, 20, 3)) == list(range(2, 20, 3))


def test_resolve_frame_indices_end_clamped_to_n_frames():
    import midas_gui.workers as wk
    assert wk.resolve_frame_indices(10, (5, 1000, 1)) == list(range(5, 10))


def test_split_into_chunks_covers_all_indices_in_order():
    import midas_gui.workers as wk
    indices = list(range(23))
    chunks = wk._split_into_chunks(indices, 4)
    assert len(chunks) == 4
    assert sum(chunks, []) == indices
    # near-equal: no chunk more than 1 larger than the smallest
    assert max(len(c) for c in chunks) - min(len(c) for c in chunks) <= 1


def test_split_into_chunks_more_chunks_than_items():
    import midas_gui.workers as wk
    indices = [0, 1, 2]
    chunks = wk._split_into_chunks(indices, 10)
    assert len(chunks) == 3
    assert all(len(c) == 1 for c in chunks)
    assert sum(chunks, []) == indices


def test_batch_parallel_frame_done_reorders_out_of_completion_order():
    """Batch Parallel's chunks run concurrently, so a faster chunk's
    frame_done signals can arrive before a slower, earlier chunk's — the
    waterfall/stacked-profile views must still see frames in overall sorted
    frame-index order, not wall-clock completion order."""
    import midas_gui.workers as wk

    coord = wk.BatchRunCoordinator(
        spec=None, source_cfg=None, mask=None, out_dir=None, fmts=[],
        kernel=None, corrections=None, variance_cfg=None)

    class _DummyWorker:
        pass

    chunk_a, chunk_b = [0, 1, 2], [3, 4, 5]
    w_a, w_b = _DummyWorker(), _DummyWorker()
    coord._chunks = [chunk_a, chunk_b]
    coord._live_order = [i for c in coord._chunks for i in c]
    coord._chunk_frame_counter = {id(w_a): 0, id(w_b): 0}

    emitted = []
    coord.frame_done.connect(lambda fid, r_ax, prof, sigma: emitted.append(fid))

    # Chunk b (fast) completes fully before chunk a (slow) reports anything.
    for fid in (3, 4, 5):
        coord._on_chunk_frame(w_b, chunk_b, str(fid), None, None, None)
    assert emitted == []   # buffered — frames 0-2 haven't arrived yet
    for fid in (0, 1, 2):
        coord._on_chunk_frame(w_a, chunk_a, str(fid), None, None, None)

    assert emitted == ["0", "1", "2", "3", "4", "5"]


def _make_h5_stack(path, n_raw=10, size=4):
    h5py = pytest.importorskip("h5py")
    with h5py.File(str(path), "w") as f:
        f.create_dataset("exchange/data",
                          data=np.zeros((n_raw, size, size), dtype=np.float32))


def test_frame_range_multi_file_hdf5_spans_all_files_with_combine_chunk(tmp_path):
    """Regression: a multi-file HDF5 pick with "Combine sub-frames" set
    used to return FILE-index bounds (0..len(paths)) even though the
    worker consumes COMBINED-FRAME indices — so any chunk_size that split
    a file into more than one combined frame made the run stop partway
    through the first file(s) and never reach later files at all."""
    pytest.importorskip("h5py")
    W, _app = _make_app_and_module()

    paths = []
    for num in (9251, 9253, 9255):
        p = tmp_path / f"C611_017Fe_1_load3_{num:06d}.vrx.h5"
        _make_h5_stack(p, n_raw=10)
        paths.append(str(p))

    panel = W.DataLoaderPanel(mode="stream")
    panel._set_explicit_paths(paths)
    panel._combine_chunk.setValue(3)   # 10 raw frames / chunk 3 -> 4 combined frames/file
    panel._autofill_frame_range()

    assert panel.source_cfg()["type"] == "hdf5_stack_glob"
    # 3 files x 4 combined frames each = 12, not the file count (3).
    assert panel.frame_range() == (0, 12, 1)


def test_resolve_worker_count_shrinks_to_minimum_ten_per_worker():
    import midas_gui.workers as wk
    # plenty of frames — full requested count survives
    assert wk.resolve_worker_count(100, 8, 10) == 8
    # too few frames for 8 workers at 10/worker — shrinks to 1 (15 // 10 == 1)
    assert wk.resolve_worker_count(15, 8, 10) == 1
    # exactly enough for 2 workers
    assert wk.resolve_worker_count(20, 8, 10) == 2
    # never below 1, even with very few frames
    assert wk.resolve_worker_count(3, 8, 10) == 1


# ── write_all_profiles (backs the batch tabs' Save button) ──────────────────

def test_write_all_profiles_writes_every_frame_and_format(tmp_path):
    pytest.importorskip("midas_integrate_v2")
    import midas_gui.workers as wk

    n = 3
    r_axis = np.linspace(1.0, 10.0, 20)
    profiles = np.random.rand(n, 20)
    sigmas = np.sqrt(profiles)
    frame_ids = [f"frame_{i:03d}" for i in range(n)]

    paths = wk.write_all_profiles(
        tmp_path, ["csv", "dat", "h5"], r_axis, profiles, sigmas, frame_ids,
        lsd=200000.0, px=200.0, wl=0.2)

    for fid in frame_ids:
        assert (tmp_path / f"{fid}.csv").exists()
        assert (tmp_path / f"{fid}.dat").exists()
    assert (tmp_path / "integrated.h5").exists()
    assert len(paths) == 2 * n + 1


def test_write_all_profiles_skips_2d_csv(tmp_path):
    pytest.importorskip("midas_integrate_v2")
    import midas_gui.workers as wk

    r_axis = np.linspace(1.0, 10.0, 5)
    profiles = np.random.rand(1, 5)
    frame_ids = ["f0"]
    paths = wk.write_all_profiles(
        tmp_path, ["2d_csv"], r_axis, profiles, None, frame_ids,
        lsd=200000.0, px=200.0, wl=0.2)
    assert paths == []
    assert not any(tmp_path.iterdir())
