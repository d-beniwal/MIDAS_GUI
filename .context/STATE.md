# Project state

Last updated: 2026-11-11 (Batch combined-HDF5 audit; fixes uncommitted)

## Now working on

2026-11-11 (env): env files re-synced to the working env (requirements.txt was stale; pytest-forked added; midas-defect is 0.9.0). Open: orphan `imagecodecs` (numpy>=2.1 vs 1.26.4) — DECISIONS 2026-11-11.

2026-11-11 (latest): **Batch HDF5 audit** — fixed parallel per-chunk h5 collisions (run could fail after integrating), `000000_000000` filename in plain 1-D, wrong `n_r_bins` on Q/2θ files, parallel monitor offset, unweighted Save cake; added `/instrument`, `/frame_metadata`, `/monitor_values`, `/lsd_per_frame` (DECISIONS 2026-11-11). Synthetic 20-ID-E/D, 1-ID-E layouts + CLI tested; **uncommitted, not live-checked in the GUI; no real beamline data.** Open limits listed in the DECISIONS entry (1-ID-E IC1-3 not in h5, etc.).

2026-11-11 (latest): `solve-cell-tuning-and-path-sync` and `bkg_pump` merged into `main` and pushed. Batch progress bar rounds + "finalizing outputs" log/label (sequential and parallel; DECISIONS 2026-11-11). Not live-checked in the GUI.

2026-11-11 (later): **Ruby identified as corundum** (phase screen vs same-volume decoys z=12.6; per-panel omega offsets found and applied; known-cell refine within 1.5%; blind ab-initio then returns a corundum-like cell). New: Identify phase / Align panels (Index box), domain delete / hide / re-assign, lattice-point classes (spot nearby unassigned / other domain), CLI `python -m midas_gui.solve_cell.identify`, report `.context/reference/solve_cell_ruby_identification.md` (DECISIONS 2026-11-11). Unit/offscreen-tested; not live-checked in the GUI; not in the control API/MCP. **Open:** physical cause of the per-panel omega offsets; Run Ingest stays disabled while Suggest runs. All of this and the Unit cell tab are uncommitted.

2026-11-11: Solve Cell **Unit cell tab** (reduced cell, volume, closest known lattices with deviations, symmetry-vs-tolerance ladder, 3D cell with reference overlay) + `solve_cell/cell_report.py`; **fix:** edited panel data path not reloaded before Ingest/Suggest (`DataLoaderPanel.reload_if_path_changed`). Offscreen/unit-tested (121 UI + 31 control-API + report/view tests pass); 3D view checked via standalone render only, **not seen in the live window**. Ruby fit matches corundum at 2.5% (c edge 1.7% short -> likely geometry). Open: Run Ingest stays disabled while Suggest runs (no cancel/progress); user's "3 panels after re-ingest" not reproduced. Branch `solve-cell-tuning-and-path-sync` pushed; Unit cell work uncommitted.

2026-11-10: Solve Cell MCP coverage completed: panel inputs (dark/bright/background with dataset/range/scale, masks, omega field/auto first idx, read-back `panel_get_inputs`), `suggest_parameters`/`apply_suggested_parameters`, and `run_domain_loop`/`cancel_domain_loop` (the "sequential refinements" pipeline). Control-API + coverage tests pass; two live MCP-over-socket runs on synthetic data pass (DECISIONS 2026-11-10). **Not tested: a real second domain via the loop, full-size data over MCP, the real GUI window.** Uncommitted. New skill `midas_gui_mcp/skills/midas-gui-mcp-solve-cell/`.

2026-11-10: Solve Cell ingest-parameter tuning: `midas_gui/solve_cell/tuning.py` (Qt-free, advisory, deterministic, process-parallel; CLI `python -m midas_gui.solve_cell.tuning`) + procedure doc `.context/reference/solve_cell_ingest_tuning.md` + `tests/test_solve_cell_tuning.py` (rules only; DECISIONS 2026-11-10). Run on one real file (Ruby7, 634 frames): 300 s serial, 122 s with 4 workers, identical output. **Now in the Solve Cell tab: Ingest box -> "Suggest parameters" / "Set suggested" (stage `tune_ingest`; DECISIONS 2026-11-10). Rules validated on ONE dataset only; not yet run at full size through the GUI; no cancel.** Uncommitted. Known: `test_closing_the_last_panel_tab_is_refused` hangs without pytest-forked (pre-existing).

