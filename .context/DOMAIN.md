# DOMAIN

_On-demand: domain knowledge, terminology, MIDAS API specifics, external facts._
_Not auto-loaded. Verify against current code before relying on file:line specifics._

## MIDAS

A suite of X-ray/synchrotron powder-diffraction analysis packages. This repo is
the PyQt5 desktop GUI that orchestrates the backends. Backends are **separate
installs, never vendored, no `sys.path` hacks** (except the vendored `midas_pdf`
— see DECISIONS). Scope: `midas_calibrate_v2` (23 notebooks) +
`midas_integrate_v2` (35 notebooks) + `midas_hkls` (hkl generation, 230 space
groups) + `midas_distortion` + `midas_calibrate` (v1).

## Core conventions

- **Image shape everywhere: `(NrPixelsZ, NrPixelsY)` = (height, width)**,
  Z-outer / Y-inner. tifffile loads already in this shape.
- **`IntegrationSpec.RhoD` MUST be in µm** (distortion normalization radius =
  corner_px × px_mean). Getting this wrong (pixels) makes ρ ~150× too large and
  the distortion polynomial explodes.
- Axis conversions: `two_theta = degrees(arctan(r_px·pxY / Lsd))`;
  `Q = 4π·sin(2θ/2)/λ`; `d = 2π/Q`. Validated to sub-milli-unit.
- **Set `KMP_DUPLICATE_LIB_OK=TRUE`** at startup (OpenMP duplicate-lib conflict).

## Calibration API (`midas_calibrate_v2`)

- `calibrate(...)`: `im_trans` codes 1=flipY, 2=flipZ, 3=transpose; calibrants
  `"CeO2"|"LaB6"|"Si"|"Al2O3"` or dict `{'a','sg',...}`; `initial_Lsd` default
  1,000,000 µm; `n_iter=4` EM iterations; `refine_tilts` / `refine_distortion`.
  Returns `AutoCalibrationResult`.
- **`initial_BC_y/z` must be passed WITH a valid `initial_Lsd` or not at all.**
  Providing BC bypasses the auto-seeder; without a correct Lsd it falls back to
  1e6 µm and crashes `run_estep_v1`. GUI requires both "Use BC seed" and "Use
  Lsd seed" checked together.
- **`make_seed` always `use_diplib=False`** — diplib segfaults on macOS.
- `AutoCalibrationResult` attrs: `Lsd`(µm), `BC_y/BC_z`(px), `tx/ty/tz`(deg),
  `pxY/pxZ`(µm), `NrPixelsY/Z`, `wavelength_A`(Å), `distortion` dict
  (iso_R2/R4/R6, a1–a6, phi1–phi6), `post_residual_strain_uE`,
  `in_loop_strain_uE`, `residual_corr_bin_path`, `iter_history`, seed_* fields.
- Pipelines beyond one-shot: `first_time_calibrate()`, `autocalibrate_four_stage`,
  `autocalibrate_multi()` (breaks Lsd/pxY degeneracy), `autocalibrate_joint()`,
  Bayesian `fisher_at_map()`→Laplace (per-param σ).
- **Tilt caveat:** `one_shot` and `bayesian` can report a large spurious tilt
  (~−3°) on weakly-tilted data (tilt↔Lsd↔BC degeneracy); it self-compensates so
  Lsd/BC stay trustworthy but the reported tilt isn't. `four_stage`, `joint`,
  `first_time` recover near-zero tilt correctly → those are recommended.
- **Multi-panel:** `calibrate()` runs per-panel shift refinement internally but
  drops `panel_delta_yz`/`panel_delta_theta` before returning. GUI routes
  one_shot+panel_layout through `autocalibrate_four_stage` to expose
  `stage2.unpacked`, attaches as `result._panel_unpacked`, writes
  `panel_shifts.txt` (+ `PanelShiftsFile` line in paramstest).
- Distortion **v2↔v1 name map** (for `midas_calibrate.params.CalibrationParams`):
  iso_R2→p2, iso_R4→p5, iso_R6→p4, a1→p7, phi1→p8, a2→p0, phi2→p6, a3→p9,
  phi3→p10, a4→p1, phi4→p3, a5→p11, phi5→p12, a6→p13, phi6→p14.
- `ff_paramstest_from_auto_result(result, template, out)` injects geometry into
  an existing paramstest verbatim (v2-native distortion names).
