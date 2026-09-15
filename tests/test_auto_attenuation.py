"""Unit tests for midas_gui.auto_attenuation.

Covers the numerical core ported from pyAutoBeam (masking, NIST Cu lookup,
single-capture analysis), the new saturation pre-check (not present in
pyAutoBeam), the snapshot round-trip, and a Qt smoke test of the popup
dialog built directly off an in-memory snapshot (no subprocess).
"""
import json

import numpy as np
import pytest


# ── masking.py ──────────────────────────────────────────────────────────

def test_create_frozen_pixel_mask_flags_stuck_nonzero_pixel():
    from midas_gui.auto_attenuation.masking import create_frozen_pixel_mask

    data = np.zeros((5, 4, 4), dtype=np.float32)
    data[:, 1, 1] = 100.0                      # frozen: constant nonzero
    data[:, 2, 2] = np.arange(5) * 10.0        # fluctuating, not frozen

    mask = create_frozen_pixel_mask(data, std_cutoff=0.5)
    assert mask[1, 1] == 1.0
    assert mask[2, 2] == 0.0
    assert mask[0, 0] == 0.0  # always-zero pixel is not "frozen"


def test_create_isolated_hot_pixel_mask_requires_isolation():
    from midas_gui.auto_attenuation.masking import create_isolated_hot_pixel_mask

    frame = np.zeros((10, 10), dtype=np.float32)
    frame[5, 5] = 5000.0          # isolated hot spike
    frame[2, 2] = 5000.0
    frame[2, 3] = 4000.0          # has a bright neighbor -> not isolated

    mask = create_isolated_hot_pixel_mask(frame, noise_floor=30.0, min_hot_intensity=2000.0)
    assert mask[5, 5] == 1.0
    assert mask[2, 2] == 0.0


def test_create_dark_mask_flags_dead_and_hot_pixels():
    from midas_gui.auto_attenuation.masking import create_dark_mask

    rng = np.random.default_rng(0)
    dark = 100.0 + rng.normal(0, 2.0, size=(10, 21, 21)).astype(np.float32)
    dark[:, 10, 10] = 100.0        # dead: zero variance
    dark[:, 5, 5] = 900.0          # hot: far above local neighborhood

    mask, info = create_dark_mask(dark, n_sigma=5, local_window=9)
    assert mask[10, 10] == 1.0
    assert mask[5, 5] == 1.0
    assert info["n_dead"] >= 1
    assert info["n_hot"] >= 1


def test_create_dark_mask_requires_at_least_two_frames():
    from midas_gui.auto_attenuation.masking import create_dark_mask

    with pytest.raises(ValueError):
        create_dark_mask(np.zeros((1, 4, 4), dtype=np.float32))


def test_apply_mask_zeroes_bad_pixels():
    from midas_gui.auto_attenuation.masking import apply_mask

    data = np.ones((2, 3, 3), dtype=np.float32) * 10.0
    mask = np.zeros((3, 3), dtype=np.float32)
    mask[1, 1] = 1.0
    out = apply_mask(data, mask)
    assert out[:, 1, 1].sum() == 0.0
    assert out[:, 0, 0].sum() == 20.0


# ── nist_cu.py ──────────────────────────────────────────────────────────

def test_estimate_mu_linear_matches_hand_computed_value_at_a_table_point():
    from midas_gui.auto_attenuation.nist_cu import estimate_mu_linear, CU_DENSITY

    # 5.00E-01 MeV = 500 keV, mu/rho = 8.36E-02 cm^2/g exactly in the table.
    mu = estimate_mu_linear(500.0)
    expected = 8.36e-02 * CU_DENSITY / 10.0
    assert mu == pytest.approx(expected, rel=1e-6)


def test_estimate_mu_linear_out_of_range_raises():
    from midas_gui.auto_attenuation.nist_cu import estimate_mu_linear

    with pytest.raises(ValueError):
        estimate_mu_linear(0.001)  # far below 1 keV table floor


def test_mu_decreases_then_flattens_with_energy():
    from midas_gui.auto_attenuation.nist_cu import estimate_mu_linear

    mu_low = estimate_mu_linear(10.0)
    mu_mid = estimate_mu_linear(63.0)
    mu_high = estimate_mu_linear(500.0)
    assert mu_low > mu_mid > mu_high


