"""Unit tests for midas_gui.hydra + helpers.hydra_siblings/hydra_panel_index.

Uses the small synthetic fixture at test_data/gui_synthetic/hydra/ (see
make_hydra_test_data.py) — 4 panels with an idealized windmill geometry
(tx = 0/90/180/270 deg) and one bright-square marker per panel, at the same
local raw-pixel offset from each panel's own beam centre.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from midas_gui import hydra
from midas_gui.helpers import (hydra_panel_index, hydra_siblings,
                                geometry_fields_from_file)

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "test_data" / "gui_synthetic" / "hydra"


# ── sibling / panel-index discovery ─────────────────────────────────────────

def test_hydra_panel_index_folder_segment():
    assert hydra_panel_index("/a/b/ge3/scan.h5") == 3


def test_hydra_panel_index_filename_infix():
    assert hydra_panel_index("/a/b/scan_002020.ge2.h5") == 2


def test_hydra_panel_index_both_conventions_at_once():
    assert hydra_panel_index("/a/ge1/scan_002020.ge1.h5") == 1


def test_hydra_panel_index_none_when_absent():
    assert hydra_panel_index("/a/b/scan.h5") is None


def test_hydra_siblings_finds_all_four(tmp_path):
    for n in (1, 2, 3, 4):
        d = tmp_path / f"ge{n}"
        d.mkdir()
        (d / f"scan_0001.ge{n}.h5").write_bytes(b"")
    sib = hydra_siblings(str(tmp_path / "ge1" / "scan_0001.ge1.h5"))
    assert sorted(sib.keys()) == [1, 2, 3, 4]
    for n, p in sib.items():
        assert Path(p).name == f"scan_0001.ge{n}.h5"


def test_hydra_siblings_tolerates_missing_panels(tmp_path):
    for n in (1, 3):
        d = tmp_path / f"ge{n}"
        d.mkdir()
        (d / f"scan.ge{n}.h5").write_bytes(b"")
    sib = hydra_siblings(str(tmp_path / "ge1" / "scan.ge1.h5"))
    assert sorted(sib.keys()) == [1, 3]


def test_hydra_siblings_empty_for_non_hydra_path(tmp_path):
    p = tmp_path / "scan.h5"
    p.write_bytes(b"")
    assert hydra_siblings(str(p)) == {}


# ── real fixture existence ───────────────────────────────────────────────

@pytest.fixture(scope="module")
def fixture_available():
    if not FIXTURE_DIR.exists():
        pytest.skip("test_data/gui_synthetic/hydra/ fixture not present — "
                    "run make_hydra_test_data.py")
    return FIXTURE_DIR


# ── bundled default geometry ────────────────────────────────────────────

def test_bundled_default_geometry_files_parse():
    txs = []
    for n in (1, 2, 3, 4):
        fields = geometry_fields_from_file(str(hydra.default_param_file(n)))
        assert fields["BC_y"] is not None and fields["BC_z"] is not None
        assert fields["NrPixelsY"] == 2048 and fields["NrPixelsZ"] == 2048
        txs.append(fields["tx"])
    # a real fitted 1-ID-E windmill — four tx values roughly 90 deg apart
    txs = sorted(txs)
    gaps = [(txs[(i + 1) % 4] - txs[i]) % 360 for i in range(4)]
    for g in gaps:
        assert 60.0 < g < 120.0


# ── DetectorState ────────────────────────────────────────────────────────

def test_detector_state_load_from_geometry_dict():
    st = hydra.DetectorState()
    st.load_from_geometry_dict({
        "BC_y": 10.0, "BC_z": 20.0, "tx": 30.0, "ty": 1.0, "tz": 2.0,
        "Lsd": 5000.0, "pxY": 150.0, "NrPixelsY": 512, "NrPixelsZ": 512,
        "im_trans": [1, 3],
    })
    assert (st.bc_y, st.bc_z, st.tx, st.ty, st.tz) == (10.0, 20.0, 30.0, 1.0, 2.0)
    assert (st.lsd, st.px, st.ny, st.nz) == (5000.0, 150.0, 512, 512)
    assert st.im_trans_opts == [1, 3]


def test_detector_state_load_default():
    st = hydra.DetectorState()
    st.load_default(1)
    assert st.ny == 2048 and st.nz == 2048
    assert st.px == 200.0


def test_detector_state_dark_correction_applied_before_remap(tmp_path):
    """get_remapped_frame applies dark/bright/background correction to the
    raw frame before the compositing remap runs — same order as the
    single-detector tab's DataLoaderPanel.corrected() -> _apply_im_trans."""
    import h5py

    st = hydra.DetectorState(bc_y=64.0, bc_z=64.0, tx=0.0, px=200.0, ny=128, nz=128)
    data = np.full((1, 128, 128), 100.0, dtype=np.float32)
    path = tmp_path / "panel.ge1.h5"
    with h5py.File(path, "w") as f:
        f.create_dataset("exchange/data", data=data)
    st.data_file = str(path)
    big = 256

    plain = st.get_remapped_frame(0, "exchange/data", big)

    st.dark = np.full((128, 128), 30.0, dtype=np.float32)
    corrected = st.get_remapped_frame(0, "exchange/data", big)

    valid = np.isfinite(plain) & np.isfinite(corrected)
    assert valid.sum() > 1000   # sanity: not a degenerate/empty overlap
    assert np.allclose(corrected[valid], plain[valid] - 30.0, atol=1e-3)


