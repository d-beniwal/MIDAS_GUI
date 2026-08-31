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
import os
from pathlib import Path
from typing import Optional

import numpy as np

import midas_gui._paths  # noqa: F401  (sys.path setup must run before MIDAS imports)
from midas_gui.constants import _SG, _LC, _V2_TO_V1, DISTORTION_NAMES, DEFAULT_LSD_UM


def _supported_kwargs(fn, kwargs: dict) -> dict:
    """Drop kwargs the callable ``fn`` doesn't accept, so the GUI stays compatible
    across MIDAS-backend versions whose signatures differ (e.g. midas-calibrate-v2
    0.3.3 has no ``initial_BC_y`` / ``initial_BC_z`` beam-centre seed). If ``fn``
    takes ``**kwargs`` nothing is dropped. Dropped names are logged to stdout (which
    the calibration worker relays to the GUI log)."""
    import inspect
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return dict(kwargs)
    if any(p.kind == p.VAR_KEYWORD for p in params.values()):
        return dict(kwargs)
    out = {k: v for k, v in kwargs.items() if k in params}
    dropped = [k for k in kwargs if k not in params]
    if dropped:
        print(f"[calib] note: {getattr(fn, '__name__', 'callable')}() does not accept "
              f"{', '.join(dropped)} in this backend version — ignoring.")
    return out


# ── Seed ────────────────────────────────────────────────────────────────────────

def _prep_transformed(image: np.ndarray, dark, im_trans: tuple):
    """Apply ``im_trans`` to image + dark once, together, before any seeding
    or solving happens — mirrors ``midas_calibrate_v2.calibrate()``'s own
    internal transform handling (it flips image AND dark, then derives
    NrPixelsY/Z, then seeds, all from the same transformed array).

    Needed only because ``autocalibrate_four_stage`` / ``_bayesian`` /
    ``_joint`` / ``pipelines.single.autocalibrate`` have no native
    ``im_trans`` parameter (see ROADMAP P3-1) — every caller of this helper
    must use its returned ``img``/``dark``/``NY``/``NZ`` for everything
    downstream (seed AND solve), never the original ``image``, or the seed
    and the solve run in two different frames.
    """
    img = image.astype(np.float32)
    dk = dark
    if im_trans:
        from midas_gui.helpers import _apply_im_trans
        img = _apply_im_trans(img, im_trans)
        if dk is not None:
            dk = _apply_im_trans(np.asarray(dk, dtype=np.float32), im_trans)
    NZ, NY = img.shape
    return img, dk, NY, NZ


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


def _distortion_coeffs(refine: dict) -> set:
    """Resolve which distortion coefficients (v2 names) to refine.

    Prefers the per-coefficient ``distortion_coeffs`` set; falls back to the
    legacy single ``Distortion`` bool (all-or-nothing) for callers that predate
    the per-coefficient dialog.
    """
    coeffs = refine.get("distortion_coeffs")
    if coeffs is not None:
        return set(coeffs)
    return set(DISTORTION_NAMES) if bool(refine.get("Distortion", True)) else set()


def _manual_seed_dict(manual: dict) -> dict:
    """Seed dict from a GUI manual_seed: BC/Lsd plus optional tilts + distortion."""
    seed = {"BC_y": manual["BC_y"], "BC_z": manual["BC_z"], "Lsd": manual["Lsd"]}
    for k in ("tx", "ty", "tz"):
        if manual.get(k) is not None:
            seed[k] = float(manual[k])
    if manual.get("distortion"):
        seed["distortion"] = dict(manual["distortion"])
    return seed


