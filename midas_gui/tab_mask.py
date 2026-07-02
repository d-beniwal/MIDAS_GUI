"""Tab 1 — Mask Builder.

Threshold mask (always applied) + checkbox-gated statistical spatial-outlier
auto-mask, with TIFF save/load and a red bad-pixel overlay.  Ported from v3.
"""
from __future__ import annotations

import glob as _glob
from pathlib import Path
from typing import Optional

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

from midas_gui.constants import _SENTINELS, H5_EXTS, DEFAULT_CALIBRANT_TIF
from midas_gui.helpers import _load_image, _fspin, _NoScrollSpinBox, _browse, is_h5
from midas_gui.widgets import ImageViewer
from midas_gui.workers import MaskComputeWorker
from midas_gui import style as S


class MaskTab(QtWidgets.QWidget):
    maskReady = QtCore.pyqtSignal(object)   # np.ndarray (uint8) or None

    def __init__(self, parent=None):
        super().__init__(parent)
        self._image: Optional[np.ndarray] = None
        self._orig_dtype: Optional[np.dtype] = None
        self._mask: Optional[np.ndarray] = None
        self._thresh_mask: Optional[np.ndarray] = None
        self._mask_worker = None
        self._calib_result = None
        # Mask composed of: computed (threshold/stat/…) OR hand-drawn shapes
        self._computed_mask: Optional[np.ndarray] = None   # bool (NZ, NY) or None
        self._drawn_mask: Optional[np.ndarray] = None       # bool (NZ, NY) or None
        self._shapes: list = []        # [{'kind':'shape'|'annulus', ...}]
        self._points: list = []        # [(col, row)] single-pixel picks
        self._point_items: list = []   # scatter markers for points
        self._point_mode = False
        self._click_proxy = None
        # Freeform click-polygon state
        self._freeform_mode = False
        self._freeform_pts: list = []    # [(x, y)] image-coord vertices
        self._freeform_line = None       # pg.PlotDataItem — live edge preview
        self._freeform_vdots = None      # pg.ScatterPlotItem — vertex markers
        self._build_ui()
        if Path(self._img_edit.text().strip() or "x").exists():
            self._load_image()

    def set_calibration(self, result):
        """Receive calibration from Tab 2 — enables geometry-based mask methods."""
        self._calib_result = result
        if result is not None:
            self._geom_group.setEnabled(True)
            self._geom_note.setText(
                f"Calibration available (Lsd={result.Lsd/1000:.2f} mm) — "
                "azimuthal & learnable masks enabled.")

    def _build_ui(self):
        root = QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6); root.setSpacing(8)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True); scroll.setFixedWidth(468)
        inner = QtWidgets.QWidget(); lv = QtWidgets.QVBoxLayout(inner)
        lv.setContentsMargins(2, 2, 2, 2); lv.setSpacing(8)
        scroll.setWidget(inner)

        def _br(w=30):
            b = QtWidgets.QPushButton("…"); b.setFixedWidth(w); return b

        def _frow(edit, slot):
            r = QtWidgets.QHBoxLayout(); r.setSpacing(4)
            r.addWidget(edit); b = _br(); b.clicked.connect(slot); r.addWidget(b)
            return r

        # ── Image ──
        img = S.make_card("Image")
        self._img_edit = QtWidgets.QLineEdit(DEFAULT_CALIBRANT_TIF)
        self._img_edit.setPlaceholderText("Select image file…")
        img.body.addLayout(_frow(self._img_edit, self._browse_img))
        self._h5loc_edit = QtWidgets.QLineEdit("exchange/data")
        self._h5loc_lbl = S.LabelRight("Dataset:")
        self._h5loc_lbl.setVisible(False); self._h5loc_edit.setVisible(False)
        ds = QtWidgets.QHBoxLayout(); ds.setSpacing(4)
        ds.addWidget(self._h5loc_lbl); ds.addWidget(self._h5loc_edit, 1)
        img.body.addLayout(ds)
        self._img_edit.textChanged.connect(lambda p: (
            self._h5loc_lbl.setVisible(is_h5(p)), self._h5loc_edit.setVisible(is_h5(p))))
        self._img_edit.returnPressed.connect(self._load_image)
        self._h5loc_edit.editingFinished.connect(
            lambda: self._image is not None and self._load_image())
        lv.addWidget(img)

        # ── 1 · Threshold ──
        thr = S.make_card("1 · Threshold mask")
        self._lower = _fspin(-1e9, 1e9, 1, 0.0)
        self._lower.setToolTip("Pixels ≤ this value are masked (dead / gap / beam-stop)")
        self._upper = _fspin(0, 5e9, 0, 1_048_575)
        self._upper.setToolTip("Pixels > this value are masked. Auto-filled from dtype on load.")
        thr.body.addLayout(S.Form().row(("pixel ≤", self._lower), ("pixel >", self._upper)))
        lv.addWidget(thr)

        # ── 2 · Statistical auto-mask ──
        auto = S.make_card("2 · Statistical auto-mask")
        self._auto_check = QtWidgets.QCheckBox("Enable  (spatial outlier — numpy/scipy)")
        self._auto_check.setToolTip(
            "Flags pixels whose local Z-score exceeds K_σ AND whose intensity\n"
            "passes the hot/dead magnitude gate. No MIDAS geometry required.")
        auto.body.addWidget(self._auto_check)
        self._auto_widget = QtWidgets.QWidget()
        awf = QtWidgets.QVBoxLayout(self._auto_widget); awf.setContentsMargins(0, 0, 0, 0); awf.setSpacing(5)
        self._k_sigma = _fspin(1.0, 20.0, 1, 6.0)
        self._hot_factor = _fspin(0.1, 10.0, 2, 1.5)
        self._dead_factor = _fspin(0.0, 1.0, 2, 0.5)
        self._frozen_frac = _fspin(0.0, 1.0, 3, 0.05)
        self._frozen_frac.setToolTip(
            "Temporal constancy: flag pixels whose frame-to-frame std < this × Q75(std).\n"
            "Catches constant-value regions (dead modules, stuck pixels).\n"
            "0 = disabled. Requires a stack of ≥2 frames.")
        f1 = S.Form(); f1.row(("K_σ:", self._k_sigma), ("Hot:", self._hot_factor))
        f1.row(("Dead:", self._dead_factor), ("Frozen:", self._frozen_frac))
        awf.addLayout(f1)
        self._stack_ed = QtWidgets.QLineEdit()
        self._stack_ed.setPlaceholderText("stack folder / *.tif  (blank = single image)")
        awf.addWidget(QtWidgets.QLabel("Stack (optional — temporal median):"))
        awf.addLayout(_frow(self._stack_ed, self._browse_stack))
        self._stride_spin = _NoScrollSpinBox(); self._stride_spin.setRange(1, 100); self._stride_spin.setValue(1)
        awf.addLayout(S.Form().row(("Stride:", self._stride_spin)))
        self._auto_widget.setVisible(False)
        self._auto_check.toggled.connect(self._auto_widget.setVisible)
        auto.body.addWidget(self._auto_widget)
        self._stat_prog = QtWidgets.QLabel("")
        self._stat_prog.setStyleSheet("color:#7fb8ff;font-size:10px"); self._stat_prog.setWordWrap(True)
        auto.body.addWidget(self._stat_prog)
        lv.addWidget(auto)

        # ── 3 · Spike rejection ──
        spike = S.make_card("3 · Spatial spike rejection")
        self._spike_check = QtWidgets.QCheckBox("Enable  (Laplacian spike detector)")
        self._spike_check.setToolTip(
            "Flags isolated single-pixel spikes via a Laplacian high-pass + robust σ.")
        spike.body.addWidget(self._spike_check)
        self._spike_sigma = _fspin(1.0, 20.0, 1, 5.0)
        spike.body.addLayout(S.Form().row(("n_σ:", self._spike_sigma)))
        lv.addWidget(spike)

        # ── 3b · Cosmic-ray rejection ──
        cosmic = S.make_card("3b · Cosmic-ray rejection (temporal)")
        self._cosmic_check = QtWidgets.QCheckBox(
            "Enable  (temporal σ-clip across frames)")
        self._cosmic_check.setToolTip(
            "Flags pixels that are statistical outliers in any frame compared\n"
            "to the per-pixel temporal median.\n"
            "Requires a stack of ≥3 frames in the same folder as section 2.\n"
            "Uses the stack folder specified in section 2 above.")
        cosmic.body.addWidget(self._cosmic_check)
        self._cosmic_sigma = _fspin(1.0, 20.0, 1, 5.0)
        self._cosmic_sigma.setToolTip("n_σ threshold — pixels deviating more than\n"
                                      "this many MAD-σ from the temporal median are flagged.")
        cosmic.body.addLayout(S.Form().row(("n_σ:", self._cosmic_sigma)))
        cosmic_note = QtWidgets.QLabel(
            "Uses the stack folder from section 2.  "
            "Needs ≥3 frames.  Flags cosmic-ray hits that look like isolated\n"
            "bright spikes in individual frames but are absent in other frames.")
        cosmic_note.setWordWrap(True)
        cosmic_note.setStyleSheet(f"color:{S.MUTED};font-size:10px")
        cosmic.body.addWidget(cosmic_note)
        lv.addWidget(cosmic)

        # ── 4 · Calibration-based masks ──
        self._geom_group = S.make_card("4 · Calibration-based masks")
        self._geom_group.setEnabled(False)
        self._geom_note = QtWidgets.QLabel("Run Tab 2 calibration to enable these.")
        self._geom_note.setStyleSheet(f"color:{S.ACCENT};font-size:10px"); self._geom_note.setWordWrap(True)
        self._geom_group.body.addWidget(self._geom_note)
        self._azim_check = QtWidgets.QCheckBox("Azimuthal σ-clip")
        self._azim_check.setToolTip("Per-(R,η) ring-uniformity outlier clip (needs geometry).")
        self._azim_sigma = _fspin(1.0, 20.0, 1, 5.0)
        self._geom_group.body.addLayout(S.Form().row(("Azimuthal n_σ:", self._azim_sigma)))
        self._geom_group.body.addWidget(self._azim_check)
        self._learn_check = QtWidgets.QCheckBox("Learnable mask")
        self._learn_check.setToolTip(
            "Differentiable per-pixel weights optimised against ring η-uniformity.")
        self._geom_group.body.addWidget(self._learn_check)
        self._learn_steps = _NoScrollSpinBox(); self._learn_steps.setRange(50, 2000); self._learn_steps.setValue(300)
        self._learn_lr = _fspin(0.01, 5.0, 2, 0.5)
        self._learn_sparsity = _fspin(0.0, 1.0, 5, 1e-4)
        lf = S.Form()
        lf.row(("steps:", self._learn_steps), ("lr:", self._learn_lr))
        lf.row(("sparsity:", self._learn_sparsity), (None, QtWidgets.QWidget()))
        self._geom_group.body.addLayout(lf)
        lv.addWidget(self._geom_group)

        # ── Compute + stats ──
        compute_btn = S.primary_btn("Compute Mask")
        compute_btn.clicked.connect(self._compute)
        lv.addWidget(compute_btn)
        self._stat_lbl = QtWidgets.QLabel("No mask computed.")
        self._stat_lbl.setWordWrap(True); self._stat_lbl.setStyleSheet(f"color:{S.MUTED};font-size:10px")
        lv.addWidget(self._stat_lbl)

        # ── Save / Load ──
        sl = S.make_card("Save / Load")
        self._save_edit = QtWidgets.QLineEdit(); self._save_edit.setPlaceholderText("mask.tif")
        srow = QtWidgets.QHBoxLayout(); srow.setSpacing(4)
        srow.addWidget(self._save_edit, 1)
        b2 = _br(); b2.clicked.connect(self._browse_save); srow.addWidget(b2)
        self._save_btn = QtWidgets.QPushButton("Save"); self._save_btn.setEnabled(False)
        self._save_btn.setFixedWidth(52); self._save_btn.clicked.connect(self._save); srow.addWidget(self._save_btn)
        sl.body.addLayout(srow)
        self._load_mask_edit = QtWidgets.QLineEdit(); self._load_mask_edit.setPlaceholderText("Load existing mask…")
        lrow = QtWidgets.QHBoxLayout(); lrow.setSpacing(4)
        lrow.addWidget(self._load_mask_edit, 1)
        b3 = _br(); b3.clicked.connect(self._browse_load_mask); lrow.addWidget(b3)
        sl.body.addLayout(lrow)
        lv.addWidget(sl)

        lv.addStretch(1)
        root.addWidget(scroll)

        # Right: overlay toggle + viewer
        right_panel = QtWidgets.QWidget()
        rv = QtWidgets.QVBoxLayout(right_panel)
        rv.setContentsMargins(0, 0, 0, 0); rv.setSpacing(2)
        ov_bar = QtWidgets.QHBoxLayout()
        self._show_overlay_check = QtWidgets.QCheckBox("Show bad pixels overlay")
        self._show_overlay_check.setChecked(True)
        self._show_overlay_check.toggled.connect(self._on_show_overlay)
        ov_bar.addWidget(self._show_overlay_check)
        self._draw_check = QtWidgets.QCheckBox("Draw mask")
        self._draw_check.setToolTip("Reveal tools to draw shapes that mark regions to mask out.")
        self._draw_check.toggled.connect(self._on_draw_toggled)
        ov_bar.addWidget(self._draw_check)
        ov_bar.addStretch(1)
        rv.addLayout(ov_bar)

        # Shape-drawing toolbar (hidden until "Draw mask" is on)
        self._draw_bar = QtWidgets.QWidget()
        db = QtWidgets.QHBoxLayout(self._draw_bar)
        db.setContentsMargins(0, 0, 0, 0); db.setSpacing(4)
        db.addWidget(QtWidgets.QLabel("Add:"))
        for label, slot in [("Rectangle", self._add_rect), ("Oval", self._add_oval),
                            ("Circle", self._add_circle), ("Polygon", self._add_polygon),
                            ("Annulus", self._add_annulus)]:
            b = QtWidgets.QPushButton(label); b.clicked.connect(slot)
            b.setFixedHeight(24); db.addWidget(b)
        self._freeform_btn = QtWidgets.QPushButton("Freeform"); self._freeform_btn.setCheckable(True)
        self._freeform_btn.setFixedHeight(24)
        self._freeform_btn.setToolTip(
            "Click to place vertices one by one.\n"
            "Lines connect each vertex to the next.\n"
            "Double-click OR press 'Close shape' to finish and seal the polygon.\n"
            "Need ≥ 3 vertices to close.")
        self._freeform_btn.toggled.connect(self._toggle_freeform_mode)
        db.addWidget(self._freeform_btn)
        self._close_shape_btn = QtWidgets.QPushButton("Close shape")
        self._close_shape_btn.setFixedHeight(24)
        self._close_shape_btn.setToolTip("Seal the freeform polygon and add it to the shape list.")
        self._close_shape_btn.setVisible(False)
        self._close_shape_btn.clicked.connect(self._close_freeform)
        db.addWidget(self._close_shape_btn)
        self._point_btn = QtWidgets.QPushButton("Point"); self._point_btn.setCheckable(True)
        self._point_btn.setFixedHeight(24)
        self._point_btn.setToolTip("Toggle, then click pixels to mask them individually.")
        self._point_btn.toggled.connect(self._toggle_point_mode)
        db.addWidget(self._point_btn)
        db.addStretch(1)
        self._apply_shapes_btn = QtWidgets.QPushButton("Apply shapes → mask")
        self._apply_shapes_btn.clicked.connect(self._apply_shapes); self._apply_shapes_btn.setFixedHeight(24)
        db.addWidget(self._apply_shapes_btn)
        clr = QtWidgets.QPushButton("Clear shapes"); clr.clicked.connect(self._clear_shapes); clr.setFixedHeight(24)
        db.addWidget(clr)
        self._draw_bar.setVisible(False)
        rv.addWidget(self._draw_bar)

        self._viewer = ImageViewer(title="")
        rv.addWidget(self._viewer, stretch=1)
        root.addWidget(right_panel, stretch=1)

    # ── Actions ──────────────────────────────────────────────────

    def _browse_img(self):
        p = _browse(self, "Open Image",
                    "Images (*.tif *.tiff *.h5 *.hdf5 *.hdf *.nxs *.ge*);;All (*)")
        if p: self._img_edit.setText(p); self._load_image()

    def _browse_save(self):
        p, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Mask", "mask.tif", "TIFF (*.tif);;All (*)")
        if p: self._save_edit.setText(p)

    def _browse_load_mask(self):
        p = _browse(self, "Open Mask", "TIFF (*.tif *.tiff);;All (*)")
        if p: self._load_mask_edit.setText(p); self._load_existing_mask()

    def _browse_stack(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Select stack folder")
        if d: self._stack_ed.setText(d)

    def _load_image(self):
        path = self._img_edit.text().strip()
        if not path or not Path(path).exists():
            QtWidgets.QMessageBox.warning(self, "Error", "File not found."); return
        try:
            data_loc = self._h5loc_edit.text().strip() or "exchange/data"
            import tifffile
            raw = tifffile.imread(path) if Path(path).suffix.lower() in (".tif", ".tiff") \
                  else _load_image(path, data_loc=data_loc)
            self._orig_dtype = raw.dtype
            self._image = raw.astype(np.float32)
            sentinel = _SENTINELS.get(np.dtype(raw.dtype).name)
            if sentinel is not None:
                self._upper.setValue(float(sentinel))
            self._viewer.set_image(self._image)
            self._stat_lbl.setText(
                f"Loaded: {raw.shape[1]}×{raw.shape[0]}  dtype={raw.dtype}  "
                f"range [{raw.min():.0f}, {raw.max():.0f}]")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Load error", str(e))

    def _on_show_overlay(self, visible: bool):
        self._viewer.set_overlay_visible(visible)

    def _compute(self):
        if self._image is None:
            QtWidgets.QMessageBox.warning(self, "No image", "Load an image first."); return
        if self._mask_worker and self._mask_worker.isRunning():
            return
        lower = self._lower.value(); upper = self._upper.value()
        img = self._image
        thresh_mask = np.zeros(img.shape, dtype=np.uint8)
        if lower > -1e9:
            thresh_mask |= (img <= lower).astype(np.uint8)
        if upper > 0:
            thresh_mask |= (img > upper).astype(np.uint8)
        self._thresh_mask = thresh_mask

        methods = {}
        if self._auto_check.isChecked():
            methods["stat"] = {
                "k_sigma": self._k_sigma.value(),
                "hot_factor": self._hot_factor.value(),
                "dead_factor": self._dead_factor.value(),
                "frozen_frac": self._frozen_frac.value(),
                "overflow": upper if upper > 0 else None,
            }
        if self._spike_check.isChecked():
            methods["spike"] = {"n_sigma": self._spike_sigma.value(), "method": "laplacian"}
        if self._cosmic_check.isChecked():
            methods["cosmic_ray"] = {"n_sigma": self._cosmic_sigma.value()}
        if self._azim_check.isChecked() and self._calib_result is not None:
            methods["azimuthal"] = {"n_sigma": self._azim_sigma.value()}
        if self._learn_check.isChecked() and self._calib_result is not None:
            methods["learnable"] = {
                "n_steps": self._learn_steps.value(), "lr": self._learn_lr.value(),
                "sparsity_weight": self._learn_sparsity.value(), "init_weight": 0.9,
            }

        if not methods:
            self._set_mask(thresh_mask); return

        self._stat_prog.setText("Computing mask…")
        self._mask_worker = MaskComputeWorker(
            self._image, thresh_mask, methods,
            stack_paths=self._collect_stack_paths(),
            calib_result=self._calib_result, parent=self)
        self._mask_worker.progress.connect(self._stat_prog.setText)
        self._mask_worker.finished.connect(self._on_mask_done)
        self._mask_worker.failed.connect(self._on_stat_fail)
        self._mask_worker.start()

    def _collect_stack_paths(self) -> list:
        raw = self._stack_ed.text().strip()
        if not raw:
            return []
        p = Path(raw)
        if p.is_dir():
            paths = []
            for ext in ("*.tif", "*.tiff", "*.h5", "*.hdf5", "*.ge*"):
                paths.extend(sorted(p.glob(ext)))
        elif "*" in raw or "?" in raw:
            paths = sorted(Path(f) for f in _glob.glob(raw))
        elif p.is_file():
            paths = [p]
        else:
            return []
        stride = max(1, self._stride_spin.value())
        return [str(x) for x in paths[::stride]]

    def _on_mask_done(self, mask: np.ndarray):
        self._set_mask(mask)

    def _on_stat_fail(self, msg: str):
        self._stat_prog.setText("Failed — check parameters.")
        QtWidgets.QMessageBox.critical(
            self, "Mask error", f"Statistical outlier mask failed:\n\n{msg[:500]}")

    def _set_mask(self, mask):
        """Set the *computed* mask (threshold/stat/loaded) and refresh the final mask."""
        self._computed_mask = None if mask is None else mask.astype(bool)
        self._emit_final()

    def _emit_final(self):
        """Combine computed + hand-drawn masks, update overlay/stats, emit maskReady."""
        parts = [m for m in (self._computed_mask, self._drawn_mask) if m is not None]
        if not parts:
            self._mask = None
            self._viewer.clear_overlay()
            self._stat_lbl.setText("No mask computed.")
            return
        final = parts[0].copy()
        for m in parts[1:]:
            final |= m
        final = final.astype(np.uint8)
        self._mask = final
        n_bad = int(final.sum()); n_tot = final.size; pct = 100 * n_bad / n_tot
        drawn_n = int(self._drawn_mask.sum()) if self._drawn_mask is not None else 0
        self._stat_lbl.setText(
            f"Bad pixels: {n_bad:,} / {n_tot:,} ({pct:.2f}%)"
            + (f"   (incl. {drawn_n:,} hand-drawn)" if drawn_n else "")
            + f"\nGood pixels: {n_tot - n_bad:,} ({100 - pct:.2f}%)")
        self._viewer.set_mask_overlay(final)
        self._viewer.set_overlay_visible(self._show_overlay_check.isChecked())
        self._save_btn.setEnabled(True)
        self.maskReady.emit(final)

    # ── Hand-drawn shape masks ────────────────────────────────────

    _SHAPE_PEN = None  # set lazily

    def _on_draw_toggled(self, on: bool):
        if on and self._image is None:
            QtWidgets.QMessageBox.warning(self, "No image", "Load an image first.")
            self._draw_check.setChecked(False); return
        self._draw_bar.setVisible(on)
        if not on:
            if self._point_btn.isChecked():
                self._point_btn.setChecked(False)
            if self._freeform_btn.isChecked():
                self._freeform_btn.setChecked(False)  # triggers _cancel_freeform via toggled signal

    def _pen(self):
        return pg.mkPen("#ff5a5a", width=2)

    def _ipen(self):
        return pg.mkPen("#5ab0ff", width=2, style=QtCore.Qt.DashLine)

    def _default_geom(self):
        """Reasonable starting (pos, size) for a new ROI, in image (x=Y, y=Z) coords."""
        NZ, NY = self._image.shape
        sx, sy = NY * 0.25, NZ * 0.25
        return (NY * 0.5 - sx / 2, NZ * 0.5 - sy / 2), (sx, sy)

    def _register(self, roi):
        roi.setZValue(20)
        roi.removable = True
        roi.sigRemoveRequested.connect(self._remove_roi)
        self._viewer._iv.addItem(roi)
        return roi

    def _add_rect(self):
        if not self._guard_draw(): return
        pos, size = self._default_geom()
        roi = pg.RectROI(pos, size, pen=self._pen(), rotatable=True)
        self._shapes.append({"kind": "shape", "roi": self._register(roi)})

    def _add_oval(self):
        if not self._guard_draw(): return
        pos, size = self._default_geom()
        roi = pg.EllipseROI(pos, size, pen=self._pen(), rotatable=True)
        self._shapes.append({"kind": "shape", "roi": self._register(roi)})

    def _add_circle(self):
        if not self._guard_draw(): return
        NZ, NY = self._image.shape
        d = min(NY, NZ) * 0.25
        roi = pg.CircleROI((NY * 0.5 - d / 2, NZ * 0.5 - d / 2), (d, d), pen=self._pen())
        self._shapes.append({"kind": "shape", "roi": self._register(roi)})

    def _add_polygon(self):
        if not self._guard_draw(): return
        NZ, NY = self._image.shape
        cx, cy, r = NY * 0.5, NZ * 0.5, min(NY, NZ) * 0.18
        pts = [[cx + r, cy], [cx, cy + r], [cx - r, cy], [cx, cy - r]]
        roi = pg.PolyLineROI(pts, closed=True, pen=self._pen())
        self._shapes.append({"kind": "shape", "roi": self._register(roi)})

    def _add_annulus(self):
        if not self._guard_draw(): return
        NZ, NY = self._image.shape
        do = min(NY, NZ) * 0.4; di = do * 0.5
        cx, cy = NY * 0.5, NZ * 0.5
        outer = pg.EllipseROI((cx - do / 2, cy - do / 2), (do, do), pen=self._pen(), rotatable=True)
        inner = pg.EllipseROI((cx - di / 2, cy - di / 2), (di, di), pen=self._ipen(), rotatable=True)
        self._register(outer); self._register(inner)
        self._shapes.append({"kind": "annulus", "outer": outer, "inner": inner})

    def _guard_draw(self) -> bool:
        if self._image is None:
            QtWidgets.QMessageBox.warning(self, "No image", "Load an image first.")
            return False
        return True

    def _remove_roi(self, roi):
        for item in list(self._shapes):
            if item["kind"] == "shape" and item["roi"] is roi:
                self._viewer._iv.removeItem(roi); self._shapes.remove(item); return
            if item["kind"] == "annulus" and roi in (item["outer"], item["inner"]):
                self._viewer._iv.removeItem(item["outer"]); self._viewer._iv.removeItem(item["inner"])
                self._shapes.remove(item); return

    # ── Single-pixel point picking ────────────────────────────────

    def _toggle_point_mode(self, on: bool):
        if on and not self._guard_draw():
            self._point_btn.setChecked(False); return
        self._point_mode = on
        self._ensure_click_proxy()

    # ── Freeform click-polygon ────────────────────────────────────

    def _toggle_freeform_mode(self, on: bool):
        if on and not self._guard_draw():
            self._freeform_btn.setChecked(False); return
        self._freeform_mode = on
        self._close_shape_btn.setVisible(on)
        if not on:
            self._cancel_freeform()
        else:
            self._ensure_click_proxy()

    def _ensure_click_proxy(self):
        if self._click_proxy is None:
            self._click_proxy = pg.SignalProxy(
                self._viewer._iv.scene.sigMouseClicked, rateLimit=60,
                slot=self._on_scene_click)

    def _update_freeform_preview(self):
        """Redraw the live edge-lines and vertex dots from _freeform_pts."""
        pts = self._freeform_pts
        if not pts:
            return

        # Build coordinate arrays for the preview: vertices + closing segment back to start
        xs = [p[0] for p in pts] + [pts[0][0]]
        ys = [p[1] for p in pts] + [pts[0][1]]

        if self._freeform_line is None:
            self._freeform_line = pg.PlotDataItem(
                xs, ys,
                pen=pg.mkPen("#ffcc00", width=1.5),
                symbol=None)
            self._freeform_line.setZValue(22)
            self._viewer._iv.addItem(self._freeform_line)
        else:
            self._freeform_line.setData(xs, ys)

        vx = [p[0] for p in pts]
        vy = [p[1] for p in pts]
        if self._freeform_vdots is None:
            self._freeform_vdots = pg.ScatterPlotItem(
                vx, vy, symbol="o", size=7,
                pen=pg.mkPen("#ffcc00", width=1),
                brush=pg.mkBrush("#ffcc0088"))
            self._freeform_vdots.setZValue(23)
            self._viewer._iv.addItem(self._freeform_vdots)
        else:
            self._freeform_vdots.setData(vx, vy)

    def _close_freeform(self):
        """Seal the polygon: turn accumulated vertices into a PolyLineROI."""
        pts = self._freeform_pts
        if len(pts) < 3:
            QtWidgets.QMessageBox.information(
                self, "Too few vertices",
                "Place at least 3 vertices before closing the shape.")
            return
        # Remove live preview items
        self._cancel_freeform(keep_roi=True)
        # Create a proper closeable PolyLineROI from the accumulated vertices
        roi = pg.PolyLineROI(list(pts), closed=True, pen=self._pen())
        self._shapes.append({"kind": "shape", "roi": self._register(roi)})
        # Reset freeform state but leave the button toggled off
        self._freeform_mode = False
        self._freeform_btn.setChecked(False)
        self._close_shape_btn.setVisible(False)

    def _cancel_freeform(self, keep_roi=False):
        """Remove live preview graphics and reset vertex list."""
        if self._freeform_line is not None:
            self._viewer._iv.removeItem(self._freeform_line)
            self._freeform_line = None
        if self._freeform_vdots is not None:
            self._viewer._iv.removeItem(self._freeform_vdots)
            self._freeform_vdots = None
        self._freeform_pts = []

    def _on_scene_click(self, evt):
        event = evt[0]
        if event.button() != QtCore.Qt.LeftButton:
            return
        if self._image is None:
            return

        imgitem = self._viewer._iv.getImageItem()
        p = imgitem.mapFromScene(event.scenePos())
        x, y = float(p.x()), float(p.y())
        NZ, NY = self._image.shape

        # ── Freeform polygon mode ──
        if self._freeform_mode:
            if not (0 <= y < NZ and 0 <= x < NY):
                return
            if event.double():
                # Double-click: close without adding another vertex
                # (the preceding single-click already added the final vertex)
                if len(self._freeform_pts) >= 3:
                    self._close_freeform()
                return
            self._freeform_pts.append((x, y))
            self._update_freeform_preview()
            return

        # ── Single-pixel point mode ──
        if self._point_mode:
            col, row = int(x), int(y)
            if 0 <= row < NZ and 0 <= col < NY:
                self._points.append((col, row))
                dot = pg.ScatterPlotItem([col], [row], symbol="s", size=6,
                                         pen=pg.mkPen(None), brush=pg.mkBrush("#ff5a5a"))
                dot.setZValue(21)
                self._viewer._iv.addItem(dot); self._point_items.append(dot)

    # ── Rasterise shapes → mask ───────────────────────────────────

    def _raster_roi(self, roi) -> np.ndarray:
        """Boolean (NZ, NY) mask of pixels inside a pyqtgraph ROI (any shape)."""
        NZ, NY = self._image.shape
        imgitem = self._viewer._iv.getImageItem()
        path = imgitem.mapFromScene(roi.mapToScene(roi.shape()))
        qimg = QtGui.QImage(NY, NZ, QtGui.QImage.Format_Grayscale8)
        qimg.fill(0)
        painter = QtGui.QPainter(qimg)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(255, 255, 255))
        painter.drawPath(path)
        painter.end()
        bpl = qimg.bytesPerLine()
        ptr = qimg.constBits(); ptr.setsize(NZ * bpl)
        arr = np.frombuffer(ptr, np.uint8).reshape(NZ, bpl)[:, :NY]
        return arr > 127

    def _apply_shapes(self):
        if self._image is None:
            return
        if not self._shapes and not self._points:
            QtWidgets.QMessageBox.information(self, "No shapes", "Draw a shape or pick points first.")
            return
        NZ, NY = self._image.shape
        drawn = np.zeros((NZ, NY), dtype=bool)
        for item in self._shapes:
            if item["kind"] == "shape":
                drawn |= self._raster_roi(item["roi"])
            else:  # annulus: region between outer and inner
                drawn |= (self._raster_roi(item["outer"]) & ~self._raster_roi(item["inner"]))
        for col, row in self._points:
            drawn[row, col] = True
        self._drawn_mask = drawn
        self._emit_final()

    def _clear_shapes(self):
        # Cancel any in-progress freeform polygon first
        self._cancel_freeform()
        self._freeform_mode = False
        self._freeform_btn.setChecked(False)
        self._close_shape_btn.setVisible(False)
        for item in self._shapes:
            if item["kind"] == "shape":
                self._viewer._iv.removeItem(item["roi"])
            else:
                self._viewer._iv.removeItem(item["outer"]); self._viewer._iv.removeItem(item["inner"])
        self._shapes.clear()
        for dot in self._point_items:
            self._viewer._iv.removeItem(dot)
        self._point_items.clear(); self._points.clear()
        self._drawn_mask = None
        self._emit_final()

    def _save(self):
        if self._mask is None: return
        path = self._save_edit.text().strip()
        if not path:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Save Mask", "mask.tif", "TIFF (*.tif)")
        if not path: return
        try:
            import tifffile
            tifffile.imwrite(str(path), self._mask)
            QtWidgets.QMessageBox.information(self, "Saved", f"Mask saved:\n{path}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Save error", str(e))

    def _load_existing_mask(self):
        path = self._load_mask_edit.text().strip()
        if not path or not Path(path).exists():
            QtWidgets.QMessageBox.warning(self, "Error", "File not found."); return
        try:
            import tifffile
            raw = tifffile.imread(path)
            self._set_mask((raw != 0).astype(np.uint8))
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Load error", str(e))

    def get_mask(self) -> Optional[np.ndarray]:
        return self._mask
