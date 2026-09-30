"""Tests for the provenance stamping added with the zarr/HDF5 batch outputs
(``provenance.py``) and the cake-parameters CSV reader (``cake_params.py``).

Both are metadata-only: a failure here never corrupts integration results, but
it does silently destroy the record of how a dataset was produced — which is
the entire point of the feature — so the round-trips are worth pinning.
"""
import json
import os
import sys

import pytest

from midas_gui import cake_params, provenance


# ── build_entry ──────────────────────────────────────────────────────────────

def test_build_entry_records_the_standard_fields():
    entry = provenance.build_entry("midas_gui.test", command=["prog", "--x"])
    for key in ("tool", "utc_time", "host", "user", "cwd", "command",
                "script", "script_sha256", "midas_gui", "backends", "python",
                "zarr", "inputs"):
        assert key in entry, f"missing {key}"
    assert entry["tool"] == "midas_gui.test"
    assert entry["command"] == "prog --x"
    assert entry["inputs"] == []
    # Optional blocks stay absent unless supplied, so a reader can tell
    # "not recorded" from "recorded as empty".
    assert "cake_params" not in entry
    assert "instrument_params" not in entry
    assert "extra" not in entry


def test_build_entry_records_the_running_script_and_its_checksum():
    """``script``/``script_sha256`` — parity with mpe_wf_saxs_waxs's
    provenance.py, which records the entry-point script's resolved path and
    content hash so a later reader can tell a modified/uncommitted script
    apart from the git commit recorded alongside it."""
    entry = provenance.build_entry("t")
    # sys.argv[0] under pytest resolves to a real, existing file (pytest's
    # own launcher/module), so this should hash successfully rather than
    # come back None — that only happens for a script path that doesn't
    # exist on disk (see test below).
    assert entry["script"]
    assert os.path.isfile(entry["script"])
    assert entry["script_sha256"]
    assert len(entry["script_sha256"]) == 64  # hex sha256


def test_build_entry_script_sha256_is_none_when_script_path_is_missing(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["/no/such/script.py"])
    entry = provenance.build_entry("t")
    assert entry["script"] == "/no/such/script.py"
    assert entry["script_sha256"] is None


def test_git_rev_includes_a_tag_field():
    """``tag`` — parity with mpe_wf_saxs_waxs's ``_git_rev``: the nearest
    reachable annotated tag, separate from ``describe``'s "N commits past a
    tag" form. This repo carries no tags, so it's legitimately None rather
    than absent."""
    repo_dir = os.path.dirname(os.path.dirname(os.path.abspath(provenance.__file__)))
    info = provenance._repo_info(repo_dir)
    assert info is not None
    assert "tag" in info


def test_build_entry_embeds_optional_blocks():
    entry = provenance.build_entry(
        "t", cake_params={"RMin": 10.0}, instrument_params={"Lsd": 1.0},
        extra={"n_frames": 3})
    assert entry["cake_params"] == {"RMin": 10.0}
    assert entry["instrument_params"] == {"Lsd": 1.0}
    assert entry["extra"] == {"n_frames": 3}


