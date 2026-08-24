# STATE — current snapshot

_Keep this under ~1 page. Permanent history lives in DECISIONS.md, not here._
_Last updated: 2026-08-24 (Calibrate tab split into Single-detector/Hydra modes, + bundled Data Viewer Hydra refinements — committed `93dafa2` + docs `c23151f`; not yet pushed)_

## Now working on

**Calibrate tab (Tab 2) split into Single-detector / Hydra modes** (`93dafa2`),
mirroring the Data Viewer tab's existing split (`HydraModeRibbon` reused as-is).
New files: `hydra_calib_widgets.py` (`HydraCalibPanelCard` — one GE panel's
Transforms + seed + fitted result/rings/residuals/results-grid, all
genuinely independent per panel) and `hydra_calib_page.py`
(`HydraCalibrationPage` — shared Pipeline/Detector&Calibrant/Threshold/
Average-frames/Refine-parameters/Advanced cards applied to all 4 panels'
fits, a `HydraLoaderPanel` reused verbatim, a shared image viewer + ge1-4
toolbar (no Composite — calibration is inherently per-panel), and bottom
tabs: shared multi-curve Radial Profile, per-panel Ring Residuals/Results
that switch with the active panel, shared Log prefixed `[ge{n}]`). Supports
**Sequential** (one panel at a time, full log capture) or **Parallel** (all
present panels fit at once) runs, plus a **← Data Viewer** import of the
Hydra Data Viewer page's loaded panels + fitted geometry
(`HydraViewerPage.export_for_calibration`/`DataViewerTab.get_hydra_export`),
and a **→ Send to Data Viewer** push per panel
(`DataViewerTab.set_hydra_panel_geometry`). `CalibrationWorker` gained a
`capture_stdout: bool = True` flag so Parallel mode's several concurrent
workers don't race on the process-global `sys.stdout`/`sys.stderr` redirect
(Sequential keeps `True`). `helpers.source_kind`/`helpers.paramstest_pairs`
promoted out of per-tab private methods to be shared.

**Same commit also bundled Data Viewer Hydra-page refinements** that had
accumulated uncommitted earlier in this session (STATE.md had drifted out of
sync with the actual working tree — caught and reconciled before this
commit, see DECISIONS.md's pre-commit sanity-check note): Transforms (Flip
Y/Flip Z/Transpose[/Rotate]) extracted into its own boxed Transforms card
shared by every `DetectorGeometryCard`; a per-panel-only Rotate field on
Hydra ge1-4 (`hydra.apply_panel_rotation`, excluded from the Composite);
a per-panel Projection card (Max/Sum/Average stack reduction, new
`ProjectionWorker` usage in `HydraLoaderPanel`); a fix for Flip Y/Flip
Z/Transpose not refreshing the displayed Hydra image; the Hydra radial plot's
pan/zoom now bounded to the visible curves' data extent; and the Material
dialog's Preset dropdown also fills the Name field.

New test `tests/test_hydra_calib_ui.py` (2 test functions, each building
exactly ONE `HydraCalibrationPage` — building one per test function
reliably segfaulted the file via the known pyqtgraph-teardown crash, see
below): pick-state cross-panel isolation (same shape as `eadb14d`), and
Sequential/Parallel run orchestration + Results/Ring-Residuals panel
switching, both using a stubbed `CalibrationWorker`/`IntegrationWorker`
(the synthetic Hydra fixture's `ps_ge{1..4}.txt` files share one nominal
BC and don't carry enough rings for a real fit to converge — a pre-existing
fixture characteristic, not a bug in this page).

Full test suite run clean apart from the pre-existing
`test_app_builds_offscreen` "visible_tabs" flake and the known pyqtgraph
interpreter-teardown noise at process exit (both pre-existing, non-blocking
— see DECISIONS.md).

## Next steps

- Committed (`93dafa2` + docs `c23151f`) but **not yet pushed to `origin`**.
- Not yet exercised with a live windowed pass (Pick BC/Pick Ring mouse
  clicks, real calibration convergence on real Hydra data e.g.
  `test_data/s1ide`) — only offscreen/stubbed-worker tests so far. Given
  Phase 6's experience with the Data Viewer's Hydra page (5 real bugs found
  only by a real windowed pass), treat this page as similarly unverified
  until someone actually runs it.
- Per-panel masks are NOT wired into Hydra calibration (`cfg["mask"] = None`
  always) — an explicit scope cut, not yet raised with the user.

## Open questions / blockers

- None currently blocking.
- A rare, pre-existing pyqtgraph interpreter-crash risk under a large test
  suite (many `ImageView`/`ViewBox` instances) — see DECISIONS.md if
  `pytest` ever segfaults/bus-errors again; do not reach for `gc.collect()`,
  already confirmed to make it worse. This session's new page adds to the
  per-`MainWindow`-build widget count (see above) — if the full-suite crash
  rate becomes a real nuisance, DECISIONS.md's pyqtgraph entry suggests
  running Hydra UI tests in a separate pytest process as the next lever.
- **`.context/` is `.gitignore`d in this repo** (`.gitignore:58`), which
  contradicts the global CLAUDE.md instruction that `.context/` should
  always be committed. Not touched — flagged for the user to decide.

## Recent changes (last 3-5 sessions, dated; drop the oldest as it grows)

- 2026-08-24 (`93dafa2` + docs `c23151f`): Calibrate tab split into
  Single-detector/Hydra modes, + bundled Data Viewer Hydra refinements
  (Transforms card extraction, per-panel Rotate, per-panel Projection,
  bounded radial-plot pan/zoom, Preset-fills-Name) — see "Now working on".
- 2026-08-24 (`eadb14d` + docs `875d3ed`): Data Viewer Hydra Phase 6, 6th
  bug — Pick BC/Pick Ring point leakage across panels (shared viewer's pick
  state not cleared on panel switch).
- 2026-08-24 (`0aa5feb` + docs `8d5ed5a`): Data Viewer Hydra Phase 6 manual
  pass found and fixed 5 bugs — shared λ/max2θ/px, dark/bright/background
  correction, composite orientation (rotation direction + vertical mirror),
  stale radial-integration geometry, zero-excluded vmin% percentile.
- 2026-08-23 (`3b785c9`..`1d8c6e5`, 14 commits, on `main`): Data Viewer
  Hydra detector-view feature, phases 1-5, plus an unrelated
  stale-test-data-path fix and committing `.context/`.

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
