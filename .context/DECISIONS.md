# DECISIONS — append-only, newest first

Each entry: what was decided and *why* (the reasoning that would be expensive
to reconstruct later). Never rewrite history; add a new entry to supersede.

_Condensed 2026-08-31 (~2150 → ~500 lines): cut verification transcripts,
file-by-file implementation narrative, and duplicated/superseded content;
kept the durable "why" behind each decision. See git history before this
date for the full uncondensed entries if ever needed._

## 2026-09-02 — PR #7's `<froot>_<NNNNNN>` output naming kept, but made collision-safe rather than reverted

junspark's PR #7 changed Batch Integrate's per-frame profile filenames from
the frame id verbatim (`frame_000.csv`) to a zero-padded
`<froot>_<NNNNNN><tag>` (`frame_000000.csv`), matching mpe_wf_saxs_waxs's own
output convention. Nothing in the PR's 20 commit messages mentions this, and
it silently broke the two tests that asserted the old names.

Two ways to resolve it: revert the convention, or keep it and fix what it
broke. **Kept it** — the whole point is interoperating with mpe_wf_saxs_waxs's
tooling, and a padded, sortable name is genuinely better than a raw stem; the
tests were simply stale (confirmed: with only the expected filenames updated,
22/22 pass, so the writer itself was correct).

But the padding normalisation introduced a real defect worth recording,
because it is the kind that produces no error: `froot_and_frame_num` maps
`scan_1`, `scan_01` and `scan_001` all to frame 1, so all three wrote to
`scan_000001.csv` — three frames in, one file out, the first two silently
overwritten. Reachable through the Browse dialog's "Multiple files" /
"Files sharing a name stem" modes when a pick spans differently-padded files.

Fixed by separating *parsing* from *naming*: `froot_and_frame_num` stays a
pure (and deliberately non-injective) parser, and a new
`frame_output_base(out_dir, fid, fallback_idx, used)` owns filename
allocation, carrying a per-run `used` set. On a clash it falls back to the raw
frame id — unique by construction, since it is a file stem or a stem plus a
chunk suffix — and only then to an index suffix. Chose de-duplication over
"detect and abort" because a mixed-padding folder is a legitimate input, not
user error, and over "always use the raw id" because that throws away the
sortable-name benefit for every well-formed run. All three call sites
(`BatchWorker`, `FolderMonitorWorker`, `write_all_profiles`) go through it.

Also taught the parser the `_c<NN>` chunk suffix the PR's own
`_HDF5StackGlobSource` mints: those ids matched no numeric run, fell to the
fallback branch, and produced names like `run_009243.vrx_c00_000000.csv` that
no longer sort by frame. Split off before the numeric parse and re-attached
after, so a stem with its own frame number keeps it and a stem without one
uses the chunk index as the frame number.

**Open, not decided here:** junspark has not been told about the convention
change. It breaks any user script globbing Batch Integrate output, and
deserves either a note on the PR or a line in the release notes.

## 2026-09-02 — Reviewing an incoming PR: baseline first, and cover the new code rather than trusting a green run

PR #7 was 20 commits / +3492 lines / 6 new modules with **zero test changes**
— the largest external contribution this repo has taken. Two process
decisions came out of reviewing it, both worth repeating.

**Baseline the suite on `main` before reading the PR.** This repo has four
permanently-failing test files (see STATE.md for the current counts). Run
per-file on `main` first, capture every summary line, then run the identical
sweep on the PR branch and diff. Without that, the PR's two genuine
regressions are indistinguishable from the standing noise — and, worse, a
reviewer who knows "those four always fail" is primed to wave through a
*fifth* failing file. Diff per-file results, never a combined `pytest tests/`
run (see the 2026-08-30 entry below for why a combined run is untrustworthy
here regardless).

**A green suite is not coverage.** The only two failures PR #7 tripped were in
*old* tests, and they were tripped by an intentional rename — pure luck, not
verification. Nothing in the suite touched the strain-cake maths, the zarr
schema, the provenance stamping or the tilt-seed gating. So 108 tests were
added before merging, prioritising the failures that are *silent* over the
ones that crash:
- `test_zarr_cake.py` pins the REtaMap channel order and (5, nR, nEta)
  orientation. This layout is not ours — GSAS-II's `G2pwd_MIDAS.py` and
  mpe_wf_saxs_waxs read it — so a transposition yields a file that opens
  fine and is wrong.
