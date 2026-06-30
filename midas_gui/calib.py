"""Calibration pipeline layer.

The GUI offers several calibration pipelines, but only ``calibrate()`` and
``first_time_calibrate()`` accept a raw image.  The other pipelines
(``autocalibrate_four_stage`` etc.) take a pre-built ``V1Params`` object plus an
explicit seed, and each returns a *different* result type whose geometry lives in
a different attribute.

This module hides that heterogeneity behind two functions:

* :func:`run_pipeline` — dispatch on a mode string, returning whatever the
  underlying pipeline returns.
* :func:`normalize_result` — convert any pipeline output into a real
  :class:`AutoCalibrationResult`, so the rest of the GUI (spec building, ring
  drawing, paramstest export, results display) never needs to know which
  pipeline ran.

The unpacked-dict → AutoCalibrationResult mapping mirrors the canonical wrapping
in ``midas_calibrate_v2/pipelines/auto.py`` (the body of ``calibrate()``).
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

import midas_gui._paths  # noqa: F401  (sys.path setup must run before MIDAS imports)
from midas_gui.constants import _SG, _LC, DISTORTION_NAMES, DEFAULT_LSD_UM


# ── Seed ────────────────────────────────────────────────────────────────────────

def make_seed_safe(image: np.ndarray, wavelength: float, pxY: float,
                   calibrant: str):
    """Run the automatic seeder; return a Seed or None on failure.

    use_diplib=False is mandatory: diplib's median filter segfaults on macOS
    (the package's own one_shot pipeline disables it for the same reason, and a
    native segfault is not catchable by the try/except inside make_seed).
    """
    try:
        from midas_calibrate_v2.seed.auto_seed import make_seed
        return make_seed(image.astype(np.float32), wavelength_A=wavelength,
                         px_um=pxY, calibrant=calibrant, use_diplib=False)
    except Exception:
        return None


def _refine_dict(refine: dict) -> dict:
    """Translate the GUI refine flags into a v1 ``Refine`` dict.

    GUI flags: Lsd, BC, ty, tz, tx, Wavelength, Distortion.
    The 15 distortion coefficients (p0..p14) share the single Distortion flag.
    """
    dist = bool(refine.get("Distortion", True))
    d = {
        "Lsd":        bool(refine.get("Lsd", True)),
        "BC":         bool(refine.get("BC", True)),
        "ty":         bool(refine.get("ty", True)),
        "tz":         bool(refine.get("tz", True)),
        "Wavelength": bool(refine.get("Wavelength", False)),
        "Parallax":   False,
    }
    for i in range(15):
        d[f"p{i}"] = dist
    return d


def build_v1_params(seed, *, wavelength, pxY, pxZ, calibrant, NY, NZ,
                    refine: dict, n_iter: int, device: str,
                    min_ring_px: float = 120.0, max_ring_px: Optional[float] = None):
    """Build a CalibrationParams (V1Params) from a seed.

    Mirrors the construction in ``pipelines/auto.py`` — RhoD is the BC-to-farthest
    -corner distance expressed in µm.
    """
    from midas_calibrate.params import CalibrationParams

    bc_y, bc_z = float(seed["BC_y"]), float(seed["BC_z"])
    lsd = float(seed["Lsd"])
    pxZ = pxZ or pxY
    rho_px = math.sqrt(max(bc_y, NY - bc_y) ** 2 + max(bc_z, NZ - bc_z) ** 2)
    if max_ring_px is None:
        max_ring_px = rho_px * 0.97
    a, b, c, alpha, beta, gamma = _LC.get(calibrant, _LC["CeO2"])

    v1 = CalibrationParams(
        NrPixelsY=NY, NrPixelsZ=NZ, pxY=pxY, pxZ=pxZ,
        Lsd=lsd, BC_y=bc_y, BC_z=bc_z, tx=0.0, ty=0.0, tz=0.0,
        Wavelength=wavelength,
        SpaceGroup=_SG.get(calibrant, 225),
        LatticeConstant=(a, b, c, alpha, beta, gamma),
        RhoD=rho_px * pxY, MaxRingRad=max_ring_px, MinRingRad=min_ring_px,
        nIterations=n_iter, Refine=_refine_dict(refine),
        Device=device, Dtype="fp64",
    )
    v1.validate()
    return v1


# ── Normalisation: any pipeline output → AutoCalibrationResult ───────────────────

_PANEL_KEYS = ("panel_delta_yz", "panel_delta_theta", "panel_delta_lsd", "panel_delta_p2")


def _extract_panel_unpacked(unpacked: dict) -> dict:
    """Pull panel delta tensors out of an unpacked dict (empty if none present)."""
    return {k: v for k, v in unpacked.items() if k in _PANEL_KEYS}


def _auto_result_from_unpacked(u: dict, *, NY, NZ, pxY, pxZ, wavelength,
                               strain=None, residual_map=None,
                               residual_bin_path=None):
    from midas_calibrate_v2 import AutoCalibrationResult
    pxZ = pxZ or pxY
    distortion = {n: float(u[n]) for n in DISTORTION_NAMES if n in u}
    return AutoCalibrationResult(
        Lsd=float(u["Lsd"]),
        BC_y=float(u["BC_y"]), BC_z=float(u["BC_z"]),
        tx=float(u.get("tx", 0.0)),
        ty=float(u["ty"]), tz=float(u["tz"]),
        distortion=distortion,
        pxY=pxY, pxZ=pxZ, NrPixelsY=NY, NrPixelsZ=NZ,
        wavelength_A=wavelength,
        post_residual_strain_uE=strain,
        residual_corr_map=residual_map,
        residual_corr_bin_path=residual_bin_path,
    )


def normalize_result(raw, mode: str, *, NY, NZ, pxY, pxZ, wavelength):
    """Return an AutoCalibrationResult regardless of which pipeline produced raw.

    When panel_layout was used, the refined panel shifts (panel_delta_yz /
    panel_delta_theta) are attached as ``result._panel_unpacked`` so the save
    dialog can write a companion panel_shifts.txt.  For one_shot + panel_layout,
    run_pipeline internally routes through autocalibrate_four_stage (which
    exposes stage2.unpacked); we detect this by checking for a ``.stage2``
    attribute on the raw result.
    """
    # one_shot+panel_layout was re-routed through four_stage to expose unpacked
    effective_mode = "four_stage" if (mode == "one_shot" and hasattr(raw, "stage2")) else mode

    if effective_mode == "one_shot":
        return raw   # calibrate() already returns AutoCalibrationResult, no panel data

    if effective_mode == "first_time":
        pv = raw.result
        strain = (pv.history[-1].mean_strain_uE
                  if getattr(pv, "history", None) else None)
        return _auto_result_from_unpacked(
            pv.unpacked, NY=NY, NZ=NZ, pxY=pxY, pxZ=pxZ,
            wavelength=wavelength, strain=strain)

    if effective_mode == "four_stage":
        pv = raw.stage2   # final geometry stage (PVCalibrationResult)
        strain = getattr(raw, "stage4_strain_uE", None)
        result = _auto_result_from_unpacked(
            pv.unpacked, NY=NY, NZ=NZ, pxY=pxY, pxZ=pxZ,
            wavelength=wavelength, strain=strain)
        panel_u = _extract_panel_unpacked(pv.unpacked)
        if panel_u:
            result._panel_unpacked = panel_u
        return result

    if effective_mode == "bayesian":
        result = _auto_result_from_unpacked(
            raw.map_unpacked, NY=NY, NZ=NZ, pxY=pxY, pxZ=pxZ, wavelength=wavelength)
        panel_u = _extract_panel_unpacked(raw.map_unpacked)
        if panel_u:
            result._panel_unpacked = panel_u
        lap = getattr(raw, "laplace", None)
        if lap is not None:
            names = list(getattr(lap, "refined_names", []) or [])
            sig = getattr(lap, "sigma_per_dim", None)
            if sig is not None:
                result._laplace_sigma = {n: float(s) for n, s in zip(names, sig)}
        return result

    if effective_mode == "joint":
        result = _auto_result_from_unpacked(
            raw.map_unpacked, NY=NY, NZ=NZ, pxY=pxY, pxZ=pxZ, wavelength=wavelength)
        panel_u = _extract_panel_unpacked(raw.map_unpacked)
        if panel_u:
            result._panel_unpacked = panel_u
        return result

    raise ValueError(f"Unsupported pipeline mode for normalisation: {effective_mode}")


# ── Dispatch ─────────────────────────────────────────────────────────────────────

def run_pipeline(mode: str, image: np.ndarray, dark, cfg: dict):
    """Run the requested calibration pipeline.

    cfg keys: wavelength, pxY, pxZ, calibrant, refine (dict), n_iter,
    lm_max_iter, device, build_residual_corr, im_trans, output_dir, and an
    optional manual seed {"BC_y","BC_z","Lsd"}.

    Returns the raw pipeline result; call :func:`normalize_result` on it.
    """
    wavelength = cfg["wavelength"]
    pxY        = cfg["pxY"]
    pxZ        = cfg.get("pxZ")
    calibrant  = cfg["calibrant"]
    refine     = cfg.get("refine", {})
    n_iter     = int(cfg.get("n_iter", 4))
    lm_iter    = int(cfg.get("lm_max_iter", 200))
    device     = cfg.get("device", "cpu")
    im_trans   = tuple(cfg.get("im_trans", ()))
    manual     = cfg.get("manual_seed")   # None or {"BC_y","BC_z","Lsd"}
    NZ, NY     = image.shape
    panel_layout = _build_panel_layout(cfg.get("panel_layout"))

    if mode == "one_shot":
        if panel_layout is not None:
            # calibrate() runs panel refinement internally but drops panel_delta_*
            # before returning AutoCalibrationResult.  Route through
            # autocalibrate_four_stage instead so stage2.unpacked retains the
            # refined panel shifts; normalize_result detects the FourStageResult
            # via its .stage2 attribute and handles it correctly.
            if manual:
                seed = {"BC_y": manual["BC_y"], "BC_z": manual["BC_z"],
                        "Lsd": manual["Lsd"]}
            else:
                s = make_seed_safe(image, wavelength, pxY, calibrant)
                if s is None:
                    raise RuntimeError(
                        "Auto-seed failed for panel calibration (one_shot). "
                        "Enable manual seed (Pick BC / Pick Ring + Lsd) and retry.")
                seed = {"BC_y": s.BC_y, "BC_z": s.BC_z, "Lsd": s.Lsd_um}
            v1 = build_v1_params(
                seed, wavelength=wavelength, pxY=pxY, pxZ=pxZ, calibrant=calibrant,
                NY=NY, NZ=NZ, refine=refine, n_iter=n_iter, device=device)
            img = image.astype(np.float32)
            if im_trans:
                from midas_gui.helpers import _apply_im_trans
                img = _apply_im_trans(img, im_trans)
            from midas_calibrate_v2.pipelines import autocalibrate_four_stage
            return autocalibrate_four_stage(v1, img, dark=dark, device=device,
                                            panel_layout=panel_layout, verbose=True)

        from midas_calibrate_v2 import calibrate
        kwargs = dict(
            wavelength=wavelength, pxY=pxY, dark=dark, calibrant=calibrant,
            output_dir=cfg.get("output_dir"),
            build_residual_corr=bool(cfg.get("build_residual_corr", True)),
            n_iter=n_iter, lm_max_iter=lm_iter, device=device, verbose=True,
            refine_tilts=bool(refine.get("ty", True) or refine.get("tz", True)),
            refine_distortion=bool(refine.get("Distortion", True)),
        )
        if pxZ:
            kwargs["pxZ"] = pxZ
        if im_trans:
            kwargs["im_trans"] = im_trans
        # BC + Lsd seed must be supplied together (see bugs_and_fixes Bug 5)
        if manual:
            kwargs["initial_BC_y"] = manual["BC_y"]
            kwargs["initial_BC_z"] = manual["BC_z"]
            kwargs["initial_Lsd"]  = manual["Lsd"]
        return calibrate(image, **kwargs)

    if mode == "first_time":
        from midas_calibrate_v2.pipelines import first_time_calibrate
        a, b, c, alpha, beta, gamma = _LC.get(calibrant, _LC["CeO2"])
        return first_time_calibrate(
            image,
            lattice=(a, b, c, alpha, beta, gamma),
            space_group=_SG.get(calibrant, 225),
            wavelength_A=wavelength,
            pixel_size_um=pxY,
            n_pixels_y=NY, n_pixels_z=NZ,
            lsd_initial_guess_um=(manual["Lsd"] if manual else DEFAULT_LSD_UM),
            bc_initial_guess=((manual["BC_y"], manual["BC_z"]) if manual else None),
            dark=dark,
        )

    if mode == "four_stage":
        from midas_calibrate_v2.pipelines import autocalibrate_four_stage
        if manual:
            seed = {"BC_y": manual["BC_y"], "BC_z": manual["BC_z"], "Lsd": manual["Lsd"]}
        else:
            s = make_seed_safe(image, wavelength, pxY, calibrant)
            if s is None:
                raise RuntimeError(
                    "Auto-seed failed for four-stage pipeline. "
                    "Enable manual seed (Pick BC / Pick Ring + Lsd) and retry.")
            seed = {"BC_y": s.BC_y, "BC_z": s.BC_z, "Lsd": s.Lsd_um}
        v1 = build_v1_params(
            seed, wavelength=wavelength, pxY=pxY, pxZ=pxZ, calibrant=calibrant,
            NY=NY, NZ=NZ, refine=refine, n_iter=n_iter, device=device)
        img = image.astype(np.float32)
        if im_trans:
            from midas_gui.helpers import _apply_im_trans
            img = _apply_im_trans(img, im_trans)
        return autocalibrate_four_stage(v1, img, dark=dark, device=device,
                                        panel_layout=panel_layout, verbose=True)

    if mode in ("bayesian", "joint"):
        v1 = _seed_and_v1(image, wavelength, pxY, pxZ, calibrant, NY, NZ,
                          refine, n_iter, device, manual)
        img = image.astype(np.float32)
        if im_trans:
            from midas_gui.helpers import _apply_im_trans
            img = _apply_im_trans(img, im_trans)
        if mode == "bayesian":
            from midas_calibrate_v2.pipelines import autocalibrate_bayesian
            return autocalibrate_bayesian(v1, img, mode="laplace", dark=dark,
                                          panel_layout=panel_layout)
        from midas_calibrate_v2.pipelines import autocalibrate_joint
        return autocalibrate_joint(v1, img, dark=dark, panel_layout=panel_layout)

    raise ValueError(f"Unknown pipeline mode: {mode}")


def _seed_and_v1(image, wavelength, pxY, pxZ, calibrant, NY, NZ,
                 refine, n_iter, device, manual):
    """Seed (manual or auto) → build_v1_params. Shared by advanced pipelines."""
    if manual:
        seed = {"BC_y": manual["BC_y"], "BC_z": manual["BC_z"], "Lsd": manual["Lsd"]}
    else:
        s = make_seed_safe(image, wavelength, pxY, calibrant)
        if s is None:
            raise RuntimeError(
                "Auto-seed failed. Enable manual seed (Pick BC / Pick Ring + Lsd).")
        seed = {"BC_y": s.BC_y, "BC_z": s.BC_z, "Lsd": s.Lsd_um}
    return build_v1_params(
        seed, wavelength=wavelength, pxY=pxY, pxZ=pxZ, calibrant=calibrant,
        NY=NY, NZ=NZ, refine=refine, n_iter=n_iter, device=device)


def _build_panel_layout(cfg):
    """Build a PanelLayout.regular from a config dict, or None."""
    if not cfg:
        return None
    from midas_calibrate_v2.forward.panels import PanelLayout
    return PanelLayout.regular(
        int(cfg["n_y"]), int(cfg["n_z"]),
        int(cfg["sy"]), int(cfg["sz"]),
        gap_y=int(cfg.get("gap_y", 0)), gap_z=int(cfg.get("gap_z", 0)))
