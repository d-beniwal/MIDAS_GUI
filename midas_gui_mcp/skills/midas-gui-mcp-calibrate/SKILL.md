---
name: midas-gui-mcp-calibrate
description: Use when calibrating a detector (CeO2, LaB6, Si, Al2O3 powder pattern) through the midas-gui MCP server's calibrate_* tools. Covers required inputs, the default seed policy (unseeded first, then feed result back, frozen-point first for high tilt), polling, abort, and result checks.
---

# midas-gui MCP: calibration

Assumes the GUI and server are up (see `midas-gui-mcp-setup`).

## Required inputs

**Wavelength (or energy) and pixel size are mandatory starting inputs.** They
come from the user's prompt, or from a file or filename the user points to
(parameter file, calibration file, name containing the energy). If either is
missing or ambiguous, ask; never fall back to the tab's defaults (75 µm,
0.39 Å are leftovers, not data). Convert energy to wavelength: λ[Å] =
12.3984 / E[keV]. Do not trust a file's Lsd, BC or lattice comments: it may
describe a different calibrant, and auto-seed recovers a wrong Lsd guess
(800 mm guess -> 1000 mm found). Confirm the calibrant if file and request
disagree.

## Defaults (apply unless the prompt says otherwise)

1. **Start with no initial seed.** Use `clear_seed=True` on the first run and
   do not pass `manual_seed`. Seeds persist between runs, so a stale one would
   silently apply otherwise.
2. **Then feed the result back.** After the first run finishes, call
   `calibrate_seed_from_last_run()` ("Feed result back to seed": it copies the
   run's refined parameters into the seed fields for the next run), then run
   again. Report both runs so the user sees whether the second run moved.
   Skip this only when the user says so (e.g. "single run", "no feedback").
3. **High-tilt detector: frozen-point first.** If the user, the file or the
   beamline information says the detector is strongly tilted, do not start
   with one-shot. Run `frozen_point` first (unseeded, per rule 1), feed its
   result into the seed, then run `one_shot` seeded by it.
   `calibrate_run_frozen_point_then_oneshot` does this chain in one call;
   pass no seed in either cfg. Do not decide "high tilt" from a guess. If
   nothing says so, run the normal flow, and if the first result shows large
   tilts, tell the user and offer the frozen-point chain.

## Recipe

1. **Inputs:** gather wavelength/energy and pixel size (above), the calibrant
   and the image path.
2. **Wavelength and pixel size have no direct tool.** Set them through the
   Data Viewer: `data_viewer_set_geometry(wavelength_A=..., pxY=..., pxZ=...)`
   then `calibrate_import_from_data_viewer()`. This also copies the viewer's
   other geometry into the tab; harmless when running unseeded.
3. `calibrate_load(path, calibrant="LaB6")`: set the calibrant explicitly;
   the tab keeps the previous one (CeO2 by default).
4. `calibrate_get_state()` and check `fields`: `cal`, `wl`, `pxY`, `pipeline`,
   and every `seed_en_*` is false.
5. First run: `calibrate_run(mode="one_shot", clear_seed=True)` (or the
   frozen-point chain for a high-tilt detector).
6. Poll `calibrate_get_job(job_id)` every few seconds until
   `status != "running"`. `calibrate_wait` can return `running` early.
7. Feed back and re-run per Defaults rule 2: `calibrate_seed_from_last_run()`,
   then `calibrate_run(mode="one_shot")` **without** `clear_seed`, so the fed
   seed is used. Check `seed_en_*` in `calibrate_get_state` first.
8. Report Lsd (µm -> mm), BC_y/BC_z, ty, tz, post_residual_strain_uE, and
   which values were fixed (wavelength, tx). `fit_sigma` and `fit_at_limit`
   can be null.

## Abort

`calibrate_abort()` is the Abort button. It frees the tab so a new run can
start, and marks the job `aborted`. A new `calibrate_run` is refused while a
run is still active. It does not touch seeds or projection jobs.

## Do not

- Run a calibration with a calibrant, wavelength or seed you have not
  verified in `calibrate_get_state`.
- Save or export (`calibrate_save_*`, `calibrate_export_to_data_viewer`)
  unless asked; these change files or the Data Viewer.
- Claim the fit is good from the numbers alone. The ring overlay needs a
  look in the GUI; tell the user to check it.
