---
name: midas-gui-mcp-project
description: Use when saving or loading a midas-gui project (.h5) through the midas-gui MCP server's project_* tools, e.g. "save this session", "open the project at ...", "continue from yesterday's project".
---

# midas-gui MCP: projects

A project is one `.h5` holding the GUI session (every tab's fields), recorded
calibrate/integrate attempts, the mask history and the Solve Cell record.
Tools: `project_get_info`, `project_save`, `project_open`. Same as File ▸ Open
Project and Save Project (As), without dialogs.

## Rules

- **Look first.** `project_get_info()` gives the open project (or null), whether
  there are unsaved changes, and how many attempts it records.
- **Save:** `project_save()` overwrites the open project. With no project open
  it needs a `path`. `project_save(path=...)` is Save As: a fresh file (`.h5`
  is appended). An existing file is refused unless the user said to replace it,
  then pass `overwrite=True` (a `.bak` copy is kept). `history` ("none" default,
  "latest", "all") carries the previous project's analysis history into a Save
  As; use "none" unless asked.
- **Open:** `project_open(path)` restores everything in it. Use `exclude` only
  when the user wants part left out (workspace_tabs, calib_refs,
  integrate_refs, solve_cell, mask_ref).
- **Unsaved changes block an open.** Never pass `discard_unsaved=True` on your
  own. Offer to `project_save` first, or ask the user.
- Opening restores fields and reloads path-backed images, but does not re-run
  fits or pipelines. Say so, and re-run what the user needs.
- After a save or open, check `project_get_info` and tell the user the path.
- `solve_cell_save_project` still exists; prefer `project_save`.
