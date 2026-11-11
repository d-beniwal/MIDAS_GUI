# Phase identification — test_data/projects/s3idc_oct8_ruby.h5

337 candidate spots. Lattice agreement only (no intensities, no atoms).

## 1. Which candidate phase fits the spots better than chance?

Best random-orientation match per phase (tolerance 0.1 in hkl) vs 20 random lattices of the same volume.

| Phase | Spots matched | Decoys mean ± sd (max) | z | Verdict |
|---|---|---|---|---|
| Corundum Al2O3 (ruby, sapphire) | 118 / 337 | 46.0 ± 5.7 (58) | 12.6 | supported |
| Rhenium | 74 / 337 | 57.1 ± 7.1 (71) | 2.4 | weak |
| Quartz (alpha SiO2) | 58 / 337 | 47.2 ± 8.5 (73) | 1.3 | not distinguished |
| Platinum | 63 / 337 | 58.9 ± 4.7 (65) | 0.9 | not distinguished |
| Aluminium | 62 / 337 | 58.9 ± 5.9 (69) | 0.5 | not distinguished |
| Tungsten | 57 / 337 | 57.1 ± 9.4 (76) | -0.0 | not distinguished |
| Gold | 60 / 337 | 61.5 ± 4.8 (70) | -0.3 | not distinguished |
| Copper | 52 / 337 | 56.9 ± 9.4 (70) | -0.5 | not distinguished |
| Spinel MgAl2O4 | 37 / 337 | 43.1 ± 7.2 (67) | -0.9 | not distinguished |
| Diamond | 52 / 337 | 60.0 ± 4.5 (69) | -1.8 | not distinguished |
| Iron (bcc) | 48 / 337 | 59.4 ± 5.1 (72) | -2.2 | not distinguished |
| MgO | 48 / 337 | 59.5 ± 5.1 (69) | -2.3 | not distinguished |
| NaCl | 31 / 337 | 49.0 ± 6.7 (66) | -2.7 | not distinguished |
| Silicon | 26 / 337 | 54.8 ± 8.5 (77) | -3.4 | not distinguished |
| CeO2 | 26 / 337 | 52.2 ± 7.2 (66) | -3.6 | not distinguished |
| LaB6 | 32 / 337 | 50.0 ± 4.6 (59) | -3.9 | not distinguished |

## 2. Per-panel omega offsets (Corundum Al2O3 (ruby, sapphire) hypothesis)

Relative to panel 2. Matched spots at tolerance 0.08: **95 → 166** of 337; rms 0.0120 → 0.0092 Å⁻¹.

| Panel | Offset (°) | Matched before → after | rms after (Å⁻¹) |
|---|---|---|---|
| 1 | -1.20 | 23 → 26 of 42 | 0.0104 |
| 2 | +0.00 | 12 → 30 of 78 | 0.0108 |
| 3 | -1.90 | 28 → 31 of 53 | 0.0089 |
| 4 | -5.90 | 0 → 18 of 31 | 0.0071 |
| 5 | -4.90 | 11 → 34 of 69 | 0.0092 |
| 6 | -2.70 | 21 → 27 of 64 | 0.0077 |

Control — random same-volume lattices aligned the same way: matched 14.8 ± 14.1 (max 46) vs 166 for Corundum Al2O3 (ruby, sapphire).

## 3. Re-indexing with the identified cell (known structure, then refined freely)

| Spots | Refined cell (Å, °) | V (Å³) | Closest known lattice | Edge dev % | Angle dev ° | ΔV % |
|---|---|---|---|---|---|---|
| unaligned | 5.127, 5.224, 5.112, 55.879, 55.270, 55.414 | 86.76 | Corundum Al2O3 (ruby, sapphire) (close) | -0.03, +1.86, -0.32 | +0.60, -0.01, +0.13 | +2.1 |
| aligned | 5.123, 5.204, 5.141, 55.154, 55.022, 55.125 | 85.87 | Corundum Al2O3 (ruby, sapphire) (matches) | -0.11, +1.46, +0.24 | -0.12, -0.26, -0.15 | +1.1 |

## 4. Blind (free ab-initio) indexing of the aligned spots

Indexed 154 of 337 (chance level 67, score floor 92); cell 4.714, 4.726, 5.037, 91.388, 63.322, 60.563, V = 82.88 Å³; closest known lattice: Corundum Al2O3 (ruby, sapphire) (close, score 2.18%).
