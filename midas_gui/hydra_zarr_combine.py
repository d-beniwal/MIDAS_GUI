"""Combine the four Hydra panels' caked zarrs into one, concatenated on eta.

A port of ``mpe_wf_saxs_waxs/combine_hydra_zarr.py`` into this app, so a
Hydra Batch Integrate run can produce the merged archive itself rather than
leaving the user to run a second tool by hand. The LAYOUT is what downstream
code reads, so it is reproduced exactly:

  ``IntegrationResult/<key>``   axis=1   (nR, nEta) -> (nR, sum nEta)
  ``OmegaSumFrame/<key>``       axis=1   (nR, nEta) -> (nR, sum nEta)
  ``REtaMap``                   axis=2   (C, nR, nEta) -> (C, nR, sum nEta)
  ``Omegas``                    copied from panel 0 (validated identical)
  ``InstrumentParameters/<key>`` stacked into a length-N array, one entry per
                                panel, order recorded in ``.attrs['panels']``
  ``instrument/``/``measurement/`` copied verbatim from panel 0
  root attrs                    panel 0's, plus ``combined_panels``

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

#: Groups copied across untouched from the first panel.
_VERBATIM_GROUPS = ("instrument", "measurement")


def build_panel_tag(panels) -> str:
    """``ge1,ge2,ge3,ge4`` -> ``ge1234``; anything irregular is ``_``-joined."""
    panels = list(panels)
    if panels and all(re.fullmatch(r"ge\d+", p) for p in panels):
        return "ge" + "".join(p[2:] for p in panels)
    return "_".join(panels)


def default_combined_path(panel_paths: dict, out_dir=None) -> Optional[Path]:
    """Where the merged archive goes for a ``{panel: path}`` mapping.

    Directly under the run's shared output (froot) directory, a sibling of
    the ``ge1``..``ge4`` folders -- the merged archive is a property of the
    RUN, not of a detector, and burying it in ``ge1/zarr/`` both reads as a
    ge1 product and makes it awkward to glob out of a per-panel folder.

    The name is the first panel's with its token replaced by the combined
    tag: ``..._003502.ge1.zarr.zip`` -> ``..._003502.ge1234.zarr.zip``.

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
    name = (first.name.replace(panels[0], tag, 1) if panels[0] in first.name
            else f"{first.stem}.{tag}{first.suffix}")
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
    sets = []
    for a in sorted(first_dir.glob("*.zarr.zip")):
        if first not in a.name:
            continue
        group = {first: str(a)}
        for p in panels[1:]:
            cand = root / p / "zarr" / a.name.replace(first, p)
            if cand.is_file():
                group[p] = str(cand)
        if len(group) == len(panels):
            sets.append(group)
    return sets


def _check(condition: bool, msg: str) -> None:
    if not condition:
        raise ValueError(msg)


def validate(panels, zs):
    """``(ir_keys, osf_keys, eta_widths)``, or raise naming the first
    disagreement. Nothing is written until this passes."""
    p0 = panels[0]
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

    om0 = zs[0]["Omegas"][...]
    for i in range(1, len(zs)):
        omi = zs[i]["Omegas"][...]
        _check(om0.shape == omi.shape and np.array_equal(om0, omi),
               f"Omegas differ between {p0} and {panels[i]}")

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

    src = "IntegrationResult" if ir_keys else "OmegaSumFrame"
    key = (ir_keys or osf_keys)[0]
    eta_widths = [z[src][key].shape[1] for z in zs]
    return ir_keys, osf_keys, eta_widths


def _aggregate_cake_params(per_panel: dict, panels) -> Optional[dict]:
    """One cake_params block that honestly describes the MERGED file: eta and
    R span the union across panels, identical values are carried through, and
    anything that genuinely differs is dropped rather than misreported."""
    blocks = [per_panel[p] for p in panels if per_panel.get(p)]
    if not blocks:
        return None
    out = {}
    for k in sorted({k for b in blocks for k in b}):
        vals = [b[k] for b in blocks if k in b]
        if k.endswith("_MIN"):
            out[k] = min(vals)
        elif k.endswith("_MAX"):
            out[k] = max(vals)
        elif all(v == vals[0] for v in vals):
            out[k] = vals[0]
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


def _write_merged(root, zs, panels, ir_keys, osf_keys) -> None:
    import zarr
    attrs = dict(zs[0].attrs)
    attrs["combined_panels"] = list(panels)
    root.attrs.put(attrs)

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
            g.create_dataset(k, data=np.concatenate(
                [z[group][k][...] for z in zs], axis=1))

    root.create_dataset("REtaMap", data=np.concatenate(
        [z["REtaMap"][...] for z in zs], axis=2))

    for grp in _VERBATIM_GROUPS:
        if grp in zs[0]:
            zarr.copy(zs[0][grp], root, name=grp)


def _merge_provenance(root, zs, panels, paths, out_path) -> None:
    """Carry every panel's prior history forward, tagged by source panel, and
    append this merge. Four streams stay distinguishable rather than one
    overwriting the others."""
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
    merged.append({
        "tool": "midas_gui.hydra_zarr_combine",
        "timestamp_iso": datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
        "inputs": [str(p) for p in paths],
        "cake_params": _aggregate_cake_params(per_cake, panels),
        "extra": {"panels": list(panels),
                  "cake_params_per_panel": per_cake,
                  "instrument_params_per_panel": per_instr,
                  "out": str(out_path)},
    })
    root.attrs["provenance_history"] = merged


def combine_panel_zarrs(panel_paths: dict, out_path=None) -> Path:
    """Merge ``{panel: zarr.zip path}`` into one archive; returns its path.

    Raises ``ValueError`` naming the first disagreement, before writing
    anything, so a half-merged archive is never left behind.
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
        ir_keys, osf_keys, _eta = validate(panels, zs)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="midas_gui_combine_") as tmp:
            root = zarr.group(zarr.DirectoryStore(tmp))
            _write_merged(root, zs, panels, ir_keys, osf_keys)
            _merge_provenance(root, zs, panels, paths, out_path)
            _zip_directory(Path(tmp), out_path)
    finally:
        for s in stores:
            try:
                s.close()
            except Exception:
                pass
    return out_path
