# Solve-cell ingest tuning — procedure for a new, unknown dataset

Purpose: pick the ingest parameters (mask, background model, blob cuts) for a
dataset you have never processed, **from the data**, before indexing. Follow the
steps in order; each has a pass/fail check. Everything here is dataset-agnostic:
numbers in `[brackets]` are the worked example (Ruby7, Eiger 1028x512, 0.02 s,
~4 counts/px background) and must be re-derived, never copied.

**Automated:** `midas_gui/solve_cell/tuning.py` implements steps 1-6 (advisory, Qt-free,
deterministic, process-parallel): `python -m midas_gui.solve_cell.tuning --data RAW.h5
--project any_saved_project.h5 --out result.json`, or `suggest_ingest_params(frames, geometry)`.
It returns the suggestion, a confidence grade, warnings and the evidence tables.
In the GUI: Solve Cell tab -> Ingest -> **Suggest parameters**, then **Set as parameters**. Its
thresholds are the module constants at the top of that file. The rest of this document
is the manual procedure it follows.

Evidence base: worked example in the last section. Provenance: DECISIONS/STATE
do not hold this; the numbers came from the sweep script below run on real data.

## 0. Inputs and environment

Need: raw frame stack (path + dataset), the panel's calibration (Lsd, BC, tilts,
`im_trans`), omega window (first/last frame idx, step), optional bad-pixel mask.
The easiest source of geometry/omega is any saved project `.h5` for that panel
(`analysis/solve_cell/ingest/metadata` -> `panels.<id>.geometry_cfg`).

Python env: use the GUI's conda env (`~/miniconda3/envs/midas-gui/bin/python`:
h5py, hdf5plugin, scipy, torch, `midas_defect`). Other envs usually lack the
HDF5 compression plugin or `midas_defect`. `python -I` ignores `PYTHONPATH`; run
scripts from a scratch dir, not the data dir. Never write into the data dir.

## 1. Characterise the data (5 min, no GUI)

Load the omega-window frames as float32, set sentinel `4294967295` to 0, then
compute and record:

- `med = median over frames, per pixel` -> percentiles [1, 50, 99, 100].
- `sigma = 1.4826 * MAD` of a background-subtracted far-field patch.
- fraction of pixels with `med < 1`, `< 5`, `< 20`.

Decide the regime:

| Typical `med` | Regime | Consequence |
|---|---|---|
| < ~20 counts | photon counting | thresholds ~8-20; `low_count_threshold` must be ~0; the negative-blob control is weak (see 4) |
| >= ~100 | high counts | thresholds scale with sigma (4-6 sigma); the control works well |

Rule of thumb: first-guess threshold = 4-6 x sigma of the **subtracted** stack,
never an eyeballed raw-image level. The blob threshold acts on the
background-subtracted stack, not on raw counts.

## 2. Mask (`low_count_threshold`, `mask_grow`)

`low_count_threshold` masks pixels whose median-over-frames is below it. It is an
absolute count level, so it is only valid when ordinary pixels sit well above it.

1. Start at `0`. Keep `mask_grow = 2`.
2. Run the ingest/mask step and read `build_mask: N/total px masked`.
3. **PASS** if N is a small fraction (a few 0.1%: gaps + known bad pixels).
   **FAIL** if > ~1-2 %. At 100 % everything downstream is zeros and ingest
   returns 0 spots with no error (seen: `low_count=20` at ~4 counts/px).
4. If you want dead/shadow pixels masked, sweep the threshold over
   [0, 1, 2, 5, 10, 20] and use a value on a plateau in masked-pixel count
   (shadow/dead pixels sit near 0, normal pixels far above). No plateau -> keep 0
   and supply a bad-pixel mask file instead.

## 3. Build the background-subtracted stacks (the expensive part)

Use `midas_defect.ingest.subtract_background` with the panel's `tth`/`az` maps
(same construction as `pipeline._stage_ingest`: `Geometry` -> `detector_angle_maps`
-> `pipeline._calib_map_to_raw(map, im_trans)`). Build one stack per candidate
`n_sectors` in `{1, 8, 24, 96}`.

