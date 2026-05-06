# -*- coding: utf-8 -*-
"""Stage A of the hyperspectral pipeline (see ``hyperspectral_plan.md``).

Reject obviously-bad PIKA-L frames before mosaicking. Each frame is a
georeferenced GeoTIFF; NoData is taken from ``src.nodata``. All frames are
assumed to share CRS and pixel size — that invariant is checked in Stage B,
not here.

Public API:
    * :class:`FilterThresholds` — bundle of tunable thresholds.
    * :func:`is_bad_frame` — single-frame heuristic check.
    * :func:`filter_frames` — batch wrapper that derives the median
      footprint area and dispatches to :func:`is_bad_frame`.

Dependencies are limited to ``rasterio``, ``numpy`` and the Python stdlib;
in particular this module must not import ``osgeo.gdal`` or ``qgis``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
import rasterio
from rasterio.windows import Window


# ---------------------------------------------------------------------------
# Thresholds (tuned for PIKA-L frames; see hyperspectral_plan.md, Stage A).
# Kept as module-level constants so they remain the documented defaults.
# ---------------------------------------------------------------------------

SKEW_MAX: float = 0.05
AREA_LO: float = 0.5
AREA_HI: float = 2.0
ASPECT_MAX: float = 4.0
CENTRE_WINDOW: int = 64
MIN_VALID_FRACTION: float = 0.5
STD_MIN: float = 0.005
SATURATION_FRACTION: float = 0.95


@dataclass
class FilterThresholds:
    """Tunable thresholds for :func:`is_bad_frame` / :func:`filter_frames`."""

    skew_max: float = SKEW_MAX
    area_lo: float = AREA_LO
    area_hi: float = AREA_HI
    aspect_max: float = ASPECT_MAX
    centre_window: int = CENTRE_WINDOW
    min_valid_fraction: float = MIN_VALID_FRACTION
    std_min: float = STD_MIN
    saturation_fraction: float = SATURATION_FRACTION


# ---------------------------------------------------------------------------
# Threshold presets (Pipeline TO-DO #13 in ``hyperspectral_plan.md``).
#
# Three named bundles let junior users pick a behaviour instead of tuning
# the eight raw knobs by hand. ``"default"`` matches the historical
# defaults (``FilterThresholds()``) so preserving behaviour is just
# selecting the default preset. ``"permissive"`` relaxes every threshold
# so almost nothing is rejected (useful when the v1 heuristic over-rejects
# on a new sensor); ``"strict"`` tightens them for clean acquisitions
# where any doubt should drop the frame.
# ---------------------------------------------------------------------------

THRESHOLD_PRESETS: dict[str, FilterThresholds] = {
    "permissive": FilterThresholds(
        skew_max=0.10,
        area_lo=0.25,
        area_hi=4.0,
        aspect_max=8.0,
        centre_window=CENTRE_WINDOW,
        min_valid_fraction=0.20,
        std_min=0.001,
        saturation_fraction=0.99,
    ),
    "default": FilterThresholds(),
    "strict": FilterThresholds(
        skew_max=0.02,
        area_lo=0.75,
        area_hi=1.5,
        aspect_max=2.5,
        centre_window=CENTRE_WINDOW,
        min_valid_fraction=0.75,
        std_min=0.01,
        saturation_fraction=0.85,
    ),
}


def preset_thresholds(name: str) -> FilterThresholds:
    """Return the :class:`FilterThresholds` bundle for a preset name.

    ``name`` is matched case-insensitively against
    :data:`THRESHOLD_PRESETS` (``"permissive"`` / ``"default"`` /
    ``"strict"``). Raises ``KeyError`` for any other name so callers
    fail loudly instead of silently using the default.
    """
    key = name.strip().lower()
    if key not in THRESHOLD_PRESETS:
        raise KeyError(
            "unknown threshold preset {!r}; expected one of {}".format(
                name, sorted(THRESHOLD_PRESETS)))
    return THRESHOLD_PRESETS[key]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_bad_frame(
    path: str,
    *,
    median_area: float | None = None,
    thresholds: FilterThresholds | None = None,
) -> tuple[bool, str]:
    """Return ``(is_bad, reason)``; ``reason`` is empty when the frame is good.

    Heuristics are applied in order and the first failure short-circuits:
    affine skew, footprint area (only when ``median_area`` is given),
    aspect ratio, then a low-variance / saturated centre check on band 1.
    """
    th = thresholds if thresholds is not None else FilterThresholds()

    with rasterio.open(path) as src:
        t = src.transform
        a, b, d, e = abs(t.a), abs(t.b), abs(t.d), abs(t.e)
        width, height = src.width, src.height
        nodata = src.nodata
        dtype = np.dtype(src.dtypes[0])

        # 1. Affine skew / rotation.
        denom = max(a, e)
        skew = max(b, d) / denom if denom > 0 else float("inf")
        if skew > th.skew_max:
            return True, "skewed transform (skew={:.3f} > {:.3f})".format(
                skew, th.skew_max)

        # 2. Footprint area (CRS units squared).
        area = width * height * a * e
        if median_area is not None:
            lo = th.area_lo * median_area
            hi = th.area_hi * median_area
            if area < lo or area > hi:
                return True, (
                    "abnormal area (area={:.3g}, allowed=[{:.3g}, {:.3g}])"
                    .format(area, lo, hi)
                )

        # 3. Aspect ratio.
        long_side = max(width, height)
        short_side = min(width, height)
        ar = long_side / short_side if short_side > 0 else float("inf")
        if ar > th.aspect_max:
            return True, "abnormal aspect ratio (ar={:.2f} > {:.2f})".format(
                ar, th.aspect_max)

        # 4. Centre window of band 1.
        win_w = min(th.centre_window, width)
        win_h = min(th.centre_window, height)
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
        valid_fraction = (valid_count / total) if total > 0 else 0.0
        if total == 0 or valid_fraction < th.min_valid_fraction:
            return True, (
                "mostly nodata in centre (valid={:.2f} < {:.2f})"
                .format(valid_fraction, th.min_valid_fraction)
            )

        valid_pixels = centre[valid_mask]

        # Saturation check (integer dtypes only).
        if np.issubdtype(dtype, np.integer):
            dtype_max = np.iinfo(dtype).max
            sat_fraction = float((valid_pixels == dtype_max).mean())
            if sat_fraction > th.saturation_fraction:
                return True, "saturated centre (sat={:.2f} > {:.2f})".format(
                    sat_fraction, th.saturation_fraction)

        std = float(np.std(valid_pixels.astype(np.float64)))
        if std < th.std_min:
            return True, "low variance centre (std={:.2f} < {:.2f})".format(
                std, th.std_min)

    return False, ""


def filter_frames(
    paths: list[str],
    *,
    thresholds: FilterThresholds | None = None,
    is_canceled: Optional[Callable[[], bool]] = None,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Split ``paths`` into ``(good_paths, rejected)``.

    ``rejected`` holds ``(path, reason)`` pairs. The median footprint area
    across all input frames is computed first and then passed to
    :func:`is_bad_frame` so that area outliers can be detected. Input
    order is preserved in both returned lists.

    ``is_canceled`` is an optional zero-arg predicate consulted **between
    frames** in both passes (Pipeline TO-DO #11 in
    ``hyperspectral_plan.md``). When it returns ``True`` the function
    aborts immediately by raising ``RuntimeError("canceled")`` so a
    multi-minute Stage A run can be stopped cleanly from the QGIS dialog.
    Default ``None`` keeps the previous uncancellable behaviour.
    """
    th = thresholds if thresholds is not None else FilterThresholds()

    # First pass: collect each frame's footprint area in CRS units squared.
    areas: list[float] = []
    for p in paths:
        if is_canceled is not None and is_canceled():
            raise RuntimeError("canceled")
        with rasterio.open(p) as src:
            t = src.transform
            areas.append(src.width * src.height * abs(t.a) * abs(t.e))

    median_area = float(np.median(areas)) if areas else None

    good: list[str] = []
    rejected: list[tuple[str, str]] = []
    for p in paths:
        if is_canceled is not None and is_canceled():
            raise RuntimeError("canceled")
        bad, reason = is_bad_frame(
            p, median_area=median_area, thresholds=th)
        if bad:
            rejected.append((p, reason))
        else:
            good.append(p)
    return good, rejected
