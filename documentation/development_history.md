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
| Ship test_data sample data in the repo (fresh clone works out of the box) | `7e157e0` |
| Drop midas_suite from env (unblock conda env create; backends via -e .) | `72a1770` |
| Pin environment.yml to verified NumPy-1 set + README recreate steps | `68313f8` |

### Data Viewer (Tab 0)
| Change | Commit |
|--------|--------|
| Radial-integration plot, beam-centre ring picking, zoom persistence | `acf0866` |
| Zoom/pan preserved across frames (autoRange off) | `d9e527e` |
| Intensity-range mask, calibration-file loading, click-to-draw ring | `d50b588` |
| Compact 3-per-row lattice + cubic quick-set; mask default max(99.99pct,1e5) | `41f28f5` |
| Projection Skip-frames field; compact geometry/intensity fields; Lsd separators | `108ea7d` |
| Intensity-statistics panel + histogram (bottom of loader) | `2b7a695` |
| Tilt/distortion-aware radial integration when a calibration is loaded | `df544d2` |
| Data Viewer Top-N brightest pixels + mask-aware stats + full-geometry receive | `bc86d74` |
| Calibrate per-coefficient distortion, frame averaging, seed feedback | `b0a5cc3` |
| Batch read-only "Calibration values" card | `b6f1681` |
| Pump Probe publication plotting, delay colour-bar, default detector mask | `49623ae` |
| Gitignore local context, internal docs, large sample sets | `b251ca8` |
| Live Data card: in-tab EPICS PVA stream via pvapy, reload button | `1769f69` |
| Live viewer: manual colormap/level changes now survive new frames | `234ded9` |
| Wider image/radial-plot splitter handle | `69f470c` |
| Radial profile: Y-min defaults to 0.9x data min, manual Y-range sticks | `96f8add` |
| Simulate rings is now a live toggle (auto-recomputes on param change) | `ecf6d01` |
| Live PV field becomes a device dropdown (Preferences ▸ Devices) | `aa82cb6` |
| Fix image-view autorange drift; bound pan/zoom to image | `c156c62` |

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
| Multi-column paramstest results readout + Send-to-Data-Viewer button | `455ca4e` |
| Calibrate Results all-text (drop distortion table; named distortion, bigger font) | `3a6ecb9` |
| Fix calibrate() initial_BC_y TypeError (kwargs filter) + pin scikit-image | `b1642fa` |
| Pick-Ring fit overlay recolored blue (was same amber as simulated rings) | `3eb4a44` |

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
| Default optional tabs reduced (Corrections/PDF/Texture/Export hidden) | `7e157e0` |
| Lsd displayed in mm (calculations & calibration files stay in µm) | `c4e1c12` |
| Fix "Populating font family aliases" warning (real fixed-width fonts) | `d6df1f8` |
| Fix fresh-env launch crash — colormap resolution without matplotlib | `6332b3d` |
| Fix native Bus-error crash on Run Calibration — cap BLAS/OMP thread pools | `0b4326d` |
| Preferences ▸ Devices tab; Live PV field becomes a device dropdown | `aa82cb6` |

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

### `c4e1c12` — Display Lsd in mm (calculations & files stay in µm) (2026-07-13)
**Effect:** The sample-to-detector distance is entered/shown in **mm** in the Data
Viewer, the Calibrate seed, and Preferences ▸ Geometry; conversion to/from µm happens
only at the display boundary (`DataViewerTab._lsd_um()`, ×1000 on read, ÷1000 on set).
`get_geometry`/`apply_geometry`/`manual_seed`/prefs `_assemble` still emit µm; the
config key stays `lsd_um` (µm); calibration-file writers (`paramstest.txt`,
`calibration.json`) are unchanged (always µm). Batch drift-status label → mm.
**Files:** `tab_view.py`, `tab_calibrate.py`, `prefs_dialog.py`, `tab_batch.py`, docs.
**Roll back:** `git revert c4e1c12` to return every Lsd field to a µm display. Purely
a display-layer change — no stored/file/calculation values are affected either way.

