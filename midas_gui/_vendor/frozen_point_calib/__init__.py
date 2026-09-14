"""Temporary vendored copy of the "frozen-point, high-tilt" calibration
pipeline from an unmerged MIDAS branch.

**Provenance.** Source: ``github.com/d-beniwal/MIDAS``, branch
``frozen-point-high-tilt-calibration``, tip commit
``bca0500812ec42ca03a660adf1f330a6d3abce50`` (2026-09-14, updated from the
original ``9df2999f`` vendoring of 2026-09-10 to pick up
``iterate_frozen_point_until_stable`` distortion support). That branch adds
``pipelines/frozen_point.py`` + ``forward/point_pick.py`` to
``midas_calibrate_v2`` on top of base commit ``76cdbf99`` — the exact commit
that shipped as PyPI ``midas-calibrate-v2==0.17.0``, the version midas-gui is
pinned to. As of this vendoring, ``midas_calibrate_v2`` upstream has not
merged the branch, so the GUI can't just bump its pin to get it.

**Distortion support (2026-09-14).** Both ``autocalibrate_frozen_point`` and
``iterate_frozen_point_until_stable`` now defer to ``v1_params.Refine
["p0".."p14"]`` for distortion refinement, exactly like every sibling
pipeline (``autocalibrate_pv``/``_four_stage``/``_bayesian``/``_joint``) —
which is also exactly what ``midas_gui.calib.build_v1_params(...,
refine=...)`` already sets from the Calibrate tab's Distortion checkboxes.
Previously ``iterate_frozen_point_until_stable`` force-froze all 15
coefficients every iteration regardless of ``v1_params.Refine``; that's the
bug this update fixes, so no ``midas_gui.calib`` dispatch changes were
needed beyond this vendored copy — the GUI's existing distortion selection
just starts taking effect. There's also a ``refine_distortion`` kwarg now
(same selector as ``calibrate()``'s own) for a caller who'd rather not
build a ``Refine`` dict; ``midas_gui.calib`` doesn't pass it, relying on
``v1_params.Refine`` alone like it already does for ``four_stage``/
``bayesian``/``joint``. Per upstream's own docstring: refining distortion
inside the *iterative* wrapper is newer, less-validated territory than the
geometry-only mode this pipeline was originally proven on — a
still-converging early iteration re-fits distortion against a still-wrong
geometry each time, with no continuity between iterations. ``tx`` remains
always frozen regardless (a real physical identifiability limit, not a
distortion-support gap).

**Why this is safe to vendor as-is.** Every symbol the two files below import
from ``midas_calibrate_v2`` internals already exists, unchanged, in the
installed 0.17.0 package (verified directly against the ``midas-gui`` conda
env) — the branch only *adds* two new files, it doesn't otherwise touch the
forward model / LM solver / spec machinery they depend on. This includes
``forward.distortion.resolve_distortion_block`` (added by the 2026-09-14
distortion-support commit above) — already present in 0.17.0, since it's
the same selector ``pipelines.auto.calibrate()`` has used all along. The
one exception is ``_filter_by_snr``, which the real upstream PR promotes
from ``pipelines.single_pv`` to ``pipelines._common``; the vendored
``frozen_point.py`` here imports it from ``single_pv`` instead (where 0.17.0
still has it) rather than carrying that refactor.

**Removal.** Once ``midas_calibrate_v2`` merges this pipeline and midas-gui's
pin moves past that release: delete this whole ``_vendor/frozen_point_calib``
directory, drop the ``"frozen_point"`` entries from
``midas_gui.constants.PIPELINES`` and the corresponding branches in
``midas_gui.calib`` (``run_pipeline``, ``normalize_result``,
``tilt_seed_effective``), and re-point the Calibrate tab's dropdown label at
the real ``midas_calibrate_v2.pipelines.autocalibrate_frozen_point`` /
``iterate_frozen_point_until_stable``.

**Compatibility guard.** If the installed ``midas_calibrate_v2`` is not the
verified baseline, log a note rather than fail hard — the private internals
this leans on may well still match a nearby release, but a bump is exactly
when they'd silently drift.
"""
from __future__ import annotations

_VERIFIED_BASELINE = "0.17.0"


def _check_backend_compat() -> None:
    try:
        import midas_calibrate_v2
        installed = getattr(midas_calibrate_v2, "__version__", None)
    except Exception:
        return
    if installed is not None and installed != _VERIFIED_BASELINE:
        print(
            f"[frozen_point_calib] note: vendored against "
            f"midas-calibrate-v2=={_VERIFIED_BASELINE}, but "
            f"{installed} is installed — the frozen-point (high-tilt) "
            "pipeline leans on that package's private internals, which may "
            "have drifted. If it errors, check "
            "midas_gui/_vendor/frozen_point_calib/__init__.py.",
            flush=True,
        )


_check_backend_compat()

from .frozen_point import (  # noqa: E402
    autocalibrate_frozen_point,
    iterate_frozen_point_until_stable,
    IterateResult,
)

__all__ = [
    "autocalibrate_frozen_point",
    "iterate_frozen_point_until_stable",
    "IterateResult",
]
