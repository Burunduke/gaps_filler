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
    QgsProcessingParameterBoolean,
    QgsProcessingParameterEnum,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterNumber,
    QgsProcessingParameterRasterDestination,
)

from . import methods, mosaic, pipeline
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
    REPROJECT_TO_FIRST = "REPROJECT_TO_FIRST"
    FILL_ONLY_INTERIOR = "FILL_ONLY_INTERIOR"
    MAX_INTERIOR_GAP_PX = "MAX_INTERIOR_GAP_PX"

    FRAME_FILTER_METHOD = "FRAME_FILTER_METHOD"
    MOSAIC_METHOD = "MOSAIC_METHOD"
    GAP_FILL_METHOD = "GAP_FILL_METHOD"

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

        ff_param = QgsProcessingParameterEnum(
            self.FRAME_FILTER_METHOD,
            self.tr("Frame filter method"),
            options=methods.labels(methods.FRAME_FILTER_METHODS),
            defaultValue=0,
        )
        ff_param.setHelp(methods.tooltip_block(methods.FRAME_FILTER_METHODS))
        self.addParameter(ff_param)

        mos_param = QgsProcessingParameterEnum(
            self.MOSAIC_METHOD,
            self.tr("Mosaic method"),
            options=methods.labels(methods.MOSAIC_METHODS),
            defaultValue=0,
        )
        mos_param.setHelp(methods.tooltip_block(methods.MOSAIC_METHODS))
        self.addParameter(mos_param)

        gf_param = QgsProcessingParameterEnum(
            self.GAP_FILL_METHOD,
            self.tr("Gap fill method"),
            options=methods.labels(methods.GAP_FILL_METHODS),
            defaultValue=0,
        )
        gf_param.setHelp(methods.tooltip_block(methods.GAP_FILL_METHODS))
        self.addParameter(gf_param)

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
            QgsProcessingParameterBoolean(
                self.REPROJECT_TO_FIRST,
                self.tr("Reproject mismatched frames to the first frame's "
                        "CRS / pixel size (instead of aborting on "
                        "mismatch)"),
                defaultValue=False,
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.FILL_ONLY_INTERIOR,
                self.tr("Fill only interior holes (leave outside-footprint "
                        "pixels as NoData)"),
                defaultValue=True,
            )
        )

        gap_param = QgsProcessingParameterNumber(
            self.MAX_INTERIOR_GAP_PX,
            self.tr("Max interior gap width to bridge (pixels, 0 = strict)"),
            type=Integer,
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

        reproject_to_first = self.parameterAsBoolean(
            parameters, self.REPROJECT_TO_FIRST, context)
        fill_only_interior = self.parameterAsBoolean(
            parameters, self.FILL_ONLY_INTERIOR, context)
        max_interior_gap_px = self.parameterAsInt(
            parameters, self.MAX_INTERIOR_GAP_PX, context)

        # Validate inputs up-front so the user gets a clear error in
        # ~1 second on a CRS / pixel-size / band-count / dtype mismatch
        # instead of waiting for Stage A to finish on a flight that
        # cannot be mosaicked anyway. See Pipeline TO-DO #2 in
        # ``hyperspectral_plan.md``. When ``reproject_to_first`` is
        # set, CRS / pixel-size mismatches are tolerated here -- the
        # mosaic stage will reproject them onto the first frame's grid
        # (Pipeline TO-DO #5).
        feedback.pushInfo(
            "Validating {} input frame(s)...".format(len(paths)))
        try:
            mosaic.validate_inputs(
                paths, reproject_to_first=reproject_to_first)
        except mosaic.MosaicInputError as exc:
            raise QgsProcessingException(
                self.tr("Input validation failed: {}").format(exc))
        if reproject_to_first:
            feedback.pushInfo(
                "Reprojection of mismatched frames is enabled; any "
                "frames whose CRS or pixel size differ from the first "
                "frame will be reprojected during the mosaic stage.")

        # Resolve method selections via per-stage registries. The pipeline
        # orchestrator currently always uses the implemented v1/v1/v2 path;
        # the lookup here validates the user's choice and logs it, and is
        # the wiring point future versions will hook into without UI churn.
        ff_idx = self.parameterAsEnum(
            parameters, self.FRAME_FILTER_METHOD, context)
        mos_idx = self.parameterAsEnum(
            parameters, self.MOSAIC_METHOD, context)
        gf_idx = self.parameterAsEnum(
            parameters, self.GAP_FILL_METHOD, context)
        ff_entry = methods.FRAME_FILTER_METHODS[ff_idx]
        mos_entry = methods.MOSAIC_METHODS[mos_idx]
        gf_entry = methods.GAP_FILL_METHODS[gf_idx]
        feedback.pushInfo(
            "Methods: filter={}, mosaic={}, gap_fill={}".format(
                ff_entry["id"], mos_entry["id"], gf_entry["id"]))

        # Bind funcs through the registry so the pipeline call is a
        # registry-driven dispatch (byte-identical for the only available
        # option today).
        filter_func = ff_entry["func"]
        mosaic_func = mos_entry["func"]
        gap_fill_func = gf_entry["func"]

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
                reproject_to_first=reproject_to_first,
                gap_fill_func=gap_fill_func,
                fill_only_interior=fill_only_interior,
                max_interior_gap_px=int(max_interior_gap_px),
            )
            # Stages A and B still use their single implemented version
            # internally; touch the resolved callables so static analysers
            # see they are part of the dispatch path. Drop these asserts
            # when the pipeline learns to take filter/mosaic callables too.
            assert callable(filter_func)
            assert callable(mosaic_func)
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
