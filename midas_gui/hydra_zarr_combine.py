"""Combine the four Hydra panels' caked zarrs into one, concatenated on eta.

A port of ``mpe_wf_saxs_waxs/combine_hydra_zarr.py`` into this app, so a
Hydra Batch Integrate run can produce the merged archive itself rather than
leaving the user to run a second tool by hand. The LAYOUT is what downstream
code reads, so it is reproduced exactly:

  ``IntegrationResult/<key>``   axis=1   (nR, nEta) -> (nR, sum nEta)
  ``OmegaSumFrame/<key>``       axis=1   (nR, nEta) -> (nR, sum nEta)
  ``SumFrames``                 axis=1   (nR, nEta) -> (nR, sum nEta)
  ``REtaMap``                   axis=2   (C, nR, nEta) -> (C, nR, sum nEta)

...then the eta axis is put in ascending order, in ``[-180, 180)``. See
:func:`plan_eta_order` for why, and for the cases it declines.
  ``Omegas``                    copied from panel 0 (validated identical)
  ``InstrumentParameters/<key>`` stacked into a length-N array, one entry per
                                panel, order recorded in ``.attrs['panels']``
  ``instrument/``, ``measurement/``, ``active_instrument/``
                                copied verbatim from panel 0
  root attrs                    panel 0's, plus ``combined_panels``
  anything else                 carried by ``_carry_unhandled``: an array
                                whose last axis is the panel's eta width is
                                concatenated on it, otherwise taken from
                                panel 0 -- and NAMED through ``warn``

The last line is there because the first version of this file reproduced a
hardcoded group list and dropped everything else without a word.
``SumFrames`` -- an (nR, nEta) caked array, i.e. primary data -- and
``active_instrument/`` were both missing from every archive it wrote. A
list that silently loses whatever the next MIDAS release adds is the bug;
the two names it was missing were only the symptom.

``Distance`` legitimately differs between hydra panels, which is why
InstrumentParameters values are stacked rather than required to agree -- only
the KEY SET has to match. Everything else is validated to agree before a byte
is written, so a mismatched set fails with a named reason instead of
producing a quietly wrong archive.

``IntegrationResult`` is optional: MIDAS 11.0 omits it when per-frame results
are not saved individually. It must be absent from ALL panels or present in
all -- a partial set is a mistake worth stopping for.

Staged through a DirectoryStore and zipped once at the end: zarr rewrites
``.zattrs`` as attributes are updated, and ZipFile cannot replace an entry in
place, so writing a ZipStore directly emits duplicate-entry warnings.
"""
from __future__ import annotations

import datetime
import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

import numpy as np

#: Floor for the omega agreement test, used when the step cannot be
#: measured (a single-frame scan has no step).
OMEGA_TOL_DEG = 1e-3


def _omega_tolerance(om) -> float:
    """How far two panels' omegas may differ and still be one rotation.

    Scaled to the omega STEP, not a fixed angle. The panels are
    synchronised frames of one stage, but each reads omega from its own
    file's measured channel, and a real set showed ge2 disagreeing with
    ge1/ge3/ge4 by up to 0.0321 deg on a 0.064 deg step -- half a step, on
    one frame out of 64, with a mean of 0.0005. That is an encoder reading,
    not a different scan, and a fixed 1e-3 deg tolerance rejected the whole
    four-panel set over it.

    One step is the meaningful threshold: less than that and the panels
    describe the same frames; a step or more and they are offset by a
    frame, which IS a real problem and still fails.
    """
    om = np.asarray(om, dtype=float)
    if om.size < 2:
        return OMEGA_TOL_DEG
    steps = np.abs(np.diff(om))
    steps = steps[np.isfinite(steps) & (steps > 0)]
    return max(OMEGA_TOL_DEG, float(np.median(steps))) if steps.size \
        else OMEGA_TOL_DEG

#: Groups copied across untouched from the first panel.
_VERBATIM_GROUPS = ("instrument", "measurement", "active_instrument")

#: Top-level ARRAYS that are caked per eta, like the members of
#: ``IntegrationResult``/``OmegaSumFrame``, and so concatenate the same way.
#: ``SumFrames`` is (nR, nEta) and was being dropped on the floor.
_ETA_CONCAT_ARRAYS = ("SumFrames",)

#: Everything written by name below. Anything in a source archive that is
#: NOT here goes through the catch-all in :func:`_carry_unhandled`, because
#: a hardcoded list silently loses whatever the next MIDAS release adds --
#: which is exactly how SumFrames and active_instrument went missing.
_NAMED = ("Omegas", "REtaMap", "InstrumentParameters", "IntegrationResult",
          "OmegaSumFrame") + _ETA_CONCAT_ARRAYS + _VERBATIM_GROUPS


def build_panel_tag(panels) -> str:
    """``ge1,ge2,ge3,ge4`` -> ``ge1234``; anything irregular is ``_``-joined."""
    panels = list(panels)
    if panels and all(re.fullmatch(r"ge\d+", p) for p in panels):
        return "ge" + "".join(p[2:] for p in panels)
    return "_".join(panels)


#: The frame-range segment a run-grouped archive carries before its
#: suffixes, e.g. the ``.000000_000833`` in
#: ``<froot>.000000_000833.ave.zarr.zip``.
_RANGE_RE = re.compile(r"\.\d+_\d+$")

