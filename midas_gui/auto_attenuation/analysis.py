"""Single-capture attenuation analysis.

Ported from pyAutoBeam's ``attenuation/analysis.py``, trimmed to the
single-capture (one buffer, one attenuator position, one exposure time)
case the GUI actually has — all file discovery / HDF5 reading / filename
parsing is dropped, since the caller already has the buffer, dark, mask,
energy, attenuator position and exposure time as in-memory values.

The multi-file linear-regression fit branch of the original ``analyze()``
is not ported: with a single capture, mu is always fixed from NIST and
S*I0 computed directly from the one measurement — pyAutoBeam's own
"single-file mode".
"""

import math
from dataclasses import dataclass, field

import numpy as np

from midas_gui.auto_attenuation import masking
from midas_gui.auto_attenuation.attenuator_table import (
    DEFAULT_POSITION_THICKNESS_MM,
    thickness_from_pos,
)
from midas_gui.auto_attenuation.nist_cu import estimate_mu_linear


@dataclass
class PreprocessResult:
    data: np.ndarray           # processed stack, bad pixels zeroed
    final_mask: np.ndarray     # 2D union of every mask step actually applied
    n_frozen: int = 0
    n_isolated_hot: int = 0
    log: list = field(default_factory=list)


def preprocess_stack(
    frames,
    dark=None,
    user_mask=None,
    dark_mask_stack=None,
    *,
    skip_frames=1,
    apply_dark=True,
    apply_dark_mask=True,
    apply_user_mask=True,
    frozen_mask=True,
    frozen_std_cutoff=0.5,
    hot_pixel_mask=True,
    noise_floor=30.0,
    min_hot_intensity=2000.0,
    percentile_mask=100.0,
    dark_mask_n_sigma=5,
    dark_mask_local_window=101,
):
    """Run the masking pipeline, keeping the full processed stack (not just max).

    Step order mirrors pyAutoBeam's ``extract_intensity``: skip frames ->
    dark subtract -> (dark-derived mask OR user mask) -> frozen-pixel mask
    -> isolated hot-pixel mask -> percentile mask.
    """
    log = []
    data = np.array(frames, dtype=np.float32)

    n_skip = max(0, int(skip_frames))
    if n_skip >= data.shape[0]:
        raise ValueError(
            f"skip_frames ({n_skip}) skips the entire stack "
            f"({data.shape[0]} frames)."
        )
    if n_skip > 0:
        data = data[n_skip:]
        log.append(f"Skipped first {n_skip} frame(s); {data.shape[0]} remain.")

    final_mask = np.zeros(data.shape[-2:], dtype=np.float32)

    if apply_dark and dark is not None:
        data = masking.subtract_dark(data, dark)
        np.clip(data, 0, None, out=data)
        log.append("Dark subtraction applied.")

    if apply_dark_mask and dark_mask_stack is not None:
        dmask, info = masking.create_dark_mask(
            dark_mask_stack, n_sigma=dark_mask_n_sigma,
            local_window=dark_mask_local_window,
        )
        final_mask = np.maximum(final_mask, dmask)
        log.append(
            f"Dark-derived mask: {info['n_dead']} dead, {info['n_hot']} hot "
            f"({info['n_total_bad']} total)."
        )

    if apply_user_mask and user_mask is not None:
        final_mask = np.maximum(final_mask, (np.asarray(user_mask) > 0).astype(np.float32))
        log.append(f"Data Viewer mask applied ({int(np.sum(user_mask > 0))} bad pixels).")

    if np.any(final_mask):
        data = masking.apply_mask(data, final_mask)

    n_frozen = 0
    if frozen_mask and data.shape[0] >= 2:
        fz_mask = masking.create_frozen_pixel_mask(data, std_cutoff=frozen_std_cutoff)
        n_frozen = int(np.sum(fz_mask > 0.5))
        data = masking.apply_mask(data, fz_mask)
        final_mask = np.maximum(final_mask, fz_mask)
        log.append(f"Frozen-pixel mask: {n_frozen} pixel(s).")

    n_isolated_hot = 0
    if hot_pixel_mask:
        hp_mask = masking.create_isolated_hot_pixel_mask(
            data, noise_floor=noise_floor, min_hot_intensity=min_hot_intensity,
        )
        n_isolated_hot = int(np.sum(hp_mask > 0.5))
        data = masking.apply_mask(data, hp_mask)
        final_mask = np.maximum(final_mask, hp_mask)
        log.append(f"Isolated hot-pixel mask: {n_isolated_hot} pixel(s).")

    if percentile_mask < 100.0:
        pct_mask = masking.create_percentile_mask(data, percentile=percentile_mask)
        data = masking.apply_mask(data, pct_mask)
        final_mask = np.maximum(final_mask, pct_mask)
        log.append(f"Percentile mask ({percentile_mask}%): "
                    f"{int(np.sum(pct_mask > 0.5))} pixel(s).")

    return PreprocessResult(
        data=data, final_mask=final_mask,
        n_frozen=n_frozen, n_isolated_hot=n_isolated_hot, log=log,
    )


