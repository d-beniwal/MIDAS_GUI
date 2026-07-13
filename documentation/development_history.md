# MIDAS GUI — Development History

A commit-by-commit record of what changed and when, so you can (a) find **when**
a particular behaviour/feature entered the code and (b) know **what to roll back**
to undo a specific effect.

> Keep this file updated when you add commits: append a new entry at the bottom of
> the chronological log and add a row to the quick-reference index. Regenerate the
> PDF the same way as the other docs (pandoc + headless Chrome) if you keep one.

---

## How to use this document

- **Find when a change landed:** search the change → commit index below, or
  `git log --oneline -- <path>` for a specific file, or
  `git log -S"<code string>"` to find the commit that introduced/removed a string.
- **See a commit's exact diff:** `git show <hash>` (whole commit) or
  `git show <hash> -- <path>` (one file).
- **Undo one commit cleanly (keeps later history):** `git revert <hash>`.
  For a range: `git revert <oldest>^..<newest>`.
- **Undo just one file back to a commit:** `git checkout <hash> -- <path>` then
  commit. To see the file *as of* a commit without changing anything:
  `git show <hash>:<path>`.
- **Hard reset to a point (destructive — local only, never on pushed history):**
  `git reset --hard <hash>`. Prefer `git revert` on a shared branch.
- **Dependencies matter.** Many later commits build on earlier ones (e.g. the
  unified data-loader, the config overlay). The "Roll back" note on each entry
  flags when reverting will disturb dependents.

Branch: `main`. All commits are authored by Dishant Beniwal (AI pair-authored).
Dates are commit dates (YYYY-MM-DD).

---

## Change → commit quick-reference index

### Packaging / build / launch
| Change | Commit |
|--------|--------|
| Initial v1.0.0 release (9-tab app, all backends) | `f19ee8c` |
| setuptools.build_meta backend (broader pip compat) | `3cd1f36` |
| `launch.py` standalone launcher | `2e9f419` |
| Startup crash diagnostics + robust launcher (Windows) | `916ece2` |
| MIDAS-style packaging (LICENSE, pyproject, release.sh, smoke tests) | `e862135` |

### Data Viewer (Tab 0)
| Change | Commit |
|--------|--------|
| Radial-integration plot, beam-centre ring picking, zoom persistence | `acf0866` |
| Zoom/pan preserved across frames (autoRange off) | `d9e527e` |
| Intensity-range mask, calibration-file loading, click-to-draw ring | `d50b588` |
| Compact 3-per-row lattice + cubic quick-set; mask default max(99.99pct,1e5) | `41f28f5` |
| Projection Skip-frames field; compact geometry/intensity fields; Lsd separators | `108ea7d` |
| Intensity-statistics panel + histogram (bottom of loader) | `2b7a695` |

### Mask Builder (Tab 1)
| Change | Commit |
|--------|--------|
| Unbounded σ fields; independent spatial/temporal auto-mask; Viewer↔Calibrate geometry hand-off | `10df828` |
| Temporal-constancy accepts a 3-D HDF5 (time,y,x) stack | `2385f75` |

### Calibrate (Tab 2) / Refinement (Tab 3)
| Change | Commit |
|--------|--------|
| Abort buttons; dark/bright/background field correction; auto-load on browse | `7d010d7` |
| Calibration input accepts paramstest/poni/json | `41f28f5` |
| Load-calibration-file (geometry + λ) helpers | `b381d8d` |

### Batch Integrate (Tab 4)
| Change | Commit |
|--------|--------|
| Live folder MONITOR + per-file legend | `1149afc` |
| Clear results + inline per-file labels & plot controls | `524f5f1` |
| Stacked profiles: publication themes, point+line, symbol size, x-units, grid | `d135c80` |
| Waterfall R/2θ/Q x-axis selector | `2385f75` |

### PDF Analysis (Tab 6)
| Change | Commit |
|--------|--------|
| Polyatomic midas_pdf reduction pipeline + vendored midas_pdf + calibration loading | `b381d8d` |
| Defaults to real I(Q) test data | `1149afc` |

