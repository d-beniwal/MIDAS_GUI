"""Phase identification report for a saved Solve Cell project (Qt-free; CLI).

    python -m midas_gui.solve_cell.identify PROJECT.h5 [--out report.md] [--json out.json]
                                            [--tol 0.10] [--decoys 20] [--ab-initio]

Reads the project's diamond-filtered candidate spots and
  1. screens the built-in candidate phases against same-volume random lattices (``phase_screen``);
  2. fits one omega offset per panel for the best phase (``panel_align``) and reports the
     before/after match count and residual, with a control: random same-volume lattices aligned the
     same way;
  3. re-indexes the aligned spots with the best phase's cell (known structure) and, with
     ``--ab-initio``, blind, and compares the refined cells with the known lattice.
Nothing is written back to the project. Lattice agreement only (no intensities).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Optional

import numpy as np


def identify(record: dict, *, tol: float = 0.10, align_tol: float = 0.08, n_decoys: int = 20,
             n_control: int = 10, ab_initio: bool = False, log=print) -> dict:
    from . import cell_report as cr, panel_align as pa, phase_screen as ps
    from . import pipeline as P

    fl = record["diamond"]["flagged_df"] if record.get("diamond") else record["ingest"]["spots_df"]
    cand = fl[~fl["is_diamond"]].reset_index(drop=True) if "is_diamond" in fl.columns else fl
    g0 = cand[["qsample_x", "qsample_y", "qsample_z"]].to_numpy(float)
    out: dict = {"n_spots": int(len(cand)), "tol": tol, "align_tol": align_tol}

    log(f"[identify] screening phases on {len(cand)} spots (tol {tol}, {n_decoys} decoys each)")
    rows = ps.screen_phases(g0, tol=tol, n_decoys=n_decoys,
                            on_progress=lambda k, v: log(f"[identify]   {v['name']}: {v['best_n']} "
                                                         f"(decoys {v['decoy_mean']:.0f}±{v['decoy_sd']:.0f}, z {v['z']:.1f})"))
    out["screen"] = [{k: v for k, v in r.items() if k not in ("UB",)} for r in rows]
    best = rows[0]
    out["best_phase"] = best["name"]

    have_lab = all(c in cand.columns for c in ("qlab_x", "omega_deg", "panel_id")) and cand["panel_id"].nunique() > 1
    qs = g0
    if have_lab:
        qlab = cand[["qlab_x", "qlab_y", "qlab_z"]].to_numpy(float)
        om, pid = cand["omega_deg"].to_numpy(float), cand["panel_id"].to_numpy()
        B0 = P._bmatrix_from_cell(best["primitive_cell"])
        log(f"[identify] aligning panels with {best['name']}")
        al = pa.fit_panel_omega_offsets(qlab, om, pid, B0, tol=align_tol, UB0=best["UB"])
        out["align"] = {k: v for k, v in al.items() if k != "UB"}
        _, qs = pa.apply_omega_offsets(qlab, om, pid, al["offsets"])
        if n_control:
            log(f"[identify] control: {n_control} same-volume random lattices aligned the same way")
            rng = np.random.default_rng(11)
            ctl = []
            for i, c in enumerate(ps.random_decoy_cells(best["volume"], n_control, rng)):
                a = pa.fit_panel_omega_offsets(qlab, om, pid, P._bmatrix_from_cell(c), tol=align_tol,
                                               n_search=100_000, seed=i)
                ctl.append(a["n_matched_after"])
            out["align_control"] = {"matched_after": ctl, "mean": float(np.mean(ctl)), "sd": float(np.std(ctl)),
                                    "max": int(np.max(ctl))}
    else:
        out["align"] = None

    refined = {}
    for tag, g in (("unaligned", g0),) + ((("aligned", qs),) if have_lab else ()):
        r = P._stage_index_known_cell({"g_2pi": g, "known_cell": best["primitive_cell"], "tol": align_tol,
                                       "n_search": 300_000, "n_null_draws": 3, "min_accept": 15})
        refined[tag] = _cell_summary(r, cr) if r["success"] else {"success": False, "notes": r.get("notes")}
    out["known_structure"] = refined
    if ab_initio:
        gdf = cand.copy()
        gdf[["qsample_x", "qsample_y", "qsample_z"]] = qs
        ab = P._stage_ab_initio({"candidate_df": gdf, "sigma_g": 0.005, "min_reflections": 20, "tol": tol})["ab_initio_raw"]
        out["ab_initio"] = ({"success": True, "n_indexed": int(ab.n_indexed), "n_reflections": int(ab.n_reflections),
                             "chance": (ab.diagnostics or {}).get("chance"),
                             "score_floor": (ab.diagnostics or {}).get("score_floor"),
                             **_cell_summary({"cell": list(ab.cell), "rms_drlv": (ab.diagnostics or {}).get("rms_drlv", float("nan"))}, cr)}
                            if ab.success else {"success": False, "notes": list(ab.notes)})
    return out


def _cell_summary(r: dict, cr) -> dict:
    cell = [float(v) for v in r["cell"]]
    m = cr.rank_references(cell, top=1)
    best = m[0] if m else None
    return {"success": True, "cell": cell, "volume": cr.cell_volume(cell), "rms_drlv": float(r.get("rms_drlv", float("nan"))),
            "closest": ({"name": best["name"], "verdict": best["verdict"], "score": best["score"],
                         "d_len_pct": best["d_len_pct"], "d_ang_deg": best["d_ang_deg"],
                         "d_vol_pct": best["d_vol_pct"]} if best else None)}


def format_markdown(res: dict, project: str = "") -> str:
    L = [f"# Phase identification — {project}", "",
         f"{res['n_spots']} candidate spots. Lattice agreement only (no intensities, no atoms).", "",
         "## 1. Which candidate phase fits the spots better than chance?", "",
         f"Best random-orientation match per phase (tolerance {res['tol']} in hkl) vs {res['screen'][0]['n_decoys']} "
         "random lattices of the same volume.", "",
         "| Phase | Spots matched | Decoys mean ± sd (max) | z | Verdict |", "|---|---|---|---|---|"]
    for r in res["screen"]:
        L.append(f"| {r['name']} | {r['best_n']} / {r['n_spots']} | {r['decoy_mean']:.1f} ± {r['decoy_sd']:.1f} "
                 f"({r['decoy_max']}) | {r['z']:.1f} | {r['verdict']} |")
    al = res.get("align")
    if al:
        L += ["", f"## 2. Per-panel omega offsets ({res['best_phase']} hypothesis)", "",
              f"Relative to panel {al['reference_panel']}. Matched spots at tolerance {res['align_tol']}: "
              f"**{al['n_matched_before']} → {al['n_matched_after']}** of {al['n_spots']}; rms "
              f"{al['rms_before']:.4f} → {al['rms_after']:.4f} Å⁻¹.", "",
              "| Panel | Offset (°) | Matched before → after | rms after (Å⁻¹) |", "|---|---|---|---|"]
        for r in al["per_panel"]:
            L.append(f"| {r['panel']} | {al['offsets'][r['panel']]:+.2f} | {r.get('n_matched_before', '–')} → "
                     f"{r['n_matched']} of {r['n_spots']} | {r['rms']:.4f} |")
        c = res.get("align_control")
        if c:
            L += ["", f"Control — random same-volume lattices aligned the same way: matched {c['mean']:.1f} ± "
                      f"{c['sd']:.1f} (max {c['max']}) vs {al['n_matched_after']} for {res['best_phase']}."]
    L += ["", "## 3. Re-indexing with the identified cell (known structure, then refined freely)", "",
          "| Spots | Refined cell (Å, °) | V (Å³) | Closest known lattice | Edge dev % | Angle dev ° | ΔV % |", "|---|---|---|---|---|---|---|"]
    for tag, k in res["known_structure"].items():
        if not k.get("success"):
            L.append(f"| {tag} | indexing failed | | | | | |"); continue
        c = k["closest"] or {}
        L.append(f"| {tag} | {', '.join(f'{v:.3f}' for v in k['cell'])} | {k['volume']:.2f} | "
                 f"{c.get('name', '–')} ({c.get('verdict', '–')}) | "
                 f"{', '.join(f'{v:+.2f}' for v in c.get('d_len_pct', []))} | "
                 f"{', '.join(f'{v:+.2f}' for v in c.get('d_ang_deg', []))} | {c.get('d_vol_pct', float('nan')):+.1f} |")
    ab = res.get("ab_initio")
    if ab:
        L += ["", "## 4. Blind (free ab-initio) indexing of the aligned spots", ""]
        if ab.get("success"):
            c = ab["closest"] or {}
            L.append(f"Indexed {ab['n_indexed']} of {ab['n_reflections']} (chance level {ab['chance']:.0f}, "
                     f"score floor {ab['score_floor']:.0f}); cell {', '.join(f'{v:.3f}' for v in ab['cell'])}, "
                     f"V = {ab['volume']:.2f} Å³; closest known lattice: {c.get('name')} ({c.get('verdict')}, "
                     f"score {c.get('score', float('nan')):.2f}%).")
        else:
            L.append("Ab-initio indexing did not succeed: " + "; ".join(ab.get("notes") or []))
    return "\n".join(L) + "\n"


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project")
    ap.add_argument("--out", help="write the Markdown report here")
    ap.add_argument("--json", help="write the raw results here")
    ap.add_argument("--tol", type=float, default=0.10)
    ap.add_argument("--align-tol", type=float, default=0.08)
    ap.add_argument("--decoys", type=int, default=20)
    ap.add_argument("--control", type=int, default=10, help="aligned random-lattice controls (0 = skip)")
    ap.add_argument("--ab-initio", action="store_true")
    ap.add_argument("-q", "--quiet", action="store_true")
    a = ap.parse_args(argv)
    from midas_gui import project
    rec = project.read_solve_cell(a.project)
    if rec is None:
        print("no Solve Cell record in that project", file=sys.stderr)
        return 2
    t0 = time.time()
    res = identify(rec, tol=a.tol, align_tol=a.align_tol, n_decoys=a.decoys, n_control=a.control,
                   ab_initio=a.ab_initio, log=(lambda *_: None) if a.quiet else (lambda m: print(m, file=sys.stderr, flush=True)))
    md = format_markdown(res, a.project)
    if a.out:
        open(a.out, "w").write(md)
    if a.json:
        json.dump(res, open(a.json, "w"), indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))
    print(md)
    print(f"({time.time() - t0:.0f} s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