# ── saturation.py ────────────────────────────────────────────────────────

def test_check_saturation_ok_within_tolerance():
    from midas_gui.auto_attenuation.saturation import check_saturation

    frames = np.zeros((3, 5, 5), dtype=np.float32)
    frames[1, 0, 0] = 9000.0
    frames[1, 0, 1] = 9000.0  # 2 saturated pixels in frame 1, tolerate 5

    result = check_saturation(frames, None, saturation_intensity=1000.0,
                              tolerate_n=5, skip_frames=0)
    assert result.ok
    assert result.per_frame_bad_counts[1] == 2


def test_check_saturation_flags_frame_over_tolerance():
    from midas_gui.auto_attenuation.saturation import check_saturation

    frames = np.zeros((2, 5, 5), dtype=np.float32)
    frames[0, :2, :4] = 9000.0  # 8 saturated pixels, tolerate 5

    result = check_saturation(frames, None, saturation_intensity=1000.0,
                              tolerate_n=5, skip_frames=0)
    assert not result.ok
    assert 0 in result.bad_frame_indices


def test_check_saturation_skips_leading_frames():
    from midas_gui.auto_attenuation.saturation import check_saturation

    frames = np.zeros((2, 5, 5), dtype=np.float32)
    frames[0, :, :] = 9000.0  # entirely saturated first frame

    result = check_saturation(frames, None, saturation_intensity=1000.0,
                              tolerate_n=5, skip_frames=1)
    assert result.ok
    assert result.per_frame_bad_counts == {1: 0}


def test_check_saturation_excludes_masked_pixels():
    from midas_gui.auto_attenuation.saturation import check_saturation

    frames = np.zeros((2, 3, 3), dtype=np.float32)
    frames[:, 0, 0] = 9000.0
    mask = np.zeros((3, 3), dtype=np.float32)
    mask[0, 0] = 1.0  # this pixel is already known-bad

    result = check_saturation(frames, mask, saturation_intensity=1000.0,
                              tolerate_n=0, skip_frames=0)
    assert result.ok


# ── analysis.py ──────────────────────────────────────────────────────────

def test_preprocess_stack_skip_frames_and_dark_subtraction():
    from midas_gui.auto_attenuation.analysis import preprocess_stack

    frames = np.full((3, 4, 4), 100.0, dtype=np.float32)
    frames[0] = 99999.0  # would-be-saturated leading frame, must be dropped
    dark = np.full((4, 4), 10.0, dtype=np.float32)

    result = preprocess_stack(
        frames, dark=dark, skip_frames=1,
        frozen_mask=False, hot_pixel_mask=False,
    )
    assert result.data.shape[0] == 2
    assert np.allclose(result.data, 90.0)


def test_run_single_capture_recovers_injected_intensity():
    from midas_gui.auto_attenuation.analysis import (
        preprocess_stack, run_single_capture,
    )
    from midas_gui.auto_attenuation.attenuator_table import DEFAULT_POSITION_THICKNESS_MM
    from midas_gui.auto_attenuation.nist_cu import estimate_mu_linear

    energy_keV = 63.0
    att_pos = 3
    acq_time_s = 2.0
    thickness = DEFAULT_POSITION_THICKNESS_MM[att_pos]
    mu = estimate_mu_linear(energy_keV)

    # Build a frame stack whose max intensity, after acq_time normalization
    # and the known thickness, implies a chosen S*I0.
    si0_true = 20000.0
    intensity = si0_true * np.exp(-mu * thickness) * acq_time_s
    frames = np.full((3, 8, 8), intensity, dtype=np.float32)

    pre = preprocess_stack(frames, skip_frames=0, frozen_mask=False,
                           hot_pixel_mask=False)
    result = run_single_capture(
        pre, energy_keV=energy_keV, att_pos=att_pos, acq_time_s=acq_time_s,
        target_intensity=50000.0, min_intensity=10.0,
    )

    assert result["ok"]
    assert result["mu"] == pytest.approx(mu)
    assert result["SI0"] == pytest.approx(si0_true, rel=1e-4)
    # Recommendation table: thicker attenuator -> longer recommended time.
    recs = result["recommendations"]
    assert set(recs) == set(DEFAULT_POSITION_THICKNESS_MM)
    times_by_thickness = sorted(
        (DEFAULT_POSITION_THICKNESS_MM[p], recs[p]["recommended_time_s"])
        for p in recs
    )
    times = [t for _, t in times_by_thickness]
    assert times == sorted(times)


