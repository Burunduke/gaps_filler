# -*- coding: utf-8 -*-
"""Stage C orchestrator of the hyperspectral pipeline.

Chains Stage A (:mod:`frame_filter`) → Stage B (:mod:`mosaic`) → Stage C
(per-band gap fill via :func:`fill_nodata.fill_nodata`). The mosaic from
Stage B is a float32 GeoTIFF with NaN as NoData; Stage C reads each band
into a numpy array, calls the existing array-level
:func:`fill_nodata.fill_nodata` (a 2-D numpy in / 2-D numpy out function
— see ``fill_nodata.py``), and writes the filled band into the final
output. ``osgeo.gdal`` is intentionally not used here: the public API of
``fill_nodata`` is pure numpy, so a GDAL round-trip would be wasted I/O.
"""

from __future__ import annotations

import os
from typing import Callable, Optional

import numpy as np
import rasterio

from . import fill_nodata, frame_filter, mosaic


_ProgressCb = Callable[[float, str], None]


def _noop(fraction: float, message: str) -> None:
    return None


def run_pipeline(
    input_paths: list[str],
    output_path: str,
    *,
    max_distance: int = 100,
    smoothing_iterations: int = 0,
    progress: Optional[_ProgressCb] = None,
) -> dict:
    """Run filter → mosaic → fill_nodata. Return a summary dict."""
    cb: _ProgressCb = progress if progress is not None else _noop

    # ---- Stage A: filter -------------------------------------------------
    good, rejected = frame_filter.filter_frames(input_paths)
    if len(good) == 0:
        raise RuntimeError("all frames rejected by filter")
    cb(0.05, "filtered: {} kept, {} rejected".format(len(good), len(rejected)))

    # ---- Stage B: mosaic -------------------------------------------------
    # Choice: place the temp mosaic next to the final output as
    # ``<output>.mosaic.tif``. Simplest possible — no tempfile bookkeeping,
    # path is predictable for debugging, and it is removed at the end.
    mosaic_path = output_path + ".mosaic.tif"
    mosaic.mosaic_frames(
        good,
        mosaic_path,
        progress=lambda f, m: cb(0.05 + 0.65 * f, "mosaic: " + m),
    )

    # ---- Stage C: per-band gap fill -------------------------------------
    try:
        with rasterio.open(mosaic_path) as src:
            profile = src.profile.copy()
            band_count = int(src.count)
            descriptions = src.descriptions

        # Make sure the destination is a fresh, sane GeoTIFF.
        profile.update(driver="GTiff")

        with rasterio.open(mosaic_path) as src, \
                rasterio.open(output_path, "w", **profile) as dst:
            if descriptions:
                for i, desc in enumerate(descriptions, start=1):
                    if desc:
                        dst.set_band_description(i, desc)

            for b in range(1, band_count + 1):
                arr = src.read(b)
                # Call site for the existing array-level fill function.
                # See ``fill_nodata.py`` -> ``def fill_nodata(band, mask=None,
                # max_search_dist=100.0, smoothing_iterations=0, nodata=None,
                # interpolation="INV_DIST", feedback=None)``. Mosaic NoData
                # is NaN (set by ``mosaic.mosaic_frames``), so passing
                # ``nodata=np.nan`` lets the function derive the validity
                # mask itself.
                filled = fill_nodata.fill_nodata(
                    arr,
                    max_search_dist=float(max_distance),
                    smoothing_iterations=int(smoothing_iterations),
                    nodata=np.nan,
                )
                if filled.dtype != np.float32:
                    filled = filled.astype(np.float32)
                dst.write(filled, b)

                frac = 0.70 + 0.30 * (b / band_count)
                cb(frac, "fill: band {}/{}".format(b, band_count))
    finally:
        # Best-effort temp cleanup.
        try:
            os.remove(mosaic_path)
        except OSError:
            pass

    return {
        "input_count": len(input_paths),
        "kept_count": len(good),
        "rejected": rejected,
        "output_path": output_path,
        "band_count": band_count,
    }
