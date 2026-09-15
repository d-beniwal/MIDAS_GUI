"""Auto Attenuation popup window.

Runs entirely inside its own OS process (see ``app.py``): everything here
only ever touches the one-time snapshot dict loaded at startup, never the
main GUI's live objects.
"""

import json
import os
import traceback

from PyQt5 import QtCore, QtNetwork, QtWidgets

from midas_gui.auto_attenuation import analysis, refresh_server, saturation
from midas_gui.auto_attenuation.attenuator_table import DEFAULT_POSITION_THICKNESS_MM


def _monospace_font():
    try:
        from PyQt5 import QtGui
        f = QtGui.QFont("Menlo")
        f.setStyleHint(QtGui.QFont.Monospace)
        return f
    except Exception:
        return None


class _AnalysisWorker(QtCore.QThread):
    logLine = QtCore.pyqtSignal(str)
    finishedRun = QtCore.pyqtSignal(dict)
    failed = QtCore.pyqtSignal(str)

    def __init__(self, snapshot, params, parent=None):
        super().__init__(parent)
        self._snapshot = snapshot
        self._p = params

    def run(self):
        try:
            p = self._p
            frames = self._snapshot["frames"]
            dark = self._snapshot.get("dark") if p["apply_dark"] else None
            user_mask = self._snapshot.get("mask") if p["apply_user_mask"] else None
            dark_stack = (
                self._snapshot.get("dark_stack") if p["apply_dark_mask"] else None
            )

            pre = analysis.preprocess_stack(
                frames, dark=dark, user_mask=user_mask, dark_mask_stack=dark_stack,
                skip_frames=p["skip_frames"], apply_dark=p["apply_dark"],
                apply_dark_mask=p["apply_dark_mask"], apply_user_mask=p["apply_user_mask"],
                frozen_mask=p["frozen_mask"], frozen_std_cutoff=p["frozen_std_cutoff"],
                hot_pixel_mask=p["hot_pixel_mask"], noise_floor=p["noise_floor"],
                min_hot_intensity=p["min_hot_intensity"],
                percentile_mask=p["percentile_mask"],
                dark_mask_n_sigma=p["dark_mask_n_sigma"],
                dark_mask_local_window=p["dark_mask_local_window"],
            )
            for line in pre.log:
                self.logLine.emit(line)

            sat = saturation.check_saturation(
                pre.data, None, p["saturation_intensity"], p["tolerate_n"],
                skip_frames=0,
            )
            if not sat.ok:
                self.finishedRun.emit({"ok": False, "saturated": True,
                                        "saturation": sat})
                return

            result = analysis.run_single_capture(
                pre, energy_keV=p["energy_keV"], att_pos=p["att_pos"],
                acq_time_s=p["acq_time_s"], target_intensity=p["target_intensity"],
                min_intensity=p["min_intensity"],
                thickness_table=p["thickness_table"],
            )
            self.finishedRun.emit(result)
        except Exception:
            self.failed.emit(traceback.format_exc())