### Pump Probe (TR-XRD) tab
| Change | Commit |
|--------|--------|
| New tab: TRR pooling + MIDAS-engine integration + ΔI(q,delay) + 4 pyqtgraph views + publication-quality plots + default MIDAS calibration | `590b410` |

### Cross-cutting UI / infrastructure
| Change | Commit |
|--------|--------|
| Linux visible files/folders style fix | `fc754d0` |
| Pixel-weighted azimuthal mean; readable QMenu; no-scroll combos | `41f28f5` |
| Unified Data-Loader panel + draggable 3-panel splitter (tabs 0/2/3/4) | `aa9395e` |
| Clickable λ label with K-edge foil menu | `b628446` |
| Clickable px label with detector pixel-size menu | `3248638` |
| Per-user JSON configuration system + Preferences dialog | `d30be2c` |
| Modular tab visibility (show/hide optional tabs) | `590b410` |
| User-adjustable interface scaling (HiDPI / 4K, QT_SCALE_FACTOR) | `d5fafd8` |

### Stability / performance / consistency (review-driven, phases 1–3)
| Change | Commit |
|--------|--------|
| Clean worker shutdown, drop QThread.terminate(), guard stdout, offload projection | `399f3d7` |
| Waterfall O(N²)→O(N), frame-slider debounce, radial-grid + stats caching | `4462ee1` |
| Dedupe distortion map + paramstest writer, align calibrant lattices, texture colormap | `8846619` |

### Documentation
| Change | Commit |
|--------|--------|
| README not-on-PyPI clone/conda instructions | `cd05ced` |
| gui_documentation.md added | `2e5f7c9` |
| gui_documentation.pdf added | `75c135c` |
| README/environment install via `pip install midas_suite` | `aceb40f` |
| implementation_details.md/.pdf added | `524f5f1` |
| config_gui.md + config.example.json added | `d30be2c` |
| development_history.md/.pdf added | `6d6f3fb` |

---

## Chronological commit log (oldest → newest)

### `f19ee8c` — Initial release: midas_gui v1.0.0 (2026-06-30)
**Effect:** First version. Nine-tab PyQt5 GUI over `midas_calibrate_v2` /
`midas_integrate_v2`: mask building, auto-calibration, refinement, batch
integration + waterfall, corrections, PDF, texture, export. Entry points
`midas-gui` / `python -m midas_gui`.
**Files:** entire `midas_gui/` package + `pyproject.toml`, `README`, `environment.yml`.
**Roll back:** this is the root — everything depends on it; don't revert.

### `3cd1f36` — setuptools.build_meta backend (2026-06-30)
**Effect:** Build backend switched for setuptools≥61 compatibility.
**Files:** `pyproject.toml`. **Roll back:** `git revert 3cd1f36` (only affects builds).

### `2e9f419` — add launch.py standalone launcher (2026-06-30)
**Effect:** `python /path/to/launch.py` runs the app from anywhere.
**Files:** `launch.py`. **Roll back:** safe to revert; later hardened by `916ece2`.

### `fc754d0` — Linux style fix for invisible files/folders (2026-06-30)
**Effect:** Stylesheet fix so file/folder text is visible on Linux.
**Files:** `midas_gui/style.py`. **Roll back:** `git revert fc754d0` (cosmetic).

### `acf0866` — Data Viewer: radial plot, ring picking, zoom persistence (2026-06-30)
**Effect:** Added the radial-integration plot, beam-centre ring picking, and
zoom-persistence across a stack in the Data Viewer; widened left panels.
**Files:** `tab_view.py` (+1-line width bumps in other tabs).
**Roll back:** revert to remove the Data Viewer radial plot/ring picking.

### `d9e527e` — preserve zoom/pan across frames (2026-06-30)
**Effect:** Disabled pyqtgraph autoRange on redisplay so zoom/pan survive frame
changes. **Files:** `widgets.py`. **Roll back:** revert if you *want* auto-refit per frame.

