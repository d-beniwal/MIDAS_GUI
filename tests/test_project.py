"""Pure-logic tests for midas_gui.project (no Qt needed)."""
import json
from types import SimpleNamespace

import h5py
import numpy as np
import pytest

from midas_gui import project


class _FakeTensor:
    """Duck-types a torch tensor just enough to be dropped by the
    '.numpy' heuristic used elsewhere in the codebase (e.g.
    hydra_calib_widgets.py's _save_json)."""

    def numpy(self):
        return np.zeros((2, 2))


def test_create_and_open_project_roundtrip(tmp_path):
    path = tmp_path / "proj.h5"
    project.create_project(str(path), name="my-experiment")
    opened = project.open_project(str(path))
    assert opened == str(path)
    with h5py.File(path, "r") as f:
        assert f.attrs[project.PROJECT_MARKER] is True or f.attrs[project.PROJECT_MARKER] == True  # noqa: E712
        assert f.attrs["schema_version"] == project.SCHEMA_VERSION
        assert f.attrs["project_name"] == "my-experiment"
        assert "created_utc" in f.attrs


def test_create_project_refuses_to_overwrite(tmp_path):
    path = tmp_path / "proj.h5"
    project.create_project(str(path))
    with pytest.raises(FileExistsError):
        project.create_project(str(path))


def test_open_project_rejects_non_project_h5(tmp_path):
    path = tmp_path / "plain.h5"
    with h5py.File(path, "w") as f:
        f.create_dataset("data", data=[1, 2, 3])
    with pytest.raises(ValueError):
        project.open_project(str(path))


def test_read_workspace_empty_for_fresh_project(tmp_path):
    path = str(tmp_path / "proj.h5")
    project.create_project(path)
    state, sidecars = project.read_workspace(path)
    assert state == {}
    assert sidecars == {}


def test_write_and_read_workspace_roundtrip(tmp_path):
    path = str(tmp_path / "proj.h5")
    project.create_project(path)
    state = {"__midas_gui_state__": True, "version": 1, "tabs": {"Mask Builder": {"fields": {}}},
              "active_profile": "1-ID-E", "active_tab": "Mask Builder"}
    sidecars = {"sidecar_mask.tif": np.ones((4, 4), dtype=np.uint8),
                "sidecar_calibration.json": json.dumps({"Lsd": 200000.0})}

    project.write_workspace(path, state, sidecars)
    got_state, got_sidecars = project.read_workspace(path)
    assert got_state == state
    assert np.array_equal(got_sidecars["sidecar_mask.tif"], sidecars["sidecar_mask.tif"])
    assert json.loads(got_sidecars["sidecar_calibration.json"]) == {"Lsd": 200000.0}

    # A second save overwrites the single /workspace slot rather than appending.
    state2 = dict(state, active_tab="Calibrate")
    project.write_workspace(path, state2, sidecars=None)
    got_state2, got_sidecars2 = project.read_workspace(path)
    assert got_state2["active_tab"] == "Calibrate"
    assert got_sidecars2 == {}

    # Calibrate/integrate attempt history is untouched by a workspace save.
    project.append_calibration_attempt(
        path, "single", cfg={"mode": "one_shot"}, result=_fake_result(), loader_state={})
    with h5py.File(path, "r") as f:
        assert "single/calib/attempt_0001" in f
    project.write_workspace(path, state2, sidecars=None)
    with h5py.File(path, "r") as f:
        assert "single/calib/attempt_0001" in f


def test_sha256_file_full_and_partial(tmp_path, monkeypatch):
    small = tmp_path / "small.bin"
    small.write_bytes(b"hello world")
    info = project.sha256_file(str(small))
    assert info["method"] == "sha256_full"
    assert info["size_bytes"] == 11

    monkeypatch.setattr(project, "_HASH_FULL_MAX_BYTES", 10)
    monkeypatch.setattr(project, "_HASH_PARTIAL_CHUNK", 4)
    info2 = project.sha256_file(str(small))
    assert info2["method"] == "sha256_partial"
    assert info2["size_bytes"] == 11
    assert "sha256_head_tail" in info2


def test_environment_snapshot_shape():
    env = project.environment_snapshot()
    assert isinstance(env, dict)
    assert "midas_gui_version" in env
    assert "python_version" in env


