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

## Requirements

### Python environment

Create and activate the conda environment (recommended):

```bash
conda env create -f environment.yml
conda activate midas-gui
```

This installs all dependencies including editable installs of the MIDAS
analysis packages.  Edit the `pip:` section of `environment.yml` to point
to your local MIDAS monorepo or a private package index — see the comments
inside that file.

Alternatively, install into an existing environment:

```bash
pip install -e .
# Then install the MIDAS backend separately (not on PyPI):
pip install -e /path/to/MIDAS/packages/midas_calibrate_v2
pip install -e /path/to/MIDAS/packages/midas_integrate_v2
# Plus the packages already on PyPI / conda:
# midas-calibrate, midas-hkls, midas-distortion
```

### MIDAS backend packages

The following packages are required at runtime but are **not on PyPI**:

| Package | Version | Source |
|---------|---------|--------|
| `midas-calibrate-v2` | ≥ 0.3 | MIDAS monorepo `packages/midas_calibrate_v2` |
| `midas-integrate-v2` | ≥ 0.1 | MIDAS monorepo `packages/midas_integrate_v2` |
| `midas-calibrate` | ≥ 0.2 | internal / conda channel |
| `midas-hkls` | ≥ 0.4 | internal / conda channel |
| `midas-distortion` | ≥ 0.2 | internal / conda channel |

## Running

```bash
# After installation via environment.yml or pip install -e .
midas-gui

# Or without installing:
python -m midas_gui
```

## Development

```bash
# Clone and install in editable mode
git clone https://github.com/<your-org>/midas-gui.git
cd midas-gui
pip install -e ".[dev]"
```

## License

MIT
