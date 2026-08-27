# ROADMAP — open / pending items

_On-demand: outstanding work worth tracking. Not auto-loaded._
_Migrated from `claude/gui_plan.md` + `analyze_workflows/` on 2026-07-17;_
_verify against current code/commits before assuming still-open._

Phases 1–3 (pipeline dropdown, refine flags, kernels, variance, Q-uniform,
extra formats, learnable mask, refinement tab, corrections preview,
multi-panel, Bayesian UQ, PDF Stage 1, texture, export hub, joint-cake) are
**complete**.

## PDF (Stage 2–3) — top priority, unblocked (2026-08-10)

CIF structure fit, Δ-PDF, multiple scattering, absorption, RMCProfile/DISCUS
export. The former blocker (`midas-hkls>=0.5.0` for the `absorption`
submodule) is resolved — `midas-hkls` is now pinned to 0.7.0 and `midas_pdf`
is the real PyPI package (0.1.1), replacing the old `midas_gui/_vendor` copy
and its compatibility shim (see DECISIONS 2026-08-10). The installed
`midas_pdf` already ships `cif.py`, `deltapdf.py`, `multiple_scattering.py`,
`ms_transport.py`, `multi_phase.py`, `aniso_refine.py`, `bayesian_refine.py`,
`rmc/`, `saxs/`, `strain_pdf.py` — Stage 2–3 is now GUI wiring work against an
already-available backend, not a packaging blocker. Build-critical reference:
`.context/reference/midas_pdf/` (esp. `01_core_api.md`, `05_gui_integration.md`)
— verify against the installed package before assuming still accurate.

## Per-tab open items

- **Calibrate:** multi-distance (`autocalibrate_multi`), doublet calibrants,
  NN-residual augmenter, per-ring δr_k JSON sidecar export, full custom-calibrant UI.
- **Batch:** per-frame outlier rejection (cosmic-ray / azimuthal σ-clip in
  batch), Compton/empty/absorption in batch, Zarr/GE/EDF sources, soft
  (autograd) kernel.
- **Corrections:** empty-scale LBFGS refine, absorption-param refine.
- **Refinement:** ProfileMSE / PeakPosition losses, multi-distance, in-tab
  Laplace UQ, energy-sweep drift.
- **Data Viewer:** tilt/distortion in ring **overlay** (radial integration
  already handled by df544d2), multi-detector.
- **Mask:** DAC gasket / angular wedge exclusion.
- **Texture:** multi-frame (χ,φ) stacks, ODF/WIMV. **Known bug (found
  2026-08-25, not fixed — out of scope for the ImTransOpt session that found
  it):** `PoleFigureWorker` (`workers.py`) passes its mask straight to
  `build_geom(spec, "subpixel2", mask_t)` with **no transform handling at
  all** — never did, before or after the 2026-08-25 ImTransOpt fix. If the
  active calibration has a non-zero ImTransOpt, the mask will be misaligned
  with the geometry (same class of bug as the one fixed everywhere else this
  session, but this one call site was outside the reported scope). Fix the
  same way as `BatchWorker`/`RefinementWorker`: pre-flip the mask (not the
  image — `spec.TransOpt` already handles the image) before it reaches
  `build_geom`.
- **Cross-cutting:** multi-detector merge, energy-sweep calibration.

## Package-side fixes (for MIDAS maintainers — NOT done in GUI)

P0-1 normalize corrections cake · P0-2 wire Q-uniform into kernels · P1-1 finite
autograd geometry grads · P1-2 robuster tilt in one_shot/bayesian · P2-1 smooth
absorption at μR=1.5 · P2-3 fold `analyze_workflows/` round-trips into package CI.
(GUI already works around P0-1/P0-2/P1-1/P1-2 — see DECISIONS.)

**P3-1 — `im_trans`/`ImTransOpt` not accepted by most calibration pipeline
entry points (found 2026-08-25, still true in 0.10.0 — current PyPI latest,
re-checked 2026-08-27).** Only `midas_calibrate_v2.calibrate()` accepts
`im_trans` as a native kwarg (and flips `image`/`dark` internally, then
derives NrPixelsY/Z from the transformed shape, then seeds — everything
downstream in one consistent frame). `autocalibrate_four_stage`,
`autocalibrate_bayesian`, `autocalibrate_joint`, `first_time_calibrate`, and
`pipelines.single.autocalibrate` (the panel-layout / partial-distortion-
refinement routes `calib.py` uses) have **no such parameter** — confirmed
via `inspect.signature()` against the installed package.

Additionally (found 2026-08-27): `calibrate()`'s own `panel_layout` support
doesn't help either — it runs panel refinement internally
(`pipelines/auto.py:587-638`, computes `panel_delta_*` into `cr.unpacked`)
but the final `return AutoCalibrationResult(...)` never copies those keys
out, and the dataclass has no fields for them. So even `calibrate()` can't
be used for panel-layout calibration without losing the per-panel
shift/rotation output the GUI needs (its panel_shifts.txt export). Upstream
fix needs **either** `im_trans` added to the four entry points above, **or**
`panel_delta_*` exposed on `AutoCalibrationResult` (either one would let
`calib.py` stop manually pre-flipping pixels for panel-layout calibration).

**Correction (2026-08-27):** the previous note here ("`midas_gui`'s
`calib.py` already works around this correctly") was wrong. The manual
pre-flip workaround had a real bug: it flipped the image for the *solve*
call but computed the auto-seed from the *unflipped* image in the same
branch, so seed and solve ran in two different frames whenever a transform
was active — exactly the failure a user hit with Flip Z + Multi-panel
detector. Fixed in `calib.py` via `_prep_transformed()`, used consistently
for seed + solve + dark in every affected branch. See DECISIONS 2026-08-27.
This upstream ask still stands — the fix only makes the workaround correct,
it doesn't remove the need for one.

**P3-2 — no `apply_trans_opt` hook on `*BinGeometry.from_spec(spec,
mask=mask)` (found 2026-08-25).** Every `midas_integrate_v2.integrate_*`
function accepts `apply_trans_opt=True` (default) and flips the *image*
internally via `spec.TransOpt`. Geometry construction itself
(`HardBinGeometry`/`SubpixelBinGeometry`/`PolygonBinGeometry.from_spec`) has
no equivalent — a `mask=` array passed to `from_spec()` is evaluated
directly against the untransformed pixel-index grid, with no way to ask it
to honor `spec.TransOpt`. `midas_gui` therefore must keep manually
pre-flipping every mask in Python (once, before `from_spec`/`build_geom`)
even though it never flips images anymore — see DECISIONS 2026-08-25
("backend does the flip"). Upstream fix would be an `apply_trans_opt` param
on `from_spec()` itself, mirroring the `integrate_*` functions.
