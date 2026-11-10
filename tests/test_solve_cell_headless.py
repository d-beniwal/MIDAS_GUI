"""Tests for midas_gui.solve_cell.headless -- the Qt-free Solve Cell runner.

Pure headless: nothing here creates a QApplication (the module under test
must not need one). Synthetic data: a small cubic crystal is rendered into an
HDF5 frame stack using the same geometry code the pipeline inverts
(``geometry_adapter.predict_reflections``), so the full chain
ingest -> filter -> ab-initio -> refine can run in seconds and must recover
the generating cell. A real-data test (Eiger ceria anchor + calibration from
test_data/) and an opt-in real spinel run are skipped when the data is absent.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("midas_defect")
pytest.importorskip("midas_hkls")
h5py = pytest.importorskip("h5py")

from midas_gui import project
from midas_gui.solve_cell import headless as H
from midas_gui.solve_cell import geometry_adapter as GA

REPO = Path(__file__).resolve().parents[1]
CELL_A = 6.0
N_ROWS, N_COLS = 256, 512
STEP = 1.5
N_FRAMES = 246            # 360 deg + pre-roll
FIRST_IDX = 6             # a few pre-roll frames (constant omega), like a real scan
OMEGA0 = 0.0


def _calib_json(path: Path):
    path.write_text(json.dumps({
        "Lsd": 60000.0, "BC_y": 20.0, "BC_z": 20.0, "tx": 0.0, "ty": 0.0, "tz": 0.0,
        "pxY": 75.0, "pxZ": 75.0, "NrPixelsY": N_COLS, "NrPixelsZ": N_ROWS,
        "wavelength_A": 0.4959, "im_trans": [],
        "distortion": {}}))


def _rotation(seed):
    q = np.random.default_rng(seed).normal(size=4)
    q /= np.linalg.norm(q)
    w, x, y, z = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    """HDF5 with a rendered cubic crystal + omega field + calibration json."""
    d = tmp_path_factory.mktemp("sc_headless_synth")
    calib = d / "synth.instr.json"
    _calib_json(calib)
    from midas_gui.helpers import geometry_fields_from_file
    g = geometry_fields_from_file(str(calib))
    geom = {"Lsd": g["Lsd"], "BC_y": g["BC_y"], "BC_z": g["BC_z"], "ty": 0.0, "tz": 0.0,
            "wavelength_A": g["wavelength_A"], "px_um": g["pxY"], "nrpixels_y": N_COLS,
            "nrpixels_z": N_ROWS, "im_trans": []}
    omega_last = OMEGA0 + (N_FRAMES - 1 - FIRST_IDX) * STEP
    snap = GA.PanelSnapshot(
        panel_id=1, geom_cfg=geom, raw_shape=(N_ROWS, N_COLS), n_frames_loaded=N_FRAMES,
        omega_ref_frame_idx=FIRST_IDX, omega_ref_deg=OMEGA0, omega_step_deg=STEP,
        omega_last_frame_idx=N_FRAMES - 1)
    UB = _rotation(3) @ (np.eye(3) / CELL_A)            # g = UB @ hkl, 1/d convention
    preds = GA.predict_reflections(UB, {1: snap}, {1: (OMEGA0, omega_last)}, q_max=7.0)
    vis = [p for p in preds if p["visibility"] == "visible"]
    assert len(vis) > 60, "synthetic crystal produced too few visible reflections"

    frames = np.random.default_rng(0).poisson(1.0, (N_FRAMES, N_ROWS, N_COLS)).astype(np.float32)
    rr, cc = np.mgrid[0:N_ROWS, 0:N_COLS]
    for p in vis:
        f = FIRST_IDX + (p["omega_deg"] - OMEGA0) / STEP
        r0, c0 = p["row"], p["col"]
        if not (3 < r0 < N_ROWS - 4 and 3 < c0 < N_COLS - 4):
            continue
        for fi in range(int(np.floor(f)) - 3, int(np.ceil(f)) + 4):
            if 0 <= fi < N_FRAMES:
                amp = 400.0 * np.exp(-0.5 * ((fi - f) / 0.9) ** 2)
                r_lo, c_lo = int(r0) - 5, int(c0) - 5
                sub = (slice(max(r_lo, 0), r_lo + 11), slice(max(c_lo, 0), c_lo + 11))
                frames[fi][sub] += amp * np.exp(
                    -0.5 * (((rr[sub] - r0) / 1.3) ** 2 + ((cc[sub] - c0) / 1.3) ** 2))
    omega = np.full(N_FRAMES, OMEGA0)
    omega[FIRST_IDX:] = OMEGA0 + np.arange(N_FRAMES - FIRST_IDX) * STEP
    data = d / "synth.h5"
    with h5py.File(data, "w") as f:
        f.create_dataset("entry/data/data", data=frames.astype(np.uint32))
        f.create_dataset("entry/instrument/sample_stage/omega", data=omega)
    return {"dir": d, "data": str(data), "calib": str(calib), "n_vis": len(vis)}


def _spec(synth, out, **over):
    spec = {
        "project_path": str(out), "data_path": synth["data"], "calibration": synth["calib"],
        "omega": {"start": OMEGA0, "end": OMEGA0 + (N_FRAMES - 1 - FIRST_IDX) * STEP,
                  "step": STEP, "first_idx": FIRST_IDX, "field": "entry/instrument/sample_stage/omega"},
        "blobs": {"threshold": 15.0, "min_vol": 6, "min_frames": 2, "sector_candidates": [1]},
        "material_filter": {"enabled": False},
        "index": {"sigma_g": 0.01, "min_reflections": 20},
    }
    spec.update(over)
    return spec


# ── omega mapping ───────────────────────────────────────────────────────────

def test_resolve_omega_autodetects_first_idx(synth):
    om = H.resolve_omega(synth["data"], {"start": 0.0, "end": 89.0, "step": STEP,
                                         "field": "entry/instrument/sample_stage/omega"}, None, N_FRAMES)
    # the constant pre-roll frames sit at omega 0.0 == start, so detection (a plain
    # in-range test, as in the GUI) picks frame 0; an explicit first_idx wins.
    assert om["first_idx"] == 0
    om = H.resolve_omega(synth["data"], {"start": 0.0, "end": 89.0, "step": STEP, "first_idx": FIRST_IDX},
                         None, N_FRAMES)
    assert om["first_idx"] == FIRST_IDX and om["last_idx"] == FIRST_IDX + 59
    assert om["ref_deg"] == 0.0


def test_resolve_omega_estimates_step_and_ref_from_field(synth):
    om = H.resolve_omega(synth["data"], {"start": 0.1, "end": 80.0, "first_idx": FIRST_IDX,
                                         "field": "entry/instrument/sample_stage/omega",
                                         "ref_from_field": True}, None, N_FRAMES)
    assert om["step"] == pytest.approx(STEP)
    assert om["ref_deg"] == pytest.approx(0.0)          # measured value at first_idx


def test_resolve_omega_errors(synth):
    with pytest.raises(H.SpecError):
        H.resolve_omega(synth["data"], {"start": 500.0, "end": 600.0, "step": 0.5,
                                        "field": "entry/instrument/sample_stage/omega"}, None, N_FRAMES)
    with pytest.raises(H.SpecError):
        H.resolve_omega(synth["data"], {"start": 0.0, "end": 9.0, "step": 0.0, "first_idx": 0}, None, N_FRAMES)


# ── spec validation: never raises, never leaves a file ──────────────────────

def test_bad_specs_report_error_without_writing(tmp_path, synth):
    out = tmp_path / "x.h5"
    for spec in ({}, {"project_path": str(out)},
                 {"project_path": str(out), "data_path": "/nope.h5", "calibration": synth["calib"],
                  "omega": {"start": 0, "end": 1, "step": 1}},
                 _spec(synth, out, index={"method": "known_cell"}),
                 _spec(synth, out, index={"method": "bogus"})):
        res = H.run_solve_cell_headless(spec)
        assert res["status"] == "error" and res["error"]
        assert not out.exists()
        assert not [p for p in tmp_path.iterdir()], "temp dir left behind"


def test_refuses_to_overwrite(tmp_path, synth):
    out = tmp_path / "exists.h5"
    out.write_bytes(b"keep me")
    res = H.run_solve_cell_headless(_spec(synth, out))
    assert res["status"] == "error" and "already exists" in res["error"]
    assert out.read_bytes() == b"keep me"


# ── full chain on the synthetic crystal ─────────────────────────────────────

@pytest.fixture(scope="module")
def solved(tmp_path_factory, synth):
    out = tmp_path_factory.mktemp("sc_headless_out") / "solved.h5"
    logs = []
    res = H.run_solve_cell_headless(_spec(synth, out), log=logs.append)
    return {"res": res, "out": out, "logs": logs}


def test_full_chain_recovers_cell(solved, synth):
    res = solved["res"]
    assert res["status"] == "ok", res["error"]
    assert res["project_path"] == str(solved["out"])
    assert res["n_spots"] >= 40 and res["n_candidates"] == res["n_spots"]
    # a primitive-cubic crystal: conventional cell recovers the generating a
    assert res["conventional_cell"][0] == pytest.approx(CELL_A, rel=0.01)
    assert res["holohedry"] in ("cubic", "triclinic", "monoclinic", "orthorhombic", "tetragonal",
                                "rhombohedral", "hexagonal")
    assert sorted(res["cell"][:3]) == pytest.approx([CELL_A] * 3, rel=0.02)
    assert res["rms_drlv"] < 0.05
    assert res["indexed_fraction"] > 0.8
    assert set(res["timings"]) >= {"ingest", "material_filter", "index", "total"}
    assert any("[solve-cell]" in line for line in solved["logs"])      # progress forwarded
    assert res["omega"]["1"]["first_idx"] == FIRST_IDX


def test_project_reads_back_like_the_gui_writes_it(solved):
    path = str(solved["out"])
    assert project.has_solve_cell(path)
    rec = project.read_solve_cell(path)
    assert rec["last_stage"] == "index"
    assert len(rec["ingest"]["spots_df"]) == solved["res"]["n_spots"]
    assert {"panel_id", "spot_id", "qsample_x", "omega_deg", "loaded_frame_idx"} <= set(
        rec["ingest"]["spots_df"].columns)
    imeta = rec["ingest"]["meta"]
    assert imeta["pooled"] is False and imeta["source_panel_id"] == 1
    p1 = imeta["panels"]["1"]
    assert {"panel_id", "state", "geometry_cfg", "calibration", "summary", "preview_meta"} <= set(p1)
    assert p1["geometry_cfg"]["omega_ref_frame_idx"] == FIRST_IDX
    assert p1["geometry_cfg"]["omega_step_deg"] == STEP
    assert p1["calibration"]["file_text"] and p1["calibration"]["geometry"]["Lsd"] == 60000.0
    assert p1["state"]["fields"]["ome_first_idx"] == FIRST_IDX
    assert p1["state"]["loader"]["path_hash"]["sha256"]        # GUI-style provenance hash
    assert set(rec["ingest"]["panels"]["panel_01"]) >= {"mask", "background_subtracted"}
    for key in ("sigma_g", "index_method", "material_filter", "blob_thresh", "ks3_a", "ks5_sg"):
        assert key in imeta["fields"]
    assert rec["diamond"]["meta"]["status_text"].startswith("Filter off")
    assert len(rec["diamond"]["flagged_df"]) == solved["res"]["n_spots"]
    idx = rec["index"]
    dom = idx["meta"]["domains"]
    assert len(dom) == 1 and dom[0]["label"] == "Domain 1" and dom[0]["method"] == "free (ab-initio)"
    r = dom[0]["result"]
    for key in ("cell", "cell_sigma", "rms_drlv", "holohedry_system", "conventional_cell",
                "n_reflections", "UB", "reflection_fit"):
        assert key in r
    assert r["cell"] == pytest.approx(solved["res"]["cell"])
    assert idx["meta"]["ab_initio"]["success"] is True
    assert idx["meta"]["round1_domain_index"] == 0
    assert set(idx["ab_initio"]) == {"hkl", "indexed_mask", "UB", "UBI"}
    d0 = idx["domains"][0]
    assert len(d0["claimed_index"]) == len(d0["xyz"]) == len(d0["panel_ids"]) == r["n_reflections"]
    ov = project.solve_cell_overview(path := str(solved["out"]))
    assert ov["n_domains"] == 1 and ov["n_spots"] == solved["res"]["n_spots"]
    # a project the GUI created: marker attrs + schema
    with h5py.File(path, "r") as f:
        assert f.attrs[project.PROJECT_MARKER]


def test_domain_geometry_reconstructs_for_viz(solved):
    """The GUI's viz code rebuilds panel snapshots from the stored ingest meta."""
    rec = project.read_solve_cell(str(solved["out"]))
    snaps = GA.snapshots_from_ingest_meta(rec["ingest"]["meta"])
    assert set(snaps) == {1} and snaps[1].raw_shape == (N_ROWS, N_COLS)
    assert snaps[1].omega_ref_frame_idx == FIRST_IDX


