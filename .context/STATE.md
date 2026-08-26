# STATE — current snapshot

_Keep this under ~1 page. Permanent history lives in DECISIONS.md, not here._
_Last updated: 2026-08-26 (committing the full uncommitted diff — Project
records now embed calibration results + skip embedding file-backed masks,
Hydra Overall Cake gained a full UI, Data Viewer gained a Cake tab, and
Workspace/Project now persist the active beamline Profile — see "Recently
completed" below)_

## Now working on

Nothing in progress — the diff described below is being committed now.
`main` is otherwise 3 commits ahead of `origin/main` (`6b1564b`, `b4a6dfe`,
`77f5867`), about to be pushed along with the new commit(s).

## Recently completed

**Full 2026-08-26 diff (committed this session) — bundles several related
features found already on disk when this task started; see DECISIONS.md
for full technical detail on each:**

- **Project attempts are more self-sufficient (`project.py`,
  `tab_calibrate.py`, `hydra_calib_page.py`, `tab_batch.py`,
  `hydra_batch_page.py`, `widgets.py`).** A mask is now embedded in a
  Project attempt only when it includes something hand-drawn/computed in
  Mask Builder (`MaskSelector.has_live_mask_source`); a mask assembled
  purely from file/folder sources is referenced by path+hash instead, like
  the raw calibrant image already was — keeps project files smaller.
  Dark/bright/background are unaffected (always embedded). Separately, a
  calibration attempt now also embeds its computed Radial Profile / Eta vs
  R Cake arrays (`append_calibration_attempt(..., results=...)` /
  `read_calib_attempt_results`), so **Open Project…**'s Populate step shows
  them instantly with no recompute and no need for the original image to
  still be reachable (older attempts without a saved `results` group fall
  back to the previous recompute-if-image-loaded behavior).
- **Hydra Calibrate Overall Eta-R Cake, full UI (`hydra_calib_page.py`).**
  The **Radial Profile** plot's **Overall** control is now a
  green-when-active toggle button (was a checkbox) that hides GE1-4's
  curves and shows their NaN-aware summed profile (`_compose_overall_cake`
  verified correct 2026-08-26, see DECISIONS.md); clicking again restores
  GE1-4. The **Eta vs R Cake** tab gained its own GE1-4 checkboxes plus a
  matching **Overall** button that computes and displays the summed cake.
  Once a live Hydra run finishes with Overall active, the summed profile is
  also logged to the project as its own lightweight `hydra_composite`
  attempt (`dialogs.py` label "Hydra Overall"; `app.py`'s
  `discover_panels`/Populate flow explicitly skips this pseudo-panel since
  it has no tab widget to restore into — visible only via Project History).
- **Cake-plot independent-axis zoom (`widgets.py`).** Eta-R Cake plots'
  right-click-drag now zooms only the axis actually dragged (horizontal ->
  R, vertical -> η, diagonal -> both), matching Radial Profile's plots.
  Root cause: `pg.ImageView.__init__` force-locks `aspectLocked=True` on
  any view it's given, and an aspect-locked ViewBox couples X/Y on every
  right-drag no matter what a custom `mouseDragEvent` computes — the fix is
  `vb.setAspectLocked(False)` (R-px and η-degrees have no physical aspect
  to preserve). Removed the now-obsolete `_YZoomViewBox` class. See
  DECISIONS.md for an important test-suite trade-off this fix carries.
- **Data Viewer gains an Eta vs R Cake tab (`tab_view.py`,
  `hydra_geometry_card.py`).** Sits next to the existing Radial Profile tab
  in single-detector mode; re-renders whenever the tilt/distortion-aware
  MIDAS integration path runs (the fast geometry-free fallback has no cake
  to show).
- **Workspace/Project now record + restore the active beamline Profile
  (`app.py`, `project.py`).** A saved Workspace and a newly-created Project
  both record the Profile active at that time; on load/Open Project it's
  restored automatically (header combo synced, tab visibility and
  calibrant/device dropdowns refreshed) if it still exists locally and
  differs from the one currently active — silently skipped otherwise.
  `project.py`'s existing schema fields are all unchanged; every addition
  above is a new, optional dataset/attribute, so older project files still
  read exactly as before.

`documentation/gui_documentation.md` already documents all of the above in
full (§5, §15-§17); this note exists because STATE.md's previous revision
only covered the Overall-Cake-logic-verification and zoom-fix items and
missed the rest of the diff — a repeat of the "Now working on omits
already-implemented features" gap from 2026-08-24 (see project github
memory). Functionally verified via synthetic-data tests and the existing
test suite (see "Open questions" below for known pre-existing test
flakiness); not run against a live GUI session by a human this session.

