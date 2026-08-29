# STATE — current snapshot

_Keep this under ~1 page. Permanent history lives in DECISIONS.md, not here._
_Last updated: 2026-08-29 (Multi-panel calibration actually refines panel
shifts + persists across save/reload — committed as `c67ad1b`; see
"Recently completed" below)_

## Now working on

Nothing in flight — `c67ad1b` (multi-panel calibration refinement fix +
persistence) is committed and pushed; awaiting next task.

## Recently completed

**2026-08-29 (`c67ad1b`) — Calibrate: multi-panel pipelines actually
refine panel shifts, persist across save/reload.** Root cause: every
Multi-panel-detector pipeline (one-shot, first-time, four-stage, bayesian,
joint) passed `panel_layout` only to the fixed forward geometry — no
per-panel δy/δz/δθ was ever registered as refinable, so a Fit run produced
no real correction regardless of export path. New `calib._panel_spec()`
builds a `CalibrationSpec` with panel parameters added
(`spec_from_v1_params()` + `add_panel_parameters()`, tolerances matching
`midas_calibrate_v2.calibrate()`'s own `panel_mode="shift"` defaults) and
every `run_pipeline()` branch now passes `spec=`; one-shot's
`normalize_result()` also gained the missing `_attach_panel_result()`
call. Persistence follow-through: (1) Save calibration.json/paramstest.txt
rewrite a co-located `<name>_panelshifts.txt` sidecar at save time instead
of trusting a possibly-ephemeral `panel_shifts_path` (`calib.
_attach_panel_result` now logs to the tab when it falls back to a
tempfile); (2) `helpers.geometry_fields_from_file` retries a sidecar next
to the geometry file when the recorded path is stale; (3) Project (`.h5`)
calibration attempts embed the raw panel-shifts array
(`project._panel_shifts_array`/`append_calibration_attempt`), and
reopening a project materializes a real sidecar next to the project file
(`materialize_panel_shifts`, wired into `app.py`'s attempt-population
path) instead of pointing at a long-gone tempfile. New tests:
`tests/test_panel_refinement.py`, `tests/test_panel_shifts.py`,
`tests/test_calibrate_panel_save.py`; `tests/test_project.py` gained two
cases for the embed/materialize round-trip. All pass per-file isolated.
`gui_documentation.md` §5/§16 updated + PDF rebuilt.

**2026-08-28 (`e6f2e50`) — Batch Integrate: Output format + Run mode as
popups, fix calib-popup rebuild.** Same-day follow-up to `a27790a`: (1)
`OutputFormatSelector`'s checkboxes moved from an always-visible list into
a `QMenu` behind a clickable "Output format ▾" button whose text names the
checked formats (same pattern as `make_calib_values_button`); (2) the
Run-mode explanatory note (single-detector "Mode:", Hydra "Per panel:")
became a hover tooltip on the label + combo instead of a permanent
`QLabel`; (3) fixed `make_calib_values_button`'s popup caching a stale
near-zero size on first open — `_populate()` now `menu.clear()`s and
rebuilds the whole widget tree (not just the grid contents) on every open,
since `QMenu` computes its popup size from the `QWidgetAction`'s sizeHint
at show time. No behavior change to `checked_keys()`/`get_state()`/
`set_state()` or the calib popup's field content — presentation only.
`gui_documentation.md` updated + PDF rebuilt.

_(Older entries — `a27790a` Batch Integrate cosmetic overhaul +
Batch-Parallel workers, `ae3b665` merged Workspace+Project into one `.h5`,
`af8066f` Batch Browse… parity, `a54f796`/`ac13797` Browse… popup
(multi-file/folder/name-stem + polish), `101558a` Calibrate
Multi-panel→downstream-integration feed, `ccce056` Flip-Z/Multi-panel fix,
`08fe8f6`/`fe59939` README+gitignore, `5cf2e8c` error-dialog truncation fix
— trimmed here; full detail in `documentation/development_history.md`.)_

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
  introduced by it, just re-discovered while checking that commit's tests.
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