def test_no_stray_files(solved):
    assert sorted(p.name for p in solved["out"].parent.iterdir()) == ["solved.h5"]


def test_creates_no_qapplication(tmp_path, synth):
    """In a clean interpreter (no pytest/conftest Qt setup) the runner never
    instantiates a QApplication."""
    sp = tmp_path / "s.json"
    sp.write_text(json.dumps(_spec(synth, tmp_path / "q.h5")))
    code = ("import sys, json; from midas_gui.solve_cell import headless as H; "
            "r = H.run_solve_cell_headless(json.load(open(sys.argv[1]))); "
            "from PyQt5 import QtWidgets; "
            "print(r['status'], QtWidgets.QApplication.instance() is None)")
    env = dict(os.environ); env.pop("QT_QPA_PLATFORM", None)
    proc = subprocess.run([sys.executable, "-c", code, str(sp)], cwd=str(REPO), env=env,
                          capture_output=True, text=True, timeout=600)
    assert proc.stdout.split()[-2:] == ["ok", "True"], proc.stderr[-1500:]


# ── frame sub-range, material filter on, known-cell mode ────────────────────

def test_frame_range_uses_file_indices(tmp_path, synth):
    out = tmp_path / "range.h5"
    # drop the first 3 frames: first_idx/ref stay in FILE indices
    res = H.run_solve_cell_headless(_spec(synth, out, frame_range=[3, N_FRAMES],
                                          omega={"start": OMEGA0, "step": STEP, "first_idx": FIRST_IDX,
                                                 "end": OMEGA0 + (N_FRAMES - 1 - FIRST_IDX) * STEP}))
    assert res["status"] == "ok", res["error"]
    rec = project.read_solve_cell(str(out))
    g = rec["ingest"]["meta"]["panels"]["1"]["geometry_cfg"]
    assert g["omega_ref_frame_idx"] == FIRST_IDX
    assert rec["ingest"]["meta"]["headless"]["frame_ranges"]["1"] == [3, N_FRAMES]
    spots = rec["ingest"]["spots_df"]
    assert spots["loaded_frame_idx"].min() >= FIRST_IDX - 1     # file indices, not sub-stack indices
    # same omega mapping => same cell as the full-range run
    assert res["conventional_cell"][0] == pytest.approx(CELL_A, rel=0.01)


