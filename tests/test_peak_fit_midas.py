"""MIDAS-solver single-peak fitting, and the CSV it writes.

Plain pytest -- ``peak_fit_midas`` and ``peak_fit_cli`` import no Qt, and
keeping them that way is the point of the split. No forked marker, no
``QApplication``, no cap on the number of tests.

The two tests that matter most are not the round-trip ones:

  * ``test_the_profile_reproduces_a_published_gsas_ii_peak_height`` pins
    the UNIT CONVENTION against a real GSAS-II result. Get centidegrees
    wrong anywhere and every fitted width is out by 10^4 while every fit
    still converges and still looks fine.
  * ``test_the_written_csv_is_read_back_by_the_gsas_ii_parser`` closes the
    loop with ``peak_fit_data``. The whole reason for this format is that
    the existing readers take it unchanged.
"""
from __future__ import annotations

import numpy as np
import pytest

from midas_gui import peak_fit_midas as pf


# ── the profile and its units ───────────────────────────────────────────

def test_the_profile_has_unit_area_in_centidegrees():
    """``int`` is an integrated intensity, which is only true if the
    profile integrates to 1 over centidegrees."""
    dx = np.linspace(-400.0, 400.0, 200001)
    y = pf.pseudo_voigt(dx, 0.41, 0.57)
    assert np.trapz(y, dx) == pytest.approx(1.0, rel=2e-3)


def test_tch_mixing_degenerates_to_the_pure_gaussian_and_lorentzian():
    """The two limits are the only part of TCH that has a closed form, so
    they are the only part that can be checked rather than merely
    reproduced."""
    g_fwhm, eta = pf.tch_fwhm_eta(sig=(2.0 / 2.3548) ** 2, gam=1e-9)
    assert g_fwhm == pytest.approx(2.0, rel=1e-4)
    assert eta == pytest.approx(0.0, abs=1e-6)
    l_fwhm, eta = pf.tch_fwhm_eta(sig=1e-12, gam=3.0)
    assert l_fwhm == pytest.approx(3.0, rel=1e-3)
    assert eta == pytest.approx(1.0, abs=2e-3)


def test_the_profile_reproduces_a_published_gsas_ii_peak_height():
    """Row 0 of a real GSAS-II sequential fit
    (connolly_oct26_4130_D_load_step_4, eta=-175, img=0) against the
    measured lineout height at that point, 513.2 counts.

    This is the test that pins ``sig`` as a Gaussian VARIANCE in
    centidegrees**2 and ``gam`` as a Lorentzian FWHM in centidegrees. A
    wrong unit here converges happily and reports nonsense.
    """
    inten, pos, sig, gam, back = (1114.0212444279973, 7.523254898165509,
                                  0.4086949431179158, 0.5726541654084414,
                                  8.962003209081761)
    height = back + inten * pf.pseudo_voigt(np.array([0.0]), sig, gam)[0]
    assert height == pytest.approx(513.2, rel=0.02)

    fwhm, _eta = pf.tch_fwhm_eta(sig, gam)
    assert fwhm / pf.CD == pytest.approx(0.0183, rel=0.05)   # degrees


def test_background_basis_runs_minus_one_to_plus_one():
    t = pf.background_basis(np.linspace(7.4, 7.6, 11))
    assert t[0] == pytest.approx(-1.0) and t[-1] == pytest.approx(1.0)
    assert t[5] == pytest.approx(0.0)


# ── seeding ─────────────────────────────────────────────────────────────

def _one(x, pos=7.523, inten=1100.0, sig=0.41, gam=0.57, b0=9.0, b1=0.6):
    return pf.model(np.array([[pos, inten, sig, gam, b0, b1]]), x)[0]


def test_the_width_seed_is_not_swamped_by_the_window():
    """Regression. The seed used the second moment of the
    background-subtracted lineout; in a window seven FWHM wide that is a
    property of the window, and it came out 11x too wide. The solver then
    converged -- rc=0 -- to a local minimum with 0.04 deg of position
    scatter, which is the kind of failure that gets believed.
    """
    x = np.arange(7.458, 7.589, 0.0023634)
    s = pf.seed_params(x, _one(x)[None, :])[0]
    fwhm, _e = pf.tch_fwhm_eta(s[2], s[3])
    assert 1.0 < fwhm < 4.0, f"seeded FWHM {fwhm} cd, truth is ~1.83"
    assert s[0] == pytest.approx(7.523, abs=0.003)
    # The background seed reads ~3 counts HIGH and cannot do better: at a
    # window of +-0.065 deg the peak's own Lorentzian tail is still a few
    # counts at the edge, so there is no peak-free point to measure. The
    # fit absorbs it -- hence a loose bound here and a tight one on the
    # fitted value in test_the_other_parameters_are_recovered_too.
    assert s[4] == pytest.approx(9.0, abs=4.5)


