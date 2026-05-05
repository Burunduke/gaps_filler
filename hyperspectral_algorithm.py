# -*- coding: utf-8 -*-
"""Processing algorithm wrapper around :mod:`pipeline`.

Exposes the end-to-end hyperspectral pipeline (filter → mosaic → fill
gaps) as a single QGIS Processing algorithm so users can run it from
the toolbox or in batch mode.
"""

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
)

from . import pipeline


class HyperspectralPipelineAlgorithm(QgsProcessingAlgorithm):
    """Filter bad PIKA-L frames, mosaic the survivors, then fill gaps."""

    INPUT_LAYERS = "INPUT_LAYERS"
    MAX_DISTANCE = "MAX_DISTANCE"
    SMOOTHING_ITERATIONS = "SMOOTHING_ITERATIONS"
    OUTPUT = "OUTPUT"

    # ---- Algorithm metadata ------------------------------------------------

    def tr(self, text):
        return QCoreApplication.translate(
            "HyperspectralPipelineAlgorithm", text)

    def createInstance(self):
        return HyperspectralPipelineAlgorithm()

    def name(self):
        # Combined with provider id -> "gapsfiller:hyperspectral_pipeline".
        return "hyperspectral_pipeline"

    def displayName(self):
        return self.tr("Hyperspectral pipeline (filter, mosaic, fill)")

    def group(self):
        return self.tr("Raster analysis")

    def groupId(self):
        return "rasteranalysis"

    def shortHelpString(self):
        return self.tr(
            "End-to-end PIKA-L pipeline: rejects obviously-bad frames, "
            "mosaics the survivors with first-write-wins overlap, and "
            "fills NoData gaps in every band of the resulting mosaic."
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
            QgsProcessingParameterNumber(
                self.MAX_DISTANCE,
                self.tr("Maximum distance (in pixels) to search out for "
                        "values to interpolate"),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=100,
                minValue=1,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.SMOOTHING_ITERATIONS,
                self.tr("Number of smoothing iterations to run after "
                        "the interpolation"),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=0,
                minValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterDestination(
                self.OUTPUT, self.tr("Filled mosaic")
            )
        )

    # ---- Execution ---------------------------------------------------------

    def processAlgorithm(self, parameters, context, feedback):
        layers = self.parameterAsLayerList(
            parameters, self.INPUT_LAYERS, context)
        if not layers:
            raise QgsProcessingException(
                self.tr("No input frames provided"))

        max_distance = self.parameterAsInt(
            parameters, self.MAX_DISTANCE, context)
        smoothing = self.parameterAsInt(
            parameters, self.SMOOTHING_ITERATIONS, context)
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
            summary = pipeline.run_pipeline(
                paths,
                out_path,
                max_distance=int(max_distance),
                smoothing_iterations=int(smoothing),
                progress=cb,
            )
        except QgsProcessingException:
            raise
        except RuntimeError as exc:
            if str(exc) == "canceled":
                raise QgsProcessingException(self.tr("Canceled by user"))
            raise QgsProcessingException(str(exc))
        except Exception as exc:
            raise QgsProcessingException(str(exc))

        kept = summary["kept_count"]
        rejected = summary["rejected"]
        feedback.pushInfo(
            "Pipeline done: {} kept, {} rejected, {} band(s) written.".format(
                kept, len(rejected), summary["band_count"]))
        if rejected:
            feedback.pushInfo("Rejected frames:")
            for path, reason in rejected[:20]:
                feedback.pushInfo("  - {}: {}".format(path, reason))
            if len(rejected) > 20:
                feedback.pushInfo(
                    "  ... and {} more".format(len(rejected) - 20))

        return {self.OUTPUT: out_path}
