# Project state

Last updated: 2026-10-25

## Now working on

**Paused on purpose (2026-10-25):** the Solve Cell visualization increments stop here.
Next session refines the existing Solve Cell tab (polish, live-display checks, fixes to
what is already built) *before* any new visualization is implemented (V06, V07, V14,
V16–V22 stay queued). Do not start new views until the user says refinement is done.

Merged to `main` and pushed 2026-10-25 (branch `solve_cell_vis` deleted): Solve Cell visualization
improvements from `scratch/solve_cell_visualization_research_and_implementation.md`,
all backed by products cached in the project (`/analysis/solve_cell/viz/`) —
open `test_data/projects/s3idc_oct8_spinel.h5` (pre-populated) to see them with
no re-run. Done: Ewald cap + a*/b*/c*/cell + predicted lattice points + |q|
shells (Reciprocal tab overlays, V01/V08/V15); Diagnostics tab with residual
vector fields/histograms/omega timeline (V09/V11); Volume tab with build
worker, 2-D slabs, isosurface + coverage outline (V03–V05); Spot inspector
(V10); Contamination tab (V12); Compare domains tab (V13: metric→basis→misorientation, reviewer conclusions); Frame patches overlay (V02). Documentation: `documentation/solve_cell_visualizations.{md,pdf}`. See DECISIONS 2026-10-24 (viz entry).
Backup of the example project before edits: `scratch/s3idc_oct8_spinel.pre_vis_backup.h5`.

## Next steps

- (Deferred until refinement is done) next viz increments: V06 stereographic maps, V07 DAC accessibility; make ingest retain dead-frame ids + null draws.
- Live-check the project round trip on the real `s3idc_oct8_spinel.h5`
  project: run Solve Cell, Save, reopen, confirm domains/previews return and
  Refine replaces Domain 1. Watch file growth after repeated re-ingests
  (HDF5 keeps freed space; Save As compacts).
- Revisit the research handoff before adding views: distinguish generated
  sphere, mapped detector patch, dense intensity/coverage volume and spots.
  Suggested starts: geometry/schedule adapter, spot inspector/timeline,
  Ewald sphere + current-frame patch, then bounded Cartesian-q volumes/slices.
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

- 2026-10-25 — Reciprocal map: removed fitted-positions/Fit-visible controls, renamed/separated overlays, fixed click picking (own screen-space projection, HiDPI-safe) and Detector view now follows a click. User-verified live.
- 2026-10-25 — MCP: all Solve Cell workflows exposed as `solve_cell.*` control ops / 37 `solve_cell_*` tools (`control_api.py`, `server.py`, `SolveCellTab.stageFinished` + agent mode; `tests/test_control_api_solve_cell_ops.py`). Uncommitted. Live-GUI check over the real socket still to do.
- 2026-10-25 — Global Data/Controls/Results terminology; Solve Cell Data/Controls panels minimise to a bottom ribbon (`panel_toggle.py`); also Data Viewer, Calibrate, Batch Integrate, Pump Probe (`tests/test_panel_toggle_tabs.py`). Committed `2ddc492`.
- 2026-10-25 — Solve Cell left-panel refinement: Panels ribbon (number = id), start/end/step/first-idx omega mapping with derived last idx + click-to-detect, size-only Data info, calibration filename link + popup, right-click About (see DECISIONS). Committed `2ddc492`.
- 2026-10-24 — `solve_cell_vis`: V02 frame patches (`frame_patch.py`) + visualization document (md+pdf); V13 domain comparison (`domain_compare.py`, `solve_cell_compare.py`); geometry adapter, viz products + project cache, overlays, Diagnostics/Volume/Spot-inspector/Contamination tabs (`tests/test_solve_cell_viz*.py`).
- 2026-10-24 — Solve Cell results persist in the project as a single latest record and auto-load on open (`project.write_solve_cell_*`/`read_solve_cell`, `SolveCellTab.apply_project_solve_cell`).
- 2026-10-23 — Added offline visualization research: 22 proposals, 37 sources; no app-code changes.
- 2026-10-23 — Ingest now honors calibration im_trans; added validation/warnings and regression coverage (`5e99421`).
- 2026-10-23 — Added omega-metadata frame-range detection and zero-step warnings; real-project mapping issue diagnosed (see DECISIONS).
- 2026-10-23 — Retained PyVista reciprocal renderer, reverted detector to pyqtgraph; added reciprocal point sizing/intensity scaling.
