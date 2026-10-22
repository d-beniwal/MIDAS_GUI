# Agent instructions

## Project context

`midas-gui` — a PyQt5 desktop GUI for the MIDAS X-ray diffraction suite
(view, mask, calibrate, integrate, corrections, PDF, texture). Drives the
`midas-*` analysis backends. Entry: `midas-gui` → `midas_gui.app:main`.

**Dev history:** tracked via `git log`/commit messages, not a hand-maintained
doc. `documentation/development_history.md` and `gui_documentation.md` are
updated only when explicitly asked (e.g. before a release), never
automatically per commit.

## At the start of a task

Read `.context/STATE.md` for the current focus, next steps, blockers, and
recent changes.

Read only the relevant portions of the on-demand references below when the
task actually needs that background — do not bulk-read them:

- `.context/DECISIONS.md`   — log of decisions and *why* (newest first)
- `.context/ARCHITECTURE.md` — system shape, components, data flow, UI conventions
- `.context/DOMAIN.md`       — domain knowledge, MIDAS API specifics, terminology
- `.context/ROADMAP.md`      — open/pending work items (per-tab, package-side fixes)
- `.context/reference/midas_pdf/` — build-critical PDF knowledge stack (00–05)

## Resuming work (`/resume` / `resume-context`)

Follow the shared `resume-context` skill when available. The same portable
procedure applies when a harness does not expose that skill or slash command:

1. Read `.context/STATE.md`.
2. Read roughly the five newest entries in `.context/DECISIONS.md` (newest first).
3. Load architecture, domain, roadmap, or source files only as the task needs.
4. Briefly summarize the current focus, blockers, and next action, then continue
   the requested work. Check Git status/log before relying on branch claims.

`/resume` is the shared skills repository's compatibility command for this
procedure; command registration belongs to the agent harness, not this repo.
`AGENTS.md` and `.context/` are the canonical project memory for every agent.

## After significant work

Update `.context/STATE.md` (keep it concise, under ~1 page) and prepend a
dated entry with reasoning to `.context/DECISIONS.md`. Do not overwrite prior
decisions. "Significant work" = a milestone/feature finished, a design or
architecture decision made, a bug root-caused, or a plan/approach changed —
trivial edits, typo fixes, or pure Q&A don't need an update.
Use the shared `save-context` format: **Now working on**, **Next steps**,
**Open questions / blockers**, and **Recent changes** (three to five one-line
entries), plus `Last updated`. Move durable details to the on-demand references;
keep historical reasoning in `DECISIONS.md` and development history in Git.

## Standing rules

- Commit history is the record of recent work — see `git log`. Docs
  (`development_history.md`, `gui_documentation.md`) are updated only when
  explicitly asked, not automatically per commit.
