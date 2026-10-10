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

import numcodecs
import numpy as np
import pytest

zarr = pytest.importorskip("zarr")

from midas_gui.hydra_zarr_combine import (build_panel_tag, combine_panel_zarrs,
                                          default_combined_path, validate)

PANELS = ["ge1", "ge2", "ge3", "ge4"]
NR, NETA, NFRAMES = 7, 3, 2
MPE_WF = Path("/home/beams3/PARKJS/opt/mpe_wf_saxs_waxs/combine_hydra_zarr.py")


#: The windmill tiling a real Hydra run writes, panel by panel: four 90 deg
#: sectors that meet edge to edge, ge1's 210 being ge2's -150 once wrapped.
#: The ORIGINAL fixture here invented ``ETA_MIN``/``ETA_MAX`` keys and gave
#: the four panels nested ranges (-10..10, -20..20, ...) that tile nothing.
#: Both mistakes hid a live bug: the aggregation keyed off a ``_MIN`` suffix
#: no writer in this app emits, so on real archives it never ran at all.
#: Fixtures now use the spelling and the geometry that real files have.
PANEL_ETA = {"ge1": (120.0, 210.0), "ge2": (-150.0, -60.0),
             "ge3": (-60.0, 30.0), "ge4": (30.0, 120.0)}


def _panel_zarr(path, panel, *, with_ir=True, n_eta=NETA, n_r=NR, omegas=None,
                eta=None, with_sumframes=True):
    """One synthetic panel archive in the layout the combiner expects."""
    idx = int(panel[2:])
    eta_lo, eta_hi = eta if eta is not None else PANEL_ETA[panel]
    store = zarr.ZipStore(str(path), mode="w")
    root = zarr.group(store)
    root.attrs.put({"source": "test", "provenance_history": [
        {"tool": "integrate", "cake_params": {"EtaMin": eta_lo,
                                              "EtaMax": eta_hi,
                                              "EtaBinSize": 10.0,
                                              "RMin": 5.0, "RMax": 900.0,
                                              "RBinSize": 1.0}}]})
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
    # Both of these are in every real archive and both were being dropped:
    # SumFrames is an (nR, nEta) caked array, i.e. primary data.
    if with_sumframes:
        root.create_dataset("SumFrames",
                            data=np.full((n_r, n_eta), float(idx)))
    ai = root.create_group("active_instrument")
    ai.create_dataset("ExpID", data=np.array(["connolly_oct26"], dtype=object),
                      object_codec=numcodecs.VLenUTF8())
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


def test_the_merge_entry_spans_the_sweep_the_file_actually_covers(panels,
                                                                  tmp_path):
    """Four tiling 90 deg panels make one 360 deg sweep, and it runs PAST
    180: the axis is 120 -> 480, not the last panel's EtaMax of 120."""
    got = _read(combine_panel_zarrs(panels, tmp_path / "m.zarr.zip"))
    cake = got["attrs"]["provenance_history"][-1]["cake_params"]
    assert (cake["EtaMin"], cake["EtaMax"]) == (120.0, 480.0)
    assert cake["RMin"] == 5.0 and cake["RMax"] == 900.0
    assert cake["RBinSize"] == 1.0, "a value all panels agree on carries through"
    assert got["attrs"]["provenance_history"][-1]["extra"][
        "eta_tiling_problem"] is None


def test_a_gap_in_the_tiling_drops_the_eta_span_and_is_reported(tmp_path):
    """connolly_oct26 ge1234, exactly: ge4 was caked from 40 where ge3 ends
    at 30, so the merged archive is one 10 deg bin short of a full turn --
    35 eta columns, not 36. Nothing about a (3520, 35) array says so, and
    reporting EtaMin=-150/EtaMax=210 would actively assert the hole is not
    there. The span is dropped and the gap named instead."""
    paths, seen = {}, []
    for p in PANELS:
        lo, hi = PANEL_ETA[p]
        if p == "ge4":
            lo = 40.0                       # the typo, one bin too high
        paths[p] = str(_panel_zarr(tmp_path / f"s_000001.{p}.zarr.zip", p,
                                   eta=(lo, hi)))
    out = combine_panel_zarrs(paths, tmp_path / "m.zarr.zip",
                              warn=seen.append)
    cake = _read(out)["attrs"]["provenance_history"][-1]["cake_params"]
    assert "EtaMin" not in cake and "EtaMax" not in cake
    assert cake["RMin"] == 5.0, "R is unaffected -- it does not wrap"
    tiling = [m for m in seen if "do not tile" in m]
    assert len(tiling) == 1
    assert "10 deg gap" in tiling[0] and "ge3" in tiling[0] and "ge4" in tiling[0]


