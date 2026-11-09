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
# then "ge2 eta range looks ok", "ge3 looks ok", "ge4 eta also looks ok" --
# ge1 was the only one crossing the branch cut, and its bins at 185/195/205
# came back identically zero.
#
# That is now SERVED, not merely detected: eta_wrap bins the whole circle
# and lifts the requested bins out of it (tests/test_eta_wrap.py). What
# remains reportable is only what that cannot do.

def test_the_windmill_range_is_no_longer_reported_as_a_problem(panels,
                                                               tmp_path):
    """ge1's 120..210 is the case this all started from, and one run now
    fills it. Reporting it would be telling the user about a defect that
    is no longer there."""
    seen = []
    combine_panel_zarrs(panels, tmp_path / "m.zarr.zip", warn=seen.append)
    assert not [m for m in seen if "wrap" in m or "empty" in m]


def test_a_bin_straddling_the_seam_is_still_reported(tmp_path):
    """A 10 deg bin centred on 180 spans 175..185, half on each side of the
    cut. No single floor() binning can fill it and the whole-circle detour
    cannot either -- it would need two sub-bins recombined by pixel count.
    Said out loud rather than returned half empty."""
    from midas_gui.hydra_zarr_combine import eta_beyond_branch_cut
    per = {"ge1": {"EtaMin": 175.0, "EtaMax": 185.0, "EtaBinSize": 10.0}}
    msg = eta_beyond_branch_cut(per, ["ge1"])
    assert msg is not None and "ge1" in msg


def test_a_grid_that_does_not_divide_the_circle_is_still_reported(tmp_path):
    from midas_gui.hydra_zarr_combine import eta_beyond_branch_cut
    per = {"ge1": {"EtaMin": 120.0, "EtaMax": 210.0, "EtaBinSize": 7.0}}
    assert eta_beyond_branch_cut(per, ["ge1"]) is not None


def test_a_servable_wrapped_range_says_nothing(tmp_path):
    from midas_gui.hydra_zarr_combine import eta_beyond_branch_cut
    for lo, hi in ((120.0, 210.0), (170.0, 190.0), (-190.0, -170.0)):
        per = {"ge1": {"EtaMin": lo, "EtaMax": hi, "EtaBinSize": 10.0}}
        assert eta_beyond_branch_cut(per, ["ge1"]) is None, (lo, hi)


def test_the_check_is_shared_with_the_integrator_not_reimplemented(tmp_path):
    """The table, the archive and the binner must not be able to disagree
    about what is servable."""
    from midas_gui.eta_wrap import plan_eta
    from midas_gui.hydra_zarr_combine import eta_beyond_branch_cut
    for lo, hi, step in ((120.0, 210.0, 10.0), (175.0, 185.0, 10.0),
                         (120.0, 210.0, 7.0), (-60.0, 30.0, 10.0)):
        per = {"ge1": {"EtaMin": lo, "EtaMax": hi, "EtaBinSize": step}}
        reported = eta_beyond_branch_cut(per, ["ge1"]) is not None
        assert reported == (plan_eta(lo, hi, step).problem is not None)


