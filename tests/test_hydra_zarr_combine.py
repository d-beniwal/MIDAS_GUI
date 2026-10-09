"""The ported ge1..ge4 zarr combiner.

A port of mpe_wf_saxs_waxs/combine_hydra_zarr.py (see
midas_gui/hydra_zarr_combine.py). The LAYOUT is the contract -- downstream
tools read these arrays -- so the shapes, concat axes and attrs are pinned
here, and where the original script is present on this machine its output is
compared against ours on identical inputs, which is a far stronger check
than asserting our own expectations back at us.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

zarr = pytest.importorskip("zarr")

from midas_gui.hydra_zarr_combine import (build_panel_tag, combine_panel_zarrs,
                                          default_combined_path, validate)

PANELS = ["ge1", "ge2", "ge3", "ge4"]
NR, NETA, NFRAMES = 7, 3, 2
MPE_WF = Path("/home/beams3/PARKJS/opt/mpe_wf_saxs_waxs/combine_hydra_zarr.py")


def _panel_zarr(path, panel, *, with_ir=True, n_eta=NETA, n_r=NR, omegas=None):
    """One synthetic panel archive in the layout the combiner expects."""
    idx = int(panel[2:])
    store = zarr.ZipStore(str(path), mode="w")
    root = zarr.group(store)
    root.attrs.put({"source": "test", "provenance_history": [
        {"tool": "integrate", "cake_params": {"ETA_MIN": -10.0 * idx,
                                              "ETA_MAX": 10.0 * idx,
                                              "R_MIN": 5.0, "R_MAX": 900.0,
                                              "R_STEP": 1.0}}]})
    root.create_dataset("Omegas",
                        data=np.arange(NFRAMES, dtype=float) if omegas is None
                        else np.asarray(omegas, dtype=float))
    ip = root.create_group("InstrumentParameters")
    ip.create_dataset("Distance", data=np.array([1000.0 + idx]))
    ip.create_dataset("Wavelength", data=np.array([0.1536]))
    for group in (["IntegrationResult"] if with_ir else []) + ["OmegaSumFrame"]:
        g = root.create_group(group)
        for f in range(NFRAMES):
            g.create_dataset(f"FrameNr_{f}",
                             data=np.full((n_r, n_eta), float(idx)))
    root.create_dataset("REtaMap", data=np.full((2, n_r, n_eta), float(idx)))
    inst = root.create_group("instrument")
    inst.create_dataset("energy", data=np.array([80.725]))
    store.close()
    return path


@pytest.fixture
def panels(tmp_path):
    return {p: str(_panel_zarr(tmp_path / f"scan_000001.{p}.zarr.zip", p))
            for p in PANELS}


def _read(path):
    store = zarr.ZipStore(str(path), mode="r")
    try:
        z = zarr.group(store)
        return {
            "attrs": dict(z.attrs),
            "ir": {k: z["IntegrationResult"][k][...] for k in z["IntegrationResult"]}
                  if "IntegrationResult" in z else {},
            "osf": {k: z["OmegaSumFrame"][k][...] for k in z["OmegaSumFrame"]},
            "reta": z["REtaMap"][...],
            "omegas": z["Omegas"][...],
            "ip": {k: z["InstrumentParameters"][k][...]
                   for k in z["InstrumentParameters"]},
            "ip_attrs": dict(z["InstrumentParameters"].attrs),
            "has_instrument": "instrument" in z,
        }
    finally:
        store.close()


# ── layout ───────────────────────────────────────────────────────────────
def test_eta_is_concatenated_and_r_is_not(panels, tmp_path):
    out = combine_panel_zarrs(panels, tmp_path / "merged.zarr.zip")
    got = _read(out)
    for key, arr in got["osf"].items():
        assert arr.shape == (NR, NETA * 4), key
    for key, arr in got["ir"].items():
        assert arr.shape == (NR, NETA * 4), key


def test_panels_land_in_panel_order_along_eta(panels, tmp_path):
    """Each synthetic panel is filled with its own index, so the merged eta
    axis must read 1,1,1, 2,2,2, 3,3,3, 4,4,4."""
    got = _read(combine_panel_zarrs(panels, tmp_path / "m.zarr.zip"))
    row = got["osf"]["FrameNr_0"][0]
    assert list(row) == [1.0] * NETA + [2.0] * NETA + [3.0] * NETA + [4.0] * NETA


def test_retamap_concatenates_on_axis_2(panels, tmp_path):
    got = _read(combine_panel_zarrs(panels, tmp_path / "m.zarr.zip"))
    assert got["reta"].shape == (2, NR, NETA * 4)


def test_instrument_parameters_stack_rather_than_having_to_agree(panels, tmp_path):
    """Distance legitimately differs per hydra panel."""
    got = _read(combine_panel_zarrs(panels, tmp_path / "m.zarr.zip"))
    assert list(got["ip"]["Distance"]) == [1001.0, 1002.0, 1003.0, 1004.0]
    assert got["ip_attrs"]["panels"] == PANELS


def test_omegas_and_bulk_groups_come_from_the_first_panel(panels, tmp_path):
    got = _read(combine_panel_zarrs(panels, tmp_path / "m.zarr.zip"))
    assert list(got["omegas"]) == list(range(NFRAMES))
    assert got["has_instrument"]


def test_the_merged_file_records_which_panels_went_in(panels, tmp_path):
    got = _read(combine_panel_zarrs(panels, tmp_path / "m.zarr.zip"))
    assert got["attrs"]["combined_panels"] == PANELS


# ── provenance ───────────────────────────────────────────────────────────
def test_every_panels_history_is_carried_forward_and_tagged(panels, tmp_path):
    got = _read(combine_panel_zarrs(panels, tmp_path / "m.zarr.zip"))
    hist = got["attrs"]["provenance_history"]
    tagged = [e for e in hist if e.get("source_panel")]
    assert sorted(e["source_panel"] for e in tagged) == PANELS
    assert hist[-1]["tool"] == "midas_gui.hydra_zarr_combine"


def test_the_merge_entry_spans_every_panels_eta_range(panels, tmp_path):
    """The merged file really does cover -40..40, so saying so is honest."""
    got = _read(combine_panel_zarrs(panels, tmp_path / "m.zarr.zip"))
    cake = got["attrs"]["provenance_history"][-1]["cake_params"]
    assert cake["ETA_MIN"] == -40.0 and cake["ETA_MAX"] == 40.0
    assert cake["R_STEP"] == 1.0, "a value all panels agree on is carried through"


# ── validation refuses before writing ────────────────────────────────────
def _zs(paths):
    return [zarr.group(zarr.ZipStore(str(p), mode="r")) for p in paths]


def test_mismatched_eta_is_fine_but_mismatched_r_is_not(tmp_path):
    a = _panel_zarr(tmp_path / "a.ge1.zarr.zip", "ge1", n_r=7)
    b = _panel_zarr(tmp_path / "b.ge2.zarr.zip", "ge2", n_r=9)
    with pytest.raises(ValueError, match="R-bins differ"):
        validate(["ge1", "ge2"], _zs([a, b]))


def test_differing_omegas_are_refused(tmp_path):
    a = _panel_zarr(tmp_path / "a.ge1.zarr.zip", "ge1")
    b = _panel_zarr(tmp_path / "b.ge2.zarr.zip", "ge2", omegas=[9.0, 9.0])
    with pytest.raises(ValueError, match="Omegas differ"):
        validate(["ge1", "ge2"], _zs([a, b]))


def test_integrationresult_in_only_some_panels_is_refused(tmp_path):
    """Absent everywhere is legitimate (MIDAS 11.0 with individualSave=0);
    absent in only some is a mistake worth stopping for."""
    a = _panel_zarr(tmp_path / "a.ge1.zarr.zip", "ge1", with_ir=True)
    b = _panel_zarr(tmp_path / "b.ge2.zarr.zip", "ge2", with_ir=False)
    with pytest.raises(ValueError, match="present in some panels"):
        validate(["ge1", "ge2"], _zs([a, b]))


def test_omegasumframe_only_panels_still_combine(tmp_path):
    ps = {p: str(_panel_zarr(tmp_path / f"s.{p}.zarr.zip", p, with_ir=False))
          for p in ("ge1", "ge2")}
    got = _read(combine_panel_zarrs(ps, tmp_path / "m.zarr.zip"))
    assert got["ir"] == {}
    assert got["osf"]["FrameNr_0"].shape == (NR, NETA * 2)


def test_nothing_is_written_when_validation_fails(tmp_path):
    ps = {"ge1": str(_panel_zarr(tmp_path / "a.ge1.zarr.zip", "ge1", n_r=7)),
          "ge2": str(_panel_zarr(tmp_path / "b.ge2.zarr.zip", "ge2", n_r=9))}
    out = tmp_path / "m.zarr.zip"
    with pytest.raises(ValueError):
        combine_panel_zarrs(ps, out)
    assert not out.exists(), "a half-merged archive must never be left behind"


def test_a_single_panel_is_not_a_combination(tmp_path):
    ps = {"ge1": str(_panel_zarr(tmp_path / "a.ge1.zarr.zip", "ge1"))}
    with pytest.raises(ValueError, match="at least two panels"):
        combine_panel_zarrs(ps, tmp_path / "m.zarr.zip")


# ── naming ───────────────────────────────────────────────────────────────
def test_the_panel_tag_collapses_a_regular_set():
    assert build_panel_tag(PANELS) == "ge1234"
    assert build_panel_tag(["ge1", "varex2"]) == "ge1_varex2"


def test_the_default_output_sits_beside_the_inputs(panels):
    """Flat fixture (no <panel>/zarr/ nesting) -- falls back to the input's
    own directory."""
    out = default_combined_path(panels)
    assert out.name == "scan_000001.ge1234.zarr.zip"
    assert out.parent == Path(panels["ge1"]).parent


def test_a_real_run_layout_puts_it_under_the_froot_dir(tmp_path):
    """The merged archive is a property of the RUN, not of a detector, so it
    is a sibling of ge1..ge4 rather than buried in ge1/zarr/."""
    froot = tmp_path / "expid_bc" / "my_scan"
    ps = {p: str(froot / p / "zarr" / f"my_scan_000012.{p}.zarr.zip")
          for p in PANELS}
    out = default_combined_path(ps)
    assert out == froot / "my_scan_000012.ge1234.zarr.zip"
    assert out.parent.name != "zarr" and out.parent.name != "ge1"


def test_an_explicit_out_dir_wins(tmp_path):
    ps = {p: str(tmp_path / p / "zarr" / f"s_000001.{p}.zarr.zip")
          for p in PANELS}
    out = default_combined_path(ps, tmp_path / "elsewhere")
    assert out == tmp_path / "elsewhere" / "s_000001.ge1234.zarr.zip"


# ── cross-check against the original, where it exists ────────────────────
@pytest.mark.skipif(not MPE_WF.is_file(), reason="mpe_wf_saxs_waxs not present")
def test_our_arrays_match_the_original_scripts(panels, tmp_path):
    """The strongest check available: same inputs through both implementations."""
    ours = combine_panel_zarrs(panels, tmp_path / "ours.zarr.zip")
    theirs = tmp_path / "theirs.zarr.zip"
    proc = subprocess.run(
        [sys.executable, str(MPE_WF), panels["ge1"],
         "--panels", ",".join(PANELS), "--out", str(theirs)],
        capture_output=True, text=True, cwd=str(MPE_WF.parent))
    if proc.returncode != 0 or not theirs.exists():
        pytest.skip(f"reference script did not run here: "
                    f"{proc.stderr.strip()[:300]}")
    a, b = _read(ours), _read(theirs)
    for key in ("reta", "omegas"):
        np.testing.assert_array_equal(a[key], b[key], err_msg=key)
    for group in ("ir", "osf"):
        assert sorted(a[group]) == sorted(b[group]), group
        for k in a[group]:
            np.testing.assert_array_equal(a[group][k], b[group][k],
                                          err_msg=f"{group}/{k}")
    for k in a["ip"]:
        np.testing.assert_array_equal(a["ip"][k], b["ip"][k], err_msg=k)
    assert a["attrs"]["combined_panels"] == b["attrs"]["combined_panels"]


# ── pairing frames across the four panel output folders ──────────────────
from midas_gui.hydra_zarr_combine import find_panel_zarr_sets


def _run_tree(root, frames, panels=PANELS, skip=()):
    """The layout a Hydra run leaves: <out>/ge{n}/zarr/<stem>.ge{n}.zarr.zip"""
    for p in panels:
        d = Path(root) / p / "zarr"
        d.mkdir(parents=True, exist_ok=True)
        for f in frames:
            if (p, f) in skip:
                continue
            (d / f"scan_{f:06d}.{p}.zarr.zip").write_bytes(b"")
    return Path(root)


def test_one_set_per_frame_across_all_panels(tmp_path):
    sets = find_panel_zarr_sets(_run_tree(tmp_path, [1, 2, 3]), PANELS)
    assert len(sets) == 3
    assert all(sorted(s) == PANELS for s in sets)


def test_each_set_pairs_the_same_frame(tmp_path):
    sets = find_panel_zarr_sets(_run_tree(tmp_path, [7]), PANELS)
    names = {Path(v).name for v in sets[0].values()}
    assert names == {f"scan_000007.{p}.zarr.zip" for p in PANELS}


def test_a_frame_missing_from_one_panel_is_skipped_not_part_combined(tmp_path):
    """Three of four panels would be silently short on eta."""
    tree = _run_tree(tmp_path, [1, 2], skip=[("ge3", 2)])
    sets = find_panel_zarr_sets(tree, PANELS)
    assert len(sets) == 1
    assert Path(sets[0]["ge1"]).name == "scan_000001.ge1.zarr.zip"


def test_no_sets_when_the_run_wrote_nothing(tmp_path):
    assert find_panel_zarr_sets(tmp_path, PANELS) == []


def test_frames_come_back_in_order(tmp_path):
    sets = find_panel_zarr_sets(_run_tree(tmp_path, [3, 1, 2]), PANELS)
    assert [Path(s["ge1"]).name for s in sets] == [
        "scan_000001.ge1.zarr.zip", "scan_000002.ge1.zarr.zip",
        "scan_000003.ge1.zarr.zip"]
