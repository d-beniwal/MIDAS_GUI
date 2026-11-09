"""Reference-peak q-alignment for pump-probe patterns (Qt-free, unit-testable).

Experimental drift (mono energy, sample height, beam centre …) shifts every
integrated pattern in q without any change in the material.  The user picks a
peak that the pump does **not** move; this module tracks that peak's position
frame by frame and builds a per-frame correction that pins it to a target
position, leaving every other q-dependent change (the real signal) intact.

Pipeline (all functions are pure NumPy/SciPy):

  fit_peak_positions   per-frame centre (+σ, flags) inside a q-window
  drift_curve          flagged-frame interpolation, optional reference-only fit,
                       optional running-median smoothing
  choose_target        where "preserved" is (reference mean / first / fixed)
  shift_parameters     per-frame (scale, offset) mapping measured → corrected q
  apply_q_shift        resample each pattern onto the original q grid

The correction maps a measured abscissa ``q`` to ``f(q)``:

  scale :  f(q) = q * (q_target / q_peak)
  offset:  f(q) = q + (q_target − q_peak)

and the corrected pattern is ``I_c(q) = I_meas(f⁻¹(q))`` (edge bins are clamped
to the nearest measured value, never NaN, so downstream autorange stays safe).
"""
from __future__ import annotations

import numpy as np

METHODS = ("pvoigt", "centroid")
SHIFT_MODES = ("scale", "offset")
TARGET_MODES = ("reference", "first", "fixed")


# ── peak models ──────────────────────────────────────────────────────────────
def _pvoigt(x, amp, x0, fwhm, eta, b0, b1):
    """Pseudo-Voigt (area-free, peak height ``amp``) on a linear background."""
    dx = (x - x0)
    g = np.exp(-4.0 * np.log(2.0) * (dx / fwhm) ** 2)
    l = 1.0 / (1.0 + 4.0 * (dx / fwhm) ** 2)
    return amp * (eta * l + (1.0 - eta) * g) + b0 + b1 * (x - x0)


