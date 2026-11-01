# Project state

Last updated: 2026-11-01 (integer color limits, Min=1 default, Reset button, sliders leave fields fixed)

## Now working on

2026-11-01: Min/Max are whole numbers, auto Min = 1, button is "Reset", and histogram sliders only adjust contrast (`contrast`) without touching the fields. Signed/fractional maps (residual, pump-probe) opt out via `integer=False`.

Shared image color scales now have Max/Min fields above/below the histogram,
defaulting to finite image extrema. Typed limits stay linear in the fields,
survive live frames/log switches, and bound histogram navigation. Auto resets
to image extrema. Detector/cake/stack/residual/waterfall/Pump Probe heatmaps
share `ColorScaleControls`; focused tests and offscreen layout preview checked.

Calibrate now offers only One-shot (default), Frozen-point (fast/high-tilts),
and Frozen-point + One-shot. The combined mode runs both stages in one
abortable worker and seeds stage 2 with stage 1's fitted geometry/distortion.
Other algorithms are removed from GUI selection pending systematic probing
(ROADMAP). Backend adapters remain. GUI windows apply only to stage 2;
Frozen-point window/cap parity remains open.

**Paused on purpose (2026-10-25):** the Solve Cell visualization increments stop here.
Next session refines the existing Solve Cell tab (polish, live-display checks, fixes to
what is already built) *before* any new visualization is implemented (V06, V07, V14,
V16–V22 stay queued). Do not start new views until the user says refinement is done.

Solve Cell visualization work merged to main 2026-10-25; example project:
`test_data/projects/s3idc_oct8_spinel.h5` (cached products, no re-run needed).
View details: DECISIONS 2026-10-24 and `documentation/solve_cell_visualizations.md`.

## Next steps

- Live-check the combined calibration on representative beamline data; later
  systematically compare the deferred pipelines (ROADMAP). Connect Frozen-point
  windows at both adapter and iterative-backend layers before promising bounds.
- (Deferred until refinement is done) next viz increments: V06 stereographic maps, V07 DAC accessibility; make ingest retain dead-frame ids + null draws.
- Live-check the project round trip on the real `s3idc_oct8_spinel.h5`
  project: run Solve Cell, Save, reopen, confirm domains/previews return and
  Refine replaces Domain 1. Watch file growth after repeated re-ingests
  (HDF5 keeps freed space; Save As compacts).
- Revisit the visualization research handoff before adding views; distinguish
  generated sphere, detector patches, measured intensity/coverage and spots.
- Re-run the real `s3idc_oct8_spinel.h5` project after the im_trans fix and
  confirm omega steps/windows on all panels and one consistent pooled map.
- Live-display verification: omega auto-detection/warnings, orbit/zoom,
  hover/picking, fitted overlays and multi-panel/multi-domain linking.
- Continue domain comparison, geometry resolution, classification and
  diagnostics; do not blindly ab-initio-index pooled mixed domains.
- Decide whether/when to merge `pyvista` into main after live verification.
- Investigate the reported midas_defect grouped-median abort; apply thread
  parallelism to known-cell search; consult ROADMAP for other pending work.
- Live-app checks remain for MCP Calibrate + Solve Cell (real socket, real ingest), APEXA, relaunch and real
  multi-panel calibration output.

## Open questions / blockers

- Some reference DAC data are absent locally; synthetic/offscreen checks
  do not establish beamline scientific validity.
- Qt-offscreen embedded VTK has no usable pixel buffer/viewport picking;
  use standalone off-screen Plotter for supported pixel QA and a real
  display for interactions. Explicit Qt deletion/event-loop teardown is
  required. Run Qt test files separately (fork-safety issues); see
  ARCHITECTURE and DECISIONS for details. VisPy segfaulted in this setup.
- Windows `lheald` calibration-import failure and grouped-median crash
  remain unresolved; no new conclusion about their cause in this task.

## Recent changes
- 2026-10-31: Absolute image color-limit fields + Auto; bounded LUT zoom/pan, stable live-frame/log behavior, display-state persistence; tests in `test_color_scale_controls.py` and Data Viewer control regression. See ARCHITECTURE.
- 2026-10-31: Three calibration choices; `frozen_point_one_shot` chains fitted seeds into One-shot refinement, with legacy migration and stage-specific limits text. Focused tests pass; controlled synthetic check recovered geometry but One-shot increased residual strain (DECISIONS). Real-data comparison queued in ROADMAP.
- 2026-10-31: Calibrate Mask overlay now applies the preview's Flip Y / Flip Z / Transpose while the backend mask remains in raw detector space; transform changes repaint an enabled overlay. Focused tests pass; not live-checked visually.
- 2026-10-31: Calibrate parameters panel slimmed (Distortion name = picker button + n/15 line, green name = refined, merged ± range cell, BC_Y/BC_Z tooltips, Distortion/Residual map default off). Not live-checked visually.
- 2026-10-31: Calibrate: custom calibrant names/edited lattices no longer fail/ignored — `calib.backend_calibrant()` passes the GUI lattice dict to v2 `calibrate()`/`make_seed()`. Needs deploying to the beamline install; not yet live-tested in the GUI.
