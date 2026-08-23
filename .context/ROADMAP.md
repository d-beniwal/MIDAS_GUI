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
- **Texture:** multi-frame (χ,φ) stacks, ODF/WIMV.
- **Cross-cutting:** multi-detector merge, energy-sweep calibration.

## Package-side fixes (for MIDAS maintainers — NOT done in GUI)

P0-1 normalize corrections cake · P0-2 wire Q-uniform into kernels · P1-1 finite
autograd geometry grads · P1-2 robuster tilt in one_shot/bayesian · P2-1 smooth
absorption at μR=1.5 · P2-3 fold `analyze_workflows/` round-trips into package CI.
(GUI already works around P0-1/P0-2/P1-1/P1-2 — see DECISIONS.)
