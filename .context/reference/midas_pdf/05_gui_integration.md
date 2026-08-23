# midas-pdf → midas-gui Integration Notes

> **STATUS — Stage 1 implemented (2026-07-07).** The PDF tab now uses `midas_pdf`.
> - **Vendored:** `midas_gui/_vendor/midas_pdf/` (upstream 0.1.0; see `_vendor/VENDORED.md`),
>   shipped as package-data in `pyproject.toml`.
> - **Backend:** `midas_gui/pdf_backend.py` — installs a `midas_hkls.absorption` shim only
>   when the real submodule is absent (`midas-hkls` 0.4.1): `Z_for`←`anomalous.Z_for`,
>   `atomic_mass`←`placzek._ATOMIC_MASS_U` (lazy), `element_density` small table + stub,
>   `mass_attenuation_coefficient` NotImplementedError stub. Puts `_vendor` on `sys.path`;
>   re-exports `Composition, i_of_q_to_Gr, faber_ziman_S, refine_normalization,
>   structure_function_F, pair_distribution_g, total_correlation_T, radial_distribution_R`;
>   exposes `USING_VENDORED`, `SHIM_INSTALLED`, `PDF_VERSION`.
> - **Worker:** `PDFWorker` (workers.py) — I(Q) from an integrated frame (Poisson σ) or a
>   `Q,I,σ` file (`_load_iq_file`); `i_of_q_to_Gr` or `refine_normalization`; true
>   `structure_function_F`; G/g/T/R family. Verified: Ni first shell = 2.500 Å; refine loss
>   3.27, high-Q ⟨S⟩→0.94 at 150 steps.
> - **Tab:** `PDFTab` (tab_pdf.py) — I(Q)-source / Sample / Normalization / Output groups;
>   plots I(Q)+bg, S(Q)|F(Q), and G/g/T/R with a ±1σ `FillBetweenItem` band; Save G(r)/S(Q).
> - **Deferred (Stage 2–3):** CIF structure fit, Δ-PDF, multiple scattering, self-absorption
>   (the shimmed `mass_attenuation_coefficient` stub is what these would need — bump to
>   `midas-hkls>=0.5.0` and drop the vendored copy + shim).

The notes below are the original pre-implementation survey, kept for reference.

For the NEXT session (building the PDF tab). This maps the package to the GUI and records the
current state so the improvement isn't started blind. **No GUI code was written this session.**

## Current GUI PDF implementation (the thing to improve)

- `midas_gui/tab_pdf.py` — `PDFTab`. Inputs: calibration (from Tab 2 `set_calibration(result)`),
  a sample frame (image/h5), Q range (Qmin/Qmax/ΔQ), r range (rmin/rmax/Δr), window
  (lorch/none), binning (hard/polygon). Three stacked plots: I(Q)+bg / F(Q) / G(r). Save G(r)
  as `r G sigma` text. Mask from Tab 1 via `set_mask_from_tab1`.
- `midas_gui/workers.py::PDFWorker` — does the compute in a QThread. **It uses
  `midas_integrate_v2.pdf` DIRECTLY, not midas-pdf:**
  - I(Q): hard-bin the frame, `estimate_background(prof, window, percentile)`.
  - `F(Q) = Q·(I/bg − 1)` — explicitly a **"composition-free approximation"** (no form factor).
  - `G(r) = midas_integrate_v2.pdf.integrate_to_Gr_with_variance(...)` — this is the
    **monoatomic** normalization (`normalize_to_S` divides by a single ⟨f²⟩).

**The gap = the whole reason midas-pdf exists:** the current tab has no `Composition`, no
Faber-Ziman polyatomic normalization, no Compton subtraction, no differentiable refinement,
no conventions family, no Δ-PDF, no structure fitting. The F(Q) shown is a ring-position
visual aid, not a real reduced structure function.

## What to add (suggested staging — confirm scope with user first)

**Stage 1 — real normalization (core value):**
- Add a **Composition** input to the tab (element:fraction rows, or parse a formula like
  `C3H8O`; optional number_density ρ₀; ionic species allowed). Feed `Composition(...)`.