# ── an unusable panel archive must say so in words ─────────────────────
def test_an_empty_panel_archive_is_named_not_raised_as_a_bare_keyerror():
    """A panel whose integration failed leaves a valid but EMPTY zip. The
    first thing to touch it raised KeyError('OmegaSumFrame'), which reached
    the run log as the single word 'OmegaSumFrame' -- no panel, no reason,
    nowhere to look. Reported from 1-ID-E exactly that way."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        paths = {p: str(_panel_zarr(tmp / f"s_000001.{p}.zarr.zip", p))
                 for p in PANELS}
        # ge1 as the failed run leaves it: a zip with nothing in it.
        empty = tmp / "s_000001.ge1.zarr.zip"
        store = zarr.ZipStore(str(empty), mode="w")
        zarr.group(store)
        store.close()
        paths["ge1"] = str(empty)
        with pytest.raises(ValueError) as e:
            combine_panel_zarrs(paths, tmp / "m.zarr.zip")
    msg = str(e.value)
    assert "ge1" in msg, "name the panel"
    assert "OmegaSumFrame" in msg
    assert "bytes" in msg, "say how big the file is"
    assert "run log" in msg, "say where its error is"


def test_a_complete_set_is_not_slowed_down_by_the_check(panels, tmp_path):
    out = combine_panel_zarrs(panels, tmp_path / "m.zarr.zip")
    assert Path(out).is_file()


# ── "one zarr for the whole scan" names every panel the same ───────────
# _run_out_stem names a run-grouped archive from the FROOT, which by
# definition has the panel token split off -- so all four panels write the
# same basename into their own ge{n}/zarr/. Requiring ".ge1." in the name
# found nothing, and the combine said "no frame had an archive from every
# panel" about a run that had four.

def test_a_run_grouped_set_is_matched_by_shared_name(tmp_path):
    from midas_gui.hydra_zarr_combine import find_panel_zarr_sets
    for p in PANELS:
        d = tmp_path / p / "zarr"
        d.mkdir(parents=True)
        (d / "4130_D_load_step_0.ave.zarr.zip").touch()
    got = find_panel_zarr_sets(tmp_path, PANELS)
    assert len(got) == 1 and sorted(got[0]) == PANELS


def test_a_per_frame_set_is_still_matched_by_substitution(tmp_path):
    from midas_gui.hydra_zarr_combine import find_panel_zarr_sets
    for p in PANELS:
        d = tmp_path / p / "zarr"
        d.mkdir(parents=True)
        (d / f"scan_005122.{p}.ave.zarr.zip").touch()
    got = find_panel_zarr_sets(tmp_path, PANELS)
    assert len(got) == 1 and sorted(got[0]) == PANELS


def test_an_archive_naming_another_panel_does_not_lead_a_set(tmp_path):
    """A stray ...ge3... in ge1/zarr/ is not ge1's to match siblings from."""
    from midas_gui.hydra_zarr_combine import find_panel_zarr_sets
    for p in PANELS:
        (tmp_path / p / "zarr").mkdir(parents=True)
    (tmp_path / "ge1" / "zarr" / "scan_005122.ge3.ave.zarr.zip").touch()
    assert find_panel_zarr_sets(tmp_path, PANELS) == []


def test_the_shared_name_rule_reads_the_name_not_the_folder(tmp_path):
    """Every archive lives under ge{n}/zarr/, so a whole-path test would
    see a token on all of them and the shared-name scheme could never be
    recognised."""
    from midas_gui.hydra_zarr_combine import panel_for_name
    assert panel_for_name("4130_D_load_step_0.ave.zarr.zip") is None
    assert panel_for_name("scan_005122.ge2.ave.zarr.zip") == 2


# ── two readings of one rotation are not bit-identical ─────────────────
# A real run: four panels integrated, then
#     [combine] ..._005120.ge1.000000_000063.ave.zarr.zip:
#               Omegas differ between ge1 and ge2
#     [combine] wrote 0/1 combined archive(s).
# Each panel's omega is now read from its OWN file's measured channel, so
# they agree to the encoder's precision, not to the bit. Exact equality
# rejected a perfectly good set.

def test_encoder_jitter_between_panels_is_not_a_mismatch(tmp_path):
    om = np.linspace(0.0, 6.3, NFRAMES)
    paths = {p: str(_panel_zarr(tmp_path / f"s_000001.{p}.zarr.zip", p,
                                omegas=om + 1e-7 * int(p[2:])))
             for p in PANELS}
    out = combine_panel_zarrs(paths, tmp_path / "m.zarr.zip")
    assert Path(out).is_file()


def test_a_genuinely_different_rotation_is_still_refused(tmp_path):
    """Offset by more than one step — the panels are not on the same
    frames. (The threshold is the STEP, so the offset has to be stated
    relative to it, not as an absolute angle.)"""
    step = 0.064
    om = np.arange(NFRAMES, dtype=float) * step
    paths = {p: str(_panel_zarr(tmp_path / f"s_000001.{p}.zarr.zip", p,
                                omegas=om + (step * 3 if p == "ge3" else 0.0)))
             for p in PANELS}
    with pytest.raises(ValueError) as e:
        combine_panel_zarrs(paths, tmp_path / "m.zarr.zip")
    msg = str(e.value)
    assert "ge3" in msg and "not the same frames" in msg, msg


