# ARCHITECTURE

_On-demand: system shape, components, data flow, UI conventions. Not auto-loaded._
_Verify against current code — file:line specifics may drift._

## What it is

`midas-gui` — PyQt5 desktop GUI for the MIDAS X-ray diffraction suite. Entry
point: `midas-gui` script → `midas_gui.app:main` (also `launch.py` standalone).
A ~9–10-tab QMainWindow (window 1600×950).

## Package layout (`midas_gui/`)

`app.py` (main window, tab assembly, entry) · `__main__.py` · `_paths.py`
(**inert stub** — only sets `KMP_DUPLICATE_LIB_OK`, `REPO_ROOT=None`;
backends are separate deps) · `constants.py` · `helpers.py` · `style.py` ·
`widgets.py` · `workers.py` · `calib.py` · `dialogs.py` · `prefs_dialog.py` ·
`settings.py` · `pdf_backend.py` (import boundary for vendored `midas_pdf` +
`midas_hkls.absorption` shim) · `_vendor/midas_pdf/` (vendored PDF pkg + data).

**Tabs** (`tab_*.py`): view, mask, calibrate, refine, batch, corrections, pdf,
texture, export, pumpprobe (TR-XRD). **Tab status:** 0–4 verified; 5–8 WIP —
keep this note current in `documentation/gui_documentation.md`. Tab visibility
is user-configurable via Settings▸Preferences (`ui.visible_tabs`).

## Cross-tab wiring

- Shared state held in MainWindow: `_calib_result`, `_spec`, `_mask`, `_gain`,
  `_dark`. Signals propagate **downward**.
- `MaskTab.maskReady` → Calibrate/Batch/Corrections `set_mask_from_tab1`
  (forwards to `loader.set_tab1_mask`).
- `CalibrationTab.calibrationDone(result)` → `BatchTab.set_calibration` +
  enables Refinement tab.

## Worker / threading pattern

- **Every op >0.1s runs in a `QThread` worker** (never block the UI thread) with
  `log_line` / `finished` / `failed` signals. `_LogStream` (io.TextIOBase)
  redirects `print()` → `log_line` so `[calibrate] STAGE...` progress shows in
  the Log panel.
- **Workers and `pg.SignalProxy` must be stored as instance vars** or they're
  GC'd mid-run.
- **Abort = terminate the thread**, not cooperative-only:
  `requestInterruption()` → `terminate()` → `wait()`. On terminate the worker
  `finally` never runs, so Calibrate manually restores `sys.stdout/err`. Batch
  gives ~1s cooperative grace then force-terminates, keeping frames already
  written. `terminate()` on torch/native threads carries small accepted
  instability risk.
- **Post-abort re-run fix:** detach signals, terminate, move worker to
  `self._orphans`, set `self._worker=None` so a fresh run starts immediately
  (blocking on `wait()` had left the worker "running" and blocked new runs).

## Shared widgets & compute helpers

- `widgets.DataLoaderPanel(mode="stack"|"single"|"stream")` — shared left panel
  for view/calibrate/batch/corrections; 3-panel `[loader|params|display]`,
  loader fixed 340px. Owns Data + frame nav, Dark/Bright/Background
  (`FieldSelector`), Mask (`MaskSelector` union). Getters: `current_frame()`,
  `full_stack()`, `n_frames()`, `frame_range()`, `source_cfg()`,
  `dark()/bright()/background()/bright_mode()`, `corrected(frame)`,
  `composite_mask()`. Signals `dataChanged`/`fieldsChanged`.
- `PickableImageViewer` — PICK_BC (single-click→`bcPicked`) and PICK_RING (3+
  clicks→algebraic circle fit→`ringFitBC`). Colormap via `iv.setColorMap()`.
- `ProfileViewer` — R/2θ/Q axis switch + Log Y. **Ring markers drawn LAST in
  `_replot()`** after `setXRange()`; stores ring *data* (`_ring_radii_px`, etc.)
  not items. `radiusClicked(float)` → Data Viewer draws magenta ring (`#ff30ff`).
- `DataViewerTab._radial_profile(img, bc_y, bc_z, r_bin, mask=)` — static numpy
  azimuthal mean (`np.indices`/`np.hypot`/`np.bincount`), **no MIDAS spec
  required** (runs without a calibration; off-centre BC is the usual reason
  manual radial "looks wrong"). Loading calibration.json supplies real geometry.
- Field correction math (`helpers.apply_field_corrections`): `(img−dark)` →
  bright divide `/(bright−dark)×mean` or subtract → `−background` → clip≥0.
  Fields averaged off-thread by `FieldAverageWorker` → `helpers.average_field`.
- Azimuthal averaging (`workers._profile_from_cake(cake, count_cake=None)`):
  count_cake from `workers.count_cake(...)`. **Pixel-count-weighted mean is the
  default** (`weighted=True`): `Σ(mean·count)/Σ(count)`, η-bin-size independent
  (legacy unweighted mean distorts badly with off-detector BC).

## Live Data ring buffer ("Use Buffer")

- `DataLoaderPanel._buffer` (`widgets.py`) — a plain `collections.deque(maxlen=n)`
  of numpy frames, n=2–100 via spin box. **In-memory only**, no disk/temp file.
