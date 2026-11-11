---
name: midas-gui-mcp-solve-cell
description: Use when solving an unknown crystal cell through the midas-gui MCP server's solve_cell_* tools — loading frames, dark/background/mask/calibration/omega inputs, suggesting ingest parameters, ingest, material filter, indexing, refining and finding further domains.
---

# midas-gui MCP: Solve Cell

Prerequisite: the GUI is running with the control socket (see `midas-gui-mcp-setup`).
Every `solve_cell_run_*`, `solve_cell_suggest_parameters` and `solve_cell_run_domain_loop` call is
**async**: it returns a `job_id`; block on it with `solve_cell_wait(job_id)`. Only one stage runs at a time.
A failed precondition comes back as `precondition_failed` with a message — read it, do not retry blindly.

## 1. Inputs (per panel; `panel_id` defaults to the active panel)

1. `solve_cell_panel_load_data(path, dataset)`.
2. `solve_cell_panel_load_calibration(path)` — check the returned notes: a file that does not declare
   `im_trans` makes ingest refuse until you acknowledge it.
3. Omega: `solve_cell_panel_set_omega(start, end, step, first_idx, field)`; omega(frame) = start +
   (frame − first_idx)·step, last_idx = first_idx + round((end − start)/step). With the HDF5 omega field
   (`solve_cell_panel_list_omega_fields`) pass `field` and `auto_first_idx=True` to fill `first_idx`.
   Never leave `step` at 0 with start ≠ end.
4. Optional corrections: `solve_cell_panel_set_field(role, path, dataset, start, end, mode, scale)` for
   `dark` / `bright` (mode divide|subtract) / `background` (scale). **They compute in the background:**
   poll `solve_cell_panel_get_inputs` until each field's `pending` is false.
5. Optional detector masks: `solve_cell_panel_set_masks([{kind: file|folder, path, enabled}], mode)`;
   the reply gives `composite_masked_px` and any source that failed to load.
6. Verify with `solve_cell_panel_get_inputs`: data, fields, masks, calibration geometry, omega mapping.
   More panels: `solve_cell_add_panel`; ingest pools every fully configured panel.

## 2. Suggest the ingest parameters (do this for a new dataset)

`solve_cell_suggest_parameters()` → `solve_cell_wait`. Read the job result:
- `confidence: high` → `solve_cell_apply_suggested_parameters()`.
- `confidence: low` → no clear spot-count plateau; the blob cuts returned are the app defaults, not a
  finding. Apply only if you accept defaults, or inspect `report`/`evidence` and set the values yourself
  with `solve_cell_set_ingest_params`.
- `warnings` about diffuse scatter surviving background subtraction: spots inside that region may be
  diffuse signal; consider a mask or dropping them after ingest.
It analyses ONE panel (pass `panel_id`); pooled runs share the parameters. It never changes
split_ratio, gap bridging or the material filter.

## 3. Run the stages

1. `solve_cell_run_ingest()` (add `acknowledge_warnings=True` only after checking the omega step and
   `im_trans` are intended). Expect the spot count to be near the suggestion's `expected_spots`; far
   more means noise blobs, far fewer means a threshold or mask problem — check `solve_cell_get_log`.
2. `solve_cell_set_material_filter(...)` then `solve_cell_run_diamond_filter()` (with the filter disabled
   it passes every spot through, which is still required before indexing).
3. `solve_cell_set_index_params(method="free"|"known_structure", ...)`.

## 4. Solve and find further domains — `solve_cell_run_domain_loop`

One job: index → refine Domain 1, then repeat "index remaining spots" (each found domain is refined
inside that stage). Arguments: `max_domains` (default 5), `include_first_domain`, `fill_from_latest`
(known-structure "remaining" method only), `min_remaining_spots`. The result lists every step and
`stop_reason`:

| stop_reason | meaning / next action |
|---|---|
| `no_new_domain` | the remaining spots do not index another domain — done |
| `max_domains_reached` | raise `max_domains` if spots remain |
| `too_few_remaining_spots` | fewer spots than the stage minimum are left — done |
| `first_domain_not_found` | Domain 1 could not be indexed: revisit ingest (re-run step 2, check masks/omega) or lower `min_reflections`/raise `sigma_g` |
| `cancelled` | `solve_cell_cancel_domain_loop` — takes effect after the stage in flight |

A failed stage makes the job `failed` but the partial result (steps, domains found) is kept. The single
stages (`run_index`, `run_refine`, `run_index_remaining`) remain available for manual control.

## 5. Inspect and save

`solve_cell_get_domains`, `solve_cell_get_spots`, `solve_cell_get_state` (includes `tuning`,
`domain_loop`, per-panel fields/masks/omega), `solve_cell_save_project` (or `project_save`).
