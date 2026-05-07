# -*- coding: utf-8 -*-
"""Processing provider that exposes the gaps-filler algorithm to QGIS."""

from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsProcessingProvider

# Make sure the compiled Qt resource (icon.png) is registered.
from .resources import *  # noqa: F401,F403
from .gaps_filler_algorithm import FillNoDataAlgorithm
from .hyperspectral_algorithm import HyperspectralPipelineAlgorithm
from .frame_filter_algorithm import FrameFilterAlgorithm
from .mosaic_algorithm import MosaicAlgorithm
from .mosaic_quality_algorithm import MosaicQualityAlgorithm
from .airborne_georef_algorithm import AirborneGeorefAlgorithm


class GapsFillerProvider(QgsProcessingProvider):
    """Single-algorithm provider for the Hyperspectral gaps filler plugin."""

    def id(self):
        # Combined with algorithm name -> "gapsfiller:fillnodata".
        return "gapsfiller"

    def name(self):
        return "Hyperspectral gaps filler"

    def icon(self):
        return QIcon(":/plugins/gaps_filler/icon.png")

    def loadAlgorithms(self):
        self.addAlgorithm(FillNoDataAlgorithm())
        self.addAlgorithm(HyperspectralPipelineAlgorithm())
        self.addAlgorithm(FrameFilterAlgorithm())
        self.addAlgorithm(MosaicAlgorithm())
        self.addAlgorithm(MosaicQualityAlgorithm())
        self.addAlgorithm(AirborneGeorefAlgorithm())