- Appended on the GUI thread in `_on_live_frame`; consumed via `full_stack()`,
  which returns a **copied** `np.stack(...)`, never a view into the deque —
  so nothing outside `DataLoaderPanel` ever holds a live reference to it.
- New capture (toggling "Use Buffer" on, or restarting live stream while
  buffering) does `self._buffer = deque(maxlen=n)` — **replaces**, doesn't
  merge/append. Old deque has zero remaining refs → refcounted GC frees it
  immediately. Explicit `_reset_buffer()` sets it to `None` when unchecked.
- **No cleanup on GUI close**: `closeEvent` → `sys.exit(app.exec_())` ends the
  whole process, so the OS reclaims the buffer's RAM regardless; an explicit
  `del`/clear on shutdown would be inert given the current single-window,
  process-exits-on-close design. Only relevant if that changes (e.g. a
  reopen-without-restarting-process feature, or multiple `MainWindow`s in one
  process).
- **Multiple GUI instances on one machine** = separate OS processes = fully
  independent heaps/buffers; no sharing or interference, RAM use just sums.

## Batch specifics

- Drift correction: anchors JSON `{frame_idx: {Lsd, BC_y, BC_z}}` (Lsd µm, BC
  px); `_spec_from_trajectory` deep-copies base spec + `np.interp` per frame,
  rebuilding geometry each frame (costly but geometry-correct; per-frame Lsd →
  correct 2θ/Q axes).
- Mask applied by zeroing pixels in the corrections branch.

## Refinement (Tab 3)

- **Derivative-free Nelder-Mead**, not autograd: the differentiable integrator
  returns NaN gradients w.r.t. BC/tilt (atan2 singularity at R→0). NM on an
  η-uniformity loss converges reliably (recovered BC +6px → within 0.05px).
- LearnableGain: `g_i = 1 + scale·r_i` (scale=0.1, r_i init 0 = identity);
  loss = MSE(profile vs ref) + unity prior + smoothness (TV) prior.

## Crash diagnostics

`app.py._install_diagnostics()` (first in `main()`) installs `sys.excepthook` +
faulthandler → `~/midas_gui_error.log` + dialog. **PyQt5 ≥5.5 hard-aborts the
process on a slot exception** — this excepthook prevents the Windows
silent-crash. Tabs built in isolation (`_build_ui._tab`) so a raising tab
becomes a placeholder; signal wiring guarded by `_connect`.

## UI / UX conventions (user-mandated — see also DECISIONS)

- **Dioptas-inspired dark theme** (Fusion + dark QPalette, charcoal panels,
  off-white text); **single orange primary action per tab**; **no emojis**
  anywhere (UI or code). Inspiration repo: `~/ANL-research/github/Dioptas`.
- Green checkboxes AND radio buttons (white SVG tick via global stylesheet).
- Colormap default **"hot"**; vmin% default **30**; Y lower-limit −1000 linear /
  1 log.
- Compact left panels: pair related fields per row via `_twocol()`; advanced
  options in collapsible checkable QGroupBox; scrollable fixed-width panels.
- **No mouse-wheel value changes** on spinboxes (`_fspin`/`_NoScrollSpinBox`).
- Pixel value always visible in a monospace bottom status bar
  (`x(col)=N y(row)=M intensity=V`), `int(x)` floor.
- Auto-integrate immediately after calibration (default bins); Re-integrate +
  X-axis switch (R/2θ/Q) + Log Y in profile toolbar.
- **Ring markers MUST appear after calibration+integration** (user highly
  sensitive; has broken repeatedly). Dotted amber, toggle on by default;
  corrected forward-model rings cyan, off by default.
- Threshold mask always applied; auto-mask checkbox-gated below it; single
  Compute button; bad-pixel overlay on by default.
- **No silent failures** — every worker exception → `QMessageBox.critical()`
  (first ~400 chars of traceback).
- Browse auto-loads everywhere (Load buttons removed); default paths auto-load
  on startup guarded by `Path.exists()`. HDF5 dataset path auto-shown for
  .h5/.hdf5/.hdf/.nxs (defaults `exchange/data`, `exchange/data_dark`).
- Save paramstest.txt always offered alongside calibration.json in one dialog.
- Calibrated-parameters readout lives in a right-side "Results" tab, not the
  left control panel.
- **Detector-image origin is bottom-left `(0,0)`**, not top-left, on every
  `pg.ImageView`-based viewer (Data Viewer, Mask Builder, Calibrate) —
  matches MIDAS's convention that the on-screen image match the physical
  world view of the detector looking downstream from the sample along the
  beam. Enforced by one `vb.invertY(False)` call in
  `ImageViewer.__init__` (`widgets.py`), which overrides `pg.ImageView`'s
  own implicit `invertY()`; `roi_tools.py`'s crop-preview popup mirrors it.
  Independent of the `ImTransOpt`/Transforms-checkbox data-level flip.

## Tests & docs

- `tests/` (pytest); data under `tests/test_data/`. Offscreen QApplication smoke
  test exercises all tabs.
- `documentation/` (committed, shipped): `gui_documentation.md`,
  `development_history.md`, `implementation_details.md`,
  `pdf_calculation_explained.md`, `config_gui.md` — each with a `.pdf` rebuilt
  via pandoc + headless-Chrome.