def test_build_entry_records_input_files_with_checksums(tmp_path):
    f = tmp_path / "in.dat"
    f.write_bytes(b"hello")
    entry = provenance.build_entry("t", inputs=[f], compute_checksums=True)
    assert len(entry["inputs"]) == 1
    meta = entry["inputs"][0]
    assert meta["size"] == 5
    # sha256("hello")
    assert meta["sha256"] == (
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824")


def test_build_entry_can_skip_checksums(tmp_path):
    """Batch inputs can be very large; hashing must be opt-out."""
    f = tmp_path / "in.dat"
    f.write_bytes(b"hello")
    meta = provenance.build_entry(
        "t", inputs=[f], compute_checksums=False)["inputs"][0]
    assert meta.get("sha256") in (None, "")


def test_build_entry_survives_a_missing_input(tmp_path):
    """A recorded input that no longer exists must not abort the run."""
    entry = provenance.build_entry("t", inputs=[tmp_path / "gone.tif"])
    assert len(entry["inputs"]) == 1


def test_build_entry_is_json_serialisable():
    """Both sinks (HDF5 attrs, zarr .zattrs) serialise to JSON."""
    entry = provenance.build_entry("t", extra={"a": 1})
    json.loads(json.dumps(entry, default=str))


# ── append_to_hdf5_attrs ─────────────────────────────────────────────────────

def test_append_to_hdf5_attrs_accumulates_a_history(tmp_path):
    h5py = pytest.importorskip("h5py")
    path = tmp_path / "out.h5"
    with h5py.File(path, "w") as f:
        provenance.append_to_hdf5_attrs(f, provenance.build_entry("first"))
        provenance.append_to_hdf5_attrs(f, provenance.build_entry("second"))
    with h5py.File(path, "r") as f:
        history = json.loads(f.attrs["provenance_history"])
    assert [e["tool"] for e in history] == ["first", "second"]


def test_stamp_h5_provenance_reopens_an_existing_file(tmp_path):
    """workers.stamp_h5_provenance appends to a file write_h5 already closed."""
    h5py = pytest.importorskip("h5py")
    from midas_gui.workers import stamp_h5_provenance

    path = tmp_path / "integrated.h5"
    with h5py.File(path, "w") as f:
        f.create_dataset("profiles", data=[[1.0, 2.0]])
    stamp_h5_provenance(path, provenance.build_entry("batch"))
    with h5py.File(path, "r") as f:
        assert "profiles" in f, "existing datasets must survive the stamp"
        assert json.loads(f.attrs["provenance_history"])[0]["tool"] == "batch"


# ── append_to_zarr_group ─────────────────────────────────────────────────────

def test_append_to_zarr_group_accumulates_a_history():
    zarr = pytest.importorskip("zarr")
    root = zarr.group(store=zarr.MemoryStore())
    provenance.append_to_zarr_group(root, provenance.build_entry("first"))
    provenance.append_to_zarr_group(root, provenance.build_entry("second"))
    assert [e["tool"] for e in root.attrs["provenance_history"]] == \
        ["first", "second"]


def test_append_to_zip_updates_an_existing_store(tmp_path):
    """The .zarr.zip path extracts / edits / repacks rather than mutating a
    live ZipStore (which would leave duplicate entries)."""
    zarr = pytest.importorskip("zarr")
    import numpy as np

    # Build the fixture store directly rather than through a cake writer —
    # this test is about append_to_zip's extract/edit/repack, not about
    # whichever module happens to produce the zip.
    path = tmp_path / "c.zarr.zip"
    store = zarr.ZipStore(str(path), mode="w")
    root = zarr.group(store=store)
    root.create_dataset("REtaMap", data=np.zeros((5, 3, 2), dtype="f4"))
    provenance.append_to_zarr_group(root, provenance.build_entry("write"))
    store.close()

    provenance.append_to_zip(path, provenance.build_entry("restamp"))

    root = zarr.open(zarr.ZipStore(str(path), mode="r"), mode="r")
    assert [e["tool"] for e in root.attrs["provenance_history"]] == \
        ["write", "restamp"]
    assert "REtaMap" in root, "repack must not drop existing arrays"


def test_append_to_zip_rejects_a_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        provenance.append_to_zip(tmp_path / "nope.zarr.zip",
                                 provenance.build_entry("t"))


# ── read_instrument_params ───────────────────────────────────────────────────

def test_read_instrument_params_parses_a_paramstest(tmp_path):
    p = tmp_path / "paramstest.txt"
    p.write_text(
        "Lsd 500000.0\n"
        "BC 50.0 60.0\n"
        "ImTransOpt 2   # trailing comment\n"
        "\n"
        "DetParams some_string\n"
    )
    out = provenance.read_instrument_params(p)
    assert out["Lsd"] == 500000.0
    assert out["BC"] == [50.0, 60.0]          # multi-value keys stay lists
    assert out["ImTransOpt"] == 2.0           # comment stripped
    assert out["DetParams"] == "some_string"  # non-numeric kept as a string


@pytest.mark.parametrize("arg", [None, "", "/definitely/not/here.txt"])
def test_read_instrument_params_returns_none_when_unusable(arg):
    assert provenance.read_instrument_params(arg) is None


def test_read_instrument_params_returns_none_for_an_empty_file(tmp_path):
    p = tmp_path / "empty.txt"
    p.write_text("# only a comment\n\n")
    assert provenance.read_instrument_params(p) is None


# ── cake_params.parse_cake_csv ───────────────────────────────────────────────

def test_parse_cake_csv_reads_the_last_data_row(tmp_path):
    p = tmp_path / "cake_parameters.csv"
    p.write_text(
        "r_min,r_max,r_step,eta_min,eta_max,eta_step\n"
        "10,100,1,-180,180,5\n"
        "20,200,2,-90,90,10\n"
    )
    out = cake_params.parse_cake_csv(str(p))
    # Header is upper-cased on read; the LAST row wins.
    assert out["R_MIN"] == 20.0 and out["R_MAX"] == 200.0
    assert out["ETA_STEP"] == 10.0
    assert set(cake_params.CAKE_KEYS) >= {"R_MIN", "R_MAX", "ETA_STEP"}


def test_parse_cake_csv_skips_non_numeric_columns(tmp_path):
    p = tmp_path / "c.csv"
    p.write_text("r_min,label\n10,some_text\n")
    out = cake_params.parse_cake_csv(str(p))
    assert out == {"R_MIN": 10.0}


@pytest.mark.parametrize("content", [
    "",                       # empty file
    "r_min,r_max\n",          # header only, no data row
    "label\nsome_text\n",     # no parseable numeric value
])
def test_parse_cake_csv_returns_none_when_unusable(tmp_path, content):
    p = tmp_path / "c.csv"
    p.write_text(content)
    assert cake_params.parse_cake_csv(str(p)) is None


def test_parse_cake_csv_returns_none_for_a_missing_path(tmp_path):
    assert cake_params.parse_cake_csv(str(tmp_path / "nope.csv")) is None
    assert cake_params.parse_cake_csv("") is None


# ── cake_params.write_cake_csv ───────────────────────────────────────────────

def test_write_cake_csv_round_trips_every_key(tmp_path):
    p = tmp_path / "cake_parameters.csv"
    values = {"R_MIN": 10.0, "R_MAX": 2032.0, "R_STEP": 1.5,
              "ETA_MIN": -180.0, "ETA_MAX": 90.0, "ETA_STEP": 5.0,
              "OME_SUM": 10.0, "OME_START": 0.25, "OME_STEP": 0.1}
    cake_params.write_cake_csv(str(p), values)
    back = cake_params.parse_cake_csv(str(p))
    assert back == values


def test_write_cake_csv_matches_the_mpe_wf_column_layout(tmp_path):
    """The compatibility assertion: a plain ``csv.DictReader`` — which is what
    mpe_wf's own tools use — sees exactly ``CAKE_KEYS``, in order, in one data
    row. Our reader is deliberately lenient (case-insensitive, last row wins);
    theirs is not, so reading it back with ours would prove nothing."""
    import csv as _csv
    p = tmp_path / "cake_parameters.20ide.s20varex2.csv"
    cake_params.write_cake_csv(str(p), {k: 1 for k in cake_params.CAKE_KEYS})
    with p.open(newline="") as f:
        reader = _csv.DictReader(f)
        rows = list(reader)
    assert tuple(reader.fieldnames) == cake_params.CAKE_KEYS
    assert len(rows) == 1


def test_write_cake_csv_defaults_missing_keys_to_zero(tmp_path):
    """Not an empty cell: mpe_wf's reader turns a blank into a ValueError and
    its editor refuses to save one, so an omitted key has to be a number."""
    p = tmp_path / "c.csv"
    cake_params.write_cake_csv(str(p), {"R_MIN": 5.0})
    assert p.read_text().splitlines()[1] == "5,0,0,0,0,0,0,0,0"
    assert cake_params.parse_cake_csv(str(p))["OME_STEP"] == 0.0


def test_write_cake_csv_writes_whole_numbers_without_a_decimal_point(tmp_path):
    """``%g``, matching the hand-written files already in circulation rather
    than decorating 1.0 into 1.000000."""
    p = tmp_path / "c.csv"
    cake_params.write_cake_csv(
        str(p), {"R_MIN": 0.0, "R_MAX": 2032.0, "R_STEP": 1.0,
                 "ETA_MIN": -180.0, "ETA_MAX": 180.0, "ETA_STEP": 5.0,
                 "OME_SUM": 10, "OME_START": 0, "OME_STEP": 0})
    assert p.read_text().splitlines()[1] == "0,2032,1,-180,180,5,10,0,0"


def test_write_cake_csv_overwrites_rather_than_appends(tmp_path):
    """``parse_cake_csv`` reads the last data row so a file *can* be a running
    log, but a config the user just saved must read back as what they saved."""
    p = tmp_path / "c.csv"
    cake_params.write_cake_csv(str(p), {"R_MIN": 1.0})
    cake_params.write_cake_csv(str(p), {"R_MIN": 2.0})
    assert len(p.read_text().strip().splitlines()) == 2
    assert cake_params.parse_cake_csv(str(p))["R_MIN"] == 2.0


# ── cake_params.omega_for_window / omega_series ──────────────────────────────

def test_omega_reproduces_mpe_wfs_own_expression_for_every_ome_sum():
    """The compatibility assertion for the whole omega feature.

    ``gui_data_explorer.py`` computes an integrated frame's angle as
    ``ome_start + (idx*ome_sum + (ome_sum-1)/2) * ome_step``. We compute the
    mean of the frame's inclusive raw window instead — one expression for
    every case, including ones mpe_wf spells out separately. The two must
    agree on the case they share, or a file caked here and a file caked there
    put the same peak at different angles."""
    start, step = 3.5, 0.2
    for ome_sum in (1, 2, 3, 5, 10):
        for idx in range(4):
            lo = idx * ome_sum
            hi = lo + ome_sum - 1
            mine = cake_params.omega_for_window(start, step, lo, hi)
            theirs = start + (idx * ome_sum + (ome_sum - 1) / 2.0) * step
            assert mine == pytest.approx(theirs), (ome_sum, idx)


def test_omega_of_a_single_raw_frame_is_the_plain_ramp():
    """``OME_SUM = 1``: window ``(k, k)``, so no averaging term at all."""
    for k in range(5):
        assert cake_params.omega_for_window(10.0, 0.5, k, k) == \
            pytest.approx(10.0 + 0.5 * k)


def test_omega_of_a_collapsed_run_is_the_mean_of_the_window():
    """``OME_SUM = 0`` — this app's "combine everything selected into one
    frame". The single output frame covers raw 0…N-1, and the angle it should
    be filed under is the middle of the exposure, not its start."""
    n = 11
    got = cake_params.omega_for_window(0.0, 1.0, 0, n - 1)
    assert got == pytest.approx((n - 1) / 2.0)


def test_omega_of_an_unconfigured_run_is_a_genuine_zero():
    """Both keys at their default 0 is a *stationary sample at ω = 0*, not a
    sentinel for "unset" — deliberately, because the alternative the zarr
    writer used to fall back to was the frame index, and an index labelled
    "Degrees" is a far worse wrong answer than 0.0."""
    for lo, hi in ((0, 0), (7, 7), (0, 999)):
        assert cake_params.omega_for_window(0.0, 0.0, lo, hi) == 0.0


def test_omega_handles_a_reverse_scan_without_special_casing():
    """A negative ``OME_STEP`` is a scan running backwards; the same
    arithmetic has to produce a descending series, not an absolute value."""
    series = cake_params.omega_series(90.0, -0.25, [(k, k) for k in range(4)])
    assert series == pytest.approx([90.0, 89.75, 89.5, 89.25])


def test_omega_series_maps_each_window_independently():
    windows = [(0, 1), (2, 3), (4, 5)]
    assert cake_params.omega_series(1.0, 2.0, windows) == \
        pytest.approx([2.0, 6.0, 10.0])


def test_omega_series_collapse_gives_every_frame_the_run_wide_mean():
    """The "these images were averaged or summed" override: the per-frame
    windows describe a ramp the pixels no longer have, so all of them collapse
    onto the union window's single angle."""
    windows = [(0, 1), (2, 3), (4, 5)]
    got = cake_params.omega_series(1.0, 2.0, windows, collapse=True)
    assert got == pytest.approx([6.0, 6.0, 6.0])       # mean of 0…5 = 2.5
    assert len(set(got)) == 1


def test_omega_series_collapse_is_a_noop_when_the_loader_already_collapsed():
    """``OME_SUM = 0`` produces one window spanning the run, so ticking the
    override on top of it must not change the answer — the two routes to "one
    frame for everything" have to agree."""
    plain = cake_params.omega_series(1.0, 2.0, [(0, 5)])
    forced = cake_params.omega_series(1.0, 2.0, [(0, 5)], collapse=True)
    assert plain == pytest.approx(forced)


def test_omega_series_of_no_frames_is_empty_rather_than_raising():
    """The collapse branch takes a ``min``/``max`` over the windows; an
    aborted run reaching it with nothing must not raise."""
    assert cake_params.omega_series(1.0, 2.0, []) == []
    assert cake_params.omega_series(1.0, 2.0, [], collapse=True) == []