def test_frame_range_outside_first_idx_is_an_error(tmp_path, synth):
    res = H.run_solve_cell_headless(_spec(synth, tmp_path / "r.h5", frame_range=[50, 100]))
    assert res["status"] == "error" and "outside the loaded frames" in res["error"]


def test_material_filter_on_records_summary(tmp_path, synth):
    out = tmp_path / "filt.h5"
    res = H.run_solve_cell_headless(_spec(synth, out, material_filter={
        "enabled": True, "materials": ["Diamond"], "n_mc": 2000}))
    assert res["status"] == "ok", res["error"]
    rec = project.read_solve_cell(str(out))
    s = rec["diamond"]["meta"]["summary"]
    assert s["enabled"] is True and s["n_spots"] == res["n_spots"]
    assert rec["ingest"]["meta"]["fields"]["material_filter"]["materials"][0]["name"] == "Diamond"


def test_known_cell_mode(tmp_path, synth):
    out = tmp_path / "known.h5"
    res = H.run_solve_cell_headless(_spec(synth, out, index={
        "method": "known_cell", "sigma_g": 0.01,
        "known_cell": {"cell": [CELL_A] * 3 + [90.0] * 3, "n_search": 40000, "min_accept": 20}}))
    assert res["status"] == "ok", res["error"]
    rec = project.read_solve_cell(str(out))
    dom = rec["index"]["meta"]["domains"]
    assert dom[0]["method"] == "known structure (manual)"
    assert rec["index"]["meta"]["ab_initio"] is None and not rec["index"]["ab_initio"]
    assert rec["ingest"]["meta"]["fields"]["index_method"] == "Known structure"
    assert rec["ingest"]["meta"]["fields"]["ks3_a"] == CELL_A
    assert sorted(res["cell"][:3]) == pytest.approx([CELL_A] * 3, rel=0.02)


