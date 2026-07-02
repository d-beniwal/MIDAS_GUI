"""Background QThread workers — every heavy operation runs off the GUI thread.

Worker pattern (context/design_rules.md): redirect stdout to a log signal for
verbose pipelines, catch every exception and emit it, store the worker as an
instance variable on the caller so it is not GC'd mid-run.
"""
from __future__ import annotations

import math
import traceback
from pathlib import Path
from typing import Optional

import numpy as np
from PyQt5 import QtCore

import midas_gui._paths  # noqa: F401  (sys.path setup before MIDAS imports)
from midas_gui import calib
from midas_gui.helpers import (_LogStream, _load_image, _apply_im_trans, _build_spec,
                               _spec_from_json, average_field, apply_field_corrections)


# ═════════════════════════════════════════════════════════════════════════════
#  Shared integration core (used by both single-frame and batch workers)
# ═════════════════════════════════════════════════════════════════════════════

def apply_q_uniform(spec, q_cfg: Optional[dict]):
    """Activate native Q-uniform binning on a spec when requested."""
    if q_cfg:
        spec.QMin     = float(q_cfg["QMin"])
        spec.QMax     = float(q_cfg["QMax"])
        spec.QBinSize = float(q_cfg["QBinSize"])
    return spec


def compute_r_axis(spec) -> np.ndarray:
    """Bin-centre radii in px, whether the spec is in R-uniform or Q-uniform mode."""
    n = spec.n_r_bins
    if spec.q_mode_active:
        wl  = float(spec.Wavelength)
        lsd = float(spec.Lsd); px = float(spec.pxY)
        q_c = spec.QMin + spec.QBinSize * (np.arange(n) + 0.5)
        two_theta = 2.0 * np.arcsin(np.clip(q_c * wl / (4 * math.pi), -1, 1))
        return lsd * np.tan(two_theta) / px
    return float(spec.RMin) + float(spec.RBinSize) * (np.arange(n) + 0.5)


def axis_conversions(r_px, lsd, px, wl):
    """Return (two_theta_deg, two_theta_centideg, Q_invA) from r in px."""
    r_px = np.asarray(r_px, dtype=float)
    two_theta = np.degrees(np.arctan(r_px * px / lsd))
    q = 4 * math.pi * np.sin(np.radians(two_theta) / 2) / wl
    return two_theta, two_theta * 100.0, q


def build_geom(spec, kernel: str, mask):
    from midas_integrate_v2 import (
        SubpixelBinGeometry, HardBinGeometry, PolygonBinGeometry)
    if kernel == "hard":
        return HardBinGeometry.from_spec(spec, mask=mask)
    if kernel == "polygon":
        return PolygonBinGeometry.from_spec(spec, mask=mask, n_jobs=-1)
    K = 4 if kernel == "subpixel4" else 2
    return SubpixelBinGeometry.from_spec(spec, K=K, mask=mask)


def _profile_from_cake(cake_np: np.ndarray) -> np.ndarray:
    prof = np.nanmean(cake_np, axis=0)
    return np.nan_to_num(prof, nan=0.0)


def q_grid_and_r(q_cfg, lsd, px, wl):
    """Uniform-Q bin centres + the matching R(px) for each Q (for axis/writers)."""
    n = max(1, int(round((q_cfg["QMax"] - q_cfg["QMin"]) / q_cfg["QBinSize"])))
    qgrid = q_cfg["QMin"] + q_cfg["QBinSize"] * (np.arange(n) + 0.5)
    two_theta = 2.0 * np.arcsin(np.clip(qgrid * wl / (4 * math.pi), -1, 1))
    r_of_q = lsd * np.tan(two_theta) / px
    return qgrid, r_of_q


def rebin_R_to_Q(r_ax, prof, sigma, qgrid, lsd, px, wl):
    """Rebin an R-uniform profile/σ onto a uniform-Q grid (the kernels don't do Q-mode).

    See analyze_workflows/workflow_analysis.md (P0-2): integrate R-uniform then interpolate
    onto uniform Q so rings land at the correct Q.
    """
    q_of_r = 4 * math.pi * np.sin(np.radians(np.degrees(np.arctan(r_ax * px / lsd))) / 2) / wl
    order = np.argsort(q_of_r)
    prof_q = np.interp(qgrid, q_of_r[order], prof[order])
    sig_q = np.interp(qgrid, q_of_r[order], sigma[order]) if sigma is not None else None
    return prof_q, sig_q


def corrections_counts(spec):
    """Per-(η,R)-bin pixel-count cake for normalising integrate_with_corrections.

    integrate_with_corrections returns SUMMED (unnormalised) counts per bin — a flat
    field integrates to a ramp rising with R.  Dividing by this counts cake (the same
    function applied to a ones-image) restores the per-pixel mean, matching the plain
    kernels.  See analyze_workflows/workflow_analysis.md (P0-1).
    """
    import torch
    import midas_integrate_v2 as m
    ones = torch.ones((spec.NrPixelsZ, spec.NrPixelsY), dtype=torch.float64)
    return m.integrate_with_corrections(ones, spec).detach().cpu().numpy()


def integrate_frame(img_t, spec, geom, kernel, corrections, variance_cfg,
                    need_sigma: bool, corr_counts=None, return_cake=False):
    """Integrate one frame, returning (profile, sigma_or_None) or (profile, sigma, cake).

    Routing:
      - corrections enabled  → integrate_with_corrections, NORMALISED by the pixel-count
        cake (pass corr_counts to avoid recomputing it per frame)
      - variance enabled     → integrate_<kernel>_with_variance (σ from error model)
      - otherwise            → plain kernel integration

    When return_cake=True, returns a 3-tuple (prof, sigma, cake_2d) where cake_2d is
    the (n_eta_bins, n_r_bins) normalised cake array.
    """
    import torch
    import midas_integrate_v2 as m

    pol, sa = corrections
    if pol is not None or sa is not None:
        int2d = m.integrate_with_corrections(
            img_t, spec, polarization=pol, solid_angle=sa).detach().cpu().numpy()
        counts = corr_counts if corr_counts is not None else corrections_counts(spec)
        with np.errstate(invalid="ignore", divide="ignore"):
            norm = np.where(counts > 0.5, int2d / counts, np.nan)
        prof = _profile_from_cake(norm)
        sigma = np.sqrt(np.maximum(prof, 0.0)) if need_sigma else None
        if return_cake:
            return prof, sigma, norm
        return prof, sigma

    if variance_cfg is not None:
        em = variance_cfg.get("error_model", "poisson")
        fn = {
            "hard":      m.integrate_hard_with_variance,
            "polygon":   m.integrate_polygon_with_variance,
        }.get(kernel, m.integrate_subpixel_with_variance)
        mean2d, sig2d = fn(img_t, geom, error_model=em)
        mean_np = mean2d.detach().cpu().numpy()
        sig_np  = sig2d.detach().cpu().numpy()
        prof = _profile_from_cake(mean_np)
        # σ of the η-mean: sqrt(Σσ²)/N over valid η bins
        var = np.nansum(sig_np ** 2, axis=0)
        cnt = np.maximum(np.sum(np.isfinite(sig_np), axis=0), 1)
        sigma = np.nan_to_num(np.sqrt(var) / cnt, nan=0.0)
        if return_cake:
            return prof, sigma, mean_np
        return prof, sigma

    fn = {
        "hard":    m.integrate_hard,
        "polygon": m.integrate_polygon,
    }.get(kernel, m.integrate_subpixel)
    int2d = fn(img_t, geom, normalize=True)
    cake_np = int2d.detach().cpu().numpy()
    prof = _profile_from_cake(cake_np)
    sigma = np.sqrt(np.maximum(prof, 0.0)) if need_sigma else None
    if return_cake:
        return prof, sigma, cake_np
    return prof, sigma


# ═════════════════════════════════════════════════════════════════════════════
#  Dark / bright / background field averaging
# ═════════════════════════════════════════════════════════════════════════════

