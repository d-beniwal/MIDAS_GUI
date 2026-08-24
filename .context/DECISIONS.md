# DECISIONS — append-only, newest first

Each entry: what was decided and *why* (the reasoning that would be expensive
to reconstruct later). Never rewrite history; add a new entry to supersede.

## 2026-08-24 — Hydra composite needs a vertical-axis mirror too (partially reopens the "no chirality/X-mirror correction" entry further below)

After the rotation-direction revert (entry directly below) still didn't
fully match, the user identified the remaining discrepancy precisely: a
plain left-right mirror of the *whole finished composite* makes it match
reality, and named which panels swap sides — ge4 should be on the right,
ge2 on the left (the composite as built was putting them the other way
round).

**This does re-open the earlier "no chirality/X-mirror correction needed"
conclusion** (further below) — that entry checked ring continuity and
overall render plausibility with real data, which (as the rotation-
direction entry already noted) a pure mirror combined with a compensating
rotation-direction error can pass just as easily as no error at all.
Neither ring continuity nor "does it look like a plausible windmill" can
distinguish "correct" from "mirrored + counter-rotated" — only knowing
which physical panel should be on which side of a real detector can, which
is exactly what the user supplied here.

**Fix**: `hydra.py::compute_inv_coords` now computes `Y_lab = (half - Yo) *
px` instead of `Y_lab = (Yo - half) * px` — mirrors the composite canvas
about its vertical axis, independent of the rotation-by-`tx` logic. Verified
against the real `park_may26_bc` calibration + `park_may26/ge{1..4}` frames:
per-panel single-detector composites now centroid at `ge1`/`ge4` on the
right half of the canvas and `ge2`/`ge3` on the left (previously the
reverse for ge2/ge4), matching the user's stated correct arrangement.

