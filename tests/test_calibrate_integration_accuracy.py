"""The Calibrate tab's radial profile and η–R cake are the *accurate* pipeline.

The Data Viewer grew an explicit "Accurate" tick (2026-09-10) that swaps its
fast live-view profile for the Batch-Integrate pipeline verbatim — the MIDAS
engine, a spec from ``spec_from_calibration_result``, and the ``subpixel2``
kernel. The Calibrate tab has no such tick because it has no live-view budget
to protect: it integrates once, on demand, after a fit, and so has always run
the accurate path.

"Always" is the part worth pinning. Nothing structural stops someone dropping
the Calibrate worker to the cheaper ``hard`` kernel or the unweighted η-bin
mean for speed, and the symptom would be a profile that quietly disagrees with
the Batch run the user compares it against — peak positions shifted by a
fraction of a bin on a tilted geometry, which reads as a calibration problem
rather than an integration one.

So this asserts equality against the Data Viewer's accurate path, computed
here from the same module-level functions ``DetectorGeometryCard._midas_radial``
calls, rather than re-deriving what either *should* produce.

``IntegrationWorker`` is a QThread, but ``run()`` is an ordinary method and the
work inside it touches no Qt object beyond the two result signals — so it runs
here with no QApplication and no widgets, and the file needs neither the fork
marker nor the deferred-Qt-import dance (see STATE.md).
"""
import numpy as np
import pytest


R_BIN, ETA_BIN = 1.0, 5.0


@pytest.fixture(scope="module")
def geometry():
    """A tilted, mildly distorted 256×256 detector — tilt and distortion are
    exactly what the two kernels disagree about, so a flat geometry would let
    an inaccurate path pass."""
    from types import SimpleNamespace
    return SimpleNamespace(
        Lsd=200_000.0, BC_y=131.4, BC_z=124.9, tx=0.3, ty=-1.1, tz=0.8,
        pxY=200.0, pxZ=200.0, NrPixelsY=256, NrPixelsZ=256,
        wavelength_A=0.1729, distortion={"iso_R2": 2e-3, "a2": 1e-3, "phi2": 20.0},
        residual_corr_bin_path=None, im_trans=[], panel_layout=None,
        panel_shifts_path=None, post_residual_strain_uE=None)


@pytest.fixture(scope="module")
def frame(geometry):
    """Concentric rings, so the profile has real structure to disagree about."""
    nz, ny = geometry.NrPixelsZ, geometry.NrPixelsY
    zz, yy = np.mgrid[0:nz, 0:ny]
    r = np.hypot(yy - geometry.BC_y, zz - geometry.BC_z)
    img = np.zeros((nz, ny), dtype=np.float64)
    for r0 in (25.0, 47.0, 76.0, 103.0):
        img += 1000.0 * np.exp(-0.5 * ((r - r0) / 1.5) ** 2)
    return img + 5.0


def _viewer_accurate_path(geometry, img):
    """What ``DetectorGeometryCard._midas_radial`` computes with the "Accurate"
    tick on — the same calls, without the widget around them."""
    import torch
    from midas_gui.helpers import _spec_from_result_ns
    from midas_gui.hydra_geometry_card import ACCURATE_KERNEL
    from midas_gui.workers import build_integration_context, integrate_frame

    spec = _spec_from_result_ns(
        R_BIN, ETA_BIN, NrPixelsY=geometry.NrPixelsY, NrPixelsZ=geometry.NrPixelsZ,
        pxY=geometry.pxY, pxZ=geometry.pxZ, Lsd=geometry.Lsd,
        BC_y=geometry.BC_y, BC_z=geometry.BC_z,
        tx=geometry.tx, ty=geometry.ty, tz=geometry.tz,
        wavelength_A=geometry.wavelength_A, distortion=geometry.distortion,
        im_trans=())
    ctx = build_integration_context(spec, ACCURATE_KERNEL, None, (None, None), weighted=True)
    prof, _, cake, _ = integrate_frame(
        torch.from_numpy(np.ascontiguousarray(img, dtype=np.float64)),
        spec, ctx["geom"], ACCURATE_KERNEL, (None, None), None, False,
        corr_counts=ctx["corr_counts"], return_cake=True,
        weighted=True, cnt_cake=ctx["cnt"])
    return ctx["r_ax"], prof, cake


@pytest.fixture(scope="module")
def calibrate_result(geometry, frame):
    """What the Calibrate tab's own IntegrationWorker emits. Run synchronously
    — ``run()`` is an ordinary method; only ``start()`` needs a thread."""
    from midas_gui.workers import IntegrationWorker
    out = {}
    worker = IntegrationWorker(geometry, frame, None, (), r_bin=R_BIN, eta_bin=ETA_BIN,
                               weighted=True)
    worker.finished.connect(out.update)
    worker.failed.connect(lambda msg: pytest.fail(f"IntegrationWorker failed:\n{msg}"))
    worker.run()
    assert out, "IntegrationWorker emitted neither finished nor failed"
    return out


def test_calibrate_radial_profile_equals_the_data_viewers_accurate_path(
        calibrate_result, geometry, frame):
    r_ax, prof, _ = _viewer_accurate_path(geometry, frame)
    np.testing.assert_allclose(calibrate_result["r_axis_px"], r_ax, rtol=0, atol=0)
    np.testing.assert_allclose(calibrate_result["profile"], prof, rtol=0, atol=0)


def test_calibrate_cake_equals_the_data_viewers_accurate_path(
        calibrate_result, geometry, frame):
    _, _, cake = _viewer_accurate_path(geometry, frame)
    assert calibrate_result["cake_2d"] is not None
    np.testing.assert_array_equal(
        np.nan_to_num(calibrate_result["cake_2d"]), np.nan_to_num(cake))


def test_the_accurate_kernel_is_the_one_batch_integrate_uses():
    """The equality above is only worth anything if both sides are the kernel
    a Batch run would use — otherwise they could agree on being wrong."""
    from midas_gui.constants import DEFAULT_KERNEL
    from midas_gui.hydra_geometry_card import ACCURATE_KERNEL
    assert ACCURATE_KERNEL == DEFAULT_KERNEL == "subpixel2"


def test_the_profile_is_pixel_count_weighted_not_an_eta_bin_mean(
        calibrate_result, geometry, frame):
    """The two collapses differ wherever η coverage is uneven. Pinning which
    one ran keeps the Azim. mean default ("Pixel-weighted") honest."""
    import torch
    from midas_gui.helpers import _build_spec
    from midas_gui.workers import build_geom, integrate_frame

    spec = _build_spec(geometry, R_BIN, ETA_BIN)
    geom = build_geom(spec, "subpixel2", None)
    unweighted, _, _, _ = integrate_frame(
        torch.from_numpy(np.ascontiguousarray(frame, dtype=np.float64)),
        spec, geom, "subpixel2", (None, None), None, False,
        return_cake=True, weighted=False, cnt_cake=None)
    assert not np.allclose(calibrate_result["profile"], unweighted), (
        "weighted and unweighted collapses are indistinguishable on this "
        "geometry — the test cannot tell which one ran")