class FieldAverageWorker(QtCore.QThread):
    """Average a dark/bright/background field off the GUI thread.

    kind ∈ {"file","folder","hdf5"}; index range is inclusive (end=-1 → last).
    """
    finished = QtCore.pyqtSignal(object)   # 2-D np.ndarray
    failed   = QtCore.pyqtSignal(str)

    def __init__(self, kind, path, dataset, idx_start, idx_end, parent=None):
        super().__init__(parent)
        self._kind, self._path, self._dataset = kind, path, dataset
        self._start, self._end = idx_start, idx_end

    def run(self):
        try:
            field = average_field(self._kind, self._path, self._dataset,
                                  self._start, self._end)
            self.finished.emit(np.asarray(field, dtype=np.float32))
        except Exception:
            self.failed.emit(traceback.format_exc())


# ═════════════════════════════════════════════════════════════════════════════
#  Mask workers
# ═════════════════════════════════════════════════════════════════════════════

def spatial_outlier_mask(med, stackmax, k_sigma, hot_f, dead_f, overflow):
    """Spatial outlier mask (template_auto_mask_unconstrained.ipynb approach).

    5×5 median residual → 15×15 robust local MAD → Z-score → hot/dead/sat gates.
    Returns (mask_bool, breakdown_str).
    """
    from scipy.ndimage import median_filter
    med = med.astype(np.float64)
    mf = median_filter(med, size=5)
    resid = med - mf
    local_scale = median_filter(np.abs(resid), size=15) * 1.4826 + 1e-6
    z = resid / local_scale
    mf_safe = np.clip(mf, 1e-9, None)
    hot  = (z >  k_sigma) & (med > hot_f  * mf_safe)
    dead = (z < -k_sigma) & (med < dead_f * mf_safe)
    sat  = (stackmax >= overflow) if overflow is not None else np.zeros_like(med, dtype=bool)
    mask = hot | dead | sat
    info = (f"hot: {int(hot.sum()):,}  dead: {int(dead.sum()):,}  sat: {int(sat.sum()):,}")
    return mask, info


def temporal_constancy_mask(stack: np.ndarray, frozen_frac: float) -> tuple:
    """Flag pixels whose temporal std is far below the detector-wide typical variation.

    A detector module stuck at a constant value (dead, gap, stuck ADC) has temporal
    std ≈ 0.  The 75th-percentile of non-zero per-pixel std is used as reference so
    the threshold adapts to the overall signal level without being pulled by dead pixels.

    Returns (mask_bool, info_str).
    """
    temp_std = np.std(stack.astype(np.float64), axis=0)
    nonzero = temp_std[temp_std > 0]
    if len(nonzero) == 0:
        return np.zeros(temp_std.shape, dtype=bool), "frozen: 0 (no variation)"
    ref = np.percentile(nonzero, 75)
    frozen = temp_std < (frozen_frac * ref)
    return frozen, f"frozen: {int(frozen.sum()):,} (ref_std={ref:.2g})"


class MaskComputeWorker(QtCore.QThread):
    """Compute the combined bad-pixel mask: base threshold OR'd with any enabled
    advanced method (statistical outlier, spatial spike, azimuthal clip, learnable).

    Azimuthal-clip and learnable-mask require a calibration result (geometry).
    """
    progress = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal(object)   # uint8 combined mask
    failed   = QtCore.pyqtSignal(str)

    def __init__(self, image, base_mask, methods, *, stack_paths=None,
                 calib_result=None, parent=None):
        super().__init__(parent)
        self._image = image
        self._base = base_mask
        self._methods = methods          # dict of {name: params or False}
        self._stack_paths = stack_paths
        self._result = calib_result

    def run(self):
        try:
            import torch
            import midas_integrate_v2 as m
            combined = self._base.astype(bool).copy()
            parts = [f"threshold: {int(self._base.sum()):,}"]

            stat = self._methods.get("stat")
            cosmic_ray = self._methods.get("cosmic_ray")
            # Load the frame stack once if any temporal method needs it
            stack = None
            if (stat or cosmic_ray) and self._stack_paths:
                self.progress.emit(f"Loading {len(self._stack_paths)} frames…")
                frames = [_load_image(p).astype(np.float32) for p in self._stack_paths]
                stack = np.stack(frames, axis=0)

            # Build median / stack-max for the statistical method
            if stat:
                if stack is not None:
                    self.progress.emit("Computing temporal median…")
                    med = np.median(stack, axis=0); stackmax = stack.max(axis=0)
                    # Temporal constancy: catches constant-value modules spatial methods miss
                    frozen_frac = stat.get("frozen_frac", 0.0)
                    if frozen_frac > 0 and stack.shape[0] >= 2:
                        self.progress.emit("Temporal constancy check…")
                        fmask, finfo = temporal_constancy_mask(stack, frozen_frac)
                        combined |= fmask
                        parts.append(finfo)
                else:
                    med = self._image; stackmax = self._image
                self.progress.emit("Statistical outlier detection…")
                mask, info = spatial_outlier_mask(
                    med, stackmax, stat["k_sigma"], stat["hot_factor"],
                    stat["dead_factor"], stat.get("overflow"))
                combined |= mask
                parts.append(f"stat({info})")

            # Cosmic-ray rejection (temporal σ-clip along the frame axis)
            if cosmic_ray:
                if stack is not None and stack.shape[0] >= 3:
                    self.progress.emit(
                        f"Cosmic-ray rejection (n_σ={cosmic_ray['n_sigma']}, "
                        f"{stack.shape[0]} frames)…")
                    from midas_integrate_v2.streaming import reject_cosmic_rays
                    _, cr_mask_3d = reject_cosmic_rays(
                        stack.astype(np.float64),
                        n_sigma=cosmic_ray["n_sigma"], mode="flag_only", use_mad=True)
                    cr_mask = cr_mask_3d.any(axis=0)
                    combined |= cr_mask
                    parts.append(f"cosmic-ray: {int(cr_mask.sum()):,}")
                elif stack is not None:
                    self.progress.emit(
                        "[cosmic-ray] skipped — need ≥3 frames "
                        f"(stack has {stack.shape[0]})")
                else:
                    self.progress.emit("[cosmic-ray] skipped — no stack folder specified")

            # Spatial spike rejection (geometry-free)
            spike = self._methods.get("spike")
            if spike:
                self.progress.emit("Spatial spike rejection…")
                _, sm = m.reject_spatial_spikes(
                    self._image.astype(np.float64), n_sigma=spike["n_sigma"],
                    method=spike.get("method", "laplacian"))
                combined |= sm.astype(bool)
                parts.append(f"spike: {int(sm.sum()):,}")

            # Azimuthal sigma-clip (needs geometry)
            azim = self._methods.get("azimuthal")
            if azim and self._result is not None:
                self.progress.emit("Azimuthal σ-clip…")
                from midas_gui.helpers import _build_spec
                spec = _build_spec(self._result, 2.0, 5.0)
                geom = m.HardBinGeometry.from_spec(spec)   # needs per-pixel bins
                _, am = m.azimuthal_sigma_clip(
                    self._image.astype(np.float64), geom, n_sigma=azim["n_sigma"])
                combined |= am.astype(bool)
                parts.append(f"azimuthal: {int(am.sum()):,}")

            # Learnable mask (needs geometry; differentiable training)
            learn = self._methods.get("learnable")
            if learn and self._result is not None:
                self.progress.emit("Learnable mask training…")
                from midas_gui.helpers import _build_spec
                spec = _build_spec(self._result, 2.0, 5.0)
                NZ, NY = self._image.shape
                static_t = torch.from_numpy(combined.astype(bool))
                lm = m.LearnableMask(NZ, NY, init_weight=float(learn.get("init_weight", 0.9)),
                                     static_mask=static_t)
                img_t = torch.from_numpy(self._image.astype(np.float64))
                loss_fn = m.EtaUniformityLoss(intensity_floor=0.0)
                opt = torch.optim.Adam(lm.parameters(), lr=float(learn.get("lr", 0.5)))
                n_steps = int(learn.get("n_steps", 300))
                sp_wt = float(learn.get("sparsity_weight", 1e-4))
                for step in range(n_steps):
                    opt.zero_grad()
                    int2d = m.integrate_with_corrections(img_t, spec, learnable_mask=lm)
                    loss = loss_fn(int2d) + m.sparsity_prior(lm, weight=sp_wt, target=1.0)
                    loss.backward(); opt.step()
                    if step % 25 == 0 or step == n_steps - 1:
                        with torch.no_grad():
                            nlow = lm.n_low_weight_pixels(0.5)
                        self.progress.emit(f"Learnable step {step+1}/{n_steps}  "
                                           f"loss={float(loss.detach()):.4g}  masked≈{nlow:,}")
                hard = lm.extract_hard_mask(threshold=0.5)
                combined |= np.asarray(hard).astype(bool)
                parts.append(f"learnable: {int(np.asarray(hard).sum()):,}")

            out = combined.astype(np.uint8)
            n = int(out.sum())
            self.progress.emit(f"Done — {'  '.join(parts)}  →  combined: {n:,} "
                               f"({100*n/out.size:.3f}%)")
            self.finished.emit(out)
        except Exception:
            self.failed.emit(traceback.format_exc())