def test_an_overlap_is_caught_as_well_as_a_gap(tmp_path):
    paths, seen = {}, []
    for p in PANELS:
        lo, hi = PANEL_ETA[p]
        if p == "ge4":
            lo = 10.0                       # 20 deg back over ge3
        paths[p] = str(_panel_zarr(tmp_path / f"s_000001.{p}.zarr.zip", p,
                                   eta=(lo, hi)))
    combine_panel_zarrs(paths, tmp_path / "m.zarr.zip", warn=seen.append)
    tiling = [m for m in seen if "do not tile" in m]
    assert len(tiling) == 1 and "20 deg overlap" in tiling[0]


def test_a_non_tiling_set_is_still_written(tmp_path):
    """A hole is the user's caking to fix; refusing to write would cost them
    the other 350 degrees they did ask for."""
    paths = {p: str(_panel_zarr(tmp_path / f"s_000001.{p}.zarr.zip", p,
                                eta=(40.0, 120.0) if p == "ge4"
                                else PANEL_ETA[p]))
             for p in PANELS}
    out = combine_panel_zarrs(paths, tmp_path / "m.zarr.zip")
    assert Path(out).is_file(), "no warn sink must not mean no archive"


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


# ── nothing in a source archive may vanish ──────────────────────────────
# connolly_oct26's merged file was missing SumFrames and active_instrument:
# _write_merged reproduced a hardcoded group list from the reference script
# and dropped everything else without a word. The two names were the
# symptom; the silent list was the bug.

def _read_top(path):
    store = zarr.ZipStore(str(path), mode="r")
    try:
        return sorted(zarr.group(store).keys())
    finally:
        store.close()


def test_the_merged_file_has_every_top_level_entry_a_panel_had(panels, tmp_path):
    out = combine_panel_zarrs(panels, tmp_path / "m.zarr.zip")
    src = _read_top(panels["ge1"])
    assert set(src) <= set(_read_top(out)), "a source entry went missing"


def test_sumframes_is_eta_concatenated_like_the_other_caked_arrays(panels,
                                                                   tmp_path):
    """(nR, nEta) per panel -> (nR, sum nEta). It is primary data, not
    metadata: copying panel 0's would have been just as wrong as dropping
    it."""
    out = combine_panel_zarrs(panels, tmp_path / "m.zarr.zip")
    store = zarr.ZipStore(str(out), mode="r")
    try:
        sf = zarr.group(store)["SumFrames"][...]
        assert sf.shape == (NR, NETA * len(PANELS))
        # each panel's block carries its own idx, in panel order
        for i, p in enumerate(PANELS):
            block = sf[:, i * NETA:(i + 1) * NETA]
            assert np.all(block == float(int(p[2:])))
    finally:
        store.close()


def test_active_instrument_is_carried_verbatim(panels, tmp_path):
    out = combine_panel_zarrs(panels, tmp_path / "m.zarr.zip")
    store = zarr.ZipStore(str(out), mode="r")
    try:
        assert zarr.group(store)["active_instrument"]["ExpID"][0] \
            == "connolly_oct26"
    finally:
        store.close()


def test_an_entry_this_module_has_never_heard_of_is_still_carried(tmp_path):
    """The regression guard that outlives the two names above: a future
    MIDAS release adding a dataset must not lose it silently."""
    paths, seen = {}, []
    for p in PANELS:
        path = tmp_path / f"s_000001.{p}.zarr.zip"
        _panel_zarr(path, p)
        store = zarr.ZipStore(str(path), mode="a")
        zarr.group(store).create_dataset(
            "SomethingNew", data=np.full((NR, NETA), float(int(p[2:]))))
        store.close()
        paths[p] = str(path)
    out = combine_panel_zarrs(paths, tmp_path / "m.zarr.zip", warn=seen.append)
    store = zarr.ZipStore(str(out), mode="r")
    try:
        got = zarr.group(store)["SomethingNew"][...]
    finally:
        store.close()
    assert got.shape == (NR, NETA * len(PANELS)), \
        "an (nR, nEta) array is eta-caked by construction"
    assert any("SomethingNew" in m for m in seen), \
        "carrying it silently is the same bug in a new coat"


