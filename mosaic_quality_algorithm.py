# -*- coding: utf-8 -*-
"""Processing algorithm wrapper around :mod:`mosaic_quality`.

Compares a built mosaic against a reference orthophoto and reports
per-band RMSE / MAE / PSNR / SSIM plus their means. The auto-generated
QGIS dialog gives us layer pickers, batch mode and history for free.
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

    # Output keys (means across bands).
    OUT_MEAN_RMSE = "MEAN_RMSE"
    OUT_MEAN_MAE = "MEAN_MAE"
    OUT_MEAN_PSNR = "MEAN_PSNR"
    OUT_MEAN_SSIM = "MEAN_SSIM"

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
            "(scikit-image required) and their means across all bands.\n\n"
            "Both rasters must share CRS, resolution and extent. Pixels "
            "marked nodata in either raster are excluded; no spatial "
            "alignment or cropping is performed beyond that mask.\n\n"
            "Per-band results are logged as a table; the mean of each "
            "metric is also exposed as an algorithm output."
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
        self.addOutput(QgsProcessingOutputNumber(
            self.OUT_MEAN_RMSE, self.tr("Mean RMSE")))
        self.addOutput(QgsProcessingOutputNumber(
            self.OUT_MEAN_MAE, self.tr("Mean MAE")))
        self.addOutput(QgsProcessingOutputNumber(
            self.OUT_MEAN_PSNR, self.tr("Mean PSNR")))
        self.addOutput(QgsProcessingOutputNumber(
            self.OUT_MEAN_SSIM, self.tr("Mean SSIM")))

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

        # Pretty per-band table + means.
        feedback.pushInfo("\n" + mosaic_quality.format_report(summary))

        return {
            self.OUT_MEAN_RMSE: float(summary["mean_rmse"]),
            self.OUT_MEAN_MAE: float(summary["mean_mae"]),
            self.OUT_MEAN_PSNR: float(summary["mean_psnr"]),
            self.OUT_MEAN_SSIM: float(summary["mean_ssim"]),
        }
