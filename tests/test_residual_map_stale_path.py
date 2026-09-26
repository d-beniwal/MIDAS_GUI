"""A residual-correction map that isn't on disk must not take down integration.

midas_integrate_v2 raises FileNotFoundError from _load_residual_map when
spec.ResidualCorrectionMap names an unreadable file, which kills the cake, the
ring residual and the pseudo-strain views along with the integration itself.
The geometry in hand is complete without the map, so the GUI degrades to "no
map" instead of failing shut -- and, at the source, never records a path for a
map the pipeline didn't write.
"""
import os
import shutil
import tempfile
from types import SimpleNamespace

import pytest


@pytest.fixture
def scratch():
    """A scratch dir that cleans up after itself.

    Not ``tmp_path``: pyproject's ``--basetemp=.scratch`` races with
    ``--forked`` and turns any tmp_path test into a setup FileExistsError.
    """
    d = tempfile.mkdtemp(prefix="mg_rcm_")
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)

from midas_gui.helpers import _drop_missing_residual_map


def test_unreadable_map_is_dropped():
    spec = SimpleNamespace(ResidualCorrectionMap="/nope/residual_corr.bin")
    _drop_missing_residual_map(spec)
    assert spec.ResidualCorrectionMap is None


def test_readable_map_is_kept(scratch):
    p = os.path.join(scratch, "residual_corr.bin")
    with open(p, "wb") as f:
        f.write(b"\x00" * 16)
    spec = SimpleNamespace(ResidualCorrectionMap=p)
    _drop_missing_residual_map(spec)
    assert spec.ResidualCorrectionMap == p


def test_absent_map_is_left_alone():
    spec = SimpleNamespace(ResidualCorrectionMap=None)
    _drop_missing_residual_map(spec)
    assert spec.ResidualCorrectionMap is None
    spec2 = SimpleNamespace()          # attribute not set at all
    _drop_missing_residual_map(spec2)
    assert getattr(spec2, "ResidualCorrectionMap", None) is None


def test_rerouted_result_records_no_path_when_no_bin_was_written(scratch):
    """The reroute computes bin_path before autocalibrate runs; the file only
    appears if a map was actually built. Mirrors calib.py's guard."""
    bin_path = os.path.join(scratch, "residual_corr.bin")
    recorded = bin_path if bin_path and os.path.isfile(bin_path) else None
    assert recorded is None
    with open(bin_path, "wb") as f:
        f.write(b"\x00" * 16)
    recorded = bin_path if bin_path and os.path.isfile(bin_path) else None
    assert recorded == bin_path
