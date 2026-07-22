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
"""
from __future__ import annotations

import os

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
for _var in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS",
             "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_var, "1")

# Kept for backward compatibility; not meaningful in an installed package.
REPO_ROOT = None