#: Suffix tails a caked archive ends in, longest first — the tag goes in
#: FRONT of these, never between them.
_ZARR_TAILS = (".ave.zarr.zip", ".zarr.zip", ".zip")


def insert_panel_tag(name: str, tag: str) -> str:
    """Put ``tag`` where the panel token sits in a per-file name.

    "One zarr for the whole scan" names every panel's archive from the
    froot, which by definition has the detector token split off — so all
    four panels write the SAME basename and there is no ``.ge1.`` to
    substitute. The fallback that shipped inserted before the final suffix
    only, ``<froot>.<lo>_<hi>.ave.zarr`` + ``.ge1234`` + ``.zip``:

        4130_D_load_step_0.000000_000833.ave.zarr.ge1234.zip

    which reads as a zip of a zarr of ge1234, does not end in
    ``.zarr.zip`` the way every other archive does, and is missed by any
    glob looking for ``*.ge1234.*.zarr.zip``. Measured on a real 189-folder
    run.

    The tag belongs exactly where the token sits in the per-file names
    beside it: straight after the froot, BEFORE the frame range and before
    the ``.ave.zarr.zip`` tail.

        4130_D_load_step_0.ge1234.000000_000833.ave.zarr.zip
    """
    for tail in _ZARR_TAILS:
        if name.endswith(tail):
            stem = name[:-len(tail)]
            break
    else:
        stem, tail = name, ""
    m = _RANGE_RE.search(stem)
    if m:
        return f"{stem[:m.start()]}.{tag}{m.group(0)}{tail}"
    return f"{stem}.{tag}{tail}"


def default_combined_path(panel_paths: dict, out_dir=None) -> Optional[Path]:
    """Where the merged archive goes for a ``{panel: path}`` mapping.

    Directly under the run's shared output (froot) directory, a sibling of
    the ``ge1``..``ge4`` folders -- the merged archive is a property of the
    RUN, not of a detector, and burying it in ``ge1/zarr/`` both reads as a
    ge1 product and makes it awkward to glob out of a per-panel folder.

    The name is the first panel's with its token replaced by the combined
    tag: ``..._003502.ge1.zarr.zip`` -> ``..._003502.ge1234.zarr.zip``.
    When the per-panel name carries no token — "one zarr for the whole
    scan", where all four share a basename — :func:`insert_panel_tag` puts
    the tag in the same place the token would have been, so the result
    still ends in ``.zarr.zip``.

    ``out_dir`` is that froot directory when the caller knows it. Otherwise
    it is recovered from the panel path's own ``<out>/<panel>/zarr/<name>``
    shape, falling back to writing beside the input when the layout is
    something else.
    """
    if not panel_paths:
        return None
    panels = sorted(panel_paths)
    first = Path(panel_paths[panels[0]])
    tag = build_panel_tag(panels)
    tok = f".{panels[0]}."
    name = (first.name.replace(tok, f".{tag}.", 1) if tok in first.name
            else insert_panel_tag(first.name, tag))
    if out_dir:
        return Path(out_dir) / name
    parent = first.parent
    if parent.name == "zarr" and parent.parent.name == panels[0]:
        return parent.parent.parent / name
    return first.with_name(name)


def find_panel_zarr_sets(out_dir, panels) -> list:
    """``[{panel: path}, ...]`` -- one entry per frame that ALL panels wrote.

    A Hydra run writes each panel into its own ``ge{n}/zarr/`` subfolder under
    the shared Output directory, and with per-frame grouping that is many
    archives per panel, not one. Corresponding frames are matched by name:
    ``frame_output_stem`` puts the detector tag last, so panel n's
    ``<froot>_<NNNNNN>.ge{n}.zarr.zip`` names its siblings by substitution --
    the same rule the cake_parameters CSVs follow.

    Frames missing from any panel are skipped rather than part-combined: an
    archive built from three of four panels would be silently short on eta.
    """
    panels = list(panels)
    if len(panels) < 2:
        return []
    root = Path(out_dir)
    first = panels[0]
    first_dir = root / first / "zarr"
    if not first_dir.is_dir():
        return []
    # The panel token must appear DOT-DELIMITED, as frame_output_stem writes
    # it. A bare `first in a.name` also matched a combined `...ge1234...`
    # archive left in ge1/zarr/ by an older build that defaulted the output
    # beside its input -- it was then treated as a ge1 frame whose siblings
    # would be named `ge2234`. Harmless only because those never exist.
    tok = f".{first}."
    sets = []
    for a in sorted(first_dir.glob("*.zarr.zip")):
        # Two naming schemes reach this, and both are legitimate:
        #
        #   per-frame / per-file grouping -> <froot>_<NNNNNN>.ge1.ave.zarr.zip
        #       the panel token is IN the name, and a sibling is a
        #       substitution of it.
        #   "one zarr for the whole scan"  -> <froot>.ave.zarr.zip
        #       _run_out_stem names it from the froot, which by definition
        #       has the panel token split off, so all four panels write the
        #       SAME basename into their own ge{n}/zarr/. Requiring the
        #       token here found nothing and the combine reported "no frame
        #       had an archive from every panel" on a run that had four.
        if tok in a.name:
            sibling = lambda p, _n=a.name: _n.replace(tok, f".{p}.")
        elif panel_for_name(a.name) is None:
            sibling = lambda p, _n=a.name: _n
        else:
            continue            # names another panel; not ge1's to lead
        group = {first: str(a)}
        for p in panels[1:]:
            cand = root / p / "zarr" / sibling(p)
            if cand.is_file():
                group[p] = str(cand)
        if len(group) == len(panels):
            sets.append(group)
    return sets


