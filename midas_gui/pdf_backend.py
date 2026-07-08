"""Single integration point for the vendored ``midas_pdf`` package.

The GUI's PDF tab uses the polyatomic Faber-Ziman reduction from ``midas_pdf``.
That package is not yet pip-installable here, and the installed ``midas_hkls``
(0.4.1) is missing the ``midas_hkls.absorption`` submodule that ``midas_pdf``
imports at module load.  This module

1. installs a small ``midas_hkls.absorption`` compatibility shim *only when* the
   real submodule is absent (0.4.1),
2. puts the vendored copy (``midas_gui/_vendor``) on ``sys.path``, and
3. imports ``midas_pdf`` and re-exports the symbols the GUI needs.

Flip to the pip package later by editing only this file: once
``midas-hkls>=0.5.0`` (with ``absorption``) is installed the shim no-ops, and
once ``midas-pdf`` is installed the ``_vendor`` path insert becomes redundant.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import types
from pathlib import Path

import midas_gui._paths  # noqa: F401  (sets KMP_DUPLICATE_LIB_OK etc. before torch)

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")


# ─────────────────────────────────────────────────────────────────────────────
#  1. midas_hkls.absorption compatibility shim
# ─────────────────────────────────────────────────────────────────────────────
# Standard elemental densities (g/cm³) for the flat-plate / Paalman-Pings
# self-absorption helpers (Stage-2, not used by the core reduction or refine).
_ELEMENT_DENSITY_G_CM3 = {
    "C": 2.267, "Al": 2.699, "Si": 2.329, "Ti": 4.506, "V": 6.110,
    "Cr": 7.190, "Mn": 7.210, "Fe": 7.874, "Co": 8.900, "Ni": 8.908,
    "Cu": 8.960, "Zn": 7.140, "Zr": 6.520, "Mo": 10.280, "Ag": 10.490,
    "Sn": 7.265, "W": 19.250, "Pt": 21.450, "Au": 19.300, "Pb": 11.340,
    "Ce": 6.770,
}


def _build_absorption_shim() -> types.ModuleType:
    """Construct a ``midas_hkls.absorption`` stand-in backed by real data where
    possible and informative stubs for the deferred Stage-2 pieces."""
    mod = types.ModuleType("midas_hkls.absorption")
    mod.__doc__ = (
        "Compatibility shim installed by midas_gui.pdf_backend for midas_hkls "
        "< 0.5.0 (which lacks the real absorption submodule). Core PDF reduction "
        "and refine_normalization do not need this; only Stage-2 self-absorption "
        "/ multiple-scattering do."
    )

    def Z_for(element: str) -> int:
        # Atomic number — delegate to the real anomalous table.
        from midas_hkls.anomalous import Z_for as _Z
        return _Z(element)

    def atomic_mass(element: str) -> float:
        # g/mol — reuse the vendored table (lazy import avoids a load-time cycle).
        from midas_pdf.placzek import _ATOMIC_MASS_U
        base = "".join(ch for ch in element if ch.isalpha())
        try:
            return float(_ATOMIC_MASS_U[base])
        except KeyError as e:
            raise KeyError(f"atomic_mass: unknown element {element!r}") from e

    def element_density(element: str) -> float:
        # g/cm³ — small standard table for Stage-2 self-absorption only.
        base = "".join(ch for ch in element if ch.isalpha())
        try:
            return float(_ELEMENT_DENSITY_G_CM3[base])
        except KeyError as e:
            raise NotImplementedError(
                f"element_density({element!r}) unavailable in the midas_hkls "
                f"0.4.x shim (Stage-2 absorption). Install midas-hkls>=0.5.0 or "
                f"pass an explicit density."
            ) from e

    def mass_attenuation_coefficient(element: str, wavelength_A: float) -> float:
        raise NotImplementedError(
            "mass_attenuation_coefficient is not available in the midas_hkls "
            "0.4.x shim. Self-absorption / multiple-scattering corrections "
            "(Stage-2) require midas-hkls>=0.5.0."
        )

    mod.Z_for = Z_for
    mod.atomic_mass = atomic_mass
    mod.element_density = element_density
    mod.mass_attenuation_coefficient = mass_attenuation_coefficient
    return mod


def _ensure_absorption_shim() -> bool:
    """Register the shim iff the real submodule is missing. Returns True if a
    shim was installed, False if the real ``midas_hkls.absorption`` exists."""
    import midas_hkls
    if importlib.util.find_spec("midas_hkls.absorption") is not None:
        return False
    shim = _build_absorption_shim()
    sys.modules["midas_hkls.absorption"] = shim
    setattr(midas_hkls, "absorption", shim)
    return True


# ─────────────────────────────────────────────────────────────────────────────
#  2. + 3. path insert and import
# ─────────────────────────────────────────────────────────────────────────────
_VENDOR_DIR = Path(__file__).resolve().parent / "_vendor"


def _import_midas_pdf():
    """Import ``midas_pdf`` — installed if present, else the vendored copy."""
    installed = importlib.util.find_spec("midas_pdf") is not None
    using_vendored = False
    if not installed:
        vendor = str(_VENDOR_DIR)
        if vendor not in sys.path:
            sys.path.insert(0, vendor)
        using_vendored = True
    return importlib.import_module("midas_pdf"), using_vendored


SHIM_INSTALLED = _ensure_absorption_shim()
midas_pdf, USING_VENDORED = _import_midas_pdf()
PDF_VERSION = getattr(midas_pdf, "__version__", "unknown")

# Re-export the surface the GUI uses.
from midas_pdf import (  # noqa: E402
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

__all__ = [
    "midas_pdf", "PDF_VERSION", "USING_VENDORED", "SHIM_INSTALLED",
    "Composition", "i_of_q_to_Gr", "faber_ziman_S", "refine_normalization",
    "RefineResult", "structure_function_F", "pair_distribution_g",
    "total_correlation_T", "radial_distribution_R",
]
