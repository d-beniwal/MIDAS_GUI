# midas-pdf knowledge stack

Compressed reference for the `midas_pdf` package, built to support adding real PDF
functionality to midas-gui. Read in order:

1. **`00_overview.md`** — what midas-pdf is, the reuse-not-reimplement architecture, the
   dependency chain, module map, conventions, env gotchas, source/test-data locations, raw
   `.vrx.h5` format.
2. **`01_core_api.md`** — the pixels→G(r) path: `Composition`, `faber_ziman_S`, `i_of_q_to_Gr`,
   sine FT, `image_to_iq/image_to_Gr`, `refine_normalization`, `conventions`, `deltapdf`. **Start here for the GUI.**
3. **`02_corrections_and_ms.md`** — Compton, detector efficiency, self-absorption, Paalman-Pings,
   fluorescence diagnostic, cross-section, ionic form factors, tiered multiple scattering.
4. **`03_modeling.md`** — structure fit (`pdffit_gr`/`refine_structure`), aniso/occupancy, Bayesian
   posteriors, multi-phase/core-shell, model comparison, validate, CIF, RMC, SAXS, the 6 CLI scripts.
5. **`04_workflows.md`** — end-to-end recipes; the real 5-sample beamline pipeline; which helpers are
   package functions vs demo-local recipes (absolute-normalize, tail-flatten).
6. **`05_gui_integration.md`** — current GUI PDF tab/PDFWorker state, the gap, staged plan, data
   plumbing, test data to drive it.

Key facts to remember:
- All torch **float64**; `S(Q)→1` at high Q; `G(r)=(2/π)∫Q[S−1]sin(Qr)W(Q)dQ`; default window Lorch.
- The sine FT + `R_px_to_Q` live in **midas_integrate_v2.pdf** (re-exported by `gr.py`).
- The one genuinely new physics is the **polyatomic Faber-Ziman composition layer**.
- **Absolute normalization** and **S(Q) tail-flattening** are demo recipes, NOT package functions;
  `refine_normalization` is the principled alternative.
- Package source: `/Users/dbeniwal/ANL-research/midas_pdf_src/midas_pdf/`;
  test data: `/Users/dbeniwal/ANL-research/midas_pdf_src/midas_pdf_test/`
  (moved out of `~/Downloads`, which macOS TCC blocks the agent from reading).
