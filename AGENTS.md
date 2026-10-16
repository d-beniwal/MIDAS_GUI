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

## After significant work

Update `.context/STATE.md` (keep it concise, under ~1 page) and prepend a
dated entry with reasoning to `.context/DECISIONS.md`. Do not overwrite prior
decisions. "Significant work" = a milestone/feature finished, a design or
architecture decision made, a bug root-caused, or a plan/approach changed —
trivial edits, typo fixes, or pure Q&A don't need an update.

## Standing rules

- Commit history is the record of recent work — see `git log`. Docs
  (`development_history.md`, `gui_documentation.md`) are updated only when
  explicitly asked, not automatically per commit.
