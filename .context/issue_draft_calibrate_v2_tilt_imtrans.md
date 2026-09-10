# `tx/ty/tz` seeds and `im_trans` are honoured by disjoint sets of calibration pipelines — no entry point accepts both

**Package:** `midas-calibrate-v2` **0.13.0** (current PyPI latest, installed from PyPI)
**Also relevant:** `midas-calibrate` (v1 `CalibrationParams`), `midas-integrate-v2` 0.7.1
**Reported from:** MIDAS_GUI, which drives these pipelines and has had to work around both gaps.

---

## Summary

Two user-supplied inputs — the detector tilt guesses `tx/ty/tz`, and the MIDAS image
transform `ImTransOpt` / `im_trans` — are each supported by only *part* of the
calibration API, and by **complementary, non-overlapping parts**:

* `im_trans` is accepted **only** by `pipelines.auto.calibrate()` (and by
  `pipelines.ff_calibrate.calibrate_ff_from_files()`, which just delegates to it).
* Tilt seeds are accepted **only** by the eight pipelines that take a `v1_params`
  (`CalibrationParams`) object — which is every pipeline **except** `calibrate()`.

So there is currently **no entry point in the package that accepts both**. A detector that
needs a flip (`ImTransOpt 1/2/3`) *and* has a known non-zero tilt cannot be described to
any single pipeline. Callers must pre-transform the image themselves and hand the result
to a `v1_params` pipeline — which is exactly the error-prone bookkeeping `calibrate()`
already does correctly, internally, for its own callers.

Separately, `calibrate()` hardcodes `tx = ty = tz = 0.0`, so **`tx` is structurally
unreachable through `calibrate()`**: it can be neither seeded nor refined, and the returned
`AutoCalibrationResult.tx` is always exactly `0.0` for every input.

---

## Verified matrix (`midas-calibrate-v2` 0.13.0)

Produced with `inspect.signature()` against the installed package, plus a source read of
each pipeline's spec construction.

| Entry point | takes `v1_params` | `im_trans` accepted | `tx/ty/tz` seed honoured |
|---|---|---|---|
| `pipelines.auto.calibrate()` | no | **yes** | **no** — hardcoded `0.0` |
| `pipelines.ff_calibrate.calibrate_ff_from_files()` | no | **yes** | **no** — delegates to `calibrate()` |
| `pipelines.single.autocalibrate()` | yes | **no** | yes |
| `pipelines.single_pv.autocalibrate_pv()` | yes | **no** | yes |
| `pipelines.multi.autocalibrate_multi()` | yes | **no** | yes |
| `pipelines.bayesian.autocalibrate_bayesian()` | yes | **no** | yes |
| `pipelines.four_stage.autocalibrate_four_stage()` | yes | **no** | yes |
| `pipelines.joint_cake.autocalibrate_joint()` | yes | **no** | yes |
| `pipelines.nn_residual.autocalibrate_nn()` | yes | **no** | yes |
| `pipelines.robust.autocalibrate_robust()` | yes | **no** | yes (forwards to `single`) |
| `pipelines.first_time.first_time_calibrate()` | no | **no** | **no** — see note below |

Note the two "yes" columns never coincide on a single row. That is the core of this issue.

---

## Detail 1 — `im_trans` reaches only one pipeline

`grep -rn "im_trans\|ImTransOpt\|TransOpt"` over the whole package returns real handling in
exactly one place: `pipelines/auto.py:412-440`. That block is *correct and complete* — it
transforms the image, the dark **and** the mask together, validates the mask shape against
the transformed image, and only then derives `NZ, NY = image.shape`:

```python
# pipelines/auto.py:418-439
def _imtrans(arr):
    for opt in im_trans:
        if opt == 1:   arr = arr[:, ::-1]
        elif opt == 2: arr = arr[::-1, :]
        elif opt == 3: arr = arr.T
    return np.ascontiguousarray(arr)
if im_trans:
    image = _imtrans(image)
    if dark is not None:
        dark = _imtrans(dark)
    # The MASK must ride along. A mask left in the raw orientation while
    # the image is flipped masks the wrong pixels — silently, and worse
    # than no mask at all.
    if mask is not None:
        mask = _imtrans(mask)
...
NZ, NY = image.shape
```

Every other pipeline has no equivalent. And there is no object-level route either:

* `midas_calibrate.params.CalibrationParams` has **no `ImTransOpt` field** (confirmed via
  `dataclasses.fields`).
* `parameters.spec.CalibrationSpec` has **no transform field** either.
* `CalibrationParams.extra["ImTransOpt"]` — which `ff_calibrate.py:412-433` explicitly
  acknowledges is where the key lands when a v1 `paramstest.txt` is parsed — is **silently
  dropped** by `compat.from_v1.spec_from_v1_params()`. Verified:

```python
v1.extra["ImTransOpt"] = "2"
spec = spec_from_v1_params(v1)
# spec param names: ['Lsd','BC_y','BC_z','tx','ty','tz','a2',...,'Apothem','LatticeOrientation']
# -> no transform field anywhere; the ImTransOpt is gone.
```

So a caller of `autocalibrate_four_stage()` et al. has no way — kwarg, spec field, or
params field — to say "this detector is flipped".

**Consequence.** The caller must replicate the `auto.py` block above by hand. Doing it
*partly* right fails silently: the classic failure is transforming the image for the solve
but computing the auto-seed from the untransformed array, so seed and solve run in two
different frames. (We hit exactly this in MIDAS_GUI with Flip-Z + a multi-panel detector;
it is now centralised in one helper, but every downstream caller of this package has to
independently rediscover and re-implement it.)

## Detail 2 — `calibrate()` cannot be given tilts, and pins `tx` to zero

`calibrate()`'s signature has `initial_Lsd`, `BC_guess`, `initial_BC_y`, `initial_BC_z` —
but no tilt counterpart. Confirmed:

```python
from midas_calibrate_v2 import calibrate; import inspect
p = inspect.signature(calibrate).parameters
[k in p for k in ("initial_tx","initial_ty","initial_tz","tx","ty","tz","spec","v1_params")]
# -> [False, False, False, False, False, False, False, False]
```

There is not even a `spec=` escape hatch, so the internal `CalibrationParams` it builds
cannot be reached or overridden by the caller. It is constructed with the tilts wired to
literal zeros:

```python
# pipelines/auto.py:609-621
v1 = CalibrationParams(
    ...,
    tx=0.0, ty=0.0, tz=0.0,
    ...,
    Refine={"Lsd": True, "BC": True,
            "ty": bool(refine_tilts), "tz": bool(refine_tilts),
            ...},
)
```

`ty`/`tz` at least get refined from zero when `refine_tilts=True`, so for those the loss is
a *seed* (relevant when the true tilt is far enough from 0 to be outside the LM basin —
the same reason `initial_BC_*` exists). `tx` is different in kind: it is absent from the
`Refine` dict, and `compat/from_v1.py:49` freezes it unconditionally —

```python
_add(s, "tx", v1.tx, refined=False, tol=1e-6)   # v1: tx never refined
```

— so through `calibrate()`, `tx` is **never seeded and never refined**. It is not that `tx`
is unmodelled: `forward/geometry.py` documents "`tx` is refinable (v1 fixed it at 0)" and
`build_tilt_matrix(tx, ty, tz)` uses it in the full intrinsic Z-Y-X rotation. The forward
model supports it; the flagship entry point structurally cannot express it.

The `v1_params` pipelines *do* honour the seed — `spec_from_v1_params` carries the values
through as `init` (verified with `tx=3.5, ty=-1.25, tz=0.75`):

