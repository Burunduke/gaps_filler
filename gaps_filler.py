# -*- coding: utf-8 -*-
"""GapsFiller QGIS plugin entry point.

This module is intentionally tiny: it only registers a
:class:`QgsProcessingProvider` with QGIS. The actual algorithm lives in
:mod:`gaps_filler_algorithm`; the gap-fill logic lives in
:mod:`fill_nodata`. There is no custom dialog — QGIS auto-generates one
from the algorithm's parameter list.
"""

import os.path

from qgis.PyQt.QtCore import QSettings, QTranslator, QCoreApplication
from qgis.core import QgsApplication

from .gaps_filler_provider import GapsFillerProvider


class GapsFiller:
    """QGIS Plugin Implementation."""

    def __init__(self, iface):
        """Constructor.

        :param iface: An interface instance that will be passed to this
            class which provides the hook by which you can manipulate the
            QGIS application at run time.
        :type iface: QgsInterface
        """
        self.iface = iface
        self.provider = None

        # Plugin directory + i18n setup (kept from the Plugin Builder
        # scaffold; harmless even though we currently ship no .qm files).
        self.plugin_dir = os.path.dirname(__file__)
        locale = QSettings().value("locale/userLocale")
        if locale:
            locale = locale[0:2]
            locale_path = os.path.join(
                self.plugin_dir,
                "i18n",
                "GapsFiller_{}.qm".format(locale),
            )
            if os.path.exists(locale_path):
                self.translator = QTranslator()
                self.translator.load(locale_path)
                QCoreApplication.installTranslator(self.translator)

    def initGui(self):
        """Register the Processing provider with QGIS."""
        self.provider = GapsFillerProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def unload(self):
        """Remove the Processing provider on plugin unload."""
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
