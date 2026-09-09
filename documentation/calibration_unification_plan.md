# Unifying the Calibrate surfaces

_Written 2026-09-08. A path, not a commitment — each phase is independently
useful and independently abandonable._

## 1. What's actually inconsistent

Three surfaces build calibration UI, independently:

| surface | file(s) | lines | what it calibrates |
|---|---|---|---|
| Calibrate tab (single detector) | `tab_calibrate.py` | 2188 | one monolithic detector |
| Calibrate tab (Hydra mode) | `hydra_calib_page.py` + `hydra_calib_widgets.py` | 1106 + 481 | 4 separate GE panels |
| Geometry/ring card | `hydra_geometry_card.py::DetectorGeometryCard` | 1321 | seed + ring simulation, used by Data Viewer and the Hydra page — **not** by either Calibrate surface |

The Hydra Calibrate page's cards (Pipeline, Detector & Calibrant, Threshold,
Average frames, Refine parameters, Advanced) were copy-pasted from the
single-detector tab and have since drifted. Grepping for the features added to
the single-detector tab over the last few sessions, in the Hydra copy:

| feature | `tab_calibrate.py` | `hydra_calib_page.py` |
|---|---|---|
| d-spacing / manual ring-pick calibrants (AgBH, SAXS) | 8 refs | **0** |
| "Refining: … Fixed: …" summary line | 13 | **0** |
| Parameter limits (± windows) | 7 | **0** |
| Seed-driven ring preview | 16 | **0** |
| Use seed as calibration (no fit) | 5 | **0** |
| Per-calibrant-kind refine defaults | yes | **no** |
| Refined-value ± σ in the results grid | yes | **no** |

So the Hydra path today can only do crystalline calibrants with the crystalline
defaults. Every fix landed on one side has to be hand-carried to the other, and
none of them has been.

The `Refine parameters` card is the clearest case: `hydra_calib_page.py:293-315`
is a near-literal copy of what `tab_calibrate.py:345-360` used to be, down to the
`i // 2, i % 2` grid arithmetic and the `…` distortion button.

## 2. Why it drifts — the root cause

**There is no single description of "a calibration job."** Each surface hardcodes,
in layout code, all of: which cards exist, which parameters are refinable, which
backend runs, what a result looks like, and how it exports.

Capability differences are expressed *only* by hiding widgets:

```python
self._set_limits_visible(is_dsp)          # tab_calibrate.py
self._dist_row.setVisible(not is_dsp)
self._build_rc.setVisible(not is_dsp)
self._panel_grp.setVisible(not is_dsp)
self._adv_grp.setVisible(not is_dsp)
```

Two consequences, both of which cost real time this week:

1. **New capability ⇒ N implementations.** Nothing forces the Hydra page to grow
   a limits column when the single tab does.
2. **The UI cannot explain itself.** A widget that vanishes reads as a glitch.
   Both questions asked this session — *"why are the bounds not visible for
   CeO2?"* and *"why do I have to pick points for AgBH?"* — are the same bug:
   a capability the backend genuinely lacks, communicated by absence.

## 3. The target shape

One declarative capability record per (backend × calibrant kind), and a card
library that renders itself from it.

```python
@dataclass(frozen=True)
class CalibrationCapabilities:
    key: str                     # "crystalline" | "dspacing"
    label: str                   # "MIDAS calibrate (crystalline)"
    refinable: frozenset         # Lsd BC tx ty tz Wavelength Distortion
    default_refine: frozenset
    supports_bounds: bool        # crystalline: False — backend takes no bounds kwarg
    supports_distortion: bool
    supports_residual_map: bool
    supports_multipanel: bool
    needs_picks: bool            # dspacing: True
    min_picks: Callable[[dict], int]
    reports_sigma: bool
    #: capability -> the sentence shown in its place when it is unavailable.
    why_not: Mapping[str, str]
```

`why_not` is the piece that fixes the class of confusion above. A card never
silently hides a control: it asks the record, and if the answer is no, it renders
the reason. The label added by hand today —

> Parameter limits are not available for this calibrant: the MIDAS calibrate
> backend takes no bounds arguments, so there is nothing to pass them to.

— becomes generated, and therefore present on every surface at once.

## 4. Phases

Each phase ends green and shippable. Sequence matters: 0 before 1, 1 before 2.

### Phase 0 — freeze the drift (no behaviour change)

A characterisation test that asserts the two surfaces agree where they claim to.
Build a `CalibrationTab` and a Hydra calib page and compare their refine-flag
vocabulary, card titles, and `state_widgets()` key sets, listing today's known
differences as an explicit allow-list.

Cheap, and it turns every later phase from "hope nothing moved" into a diff.
It also documents the drift in executable form — the allow-list shrinking is
the progress metric for phases 1–4.

*Touches:* `tests/test_calibration_surface_parity.py` (new). ~150 lines.

### Phase 1 — extract the capability table

New `midas_gui/calib_caps.py` holding `CalibrationCapabilities` and the two
records. Rewrite `tab_calibrate.py`'s five `setVisible(is_dsp)` calls and
`_manual_min_picks()` to consult it. No new UI.

Do this *before* extracting cards: the extracted card needs something to be
parameterised by, and getting the record right against one working surface is
much easier than against two.

