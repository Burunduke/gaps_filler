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

from . import canvas_styling, fill_nodata, methods


def _default_output_path(input_path, suffix):
    """Derive ``<input_dir>/<input_stem><suffix>`` as a default OUTPUT.

    Pipeline TO-DO #14: when the user leaves the OUTPUT field empty
    (or QGIS pre-fills it with ``TEMPORARY_OUTPUT``), drop the result
    next to the input instead of forcing them to type a path.
    """
    folder = os.path.dirname(os.path.abspath(input_path))
    stem, _ext = os.path.splitext(os.path.basename(input_path))
    return os.path.join(folder, stem + suffix)


def _is_empty_output(raw):
    """Return True when the user gave no real OUTPUT value."""
    if raw is None:
        return True
    if isinstance(raw, str) and (not raw or raw == "TEMPORARY_OUTPUT"):
        return True
    return False


class FillNoDataAlgorithm(QgsProcessingAlgorithm):
    """Fill NoData pixels in a single raster band."""

    INPUT = "INPUT"
    DISTANCE = "DISTANCE"
    ITERATIONS = "ITERATIONS"
    MASK_LAYER = "MASK_LAYER"
    OUTPUT = "OUTPUT"
    GAP_FILL_METHOD = "GAP_FILL_METHOD"
    FILL_ONLY_INTERIOR = "FILL_ONLY_INTERIOR"
    MAX_INTERIOR_GAP_PX = "MAX_INTERIOR_GAP_PX"
    TILE_SIZE = "TILE_SIZE"
    N_WORKERS = "N_WORKERS"

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
        gap_param = QgsProcessingParameterNumber(
            self.MAX_INTERIOR_GAP_PX,
            self.tr("Max interior gap width to bridge (pixels, 0 = strict)"),
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=50,
            minValue=0,
        )
        gap_param.setHelp(self.tr(
            "Bridge narrow gaps in the validity footprint up to ~2N pixels "
            "wide, so slits and edge-touching holes are filled. 0 = strict "
            "(only topologically enclosed holes are filled). Ignored when "
            "'Fill only interior holes' is OFF or a user mask is supplied."
        ))
        self.addParameter(gap_param)
        tile_param = QgsProcessingParameterNumber(
            self.TILE_SIZE,
            self.tr("Tile size in pixels (0 = whole-band, no tiling)"),
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=0,
            minValue=0,
        )
        tile_param.setHelp(self.tr(
            "When > 0, each band is processed in square tiles of this "
            "size with a halo of (max distance + smoothing iterations) "
            "pixels around each tile, instead of loading whole bands "
            "into RAM. Use this for big hyperspectral cubes where a "
            "single band is hundreds of MB. 0 keeps the legacy "
            "whole-band behaviour. The v3 backend (gdal.FillNodata) "
            "ignores this -- it streams in C already."
        ))
        self.addParameter(tile_param)
        workers_param = QgsProcessingParameterNumber(
            self.N_WORKERS,
            self.tr("Worker processes for per-band fill (1 = sequential)"),
            type=QgsProcessingParameterNumber.Integer,
            defaultValue=1,
            minValue=1,
        )
        workers_param.setHelp(self.tr(
            "When > 1, the per-band fill loop is dispatched to a "
            "ThreadPoolExecutor with one thread per band (bands are "
            "independent). 1 (the default) keeps the legacy in-process "
            "loop. Ignored when 'Tile size' > 0 (tiled mode runs "
            "sequentially) and by the v3 backend (gdal.FillNodata is "
            "already a C routine)."
        ))
        self.addParameter(workers_param)
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
        # Pipeline TO-DO #14: derive a default output path from the
        # input file when the user leaves OUTPUT empty.
        if _is_empty_output(parameters.get(self.OUTPUT)):
            default = _default_output_path(
                src_layer.source(), "_filled.tif")
            parameters[self.OUTPUT] = default
            feedback.pushInfo(
                "Output path empty; defaulting to {}".format(default))
        out_path = self.parameterAsOutputLayer(
            parameters, self.OUTPUT, context)

        # Pipeline TO-DO #15: when QGIS will auto-load the result onto
        # the canvas, swap the default grayscale-band-1 renderer for an
        # RGB composite picked from the input cube's band count.
        if context.willLoadLayerOnCompletion(out_path):
            pending = context.layersToLoadOnCompletion()
            details = pending.get(out_path)
            if details is not None:
                self._rgb_post_processor = (
                    canvas_styling.attach_rgb_post_processor(
                        details, src_layer.bandCount()))
        fill_only_interior = self.parameterAsBoolean(
            parameters, self.FILL_ONLY_INTERIOR, context)
        max_interior_gap_px = self.parameterAsInt(
            parameters, self.MAX_INTERIOR_GAP_PX, context)
        tile_size = self.parameterAsInt(
            parameters, self.TILE_SIZE, context)
        n_workers = self.parameterAsInt(
            parameters, self.N_WORKERS, context)

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
            fill_nodata.write_interior_fill_mask(
                in_path, auto_mask_path,
                max_gap_px=int(max_interior_gap_px),
            )
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
                tile_size=int(tile_size),
                n_workers=int(n_workers),
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
