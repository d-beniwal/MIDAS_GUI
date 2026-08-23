# midas-pdf — Core API (the pixels → G(r) path)

The functions a GUI PDF tab needs first. All `torch.float64`; most accept numpy or
torch and return torch. Import surface is flat: `from midas_pdf import ...`.

---

## `Composition` — the composition layer (`composition.py`)

```python
Composition(fractions: Mapping[str, float], *, number_density: float | None = None)
```
- `fractions` = `{element_symbol: number(mole) fraction}`, need NOT sum to 1 (renormalized
  internally). Elements can be ionic (`"Ni2+"`, `"O2-"`) — see ionic handling below.
- `number_density` ρ₀ (atoms·Å⁻³): optional, only needed for the low-r slope
  `G(r→0) = −4πρ₀r`, absolute normalization, and the `conventions.py` family. S(Q)/G(r)
  themselves don't need it.
- Accessors: `.elements` (list), `.fractions` (np array), `.as_dict()`, `.number_density`.

Key methods:
- `form_factor_averages(q, *, fractions=None, wavelength_A=None, anomalous=False) -> (f_avg, f2_avg)`
  ⟨f⟩(Q)=Σcᵢfᵢ, ⟨f²⟩(Q)=Σcᵢfᵢ². Form factors from `midas_hkls.form_factor_batch`
  (Cromer-Mann IT92, argument s²=(Q/4π)²). Differentiable in Q and (if `fractions` is a
  grad tensor) composition. `anomalous=True` + `wavelength_A` adds Cromer-Liberman f′,f″
  via **xraylib** (live call): fᵢ=(f0+f′)+if″, uses |fᵢ|² and |⟨f⟩|². Small (<1.5% Ni@63keV)
  but real near edges / heavy elements.
- `laue(q, *, fractions=None) -> ⟨f²⟩−⟨f⟩²` (the polyatomic self-scattering term; →0 monoatomic).
- `compton(q, *, wavelength_A, fractions=None, breit_dirac=True, k=2, method="hubbell") -> Tensor`
  Composition-weighted incoherent intensity per atom. `method="hubbell"` (default, tabulated
  Hubbell+Breit-Dirac — see file 02) or `"it94"` (coarse midas_integrate_v2 analytic fallback).

**Ionic form factors**: if a species is a *registered* ion (`ionic_form_factors.ION_COEFFICIENTS`)
its column uses ionic Cromer-Mann coeffs; otherwise midas_hkls silently maps `"Ni2+"→"Ni"`
(neutral fallback). Registered ions: F1-, Cl1-, Na1+, K1+, Mg2+, Ca2+, Fe2+, Fe3+, Cu2+, Zn2+, Ce4+.

---

## `faber_ziman_S` — I(Q) → S(Q) with σ (`normalize.py`)

```python
faber_ziman_S(intensity, q, composition, *, wavelength_A,
              scale=1.0, compton=True, background=None,
              sigma_intensity=None, fractions=None) -> (S, sigma_S)
```
The one new normalization step. Physics:
```
I_coh(Q) = scale · [I_meas(Q) − background(Q)] − I_compton(Q)
S(Q)     = [I_coh(Q) − (⟨f²⟩ − ⟨f⟩²)] / ⟨f⟩²
σ_S(Q)   = |scale| · σ_I(Q) / ⟨f⟩²      (form factors & Compton treated noiseless)
```
- `scale`: float or grad-tensor (refinable). Multiplicative onto per-atom electron units.
- `compton`: `True`=compute+subtract composition Compton; `False`/`None`=skip; or a
  precomputed tensor shaped like q to subtract directly.
- `background`: optional lumped smooth b(Q) (Tier-1 MS+fluorescence+air), same shape as q,
  subtracted **on the measured scale** before scaling. May be a grad-tensor (refinable).
