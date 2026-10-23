# Project state

Last updated: 2026-10-23

## Now working on

Branch `pyvista` (off `main` @ `689282c`): Solve Cell's reciprocal-space map
is PyVista/VTK (GPU-backed, native scalar bar/legend, VTK point picking);
the Detector view was tried in PyVista too but reverted back to the
pyqtgraph `widgets.ImageViewer` + `pg.ScatterPlotItem` version after live
comparison looked worse (see DECISIONS 2026-10-23, second entry) —
`pyvista_views.PyVistaImageView` was deleted as dead code once nothing
referenced it. A VisPy attempt before any of this (branch now deleted)
segfaulted under this project's `QT_QPA_PLATFORM=offscreen` test convention
on first draw — would have crashed every Solve Cell UI test, not just
rendering ones. PyVista doesn't have that problem: `pyvistaqt.QtInteractor`
supports every scene-building/picking/camera op offscreen with no crash
(only `.screenshot()`/`.image` raise a catchable `RenderWindowUnavailable`
there, and a plain `.grab()` of the live widget also comes back blank
offscreen — real pixel QA needs a real display); `pv.Plotter(off_screen=
True)` renders real pixels offscreen with no Qt platform dependency at all.

`midas_gui/pyvista_views.py` now holds only `PyVistaScatter3DView`
(reciprocal tab) built on `pyvistaqt.QtInteractor` via `_PickableInteractor`
(left-click-vs-drag via press/release threshold, VTK `vtkPointPicker` keyed
off a `"spot_id"` point-data array — same click/drag discrimination the
matplotlib version used, no manual screen-space projection needed). Native
`add_scalar_bar()`/`add_legend()` replace the matplotlib colorbar-axes/
`ax.legend()` entirely — explicit `color="white"` is required on the scalar
bar, or its title/tick text is invisible against the black background (hit
this, fixed it, caught by eye in a screenshot, not by any test). Reciprocal
spot size is now adjustable from the tab (`_recip_point_size` spinbox,
default 14, up from the old hardcoded 6) with two modes
(`_recip_size_mode`): equal size, or size scaled by each spot's `"integrated"`
blob intensity relative to the brightest visible spot, via
`vtkPointGaussianMapper`'s per-point `scale_array` (see DECISIONS). Picking
is unaffected by either the mapper-style or the Detector-view reversion.
Dependencies (`vtk==9.7.1`, `pyvista==0.49.1`, `pyvistaqt==0.13.1`) are
pip-installed, not conda-forge: conda-forge's `vtk`/`pyvista` pull ffmpeg/
proj/libcurl/mesa-lavapipe versions that conflict with this repo's existing
pins (and, for current VTK, need macOS>=15; this machine is 14.8).

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

- Re-run Ingest on `s3idc_oct8_spinel.h5` in the real app (im_trans fix is in; headless re-run result in `scratch/spinel_overlay/rerun.log`) and confirm pooled recip map is one consistent crystal. Omega-step item below is already done in the project.
- Fix the real `s3idc_oct8_spinel.h5` project by hand: panels 3/4's `omega
  step` is 0.0 (should almost certainly be 0.1, matching panel 1 and the
  stated -30..29 scan) -- re-run Ingest once corrected and confirm the
  pooled reciprocal-space scatter looks like one consistent crystal/domain
  set across all three panels, not panel_01 alone looking "real".
- Launch the real app on a real display and try the new omega auto-detect
  checkbox by hand (field combo, Detect button, both new warnings) -- only
  exercised headless/offscreen with a synthetic file and the one real
  beamline file checked so far.
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

- 2026-10-23 — Solve Cell: ingest now honours the calibration's `im_trans` (was ignored -> mirrored spots, inconsistent multi-panel q) + always-on `check_im_trans` guard/warnings (pipeline + pre-ingest confirm). +7 pipeline, +3 UI tests; real 3-panel project re-ingested headless -> spinel a=7.974/7.975/7.976 A (was 8.13/7.90/8.26).
- 2026-10-23 — Solve Cell: diagnosed a real multi-panel Ingest distortion to `ome_step: 0.0` on 2 of 3 panels in a real project (auto-detect fills first/last frame idx, not step -- see DECISIONS); added a Detect-time reminder and an Ingest-time confirm-to-proceed warning for a zero step across a genuinely bounded window, careful not to false-positive on a panel whose omega mapping was never touched (caught this via a UI-test hang on unmocked `QMessageBox.warning`). 7 new tests; 96/96 pass, stable across repeats.
- 2026-10-23 — Solve Cell: added "Auto-detect first/last frame idx from omega metadata" to the Frame ↔ omega mapping group (checkbox, omega-field combo, start/end-omega fields, Detect button); new `helpers.detect_omega_frame_range` (value-in-range, not nearest-match/step-count -- see DECISIONS). Validated against a real beamline file (frame 38/628 for -30°/29°, matching the user's manually-determined answer). Also fixed a pre-existing intermittent interpreter-exit VTK segfault in `test_solve_cell_ui.py`'s other project-restore round-trip test. 91/91 UI tests pass, confirmed stable across two full runs. (Later same day: `test_solve_cell_ui.py` now 99/99, two consecutive clean full runs.)
- 2026-10-23 — `pyvista` branch: reverted Solve Cell's Detector view from PyVista back to pyqtgraph (`ImageViewer`/`ScatterPlotItem`, same as `main`) after live comparison; reciprocal-space map stays PyVista. Deleted now-unused `PyVistaImageView`. Added adjustable reciprocal spot size (spinbox, default 14) with equal/by-total-intensity modes (`vtkPointGaussianMapper` per-point `scale_array` off each spot's `"integrated"` field); selection-ring size now tracks the chosen spot size. Reverted + added tests in `test_solve_cell_ui.py`; full file (108 tests) + spike (3) + pipeline (39, 3 skipped) all pass.
- 2026-10-23 — `pyvista` branch: migrated Solve Cell detector/reciprocal views from pyqtgraph/matplotlib to PyVista/VTK, after a VisPy attempt segfaulted offscreen (branch deleted). New `pyvista_views.py`; rewrote 19 affected UI tests; fixed an interpreter-exit VTK teardown crash via explicit fixture cleanup; fixed an invisible (black-on-black) scalar bar title. 81 UI + 39 pipeline (3 skipped) + 3 spike tests all pass individually; offscreen screenshots of both views inspected.
- 2026-10-23 — Fixed residual-colorbar plot shrink; selection preserves existing scene/colorbar objects and plot area. 81 UI tests passed with a clean temporary HOME; rendered plot inspected. (main, `689282c`)
- 2026-10-23 — Added Solve Cell linked spot selection, hover details, residual coloring and fitted-position overlays; exported UB/assigned HKLs/per-reflection fit data with explicit q conventions and stable ingest spot IDs.
- 2026-10-23 — New ingest clears stale domain assignments; diamond-filter reruns invalidate prior ab-initio refinement inputs. Solve Cell UI: 79 passed; pipeline: 39 passed, 3 skipped; app smoke: 13 passed; synthetic offscreen render inspected.
- 2026-10-23 — Existing commit `54fc06d` synchronized environment.yml/pyproject.toml dependency pins; main matches origin/main at inspection.