- `test_strain_cake.py::test_subpixel_refinement_beats_bin_quantization`
  fails if `ring_azimuth_residual` ever regresses to a plain argmax, which
  produces a smooth, plausible-looking map carrying no azimuthal
  information at all.
- `test_set_raw_frame.py` pins that the centralized transform returns
  exactly what `_apply_im_trans` would, so the overlay-vs-rings
  misalignment the refactor fixed cannot silently return.
- `test_helpers.py` pins the documented invariant that `tilted_ring_xy`
  reduces to the plain circle at zero tilt, and that ring and spoke agree
  where they meet.
Deliberately **not** covered, and left open in ROADMAP.md: `job_queue.py`,
`peak_fit_panel.py`, `batch_cli.py`. They are subprocess/GUI-shell code whose
useful tests need either a live `screen` session or a full widget harness —
disproportionate to this session's scope, and honest to record as a gap
rather than paper over with import-only smoke tests.

Merged as a fast-forward (`092fbba`, `46e0fec` on top of the PR's own 20),
since the branch was already based on current `main`.

## 2026-08-31 (`fd7f67a`) — Hydra Overall Eta-R Cake rotates each panel by its own `tx` before summing

Fixes the Overall cake piling every panel onto the same wedge instead of
covering -180°..180°. Supersedes the 2026-08-26 "Hydra Overall Cake
verified, no code change" entry below — that verification only tested the
default `tx=0` case (all panels share one η axis, no rotation needed by
construction); it didn't cover a panel calibrated with its own true,
distinct installation `tx`, which needed a rotation the un-updated compose
function never applied. `_resample_rows_to_eta_grid` (new) handles the
general case; existing R-axis (2θ) resampling logic unchanged.

## 2026-08-30 — `pytest-forked` isolates the known teardown-crash test files; root cause is `fork()`-after-multithreading, not just pyqtgraph

Added `pytest-forked` + `pytestmark = pytest.mark.forked` to the four
known interpreter-teardown-crash-prone files. **Confirmed working** for a
single isolated file run: a pyqtgraph-teardown segfault that used to kill
the whole pytest process now surfaces as a clean `FAILED ... CRASHED with
signal N` instead. **Does NOT fix a combined `pytest tests/` run** — `os.fork()`
of an already-multithreaded process (torch/numba/Qt/HDF5 thread pools
accumulated earlier in the run) is itself unsafe and crashes regardless of
which test forked, including files with zero Qt content. Trust per-file
isolated runs only; a real fix would need spawn-based workers (e.g.
`pytest-xdist`), not attempted.

## 2026-08-29 — Export for GSAS-II: native MIDAS zarr, not a GUI-specific format; v1 scoped to single-detector/R-uniform/embedded-mask

`gsas_export.py` calls `midas_integrate_v2.io.zarr_gsas.write_gsas_zarr_zip`
directly rather than inventing a GUI format — verified against GSAS-II's
own `G2pwd_MIDAS.py` import contract (required groups, `REtaMap` index
order, its >20-unmasked-point-per-azimuth filter) so the output needs no
adapter. Picks ONE attempt from history (dropdown) rather than "latest" —
GSAS-II imports one dataset at a time and silently picking latest would be
a hidden assumption. A `.provenance.json` sidecar carries full metadata,
kept *next to* the zip so the zip's internal structure stays exactly what
GSAS-II expects.

v1 scope, each enforced as a named `ValueError`: single-detector only
(Hydra composite = possible fast-follow); R-uniform binning only (a
Q-rebinned attempt's `r_axis_px` isn't a plain function of geometry, so
`REtaMap`'s bin area can't be reconstructed); embedded mask only (a
file-backed mask reference isn't guaranteed to still resolve at export
time). Works with either the legacy 2-D profile or the multi-azimuth cake
(below) — forces η bin to 360° for the 2-D case since that run's η bin was
only ever a collapse-weighting knob, not a real azimuthal-sector count.

## 2026-08-29 — Batch Integrate "Multi-azimuth output (cake)": opt-in, off by default, repurposes the existing η bin field

`midas_integrate_v2` always computes a full (η,R) cake internally — Batch
was collapsing it to one full-circle profile before anything downstream
ever saw it. The checkbox keeps every azimuthal sector as its own output
profile (`(n_frames, n_eta, n_r)`), needed for per-azimuth GSAS-II/texture
work. Deliberately reuses the existing η bin/range fields rather than
adding a parallel control — they already meant "how finely to slice
azimuth," so overloading their meaning when the checkbox is on is more
honest than a second control that could drift out of sync. Not combinable
with Q-uniform bins (`rebin_R_to_Q` only handles 1-D profiles) — blocked at
both UI and worker level. HDF5 output skipped in this mode
(`write_h5` expects one profile per frame); use text formats or GSAS-II
export instead.

## 2026-08-29 — Project `.h5` schema redesign (`21faaf8`): `gui_workspace` + `analysis`, clean cutover, global mask history, one combined Open Project dialog

Three explicit user decisions (via `AskUserQuestion`, each a real
trade-off):
1. **Mask attempts are a global history** (`/analysis/mask/attempt_NNNN`),
   not per-panel like calibrate/integrate — `/analysis/mask` only stores
   mask-*creation* attempts; calibrate/integrate records separately embed
   whichever mask they actually used, so every analysis attempt stays
   self-sufficient regardless.
2. **Clean cutover, no backward compatibility** with schema v1/2 — chosen
   over a dual-path reader (used at the 1→2 bump) because the old one-JSON-
   blob layout has no natural per-tab boundary to migrate into
   automatically. Opening a pre-3 file shows a clear version warning
   instead of a silent partial restore.
3. **One combined dialog** (file-tree + live checkbox-tree preview) rather
   than `QFileDialog` + a second custom dialog, reusing `BrowseFilesDialog`'s
   navigation building blocks.

Mask Builder's "Log to Project" is an explicit button (not piggybacked on
Save) since — unlike Calibrate/Batch's single "run finished" moment — a
mask can be finalized via Compute, Load, or hand-drawn shapes in any
combination; the button puts the user in control of when it's "final."