def _fake_result(**overrides):
    fields = dict(
        Lsd=200000.0, BC_y=1024.0, BC_z=1024.0, tx=0.0, ty=0.0, tz=0.0,
        distortion={"iso_R2": 0.1}, pxY=200.0, pxZ=200.0,
        NrPixelsY=2048, NrPixelsZ=2048, wavelength_A=0.1729,
        post_residual_strain_uE=12.3, seed_seconds=0.1, refine_seconds=1.2,
    )
    fields.update(overrides)
    ns = SimpleNamespace(**fields)
    ns._calibrant_name = "CeO2"
    ns.residual_corr_map = _FakeTensor()
    return ns


def test_append_calibration_attempt_numbering_and_filtering(tmp_path):
    path = str(tmp_path / "proj.h5")
    project.create_project(path)
    result = _fake_result()
    cfg = {"mode": "one_shot", "calibrant": "CeO2", "refine": {"Lsd": True},
           "mask": np.zeros((4, 4), dtype=np.uint8)}
    loader_state = {"path": None, "dataset": "exchange/data"}

    ref1 = project.append_calibration_attempt(
        path, "single", cfg=cfg, result=result, loader_state=loader_state)
    ref2 = project.append_calibration_attempt(
        path, "single", cfg=cfg, result=result, loader_state=loader_state,
        mask_is_file_backed=True)

    assert ref1 == "/single/calib/attempt_0001"
    assert ref2 == "/single/calib/attempt_0002"

    with h5py.File(path, "r") as f:
        grp = f["single/calib"]
        assert grp.attrs["latest"] == "attempt_0002"
        att = grp["attempt_0001"]
        assert att.attrs["Lsd"] == pytest.approx(200000.0)
        meta = json.loads(att["metadata"][()])
        assert meta["result"]["_calibrant_name"] == "CeO2"
        assert "residual_corr_map" not in meta["result"]
        assert "mask" not in meta["cfg"]
        assert "dark" not in att
        assert "bright" not in att
        # A live (non-file-backed) mask is still embedded as raw array data —
        # the one approved exception, since it has no path to hash.
        assert "mask" in att
        att2 = grp["attempt_0002"]
        # File-backed mask (mask_is_file_backed=True) -> path+hash only, never embedded.
        assert "mask" not in att2


def test_append_integration_attempt_embeds_profiles_and_links_calibration(tmp_path):
    path = str(tmp_path / "proj.h5")
    project.create_project(path)
    calib_ref = project.append_calibration_attempt(
        path, "ge1", cfg={"mode": "one_shot"}, result=_fake_result(),
        loader_state={})

    inputs = {"src_cfg": {"type": "hdf5", "path": None}, "kernel": "subpixel4"}
    finished_payload = {
        "n": 5, "profiles": np.random.rand(5, 100).astype(np.float32),
        "r_axis_px": np.arange(100, dtype=np.float32),
        "sigmas": np.ones((5, 100), dtype=np.float32),
        "frame_ids": [f"frame_{i}" for i in range(5)],
        "out_paths": ["/tmp/out/frame_0.csv"],
        "aborted": False,
    }
    ref = project.append_integration_attempt(
        path, "ge1", inputs=inputs, finished_payload=finished_payload,
        calibration_snapshot={"Lsd": 200000.0}, calib_attempt_ref=calib_ref)

    assert ref == "/ge1/integrate/attempt_0001"
    with h5py.File(path, "r") as f:
        att = f["ge1/integrate/attempt_0001"]
        assert att.attrs["n_frames"] == 5
        assert att.attrs["kernel"] == "subpixel4"
        assert att.attrs["calib_attempt_ref"] == calib_ref
        assert att["results/profiles"].shape == (5, 100)
        assert att["results/frame_ids"].shape == (5,)
        meta = json.loads(att["metadata"][()])
        assert meta["calibration_snapshot"] == {"Lsd": 200000.0}
        assert meta["out_paths"] == ["/tmp/out/frame_0.csv"]


