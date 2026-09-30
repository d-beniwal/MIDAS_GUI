"""A ``.zarr.zip`` must read the same way whichever path wrote it.

MIDAS_GUI has two: Batch Integrate (``workers.BatchWorker``, one store per
combined output frame) and the GSAS-II export of a single logged attempt
(``gsas_export.export_gsas_zarr``). Both call the same backend writer, so
the arrays and groups have always matched by construction — but the
provenance did not. Batch Integrate stamped a ``build_entry()`` into the
root attrs while the export path wrote only its JSON sidecar, so which path
produced a file changed where, and whether, you could read its history back.
Neither recorded the geometry at all: a reader got ``Distance`` and ``Lam``
out of ``InstrumentParameters/`` and nothing else, the rest of the
calibration surviving only baked into ``REtaMap``'s per-bin columns.

These tests drive both real paths over one geometry and diff the result, so
a future change to either one has to keep them aligned.
"""
import json

import numpy as np
import pytest

_CALIB = dict(
    Lsd=200000.0, BC_y=32.0, BC_z=32.0, tx=0.0, ty=0.0, tz=0.0,
    distortion={}, pxY=200.0, pxZ=200.0,
    NrPixelsY=64, NrPixelsZ=64, wavelength_A=0.1729,
)
R_BIN = 1.0

#: Both paths are driven over the same two-frame rotation so ``/Omegas`` can
#: be diffed as data, not just as a present-or-absent array.
OMEGA_CFG = {"start": 5.0, "step": 0.25, "channel": "", "collapse": False}
OMEGAS = (5.0, 5.25)


@pytest.fixture(scope="module")
def app():
    from PyQt5 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _open(path):
    import zarr
    store = zarr.storage.ZipStore(str(path), mode="r")
    return zarr.open_group(store, mode="r")


def _members(group, skip_prefix="OmegaSumFrame/"):
    """Every group/array path in the store, with the per-frame
    ``OmegaSumFrame/<name>`` leaves collapsed — the two paths legitimately
    write different numbers of frames; what must match is the skeleton."""
    out = set()

    def walk(g, prefix=""):
        for k in g.group_keys():
            p = f"{prefix}{k}"
            out.add(p + "/")
            walk(g[k], p + "/")
        for k in g.array_keys():
            p = f"{prefix}{k}"
            if not p.startswith(skip_prefix):
                out.add(p)
    walk(group)
    return out


# ── The two real writers ─────────────────────────────────────────────────

def _write_via_batch(tmp_path, app):
    pytest.importorskip("torch")
    pytest.importorskip("zarr")
    tifffile = pytest.importorskip("tifffile")
    from types import SimpleNamespace
    import midas_gui.workers as wk
    from midas_gui.helpers import _build_spec

    src = tmp_path / "in"
    src.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(0)
    paths = []
    for i in range(2):
        p = src / f"frame_{i:04d}.tif"
        tifffile.imwrite(str(p), (rng.random((64, 64)) * 100 + 10).astype(np.float32))
        paths.append(str(p))

    spec = _build_spec(SimpleNamespace(**_CALIB), r_bin=R_BIN, eta_bin=360.0)
    out = tmp_path / "batch_out"
    worker = wk.BatchWorker(spec, {"type": "tiff_list", "paths": paths}, None,
                            out, ["zarr"], "subpixel2", (None, None), None,
                            multi_azimuth=False, omega_cfg=OMEGA_CFG)
    failures = []
    worker.failed.connect(failures.append)
    worker.run()
    assert not failures, failures[0]
    written = sorted((out / "zarr").glob("*.zarr.zip"))
    assert written, "Batch Integrate wrote no zarr"
    return written[0]


