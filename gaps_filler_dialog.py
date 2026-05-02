# -*- coding: utf-8 -*-
"""GapsFiller dialog.

UI is built in Python (not loaded from the .ui file) — this keeps the
parameter wiring in one place and avoids needing Qt Designer for what
is a simple form. Layout mirrors QGIS's built-in "Fill nodata" tool
(which wraps ``gdal_fillnodata.py`` / ``gdal.FillNodata``).
"""

from qgis.PyQt import QtWidgets
from qgis.core import QgsMapLayerProxyModel
from qgis.gui import QgsMapLayerComboBox, QgsRasterBandComboBox, QgsFileWidget


class GapsFillerDialog(QtWidgets.QDialog):
    """Form-style dialog with the same params as GDAL Fill nodata."""

    def __init__(self, parent=None):
        super(GapsFillerDialog, self).__init__(parent)
        self.setWindowTitle("Fill nodata (gaps filler)")
        self.resize(480, 320)

        form = QtWidgets.QFormLayout()

        # Input raster layer (filtered to raster layers).
        self.input_combo = QgsMapLayerComboBox(self)
        self.input_combo.setFilters(QgsMapLayerProxyModel.RasterLayer)
        form.addRow("Input layer", self.input_combo)

        # Band number — follows the input layer.
        self.band_combo = QgsRasterBandComboBox(self)
        form.addRow("Band number", self.band_combo)

        # Max search distance in pixels.
        self.dist_spin = QtWidgets.QSpinBox(self)
        self.dist_spin.setRange(0, 1000000)
        self.dist_spin.setValue(10)
        form.addRow("Maximum distance (pixels)", self.dist_spin)

        # Smoothing iterations.
        self.smooth_spin = QtWidgets.QSpinBox(self)
        self.smooth_spin.setRange(0, 1000)
        self.smooth_spin.setValue(0)
        form.addRow("Smoothing iterations", self.smooth_spin)

        # Optional validity mask raster (allow "no selection").
        self.mask_combo = QgsMapLayerComboBox(self)
        self.mask_combo.setFilters(QgsMapLayerProxyModel.RasterLayer)
        self.mask_combo.setAllowEmptyLayer(True)
        self.mask_combo.setCurrentIndex(0)  # default: empty
        form.addRow("Validity mask (optional)", self.mask_combo)

        # Output raster path (save mode).
        self.output_widget = QgsFileWidget(self)
        self.output_widget.setStorageMode(QgsFileWidget.SaveFile)
        self.output_widget.setFilter("GeoTIFF (*.tif *.tiff)")
        form.addRow("Output raster", self.output_widget)

        # OK / Cancel.
        self.button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
            parent=self,
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

        outer = QtWidgets.QVBoxLayout(self)
        outer.addLayout(form)
        outer.addWidget(self.button_box)

        # Keep band combo in sync with the chosen input layer.
        self.input_combo.layerChanged.connect(self.band_combo.setLayer)
        self.band_combo.setLayer(self.input_combo.currentLayer())
