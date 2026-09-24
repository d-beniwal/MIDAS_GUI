"""The Batch Queue's Add-Samples dialog: sample discovery and the picks it returns.

Two halves, split by what they need:

* ``find_samples_below`` — the "one folder per sample" walk. It builds no
  widgets, so it is tested against a real temp tree with no QApplication at
  all. (It lives in ``dialogs``, which imports Qt at module level, so these
  still pull PyQt5 in — but they never construct a widget, which is what
  matters for speed and for not needing an event loop.)
* ``AddSamplesDialog`` itself — constructed, hence forked, with every
  Qt-pulling import deferred into a fixture per STATE.md's rule.

The behaviour worth pinning is what a user would otherwise discover the hard
way: a folder that merely *contains* sample folders is not itself a sample,
darks are skipped by the scan but kept when hand-picked, and a dropped frame
file resolves to its folder.
"""
from pathlib import Path

import pytest

pytestmark = pytest.mark.forked


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _tree(root: Path) -> Path:
    """A beamtime-shaped tree:

        bt/
          s1/ scan_001.h5  scan_002.h5  dark_before_001.h5
          tiffs/ sampleA/ *.tif (3)
                 sampleB/ *.tif (2)
                 darks/   *.tif (2)
          empty/                       (no frames — not a sample)
    """
    (root / "bt/s1").mkdir(parents=True)
    for n in ("scan_001.h5", "scan_002.h5", "dark_before_001.h5"):
        (root / "bt/s1" / n).write_bytes(b"")
    for folder, count in (("sampleA", 3), ("sampleB", 2), ("darks", 2)):
        d = root / "bt/tiffs" / folder
        d.mkdir(parents=True)
        for i in range(count):
            (d / f"f_{i:03d}.tif").write_bytes(b"")
    (root / "bt/empty").mkdir(parents=True)
    return root / "bt"


# ── discovery (no widgets) ───────────────────────────────────────────

def test_finds_hdf5_files_and_frame_folders(tmp_path):
    from midas_gui.dialogs import find_samples_below
    found = find_samples_below(_tree(tmp_path))
    by_kind = {}
    for path, kind in found:
        by_kind.setdefault(kind, []).append(Path(path).name)
    assert sorted(by_kind["hdf5"]) == ["scan_001.h5", "scan_002.h5"]
    assert sorted(by_kind["folder"]) == ["sampleA", "sampleB"]


def test_a_folder_of_sample_folders_is_not_itself_a_sample(tmp_path):
    """`bt/tiffs` contains samples but no loose frames of its own — queueing it
    would integrate every sample's frames as one run."""
    from midas_gui.dialogs import find_samples_below
    names = [Path(p).name for p, kind in find_samples_below(_tree(tmp_path))
             if kind == "folder"]
    assert "tiffs" not in names and "bt" not in names


def test_an_empty_folder_is_not_a_sample(tmp_path):
    from midas_gui.dialogs import find_samples_below
    names = [Path(p).name for p, _k in find_samples_below(_tree(tmp_path))]
    assert "empty" not in names


def test_the_scan_skips_dark_named_files_and_folders(tmp_path):
    """Beamline convention puts darks beside the scan they bracket, so a scan
    that swept them up would integrate bogus frames — the same reason
    DataLoaderPanel.source_cfg drops them."""
    from midas_gui.dialogs import find_samples_below
    names = [Path(p).name for p, _k in find_samples_below(_tree(tmp_path))]
    assert "dark_before_001.h5" not in names
    assert "darks" not in names


@pytest.mark.parametrize("folder", ["dark", "darks", "dark_frames", "DARKS"])
def test_plural_and_variant_dark_folder_names_are_skipped(tmp_path, folder):
    """`helpers.is_dark_like_name` is file-tuned and matches 'dark' but not
    'darks'; the scan widens it for directory names because that plural is
    common in the one-folder-per-sample layout."""
    from midas_gui.dialogs import find_samples_below
    d = tmp_path / "bt" / folder
    d.mkdir(parents=True)
    (d / "f_000.tif").write_bytes(b"")
    (tmp_path / "bt/real").mkdir()
    (tmp_path / "bt/real/f_000.tif").write_bytes(b"")
    names = [Path(p).name for p, _k in find_samples_below(tmp_path)]
    assert names == ["real"]


def test_darks_can_be_kept_deliberately(tmp_path):
    from midas_gui.dialogs import find_samples_below
    names = [Path(p).name for p, _k in
             find_samples_below(_tree(tmp_path), skip_darks=False)]
    assert "dark_before_001.h5" in names and "darks" in names


def test_depth_is_bounded(tmp_path):
    """An unbounded walk on a network mount hangs the UI for minutes."""
    from midas_gui.dialogs import find_samples_below
    deep = tmp_path / "a/b/c/d/e/f/g/h"
    deep.mkdir(parents=True)
    (deep / "frame.tif").write_bytes(b"")
    assert find_samples_below(tmp_path, max_depth=3) == []
    assert find_samples_below(tmp_path, max_depth=20)


