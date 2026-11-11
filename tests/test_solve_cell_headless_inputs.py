"""Headless Solve Cell inputs: dark / bright / background fields, multiple mask sources, and the
headless ingest-tuning entry point. Reuses the synthetic crystal from test_solve_cell_headless."""
import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("midas_defect")
pytest.importorskip("midas_hkls")
h5py = pytest.importorskip("h5py")

from midas_gui import project
from midas_gui.solve_cell import headless as H
from test_solve_cell_headless import CELL_A, N_COLS, N_ROWS, _spec, synth  # noqa: F401


def test_corrections_and_mask_sources_are_applied_and_recorded(tmp_path, synth):
    bg = tmp_path / "bg.npy"; np.save(bg, np.full((N_ROWS, N_COLS), 0.5, np.float32))
    dk = tmp_path / "dark.h5"
    with h5py.File(dk, "w") as f:
        f.create_dataset("exchange/data_dark", data=np.zeros((4, N_ROWS, N_COLS), np.float32))
    m1 = np.zeros((N_ROWS, N_COLS), np.uint8); m1[10, 10] = 1
    m2 = np.zeros((N_ROWS, N_COLS), np.uint8); m2[20, 30] = 1
    np.save(tmp_path / "m1.npy", m1)
    (tmp_path / "mdir").mkdir(); np.save(tmp_path / "mdir" / "m2.npy", m2)
    out = tmp_path / "c.h5"
    spec = _spec(synth, out,
                 corrections={"dark": {"path": str(dk), "dataset": "exchange/data_dark", "start": 0, "end": -1},
                              "background": {"path": str(bg), "scale": 2.0}},
                 mask={"file": str(tmp_path / "m1.npy"), "sources": [str(tmp_path / "mdir")]})
    res = H.run_solve_cell_headless(spec)
    assert res["status"] == "ok", res["error"]
    assert res["conventional_cell"][0] == pytest.approx(CELL_A, rel=0.01)
    ld = project.read_solve_cell(str(out))["ingest"]["meta"]["panels"]["1"]["state"]["loader"]
    assert ld["dark"]["checked"] and ld["dark"]["path"] == str(dk)
    assert ld["background"]["checked"] and ld["background"]["scale"] == 2.0
    assert not ld["bright"]["checked"]
    assert [s["kind"] for s in ld["mask"]["sources"]] == ["file", "folder"]
    arr = project.read_solve_cell(str(out))["ingest"]["panels"]["panel_01"]["mask"]
    assert arr[10, 10] and arr[20, 30]                  # both mask sources reached the pipeline mask


@pytest.mark.parametrize("over,msg", [
    ({"corrections": {"flat": "x.tif"}}, "unknown key"),
    ({"corrections": {"bright_mode": "mul"}}, "bright_mode"),
    ({"corrections": {"dark": "/no/such/dark.tif"}}, "no such file"),
    ({"mask": {"file": "/no/such/mask.tif"}}, "no such file"),
])
def test_bad_inputs_are_reported(tmp_path, synth, over, msg):
    res = H.run_solve_cell_headless(_spec(synth, tmp_path / "x.h5", **over))
    assert res["status"] == "error" and msg in res["error"]
    assert not (tmp_path / "x.h5").exists()


def test_mask_and_field_shape_mismatch(tmp_path, synth):
    np.save(tmp_path / "bad.npy", np.zeros((N_ROWS + 1, N_COLS), np.uint8))
    res = H.run_solve_cell_headless(_spec(synth, tmp_path / "x.h5", mask={"file": str(tmp_path / "bad.npy")}))
    assert res["status"] == "error" and "shape" in res["error"]
    np.save(tmp_path / "bg.npy", np.zeros((3, 3), np.float32))
    res = H.run_solve_cell_headless(_spec(synth, tmp_path / "y.h5", corrections={"background": str(tmp_path / "bg.npy")}))
    assert res["status"] == "error" and "shape" in res["error"]


def test_tune_headless_returns_suggestion_and_writes_nothing(tmp_path, synth):
    spec = _spec(synth, tmp_path / "never.h5", tune={"sectors": [1], "workers": 1})
    spec.pop("project_path")
    res = H.run_tune_headless(spec)
    assert res["status"] == "ok", res["error"]
    p = res["by_panel"]["1"]
    assert set(p["suggestion"]) >= {"low_count_threshold", "sector_candidates", "blob_thresh",
                                    "blob_minvol", "blob_minframes"}
    assert res["suggestion"] == p["suggestion"] and res["confidence"] in ("high", "low", "medium")
    assert "Regime:" in p["report"] and not list(tmp_path.glob("*.h5"))
    out = H.suggestion_to_spec(res["suggestion"])
    assert out["blobs"]["sector_candidates"] == [1] and "threshold" in out["blobs"] and "low_count_threshold" in out["mask"]
    json.dumps(res, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))


def test_tune_cli_flag(tmp_path, synth):
    spec = _spec(synth, tmp_path / "n.h5", tune={"sectors": [1], "workers": 1}); spec.pop("project_path")
    sp = tmp_path / "spec.json"; sp.write_text(json.dumps(spec))
    rc = H.main([str(sp), "--tune", "-q", "--summary", str(tmp_path / "s.json")])
    assert rc == 0 and json.loads((tmp_path / "s.json").read_text())["status"] == "ok"