def test_run_single_capture_rejects_low_intensity():
    from midas_gui.auto_attenuation.analysis import (
        preprocess_stack, run_single_capture,
    )

    frames = np.full((2, 4, 4), 1.0, dtype=np.float32)
    pre = preprocess_stack(frames, skip_frames=0, frozen_mask=False,
                           hot_pixel_mask=False)
    result = run_single_capture(
        pre, energy_keV=63.0, att_pos=0, acq_time_s=1.0,
        min_intensity=1000.0,
    )
    assert not result["ok"]


# ── snapshot.py ──────────────────────────────────────────────────────────

def test_snapshot_round_trip(tmp_path):
    from midas_gui.auto_attenuation.snapshot import write_snapshot, load_snapshot

    frames = np.random.default_rng(1).random((3, 6, 6)).astype(np.float32)
    dark = np.ones((6, 6), dtype=np.float32) * 2.0
    mask = np.zeros((6, 6), dtype=np.float32)
    mask[0, 0] = 1.0
    geometry = {"wavelength_A": 0.1729, "Lsd": 200000.0}

    path = tmp_path / "snap.npz"
    write_snapshot(str(path), frames=frames, dark=dark, mask=mask,
                   energy_keV=71.676, geometry=geometry)
    out = load_snapshot(str(path))

    np.testing.assert_allclose(out["frames"], frames)
    np.testing.assert_allclose(out["dark"], dark)
    np.testing.assert_allclose(out["mask"], mask)
    assert out["energy_keV"] == pytest.approx(71.676)
    assert out["geometry"] == geometry
    assert "dark_stack" not in out
    assert "source" not in out


def test_snapshot_round_trip_records_source(tmp_path):
    from midas_gui.auto_attenuation.snapshot import write_snapshot, load_snapshot

    frames = np.zeros((2, 4, 4), dtype=np.float32)
    path = tmp_path / "snap.npz"
    write_snapshot(str(path), frames=frames, source="buffer")
    out = load_snapshot(str(path))
    assert out["source"] == "buffer"


# ── dialog.py (Qt smoke test) ─────────────────────────────────────────────

@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _make_snapshot():
    return {
        "frames": np.full((3, 8, 8), 500.0, dtype=np.float32),
        "dark": np.full((8, 8), 10.0, dtype=np.float32),
        "mask": np.zeros((8, 8), dtype=np.float32),
        "energy_keV": 63.0,
    }


def test_dialog_prefills_energy_and_toggles_advanced(app):
    from midas_gui.auto_attenuation.dialog import AutoAttenuationDialog

    dlg = AutoAttenuationDialog(_make_snapshot())
    assert dlg.energy_spin.value() == pytest.approx(63.0)
    assert dlg.adv_box.isChecked() is False
    assert dlg.chk_dark.isEnabled()
    assert dlg.chk_dark_mask.isEnabled() is False  # no raw dark_stack in snapshot
    dlg.close()


def test_dialog_run_blocks_without_saturation_intensity(app, monkeypatch):
    from midas_gui.auto_attenuation.dialog import AutoAttenuationDialog
    from PyQt5 import QtWidgets

    dlg = AutoAttenuationDialog(_make_snapshot())
    warned = {}
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "warning",
        lambda *a, **k: warned.setdefault("called", True),
    )
    dlg.saturation_spin.setValue(0.0)
    dlg._on_run()
    assert warned.get("called")
    assert dlg._worker is None
    dlg.close()


