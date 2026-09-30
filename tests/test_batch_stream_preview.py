"""Tests for the "stream"-mode preview frame going async
(``DataLoaderPanel._start_preview_worker`` / ``workers.StreamPreviewWorker``).

Prompted by a real freeze: the preview used to run synchronously on the GUI
thread (``_peek_stream_frame``), and a multi-file HDF5 source over an
NFS-mounted beamline share hung the whole app indefinitely (HDF5's file
locking never being granted — see midas_gui/_paths.py and
.context/DECISIONS.md, 2026-09-25). These tests pin the new contract:
``current_frame()`` never blocks, and ``previewFrameReady`` fires once the
background read actually lands.
"""
import numpy as np
import pytest


def _make_app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets, QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _make_tiff(tmp_path, name, value, size=4):
    tifffile = pytest.importorskip("tifffile")
    p = tmp_path / name
    tifffile.imwrite(str(p), np.full((size, size), value, dtype=np.float32))
    return str(p)


# ── StreamPreviewWorker (standalone) ─────────────────────────────────────

def test_stream_preview_worker_sums_and_corrects_before_summing(tmp_path):
    """Correction must be applied to each constituent frame BEFORE summing
    (matching the real batch run) — correcting only the final sum would
    subtract just one dark frame's worth from an N-times-larger signal."""
    _QtWidgets, _app = _make_app()
    import midas_gui.workers as wk

    paths = [_make_tiff(tmp_path, f"f{i}.tif", 10.0 + i) for i in range(3)]
    dark = np.full((4, 4), 2.0, dtype=np.float32)

    worker = wk.StreamPreviewWorker(
        {"type": "tiff_list", "paths": paths}, preview_sum_n=3, dark=dark)
    results = {}
    worker.finished.connect(lambda f: results.update(frame=f))
    worker.failed.connect(lambda m: results.update(error=m))
    worker.start()
    worker.wait(5000)
    _QtWidgets.QApplication.processEvents()

    assert "error" not in results, results.get("error")
    # (10-2) + (11-2) + (12-2) = 27, not (10+11+12)-2 = 31.
    assert np.allclose(results["frame"], 27.0)


def test_stream_preview_worker_emits_none_for_an_empty_source():
    _QtWidgets, _app = _make_app()
    import midas_gui.workers as wk

    worker = wk.StreamPreviewWorker({"type": "tiff_list", "paths": []}, preview_sum_n=1)
    results = {}
    worker.finished.connect(lambda f: results.update(frame=f, called=True))
    worker.start()
    worker.wait(5000)
    _QtWidgets.QApplication.processEvents()
    assert results.get("called") is True
    assert results["frame"] is None


def test_stream_preview_worker_emits_failed_on_a_bad_source():
    _QtWidgets, _app = _make_app()
    import midas_gui.workers as wk

    worker = wk.StreamPreviewWorker(
        {"type": "tiff_list", "paths": ["/no/such/file.tif"]}, preview_sum_n=1)
    results = {}
    worker.failed.connect(lambda m: results.update(error=m))
    worker.finished.connect(lambda f: results.update(frame=f))
    worker.start()
    worker.wait(5000)
    _QtWidgets.QApplication.processEvents()
    assert "error" in results
    assert "frame" not in results


# ── DataLoaderPanel("stream") integration ────────────────────────────────

def test_current_frame_does_not_block_returns_none_first(tmp_path):
    """The core regression test: current_frame() must return immediately
    (None, on first ask) rather than blocking until the file read finishes —
    the opposite of the old synchronous _peek_stream_frame contract."""
    QtWidgets, _app = _make_app()
    import midas_gui.widgets as W

    path = _make_tiff(tmp_path, "a.tif", 5.0)
    panel = W.DataLoaderPanel(mode="stream")
    panel._path_ed.setText(path)

    frame = panel.current_frame()   # kicks off the background worker
    assert frame is None            # not computed yet — must not have blocked
    assert panel._preview_worker is not None

    panel._preview_worker.wait(5000)
    QtWidgets.QApplication.processEvents()

    assert panel.current_frame() is not None
    assert np.allclose(panel.current_frame(), 5.0)


def test_preview_frame_ready_fires_once_the_background_read_lands(tmp_path):
    QtWidgets, _app = _make_app()
    import midas_gui.widgets as W

    path = _make_tiff(tmp_path, "a.tif", 3.0)
    panel = W.DataLoaderPanel(mode="stream")
    panel._path_ed.setText(path)

    seen = []
    panel.previewFrameReady.connect(lambda: seen.append(True))
    panel.current_frame()
    assert not seen   # nothing yet — still in flight

    panel._preview_worker.wait(5000)
    QtWidgets.QApplication.processEvents()
    assert seen


def test_dark_correction_is_baked_into_the_async_preview(tmp_path):
    QtWidgets, _app = _make_app()
    import midas_gui.widgets as W

    path = _make_tiff(tmp_path, "a.tif", 10.0)
    panel = W.DataLoaderPanel(mode="stream")
    panel._path_ed.setText(path)
    panel._dark_sel._field = np.full((4, 4), 4.0, dtype=np.float32)
    panel._dark_sel.setChecked(True)   # get_field() only returns _field when checked

    panel.current_frame()
    panel._preview_worker.wait(5000)
    QtWidgets.QApplication.processEvents()

    assert np.allclose(panel.current_frame(), 6.0)   # 10 - 4


def test_a_second_dirty_trigger_while_one_is_in_flight_does_not_spawn_a_second_worker(tmp_path):
    """Rapid consecutive changes (e.g. checking Dark then Bright quickly)
    must not pile up concurrent reads against the same source — the second
    trigger flags the in-flight worker as stale instead, and a fresh one
    starts automatically once it finishes."""
    QtWidgets, _app = _make_app()
    import midas_gui.widgets as W

    path = _make_tiff(tmp_path, "a.tif", 1.0)
    panel = W.DataLoaderPanel(mode="stream")
    panel._path_ed.setText(path)

    panel.current_frame()               # starts worker #1
    first_worker = panel._preview_worker
    assert first_worker is not None

    panel._stream_preview_dirty = True  # simulate another field change arriving
    panel.current_frame()               # must NOT start a second worker
    assert panel._preview_worker is first_worker
    assert panel._preview_worker_stale is True

    first_worker.wait(5000)
    QtWidgets.QApplication.processEvents()
    # The stale flag triggered an automatic restart — let that one finish too.
    if panel._preview_worker is not None:
        panel._preview_worker.wait(5000)
        QtWidgets.QApplication.processEvents()

    assert panel._preview_worker_stale is False
    assert np.allclose(panel.current_frame(), 1.0)


def test_no_source_emits_preview_ready_immediately_with_none_frame():
    QtWidgets, _app = _make_app()
    import midas_gui.widgets as W

    panel = W.DataLoaderPanel(mode="stream")
    seen = []
    panel.previewFrameReady.connect(lambda: seen.append(True))
    frame = panel.current_frame()
    assert frame is None
    assert seen   # no source to read at all -> resolved synchronously, no worker
    assert panel._preview_worker is None
