# STATE — current snapshot

_Keep this under ~1 page. Permanent history lives in DECISIONS.md, not here._
_Last updated: 2026-09-11 (ring overlay through the full forward model;
Batch "Eta-R cakes" tab; full calibration in the provenance record)_

## Now working on

Nothing in progress.

Open follow-ups, none blocking:
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

- Still untested (ROADMAP.md): `job_queue.py`, `peak_fit_panel.py`,
  `batch_cli.py`.
- Branch cleanup done 2026-09-10: `pr-7-strain-cake`, `test-fork-imports` and
  the four fetched `refs/remotes/origin/pr/*` refs are gone; only `main` and
  `origin/main` remain. Re-fetch any PR head with
  `git fetch origin 'refs/pull/*/head:refs/remotes/origin/pr/*'`.

## Recently completed

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

**2026-09-09 — Manual d-spacing (AgBH/SAXS) fit made trustworthy.** Reported
as *"calibration runs away and the AgBH rings are significantly off"* on a
13.5 m SAXS geometry; root cause was identifiability, not a solver bug (at
small 2θ, Lsd/BC/tilt are near-degenerate). d-spacing calibrants default to
**BC-only** refinement, the fit reports per-parameter **1σ** +
`at_limit`/`clamped`/`method`, and a **Limits…** dialog bounds a parameter to a
window around its seed (`lm`→`trf`; unbounded stays bit-identical). Same
session: lab-frame compass, ring labels on the visible arc, mismatched
correction fields skipped-and-flagged instead of fatal, Batch persisting its
Tab-2 calibration result. (**"Use seed as calibration (no fit)"** also landed
here and was removed again 2026-09-11 — see above.) Full detail in DECISIONS.md.

**2026-09-04 (`549e96f`) + 2026-09-02/03 (`092fbba`, `46e0fec`) — Jun-Sang Park's
(`junspark`) PR #7 reviewed, fixed, covered by tests and merged in two halves**
(36 commits, ~+5500/−580 over ~30 files; PR closed). Strain Cake tab, job queue,
peak-fit panel, provenance, per-frame zarr cake, batch CLI, Batch Integrate
output-folder/Exp-ID/preflight work, HDF5 stack fixes. Shipped with zero test
changes; +108 tests added. Two real regressions caught only by holding a
per-file baseline first — silent frame loss from zero-padding normalisation
(`scan_1`/`scan_01`/`scan_001` collapsing onto one output file) and a stale
import git could not see. Full detail in DECISIONS.md and
`documentation/development_history.md`.

_(Older entries — `fd7f67a` Workstation provenance + Hydra Overall-Cake
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