### `d50b588` — Data Viewer: intensity mask, calib loading, click-to-draw ring (2026-07-01)
**Effect:** 99.9-pct intensity-range mask default; load calibration (json/poni/
paramstest); click-to-draw ring; test-data defaults point at local `test_data/`.
**Files:** `tab_view.py`, `widgets.py`, `helpers.py`, `constants.py`, `tab_mask.py`.
**Roll back:** revert to drop these Data Viewer features (note `helpers.py` geometry
parsing added here is reused later — see `b381d8d`).

### `916ece2` — startup crash diagnostics + robust launcher (2026-07-01)
**Effect:** Logs uncaught exceptions/native faults to `~/midas_gui_error.log`,
prevents PyQt slot-exception aborts, isolates failing tabs, hardens the launcher.
**Files:** `app.py`, `launch.py`.
**Roll back:** revert only if you want raw exceptions to propagate; not recommended.

### `7d010d7` — Abort buttons + dark/bright/background + auto-load (2026-07-01)
**Effect:** Abort on calibrate/batch; dark/bright/background field correction
(file/folder/HDF5, index-range averaging, divide/subtract); browse auto-loads,
"Load" buttons removed; compact FieldSelector.
**Files:** `tab_batch/calibrate/corrections/mask/pdf/refine/texture/view.py`,
`widgets.py`, `workers.py`, `helpers.py`.
**Roll back:** wide-reaching; reverting removes field correction + auto-load across
tabs. The Abort behaviour here was later re-worked by `399f3d7` (no `terminate()`).

### `e862135` — MIDAS-style packaging (2026-07-01)
**Effect:** BSD-3 LICENSE, MIDAS-convention `pyproject.toml`, `release.sh` +
`RELEASING.md`, headless `tests/` smoke suite.
**Files:** `LICENSE`, `README.md`, `RELEASING.md`, `pyproject.toml`, `release.sh`,
`tests/`. **Roll back:** revert affects packaging/tests only.

### `41f28f5` — compact lattice, pixel-weighted azimuthal mean, readable menus (2026-07-06)
**Effect:** Data Viewer 3-per-row lattice + cubic quick-set; intensity-mask default
max(99.99pct,1e5); **pixel-weighted azimuthal mean** (selectable vs legacy η-bin
mean) fixing off-detector-BC distortion; batch calibration accepts paramstest/poni/
json; readable right-click menus; no-scroll combos.
**Files:** `helpers.py`, `workers.py`, `tab_view/batch/calibrate/pdf/refine/texture.py`,
`widgets.py`, `style.py`.
**Roll back:** to undo the azimuthal-averaging change specifically, prefer editing
the `weighted` default rather than reverting the whole commit (it bundles UI changes).

### `cd05ced` — docs: not-on-PyPI install instructions (2026-07-06)
**Effect:** README clone+conda+run instructions; relative env self-install.
**Files:** `README.md`, `environment.yml`. **Roll back:** docs only.

### `2e5f7c9` — docs: add gui_documentation.md (2026-07-06)
**Effect:** First committed user guide. **Files:** `documentation/gui_documentation.md`.
**Roll back:** docs only.

### `75c135c` — docs: add gui_documentation.pdf (2026-07-06)
**Effect:** PDF build of the guide. **Files:** `documentation/gui_documentation.pdf`.
**Roll back:** docs only.

### `aceb40f` — docs: install via `pip install midas_suite` (2026-07-06)
**Effect:** Corrected backend-install instructions. **Files:** `README.md`,
`environment.yml`. **Roll back:** docs only.

### `aa9395e` — Unified Data-Loader panel + 3-panel splitter (tabs 0/2/3/4) (2026-07-07)
**Effect:** Shared `DataLoaderPanel` (Data/Dark/Bright/Background/Mask, per-mode
frame controls) + `MaskSelector`; corrections applied on tabs 0/2/3/4; each of
those tabs restructured into a draggable `[Data Loader | Parameters | Display]`
splitter.
**Files:** `widgets.py` (+442), `tab_view/calibrate/batch/refine.py` (large
rewrites), `app.py`, `helpers.py`, `workers.py`, docs.
**Roll back:** **high impact** — this is the base for the Batch/Calibrate/Refine and
later Pump Probe layouts. Reverting breaks the 3-panel tabs and the Pump Probe
tab's left panel. Avoid a blanket revert; fix forward instead.

