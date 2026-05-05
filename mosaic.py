# -*- coding: utf-8 -*-
"""Stage B of the hyperspectral pipeline (see ``hyperspectral_plan.md``).

Mosaic a list of already-filtered, georeferenced PIKA-L GeoTIFF frames
into a single tiled BigTIFF, processed band-by-band to keep memory
bounded. Overlapping pixels follow first-write-wins
(``method="first"`` in :func:`rasterio.merge.merge`). Output is
``float32`` with ``NaN`` as NoData.

Public API:
    * :class:`MosaicInputError` — raised on incompatible inputs.
    * :func:`validate_inputs` — cross-frame compatibility check.
    * :func:`mosaic_frames` — band-streaming mosaic writer.

Dependencies are limited to ``rasterio``, ``numpy`` and the Python
stdlib; in particular this module must not import ``osgeo.gdal`` or
``qgis``.
"""

from __future__ import annotations

from typing import Callable, Optional

import numpy as np
import rasterio
from rasterio.merge import merge
from rasterio.transform import from_origin


_PIXEL_REL_TOL: float = 1e-6


class MosaicInputError(ValueError):
    """Raised when input frames are not compatible for mosaicing."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_inputs(paths: list[str]) -> dict:
    """Check that ``paths`` can be mosaicked together.

    Verifies, across every frame: identical CRS, identical absolute
    pixel size (relative tolerance ``1e-6``), identical band count and
    identical source dtype string. Raises :class:`MosaicInputError`
    with the offending path on any mismatch.

    Returns a dict with keys ``'crs'``, ``'res'`` (``(xres, yres)``),
    ``'count'``, ``'src_dtype'``, ``'nodata'`` (per-frame NoData of the
    first frame, may be ``None``).
    """
    if not paths:
        raise MosaicInputError("no input frames")

    with rasterio.open(paths[0]) as first:
        ref_crs = first.crs
        ref_xres = abs(first.transform.a)
        ref_yres = abs(first.transform.e)
        ref_count = int(first.count)
        ref_dtype = str(first.dtypes[0])
        ref_nodata = first.nodata

    for p in paths[1:]:
        with rasterio.open(p) as src:
            xres = abs(src.transform.a)
            yres = abs(src.transform.e)
            if src.crs != ref_crs:
                raise MosaicInputError(
                    "CRS mismatch in {}: {!r} != {!r}".format(p, src.crs, ref_crs)
                )
            if (abs(xres - ref_xres) > _PIXEL_REL_TOL * ref_xres
                    or abs(yres - ref_yres) > _PIXEL_REL_TOL * ref_yres):
                raise MosaicInputError(
                    "pixel size mismatch in {}: ({}, {}) != ({}, {})".format(
                        p, xres, yres, ref_xres, ref_yres))
            if int(src.count) != ref_count:
                raise MosaicInputError(
                    "band count mismatch in {}: {} != {}".format(
                        p, src.count, ref_count))
            if str(src.dtypes[0]) != ref_dtype:
                raise MosaicInputError(
                    "dtype mismatch in {}: {} != {}".format(
                        p, src.dtypes[0], ref_dtype))

    return {
        "crs": ref_crs,
        "res": (ref_xres, ref_yres),
        "count": ref_count,
        "src_dtype": ref_dtype,
        "nodata": ref_nodata,
    }


def mosaic_frames(
    paths: list[str],
    output_path: str,
    *,
    progress: Optional[Callable[[float, str], None]] = None,
) -> str:
    """Mosaic ``paths`` into a single tiled BigTIFF at ``output_path``.

    Frames are merged band-by-band with :func:`rasterio.merge.merge`
    using ``method="first"`` (first-write-wins). The output is
    ``float32`` with ``NaN`` as NoData. ``progress`` is an optional
    ``callable(fraction, message)`` invoked once per band; ``None``
    disables progress reporting. Returns ``output_path``.
    """
    info = validate_inputs(paths)
    xres, yres = info["res"]
    band_count = info["count"]
    src_nodata = info["nodata"]

    # Union of all frame bounds.
    lefts, bottoms, rights, tops = [], [], [], []
    for p in paths:
        with rasterio.open(p) as src:
            b = src.bounds
            lefts.append(b.left)
            bottoms.append(b.bottom)
            rights.append(b.right)
            tops.append(b.top)
    left, bottom = min(lefts), min(bottoms)
    right, top = max(rights), max(tops)

    width = int(round((right - left) / xres))
    height = int(round((top - bottom) / yres))
    out_transform = from_origin(left, top, xres, yres)
    out_bounds = (left, bottom, right, top)

    # Build output profile from the first frame, then override.
    with rasterio.open(paths[0]) as first:
        profile = first.profile.copy()
        descriptions = first.descriptions

    for k in ("predictor", "photometric"):
        profile.pop(k, None)
    profile.update(
        driver="GTiff",
        dtype="float32",
        nodata=float("nan"),
        count=band_count,
        width=width,
        height=height,
        transform=out_transform,
        crs=info["crs"],
        tiled=True,
        blockxsize=512,
        blockysize=512,
        compress="deflate",
        BIGTIFF="YES",
    )

    nan = np.float32("nan")
    nodata_is_nan = (
        isinstance(src_nodata, float) and np.isnan(src_nodata)
    )

    with rasterio.open(output_path, "w", **profile) as dst:
        for b in range(1, band_count + 1):
            sources = [rasterio.open(p) for p in paths]
            try:
                arr, _ = merge(
                    sources,
                    indexes=[b],
                    method="first",
                    dtype="float32",
                    nodata=nan,
                    res=(xres, yres),
                    bounds=out_bounds,
                )
            finally:
                for s in sources:
                    s.close()

            if arr.dtype != np.float32:
                arr = arr.astype(np.float32)
            if src_nodata is not None and not nodata_is_nan:
                arr[arr == np.float32(src_nodata)] = nan

            dst.write(arr[0], b)

            if progress is not None:
                progress(b / band_count, "band {}/{}".format(b, band_count))

        if descriptions:
            for i, desc in enumerate(descriptions, start=1):
                if desc:
                    dst.set_band_description(i, desc)

    return output_path
