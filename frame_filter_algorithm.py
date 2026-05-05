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
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterMultipleLayers,
)

from . import frame_filter


class FrameFilterAlgorithm(QgsProcessingAlgorithm):
    """Reject obviously-bad PIKA-L frames; copy the survivors to a folder."""

    INPUT_LAYERS = "INPUT_LAYERS"
    OUTPUT_FOLDER = "OUTPUT_FOLDER"
    REPORT = "REPORT"

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
        self.addParameter(
            QgsProcessingParameterMultipleLayers(
                self.INPUT_LAYERS,
                self.tr("Input frames"),
                layerType=QgsProcessing.TypeRaster,
            )
        )
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

        paths = [lyr.source() for lyr in layers]

        try:
            good, rejected = frame_filter.filter_frames(paths)
        except Exception as exc:
            raise QgsProcessingException(str(exc))

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