### `b381d8d` — Tab 6 polyatomic midas_pdf pipeline + calibration loading (2026-07-08)
**Effect:** PDF tab rewritten to the total-scattering pipeline; **vendored
`midas_pdf`** under `midas_gui/_vendor/` + `pdf_backend.py`; `helpers`
`geometry_fields_from_file` / `result_ns_from_geometry_file` (paramstest/json/poni)
used by Calibrate and PDF "Load calibration file…".
**Files:** `_vendor/midas_pdf/**` (new, large), `pdf_backend.py`, `tab_pdf.py`,
`workers.py`, `helpers.py`, `tab_calibrate.py`, `constants.py`, `pyproject.toml`, docs.
**Roll back:** reverting removes the PDF pipeline **and** the shared geometry-file
loaders that later tabs (incl. Pump Probe via `spec_from_geometry_file`) rely on —
revert with care.

### `1149afc` — Batch live MONITOR + legend; PDF real-data default (2026-07-08)
**Effect:** MONITOR button (stream mode) watches a folder and integrates new frames
via `FolderMonitorWorker`, reusing a cached detector map (factored
`build_integration_context()` + `write_profile()`, shared with `BatchWorker`);
per-file legend; PDF tab defaults to real I(Q).
**Files:** `tab_batch.py`, `workers.py`, `widgets.py`, `constants.py`, `tab_pdf.py`, docs.
**Roll back:** to remove MONITOR only, revert this commit — but
`build_integration_context()` introduced here is **reused by the Pump Probe worker**
(`590b410`), so a full revert would break Pump Probe integration.

### `524f5f1` — Batch Clear results + inline labels; implementation-details doc (2026-07-09)
**Effect:** "Clear results" button; inline per-file curve labels + line-width/font
toolbar on stacked profiles; new `implementation_details.md/.pdf`.
**Files:** `tab_batch.py`, `widgets.py`, `documentation/implementation_details.*`.
**Roll back:** safe, localized to Batch + the new doc.

### `10df828` — Mask unbounded σ + split auto-mask; Viewer↔Calibrate geometry (2026-07-09)
**Effect:** Removed σ upper limits; split statistical auto-mask into independent
Spatial-outlier / Temporal-constancy checkboxes; geometry hand-off
`DataViewer.pushGeometry` / `Calibrate.pullGeometry` → `apply_geometry`.
**Files:** `tab_mask.py`, `tab_view.py`, `tab_calibrate.py`, `app.py`, `workers.py`, docs.
**Roll back:** revert to restore bounded σ / combined auto-mask / no geometry hand-off.

### `d135c80` — Batch stacked-profiles: themes, point+line, x-units, grid (2026-07-09)
**Effect:** `StackedProfileViewer` publication/dark themes, point+line, symbol-size,
R/2θ/Q x-unit selector, grid toggle. (This viewer's styling is the template the
Pump Probe plots later mirror.)
**Files:** `widgets.py` (+228), `tab_batch.py`, docs.
**Roll back:** localized to the Batch stacked-profile viewer.

### `2385f75` — Batch waterfall R/2θ/Q axis; Mask 3-D HDF5 stack (2026-07-10)
**Effect:** Waterfall x-unit selector via a converting `AxisItem` +
`_convert_radial()`; Mask temporal-constancy reads a single 3-D (time,y,x) HDF5.
**Files:** `widgets.py`, `tab_batch.py`, `tab_mask.py`, `workers.py`, docs.
**Roll back:** `_convert_radial` / `_UnitAxis` introduced here are reused by the
Pump Probe heatmap — don't fully revert if Pump Probe is present.

### `b628446` — clickable λ label with K-edge foil menu (2026-07-10)
**Effect:** Compact clickable "λ" label → K-edge foils menu (sets λ from edge
energy). `constants.K_EDGE_FOILS`, `HC_KEV_A`, `helpers.make_kedge_label`.
**Files:** `constants.py`, `helpers.py`, `tab_view/calibrate/pdf.py`, docs.
**Roll back:** localized; `make_pixel_label` (next) reuses the factored helper.

