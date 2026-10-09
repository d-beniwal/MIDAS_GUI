# STATE — current snapshot

_Keep this under ~1 page. Permanent history lives in DECISIONS.md, not here._
_Last updated: 2026-10-08 (merge of two independent lines of work: Hydra calibration page's layout brought to parity with the single-detector Calibrate tab, and Solve Cell phases 1-3 through the reciprocal-space viewer's Panel/Crystal color modes — see DECISIONS)_

## Now working on

Nothing in progress.

Open follow-ups, none blocking:
- **Solve Cell Phase 2+ still open** (`documentation/solve_cell_handoff.md` §10): geometry-resolution tiers keyed on detector stage position, self-cal fallback, bootstrap CI/axis-doubling on the pooled fit, further multi-domain separation, and the diagnostics suite are all still future work beyond what's landed (per-panel UI, calibration-file-only geometry, dark/bright/background/mask in `_stage_ingest`, explicit frame-index→omega window, multi-panel pooled ingest, leftover-spot re-indexing, and the reciprocal-space viewer's Panel/Crystal color modes).
- **Solve Cell ingest, real-raw-data gap closed (2026-10-04).** The GUI's
  `_stage_ingest` was run against a real raw HDF5
  (`DAC_Ge_op32_c1/LaB6_exp0p02_slit0p3_002_EigPos_-75_10_30_000001.h5`,
  this machine's local S3ID_data copy of the Ge-oP32 c1 beamtime), the
  `frame_omega_pairing_calculated.csv` frame/omega window, and the matching
  ceria calibration file — this dataset is panel 6 of the already-validated
  `Ge_oP32_c1_solve_cell` reference analysis. Result reproduced the
  reference closely (`n_sectors=1`, 583 vs. 584 kept blobs). The one real
  issue found was a Detector-view diagnostic gap, not an ingest bug — see
  DECISIONS 2026-10-04 (latest, 2): a single previewed frame is almost
  always near-empty for a sparse rotation series regardless of whether the
  background model worked, which read to the user as "background almost
  non-existent." Fixed with a new max-over-rotation projection view. The
  diamond-filter/ab-initio/refine stages remain regression-tested against
  real spinel panel 1+2 CSVs (unchanged, still reproduce the known cell).
- **Solve Cell panels (new, 2026-10-04)**: dark/bright/background correction
  is applied via a small duplicated `_apply_stack_corrections` in
  `solve_cell/pipeline.py` rather than importing `helpers.
  apply_field_corrections` — deliberate, to keep the module free of
  `midas_gui` imports (its own documented portability boundary); not yet
  cross-checked numerically against the GUI's own `apply_field_corrections`
  beyond the synthetic unit test. Multi-panel pooling at ingest landed
  2026-10-05 (see "Now working on"); still open from Phase 2: the
  geometry-resolution tiers (exact-anchor/regression/self-cal) themselves
  are not implemented, so pooling today requires every panel to already have
  its own calibration file loaded by hand.
- **From junspark's own STATE.md (2026-09-29), carried forward**: `PoleFigureWorker`
  is still single-frame and takes χ/φ from its cfg — making it ω-aware across a
  series is the piece the whole omega arc (landed in this merge) exists to
  enable, and it now has a correct, verified angle to stand on. The cake HDF5 has
  still never been opened in a real viewer (needs an X11/VNC session). Metadata
  provenance was flagged as something to keep testing against.
- The Frozen-point (high-tilt) pipeline still doesn't forward the Refine card's
  ± tolerance window to the backend (`calib.py`'s `_seed_and_v1` call in that
  branch is missing `tols=tols`, unlike the identical bayesian/joint call
  above it) — disclosed with a console warning for now; the real one-line fix
  is still open. See DECISIONS 2026-09-30.
- `documentation/calibration_unification_plan.md` — the three Calibrate UI
  surfaces (`tab_calibrate.py`, `hydra_calib_page.py`,
  `hydra_geometry_card.py`) have drifted; Hydra has none of the d-spacing
  work. Phases 0–1 are the cheap half. Phase 5 (technique presets) would
  replace the AgBH-shaped special case with a conditioning advisory driven
  by the actual seed geometry, which is the more honest axis.
- Test suite needs two runs to cover: `--forked` races with `--basetemp`,
  unforked segfaults on multiple `CalibrationTab`s. See DECISIONS 2026-09-09.
- `test_apply_project_calibration_single_detector` still hits the known
  pyqtgraph teardown SIGABRT (reproduces on clean HEAD; not ours).

- Still untested (ROADMAP.md): `job_queue.py`, `peak_fit_panel.py`.
  `batch_cli.py` now has `tests/test_batch_cli_omega.py`, which covers the
  omega flags and the tab→argv→cfg round trip but nothing else in the file.
- Branch cleanup done 2026-09-10: `pr-7-strain-cake`, `test-fork-imports` and
  the four fetched `refs/remotes/origin/pr/*` refs are gone; only `main` and
  `origin/main` remain. Re-fetch any PR head with
  `git fetch origin 'refs/pull/*/head:refs/remotes/origin/pr/*'`.

## Recently completed

**2026-10-08 (latest) — Hydra calibration page's layout brought to parity
with the single-detector Calibrate tab.** Requested directly ("the layout of
the hydra calibration tab is very different... positioning of manual is
very different, the options to define tolerances is not there... I want the
hydra calibration tab layout to be exactly the same as single detector
except hydra-specific features should stay as they are"). A research pass
diffed the two tabs section-by-section first; scope was narrowed with the
user (declined: non-crystalline/d-spacing calibrant support, per-coefficient
Distortion seeding in the per-panel Manual-seed dialog — both stay Hydra's
existing crystalline-only / BC-Lsd-tilts-only shape). Four changes, all in
`hydra_calib_page.py`:
- **Refine "Limits" grid, previously entirely absent.** Added the same
  always-on ± tolerance windows (Lsd/BC/ty+tz-merged/Wavelength/Distortion,
  `tx` excluded — the crystalline backend never bounds it) that
  `CalibrationTab`'s crystalline mode has, reusing `dialogs.
  PARAMETER_LIMIT_ROWS`/`limit_window` and `calib.tol_defaults`/
  `tols_are_default` directly. Hydra needed no dsp/xtal mode-switching
  machinery (`CalibrationTab._sync_limits_mode`) since it's crystalline-only
  — new `_crystalline_tols(card)` takes the panel card explicitly, because
  the tolerance *windows* are shared across all 4 panels but each is
  centred on *that panel's own* seed. Wired into `_build_cfg` as
  `cfg["tols"]` (`calib.py`'s `run_pipeline` already reads this key — no
  backend-call changes needed) and into `_state_widgets()` for project
  round-trip.
- **Per-panel "Initial seed" card stack moved up**, from after Run (bottom
  of the page) to between Mean-of-frames and Refine parameters — where
  `CalibrationTab`'s own "Initial seed" card sits. This was the concrete
  shape of "positioning of manual is very different": a user had to scroll
  past Threshold/Mean/Refine/Advanced/Run just to reach the seed controls.
- **Fixed footer.** Working dir/Run/Abort/progress/Save All moved out of
  the scrollable column into a pinned footer (mirrors `CalibrationTab`'s
  `mid_col`/footer split). Working dir was previously nested inside the
  collapsed-by-default "Advanced" group — a real bug, not just a layout
  mismatch: the field (and Run, which needs it) was disabled until the user
  opened Advanced. Run-mode (Sequential/Parallel) stayed in the footer,
  beside Run/Abort, since it's Hydra-specific.
- **Pattern-matched three smaller mismatches:** Threshold and Mean-of-frames
  are now checkable `QGroupBox`es (card title is the on/off toggle) instead
  of a plain card with an internal checkbox, matching `CalibrationTab`
  exactly; the λ/Calibrant row is hand-built (no `S.Form().row()`, which
  stretches the two fields apart as the panel widens — same fix
  `CalibrationTab` already carries with its own explanatory comment); the
  Pixel row's spin boxes lost their stretch factor so they stay packed next
  to their checkbox instead of drifting apart on a wide panel.

Left alone, confirmed Hydra-specific: per-panel Transforms card (forced —
transforms are per-panel, calibrant is shared), frame navigation living in
the loader panel rather than a scrub bar under the viewer, panel-selector
toolbar, Save All, seed-status banner, Eta-R cake Overall toggle, per-panel
Results/Ring-Residuals stacks, no Multi-panel-detector group. Not done this
pass (noted, not requested): the single-detector viewer toolbar's
`_ring_status` label and "Lab-frame axes" toggle have no Hydra counterpart.

New assertions added to both of `tests/test_hydra_calib_ui.py`'s existing
test functions (no new pyqtgraph-building test — see that file's module
docstring): Limits defaults match `calib.tol_defaults()`, per-panel
centering, `_crystalline_tols()` None-at-defaults, `_build_cfg()["tols"]`
wiring, `get_state()`/`set_state()` round-trip, and the card-stack's new
position relative to the Refine card.
**Verified:** `test_hydra_calib_ui.py`/`test_hydra_ui.py`/
`test_hydra_batch_ui.py`/`test_calibrate_panel_save.py`/`test_smoke.py`/
`test_manual_dspacing_calib_ui.py` green per-file on a clean `HOME`;
`pyflakes` unchanged (same two pre-existing warnings only, both in
untouched files/lines); offscreen screenshots of both tabs side-by-side
confirm matching card order and that Working dir/Run/Save are enabled
without opening Advanced (previously disabled by default in Hydra).

**2026-10-08 (later) — Hydra calibration: page-level "Save All .json" /
"Save All paramstest.txt", one file per panel.** Requested directly ("there
is no option to save the calibration results [in Hydra]... make it match
the single detector tab... one separate file for each ge panel"). Per-panel
Save .json/paramstest.txt buttons already existed on each
`HydraCalibPanelCard`'s Results tab (reachable only by switching the active
panel and saving one at a time) — refactored their bodies into
`write_json`/`write_paramstest` methods so the same code runs without a
dialog, then added a "Save All" row to the Run card (always visible, next
to Run/Abort, mirroring `CalibrationTab`'s footer) that writes every fitted
panel's file at once: `<stem>_ge<N>.instr.json` into a chosen folder, or
`<stem>_ge<N>.instr.txt` (optionally from a template) derived from one
chosen output path via new `_panel_tagged_path()` — which treats
`.instr.txt`/`.instr.json` as one suffix rather than letting
`Path.stem`/`.suffix`'s single-dot split mangle them. `stem` comes from new
`HydraCalibrationPage._default_save_stem()`, the same `<expid>_<data stem>`
convention as `CalibrationTab`'s. Buttons stay disabled until at least one
panel has a fitted result (`_fitted_panels()`/`_update_save_all_enabled()`,
wired into both `_on_panel_done` and `display_stored_result` so a reopened
project's restored results also enable them); a failure on one panel is
collected and reported without blocking the others. New assertions in
`tests/test_hydra_calib_ui.py`'s existing run-orchestration test (no new
pyqtgraph-building test function — see that file's module docstring).
**Verified:** `test_hydra_calib_ui.py`/`test_hydra_batch_ui.py`/
`test_calibrate_panel_save.py`/`test_smoke.py` green per-file on a clean
`HOME`; `pyflakes` unchanged (40, same pre-existing warnings only);
offscreen screenshot confirms the new row's placement and disabled state.

**2026-10-08 — "Feed result back to seed" was silently locking in unrefined
parameters; fixed, and Hydra's manual-seed state is now visible without
opening a dialog.** Root-caused a real bad calibration (connoly_oct26 Hydra
data, all 4 panels producing garbage geometry): `tx` was locked at a
different non-physical value per panel (180°/27.3°/117.8°/180°) despite not
being in that run's Refine list — carried forward from an earlier
exploratory run by `seed_from_result()`, which used to promote ALL of
BC/Lsd/tx/ty/tz into the manual seed after every fit regardless of what was
actually refined (same bug, same code shape, in both `tab_calibrate.py` and
`hydra_calib_widgets.py`). Hydra compounds it further: `_sync_seed_checkbox`
mirrors each seed-enable flag across all 4 panels by design, so one panel's
bad promotion spreads to all three siblings. Fixed: both tabs' feedback now
gate each parameter's promotion on that run's own refine flags; Hydra's
`HydraCalibPanelCard.on_result()` gained a `refine` parameter that defaults
to `None`, so `display_stored_result()` (project restore) — which calls it
with no `refine` — can never again silently promote a historical result
into tomorrow's seed, matching the single-detector tab's existing restraint
on its own project-restore path. Separately requested: manual-seed status is
now visible without opening "Manual seed…" — the per-panel summary label
turns orange when active, and a new always-visible page-level banner shows
the shared state regardless of which panel is displayed. Confirmed (not
changed): Hydra's per-panel seed dialog already existed and was already
fully independent from the single-detector tab's (separate widget instances
throughout) — the bug was within-Hydra across runs, never cross-tab.
New/updated tests in `test_calibrate_panel_save.py`, `test_hydra_calib_ui.py`,
`test_manual_dspacing_calib_ui.py`. Full rationale in DECISIONS 2026-10-08.
**Verified:** all touched/new test files green per-file on a clean `HOME`;
`pyflakes` unchanged; headless screenshots confirm the new banner in both
states; the pre-existing `test_apply_project_calibration_single_detector`
SIGABRT reproduced identically on unmodified HEAD (not a regression).

**2026-10-07 (latest) — Calibration (single-detector + every Hydra panel)
moved from an in-process QThread to a subprocess.** Requested directly
("the separate qt processes are not stable with the gui… there's already
precedence for this in the batch integrate tab"), done unattended. New
`midas_gui/calib_cli.py` (`python -m midas_gui.calib_cli --job-dir <dir>`) —
`batch_cli.py`'s pattern, simpler (no detached `screen` session; stays tied
to the GUI's lifetime like the old QThread did). `workers.CalibrationWorker`
is now a `QtCore.QObject` wrapping a `QProcess`, duck-typing `start()`/
`isRunning()`/`requestInterruption()` so neither tab's wiring changed beyond
dropping the now-meaningless `capture_stdout` kwarg. Hand-off is
`calib_job.json`+`calib_job.npz` in the run's own scratch leaf in, a pickled
`calib_result.pkl` (CPU-detached `residual_corr_map`) out. Two real,
independent wins this unlocks: `requestInterruption()` is now a genuine
`QProcess.kill()` (the old abort could only orphan an uninterruptible
QThread and hope), and Hydra's Parallel mode no longer needs
`capture_stdout=False` — every panel's calibration is a real separate
process with its own real stdout, so full per-panel log capture is always
safe now. `helpers._LogStream` (now dead) and its `import io` deleted.
**Verified live** against real `test_data/s1ide` ge1/ge2 data: a real ~147s
successful run (BC/Lsd matching known truth), a real failure path (log
streamed live, traceback in `failed`), a real mid-run kill, and two real
calibrations run concurrently through two genuine OS processes with full,
non-cross-contaminated per-panel logs and results — not possible before
this change. New `tests/test_calibration_subprocess.py` (10 tests, backend
mocked for the fast paths, a real fast-failing subprocess for the worker's
QProcess contract). Full rationale in DECISIONS 2026-10-07 (latest).
**Verified:** pyflakes +1 over baseline (exactly the one expected
`_paths`-unused warning the new CLI file carries, same pattern as
`batch_cli.py`); every test file touching `workers.py` green per-file; a
combined-run failure set reproduced identically on unmodified HEAD (the
documented pre-existing `--forked`-races-with-many-tests flakiness, not a
regression).

**2026-10-07 (later) — Threshold curve popped into a dialog; log Y-axis
floored at 1; X locked to the detector's radius range.** The embedded
`RadialThresholdEditor` (below) was cramped in the narrow card column, so it
now lives in a non-modal `QDialog` opened via an "Adjust curve…" button —
the card itself is just checkbox + button now. Non-modal (`.show()`, never
`.exec_()`), so the rest of the tab stays usable and the main image preview
keeps updating live while it's open (unchanged `pointsChanged` wiring — same
long-lived editor instance, just reparented). Y-axis is now log-scaled via
`setLogMode(y=True)`; since that only auto-transforms `PlotDataItem`s (our
curve) and NOT `pg.TargetItem`s (confirmed empirically), every point's
position is stored/read as `(r, log10(real_y))` internally via new
`_log_y`/`_real_y` — the public API (`points()`/`set_points()`/
`pick_state()`) is unchanged, still real units only. Floor is 1.0 (not 0) —
"y min can be 1 i.e. 0 on log scale," so 1 count stands in for "zero."
X-axis: `vb.setMouseEnabled(x=False, y=True)` + `vb.setLimits(xMin=0,
xMax=r_domain)` — genuinely not pannable/zoomable; Y keeps native zoom, with
a new `set_y_view(y_max)` only setting the *default* view (not reasserted on
every drag). Default Y ceiling (and the default curve's top point) now comes
from new `helpers.median_intensity_near_bc(img, bc_y, bc_z, r_max=10)` — the
median near BC, not the raw image max, which could be a single saturated
pixel dominating the whole default scale. New tests in
`test_radial_threshold_editor.py` (floor/log-roundtrip/axis-lock) and
`test_helpers.py` (`median_intensity_near_bc`). Full rationale in DECISIONS
2026-10-07 (later).
**Verified:** all touched/new test files green per-file on a clean `HOME`;
`pyflakes` unchanged (39 warnings); `get_state()`/`set_state()` round-trip
re-confirmed on both tab classes; offscreen screenshots confirm the compact
card and a correctly-rendered log-scale popup (10/100/1000 tick labels,
curve bottoming out at 1).

**2026-10-07 — Power-law threshold superseded by an interactive drag-point
curve (`widgets.RadialThresholdEditor`), no formula at all.** Spinbox-driven
parametric curves weren't effective for the user to shape a threshold by
feel; chose a free-form curve (not a fit of the old formula to dragged
points) when asked, including allowing a dragged point to sit above its
neighbour (a "bump" — fully free-form, no monotonicity constraint). Both the
Calibrate tab and Hydra page's threshold card are now a `pg.PlotWidget` (x =
radius px, y = intensity) holding 2–10 draggable `pg.TargetItem` control
points connected by a monotone-cubic (PCHIP) spline through them, flat
beyond the first/last knot. New `helpers.radial_spline_values`/
`radial_spline_threshold_map`/`default_radial_threshold_points` replace
`radial_power_threshold_map` wholesale; `apply_radial_threshold` keeps its
name, now takes `(radii, values)` arrays. The widget's own drawn curve and
the real per-pixel mask call the exact same interpolation function, so they
can never disagree. Drag clamps: radius against neighbours (can't cross),
y to `>= 0` only. Double-click empty space adds a point, double-click an
existing point removes it. `set_editable(False)` disables the whole widget
(`QWidget.setEnabled`, covers drag/pan/zoom/double-click for free) and
recolors it gray — needed because custom `QGraphicsView` painting doesn't
pick up Qt's native disabled look. State: the curve's points have no widget
of their own, so they round-trip via a new `thr_points` top-level key in
`get_state()`/`set_state()`, following `PickableImageViewer.pick_state()`'s
exact precedent; an old saved project missing that key just keeps fresh
defaults. One real bug fixed while wiring this up: Hydra's "Apply threshold"
is a plain `QCheckBox` with no native enable-cascade (unlike the
single-detector tab's checkable `QGroupBox`), so its `set_state()` needed an
explicit resync call or a restored checked state left the editor looking
disabled. Full rationale in DECISIONS 2026-10-07. New
`tests/test_radial_threshold_editor.py` (10 tests, drives the widget via
`TargetItem.setPos()` directly — no `QTest` mouse-event simulation exists in
this suite); `test_helpers.py`, `test_calib_radial_threshold.py`,
`test_hydra_calib_ui.py` updated for the new function/widget.
**Verified:** all touched/new test files green per-file on a clean `HOME`;
`pyflakes` unchanged (39 warnings, same list); `get_state()`/`set_state()`
round-trip of `thr_points` confirmed by direct script on both tab classes;
offscreen screenshots confirm the plot renders with labeled draggable
points and the disabled state is visibly dimmed.

**2026-10-06 (later) — Radial Gaussian threshold superseded by a 4-knob
power-law step (peak/floor/r0/steepness). Superseded the very next day by
the drag-point curve above** — kept here only as a one-line pointer; no
part of this design survives (spinboxes, formula, and
`radial_power_threshold_map` are all gone). Full rationale in DECISIONS
2026-10-06 (later), for history only.

**2026-10-06 — Calibrate: scalar threshold replaced with a radial Gaussian
(amplitude/location/scale from BC); Pick BC/Ring no longer auto-activates
Manual seed. Superseded later the same day by the power-law entry above** —
kept here only for the Pick-BC/Pick-Ring "don't auto-activate Manual seed"
half of this change, which is still current: `_on_bc_picked`/
`_on_ring_fit_bc` (both tabs) no longer call `_enable_seed(BC=True)`; a pick
only populates the BC value, the user must tick Manual seed themselves.
Also still current: Hydra's `_reset_threshold_defaults_for_active_panel()`,
called only when the underlying raw image actually changes (extracted after
finding `_refresh_display()` used to recompute threshold defaults on every
call, including from editing those very fields). Full rationale in DECISIONS
2026-10-06.
**Verified:** all touched/new test files green per-file on a clean `HOME`;
`pyflakes` unchanged (same pre-existing warnings only); offscreen screenshots
of both new threshold cards confirmed the 3-spinbox layout.

**2026-10-05 (latest, 3) — Solve Cell: reciprocal-space viewer gets Panel/Crystal color modes, an unindexed toggle, and live stats.** The 3-D reciprocal-space map
got a "Color by: Panel / Crystal" mode switch, a "Show unindexed/spurious
spots" toggle, and a stats label that updates with each. Panel mode colors
every currently-shown spot purely by its `panel_id` (bucketed as
"(single panel)" when that column doesn't exist); Crystal mode keeps
today's look — diamond/gasket fixed red, each solved domain its own color,
leftover/unassigned grey — but now diamond/gasket spots persist across
Ab-initio/Refine instead of disappearing once those stages overwrite the
plot (new `self._flagged_df` state, set in `_on_diamond_done`). The
unindexed toggle hides only the leftover/unassigned population — diamond is
a known category, not "spurious", so it's unaffected by the toggle.
`_update_recip_plot`'s own signature/behavior is untouched; one new
`_refresh_recip_view()` is now the single place that reads pipeline state +
the two controls and redraws + updates the stats label — it replaces the
ad hoc `_update_recip_plot(...)` calls after Ingest/pooled-Ingest/Diamond
Filter, and `_update_multidomain_plot()` is now a thin wrapper delegating
to it (kept so existing callers/tests are unaffected). Each domain dict
gained a `"panel_ids"` array (parallel to its existing `"xyz"`, `None` if
the claiming dataframe had no `panel_id` column) so Panel mode can bucket a
solved domain's own spots by panel too — `_add_domain`/`_replace_domain`
take it as a new optional keyword arg. No `pipeline.py` changes needed;
every column used (`panel_id`, `is_diamond`, `qsample_x/y/z`) already
existed.
Verified: 8 new tests in `tests/test_solve_cell_ui.py` (mode/checkbox
defaults, `_refresh_recip_view` not raising with no data, Panel-mode stats
on single-panel and pooled 2-panel data, hiding unindexed, Crystal-mode
stats with diamond+2-domains+leftover and the "(hidden)" footnote, a
domain's own panel_ids bucketing correctly, mode/checkbox changes
triggering a refresh). Full file green (60/60), `test_solve_cell_pipeline.py`
unaffected (34/34), `pyflakes` clean. Offscreen screenshots of a synthetic
2-panel/2-domain/diamond/leftover scene confirmed Panel mode shows two
distinct panel colors, Crystal mode shows diamond(red)/domain1/domain2/
leftover(grey) as four distinct colors, and unchecking the toggle removes
only the grey leftover points.

**2026-10-05 (latest, 2) — Solve Cell: multi-panel pooled ingest.** Solve Cell's "Run Ingest" now pools
multiple panels. With one panel configured, nothing changes; with more than
one panel ready (its own calibration + data loaded), each ready panel is
ingested independently against its own geometry, then every panel's own
g-vectors are concatenated into one combined set — tagged by a new
`panel_id` column — for Diamond Filter/Ab-initio/Refine/Index-remaining-
spots downstream (new `pipeline._stage_ingest_pooled`, dispatched as stage
`"ingest_pooled"`). Those four downstream stages needed NO changes at all:
they already only ever consumed whatever `spots_df`/`candidate_df` ingest
handed them, regardless of panel count. A not-ready panel tab (no
calibration/data yet) is skipped with a log line, not blocked. Per-panel
provenance logging and the Detector tab's spot overlay/background preview
are now panel-aware too (`panel_id`-filtered overlay, a
`_ingest_preview_by_panel` dict keyed by panel so switching panel tabs shows
that panel's own preview, not whichever panel ingested last). See DECISIONS
for why per-panel sequential frame loading (not eager) was necessary, and
why plain concatenation of independently-ingested panels is physically
correct here.
Verified: new pipeline tests (`_stage_ingest_pooled` tagging/concatenation/
per-panel-dir-writing/pooled-dir-writing, sequential frame-loading discipline,
empty-panel-list error, dispatch via `run_stage`, and a synthetic two-"panel"
concatenation recovering a known cell without needing gated real reference
data); new UI tests (pooled dispatch vs. single-panel fallback, not-ready-
panel skip + log message, pooled completion handler state, panel-filtered
spot overlay, per-panel pooled provenance logging). All green per-file
(pipeline 34/34, UI 52/52, project 29/30 with the one known pre-existing
SIGABRT, smoke 13/13); `pyflakes` clean on every touched file. A real,
non-mocked two-panel run through an actual `QThread`-backed
`SolveCellWorker` (offscreen, synthetic frames) confirmed the combined
`spots_df` carries both panel ids and the right per-panel spot counts.

**2026-10-05 — Solve Cell: repeatable leftover-spot re-indexing (multi-domain).** Solve Cell can also re-index whatever ab-initio leaves unassigned,
repeatably (landed earlier the same day). A "5. Index remaining spots"
section (`tab_solve_cell.py`) lets the user re-run indexing on the leftover
pool either "Free (ab-initio)" (a fresh unknown structure) or "Known
structure" (search for an orientation of the most recently solved domain's
own cell — new `pipeline._stage_index_known_cell`, a from-scratch
random-rotation search against a fixed B-matrix validated by a null test,
per handoff §5.4.2/§5.6; no `midas_hkls` function does this already —
confirmed by reading `cell_constrained.py`/`cell_series.py`, both require
hkl already assigned). Round 1's own refine result is now "Domain 1" in a
new domain list (`QListWidget`) rather than a single overwritten result
grid; each further round appends a domain, tracked via a `_claimed_index`
union so no two domains can claim the same spot. The 3-D reciprocal map
colors each domain distinctly (`_update_multidomain_plot`). Session-only,
like every other Solve Cell stage's results (no new `project.py`
persistence) — see DECISIONS for the full scope-decision rationale.
Verified: new pipeline tests recover a synthetic second domain's cell and
correctly reject a pure-noise leftover pool (no spurious domain); new UI
tests cover domain bookkeeping, round-1 rerun not duplicating itself, and
the 3-D plot with multiple domains. Offscreen screenshot confirmed the
domain list and distinct per-domain colors on a real (non-mocked)
two-stage pipeline run.

**2026-10-05 — Solve Cell: Detector view circles ingest spots per frame;
ingest results now persist into the project `.h5`.** Two user-requested
features, both built on data `_stage_ingest` already computed:
- **Spot overlay.** `spots_df`'s `frame` column indexes the *kept* stack
  (post omega-window + `live_frames` filtering), not the *originally loaded*
  stack `DataLoaderPanel.frame_index()` scrubs through — the one real gap.
  `_stage_ingest` now also computes `kept_loaded_idx` (an index array from
  the same `omega_window_mask`/`live_mask` it already builds) and uses it to
  add a `loaded_frame_idx` column to `spots_df` and a `loaded_frame_index`
  key to the `preview` dict, so every Detector stage can compare against one
  consistent frame-index basis. `tab_solve_cell.py` adds one persistent
  `pg.ScatterPlotItem` (open yellow circles) to the Detector view, updated by
  new `_update_spot_overlay(stage, current_loaded_idx)`: Raw/Corrected/the
  single-frame background preview show only the spot(s) whose nearest frame
  exactly matches the one on screen (confirmed as the wanted behavior over
  showing a spot across its whole blob-width span — simpler, and each spot's
  rounded centroid is already "its closest frame" by construction); the
  max-over-rotation projection shows every spot (the image itself already
  collapses the whole rotation); the mask stage shows none.
- **Project persistence.** `SolveCellTab` had no `set_project_context` at
  all until now (unlike every other FAIR-provenance tab) — added, wired into
  `app.py`'s existing wiring loop. New `project.append_solve_cell_ingest_attempt`/
  `read_solve_cell_ingest_results`, modeled directly on
  `append_integration_attempt`, write to
  `/analysis/solve_cell/<panel_key>/attempt_NNNN` (`panel_key` =
  `solve_pipeline.panel_dir_name(panel_id)`, matching the on-disk CSV
  naming). `spots_df` is embedded whole as one compound HDF5 dataset
  (new `project._write_dataframe`/`_read_dataframe`) rather than
  column-by-column, since its schema comes from `midas_defect` and can grow.
  **Scope is the ingest stage only** (per the request) — diamond
  filter/ab-initio/refine stay session-only, same as before; a natural,
  separately-scoped follow-up. `_on_ingest_done` calls the new
  `_log_ingest_to_project` with `inputs` rebuilt fresh from panel/widget
  state, deliberately not the `cfg` dict handed to `SolveCellWorker` — that
  dict is mutated in place by the worker thread (its `frames_loader`
  callable is popped and replaced with the fully materialized raw stack
  array), so logging it directly would risk dragging a multi-GB array into
  the metadata path.

New tests: `tests/test_solve_cell_pipeline.py` (+1: `loaded_frame_idx`
mapping on a stack with a non-trivial, offset omega window, confirming it
isn't just the identity), `tests/test_project.py` (+2: round-trip and
empty-`spots_df` embedding), `tests/test_solve_cell_ui.py` (+7: scatter item
is attached to the view, per-stage filtering behavior for all 5 stages,
project-context logging guarded-skip and actual-write cases).
**Verified:** all four touched/new test files green per-file on a clean
`HOME` (`test_solve_cell_pipeline.py` 26/26, `test_solve_cell_ui.py` 36/36,
`test_project.py` 11/11 + the one known pre-existing
`test_apply_project_calibration_single_detector` SIGABRT, `test_smoke.py`
13/13); `pyflakes` clean on every touched file. Offscreen screenshots
confirmed circles land on the correct (row, col) pixel positions on the
max-projection stage (all 4 synthetic spots shown) and correctly filter down
to only the matching spots on a specific Raw-stage frame index (2 of 4).
**Not verified:** no real ingest run + reopened project available locally to
see a logged attempt's spot table read back by a human (the round-trip is
covered by `test_project.py`, not by driving the actual GUI against real
raw data).

**2026-10-04 (latest) — Solve Cell: project save/restore wired up (was
missing entirely), Detector tab's "Background-subtracted" renamed to
"Calculated background," pan/zoom no longer resets on a Stage combo switch,
red cross marks q=0 on the 3-D reciprocal-space map.** All four were
user-reported after using the tab. The project-save gap was a genuine bug,
not a design choice: `SolveCellTab`/`PanelCard` had no `get_state`/
`set_state` at all, so `app.py`'s generic per-tab save/restore loop silently
skipped them — now fixed, following the same contract every other tab uses
(configuration only; Ingest/Diamond/Ab-initio/Refine results are not
recomputed on restore, same as every other tab). Full detail, including why
the zoom-vs-levels split works the way it does, in DECISIONS.
**Verified:** `tests/test_solve_cell_ui.py` 28/28 (+6), `tests/
test_solve_cell_pipeline.py` 24/24 (unaffected), `test_smoke.py` 13/13 on
retry (first run's 11 SIGABRTs were the documented pre-existing teardown
flake, unrelated tests). `pyflakes` clean. Offscreen screenshots confirmed
the origin cross and the renamed combo label.
**Not verified:** no real Ingest run available locally to see the renamed
stage against real data (same raw-HDF5 gap as below); save/restore checked
via direct `get_state`/`set_state` round trip, not a literal `.h5` file
write/read (generic and already covered elsewhere — see DECISIONS).

**2026-10-04 — Solve Cell: ingest performance fixes, 3-D reciprocal-space
map, new Detector view.** Profiling `Run Ingest` on a real 2880×2880, ~587-
frame DAC dataset found the dominant cost isn't the obvious float64 upcast —
it's `choose_sectors()` (`midas_defect.ingest`) grid-searching 5 azimuth-
sector candidates `(1,8,24,48,96)`, each a full per-frame
`subtract_background` + `count_signed_blobs` pass (measured ~50+ min total),
plus `_stage_ingest` **redundantly rerunning `subtract_background` a 6th
time** at the winning sector count even though `choose_sectors` already
computed and discarded that exact result (verified bit-identical before
removing it). Fixed everything short of the one upstream-only fix
(parallelizing `midas_defect`'s internal per-frame loop — out of scope, no
MIDAS-side changes right now):
- `sub = bg_choice.stack` replaces the redundant rerun (`pipeline.py`).
- New `blobs.sector_candidates` cfg override (GUI: "sector candidates"
  field, default `1,8,24,48,96`) lets a narrowed search skip candidates once
  a panel/geometry's winning `n_sectors` is already known from a prior run's
  log line.
- `float64` → `float32` throughout ingest/corrections, and
  `_apply_stack_corrections` now mutates in place (`out -= d` etc. instead of
  `out = out - d`) instead of allocating a new full-stack array per
  correction step — on this machine's 36 GB RAM, the old float64 stack alone
  (~39 GB for 587×2880²) exceeded physical memory before any corrections
  were even applied.
- The raw-stack disk read (`DataLoaderPanel.full_stack()`, ~10 GB for an
  HDF5 source) moved off the GUI thread: `tab_solve_cell.py` now passes
  `cfg["frames_loader"]` (the unbound method) instead of a materialized
  array, and `workers.SolveCellWorker.run()` resolves it inside the worker
  thread before dispatching to `pipeline.run_stage` — the GUI no longer
  freezes with no progress shown during that read. `pipeline.py`'s own
  `cfg["frames"]` contract is unchanged.

Also two visualization asks, both on the Solve Cell tab's right-hand results
panel (now a `QTabWidget`, `self._view_tabs`):
- **Reciprocal space map** upgraded from a 2-D qx/qy `pyqtgraph` scatter to a
  true 3-D qx/qy/qz plot — `matplotlib`'s `mplot3d` embedded via
  `FigureCanvasQTAgg` (this project's third embedded matplotlib canvas, same
  pattern as `peak_fit_panel.py`/`tab_zarrviewer.py`), not
  `pyqtgraph.opengl` — `PyOpenGL` isn't installed and would be a new native
  dependency this project has reason to be cautious about (see the
  unresolved Windows `midas_calibrate_v2` import issue below). Free
  mouse-drag rotation, zero new dependencies. The handoff doc (§9.2) had
  flagged this exact choice as an open question deferred to Phase 5; decided
  here since the feature was explicitly requested now.
- New **"Detector"** tab: shows the active panel's real 2-D detector frame
  at a chosen stage (Raw/Corrected read live from `DataLoaderPanel.
  current_frame()`/`.corrected()`, no Ingest run needed — wired to its
  `dataChanged`/`fieldsChanged` signals, the same live-refresh pattern
  `tab_batch.py`'s own "Detector view" already uses; Background-subtracted/
  Mask come from a new `preview` key `_stage_ingest` returns — exactly one
  frame's subtracted image + the mask, not the whole stack, to avoid
  reintroducing the memory pressure just fixed above). Frame-nav scrub bar
  reuses the app-wide `frameNavBtn`/`frameNavSlider` convention.

New tests: `tests/test_solve_cell_pipeline.py` (+3: sector-candidates
override, preview capture, preview clamping), `tests/test_solve_cell_ui.py`
(+5: view-tabs exist, 3-D plot update doesn't raise, stage-switch with no
data doesn't raise, Detector tracks active-panel switch, sector-candidates
parsing). **Verified:** both files green per-file (24 + 22 tests), full
`test_smoke.py` green, `pyflakes midas_gui/*.py` unchanged at 39 warnings
(none new). Offscreen screenshots confirmed the 3-D plot renders with real
spot data and all four Detector stages (Raw/Corrected/Background-subtracted/
Mask) render correctly end-to-end on synthetic frames.
**Not verified:** no real 587-frame dataset is available in-repo to re-time
the actual ingest wall-clock speedup; the synthetic benchmarks from the
profiling session are the basis for the fix, not a before/after timing on
real data.

**2026-10-04 — New "Solve Cell" tab (Phase 1): single-panel, known-geometry
deterministic unit-cell solving.** First slice of
`documentation/solve_cell_handoff.md`'s multi-phase build: ingest (raw
frames → background-subtracted 3-D blobs → g-vectors, via `midas_defect`) →
diamond/anvil 2θ-proximity filter → blind ab-initio indexing → free UB/cell
refinement (both via `midas_hkls`). Per user decision, the pipeline logic
lives embedded at `midas_gui/solve_cell/pipeline.py` rather than as a new
standalone package — but that module has zero `midas_gui`/PyQt5 imports by
design, so lifting it into a standalone `midas_solve_cell` package later is a
plain directory move. `midas_gui/tab_solve_cell.py` (`SolveCellTab`) is the
thin Qt frontend; `workers.SolveCellWorker` is the one-worker-per-stage
`QThread` dispatcher (handoff §4.3's own template). Registered as a new
optional tab (ships hidden, like Corrections/PDF/Texture/Results & Export).
New dependency: `midas-defect==0.9.0` (pinned in requirements.txt/
environment.yml); pulled in `pandas` as a transitive dependency that was
previously unpinned/floating in this repo — now pinned explicitly too
(`pandas==3.0.6`), verified against a broad pandas-touching test subset with
no regressions found.

**g-vector convention is the single most important correctness point** (see
the pipeline module's own docstring): `midas_defect` natively produces
`q = 2π/d`; `index_ab_initio` accepts `two_pi=True` but *always* returns
`UB`/`cell` in `1/d` regardless of the flag; `refine_ub_from_gvectors` has
**no** `two_pi` parameter at all and returns whatever convention its input
carries. One conversion boundary (`_to_inverse_d`) handles this, right before
the refine call. Caught one real bug implementing this: initially called
`to_conventional_from_fit` (a covariance-tolerance-aware variant) instead of
the validated reference scripts' own plain `to_conventional(fit.cell)` — the
covariance-derived tolerance was too strict on real 2-panel spinel data and
missed the cubic symmetry the plain default-window search correctly finds
(reproducing the known `a≈7.9672 Å`). Fixed to match the reference exactly.

**Verified:** `tests/test_solve_cell_pipeline.py` (18 tests, no Qt) includes
real-data regression tests against the already-computed spinel panel 1 and
panel 1+2 `spots_g*.csv` files (present locally even though the raw HDF5
frames that produced them are not — those live only on the beamline
cluster), reproducing the known `a≈7.9672 Å` cell within a generous band;
`tests/test_solve_cell_ui.py` (8 tests, forked, Qt). Both green per-file.
Full per-file sweep of touched existing files (`app.py`, `constants.py`,
`workers.py`, `test_smoke.py` ×3, plus a pandas-touching subset: `test_
project.py`, `test_batch_data_source.py`, `test_batch_cake_h5.py`, `test_
batch_queue_model.py`, `test_2d_csv_output.py`, `test_queue_policy.py`,
`test_batch_job_results.py`) green except the one known pre-existing
`test_apply_project_calibration_single_detector` SIGABRT. `pyflakes` clean on
every new file, zero new warnings on touched files.
**Not verified with eyes on it beyond an offscreen screenshot:** no live run
against real raw frames (none available locally); the GUI has not been
driven end-to-end by a human against a real dataset.

**2026-10-01 — Calibrate: predicted rings now bounded by true detector
coverage, not a fixed 30°.** `helpers._predict_ring_radii` generated
candidate rings from the calibrant's wavelength/d-spacings with a hardcoded
`two_theta_max_deg=30.0`, decided before any detector geometry was
consulted — so on a short-Lsd/wide-detector/off-centre-beam geometry whose
real coverage exceeds 30°, real rings beyond it were never generated at all
(the single-detector image overlay, its radial-profile ring markers, and the
Hydra multi-panel overlay all fed from this one function). New
`helpers.max_two_theta_deg()` computes the true max 2θ from the farthest
detector corner (reusing the same beam-centre/corner-distance reasoning as
the existing `rmax_corner_px`), with a safe fallback to 30° only when
detector dimensions aren't known yet. Also fixed the redundant post-hoc
pixel filter in `tab_calibrate._draw_rings`/`hydra_calib_widgets._redraw_rings`
(`max(NrPixelsY, NrPixelsZ)` → `rmax_corner_px(...)`), which was an
axis-aligned approximation, not the true corner distance, and could have
clipped a few farther rings even after the generation-side fix. New test
`tests/test_helpers.py::test_predict_ring_radii_uses_detector_coverage_not_fixed_30deg`.
**Verified:** the 5 touched/related test files green per-file on a clean
`HOME` (helpers/manual-dspacing-ui/manual-fit-conditioning/batch-queue-ui/
hydra-calib-ui), plus `test_smoke.py`; `pyflakes` unchanged (same
pre-existing warnings only).

**2026-09-30 — PR #11 (junspark) merged into `main`: 48 commits, 8 staged
checkpoints, 3 real bugs found and fixed along the way.** Full rationale,
per-checkpoint GUI-risk table and verification detail in DECISIONS. Headline
additions: new Zarr Viewer tab (visible by default), a cake-parameters editor
dialog, omega (rotation-angle) tracking end-to-end through Batch Integrate,
✕-to-close on every optional tab, the horizontal polarization-plane fix, and
assorted Calibrate/Corrections polish. `main` was never touched mid-flight —
everything happened on a disposable `merge/pr11-staged` branch, checkpoint by
checkpoint, with a test sweep + offscreen screenshot diff after each before
advancing. Full 75-file per-file sweep is green except the one known
pre-existing `test_apply_project_calibration_single_detector` SIGABRT.

**2026-09-29 — ω verified live, then made to say what it means; three bugs
out.** Six commits, `4c7b776`…`dc24f12`, pushed. You confirmed a real run's
zarr `/Omegas` are correct — that closes the live-verification item the omega
work had been carrying, and everything below was written on top of it.

- **ω restarts at every file** (`236ffa0`). The global ramp meant the computed
  `OME_START`/`OME_STEP` ramp and a *measured* ω channel indexed two different
  axes for the same frame, and a file-number filter silently rebased the ramp
  anyway (dropped files were gone before the source saw them). One rule now:
  ω is measured from raw sub-frame 0 of the rotation the frame came from — an
  HDF5 sub-frame stack is one rotation, a one-frame-per-file series is one.
  `raw_window_for_index` is now literally `omega_channel_window(idx)[1:]`, so
  there is one place that decides what the axis is.
- **The GUI says frame numbers and angles are the same axis** (`1bb4341`).
  The cake summary line and the loader's range hint both map the sub-frame
  range to the angle range, spelling out Δω/sub-frame **vs** Δω/frame — the
  `OME_SUM` multiplication nobody should have to do in their head. A loaded
  rotation with no angles set now says so rather than quietly producing
  all-zero `/Omegas`. `DataLoaderPanel` still knows nothing about ω; it takes
  a callable (`set_omega_hint_fn`), so every other tab's hint is unchanged.
- **Background jobs wrote ω = 0** (`dc24f12`). The `batch_cli` argv carried no
  omega flags at all, so "Run as background job" recorded zeros while Start
  Integration recorded the right angles, and nothing could report it. Fixed by
  serialising the config rather than re-deriving it; start/step go out even at
  0/0 so the command line in the Logs tab always states what the job will
  record.
- **2D CSV wrote nothing with multi-azimuth off** (`6ed405d`), and reported a
  file it had not written, under the wrong name. `cake_2d is not None` had
  come to mean both "a cake exists" and "fan out per η"; those are now
  separate, and `write_profile` raises rather than no-op'ing.
- **One frame no longer costs a whole-file decode** (`520ae44`). Previewing
  one frame of the 1442-sub-frame VAREX file (23.9 GB) read all of it: ~230 s
  and ~1.9 GB to draw 33 MB, paid again by every parallel worker. Now ~4 s /
  415 MB. This is the second half of the fix whose first half (counting
  without decoding) landed earlier.
- **`kill -USR1` dumps every thread's stack** to `~/midas_gui_hang.log`
  (`4c7b776`) — a freeze leaves no traceback, and that is exactly when the app
  can no longer be asked anything.

**Verified:** full suite 1,149 collected, 2 failed / 3 skipped — the same
known pair (`test_pva_live_source_roundtrip`, a system `libstdc++` CXXABI
mismatch, and `test_apply_project_calibration_single_detector`, the pyqtgraph
teardown SIGABRT) and the same three skips. Four new test files, ~890 lines.
**Not verified with eyes on it:** the two new readouts have not been seen
rendered — they are pinned by `tests/test_omega_readout.py` at the text level
only, and the exact wording in a narrow panel wants a look.

**2026-09-29 — Upstream's cake HDF5, merged.** `cc1045d` (merge) and
`43c672c` (docs), pushed. Upstream's two 2026-09-28 Batch Integrate commits —
`31e904c` (multi-azimuth HDF5 output, new `midas_gui/cake_hdf5.py`) and
`61feeb3` (flatter layout, 2θ/d/Q axes, wider provenance) — land on exactly
the code the omega work had rewritten. Only `workers.py` and `tab_batch.py`
conflicted; DECISIONS 2026-09-29 records which side won where. Three things
worth knowing without opening that entry:

- Upstream's `all_omegas` is frame *indices* (the combined-HDF5 `<lo>_<hi>`
  stem); this fork had already split that list in two, so upstream's became
  `all_frame_idx` and its append condition widened to the union of both
  guards. No behaviour on either side changed.
- Upstream hoisted the BinArea count out of the `want_zarr` branch to share
  it with the cake writer, but hoisted the version that hands `geom` straight
  to `count_cake` — `None` on the corrections path, the crash this fork had
  already fixed for zarr. Resolved to upstream's structure with this fork's
  geometry fallback.
- `write_cake_h5` gained an `omegas` dataset, so the fork's angle reaches the
  cake file too — that file is what a pole figure over a rotation series
  would read.

**Verified:** full suite 1089 passed / 2 failed (the same known pair) /
3 skipped, with upstream's own `tests/test_batch_cake_h5.py` (+12) green and
two new tests of ours pinning the cake file's omegas and its survival of the
corrections path.
**Not verified with eyes on it:** the cake HDF5 has never been opened in a
viewer here, and no live run has produced one.

**2026-09-29 — Omega: a real rotation angle on every frame, and in the
zarr.** `1117cc1`, pushed to `origin/main` along with the eight commits that
had been sitting local (the 2026-09-28 entry below said "nothing pushed"; that
is no longer true).

- The cake CSV's `OME_START`/`OME_STEP` stop being carried-and-ignored. One
  formula covers every case — `ω = OME_START + mean(raw sub-frame indices of
  the frame) × OME_STEP` — which reduces exactly to mpe_wf's own
  `ome_start + (idx*ome_sum + (ome_sum−1)/2)*ome_step`, and to the mean of the
  collapsed window when the loader combined everything into one frame.
  `cake_params.omega_for_window`/`omega_series` own it.
- The raw indices come from new `raw_window_for_index` methods on
  `_HDF5StackGlobSource` and `_ChunkCombinedFileSource` (sources without one
  fall back to `(i, i)`). **Superseded the same day** — they were global
  across the run; `236ffa0` makes the HDF5 one restart at every file. See the
  entry above.
- Two new inputs in the Cake parameters dialog, outside the nine CSV columns:
  an editable **omega channel** combo (blank = the computed ramp; populated
  from the loaded HDF5 by new `helpers.list_h5_1d_datasets`) and an
  **averaged/summed** override that gives every frame the one run-wide mean.
- Where it lands: the zarr's `/Omegas` (both writers), the combined HDF5's
  `omegas` dataset, the attempt record (`results/omegas`), and the Save button's
  `integrated.h5`. The export path *stores* rather than recomputes, and tags
  the provenance entry with `omega_source` = recorded / recomputed from
  `omega_cfg` / unavailable.
- **Behaviour change:** `/Omegas` used to hold the frame index labelled as
  degrees. An unconfigured run now writes `[0.0, 0.0, …]` — a stationary sample
  really is at ω = 0, and that is a better wrong answer than an index.
  Deliberate; see DECISIONS 2026-09-29.
- `PoleFigureWorker` was left alone — making it ω-aware across a series is the
  next piece, and now has a correct angle to stand on.

**Verified:** full suite 1073 passed / 2 failed, both the known pre-existing
pair (`test_pva_live_source_roundtrip`, `test_apply_project_calibration_single_detector`);
48 new tests across five files, one new (`tests/test_omega_windows.py`), and a
re-run of the ten files touched after that suite started (190 passed). Offscreen
check confirmed the summary line, the dialog round trip, and `SPEC` still being
exactly `CAKE_KEYS`. Note `test_app_builds_offscreen`, which CLAUDE.md lists as
a third known failure, passed both times here — its tab-count assertion depends
on the active profile's tab set, so CLAUDE.md was left as-is rather than
rewritten off two green runs.
**Verified live 2026-09-29:** a real run's zarr `/Omegas` are the angles
expected. Not walked through: the averaged/summed override (every frame
reporting the one run-wide angle) and a measured ω channel picked from the
combo — both still only pinned by tests.

**2026-09-28 — The source HDF5's `instrument/` tree reaches the zarr; an ✕
closes any optional tab.** Three commits, all local, nothing pushed.

- `7414502` — every optional tab gets an ✕ that is the same act as unchecking
  it in Preferences ▸ Tabs (widget kept, choice persisted to the active
  profile); the four pinned tabs have theirs stripped. Same commit wrote down
  what GSAS-II's importer *actually* reads from a MIDAS zarr and corrected a
  factually wrong claim in `gsas_export.py`'s docstring about the sidecar
  convention.
- `35a7b8b` — new `midas_gui/h5_metadata.py` copies `instrument/` +
  `active_instrument/` out of the source HDF5 and into the finished
  `.zarr.zip`, wholesale, from both writers (Batch Integrate and the GSAS-II
  export, the latter rebuilding the source from the attempt's recorded
  `src_cfg`). `provenance.rewrite_zip`/`stamp_extracted` factored out so the
  copy and the provenance stamp share one extract/repack pass.
  **Costs ~0.15 s and ~130 KiB per output frame** — see DECISIONS for why
  there is deliberately no opt-out, and what the cheapest one would be if the
  cost turns out to bite on a long scan.

**Verified:** full suite green apart from the two known pre-existing failures
(`test_pva_live_source_roundtrip`, the system libstdc++ CXXABI mismatch; and
`test_apply_project_calibration_single_detector`, the pyqtgraph teardown
SIGABRT). New tests: `tests/test_h5_metadata_copy.py` (+7),
`tests/test_zarr_layout_parity.py` (+1 cross-writer instrument-tree test),
`tests/test_smoke.py` (+3 tab-close tests).
**Not verified with eyes on it:** no live X11 session — the ✕ and the
enriched Zarr Viewer tree still want a look on a real run.

**2026-09-26 — Batch Integrate: "stride" replaced by unified "Combine
sub-frames" (HDF5 + TIFF alike).** `DataLoaderPanel`'s start/end/stride +
HDF5-only "Combine sub-frames" consolidated into one control: stride is gone
(the panel gains a new opt-in `unify_combine=True`, used only by
`tab_batch.py`; Pump Probe's own loader instance keeps stride untouched —
see DECISIONS for why the flag exists at all). Combine sub-frames now applies
to a TIFF/`.ge*` folder too, via new `workers._ChunkCombinedFileSource`
(groups consecutive FILES, mirroring `_HDF5StackGlobSource`'s within-file
chunking). Start/end filtering moved from a post-hoc index range into
`source_cfg()` itself (`frame_start`/`frame_end`, new
`workers._filter_paths_by_frame_number`), applied *before* chunking so a
narrowed range always restarts chunk-counting at its own start.
Opportunistic fixes in the same touched code: background "Run as background
job" (`batch_cli.py`) previously had no `--chunk-size`/`--combine-op` at all
(silently ignored); `tab_batch.py`'s `_run_as_job` mis-routed multi-file HDF5
through `--source-type tiff_list` (now has its own `hdf5_stack_glob` branch).
MONITOR now also refuses when combine/filtering is active (live-combining
isn't supported). New tests: `test_batch_data_source.py` (+6),
`test_frame_naming.py` (+9), `test_project.py` (+1) — all existing HDF5
multi-file combine tests needed zero changes.
**Verified:** 13 touched/related test files green per-file on a clean `HOME`
(one pre-existing unrelated SIGABRT); `pyflakes` unchanged at 37; offscreen
screenshot confirmed the new layout. Full detail in DECISIONS.md.

**2026-09-25 — Data Viewer: folder format filter, under-viewer frame
scrubber, profile-file lineout; app-wide frame-nav slider/button styling.**
- Image folder loads can be filtered to one detected format via a new
  "Format:" combo (`helpers._folder_format_groups`, opt-in
  `DataLoaderPanel(folder_format_filter=True)`, Data Viewer only).
- Frame scrubber moved from the left loader column to directly under the
  image viewer (`tab_view._build_frame_scrub_bar`, copies `tab_calibrate.py`
  /`CakeStackViewer`'s pattern); `hide_frame_field` now also hides
  `DataLoaderPanel`'s `mode="stack"` nav row (previously "single"-only).
- Radial Profile tab gained "Source: Detector frame / Profile file…" to load
  an existing `.csv/.xye/.dat/.fxye` integration output directly
  (`helpers.load_profile_file`, generalized off the PDF tab's reader).
  `helpers.native_axis_to_r_px` converts a 2θ/Q-native file to r_px once a
  calibration is attached; without one it plots in its native unit with the
  R/2θ/Q toggle locked (new `ProfileViewer.set_profile(...,
  native_unit=...)`). Image viewer stays blank in this mode.
- Found + fixed along the way: `DetectorGeometryCard._simulate()` (ring-radius
  computation) hard-required an image it never actually used the pixels of —
  new `simulate_rings_without_image()` (shares `_compute_material_rings()`
  with `_simulate`) lets ring overlays work with no image loaded.
- Separately requested: every frame-nav ◀/▶ button + slider app-wide (Data
  Viewer, Calibrate, Mask Builder, Hydra `mode="nav"` loader,
  `CakeStackViewer`, `DataLoaderPanel`'s own stack-mode row) now carries
  `objectName` `frameNavBtn`/`frameNavSlider`, styled in `style.py` — several
  were plain `QToolButton`s with no default border/background, nearly
  invisible on the dark theme.
- New `tests/test_dataviewer_format_filter.py` (7),
  `test_dataviewer_frame_scrub.py` (5), `test_dataviewer_profile_file.py`
  (12) — **not** fork-isolated, same reason as `test_view_tab_controls.py`
  (a forked `DataViewerTab`-building test SIGSEGVs on this machine).
**Verified:** 11 touched/related test files green per-file on a clean
`HOME`; `pyflakes midas_gui/*.py` 38→37 (only change: `Path` in
`tab_view.py` went from unused to used); offscreen screenshot confirmed the
new button/slider colors. Full detail in DECISIONS.md.

**2026-09-24 (`d224c97`) — Mask Builder: folder/multi-frame Image loading +
threshold-mask projection.** The Image field now accepts a folder of
single-frame files, a multi-page TIFF, a 3-D HDF5 dataset, or a multi-frame
`.geN` file (new `_detect_multiframe`, peeks shape/page-count/file-size
only, never loads pixel data), with a Frame navigator (◀/▶ + spinbox) to
step through them — same pattern as the existing section-2 Stack folder
loader. New Projection combo (Current frame / Average / Sum / Max) lets the
threshold step (section 1, `_compute`/`_threshold_source_image`) build its
mask from a reduction across every frame instead of just the frame shown,
since a per-frame threshold on one noisy frame is often unreliable; disabled
and locked to "Current frame" for a plain single image. Frame index
persists in both mask-attempt provenance and sidecar state. New
`tests/test_mask_folder_frames.py` (10 tests, forked per the pyqtgraph
teardown-crash pattern).

**2026-09-11 (later) — One honest ring overlay, Batch's cakes made visible,
and the whole calibration in the provenance record.** Two commits.
- **`54cdd48` Calibrate.** The predicted-ring overlay is *always* the full
  forward model — fitted tilt **and** refined distortion — and the "Corrected"
  tick is gone from both the single-detector tab and the Hydra calib page
  (with its saved state). It was off by default and applied tilt only, so on a
  distortion-refined detector both of its states drew rings off the measured
  ones, which reads as a bad calibration rather than a bad overlay. New
  `helpers.ring_xy_corrected` inverts the backend's own
  `R_corrected = D(ρ,η)·R` by fixed-point iteration, calling `midas_distortion`
  rather than copying the model; `helpers.distortion_rho_d_um` reproduces
  `spec_from_calibration_result`'s normalisation radius exactly. Reduces
  bit-exactly to `tilted_ring_xy` (and so to a circle) with nothing to apply.
  The status line now names what was applied and says when the empirical
  `residual_corr_map` is *not* drawn. Also: the seed overlay carries fed-back
  distortion (fresh fit **and** project restore — it silently drew tilt-only
  rings before); **"Use seed as calibration (no fit)" removed** (superseded by
  the Data Viewer's Ring simulation card + `Geometry: [← Get]`); d-spacing pick
  controls hidden for crystalline calibrants, and leaving a d-spacing calibrant
  cancels an active pick mode; Refine card compacted to three rows with Limits
  as its own block, Seed/Advanced three-per-row, `S.Form.row` stretching every
  field column; `IntegrationWorker` float64 end-to-end (it narrowed to float32
  and widened back, so the Calibrate preview differed from the Batch run it
  previews). New `tests/test_ring_projection.py` (11, every drawn point fed
  back through `pixel_to_REta`) and `tests/test_calibrate_integration_accuracy.py`
  (4, pinning the Calibrate profile equal to the Data Viewer's accurate path).
- **`f44314d` Batch Integrate.** A multi-azimuth run's per-frame `(η, R)` cakes
  were computed, written to disk and embedded in the project — and shown
  nowhere; reopening such a project *raised* (2-D cake rows fed to the 1-D
  waterfall buffer). New `CakeStackViewer` + an "Eta-R cakes" view tab, frame
  scrubber, zoom preserved across steps; the 1-D views get an η-collapse over
  filled bins; a non-multi-azimuth run clears the tab rather than showing stale
  cakes. `CakeViewer`'s x-axis can be labelled R / 2θ / d / Q — tick strings
  only, no resampling, d → "∞" at R = 0 — and the selector stays hidden until a
  caller supplies geometry, which is what leaves Calibrate, Hydra and
  `RingResidualViewer` untouched. Separately, an attempt's
  `calibration_snapshot` is now the whole calibration via new
  `helpers.full_calibration_snapshot` (a strict superset of the display
  fields, so every existing reader is unchanged). New
  `tests/test_batch_cake_stack.py` (21) + 2 in `test_project.py`.
**Verified:** the 5 touched/new test files pass per-file on a clean `HOME`
(11/4/21/23, and `test_project.py` 42 pass + the known
`test_apply_project_calibration_single_detector` SIGABRT); `pyflakes
midas_gui/*.py` 35 warnings before and after, line numbers only.

**2026-09-11 — MIDAS backends bumped to current PyPI latest; the tilt/im_trans
issue this repo filed came back fixed.** `midas-calibrate-v2` 0.13.0→**0.17.0**,
`midas-hkls` 0.10.0→0.11.0, `midas-stress` 0.13.0→0.14.0 (the rest of the set was
already latest; nothing else moves — numpy/torch/numba/zarr untouched). The
calibrate-v2 jump is upstream implementing
`.context/issue_draft_calibrate_v2_tilt_imtrans.md` nearly whole: `initial_tx/ty/tz`
on `calibrate()`, `CalibrationSpec.im_trans` reaching every pipeline via one shared
`io/transforms.apply_im_trans`, `im_trans` recorded on the result and carried into
`IntegrationSpec.TransOpt`, and `first_time_calibrate` gaining both. **ROADMAP P3-1
and P3-4 closed**; P3-2/P3-3 re-checked, still open.
- **`calib._prep_transformed()` deliberately kept.** 0.15.0 introduces a silent
  double-transform for callers that pre-transform, but the GUI is not exposed: its
  specs come from `build_v1_params`, which sets no `ImTransOpt`, so `spec.im_trans`
  is `()` and every pipeline's `if spec.im_trans:` guard is false. Verified, plus
  `apply_im_trans` proven bit-identical to `helpers._apply_im_trans` on all 8 opcode
  combos. Removing the workaround is optional cleanup and must be done whole (delete
  `_prep_transformed` **and** set `v1.extra["ImTransOpt"]` in the same change) — see
  DECISIONS 2026-09-11.
- **Behaviour change to know about:** `initial_tx/ty/tz`, which the one_shot branch
  has always passed speculatively and `_supported_kwargs` silently dropped, now take
  effect. Also inherited: RhoD µm unit fixes, `use_diplib` defaulting False,
  distortion phase bounds ±90→±180.
- **`requirements.txt` was two bumps stale** (the 2026-09-08 bump never reached it),
  so it and `pip install .` installed different backends. Regenerated in sync; all
  three pin files now cross-checked against the installed env.
- **`first_time` now gets the transform (same session).** That branch had never
  passed one — a first_time calibration on a flipped detector ran in the wrong
  frame, silently. It now forwards the codes and hands over the RAW frame (the
  backend flips image/dark/panel_mask and re-derives `n_pixels_y/z`, so it must not
  also pre-flip). Fixing it exposed a wider bug: `workers.CalibrationWorker` read
  `NZ, NY = image.shape` off the **raw** image, so on a non-square detector with a
  transpose every non-plain-one_shot mode recorded `NrPixelsY/Z` for a detector it
  never fitted — now `calib.effective_pixel_counts()`. New
  `tests/test_first_time_im_trans.py` (19 tests, mutation-checked).
  **Still open:** first_time is not passed a *tilt* seed, which 0.17.0 now accepts
  (`initial_tx` + `tilt_prior_deg`→ty/tz); `test_calib_tilt_seed.py`'s "at any
  backend version" wording is stale.
- **Verified:** 43-file per-file sweep byte-identical before/after (same one
  pre-existing `test_smoke` local-config failure, 10/10 under a clean `HOME`); all 46
  modules import. One sweep run had `test_live_stream.py` exit 139 — the documented
  pyqtgraph teardown flake, not this work (5/5 green on re-run; that file imports
  neither changed module).

**2026-09-10 — Data Viewer: accurate integration on demand,
rings that stay put, and a two-way geometry hand-off.** Six requested changes:
- **"Accurate" tick** above the radial profile (off by default) switches it
  from the fast path (circle binning, or the engine's `hard` kernel once a
  calibration/tilt exists — the live-view-capable default, unchanged) to the
  **Batch-Integrate pipeline verbatim**: geometry synthesized from the live
  widgets even at zero tilt, `subpixel2` kernel.
- **Eta-vs-R Cake** gained its own `R bin` / `η bin` spinboxes next to
  `Calculate` (defaults 1.00 px / 5.00°, i.e. what it computed before) and now
  *always* runs the accurate pipeline. Because the two bin independently,
  `radial_integrate` no longer fills the cake as a by-product once
  `set_cake_controls` is bound (Hydra binds none, keeps the old behaviour).
  The single engine-context slot became a bounded 6-entry cache keyed on
  kernel + both bin sizes.
- **Simulate rings** is a plain button + `live` tick + `✕` again. A one-shot
  click leaves the button orange and **freezes** the overlay at the parameters
  it was simulated with (`_ring_draw_geom`) — fixes rings drifting on a BC/tilt
  edit while not live. Green only when live is armed; `✕` clears the simulated
  rings and disarms (leaves the click-picked magenta ring).
- **"Pick d-spacing pts" + "Ring #"** are hidden unless an *enabled* material
  is `kind == "dspacing"` (AgBH, custom lists), via new
  `PickableImageViewer.set_dspacing_picking_visible()`. Visible by default, so
  the Calibrate tab is untouched.
- **Transforms card** moved between Projection and Ring simulation (inside the
  card, so Hydra's panel cards match).
- **`Geometry: [Send →] [← Get]`** replaces the single Send button; `← Get`
  pulls via new `CalibrationTab.geometry_for_viewer()` (shared with Calibrate's
  own "→ Send to Data Viewer") and says so plainly when there is no result.
**Files:** `hydra_geometry_card.py`, `tab_view.py`, `widgets.py`,
`tab_calibrate.py`, `app.py`; new `tests/test_view_tab_controls.py` (31 tests).
**Verified:** 21-file per-file sweep green, zero new pyflakes warnings vs.
HEAD, offscreen screenshots of all three toolbars + the card column.
**2026-09-09 (later) — Parameter limits for crystalline calibrants; One-shot
refine flags made real.** The "limits are not available for this calibrant"
label shipped in the entry below was **wrong**: `CalibrationParams` carries
`tolLsd`/`tolBC`/`tolTilts`/`tolDistortion`/`tolWavelength` and
`param_vector.bounds()` makes them hard LM box constraints, so crystalline fits
were already bounded at invisible defaults (±15 mm / ±20 px / ±3°). The Refine
card now shows those windows, always-on and prefilled from the installed
backend, merged to the backend's coarser granularity (one BC window, one tilt
window, no tx). Plain One-shot is rerouted through
`build_v1_params` + `pipelines.single.autocalibrate` whenever `calibrate()`
cannot express what was asked — a custom window, a held Lsd/BC, or exactly one
of ty/tz — which also makes those checkboxes genuinely control the fit for the
first time. Seed arrow steps now follow the window (10 % of the full range).
**Files:** `calib.py` (`tol_defaults`/`tols_are_default`/`_resolve_seed`,
`build_v1_params(tols=)`, reroute), `tab_calibrate.py` (`_sync_limits_mode`,
`_crystalline_tols`, `_sync_seed_steps`), `dialogs.py` (distortion row; dead
`ParameterLimitsDialog` deleted), tests in `test_calibrate_panel_save.py` and
`test_manual_dspacing_calib_ui.py`. Docs: DECISIONS + `gui_documentation.md` §5.

_(Older entries — `eebce45` Batch Integrate run/restore crash from stale
views under a new axis context (reset stack/waterfall/cake views before
re-deriving axis context), 2026-09-09 manual d-spacing (AgBH/SAXS) fit trustworthiness
(BC-only default refinement, per-parameter 1σ, Limits… dialog), 2026-09-04
Jun-Sang Park's PR #7 (Strain Cake tab, job queue, peak-fit panel,
provenance, batch CLI; +108 tests), `fd7f67a` Workstation provenance + Hydra Overall-Cake
per-panel `tx` rotation fix + Batch Integrate Rmin/Rmax + Detector-view
preview, `18c9b77` crash-safe project saves (staging-group swap + rolling
`.bak`) + Save-As analysis-history choice + Open-Project unsaved-changes
guard, `d84c58e` Batch Multi-azimuth cake output + Export for
GSAS-II + MIDAS backend bump + `pytest-forked` isolation, `5954a57` Mask Builder raw-detector-space fix (removed
double-transform bug), `0332683` Batch-Parallel live-view frame-ordering fix,
`21faaf8` Project schema redesign (`gui_workspace` + `analysis`) + unified
Open Project dialog, `c67ad1b` multi-panel calibration refinement fix +
persistence, `e6f2e50` Output-format/Run-mode popups, `a27790a` Batch
Integrate cosmetic overhaul + Batch-Parallel workers, `ae3b665` merged
Workspace+Project into one `.h5`, `af8066f` Batch Browse… parity,
`a54f796`/`ac13797` Browse… popup (multi-file/folder/name-stem + polish),
`101558a` Calibrate Multi-panel→downstream-integration feed, `ccce056`
Flip-Z/Multi-panel fix — trimmed here; full detail in
`documentation/development_history.md`.)_

## Open questions / blockers

- **Windows user (`lheald`) calibration failure, unresolved.** Two
  different tracebacks seen so far, both breaking on a bare
  `from midas_calibrate_v2[.x] import y` statement (once in the plain
  single-detector branch, once via `_build_panel_layout` →
  `midas_calibrate_v2.forward.panels.PanelLayout`) — never inside real
  calibration math. Suggests either an outdated `midas_calibrate_v2`
  install (predating the 2026-08-25 upgrade) or a Windows DLL/native-ext
  load failure in that package. Asked the user to run `import
  midas_calibrate_v2` / `from midas_calibrate_v2.forward.panels import
  PanelLayout` / `pip show midas_calibrate_v2` directly in their env to get
  the untruncated traceback + version — response not yet received.
- **New follow-ups (tracked in ROADMAP.md "Package-side fixes" P3-2/P3-3
  and the Texture per-tab item):** (1) ~~several `midas_calibrate_v2`
  pipelines have no native `im_trans` param~~ — fixed upstream in 0.15.0,
  see 2026-09-11 above;
  (2) `*BinGeometry.from_spec()` has no `apply_trans_opt` hook for masks —
  GUI must keep pre-flipping masks in Python; (3) Texture tab's
  `PoleFigureWorker` has a pre-existing, unrelated mask/ImTransOpt bug;
  (4) `spec_from_calibration_result` has no panel-layout support — GUI
  already works around it (see P3-3).
- **Known-failing baseline (2026-09-10, per-file runs): none.** Every test
  file is green on a clean config. The one failure you will see on this
  machine, `test_smoke` 1 (`test_app_builds_offscreen`), is a local-config
  artifact, not code: `constants._apply` replaces `MATERIALS`/`CALIBRANTS`
  wholesale from the saved config, so a stale block changes what the
  Calibrate combo offers. `HOME=$(mktemp -d) pytest tests/test_smoke.py`
  gives 10/10. **Re-check any suspicious failure that way before calling it
  a regression**, and still capture a per-file baseline before reviewing an
  incoming change (this is how PR #7's and PR #8's real regressions were
  isolated; see the github-skill project memory for the review recipe).
- **The 29 forked SIGSEGVs are fixed (2026-09-10).** They were never the
  pyqtgraph teardown crash — the forked children died before the test bodies
  ran. Cause: pytest imports test modules during collection in the *parent*,
  and importing PyQt5 there (directly, or transitively via any `midas_gui`
  GUI module) initialises macOS CoreFoundation, which a forked child may not
  use. Proved causal by adding one `from PyQt5 import QtWidgets` line to
  `test_set_raw_frame.py`: 12 passed → 12 failed, restored on removal.
  `test_hydra_ui` (8), `test_manual_dspacing_calib_ui` (17),
  `test_hydra_batch_ui` (2) and `test_hydra_calib_ui` (2) now defer every
  Qt-pulling import into a `_load_qt()` called from their `app` fixture,
  which publishes the names (and the `QtCore.QObject` fake workers, which
  cannot be defined at module scope for the same reason) into module
  globals. All 29 pass. **Rule for new Qt test files: import PyQt5 and
  `midas_gui` GUI modules inside a fixture, never at module level** —
  `tests/test_set_raw_frame.py` is the reference.
- **Pre-existing interpreter-teardown crash risk**, especially around
  `CakeViewer`'s ViewBox (`tests/test_hydra_calib_ui.py`,
  `tests/test_hydra_ui.py`) and any module-scoped-fixture MainWindow
  (`tests/test_workspace_ux.py`, `test_smoke.py` run as a whole file).
  Trust per-file isolated runs, not a combined `tests/` run; do not reach
  for `gc.collect()` (confirmed to make it worse). Out of scope, see
  DECISIONS.md for the 2026-08-26 bisection. **2026-08-30: `pytest-forked`
  now isolates this for the trusted per-file workflow** — `test_hydra_
  calib_ui.py`, `test_hydra_ui.py`, `test_smoke.py`, `test_project.py` (and,
  from 2026-09-03, `test_set_raw_frame.py`, which builds one `ImageViewer`)
  all carry `pytestmark = pytest.mark.forked`, so a crash inside one of them
  run alone is a clean `FAILED ... CRASHED with signal N` instead of an
  interpreter abort. Does NOT fix a combined `tests/` run — see DECISIONS.md
  2026-08-30: `os.fork()` itself becomes unsafe once torch/numba/Qt/HDF5
  have spun up background threads earlier in the session, so forked tests
  late in a combined run can crash regardless of their own content (even
  `test_helpers.py`, pure logic). Keep trusting per-file runs only.
  **Widened 2026-08-29:** also
  seen with a `Fatal Python error: Aborted` in a leaked `workers.py`
  `build_geom`-running `QThread` at pytest teardown, reproduced even
  running `tests/test_project.py` (pure-logic, no Qt) alone; confirmed
  present on HEAD *before* the `c67ad1b` panel-refinement commit too — not
  introduced by it. **Widened again 2026-08-29 (schema-redesign session):**
  `tests/test_smoke.py` run alone is *non-deterministic* even on
  unmodified HEAD — 3 consecutive runs gave 10/10 pass, then a Bus error at
  4 dots, then a Segfault at 4 dots (each MainWindow-constructing test adds
  more pyqtgraph widgets to the same process; teardown corruption seems to
  accumulate randomly rather than at a fixed test). Don't trust a single
  green/red `test_smoke.py` run as signal either way — rerun a few times
  before concluding a change broke or fixed it.
- `test_smoke.py::test_app_builds_offscreen` has a pre-existing, unrelated
  local-config flake (stale `visible_tabs` count) — hit again this session,
  confirmed unrelated to the truncation fix.

## Standing rules (from memory)

- Commit history is the record of recent work — see `git log`. Docs
  (`development_history.md`, `gui_documentation.md`) are updated only when
  explicitly asked, not automatically per commit.
