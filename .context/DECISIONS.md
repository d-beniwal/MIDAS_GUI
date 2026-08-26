# DECISIONS — append-only, newest first

Each entry: what was decided and *why* (the reasoning that would be expensive
to reconstruct later). Never rewrite history; add a new entry to supersede.

## 2026-08-26 — Workspace/Project UX rework: unify the mental model at the UI layer only, never touch the FAIR HDF5 schema (`6b1564b` + docs `b4a6dfe`, branch `workspace_ux`)

User feedback: creating/saving/loading a "project" didn't feel convenient or
approachable; asked for a rethink on par with established software (VS
Code, Photoshop, Xcode, Blender), while guaranteeing FAIR data provenance
stays exactly as rigorous. Explicitly asked for this to land on a separate
branch (`workspace_ux`) so it can be reviewed and kept-or-discarded
independently of `main`, and asked to proceed without further check-ins.

**Root-cause investigation (via two research subagents) found the problem
wasn't one broken feature — it was two functionally unrelated concepts both
occupying the "Project" mental-model space with none of the affordances
users expect from any of them:** "GUI State" (`Ctrl+S`/`Shift+S`/`Ctrl+O`, a
mutable JSON widget-value snapshot — what a user actually means by "my
project" day to day) and "Project" (File ▸ New/Open/Close Project, no
shortcuts, an append-only HDF5 FAIR-provenance log — deliberately immutable
and opt-in by design). Neither had a recent-files list, a dirty/unsaved
indicator, autosave/crash recovery, or an in-app way to browse a project's
own records (only `h5dump`/HDFView). No icon/resource/QSettings
infrastructure existed anywhere in the repo (confirmed via full-repo
search) — ruled out thumbnails for this pass as a separable, larger effort.

**Guiding constraint, honored throughout: unify the UX/mental model without
ever touching `project.py`'s HDF5 schema, its `create_project`/
`open_project`/`append_*_attempt` functions, or its opt-in/append-only
guarantees.** Every new feature either reuses `project.py`'s existing
read-side API (`discover_panels`/`list_attempts`/`read_attempt`/
`read_attempt_results`) or lives entirely in new, additive sidecar/config
files. No migration needed for existing `.h5` or `.json` files.

**Decisions locked in via AskUserQuestion before implementing (user picked
the recommended option each time):**
1. Full scope in one pass (not a smaller slice): recents, rename, dirty-
   state + confirm-on-close, autosave/crash-recovery, **and** the new
   Project History viewer.
2. Rename "GUI State" → **"Workspace"** in every user-facing label (menus,
   dialog titles, window title) — shortcuts unchanged, JSON format
   unchanged. Mental model: **Workspace = editable draft, Project =
   permanent FAIR record it can optionally log into.**
3. The new recent-files list is **global** (not scoped per beamline
   Profile) — stored in a new sibling file next to `profile_meta.json`,
   not inside a profile's own JSON, since a recently-opened project
   shouldn't stop being "recent" just because the active Profile changed.

**Dirty-state tracking deliberately reuses each tab's existing `get_state()`
rather than wiring per-widget change signals across 10 tabs.** A shared
`_serialize_workspace()` helper (refactored out of the old inline
`save_gui_state` body) is called on a ~7s `QTimer`; its result is hashed
(`sha256` of `json.dumps(..., sort_keys=True)`) and compared to the hash
captured at the last save/load. Passing `sidecar_stem=None` on every timer
tick — confirmed by reading `tab_mask.py`/`tab_calibrate.py`'s `get_state`
bodies — skips their mask/calibration sidecar file writes entirely, so the
periodic check has zero disk I/O side effects and is cheap enough to run
every few seconds. This is far less code than instrumenting every widget's
`textChanged`/`valueChanged`/etc. signal, and can never drift from what
`save_gui_state` actually persists, since both now share one code path.

**Autosave/crash-recovery is wired *only* from `main()`, never from
`MainWindow.__init__`/`_build_ui` — this was the trickiest correctness
constraint.** Every existing test constructs `MainWindow()` directly (not
via `main()`); if the restore-prompt check ran during construction, a
leftover autosave draft on a dev machine would pop a blocking modal
`QMessageBox` under the offscreen QPA platform with no user to click it,
hanging any test (and CI) that builds a `MainWindow`. Confirmed this isn't
theoretical: an early test-writing pass that called `save_gui_state()`
directly (which ends in a real, unstubbed `QMessageBox.information`) hung
for the full 120s tool timeout under `QT_QPA_PLATFORM=offscreen` before the
fix (stub `QMessageBox.information`/`.critical` in tests that must call
these methods — see `tests/test_workspace_ux.py`'s `no_modal_dialogs`
fixture). The periodic dirty-check/autosave `QTimer`s themselves *are*
started unconditionally in `__init__` (they show no UI, matching the
existing precedent of `bridge_server.start()` and `settings.py`'s
profile-directory writes already happening unconditionally on
`MainWindow()` construction in every test) — only the modal *restore
prompt* needed the `main()`-only guard.

**New Project's optional Workspace pairing simplified from the original
plan's "checkbox in the creation dialog" to a plain follow-up
`QMessageBox.question`.** Same user-facing outcome (offer to save+link a
Workspace right after creating a project, default Yes) with far less code
than a custom dialog subclass — reuses `save_gui_state()` verbatim, which
already shows its own completion dialog.

**Project History viewer shows raw JSON metadata in a detail pane instead
of parsing app-specific fields into extra table columns.** The project
schema's `metadata` blob varies by attempt kind (Calibrate vs. Integrate)
and has evolved before (see the ImTransOpt-era `2358ae4` entry above); a
generic "select a row, see its full recorded JSON" viewer can't drift out
of sync with the schema the way column-specific field extraction could.