**Scope of the fix**: this coordinate map (`compute_inv_coords` via
`DetectorState.get_inv_coords`/`get_remapped_frame`) is used *only* by
`build_windmill_composite` — not by the per-panel ge1-4 raw-frame displays
or their ring overlays (those live in `hydra_geometry_card.py`/
`hydra_page.py` and don't go through this code path), so this mirror only
affects the Composite view, as intended.

Both independently hand-derived test formulas
(`tests/test_hydra_chirality.py::_expected_xy`,
`tests/test_hydra_geometry.py::_expected_composite_xy`) updated to invert
the same mirror (`half - Y_lab/px` instead of `Y_lab/px + half`). Full
Hydra geometry/chirality suite (14 tests) passes after the change.

## 2026-08-23 — Hydra composite rotation direction: reverted back to counterclockwise (supersedes the "clockwise, not CCW" entry below — that fix was itself wrong)

The user did the real windowed comparison against `test_data/s1ide` that
the prior entry (below) flagged as still needed, using the real per-panel
fitted calibration (`park_may26_bc/refined_MIDAS_params_ge{1..4}_Tx_cake.txt`)
and a known-correct reference composite image for the same data. The
earlier same-day "clockwise" fix (`tx_rad = math.radians(-tx_deg)` in
`hydra.py::compute_inv_coords`) was **wrong** — it visibly mis-rotates the
composite (panel sectors and each panel's identical local shadow-marker
land at the wrong azimuth) relative to the reference. Reverted to the
original `tx_rad = math.radians(tx_deg)` (counterclockwise), which was
correct all along.

**How this was diagnosed** (pixel forensics alone was a dead end — see
below — the fix came from reproducing the bug with real code + real data
and testing sign hypotheses against a known-correct reference image):
1. Built the composite with `hydra.build_windmill_composite` using the
   actual `park_may26_bc` calibration files and the real `park_may26/ge{1..4}`
   HDF5 frames — this reproduced the user's "current (wrong)" screenshot
   pixel-for-pixel in structure, confirming the bug lived in this
   codebase's math, not in how the screenshot was captured.
2. Every panel's raw frame carries an identical small dark shadow feature
   (a fixed local fiducial/support-arm shadow, same raw pixel location on
   every panel — same idea as the synthetic test fixture's marker) that
   necessarily renders *radial* from the shared beam centre regardless of
   whether `tx`'s sign is right or wrong (a pure rotation about the centre
   always keeps a "points at my own BC" feature pointing at the composite
   centre) — this makes ring continuity *and* "does the marker point at
   the centre" both blind to a global direction error, exactly as the
   entry below already suspected. What *does* differ between a correct and
   backwards sign is **which azimuth** each panel (and its marker) ends up
   at.
3. Recomputed the composite with `tx_rad = math.radians(tx_deg)` (i.e.
   undoing the clockwise change) using the same real data — the result
   matched the known-correct reference image closely (each panel's marker
   lands at the same near-cardinal azimuth, same "hook" orientation, same
   overall windmill arrangement), while the clockwise version reproduced
   the wrong one. This was a direct empirical test (render + compare), not
   inference from the marker-radial argument in point 2, which is
   necessarily inconclusive on its own.
4. Reverted `hydra.py::compute_inv_coords` and the two independently
   hand-derived test formulas that had been flipped to match the wrong
   convention (`tests/test_hydra_geometry.py::_expected_composite_xy`,
   `tests/test_hydra_chirality.py::_expected_xy`) back to counterclockwise.
   Full Hydra test suite re-run clean after the revert.

**Lesson**: the earlier "clockwise" conclusion was reached from a
plausible-sounding argument (nominal physical Tx values matching the
bundled defaults) without actually rendering and comparing against a
known-correct reference — exactly the kind of "looks right on paper" bug
a real look at real data catches and paper reasoning does not. Don't trust
a sign-convention conclusion for this rotation without a render-and-compare
check against known-correct output.

## 2026-08-23 — Five Hydra bugs found in the real windowed (Phase 6) pass, fixed

The first real windowed manual review of Hydra mode (the one item STATE.md
had flagged as still outstanding) surfaced 5 real bugs that offscreen
tests/screenshots never exercised:

1. **λ/max 2θ/px now mirrored across ge1-4 + the Composite card.**
   `DetectorGeometryCard` gained `get_shared_fields()`/`apply_shared_fields()`;
   `HydraViewerPage._sync_shared_fields()` mirrors whichever card's value
   changed onto the other 4, guarded by a `_syncing_shared` re-entrancy
   flag so applying to a sibling (which itself emits `geometryChanged`)
   doesn't recurse. Composite is included (not just ge1-4) since it's the
   same beam/pixel size, so its own ring radii would otherwise silently go
   stale relative to the per-panel views.
2. **Dark/bright/background correction added for Hydra**, closing the
   scope cut noted in `hydra_page.py`'s original module docstring. New
   `HydraFieldSelector` (`hydra_widgets.py`) mirrors the single-detector
   tab's `FieldSelector`/`DataLoaderPanel.corrected()` machinery
   (`helpers.apply_field_corrections`/`average_field`,
   `workers.FieldAverageWorker` reused as-is) but auto-discovers the other
   3 panels' field files via `helpers.hydra_siblings` — the same function
   the main "Hydra data" path already used — rather than requiring 4
   separate picks.
3. **Composite rotation direction fixed: clockwise, not counterclockwise.**
   See the dedicated entry below — this is the one that reversed a
   conclusion from an earlier session's chirality verification, so it gets
   its own writeup.
4. **Stale radial-integration geometry fixed.**
   `_effective_calib_geom()` was returning `self._calib_geom` (a snapshot
   frozen at calibration-load time) verbatim, so once a full geometry was
   loaded, later edits to the live BC/λ/Lsd/px/tilt widgets moved the ring
   overlay (which always reads the live widgets) but not the radial
   profile. Latent in the single-detector tab too, but only guaranteed to
   bite in Hydra because every bundled default `ps_ge{1..4}.txt` already
   carries a non-zero `tx`, so `_apply_full_geometry_dict`'s "has_full"
   gate — and therefore this bug — is live from the very first frame,
   unlike the single-detector tab's usual all-manual starting state. Fixed
   by having `_effective_calib_geom()` return a copy with
   wavelength/Lsd/BC/pxY/pxZ/ty/tz always overridden from the live widgets
   (only `tx`/`distortion`/`NrPixelsY/Z` — which have no live widget — come
   from the frozen snapshot). Related: `_on_sim_param_changed()` only
   redrew/reintegrated when "Simulate rings (live)" was checked; gave it
   the same always-refresh tail `_on_bc_changed()` already had, since λ/
   Lsd/px/max2θ feed the same geometry regardless of that toggle — needed
   for issue 1's cross-card sync to be visible at all when live-sim is off.
5. **vmin% percentile now excludes exact-zero pixels**
   (`widgets.py::ImageViewer._redisplay`). On the Hydra composite's mostly
   -empty `BigDet` canvas (see `hydra.composite`'s NaN→0 fill for the max
   op) those zeros dominated the percentile and washed out the auto-level
   window; masked on the raw data (not the log-transformed display array,
   since `log10(0) != 0`), with a fallback to the unfiltered set if the
   whole frame is exactly zero. Single fix point in the `ImageViewer` base
   class — benefits every viewer in the app, not just Hydra's.

New/expanded regression coverage in `tests/test_hydra_ui.py`: a beam-centre
-edit-updates-profile check and a shared-field-sync check were folded into
the existing `test_hydra_composite_builds_with_matched_calibration` test
(rather than each getting its own `DataViewerTab`) specifically to avoid
pushing the file over the pyqtgraph-teardown-crash threshold described in
the entry directly below — adding even one more heavy Hydra-page test
function to this file flipped the crash from "never observed in 3 runs" to
"reliably crashes," and folding into an existing test brought it back to
"occasional, as before." If more Hydra-UI coverage is needed later, prefer
extending an existing test over adding a new one, or address the pyqtgraph
issue at its root (see that entry's suggestions) first.

## 2026-08-23 — Hydra composite rotation direction: clockwise, not CCW (supersedes the chirality entry below for direction, not for the X-mirror conclusion)

Issue 3 above, isolated: `hydra.py::compute_inv_coords`'s inverse-mapping
rotation implemented `world→panel = R(-tx)`, i.e. forward `panel→world`
placement was `R(+tx)` — **counterclockwise** by `tx`, in this app's
Y-right/Z-up, bottom-left-origin display convention (that convention makes
CCW visually match standard CCW with no extra mirroring — see the
origin-flip entry below). The user's nominal physical Tx values (GE1 297,
GE2 27, GE3 117, GE4 207) match the bundled default files' `tx` almost
exactly, confirming `tx` *is* the intended physical angle — only the
rotation **direction** was backwards. Fixed with a one-line change:
`tx_rad = math.radians(-tx_deg)` (was `math.radians(tx_deg)`).

**Why the prior "no chirality/X-mirror correction needed" verification
(entry below) didn't catch this**: that check confirmed *self-consistency*
— real fitted-calibration Debye-Scherrer rings stitched into continuous,
correctly-curving arcs across all four panel boundaries, and the composite
rendered through the real viewer pipeline looked right. But ring
continuity for circularly-symmetric data centred on a shared beam centre is
a weak test against a *globally consistent* direction error: reflecting/
rotating all 4 panels the same wrong way can still produce locally
plausible, continuous-looking arcs, especially when each panel's own tilt
(ty/tz) was fit independently without reference to absolute world
orientation. It takes someone who knows what the physical detector
actually looks like — which is exactly what a real windowed Phase 6 pass
provides and an offscreen ring-continuity check cannot — to catch an
overall-orientation bug like this. Verified two ways: `tests/
test_hydra_geometry.py::_expected_composite_xy` and `tests/
test_hydra_chirality.py::_expected_xy` each independently hand-derive the
same rotation (typed out separately, not calling `compute_inv_coords`) —
both updated to negate `tx_deg` the same way, and both still pass,
confirming production code and an independently-typed formula agree on the
corrected direction. **Still recommended**: a real windowed comparison
against `test_data/s1ide` (real CeO2 Hydra data) against known physical
detector orientation, before fully closing out Phase 6.

## 2026-08-23 — Rare pyqtgraph teardown crash under a large test suite; mitigated, not fixed

While adding `tests/test_hydra_ui.py` (each test builds a full
`DataViewerTab`, i.e. the single-detector page's own viewer/profile plot
*plus* the Hydra page's 5 `DetectorGeometryCard`s sharing one more viewer —
a lot of `pg.ViewBox`/`pg.ImageView` instances per test), the full pytest
suite intermittently crashed the interpreter outright (`Fatal Python error:
Segmentation fault` / `Bus error`), not just failed an assertion. Tracebacks
point into pyqtgraph's own `ViewBox.forgetView`/`WidgetGroup.autoAdd`
internals — a known category of pyqtgraph fragility in its **global**
ViewBox/WidgetGroup registries when many `ImageView`s are constructed and
destroyed across one long-running process, not a bug in this codebase's
own code.

- Adding `gc.collect()` in an autouse per-test teardown fixture made it
  **worse** (crashed sooner) — forcing Python-level GC mid-teardown
  apparently hits pyqtgraph's half-torn-down C++/Python object graph more
  often than letting it happen lazily.
- Just pumping the event loop (`app.processEvents()`) after each test, with
  no forced GC, measurably reduced the crash rate: reliably crashed before
  the fix, 3-for-3 clean full-suite runs after it (still probabilistic, not
  a guaranteed fix — pytest ran this at pass exit code with only the one
  known pre-existing `test_app_builds_offscreen` assertion failure each
  time, but a 4th or 5th run could still hit it).
- **This is a pre-existing pyqtgraph characteristic** made more likely to
  surface by this session's Hydra tests specifically because they multiply
  the number of live `ImageView`/`ViewBox` instances per test file quite a
  bit. If this recurs (in CI or locally) and the `processEvents()` mitigation
  in `tests/test_hydra_ui.py`'s `_qt_teardown` fixture isn't enough,
  consider: running Hydra UI tests in a separate pytest process (e.g.
  `pytest-forked`), reducing the number of `DataViewerTab`/`ROIImageViewer`
  instances built across the Hydra test files, or filing upstream against
  pyqtgraph — do not just add more `gc.collect()` calls, that direction is
  already confirmed to make it worse.

## 2026-08-23 — Hydra composite needs no chirality/X-mirror correction in this codebase

**Superseded in part** by the "Hydra composite rotation direction" entry
above (same date): the X-mirror conclusion below still holds, but the
rotation *direction* this entry validated as self-consistent (CCW) turned
out to be physically backwards (should be CW) — see that entry for why a
ring-continuity check couldn't tell the two apart.

While porting the Hydra (4-panel GE detector) windmill-compositing engine
(`midas_gui/hydra.py`, ported from `midas_saxs_waxs/midas-gui-swaxs`'s
`hydra.py`), the reference project's own JSP-fork code (`gui_common.py`'s
`MIDASImageView`) mirrors the composite's X axis (`origin='br'`) because,
per its own comment, "the HYDRA composite... stitching introduces an X-axis
flip that needs cancelling at display time." Whether this codebase's
`pg.ImageView`-based viewers (which only ever override `invertY(False)`,
never `invertX`) need an equivalent correction was an open question flagged
in the implementation plan, not something to assume either way.

