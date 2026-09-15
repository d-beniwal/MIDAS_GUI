"""Unit tests for midas_gui.auto_attenuation.

Covers the numerical core ported from pyAutoBeam (masking, NIST Cu lookup,
single-capture analysis), the new saturation pre-check (not present in
pyAutoBeam), the snapshot round-trip, and a Qt smoke test of the popup
dialog built directly off an in-memory snapshot (no subprocess).
"""
import numpy as np
import pytest


# ── masking.py ──────────────────────────────────────────────────────────

def test_create_frozen_pixel_mask_flags_stuck_nonzero_pixel():
    from midas_gui.auto_attenuation.masking import create_frozen_pixel_mask

    data = np.zeros((5, 4, 4), dtype=np.float32)
    data[:, 1, 1] = 100.0                      # frozen: constant nonzero
    data[:, 2, 2] = np.arange(5) * 10.0        # fluctuating, not frozen

    mask = create_frozen_pixel_mask(data, std_cutoff=0.5)
    assert mask[1, 1] == 1.0
    assert mask[2, 2] == 0.0
    assert mask[0, 0] == 0.0  # always-zero pixel is not "frozen"


def test_create_isolated_hot_pixel_mask_requires_isolation():
    from midas_gui.auto_attenuation.masking import create_isolated_hot_pixel_mask

    frame = np.zeros((10, 10), dtype=np.float32)
    frame[5, 5] = 5000.0          # isolated hot spike
    frame[2, 2] = 5000.0
    frame[2, 3] = 4000.0          # has a bright neighbor -> not isolated

    mask = create_isolated_hot_pixel_mask(frame, noise_floor=30.0, min_hot_intensity=2000.0)
    assert mask[5, 5] == 1.0
    assert mask[2, 2] == 0.0


def test_create_dark_mask_flags_dead_and_hot_pixels():
    from midas_gui.auto_attenuation.masking import create_dark_mask

    rng = np.random.default_rng(0)
    dark = 100.0 + rng.normal(0, 2.0, size=(10, 21, 21)).astype(np.float32)
    dark[:, 10, 10] = 100.0        # dead: zero variance
    dark[:, 5, 5] = 900.0          # hot: far above local neighborhood

    mask, info = create_dark_mask(dark, n_sigma=5, local_window=9)
    assert mask[10, 10] == 1.0
    assert mask[5, 5] == 1.0
    assert info["n_dead"] >= 1
    assert info["n_hot"] >= 1


def test_create_dark_mask_requires_at_least_two_frames():
    from midas_gui.auto_attenuation.masking import create_dark_mask

    with pytest.raises(ValueError):
        create_dark_mask(np.zeros((1, 4, 4), dtype=np.float32))


def test_apply_mask_zeroes_bad_pixels():
    from midas_gui.auto_attenuation.masking import apply_mask

    data = np.ones((2, 3, 3), dtype=np.float32) * 10.0
    mask = np.zeros((3, 3), dtype=np.float32)
    mask[1, 1] = 1.0
    out = apply_mask(data, mask)
    assert out[:, 1, 1].sum() == 0.0
    assert out[:, 0, 0].sum() == 20.0


# ── nist_cu.py ──────────────────────────────────────────────────────────

def test_estimate_mu_linear_matches_hand_computed_value_at_a_table_point():
    from midas_gui.auto_attenuation.nist_cu import estimate_mu_linear, CU_DENSITY

    # 5.00E-01 MeV = 500 keV, mu/rho = 8.36E-02 cm^2/g exactly in the table.
    mu = estimate_mu_linear(500.0)
    expected = 8.36e-02 * CU_DENSITY / 10.0
    assert mu == pytest.approx(expected, rel=1e-6)


def test_estimate_mu_linear_out_of_range_raises():
    from midas_gui.auto_attenuation.nist_cu import estimate_mu_linear

    with pytest.raises(ValueError):
        estimate_mu_linear(0.001)  # far below 1 keV table floor


def test_mu_decreases_then_flattens_with_energy():
    from midas_gui.auto_attenuation.nist_cu import estimate_mu_linear

    mu_low = estimate_mu_linear(10.0)
    mu_mid = estimate_mu_linear(63.0)
    mu_high = estimate_mu_linear(500.0)
    assert mu_low > mu_mid > mu_high


# ── saturation.py ────────────────────────────────────────────────────────

