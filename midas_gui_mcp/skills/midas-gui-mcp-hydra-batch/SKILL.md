---
name: midas-gui-mcp-hydra-batch
description: Use when integrating Hydra (4-panel ge1..ge4) datasets through the midas-gui MCP server's hydra_batch_* tools, including several datasets that each use their own calibration files, e.g. "integrate these scans with the Oct calibration", "run the queue", "why did the ge1234 merge fail".
---

# midas-gui MCP: Hydra Batch Integrate

Drives the Batch Integrate tab's Hydra page. The tools set the same widgets a
click would and run the page's own integration, so GUI and agent agree. Check
`midas-gui-mcp-setup` first (GUI running with the control socket).

## One dataset

1. `hydra_batch_set_scans(paths=[<one ge1..ge4 file>], output_dir=..., dark=<one panel's dark>)`
   — the other three panels are found by the `geN` token. Several paths from
   different folders = several scans (each its own folder under `output_dir`).
2. `hydra_batch_set_calibration(files={"ge1":..,"ge2":..,"ge3":..,"ge4":..})` or
   `from_file=<one panel's calibration>` (others derived by name). `.json`
   (`*.instr.json`), paramstest `.txt`, `.poni` all work.
3. Optional: `hydra_batch_set_caking(r_bin=..., eta_bin=..., ...)`,
   `hydra_batch_set_options(formats=["csv","zarr"], combine_zarr=True, run_mode="parallel")`.
4. `hydra_batch_run` -> `job_id`; `hydra_batch_wait(job_id)` (call again while
   it still says `running`). Read `result.scans[].panels` (status, frames,
   error) and `result.scans[].combine` (wrote/total/errors).

## Several datasets, different calibrations

The page holds ONE calibration per panel for a whole multi-scan run. When the
datasets need different calibration sets, use `hydra_batch_run_queue(jobs=[...])`:
each job carries its own `scans`, `calibration`, `output_dir` (and optionally
`dark`, `caking`, `options`). All jobs are validated first; a bad job is
reported `failed` and the queue continues unless `stop_on_error=True`. Settings
persist in the GUI afterwards: the last job's calibration is still loaded.

## Gotchas

- **Rmax 0 = auto = one value shared by every panel** (largest farthest corner).
  Per-panel auto used to give each panel a different n_R and the `ge1234`
  merge failed with "OmegaSumFrame first-dim differs". If you type an explicit
  `r_max`, set it on all panels (the default sets all).
- The merge needs `zarr` in `formats` and `combine_zarr=True`.
- Hydra mode only shows on the 1-ID-E beamline profile in the UI; the tools
  switch the tab into Hydra mode themselves.
- `hydra_batch_run` refuses with `precondition_failed` and a reason (no scans,
  no output dir, uncalibrated panel, unwritable folder) instead of opening a
  dialog. `hydra_batch_abort` stops a run or a queue; an aborted scan is not
  merged.
- Panels that cannot resolve a calibration are *skipped*, so a run with one
  missing calibration still reports `failed` (not `done`).