def panel_for_name(name: str):
    """The ge<n> a BARE FILENAME claims, or ``None``.

    Deliberately not ``hydra.panel_for_file``, which reads the whole path:
    here the question is whether the NAME carries a token, with the folder
    (always ``ge{n}/zarr/``) excluded -- otherwise every archive would look
    like it named a panel and the shared-name scheme could never be
    recognised.
    """
    hits = re.findall(r"ge\s*([1-4])\b", name, flags=re.IGNORECASE)
    return int(hits[-1]) if hits else None


def _check(condition: bool, msg: str) -> None:
    if not condition:
        raise ValueError(msg)


#: Groups a per-panel archive must have for the merge to mean anything.
_REQUIRED = ("OmegaSumFrame", "Omegas", "REtaMap", "InstrumentParameters")


def _check_usable(panels, zs, paths) -> None:
    """Fail with a sentence, not a bare KeyError, on an unusable archive.

    A panel whose integration failed leaves a valid but EMPTY zip -- 136
    bytes, no groups -- and the first thing to touch it raised
    ``KeyError('OmegaSumFrame')``, which reached the run log as the single
    word ``'OmegaSumFrame'``. That says nothing about which panel, nothing
    about why, and nothing about where to look.
    """
    for panel, z, path in zip(panels, zs, paths):
        absent = [g for g in _REQUIRED if g not in z]
        if not absent:
            continue
        try:
            size = Path(path).stat().st_size
        except OSError:
            size = -1
        empty = not list(z.keys())
        raise ValueError(
            f"{panel}'s archive is not usable: {Path(path).name} has no "
            + ", ".join(absent)
            + (f" (the file is {size} bytes and holds nothing — that panel's "
               f"integration almost certainly failed; its error is in the run "
               f"log above)" if empty else
               f" (the file is {size} bytes; it holds "
               f"{', '.join(sorted(z.keys())) or 'nothing'})"))


def validate(panels, zs, paths=None):
    """``(ir_keys, osf_keys, eta_widths)``, or raise naming the first
    disagreement. Nothing is written until this passes."""
    p0 = panels[0]
    if paths is not None:
        _check_usable(panels, zs, paths)
    has_ir = ["IntegrationResult" in z for z in zs]
    if any(has_ir) and not all(has_ir):
        missing = [panels[i] for i, h in enumerate(has_ir) if not h]
        raise ValueError(
            f"IntegrationResult is present in some panels but missing in "
            f"others: missing in {missing}. All panels must agree.")

    if all(has_ir):
        ir_keys = sorted(zs[0]["IntegrationResult"].keys())
        for i in range(1, len(zs)):
            ki = sorted(zs[i]["IntegrationResult"].keys())
            _check(ki == ir_keys,
                   f"IntegrationResult key sets differ between {p0} and "
                   f"{panels[i]} (|{p0}|={len(ir_keys)}, "
                   f"|{panels[i]}|={len(ki)})")
        nR = zs[0]["IntegrationResult"][ir_keys[0]].shape[0]
        for i in range(1, len(zs)):
            s = zs[i]["IntegrationResult"][ir_keys[0]].shape
            _check(s[0] == nR, f"IntegrationResult R-bins differ: {p0}={nR}, "
                               f"{panels[i]}={s[0]}")
    else:
        ir_keys = []

    osf_keys = sorted(zs[0]["OmegaSumFrame"].keys())
    for i in range(1, len(zs)):
        ki = sorted(zs[i]["OmegaSumFrame"].keys())
        _check(ki == osf_keys,
               f"OmegaSumFrame key sets differ between {p0} and {panels[i]} "
               f"(|{p0}|={len(osf_keys)}, |{panels[i]}|={len(ki)})")
    nR_osf = zs[0]["OmegaSumFrame"][osf_keys[0]].shape[0]
    for i in range(1, len(zs)):
        s = zs[i]["OmegaSumFrame"][osf_keys[0]].shape
        _check(s[0] == nR_osf, f"OmegaSumFrame first-dim differs: "
                               f"{p0}={nR_osf}, {panels[i]}={s[0]}")

    # Omegas are compared with a TOLERANCE, not exactly. The four panels
    # are synchronised frames of one rotation, so they describe the same
    # angles -- but each panel's omega is now read from its OWN file's
    # measured channel (instrument/SMS/E/HL/RotX), and two recordings of
    # one stage agree to the encoder's precision, not to the bit. Exact
    # equality rejected a perfectly good four-panel set with
    # "Omegas differ between ge1 and ge2" and wrote 0/1 archives.
    #
    # The merged file takes panel 0's, which is as arbitrary and as
    # correct as any other member of a set this close together.
    om0 = np.asarray(zs[0]["Omegas"][...], dtype=float)
    for i in range(1, len(zs)):
        omi = np.asarray(zs[i]["Omegas"][...], dtype=float)
        _check(om0.shape == omi.shape,
               f"Omegas have different lengths: {p0}={om0.shape}, "
               f"{panels[i]}={omi.shape} — the panels did not integrate the "
               f"same number of frames")
        tol = _omega_tolerance(om0)
        if om0.size and not np.allclose(om0, omi, rtol=0.0, atol=tol,
                                        equal_nan=True):
            worst = float(np.nanmax(np.abs(om0 - omi)))
            _check(False,
                   f"Omegas differ between {p0} and {panels[i]} by up to "
                   f"{worst:g}° (tolerance {tol:g}°, one omega step) — that "
                   f"is a whole frame or more, so these are not the same "
                   f"frames of one rotation")

    rem0 = zs[0]["REtaMap"].shape
    for i in range(1, len(zs)):
        s = zs[i]["REtaMap"].shape
        _check(s[0] == rem0[0] and s[1] == rem0[1],
               f"REtaMap first/second dims differ: {p0}={rem0[:2]}, "
               f"{panels[i]}={s[:2]}")

    ip0 = sorted(zs[0]["InstrumentParameters"].keys())
    for i in range(1, len(zs)):
        ki = sorted(zs[i]["InstrumentParameters"].keys())
        _check(ki == ip0, f"InstrumentParameters keys differ between {p0} "
                          f"and {panels[i]}")

    for name in _ETA_CONCAT_ARRAYS:
        present = [name in z for z in zs]
        if any(present) and not all(present):
            absent = [panels[i] for i, h in enumerate(present) if not h]
            raise ValueError(
                f"{name} is present in some panels but missing in others: "
                f"missing in {absent}. All panels must agree.")
        if all(present):
            n0 = zs[0][name].shape[0]
            for i in range(1, len(zs)):
                si = zs[i][name].shape
                _check(si[0] == n0, f"{name} first-dim differs: {p0}={n0}, "
                                    f"{panels[i]}={si[0]}")

    src = "IntegrationResult" if ir_keys else "OmegaSumFrame"
    key = (ir_keys or osf_keys)[0]
    eta_widths = [z[src][key].shape[1] for z in zs]
    return ir_keys, osf_keys, eta_widths


