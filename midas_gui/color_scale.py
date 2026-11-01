"""Shared absolute-value editor and bounded navigation for image LUTs."""
from __future__ import annotations

import math
import numpy as np
from PyQt5 import QtWidgets


class ColorScaleControls(QtWidgets.QWidget):
    """Wrap a vertical HistogramLUTWidget with Max/Min in raw data units.

    Linear limits are canonical. Only the image and LUT coordinates are
    transformed, so toggling Log cannot lose a zero/negative entered limit.
    """
    LOG_FLOOR = 1e-10

    def __init__(self, histogram, parent=None):
        super().__init__(parent)
        self.histogram = histogram
        self.manual_limits = None
        self.limits = (0., 1.)
        self.percentiles = None  # compatibility for older saved display styles
        self.hist_range = None
        self._log = False
        self._updating = False
        self._auto_limits = (0., 1.)
        self._data_limits = (0., 1.)
        self._display_bounds = None
        self._has_data = False
        self._restored_pending = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.maximum = self._field("Max", layout)
        layout.addWidget(histogram, 1)
        self.minimum = self._field("Min", layout)
        self.auto_button = QtWidgets.QPushButton("Auto", self)
        self.auto_button.setToolTip("Use the current image minimum and maximum; follow new frames.")
        self.auto_button.clicked.connect(self.reset)
        layout.addWidget(self.auto_button)
        self.setMaximumWidth(170)
        self.setMinimumWidth(120)
        histogram.sigLevelsChanged.connect(self._levels_changed)
        histogram.sigLevelChangeFinished.connect(self._levels_finished)
        histogram.item.vb.sigRangeChanged.connect(self._range_changed)
        self._sync_fields()

    def _field(self, label, layout):
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QtWidgets.QLabel(label))
        edit = QtWidgets.QLineEdit()
        edit.setMinimumWidth(60)
        edit.setToolTip("Absolute value in linear data units, including in Log mode. "
                        "Scientific notation is accepted. Min must be less than Max.")
        edit.setAccessibleName(f"Color scale {label.lower()}")
        edit.editingFinished.connect(self._edited)
        row.addWidget(edit, 1)
        layout.addLayout(row)
        return edit

    @classmethod
    def install(cls, image_view):
        histogram = image_view.getHistogramWidget()
        layout = image_view.ui.gridLayout
        index = layout.indexOf(histogram)
        row, col, rows, cols = layout.getItemPosition(index)
        layout.removeWidget(histogram)
        controls = cls(histogram, image_view)
        layout.addWidget(controls, row, col, rows, cols)
        return controls

    @staticmethod
    def _valid(limits):
        return (len(limits) == 2 and all(math.isfinite(x) for x in limits)
                and limits[0] < limits[1] and math.isfinite(limits[1] - limits[0]))

    def _sync_fields(self):
        for edit, value in ((self.minimum, self.limits[0]),
                            (self.maximum, self.limits[1])):
            if not edit.hasFocus():
                edit.setText(format(value, '.12g'))
                edit.setStyleSheet("")

    def _edited(self):
        try:
            limits = (float(self.minimum.text()), float(self.maximum.text()))
            if not self._valid(limits):
                raise ValueError
        except (ValueError, OverflowError):
            for edit in (self.minimum, self.maximum):
                edit.setStyleSheet("border:1px solid #d06040")
            return
        self.manual_limits = limits
        self.limits = limits
        self.hist_range = None
        self._apply()

    def reset(self, preserve_restored=False):
        if preserve_restored and self._restored_pending:
            return
        self._restored_pending = False
        self.manual_limits = None
        self.percentiles = None
        self.hist_range = None
        self.limits = self._auto_limits = self._data_limits
        self._apply()

    def set_percentiles(self, lo, hi):
        self.manual_limits = None
        self.percentiles = None if (lo, hi) == (0, 100) else (lo, hi)
        self.hist_range = None

    def render(self, data, *, log=False, image_view=None, image_item=None):
        """Display raw data, keeping user limits and histogram zoom pinned."""
        data = np.asarray(data)
        finite = data[np.isfinite(data)]
        if finite.size:
            self._data_limits = (float(finite.min()), float(finite.max()))
            if self.percentiles is None:
                self._auto_limits = self._data_limits
            else:
                self._auto_limits = tuple(float(x) for x in np.percentile(finite, self.percentiles))
        else:
            self._auto_limits = self._data_limits = (0., 1.)
        self.limits = self.manual_limits if self.manual_limits is not None else self._auto_limits
        if log != self._log:
            self.hist_range = None
        self._log = log
        disp = np.log10(np.clip(data, self.LOG_FLOOR, None)) if log else data
        # Infinite detector sentinels are not histogram endpoints; keeping
        # them out also avoids pyqtgraph attempting bins spanning infinity.
        disp = np.where(np.isfinite(disp), disp, np.nan)
        self._updating = True
        try:
            # Drop old drag bounds before installing data with a new range.
            self.histogram.item.region.setBounds((None, None))
            levels = self._mapped_limits()
            if image_view is not None:
                image_view.setImage(disp, autoLevels=False, levels=levels,
                                    autoRange=False, autoHistogramRange=False)
            else:
                image_item.setImage(disp, autoLevels=False, levels=levels)
        finally:
            self._updating = False
        self._apply()
        self._has_data = True
        self._restored_pending = False

    def _mapped_limits(self):
        lo, hi = self.limits
        if self._log:
            lo, hi = (math.log10(max(x, self.LOG_FLOOR)) for x in (lo, hi))
        # Constant/all-zero images still have exact data extrema in the
        # fields, but Qt needs a nonzero interval to draw/navigate the LUT.
        if hi <= lo:
            hi = lo + max(abs(lo) * 1e-6, 1e-6)
        return lo, hi

    def _apply(self):
        self._updating = True
        try:
            lo, hi = self._mapped_limits()
            span = hi - lo
            vb = self.histogram.item.vb
            self.histogram.item.region.setBounds((None, None))
            vb.setLimits(yMin=lo, yMax=hi, minYRange=span * 1e-6, maxYRange=span)
            self.histogram.setLevels(lo, hi)
            self.histogram.item.region.setBounds((lo, hi))
            if self.hist_range is None or self._display_bounds != (lo, hi):
                self.histogram.item.setHistogramRange(lo, hi, padding=0)
            else:
                self.histogram.item.setHistogramRange(*self.hist_range, padding=0)
            self._display_bounds = (lo, hi)
            self._sync_fields()
        finally:
            self._updating = False

    def _levels_changed(self, *_):
        if self._updating:
            return
        mapped = tuple(self.histogram.getLevels())
        if self._log:
            # Keep the original endpoints when a handle has not moved. In
            # particular log10(0 clipped) must not rewrite the field as 1e-10.
            limits = tuple(original if value == bound else 10. ** min(value, 300.)
                           for value, bound, original in
                           zip(mapped, self._mapped_limits(), self.limits))
        else:
            limits = mapped
        if self._valid(limits):
            self.manual_limits = self.limits = limits
            self._sync_fields()

    def _levels_finished(self, *_):
        # Reframe only after release. Changing the histogram coordinates
        # under a moving handle makes dragging jump around unpredictably.
        if not self._updating:
            self.hist_range = None
            self._apply()

    def _range_changed(self, *_):
        if not self._updating:
            self.hist_range = tuple(self.histogram.item.getHistogramRange())

    def display_state(self):
        return {"color_limits": self.manual_limits}

    def restore_state(self, state):
        if "color_limits" not in state:
            return
        limits = state["color_limits"]
        if limits is None:
            self.manual_limits = None
        else:
            try:
                limits = tuple(float(x) for x in limits)
                if self._valid(limits):
                    self.manual_limits = limits
            except (TypeError, ValueError, OverflowError):
                pass
        self.hist_range = None
        self._restored_pending = self.manual_limits is not None and not self._has_data