def test_check_saturation_ok_within_tolerance():
    from midas_gui.auto_attenuation.saturation import check_saturation

    frames = np.zeros((3, 5, 5), dtype=np.float32)
    frames[1, 0, 0] = 9000.0
    frames[1, 0, 1] = 9000.0  # 2 saturated pixels in frame 1, tolerate 5

    result = check_saturation(frames, None, saturation_intensity=1000.0,
                              tolerate_n=5, skip_frames=0)
    assert result.ok
    assert result.per_frame_bad_counts[1] == 2


def test_check_saturation_flags_frame_over_tolerance():
    from midas_gui.auto_attenuation.saturation import check_saturation

    frames = np.zeros((2, 5, 5), dtype=np.float32)
    frames[0, :2, :4] = 9000.0  # 8 saturated pixels, tolerate 5

    result = check_saturation(frames, None, saturation_intensity=1000.0,
                              tolerate_n=5, skip_frames=0)
    assert not result.ok
    assert 0 in result.bad_frame_indices


def test_check_saturation_skips_leading_frames():
    from midas_gui.auto_attenuation.saturation import check_saturation

    frames = np.zeros((2, 5, 5), dtype=np.float32)
    frames[0, :, :] = 9000.0  # entirely saturated first frame

    result = check_saturation(frames, None, saturation_intensity=1000.0,
                              tolerate_n=5, skip_frames=1)
    assert result.ok
    assert result.per_frame_bad_counts == {1: 0}


def test_check_saturation_excludes_masked_pixels():
    from midas_gui.auto_attenuation.saturation import check_saturation

    frames = np.zeros((2, 3, 3), dtype=np.float32)
    frames[:, 0, 0] = 9000.0
    mask = np.zeros((3, 3), dtype=np.float32)
    mask[0, 0] = 1.0  # this pixel is already known-bad

    result = check_saturation(frames, mask, saturation_intensity=1000.0,
                              tolerate_n=0, skip_frames=0)
    assert result.ok


# ── analysis.py ──────────────────────────────────────────────────────────

def test_preprocess_stack_skip_frames_and_dark_subtraction():
    from midas_gui.auto_attenuation.analysis import preprocess_stack

    frames = np.full((3, 4, 4), 100.0, dtype=np.float32)
    frames[0] = 99999.0  # would-be-saturated leading frame, must be dropped
    dark = np.full((4, 4), 10.0, dtype=np.float32)

    result = preprocess_stack(
        frames, dark=dark, skip_frames=1,
        frozen_mask=False, hot_pixel_mask=False,
    )
    assert result.data.shape[0] == 2
    assert np.allclose(result.data, 90.0)


def test_run_single_capture_recovers_injected_intensity():
    from midas_gui.auto_attenuation.analysis import (
        preprocess_stack, run_single_capture,
    )
    from midas_gui.auto_attenuation.attenuator_table import DEFAULT_POSITION_THICKNESS_MM
    from midas_gui.auto_attenuation.nist_cu import estimate_mu_linear

    energy_keV = 63.0
    att_pos = 3
    acq_time_s = 2.0
    thickness = DEFAULT_POSITION_THICKNESS_MM[att_pos]
    mu = estimate_mu_linear(energy_keV)

    # Build a frame stack whose max intensity, after acq_time normalization
    # and the known thickness, implies a chosen S*I0.
    si0_true = 20000.0
    intensity = si0_true * np.exp(-mu * thickness) * acq_time_s
    frames = np.full((3, 8, 8), intensity, dtype=np.float32)

    pre = preprocess_stack(frames, skip_frames=0, frozen_mask=False,
                           hot_pixel_mask=False)
    result = run_single_capture(
        pre, energy_keV=energy_keV, att_pos=att_pos, acq_time_s=acq_time_s,
        target_intensity=50000.0, min_intensity=10.0,
    )

    assert result["ok"]
    assert result["mu"] == pytest.approx(mu)
    assert result["SI0"] == pytest.approx(si0_true, rel=1e-4)
    # Recommendation table: thicker attenuator -> longer recommended time.
    recs = result["recommendations"]
    assert set(recs) == set(DEFAULT_POSITION_THICKNESS_MM)
    times_by_thickness = sorted(
        (DEFAULT_POSITION_THICKNESS_MM[p], recs[p]["recommended_time_s"])
        for p in recs
    )
    times = [t for _, t in times_by_thickness]
    assert times == sorted(times)


