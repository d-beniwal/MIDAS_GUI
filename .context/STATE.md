# STATE — current snapshot

_Keep this under ~1 page. Permanent history lives in DECISIONS.md, not here._
_Last updated: 2026-08-24 (Hydra composite also needed a vertical-axis mirror on top of the CCW revert — ge2/ge4 were swapped left/right; fixed)_

## Now working on

Hydra (4-panel GE detector) mode, Data Viewer tab. Phases 1-5 (engine, UI,
per-panel calibration, radial profiles) landed on `main` in a prior session
(`3b785c9`..`1d8c6e5`, not pushed to `origin`). **Phase 6 (the real
windowed manual pass) started this session and immediately found 5 real
bugs** that offscreen tests never caught — all 5 are now fixed, but
**uncommitted**:
1. λ/max 2θ/px now shared (mirrored) across ge1-4 + Composite cards
   (`DetectorGeometryCard.get_shared_fields`/`apply_shared_fields`,
   `HydraViewerPage._sync_shared_fields`).
2. Dark/bright/background correction added for Hydra (was a documented
   scope cut) — new `HydraFieldSelector` (`hydra_widgets.py`), sibling
   -aware like the main data path, reuses `helpers.apply_field_corrections`/
   `workers.FieldAverageWorker` as-is.
3. Composite windmill orientation — two rounds of fixes:
   a. Rotation direction was originally "fixed" to clockwise this session,
      but that was **wrong** — reverted back to counterclockwise (the
      original, pre-session convention was correct all along).
   b. Even after (a), the composite still had ge2/ge4 on the wrong sides
      (left/right swapped) — the user identified this precisely from the
      physical layout. Fixed by adding a vertical-axis mirror to the
      composite canvas (`hydra.py::compute_inv_coords`: `Y_lab = (half -
      Yo) * px` instead of `(Yo - half) * px`) — independent of the
      rotation-direction logic, and scoped to the composite build only
      (doesn't touch per-panel ge1-4 raw displays).
   Both confirmed by rebuilding the composite from the real
   `park_may26_bc` calibration + real `park_may26/ge{1..4}` frames and
   checking panel placement/orientation directly (pixel forensics on
   screenshots alone was inconclusive for either; render-and-compare with
   real code + real data, plus the user's own knowledge of the physical
   layout, is what actually resolved both). See DECISIONS.md's "reverted
   back to counterclockwise" and "needs a vertical-axis mirror too"
   entries.
4. Stale radial-integration geometry fixed: a BC/λ/Lsd/px/tilt edit after a
   full geometry (tilts) was loaded now actually moves the radial profile,
   not just the ring overlay (`_effective_calib_geom` was returning a
   frozen snapshot).
5. vmin% percentile auto-level now excludes exact-zero pixels app-wide
   (`widgets.py::ImageViewer._redisplay`) — fixes a washed-out Composite
   view (mostly-empty canvas dominated the percentile).

Full reasoning for all 5 in DECISIONS.md's "Five Hydra bugs found in the
real windowed (Phase 6) pass" entry (+ the two newer entries for bug 3's
corrections). `test_hydra_chirality.py` and `test_hydra_geometry.py`'s
hand-derived formulas updated to match both the reverted (counterclockwise)
rotation and the new vertical-axis mirror. All 14 hydra geometry/chirality
tests pass; the known pyqtgraph teardown crash (see below) still
occasionally reproduces when the whole `test_hydra_*.py` battery runs in
one process — unchanged risk profile, not worsened.

## Next steps

- Composite orientation (rotation direction + left/right mirror) is now
  user-confirmed correct against real `test_data/s1ide` data.
- Rest of Phase 6: continue the real windowed manual pass — Pick BC/Pick
  Ring tools on a real Hydra panel (interactive mouse picking can't be
  exercised headlessly), and general layout/usability at a real window
  size, now including the new Dark/Bright/Background card and shared-field
  behavior.
- Commit this session's fixes (not yet committed) once the above manual
  check is done — remember `documentation/development_history.md` +
  PDF rebuild per standing rules.
- Remaining documented scope cut: no intensity-range mask or Top-N
  brightest-pixel in Hydra mode (dark/bright/background is no longer a
  cut — see above).
- A rare, pre-existing pyqtgraph interpreter-crash risk under a large test
  suite (many `ImageView`/`ViewBox` instances) — see DECISIONS.md if
  `pytest` ever segfaults/bus-errors again; do not reach for `gc.collect()`,
  already confirmed to make it worse. New tests were folded into existing
  ones specifically to avoid raising this risk further.

## Open questions / blockers

- None currently blocking. `test_smoke.py::test_app_builds_offscreen`'s
  known local-config flakiness (`visible_tabs` double-counting) reproduced
  again this session on unmodified `main` — confirmed pre-existing,
  unrelated (same as prior sessions).
- **Found and fixed this session** (`3b785c9`, separate commit): six
  `DEFAULT_*` test-data paths in `constants.py` were stale after the
  2026-08-23 `gui_synthetic/` reorg (pointed at the old repo-root
  locations) — silently broke every tab's default-data preload. Fixed to
  point at `test_data/gui_synthetic/`.
- **Found, NOT fixed (local machine state, out of repo scope)**: the
  locally-active "1-ID-E" profile at `~/Library/Application
  Support/midas_gui/profiles/1-ID-E.json` has the same stale paths baked in
  from before the reorg (seeded at some point before `3b785c9`). It
  overrides the now-fixed shipped defaults at runtime via
  `constants.reload_from_config()`. Needs a manual reset/edit of that local
  profile file to actually see the fix take effect on this machine — not
  something to fix via a repo commit.
- **`.context/` is `.gitignore`d in this repo** (`.gitignore:58`), which
  contradicts the global CLAUDE.md instruction that `.context/` should
  always be committed. Not touched — flagged for the user to decide whether
  to change (pre-existing repo state, not caused by this session).
- `test_data/s1ide/` (real CeO2 Hydra data, gitignored, not created by this
  assistant) is now confirmed useful and actively used read-only for Hydra
  compositing verification (user approved this use). Still never committed
  or treated as a pytest dependency — only `test_data/gui_synthetic/hydra/`
  is.

## Recent changes (last 3-5 sessions, dated; drop the oldest as it grows)

- 2026-08-24 (uncommitted): follow-up to the session below — the Hydra
  composite still had ge2/ge4 on the wrong sides after the CW->CCW revert;
  fixed with an added vertical-axis mirror in `compute_inv_coords`. See
  "Now working on" above + DECISIONS.md.
- 2026-08-23 (uncommitted): Hydra Phase 6 manual pass found and fixed 5
  bugs — shared λ/max2θ/px, dark/bright/background correction, composite
  rotation direction, stale radial-integration geometry, and zero-excluded
  vmin% percentile. The rotation-direction fix was initially flipped the
  wrong way (CW) and then corrected back to CCW after a real-data visual
  check. See "Now working on" above + DECISIONS.md.
- 2026-08-23 (`3b785c9`..`1d8c6e5`, 14 commits, on `main`, not pushed to
  origin): Hydra detector-view feature, phases 1 through 5, plus an
  unrelated stale-test-data-path fix (`3b785c9`) and committing `.context/`
  which had been gitignored (`778a2f3`). Full detail in `DECISIONS.md`.
- 2026-08-23 (`c8d98f1`, `246dba7`, `328674d`): `test_data/` reorganized
  into per-dataset subfolders; detector-image origin flipped to
  **bottom-left `(0,0)`** across every `pg.ImageView`-based viewer — see
  `DECISIONS.md` for full detail (superseded here to keep this file short).

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
