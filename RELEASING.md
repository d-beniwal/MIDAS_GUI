# Releasing midas-gui

`midas-gui` follows the same release workflow as the other MIDAS sub-packages
(`packages/midas_*`): a single `release.sh` bumps the version in lock-step across
`pyproject.toml` and `midas_gui/__init__.py`, runs the test suite headless, builds
the sdist + wheel, and (optionally) tags + publishes.

## When to bump

| Change | Bump |
|---|---|
| Bug fix / small UI tweak | patch (`1.0.0` → `1.0.1`) |
| New feature / tab capability | minor (`1.0.0` → `1.1.0`) |
| Backwards-incompatible change (deps, entry point, layout) | major (`1.x.y` → `2.0.0`) |

## Quick reference

```bash
cd midas-gui
./release.sh <new_version> [--publish | --dry-run]
```

Three modes:

- **prepare** (default) — bump, test, build into `dist/`, commit, tag; prints the
  manual push/publish commands.
- **`--dry-run`** — bump, test, build only; no commit or tag (revert with
  `git checkout -- pyproject.toml midas_gui/__init__.py`).
- **`--publish`** — everything, then `git push origin main --follow-tags`, create a
  GitHub release (`midas-gui-v<version>`), and (if not automated in CI) `twine upload`.

The tests run with `QT_QPA_PLATFORM=offscreen`, so no display is required.

## Prerequisites

- On `main` with a clean working tree.
- `python -m build`, `twine`, and `gh` (GitHub CLI) available for `--publish`.
- PyPI credentials / trusted-publishing configured for the `midas-gui` project.

## Making midas-gui part of midas-suite

`midas-gui` is intentionally **not** yet referenced by the `midas-suite`
meta-package. Once `midas-gui` is published to PyPI, wire it in from the MIDAS
repo (`packages/midas_suite/`) as an **optional extra** so PyQt5 is not forced
onto headless / server installs:

```toml
# packages/midas_suite/pyproject.toml  →  [project.optional-dependencies]
gui = ["midas-gui>=1.0.0"]
```

Then `pip install midas-suite[gui]` installs the full pipeline plus the GUI.
Bump `midas-suite` (minor) and release it with its own `release.sh`.