def _write_via_export(tmp_path, app):
    pytest.importorskip("torch")
    pytest.importorskip("zarr")
    from midas_gui import project
    from midas_gui.gsas_export import export_gsas_zarr
    from midas_gui.helpers import _build_spec

    proj = str(tmp_path / "proj.h5")
    project.create_project(proj)
    spec = _build_spec(project.calibration_namespace(dict(_CALIB)),
                       R_BIN, 360.0)
    n_r = spec.n_r_bins
    r_axis_px = spec.RMin + spec.RBinSize * (np.arange(n_r) + 0.5)
    profiles = np.abs(np.random.rand(2, n_r)) + 1.0
    ref = project.append_integration_attempt(
        proj, "single",
        inputs={"kernel": "subpixel2", "r_bin": R_BIN, "e_bin": 360.0,
                "q_cfg": None},
        finished_payload={"n": 2, "profiles": profiles, "r_axis_px": r_axis_px,
                          "sigmas": np.sqrt(profiles),
                          "frame_ids": ["a", "b"],
                          "omegas": list(OMEGAS), "aborted": False},
        calibration_snapshot=dict(_CALIB),
        extra={"n_eta_bins": 1, "eta_axis_deg": None})
    return export_gsas_zarr(proj, "single", ref, tmp_path / "export.zarr.zip")


@pytest.fixture(scope="module")
def both(tmp_path_factory, app):
    root = tmp_path_factory.mktemp("parity")
    b, e = root / "b", root / "e"
    b.mkdir()
    e.mkdir()
    return _open(_write_via_batch(b, app)), _open(_write_via_export(e, app))


def _prov(group):
    hist = dict(group.attrs).get("provenance_history")
    assert hist, "no provenance_history at the zarr root"
    return hist[-1]


# ── Structural parity ────────────────────────────────────────────────────

def test_both_paths_write_the_same_groups_and_arrays(both):
    batch, export = both
    assert _members(batch) == _members(export)


def test_both_paths_stamp_provenance_history_inside_the_zip(both):
    """The regression this file exists for: the export path used to write
    its history *only* to a sidecar .json, so the zarr itself was anonymous."""
    for g in both:
        assert "provenance_history" in dict(g.attrs)


def test_both_provenance_entries_have_the_same_top_level_shape(both):
    batch, export = both
    # `tool` differs on purpose — it names the writer. Everything else is the
    # shared schema, and a key present in one file but not the other means a
    # reader has to know which path produced it.
    assert set(_prov(batch)) == set(_prov(export))


def test_both_paths_name_themselves_distinctly(both):
    batch, export = both
    assert _prov(batch)["tool"] == "midas_gui.batch_integrate"
    assert _prov(export)["tool"] == "midas_gui.gsas_export"


def test_per_frame_attrs_match_when_neither_source_has_instrument_metadata(both):
    """TIFF input carries no temperature/ion-chamber readings, so the two
    paths' per-frame attr keys must agree exactly. (A real HDF5 source adds
    I/I0/Temperature/Pressure on the Batch path — that is data the export
    path genuinely doesn't have, not a layout difference.)"""
    batch, export = both
    def keys(g):
        osf = g["OmegaSumFrame"]
        return set(dict(osf[sorted(osf.array_keys())[0]].attrs))
    assert keys(batch) == keys(export)


# ── The geometry is actually recorded ────────────────────────────────────

def test_both_paths_record_identical_instrument_params(both):
    batch, export = both
    assert _prov(batch)["instrument_params"] == _prov(export)["instrument_params"]


def test_instrument_params_carries_what_retamap_only_implies(both):
    """Before this, a file's beam centre and tilts existed only baked into
    REtaMap's per-bin columns — recoverable in principle by inverting the
    map, not in practice. InstrumentParameters/ held Lsd and the wavelength
    and nothing else of the fit."""
    ip = _prov(both[0])["instrument_params"]
    for key in ("Lsd", "BC_y", "BC_z", "tx", "ty", "tz",
                "pxY", "pxZ", "NrPixelsY", "NrPixelsZ", "Wavelength"):
        assert key in ip, f"{key} missing from instrument_params"
    assert ip["Lsd"] == pytest.approx(200000.0)
    assert ip["BC_y"] == pytest.approx(32.0)
    assert ip["Wavelength"] == pytest.approx(0.1729)


def test_all_fifteen_distortion_harmonics_are_recorded_even_when_zero(both):
    """Absent keys can't distinguish "no distortion" from "this writer
    didn't record distortion"; zeros can."""
    from midas_gui.constants import DISTORTION_NAMES
    for g in both:
        dist = _prov(g)["instrument_params"]["distortion"]
        assert set(dist) == set(DISTORTION_NAMES)
        assert all(v == 0.0 for v in dist.values())


