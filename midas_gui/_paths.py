"""Runtime environment setup for midas_gui.

When midas-gui is installed as a pip package all MIDAS dependencies are
installed separately, so no sys.path manipulation is needed.  This stub is
kept so that existing ``import midas_gui._paths`` guards in the modules
continue to work unchanged.

The only runtime side-effect is suppressing PyTorch's duplicate-library
warning on macOS/Windows.
"""
from __future__ import annotations

import os

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

# Kept for backward compatibility; not meaningful in an installed package.
REPO_ROOT = None