def test_bounds_are_seed_relative_for_width_and_intensity():
    """Also a regression: fixed decade-wide boxes are not just loose. The
    sigmoid reparam scales the solver's step by the box width, so an
    absurd box makes every step in that parameter absurd too."""
    x = np.arange(7.458, 7.589, 0.0023634)
    s = pf.seed_params(x, _one(x)[None, :])
    lo, hi, s2 = pf.seed_bounds(x, s)
    assert hi[0, 2] / s[0, 2] == pytest.approx(30.0, rel=1e-6)
    assert hi[0, 3] / s[0, 3] == pytest.approx(30.0, rel=1e-6)
    assert lo[0, 1] == 0.0
    assert np.all(s2 > lo) and np.all(s2 < hi), "a seed sits on its bound"


def test_a_flat_lineout_seeds_without_nan():
    """Empty eta wedges exist; one NaN seed poisons its whole batch."""
    x = np.arange(7.458, 7.589, 0.0023634)
    s = pf.seed_params(x, np.zeros((1, x.size)))
    assert np.all(np.isfinite(s))


# ── fitting ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def roundtrip():
    pytest.importorskip("torch")
    pytest.importorskip("midas_peakfit.lm_generic")
    rng = np.random.default_rng(0)
    x = np.arange(7.458, 7.589, 0.0023634)
    B = 48
    truth = np.empty((B, 6))
    truth[:, 0] = 7.5230 + rng.normal(0, 0.004, B)
    truth[:, 1] = 1100 + rng.normal(0, 60, B)
    truth[:, 2] = 0.41 + rng.normal(0, 0.03, B)
    truth[:, 3] = 0.57 + rng.normal(0, 0.04, B)
    truth[:, 4] = 9.0 + rng.normal(0, 0.5, B)
    truth[:, 5] = 0.6 + rng.normal(0, 0.2, B)
    Y = rng.poisson(np.clip(pf.model(truth, x), 0, None)).astype(float)
    return x, truth, Y, pf.fit_lineouts(x, Y, max_iter=200)


def test_every_lineout_converges(roundtrip):
    _x, _t, _Y, r = roundtrip
    assert (r.rc == 0).all(), f"rc values {np.unique(r.rc)}"


def test_position_is_recovered_to_a_fraction_of_a_millidegree(roundtrip):
    """The scale that matters: the 2theta structure being chased in this
    data is ~10 mdeg, so the fit has to be an order of magnitude inside
    that before a map of it means anything."""
    _x, truth, _Y, r = roundtrip
    d = r.params[:, 0] - truth[:, 0]
    assert abs(d.mean()) < 2e-4, f"bias {d.mean()*1000:.3f} mdeg"
    assert d.std() < 5e-4, f"scatter {d.std()*1000:.3f} mdeg"


def test_the_other_parameters_are_recovered_too(roundtrip):
    _x, truth, _Y, r = roundtrip
    for k, tol in ((1, 0.06), (2, 0.25), (3, 0.35)):
        rel = (r.params[:, k] - truth[:, k]) / truth[:, k]
        assert abs(rel.mean()) < tol, f"{pf.PARAM_NAMES[k]} bias {rel.mean()}"


def test_the_esds_match_the_actual_scatter(roundtrip):
    """An esd is only worth writing to a CSV if it predicts the spread.
    Within a factor of two of the realised scatter, on synthetic Poisson
    data where the spread is known."""
    _x, truth, _Y, r = roundtrip
    for k in (0, 1, 2, 3):
        obs = (r.params[:, k] - truth[:, k]).std()
        est = np.nanmean(r.esds[:, k])
        assert 0.5 < obs / est < 2.0, f"{pf.PARAM_NAMES[k]}: {obs:g} vs {est:g}"


def test_gof_is_about_one_on_poisson_data(roundtrip):
    """Which is what validates the weighting AND the degrees of freedom
    together -- the dof is the easy half to get wrong."""
    _x, _t, _Y, r = roundtrip
    assert 0.85 < r.gof.mean() < 1.2, r.gof.mean()


def test_too_few_points_is_an_error_not_a_silent_fit():
    with pytest.raises(ValueError, match="too few"):
        pf.fit_lineouts(np.linspace(7.5, 7.6, 7), np.ones((1, 7)))


# ── the CSV ─────────────────────────────────────────────────────────────

