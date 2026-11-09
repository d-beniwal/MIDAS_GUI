# MIDAS GUI

A modular PyQt5 desktop application for the **MIDAS** X-ray diffraction suite —
a dark-themed workflow that takes you from raw detector frames to
publication-ready results: view and mask images, calibrate detector geometry
(single detectors or multi-panel Hydra arrays), integrate stacks (including
live/streaming data), apply physics corrections, reduce total-scattering data
to a pair-distribution function or a texture pole figure, and solve unknown
unit cells from diamond-anvil-cell diffraction frames. It drives the
`midas-calibrate-v2` and `midas-integrate-v2` analysis backends (plus
`midas-calibrate`, `midas-integrate`, `midas-peakfit`, `midas-hkls`,
`midas-distortion`, `midas-pdf` and `midas-zipper`) so you get their full
numerical capability behind an interactive, threaded GUI instead of scripting
each step by hand. Work is saved in a single HDF5 **project** file that carries
the session state and a FAIR provenance record.

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

**Updating the environment.** If `environment.yml` changed (e.g. after
pulling new commits) and you want to keep the existing `midas-gui`
environment rather than recreating it from scratch:

```bash
conda env update -n midas-gui -f environment.yml --prune
```

`--prune` removes packages no longer listed in `environment.yml`, keeping
the environment in sync with the file rather than just adding to it.

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

**Alternative: pip only (no conda).** `requirements.txt` mirrors
`environment.yml`'s pinned versions for a plain `venv` + pip workflow:

```bash
python3.12 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python launch.py
```

> **PyQt5 note:** `environment.yml` installs Qt bindings from conda-forge
> instead of pip's `PyQt5` wheel because the pip wheel has caused a silent
> startup hang on at least one Linux beamline workstation (missing
> `libxcb-cursor.so.0` and related xcb system libraries that conda-forge's
> build vendors but the pip wheel dlopens from the system instead). If
> `python launch.py` hangs at startup with the pip install above, either use
> the conda path instead or install the missing system libraries (e.g. on
> Debian/Ubuntu: `apt install libxcb-cursor0`).

The app auto-loads bundled synthetic test data (`test_data/`) on startup when
present, so it's immediately usable after checkout — no dataset of your own
required to explore the tabs.

## Troubleshooting

