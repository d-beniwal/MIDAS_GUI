"""The source HDF5's instrument/ PV snapshot has to survive into the zarr.

MIDAS's own pipeline carries a few hundred EPICS PVs from the detector HDF5
into the final store (``integrator.py:_enrich_zarr_with_metadata``); MIDAS_GUI
reads frames straight into memory and so used to drop all of them. These tests
cover the after-the-fact copy that closes that gap: what gets picked up, how
per-frame arrays are aligned to the output frames, and that both writers —
Batch Integrate and the GSAS-II export — actually put it in the file.

The alignment rule is the interesting part and the reason this isn't a
verbatim copy: the DAQ writes one metadata sample per *acquisition*, lights
and darks in one flat array, so a 10-frame scan carries length-20 metadata.
Matching on raw length (as upstream does) would match nothing at all here.
"""
import numpy as np
import pytest


def _make_h5(path, *, n_light=10, n_dark=10, size=16):
    """A VAREX-shaped file with a richer ``instrument/`` tree than the other
    fixtures need: the three per-acquisition arrays, plus a scalar PV, a
    string PV, a nested subgroup and an empty dataset — the four shapes the
    copy has to survive on a real file."""
    h5py = pytest.importorskip("h5py")
    rng = np.random.default_rng(0)
    n_total = n_light + n_dark
    gaps = np.full(n_total - 1, 7.0)
    gaps[n_light - 1] = 9.5          # the light→dark boundary
    timestamps = np.concatenate([[0.0], np.cumsum(gaps)])
    temperature = np.arange(n_total, dtype=np.float64)
    with h5py.File(str(path), "w") as f:
        f.create_dataset("exchange/data",
                         data=(rng.random((n_light, size, size)) * 100).astype(np.float32))
        f.create_dataset("misc/NDArrayTimeStamp", data=timestamps)
        f.create_dataset("instrument/GSAS2_PVS/Temperature", data=temperature)
        f.create_dataset("instrument/StorageRing/SRCurrent",
                         data=200.0 + np.arange(n_total, dtype=np.float64))
        f.create_dataset("instrument/HRM/energy", data=np.array([71.676]))
        f.create_dataset("instrument/SMS/D/HR/samX",
                         data=np.arange(n_total, dtype=np.float64) * 0.5)
        f.create_dataset("instrument/name", data=np.array([b"20-ID-E"]))
        f.create_dataset("instrument/Encoders/empty", data=np.zeros(0))
        f.create_dataset("active_instrument/station", data=np.array([b""]))
    return temperature


def test_snapshot_walks_the_whole_instrument_tree(tmp_path):
    """Every dataset under the copied groups, keyed by its full path — not a
    curated list, so a PV the DAQ adds tomorrow comes along for free."""
    from midas_gui import h5_metadata

    h5_path = tmp_path / "scan_001.h5"
    _make_h5(h5_path)
    snap = h5_metadata.snapshot(h5_path)

    assert "instrument/GSAS2_PVS/Temperature" in snap
    assert "instrument/HRM/energy" in snap
    assert "instrument/SMS/D/HR/samX" in snap
    assert "active_instrument/station" in snap
    # Image data is already in the zarr as cakes; copying it again would
    # double the archive for nothing.
    assert not any(k.startswith(("exchange/", "misc/")) for k in snap)
    # A zero-length dataset has nothing to carry and would only add an empty
    # array to every output frame.
    assert "instrument/Encoders/empty" not in snap
    # h5py hands back bytes; zarr needs str.
    assert snap["instrument/name"].dtype.kind == "U"


def test_per_frame_arrays_are_chunk_averaged_and_others_left_alone(tmp_path):
    """The alignment rule, on the two array shapes that matter: a length-20
    per-acquisition array over a 10-light-frame scan collapses to one value
    per output frame, while a length-1 PV is copied as-is."""
    from midas_gui import h5_metadata

    h5_path = tmp_path / "scan_001.h5"
    temperature = _make_h5(h5_path, n_light=10, n_dark=10)

    snap = h5_metadata.snapshot(h5_path, frame_ranges=[(0, 4), (5, 9)], n_aligned=10)
    temps = snap["instrument/GSAS2_PVS/Temperature"]
    assert temps.shape == (2,)
    # Averaged over the LIGHT block only — a naive mean over all 20 entries
    # would give 2.0/7.0 here, blending in the dark tail.
    assert temps[0] == pytest.approx(temperature[0:5].mean())
    assert temps[1] == pytest.approx(temperature[5:10].mean())
    # Not per-frame: copied through untouched.
    assert snap["instrument/HRM/energy"] == pytest.approx([71.676])


def test_snapshot_is_quiet_about_a_file_it_cannot_use(tmp_path):
    """A missing file, or one with no instrument tree at all, is a source
    that never had this metadata — not an error."""
    from midas_gui import h5_metadata
    h5py = pytest.importorskip("h5py")

    assert h5_metadata.snapshot(tmp_path / "nope.h5") == {}
    bare = tmp_path / "bare.h5"
    with h5py.File(str(bare), "w") as f:
        f.create_dataset("exchange/data", data=np.zeros((2, 4, 4)))
    assert h5_metadata.snapshot(bare) == {}


