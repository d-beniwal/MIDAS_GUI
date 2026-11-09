"""GSAS-II peak-fit CSV parsing and reduction.

Plain pytest — ``midas_gui.peak_fit_data`` imports no Qt, which is the whole
point of splitting it out of ``peak_fit_panel``.

``parse_peak_fit_csv`` has been in this tree since the peak-fit port with no
coverage at all; the `PeakFitPanel` that consumes it has none either. These
are the first tests either has had, and they exist because the Data Explorer
port makes this function load-bearing for a second consumer.
"""
from __future__ import annotations

import numpy as np
import pytest


_HEADER = "name,Use,pos1,int1,sig1,gam1,Rwp,GOF"


def _row(img, omega, eta, pos, inten=100.0, use="True", rwp=2.5, prefix=""):
    return (f"{prefix}img={img} omega={omega} eta={eta},{use},"
            f"{pos},{inten},0.1,0.2,{rwp},1.1")


def _csv(tmp_path, *rows, header=_HEADER, name="fit.csv"):
    p = tmp_path / name
    p.write_text("\n".join([header, *rows]) + "\n")
    return str(p)


# ── parsing ─────────────────────────────────────────────────────────────

def test_parse_pulls_img_omega_eta_out_of_the_name_column(tmp_path):
    from midas_gui.peak_fit_data import parse_peak_fit_csv
    c = parse_peak_fit_csv(_csv(tmp_path,
                                _row(0, 0.0, -90.0, 5.1),
                                _row(1, 1.0, 0.0, 5.2),
                                _row(2, 2.0, 90.0, 5.3)))
    assert c is not None
    assert np.allclose(c["img_idx"], [0, 1, 2])
    assert np.allclose(c["omega"], [0.0, 1.0, 2.0])
    assert np.allclose(c["eta"], [-90.0, 0.0, 90.0])
    assert np.allclose(c["pos1"], [5.1, 5.2, 5.3])
    assert c["peak_indices"] == [1]


def test_parse_returns_none_rather_than_raising(tmp_path):
    from midas_gui.peak_fit_data import parse_peak_fit_csv
    assert parse_peak_fit_csv("") is None
    assert parse_peak_fit_csv(str(tmp_path / "nope.csv")) is None
    assert parse_peak_fit_csv(_csv(tmp_path, header=_HEADER)) is None, "header only"


def test_a_leading_filename_in_the_name_column_is_kept(tmp_path):
    """Spanned runs prefix the name with the source file so rows can be
    matched back to the file they came from."""
    from midas_gui.peak_fit_data import parse_peak_fit_csv
    c = parse_peak_fit_csv(_csv(
        tmp_path, _row(0, 0.0, 0.0, 5.0, prefix="scan_010128.vrx.zarr.zip ")))
    assert c is not None and np.allclose(c["img_idx"], [0])
    assert "file_num" in c and np.allclose(c["file_num"], [10128])


# ── reducers ────────────────────────────────────────────────────────────

def test_per_frame_averages_the_eta_rows_of_each_frame(tmp_path):
    from midas_gui.peak_fit_data import parse_peak_fit_csv, peak_fit_per_frame
    c = parse_peak_fit_csv(_csv(tmp_path,
                                _row(0, 0.0, -45.0, 4.0),
                                _row(0, 0.0, 45.0, 6.0),
                                _row(1, 1.0, 0.0, 10.0)))
    out = peak_fit_per_frame(c, 1, "pos", "mean", 3)
    assert np.allclose(out[:2], [5.0, 10.0])
    assert np.isnan(out[2]), "a frame with no row must be NaN, not 0"


def test_rows_marked_unused_are_excluded(tmp_path):
    from midas_gui.peak_fit_data import parse_peak_fit_csv, peak_fit_per_frame
    c = parse_peak_fit_csv(_csv(tmp_path,
                                _row(0, 0.0, -45.0, 4.0),
                                _row(0, 0.0, 45.0, 999.0, use="False")))
    assert np.allclose(peak_fit_per_frame(c, 1, "pos", "mean", 1), [4.0])


def test_the_use_column_is_spelled_with_a_capital_U(tmp_path):
    """Pinned because getting it wrong fails OPEN, not closed: the parser
    does ``row.get('Use', 'True')``, so a CSV whose column is lowercase
    ``use`` has every row silently treated as used — excluded fits would
    be averaged back into the result with no error anywhere.

    Verbatim from the source; this test documents the behaviour rather
    than changing it. If a real GSAS-II export turns out to write
    lowercase, make the lookup case-insensitive and delete this test.
    """
    from midas_gui.peak_fit_data import parse_peak_fit_csv, peak_fit_per_frame
    lower = _csv(tmp_path, _row(0, 0.0, -45.0, 4.0),
                 _row(0, 0.0, 45.0, 996.0, use="False"),
                 header="name,use,pos1,int1,sig1,gam1,Rwp,GOF", name="lower.csv")
    c = parse_peak_fit_csv(lower)
    assert peak_fit_per_frame(c, 1, "pos", "mean", 1)[0] == pytest.approx(500.0), (
        "lowercase 'use' is ignored — both rows averaged")


def test_a_missing_column_reduces_to_none_not_an_exception(tmp_path):
    from midas_gui.peak_fit_data import parse_peak_fit_csv, peak_fit_per_frame
    c = parse_peak_fit_csv(_csv(tmp_path, _row(0, 0.0, 0.0, 5.0)))
    assert peak_fit_per_frame(c, 9, "pos", "mean", 1) is None, "no peak 9"