- `read_geometry(path)` parses 3 formats: MIDAS paramstest, pyFAI `.poni` (SI
  m→µm; BC_z=Poni1/pixel1, BC_y=Poni2/pixel2, tilts ignored), calibration `.json`.
- **`ImTransOpt` is also a persisted paramstest **file** key** (repeatable —
  one `ImTransOpt <code>` line per op, applied in file order), not just the
  in-memory `im_trans` API arg above. `midas_gui/helpers.py`:
  `parse_im_trans(text)` reads it (0s dropped as an explicit no-op),
  `im_trans_codes_from_checkboxes(...)` builds the ordered list from the 3
  Flip Y/Flip Z/Transpose checkboxes now on the Data Viewer, Mask, and
  Calibrate tabs, and `write_standalone_paramstest()` appends the lines from
  `getattr(result, "im_trans", None)` after the main file write (same
  append-after-write pattern as `PanelShiftsFile`). `read_geometry()` and
  `geometry_fields_from_file()` both return it as `im_trans` (list, `[]` if
  absent) for all 3 supported formats (`.poni` always `[]` — no pyFAI
  equivalent). GUI checkboxes only support the fixed flips-then-transpose
  composition order, so a file with a non-canonical order (e.g.
  transpose-then-flip) loads with a logged note rather than exactly.
  Returns `{wavelength_A, Lsd_um, px_um, BC_y, BC_z}`.

## Integration API (`midas_integrate_v2`)

- **Always build the spec via `spec_from_calibration_result()`** — never a manual
  `IntegrationParams` + `spec_from_v1_params()` (wrong RhoD units, see above).
- Kernels: hard (fastest), **subpixel K=2 (recommended default)**, polygon
  (most accurate/slowest). `SubpixelBinGeometry.from_spec(spec, K=2, mask=...)`;
  mask is 2D (NZ,NY), 1=bad / 0=good, or None.
- Streaming sources: `TIFFGlobSource`, `HDF5FrameSource`, `GEBinaryFrameSource`;
  `integrate_stream(spec, source, mode=, K=, ...)`.
- **Q-uniform output = R→Q rebinning in the GUI, NOT the kernels.** Setting
  `spec.QMin/QMax/QBinSize` flips `q_mode_active` but `*BinGeometry.from_spec`
  still builds R-uniform edges → rings at wrong Q. GUI integrates R-uniform
  (fine RBinSize), converts R→Q, `np.interp` onto the requested uniform-Q grid.
- **Corrections cake is summed/unnormalized** per (η,R) bin (a flat field
  becomes a ramp rising with R), unlike plain kernels which return the mean.
  GUI divides by a per-bin pixel-count cake (`integrate_with_corrections(ones)`)
  before the η-mean, else every correction-enabled profile has a spurious rising
  radial background.
- Corrections & variance are **mutually exclusive** in batch (correction path
  has no σ → √I fallback).
- Output formats: CSV (R px, I, σ), XYE (2θ deg — Rietveld GSAS-II/FullProf),
  FXYE (2θ centideg — GSAS legacy), DAT (Q Å⁻¹, I, σ — PDF/PDFgetX3/diffpy-CMI),
  ESG, H5 (full provenance), 2D CSV (cake for texture).

## Corrections physics (validated correct)

- Polarization `1 − PF·sin²2θ·cos²(η−plane)`; solid-angle ∝ 1/cos³(2θ) (flat
  detector); absorption T(2θ)∈(0,1]; Compton rises with Q. Cylindrical
  absorption switches thin↔quadrature at **μR=1.5** → small harmless
  non-monotonic T discontinuity.
- Variance: η-combination `σ_1d = √(Σσ²)/N` is sound; σ ∝ 1/√(pixels/bin).
- Dead pixels are caught by the **threshold mask**, not the statistical dead-gate
  (dead-gate recall ~0.28 in low-background regions — expected, fine).

## Calibrants, quality targets, sentinels

- Calibrant DB: CeO2 a=5.4116 sg225 (most common, no ring overlap); LaB6
  a=4.1569 sg221 (some overlap); Si a=5.4310 sg227 (low absorption); Al2O3
  a=4.7589 c=12.992 sg167 (non-cubic R-3c).
- Ring radii from result: `Lsd·tan(2θ)/pxY`, 2θ via
  `midas_hkls.generate_hkls(SpaceGroup.from_number(sg), Lattice(...),
  wavelength_A, two_theta_max_deg)`.