- `sigma_intensity`: optional 1σ on I; else `sigma_S` is all-zeros.
- Monoatomic reduces exactly to `midas_integrate_v2.pdf.normalize_to_S` (pinned by test).
- Shape checks: intensity, q, background, sigma_intensity, precomputed compton must all match q.

---

## `i_of_q_to_Gr` — I(Q) → G(r) end-to-end (`pipeline.py`)

```python
i_of_q_to_Gr(q, intensity, composition, r_grid, *, wavelength_A,
             scale=1.0, compton=True, background=None, sigma_intensity=None,
             q_max=None, window="lorch", fractions=None, return_S=True)
    -> (G, sigma_G, S)      # S is None if return_S=False; sigma_G zeros if no sigma_intensity
```
Composes `faber_ziman_S` then `fourier_sine_transform`. This is the main call for a GUI
that already has an integrated I(Q). Gradients flow to scale/fractions/background and
(through q) upstream geometry/wavelength.

---

## `fourier_sine_transform` (aka `G_of_r`) + Q-axis (`gr.py` → re-export from midas_integrate_v2.pdf)

```python
fourier_sine_transform(q, S, r, *, Q_max=None, window="lorch", sigma_S=None) -> (G, sigma_G)
# G_of_r is an alias of the same function.
G(r) = (2/π) ∫ Q[S(Q)−1] sin(Qr) W(Q) dQ
σ²(G) = (2/π·ΔQ)² Σ_q [Q sin(Qr) W(Q)]² σ²(S)

R_px_to_Q(R_px, *, Lsd_um, px_um, lambda_A) -> Q(Å⁻¹)     # radial pixel axis → Q
estimate_background(profile, window=51, percentile=10.0)  # rough smooth bg estimate
```
These three (`fourier_sine_transform`, `R_px_to_Q`, `estimate_background`) physically live
in **`midas_integrate_v2.pdf`** and are re-exported. `window ∈ {"lorch","none"}`.

---

## `image_to_iq` / `image_to_Gr` — detector pixels → G(r) (`frontend.py`)

```python
image_to_iq(image, spec, *, binning="polygon") -> (Q, I, sigma_I)
image_to_Gr(image, spec, composition, r_grid, *, scale=1.0, compton=True,
            background=None, q_min=0.7, q_max=None, window="lorch",
            binning="polygon", fractions=None) -> (Q, G, sigma_G, S)
```
- `spec` = a populated **`midas_integrate_v2.spec.IntegrationSpec`** (geometry, wavelength,
  RMin/RMax/RBinSize, EtaBinSize, corrections). `binning ∈ {"polygon","hard"}`.
- `image_to_iq` calls integrate-v2 polygon/hard-with-variance → 2D (η,R), then collapses η
  with a NaN-aware reduction (off-detector bins are NaN), maps R→Q via `R_px_to_Q` using
  `spec.Lsd/pxY/Wavelength`.
- `image_to_Gr` then applies a `q_min` floor (drop beam-stop/first-ring), optional `q_max`,
  and hands off to `i_of_q_to_Gr`. `spec.Wavelength` sets the Compton scale.
- ⚠️ Polygon binning geometry build is **slow** (~15-20 min for 2880²); demos cache the
  `PolygonBinGeometry` to a pickle. GUI must build once + cache, or use `"hard"` binning.

---

## `refine_normalization` — differentiable scale/background/ρ₀ fit (`refine.py`)

```python
refine_normalization(q, intensity, composition, r_grid, *, wavelength_A, number_density,
    sigma_intensity=None, compton=True, q_max=None, window="lorch", fractions=None,
    r_min_phys=1.0, q_asymptote_frac=0.25, init_scale=1.0,
    fit_background=True, bg_order=0, fit_number_density=False,
    steps=60, lr=0.2, w_lowr=1.0, w_highq=1.0) -> RefineResult
```
Replaces manual scale twiddling. Fits (scale, background polynomial coeffs, optionally ρ₀)
by **L-BFGS** against two model-free constraints:
1. high-Q asymptote: ⟨S(Q)⟩→1 over the top `q_asymptote_frac` of the Q range;
2. low-r: G(r) = −4πρ₀r for r < `r_min_phys` (below nearest-neighbour distance).