# ═════════════════════════════════════════════════════════════════════════════
#  Calibration worker (pipeline-aware)
# ═════════════════════════════════════════════════════════════════════════════

class CalibrationWorker(QtCore.QThread):
    log_line = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal(object)
    failed   = QtCore.pyqtSignal(str)

    def __init__(self, mode, image, dark, cfg, parent=None,
                 bright=None, background=None, bright_mode="divide"):
        super().__init__(parent)
        self._mode  = mode
        self._image = image
        self._dark  = dark
        self._cfg   = cfg
        self._bright = bright
        self._background = background
        self._bright_mode = bright_mode

    def run(self):
        import sys
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = _LogStream(self.log_line)  # type: ignore
        try:
            image = self._image.astype(np.float32)
            # Bright/background are applied here; dark stays passed to the pipeline.
            if self._bright is not None or self._background is not None:
                image = apply_field_corrections(
                    image, dark=None, bright=self._bright,
                    bright_mode=self._bright_mode, background=self._background
                ).astype(np.float32)
                self.log_line.emit(
                    f"[calibrate] applied "
                    f"{'bright(' + self._bright_mode + ') ' if self._bright is not None else ''}"
                    f"{'background ' if self._background is not None else ''}correction")
            mask = self._cfg.get("mask")
            if mask is not None:
                image = image.copy()
                image[mask.astype(bool)] = 0.0   # zero sentinels before calibration
            raw = calib.run_pipeline(self._mode, image, self._dark, self._cfg)
            NZ, NY = image.shape
            result = calib.normalize_result(
                raw, self._mode, NY=NY, NZ=NZ,
                pxY=self._cfg["pxY"], pxZ=self._cfg.get("pxZ"),
                wavelength=self._cfg["wavelength"])
            result._calibrant_name = self._cfg["calibrant"]
            self.finished.emit(result)
        except Exception:
            self.failed.emit(traceback.format_exc())
        finally:
            sys.stdout, sys.stderr = old_out, old_err


# ═════════════════════════════════════════════════════════════════════════════
#  Single-frame integration worker (Tab 2 post-calibration preview)
# ═════════════════════════════════════════════════════════════════════════════

class IntegrationWorker(QtCore.QThread):
    log_line = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal(object)
    failed   = QtCore.pyqtSignal(str)

    def __init__(self, result, image, dark, im_trans, r_bin, eta_bin,
                 mask=None, parent=None, bright=None, background=None,
                 bright_mode="divide"):
        super().__init__(parent)
        self._result, self._image, self._dark = result, image, dark
        self._im_trans, self._r_bin, self._eta_bin = im_trans, r_bin, eta_bin
        self._mask = mask
        self._bright, self._background, self._bright_mode = bright, background, bright_mode

    def run(self):
        try:
            import torch
            self.log_line.emit("[integrate] Building spec…")
            spec = _build_spec(self._result, self._r_bin, self._eta_bin)
            img = _apply_im_trans(self._image.astype(np.float32), self._im_trans)
            if self._dark is not None or self._bright is not None or self._background is not None:
                dark = (_apply_im_trans(self._dark.astype(np.float32), self._im_trans)
                        if self._dark is not None else None)
                bright = (_apply_im_trans(self._bright.astype(np.float32), self._im_trans)
                          if self._bright is not None else None)
                bg = (_apply_im_trans(self._background.astype(np.float32), self._im_trans)
                      if self._background is not None else None)
                img = apply_field_corrections(
                    img, dark=dark, bright=bright,
                    bright_mode=self._bright_mode, background=bg).astype(np.float32)
            mask_t = None
            if self._mask is not None:
                mask_t = _apply_im_trans(self._mask.astype(np.float32), self._im_trans)
            self.log_line.emit("[integrate] Running integration…")
            geom = build_geom(spec, "subpixel2", mask_t)
            img_t = torch.from_numpy(img.astype(np.float64))
            prof, _ = integrate_frame(img_t, spec, geom, "subpixel2",
                                      (None, None), None, need_sigma=False)
            r_ax = compute_r_axis(spec)
            self.log_line.emit(f"[integrate] Done — {len(prof)} bins, peak={prof.max():.1f}")
            self.finished.emit({
                "r_axis_px": r_ax, "profile": prof,
                "wavelength_A": float(spec.Wavelength),
                "lsd_um": float(spec.Lsd), "px_um": float(spec.pxY),
            })
        except Exception:
            self.failed.emit(traceback.format_exc())


# ═════════════════════════════════════════════════════════════════════════════
#  Batch integration worker (Tab 3)
# ═════════════════════════════════════════════════════════════════════════════