2026-11-09: Headless Solve Cell runner added (`midas_gui/solve_cell/headless.py`, tests `tests/test_solve_cell_headless*.py`; DECISIONS 2026-11-09). Synthetic-crystal + CLI + concurrent-process + `project.open` offscreen tests pass; one real spinel panel trialled by hand (cubic a~7.9 A). Uncommitted.

2026-11-09: Solve Cell project-save audit — round trip verified exact except viz products lost on Save As; fixed in `flush_project_record` (DECISIONS 2026-11-09). Offscreen test vs local spinel project; not live-checked in the GUI. Uncommitted.

2026-11-08: Transforms checkboxes record tick order and show it as a number ("Flip Y (2)"); order persisted in GUI state/projects (DECISIONS 2026-11-08). Offscreen-tested (`tests/test_im_trans_order.py`); not hand-checked on cocoa. **Also fixed:** template-based paramstest output doubled ImTransOpt lines. **Open:** midas_calibrate/IntegratorZarrOMP.c read multi-op ImTransOpt differently from the rest (DECISIONS); non-square 90° rotation/transpose still fails in Integrate (backend gap; GUI-side pre-transform workaround not built). Uncommitted.

2026-11-08: Fast HDF5 load for big/network files — single-open probe, cached dataset listing, worker-thread probes with a 0.25 s GUI wait budget, byte-bounded frame cache + slow-storage read-ahead (DECISIONS 2026-11-08). Unit/offscreen-tested with simulated slow I/O (`tests/test_h5_fast_load.py`); **not tried on a real slow share or on Windows/Linux hosts**. Uncommitted.

2026-11-07: Batch Integrate result plots (waterfall/stacked) — x ticks/zoom floored at the bin width (DECISIONS 2026-11-07). Uncommitted. Detector-view flip issue not yet reproduced (see DECISIONS).

2026-11-07: File-info ⓘ icon + hover tooltip (size, dates, owner, perms) on all input path fields via `helpers.attach_file_info` (DECISIONS 2026-11-07). Offscreen-verified (32 fields in the live window, `tests/test_file_info.py`); not hand-checked on cocoa. Uncommitted.

**Scope (2026-11-07):** this repo is the **Qt5/PyQt5** version. The Qt6 port is finished and is developed separately in another app folder; all work here applies to the Qt5 version only (DECISIONS 2026-11-07).

2026-11-05: Batch Integrate caking params — job path now forwards η min/max and R min=0; 1-D HDF5 stamps R/η params as attrs (DECISIONS 2026-11-05). Uncommitted. Reporter's 750-bin R axis not reproduced; awaiting the new `[batch] cake:` log line.

2026-11-04: Heavy QThread workers now run in a child process (`proc_worker.py`/`proc_cli.py`; Mask, Integration, Refine, Corrections, PDF, Texture, Drift, Manual d-spacing) so a native crash can't kill the GUI or a running calibration (DECISIONS 2026-11-04). Unit-tested with a fake worker + real mask run offscreen; not yet exercised from the live GUI tabs. Not converted: SolveCell, PumpProbe, Batch*, FolderMonitor, Projection/AllFrameStats (reasons in DECISIONS). Committed on main. Targeted tests (calibrate/hydra/mask/project/smoke/manual-dspacing) pass; test_hydra_ui, test_calibrate_mask_overlay and test_mask_display_transform fail identically with and without it. A full-suite run hung ~30 min on one forked test (not identified).

2026-11-04: Pump Probe "Calculate background" card + Detector tab on branch `bkg_pump` (uncommitted; `bkg_estimate.py`, `PumpBackgroundWorker`, `tests/test_bkg_estimate.py`; DECISIONS 2026-11-04). Needs a live check on real TRR data (SNIP iterations, pre-pump median). `qshift_pump` was merged to main (ff) and deleted locally 2026-11-04; `origin/qshift_pump` not yet deleted.

2026-11-03: Calibrate "Rings…" button (next to Calibrant) — tick/untick calibrant rings to use in the fit; wired through `rings_to_exclude` (DECISIONS 2026-11-03). Backend path and widget state verified offscreen; not run through the full GUI Run button or live data. Ignored by First-time/Frozen-point (backend takes no spec). Committed on main.

2026-11-03: APEXA CLI now runs in an embedded dockable terminal (`midas_gui/apexa_terminal.py`, Tools ▸ APEXA (CLI)…); verified offscreen against a fake script (I/O, colour, resize, float/re-dock) but not yet against the real APEXA CLI or on cocoa (fonts, Cmd+C/V, Ctrl-C). Committed on main.