@pytest.fixture(scope="module")
def app():
    from PyQt5 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_calibration_tab_logs_to_project(app, tmp_path):
    """CalibrationTab._log_to_project (called from _on_done) appends a
    provenance record using the cfg/loader-state it already has in scope,
    and no-ops cleanly when no project is open."""
    from midas_gui.tab_calibrate import CalibrationTab

    tab = CalibrationTab()
    result = _fake_result()

    # No project open — must not raise or touch anything.
    tab._log_to_project(result)
    assert not hasattr(result, "_project_attempt_ref")

    proj_path = str(tmp_path / "proj.h5")
    project.create_project(proj_path)
    ctx = project.ProjectContext()
    ctx.path = proj_path
    tab.set_project_context(ctx)
    tab._last_cfg = {"mode": "one_shot", "wavelength": 0.1729, "calibrant": "CeO2",
                      "refine": {}, "mask": None}

    tab._log_to_project(result)
    assert result._project_attempt_ref == "/single/calib/attempt_0001"
    with h5py.File(proj_path, "r") as f:
        att = f["single/calib/attempt_0001"]
        assert att.attrs["pipeline"] == "one_shot"
        meta = json.loads(att["metadata"][()])
        assert meta["result"]["_calibrant_name"] == "CeO2"


def test_batch_tab_logs_to_project_with_calibration_snapshot(app, tmp_path):
    """BatchTab._log_to_project links an integration attempt back to the
    Tab-2 calibration result's project attempt (when it has one)."""
    from midas_gui.tab_batch import BatchTab

    tab = BatchTab()
    proj_path = str(tmp_path / "proj.h5")
    project.create_project(proj_path)
    ctx = project.ProjectContext()
    ctx.path = proj_path
    tab.set_project_context(ctx)

    calib_result = _fake_result()
    calib_result._project_attempt_ref = "/single/calib/attempt_0003"
    tab._calib_result = calib_result
    tab._use_tab2_btn.setChecked(True)

    tab._last_run_inputs = {"src_cfg": {"type": "hdf5", "path": None}, "kernel": "subpixel4"}
    tab._last_run_fields = {"mask": None, "mask_is_file_backed": False}
    data = {"n": 3, "out_paths": ["/tmp/out/f0.csv"], "aborted": False,
            "profiles": np.random.rand(3, 20).astype(np.float32)}

    tab._log_to_project(data)
    with h5py.File(proj_path, "r") as f:
        att = f["single/integrate/attempt_0001"]
        assert att.attrs["n_frames"] == 3
        assert att.attrs["calib_attempt_ref"] == "/single/calib/attempt_0003"
        assert att["results/profiles"].shape == (3, 20)


def test_read_attempt_results_roundtrip(tmp_path):
    path = str(tmp_path / "proj.h5")
    project.create_project(path)
    finished_payload = {
        "n": 3, "aborted": False,
        "profiles": np.random.rand(3, 10).astype(np.float32),
        "r_axis_px": np.arange(10, dtype=np.float32),
        "sigmas": np.ones((3, 10), dtype=np.float32),
        "frame_ids": ["f0", "f1", "f2"],
    }
    ref = project.append_integration_attempt(
        path, "single", inputs={}, finished_payload=finished_payload)

    arrays = project.read_attempt_results(path, ref)
    assert arrays["profiles"].shape == (3, 10)
    assert list(arrays["r_axis_px"]) == pytest.approx(list(range(10)))
    assert arrays["frame_ids"] == ["f0", "f1", "f2"]

    # A calibration attempt has no "results" group — {} rather than raising.
    calib_ref = project.append_calibration_attempt(
        path, "single", cfg={}, result=_fake_result(), loader_state={})
    assert project.read_attempt_results(path, calib_ref) == {}


def test_discover_list_and_read_attempt(tmp_path):
    path = str(tmp_path / "proj.h5")
    project.create_project(path)
    project.append_calibration_attempt(
        path, "ge1", cfg={"mode": "one_shot", "calibrant": "CeO2"},
        result=_fake_result(), loader_state={"path": "/x/ge1.h5"})
    project.append_calibration_attempt(
        path, "ge1", cfg={"mode": "one_shot", "calibrant": "CeO2"},
        result=_fake_result(BC_y=999.0), loader_state={"path": "/x/ge1.h5"})

    assert project.discover_panels(path) == ["ge1"]

    attempts = project.list_attempts(path, "ge1", "calib")
    assert [a["name"] for a in attempts] == ["attempt_0002", "attempt_0001"]  # newest first
    assert project.list_attempts(path, "ge1", "integrate") == []
    assert project.list_attempts(path, "single", "calib") == []

    meta = project.read_attempt(path, attempts[0]["ref"])
    assert meta["result"]["BC_y"] == pytest.approx(999.0)


