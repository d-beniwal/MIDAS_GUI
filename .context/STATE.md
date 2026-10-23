# Project state

Last updated: 2026-10-23

## Now working on

Solve Cell visualization: fixed progressive plot shrink on spot selection
in residual mode. The colorbar repeatedly stole axes area; it now has a
fixed axes, and clicks update only the selection marker. Added regressions
for screen area and actual repeated click events. Evaluated GPU options:
VisPy is the recommended larger upgrade; no graphics dependencies installed.

Previously implemented a first interaction slice using the
installed matplotlib 3-D canvas and pyqtgraph detector view. Hover/click
exposes spot provenance and fit details; selection is shared across views,
with explicit navigation to the source panel and original loaded frame.
Added continuous q-residual colors, UB-fitted observed-reflection overlays,
camera/zoom preservation, and a Fit visible spots control. New ingest
invalidates old domain claims and selection. Changes remain uncommitted;
pre-existing tooltip/indexing-default edits and `scratch/` were preserved.

## Next steps

- Verify the new interactions on real multi-panel data, including filtered
  frame schedules and multiple domains. Offscreen synthetic visual QA and
  automated tests do not replace beamline validation.
- Continue Solve Cell: domain comparison/reduced cells/misorientation,
  geometry resolution ladder, classification and diagnostics. Preserve the
  documented restriction against blindly indexing pooled mixed domains.
- If a new 3-D renderer is needed, evaluate that separately: this change
  retains matplotlib and rebuilds artists on data/color/filter updates;
  selection now updates only its marker.
- Investigate the previously reported `midas_defect._grouped_median` abort;
  it has not been root-caused or fixed here. Check current dependency pins
  before pursuing the earlier pandas-version hypothesis.
- Apply thread-pool treatment to the known-cell random rotation search;
  consult ROADMAP for other Solve Cell and package-side work.
- Live-app checks remain for MCP Calibrate operations, the APEXA launcher,
  restart/relaunch and real multi-panel calibration output.

## Open questions / blockers

- Beamtime reference datasets are absent locally (three pipeline tests skip).
- Windows `lheald` calibration import failure still needs direct tracebacks
  and backend version details.
- Qt teardown/local preferences can obscure test results; run affected files
  separately and use a fresh temporary HOME as needed (ARCHITECTURE).

## Recent changes

- 2026-10-23 — Fixed residual-colorbar plot shrink; selection preserves existing scene/colorbar objects and plot area. 81 UI tests passed with a clean temporary HOME; rendered plot inspected.
- 2026-10-23 — Added Solve Cell linked spot selection, hover details, residual coloring and fitted-position overlays; exported UB/assigned HKLs/per-reflection fit data with explicit q conventions and stable ingest spot IDs.
- 2026-10-23 — New ingest clears stale domain assignments; diamond-filter reruns invalidate prior ab-initio refinement inputs. Solve Cell UI: 79 passed; pipeline: 39 passed, 3 skipped; app smoke: 13 passed; synthetic offscreen render inspected.
- 2026-10-23 — Existing commit `54fc06d` synchronized environment.yml/pyproject.toml dependency pins; main matches origin/main at inspection.
- 2026-10-22 — Exposed ingest CPU count and synced requirements pins; real-data grouped-median abort reported (unresolved).
