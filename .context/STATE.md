# Project state

Last updated: 2026-11-04 (process-isolated workers)

## Now working on

2026-11-08: Fast HDF5 load for big/network files — single-open probe, cached dataset listing, worker-thread probes with a 0.25 s GUI wait budget, byte-bounded frame cache + slow-storage read-ahead (DECISIONS 2026-11-08). Unit/offscreen-tested with simulated slow I/O (`tests/test_h5_fast_load.py`); **not tried on a real slow share or on Windows/Linux hosts**. Uncommitted.

2026-11-07: Batch Integrate result plots (waterfall/stacked) — x ticks/zoom floored at the bin width (DECISIONS 2026-11-07). Uncommitted. Detector-view flip issue not yet reproduced (see DECISIONS).

2026-11-07: File-info ⓘ icon + hover tooltip (size, dates, owner, perms) on all input path fields via `helpers.attach_file_info` (DECISIONS 2026-11-07). Offscreen-verified (32 fields in the live window, `tests/test_file_info.py`); not hand-checked on cocoa. Uncommitted.

**Scope (2026-11-07):** this repo is the **Qt5/PyQt5** version. The Qt6 port is finished and is developed separately in another app folder; all work here applies to the Qt5 version only (DECISIONS 2026-11-07).

## Needing you (fork, carried from 2026-10-12 — prune what's done)

Needing you, in the order it matters:
- **Delete two sets of stale files on the share.** This host cannot — NFS
  maps it to a different principal (`touch` fails even in a `drwxrwsrwx`
  dir), so run these from wherever the GUI writes from:
  `find /home/s1c/connolly_oct26_bc -name '_bg_job_results.npz' -type f -delete`
  (196 files, 826 MB) and the bigger one, 55,157 stale per-frame archives
  from the Oct 10 run sitting beside today's correct per-file output in the
  same `zarr/` folders:
  `find /home/s1c/connolly_oct26_bc -path '*/pixirad/*' -name '*.pixi.frame_*.zarr.zip' -delete`.
  Anything globbing `*/pixirad/zarr/*.zarr.zip` currently mixes the two.
- **The ge1234 combined view.** Asked for and NOT built: Waterfall /
  Stacked profiles / Eta-R cakes tabs on the Hydra page showing the MERGED
  archive (the per-panel view stays). Needs a reader for the merged zarr
  and a frame cap — an 834-frame merged cake is >1 GB.
- **A PR upstream.** See below.
- **Eyes on a real Batch Correction run.** Verified only against synthetic
  two-file stacks plus a name-level replay of the real share. What needs a
  live run: that the Log names a sensible dark for each file of a multi-file
  VAREX scan, that the reduced frames look right loaded back into the Data
  Viewer, and that the output HDF5 opens downstream.
- **Push, then open the next PR upstream.** Upstream's staged-checkpoint
  method worked well on 48 commits; this one is small enough not to need it.
- **Eyes on a real zarr-grouping run.** The `Fe9Cr_KGT6038_after_failure_ff`
  folder with Dark set to both bracketing dark files, grouping *per source
  file*, should give 3 archives rather than 435. Verified only against
  synthetic two-file stacks, and the combo has never been seen rendered.
- **Eyes on the cake HDF5.** Still never opened in a viewer here — the one
  remaining eyes-on item now that the zarr's `/Omegas` are confirmed.
- **`PoleFigureWorker`** is still single-frame and takes χ/φ from its cfg.
  Making it ω-aware across a series is the piece the whole omega arc exists to
  enable, and it now has a correct, verified angle to stand on.
- **Metadata provenance**, which you said you'd keep testing against.


2026-11-05: Batch Integrate caking params — job path now forwards η min/max and R min=0; 1-D HDF5 stamps R/η params as attrs (DECISIONS 2026-11-05). Uncommitted. Reporter's 750-bin R axis not reproduced; awaiting the new `[batch] cake:` log line.