def test_instrument_params_are_plain_json_numbers_not_tensors(both):
    """Geometry comes off the spec as 0-dim torch tensors; anything that
    reached the attrs un-coerced would have failed the write, or worse,
    round-tripped as a string."""
    for g in both:
        ip = _prov(g)["instrument_params"]
        assert json.loads(json.dumps(ip)) == ip
        assert isinstance(ip["Lsd"], float)
        assert isinstance(ip["NrPixelsY"], int)


def test_the_export_sidecar_is_still_written_alongside(tmp_path, app):
    """The in-zip entry is an addition, not a replacement: the sidecar
    carries attempt-level metadata the Batch path has no equivalent for."""
    out = _write_via_export(tmp_path, app)
    assert (tmp_path / "export.zarr.zip.provenance.json").exists()
    assert out.exists()


# ── The instrument/ tree, which only an HDF5 source has ──────────────────

def test_both_paths_copy_the_same_instrument_tree_from_the_same_hdf5(tmp_path, app):
    """A TIFF source has no instrument metadata, so the fixtures above can't
    see this: when the frames *did* come from a detector HDF5, both writers
    must carry its ``instrument/`` PV snapshot forward, and carry the same
    one. Batch Integrate has the open source in hand; the export path has to
    reconstruct it from the attempt's recorded ``src_cfg``, which is the
    whole reason that gets logged.
    """
    pytest.importorskip("torch")
    pytest.importorskip("h5py")
    pytest.importorskip("zarr")
    import midas_gui.workers as wk
    from midas_gui import project
    from midas_gui.gsas_export import export_gsas_zarr
    from midas_gui.helpers import _build_spec
    from tests.test_h5_metadata_copy import _make_h5

    h5_path = tmp_path / "scan_001.h5"
    temperature = _make_h5(h5_path, n_light=4, n_dark=4, size=64)
    src_cfg = {"type": "hdf5_stack_glob", "paths": [str(h5_path)],
               "dataset": "exchange/data", "chunk_size": None,
               "combine_op": "mean"}

    spec = _build_spec(project.calibration_namespace(dict(_CALIB)), R_BIN, 360.0)
    out = tmp_path / "batch_out"
    worker = wk.BatchWorker(spec, dict(src_cfg), None, out, ["zarr"],
                            "subpixel2", (None, None), None, multi_azimuth=False)
    failures = []
    worker.failed.connect(failures.append)
    worker.run()
    assert not failures, failures[0]
    batch = _open(sorted((out / "zarr").glob("*.zarr.zip"))[0])

    # One frame, so the export's single OmegaSumFrame lines up with the one
    # combined frame Batch Integrate wrote — same alignment, same averages.
    proj = str(tmp_path / "proj.h5")
    project.create_project(proj)
    n_r = spec.n_r_bins
    profiles = np.abs(np.random.rand(1, n_r)) + 1.0
    ref = project.append_integration_attempt(
        proj, "single",
        inputs={"kernel": "subpixel2", "r_bin": R_BIN, "e_bin": 360.0,
                "q_cfg": None, "src_cfg": src_cfg},
        finished_payload={"n": 1, "profiles": profiles,
                          "r_axis_px": spec.RMin + spec.RBinSize * (np.arange(n_r) + 0.5),
                          "sigmas": np.sqrt(profiles), "frame_ids": ["a"],
                          "aborted": False},
        calibration_snapshot=dict(_CALIB),
        extra={"n_eta_bins": 1, "eta_axis_deg": None})
    export = _open(export_gsas_zarr(proj, "single", ref,
                                    tmp_path / "export.zarr.zip"))

    def instrument_members(g):
        return {m for m in _members(g)
                if m.startswith(("instrument/", "active_instrument/"))}

    assert instrument_members(batch), "Batch Integrate copied no instrument tree"
    assert instrument_members(batch) == instrument_members(export)
    # Not just the same skeleton — the same values, averaged over the same
    # light-frame block (4 lights, not the 8 acquisitions in the file).
    for g in (batch, export):
        assert g["instrument/GSAS2_PVS/Temperature"][0] == pytest.approx(
            temperature[:4].mean())
        assert g["instrument/HRM/energy"][0] == pytest.approx(71.676)


# ── /Omegas: the same angles whichever path wrote them ───────────────────