### `d6df1f8` — Fix "Populating font family aliases" startup warning (2026-07-13)
**Effect:** Removed the `qt.qpa.fonts: Populating font family aliases` warning (and its
~40 ms startup cost) that Qt emitted because the code named the nonexistent family
"Monospace" — both via `QFont("Monospace", …)` and the CSS generic
`font-family:monospace`. Now names real per-platform families
(Menlo/Consolas/DejaVu Sans Mono/Courier New) via `style.MONO_FAMILIES` / `MONO_CSS`
and `widgets._mono_font()` (QFont `setFamilies` + Monospace style hint).
**Files:** `style.py`, `widgets.py`, `tab_export.py`, `tab_view.py`.
**Roll back:** `git revert d6df1f8` (cosmetic; only affects fixed-width font selection
and re-introduces the harmless warning).

### `455ca4e` — Calibrate: multi-column results readout + Send-to-Data-Viewer (2026-07-13)
**Effect:** (1) The Calibrate **Results** tab shows the full parameter set exactly as
written to `paramstest.txt` (Lsd, BC, tx/ty/tz, p0–p14, Parallax, Wavelength, px,
NrPixelsY/Z, RhoD, SpaceGroup, LatticeConstant) in a 3-column, column-major grid
(`_paramstest_pairs` via the shared writer + `_populate_param_grid`), with a
strain/timing line and the named-distortion table below. (2) New **→ Send to Data
Viewer** button (`sendGeometryToViewer` signal + `_send_to_viewer`) pushes the
calibrated λ/pixel/Lsd/beam-centre into the Data Viewer through new
`DataViewerTab.set_geometry` (µm internal, Lsd shown mm, auto-BC off); wired in
`app.py` as the reverse of the Viewer→Calibrate hand-off.
**Files:** `tab_calibrate.py`, `tab_view.py`, `app.py`, docs.
**Roll back:** `git revert 455ca4e`. Self-contained; depends on the mm-display change
(`c4e1c12`) for the Viewer Lsd conversion.

### `7e157e0` — Ship test_data; hide four optional tabs by default (2026-07-14)
**Effect:** (1) `test_data/` sample data (calibrant_ceria.tif/h5, nickel_stack.h5,
nickel_tifs/, make_test_data.py) is now committed so the GUI's default paths work on a
fresh clone — `.gitignore` re-includes it past the global `*.tif`/`*.h5` ignores via
trailing negations, while `test_data/output/`, `out_temp.txt` and `.DS_Store` stay
ignored. (2) `DEFAULT_VISIBLE_TABS = ["Calib. Refinement", "Pump Probe"]` — Corrections,
PDF Analysis, Texture and Results & Export ship hidden (enable in Preferences ▸ Tabs).
**Files:** `.gitignore`, `test_data/**` (added), `constants.py`, `config.example.json`,
`tests/test_smoke.py`, docs.
**Roll back:** `git revert 7e157e0` restores the all-optional-tabs default and removes
the shipped data + re-ignores `test_data/`. Note: a per-user config with its own
`ui.visible_tabs` overrides the shipped default regardless.

### `6332b3d` — Fix launch crash on fresh Linux/Windows (colormap w/o matplotlib) (2026-07-14)
**Effect:** Fixes the GUI failing to start on a clean env where every always-on tab's
image viewer crashed with `'NoneType' object has no attribute 'getColors'`. Cause:
`DEFAULT_COLORMAP` "hot" (and the whole `COLORMAPS` list) are matplotlib colormaps;
`pg.colormap.get("hot")` returns None without matplotlib, and that None was passed into
pyqtgraph. New `widgets._resolve_cmap()` resolves native → matplotlib → native fallback
→ grayscale ramp and never returns None (used by ImageViewer, WaterfallViewer, Texture);
matplotlib added as a declared dependency (`pyproject` + `environment.yml`
matplotlib-base) so the named maps actually resolve. The downstream "Geometry hand-off
wiring failed / QWidget has no attribute pushGeometry" log lines were only a symptom of
the tabs becoming placeholders and disappear with this fix.
**Files:** `widgets.py`, `tab_texture.py`, `pyproject.toml`, `environment.yml`,
`tests/test_smoke.py`.
**Roll back:** `git revert 6332b3d` (reintroduces the crash on matplotlib-less envs).

