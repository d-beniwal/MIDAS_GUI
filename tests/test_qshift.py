"""Reference-peak q-alignment (midas_gui.qshift): drift is removed, real shifts survive."""
import numpy as np
import pytest

from midas_gui import qshift as Q


def _peak(q, c, a=100.0, w=0.02):
    return a * np.exp(-4 * np.log(2) * ((q - c) / w) ** 2)


def _synthetic(n=40, seed=0, noise=0.2):
    """Ref peak at 2.00 Å⁻¹ (stationary), sample peak at 3.00 that *really* moves
    with delay; the whole pattern is scaled by a slow drift (up to +3e-4)."""
    rng = np.random.default_rng(seed)
    q = np.linspace(1.5, 3.5, 2000)
    delays = np.repeat([-2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 4.0, 5.0], n // 8)
    drift = 1.0 + 3e-4 * np.linspace(0, 1, n)            # scale drift with clock
    real = np.where(delays > 0, -1.5e-3 * delays / 5.0, 0.0)   # real peak-2 shift (Å⁻¹ frac)
    P = np.empty((n, q.size))
    for i in range(n):
        p = _peak(q, 2.00 * drift[i]) + _peak(q, 3.00 * (1 + real[i]) * drift[i]) + 5.0
        P[i] = p + rng.normal(0, noise, q.size)
    return q, P, delays, drift, real


@pytest.mark.parametrize("method", ["pvoigt", "centroid"])
def test_fit_recovers_known_positions(method):
    q, P, _, drift, _ = _synthetic()
    fit = Q.fit_peak_positions(q, P, (1.9, 2.1), method)
    assert fit["ok"].all()
    assert np.allclose(fit["pos"], 2.00 * drift, atol=2e-4 if method == "pvoigt" else 1e-3)


@pytest.mark.parametrize("shift_mode", ["scale", "offset"])
def test_alignment_pins_reference_and_keeps_real_shift(shift_mode):
    q, P, delays, drift, real = _synthetic()
    ref = [-2.0, -1.0, 0.0]
    corrected, info = Q.align_profiles(
        q, P, delays, window=(1.9, 2.1), ref_delays=ref, method="pvoigt",
        shift_mode=shift_mode, target_mode="reference")
    # reference peak is now (nearly) constant at the reference-frame mean
    after = Q.fit_peak_positions(q, corrected, (1.9, 2.1), "pvoigt")["pos"]
    assert np.ptp(after) < 3e-4
    assert abs(after.mean() - info["target"]) < 3e-4
    # the *real* movement of the second peak survives: later delays sit lower in q
    pos2 = Q.fit_peak_positions(q, corrected, (2.8, 3.2), "pvoigt")["pos"]
    early = pos2[delays <= 0].mean(); late = pos2[delays >= 4].mean()
    assert early - late > 0.8e-3 * 3.0          # ≥ 0.8 of injected 1.5e-3·3.0·(4..5)/5
    # …and the uncorrected data had the drift mixed in (sanity: correction mattered)
    raw2 = Q.fit_peak_positions(q, P, (2.8, 3.2), "pvoigt")["pos"]
    assert not np.allclose(raw2, pos2, atol=1e-4)


def test_stationarity_guard_flags_a_peak_that_follows_the_pump():
    q, P, delays, *_ = _synthetic(noise=0.05)
    # the SECOND peak really moves with delay → using it as the reference must warn
    _, info = Q.align_profiles(q, P, delays, window=(2.8, 3.2), ref_delays=[-2.0],
                               smooth=0)
    assert abs(info["delay_corr"]) > 0.5


def test_flagged_frames_are_interpolated():
    q, P, delays, *_ = _synthetic()
    P = P.copy()
    P[10] = 5.0                                  # peak vanishes in one frame
    fit = Q.fit_peak_positions(q, P, (1.9, 2.1), "pvoigt")
    assert not fit["ok"][10]
    curve = Q.drift_curve(fit["pos"], fit["ok"], smooth=0)
    assert np.isfinite(curve).all()
    assert abs(curve[10] - 0.5 * (curve[9] + curve[11])) < 1e-6


def test_apply_identity_and_clamping():
    q = np.linspace(1.0, 2.0, 500)
    P = np.vstack([_peak(q, 1.5, w=0.05)])
    same = Q.apply_q_shift(q, P, np.array([1.0]), np.array([0.0]))
    assert np.allclose(same, P)
    shifted = Q.apply_q_shift(q, P, np.array([1.0]), np.array([0.1]))
    assert np.isfinite(shifted).all()
    c = Q.fit_peak_positions(q, shifted, (1.3, 1.9), "centroid")["pos"][0]
    assert abs(c - 1.6) < 1e-3


def test_bad_inputs():
    q = np.linspace(1, 2, 100)
    with pytest.raises(ValueError):
        Q.fit_peak_positions(q, np.ones((3, 100)), (1.50, 1.51))     # too few bins
    with pytest.raises(ValueError):
        Q.choose_target(np.ones(3), mode="fixed", fixed=None)
    with pytest.raises(ValueError):
        Q.drift_curve(np.full(3, np.nan), np.zeros(3, bool))


@pytest.fixture(scope="session")
def qapp_keepalive():
    """Hold the QApplication for the whole session: a function-local reference is
    collected at test end and the next GUI test then aborts (see test_smoke.py)."""
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_tab_alignment_roundtrip(qapp_keepalive):
    """Tab: toggling alignment swaps ``_data`` between raw and aligned payloads and the
    reference peak ends up pinned; raw payload is never modified."""
    try:
        from midas_gui.tab_pumpprobe import PumpProbeTab
        from midas_gui.workers import PumpProbeWorker
    except Exception as exc:
        pytest.skip(f"needs the full MIDAS stack: {exc}")
    q, P, delays, *_ = _synthetic()
    res = PumpProbeWorker._group_and_difference(P, delays, [-2.0, -1.0, 0.0])
    res.update({"r_axis_px": np.arange(q.size, dtype=float), "q_axis": q,
                "tth_axis": q.copy(), "lsd": 1e6, "px": 200.0, "wl": 0.2,
                "profiles": P, "frame_delays": delays})
    tab = PumpProbeTab()
    tab._on_done(res)
    raw_copy = res["I_by_delay"].copy()
    assert tab._data is tab._data_raw
    tab._qa_lo.setValue(1.9); tab._qa_hi.setValue(2.1)
    tab._qa_check.setChecked(True)
    assert tab._data is not tab._data_raw and tab._qa_info is not None
    pos = Q.fit_peak_positions(q, tab._data["I_by_delay"], (1.9, 2.1), "pvoigt")["pos"]
    assert np.ptp(pos) < 3e-4
    assert np.array_equal(tab._data_raw["I_by_delay"], raw_copy)      # raw untouched
    tab._qa_check.setChecked(False)
    assert tab._data is tab._data_raw and tab._qa_info is None
    # empty / bad window reports instead of raising
    tab._qa_lo.setValue(0.0); tab._qa_hi.setValue(0.0)
    tab._qa_check.setChecked(True)
    assert tab._data is tab._data_raw
    assert "window" in tab._qa_status.text().lower()
    tab.deleteLater()