#: ``(min_key, max_key)`` pairs, in both the CamelCase spelling every writer
#: in this app actually emits (``EtaMin``) and the underscored one that only
#: ever appeared in this module's own tests (``ETA_MIN``).
#:
#: Matching on a ``_MIN``/``_MAX`` SUFFIX alone -- which is what this did --
#: missed every real archive: nothing the GUI writes is spelled that way, so
#: the span logic never ran on live data, and ``EtaMin``/``EtaMax`` fell
#: through to the "all panels agree" branch, disagreed (as hydra panels must,
#: covering different sectors), and were dropped from the merged file
#: entirely. ``RMin``/``RMax`` survived only because every panel shares them.
_SPAN_PAIRS = (("EtaMin", "EtaMax"), ("ETA_MIN", "ETA_MAX"),
               ("RMin", "RMax"), ("R_MIN", "R_MAX"))


def _eta_bounds(block: dict):
    """``(lo, hi)`` from either spelling, or ``None``."""
    if not block:
        return None
    lo = block.get("EtaMin", block.get("ETA_MIN"))
    hi = block.get("EtaMax", block.get("ETA_MAX"))
    if lo is None or hi is None:
        return None
    return float(lo), float(hi)


def eta_tiling_problem(per_panel: dict, panels, tol: float = 1e-6):
    """``None`` if the panels' eta ranges tile edge to edge in PANEL ORDER,
    else a sentence naming the first gap or overlap.

    Hydra's panels usually pave the azimuth: each one's EtaMax is the next
    one's EtaMin, modulo the 360 deg wrap that makes ge1's 210 and ge2's
    -150 the same ray.

    A set that does NOT tile is not an error and does not make the merged
    archive wrong. The merge is a concatenation, and every column's eta
    travels with it in REtaMap, so an uncovered wedge is simply ABSENT --
    not misindexed, not shifted. I against 2theta/q/d for each eta the file
    does have is exactly as correct as it would be with a full turn, and a
    gap may be entirely deliberate (a panel's real coverage, a masked
    region).

    What it does rule out is describing the result with ONE (EtaMin, EtaMax)
    pair, which is the only reason this is computed: such a pair would
    assert a continuous sweep the file does not contain. Callers use it to
    decide whether that summary is sayable, and to mention the discontinuity
    in passing -- not to refuse anything.
    """
    spans = []
    for p in panels:
        b = _eta_bounds(per_panel.get(p))
        if b is None:
            return None            # nothing to check against
        spans.append((p, b[0], b[1]))
    if len(spans) < 2:
        return None
    prev_panel, _, prev_hi = spans[0]
    end = prev_hi
    for panel, lo, hi in spans[1:]:
        step = (lo - end) % 360.0
        off = min(step, 360.0 - step)
        if off > tol:
            kind = "gap" if step <= 180.0 else "overlap"
            return (f"eta ranges do not tile: {off:g} deg {kind} between "
                    f"{prev_panel} (ends {prev_hi:g}) and {panel} "
                    f"(starts {lo:g})")
        end += hi - lo
        prev_panel, prev_hi = panel, hi
    return None


