# STATE — current snapshot

_Keep this under ~1 page. Permanent history lives in DECISIONS.md, not here._
_Last updated: 2026-08-23 (Hydra detector-view feature in progress — engine done, UI in progress)_

## Now working on

Building a Hydra (4-panel GE detector) mode into the Data Viewer tab.
Approved plan at implementation time: `/Users/dbeniwal/.claude/plans/rosy-wiggling-wind.md`
(7 phases). Done so far, all committed to `main`:
- **Phase 1** (`3d2d99d`, folded together with 1.5): leftmost `HydraModeRibbon`
  (Single detector / Hydra) + `QStackedWidget`; Hydra page is still a
  placeholder.
- **Phase 1.5** (same commit): extracted ~700 lines of ring-simulation/
  calibration-load-save/radial-integration logic out of `DataViewerTab` into
  a reusable `DetectorGeometryCard` (`hydra_geometry_card.py`), bindable via
  `set_viewer`/`set_profile_view`/`set_image_source`/`set_radial_controls` —
  verified behavior-identical via offscreen pixel-diff before any
  Hydra-specific code was added.
- **Phase 2, engine half** (`dd44f38`): `midas_gui/hydra.py` (windmill
  compositing math, ported), `helpers.hydra_siblings`/`hydra_panel_index`
  (sibling auto-discovery), bundled default geometry
  (`hydra_default_geometry/ps_ge{1..4}.txt`), a committed synthetic test
  fixture (`test_data/gui_synthetic/hydra/`), and two new test files
  (`test_hydra_geometry.py`, `test_hydra_chirality.py` — 13 tests total).
  **Resolved the chirality/X-mirror open question**: verified (real local
  s1ide data + the new synthetic-marker tests) that this codebase's
  `pg.ImageView`-based viewers need NO X-mirror correction, unlike the
  reference project's own custom viewer. Full reasoning in DECISIONS.md.

## Next steps (remaining phases, not yet started)

- **Phase 2, UI half**: build `HydraLoaderPanel` (single "any geX file" path
  field + sibling-status + frame navigator) and wire the ge1/ge2/ge3/ge4/
  composite toolbar buttons to display each raw panel (no compositing yet)
  in the shared `ROIImageViewer`, replacing the current placeholder Hydra
  page in `tab_view.py`.
- **Phase 3**: wire each of 5 `DetectorGeometryCard` instances (ge1-4 +
  composite) to a `hydra.DetectorState`; middle-panel card swaps on toolbar
  click; per-panel calibration-file loading feeds `DetectorState` geometry.
- **Phase 4**: composite button (`build_windmill_composite`) + composite
  ring overlay (centered at canvas/2) — the compositing math itself is
  already built/verified; this phase is UI wiring only.
- **Phase 5**: `HydraProfileViewer` — 4 independent per-panel radial
  profiles + a toggleable summed/resampled "composite" curve (approach
  confirmed with user: NaN-aware `nansum` after resampling each panel's own
  profile onto a shared 2θ axis, NOT integrating the composited image).
- **Phase 6**: docs + a real windowed manual pass (both modes, save/load
  state round-trip, mode switching mid-session).

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

- 2026-08-23 (`3b785c9`, `85069ed`, `3d2d99d`, `f3c7ffa`, `dd44f38`,
  `8554114`, pushed to `main` as of this session): Hydra detector-view
  feature, phases 1/1.5/2-engine (see "Now working on" above), plus an
  unrelated stale-test-data-path fix. Full detail in `DECISIONS.md`.
- 2026-08-23 (`c8d98f1`, `246dba7`, `328674d`): `test_data/` reorganized
  into per-dataset subfolders; detector-image origin flipped to
  **bottom-left `(0,0)`** across every `pg.ImageView`-based viewer — see
  prior STATE.md snapshot / `DECISIONS.md` for full detail (superseded
  here to keep this file short).
- 2026-08-17 (`068bd0d`, `0e782b1`): MIDAS `ImTransOpt` support added to
  Data Viewer, Mask Builder, Calibrate.

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
