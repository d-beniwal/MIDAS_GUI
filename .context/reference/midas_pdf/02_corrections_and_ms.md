# midas-pdf — Corrections & Multiple Scattering

Correction physics that feeds the normalization. All differentiable (torch) unless flagged
**numpy/non-diff**. Two shipped data tables (generated from xraylib at build time, no runtime
xraylib): `midas_pdf/data/incoherent_hubbell.json` (`{q_grid, Z_max, S_inc[str(Z)]}`) and
`midas_pdf/data/fluor_edges.json` (98 elements). External runtime dep for absorption:
`midas_hkls.absorption` (NIST μ/ρ, element density, Z lookup).

---

## `compton.py` — Hubbell incoherent (Compton) scattering  [torch, diff]

- `breit_dirac_factor(q, *, wavelength_A, k=2) -> Tensor` — recoil factor Rᵏ.
  `R = 1/[1+(E0/mₑc²)(1−cosψ)]`, `(1−cosψ)=2(Qλ/4π)²`. `k=2` photon-counting area detector
  (default), `k=3` energy-dispersive. **GUI toggle: k.**
- `incoherent_scattering(q, elements, *, wavelength_A, fractions=None, breit_dirac=True, k=2)`
  → `Iinc(Q)=Rᵏ·Σcᵢ S_inc(Q/4π, Zᵢ)` per atom. Table indexed at Q/4π, linear interp.
  This is what `Composition.compton(method="hubbell")` calls. Same data class GudrunX/PDFgetX use.

## `corrections.py` — Q-dependent 1-D intensity corrections

- `cos_scattering_angle(q, wavelength_A) -> cosψ = 1−2(Qλ/4π)²`  [diff]
- `linear_attenuation_um(material, wavelength_A, *, density_g_cm3=None) -> float`  **[non-diff, float]**
  μ in 1/µm. `material` = element symbol OR `{element: mass_fraction}` compound. Single element
  uses tabulated density if None; **compound REQUIRES density_g_cm3** (else ValueError).
- `detector_efficiency(q, *, wavelength_A, material, thickness_um, density_g_cm3=None) -> Tensor`
  η(Q)=1−exp(−μt/cosψ) ∈[0,1], rises with Q (e.g. CdTe high-Q tilt).  [diff]
- `apply_detector_efficiency(intensity, q, *, wavelength_A, material, thickness_um, density_g_cm3=None, sigma=None) -> (I', sigma')`
  divides I (and σ) by η. **GUI toggle + inputs: material, thickness_um.**
- `flat_plate_transmission(q, *, wavelength_A, mu_um, thickness_um) -> A(Q)` — slab
  self-absorption; divide I by A. `mu_um` from `linear_attenuation_um`.  [diff]
- `paalman_pings_cylinder_in_cylinder(q, *, wavelength_A, mu_sample_um, mu_container_um,
   R_sample_um, R_container_um, n_grid=64) -> dict` **[numpy per-Q loop, non-diff]**
  Capillary absorption factors (dict of tensors: `A_s_sc, A_c_sc, A_c_c, A_s_s, two_theta_rad`).
  Empty-cell subtraction: `I_sample = [I_meas − (A_c_sc/A_c_c)·I_empty] / A_s_sc`. `n_grid` is
  accuracy/speed. Used in `07_strong_demo` for the Ni-in-Kapton capillary.
- `paalman_pings_cell_only(q, *, wavelength_A, mu_container_um, R_inner_um, R_outer_um, n_grid=64) -> A_c_c`

## `fluorescence.py` — DIAGNOSTIC only (not a correction)  [numpy]

- `wavelength_to_energy_keV(wavelength_A) -> keV`
- `expected_fluorescence(elements, *, incident_energy_keV=None, wavelength_A=None, min_yield=0.01) -> list[dict]`
  exactly one of energy/wavelength required. Each dict `{element, shell, edge_keV, line_keV, yield}`
  for K/L3 shells with edge < E0 and yield > cutoff, strongest first. Empty = clean.
- `fluorescence_report_sample_and_container(sample_comp, container_comp=None, *, incident_energy_keV=None, wavelength_A=None, min_yield=0.05) -> {sample_lines, container_lines, clean}`
- Quantitative fluorescence subtraction is out of scope → use `refine_normalization(bg_order=…)`
  refinable smooth background to absorb the baseline.

## `cross_section.py` — differential cross-section (MS engine)  [torch, diff]

- `polarization_factor(q, *, wavelength_A, polarization_fraction=0.0, plane_cos2=0.5)` — P(ψ);
  0 = unpolarized `(1+cos²ψ)/2`, →1 = polarized synchrotron.