## 2026-08-27 — Browse… popup: 4-mode file picker, `list[str]` end-to-end (not glob-collapsed), Hydra registry wiring, plus later polish

`dialogs.BrowseFilesDialog`: one popup, up to 4 mutually-exclusive modes
(Single file / Multiple files / Full folder / Files sharing a name stem) —
which modes a field offers is passed in by the caller since different
pipelines can't take every shape (e.g. Hydra's main Data field has one
anchor file, so `modes=("file",)`). **A confirmed Multiple-files/Files-
sharing-a-stem pick is carried through as a plain `list[str]` end to end,
deliberately not collapsed to a glob** — a stem match or arbitrary
multi-select may not share one glob pattern (non-contiguous numbering,
mixed extensions), and collapsing would silently include/exclude files the
user didn't pick. `helpers.source_kind`/`_collect_frame_paths` special-case
`isinstance(raw, list)` as a "folder"-like kind, returned as-is. Hydra's
loader joined the same `DataSourceRegistry` its single-detector sibling
used, gated to Hydra-labeled fields only so anchor paths aren't offered
across modes that can't consume them. Follow-up polish: field text shows a
folder/file path instead of an "N files selected" count
(`helpers.display_text_for_paths`); default browse dir is the repo root,
not `$HOME`; file-tree name column doubled in width (Qt's stock 100px
default).

## 2026-08-27 — Feed Calibrate's Multi-panel results to downstream integration

Root cause (3 gaps, all GUI-side workarounds for upstream package gaps —
see `.context/ROADMAP.md` P3-3): `spec_from_calibration_result()` has no
panel awareness at all; `geometry_fields_from_file()` never parsed panel
keys out of a paramstest/calibration.json; `_save_paramstest()` wrote the
per-panel shifts file but never the panel *grid* keys, so even MIDAS's own
native reader would see `NPanelsY=0`. Fixed by having `calib.py` set two
new plain JSON-safe attrs on the result (`panel_layout`, `panel_shifts_path`)
and `helpers._apply_panel_fields()` apply them to any spec built from that
result — additive, so every non-panel caller (Hydra, PumpProbe, PDF,
Export) is an unaffected no-op.

## 2026-08-27 — Fix: Flip Z ignored when "Multi-panel detector" is checked in Calibrate