Cost (measured, 12-core Mac, 512 x 1028 frames): `subtract_background` is
~0.03 / 0.06 / 0.14 s per frame for 1 / 24 / 96 sectors (0.4 / 0.6 / 1.4 min for 634
frames). `find_blobs_3d` costs ~0.1-0.2 min per run with `gap_bridge=0` but ~1.5 min
per run with `gap_bridge=21` (the bridging step dominates, ~25x, even when the mask
has no gaps). **Run the sweep with `gap_bridge=0`**; it only affects blobs that
straddle a masked gap, which does not change the tuning counts materially (confirm
in step 7 if the GUI has gap-bridging on). The first version of this procedure ran
with `gap_bridge=21` and took ~10 min per candidate for no benefit.
Also: stacks are ~1.3 GB each for 634 frames; peak memory is one stack per worker.

Do not substitute a temporal (rolling-median-over-omega) background: on the
worked example it gave 10-20x more negative blobs than polar-24 and no gain in
real spots.

## 4. Choose `sector_candidates`

For each stack, run `find_blobs_3d` on `+stack` and on `-stack` (the sign control:
diffraction is positive-only, so negative blobs measure background-model error)
at a **low, noise-dominated** threshold T_lo (`~5.5 x sigma`, sigma = MAD-sigma of the subtracted stack; [8]) with
loose cuts (`min_vol=2`), then count survivors for tighter cuts.

Selection rule (apply in this order):

1. Reject candidates whose negative count grows while the positive count does not
   fall (the model is fitting spots and leaving negative halos) [96 sectors].
2. Among the rest, take the one with the **fewest noise-dominated positives**
   (count at T_lo with `min_vol>=2, min_frames>=3`) [24: 788 vs 1: 2396].
3. Prefer fewer sectors on ties.

Traps:
- In photon-counting data a subtracted pixel cannot go below ~-background, so the
  negative count can be exactly 0 for a poor model [1 sector: 0 negatives, 2396
  noise positives]. **A zero negative count is not evidence of a good
  background** — rule 2 is what picks the winner.
- `choose_sectors` ranks by `neg/pos`; with a zero or tied ratio it takes the
  first candidate. Therefore set `sector_candidates` to the single winning value
  explicitly in the GUI rather than leaving a grid.

## 5. Choose `blob_thresh`, `blob_minvol`, `blob_minframes`

On the winning stack, tabulate positive/negative blob counts for
T in {5.5, 6.5, 8, 10, 13.5} x sigma [8, 10, 12, 15, 20] x (min_vol, min_frames) in
{(2,1), (2,3), (4,3), (8,3)}. Then:

1. **Find the plateau:** the count that stays within ~15 % across the tighter
   cuts and the higher thresholds is `N_real` [~50-55]. Counts far above it are
   noise (many 1-3 voxel, 1-2 frame blobs near the threshold).
2. **Pick the loosest setting** whose positive count is within ~25 % of `N_real`
   **and** whose negative/positive ratio is <= ~5 % [T=15, min_vol 4,
   min_frames 3 -> ~60 spots].
3. Sanity-check physically: `min_frames` should not exceed the real omega width
   of the reflections (spot `n_frames` at the plateau, median/5th percentile);
   never set it so high that genuine short reflections vanish.
4. If a conservative set is wanted, take the plateau's strict end
   (higher T, `min_vol` 8) [T=20, min_vol 8 -> ~50 spots, ~1 negative].

Raising the threshold alone is the wrong tool for noise blobs: noise rarely
forms connected 3-D structures, so tighten `min_vol`/`min_frames` first.

## 6. Diffuse structure the background model cannot remove

Check whether bright diffuse scatter (thermal-diffuse streaks, amorphous halo,
DAC gasket) survives subtraction:

- `star = time-mean(raw) > ~2x typical med` ; report mean and p99.9 of the
  subtracted stack on `star` vs far field.
- Large positive residual there + circles clustered in it = spots are partly
  diffuse signal. Options: mask it with a user mask, or drop spots inside it after
  ingest. Do not try to fix it with more sectors (it made things worse).
- If the star is omega-dependent, its time-median looks low; use the time-mean.

## 7. Verify in the real pipeline, then record

1. Put the chosen values in the Solve Cell tab (or the headless spec's
   `fields`/`blobs`/`mask`) and rerun ingest.
2. **PASS** if: masked fraction as in step 2; `n_spots` within ~25 % of
   `N_real`; spot `n_frames` median >= `min_frames`; spots no longer pile up in
   the diffuse region; indexing no longer reports an implausibly large number of
   peaks.
3. Save the values **and the evidence table** (counts per setting) in the project
   notes / `.context/DECISIONS.md` (parameters are dataset-tuned, not universal).
4. For a new panel/position of the same sample and setup, rerun steps 1-2 and
   check that step 5's plateau is similar before reusing values.

## Sweep script (adapted from the scripts used for the worked example)

