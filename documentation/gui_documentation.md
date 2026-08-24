# MIDAS GUI — User Documentation

**Version:** 1.0.0
**Application:** `midas-gui` (or `python -m midas_gui`)
**Backends:** `midas_calibrate_v2`, `midas_integrate_v2`, `midas_calibrate`, `midas_hkls`, `midas_distortion`
**Last updated:** 2026-08-24 (Tab 4 — Batch Integrate: split into **Single
detector** / **Hydra** modes behind a leftmost mode ribbon, the same pattern
as the Calibrate/Data Viewer tabs' own splits. Hydra mode integrates each of
the 4 GE panels with its own independently fitted geometry — auto-populated
from the Calibrate tab's Hydra fits as each panel finishes, or loadable from
a file per panel — using one shared Integration/Corrections/Monitor
-normalisation/Output recipe, a **Sequential** or **Parallel** run-mode
choice, independent per-panel masks, and per-panel Waterfall/Stacked
-profile output. See §7 "Hydra mode (4-panel GE detector)".)

**Previously:** (2026-08-24, Tab 2 — Calibrate: split into **Single
detector** / **Hydra** modes behind a leftmost mode ribbon, the same pattern
as the Data Viewer tab's own split. Hydra mode fits each of the 4 GE panels'
own geometry from one calibrant dataset using a shared pipeline/refine
recipe, with independent per-panel Transforms/seed/results, a **Sequential**
or **Parallel** run-mode choice, and a **← Data Viewer** import of the
Hydra Data Viewer page's loaded panels + fitted geometry. See §5 "Hydra
mode (4-panel GE detector)".)

**Previously:** (2026-08-24, Tab 0 — Data Viewer: the **Transforms** (Flip
Y/Flip Z/Transpose[/Rotate]) checkboxes are now their own boxed
**Transforms** card, directly below the Ring simulation card, instead of an
inline "Transforms:" label + row inside it — fixes a large visual gap
between the heading and the row; this is shared code, so it applies to both
the single-detector tab's geometry card and every Hydra-tab panel card.
Hydra mode: the **Projection** card moved from the left-side loader panel to
the top of the middle panel, matching where the single-detector tab's own
Projection card sits; and the radial-integration plot's pan/zoom is now
bounded to the combined data range of the currently-visible curves (same
margin formula as the single-detector tab's radial plot), instead of being
free to scroll/zoom arbitrarily far from the data.)

**Previously:** (2026-08-24, Tab 0 — Data Viewer, Hydra mode: added a
per-panel **Projection** card (Max/Sum/Average stack reduction, mirroring
the single-detector tab) that feeds all of GE1-4 *and* the Composite view;
added a per-panel-only **Rotate** field (clockwise, degrees) next to
Flip Y/Flip Z/Transpose on GE1-4 — deliberately excluded from the Composite
view; fixed a bug where Flip Y/Flip Z/Transpose didn't actually refresh the
displayed image on GE1-4 panels; and the Material dialog's **Preset**
dropdown now also fills in the **Name** field (still editable), on both the
single-detector and Hydra tabs.)

**Previously:** (2026-08-24, Tab 0 — Data Viewer, Hydra mode: fixed a
manual (Phase 6) review's 5 bugs — λ/max 2θ/pixel size now shared across
all 5 geometry cards; dark/bright/background correction added (sibling
-aware, mirroring the single-detector tab); the Composite windmill assembly's
panel rotation stays counterclockwise (confirmed correct against real
`test_data/s1ide` data) and now also mirrors the whole canvas about its
vertical axis, which was needed to put ge2/ge4 on their correct sides; a
beam-centre edit now correctly updates the radial profile, not just the
ring overlay; and the image viewer's vmin% auto-level now excludes exact
-zero pixels app-wide, fixing a washed-out Composite view. The intensity
-range exclude mask and Top-N brightest-pixel remain single-detector-only.)

**Previously:** (2026-08-23, Tab 0 — Data Viewer, Hydra mode: the radial
plot now shows all 4 panels' profiles at once (fixed colors, independent
per-panel calibration) plus a toggleable summed **Composite** curve,
replacing the earlier single-curve placeholder. Dark/bright/mask
corrections for Hydra frames are still a planned follow-up.)

**Previously:** (2026-08-23, Tab 0 — Data Viewer: the **Hydra** mode
ribbon entry became functional — 4-panel GE detector loading, ge1-4/
composite image toolbar, independent per-panel beam-centre/ring
calibration, and a geometry-based windmill composite. Single detector mode
unchanged and still the default.)

**Previously:** (2026-08-23, Tab 0 — Data Viewer: added a leftmost **mode
ribbon** (Single detector / Hydra) — Hydra mode was a placeholder at that
point.)

**Previously:** (2026-08-23, All detector-image viewers — Data Viewer, Mask
Builder, Calibrate — now draw pixel `(0, 0)` at the **bottom-left** corner
instead of the top-left, matching MIDAS's convention that the on-screen
image match the physical world view of the detector when looking downstream
from the sample along the beam direction. This is a display-only rendering
change (`pg.ImageView`'s own default `invertY()` is overridden); it is
independent of the **Transforms** checkboxes and the `ImTransOpt` data
transform, which are unchanged. The Data Viewer ROI tool's box annotations
now label the bottom-left corner (was top-left).)

**Previously:** (2026-08-17, Tab 2 — Calibrate: the **Transforms: Flip Y /
Flip Z / Transpose** checkboxes now actually do something — toggling one
live-updates the image preview and Pick BC/Pick Ring clicks land in that same
transformed space, matching Data Viewer/Mask Builder; the calibration run
itself now applies the identical transform to the calibrant image and Dark
right before the pipeline call, fixing a real bug where several pipeline
modes (Four-stage, Bayesian, Joint-cake, panel-layout, partial
distortion-coefficient selection) silently transformed the image internally
while the seed beam centre and detector dimensions stayed untransformed;
**→ Send to Data Viewer** now carries the Transforms state too. Also: the
Average frames card's **skip** (stride) control was removed — **start**/
**end (0=all)** remain.) (2026-08-17, Data Viewer, Mask Builder, and Calibrate tabs all
gain matching **Transforms: Flip Y / Flip Z / Transpose** checkboxes applying
MIDAS's `ImTransOpt` image transform to the raw detector frame before
display/masking/calibration; the Calibrate tab's existing transform controls
now round-trip through saved/loaded `paramstest.txt` and `calibration.json`
files instead of being in-memory-only, and geometry hand-offs between the
three tabs keep the Transforms state in sync.)

**Previously:** (2026-08-16, Tab 2 — Calibrate: two fixes. (1) Picking a
strict subset of distortion coefficients (the "…" dialog) now genuinely
restricts the LM fit to just those coefficients on every pipeline, including
One-shot — which previously silently refined all 15 regardless of the
selection — and the Results tab now lists only the coefficients actually
selected for that run instead of always showing all 15 (saved
paramstest.txt/.json exports are unaffected — they still carry every slot's
real, possibly held-fixed, value). (2) Selecting/changing a Dark, Bright, or
Background field now immediately updates the image preview to the corrected
frame — it previously kept showing the raw, uncorrected image until the next
full data load).

**Previously:** (2026-08-13, Data Viewer ROI tool usability follow-ups: fixed
an OS-level minimize glitch where a popup would pop back open needing a
second click; box ROI `(x, y)`/`w = `/`h = ` annotations are now whole
pixels, not fractional; line ROIs now show their length in pixels at the
line's midpoint, and their "ROI N" label is anchored at the line's actual
start point instead of the image's top-left corner; and the box/line popup
plots plus the Intensity statistics histogram now cap zoom-out/pan at the
current data range, matching the radial-integration plot) (2026-08-13, Data Viewer ROI tool: OS-level window minimize
now tucks a popup into the ribbon (the old custom "–" button is gone); box
ROIs show live `(x, y)` / `w = ` / `h = ` annotations on the image; all
on-image ROI text gets a translucent background and a ~20% larger font;
popup stats/crop images now respect the active bad-pixel mask (excluded from
histograms/line stats, marked bright red on the crop); and the line-ROI drag
preview is a true diagonal line instead of a box) (2026-08-12, Tab 1 — Mask Builder's **Dilation (px)** control
switched from 4-connected to 8-neighbor dilation: N=1 now grows a bad pixel to
the full surrounding 3×3 block, N=2 to the full 5×5 block, etc.)
(2026-08-12, Tab 1 — Mask Builder gains a **5 · Post-processing**
card with a **Dilation (px)** control that grows bad-pixel regions from the
threshold/statistical/loaded mask by N pixels before combining with hand-drawn
shapes) (2026-08-12, Tab 1 — Mask Builder: Stack browse menu gains a
**Files (multi-select)…** entry so the temporal stack can be built from an
explicit, hand-picked set of files instead of only a whole folder/glob) (2026-08-12, Tab 6 — PDF Analysis rebuilt for the full
ROADMAP Stage 2-3 workflow: empty-cell/Paalman-Pings background subtraction,
detector-efficiency correction, absolute normalization, differentiable
multiple scattering, a fluorescence diagnostic, CIF-driven structure
refinement, and Δ-PDF significance testing, backed by a dedicated
`test_data/test_pdf/` dataset) (2026-08-10, Preferences ▸ Profile ships three bundled
beamline device presets — **20-ID-D**, **20-ID-E**, **1-ID-E** — so the Data
Viewer's Live Data PV dropdown can be switched to a beamline's detectors
without hand-editing Preferences ▸ Devices) (2026-08-09, Cross-tab data sharing: every Data Loader
panel's Data browse button, plus Mask Builder's Image/Stack browse buttons,
gain an **Import from…** menu to pull a file/folder/buffer already loaded in
another tab; **Use Buffer** gets a 💾 button to save the buffer to an HDF5
file (`buffer/data`); Data Viewer's **Project stack** button turns green while
a projection is displayed; ROI popups are now always-on-top and can be
minimized to a ribbon on the image viewer's left edge) (2026-08-09, Data Viewer: new B-PILOT auto-start bridge —
a local-socket server lets the separate B-PILOT plan-runner GUI trigger
Live Data on a scan's detector with no clicks in MIDAS GUI; see the Live
Data card section) (2026-08-03, Data Viewer/Calibrate/PDF: the clickable λ
label's popup menu gains an **Energy (keV)** entry box that converts to
wavelength on Enter, ahead of the existing K-edge foil menu; Data Viewer's
Projection card drops the **Axis** field (always 0, i.e. across frames) and
adds an **N frames** field capping how many frames after Skip frames are
included, 0 = all remaining) (2026-08-01, Data Viewer: box-ROI popup's zoomed crop image
is no longer fixed-size — it now resizes along with the popup window) (2026-08-01,
Data Viewer: line ROI is now drawn as a single
arrow shape — shaft and head recomputed together from the two endpoints on
every drag — replacing the separate arrowhead overlay item that could rotate
oddly as the endpoint moved) (2026-08-01,
Data Viewer: image toolbar gains a Box/Line
ROI tool — click-drag draws a shape, opening a small floating, freely-draggable
stats popup next to it, color/label-matched to the shape; box popups show a
linear/log intensity histogram plus a zoomed-in crop of the region, line popups
show a flippable intensity-vs-distance profile; popups stay live-linked to
their shape as it's dragged/resized but not to its screen position; multiple
ROIs supported, session-only, removable via the popup's close button, a
shape's right-click menu, or Clear ROIs (which also resets ROI numbering back
to 1); Pick BC/Pick Ring's shared Clear button now also removes the Pick BC
crosshair marker, not just Pick Ring's points) (2026-07-31,
Data Viewer: pixel-size field now accepts a
second decimal place, needed for near-field detectors' finer pixel pitches;
radial-integration plot gains a "?"
help button explaining the R-bin calculation; ring-width field widened
60->80px; Live Data card gets a "Use Buffer"
last-N-frames ring buffer — yellow while filling, green once streaming pauses,
at which point Projection and the rest of the stack analysis work on it like
a loaded HDF5/folder stack; Load-calibration card renamed/moved below Ring
simulation's Simulate button and now syncs ty/tz on load; Intensity range
card's title bar is now the on/off checkbox; Mask rows — including the
"Tab 1 mask" from the Mask Builder tab — get a checkbox to include/exclude
without deleting, and the Tab 1 mask row always stays at the top of the list;
Live Data "Use Buffer" N field capped at 100 frames to bound memory use;
Mask status line turns amber and names any source dropped for a load
failure or shape mismatch, instead of silently ignoring it; Live Data PV
dropdown gains a built-in **Sim Detector** entry — a hardware-free fake PVA
stream shaped like an Eiger2 500K with 0-60000 counts, for exercising Live
Data without a real beamline connection, see `midas_gui/sim_detector.py`;
image viewer colorbar/histogram now defaults its own zoom to the vmin%/vmax%
percentile window instead of the full data range, converts a manual
level/zoom window between Log and Linear scale on toggle, auto-resets to the
percentile defaults whenever new data is loaded — except frame-to-frame
updates within an active live stream, which keep a manual window fixed — and
always reframes its own visible window tightly around the (converted) levels
on a Log/Linear toggle, instead of carrying a manually zoomed/panned window
through the nonlinear conversion, which could leave the sliders squeezed
into a barely-visible sliver of the window; Live Data "Use Buffer" now
carries over into **Start** instead of being silently reset — if buffering
is already armed when Start is clicked, it re-arms with a fresh buffer for
the new stream rather than turning back off; Ring simulation card's λ/Lsd/pixel
size fields now accept any positive value (no more 1–5000 µm pixel-size cap
etc.) and display λ to 4 decimals, Lsd to 3, pixel size to 2 (needed for
near-field detectors' sub-µm-precision pixel pitches), BC_y/BC_z to 1, and
ty/tz to 2; "Show rings" renamed **Rings** and moved onto the same row as
**Labels** and a shrunk ring-thickness field; Exclude-out-of-range-pixels
controls moved from their own left-panel card into the radial-integration
plot's own toolbar (right end of the `X` / `Log Y` / `R bin` / `Auto` /
`Integrate` row), with the `R bin` field narrowed and the `N bins | max=...`
stats printout removed from that row to make space; Load calibration card
renamed **Load/save calibration**; Exclude-range controls further refined —
moved to sit pinned at the far right of the toolbar row, the `<`/`>` bound
fields widened (56px → 112px) and switched from float to integer spin boxes
(no decimal display), and the lower-bound comparison changed from `<=` to a
strict `<` — a pixel exactly equal to the lower bound is no longer masked)

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
3. [Data Viewer](#3-tab-0--data-viewer)
4. [Mask Builder](#4-tab-1--mask-builder)
5. [Calibrate](#5-tab-2--calibrate)
6. [Calibration Refinement](#6-tab-3--calibration-refinement)
7. [Batch Integrate](#7-tab-4--batch-integrate)
8. [Corrections & Physics](#8-tab-5--corrections--physics)
9. [PDF Analysis](#9-tab-6--pdf-analysis)
10. [Texture / Pole Figure](#10-tab-7--texture--pole-figure)
11. [Pump Probe (time-resolved / TR-XRD)](#11-pump-probe-time-resolved--tr-xrd)
12. [Results & Export](#12-results--export)
13. [Common UI Conventions](#13-common-ui-conventions)
14. [Packaging, Deployment & Diagnostics](#14-packaging-deployment--diagnostics)
15. [Configuration & Defaults](#15-configuration--defaults)
16. [File ▸ Save/Load GUI State](#16-file--saveload-gui-state)

---

## 1. Overview and Architecture

The MIDAS GUI is a modular PyQt5 desktop application (up to 10 tabs) that exposes the
scientific capability of the `midas_calibrate_v2` and `midas_integrate_v2` packages
through a structured workflow. The intended order of use is:

```
Data Viewer (inspect) → Mask Builder → Calibrate → [Refine] → Batch Integrate
                                                              → Corrections preview
                                                              → PDF Analysis
                                                              → Texture / Pole Figure
                                                              → Pump Probe (TR-XRD)
                                                              → Results & Export
```

**Modular tabs.** Data Viewer, Mask Builder, Calibrate and Batch Integrate are always
shown; the remaining tabs (Calibration Refinement, Corrections, PDF Analysis, Texture,
Pump Probe, Results & Export) are optional and can be shown/hidden from **Settings ▸
Preferences ▸ Tabs**. By default only **Calib. Refinement** and **Pump Probe** are
shown; Corrections, PDF Analysis, Texture and Results & Export ship **hidden** — turn
them on when you need them. The choice is saved per-user (`ui.visible_tabs`) and applies
immediately — see §15. Hidden tabs are only removed from the tab bar; they stay
constructed, so cross-tab wiring and state are preserved.

**Cross-tab shared state.** When Tab 2 (Calibrate) produces a result it is
automatically propagated to all downstream tabs. When Tab 1 (Mask Builder) computes
a mask it is sent to the consuming tabs. Tab 3 (Refinement), if applied, re-broadcasts
the refined geometry. No manual copying is needed.

**Cross-tab data import ("Import from…").** Every Data Loader panel's **Data**
browse button (Data Viewer, Calibrate, Calib. Refinement, Batch Integrate, Pump
Probe), plus Mask Builder's **Image** and **Stack** browse buttons, carry an
**Import from…** submenu listing whatever's currently loaded in every *other*
tab — a file/folder path, or, if that tab's Live Data **Use Buffer** ring
buffer is frozen (green), a **Buffer (N frames)** entry. The menu is built
fresh each time it's opened, so it always reflects what's loaded right now.
Picking a path just loads it like a normal browse. Picking a **buffer**:
- In a tab that keeps a live in-memory stack (Data Viewer, Calibrate,
  Refinement), the picking tab **delegates** to the source's buffer directly —
  no copy is made, so it always reflects the source buffer's current
  contents, and if the source buffer is later reset, the delegating tab clears
  itself and shows "Source buffer was reset" rather than showing stale data.
- In a tab that only ever reads from a file/HDF5 path (Batch Integrate, Pump
  Probe, and Mask Builder's Stack field), the buffer is instead **snapshotted
  once** to a temporary HDF5 file (dataset `buffer/data`) and that path is
  loaded — later changes to the source buffer are not reflected until you
  re-pick the entry.

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

**Image orientation.** Every detector-image viewer (Data Viewer, Mask
Builder, Calibrate) draws pixel `(0, 0)` at the **bottom-left** corner,
matching MIDAS's own convention: the on-screen image then matches the
physical world view of the detector when looking downstream from the
sample along the beam direction. This is a display-only convention,
separate from the **Transforms** (Flip Y / Flip Z / Transpose) checkboxes
found in Data Viewer, Mask Builder, and Calibrate — those still flip or
transpose the underlying pixel *data* itself (persisted as MIDAS's
`ImTransOpt` in saved calibration files) to correct a detector's raw
readout orientation, independent of which corner the GUI renders as the
origin.

**Layout pattern.** The four analysis tabs — **0 Data Viewer, 2 Calibrate, 3 Calib.
Refinement, 4 Batch Integrate** — use a **three-panel layout**:

```
[ Data Loader | Parameters | Display / results ]
```

- **Data Loader (left).** A shared `DataLoaderPanel` for selecting the five inputs —
  **Data, Dark, Bright, Background, Mask** — each as a single file, a folder, or an
  HDF5 dataset (a container dropdown appears for HDF5). Frame controls live here too
  (Tab 0 navigator, Tab 2/3 frame index, Tab 4 frame range + stride).
- **Parameters (middle).** The tab's analysis controls.
- **Display / results (right).** Image viewer, plots, and/or a log panel.

The three panels are separated by **draggable splitter handles** — drag the
`Data Loader | Parameters` and `Parameters | Display` boundaries to rebalance widths
to taste. Each panel has a minimum width; drag past it and the panel shows a scrollbar
rather than clipping its controls.

The remaining tabs (1, 5–8) keep the classic two-panel layout.

**Corrections & mask (all four analysis tabs).** Dark is averaged then subtracted;
Bright is averaged then flat-field **divided** or **subtracted** (your choice);
Background is averaged then subtracted; every **checked** Mask source (files/folders plus
the auto-added "Tab 1 mask", populated from the Mask Builder tab) is **unioned** into one
composite mask that zeroes/ignores those pixels. Correction order: `(img − dark)` →
bright → `− background` → clip ≥ 0.

Each Mask row has its own **checkbox** to include/exclude it from the union without
deleting it (the **✕** button still removes the row entirely). The "Tab 1 mask" row is
always kept at the **top** of the list when other mask sources are present.

If a checked mask source fails to load, or its shape doesn't match the other sources
already unioned, it's dropped from the composite and the status line under the mask
list turns amber and names the source and the reason — it is never silently ignored.

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

**Purpose:** inspect detector frames and do a quick radial integration — geometry-free
(circle binning) by default, or a full tilt/distortion-aware integration when a
calibration file is loaded.
Produces no shared state for other tabs.

### Mode ribbon (leftmost strip)
A narrow vertical strip at the very left edge of the tab switches between
**Single detector** (the view described below — unchanged) and **Hydra**
(the 1-ID-E 4-panel GE detector view). The two modes are independent:
switching does not share data or geometry between them.

### Hydra mode (4-panel GE detector)

**Purpose:** inspect and calibrate the 1-ID-E Hydra detector — 4 separate GE
panels arranged in a "windmill" layout around a shared beam axis, each with
its own independent beam-centre/tilt calibration, composited into one
registered image for a full-coverage view.

- **Hydra data (left panel)**: point the path field at **any one** of the 4
  GE panel files (a `.geN.h5`/`.tif` or a `geN/` folder — matching this
  beamline's own naming convention) and the other 3 panels are found
  automatically. Small `ge1 ge2 ge3 ge4` status labels turn green as each
  panel is located (grayed out if not found — the view still works with as
  few as 2 panels present). A frame slider/spinbox navigates a shared frame
  index across all panels (they're synchronized frames of the same scan).
- **Dark / Bright / Background (left panel, below Hydra data)**: same
  correction math as the single-detector tab (dark subtraction, bright
  flat-field divide/subtract, background subtraction). Point each field at
  **any one** panel's dark/bright/background file and the other panels'
  matching files are found automatically, the same way the main Hydra data
  path works — no need to pick all 4 by hand. Each field computes and
  applies independently per panel.
- **Projection (top of the middle panel)**: same **Max / Sum / Average**
  stack-reduction as the single-detector tab's own Projection card (and in
  the same place — top of the middle panel, above the geometry cards), but
  computed **per panel** — clicking **Project stack** reduces every
  currently-found panel's own frame stack (honoring Skip frames/N frames and
  whatever Dark/Bright/Background correction is currently set) and shows the
  result in place of the current frame for **all of GE1-4 and the Composite
  view** (the Composite is rebuilt from the projected frames, not frame 0).
  **Back to frames** — or moving the frame slider/spinbox — returns to
  normal per-frame navigation.
- **Image toolbar**: five buttons — **GE1 / GE2 / GE3 / GE4 / Composite** —
  select what's shown in the image viewer below. GE1-4 show that panel's own
  raw frame; **Composite** shows all currently-available panels remapped
  (via each panel's own beam-centre + tilt) into one shared, registered
  canvas — a geometry-based "windmill" composite, not a raw mosaic.
- **Per-panel geometry card (middle panel, below the Projection card)**:
  switching the image-toolbar button swaps which panel's **Ring simulation +
  Transforms + Load/save calibration** cards are shown — identical in every
  way to the single-detector tab's own cards (materials list, beam-centre
  pick/ring-fit, tilt fields, calibration
  file load/save), just bound to that one panel. Loading a calibration file
  (or picking/editing a beam centre) on a GE1-4 card takes effect
  immediately: it's used for that panel's own ring overlay/radial
  integration, and — since the Composite view depends on every panel's
  geometry — the composite is automatically rebuilt the next time it's
  shown. Panels with no calibration loaded yet use a bundled default
  1-ID-E-style geometry (an example windmill layout, not your
  instrument's real calibration — load a real per-panel file to override).
  The **Composite**'s own geometry card is separate from the 4 panels': its
  beam centre is automatically seeded at the composite canvas's own centre
  the first time a given canvas size is built, so ring simulation/radial
  integration on the Composite view work immediately with no extra setup
  (though it can still be hand-edited like any other card). **λ (wavelength),
  max 2θ, and pixel size are shared across all 5 cards** — editing any one
  of them on GE1-4 or Composite applies it to the other 4 immediately (same
  X-ray beam and GE detector model, so these three are always physically
  identical); beam centre, Lsd, and tilt remain independent per panel.
  GE1-4 (not the Composite) also have a **Rotate** field on the same row as
  **Flip Y / Flip Z / Transpose** in that panel's own **Transforms** card: a
  clockwise rotation (degrees, default 0) applied only to that one panel's
  own raw display/radial-integration image — it is deliberately **not**
  applied to the Composite view, which keeps building from each panel's
  un-rotated frame and its own independent beam-centre/tilt geometry.
- **Radial integration plot (bottom right)**: shows **all 4 panels' own
  azimuthal profiles at once** — GE1-4 in fixed colors, computed
  independently from each panel's own beam centre/geometry — plus a
  toggleable **Composite** curve (white, dashed). Checkboxes above the plot
  show/hide each curve individually. The R-bin/Auto/Integrate controls are
  shared across all 5 views (one setting, not a separate control per
  panel); **Integrate** recomputes every curve immediately regardless of
  Auto. The X-axis unit selector (R/2θ/Q) applies to all curves at once.
  The **Composite** curve is the sum of the 4 panels' own profiles — each
  resampled onto a shared 2θ axis first, then added together — **not** a
  radial integration of the composited image itself (which would
  double-count any panel overlap and mix registration error into the
  profile). **Pan and zoom are bounded** to the combined X/Y extent of every
  currently-visible curve (same margin formula as the single-detector tab's
  own radial plot — see **Pan and zoom are always bounded to the current
  profile's extent** below), recomputed on every curve refresh (X-axis unit
  switch, checkbox toggle, or new data) so scrolling/zooming can't wander
  off into empty space. This plot doesn't yet have the single-detector
  plot's Auto/Manual toggle button pair.
- **Not yet supported in Hydra mode** (present in Single detector mode):
  the intensity-range exclude mask, and Top-N brightest-pixel.

### Data Loader panel (left)
Data, Dark, Bright, Background and Mask are all selected in the shared **Data Loader
panel** (see §1): each accepts a file / folder / HDF5-dataset. The **Data** card here
holds the **frame navigator** (slider, spin box, ◀/▶); **zoom/pan is preserved** as you
step through frames (the view only auto-frames on a fresh load). Dark/bright/background
and the composite mask are applied to the displayed image and to the radial integration.
**Pan and zoom are bounded to the image** (roughly half an image-width of margin on each
side) so scrolling/dragging cannot wander off into empty space or zoom out indefinitely;
the bottom-left **A** (auto-range) button re-fits the image without leaving zoom "stuck"
tracking the mouse.

### Projection card
Collapse a stack to one image: **Max** (hot-pixel hunting), **Sum** (long-exposure
equivalent), or **Average** (noise reduction), always across the stack of frames.
**Skip frames** (default 1) ignores that many leading frames before projecting (e.g. 1
drops the first frame, 4 drops the first four) — useful when the opening frames are
detector warm-up / shutter-transient exposures. **N frames** caps how many frames
(after Skip frames) are included in the projection; **0** (default) uses every
remaining frame in the stack. While a projection is being displayed, **Project
stack** stays highlighted **green** so it's obvious the image on screen is a
projection rather than a live frame; **Back to frames** (or loading new data)
reverts it to its normal styling.

### Exclude-out-of-range-pixels controls (radial-plot toolbar)
These controls used to be their own left-panel card; they now live at the **far
right** of the **radial integration plot's toolbar** (see below), past the `X`,
`Log Y`, `R bin`, `Auto`, `Integrate` controls, so there's no separate card here
anymore.

| Field | Description |
|---|---|
| Exclude range (checkbox) | When on, pixels < min or > max are drawn as a red overlay and excluded from the radial integration (removes gaps / hot / overflow). |
| < / > | Lower / upper bounds, entered as **whole-pixel-count integers** (no decimals). On load, the upper bound auto-fills to **max(99.99th percentile, 100000)**. |

### Ring simulation card
Overlays simulated Debye-Scherrer rings to check geometry. Supports **multiple
materials at once** — e.g. a sample phase overlaid on a calibrant — each with
its own lattice, visibility, and ring color.
- **Material rows**: one row per material, each with a **checkbox** (show/hide
  that material's rings on both the image and the radial-integration plot), a
  **color swatch** button (click to open a color picker; the chosen color
  drives that material's ring lines and hkl labels on both plots), and the
  **material name** as a clickable (underlined) button. A **✕** button deletes
  the row — disabled when only one material remains, since at least one row
  is always kept. **+ Add material** appends a new row (default name
  `Material N`, generic cubic lattice, next color from a 10-color palette
  cycled by row order). A single default **Ni (FCC)** row is present at
  startup, matching the pre-multi-material behavior.
- Clicking a material's name opens a **Material dialog**: an editable **Name**
  field, the **Preset** dropdown (CeO₂, LaB₆, Si, Al₂O₃, Cu, Ni, FCC-γFe,
  BCC-αFe, Au, Ag, Pt, W, Ti, or **Custom**), lattice **a, b, c** (3 decimals)
  on one row and **α, β, γ** (2 decimals) on the next, plus **SG #**, and a
  **Cubic (a=b=c, α=β=γ=90°)** checkbox that lets you enter only `a` for cubic
  crystals (b, c mirror a; angles fixed at 90°). Lattice fields are editable
  only for Custom. Picking a named preset also fills the **Name** field with
  that preset's name (still freely editable afterward — e.g. rename it
  without losing the lattice values just applied). OK applies the
  name/lattice/preset back to that material's row (renaming here updates the
  row's displayed name); Cancel discards edits.
- Geometry (λ, max 2θ, Lsd, pixel size, beam centre) is shared across all
  materials — only the lattice/space-group/color/visibility are per-material.
  Beam centre (auto = image centre, or
  manual BC_y/BC_z). The **λ** label is clickable (underlined) — click it to open a
  menu with an **Energy (keV)** entry box at the top (type a photon energy and press
  Enter, or click **↵**, to convert it to wavelength via λ = 12.398420 / E) followed
  by a menu of common K-edge foils (Pr, Sm, Yb, Lu, Hf, Ta, W, Re, Pt, Au, Pb, Bi)
  that set λ directly to that element's K absorption-edge wavelength. The same
  clickable-λ menu is on the Calibrate and PDF tabs. The **px** label is likewise clickable — a menu
  of common detectors (GE 200 µm, Varex 150 µm, Pilatus 172 µm, Eiger 75 µm) sets
  the pixel size (also on the Calibrate tab, where it sets both pxY and pxZ).
- Every field in this card steps by a fixed amount per up/down-arrow click
  (λ 0.01 Å, max 2θ 1°, Lsd 1 mm, pixel 0.1 µm, BC_y/BC_z 1 px, ty/tz 0.1°) —
  customizable from **Settings ▸ Preferences ▸ Data Viewer**.
- **ty / tz** (detector tilt about the Y/Z axes, degrees) sit right below
  BC_y/BC_z — when non-zero, the simulated rings are forward-projected through
  the tilt geometry instead of drawn as plain circles, so the overlay shows the
  same non-circular ring shape a tilted detector actually produces. Leaving
  both at 0° reproduces the previous plain-circle rendering exactly. (Rotation
  about the beam axis, `tx`, leaves a full ring's shape unchanged, so it isn't
  exposed here.)
- **Rings / Labels** toggles sit on one row together with a compact **thickness**
  spin box (0.5–10 px) that sets the line width of the simulated-ring overlay on
  the image; redraws immediately as you change it.
- **Simulate rings** is a **live toggle** (not a
  one-shot click) — while on, the button turns **green** as a visual cue, and the
  overlay + hkl table recompute automatically whenever material, lattice constants,
  space group, or geometry (λ/Lsd/px/max 2θ) change. Beam-centre and ty/tz edits
  still just reposition the existing rings (their 2θ values don't depend on BC or
  tilt). Rings are drawn out to the full **max 2θ** you set, regardless of how far
  that places them from the beam centre (rings landing off-canvas simply aren't
  visible — they aren't silently dropped at some fixed pixel-radius cutoff).
- **→ Send geometry to Calibrate** copies λ, pixel size, Lsd and beam centre into the
  Calibrate tab's detector + seed fields (the Calibrate tab has a matching
  **← Data Viewer** button that pulls the same values).

### Transforms card
A small standalone card sitting directly below the Ring simulation card,
titled **Transforms** (previously an inline "Transforms:" label + row buried
inside the Ring simulation card, with the label and row visually far apart —
now its own boxed section so the heading sits directly above the checkboxes
it labels). **Flip Y / Flip Z / Transpose** apply MIDAS's `ImTransOpt` image
transform to the raw detector frame — before display, ring overlay, and
radial integration — in that fixed order (flips, then transpose). Use this
when the detector's raw pixel orientation doesn't match the geometry model
(e.g. the beam centre would otherwise land on the wrong side of the image).
Toggling refreshes the current frame (or the active projection) immediately.
The same three checkboxes appear on the Mask Builder and Calibrate tabs and
stay in sync whenever geometry is pushed/pulled between tabs; saved/loaded
calibration files (`.json`/`.txt`) round-trip the codes as MIDAS's repeatable
`ImTransOpt <code>` paramstest key (1=Flip Y, 2=Flip Z, 3=Transpose). In
Hydra mode, GE1-4's Transforms cards also carry the per-panel-only **Rotate**
field (see Hydra mode below); this same Transforms card is shared code
between the single-detector and Hydra tabs.

### Load/save calibration card (optional)
Sits directly below the Transforms card.
Load geometry from a **calibration `.json`, a MIDAS `paramstest.txt`, or a pyFAI
`.poni`** (auto-detected). It fills **BC, Lsd, pixel size, wavelength, the
Ring-simulation card's ty/tz tilt fields, and the Transforms checkboxes**
(from any `ImTransOpt` lines in the file), unchecks "Beam centre = image
centre", and refreshes the overlay and radial plot.

When a calibration file carries the **full geometry (tilts + distortion)**, the radial
integration switches from simple concentric-circle binning to a **proper MIDAS-engine
integration** that maps every pixel through the calibrated tilts and distortion (the
same core as Batch Integrate) — so ring positions/intensities are geometry-correct, not
just distance-from-beam-centre. The card's status line reports which mode is active. The
binning geometry is built once and reused across frames (a fast `hard` kernel keeps the
preview responsive); without a full-geometry file, the fast circle binning is used. If no
calibration file is loaded but the Ring-simulation card's **ty/tz** tilt fields are
non-zero, the radial integration still runs the tilt-aware MIDAS engine using a geometry
built live from those fields (BC, Lsd, pixel size, wavelength) — so dialing in tilts here
without a calibration file already produces a tilt-corrected profile, not just a
tilt-shaped ring overlay.

**Save JSON / Save params (.txt) / Save PONI** (below the loader) export whatever
geometry is currently in effect — the loaded calibration file if any, otherwise the
one synthesized from the Ring-simulation widgets above — as a calibration file you can
reload here, on the Calibrate tab, or in Batch Integrate. **Save params (.txt)** writes
a standalone MIDAS `paramstest.txt`; **Save PONI** writes a pyFAI `.poni` (note: PONI's
Rot1–3 convention cannot represent MIDAS's tx/ty/tz tilts, so tilts are **not** included
in a `.poni` export — use JSON or `.txt` to keep them). Clicking any of the three before
an image is loaded warns instead of writing a file (there is no detector size to write).

### Live Data card *(experimental)*
Sits **above the Data card**, collapsed by default behind its own title-bar
checkbox — check it to reveal the live-PV controls, uncheck to hide them
again (unchecking also stops an active stream, so a hidden card can never be
left silently connected). Subscribes directly to an EPICS PVA detector-image
PV (NTNDArray) and renders each frame **inline in the Data Viewer's own image
pane** — the same view used for files/folders/HDF5 stacks. Because it feeds
the normal frame pipeline, all existing Data Viewer analysis keeps working
live: dark/bright/background/mask corrections, the intensity-range mask,
beam-centre picking, and the radial integration plot all update as new
frames arrive.
- **Dependency:** `pvapy` (the EPICS pvAccess client library) is a required,
  pinned dependency (`pyproject.toml` / `environment.yml`), installed
  automatically with the rest of the GUI's stack — no separate extra to
  install. If somehow missing from the active environment, **Start** shows an
  install hint instead of failing outright.
- **Live PV** field is an editable dropdown: pick a known device by name (the
  list comes from **Preferences ▸ Devices**, see below) to fill in its full PV
  automatically, or type any other PV by hand (placeholder shows an example,
  `20IDFF:Pva1:Image`). **Start** / **Stop** buttons and a status line (stopped
  / waiting for PV / connected / streaming with frame id / error). GUI updates
  are throttled to the tab's existing ~16 fps debounce, so a fast PV update
  rate doesn't overwhelm the interface.
- **Sim Detector** is a built-in dropdown entry (PV `midasSim:Pva1:Image`) for
  exercising Live Data with **no beamline hardware**: picking it and clicking
  **Start** lazily launches an in-process fake PVA server
  (`midas_gui/sim_detector.py`) that streams random frames shaped like a
  DECTRIS Eiger2 500K (1030×514 px) with counts in `[0, 60000]`, at 5 Hz by
  default — real `PvaLiveSource` code path, so it's indistinguishable from a
  real detector's stream. The rate, frame size and intensity range are
  constructor parameters in that file for anyone who wants a different fake
  stream. The simulator keeps running (harmless background thread) across
  repeated Start/Stop until the app closes, at which point it's stopped
  automatically.
- **Use Buffer** captures the last **N** live frames (N field next to it,
  2–100 — capped to bound memory use for large-format detectors) into
  an in-memory ring buffer, so Projection and every other stack-based analysis
  become available on live data. Click it to arm: it turns **yellow** and its
  label counts up (`Buffering… (7/20)`) while frames keep streaming in — the
  live single-frame view is unaffected during this. When no new frame arrives
  for ~2 s (streaming paused, or **Stop** clicked), it turns **green**
  (`Buffer Ready (20)`) and the buffered frames become a normal navigable
  stack: the frame slider/spin/prev-next enable, and **Project stack** runs
  max/sum/average over the buffered frames exactly as it would for a loaded
  HDF5 file or folder. If new frames resume arriving, it flips back to yellow
  and keeps rolling (oldest frame dropped once past N). Click it again to turn
  buffering off and discard the buffer. If **Use Buffer** is already armed
  (yellow or green) when **Start** is clicked, buffering carries over into the
  new stream — it re-arms with a fresh empty buffer rather than turning off,
  so you don't need to click **Use Buffer** again after Start. Loading static
  data always clears any existing buffer. A small **💾** button next to **Use
  Buffer** enables once the buffer is frozen (green) — click it to save the
  buffered frames to an HDF5 file you choose, written as a `(N,H,W)` dataset
  named `buffer/data`. The same buffer is also what **Import from…** offers to
  other tabs — see **Cross-tab data import** in §1.
- **Stopping live streaming does not auto-reload the previous file/folder/HDF5
  source** — the Data card below gets a small **⟳ Reload** button next to its
  path field for exactly this: click it after Stop to restore the static data
  that was loaded before streaming started.
- **Ring overlay is static per-frame while streaming**: rings drawn by "Simulate
  rings" (Ring simulation card) keep rendering on top of each new live frame at
  their already-computed geometry — arrival of a new frame does **not** by itself
  trigger a recompute (ring radii don't depend on frame data). With Simulate
  rings' live toggle on, changing a material/lattice/geometry field still
  recomputes and redraws immediately. **Stop** leaves the last received frame on
  screen. Closing midas-gui also stops any running stream.
- **Colormap/level changes persist across live frames**: dragging the histogram's
  level range or its own zoom (or the cmap dropdown) is remembered and reapplied
  to every new incoming live frame instead of being reset to the vmin%/vmax%
  percentile defaults. Editing vmin%/vmax%, toggling Log/Linear, or loading a
  new file/frame/dataset switches back to auto-levels — see §13 for the general
  rule shared by every image viewer in the app.
- **B-PILOT auto-start bridge**: on launch, MIDAS GUI opens a local-socket
  server (`midas_gui/bridge_server.py`) that lets **B-PILOT** (a separate
  Bluesky plan-runner GUI) trigger Live Data with no clicks here — when
  B-PILOT dispatches a scan on a known detector, it sends a `{"type":
  "live_pv", "prefix": ...}` message; MIDAS GUI resolves the prefix against
  **Preferences ▸ Devices** and, if it matches, checks/expands the Live Data
  card, fills in the PV and clicks **Start** automatically (switching PVs
  first if a different stream is already running). No-op — and silent in the
  log only — if B-PILOT never connects or the prefix isn't a known device.

### Beam-centre picking (on the image)
The image viewer has **Pick BC** (single click sets the beam centre) and **Pick Ring**
(click ≥3 points on a ring; a circle fit estimates the beam centre). Either updates
BC_y/BC_z and re-runs the overlay + radial integration. Pick Ring's picked points and
fitted circle are drawn in **blue**, distinct from the **amber** Simulate-rings overlay
so the two aren't confused when both are visible on the same image. **Clear** removes
all of it at once — Pick Ring's points/fit **and** the Pick BC crosshair marker,
regardless of which tool left it on screen.

### Top-N brightest pixels (image toolbar)
A **Top-N pixels** toggle button (with an **N** spin box) sits on the image toolbar.
When on, the **N highest-intensity pixels** of the current frame are marked with a
crosshair inside a translucent circle (cyan) centred on each pixel, and the Intensity
statistics panel switches to show the statistics of just those N pixels ("Top N
pixels"). It follows frame changes while active. Clicking the button again removes the
markers and restores the normal statistics. Useful for quickly locating saturation /
hot pixels.

An **I >** checkbox next to the N spin box enables an optional intensity floor
(a field to its right, editable once the checkbox is on): when set, pixels at
or below that value are excluded from ranking entirely — never marked, never
counted toward N — so a low-signal frame can't be forced to mark N pixels that
aren't actually meaningful. If fewer than N pixels clear the threshold, fewer
than N markers are drawn (down to none).

### Region-of-interest (ROI) tool (image toolbar)
A **ROI: Box / Line** row sits on the image toolbar, alongside a
**Clear ROIs** button. Click Box or Line to arm it, then click-drag on the
image to draw the shape (a drag shorter than a few pixels is treated as a
cancelled attempt — the mode stays armed so you can retry). Arming a shape
mode automatically disarms Pick BC/Pick Ring and vice versa, since they all
read the same click-drag. Drawing a shape opens a small **floating stats
popup** next to it and un-arms the button (one-shot, like Pick BC). While
dragging out a **line** ROI, the live preview is a true diagonal line
(previously it looked like a rectangle until the mouse was released); a
**box** ROI's drag preview is unchanged.

Each ROI gets its own color (cycled from a fixed palette) shared by the
on-image shape, its on-image label, and its popup's title/label field, so
multiple simultaneous ROIs stay easy to tell apart. The label is editable —
typing a new name in the popup updates the on-image label to match. New
ROIs are numbered "ROI 1", "ROI 2", ... in creation order; **Clear ROIs**
resets that counter, so the next ROI drawn after a clear starts back at 1.
All on-image ROI text (the "ROI N" label, and a box's corner/width/height
annotations below) is drawn with a translucent dark background so it stays
legible over bright image content, in a font ~20% larger than the app
default.

A **box** ROI additionally shows its pixel geometry directly on the image,
in the ROI's color, updating live while it's dragged or resized: the
bottom-left corner's `(x, y)` coordinate (rounded to the nearest whole pixel)
next to that corner, `w = ...` below the bottom edge, and `h = ...` to the
right of the right edge (also rounded to whole pixels — no fractional-pixel
values are shown). A **line** ROI shows its length in pixels (`NN px`,
rounded) at the line's midpoint. Every ROI's editable "ROI N" label is
anchored at the shape's actual position — for a line, that's its current
start point (the end where distance = 0 in the popup's profile, swapping
ends when **Flip direction** is used), not a fixed image corner.

- **Box** popups show a linear/log intensity histogram of the pixels inside
  the box, plus a zoomed-in crop of the boxed region rendered with the
  tab's current colormap. The crop image is not fixed-size — dragging the
  popup window's edge to resize it grows or shrinks the crop image along
  with the histogram. When a bad-pixel mask is active (composite mask file
  or the intensity-exclude controls), masked pixels are excluded from the
  histogram/stats and are marked in translucent bright red on the crop
  image, matching the main viewer's bad-pixel overlay convention.
- **Line** popups show a live **intensity-vs-distance profile** along the
  line (distance measured from one endpoint), plus N/min/max/mean of the
  sampled values. The line itself is drawn as a single arrow (shaft + head
  in one shape, recomputed from the two endpoints on every drag) pointing
  toward the end where distance = 0 → increasing runs; a **Flip direction**
  button in the popup reverses it. Masked pixels along the line are excluded
  from min/max/mean (shown as gaps in the profile); a line drawn entirely
  over masked pixels shows "(all pixels masked)" instead of the stats.

Both the box's histogram and the line's intensity-vs-distance profile cap
how far you can zoom or pan out — the same range-limiting used on the tab's
radial-integration plot — so scrolling/dragging the plot can't lose the
data in an empty view; the limit is recomputed from the current data range
each time the popup refreshes.

Dragging or resizing a shape (or right-click → drag a handle) recomputes its
popup immediately, and every popup also refreshes automatically on frame
navigation, new live-streamed frames, and dark/bright/background/mask
correction changes — the same as the rest of the tab's live readouts.

A popup's on-screen **position is independent of the shape's position**: it
opens next to its shape once, at creation time (placed on whichever monitor
the shape is on, useful for multi-detector/multi-screen setups), and after
that it's a normal free-floating window you can drag anywhere — moving it
never repositions again on its own, only its contents stay live.

Closing a popup (its window close button) removes its shape from the image;
right-clicking a shape and choosing **Remove ROI** closes its popup too.
**Clear ROIs** removes every ROI and popup at once. ROIs are session-only —
they are not saved with the rest of the tab's state.

**Always on top, minimize to ribbon.** ROI popups stay **above every other
window** (not just above the main midas-gui window) so clicking elsewhere in
the GUI, or in another application, never buries one behind something else.
Minimizing a popup with the window's own **OS-level minimize button** (the
title bar/traffic-light control) doesn't send it to the dock/taskbar as a
normal window minimize would — instead the popup hides and a small colored
square button, color-matched to its ROI, appears on a narrow **ribbon**
along the left edge of the image viewer. Clicking a ribbon button restores
that popup (shows it, raises it, gives it focus) and removes the ribbon
entry. A minimized ROI's shape stays on the image and its stats keep
updating live in the background exactly as if the popup were open —
minimizing only hides the window. Removing a minimized ROI (right-click →
**Remove ROI**, or **Clear ROIs**) also removes its ribbon entry.

### Intensity statistics (left panel, bottom)
At the bottom of the Data-Loader panel, in a **draggable pane** below the loader
cards — grab the splitter handle above the panel to make the statistics area taller or
shorter. It holds a **histogram** of the intensity distribution (full range, log-y
toggle) with a **textbox** beneath it reporting N (pixel count) and the
**p70 / p90 / p99 / p99.9 / p99.99** percentiles — each with the **number of pixels
above** that value. The histogram's lower-left corner is fixed at x = 0, y = −2 and both
axes rescale to `(0, xmax)` / `(−2, ymax)` on every refresh (frame change, scope change,
projection, new data); zooming/panning is capped at that same range (as with the ROI
popup plots and the radial-integration plot), so it can't be scrolled out until the
distribution is lost in empty space. It reflects the **corrected** image (dark / bright / background)
with masked pixels (file masks + the intensity-range mask) excluded, and updates live as
any of those change. A scope selector switches between the **current frame** (per the
slider) and **All frames** (combined over the whole stack/folder). When a **Projection**
is active the panel shows the projected image's statistics (the scope selector is
disabled); when **Top-N pixels** is active it shows those pixels' statistics.

A small **"A"/"M"** button pair sits in the histogram's bottom-left corner
(replacing pyqtgraph's native auto-range corner button), same control as on the
radial-integration plot below — see **Manual axis limits (A/M toggle)** under
that section for the full behavior (native right-click "Manual" min/max
fields, persistence across live updates, reclick-to-reset). In **Manual**,
the histogram holds those limits instead of auto-rescaling to
`(0, xmax)` / `(−2, ymax)` on every refresh (new frame, scope change,
Top-N toggle).

### Radial integration plot (bottom-right)
Below the image is a live **azimuthal average about the beam centre** (`R bin`,
`Integrate`, and an `Auto` toggle that recomputes on frame/BC/mask change) — geometry-free
circle binning by default, or the tilt/distortion-aware MIDAS engine when a full
geometry is in effect (a loaded calibration file, or non-zero ty/tz in the
Ring-simulation card — see the Load/save calibration card above). Peak/ring markers overlaid on
the plot use the ring's true 2θ, so they line up with the profile in either mode.
**Clicking a radius on the plot draws the matching ring (magenta) on the image.** Axis
units switch between R (px) / 2θ / Q; the **X-axis lower bound defaults to 0**. A small
circular **"?"** button next to the `Radial` control opens a message box explaining how
the profile is computed (full-geometry (η, R) binning vs. the circle-binning fallback).
The **Exclude range** checkbox and its `<` / `>` integer bounds (see previous
section) sit pinned to the far right end of this same toolbar row — the `R bin`
field was narrowed and the `N bins | max=...` stats printout that used to occupy
that space was removed to fit them.

**The default view always auto-fits the current profile's X/Y extent** — it never gets
stuck showing a stale or unrelated range from an earlier profile. If you manually
zoom or pan (drag, wheel-zoom, box-zoom, or the axis context menu), that exact view is
**preserved across parameter changes** (new frame, changed BC/tilt/Lsd, etc.) instead of
snapping back to full range every time the profile updates — the curve redraws in
place under your current zoom. A manual zoom is only cleared when it would no longer
make sense: switching the X-axis unit (R/2θ/Q) drops the remembered X range, and
toggling **Log Y** drops the remembered Y range (both because the old numbers belong to
a different scale). **Pan and zoom are always bounded to the current profile's extent**
(a margin around its X/Y range) so you cannot drag or scroll off into empty space. The
splitter handle above this panel (between it and the image view) is wider than a
default Qt splitter, to make it easier to grab.

#### Manual axis limits (A/M toggle)
A small **"A"/"M"** button pair sits in the plot's bottom-left corner, in the
spot pyqtgraph's native auto-range button normally occupies (that native
button is hidden in favor of this pair). **A** (default) is the auto-fit
behavior described above. **M** switches to **Manual**, which holds exactly
the limits set via each axis's own **native right-click menu** — right-click
the plot, open **X axis** or **Y axis**, pick **Manual**, and type a min/max
(this is stock pyqtgraph, not a MIDAS-specific control). Once **M** is
active, the plot's axes show **exactly** those typed values (e.g. entering
`0` shows `0`, not a padded/rounded value) and hold them through every
live-acquisition redraw — Manual mode stops the plot from re-fitting or
re-clamping its range on each new frame, and editing the min/max fields
again takes effect immediately, live acquisition or not. Switching to **A**
does not discard the typed values — clicking **M** again restores exactly
what was last entered. **Reclicking the already-active button resets the
view to that mode's default**: **A** forces an immediate re-fit to the
current profile (discarding any manual pan/zoom drift), and **M** snaps
the view back to the held manual limits (discarding any drift from panning
around while still in Manual).

---

## 4. Tab 1 — Mask Builder

**Purpose:** identify and exclude bad pixels. The final mask is a uint8 array
(1 = bad) broadcast to Tabs 2, 3, 4, 5. Browsing an image or a mask loads it
immediately. Pointing the **Image** field at an `.h5` file reveals a **Dataset**
dropdown (auto-populated with the file's datasets and shapes, preferring a
≥2-D dataset) — the same pattern used by the Stack field below it and by the
Data Loader panels elsewhere in the app. The **Image** field's browse button
also has an **Import from…** submenu of whatever file/folder is currently
loaded in another tab (see **Cross-tab data import** in §1); the **Stack**
field's browse menu additionally lists any other tab's frozen (green) Live
Data buffer as a **Buffer (N frames)** entry — picking one snapshots that
buffer to a temporary HDF5 file and feeds it into the auto-mask stack exactly
like a browsed folder/file would. The Stack browse menu also has a
**Files (multi-select)…** entry that opens a multi-select file dialog so the
temporal stack can be built from an explicit, hand-picked set of files
(e.g. frames scattered across a directory or spread across multiple
directories) instead of only a whole folder or a single glob pattern — the
field then shows "N files selected" (hover for the full file list) and the
stride still applies to the chosen list.

**Transforms: Flip Y / Flip Z / Transpose** (below the Image field) apply
MIDAS's `ImTransOpt` image transform to every frame this tab loads — the
single preview image and every frame of the Section 2 statistical stack
source alike — before any masking runs, in the fixed Flip Y → Flip Z →
Transpose order. Receiving a calibration from Tab 2 (Section 4) pre-checks
these to match the Calibrate tab's own Transforms state (still overridable
here). Same codes/semantics as the Data Viewer and Calibrate tabs.

### Section 1 · Threshold mask (always applied)
`pixel ≤ lower | pixel > upper`. The upper bound auto-fills from the data type on load
(e.g. 1,048,575 for uint20 Eiger, 4,294,967,295 for uint32).

### Section 2 · Statistical auto-mask
Two **independently selectable** methods — enable either or both:
- **Spatial outlier** — robust local-anomaly detection (5×5 median residual → 15×15
  local MAD → Z-score → hot/dead/saturated gates). Uses a **temporal median** over the
  stack folder when provided (cleaner reference), else the single frame. Controls:
  `K_σ` (6.0), `Hot` (1.5), `Dead` (0.5).
- **Temporal constancy** — flags frozen pixels whose frame-to-frame std is below
  `Frozen × Q75(std)` (0.05); needs a stack of ≥2 frames.

A shared **stack source + stride** feeds both (the temporal median for spatial, the
frame stack for temporal constancy). The stack can be a **folder / `*.tif` glob**, an
explicit **multi-select file list** (via the browse menu's Files (multi-select)…
entry), or a **single HDF5 file whose 3-D dataset is a time sequence of images** — for
an `.h5` a **Dataset** selector appears (auto-populated, 3-D datasets preferred). *All
σ / K_σ / n_σ fields in this tab accept any value — there is no upper limit.*

### Section 3 · Spatial spike rejection
Laplacian high-pass single-pixel-spike detector; `n_σ` threshold (5.0).

### Section 3b · Cosmic-ray rejection (temporal)
Per-pixel temporal σ-clip across a ≥3-frame stack; anomalies OR'd across frames.
`n_σ` (5.0).

### Section 4 · Calibration-based masks (need a Tab 2 result)
- **Azimuthal σ-clip** — flags (R, η) cells deviating from the azimuthal mean.
- **Learnable mask** — differentiable per-pixel mask trained against an η-uniformity
  loss (`steps`, `lr`, `sparsity`).

### Section 5 · Post-processing
**Dilation (px)** (default 0) grows every bad pixel from Sections 1-4 (and a
mask loaded via **Save / Load**) using 8-neighbor morphological dilation: at
N=1 the full 3×3 block around each bad pixel becomes bad, at N=2 the full
5×5 block, and so on (a (2N+1)×(2N+1) square centered on each pixel).
Applied once, on **Compute Mask** / mask load, before hand-drawn shapes are
combined in — hand-drawn regions are never grown by this control.

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

### Mode ribbon (leftmost strip)
Like the Data Viewer tab, a narrow vertical strip switches between **Single
detector** (the view described below — unchanged) and **Hydra** (per-panel
calibration for the 1-ID-E 4-panel GE detector). The two modes are
independent: switching does not share data or geometry between them.

### Hydra mode (4-panel GE detector)

**Purpose:** fit each of the 4 GE panels' own detector geometry (BC, Lsd,
tilts, distortion) from one calibrant dataset, using one shared "recipe"
(pipeline, λ/pixel/calibrant, refine-parameter choice) applied to all 4 —
since each GE panel is a physically separate detector, its beam centre,
Lsd, and tilt are fit and shown independently.

- **Hydra data / ← Data Viewer (left panel)**: the same sibling-discovery
  data loader as the Data Viewer tab's Hydra mode (point at any one panel
  file, the other 3 are found automatically; shared frame slider; Dark /
  Bright / Background per panel). **← Data Viewer** pulls the currently
  loaded Hydra panel path *and* every present panel's fitted/seeded geometry
  (BC, Lsd, tilts, Transforms) straight from the Data Viewer tab's Hydra
  page, so you don't have to re-browse or re-pick beam centres you've
  already set up there. This mode has no stack-projection feature (like the
  single-detector Calibrate tab) — use **Average frames** below instead.
- **Detector & Calibrant, Threshold, Average frames, Refine parameters,
  Advanced (middle panel, shared across ge1–ge4)**: one copy of each,
  identical in meaning to the single-detector tab's own cards, applied to
  every panel's fit. There is no **Multi-panel detector** (tiled sub-panel
  rigid-shift) group in Hydra mode — that feature refines shifts between
  tiles *inside* one monolithic detector's readout, which doesn't apply to
  4 separate physical GE detectors.
- **Transforms / Initial seed (middle panel, switches with the active
  panel)**: independent per GE panel — physical mounting orientation
  (Flip Y/Flip Z/Transpose) and beam centre/Lsd/tilts genuinely differ
  panel to panel. **Load calibration file…** seeds one panel's BC/Lsd/tilts/
  Transforms from a file (and mirrors λ/pixel into the shared Detector card
  if the file carries them); Pick BC/Pick Ring on the image seed that
  panel's BC the same way as the single-detector tab.
- **Run (Run mode / Run Calibration / Abort)**: **Sequential** fits one
  panel at a time (full per-line progress in the Log tab, same as the
  single-detector tab); **Parallel** starts every currently-found panel's
  fit at once — faster, but each panel's fine-grained progress prints go to
  the console rather than the Log tab (only start/finish/error lines appear
  there), since a fit's internal progress-capture can't safely be shared
  across concurrent threads.
- **Image toolbar**: **GE1 / GE2 / GE3 / GE4** (no Composite — calibration
  is inherently per-panel; see the Data Viewer tab for the windmill
  composite once all 4 are fitted) plus **Show rings** / **Corrected**,
  identical in meaning to the single-detector tab's own predicted-ring
  overlay toggles, applied to whichever panel is active.
- **Radial Profile (bottom right)**: one shared multi-curve plot — all 4
  panels' post-fit azimuthal profiles at once, same style/controls as the
  Data Viewer tab's Hydra Radial Profile plot (R-bin/η-bin/weighting +
  **Re-integrate**, X-axis unit selector). **Ring Residuals** and
  **Results** switch to show only the currently active panel's own chart/
  parameter grid (each has its own **→ Send to Data Viewer**, **Save
  .json**, and **Save paramstest.txt**, scoped to that one panel). **Log**
  is shared across all 4 panels, each line prefixed `[ge1]`…`[ge4]`.

### Data Loader panel (left)
The calibrant frame (Data + Frame index), Dark, Bright, Background and Mask are selected
in the shared **Data Loader panel** (see §1). Each Dark/Bright/Background field is
averaged over a chosen index range; Bright offers **Flat-field divide** or **Subtract**;
all mask sources (plus the auto-added Tab 1 mask) are unioned. Dark is passed to the
calibration pipeline; bright/background are applied to the calibrant before calibration
and post-calibration integration. The image preview updates live to show the
dark/bright/background-corrected frame as soon as a field is picked or changed (the raw
frame still feeds the actual calibration run, which applies these corrections itself).

### Detector, seed & Load calibration file
The **Detector & Calibrant** card sets λ, pixel size(s) and detector transforms
(**Flip Y / Flip Z / Transpose** — MIDAS's `ImTransOpt`, in that fixed order).
Toggling a Transforms checkbox live-updates the image preview immediately, the
same way Data Viewer and Mask Builder do; Pick BC / Pick Ring clicks are read
straight off that (transformed) preview, so the seed beam centre always lands
in the same coordinate space the fit will actually run in. The calibration run
itself applies the identical transform (to the calibrant image and to Dark) right
before handing the array to the pipeline, so a Flip/Transpose selection is now
honoured by every pipeline mode, not just the display; the **Initial seed** card
sets the LM starting point (BC_y, BC_z, Lsd, **and tilts tx/ty/tz**). **Load calibration file…** (top of the Detector card) reads a MIDAS
**paramstest `.txt`**, a calibration **`.json`**, or a pyFAI **`.poni`** (auto-detected)
and fills λ, pixel size, the seed BC + Lsd, and the Transforms checkboxes from any
`ImTransOpt` lines — a fast way to start from a previous calibration or a known
geometry. (The synthetic test data ships
`test_data/calibration_synthetic.{json,txt,poni}` as a ready example.) **← Data Viewer**
(next to *Load calibration file…*) pulls λ, pixel size, Lsd, beam centre, the Data
Viewer's ty/tz tilt fields, and its Transforms state straight from the Data Viewer tab
into the same fields (BC and Lsd land in the seed card; ty/tz land in the seed tilt
fields). A **Feed result back to seed** checkbox (on by
default) copies the optimized BC / Lsd / tilts / distortion of each run back into the
seed fields, so a follow-up run starts from the previous solution. *Seed tilts are
honoured by the Four-stage / advanced pipelines; the One-shot / First-time paths seed
tilts only if the installed backend exposes initial-tilt options.*

### Average frames
For a multi-frame source (HDF5 / folder), **Average frames into a single image** builds
the mean of a frame range and calibrates on that. **start** / **end (0 = all)** select
the range. The card is disabled for single-frame sources; the preview updates
live as the options change.

### Pipeline selector
One-shot (default) · First-time · Four-stage · Bayesian (Laplace σ) · Joint-cake.
*For trustworthy tilt/strain, prefer Four-stage or First-time — One-shot/Bayesian can
report a spurious self-compensated tilt on weakly-tilted data.*

### Refine flags
Which parameters vary: Lsd, BC, ty, tz, tx, Wavelength, plus a "Residual map" build
toggle. The **Distortion (n/15)** checkbox has a companion **…** button that opens a
per-coefficient dialog: the 15 distortion coefficients are grouped by η-fold (isotropic
radial + folds 1–6), and named **preset modes** (None · Isotropic only · Iso + up to
2-fold · Iso + up to 4-fold · All (15)) auto-select whole ladders. The checkbox label
shows how many coefficients are selected. Picking a strict subset genuinely restricts
which coefficients the LM fit refines — including on the One-shot pipeline, which
transparently switches to a lower-level routine for a partial selection (its own
backend otherwise only supports all-15-or-none); picking "All (15)" or leaving
Distortion unchecked runs the normal One-shot path unchanged. Advanced (E-M / LM
iters, device, output dir) and Multi-panel detector groups are collapsible.

### Live threshold slider
Zeroes calibration-image pixels below the slider value (background suppression for
ring-finding); the preview updates instantly.

### Pick BC / Pick Ring
Click the image to seed the beam centre (single click) or fit a ring (≥3 clicks); the
Pick Ring points/fit are drawn in blue, distinct from the amber Simulate-rings overlay.

### Predicted-ring overlay (image toolbar)
After a run, the calibrant's predicted ring radii are drawn in **lime** with a
red/yellow beam-centre marker. **Show rings** toggles the overlay; **Corrected**
redraws the same lime rings reshaped through the fitted tilt (tx/ty/tz) instead
of as plain circles, so you can see how much the geometry actually deviates
from an untilted detector — the rings still look like the same thin curves,
just bent, rather than a separate scattered point-cloud.

### Run / Abort
**Run Calibration** launches the worker; **Abort** terminates it and frees the slot so
you can immediately start a new run (the calibration is one uninterruptible library
call, so abort hard-terminates the worker thread rather than waiting).

### Results (right panel, bottom tabs)
Radial Profile (with ring markers and an **Azim. avg** selector — see Tab 4), Ring
Residuals bar chart, **Results**, and Log. Integration runs automatically after
calibration. The **Results** tab shows the parameter set exactly as it is written to
`paramstest.txt` (Lsd, BC, tx/ty/tz, the distortion coefficients, Parallax, Wavelength,
px, NrPixelsY/Z, RhoD, SpaceGroup, LatticeConstant, and any `ImTransOpt` codes from the
Transforms checkboxes) as **plain text laid out in multiple
columns** so the wide-but-short panel stays readable — no table widget. The distortion
`p0–p14` slots are labelled with their coefficient names (iso_R2, a1, phi1, …), and only
the coefficients actually selected for that run are listed (not all 15), so the display
matches what you asked to refine; the saved `paramstest.txt`/`.json` exports still carry
every slot's real value, including any held-fixed value carried over from a prior
calibration. A strain/timing line sits below. **→ Send to Data Viewer** pushes the *full*
calibrated geometry (λ, pixel size, Lsd, beam centre, **tilts, distortion, and the
Transforms state**) into the Data Viewer tab — where it drives the tilt/distortion-aware
radial integration, not just circle binning, and sets Data Viewer's own Flip/Transpose
checkboxes to match — the reverse of the Data Viewer's "→ Send geometry to Calibrate". The
bottom tab area is fully resizable (drag the horizontal splitter) and the Log fills its
tab.

### Export
**Save calibration.json** and **Save paramstest.txt** (standalone or from a template).
Both carry the Transforms checkboxes' `ImTransOpt` codes (one `ImTransOpt <code>` line
per checked transform in the `.txt`; an `im_trans` list in the `.json`), so reloading
either file elsewhere restores the same detector orientation.

---

## 6. Tab 3 — Calibration Refinement

**Purpose:** optional post-calibration geometry refinement against an **η-uniformity**
criterion (rings should be azimuthally uniform). Uses a **derivative-free Nelder-Mead**
optimiser (the differentiable integrator returns NaN geometry gradients at the
beam-centre singularity on this build).

The sample frame and Dark/Bright/Background/Mask are selected in the shared **Data Loader
panel** (see §1) — the refinement runs on the corrected image. Middle-panel controls:
calibration source (Tab 2 result, or start-from/continue radios), parameters to refine
(BC_y, BC_z, Lsd, ty, tz), optimiser + iterations. **Run Refinement** shows a live loss
curve; **Apply** broadcasts the refined geometry downstream. If Apply is never clicked,
the Tab 2 result flows through unchanged.

---

## 7. Tab 4 — Batch Integrate

**Purpose:** integrate a stack of frames into 1-D profiles using the calibrated
geometry and optional corrections.

### Mode ribbon (leftmost strip)
Like the Data Viewer and Calibrate tabs, a narrow vertical strip switches
between **Single detector** (the view described below — unchanged) and
**Hydra** (per-panel integration for the 1-ID-E 4-panel GE detector). The
two modes are independent: switching does not share data or geometry
between them.

### Hydra mode (4-panel GE detector)

**Purpose:** integrate each of the 4 GE panels' own frame stack with its
own independently fitted geometry, using one shared integration "recipe"
(kernel, bins, corrections, monitor normalisation, output format) applied
to all 4 — since each GE panel is a physically separate detector with its
own geometry, but the same beam and the same choice of what to compute.

- **Hydra data (left panel)**: the same sibling-discovery data loader as
  the other Hydra pages (point at any one panel file, the other 3 are found
  automatically), but in **streaming** form for batch runs — a shared
  frame **range + stride** (frames are synchronized across panels, so one
  range applies to all) instead of a frame navigator, Dark/Bright/
  Background per panel, and an independent **Mask** per panel (its own
  file/folder sources, unioned — no cross-panel auto-discovery, since mask
  files are physically panel-specific and may not follow the ge{n} naming
  convention data files do).
- **Integration, Physics corrections, Monitor normalisation, Output
  (middle panel, shared across ge1–ge4)**: one copy of each, identical in
  meaning to the single-detector tab's own cards, applied to every panel's
  run. Each panel writes its output into its own `ge{n}/` subfolder under
  the shared output directory, so the 4 panels' files never collide. There
  is no Drift correction or live **MONITOR** folder-watch in Hydra mode
  (both single-detector-only for now).
- **ge1 – ge4 toggle + Calibration source / values / progress (middle
  panel, switches with the active panel)**: each panel picks **From
  Calibrate tab** (auto-populated the moment that panel's fit finishes on
  the Calibrate tab's own Hydra page — no action needed here) or **From
  file** (a calibration `.json`, MIDAS `paramstest.txt`, or pyFAI `.poni`,
  same auto-detection as the single-detector tab); the **Calibration
  values** grid and a compact progress bar are per panel.
- **Run (Run mode / Start Integration / Abort)**: **Sequential** integrates
  one panel at a time; **Parallel** starts every currently-found panel's
  run at once. Abort stops every running panel after its current frame,
  keeping frames already written.
- **Waterfall / Stacked profiles (right panel, switches with the active
  panel)**: each panel has its own pair, same controls as the
  single-detector tab's own viewers. A shared **Log** below is prefixed
  `[ge{n}]` per line so all 4 panels' activity can be read from one place.

### Data Loader panel (left)
The streaming **Data** source (folder/glob or HDF5 dataset) with **frame range + stride**,
plus Dark/Bright/Background and Mask, are selected in the shared **Data Loader panel**
(see §1). Dark/bright/background are applied per frame; all mask sources are unioned.

### Calibration source (middle)
- **From Tab 2** — the calibration (or refined) result.
- **From file** — a **calibration `.json`, MIDAS `paramstest.txt`, or pyFAI `.poni`**
  (auto-detected; both GUI and MIDAS-pipeline json key styles supported). Entering a
  path auto-selects this option.

### Calibration values (middle, read-only)
A **Calibration values** card shows the geometry actually in use — λ, Lsd (mm),
BC_y/BC_z (px), tilts tx/ty/tz (°), pixel sizes, detector size, and a distortion summary
(number of non-zero coefficients). It repopulates whenever a calibration arrives from
Tab 2 (or Tab 3 refinement, or the Data Viewer via Tab 2) **and** when a calibration
file path is entered, parsing the file directly so you can confirm the numbers before
running. A note line reports the active source (or any file-read error).

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

### Run / Abort / Clear
**Start Integration** / **Abort**. Abort first asks the worker to stop cleanly between
frames (keeping frames already written); if it does not stop promptly it force-terminates
and frees the slot. Completed frames' output files are preserved. **Clear results** removes
the profiles/plots computed **this session** for the current data (waterfall, stacked
profiles, and the integrated-frame tracking) so a fresh integration can start — it stops
any active monitor but does **not** delete raw data or any files already written to disk.

### Live folder monitoring (MONITOR)
The **MONITOR** button at the bottom of the left Data Loader panel (folder/glob sources
only) starts a live watch: while active it turns **green** and the GUI polls the data
folder for **new** TIFF frames, integrating each one **as it appears** and adding it to
the display. It does **not** re-run the whole batch — it reuses the already-built
**detector map** (the binning geometry / pixel-count cakes; reused from a prior *Start
Integration* run when the calibration, kernel, bins, mask and folder are unchanged, or
built once on first use) and integrates **only the new files** (tracked by frame id, so
frames already shown are skipped). New frames honour the current kernel, corrections,
Dark/Bright/Background, mask and Q-uniform settings, and are saved to the output folder
when a 1-D format is selected. Click MONITOR again to stop; starting a fresh *Start
Integration* also stops it. (Distinct from **Monitor normalisation** above, which divides
profiles by a scalar file.)

### Output formats
CSV (R,I,σ) · XYE (2θ) · FXYE (centideg) · DAT (Q) · HDF5 (full stack) · 2D-CSV (η×R
cake). Right panel: live **Waterfall** and **Stacked profiles** — both have an **x**
selector to show the axis in **R (px) / 2θ (°) / Q (Å⁻¹)** (converted from the run's
calibration).

The **Stacked profiles** view is a publication-quality plot: each curve tags its
**source file name inline, just below the curve at its left edge** (toggle with the
*Labels* checkbox; an optional corner *Legend* is also available). A **theme** selector
switches between two saved presets:
- **White (publication)** *(default)* — white background, **point + line** markers, a
  colour-blind-friendly categorical palette (matplotlib *tab10*), a boxed frame and a
  light grid — ready to drop into a paper.
- **Dark** — the classic on-screen look (dark background, line-only, vivid
  golden-angle colours).

An **x** selector plots the axis in **R (px)**, **2θ (°)** or **Q (Å⁻¹)** (converted
from the run's calibration). The **Grid** checkbox (off by default) toggles the
horizontal + vertical grid. The *spacing* box shifts successive curves vertically
(0 = overlay). Top-right controls adjust the **line width** (`− line +`), **symbol
size** (`− sym +`, for the point+line markers — also turns markers on if a line-only
theme is active) and **label font size** (`− font +`).

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

## 9. Tab 6 — PDF Analysis

Polyatomic **total-scattering** workflow, powered by the `midas_pdf` backend:
I(Q) → Faber-Ziman structure function S(Q) → pair-distribution G(r) (Stage 1:
real composition-weighted normalization, Compton subtraction, end-to-end σ
propagation, optional differentiable scale/background refinement) **plus** a
full Stage 2-3 corrections/analysis surface — empty-cell/Paalman-Pings
absorption subtraction, detector-efficiency correction, absolute (electron-unit)
normalization, differentiable multiple-scattering correction, a fluorescence
diagnostic, CIF-driven small-box structure refinement (PDFfit-style,
error-aware), and Δ-PDF significance testing between two saved reductions. The
tab is organized as a **4-tab left panel** (Data & Reduction / Corrections /
Structure Fit / Δ-PDF) driving a **4-tab right panel** (Reduction / Structure
Fit / Δ-PDF / Log).

Ships with a dedicated, ready-to-run dataset at `test_data/test_pdf/` (real
beamline frames + pre-integrated I(Q) for Ni/CeO₂/IPA/Kapton/air-scatter, a
rasterized beamstop mask, a MIDAS calibration file, and an authored `Ni.cif`)
— kept out of git via `.gitignore` (~320 MB raw frames) but present on this
machine, so the tab opens ready to run against real data with no setup.

### Background — what I(Q), S(Q) and G(r) mean

PDF analysis is three transforms that move from *what the detector measured* to
*where the atoms are*. They are exactly the three stacked plots (top → bottom).

- **I(Q) — measured scattered intensity.** Intensity vs. momentum transfer
  `Q = 4π·sin(θ)/λ` (Å⁻¹), a wavelength-independent rescaling of the angle 2θ;
  high Q = wide angle = fine spatial detail. Obtained by azimuthally integrating
  the detector rings to a 1-D curve. I(Q) is **not** pure structure — it mixes the
  interference (coherent) signal with big smooth backgrounds: per-atom
  self-scattering (form factors `⟨f²⟩`) and inelastic **Compton** scattering.

- **S(Q) — structure function.** I(Q) with the self-scattering and Compton removed
  and normalized away (the **Faber-Ziman** step), leaving only interference:
  `S(Q) = [I_coh − (⟨f²⟩ − ⟨f⟩²)] / ⟨f⟩²`, where `⟨f⟩, ⟨f²⟩` are
  composition-weighted atomic form factors (hence the **Composition** input). S(Q)
  oscillates about **1** and → 1 at high Q where correlations wash out (the dashed
  guide line). `F(Q) = Q·[S(Q)−1]` is the same thing re-weighted by Q to emphasize
  the structurally rich high-Q oscillations.

- **G(r) — reduced pair distribution function.** The real-space answer: a
  histogram of interatomic distances, via a sine Fourier transform
  `G(r) = (2/π)·∫ Q·[S(Q)−1]·sin(Qr) dQ`. A **peak at r** means many atom pairs are
  separated by that distance; the **first peak is the nearest-neighbour bond**
  (Ni ≈ 2.49 Å, CeO₂ Ce–O ≈ 2.34 Å), later peaks are further coordination shells.
  Below the first bond `G(r) = −4πρ₀r` (nothing can be closer) — a constraint that
  needs the number density **ρ₀**. Because it uses *total* scattering (Bragg **and**
  diffuse), the PDF captures local structure even in disordered / nanoscale /
  amorphous materials, unlike a Bragg-only Rietveld fit.

```
 detector image / I(Q) file
        │  azimuthal integration
        ▼
     I(Q)   raw intensity = interference + form factors + Compton      (top plot)
        │  subtract Compton, subtract Laue (⟨f²⟩−⟨f⟩²), divide by ⟨f⟩²  (needs composition)
        ▼
     S(Q)   pure interference, oscillates about 1                      (middle plot)
        │  Fourier sine transform over [Qmin, Qmax] with a window (Lorch)
        ▼
     G(r)   interatomic distances; peaks = coordination shells         (bottom plot)
```

The **±1σ band** on G(r) is the counting uncertainty σ propagated from I(Q) through
every step, so real peaks can be told from noise. The **G/g/T/R** family (Output
selector) is the same information re-weighted: **G** oscillates about 0 (refinement
standard); **g** → 1 at large r; **T** is the total correlation; **R** is the radial
distribution whose *peak area = coordination number*. g/T/R all need ρ₀.

### Left tab 1 — Data & Reduction

**I(Q) source** (choose one):
- **Integrate detector frame** — a calibrated frame is integrated (hard/polygon
  binning, Poisson σ) and mapped to Q. Uses the Tab 2 calibration (λ, geometry)
  and an optional local **Mask** (`.tif`, nonzero = masked — same convention as
  Tab 1, loaded independently so this tab needs no cross-tab wiring to run).
- **Load I(Q) file** — a pre-integrated 2- or 3-column `Q, I, σ` text/CSV
  (comment/header lines tolerated). The tab **opens on this source by default**,
  pointed at `test_data/test_pdf/iq/04_iq_Nickel.csv` — just press
  **Compute G(r)** for the Ni PDF (first shell ≈ 2.5 Å). The same folder also
  has CeO₂, IPA, Kapton (container), and air-scatter I(Q) at λ 0.1839 Å for use
  as backgrounds or empty-cell references (see Corrections, below).

**Calibration.** The geometry/wavelength comes from Tab 2 automatically, or use
**Load calibration file…** to read a MIDAS `paramstest.txt`, a `.json`, or a pyFAI
`.poni` (auto-detected). This is required for *Integrate detector frame* and also
sets λ used by *Load I(Q) file* mode.

**Sample.** Composition (e.g. `Ni` or `C:3,H:8,O:1`; ions like `Ni2+` allowed),
number density ρ₀ (atoms/Å³; needed for refinement and for g/T/R), wavelength λ
(auto-filled from calibration), and a **Compton subtraction** toggle.

**Background subtraction (empty-cell).** Optional. Loads a second I(Q) file
(e.g. the Kapton or air-scatter references) and subtracts it from the sample:
**Manual scale** (a fixed transmission factor `s`, `I_corr = I − s·I_empty`) or
**Fit high-Q** (least-squares `s` over a chosen high-Q window). Enabling
**Paalman-Pings cylinder-in-cylinder correction** replaces the flat scale with
the Q-dependent self-/container-absorption ratio, using sample/container linear
attenuation μ (1/µm) and cylinder radii; **Estimate μ from composition** fills μ
from the sample/container composition, λ, and container density automatically.

**Normalization.** Optional **Refine scale + background** (L-BFGS) — reveals a
background polynomial degree (0–3), a low-r cutoff `r_min` (Å), and an iteration
count. Refinement requires ρ₀ > 0. It fits scale and a smooth b(Q) against two
model-free constraints: high-Q ⟨S⟩→1 and G(r)=−4πρ₀r below `r_min`.

**Q range / r range + FT / Output.** Unchanged from Stage 1: Qmin/Qmax trim,
rmin/rmax/Δr + window (Lorch/none) + binning (hard/polygon), and the bottom-plot
convention family — **G(r)** (reduced PDF), **g(r)** (pair distribution),
**T(r)** (total correlation), or **R(r)** (radial distribution, peak integral =
coordination number); g/T/R need ρ₀. Middle plot toggles between **S(Q)**
(with the S=1 guide) and **F(Q)=Q(S−1)**.

**Save G(r)** writes a three-column `r, G(r), σ` file (diffpy-CMI / PDFgui /
RMCProfile compatible); **Save S(Q)** writes `Q, S(Q)`.

### Left tab 2 — Corrections

All four stages are opt-in checkboxes; leaving all off reproduces Stage-1
behavior exactly.

- **Detector efficiency** — divides I(Q) by the sensor's quantum efficiency
  `η(Q)` for a given material/thickness (e.g. 500 µm Si), computed from the
  photoelectric absorption at each Q's implied path length. At hard X-ray
  energies a thin high-Z-poor sensor can be only a few percent efficient, so
  this legitimately amplifies I(Q) severalfold — that is physically expected,
  not a bug.
- **Multiple scattering** — differentiable cylinder-transport correction
  (`slab_transport_ms`/`ms_background_on_grid`): given an effective optical
  depth τ (from μ × R, μ either auto-estimated from composition/density or set
  manually), a single-scattering albedo, and a Q grid, computes and subtracts
  an MS background before normalization. The Log reports the median MS
  fraction β.
- **Absolute (electron-unit) normalization** — anchors the mean I(Q) over a
  high-Q window to the composition's `⟨f²⟩+⟨S_inc⟩` baseline, putting I(Q) on
  an absolute per-electron scale (needed before meaningfully comparing
  intensities across samples/geometries).
- **S(Q) tail-flatten** — a **display-only** PDFgetX3-style iterative
  MAD-clipped polynomial baseline flatten over a high-Q window; toggled on the
  Reduction plot via **Show tail-flattened S(Q)**. It never feeds back into
  G(r) — the reduction always uses the un-flattened S(Q).
- **Fluorescence diagnostic** — **Check fluorescence** reports which
  sample/container element K/L emission lines fall near the incident energy
  (a source of spurious background near an absorption edge), via
  `expected_fluorescence` / `fluorescence_report_sample_and_container`.

### Left tab 3 — Structure Fit

CIF-driven (or manually-specified lattice + atom table) small-box structure
refinement against the observed G(r), in the style of PDFfit/PDFgui:

- **Crystal** — load a `.cif` (e.g. `test_data/test_pdf/structures/Ni.cif`,
  authored FCC Ni, space group Fm-3m #225, a=3.524 Å) or build one manually
  (lattice a/b/c/α/β/γ, space-group number, an atom table with add/remove rows).
- **Fit range + parameters** — pair-list cutoff (`build_pair_list` r_max), the
  `[fit rmin, fit rmax]` window actually fit, a **σ inflate** factor (real
  beamline G(r) is often dominated by shape-mismatch systematics rather than
  counting noise, so σ can be scaled up before fitting — exposed as a tunable,
  not hardcoded), initial guesses for lattice constant / isotropic ADP (u_iso)
  / scale, an optional background polynomial order, and optimizer steps/lr/
  posterior-sample count.
- **Run structure fit** calls `refine_structure` (gradient-based, PyTorch) and
  reports fitted values ± uncertainty, χ²/ndof, and observed/model/residual
  curves on the right panel's **Structure Fit** tab.

### Left tab 4 — Δ-PDF

Significance testing between two saved G(r) snapshots (e.g. before/after a
correction, or two temperatures): **Save current result as State A/B** each
capture `(r, G, σ)` from the last Compute; **Compute ΔG(r) = B − A** calls
`delta_pdf`/`significant_mask` and plots ΔG(r) with an n·σ band, marking points
that exceed the chosen threshold as a red scatter overlay — a fast way to see
*where* two reductions genuinely differ vs. where the difference is just noise.

### Right panel

- **Reduction** — I(Q) (+ background overlay), S(Q)/F(Q) (with the
  tail-flatten toggle), and the selected G/g/T/R with a shaded **±1σ** band —
  same three-stacked-plot layout as Stage 1.
- **Structure Fit** — observed G(r) (±1σ band) with the fitted model overlaid,
  a `(obs−calc)/σ` residual sub-plot, and a fitted-parameter table with χ²/ndof.
- **Δ-PDF** — ΔG(r) with its uncertainty band and significant-point scatter,
  plus an "N/Ntotal points > nσ" summary label.
- **Log** — shared run log for reduction, corrections, structure fit, and
  Δ-PDF, in the same `LogPanel` used elsewhere.

### Functions behind the tab

The tab is a thin UI over two background workers and the `midas_pdf` backend.

**UI — `PDFTab` (`midas_gui/tab_pdf.py`)**

| Method | Role |
|--------|------|
| `_build_ui` | Builds the 4-tab left panel and 4-tab right panel described above. |
| `set_calibration(result, source)` | Receives a Tab-2 result (or a loaded geometry) → sets λ and enables Compute. |
| `_load_calib_file` | Loads a `paramstest`/`.json`/`.poni` → builds a calibration result → `set_calibration`. |
| `_load_img` / `_load_mask` | Loads a detector frame / local `.tif` mask for *Integrate detector frame* mode. |
| `_estimate_mu` | Fills sample/container μ (1/µm) from composition + λ (+ container density) for the Paalman-Pings correction. |
| `_check_fluorescence` | Calls `fluorescence_report_sample_and_container` and reports the result in the Log + a message box. |
| `_run` | Validates inputs, assembles the full Stage-1 + Stage-2/3 `cfg` dict, and starts a `PDFWorker`. |
| `_on_done` / `_redraw_mid` / `_redraw_bottom` | Draw I(Q)+bg, the S(Q)↔F(Q) toggle (incl. tail-flattened), and the G/g/T/R curve with its ±1σ band. |
| `_collect_fit_cfg` / `_run_structure_fit` / `_on_fit_done` / `_redraw_fit` | Build the CIF/manual crystal + fit-parameter `cfg`, run `PDFStructureFitWorker`, and draw the observed/model/residual plots + parameter table. |
| `_save_state` / `_run_delta_pdf` / `_redraw_delta_pdf` | Snapshot `(r, G, σ)` into State A/B and compute/plot `delta_pdf`/`significant_mask`. |
| `_save_gr_file` / `_save_sq_file` | Export `r,G,σ` and `Q,S`. |

**Workers — `midas_gui/workers.py`** run off the GUI thread:

| Worker / function | Role |
|----------|------|
| `PDFWorker._acquire_iq` | Produces `(q, I, σ)` — either by integrating the frame (image mode) or by reading a file (`_load_iq_file`, tolerant of comma/space and 2- or 3-columns). |
| `PDFWorker.run` | Trims to `[Qmin,Qmax]`, builds `Composition`, then runs the opt-in Stage 2-3 stages in order — background subtraction (manual or fit-scale, optional Paalman-Pings), detector efficiency, absolute normalization, multiple scattering — before the existing refine/non-refine normalization, then an optional display-only S(Q) tail-flatten; emits `q,Iq,background,S,S_flat,Fq,r,Gr,sigma_Gr,Gr_family,scale,bg_coef,refine_loss,bg_scale_used,ms_beta_median`. |
| `_fit_subtraction_scale` / `_absolute_normalize` / `_flatten_sq_tail` | Hand-written recipe helpers (numpy-only, not part of `midas_pdf` itself) for the fit-scale empty-cell mode, absolute normalization, and the tail-flatten display transform. |
| `PDFStructureFitWorker.run` | Builds a `Crystal` (from CIF via `read_cif_to_crystal`, or manually from `Lattice`/`SpaceGroup.from_number`/`Atom`), builds the pair list (`build_pair_list`), masks `(r,G,σ)` to the fit window (with `sigma_inflate`), calls `refine_structure`, and emits `fitted,uncertainty,chi2_reduced,history,r_fit,G_obs,G_calc,sigma_fit,posterior,cov`. |

Image-mode integration reuses the shared core `_build_spec`, `build_geom`,
`integrate_frame`, `axis_conversions` (same path as the other tabs); geometry-file
parsing uses `geometry_fields_from_file` / `result_ns_from_geometry_file`
(`midas_gui/helpers.py`).

**Reduction — `midas_pdf` via `midas_gui/pdf_backend.py`** (re-exported symbols):

| Function | Does | Maps to plot |
|----------|------|--------------|
| `Composition(fractions, number_density)` | Composition layer: `⟨f⟩, ⟨f²⟩` form-factor averages, Laue term `⟨f²⟩−⟨f⟩²`, and per-atom Compton. | — |
| `faber_ziman_S(I, q, comp, …)` | I(Q) → S(Q) with σ (subtract Compton/Laue, divide by `⟨f⟩²`). | S(Q) |
| `structure_function_F(q, S)` | `F(Q) = Q·(S−1)`. | F(Q) |
| `fourier_sine_transform(q, S, r, window)` | S(Q) → G(r) with σ (Lorch / none window). | G(r) |
| `i_of_q_to_Gr(q, I, comp, r, …)` | End-to-end I(Q) → G(r) (composes the two above); the default Compute path. | S(Q) + G(r) |
| `refine_normalization(q, I, comp, r, …)` | L-BFGS fit of scale + background polynomial against high-Q ⟨S⟩→1 and low-r `G=−4πρ₀r`. | refined S(Q)/G(r) + bg |
| `pair_distribution_g` / `total_correlation_T` / `radial_distribution_R` | Convention family from G(r) + ρ₀. | g/T/R |
| `paalman_pings_cylinder_in_cylinder` / `paalman_pings_cell_only` | Q-dependent self-/container-absorption correction for empty-cell subtraction. | background |
| `apply_detector_efficiency` / `detector_efficiency` / `linear_attenuation_um` | Detector-efficiency correction + the μ used by it and by Paalman-Pings/MS. | I(Q) |
| `cylinder_effective_tau` / `slab_transport_ms` / `ms_background_on_grid` | Differentiable cylinder multiple-scattering transport → MS background. | I(Q) |
| `expected_fluorescence` / `fluorescence_report_sample_and_container` | Predicted fluorescence emission lines near the incident energy. | diagnostic only |
| `read_cif_to_crystal` / `build_pair_list` / `refine_structure` | CIF loading, pair-list construction, and the gradient-based small-box structure fit. | Structure Fit tab |
| `delta_pdf` / `significant_mask` | Difference + σ-significance test between two G(r) snapshots — **require `torch.Tensor` inputs**, not numpy arrays. | Δ-PDF tab |

> **Packaging note.** `midas_pdf` is the public PyPI package (pinned in
> `environment.yml` / `pyproject.toml`), imported directly through
> `midas_gui/pdf_backend.py`. Earlier versions of the GUI carried a vendored
> copy under `midas_gui/_vendor/` plus a `midas_hkls.absorption` compatibility
> shim, needed before `midas-pdf` was published and while `midas-hkls` was
> pinned below the release that added `absorption`; both were retired once
> `midas-hkls>=0.5.0` and `midas-pdf` were available. Deliberately **not**
> re-exported (out of scope, see `.context/ROADMAP.md`): the Bayesian SVI/NUTS
> posterior path, RMC big-box refinement, and the non-differentiable
> Monte-Carlo multiple-scattering variants.

---

## 10. Tab 7 — Texture / Pole Figure  *(work in progress)*

Per-ring azimuthal analysis for preferred orientation. Controls: calibration source,
sample frame, R/η bins, ring index, χ (tilt) / φ (rotation). **Compute Pole Figure**
integrates to a cake and extracts I(η) at the selected ring; the right panel shows the
stereographic pole figure and the raw I(η). **Save pole figure (.pol)** exports POPLA
format.

---

## 11. Pump Probe (time-resolved / TR-XRD)

Analyses time-resolved (pump-probe) diffraction the way the TRR group does: a folder of
raw detector frames is pooled by a filename prefix, the pump-probe **delay** is parsed
from each name, every frame is integrated to I(q) with the **MIDAS engine** (the same
core as Batch Integrate, driven by a calibration), repeats at each delay are averaged,
and a reference (mean of the pre-time-zero / negative delays) is subtracted to give
**ΔI(q, delay)**.

**File-naming / pooling.** Frames follow `PREFIX-<fshw>fshw<delay>delay<id>.tif`, where
`PREFIX` (e.g. `Ex01_Sa01_Sc17` = Experiment / Sample / Scan) is one opaque grouping key.
**Scan folder** globs `PREFIX*.tif` in the loaded folder, parses `fshw` and `delay`
(seconds) from each name, and reports how many frames and unique delays were found. The
delay sign is flipped on load so positive delay = after the pump.

The tab uses the same three-panel layout as Batch Integrate: **data loader** (left),
**settings** (middle), **plots** (right).

**Left — data loader** (the shared loader panel, as on Calibrate / Batch):
- **Data** — the folder of raw frames. Defaults to the bundled TRR test set in
  `test_data/trr_s7id/pump_probe_BTO/detimages/` (125 Pilatus2M frames; a large,
  git-ignored local asset), so the tab is populated on open. An index range / stride
  can subset the pooled frames.
- **Dark / Bright / Background** — per-frame field corrections (compute each field, then
  it is applied to every frame before integration).
- **Mask** — a mask file and/or the mask from the Mask Builder tab. For the TRR data the
  tab pre-loads `invert_mask.tif` (0 = valid pixel, 1 = bad pixel / module gap), which
  matches MIDAS's convention that non-zero pixels are masked out.

**Middle — settings.**
- **Calibration source** — *From Tab 2* (the live calibration) or *From file*
  (`calibration.json` / `paramstest.txt` / `.poni`). Same convention as Batch Integrate.
  For the shipped TRR test data the field defaults to a ready MIDAS `paramstest`
  (`Ex01_Sa01_Sc17_midas.txt`, converted from the dataset's pyFAI/Fit2D geometry), so
  the tab integrates out of the box.
- **Data pooling (TRR)** — the pooling **prefix** + **Scan folder** (the folder itself
  comes from the loader on the left).
- **Integration** — kernel, R/η bins, the plot axis (Q / 2θ / R), and optional
  Q-uniform binning. Identical engine and options to Batch Integrate.
- **Physics corrections** — polarization / solid-angle.
- **Pump-probe options** — the reference-delay set (defaults to all negative delays;
  multi-select to override), optional per-pattern normalization over a q-window, the ΔI
  colour range (auto or fixed ±) and the diverging colormap.

**Views (right panel).** Every axis shows **real physical values**, not indices — the
radial axis is in Q (Å⁻¹), 2θ (°) or R (px) per the plot-axis selector, and delays are in
seconds.
1. **ΔI heatmap** — ΔI over the radial axis (a true, continuous Q/2θ/R axis) versus
   delay, diverging colour centred at zero, with a labelled ΔI colour-bar and the
   reference I(q) lineout beside it (shared radial axis). Delays span several decades and
   are irregular, so they are laid out as columns labelled with their real delay values.
2. **ΔI vs q** — one curve per delay, coloured along a rainbow by delay. With ≤ 8 delays
   each curve is named in the legend; with more, a continuous **delay colour-bar**
   (min → max, in seconds) replaces the legend.
3. **Kinetics** — ΔI versus delay for user-defined q-bands (**Add band** over a q range);
   x-axis as real delay (**linear**), **log** (post-t₀ delays, natural for the decade-wide
   delay range), or **rank**.
4. **Mean patterns** — the averaged I(q) at each delay plus the dashed reference, with an
   optional **±1σ band** (spread of I(q) across delays) and a **Log Y** toggle to reveal
   weak features across the full dynamic range; a signal-level / stability check.

All views use a publication-style white theme with large, clearly-labelled axes, readable
legends and a subtle grid. A toolbar above the plots sets the **draw** mode (lines /
lines+points / points) and the **line**, **point (sym)** and **font** sizes (the −/+
groups), as on the Batch Integrate tab. Pan and zoom are bounded to the data so you cannot
lose the plot area.

Integration runs off the GUI thread (`PumpProbeWorker`) with a progress bar and
**Abort**. There is no peak fitting — peak position / width / area are read from the
plots, matching the reference workflow.

---

## 12. Results & Export  *(work in progress)*

Session summary + one-click export. Checkboxes select which products (calibration.json,
paramstest.txt, mask.tif, integrated profiles, G(r), pole figures, session log) to copy
to an output directory. A provenance block (package versions, geometry hash, mask
fraction, correction flags) can be copied to the clipboard for a Methods section.

---

## 13. Common UI Conventions

- **Browse = load.** Selecting a file/folder (or pressing Enter in a path field) loads
  it immediately; there are no separate "Load" buttons. HDF5 dataset / frame-index
  changes reload automatically.
- **No accidental scroll changes.** Spin boxes and drop-downs ignore the mouse wheel —
  values change only by clicking/typing; the wheel scrolls the panel instead.
- **Readable right-click menus.** pyqtgraph plot context menus use the dark theme.
- **Dark theme, orange accent** (Dioptas-inspired); off-white text, light input fields.
- **Sample-to-detector distance is entered/shown in mm** (Data Viewer, Calibrate seed,
  Preferences ▸ Geometry). Internally — all calculations — and in written calibration
  files (`paramstest.txt`, `calibration.json`) it is always in **microns**; the mm↔µm
  conversion happens only at the display boundary. The config key remains `lsd_um` (µm).
- **Image viewer color scale (Log/cmap/vmin%/vmax%)**, shared by the Data Viewer,
  Calibrate, and Mask Builder image viewers (including Hydra mode): the colorbar's own
  zoom defaults to the vmin%/vmax% percentile window rather than the full data range,
  which a single bad pixel can otherwise stretch into an unreadable sliver. The
  percentile calculation excludes exact-zero pixels, so a mostly-empty frame (e.g. the
  Hydra Composite view's unfilled canvas background) doesn't skew the window toward
  looking washed out. Dragging the LUT region or
  zooming/panning the histogram's own axis is remembered and reapplied on redraw
  instead of resetting to the percentile defaults, and toggling Log/Linear converts a
  manually-set window into the other scale so it keeps pointing at the same data
  (rather than the same raw numbers) — and always reframes the histogram's own
  visible window tightly around the converted levels rather than carrying a
  manually zoomed/panned window through the same nonlinear conversion, which
  would otherwise leave the sliders technically in range but squeezed into a
  barely-visible sliver of the window. Loading new data — a new file, a different
  frame/dataset, a projection, or a correction/mask change — resets to the percentile
  defaults; only frame-to-frame updates within an active live stream (Data Viewer's
  Live Data card) keep a manual window fixed, matching the live-streaming behavior
  described in §3.

---

## 14. Packaging, Deployment & Diagnostics

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

## 15. Configuration & Defaults

Every default in the GUI — detector geometry, data/output paths, ring-simulation
**materials**, **calibrants**, the **pixel-preset** and **K-edge** menus, the
calibration / integration **algorithms**, and **which tabs are visible** — can be set
**without editing code**, via a single per-user JSON config that overrides the shipped
built-in defaults.

### Where it lives
One per-user file, auto-located per OS:
`~/.config/midas_gui/config.json` (Linux),
`~/Library/Application Support/midas_gui/config.json` (macOS),
`%APPDATA%\midas_gui\config.json` (Windows). If it's absent, the shipped built-in
defaults are used. Sharing between machines/users is done by exporting/importing a
JSON file (below) — copy it wherever you like.

### Profiles — Settings ▸ Preferences… ▸ Profile row
The top of the Preferences dialog has a **Profile** row —
`Profile: [combo ▼]  [New…] [Duplicate…] [Rename…] [Delete]` — for keeping several
independent sets of defaults (e.g. one per beamline) and switching between them:
- Each profile is its own config file under `<config dir>/profiles/<name>.json`; the
  active one is remembered in `<config dir>/profile_meta.json`. Existing single-config
  installs are migrated transparently into a profile named **Default** the first time
  this runs — no data is lost.
- Three beamline device presets ship bundled and appear in the combo alongside
  **Default**: **20-ID-D**, **20-ID-E**, **1-ID-E** — each differs only in its
  **Devices** list (below), so picking one just swaps the Live Data PV dropdown's
  detectors for that beamline's. They're seeded once, the first time the app runs
  on a machine; deleting one doesn't bring it back. Fresh installs still start on
  **Default** (same detectors as 20-ID-D) so existing setups are unaffected.
- **New…** seeds a blank profile from the shipped built-in defaults.
- **Duplicate…** seeds a new profile from whatever is currently shown in the dialog
  (including unsaved edits), then switches to it.
- **Rename… / Delete** — Delete refuses to remove the last remaining profile; deleting
  the active profile switches to another one first.
- Switching profiles (via the combo) prompts to confirm if the dialog has unsaved
  edits, then reloads the dialog's fields and the live `DEFAULT_*` globals from the
  new profile — most values apply immediately; a few (e.g. viewer step sizes set at
  widget-construction time) need a restart, and you're offered one.
- The "Local config: …" label under the row becomes **"Profile '\<name\>': \<path\>"**
  so you always know which file you're editing.

### Editing — Settings ▸ Preferences…
The **Settings** menu opens **Preferences…**, a dialog whose tabs are **pre-filled
with the full shipped defaults** so you edit from a complete starting point:
- **Geometry** — λ, pixel size, Lsd, beam centre.
- **Data Viewer** — the up/down-arrow step size for each field in the Data Viewer's
  Ring simulation card (λ, max 2θ, Lsd, pixel size, BC_y/BC_z, ty/tz). Shipped
  defaults: 0.01 Å, 1°, 1 mm, 0.1 µm, 1 px, 0.1° respectively.
- **Paths** — default data / calibration / output files & folders.
- **Materials** / **Calibrants** — add / remove / modify (name + lattice + SG).
- **Devices** — the detector devices offered in the Data Viewer's **Live Data**
  PV dropdown (name, prefix, PVA suffix). The live PV is built as
  `prefix + PVA suffix`. All PVA suffixes are `Pva1:Image`, plus a built-in
  **Sim Detector** entry (`midasSim:` prefix) for hardware-free testing — see
  the Live Data card section above; add / remove / edit rows for your own
  beamline's devices, or switch to one of the bundled beamline **Profiles**
  above instead of hand-editing:
  - **20-ID-D** — `20iddNF` (`20idOR1:`), `s20idPil` (`20idPil:`), `pg4`
    (`1idPG4:`), `20iddTomo` (`20idGH1s:`), `20iddFF` (`20IDFF:`).
  - **20-ID-E** — `pimega` (`PITEC:D:RAD1_5Mh:`), `spl1` (`20idsp1:`),
    `s20varex2` (`20idVarex2:`), `pg6` (`20idPG6s:`), `gh2` (`20idGH2S:`).
  - **1-ID-E** — `ge1`–`ge5` (`GE1:`–`GE5:`), `pixirad` (`s1_pixirad2:`),
    `gh1` (`1idGH1:`), `pg1` (`1idPG1:`), `pg5` (`1idSP5:`), `s1varex1`
    (`1idVarex1:`). Names match each detector's variable name in B-PILOT
    (`mpe_bluesky/instrument/devices/`), the beamline's Bluesky/ophyd device
    definitions, so entries are traceable back to source.
- **Menus** — the pixel-size presets and K-edge foils.
- **Algorithms** — default calibration pipeline, integration kernel, output format,
  error model, colormap/theme.
- **Tabs** — which tabs are visible. Data Viewer, Mask Builder, Calibrate and Batch
  Integrate are always shown (their boxes are locked on); tick/untick the rest. **Tab
  visibility applies immediately** on save (no restart), unlike the other settings.
- **Display** — the **interface scale**, a whole-application zoom (layout *and* fonts)
  for HiDPI / 4K monitors where the default looks too small. Pick a value or a preset
  (100 % ≈ 1080p, 150 % ≈ 1440p, 200 % ≈ 4K); it applies after a restart (you're
  offered one on save). Also reachable from **Settings ▸ Interface scaling…**. It is
  implemented with Qt's `QT_SCALE_FACTOR`, so the whole layout scales uniformly.

Buttons: **Save as my defaults** (write your config), **Save current GUI state**
(capture the Data Viewer's live λ/pixel/Lsd/BC into the fields), **Save config to
JSON…** / **Load config (JSON)…** (export/import a config to share), and **Reset to
shipped defaults** (delete your config). Also on the menu: **Open config folder** and
**Reload config**. Saving writes your per-user file; **changes take effect on the
next launch**.

### File format
```json
{
  "geometry": { "wavelength_A": 0.39, "pixel_um": 75.0, "lsd_um": 121000.0,
                "bc_y": 10.0, "bc_z": 10.0,
                "pixel_presets": [["Eiger", 75.0], ["Pilatus", 172.0]],
                "k_edge_foils": [["Au", 80.725], ["Pb", 88.005]] },
  "viewer_steps": { "wavelength": 0.01, "two_theta": 1.0, "lsd_mm": 1.0,
                    "pixel": 0.1, "bc": 1.0, "tilt": 0.1 },
  "materials":  { "Ni (FCC)": {"a":3.5238,"b":3.5238,"c":3.5238,"alpha":90,"beta":90,"gamma":90,"sg":225} },
  "calibrants": { "CeO2": {"a":5.4116,"b":5.4116,"c":5.4116,"alpha":90,"beta":90,"gamma":90,"sg":225} },
  "devices": [ {"name": "s20varex1", "prefix": "20IDFF:", "pva_suffix": "Pva1:Image"} ],
  "paths": { "nickel_h5": "/data/mygroup/sample.h5", "calib_file": "/data/mygroup/calibration.json" },
  "ui": { "calibration_pipeline": "one_shot", "integration_kernel": "subpixel2",
          "output_format": "csv", "azimuthal_method": "poisson", "plot_theme": "hot",
          "visible_tabs": ["Calib. Refinement", "Pump Probe"],
          "ui_scale": 1.0 }
}
```
- `ui.visible_tabs` lists the **optional** tabs to show (the four always-on tabs are
  implicit). The shipped default is `["Calib. Refinement", "Pump Probe"]` — Corrections,
  PDF Analysis, Texture and Results & Export are hidden until you add them. Edit it from
  **Preferences ▸ Tabs**.
- `ui.ui_scale` is the whole-interface zoom (0.5–4.0) applied at startup via
  `QT_SCALE_FACTOR`; ~1.5 for 1440p, ~2.0 for 4K. Edit it from **Preferences ▸
  Display** or **Settings ▸ Interface scaling…** (takes effect on restart).
- When a **list section is present it fully replaces** that built-in list — so
  `materials`, `calibrants`, `devices`, `pixel_presets` and `k_edge_foils` in the file
  are your complete lists. (The Preferences dialog pre-fills them with the shipped
  entries, so you always start from the full set; use **Reset to shipped defaults** to
  get them back.) `sg` is the space-group number.
- Geometry scalars, `paths` and `ui` are simple overrides; any section or key may be
  omitted. A malformed config is ignored (built-ins are used) rather than blocking
  startup.
- A minimal template lives at `documentation/config.example.json`; the step-by-step
  guide is `documentation/config_gui.md`.

---

## 16. File ▸ Save/Load GUI State

Beyond a saved **profile** of defaults (§15), the **File** menu can capture the
**live, in-progress state of every tab** — every field you've typed or picked, across
all 10 tabs — to one JSON file, so a session can be closed and resumed later exactly
where it left off.

- **File ▸ Save GUI State…** (`Ctrl+S`) — the first time in a session, prompts for a
  destination (default `midas_session.json`); every subsequent `Ctrl+S` **silently
  overwrites that same file** (no dialog) as long as the session hasn't loaded/saved
  a different file since. **File ▸ Save GUI State As…** (`Ctrl+Shift+S`) always prompts
  for a destination, and makes *that* file the target for future plain `Ctrl+S`. Either
  way, a completion dialog lists which tabs (if any) failed to save.
- **File ▸ Load GUI State…** (`Ctrl+O`) — asks for confirmation (loading overwrites
  every tab's current values), then pick a previously saved file. A completion dialog
  reports any tabs present in the file that no longer exist, or that failed to restore.
  The tab that was active at save time is re-selected.

### What is restored automatically
Every field (text boxes, spin boxes, checkboxes, combo/dropdown selections) in every
tab is restored as typed. In addition, **path-backed data is reloaded from disk**, the
same way it loads when you type/browse to a path by hand — images, masks-by-path,
dark/bright/background frames, and HDF5 datasets all re-read their file automatically
after a state load (guarded so a moved/deleted file is skipped quietly rather than
popping a warning).

### What is *not* re-run
Loading a state **does not re-run any long-running pipeline** — Calibrate's Fit,
Batch Integrate, the PDF transform, and Calibration Refinement all keep their inputs
restored but require **one manual click of the tab's own Run/Fit button** to reproduce
their result. This keeps a load fast and avoids silently kicking off a multi-minute
job in the background.

### Sidecar files for in-progress derived data
Two tabs can hold computed data that hasn't been exported to a file of its own yet —
a drawn/computed **mask** (Mask Builder) and a just-fit **calibration result**
(Calibrate). Saving a GUI state writes small sidecar files next to it so this
in-progress work isn't silently lost:
- `<state file stem>_mask.tif` — the current in-memory mask, if any; reloaded
  automatically on the next Load.
- `<state file stem>_calibration.json` — the last fit result's flattened fields, kept
  for the record and to reseed the manual/seed geometry fields. This does **not**
  reconstruct the in-memory fitted result object (it isn't a plain, re-loadable
  structure) — re-run **Fit** after loading to reproduce it.

*For bugs or questions, see the MIDAS GUI repository.*