**Verified empirically, two ways, using real local `test_data/s1ide` CeO2
Hydra data (read-only, not committed):**
1. Built the composite with the real per-panel fitted calibration
   (`park_may26_bc/refined_MIDAS_params_ge{1..4}_Tx_cake.txt`) and rendered
   it at a tight intensity window to reveal the Debye-Scherrer rings — they
   stitch into **continuous, correctly-curving arcs across all four panel
   boundaries** (no angular discontinuity, no local mirroring at any seam).
   This is the physically-grounded check: a per-panel chirality error would
   show as a ring reflecting rather than continuing at a boundary.
2. Rendered the same composite through the actual `ROIImageViewer` (real
   `invertY(False)` pipeline, via `widget.grab()`) — the windmill
   arrangement displays correctly oriented; a global Y-flip (which is all
   this codebase's viewers ever apply) doesn't break ring continuity or
   introduce a chirality error, since it's a pure whole-canvas reflection,
   not a per-panel one.

**Conclusion: no `invertX`/X-mirror is needed anywhere in `hydra.py` or the
Hydra viewer for this codebase.** The reference project's `origin='br'`
requirement is specific to how JSP's own `MIDASImageView` derived its
inverse-coordinate math relative to *its* particular display convention —
it doesn't transfer to this codebase's ported (unmodified) math running
under our own bottom-left-only convention. `compute_inv_coords`/
`remap_to_composite` were ported byte-for-byte from the reference with no
sign changes. If a *future* change to the compositing math or the base
viewer's invert settings is made, re-run this same two-part check (ring
continuity + real-viewer render) before assuming the sign convention still
holds — this is the "looks plausible but is secretly mirrored" class of bug
that has no other automated guard beyond `tests/test_hydra_chirality.py`'s
synthetic-marker regression test.