def _refine_dict(refine: dict) -> dict:
    """Translate the GUI refine flags into a v1 ``Refine`` dict.

    GUI flags: Lsd, BC, ty, tz, tx, Wavelength, plus distortion selection
    (``distortion_coeffs`` set of v2 harmonic names, or the legacy ``Distortion``
    bool).  Each selected v2 coefficient maps to its v1 ``p0..p14`` slot.
    """
    coeffs = _distortion_coeffs(refine)
    d = {
        "Lsd":        bool(refine.get("Lsd", True)),
        "BC":         bool(refine.get("BC", True)),
        "ty":         bool(refine.get("ty", True)),
        "tz":         bool(refine.get("tz", True)),
        "Wavelength": bool(refine.get("Wavelength", False)),
        "Parallax":   False,
    }
    for i in range(15):
        d[f"p{i}"] = False
    for name in coeffs:
        slot = _V2_TO_V1.get(name)
        if slot is not None:
            d[slot] = True
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

    # Optional seed tilts (default 0) and distortion coefficients (v2 harmonic
    # names → v1 p-slots) carried in from a prior calibration result.
    tx = float(seed.get("tx", 0.0)); ty = float(seed.get("ty", 0.0))
    tz = float(seed.get("tz", 0.0))
    p_seed = {f"p{i}": 0.0 for i in range(15)}
    for name, val in (seed.get("distortion") or {}).items():
        slot = _V2_TO_V1.get(name)
        if slot is not None:
            p_seed[slot] = float(val)

    v1 = CalibrationParams(
        NrPixelsY=NY, NrPixelsZ=NZ, pxY=pxY, pxZ=pxZ,
        Lsd=lsd, BC_y=bc_y, BC_z=bc_z, tx=tx, ty=ty, tz=tz,
        Wavelength=wavelength,
        SpaceGroup=_SG.get(calibrant, 225),
        LatticeConstant=(a, b, c, alpha, beta, gamma),
        RhoD=rho_px * pxY, MaxRingRad=max_ring_px, MinRingRad=min_ring_px,
        nIterations=n_iter, Refine=_refine_dict(refine),
        Device=device, Dtype="fp64", **p_seed,
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


def _attach_panel_result(result, panel_u: dict, panel_layout: Optional[dict],
                         output_dir: Optional[str]) -> None:
    """Attach panel-layout results to ``result`` in a form downstream spec
    building / paramstest export can use directly.

    ``result._panel_unpacked`` (raw tensors, private) already lets the save
    dialog write a companion panel_shifts.txt on demand. That alone isn't
    enough for in-GUI integration (Results-tab preview, Batch Integrate's
    "Use Tab 2 calibration"): those build an IntegrationSpec straight from
    ``result`` with no save step, so the shifts need to already be on disk
    and the panel *grid* (rows/cols/size/gaps — not just the deltas) needs
    to be recorded somewhere too. Writes panel_shifts.txt unconditionally
    (unlike residual_corr.bin, which stays in-memory-only without an
    output_dir) since a missing panel correction silently produces the
    wrong geometry, not just a smaller residual. Sets two plain,
    JSON-serializable attributes (``panel_layout`` dict of ints,
    ``panel_shifts_path`` str) so both survive ``_save_json``'s
    underscore-attribute filter.
    """
    if not panel_u or not panel_layout:
        return
    from midas_calibrate_v2.compat.to_v1 import write_panel_shifts_file
    if output_dir:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        path = out / "panel_shifts.txt"
    else:
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix="_panel_shifts.txt")
        os.close(fd)
        path = Path(tmp)
        print(f"[calibrate] panel shifts written to a temporary file ({path}) — "
              f"Save .json / Save paramstest, or set an Output folder, to keep them.")
    write_panel_shifts_file(panel_u, path)
    result.panel_layout = dict(panel_layout)
    result.panel_shifts_path = str(path)