Root cause: `calib.py`'s manual pre-flip workaround (needed because several
calibration pipelines have no native `im_trans` param — ROADMAP P3-1)
computed the auto-seed from the *raw* image but ran the solve on the
*manually flipped* image, in four branches of `run_pipeline()` — with Flip
Z on, the seed landed ~`NrPixelsZ` away from the true position and these
pipelines only do local refinement from the seed, so they converged near
the wrong (unflipped) position. Fixed via `_prep_transformed(image, dark,
im_trans)`, applied once at the top of all four branches (also fixed dark
being left untransformed in the same branches). **Class-of-bug lesson**:
whenever an array is used for both a seed step and a solve step, check
both are computed from the *same* transformed frame — a silent mismatch
between two local variables named similarly (`image` vs `img`) is easy to
introduce and won't raise an error. `first_time` pipeline branch left with
its own separate, pre-existing gap (ignores `im_trans` entirely) — tracked
as a follow-up, not fixed here.

## 2026-08-27 (`5cf2e8c`) — Error dialogs/logs never truncate the underlying exception, app-wide

A `msg[:400]`/`traceback.format_exc()[:N]` pattern was copy-pasted across
~15 files' worker-failure handlers — any long enough traceback could get
cut mid-word. Fixed once via a shared `dialogs.show_error(parent, title,
full_text, log=, log_prefix=)` using Qt's `setDetailedText` (compact
one-line summary + scrollable "Show Details…"), not just deleting the
`[:N]` slices — a full multi-KB traceback dumped as plain `QMessageBox`
text resizes into an unreadable giant box. Asked the user explicitly
whether to fix app-wide vs. just the one reported dialog; chose app-wide.

## 2026-08-26 — Project records embed calibration results + skip embedding file-backed masks; Hydra Overall Cake full UI; Data Viewer Cake tab; Workspace/Project active-Profile persistence

- **Selective mask embedding**: a mask built purely from file/folder
  sources is already reconstructable from its path+hash, so embedding its
  array too was pure size bloat; a mask with anything hand-drawn/computed
  has no file to point back to and must still be embedded.
- **Calibration attempts embed their computed Radial Profile/Eta-R Cake**
  so Open Project's Populate step is instant and independent of the
  original data file's continued existence (FAIR self-containment).
- **Hydra Overall Cake got the button/checkbox UI** matching the
  already-existing compose function; a finished Overall run also logs as
  its own `hydra_composite` attempt for provenance, deliberately not wired
  into Populate (no tab widget to restore an Overall result into).
- **Workspace/Project restore the active beamline Profile on load** — a
  project made under one Profile (different calibrants/devices/tab
  visibility) would otherwise silently apply against whatever Profile
  happens to be active at load time. Silently skipped if the recorded
  Profile no longer exists locally.

All additive to the HDF5 schema — no field renamed/removed.

## 2026-08-26 — Hydra cake-plot independent-axis zoom fix

