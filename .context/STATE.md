# Project state

Last updated: 2026-10-22

## Now working on

All five pieces previously sitting uncommitted in the working tree are now
committed to `main` (the working copy ended up on `main` rather than
`solve-cell` — that branch never held any commits of its own, so nothing
was lost): the Calibrate flip-threshold bug fix (`0458bcf`), the MCP
Calibrate control-surface extension (`aaaeecc`), Solve Cell Ingest live
progress (`f1da5e7`), the agent-neutral context migration (`042bb0e`), and
the new Tools ▸ APEXA Chat… launcher (`fcd5fbb`). Not yet pushed to
`origin`. See `.context/DECISIONS.md` (2026-10-22/2026-10-21 entries) for
full design/trace on each.

## Next steps

- Push `main` to `origin` (now includes `a2acc78` max_workers wiring + the
  hdf5plugin fix + the requirements.txt `midas-defect` pin bump).
- Live-app check for Solve Cell Ingest: run a real Ingest (beamline data,
  now that the hdf5plugin bug is fixed) and confirm the Detector tab
  visibly updates per step, not just via the automated worker/tab unit
  tests already covering `_on_ingest_step`.
- Consider exposing `max_workers` in the Solve Cell tab's own UI (currently
  cfg-only) and applying the same thread-pool treatment to
  `_stage_index_known_cell`'s `_random_rotation_search` — see ROADMAP.
- Flag ROADMAP P4-1 to MIDAS maintainers (midas-defect's `perf_solve_cell`
  branch never bumped its own version number, so the requirements.txt pin
  can't disambiguate it from master by version alone).
- Consider extending the same `on_step` pattern to `diamond_filter`/
  `ab_initio`/`refine` if per-step visibility is wanted there too (not done
  — out of scope for this change).
- Live-app check for the new MCP ops: drive `calibrate.set_projection`/
  `.project`/`.run` (with `limits`/`distortion_mode`/`build_residual_corr`)/
  `.save_json`/`.save_paramstest`/`.set_working_dir` against a real running
  GUI (`MIDAS_GUI_CONTROL=1`), not just the unit tests.
- Live-app check for Tools ▸ APEXA Chat…: set Preferences ▸ Integrations ▸
  APEXA repo path and confirm the launcher actually starts APEXA's desktop
  UI wired to this GUI's control socket.
- Consult `.context/ROADMAP.md` for outstanding calibration, Solve Cell,
  Texture, and package-side work; verify older items against current code.
- For GUI changes, use the per-file Qt verification guidance in
  `.context/ARCHITECTURE.md` rather than trusting a combined suite run.

## Open questions / blockers

- Windows user `lheald` calibration import failure remains unresolved;
  awaiting direct import tracebacks and `midas_calibrate_v2` version details.
- Live beamline verification is still needed for restart/relaunch, recent
  Hydra UI changes, and real multi-panel calibration output.
- Known local-config and Qt/pyqtgraph teardown failures can obscure test
  results. Per-file runs with a clean temporary `HOME` are the reference;
  see architecture guidance. No application tests were run for this
  context-only migration.

## Recent changes

- 2026-10-22 — Fixed the hdf5plugin loading bug (import added to `midas_gui/__init__.py`) and upgraded the real env's `midas_defect`/`midas_hkls` to the `perf_solve_cell` build, resolving the `max_workers` TypeError `a2acc78` had introduced there; `requirements.txt` pin bumped with a version-ambiguity caveat (ROADMAP P4-1). Verified green per-file: `test_solve_cell_pipeline.py`, `test_solve_cell_ui.py`, `test_project.py`, `test_smoke.py`.
- 2026-10-22 — Solve-cell ingest perf benchmark (MIDAS `perf_solve_cell` vs `master`): 4.35x serial / 9.42x at 12 workers, output byte-identical; wired `max_workers` into `pipeline.py` (`a2acc78`).
- 2026-10-22 — Added Tools ▸ APEXA Chat… launcher, wired through `midas_gui_mcp` and a new Preferences ▸ Integrations APEXA-repo-path setting (`fcd5fbb`).
- 2026-10-22 — Aligned context with shared init/resume/save skills; removed legacy provider files and corrected stale branch state (`042bb0e`).
- 2026-10-22 — Solve Cell Ingest gained live per-step progress via a new `on_step` callback through `pipeline`/`SolveCellWorker`/`tab_solve_cell` (`f1da5e7`).
