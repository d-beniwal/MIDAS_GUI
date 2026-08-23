# midas-pdf — End-to-End Workflows (how it's actually done)

Recipes distilled from `examples/` and the real-data `midas_pdf_test/output/` scripts.
The examples are synthetic/didactic; the `output/NN_*.py` scripts are the real beamline
pipeline. `07_strong_demo.py` is the definitive "every capability on real data" reference.

---

## A. Canonical I(Q) → G(r) (already have an integrated pattern)

```python
comp = Composition({"Ni": 1})                       # number fractions
q = torch.tensor(Q, dtype=torch.float64)            # Å⁻¹
r = torch.linspace(0.0, 12.0, 1500, dtype=torch.float64)
G, sigma_G, S = i_of_q_to_Gr(q, I, comp, r, wavelength_A=0.1839,
                             sigma_intensity=sigma_I, compton=True, q_max=float(q.max()))
```
This is `examples/03` and `output/05_pdf_nickel.py`. Trim Q first to the trustworthy window
(demos use ~[1.5, 22] Å⁻¹ — drop noisy low-Q beam-stop region and high-Q where detector
distortion bites).

## B. Detector image → G(r) (no pre-integrated pattern)

```python
from midas_integrate_v2.spec import IntegrationSpec         # or spec_from_v1_paramstest(...)
spec = IntegrationSpec(); spec.NrPixelsY = spec.NrPixelsZ = 2880
spec.pxY = spec.pxZ = 150.0; spec.Wavelength = 0.1839
spec.Lsd = ...; spec.BC_y = ...; spec.BC_z = ...            # from calibration
spec.RMin, spec.RMax, spec.RBinSize, spec.EtaBinSize = 50.0, 3200.0, 1.0, 5.0
Q, G, sigma_G, S = image_to_Gr(img, spec, comp, r, compton=True, q_min=0.8, q_max=15.0,
                               binning="polygon")           # or "hard" (fast)
```
`examples/04`. **Polygon geometry build is slow (~15-20 min @2880²)** — cache
`PolygonBinGeometry.from_spec(spec)` to a pickle (see `output/02_integrate_all.py`) and reuse.
`"hard"` binning is fast and is what the current GUI PDFWorker uses.

## C. The real 5-sample beamline pipeline (`output/` scripts, in order)

1. **Calibrate** (`01`/`03_calibrate_from_seed.py`): ring-picker seed
   (`seed_manual.json`: BC, Lsd from arc fit on the (2,2,0) CeO₂ ring) →
   `midas-calibrate-v2` → V1 params txt. Result: Lsd≈470.71 mm, BC≈(1.51, 1473.87) px
   (beam off the left detector edge), λ=0.1839 Å (Ta edge, 67.4 keV).
2. **Integrate all 5** (`02`/`04_integrate_all.py`): load each `*.vrx.h5`
   (`exchange/data[0] − exchange/data_dark[0]`), `integrate_polygon_with_variance`, NaN-aware
   η-collapse → 1-D (Q, I, σ_I) saved as `04_iq_<sample>.csv`. Validated against CeO₂ FCC peaks.
   Samples: CeO₂ (calibrant), Nickel (in carbon black), IPA (liquid), Kapton (capillary), airscatter (empty).
3. **Reduce to G(r)** (`05`→`07`): background subtraction → absolute normalize → Faber-Ziman →
   sine FT → refine → model. Detail below.

## D. Real-data reduction recipe (from `06`/`07_strong_demo.py`) — the important nuances

These steps are the **domain recipe**; several helpers are **demo-local, NOT in the package**:

1. **Empty-cell / background subtraction** (`I_corr = I_meas − s·I_empty`, σ propagated):
   - Scalar scale `s` from the high-Q tail ratio `median(I_sample/I_empty)` over ~[20,25] Å⁻¹
     (Bragg-sparse, Compton+air dominated), OR physical values (attenuator transmission ~0.10 for
     Ni behind attenuator-4; 1.0 for transmission-matched).
   - `07` uses **Paalman-Pings** `paalman_pings_cylinder_in_cylinder` for the Ni-in-Kapton
     capillary instead of a scalar: `I_sample = [I_meas − (A_c_sc/A_c_c)·I_empty] / A_s_sc`.
   - Do NOT clamp negative I_corr silently — a negative means over-subtraction (wrong empty-cell model).