2026-11-04: Heavy QThread workers now run in a child process (`proc_worker.py`/`proc_cli.py`; Mask, Integration, Refine, Corrections, PDF, Texture, Drift, Manual d-spacing) so a native crash can't kill the GUI or a running calibration (DECISIONS 2026-11-04). Unit-tested with a fake worker + real mask run offscreen; not yet exercised from the live GUI tabs. Not converted: SolveCell, PumpProbe, Batch*, FolderMonitor, Projection/AllFrameStats (reasons in DECISIONS). Committed on main. Targeted tests (calibrate/hydra/mask/project/smoke/manual-dspacing) pass; test_hydra_ui, test_calibrate_mask_overlay and test_mask_display_transform fail identically with and without it. A full-suite run hung ~30 min on one forked test (not identified).

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

## Hydra Batch Integrate — current shape (2026-10-10)

- A batch is a source FOLDER. Scans are grouped by the parent of each
  file's **ge1-normalised** path, so both real layouts collapse to one key:
  panels together in a scan folder, and panel-above-scan
  (`.../ge1/APS_4130_C_load_step_0/...`). See DECISIONS 2026-10-09/10.
- The Output box is authoritative for a SINGLE scan; each panel appends its
  own `ge{n}/`. Only a multi-scan pick fans out.
- A pick of one file is still a four-panel scan (expanded via
  `hydra_siblings`, narrowed to the ticked panels).
- Caking, rotation and the bin-grid toggle all live on the caking card and
  follow whichever one is showing (per-panel or the all-panels composite).
- An eta range crossing ±180 is served by binning the whole circle and
  selecting back down (`midas_gui.eta_wrap`). A bin that STRADDLES the seam
  is declined, not half-filled.

### Not verified against real data

- The multi-scan queue has never completed with real frames. Grouping,
  output fan-out and source descriptors are tested; four panels walked
  through several folders to completion is not.
- The tx-aware lab compass has never been seen rendered. It is pinned
  against `pixel_eta_deg` at every Hydra roll, which is the strongest
  headless check available.
- The omega ramp/channel has not been driven by a real rotation scan —
  confirm `/Omegas` ramps rather than sitting at zero.

### Settled: the merged eta axis is sorted into [-180, 180)

Was "the Zarr Viewer's eta axis reads past 360". Chosen 2026-10-12, after
checking the inputs rather than the plotting code: **wrap and sort in the
combine**, not in the viewer.

Diagnosed on `4130_D_load_step_8_time_step_0`, reported as "ge1 is the
issue, its eta range is inconsistent with the caking input". It was not.
Every panel matched its CSV exactly:

| panel | `cake_parameters.1ide.geN.csv` | stored centres |
|---|---|---|
| ge1 | `120…210` step 10 | 125…205 (9) |
| ge2 | `-150…-60` | -145…-65 (9) |
| ge3 | `-60…30` | -55…25 (9) |
| ge4 | `40…120` | 45…115 (8) |

ge1 is written past the cut because **its CSV asks for 210**, and that is
the standing 1-ID-E convention, not a slip: `raghavan_jul25` ge1 is
130…205, `brugger_apr26` 125…200, `stock_jul25` 130…205. `eta_wrap` then
labels the columns with the caller's own requested centres, which is where
185/195/205 come from. The sawtooth was a *storage-order* artefact of
concatenating in panel order, not a labelling error in any panel.

`hydra_zarr_combine.plan_eta_order` now wraps the merged axis into
`[-180, 180)` and sorts it — a permutation plus a relabel of REtaMap's eta
plane, applied to every caked array at once. Verified on the real archive:
`-175…175` strictly ascending, `SumFrames` bit-identical to the old array
under that permutation, total counts unchanged. `_unwrap_eta` then returns
it untouched, so the viewer stops saying "(unwrapped)" and stops plotting
to 475. It is still needed for archives written before this.

It declines — leaving the merge exactly as it was — when the axis is
already ascending, when REtaMap has no eta plane, and when two columns
share an azimuth (overlapping panels have no one right order).

Also confirmed here, and previously only reasoned about: **`eta_wrap`'s
full-circle-then-select really does work on real data.** All nine ge1
columns carry signal, 185 included, at 100% non-zero bins.

