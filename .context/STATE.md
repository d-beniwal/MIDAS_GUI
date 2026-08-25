# STATE — current snapshot

_Keep this under ~1 page. Permanent history lives in DECISIONS.md, not here._
_Last updated: 2026-08-25 (ImTransOpt propagation fix, `2358ae4` + docs
`003d466` — committed and pushed)_

## Now working on

Nothing in progress — working tree is clean. `2358ae4` (+ docs `003d466`)
committed and pushed to `origin/main`.

## Recently completed

**ImTransOpt propagation fix across Batch/Pump-probe/Refine/Data
Viewer/Mask Builder (`2358ae4`, see DECISIONS.md 2026-08-25 for full
detail)** User reported Batch Integrate's lineout was wrong whenever
the active calibration had a non-zero `ImTransOpt`. Root cause: the
transform was silently dropped when a calibration result became an
`IntegrationSpec`, so raw (untransformed) frames were integrated against a
geometry fit on a *transformed* image. Fixed so the MIDAS backend itself
performs every pixel flip used in an actual calibration/integration
computation (`spec.TransOpt` + `midas_integrate_v2`'s own
`apply_trans_opt=True`) — midas_gui no longer flips images in Python for
that purpose, only masks (backend has no flip-hook for those) and viewer
on-screen previews (explicitly fine per user). Touched: `helpers.py`
(central spec builders + a new "ImTransOpt" row in the Calibration-values
display), `workers.py` (CalibrationWorker, IntegrationWorker, BatchWorker,
FolderMonitorWorker, PumpProbeWorker, RefinementWorker, RefineCompareWorker,
MaskComputeWorker), `tab_batch.py`, `hydra_batch_page.py`/
`hydra_batch_widgets.py`, `tab_pumpprobe.py`, `hydra_geometry_card.py`.
Verified against the real `test_data/test_ps.txt` + CeO2 test image repro,
plus per-file-isolated pytest across every touched test file. Two real
*backend* (not GUI) limitations found along the way — logged in ROADMAP.md
("Package-side fixes") for future upstream/GUI follow-up, not fixed here.

**Upgrade all 8 MIDAS backend packages to latest PyPI releases (`a74b7d6`)**
midas-calibrate/-v2, midas-hkls, midas-integrate/-v2, midas-peakfit,
midas-zipper, midas-pdf bumped to latest (midas-distortion unchanged).
Verified API-clean first (3 parallel research passes statically diffed
every symbol midas_gui imports — all present, unchanged/compatible
signatures, zero midas_gui code changes needed), then tested in a cloned
conda env (`conda create --clone`) before touching the live `midas-gui`
env; full per-file-isolated test suite identical in both envs (no new
failures, same pre-existing pyqtgraph teardown flakiness); manual runtime
smoke test of the full `pdf_backend.py` surface against real test data
(identical numeric results). New dependency `midas-params` now required by
several bumped packages; `zarr`/`numcodecs` (previously unpinned transitive
deps) now pinned explicitly. Live env upgraded in place after tests were
green (`pip freeze` backup at `/tmp/midas-gui-env-backup-2026-08-25.txt`);
test clone (`midas-gui-next`) removed after cutover. **Gotcha found:**
`midas_gui` is pip-installed editable (`-e .`) in the `midas-gui` conda env
despite `environment.yml`'s comment claiming otherwise — its
`egg-info/requires.txt` goes stale on a pin bump and must be refreshed with
`pip install --no-deps -e .` (not a plain `-e .`, which would fight the
conda-forge PyQt5 pin — see that file's own warning) or pip prints spurious
"midas-gui requires X==old but you have new" conflict warnings forever.
**New (benign) runtime behavior:** midas-hkls 0.9.0 added a self-check on
its bundled ionic form-factor table; it now emits a `RuntimeWarning` when
loading Ce4+ (flags its own coefficients as violating the electron-count
sum rule, falls back to neutral-atom scattering) — upstream data-table
note, not a midas_gui bug, but will show up in the GUI's log console the
first time a Ce-containing composition hits PDF/structure-factor code.

See DECISIONS.md / `development_history.md` (`87d9df7`, `08c917f`,
`653c832`, `9e4b5c0`) for earlier sessions' work (lab-frame axes overlay,
plot bounding, Open Project auto-plots, auto-detect geometry) — summarized
in "Recent changes" below.

## Open questions / blockers

- None currently blocking.
- **New follow-ups (not blocking, tracked in ROADMAP.md "Package-side
  fixes" P3-1/P3-2 and the Texture per-tab item):** (1) several
  `midas_calibrate_v2` calibration pipelines have no native `im_trans`
  parameter — GUI already works around it correctly, upstream addition
  would just simplify `calib.py`; (2) `*BinGeometry.from_spec()` has no
  `apply_trans_opt` hook for masks — GUI must keep pre-flipping masks in
  Python indefinitely unless that's added upstream; (3) Texture tab's
  `PoleFigureWorker` has a pre-existing, unrelated mask/ImTransOpt bug,
  found but not fixed this session (out of scope).
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

## Recent changes (last 3-5 sessions, dated; drop the oldest as it grows)

- 2026-08-25 (`2358ae4`): ImTransOpt propagation fix — the MIDAS backend
  now performs every pixel flip for calibration/integration; GUI only
  pre-flips masks and viewer-display previews. See "Recently completed" above.
- 2026-08-25 (`a74b7d6`): Upgraded all 8 MIDAS backend packages to latest
  PyPI releases (API-clean, zero midas_gui code changes); added
  `midas-params`, pinned `zarr`/`numcodecs`; tested in a cloned conda env
  before cutting the live `midas-gui` env over.
- 2026-08-25 (`87d9df7` + docs `8f618a8`): Data Viewer gains a **Lab-frame
  axes** overlay toggle (APS/MIDAS coordinate compass), ported from
  midas-gui-swaxs with the Y-invert sign flipped for this repo's convention.
- 2026-08-25 (`08c917f` + docs `565524a`): Radial Profile/Cake/Waterfall/
  Stacked profiles pan-zoom bounded to data extent (like image viewers);
  Cake right-drag now zooms η (Y) only; Waterfall gains a color-scale
  histogram sidebar.

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
