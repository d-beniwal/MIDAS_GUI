"""Large / network-mounted HDF5: one open per load, off-GUI-thread probes,
bounded frame cache with read-ahead.  Behaviour on normal (fast) files must be
identical to the old synchronous loader — the data read is checked against
``_load_image`` directly.
"""
from __future__ import annotations

import threading
import time

import h5py
import numpy as np
import pytest
from PyQt5 import QtCore, QtWidgets

from midas_gui import helpers
from midas_gui.async_io import AdaptiveCall
from midas_gui.helpers import FrameCache
from midas_gui.widgets import DataLoaderPanel


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _make_h5(path, n=6, shape=(8, 10), energy_kev=None, dtype=np.uint16):
    data = (np.arange(n * shape[0] * shape[1]).reshape(n, *shape) % 4000).astype(dtype)
    with h5py.File(path, "w") as f:
        f.create_dataset("exchange/data", data=data, chunks=(1, *shape))
        f.create_dataset("exchange/other", data=np.zeros((3, 3, 3), np.float32))
        f.create_dataset("scalar", data=1.0)
        if energy_kev is not None:
            f.create_dataset("instrument/HEM/Energy", data=energy_kev)
    return data


def _pump(app, cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    return False


class _OpenCounter:
    """Counts opens made through helpers.open_h5_read."""
    def __init__(self, monkeypatch):
        self.n = 0
        real = helpers.open_h5_read

        def counting(path):
            self.n += 1
            return real(path)
        monkeypatch.setattr(helpers, "open_h5_read", counting)


# ── FrameCache ───────────────────────────────────────────────────────────────
def test_frame_cache_is_byte_bounded_lru():
    c = FrameCache(max_bytes=3 * 100, min_items=1)
    a = lambda: np.zeros(100, np.uint8)
    for k in "abc":
        c.put(k, a())
    c.get("a")                       # a is now most recent
    c.put("d", a())                  # evicts the least recent: b
    assert "a" in c and "d" in c and "b" not in c


def test_frame_cache_keeps_min_items_even_if_oversized():
    c = FrameCache(max_bytes=10, min_items=2)
    c.put("a", np.zeros(100, np.uint8)); c.put("b", np.zeros(100, np.uint8))
    c.put("c", np.zeros(100, np.uint8))
    assert len(c) == 2


def test_frame_cache_entries_are_read_only_and_stale_gen_is_dropped():
    c = FrameCache()
    c.put("a", np.zeros(4))
    with pytest.raises(ValueError):
        c.get("a")[0] = 1
    g = c.gen
    c.clear()
    c.put("late", np.zeros(4), gen=g)          # a read that began before clear()
    assert "late" not in c
    c.put("ok", np.zeros(4), gen=c.gen)
    assert "ok" in c


# ── probe / cache ────────────────────────────────────────────────────────────
def test_probe_h5_reads_everything_in_a_single_open(tmp_path, monkeypatch):
    p = tmp_path / "a.h5"
    data = _make_h5(p, energy_kev=65.0)
    counter = _OpenCounter(monkeypatch)
    pr = helpers.probe_h5(str(p), "exchange/data", first_frame=True, listing=True)
    assert counter.n == 1
    assert pr.shape == data.shape
    assert pr.frame.dtype == np.uint16 and np.array_equal(pr.frame, data[0])
    assert pr.wavelength == pytest.approx(helpers.HC_KEV_A / 65.0)
    assert {n for n, _ in pr.items} == {"exchange/data", "exchange/other"}  # ≥2-D only


def test_probe_h5_missing_dataset_has_no_shape(tmp_path):
    p = tmp_path / "a.h5"
    _make_h5(p)
    pr = helpers.probe_h5(str(p), "nope/x")
    assert pr.shape is None and pr.frame is None


def test_listing_is_cached_until_the_file_changes(tmp_path, monkeypatch):
    p = tmp_path / "a.h5"
    _make_h5(p)
    counter = _OpenCounter(monkeypatch)
    helpers._H5_PROBE_CACHE.clear()
    first = helpers.h5_probe_cached(str(p))
    again = helpers.h5_probe_cached(str(p))
    assert counter.n == 1 and again is first
    assert helpers.h5_dataset_shape_cached(str(p), "exchange/data") == (6, 8, 10)
    _make_h5(p, n=9, shape=(4, 4))               # rewritten → different size/mtime
    fresh = helpers.h5_probe_cached(str(p))
    assert counter.n == 2
    assert helpers.h5_dataset_shape_cached(str(p), "exchange/data") == (9, 4, 4)
    assert fresh is not first
    assert helpers.h5_probe_cached(str(tmp_path / "missing.h5")) is None


# ── AdaptiveCall ─────────────────────────────────────────────────────────────
def test_adaptive_call_fast_returns_synchronously(app):
    assert AdaptiveCall(lambda: 7).run(1.0) == (7, None)


def test_adaptive_call_none_budget_always_waits(app):
    def slow():
        time.sleep(0.15)
        return "done"
    assert AdaptiveCall(slow).run(None) == ("done", None)


def test_adaptive_call_slow_delivers_once_via_signal(app):
    gate = threading.Event()
    call = AdaptiveCall(lambda: gate.wait(5) and "late")
    got = []
    call.finished.connect(lambda r, e: got.append((r, e)))
    assert call.run(0.02) is None                # GUI is released immediately
    gate.set()
    assert _pump(app, lambda: got)
    app.processEvents()
    assert got == [("late", None)]


def test_adaptive_call_reports_exceptions(app):
    def boom():
        raise KeyError("x")
    res, exc = AdaptiveCall(boom).run(1.0)
    assert res is None and isinstance(exc, KeyError)


# ── DataLoaderPanel ──────────────────────────────────────────────────────────
@pytest.fixture
def panel(app):
    helpers._H5_PROBE_CACHE.clear()
    return DataLoaderPanel(mode="stack")


def test_load_opens_the_file_once_and_matches_old_reader(panel, tmp_path, monkeypatch):
    p = tmp_path / "a.h5"
    data = _make_h5(p, energy_kev=65.0)
    helpers._H5_PROBE_CACHE.clear()
    counter = _OpenCounter(monkeypatch)
    panel._path_ed.blockSignals(True); panel._path_ed.setText(str(p))
    panel._path_ed.blockSignals(False)           # skip the dropdown listing here
    panel._ds_combo.setEditText("exchange/data")
    panel._load()
    assert counter.n == 1                        # shape + frame 0 + energy: one open
    assert panel.n_frames() == 6
    assert np.array_equal(panel._cur, data[0].astype(np.float32))
    for i in range(6):                           # every frame equals the old reader
        old = helpers._load_image(str(p), data_loc="exchange/data", frame=i)
        assert np.array_equal(panel._get_frame(i), old)


def test_detected_wavelength_comes_from_the_probe(panel, tmp_path, monkeypatch):
    p = tmp_path / "a.h5"
    _make_h5(p, energy_kev=65.0)
    seen = {}
    real = helpers.detect_geometry_from_path
    import midas_gui.widgets as W
    monkeypatch.setattr(W, "detect_geometry_from_path",
                        lambda path, **kw: seen.update(kw) or real(path, **kw))
    counter = _OpenCounter(monkeypatch)
    panel.set_path(str(p))
    # listing (1 open, cached) + load (1 open): no third open for the wavelength
    assert counter.n == 2
    assert seen["wavelength"] == pytest.approx(helpers.HC_KEV_A / 65.0)


def test_returned_frames_are_fresh_copies(panel, tmp_path):
    p = tmp_path / "a.h5"
    data = _make_h5(p)
    panel.set_path(str(p), dataset="exchange/data")
    f = panel._get_frame(0)
    f += 1000                                    # callers correct frames in place
    assert f.dtype == np.float32
    assert np.array_equal(panel._get_frame(0), data[0].astype(np.float32))


def test_dropdown_lists_datasets_synchronously_for_a_fast_file(panel, tmp_path):
    p = tmp_path / "a.h5"
    _make_h5(p)
    panel._path_ed.setText(str(p))
    names = [panel._ds_combo.itemData(i) for i in range(panel._ds_combo.count())]
    assert set(names) == {"exchange/data", "exchange/other"}
    assert panel._dataset() == "exchange/data"   # default = first ≥3-D stack


def test_missing_dataset_reports_an_error(panel, tmp_path, monkeypatch):
    p = tmp_path / "a.h5"
    _make_h5(p)
    shown = []
    import midas_gui.widgets as W
    monkeypatch.setattr(W, "show_error", lambda parent, title, msg: shown.append(msg))
    panel.set_path(str(p), dataset="no/such")
    assert panel.n_frames() == 0
    assert shown and "no/such" in shown[0]


def test_slow_storage_does_not_block_and_applies_result_later(app, panel, tmp_path,
                                                              monkeypatch):
    p = tmp_path / "a.h5"
    data = _make_h5(p)
    gate = threading.Event()
    import midas_gui.widgets as W
    real = W.probe_h5

    def slow_probe(*a, **kw):
        gate.wait(5)
        return real(*a, **kw)
    monkeypatch.setattr(W, "probe_h5", slow_probe)
    panel._path_ed.blockSignals(True); panel._path_ed.setText(str(p))
    panel._path_ed.blockSignals(False)
    panel._ds_combo.setEditText("exchange/data")
    panel._io_budget = 0.02                      # interactive load, tiny wait budget
    panel._load()
    assert panel.n_frames() == 0                 # returned without waiting
    assert "Loading" in panel._info.text()
    gate.set()
    assert _pump(app, lambda: panel.n_frames() == 6)
    assert np.array_equal(panel._cur, data[0].astype(np.float32))


def test_a_newer_load_supersedes_a_slow_one(app, panel, tmp_path, monkeypatch):
    a, b = tmp_path / "a.h5", tmp_path / "b.h5"
    _make_h5(a, n=6); _make_h5(b, n=3)
    gate = threading.Event()
    import midas_gui.widgets as W
    real = W.probe_h5

    def slow_for_a(path, *args, **kw):
        if str(path).endswith("a.h5"):
            gate.wait(5)
        return real(path, *args, **kw)
    monkeypatch.setattr(W, "probe_h5", slow_for_a)
    panel._io_budget = 0.02
    for f in (a, b):
        panel._path_ed.blockSignals(True); panel._path_ed.setText(str(f))
        panel._path_ed.blockSignals(False)
        panel._ds_combo.setEditText("exchange/data")
        panel._load()
    assert _pump(app, lambda: panel.n_frames() == 3)      # b finished
    gate.set()
    time.sleep(0.1); app.processEvents(); app.processEvents()
    assert panel.n_frames() == 3                          # late a was discarded


def test_set_path_stays_synchronous_even_when_storage_is_slow(panel, tmp_path,
                                                              monkeypatch):
    p = tmp_path / "a.h5"
    _make_h5(p)
    import midas_gui.widgets as W
    real = W.probe_h5

    def slowish(*a, **kw):
        time.sleep(0.15)                          # > the 0.25 s GUI budget is not needed:
        return real(*a, **kw)                     # budget is forced off in set_path
    monkeypatch.setattr(W, "probe_h5", slowish)
    panel._io_budget = 0.01
    panel.set_path(str(p), dataset="exchange/data")
    assert panel.n_frames() == 6                  # ready the moment set_path returns
    assert panel._io_budget == 0.01               # budget restored afterwards


def test_slow_read_triggers_readahead_and_hits_the_cache(app, panel, tmp_path,
                                                         monkeypatch):
    p = tmp_path / "a.h5"
    data = _make_h5(p, n=8)
    panel.set_path(str(p), dataset="exchange/data")
    panel._SLOW_READ_S = 0.0                      # treat every read as slow
    panel._last_read_s = 1.0
    panel._set_frame(2)                           # stepping forward from 0
    assert _pump(app, lambda: not panel._prefetching and
                 panel._frame_key(3) in panel._fcache and
                 panel._frame_key(4) in panel._fcache)
    counter = _OpenCounter(monkeypatch)
    nxt = panel._get_frame(3)                     # served from RAM, no file open
    assert counter.n == 0
    assert np.array_equal(nxt, data[3].astype(np.float32))


def test_fast_local_reads_do_not_prefetch(panel, tmp_path):
    p = tmp_path / "a.h5"
    _make_h5(p, n=8)
    panel.set_path(str(p), dataset="exchange/data")
    panel._last_read_s = 0.0
    panel._set_frame(2)
    assert not panel._prefetching
    assert panel._frame_key(3) not in panel._fcache


def test_reload_clears_the_frame_cache(panel, tmp_path):
    p = tmp_path / "a.h5"
    _make_h5(p, n=4)
    panel.set_path(str(p), dataset="exchange/data")
    panel._set_frame(2)
    assert panel._frame_key(2) in panel._fcache
    new = _make_h5(p, n=4, dtype=np.uint16)
    new += 1
    with h5py.File(p, "w") as f:
        f.create_dataset("exchange/data", data=new)
    panel._load()
    assert panel._frame_key(2) not in panel._fcache
    assert np.array_equal(panel._get_frame(2), new[2].astype(np.float32))


def test_prefetch_is_dropped_after_a_source_change(panel, tmp_path):
    p = tmp_path / "a.h5"
    _make_h5(p, n=4)
    panel.set_path(str(p), dataset="exchange/data")
    gen = panel._fcache.gen
    panel._fcache.clear()                         # source changed mid-prefetch
    panel._prefetch_worker(gen, panel._h5, None, [1, 2])
    assert len(panel._fcache) == 0


def test_folder_of_tiffs_still_loads_and_caches(app, panel, tmp_path):
    import tifffile
    for i in range(3):
        tifffile.imwrite(tmp_path / f"f_{i:03d}.tif", np.full((4, 5), i, np.float32))
    panel.set_path(str(tmp_path))
    assert panel.n_frames() == 3
    panel._set_frame(1)
    assert np.all(panel._get_frame(1) == 1)
    assert panel._frame_key(1) in panel._fcache
