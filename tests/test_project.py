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
        path, "single", cfg=cfg, result=result, loader_state=loader_state,
        dark=np.ones((4, 4)), bright=np.ones((4, 4)) * 2)
    ref2 = project.append_calibration_attempt(
        path, "single", cfg=cfg, result=result, loader_state=loader_state)

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
        assert "dark" in att
        assert "bright" in att
        assert "mask" in att
        att2 = grp["attempt_0002"]
        assert "dark" not in att2


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
    tab._last_run_fields = {"mask": None, "dark": None, "bright": None, "background": None}
    data = {"n": 3, "out_paths": ["/tmp/out/f0.csv"], "aborted": False,
            "profiles": np.random.rand(3, 20).astype(np.float32)}

    tab._log_to_project(data)
    with h5py.File(proj_path, "r") as f:
        att = f["single/integrate/attempt_0001"]
        assert att.attrs["n_frames"] == 3
        assert att.attrs["calib_attempt_ref"] == "/single/calib/attempt_0003"
        assert att["results/profiles"].shape == (3, 20)


def test_hash_paths_in_adds_hash_for_existing_files(tmp_path):
    f = tmp_path / "data.bin"
    f.write_bytes(b"abc123")
    obj = {"path": str(f), "nested": {"path": str(f)}, "missing": {"path": "/no/such/file"}}
    out = project._hash_paths_in(obj)
    assert out["path_hash"]["method"] == "sha256_full"
    assert out["nested"]["path_hash"]["method"] == "sha256_full"
    assert "path_hash" not in out["missing"]
