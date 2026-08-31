# STATE — current snapshot

_Keep this under ~1 page. Permanent history lives in DECISIONS.md, not here._
_Last updated: 2026-08-31 (about to commit Project save-safety +
Save-As-history-scope work; see "Recently completed" below)_

## Now working on

Nothing in progress — about to commit the crash-safe-saves/Save-As-history
work below. Working tree otherwise clean apart from local-only
`test_data/test_out/` (GSAS-II export test artifacts; untracked,
gitignore-precedent says leave it, see the github-skill project memory).

## Recently completed

**2026-08-31 (uncommitted) — Project saves made crash-safe; Save-As lets
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

**2026-08-31 (`d84c58e`) — Batch Integrate Multi-azimuth cake output +
Export for GSAS-II; MIDAS backend bump; pytest-forked test isolation.**
Four independent pieces bundled into one commit (this repo's "bundle the
full diff" convention — see DECISIONS.md 2026-08-29/2026-08-30 entries for
the *why* behind each): (1) Batch Integrate's opt-in **"Multi-azimuth
output (cake)"** checkbox (off by default) keeps every azimuthal (η)
sector as a separate output profile (`profiles`/`sigmas` →
`(n_frames, n_eta, n_r)`) instead of collapsing to one full-circle profile
per frame, reusing the existing η bin/range fields; `write_frame_profiles()`
is the new shared per-format writer for both the live-run and Save-button
paths. Not yet combinable with Q-uniform bins; HDF5 output is skipped in
this mode. (2) New **Export for GSAS-II** feature (`midas_gui/
gsas_export.py` + a card in Results & Export, `tab_export.py`) writes one
chosen Batch-Integrate attempt as a native MIDAS-format GSAS-II zarr via
`midas_integrate_v2.io.zarr_gsas.write_gsas_zarr_zip` + a provenance
sidecar; v1 scope is single-detector/R-uniform-binning/embedded-mask only,
each unsupported case raising a named `ValueError`. (3) MIDAS backend
package bump — `midas-integrate-v2` 0.7.0, `midas-calibrate-v2` 0.11.0,
`midas-integrate` 0.7.0, `midas-calibrate` 0.5.0, `midas-pdf` 0.2.0 — for
`PolygonBinGeometry.from_spec()`'s tilt/distortion/parallax/panel-shift
fix. (4) `pytest-forked` added + `pytestmark = pytest.mark.forked` on the
four known interpreter-teardown-crash-prone test files (see the crash-risk
bullet below). **Files:** `tab_batch.py`, `tab_export.py`, `workers.py`,
`app.py`, new `gsas_export.py`, `requirements.txt`/`environment.yml`/
`pyproject.toml`, the four test files + new `tests/test_batch_multiazimuth.py`/
`tests/test_gsas_export.py`. **Verified:** both new test files pass
(`pytest tests/test_batch_multiazimuth.py tests/test_gsas_export.py`, 8/8);
(1)/(2) were actually implemented and GUI-tested in an earlier, uncaptured
2026-08-29 session whose STATE.md update never landed — caught only by
diffing the full working tree against `gui_documentation.md` (already
current) before this commit, per the github-skill project memory's
recurring "pre-commit sanity check" pattern. (3)/(4) were verified
independently the same way in the 2026-08-30 session that made them (see
DECISIONS.md).

**2026-08-29 (`5954a57`) — Mask Builder: build masks in raw detector-space,
fix double-transform bug.** Mask Builder's own Flip Y/Flip Z/Transpose
checkboxes (added in `068bd0d`) let a mask be built already transformed,
which then got transformed *again* by Calibrate/Batch Integrate/Refinement
downstream — silently misaligning the mask against the geometry whenever
Mask Builder's checkboxes were checked to match a flipped calibration.
Removed those checkboxes entirely: Mask Builder now always loads/previews
raw and every mask it produces is raw detector-space, matching a file/
folder mask loaded from disk. `MaskComputeWorker` drops its `im_trans`
param; `azimuthal_sigma_clip` and the learnable-mask trainer each
transform to/from world space only around their own call (their internal
conventions differ), mapping results back to raw before combining. Data
Viewer's composite-mask overlay (always raw) is now transformed with that
tab's own `im_trans` codes before compositing onto its transformed
preview. Bundled in: the `ProjectContentsPicker` Analysis-section layout
refinement (Single detector/Hydra headings, GUI Workspace tabs indented
under "Select all") that had been sitting uncommitted in the working tree
from an earlier, uncaptured session — `gui_documentation.md` already
described it as done, so it was folded into this commit rather than left
stranded. **Files:** `tab_mask.py`, `tab_view.py`, `workers.py`,
`dialogs.py`. **Not verified against a running GUI or test suite this
session** — reviewed by diff/syntax-check only; no automated test covers
the azimuthal/learnable transform-direction logic (pre-existing gap).

_(Older entries — `0332683` Batch-Parallel live-view frame-ordering fix,
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
- **Pre-existing interpreter-teardown crash risk**, especially around
  `CakeViewer`'s ViewBox (`tests/test_hydra_calib_ui.py`,
  `tests/test_hydra_ui.py`) and any module-scoped-fixture MainWindow
  (`tests/test_workspace_ux.py`, `test_smoke.py` run as a whole file).
  Trust per-file isolated runs, not a combined `tests/` run; do not reach
  for `gc.collect()` (confirmed to make it worse). Out of scope, see
  DECISIONS.md for the 2026-08-26 bisection. **2026-08-30: `pytest-forked`
  now isolates this for the trusted per-file workflow** — `test_hydra_
  calib_ui.py`, `test_hydra_ui.py`, `test_smoke.py`, `test_project.py` all
  carry `pytestmark = pytest.mark.forked`, so a crash inside one of them
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

- After **every commit**: append to `documentation/development_history.md`
  (hash/date/subject/Effect/Files/Roll back) + rebuild the `.pdf`.
- On any user-visible GUI/tab/workflow change: update
  `documentation/gui_documentation.md` + bump its "Last updated".
- PDF rebuild pipeline: `pandoc <file>.md -s -o /tmp/<file>.html
  --css=<inline stylesheet>`, then
  `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  --headless --disable-gpu --no-pdf-header-footer
  --print-to-pdf=documentation/<file>.pdf file:///tmp/<file>.html`
  (no committed script/template — recreated ad hoc each session).
