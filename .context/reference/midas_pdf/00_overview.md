# midas-pdf — Overview & Orientation

> Compressed knowledge stack for building PDF functionality in **midas-gui**.
> Built by reading the `midas_pdf` package + its real-data test project. Start here,
> then read the sibling files in order. Written 2026-07-07.

## What midas-pdf is

A **differentiable, error-propagating total-scattering / pair-distribution-function
(PDF, G(r)) pipeline**: detector pixels → I(Q) → S(Q) → G(r), with a 1σ uncertainty
carried at every step and full PyTorch autograd end-to-end. Its selling point (vs
PDFgetX3 / PDFgetN / GudrunX) is that it does **both** autograd **and** rigorous σ
propagation — so "ad-hoc scale twiddling" becomes gradient optimization, and every
G(r) point gets a validated error band.

- Author: Hemant Sharma (ANL). Version 0.1.0, BSD-3. `Development Status: 3 - Alpha`.
- Collaborators anchoring real data: Leighanne Gallington, Chris Benmore.
- All tensors are **`torch.float64`**. Q in **Å⁻¹**, r in **Å**, wavelength in **Å**.

## Design principle: thin layer, reuse the rest

midas-pdf deliberately reuses the existing MIDAS chain and only adds the missing
**polyatomic composition layer**:

```
raw pixels ──midas-calibrate-v2──▶ geometry + λ (+ covariance)
                  │
                  ▼
     midas-integrate-v2: polygon/hard I(Q), pol/SA/dark corrections, σ propagated
                  │
   midas-hkls f(Q),f′,f″ ──▶ [midas-pdf NEW] Faber-Ziman S(Q) normalization
                  │
                  ▼
   midas-integrate-v2.pdf.fourier_sine_transform ──▶ G(r) with σ
```

The one thing that did not exist anywhere: `midas_integrate_v2.pdf.normalize_to_S`
is **monoatomic** (divides by a single ⟨f²⟩). A polyatomic sample needs the
**Faber-Ziman** form with both ⟨f²⟩(Q) and ⟨f⟩²(Q) built from the composition.
midas-pdf adds that bridge (`composition.py` + `normalize.py`) plus the Δ-PDF helper,
correction physics, structure refinement, RMC, and joint SAXS+PDF.

`gr.py` is literally a re-export of `fourier_sine_transform`, `R_px_to_Q`,
`estimate_background` from `midas_integrate_v2.pdf`. So **the sine FT and the Q-axis
mapping live in midas-integrate-v2, not midas-pdf.**

## Dependencies (runtime)

- `numpy>=1.22`, `torch>=2.1`
- `midas-hkls>=0.5.0` — atomic form factors `form_factor_batch`, `Crystal/Atom/Lattice/SpaceGroup`, `absorption` (NIST μ/ρ, Z lookup), lattice torch helpers.
- `midas-integrate-v2>=0.1.0` — integration (`binning`), sine FT / Q-mapping (`pdf`), `spec`/`IntegrationSpec`.
- Optional `[bayes]`: `pyro-ppl>=1.9.0` (SVI/NUTS posteriors).
- Optional `[dev]`: pytest, matplotlib.
- **xraylib**: used at build time only to generate two JSON tables; **not needed at
  runtime**. EXCEPTION: `Composition.form_factor_averages(anomalous=True)` calls
  `xraylib.Fi/Fii` live for Cromer-Liberman f′,f″ — so anomalous mode needs xraylib.

## Environment gotchas

- Set `KMP_DUPLICATE_LIB_OK=TRUE` before importing torch (libomp double-load abort on
  macOS). `midas_pdf/__init__.py` already does `os.environ.setdefault(...)`, but the
  demo scripts set it explicitly too. The GUI should set it early.
- Editable installs in the source repo need `--config-settings editable_mode=compat`.

## Module map (what lives where)

