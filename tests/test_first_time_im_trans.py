"""``run_pipeline("first_time", ...)`` must hand the image transform to the
backend, and must hand it over exactly once.

Until ``midas_calibrate_v2`` 0.15.0 no ``first_time`` entry point accepted an
``im_trans`` at all, so this branch quietly passed none: a first_time
calibration on a flipped detector ran in the wrong frame and returned a
confident wrong geometry, with nothing in the output to say so. 0.15.0 gave
``first_time_calibrate()`` a native ``im_trans`` (it flips image, dark and
panel_mask together and re-derives ``n_pixels_y/z`` from the transformed
shape), so the branch now forwards the codes and hands over the RAW frame.

The failure mode on the other side is just as silent: pre-flipping *and*
forwarding the codes applies the transform twice, and a double flip is a
perfectly plausible-looking image. So these pin both halves — the codes are
forwarded, and the array is not pre-transformed.
"""
import inspect

import numpy as np
import pytest

from midas_gui import calib


@pytest.fixture
def spy(monkeypatch):
    """Capture what run_pipeline hands to first_time_calibrate."""
    seen = {}

    def fake(image, **kwargs):
        seen["image"] = image
        seen["kwargs"] = kwargs
        # run_pipeline returns this straight through; normalize_result is not
        # exercised here, so a bare sentinel is enough.
        return "RESULT"

    import midas_calibrate_v2.pipelines as P
    monkeypatch.setattr(P, "first_time_calibrate", fake)
    return seen


def _cfg(**over):
    cfg = dict(wavelength=0.1729, pxY=200.0, pxZ=200.0, calibrant="CeO2",
               refine={}, n_iter=1, device="cpu")
    cfg.update(over)
    return cfg


# ── the codes reach the backend ───────────────────────────────────────────────

def test_im_trans_is_forwarded(spy):
    img = np.zeros((6, 9), dtype=np.float32)
    calib.run_pipeline("first_time", img, None, _cfg(im_trans=(2,)))
    assert spy["kwargs"].get("im_trans") == (2,)


def test_multiple_codes_are_forwarded_in_order(spy):
    img = np.zeros((6, 9), dtype=np.float32)
    calib.run_pipeline("first_time", img, None, _cfg(im_trans=(1, 3)))
    assert spy["kwargs"].get("im_trans") == (1, 3)


def test_no_transform_omits_the_kwarg_entirely(spy):
    """Back-compat: with nothing to apply, don't require a backend that
    accepts the kwarg at all."""
    img = np.zeros((6, 9), dtype=np.float32)
    calib.run_pipeline("first_time", img, None, _cfg(im_trans=()))
    assert "im_trans" not in spy["kwargs"]


# ── ...and the array is NOT pre-transformed ───────────────────────────────────

def test_image_is_handed_over_raw_not_pre_flipped(spy):
    """The double-flip guard. The backend applies the transform itself, so the
    array crossing this boundary must still be in the raw frame."""
    rng = np.random.default_rng(0)
    img = rng.random((6, 9)).astype(np.float32)
    calib.run_pipeline("first_time", img.copy(), None, _cfg(im_trans=(2,)))
    assert np.array_equal(spy["image"], img), "image was pre-transformed"


def test_pixel_counts_are_handed_over_raw(spy):
    """``n_pixels_y/z`` ride with the raw frame: the backend overwrites them
    from the transformed shape, so pre-swapping here would double-swap."""
    img = np.zeros((6, 9), dtype=np.float32)      # NZ=6, NY=9
    calib.run_pipeline("first_time", img, None, _cfg(im_trans=(3,)))
    assert (spy["kwargs"]["n_pixels_y"], spy["kwargs"]["n_pixels_z"]) == (9, 6)


# ── the unguarded kwarg is safe against the pinned backend ────────────────────

def test_installed_backend_accepts_im_trans():
    """run_pipeline passes ``im_trans`` unguarded (no _supported_kwargs), on
    purpose — silently dropping it is the bug being fixed. That is only safe
    while the pinned backend really declares it, so pin that here rather than
    discovering it as a TypeError mid-calibration."""
    from midas_calibrate_v2.pipelines import first_time_calibrate
    assert "im_trans" in inspect.signature(first_time_calibrate).parameters


# ── post-transform pixel counts ───────────────────────────────────────────────

@pytest.mark.parametrize("codes", [(), (1,), (2,), (3,), (1, 2), (2, 3), (1, 3),
                                   (3, 1), (3, 3), (1, 2, 3), (3, 2, 3)])
def test_effective_pixel_counts_matches_the_backend(codes):
    """``effective_pixel_counts`` must agree with the transform the backend
    actually applies — checked against it, not against a re-derivation."""
    from midas_calibrate_v2.io.transforms import apply_im_trans
    img = np.zeros((6, 9), dtype=np.float32)      # deliberately non-square
    _, _, _, ny, nz = apply_im_trans(img.copy(), None, None, codes)
    assert calib.effective_pixel_counts(img, codes) == (ny, nz)


def test_effective_pixel_counts_swaps_only_on_an_odd_transpose():
    img = np.zeros((6, 9), dtype=np.float32)      # NZ=6, NY=9
    assert calib.effective_pixel_counts(img, ()) == (9, 6)
    assert calib.effective_pixel_counts(img, (1, 2)) == (9, 6)   # mirrors only
    assert calib.effective_pixel_counts(img, (3,)) == (6, 9)
    assert calib.effective_pixel_counts(img, (3, 3)) == (9, 6)   # back again


def test_effective_pixel_counts_tolerates_none():
    img = np.zeros((6, 9), dtype=np.float32)
    assert calib.effective_pixel_counts(img, None) == (9, 6)
