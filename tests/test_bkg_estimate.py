"""Pump Probe background estimation: numerics (bkg_estimate) + tab wiring."""
import numpy as np
import pytest

from midas_gui import bkg_estimate as be


def _frames(tmp_path, n=5, shape=(64, 64)):
    tifffile = pytest.importorskip("tifffile")
    rng = np.random.default_rng(0)
    paths, imgs = [], []
    for i in range(n):
        img = (100 + 5 * i + rng.normal(0, 1, shape)).astype(np.float32)
        p = tmp_path / f"f{i}.tif"
        tifffile.imwrite(str(p), img)
        paths.append(str(p)); imgs.append(img)
    return paths, np.stack(imgs)


@pytest.mark.parametrize("op,fn", [("mean", np.mean), ("sum", np.sum), ("max", np.max),
                                   ("min", np.min), ("median", np.median)])
def test_projection_matches_numpy(tmp_path, op, fn):
    paths, stack = _frames(tmp_path)
    out = be.project_frames(paths, op)
    assert out.shape == stack.shape[1:]
    np.testing.assert_allclose(out, fn(stack, axis=0), rtol=1e-5)


def test_projection_cancel_and_errors(tmp_path):
    paths, _ = _frames(tmp_path, n=3)
    with pytest.raises(be.Cancelled):
        be.project_frames(paths, "mean", should_stop=lambda: True)
    with pytest.raises(ValueError):
        be.project_frames([], "mean")
    with pytest.raises(ValueError):
        be.project_frames(paths, "bogus")


def test_median_subsamples_large_stacks():
    seen = []
    be.project_frames([str(i) for i in range(200)], "median",
                      loader=lambda p: (seen.append(p), np.zeros((4, 4)))[1])
    assert len(seen) == be.MEDIAN_MAX_FRAMES


def test_snip_removes_peaks_keeps_smooth_background():
    yy, xx = np.mgrid[:128, :128]
    smooth = 200 + 0.5 * xx + 0.3 * yy
    img = smooth.copy()
    img[60:64, :] += 2000            # a "ring"
    img[20, 30] += 5000              # a "spot"
    bg = be.snip_2d(img, 20)
    assert bg[62, 64] < smooth[62, 64] * 1.1          # ring clipped
    assert bg[20, 30] < smooth[20, 30] * 1.1          # spot clipped
    away = (slice(80, 120), slice(10, 120))
    assert abs(bg[away] / smooth[away] - 1).max() < 0.1   # smooth part preserved
    assert (bg >= 0).all()


def test_snip_masked_pixels_do_not_pull_to_zero():
    img = np.full((64, 64), 300.0)
    mask = np.zeros_like(img, bool); mask[10:20, 10:20] = True
    img[mask] = 0
    bg = be.snip_2d(img, 10, mask)
    assert bg[15, 15] > 250


def test_median_2d_removes_rings_keeps_smooth_background():
    yy, xx = np.mgrid[:128, :128]
    smooth = 200 + 0.5 * xx + 0.3 * yy
    img = smooth.copy()
    img[60:63, :] += 2000            # ring narrower than the window
    img[20, 30] += 5000
    bg = be.median_2d(img, 21, 3)
    assert abs(bg[61, 64] / smooth[61, 64] - 1) < 0.15
    assert abs(bg[20, 30] / smooth[20, 30] - 1) < 0.15
    assert bg.shape == img.shape and np.isfinite(bg).all()


def test_median_2d_downsampled_path_and_mask():
    img = np.full((1100, 1030), 300.0)           # >= 1024 -> 4x downsample path
    img[500:503, :] += 3000
    mask = np.zeros(img.shape, bool); mask[:40, :] = True
    img[mask] = 0
    bg = be.median_2d(img, 51, 3, mask)
    assert bg.shape == img.shape
    assert abs(bg[501, 500] - 300) < 30 and abs(bg[10, 10] - 300) < 30
    assert be.refine(img, "median", median_kernel=51, median_iters=1).shape == img.shape


def test_refine_and_effective():
    img = np.random.default_rng(1).normal(100, 10, (32, 32))
    assert be.refine(img, "none") is not None
    sm = be.refine(img, "gauss", sigma=3.0)
    assert sm.std() < img.std()
    with pytest.raises(ValueError):
        be.refine(img, "nope")
    np.testing.assert_allclose(be.effective_background(img, 2.0, 5.0), 2 * img + 5)


@pytest.fixture(scope="session")
def qapp_keepalive():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_tab_background_flow(qapp_keepalive, tmp_path):
    try:
        from midas_gui.tab_pumpprobe import PumpProbeTab
    except Exception as exc:
        pytest.skip(f"needs the full MIDAS stack: {exc}")
    paths, stack = _frames(tmp_path, n=4)
    tab = PumpProbeTab()
    tab._frames = [(p, float(i - 1), 1.0) for i, p in enumerate(paths)]
    tab._det_set_range()
    assert tab._det_slider.maximum() == 3 and tab._bkg_frame.maximum() == 3
    # card off -> no calculated background, loader's (None here) is used
    assert tab._bkg_effective() is None
    tab._bkg_card.setChecked(True)
    assert tab._bkg_body.isVisible() or not tab.isVisible()
    # Detector tab shows a frame
    tab._det_refresh()
    assert tab._det_view._data.shape == stack.shape[1:]
    # compute (run the worker body synchronously) -> median projection
    from midas_gui.workers import PumpBackgroundWorker
    w = PumpBackgroundWorker(tab._bkg_paths(), "median", "none")
    out = {}
    w.finished.connect(lambda r, f: out.update(ref=r, final=f))
    w.run()
    tab._bkg_on_done(out["ref"], out["final"])
    np.testing.assert_allclose(tab._bkg_final, np.median(stack, axis=0), rtol=1e-5)
    tab._bkg_scale.setValue(2.0); tab._bkg_offset.setValue(3.0)
    np.testing.assert_allclose(tab._field_background(), 2 * tab._bkg_final + 3)
    # changing a setting invalidates; reference-only subset with none selected errors
    tab._bkg_op.setCurrentIndex(1)
    assert tab._bkg_final is None and tab._bkg_effective() is None
    tab._bkg_refine.setCurrentIndex(tab._bkg_refine.findData("median"))
    assert tab._bkg_mkernel.isEnabled() and not tab._bkg_snip.isEnabled()
    w = PumpBackgroundWorker(tab._bkg_paths(), "mean", "median", median_kernel=9, median_iters=2)
    w.finished.connect(lambda r, f: out.update(final=f)); w.run()
    assert out["final"].shape == stack.shape[1:]
    # single-image source
    tab._bkg_src_single.setChecked(True)
    tab._bkg_frame.setValue(2)
    assert tab._bkg_paths() == [paths[2]]
    # state round trip
    st = tab.get_state()["fields"]
    assert st["bkg_on"] is True and st["bkg_src_single"] is True and st["bkg_frame"] == 2
    tab.deleteLater()