#: Plane of ``REtaMap`` holding each bin's azimuth. The layout is
#: ``[R, 2theta, eta, area, Q]``, and plane 2 is read as eta everywhere else
#: in this app too (``tab_zarrviewer._get_integration_axes``, the GSAS
#: export, ``test_gsas_export``) -- this is not a local guess.
_RETAMAP_ETA_PLANE = 2


def wrap_eta(a):
    """Fold angles into ``[-180, 180)``, the range the backend reports in.

    The same half-open interval as ``eta_wrap.ETA_LO``/``ETA_HI``, so the
    two modules cannot disagree about where the cut falls.
    """
    return (np.asarray(a, dtype=float) + 180.0) % 360.0 - 180.0


def merged_eta_axis(zs):
    """Each merged column's azimuth, in panel order, or ``None``.

    Read from ``REtaMap``'s eta plane rather than from the panels'
    ``cake_params``, because the plane is what the file actually says and
    what every reader plots against. A request and a label can differ --
    ``eta_wrap`` deliberately relabels ge1's columns with the caller's own
    unwrapped centres -- and it is the label that has to come out sorted.
    """
    out = []
    for z in zs:
        rm = z.get("REtaMap", None)
        if rm is None or len(rm.shape) != 3 or rm.shape[0] <= _RETAMAP_ETA_PLANE:
            return None
        out.append(np.asarray(rm[_RETAMAP_ETA_PLANE]).mean(axis=0))
    return np.concatenate(out) if out else None


def plan_eta_order(eta_axis):
    """``(order, wrapped)`` sorting a merged eta axis, or ``(None, None)``.

    A four-panel merge concatenates in PANEL order, and at 1-ID-E ge1's
    caking range is written past the cut (``ETA_MIN=120, ETA_MAX=210``, a
    convention every experiment on this machine shares). So the merged axis
    reads 125..205, then jumps to -145, then -55, then 45: one continuous
    sweep, stored as a sawtooth. Downstream that forced every reader to
    unwrap it before it could derive cell edges, and the unwrapped axis
    then ran to 475 deg -- faithful, and not an angle anyone quotes.

    Wrapping ge1's three past-cut columns back to -175..-155 and sorting
    makes the axis monotonic in conventional numbers, with no column
    gained, lost or altered: this is a PERMUTATION plus a relabel of the
    eta plane, and the data in each column is untouched.

    Declines, leaving the merge exactly as it was, when:

    * the axis is already strictly monotonic -- nothing to fix, and a
      single-panel ge1 archive keeps the unwrapped centres ``eta_wrap``
      deliberately gave it;
    * any value is non-finite, so the order is not defined;
    * sorting does not make it STRICTLY increasing, i.e. two columns share
      an azimuth. Overlapping panels have no one right order and quietly
      interleaving them would be a fabrication.
    """
    if eta_axis is None:
        return None, None
    arr = np.asarray(eta_axis, dtype=float)
    if arr.ndim != 1 or arr.size < 2 or not np.all(np.isfinite(arr)):
        return None, None
    d = np.diff(arr)
    if np.all(d > 0) or np.all(d < 0):
        return None, None
    wrapped = wrap_eta(arr)
    order = np.argsort(wrapped, kind="stable")
    if not np.all(np.diff(wrapped[order]) > 0):
        return None, None
    return order, wrapped


def apply_eta_order(arr, order):
    """Reorder an eta-caked array's LAST axis.

    Every caked array in this layout -- ``(nR, nEta)`` and
    ``(C, nR, nEta)`` alike -- carries eta last, so one indexing expression
    covers them all. ``None`` passes straight through.
    """
    if order is None or arr is None:
        return arr
    return np.asarray(arr)[..., order]


def _eta_bin_size(block: dict):
    if not block:
        return None
    v = block.get("EtaBinSize", block.get("ETA_STEP", block.get("ETA_BIN_SIZE")))
    return None if v in (None, 0) else float(v)


def eta_beyond_branch_cut(per_panel: dict, panels):
    """Panels whose eta request cannot be served, or ``None``.

    Crossing the +/-180 wrap is no longer a problem in itself:
    ``midas_gui.eta_wrap`` bins the whole circle and lifts the requested
    bins out of it, so ge1's 120..210 now comes back full in one run. This
    reports only what that cannot do -- a grid that does not divide the
    circle from -180, or a BIN that straddles the seam (a 10 deg bin
    centred on 180 spans 175..185, half on each side, and no single
    floor() binning can fill it).

    Delegates to ``plan_eta`` rather than re-deriving the rule, so the GUI
    and the integrator cannot disagree about what is servable.
    """
    from midas_gui.eta_wrap import plan_eta
    bad = []
    for p in panels:
        b = per_panel.get(p) or {}
        bounds = _eta_bounds(b)
        step = _eta_bin_size(b)
        if bounds is None or not step:
            continue
        problem = plan_eta(bounds[0], bounds[1], step).problem
        if problem:
            bad.append(f"{p}: {problem}")
    return "; ".join(bad) if bad else None