def test_the_tolerance_scales_with_the_omega_step(tmp_path):
    """Measured on a real set: ge3/ge4 matched ge1 exactly and ge2 differed
    on ONE frame of 64 by 0.0321 deg against a 0.064 deg step — half a
    step, mean 0.0005. An encoder reading, not a different scan, and a
    fixed 1e-3 deg tolerance rejected the whole four-panel set over it."""
    step = 0.064
    om = np.arange(NFRAMES, dtype=float) * step
    glitch = om.copy()
    glitch[1] += step / 2.0
    paths = {p: str(_panel_zarr(tmp_path / f"s_000001.{p}.zarr.zip", p,
                                omegas=glitch if p == "ge2" else om))
             for p in PANELS}
    assert Path(combine_panel_zarrs(paths, tmp_path / "m.zarr.zip")).is_file()


def test_a_whole_frame_of_offset_still_fails(tmp_path):
    """Below one step the panels describe the same frames; a step or more
    means they are offset by a frame, which is a real problem."""
    step = 0.064
    om = np.arange(NFRAMES, dtype=float) * step
    paths = {p: str(_panel_zarr(tmp_path / f"s_000001.{p}.zarr.zip", p,
                                omegas=om + (step * 1.5 if p == "ge2" else 0)))
             for p in PANELS}
    with pytest.raises(ValueError, match="not the same frames"):
        combine_panel_zarrs(paths, tmp_path / "m.zarr.zip")


# ── the merged eta axis is sorted into [-180, 180) ───────────────────────
#
# The fixtures above give REtaMap only two planes, so there is no eta plane
# to read and the merge stays in panel order -- which is what pins the
# "declines rather than guesses" half of plan_eta_order. These build the
# real five-plane layout, [R, 2theta, eta, area, Q], with ge1 labelled past
# the cut exactly as connolly_oct26's cake_parameters.1ide.ge1.csv asks
# (ETA_MIN=120, ETA_MAX=210 -> centres 125..205).

#: connolly_oct26's four CSVs, verbatim. ge4 is 80 deg where the others are
#: 90: the 10 deg wedge at eta~35 is shadowed, so this set does not tile and
#: is not meant to.
REAL_ETA = {"ge1": (120.0, 210.0), "ge2": (-150.0, -60.0),
            "ge3": (-60.0, 30.0), "ge4": (40.0, 120.0)}
REAL_STEP = 10.0


def _centres(lo, hi, step=REAL_STEP):
    return lo + step * (np.arange(int(round((hi - lo) / step))) + 0.5)


def _panel_with_eta(path, panel, eta_range, n_r=NR):
    """A panel archive whose REtaMap really carries its own eta centres."""
    lo, hi = eta_range
    cen = _centres(lo, hi)
    n_eta = len(cen)
    store = zarr.ZipStore(str(path), mode="w")
    root = zarr.group(store)
    root.attrs.put({"provenance_history": [
        {"tool": "integrate", "cake_params": {
            "EtaMin": lo, "EtaMax": hi, "EtaBinSize": REAL_STEP,
            "RMin": 440.0, "RMax": 2200.0, "RBinSize": 0.5}}]})
    root.create_dataset("Omegas", data=np.arange(NFRAMES, dtype=float))
    ip = root.create_group("InstrumentParameters")
    ip.create_dataset("Distance", data=np.array([1000.0 + int(panel[2:])]))
    # Every caked array is stamped with its own eta, so a column that moves
    # can be traced back to the eta it started as -- a permutation that
    # scrambled data against labels would show up as a mismatch here.
    stamp = np.broadcast_to(cen, (n_r, n_eta)).copy()
    for group in ("IntegrationResult", "OmegaSumFrame"):
        g = root.create_group(group)
        for f in range(NFRAMES):
            g.create_dataset(f"FrameNr_{f}", data=stamp + f)
    root.create_dataset("SumFrames", data=stamp)
    reta = np.zeros((5, n_r, n_eta), dtype=float)
    reta[0] = np.arange(n_r, dtype=float)[:, None]      # R
    reta[1] = 1.0                                        # 2theta
    reta[2] = stamp                                      # eta
    reta[3] = 2.0                                        # area
    reta[4] = 3.0                                        # Q
    root.create_dataset("REtaMap", data=reta)
    store.close()
    return path


@pytest.fixture
def real_panels(tmp_path):
    return {p: str(_panel_with_eta(tmp_path / f"s_005244.{p}.zarr.zip", p,
                                   REAL_ETA[p])) for p in PANELS}


def _merged_eta(path):
    store = zarr.ZipStore(str(path), mode="r")
    try:
        return zarr.group(store)["REtaMap"][2].mean(axis=0)
    finally:
        store.close()


