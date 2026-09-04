# STATE — current snapshot

_Keep this under ~1 page. Permanent history lives in DECISIONS.md, not here._
_Last updated: 2026-09-04 (all 36 commits of **Jun-Sang Park's** PR #7 merged
into `main` as `549e96f` and pushed; PR #7 closed as merged and commented on)_

## Now working on

Nothing in progress. **PR #7 is fully merged and closed; `main` is pushed**
(`549e96f`, `origin/main` in sync). A comment on PR #7 records the merge, the
four things fixed on the way in, the output-filename convention change, and a
request for tests on the next contribution.

Cleanup left: local branch `pr-7-strain-cake` and the fetched
`refs/remotes/origin/pr/*` refs can be deleted. Working tree clean apart from
local-only `test_data/test_out/` (untracked, leave it).

Still open from the merge: `job_queue.py`, `peak_fit_panel.py` and
`batch_cli.py` remain untested (ROADMAP.md). One new pyflakes warning, unused
`HDF5FrameSource` import in `workers.py`. Pre-existing and unrelated:
`app.py:992` annotates `Optional` without importing it — harmless only because
of `from __future__ import annotations`.

## Recently completed

**2026-09-04 (`549e96f`) — PR #7's remaining 16 commits merged, `main`
pushed, PR closed.** Second half of Jun-Sang Park's PR (+1982/−285 over 23
files): Batch Integrate output-folder auto-suggest + Exp ID + writability
preflight, recursive stem-match search, single-file HDF5 honouring Combine
sub-frames, dark-file skipping / frame-range autofill / HDF5-stack hang fix,
Detector-view eta-spoke + tilted-ring-seam fixes, viewer display settings
persisted across all tabs, File-menu Open Last Project/Quit, worker-exception
logging, job-queue cwd pinning, `QUESTIONS_FOR_COLLEAGUES.md`.
- **Resolved on merge:** `workers.py` (kept their per-format `csv/`/`h5/`/
  `zarr/` subfolders, but re-applied our collision-safe stem allocation, which
  their branch had reverted by branching before `092fbba`; `frame_output_base`
  split into `frame_output_stem` + path wrapper so a frame allocates its stem
  once, not once per format); taught the parser their new
  `.frame_<start>_<end>` chunk suffix; `.context/DECISIONS.md` interleaved.
- **Silent break git couldn't see:** their `zarr_cake.py` retirement (correct —
  the old schema wrote `/IntegrationResult/FrameNr_<i>`, which GSAS-II never
  reads) auto-merged clean but left `tests/test_zarr_cake.py` importing a
  deleted module. Removed it, rewired `test_batch_zarr_output.py` onto the new
  one-zarr-per-frame layout, rebuilt the `test_provenance` fixture.
- **Checked, not a regression:** `tilted_ring_xy`'s `endpoint=True` change
  shifted the η sampling grid and tripped 2 `test_helpers` tests; zero-tilt
  reduction to a plain circle still holds to 1.7e-13. Expectations updated,
  closure property newly pinned.
- **Verified:** per-file suite identical to the pre-merge baseline — same 5
  files failing with the same counts, no new failures; all 42 modules import.

**2026-09-02/03 (`092fbba`, `46e0fec`) — Jun-Sang Park's (`junspark`) PR #7
("Add Strain Cake tab with azimuth strain map and lab-frame axes"), first 20
commits, reviewed, fixed, covered by tests, and fast-forward-merged into
local `main`.** (PR still open upstream; see "Now working on".) 20 commits, +3492/−295 over 19 files, 6 new modules
(`job_queue.py`, `peak_fit_panel.py`, `provenance.py`, `zarr_cake.py`,
`cake_params.py`, `batch_cli.py`), shipped with **zero test changes**.
Reviewed against a per-file baseline of `main` first (essential — this repo
has 4 permanently-failing files, see the crash blocker below); everything
matched baseline except two files, both tripped by one intentional but
undeclared behaviour change.
- **Found + fixed: silent frame loss.** The PR moved per-frame profile
  output to a `<froot>_<NNNNNN><tag>` convention (matching
  mpe_wf_saxs_waxs) via a new `workers.froot_and_frame_num`, which
  normalises zero-padding — so `scan_1`/`scan_01`/`scan_001` all became
  `scan_000001.csv`: 3 frames in, 1 file out. New
  `workers.frame_output_base()` now owns naming and de-duplicates per run;
  all three call sites (`BatchWorker`, `FolderMonitorWorker`,
  `write_all_profiles`) route through it. Also taught the parser the
  `_c<NN>` chunk ids the PR's own `_HDF5StackGlobSource` mints.
- **+108 tests** across 7 new files (`test_frame_naming`,
  `test_provenance`, `test_zarr_cake`, `test_batch_zarr_output`,
  `test_strain_cake`, `test_calib_tilt_seed`, `test_set_raw_frame`) plus 15
  added to `test_helpers.py`; the two stale tests updated (the writer was
  right, only their expectations were wrong).
- **Verified:** full per-file suite on merged `main` is identical to the
  pre-PR baseline — same 4 pre-existing failures, no new ones. No new
  third-party deps (matplotlib/zarr/numcodecs already pinned); all 43
  modules import; one new pyflakes warning only (unused local `spec`,
  `tab_batch.py:972`). **Still untested** (no coverage added, out of
  scope): `job_queue.py`, `peak_fit_panel.py`, `batch_cli.py` — tracked in
  ROADMAP.md.

