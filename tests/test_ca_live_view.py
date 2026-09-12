"""Tests for the EPICS Channel Access (CA) live-view backend added alongside
the existing PVA one (see midas_gui/live_sources.py). No real EPICS
connection (CA or PVA) is opened anywhere here -- these are pure logic/UI
tests, covering: backend-selection factory, the NDPluginStdArrays reshape
math, and backward-compatible config parsing across constants/settings/
prefs_dialog (every device dict predating the "backend"/"ca_suffix" fields
must keep resolving to plain PVA, unchanged).
"""
import numpy as np
import pytest

from midas_gui import live_sources


# ── factory selection ───────────────────────────────────────────────────
@pytest.mark.parametrize("backend,expected", [
    ("ca", live_sources.CaLiveSource),
    ("CA", live_sources.CaLiveSource),
    (" ca ", live_sources.CaLiveSource),
    ("pva", live_sources.PvaLiveSource),
    ("", live_sources.PvaLiveSource),
    (None, live_sources.PvaLiveSource),
    ("bogus", live_sources.PvaLiveSource),
])
def test_create_live_source_selects_backend(backend, expected):
    pytest.importorskip("PyQt5.QtWidgets")
    src = live_sources.create_live_source(backend)
    assert isinstance(src, expected)


# ── NDPluginStdArrays reshape math ──────────────────────────────────────
def test_reshape_std_arrays_mono():
    height, width = 5, 8
    raw = np.arange(height * width, dtype=np.uint16)
    out = live_sources._reshape_std_arrays(raw, width, height, 1, color_mode=0)
    assert out.shape == (height, width)
    assert out.dtype == np.float32
    assert np.array_equal(out, raw.reshape(height, width).astype(np.float32))


def test_reshape_std_arrays_mono_drops_size2():
    # size2 is unused/ignored for Mono -- must not affect the result.
    height, width = 3, 4
    raw = np.arange(height * width, dtype=np.uint16)
    out = live_sources._reshape_std_arrays(raw, width, height, 999, color_mode=0)
    assert out.shape == (height, width)


def test_reshape_std_arrays_rgb1():
    # dims = [3, NX, NY] -> flat order already matches (NY, NX, 3)
    height, width = 4, 3
    raw = np.arange(height * width * 3, dtype=np.uint8)
    out = live_sources._reshape_std_arrays(raw, 3, width, height, color_mode=2)
    assert out.shape == (height, width, 3)
    assert np.array_equal(out, raw.reshape(height, width, 3).astype(np.float32))


def test_reshape_std_arrays_rgb3():
    # dims = [NX, NY, 3] -> flat order is (3, NY, NX), color must move last
    height, width = 4, 3
    raw = np.arange(width * height * 3, dtype=np.uint8)
    out = live_sources._reshape_std_arrays(raw, width, height, 3, color_mode=4)
    assert out.shape == (height, width, 3)
    expected = raw.reshape(3, height, width).transpose(1, 2, 0).astype(np.float32)
    assert np.array_equal(out, expected)


def test_reshape_std_arrays_unknown_color_mode_raises():
    with pytest.raises(ValueError):
        live_sources._reshape_std_arrays(np.zeros(4), 2, 2, 1, color_mode=99)


# ── CaLiveSource frame callback wiring (no network -- fed synthetically) ──
def test_ca_live_source_drops_frame_before_metadata_ready():
    pytest.importorskip("PyQt5.QtWidgets")
    src = live_sources.CaLiveSource()
    received = {}
    src.frameReady.connect(lambda image, image_id: received.update(id=image_id))
    src._on_array(value=np.zeros(12, dtype=np.uint16))  # size0/size1 never set
    assert received == {}


def test_ca_live_source_emits_local_uid_when_uniqueid_missing():
    pytest.importorskip("PyQt5.QtWidgets")
    src = live_sources.CaLiveSource()
    src._on_size0(value=4)
    src._on_size1(value=3)
    received = []
    src.frameReady.connect(lambda image, image_id: received.append(image_id))
    src._on_array(value=np.arange(12, dtype=np.uint16))
    src._on_array(value=np.arange(12, dtype=np.uint16))
    assert received == [1, 2]  # local counter increments each frame


def test_ca_live_source_prefers_real_uniqueid_when_present():
    pytest.importorskip("PyQt5.QtWidgets")
    src = live_sources.CaLiveSource()
    src._on_size0(value=4)
    src._on_size1(value=3)
    src._on_uid(value=42)
    received = []
    src.frameReady.connect(lambda image, image_id: received.append(image_id))
    src._on_array(value=np.arange(12, dtype=np.uint16))
    assert received == [42]


# ── backward-compatible config parsing (constants._apply) ───────────────
def test_apply_devices_without_backend_key_defaults_to_pva():
    import midas_gui.constants as C
    C._apply({"devices": [{"name": "legacy", "prefix": "p:", "pva_suffix": "Pva1:Image"}]})
    try:
        assert C.DEVICES == [{"name": "legacy", "prefix": "p:", "pva_suffix": "Pva1:Image",
                               "backend": "pva", "ca_suffix": "image1:"}]
    finally:
        C.reload_from_config()


def test_apply_devices_with_ca_backend_round_trips():
    import midas_gui.constants as C
    C._apply({"devices": [{"name": "varex", "prefix": "17bmVarex:",
                            "backend": "ca", "ca_suffix": "image1:"}]})
    try:
        assert C.DEVICES == [{"name": "varex", "prefix": "17bmVarex:", "pva_suffix": "",
                               "backend": "ca", "ca_suffix": "image1:"}]
    finally:
        C.reload_from_config()


# ── settings.py bundled 17-BM profile ────────────────────────────────────
def test_bundled_17bm_profile_has_ca_varex_device():
    from midas_gui import settings as S
    devices = S.BUNDLED_PROFILES["17-BM"]["devices"]
    varex = next(d for d in devices if d["name"] == "varex")
    assert varex["backend"] == "ca"
    assert varex["prefix"] == "17bmVarex:"
    assert varex["ca_suffix"] == "image1:"


# ── Preferences ▸ Devices tab round-trip ─────────────────────────────────
def _make_prefs_dialog():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    try:
        import midas_gui.prefs_dialog as PD
    except Exception as exc:
        pytest.skip(f"midas_gui.prefs_dialog needs the full MIDAS stack: {exc}")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return PD, app


def test_device_list_blank_backend_columns_default_to_pva():
    PD, _app = _make_prefs_dialog()
    dlg = PD.PreferencesDialog()
    try:
        dlg._populate({"devices": [{"name": "legacy", "prefix": "p:", "pva_suffix": "Pva1:Image"}]})
        out = dlg._device_list(dlg._dev_table)
        assert out == [{"name": "legacy", "prefix": "p:", "pva_suffix": "Pva1:Image",
                        "backend": "pva", "ca_suffix": "image1:"}]
    finally:
        dlg.close()


def test_device_list_ca_backend_round_trips():
    PD, _app = _make_prefs_dialog()
    dlg = PD.PreferencesDialog()
    try:
        dlg._populate({"devices": [{"name": "varex", "prefix": "17bmVarex:",
                                     "backend": "ca", "ca_suffix": "image1:"}]})
        out = dlg._device_list(dlg._dev_table)
        assert out == [{"name": "varex", "prefix": "17bmVarex:", "pva_suffix": "",
                        "backend": "ca", "ca_suffix": "image1:"}]
    finally:
        dlg.close()
