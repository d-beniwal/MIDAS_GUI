"""Tests for Batch Integrate's Browse… parity: DataLoaderPanel(mode="stream")'s
filestem-filter/explicit-multi-file source_cfg(), and the workers.py source
that backs an explicit file pick (`_ExplicitTIFFSource`).

Dependency-free: no MIDAS analysis backend needed, only PyQt5 + numpy + tifffile.
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