2. **Absolute normalization** — put raw counts on per-atom electron units so FZ starts in the
   right basin. **Demo-local `absolute_normalize()`**: scale so ⟨I⟩ over a clean high-Q window
   (e.g. [18, 21.5]) equals ⟨f²⟩(Q)+⟨S_inc⟩(Q) from the composition. Propagate σ by the constant
   scale K (NOT elementwise ratio, which blows up near I=0). `07` optionally uses `anomalous=True`.
3. **Faber-Ziman + sine FT** with σ → `i_of_q_to_Gr(...)` (per sample; IPA gets the cylinder-MS
   `background=ms_background_on_grid(...)`).
4. **Tail-flatten** (optional, PDFgetX3-style) — **demo-local `flatten_sq_tail()`**: robust slow
   polynomial fit to S(Q) with MAD-clipping of Bragg peaks, subtract (poly−1) to force baseline→1.
   Absorbs residual empty-cell error while preserving Bragg amplitudes.
5. **Differentiable refinement** (package): `refine_normalization(..., r_min_phys=1.8, bg_order=0)`
   is the principled alternative to steps 2/4's manual twiddling (fits scale+bg to ⟨S⟩→1 & G=−4πρ₀r).
6. **Convention family**: F(Q), g(r), T(r), R(r) from `conventions.py` (+ρ₀; RHO_NI=0.0913 for FCC Ni).
7. **Structure refinement** (package): build `Crystal` (or `read_cif_to_crystal`), `build_pair_list`,
   `refine_structure(..., sigma_obs=σ_G_inflated, fit_mask r∈[1.8,9], bg_order=0/1, steps=400)`.
   Inflate σ (×20 in demos) to absorb systematic shape mismatch (χ² is systematics-dominated;
   parameter recovery of `a` is still robust because it's pinned by peak positions).
8. **Δ-PDF**: `delta_pdf(G_naive, G_refined, sigma_a=…, sigma_b=…)` + `significant_mask(n_sigma=3)`
   to show the corrections drive a statistically real change.

Output files of the recipe: `05_gr_Ni.csv` (r, G, σ), `07_strong_demo.png` (9-panel figure).

## E. Which corrections apply (the "8 corrections" taxonomy)

Gallington/Benmore taxonomy — midas-pdf ticks all 8: density (ρ₀) · background/container scale ·
Compton · constant offset/flat · self-absorption · **multiple scattering** · oblique incidence
(detector efficiency) · Lorch modification (window). MS is the differentiable+MC-validated one.

## F. CLI workflows (file 03 has full flags)

```bash
midas-pdf-refine   --cif Ni.cif --gr Ni.gr --r-min 1.8 --r-max 20 --steps 400
midas-pdf-joint    --cif np.cif --gr np.gr --saxs np_saxs.dat --shape sphere
midas-pdf-rmc      --cif glass.cif --gr glass.gr --size 5 --moves 5000 --output final.cif
midas-pdf-multiphase --cif a.cif b.cif --gr mix.gr --weights 0.5 0.5
midas-pdf-coreshell  --core-cif Au.cif --shell-cif Pd.cif --gr cs.gr --r-core 30 --shell-thickness 10
```
`.gr` = 2/3-col ASCII `r G [sigma]`. All print JSON summaries to stdout.

## G. Gotchas / honest residuals (from the real run)
- χ²/ndof is large (~1400) because counting σ ≪ systematic shape mismatch; that's expected, and
  `a` recovery + its Hessian σ are still meaningful.
- IPA G(r) had a low-r artifact from imperfect Kapton-as-empty subtraction → Paalman-Pings fixes it.
- Calibration residual strain ~335 µε is the Varex 4343CT basis-incompleteness floor (documented).
- Set `KMP_DUPLICATE_LIB_OK=TRUE` before torch import.
