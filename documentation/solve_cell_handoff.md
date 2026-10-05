# Solve-Cell Tab — Implementation Handoff

**Audience**: the agent/developer implementing a new "Solve Cell" tab in this repo (`MIDAS_GUI`).
**Author context**: this document was produced by an agent working in the beamtime data directory
(`S3ID_data/2026-2`) that *performed* the analyses described below, interactively, over several
Claude Code sessions. That agent had no access to this repo's internals; this document is the
bridge. Everything under "Methodology" was extracted by re-reading the actual scripts/outputs of
two completed analyses — it is a specification of **already-validated, already-run logic**, not a
proposal. Numbers, thresholds, and file schemas quoted below are real, not illustrative.

## 0. TL;DR for whoever implements this

- Goal: turn an interactive, judgment-heavy, agent-driven X-ray diffraction "solve the unit cell
  of an unknown crystal in a DAC" workflow into a **deterministic pipeline + GUI tab**, so it can
  be re-run without an LLM in the loop.
- Decided scope (user confirmed): the tab must **both execute new runs and browse/replay existing
  results** ("Run + browse (full)"), and must cover the **full methodology including multi-crystal
  separation** (not just a single-crystal MVP).
- The two reference analyses are in a *different* project directory
  (`/home/beams0/DBENIWAL/S3ID_data/2026-2/analysis/{spinel_DAC_solve_cell,Ge_oP32_c1_solve_cell,Ge_oP32_c2_lineX_solve_cell}/`).
  **Do not move or edit those** — they are the validated reference implementation and regression
  fixtures. Read them for ground truth if anything below is ambiguous.
- This repo already has the right shape for this: a `QThread`-worker-per-backend-call pattern
  (`midas_gui/workers.py`), one-file-per-tab (`midas_gui/tab_*.py`), and existing MIDAS backend
  packages installed as libraries (`midas_calibrate_v2`, `midas_hkls`, ...). The solve-cell
  pipeline should be packaged the same way: a new installable backend package (e.g.
  `midas_solve_cell`) built on top of two *already-existing* libraries the reference analyses
  depend on — `midas_defect` and `midas_hkls` — with the tab itself staying a thin PyQt5/pyqtgraph
  front end that builds a config dict, runs it on a worker thread, and renders the JSON/CSV it
  gets back.
- This is a big feature. §10 proposes a phased build order. Build Phase 1 (single-crystal,
  single-panel-known-geometry solve) end-to-end and working in the GUI before adding the
  multi-domain separation and full diagnostics suite — even though the *document* covers
  everything, the *code* should land in reviewable slices.

---

## 1. Why this exists / the problem being solved

Two completed analyses (`spinel_DAC_solve_cell`, `Ge_oP32_c1_solve_cell` + its `c2_lineX` sibling)
determine an **unknown crystal unit cell** from raw X-ray diffraction frames collected through a
diamond anvil cell (DAC), at one or more detector-panel positions, with **no prior cell, no prior
orientation, and (for Ge-oP32) multiple crystals + diamond + ruby all contributing spots to the
same frames**. Both were done as long interactive Claude Code sessions: a human-in-the-loop agent
wrote ~20-30 numbered Python scripts per analysis, ran them, inspected output, hit dead ends
(degenerate ab-initio bases, a spurious physically-impossible cell, mis-attributed orientations),
diagnosed *why*, and tried a different method — sometimes bugs, sometimes genuine physics (e.g. a
real zero-frame acquisition gap that looked like a bug until checked against the master schedule
table).

The underlying **algorithms** that eventually worked are fully deterministic numerical code (numpy/
scipy/torch least-squares fits, FFT-based Patterson search, Monte Carlo null tests, etc.) — nothing
in the final adopted pipeline requires an LLM. What *was* agentic was the **decision tree**: which
method to try, how to tell a real result from an artifact, when a null hypothesis test says "stop."
That decision tree is now understood well enough (having been run to completion twice, on two
different crystal systems and DAC configurations) to encode as an explicit pipeline with configurable
thresholds and fallback branches — which is exactly what this handoff specifies.

## 2. What already exists — read-only reference material

Do **not** modify these. They live in a different machine-visible project tree than this repo:

```
/home/beams0/DBENIWR/S3ID_data/2026-2/analysis/
├── spinel_DAC_solve_cell/        # single crystal, 6 panels, cubic spinel, known composition
│   ├── scripts/00_resolve_panels.py ... 19_selfcal_new_panels.py   (20 scripts)
│   ├── geometry/panels.json
│   ├── panel_NN_*/data/spots_g*.csv
│   ├── pooled/*.json *.csv
│   ├── figures/*.png
│   ├── notebooks/*.ipynb         (interactive Plotly explorers)
│   └── REPORT.md                 # master narrative, read this first
├── Ge_oP32_c1_solve_cell/        # 4 crystals + ruby + diamond in one DAC, orthorhombic Pbcm
│   ├── scripts/00_resolve_panels.py ... 28_build_crystalBC_notebooks.py   (29 scripts)
│   ├── geometry/, panel_NN_*/data/, pooled/, figures/, notebooks/, documents/
│   └── REPORT.md
└── Ge_oP32_c2_lineX_solve_cell/  # 40-position line scan, assignment-only against 4 known UBs
    ├── scripts/00_resolve_geometry.py ... 06_build_spatial_map.py   (9 scripts)
    ├── geometry/, reference_panel/, line_positions/pos_01..40/, pooled/, figures/
    └── (no REPORT.md yet — see DECISIONS.md 2026-10-04b in that project)
```

External library dependencies these scripts actually call (the GUI's backend package should sit on
top of these, not reimplement their math):

- **`midas_defect`** (editable install, source at `~/github/MIDAS/packages/midas_defect`): raw-frame
  ingest (`ingest.py`: `live_frames`, `build_mask`, `choose_sectors`, `subtract_background`,
  `find_blobs_3d`), detector forward/inverse geometry (`geometry.py`: `Geometry` dataclass,
  `pixel_to_qlab`, `qlab_to_qsample`, `qsample_to_qlab`, `qlab_to_pixel`, `ewald_crossing_omegas`,
  `detector_angle_maps`), self-calibration (`selfcal.py`: `selfcalibrate_from_crystals`).
- **`midas_hkls`** (installed package, v0.16.0): ab-initio indexing (`ab_initio.index_ab_initio`),
  UB refinement (`ub_refine.refine_ub_from_gvectors`, `cell_constrained.refine_cell_constrained`),
  primitive→conventional cell (`conventional.to_conventional`), symmetry (`lattice_symmetry.
  holohedry_from_fit`), distortion-mode gates (`ab_splitting.{ab_separable,shear_separable,
  distortion_condition}`), strain (`cell_series.cell_deformation`), structure factors
  (`structure_factor.{structure_factors,structure_factor_intensity}`), Lorentz-polarization
  (`intensity.lorentz_polarization`), plus top-level `niggli_reduce`, `SpaceGroup`, `Lattice`,
  `generate_hkls`, `Atom`, `Crystal`, `ub_to_cell`.
- **`gemmi`**: independent space-group/systematic-absence cross-check (`gemmi.SpaceGroup(n)
  .operations().systematic_absences(hkl_array)`).
