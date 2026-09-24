"""Batch Integrate's detector-map cache key (``BatchTab._integration_signature``).

The detector map is expensive to build and is a property of the *geometry* —
``workers.build_integration_context`` takes only (spec, kernel, mask,
corrections, weighted) and never sees the data source. Two consequences this
file pins, because getting either wrong is silent:

* Keying on the source path throws the map away every time the user points the
  tab at a different file under the same calibration — pure waste, no symptom.
* Leaving Eta min/max *out* of the key reuses a stale map after the user
  narrows the azimuthal range — wrong results, no symptom.

Builds a ``BatchTab``, hence forked, with every Qt-pulling import deferred into
the ``app`` fixture — see STATE.md on module-level PyQt5 imports and the
forked-child CoreFoundation crash. ``tests/test_set_raw_frame.py`` is the
reference shape.
"""
import pytest

pytestmark = pytest.mark.forked


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def tab(app):
    from midas_gui.tab_batch import BatchTab
    t = BatchTab()
    # "From file" with a fixed (nonexistent) path keeps the calibration half of
    # the key constant and independent of any live Tab-2 result object's id().
    t._use_json_btn.setChecked(True)
    t._json_ed.setText("/calib/ceria.txt")
    return t


CORR = (None, None)          # (polarisation, solid-angle) — both off
HDF5 = {"type": "hdf5", "path": "/d/s1/scan_001.h5", "dataset": "exchange/data"}
OTHER = {"type": "hdf5", "path": "/d/s2/scan_999.h5", "dataset": "other/data"}
FOLDER = {"type": "tiff_glob", "path": "/d/tiffs/sampleA"}


def _sig(tab, src_cfg=HDF5, kernel="subpixel4", corrections=CORR, weighted=True):
    return tab._integration_signature(src_cfg, kernel, corrections, weighted)


# ── the source must not participate ──────────────────────────────────

def test_a_different_source_reuses_the_same_detector_map(tab):
    """The whole point: same geometry, different sample → same key.

    This is what lets the Batch Queue share one built map across every sample
    under a calibration, and what stops the interactive tab rebuilding on every
    file switch."""
    assert _sig(tab, HDF5) == _sig(tab, OTHER)


def test_even_a_different_source_type_reuses_it(tab):
    """An HDF5 container and a folder of TIFFs are read differently but
    integrate through the identical detector map."""
    assert _sig(tab, HDF5) == _sig(tab, FOLDER)


def test_no_source_field_leaks_into_the_key(tab):
    """Guards the regression directly rather than only its consequences."""
    sig = _sig(tab, HDF5)
    flat = [x for item in sig for x in (item if isinstance(item, tuple) else (item,))]
    for leaked in (HDF5["path"], HDF5["dataset"], HDF5["type"]):
        assert leaked not in flat


# ── everything that does affect the map still does ───────────────────

def test_eta_range_changes_the_key(tab):
    """Eta min/max feed _build_spec, and the context's eta axis is derived from
    them — a stale map here means wrong output, not just slow output."""
    before = _sig(tab)
    tab._eta_min.setValue(-90.0)
    assert _sig(tab) != before
    mid = _sig(tab)
    tab._eta_max.setValue(90.0)
    assert _sig(tab) not in (before, mid)


@pytest.mark.parametrize("attr, value", [
    ("_r_bin", 2.5), ("_e_bin", 2.0), ("_r_min", 25.0), ("_r_max", 900.0),
])
def test_binning_changes_the_key(tab, attr, value):
    """Each `value` must differ from the widget's default (r_bin 1.0, e_bin 5.0,
    r_min/r_max 0.0), or setValue is a no-op and the test asserts nothing."""
    before = _sig(tab)
    getattr(tab, attr).setValue(value)
    assert getattr(tab, attr).value() == value, "value equals the default — pick another"
    assert _sig(tab) != before


def test_kernel_changes_the_key(tab):
    assert _sig(tab, kernel="subpixel4") != _sig(tab, kernel="nearest")


def test_weighting_changes_the_key(tab):
    assert _sig(tab, weighted=True) != _sig(tab, weighted=False)


def test_corrections_being_enabled_changes_the_key(tab):
    """build_integration_context branches on whether either correction is on
    (it skips build_geom entirely when they are), so the key must too."""
    import numpy as np
    pol = np.ones((4, 4), dtype=np.float32)
    assert _sig(tab, corrections=(pol, None)) != _sig(tab, corrections=CORR)
    assert _sig(tab, corrections=(None, pol)) != _sig(tab, corrections=CORR)


def test_calibration_file_changes_the_key(tab):
    before = _sig(tab)
    tab._json_ed.setText("/calib/agbh.txt")
    assert _sig(tab) != before


def test_the_key_is_hashable_and_stable(tab):
    """It is used as a dict/equality key across runs — an unhashable or
    non-reproducible element would break the cache silently."""
    first = _sig(tab)
    assert hash(first) == hash(_sig(tab))
    assert first == _sig(tab)
