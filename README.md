# MIDAS GUI

A modular PyQt5 desktop application for the **MIDAS** X-ray diffraction suite —
a Dioptas-inspired, dark-themed workflow that takes you from raw detector
frames to publication-ready results: view and mask images, calibrate detector
geometry, integrate stacks (including live/streaming data), apply physics
corrections, and reduce total-scattering data to a pair-distribution function
or a texture pole figure. It drives the `midas-calibrate-v2` and
`midas-integrate-v2` analysis backends (plus `midas-calibrate`, `midas-hkls`,
`midas-distortion`) so you get their full numerical capability behind an
interactive, threaded GUI instead of scripting each step by hand.

## Installation & Running

`midas-gui` is not on PyPI — run it straight from a clone. All commands below
use `conda` to create an isolated environment with the exact package versions
the GUI is verified against (this matters: the scientific stack has tight
cross-package version constraints — see the comments in `environment.yml`).

```bash
# 1. Clone the repository
git clone https://github.com/d-beniwal/MIDAS_GUI.git
cd MIDAS_GUI

# 2. Create the environment (creates 'midas-gui' with Python 3.12 + all
#    pinned dependencies, including the MIDAS analysis backends)
conda env create -f environment.yml

# 3. Activate it
conda activate midas-gui

# 4. Launch the GUI
python launch.py
```

`launch.py` is a thin, crash-safe wrapper: if startup fails it writes a
traceback to `~/midas_gui_error.log` and shows it in a dialog (or the
console) instead of the window silently vanishing.

**Recreating the environment.** If `midas-gui` already exists (e.g. a failed
install, or `environment.yml` changed), remove it first — `conda env create`
will not overwrite an existing environment:

```bash
conda deactivate
conda env remove -n midas-gui
conda env create -f environment.yml
conda activate midas-gui
python launch.py
```

> **conda solver note:** if `conda env create` fails with an error mentioning
> `conda-libmamba-solver` / `libarchive`, add `--solver=classic` to the
> `remove`/`create` commands, or repair the base install with
> `conda install -n base -c conda-forge conda-libmamba-solver libarchive`.

> **Linux/Windows CPU note:** the pinned `torch==2.4.0` comes from pip; on
> Linux/Windows the default PyPI wheel is CUDA-enabled (large). For a lean
> CPU-only install, after the environment is created run:
> `pip install torch==2.4.0 --index-url https://download.pytorch.org/whl/cpu`

The app auto-loads bundled synthetic test data (`test_data/`) on startup when
present, so it's immediately usable after checkout — no dataset of your own
required to explore the tabs.

## What it's for

MIDAS GUI is built around one continuous workflow, and each tab hands its
result to the next automatically (no manual copying of geometry, masks, or
calibration values between steps):

```
Data Viewer (inspect) → Mask Builder → Calibrate → [Refine] → Batch Integrate
                                                              → Corrections preview
                                                              → PDF Analysis
                                                              → Texture / Pole Figure
                                                              → Pump Probe (TR-XRD)
                                                              → Results & Export
```

Typical use cases:
- **Quick-look at a beamline** — point Data Viewer at a live EPICS PVA image
  PV (or a hardware-free Sim Detector stream) and get an instant radial
  profile, no calibration required.
- **Detector calibration** — fit beam centre, sample-to-detector distance,
  tilts, wavelength and per-pixel distortion from a calibrant pattern, with
  five selectable pipelines (one-shot, first-time, four-stage, Bayesian,
  joint-cake) depending on how much you trust the seed geometry.
- **Batch reduction of a scan** — integrate a folder or HDF5 stack of frames
  to 1-D profiles (CSV/XYE/FXYE/DAT/HDF5), with drift correction, monitor
  normalization, and live monitoring of frames as they land on disk.
- **Total-scattering / PDF** — reduce I(Q) to S(Q) and G(r) with real
  composition-weighted normalization and error propagation, for local
  structure in disordered or nanoscale materials.
- **Time-resolved (pump-probe) XRD** — pool frames by delay, integrate each
  with the shared MIDAS engine, and view ΔI(q, delay) heatmaps, kinetics
  traces, and mean patterns.
- **Texture** — extract per-ring azimuthal intensity and project it as a
  stereographic pole figure for preferred-orientation analysis.

All heavy computation (calibration, integration, mask training, PDF
transforms, gain training) runs on background threads, so the GUI stays
responsive and streams log output in real time. Tab visibility is
configurable from **Settings ▸ Preferences ▸ Tabs** — the four core tabs are
always shown, the rest can be toggled on as you need them.

## Tabs

| # | Tab | Capability |
|---|-----|------------|
| 0 | **Data Viewer** | TIFF / HDF5-stack / folder / live-PVA-stream viewer; projections; geometry-free or full-geometry radial integration; simulated multi-material ring overlay |
| 1 | **Mask Builder** | Threshold, statistical auto-mask (spatial outlier, temporal constancy), drawn shapes (rect/oval/annulus/polygon/freeform), cosmic-ray rejection, calibration-based azimuthal σ-clip, learnable mask |
| 2 | **Calibrate** | One-shot / first-time / four-stage / Bayesian / joint-cake pipelines; multi-panel detector; per-coefficient distortion refinement; ring residuals |
| 3 | **Calib. Refinement** | Derivative-free Nelder-Mead optimization against an η-uniformity loss |
| 4 | **Batch Integrate** | Hard / subpixel / polygon kernels; monitor normalisation; frame stride; drift correction; live folder monitoring; publication-quality waterfall/stacked-profile plots |
| 5 | **Corrections & Physics** | Polarization, solid-angle, absorption, Compton preview; per-pixel learnable gain training |
| 6 | **PDF Analysis** | Composition-weighted I(Q) → S(Q) → F(Q) → G(r) total-scattering reduction with σ propagation and optional scale/background refinement |
| 7 | **Texture** | Per-ring azimuthal extraction and stereographic pole-figure projection (POPLA export) |
| 8 | **Pump Probe** | Time-resolved (TR-XRD) delay pooling, ΔI(q, delay) heatmaps, kinetics, and mean-pattern views |
| 9 | **Results & Export** | One-click export of calibration/mask/profiles/G(r)/pole figures + a provenance block for methods sections |

> **Status:** Tabs **0–4** (Data Viewer, Mask Builder, Calibrate, Calib.
> Refinement, Batch Integrate) are verified and ready to use. Tabs **5–9**
> (Corrections & Physics, PDF Analysis, Texture, Pump Probe, Results &
> Export) are a **work in progress** and continue to be refined.

The full user manual — every field, tool tip, and configuration option — is
in [`documentation/gui_documentation.md`](documentation/gui_documentation.md)
(also built as a PDF). A running log of every change is kept in
[`documentation/development_history.md`](documentation/development_history.md).

## Development

```bash
git clone https://github.com/d-beniwal/MIDAS_GUI.git
cd MIDAS_GUI
pip install -e ".[dev]"

# Run the (headless) test suite
QT_QPA_PLATFORM=offscreen pytest -q
```

Cutting a release is handled by `./release.sh <version>` — see
[RELEASING.md](RELEASING.md).

## License

BSD-3-Clause — see [LICENSE](LICENSE).
