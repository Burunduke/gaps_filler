# -*- coding: utf-8 -*-
"""Processing algorithm wrapper around :mod:`mosaic_quality`.

Compares a built mosaic against a reference orthophoto and reports
per-band RMSE / MAE / PSNR / SSIM, plus three aggregates per metric
(``MEAN_<M>``, ``WORST_<M>``, ``P05_<M>``) and a whole-cube SAM
(Spectral Angle Mapper). The auto-generated QGIS dialog gives us
layer pickers, batch mode and history for free.
"""

from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputNumber,
    QgsProcessingParameterRasterLayer,
)

from . import mosaic_quality


class MosaicQualityAlgorithm(QgsProcessingAlgorithm):
    """Compare a mosaic to a reference orthophoto and emit pixel-wise metrics."""

    REFERENCE = "REFERENCE"
    MOSAIC = "MOSAIC"

    # Output keys. For each band-level metric we expose three aggregates
    # in the documented order MEAN / WORST / P05. WORST = max for
    # lower-is-better metrics (RMSE, MAE), min for higher-is-better
    # (PSNR, SSIM). P05 means "5% of bands are at least this bad"
    # regardless of polarity (np.percentile(..,5) for higher-is-better,
    # np.percentile(..,95) for lower-is-better). Plus whole-cube SAM.
    OUT_MEAN_RMSE = "MEAN_RMSE"
    OUT_WORST_RMSE = "WORST_RMSE"
    OUT_WORST_RMSE_BAND = "WORST_RMSE_BAND"
    OUT_P05_RMSE = "P05_RMSE"
    OUT_P05_RMSE_BAND = "P05_RMSE_BAND"
    OUT_MEAN_MAE = "MEAN_MAE"
    OUT_WORST_MAE = "WORST_MAE"
    OUT_WORST_MAE_BAND = "WORST_MAE_BAND"
    OUT_P05_MAE = "P05_MAE"
    OUT_P05_MAE_BAND = "P05_MAE_BAND"
    OUT_MEAN_PSNR = "MEAN_PSNR"
    OUT_WORST_PSNR = "WORST_PSNR"
    OUT_WORST_PSNR_BAND = "WORST_PSNR_BAND"
    OUT_P05_PSNR = "P05_PSNR"
    OUT_P05_PSNR_BAND = "P05_PSNR_BAND"
    OUT_MEAN_SSIM = "MEAN_SSIM"
    OUT_WORST_SSIM = "WORST_SSIM"
    OUT_WORST_SSIM_BAND = "WORST_SSIM_BAND"
    OUT_P05_SSIM = "P05_SSIM"
    OUT_P05_SSIM_BAND = "P05_SSIM_BAND"
    OUT_SAM = "SAM"           # whole-cube SAM in radians (lower is better)
    OUT_SAM_DEG = "SAM_DEG"   # same metric in degrees

    # ---- Algorithm metadata ------------------------------------------------

    def tr(self, text):
        return QCoreApplication.translate("MosaicQualityAlgorithm", text)

    def createInstance(self):
        return MosaicQualityAlgorithm()

    def name(self):
        return "mosaic_quality"

    def displayName(self):
        return self.tr("Mosaic quality (vs reference)")

    def group(self):
        return self.tr("Raster analysis")

    def groupId(self):
        return "rasteranalysis"

    def shortHelpString(self):
        return self.tr(
            "Pixel-wise quality assessment of a mosaic against a reference "
            "orthophoto. Computes per-band RMSE, MAE, PSNR and SSIM "
            "(scikit-image required) and aggregates per metric:\n"
            "  - MEAN_<M>: average across bands;\n"
            "  - WORST_<M>: worst per-band value (max for RMSE/MAE,\n"
            "    min for PSNR/SSIM);\n"
            "  - WORST_<M>_BAND: 1-based index of the band that hit\n"
            "    WORST_<M> (first occurrence on ties → deterministic);\n"
            "  - P05_<M>: '5% of bands are at least this bad' — uses\n"
            "    np.percentile(..,5) for PSNR/SSIM and np.percentile(..,95)\n"
            "    for RMSE/MAE so the polarity convention is consistent;\n"
            "  - P05_<M>_BAND: 1-based index of the band whose value is\n"
            "    closest to P05_<M> (first occurrence on ties).\n\n"
            "Also computes whole-cube SAM (Spectral Angle Mapper, lower is "
            "better): mean over valid pixels of the angle between reference "
            "and mosaic spectra; reported in radians (SAM) and degrees "
            "(SAM_DEG).\n\n"
            "The reference is automatically warped onto the mosaic's exact "
            "grid (same extent, pixel size and projection), so only the "
            "overlapping region is compared and the two rasters do not need "
            "to share extent up-front. Pixels marked nodata in either "
            "raster are excluded from every metric; SAM additionally "
            "requires a pixel to be valid in EVERY band.\n\n"
            "Per-band results are logged as a table; the aggregate values "
            "are also exposed as algorithm outputs."
        )

    # ---- Parameters --------------------------------------------------------

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.REFERENCE, self.tr("Reference orthophoto")
            )
        )
        self.addParameter(
            QgsProcessingParameterRasterLayer(
                self.MOSAIC, self.tr("Built mosaic")
            )
        )
        # Band-level metric aggregates: MEAN / WORST / WORST_BAND / P05 /
        # P05_BAND. The *_BAND outputs are 1-based band indices (ints);
        # QgsProcessingOutputNumber accepts ints fine, and there is no
        # dedicated integer-output class for processing algorithms.
        self.addOutput(QgsProcessingOutputNumber(
            self.OUT_MEAN_RMSE, self.tr("Mean RMSE")))
        self.addOutput(QgsProcessingOutputNumber(
            self.OUT_WORST_RMSE, self.tr("Worst RMSE (max — lower is better)")))
        self.addOutput(QgsProcessingOutputNumber(
            self.OUT_WORST_RMSE_BAND,
            self.tr("Worst RMSE band (1-based index)")))
        self.addOutput(QgsProcessingOutputNumber(
            self.OUT_P05_RMSE,
            self.tr("P05 RMSE (95th pct: 5% of bands are at least this bad)")))
        self.addOutput(QgsProcessingOutputNumber(
            self.OUT_P05_RMSE_BAND,
            self.tr("P05 RMSE band (1-based index, nearest to P05)")))
        self.addOutput(QgsProcessingOutputNumber(
            self.OUT_MEAN_MAE, self.tr("Mean MAE")))
        self.addOutput(QgsProcessingOutputNumber(
            self.OUT_WORST_MAE, self.tr("Worst MAE (max — lower is better)")))
        self.addOutput(QgsProcessingOutputNumber(
            self.OUT_WORST_MAE_BAND,
            self.tr("Worst MAE band (1-based index)")))
        self.addOutput(QgsProcessingOutputNumber(
            self.OUT_P05_MAE,
            self.tr("P05 MAE (95th pct: 5% of bands are at least this bad)")))
        self.addOutput(QgsProcessingOutputNumber(
            self.OUT_P05_MAE_BAND,
            self.tr("P05 MAE band (1-based index, nearest to P05)")))
        self.addOutput(QgsProcessingOutputNumber(
            self.OUT_MEAN_PSNR, self.tr("Mean PSNR")))
        self.addOutput(QgsProcessingOutputNumber(
            self.OUT_WORST_PSNR, self.tr("Worst PSNR (min — higher is better)")))
        self.addOutput(QgsProcessingOutputNumber(
            self.OUT_WORST_PSNR_BAND,
            self.tr("Worst PSNR band (1-based index)")))
        self.addOutput(QgsProcessingOutputNumber(
            self.OUT_P05_PSNR,
            self.tr("P05 PSNR (5th pct: 5% of bands are at least this bad)")))
        self.addOutput(QgsProcessingOutputNumber(
            self.OUT_P05_PSNR_BAND,
            self.tr("P05 PSNR band (1-based index, nearest to P05)")))
        self.addOutput(QgsProcessingOutputNumber(
            self.OUT_MEAN_SSIM, self.tr("Mean SSIM")))
        self.addOutput(QgsProcessingOutputNumber(
            self.OUT_WORST_SSIM, self.tr("Worst SSIM (min — higher is better)")))
        self.addOutput(QgsProcessingOutputNumber(
            self.OUT_WORST_SSIM_BAND,
            self.tr("Worst SSIM band (1-based index)")))
        self.addOutput(QgsProcessingOutputNumber(
            self.OUT_P05_SSIM,
            self.tr("P05 SSIM (5th pct: 5% of bands are at least this bad)")))
        self.addOutput(QgsProcessingOutputNumber(
            self.OUT_P05_SSIM_BAND,
            self.tr("P05 SSIM band (1-based index, nearest to P05)")))
        # Whole-cube SAM (lower is better).
        self.addOutput(QgsProcessingOutputNumber(
            self.OUT_SAM, self.tr("SAM, radians (lower is better)")))
        self.addOutput(QgsProcessingOutputNumber(
            self.OUT_SAM_DEG, self.tr("SAM, degrees (lower is better)")))

    # ---- Execution ---------------------------------------------------------

    def processAlgorithm(self, parameters, context, feedback):
        ref_layer = self.parameterAsRasterLayer(
            parameters, self.REFERENCE, context)
        if ref_layer is None:
            raise QgsProcessingException(
                self.invalidRasterError(parameters, self.REFERENCE))

        mos_layer = self.parameterAsRasterLayer(
            parameters, self.MOSAIC, context)
        if mos_layer is None:
            raise QgsProcessingException(
                self.invalidRasterError(parameters, self.MOSAIC))

        ref_path = ref_layer.source()
        mos_path = mos_layer.source()

        feedback.pushInfo("Reference: {}".format(ref_path))
        feedback.pushInfo("Mosaic:    {}".format(mos_path))

        try:
            summary = mosaic_quality.compare_rasters(
                ref_path, mos_path, feedback=feedback)
        except (ValueError, IOError) as exc:
            raise QgsProcessingException(str(exc))
        except RuntimeError as exc:
            # Most likely scikit-image missing — surface verbatim.
            raise QgsProcessingException(str(exc))
        except Exception as exc:
            raise QgsProcessingException(str(exc))

        if not summary["per_band"]:
            raise QgsProcessingException(
                self.tr("No band produced a valid metric "
                        "(no overlapping non-nodata pixels)."))

        # Pretty per-band table + aggregates + SAM.
        feedback.pushInfo("\n" + mosaic_quality.format_report(summary))

        return {
            self.OUT_MEAN_RMSE: float(summary["mean_rmse"]),
            self.OUT_WORST_RMSE: float(summary["worst_rmse"]),
            self.OUT_WORST_RMSE_BAND: int(summary["worst_rmse_band"]),
            self.OUT_P05_RMSE: float(summary["p05_rmse"]),
            self.OUT_P05_RMSE_BAND: int(summary["p05_rmse_band"]),
            self.OUT_MEAN_MAE: float(summary["mean_mae"]),
            self.OUT_WORST_MAE: float(summary["worst_mae"]),
            self.OUT_WORST_MAE_BAND: int(summary["worst_mae_band"]),
            self.OUT_P05_MAE: float(summary["p05_mae"]),
            self.OUT_P05_MAE_BAND: int(summary["p05_mae_band"]),
            self.OUT_MEAN_PSNR: float(summary["mean_psnr"]),
            self.OUT_WORST_PSNR: float(summary["worst_psnr"]),
            self.OUT_WORST_PSNR_BAND: int(summary["worst_psnr_band"]),
            self.OUT_P05_PSNR: float(summary["p05_psnr"]),
            self.OUT_P05_PSNR_BAND: int(summary["p05_psnr_band"]),
            self.OUT_MEAN_SSIM: float(summary["mean_ssim"]),
            self.OUT_WORST_SSIM: float(summary["worst_ssim"]),
            self.OUT_WORST_SSIM_BAND: int(summary["worst_ssim_band"]),
            self.OUT_P05_SSIM: float(summary["p05_ssim"]),
            self.OUT_P05_SSIM_BAND: int(summary["p05_ssim_band"]),
            self.OUT_SAM: float(summary["sam"]),
            self.OUT_SAM_DEG: float(summary["sam_deg"]),
        }