- Replace the PDFWorker G(r) path with `midas_pdf.i_of_q_to_Gr(q, I, comp, r, wavelength_A=…,
  sigma_intensity=σ_I, compton=True/toggle, q_max=…, window=…)`. Show real S(Q) and true
  `F(Q)=structure_function_F(q,S)`.
- Add a **Compton on/off** toggle (+ optional method hubbell/it94, Breit-Dirac k).
- Keep the pixels→I(Q) step via `image_to_iq`/existing integration; or accept a pre-integrated
  I(Q) file as input (the CLI/`.csv` path — column `Q,I,sigma`).

**Stage 2 — refinement & corrections:**
- "Refine normalization" button → `refine_normalization(...)` (needs ρ₀); show scale/bg + refined S,G.
- Background subtraction UI (empty-cell frame + scale, or Paalman-Pings capillary geometry).
- Detector-efficiency / self-absorption toggles (`corrections.py`).
- Fluorescence diagnostic panel (`expected_fluorescence` at the calibration energy).
- MS: Tier-1 refinable `bg_order`, or first-principles β via `slab_transport_ms` + `ms_background_on_grid`.

**Stage 3 — outputs & modeling:**
- Convention family selector (G/g/T/R + F(Q)) — one-line via `conventions.py`.
- Δ-PDF panel: two states or a sequence → `delta_pdf`/`sequence_delta_pdf` + significance mask.
- Structure fit: load CIF (`read_cif_to_crystal`), `build_pair_list`, `refine_structure`; overlay
  model G(r) + report a±σ, U_iso, χ²/ndof. (Then optionally aniso/multiphase/core-shell/RMC/SAXS.)

## Data plumbing the GUI must provide

| midas-pdf needs | Source in GUI |
|---|---|
| `wavelength_A` | calibration result (`result.wavelength_A`) |
| `IntegrationSpec` (Lsd, BC, px, RMin/RMax/RBinSize, EtaBinSize, corrections) | built from calibration (`workers._build_spec`) |
| `q, I, sigma_I` | integrate frame (existing) or load `Q,I,σ` file |
| `Composition` | **NEW UI** — element fractions / formula / ρ₀ / ions |
| `r_grid` | rmin/rmax/Δr (existing) |
| `number_density` ρ₀ | NEW UI field (needed for refine + g/T/R) |
| Crystal / CIF | NEW file browser → `read_cif_to_crystal` (structure fit) |

## Performance / threading
- Keep compute in `QThread` (as PDFWorker already does). Set `KMP_DUPLICATE_LIB_OK=TRUE`.
- Polygon geometry build is minutes — cache it; default to `hard` binning for interactivity.
- `refine_normalization` / `refine_structure` are L-BFGS (seconds); Bayesian SVI/NUTS are
  slower (~7s+) and need the `[bayes]` extra — make them opt-in.
- Everything is float64 torch on CPU; convert to numpy (`.detach().numpy()`) for pyqtgraph.

## Test data to drive the new tab (verified this session)
- `…/midas_pdf_src/midas_pdf_test/ZedongZhang_wheel_1_PDF/*.vrx.h5` — 5 raw frames.
- `…/midas_pdf_test/output/04_iq_<sample>.csv` — pre-integrated `Q,I,σ` (skiprows=1) for quick
  I(Q)→G(r) testing without the slow integration. Compositions: Ni `{"Ni":1}` (ρ₀=0.0913),
  IPA `{"C":3,"H":8,"O":1}`, Kapton `{"C":22,"H":10,"N":2,"O":5}`, CeO₂ `{"Ce":1,"O":2}`.
- Expected FCC Ni G(r) peaks: 2.49, 3.52, 4.32, 4.98, 5.57 Å (NN = a/√2, a=3.524).
- `…/midas_pdf_test/output/05_gr_Ni.csv` — reference Ni G(r) to compare against.

## Keep docs in sync
Per user memory `keep-gui-docs-updated`: update `documentation/gui_documentation.md` and
`claude/gui_plan.md` (Implementation Status) when the PDF tab changes.
