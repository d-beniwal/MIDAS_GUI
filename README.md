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
the editable `-e .` install pulls them in. No separate step is needed.

> Do **not** `pip install midas_suite` into this environment: the meta-package pulls
> extra components the GUI never uses (e.g. `midas-index`, whose current sdist fails to
> build against `scikit-build-core>=0.8`) and that will abort the install.

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