### `108ea7d` — Data Viewer projection Skip-frames + compact fields (2026-07-10)
**Effect:** Projection "Skip frames" field; narrower Axis/Skip/pixel fields; Lsd
thousands-separators. **Files:** `tab_view.py`, docs. **Roll back:** localized.

### `3248638` — clickable px label with detector pixel-size menu (2026-07-10)
**Effect:** Clickable "px" label → GE/Varex/Pilatus/Eiger sizes;
`constants.PIXEL_PRESETS`; factored `helpers._clickable_menu_label`.
**Files:** `constants.py`, `helpers.py`, `tab_view.py`, `tab_calibrate.py`, docs.
**Roll back:** localized.

### `2b7a695` — Data Viewer intensity-statistics panel + histogram (2026-07-10)
**Effect:** `IntensityStatsPanel` (histogram + p70/p90/p99/p99.9/p99.99 with pixel
counts) pinned to the loader bottom (stack mode); Current/All/projected scope.
**Files:** `widgets.py`, `tab_view.py`, docs. **Roll back:** localized.

### `d30be2c` — per-user configuration system + Preferences dialog (2026-07-12)
**Effect:** Per-user JSON config overlays shipped defaults at import; `settings.py`;
`prefs_dialog.py` (Geometry/Paths/Materials/Calibrants/Menus/Algorithms);
Settings menu; `DEFAULT_KERNEL/PIPELINE/OUTPUT_FORMAT/ERROR_MODEL/COLORMAP`; tab
combos honor configured defaults. New `config_gui.md`, `config.example.json`,
`tests/test_config.py`.
**Files:** `settings.py` (new), `prefs_dialog.py` (new), `constants.py` (+165),
`app.py`, `tab_batch.py`, `tab_calibrate.py`, `widgets.py`, docs, tests.
**Roll back:** **high impact** — the config overlay + `DEFAULT_*` and the
Preferences dialog are extended by the modular-tabs commit (`590b410`). Reverting
this would also break `ui.visible_tabs` and the Tabs preferences section.

### `399f3d7` — Stability: clean worker shutdown, no terminate(), stdout guard (2026-07-12)
**Effect:** `closeEvent` stops all tab QThreads; removed every `QThread.terminate()`
(cooperative interrupt + orphan instead); `CalibrationWorker` restores stdout only
if still its own; Data Viewer projection moved to `ProjectionWorker` (off GUI thread).
**Files:** `app.py`, `tab_batch.py`, `tab_calibrate.py`, `tab_view.py`, `workers.py`.
**Roll back:** reverting reintroduces the exit hard-crash risk and UI-freeze on
projection — not recommended. Phase 1 of the 3-part review.

### `4462ee1` — Performance: waterfall O(N), debounce, caching (2026-07-12)
**Effect:** Waterfall pre-allocated buffer + 100 ms coalesced redraw (O(N) not
O(N²)); 60 ms frame-slider debounce; cached radial bin-index grid; skip all-frame
stats on frame-only changes. **Files:** `tab_view.py`, `widgets.py`.
**Roll back:** reverting slows large scans / scrubbing; behaviour otherwise same.
Phase 2 of the review.

### `8846619` — Consistency: dedupe maps/writer, align lattices (2026-07-12)
**Effect:** `helpers._PARAMSTEST_DISTORTION` derived from `constants._V2_TO_V1`
(removed duplicated `_V2V1` dicts); shared `helpers.write_standalone_paramstest`
used by Calibrate + Export; LaB6/Si `_LATT/_LC` aligned to `MATERIALS`; texture
colormap honors `DEFAULT_COLORMAP`.
**Files:** `constants.py`, `helpers.py`, `tab_calibrate.py`, `tab_export.py`,
`tab_texture.py`. **Roll back:** reverting reintroduces duplicated code paths; low
functional risk. Phase 3 of the review.