def test_row_name_matches_what_the_parser_looks_for():
    from midas_gui.peak_fit_data import _PEAK_FIT_NAME_RX, _PEAK_FIT_FILE_RX
    n = pf.row_name("4130_D_load_step_4_005180.ge1234.ave.zarr.zip",
                    7, 1.25, -175.0)
    m = _PEAK_FIT_NAME_RX.search(n)
    assert m and int(m.group("img")) == 7
    assert float(m.group("eta")) == -175.0
    f = _PEAK_FIT_FILE_RX.match(n)
    assert f and int(f.group("num")) == 5180, "the file number did not parse"


def test_the_written_csv_is_read_back_by_the_gsas_ii_parser(tmp_path):
    """The point of the whole format choice."""
    from midas_gui.peak_fit_data import parse_peak_fit_csv
    rows = [dict(name=pf.row_name("scan_000012.ge1234.ave.zarr.zip", i, 1.0,
                                  e),
                 use=True, rwp=3.0 + i, gof=0.4,
                 params=np.array([7.52 + 1e-4 * i, 1100.0, 0.41, 0.57,
                                  9.0, 0.6]),
                 esds=np.array([1e-5, 5.0, 0.01, 0.01, 0.2, 0.2]))
            for i in range(3) for e in (-175.0, -165.0)]
    out = tmp_path / "fit.csv"
    pf.write_peak_fit_csv(str(out), rows, meta={"center_deg": 7.52})

    d = parse_peak_fit_csv(str(out))
    assert d is not None
    assert d["peak_indices"] == [0]
    assert len(d["eta"]) == 6
    assert np.allclose(np.unique(d["eta"]), [-175.0, -165.0])
    assert np.allclose(np.unique(d["img_idx"]), [0, 1, 2])
    assert np.allclose(np.unique(d["file_num"]), [12])
    assert d["pos0"][0] == pytest.approx(7.52)
    assert d["sig0"][0] == pytest.approx(0.41)
    assert d["gam0"][0] == pytest.approx(0.57)


def test_a_sidecar_records_what_the_csv_format_cannot(tmp_path):
    """Fit window and weighting are not columns, and without them two
    peak-fit CSVs cannot honestly be compared -- which is the one thing
    this file exists to make possible."""
    import json
    out = tmp_path / "fit.csv"
    pf.write_peak_fit_csv(str(out), [], meta={"fit_window_deg": [7.4, 7.6],
                                              "weights": "Poisson"})
    side = tmp_path / "fit.meta.json"
    assert side.is_file()
    m = json.loads(side.read_text())
    assert m["fit_window_deg"] == [7.4, 7.6]


def test_no_sidecar_is_written_when_no_meta_is_given(tmp_path):
    out = tmp_path / "fit.csv"
    pf.write_peak_fit_csv(str(out), [])
    assert not (tmp_path / "fit.meta.json").exists()


# ── the CLI ─────────────────────────────────────────────────────────────

def test_dark_archives_are_excluded_from_the_scan_point_list(tmp_path):
    """A dark in the middle of the list would shift every subsequent
    ``img`` index relative to a GSAS-II fit of the same scan -- a silent
    off-by-N in the comparison this tool exists for."""
    from midas_gui.peak_fit_cli import _archives
    for n in ("s_000010.ge1234.ave.zarr.zip", "s_000011.ge1234.ave.zarr.zip",
              "dark_000009.ge1234.ave.zarr.zip", "notes.txt"):
        (tmp_path / n).write_bytes(b"")
    got = _archives(str(tmp_path), "*.ge1234.ave.zarr.zip")
    assert [n for n, _p in got] == [10, 11]


def test_a_center_outside_the_data_says_so_rather_than_crashing(tmp_path,
                                                                capsys):
    """``--center`` is the one argument with no sensible default and no
    way to validate ahead of time, so a typo in it is the likeliest way
    to misuse this CLI."""
    zarr = pytest.importorskip("zarr")
    from midas_gui.peak_fit_cli import main
    arc = tmp_path / "s_000001.ge1234.ave.zarr.zip"
    store = zarr.ZipStore(str(arc), mode="w")
    z = zarr.group(store)
    rmap = np.zeros((5, 40, 2))
    rmap[1] = np.linspace(5.0, 6.0, 40)[:, None]       # 2theta
    rmap[2] = np.array([-175.0, -165.0])[None, :]      # eta
    z["REtaMap"] = rmap
    z["Omegas"] = np.arange(3.0)
    for i in range(3):
        z[f"OmegaSumFrame/LastFrameNumber_{i}"] = np.ones((40, 2))
    store.close()

    rc = main(["--scan-dir", str(tmp_path), "--out", str(tmp_path / "o.csv"),
               "--center", "7.52"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "no R bin falls in" in out
    assert not (tmp_path / "o.csv").exists()
