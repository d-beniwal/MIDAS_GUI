# STATE — current snapshot

_Keep this under ~1 page. Permanent history lives in DECISIONS.md, not here._
_Last updated: 2026-08-26 (Workspace/Project UX rework, `6b1564b` + docs
`b4a6dfe` — committed to branch `workspace_ux`, NOT merged into `main`)_

## Now working on

Nothing in progress on `main` (working tree clean there). A separate branch
**`workspace_ux`** (2 commits ahead: `6b1564b` feature, `b4a6dfe` docs) holds
a File-menu UX rework — see "Recently completed" below. Landed on its own
branch **by explicit user request**, so the user can review/diff it and
decide whether to keep (merge) or discard it; not touched on `main`.

## Recently completed

**Workspace/Project UX rework (`6b1564b` + docs `b4a6dfe`, branch
`workspace_ux`, see DECISIONS.md 2026-08-26 for full detail).** User asked
for a from-scratch rethink of Project create/save/load UX, benchmarked
against established desktop apps (VS Code/Photoshop/Blender), while keeping
FAIR provenance guarantees perfectly intact. Diagnosis: two unrelated
concepts ("GUI State" JSON snapshot vs. "Project" HDF5 provenance log) both
occupied the "Project" mental-model space with no recent-files list, dirty-
state indicator, autosave, or in-app way to browse a project's records.
Fix, entirely additive — **`project.py`'s HDF5 schema/functions are
untouched**: (1) "GUI State" renamed to **Workspace** everywhere (same
shortcuts/JSON format); (2) new global recent-files store
(`settings.py: record_recent/get_recent`) backing File ▸ Recent
Projects/Workspaces submenus; (3) window title `[*]` unsaved-marker driven
by a cheap periodic hash-diff of each tab's existing `get_state()` (no new
per-widget signals); `closeEvent` now prompts Save/Discard/Cancel when
dirty; (4) autosave-to-a-draft while dirty + restore-on-relaunch prompt,
wired only from `main()` (never `__init__`) so bare `MainWindow()` in tests
can't block on a stale draft; (5) New Project can optionally save+link a
Workspace in one step; (6) new read-only `ProjectHistoryDialog`
(`dialogs.py`, first `QTableWidget` in the app) browsing every recorded
attempt via `project.py`'s existing read API — no more needing
`h5dump`/HDFView to see what a project contains. New tests:
`tests/test_workspace_ux.py` (9 tests, all green) plus 3 new settings.py
tests. Verified via per-file-isolated pytest; confirmed the pyqtgraph
interpreter-teardown crash risk (already documented below) reproduces
identically on unmodified `main`, so it's pre-existing, not a regression.

**ImTransOpt propagation fix (`2358ae4` + docs `003d466`, 2026-08-25)** and
**MIDAS backend package upgrade (`a74b7d6`, 2026-08-25)** — see DECISIONS.md
for full detail; summarized in "Recent changes" below.

See DECISIONS.md / `development_history.md` for earlier sessions' work
(lab-frame axes overlay, plot bounding, Open Project auto-plots, auto-detect
geometry) — summarized in "Recent changes" below.

## Open questions / blockers

- **`workspace_ux` branch awaits user review** — not merged, not deleted.
  Next session should check whether the user has decided to keep/merge it
  or discard it before doing further work there.
- **New follow-ups (not blocking, tracked in ROADMAP.md "Package-side
  fixes" P3-1/P3-2 and the Texture per-tab item):** (1) several
  `midas_calibrate_v2` calibration pipelines have no native `im_trans`
  parameter — GUI already works around it correctly, upstream addition
  would just simplify `calib.py`; (2) `*BinGeometry.from_spec()` has no
  `apply_trans_opt` hook for masks — GUI must keep pre-flipping masks in
  Python indefinitely unless that's added upstream; (3) Texture tab's
  `PoleFigureWorker` has a pre-existing, unrelated mask/ImTransOpt bug,
  found but not fixed this session (out of scope).
- Pre-existing pyqtgraph interpreter-teardown crash risk — confirmed again
  this session (workspace_ux work): a module-scoped-fixture MainWindow in
  `tests/test_workspace_ux.py` triggers the same known teardown tracebacks
  at interpreter exit (harmless to the pytest exit code); `test_smoke.py`
  run as a whole file still segfaults on unmodified `main` too (re-verified
  via `git stash`). Verification going forward: trust per-file isolated
  runs, not a combined `tests/` run. Do not reach for `gc.collect()`
  (confirmed to make it worse — see DECISIONS.md). Not attempted to fix —
  out of scope, pre-existing.
- `test_smoke.py::test_app_builds_offscreen` has a pre-existing, unrelated
  local-config flake (stale `visible_tabs` count) — reconfirmed present on
  bare `main` this session too.

## Recent changes (last 3-5 sessions, dated; drop the oldest as it grows)

- 2026-08-26 (`6b1564b` + docs `b4a6dfe`, branch `workspace_ux`): Workspace/
  Project UX rework — see "Recently completed" above. Not merged to `main`.
- 2026-08-25 (`2358ae4`): ImTransOpt propagation fix — the MIDAS backend
  now performs every pixel flip for calibration/integration; GUI only
  pre-flips masks and viewer-display previews.
- 2026-08-25 (`a74b7d6`): Upgraded all 8 MIDAS backend packages to latest
  PyPI releases (API-clean, zero midas_gui code changes); added
  `midas-params`, pinned `zarr`/`numcodecs`; tested in a cloned conda env
  before cutting the live `midas-gui` env over.
- 2026-08-25 (`87d9df7` + docs `8f618a8`): Data Viewer gains a **Lab-frame
  axes** overlay toggle (APS/MIDAS coordinate compass), ported from
  midas-gui-swaxs with the Y-invert sign flipped for this repo's convention.

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