> **"Module could not be found" / DLL load errors / missing submodule
> errors (e.g. `torch`'s `fbgemm.dll`, `ModuleNotFoundError: No module named
> 'mpmath.libmp'`).** These usually mean the package was installed from a
> corrupted or incomplete pip cache entry, not a real incompatibility.
> Force a clean reinstall of the offending package and confirm it imports
> standalone before retrying the GUI:
> ```bash
> pip uninstall lib_name -y
> pip install lib_name --no-cache-dir
> python -c "import lib_name"
> ```
> If the standalone import still fails, the traceback from that last command
> is the real error to debug (it isolates the problem from midas-gui
> entirely).

## What it's for

MIDAS GUI is built around one continuous workflow, and each tab hands its
result to the next automatically (no manual copying of geometry, masks, or
calibration values between steps):

```
Data Viewer (inspect) → Mask Builder → Calibrate → [Refine] → Batch Integrate
                                                              → Batch Correction / Queue
                                                              → Corrections preview
                                                              → PDF Analysis
                                                              → Texture / Pole Figure
                                                              → Pump Probe (TR-XRD)
                                                              → Results & Export
Solve Cell (unknown-cell indexing from DAC frames) — standalone chain
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
- **Unknown-cell solving** — filter diamond/anvil and known-material spots from
  DAC frames, index the remainder ab initio, refine the cell, and inspect the
  result in 2-D/3-D reciprocal space (Ewald sphere, lattice points, volumes,
  residual diagnostics, domain comparison).

All heavy computation (calibration, integration, mask training, PDF
transforms, gain training) runs on background threads, so the GUI stays
responsive and streams log output in real time. Tab visibility is
configurable from **Settings ▸ Preferences ▸ Tabs** — the four core tabs are
always shown, the rest can be toggled on as you need them (each optional tab
also has an ✕ to close it). The Data/Controls panels on most tabs can be
minimized to a bottom ribbon for more image space.

## Tabs

| # | Tab | Capability |
|---|-----|------------|
| 0 | **Data Viewer** | TIFF / HDF5-stack / folder / live-PVA-stream viewer; projections; geometry-free or full-geometry radial integration; simulated multi-material ring overlay; peak fitting |
| 1 | **Mask Builder** | Threshold, statistical auto-mask (spatial outlier, temporal constancy), drawn shapes (rect/oval/annulus/polygon/freeform), cosmic-ray rejection, calibration-based azimuthal σ-clip, learnable mask |
| 2 | **Calibrate** | One-shot / first-time / four-stage / Bayesian / joint-cake pipelines; single detector or multi-panel Hydra; per-coefficient distortion refinement; ring residuals |
| 3 | **Calib. Refinement** | Derivative-free Nelder-Mead optimization against an η-uniformity loss |
| 4 | **Batch Correction** | Dark/bright/gain frame correction of image stacks ahead of integration |
| 5 | **Batch Integrate** | Hard / subpixel / polygon kernels; monitor normalisation; frame stride; drift correction; live folder monitoring; auto-suggested per-format output folders; publication-quality waterfall/stacked-profile plots |
| 6 | **Batch Queue** | Queue and run multiple integration/correction jobs in sequence |
| 7 | **Zarr Viewer** | Browse/plot any `.zarr.zip` — group/array tree; 1-D/2-D/3-D plots with colormap/scale/clim controls; R bin/2θ/Q/d axis conversion via the file's own REtaMap; metadata inspector |
| 8 | **Data Explorer** | Open and inspect what the pipeline wrote |
| 9 | **Corrections** | Polarization, solid-angle, absorption, Compton preview; per-pixel learnable gain training |
| 10 | **PDF Analysis** | Composition-weighted I(Q) → S(Q) → F(Q) → G(r) total-scattering reduction with σ propagation and optional scale/background refinement |
| 11 | **Texture** | Per-ring azimuthal extraction and stereographic pole-figure projection (POPLA export) |
| 12 | **Pump Probe** | Time-resolved (TR-XRD) delay pooling, ΔI(q, delay) heatmaps, kinetics, and mean-pattern views |
| 13 | **Results & Export** | One-click export of calibration/mask/profiles/G(r)/pole figures + a provenance block for methods sections |
| 14 | **Solve Cell** | Unit-cell solving from DAC diffraction frames: ingest → diamond/anvil and optional multi-material filter → blind ab-initio indexing → free UB/cell refinement; PyVista 3-D reciprocal-space views, diagnostics, spot inspector, contamination and domain-comparison tabs |

Tab numbers shift with Preferences ▸ Tabs visibility. Out of the box, Data
Viewer, Mask Builder, Calibrate, Batch Integrate, Calib. Refinement, Batch
Queue, Zarr Viewer and Pump Probe are shown; the others ship hidden.

> **Status:** Data Viewer, Mask Builder, Calibrate, Calib. Refinement, Batch
> Integrate and Zarr Viewer are verified and ready to use. Batch Correction,
> Data Explorer, Corrections, PDF Analysis, Texture, Pump Probe, Results &
> Export and Solve Cell are a **work in progress** and continue to be refined.

## Projects

**File ▸ Project** saves and reopens a whole session (paths, calibration,
masks, integration settings, and for Solve Cell the cached visualization
products) in one HDF5 file, with a FAIR provenance history of every calibrate
and integrate run. The active project name is shown in the status bar.

## Scripting and agent control (MCP)

Setting `MIDAS_GUI_CONTROL=1` when launching opens a local control socket so a
script or LLM agent can drive the live app (Data Viewer, Calibrate, Solve
Cell). The proxy and protocol are documented in
[`midas_gui_mcp/README.md`](midas_gui_mcp/README.md). It is off by default.

The full user manual — every field, tool tip, and configuration option — is
in [`documentation/gui_documentation.md`](documentation/gui_documentation.md)
(also built as a PDF). Solve Cell's 3-D views are described in
[`documentation/solve_cell_visualizations.md`](documentation/solve_cell_visualizations.md).
Change history lives in `git log`; a longer narrative is in
[`documentation/development_history.md`](documentation/development_history.md).

## Development

```bash
git clone https://github.com/d-beniwal/MIDAS_GUI.git
cd MIDAS_GUI
pip install -e ".[dev]"

# Run the (headless) test suite. Some Qt test files can crash when run in the
# same process, so if a combined run aborts, run those files separately.
QT_QPA_PLATFORM=offscreen pytest -q
```

Cutting a release is handled by `./release.sh <version>` — see
[RELEASING.md](RELEASING.md).

## License

BSD-3-Clause — see [LICENSE](LICENSE).
