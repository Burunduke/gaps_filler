# -*- coding: utf-8 -*-
"""Stage A of the hyperspectral pipeline (see ``hyperspectral_plan.md``).

Reject obviously-bad PIKA-L frames before mosaicking. Each frame is a
georeferenced GeoTIFF; NoData is taken from ``src.nodata``. All frames are
assumed to share CRS and pixel size — that invariant is checked in Stage B,
not here.

Public API:
    * :func:`is_bad_frame` — single-frame heuristic check.
    * :func:`filter_frames` — batch wrapper that derives the median
      footprint area and dispatches to :func:`is_bad_frame`.

Dependencies are limited to ``rasterio``, ``numpy`` and the Python stdlib;
in particular this module must not import ``osgeo.gdal`` or ``qgis``.
"""

from __future__ import annotations

import numpy as np
import rasterio
from rasterio.windows import Window


# ---------------------------------------------------------------------------
# Thresholds (tuned for PIKA-L frames; see hyperspectral_plan.md, Stage A).
# ---------------------------------------------------------------------------

SKEW_MAX: float = 0.05
AREA_LO: float = 0.5
AREA_HI: float = 2.0
ASPECT_MAX: float = 2.0
CENTRE_WINDOW: int = 64
MIN_VALID_FRACTION: float = 0.5
STD_MIN: float = 1.0
SATURATION_FRACTION: float = 0.95


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_bad_frame(
    path: str,
    *,
    median_area: float | None = None,
) -> tuple[bool, str]:
    """Return ``(is_bad, reason)``; ``reason`` is empty when the frame is good.

    Heuristics are applied in order and the first failure short-circuits:
    affine skew, footprint area (only when ``median_area`` is given),
    aspect ratio, then a low-variance / saturated centre check on band 1.
    """
    with rasterio.open(path) as src:
        t = src.transform
        a, b, d, e = abs(t.a), abs(t.b), abs(t.d), abs(t.e)
        width, height = src.width, src.height
        nodata = src.nodata
        dtype = np.dtype(src.dtypes[0])

        # 1. Affine skew / rotation.
        denom = max(a, e)
        skew = max(b, d) / denom if denom > 0 else float("inf")
        if skew > SKEW_MAX:
            return True, "skewed transform (skew={:.3f})".format(skew)

        # 2. Footprint area (CRS units squared).
        area = width * height * a * e
        if median_area is not None:
            lo = AREA_LO * median_area
            hi = AREA_HI * median_area
            if area < lo or area > hi:
                return True, (
                    "abnormal area (area={:.3g}, median={:.3g})"
                    .format(area, median_area)
                )

        # 3. Aspect ratio.
        long_side = max(width, height)
        short_side = min(width, height)
        ar = long_side / short_side if short_side > 0 else float("inf")
        if ar > ASPECT_MAX:
            return True, "abnormal aspect ratio (ar={:.2f})".format(ar)

        # 4. Centre 64x64 window of band 1.
        win_w = min(CENTRE_WINDOW, width)
        win_h = min(CENTRE_WINDOW, height)
        col_off = (width - win_w) // 2
        row_off = (height - win_h) // 2
        window = Window(col_off, row_off, win_w, win_h)
        centre = src.read(1, window=window)

        if nodata is not None:
            if isinstance(nodata, float) and np.isnan(nodata):
                valid_mask = ~np.isnan(centre.astype(np.float64))
            else:
                valid_mask = centre != nodata
                if np.issubdtype(dtype, np.floating):
                    valid_mask &= ~np.isnan(centre)
        else:
            valid_mask = np.ones(centre.shape, dtype=bool)

        total = valid_mask.size
        valid_count = int(valid_mask.sum())
        if total == 0 or (valid_count / total) < MIN_VALID_FRACTION:
            return True, "mostly nodata in centre"

        valid_pixels = centre[valid_mask]

        # Saturation check (integer dtypes only).
        if np.issubdtype(dtype, np.integer):
            dtype_max = np.iinfo(dtype).max
            sat_fraction = float((valid_pixels == dtype_max).mean())
            if sat_fraction > SATURATION_FRACTION:
                return True, "saturated centre"

        std = float(np.std(valid_pixels.astype(np.float64)))
        if std < STD_MIN:
            return True, "low variance centre (std={:.2f})".format(std)

    return False, ""


def filter_frames(
    paths: list[str],
) -> tuple[list[str], list[tuple[str, str]]]:
    """Split ``paths`` into ``(good_paths, rejected)``.

    ``rejected`` holds ``(path, reason)`` pairs. The median footprint area
    across all input frames is computed first and then passed to
    :func:`is_bad_frame` so that area outliers can be detected. Input
    order is preserved in both returned lists.
    """
    # First pass: collect each frame's footprint area in CRS units squared.
    areas: list[float] = []
    for p in paths:
        with rasterio.open(p) as src:
            t = src.transform
            areas.append(src.width * src.height * abs(t.a) * abs(t.e))

    median_area = float(np.median(areas)) if areas else None

    good: list[str] = []
    rejected: list[tuple[str, str]] = []
    for p in paths:
        bad, reason = is_bad_frame(p, median_area=median_area)
        if bad:
            rejected.append((p, reason))
        else:
            good.append(p)
    return good, rejected
