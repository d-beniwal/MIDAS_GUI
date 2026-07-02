"""Pytest configuration for midas-gui.

Force Qt into offscreen mode so the GUI smoke tests run headless (CI, servers,
release.sh) with no X server / display, and silence the OpenMP duplicate-lib
abort on macOS (same workaround the MIDAS packages use).
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
