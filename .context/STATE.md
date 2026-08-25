# STATE — current snapshot

_Keep this under ~1 page. Permanent history lives in DECISIONS.md, not here._
_Last updated: 2026-08-25 (Open Project auto-plots + header project label, `653c832` — committed, not yet pushed)_

## Now working on

Docs for `653c832` written (development_history.md + PDFs, gui_documentation.md
+ PDF) but not yet committed/pushed — do that next, then push both commits.

## Recently completed

**Open Project auto-displays rings/profile/cake + Hydra per-panel batch
plots; header project-name indicator (`653c832`)**
Populating Calibrate/Batch Integrate from a project attempt previously only
restored input *fields*, leaving every plot blank until Fit/Run was pressed
again. Now `CalibrationTab._display_stored_result()`/`HydraCalibrationPage.
display_stored_result()` redraw the ring overlay immediately from the
stored geometry (no image needed) and re-run the existing
`IntegrationWorker` for Radial Profile/Eta-vs-R Cake if the attempt's data
file still loads — mirrors a live Fit's `_on_done`/`_on_panel_done` exactly.
New `project.read_attempt_results()` reads the embedded
profiles/r_axis_px/frame_ids arrays (previously unread by Open Project);
`BatchTab._populate_plots_from_attempt()`/`HydraBatchPage.
populate_panel_plots()` replay them into Waterfall/Stacked-profiles — each
Hydra panel keeps its own independent viewer pair, so GE1–GE4 toolbar
switching now shows genuinely panel-specific results. Also added: a bold
high-contrast "Project: `<name>`" label at the tab bar's top-right corner
(mirrors the Profile combo's top-left one), alongside the existing quiet
status-bar label. Verified via new/extended tests in test_project.py +
test_smoke.py, each test file re-run individually in its own process (see
"Open questions" below re: the pyqtgraph segfault).

**Auto-detect pixel size + wavelength on file load (`9e4b5c0` + docs `e43f5a2`)**
Loading a Data file now auto-populates **pixel size** (from the detector tag
in the filename — `.ge1`–`.ge5` → GE 200 µm, `.vrx` → Varex 150 µm, `.pxrd`
identified but no known size) and, for an HDF5 frame file, **wavelength**
(from its recorded beam energy at `instrument/HEM/Energy`, keV → Å). Gated
to the **1-ID-E**/**20-ID-D**/**20-ID-E** profiles only (beamline-specific
filename/metadata conventions); anything not detected leaves the field at
its previous/default value. New `helpers.detect_geometry_from_path()` (+
`detect_detector_from_filename()`/`detect_wavelength_from_h5()`, best-effort
— never blocks the actual load on a read error). Wired into Data Viewer +
Calibrate, both single-detector (`DataLoaderPanel.metadataDetected` signal)
and Hydra mode (`HydraLoaderPanel.detected_geometry()`, consulted from each
page's `_on_siblings_changed`). Batch Integrate unaffected by design — it
gets geometry from the calibration it's handed, not the raw file. Verified:
new tests in `test_helpers.py` + `test_smoke.py`; full suite green apart
from the two known pre-existing issues below (each new/relevant test also
re-run individually in its own process to rule out the pyqtgraph
teardown-crash masking a real failure).

See DECISIONS.md / `development_history.md` (`e693316`, `162fef1`, `e8dea6b`)
for earlier sessions' work (Open Project populate-GUI, Hydra seed-mode
linking, FAIR provenance) — summarized in "Recent changes" below.

## Open questions / blockers

- None currently blocking.
- Pre-existing pyqtgraph interpreter-teardown crash risk — **now confirmed
  worse than previously documented**: this session found a plain
  `pytest tests/` (full dir, ignoring only the 2 named Hydra UI files, i.e.
  `release.sh`'s own invocation) segfaults/bus-errors on **unmodified**
  `main` too (3/3 runs), and `tests/test_hydra_ui.py` alone (not one of the
  2 files release.sh isolates) also crashes standalone on unmodified `main`.
  So this is env-level flakiness (this machine's current PyQt5/pyqtgraph/
  Python 3.12 combo), not something introduced by any recent session's code
  — but the earlier claim "`release.sh` ... is unaffected" no longer holds
  reliably. Verification going forward: trust per-file isolated runs (each
  file in its own `pytest <file>` process), not a combined `tests/` run. Do
  not reach for `gc.collect()` (confirmed to make it worse — see
  DECISIONS.md). Not attempted to fix — out of scope, pre-existing.
- `test_smoke.py::test_app_builds_offscreen` has a pre-existing, unrelated
  local-config flake (stale `visible_tabs` count) — confirmed present on
  bare `main` before this session's changes too.
- ~~`.context/` is gitignored~~ — checked this session: it is **not**
  gitignored (`.gitignore` only excludes `claude/` and `CLAUDE.md`; `git
  check-ignore` confirms `.context/STATE.md`/`DECISIONS.md` are tracked).
  The earlier flagged note was stale; no action needed.

## Recent changes (last 3-5 sessions, dated; drop the oldest as it grows)

- 2026-08-25 (`653c832`): Open Project now redraws rings/profile/cake
  (Calibrate) and Waterfall/Stacked-profiles per Hydra panel (Batch
  Integrate) from a populated attempt's stored result, not just fields;
  new header "Project: `<name>`" indicator (top-right, high-contrast).
- 2026-08-25 (`9e4b5c0` + docs `e43f5a2`): Auto-detect pixel size (filename
  tag) + wavelength (HDF5 beam energy) on file load, gated to 1-ID-E/
  20-ID-D/20-ID-E; Data Viewer + Calibrate, single-detector + Hydra.
- 2026-08-25 (`81056e4` + docs `27f172c`): Header Profile combo (instant
  switching); Hydra mode gated to 1-ID-E profile only; fixed Live PV
  device/Calibrant dropdowns + pixel/K-edge popups going stale on a
  profile switch.
- 2026-08-24 (`e693316` + docs `f60e0dd`): File > Open Project now offers a
  "Populate from project" dialog that restores Calibrate/Batch Integrate
  fields (+ a live calibration) from the project's recorded attempts.
- 2026-08-24 (`162fef1` + docs `be0347c`): Hydra Calibrate seed-mode
  (manual seed/feed-back) linked across all 4 panels; new Eta vs R Cake
  tab in both Single-detector and Hydra Calibrate.

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
