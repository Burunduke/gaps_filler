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
    QgsProcessingParameterDefinition,
    QgsProcessingParameterEnum,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterNumber,
)

from .src import frame_filter, methods
from . import qgis_helpers
from .src.frame_filter import (
    AREA_HI,
    AREA_LO,
    ASPECT_MAX,
    CENTRE_WINDOW,
    FilterThresholds,
    MAX_DROPOUT_FRAC,
    MAX_STRIPE_RATIO,
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
    THRESHOLD_PRESET = "THRESHOLD_PRESET"
    K_MAD = "K_MAD"
    MAX_DROPOUT_FRAC = "MAX_DROPOUT_FRAC"
    MAX_STRIPE_RATIO = "MAX_STRIPE_RATIO"

    # Order matches the dropdown shown in QGIS. Index 0 keeps the
    # historical "use the eight raw inputs below" behaviour so existing
    # users / scripts that don't set the preset see no change.
    PRESET_CHOICES = [
        ("custom", "Custom (use values below)"),
        ("permissive", "Permissive"),
        ("default", "Default"),
        ("strict", "Strict"),
    ]

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
            "Rejects obviously-bad PIKA-L frames and copies the survivors "
            "into the chosen output folder; the rejection report lists "
            "why each frame was dropped. The actual rejection rule is "
            "picked from the 'Filter method' dropdown (v1 — hard "
            "thresholds; v2 — adaptive MAD per flight; v3 — per-band "
            "striping / dropout detection)."
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

        # Pipeline TO-DO #13: a single dropdown that overrides the eight
        # raw threshold inputs with a named bundle. "Custom" keeps the
        # raw inputs (default), so adding this parameter is backwards
        # compatible.
        preset_param = QgsProcessingParameterEnum(
            self.THRESHOLD_PRESET,
            self.tr("Threshold preset"),
            options=[label for _, label in self.PRESET_CHOICES],
            defaultValue=0,
        )
        preset_param.setHelp(self.tr(
            "Pick a named bundle of all eight thresholds instead of "
            "tuning each one. 'Custom' uses the raw values below "
            "(unchanged behaviour). 'Permissive' relaxes every "
            "threshold (use when v1 over-rejects on a new sensor). "
            "'Default' matches the documented PIKA-L defaults. "
            "'Strict' tightens every threshold (clean acquisitions, "
            "drop on any doubt). When a non-Custom preset is chosen "
            "the raw threshold inputs below are ignored."
        ))
        self.addParameter(preset_param)

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

        skew_param = QgsProcessingParameterNumber(
            self.SKEW_MAX, self.tr("Max skew (rotation tolerance)"),
            type=Double, defaultValue=SKEW_MAX, minValue=0.0, maxValue=1.0)
        skew_param.setFlags(
            skew_param.flags()
            | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(skew_param)
        area_lo_param = QgsProcessingParameterNumber(
            self.AREA_LO,
            self.tr("Min area ratio vs. flight median"),
            type=Double, defaultValue=AREA_LO, minValue=0.0, maxValue=1.0)
        area_lo_param.setFlags(
            area_lo_param.flags()
            | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(area_lo_param)
        area_hi_param = QgsProcessingParameterNumber(
            self.AREA_HI,
            self.tr("Max area ratio vs. flight median"),
            type=Double, defaultValue=AREA_HI, minValue=1.0, maxValue=10.0)
        area_hi_param.setFlags(
            area_hi_param.flags()
            | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(area_hi_param)
        aspect_param = QgsProcessingParameterNumber(
            self.ASPECT_MAX,
            self.tr("Max aspect ratio (long / short side)"),
            type=Double, defaultValue=ASPECT_MAX,
            minValue=1.0, maxValue=10.0)
        aspect_param.setFlags(
            aspect_param.flags()
            | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(aspect_param)
        centre_param = QgsProcessingParameterNumber(
            self.CENTRE_WINDOW,
            self.tr("Centre window size (pixels)"),
            type=Integer, defaultValue=CENTRE_WINDOW,
            minValue=4, maxValue=4096)
        centre_param.setFlags(
            centre_param.flags()
            | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(centre_param)
        min_valid_param = QgsProcessingParameterNumber(
            self.MIN_VALID_FRACTION,
            self.tr("Min fraction of valid pixels in centre"),
            type=Double, defaultValue=MIN_VALID_FRACTION,
            minValue=0.0, maxValue=1.0)
        min_valid_param.setFlags(
            min_valid_param.flags()
            | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(min_valid_param)
        std_min_param = QgsProcessingParameterNumber(
            self.STD_MIN,
            self.tr("Min std-dev of centre (low-variance reject)"),
            type=Double, defaultValue=STD_MIN,
            minValue=0.0, maxValue=1e6)
        std_min_param.setFlags(
            std_min_param.flags()
            | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(std_min_param)
        sat_param = QgsProcessingParameterNumber(
            self.SATURATION_FRACTION,
            self.tr("Max saturated-pixel fraction in centre"),
            type=Double, defaultValue=SATURATION_FRACTION,
            minValue=0.0, maxValue=1.0)
        sat_param.setFlags(
            sat_param.flags()
            | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(sat_param)

        # Roadmap item #2: only used when the user picks the v2 adaptive
        # MAD filter method. Default 3.0 = drop frames whose footprint
        # area is more than ~3 σ from the dataset median (assuming
        # normal-ish spread; MAD is scaled by 1.4826 inside v2). Ignored
        # by v1, so adding this parameter is backwards compatible.
        kmad_param = QgsProcessingParameterNumber(
            self.K_MAD,
            self.tr("K_MAD (only used by v2 adaptive MAD filter)"),
            type=Double, defaultValue=3.0, minValue=0.0)
        kmad_param.setHelp(self.tr(
            "Strictness of the v2 adaptive filter: a frame is rejected "
            "when its footprint area deviates from the dataset median "
            "by more than K_MAD * scaled-MAD (1.4826 * MAD). Larger "
            "K_MAD = more permissive. Ignored by v1. Default 3.0."
        ))
        kmad_param.setFlags(
            kmad_param.flags()
            | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(kmad_param)

        # Roadmap item #3: only used when the user picks the v3 per-band
        # filter method. Defaults preserve the documented behaviour;
        # ignored by v1 / v2, so adding these parameters is backwards
        # compatible.
        dropout_param = QgsProcessingParameterNumber(
            self.MAX_DROPOUT_FRAC,
            self.tr("Max dropout fraction "
                    "(only used by v3 per-band filter)"),
            type=Double, defaultValue=MAX_DROPOUT_FRAC,
            minValue=0.0, maxValue=1.0)
        dropout_param.setHelp(self.tr(
            "v3 per-band filter: a frame is rejected when any band has "
            "more than this share of zero / saturated / NoData pixels "
            "inside the valid footprint. Default 0.30. Ignored by "
            "v1 / v2."
        ))
        dropout_param.setFlags(
            dropout_param.flags()
            | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(dropout_param)

        stripe_param = QgsProcessingParameterNumber(
            self.MAX_STRIPE_RATIO,
            self.tr("Max stripe ratio "
                    "(only used by v3 per-band filter)"),
            type=Double, defaultValue=MAX_STRIPE_RATIO,
            minValue=0.0)
        stripe_param.setHelp(self.tr(
            "v3 per-band filter: a frame is rejected when any band's "
            "ratio of column-mean variance to overall variance exceeds "
            "this value. Values closer to 1.0 mean more striping. "
            "Default 0.5. Ignored by v1 / v2."
        ))
        stripe_param.setFlags(
            stripe_param.flags()
            | QgsProcessingParameterDefinition.FlagAdvanced)
        self.addParameter(stripe_param)

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

        preset_idx = self.parameterAsEnum(
            parameters, self.THRESHOLD_PRESET, context)
        preset_id, preset_label = self.PRESET_CHOICES[preset_idx]
        if preset_id == "custom":
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
            feedback.pushInfo(
                "Threshold preset: {} (using raw values below)".format(
                    preset_label))
        else:
            thresholds = frame_filter.preset_thresholds(preset_id)
            feedback.pushInfo(
                "Threshold preset: {} (raw threshold inputs ignored)"
                .format(preset_label))

        paths = [lyr.source() for lyr in layers]

        method_idx = self.parameterAsEnum(
            parameters, self.FRAME_FILTER_METHOD, context)
        method_entry = methods.FRAME_FILTER_METHODS[method_idx]
        feedback.pushInfo(
            "Frame filter method: {}".format(method_entry["id"]))

        # Build kwargs and only forward ``k_mad`` when v2 is selected,
        # so the v1 dispatch path stays byte-equivalent to before.
        method_kwargs = {
            "thresholds": thresholds,
            "is_canceled": feedback.isCanceled,
        }
        if method_entry["id"] == "v2_adaptive_mad":
            method_kwargs["k_mad"] = self.parameterAsDouble(
                parameters, self.K_MAD, context)
        elif method_entry["id"] == "v3_per_band":
            method_kwargs["max_dropout_frac"] = self.parameterAsDouble(
                parameters, self.MAX_DROPOUT_FRAC, context)
            method_kwargs["max_stripe_ratio"] = self.parameterAsDouble(
                parameters, self.MAX_STRIPE_RATIO, context)

        try:
            # Pipeline TO-DO #11: forward QGIS cancellation so the filter
            # function checks ``feedback.isCanceled`` between frames in
            # both passes and aborts cleanly on user cancel.
            good, rejected = method_entry["func"](paths, **method_kwargs)
        except Exception as exc:
            qgis_helpers.handle_processing_exception(exc)

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