def test_the_omega_eta_grid_places_each_row_in_its_cell(tmp_path):
    from midas_gui.peak_fit_data import parse_peak_fit_csv, peak_fit_omega_eta_grid
    c = parse_peak_fit_csv(_csv(tmp_path,
                                _row(0, 0.0, -90.0, 1.0),
                                _row(1, 10.0, -90.0, 2.0),
                                _row(2, 0.0, 90.0, 3.0)))
    grid = peak_fit_omega_eta_grid(c, 1, "pos",
                                   np.array([0.0, 10.0]), np.array([-90.0, 90.0]))
    assert grid.shape == (2, 2)
    assert grid[0, 0] == pytest.approx(1.0)
    assert grid[1, 0] == pytest.approx(2.0)
    assert grid[0, 1] == pytest.approx(3.0)
    assert np.isnan(grid[1, 1]), "a cell with no row must stay NaN"


def test_eta_choices_bins_the_distinct_angles(tmp_path):
    from midas_gui.peak_fit_data import parse_peak_fit_csv, peak_fit_eta_choices
    c = parse_peak_fit_csv(_csv(tmp_path,
                                _row(0, 0.0, -90.0, 1.0),
                                _row(1, 0.0, -89.9, 1.0),   # same bin
                                _row(2, 0.0, 90.0, 1.0)))
    etas = peak_fit_eta_choices(c, bin_deg=0.5)
    assert len(etas) == 2, f"near-identical etas should collapse: {etas}"
    assert etas[0] == pytest.approx(-90.0, abs=0.1)
    assert etas[-1] == pytest.approx(90.0, abs=0.1)


def test_per_frame_at_eta_selects_within_tolerance(tmp_path):
    from midas_gui.peak_fit_data import parse_peak_fit_csv, peak_fit_per_frame_at_eta
    c = parse_peak_fit_csv(_csv(tmp_path,
                                _row(0, 0.0, -90.0, 4.0),
                                _row(0, 0.0, 0.0, 7.0)))
    out = peak_fit_per_frame_at_eta(c, 1, "pos", 0.0, 1, atol_deg=0.5)
    assert out[0] == pytest.approx(7.0), "picked the wrong eta row"


# ── multi-file exports ──────────────────────────────────────────────────
# GSAS-II numbers img= from 0 in every file, so a 13-file CSV has thirteen
# frames called 0. Both of these were found on real beamline data, where a
# 13 x 64 raster rendered as a single coloured row with the other twelve
# blank and no error anywhere.

def test_the_file_regex_survives_this_projects_own_archive_names(tmp_path):
    """The source's pattern allowed ONE extension segment before the
    container. This project writes two -- `.ge1234` AND the combine-op
    `.ave` -- so its own output failed to parse and every row looked like
    it came from an unknown file."""
    from midas_gui.peak_fit_data import _PEAK_FIT_FILE_RX as RX
    for name, want in [
            ("4130_D_load_step_4_005180.ge1234.ave.zarr.zip", "005180"),
            ("4130_D_load_step_4_005180.ge1234.median.zarr.zip", "005180"),
            ("4130_D_load_step_4_005180.ge1.zarr.zip", "005180"),
            ("scan_010128.vrx.zarr.zip", "010128"),
            ("scan_010128.h5", "010128")]:
        m = RX.match(name)
        assert m and m.group("num") == want, f"{name} -> {m and m.group('num')}"


def _multi_file_csv(tmp_path, files=3, frames=4, etas=2):
    rows = []
    for fi in range(files):
        for im in range(frames):
            for e in range(etas):
                nm = (f"scan_{5180+fi:06d}.ge1234.ave.zarr.zip "
                      f"img={im} omega=999.0 eta={-175.0 + e*10:.1f}")
                rows.append(f"{nm},True,{10.0 + fi},100,0.1,0.2,2.5,1.1")
    p = tmp_path / "multi.csv"
    p.write_text(_HEADER + "\n" + "\n".join(rows) + "\n")
    return str(p), files * frames


def test_frames_are_numbered_across_files_not_within_one(tmp_path):
    from midas_gui.peak_fit_data import parse_peak_fit_csv
    path, total = _multi_file_csv(tmp_path)
    c = parse_peak_fit_csv(path)
    assert np.unique(c["file_num"]).size == 3
    g = c["global_idx"]
    assert g.min() == 0 and g.max() == total - 1
    assert np.unique(g).size == total, "frames from different files collided"


def test_a_per_frame_reduction_covers_every_file(tmp_path):
    """The symptom: with img_idx restarting per file, only the first
    file's frames got values and the rest of the scan came back NaN."""
    from midas_gui.peak_fit_data import parse_peak_fit_csv, peak_fit_per_frame
    path, total = _multi_file_csv(tmp_path)
    c = parse_peak_fit_csv(path)
    out = peak_fit_per_frame(c, 1, "pos", "mean", total)
    assert np.all(np.isfinite(out)), (
        f"only {int(np.sum(np.isfinite(out)))}/{total} frames got a value")
    # pos was written as 10+file_index, so each file's block must differ
    assert np.allclose(out[:4], 10.0) and np.allclose(out[4:8], 11.0)


def test_a_single_file_csv_is_unchanged(tmp_path):
    """global_idx must be a no-op for the single-file case, or the Texture
    tab's peak-fit panel changes behaviour for no reason."""
    from midas_gui.peak_fit_data import parse_peak_fit_csv
    c = parse_peak_fit_csv(_csv(tmp_path,
                                _row(0, 0.0, 0.0, 5.0),
                                _row(1, 1.0, 0.0, 6.0)))
    assert np.array_equal(c["global_idx"], c["img_idx"])