def test_no_solution_still_writes_ingest_and_diamond(tmp_path, synth):
    out = tmp_path / "nosol.h5"
    res = H.run_solve_cell_headless(_spec(synth, out, index={
        "sigma_g": 0.01, "min_reflections": 5000}))      # more than any pool can supply
    assert res["status"] == "no_solution" and res["cell"] is None and res["error"]
    rec = project.read_solve_cell(str(out))
    assert rec["ingest"]["spots_df"] is not None and rec["diamond"] is not None
    assert not rec["index"] or not rec["index"]["meta"]["domains"]


def test_blank_frames_report_no_spots(tmp_path, synth):
    d = tmp_path / "blank"
    d.mkdir()
    data = d / "blank.h5"
    with h5py.File(data, "w") as f:
        f.create_dataset("entry/data/data", data=np.zeros((20, N_ROWS, N_COLS), np.uint32))
    out = d / "blank_out.h5"
    res = H.run_solve_cell_headless(_spec(synth, out, data_path=str(data), omega={
        "start": 0.0, "end": 9.5, "step": 0.5, "first_idx": 0}))
    assert res["status"] in ("no_solution", "error")
    assert res["cell"] is None


# ── CLI + concurrency ───────────────────────────────────────────────────────

def test_cli_prints_json_summary(tmp_path, synth):
    spec = _spec(synth, tmp_path / "cli.h5")
    sp = tmp_path / "spec.json"
    sp.write_text(json.dumps(spec))
    env = dict(os.environ)
    env.pop("QT_QPA_PLATFORM", None)         # prove no display/Qt platform is needed
    proc = subprocess.run(
        [sys.executable, "-m", "midas_gui.solve_cell.headless", str(sp), "-q",
         "--summary", str(tmp_path / "summary.json")],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)             # stdout is only the JSON
    assert out["status"] == "ok" and out["conventional_cell"][0] == pytest.approx(CELL_A, rel=0.01)
    assert json.loads((tmp_path / "summary.json").read_text())["project_path"] == out["project_path"]
    assert project.has_solve_cell(out["project_path"])