Cake plots forced every right-drag to η-only zoom. Root cause: `pg.ImageView.
__init__` unconditionally aspect-locks its ViewBox, and an aspect-locked
ViewBox recouples both axes on every range-change (not just on drag) — no
custom `mouseDragEvent` can work around it. Fix: `vb.setAspectLocked(False)`;
stock pyqtgraph's own right-drag handler then already computes independent
per-axis zoom for free. (See ARCHITECTURE.md's Qt gotchas list.) Note: this
session's separate verification that "Overall cake needs no η-axis
resampling" only held for the `tx=0` default case — see the 2026-08-31
entry above, which found and fixed the general case.

## 2026-08-26 — Workspace/Project UX rework: unify the mental model at the UI layer only, never touch the FAIR HDF5 schema (`6b1564b`)

Root cause of the "doesn't feel convenient" complaint: two functionally
unrelated concepts ("GUI State" — a mutable JSON snapshot — and "Project" —
an append-only FAIR log) both occupied the "project" mental-model space
with neither having recents/dirty-indicator/autosave/an in-app history
browser. **Guiding constraint, honored throughout: unify the UX without
ever touching `project.py`'s schema or its opt-in/append-only guarantees**
— every new feature reuses existing read-side API or lives in new additive
sidecar files.

Key decisions (confirmed via `AskUserQuestion`): renamed "GUI State" →
**Workspace** in all user-facing labels (Workspace = editable draft,
Project = permanent FAIR record); recent-files list is global, not scoped
per beamline Profile (a recent project shouldn't stop being recent just
because the active Profile changed). Dirty-state tracking reuses each
tab's existing `get_state()` on a ~7s timer (hashed and diffed) rather than
wiring per-widget change signals across 10 tabs — can't drift from what
Save actually persists. **Autosave/crash-recovery's restore-prompt is
wired only from `main()`, never `MainWindow.__init__`** — every test
constructs `MainWindow()` directly, and a leftover autosave draft would
otherwise pop a blocking modal under the offscreen QPA platform with no
one to click it, hanging tests/CI. Project History viewer shows raw JSON
metadata rather than parsing into table columns, since the schema varies
by attempt kind and has evolved before — a generic viewer can't drift out
of sync with it.

## 2026-08-25 — ImTransOpt fix: the MIDAS backend does the pixel flip everywhere it can; GUI only flips masks (never images) and only for on-screen display (`2358ae4`)

Root cause of a Batch Integrate lineout bug: `spec_from_calibration_result()`
never copied `im_trans` onto the built spec, so raw untransformed frames
were integrated against a geometry fit on a *transformed* image — a
coordinate-frame mismatch. **First pass (pre-flip the array in GUI Python)
was explicitly rejected by the user**: MIDAS's own packages already accept
`ImTransOpt`/`TransOpt` and apply it internally — the GUI's job is to pass
the parameter, not duplicate the transform logic. **Final architecture,
one rule for the whole app: the backend performs every pixel-array flip
used for an actual calibration/integration computation; `midas_gui` never
does. The only exception is a viewer's on-screen preview array**, which may
be flipped locally for display (explicitly confirmed fine by the user —
"ok to transform temporarily to view the effect of the parameter").

**Masks are the one genuine, still-open exception**: `*BinGeometry.
from_spec(spec, mask=mask)` has no `apply_trans_opt` hook at all (unlike
every `integrate_*` function), so a mask must still be pre-flipped in
Python before reaching `from_spec`/`build_geom` — see ROADMAP P3-2 for the
upstream ask. `azimuthal_sigma_clip()` has the same gap, so Mask Builder's
azimuthal-clip branch is the one call site that deliberately keeps feeding
it an already-transformed array. **Self-inflicted bug caught and fixed
before landing**: a mask used by two different consumers in the same
worker needs to be checked per-consumer for which coordinate space it
expects — they aren't always the same (raw for pointwise zeroing, flipped
for `build_geom`). `PoleFigureWorker` (Texture) has the same untransformed-
mask gap and was left unfixed (out of scope) — tracked in ROADMAP.

## 2026-08-25 — MIDAS backend package upgrade procedure: clone env first, hold numpy/torch/numba fixed, static-diff the API before installing

Reusable procedure for the next backend version bump: (1) `conda create
--clone` rather than a fresh env from `environment.yml` — guarantees
byte-identical Qt/PyQt5 bindings, since a "faithful" rebuild from the
recipe can still solve to a different conda-forge build. (2) Hold
numpy/torch/numba fixed deliberately, not because the resolver forces it —
a numpy 2.x move is a much larger, separately-scoped change. (3) **Static
API-diff every symbol `midas_gui` imports (old vs. new) before touching any
environment** — this is why a 0.3.x→0.6.x-scale jump needed zero
`midas_gui` source changes; don't assume semver discipline instead. (4)
Verify via per-file isolated pytest runs in both old and new env, run
several times each — a single run can't distinguish "always crashes now"
from "sometimes crashed before" given the known teardown-crash flakiness.

## 2026-08-25 — Open Project's auto-plots reuse the exact live-fit code paths; Batch's replay is new code because no such path existed

Calibrate/Hydra Calibrate needed **no new "replay" logic** — the same
methods a live fit calls (`_draw_rings`, `_run_integration`) are already
pure functions of a duck-typed result object, so a stored project attempt's
reconstructed result works identically to a fresh `CalibrationWorker`
result. **Batch/Hydra Batch needed genuinely new code** — the only existing
data path into the waterfall/stacked-profile widgets was `_on_frame`'s
incremental per-frame `add_profile()`; there was no "given a complete
profiles array, populate the whole plot" entry point, and
`project.read_attempt` never read integration `results/*` arrays at all
(only JSON metadata) until a new `read_attempt_results()` was added.
**Verification finding, not a fix**: the pre-existing pyqtgraph
interpreter-teardown segfault was confirmed (via `git stash` A/B) to
reproduce on unmodified `main` too — not a regression from this session.
Per-file test isolation is the only trustworthy verification method going
forward, not just for the two previously-named heavy Hydra UI files.

## 2026-08-25 — Profile switch refreshes option *lists* live, never re-seeds already-built field *values*

