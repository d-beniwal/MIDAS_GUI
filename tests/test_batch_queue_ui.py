"""The Batch Queue tab: the three-level tree, its state round-trip, and the
guards that stop a bad run before it starts.

Forked, with every PyQt5 / ``midas_gui`` GUI import deferred into a fixture —
see STATE.md on module-level PyQt5 imports and the forked-child CoreFoundation
crash. ``tests/test_set_raw_frame.py`` is the reference shape.

The model, the path mirroring and the scheduling policy are all tested Qt-free
elsewhere (``test_batch_queue_model``, ``test_batch_queue_paths``,
``test_queue_policy``); what is left for this file is the widget layer — that
the tree mirrors the model, that a saved project restores the whole queue, and
that Run refuses rather than half-starting.
"""
import pytest

pytestmark = pytest.mark.forked


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def tab(app):
    from midas_gui.tab_queue import BatchQueueTab
    return BatchQueueTab()


def _populate(tab, out_root="/out", data_root="/data/bt"):
    """Two calibrations; the first with two corrections nodes, mixed kinds."""
    from midas_gui.batch_queue import (BatchQueue, CalibrationNode,
                                       CorrectionsNode, Sample)
    tab._queue = BatchQueue(
        calibrations=[
            CalibrationNode(
                name="AgBH", calib_snapshot={"Lsd": 1.0e6},
                mask_sources=[{"kind": "file", "path": "/m/beamstop.tif",
                               "enabled": True}],
                corrections=[
                    CorrectionsNode(name="Corr 1", dark="/d/dark_001.tif",
                                    samples=[Sample("/data/bt/s1/scan_001.h5"),
                                             Sample("/data/bt/s1/scan_002.h5")]),
                    CorrectionsNode(name="Corr 2", dark="/d/dark_099.tif",
                                    samples=[Sample("/data/bt/tiffs/sampleA")]),
                ]),
            CalibrationNode(name="CeO2", source="file", file_path="/c/ceria.txt",
                            corrections=[CorrectionsNode(
                                samples=[Sample("/data/bt/s2/scan_010.h5")])]),
        ], data_root=data_root, out_root=out_root)
    tab._data_root.setText(data_root or "")
    tab._out_root.setText(out_root or "")
    tab._rebuild_tree()
    return tab


# ── construction ─────────────────────────────────────────────────────

def test_tab_builds_empty(tab):
    assert tab._tree.topLevelItemCount() == 0
    assert tab._queue.sample_count() == 0