- **torch** (CPU is fine): `midas_defect.geometry` is implemented with PyTorch tensors for autograd
  (used by the tx/tilt refinement fits); `scipy.optimize.least_squares` / `scipy.spatial.transform.
  Rotation` for the self-cal and orientation-search fits.

This repo (`MIDAS_GUI`) already depends on sibling MIDAS packages the same way
(`midas-calibrate-v2`, `midas-integrate-v2`, etc., in `requirements.txt`/`environment.yml`) — add
`midas_defect` and `midas_hkls` (and `gemmi`) to that same dependency set.

## 3. Scope decisions already made (do not re-litigate without the user)

| Decision | Answer |
|---|---|
| Run new analyses from the GUI, or just browse old ones? | **Both.** The tab must configure+launch new solve-cell runs AND load/inspect completed ones (its own or ones produced by the CLI pipeline directly). |
| How much of the methodology for v1? | **Everything**, including multi-crystal/multi-domain separation (crystal B/C discovery, the 24-orientation-relabeling check, the 4th-domain negative-result search) and the full diagnostics suite (holohedry ladder, completeness, spot quality, structure factors). Not just the single-crystal happy path. |
| Where does this handoff doc live? | Here: `documentation/solve_cell_handoff.md` in this repo. |

Still open — flag these to the user before/while implementing (see §9 for the full list); don't
silently guess on anything load-bearing.

## 4. How this fits the existing MIDAS_GUI architecture

(Full detail gathered by reading this repo's own `tab_calibrate.py`/`workers.py`/`app.py`/
`constants.py`; cite these files directly rather than re-deriving conventions.)

### 4.1 Framework facts

- **PyQt5** (`5.15.10`), **pyqtgraph** (`0.14.0`) for all plotting — not matplotlib (matplotlib is
  present only for its named colormaps). A new tab's figures (reciprocal-space scatter, real-space
  overlays, residual plots, bar charts for holohedry ladders) should use `pg.PlotWidget` /
  `pg.ScatterPlotItem` / `pg.ImageView`-family widgets, following `midas_gui/widgets.py`'s existing
  `ProfileViewer`/`CakeViewer`/`PickableImageViewer` patterns. 3-D reciprocal-space views (used
  heavily in the reference notebooks via Plotly) have no existing pyqtgraph equivalent in this repo
  — `pyqtgraph.opengl` (`GLScatterPlotItem`) is the natural choice; confirm it's an acceptable new
  sub-dependency, or render fixed orthogonal 2-D projections (qx-qy, qx-qz, qy-qz) as a fallback,
  matching the reference analyses' own static-PNG fallback for the same data.
