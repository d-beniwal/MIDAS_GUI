# ROADMAP — open / pending items

_On-demand: outstanding work worth tracking. Not auto-loaded._
_Migrated from `claude/gui_plan.md` + `analyze_workflows/` on 2026-07-17;_
_verify against current code/commits before assuming still-open._

Phases 1–3 (pipeline dropdown, refine flags, kernels, variance, Q-uniform,
extra formats, learnable mask, refinement tab, corrections preview,
multi-panel, Bayesian UQ, PDF Stage 1, texture, export hub, joint-cake) are
**complete**. **PDF Stage 2–3 is also complete** (2026-08-12: CIF structure
fit, Δ-PDF, multiple scattering, absorption, fluorescence — confirmed live
in `midas_gui/tab_pdf.py`). Deliberately left out of scope, not currently
planned: Bayesian SVI/NUTS, RMC/DISCUS export, SAXS/SANS joint refinement,
multi-phase/core-shell, anisotropic ADP, directional strain-PDF.
Build-critical reference for maintaining the PDF tab:
`.context/reference/midas_pdf/` (esp. `01_core_api.md`, `05_gui_integration.md`).

## Follow-ups carried forward from STATE (2026-10-21)

These were open in the prior snapshot; check current code before acting.

- **MCP phase 2:** extend the current Data Viewer/Calibrate control surface
  to Mask and Batch Integrate when requested. Phase 1 is already on `main`;
  see `midas_gui_mcp/README.md` for operation coverage and APEXA integration.
- **Solve Cell Phase 2+:** detector-stage geometry resolution tiers,
  self-calibration fallback, bootstrap confidence intervals/axis doubling,
  further domain separation, diagnostics; see
  `documentation/solve_cell_handoff.md` §10. Multi-panel pooling currently
  requires a calibration file for each panel. Numerically cross-check the
  deliberately standalone pipeline's `_apply_stack_corrections` against
  GUI field corrections beyond the synthetic unit test.
- **Texture:** make `PoleFigureWorker` omega-aware across a series (currently
  single-frame with cfg χ/φ); inspect cake HDF5 in a real viewer and continue
  testing metadata provenance.
- **Frozen-point calibration:** the high-tilt `_seed_and_v1` branch does not
  forward `tols=tols`; the UI currently warns. See DECISIONS 2026-09-30.
- **Calibrate UI unification:** see
  `documentation/calibration_unification_plan.md`; recheck surface parity
  after recent Hydra changes before choosing the next phase.
- **Verification gaps:** no live end-to-end restart/relaunch check or recent
  real multi-panel beamline calibration output; Windows `lheald` import
  failure awaits direct tracebacks/version. `job_queue.py` and
  `peak_fit_panel.py` still lack coverage; `test_batch_cli_omega.py` covers
  only the omega argument/configuration round trip for `batch_cli.py`.

## Solve Cell: ingest optimized, next bottlenecks + a loading bug (2026-10-22)

Benchmarked MIDAS's `perf_solve_cell` branch against `master` on a real
642-frame spinel panel (`test6_..._det_-10_-15_0_...h5`): ingest is 4.35x
faster at the new default (`max_workers=1`, wired into `pipeline.py` in
`a2acc78`), 9.42x at `max_workers=12` on this 12-core machine, with
byte-identical `spots_g.csv`/`ingest_summary.json` at every setting.
`_stage_ingest`'s own hot loops (`build_mask`, `live_frames`,
`subtract_background`, `choose_sectors`, `find_blobs_3d`) are now all
vectorized and/or thread-pool-capable — nothing left to optimize inside
that stage itself.