**Workspace/Project UX rework (`6b1564b` + docs `b4a6dfe`, branch
`workspace_ux`, committed 2026-08-26, see DECISIONS.md for full detail).**
Renamed "GUI State" → **Workspace**; added a global recent-files store
backing File ▸ Recent Projects/Workspaces; window title `[*]` dirty-marker +
close-time Save/Discard/Cancel prompt; autosave-to-draft + restore-on-
relaunch; New Project can optionally save+link a Workspace; new read-only
`ProjectHistoryDialog` browsing a project's recorded attempts.
`project.py`'s HDF5 schema/functions were untouched.

**ImTransOpt propagation fix (`2358ae4` + docs `003d466`, 2026-08-25)** and
**MIDAS backend package upgrade (`a74b7d6`, 2026-08-25)** — see DECISIONS.md
for full detail; summarized in "Recent changes" below.

See DECISIONS.md / `development_history.md` for earlier sessions' work
(lab-frame axes overlay, plot bounding, Open Project auto-plots, auto-detect
geometry) — summarized in "Recent changes" below.

## Open questions / blockers

- `main` is 3 commits ahead of `origin/main` (`6b1564b`, `b4a6dfe`,
  `77f5867`) — not yet pushed; ask before pushing.
- **New follow-ups (not blocking, tracked in ROADMAP.md "Package-side
  fixes" P3-1/P3-2 and the Texture per-tab item):** (1) several
  `midas_calibrate_v2` calibration pipelines have no native `im_trans`
  parameter — GUI already works around it correctly, upstream addition
  would just simplify `calib.py`; (2) `*BinGeometry.from_spec()` has no
  `apply_trans_opt` hook for masks — GUI must keep pre-flipping masks in
  Python indefinitely unless that's added upstream; (3) Texture tab's
  `PoleFigureWorker` has a pre-existing, unrelated mask/ImTransOpt bug,
  found but not fixed this session (out of scope).
- **Pre-existing pyqtgraph interpreter-teardown crash risk is unusually
  sensitive around `CakeViewer`'s ViewBox (found 2026-08-26, see DECISIONS.md
  for the bisection).** `tests/test_hydra_calib_ui.py` and `tests/test_hydra_ui.py`
  now segfault on teardown far more often after the Cake-plot zoom fix above
  — but the *identical* flake was reproduced on unmodified `main` too (just
  at much lower frequency for `test_hydra_calib_ui.py`, and `test_hydra_ui.py`
  already segfaults every time even on unmodified `main`). Bisection showed
  even a behaviorally-inert edit to `CakeViewer`'s ViewBox class shifts the
  crash probability — this looks like a heisenbug sensitive to memory/GC
  timing near that object, not a logic bug in the zoom fix (which was
  verified correct via direct ViewBox-level tests, bypassing the full page).
  Not attempted to fix further — same pre-existing, out-of-scope class of
  issue as below, just more frequent in these two files now. Trust the live
  GUI over these two files' pytest exit codes until this is investigated.
- Pre-existing pyqtgraph interpreter-teardown crash risk, general — a
  module-scoped-fixture MainWindow (e.g. `tests/test_workspace_ux.py`)
  triggers known teardown tracebacks at interpreter exit; `test_smoke.py`
  run as a whole file segfaults on unmodified `main` too. Trust per-file
  isolated runs, not a combined `tests/` run. Do not reach for `gc.collect()`
  (confirmed to make it worse — see DECISIONS.md). Out of scope, pre-existing.
- `test_smoke.py::test_app_builds_offscreen` has a pre-existing, unrelated
  local-config flake (stale `visible_tabs` count).

## Recent changes (last 3-5 sessions, dated; drop the oldest as it grows)

- 2026-08-26 (`943a91d`): Project attempts embed calibration results +
  skip embedding file-backed masks, Hydra Overall Cake full UI, Data Viewer
  Cake tab, Workspace/Project active-Profile persistence, Cake-plot
  independent-axis zoom fix — see "Recently completed" above.
- 2026-08-26 (`6b1564b` + docs `b4a6dfe`): Workspace/Project UX rework —
  merged into `main` via fast-forward (`77f5867`); `workspace_ux` branch
  deleted.
- 2026-08-25 (`2358ae4`): ImTransOpt propagation fix — the MIDAS backend
  now performs every pixel flip for calibration/integration; GUI only
  pre-flips masks and viewer-display previews.
- 2026-08-25 (`a74b7d6`): Upgraded all 8 MIDAS backend packages to latest
  PyPI releases; added `midas-params`, pinned `zarr`/`numcodecs`.

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