## 2026-08-23 — Detector-image origin flipped to bottom-left (MIDAS convention)

User made a standing design decision: every image viewer in the GUI must
place pixel `(0,0)` at the **bottom-left** corner, not top-left. Reason:
MIDAS assumes this origin, and the Flip Y/Flip Z/Transpose (`ImTransOpt`)
controls only make sense as "align raw detector readout to match the world
view of the detector looking downstream from the sample along the beam" if
the GUI's own baseline rendering convention matches that world view.
Uncommitted — code + docs done, not yet committed/pushed per user request.

- **Root cause, found via two exploration passes**: `pg.ImageView.__init__`
  (pyqtgraph internals, not app code) unconditionally calls
  `self.view.invertY()`, which is the *only* thing forcing top-left origin
  anywhere in this app. `ImageViewer` (`widgets.py`), and its subclasses
  `PickableImageViewer` (Calibrate) and `ROIImageViewer` (Data Viewer), all
  share the one `pg.ImageView` built in `ImageViewer.__init__` — so a single
  `vb.invertY(False)` there (right after `vb = self._iv.getView()
  .getViewBox()`) fixes all three tabs at once.
- **The other three 2D image displays in the app** (`tab_pumpprobe.py`'s
  ΔI(q,delay) heatmap, `tab_texture.py`'s pole figure,
  `widgets.py::WaterfallViewer`) use a plain `pg.PlotWidget`/`ImageItem`
  with no `invertY` call — already bottom-left by pyqtgraph's own default,
  and not detector-pixel-space displays anyway (frame index/q/angle/radius
  axes), so explicitly left untouched.
- **Confirmed safe to treat as a pure visual flip**, no data/geometry math
  changes needed anywhere:
  - All click-to-pixel code (`ImageViewer._mouse` crosshair,
    `PickableImageViewer` BC/ring-pick, `roi_tools.py` ROI drag/raster/
    `getArrayRegion` sampling, `tab_mask.py` point/polygon drawing) goes
    through pyqtgraph's `mapSceneToView`/`mapFromScene`, which are
    **invert-aware** — verified empirically (round-trip test: mapping a
    data-space point to scene and back returns the identical point
    regardless of `invertY`; a screen click near the bottom of the widget
    numerically maps to the correct, now-smaller, row index).
  - Calibrate's ring-geometry math (`tab_calibrate.py::_draw_rings`,
    `_draw_corrected_rings`, `helpers.py::tilted_ring_xy`/
    `_tilt_matrix_np`) computes purely in pixel-index/detector-frame
    coordinates with no reference to `invertY` anywhere — confirmed by
    reading every line of both functions. Rings are added into the same
    ViewBox as the image, so they flip together with it automatically.
  - **Exactly one place explicitly compensated for the old top-left
    default**: `roi_tools.py`'s `ROIStatsPopup` zoomed-crop preview (a
    separate plain `PlotWidget`) called `self._crop_vb.invertY(True)`
    specifically to *match* the main viewer — flipped to `invertY(False)`
    in lockstep.
  - `ImageViewer._redisplay`'s `.T`/log10 transpose and `set_mask_overlay`'s
    `.T` handle pyqtgraph's row/col-major **axis order**, a completely
    separate concern from the Y-**invert** direction — left untouched.
- **Verification was render-based, not just coordinate math**: coordinate
  round-trips are self-consistent regardless of `invertY` by design, so
  they can't prove the on-screen direction actually changed. Wrote a
  throwaway offscreen script (`QT_QPA_PLATFORM=offscreen`) that fed each
  viewer a synthetic array with row 0 marked bright, called `widget.grab()`
  to get an actual rendered `QImage`, and checked the bright band lands at
  the bottom of the pixmap — confirmed for `ImageViewer`,
  `PickableImageViewer`, `ROIImageViewer`, and the `ROIStatsPopup` crop
  preview (this last one required feeding the transposed array, since the
  real code path samples `self._data.T` before it ever reaches the crop
  widget — a naive same-array test gave a false "still top-left" result at
  first). Full pytest suite re-run clean afterward: 44 passed, 1 failed
  (the pre-existing `test_app_builds_offscreen` `visible_tabs`
  double-counting flakiness already logged in `STATE.md`, reconfirmed
  unrelated by re-running on unmodified `main` via `git stash`).