class BatchWorker(QtCore.QThread):
    progress   = QtCore.pyqtSignal(int, int)
    frame_done = QtCore.pyqtSignal(str, object, object, object)  # id, r_axis, prof, sigma
    finished   = QtCore.pyqtSignal(dict)
    failed     = QtCore.pyqtSignal(str)
    log_line   = QtCore.pyqtSignal(str)

    def __init__(self, spec, source_cfg, mask, out_dir, fmt, kernel,
                 corrections, variance_cfg, q_cfg=None,
                 frame_range=None, monitor_file=None, drift_traj=None, parent=None,
                 dark=None, bright=None, background=None, bright_mode="divide"):
        super().__init__(parent)
        self._spec = spec                    # always R-uniform (Q handled by rebinning)
        self._src  = source_cfg
        self._mask = mask
        self._out_dir = Path(out_dir) if out_dir else None
        self._fmt = fmt
        self._kernel = kernel
        self._corrections = corrections      # (pol, sa)
        self._variance_cfg = variance_cfg    # dict or None
        self._q_cfg = q_cfg                  # {"QMin","QMax","QBinSize"} or None
        # frame_range: (start, end_exclusive_or_None, stride) — None means all frames
        self._frame_range = frame_range or (0, None, 1)
        self._monitor_file = monitor_file    # path to text file, one value per line
        self._drift_traj = drift_traj        # DriftTrajectory or None
        # Dark / bright / background pre-processing (per-frame)
        self._dark, self._bright, self._background = dark, bright, background
        self._bright_mode = bright_mode

    def _open_source(self):
        from midas_integrate_v2.streaming import TIFFGlobSource, HDF5FrameSource
        c = self._src
        if c["type"] == "tiff_glob":
            return TIFFGlobSource(c["path"])
        if c["type"] == "hdf5":
            return HDF5FrameSource(c["path"], dataset=c.get("dataset", "frames"))
        raise ValueError(f"Unknown source type: {c['type']}")

    def _write_one(self, base: Path, fmt, r_px, prof, sigma, lsd, px, wl,
                   cake_2d=None, eta_axis=None):
        import midas_integrate_v2 as m
        two_theta, two_theta_cd, q = axis_conversions(r_px, lsd, px, wl)
        sig = sigma if sigma is not None else np.sqrt(np.maximum(prof, 0.0))
        if fmt == "csv":
            m.write_csv(str(base) + ".csv", r_axis=r_px, intensity=prof, sigma=sig)
        elif fmt == "xye":
            m.write_xye(str(base) + ".xye", r_axis=two_theta, intensity=prof, sigma=sig)
        elif fmt == "fxye":
            m.write_fxye(str(base) + ".fxye", r_axis=two_theta_cd, intensity=prof, sigma=sig)
        elif fmt == "dat":
            m.write_dat(str(base) + ".dat", q_axis_invA=q, intensity=prof, sigma=sig)
        elif fmt == "2d_csv" and cake_2d is not None:
            # Save (n_eta, n_r) cake: first row = R(px) header, first col = η(deg) labels
            out_path = str(base) + "_cake.csv"
            n_eta, n_r = cake_2d.shape
            eta_vals = eta_axis if eta_axis is not None else np.arange(n_eta, dtype=float)
            header = "eta\\R(px)," + ",".join(f"{r:.4f}" for r in r_px)
            rows = [f"{eta_vals[k]:.4f}," + ",".join(f"{v:.6g}" for v in cake_2d[k])
                    for k in range(n_eta)]
            with open(out_path, "w") as fh:
                fh.write(header + "\n")
                fh.write("\n".join(rows) + "\n")

    def run(self):
        try:
            import torch
            import midas_integrate_v2 as m
            spec = self._spec
            spec.validate()
            lsd = float(spec.Lsd); px = float(spec.pxY); wl = float(spec.Wavelength)

            self.log_line.emit("[batch] Building geometry (one-time)…")
            pol, sa = self._corrections
            corr_on = pol is not None or sa is not None
            want_cake = (self._fmt == "2d_csv")
            geom = None if corr_on else build_geom(spec, self._kernel, self._mask)
            r_ax = compute_r_axis(spec)
            need_sigma = True   # xye/fxye require σ; always provide it
            # Precompute the pixel-count cake once for the (unnormalised) corrections path
            corr_counts = corrections_counts(spec) if corr_on else None
            # Q-uniform handled by rebinning the R-uniform profile (kernels lack Q-mode)
            if self._q_cfg:
                qgrid, r_ax = q_grid_and_r(self._q_cfg, lsd, px, wl)

            # η axis for 2D-CSV column labels
            n_eta = spec.n_eta_bins
            eta_ax = float(spec.EtaMin) + float(spec.EtaBinSize) * (np.arange(n_eta) + 0.5)

            # Frame range / stride
            fr_start, fr_end, fr_stride = self._frame_range

            # Monitor normalisation: load per-frame scalars if a file was provided
            monitor_vals = None
            if self._monitor_file:
                try:
                    monitor_vals = [float(x) for x in
                                    Path(self._monitor_file).read_text().split()]
                    self.log_line.emit(
                        f"[batch] monitor file: {len(monitor_vals)} values loaded")
                except Exception as e:
                    self.log_line.emit(f"[batch] monitor file error: {e}")

            source = self._open_source()
            total = source.n_frames
            self.log_line.emit(
                f"[batch] {total} frames | kernel={self._kernel} | "
                f"corrections={'on' if corr_on else 'off'} | "
                f"variance={'on' if self._variance_cfg else 'off'} | "
                f"q_uniform={'on (rebinned)' if self._q_cfg else 'off'} | "
                f"frame_range=({fr_start},{fr_end},{fr_stride}) | "
                f"monitor={'yes' if monitor_vals else 'no'} | "
                f"drift={'on' if self._drift_traj else 'off'}")
            if self._drift_traj is not None:
                self.log_line.emit(
                    f"[batch] drift trajectory: {len(self._drift_traj.frame_indices)} knots  "
                    f"Lsd [{self._drift_traj.Lsd_t.min():.0f}, {self._drift_traj.Lsd_t.max():.0f}] µm")

            fields_on = (self._dark is not None or self._bright is not None
                         or self._background is not None)
            if fields_on:
                self.log_line.emit(
                    f"[batch] field corrections: dark={'y' if self._dark is not None else 'n'} "
                    f"bright={self._bright_mode if self._bright is not None else 'n'} "
                    f"background={'y' if self._background is not None else 'n'}")

            aborted = False
            all_profiles, all_sigmas, frame_ids, out_paths = [], [], [], []
            proc_idx = 0  # index into monitor_vals for processed frames only
            for abs_i, (fid, img) in enumerate(source):
                # Cooperative abort — stop cleanly, keeping frames already done.
                if self.isInterruptionRequested():
                    aborted = True
                    self.log_line.emit(f"[batch] aborted by user after {proc_idx} frame(s)")
                    break
                # Apply frame range / stride
                if abs_i < fr_start:
                    continue
                if fr_end is not None and abs_i >= fr_end:
                    break
                if (abs_i - fr_start) % fr_stride != 0:
                    continue
                # Dark / bright / background pre-processing
                if fields_on:
                    img = apply_field_corrections(
                        img, dark=self._dark, bright=self._bright,
                        bright_mode=self._bright_mode, background=self._background)

                # Per-frame geometry when drift correction is active
                if self._drift_traj is not None:
                    cur_spec = _spec_from_trajectory(self._spec, self._drift_traj, abs_i)
                    cur_lsd  = float(cur_spec.Lsd)
                    cur_geom = None if corr_on else build_geom(cur_spec, self._kernel, self._mask)
                    cur_cc   = corrections_counts(cur_spec) if corr_on else None
                else:
                    cur_spec = spec
                    cur_lsd  = lsd
                    cur_geom = geom
                    cur_cc   = corr_counts

                img_t = torch.from_numpy(img.astype(np.float64))
                if want_cake:
                    prof, sigma, cake_2d = integrate_frame(
                        img_t, cur_spec, cur_geom, self._kernel, self._corrections,
                        self._variance_cfg, need_sigma, corr_counts=cur_cc,
                        return_cake=True)
                else:
                    cake_2d = None
                    prof, sigma = integrate_frame(
                        img_t, cur_spec, cur_geom, self._kernel, self._corrections,
                        self._variance_cfg, need_sigma, corr_counts=cur_cc)

                if sigma is None:
                    sigma = np.sqrt(np.maximum(prof, 0.0))

                # Apply monitor normalisation
                if monitor_vals is not None and proc_idx < len(monitor_vals):
                    mon = float(monitor_vals[proc_idx])
                    if mon != 0.0:
                        prof = prof / mon
                        sigma = sigma / abs(mon)
                        if cake_2d is not None:
                            cake_2d = cake_2d / mon

                if self._q_cfg:   # rebin R-uniform → uniform Q
                    prof, sigma = rebin_R_to_Q(compute_r_axis(spec), prof, sigma,
                                               qgrid, lsd, px, wl)
                all_profiles.append(prof)
                all_sigmas.append(sigma)
                frame_ids.append(fid)
                self.frame_done.emit(fid, r_ax, prof, sigma)
                self.progress.emit(proc_idx + 1, total)
                proc_idx += 1

                if self._out_dir is not None and self._fmt not in ("h5",):
                    self._out_dir.mkdir(parents=True, exist_ok=True)
                    base = self._out_dir / fid
                    self._write_one(base, self._fmt, r_ax, prof, sigma, cur_lsd, px, wl,
                                    cake_2d=cake_2d, eta_axis=eta_ax)
                    ext = "_cake.csv" if self._fmt == "2d_csv" else "." + self._fmt
                    out_paths.append(str(base) + ext)

            # HDF5: single file with the full stack
            if self._out_dir is not None and self._fmt == "h5":
                self._out_dir.mkdir(parents=True, exist_ok=True)
                h5_path = self._out_dir / "integrated.h5"
                m.write_h5(str(h5_path),
                           profiles=np.array(all_profiles),
                           r_axis=r_ax,
                           frame_ids=frame_ids,
                           sigmas=np.array(all_sigmas))
                out_paths.append(str(h5_path))

            n_proc = len(all_profiles)
            self.finished.emit({
                "n": n_proc, "r_axis_px": r_ax,
                "profiles": np.array(all_profiles) if all_profiles else np.array([]),
                "frame_ids": frame_ids,
                "out_paths": out_paths,
                "aborted": aborted,
            })
        except Exception:
            self.failed.emit(traceback.format_exc())