def _with_extra(tmp_path, name, data_for, *, eta=None):
    """Four panels, each carrying an extra top-level array."""
    paths = {}
    for p in PANELS:
        path = tmp_path / f"s_000001.{p}.zarr.zip"
        _panel_zarr(path, p, n_eta=(1 if eta else NETA),
                    eta=(eta[p] if eta else None))
        store = zarr.ZipStore(str(path), mode="a")
        zarr.group(store).create_dataset(name, data=data_for(p))
        store.close()
        paths[p] = str(path)
    return paths


#: One 10 deg bin on each cardinal -- "I only produce cakes along the main
#: directions 0, 90, 180, and -90 deg, each with 10 deg eta bin size." Every
#: panel then has nEta=1, which is what breaks a naive shape test.
CARDINAL_ETA = {"ge1": (175.0, 185.0), "ge2": (-95.0, -85.0),
                "ge3": (-5.0, 5.0), "ge4": (85.0, 95.0)}


def test_cardinal_only_caking_merges_one_bin_per_panel(tmp_path):
    paths = {p: str(_panel_zarr(tmp_path / f"s_000001.{p}.zarr.zip", p,
                                n_eta=1, eta=CARDINAL_ETA[p]))
             for p in PANELS}
    out = combine_panel_zarrs(paths, tmp_path / "m.zarr.zip")
    got = _read(out)
    assert got["osf"]["FrameNr_0"].shape == (NR, len(PANELS))
    assert list(got["osf"]["FrameNr_0"][0, :]) == [1.0, 2.0, 3.0, 4.0]


def test_at_one_bin_per_panel_a_non_caked_array_is_not_concatenated(tmp_path):
    """The trailing-axis test is only worth something when the widths say
    something. Cake four cardinals at one bin each and EVERY panel has
    nEta=1, so an unrelated (3, 1) array matches [1,1,1,1] -- and was
    concatenated into a (3, 4) fabrication before this was guarded."""
    paths = _with_extra(tmp_path, "NotACake",
                        lambda p: np.full((3, 1), float(int(p[2:]))),
                        eta=CARDINAL_ETA)
    seen = []
    out = combine_panel_zarrs(paths, tmp_path / "m.zarr.zip", warn=seen.append)
    store = zarr.ZipStore(str(out), mode="r")
    try:
        got = zarr.group(store)["NotACake"][...]
    finally:
        store.close()
    assert got.shape == (3, 1), "shape says it is not a cake; do not stack it"
    assert any("NotACake (from ge1)" in m for m in seen), \
        "and say which rule was applied"


def test_at_one_bin_per_panel_a_real_cake_is_still_concatenated(tmp_path):
    """The guard must not cost the genuine article: same leading dims as
    the caked arrays already in the file means it IS one."""
    paths = _with_extra(tmp_path, "AlsoACake",
                        lambda p: np.full((NR, 1), float(int(p[2:]))),
                        eta=CARDINAL_ETA)
    seen = []
    out = combine_panel_zarrs(paths, tmp_path / "m.zarr.zip", warn=seen.append)
    store = zarr.ZipStore(str(out), mode="r")
    try:
        got = zarr.group(store)["AlsoACake"][...]
    finally:
        store.close()
    assert got.shape == (NR, len(PANELS))
    assert any("AlsoACake (eta-concatenated)" in m for m in seen)


def test_a_one_dimensional_array_is_never_treated_as_a_cake(tmp_path):
    paths = _with_extra(tmp_path, "AVector",
                        lambda p: np.arange(NETA, dtype=float))
    out = combine_panel_zarrs(paths, tmp_path / "m.zarr.zip")
    store = zarr.ZipStore(str(out), mode="r")
    try:
        assert zarr.group(store)["AVector"].shape == (NETA,)
    finally:
        store.close()


def test_an_entry_only_some_panels_have_is_left_out_and_named(tmp_path):
    paths, seen = {}, []
    for p in PANELS:
        path = tmp_path / f"s_000001.{p}.zarr.zip"
        _panel_zarr(path, p)
        if p == "ge1":
            store = zarr.ZipStore(str(path), mode="a")
            zarr.group(store).create_dataset("OnlyGe1", data=np.zeros(3))
            store.close()
        paths[p] = str(path)
    out = combine_panel_zarrs(paths, tmp_path / "m.zarr.zip", warn=seen.append)
    assert "OnlyGe1" not in _read_top(out)
    assert any("OnlyGe1" in m for m in seen)