- **Docs updated**: `gui_documentation.md` gained an "Image orientation"
  note (in §1 Overview) stating the bottom-left convention and its
  independence from the `ImTransOpt` Transforms checkboxes, plus the
  existing box-ROI annotation description's "top-left corner" →
  "bottom-left corner" (the box's `roi.pos()` minimum-`(x,y)` corner is the
  same numeric corner as before — only which screen corner it visually
  renders at changed, so this was purely a documentation/comment fix, not a
  logic change).
- **Unrelated hiccup during verification, not caused by this change**: an
  early full-suite pytest run left several committed `test_data/*.h5`/
  `*.tif`/`make_test_data.py` files showing as deleted in `git status`.
  Restored via `git checkout -- test_data/`; reproduced clean on a second
  full-suite run. Ruled out as caused by this session's edits (grepped
  `tests/*.py` and `conftest.py` — nothing references those paths;
  reproduced the same deletion after `git stash`-ing this session's edits
  and running only `test_smoke.py` on stock `main`). Root cause not
  identified — flagged to the user, not investigated further since it's
  orthogonal to this task and did not recur.

## 2026-08-12 — Mask tab dilation: switched to 8-neighbor growth (follow-up)

User corrected the dilation semantics from the previous session: they want
**8-neighbor** (full-block) growth, not 4-connected. Spec: dilation=1 → the
entire 3×3 block around a bad pixel becomes bad; dilation=2 → the entire
5×5 block, etc.

- Changed `binary_dilation(m, iterations=n)` → `binary_dilation(m,
  structure=np.ones((3,3), dtype=bool), iterations=n)` in `_set_mask()`.
  An explicit full 3×3 structuring element with `iterations=n` gives
  exactly a `(2n+1)×(2n+1)` square per isolated bad pixel (Chebyshev-
  distance ≤ n), matching the spec precisely — no custom BFS/ring logic
  needed, scipy's structure+iterations composition already does it.
- Everything else about the feature (insertion point in `_set_mask()`,
  hand-drawn shapes never dilated, `_state_widgets()` persistence) was
  unchanged — this is a pure structuring-element swap.
- Re-verified with the same style of targeted offscreen script: isolated
  bad pixel at dilation 1/2 now yields exactly 9/25 True pixels (was
  5/13 under 4-connected), and the hand-drawn-pixel-not-grown check still
  passes.
- New commit `188ea77` (+ docs `9948695`) on the same
  `feature/mask-multiselect-dilation` branch, rather than amending
  `429d41a` — branch isn't merged/pushed yet, but per the project's git
  workflow rule new commits are still preferred over amending so the
  history shows the correction was made in response to explicit feedback.

## 2026-08-12 — Mask tab: multi-file stack picker + bad-pixel dilation (branch, not merged)

User asked for two additive Mask Builder changes on a separate branch
(`feature/mask-multiselect-dilation`, off `main`, not pushed/merged): (1) a
way to hand-pick individual files for a temporal stack instead of only a
whole folder, and (2) configurable dilation of identified bad pixels.

- **Explicit `self._stack_files` list, not a delimiter-packed string in the
  `QLineEdit`.** Keeps the multi-select path fully separate from the
  existing folder/file/glob text parsing in `_collect_stack_paths()`, which
  checks `self._stack_files` first and falls through otherwise. Files are
  sorted on selection for a deterministic frame order regardless of the
  OS file-picker's click/selection order.
- **`_on_stack_path_changed` clears `_stack_files`** as its first action, so
  switching back to Folder/File/typed-path input (all of which route through
  `QLineEdit.setText` → this handler) automatically drops stale multi-select
  state. `_browse_stack_files()` relies on this: it calls `setText()` first
  (synchronously firing the clear) and only assigns `self._stack_files =
  files` afterward, so no `blockSignals` dance is needed.
- **Dilation applied once, in `_set_mask()`, to `self._computed_mask` only —
  never `self._drawn_mask`.** `_set_mask()` is the single convergence point
  for all three mask-producing paths (threshold-only short-circuit,
  `MaskComputeWorker` result, mask loaded from disk), so this is the only
  place dilation needs to be added. Hand-drawn shapes are combined in
  afterward via `_emit_final()`'s OR, so they're never grown — matches the
  user's request literally ("dilation of the bad pixels identified", not
  hand-drawn regions the user placed deliberately).
- **`scipy.ndimage.binary_dilation(m, iterations=n)` with the default
  4-connected structuring element** is an exact semantic match for the
  user's spec: "dilation of 1" = all pixels directly connected to a bad
  pixel, "dilation of 2" = one further ring, etc. `scipy` was already a
  pinned dependency and `scipy.ndimage` already used the same way
  (`median_filter` in `workers.py`), so no new dependency.
- **Two atomic commits, not one combined diff** — `cc63d5a` (multi-select)
  then `429d41a` (dilation), even though both were designed and functionally
  verified together first. Each commit's `documentation/gui_documentation.md`
  update is scoped to only that commit's feature (separate "Last
  updated"/"Previously" header rotations), so the doc history stays
  one-commit-per-entry like the rest of the file.
- **Verification was a targeted offscreen script** instantiating `MaskTab`
  directly and asserting exact pixel counts at dilation 0/1/2, plus that a
  hand-drawn pixel survives untouched at dilation=5 while the computed mask
  doesn't reach it (Manhattan-distance check) — not the full GUI, since no
  interactive display is available in this environment.