def test_calib_attempt_gui_fields_and_loader_state():
    meta = {
        "cfg": {"wavelength": 0.1729, "calibrant": "CeO2", "pxY": 200.0, "pxZ": 150.0,
                "refine": {"Lsd": True, "BC": True, "ty": False, "tz": False, "tx": False,
                           "Wavelength": False, "Distortion": False},
                "n_iter": 4, "lm_max_iter": 200, "device": "cpu",
                "build_residual_corr": False, "im_trans": [1, 3]},
        "result": {"BC_y": 1024.0, "BC_z": 1025.0, "Lsd": 200000.0, "tx": 0.1, "ty": 0.2, "tz": 0.3},
        "loader_state": {"path": "/data/ge1.h5", "dataset": "exchange/data", "frame_index": 3},
    }
    fields = project.calib_attempt_gui_fields(meta)
    assert fields["wl"] == 0.1729
    assert fields["cal"] == "CeO2"
    assert fields["seed_bcy"] == 1024.0 and fields["seed_bcz"] == 1025.0
    assert fields["seed_lsd"] == pytest.approx(200.0)   # µm -> mm
    assert fields["manual_seed_check"] is True
    assert fields["flip_y"] is True and fields["transp"] is True and fields["flip_z"] is False
    assert fields["ref_lsd"] is True and fields["ref_bc"] is True and fields["ref_ty"] is False
    assert fields["pxZ_check"] is True and fields["pxZ_spin"] == 150.0

    loader = project.calib_attempt_loader_state(meta)
    assert loader == {"path": "/data/ge1.h5", "dataset": "exchange/data", "frame_index": 3}


def test_integrate_attempt_gui_fields_and_loader_state():
    meta = {
        "inputs": {
            "src_cfg": {"type": "hdf5", "path": "/data/ge1.h5", "dataset": "exchange/data"},
            "kernel": "subpixel4", "fmt": "xye", "frame_range": [0, 20, 1],
            "monitor_file": "/data/monitor.csv",
            "q_cfg": {"QMin": 0.5, "QMax": 8.0, "QBinSize": 0.01},
        },
    }
    fields = project.integrate_attempt_gui_fields(meta)
    assert fields["kernel"] == "Subpixel K=4"
    assert fields["fmt"] == "XYE  (2θ, I, σ)"
    assert fields["mon_ed"] == "/data/monitor.csv"
    assert fields["q_check"] is True
    assert fields["q_min"] == 0.5 and fields["q_max"] == 8.0 and fields["q_bin"] == 0.01

    loader = project.integrate_attempt_loader_state(meta)
    assert loader == {"path": "/data/ge1.h5", "dataset": "exchange/data",
                       "fr_start": 0, "fr_end": 20, "fr_stride": 1}

    # Unbounded frame range (end=None) maps to fr_end=0 ("all"), not a bare None
    # that would crash DataLoaderPanel.set_state's int(state["fr_end"]).
    meta["inputs"]["frame_range"] = [0, None, 1]
    assert project.integrate_attempt_loader_state(meta)["fr_end"] == 0


def test_calibration_namespace_has_expected_attributes():
    ns = project.calibration_namespace(
        {"Lsd": 200000.0, "wavelength_A": 0.1729, "BC_y": 1024.0, "BC_z": 1024.0,
         "NrPixelsY": 2048, "NrPixelsZ": 2048})
    assert ns.Lsd == 200000.0
    assert ns.wavelength_A == 0.1729
    assert ns.residual_corr_bin_path is None