- `differential_cross_section(q, composition, *, wavelength_A, structure_factor=None,
   include_incoherent=True, polarization_fraction=0.0, fractions=None)` = P·(⟨f²⟩·S + ⟨S_inc⟩)
  per atom (Thomson units). `structure_factor=None` → S=1 (independent-atom, usual smooth-MS choice).
- `total_cross_section(composition, *, wavelength_A, n_theta=512, include_incoherent=True,
   polarization_fraction=0.0) -> scalar` — σ = 2π∫ dσ/dΩ sinψ dψ.

## `ionic_form_factors.py` / `placzek.py`

- `ionic_form_factor(q, species) -> Tensor` (KeyError if unregistered → caller falls back to
  neutral). `is_ionic_species`, `available_ions()`, `register_ion(species, CromerMannCoeff, ...)`,
  `ION_COEFFICIENTS` dict. Form `f(Q)=c+Σaᵢexp(−bᵢs²)`, s=Q/4π.
- `placzek.py`: no Placzek fn (for X-rays subsumed by Breit-Dirac). `mean_atomic_mass_u(composition)`.

---

# Multiple scattering — tiered, all feed the `background=` hook

Everything converges on the `background=` argument of `faber_ziman_S`/`i_of_q_to_Gr`. Two routes:
Tier-1 lumped polynomial (refinable) or first-principles β(Q)·I via `ms_background_on_grid`.

## `multiple_scattering.py` — Tier 1 (lumped smooth background)  [torch, diff]
- `polynomial_basis(q, order, *, q_max=None) -> (len(q), order+1)` design matrix of (Q/Q_max)ʲ.
- `lumped_background(q, coef, *, q_max=None) -> b(Q)`; `coef` grad-tensor → refinable. Lumps
  MS+fluorescence+air. Adequate when MS is small & smooth (high-energy, thin samples). Fit via
  `refine_normalization(bg_order=…)`.

## `ms.py` — Tier 2 (single + MC) & Tier 3 (analytic double) + cylinder
- `CYLINDER_SLAB_FACTOR = 1.45`; `cylinder_effective_tau(mu_um, radius_um) -> 1.45·µ·R` (float).
- `slab_single_scattering_factor(q, *, thickness_um, mu_um, wavelength_A)` — analytic A1(Q) [diff].
- `slab_optical_params(composition, *, wavelength_A, thickness_um, number_density_A3, packing_fraction=1.0) -> (mu_um, tau, albedo)` [non-diff floats].
- `multiple_scattering_mc(composition, *, wavelength_A, tau, albedo, n_photons=200000, n_psi=90, max_events=40, seed=0) -> dict` **[non-diff MC reference]** — slab; dict `Q, psi, I_single, I_double, I_multiple, beta, n_single, n_multiple`.
- `slab_double_scattering(composition, *, wavelength_A, tau, albedo, q_max=20.0, n_psi=60, n_z=48, n_theta=96, n_phi=96, geometric_series=True) -> dict` **[diff]** — closed-form+quadrature double scattering; `geometric_series=True` extends to all orders Soper-style. dict `Q, psi, I_single, I_double, beta, beta_double`.
- `multiple_scattering_mc_cylinder(composition, *, wavelength_A, tau_radius, albedo, ...) -> dict` **[non-diff MC reference]** — capillary.
- `ms_background_on_grid(q_data, intensity, mc_result) -> b(Q)=β(Q)·I_meas` — bridge: interpolates β
  from ANY MS dict onto q_data; feed as `background=`. (β interp is numpy; grad flows through I only.)

## `ms_transport.py` — all-orders differentiable radiative transfer (slab)  [torch, diff]
- `phase_matrix(composition, wavelength_A, mu_nodes, *, n_azimuth=240) -> (K,K)`.
- `slab_transport_ms(composition, *, wavelength_A, tau, albedo, q_max=20.0, n_mu=32, n_tau=100) -> dict`
  discrete-ordinates solve (`torch.linalg.solve`), all scattering orders at once; dict `Q, mu, I_single, I_total, beta`. Converged by ~n_mu=24. **Recommended differentiable path for a cylinder** when fed `tau = cylinder_effective_tau(mu_um, R_um)` (validated vs cylinder MC to ~1-3%).

### GUI knobs for MS
Geometry slab vs cylinder; `thickness_um`/`radius_um`, `number_density_A3`, `packing_fraction`;
`tau`, `albedo`, `mu_um`; background mode = Tier-1 refinable polynomial (`bg_order`) vs
first-principles β via `ms_background_on_grid`. In practice for thin high-energy capillaries
β is ~1% (negligible) — see `07_strong_demo`.