- **Full-suite pytest segfault discovered and ruled out as pre-existing.**
  Running the *entire* suite (not just `test_smoke.py` alone) segfaults
  during `test_tab_visibility_toggle`'s teardown, in pyqtgraph's `PlotWidget`
  garbage collection triggered by `tab_pdf.py`'s reduction-plot widgets. This
  reproduces identically after `git stash` on a state with none of this
  session's mask-tab changes applied — confirmed unrelated to `tab_mask.py`,
  a pre-existing interaction between the PDF tab's pyqtgraph plots and
  offscreen Qt teardown. Not root-caused further or reported upstream; noted
  in `STATE.md` for future investigation.
- **Not merged to `main` or pushed** — user asked for a separate branch only;
  no instruction to open a PR or merge.

## 2026-08-12 — PDF tab rebuilt for full ROADMAP Stage 2-3 workflow

User wanted the PDF tab built out from Stage-1-only (composition-weighted
Faber-Ziman S(Q)→G(r)) to the full Stage 2-3 workflow, grounded in
`midas_pdf`'s own `examples/`/`notebooks/` and a validated end-to-end
reference script run on real beamline data, plus a dedicated test dataset.
Scoped explicitly to Stage 2-3 (empty-cell/Paalman-Pings absorption,
detector-efficiency correction, absolute normalization, differentiable
multiple scattering, fluorescence diagnostic, CIF-driven structure
refinement, Δ-PDF) — **excluding** Bayesian SVI/NUTS, RMC, SAXS/SANS joint
refinement, multi-phase/core-shell, anisotropic ADP, directional strain-PDF
(user decision, kept out of `pdf_backend.py`'s re-exports on purpose).

- **Test data ships as `test_data/test_pdf/`** (raw Varex frames, calibration,
  a rasterized `.tif` mask, pre-integrated I(Q) for Ni/CeO₂/IPA/Kapton/
  air-scatter, and an authored `Ni.cif`) but is **gitignored**
  (`/test_data/test_pdf/` in `.gitignore`, ~320 MB raw) unlike the rest of
  `test_data/`, which ships with the repo — same treatment as
  `test_data_pump_probe/`. `constants.py`'s `DEFAULT_PDF_*` point here so the
  tab opens ready-to-run on this machine; a fresh checkout elsewhere just
  won't have data preloaded.
- **Mask rasterized once, offline**: the source data only has GSAS-II
  `.immask` polygon masks, and this GUI has no GSAS-II mask parser anywhere.
  Rather than add one, the beamstop-arm polygon was rasterized to a `.tif`
  (nonzero=masked) matching the GUI's own mask convention
  (`tab_mask.py::_load_existing_mask`) — keeps the "fix GUI-side, don't grow
  parsers we don't need" pattern intact.
- **QCheckBox, not checkable QGroupBox, for every optional-stage toggle**:
  `widgets_to_dict`/`apply_dict_to_widgets` (`helpers.py`) only persist
  `QAbstractSpinBox`/`QComboBox`/`QLineEdit`/`QAbstractButton` subclasses, and
  `QGroupBox` — even `setCheckable(True)` — is **not** a `QAbstractButton`
  subclass. A checkable groupbox's checked state would silently fail to
  save/restore via Save/Load GUI State. Fixed by using explicit
  `QCheckBox` "Enable" widgets inside each groupbox instead, consistent with
  the existing `self._refine` convention elsewhere in the tab.
- **Two API gotchas worth remembering** (both would silently misbehave if
  reintroduced): `pdf.delta_pdf(...)` requires `torch.Tensor` inputs, not
  numpy arrays (`tab_pdf.py::_run_delta_pdf` wraps with `torch.as_tensor`
  before calling); `refine_structure`'s `fitted` dict can contain a
  `"bg_coef"` key whose value is a `list` (when `bg_order` is set) rather than
  a scalar — any code formatting `fitted.items()` must branch on
  `isinstance(v, (list, tuple))` (done in `_on_fit_done`/`_redraw_fit`).
- **Detector-efficiency amplification (~30× for 500 µm Si at 67 keV) is
  physically correct, not a bug** — confirmed by directly computing
  `pdf.detector_efficiency(...)`, which returns η≈0.03-0.04 for that
  material/thickness/energy combination; a thin, low-Z sensor is genuinely
  only a few percent efficient at hard X-ray energies.
- **σ inflate is a user-tunable knob on the Structure Fit tab, not
  hardcoded**: the validated reference script needed ×20 to make χ²/ndof
  sane on real (non-synthetic) beamline data — that factor is
  dataset-specific systematics, not a universal constant, so it's exposed
  rather than baked in.
- **Verification**: full offscreen pytest (26/26 green, including the
  previously-known-flaky `test_smoke.py::test_app_builds_offscreen`, which
  turned out to be a stale local `visible_tabs` config issue rather than a
  code bug — resolved itself, not touched directly). Manual functional pass
  driven through the actual `PDFTab` widgets (not just the workers directly):
  Stage-1 file-mode Ni reduction (first-shell peak at 2.50 Å), background
  subtraction + Paalman-Pings + absolute normalization → structure fit on
  `Ni.cif` recovering `a=3.5247 Å` (expected 3.524 Å) with physically sane
  `u_iso=0.0090`, detector-efficiency + multiple-scattering + tail-flatten all
  enabled together end-to-end (nonzero `ms_beta_median`), Δ-PDF between two
  saved states, and a full `get_state()`/`set_state()` round-trip including
  the manual-crystal atom table.

## 2026-08-10 — Upgrade all MIDAS backend pins; retire vendored `midas_pdf`

User asked for a full MIDAS package upgrade plus a thorough audit of
`environment.yml`/`pyproject.toml` so a fresh workstation install works
without issues, then to functionally verify the GUI against the upgraded
stack (not just import-check it). Supersedes the `~2026-07-07` vendoring
entry below.

- **Version bumps** (checked against PyPI metadata, not the local dev conda
  env, which had drifted to git-based installs): `midas-hkls` 0.4.1→0.7.0,
  `midas-calibrate-v2` 0.5.2→0.5.3. `midas-integrate-v2`, `midas-calibrate`,
  `midas-distortion` re-verified at their existing pins.
- **`midas_pdf` is now the real public PyPI package (0.1.1)**, not vendored.
  Deleted `midas_gui/_vendor/` entirely (33 files) and rewrote
  `pdf_backend.py` to a plain `import midas_pdf` + re-export — no more
  `sys.path` activation, no more `midas_hkls.absorption` compatibility shim.
  **Why safe now:** `midas-hkls>=0.5.0` (we're at 0.7.0) ships `absorption`
  natively, so the shim's entire reason for existing is gone.
- **Newly pinned for completeness** (previously relied on implicitly, not
  declared anywhere): transitive MIDAS deps `midas-integrate==0.4.2`,
  `midas-peakfit==0.5.0`, `midas-zipper==0.1.5`, plus `hdf5plugin==7.0.0`,
  `psutil==7.2.2`. `pyproject.toml` specifically was missing `numba` and
  `scikit-image` outright — `scikit-image` is a soft/try-except optional dep
  of `midas-calibrate-v2`'s `auto_seed_calibrant` (better ring seeding) that
  no MIDAS package declares in its own metadata, so a plain `pip install .`
  would silently fall back to coarser arc seeding without it.
  **Why:** the goal was a `pip install .` that reproduces the exact
  verified-working set on its own, without leaning on `environment.yml` or on
  another package's `install_requires` happening to pull the right version.
- **NumPy-1.x pin chain unchanged and re-confirmed**: numba 0.59.x needs
  NumPy<2.1, torch 2.4.0 targets NumPy 1.x, pvapy 5.5.0+ needs numpy>=2.0
  (stays pinned at 5.4.1). numpy stays at 1.26.4.
- **Verification method:** built an isolated Python 3.12 venv
  (`/tmp/midas_gui_verify_venv/`, not the user's live dev conda env) with the
  full upgraded pin set from a clean `pip install`, confirmed no resolver
  conflicts and the numpy/torch/numba pins held. Ran the full pytest suite
  (25/25 green, offscreen Qt). Then wrote and ran an end-to-end functional
  smoke test (`e2e_smoke.py`, not committed — scratch) that fabricates a
  synthetic CeO2 ring-pattern detector image via
  `midas_hkls.generate_hkls` + `midas_pdf.validate.synthetic_powder_image` +
  `midas_calibrate_v2.compat.to_integrate.spec_from_calibration_result`, then
  drives it through the GUI's own code paths end to end: auto-seed
  (`midas_gui.calib.make_seed_safe`) → full `one_shot` auto-calibration
  (`calib.run_pipeline` + `normalize_result`) → radial integration
  (`midas_gui.workers.build_geom`/`integrate_frame`) → PDF reduction
  (`midas_gui.pdf_backend.i_of_q_to_Gr`). All five steps passed. Also checked
  `inspect.signature()` on every other calibration entry point the GUI calls
  (`first_time_calibrate`, `autocalibrate_four_stage`,
  `autocalibrate_bayesian`, `autocalibrate_joint`, compat helpers) — no
  signature drift in `midas-calibrate-v2==0.5.3`.

## 2026-07-16 — Adopt two-layer .context system

Adopted the STATE (disposable-but-current) + DECISIONS (permanent) split so
returning to this project after a long gap is cheap: only STATE.md auto-loads;
detail is read on demand. `.context/` is committed to git so it travels
between workstations.

Initial content below was inferred from existing Claude auto-memory + repo
state, not from a live work session — verify against code before relying on
file:line specifics.

## 2026-07-17 — Migrated legacy `claude/` knowledge into `.context/`

Folded the old per-session `claude/` folder into this two-layer structure and
deleted it. The durable technical decisions below were extracted from
`claude/context/*` + `claude/analyze_workflows/*` + `claude/gui_plan.md`. The
build-critical `midas_pdf` reference stack was **moved** to
`.context/reference/midas_pdf/` (not summarized — too detailed to lose).
Stale files discarded: `CLAUDE_original_scratch.md` (scratch-era) and
`claude/gui_documentation.md` (a strict subset of the shipped doc).

### Standing engineering decisions (with why)

- **Never edit MIDAS backend packages; fix everything GUI-side** as
  "correct-usage workarounds." A round-trip validation found real package bugs;
  the rule is to work around them, not make the GUI cleverer than the packages.
  Package-side fixes are logged for MIDAS maintainers, deliberately not done here.
- **Always build specs via `spec_from_calibration_result()`**, never manual
  `IntegrationParams` (wrong RhoD units → distortion polynomial explodes).
- **No Lsd auto-estimation from ring radius** — ring scoring always picked the
  wrong ring (ambiguous assignment). User picks the ring via an explicit
  2θ→Lsd table; "Pick Ring" gives BC+radius, Lsd stays manual.
- **Corrections profiles normalized by a counts-cake** (P0-1 workaround):
  `integrate_with_corrections` returns summed/unnormalized counts, so divide by
  a per-bin count cake before the η-mean or every profile gets a spurious
  rising radial background.
- **Q-uniform output = R→Q rebinning in the GUI, not the kernels** (P0-2):
  setting spec QMin/QMax flips q_mode but kernels still build R-uniform edges →
  rings at wrong Q. Integrate R-uniform, then `np.interp` onto the Q grid.
- **Refinement stays derivative-free (Nelder-Mead), not autograd** — the
  differentiable integrator returns NaN gradients w.r.t. BC/tilt (atan2
  singularity at R→0). NM on η-uniformity converges (BC +6px → within 0.05px).
- **Calibrate tab recommends four_stage / first_time for tilt/strain** —
  one_shot and bayesian report a spurious ~−3° tilt on weakly-tilted data
  (self-compensating degeneracy; Lsd/BC still fine, reported tilt isn't).
- **`make_seed` always `use_diplib=False`** — diplib segfaults on macOS.
- **Abort = terminate the worker thread**, not cooperative-only (safe-cancel
  merely unfroze the GUI while work continued, confusing the user). Manual
  stdout restore on terminate; Batch keeps frames already written.
- **Pixel-count-weighted azimuthal averaging is the default** (`weighted=True`)
  — legacy unweighted η-bin mean distorts badly with an off-detector BC (46%
  change 2°→10° η on test data).
- **Data Viewer radial integration is pure numpy** (no MIDAS spec) so it runs
  without a calibration loaded; loading a calibration.json just supplies geometry.
- **Keep previous GUI versions; never edit vN in place — create vN+1.** Old
  GUIs are frozen.
- **`_paths.py` reduced to an inert stub**; backends are separate installs
  (optional-deps group `midas`), no `sys.path` manipulation.

### Qt / pyqtgraph gotchas (recurring root causes)

- `autoRange(axes='x')` raises TypeError on this pyqtgraph version → use
  `setXRange(min, max, padding=0.02)`.
- `pg.ImageView.setImage()` defaults `autoRange=True, autoHistogramRange=True` —
  pass both `False` on redraw (`ImageViewer._redisplay`) or zoom/pan resets
  every frame.
- `imageItem.setLookupTable()` is reset by every `setImage()` — use
  `iv.setColorMap()`.
- `pg.SignalProxy` and QThread workers must be stored as instance vars or GC'd.
- Stale `.pyc` caused phantom signature errors — clear `__pycache__` after
  signature changes.
- Linux non-native QFileDialog inherits the global light `QWidget` color →
  invisible file names; needs explicit dark item-view colors in `style.py`
  (macOS native dialog unaffected).
- PyQt5 ≥5.5 hard-aborts the process on a slot exception → own `sys.excepthook`
  + faulthandler installed; Windows crash log at `%USERPROFILE%\midas_gui_error.log`.

## ~2026-07-07 — PDF tab: vendor `midas_pdf`, don't depend on it (superseded 2026-08-10)

**Superseded:** `midas_pdf` is now a real published PyPI package; the vendor
tree and the `midas_hkls.absorption` shim described below were removed on
2026-08-10 (see entry above). Left in place as historical record of why the
vendoring existed.

Replaced the GUI's monoatomic PDF path (`midas_integrate_v2.pdf`, no
composition) with real Faber-Ziman **polyatomic** G(r).

- **Vendored** `midas_pdf` into `midas_gui/_vendor/midas_pdf/`, imported as
  top-level `midas_pdf` through a single backend module
  `midas_gui/pdf_backend.py`. Vendored tree (incl. `data/*.json`) is packaged
  as `midas_gui` package data in `pyproject.toml`.
  **Why:** `midas-pdf` isn't published yet; vendoring keeps the GUI installable.
- **Required dependency shim:** installed `midas-hkls` is 0.4.1 and lacks the
  `midas_hkls.absorption` submodule that `midas_pdf` imports at load. A shim is
  registered **before** importing `midas_pdf` (`Z_for` from
  `midas_hkls.anomalous`; `atomic_mass` from `midas_pdf.placzek._ATOMIC_MASS_U`;
  `element_density`/`mass_attenuation_coefficient` stubbed).
  **Why:** without it `import midas_pdf` fails in this environment.
- **Scope (Stage 1, done):** Composition → Faber-Ziman S(Q)→G(r) with ±1σ
  band, Compton toggle, `refine_normalization` (scale/background), G/g/T/R +
  F(Q) family. I(Q) source = both (integrate a detector image in-tab OR load a
  pre-integrated Q,I,σ file). **Deferred (Stage 2–3):** CIF structure fit,
  Δ-PDF, multiple scattering, absorption — need `midas-hkls>=0.5.0` to drop
  the shim.
- Verified: Ni first shell ~2.50 Å; offscreen 9-tab build + pytest green.

## Environment / tooling notes

- **macOS TCC:** the Claude Code process cannot read `~/Downloads`,
  `~/Desktop`, `~/Documents` (`Operation not permitted` even with sandbox off).
  Keep any external source dirs outside those three. `midas_pdf` source lives
  at `/Users/dbeniwal/ANL-research/midas_pdf_src/`.
- `environment.yml` is pinned to a verified-working NumPy-1 set (commit 68313f8).
