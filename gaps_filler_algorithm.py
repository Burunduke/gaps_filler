# -*- coding: utf-8 -*-
"""Processing algorithm wrapper around :mod:`fill_nodata`.

Subclassing :class:`QgsProcessingAlgorithm` lets QGIS auto-generate a dialog
that mirrors GDAL's "Fill nodata" tool, plus batch mode, history and
"Run as Python command" — without us writing any Qt UI code.
"""

import os

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterRasterLayer,
)

from . import fill_nodata, methods


class FillNoDataAlgorithm(QgsProcessingAlgorithm):
    """Fill NoData pixels in a single raster band."""

    INPUT = "INPUT"
    DISTANCE = "DISTANCE"
    ITERATIONS = "ITERATIONS"
    MASK_LAYER = "MASK_LAYER"
    OUTPUT = "OUTPUT"
    GAP_FILL_METHOD = "GAP_FILL_METHOD"
    FILL_ONLY_INTERIOR = "FILL_ONLY_INTERIOR"

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
        method_param = QgsProcessingParameterEnum(
            self.GAP_FILL_METHOD,
            self.tr("Gap fill method"),
            options=methods.labels(methods.GAP_FILL_METHODS),
            defaultValue=0,
        )
        method_param.setHelp(methods.tooltip_block(methods.GAP_FILL_METHODS))
        self.addParameter(method_param)
        interior_param = QgsProcessingParameterBoolean(
            self.FILL_ONLY_INTERIOR,
            self.tr("Fill only interior holes (footprint-aware)"),
            defaultValue=True,
        )
        interior_param.setHelp(self.tr(
            "When ON, only fills holes that are surrounded by valid data. "
            "Pixels outside the data footprint stay as NoData. Turn OFF "
            "to fill toward the bounding box (legacy behaviour).\n\n"
            "If you also supply a 'Validity mask' input, that mask takes "
            "precedence and this checkbox is ignored."
        ))
        self.addParameter(interior_param)
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
        fill_only_interior = self.parameterAsBoolean(
            parameters, self.FILL_ONLY_INTERIOR, context)

        in_path = src_layer.source()
        user_mask_path = mask_lyr.source() if mask_lyr is not None else None

        method_idx = self.parameterAsEnum(
            parameters, self.GAP_FILL_METHOD, context)
        method_entry = methods.GAP_FILL_METHODS[method_idx]
        feedback.pushInfo(
            "Gap fill method: {}".format(method_entry["id"]))
        feedback.pushInfo(
            "Filling all bands of {}".format(in_path))

        # Mask dispatch:
        #   * user-supplied mask wins (regardless of FILL_ONLY_INTERIOR);
        #   * else FILL_ONLY_INTERIOR=True   -> auto-build interior mask;
        #   * else FILL_ONLY_INTERIOR=False  -> mask_path=None (legacy).
        auto_mask_path = None  # only set when we generate one to clean up
        if user_mask_path is not None:
            mask_path = user_mask_path
            if fill_only_interior:
                feedback.pushInfo(
                    "User-supplied validity mask takes precedence over "
                    "'Fill only interior holes'.")
        elif fill_only_interior:
            auto_mask_path = out_path + ".fillmask.tif"
            feedback.pushInfo(
                "Building interior-hole mask at {}".format(auto_mask_path))
            fill_nodata.write_interior_fill_mask(in_path, auto_mask_path)
            mask_path = auto_mask_path
        else:
            mask_path = None

        try:
            method_entry["func"](
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
        finally:
            # Best-effort cleanup of the auto-generated mask (only the one
            # we created -- never the user-supplied one).
            if auto_mask_path is not None:
                try:
                    os.remove(auto_mask_path)
                except OSError:
                    pass

        return {self.OUTPUT: out_path}