Two related bugs: profile switching was buried in Preferences (fixed by
adding a header combo alongside it, both routing through one
`on_profile_changed()`), and several dropdowns/menus (device list,
calibrant list, pixel-size/K-edge popups) silently kept showing the
*previous* profile's choices until restart. **Deliberate asymmetry**:
option *lists* refresh live (a finite, profile-owned vocabulary — the
widget's current selection is preserved if it still exists in the new
list), but numeric/path *default values* a profile seeds into a field are
NOT re-pushed into already-built fields on a switch — there's no way to
tell a field's current value is the seeded default vs. the user's own
in-progress edit, so re-seeding on every switch risks silently clobbering
real work. Hydra mode gated to the 1-ID-E profile (only that beamline has
the 4-panel GE detector).

## 2026-08-24 — Open Project's "populate the GUI" reuses GUI-State's widget-key vocabulary; Batch Integrate gets a real calibration, not just display fields (`e693316`)

Opening a project previously only marked it active for *future* logging —
fields were never actually populated. The attempt→GUI mapping reuses
`_state_widgets()`'s existing widget-key vocabulary via
`apply_dict_to_widgets()`, rather than a bespoke schema, because the
single-detector tab's state dict is literally the union of the two Hydra
per-panel/shared dicts — one pure function
(`project.calib_attempt_gui_fields`) serves all three call sites. **Batch
Integrate's populate step calls `set_calibration()` directly** rather than
going through `set_state()` (which deliberately never restores a live
calibration result — no live object to restore from plain GUI State) —
a project attempt is different: it already recorded the exact calibration
values used, so `project.calibration_namespace()` turns that stored dict
into a duck-typed object the existing "From Tab 2" path already accepts.
Explicitly out of scope: restoring dark/bright/background/mask sources
that were embedded directly with no owning file (pre-existing gap, not a
regression).

## 2026-08-24 — Hydra seed-mode linking is signal-fanout across per-panel checkboxes; cake data reuses an already-computed, previously-discarded array (`162fef1`)

**Seed-mode linking kept the checkboxes on each per-panel card** and
synced their state via signals, rather than hoisting one shared checkbox
to the toolbar — several existing per-panel methods already toggle the
checkbox as a side effect scoped to *that* card; moving it would have
meant rewriting all those call sites for a purely cosmetic win. **The
Eta-vs-R cake array was already being computed by the backend and silently
discarded** — `integrate_frame(return_cake=True)` existed before this
session (used by Batch's `2d_csv` export), but `IntegrationWorker`'s
post-fit auto-integration call site never passed the flag. New `CakeViewer`
widget rather than retrofitting `ImageViewer`, because `ImageViewer`
assumes displayed array indices *are* physical detector-pixel coordinates
starting at (0,0) — a cake's axes are R(px)/η(°) bin centres, neither
starting at the origin nor 1-unit-per-pixel.

## 2026-08-24 — File ▸ Project: FAIR provenance is separate from GUI State, always best-effort, and links Batch → Calibrate attempts (`e8dea6b`)

**Deliberately a separate concept from GUI State**, not an extension of
it: GUI State is a point-in-time snapshot meant to be overwritten (Ctrl+S
semantics); a provenance record must never be overwritten — it accumulates
a full history across many separate GUI launches over an experiment's
lifetime. **Raw scan data is referenced by path + checksum, never
duplicated** (a Hydra Batch run can touch thousands of frames) — hashed
fully under 200MB, head+tail fingerprint above. Masks/dark/bright/
background *are* embedded directly (small, and a hand-drawn mask has no
file to reference). **Logging is always best-effort and must never affect
the run's own outcome** — wrapped in a bare `try/except` that logs failure
to the tab's own Log panel; a provenance-write problem must never make a
successful run look failed. **Batch→Calibrate linking uses a ref string
bolted onto the result object** (`result._project_attempt_ref`), cheaper
than re-deriving "which calibration attempt matches these values" by
scanning the HDF5 file — correct as long as the same GUI session did both
runs.

## 2026-08-24 — Batch Integrate Hydra split: automatic hand-off, per-panel masks (diverges from Calibrate's scope-cut), lazy page construction

**Hand-off is automatic push, not a manual pull button** — unlike the Data
Viewer↔Calibrate hand-off (manual, because the Data Viewer has no
"finished" event), a Hydra panel's fit genuinely finishes, so Batch
Integrate's calibration source populates itself the moment that panel's
fit completes, mirroring the single-detector tab's existing auto-wiring.
**Masks ARE wired per panel for Hydra Integrate — a deliberate departure
from Hydra Calibrate's `mask=None` scope-cut** (confirmed with the user):
bad-pixel/beamstop masking matters more for integration-profile quality
than for ring-centroid fitting; each panel gets its own independent
`MaskSelector` with no cross-panel auto-discovery (mask files are
physically panel-specific, unlike data files). Drift correction and live
MONITOR mode deferred for Hydra v1 (user confirmed). `HydraBatchPage` is
built lazily (unlike its eagerly-built siblings) since it owns 8
pyqtgraph widgets and most sessions never open it.

## 2026-08-24 — Calibrate tab Hydra split: shared-vs-per-panel field boundary; `CalibrationWorker`'s stdout capture made optional for Parallel mode

**Field-sharing boundary** (confirmed with the user): wavelength, pixel
size, calibrant, and refine-parameter selection are shared across all 4
panels (one beam, one choice of what to refine); Transforms and the
initial seed (BC/Lsd/tilts) are independent per panel (each GE module is a
physically separate detector). **`CalibrationWorker`'s stdout capture had
to become optional** for the new Parallel run mode — the worker redirects
the *process-global* `sys.stdout` for the pipeline call's duration, safe
with one worker at a time but a data race with several racing in parallel.
Added `capture_stdout: bool` (Sequential=True unchanged, Parallel=False —
each worker's fine `print()` output goes to the real console instead of
the Log tab, but the Qt signals that don't touch global state are
unaffected).

## 2026-08-23/24 — Hydra composite windmill orientation: final state is CCW rotation by `tx` plus a separate vertical-axis mirror

Took several iterations to land on the correct orientation for
`hydra.py::compute_inv_coords`'s composite placement math:
**counterclockwise rotation by each panel's own `tx`** (not clockwise — an
earlier same-day fix flipped the sign based on a plausible-looking nominal-
value match, without actually rendering and comparing against a known-
correct reference; that fix was itself wrong and was reverted), **plus** a
separate left-right mirror of the whole finished composite canvas (`Y_lab =
(half - Yo) * px`, not `(Yo - half) * px`) needed to put the correct panels
on the correct sides. No `invertX` on the underlying viewer is needed
beyond that — verified via real Debye-Scherrer ring continuity across all
4 panel boundaries plus an actual rendered comparison.

