# MIDAS GUI

A nine-tab PyQt5 application for the MIDAS X-ray diffraction analysis suite.
Exposes `midas_calibrate_v2` and `midas_integrate_v2` through a structured,
Dioptas-inspired workflow interface.

## Tabs

| # | Tab | Capability |
|---|-----|------------|
| 0 | **Data Viewer** | TIFF / HDF5-stack / folder viewer; projections; simulated ring overlay |
| 1 | **Mask Builder** | Threshold, statistics, drawn shapes (rect/oval/annulus/polygon/freeform), cosmic-ray rejection, learnable mask |
| 2 | **Calibrate** | one-shot / first-time / four-stage / Bayesian / joint pipelines; multi-panel detector; ring residuals |
| 3 | **Calib. Refinement** | Derivative-free Nelder-Mead on η-uniformity loss |
| 4 | **Batch Integrate** | Hard / subpixel / polygon kernels; monitor normalisation; frame stride; drift correction; waterfall viewer |
| 5 | **Corrections** | Solid-angle, polarisation, gradient; azimuthal σ-clip; learnable gain training |
| 6 | **PDF Analysis** | I(Q) → S(Q) → F(Q) → G(r) pipeline |
| 7 | **Texture** | Pole-figure integration and stereographic projection |
| 8 | **Results & Export** | CSV / XYE / FXYE / DAT / HDF5 writers |

> **Status:** Tabs **0–4** (Data Viewer, Mask Builder, Calibrate, Calib. Refinement,
> Batch Integrate) are verified and ready to use. Tabs **5–8** (Corrections, PDF
> Analysis, Texture, Results & Export) are a **work in progress** and will be updated
> in the coming weeks.

## Installation & running

`midas-gui` is **not on PyPI** — install it from source with conda:

```bash
git clone https://github.com/d-beniwal/MIDAS_GUI.git
cd MIDAS_GUI
conda env create -f environment.yml    # creates the 'midas-gui' env and installs the GUI
conda activate midas-gui
midas-gui                              # launch (equivalently: python -m midas_gui)
```

The GUI drives the MIDAS analysis backends (`midas-calibrate-v2`, `midas-integrate-v2`,
`midas-calibrate`, `midas-hkls`, `midas-distortion`). **These are installed
automatically** by `conda env create` above — they are declared in `pyproject.toml`, so
the editable `-e .` install pulls them in. No separate step is needed. `environment.yml`
pins every package to a verified-working set, so a fresh `conda env create` reproduces
a known-good environment.

> Do **not** `pip install midas_suite` into this environment: the meta-package pulls
> extra components the GUI never uses (e.g. `midas-index`, whose current sdist fails to
> build against `scikit-build-core>=0.8`) and that will abort the install.

### Recreating the environment

If a `midas-gui` environment already exists (e.g. a half-built one from a failed
install, or after `environment.yml` changed) and you want a clean rebuild, **remove it
first, then recreate** — `conda env create` will not overwrite an existing env:

```bash
conda deactivate                              # don't remove the active env
conda env remove -n midas-gui                 # delete the existing env
conda env create -f environment.yml           # rebuild from the pinned spec
conda activate midas-gui
midas-gui
```

(You can also update an existing env in place with
`conda env update -f environment.yml --prune`, but a remove + create is the most
reliable way to get an exact, conflict-free match to the pinned `environment.yml`.)

> **conda solver note:** if `conda env create` fails to start with an error mentioning
> `conda-libmamba-solver` / `libarchive` (a broken base‑conda mamba), add
> `--solver=classic` to the `conda env remove` / `conda env create` commands, or fix the
> base install with `conda install -n base -c conda-forge conda-libmamba-solver libarchive`.

> **Linux/Windows CPU note:** the pinned `torch==2.4.0` comes from pip; on Linux/Windows
> the default PyPI wheel is CUDA-enabled (large). For a lean CPU-only install, after the
> env is created run:
> `pip install torch==2.4.0 --index-url https://download.pytorch.org/whl/cpu`

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
