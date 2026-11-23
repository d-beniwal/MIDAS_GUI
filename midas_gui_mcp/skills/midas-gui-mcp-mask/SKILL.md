---
name: midas-gui-mcp-mask
description: Use when building a detector bad-pixel / region mask through the midas-gui MCP server's mask_* tools — choosing the image and stack, threshold, statistical (spatial/temporal), spike, cosmic-ray, azimuthal/learnable masks, dilation, hand-drawn shapes, then saving the mask TIFF or the project.
---

# midas-gui MCP: Mask Builder

Prerequisite: the GUI is running with the control socket (see `midas-gui-mcp-setup`). Every `mask_*` tool
drives the Mask Builder tab itself (the tab comes to the front), so you can watch it. `mask_compute` is
**async**: it returns a `job_id`; block on it with `mask_wait(job_id)`. Read everything back with `mask_get_state`.

## Order matters

1. `mask_set_image(path, dataset, frame)` — a file, or a **folder** of single frames (frame navigator, 1-based).
   **Loading an image resets the upper threshold** to the dtype maximum, so set the threshold after it.
2. `mask_set_threshold(lower, upper, projection, skip_frames)` — masked where pixel <= lower or > upper.
   `lower <= -1e9` / `upper <= 0` switch a test off. `projection` (Average/Sum/Max) needs a multi-frame Image.
3. Statistical: `mask_set_statistical(spatial, temporal, k_sigma, hot_factor, dead_factor, frozen_frac)` and
   `mask_set_stack(path | files, dataset, stride)`. **One stack per compute**; spatial uses its temporal
   median (no stack: the single image), temporal needs >= 2 frames, cosmic-ray >= 3.
4. Optional: `mask_set_spike`, `mask_set_cosmic_ray`, `mask_set_calibration` + `mask_set_calibration_methods`
   (azimuthal / learnable — refused without a calibration), `mask_set_postprocess(dilation)`.
5. `mask_compute` -> `mask_wait`. Read `result.n_masked`, `percent_masked` and **`warnings`** (a method that
   was skipped, e.g. temporal constancy with no stack, is a warning, not a failure).
6. Hand-drawn regions: `mask_add_shape` / `mask_add_points`, then `mask_apply_shapes` (ORs them into the
   mask). `mask_list_shapes`, `mask_remove_shape`, `mask_clear_shapes` (also drops the drawn part of the mask).
   **Eraser:** `mask_erase(centers, size)` clears mask pixels inside circles of `size` px diameter (default 2,
   the slider) at raw [x, y] positions — one undoable stroke per call (`mask_undo_erase`, 50 deep); it edits
   the computed and hand-drawn parts, so a later `mask_compute` / `mask_apply_shapes` rebuilds the mask and
   drops erasures. `mask_set_eraser(enabled, size)` only shows/hides the GUI brush for a human watching.
7. Output: `mask_save(path)` (TIFF, uint8, 1 = bad), `mask_get_array` (a `.npy` reference to read with numpy),
   `project_save` (stores the tab's settings and the mask; see `midas-gui-mcp-project`) and
   `mask_log_to_project` (adds a mask attempt to the open project).

Shapes use RAW detector coordinates (x = column, y = row; pixel (c, r) covers [c, c+1]); the tab carries them
through the Data Viewer's flips/transposes. A mask TIFF is always raw-frame too.

## Choosing the data (what worked on a Pilatus 2M, 1475x1679)

- **Detector defects need a smooth, spot-free image.** An amorphous background scan (fused silica, air) with many
  frames is best: any pixel far from its neighbours is a defect. A powder-ring image (CeO2) is the WORST for the
  spatial test (ring edges look like outliers); a sample scan with Bragg spots flags the spots as hot pixels.
- Check the data before trusting a result: `mask_get_array(which="image")`, and compare the mask between two
  datasets from the same detector — real defects repeat at the same pixels, signal does not.
- The detector firmware already flags gaps (-1) and dead pixels (-2) on Pilatus: `lower=0` masks them first.
  Pilatus counters are 20-bit: `upper=1048575` with a `Max` projection catches over-range pixels in any frame.
- Defaults (K_sigma 6, hot 1.5, dead 0.5) miss weakly bad pixels; `hot 1.25 / dead 0.8` recovered ~90 % of the
  pixels that were off by >25 % in two independent datasets. Keep `dilation` 0 unless neighbours are bad.
- Spike and cosmic-ray flagged 100k+ real-structure pixels on a theta scan; leave them off unless the data is
  static (cosmic-ray: n_sigma >= 8 on a long static stack).
- **Gap edges.** The local medians ignore the threshold mask's gap pixels, and pixels within `gap_guard_px`
  (default 1) of a gap need `gap_guard_factor` (default 2.0) x their neighbours to be flagged. Module-edge pixels
  respond ~1.3-2x normally (12 % of them exceed 1.25x vs 0.1 % in the interior, but only 0.04 % exceed 2x), so
  without the guard a dotted line of "bad" pixels follows every gap. Keep the guard unless you know the edge
  pixels are bad; the gap pixels must be in the threshold mask (`lower=0`) for it to find them.

## Errors

Bad input is `bad_args`; a missing image, missing calibration or empty mask is `precondition_failed` —
read the message. Dialogs the GUI would show are recorded and returned as `messages` / error text.
