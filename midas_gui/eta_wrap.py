"""Caking an eta range that crosses the +/-180 branch cut, in one run.

``midas_calibrate_v2``'s ``pixel_to_REta`` types its output
``eta_deg: azimuth (deg, [-180, 180))``, and every binning kernel in
``midas_integrate_v2`` assigns a pixel with a bare
``floor((Eta - EtaMin) / EtaBinSize)`` and a ``0 <= bin < n_eta`` test. No
kernel wraps. (``polygon``'s ``wraparound`` is a different thing: one PIXEL
whose corners straddle the seam.)

So a request for eta 120..210 -- which is what a 1-ID-E Hydra ge1 subtends,
geometry putting it at 131.8..207.0 out to R=2200 -- asks for bins centred
at 185, 195 and 205 that no pixel can ever land in. They come back
identically zero, and the pixels that belong in them are not accumulated
anywhere: measured on ge1, 1,027,309 of 3,091,116 in-range pixels, 33.2%,
dropped with nothing said.

The fix is to bin the WHOLE CIRCLE and then take the requested bins out of
it, in the requested order. Every pixel eta lands somewhere in
``[-180, 180)``, so nothing is lost; the columns are then selected and
relabelled with the caller's own unwrapped centres. Verified bit-identical
against integrating the two halves separately on ge1's real geometry --
``120..180`` equals the first six selected columns and ``-180..-150`` the
last three -- with no all-zero bins left.

The one requirement is that the requested bin edges fall on the full-circle
grid, i.e. ``(EtaMin + 180) / EtaBinSize`` is a whole number. Otherwise the
requested bins are not a subset of any full-circle binning and selection
cannot be exact, so :func:`plan_eta` declines and says why rather than
quietly returning something off by a fraction of a bin.
"""
from __future__ import annotations

import math
from typing import NamedTuple, Optional

import numpy as np

#: The half-open interval the backend reports eta in.
ETA_LO, ETA_HI = -180.0, 180.0


class EtaPlan(NamedTuple):
    """How to bin a requested eta range.

    ``take`` is ``None`` when the request needs no help -- the overwhelmingly
    common case, and the one where everything downstream must behave exactly
    as it did before this module existed.
    """
    #: EtaMin/EtaMax to hand the backend (the request itself, or the circle).
    bin_min: float
    bin_max: float
    #: Column indices to lift out of the binned cake, in requested order.
    take: Optional[np.ndarray]
    #: Bin centres as the CALLER asked for them, unwrapped (125..205, not
    #: 125..175 then -175..-155). What the output axis should say.
    centres: np.ndarray
    #: Set when the request crosses the cut but cannot be served exactly.
    problem: Optional[str]


def _n_bins(lo: float, hi: float, step: float) -> int:
    return int(math.ceil((hi - lo) / step))


def crosses_branch_cut(eta_min: float, eta_max: float, eta_bin: float) -> bool:
    """Does any requested bin CENTRE fall outside ``[-180, 180)``?

    Centres, not edges: a range ending exactly at 180 has its last centre at
    175 and is fine, which is why the check cannot just compare EtaMax.
    """
    n = _n_bins(eta_min, eta_max, eta_bin)
    return any(not (ETA_LO <= eta_min + eta_bin * (i + 0.5) < ETA_HI)
               for i in range(max(n, 0)))


def plan_eta(eta_min: float, eta_max: float, eta_bin: float,
             tol: float = 1e-6) -> EtaPlan:
    """Decide how to bin ``[eta_min, eta_max)``; see the module docstring."""
    eta_min, eta_max, eta_bin = float(eta_min), float(eta_max), float(eta_bin)
    n = _n_bins(eta_min, eta_max, eta_bin)
    centres = eta_min + eta_bin * (np.arange(max(n, 0)) + 0.5)
    if eta_bin <= 0 or n <= 0 or not crosses_branch_cut(eta_min, eta_max,
                                                        eta_bin):
        return EtaPlan(eta_min, eta_max, None, centres, None)

    # A full turn must be a whole number of bins, and the requested grid must
    # sit on the circle's grid, or the requested bins are not a subset of it.
    n_full = (ETA_HI - ETA_LO) / eta_bin
    offset = (eta_min - ETA_LO) / eta_bin
    if (abs(n_full - round(n_full)) > tol
            or abs(offset - round(offset)) > tol):
        return EtaPlan(
            eta_min, eta_max, None, centres,
            f"eta {eta_min:g}..{eta_max:g} crosses the +/-180 wrap and its "
            f"{eta_bin:g} deg grid does not divide the circle from -180, so "
            f"the bins past the wrap cannot be recovered exactly; they will "
            f"come back empty. Use a bin size that divides 360 and an EtaMin "
            f"on that grid, or split the range by hand.")

    n_full = int(round(n_full))
    full_centres = ETA_LO + eta_bin * (np.arange(n_full) + 0.5)
    wrapped = (centres - ETA_LO) % 360.0 + ETA_LO
    take = np.array([int(np.argmin(np.abs(full_centres - w))) for w in wrapped],
                    dtype=np.int64)
    return EtaPlan(ETA_LO, ETA_HI, take, centres, None)


def apply_take(arr, take) -> np.ndarray:
    """Select eta rows from a cake shaped ``(n_eta, n_r)``.

    ``None`` passes the array straight through, untouched and not copied --
    the no-split path must stay exactly what it was.
    """
    if take is None or arr is None:
        return arr
    return np.asarray(arr)[take, :]