**2026-08-31 (`fd7f67a`) — Workstation provenance + Hydra Overall-Cake
rotation fix + Batch Integrate Rmin/Rmax + Detector-view preview.** Three
features bundled into one commit:
- **Workstation provenance** — `project.workstation_snapshot()` (hostname/
  OS/CPU/cores/RAM) folded into `environment_snapshot()`, so every Mask/
  Calibrate/Batch-Integrate attempt now records the machine it ran on.
- **Hydra Overall Eta-R Cake now rotates each panel by its own `tx`**
  before summing (`hydra_calib_page.py`: `_resample_rows_to_eta_grid`,
  `_compose_overall_cake` updated) — fixes panels piling on top of each
  other instead of covering -180°..180°.
- **Batch Integrate: Rmin/Rmax exclusion + Detector-view preview**
  (single-detector `tab_batch.py` + Hydra `hydra_batch_page.py`/
  `hydra_batch_widgets.py`) — new Rmin/Rmax spinboxes (Rmin defaults 0,
  Rmax 0="auto"→backend's own farthest-corner default, with Corner/Edge
  preset buttons: `helpers.rmax_corner_px`/`rmax_edge_px`) and a new
  "Detector view" tab showing the current frame with the Rmin/Rmax circles
  + an optional (R, η) bin-grid overlay (`helpers.draw_polar_bin_overlay`,
  thinned to ≤50 rings/≤72 spokes via `_thinned_bin_edges`). **Hydra
  Detector-view is ONE shared `ImageViewer`** (not one per panel) to avoid
  the pyqtgraph-teardown segfault; `tests/test_hydra_batch_ui.py` now
  carries `pytestmark = pytest.mark.forked`.
**Files:** `project.py`, `hydra_calib_page.py`, `helpers.py`,
`tab_batch.py`, `hydra_batch_page.py`, `hydra_batch_widgets.py`,
`tests/test_project.py` (+3 tests), new `tests/test_hydra_overall_cake.py`
(9 tests, pure-logic), `tests/test_helpers.py` (+7 tests),
`tests/test_hydra_batch_ui.py`. `gui_documentation.md` (top summary + §7 +
§16/§17) and `development_history.md`/`.pdf` (`e577e72`) already updated.

**2026-08-31 (`18c9b77`) — Project saves made crash-safe; Save-As lets
you choose how much analysis history to carry over; Open-Project guards
unsaved changes.** `project.py`: every mutating write
(`write_gui_workspace` and the three `append_*_attempt` functions) now
builds its new content in a sibling staging child group and swaps it into
place with a cheap metadata-only rename (`_stage_and_swap`) instead of
delete-then-rebuild-in-place, so a crash mid-write leaves prior content
fully intact; `write_gui_workspace` also makes a rolling `path + ".bak"`
copy (`backup_before_overwrite`) before each overwrite, and
`create_project` gained an `overwrite=True` option (also backs up first).
New `analysis_summary()`/`copy_analysis_history()` let **File ▸ Save
Project As…** (`app.py`, new `SaveAsHistoryDialog` in `dialogs.py`) always
create a genuinely fresh project at the destination (overwriting an
existing file there only after explicit confirmation, never merging into
it) and separately ask how much of the *currently open* project's
`/analysis` history to carry into it — full history (default),
latest-attempt-only (never leaving a dangling `calib_attempt_ref`), or
none. `app.py` also factored the Close-window unsaved-changes
Save/Discard/Cancel prompt into a shared `_confirm_ok_to_switch_project()`
and now runs it before **File ▸ Open Project…**/a Recent-Projects pick too,
so opening a different project can no longer silently discard in-progress
edits. **Files:** `project.py`, `dialogs.py`, `app.py`,
`tests/test_project.py` (+8 new tests), `tests/test_workspace_ux.py` (+8
new tests). `gui_documentation.md` §16 already updated. **Verified:**
`pytest tests/test_project.py` (all new tests pass; one unrelated
pre-existing test, `test_apply_project_calibration_single_detector`,
CRASHED with SIGABRT under `pytest-forked` — matches the long-documented
interpreter-teardown crash risk below, not introduced by this change) and
`pytest tests/test_workspace_ux.py` (25/25 pass, only teardown-noise
tracebacks after the dots, exit 0).

_(Older entries — `d84c58e` Batch Multi-azimuth cake output + Export for
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
- **New follow-ups (tracked in ROADMAP.md "Package-side fixes" P3-1/P3-2/P3-3
  and the Texture per-tab item):** (1) several `midas_calibrate_v2`
  pipelines have no native `im_trans` param — GUI already works around it;
  (2) `*BinGeometry.from_spec()` has no `apply_trans_opt` hook for masks —
  GUI must keep pre-flipping masks in Python; (3) Texture tab's
  `PoleFigureWorker` has a pre-existing, unrelated mask/ImTransOpt bug;
  (4) `spec_from_calibration_result` has no panel-layout support — GUI
  already works around it (see P3-3).
- **Known-failing baseline (2026-09-03, per-file runs).** Four files fail on
  a clean `main` for the reasons below, and have for a long time:
  `test_hydra_ui` 8 failed, `test_hydra_batch_ui` 2, `test_hydra_calib_ui` 2,
  `test_smoke` 1 (`test_app_builds_offscreen`). Every other file is green.
  **Capture this baseline before reviewing any incoming change** — without
  it you cannot tell a regression from the standing noise (this is how PR
  #7's two real regressions were isolated; see the github-skill project
  memory for the full review recipe).
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
