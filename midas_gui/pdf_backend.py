"""Single integration point for the ``midas_pdf`` package.

The GUI's PDF tab uses the polyatomic Faber-Ziman reduction from ``midas_pdf``
(public PyPI package, pinned in environment.yml / pyproject.toml). This module
just imports it and re-exports the symbols the GUI needs, after making sure
``KMP_DUPLICATE_LIB_OK`` is set before torch loads (macOS OpenMP-duplicate
abort otherwise).
"""
from __future__ import annotations

import os

import midas_gui._paths  # noqa: F401  (sets KMP_DUPLICATE_LIB_OK etc. before torch)

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import midas_pdf
from midas_pdf import (
    Composition,
    i_of_q_to_Gr,
    faber_ziman_S,
    refine_normalization,
    RefineResult,
    structure_function_F,
    pair_distribution_g,
    total_correlation_T,
    radial_distribution_R,
)

PDF_VERSION = getattr(midas_pdf, "__version__", "unknown")

__all__ = [
    "midas_pdf", "PDF_VERSION",
    "Composition", "i_of_q_to_Gr", "faber_ziman_S", "refine_normalization",
    "RefineResult", "structure_function_F", "pair_distribution_g",
    "total_correlation_T", "radial_distribution_R",
]
