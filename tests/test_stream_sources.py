"""The Qt-free source layer behind the Data Explorer tab.

Plain pytest on purpose. Nothing in ``midas_gui.stream_sources``'s import
chain touches PyQt5, so none of this repo's forked-child / import-inside-a-
fixture rules apply here, and none of the pyqtgraph teardown limits do
either. If a test in this file ever needs a ``QApplication``, something has
leaked into the data layer and that is the bug.

The load-bearing cases are the exclusions (what the walkers deliberately do
NOT surface) and ``infer_frame_count``, which is the one place this port
knowingly departs from its source because the source is wrong on MIDAS
layout.
"""
from __future__ import annotations

import numpy as np
import pytest


def _h5(tmp_path, name="s.h5", **datasets):
    h5py = pytest.importorskip("h5py")
    p = tmp_path / name
    with h5py.File(p, "w") as f:
        for path, arr in datasets.items():
            f.create_dataset(path, data=arr)
    return str(p)


def _zarr_zip(tmp_path, name="a.zarr.zip", **arrays):
    zarr = pytest.importorskip("zarr")
    p = tmp_path / name
    store = zarr.ZipStore(str(p), mode="w")
    z = zarr.group(store)
    for path, arr in arrays.items():
        z[path] = arr
    store.close()
    return str(p)


# ── discovery: what is found, and in what order ─────────────────────────

def test_motor_named_datasets_rank_above_other_numeric_ones(tmp_path):
    """The hint regex exists so samX/samY surface first in a combo with
    dozens of entries — ranking is the feature, not just filtering."""
    from midas_gui import stream_sources as ss
    path = _h5(tmp_path,
               **{"misc/pressure": np.arange(5.0),
                  "measurement/samX": np.arange(5.0),
                  "other/thing": np.arange(5.0)})
    names = [n for n, _ln, _lo, _hi in ss.discover_motor_datasets(path)]
    assert names, "nothing discovered"
    assert "samX" in names[0], f"motor hit did not rank first: {names}"


def test_a_length_one_dataset_is_not_a_stream(tmp_path):
    """shape[0] >= 2 is what keeps scalar metadata out of the stream list.
    It is also why InstrumentParameters/<key> (all length 1) is invisible
    in a MIDAS archive — correct, not a gap."""
    from midas_gui import stream_sources as ss
    path = _h5(tmp_path, **{"measurement/samX": np.array([1.0]),
                            "measurement/samY": np.arange(4.0)})
    names = [n for n, *_ in ss.discover_motor_datasets(path)]
    assert any("samY" in n for n in names)
    assert not any("samX" in n for n in names)


def test_a_three_dimensional_array_is_not_a_stream(tmp_path):
    """REtaMap is (5, nR, nEta). The 1-D walker must skip it and the 2-D
    walker must skip it too — this is the documented blind spot, asserted
    against the real shape so it cannot regress into a crash."""
    from midas_gui import stream_sources as ss
    path = _zarr_zip(tmp_path, **{"REtaMap": np.zeros((5, 40, 9)),
                                  "Omegas": np.arange(6.0)})
    # Paths come back absolute, as the source emits them.
    assert [n for n, *_ in ss.discover_zarr_datasets(path)] == ["/Omegas"]
    assert ss.discover_zarr_2d_attrs(path) == []


def test_two_dimensional_arrays_are_found_and_reduced(tmp_path):
    from midas_gui import stream_sources as ss
    data = np.arange(12.0).reshape(3, 4)        # rows 0-3, 4-7, 8-11
    path = _zarr_zip(tmp_path, **{"OmegaSumFrame/LastFrameNumber_0": data})
    found = [n for n, *_ in ss.discover_zarr_2d_attrs(path)]
    assert found == ["/OmegaSumFrame/LastFrameNumber_0"]
    key = found[0]
    assert np.allclose(ss.read_zarr_2d_reduced(path, key, "mean"), [1.5, 5.5, 9.5])
    assert np.allclose(ss.read_zarr_2d_reduced(path, key, "sum"), [6, 22, 38])
    assert np.allclose(ss.read_zarr_2d_reduced(path, key, "max"), [3, 7, 11])
    assert np.allclose(ss.read_zarr_2d_reduced(path, key, "min"), [0, 4, 8])