# ── autopick_big_det_size ────────────────────────────────────────────────

def test_autopick_big_det_size_rounds_up_to_256():
    st = hydra.DetectorState(bc_y=128.0, bc_z=128.0, ny=256, nz=256)
    size = hydra.autopick_big_det_size([st])
    # farthest corner from BC(128,128) in a 256x256 panel is ~181px away
    assert size % 256 == 0
    assert size >= 2 * 181


# ── compute_inv_coords / remap_to_composite round-trip ──────────────────

def _expected_composite_xy(bc_y, bc_z, tx_deg, px, big_det_size, y_pix0, z_pix0):
    """Independent (hand-derived) forward map: given a raw-pixel marker
    position, where should it land on the composite canvas? This is the
    analytic inverse of hydra.compute_inv_coords's rotation + vertical-axis
    mirror — typed out separately here rather than calling that function,
    so a sign error in the module under test would show up as a mismatch.

    Forward (panel -> composite) is a rotation by ``tx_deg`` COUNTERCLOCKWISE
    (see the 2026-08-23 "Hydra composite rotation direction: reverted back to
    counterclockwise" DECISIONS.md entry) — negating the angle before the
    analytic inverse below is the independently-typed equivalent of that
    same convention — followed by a mirror about the composite's vertical
    axis (``half - Y_lab/px`` instead of ``Y_lab/px + half``, see the
    2026-08-24 "Hydra composite needs a vertical-axis mirror too"
    DECISIONS.md entry)."""
    half = big_det_size * 0.5
    tx = math.radians(tx_deg)
    c, s = math.cos(tx), math.sin(tx)
    Y_det = px * (bc_y - y_pix0)
    Z_det = px * (z_pix0 - bc_z)
    Y_lab = Y_det * c - Z_det * s
    Z_lab = Y_det * s + Z_det * c
    return half - Y_lab / px, Z_lab / px + half


def test_windmill_composite_places_markers_at_predicted_positions(fixture_available):
    states = {}
    for n in (1, 2, 3, 4):
        st = hydra.DetectorState()
        fields = geometry_fields_from_file(str(fixture_available / f"ge{n}" / f"ps_ge{n}.txt"))
        st.load_from_geometry_dict(fields)
        states[n] = st
        st.data_file = str(fixture_available / f"ge{n}" / f"panel.ge{n}.h5")

    siblings = {n: states[n].data_file for n in (1, 2, 3, 4)}
    # Pass the pre-populated `states` (our synthetic BC=128/tx=0-270 geometry)
    # rather than {} — an empty dict would make build_windmill_composite
    # silently fall back to the *bundled real 1-ID-E default* geometry
    # (2048px panels, BC~2300) instead of exercising our synthetic fixture.
    comp, size = hydra.build_windmill_composite(siblings, 0, "exchange/data", states, op="max")
    assert size == 512   # autopick for BC=(128,128) in a 256x256 panel
    assert comp.shape == (size, size)
    assert np.count_nonzero(comp) > 0
    assert not np.all(np.isnan(comp))

    # marker is at local raw (row=128, col=188) in every panel (see
    # make_hydra_test_data.py) — check each panel's marker independently by
    # remapping just that one panel (compositing with max() could occlude
    # one marker under another if their canvas positions ever coincided).
    row0, col0 = 128.0, 188.0
    for n, st in states.items():
        single = st.get_remapped_frame(0, "exchange/data", size)
        ys, xs = np.where(single > 500)
        assert len(xs) > 0, f"panel {n}: marker not found in composite"
        cx, cy = xs.mean(), ys.mean()
        ex, ey = _expected_composite_xy(st.bc_y, st.bc_z, st.tx, st.px, size, col0, row0)
        assert abs(cx - ex) < 2.0 and abs(cy - ey) < 2.0, (
            f"panel {n}: marker at ({cx:.1f},{cy:.1f}), expected ({ex:.1f},{ey:.1f})")