def test_default_concurrency_is_conservative(tab):
    """Each in-flight sample holds its own frames — the ceiling is RAM."""
    import os
    assert 1 <= tab._concurrency.value() <= max(1, min(4, (os.cpu_count() or 2) // 2))


# ── the tree mirrors the model ───────────────────────────────────────

def test_tree_has_three_levels(tab):
    _populate(tab)
    assert tab._tree.topLevelItemCount() == 2
    agbh = tab._tree.topLevelItem(0)
    assert "AgBH" in agbh.text(0)
    assert agbh.childCount() == 2                    # two corrections nodes
    assert agbh.child(0).childCount() == 2           # two samples under the first
    assert agbh.child(1).childCount() == 1


def test_calibration_row_shows_its_source_and_mask(tab):
    _populate(tab)
    assert "from Calibrate tab" in tab._tree.topLevelItem(0).text(1)
    assert "beamstop.tif" in tab._tree.topLevelItem(0).text(1)
    assert "ceria.txt" in tab._tree.topLevelItem(1).text(1)


def test_corrections_row_shows_its_frames(tab):
    _populate(tab)
    assert "dark_001.tif" in tab._tree.topLevelItem(0).child(0).text(1)
    assert tab._tree.topLevelItem(1).child(0).text(1) == "no corrections"


def test_sample_rows_show_path_and_kind_icon(tab):
    _populate(tab)
    first = tab._tree.topLevelItem(0).child(0).child(0)
    assert first.text(1) == "/data/bt/s1/scan_001.h5"
    assert "📄" in first.text(0)
    folder = tab._tree.topLevelItem(0).child(1).child(0)
    assert "📁" in folder.text(0)


def test_unchecking_a_sample_disables_it_in_the_model(tab, app):
    from PyQt5 import QtCore
    _populate(tab)
    item = tab._tree.topLevelItem(0).child(0).child(1)
    item.setCheckState(0, QtCore.Qt.Unchecked)
    assert tab._queue.calibrations[0].corrections[0].samples[1].enabled is False
    assert tab._queue.sample_count(enabled_only=True) == 3


# ── state round-trip ─────────────────────────────────────────────────

def test_state_round_trips_the_whole_queue(tab, app):
    from midas_gui.tab_queue import BatchQueueTab
    _populate(tab)
    tab._r_bin.setValue(2.5)
    state = tab.get_state()

    other = BatchQueueTab()
    other.set_state(state)
    assert other._queue.sample_count() == 4
    assert [c.name for c in other._queue.calibrations] == ["AgBH", "CeO2"]
    assert other._queue.calibrations[0].corrections[1].dark == "/d/dark_099.tif"
    assert other._tree.topLevelItemCount() == 2
    assert other._r_bin.value() == 2.5
    assert other._data_root.text() == "/data/bt"
    assert other.get_state() == state


def test_state_survives_json(tab):
    """It is stored as JSON in the project's /gui_workspace."""
    import json
    _populate(tab)
    assert json.loads(json.dumps(tab.get_state()))["queue"]["data_root"] == "/data/bt"


def test_empty_state_is_tolerated(tab):
    tab.set_state({})
    tab.set_state(None)
    assert tab._queue.sample_count() == 0


# ── the mapping preview ──────────────────────────────────────────────

def test_preview_shows_the_mapping(tab):
    _populate(tab)
    text = tab._preview.text()
    assert "4 sample(s)" in text and "/data/bt" in text
    assert "/out/s1/scan_001" in text


def test_preview_warns_without_an_output_root(tab):
    _populate(tab, out_root="")
    assert "No output root set." in tab._preview.text()


def test_preview_flags_unrooted_samples(tab):
    from midas_gui.batch_queue import Sample
    _populate(tab)
    tab._queue.calibrations[0].corrections[0].samples.append(Sample("/elsewhere/x.h5"))
    tab._refresh_preview()
    assert "_unrooted" in tab._preview.text()


# ── run guards ───────────────────────────────────────────────────────

def test_run_refuses_without_an_output_root(tab, monkeypatch):
    """Refuses rather than half-starting — a queue that dies partway leaves
    some samples with output and some without."""
    from PyQt5 import QtWidgets
    warned = {}
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        lambda *a, **k: warned.setdefault("msg", a[2]))
    _populate(tab, out_root="")
    tab._run()
    assert "No output root set." in warned.get("msg", "")
    assert tab._scheduler is None


def test_run_refuses_on_duplicate_output_directories(tab, monkeypatch, tmp_path):
    from PyQt5 import QtWidgets
    from midas_gui.batch_queue import Sample
    warned = {}
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        lambda *a, **k: warned.setdefault("msg", a[2]))
    _populate(tab, out_root=str(tmp_path))
    # /data/bt/s1/scan_001.h5 and the folder /data/bt/s1/scan_001 collide.
    tab._queue.calibrations[0].corrections[0].samples.append(
        Sample("/data/bt/s1/scan_001"))
    tab._run()
    assert "same output directory" in warned.get("msg", "")
    assert tab._scheduler is None


def test_run_refuses_an_unwritable_output_root(tab, monkeypatch):
    from PyQt5 import QtWidgets
    warned = {}
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning",
                        lambda *a, **k: warned.setdefault("msg", a[2]))
    _populate(tab, out_root="/proc/nope/definitely-not-writable")
    tab._run()
    assert warned.get("msg")
    assert tab._scheduler is None


# ── calibration snapshotting ─────────────────────────────────────────

def test_a_later_recalibration_does_not_change_a_queued_stage(tab):
    """The snapshot is taken when the node is created; re-fitting the calibrant
    this afternoon must not silently alter a queue built this morning."""
    from types import SimpleNamespace
    _populate(tab)
    before = dict(tab._queue.calibrations[0].calib_snapshot)
    tab.set_calibration(SimpleNamespace(Lsd=999.0, BC_y=1.0, BC_z=2.0))
    assert tab._queue.calibrations[0].calib_snapshot == before