def test_two_concurrent_workers_do_not_collide(tmp_path, synth):
    procs = []
    for i in range(2):
        sp = tmp_path / f"spec{i}.json"
        sp.write_text(json.dumps(_spec(synth, tmp_path / f"p{i}.h5")))
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "midas_gui.solve_cell.headless", str(sp), "-q"],
            cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
    outs = [p.communicate(timeout=600) for p in procs]
    for p, (so, se) in zip(procs, outs):
        assert p.returncode == 0, se[-1500:]
    cells = []
    for i in range(2):
        rec = project.read_solve_cell(str(tmp_path / f"p{i}.h5"))
        cells.append(rec["index"]["meta"]["domains"][0]["result"]["cell"])
    assert cells[0] == pytest.approx(cells[1])          # deterministic and independent
    assert sorted(p.name for p in tmp_path.iterdir() if p.suffix == ".h5") == ["p0.h5", "p1.h5"]


# ── real data (skipped when absent) ─────────────────────────────────────────

ANCHOR = REPO / "test_data/s3idc/ANCHOR1_ceria_25kev_exp0p5_-55_10_0_000061.h5"
BC = REPO / "test_data/s3idc_bc/ceria_25kev_exp0p5_-185_10_0_000120.instr.json"


@pytest.mark.skipif(not (ANCHOR.exists() and BC.exists()), reason="s3idc test data not present")
def test_real_eiger_file_and_calibration_load(tmp_path):
    """Real Eiger HDF5 + real calibration through the headless loaders and a
    short ingest; a powder ring pattern has no single cell, so only the plumbing
    (frames, calibration, project record) is asserted."""
    with h5py.File(ANCHOR, "r") as f:
        n = f["entry/data/data"].shape[0]
    out = tmp_path / "ceria.h5"
    res = H.run_solve_cell_headless({
        "project_path": str(out), "data_path": str(ANCHOR), "calibration": str(BC),
        "frame_range": [0, min(n, 8)],
        "omega": {"start": 0.0, "end": 0.7, "step": 0.1, "first_idx": 0},
        "blobs": {"sector_candidates": [1]}, "material_filter": {"enabled": False}})
    assert res["status"] in ("ok", "no_solution"), res["error"]
    assert res["n_panels"] == 1 and out.exists()
    rec = project.read_solve_cell(str(out))
    assert rec["ingest"]["meta"]["panels"]["1"]["geometry_cfg"]["nrpixels_y"] in (1028, 1062, 4150)