def normalize_result(raw, mode: str, *, NY, NZ, pxY, pxZ, wavelength,
                     panel_layout: Optional[dict] = None,
                     output_dir: Optional[str] = None):
    """Return an AutoCalibrationResult regardless of which pipeline produced raw.

    When panel_layout was used, the refined panel shifts (panel_delta_yz /
    panel_delta_theta) are attached as ``result._panel_unpacked`` so the save
    dialog can write a companion panel_shifts.txt, and — via
    :func:`_attach_panel_result` — as ``result.panel_layout``/
    ``result.panel_shifts_path`` so in-GUI spec building
    (``helpers._build_spec``) can feed panel corrections to
    ``midas_integrate_v2`` without requiring an explicit save first. For
    one_shot + panel_layout, run_pipeline internally routes through
    autocalibrate_four_stage (which exposes stage2.unpacked); we detect this
    by checking for a ``.stage2`` attribute on the raw result.
    """
    # one_shot+panel_layout was re-routed through four_stage to expose unpacked
    if mode == "one_shot" and hasattr(raw, "stage2"):
        effective_mode = "four_stage"
    elif mode == "one_shot" and hasattr(raw, "unpacked"):
        # Partial distortion-coefficient refinement re-routed run_pipeline
        # through pipelines.single.autocalibrate (see run_pipeline) — a
        # CalibrationResult, not AutoCalibrationResult; normalize like
        # first_time's pv.unpacked case.
        return _auto_result_from_unpacked(
            raw.unpacked, NY=NY, NZ=NZ, pxY=pxY, pxZ=pxZ, wavelength=wavelength,
            strain=raw.post_residual_strain_uE, residual_map=raw.residual_corr_map,
            residual_bin_path=getattr(raw, "_residual_bin_path", None))
    else:
        effective_mode = mode

    if effective_mode == "one_shot":
        return raw   # calibrate() already returns AutoCalibrationResult, no panel data

    if effective_mode == "first_time":
        pv = raw.result
        strain = (pv.history[-1].mean_strain_uE
                  if getattr(pv, "history", None) else None)
        result = _auto_result_from_unpacked(
            pv.unpacked, NY=NY, NZ=NZ, pxY=pxY, pxZ=pxZ,
            wavelength=wavelength, strain=strain)
        panel_u = _extract_panel_unpacked(pv.unpacked)
        if panel_u:
            result._panel_unpacked = panel_u
            _attach_panel_result(result, panel_u, panel_layout, output_dir)
        return result

    if effective_mode == "four_stage":
        pv = raw.stage2   # final geometry stage (PVCalibrationResult)
        strain = getattr(raw, "stage4_strain_uE", None)
        result = _auto_result_from_unpacked(
            pv.unpacked, NY=NY, NZ=NZ, pxY=pxY, pxZ=pxZ,
            wavelength=wavelength, strain=strain)
        panel_u = _extract_panel_unpacked(pv.unpacked)
        if panel_u:
            result._panel_unpacked = panel_u
            _attach_panel_result(result, panel_u, panel_layout, output_dir)
        return result

    if effective_mode == "bayesian":
        result = _auto_result_from_unpacked(
            raw.map_unpacked, NY=NY, NZ=NZ, pxY=pxY, pxZ=pxZ, wavelength=wavelength)
        panel_u = _extract_panel_unpacked(raw.map_unpacked)
        if panel_u:
            result._panel_unpacked = panel_u
            _attach_panel_result(result, panel_u, panel_layout, output_dir)
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
            _attach_panel_result(result, panel_u, panel_layout, output_dir)
        return result

    raise ValueError(f"Unsupported pipeline mode for normalisation: {effective_mode}")


