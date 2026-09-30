"""2D CSV (the whole-cake file) — written whether or not multi-azimuth is on.

``2d_csv`` is not a lineout: it is the (η, R) cake itself, one file per output
frame. That makes it the odd one out among the text formats, and it is why it
was broken — ``cake_2d is not None`` meant both "a cake exists" and "fan out
one lineout per η bin", so the Batch write site, which only wanted the second
thing under the Multi-azimuth checkbox, withheld the cake entirely when that
box was off. ``write_profile`` then did nothing, quietly, while the caller
added ``<stem>.2d_csv`` to the reported paths — a file that was never written
and whose name was wrong anyway (the real one is ``<stem>_cake.csv``).

So the tests that matter are about the checkbox-off case and about the
reported paths being the files on disk; the fan-out case is here to pin that
nothing about it changed.
"""
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest


def _tiny_calib_result(**overrides):
    fields = dict(
        Lsd=200000.0, BC_y=32.0, BC_z=32.0, tx=0.0, ty=0.0, tz=0.0,
        distortion={}, pxY=200.0, pxZ=200.0,
        NrPixelsY=64, NrPixelsZ=64, wavelength_A=0.1729,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _make_tiff_frames(tmp_path, n=2, size=64):
    tifffile = pytest.importorskip("tifffile")
    rng = np.random.default_rng(0)
    paths = []
    for i in range(n):
        p = tmp_path / f"frame_{i:04d}.tif"
        tifffile.imwrite(str(p),
                         (rng.random((size, size)) * 100 + 10).astype(np.float32))
        paths.append(str(p))
    return paths


@pytest.fixture(scope="module")
def app():
    from PyQt5 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def in_dir(tmp_path):
    (tmp_path / "in").mkdir()
    return tmp_path


ETA_BIN = 45.0          # → 8 η bins over -180…180
N_ETA = 8


def _run(app, tmp_path, fmts, *, multi_azimuth=False, n_frames=2, out_dir=None):
    pytest.importorskip("torch")
    pytest.importorskip("midas_integrate_v2")
    import midas_gui.workers as wk
    from midas_gui.helpers import _build_spec

    paths = _make_tiff_frames(tmp_path / "in", n=n_frames)
    spec = _build_spec(_tiny_calib_result(), r_bin=2.0, eta_bin=ETA_BIN)
    worker = wk.BatchWorker(
        spec, {"type": "tiff_list", "paths": paths}, None, out_dir, fmts,
        "subpixel2", (None, None), None, multi_azimuth=multi_azimuth)
    results, failures, logs = {}, [], []
    worker.finished.connect(results.update)
    worker.failed.connect(failures.append)
    worker.log_line.connect(logs.append)
    worker.run()   # direct call, not .start() — no real QThread spawned
    assert not failures, failures[0]
    return results, logs


def _read_cake_csv(path):
    """``(header_r_values, eta_column, intensity_rows)``."""
    lines = path.read_text().strip().splitlines()
    head = lines[0].split(",")
    assert head[0] == "eta\\R(px)"
    r_vals = [float(x) for x in head[1:]]
    etas, rows = [], []
    for ln in lines[1:]:
        parts = ln.split(",")
        etas.append(float(parts[0]))
        rows.append([float(x) for x in parts[1:]])
    return r_vals, etas, rows


# ── the bug: multi-azimuth off ───────────────────────────────────────────────

def test_2d_csv_is_written_with_multi_azimuth_off(app, in_dir):
    """The headline case. Ticking 2D CSV and nothing else used to produce an
    empty ``2d_csv/`` folder — the cake was computed (``want_cake`` includes
    this format) and then discarded before the writer saw it."""
    out = in_dir / "out"
    _run(app, in_dir, ["2d_csv"], multi_azimuth=False, n_frames=2, out_dir=out)

    got = sorted(p.name for p in (out / "2d_csv").iterdir())
    # Output stems follow the run-wide ``<froot>_<NNNNNN>`` convention
    # (frame_output_stem), not the input filename verbatim.
    assert got == ["frame_000000_cake.csv", "frame_000001_cake.csv"]


def test_the_2d_csv_written_without_multi_azimuth_is_a_full_cake(app, in_dir):
    """Not a degenerate one-row file: with the cake withheld the only way to
    write anything at all would have been to dump the η-collapsed profile, so
    pin that every η bin is present and that the header is the R axis."""
    out = in_dir / "out"
    res, _ = _run(app, in_dir, ["2d_csv"], multi_azimuth=False, n_frames=1,
                  out_dir=out)

    r_vals, etas, rows = _read_cake_csv(out / "2d_csv" / "frame_000000_cake.csv")
    assert len(rows) == N_ETA
    assert all(len(row) == len(r_vals) for row in rows)
    assert len(r_vals) == len(res["r_axis_px"])
    assert r_vals == pytest.approx(list(np.asarray(res["r_axis_px"], float)),
                                   abs=1e-4)
    # η bin centres, not bin indices — the axis has to survive the non-fan-out
    # path too, which is where it used to be dropped.
    assert etas == pytest.approx(
        [-180.0 + ETA_BIN * (k + 0.5) for k in range(N_ETA)], abs=1e-4)


def test_reported_paths_name_the_file_that_exists(app, in_dir):
    """The other half of the defect: ``out_paths`` claimed ``<stem>.2d_csv``.
    Anything downstream that opens what the run says it wrote — the Save
    dialog's summary, a provenance stamp, a user — got a name that never
    existed under either behaviour."""
    out = in_dir / "out"
    res, _ = _run(app, in_dir, ["2d_csv"], multi_azimuth=False, n_frames=2,
                  out_dir=out)

    reported = [p for p in res["out_paths"] if p.endswith(".csv")]
    assert sorted(reported) == sorted(
        str(out / "2d_csv" / f"frame_{i:06d}_cake.csv") for i in range(2))
    for p in res["out_paths"]:
        assert Path(p).exists(), p


def test_2d_csv_alongside_a_lineout_format_writes_both(app, in_dir):
    """Each format gets its own subfolder, and the cake's presence must not
    change what the 1-D formats write."""
    out = in_dir / "out"
    _run(app, in_dir, ["csv", "2d_csv"], multi_azimuth=False, n_frames=1,
         out_dir=out)

    assert (out / "csv" / "frame_000000.csv").exists()
    assert (out / "2d_csv" / "frame_000000_cake.csv").exists()
    # The 1-D file is the collapsed profile: one row per R, not per η.
    assert (out / "csv" / "frame_000000.csv").read_text().strip().count("\n") \
        != N_ETA


# ── multi-azimuth on: unchanged ──────────────────────────────────────────────

def test_multi_azimuth_still_fans_out_and_still_writes_one_cake(app, in_dir):
    """The mode that always worked. One ``_cake.csv`` per frame plus the per-η
    lineouts for the *other* formats — the cake is not fanned out, being a
    picture of all the η bins at once."""
    out = in_dir / "out"
    _run(app, in_dir, ["csv", "2d_csv"], multi_azimuth=True, n_frames=1,
         out_dir=out)

    assert sorted(p.name for p in (out / "2d_csv").iterdir()) == \
        ["frame_000000_cake.csv"]
    assert sorted(p.name for p in (out / "csv").iterdir()) == \
        [f"frame_000000_eta{k:03d}.csv" for k in range(N_ETA)]


def test_both_modes_write_the_same_cake(app, in_dir):
    """The two paths reach ``write_profile`` differently now (``per_eta``
    branches before it), so pin that they produce identical bytes — the
    checkbox controls fan-out, and must not quietly change the cake."""
    off, on = in_dir / "off", in_dir / "on"
    _run(app, in_dir, ["2d_csv"], multi_azimuth=False, n_frames=1, out_dir=off)
    _run(app, in_dir, ["2d_csv"], multi_azimuth=True, n_frames=1, out_dir=on)

    assert (off / "2d_csv" / "frame_000000_cake.csv").read_text() == \
        (on / "2d_csv" / "frame_000000_cake.csv").read_text()


# ── the writer's own contract ────────────────────────────────────────────────

def test_write_profile_refuses_2d_csv_without_a_cake(tmp_path):
    """Formerly an ``elif`` that fell through to nothing. A caller that has no
    cake to give is a caller bug, and the whole point of this fix is that such
    a bug stops being invisible."""
    pytest.importorskip("midas_integrate_v2")
    import midas_gui.workers as wk
    r = np.linspace(1.0, 10.0, 5)
    with pytest.raises(ValueError, match="cake"):
        wk.write_profile(tmp_path / "x", "2d_csv", r, np.ones(5), np.ones(5),
                         200000.0, 200.0, 0.2, cake_2d=None)


def test_write_frame_profiles_skips_2d_csv_when_there_is_no_cake(tmp_path):
    """The one legitimate cake-less caller is the Save button's in-memory path
    for a 1-D run (``write_all_profiles``, which excludes the format and logs
    a note). It must skip rather than raise — and must not report a path."""
    pytest.importorskip("midas_integrate_v2")
    import midas_gui.workers as wk
    r = np.linspace(1.0, 10.0, 5)
    paths = wk.write_frame_profiles(tmp_path / "x", ["csv", "2d_csv"], r,
                                    np.ones(5), np.ones(5), 200000.0, 200.0,
                                    0.2, cake_2d=None, per_eta=False)
    assert paths == [str(tmp_path / "x") + ".csv"]
    assert not (tmp_path / "x_cake.csv").exists()


def test_per_eta_defaults_to_the_old_cake_means_fan_out_rule(tmp_path):
    """Every pre-existing caller passes no ``per_eta``. Those callers supplied
    a cake only when they wanted fan-out, so the default has to reproduce
    exactly that, or this fix silently changes an unrelated output."""
    pytest.importorskip("midas_integrate_v2")
    import midas_gui.workers as wk
    r = np.linspace(1.0, 10.0, 5)
    cake = np.ones((3, 5))
    paths = wk.write_frame_profiles(tmp_path / "y", ["csv"], r, np.ones(5),
                                    np.ones(5), 200000.0, 200.0, 0.2,
                                    cake_2d=cake, cake_sigma=cake)
    assert [Path(p).name for p in paths] == \
        [f"y_eta{k:03d}.csv" for k in range(3)]