### The 10 deg hole at eta~35 in connolly_oct26 is deliberate

ge4 is caked 40…120 where the other three span 90 deg, so the four cover
350 of 360 and the merged archive has 35 columns, not 36. **That wedge is
physically shadowed** (confirmed 2026-10-12) — the CSV is correct and the
tiling note is behaving as designed by reporting rather than refusing.
Do not "fix" ge4 to 30…120. Sorting makes the gap visible as a 20 deg step
in the axis, where before it was hidden inside the sawtooth.

Note that older experiments used 75 deg panels with deliberate 15 deg
gaps at every junction (`raghavan_jul25`, `stock_jul25`), so a
non-tiling Hydra set is the norm at this station, not an anomaly.

### Known wrong, not yet fixed

- The rotation card's **"one ω per frame (averaged)"** checkbox is
  mislabelled. It means one ω for the WHOLE RUN, and it is checked before
  the channel in `_omega_resolver`, so ticking it silently discards a
  chosen omega channel. `tab_batch` labels the same control "one ω for all
  frames", which is the accurate wording.
- The caking card does not show the current chunk size, so "how are the
  frames chunked" cannot be answered from the card the rotation lives on
  (it is the loader's `combine sub-frames` spin).
- `tab_batch`, `tab_view` and `tab_calibrate` still pass `tx=0` to
  `build_lab_frame_axes_items`. A single rolled detector has the same
  compass bug Hydra had; the mechanism is in place, the wiring is not.

## Standing rules (from memory)

- Commit history is the record of recent work — see `git log`. Docs
  (`development_history.md`, `gui_documentation.md`) are updated only when
  explicitly asked, not automatically per commit.
## Recent changes
- 2026-11-02: Light theme added (`style.DARK`/`LIGHT` tokens, `set_mode`, `ui.theme` pref); inline muted greys/links tokenised across tabs. Follow-ups: light pyvista/matplotlib canvases; audit remaining dialogs/tabs (Solve Cell, PDF, Batch, Mask) in light mode.
- 2026-11-01: Hydra mode sync + composite parameter table + per-panel cakes + `_hydra_results.npz` sidecar; 2 new tests in `test_hydra_ui.py` (pre-existing `test_hydra_composite_builds_with_matched_calibration` failure unchanged).
- 2026-11-01: Panel widths: content-derived minimum (capped 520 px, re-checked every 1 s while visible), saved on drag, restored next launch, ⟲ header button resets; 5 tabs + 3 Hydra pages pass `key=` and the Hydra pages gain Data/Controls minimise + ribbon. Tests in `test_panel_toggle_tabs.py` (9 pass); Hydra test files match clean-HEAD results (pre-existing segfault/failures in 5 files).
- 2026-10-31: Absolute image color-limit fields + Auto; bounded LUT zoom/pan, stable live-frame/log behavior, display-state persistence; tests in `test_color_scale_controls.py` and Data Viewer control regression. See ARCHITECTURE.
- 2026-10-31: Three calibration choices; `frozen_point_one_shot` chains fitted seeds into One-shot refinement, with legacy migration and stage-specific limits text. Focused tests pass; controlled synthetic check recovered geometry but One-shot increased residual strain (DECISIONS). Real-data comparison queued in ROADMAP.
- 2026-10-31: Calibrate Mask overlay now applies the preview's Flip Y / Flip Z / Transpose while the backend mask remains in raw detector space; transform changes repaint an enabled overlay. Focused tests pass; not live-checked visually.
- 2026-10-31: Calibrate parameters panel slimmed (Distortion name = picker button + n/15 line, green name = refined, merged ± range cell, BC_Y/BC_Z tooltips, Distortion/Residual map default off). Not live-checked visually.
- 2026-10-31: Calibrate: custom calibrant names/edited lattices no longer fail/ignored — `calib.backend_calibrant()` passes the GUI lattice dict to v2 `calibrate()`/`make_seed()`. Needs deploying to the beamline install; not yet live-tested in the GUI.
