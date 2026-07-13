# MIDAS GUI — Configuration & Defaults Guide

**A step-by-step guide to setting up your own defaults, paths, materials and
algorithms.**

Focused how-to companion to the main `gui_documentation.md` (§15). Everything is
stored in **one per-user JSON config** that overrides the shipped built-in
defaults. You can manage it entirely from the **Preferences dialog**, or edit /
share the JSON file directly.

---

## 1. What you can configure

Without editing any code:

| Domain | Examples |
|--------|----------|
| **Geometry defaults** | wavelength λ, pixel size, Lsd, beam centre |
| **Menus** | the clickable pixel-size presets and K-edge foils |
| **Materials** | your phases for ring simulation (lattice + space group) |
| **Calibrants** | the calibrant list on the Calibrate tab |
| **Default paths** | default data / calibration / output files & folders |
| **Algorithms** | default calibration pipeline, integration kernel, output format, error model, colormap |
| **Visible tabs** | which optional tabs are shown (Data Viewer, Mask, Calibrate, Batch are always on) |

## 2. How it works

There is **one per-user config file**. If present, its values override the shipped
built-in defaults; if absent, you get the built-ins. There is no separate "group"
mechanism — to share settings, export a JSON and hand it to a colleague (§6).

**Changes take effect on the next launch** (values are read when the window is
built). A malformed config file is ignored (built-ins are used) — it will never
stop the GUI from starting.

### Where the file lives

| OS | Path |
|----|------|
| Linux | `~/.config/midas_gui/config.json` |
| macOS | `~/Library/Application Support/midas_gui/config.json` |
| Windows | `%APPDATA%\midas_gui\config.json` |

You normally never touch this path by hand — the Preferences dialog reads and
writes it. **Settings ▸ Open config folder** jumps straight to it.

---

## 3. Method A — the Preferences dialog (recommended)

1. Launch the GUI (`midas-gui`, or `python -m midas_gui`).
2. Open **Settings ▸ Preferences…** (menu bar, top-left).
3. The dialog opens **pre-filled with the full shipped defaults** — you edit from a
   complete starting point. Work through the tabs:
   - **Geometry** — wavelength, pixel size, Lsd, beam centre.
   - **Paths** — default files/folders (use the **…** button to browse).
   - **Materials** and **Calibrants** — **Add** a row and fill `name, a, b, c, α, β,
     γ, SG` (SG = space-group number), **Remove selected**, or edit any cell.
     Materials appear in the Data Viewer's material dropdown; calibrants in the
     Calibrate tab list.
   - **Menus** — the pixel-size presets (label + µm) and K-edge foils (element + keV).
   - **Algorithms** — default calibration pipeline, integration kernel, output
     format, error model, and colormap/theme.
   - **Tabs** — tick which optional tabs are visible. Data Viewer, Mask, Calibrate
     and Batch Integrate are always on (locked). This setting **applies immediately**
     on Save — no restart needed (unlike the others).
4. Handy buttons (top of the dialog):
   - **Save current GUI state** — copies the Data Viewer's *current* λ / pixel / Lsd
     / beam centre into the Geometry fields (set a system up once, then capture it).
   - **Save config to JSON…** / **Load config (JSON)…** — export or import a config
     file (how you share settings — see §6).
   - **Reset to shipped defaults** — discards your config and returns to the built-ins.
5. Click **Save as my defaults**. Your per-user file is written; **restart the GUI**
   to apply.

Because the tables are pre-filled with the shipped list, "remove" and "modify" just
work — whatever is in the tables when you Save becomes your complete list. To get
the original shipped list back at any time, use **Reset to shipped defaults**.

---

## 4. Method B — edit the JSON file by hand

Prefer the dialog for materials/calibrants (it pre-fills the full list). Hand-editing
is convenient for a few scalar overrides.

1. Copy the template to your per-user path (§2), e.g. on Linux:
   ```bash
   mkdir -p ~/.config/midas_gui
   cp documentation/config.example.json ~/.config/midas_gui/config.json
   ```
2. Keep **only the keys you want to change** — every section and key is optional.
3. Save and (re)launch the GUI.

