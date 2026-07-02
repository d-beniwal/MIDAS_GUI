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

`midas-gui` is packaged like the other MIDAS sub-packages (`packages/midas_*`):
a standard `pyproject.toml`, a `tests/` suite, and a `release.sh` for cutting
versioned releases (see [RELEASING.md](RELEASING.md)). It is a front-end for the
MIDAS analysis backends, which it declares as dependencies.

## Installation

```bash
pip install midas-gui
```

This pulls the GUI stack (PyQt5, pyqtgraph, numpy/scipy/h5py/tifffile/torch) and
the MIDAS analysis backends it drives (`midas-calibrate-v2`, `midas-integrate-v2`,
`midas-calibrate`, `midas-hkls`, `midas-distortion`) from the same index that
serves the rest of the MIDAS suite.

Editable / from source:

```bash
git clone https://github.com/d-beniwal/MIDAS_GUI.git
cd MIDAS_GUI
pip install -e ".[dev]"
```

A conda environment file is also provided:

```bash
conda env create -f environment.yml
conda activate midas-gui
```

### Runtime backend packages

| Package | Version | Provides |
|---------|---------|----------|
| `midas-calibrate-v2` | ≥ 0.3.3 | differentiable calibration + integration specs |
| `midas-integrate-v2` | ≥ 0.1.0 | integration kernels, corrections, PDF, texture |
| `midas-calibrate` | ≥ 0.2.7 | v1 calibration / paramstest interop |
| `midas-hkls` | ≥ 0.4.1 | reflection generation for ring overlays |
| `midas-distortion` | ≥ 0.2.0 | shared radial-distortion model |

Once `midas-gui` is on PyPI it can be pulled in through the `midas-suite`
meta-package as an optional extra — `pip install midas-suite[gui]` — see
[RELEASING.md](RELEASING.md).

## Running

```bash
# After installation via environment.yml or pip install -e .
midas-gui

# Or without installing:
python -m midas_gui
```

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
