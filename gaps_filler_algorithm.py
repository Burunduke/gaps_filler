# -*- coding: utf-8 -*-
"""Processing algorithm wrapper around :mod:`fill_nodata`.

Subclassing :class:`QgsProcessingAlgorithm` lets QGIS auto-generate a dialog
that mirrors GDAL's "Fill nodata" tool, plus batch mode, history and
"Run as Python command" — without us writing any Qt UI code.
"""

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterRasterLayer,
)

from . import fill_nodata


class FillNoDataAlgorithm(QgsProcessingAlgorithm):
    """Fill NoData pixels in a single raster band."""

    INPUT = "INPUT"
    DISTANCE = "DISTANCE"
    ITERATIONS = "ITERATIONS"
    MASK_LAYER = "MASK_LAYER"
    OUTPUT = "OUTPUT"

    # ---- Algorithm metadata ------------------------------------------------

    def tr(self, text):
        return QCoreApplication.translate("FillNoDataAlgorithm", text)

    def createInstance(self):
        return FillNoDataAlgorithm()

    def name(self):
        # Internal id; combined with provider id -> "gapsfiller:fillnodata".
        return "fillnodata"

    def displayName(self):
        return self.tr("Fill nodata")

    def group(self):
        return self.tr("Raster analysis")

    def groupId(self):
        return "rasteranalysis"

    def shortHelpString(self):
        return self.tr(
            "Fills NoData gaps in every band of a raster using a pure-Python "
            "re-implementation of GDAL's FillNodata: inverse-distance "
            "weighting from the four nearest valid pixels (one per spatial "
            "quadrant), followed by an optional 3x3 smoothing pass.\n\n"
            "Inputs: any GDAL-readable raster, plus an optional validity "
            "mask (non-zero = valid). Output: a multi-band GeoTIFF with the "
            "same band count, geotransform, projection and per-band nodata "
            "as the input."
        )

    # ---- Parameters --------------------------------------------------------

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.INPUT, self.tr("Input raster")
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.DISTANCE,
                self.tr("Maximum distance (in pixels) to search out for "
                        "values to interpolate"),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=10,
                minValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.ITERATIONS,
                self.tr("Number of smoothing iterations to run after "
                        "the interpolation"),
                type=QgsProcessingParameterNumber.Integer,
                defaultValue=0,
                minValue=0,
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.MASK_LAYER,
                self.tr("Validity mask"),
                optional=True,
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterDestination(
                self.OUTPUT, self.tr("Filled")
            )
        )

    # ---- Execution ---------------------------------------------------------

    def processAlgorithm(self, parameters, context, feedback):
        src_layer = self.parameterAsRasterLayer(
            parameters, self.INPUT, context)
        if src_layer is None:
            raise QgsProcessingException(
                self.invalidRasterError(parameters, self.INPUT))

        distance = self.parameterAsInt(parameters, self.DISTANCE, context)
        iters = self.parameterAsInt(parameters, self.ITERATIONS, context)
        mask_lyr = self.parameterAsRasterLayer(
            parameters, self.MASK_LAYER, context)
        out_path = self.parameterAsOutputLayer(
            parameters, self.OUTPUT, context)

        in_path = src_layer.source()
        mask_path = mask_lyr.source() if mask_lyr is not None else None

        feedback.pushInfo(
            "Filling all bands of {}".format(in_path))

        try:
            fill_nodata.fill_nodata_file(
                input_path=in_path,
                output_path=out_path,
                mask_path=mask_path,
                max_search_dist=float(distance),
                smoothing_iterations=int(iters),
                feedback=feedback,
            )
        except RuntimeError as exc:
            # fill_nodata raises RuntimeError("canceled") when feedback
            # reports cancellation. Translate to a clean Processing error.
            if str(exc) == "canceled":
                raise QgsProcessingException(self.tr("Canceled by user"))
            raise QgsProcessingException(str(exc))
        except Exception as exc:
            raise QgsProcessingException(str(exc))

        return {self.OUTPUT: out_path}