**Methodological lesson (why this took several wrong turns)**: ring-
continuity / self-consistency checks are **blind to a global orientation
error** — a mirror combined with a compensating rotation-direction error
produces locally plausible, continuous-looking arcs just as easily as the
correct answer, especially when each panel's own tilt was fit
independently. Only a real windowed comparison against a known-correct
reference image (someone who knows what the physical detector actually
looks like) can catch this class of bug — don't trust a sign-convention
conclusion for a global rotation/mirror without that check, no matter how
good the self-consistency argument looks on paper.

This same 2026-08-23 pass also added: shared λ/max-2θ/px mirrored live
across ge1-4 + Composite cards; dark/bright/background correction for
Hydra (`HydraFieldSelector`, closing a scope-cut in the original module
docstring); a stale-radial-integration-geometry fix (`_effective_calib_geom()`
was returning a frozen snapshot instead of live BC/λ/Lsd/tilt widget
values); vmin% percentile now excludes exact-zero pixels (was washing out
auto-level on the Hydra composite's mostly-empty canvas — single fix point
in the `ImageViewer` base class, benefits every viewer).

## 2026-08-23 — Rare pyqtgraph teardown crash under a large test suite; mitigated, not fixed

A large `tests/test_hydra_ui.py` (many `pg.ViewBox`/`pg.ImageView`
instances per test) intermittently crashed the interpreter outright
(segfault/bus error) during the full suite — pyqtgraph's own `ViewBox`/
`WidgetGroup` global-registry teardown fragility, not a bug in this
codebase. **`gc.collect()` in test teardown made it WORSE** (forces
Python-level GC into pyqtgraph's half-torn-down object graph more often).
Just pumping the event loop (`app.processEvents()`) after each test, with
no forced GC, measurably reduced the crash rate (not a guaranteed fix).
See the 2026-08-30 `pytest-forked` entry above for the deeper, related
fork()-after-multithreading problem this doesn't solve.

## 2026-08-23 — Detector-image origin flipped to bottom-left (MIDAS convention)

Standing design decision: every image viewer places pixel (0,0) at
bottom-left, not top-left, because MIDAS assumes this origin and the
Flip Y/Flip Z/Transpose controls only make sense as "align raw readout to
the world view looking downstream along the beam" if the baseline
rendering already matches that world view. Root cause/fix: `pg.ImageView.
__init__` unconditionally calls `invertY()` — one `vb.invertY(False)` in
`ImageViewer.__init__` fixes every viewer built on it (Data Viewer, Mask
Builder, Calibrate). Confirmed a pure visual flip needing no data/geometry
changes: all click-to-pixel code goes through pyqtgraph's invert-aware
`mapSceneToView`, and ring-geometry math computes in pixel-index space with
no reference to `invertY`. The other three 2D displays in the app (pump-
probe heatmap, pole figure, waterfall) already default to bottom-left via
plain `PlotWidget`/`ImageItem` and were left untouched.

## 2026-08-12 — Mask tab: multi-file stack picker + configurable bad-pixel dilation

Explicit `self._stack_files` list (not a delimiter-packed string) keeps
multi-select fully separate from existing folder/file/glob parsing;
cleared automatically whenever the path field is edited directly, so
switching input modes can't leave stale multi-select state behind.
Dilation applied once, in `_set_mask()`, to the *computed* mask only —
never hand-drawn shapes, which are combined in afterward and must stay
exactly what the user placed. User later corrected the dilation semantics
from 4-connected to **8-neighbor (full-block)** growth — an explicit 3×3
structuring element with `iterations=n` gives the requested `(2n+1)×(2n+1)`
square per bad pixel, no custom BFS needed.

## 2026-08-12 — PDF tab rebuilt for full Stage 2-3 workflow

Rebuilt from Stage-1-only to the full workflow (absorption, detector-
efficiency, absolute normalization, multiple scattering, fluorescence
diagnostic, CIF-driven structure fit, Δ-PDF) — deliberately excluding
Bayesian SVI/NUTS, RMC, SAXS/SANS joint refinement, multi-phase, anisotropic
ADP, directional strain-PDF (kept out of `pdf_backend.py`'s re-exports on
purpose; see ROADMAP.md). Three gotchas worth remembering if touched again:
**`QCheckBox`, not a checkable `QGroupBox`, for optional-stage toggles** —
`widgets_to_dict`/`apply_dict_to_widgets` only persist `QAbstractButton`
subclasses, and a checkable `QGroupBox` isn't one, so its checked state
would silently fail to save/restore. **`pdf.delta_pdf(...)` requires
`torch.Tensor` inputs**, not numpy arrays. **`refine_structure`'s `fitted`
dict can contain a `"bg_coef"` value that's a `list`** (when `bg_order` is
set) rather than a scalar — code formatting `fitted.items()` must branch on
type. Also: the ~30x detector-efficiency amplification seen for thin Si at
hard X-ray energies is physically correct, not a bug (confirmed by direct
computation).