def test_apply_project_calibration_and_integration_hydra(app, tmp_path):
    """End-to-end: a project with only Hydra ge1/ge2 attempts populates the
    Calibrate tab's Hydra page (mode switch + shared recipe + per-panel seed)
    and the Batch Integrate tab's Hydra page (mode switch + a live,
    immediately-usable calibration per panel) — this is the "Open Project…
    should let me pick up where I left off" behavior."""
    from midas_gui.tab_calibrate import CalibrationTab
    from midas_gui.tab_batch import BatchTab

    proj_path = str(tmp_path / "proj.h5")
    project.create_project(proj_path)
    data_paths = {}
    n_frames = {"ge1": 4, "ge2": 6}   # distinct per panel — proves independent per-panel plots
    for panel, bc in (("ge1", 1024.0), ("ge2", 1030.0)):
        data_path = tmp_path / f"{panel}.h5"
        data_path.write_bytes(b"")   # only needs to exist for the anchor-path exists() gate
        data_paths[panel] = str(data_path)
        calib_result = _fake_result(BC_y=bc)
        calib_ref = project.append_calibration_attempt(
            proj_path, panel,
            cfg={"wavelength": 0.1729, "calibrant": "CeO2", "pxY": 200.0,
                 "refine": {"Lsd": True, "BC": True}, "n_iter": 4,
                 "lm_max_iter": 200, "device": "cpu"},
            result=calib_result, loader_state={"path": data_paths[panel],
                                                "dataset": "exchange/data"})
        n = n_frames[panel]
        project.append_integration_attempt(
            proj_path, panel,
            inputs={"src_cfg": {"type": "hdf5", "path": data_paths[panel],
                                 "dataset": "exchange/data"},
                    "kernel": "subpixel2", "fmt": "csv", "frame_range": [0, None, 1]},
            finished_payload={
                "n": n, "aborted": False,
                "profiles": np.random.rand(n, 12).astype(np.float32),
                "r_axis_px": np.arange(12, dtype=np.float32),
                "frame_ids": [f"{panel}_f{i}" for i in range(n)],
            },
            calibration_snapshot={"Lsd": calib_result.Lsd, "wavelength_A": 0.1729,
                                   "BC_y": bc, "BC_z": 1024.0,
                                   "NrPixelsY": 2048, "NrPixelsZ": 2048},
            calib_attempt_ref=calib_ref)

    calib_attempts = {p: project.read_attempt(proj_path, project.list_attempts(proj_path, p, "calib")[0]["ref"])
                       for p in ("ge1", "ge2")}
    integrate_attempts = {}
    for p in ("ge1", "ge2"):
        ref = project.list_attempts(proj_path, p, "integrate")[0]["ref"]
        meta = project.read_attempt(proj_path, ref)
        meta["_results_arrays"] = project.read_attempt_results(proj_path, ref)
        integrate_attempts[p] = meta

    cal_tab = CalibrationTab()
    cal_tab.apply_project_calibration(calib_attempts)
    assert cal_tab._mode_ribbon.mode() == "hydra"
    assert cal_tab._hydra_page._cards[1]._seed_bcy.value() == pytest.approx(1024.0)
    assert cal_tab._hydra_page._cards[2]._seed_bcy.value() == pytest.approx(1030.0)
    assert cal_tab._hydra_page._cards[1]._manual_seed_check.isChecked()
    assert cal_tab._hydra_page._wl.value() == pytest.approx(0.1729)
    # Rings are redrawn from the stored result without re-running Fit — panel
    # 1 is the toolbar's default active panel (viewer already bound), so its
    # ring items are drawn immediately; panel 2's result is stored too (rings
    # appear as soon as the user switches to it — see bind_viewer).
    assert cal_tab._hydra_page._cards[1].result is not None
    assert cal_tab._hydra_page._cards[2].result is not None
    assert len(cal_tab._hydra_page._cards[1]._ring_items) > 0

    batch_tab = BatchTab()
    batch_tab.apply_project_integration(integrate_attempts)
    assert batch_tab._mode_ribbon.mode() == "hydra"
    hp = batch_tab._hydra_page
    assert hp._loader.current_path() == data_paths["ge1"]
    assert hp._kernel.currentText() == "Subpixel K=2 (default)"
    assert hp._cards[1].result.BC_y == pytest.approx(1024.0)
    assert hp._cards[2].result.BC_y == pytest.approx(1030.0)
    assert hp._cards[1]._use_calib_btn.isChecked()
    # Each panel's Waterfall/Stacked-profiles views are replayed from its own
    # attempt's stored arrays — distinct frame counts per panel prove the
    # toolbar's GE1/GE2 selection shows genuinely panel-specific results,
    # not one shared plot.
    assert hp._viewer_pairs[1].waterfall._nrows == n_frames["ge1"]
    assert hp._viewer_pairs[2].waterfall._nrows == n_frames["ge2"]
    assert len(hp._viewer_pairs[1].stack_view._profiles) == n_frames["ge1"]
    assert len(hp._viewer_pairs[2].stack_view._profiles) == n_frames["ge2"]


