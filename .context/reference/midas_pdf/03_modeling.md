# midas-pdf — Structure Modeling, Refinement, RMC, SAXS, CLI

Advanced layer: fit a model to G(r). All error-aware (χ² with 1/σ² weights). Parameter
uncertainties from the **autograd Hessian** of χ² at the minimum: `cov = 2·H⁻¹`,
`σ = sqrt(diag(cov))`. `crystal_tensor = crystal.to_torch()` (from midas-hkls Crystal).
Every refiner needs a `PairList` from `build_pair_list(crystal_tensor, r_max=…)`.

---

## `structure.py` — PDFfit-style small-box model + refiner

- `build_pair_list(crystal_tensor, *, r_max=10.0, margin=1.0) -> PairList`
  (attrs: `dfrac (P,3)`, `zweight`, `n_uc`, `z_mean`, `i_idx`, `j_idx`).
- `pdffit_gr(crystal_tensor, r, pairs, *, scale=1.0, u_iso=None, u_aniso=None, occupancy=None,
   delta1=0.0, delta2=0.0, q_damp=0.0, q_broad=0.0, lattice_params=None, r_max=10.0) -> G_calc(r)`
  Real-space Gaussian-broadened pair sum − (−4πrρ₀ baseline), × Qdamp envelope. Distances via
  the differentiable metric tensor → grad in lattice. Peak variance σ²=(Uᵢ+Uⱼ)·sharpen+(Qbroad·r)²,
  sharpen=clamp(1−δ1/r−δ2/r², 0.05). Symmetry handled by the midas-hkls space-group expansion
  (refining the asymmetric unit respects the space group for free). `u_aniso (n_uc,3,3)` Cartesian
  ADPs, `occupancy (n_uc,)` per-site.
- `refine_structure(crystal_tensor, r, G_obs, pairs, *, sigma_obs=None, init_a=None,
   init_u_iso=0.005, init_scale=1.0, bg_order=None, steps=120, lr=0.05, n_posterior_samples=0)
   -> RefineResult`
  MVP: cubic cell (`a`), single shared `u_iso`, `scale`, optional smooth-r nuisance polynomial
  (`bg_order`). L-BFGS. `n_posterior_samples>0` → Laplace posterior (draw from N(θ_MAP,cov)).
  Result (attr-access dict): `fitted{a,u_iso,scale[,bg_coef]}`, `uncertainty{…}`, `G_calc`,
  `chi2_reduced`, `loss`, `history`, `posterior{theta_samples,G_samples,G_mean,G_std,a_samples,…}|None`, `cov`.
  (In `07_strong_demo`, FCC Ni recovers a=3.52449±0.00001 Å vs lit 3.5240.)

## `aniso_refine.py` — anisotropic ADP + partial occupancy
`refine_aniso_occupancy(crystal_tensor, r, G_obs, pairs, *, sigma_obs=None, init_a=None,
 init_u_iso=0.006, init_scale=1.0, refine_aniso=True, refine_occupancy=False, steps=200, lr=0.05,
 positive_definite_penalty=1e3) -> AnisoRefineResult`. Co-refines a, scale, per-site 6-entry
aniso ADP, optional occupancy. Positive-definite soft penalty. Helpers `u_vector_to_matrix`/`u_matrix_to_vector`.

## `bayesian_refine.py` — full posteriors (needs `[bayes]`/pyro)
- `bayesian_refine_svi(crystal_tensor, r, G_obs, pairs, *, sigma_obs, map_init=None,
   prior_widths=None, bg_order=None, n_steps=2000, lr=5e-3, n_posterior_samples=500) -> BayesianRefineResult`
  (Pyro SVI, AutoNormal guide seeded at MAP). Generalises Laplace.
- `bayesian_refine_nuts(... n_warmup=200, n_samples=500 ...)` — exact HMC/NUTS (ground truth, slow).
- `BayesianRefineResult`: `posterior_samples`, `G_samples`, `G_mean`, `G_std`, `method`,
  `diagnostic`; `.summary() -> {param:{mean,std,q05,q95}}`.

## `multi_phase.py` — mixtures & core-shell nanoparticles
- `multi_phase_gr(crystal_tensors, pairs_list, r, *, weights, u_isos, scales=None,
   lattice_params_list=None, diameters_A=None, q_broad=0.0)` = Σ wᵢ·Gᵢ(r)·γᵢ(r) (γ = finite-size
   sphere damping). `refine_multi_phase(...)` refines per-phase (a, u_iso, scale) + mixing weights
   → `MultiPhaseResult`.
- `core_shell_pdf_gr(...)` + `refine_core_shell(core_crystal, shell_crystal, r, G_obs, core_pairs,
   shell_pairs, *, init_R_core_A=30.0, init_shell_thickness_A=10.0, ...) -> CoreShellResult`
  (8 params; volume fractions derived from geometry).