- Quality targets: post_residual_strain <30µε good / <100 ok / >200 check
  distortion; in_loop_strain <100 good; bad-pixel fraction <1% good / >10%
  check threshold; ring-marker alignment >1px off ⇒ re-calibrate.
- Dead-pixel sentinels: uint16→65535, uint32→4294967294 (Eiger dead),
  int16→32767, int32→2147483647; Pilatus 20-bit overflow = 1048575.
- GE binary: `np.fromfile(dtype=uint16, offset=8192)`, side candidates
  2048/4096/1024/512.

## PDF (Pair Distribution Function)

Real-space G(r) from total scattering. GUI uses the **Faber-Ziman polyatomic**
(composition-aware) formalism via the vendored `midas_pdf` backend, not the old
monoatomic F(Q) path. Pipeline: composition + I(Q) → S(Q) → G(r), with ±1σ
band, Compton toggle, `refine_normalization` (scale/background); forms G/g/T/R +
F(Q) family. First-shell sanity: Ni ≈ 2.50 Å.
- **Detailed build-critical reference:** `.context/reference/midas_pdf/`
  (README + 00–05; start with `01_core_api.md`, `05_gui_integration.md`). This
  is the knowledge stack for PDF Stage 2–3.
- `midas_pdf` package source (not in repo):
  `/Users/dbeniwal/ANL-research/midas_pdf_src/`.

## Test data

Synthetic Eiger2 500K (1028×512, 75 µm px), λ=0.39 Å, Lsd≈121 mm, BC=(10,10);
CeO2 calibrant + 10-frame Ni scan expanding +0.1%/frame, rendered with the same
`simulate_rings` the GUI overlays use (so overlays line up exactly). Regen via
`test_data/make_test_data.py`. Lives in repo-root `test_data/` (git-ignored,
~39MB) and `tests/test_data/`; all default-load code guarded by `Path.exists()`
(may be absent on a fresh clone). Older canonical CeO2 set:
CeO2 λ=0.42459Å (29.2keV), Lsd=286240µm, px=172µm (Eiger2-500k), shape
(512,1028) uint32.

## GE detector data: HDF5 lossless-compression benchmark (2026-08-27)

Measured (not extrapolated) on `test_data/s1ide/park_may26/ge1/
hydra_orientation_scan_002029.ge1.h5` (`exchange/data`, uint16, 2048×2048,
100% nonzero, bg~1300-1800 counts, max 16349 — dense/noisy GE frames, so
ratios cap ~2-3x, not the 10x+ seen on sparse/masked data). Per-frame HDF5
chunking (`chunks=(1,2048,2048)`) so each frame compresses independently —
confirmed linear scaling by writing a real tiled 1440-frame file (not just
math): 12.08 GB raw → **4.81 GB** with blosc+zstd c5 bitshuffle, 77.4s write
(53.7 ms/frame), 13.8s read (9.6 ms/frame). Raw = 8.39 MB/frame.

Best options (all via `hdf5plugin`; native gzip/lzf also tested but dominated):
- **`bzip2`**: 2.71 MB/frame (3.09x) — best ratio, but decompress is
  ~150 ms/frame (15-30x slower than the others) — avoid for interactive
  per-frame viewing.
- **`hdf5plugin.Blosc(cname='zstd', clevel=5, shuffle=Blosc.BITSHUFFLE)`
  — recommended default**: 3.34 MB/frame (2.51x), ~50 ms/frame write,
  ~9 ms/frame read. clevel=9 only gains ~5% ratio for ~18x write time.
- Fastest with real compression: `Blosc(cname='lz4')` or direct
  `hdf5plugin.Zstd(clevel=1)` — sub-20ms write, single-digit ms read,
  ~1.8-2.1x ratio.
- Bitshuffle alone (no entropy coder behind it) gives ~1.00x — useless on
  this 16-bit data without a compressor.
- Full benchmark script + all filters tried: see chat history 2026-08-27;
  not committed to repo (was an ad hoc exploratory analysis, not a code
  change).

## Terminology quick-ref

Q — scattering vector (Å⁻¹). 2θ — diffraction angle. η — azimuthal angle.
S(Q) — structure factor. F(Q) — reduced structure factor. G(r) — reduced PDF.
Faber-Ziman — partial-structure-factor weighting for multi-element samples.
Lsd — sample-to-detector distance. BC — beam center.