def test_h5_context_reports_the_file_and_raw_range_behind_a_frame(tmp_path):
    """``h5_context_for_index`` is what lets a writer ask "where did this
    output frame come from" — the location, as against ``metadata_for_index``'s
    already-reduced values."""
    pytest.importorskip("h5py")
    import midas_gui.workers as wk

    h5_path = tmp_path / "scan_001.h5"
    _make_h5(h5_path, n_light=10, n_dark=10)

    src = wk._HDF5StackGlobSource([h5_path], "exchange/data", chunk_size=5)
    assert src.n_frames == 2
    c0, c1 = src.h5_context_for_index(0), src.h5_context_for_index(1)
    assert c0["path"] == str(h5_path)
    assert c0["frame_ranges"] == [(0, 4)]
    assert c1["frame_ranges"] == [(5, 9)]
    # The light-block length, not the 20 metadata entries and not the 10
    # image frames by coincidence — see _metadata_frame_count.
    assert c0["n_aligned"] == 10
    with pytest.raises(IndexError):
        src.h5_context_for_index(2)


def test_copy_into_zip_round_trips_through_a_real_archive(tmp_path):
    """The whole point: a closed ``.zarr.zip`` comes back out with the tree
    in it, readable by anything that can open a zarr."""
    zarr = pytest.importorskip("zarr")
    pytest.importorskip("h5py")
    from midas_gui import h5_metadata

    h5_path = tmp_path / "scan_001.h5"
    _make_h5(h5_path)

    zip_path = tmp_path / "out.zarr.zip"
    store = zarr.storage.ZipStore(str(zip_path), mode="w")
    root = zarr.open_group(store, mode="w")
    root.create_dataset("REtaMap", data=np.zeros((4, 2, 2)))
    store.close()

    written = h5_metadata.copy_into_zip(zip_path, h5_path,
                                        frame_ranges=[(0, 9)], n_aligned=10)
    assert written > 0

    out = zarr.open(str(zip_path), mode="r")
    assert out["REtaMap"].shape == (4, 2, 2)       # untouched
    assert out["instrument/HRM/energy"][0] == pytest.approx(71.676)
    assert out["instrument/GSAS2_PVS/Temperature"].shape == (1,)


def test_one_rewrite_pass_carries_both_the_tree_and_the_provenance(tmp_path):
    """Editing a zip means extracting and repacking it, so the metadata copy
    and the provenance stamp share one pass. This is the composition that
    lets them: if ``rewrite_zip`` ever stopped handing the directory over,
    one of the two would silently win."""
    zarr = pytest.importorskip("zarr")
    pytest.importorskip("h5py")
    from midas_gui import h5_metadata, provenance

    h5_path = tmp_path / "scan_001.h5"
    _make_h5(h5_path)
    zip_path = tmp_path / "out.zarr.zip"
    store = zarr.storage.ZipStore(str(zip_path), mode="w")
    zarr.open_group(store, mode="w").create_dataset("REtaMap", data=np.zeros((4, 1, 1)))
    store.close()

    snap = h5_metadata.snapshot(h5_path, frame_ranges=[(0, 9)], n_aligned=10)
    entry = provenance.build_entry("test", inputs=[str(h5_path)])
    provenance.rewrite_zip(zip_path, lambda d: (
        h5_metadata.write_into_extracted(d, snap),
        provenance.stamp_extracted(d, entry)))

    out = zarr.open(str(zip_path), mode="r")
    assert "instrument/HRM/energy" in out
    assert out.attrs["provenance_history"][-1]["tool"] == "test"


def test_batch_integrate_writes_the_tree_into_every_frame_store(tmp_path):
    """End to end, the way a user gets one: a real BatchWorker run over a
    real HDF5 stack, and each per-frame archive carries both its own slice of
    the instrument metadata and its provenance entry."""
    pytest.importorskip("torch")
    pytest.importorskip("midas_integrate_v2")
    zarr = pytest.importorskip("zarr")
    pytest.importorskip("h5py")
    from PyQt5 import QtWidgets
    # Held in a local: a bare expression lets the QApplication be collected
    # mid-test, and constructing Qt objects afterwards aborts the interpreter.
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    assert app is not None
    import midas_gui.workers as wk
    from midas_gui.helpers import _build_spec
    from tests.test_batch_zarr_gsas import _tiny_calib_result

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    h5_path = src_dir / "scan_001.h5"
    temperature = _make_h5(h5_path, n_light=10, n_dark=10, size=64)

    out_dir = tmp_path / "out"
    spec = _build_spec(_tiny_calib_result(), r_bin=2.0, eta_bin=5.0)
    worker = wk.BatchWorker(
        spec,
        {"type": "hdf5_stack_glob", "paths": [str(h5_path)],
         "dataset": "exchange/data", "chunk_size": 5, "combine_op": "mean"},
        None, str(out_dir), ["zarr"], "subpixel2", (None, None), None)
    failures = []
    worker.failed.connect(failures.append)
    worker.run()
    assert not failures, failures[0] if failures else ""

    for start, end in ((0, 4), (5, 9)):
        zarr_path = out_dir / "zarr" / f"scan_001.frame_{start}_{end}.ave.zarr.zip"
        assert zarr_path.is_file(), f"expected zarr output at {zarr_path}"
        root = zarr.open(str(zarr_path), mode="r")
        # The tree the writer itself has no slot for.
        assert root["instrument/HRM/energy"][0] == pytest.approx(71.676)
        assert root["instrument/SMS/D/HR/samX"].shape == (1,)
        # Aligned to THIS frame's raw sub-frames, not the file's whole array.
        assert root["instrument/GSAS2_PVS/Temperature"][0] == pytest.approx(
            temperature[start:end + 1].mean())
        # And the provenance stamp still landed in the same pass, now naming
        # the file the tree came from.
        history = root.attrs["provenance_history"]
        assert history[-1]["extra"]["source_h5"] == str(h5_path)