def _merged_eta_span(per_panel: dict, panels, start=None):
    """``(start, end)`` of the concatenated eta axis, unwrapped.

    Only meaningful once ``eta_tiling_problem`` has returned ``None``. The end
    is the start plus the summed widths rather than the last panel's EtaMax,
    because the sweep runs past 180 and back around: this dataset's four
    90 deg panels read 120 -> 480, not 120 -> 120.

    ``start`` overrides panel 0's EtaMin. Sorting the merged axis does not
    change the sweep, only where it is cut, so the same summed width then
    runs from the first SORTED bin's lower edge: -180 -> 180 rather than
    120 -> 480 for a set that paves the circle.
    """
    bounds = [_eta_bounds(per_panel.get(p)) for p in panels]
    if start is None:
        start = bounds[0][0]
    return start, start + sum(hi - lo for lo, hi in bounds)


def _sorted_eta_start(per_panel: dict, panels, merged_eta):
    """Lower edge of the first bin on the sorted axis, or ``None``.

    ``None`` when the panels disagree about bin size, since then the first
    centre does not imply an edge and guessing one would put the span half
    a bin out.
    """
    if merged_eta is None or not len(merged_eta):
        return None
    steps = {_eta_bin_size(per_panel.get(p)) for p in panels}
    steps.discard(None)
    if len(steps) != 1:
        return None
    return float(merged_eta[0]) - steps.pop() / 2.0


def _aggregate_cake_params(per_panel: dict, panels,
                           sorted_start=None) -> Optional[dict]:
    """One cake_params block that honestly describes the MERGED file.

    Values every panel agrees on are carried through. A (min, max) pair is
    reported as the span the merged file actually covers -- but for eta only
    when the panels tile, because a min and a max taken across ranges that
    wrap, overlap or leave a hole describe a sweep the file does not contain.
    """
    blocks = [per_panel[p] for p in panels if per_panel.get(p)]
    if not blocks:
        return None
    paired = {k for pair in _SPAN_PAIRS for k in pair}
    out = {}
    for k in sorted({k for b in blocks for k in b}):
        if k in paired:
            continue
        vals = [b[k] for b in blocks if k in b]
        if all(v == vals[0] for v in vals):
            out[k] = vals[0]
    broken = eta_tiling_problem(per_panel, panels)
    for lo_k, hi_k in _SPAN_PAIRS:
        los = [b[lo_k] for b in blocks if lo_k in b]
        his = [b[hi_k] for b in blocks if hi_k in b]
        if not los or not his:
            continue
        if lo_k.lower().startswith("eta"):
            if broken:
                # One pair cannot describe a wrapped or gapped sweep. The
                # exact per-panel ranges are kept in the merge entry's
                # extra.cake_params_per_panel, which loses nothing.
                continue
            out[lo_k], out[hi_k] = _merged_eta_span(per_panel, panels,
                                                    sorted_start)
        else:
            out[lo_k], out[hi_k] = min(los), max(his)
    return out


