# -*- coding: utf-8 -*-
"""Helper functions for QGIS Processing algorithms.

This module contains utility functions that are used across multiple
QGIS Processing algorithm wrappers to reduce code duplication.
"""

from qgis.core import QgsProcessingException


def handle_processing_exception(exc):
    """Convert core-module exceptions into QgsProcessingException.
    
    This helper consolidates the repeated try/except blocks that convert
    core-module exceptions into QgsProcessingException (or log + re-raise).
    
    Usage:
        try:
            # algorithm logic
        except Exception as exc:
            handle_processing_exception(exc)
    """
    if isinstance(exc, QgsProcessingException):
        raise
    elif isinstance(exc, RuntimeError) and str(exc) == "canceled":
        raise QgsProcessingException("Canceled by user")
    else:
        raise QgsProcessingException(str(exc))