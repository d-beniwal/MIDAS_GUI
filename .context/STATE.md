# STATE — current snapshot

_Keep this under ~1 page. Permanent history lives in DECISIONS.md, not here._
_Last updated: 2026-08-28 (Output format/Run mode popups + calib-popup
rebuild fix — committed as `e6f2e50`; see "Recently completed" below)_

## Now working on

Nothing in flight — `e6f2e50` (Output format/Run mode popups, calib-popup
rebuild fix) is committed and pushed; awaiting next task.

## Recently completed

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

**2026-08-28 (`a27790a`) — Batch Integrate: cosmetic overhaul +
Batch-Parallel workers, single-detector + Hydra.** Five requested changes,
applied to both the single-detector tab and Hydra's per-panel page: (1)
Drift correction hidden from the GUI (single-detector only, not used in
production — `DriftWorker`/state untouched, `setVisible(False)`); (2) the
always-visible "Calibration values" grid replaced by a "View calibration"
popup (`helpers.make_calib_values_button`, same click-to-see-options
pattern as `make_pixel_label`/`make_kedge_label`) — single card + each of
Hydra's 4 panel cards; (3) output format is now a checkbox list
(`widgets.OutputFormatSelector`) — every checked format is written;
`BatchWorker`/`FolderMonitorWorker` gained `fmts: list[str]` replacing
`fmt: str`; (4) a green **Save** button (`workers.write_all_profiles`)
writes already-computed lineouts on demand regardless of whether an Output
folder was set before running; Start Integration narrower, Abort red; (5) a
new **Sequential**/**Batch Parallel** run-mode + worker-count control
(`workers.BatchRunCoordinator`, same signal surface as `BatchWorker`)
splits one run's frames across N `BatchWorker` QThreads (in-process, not
OS processes — numpy/torch release the GIL) sharing one detector map built
once (`_GeomBuildWorker`), auto-shrinking workers so each gets ≥10 frames
(`resolve_worker_count`); needed a new `BatchWorker.frame_indices` param
for exact random-access reads per chunk (the old frame_range skip-loop
would otherwise decode every frame up to a late chunk's start). Hydra gets
this as a *second*, independent parallelism level under its existing
per-panel Sequential/Parallel toggle. `BatchWorker.finished` also now
carries `"sigmas"` (previously computed but never emitted — a pre-existing
gap in `project.append_integration_attempt`'s expected payload, now closed).
**Verified:** `tests/test_batch_data_source.py` extended to 17 (frame-index
resolution, chunk-splitting, worker-count auto-shrink, `write_all_profiles`,
`_ExplicitTIFFSource.get`); `tests/test_hydra_batch_ui.py` updated for the
`BatchWorker`→`BatchRunCoordinator` swap + removed `_calib_val_note`;
`tests/test_project.py`'s `integrate_attempt_gui_fields` test updated for
`fmt` becoming a list. All pass (per-file isolated — the pre-existing
pyqtgraph/thread teardown crash, see "Open questions" below, still fires
after all tests pass, unrelated). Full single-detector + Hydra tabs build
and round-trip state correctly in an offscreen smoke check.
`gui_documentation.md` §7 rewritten + PDF rebuilt.

_(Older entries — `ae3b665` merged Workspace+Project into one `.h5`,
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
- **Pre-existing pyqtgraph interpreter-teardown crash risk**, especially
  around `CakeViewer`'s ViewBox (`tests/test_hydra_calib_ui.py`,
  `tests/test_hydra_ui.py`) and any module-scoped-fixture MainWindow
  (`tests/test_workspace_ux.py`, `test_smoke.py` run as a whole file).
  Trust per-file isolated runs, not a combined `tests/` run; do not reach
  for `gc.collect()` (confirmed to make it worse). Out of scope, see
  DECISIONS.md for the 2026-08-26 bisection.
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