def extract_intensity(preprocessed: PreprocessResult):
    """Max pixel value of an already-preprocessed stack."""
    data = preprocessed.data
    if data.size == 0 or np.max(data) <= 0:
        return {"intensity": 0.0, "n_frozen": preprocessed.n_frozen,
                "n_isolated_hot": preprocessed.n_isolated_hot}
    return {
        "intensity": float(np.max(data)),
        "n_frozen": preprocessed.n_frozen,
        "n_isolated_hot": preprocessed.n_isolated_hot,
    }


def run_single_capture(
    preprocessed: PreprocessResult,
    *,
    energy_keV,
    att_pos,
    acq_time_s,
    target_intensity=50000.0,
    min_intensity=1000.0,
    thickness_table=None,
):
    """Single-capture attenuation analysis: mu fixed from NIST, S*I0 direct.

    Returns ``None`` (with a message appended to the log) if the extracted
    intensity is below *min_intensity* — mirrors pyAutoBeam's own
    ``analyze()`` behaviour of refusing to fit on too-dim data.
    """
    log = list(preprocessed.log)
    table = thickness_table or DEFAULT_POSITION_THICKNESS_MM

    thickness = thickness_from_pos(att_pos, table)
    if thickness is None:
        log.append(f"ERROR: attenuator position {att_pos} is not in the "
                    f"thickness table.")
        return {"ok": False, "log": log}

    if acq_time_s is None or acq_time_s <= 0:
        log.append("ERROR: exposure time must be > 0.")
        return {"ok": False, "log": log}

    extraction = extract_intensity(preprocessed)
    intensity = extraction["intensity"]

    log.append(
        f"Energy: {energy_keV:.4f} keV | Att pos {att_pos} "
        f"(thickness {thickness:.2f} mm) | Exposure {acq_time_s:.4f} s"
    )
    log.append(
        f"Max intensity after preprocessing: {intensity:.1f} "
        f"(frozen: {extraction['n_frozen']}, isolated-hot: "
        f"{extraction['n_isolated_hot']})"
    )

    if intensity < min_intensity:
        log.append(
            f"ERROR: max intensity {intensity:.1f} is below min_intensity "
            f"({min_intensity}) — signal is too low for a reliable "
            f"estimate."
        )
        return {"ok": False, "log": log}

    mu_nist = estimate_mu_linear(energy_keV)
    log_rate = math.log(intensity / acq_time_s)
    C = log_rate + mu_nist * thickness
    SI0 = math.exp(C)

    log.append(f"mu (NIST, fixed): {mu_nist:.4f} /mm")
    log.append(f"C = log(S*I0)   : {C:.4f}")
    log.append(f"S*I0            : {SI0:.2f} cts/s")

    target_90 = 0.9 * target_intensity
    log.append("")
    log.append(f"Recommended acquisition time for 90% of target "
                f"({target_90:.0f} counts):")
    log.append(f"{'Att Pos':<10} {'Exposure per frame (s)':<24} "
                f"{'Pred. Counts':<14} {'Note':<10} {'Thickness (mm)'}")

    EXPOSURE_CAP_S = 1000.0
    recommendations = {}
    for pos in sorted(table):
        thick = table[pos]
        pred_rate = SI0 * math.exp(-mu_nist * thick)
        if pred_rate <= 0:
            continue
        rec_time = target_90 / pred_rate
        pred_counts = pred_rate * rec_time
        note = "Too Fast" if rec_time < 0.005 else ""
        recommendations[pos] = {
            "thickness_mm": thick,
            "recommended_time_s": rec_time,
            "predicted_counts": pred_counts,
        }
        exposure_str = (f">{EXPOSURE_CAP_S:g}" if rec_time > EXPOSURE_CAP_S
                        else f"{rec_time:.4f}")
        log.append(f"{pos:<10} {exposure_str:<24} "
                    f"{pred_counts:<14.0f} {note:<10} {thick:.2f}")

    return {
        "ok": True,
        "mu": mu_nist,
        "mu_nist": mu_nist,
        "C": C,
        "SI0": SI0,
        "thickness_mm": thickness,
        "intensity": intensity,
        "recommendations": recommendations,
        "log": log,
    }
