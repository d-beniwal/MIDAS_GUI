"""Tests for the Data Viewer's "Live PV" row (DataLoaderPanel, allow_live=True).

Most tests are dependency-free: no real PV/pvapy channel is used, only the
panel's own frame-handling. One test is pvapy-gated (importorskip) and
proves the pvapy integration itself works via a local PvaServer round-trip.
"""
import numpy as np
import pytest


def _make_app_and_module():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    import midas_gui.widgets as W
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return W, app


def test_allow_live_false_has_no_live_ui():
    W, _app = _make_app_and_module()
    panel = W.DataLoaderPanel(mode="single")
    assert not hasattr(panel, "_pv_ed")
    assert panel._live_src is None
    panel.stop_live()  # must be a safe no-op


def test_empty_pv_warns_and_does_not_start(monkeypatch):
    W, _app = _make_app_and_module()
    warned = {}
    monkeypatch.setattr(
        W.QtWidgets.QMessageBox, "warning",
        lambda *a, **k: warned.setdefault("called", True))
    panel = W.DataLoaderPanel(mode="stack", allow_live=True)
    panel._pv_ed.setEditText("")
    panel._start_live()
    assert warned.get("called") is True
    assert panel._live_src is None or not panel._live_src.is_active()


def test_refresh_devices_repopulates_from_current_profile(monkeypatch):
    """Data Viewer's Live PV dropdown must pick up a profile switch's device
    list live, not only at construction (the reported bug)."""
    W, _app = _make_app_and_module()
    import midas_gui.constants as C
    monkeypatch.setattr(C, "DEVICES", [
        {"name": "before", "prefix": "beforePrefix:", "pva_suffix": "Pva1:Image"}])
    monkeypatch.setattr(W, "DEVICES", C.DEVICES)
    panel = W.DataLoaderPanel(mode="stack", allow_live=True)
    assert [panel._pv_ed.itemText(i) for i in range(panel._pv_ed.count())] == ["before"]

    new_devices = [
        {"name": "after1", "prefix": "after1:", "pva_suffix": "Pva1:Image"},
        {"name": "after2", "prefix": "after2:", "pva_suffix": "Pva1:Image"},
    ]
    monkeypatch.setattr(W, "DEVICES", new_devices)
    panel.refresh_devices()
    assert [panel._pv_ed.itemText(i) for i in range(panel._pv_ed.count())] == \
        ["after1", "after2"]


def test_refresh_devices_preserves_current_text():
    W, _app = _make_app_and_module()
    panel = W.DataLoaderPanel(mode="stack", allow_live=True)
    panel._pv_ed.setEditText("customTyped:Pva1:Image")
    panel.refresh_devices()
    assert panel._pv_ed.currentText() == "customTyped:Pva1:Image"


def test_refresh_devices_noop_without_live_card():
    W, _app = _make_app_and_module()
    panel = W.DataLoaderPanel(mode="single")
    panel.refresh_devices()  # must not raise


def test_live_frame_updates_current_frame():
    W, _app = _make_app_and_module()
    panel = W.DataLoaderPanel(mode="stack", allow_live=True)
    got = {}
    panel.dataChanged.connect(lambda: got.setdefault("fired", True))
    frame = np.zeros((8, 6), dtype=np.float32)
    panel._on_live_frame(frame, 3)
    assert panel.n_frames() == 1
    assert panel.current_frame().shape == (8, 6)
    assert got.get("fired") is True


def test_pva_live_source_roundtrip():
    pytest.importorskip("pvapy")
    import time
    import pvapy as pva
    from pvapy.utility.adImageUtility import AdImageUtility
    W, _app = _make_app_and_module()

    pv_name = "midasgui:test:live_stream_roundtrip"
    server = pva.PvaServer()
    img0 = (np.random.rand(12, 10) * 1000).astype(np.uint16)
    nt = AdImageUtility.generateNtNdArray(1, img0)
    server.addRecord(pv_name, nt)
    server.start()
    try:
        received = {}
        src = W.PvaLiveSource()
        src.frameReady.connect(lambda image, image_id: received.update(
            image=image, id=image_id))
        assert src.start(pv_name)
        time.sleep(0.5)

        img1 = (np.random.rand(12, 10) * 2000).astype(np.uint16)
        AdImageUtility.replaceNtNdArrayImage2D(nt, 2, img1)
        server.update(pv_name, nt)
        for _ in range(20):
            _app.processEvents()
            if received.get("id") == 2:
                break
            time.sleep(0.1)

        assert received.get("id") == 2
        assert np.array_equal(received.get("image"), img1.astype(np.float32))
    finally:
        src.stop()
        server.stop()
