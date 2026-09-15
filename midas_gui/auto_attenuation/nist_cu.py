"""Cu X-ray attenuation coefficients from NIST XCOM.

Ported from pyAutoBeam's ``attenuation/nist_data.py`` (log-log
interpolation over the same public NIST XCOM table, copied verbatim into
``data/Cu_att_data.txt``) so this package has no import dependency on
pyAutoBeam itself.
"""

import importlib.resources

import numpy as np

# Cu density in g/cm^3
CU_DENSITY = 8.96

_CACHED_DATA = None


def load_cu_attenuation_data():
    """Return (energy_MeV, mu_over_rho, mu_en_over_rho) NIST XCOM arrays for Cu."""
    global _CACHED_DATA
    if _CACHED_DATA is not None:
        return _CACHED_DATA

    ref = importlib.resources.files("midas_gui.auto_attenuation.data").joinpath(
        "Cu_att_data.txt"
    )
    with importlib.resources.as_file(ref) as path:
        energy, mu_rho, mu_en_rho = [], [], []
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) < 3:
                    continue
                energy.append(float(parts[0]))
                mu_rho.append(float(parts[1]))
                mu_en_rho.append(float(parts[2]))

    _CACHED_DATA = (np.array(energy), np.array(mu_rho), np.array(mu_en_rho))
    return _CACHED_DATA


def get_cu_mass_attenuation(energy_keV):
    """Interpolate the Cu mass attenuation coefficient (cm^2/g) at *energy_keV*.

    Uses log-log interpolation on the NIST XCOM table. Raises ValueError if
    *energy_keV* is outside the tabulated range.
    """
    energy_MeV = energy_keV / 1000.0
    energies, mu_over_rho, _ = load_cu_attenuation_data()

    e_min, e_max = energies[0], energies[-1]
    if energy_MeV < e_min or energy_MeV > e_max:
        raise ValueError(
            f"Energy {energy_keV} keV ({energy_MeV} MeV) is outside the "
            f"tabulated range [{e_min * 1000:.1f}, {e_max * 1000:.1f}] keV."
        )

    # The table has duplicate energies at the Cu K-edge (8.98 keV); querying
    # at/above the edge lands on the post-edge branch via np.interp's use of
    # the last matching index.
    log_e = np.log(energies)
    log_mu = np.log(mu_over_rho)
    log_query = np.log(energy_MeV)

    log_mu_interp = np.interp(log_query, log_e, log_mu)
    return float(np.exp(log_mu_interp))


def estimate_mu_linear(energy_keV, density=CU_DENSITY):
    """Cu linear attenuation coefficient (mm^-1) at *energy_keV*.

    mu_linear [mm^-1] = (mu/rho [cm^2/g]) * (density [g/cm^3]) / 10
    """
    mu_over_rho = get_cu_mass_attenuation(energy_keV)
    return mu_over_rho * density / 10.0