SPINEL = Path("/Users/dbeniwal/ANL-research/S3ID/data/2026-3/Beamline/Oct8/spinel_orient1/"
              "spinel_orient1_25kev_exp0p2_det_-65_-15_0_000001.h5")
SPINEL_CALIB = Path("/Users/dbeniwal/ANL-research/S3ID/data/2026-3/Beamline/analysis/calib/"
                    "xrds4_25kev_exp0p05_det_-65_-15_0_000001.instr.json")


@pytest.mark.skipif(not (os.environ.get("MIDAS_GUI_REAL_SPINEL") and SPINEL.exists() and SPINEL_CALIB.exists()),
                    reason="set MIDAS_GUI_REAL_SPINEL=1 (minutes-long, needs the beamline file)")
def test_real_spinel_panel(tmp_path):
    out = tmp_path / "spinel.h5"
    res = H.run_solve_cell_headless({
        "project_path": str(out), "data_path": str(SPINEL), "calibration": str(SPINEL_CALIB),
        "omega": {"start": -30.0, "end": 29.0, "field": "entry/instrument/sample_stage/omega"},
        "blobs": {"min_vol": 2, "min_frames": 1, "sector_candidates": [1]}})
    assert res["status"] == "ok", res["error"]
    assert res["conventional_cell"][0] == pytest.approx(8.08, abs=0.15)