**Bug found while investigating this, not yet fixed:** `midas_gui` never
imports `hdf5plugin` anywhere (confirmed by grepping the whole package and
by importing `midas_gui.app` standalone and checking `sys.modules`). This
beamline's raw Eiger files use the dynamically-loaded `bitshuffle` HDF5
filter (id 32008, confirmed via `h5py`'s own filter introspection), so
**any h5py read of a raw frame — a Data Viewer preview (`_load_image`/
`_get_frame`/`full_stack` in `helpers.py`/`widgets.py`), a solve-cell panel
load, or the Ingest stage's own `frames_loader()` resolution in
`workers.py`'s `SolveCellWorker` — crashes with `OSError: Can't
synchronously read data (can't open directory .../lib/hdf5/plugin)`**,
unless something else in the same process happened to import `hdf5plugin`
first. Nothing in the GUI currently does that: only `midas_defect.
persistence` imports it, and nothing in the solve-cell call path touches
that module. Reproduced directly against the real `midas-gui` conda env and
the real `h5py.File(...)[...]` call `_load_image` makes, not just a scratch
script. **Fix:** import `hdf5plugin` once, early, at `midas_gui` startup
(e.g. `midas_gui/__init__.py` or `app.py`) so it is registered before any
h5py read, regardless of which tab/path touches a file first. Once
registered, the read itself is fast and not a bottleneck (2.39s measured to
load a 1.35 GB / 642-frame stack).

**Downstream solve-cell stages not yet touched by the perf_solve_cell work**
(ranked by how often each is hit):
- `midas_hkls.ab_initio.select_basis`
  (`packages/midas_hkls/midas_hkls/ab_initio.py:477`) — triple-nested Python
  loop over up to `max_try=12` candidate vectors (≤220 triples), each
  running up to 8 `_find_finer_lattice` refinement cycles (its own
  `m³`-scan, up to 96 combos x 3 basis-vector swaps ≈ 288
  `_index_fraction` calls per cycle). Runs on every ab-initio solve
  (`pipeline.py` stage 3), not a rare path.
- `_stage_index_known_cell`'s `_random_rotation_search`
  (`midas_gui/solve_cell/pipeline.py:757`) — default `n_search=200,000`
  random orientations for the real data PLUS `n_null_draws=20` more full
  200k-orientation null searches = up to 4.2M orientation trials per call,
  single-threaded. Used for second/third-domain search, which per the
  `s3_midas` skill is the *common* case at this facility (multi-crystal
  DACs), not an edge case. Same "many independent trials, no shared mutable
  state" shape as the ingest `max_workers` work — directly portable to a
  thread pool.
- `_stage_diamond_filter`'s Monte Carlo null test (`pipeline.py:596`) —
  scales with pooled spot count; lower priority since `n_total` is
  typically small (tens of spots).
- `_stage_ingest_pooled` still processes panels strictly serially by design
  (memory-bound, see its own docstring) — matters less now that each
  panel's ingest is 4-9x faster, but still N x per-panel wall time for this
  facility's typically many-panel tiled coverage.
- `midas_hkls.ub_refine.refine_ub_from_gvectors` not deeply profiled;
  reflection counts going in are modest, unlikely to be the bottleneck.

## Per-tab open items

- **Calibrate:** multi-distance (`autocalibrate_multi`), doublet calibrants,
  NN-residual augmenter, per-ring δr_k JSON sidecar export, full custom-calibrant UI.
- **Batch:** per-frame outlier rejection (cosmic-ray / azimuthal σ-clip in
  batch), Compton/empty/absorption in batch, Zarr/GE/EDF sources, soft
  (autograd) kernel. **Multi-azimuth + Q-uniform bins** (2026-10-05): still
  blocked (`workers.py:1510-1516` raises; `tab_batch.py` warns pre-run) —
  not a hard limitation, just unimplemented. `rebin_R_to_Q` only rebins a
  1-D profile; every eta row of the cake shares the same `r_ax`, so the same
  interpolation could run per-row to produce a `(n_eta, n_q)` cake. Would
  also need: `cake_sigma`'s per-bin weighting to survive the interpolation,
  the "true engine-collapsed" 1-D profile re-derived post-rebin (currently
  captured separately from the raw cake sum), `write_cake_h5`'s stored
  r/Q axis labeling updated, and the `tab_batch.py` guard dialog + its test
  removed. Note `write_cake_h5` already stores Q as a non-uniform companion
  axis today regardless (`radial_axes_from_r_px`) — this item is only about
  making the bin *spacing* uniform in Q.
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

## Inherited from PR #7 (merged 2026-09-03, `092fbba`/`46e0fec`)

- **No test coverage for two of the PR's six new modules** —
  `job_queue.py` (background batch-integrate job queue, `screen`-backed) and
  `peak_fit_panel.py` (GSAS-2 peak-fit view). Skipped deliberately: useful
  tests need a live `screen` session or a full widget harness, which was out
  of scope for the review session. Import-only smoke tests were rejected as
  false assurance. `provenance.py`, `zarr_cake.py` and `cake_params.py` are
  covered — see `tests/test_provenance.py`, `tests/test_zarr_cake.py`.
  **Updated 2026-10-03:** the third, `batch_cli.py`, is now partly covered by
  `tests/test_batch_cli_omega.py` and `tests/test_batch_cli_zarr_grouping.py`
  (the omega flags, the zarr-grouping flag, and the tab→argv→cfg round trip
  for both) — but nothing else in that file is exercised.
- **The per-frame output filename change is undeclared.** PR #7 moved Batch
  Integrate / Folder Monitor output from the verbatim frame id to
  `<froot>_<NNNNNN><tag>` (see DECISIONS 2026-09-02). No commit message in
  the PR mentions it. Anyone with a script globbing Batch Integrate output
  is broken by it. Needs a note to junspark, release notes, or both.

## Package-side fixes (for MIDAS maintainers — NOT done in GUI)

P0-1 normalize corrections cake · P0-2 wire Q-uniform into kernels · P1-1 finite
autograd geometry grads · P1-2 robuster tilt in one_shot/bayesian · P2-1 smooth
absorption at μR=1.5 · P2-3 fold `analyze_workflows/` round-trips into package CI.
(GUI already works around P0-1/P0-2/P1-1/P1-2 — see DECISIONS.)

**P4-1 — `midas-defect`'s `perf_solve_cell` branch never bumped its own
version number** (stayed `0.9.1` across `master` and every perf commit
through `060f84c9`), so `requirements.txt`'s `midas-defect==0.9.1` pin
cannot by itself distinguish "has `max_workers`" from "doesn't" — a bare
`pip install midas-defect==0.9.1` from a future registry build could land
either API depending what commit it was built from. Ask MIDAS maintainers
to bump the version (e.g. 0.9.2 or 0.10.0 per semver, since `max_workers`
is a new public kwarg) when `perf_solve_cell` merges to `master`, so this
pin becomes unambiguous again. See DECISIONS 2026-10-22.

**P3-1 — ~~`im_trans`/`ImTransOpt` not accepted by most calibration pipeline
entry points~~ RESOLVED UPSTREAM in `midas-calibrate-v2` 0.15.0, verified on
0.17.0 (2026-09-11).** Upstream took fix B1 from the P3-4 issue draft:
`CalibrationSpec.im_trans`, populated by `spec_from_v1_params()` from
`v1.extra["ImTransOpt"]`, applied at the entry point of every pipeline through
one shared `io/transforms.apply_im_trans()` (image + dark + mask together, with
`NrPixelsY/Z` re-derived from the transformed shape). `calib._prep_transformed()`
is therefore no longer *required* — but it is still correct and still in use:
the GUI's specs come from `calib.build_v1_params`, which sets no `ImTransOpt`,
so `spec.im_trans` is `()`, the pipelines' `if spec.im_trans:` guard is false,
and the pre-flip is applied exactly once. Removing the workaround is optional
cleanup, NOT a bug fix, and it is the one change that must not be made
half-way: pre-transforming *and* letting the spec carry the transform applies
it twice, silently (upstream flags this as the 0.15.0 behaviour change).
The remaining panel-layout half of this item is unresolved — see below.
Historical detail follows.

Only `midas_calibrate_v2.calibrate()` accepts
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
**Status 2026-09-11:** the `im_trans` half landed (above); the `panel_delta_*`
half did NOT — `AutoCalibrationResult` still has no panel fields (verified on
0.17.0), so the `four_stage` re-route in `calib.run_pipeline`'s one_shot branch
stays necessary. Tracked with P3-3, which is the same underlying gap.

**Correction (2026-08-27):** the previous note here ("`midas_gui`'s
`calib.py` already works around this correctly") was wrong. The manual
pre-flip workaround had a real bug: it flipped the image for the *solve*
call but computed the auto-seed from the *unflipped* image in the same
branch, so seed and solve ran in two different frames whenever a transform
was active — exactly the failure a user hit with Flip Z + Multi-panel
detector. Fixed in `calib.py` via `_prep_transformed()`, used consistently
for seed + solve + dark in every affected branch. See DECISIONS 2026-08-27.
This upstream ask stood until 0.15.0; the workaround is now belt-and-braces
rather than load-bearing (see the RESOLVED note at the top of P3-1).

**P3-3 — `midas_calibrate_v2.compat.to_integrate.spec_from_calibration_result()`
has no panel-layout support (found 2026-08-27; still true in 0.17.0,
re-checked 2026-09-11 — all 7 panel fields still come back 0/[]/'' and
`AutoCalibrationResult` still has no panel fields).** It sets none of
`IntegrationSpec`'s 7 panel fields (`NPanelsY/NPanelsZ/PanelSizeY/
PanelSizeZ/PanelGapsY/PanelGapsZ/PanelShiftsFile`) from an
`AutoCalibrationResult` — the same gap `TransOpt` already had (confirmed:
`_build_spec` already manually patches `spec.TransOpt` after calling this
function, for the same reason). `midas_gui` now works around it by
stashing `result.panel_layout`/`result.panel_shifts_path` (plain,
JSON-safe attributes; see DECISIONS 2026-08-27 "Feed Calibrate's
Multi-panel results to downstream integration") and patching the spec
itself via `helpers._apply_panel_fields()` everywhere a spec is built from
a calibration result. Upstream fix: teach `spec_from_calibration_result`
to read panel fields off the result the same way it already will need to
once P3-1's `panel_delta_*` exposure on `AutoCalibrationResult` lands —
these two upstream asks are related (both are "the panel_layout config +
refined shifts don't survive on `AutoCalibrationResult`").

**P3-2 — no `apply_trans_opt` hook on `*BinGeometry.from_spec(spec,
mask=mask)` (found 2026-08-25; still absent in `midas-integrate-v2` 0.7.1,
re-checked 2026-09-11 — `midas-integrate-v2` is unchanged at 0.7.1 and `azimuthal_sigma_clip()` still lacks it too).** Every `midas_integrate_v2.integrate_*`
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

**P3-4 — ~~`im_trans` and the `tx/ty/tz` seed are honoured by *disjoint* sets of
calibration entry points~~ RESOLVED UPSTREAM in `midas-calibrate-v2` 0.14.0-0.17.0
(filed 2026-09-08 against 0.13.0; fixed upstream 2026-09-10, verified here
2026-09-11).** Upstream implemented fixes A, B1, C and D from the draft
essentially as written:
* **A** — `calibrate()` gained `initial_tx/ty/tz`. Note the GUI has always passed
  these speculatively through `calib._supported_kwargs`, which silently dropped
  them on <=0.13.0; they now take effect. That is the behaviour change this bump
  introduces, and it is what `calib.run_pipeline` always intended.
* **B1** — `CalibrationSpec.im_trans` + one shared `io/transforms.apply_im_trans`,
  reaching every pipeline (see P3-1).
* **C** — `AutoCalibrationResult.im_trans`, carried into `IntegrationSpec.TransOpt`
  by `spec_from_calibration_result`. `helpers._build_spec` still assigns
  `spec.TransOpt` itself; that is now redundant but harmless — both read the same
  `result.im_trans`, and `TransOpt`/`NrTransOpt` were verified consistent.
* **D** — `first_time_calibrate()` gained `im_trans` and `tx/ty/tz`. **`im_trans` is
  now wired up (2026-09-11):** `calib.run_pipeline`'s `first_time` branch forwards
  the codes and hands over the RAW frame (the backend flips image/dark/panel_mask
  itself and re-derives `n_pixels_y/z`), so it must NOT also pre-flip. Fixing that
  exposed a second, wider bug: `workers.CalibrationWorker` took `NZ, NY =
  image.shape` off the **raw** image and passed it to `normalize_result`, so on a
  non-square detector with a transpose active every non-plain-one_shot mode
  (first_time, four_stage, bayesian, joint, partial-distortion) recorded
  `NrPixelsY/Z` for a detector it never fitted. Now `calib.effective_pixel_counts()`.
  Covered by `tests/test_first_time_im_trans.py` (19 tests, mutation-checked
  against both the dropped-codes and double-flip failure modes).
  **Still open:** the GUI does not pass first_time a *tilt* seed — 0.17.0 accepts
  `initial_tx` and feeds `tilt_prior_deg` into `ty/tz`, so `tilt_seed_effective(
  "first_time") is False` is now a GUI limitation rather than a backend one, and
  `test_calib_tilt_seed.py`'s "at any backend version" wording is stale.
* **Declined:** `refine_tx`. Upstream's reasoning is now documented in
  `forward/geometry.py`: `tx` reaches a ring radius only through the azimuthal
  harmonics, so with them free `(tx, phi_k) -> (tx + d, phi_k + k*d)` is an exact
  gauge orbit and refining `tx` corrupts all six phases with no residual
  signature. Seed it from grain spots or Friedel pairs, don't fit it from powder.

Historical statement of the problem follows.
The generalisation of P3-1/1b: `im_trans` reaches only `pipelines.auto.calibrate()`
(and `ff_calibrate.calibrate_ff_from_files()`, which delegates to it); a `tx/ty/tz`
seed reaches only the eight pipelines taking `v1_params` (carried as `init` by
`spec_from_v1_params`). **No entry point accepts both**, so a flipped detector with
a known non-zero tilt cannot be described to any single pipeline —
`calib.py:_prep_transformed()` exists precisely to work around this.
`first_time_calibrate()` honours neither (`tilt_prior_deg` only steers the
cone-aware BC seed; `_build_v1` never sets tilts). `calibrate()` further hardcodes
`tx=ty=tz=0.0` (`pipelines/auto.py:612`) and takes no `spec=` override, and
`compat/from_v1.py:49` freezes `tx` at `refined=False`, so `tx` is structurally
unreachable through `calibrate()` even though `forward/geometry.py` models it.
`AutoCalibrationResult` also does not record the applied `im_trans`.
**Upstream issue drafted** (verified matrix + runnable repro + proposed fixes A–D):
`.context/issue_draft_calibrate_v2_tilt_imtrans.md`. Ask is
additive/backward-compatible: `initial_tx/ty/tz` on `calibrate()`, an `im_trans`
field on `CalibrationSpec` (or the kwarg on all pipelines), one shared transform
helper factored out of `auto.py:418-439`, and `im_trans` recorded on the result.

## Hydra Batch Integrate: background jobs, naming, dark provenance

Three pieces of work in the same code (`workers.py` / `batch_cli.py` /
`hydra_batch_page.py`), found during the connolly_oct26 beamtime
(2026-10-12). Listed in the order they should be done — the first is what
makes a long run SAFE, the others make it correct.

### 1. `batch_cli` learns Hydra, so the Hydra page gets "Run as background job"

**DONE 2026-10-12.** Built as designed below. What the design did not
anticipate, recorded here because both were found the hard way:

* **A panel that cannot resolve its calibration is SKIPPED, not fatal.**
  Loading one panel's file pulls in all four siblings, so two uncalibrated
  panels is the ordinary state of a two-panel run. The in-process path has
  always skipped them (`_start_panel_worker` -> `_skip_panel`); the first
  cut of `_run_as_job` showed a modal error and refused the whole job, so
  the two paths disagreed about the identical setup. Only "no usable panel
  at all" is an error. The combine step then names ONLY the panels that
  actually ran — naming a skipped one makes `find_panel_zarr_sets` wait for
  an archive nobody writes, every frame looks incomplete, and the merge
  reports "wrote 0/0", which reads like success.
* **`eta_min`/`eta_max` had no CLI flag at all**, because the
  single-detector tab integrates the full circle and never needed them. A
  Hydra job without them silently integrates all 360° on every panel and
  looks plausible. Same class of bug as the omega one on `tab_batch`. Also
  added `--frame-range-*`, kept deliberately distinct from the existing
  `--frame-start/--frame-end` file-number filter.
* **A `QMessageBox` in a test hangs forked pytest forever** at 0% CPU with
  no output — it does not fail. `tests/test_hydra_batch_job.py` has a
  `dialogs` fixture for this; the hang cost 12 min to diagnose once.
* `Rmax = 0.0` (the documented "auto" sentinel) is unreachable on this page
  once a calibration is set — the card re-derives it from that panel's own
  geometry, overriding both `set_caking` and a direct `setValue(0)`.

Also wired: commands are recorded to the run-log tree AND to
`<root>/logs/<session>.commands.log` beside the data, the latter following
the MPE SAXS/WAXS workflow's convention (a scan folder ends as
`xy/ zarr/ mat/ logs/`). `~/.midas_gui/jobs/<session>.sh` already held the
chain but is keyed by session name in a directory nobody browses.

Original design, kept because it is still what the code does:

Decided 2026-10-12: **use the same job deployment interface as the
single-detector tab, with panels always SEQUENTIAL.**

Today the Hydra page has no background option, and nothing in DECISIONS
explains the omission — it was never built, not ruled out. The button on
`tab_batch.py:1856` is only a launcher for `python -m midas_gui.batch_cli`
in a detached `screen` session; `batch_cli` has no concept of panels
(grep it for `panel`/`hydra`/`ge1` — nothing), so there is nothing for a
Hydra button to launch.

This matters beyond convenience. A 189-folder run is ~17 h in-process, so
the GUI's own Qt/pyqtgraph code sits on the critical path of the job: that
is exactly how commit 1db9d31's `Cannot set range [nan, nan]` stopped a
real run after ONE folder. Headless there is no view to update and that
whole class of failure cannot occur — and the run survives closing the
GUI, logging out, or X dropping.

The mechanism is already there and needs no new concurrency. `_run_as_job`
ALREADY chains one `batch_cli` invocation per source folder into a single
screen session (a selection spanning folders maps to one `--out-dir`
each). Sequential panels makes Hydra the same shape:

    per scan:  batch_cli --out-dir <out>/ge1 ... (ge1 calib, ge1 eta range)
               batch_cli ... ge2
               batch_cli ... ge3
               batch_cli ... ge4
               <combine step>            -> hydra_zarr_combine.combine_panel_zarrs

all chained into ONE screen session, as the multi-folder case already is.
The combine needs a CLI entry of its own; `combine_panel_zarrs` is already
a clean function taking `{panel: path}`.

Everything the detached process needs must be on disk first — the job has
no access to live GUI state. The single-detector path already writes a
calibration snapshot, mask and dark/bright; Hydra needs four of each, plus
the per-panel caking ranges (which are per panel, not shared — see
`hydra_batch_widgets`).

### 2. Archive naming

Wanted (decided 2026-10-12, modelled on the pixirad output, which is one
archive per raw file holding one lineout per frame):

    4130_D_load_step_0_005120.ge1234.h5.ave.zarr.zip

Raw filename kept INCLUDING `.h5`, panel token -> `ge1234`, no frame range
and no chunk suffix (both redundant once grouping is per source file), and
the `ave` segment derived from `combine_op` rather than hardcoded.

`.ave.` is hardcoded at `workers.py` 2332, 2335, 2336, 2694 while the UI
offers Max and Median (`widgets.py` 4585-4586) — so a median or max
combine is written today into a file named `.ave.`. The op IS recorded
inside as `midas_gui_combine_op`, so nothing is lost, but the NAME LIES.

`workers.py:2918`'s `.cake` sibling shares this convention deliberately
(see `_run_out_stem`) and must change with it. ~13 name assertions in
tests/{test_batch_zarr_output,test_batch_zarr_gsas,test_h5_metadata_copy,
test_tab_zarrviewer}.py; the 112 in test_hydra_zarr_combine.py are
hand-built and unaffected.

Note the content is ALREADY right — this is purely the filename. The
combined archive holds `OmegaSumFrame/LastFrameNumber_<i>` per frame, each
(nR, n_eta summed over panels), exactly like pixirad's (nR, 1).

### 3. Record which dark was applied

`resolve_dark`/`dark_candidates` pick the nearest PRECEDING `dark_before`
(then a following `dark_after` for a file that precedes every
`dark_before`), a ladder derived from the 198-file screenlog corpus and
replayed by `test_dark_autodetect`. The logic is sound.

But the resolved dark is NOT recorded anywhere in the output: the run log
says only `field corrections: dark=y`, and a search of a real archive's
whole `provenance_history` for any dark reference finds nothing. So
"was the appropriate dark applied?" cannot be answered after the fact —
across 189 folders a single mis-selection is invisible.

`dark_candidates` already computes a `why` string ("preceding
dark_before"). Record the resolved path AND that reason in the provenance
entry, which turns an assumption into something checkable per scan.

### 4. Two things worth taking from `mpe_wf_saxs_waxs`

Compared 2026-10-12, prompted by "a lot of this question about paths have
been sorted out there". The headline is reassuring: **the output path
convention already agrees.** `mpe_paths.get_output_dir()` is
`<root>/<expid>_bc/<froot>/<detector>/`, which is exactly what this GUI
writes. The two codebases arrived at it independently; neither needs to
change.

`/home/beams3/PARKJS/opt/mpe_wf_saxs_waxs/combine_hydra_zarr.py` is a
SECOND, independent four-panel combine. It is not a duplicate of
`midas_gui/hydra_zarr_combine.py` and the two should not be merged
casually — the GUI's background path must produce byte-identical output to
its in-process path, which is why `hydra_combine_cli` wraps the GUI's own
module. But two things it records are strictly better:

* **`InstrumentParameters` stacked as a length-N array**, one entry per
  panel, with the order in `attrs['panels']`. `Distance` legitimately
  differs per panel on a hydra — and on 2026-10-12 ge1 turned out to have
  been physically moved ~24 px between the Oct 6 calibration and the Oct 8
  data, which a per-panel record in the merged file would have made
  obvious at a glance.
* **Per-panel provenance carry-forward** — all four panels' prior
  histories kept, each entry tagged `source_panel`, with
  `extra.cake_params_per_panel` / `extra.instrument_params_per_panel`
  alongside an honest aggregate. Ours keeps an `eta_sorted` flag and the
  per-panel cake_params but drops the upstream histories.

Also note its `consolidate_xy_and_cleanup` sweeps EVERY file it finds in a
per-detector folder into `logs/`. The background job's `_bg_job_*`
sidecars are written to the SCAN ROOT, not inside `ge{n}/`, so that sweep
does not relocate them — keep it that way.

## Background jobs and the dark: resolve at CURATION, not in the CLI

Decided 2026-10-20, replacing an earlier plan to teach `batch_cli`
`--auto-dark`/`--dark-dataset`/`--pick-origin`. That plan was
over-engineered; the question that killed it was "can the job curation
find the appropriate darks and then send out the jobs?"

It can, and it should. `hydra_batch_page._run_as_job` already fans out
**one `batch_cli` invocation per (scan, panel)** — see the `units` loop.
So the GUI, which already holds the policy code, can resolve each scan's
dark in-process and hand each invocation the right `--dark`. No new CLI
surface at all.

Three reasons this is better than resolving inside the job:

1. **One implementation of the policy.** Teaching `batch_cli` to resolve
   darks means `dark_policy` runs in two places and they can drift. The
   whole point of this series was that two code paths answered the dark
   question differently.
2. **Failures surface before the job starts.** Resolve at curation and
   you learn at submit time that 318 scans have no 10 s dark. Resolve in
   the job and you find out hours later in a screenlog — which is how the
   original `white_oct26` problem stayed invisible.
3. **Nothing new crosses the process boundary.** The dark already goes
   over as a pre-averaged TIFF; this only changes WHICH TIFF, and where
   the `--dark` flag is attached.

### What to change

`_run_as_job` currently writes `_bg_job_dark_ge{n}.tif` once per panel
and puts `--dark` in `base[n]`, shared by every scan. Move it:

- drop `--dark` from `base[n]` (bright/background stay — they are not
  per-file);
- in the per-scan loop, resolve with `dark_policy.resolve` using
  `self._loader.dark_request(n)` and that scan's first file;
- **dedupe by resolved source path** and write one TIFF per distinct
  dark. Naively writing one per (scan, panel) would be 190x4 files of
  16 MB — 12 GB. White_oct26 has 11 darks, so deduped it is at most
  11x4 = 44 TIFFs;
- a panel whose dark will not resolve is omitted from that scan's unit,
  collected, and reported through `mail.dark_skip_report` at submit time
  rather than after the run;
- the single-detector `tab_batch._run_as_job` needs the same treatment.

### What stays true

`batch_cli --dark` keeps its current meaning (one pre-averaged field),
so an existing job script and an older CLI still work. Nothing about the
job protocol changes.