2026-11-03: Pump Probe reference-peak q-alignment on branch `qshift_pump` : `qshift.py`, "Q alignment" card + tab in `tab_pumpprobe.py`, per-frame profiles from `PumpProbeWorker`, `tests/test_qshift.py` (DECISIONS 2026-11-03). Synthetic + offscreen checked; needs a live check on real TRR data. Pre-existing failure on main: `test_hydra_minimize_restore_and_persist[hydra_view]`.

2026-11-02: Light/dark theme — Preferences ▸ Display ▸ Theme (`ui.theme`, restart to apply). Chrome + pyqtgraph canvases theme via `style.py` tokens; pyvista/matplotlib canvases still unthemed (DECISIONS 2026-11-02). Checked via offscreen screenshots of 4 tabs + prefs; not yet reviewed on every tab/dialog.

2026-11-01: Hydra Data Viewer — mode ribbon synced across Data Viewer/Calibrate/Batch; composite view shows a read-only per-panel parameter table; new per-panel Eta-vs-R cakes; profiles+cakes saved/restored via a Data Viewer project sidecar (DECISIONS 2026-11-01). Not live-checked with real Hydra data.

2026-11-01: Data/Controls panel widths (`panel_toggle.py`) fit their content and are remembered per tab (QSettings `MIDAS/midas-gui`, `layout/<tab>/<panel>_width`); the ⟲ button in each panel header resets. The three Hydra pages (view/calibrate/batch) now use the same helper, including minimise-to-ribbon.

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

- (Planned, not started) Solve Cell phase identification: Symmetry stage -> intensities -> Laue merging -> phase matching; full plan in ROADMAP "Solve Cell: from lattice to phase identification".
- Ask backend (midas_calibrate_v2) to forward `rings_to_exclude` through `iterate_frozen_point_until_stable` and `first_time_calibrate`; then drop the two GUI warnings in `calib.run_pipeline` (DECISIONS 2026-11-07).
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

- **To raise with the MIDAS developer (2026-11-08):** (1) non-square transpose / 90° rotation is
  rejected by every integrator but handled by calibrate; (2) multi-op `ImTransOpt` is read per-line,
  last-line-only, or one-line depending on the reader. Details: QUESTIONS_FOR_COLLEAGUES.md #9/#10,
  ROADMAP P3-5/P3-6.

## Recent changes
- 2026-11-02: Light theme added (`style.DARK`/`LIGHT` tokens, `set_mode`, `ui.theme` pref); inline muted greys/links tokenised across tabs. Follow-ups: light pyvista/matplotlib canvases; audit remaining dialogs/tabs (Solve Cell, PDF, Batch, Mask) in light mode.
- 2026-11-01: Hydra mode sync + composite parameter table + per-panel cakes + `_hydra_results.npz` sidecar; 2 new tests in `test_hydra_ui.py` (pre-existing `test_hydra_composite_builds_with_matched_calibration` failure unchanged).
- 2026-11-01: Panel widths: content-derived minimum (capped 520 px, re-checked every 1 s while visible), saved on drag, restored next launch, ⟲ header button resets; 5 tabs + 3 Hydra pages pass `key=` and the Hydra pages gain Data/Controls minimise + ribbon. Tests in `test_panel_toggle_tabs.py` (9 pass); Hydra test files match clean-HEAD results (pre-existing segfault/failures in 5 files).
- 2026-10-31: Absolute image color-limit fields + Auto; bounded LUT zoom/pan, stable live-frame/log behavior, display-state persistence; tests in `test_color_scale_controls.py` and Data Viewer control regression. See ARCHITECTURE.
- 2026-10-31: Three calibration choices; `frozen_point_one_shot` chains fitted seeds into One-shot refinement, with legacy migration and stage-specific limits text. Focused tests pass; controlled synthetic check recovered geometry but One-shot increased residual strain (DECISIONS). Real-data comparison queued in ROADMAP.
- 2026-10-31: Calibrate Mask overlay now applies the preview's Flip Y / Flip Z / Transpose while the backend mask remains in raw detector space; transform changes repaint an enabled overlay. Focused tests pass; not live-checked visually.
- 2026-10-31: Calibrate parameters panel slimmed (Distortion name = picker button + n/15 line, green name = refined, merged ± range cell, BC_Y/BC_Z tooltips, Distortion/Residual map default off). Not live-checked visually.
- 2026-10-31: Calibrate: custom calibrant names/edited lattices no longer fail/ignored — `calib.backend_calibrant()` passes the GUI lattice dict to v2 `calibrate()`/`make_seed()`. Needs deploying to the beamline install; not yet live-tested in the GUI.
