# STATE — current snapshot

_Keep this under ~1 page. Permanent history lives in DECISIONS.md, not here._
_Last updated: 2026-08-27 (error-dialog/log truncation fixed app-wide,
committed `5cf2e8c` — see "Recently completed" below)_

## Now working on

Docs update (development_history.md + PDF rebuild) for `5cf2e8c` in
progress this session; not yet pushed.

## Recently completed

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

**2026-08-26 (`943a91d`/`573e938`, pushed):** Project attempts embed
calibration results + skip embedding file-backed masks; Hydra Calibrate
gained a full Overall Eta-R Cake UI; Data Viewer gained a Cake tab;
Workspace/Project now persist the active beamline Profile; Cake-plot
independent-axis zoom fix. Full detail in DECISIONS.md.

**2026-08-26 (`6b1564b` + docs `b4a6dfe`):** Workspace/Project UX rework
(renamed "GUI State"→Workspace, recent-files menus, dirty-marker/autosave,
`ProjectHistoryDialog`) — merged to `main` via `77f5867`.

**2026-08-25:** ImTransOpt propagation fix (`2358ae4`) — MIDAS backend now
performs every pixel flip for calibration/integration, GUI only pre-flips
masks/previews. MIDAS backend package upgrade (`a74b7d6`) — all 8 backend
packages to latest PyPI.

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
- **New follow-ups (tracked in ROADMAP.md "Package-side fixes" P3-1/P3-2
  and the Texture per-tab item):** (1) several `midas_calibrate_v2`
  pipelines have no native `im_trans` param — GUI already works around it;
  (2) `*BinGeometry.from_spec()` has no `apply_trans_opt` hook for masks —
  GUI must keep pre-flipping masks in Python; (3) Texture tab's
  `PoleFigureWorker` has a pre-existing, unrelated mask/ImTransOpt bug.
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
