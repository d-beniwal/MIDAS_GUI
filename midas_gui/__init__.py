"""MIDAS GUI — modular PyQt5 application for the MIDAS X-ray diffraction suite.

Nine tabs (Data Viewer, Mask Builder, Calibrate, Calib. Refinement,
Batch Integrate, Corrections & Physics, PDF Analysis, Texture/Pole Figure,
Results & Export) exposing midas_calibrate_v2 and midas_integrate_v2.

Run with:
    midas-gui                  (after pip install)
    python -m midas_gui        (from any directory)
"""
import hdf5plugin  # noqa: F401 -- registers HDF5's dynamically-loaded compression
# filters (notably `bitshuffle`, id 32008) at import time, before any h5py read of
# a raw frame can happen -- several beamlines this app supports write their raw
# detector HDF5 files with such a filter (e.g. 3-ID-C's Eiger), and nothing else
# in this package imports hdf5plugin on that path (only midas_defect.persistence
# does, and nothing in the Data Viewer / solve-cell loading path touches that
# module). Without this, the first h5py read of such a file -- Data Viewer
# preview, solve-cell panel load, or Ingest's own frame loading -- raises
# "OSError: Can't synchronously read data (can't open directory .../lib/hdf5/
# plugin)". See .context/DECISIONS.md 2026-10-22.

__version__ = "1.0.0"
