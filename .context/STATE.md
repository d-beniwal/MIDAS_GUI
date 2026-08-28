# STATE — current snapshot

_Keep this under ~1 page. Permanent history lives in DECISIONS.md, not here._
_Last updated: 2026-08-28 (Batch Integrate Browse… parity — committed as
`af8066f`; see "Recently completed" below)_

## Now working on

Nothing in flight — `af8066f` (Batch Browse… parity) is committed and
pushed; awaiting next task.

## Recently completed

**2026-08-28 (`af8066f`) — Batch Integrate: Browse… parity — Multiple
files + filestem-filtered sources.** Batch Integrate's streamed Data field
previously only offered Single file/Full folder in Browse… (Multiple
files/Filestem were withheld since the streaming reader could only consume
a glob path). Now offers all four modes: a **Filestem** pick in stream mode
is kept as a live `(folder, prefix)` filter (`DataLoaderPanel.
_set_stem_filter`) rather than resolved to a frozen list — `_raw_source()`
substitutes it into a `<folder>/<prefix>*` glob so `source_cfg()`/`_load()`/
cross-tab Import-from stay filestem-aware for free, and `FolderMonitorWorker`
re-globs it every poll (a new matching file dropped in later is picked up
by MONITOR, non-matching ones ignored). A **Multiple files** pick becomes a
new `"tiff_list"` source type; `workers._ExplicitTIFFSource` iterates the
resolved paths via `helpers._load_image` (covers `.ge*` too) — not
watchable by MONITOR (no glob describes an arbitrary list), guarded by the
existing `type != "tiff_glob"` check with a clearer warning message.
**Verified:** new `tests/test_batch_data_source.py` (8, dependency-free —
PyQt5/numpy/tifffile only) — stem-filter/explicit-list `source_cfg()`
shapes, manual-edit clearing, info-label text, get/set_state round-trip,
`_ExplicitTIFFSource` ordering, `BatchWorker._open_source` dispatch. All
pass. `gui_documentation.md` updated (§1 "The Browse… popup" + new
"Batch Integrate: filestem-filtered sources and MONITOR" under §7) + PDF
rebuilt.

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

**2026-08-27 (`a54f796`) — Browse… popup polish: folder-path display,
project-root default dir, wider name column.** User feedback on the just-
shipped Browse… popup (`ac13797`): (1) a Multiple-files/Filestem pick
showed a bare `"N files selected"` count instead of a real path — new
`helpers.display_text_for_paths()` (single file's own path if exactly one
matched, else the shared parent folder via `os.path.commonpath`) is now
used by `FieldSelector`/`DataLoaderPanel._set_explicit_paths` **and**
Mask Builder's pre-existing `_browse_stack_files` "Files (multi-select)…"
picker (`tab_mask.py` — same `"N files selected"` pattern, predates this
session, unrelated to `ac13797`'s new dialog); (2) `BrowseFilesDialog`
opened to `Path.home()` — now defaults to the new `constants.PROJECT_ROOT`
(`Path(__file__).resolve().parent.parent`, matching the existing
`_TEST_DATA` repo-root convention) unless a caller passes `start_dir`; (3)
the tree view's name column (Qt's stock 100px default) is now doubled to
200px so longer filenames aren't immediately elided. `gui_documentation.md`
updated (§1 "The Browse… popup" + the Mask Builder Stack-field paragraph).
Verified: per-file isolated `test_smoke.py` tests + `test_live_stream.py`
green; a standalone offscreen script confirmed `PROJECT_ROOT` resolves to
the repo root, the popup's initial `_current_dir` matches it, and
`columnWidth(0)` goes from 100→200.

**2026-08-27 (`ac13797`) — Data Loader: Browse… popup (multi-file/folder/
name-stem) + Hydra gains real cross-tab Import from….** Every Data/Dark/
Bright/Background field's ⋯ button (single-detector and Hydra alike) now
opens **Browse…** + **Import from…** instead of the old flat File…/
Folder… pair. New `dialogs.BrowseFilesDialog` offers up to 4 modes per
field (Single file / Multiple files / Full folder / Files sharing a name
stem — HDF5 excluded from every mode but Single file). Hydra's Data
Viewer/Calibrate/Batch Integrate pages now bind into the same
`data_bridge.DataSourceRegistry` as their single-detector counterparts
(new `bind_hydra_registry()` per tab, wired from `app.py`), so Hydra
fields get a real "Import from…" for the first time — labeled distinctly
("Data Viewer (Hydra)" etc.) so a Hydra path is never confused with the
single-detector one. An explicit Multiple-files/stem pick is a `list[str]`
with no string/glob form; `helpers.source_kind`/`_collect_frame_paths` and
`FieldSelector`/`DataLoaderPanel` state save/restore now handle it
alongside plain path text. `gui_documentation.md` updated ("The Browse…
popup", "Cross-tab data import"). Verified: per-file isolated
`tests/test_smoke.py`, `test_live_stream.py`, `test_hydra_geometry.py`
pass; `test_hydra_ui.py`/`test_project.py` hit the pre-existing
interpreter-teardown segfault/abort at process exit (see "Open questions"
below) — unrelated, all tests pass before it.

_(Older entries — `101558a` Calibrate Multi-panel→downstream-integration
feed, `ccce056` Flip-Z/Multi-panel fix, `08fe8f6`/`fe59939` README+gitignore,
`5cf2e8c` error-dialog truncation fix — trimmed here; full detail in
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