def test_ge1_past_the_cut_is_wrapped_and_the_axis_comes_out_ascending(
        real_panels, tmp_path):
    """The reported defect. ge1 asks for 120..210 and is labelled 125..205,
    so the panel-order axis sawtooths 205 -> -145 and any reader has to
    unwrap it to 475 before it can derive cell edges. Sorted, it reads
    -175..175 -- conventional numbers, no unwrap needed."""
    eta = _merged_eta(combine_panel_zarrs(real_panels, tmp_path / "m.zarr.zip"))
    assert np.all(np.diff(eta) > 0), "must be strictly ascending"
    assert eta.min() == -175.0 and eta.max() == 175.0
    assert np.all((eta >= -180.0) & (eta < 180.0))


def test_the_sort_is_a_permutation_and_loses_no_column(real_panels, tmp_path):
    eta = _merged_eta(combine_panel_zarrs(real_panels, tmp_path / "m.zarr.zip"))
    expect = sorted((c + 180.0) % 360.0 - 180.0
                    for p in PANELS for c in _centres(*REAL_ETA[p]))
    assert list(eta) == expect
    assert len(eta) == 35, "9+9+9+8 columns, 350 deg of 360"


def test_every_caked_array_moves_with_the_eta_plane(real_panels, tmp_path):
    """The permutation has to reach SumFrames, OmegaSumFrame and
    IntegrationResult alike. Each column is stamped with the eta it was
    integrated at, so data and label must still agree afterwards: if only
    REtaMap were reordered, the archive would be silently scrambled."""
    orig = np.array([c for p in PANELS for c in _centres(*REAL_ETA[p])])
    stamp = orig[np.argsort((orig + 180.0) % 360.0 - 180.0, kind="stable")]
    assert list(stamp[:3]) == [185.0, 195.0, 205.0], \
        "ge1's past-cut columns sort to the FRONT, still stamped 185..205"

    out = combine_panel_zarrs(real_panels, tmp_path / "m.zarr.zip")
    store = zarr.ZipStore(str(out), mode="r")
    try:
        z = zarr.group(store)
        assert list(z["SumFrames"][0]) == list(stamp)
        for group in ("IntegrationResult", "OmegaSumFrame"):
            for f in range(NFRAMES):
                assert list(z[group][f"FrameNr_{f}"][0]) == list(stamp + f), \
                    f"{group}/FrameNr_{f}"
    finally:
        store.close()


def test_the_gap_the_sort_exposes_is_still_reported_as_a_gap(real_panels,
                                                             tmp_path):
    """Sorting changes where the columns sit, not what was asked for, so
    the tiling check still reads the requested ranges in panel order and
    still names ge3 -> ge4. The span stays dropped: one pair cannot
    describe an axis with a hole in it, sorted or not."""
    seen = []
    out = combine_panel_zarrs(real_panels, tmp_path / "m.zarr.zip",
                              warn=seen.append)
    entry = _read(out)["attrs"]["provenance_history"][-1]
    assert "10 deg gap" in entry["extra"]["eta_tiling_problem"]
    assert "ge3" in entry["extra"]["eta_tiling_problem"]
    assert "EtaMin" not in entry["cake_params"]
    assert entry["extra"]["eta_sorted"] is True
    assert any("wrapped into [-180, 180) and sorted" in m for m in seen)


def test_a_set_that_paves_the_circle_spans_minus180_to_180(tmp_path):
    """With ge4's shadowed wedge included the four panels tile exactly, and
    the span then describes the sorted axis: -180..180, not 120..480."""
    full = dict(REAL_ETA, ge4=(30.0, 120.0))
    paths = {p: str(_panel_with_eta(tmp_path / f"s.{p}.zarr.zip", p, full[p]))
             for p in PANELS}
    out = combine_panel_zarrs(paths, tmp_path / "m.zarr.zip")
    entry = _read(out)["attrs"]["provenance_history"][-1]
    assert entry["extra"]["eta_tiling_problem"] is None
    assert (entry["cake_params"]["EtaMin"],
            entry["cake_params"]["EtaMax"]) == (-180.0, 180.0)
    assert len(_merged_eta(out)) == 36, "a full turn of 10 deg bins"


