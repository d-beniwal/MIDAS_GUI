# STATE — current snapshot

_Keep this under ~1 page. Permanent history lives in DECISIONS.md, not here._
_Last updated: 2026-08-25 (Auto-detect pixel size/wavelength on file load, `9e4b5c0` — committed & pushed)_

## Now working on

Nothing in progress — working tree is clean, everything below is committed
and pushed to `origin/main`.

## Recently completed

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
- 2026-08-24 (`e8dea6b` + docs `e79a104`): File ▸ Project — opt-in FAIR
  provenance (HDF5) for Calibrate/Batch Integrate runs, single-detector +
  Hydra.

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
