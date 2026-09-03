"""Tests for the provenance stamping added with the zarr/HDF5 batch outputs
(``provenance.py``) and the cake-parameters CSV reader (``cake_params.py``).

Both are metadata-only: a failure here never corrupts integration results, but
it does silently destroy the record of how a dataset was produced — which is
the entire point of the feature — so the round-trips are worth pinning.
"""
import json

import pytest

from midas_gui import cake_params, provenance


# ── build_entry ──────────────────────────────────────────────────────────────

def test_build_entry_records_the_standard_fields():
    entry = provenance.build_entry("midas_gui.test", command=["prog", "--x"])
    for key in ("tool", "utc_time", "host", "user", "cwd", "command",
                "midas_gui", "backends", "python", "zarr", "inputs"):
        assert key in entry, f"missing {key}"
    assert entry["tool"] == "midas_gui.test"
    assert entry["command"] == "prog --x"
    assert entry["inputs"] == []
    # Optional blocks stay absent unless supplied, so a reader can tell
    # "not recorded" from "recorded as empty".
    assert "cake_params" not in entry
    assert "instrument_params" not in entry
    assert "extra" not in entry


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

    from midas_gui.zarr_cake import write_cake_zarr
    path = tmp_path / "c.zarr.zip"
    write_cake_zarr(path, [np.zeros((2, 3))], r_axis_px=np.arange(3.0),
                    eta_axis_deg=np.arange(2.0), lsd_um=1e5, px_um=200.0,
                    wavelength_A=0.2,
                    provenance_entry=provenance.build_entry("write"))

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