def tilt_seed_effective(mode: str, *, panel_layout=None, refine: Optional[dict] = None) -> bool:
    """Whether a manual tx/ty/tz seed will actually reach the underlying solver
    for this pipeline/config, mirroring :func:`run_pipeline`'s own branching —
    kept in sync with it deliberately rather than introspected generically,
    since the branch taken (not just the pipeline name) decides this.

    * ``four_stage`` / ``bayesian`` / ``joint`` always seed tilts via
      :func:`build_v1_params` (``CalibrationParams`` takes tx/ty/tz directly).
    * ``one_shot`` seeds tilts the same way when internally routed through
      ``autocalibrate_four_stage`` (``panel_layout`` set) or
      ``pipelines.single.autocalibrate`` (distortion refinement restricted to
      a subset of coefficients) — see the corresponding branches in
      :func:`run_pipeline`. The *plain* one_shot path calls
      ``midas_calibrate_v2.calibrate()`` directly, whose ``initial_tx/ty/tz``
      kwargs are silently dropped by :func:`_supported_kwargs` unless the
      installed backend's signature actually exposes them — checked here at
      call time, not assumed, so this stays correct if a future backend
      release adds them (see QUESTIONS_FOR_COLLEAGUES.md item 1).
    * ``first_time`` never passes a tilt seed to ``first_time_calibrate()``
      at all, regardless of backend version.
    """
    if mode in ("four_stage", "bayesian", "joint"):
        return True
    if mode == "first_time":
        return False
    if mode == "one_shot":
        if panel_layout:
            return True
        refine = refine or {}
        coeffs = _distortion_coeffs(refine)
        if refine.get("Distortion", True) and coeffs and coeffs != set(DISTORTION_NAMES):
            return True
        try:
            import inspect
            from midas_calibrate_v2 import calibrate
            params = inspect.signature(calibrate).parameters
            return all(f"initial_{k}" in params for k in ("tx", "ty", "tz"))
        except Exception:
            return False
    return False


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
            img, dk, pNY, pNZ = _prep_transformed(image, dark, im_trans)
            if manual:
                seed = _manual_seed_dict(manual)
            else:
                s = make_seed_safe(img, wavelength, pxY, calibrant)
                if s is None:
                    raise RuntimeError(
                        "Auto-seed failed for panel calibration (one_shot). "
                        "Enable manual seed (Pick BC / Pick Ring + Lsd) and retry.")
                seed = {"BC_y": s.BC_y, "BC_z": s.BC_z, "Lsd": s.Lsd_um}
            v1 = build_v1_params(
                seed, wavelength=wavelength, pxY=pxY, pxZ=pxZ, calibrant=calibrant,
                NY=pNY, NZ=pNZ, refine=refine, n_iter=n_iter, device=device)
            from midas_calibrate_v2.pipelines import autocalibrate_four_stage
            return autocalibrate_four_stage(
                v1, img, dark=dk, device=device, panel_layout=panel_layout,
                spec=_panel_spec(v1, panel_layout), verbose=True)

        coeffs = _distortion_coeffs(refine)
        if refine.get("Distortion", True) and coeffs and coeffs != set(DISTORTION_NAMES):
            # midas_calibrate_v2.calibrate() only exposes an all-or-nothing
            # refine_distortion bool (every p-slot gets the same flag) — route
            # through the same lower-level single-pass routine four_stage /
            # bayesian / joint already use (build_v1_params' per-p# Refine
            # dict) so the LM fit actually restricts itself to the selected
            # coefficients instead of silently widening the selection to all 15.
            img, dk, pNY, pNZ = _prep_transformed(image, dark, im_trans)
            if manual:
                seed = _manual_seed_dict(manual)
            else:
                s = make_seed_safe(img, wavelength, pxY, calibrant)
                if s is None:
                    raise RuntimeError(
                        "Auto-seed failed for partial distortion refinement "
                        "(one_shot). Enable manual seed (Pick BC / Pick Ring + "
                        "Lsd) and retry.")
                seed = {"BC_y": s.BC_y, "BC_z": s.BC_z, "Lsd": s.Lsd_um}
            v1 = build_v1_params(
                seed, wavelength=wavelength, pxY=pxY, pxZ=pxZ, calibrant=calibrant,
                NY=pNY, NZ=pNZ, refine=refine, n_iter=n_iter, device=device)
            bin_path = None
            if cfg.get("output_dir"):
                from pathlib import Path
                out = Path(cfg["output_dir"]); out.mkdir(parents=True, exist_ok=True)
                bin_path = str(out / "residual_corr.bin")
            from midas_calibrate_v2.pipelines.single import autocalibrate
            raw = autocalibrate(
                v1, img, dark=dk, n_iter=n_iter, lm_max_iter=lm_iter,
                device=device, verbose=True,
                build_residual_corr=bool(cfg.get("build_residual_corr", True)),
                residual_corr_path=bin_path)
            raw._residual_bin_path = bin_path   # no .bin field on CalibrationResult
            return raw

        from midas_calibrate_v2 import calibrate
        kwargs = dict(
            wavelength=wavelength, pxY=pxY, dark=dark, calibrant=calibrant,
            output_dir=cfg.get("output_dir"),
            build_residual_corr=bool(cfg.get("build_residual_corr", True)),
            n_iter=n_iter, lm_max_iter=lm_iter, device=device, verbose=True,
            refine_tilts=bool(refine.get("ty", True) or refine.get("tz", True)),
            refine_distortion=bool(_distortion_coeffs(refine)),
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
            # Seed tilts only if the installed calibrate() exposes them
            # (_supported_kwargs drops any it does not accept).
            for k in ("tx", "ty", "tz"):
                if manual.get(k) is not None:
                    kwargs[f"initial_{k}"] = float(manual[k])
        return calibrate(image, **_supported_kwargs(calibrate, kwargs))

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
            # first_time_calibrate registers + refines panel shifts correctly
            # on its own (unlike four_stage/bayesian/joint below, which need
            # an explicit panel-aware spec) — it just needs the layout passed.
            panel_layout=panel_layout,
        )

    if mode == "four_stage":
        from midas_calibrate_v2.pipelines import autocalibrate_four_stage
        img, dk, pNY, pNZ = _prep_transformed(image, dark, im_trans)
        if manual:
            seed = _manual_seed_dict(manual)
        else:
            s = make_seed_safe(img, wavelength, pxY, calibrant)
            if s is None:
                raise RuntimeError(
                    "Auto-seed failed for four-stage pipeline. "
                    "Enable manual seed (Pick BC / Pick Ring + Lsd) and retry.")
            seed = {"BC_y": s.BC_y, "BC_z": s.BC_z, "Lsd": s.Lsd_um}
        v1 = build_v1_params(
            seed, wavelength=wavelength, pxY=pxY, pxZ=pxZ, calibrant=calibrant,
            NY=pNY, NZ=pNZ, refine=refine, n_iter=n_iter, device=device)
        spec = _panel_spec(v1, panel_layout) if panel_layout is not None else None
        return autocalibrate_four_stage(v1, img, dark=dk, device=device,
                                        panel_layout=panel_layout, spec=spec,
                                        verbose=True)

    if mode in ("bayesian", "joint"):
        img, dk, pNY, pNZ = _prep_transformed(image, dark, im_trans)
        v1 = _seed_and_v1(img, wavelength, pxY, pxZ, calibrant, pNY, pNZ,
                          refine, n_iter, device, manual)
        spec = _panel_spec(v1, panel_layout) if panel_layout is not None else None
        if mode == "bayesian":
            from midas_calibrate_v2.pipelines import autocalibrate_bayesian
            return autocalibrate_bayesian(v1, img, mode="laplace", dark=dk,
                                          panel_layout=panel_layout, spec=spec)
        from midas_calibrate_v2.pipelines import autocalibrate_joint
        return autocalibrate_joint(v1, img, dark=dk, panel_layout=panel_layout,
                                   spec=spec)

    raise ValueError(f"Unknown pipeline mode: {mode}")


