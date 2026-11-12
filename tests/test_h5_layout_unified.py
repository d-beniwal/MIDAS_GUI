"""The 1-D and multi-azimuth Batch Integrate HDF5 files share one NeXus
layout: entry/data holds frame_ids, profiles, sigmas, R_px, Q, TTH; the
multi-azimuth file only ADDS cake/cake_sigma/eta_deg/bin_area there. Nothing
is duplicated at the root (no soft links, no entry/extra)."""
from types import SimpleNamespace

import numpy as np
import pytest

h5py = pytest.importorskip("h5py")
from midas_gui.cake_hdf5 import write_cake_h5, write_profiles_h5

N, NETA, NR = 3, 4, 10
SPEC = SimpleNamespace(Lsd=200000.0, pxY=200.0, Wavelength=0.1, n_eta_bins=NETA)
R = np.linspace(10, 100, NR)


def _tree(f):
    out = []
    f.visit(lambda n: out.append(n))
    return set(out)


def test_one_layout_for_both(tmp_path):
    rng = np.random.default_rng(0)
    prof = rng.random((N, NR))
    a = write_profiles_h5(tmp_path / "a.h5", profiles=prof, sigmas=prof * .1,
                          r_axis=R, frame_ids=list("abc"), lsd=2e5, px=200.0,
                          wl=0.1, omegas=[0, 1, 2], spec=SPEC)
    cake = rng.random((N, NETA, NR))
    area = np.ones((NETA, NR))
    b = write_cake_h5(tmp_path / "b.h5", cake=cake, cake_sigma=cake * .1,
                      r_axis=R, eta_axis=np.arange(NETA) * 90.0,
                      frame_ids=list("abc"), spec=SPEC, bin_area=area,
                      omegas=[0, 1, 2])
    common = {"entry", "entry/data"} | {
        f"entry/data/{k}" for k in
        ("frame_ids", "profiles", "sigmas", "R_px", "Q", "TTH", "omegas")}
    with h5py.File(a) as fa, h5py.File(b) as fb:
        ta, tb = _tree(fa), _tree(fb)
        assert ta == common
        assert tb == common | {f"entry/data/{k}" for k in
                               ("cake", "cake_sigma", "eta_deg", "bin_area")}
        for f in (fa, fb):
            assert f.attrs["NX_class"] == "NXroot"
            assert f["entry"].attrs["NX_class"] == "NXentry"
            assert f["entry/data"].attrs["NX_class"] == "NXdata"
            assert f["entry/data/Q"].shape == (NR,)
            assert f["entry/data/TTH"].shape == (NR,)
        np.testing.assert_allclose(fa["entry/data/Q"][()], fb["entry/data/Q"][()])
        assert fa.attrs["multi_azimuth"] == False and fb.attrs["multi_azimuth"] == True
