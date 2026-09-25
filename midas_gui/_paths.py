"""Runtime environment setup for midas_gui.

When midas-gui is installed as a pip package all MIDAS dependencies are
installed separately, so no sys.path manipulation is needed.  This stub is
kept so that existing ``import midas_gui._paths`` guards in the modules
continue to work unchanged.

Runtime side-effects (env vars only — must run before numpy/torch import):

* Suppress PyTorch's duplicate-OpenMP-library abort on macOS/Windows, where
  torch's bundled libiomp5 and numpy/scipy's OpenBLAS-linked libomp both load
  into the same process.
* Cap every native thread pool (OpenBLAS, Intel/LLVM OpenMP, MKL, Accelerate)
  to 1 thread. Every heavy computation in this app runs inside a QThread
  worker (see workers.py); with KMP_DUPLICATE_LIB_OK masking the duplicate-
  runtime check, those two competing multi-threaded pools racing inside a
  QThread reproducibly crashes with a native SIGBUS/SIGABRT (seen inside
  numpy.linalg.inv during calibration's HKL/ring generation, and in scipy's
  ndimage filters during seeding) — uncatchable by any Python try/except.
  Single-threading them avoids the race; ``setdefault`` lets a user override
  via their own shell environment if they want multi-threaded BLAS and are
  not hitting this.
* Disable HDF5's file locking. HDF5 tries to `flock()` every file it opens
  (h5py included), and on many NFS servers/clients that lock is never
  granted — the call doesn't fail, it hangs *indefinitely*. Confirmed live:
  Batch Integrate's Detector-view preview (``widgets.py``'s
  ``DataLoaderPanel``, then still synchronous on the GUI thread — see
  .context/DECISIONS.md, 2026-09-25) opened a multi-file VAREX HDF5 source
  over an NFS-mounted beamline share and froze the whole app with no
  recovery, not just a slow read. `HDF5_USE_FILE_LOCKING=FALSE` is the
  standard, documented workaround (h5py/HDF5 both honor it) — safe on a
  read-only/single-writer workflow like this one, where the file-corruption
  risk file locking exists to prevent doesn't apply. ``setdefault`` lets a
  user override via their own shell environment if their storage needs
  locking left on.
"""
from __future__ import annotations

import os

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
             "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_var, "1")
os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")

# Kept for backward compatibility; not meaningful in an installed package.
REPO_ROOT = None