def test_a_missing_or_file_root_yields_nothing(tmp_path):
    from midas_gui.dialogs import find_samples_below
    assert find_samples_below(tmp_path / "nope") == []
    f = tmp_path / "a.h5"; f.write_bytes(b"")
    assert find_samples_below(f) == []


# ── the dialog ───────────────────────────────────────────────────────

@pytest.fixture
def dlg(app, tmp_path):
    from midas_gui.dialogs import AddSamplesDialog
    return AddSamplesDialog(start_dir=str(_tree(tmp_path)))


def test_starts_empty_with_add_disabled(dlg):
    assert dlg.samples() == []
    assert not dlg._ok_btn.isEnabled()


def test_scanning_stages_every_sample(dlg, tmp_path):
    dlg._scan_below()
    labels = sorted(s.label for s in dlg.samples())
    assert labels == ["sampleA", "sampleB", "scan_001", "scan_002"]
    assert dlg._ok_btn.isEnabled()


def test_samples_carry_the_right_kind(dlg):
    from midas_gui.batch_queue import KIND_FOLDER, KIND_HDF5
    dlg._scan_below()
    kinds = {s.label: s.kind for s in dlg.samples()}
    assert kinds["scan_001"] == KIND_HDF5
    assert kinds["sampleA"] == KIND_FOLDER


def test_folders_and_files_can_be_added_together(dlg):
    """The gap that made a new dialog necessary — BrowseFilesDialog can return
    files or one folder, never a mix."""
    bt = Path(dlg._current_dir)
    dlg._add_paths([str(bt / "s1/scan_001.h5"), str(bt / "tiffs/sampleA")])
    from midas_gui.batch_queue import KIND_FOLDER, KIND_HDF5
    assert sorted((s.label, s.kind) for s in dlg.samples()) == [
        ("sampleA", KIND_FOLDER), ("scan_001", KIND_HDF5)]


def test_picks_accumulate_across_directories(dlg):
    """The list, not the current directory, is the result."""
    bt = Path(dlg._current_dir)
    dlg._add_paths([str(bt / "s1/scan_001.h5")])
    dlg._navigate(str(bt / "tiffs"))
    dlg._add_paths([str(bt / "tiffs/sampleB")])
    assert sorted(s.label for s in dlg.samples()) == ["sampleB", "scan_001"]


def test_the_same_path_is_not_added_twice(dlg):
    bt = Path(dlg._current_dir)
    dlg._add_paths([str(bt / "s1/scan_001.h5")])
    dlg._add_paths([str(bt / "s1/scan_001.h5")])
    assert len(dlg.samples()) == 1


def test_a_dropped_frame_file_resolves_to_its_folder(dlg):
    """Dragging one TIFF from Finder means "this folder", since a lone frame
    is not a sample."""
    from midas_gui.batch_queue import KIND_FOLDER
    bt = Path(dlg._current_dir)
    dlg._add_paths([str(bt / "tiffs/sampleA/f_000.tif")])
    (s,) = dlg.samples()
    assert (s.label, s.kind) == ("sampleA", KIND_FOLDER)


def test_hand_picked_darks_are_kept(dlg):
    """Explicit beats convention — naming a dark by hand is deliberate, the
    same asymmetry DataLoaderPanel.source_cfg applies."""
    bt = Path(dlg._current_dir)
    dlg._add_paths([str(bt / "s1/dark_before_001.h5")])
    assert [s.label for s in dlg.samples()] == ["dark_before_001"]


def test_removing_and_clearing(dlg):
    dlg._scan_below()
    assert len(dlg.samples()) == 4
    dlg._list.item(0).setSelected(True)
    dlg._remove_selected()
    assert len(dlg.samples()) == 3
    dlg._clear()
    assert dlg.samples() == [] and not dlg._ok_btn.isEnabled()


def test_the_staging_list_shows_frame_counts_for_folders(dlg):
    dlg._scan_below()
    texts = [dlg._list.item(i).text() for i in range(dlg._list.count())]
    folder_rows = [t for t in texts if "sampleA" in t]
    assert folder_rows and "(3 frames)" in folder_rows[0]


def test_the_tree_hides_loose_frame_files(dlg):
    """Thousands of .tif rows would bury the folders that are the samples."""
    filters = dlg._model.nameFilters()
    assert any(f.endswith(".h5") for f in filters)
    assert not any("tif" in f for f in filters)


def test_dropped_urls_reach_the_dialog(dlg, app):
    """The drop plumbing, without a real drag: the list widget emits local
    paths and the dialog stages them."""
    from PyQt5 import QtCore
    bt = Path(dlg._current_dir)
    dlg._list.pathsDropped.emit([str(bt / "s1/scan_002.h5")])
    assert [s.label for s in dlg.samples()] == ["scan_002"]
    assert dlg._list.acceptDrops()
    assert isinstance(dlg._list.pathsDropped, QtCore.pyqtBoundSignal)