def _seed_and_v1(image, wavelength, pxY, pxZ, calibrant, NY, NZ,
                 refine, n_iter, device, manual):
    """Seed (manual or auto) → build_v1_params. Shared by advanced pipelines.

    ``image`` must already be im_trans-transformed (via ``_prep_transformed``)
    — this seeds directly from whatever array is passed in, so the caller is
    responsible for making sure it's the same array that gets solved against.
    """
    if manual:
        seed = _manual_seed_dict(manual)
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


def _panel_spec(v1_params, panel_layout):
    """A CalibrationSpec with per-panel rigid-shift parameters
    (panel_delta_yz/panel_delta_theta) registered as refinable.

    autocalibrate_four_stage/_bayesian/_joint build their own spec via
    spec_from_v1_params() when none is passed, which never registers panel
    parameters — panel_layout alone only affects the FIXED forward
    projection (which panel a pixel belongs to), never what gets refined.
    Passing this spec in makes their existing freeze/thaw/refined-parameter
    logic (four_stage's own Stage 1/2 split in particular) actually include
    the panel shift. Tolerances mirror midas_calibrate_v2.calibrate()'s own
    panel_mode="shift" defaults (panel_tol_shift_px=3.0,
    panel_tol_rot_deg=1.0) — the one place in the installed package that
    already does per-panel rigid-shift refinement correctly.
    """
    from midas_calibrate_v2.compat.from_v1 import (
        spec_from_v1_params, add_panel_parameters)
    spec = spec_from_v1_params(v1_params)
    add_panel_parameters(spec, panel_layout.n_panels(),
                         tol_shift_px=3.0, tol_rot_deg=1.0)
    return spec
