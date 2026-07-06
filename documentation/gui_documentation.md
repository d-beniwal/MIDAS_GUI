# MIDAS GUI — User Documentation

**Version:** 1.0.0
**Application:** `midas-gui` (or `python -m midas_gui`)
**Backends:** `midas_calibrate_v2`, `midas_integrate_v2`, `midas_calibrate`, `midas_hkls`, `midas_distortion`
**Last updated:** 2026-07-06

> **Maintenance:** keep this document in sync with the code — whenever the workflow
> or a tab's controls change, update the relevant section here in the same change.

> **Tab status:** Tabs **0–4** (Data Viewer, Mask Builder, Calibrate, Calib.
> Refinement, Batch Integrate) are **verified** and ready to use. Tabs **5–8**
> (Corrections & Physics, PDF Analysis, Texture, Results & Export) are a **work in
> progress** and will be updated in the coming weeks.

---

## Table of Contents

1. [Overview and Architecture](#1-overview-and-architecture)
2. [Getting Started](#2-getting-started)
3. [Tab 0 — Data Viewer](#3-tab-0--data-viewer)
4. [Tab 1 — Mask Builder](#4-tab-1--mask-builder)
5. [Tab 2 — Calibrate](#5-tab-2--calibrate)
6. [Tab 3 — Calibration Refinement](#6-tab-3--calibration-refinement)
7. [Tab 4 — Batch Integrate](#7-tab-4--batch-integrate)
8. [Tab 5 — Corrections & Physics](#8-tab-5--corrections--physics)
9. [Tab 6 — PDF Analysis](#9-tab-6--pdf-analysis)
10. [Tab 7 — Texture / Pole Figure](#10-tab-7--texture--pole-figure)
11. [Tab 8 — Results & Export](#11-tab-8--results--export)
12. [Common UI Conventions](#12-common-ui-conventions)
13. [Packaging, Deployment & Diagnostics](#13-packaging-deployment--diagnostics)

---

## 1. Overview and Architecture

The MIDAS GUI is a 9-tab PyQt5 desktop application that exposes the scientific
capability of the `midas_calibrate_v2` and `midas_integrate_v2` packages through a
structured workflow. The intended order of use is:

```
Data Viewer (inspect) → Mask Builder → Calibrate → [Refine] → Batch Integrate
                                                              → Corrections preview
                                                              → PDF Analysis
                                                              → Texture / Pole Figure
                                                              → Results & Export
```

**Cross-tab shared state.** When Tab 2 (Calibrate) produces a result it is
automatically propagated to all downstream tabs. When Tab 1 (Mask Builder) computes
a mask it is sent to the consuming tabs. Tab 3 (Refinement), if applied, re-broadcasts
the refined geometry. No manual copying is needed.

**All heavy computation runs off the GUI thread.** Every operation that touches a
MIDAS package (calibration, integration, mask training, PDF transform, gain training,
field averaging, drift fitting) runs in a background `QThread` worker; the GUI stays
responsive and log lines stream in real time.

**Package layout.**

```
midas_gui/
├── app.py            MainWindow, dark theme, crash diagnostics, main()
├── __main__.py       enables `python -m midas_gui`
├── style.py          Dioptas-inspired QSS theme + layout helpers
├── constants.py      calibrants, colormaps, dtype sentinels, default paths
├── helpers.py        image IO, geometry parsing, spec building, no-scroll widgets
├── widgets.py        ImageViewer / PickableImageViewer / ProfileViewer / FieldSelector / …
├── workers.py        all QThread workers + the integration core
├── calib.py          calibration-pipeline dispatch
├── dialogs.py        save dialogs
└── tab_*.py          one module per tab
```

**Layout pattern.** Every tab has a fixed-width scrollable left control panel and a
right display area (image viewer, plots, and/or a log panel).

---

## 2. Getting Started

`midas-gui` is **not on PyPI** — install it from source with conda.

```bash
git clone https://github.com/d-beniwal/MIDAS_GUI.git
cd MIDAS_GUI
conda env create -f environment.yml    # creates the 'midas-gui' env and installs the GUI
conda activate midas-gui
midas-gui                              # launch (equivalently: python -m midas_gui)
```

The GUI drives the MIDAS analysis backends (`midas-calibrate-v2`, `midas-integrate-v2`,
`midas-calibrate`, `midas-hkls`, `midas-distortion`), which are also not on PyPI —
point the `pip:` section of `environment.yml` at your MIDAS source before creating the
environment (see the comments in that file).

### Test data
Synthetic test data lives in the repo-root `test_data/` folder (git-ignored):
- `calibrant_ceria.tif` / `.h5` — CeO₂ calibrant (Eiger2 500K, 1028×512, 75 µm pixels)
- `nickel_tifs/` — 10-frame Ni scan, lattice expanding +0.1 %/frame
- `nickel_stack.h5` — the same scan as one HDF5 stack (`exchange/data`)

Default geometry: λ = 0.39 Å, Lsd = 121 mm, pixel = 75 µm, BC = (10, 10) px. Every
tab's default file paths point at this data and **auto-load on startup when present**,
so the app is usable immediately after checkout.

---

## 3. Tab 0 — Data Viewer

**Purpose:** inspect detector frames and do a quick geometry-free radial integration.
Produces no shared state for other tabs.

### Data card
| Field | Description |
|---|---|
| Path field | File, folder, or glob (e.g. `frames/*.tif`). Selecting via browse — or pressing Enter — **loads immediately** (no separate Load button). |
| `…` button | Browse for a file. |
| Browse folder… | Browse for a folder of frames (loads immediately). |
| Dataset (HDF5) | Auto-populated dropdown of every ≥2-D dataset with its shape; changing it reloads. |

### Frame navigator
Appears for a multi-frame stack — slider, spin box, or ◀/▶. **Zoom/pan is preserved
as you step through frames** (the view only auto-frames on a fresh load).

### Projection card
Collapse a stack to one image: **Max** (hot-pixel hunting), **Sum** (long-exposure
equivalent), or **Average** (noise reduction), along a chosen axis (0 = across frames).

### Calibration card (optional)
Load geometry from a **calibration `.json`, a MIDAS `paramstest.txt`, or a pyFAI
`.poni`** (auto-detected). It fills BC, Lsd, pixel size and wavelength, unchecks
"Beam centre = image centre", and refreshes the overlay and radial plot.

### Intensity range card (radial integration)
| Field | Description |
|---|---|
| Exclude out-of-range pixels | When on, pixels ≤ min or > max are drawn as a red overlay and excluded from the radial integration (removes gaps / hot / overflow). |
| pixel ≤ / pixel > | Lower / upper bounds. On load, the upper bound auto-fills to **max(99.99th percentile, 100000)**. |

### Ring simulation card
Overlays simulated Debye-Scherrer rings to check geometry.
- **Material** dropdown (CeO₂, LaB₆, Si, Al₂O₃, Cu, Ni, FCC-γFe, BCC-αFe, Au, Ag, Pt,
  W, Ti) or **Custom**.
- Lattice entry is compact: **a, b, c** on one row and **α, β, γ** on the next, plus
  **SG #**. A **Cubic (a=b=c, α=β=γ=90°)** checkbox lets you enter only `a` for cubic
  crystals (b, c mirror a; angles fixed at 90°). Lattice fields are editable only for
  Custom.
- Geometry: λ, max 2θ, Lsd, pixel size, and beam centre (auto = image centre, or
  manual BC_y/BC_z).
- **Show rings / Labels** toggles; **Simulate rings** draws the overlay + hkl table.

### Beam-centre picking (on the image)
The image viewer has **Pick BC** (single click sets the beam centre) and **Pick Ring**
(click ≥3 points on a ring; a circle fit estimates the beam centre). Either updates
BC_y/BC_z and re-runs the overlay + radial integration.

### Radial integration plot (bottom-right)
Below the image is a live **azimuthal average about the beam centre** (`R bin`,
`Integrate`, and an `Auto` toggle that recomputes on frame/BC/mask change). It is a
simple geometry-free radial average (flat detector, no tilt/distortion). **Clicking a
radius on the plot draws the matching ring (magenta) on the image.** Axis units switch
between R (px) / 2θ / Q.

---

## 4. Tab 1 — Mask Builder

**Purpose:** identify and exclude bad pixels. The final mask is a uint8 array
(1 = bad) broadcast to Tabs 2, 3, 4, 5. Browsing an image or a mask loads it
immediately.

### Section 1 · Threshold mask (always applied)
`pixel ≤ lower | pixel > upper`. The upper bound auto-fills from the data type on load
(e.g. 1,048,575 for uint20 Eiger, 4,294,967,295 for uint32).

### Section 2 · Statistical auto-mask
Robust spatial-outlier detection (5×5 median residual → 15×15 local MAD → Z-score →
hot/dead/saturated gates). Uses a **temporal median** over a stack folder when
provided (much cleaner reference). Controls: `K_σ` (6.0), `Hot` (1.5), `Dead` (0.5),
`Frozen` temporal-constancy fraction (0.05, needs ≥2 frames), stack folder + stride.

### Section 3 · Spatial spike rejection
Laplacian high-pass single-pixel-spike detector; `n_σ` threshold (5.0).

### Section 3b · Cosmic-ray rejection (temporal)
Per-pixel temporal σ-clip across a ≥3-frame stack; anomalies OR'd across frames.
`n_σ` (5.0).

### Section 4 · Calibration-based masks (need a Tab 2 result)
- **Azimuthal σ-clip** — flags (R, η) cells deviating from the azimuthal mean.
- **Learnable mask** — differentiable per-pixel mask trained against an η-uniformity
  loss (`steps`, `lr`, `sparsity`).

### Draw mask tools
Interactive rectangle / oval / circle / polygon / annulus / point tools, live-draggable;
**Apply shapes → mask** rasterises them (OR'd with the computed mask). **Clear shapes**
removes them.

### Save / Load
Save the combined mask as TIFF (0 = good, 1 = bad); loading a TIFF applies immediately.

---

## 5. Tab 2 — Calibrate

**Purpose:** determine detector geometry (Lsd, BC, tilts, distortion, wavelength) from
a calibrant pattern.

### Files
Load a TIFF/HDF5 calibrant (browse or Enter loads immediately; the HDF5 dataset field
and Frame index reload on change). A mask file can be loaded here too.

### Dark / Bright / Background
A card with three **field selectors**. Each builds one 2-D field from a **single file,
a folder/glob, or an HDF5 dataset** (with a container dropdown), averaged over a chosen
**index range** (clamped to the frames available). The group is compact — each field's
body is hidden until its checkbox is ticked. The **Bright** field has a mode:
**Flat-field divide** or **Subtract**. Correction order: `(img − dark)` → bright
divide/subtract → `− background` → clip ≥ 0.

### Pipeline selector
One-shot (default) · First-time · Four-stage · Bayesian (Laplace σ) · Joint-cake.
*For trustworthy tilt/strain, prefer Four-stage or First-time — One-shot/Bayesian can
report a spurious self-compensated tilt on weakly-tilted data.*

### Refine flags
Which parameters vary: Lsd, BC, ty, tz, tx, Wavelength, Distortion (15 coeffs),
plus a "Residual map" build toggle. Advanced (E-M / LM iters, device, output dir) and
Multi-panel detector groups are collapsible.

### Live threshold slider
Zeroes calibration-image pixels below the slider value (background suppression for
ring-finding); the preview updates instantly.

### Pick BC / Pick Ring
Click the image to seed the beam centre (single click) or fit a ring (≥3 clicks).

### Run / Abort
**Run Calibration** launches the worker; **Abort** terminates it and frees the slot so
you can immediately start a new run (the calibration is one uninterruptible library
call, so abort hard-terminates the worker thread rather than waiting).

### Results (right panel, bottom tabs)
Radial Profile (with ring markers and an **Azim. avg** selector — see Tab 4), Ring
Residuals bar chart, Results (Lsd/BC/strain + distortion table), and Log. Integration
runs automatically after calibration.

### Export
**Save calibration.json** and **Save paramstest.txt** (standalone or from a template).

---

## 6. Tab 3 — Calibration Refinement

**Purpose:** optional post-calibration geometry refinement against an **η-uniformity**
criterion (rings should be azimuthally uniform). Uses a **derivative-free Nelder-Mead**
optimiser (the differentiable integrator returns NaN geometry gradients at the
beam-centre singularity on this build).

Controls: calibration source (Tab 2 result or JSON), sample frame (auto-loads on
browse), parameters to refine (BC_y, BC_z, Lsd, ty, tz), max iterations (500),
tolerance (1e-6). **Run Refinement** shows a live loss curve; **Apply** broadcasts the
refined geometry downstream. If Apply is never clicked, the Tab 2 result flows through
unchanged.

---

## 7. Tab 4 — Batch Integrate

**Purpose:** integrate a stack of frames into 1-D profiles using the calibrated
geometry and optional corrections.

### Calibration source
- **From Tab 2** — the calibration (or refined) result.
- **From file** — a **calibration `.json`, MIDAS `paramstest.txt`, or pyFAI `.poni`**
  (auto-detected; both GUI and MIDAS-pipeline json key styles supported). Entering a
  path auto-selects this option.

### Data files
TIFF folder/glob or HDF5 (with dataset). **Streaming controls**: start / end (0 = all)
/ stride.

### Dark / Bright / Background
Same three field selectors as the Calibrate tab (file/folder/HDF5, index-range average,
bright divide or subtract), applied per frame.

### Mask
Load a mask TIFF (browse loads immediately) or use the Tab 1 mask.

### Integration
| Field | Description |
|---|---|
| Kernel | Hard (fastest) · Subpixel K=2 · Subpixel K=4 · Polygon (exact). |
| R bin (px) / η bin (°) | Radial and azimuthal bin sizes. |
| **Azim. avg** | How the (η, R) cake becomes a 1-D profile: **Pixel-weighted** (default) `Σ(mean·count)/Σ(count)` — independent of η-bin size and robust to partial azimuthal coverage / **off-detector beam centres**; or **η-bin mean (legacy)** — the unweighted mean of per-η-bin means, which can distort with a coarse η bin when the beam centre is off the detector. |
| Per-bin variance (σ) | Error model poisson / azimuthal / hybrid (ignored when corrections are on → σ = √I). |
| Q-uniform bins | Integrate in R then rebin onto a uniform-Q grid (Qmin, Qmax, ΔQ). |

### Physics corrections
Polarization and solid-angle (pixel-domain, via `integrate_with_corrections`).

### Monitor normalisation
Divide each processed frame's profile/σ by a per-frame scalar from a text file (one
value per *processed* frame).

### Drift correction (long scans)
Per-frame geometry interpolated from an anchor JSON (`{frame_idx: {Lsd, BC_y, BC_z}}`)
with spline/linear/constant parametrization; **Fit trajectory** builds it. Each frame
is then integrated with its own geometry.

### Run / Abort
**Start Integration** / **Abort**. Abort first asks the worker to stop cleanly between
frames (keeping frames already written); if it does not stop promptly it force-terminates
and frees the slot. Completed frames' output files are preserved.

### Output formats
CSV (R,I,σ) · XYE (2θ) · FXYE (centideg) · DAT (Q) · HDF5 (full stack) · 2D-CSV (η×R
cake). Right panel: live **Waterfall** and **Stacked profiles**.

---

## 8. Tab 5 — Corrections & Physics  *(work in progress)*

Preview physics corrections on a single frame (auto-loads on browse). Pixel-domain:
polarization, solid angle, empty subtraction. Profile-domain: cylindrical absorption
(μR), Compton subtraction (composition:fraction). **Compute corrected profile** overlays
corrected vs uncorrected and shows the correction factor vs 2θ.

Also hosts **per-pixel gain training (LearnableGain)**: from a clean reference frame and
a drifted frame, learn a spatial gain map `g_i = 1 + scale·r_i` by minimising
`MSE(profile) + unity·Σ(g−1)² + smooth·TV(g)`; save as NPZ and apply with
`corrected = raw / gain_map`.

---

## 9. Tab 6 — PDF Analysis  *(work in progress)*

Compute G(r) from one calibrated frame: image → I(Q) + background → F(Q) → G(r).
Controls: Qmin/Qmax/ΔQ, rmin/rmax/Δr, window (Lorch/none), binning (hard/polygon).
Right panel: I(Q)+background, F(Q) = Q·(I/bg−1), and G(r). **Save G(r)** writes a
three-column `r, G(r), σ` file (diffpy-CMI / PDFgui / RMCProfile compatible).

---

## 10. Tab 7 — Texture / Pole Figure  *(work in progress)*

Per-ring azimuthal analysis for preferred orientation. Controls: calibration source,
sample frame, R/η bins, ring index, χ (tilt) / φ (rotation). **Compute Pole Figure**
integrates to a cake and extracts I(η) at the selected ring; the right panel shows the
stereographic pole figure and the raw I(η). **Save pole figure (.pol)** exports POPLA
format.

---

## 11. Tab 8 — Results & Export  *(work in progress)*

Session summary + one-click export. Checkboxes select which products (calibration.json,
paramstest.txt, mask.tif, integrated profiles, G(r), pole figures, session log) to copy
to an output directory. A provenance block (package versions, geometry hash, mask
fraction, correction flags) can be copied to the clipboard for a Methods section.

---

## 12. Common UI Conventions

- **Browse = load.** Selecting a file/folder (or pressing Enter in a path field) loads
  it immediately; there are no separate "Load" buttons. HDF5 dataset / frame-index
  changes reload automatically.
- **No accidental scroll changes.** Spin boxes and drop-downs ignore the mouse wheel —
  values change only by clicking/typing; the wheel scrolls the panel instead.
- **Readable right-click menus.** pyqtgraph plot context menus use the dark theme.
- **Dark theme, orange accent** (Dioptas-inspired); off-white text, light input fields.

---

## 13. Packaging, Deployment & Diagnostics

**Packaging.** `midas-gui` is a MIDAS-style package: `pyproject.toml` (BSD-3-Clause),
a `tests/` smoke suite, and `release.sh` for cutting versioned releases (see
`RELEASING.md`). Once published it can join the `midas-suite` meta-package as an
optional `gui` extra (`pip install midas-suite[gui]`).

**Deployment on another system.** Clone the repo, create the conda environment
(`environment.yml`), and run `midas-gui`. The MIDAS analysis backends are installed via
the `pip:` section of `environment.yml` (editable from a local MIDAS checkout, from a
git subdirectory, or from a private index). `midas_gui/_paths.py` is an inert stub in an
installed package (no `sys.path` manipulation).

**Crash diagnostics.** On startup the app installs a logging exception hook and
`faulthandler`; any uncaught Python exception or native fault is written to
`~/midas_gui_error.log` and shown in a dialog. Installing the hook also prevents PyQt5
from hard-aborting on an exception inside a slot. Each tab is built in isolation — a tab
that fails becomes an error placeholder instead of taking the window down. If the app
ever "pops up and dies" (typically on Windows), send that log file.

---

*For bugs or questions, see the MIDAS GUI repository.*
