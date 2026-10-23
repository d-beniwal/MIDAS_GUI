# Project state

Last updated: 2026-10-23

## Now working on

Branch `pyvista` (off `main` @ `689282c`): migrated Solve Cell's detector
and reciprocal-space views from pyqtgraph/matplotlib to PyVista/VTK
(GPU-backed, native scalar bar/legend, VTK point picking). A VisPy attempt
first (branch now deleted) segfaulted under this project's
`QT_QPA_PLATFORM=offscreen` test convention on first draw — would have
crashed every Solve Cell UI test, not just rendering ones. PyVista doesn't
have that problem: `pyvistaqt.QtInteractor` supports every scene-building/
picking/camera op offscreen with no crash (only `.screenshot()`/`.image`
raise a catchable `RenderWindowUnavailable` there); `pv.Plotter(off_screen=
True)` renders real pixels offscreen with no Qt platform dependency at all.

New module `midas_gui/pyvista_views.py`: `PyVistaImageView` (detector tab)
and `PyVistaScatter3DView` (reciprocal tab), both built on
`pyvistaqt.QtInteractor` via a shared `_PickableInteractor` (left-click-vs-
drag via press/release threshold, VTK `vtkPointPicker` keyed off a
`"spot_id"` point-data array — same click/drag discrimination the
matplotlib version used, no manual screen-space projection needed). Cell
C-order raveling of a 2-D frame onto `pv.ImageData` + `view_xy()` gives the
MIDAS bottom-left pixel convention by default (verified empirically — see
DECISIONS); `PyVistaImageView` does NOT touch the shared `widgets.
ImageViewer` used by the other 10 tabs. Native `add_scalar_bar()`/
`add_legend()` replace the matplotlib colorbar-axes/`ax.legend()`
entirely — explicit `color="white"` is required on the scalar bar, or its
title/tick text is invisible against the black background (hit this,
fixed it, caught by eye in a screenshot, not by any test). Dependencies
(`vtk==9.7.1`, `pyvista==0.49.1`, `pyvistaqt==0.13.1`) are pip-installed,
not conda-forge: conda-forge's `vtk`/`pyvista` pull ffmpeg/proj/libcurl/
mesa-lavapipe versions that conflict with this repo's existing pins (and,
for current VTK, need macOS>=15; this machine is 14.8).

A second native-crash trap, independent of VisPy-vs-PyVista: Python's
interpreter-exit GC destroying a VTK render window (rather than Qt's own
`deleteLater()` + event loop) segfaults, since there is no real OpenGL
context offscreen to tear down safely. Caught by pytest-forked as a
CRASHED test even when the test body itself passed. Fixed via explicit
`widget.deleteLater(); app.processEvents()` in the `tab` test fixture
(`test_solve_cell_ui.py`) and in `test_pyvista_spike.py`'s own teardown —
necessary for ANY test that constructs `SolveCellTab` or either new widget.

Rewrote the ~19 `test_solve_cell_ui.py` tests that inspected matplotlib/
pyqtgraph internals against the new widgets' own state (stored groups/
actors, `camera_position`, `legend_entries`, `scalar_bars.keys()`,
`spot_overlay`/`selected_xy`). Click/picking tests now drive the
`spotClicked`/`spotHovered` signals directly rather than faking screen
coordinates: VTK's `vtkPointPicker` needs a real viewport to resolve
screen->world, which doesn't exist under `QT_QPA_PLATFORM=offscreen` on
this machine (confirmed: `WorldToDisplay()` returns degenerate (0,0,z)
coordinates there) — the picker itself is VTK's own, mature, untested-here
code; what these tests own is the glue (spot_id -> `_select_spot`).

## Next steps

- Launch the real app on a real display and interact with both views by
  hand (orbit/zoom, hover/click, Color-by modes, fitted overlay) — only an
  offscreen screenshot-based check has been done here (see DECISIONS).
- Verify on real multi-panel data, including filtered frame schedules and
  multiple domains — same caveat as before, offscreen QA ≠ beamline
  validation.
- Decide whether to merge `pyvista` into `main` once live-app interaction
  is confirmed good, or keep iterating on this branch first.
- Continue Solve Cell: domain comparison/reduced cells/misorientation,
  geometry resolution ladder, classification and diagnostics. Preserve the
  documented restriction against blindly indexing pooled mixed domains.
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
  separately and use a fresh temporary HOME as needed (ARCHITECTURE). Now
  additionally confirmed as a genuine race (not just "stale state"): running
  `test_solve_cell_pipeline.py` + `test_solve_cell_ui.py` together hit
  macOS's `objc[...]: may have been in progress in another thread when
  fork() was called` — a known fork-safety hazard in a multi-threaded
  process (pytest-forked uses `os.fork()`) — with non-deterministic
  severity (one run: 15 plain FAILED; another: 80 CRASHED with SIGABRT).
  Pre-existing, independent of the PyVista work; each file still passes
  cleanly alone every time.

## Recent changes

- 2026-10-23 — `pyvista` branch: migrated Solve Cell detector/reciprocal views from pyqtgraph/matplotlib to PyVista/VTK, after a VisPy attempt segfaulted offscreen (branch deleted). New `pyvista_views.py`; rewrote 19 affected UI tests; fixed an interpreter-exit VTK teardown crash via explicit fixture cleanup; fixed an invisible (black-on-black) scalar bar title. 81 UI + 39 pipeline (3 skipped) + 3 spike tests all pass individually; offscreen screenshots of both views inspected.
- 2026-10-23 — Fixed residual-colorbar plot shrink; selection preserves existing scene/colorbar objects and plot area. 81 UI tests passed with a clean temporary HOME; rendered plot inspected. (main, `689282c`)
- 2026-10-23 — Added Solve Cell linked spot selection, hover details, residual coloring and fitted-position overlays; exported UB/assigned HKLs/per-reflection fit data with explicit q conventions and stable ingest spot IDs.
- 2026-10-23 — New ingest clears stale domain assignments; diamond-filter reruns invalidate prior ab-initio refinement inputs. Solve Cell UI: 79 passed; pipeline: 39 passed, 3 skipped; app smoke: 13 passed; synthetic offscreen render inspected.
- 2026-10-23 — Existing commit `54fc06d` synchronized environment.yml/pyproject.toml dependency pins; main matches origin/main at inspection.