def test_source_kind_detection(tmp_path):
    from midas_gui import stream_sources as ss
    zz = _zarr_zip(tmp_path)
    assert ss.is_zarr(zz) and ss.source_exists(zz)
    h5 = _h5(tmp_path, **{"a/b": np.arange(3.0)})
    assert not ss.is_zarr(h5) and ss.source_exists(h5)
    assert not ss.source_exists("") and not ss.source_exists(str(tmp_path / "nope.h5"))


def test_read_h5_dataset_returns_none_rather_than_raising(tmp_path):
    from midas_gui import stream_sources as ss
    path = _h5(tmp_path, **{"a/vec": np.arange(3.0), "a/img": np.zeros((3, 3))})
    assert np.allclose(ss.read_h5_dataset(path, "a/vec"), [0, 1, 2])
    assert ss.read_h5_dataset(path, "a/missing") is None
    assert ss.read_h5_dataset(path, "a/img") is None, "2-D is not a 1-D stream"
    assert ss.read_h5_dataset(str(tmp_path / "nope.h5"), "a/vec") is None


# ── cake sibling discovery ──────────────────────────────────────────────

def test_cake_siblings_substitute_the_panel_digit(tmp_path):
    """ge1 -> ge1..ge4, so one pick overlays all four panels."""
    from midas_gui import stream_sources as ss
    for n in (1, 2, 3, 4):
        (tmp_path / f"cake_ge{n}.csv").write_text("R_MIN\n1\n")
    found = ss.discover_cake_siblings(str(tmp_path / "cake_ge1.csv"))
    assert [tag for tag, _p in found] == ["ge1", "ge2", "ge3", "ge4"]


def test_a_lone_cake_file_is_still_returned(tmp_path):
    from midas_gui import stream_sources as ss
    p = tmp_path / "cake_ge1.csv"
    p.write_text("R_MIN\n1\n")
    found = ss.discover_cake_siblings(str(p))
    assert len(found) == 1 and found[0][1] == str(p)


# ── infer_frame_count: the deliberate deviation from the source ─────────

def test_omegas_is_authoritative_for_the_frame_count(tmp_path):
    from midas_gui import stream_sources as ss
    path = _zarr_zip(tmp_path, **{"Omegas": np.arange(64.0),
                                  "REtaMap": np.zeros((5, 3520, 9))})
    assert ss.infer_frame_count(path) == 64


def test_the_frame_count_is_not_a_radial_bin_count(tmp_path):
    """The source read IntegrationResult/FrameNr_0.shape[0] as the frame
    count. In a MIDAS archive that array is (nR, nEta), so it returned nR —
    3520 here instead of 3. Counting KEYS is the fix."""
    from midas_gui import stream_sources as ss
    path = _zarr_zip(tmp_path, **{
        "IntegrationResult/FrameNr_0": np.zeros((3520, 9)),
        "IntegrationResult/FrameNr_1": np.zeros((3520, 9)),
        "IntegrationResult/FrameNr_2": np.zeros((3520, 9)),
    })
    assert ss.infer_frame_count(path) == 3


def test_an_empty_integration_result_falls_through(tmp_path):
    """Observed on a real archive: the group exists and is empty. Returning
    0 would make the Pole figure's frame count silently zero."""
    from midas_gui import stream_sources as ss
    zarr = pytest.importorskip("zarr")
    p = tmp_path / "e.zarr.zip"
    store = zarr.ZipStore(str(p), mode="w")
    zarr.group(store).create_group("IntegrationResult")
    store.close()
    assert ss.infer_frame_count(str(p)) is None


def test_frame_count_from_a_raw_hdf5_stack(tmp_path):
    from midas_gui import stream_sources as ss
    assert ss.infer_frame_count(
        _h5(tmp_path, **{"exchange/data": np.zeros((7, 4, 4))})) == 7
    assert ss.infer_frame_count(str(tmp_path / "nope.h5")) is None