### `590b410` — Modular tabs + Pump Probe (TR-XRD) tab + plot quality (2026-07-12)
**Effect:** (1) **Modular tab visibility** — Data Viewer/Mask/Calibrate/Batch always
shown, the rest toggled in Settings ▸ Preferences ▸ Tabs (`ui.visible_tabs`,
applied live); `app.apply_tab_visibility()`. (2) **New Pump Probe tab**
(`tab_pumpprobe.py`) — TRR filename pooling, MIDAS-engine integration via new
`PumpProbeWorker` (reuses `build_integration_context`/`integrate_frame`), ΔI(q,delay),
three-panel layout, four pyqtgraph views; ships a converted MIDAS `paramstest` as
the default calibration. (3) **Plot quality** — white publication theme, black
axes/labels/titles, readable legends, ΔI colorbar, aligned reference panel, shared
draw-mode + line/point/font-size toolbar, bounded pan/zoom.
**Files:** `tab_pumpprobe.py` (new), `workers.py` (+`PumpProbeWorker`), `app.py`,
`constants.py`, `prefs_dialog.py`, `tests/test_smoke.py`, docs (`gui_documentation`,
`config_gui`, `config.example.json`).
**Roll back:** to remove **only Pump Probe**, delete `tab_pumpprobe.py` and its
wiring in `app.py`/`workers.py`; to remove **only modular tabs**, revert the
`app.apply_tab_visibility` / `prefs_dialog` Tabs section and `constants` tab lists.
A full `git revert 590b410` removes both features together. Depends on `aa9395e`
(DataLoaderPanel), `1149afc` (build_integration_context), `2385f75`
(`_convert_radial`/`_UnitAxis`), `d30be2c` (config/prefs).

### `6d6f3fb` — docs: add development_history.md/.pdf (2026-07-13)
**Effect:** Added this per-commit change log + change→commit index + rollback guide.
**Files:** `documentation/development_history.md`, `documentation/development_history.pdf`.
**Roll back:** docs only.

### `d5fafd8` — UI scaling: user-adjustable whole-interface zoom (HiDPI / 4K) (2026-07-13)
**Effect:** Whole-application scale (layout + fonts) for HiDPI / 4K monitors.
`constants.DEFAULT_UI_SCALE` (config `ui.ui_scale`); `app.main()` applies it via
`QT_SCALE_FACTOR` **before** the QApplication is created (so fixed widths, splitters,
pyqtgraph and stylesheet px all scale uniformly) + `AA_UseHighDpiPixmaps`. Manual
control in **Preferences ▸ Display** (spin + 100/125/150/200 % presets) and
**Settings ▸ Interface scaling…**; both persist `ui.ui_scale` and offer an immediate
self-restart (`MainWindow.restart_app` via `QProcess`) since the factor is read only
at startup.
**Files:** `constants.py`, `app.py`, `prefs_dialog.py`, docs (`gui_documentation`,
`config_gui`, `config.example.json`).
**Roll back:** `git revert d5fafd8`. Self-contained (depends only on the config
system `d30be2c`); reverting removes the scale control and the app renders at 1.0×.
To keep the feature but disable it, set `ui.ui_scale` to 1.0.

---

## Rollback recipes (common intents)

- **Undo the Pump Probe tab only:** `git revert 590b410` removes Pump Probe *and*
  modular tabs. To drop Pump Probe alone, hand-remove `midas_gui/tab_pumpprobe.py`,
  its import/construction/wiring in `app.py`, `PumpProbeWorker` in `workers.py`, and
  its entry in `constants.OPTIONAL_TABS`/`DEFAULT_VISIBLE_TABS`.
- **Undo modular tabs (show all 10 fixed again):** remove the `apply_tab_visibility`
  logic in `app.py` (add all tabs directly), the Tabs section in `prefs_dialog.py`,
  and the tab-list constants — see the `590b410` diff for the exact hunks.
- **Undo the config system:** `git revert d30be2c` — but first revert `590b410`'s
  config touches, since it extends the same `ui` overlay and Preferences dialog.
- **Revert the azimuthal-averaging change:** don't blanket-revert `41f28f5`
  (bundled with UI); instead set the `weighted` default back to the legacy η-bin
  mean in the batch/pump workers.
- **Go back to the pre-3-panel single-column tabs:** `aa9395e` is the pivot; a
  revert is large and cascades to later tabs — prefer fixing forward.

---

*Generated from `git log`. To refresh after new commits:
`git log --reverse --stat --date=short` and append entries above.*