def test_dialog_saturated_run_blocks_analysis(app, monkeypatch):
    from midas_gui.auto_attenuation.dialog import AutoAttenuationDialog
    from PyQt5 import QtWidgets

    # Slight per-frame jitter (real shot noise) on both the baseline and the
    # saturated pixel, so the default frozen-pixel mask (std < 0.5 across
    # frames) doesn't classify the perfectly-constant synthetic data as
    # "frozen" and mask the saturation away before the check ever runs.
    rng = np.random.default_rng(2)
    snapshot = _make_snapshot()
    snapshot["frames"] += rng.normal(0, 2.0, size=snapshot["frames"].shape).astype(np.float32)
    snapshot["frames"][:, 0, 0] = 99999.0 + rng.normal(0, 50.0, size=3).astype(np.float32)

    dlg = AutoAttenuationDialog(snapshot)
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a, **k: None)
    dlg.saturation_spin.setValue(1000.0)
    dlg.tolerate_spin.setValue(0)
    dlg.skip_frames_spin.setValue(0)

    dlg._on_run()
    assert dlg._worker is not None
    dlg._worker.wait(5000)
    QtWidgets.QApplication.processEvents()

    assert "SATURATION DETECTED" in dlg.log.toPlainText()
    dlg.close()


def test_dialog_shows_source_label(app):
    from midas_gui.auto_attenuation.dialog import AutoAttenuationDialog

    snapshot = _make_snapshot()
    snapshot["source"] = "buffer"
    dlg = AutoAttenuationDialog(snapshot)
    assert "Source: Buffer" in dlg._source_summary_lbl.text()
    dlg.close()

    snapshot["source"] = "loaded"
    dlg = AutoAttenuationDialog(snapshot)
    assert "Source: Loaded data" in dlg._source_summary_lbl.text()
    dlg.close()

    del snapshot["source"]
    dlg = AutoAttenuationDialog(snapshot)
    assert "Source: Unspecified" in dlg._source_summary_lbl.text()
    dlg.close()


def test_refresh_without_snapshot_path_shows_info(app, monkeypatch):
    """A dialog built directly off an in-memory snapshot (no launcher path,
    e.g. the other Qt smoke tests above) can't refresh -- must say so, not
    crash trying to reach a server."""
    from midas_gui.auto_attenuation.dialog import AutoAttenuationDialog
    from PyQt5 import QtWidgets

    dlg = AutoAttenuationDialog(_make_snapshot())
    informed = {}
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "information",
        lambda *a, **k: informed.setdefault("called", True),
    )
    dlg._on_refresh_source()
    assert informed.get("called")
    dlg.close()


def test_refresh_with_no_server_listening_warns(app, monkeypatch, tmp_path):
    from midas_gui.auto_attenuation.dialog import AutoAttenuationDialog
    from midas_gui.auto_attenuation.snapshot import write_snapshot
    from PyQt5 import QtWidgets
    import uuid

    path = tmp_path / "snap.npz"
    write_snapshot(str(path), frames=np.zeros((1, 4, 4), dtype=np.float32))

    dlg = AutoAttenuationDialog(
        _make_snapshot(), snapshot_path=str(path),
        refresh_server_name=f"auto_att_test_no_server_{uuid.uuid4().hex[:8]}")
    warned = {}
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "warning",
        lambda *a, **k: warned.setdefault("called", True),
    )
    dlg._on_refresh_source()
    assert warned.get("called")
    dlg.close()


class _FakeLocalSocket:
    """Stand-in for QtNetwork.QLocalSocket used by dialog._on_refresh_source.

    A real client+server round trip over one QLocalSocket/QLocalServer pair
    needs a running Qt event loop on *both* ends to dispatch socket events;
    a synchronous single-threaded test has neither, so (like
    tests/test_bridge_server.py) the server and client sides are each
    exercised directly instead of over a real socket."""

    def __init__(self, connected, reply, parent=None):
        self._connected = connected
        self._reply = reply
        self.written = None

    def connectToServer(self, name):
        pass

    def waitForConnected(self, timeout=0):
        return self._connected

    def write(self, data):
        self.written = data

    def waitForBytesWritten(self, timeout=0):
        return True

    def waitForReadyRead(self, timeout=0):
        return self._reply is not None

    def readAll(self):
        return self._reply if self._reply is not None else b""

    def disconnectFromServer(self):
        pass


def _patch_fake_socket(monkeypatch, *, connected=True, reply=None):
    from midas_gui.auto_attenuation import dialog as dialog_mod
    monkeypatch.setattr(
        dialog_mod.QtNetwork, "QLocalSocket",
        lambda parent=None: _FakeLocalSocket(connected, reply, parent))