**Verification:** all-green per-file-isolated pytest across every touched/
added test file, including a fresh `tests/test_workspace_ux.py` (9 tests:
recent-files roundtrip/pruning/cap, dirty-flag hash-diff, autosave write/
clear, restore-on-relaunch accept/decline, `ProjectHistoryDialog` against a
fixture project with both a Calibrate and an Integrate attempt). Confirmed
via `git stash` A/B that the pyqtgraph interpreter-teardown crash risk and
the `test_app_builds_offscreen` stale-config flake (both already documented
above) reproduce identically on unmodified `main`, so neither is a
regression introduced here.

## 2026-08-25 — ImTransOpt fix: the MIDAS backend does the pixel flip everywhere it can; GUI only flips masks (never images) and only for on-screen display (`2358ae4`)

User-reported bug: Batch Integrate's lineout was wrong whenever the active
calibration used a non-zero `ImTransOpt` (Flip Y / Flip Z / Transpose).
Root cause: `spec_from_calibration_result()` (external `midas_calibrate_v2`
package) never copied `im_trans` onto the built `IntegrationSpec`, so
`spec.TransOpt` was always empty — and `BatchWorker` never applied any flip
of its own either. Net effect: raw, untransformed frames were integrated
against a geometry (BC/tilts) that had actually been fit on a *transformed*
image — a coordinate-frame mismatch, not a double-flip. Confirmed by
reproducing with `test_data/test_ps.txt` (`ImTransOpt 2`) +
`test_data/trr_s25ide/images/CeO2-89427.tif`: unflipped integration piled
signal at R≈1394–1677 px (detector-corner edge artifact); the corrected
path gives clean bands at R≈96–601 px (physically sensible, near beam
centre).

**First pass (rejected by user, kept only briefly): GUI pre-flips the pixel
array itself, `spec.TransOpt` stays empty.** Implemented once, worked, but
the user explicitly objected: MIDAS's own packages already accept
`ImTransOpt`/`TransOpt` and know how to apply it — the GUI's job is to *pass
the parameter*, not duplicate the transform logic in Python. Re-architected
before anything was committed.

**Final architecture — single rule for the whole app: the MIDAS backend
performs every pixel-array flip used for an actual calibration/integration
computation; `midas_gui` never does. The only exception is a viewer's
on-screen preview array, which may be flipped locally for display.**
Concretely:
- `helpers._build_spec()` / `_spec_from_result_ns()` (the shared spec
  builders behind `_build_spec`, `spec_from_geometry_file`, and every tab
  that integrates) now set `spec.TransOpt = list(result.im_trans)`.
  `midas_integrate_v2`'s `integrate_hard/subpixel/polygon(_with_variance)`
  and `integrate_with_corrections` all default `apply_trans_opt=True` and
  read `geom.trans_opt`/`spec.TransOpt` themselves — so every raw frame
  handed to them (unflipped, exactly as loaded) gets flipped internally,
  exactly once, by the backend.
- `calib.py`'s calibration path already had this right for the ONE pipeline
  that supports it: `midas_calibrate_v2.calibrate(image, im_trans=...)`
  flips `image`/`dark` internally itself. `CalibrationWorker` used to
  pre-flip in Python *and* clear `cfg["im_trans"]` before calling
  `run_pipeline` — defeating that native support entirely. Fixed to pass the
  raw image/dark and the original (uncleared) `im_trans` straight through;
  `calib.py`'s existing per-branch logic (native kwarg for plain
  `calibrate()`, manual pre-flip only for the pipelines that lack the
  parameter — see ROADMAP P3-1) was already correct and untouched.
- **Masks are the one thing still manually pre-flipped in Python, and this
  is a genuine backend gap, not a shortcut:** `*BinGeometry.from_spec(spec,
  mask=mask)` has no `apply_trans_opt` hook at all (unlike every
  `integrate_*` function) — the mask is evaluated directly against the
  untransformed pixel grid, so it must already be in the geometry's
  transformed/world orientation by the time it reaches `from_spec`/
  `build_geom`. See ROADMAP P3-2 for the upstream ask.
