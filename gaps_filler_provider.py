# -*- coding: utf-8 -*-
"""Processing provider that exposes the gaps-filler algorithm to QGIS."""

from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsProcessingProvider

# Make sure the compiled Qt resource (icon.png) is registered.
from .resources import *  # noqa: F401,F403
from .gaps_filler_algorithm import FillNoDataAlgorithm


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