def test_refresh_updates_snapshot_and_checkboxes_on_success(app, monkeypatch, tmp_path):
    """The "main GUI" side (a real write_snapshot, standing in for what
    AutoAttenuationRefreshServer's callback would do) writes a new snapshot
    with a dark_stack the original didn't have; a successful reply makes the
    dialog reload it and re-enable the dependent checkbox."""
    from midas_gui.auto_attenuation.dialog import AutoAttenuationDialog
    from midas_gui.auto_attenuation.snapshot import write_snapshot

    path = tmp_path / "snap.npz"
    write_snapshot(str(path), frames=np.full((3, 8, 8), 500.0, dtype=np.float32),
                   source="loaded")
    _patch_fake_socket(monkeypatch, reply=json.dumps({"ok": True}).encode("utf-8"))

    dlg = AutoAttenuationDialog(
        {"frames": np.full((3, 8, 8), 500.0, dtype=np.float32), "source": "loaded"},
        snapshot_path=str(path))
    assert dlg.chk_dark_mask.isEnabled() is False

    # Simulate the main GUI's response to the refresh request, i.e. what
    # _on_auto_attenuation_refresh_request would have done to `path`.
    write_snapshot(
        str(path), frames=np.full((5, 8, 8), 700.0, dtype=np.float32),
        dark_stack=np.zeros((4, 8, 8), dtype=np.float32), source="buffer")
    dlg._on_refresh_source()

    assert dlg._snapshot["frames"].shape[0] == 5
    assert dlg._snapshot["source"] == "buffer"
    assert dlg.chk_dark_mask.isEnabled() is True
    assert "Source: Buffer" in dlg._source_summary_lbl.text()
    assert "Refreshed data source" in dlg.log.toPlainText()
    dlg.close()


def test_refresh_reports_handler_failure_message(app, monkeypatch, tmp_path):
    from midas_gui.auto_attenuation.dialog import AutoAttenuationDialog
    from midas_gui.auto_attenuation.snapshot import write_snapshot
    from PyQt5 import QtWidgets

    path = tmp_path / "snap.npz"
    write_snapshot(str(path), frames=np.zeros((1, 4, 4), dtype=np.float32))
    reply = json.dumps({
        "ok": False,
        "message": "Capture or load a buffer/stack in the Data Viewer first.",
    }).encode("utf-8")
    _patch_fake_socket(monkeypatch, reply=reply)

    dlg = AutoAttenuationDialog(_make_snapshot(), snapshot_path=str(path))
    warned = {}
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "warning",
        lambda *a, **k: warned.setdefault("message", a[-1]),
    )
    dlg._on_refresh_source()
    assert "Capture or load" in warned.get("message", "")
    dlg.close()


def test_refresh_no_reply_warns(app, monkeypatch, tmp_path):
    from midas_gui.auto_attenuation.dialog import AutoAttenuationDialog
    from midas_gui.auto_attenuation.snapshot import write_snapshot
    from PyQt5 import QtWidgets

    path = tmp_path / "snap.npz"
    write_snapshot(str(path), frames=np.zeros((1, 4, 4), dtype=np.float32))
    _patch_fake_socket(monkeypatch, reply=None)  # waitForReadyRead times out

    dlg = AutoAttenuationDialog(_make_snapshot(), snapshot_path=str(path))
    warned = {}
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "warning",
        lambda *a, **k: warned.setdefault("called", True),
    )
    dlg._on_refresh_source()
    assert warned.get("called")
    dlg.close()


# ── refresh_server.py ──────────────────────────────────────────────────────

class _FakeServerSocket:
    """Stand-in for QtNetwork.QLocalSocket on the server side — only
    `.readAll()` is read and `.write()`/`.flush()`/`.disconnectFromServer()`
    are recorded, matching tests/test_bridge_server.py's `_FakeSocket`."""

    def __init__(self, payload: bytes):
        self._payload = payload
        self.replies = []
        self.disconnected = False

    def readAll(self):
        return self._payload

    def write(self, data):
        self.replies.append(data)

    def flush(self):
        pass

    def disconnectFromServer(self):
        self.disconnected = True


