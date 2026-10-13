"""The stream catalog, and its unification with PeakFitCatalog.

``MetadataCatalog`` is a bare ``QObject`` -- no widgets, no pyqtgraph, no
matplotlib -- so this needs a ``QApplication`` but none of the teardown
care the GUI test files take. Qt imports still go inside fixtures, per the
repo rule about forked children.

The load-bearing tests here are the laziness ones. ``PeakFitCatalog`` was a
separate, smaller class precisely because the real catalog's setter walked
the whole file, and the Texture tab has no use for that walk. Making the
scan lazy is what allows one class to serve both; if that regresses, the
unification silently costs Texture a full file read on every path change.
"""
from __future__ import annotations

import numpy as np
import pytest

pytestmark = pytest.mark.forked


@pytest.fixture(scope="module")
def app():
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _h5(tmp_path, name="p.h5", **datasets):
    h5py = pytest.importorskip("h5py")
    p = tmp_path / name
    with h5py.File(p, "w") as f:
        for path, arr in datasets.items():
            f.create_dataset(path, data=arr)
    return str(p)


# ── laziness: the whole reason one class can serve both consumers ───────

def test_setting_a_path_does_not_walk_the_file(app, tmp_path):
    from midas_gui.metadata_catalog import MetadataCatalog
    c = MetadataCatalog()
    walks = []
    c._rescan_primary = lambda: walks.append(1)
    c.set_primary_source(_h5(tmp_path, **{"m/samX": np.arange(4.0)}))
    assert walks == [], "the setter scanned; Texture would pay for this"


def test_the_walk_happens_once_on_first_read_then_is_memoized(app, tmp_path):
    from midas_gui.metadata_catalog import MetadataCatalog
    c = MetadataCatalog()
    c.set_primary_source(_h5(tmp_path, **{"m/samX": np.arange(4.0)}))
    walks = []
    orig = c._rescan_primary
    c._rescan_primary = lambda: (walks.append(1), orig())[1]
    c.list_streams(); c.list_streams(); c.get("prim:/m/samX", 4)
    assert len(walks) == 1, f"expected one walk, got {len(walks)}"


def test_four_path_changes_still_cost_one_walk(app, tmp_path):
    """The source rescanned inside every setter, so configuring a session
    meant one full walk per field. Now they only invalidate."""
    from midas_gui.metadata_catalog import MetadataCatalog
    c = MetadataCatalog()
    h5 = _h5(tmp_path, **{"m/samX": np.arange(4.0)})
    walks = []
    orig = c._rescan_primary
    c._rescan_primary = lambda: (walks.append(1), orig())[1]
    c.set_primary_source(h5)
    c.set_aux_source(_h5(tmp_path, "aux.h5", **{"m/samY": np.arange(4.0)}))
    c.set_cake_csv(str(tmp_path / "cake.csv"))
    c.set_peak_fit_csv(str(tmp_path / "fit.csv"))
    assert walks == []
    c.list_streams()
    assert len(walks) == 1


def test_a_changed_path_invalidates_the_memo(app, tmp_path):
    from midas_gui.metadata_catalog import MetadataCatalog
    c = MetadataCatalog()
    c.set_primary_source(_h5(tmp_path, "a.h5", **{"m/samX": np.arange(4.0)}))
    assert any("samX" in s.key for s in c.list_streams())
    c.set_primary_source(_h5(tmp_path, "b.h5", **{"m/samZ": np.arange(4.0)}))
    keys = [s.key for s in c.list_streams()]
    assert any("samZ" in k for k in keys) and not any("samX" in k for k in keys)


def test_stream_counts_scans_rather_than_reading_stale_lists(app, tmp_path):
    """The Sources panel used to read the private lists directly, which
    would skip the scan and describe a file that is no longer selected."""
    from midas_gui.metadata_catalog import MetadataCatalog
    c = MetadataCatalog()
    c.set_primary_source(_h5(tmp_path, **{"m/samX": np.arange(4.0),
                                          "m/samY": np.arange(4.0)}))
    n1d, n2d, naux = c.stream_counts()
    assert n1d == 2 and n2d == 0 and naux == 0


# ── the 2-D flood cap ───────────────────────────────────────────────────

def test_two_dimensional_streams_are_capped(app, tmp_path):
    """A MIDAS archive holds one (nR, nEta) array per frame under each of
    IntegrationResult/ and OmegaSumFrame/, so an 834-frame scan offers
    ~1,669 -- none of them a per-frame stream. Uncapped they bury the real
    motor streams in the same combo."""
    zarr = pytest.importorskip("zarr")
    from midas_gui import metadata_catalog as mc
    p = tmp_path / "big.zarr.zip"
    store = zarr.ZipStore(str(p), mode="w")
    z = zarr.group(store)
    for i in range(mc.MAX_2D_STREAMS + 25):
        z[f"IntegrationResult/FrameNr_{i}"] = np.zeros((4, 3))
    store.close()
    c = mc.MetadataCatalog()
    c.set_primary_source(str(p))
    _n1d, n2d, _naux = c.stream_counts()
    assert n2d == mc.MAX_2D_STREAMS, f"cap not applied: {n2d}"


# ── the unification ─────────────────────────────────────────────────────

def test_peak_fit_catalog_is_the_same_class(app):
    """Two QObjects with the same signal name and overlapping methods is a
    trap: wiring the wrong one gives a silently empty combo, not an error."""
    from midas_gui.peak_fit_panel import PeakFitCatalog
    from midas_gui.metadata_catalog import MetadataCatalog
    assert PeakFitCatalog is MetadataCatalog


def test_the_unified_class_keeps_everything_peakfitpanel_uses(app):
    """The panel's documented surface. If any of these drops, Texture
    breaks in a way no test of this file would otherwise catch."""
    from midas_gui.peak_fit_panel import PeakFitCatalog
    c = PeakFitCatalog()
    for name in ("catalogChanged", "csv_cache", "primary_path", "aux_h5_path",
                 "set_peak_fit_csv", "set_primary_source", "peak_fit_csv_path"):
        assert hasattr(c, name), f"PeakFitPanel would lose {name}"


def test_csv_cache_is_memoized_and_follows_the_path(app, tmp_path):
    from midas_gui.peak_fit_panel import PeakFitCatalog
    c = PeakFitCatalog()
    assert c.csv_cache() is None, "no CSV set"
    csv = tmp_path / "f.csv"
    csv.write_text("name,Use,pos1\nimg=0 omega=0.0 eta=0.0,True,5.0\n")
    c.set_peak_fit_csv(str(csv))
    first = c.csv_cache()
    assert first is not None and c.csv_cache() is first, "not memoized"
    c.set_peak_fit_csv("")
    assert c.csv_cache() is None, "cache survived the path being cleared"