def test_both_paths_write_the_same_angle_for_the_same_frame(both):
    """Batch Integrate computes ω from OME_START/OME_STEP as it goes; the
    export path replays what the logged attempt recorded. They are different
    mechanisms and must land on the same number, or re-exporting a run
    silently moves every peak.

    Compared frame-by-frame rather than array-to-array because the two paths
    legitimately write different numbers of frames per store — one each for
    Batch Integrate, all of them for the export."""
    batch, export = both
    assert np.asarray(batch["Omegas"]).ravel()[0] == \
        pytest.approx(np.asarray(export["Omegas"]).ravel()[0])


def test_both_paths_write_the_angles_the_run_was_actually_at(both):
    """Parity with each other is not enough — they could agree on the wrong
    thing (they used to agree on the frame index)."""
    batch, export = both
    assert np.asarray(batch["Omegas"]).ravel().tolist() == \
        pytest.approx([OMEGAS[0]])
    assert np.asarray(export["Omegas"]).ravel().tolist() == \
        pytest.approx(list(OMEGAS))


def test_the_export_records_where_its_angles_came_from(both):
    """Three provenance-visible outcomes — recorded, recomputed, or
    unavailable — because a stored 0.0 and an unrecorded 0.0 are the same
    number and a reader has to be able to tell them apart."""
    _batch, export = both
    assert _prov(export)["extra"]["omega_source"] == "recorded"


def _export_attempt(tmp_path, app, *, payload_extra=None, inputs_extra=None):
    """One logged attempt → one exported store, with control over what the
    attempt did and didn't record."""
    pytest.importorskip("torch")
    pytest.importorskip("zarr")
    from midas_gui import project
    from midas_gui.gsas_export import export_gsas_zarr
    from midas_gui.helpers import _build_spec

    proj = str(tmp_path / "proj.h5")
    project.create_project(proj)
    spec = _build_spec(project.calibration_namespace(dict(_CALIB)),
                       R_BIN, 360.0)
    r_axis_px = spec.RMin + spec.RBinSize * (np.arange(spec.n_r_bins) + 0.5)
    profiles = np.abs(np.random.rand(2, spec.n_r_bins)) + 1.0
    inputs = {"kernel": "subpixel2", "r_bin": R_BIN, "e_bin": 360.0,
              "q_cfg": None}
    inputs.update(inputs_extra or {})
    payload = {"n": 2, "profiles": profiles, "r_axis_px": r_axis_px,
               "sigmas": np.sqrt(profiles), "frame_ids": ["a", "b"],
               "aborted": False}
    payload.update(payload_extra or {})
    ref = project.append_integration_attempt(
        proj, "single", inputs=inputs, finished_payload=payload,
        calibration_snapshot=dict(_CALIB),
        extra={"n_eta_bins": 1, "eta_axis_deg": None})
    return _open(export_gsas_zarr(proj, "single", ref,
                                  tmp_path / "export.zarr.zip"))


def test_a_legacy_attempt_recomputes_its_angles_from_the_recorded_config(
        tmp_path, app):
    """An attempt logged before per-frame omegas were stored still recorded
    its ``omega_cfg``, so the computed ramp can be rebuilt exactly."""
    g = _export_attempt(tmp_path, app, inputs_extra={"omega_cfg": OMEGA_CFG})
    assert np.asarray(g["Omegas"]).ravel().tolist() == pytest.approx(list(OMEGAS))
    assert _prov(g)["extra"]["omega_source"] == "recomputed from omega_cfg"


def test_an_attempt_with_no_omega_record_exports_zeros_not_indices(
        tmp_path, app):
    """The oldest attempts have neither. Writing ``range(n_frames)`` into a
    dataset the backend labels ``Units: Degrees`` is what this whole feature
    removed, so the fallback must not reintroduce it on the export path."""
    g = _export_attempt(tmp_path, app)
    got = np.asarray(g["Omegas"]).ravel().tolist()
    assert got == [0.0, 0.0]
    assert got != [0.0, 1.0]
    assert _prov(g)["extra"]["omega_source"] == "unavailable"


def test_a_stored_omega_list_of_the_wrong_length_is_not_trusted(tmp_path, app):
    """Angles are matched to frames positionally; a short list would silently
    misfile every frame after the gap, so it has to be discarded rather than
    padded."""
    g = _export_attempt(tmp_path, app, payload_extra={"omegas": [5.0]})
    assert np.asarray(g["Omegas"]).ravel().tolist() == [0.0, 0.0]
    assert _prov(g)["extra"]["omega_source"] == "unavailable"
