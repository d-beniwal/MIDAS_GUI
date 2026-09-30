"""End-to-end tests for Batch Integrate's zarr cake output wiring.

Complements ``test_batch_zarr_gsas.py`` (which pins the GSAS-II-facing file
schema) by driving a real ``BatchWorker`` run: the interesting part here is the
wiring — zarr forces the cake to be computed even when multi-azimuth output is
off, is excluded from the per-frame text-format loop, and must not disturb the
existing profile/HDF5 paths.

Output layout (since the zarr writer was rewired onto ``write_gsas_zarr_zip``):
one zarr **per combined output frame** under ``<out>/zarr/<fid>.ave.zarr.zip``,
lineouts under ``<out>/<fmt>/``, and the single whole-run HDF5 under
``<out>/h5/``.
"""
from types import SimpleNamespace

import numpy as np
import pytest


def _tiny_calib_result(**overrides):
    fields = dict(
        Lsd=200000.0, BC_y=32.0, BC_z=32.0, tx=0.0, ty=0.0, tz=0.0,
        distortion={}, pxY=200.0, pxZ=200.0,
        NrPixelsY=64, NrPixelsZ=64, wavelength_A=0.1729,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _make_tiff_frames(tmp_path, n=2, size=64):
    tifffile = pytest.importorskip("tifffile")
    rng = np.random.default_rng(0)
    paths = []
    for i in range(n):
        p = tmp_path / f"frame_{i:04d}.tif"
        tifffile.imwrite(str(p),
                         (rng.random((size, size)) * 100 + 10).astype(np.float32))
        paths.append(str(p))
    return paths


@pytest.fixture(scope="module")
def app():
    from PyQt5 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _run(app, tmp_path, fmts, *, multi_azimuth=False, n_frames=2, out_dir=None,
         corrections=(None, None), omega_cfg=None):
    pytest.importorskip("torch")
    pytest.importorskip("midas_integrate_v2")
    import midas_gui.workers as wk
    from midas_gui.helpers import _build_spec

    paths = _make_tiff_frames(tmp_path / "in", n=n_frames)
    spec = _build_spec(_tiny_calib_result(), r_bin=2.0, eta_bin=45.0)
    worker = wk.BatchWorker(
        spec, {"type": "tiff_list", "paths": paths}, None, out_dir, fmts,
        "subpixel2", corrections, None, multi_azimuth=multi_azimuth,
        omega_cfg=omega_cfg)
    results, failures, logs = {}, [], []
    worker.finished.connect(results.update)
    worker.failed.connect(failures.append)
    worker.log_line.connect(logs.append)
    worker.run()   # direct call, not .start() — no real QThread spawned
    assert not failures, failures[0]
    return results, logs


@pytest.fixture
def in_dir(tmp_path):
    (tmp_path / "in").mkdir()
    return tmp_path


def test_zarr_output_is_written_once_per_frame(app, in_dir):
    """One zarr per combined output frame, named from that frame's id — not
    one bundled store for the whole run."""
    pytest.importorskip("zarr")
    out = in_dir / "out"
    _run(app, in_dir, ["zarr"], n_frames=3, out_dir=out)

    zips = sorted(p.name for p in (out / "zarr").glob("*.zarr.zip"))
    assert zips == [f"frame_{i:04d}.ave.zarr.zip" for i in range(3)]


def test_zarr_output_works_with_multi_azimuth_off(app, in_dir):
    """want_cake has to be forced on by zarr alone — multi_azimuth defaults
    off, and without this the cake would never be computed and the store
    would come out empty."""
    zarr = pytest.importorskip("zarr")
    out = in_dir / "out"
    results, _ = _run(app, in_dir, ["zarr"], multi_azimuth=False, out_dir=out)

    # The returned profiles stay 1-D per frame (multi-azimuth is still off)...
    assert np.asarray(results["profiles"]).ndim == 2
    # ...but the zarr store still holds a real 2-D cake.
    path = next((out / "zarr").glob("*.zarr.zip"))
    root = zarr.open(zarr.ZipStore(str(path), mode="r"), mode="r")
    cake = np.asarray(root["OmegaSumFrame"]["LastFrameNumber_0"])
    assert cake.ndim == 2 and min(cake.shape) > 1


def test_zarr_survives_physics_corrections_and_keeps_the_same_bin_area(app, in_dir):
    """Polarization / solid-angle make build_integration_context leave ``geom``
    None on purpose, and the zarr branch used to hand that None straight to
    ``count_cake`` — ``AttributeError: 'NoneType' object has no attribute
    'n_pixels_z'``, reported from a live run.

    BinArea (/REtaMap row 3) is documented as a property of the geometry alone,
    so the store written with corrections on must carry byte-identical areas to
    the one written with them off.  That is the whole reason the fix builds a
    geometry rather than reusing the corrections path's own counts cake, which
    is normalised differently and folds the correction factors in.
    """
    zarr = pytest.importorskip("zarr")
    from midas_integrate_v2 import PolarizationCorrection, SolidAngleCorrection

    def _bin_area(out, corrections):
        _run(app, in_dir, ["zarr"], n_frames=1, out_dir=out,
             corrections=corrections)
        path = next((out / "zarr").glob("*.zarr.zip"))
        root = zarr.open(zarr.ZipStore(str(path), mode="r"), mode="r")
        return np.asarray(root["REtaMap"])[3]

    # pol_plane_eta_deg=0 deliberately, NOT the real 90: BinArea must be a
    # property of the geometry alone, so a physically wrong plane still has to
    # leave it untouched. See constants.POL_PLANE_HORIZONTAL_ETA_DEG.
    on = _bin_area(in_dir / "out_corr",
                   (PolarizationCorrection(pol_fraction=0.99,
                                           pol_plane_eta_deg=0.0),
                    SolidAngleCorrection()))
    off = _bin_area(in_dir / "out_plain", (None, None))

    assert np.any(on > 0), "BinArea row came out empty"
    np.testing.assert_array_equal(on, off)


def test_zarr_is_stamped_with_batch_provenance(app, in_dir):
    zarr = pytest.importorskip("zarr")
    out = in_dir / "out"
    _run(app, in_dir, ["zarr"], out_dir=out)
    path = next((out / "zarr").glob("*.zarr.zip"))
    root = zarr.open(zarr.ZipStore(str(path), mode="r"), mode="r")
    history = root.attrs["provenance_history"]
    assert history[0]["tool"] == "midas_gui.batch_integrate"
    assert "cake_params" in history[0]


def test_zarr_is_excluded_from_the_per_frame_text_writer(app, in_dir):
    """"zarr" is handled by its own writer; it must not also fall through to
    the lineout loop and produce a `<frame>.zarr` text file in csv/."""
    out = in_dir / "out"
    _run(app, in_dir, ["zarr", "csv"], out_dir=out)
    assert list((out / "zarr").glob("*.zarr.zip"))
    assert list((out / "csv").glob("*.csv")), "csv still written"
    assert not list(out.rglob("*.zarr")), "no bare .zarr from the text writer"


def test_h5_output_is_stamped_with_provenance(app, in_dir):
    h5py = pytest.importorskip("h5py")
    import json
    out = in_dir / "out"
    _run(app, in_dir, ["h5"], out_dir=out)
    h5_path = next((out / "h5").glob("*.h5"))
    with h5py.File(h5_path, "r") as f:
        history = json.loads(f.attrs["provenance_history"])
    assert history[0]["tool"] == "midas_gui.batch_integrate"


def test_no_out_dir_writes_nothing_and_still_succeeds(app, in_dir):
    """Live-preview runs pass out_dir=None; zarr must be skipped, not crash."""
    results, _ = _run(app, in_dir, ["zarr"], out_dir=None)
    assert results["n"] == 2


def test_default_formats_produce_no_zarr(app, in_dir):
    """Regression guard: a run that didn't ask for zarr gets none."""
    out = in_dir / "out"
    _run(app, in_dir, ["csv"], out_dir=out)
    assert not list(out.rglob("*.zarr*"))


# ── /Omegas: a real rotation angle, not the frame index ─────────────────────

def _omegas_in(out):
    zarr = pytest.importorskip("zarr")
    vals = []
    for path in sorted((out / "zarr").glob("*.zarr.zip")):
        root = zarr.open(zarr.ZipStore(str(path), mode="r"), mode="r")
        vals.extend(np.asarray(root["Omegas"]).ravel().tolist())
    return vals


def test_zarr_omegas_come_from_ome_start_and_ome_step(app, in_dir):
    """The point of the whole omega feature: ``/Omegas`` carries the angles
    the cake CSV describes. Each TIFF is one raw frame, so frame k is at
    ``OME_START + k·OME_STEP``."""
    out = in_dir / "out"
    _run(app, in_dir, ["zarr"], n_frames=3, out_dir=out,
         omega_cfg={"start": 5.0, "step": 0.25, "channel": "",
                    "collapse": False})
    assert _omegas_in(out) == pytest.approx([5.0, 5.25, 5.5])


def test_zarr_omegas_default_to_zero_rather_than_the_frame_index(app, in_dir):
    """The regression this feature exists to prevent. Before it, an
    unconfigured run wrote ``[0, 1, 2]`` into a dataset the backend labels
    ``Units: Degrees`` — frame counts masquerading as angles. A stationary
    sample is at ω = 0, so zeros are the honest answer."""
    out = in_dir / "out"
    _run(app, in_dir, ["zarr"], n_frames=3, out_dir=out)
    got = _omegas_in(out)
    assert got == [0.0, 0.0, 0.0]
    assert got != [0.0, 1.0, 2.0]


def test_zarr_omegas_collapse_to_one_angle_when_the_override_is_set(app, in_dir):
    """"These images were averaged or summed": every frame reports the mean
    angle of the whole run instead of its own position in a ramp it no longer
    has. Mean of raw 0…2 at 0.25°/frame from 5.0 is 5.25."""
    out = in_dir / "out"
    _run(app, in_dir, ["zarr"], n_frames=3, out_dir=out,
         omega_cfg={"start": 5.0, "step": 0.25, "channel": "",
                    "collapse": True})
    assert _omegas_in(out) == pytest.approx([5.25, 5.25, 5.25])


def test_the_omega_source_is_named_in_the_run_log(app, in_dir):
    """Every zarr this app writes changed its ``/Omegas`` with this feature,
    so a run has to say out loud which angles it used — otherwise a default
    run's zeros are indistinguishable from a misconfigured one's."""
    out = in_dir / "out"
    _, logs = _run(app, in_dir, ["zarr"], n_frames=2, out_dir=out,
                   omega_cfg={"start": 5.0, "step": 0.25, "channel": "",
                              "collapse": False})
    line = next(l for l in logs if l.startswith("[batch] omega:"))
    assert "5" in line and "0.25" in line


def _make_hdf5_stack(tmp_path, n_files=2, n_raw=6, size=64, numbered=False):
    """``n_files`` HDF5 sub-frame stacks — the multi-file case the per-file ω
    origin is about. ``numbered`` names them ``scan_000001.h5`` … so
    ``frame_start``/``frame_end`` (which are FILE numbers for this source
    type) have something to parse."""
    h5py = pytest.importorskip("h5py")
    rng = np.random.default_rng(1)
    paths = []
    for f, stem in enumerate("abcdefgh"[:n_files]):
        p = tmp_path / (f"scan_{f + 1:06d}.h5" if numbered else f"{stem}.h5")
        with h5py.File(p, "w") as h:
            h.create_dataset("exchange/data",
                             data=(rng.random((n_raw, size, size)) * 100 + 10
                                   ).astype(np.float32))
        paths.append(str(p))
    return paths


def _run_stack(app, tmp_path, fmts, *, out_dir, omega_cfg, chunk_size=2,
               n_files=2, n_raw=6):
    pytest.importorskip("torch")
    pytest.importorskip("midas_integrate_v2")
    import midas_gui.workers as wk
    from midas_gui.helpers import _build_spec

    paths = _make_hdf5_stack(tmp_path / "in", n_files=n_files, n_raw=n_raw)
    spec = _build_spec(_tiny_calib_result(), r_bin=2.0, eta_bin=45.0)
    worker = wk.BatchWorker(
        spec, {"type": "hdf5_stack_glob", "paths": paths,
               "dataset": "exchange/data", "chunk_size": chunk_size},
        None, out_dir, fmts, "subpixel2", (None, None), None,
        multi_azimuth=False, omega_cfg=omega_cfg)
    results, failures = {}, []
    worker.finished.connect(results.update)
    worker.failed.connect(failures.append)
    worker.run()
    assert not failures, failures[0]
    return results


def test_each_hdf5_file_restarts_the_rotation_at_ome_start(app, in_dir):
    """One HDF5 sub-frame stack is one rotation, so the second file's first
    frame sits at OME_START again rather than continuing the first file's
    ramp (see ``.context/DECISIONS.md``, 2026-09-29). With 6 raw sub-frames
    per file combined 2 at a time, each file gives the same three angles.

    This is the assertion a global ramp would fail: it would produce
    5.875/6.375/6.875 for the second file."""
    out = in_dir / "out"
    res = _run_stack(app, in_dir, ["zarr"], out_dir=out,
                     omega_cfg={"start": 5.0, "step": 0.25, "channel": "",
                                "collapse": False})
    per_file = [5.125, 5.625, 6.125]
    assert res["omegas"] == pytest.approx(per_file * 2)
    assert sorted(_omegas_in(out)) == pytest.approx(sorted(per_file * 2))


def test_a_raw_sub_frame_filter_shifts_the_angles_rather_than_rebasing_them(app, in_dir):
    """A single-file pick is where start/end filter RAW SUB-FRAMES. ω is the
    angle of the sub-frames a frame actually holds, so dropping the first two
    starts the series two steps in — at 5.625, not back at OME_START."""
    pytest.importorskip("torch")
    import midas_gui.workers as wk
    from midas_gui.helpers import _build_spec

    path = _make_hdf5_stack(in_dir / "in", n_files=1)[0]
    spec = _build_spec(_tiny_calib_result(), r_bin=2.0, eta_bin=45.0)
    worker = wk.BatchWorker(
        spec, {"type": "hdf5", "path": path, "dataset": "exchange/data",
               "chunk_size": 2, "frame_start": 2, "frame_end": 5},
        None, in_dir / "out", ["csv"], "subpixel2", (None, None), None,
        multi_azimuth=False,
        omega_cfg={"start": 5.0, "step": 0.25, "channel": "",
                   "collapse": False})
    res, failures = {}, []
    worker.finished.connect(res.update)
    worker.failed.connect(failures.append)
    worker.run()
    assert not failures, failures[0]
    assert res["omegas"] == pytest.approx([5.625, 6.125])


def test_dropping_leading_files_does_not_move_the_surviving_angles(app, in_dir):
    """On a multi-file pick start/end are FILE numbers, and the files are
    dropped before the ω code ever sees them. Under the old global ramp the
    survivors were then renumbered from zero, so filtering out file 1
    silently moved file 2's angles down onto file 1's — the exact thing the
    documented rule said could not happen. Per-file origin makes the
    survivors' angles independent of what was filtered out."""
    pytest.importorskip("torch")
    import midas_gui.workers as wk
    from midas_gui.helpers import _build_spec

    paths = _make_hdf5_stack(in_dir / "in", n_files=2, numbered=True)
    spec = _build_spec(_tiny_calib_result(), r_bin=2.0, eta_bin=45.0)

    def run(**filt):
        worker = wk.BatchWorker(
            spec, {"type": "hdf5_stack_glob", "paths": paths,
                   "dataset": "exchange/data", "chunk_size": 2, **filt},
            None, None, [], "subpixel2", (None, None), None,
            multi_azimuth=False,
            omega_cfg={"start": 5.0, "step": 0.25, "channel": "",
                       "collapse": False})
        res, failures = {}, []
        worker.finished.connect(res.update)
        worker.failed.connect(failures.append)
        worker.run()
        assert not failures, failures[0]
        return res["omegas"]

    both = run()
    second_only = run(frame_start=2, frame_end=2)
    assert both == pytest.approx([5.125, 5.625, 6.125] * 2)
    assert second_only == pytest.approx([5.125, 5.625, 6.125])


def test_the_finished_payload_carries_one_omega_per_frame(app, in_dir):
    """``frame_ids`` and ``omegas`` are read positionally against each other
    downstream (the combined HDF5, the logged attempt, the GSAS-II export),
    so they must stay the same length even when zarr output is off — the
    omega must not be computed inside the zarr branch."""
    out = in_dir / "out"
    results, _ = _run(app, in_dir, ["csv"], n_frames=3, out_dir=out,
                      omega_cfg={"start": 1.0, "step": 2.0, "channel": "",
                                 "collapse": False})
    assert results["omegas"] == pytest.approx([1.0, 3.0, 5.0])
    assert len(results["omegas"]) == len(results["frame_ids"])


def test_the_combined_hdf5_carries_the_omegas_alongside_the_profiles(app, in_dir):
    """Where a downstream peak fit will look for the angle. Written as an
    ``extra_datasets`` entry rather than a ``ProfileMetadata`` field, since
    it is per-frame data, not a per-run scalar."""
    h5py = pytest.importorskip("h5py")
    out = in_dir / "out"
    _run(app, in_dir, ["h5"], n_frames=3, out_dir=out,
         omega_cfg={"start": 1.0, "step": 2.0, "channel": "",
                    "collapse": False})
    h5_path = next((out / "h5").glob("*.h5"))
    with h5py.File(h5_path, "r") as f:
        key = next(k for k in f if k.lower() == "omegas")
        np.testing.assert_allclose(np.asarray(f[key]).ravel(), [1.0, 3.0, 5.0])


def test_the_cake_hdf5_carries_the_omegas_too(app, in_dir):
    """Multi-azimuth mode writes ``cake_hdf5.write_cake_h5`` instead of
    ``midas_integrate_v2.write_h5`` (upstream's cake layout), and a pole
    figure over a rotation series reads that file — so the angle has to be
    in it, not only in the 1-D sibling."""
    h5py = pytest.importorskip("h5py")
    out = in_dir / "out"
    _run(app, in_dir, ["h5"], multi_azimuth=True, n_frames=3, out_dir=out,
         omega_cfg={"start": 1.0, "step": 2.0, "channel": "",
                    "collapse": False})
    h5_path = next((out / "h5").glob("*.h5"))
    with h5py.File(h5_path, "r") as f:
        assert f["cake"].ndim == 3, "not the cake layout"
        np.testing.assert_allclose(np.asarray(f["omegas"]), [1.0, 3.0, 5.0])
        assert f["omegas"].attrs["units"] == "degree"


def test_the_cake_hdf5_survives_physics_corrections(app, in_dir):
    """The corrections path leaves ``ctx["geom"]`` None on purpose, and the
    cake HDF5's BinArea is computed from a geometry — handing that None
    straight to ``count_cake`` is the same ``AttributeError`` the zarr branch
    already had to be fixed for (see
    ``test_zarr_survives_physics_corrections_and_keeps_the_same_bin_area``).
    A geometry is built for the count, so the area is there either way."""
    h5py = pytest.importorskip("h5py")
    from midas_integrate_v2 import PolarizationCorrection, SolidAngleCorrection

    def _area(out, corrections):
        _run(app, in_dir, ["h5"], multi_azimuth=True, n_frames=1, out_dir=out,
             corrections=corrections)
        with h5py.File(next((out / "h5").glob("*.h5")), "r") as f:
            return np.asarray(f["bin_area"])

    on = _area(in_dir / "out_corr",
               (PolarizationCorrection(pol_fraction=0.99,
                                       pol_plane_eta_deg=0.0),
                SolidAngleCorrection()))
    off = _area(in_dir / "out_plain", (None, None))

    assert np.any(on > 0), "bin_area came out empty"
    np.testing.assert_array_equal(on, off)
