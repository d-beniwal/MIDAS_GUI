"""Single integration point for the ``midas_pdf`` package.

The GUI's PDF tab uses the polyatomic Faber-Ziman reduction from ``midas_pdf``
(public PyPI package, pinned in environment.yml / pyproject.toml), plus the
Stage 2-3 corrections/structure-fit/Delta-PDF surface (empty-cell + Paalman-
Pings absorption, detector efficiency, differentiable multiple scattering,
fluorescence diagnostic, CIF-driven small-box structure refinement, Delta-PDF
significance testing). This module just imports it and re-exports the symbols
the GUI needs, after making sure ``KMP_DUPLICATE_LIB_OK`` is set before torch
loads (macOS OpenMP-duplicate abort otherwise).

Deliberately NOT re-exported (out of scope, see .context/ROADMAP.md): the
Bayesian SVI/NUTS posterior path (``midas_pdf.bayesian_refine``), RMC big-box
refinement, and the non-differentiable Monte-Carlo multiple-scattering
variants (``multiple_scattering_mc*``) — the GUI uses the differentiable
transport path (``slab_transport_ms`` / ``ms_background_on_grid``) instead.
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
    # Stage 2-3: absorption / detector-efficiency corrections
    apply_detector_efficiency,
    detector_efficiency,
    flat_plate_transmission,
    linear_attenuation_um,
    # Stage 2-3: differentiable multiple scattering (cylinder transport)
    cylinder_effective_tau,
    slab_transport_ms,
    ms_background_on_grid,
    # Stage 2-3: fluorescence diagnostic
    expected_fluorescence,
    # Stage 2-3: CIF-driven small-box structure refinement (PDFfit-style)
    build_pair_list,
    pdffit_gr,
    refine_structure,
    # Stage 2-3: Delta-PDF significance testing
    delta_pdf,
    significant_mask,
)
from midas_pdf.corrections import (
    paalman_pings_cylinder_in_cylinder,
    paalman_pings_cell_only,
)
from midas_pdf.cif import read_cif_to_crystal
from midas_pdf.fluorescence import fluorescence_report_sample_and_container
from midas_hkls import Atom, Crystal, Lattice, SpaceGroup

PDF_VERSION = getattr(midas_pdf, "__version__", "unknown")

__all__ = [
    "midas_pdf", "PDF_VERSION",
    "Composition", "i_of_q_to_Gr", "faber_ziman_S", "refine_normalization",
    "RefineResult", "structure_function_F", "pair_distribution_g",
    "total_correlation_T", "radial_distribution_R",
    "apply_detector_efficiency", "detector_efficiency",
    "flat_plate_transmission", "linear_attenuation_um",
    "cylinder_effective_tau", "slab_transport_ms", "ms_background_on_grid",
    "expected_fluorescence", "fluorescence_report_sample_and_container",
    "build_pair_list", "pdffit_gr", "refine_structure",
    "delta_pdf", "significant_mask",
    "paalman_pings_cylinder_in_cylinder", "paalman_pings_cell_only",
    "read_cif_to_crystal",
    "Atom", "Crystal", "Lattice", "SpaceGroup",
]