Background model is a polynomial `b(Q)=Σ cⱼ(Q/Q_max)ʲ` of degree `bg_order` (0 = constant
offset). `RefineResult` (attr-access dict) keys: `scale`, `offset` (=b at Q=0), `bg_coef`
(list), `background` (b(Q) tensor), `number_density`, `S`, `G`, `sigma_G`, `loss`, `history`.

> NOTE — absolute normalization & tail-flatten are NOT in the package. The real-data demos
> (`06`/`07_strong_demo.py`) put raw counts on per-atom scale with a **demo-local**
> `absolute_normalize()` (anchor ⟨I⟩ over a high-Q window to ⟨f²⟩+⟨S_inc⟩) and a
> **demo-local** `flatten_sq_tail()` (PDFgetX3-style robust polynomial baseline). These are
> recipes to re-implement in the GUI if wanted, not importable functions. `refine_normalization`
> is the package's principled alternative to the manual anchor step. See file 04.

---

## `conventions.py` — the output-function family from G(r) + ρ₀

Keen (2001) relations; σ propagates linearly (all linear in G at fixed r).
```python
structure_function_F(q, S, *, sigma_S=None) -> (F, sigma_F)   # F=Q(S−1), σ_F=Q·σ_S  (reciprocal space)
pair_distribution_g(r, G, *, number_density, sigma_G=None) -> (g, sigma_g)   # g=1+G/(4πrρ₀); r=0→0
total_correlation_T(r, G, *, number_density, sigma_G=None) -> (T, sigma_T)   # T=G+4πrρ₀; σ_T=σ_G
radial_distribution_R(r, G, *, number_density, sigma_G=None) -> (R, sigma_R) # R=rG+4πr²ρ₀; ∫R dr = coordination #
```
Needs ρ₀. g(r) σ blows up as r→0 (honest). RDF integral over a peak = coordination number.

---

## `deltapdf.py` — difference PDF for time-resolved / operando

```python
delta_pdf(G_a, G_b, *, sigma_a=None, sigma_b=None) -> (dG, sigma_dG)
    # dG = G_b − G_a ;  σ²(dG) = σ²_a + σ²_b  (states assumed independent)
significant_mask(delta_G, sigma_delta, *, n_sigma=3.0) -> bool tensor   # |dG| > n_sigma·σ  (σ=0 → False)

sequence_delta_pdf(G_stack(T,R), *, sigma_stack=None, baseline=0|"mean") -> (dG(T,R), sigma_dG(T,R))
significant_features(delta_G(T,R), sigma_delta, *, n_sigma=3.0) -> bool (T,R)
cluster_significant_regions(mask, r, *, min_width_points=2) -> list[list[(r_lo,r_hi)]]  # contiguous-r intervals per frame
```
The reason it lives in an error-propagating pipeline: each ΔG(r) feature can be tested
against noise. `sequence_delta_pdf` handles a stack of time/load/temperature frames;
`baseline="mean"` uses the correct reduced variance (frame is inside the mean).
(This is the *difference*-PDF of total scattering, NOT 3D-ΔPDF of single-crystal diffuse.)

---

## Minimal GUI-relevant call sequence (already-integrated I(Q))

```python
comp = Composition({"Ni": 1}, number_density=0.0913)
G, sigma_G, S = i_of_q_to_Gr(q, I, comp, r, wavelength_A=0.1839,
                             sigma_intensity=sigma_I, compton=True, q_max=21.0)
# optional: refine scale/bg first
res = refine_normalization(q, I, comp, r, wavelength_A=0.1839, number_density=0.0913,
                           sigma_intensity=sigma_I, r_min_phys=1.8, bg_order=0)
G, sigma_G, S = res.G, res.sigma_G, res.S
# optional: convention family, delta-PDF, structure fit (files 03/04)
```
