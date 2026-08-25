# STATE — current snapshot

_Keep this under ~1 page. Permanent history lives in DECISIONS.md, not here._
_Last updated: 2026-08-24 (Open Project now populates the GUI — committed as `e693316`, docs `f60e0dd`)_

## Now working on

Nothing in progress — awaiting next task.

## Recently completed

**File > Open Project now populates Calibrate/Batch Integrate (`e693316`)**
User complaint: opening a project (`e8dea6b`) only wired it up for *future*
logging — data paths/geometry/settings from what was already recorded were
never restored into the GUI. Fixed with a new `ProjectLoadDialog` shown
right after a successful Open Project: one row per panel found in the file
(single-detector or each present Hydra ge1–ge4), two checkboxes per row
(Calibrate / Batch Integrate) each paired with an attempt picker (defaults
to latest). `project.py` gained a read-side API
(`discover_panels`/`list_attempts`/`read_attempt`) plus pure mapping
functions that translate a stored attempt into the *same widget-keyed field
dicts* `CalibrationTab`/`BatchTab`'s existing `get_state()`/`set_state()`
machinery already applies — so `apply_project_calibration()`/
`apply_project_integration()` reuse that machinery instead of poking
widgets directly. Batch Integrate additionally gets a live, usable
calibration (via `project.calibration_namespace` + `set_calibration()`/
`set_panel_calibration()`), so Run works immediately without re-running
Tab 2 first. Known gap (documented, not fixed): embedded (no-file)
mask/dark/bright/background sources aren't restored — `FieldSelector`/
`MaskSelector` only ever supported file-path-backed sources.
Verified: new pure-logic + Qt end-to-end tests in `tests/test_project.py`
(discovery/listing/reading, both attempt→field mappings, a Hydra
2-panel apply, a single-detector apply); manually exercised against the
real `test_data/midas_project.h5` (a Hydra ge1–ge4 project) through a full
`MainWindow` — mode switch, per-panel seed values, and a live per-panel
Batch calibration all populated correctly. Full suite clean apart from the
two known pre-existing issues below.

**Calibrate: Hydra seed-mode linking + Eta vs R Cake tab (`162fef1`)** — Use
manual seed/Feed-back checkboxes linked across all 4 Hydra GE panels
(values stay independent); new Eta vs R Cake 2-D heatmap tab (both
Single-detector and Hydra Calibrate), reusing `IntegrationWorker`'s
already-computed cake array via a new `return_cake=True` flag. See
DECISIONS.md for full detail.

## Open questions / blockers

- None currently blocking.
- Pre-existing pyqtgraph interpreter-teardown crash risk: a plain
  `pytest tests/` (not through `release.sh`) still segfaults more often
  than before the two Hydra UI test files existed; `release.sh` itself
  runs them isolated and is unaffected. Do not reach for `gc.collect()`
  (confirmed to make it worse — see DECISIONS.md).
- `test_smoke.py::test_app_builds_offscreen` has a pre-existing, unrelated
  local-config flake (stale `visible_tabs` count) — confirmed present on
  bare `main` before this session's changes too.
- ~~`.context/` is gitignored~~ — checked this session: it is **not**
  gitignored (`.gitignore` only excludes `claude/` and `CLAUDE.md`; `git
  check-ignore` confirms `.context/STATE.md`/`DECISIONS.md` are tracked).
  The earlier flagged note was stale; no action needed.

## Recent changes (last 3-5 sessions, dated; drop the oldest as it grows)

- 2026-08-24 (`e693316` + docs `f60e0dd`): File > Open Project now offers a
  "Populate from project" dialog that restores Calibrate/Batch Integrate
  fields (+ a live calibration) from the project's recorded attempts.
- 2026-08-24 (`162fef1` + docs `be0347c`): Hydra Calibrate seed-mode
  (manual seed/feed-back) linked across all 4 panels; new Eta vs R Cake
  tab in both Single-detector and Hydra Calibrate.
- 2026-08-24 (`e8dea6b` + docs `e79a104`): File ▸ Project — opt-in FAIR
  provenance (HDF5) for Calibrate/Batch Integrate runs, single-detector +
  Hydra.
- 2026-08-24 (`6b961d4` + docs `0382137`): Batch Integrate tab split into
  Single-detector/Hydra modes, per-panel masks, automatic Calibrate→Batch
  hand-off, lazy Hydra-page construction, `release.sh` test-isolation fix.
- 2026-08-24 (`93dafa2` + docs `c23151f`): Calibrate tab split into
  Single-detector/Hydra modes, + bundled Data Viewer Hydra refinements
  (Transforms card extraction, per-panel Rotate, per-panel Projection,
  bounded radial-plot pan/zoom, Preset-fills-Name).

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