Run from a scratch dir with the `midas-gui` env python. Parameters at the top.

```python
import json, sys, time, numpy as np, h5py, hdf5plugin
from midas_defect.ingest import build_mask, subtract_background, find_blobs_3d
from midas_defect.geometry import Geometry, detector_angle_maps
from midas_gui.solve_cell.pipeline import _calib_map_to_raw   # needs PYTHONPATH=<midas-gui repo>

DATA, DSET = "RAW.h5", "entry/data/data"
PROJ, PANEL = "any_saved_project.h5", "1"       # source of geometry + omega
FIRST, LAST = 20, 653                            # omega-window frame idx (inclusive)
SENTINEL = 4294967295
SECTORS = (1, 24, 96)                            # add 8 for a fuller grid
T_LIST  = (8, 12, 20)                            # ~5.5, 8, 13.5 x sigma
CUTS    = [(2, 1), (2, 3), (4, 3), (8, 3)]       # (min_vol, min_frames)

raw = h5py.File(DATA, "r")[DSET][FIRST:LAST + 1]
sent = raw == SENTINEL
a = raw.astype(np.float32); a[sent] = 0
m = json.loads(h5py.File(PROJ, "r")["analysis/solve_cell/ingest/metadata"][()])
g = m["panels"][PANEL]["geometry_cfg"]; codes = g["im_trans"]
geom = Geometry(lsd_um=g["Lsd"], bcy_px=g["BC_y"], bcz_px=g["BC_z"], px_um=g["px_um"],
    wavelength_A=g["wavelength_A"], n_pix_y=g["nrpixels_y"], n_pix_z=g["nrpixels_z"],
    omega_first_deg=g["omega_ref_deg"], omega_step_deg=g["omega_step_deg"],
    n_frames=len(a), tx_deg=0.0, ty_deg=g.get("ty", 0), tz_deg=g.get("tz", 0))
tth, az = detector_angle_maps(geom)
tth, az = _calib_map_to_raw(tth, codes), _calib_map_to_raw(az, codes)
mask = build_mask(a, low_count_threshold=0.0, grow=2).mask | sent.all(axis=0)
print("masked px:", int(mask.sum()), "/", mask.size)

for n in SECTORS:
    t0 = time.time()
    sub = subtract_background(a, tth, az, mask, n_sectors=n)
    sig = 1.4826 * np.median(np.abs(sub[::8][:, ~mask]))
    print(f"== n_sectors={n}  MAD-sigma={sig:.2f}", flush=True)
    for T in T_LIST:
        pos = find_blobs_3d(sub, mask, threshold=T, min_vol=2, gap_bridge=0)
        neg = find_blobs_3d(-sub, mask, threshold=T, min_vol=2, gap_bridge=0)
        row = []
        for mv, mf in CUTS:
            p = ((pos.volume_vox >= mv) & (pos.n_frames >= mf)).sum()
            q = ((neg.volume_vox >= mv) & (neg.n_frames >= mf)).sum()
            row.append(f"v{mv}f{mf}:{p}/{q}")
        print(f"  T={T} (pos/neg) " + "  ".join(row), flush=True)
    print(f"  [{time.time()-t0:.0f}s]", flush=True)
```

Read the output with steps 4-5. The negative count here is the sign control;
see the step-4 traps before trusting a zero.

## Worked example (Ruby7_25kev_exp0p02, panel det_-185_-10_0)

- Regime: median pixel 1-8 counts (photon counting), far-field MAD-sigma ~1.5.
- Mask: `low_count=20` masked 526336/526336 px -> 0 spots. `0` -> 42 px masked.
- Sectors: 1 -> 2396 noise positives (T=8, v2f3), 0 negatives (uninformative);
  24 -> 788 / 19; 96 -> 795 / 37 and negatives rising at every cut. **24 wins.**
- Blob cuts on 24 sectors (pos/neg): T=12 v4f3 67/2; T=20 v4f3 53/1; T=20 v8f3 50/1.
  Plateau ~50-55 real reflections. Chosen: T=15, min_vol 4, min_frames 3.
- Before: sectors 1, T=15, min_vol 2, min_frames 1 -> 285 spots (~220 noise).
- Temporal rolling median (w=21): 22-56 negatives at the same cuts -> rejected.
- Diffuse star: ~1941 px with time-mean > 8, mean residual ~+50 after subtraction;
  not removable by sectors -> mask or post-filter.
- T=15 itself was interpolated between measured T=12 and T=20, not run.
