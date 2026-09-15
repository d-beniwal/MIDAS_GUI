"""One-time snapshot handoff between the main GUI process and the Auto
Attenuation popup process.

The popup never talks back to the main GUI's live objects — it only ever
reads a single ``.npz`` file written once, at launch time.
"""

import json

import numpy as np


def write_snapshot(path, *, frames, dark=None, dark_stack=None, mask=None,
                    energy_keV=None, geometry=None, source=None):
    """Write a buffer/dark/mask/geometry snapshot to *path* as ``.npz``.

    ``source`` optionally records which of the Data Viewer's two mutually
    exclusive frame sources *frames* came from (``"buffer"`` or ``"loaded"``
    — see ``widgets.DataLoaderPanel.data_source_kind``), purely for display
    in the popup.
    """
    kwargs = {"frames": np.asarray(frames, dtype=np.float32)}
    if dark is not None:
        kwargs["dark"] = np.asarray(dark, dtype=np.float32)
    if dark_stack is not None:
        kwargs["dark_stack"] = np.asarray(dark_stack, dtype=np.float32)
    if mask is not None:
        kwargs["mask"] = np.asarray(mask, dtype=np.float32)
    if energy_keV is not None:
        kwargs["energy_keV"] = np.array(float(energy_keV))
    if geometry is not None:
        kwargs["geometry_json"] = np.array(json.dumps(geometry))
    if source is not None:
        kwargs["source"] = np.array(str(source))

    with open(path, "wb") as f:
        np.savez(f, **kwargs)


def load_snapshot(path):
    """Read back a snapshot written by :func:`write_snapshot`.

    Returns a dict with ``frames`` always present; ``dark``, ``dark_stack``,
    ``mask``, ``energy_keV``, ``geometry`` and ``source`` present only if
    they were written.
    """
    result = {}
    with open(path, "rb") as f:
        with np.load(f, allow_pickle=False) as z:
            result["frames"] = z["frames"]
            for key in ("dark", "dark_stack", "mask"):
                if key in z.files:
                    result[key] = z[key]
            if "energy_keV" in z.files:
                result["energy_keV"] = float(z["energy_keV"])
            if "geometry_json" in z.files:
                result["geometry"] = json.loads(str(z["geometry_json"]))
            if "source" in z.files:
                result["source"] = str(z["source"])
    return result