### Format
```json
{
  "geometry": {
    "wavelength_A": 0.39, "pixel_um": 75.0, "lsd_um": 121000.0, "bc_y": 10.0, "bc_z": 10.0,
    "pixel_presets": [["GE", 200.0], ["Varex", 150.0], ["Pilatus", 172.0], ["Eiger", 75.0]],
    "k_edge_foils": [["W", 69.525], ["Au", 80.725], ["Pb", 88.005]]
  },
  "materials":  { "Ni (FCC)": {"a":3.5238,"b":3.5238,"c":3.5238,"alpha":90,"beta":90,"gamma":90,"sg":225} },
  "calibrants": { "CeO2": {"a":5.4116,"b":5.4116,"c":5.4116,"alpha":90,"beta":90,"gamma":90,"sg":225} },
  "paths": {
    "calibrant_tif": "", "calibrant_h5": "", "nickel_h5": "", "nickel_dir": "",
    "nickel_frame0": "", "calib_file": "", "pdf_iq_file": "", "pdf_calib": ""
  },
  "ui": {
    "calibration_pipeline": "one_shot", "integration_kernel": "subpixel2",
    "output_format": "csv", "azimuthal_method": "poisson", "plot_theme": "hot",
    "visible_tabs": ["Calib. Refinement", "Corrections", "PDF Analysis",
                     "Texture", "Pump Probe", "Results & Export"]
  }
}
```

### Key reference
- **geometry** — numbers in Å / µm / px. `pixel_presets` = `[label, size_µm]` list
  for the clickable **px** menu; `k_edge_foils` = `[element, K-edge keV]` list for the
  clickable **λ** menu.
- **materials / calibrants** — keyed by display name; each is `a, b, c` (Å),
  `alpha, beta, gamma` (deg), `sg` (space-group number). Paths accept `~` and
  `$ENV_VARS`.
- **paths** — override the default file/folder each tab opens with. Empty `""` (or
  omit) keeps the built-in.
- **ui** — `calibration_pipeline` ∈ `one_shot | first_time | four_stage | bayesian |
  joint`; `integration_kernel` ∈ `hard | subpixel2 | subpixel4 | polygon`;
  `output_format` ∈ `csv | xye | fxye | dat | h5 | 2d_csv`;
  `azimuthal_method` (error model) ∈ `poisson | azimuthal | hybrid`;
  `plot_theme` (colormap) ∈ `hot | gray | viridis | inferno | plasma | turbo`.
  `visible_tabs` = the list of **optional** tabs to show (`Calib. Refinement`,
  `Corrections`, `PDF Analysis`, `Texture`, `Pump Probe`, `Results & Export`); the
  four always-on tabs are implicit. Omit to show all. Unlike the rest, this one
  applies immediately (no restart).

> **Important — lists replace.** If you include `materials`, `calibrants`,
> `pixel_presets`, or `k_edge_foils`, that section becomes your **complete** list
> (it replaces the built-in one). Omit a section to keep the built-in list, or use
> the Preferences dialog, which pre-fills the full shipped list so you always start
> complete. Valid JSON only — no comments, no trailing commas.

---

## 5. Reset to the shipped defaults

- **In the dialog:** Preferences ▸ **Reset to shipped defaults** (then restart).
- **By hand:** delete your per-user `config.json` (§2) and relaunch.

Either returns every setting to the built-in shipped values.

---

## 6. Sharing settings with colleagues

There is no shared-file mechanism to configure — you just move a JSON around:

1. Set everything up via the Preferences dialog.
2. **Preferences ▸ Save config to JSON…** and choose a location (email it, drop it
   on a share, commit it to a project repo — your choice).
3. A colleague opens **Preferences ▸ Load config (JSON)…**, picks the file, reviews
   it, and clicks **Save as my defaults**.

(Or simply copy the file into their per-user config path from §2.)

---

## 7. Common recipes

- **Set a beamline's default energy and detector:** Preferences ▸ Geometry set
  `wavelength_A` and `pixel_um` (or click the **px** label → your detector) → Save →
  **Save config to JSON…** and share it.
- **Add a house sample phase:** Preferences ▸ Materials ▸ Add → lattice + SG.
- **Default to polygon integration + XYE output:** Preferences ▸ Algorithms → kernel
  `polygon`, output `xye`.
- **Point at your data folders:** Preferences ▸ Paths → set the sample/calibration
  files & folders.

## 8. Troubleshooting

- **My change didn't apply.** Restart the GUI (settings load at startup).
- **The GUI ignored my file.** It's probably invalid JSON — the app falls back to
  built-ins. Re-save through the Preferences dialog (it always writes valid JSON).
- **I lost the shipped materials/calibrants.** A hand-edited file with a partial
  `materials` (or `calibrants`) list replaces the built-ins. Open Preferences (or
  **Reset to shipped defaults**) to get the full list back, then re-curate.
- **Back to factory defaults.** Preferences ▸ **Reset to shipped defaults** (or
  delete the per-user `config.json`).