### `72a1770` — env: drop midas_suite (unblocks `conda env create`) (2026-07-14)
**Effect:** `conda env create -f environment.yml` was aborting because the pip section's
`midas_suite` meta-package pulls `midas-index 0.7.3`, whose sdist fails to build against
`scikit-build-core>=0.8` (`Use cmake.version instead of cmake.minimum-version`). The GUI
never uses midas_suite's extras; the backends it actually imports
(midas-calibrate-v2/-integrate-v2/-calibrate/-hkls/-distortion) are declared in
`pyproject.toml` and pulled by the editable `-e .` install. Removed the redundant
`midas_suite` line; README updated. (Unrelated to the repo: the `libarchive.19.dylib` /
conda-libmamba-solver errors in that log are a broken base-Anaconda mamba, worked around
with `--solver=classic`.)
**Files:** `environment.yml`, `README.md`.
**Roll back:** `git revert 72a1770` (re-adds midas_suite and its build failure).

### `68313f8` — Pin environment.yml to a verified NumPy-1 set + README recreate steps (2026-07-14)
**Effect:** Makes `conda env create` reproduce a known-good environment. Fresh installs
previously pulled the newest backends (numba 0.66 → NumPy 2.x) against conda torch 2.2.2,
which can't interop with NumPy 2 (`torch.from_numpy: Numpy is not available`). Pinned
every package to the versions from the user's working base Anaconda — the **NumPy-1
family**: numpy=1.26.4, scipy=1.13.1, h5py=3.11.0, tifffile=2023.4.12, pyqtgraph=0.14.0,
matplotlib-base=3.8.4 (conda); PyQt5==5.15.10, torch==2.4.0, numba==0.59.1, and the
NumPy-1-era MIDAS backends (calibrate-v2 0.3.3, integrate-v2 0.1.0, calibrate 0.2.3,
hkls 0.4.1, distortion 0.2.0) via pip; then `-e .`. Dropped the conda pytorch/cpuonly
lines + pytorch channel (torch now via pip). `pyproject.toml`: lowered `midas-calibrate`
floor 0.2.7 → 0.2.3 so the proven base version satisfies `-e .`. README gains a
"Recreating the environment" section (remove + create), a conda-solver/libarchive note,
and the CPU-only torch install note. Verified end-to-end: fresh env resolves clean; GUI
+ a real nickel-frame integration run (numpy 1.26.4 + torch 2.4.0).
**Files:** `environment.yml`, `pyproject.toml`, `README.md`.
**Roll back:** `git revert 68313f8` (returns to ranged deps; a fresh env would again risk
the NumPy-2/torch mismatch). To move forward to a NumPy-2 stack instead, bump torch>=2.3
and the backends to their numba-0.66 releases together.

### `3a6ecb9` — Calibrate Results: all-text multi-column readout (drop distortion table) (2026-07-14)
**Effect:** The Calibrate **Results** tab is now entirely text. Removed the distortion
`QTableWidget`; the distortion coefficients appear in the same multi-column parameter
grid, with the paramstest `p0–p14` slots relabelled to their names (iso_R2, a1, phi1, …)
via `helpers._PARAMSTEST_DISTORTION`. Larger font (12 pt) and roomier row/column spacing
for readability. Removed the `DistortionTable` import + `set_distortion` call.
**Files:** `tab_calibrate.py`, `documentation/gui_documentation.md`.
**Roll back:** `git revert 3a6ecb9` (restores the named-distortion table widget below the
grid). `DistortionTable` still exists in `widgets.py` (used only here), so a revert is clean.

### `b1642fa` — Fix calibration TypeError (initial_BC_y) + add scikit-image (2026-07-14)
**Effect:** Fixes calibration failing with `calibrate() got an unexpected keyword
argument 'initial_BC_y'` on the pinned (base-matching) midas-calibrate-v2 0.3.3, whose
`calibrate()` has no beam-centre seed parameter (only newer backends add it). New
`calib._supported_kwargs(fn, kwargs)` filters kwargs to the installed callable's
signature (keeps `initial_Lsd`, drops the unsupported `initial_BC_y/z`, logs it), so the
GUI tolerates backend-version signature drift. Also pins **scikit-image=0.23.2** in
`environment.yml` — midas-calibrate-v2's `auto_seed_calibrant` needs it; without it,
seeding degrades to the coarse arc fallback. Verified: manual-seed one_shot calibration
of the shipped ceria runs end-to-end (numpy 1.26.4 + torch 2.4.0).
**Files:** `calib.py`, `environment.yml`.
**Roll back:** `git revert b1642fa` (reintroduces the TypeError on backends lacking the
BC-seed args, and removes the scikit-image pin).

