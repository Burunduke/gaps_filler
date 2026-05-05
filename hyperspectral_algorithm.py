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
from .frame_filter import (
    AREA_HI,
    AREA_LO,
    ASPECT_MAX,
    CENTRE_WINDOW,
    FilterThresholds,
    MIN_VALID_FRACTION,
    SATURATION_FRACTION,
    SKEW_MAX,
    STD_MIN,
)


class HyperspectralPipelineAlgorithm(QgsProcessingAlgorithm):
    """Filter bad PIKA-L frames, mosaic the survivors, then fill gaps."""

    INPUT_LAYERS = "INPUT_LAYERS"
    MAX_DISTANCE = "MAX_DISTANCE"
    SMOOTHING_ITERATIONS = "SMOOTHING_ITERATIONS"
    OUTPUT = "OUTPUT"

    SKEW_MAX = "SKEW_MAX"
    AREA_LO = "AREA_LO"
    AREA_HI = "AREA_HI"
    ASPECT_MAX = "ASPECT_MAX"
    CENTRE_WINDOW = "CENTRE_WINDOW"
    MIN_VALID_FRACTION = "MIN_VALID_FRACTION"
    STD_MIN = "STD_MIN"
    SATURATION_FRACTION = "SATURATION_FRACTION"

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
        Double = QgsProcessingParameterNumber.Double
        Integer = QgsProcessingParameterNumber.Integer

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
                type=Integer,
                defaultValue=100,
                minValue=1,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.SMOOTHING_ITERATIONS,
                self.tr("Number of smoothing iterations to run after "
                        "the interpolation"),
                type=Integer,
                defaultValue=0,
                minValue=0,
            )
        )

        self.addParameter(QgsProcessingParameterNumber(
            self.SKEW_MAX, self.tr("Max skew (rotation tolerance)"),
            type=Double, defaultValue=SKEW_MAX, minValue=0.0, maxValue=1.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.AREA_LO,
            self.tr("Min area ratio vs. flight median"),
            type=Double, defaultValue=AREA_LO, minValue=0.0, maxValue=1.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.AREA_HI,
            self.tr("Max area ratio vs. flight median"),
            type=Double, defaultValue=AREA_HI, minValue=1.0, maxValue=10.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.ASPECT_MAX,
            self.tr("Max aspect ratio (long / short side)"),
            type=Double, defaultValue=ASPECT_MAX,
            minValue=1.0, maxValue=10.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.CENTRE_WINDOW,
            self.tr("Centre window size (pixels)"),
            type=Integer, defaultValue=CENTRE_WINDOW,
            minValue=4, maxValue=4096))
        self.addParameter(QgsProcessingParameterNumber(
            self.MIN_VALID_FRACTION,
            self.tr("Min fraction of valid pixels in centre"),
            type=Double, defaultValue=MIN_VALID_FRACTION,
            minValue=0.0, maxValue=1.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.STD_MIN,
            self.tr("Min std-dev of centre (low-variance reject)"),
            type=Double, defaultValue=STD_MIN,
            minValue=0.0, maxValue=1e6))
        self.addParameter(QgsProcessingParameterNumber(
            self.SATURATION_FRACTION,
            self.tr("Max saturated-pixel fraction in centre"),
            type=Double, defaultValue=SATURATION_FRACTION,
            minValue=0.0, maxValue=1.0))

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

        thresholds = FilterThresholds(
            skew_max=self.parameterAsDouble(
                parameters, self.SKEW_MAX, context),
            area_lo=self.parameterAsDouble(
                parameters, self.AREA_LO, context),
            area_hi=self.parameterAsDouble(
                parameters, self.AREA_HI, context),
            aspect_max=self.parameterAsDouble(
                parameters, self.ASPECT_MAX, context),
            centre_window=self.parameterAsInt(
                parameters, self.CENTRE_WINDOW, context),
            min_valid_fraction=self.parameterAsDouble(
                parameters, self.MIN_VALID_FRACTION, context),
            std_min=self.parameterAsDouble(
                parameters, self.STD_MIN, context),
            saturation_fraction=self.parameterAsDouble(
                parameters, self.SATURATION_FRACTION, context),
        )

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
                thresholds=thresholds,
                max_distance=int(max_distance),
                smoothing_iterations=int(smoothing),
                progress=cb,
                log=feedback.pushInfo,
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

        return {self.OUTPUT: out_path}
