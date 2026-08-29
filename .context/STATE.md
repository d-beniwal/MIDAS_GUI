# STATE — current snapshot

_Keep this under ~1 page. Permanent history lives in DECISIONS.md, not here._
_Last updated: 2026-08-29 (Mask Builder: build masks in raw detector-space,
fix double-transform bug, committed as `5954a57`; see "Recently completed"
below)_

## Now working on

Nothing in flight — `5954a57` (Mask Builder raw-detector-space fix +
Open Project layout refinement) is committed and pushed; awaiting next task.

## Recently completed

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
  DECISIONS.md for the 2026-08-26 bisection. **Widened 2026-08-29:** also
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