## `model_comparison.py` — WAIC / LOO (torch only, no ArviZ)
`waic(...)`, `loo(...)`, `compare_models({name: InformationCriterionResult}) -> ModelComparisonResult`
(`winner`, `delta`, `se_delta`, `z`). Operates on posterior-predictive `G_samples` stacks.

## `validate.py` — model-free ground truth
- `debye_scattering_intensity(q, elements, positions(N,3), *, thermal_B=0.0) -> I(Q)` — exact
  powder-averaged Debye equation for an atom cluster (differentiable in positions).
- `synthetic_powder_image(spec, q_profile, I_profile, *, counts=5e4, seed=0, flat_detector=True)`
  → 2-D powder image (Poisson noise). Used by example 04 to make a fake detector frame.
- `interatomic_distances(positions) -> (N,N)`.

## `cif.py` — CIF ↔ midas-hkls Crystal
- `read_cif_to_crystal(path) -> Crystal` — **the GUI CIF loading path**. Handles IT number /
  H-M symbol / Hall (with alias table), occupancy, B_iso, ionic labels. Call `.to_torch()` after.
- `write_crystal_to_cif(crystal, path)`, `write_supercell_to_cif(supercell, path)` (RMC export as P1),
  `parse_cif(path) -> CIFData`.

## `rmc/` — reverse Monte-Carlo (glasses, liquids, defective crystals)
- `Supercell.from_crystal(crystal_tensor, size=(nx,ny,nz))` (dataclass: species, positions, cell).
- `rmc_refine(supercell, r_grid, G_obs, *, sigma_G=None, moves=None, n_moves=5000, u_iso=0.005,
   temperature=1.0, min_distance_A=None, chemical_potential=0.0, seed=None, ...) -> RMCResult`
  Metropolis accept/reject on χ² vs target G(r). Mutates supercell in place. `RMCResult`:
  `supercell`, `chi2_trace`, `accept_trace`, `initial_chi2`, `final_chi2`, `.acceptance_ratio`.
- `rmc_refine_ensemble(...)` (n_chains). Moves: Displace/Swap/ClusterDisplace/RigidRotation/
  Insert/Remove(GC). Analysis: `coordination_number`, `partial_g_r`, `ergodicity_diagnostics`.
- Forward model `supercell_G_r(...)` matches `pdffit_gr` convention.

## `saxs/` — joint SAXS(+SANS)+PDF refinement
Coupling: `G_calc(r)=G_bulk(r)·γ(r,D)` where D (particle diameter) is the **shared parameter**.
- `SAXSModel(shape∈{sphere,ellipsoid,cylinder}, polydispersity, n_poly_nodes=21, S_Q_model∈{None,"hard_sphere_PY"})`, `.I(q, *, D_median, ...)`.
- `joint_refine(*, crystal_tensor, r_pdf, G_obs, pairs, q_saxs, I_saxs, sigma_G=None, sigma_I=None,
   saxs_model=None, init_a=None, init_diameter_A=100.0, weights_saxs_pdf=(10.,1.), n_steps=200, lr=0.05)
   -> JointRefineResult` (fitted, uncertainty, G_calc, I_saxs_calc, chi2_pdf/saxs/total).
- `joint_refine_three_way(... q_sans, I_sans, weights=(10,10,1) ...)` — shared diameter, per-channel
  scale+background. Bayesian variants in `joint_bayesian.py`/`joint_three_way_bayesian.py`.

---

## `cli/` — six console scripts (reveal intended end-user workflows)

Shared (`_common.py`): input data files are 2-/3-col ASCII `x y [sigma]` (comments `# // @`
skipped); a `.gr` with no σ column gets a fabricated σ = 5%·|G|max + a warning (chi2 then flagged
arbitrary); **all commands print a JSON summary to stdout**; only `cif convert` and `rmc --output`
write files.

| Script | Purpose | Key args |
|--------|---------|----------|
| `midas-pdf-cif` | inspect/round-trip CIF | `info <path>` / `convert <src> <dst.cif>` / `raw <path>` |
| `midas-pdf-refine` | small-box PDF refine | `--cif --gr --r-min 1.5 --r-max 20 --u-iso 0.006 --scale 1 --steps 400 --bg-order 0 --posterior-samples 0` |
| `midas-pdf-joint` | joint SAXS+PDF (`--sans`→3-way) | `--cif --gr --saxs [--sans] --shape sphere --polydispersity 0.05 --init-diameter 100 --steps 100 --lr 0.5` |
| `midas-pdf-rmc` | reverse Monte-Carlo | `--cif --gr --size 4 --moves 2000 --move-types {displace,displace+cluster,all,gc} --sigma-A 0.05 --temperature 1 --min-distance --output out.cif` |
| `midas-pdf-multiphase` | weighted multi-phase (≥2 CIFs) | `--cif A.cif B.cif --gr --weights --diameter --steps 200` |
| `midas-pdf-coreshell` | core-shell nanoparticle | `--core-cif --shell-cif --gr --r-core 30 --shell-thickness 10 --pin-geometry` |
