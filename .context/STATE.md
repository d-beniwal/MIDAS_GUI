# STATE — current snapshot

_Keep this under ~1 page. Permanent history lives in DECISIONS.md, not here._
_Last updated: 2026-08-25 (Data Viewer lab-frame axes overlay, `87d9df7` — committed, not yet pushed)_

## Now working on

Nothing in progress — working tree is clean. `87d9df7` (+ docs `8f618a8`)
committed locally; not yet pushed to `origin/main`.

## Recently completed

**Data Viewer: lab-frame axes overlay ported from midas-gui-swaxs (`87d9df7`)**
New **Lab-frame axes** checkbox on the Data Viewer's image toolbar overlays
the APS/MIDAS lab coordinate system (X_Lab/Y_Lab arrows, Z_Lab beam glyph,
η-sweep arc) anchored at the beam centre, ported from the sibling
`midas-gui-swaxs` repo's `DataViewerTab._draw_lab_axes()`. Porting subtlety:
that repo's `pg.ImageView` keeps pyqtgraph's default `invertY()`, needing a
`V=-1` flip to keep the Y_Lab arrow pointing up; this repo's `ImageViewer`
overrides that (`vb.invertY(False)`, MIDAS `(0,0)` bottom-left convention)
— the opposite sense — so `V=+1` here instead. Redraw wired to
`DetectorGeometryCard.geometryChanged` + every `self._cur`-reassigning call
site + `set_state()`. Verified offscreen (item count on toggle, redraw on
BC change, screenshot confirming arrow directions) plus 5x isolated
`test_smoke.py` runs (only the two known pre-existing flakes reproduced).

**Calibrate/Batch Integrate plots bounded like image viewers; Cake
right-drag zooms η only; Waterfall gains a color-scale sidebar (`08c917f`)**
Radial Profile, Eta vs R Cake (Calibrate) and Waterfall, Stacked profiles
(Batch Integrate) could be zoomed/panned arbitrarily far from their actual
data — unlike the main image viewers, which `ImageViewer._apply_view_limits()`
already bounds to the image extent. Added the same `setLimits()`-based
bounding to `CakeViewer`, `WaterfallViewer`, `StackedProfileViewer`
(`ProfileViewer`/`HydraProfileViewer` already had it). Also: the cake plot's
right-click-drag zoomed both R and η together (stock pyqtgraph); new
`_YZoomViewBox(pg.ViewBox)` restricts that gesture to η (Y) only, everything
else delegated to the base class. `WaterfallViewer` had no color-scale
legend at all (unlike the `pg.ImageView`-based viewers) — added a
`pg.HistogramLUTWidget` wired via `setImageItem()`, driven by the existing
cmap dropdown through the gradient editor. All three touched classes are
shared between single-detector and Hydra mode, so one fix covers both.
Verified via `tests/test_hydra_calib_ui.py`/`test_hydra_batch_ui.py`/
`test_hydra_ui.py` (each run individually, in its own process) plus manual
offscreen exercising of the drag math and histogram/cmap wiring.

See DECISIONS.md / `development_history.md` (`653c832`, `9e4b5c0`, `e693316`)
for earlier sessions' work (Open Project auto-plots, auto-detect geometry,
Hydra seed-mode linking) — summarized in "Recent changes" below.

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

- 2026-08-25 (`87d9df7` + docs `8f618a8`): Data Viewer gains a **Lab-frame
  axes** overlay toggle (APS/MIDAS coordinate compass), ported from
  midas-gui-swaxs with the Y-invert sign flipped for this repo's convention.
- 2026-08-25 (`08c917f` + docs `565524a`): Radial Profile/Cake/Waterfall/
  Stacked profiles pan-zoom bounded to data extent (like image viewers);
  Cake right-drag now zooms η (Y) only; Waterfall gains a color-scale
  histogram sidebar.
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