- **Viewer-only exception, deliberately preserved:** the Calibrate/Mask/Data
  Viewer tabs still call `_apply_im_trans()` on the array they hand to
  `ImageViewer.set_image()` for on-screen preview — this never touches a
  backend calibration/integration call and was explicitly called out by the
  user as fine to keep ("it is ok to transform image temporarily since we
  need to view the effect of the parameter").
- **Structural wrinkle found along the way:** a few places reuse the SAME
  array for both display and a backend computation (`hydra_geometry_card.py`
  `_midas_radial()`'s `image_provider()`, and `tab_mask.py`'s
  `MaskComputeWorker` azimuthal/learnable methods) — both had already
  flipped that shared array for display before this session. Rather than
  restructure the image-provider wiring, both now un-transform the array
  back to raw (`_apply_im_trans(img, tuple(reversed(im_trans)))` — each op
  is self-inverse, so reversing the code order inverts the composition)
  immediately before the backend call, then let `spec.TransOpt` reapply it
  once, correctly. `azimuthal_sigma_clip()` was found to have no
  `apply_trans_opt` parameter either (unlike `integrate_with_corrections`),
  so `MaskComputeWorker`'s azimuthal-clip branch is the one call site that
  deliberately keeps feeding it the already-transformed array — verified
  case-by-case per function signature via `inspect.signature()`, not assumed.
- **Self-inflicted bug caught during this session, fixed before landing:**
  an intermediate edit to `RefinementWorker` zeroed pixels with
  `img[mask.astype(bool)] = 0.0` using a mask that had been pre-flipped to
  transformed space while `img` was raw — silently masking the wrong
  pixels. Fixed by keeping two mask copies: the untouched raw one for the
  pointwise `img[mask]=0` zeroing (must match `img`'s current, raw, space)
  and a separately-flipped one (`mask_t`) for `build_geom`. Lesson: whenever
  a mask feeds two different consumers, check what coordinate space *each
  one* expects — they are not always the same.
- **`geometry_fields_from_file()`'s parsed `im_trans` was already correct**
  (confirmed `ImTransOpt` round-trips through paramstest/`.json`); the bug
  was entirely downstream of that parse, in spec-building and worker code.
- **Known follow-up left deliberately unfixed (flagged, not silently
  expanded into):** `PoleFigureWorker` (Texture tab) passes its mask to
  `build_geom` with zero transform handling — pre-existing, unrelated to
  what was asked, tracked in ROADMAP's Texture item.

**Verification:** re-ran the physical repro against the real `BatchWorker`
code path (not a standalone script) before and after; per-file-isolated
pytest across every touched test file (`test_im_trans`, `test_helpers`,
`test_hydra_batch_ui`, `test_hydra_geometry`, `test_hydra_calib_ui`,
`test_config`, `test_bridge_server`, `test_hydra_chirality`) — all green.
`test_project.py`/`test_smoke.py` crashes matched the identical pre-existing
pyqtgraph-teardown flake rate on unmodified `main` via `git stash` A/B
comparison — not a new regression.

## 2026-08-25 — MIDAS backend package upgrade: test in a cloned conda env first, keep numpy/torch/numba fixed, pin the newly-surfaced transitive deps explicitly (`a74b7d6`)

User request: the MIDAS PyPI packages had moved on substantially since this
repo's pins were set (e.g. midas-calibrate-v2 0.5.3→0.10.0); confirm the GUI
still works against the new releases, then upgrade the working `midas-gui`
env to match.

**Isolation choice: `conda create --clone`, not a fresh env from
`environment.yml`.** A fresh env would rebuild `pyqt`/`qt` from conda-forge
too, but cloning guarantees byte-identical Qt bindings to the working env —
the whole point of `environment.yml`'s conda-forge-not-pip PyQt5 pin is a
specific machine's xcb library set; a "faithful" rebuild from the recipe
still risks conda solving to slightly different builds. User explicitly
chose this over an in-place upgrade-with-rollback to keep the daily-driver
GUI usable during testing.

**Core stack (numpy/torch/numba/pvapy) held fixed on purpose, not because
the resolver forced it.** User explicitly chose "keep numpy/torch/numba
fixed" over "let the upgrade pull them forward too" before the real `pip`
resolve was even run. It happened to resolve cleanly at the old pins anyway
(none of the new backend releases' metadata requires numpy>=2), but the
decision was made independent of that outcome — a numpy 2.x move is a much
larger, separately-scoped change (see the numpy-1-vs-2/numba/torch
compatibility note already at the top of `environment.yml`).

**Static API diffing before any install.** Three parallel research agents
statically diffed the exact symbols `midas_gui` imports (old installed
source vs. new PyPI wheels/GitHub source) *before* any environment was
touched, to catch a rename/removal cheaply. All 16+ symbols checked were
unchanged or backward-compatible (new args optional) — this is why the
upgrade needed zero `midas_gui` source changes, unusual for a jump this
large (0.3.x→0.6.x, 0.5.x→0.10.x). Worth repeating this pattern before any
future backend bump of this size, rather than assuming semver discipline.

**New dependency `midas-params` and the zarr/numcodecs pins: not a
regression, a pre-existing gap made visible.** `zarr`/`numcodecs` were
*already* being pulled in transitively by the old midas-integrate/-peakfit/
-zipper versions (confirmed via `pip show` in the untouched baseline env
before any upgrade) — they were just never pinned in this repo's env files.
The upgrade didn't introduce the dependency, only the discipline of pinning
it. `midas-params` (a lightweight params-file registry/validator) is
genuinely new, first required (`>=0.9.0`) by several of the bumped
packages.

**Verification trusted per-file isolated pytest runs only, run in both the
old and new env for direct comparison** — not a combined `pytest tests/`
invocation, per the crash-flakiness already documented below (`653c832`
entry's era). A same-file, same-env repeat (5x) of `test_smoke.py` in both
envs showed the identical ~3-4/5 flake rate, which is what actually proved
the crash pattern is environment-level and not a new regression — a single
run in each env would not have been enough to distinguish "always crashes
now" from "sometimes crashed before too."

**Gotcha for next time:** `midas_gui` turned out to be pip-installed
editable in the `midas-gui` conda env (contrary to `environment.yml`'s own
comment saying it isn't) — pip's dependency resolver read that stale
`egg-info/requires.txt` and printed permanent "midas-gui requires X==old
but you have new" conflict warnings on every `pip install` afterward, purely
cosmetic but noisy. Fixed by `pip install --no-deps -e .` (regenerates the
egg-info without re-resolving dependencies, so it can't fight the
conda-forge PyQt5 pin the way a plain `-e .` would per that file's existing
warning).

## 2026-08-25 — Open Project's auto-plots reuse the exact live-fit code paths (no new "replay" logic for Calibrate); Batch's replay is new because no such path existed; per-file test isolation is the only trustworthy verification now (`653c832`)

User request: after Open Project populates Calibrate/Batch Integrate
fields, also show the recorded *results* (rings, radial profile, Eta-vs-R
cake, Waterfall/Stacked-profiles — panel-specific in Hydra) without
requiring a re-run, plus a prominent header project-name indicator.

**Calibrate/Hydra Calibrate: no new "replay" code — call the same methods a
live fit already calls.** `_draw_rings(result)`/`_run_integration(result)`
(single-detector) and `card.on_result(result)`/`_run_integration(n, result)`
(Hydra) were already pure functions of a duck-typed result object plus
whatever image the loader currently has loaded — neither cares whether the
result came from a just-finished `CalibrationWorker` or from
`project.calibration_namespace()` reconstructing a stored attempt's `result`
dict. So `_display_stored_result()`/`HydraCalibrationPage.
display_stored_result()` are thin wrappers that just call the existing
methods in the same order `_on_done`/`_on_panel_done` do, wrapped in
per-step `try/except` (a stored attempt's data file may no longer exist,
which must degrade to "rings only" rather than raising). Confirmed via
`_predict_ring_radii`/`_sanitize_result_dict`: the stored `result` dict
already carries every field these methods read (including the
underscore-prefixed `_calibrant_name` GUI-bolted-on extra), since
`_sanitize_result_dict` only drops torch-tensor fields, not underscore keys.

**Batch/Hydra Batch: genuinely new code, because no prior path replayed a
finished run's arrays into the plots.** `_on_frame`'s incremental
`reset()`+`add_profile()`-per-frame calls are the *only* existing way data
reaches `WaterfallViewer`/`StackedProfileViewer` — there was no "given a
complete profiles array, populate the whole plot" entry point. Also,
`project.read_attempt` never read integration attempts' `results/*` HDF5
datasets at all (only the JSON `metadata`) — a separate
`read_attempt_results()` was needed, and the Open Project dialog
(`app.py:_offer_populate_from_project`) now stashes its output onto
`meta["_results_arrays"]` before handing attempts to
`apply_project_integration`, rather than teaching `read_attempt` itself to
always eagerly read arrays it usually doesn't need (calibration attempts
have no `results` group at all; keeping the array read separate and
call-site-triggered avoids a wasted HDF5 read on every calibration-only
`Open Project`).

**Axis re-labelling (R/2θ/Q) reuses `_build_spec()`/`resolved_spec()`
rather than reading `Lsd`/`pxY`/`wavelength_A` off the raw result
directly** — those return the same v2-backend `spec` object
`set_axis_context()` already expects (attribute names differ:
`spec.Wavelength` vs. the stored result's `wavelength_A`), and calling
through the existing method keeps this consistent with what a live run's
`_run()`/`_start_panel_worker()` compute. Wrapped in `try/except` since a
synthetic/incomplete stored calibration could fail spec resolution —
axis mislabelling is acceptable, silently losing the whole plot is not.

**Header project-name label is a second, new corner widget — `TopRightCorner`
of the same `QTabWidget` — not a replacement for the existing status-bar
one.** Mirrors the 2026-08-25 header-Profile-combo decision above: added
*alongside* the quiet, always-present status-bar label rather than
replacing it, because the ask was specifically for something *prominent*
("different contrasting color... easily visible"), which is a different
job than a permanent status readout. Text is empty (not "Project: none")
when no project is open, since an empty high-contrast label reads as
"nothing to see here" more naturally than a colored "none" would.

**Verification finding, not a fix: the pre-existing pyqtgraph
interpreter-teardown segfault reproduces on unmodified `main`, not just
after this session's edits.** While chasing an apparent regression (the
exact `release.sh`-style invocation — full `tests/` dir minus the 2 named
Hydra UI files — segfaulted/bus-errored with this session's changes
applied), `git stash`-ing back to a clean `main` and re-running 3× showed
the *same* crash on *unmodified* code, and `tests/test_hydra_ui.py` alone
(not one of the two files `release.sh` isolates) also crashes standalone on
unmodified `main`. So the crash is environment-level flakiness (this
machine's current PyQt5/pyqtgraph/Python 3.12 combination), not a
regression from this session's widget additions — but it also means the
STATE.md claim "`release.sh` ... is unaffected" no longer holds reliably,
and a combined `pytest tests/` run can no longer be trusted to surface a
real failure vs. this crash. Every test file touched this session was
therefore verified by running it alone in its own process (already this
repo's established mitigation for exactly this scenario), and that
per-file-isolation requirement is now recorded as the verification method
going forward, not just for the two already-named heavy files. Not
attempted to fix (per the standing "`gc.collect()` makes it worse" note) —
out of scope for a feature session.

## 2026-08-25 — Profile switch refreshes option *lists*, never seeded default *values*; header combo added alongside (not instead of) the Preferences one

Two related bugs reported together: switching profiles was buried in
Settings ▸ Preferences, and several option-list widgets (Data Viewer's Live
PV device dropdown, Calibrate's Calibrant dropdown on every panel, the
pixel-size-preset and K-edge-foil popup menus) silently kept showing the
*previous* profile's choices until the app was restarted.

**Header Profile combo, not a replacement for the Preferences one.** Added
via `QTabWidget.setCornerWidget` on the main tab bar rather than a toolbar,
so it reads as part of the window chrome without a new toolbar row. Both
places call through the same `MainWindow.on_profile_changed()` now (the
Preferences dialog previously called `apply_tab_visibility()` directly),
so there's exactly one code path that reacts to "the active profile just
changed," regardless of which UI triggered it.

**Hydra mode gated to the 1-ID-E profile.** Only that beamline has the
4-panel GE detector; showing the Hydra ribbon option elsewhere just invited
confusion. `set_hydra_available()`/`set_hydra_enabled()` on each of the
three split tabs' mode ribbons falls back to Single detector automatically
if Hydra was the active mode when it's hidden — never leaves the ribbon in
a state with no visible option selected.

**Option lists refresh live; seeded default *values* deliberately do not.**
`refresh_devices()`/`refresh_calibrants()` (new, called from
`on_profile_changed()`) repopulate combo boxes in place, and the
pixel-size/K-edge popup menus now rebuild their entries from live
`constants.*` module attributes on every `QMenu.aboutToShow` instead of
freezing a list at construction time. But numeric/path defaults that a
profile seeds into a field — wavelength, pixel size, Lsd, beam-centre,
default file paths — are **not** re-pushed into already-built fields on a
switch; they only apply to fields built *after* the switch (or after a
restart). **Why:** a field's value at any moment may be the user's own
in-progress edit, not the seeded default — there is no way to tell the two
apart from the widget alone, so re-seeding on every switch risks silently
clobbering real work. An option *list* has no such ambiguity (it's a
finite, profile-owned vocabulary, and the widget's current selection is
preserved by `refresh_combo_items()` when it still exists in the new list),
which is why lists refresh live but values don't.

## 2026-08-24 — Open Project's "populate the GUI" reuses GUI-State's widget-key vocabulary instead of a bespoke schema; Batch Integrate gets a real calibration, not just display fields (`e693316`)

User complaint: opening a project (`e8dea6b`) only marked it active for
*future* provenance logging — every tab's fields were left exactly as they
were, so a saved project's data paths/geometry/settings could never actually
be picked back up. Fixed by adding a "populate the GUI from this project"
path, triggered from `_open_project_dialog` right after a successful open.
A few things worth recording:

**The attempt→GUI mapping reuses `_state_widgets()`/`state_widgets()`'s
existing widget-key vocabulary and `apply_dict_to_widgets`, rather than
writing bespoke per-field setter calls.** Investigating `CalibrationTab`'s
and `HydraCalibrationPage`/`HydraCalibPanelCard`'s state dictionaries showed
they're three *non-overlapping* subsets of the same key names (e.g. `wl`,
`cal`, `seed_bcy`, `ref_lsd` mean the same thing whether they come from the
single-detector tab's own `_state_widgets()`, the Hydra page's shared
"recipe" fields, or one Hydra panel card's seed fields) — because the
single-detector tab's dict is literally the union of the other two (no
separate cards there, just one detector). That meant one pure function,
`project.calib_attempt_gui_fields(meta)`, could produce a single field dict
handed to `apply_dict_to_widgets()` in all three places; each call site
naturally ignores the keys it doesn't define. Same reasoning applies to
`integrate_attempt_gui_fields`. This avoided a second, independent
attempt-schema-to-widget mapping (the risk being that if the widget schema
changes later, only one place — `_state_widgets()`/`state_widgets()` — would
need touching, not two).

**Batch Integrate's populate step bypasses `set_state()` for the actual
calibration and calls `set_calibration()`/`set_panel_calibration()`
directly.** `BatchTab.set_state()` deliberately never restores
`self._calib_result` (see its docstring: "long-running pipelines ... are
not re-run"), because plain GUI State has no live result object to restore
— a real Tab-2 run is the only source. A project attempt is different: it
already recorded the *exact* calibration values used
(`calibration_snapshot`), so there's no reason to leave Batch Integrate
non-functional pending a Tab-2 re-run. `project.calibration_namespace()`
turns that stored dict into a duck-typed object (same shape as
`helpers.result_ns_from_geometry_file()`'s output, which already proved
sufficient for `resolve_calibration_fields`/`_build_spec`) and hands it to
the existing `set_calibration()` "From Tab 2"-radio path — the button's
static label text becomes slightly inaccurate (the values didn't come from
a live Tab-2 run this session) but this was accepted rather than adding a
third calibration-source radio button/UI, since the values displayed and
used are exactly correct either way.

**Combined into one `set_state()` call per tab, not one per selected
panel.** Calling `set_state()` per Hydra panel selection would flip the
mode ribbon and touch `anchor_path`/shared "recipe" fields redundantly on
every call; instead `apply_project_calibration`/`apply_project_integration`
loop over the selected attempts building one combined `cards`/`fields`
dict and call `set_state()` exactly once, so a project with attempts for
all 4 Hydra panels lands in a single restore.

**Explicitly out of scope for this pass**: restoring dark/bright/
background/mask sources that were embedded directly in a project record
(no owning file — e.g. a mask hand-drawn in Mask Builder). Checked first
whether plain GUI-State save/load already supported this (it doesn't —
`FieldSelector`/`MaskSelector.set_state()` only ever restore file-path
-backed sources) — so this is a pre-existing gap, not a regression, and
left as a known follow-up rather than growing this change into a
selector-widget rework.

## 2026-08-24 — Hydra seed-mode linking is signal-fanout across per-panel checkboxes, not a moved-to-toolbar shared widget; cake data reuses an already-computed, previously-discarded array (`162fef1`)

Two independent Calibrate-tab changes, requested together:

**Seed-mode linking kept the checkboxes on each per-panel card and
synced their *state* via signals, rather than hoisting a single shared
checkbox onto the page-level toolbar** (the toolbar — where
`_show_rings_check`/`_corrected_check` already live — was the obvious
alternative). Rejected because several existing per-panel methods
(`_on_bc_picked`, `_on_ring_fit_bc`, `_load_calib_file`, `seed_from_result`,
`seed_from_geometry`) already call `self._manual_seed_check.setChecked(True)`
as a side effect scoped to *that* card, with tooltips/log lines phrased
per-panel ("sets this panel's seed…"). Moving the checkbox off the card
would have meant rewriting all of those call sites to reach through to a
page-level widget instead, for a purely cosmetic win. Instead,
`HydraCalibrationPage._sync_seed_checkbox(attr, src_panel, checked)` is
wired to each card's `toggled` signal at construction and mirrors any
state change (manual click or one of those programmatic call sites) onto
the other three cards' checkboxes directly (`blockSignals` during the
mirror, so it's a flat fan-out rather than a signal cascade through all 4
cards). Net effect matches the request exactly — "a selection on any panel
updates all of them" — while every existing per-panel code path continues
to work unmodified.

**The Eta-vs-R cake array was already being computed by the backend and
silently discarded at the GUI boundary** — `integrate_frame()` has
supported `return_cake=True` since before this session (used by the Batch
tab's `2d_csv` export format and by `texture.cake_to_pole_figure`), but
`IntegrationWorker` — the one class shared by both the single-detector and
Hydra Calibrate tabs' post-fit auto-integration — called it without that
flag and its `finished` dict only carried the collapsed 1-D profile. So
this feature needed no new integration math: only threading
`return_cake=True` through one call site, deriving `eta_axis_deg` the same
way `build_integration_context` already does for the Batch tab
(`spec.EtaMin + spec.EtaBinSize * (arange(n_eta) + 0.5)`), and adding the
two fields to the emitted dict.

**New `CakeViewer` widget rather than reusing/subclassing `ImageViewer`.**
`ImageViewer`'s mouse-hover/status-bar/pan-zoom-limits code assumes the
displayed array's row/column indices *are* the physical coordinates
(detector pixels, always starting at (0,0)) — see its `_mouse()` doing
`ix, iy = int(x), int(y)` directly against the view coordinates. A cake's
two axes are physical R (px) and η (°) bin centres, which don't start at
the origin and aren't 1-unit-per-pixel. Retrofitting `ImageViewer` to
support an arbitrary axis extent would have meant conditionally branching
most of its methods; a new, smaller, purpose-built widget
(`ImageItem.setRect()` to position/scale the image to the real R/η extent,
its own percentile-based level logic, a hover readout that inverts the
R/η→array-index mapping) was more contained and left `ImageViewer` alone.
It does reuse `_resolve_cmap`/`COLORMAPS`/`_DEFAULT_CMAP` and the same
Log/cmap/vmin%/vmax% toolbar convention, so it looks and behaves like the
rest of the app's 2-D viewers.

**Verification went beyond the stubbed UI tests deliberately.** Both
Hydra UI test files stub `IntegrationWorker` entirely (documented reason:
the synthetic fixture doesn't converge a real fit), so the stub's
`finished` dict needed `cake_2d`/`eta_axis_deg` added by hand for the new
assertions to exercise the GUI-side wiring at all — that only proves the
*plumbing*, not that `integrate_frame(return_cake=True)` actually produces
a sane array end-to-end. A short manual script (not committed — ad hoc,
same rationale as this repo's ad hoc PDF-rebuild pipeline) ran the real,
non-stubbed `IntegrationWorker.run()` against a synthetic image through
the real `midas_integrate_v2` backend and confirmed a correctly-shaped
`(n_eta, n_r)` cake, before trusting the stubbed-test assertions as
sufficient regression coverage.

## 2026-08-24 — File ▸ Project: FAIR provenance is separate from GUI State, always best-effort, and links Batch → Calibrate attempts (`e8dea6b`)

Added an opt-in, long-lived HDF5 "project" file (`midas_gui/project.py`)
that records what actually *happened* during Calibrate/Batch Integrate
runs, as opposed to GUI State's existing job of snapshotting widget
*configuration* for session resume. A few things worth recording:

**Deliberately a separate concept from GUI State**, not an extension of
it. GUI State is a point-in-time UI snapshot meant to be overwritten
(Ctrl+S semantics). A provenance record must never be overwritten — it's
supposed to accumulate a full history of every attempt across many
separate GUI launches over an experiment's lifetime. Reusing the GUI
State sidecar format/file would have made "every save silently loses the
previous attempt" the default behavior, which is the opposite of what
FAIR provenance needs. Hence its own file format (HDF5, self-marked via a
`PROJECT_MARKER` attr + schema version so `open_project` can reject a
random `.h5` file), its own menu section, and its own append-only
`attempt_NNNN` group-per-run structure.

**Raw scan data is referenced by path + checksum, never duplicated.**
A Hydra Batch Integrate run can touch a multi-thousand-frame HDF5 stack;
embedding it would make the project file balloon and duplicate data users
already have. `sha256_file` hashes fully for files ≤200 MB, and for larger
files takes a head+tail fingerprint instead (`sha256_partial`) — good
enough to detect "this isn't the file the record claims" without stalling
every run on hashing gigabytes. Masks/dark/bright/background frames *are*
embedded directly (small, and — critically — a mask drawn by hand in Mask
Builder has no file of its own to reference at all).

**Logging is always best-effort and must never affect the run's own
outcome.** Each tab/page's `_log_to_project` is called from the existing
`_on_done`/panel-done handler, wrapped in a bare `try/except` that logs
any failure (e.g. disk full, permissions) to the tab's own Log panel and
otherwise does nothing — a provenance-write problem must never make a
successful calibration or integration look like it failed, nor block the
result from displaying. `project.py` itself has no Qt dependency (pure
`h5py`/`json`/`hashlib`), so `tests/test_project.py` covers most of it
without a QApplication.

**Batch → Calibrate linking uses a field bolted onto the result object,
not a project-side query.** When a calibration attempt is logged,
`append_calibration_attempt` returns a path-like ref string
(`/ge2/calib/attempt_0003`) that the calling page stashes as
`result._project_attempt_ref`. When that same result later gets used for
a Batch Integrate run, `_log_to_project` reads that attribute back off and
passes it as `calib_attempt_ref` so the integration record can point at
its calibration's exact record — cheaper and simpler than re-deriving
"which calibration attempt matches these exact cfg values" by scanning
the HDF5 file, and correct as long as the same in-memory GUI session did
both runs (a manual "From file" calibration source has no such ref, and
correctly logs `calib_attempt_ref=None`).

**Reused as-is**: the `.numpy`-attribute duck-type check for dropping
torch-tensor fields from a result (already used by
`hydra_calib_widgets.py`'s `_save_json`) — `_sanitize_result_dict` uses
the identical heuristic so large tensor fields (`residual_corr_map`, etc.)
are never JSON-serialized into the metadata blob, only their existence
implied by the sibling `..._embedded` boolean flags.

## 2026-08-24 — Batch Integrate Hydra split: hand-off direction, mask scope (diverges from Calibrate), lazy page construction, and a pytest-isolation fix for `release.sh` (uncommitted at write time)

Added Hydra support to Batch Integrate (Tab 3), mirroring the Calibrate
tab's Single-detector/Hydra ribbon split: each of the 4 GE panels is
integrated with its own independently fitted geometry, via a new
`HydraBatchPanelCard` (per-panel calibration source + values + progress)
and `HydraBatchPage` (shared Integration/Corrections/Monitor/Output cards,
Sequential/Parallel `BatchWorker`-per-panel orchestration copied from
`HydraCalibrationPage`'s pattern). Four decisions worth recording:

**Hand-off direction: automatic push, not a manual pull button.** Confirmed
with the user before implementing. The Data Viewer↔Calibrate hand-off uses
a manual "← Data Viewer" pull button because the Data Viewer has no
"finished" event — it's just whatever's currently loaded. Calibrate→Batch
is different: a Hydra panel's fit genuinely *finishes* (`_on_panel_done`),
exactly like the single-detector tab's existing `calibrationDone`→
`set_calibration` auto-wiring. So `HydraCalibrationPage` gained
`panelCalibrationDone(n, result)`, forwarded through `CalibrationTab
.hydraPanelCalibrationDone`, wired in `app.py` to `BatchTab
.set_hydra_panel_calibration` — a panel's Batch Integrate calibration
source populates itself the moment that panel's Calibrate-tab fit
completes, with no user action needed. Each panel keeps an independent
manual "From file" fallback too, mirroring the single-detector tab's own
radio-button pattern.

**Masks ARE wired per panel for Hydra Integrate — a deliberate departure
from Hydra Calibrate's `cfg["mask"]=None` scope-cut**, confirmed explicitly
with the user rather than assumed. Rationale: the single-detector Batch tab
already treats masks as a first-class input (`DataLoaderPanel`'s
`MaskSelector`), and bad-pixel/beamstop masking matters more for
integration-profile quality than for ring-centroid calibration fitting.
`HydraLoaderPanel` gained a `mode="nav"|"stream"` constructor param
(default `"nav"` preserves existing Viewer/Calib behavior unchanged);
`"stream"` mode adds a shared frame-range+stride row (frames are
synchronized across panels — one range for all 4, per the class's own
existing docstring) and one independent `MaskSelector` per panel (each with
its own manual "Add file…" picker — **no sibling auto-discovery**, also
confirmed with the user: mask files are physically panel-specific and may
not follow a shared ge{n} naming convention the way data files do).

**Drift correction and live MONITOR (folder-watch) mode are deferred for
Hydra v1** — both exist on the single-detector Batch tab; the user
confirmed deferring both rather than scoping either in now. `BatchWorker`
needed no `capture_stdout`-style flag for safe Parallel-mode concurrency
(unlike `CalibrationWorker`) — it already logs via the `log_line` Qt signal
only and never redirects the process-global `sys.stdout`.

**`HydraBatchPage` is built lazily**, unlike `HydraCalibrationPage`/
`HydraViewerPage` (both built eagerly in their tab's `__init__`). It owns 8
pyqtgraph widgets (4 `WaterfallViewer` + 4 `StackedProfileViewer` — more
than Calib's 6), and most sessions never open Hydra Batch Integrate at all.
`BatchTab._ensure_hydra_page()` builds it on first ribbon switch to
"hydra", on `set_hydra_panel_calibration` (the auto hand-off must land
somewhere even if the user hasn't opened the tab yet), or on `set_state`
restore if the saved session actually has Hydra page state.

**A concrete instance of the pyqtgraph interpreter-teardown crash risk this
change ran into, and how it was fixed**: DECISIONS.md's existing entry on
this risk anticipated "if the full-suite crash rate becomes a real
nuisance, run Hydra UI tests in a separate pytest process" — this session
hit exactly that. A/B testing (5 runs each) showed the *unmodified*
baseline full suite (`pytest tests/`, one process) already segfaults
~40% of the time from this pre-existing, documented pyqtgraph
`ViewBox`/GC global-state bug (confirmed via crash tracebacks landing in
unrelated tests, e.g. `test_mask`'s own `ViewBox` construction — not a
correctness bug in any specific page). Adding this session's new
`tests/test_hydra_batch_ui.py` (which — like `test_hydra_calib_ui.py`
before it — builds `HydraBatchPage` instances, each with several more
pyqtgraph widgets) pushed that same full-suite run to 5/5 segfaults.
Running each Hydra-UI-heavy test file in its own fresh `pytest` invocation
(a fresh interpreter → fresh pyqtgraph global state) eliminated the
segfault for those files entirely (0/4 in isolation across repeated runs)
without touching the files' content or reducing test coverage — the crash
is a property of *cumulative* pyqtgraph widget churn within one process,
not of any single test. `release.sh`'s test step was changed from one
`pytest tests/` call to three (main suite minus
`test_hydra_calib_ui.py`/`test_hydra_batch_ui.py`, then each of those two
files run separately), preserving identical pass/fail gating semantics. A
bare `pytest tests/` run by hand still carries the underlying (pre-existing,
now slightly more frequent) risk — this only fixes the release-gating path.

**Reused as-is / promoted**: `helpers._build_spec`/`spec_from_geometry_file`
(a Hydra panel's fitted result is exactly the `AutoCalibrationResult`-shaped
object those already expect — no new spec-building code needed);
`widgets.MaskSelector` (confirmed zero-coupling to `DataLoaderPanel`,
directly reusable standalone per panel); a new
`helpers.resolve_calibration_fields`/`render_calib_value_grid` pair,
hoisted out of `BatchTab._calib_fields_in_use`/`_refresh_calib_values` so
`HydraBatchPanelCard` doesn't duplicate that logic — the single-detector
tab's own methods are now thin wrappers over the same two functions.

## 2026-08-24 — Calibrate tab Hydra split: field-sharing boundary, and CalibrationWorker's stdout capture made optional for Parallel mode

Split the Calibrate tab into Single-detector/Hydra modes (mirroring the
Data Viewer tab's split), so each of the 1-ID-E Hydra rig's 4 GE panels can
be fit from one calibrant dataset. Two decisions worth recording:

**Field-sharing boundary** (confirmed with the user before implementing):
wavelength, pixel size, calibrant, pipeline choice, and refine-parameter
selection (including which distortion coefficients) are shared across all
4 panels — one "recipe" — since it's the same beam and the same choice of
what to refine. Transforms (Flip Y/Flip Z/Transpose) and the initial seed
(BC/Lsd/tx/ty/tz) are independent per panel, since each GE module is a
physically separate detector with its own mounting orientation and beam
centre. This mirrors the Data Viewer Hydra page's existing λ/max2θ/pixel
-shared-but-BC/Lsd/tilt-independent split (`DetectorGeometryCard.get_shared_fields`/
`apply_shared_fields`), just drawn for a fitting UI instead of a viewing
one. No "Multi-panel detector" (tiled sub-panel rigid-shift refinement)
group in Hydra mode — that feature is about tiles *inside* one monolithic
detector's readout, not 4 separate physical detectors. No "Composite"
option in the image-panel toolbar — calibration is inherently per-panel;
the windmill composite stays a Data-Viewer-only visualization.

**`CalibrationWorker.run()`'s stdout capture had to become optional.**
Found during design (before any code was run): the worker redirects the
*process-global* `sys.stdout`/`sys.stderr` to a `_LogStream` for the
duration of the pipeline call, restoring it afterward. That's safe with
today's only caller (one worker at a time, single-detector tab). But the
user explicitly asked for a **Parallel** run mode — several
`CalibrationWorker`s racing on the same global `sys.stdout` would
misattribute or lose log lines, and there's no clean way to make Python's
process-wide `sys.stdout` swap thread-local (the underlying pipeline just
calls plain `print()`). Rather than silently serializing "Parallel" mode
(defeating its purpose) or shipping the race, `CalibrationWorker` gained a
`capture_stdout: bool = True` constructor flag. **Sequential** mode passes
`True` (one worker active at a time, unchanged/safe). **Parallel** mode
passes `False`: each worker's fine-grained `print()` output goes to the
real console instead of the Log tab, but the coarser `log_line`/`finished`/
`failed` Qt signals — which don't touch global state — are untouched, so
the Log tab still shows each panel's start/finish/error lines. This
trade-off is stated in the Hydra page's "Run mode" combo tooltip and in
`CalibrationWorker`'s own docstring/comment, not just here.

**Verification**: offscreen tests (`tests/test_hydra_calib_ui.py`) stub out
`CalibrationWorker`/`IntegrationWorker` entirely rather than running a real
fit — the synthetic Hydra fixture's `ps_ge{1..4}.txt` files share one
nominal BC/Lsd and don't carry enough calibrant rings within the default
pipeline's window for a real `one_shot` fit to converge (`RuntimeError:
Only 2 simulated rings under 28.0°`), verified by calling
`midas_gui.calib.run_pipeline` directly with the same image/cfg outside any
GUI code — a pre-existing fixture/data characteristic (it was built for
geometry/composite display tests, not fit convergence), not a bug in this
page. The tests instead verify the GUI's own sequencing/routing: Sequential
produces independent per-panel results, Parallel starts all 4 workers
up-front, and Results/Ring-Residuals correctly switch with the active
panel. Also folded into just 2 test functions sharing one
`HydraCalibrationPage` each (not one page per test) — a first pass with one
page per test function reproduced the pyqtgraph-teardown segfault described
in the entry below, same category, made more likely here because this page
alone builds 6 pyqtgraph widgets (1 `PickableImageViewer` + 4
`ResidualBarChart` + 1 `HydraProfileViewer`).

**A mid-edit bug worth flagging for future sessions**: while hoisting
`CalibrationTab._paramstest_pairs` into `helpers.paramstest_pairs`, an
`Edit` whose `old_string` ended at `p.write(str(path))` (believed to be
`write_standalone_paramstest`'s last line, from an earlier truncated read)
actually split that function in half — its real tail (writing
`ImTransOpt` lines and `return p`) got stranded as dead code after the
newly-inserted function's `return pairs`. This silently broke `ImTransOpt`
round-tripping (`write_standalone_paramstest` stopped writing those lines
and stopped returning `p`) with no import error or syntax error — caught
only by running the full test suite (`tests/test_im_trans.py`), not by
`ast.parse` or an offscreen smoke import. Lesson: when relocating a
function by matching on its last line, re-read the surrounding lines after
the edit (or match through to the next `def`/blank-line boundary) rather
than trusting an earlier read's apparent end-of-function.

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
