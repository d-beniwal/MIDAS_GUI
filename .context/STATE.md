# STATE — current snapshot

_Keep this under ~1 page. Permanent history lives in DECISIONS.md, not here._
_Last updated: 2026-08-28 (Batch Integrate cosmetic overhaul + Batch-Parallel
workers — committed as `a27790a`; see "Recently completed" below)_

## Now working on

Nothing in flight — `a27790a` (Batch cosmetic overhaul + Batch-Parallel) is
committed and pushed; awaiting next task.

## Recently completed

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

**2026-08-28 (`ae3b665`) — Merged Workspace + Project into one `.h5`
"Project" file.** User request: unify the two previously-independent
persistence mechanisms — a JSON "Workspace" (`Ctrl+S`/`Ctrl+Shift+S`/
`Ctrl+O`, every tab's live fields) and an HDF5 "Project" (append-only
Calibrate/Batch-Integrate provenance) — into one `.h5`. `project.py` gained
`write_workspace()`/`read_workspace()`: a single mutable `/workspace` slot
(JSON state + optional sidecars), overwritten each save, alongside the
existing append-only `attempt_NNNN` history (`SCHEMA_VERSION` bumped to 2,
old v1 files still open fine — `read_workspace` returns `({}, {})` when
there's no `/workspace` group). `app.py`'s File menu collapsed to Save
Project (`Ctrl+S`)/Save Project As…(`Ctrl+Shift+S`)/Open Project…(`Ctrl+O`)
— `New Project…` is gone (Save-As to a new filename creates one); `Close
Project` and `closeEvent` now prompt to save first if the session is
dirty, since Ctrl+S now targets the same file. `save_project`/
`_apply_workspace_state` replace `save_gui_state`/`load_gui_state`,
harvesting the Mask-Builder/Calibrate sidecar files (`get_state(sidecar_
stem=...)`, unchanged) through a scratch `tempfile.TemporaryDirectory()`
instead of leaving them next to a JSON file — **no changes needed in
`tab_mask.py`/`tab_calibrate.py`**. A `File ▸ Import Legacy Workspace
(.json)…` action reads old standalone Workspace JSON files for backward
compatibility. Per user's explicit decision, `append_calibration_attempt`/
`append_integration_attempt` dropped their `dark`/`bright`/`background`
embedding entirely (always file-backed already — path+hash in
`loader_state`/`inputs` already covers provenance); a live/drawn-in-tab
mask with no file of its own remains the one embedded exception (had no
`mask_is_file_backed` alternative). Fixed 4 call sites across
`tab_calibrate.py`, `tab_batch.py`, `hydra_calib_page.py`,
`hydra_batch_page.py` (plus removed now-dead `_last_fields`/`dark`/
`bright`/`background` plumbing in the two Hydra pages).
**Verified:** `tests/test_project.py` (20, incl. 2 new `write_workspace`/
`read_workspace` round-trip tests) and `tests/test_workspace_ux.py` (9,
updated for the new API) pass per-file (the known pyqtgraph teardown crash
— see "Open questions" — fires after all tests pass in both files,
unrelated). An offscreen end-to-end script confirmed: Ctrl+S with no
project open → Save-As creates a fresh `.h5`; a second save overwrites
`/workspace` in place leaving `attempt_NNNN` groups untouched; a logged
calibration attempt has no `dark`/`bright`/`background` datasets; a fresh
`MainWindow` opening that project restores an edited field exactly.
`gui_documentation.md` §16 rewritten (old §16/§17 merged into one section)
+ PDF rebuilt.

_(Older entries — `af8066f` Batch Browse… parity, `a54f796`/`ac13797`
Browse… popup (multi-file/folder/name-stem + polish), `101558a` Calibrate
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