def _centroid(x, y):
    """Background-subtracted (line through the window ends) centroid."""
    n = max(2, len(x) // 10)
    xb = np.r_[x[:n], x[-n:]]
    yb = np.r_[y[:n], y[-n:]]
    try:
        c = np.polyfit(xb, yb, 1)
    except Exception:
        c = np.array([0.0, float(np.mean(yb))])
    w = y - np.polyval(c, x)
    w = np.clip(w, 0.0, None)
    s = w.sum()
    if not np.isfinite(s) or s <= 0:
        return np.nan, np.nan, 0.0
    x0 = float((w * x).sum() / s)
    var = float((w * (x - x0) ** 2).sum() / s)
    return x0, float(np.sqrt(max(var, 0.0))), float(w.max())


def _half_max_width(x, y):
    """Crude FWHM seed from the half-height crossing of a baseline-corrected peak."""
    base = float(np.min(y))
    h = float(np.max(y)) - base
    if h <= 0:
        return float(x[-1] - x[0]) / 4.0
    above = np.where(y - base >= 0.5 * h)[0]
    if len(above) < 2:
        return float(x[-1] - x[0]) / 6.0
    return float(max(x[above[-1]] - x[above[0]], (x[-1] - x[0]) / 40.0))


# ── 1. per-frame peak positions ──────────────────────────────────────────────
def fit_peak_positions(q, profiles, window, method="pvoigt"):
    """Track one peak across frames.

    Parameters
    ----------
    q        1-D q axis (Å⁻¹), monotonic (need not be uniform).
    profiles (n_frames, n_q) intensities.
    window   (q_lo, q_hi) fit window; include flat background on both sides.
    method   ``"pvoigt"`` (pseudo-Voigt + linear background; default) or
             ``"centroid"`` (robust for low S/N).

    Returns a dict of length-``n_frames`` arrays:
      ``pos`` centre, ``sigma`` 1σ uncertainty (NaN if unknown), ``width``
      FWHM (centroid: 2.355·σ), ``amp`` peak height, ``ok`` bool quality flag.
    """
    if method not in METHODS:
        raise ValueError(f"unknown method {method!r}")
    q = np.asarray(q, dtype=float)
    P = np.asarray(profiles, dtype=float)
    if P.ndim != 2 or P.shape[1] != q.size:
        raise ValueError("profiles must be (n_frames, len(q))")
    lo, hi = float(min(window)), float(max(window))
    sel = (q >= lo) & (q <= hi) & np.isfinite(q)
    if sel.sum() < 7:
        raise ValueError("fit window holds fewer than 7 q-bins — widen it")
    x = q[sel]
    n = P.shape[0]
    pos = np.full(n, np.nan); sig = np.full(n, np.nan)
    wid = np.full(n, np.nan); amp = np.full(n, np.nan)
    ok = np.zeros(n, dtype=bool)
    span = float(x[-1] - x[0])
    edge = 0.02 * span
    # Seed from the mean pattern so frame 0 is not special-cased.
    ymean = np.nanmean(P[:, sel], axis=0)
    seed_x0 = float(x[int(np.nanargmax(ymean))])
    prev_x0 = seed_x0
    seed_w = _half_max_width(x, ymean)
    # A frame whose peak has (nearly) vanished cannot define a position.
    min_amp = 0.1 * max(float(np.nanmax(ymean) - np.nanmin(ymean)), 1e-12)

    curve_fit = None
    if method == "pvoigt":
        try:
            from scipy.optimize import curve_fit
        except Exception:      # pragma: no cover - scipy is a hard dependency
            method = "centroid"

    for i in range(n):
        y = P[i, sel]
        if not np.all(np.isfinite(y)):
            continue
        if method == "centroid":
            x0, s_w, h = _centroid(x, y)
            if np.isfinite(x0) and h >= min_amp and (x[0] + edge) < x0 < (x[-1] - edge):
                pos[i], wid[i], amp[i], ok[i] = x0, 2.3548 * s_w, h, True
                # centroid σ ~ width / sqrt(effective counts); approximate with
                # width / sqrt(n_bins) — only used for relative weighting/UI.
                sig[i] = (s_w / np.sqrt(max(1, len(x))))
            continue
        b0 = float(np.min(y))
        a0 = max(float(np.max(y)) - b0, 1e-12)
        p0 = [a0, prev_x0, seed_w, 0.5, b0, 0.0]
        a_hi = max(10.0 * a0, 1.0)
        bounds = ([0.0, x[0], span / 200.0, 0.0, -np.inf, -np.inf],
                  [a_hi, x[-1], span, 1.0, np.inf, np.inf])
        try:
            popt, pcov = curve_fit(_pvoigt, x, y, p0=p0, bounds=bounds, maxfev=4000)
        except Exception:
            continue
        x0 = float(popt[1])
        good = (np.isfinite(x0) and popt[0] >= min_amp
                and (x[0] + edge) < x0 < (x[-1] - edge)
                and popt[2] < 0.95 * span)
        if not good:
            continue
        pos[i] = x0; wid[i] = float(popt[2]); amp[i] = float(popt[0]); ok[i] = True
        try:
            sg = float(np.sqrt(pcov[1, 1]))
            sig[i] = sg if np.isfinite(sg) else np.nan
        except Exception:
            pass
        prev_x0 = x0
    return {"pos": pos, "sigma": sig, "width": wid, "amp": amp, "ok": ok}


# ── 2. drift curve ───────────────────────────────────────────────────────────
def _running_median(v, k):
    k = int(k)
    if k < 3:
        return v.copy()
    if k % 2 == 0:
        k += 1
    from scipy.ndimage import median_filter
    return median_filter(v, size=k, mode="nearest")


def drift_curve(pos, ok, *, smooth=0, use_mask=None):
    """Positions → a complete, optionally smoothed drift curve.

    ``ok`` frames that are also in ``use_mask`` (default: all) define the curve;
    every other frame is linearly interpolated in frame order.  ``smooth`` is a
    running-median window in frames (0/1/2 = off).  Raises if fewer than one
    usable frame exists.
    """
    pos = np.asarray(pos, dtype=float)
    good = np.asarray(ok, dtype=bool) & np.isfinite(pos)
    if use_mask is not None:
        good &= np.asarray(use_mask, dtype=bool)
    if not good.any():
        raise ValueError("no frame produced a usable peak position — adjust the window")
    idx = np.arange(pos.size, dtype=float)
    full = np.interp(idx, idx[good], pos[good])
    return _running_median(full, smooth)


# ── 3. target + shift parameters ─────────────────────────────────────────────
def choose_target(curve, *, mode="reference", ref_mask=None, fixed=None):
    """The q position the reference peak is pinned to."""
    if mode not in TARGET_MODES:
        raise ValueError(f"unknown target mode {mode!r}")
    if mode == "fixed":
        if fixed is None or not np.isfinite(fixed):
            raise ValueError("fixed target needs a value")
        return float(fixed)
    if mode == "first":
        return float(curve[0])
    if ref_mask is not None and np.any(ref_mask):
        return float(np.mean(np.asarray(curve)[np.asarray(ref_mask, dtype=bool)]))
    return float(np.mean(curve))


def shift_parameters(curve, target, mode="scale"):
    """Per-frame ``(scale, offset)`` such that ``f(q) = scale*q + offset`` sends the
    measured peak ``curve[i]`` onto ``target``."""
    if mode not in SHIFT_MODES:
        raise ValueError(f"unknown shift mode {mode!r}")
    curve = np.asarray(curve, dtype=float)
    if mode == "scale":
        return target / curve, np.zeros_like(curve)
    return np.ones_like(curve), target - curve


# ── 4. apply ─────────────────────────────────────────────────────────────────
def apply_q_shift(q, profiles, scale, offset):
    """Resample each pattern so features move from ``q`` to ``scale*q + offset``.

    ``I_c(q) = I_meas((q − offset)/scale)`` via linear interpolation on the
    original grid; out-of-range samples clamp to the nearest edge value.
    """
    q = np.asarray(q, dtype=float)
    P = np.asarray(profiles, dtype=float)
    scale = np.asarray(scale, dtype=float); offset = np.asarray(offset, dtype=float)
    out = np.empty_like(P)
    for i in range(P.shape[0]):
        out[i] = np.interp((q - offset[i]) / scale[i], q, P[i])
    return out


# ── convenience: whole pipeline ──────────────────────────────────────────────
def spearman(a, b):
    """Spearman rank correlation (no SciPy needed); NaN if degenerate."""
    a = np.asarray(a, dtype=float); b = np.asarray(b, dtype=float)
    if a.size < 3 or np.ptp(a) == 0 or np.ptp(b) == 0:
        return float("nan")
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    return float(np.corrcoef(ra, rb)[0, 1])


def align_profiles(q, profiles, delays, *, window, ref_delays=(), method="pvoigt",
                   shift_mode="scale", target_mode="reference", target_value=None,
                   smooth=0, fit_on_reference_only=False):
    """Run the full correction on per-frame ``profiles`` (frame/acquisition order).

    Returns ``(corrected_profiles, info)``; ``info`` carries ``pos``, ``curve``,
    ``target``, ``scale``, ``offset``, ``ok``, ``sigma``, ``width``, ``amp``,
    ``ref_mask`` and ``delay_corr`` (Spearman ρ between the per-delay mean
    position and delay — a large |ρ| means the "stationary" peak is tracking the
    pump response, so aligning on it would remove real signal).
    """
    delays = np.asarray(delays, dtype=float)
    fit = fit_peak_positions(q, profiles, window, method)
    ref_set = set(float(d) for d in ref_delays)
    ref_mask = np.array([float(d) in ref_set for d in delays], dtype=bool)
    use = ref_mask if (fit_on_reference_only and ref_mask.any()) else None
    curve = drift_curve(fit["pos"], fit["ok"], smooth=smooth, use_mask=use)
    target = choose_target(curve, mode=target_mode, ref_mask=ref_mask,
                           fixed=target_value)
    scale, offset = shift_parameters(curve, target, shift_mode)
    corrected = apply_q_shift(q, profiles, scale, offset)
    # stationarity guard: does the *measured* peak track delay?
    uniq = sorted(set(delays.tolist()))
    means = [float(np.nanmean(curve[delays == d])) for d in uniq]
    info = dict(fit, curve=curve, target=target, scale=scale, offset=offset,
                ref_mask=ref_mask, delay_corr=spearman(uniq, means))
    return corrected, info