class AutoAttenuationDialog(QtWidgets.QMainWindow):
    """Standalone Auto Attenuation window (built in its own QApplication)."""

    def __init__(self, snapshot, parent=None, snapshot_path=None,
                 refresh_server_name=refresh_server.SERVER_NAME):
        super().__init__(parent)
        self.setWindowTitle("Auto Attenuation")
        self.resize(980, 620)
        self._snapshot = snapshot
        self._snapshot_path = snapshot_path
        self._refresh_server_name = refresh_server_name
        self._worker = None

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        outer = QtWidgets.QHBoxLayout(central)

        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)

        # The control panel can grow taller than the screen (many mask
        # sub-sections, the attenuator table, …) — scroll it instead of
        # letting the window itself grow unbounded.
        scroll = QtWidgets.QScrollArea()
        scroll.setWidget(left)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setMaximumWidth(440)
        outer.addWidget(scroll)

        self._build_source_summary(left_layout)
        self._build_primary_fields(left_layout)
        self._build_mask_checkboxes(left_layout)
        self._build_advanced(left_layout)

        self.run_btn = QtWidgets.QPushButton("Run")
        self.run_btn.clicked.connect(self._on_run)
        left_layout.addWidget(self.run_btn)
        left_layout.addStretch(1)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        font = _monospace_font()
        if font is not None:
            self.log.setFont(font)
        outer.addWidget(self.log, 1)

        self._prefill_from_snapshot()

    # ── UI construction ──────────────────────────────────────────────

    _SOURCE_LABELS = {"buffer": "Buffer", "loaded": "Loaded data (Data Viewer)"}

    def _source_summary_text(self):
        frames = self._snapshot["frames"]
        n = frames.shape[0] if frames.ndim == 3 else 1
        shape = frames.shape[-2:]
        source_label = self._SOURCE_LABELS.get(
            self._snapshot.get("source"), "Unspecified")
        bits = [f"Source: {source_label}",
                f"{n} frame(s), {shape[0]}x{shape[1]} px"]
        bits.append("Dark: " + ("yes" if "dark" in self._snapshot else "none"))
        bits.append(
            "Dark stack (for dark mask): "
            + ("yes" if "dark_stack" in self._snapshot else "none")
        )
        bits.append("Mask: " + ("yes" if "mask" in self._snapshot else "none"))
        return "\n".join(bits)

    def _build_source_summary(self, layout):
        box = QtWidgets.QGroupBox("Captured from Data Viewer")
        v = QtWidgets.QVBoxLayout(box)
        self._source_summary_lbl = QtWidgets.QLabel(self._source_summary_text())
        self._source_summary_lbl.setWordWrap(True)
        v.addWidget(self._source_summary_lbl)
        self.refresh_source_btn = QtWidgets.QPushButton("Refresh from Data Viewer")
        self.refresh_source_btn.setToolTip(
            "Ask the main MIDAS GUI for its current buffer/loaded data, "
            "dark and mask, and reload this window with it."
        )
        self.refresh_source_btn.clicked.connect(self._on_refresh_source)
        v.addWidget(self.refresh_source_btn)
        layout.addWidget(box)

    def _build_primary_fields(self, layout):
        box = QtWidgets.QGroupBox("Attenuation estimate")
        form = QtWidgets.QFormLayout(box)

        self.energy_spin = QtWidgets.QDoubleSpinBox()
        self.energy_spin.setRange(0.1, 999.0)
        self.energy_spin.setDecimals(4)
        self.energy_spin.setSuffix(" keV")
        form.addRow("Energy:", self.energy_spin)

        self.att_level_spin = QtWidgets.QSpinBox()
        self.att_level_spin.setRange(0, 999)
        form.addRow("Attenuator level:", self.att_level_spin)

        self.exposure_spin = QtWidgets.QDoubleSpinBox()
        self.exposure_spin.setRange(0.0001, 100000.0)
        self.exposure_spin.setDecimals(4)
        self.exposure_spin.setValue(1.0)
        self.exposure_spin.setSuffix(" s")
        form.addRow("Exposure time / frame:", self.exposure_spin)

        self.saturation_spin = QtWidgets.QDoubleSpinBox()
        self.saturation_spin.setRange(0.0, 1.0e9)
        self.saturation_spin.setDecimals(1)
        self.saturation_spin.setValue(0.0)
        self.saturation_spin.setToolTip(
            "Required — maximum pixel value below which a pixel is not "
            "considered saturated."
        )
        form.addRow("Detector saturation intensity:", self.saturation_spin)

        self.tolerate_spin = QtWidgets.QSpinBox()
        self.tolerate_spin.setRange(0, 10_000_000)
        self.tolerate_spin.setValue(5)
        form.addRow("Tolerate N oversaturated px:", self.tolerate_spin)

        self.target_spin = QtWidgets.QDoubleSpinBox()
        self.target_spin.setRange(1.0, 1.0e9)
        self.target_spin.setDecimals(1)
        self.target_spin.setValue(50000.0)
        form.addRow("Target intensity:", self.target_spin)

        layout.addWidget(box)

    @staticmethod
    def _nested_params(checkbox, rows):
        """A QFormLayout of *rows* ``(label, widget)`` indented under
        *checkbox*, shown only while it's checked."""
        container = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(container)
        form.setContentsMargins(20, 2, 0, 6)
        for label, widget in rows:
            form.addRow(label, widget)
        container.setVisible(checkbox.isChecked())
        checkbox.toggled.connect(container.setVisible)
        return container

    def _apply_source_availability(self):
        """Sync the dark/dark-mask/user-mask checkboxes to what
        ``self._snapshot`` actually has — called at construction and again
        after a successful refresh, since a refreshed snapshot's dark/mask
        availability can differ from what the window opened with."""
        has_dark = "dark" in self._snapshot
        has_dark_stack = "dark_stack" in self._snapshot
        has_mask = "mask" in self._snapshot

        self.chk_dark.setChecked(has_dark)
        self.chk_dark.setEnabled(has_dark)

        self.chk_dark_mask.setChecked(has_dark_stack)
        self.chk_dark_mask.setEnabled(has_dark_stack)
        self.chk_dark_mask.setToolTip(
            "" if has_dark_stack else
            "Requires a dark source with multiple raw frames captured "
            "(not just a single averaged dark)."
        )

        self.chk_user_mask.setChecked(has_mask)
        self.chk_user_mask.setEnabled(has_mask)

    def _build_mask_checkboxes(self, layout):
        box = QtWidgets.QGroupBox("Masking steps")
        v = QtWidgets.QVBoxLayout(box)

        # ── Dark subtraction (no nested params) ─────────────────────
        self.chk_dark = QtWidgets.QCheckBox("Apply dark subtraction")
        v.addWidget(self.chk_dark)

        # ── Dark-derived dead/hot pixel mask ─────────────────────────
        self.chk_dark_mask = QtWidgets.QCheckBox(
            "Dark-derived dead/hot pixel mask"
        )
        v.addWidget(self.chk_dark_mask)

        self.dark_nsigma_spin = QtWidgets.QSpinBox()
        self.dark_nsigma_spin.setRange(1, 100)
        self.dark_nsigma_spin.setValue(5)
        self.dark_window_spin = QtWidgets.QSpinBox()
        self.dark_window_spin.setRange(3, 2001)
        self.dark_window_spin.setSingleStep(2)
        self.dark_window_spin.setValue(101)
        v.addWidget(self._nested_params(self.chk_dark_mask, [
            ("n-sigma:", self.dark_nsigma_spin),
            ("Local window (px):", self.dark_window_spin),
        ]))

        # ── Data Viewer mask (no nested params) ──────────────────────
        self.chk_user_mask = QtWidgets.QCheckBox("Apply Data Viewer mask")
        v.addWidget(self.chk_user_mask)
        self._apply_source_availability()

        # ── Frozen-pixel mask ─────────────────────────────────────────
        self.chk_frozen = QtWidgets.QCheckBox("Frozen-pixel mask")
        self.chk_frozen.setChecked(True)
        v.addWidget(self.chk_frozen)

        self.frozen_std_spin = QtWidgets.QDoubleSpinBox()
        self.frozen_std_spin.setRange(0.0, 1.0e6)
        self.frozen_std_spin.setDecimals(3)
        self.frozen_std_spin.setValue(0.5)
        v.addWidget(self._nested_params(self.chk_frozen, [
            ("Std cutoff:", self.frozen_std_spin),
        ]))

        # ── Isolated hot-pixel mask ───────────────────────────────────
        self.chk_hot = QtWidgets.QCheckBox("Isolated hot-pixel mask")
        self.chk_hot.setChecked(True)
        v.addWidget(self.chk_hot)

        self.noise_floor_spin = QtWidgets.QDoubleSpinBox()
        self.noise_floor_spin.setRange(0.0, 1.0e9)
        self.noise_floor_spin.setValue(30.0)
        self.min_hot_spin = QtWidgets.QDoubleSpinBox()
        self.min_hot_spin.setRange(0.0, 1.0e9)
        self.min_hot_spin.setValue(2000.0)
        v.addWidget(self._nested_params(self.chk_hot, [
            ("Noise floor:", self.noise_floor_spin),
            ("Min hot-pixel intensity:", self.min_hot_spin),
        ]))

        # ── Percentile mask ───────────────────────────────────────────
        self.chk_percentile = QtWidgets.QCheckBox("Percentile mask")
        self.chk_percentile.setChecked(False)
        v.addWidget(self.chk_percentile)

        self.percentile_spin = QtWidgets.QDoubleSpinBox()
        self.percentile_spin.setRange(0.0, 100.0)
        self.percentile_spin.setDecimals(3)
        self.percentile_spin.setValue(99.99)
        v.addWidget(self._nested_params(self.chk_percentile, [
            ("Percentile value:", self.percentile_spin),
        ]))

        layout.addWidget(box)

    def _build_advanced(self, layout):
        self.adv_box = QtWidgets.QGroupBox("Advanced")
        self.adv_box.setCheckable(True)
        self.adv_box.setChecked(False)
        form = QtWidgets.QFormLayout(self.adv_box)

        self.skip_frames_spin = QtWidgets.QSpinBox()
        self.skip_frames_spin.setRange(0, 10000)
        self.skip_frames_spin.setValue(1)
        form.addRow("Skip first N frames:", self.skip_frames_spin)

        self.min_intensity_spin = QtWidgets.QDoubleSpinBox()
        self.min_intensity_spin.setRange(0.0, 1.0e9)
        self.min_intensity_spin.setValue(1000.0)
        form.addRow("Min intensity to accept:", self.min_intensity_spin)

        # Per-mask parameters (frozen/hot-pixel/percentile/dark-mask
        # n-sigma & window) live nested under their own checkbox in the
        # "Masking steps" box, not here.

        self.att_table = QtWidgets.QTableWidget(0, 2)
        self.att_table.setHorizontalHeaderLabels(["Position", "Thickness (mm)"])
        self.att_table.horizontalHeader().setStretchLastSection(True)
        self.att_table.setMaximumHeight(180)
        for pos, thick in sorted(DEFAULT_POSITION_THICKNESS_MM.items()):
            self._add_att_row(pos, thick)
        form.addRow("Attenuator table:", self.att_table)

        row_btns = QtWidgets.QHBoxLayout()
        add_btn = QtWidgets.QPushButton("Add row")
        add_btn.clicked.connect(lambda: self._add_att_row(0, 0.0))
        rm_btn = QtWidgets.QPushButton("Remove selected")
        rm_btn.clicked.connect(self._remove_att_row)
        row_btns.addWidget(add_btn)
        row_btns.addWidget(rm_btn)
        form.addRow("", row_btns)

        layout.addWidget(self.adv_box)

    def _add_att_row(self, pos, thickness_mm):
        r = self.att_table.rowCount()
        self.att_table.insertRow(r)
        self.att_table.setItem(r, 0, QtWidgets.QTableWidgetItem(str(pos)))
        self.att_table.setItem(r, 1, QtWidgets.QTableWidgetItem(str(thickness_mm)))

    def _remove_att_row(self):
        rows = sorted({i.row() for i in self.att_table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.att_table.removeRow(r)

    def _thickness_table(self):
        table = {}
        for r in range(self.att_table.rowCount()):
            pos_item = self.att_table.item(r, 0)
            thick_item = self.att_table.item(r, 1)
            if pos_item is None or thick_item is None:
                continue
            try:
                table[int(float(pos_item.text()))] = float(thick_item.text())
            except ValueError:
                continue
        return table or dict(DEFAULT_POSITION_THICKNESS_MM)

    # ── prefill ──────────────────────────────────────────────────────

    def _prefill_from_snapshot(self):
        if "energy_keV" in self._snapshot:
            self.energy_spin.setValue(self._snapshot["energy_keV"])

    # ── refresh from Data Viewer ────────────────────────────────────────

    def _on_refresh_source(self):
        """Ask the main GUI (over refresh_server.AutoAttenuationRefreshServer)
        to overwrite our snapshot file with whatever the Data Viewer
        currently holds, then reload it. Only the captured data/dark/mask
        summary and availability are refreshed — entered parameters
        (energy, exposure, thresholds, ...) are left exactly as the user set
        them."""
        if not self._snapshot_path:
            QtWidgets.QMessageBox.information(
                self, "Auto Attenuation",
                "This window has no snapshot file to refresh (opened directly, "
                "not from the main GUI).")
            return

        sock = QtNetwork.QLocalSocket(self)
        sock.connectToServer(self._refresh_server_name)
        if not sock.waitForConnected(1500):
            QtWidgets.QMessageBox.warning(
                self, "Auto Attenuation",
                "Could not reach the main MIDAS GUI — it may have been "
                "closed. Source not refreshed.")
            return

        request = json.dumps({
            "type": "refresh_request", "version": 1, "path": self._snapshot_path,
        }).encode("utf-8")
        sock.write(request)
        sock.waitForBytesWritten(1500)
        if not sock.waitForReadyRead(5000):
            QtWidgets.QMessageBox.warning(
                self, "Auto Attenuation",
                "No response from the main GUI — source not refreshed.")
            return
        try:
            resp = json.loads(bytes(sock.readAll()).decode("utf-8"))
        except Exception:
            QtWidgets.QMessageBox.warning(
                self, "Auto Attenuation", "Bad response from the main GUI.")
            return
        finally:
            sock.disconnectFromServer()

        if not resp.get("ok"):
            QtWidgets.QMessageBox.warning(
                self, "Auto Attenuation",
                resp.get("message") or "Refresh failed.")
            return

        from midas_gui.auto_attenuation.snapshot import load_snapshot
        self._snapshot = load_snapshot(self._snapshot_path)
        self._source_summary_lbl.setText(self._source_summary_text())
        self._apply_source_availability()
        self._append_log("Refreshed data source from the Data Viewer.")

    # ── run ──────────────────────────────────────────────────────────

    def _append_log(self, text):
        self.log.appendPlainText(text)

    def _on_run(self):
        if self.saturation_spin.value() <= 0:
            QtWidgets.QMessageBox.warning(
                self, "Auto Attenuation",
                "Enter a Detector Saturation Intensity greater than 0 "
                "before running.",
            )
            return

        percentile = self.percentile_spin.value() if self.chk_percentile.isChecked() else 100.0

        params = {
            "energy_keV": self.energy_spin.value(),
            "att_pos": self.att_level_spin.value(),
            "acq_time_s": self.exposure_spin.value(),
            "saturation_intensity": self.saturation_spin.value(),
            "tolerate_n": self.tolerate_spin.value(),
            "target_intensity": self.target_spin.value(),
            "skip_frames": self.skip_frames_spin.value(),
            "min_intensity": self.min_intensity_spin.value(),
            "frozen_std_cutoff": self.frozen_std_spin.value(),
            "noise_floor": self.noise_floor_spin.value(),
            "min_hot_intensity": self.min_hot_spin.value(),
            "percentile_mask": percentile,
            "dark_mask_n_sigma": self.dark_nsigma_spin.value(),
            "dark_mask_local_window": self.dark_window_spin.value(),
            "apply_dark": self.chk_dark.isChecked(),
            "apply_dark_mask": self.chk_dark_mask.isChecked(),
            "apply_user_mask": self.chk_user_mask.isChecked(),
            "frozen_mask": self.chk_frozen.isChecked(),
            "hot_pixel_mask": self.chk_hot.isChecked(),
            "thickness_table": self._thickness_table(),
        }

        self.log.clear()
        self.run_btn.setEnabled(False)
        self._worker = _AnalysisWorker(self._snapshot, params, self)
        self._worker.logLine.connect(self._append_log)
        self._worker.finishedRun.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(lambda: self.run_btn.setEnabled(True))
        self._worker.start()

    def _on_finished(self, result):
        if result.get("saturated"):
            sat = result["saturation"]
            bad = ", ".join(str(i) for i in sat.bad_frame_indices)
            self._append_log(
                f"SATURATION DETECTED in frame(s) {bad} — analysis not run."
            )
            for i, count in sat.per_frame_bad_counts.items():
                self._append_log(f"  frame {i}: {count} oversaturated pixel(s)")
            QtWidgets.QMessageBox.warning(
                self, "Auto Attenuation — saturated",
                "This capture is saturated beyond the tolerated pixel "
                "count.\n\nCapture a buffer at higher attenuation, or "
                "reduce the exposure time, then try again.",
            )
            return

        if not result.get("ok"):
            for line in result.get("log", []):
                self._append_log(line)
            return

        for line in result["log"]:
            self._append_log(line)

    def _on_failed(self, message):
        self._append_log("ERROR:\n" + message)
        QtWidgets.QMessageBox.critical(self, "Auto Attenuation — error", message)

    def closeEvent(self, event):
        if self._snapshot_path:
            try:
                os.unlink(self._snapshot_path)
            except OSError:
                pass
        super().closeEvent(event)