def test_on_refresh_data_dispatches_valid_request(tmp_path):
    from midas_gui.auto_attenuation.refresh_server import AutoAttenuationRefreshServer

    calls = []

    def handler(path):
        calls.append(path)
        return True, None

    srv = AutoAttenuationRefreshServer(handler)
    sock = _FakeServerSocket(json.dumps({
        "type": "refresh_request", "version": 1, "path": str(tmp_path / "snap.npz"),
    }).encode("utf-8"))
    srv._on_refresh_data(sock)

    assert calls == [str(tmp_path / "snap.npz")]
    reply = json.loads(sock.replies[0].decode("utf-8"))
    assert reply == {"type": "refresh_done", "ok": True, "message": None}
    assert sock.disconnected


@pytest.mark.parametrize("payload", [
    b"not json",
    b'{"type": "something_else", "version": 1, "path": "/tmp/x.npz"}',
    b'{"type": "refresh_request", "version": 2, "path": "/tmp/x.npz"}',
    b'{"type": "refresh_request", "version": 1}',
    b'{"type": "refresh_request", "version": 1, "path": ""}',
])
def test_on_refresh_data_rejects_invalid_requests(payload):
    from midas_gui.auto_attenuation.refresh_server import AutoAttenuationRefreshServer

    calls = []
    srv = AutoAttenuationRefreshServer(lambda path: calls.append(path) or (True, None))
    sock = _FakeServerSocket(payload)
    srv._on_refresh_data(sock)

    assert calls == []
    reply = json.loads(sock.replies[0].decode("utf-8"))
    assert reply["ok"] is False


def test_on_refresh_data_reports_handler_exception():
    from midas_gui.auto_attenuation.refresh_server import AutoAttenuationRefreshServer

    def handler(path):
        raise RuntimeError("boom")

    srv = AutoAttenuationRefreshServer(handler)
    sock = _FakeServerSocket(json.dumps({
        "type": "refresh_request", "version": 1, "path": "/tmp/x.npz",
    }).encode("utf-8"))
    srv._on_refresh_data(sock)

    reply = json.loads(sock.replies[0].decode("utf-8"))
    assert reply["ok"] is False
    assert "boom" in reply["message"]


def test_refresh_server_start_removes_stale_socket_and_listens():
    import uuid
    from midas_gui.auto_attenuation.refresh_server import AutoAttenuationRefreshServer

    name = f"auto_att_refresh_test_{uuid.uuid4().hex[:8]}"
    srv = AutoAttenuationRefreshServer(lambda path: (True, None))
    try:
        assert srv.start(name) is True
    finally:
        srv.stop()


def test_write_auto_attenuation_snapshot_wired_in_mainwindow(tmp_path):
    """MainWindow._on_auto_attenuation_refresh_request (the refresh server's
    callback) and _open_auto_attenuation's own snapshot gathering both go
    through _write_auto_attenuation_snapshot -- exercise it directly against
    a real MainWindow/DataLoaderPanel, no subprocess or socket involved."""
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    try:
        import midas_gui.app as app_mod
    except Exception as exc:
        pytest.skip(f"midas_gui.app needs the full MIDAS stack: {exc}")

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = app_mod.MainWindow()
    try:
        path = tmp_path / "snap.npz"
        loader = win._view_tab._loader

        # MainWindow starts up with a default sample stack already loaded
        # (tab_view.py's DEFAULT_NICKEL_H5) -- a real "loaded data" source.
        assert loader.n_frames() > 0
        ok, message = win._on_auto_attenuation_refresh_request(str(path))
        assert ok
        assert message is None
        from midas_gui.auto_attenuation.snapshot import load_snapshot
        snap = load_snapshot(str(path))
        assert snap["frames"].shape[0] == loader.n_frames()
        assert snap["source"] == "loaded"

        # Clearing it out entirely reproduces the no-data guard.
        loader._stack = loader._paths = loader._h5 = None
        loader._nframes = 0
        ok, message = win._on_auto_attenuation_refresh_request(str(path))
        assert not ok
        assert "Capture or load" in message
    finally:
        win._bridge_server.stop()
        win._auto_att_refresh_server.stop()
