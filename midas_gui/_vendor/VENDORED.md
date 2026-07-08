# Vendored third-party code

## `midas_pdf/` — polyatomic total-scattering PDF reduction

- **Upstream version:** `0.1.0` (`midas_pdf.__version__`).
- **Source:** copied from
  `/Users/dbeniwal/ANL-research/midas_pdf_src/midas_pdf/midas_pdf/`
  (the inner package directory), caches excluded.
- **Imported as:** top-level `midas_pdf`, via `midas_gui/pdf_backend.py`, which
  puts this `_vendor/` directory on `sys.path` and re-exports the symbols the GUI
  uses. Nothing here is modified — compatibility with the installed
  `midas_hkls` is handled by a shim installed in `pdf_backend.py`.

### Why it is vendored (and how to remove it)

`midas_pdf` is not yet pip-installable in this environment, and the installed
`midas_hkls` (0.4.1) is missing the `midas_hkls.absorption` submodule that
`midas_pdf` imports at module load. `pdf_backend.py` installs a small
`midas_hkls.absorption` compatibility shim (only when the real submodule is
absent) so the unmodified package imports cleanly.

**To retire this vendored copy:** once `midas-pdf` is published and
`midas-hkls>=0.5.0` (which ships `absorption`) is a dependency, delete this
directory and change `pdf_backend.py` to `import midas_pdf` from the installed
package (the shim then no-ops because the real submodule exists).
