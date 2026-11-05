"""The 1-D combined HDF5 must record the caking parameters it was built with."""
from types import SimpleNamespace as N

import numpy as np
import pytest

h5py = pytest.importorskip("h5py")
m = pytest.importorskip("midas_integrate_v2")


def _spec():
    from midas_gui.helpers import _build_spec
    r = N(NrPixelsY=2048, NrPixelsZ=2048, pxY=200., pxZ=200., Lsd=1e6,
          BC_y=1024., BC_z=1024., tx=0., ty=0., tz=0., wavelength_A=0.17,
          distortion={}, im_trans=[], residual_corr_bin_path=None,
          panel_layout=None, panel_shifts_path=None)
    return _build_spec(r, 0.5, 5.0, r_min=240, r_max=450, eta_min=-90, eta_max=90)


def test_spec_carries_requested_cake_params():
    s = _spec()
    assert (s.RMin, s.RMax, s.RBinSize) == (240.0, 450.0, 0.5)
    assert (s.EtaMin, s.EtaMax, s.EtaBinSize) == (-90.0, 90.0, 5.0)
    assert s.n_r_bins == 420


def test_write_all_profiles_1d_h5_stamps_params(tmp_path):
    from midas_gui.workers import write_all_profiles
    s = _spec()
    n_r = s.n_r_bins
    r = s.RMin + s.RBinSize * (np.arange(n_r) + 0.5)
    prof = np.ones((3, n_r))
    paths = write_all_profiles(tmp_path, ["h5"], r, prof, prof, ["a", "b", "c"],
                               1e6, 200., 0.17, spec=s)
    with h5py.File(paths[0], "r") as f:
        assert f.attrs["RMin"] == 240.0 and f.attrs["RMax"] == 450.0
        assert f.attrs["RBinSize"] == 0.5
        assert f.attrs["EtaMin"] == -90.0 and f.attrs["EtaMax"] == 90.0
        assert f.attrs["EtaBinSize"] == 5.0
        assert f.attrs["n_r_bins"] == 420
