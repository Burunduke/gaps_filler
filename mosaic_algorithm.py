# -*- coding: utf-8 -*-
"""Processing algorithm wrapper around :mod:`mosaic`.

Exposes Stage B of the hyperspectral pipeline (mosaic of already-filtered
frames) as a standalone QGIS Processing algorithm. The actual blending
strategy for overlapping pixels is picked from the ``Mosaic method``
dropdown (registered in :data:`methods.MOSAIC_METHODS` — currently
only ``v1`` first-write-wins; visual-only feather variants were
removed to keep mosaic output spectrally exact).
The internal algorithm id (``name()`` -> ``"mosaic_frames"``) is kept
unchanged so saved Model Builder graphs keep resolving this algorithm.
"""

import os

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterDefinition,
    QgsProcessingParameterEnum,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
)

from . import canvas_styling, methods, mosaic


def _default_mosaic_output(input_paths):
    """Derive ``<first_input_dir>/mosaic.tif`` as a default OUTPUT.

    Pipeline TO-DO #14: when the user leaves OUTPUT empty, save the
    mosaic next to the first input frame instead of forcing them to
    type a path.
    """
    folder = os.path.dirname(os.path.abspath(input_paths[0]))
    return os.path.join(folder, "mosaic.tif")


def _is_empty_output(raw):
    """Return True when the user gave no real OUTPUT value."""
    if raw is None:
        return True
    if isinstance(raw, str) and (not raw or raw == "TEMPORARY_OUTPUT"):
        return True
    return False


class MosaicAlgorithm(QgsProcessingAlgorithm):
    """Mosaic frames into a single GeoTIFF; overlap rule is method-dependent."""

    INPUT_LAYERS = "INPUT_LAYERS"
    OUTPUT = "OUTPUT"
    MOSAIC_METHOD = "MOSAIC_METHOD"
    REPROJECT_TO_FIRST = "REPROJECT_TO_FIRST"

    # ---- Algorithm metadata ------------------------------------------------

    def tr(self, text):
        return QCoreApplication.translate("MosaicAlgorithm", text)

    def createInstance(self):
        return MosaicAlgorithm()

    def name(self):
        return "mosaic_frames"

    def displayName(self):
        return self.tr("Mosaic frames")

    def group(self):
        return self.tr("Raster analysis")

    def groupId(self):
        return "rasteranalysis"

    def shortHelpString(self):
        return self.tr(
            "Mosaics the supplied frames into a single GeoTIFF using "
            "the v1 first-write-wins rule (spectrally faithful — every "
            "output pixel comes from exactly one source frame). All "
            "inputs must share CRS and pixel size (or enable "
            "reprojection below). "
            "By default, two side outputs are also generated: "
            "<output>.overlap_count.tif (number of input frames covering each pixel) "
            "and <output>.valid_coverage.tif (binary mask of pixels covered by at least one frame)."
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
        method_param = QgsProcessingParameterEnum(
            self.MOSAIC_METHOD,
            self.tr("Mosaic method"),
            options=methods.labels(methods.MOSAIC_METHODS),
            defaultValue=0,
        )
        method_param.setHelp(methods.tooltip_block(methods.MOSAIC_METHODS))
        self.addParameter(method_param)
        self.addParameter(
            QgsProcessingParameterBoolean(
                self.REPROJECT_TO_FIRST,
                self.tr("Reproject mismatched frames to the first frame's "
                        "CRS / pixel size (instead of aborting)"),
                defaultValue=False,
            )
        )
        self.addParameter(
            QgsProcessingParameterBoolean(
                "EMIT_COVERAGE_OUTPUTS",
                self.tr("Emit coverage outputs (.overlap_count.tif and .valid_coverage.tif)"),
                defaultValue=True,
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

        paths = [lyr.source() for lyr in layers]

        # Pipeline TO-DO #14: derive a default output path from the
        # first input frame's folder when OUTPUT is empty.
        if _is_empty_output(parameters.get(self.OUTPUT)):
            default = _default_mosaic_output(paths)
            parameters[self.OUTPUT] = default
            feedback.pushInfo(
                "Output path empty; defaulting to {}".format(default))
        out_path = self.parameterAsOutputLayer(
            parameters, self.OUTPUT, context)

        # Pipeline TO-DO #15: queue an RGB-composite post-processor so
        # the auto-loaded mosaic shows a colour view (PIKA-L cubes
        # default to a near-black band-1 in grayscale otherwise). The
        # mosaic preserves the input band count, so we read it from
        # the first input layer.
        if context.willLoadLayerOnCompletion(out_path):
            pending = context.layersToLoadOnCompletion()
            details = pending.get(out_path)
            if details is not None:
                self._rgb_post_processor = (
                    canvas_styling.attach_rgb_post_processor(
                        details, layers[0].bandCount()))

        def cb(fraction, message):
            if feedback.isCanceled():
                raise QgsProcessingException(self.tr("Canceled by user"))
            feedback.setProgress(int(max(0.0, min(1.0, fraction)) * 100))
            if message:
                feedback.pushInfo(message)

        method_idx = self.parameterAsEnum(
            parameters, self.MOSAIC_METHOD, context)
        method_entry = methods.MOSAIC_METHODS[method_idx]
        feedback.pushInfo(
            "Mosaic method: {}".format(method_entry["id"]))

        reproject_to_first = self.parameterAsBoolean(
            parameters, self.REPROJECT_TO_FIRST, context)
        if reproject_to_first:
            feedback.pushInfo(
                "Reprojection of mismatched frames is enabled.")

        # Read coverage outputs parameter
        emit_coverage_outputs = self.parameterAsBoolean(
            parameters, "EMIT_COVERAGE_OUTPUTS", context)
        if not emit_coverage_outputs:
            feedback.pushInfo(
                "Coverage outputs (.overlap_count.tif and .valid_coverage.tif) disabled.")

        try:
            method_entry["func"](
                paths, out_path,
                progress=cb,
                reproject_to_first=reproject_to_first,
                emit_coverage_outputs=emit_coverage_outputs,
            )
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
