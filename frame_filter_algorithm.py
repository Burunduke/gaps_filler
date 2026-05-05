# -*- coding: utf-8 -*-
"""Processing algorithm wrapper around :mod:`frame_filter`.

Exposes Stage A of the hyperspectral pipeline (rejecting obviously-bad
PIKA-L frames) as a standalone QGIS Processing algorithm.
"""

import os
import shutil

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterNumber,
)

from . import frame_filter
from . import methods
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


_LOG_CAP = 1000


class FrameFilterAlgorithm(QgsProcessingAlgorithm):
    """Reject obviously-bad PIKA-L frames; copy the survivors to a folder."""

    INPUT_LAYERS = "INPUT_LAYERS"
    OUTPUT_FOLDER = "OUTPUT_FOLDER"
    REPORT = "REPORT"
    FRAME_FILTER_METHOD = "FRAME_FILTER_METHOD"

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
        return QCoreApplication.translate("FrameFilterAlgorithm", text)

    def createInstance(self):
        return FrameFilterAlgorithm()

    def name(self):
        return "frame_filter"

    def displayName(self):
        return self.tr("Filter bad frames")

    def group(self):
        return self.tr("Raster analysis")

    def groupId(self):
        return "rasteranalysis"

    def shortHelpString(self):
        return self.tr(
            "Runs the PIKA-L bad-frame heuristic over the supplied rasters "
            "and copies the survivors into the chosen output folder; the "
            "rejection report lists why each frame was dropped."
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
        method_param = QgsProcessingParameterEnum(
            self.FRAME_FILTER_METHOD,
            self.tr("Filter method"),
            options=methods.labels(methods.FRAME_FILTER_METHODS),
            defaultValue=0,
        )
        method_param.setHelp(methods.tooltip_block(
            methods.FRAME_FILTER_METHODS))
        self.addParameter(method_param)
        self.addParameter(
            QgsProcessingParameterFolderDestination(
                self.OUTPUT_FOLDER,
                self.tr("Output folder for kept frames"),
            )
        )
        self.addParameter(
            QgsProcessingParameterFileDestination(
                self.REPORT,
                self.tr("Rejection report"),
                fileFilter="Text files (*.txt)",
                defaultValue=None,
                optional=True,
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

    # ---- Execution ---------------------------------------------------------

    def processAlgorithm(self, parameters, context, feedback):
        layers = self.parameterAsLayerList(
            parameters, self.INPUT_LAYERS, context)
        if not layers:
            raise QgsProcessingException(
                self.tr("No input frames provided"))

        folder = self.parameterAsString(
            parameters, self.OUTPUT_FOLDER, context)
        report_path = self.parameterAsFileOutput(
            parameters, self.REPORT, context)

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

        method_idx = self.parameterAsEnum(
            parameters, self.FRAME_FILTER_METHOD, context)
        method_entry = methods.FRAME_FILTER_METHODS[method_idx]
        feedback.pushInfo(
            "Frame filter method: {}".format(method_entry["id"]))

        try:
            good, rejected = method_entry["func"](
                paths, thresholds=thresholds)
        except Exception as exc:
            raise QgsProcessingException(str(exc))

        # Log every rejected frame so the user sees WHY each was dropped.
        for i, (p, reason) in enumerate(rejected):
            if i >= _LOG_CAP:
                feedback.pushInfo(
                    "... and {} more rejected (see report file)".format(
                        len(rejected) - _LOG_CAP))
                break
            feedback.pushInfo(
                "REJECTED  {}: {}".format(os.path.basename(p), reason))

        feedback.pushInfo(
            "Kept {} / {} frames".format(len(good), len(paths)))

        os.makedirs(folder, exist_ok=True)

        total_kept = len(good)
        for i, src in enumerate(good):
            if feedback.isCanceled():
                raise QgsProcessingException(self.tr("Canceled by user"))
            dst = os.path.join(folder, os.path.basename(src))
            try:
                if (os.path.exists(dst)
                        and os.path.getsize(dst) == os.path.getsize(src)):
                    pass
                else:
                    shutil.copy2(src, dst)
            except OSError as exc:
                raise QgsProcessingException(str(exc))
            if total_kept:
                feedback.setProgress(int((i + 1) / total_kept * 100))

        total = len(good) + len(rejected)
        header = "kept {} / rejected {} / total {}".format(
            len(good), len(rejected), total)
        feedback.pushInfo(header)

        if report_path:
            try:
                with open(report_path, "w", encoding="utf-8") as fh:
                    fh.write(header + "\n\n")
                    fh.write("REJECTED:\n")
                    for path, reason in rejected:
                        fh.write("{}\t{}\n".format(path, reason))
                    fh.write("\nKEPT:\n")
                    for path in good:
                        fh.write("{}\n".format(path))
            except OSError as exc:
                raise QgsProcessingException(str(exc))

        return {self.OUTPUT_FOLDER: folder, self.REPORT: report_path}