## 2026-08-10 — Upgrade all MIDAS backend pins; retire vendored `midas_pdf`

**`midas_pdf` is now the real public PyPI package**, not vendored — deleted
`midas_gui/_vendor/` (33 files), rewrote `pdf_backend.py` to a plain
`import midas_pdf` + re-export, no more `sys.path` tricks or the
`midas_hkls.absorption` compatibility shim (safe now that installed
`midas-hkls>=0.5.0` ships `absorption` natively). Newly pinned for
completeness several transitive MIDAS deps that were already being pulled
in implicitly but never declared (`midas-integrate`, `midas-peakfit`,
`midas-zipper`, `hdf5plugin`, `psutil`) plus `scikit-image` (a soft/
try-except optional dep of `midas-calibrate-v2`'s better ring seeding that
no MIDAS package declares) — goal was a `pip install .` that reproduces the
verified-working set on its own. NumPy-1.x pin chain re-confirmed
unchanged (numba/torch/pvapy compatibility).

## 2026-07-16/17 — Adopted the two-layer `.context` system; migrated legacy `claude/` knowledge into it

STATE (disposable-but-current) + DECISIONS (permanent) split so returning
to this project after a gap is cheap — only STATE.md auto-loads, detail is
read on demand. Folded the old per-session `claude/` folder into this
structure and deleted it; the build-critical `midas_pdf` reference stack
was *moved* (not summarized) to `.context/reference/midas_pdf/` — too
detailed to lose. Discarded as stale: `CLAUDE_original_scratch.md` and
`claude/gui_documentation.md` (a strict subset of the shipped doc).
