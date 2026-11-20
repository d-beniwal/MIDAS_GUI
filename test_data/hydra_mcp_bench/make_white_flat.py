"""White_oct26 geometry (transcribed from the calibration report screenshot,
no distortion terms) + flat-field panels for checking R-eta cake coverage.
Run: python -I make_white_flat.py"""
import json
from pathlib import Path
import h5py, numpy as np
H = Path(__file__).resolve().parent
G = {1: (2403.155, 2274.919, 2165.822, 300.0, 0.1157, 0.0657),
     2: (2403.414, 2287.316, 2119.316, 30.0, 0.1432, 0.5250),
     3: (2403.213, 2279.107, 2081.821, 120.0, 0.4711, 0.4277),
     4: (2403.377, 2387.187, 2189.116, 210.0, 0.4059, 0.2441)}
names = ["a1","a2","a3","a4","a5","a6","iso_R2","iso_R4","iso_R6","phi1","phi2","phi3","phi4","phi5","phi6"]
for n, (lsd, by, bz, tx, ty, tz) in G.items():
    d = H / "white_calib"; d.mkdir(exist_ok=True)
    (d / f"white_oct26.ge{n}.json").write_text(json.dumps({
        "Lsd": lsd * 1000.0, "BC_y": by, "BC_z": bz, "tx": tx, "ty": ty, "tz": tz,
        "distortion": {k: 0.0 for k in names}, "pxY": 200.0, "pxZ": 200.0,
        "NrPixelsY": 2048, "NrPixelsZ": 2048, "wavelength_A": 0.17298, "im_trans": []}, indent=2))
    p = H / "white_flat" / f"ge{n}" / "flat" / f"flat_000001.ge{n}.h5"
    p.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(p, "w") as f:
        f.create_dataset("exchange/data", data=np.full((1, 2048, 2048), 1000, np.uint16),
                         chunks=(1, 2048, 2048), compression="gzip", compression_opts=1)
print("ok")