- **No stdlib `logging`** anywhere in `midas_gui/` — all progress output goes through a `LogPanel`
  (`QPlainTextEdit`) fed by redirecting the worker's `sys.stdout`/`sys.stderr` to a
  `_LogStream(self.log_line)` Qt-signal-emitting stream (see `workers.py:CalibrationWorker`,
  `tab_calibrate.py`'s `_log` usage). The new backend package should just use plain `print()` for
  progress narration (exactly like the reference scripts already do — `print(f"Phase 1: ...")`
  etc.) and let the worker's stdout redirect carry it into the GUI; don't add a logging framework.
- **No emojis** anywhere in UI text or code (repo-wide rule).
- Qt idioms to follow (`.context/ARCHITECTURE.md` in this repo, confirmed in code): no
  mouse-wheel-driven spinbox changes (`_NoScrollSpinBox`/`_NoScrollDoubleSpinBox`/
  `_NoScrollComboBox` in `helpers.py`), workers and `pg.SignalProxy` objects always stored as
  instance attributes (else garbage-collected mid-run), `setColorMap()` not `setLookupTable()`,
  `setXRange()` not `autoRange(axes=...)`.

### 4.2 Registering the new tab (exact steps)

1. Create `midas_gui/tab_solve_cell.py` with `class SolveCellTab(QtWidgets.QWidget)` — one file,
   one class, matching every existing tab (`tab_calibrate.py` → `CalibrationTab`, etc.).
2. Import it in `midas_gui/app.py` alongside the existing `from midas_gui.tab_X import XTab` block
   (currently lines 66-77).
3. Construct it through the existing defensive `_tab(factory, name)` helper used for all 12 current
   tabs (`app.py` lines 219-230) — this means a crash during `SolveCellTab.__init__` degrades to an
   error-placeholder widget instead of killing the whole app at startup, matching every other tab.
4. Add `(self._solve_cell_tab, "Solve Cell", False)` to the `_tab_specs` list (`app.py` lines
   237-250) — `False` because this should start as an **optional** tab (see `ALWAYS_TABS` vs.
   `OPTIONAL_TABS` in `constants.py:260-269`), toggleable from Preferences like Zarr
   Viewer/Corrections/PDF/Texture/Pump Probe/Export already are. Given its WIP-scale complexity,
   following their precedent (shown as "work in progress" in the README's tab table) is
   appropriate until it's validated.
5. Add the exact string `"Solve Cell"` to `constants.OPTIONAL_TABS` — **the string must match
   exactly** between `_tab_specs` and `constants.py` (explicit comment at `constants.py:257`); it
   also auto-populates a Preferences checkbox for free via `prefs_dialog.py:292,295`.
6. Add `"Solve Cell"` to the README.md tab table as "work in progress," matching the existing
   convention for 6-11.

### 4.3 Backend invocation pattern — one `QThread` worker per long operation

Follow `workers.py:CalibrationWorker` (lines 875-956) exactly as the template:

```python
class SolveCellWorker(QtCore.QThread):
    log_line = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal(object)     # the result dict/dataclass
    failed = QtCore.pyqtSignal(str)          # full traceback text

    def __init__(self, stage: str, cfg: dict, parent=None):
        super().__init__(parent)
        self._stage = stage      # e.g. "resolve_geometry", "ab_initio", "assign_refine", ...
        self._cfg = cfg

    def run(self):
        import sys, traceback, io
        class _LogStream(io.TextIOBase):
            def __init__(self, emit): self._emit = emit
            def write(self, s):
                if s.strip(): self._emit(s.rstrip("\n"))
                return len(s)
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = _LogStream(self.log_line.emit)
        try:
            from midas_solve_cell import pipeline
            result = pipeline.run_stage(self._stage, self._cfg)
            self.finished.emit(result)
        except Exception:
            self.failed.emit(traceback.format_exc())
        finally:
            sys.stdout, sys.stderr = old_out, old_err
```

Given solve-cell is naturally a **multi-stage pipeline** (geometry → ingest → index → refine →
classify → [multi-domain] → diagnostics), prefer **one worker per stage** invoked in sequence from
the tab (each stage's `finished` signal enables the "Run next stage" button and populates that
stage's panel) over one giant worker running everything — this mirrors how the reference analyses
themselves are structured as independently-resumable numbered scripts (several of which, e.g. the
geometry-resolution script, are explicitly checkpointed to disk — see §6.1 — specifically so a crash
partway through doesn't lose completed work). It also lets "browse existing results" reuse the exact
same per-stage result-rendering code as "run a new stage," just fed from a loaded JSON/CSV instead
of a `finished` signal.

If any single stage can run long (the reference ab-initio/orientation-search stages can take
minutes; a 12-panel ingest can take longer), the in-process `QThread` pattern above is still right —
reserve the `screen`-session-detached-subprocess pattern (`job_queue.py`/`batch_cli.py`, used only
by Batch Queue) for the rare case a solve-cell run needs to survive the GUI closing, which is not
expected to be the common case here (batch integration of e.g. hundreds of files is a different
problem shape).

### 4.4 Error handling

Route every worker `failed` signal through the existing shared `dialogs.show_error(self, "Solve
Cell: <stage> failed", msg, log=self._log, log_prefix="\nERROR:\n")` helper (`dialogs.py:17`) — do
not build a bespoke error dialog. Use plain `QtWidgets.QMessageBox.warning(...)` for pre-flight
validation only (e.g. "no panels configured," "geometry not yet resolved for this panel") before a
worker is even started, matching `tab_calibrate.py`'s `_run()` pattern.

### 4.5 Input/config widgets

- Reuse `DataLoaderPanel` (`widgets.py:3711`) wherever the tab needs to point at a raw HDF5
  file/folder for ingest — it already handles file/folder/HDF5-dataset selection uniformly across
  tabs.
- For everything else (tolerances, thresholds, panel geometry fields, candidate UB selection), use
  the same widget-factory conventions as Calibrate: `_fspin(lo, hi, dec, val, suf, step)` for numeric
  spinboxes (`helpers.py:2563`), `_NoScrollComboBox` for dropdowns, `QCheckBox` for booleans,
  `QFileDialog.getExistingDirectory`/`getOpenFileName` for paths. Pack the full set of a stage's
  parameters into one `cfg: dict` right before launching that stage's worker — this dict *is* the
  parameter contract (see §6 for exactly what each stage's cfg/output should contain, derived
  directly from the reference scripts' actual inputs/outputs).
- A **panel table** (add/remove/edit rows of `{det_x, eiger_y, eiger_z, raw_path, geometry_source}`)
  is needed since every reference analysis operates over N detector panels, not one file — this has
  no existing analog in another tab; build it as a `QTableWidget` or a repeated-row custom widget.
- A **candidate-UB list** (for assignment-based indexing and multi-domain work — "known UBs to test
  candidates against," each with a name/label, e.g. `crystal1`, `crystalB`, `crystal2_ref`) likewise
  has no existing analog; needed for Phase 3/4 (see §5).

### 4.6 Output/results display

- Parameter/result grids: follow `_populate_param_grid` (`tab_calibrate.py:2290`) — a `QGridLayout`
  of labeled values, not a `QTableWidget`, for scalar result fields (refined cell, volume ratio,
  holohedry verdict, etc.), with a muted "(not refined)"/"(fixed)" treatment for inputs vs. outputs,
  and 1σ shown per-row where available.
- Per-reflection tables (indexed spots, classified candidates): `QTableWidget` or a
  pandas-DataFrame-backed table model, sortable/filterable by category — needed because every stage
  ultimately produces a CSV of per-candidate rows (`spots_g.csv`, `candidates_classified.csv`,
  `indexed_spots_combined.csv`, etc.) that a user will want to inspect, not just view as a plot.
- Figures: see §4.1 — 2-D projections and bar/scatter diagnostics via pyqtgraph; consider
  `pyqtgraph.opengl` for the 3-D reciprocal-space view the reference Plotly notebooks provide
  (hover-pick on a spot to see its hkl/residual is a core piece of the reference interactive
  notebooks' value — try to preserve at least a simplified version of that interactivity, e.g.
  click-to-inspect via pyqtgraph's own point-click signals).
- A results-browsing mode (loading a *previously completed* solve-cell folder, whether produced by
  the GUI or by the original CLI scripts) should parse the same JSON/CSV schema (§6) and populate the
  identical display widgets used for a live run — build the "render stage N's result" function once
  and call it from both the worker's `finished` handler and a "load existing run" file-picker action.

### 4.7 Cross-tab / project integration

- Implement `set_project_context(ctx)` if solve-cell runs should be logged into this repo's FAIR
  provenance system (`project.py` — append-only `/analysis/<kind>/<key>/attempt_NNNN` records). This
  is a good fit: each stage-run is naturally one provenance "attempt" (full params + result +
  resolved input-path hashes).
- A solve-cell result is logically downstream of nothing else in this GUI (Calibrate/Mask produce
  detector geometry/mask for *integration*, not for *per-crystal indexing*) — no obvious signal
  wiring into `calibrationDone`/`maskReady` is needed, though reusing an already-calibrated detector
  geometry (Lsd/BC/tilt) as a *starting point* for a solve-cell panel's geometry could be a nice
  follow-on (flag to the user, don't build unasked).
- Register its own `DataLoaderPanel` with `bind_registry` (`data_bridge.py`) if/when it loads a raw
  dataset independently, so other tabs can "Import from… Solve Cell" the same way they do for
  Calibrate/Batch today.

---

## 5. Methodology — the deterministic pipeline to implement

This is the actual algorithm specification, organized as **pipeline stages**, each corresponding to
roughly one GUI sub-page/panel and one backend-package module. Everything here was read directly out
of working, already-validated code — not reconstructed from memory. Code shown is close to verbatim
(trimmed for space); exact file/line references are in the two source analyses if more detail is
ever needed (cite `spinel_DAC_solve_cell/scripts/NN_*.py` / `Ge_oP32_c1_solve_cell/scripts/NN_*.py`).

### 5.0 Shared conventions across every stage

- **g-vector convention split — the single most common bug class.** Ingest produces
  `g = 2π/d` (stored as `qlab_*`/`qsample_*` columns). `midas_hkls.ab_initio.index_ab_initio(...,
  two_pi=True)` accepts the 2π convention on input **but its `.UB` output is always in the `1/d`
  convention regardless of the `two_pi` flag** (a confirmed gotcha — verified by checking `UB@hkl`
  against `g/(2π)` directly). `refine_ub_from_gvectors`/`refine_cell_constrained` always expect
  `g=1/d` on input. **Decide one internal convention for the backend package and document the
  boundary explicitly at every call into `midas_hkls`** — getting this wrong silently produces a
  cell wrong by exactly `2π` and a bogus triclinic verdict (this exact failure happened once in the
  reference work).
- **The "fixed-cell consistency" / assignment test** — the single most-reused primitive in the whole
  project (panel-geometry validation, multi-domain assignment, line-scan UB assignment):
  ```python
  hkl_float = (np.linalg.inv(UB) @ g.T).T
  resid = np.max(np.abs(hkl_float - np.round(hkl_float)), axis=1)   # Chebyshev distance to nearest integer hkl
  keep = resid < tol
  fraction_consistent = np.mean(keep)
  ```
  `tol = 0.25` is the project-wide standard; `0.15` was used only for one orientation-*search*
  stage (Ge-oP32 line scan, §5.4) where a tighter tolerance was needed to keep the null-hypothesis
  test clean. Expose this tolerance as a configurable parameter, not a hardcoded constant.
- **The 24-proper-axis-relabeling orientation comparison** — needed to compare two independently
  ab-initio-fit orientations with no fixed axis-labeling convention (ab initio can report the "same"
  lattice with a/b/c permuted or sign-flipped):
  ```python
  def U_from_UB(UB):   # polar decomposition -> nearest proper rotation
      Usvd, _, Vt = np.linalg.svd(UB)
      R = Usvd @ Vt
      if np.linalg.det(R) < 0:
          Usvd[:, -1] *= -1
          R = Usvd @ Vt
      return R

  ALL_PROPER_OPS = [np.diag(signs) @ permutation_matrix(perm)
                     for perm in itertools.permutations([0, 1, 2])
                     for signs in itertools.product([1, -1], repeat=3)
                     if np.linalg.det(np.diag(signs) @ permutation_matrix(perm)) > 0]  # 24 of 48

  def misorientation_deg(U1, U2, ops=ALL_PROPER_OPS):
      best_cos = max((np.trace(U1.T @ S @ U2) - 1) / 2 for S in ops)
      return np.degrees(np.arccos(np.clip(best_cos, -1, 1)))
  ```
  These 24 matrices are the proper (det=+1) signed-permutation symmetries of a rectangular box — a
  purely bookkeeping correction for ab-initio's arbitrary axis labeling, **not** a crystallographic
  point-group statement (a general `point_group_ops(holohedry_system)` variant exists for the
  narrower, physically-meaningful "two panels of the *same* crystal, how misoriented" check:
  triclinic→identity only, monoclinic→2 ops, orthorhombic→4 ops `diag(±1,±1,±1)` w/ det=+1,
  tetragonal→8, cubic→the same 24).
  **No single numeric threshold universally separates "same crystal" from "new domain" from "noise"**
  — judgment calls were made by inspecting the empirical gap each time (~7-9° = contamination,
  ~29-36° with tight mutual agreement = genuine new domain). **Expose this as a user-configured
  threshold with the observed reference ranges as UI hints/defaults, not a hardcoded cutoff.**
- **Numeric thresholds table** (every constant that appeared in the reference code, to expose as
  configurable defaults):

  | Constant | Value | Controls |
  |---|---|---|
  | `ASSIGN_HKL_TOL` (standard) | 0.25 | geometry acceptance, assignment indexing (all stages) |
  | `ASSIGN_HKL_TOL` (orientation-search stage only) | 0.15 | tighter tolerance during a declared-cell random-rotation search |
  | `ACCEPT_FRAC_OF_REFERENCE` | 0.7 | panel-geometry acceptance band (ceria-regression candidate vs. known-panel reference) |
  | `SIGMA_G` | 5e-3 Å⁻¹ | g-vector uncertainty proxy fed to every ab-initio/refinement call |
  | `MIN_REFLECTIONS` (ab initio) | 20 | refuse indexing below this count |
  | `min_excess_sigma` (Patterson candidate) | 3.0 | Poisson-significance floor over chance |
  | `subset_tolerance` (basis selection) | 0.75 | "comparable indexed subset" band when picking smallest-volume basis |
  | `max_supercell_ratio` | 100.0 | last-resort supercell guard |
  | `SEARCH_RADII_PX` (self-cal fallback) | (25, 10, 5) | shrinking-radius forward-match iteration |
  | `VALIDATION_TOL` (self-cal gate) | bc_px=4.0, lsd_rel=0.003–0.012 (dataset-dependent), tilt_deg=0.02 | pass/fail gate before trusting self-cal on unknown panels |
  | `CONTAM_TOL_DEG` (diamond) | 0.07° | 2θ-proximity diamond-line exclusion |
  | `N_MC` (diamond null) | 200,000 | Monte Carlo draws for the diamond-tolerance null |
  | `BLOB_THRESHOLD` / `BLOB_MIN_VOL` | 15.0 / 6 | 3-D blob finder |
  | `split_ratio` / `gap_bridge` / `core_frac` | 3.0 / 21 / 0.5 | watershed splitting / gap bridging / centroid core fraction |
  | `live_frames frac_of_median` | 0.002 | dead-frame rejection (max-based, not sum-based) |
  | `LOW_VOLUME_MULT` / `EDGE_MARGIN_MULT` / `ASPECT_DEGENERATE_MULT` / `FAINT_PERCENTILE` | 1.5 / 3.0 / 10.0 / 10.0 | candidate classification ("spurious" test) |
  | `PSEUDOSYMMETRY_RATIO` (Powell 2013) | 1.3× | holohedry ladder "not excluded" threshold |
  | McMahon 2013 tolerance | position_error_px > 0.5 OR \|omega_error_deg\| > achieved_step/2 | retrospective spot-quality flag |
  | `N_BOOTSTRAP` / `RNG_SEED` | 2000 / 0 | every bootstrap 95% CI |
  | `determined_ratio` (UB fit) | 0.25 | "is this cell parameter determined" threshold (σ < 0.25×value) |
  | `tolerance_from_fit n_sigma` | 3.0 | holohedry tolerance derived from fit covariance |

### 5.1 Stage: Panel geometry resolution

**Goal**: for each configured detector panel `(det_x, eiger_y, eiger_z)`, produce `{Lsd, BC_y, BC_z,
ty, tz, tx, strain_uE, source}` plus a provenance string saying which of 3 methods produced it. This
is a **decision tree with an explicit acceptance test at each tier**, not a single formula —
implement all three tiers, in order:

1. **Exact anchor**: look up `(det_x, eiger_y, eiger_z)` to `tol=1e-6` in an existing ceria/LaB6
   `batch_summary.csv`-style calibration table. If found, use it directly — no uncertainty, no
   fallback needed. `source = "exact_anchor:<row>"`.
2. **Regression from a dense calibration sweep** (not `predict_geometry()`'s theoretical
   pixel-pitch model — explicitly rejected both times as unreliable outside the exact range it was
   calibrated over): OLS-fit `Lsd`, `BC_y`, `BC_z` vs. the real densely-sampled sweep axis (e.g.
   `eiger_Y`) from the same calibration table (`_fit_line`, plain least-squares w/ standard error).
   Compose the candidate panel's geometry as `anchor + slope·(target − anchor_value)` for each swept
   axis. **Tilt (`ty`,`tz`) is never regressed — always copied from the nearest same-side exact
   anchor** (confirmed empirically to be a translation-invariant detector property). `tx` stays
   fixed at whatever was independently resolved (see §5.1.1).
   - **Acceptance test before trusting this candidate**: convert the *candidate* panel's own blobs
     to g-vectors under the *candidate* geometry, test what fraction land within `ASSIGN_HKL_TOL` of
     an integer hkl under an **already-known UB** (the already-solved reference panel's UB — this
     requires Phase 1/§5.3 to have already produced a UB for at least one exact-anchor panel).
     Compare against the same test run on the *known* exact-anchor panel's own geometry (the
     "reference consistent fraction"). Accept if `candidate_consistent_frac ≥
     ACCEPT_FRAC_OF_REFERENCE × reference_consistent_frac`.
     *Do not* use a naive forward-predict-and-nearest-pixel-match acceptance test (this was tried
     first and failed on streak/diffuse-scattering samples where blobs are elongated fragments, not
     compact spots — only ~1-7% of real reflections survive a tight nearest-pixel match even on a
     geometry's own true panel).
3. **Self-calibration fallback** (used only when tier 2 fails its acceptance test): iteratively
   predict → match blobs to predictions (nearest-neighbor, shrinking search radius, e.g.
   `(25,10,5)` px) → `midas_defect.selfcal.selfcalibrate_from_crystals(domain, geom, free=
   ("bcy_px","bcz_px","lsd_um"), omega_sign=+1)` (note: **tilt must NOT be in the free-parameter
   list** — a real bug was found where floating tilt absorbed a large wrong correction via a
   tilt/beam-center degeneracy) → re-match → repeat until the matched-hkl set stabilizes or radii
   are exhausted. **Before trusting this method on any genuinely-unknown panel, validate it by
   holding out a known exact-anchor panel, re-deriving its geometry via the same self-cal procedure
   seeded from a different known panel, and checking the recovered geometry against truth** within
   an explicit tolerance band (`bc_px`, `lsd_rel`, `tilt_deg`). Only proceed to real unknown panels
   if this validation passes.

**5.1.1 Detector `tx` (azimuthal rotation about the beam) is a separate, harder sub-problem** that
both reference analyses treated as largely out of scope for per-session re-derivation: a documented,
multi-method investigation (shared-vs-per-panel fit, joint tilt fit, omega-offset fit, self-cal
per-panel, joint tx+BC+Lsd fit) concluded `tx≈0` with an unexplained ~0.3° panel-to-panel
disagreement that was characterized but never resolved, and is treated as "uncharacterized but
bounded." **For v1, hardcode/default `tx=0`** and expose the investigation methodology (§ "tx
investigation," spinel `scripts/09-12,15`) as an optional advanced diagnostic panel, not a blocking
requirement — this is explicitly a case where the GUI should surface the *existing* bounded
uncertainty rather than try to re-solve an open research question.

**Output schema** (`panels.json`, one dict per configured panel):
```json
{"panel_id": 1, "raw_path": "...", "det_x": -195.0, "eiger_y": 10.0, "eiger_z": 30.0,
 "wavelength_A": 0.48621, "px_um": 75.0, "nrpixels_y": 1028, "nrpixels_z": 512,
 "geometry": {"Lsd": 169834.36, "BC_y": -283.15, "BC_z": 291.74, "ty": -0.168, "tz": -0.893,
              "tx": 0.0, "strain_uE": 67.2, "source": "exact_anchor:...", "side": "right"},
 "master_row": {"omega_start_achieved": -29.9, "omega_end_achieved": 28.9, "n_actual": 588,
                "n_nominal": 590, "frac_nominal": 0.9966, "possible_dwell_collapse": false}}
```

### 5.2 Stage: Ingest (raw frames → background-subtracted 3-D blobs → g-vectors)

Per panel:
1. Load the frame stack, trimmed to the omega range given by the project's frame↔omega master
   table (never recompute from nominal acquisition parameters — always resolve per-frame omega from
   the actual master CSV; **resolve that CSV's own internal relative-path columns against the
   directory the table itself documents as its base, not an unrelated project root** — a real path
   bug was hit here).
2. Zero the vendor sentinel value (`4294967295`) per-frame (a pixel transiently sentinel in some
   frames still corrupts a static mask).
3. `live_frames`: keep frames whose **max** (not sum) exceeds a small fraction (`0.002`) of the
   median per-frame max — real vs. dead frames separate by orders of magnitude; a sum-based
   criterion silently discards real low-signal frames.
4. `build_mask`: combine a `negative`/vendor-gap component, a `low_count` component (**the default
   low-count threshold can mask 80-99%+ of the whole detector on a single-crystal-in-DAC image with
   no powder/diffuse baseline — this threshold must be exposed and tunable per dataset, not
   hardcoded**), and any persistent-sentinel mask; binary-dilate by a `grow` parameter.
5. `detector_angle_maps` → per-pixel (2θ, azimuth); `choose_sectors` picks an azimuthal sector count
   for background subtraction by minimizing spurious **negative** coherent blobs after subtraction
   (diffraction is positive-only — negative coherent structure is itself the evidence a background
   model is wrong); `subtract_background` (per-frame polar-median in 2θ-bin × azimuth-sector cells,
   smoothed along 2θ only within each sector).
6. `find_blobs_3d`: threshold → bridge detector gaps → 26-connectivity label in (ω,row,col) → drop
   below `min_vol` → watershed-split each surviving blob by **scale-free** log-intensity prominence
   ratio (not an absolute height) → per sub-peak, centroid from the intensity-weighted high-intensity
   **core only** (avoids streak-tail drag), shape from in-plane covariance moments (not a Gaussian
   fit — spots here are elongated streaks, not points) — report both per-sub-peak and per-whole-blob
   (`full_*`) statistics.
7. Keep only blobs spanning `n_frames ≥ 2`.
8. `pixel_to_qlab` → `qlab_to_qsample` (at the panel's resolved `tx`) → store `qlab_{x,y,z}`,
   `qsample_{x,y,z}`, `q_norm`, `two_theta_deg`.

**Output**: one `spots_g.csv` per panel (schema: `blob_id, sub_id, frame, row, col, integrated,
volume_vox, n_frames, peak_counts, length_px, width_px, pos_angle_deg, aspect, skew_length,
skew_width, kurt_length, kurt_width, omega_width_frames, skew_omega, kurt_omega, extent_frame,
extent_row, extent_col, full_* variants, full_n_sub_peaks, omega_deg, qlab_x/y/z, qsample_x/y/z,
q_norm, two_theta_deg`), plus an `ingest_summary.json` (blob-find counts, mask stats).

### 5.3 Stage: Diamond/anvil filter

Pure 2θ-proximity test against the diamond's own known, sample-independent reflection list (physics
only, reusable across every dataset in this facility that uses the same anvil type):
```python
nearest = np.min(np.abs(tth[:, None] - diamond_tth_lines[None, :]), axis=1)
is_diamond = nearest < CONTAM_TOL_DEG   # 0.07°, Monte-Carlo derived (see below)
```
The `0.07°` tolerance was derived, not guessed: a Monte Carlo null (200,000 draws of `n_spots`
uniform-random 2θ within the panel's own observed range) finds where the observed match count first
clearly exceeds (~3σ) chance; beyond ~0.10-0.15° matches become indistinguishable from chance. Reuse
this derivation (expose it as a diagnostic, with the chosen tolerance as a configurable default) —
don't just hardcode `0.07` blind if a future sample's diamond-line table or detector geometry
differs meaningfully.

**Output**: `+is_diamond, delta_to_diamond_deg` columns; a `_candidate.csv` with diamond excluded;
a pooled `diamond_filter_summary.json` (`n_spots, n_diamond_flagged, n_kept, null_mean, null_std,
z_score, tolerance_deg` per panel).

### 5.4 Stage: Finding the first orientation (two alternative methods — implement both, pick by context)

**5.4.1 Blind ab-initio indexing** (use when the cell is *genuinely* unknown and only one
clean/representative panel is being indexed in isolation — never run this blind across multiple
panels/domains pooled together, see the explicit negative result below):

A Patterson/difference-vector FFT method (`midas_hkls.ab_initio.index_ab_initio`), not a library
like ImageD11:
1. Bin g-vectors onto an `n_grid³` grid spanning `±q_max`, 3-D FFT → off-origin magnitude peaks are
   candidate real-space lattice vectors.
2. Score each candidate by how many g-vectors project onto it within `tol` of an integer, keep only
   if the count exceeds the random-direction chance level by `min_excess_sigma` Poisson sigmas.
3. Refine each surviving candidate by iterative integer-rounding + linear least-squares.
4. Select a 3-vector basis via Buerger/Gauss reduction + a supercell-correction search, with the
   **critical, previously-violated rule: among bases whose indexed subset is within
   `subset_tolerance` of the best subset found, pick the SMALLEST VOLUME, not the largest indexed
   subset** — ranking by subset size alone systematically favors spurious supercells (observed up to
   14× the true volume in testing).
5. Final free refinement of UB from the accepted basis's indexed subset.

**Documented failure mode, do not repeat it**: blindly pooling all panels/domains of a
multi-crystal, diffuse-scattering-heavy sample and running ab-initio once on the combined cloud
**collapsed to 8% indexed and a physically-impossible cell** (a=0.86 Å) in the Ge-oP32 case — most
panels' own hkl assignment under that "answer" was coplanar/degenerate. **Always index per-panel or
per-isolated-subset first**, and only pool via *assignment* (§5.5) once at least one trusted
orientation exists.

**5.4.2 Declared-cell orientation search** (use when the *cell* is already known from a related
crystal/sample but this specific domain's *orientation* is not — e.g. a second crystal in the same
DAC known to share a composition/cell family with an already-solved one). This is a from-scratch
random-rotation search against a **fixed B-matrix template** (never treated as a seed/prior —
disprovable by design):
```python
def random_rotation_search(B0, g_work, n, rng, tol, chunk=2000):
    best_n, best_R = -1, None
    done = 0
    while done < n:
        m = min(chunk, n - done)
        rots = Rotation.random(m, random_state=rng).as_matrix()
        UB = rots @ B0[None, :, :]
        hkl_float = np.einsum('mij,gj->mgi', np.linalg.inv(UB), g_work)
        resid = np.max(np.abs(hkl_float - np.round(hkl_float)), axis=2)
        n_match = (resid < tol).sum(axis=1)
        i = int(np.argmax(n_match))
        if n_match[i] > best_n:
            best_n, best_R = int(n_match[i]), rots[i]
        done += m
    return best_n, best_R
```
With a **null test**: repeat the identical search against direction-scrambled g-vectors (same
|g|/d-spacing distribution, randomized direction — destroys real orientation/Bragg structure while
preserving anything a search could exploit from the radial distribution alone), several repeats, and
require `best_n` to clear both an absolute floor (`MIN_ACCEPT`, e.g. 20) and the null distribution's
own max by a large z-score (one reference run: 65/172 real vs. null mean 16.5±1.0, z≈47 — i.e. don't
accept a borderline result). Use a **tighter** `ASSIGN_HKL_TOL` (0.15, not the usual 0.25) for this
search stage specifically, to keep the null comparison clean. On success, free-refine (cell AND
orientation both open) from the winning seed — never report the search's raw rotation as the final
answer.

### 5.5 Stage: Assignment-based indexing (pooling multiple panels of ONE already-identified domain)

Once one trusted UB exists (from §5.4, on one panel or one isolated subset), extend to every other
panel of the *same* crystal via assignment, not by re-running ab-initio pooled:
```python
hkl_float = (UB_seed_inv @ g.T).T
resid = np.max(np.abs(hkl_float - np.round(hkl_float)), axis=1)
keep = resid < ASSIGN_HKL_TOL   # 0.25
```
Concatenate every panel's kept, integer-rounded hkl + g, then run one free refinement
(`refine_ub_from_gvectors`, cell AND orientation both open — the seed UB is a **hypothesis**, not a
constraint) over the pooled set. This is "seeded indexing, not a seeded answer": justified when the
panels are genuinely different views of the *same physical crystal* (so a shared cell/orientation
hypothesis is physically motivated), not when panels might contain different crystals (then the
24-relabeling + per-panel-first-then-pool discipline of §5.6 applies instead).

Compute, for the pooled result: per-panel misorientation (fit each panel's own UB using the combined
fit's hkl labels, check agreement, flag drift), holohedry + jackknife stability (leave-one-reflection-
out refit, count how often the symmetry verdict flips), distortion-mode gates (`ab_separable`,
`shear_separable`, `distortion_condition` — a conditioning number, lower = better leverage to
separate a/b/γ, larger datasets should show this improving), and a 2000-replicate bootstrap 95% CI
on the cell parameters (resample reflections with replacement, refit, apply the **same fixed**
primitive→conventional transformation matrix each time — re-deriving it per replicate risks an
axis-labeling artifact inflating the spread).

**Axis-doubling / supercell detection** (needed whenever a literature or expected cell/volume exists
to compare against): if the fit cell's volume is ≈half the expected volume (from known atom count ×
literature volume-per-atom, or a literature cell directly) AND one edge is suspiciously close to
half its expected literature counterpart, the fit basis is missing a centering/doubling along that
axis. **Two independent numeric signals are required, not one** (edge-length ratio AND volume
ratio) — pick the doubling axis (if ambiguous) by whichever interpretation's resulting volume lands
closest to the literature value; the axis *identity* this way, never guessed from space-group theory
alone. A systematic-absence test (via `gemmi`'s `SpaceGroup(n).operations().systematic_absences()`,
testing all 6 permutations of the doubled triple against the number of forbidden reflections in the
*observed* data, picking whichever permutation gives zero/near-zero forbidden) can then confirm
*which axis labeling/permutation* is correct, once the doubling axis itself is already established.

### 5.6 Stage: Multi-domain separation (finding additional crystals in the same dataset)

Trigger: after classification (§5.7), a large unindexed fraction remains across many panels (in the
Ge-oP32 case, 54% — strongly suggestive of additional diffracting material, not just noise).
Procedure, applied iteratively (crystal 1 → crystal B → crystal C → ... until a negative result):

1. **Per-panel-only blind ab-initio** on the current "still unindexed" pool, **never pooled across
   panels at this stage** (pooling here is exactly the failure mode in §5.4.1). Niggli-reduce each
   panel's resulting cell and look for a consistent cell family across several panels — this
   consistency is a judgment call made by inspecting reduced values, not a single hardcoded
   tolerance; surface the reduced cells side-by-side in the UI for the user to confirm/reject.
2. Pick the best-indexed consistent panel as a seed; run assignment-based indexing (§5.5) against
   the **remaining unindexed-only pool** (never let a new domain claim spots already assigned to an
   earlier domain, diamond, or spurious).
3. **Before accepting a new domain as genuinely separate** from an already-found one with a similar
   cell: run the 24-proper-relabeling misorientation check (§5.0) between the new domain's UB and
   every already-known domain's UB. A small result (empirically ~7-9° in the reference work) means
   "this is contamination of an existing domain that just missed the assignment tolerance" — exclude
   those panels from the new seed and retry. A large result (~29-36°, combined with tight *mutual*
   agreement among the candidate panels themselves) supports "a genuine separate population."
4. Repeat until a new domain search comes back **negative** — characterize a negative result
   explicitly (don't just silently stop): per-panel fits that don't agree with each other, each
   individually close to one of the *already-known* domains (never far from all of them), and a
   direct blind pool of the remainder "succeeding" only by rediscovering an existing domain's own
   diffuse tail at a loosened tolerance. Report this as a documented negative result, not a silent
   stop condition — the reference work's own `crystalD_search_result.json` is the template for what
   this record should contain (`conclusion`, per-panel cells, pairwise misorientations,
   misorientation vs. each known domain, the loosened-tolerance consistency check numbers).

### 5.7 Stage: Candidate classification (indexed / diamond / spurious / unindexed, per domain)

The one piece of business logic that is standalone project logic, not a thin library wrapper —
implement exactly, with reference-population-derived thresholds (never computed from the candidates
being classified themselves, to avoid circularity):
```python
faint_threshold = np.percentile(indexed_population.peak_counts, FAINT_PERCENTILE)        # 10
aspect_degenerate_threshold = ASPECT_DEGENERATE_MULT * np.percentile(indexed_population.aspect.dropna(), 95)  # x10
edge_margin_px = EDGE_MARGIN_MULT * np.median(indexed_population.full_extent_row_and_col)  # x3

low_volume_flag       = volume_vox <= LOW_VOLUME_MULT * BLOB_MIN_VOL   # <= 1.5x6 = 9
edge_flag             = edge_dist_px < edge_margin_px
degenerate_shape_flag = aspect.isna() | (aspect > aspect_degenerate_threshold)
faint_flag            = peak_counts < faint_threshold
structural_flag       = low_volume_flag | edge_flag | degenerate_shape_flag
spurious_test         = structural_flag & faint_flag     # AND -- must be BOTH structurally suspect AND faint

category = "indexed_<domain>" if matched by that domain's assignment
category = "diamond" if is_diamond
category = "spurious" if (leftover and spurious_test)
category = "unindexed" otherwise   # deliberately ambiguous, never forced to a verdict
```
When multiple domains exist, build the **unified multi-category view** by relabeling rows pulled
from each domain's own per-domain source table. **Bug class to guard against explicitly**: when
relabeling a row from `"unindexed"` to e.g. `"crystalB"`, you must backfill every derived column
that domain's own indexing computed (`h,k,l,qpred_*,residual_*`), not just the category string — a
prior implementation left these NaN for relabeled rows because the original classification pass
never computed them under that label, and it surfaced only when a downstream notebook/viewer tried
to render an int() conversion on a NaN `h`. Add an explicit schema check (no NaN in the numeric
columns for any category except `"unindexed"`/`"spurious"`) rather than relying on it being caught
visually.

### 5.8 Stage: Diagnostics suite

Each of these is optional/advanced but was fully built out in the reference work and should be
exposed per-domain once a domain has a refined UB:

- **Holohedry ladder test (Powell et al. 2013)**: refit the same indexed reflections under a ladder
  of increasingly-constrained symmetries (triclinic → monoclinic → orthorhombic → [tetragonal →
  cubic], truncated at whatever symmetry the literature/expected system caps out at), with the hkl
  assignment held fixed each time (a constrained refit, not a re-index). A level is "not excluded" if
  its r.m.s.d. is within `PSEUDOSYMMETRY_RATIO` (1.3×) of the triclinic (most free) r.m.s.d. This is
  the correct way to ask "is this cell's apparent lower symmetry just noise" with real statistical
  backing, rather than eyeballing how close angles are to 90°.
- **Spot quality / ff-HEDM-style diagnostics**: invert the forward model for every indexed spot
  (predicted q from UB·hkl → Ewald-crossing omega(s) → pick the crossing nearest the observed omega
  → predicted pixel) to get `position_error_px` and `omega_error_deg` per reflection. Bin by 2θ (not
  by `h²+k²+l²` ring index unless the cell is cubic — that grouping is invalid for lower-symmetry
  cells where different hkl can share the ring sum at very different |G|). Optionally apply the
  McMahon et al. 2013 literature tolerance (`position_error_px > 0.5` OR `|omega_error| >
  achieved_step/2`) as a retrospective flag — expect most/all reflections to fail this on an
  ab-initio-only solve (it's a converged-single-crystal-refinement-grade tolerance, not an
  indexing-success criterion); report honestly rather than silently dropping it for looking bad.
- **Completeness**: report **both** a sphere-limited denominator (every symmetry-unique hkl out to
  the observed 2θ max, under the loosest plausible space group — i.e. no centering/glide
  extinctions assumed, unless/until a systematic-absence check independently confirms the true space
  group) and a geometry-limited denominator (further restricted to hkl whose Ewald crossing actually
  lands on *some* configured panel within its achieved omega range) — report both, they can differ
  by an order of magnitude and conflating them is misleading.
- **Structure factor correlation** (only if atomic coordinates are available/derivable): build a
  `midas_hkls.Crystal` from literature Wyckoff positions (verify origin-choice conventions
  programmatically — don't assume a library's bare default matches a given paper's convention;
  confirm by checking e.g. that the inversion operation carries the expected translation, or that
  computed systematic absences reproduce the paper's stated ones), compute `|F|²` at every observed
  hkl, correlate (Pearson on log-log and/or Spearman) against measured intensity. **Always
  programmatically verify any hand-picked "should be systematically absent" test reflections**
  (`sg.is_systematically_absent(h,k,l)`) before using them as a negative control — a wrongly-included
  *allowed* reflection in such a list will silently produce a nonsense "max F² among forbidden
  reflections" number.

---

## 6. Data contracts — proposed on-disk layout for a GUI-driven run

Mirror the reference analyses' own layout (already proven, and lets "browse existing results" work
on runs produced outside the GUI too):

```
<project_folder>/
├── geometry/
│   ├── panels.json                      # see §5.1 schema
│   ├── ceria_regression_report.json     # per-panel candidate/quality/accept decisions
│   ├── selfcal_validation.json
│   └── _checkpoint_*.json               # resumability checkpoints (safe to delete to force rerun)
├── panel_<NN>_<detx>_<eigery>_<eigerz>/data/
│   ├── spots_g.csv
│   ├── spots_g_diamond_flagged.csv
│   ├── spots_g_candidate.csv
│   └── ingest_summary.json
├── pooled/
│   ├── diamond_filter_summary.json
│   ├── ab_initio_result.json
│   ├── indexed_spots_<domain>.csv                     # domain = "crystal1", "crystalB", ...
│   ├── refine_symmetry_strain_result_<domain>.json
│   ├── candidates_classified.csv                      # per-domain-pass view
│   ├── candidates_classified_all.csv                  # unified multi-domain view
│   ├── holohedry_ladder_result_<domain>.json
│   ├── spot_quality_metrics_<domain>.csv / _summary.json
│   ├── completeness_result_<domain>.json
│   ├── structure_factor_result_<domain>.csv / _summary.json
│   └── <nth>_domain_search_result.json                # negative-result record, see §5.6 step 4
└── figures/                              # static PNG fallbacks/exports, mirroring the pyqtgraph views
```

Every stage's result JSON should carry enough of its own inputs (tolerances used, panel ids
involved, method/source strings) that a "browse existing results" load doesn't need to separately
reverse-engineer what configuration produced it — this is already how the reference `panels.json`'s
`geometry.source` field and every `pooled/*_result.json`'s own parameter echo works; preserve that
property since it's exactly what the GUI's "load and inspect" mode will depend on.

---

## 7. Known pitfalls — a checklist to actively test against, not just read

These are real bugs/near-misses hit during the two reference analyses. A GUI reimplementation
should have an explicit test or guard for each:

1. **g-vector convention** (`2π/d` vs `1/d`) silently mismatched between ingest and a `midas_hkls`
   call → cell wrong by exactly 2π, false triclinic verdict. (§5.0)
2. **`AbInitioResult.UB`** is always `1/d` regardless of the `two_pi=True` input flag — always
   multiply by 2π before treating an ab-initio UB as a "2π convention" seed for later calls.
3. **Blind pooled ab-initio across multiple panels/domains** collapses to a tiny indexed fraction and
   a physically-impossible cell in a multi-crystal/diffuse-scattering sample. Always index per-panel
   or per-isolated-subset first. (§5.4.1)
4. **Naive forward-predict nearest-pixel-match** as a geometry acceptance test fails on streak/
   diffuse (non-compact-spot) samples even at the *true* geometry. Use the fixed-cell consistency
   test instead. (§5.1)
5. **Letting detector tilt float during self-calibration** can absorb a large wrong correction via a
   tilt/beam-center degeneracy — keep tilt out of the self-cal free-parameter list; validate the
   self-cal method on a *known* held-out panel before trusting it on an unknown one. (§5.1)
6. **Cell-dimension agreement alone cannot distinguish "same domain, looser match" from "genuinely
   separate domain of the same lattice type."** Always follow with the 24-proper-relabeling
   orientation check. (§5.6 step 3)
7. **Axis-doubling detection needs two independent signals** (edge-length ratio AND volume ratio),
   and a systematic-absence test can only resolve the axis *permutation*, never which axis is
   doubled in the first place — that's the volume argument's job. (§5.5)
8. **A relabeled multi-domain classification row must backfill every derived column, not just the
   category string**, or downstream code will crash/silently NaN on it. (§5.7)
9. **When two sibling pipeline branches (e.g. domain-B's vs domain-C's own pipeline) name the same
   quantity differently, or compute a cross-reference value asymmetrically** (only one direction
   stores it), normalize explicitly in the consumer rather than assuming symmetric storage — a prior
   bug read a cross-domain misorientation value from the wrong JSON file for exactly this reason.
10. **Path composition against a shared master table** (e.g. the frame↔omega master CSV) must
    resolve relative path columns against the directory the table itself documents as its base, not
    an unrelated project root.
11. **Always guard the first indexed access into a per-position/per-panel array** (`arr[0]`,
    `fidx[0]`) with an explicit zero-length check that produces a well-formed empty result — a
    genuine zero-frame acquisition gap is an expected outcome for some positions/panels, not an
    exceptional one, and every downstream stage must already tolerate a zero-candidate panel/domain.
12. **The low-count mask-component threshold's default can mask 80-99%+ of the detector** on a
    single-crystal (non-powder) sample with no diffuse baseline to set a "low" plateau against — this
    must be an exposed, tunable parameter, never a silent hardcoded default.
13. **Always execute any notebook/report-generation code end-to-end and inspect real output**, not
    just read it — both backfill-NaN and wrong-cross-reference-file bugs above were only caught this
    way, not by code review.

---

## 8. Dependencies to add to this repo

Add to `requirements.txt`/`environment.yml` alongside the existing MIDAS backend package pins:
`midas_defect` (editable/source install — check whether it's published as an installable package or
needs vendoring/pinning the same way as this repo's other `midas-*` packages), `midas_hkls`
(`>=0.16.0`), `gemmi`. Confirm the numpy/numba/torch pin compatibility (`numpy==1.26.4` is held
deliberately below 2.1 for `numba`/`torch` reasons per this repo's existing `requirements.txt`
comment) doesn't conflict with whatever `midas_defect`/`midas_hkls` themselves require — check their
own requirements before pinning.

## 9. Open questions to resolve with the user before/while building (don't silently assume)

1. **Backend package shape**: should the deterministic pipeline logic live as a new standalone
   installable package (`midas_solve_cell`, mirroring `midas_calibrate_v2` etc.) that both this GUI
   and a future headless CLI could import, or as a module embedded directly inside `midas_gui/`? A
   standalone package is recommended (matches this repo's existing architecture and keeps the
   pipeline reusable/testable independent of Qt), but confirm — it affects where new code is written
   and how it's versioned/released.
2. **3-D reciprocal-space viewer**: pyqtgraph has no built-in equivalent to the reference Plotly
   notebooks' interactive 3-D hover-pick scatter. Is adding `pyqtgraph.opengl` (or another 3-D
   widget) acceptable, or should the tab ship fixed 2-D orthogonal projections (qx-qy/qx-qz/qy-qz,
   as the reference work's own static-PNG fallback already does) for v1 and defer true 3-D
   interactivity?
3. **Multi-domain UX**: should domain discovery (§5.6) be a fully automatic "search for more
   domains" button, or a guided human-in-the-loop step where the GUI proposes a candidate seed/cell
   family per panel and asks the user to confirm before committing to "a new domain exists"? Given
   that the reference work's own domain-acceptance calls were genuine judgment calls on an empirical
   gap (not a fixed threshold), a confirm-before-commit UX is likely safer than a fully automatic
   loop — but this is a real UX design decision, not something to default silently.
4. **Where does a user's literature-cell/expected-composition input come from?** (needed for
   axis-doubling detection and strain/structure-factor stages) — a small per-project config file, a
   GUI form, or reuse of an existing "Materials"/calibrant-style dropdown already in
   `constants.MATERIALS`? Check whether that existing list is a reasonable base to extend.
5. **Provenance integration depth**: full participation in `project.py`'s FAIR-provenance system
   (§4.7) is a meaningfully bigger lift than a minimal "load/run and show results" tab — confirm
   whether that integration is in scope for the first landed version or a later follow-up.

## 10. Suggested phased build order

Given the "everything, but build in reviewable slices" guidance in §0:

1. **Phase 1 — single-crystal, single-known-geometry solve.** Tab skeleton + registration (§4.2),
   one-panel ingest + diamond filter + blind ab-initio + free refinement + basic result display
   (parameter grid + 2-D reciprocal-space scatter). No geometry-resolution tiers yet (assume
   geometry is supplied directly); no multi-domain; no diagnostics suite beyond the basic refined
   cell + holohedry verdict. This alone validates the worker/threading/display pattern end-to-end.
2. **Phase 2 — multi-panel + geometry resolution tiers.** Panel table widget, the 3-tier geometry
   resolution decision tree (§5.1) including the self-cal fallback and its validation gate,
   assignment-based pooled indexing (§5.5) with bootstrap CI and axis-doubling detection.
3. **Phase 3 — classification + diagnostics suite.** Candidate classification (§5.7), holohedry
   ladder, spot quality, completeness, structure factors (§5.8) as additional result panels/tabs
   within the Solve Cell tab.
4. **Phase 4 — multi-domain separation.** The iterative domain-discovery loop (§5.6), the
   24-relabeling orientation comparison UI, unified multi-domain classification view, negative-result
   reporting.
5. **Phase 5 — browse mode + polish.** "Load an existing solve-cell run" (from this GUI or the
   original CLI pipeline) reusing every Phase 1-4 display widget; project/provenance integration if
   confirmed in scope (§9.5); any 3-D viewer upgrade (§9.2).

Each phase should be independently useful and testable against the two reference analyses' own
already-known-correct numbers (e.g. spinel's `a=7.9672 Å` 6-panel result, Ge-oP32's combined
`a=7.839 Å` doubled cell, crystal B's `95.7%` volume match) as regression fixtures — the reference
analyses' own `pooled/*.json` files are, in effect, a free golden-output test suite for whichever
phase reproduces that stage's computation.