def test_set_calibration_is_accepted_before_any_node_exists(tab):
    from types import SimpleNamespace
    tab.set_calibration(SimpleNamespace(Lsd=1.0))
    assert tab._calib_result is not None


# ── settings ─────────────────────────────────────────────────────────

def test_copy_from_batch_integrate_pulls_the_settings(tab):
    tab.set_settings_provider(lambda: {
        "kernel": "polygon", "r_bin": 3.0, "e_bin": 2.0, "r_min": 5.0,
        "r_max": 900.0, "eta_min": -90.0, "eta_max": 90.0,
        "fmt": ["csv", "zarr"], "weighted": False})
    tab._copy_from_batch()
    st = tab._settings_dict()
    assert st["kernel"] == "polygon" and st["r_bin"] == 3.0
    assert st["eta_min"] == -90.0 and st["weighted"] is False
    assert set(st["fmt"]) == {"csv", "zarr"}


def test_copy_ignores_a_kernel_this_build_does_not_have(tab):
    """A queue saved by a newer build, or a renamed kernel, must not wipe the
    current selection or raise — the combo keeps what it had."""
    tab.set_settings_provider(lambda: {"kernel": "no_such_kernel", "r_bin": 4.0})
    tab._copy_from_batch()
    st = tab._settings_dict()
    assert st["kernel"] == "subpixel2" and st["r_bin"] == 4.0


def test_copy_without_a_provider_is_harmless(tab):
    tab._copy_from_batch()
    assert tab._settings_dict()["r_bin"] == 1.0


# ── removal ──────────────────────────────────────────────────────────

def test_removing_a_calibration_takes_its_subtree(tab):
    _populate(tab)
    tab._tree.topLevelItem(0).setSelected(True)
    tab._remove_selected()
    assert [c.name for c in tab._queue.calibrations] == ["CeO2"]
    assert tab._queue.sample_count() == 1


def test_removing_several_samples_at_once(tab):
    """Removal is bottom-up so earlier deletions cannot shift later indices."""
    _populate(tab)
    corr = tab._tree.topLevelItem(0).child(0)
    corr.child(0).setSelected(True)
    corr.child(1).setSelected(True)
    tab._remove_selected()
    assert tab._queue.calibrations[0].corrections[0].samples == []
    assert tab._queue.sample_count() == 2


# ── combining sub-frames ─────────────────────────────────────────────

def test_combine_defaults_match_batch_integrate(tab):
    """0 = combine every sub-frame in a file into one, same as the Batch
    Integrate control this mirrors."""
    st = tab._settings_dict()
    assert st["chunk_size"] == 0 and st["combine_op"] == "mean"


def test_combine_setting_reaches_the_source_cfg(tab):
    """The regression this exists for: with chunk_size left at 0, an HDF5
    sample holding N distinct scan points was silently averaged into ONE
    profile — ten frames in, one CSV out, no error. The queue had no control
    to change it, so the setting is exposed and must actually be plumbed."""
    from midas_gui.batch_queue import Sample, sample_source_cfg
    tab._combine_chunk.setValue(1)
    tab._combine_op.setCurrentIndex(tab._combine_op.findData("sum"))
    st = tab._settings_dict()
    cfg = sample_source_cfg(Sample("/d/scan.h5"),
                            chunk_size=st["chunk_size"] or None,
                            combine_op=st["combine_op"])
    assert cfg["chunk_size"] == 1 and cfg["combine_op"] == "sum"


def test_zero_means_whole_file_not_literal_zero(tab):
    """0 must reach the reader as None ('combine the whole file'), never as a
    chunk size of zero."""
    from midas_gui.batch_queue import Sample, sample_source_cfg
    st = tab._settings_dict()
    assert st["chunk_size"] == 0
    cfg = sample_source_cfg(Sample("/d/scan.h5"), chunk_size=st["chunk_size"] or None)
    assert cfg["chunk_size"] is None


def test_combine_round_trips_through_state(tab, app):
    from midas_gui.tab_queue import BatchQueueTab
    tab._combine_chunk.setValue(4)
    tab._combine_op.setCurrentIndex(tab._combine_op.findData("median"))
    other = BatchQueueTab()
    other.set_state(tab.get_state())
    assert other._settings_dict()["chunk_size"] == 4
    assert other._settings_dict()["combine_op"] == "median"