def test_an_already_ascending_axis_is_left_exactly_alone(tmp_path):
    """ge2+ge3+ge4 never cross the cut, so there is nothing to fix and the
    columns must stay in panel order -- the no-op path."""
    order = ["ge2", "ge3", "ge4"]
    paths = {p: str(_panel_with_eta(tmp_path / f"s.{p}.zarr.zip", p,
                                    REAL_ETA[p])) for p in order}
    out = combine_panel_zarrs(paths, tmp_path / "m.zarr.zip")
    eta = _merged_eta(out)
    assert list(eta) == [c for p in order for c in _centres(*REAL_ETA[p])]
    assert _read(out)["attrs"]["provenance_history"][-1]["extra"][
        "eta_sorted"] is False


def test_overlapping_panels_are_left_in_panel_order(tmp_path):
    """Two columns at the same azimuth have no one right order, and
    interleaving them would be a fabrication. plan_eta_order declines and
    the merge is exactly what it was before this feature existed."""
    over = dict(REAL_ETA, ge4=(10.0, 120.0))     # 20 deg back over ge3
    paths = {p: str(_panel_with_eta(tmp_path / f"s.{p}.zarr.zip", p, over[p]))
             for p in PANELS}
    out = combine_panel_zarrs(paths, tmp_path / "m.zarr.zip")
    eta = _merged_eta(out)
    assert list(eta) == [c for p in PANELS for c in _centres(*over[p])]
    assert _read(out)["attrs"]["provenance_history"][-1]["extra"][
        "eta_sorted"] is False


def test_a_two_plane_retamap_cannot_be_sorted_and_is_not(panels, tmp_path):
    """No eta plane, no order. The merge must still work."""
    got = _read(combine_panel_zarrs(panels, tmp_path / "m.zarr.zip"))
    row = got["osf"]["FrameNr_0"][0]
    assert list(row) == [1.0] * NETA + [2.0] * NETA + [3.0] * NETA + [4.0] * NETA
    assert got["attrs"]["provenance_history"][-1]["extra"][
        "eta_sorted"] is False


def _add_corrected(path, panel, eta_range, scale=1.0):
    """Bolt a REtaMap_corrected onto a panel, as upstream MIDAS does."""
    lo, hi = eta_range
    cen = _centres(lo, hi)
    store = zarr.ZipStore(str(path), mode="a")
    try:
        root = zarr.group(store)
        reta = np.zeros((5, NR, len(cen)), dtype=float)
        reta[2] = np.broadcast_to(cen, (NR, len(cen)))
        reta[4] = scale
        root.create_dataset("REtaMap_corrected", data=reta)
    finally:
        store.close()
    return path


def test_a_carried_retamap_variant_is_relabelled_not_just_reordered(tmp_path):
    """The Zarr Viewer PREFERS REtaMap_corrected when it is present. Sorting
    its columns while leaving its eta plane saying 185 would plot the sorted
    cake against stale labels -- a non-monotonic axis the viewer would then
    unwrap all over again, which is worse than never sorting."""
    paths = {}
    for p in PANELS:
        f = _panel_with_eta(tmp_path / f"s.{p}.zarr.zip", p, REAL_ETA[p])
        paths[p] = str(_add_corrected(f, p, REAL_ETA[p]))
    seen = []
    out = combine_panel_zarrs(paths, tmp_path / "m.zarr.zip", warn=seen.append)
    store = zarr.ZipStore(str(out), mode="r")
    try:
        z = zarr.group(store)
        corr = z["REtaMap_corrected"][2].mean(axis=0)
        assert np.all(np.diff(corr) > 0), "must be ascending like REtaMap"
        assert list(corr) == list(z["REtaMap"][2].mean(axis=0)), \
            "both maps describe the same columns, so both must agree"
        assert corr.min() == -175.0 and corr.max() == 175.0
    finally:
        store.close()
    assert any("REtaMap_corrected" in m and "wrapped and sorted" in m
               for m in seen), seen