| Module | Role | Detail file |
|--------|------|-------------|
| `composition.py` | `Composition` → ⟨f⟩,⟨f²⟩, Laue, Compton | 01 |
| `normalize.py` | `faber_ziman_S`: I(Q)→S(Q)+σ | 01 |
| `gr.py` | re-export of sine FT + `R_px_to_Q` + `estimate_background` | 01 |
| `pipeline.py` | `i_of_q_to_Gr`: I(Q)→G(r) end-to-end | 01 |
| `frontend.py` | `image_to_iq`, `image_to_Gr`: pixels→G(r) | 01 |
| `refine.py` | `refine_normalization`: differentiable scale/bg/ρ₀ fit | 01 |
| `conventions.py` | F(Q), g(r), T(r), R(r) family from G(r)+ρ₀ | 01 |
| `deltapdf.py` | Δ-PDF, significance, sequence Δ-PDF | 01 |
| `compton.py` | Hubbell incoherent + Breit-Dirac | 02 |
| `corrections.py` | detector efficiency, self-absorption, Paalman-Pings | 02 |
| `fluorescence.py` | which elements fluoresce (diagnostic) | 02 |
| `cross_section.py` | dσ/dΩ(Q) — MS engine | 02 |
| `ionic_form_factors.py` | ionic Cromer-Mann coeffs | 02 |
| `placzek.py` | X-ray note + mean atomic mass | 02 |
| `multiple_scattering.py` / `ms.py` / `ms_transport.py` | tiered MS | 02 |
| `structure.py` | PDFfit-style G(r) model + `refine_structure` | 03 |
| `aniso_refine.py` | anisotropic ADP + occupancy refine | 03 |
| `bayesian_refine.py` | SVI/NUTS posteriors | 03 |
| `multi_phase.py` | multi-phase + core-shell G(r) fit | 03 |
| `model_comparison.py` | WAIC/LOO model comparison | 03 |
| `validate.py` | Debye equation, synthetic powder image | 03 |
| `cif.py` | minimal CIF read/write → midas-hkls Crystal | 03 |
| `rmc/` | reverse Monte-Carlo (glasses/liquids) | 03 |
| `saxs/` | joint SAXS(+SANS)+PDF refinement | 03 |
| `cli/` | six console scripts | 04 |

## Conventions (defaults)

- **Faber-Ziman** total structure factor, X-ray, neutral-atom form factors — matches
  PDFgetX3 default. `S(Q) → 1` as `Q → ∞`.
- `G(r) = (2/π) ∫ Q[S(Q)−1] sin(Qr) W(Q) dQ` (this is Keen's D(r) / Egami-Billinge G(r)).
- Default window `W(Q) = "lorch"` (alt: `"none"`).
- Convention choice (FZ vs Keen; which of S/F/G/g/D/T to report) is a **collaborator
  decision** — `conventions.py` implements the whole family so it's a one-line switch.

## Source & test-data locations (this machine)

- **Package source** (moved out of TCC-blocked Downloads):
  `/Users/dbeniwal/ANL-research/midas_pdf_src/midas_pdf/` (contains inner `midas_pdf/`
  package, `examples/`, `tests/`, `dev/`, `notebooks/midas_pdf_tour.ipynb`).
- **Test project**: `/Users/dbeniwal/ANL-research/midas_pdf_src/midas_pdf_test/`
  - `ZedongZhang_wheel_1_PDF/` — 5 raw `*.vrx.h5` frames + GSAS-II masks/imctrl/gpx.
  - `output/` — the canonical worked scripts (`02_integrate_all.py` … `07_strong_demo.py`)
    and their CSV/PNG outputs. `07_strong_demo.py` is the definitive real-data recipe.
- ⚠️ macOS TCC blocks the agent process from `~/Downloads`, `~/Desktop`, `~/Documents`.
  Keep the source outside those. The demo scripts hard-code
  `/Users/hsharma/Desktop/analysis/...` paths — ignore those, use the paths above.

## Raw detector-frame format (APS `.vrx.h5`)

`h5py`: `exchange/data` shape `(1, 2880, 2880) float32`, `exchange/data_dark`,
`exchange/data_white` same. Load frame 0, subtract dark: `raw[0] - dark[0]`. Rich
`instrument/…` metadata (energy, attenuator position, slits) is present but the demos
take geometry from a separate calibration, not the HDF metadata.