```
tx: init=3.5   refined=False  bounds=(3.499999, 3.500001)
ty: init=-1.25 refined=True   bounds=(-4.25, 1.75)
tz: init=0.75  refined=True   bounds=(-2.25, 3.75)
```

That is the behaviour we want everywhere. (`tx` staying `refined=False` there is defensible
as v1 parity — the user supplies it as a known fixed quantity. Worth exposing a `thaw`
route, but it is not the main ask.)

## Detail 3 — `first_time_calibrate()` honours neither

`first_time_calibrate()` has a `tilt_prior_deg=(ty, tz)` parameter, but it is **not** an
initial value for the fit. It is consumed in exactly one place — `first_time.py:636-660` —
to steer `cone_aware_bc_refine_with_tilt_prior()`, i.e. it improves the *beam-centre* seed
only. The `V1Params` the pipeline actually fits with is built by `_build_v1()`
(`first_time.py:98-135`), which never assigns `tx`, `ty` or `tz`, so all three fall to the
`CalibrationParams` default of `0.0`. There is also no `tx` input of any kind, and no
`im_trans`.

## Detail 4 — the applied transform is not recorded on the result

`AutoCalibrationResult` has no `im_trans` field (`dataclasses.fields` confirms), so even
when `calibrate()` *does* apply a transform, the frame the result lives in is not
recoverable from the result. Downstream, `compat.to_integrate.spec_from_calibration_result()`
correspondingly sets no `TransOpt` on the `IntegrationSpec`, so integration cannot infer it
either and every consumer must carry the transform separately, by hand, in parallel with
the result object. (MIDAS_GUI patches `spec.TransOpt` manually after that call for this
reason.)

---

## Why this matters

1. **Silent, not loud.** Every one of these is a wrong-answer failure, not an exception. A
   dropped tilt seed gives a converged-looking fit in the wrong basin. A mask left in the
   raw orientation masks the wrong pixels. Nothing raises.
2. **It pushes frame bookkeeping onto every caller.** The `auto.py` block is ~20 lines and
   has three separate correctness traps (dark must ride along; mask must ride along;
   `NrPixelsY/Z` must come from the transformed shape). Asking each downstream project to
   reimplement it is asking each to reintroduce the same bugs.
3. **The capabilities are artificially split.** Nothing about `im_trans` is specific to the
   one-shot algorithm, and nothing about tilt seeding is specific to the four-stage one.
   The split looks incidental to how the API grew, not intentional.

---

## Proposed fixes

