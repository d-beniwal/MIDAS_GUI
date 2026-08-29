# STATE — current snapshot

_Keep this under ~1 page. Permanent history lives in DECISIONS.md, not here._
_Last updated: 2026-08-29 (Project `.h5` schema redesign — `gui_workspace`
(per-tab) + `analysis` (mask/calibrate/integrate) — and a unified Open
Project dialog, committed as `21faaf8`/`197a266`; see "Recently completed"
below)_

## Now working on

Nothing in flight — `21faaf8` (Project schema redesign + unified Open
Project dialog) is committed and pushed; awaiting next task.

## Recently completed

**2026-08-29 (`21faaf8`) — Project: redesign `.h5` schema into
`gui_workspace` (per-tab) + `analysis` (mask/calibrate/integrate); unified
Open Project dialog.** Clean-cutover breaking change, `schema_version` 2→3,
**no backward compatibility** — an old project file now shows a clear
warning naming its schema version instead of silently restoring nothing.
(1) `/workspace` (one JSON blob for all 10 tabs) → `/gui_workspace/<tab
name>/{state, sidecars/}`, modular per tab (`project.write_gui_workspace`/
`list_workspace_tabs`/`read_workspace_tab`/`read_workspace_meta`); (2)
`/<panel_key>/{calib,integrate}/attempt_NNNN` → `/analysis/{calibrate,
integrate}/<panel_key>/attempt_NNNN` (no signature changes — every
read-side function is already keyed by an opaque `ref` string); (3) new
`/analysis/mask` — a *global* (not per-panel) FAIR-provenance history for
Mask Builder (`append_mask_attempt`/`list_mask_attempts`/
`read_mask_attempt_array`), with a new "Log to Project" button + explicit
user-triggered logging (confirmed with the user — no single "run finished"
moment exists to auto-log from, unlike Calibrate/Batch-Integrate) and
`apply_project_mask` (restores fields, reloads the image, sets the mask
directly from the recorded array without re-dilating). (4) The old
two-stage Open Project flow (blind Yes/No workspace restore, then a
separate `ProjectLoadDialog`) is replaced by one `ProjectOpenDialog`
(file-tree browser + a live checkbox-tree preview via new
`ProjectContentsPicker`, refreshing as a `.h5` is clicked, everything
checked by default) / `ProjectSelectionDialog` (picker only, for Recent
Projects); `app.py`'s open-project methods collapse into one
`_open_project_selection`. `ProjectHistoryDialog` also lists mask
attempts. **Verified:** rewrote `test_project.py`'s workspace tests +
every hardcoded old-schema path assertion (also caught stale ones in
`test_hydra_batch_ui.py`/`test_hydra_calib_ui.py` via a full per-file test
sweep); new tests for mask-attempt read/write, `apply_project_mask`, and
the new picker/dialogs. All pass per-file isolated.
`gui_documentation.md` §4/§16 rewritten + PDF rebuilt.

_(Older entries — `c67ad1b` multi-panel calibration refinement fix +
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