### `df544d2` — Data Viewer: tilt/distortion-aware radial integration (2026-07-14)
**Effect:** When a loaded calibration file carries the full geometry (tilts + distortion),
the Data Viewer's radial integration goes through the MIDAS engine
(`build_integration_context` + `integrate_frame`) instead of concentric-circle binning,
so pixels map through the calibrated tilts/distortion. `_load_calibration` now also reads
the full geometry via `geometry_fields_from_file` (falls back to scalar/circle mode if
absent) and shows the active mode. `_midas_radial` caches the binning geometry per
(geometry, R-bin, image shape, static mask) and reuses it across frames (fast `hard`
kernel; loader's static composite mask so the cache holds); `_radial_integrate` branches
to it with a guarded fallback to circle binning on error.
**Files:** `tab_view.py`, `documentation/gui_documentation.md`.
**Roll back:** `git revert df544d2` (reverts to circle-only radial integration in the
Data Viewer). Self-contained; the MIDAS-engine primitives it reuses are unchanged.

### `bc86d74` — Data Viewer: Top-N pixels + mask-aware stats + full-geometry receive (2026-07-19)
**Effect:** Adds a "Top-N pixels" toolbar toggle that marks the N brightest pixels
(crosshair + circle) and feeds their values to the intensity-statistics panel, re-ranking
live on frame/mask changes. Masked pixels (mask file + intensity-range mask) are excluded
from the Top-N ranking and its stats/histogram. `_midas_radial` now unions the static mask
with the per-frame intensity-range mask and fingerprints the mask by content so the cached
binning context rebuilds when the mask changes. The tab also accepts a *full* geometry dict
(tilts + distortion + detector size) from Calibrate and routes integration through the MIDAS
engine when it arrives. The stats panel readout box auto-sizes to its content (no inner
scrollbar) with the histogram as the single flexible child; `DataLoaderPanel` is now a
`QWidget` hosting a scroll area + stats panel in a draggable vertical splitter.
**Files:** `tab_view.py`, `widgets.py`, `documentation/gui_documentation.md`.
**Roll back:** `git revert bc86d74`.

### `b0a5cc3` — Calibrate: per-coefficient distortion, frame averaging, seed feedback (2026-07-19)
**Effect:** Adds `DistortionRefineDialog` (behind the "…" next to the Distortion checkbox)
to pick any of the 15 harmonics or use η-fold presets; `calib.py` maps each selected v2
harmonic to its v1 p-slot (via `DISTORTION_ISO`/`DISTORTION_PRESETS` in constants). Adds an
"Average frames" card (start:end:skip, streamed via `DataLoaderPanel.average_frames`), seed
tilts (tx/ty/tz) carried into the v1 params, a "Feed result back to seed" option, and a
"→ Send to Data Viewer" that now sends the full geometry (tilts + distortion + detector
size). Drops the 310px bottom-panel cap and adds a non-collapsible splitter.
**Files:** `calib.py`, `tab_calibrate.py`, `dialogs.py`, `constants.py`, `widgets.py`,
`documentation/gui_documentation.md`.
**Roll back:** `git revert b0a5cc3`.

### `b6f1681` — Batch: read-only "Calibration values" card (2026-07-19)
**Effect:** Adds a Calibration values card to the Batch parameter column showing the
geometry actually driving integration (λ, Lsd, BC, tilts, pixel sizes, detector size,
non-zero distortion count), resolved from the live Tab-2 result or a calibration file and
refreshed on source/path change. Also lets the Log panel fill its splitter space.
**Files:** `tab_batch.py`, `documentation/gui_documentation.md`.
**Roll back:** `git revert b6f1681`.

### `49623ae` — Pump Probe: publication plotting, delay colour-bar, default mask (2026-07-19)
**Effect:** Publication-quality plot defaults (clean 2.0px lines, 13pt fonts, dashed grid,
refactored `_rainbow_cmap`); a continuous delay→colour bar (`GradientLegend`) replaces the
many-row legend past a threshold, with left/right legend anchoring; a log-delay x-axis
option for kinetics; and a default detector mask wired via new
`DataLoaderPanel.add_mask_file` / `MaskSelector.add_file_source`. TR-XRD defaults now point
at a local-only `test_data_pump_probe/` folder kept out of git.
**Files:** `tab_pumpprobe.py`, `constants.py`, `widgets.py`,
`documentation/gui_documentation.md`.
**Roll back:** `git revert 49623ae`.

### `b251ca8` — chore: gitignore context, internal docs, large sample sets (2026-07-19)
**Effect:** Ignores the local `.context/` scaffold and `CLAUDE.md`, the internal-only PDF
calculation write-up, and the large local sample dirs (`test_data/17BM/`,
`test_data/test_s25ide/`) via directory-level ignores that override the `test_data/**`
re-includes.
**Files:** `.gitignore`.
**Roll back:** `git revert b251ca8`.

### `1769f69` — Data Viewer: live PV stream (Live Data card) (2026-07-20)
**Effect:** Subscribes to an EPICS PVA image PV via `pvapy` and pushes frames straight
through the existing image/corrections/ring/radial-integration pipeline — no separate
viewer window. "Live Data" is a collapsible card (own title-bar checkbox) above the
Data card; unchecking stops an active stream. Data card gets a small ⟳ reload button
to restore the static file/folder/HDF5 source after stopping a stream. `pvapy==5.4.1`
is now a required, pinned dependency (chosen over latest to avoid upgrading the
numpy/pyqtgraph pins). `MainWindow` calls a generic `tab.shutdown()` hook on close so
a live stream stops cleanly on app exit.
**Files:** `widgets.py`, `tab_view.py`, `app.py`, `pyproject.toml`, `environment.yml`,
`documentation/gui_documentation.md`, `tests/test_live_stream.py`.
**Roll back:** `git revert 1769f69`.

### `0b4326d` — Fix: cap native thread pools to prevent calibration crash (2026-07-22)
**Effect:** Clicking "Run Calibration" crashed the app outright — a native, uncatchable
`Bus error`, not a Python exception. Root cause: `CalibrationWorker` (a `QThread`) triggers
a multi-threaded `numpy.linalg.inv()` deep inside `midas-calibrate-v2`'s HKL/ring generation
(likely surfaced by the recent 0.3.3 → 0.5.2 bump); running from a `QThread` while the Qt
event loop is alive, that races against PyTorch's own OpenMP pool and corrupts memory.
Reproduced deterministically at two call sites (`numpy.linalg.inv` during ring generation,
`scipy.ndimage.median_filter` during seeding); both disappear once native thread pools are
capped to 1. Every heavy operation in this app runs inside a `QThread` (17 worker classes in
`workers.py`), so the fix caps `OPENBLAS_NUM_THREADS` / `OMP_NUM_THREADS` / `MKL_NUM_THREADS`
/ `VECLIB_MAXIMUM_THREADS` / `NUMEXPR_NUM_THREADS` to 1 process-wide via `os.environ.setdefault`
in `_paths.py` — already home to the sibling `KMP_DUPLICATE_LIB_OK` workaround for the same
underlying duplicate-OpenMP-runtime issue — rather than patching each worker individually.
Verified: offscreen run of the full calibration pipeline to convergence with no crash;
`pytest` 15/15 green.
**Files:** `midas_gui/_paths.py`.
**Roll back:** `git revert 0b4326d` (restores multi-threaded BLAS; calibration/other workers
become exposed to this native-crash class again on macOS).

### `234ded9` — Live viewer: preserve manual colormap/level changes across frames (2026-07-24)
**Effect:** `ImageViewer._redisplay` recomputed vmin%/vmax% percentile levels on every
incoming frame (live or otherwise), silently overwriting a level range the user had
dragged on the pyqtgraph histogram mid-acquisition. Now tracks manual histogram edits
via `sigLevelsChanged` (guarded against feedback from our own programmatic `setImage`
calls with a suspend flag) and keeps pinning to the last manual levels until the user
explicitly changes the vmin%/vmax% spin boxes, which resets back to auto.
**Files:** `widgets.py`.
**Roll back:** `git revert 234ded9`.

### `69f470c` — Data Viewer: widen the image/radial-plot splitter handle (2026-07-24)
**Effect:** The vertical splitter between the image view and the radial-integration
panel had no explicit handle width (unlike the outer horizontal splitter), making it
thin and hard to grab. Set `setHandleWidth(8)` to match.
**Files:** `tab_view.py`.
**Roll back:** `git revert 69f470c`.

### `96f8add` — Radial profile: stop forcing Y-axis min to -1000 on every update (2026-07-24)
**Effect:** `ProfileViewer._replot` unconditionally called `setYRange()` with a
hardcoded -1000 floor on every redraw, clobbering any manual Y-range the user set.
The auto lower bound is now `0.9 * current data minimum`; once the user manually
changes the Y range (drag, wheel-zoom, or the axis context menu's numeric entry —
detected via `sigYRangeChanged` with the same suspend-flag pattern as `234ded9`),
that value is kept for the rest of the session instead of being recomputed.
Also dropped the hardcoded `setLimits(yMin=-1000/1)` hard floors.
**Files:** `widgets.py`.
**Roll back:** `git revert 96f8add`.

### `3eb4a44` — Calibrate: recolor Pick-Ring fit overlay to blue (2026-07-24)
**Effect:** `PickableImageViewer`'s Pick-Ring points, fitted circle, and fitted-center
marker used the same amber (`#f0c060`) as the Data Viewer's Simulate-rings overlay,
making the two indistinguishable when both were visible on the same image. Pick-Ring's
fit overlay is now blue (`#2a7fd4`); the separate Pick-BC "+" click marker (already
`#00aaff`) and Simulate-rings amber were left unchanged.
**Files:** `widgets.py`.
**Roll back:** `git revert 3eb4a44`.

### `ecf6d01` — Data Viewer: make Simulate rings a live toggle instead of one-shot (2026-07-24)
**Effect:** "Simulate rings" is now a checkable toggle rather than a single-click
action. While on, it resimulates automatically whenever material, lattice constants,
space group, or geometry (wavelength/Lsd/pixel size/max 2θ) change — mirroring the
existing `_rad_auto`-style "recompute on change" idiom already used for radial
integration. Beam-centre edits still just reposition the existing rings (unchanged;
ring radii don't depend on BC), so no wiring was added there.
**Files:** `tab_view.py`.
**Roll back:** `git revert ecf6d01`.

### `aa82cb6` — Preferences: add Devices tab; Data Viewer Live PV becomes a device dropdown (2026-07-24)
**Effect:** New **Preferences ▸ Devices** tab (add/remove/edit rows of name +
prefix + PVA suffix), following the existing Materials/Calibrants table-tab
pattern; persisted as a new `"devices"` list in the JSON config (replaces
wholesale when present, same as materials/calibrants). Ships pre-filled with
the 20-ID-D detectors extracted from the beamline's `make_det(...)`
blueprint — `oryx20idd` (`20iddOR1:`), `s20idPil` (`20idPil`), `pg4`
(`1idPG4:`), `gh1s` (`20idGH1s:`), `s20varex1` (`20IDFF:`) — all with PVA
suffix `Pva1:Image`. The Data Viewer's Live Data "Live PV" field is now an
editable combo box (`_NoScrollComboBox`) populated from this list: picking a
device by name fills in its full PV (`prefix + PVA suffix`); typing any other
PV by hand still works unchanged, with the same example placeholder text.
**Files:** `constants.py`, `prefs_dialog.py`, `widgets.py`,
`tests/test_live_stream.py`.
**Roll back:** `git revert aa82cb6` (Devices tab and the Live PV dropdown both
go; the Live PV field reverts to a plain text box).

### `c156c62` — Fix image-view autorange drift; bound pan/zoom to image (2026-07-24)
**Effect:** Two bugs in the shared `ImageViewer` (Data Viewer / Mask Builder /
Calibrate all use it): (1) the crosshair `InfiniteLine`s were added to the
pyqtgraph `ViewBox` without `ignoreBounds=True`, so their position — which
follows the mouse on every hover event — fed the auto-range bounds
calculation. Once the bottom-left **A** (auto-range) button enabled
*continuous* auto-range, the view kept re-fitting itself to wherever the
cursor was, and only a right-click ▸ View All (which disables continuous
auto-range as a side effect) made it static again. Fixed by excluding the
crosshairs from bounds via `ignoreBounds=True`. (2) Pan/zoom had no limits,
so scroll/drag could wander arbitrarily far from the image or zoom out
indefinitely into empty space. Added `ViewBox.setLimits()` (computed from the
image shape in `set_image()`): pan is bounded to roughly half an
image-width/height of margin past each edge, and min/max zoom range are tied
to the image size.
**Files:** `widgets.py` (`ImageViewer.set_image`, new `_apply_view_limits`).
**Roll back:** `git revert c156c62` (crosshair can drift the view again under
continuous auto-range; pan/zoom become unbounded again).

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
