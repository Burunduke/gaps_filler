# -*- coding: utf-8 -*-
"""Processing algorithm wrapper around :mod:`mosaic`.

Exposes Stage B of the hyperspectral pipeline (first-write-wins mosaic of
already-filtered frames) as a standalone QGIS Processing algorithm.
"""

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterRasterDestination,
)

from . import mosaic


class MosaicAlgorithm(QgsProcessingAlgorithm):
    """Mosaic frames with first-write-wins overlap into a single GeoTIFF."""

    INPUT_LAYERS = "INPUT_LAYERS"
    OUTPUT = "OUTPUT"

    # ---- Algorithm metadata ------------------------------------------------

    def tr(self, text):
        return QCoreApplication.translate("MosaicAlgorithm", text)

    def createInstance(self):
        return MosaicAlgorithm()

    def name(self):
        return "mosaic_frames"

    def displayName(self):
        return self.tr("Mosaic frames (first-write-wins)")

    def group(self):
        return self.tr("Raster analysis")

    def groupId(self):
        return "rasteranalysis"

    def shortHelpString(self):
        return self.tr(
            "Mosaics the supplied frames into a single GeoTIFF using "
            "first-write-wins for overlapping pixels. All inputs must "
            "share CRS and pixel size."
        )

    # ---- Parameters --------------------------------------------------------

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterMultipleLayers(
                self.INPUT_LAYERS,
                self.tr("Input frames"),
                layerType=QgsProcessing.TypeRaster,
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterDestination(
                self.OUTPUT, self.tr("Mosaic output")
            )
        )

    # ---- Execution ---------------------------------------------------------

    def processAlgorithm(self, parameters, context, feedback):
        layers = self.parameterAsLayerList(
            parameters, self.INPUT_LAYERS, context)
        if not layers:
            raise QgsProcessingException(
                self.tr("No input frames provided"))

        out_path = self.parameterAsOutputLayer(
            parameters, self.OUTPUT, context)
        paths = [lyr.source() for lyr in layers]

        def cb(fraction, message):
            if feedback.isCanceled():
                raise QgsProcessingException(self.tr("Canceled by user"))
            feedback.setProgress(int(max(0.0, min(1.0, fraction)) * 100))
            if message:
                feedback.pushInfo(message)

        try:
            mosaic.mosaic_frames(paths, out_path, progress=cb)
        except QgsProcessingException:
            raise
        except mosaic.MosaicInputError as exc:
            raise QgsProcessingException(str(exc))
        except RuntimeError as exc:
            if str(exc) == "canceled":
                raise QgsProcessingException(self.tr("Canceled by user"))
            raise QgsProcessingException(str(exc))
        except Exception as exc:
            raise QgsProcessingException(str(exc))

        feedback.pushInfo("Mosaic written: {}".format(out_path))
        return {self.OUTPUT: out_path}
