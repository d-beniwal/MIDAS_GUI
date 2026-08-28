# STATE — current snapshot

_Keep this under ~1 page. Permanent history lives in DECISIONS.md, not here._
_Last updated: 2026-08-27 (Browse… popup polish — folder-path display,
project-root default dir, wider name column — committed `a54f796`/pending
docs commit; see "Recently completed" below)_

## Now working on

Nothing in progress; branch about to be pushed to `origin/main`.

## Recently completed

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

**2026-08-27 (`101558a`) — Feed Calibrate's Multi-panel results to
downstream integration.** Three GUI-side gaps, all upstream of any package bug (panel
numbering already matches between `PanelLayout.regular` and the v1
`DetectorMapper` convention): (1) `helpers._build_spec` (used by Calibrate's
own Results-tab preview *and* Batch Integrate's "Use Tab 2 calibration")
never patched panel fields onto the `IntegrationSpec` — same pre-existing
gap `TransOpt` already had and was already patched for; (2)
`geometry_fields_from_file` (the "Load calibration file" reader used across
Batch/Export/PDF/Corrections/Hydra) never parsed panel keys at all; (3)
`_save_paramstest`'s existing `panel_shifts.txt`/`PanelShiftsFile` export
never wrote the panel *grid* (`NPanelsY`/`NPanelsZ`/`PanelSizeY`/
`PanelSizeZ`/`PanelGapsY`/`PanelGapsZ`), so even a correct shifts file
linked to `NPanelsY=0` downstream. Root cause underneath all three:
`calib.py` never recorded the panel grid config anywhere on the result
object. Fixed by attaching two new plain, JSON-safe attributes
(`result.panel_layout`, `result.panel_shifts_path`) the moment a
Multi-panel run finishes (`calib._attach_panel_result`, called from
`normalize_result`'s four_stage/bayesian/joint branches), a shared
`helpers._apply_panel_fields()` spec-patcher used everywhere a spec is
built from a result, and the missing grid keys added to
`_save_paramstest`'s output. Verified via a synthetic panel_u/panel_layout
round-trip (write → spec-patch → save paramstest → reparse → rebuild spec)
since the real four-stage pipeline needs a physically convergent ring image
to exercise end-to-end. Full detail + file list in DECISIONS.md; ROADMAP
gained P3-3 for the one real upstream gap this surfaced
(`spec_from_calibration_result` has no panel support, same shape as the
`TransOpt` gap). Only the single-detector Calibrate tab is affected —
Hydra mode's 4 independent-detector "panels" are a different concept,
untouched.

**2026-08-27 (`ccce056`) — Fix: Calibrate ignored Flip Z when "Multi-panel
detector" was checked.** `calib.py`'s manual `im_trans` pre-flip workaround (needed
because `autocalibrate_four_stage`/`_bayesian`/`_joint`/
`pipelines.single.autocalibrate` have no native `im_trans` param — see
ROADMAP P3-1) computed the auto-seed from the raw/untransformed image but
ran the solve on the manually-flipped image, in all four affected branches
of `run_pipeline()` — seed and solve ran in two different frames whenever a
transform was active, so local gradient-based refinement converged near the
seed's original (wrong) position. Also `dark` was passed untransformed to
the solver even when `image` was flipped. Added `_prep_transformed()` to
apply `im_trans` to image+dark together once, before seeding; all four
branches now use its output for both seed and solve. Verified with a
synthetic ring image (real test image too sparse for the auto-seeder).
Full root-cause + verification detail in DECISIONS.md; ROADMAP P3-1 updated
(corrects a previous wrong claim that the workaround was already
bug-free). Scope: `first_time` pipeline branch (ignores im_trans entirely,
separate pre-existing gap) explicitly left unfixed, per user agreement.

**2026-08-27 (`08fe8f6`/`fe59939`, pushed):** Added a README Troubleshooting
section (pip `--no-cache-dir` reinstall fix for corrupted-cache DLL/
submodule import errors, e.g. torch `fbgemm.dll`, `mpmath.libmp`) and
gitignored the local-only `test_data/projects/` dataset (66 MB of
self-contained-project `.h5`/workspace JSON, same pattern as `s17bm/`
etc.).

**2026-08-27 (`5cf2e8c`) — error dialogs/logs no longer truncate the
underlying exception, app-wide.** Triggered by a Windows user's
"Calibration failed" dialog showing only a stray `F` —
`QMessageBox.critical(..., msg[:400])` in `tab_calibrate.py`'s `_on_fail`
happened to cut the traceback mid-word. The same `msg[:N]`/
`traceback.format_exc()[:N]` pattern (N in 200-600) was copy-pasted across
~15 files' worker-failure handlers. Added `dialogs.show_error(parent,
title, full_text, log=None, log_prefix="")`: shows a one-line summary with
Qt's native scrollable "Show Details…" panel holding the complete text, and
optionally appends the same full text to a tab's log widget/callback.
Replaced every truncated dialog call with it across `tab_calibrate.py`,
`tab_refine.py`, `tab_pdf.py`, `tab_corrections.py`, `tab_batch.py`,
`tab_pumpprobe.py`, `tab_texture.py`, `tab_view.py`, `tab_export.py`,
`tab_mask.py`, `widgets.py`, `hydra_calib_widgets.py`,
`hydra_geometry_card.py`; dropped the slice on log-only sites in
`hydra_batch_page.py`/`hydra_calib_page.py` that had no paired dialog.
`str(e)`-only dialogs (never truncated) were left alone, as was one
unrelated single-line status-label truncation in `widgets.py`
(`FieldAverageWidget`, no dialog/log involved — different UI affordance).
`documentation/gui_documentation.md` §13 updated. The Windows user's actual
underlying exception (an import from `midas_calibrate_v2`/
`midas_calibrate_v2.forward.panels` failing) is still unconfirmed — this
fix only unblocks *seeing* the full error next time it happens; likely
causes flagged to the user: version mismatch with the 2026-08-25 backend
upgrade (`a74b7d6`), or a Windows DLL/native-extension load failure.

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
