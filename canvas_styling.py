# -*- coding: utf-8 -*-
"""Auto-add raster results to the QGIS canvas as RGB composites.

Pipeline TO-DO #15: when a Processing algorithm produces a hyperspectral
mosaic and the user has the standard "open output file after running"
option enabled, QGIS by default shows band 1 in grayscale. For typical
hyperspectral cubes (with hundreds of bands spanning the visible to near-infrared
spectrum) that is a near-black NIR-ish slice and a poor first impression. We attach a layer post-processor to
the output details so QGIS swaps in a 3-band RGB composite renderer
once the layer is loaded onto the canvas.

The post-processor only runs if the user actually loads the layer
(``context.willLoadLayerOnCompletion`` is set by the Processing
framework when the destination layer is registered). If the user runs
in batch mode without loading, nothing happens -- safe and no-op.
"""

from qgis.core import (
    QgsContrastEnhancement,
    QgsMultiBandColorRenderer,
    QgsProcessingLayerPostProcessorInterface,
    QgsRasterLayer,
    QgsRasterMinMaxOrigin,
)


# Approximate spectral coverage of typical hyperspectral sensors is ~400-1000 nm. We pick
# RGB triplets as fractional positions of the band count so the rule
# survives sensor truncation and degrades cleanly on shorter cubes:
#   * red   ~ 640 nm  -> 40% of the way through 400-1000 nm
#   * green ~ 550 nm  -> 25%
#   * blue  ~ 470 nm  -> 12%
# These are intentionally simple constants -- if a future sensor needs
# a different mapping we can promote them to a per-method registry.
_RED_FRAC = 0.40
_GREEN_FRAC = 0.25
_BLUE_FRAC = 0.12


def _rgb_band_indices(band_count):
    """Pick (red, green, blue) 1-based band indices for a cube.

    Falls back to grayscale-style band 1 / 1 / 1 for cubes too small to
    pick three distinct slots. The minmax stretch QGIS applies later
    will still make the result visible.
    """
    if band_count < 3:
        # Single- or two-band raster: nothing sensible to compose.
        # Returning band 1 for every channel hands QGIS a renderer it
        # can still display (just monochrome-tinted).
        return 1, 1, 1

    def _pick(frac):
        # Clamp to [1, band_count] in 1-based indexing.
        idx = int(round(frac * (band_count - 1))) + 1
        return max(1, min(band_count, idx))

    return _pick(_RED_FRAC), _pick(_GREEN_FRAC), _pick(_BLUE_FRAC)


class _HyperspectralRgbPostProcessor(
        QgsProcessingLayerPostProcessorInterface):
    """Replace the default grayscale renderer with an RGB composite.

    QGIS instantiates the layer with its default style after the
    algorithm finishes; ``postProcessLayer`` is then called once on the
    main thread with the live layer, letting us swap in the renderer
    we want users to see first.
    """

    # QGIS keeps a strong reference to the post-processor via the
    # output details, so the instance survives until the layer is
    # added to the project. We just need to keep a Python-side ref to
    # the instance so the C++ side does not see a dangling object;
    # ``_keep_alive`` on the output details (set by the caller) does
    # exactly that.

    def __init__(self, red, green, blue):
        super().__init__()
        self._red = red
        self._green = green
        self._blue = blue

    def postProcessLayer(self, layer, context, feedback):
        if not isinstance(layer, QgsRasterLayer):
            return
        provider = layer.dataProvider()
        if provider is None:
            return
        # Cap requested band against the actual band count, in case
        # the destination has fewer bands than we expected (e.g. a
        # debug single-band write).
        bc = provider.bandCount()
        red = min(self._red, bc)
        green = min(self._green, bc)
        blue = min(self._blue, bc)

        renderer = QgsMultiBandColorRenderer(
            provider, red, green, blue)
        layer.setRenderer(renderer)

        # Trigger QGIS's standard cumulative-cut stretch (2-98 %) so
        # the composite is actually visible -- without this step the
        # renderer shows up but the histogram is uninitialised and
        # most pixels render as black.
        try:
            layer.setContrastEnhancement(
                QgsContrastEnhancement.StretchToMinimumMaximum,
                QgsRasterMinMaxOrigin.CumulativeCut,
            )
        except Exception:
            # Older QGIS builds expose a slightly different signature;
            # silently skip the stretch rather than fail the whole
            # post-process step.
            pass

        layer.triggerRepaint()


def attach_rgb_post_processor(output_details, band_count):
    """Wire an RGB-composite post-processor onto an output layer.

    ``output_details`` is the ``QgsProcessingContext.LayerDetails``
    instance Processing creates when ``parameterAsOutputLayer`` is
    called. We set its ``postProcessor`` so QGIS invokes our renderer
    swap once the layer is loaded onto the canvas.

    Returns the post-processor instance so the caller can stash it
    somewhere with a long enough lifetime (the output details object
    only holds a weak reference on some Qt builds).
    """
    red, green, blue = _rgb_band_indices(band_count)
    pp = _HyperspectralRgbPostProcessor(red, green, blue)
    output_details.setPostProcessor(pp)
    return pp


def attach_rgb_post_processor_if_needed(context, output_path, band_count):
    """Wire an RGB-composite post-processor onto an output layer if needed.
    
    This function checks if the layer will be loaded on completion and
    attaches the RGB post-processor if so.
    
    Args:
        context: QgsProcessingContext
        output_path: Path to the output file
        band_count: Number of bands in the raster
        
    Returns:
        The post-processor instance or None if not attached
    """
    if context.willLoadLayerOnCompletion(output_path):
        pending = context.layersToLoadOnCompletion()
        details = pending.get(output_path)
        if details is not None:
            return attach_rgb_post_processor(details, band_count)
    return None