# ═════════════════════════════════════════════════════════════════════════════
#  Corrected-ring worker (forward model: tilts + distortion)
# ═════════════════════════════════════════════════════════════════════════════

class CorrectedRingsWorker(QtCore.QThread):
    finished = QtCore.pyqtSignal(object)   # list of (xs, ys) arrays, one per ring
    failed   = QtCore.pyqtSignal(str)

    def __init__(self, result, radii_px, parent=None):
        super().__init__(parent)
        self._result = result
        self._radii_px = radii_px

    def run(self):
        try:
            import torch
            from midas_calibrate_v2.compat.to_integrate import spec_from_calibration_result
            from midas_integrate_v2.forward.pixels import pixel_to_REta_from_spec

            spec = spec_from_calibration_result(self._result, RBinSize=1.0)
            NY, NZ = spec.NrPixelsY, spec.NrPixelsZ
            step = max(2, min(NY, NZ) // 600)
            ys = torch.arange(0, NY, step, dtype=torch.float64)
            zs = torch.arange(0, NZ, step, dtype=torch.float64)
            Z_grid, Y_grid = torch.meshgrid(zs, ys, indexing="ij")
            with torch.no_grad():
                out   = pixel_to_REta_from_spec(Y_grid, Z_grid, spec)
                R_arr = out.R_px.numpy()
            Y_arr = Y_grid.numpy(); Z_arr = Z_grid.numpy()
            tol = step * 1.5
            ring_data = []
            for R_pred in self._radii_px:
                msk = np.abs(R_arr - R_pred) < tol
                ring_data.append((Y_arr[msk], Z_arr[msk]) if msk.any() else None)
            self.finished.emit(ring_data)
        except Exception:
            self.failed.emit(traceback.format_exc())


# ═════════════════════════════════════════════════════════════════════════════
#  Tab 4 — Calibration refinement (autograd against integrated profile)
# ═════════════════════════════════════════════════════════════════════════════

class RefinementWorker(QtCore.QThread):
    """Refine geometry by minimising an integrated-profile loss via autograd.

    Default loss is EtaUniformityLoss (rings should be flat in η).  The image is
    normalised to O(1) so the loss is well-conditioned; each refined parameter
    gets a unit-appropriate Adam learning rate; gradients are clipped and a NaN
    guard reverts to the last good geometry.
    """
    progress = QtCore.pyqtSignal(int, int, float, dict)   # step, total, loss, params
    log_line = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal(object)                   # updated AutoCalibrationResult
    failed   = QtCore.pyqtSignal(str)

    def __init__(self, result, image, dark, mask, refine_names, *,
                 loss_kind="eta_uniformity", optimizer="adam", lr=0.5,
                 iters=100, r_bin=2.0, eta_bin=5.0, parent=None):
        super().__init__(parent)
        self._result, self._image, self._dark, self._mask = result, image, dark, mask
        self._names = refine_names
        self._loss_kind, self._optimizer = loss_kind, optimizer
        self._lr, self._iters = lr, iters
        self._r_bin, self._eta_bin = r_bin, eta_bin

    # Typical step per parameter — used to scale the optimiser's search space so
    # every coordinate is O(1) (Nelder-Mead is scale-sensitive).
    # BC_y/BC_z use 0.5 px (not 2 px) for two reasons:
    #   1. η-uniformity is weakly sensitive to BC: shifting the beam centre
    #      translates rings but keeps them circular, producing near-zero
    #      azimuthal-variance signal. Tilts (ty/tz) deform rings into
    #      ellipses → large, unambiguous signal.
    #   2. Hard-bin (floor) assignment creates discrete steps in the loss.
    #      Large BC steps jump many pixels across bin boundaries, turning
    #      the objective into a noisy staircase that misleads Nelder-Mead.
    # Smaller step (0.5 px) + MAX_STEPS=3 → ±1.5 px exploration window.
    _STEP = {"Lsd": 500.0, "BC_y": 0.5, "BC_z": 0.5,
             "ty": 0.1, "tz": 0.1, "tx": 0.1, "Wavelength": 1e-4}
    # Map GUI/spec param name → AutoCalibrationResult attribute
    _ATTR = {"Lsd": "Lsd", "BC_y": "BC_y", "BC_z": "BC_z", "ty": "ty",
             "tz": "tz", "tx": "tx", "Wavelength": "wavelength_A"}

    def run(self):
        try:
            import copy
            import torch
            from scipy.optimize import minimize
            import midas_integrate_v2 as m

            spec = _build_spec(self._result, self._r_bin, self._eta_bin)
            img = self._image.astype(np.float64)
            if self._dark is not None:
                img = np.clip(img - self._dark.astype(np.float64), 0, None)
            # Prepare mask tensor once — passed to the geometry builder each iteration
            # so masked pixels are excluded from both intensity sums AND bin counts.
            # Zeroing in the image alone is not enough: HardBinGeometry still counts
            # those pixels in its normalisation, dragging down the mean and inflating
            # the η-variance, biasing the loss.
            if self._mask is not None:
                img = img.copy(); img[self._mask.astype(bool)] = 0.0
                mask_t = torch.from_numpy(self._mask.astype(np.float32))
                n_bad = int(self._mask.astype(bool).sum())
                self.log_line.emit(
                    f"[refine] mask active: {n_bad:,} px excluded ({100*n_bad/self._mask.size:.2f}%)")
            else:
                mask_t = None
                self.log_line.emit("[refine] mask: none — all pixels included")
            scale = float(np.mean(img[img > 0])) or 1.0
            img_t = torch.from_numpy(img / scale)

            refined = [n for n in self._names if isinstance(getattr(spec, n, None), torch.Tensor)]
            if not refined:
                raise RuntimeError("No refinable parameters selected.")
            base = {n: float(getattr(spec, n).detach()) for n in refined}
            self.log_line.emit(f"[refine] refining {refined} (derivative-free Nelder-Mead)")
            self.log_line.emit(
                f"[refine] start: {', '.join(f'{k}={v:.5g}' for k, v in base.items())}")
            # BC_y/BC_z warning: η-uniformity barely changes when the beam
            # centre shifts (rings translate but stay circular), so the loss
            # landscape is nearly flat in BC.  A small L2 anchor prevents drift.
            bc_indices = [i for i, nm in enumerate(refined) if nm in ("BC_y", "BC_z")]
            if bc_indices:
                self.log_line.emit(
                    "[refine] BC note: η-uniformity has weak sensitivity to BC "
                    "(rings stay circular when centre shifts). "
                    "Step limited to ±0.5 px, L2 anchor active. "
                    "Use Tab 2 calibration for large BC corrections.")

            # MAX_STEPS: maximum search radius in normalised units.
            # Prevents the optimizer from exploring geometry where rings fall outside
            # the integration R-range, which produces an artificially uniform (empty)
            # cake that the objective misidentifies as a perfect minimum.
            # Physical limits: BC ±1.5 px, tilt ±0.3°, Lsd ±1500 µm, λ ±3e-4 Å.
            MAX_STEPS = 3.0

            def _set(x):
                with torch.no_grad():
                    for i, n in enumerate(refined):
                        getattr(spec, n).copy_(torch.tensor(base[n] + x[i] * self._STEP[n]))

            # Track the last real loss and the initial-loss reference for scaling
            # the BC regularisation weight (set after f0 is computed below).
            last_loss = [np.nan]
            f0_ref    = [1.0]   # updated after first evaluation; 0 bc_reg at x0

            def _objective(x):
                # Hard bounds: return a steep penalty without modifying the spec.
                # This keeps the optimizer inside the physically meaningful region.
                if np.any(np.abs(x) > MAX_STEPS):
                    return (np.nan_to_num(last_loss[0], nan=1.0)) * 100 + 1.0
                _set(x)
                geom = build_geom(spec, "hard", mask_t)   # mask excludes bad px from sums AND counts
                int2d = m.integrate_hard(img_t, geom, normalize=True).detach().cpu().numpy()
                int2d = np.nan_to_num(int2d, nan=0.0)
                m_e = int2d.mean(axis=0); v_e = int2d.var(axis=0)
                w = np.clip(m_e, 0, None)
                denom = float((w * w).sum())
                # Guard: if nearly all bins are empty the geometry collapsed rings
                # outside the R-range → this is a degenerate minimum, not a real one.
                if denom < 1e-4:
                    return (np.nan_to_num(last_loss[0], nan=1.0)) * 100 + 1.0
                eta_loss = float((v_e * w).sum() / denom)
                # L2 anchor for BC: 0.2 % of f0 per unit step.  Keeps BC from
                # drifting across the flat η-landscape; allows corrections where
                # the signal genuinely exceeds the regularisation cost (~1.8 % of
                # f0 at the 1.5 px hard limit).  Zero at x=0, so f0 is pure loss.
                bc_reg = (f0_ref[0] * 2e-3) * sum(x[i] ** 2 for i in bc_indices)
                loss = eta_loss + bc_reg
                last_loss[0] = loss
                return loss

            self._eval = 0
            n = len(refined)
            x0 = np.zeros(n)
            f0 = _objective(x0)   # bc_reg = 0 at x0 → f0 = pure η-loss
            f0_ref[0] = f0        # now regularisation weight is set
            self.log_line.emit(f"[refine] initial loss = {f0:.6g}")
            self.progress.emit(0, self._iters, f0, dict(base))

            def _cb(xk):
                self._eval += 1
                # Use the cached loss — do NOT re-call _objective here, that
                # would double the evaluation count and corrupt the spec state
                # between the optimizer's internal steps.
                params = {nm: base[nm] + xk[i] * self._STEP[nm]
                          for i, nm in enumerate(refined)}
                self.progress.emit(min(self._eval, self._iters), self._iters,
                                   np.nan_to_num(last_loss[0], nan=f0), params)

            # Symmetric simplex: explore both + and − directions so the optimizer
            # does not have to reflect past x0 before it can search all of them.
            rows = [x0]
            for i in range(n):
                v = x0.copy(); v[i] =  1.5; rows.append(v)
            # For n > 1, add a few negative-direction vertices within the n+1 limit.
            for i in range(min(n, n)):
                v = x0.copy(); v[i] = -0.75; rows.append(v)
            simplex = np.array(rows[:n + 1])

            # maxiter: each Nelder-Mead iteration is 1-4 evaluations; for n ≥ 4
            # convergence typically needs 200-500 iterations.  Use at least 400.
            res = minimize(_objective, x0, method="Nelder-Mead", callback=_cb,
                           options={"maxiter": max(self._iters, 400),
                                    "initial_simplex": simplex,
                                    "xatol": 1e-3, "fatol": 1e-5, "disp": False})

            # Safety: if the optimiser returned a worse geometry, revert.
            if res.fun > f0 * 1.05:
                self.log_line.emit(
                    f"[refine] WARN: optimised loss ({res.fun:.5g}) is worse than "
                    f"starting loss ({f0:.5g}). Reverting to original geometry.")
                self.finished.emit(copy.copy(self._result))
                return

            _set(res.x)
            final = {name: base[name] + res.x[i] * self._STEP[name]
                     for i, name in enumerate(refined)}
            self.log_line.emit(
                f"[refine] final loss={res.fun:.6g}  ({res.nfev} evals, {res.nit} iters)"
                f"  converged={res.success}")
            self.log_line.emit(
                f"[refine] Δ: {', '.join(f'{nm}={final[nm]-base[nm]:+.4g}' for nm in refined)}")

            new = copy.copy(self._result)
            for name, attr in self._ATTR.items():
                if name in refined:
                    setattr(new, attr, final[name])
            new._calibrant_name = getattr(self._result, "_calibrant_name", "CeO2")
            self.finished.emit(new)
        except Exception:
            self.failed.emit(traceback.format_exc())


# ═════════════════════════════════════════════════════════════════════════════
#  Tab 4b — Profile comparison (before/after refinement)
# ═════════════════════════════════════════════════════════════════════════════

class RefineCompareWorker(QtCore.QThread):
    """Integrate one frame with both the original and refined calibration results.

    Returns profiles on a common R axis so the tab can overlay them and show
    the difference curve.
    """
    finished = QtCore.pyqtSignal(object)   # dict: r_axis_px, profile_orig, profile_refined
    failed   = QtCore.pyqtSignal(str)

    def __init__(self, orig_result, refined_result, image, mask=None,
                 r_bin=2.0, eta_bin=5.0, parent=None):
        super().__init__(parent)
        self._orig = orig_result
        self._refined = refined_result
        self._image = image
        self._mask = mask
        self._r_bin = r_bin
        self._eta_bin = eta_bin

    def run(self):
        try:
            import torch
            mask_t = torch.from_numpy(self._mask.astype(np.float32)) if self._mask is not None else None
            img_t = torch.from_numpy(self._image.astype(np.float64))
            profiles, r_axes = [], []
            for res in (self._orig, self._refined):
                spec = _build_spec(res, self._r_bin, self._eta_bin)
                geom = build_geom(spec, "subpixel2", mask_t)
                prof, _ = integrate_frame(img_t, spec, geom, "subpixel2",
                                          (None, None), None, need_sigma=False)
                profiles.append(prof)
                r_axes.append(compute_r_axis(spec))
            # Interpolate both onto the overlapping R range so subtraction is valid
            r0, r1 = r_axes
            r_min = max(float(r0.min()), float(r1.min()))
            r_max = min(float(r0.max()), float(r1.max()))
            n_bins = min(len(r0), len(r1))
            r_common = np.linspace(r_min, r_max, n_bins)
            p_orig = np.interp(r_common, r0, profiles[0])
            p_ref  = np.interp(r_common, r1, profiles[1])
            self.finished.emit({
                "r_axis_px": r_common,
                "profile_orig": p_orig,
                "profile_refined": p_ref,
            })
        except Exception:
            self.failed.emit(traceback.format_exc())


# ═════════════════════════════════════════════════════════════════════════════
#  Tab 5 — Corrections & Physics preview (single frame)
# ═════════════════════════════════════════════════════════════════════════════

def _parse_composition(text: str) -> dict:
    """Parse 'Ce:1,O:2' → {'Ce':1.0,'O':2.0}."""
    out = {}
    for tok in text.replace(";", ",").split(","):
        tok = tok.strip()
        if not tok:
            continue
        if ":" in tok:
            el, frac = tok.split(":")
            out[el.strip()] = float(frac)
        else:
            out[tok] = 1.0
    return out


class CorrectionPreviewWorker(QtCore.QThread):
    """Integrate one frame with and without the selected corrections so the user
    can see the effect of each before committing to a batch run.

    Pixel-domain (via integrate_with_corrections): polarization, solid angle,
    empty subtraction.  Profile-domain: cylindrical absorption (÷ transmission),
    Compton subtraction (− incoherent intensity).
    """
    log_line = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal(object)
    failed   = QtCore.pyqtSignal(str)

    def __init__(self, result, image, dark, mask, cfg, parent=None):
        super().__init__(parent)
        self._result, self._image, self._dark, self._mask = result, image, dark, mask
        self._cfg = cfg

    def run(self):
        try:
            import torch
            import midas_integrate_v2 as m
            c = self._cfg
            spec = _build_spec(self._result, c.get("r_bin", 1.0), c.get("eta_bin", 5.0))
            img = self._image.astype(np.float64)
            if self._dark is not None:
                img = np.clip(img - self._dark.astype(np.float64), 0, None)
            if self._mask is not None:
                img = img.copy(); img[self._mask.astype(bool)] = 0.0
            img_t = torch.from_numpy(img)
            lsd, px, wl = float(spec.Lsd), float(spec.pxY), float(spec.Wavelength)

            # Uncorrected reference
            geom = build_geom(spec, "subpixel2", None)
            prof_unc, _ = integrate_frame(img_t, spec, geom, "subpixel2",
                                          (None, None), None, need_sigma=False)

            # Pixel-domain corrections
            pol = sa = empty = None
            if c.get("polarization"):
                pol = m.PolarizationCorrection(
                    pol_fraction=c["polarization"]["frac"],
                    pol_plane_eta_deg=c["polarization"]["plane"])
            if c.get("solid_angle"):
                sa = m.SolidAngleCorrection()
            if c.get("empty"):
                ev = c["empty"]
                empty_img = _load_image(ev["path"]).astype(np.float64)
                empty = m.EmptySubtraction(torch.from_numpy(empty_img),
                                           scale=ev.get("scale", 1.0))
            cake = m.integrate_with_corrections(
                img_t, spec, polarization=pol, solid_angle=sa,
                empty_subtraction=empty).detach().cpu().numpy()
            # integrate_with_corrections is unnormalised → divide by the pixel-count cake
            counts = corrections_counts(spec)
            with np.errstate(invalid="ignore", divide="ignore"):
                cake = np.where(counts > 0.5, cake / counts, np.nan)
            prof = _profile_from_cake(cake)

            r_ax = compute_r_axis(spec)
            two_theta = np.degrees(np.arctan(r_ax * px / lsd))
            q = 4 * math.pi * np.sin(np.radians(two_theta) / 2) / wl

            # Profile-domain corrections
            if c.get("absorption"):
                T = m.CylindricalAbsorption(mu_R=c["absorption"]["mu_R"]) \
                    (torch.from_numpy(np.radians(two_theta))).detach().cpu().numpy()
                T = np.clip(T, 1e-6, None)
                prof = prof / T
                self.log_line.emit(f"[corr] absorption μR={c['absorption']['mu_R']} "
                                   f"T range [{T.min():.3f},{T.max():.3f}]")
            if c.get("compton"):
                comp_cfg = c["compton"]
                comp = m.ComptonSubtraction(_parse_composition(comp_cfg["composition"]),
                                            wavelength_A=wl) \
                    (torch.from_numpy(q)).detach().cpu().numpy()
                comp = comp * comp_cfg.get("scale", 1.0)
                prof = prof - comp
                self.log_line.emit("[corr] Compton subtracted "
                                   f"(max {comp.max():.3g})")

            with np.errstate(divide="ignore", invalid="ignore"):
                factor = np.where(prof_unc > 0, prof / prof_unc, np.nan)

            self.finished.emit({
                "r_axis_px": r_ax, "profile_unc": prof_unc, "profile_corr": prof,
                "factor": factor, "two_theta": two_theta, "q": q,
                "wavelength_A": wl, "lsd_um": lsd, "px_um": px,
            })
        except Exception:
            self.failed.emit(traceback.format_exc())


# ═════════════════════════════════════════════════════════════════════════════
#  Tab 6 — PDF analysis (image → I(Q) → G(r))
# ═════════════════════════════════════════════════════════════════════════════

class PDFWorker(QtCore.QThread):
    """Compute I(Q), an estimated background, and the pair-distribution G(r)."""
    log_line = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal(object)
    failed   = QtCore.pyqtSignal(str)

    def __init__(self, result, image, dark, mask, cfg, parent=None):
        super().__init__(parent)
        self._result, self._image, self._dark, self._mask = result, image, dark, mask
        self._cfg = cfg

    def run(self):
        try:
            import torch
            import midas_integrate_v2 as m
            c = self._cfg
            spec = _build_spec(self._result, 1.0, c.get("eta_bin", 5.0))
            img = self._image.astype(np.float64)
            if self._dark is not None:
                img = np.clip(img - self._dark.astype(np.float64), 0, None)
            if self._mask is not None:
                img = img.copy(); img[self._mask.astype(bool)] = 0.0
            img_t = torch.from_numpy(img)
            lsd, px, wl = float(spec.Lsd), float(spec.pxY), float(spec.Wavelength)

            # I(Q): hard-binned 1-D profile mapped to Q
            geom = build_geom(spec, "hard", None)
            prof, _ = integrate_frame(img_t, spec, geom, "hard", (None, None), None,
                                      need_sigma=False)
            r_ax = compute_r_axis(spec)
            _, _, q = axis_conversions(r_ax, lsd, px, wl)
            bg = m.pdf.estimate_background(prof, window=c.get("bg_window", 51),
                                          percentile=c.get("bg_percentile", 10.0))
            bg = np.asarray(bg.detach() if hasattr(bg, "detach") else bg)

            # F(Q) = Q·(I(Q)/bg(Q) − 1)  — reduced structure factor (no form-factor,
            # composition-free approximation; useful for visualising ring positions)
            with np.errstate(divide="ignore", invalid="ignore"):
                Fq = q * np.where(bg > 1e-3 * bg.max(), prof / bg - 1.0, 0.0)
            Fq = np.nan_to_num(Fq, nan=0.0)

            # G(r) via the full PDF pipeline
            self.log_line.emit("[pdf] computing G(r)…")
            r_grid = torch.from_numpy(np.arange(c["r_min"], c["r_max"], c["r_step"]))
            gr_out = m.pdf.integrate_to_Gr_with_variance(
                img_t, spec, r_grid, binning=c.get("binning", "hard"),
                Q_min=c["q_min"], Q_max=c["q_max"], Q_step=c["q_step"],
                window=c.get("window", "lorch"))
            r_np = np.asarray(gr_out[0])
            gr_np = np.asarray(gr_out[1].detach() if hasattr(gr_out[1], "detach") else gr_out[1])
            sig_np = np.asarray(gr_out[2].detach() if hasattr(gr_out[2], "detach") else gr_out[2])

            self.finished.emit({
                "q": q, "Iq": prof, "background": bg, "Fq": Fq,
                "r": r_np, "Gr": gr_np, "sigma_Gr": sig_np,
            })
        except Exception:
            self.failed.emit(traceback.format_exc())


# ═════════════════════════════════════════════════════════════════════════════
#  Tab 7 — Texture / pole figure (per-ring azimuthal extraction)
# ═════════════════════════════════════════════════════════════════════════════

class PoleFigureWorker(QtCore.QThread):
    """Integrate one frame to an (η, R) cake, then map a selected ring to a
    stereographic pole figure and extract its azimuthal intensity I(η)."""
    log_line = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal(object)
    failed   = QtCore.pyqtSignal(str)

    def __init__(self, result, image, dark, mask, cfg, parent=None):
        super().__init__(parent)
        self._result, self._image, self._dark, self._mask = result, image, dark, mask
        self._cfg = cfg

    def run(self):
        try:
            import torch
            import midas_integrate_v2 as m
            c = self._cfg
            spec = _build_spec(self._result, c.get("r_bin", 2.0), c.get("eta_bin", 2.0))
            img = self._image.astype(np.float64)
            if self._dark is not None:
                img = np.clip(img - self._dark.astype(np.float64), 0, None)
            mask_t = self._mask.astype(np.float32) if self._mask is not None else None
            geom = build_geom(spec, "subpixel2", mask_t)
            cake = m.integrate_subpixel(torch.from_numpy(img), geom, normalize=True)
            cake = np.nan_to_num(cake.detach().cpu().numpy(), nan=0.0)
            n_eta = cake.shape[0]
            eta_axis = spec.EtaMin + spec.EtaBinSize * (np.arange(n_eta) + 0.5)
            r_axis = compute_r_axis(spec)

            ring = float(c["ring_px"]); cap = float(c.get("capture_px", 4.0))
            alpha, beta, inten = m.texture.cake_to_pole_figure(
                cake, eta_axis, r_axis, hkl_R_px=ring, capture_radius_px=cap,
                sample_rotation_chi_deg=c.get("chi", 0.0),
                sample_rotation_phi_deg=c.get("phi", 0.0))

            # I(η) at the ring: mean over R bins within the capture window
            sel = np.abs(r_axis - ring) <= cap
            i_eta = cake[:, sel].mean(axis=1) if sel.any() else cake.mean(axis=1)

            self.finished.emit({
                "alpha": np.asarray(alpha), "beta": np.asarray(beta),
                "intensity": np.asarray(inten), "eta_axis": eta_axis, "i_eta": i_eta,
                "ring_px": ring,
            })
        except Exception:
            self.failed.emit(traceback.format_exc())


# ═════════════════════════════════════════════════════════════════════════════
#  Learnable gain worker (Tab 5 — per-pixel spatial gain drift recovery)
# ═════════════════════════════════════════════════════════════════════════════

class LearnableGainWorker(QtCore.QThread):
    """Train a per-pixel LearnableGain module against a reference profile.

    Workflow (notebook 16):
      1. Integrate the reference (clean) frame → target cake.
      2. Each step: divide the drifted frame by the current gain estimate,
         integrate, measure MSE vs target, add priors, back-propagate.
      3. After convergence, extract the gain map and stats.

    The gain model is ``g_i = 1 + scale · r_i``.  With ``scale=0.1``,
    a raw parameter of ±1 corresponds to ±10 % gain drift — generous for
    typical detector behaviour.  ``gain_unity_prior`` anchors the mean
    close to 1 (removes the global-scale gauge ambiguity); ``gain_smoothness_prior``
    penalises high spatial frequencies (gain drift is physically smooth).
    """
    progress = QtCore.pyqtSignal(int, int, float)   # step, total, loss
    log_line = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal(object)             # dict with gain_map, stats
    failed   = QtCore.pyqtSignal(str)

    def __init__(self, result, ref_image, drifted_image, mask, cfg, parent=None):
        super().__init__(parent)
        self._result   = result
        self._ref      = ref_image        # clean / reference frame (train target)
        self._drifted  = drifted_image    # frame with suspected gain drift
        self._mask     = mask
        self._cfg      = cfg

    def run(self):
        try:
            import copy, torch
            import midas_integrate_v2 as m

            c = self._cfg
            spec = _build_spec(self._result, c.get("r_bin", 1.0), c.get("eta_bin", 5.0))
            NZ, NY = spec.NrPixelsZ, spec.NrPixelsY

            ref  = self._ref.astype(np.float64)
            drif = self._drifted.astype(np.float64)
            if self._mask is not None:
                ref  = ref.copy();  ref[self._mask.astype(bool)]  = 0.0
                drif = drif.copy(); drif[self._mask.astype(bool)] = 0.0

            ref_t  = torch.from_numpy(ref)
            drif_t = torch.from_numpy(drif)

            # Integrate the reference frame once → training target
            self.log_line.emit("[gain] integrating reference frame → target…")
            target = m.integrate_with_corrections(ref_t, spec).detach()

            # Initialise learnable gain (centred on 1, scale = 10 %/unit)
            gain = m.LearnableGain(
                NrPixelsZ=int(NZ), NrPixelsY=int(NY),
                scale=float(c.get("gain_scale", 0.1)))
            unity_w    = float(c.get("unity_weight", 1e-4))
            smooth_w   = float(c.get("smoothness_weight", 1e-3))
            lr         = float(c.get("lr", 0.02))
            n_steps    = int(c.get("n_steps", 100))

            opt = torch.optim.Adam(gain.parameters(), lr=lr)
            self.log_line.emit(
                f"[gain] training {n_steps} steps  lr={lr}  "
                f"unity_w={unity_w}  smooth_w={smooth_w}")

            for step in range(n_steps):
                opt.zero_grad()
                g = gain().clamp(min=1e-6)          # current per-pixel gain map
                adjusted = drif_t / g               # remove the drift
                out = m.integrate_with_corrections(adjusted, spec)
                data_loss = (out - target).pow(2).mean()
                loss = (data_loss
                        + unity_w  * m.gain_unity_prior(gain)
                        + smooth_w * m.gain_smoothness_prior(gain))
                loss.backward()
                opt.step()

                loss_f = float(loss.detach())
                self.progress.emit(step + 1, n_steps, loss_f)
                if step % max(1, n_steps // 10) == 0 or step == n_steps - 1:
                    self.log_line.emit(
                        f"[gain] step {step+1:4d}/{n_steps}  "
                        f"loss={loss_f:.5g}  data={float(data_loss):.5g}")

            gain_map = gain.extract_gain_map()
            n_drifted = int(gain.n_drifted_pixels(threshold=float(c.get("drift_threshold", 0.01))))
            self.log_line.emit(
                f"[gain] done — gain range [{gain_map.min():.4f}, {gain_map.max():.4f}]  "
                f"drifted>{c.get('drift_threshold', 0.01)*100:.0f}%: {n_drifted:,} px")
            self.finished.emit({
                "gain_map": gain_map,
                "n_drifted": n_drifted,
                "gain_min": float(gain_map.min()),
                "gain_max": float(gain_map.max()),
                "gain_mean": float(gain_map.mean()),
            })
        except Exception:
            self.failed.emit(traceback.format_exc())


# ═════════════════════════════════════════════════════════════════════════════
#  Drift trajectory worker (Tab 3 — long-scan geometry drift correction)
# ═════════════════════════════════════════════════════════════════════════════

def _spec_from_trajectory(base_spec, traj, frame_abs_idx: int):
    """Return a deepcopy of base_spec with Lsd/BC_y/BC_z from the drift trajectory.

    Linear interpolation is used so frame indices between knots get smooth values.
    """
    import copy, torch
    Lsd_v = float(np.interp(frame_abs_idx, traj.frame_indices, traj.Lsd_t))
    BCy_v = float(np.interp(frame_abs_idx, traj.frame_indices, traj.BC_y_t))
    BCz_v = float(np.interp(frame_abs_idx, traj.frame_indices, traj.BC_z_t))
    s = copy.deepcopy(base_spec)
    with torch.no_grad():
        s.Lsd.copy_(torch.tensor(Lsd_v, dtype=s.Lsd.dtype))
        s.BC_y.copy_(torch.tensor(BCy_v, dtype=s.BC_y.dtype))
        s.BC_z.copy_(torch.tensor(BCz_v, dtype=s.BC_z.dtype))
    return s


class DriftWorker(QtCore.QThread):
    """Fit a per-frame geometry drift trajectory from calibrant anchor frames.

    The function ``fit_drift_trajectory`` (midas_integrate_v2.pipelines.drift)
    fits Lsd(t), BC_y(t), BC_z(t) as B-splines via L-BFGS with an optional
    Laplace-approx σ estimate.  The result is a ``DriftTrajectory`` dataclass.

    Inputs
    ------
    anchor_frames : dict  {frame_idx: {"Lsd": float, "BC_y": float, "BC_z": float}}
        Known-good geometry values at calibrant exposure indices.
    sample_indices : list of int
        Frame indices for sample data (geometry will be interpolated here).
    base_result : AutoCalibrationResult
        Used to build the base IntegrationSpec.
    cfg : dict
        parametrization ('spline'|'linear'|'constant'), n_knots, bayesian_sigma.
    """
    log_line = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal(object)    # DriftTrajectory
    failed   = QtCore.pyqtSignal(str)

    def __init__(self, result, anchor_frames, sample_indices, cfg, parent=None):
        super().__init__(parent)
        self._result   = result
        self._anchors  = anchor_frames   # {int: {"Lsd":..., "BC_y":..., "BC_z":...}}
        self._samples  = sample_indices  # list of int
        self._cfg      = cfg

    def run(self):
        try:
            from midas_integrate_v2.pipelines.drift import fit_drift_trajectory
            c = self._cfg
            spec = _build_spec(self._result, 2.0, 5.0)   # geometry only; bins don't matter
            self.log_line.emit(
                f"[drift] fitting trajectory — {len(self._anchors)} anchors, "
                f"{len(self._samples)} sample frames  "
                f"param={c.get('parametrization','spline')}  "
                f"knots={c.get('n_knots', 5)}")
            traj = fit_drift_trajectory(
                self._anchors,
                self._samples,
                spec,
                parametrization=c.get("parametrization", "spline"),
                n_knots=int(c.get("n_knots", 5)),
                bayesian_sigma=bool(c.get("bayesian_sigma", True)),
            )
            self.log_line.emit(
                f"[drift] done — Lsd [{traj.Lsd_t.min():.1f}, {traj.Lsd_t.max():.1f}] µm  "
                f"BC_y [{traj.BC_y_t.min():.3f}, {traj.BC_y_t.max():.3f}]  "
                f"BC_z [{traj.BC_z_t.min():.3f}, {traj.BC_z_t.max():.3f}]")
            self.finished.emit(traj)
        except Exception:
            self.failed.emit(traceback.format_exc())