def test_run_single_capture_rejects_low_intensity():
    from midas_gui.auto_attenuation.analysis import (
        preprocess_stack, run_single_capture,
    )

    frames = np.full((2, 4, 4), 1.0, dtype=np.float32)
    pre = preprocess_stack(frames, skip_frames=0, frozen_mask=False,
                           hot_pixel_mask=False)
    result = run_single_capture(
        pre, energy_keV=63.0, att_pos=0, acq_time_s=1.0,
        min_intensity=1000.0,
    )
    assert not result["ok"]


# ── snapshot.py ──────────────────────────────────────────────────────────

def test_snapshot_round_trip(tmp_path):
    from midas_gui.auto_attenuation.snapshot import write_snapshot, load_snapshot

    frames = np.random.default_rng(1).random((3, 6, 6)).astype(np.float32)
    dark = np.ones((6, 6), dtype=np.float32) * 2.0
    mask = np.zeros((6, 6), dtype=np.float32)
    mask[0, 0] = 1.0
    geometry = {"wavelength_A": 0.1729, "Lsd": 200000.0}

    path = tmp_path / "snap.npz"
    write_snapshot(str(path), frames=frames, dark=dark, mask=mask,
                   energy_keV=71.676, geometry=geometry)
    out = load_snapshot(str(path))

    np.testing.assert_allclose(out["frames"], frames)
    np.testing.assert_allclose(out["dark"], dark)
    np.testing.assert_allclose(out["mask"], mask)
    assert out["energy_keV"] == pytest.approx(71.676)
    assert out["geometry"] == geometry
    assert "dark_stack" not in out


# ── dialog.py (Qt smoke test) ─────────────────────────────────────────────

@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _make_snapshot():
    return {
        "frames": np.full((3, 8, 8), 500.0, dtype=np.float32),
        "dark": np.full((8, 8), 10.0, dtype=np.float32),
        "mask": np.zeros((8, 8), dtype=np.float32),
        "energy_keV": 63.0,
    }


def test_dialog_prefills_energy_and_toggles_advanced(app):
    from midas_gui.auto_attenuation.dialog import AutoAttenuationDialog

    dlg = AutoAttenuationDialog(_make_snapshot())
    assert dlg.energy_spin.value() == pytest.approx(63.0)
    assert dlg.adv_box.isChecked() is False
    assert dlg.chk_dark.isEnabled()
    assert dlg.chk_dark_mask.isEnabled() is False  # no raw dark_stack in snapshot
    dlg.close()


def test_dialog_run_blocks_without_saturation_intensity(app, monkeypatch):
    from midas_gui.auto_attenuation.dialog import AutoAttenuationDialog
    from PyQt5 import QtWidgets

    dlg = AutoAttenuationDialog(_make_snapshot())
    warned = {}
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "warning",
        lambda *a, **k: warned.setdefault("called", True),
    )
    dlg.saturation_spin.setValue(0.0)
    dlg._on_run()
    assert warned.get("called")
    assert dlg._worker is None
    dlg.close()


def test_dialog_saturated_run_blocks_analysis(app, monkeypatch):
    from midas_gui.auto_attenuation.dialog import AutoAttenuationDialog
    from PyQt5 import QtWidgets

    # Slight per-frame jitter (real shot noise) on both the baseline and the
    # saturated pixel, so the default frozen-pixel mask (std < 0.5 across
    # frames) doesn't classify the perfectly-constant synthetic data as
    # "frozen" and mask the saturation away before the check ever runs.
    rng = np.random.default_rng(2)
    snapshot = _make_snapshot()
    snapshot["frames"] += rng.normal(0, 2.0, size=snapshot["frames"].shape).astype(np.float32)
    snapshot["frames"][:, 0, 0] = 99999.0 + rng.normal(0, 50.0, size=3).astype(np.float32)

    dlg = AutoAttenuationDialog(snapshot)
    monkeypatch.setattr(QtWidgets.QMessageBox, "warning", lambda *a, **k: None)
    dlg.saturation_spin.setValue(1000.0)
    dlg.tolerate_spin.setValue(0)
    dlg.skip_frames_spin.setValue(0)

    dlg._on_run()
    assert dlg._worker is not None
    dlg._worker.wait(5000)
    QtWidgets.QApplication.processEvents()

    assert "SATURATION DETECTED" in dlg.log.toPlainText()
    dlg.close()