Ordered smallest-first; A and B are independent and each is useful alone. All are
backward-compatible (new keyword arguments defaulting to today's behaviour).

### A. Give `calibrate()` tilt seeds — mirrors an existing convention

Add to `pipelines.auto.calibrate()`:

```python
initial_tx: float = 0.0,
initial_ty: float = 0.0,
initial_tz: float = 0.0,
refine_tx: bool = False,      # optional; forward model already supports it
```

and use them at `auto.py:609`:

```python
v1 = CalibrationParams(
    ...,
    tx=float(initial_tx), ty=float(initial_ty), tz=float(initial_tz),
    Refine={"Lsd": True, "BC": True,
            "ty": bool(refine_tilts), "tz": bool(refine_tilts), ...},
)
```

Defaults of `0.0` reproduce current behaviour exactly. This is not a new capability —
`CalibrationParams` already accepts and uses these, and the sibling pipelines already
exercise that path. It is plumbing `calibrate()` through to the mechanism underneath it,
alongside the `initial_Lsd` / `initial_BC_y` / `initial_BC_z` kwargs already there.

If `refine_tx` is added, it also needs `spec_from_v1_params` to stop hardcoding
`refined=False` for `tx` — e.g. honour `v1.Refine.get("tx", False)`, which keeps v1 parity
by default while making the forward model's existing `tx` support reachable.

### B. Give `im_trans` a home that all pipelines can see

Two options; **B1 preferred.**

**B1 — put it on the spec (recommended).** Add `im_trans: tuple = ()` to
`CalibrationSpec`, carry it in `spec_from_v1_params()` from
`v1.extra["ImTransOpt"]` (parsing already exists in `ff_calibrate._im_trans_from_template`),
and apply it once at each pipeline's entry point. This makes the transform part of the
*calibration description* rather than a per-call argument, so it can be recorded,
round-tripped through a v1 `paramstest.txt`, and read by downstream consumers.

**B2 — add the kwarg to each pipeline.** Add `im_trans: Sequence[int] = ()` to the eight
`v1_params` pipelines. Less invasive, but it must be passed correctly at every call site
and still cannot be recovered from the spec afterwards.

**In either case, factor out the transform.** Lift `auto.py:418-439` into one shared
internal helper and call it from every entry point, so there is a single implementation of
the image/dark/mask/shape consistency rule:

```python
# e.g. midas_calibrate_v2/io/transforms.py
def apply_im_trans(image, dark=None, mask=None, im_trans=()):
    """Returns (image, dark, mask, NrPixelsY, NrPixelsZ), all in the same frame."""
```

`io/readers.py:86-155` already contains a second copy of this loop, so this consolidates
three implementations into one.

### C. Record the transform on the result and carry it downstream

* Add `im_trans: tuple = ()` to `AutoCalibrationResult`, set to whatever was applied.
* Have `compat.to_integrate.spec_from_calibration_result()` set `IntegrationSpec.TransOpt`
  from it.

This closes the loop so a calibration result is self-describing and integration does not
have to be told the frame a second time.

### D. Make `first_time_calibrate()` consistent

Have `_build_v1()` accept and set `tx/ty/tz` (feeding `tilt_prior_deg` into `ty/tz` as a
genuine LM initial value, in addition to its current beam-centre role), add a `tx` input,
and accept `im_trans` via whichever mechanism B lands on.

---

## Reproduction

No dataset needed — the gaps are visible in the signatures and in the constructed spec.

```python
import inspect, numpy as np
from midas_calibrate.params import CalibrationParams as V1
from midas_calibrate_v2 import calibrate, AutoCalibrationResult
from midas_calibrate_v2.compat.from_v1 import spec_from_v1_params
from midas_calibrate_v2.pipelines import autocalibrate_four_stage
import dataclasses

# 1. calibrate() accepts im_trans but no tilt seed and no spec override
p = inspect.signature(calibrate).parameters
assert "im_trans" in p
assert not any(k in p for k in
               ("initial_tx","initial_ty","initial_tz","tx","ty","tz","spec","v1_params"))

# 2. the v1_params pipelines accept a tilt seed but reject im_trans
try:
    autocalibrate_four_stage(None, np.zeros((4, 4)), im_trans=(2,))
except TypeError as e:
    print(e)   # got an unexpected keyword argument 'im_trans'

# 3. ImTransOpt cannot ride along on the params object either
v1 = V1(NrPixelsY=8, NrPixelsZ=8, pxY=200.0, pxZ=200.0, Lsd=1e6, BC_y=4, BC_z=4,
        Wavelength=0.17, SpaceGroup=225,
        LatticeConstant=(5.41,)*3 + (90.0,)*3, MaxRingRad=3.0)
v1.extra["ImTransOpt"] = "2"
spec = spec_from_v1_params(v1)
assert not [n for n in spec.parameters if "rans" in n.lower()]      # dropped
assert not hasattr(spec, "TransOpt")

# 4. the transform is not recorded on the result
assert not [f.name for f in dataclasses.fields(AutoCalibrationResult)
            if "rans" in f.name.lower()]
```

## Environment

```
midas-calibrate-v2   0.13.0     midas-integrate-v2   0.7.1
midas-calibrate      0.5.0      midas-integrate      0.8.0
midas-params         0.12.0     midas-hkls           0.10.0
Python 3.12.13, numpy 1.26.4, torch 2.4.0, macOS (darwin 23.6.0)
```