def _zip_directory(src_dir: Path, out_zip: Path) -> None:
    """Pack ``src_dir`` flat into ``out_zip``, matching ZipStore's layout."""
    if out_zip.exists():
        out_zip.unlink()
    with zipfile.ZipFile(str(out_zip), mode="w",
                         compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for root_dir, _dirs, files in os.walk(src_dir):
            for fname in files:
                abs_path = Path(root_dir) / fname
                zf.write(abs_path, abs_path.relative_to(src_dir).as_posix())


def _caked_leading_shapes(zs, eta_widths) -> set:
    """Leading dimensions (everything but the eta axis) of the arrays we
    already KNOW are caked per eta, as seen in panel 0.

    Used to recognise a caked array when the eta widths alone cannot. A
    caked array in this layout is ``(nR, nEta)`` or ``(C, nR, nEta)``, so
    its leading dims are shared with SumFrames / an OmegaSumFrame member /
    REtaMap, whatever the azimuth happens to be.
    """
    out = set()
    z0, w0 = zs[0], eta_widths[0]
    for name in ("SumFrames", "REtaMap"):
        sh = getattr(z0.get(name, None), "shape", None)
        if sh and sh[-1] == w0:
            out.add(tuple(sh[:-1]))
    for grp in ("IntegrationResult", "OmegaSumFrame"):
        if grp in z0:
            for k in sorted(z0[grp].keys())[:1]:
                sh = z0[grp][k].shape
                if sh and sh[-1] == w0:
                    out.add(tuple(sh[:-1]))
    return out


def _is_eta_caked(name, zs, eta_widths, caked_leading) -> bool:
    """Is this unrecognised array binned per eta, and therefore to be
    concatenated rather than copied?

    The test is the trailing axis matching each panel's eta width -- but
    that test is only worth anything when the widths say something. Cake
    only the four cardinal directions, one 10 deg bin each, and EVERY panel
    has nEta=1: an unrelated ``(3, 1)`` array then matches [1,1,1,1] and
    gets concatenated into a ``(3, 4)`` fabrication. Measured, on exactly
    that input, before this guard existed.

    So when the widths are uniform the array must also be SHAPED like the
    caked arrays in the same file -- same leading dims as SumFrames,
    REtaMap or an OmegaSumFrame member. Non-uniform widths (the usual
    windmill, 9/9/9/8) are discriminating on their own.

    Guessing wrong in the cautious direction costs a copy of panel 0's
    values and a line in the log saying so. Guessing wrong the other way
    invents data. They are not symmetric.
    """
    shapes = [z[name].shape for z in zs]
    if any(len(sh) < 2 for sh in shapes):
        return False                     # a 1-D array is a vector, not a cake
    if any(sh[-1] != w for sh, w in zip(shapes, eta_widths)):
        return False
    if len({tuple(sh[:-1]) for sh in shapes}) != 1:
        return False                     # leading dims must agree to stack
    if len(set(eta_widths)) == 1:
        return tuple(shapes[0][:-1]) in caked_leading
    return True


def _is_retamap_like(name: str, data) -> bool:
    """Does this carried array hold per-bin eta in :data:`_RETAMAP_ETA_PLANE`?

    Keyed on the NAME, deliberately. Shape alone cannot tell a corrected
    REtaMap from any other ``(C, nR, nEta)`` array, and rewriting a plane
    of something that merely looks similar would corrupt it -- the one
    direction this module has consistently refused to guess in.
    """
    return (name.startswith("REtaMap")
            and getattr(data, "ndim", 0) == 3
            and data.shape[0] > _RETAMAP_ETA_PLANE)


def _carry_unhandled(root, zs, panels, eta_widths, order=None) -> list:
    """Copy across anything this module does not write by name.

    Returns the names it carried AND how, so the caller can say so:
    silence is how SumFrames and active_instrument were lost in the first
    place, and the next release's addition should not go the same way.

    Arrays recognised as eta-caked (see :func:`_is_eta_caked`) are
    concatenated like the rest. Everything else is taken from panel 0 --
    the conservative choice, since inventing a merge rule for an array
    whose meaning is unknown is how you get a quietly wrong archive rather
    than a visibly incomplete one.

    A carried ``REtaMap`` VARIANT is relabelled as well as reordered. This
    app never writes one, but ``REtaMap_corrected`` comes through from
    upstream and the Zarr Viewer PREFERS it over ``REtaMap`` when present
    -- so reordering its columns while leaving its eta plane saying 185
    would plot the sorted cake against stale labels, which is worse than
    not sorting at all.
    """
    import zarr
    carried = []
    caked_leading = _caked_leading_shapes(zs, eta_widths)
    for name in sorted(zs[0].keys()):
        if name in _NAMED or name in root:
            continue
        if not all(name in z for z in zs):
            continue                     # partial: leave it out, say so
        obj = zs[0][name]
        shape = getattr(obj, "shape", None)
        if shape is None:
            zarr.copy(obj, root, name=name)
            carried.append(f"{name}/ (from {panels[0]})")
        elif _is_eta_caked(name, zs, eta_widths, caked_leading):
            data = np.concatenate([z[name][...] for z in zs],
                                  axis=len(shape) - 1)
            note = "eta-concatenated"
            if order is not None and _is_retamap_like(name, data):
                data[_RETAMAP_ETA_PLANE] = wrap_eta(data[_RETAMAP_ETA_PLANE])
                note = "eta-concatenated, eta plane wrapped and sorted"
            root.create_dataset(name, data=apply_eta_order(data, order))
            carried.append(f"{name} ({note})")
        else:
            root.create_dataset(name, data=obj[...])
            carried.append(f"{name} (from {panels[0]})")
    return carried


def _write_merged(root, zs, panels, ir_keys, osf_keys, eta_widths):
    """Write the merged archive; returns ``(carried, merged_eta, sorted)``.

    ``merged_eta`` is the eta axis as WRITTEN -- sorted and wrapped when
    :func:`plan_eta_order` accepted, panel-order otherwise -- so the
    provenance entry can describe the file rather than the request.
    """
    import zarr
    attrs = dict(zs[0].attrs)
    attrs["combined_panels"] = list(panels)
    root.attrs.put(attrs)

    # Decided once, from the concatenated axis, and applied to every caked
    # array below. Deriving it per array would risk two of them disagreeing.
    order, wrapped = plan_eta_order(merged_eta_axis(zs))

    root.create_dataset("Omegas", data=zs[0]["Omegas"][...])

    ip_out = root.create_group("InstrumentParameters")
    ip_out.attrs.put({"panels": list(panels)})
    for k in sorted(zs[0]["InstrumentParameters"].keys()):
        ip_out.create_dataset(k, data=np.concatenate(
            [np.atleast_1d(z["InstrumentParameters"][k][...]) for z in zs]))

    for group, keys in (("IntegrationResult", ir_keys),
                        ("OmegaSumFrame", osf_keys)):
        if not keys:
            continue
        g = root.create_group(group)
        for k in keys:
            g.create_dataset(k, data=apply_eta_order(np.concatenate(
                [z[group][k][...] for z in zs], axis=1), order))

    reta = np.concatenate([z["REtaMap"][...] for z in zs], axis=2)
    if order is not None:
        # Relabel before reordering: the plane has to SAY -175 where it
        # said 185, or the sorted axis would carry the old numbers and the
        # file would be self-contradictory.
        reta[_RETAMAP_ETA_PLANE] = wrap_eta(reta[_RETAMAP_ETA_PLANE])
        reta = apply_eta_order(reta, order)
    root.create_dataset("REtaMap", data=reta)

    # Top-level caked arrays: same (nR, nEta) layout as the group members
    # above, so the same axis-1 concatenation.
    for name in _ETA_CONCAT_ARRAYS:
        if all(name in z for z in zs):
            root.create_dataset(name, data=apply_eta_order(np.concatenate(
                [z[name][...] for z in zs], axis=1), order))

    for grp in _VERBATIM_GROUPS:
        if grp in zs[0]:
            zarr.copy(zs[0][grp], root, name=grp)

    carried = _carry_unhandled(root, zs, panels, eta_widths, order)
    merged_eta = wrapped[order] if order is not None else merged_eta_axis(zs)
    return carried, merged_eta, order is not None


def _merge_provenance(root, zs, panels, paths, out_path,
                      merged_eta=None, eta_sorted=False):
    """Carry every panel's prior history forward, tagged by source panel, and
    append this merge. Four streams stay distinguishable rather than one
    overwriting the others.

    Returns the eta-tiling note, if any, so the caller can mention it -- it
    is recorded in the merge entry either way, but a line in the file's
    attributes is not something anyone reads at the beamline.
    """
    merged, per_cake, per_instr = [], {}, {}
    for panel, z in zip(panels, zs):
        hist = list(z.attrs.get("provenance_history", []) or [])
        for e in hist:
            tagged = dict(e)
            tagged["source_panel"] = panel
            merged.append(tagged)
        cp = next((e.get("cake_params") for e in reversed(hist)
                   if isinstance(e, dict) and e.get("cake_params")), None)
        if cp:
            per_cake[panel] = cp
        ip = next((e.get("instrument_params") for e in reversed(hist)
                   if isinstance(e, dict) and e.get("instrument_params")), None)
        if ip:
            per_instr[panel] = ip
    broken = eta_tiling_problem(per_cake, panels)
    beyond = eta_beyond_branch_cut(per_cake, panels)
    # The tiling check reads the REQUESTED ranges in panel order, so it is
    # unaffected by how the columns ended up stored -- a gap is a property
    # of the inputs, not of the ordering.
    sorted_start = (_sorted_eta_start(per_cake, panels, merged_eta)
                    if eta_sorted else None)
    merged.append({
        "tool": "midas_gui.hydra_zarr_combine",
        "timestamp_iso": datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
        "inputs": [str(p) for p in paths],
        "cake_params": _aggregate_cake_params(per_cake, panels, sorted_start),
        "extra": {"panels": list(panels),
                  "cake_params_per_panel": per_cake,
                  "instrument_params_per_panel": per_instr,
                  "eta_tiling_problem": broken,
                  "eta_beyond_branch_cut": beyond,
                  "eta_sorted": bool(eta_sorted),
                  "out": str(out_path)},
    })
    root.attrs["provenance_history"] = merged
    return broken, beyond


def combine_panel_zarrs(panel_paths: dict, out_path=None, warn=None) -> Path:
    """Merge ``{panel: zarr.zip path}`` into one archive; returns its path.

    Raises ``ValueError`` naming the first disagreement, before writing
    anything, so a half-merged archive is never left behind.

    ``warn`` is an optional ``str -> None`` sink for remarks about the
    result -- currently only that the panels' eta ranges do not tile. That
    is a note, not a defect: see :func:`eta_tiling_problem` for why a
    non-contiguous set still merges correctly. Nothing here refuses a merge
    over it.
    """
    import zarr
    panels = sorted(panel_paths)
    if len(panels) < 2:
        raise ValueError(f"need at least two panels to combine; got {panels}")
    paths = [Path(panel_paths[p]) for p in panels]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        raise ValueError("missing panel zarr(s): " + ", ".join(missing))

    out_path = Path(out_path) if out_path else default_combined_path(panel_paths)
    stores = [zarr.ZipStore(str(p), mode="r") for p in paths]
    try:
        zs = [zarr.group(s) for s in stores]
        ir_keys, osf_keys, eta_widths = validate(panels, zs, paths)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="midas_gui_combine_") as tmp:
            root = zarr.group(zarr.DirectoryStore(tmp))
            carried, merged_eta, eta_sorted = _write_merged(
                root, zs, panels, ir_keys, osf_keys, eta_widths)
            broken, beyond = _merge_provenance(root, zs, panels, paths,
                                               out_path, merged_eta,
                                               eta_sorted)
            if warn:
                if beyond:
                    warn(beyond)
                if broken:
                    warn(broken)
                if eta_sorted:
                    warn(f"eta axis wrapped into [-180, 180) and sorted: "
                         f"{merged_eta[0]:g}…{merged_eta[-1]:g}° "
                         f"({len(merged_eta)} columns, panel data unchanged)")
                if carried:
                    warn("carried across unrecognised entries: "
                         + ", ".join(carried))
                dropped = sorted(
                    k for k in zs[0].keys()
                    if k not in root and not all(k in z for z in zs))
                if dropped:
                    warn("left out, present in some panels but not all: "
                         + ", ".join(dropped))
            _zip_directory(Path(tmp), out_path)
    finally:
        for s in stores:
            try:
                s.close()
            except Exception:
                pass
    return out_path
