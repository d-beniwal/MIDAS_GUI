# STATE — current snapshot

_Keep this under ~1 page. Permanent history lives in DECISIONS.md, not here._
_Last updated: 2026-08-24 (Batch Integrate tab split into Single-detector/Hydra modes — committed as `6b961d4` + docs `0382137`)_

## Now working on

Nothing in progress — awaiting next task.

**Batch Integrate tab (Tab 3) split into Single-detector / Hydra modes**
(committed as `6b961d4`, docs `0382137`), mirroring the Calibrate tab's split: each of the 4 GE panels
is integrated with its own independently fitted geometry. New files:
`hydra_batch_widgets.py` (`HydraBatchPanelCard` — one panel's Calibration
source [From Calibrate tab / From file] + read-only values grid + compact
progress bar, mirrors `BatchTab`'s own calibration-source card) and
`hydra_batch_page.py` (`HydraBatchPage` — shared Integration/Corrections/
Monitor-normalisation/Output cards applied to all 4 panels' runs, a
`HydraLoaderPanel(mode="stream")`, a `QStackedWidget` of 4 per-panel
Waterfall+Stacked-profile viewer pairs switched by a `HydraDetectorToolbar`
panel toggle, shared Log prefixed `[ge{n}]`). Supports **Sequential**/
**Parallel** run modes (`BatchWorker` per panel, dict-of-workers
orchestration — direct copy of `HydraCalibrationPage`'s pattern). Each
panel writes to its own `<out_dir>/ge{n}/` subfolder.

Three confirmed design decisions (user sign-off): (1) **automatic hand-off**
— `HydraCalibrationPage.panelCalibrationDone(n, result)` → `CalibrationTab
.hydraPanelCalibrationDone` → `BatchTab.set_hydra_panel_calibration`, wired
in `app.py`, auto-populates a panel's calibration source the moment that
panel's Hydra fit finishes (mirrors the existing single-detector
`calibrationDone`→`set_calibration`); (2) **masks ARE wired per panel** for
Hydra Integrate (`HydraLoaderPanel` gained `mode="nav"|"stream"`; stream
mode adds one independent `MaskSelector` per panel, no sibling
auto-discovery) — a deliberate departure from Hydra Calibrate's
`cfg["mask"]=None` scope-cut; (3) **Drift correction and live MONITOR mode
are deferred** for Hydra v1 (both exist in single-detector Batch).

`HydraBatchPage` is built **lazily** on `BatchTab` — only on first switch
to Hydra mode, or when `set_hydra_panel_calibration`/a saved session with
Hydra page state needs it — rather than eagerly in `BatchTab.__init__`
(unlike `HydraCalibrationPage`/`HydraViewerPage`, which build eagerly).
Reason: it owns 8 pyqtgraph widgets (4 `WaterfallViewer` + 4
`StackedProfileViewer`), and most sessions never touch Hydra Batch
Integrate — see the pyqtgraph test note below.

`helpers.py` gained two small shared functions, `resolve_calibration_fields`
and `render_calib_value_grid`, hoisted out of `BatchTab._calib_fields_in_use`
/`_refresh_calib_values` so `HydraBatchPanelCard` doesn't duplicate that
~50-line block; `BatchTab` itself now calls the same two helpers.

New test `tests/test_hydra_batch_ui.py` (2 test functions, each building
exactly ONE `HydraBatchPage` — same "few pages, few tests" pyqtgraph
-teardown mitigation as `test_hydra_calib_ui.py`): calibration hand-off
(auto `set_panel_calibration` + manual "From file"), per-panel mask
independence, output subfolder-per-panel naming, and Sequential/Parallel
orchestration + per-panel viewer-stack switching — all against a stubbed
`BatchWorker` (spec-building itself is also stubbed at the
`hydra_batch_widgets._build_spec`/`spec_from_geometry_file` boundary, same
rationale as Calib's stubbed `CalibrationWorker`: not exercising real
geometry-fit numerics).

**pyqtgraph test-isolation finding, and `release.sh` fix (this session):**
running `test_hydra_batch_ui.py` together with the rest of the suite in one
`pytest` process pushed the pre-existing, documented pyqtgraph interpreter
-teardown segfault (see DECISIONS.md) from a ~40% baseline rate to ~100% —
confirmed by A/B testing against the unmodified baseline (5 runs each).
Running each Hydra UI test file (`test_hydra_calib_ui.py`,
`test_hydra_batch_ui.py`) in **its own fresh `pytest` process** eliminates
the segfault for those files entirely (0/4 in isolation) and leaves the
main suite at its pre-existing baseline rate. `release.sh`'s test step was
changed to invoke pytest 3 times (main suite minus the two Hydra UI files,
then each Hydra UI file separately) rather than once — this is the "next
lever" DECISIONS.md's pyqtgraph entry already anticipated needing "if the
full-suite crash rate becomes a real nuisance." A plain `pytest tests/`
(e.g. run by hand) still carries the pre-existing risk, now with one more
contributing file.

## Next steps

- Not yet exercised with a live windowed pass (real Hydra data, real
  per-panel `BatchWorker` runs) — only offscreen/stubbed-worker tests so
  far, same caveat as the Hydra Calibrate page before it.
- No "→ Send to Batch Integrate" push button was added to
  `HydraCalibPanelCard` (only the pull-style automatic hand-off) — not
  raised with the user; could add for symmetry with "→ Send to Data
  Viewer" if wanted later.

## Open questions / blockers

- None currently blocking.
- The pre-existing pyqtgraph interpreter-teardown crash risk (see above)
  is now measurably more frequent for a plain, single-process `pytest
  tests/` run (two Hydra-UI-heavy files instead of one). `release.sh` is
  fixed; a developer running bare `pytest tests/` will still see it more
  often than before. Do not reach for `gc.collect()` — already confirmed
  to make it worse (see DECISIONS.md).
- **`.context/` is `.gitignore`d in this repo** (`.gitignore:58`), which
  contradicts the global CLAUDE.md instruction that `.context/` should
  always be committed. Not touched — flagged for the user to decide.

## Recent changes (last 3-5 sessions, dated; drop the oldest as it grows)

- 2026-08-24 (`6b961d4` + docs `0382137`): Batch Integrate tab split into
  Single-detector/Hydra modes, per-panel masks, automatic Calibrate→Batch
  hand-off, lazy Hydra-page construction, `release.sh` test-isolation fix
  — see "Now working on".
- 2026-08-24 (`93dafa2` + docs `c23151f`): Calibrate tab split into
  Single-detector/Hydra modes, + bundled Data Viewer Hydra refinements
  (Transforms card extraction, per-panel Rotate, per-panel Projection,
  bounded radial-plot pan/zoom, Preset-fills-Name).
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