def test_apply_project_calibration_single_detector(app, tmp_path):
    from midas_gui.tab_calibrate import CalibrationTab

    proj_path = str(tmp_path / "proj.h5")
    project.create_project(proj_path)
    project.append_calibration_attempt(
        proj_path, "single",
        cfg={"wavelength": 0.15359, "calibrant": "LaB6", "pxY": 200.0,
             "refine": {"Lsd": True}, "n_iter": 4, "lm_max_iter": 200, "device": "cpu"},
        result=_fake_result(BC_y=1111.0),
        loader_state={"path": "/data/single.h5", "dataset": "exchange/data", "frame_index": 2})

    meta = project.read_attempt(proj_path, project.list_attempts(proj_path, "single", "calib")[0]["ref"])
    cal_tab = CalibrationTab()
    cal_tab.apply_project_calibration({"single": meta})
    assert cal_tab._mode_ribbon.mode() == "single"
    assert cal_tab._seed_bcy.value() == pytest.approx(1111.0)
    assert cal_tab._cal.currentText() == "LaB6"
    assert cal_tab._manual_seed_check.isChecked()
    # Rings are redrawn from the stored result immediately (no image was
    # loaded here — loader_state's path doesn't exist — so the radial
    # profile/cake, which need an actual image, are correctly skipped).
    assert cal_tab._result is not None
    assert len(cal_tab._ring_items) > 0


def test_apply_project_integration_populates_batch_plots_single_detector(app, tmp_path):
    """Open Project → Batch Integrate (single-detector) should replay the
    stored profiles/r_axis_px arrays into the Waterfall/Stacked-profiles
    views right away, not leave them blank until a fresh run. The Hydra,
    per-panel version of this is covered (without building a second
    HydraBatchPage's worth of pyqtgraph widgets — see .context/STATE.md's
    interpreter-teardown crash-risk note) by
    ``test_apply_project_calibration_and_integration_hydra`` above, which
    already builds one."""
    from midas_gui.tab_batch import BatchTab

    proj_path = str(tmp_path / "proj.h5")
    project.create_project(proj_path)
    calib_result = _fake_result()
    calib_ref = project.append_calibration_attempt(
        proj_path, "single", cfg={"mode": "one_shot"}, result=calib_result, loader_state={})
    ref = project.append_integration_attempt(
        proj_path, "single", inputs={"src_cfg": {"type": "hdf5", "path": None}},
        finished_payload={
            "n": 4, "aborted": False,
            "profiles": np.random.rand(4, 12).astype(np.float32),
            "r_axis_px": np.arange(12, dtype=np.float32),
            "frame_ids": [f"f{i}" for i in range(4)],
        },
        calibration_snapshot={"Lsd": calib_result.Lsd, "wavelength_A": 0.1729,
                               "BC_y": 1024.0, "BC_z": 1024.0,
                               "NrPixelsY": 2048, "NrPixelsZ": 2048},
        calib_attempt_ref=calib_ref)
    meta = project.read_attempt(proj_path, ref)
    meta["_results_arrays"] = project.read_attempt_results(proj_path, ref)

    batch_tab = BatchTab()
    batch_tab.apply_project_integration({"single": meta})
    assert batch_tab._waterfall._nrows == 4
    assert len(batch_tab._stack_view._profiles) == 4


def test_hash_paths_in_adds_hash_for_existing_files(tmp_path):
    f = tmp_path / "data.bin"
    f.write_bytes(b"abc123")
    obj = {"path": str(f), "nested": {"path": str(f)}, "missing": {"path": "/no/such/file"}}
    out = project._hash_paths_in(obj)
    assert out["path_hash"]["method"] == "sha256_full"
    assert out["nested"]["path_hash"]["method"] == "sha256_full"
    assert "path_hash" not in out["missing"]