def test_sumframes_missing_from_only_some_panels_is_refused(tmp_path):
    """Same rule as IntegrationResult: absent from all, or present in all.
    A partial set is a mistake worth stopping for."""
    paths = {p: str(_panel_zarr(tmp_path / f"s_000001.{p}.zarr.zip", p,
                                with_sumframes=(p != "ge3")))
             for p in PANELS}
    with pytest.raises(ValueError, match="SumFrames"):
        combine_panel_zarrs(paths, tmp_path / "m.zarr.zip")


# ── eta past the +/-180 wrap ────────────────────────────────────────────
# "ge1 eta range is odd in that ge1 eta should be spanning 120 to 210 deg",
# then "ge2 eta range looks ok", "ge3 looks ok", "ge4 eta also looks ok".
# ge1 is the only one of the four whose range crosses the branch cut, and
# its bins at 185/195/205 came back identically zero across all 3520 R
# bins. pixel_to_REta types eta as "[-180, 180)" -- those bins cannot be
# filled by any pixel, so 1,027,309 real pixels (33% of ge1's in-range
# area) were dropped from the integration with nothing said.

def test_a_range_past_180_is_reported_with_the_bins_that_cannot_fill(panels,
                                                                     tmp_path):
    seen = []
    combine_panel_zarrs(panels, tmp_path / "m.zarr.zip", warn=seen.append)
    msg = [m for m in seen if "180" in m and "empty" in m]
    assert len(msg) == 1, "the wrap must be called out on its own"
    assert "ge1" in msg[0]
    for centre in ("185", "195", "205"):
        assert centre in msg[0], "name the bins, not just the panel"


def test_the_panels_that_do_not_cross_are_not_implicated(panels, tmp_path):
    seen = []
    combine_panel_zarrs(panels, tmp_path / "m.zarr.zip", warn=seen.append)
    msg = next(m for m in seen if "180" in m and "empty" in m)
    for ok in ("ge2", "ge3", "ge4"):
        assert ok not in msg


def test_a_set_wholly_inside_the_cut_says_nothing(tmp_path):
    inside = {"ge1": (120.0, 180.0), "ge2": (-150.0, -60.0),
              "ge3": (-60.0, 30.0), "ge4": (30.0, 120.0)}
    paths = {p: str(_panel_zarr(tmp_path / f"s_000001.{p}.zarr.zip", p,
                                eta=inside[p])) for p in PANELS}
    seen = []
    combine_panel_zarrs(paths, tmp_path / "m.zarr.zip", warn=seen.append)
    assert not [m for m in seen if "empty" in m]


def test_the_check_is_shared_with_the_gui_not_reimplemented(panels, tmp_path):
    """The table and the archive must not be able to disagree about which
    bins are unfillable."""
    from midas_gui.hydra_zarr_combine import eta_beyond_branch_cut
    per = {"ge1": {"EtaMin": 120.0, "EtaMax": 210.0, "EtaBinSize": 10.0}}
    assert eta_beyond_branch_cut(per, ["ge1"]) is not None
    per["ge1"]["EtaMax"] = 180.0
    assert eta_beyond_branch_cut(per, ["ge1"]) is None


def test_exactly_180_is_outside_and_minus_180_is_inside(tmp_path):
    """pixel_to_REta is [-180, 180): -180 is attainable, +180 is not. A bin
    CENTRE is what has to land inside, not the range edge."""
    from midas_gui.hydra_zarr_combine import eta_beyond_branch_cut
    # centres 175, 185 -> one unfillable
    per = {"ge1": {"EtaMin": 170.0, "EtaMax": 190.0, "EtaBinSize": 10.0}}
    msg = eta_beyond_branch_cut(per, ["ge1"])
    assert msg is not None and "185" in msg and "175" not in msg
    # centres -175, -165 -> both fine
    per = {"ge1": {"EtaMin": -180.0, "EtaMax": -160.0, "EtaBinSize": 10.0}}
    assert eta_beyond_branch_cut(per, ["ge1"]) is None