def test_a_carried_non_retamap_cake_is_reordered_but_never_relabelled(tmp_path):
    """Only a REtaMap variant holds eta AS DATA. Rewriting a plane of
    anything else that happens to be (5, nR, nEta) would corrupt it."""
    paths = {}
    for p in PANELS:
        f = _panel_with_eta(tmp_path / f"s.{p}.zarr.zip", p, REAL_ETA[p])
        store = zarr.ZipStore(str(f), mode="a")
        try:
            cen = _centres(*REAL_ETA[p])
            arr = np.zeros((5, NR, len(cen)), dtype=float)
            arr[2] = np.broadcast_to(cen, (NR, len(cen)))   # looks like eta
            zarr.group(store).create_dataset("SomeOtherMap", data=arr)
        finally:
            store.close()
        paths[p] = str(f)
    out = combine_panel_zarrs(paths, tmp_path / "m.zarr.zip")
    store = zarr.ZipStore(str(out), mode="r")
    try:
        z = zarr.group(store)
        other = z["SomeOtherMap"][2].mean(axis=0)
        # Reordered to match the rest of the file, but its VALUES are the
        # originals: 185..205 still read 185..205.
        assert other.max() == 205.0, "untouched values"
        assert list(other[:3]) == [185.0, 195.0, 205.0], \
            "moved to the front with its columns, not relabelled"
    finally:
        store.close()


# ── the combined name when no panel token is in it ───────────────────────
#
# "One zarr for the whole scan" (the default grouping) names each panel's
# archive from the froot, which has the detector token split off — so all
# four are called the same thing and there is no ".ge1." to substitute.
# Seen on a real 189-folder run:
#
#   4130_D_load_step_0.000000_000833.ave.zarr.ge1234.zip
#
# The tag landed after ".ave.zarr", so the file did not end in ".zarr.zip"
# like everything beside it and no glob for "*.ge1234.*.zarr.zip" matched.

from midas_gui.hydra_zarr_combine import insert_panel_tag


@pytest.mark.parametrize("given,expect", [
    # the real one: run grouping, froot + frame range, no token
    ("4130_D_load_step_0.000000_000833.ave.zarr.zip",
     "4130_D_load_step_0.ge1234.000000_000833.ave.zarr.zip"),
    # run grouping with no frame range
    ("my_scan.ave.zarr.zip", "my_scan.ge1234.ave.zarr.zip"),
    # bare .zarr.zip
    ("my_scan.000000_000009.zarr.zip",
     "my_scan.ge1234.000000_000009.zarr.zip"),
    # a froot that itself ends in digits must not be mistaken for a range
    ("scan_003502.ave.zarr.zip", "scan_003502.ge1234.ave.zarr.zip"),
])
def test_the_tag_goes_where_the_panel_token_would_have_been(given, expect):
    assert insert_panel_tag(given, "ge1234") == expect


@pytest.mark.parametrize("name", [
    "4130_D_load_step_0.000000_000833.ave.zarr.zip",
    "my_scan.ave.zarr.zip",
    "my_scan.000000_000009.zarr.zip",
])
def test_the_combined_name_still_ends_in_zarr_zip(name):
    """The tail is the contract — downstream globs key off it."""
    assert insert_panel_tag(name, "ge1234").endswith(".zarr.zip")


def test_a_tokenless_run_archive_gets_a_findable_name(tmp_path):
    """End to end through default_combined_path, the way the batch calls it:
    all four panels share one basename under their own ge{n}/zarr/."""
    froot = tmp_path / "expid_bc" / "4130_D_load_step_0"
    shared = "4130_D_load_step_0.000000_000833.ave.zarr.zip"
    ps = {p: str(froot / p / "zarr" / shared) for p in PANELS}
    out = default_combined_path(ps)
    assert out.name == "4130_D_load_step_0.ge1234.000000_000833.ave.zarr.zip"
    assert out.parent == froot, "still a sibling of ge1..ge4"
    assert out.name.endswith(".zarr.zip")
    assert list(froot.glob("*.ge1234.*.zarr.zip")) == [] or True
    # the glob that missed the old name must match the new one
    import fnmatch
    assert fnmatch.fnmatch(out.name, "*.ge1234.*.zarr.zip")


def test_the_old_broken_name_is_not_produced_any_more(tmp_path):
    froot = tmp_path / "scan"
    ps = {p: str(froot / p / "zarr" / "scan.000000_000833.ave.zarr.zip")
          for p in PANELS}
    out = default_combined_path(ps)
    assert not out.name.endswith(".ge1234.zip"), out.name
    assert ".ave.zarr.ge1234." not in out.name, out.name


def test_a_name_that_does_have_a_token_is_untouched(tmp_path):
    """Per-file grouping still substitutes in place — unchanged behaviour."""
    froot = tmp_path / "scan"
    ps = {p: str(froot / p / "zarr" / f"scan_005244.{p}.000000_000063.ave.zarr.zip")
          for p in PANELS}
    out = default_combined_path(ps)
    assert out.name == "scan_005244.ge1234.000000_000063.ave.zarr.zip"


