"""Single-peak pseudo-Voigt fitting with MIDAS's LM solver.

Produces a CSV that ``peak_fit_data.parse_peak_fit_csv`` reads unchanged,
so a MIDAS fit and a GSAS-II sequential fit of the same scan can be
dropped into the same Data Explorer view and compared point by point.

This exists because a fitted peak position is a *model's* answer, not a
measurement: two codes fitting the same lineouts can disagree, and when a
2theta map shows structure at the millidegree level the only way to know
whether the structure is in the sample or in the refinement is to fit it
twice with independent machinery. ``midas_peakfit.lm_generic`` is that
independent machinery -- batched Levenberg-Marquardt with sigmoid-box
bounds, already installed as part of the MIDAS backend this app wraps.

The profile is GSAS's PSVOIGT, in GSAS's units
-------------------------------------------------------------------
Matching the convention is what makes the comparison meaningful, so it is
reproduced exactly rather than approximated:

  * ``sig`` is the Gaussian VARIANCE in centidegrees**2
  * ``gam`` is the Lorentzian FWHM in centidegrees
  * the offset ``2theta - pos`` is converted to centidegrees
  * Gaussian and Lorentzian are combined by Thompson-Cox-Hastings mixing
    and the result is normalised to unit area, so ``int`` is an
    integrated intensity rather than a peak height

Verified against a real GSAS-II fit before any of this was written: with
the published row's parameters substituted into the function below, the
model peak height came out within 1% of the measured lineout. That check
is kept as ``tests/test_peak_fit_midas.py::
test_the_profile_reproduces_a_published_gsas_ii_peak_height``.

What is NOT comparable, and why
-------------------------------
``Rwp``, ``GOF`` and every ``esd-*`` column depend on the WEIGHTS, and the
weights are not a property of the data -- GSAS-II takes them from the ESD
array stored in its project file, which this code has no access to. Here
the weights are Poisson, ``w = 1 / max(y, 1)``, which is stated in the
CSV's companion ``.meta.json`` rather than left for someone to infer.

So: ``pos``/``int``/``sig``/``gam`` are directly comparable between the two
codes because they parameterise the same function. ``Rwp``/``GOF``/esds
are each self-consistent within one file and must not be compared across
files. Reading a MIDAS GOF of 0.4 as "20x better than GSAS-II's 8.8" is
the mistake this paragraph exists to prevent.

No Qt, no matplotlib. Keep it that way -- the CLI and the tests both rely
on being able to import this in a bare interpreter.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import numpy as np

#: Order of the fitted parameters everywhere in this module. It is also
#: the order of the columns in the CSV, which is GSAS-II's order.
PARAM_NAMES = ("pos", "int", "sig", "gam", "Back;0", "Back;1")
N_PARAM = len(PARAM_NAMES)

#: Degrees -> centidegrees. The profile's internal unit, per GSAS.
CD = 100.0


# ───────────────────────────────────────────────────────── the profile ──
def tch_fwhm_eta(sig, gam, xp=np):
    """Thompson-Cox-Hastings: (Gaussian variance, Lorentzian FWHM) -> the
    pseudo-Voigt's total FWHM and Lorentzian mixing fraction.

    ``sig`` in centidegrees**2, ``gam`` and the returned FWHM in
    centidegrees. This is the approximation GSAS's PSVOIGT uses; the
    coefficients are theirs and are not tunable.
    """
    g = xp.sqrt(8.0 * float(np.log(2.0)) * sig)
    fw = (g**5
          + 2.69269 * g**4 * gam
          + 2.42843 * g**3 * gam**2
          + 4.47163 * g**2 * gam**3
          + 0.07842 * g * gam**4
          + gam**5) ** 0.2
    q = gam / fw
    eta = 1.36603 * q - 0.47719 * q**2 + 0.1116 * q**3
    return fw, eta


def pseudo_voigt(dx_cd, sig, gam, xp=np):
    """Unit-area pseudo-Voigt at offsets ``dx_cd`` (centidegrees).

    Works with numpy or torch -- pass the module as ``xp``. Both are used:
    torch inside the solver, numpy in the seeding and in the tests.
    """
    fw, eta = tch_fwhm_eta(sig, gam, xp)
    lor = (2.0 / (np.pi * fw)) / (1.0 + 4.0 * (dx_cd / fw) ** 2)
    sg = fw / (2.0 * float(np.sqrt(2.0 * np.log(2.0))))
    gau = xp.exp(-0.5 * (dx_cd / sg) ** 2) / (sg * float(np.sqrt(2.0 * np.pi)))
    return eta * lor + (1.0 - eta) * gau


def background_basis(x_deg):
    """The linear term of a Chebyshev background over the fit window.

    ``Back;0`` multiplies 1 and ``Back;1`` multiplies ``t``, where ``t``
    runs -1 to +1 across the window. GSAS-II's convention, kept so the two
    CSVs' background columns mean the same thing -- though only when the
    windows match, which is why the window is recorded in the sidecar.
    """
    x0, x1 = float(x_deg[0]), float(x_deg[-1])
    span = (x1 - x0) or 1.0
    return 2.0 * (np.asarray(x_deg, dtype=np.float64) - x0) / span - 1.0


def model(params, x_deg, t=None, xp=np):
    """Evaluate the full model. ``params`` is [..., 6] in PARAM_NAMES order."""
    if t is None:
        t = background_basis(x_deg)
    pos = params[..., 0:1]
    inten = params[..., 1:2]
    sig = params[..., 2:3]
    gam = params[..., 3:4]
    b0 = params[..., 4:5]
    b1 = params[..., 5:6]
    dx = (x_deg - pos) * CD
    return b0 + b1 * t + inten * pseudo_voigt(dx, sig, gam, xp)


# ──────────────────────────────────────────────────────────── seeding ──
def seed_params(x_deg, Y):
    """Per-lineout starting values, from the data rather than a guess.

    ``Y`` is [B, M]; returns [B, 6].

    Two details here were each worth a failed round-trip:

      * The background is taken from the 10th PERCENTILE of each half of
        the window, not from a straight line through the edge points. At
        a window of +-0.065 deg the peak's own Lorentzian tail is still
        ~3 counts at the edge, so an edge fit reads the tail as
        background and biases every other seed.
      * The width comes from counting points above half maximum, not
        from the second moment. The second moment of a 0.018 deg peak
        inside a 0.13 deg window is dominated by the window, which seeded
        the FWHM eleven times too wide and sent the solver to a local
        minimum that still reported convergence.
    """
    x = np.asarray(x_deg, dtype=np.float64)
    Y = np.atleast_2d(np.asarray(Y, dtype=np.float64))
    B, M = Y.shape
    step = float(np.mean(np.diff(x))) if M > 1 else 1e-3
    h = M // 2

    # robust straight-line background through the two half-window floors
    bl = np.percentile(Y[:, :h], 10.0, axis=1)
    br = np.percentile(Y[:, h:], 10.0, axis=1)
    xl, xr = x[:h].mean(), x[h:].mean()
    slope = (br - bl) / (xr - xl)
    base = bl[:, None] + slope[:, None] * (x[None, :] - xl)

    P = np.clip(Y - base, 0.0, None)
    peak = P.max(axis=1)
    peak_safe = np.where(peak > 0, peak, 1.0)

    # position: centroid of the points in the top 80% of the peak, so a
    # sloping residual background cannot drag it
    core = np.where(P >= 0.2 * peak_safe[:, None], P, 0.0)
    cw = core.sum(axis=1)
    cw_safe = np.where(cw > 0, cw, 1.0)
    cen = (core * x).sum(axis=1) / cw_safe

    # width: how many points clear half maximum
    n_half = (P >= 0.5 * peak_safe[:, None]).sum(axis=1)
    fwhm_cd = np.clip(np.maximum(n_half, 1.0) * step, 2.0 * step, None) * CD

    tot = P.sum(axis=1)
    bad = (peak <= 0) | (tot <= 0) | ~np.isfinite(cen)
    cen = np.where(bad, 0.5 * (x[0] + x[-1]), cen)
    fwhm_cd = np.where(bad, 3.0 * step * CD, fwhm_cd)

    # Split the seed width 70/30 Gaussian/Lorentzian and scale up by the
    # ~0.87 that TCH mixing of that split contracts it by, so the seeded
    # profile really is the measured width.
    g_frac, l_frac = 0.70 / 0.87, 0.30 / 0.87

    out = np.empty((B, N_PARAM))
    out[:, 0] = cen
    out[:, 1] = np.clip(tot * step * CD, 1e-3, None)      # area, centidegrees
    out[:, 2] = (g_frac * fwhm_cd / 2.3548) ** 2
    out[:, 3] = l_frac * fwhm_cd
    out[:, 4] = bl + slope * (0.5 * (x[0] + x[-1]) - xl)  # value at centre
    out[:, 5] = 0.5 * slope * (x[-1] - x[0])              # half-rise
    return out


def seed_bounds(x_deg, seeds, pos_window=None):
    """Hard box bounds for the sigmoid reparameterisation.

    Deliberately SEED-RELATIVE for the width and intensity. A fixed box
    like ``sig in [1e-4, 5000]`` is not merely loose: under the sigmoid
    reparam the solver's step size in a parameter scales with the box
    width, so an absurd box makes every step in that parameter absurd
    too, and the round-trip test failed exactly that way. A factor of 30
    either side of a data-derived seed is wide enough to be honest and
    narrow enough to be conditioned.

    Position keeps an absolute box, because "the peak is somewhere in the
    window" is a real prior and a lineout with no peak should pin against
    it visibly rather than drift somewhere plausible.
    """
    x0, x1 = float(x_deg[0]), float(x_deg[-1])
    half = pos_window if pos_window is not None else 0.4 * (x1 - x0)
    B = seeds.shape[0]
    lo = np.empty((B, N_PARAM))
    hi = np.empty((B, N_PARAM))
    mid = 0.5 * (x0 + x1)
    lo[:, 0] = max(x0, mid - half)
    hi[:, 0] = min(x1, mid + half)
    for k, f in ((1, 50.0), (2, 30.0), (3, 30.0)):
        base = np.maximum(np.abs(seeds[:, k]), 1e-6)
        lo[:, k] = base / f
        hi[:, k] = base * f
    lo[:, 1] = 0.0
    amp = np.maximum(np.abs(seeds[:, 4]), 1.0) * 10.0 + 50.0
    lo[:, 4], hi[:, 4] = -amp, amp
    lo[:, 5], hi[:, 5] = -amp, amp
    # Keep every seed strictly inside its box -- x_to_u clamps, but a seed
    # sitting on a bound starts the solver in a saturated sigmoid.
    span = hi - lo
    return lo, hi, np.clip(seeds, lo + 1e-6 * span, hi - 1e-6 * span)


# ──────────────────────────────────────────────────────────── fitting ──
@dataclass
class FitResult:
    """One batch of fitted lineouts. Every array is length B."""
    params: np.ndarray                 # [B, 6]
    esds: np.ndarray                   # [B, 6]
    rwp: np.ndarray
    gof: np.ndarray
    rc: np.ndarray                     # 0 converged, 1 max-iter, 2 lambda
    n_points: int = 0
    extras: dict = field(default_factory=dict)


def fit_lineouts(x_deg, Y, *, max_iter=120, pos_window=None, device="cpu",
                 weights=None):
    """Fit one pseudo-Voigt + linear background to every row of ``Y``.

    ``x_deg`` is the shared 2theta grid [M]; ``Y`` is [B, M]. All B rows
    are solved simultaneously by ``midas_peakfit.lm_generic`` -- the whole
    reason this is affordable at 29k lineouts.

    ``weights`` defaults to Poisson ``1 / max(y, 1)``. It is a parameter
    only so a caller who knows better can say so; changing it changes
    Rwp, GOF and every esd, and nothing else.
    """
    import torch
    from midas_peakfit.lm_generic import GenericLMConfig, lm_solve_generic
    from midas_peakfit.reparam import u_to_x

    x = np.asarray(x_deg, dtype=np.float64)
    Y = np.atleast_2d(np.asarray(Y, dtype=np.float64))
    B, M = Y.shape
    if M < N_PARAM + 2:
        raise ValueError(f"{M} points is too few to fit {N_PARAM} parameters")

    seeds = seed_params(x, Y)
    lo_np, hi_np, seeds = seed_bounds(x, seeds, pos_window)
    W = (1.0 / np.maximum(Y, 1.0)) if weights is None else np.broadcast_to(
        np.asarray(weights, dtype=np.float64), Y.shape)
    sw = np.sqrt(W)

    dev = torch.device(device)
    tx = torch.as_tensor(x, dtype=torch.float64, device=dev)
    tt = torch.as_tensor(background_basis(x), dtype=torch.float64, device=dev)
    tY = torch.as_tensor(Y, dtype=torch.float64, device=dev)
    tsw = torch.as_tensor(sw, dtype=torch.float64, device=dev)
    tlo = torch.as_tensor(lo_np, dtype=torch.float64, device=dev)
    thi = torch.as_tensor(hi_np, dtype=torch.float64, device=dev)
    tx0 = torch.as_tensor(seeds, dtype=torch.float64, device=dev)

    def _resid_u(u, lo, hi):
        p = u_to_x(u, lo, hi)
        return tsw * (tY - model(p, tx, tt, xp=torch))

    # Analytic-free but vectorised: jacrev over one row, vmap over the
    # batch. The solver's own fallback does M backward passes PER
    # ITERATION (M is ~60 here), which is the difference between seconds
    # and an afternoon.
    from torch.func import jacrev, vmap

    def _one(u_row, lo_row, hi_row, y_row, sw_row):
        p = u_to_x(u_row, lo_row, hi_row)
        return sw_row * (y_row - model(p, tx, tt, xp=torch))

    _jac = vmap(jacrev(_one, argnums=0), in_dims=(0, 0, 0, 0, 0))

    def _resjac(u, lo, hi):
        return _resid_u(u, lo, hi), _jac(u, lo, hi, tY, tsw)

    cfg = GenericLMConfig(max_iter=int(max_iter), matmul_precision="fp64")
    x_fin, _cost, rc = lm_solve_generic(tx0, tlo, thi, _resid_u,
                                        jacobian_fn=_resjac, config=cfg)

    P = x_fin.detach().cpu().numpy()
    yc = model(P, x)
    resid = Y - yc
    chi2 = (W * resid**2).sum(axis=1)
    dof = max(M - N_PARAM, 1)
    rwp = 100.0 * np.sqrt(chi2 / np.maximum((W * Y**2).sum(axis=1), 1e-30))
    gof = np.sqrt(chi2 / dof)

    # esds from the weighted normal matrix at the solution, in BOUNDED
    # space -- the u-space Jacobian the solver used is the wrong basis to
    # quote an uncertainty in.
    esds = _param_esds(x, P, sw, chi2 / dof)
    return FitResult(params=P, esds=esds, rwp=rwp, gof=gof,
                     rc=rc.detach().cpu().numpy().astype(int), n_points=M)


def _param_esds(x, P, sw, chi2_red):
    """sqrt(diag( s^2 (J^T W J)^-1 )), J in bounded-parameter space.

    Singular or near-singular normal matrices (a lineout with no peak)
    give NaN rather than a number nobody should trust.
    """
    B = P.shape[0]
    t = background_basis(x)
    J = np.empty((B, x.size, N_PARAM))
    # Central differences: the profile's sig/gam derivatives are ugly
    # enough analytically that a numerical Jacobian is the honest choice
    # at this cost (6 extra model evaluations per batch).
    for k in range(N_PARAM):
        h = np.maximum(np.abs(P[:, k]) * 1e-6, 1e-9)
        Pp = P.copy(); Pp[:, k] += h
        Pm = P.copy(); Pm[:, k] -= h
        J[:, :, k] = (model(Pp, x, t) - model(Pm, x, t)) / (2.0 * h)[:, None]
    Jw = J * sw[:, :, None]
    N = np.einsum('bmi,bmj->bij', Jw, Jw)
    out = np.full((B, N_PARAM), np.nan)
    for b in range(B):
        try:
            C = np.linalg.inv(N[b]) * chi2_red[b]
            d = np.diag(C)
            out[b] = np.where(d > 0, np.sqrt(np.abs(d)), np.nan)
        except np.linalg.LinAlgError:
            pass
    return out


# ───────────────────────────────────────────────────────── CSV output ──
CSV_HEADER = ["name", "No.", "Use", "Rwp", "GOF",
              "Back;0", "esd-Back;0", "Back;1", "esd-Back;1",
              "gam0", "esd-gam0", "int0", "esd-int0",
              "pos0", "esd-pos0", "sig0", "esd-sig0"]


def row_name(source, img, omega, eta):
    """The ``name`` column, in the shape ``parse_peak_fit_csv`` expects.

    ``img`` restarts at 0 in every file, as GSAS-II writes it -- the
    readers already know that and reconstruct a global index from the file
    number, so deviating here would break them for no gain.
    """
    return f"{source} img={int(img)} omega={float(omega):.1f} eta={float(eta):.1f}"


def write_peak_fit_csv(path, rows, meta=None):
    """Write the CSV, and a ``.meta.json`` beside it.

    ``rows`` is an iterable of dicts with keys ``name``, ``use``, ``rwp``,
    ``gof``, ``params`` [6] and ``esds`` [6].

    The sidecar is not decoration: a peak-fit CSV without its fit window
    and weighting cannot be compared with another one, and this format has
    nowhere to put them.
    """
    import csv
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_MINIMAL)
        w.writerow(CSV_HEADER)
        for i, r in enumerate(rows):
            p, e = r["params"], r["esds"]
            w.writerow([r["name"], i, bool(r.get("use", True)),
                        r["rwp"], r["gof"],
                        p[4], e[4], p[5], e[5],
                        p[3], e[3], p[1], e[1],
                        p[0], e[0], p[2], e[2]])
    if meta is not None:
        with open(os.path.splitext(path)[0] + ".meta.json", "w") as fh:
            json.dump(meta, fh, indent=2, sort_keys=True, default=str)
    return path
