# Project state

Last updated: 2026-10-22

## Now working on

Live-app verification of Solve Cell Ingest against real beamline data
surfaced two things (see `.context/DECISIONS.md` today's newest entry):
a "Nr. of CPUs" field is now exposed next to Run Ingest (was cfg-only
before), and a real, unresolved `Fatal Python error: Aborted` crash in
`midas_defect`'s `_grouped_median` (pandas groupby path) was found and
flagged — not yet fixed, suspect is the unpinned `pandas>=1.5` floor
floating to `3.0.6`. `requirements.txt`'s MIDAS pins were also re-synced to
the real env (they had drifted from ad hoc upgrades). All four of
`test_solve_cell_pipeline.py`/`test_solve_cell_ui.py`/`test_project.py`/
`test_smoke.py` pass per-file, confirmed fresh this session.

Previously: five pieces sitting uncommitted were committed to `main` (the
working copy ended up on `main` rather than `solve-cell` — that branch
never held any commits of its own, so nothing was lost): the Calibrate
flip-threshold bug fix (`0458bcf`), the MCP Calibrate control-surface
extension (`aaaeecc`), Solve Cell Ingest live progress (`f1da5e7`), the
agent-neutral context migration (`042bb0e`), the Tools ▸ APEXA Chat…
launcher (`fcd5fbb`), and the hdf5plugin fix + defect pin bump (`d9083b0`).
Not yet pushed to `origin`.

## Next steps

- Push `main` to `origin`.
- Investigate the `_grouped_median` crash (today's DECISIONS.md entry):
  try pinning `pandas` to a known-2.x version in the real env and re-running
  the same Ingest that crashed, to see if it still reproduces; if so,
  isolate a minimal repro and report upstream against `midas_defect`.
- Live-app check for Solve Cell Ingest: now exercised once against real
  beamline data (see above) — confirm the Detector tab visibly updates per
  step on a longer/multi-panel run too, not just the automated tests.
- Apply the same thread-pool treatment to `_stage_index_known_cell`'s
  `_random_rotation_search` — see ROADMAP (the Ingest side is now done,
  UI included).
- Flag ROADMAP P4-1 to MIDAS maintainers (midas-defect's `perf_solve_cell`
  branch never bumped its own version number, so the requirements.txt pin
  can't disambiguate it from master by version alone).
- Consider bringing `pyproject.toml`/`environment.yml` in sync with
  `requirements.txt`'s re-pinned MIDAS versions (flagged, not done this
  session — `pyproject.toml` is also missing the whole Solve Cell dep block).
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

- 2026-10-22 — Added a "Nr. of CPUs" field next to Solve Cell's Run Ingest button (default 1, wired to `pipeline.py`'s existing `max_workers`); re-synced `requirements.txt`'s MIDAS pins to the real env (several had drifted via ad hoc upgrades); found and flagged (not fixed) a recurring `Fatal Python error: Aborted` crash in `midas_defect._grouped_median`'s pandas-groupby path during real Ingest — see DECISIONS.md.
- 2026-10-22 — Fixed the hdf5plugin loading bug (import added to `midas_gui/__init__.py`) and upgraded the real env's `midas_defect`/`midas_hkls` to the `perf_solve_cell` build, resolving the `max_workers` TypeError `a2acc78` had introduced there; `requirements.txt` pin bumped with a version-ambiguity caveat (ROADMAP P4-1). Verified green per-file: `test_solve_cell_pipeline.py`, `test_solve_cell_ui.py`, `test_project.py`, `test_smoke.py`.
- 2026-10-22 — Solve-cell ingest perf benchmark (MIDAS `perf_solve_cell` vs `master`): 4.35x serial / 9.42x at 12 workers, output byte-identical; wired `max_workers` into `pipeline.py` (`a2acc78`).
- 2026-10-22 — Added Tools ▸ APEXA Chat… launcher, wired through `midas_gui_mcp` and a new Preferences ▸ Integrations APEXA-repo-path setting (`fcd5fbb`).
- 2026-10-22 — Aligned context with shared init/resume/save skills; removed legacy provider files and corrected stale branch state (`042bb0e`).
- 2026-10-22 — Solve Cell Ingest gained live per-step progress via a new `on_step` callback through `pipeline`/`SolveCellWorker`/`tab_solve_cell` (`f1da5e7`).