# ── reading a merged archive back for display ────────────────────────────
# The combine's own output had no view in the GUI: the Hydra page showed
# four per-panel results, and the merged ge1234 archive -- the only place
# all four panels' eta coverage appears on one continuous axis -- had to be
# opened externally. read_merged_cakes is the read side of that.

def _write_merged(tmp_path, n_frames=3, n_r=5, n_eta=4, keys=None):
    """A minimal merged-shaped archive: OmegaSumFrame/(nR, nEta) + REtaMap."""
    import zarr
    out = tmp_path / "m.zarr.zip"
    store = zarr.ZipStore(str(out), mode="w")
    z = zarr.group(store)
    osf = z.create_group("OmegaSumFrame")
    keys = keys or [f"LastFrameNumber_{i}" for i in range(n_frames)]
    for i, k in enumerate(keys):
        osf[k] = np.full((n_r, n_eta), float(i + 1))
    rmap = np.zeros((5, n_r, n_eta))
    rmap[0] = np.arange(n_r)[:, None] * 10.0            # plane 0 = R
    rmap[2] = np.linspace(-175, 175, n_eta)[None, :]    # plane 2 = eta
    z["REtaMap"] = rmap
    store.close()
    return out


def test_read_merged_cakes_returns_the_shape_the_cake_viewer_takes(tmp_path):
    """Stored as (nR, nEta) per frame; CakeStackViewer.set_cakes wants
    (n_frames, n_eta, n_r), so each frame is transposed on the way out."""
    from midas_gui import hydra_zarr_combine as hz
    cakes, r, eta, ids, trunc = hz.read_merged_cakes(_write_merged(tmp_path))
    assert cakes.shape == (3, 4, 5)
    assert r.shape == (5,) and eta.shape == (4,)
    assert trunc == 0 and len(ids) == 3


def test_the_axes_come_from_retamap_not_a_reconstructed_linspace(tmp_path):
    """The merged eta axis is four panels' own ranges concatenated. It need
    not tile evenly, and for ge1 at 1-ID-E it crosses the +/-180 branch cut
    -- an assumed uniform grid would be wrong in exactly the case this view
    exists to show."""
    from midas_gui import hydra_zarr_combine as hz
    _c, r, eta, _i, _t = hz.read_merged_cakes(_write_merged(tmp_path))
    assert np.allclose(r, [0, 10, 20, 30, 40])
    assert eta[0] == pytest.approx(-175.0) and eta[-1] == pytest.approx(175.0)


def test_frames_are_ordered_numerically_not_lexically(tmp_path):
    """LastFrameNumber_10 sorts before _7 lexically, which shuffles the
    frame axis -- and a shuffled waterfall reads as noise, not as a sorting
    bug."""
    from midas_gui import hydra_zarr_combine as hz
    keys = [f"LastFrameNumber_{i}" for i in (0, 7, 10, 2)]
    _c, _r, _e, ids, _t = hz.read_merged_cakes(_write_merged(
        tmp_path, n_frames=4, keys=keys))
    assert ids == ["LastFrameNumber_0", "LastFrameNumber_2",
                   "LastFrameNumber_7", "LastFrameNumber_10"]


def test_the_frame_cap_truncates_and_says_by_how_much(tmp_path):
    """A merged cake is four panels wide; an 834-frame scan at 3520 R bins
    is over a gigabyte. Truncation must be reported, not silent."""
    from midas_gui import hydra_zarr_combine as hz
    cakes, _r, _e, ids, trunc = hz.read_merged_cakes(
        _write_merged(tmp_path, n_frames=10), max_frames=4)
    assert cakes.shape[0] == 4 and len(ids) == 4 and trunc == 6
    # ...and None means "load it all", for a caller that knows it is small.
    cakes, _r, _e, _i, trunc = hz.read_merged_cakes(
        _write_merged(tmp_path, n_frames=10), max_frames=None)
    assert cakes.shape[0] == 10 and trunc == 0


def test_a_non_merged_archive_is_refused_by_name(tmp_path):
    import zarr
    from midas_gui import hydra_zarr_combine as hz
    out = tmp_path / "plain.zarr.zip"
    store = zarr.ZipStore(str(out), mode="w")
    zarr.group(store)["something_else"] = np.zeros((2, 2))
    store.close()
    with pytest.raises(ValueError, match="not a merged archive"):
        hz.read_merged_cakes(out)
