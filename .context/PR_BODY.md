# Beamline work from connolly_oct26 at 1-ID-E: Hydra Batch Integrate, Batch Correction, and parity between the two detector pages

127 commits, 120 files, +28,012 / −1,172. All of it written and used against
live data during a running experiment at 1-ID-E (four-panel GE hydra +
Pixirad SAXS), so most entries are a defect found on real data plus the test
that pins it.

`upstream/main` is merged in as of `923c5f4`, so this applies cleanly and
nothing here reverts your Hydra calibration parity work — the Oct-12 block
below deliberately follows the method your `DECISIONS` entry for it lays out.

## Suggested staging

PR #11 was taken as eight checkpoints; this is ~2.6× that, and the history
already falls into four blocks that each leave the tree working. Merging in
this order should keep each checkpoint reviewable:

### 1 · Batch Correction, Bin type, and Calibrate fixes (Oct 1–6, 28 commits)

* **New optional tab: Batch Correction** — chunked frame reduction
  (mean/median/sum/max over N sub-frames per file) with field correction and
  HDF5 out, several methods per run, suggested output path.
* **Bin type goes three-way: Radial / Q / 2θ**, in background jobs as well as
  in-process. One output rebin with two units; the integration grid stays
  R-uniform in all three and these resample the finished profile.
* Run logs from both run modes, written into the screen-log tree.
* Beam-monitor CSV: a row is identified by file and raw frame range; the dark
  is one row rather than a column on every chunk.
* Calibrate: a ticked manual seed must be editable; the Limits note re-centres
  when the seed moves; Mask Builder shows the detector the same way up as
  every other tab.

### 2 · Hydra Batch Integrate (Oct 7–8, 37 commits)

* **Caking is per panel**, colour-coded, with a summary table and an overlay
  on the composite — the four panels genuinely do not share R/η.
* **An η range that crosses ±180 is served in one run.** Only ge1 does this at
  1-ID-E, and it silently threw away a third of that panel: the range was
  clipped rather than wrapped, which also wrote an empty ge1 archive and took
  the four-panel combine down with it.
* **Rotation was unsettable**, so every frame recorded ω = 0 — exactly the
  all-zero `/Omegas` the single-detector tab warns about. A single-frame scan
  never notices; a rotation scan is simply wrong.
* Hydra loader: dark / bright / background were never saved.
* Combine: at one η bin per panel the carry-over rule invented data.

### 3 · Output layout, naming, and combine correctness (Oct 9–10, 35 commits)

* **A Hydra batch is a FOLDER, not a ge1–ge4 quartet.** Grouping per quartet
  made every frame file its own scan — thirteen output directories for one
  load step.
* **A stale Output box silently wrote into the previous dataset's folder.**
  This happened on real data. A box holding a value one group already derives
  is a stale autofill, not a chosen root — which is what stopped
  `load_step_1`'s output nesting inside `load_step_0`.
* One zarr per scan rather than one per frame (834 archives per panel on an
  834-frame scan was the default, and usually a mistake).
* Combine: two readings of one rotation are not bit-identical, so the omega
  agreement test is scaled to the omega step.
* Documented exactly what Batch Integrate writes and what it never touches —
  verified empirically, not read off the code: raw data is read-only to this
  app, and output is a `<parent>_bc` sibling tree.

### 4 · Background jobs and detector-page parity (Oct 12, 22 commits)

* **Hydra Batch Integrate can run as a detached background job**, with the
  same button, the same `JobQueuePanel` (Reattach / Cancel / live tail) and
  the same one-session-many-steps chaining as the single-detector tab. A scan
  becomes one `batch_cli` invocation per panel, sequentially, then a new
  `hydra_combine_cli` step. Motivated by a 189-folder run dying after ONE
  folder because a pyqtgraph `autoRange()` on an all-NaN profile raised out
  of the Qt signal chain — headless, that whole class of failure cannot occur.
* `batch_cli` gains `--eta-min/--eta-max` (a panel's wedge had **no** CLI
  representation, so a background Hydra job would have silently integrated
  all 360° on every panel) and `--frame-range-*`.
* **The same parity pass you did for the calibration page, applied to Batch
  Integrate.** Diffed the two pages section by section, sorted each difference
  into bug/gap vs forced-by-four-panels, and put the ambiguous ones back to
  the user. Hydra gained 2θ-uniform output (it could not emit 2θ **at all**),
  multi-azimuth, the ion-CSV extras and Preview-sum; the two detector-view
  toolbars are now identical; the log and job rows are permanently visible on
  the single-detector tab instead of hidden behind a Logs tab.
* **The Detector view was drawing the detector's arming frame.** A Pixirad
  arms one frame before counting, so frame 0 of every `.pixi.h5` is
  identically zero, and the preview read index 0 unconditionally. pyqtgraph
  autoscaled a constant array into a degenerate ~1e-6 range, which reads as
  corrupt data rather than as blankness. Fixed display-only and bounded;
  which frames a *run* integrates is unchanged and stays opt-in, because an
  all-zero frame is not reliably an arming frame.
* Background jobs no longer scatter `_bg_job_results.npz` sidecars the UI
  cannot open — `JobQueuePanel` records one `out_dir` per job, so a
  196-folder sweep wrote 196 of them (826 MB) of which 1 was reachable.

## Testing

Full suite (`pytest --forked`) green apart from five failures that predate
this branch and reproduce on a clean tree:
`test_apply_project_calibration_single_detector`, `test_pva_live_source_roundtrip`
(system `libstdc++` `CXXABI` mismatch), `test_unify_combine_single_hdf5_file_start_end_span_full_raw_count`,
`test_the_profile_resolves_a_hutch_the_path_cannot`, `test_hydra_composite_builds_with_matched_calibration`.

Two traps worth knowing if you run subsets:

* `test_batch_stream_preview.py` fails **entirely** in a partial run and passes
  in a full one. It is global-GUI-state sensitive, so a subset result there is
  not evidence of a regression.
* Do not edit files while a `--forked` run is in flight — each test re-imports
  the package in a subprocess, so a mid-run edit produces mass `ImportError`s.

## Known gaps

* The Hydra page has no combined-`ge1234` view yet (Waterfall / Stacked
  profiles / Eta-R cakes of the *merged* archive). Designed, not built —
  it needs a reader for the merged zarr and a frame cap, since an 834-frame
  merged cake is >1 GB.
* Drift correction is still single-detector only. Deliberately out of scope
  for the parity pass: four panels could drift independently, which makes it
  a feature rather than a layout fix — the same call your `DECISIONS` entry
  made about ring-picking.
* `midas_gui/hydra_zarr_combine.py` and `mpe_wf_saxs_waxs/combine_hydra_zarr.py`
  are two independent four-panel combines. Not merged here on purpose (the
  GUI's background path must produce byte-identical output to its in-process
  path), but two things the latter records are strictly better and are worth
  taking: `InstrumentParameters` stacked as a length-N array, and per-panel
  provenance carry-forward. `Distance` legitimately differs per panel, and on
  2026-10-12 ge1 turned out to have been physically moved ~24 px mid-beamtime
  — a per-panel record in the merged file would have made that obvious at a
  glance instead of costing a day's debugging.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
