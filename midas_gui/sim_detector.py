"""Simulated PVA detector for exercising the Data Viewer's "Live Data" card
without any real beamline hardware.

:class:`SimDetectorServer` publishes randomly-generated frames on a local
EPICS PVA channel using the same ``pvapy``/areaDetector NTNDArray plumbing a
real detector's PVA image plugin uses (``pvapy.PvaServer`` +
``pvapy.utility.adImageUtility.AdImageUtility``). The Data Viewer's
``PvaLiveSource`` (see :mod:`midas_gui.widgets`) connects to it exactly as it
would to any other device in the PV dropdown — it has no idea the frames are
synthetic.

Frame rate, image size and intensity range are all constructor parameters;
the ``DEFAULT_*`` constants below default to a DECTRIS Eiger2 500K
(1030 x 514 px, matching ``constants.DEFAULT_PIXEL_UM``'s 75 um pixel size)
with a 0-60000 count intensity range.
"""
from __future__ import annotations

import threading
from typing import Dict, Optional

import numpy as np

# Eiger2 500K: 2 x 1 module array (raw shape including inter-chip gap pixels).
DEFAULT_WIDTH = 1030
DEFAULT_HEIGHT = 514
DEFAULT_DTYPE = "uint16"
DEFAULT_INTENSITY_MIN = 0
DEFAULT_INTENSITY_MAX = 60000
DEFAULT_FRAME_RATE_HZ = 5.0

# Channel name published by the "Sim Detector" entry in
# constants.DEFAULT_DEVICES ("midasSim:" + "Pva1:Image"); DataLoaderPanel
# auto-starts a server on this channel when this PV is connected to.
DEFAULT_CHANNEL_NAME = "midasSim:Pva1:Image"


class SimDetectorServer:
    """Publishes random frames on a PVA channel at a fixed rate.

    Not started automatically — call :meth:`start`. ``stop()`` is safe to
    call more than once, or before ``start()``.
    """

    def __init__(self, channel_name: str = DEFAULT_CHANNEL_NAME, *,
                 width: int = DEFAULT_WIDTH, height: int = DEFAULT_HEIGHT,
                 dtype: str = DEFAULT_DTYPE,
                 intensity_min: float = DEFAULT_INTENSITY_MIN,
                 intensity_max: float = DEFAULT_INTENSITY_MAX,
                 frame_rate_hz: float = DEFAULT_FRAME_RATE_HZ):
        self.channel_name = channel_name
        self.width = int(width)
        self.height = int(height)
        self.dtype = np.dtype(dtype)
        self.intensity_min = intensity_min
        self.intensity_max = intensity_max
        self.frame_rate_hz = float(frame_rate_hz)
        self._server = None
        self._ad_image_utility = None
        self._thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._frame_id = 0

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _make_frame(self) -> np.ndarray:
        shape = (self.height, self.width)
        if self.dtype.kind == "f":
            return np.random.uniform(
                self.intensity_min, self.intensity_max, size=shape).astype(self.dtype)
        lo, hi = int(self.intensity_min), int(self.intensity_max) + 1
        return np.random.randint(lo, hi, size=shape, dtype=self.dtype)

    def start(self):
        """Create the PVA channel and begin publishing frames on a background
        thread. No-op if already running."""
        if self.is_running:
            return
        import pvapy as pva
        from pvapy.utility.adImageUtility import AdImageUtility
        self._ad_image_utility = AdImageUtility
        self._server = pva.PvaServer()
        ntnda = AdImageUtility.generateNtNdArray2D(self._frame_id, self._make_frame())
        self._server.addRecord(self.channel_name, ntnda, None)
        self._server.start()
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run, args=(ntnda,), daemon=True)
        self._thread.start()

    def _run(self, ntnda):
        period = 1.0 / self.frame_rate_hz if self.frame_rate_hz > 0 else 0.0
        while not self._stop_evt.wait(period):
            self._frame_id += 1
            self._ad_image_utility.replaceNtNdArrayImage2D(
                ntnda, self._frame_id, self._make_frame())
            try:
                self._server.updateUnchecked(self.channel_name, ntnda)
            except Exception:
                break

    def stop(self):
        """Stop the publishing thread and tear down the PVA channel."""
        self._stop_evt.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None
        if self._server is not None:
            try:
                self._server.stop()
            except Exception:
                pass
            self._server = None


# ── Process-wide registry, keyed by channel name ────────────────────────────
# Lets the GUI lazily start a simulator the first time its channel is
# connected to, and reuse it across repeated Start/Stop of the Live Data
# card, without the caller having to track the SimDetectorServer instance.
_servers: Dict[str, SimDetectorServer] = {}
_registry_lock = threading.Lock()


def ensure_running(channel_name: str = DEFAULT_CHANNEL_NAME, **kwargs) -> SimDetectorServer:
    """Return the running simulator for ``channel_name``, starting one with
    ``kwargs`` (see :class:`SimDetectorServer`) if none exists yet."""
    with _registry_lock:
        srv = _servers.get(channel_name)
        if srv is None or not srv.is_running:
            srv = SimDetectorServer(channel_name, **kwargs)
            srv.start()
            _servers[channel_name] = srv
        return srv


def stop_all():
    """Stop every simulator started via :func:`ensure_running`. Safe to call
    even if none were ever started (e.g. on app shutdown)."""
    with _registry_lock:
        for srv in _servers.values():
            srv.stop()
        _servers.clear()