*Touches:* `calib_caps.py` (new, ~120 lines), `tab_calibrate.py` (~40 lines
changed). *Risk:* low — pure refactor behind existing tests.

### Phase 2 — extract the shared cards

Move to `midas_gui/calib_cards.py`, each taking a `CalibrationCapabilities`:

- `RefineParametersCard` — checkboxes + limits column + distortion row +
  summary line + the `why_not` labels. This is the biggest single win: it
  deletes `hydra_calib_page.py:293-315` outright and gives Hydra the limits,
  the summary, and the per-kind defaults for free.
- `DetectorCalibrantCard` — λ / calibrant / pixel.
- `ThresholdCard`, `AverageFramesCard` — verbatim copies today.
- `AdvancedCard` — E-M / LM iters, device, output dir.

Keep `state_widgets()` keys byte-identical so saved projects keep loading; that
constraint is what makes this safe, and Phase 0's parity test is what proves it.

*Touches:* `calib_cards.py` (new, ~500 lines), `tab_calibrate.py` (−350),
`hydra_calib_page.py` (−250). *Risk:* medium — the pyqtgraph teardown
segfaults make Qt refactors expensive to debug. Mitigate by moving one card per
commit, running the forked suite between each.

### Phase 3 — one geometry/seed card

The seed card exists three times: inline in `tab_calibrate.py`,
`HydraCalibPanelCard` in `hydra_calib_widgets.py`, and the fully-featured
`DetectorGeometryCard` (ring simulation, materials list, Pick BC / Pick Ring,
im_trans) already shared by Data Viewer and the Hydra page.

`DetectorGeometryCard` is the one to converge on — it is the most capable and
already has two consumers. The gap is that the Calibrate tab's seed card also
owns the *seed ring preview* and *feed result back to seed*, which
`DetectorGeometryCard` does not have.

Ordering: add those two to `DetectorGeometryCard` first (Data Viewer benefits
immediately — a seed-driven preview there is useful on its own), then swap the
Calibrate tab onto it, then Hydra's per-panel card.

*Touches:* `hydra_geometry_card.py` (+150), `tab_calibrate.py` (−300),
`hydra_calib_widgets.py` (−150). *Risk:* highest of the phases — this card
carries the most state and the most signals. Worth doing last of the structural
work, or splitting into its own arc.

### Phase 4 — one result and export path

`_populate_param_grid` / `paramstest_pairs` / `_save_json` / `_save_paramstest` /
`→ Send to Data Viewer` exist twice (`tab_calibrate.py`, `hydra_calib_widgets.py`),
and only the single-detector copy renders ± σ, `(at limit)`, or `(fixed)`.

Extract `CalibrationResultPanel`. Same argument as Phase 2, smaller surface.

*Touches:* `calib_results.py` (new, ~250), `tab_calibrate.py` (−150),
`hydra_calib_widgets.py` (−120).

### Phase 5 — technique presets (the part that isn't just deduplication)

This is the axis the current code conflates. `_refine_state_dsp` defaults AgBH to
**BC-only** because of the measured ill-conditioning at the SAXS geometry
(Lsd = 13.5 m, 42° of ring 1 on the detector, σ(Lsd) = 149 mm). But that is a
property of the *geometry*, not of the calibrant: AgBH at 200 mm on a 2880²
detector gives full rings and would fit Lsd fine.

So make the third axis explicit — a **technique preset** (`WAXS/powder`,
`SAXS`, `custom`) that sets seed ranges, default refine flags, and the
conditioning warnings, orthogonal to calibrant kind and detector layout. Then
"Refining: BC" stops being a mysterious AgBH special case and becomes "SAXS
preset: BC only — at this Lsd, ring radius and beam centre are nearly
degenerate."

Best implemented as an advisory: compute the conditioning from the actual seed
geometry and warn, rather than a hard preset the user has to know to pick.

## 5. What this does not touch

- `tab_refine.py` (Calib. Refinement, 319 lines) shares no UI with these — it
  consumes a result and refines against the profile. Leave it.
- The MIDAS backends are PyPI-pinned (CLAUDE.md); no phase changes what the
  backends do, only how the GUI describes them.

## 6. Suggested order if time is short

Phase 0 → Phase 1 → the `RefineParametersCard` half of Phase 2. That alone gives
Hydra the limits column, the refine summary, and per-kind defaults, and makes
every future capability a one-line addition to a record instead of an edit in
two files. Phases 3–4 are larger and can wait; Phase 5 is a separate
conversation about physics defaults, not structure.

## 7. Verification constraints (this environment)

- No live `$DISPLAY` — `QT_QPA_PLATFORM=offscreen` only. Layout changes are
  checked by rendering `widget.grab()` to PNG and inspecting, which worked well
  for the Refine-card rearrangement.
- Qt/pyqtgraph tests must be `@pytest.mark.forked` (`.context/DECISIONS.md`);
  building a third `CalibrationTab` in one unforked process segfaults inside
  `ViewBoxMenu`/`PlotItem` construction.
- Known pre-existing failures on `feature/caking-improvements`, unrelated to
  this work: `tests/test_project.py::test_apply_project_calibration_single_detector`,
  `tests/test_project.py::test_open_project_rejects_non_project_h5`,
  `tests/test_workspace_ux.py` (segfault). All reproduce on stashed HEAD.
