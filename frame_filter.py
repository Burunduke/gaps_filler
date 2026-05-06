# -*- coding: utf-8 -*-
"""Stage A of the hyperspectral pipeline.

Reject obviously-bad PIKA-L frames before mosaicking. Each frame is a
georeferenced GeoTIFF; NoData is taken from ``src.nodata``. All frames are
assumed to share CRS and pixel size — that invariant is checked in Stage B,
not here.

The actual rejection heuristic is **method-dependent**: the active
filter method is selected at the call site via
:data:`methods.FRAME_FILTER_METHODS` (e.g. ``v1`` hard thresholds,
``v2`` per-flight adaptive MAD, ``v3`` per-band striping / dropout).

Public API (one batch entry per registered method, plus shared helpers):
    * :class:`FilterThresholds` — bundle of tunable thresholds (v1).
    * :func:`is_bad_frame` — single-frame v1 heuristic check.
    * :func:`filter_frames` — v1 batch wrapper that derives the median
      footprint area and dispatches to :func:`is_bad_frame`.
    * :func:`filter_frames_adaptive_mad` — v2 batch wrapper (per-flight
      MAD around the median footprint area).
    * :func:`filter_frames_per_band` — v3 batch wrapper (per-band
      striping ratio + dropout fraction).

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

# Roadmap item #3 (v3 per-band) defaults. Kept as module-level constants
# so QGIS dialogs and the pipeline can import them as the documented
# defaults (same convention as the eight v1 thresholds above).
MAX_DROPOUT_FRAC: float = 0.30
MAX_STRIPE_RATIO: float = 0.5


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


# ---------------------------------------------------------------------------
# Filter v2 — adaptive MAD thresholds (roadmap item #2).
#
# Instead of comparing each frame to fixed v1 hard thresholds, this
# version derives the rejection band from the dataset itself: it takes
# the same per-frame footprint area metric the v1 first pass already
# computes and rejects frames whose area deviates from the dataset
# median by more than ``k_mad`` scaled MADs. Two-sided because a frame
# can be a footprint outlier on either side (very small = partial
# acquisition; very large = stitched / wrong-altitude).
# ---------------------------------------------------------------------------
def filter_frames_adaptive_mad(
    paths: list[str],
    *,
    thresholds: FilterThresholds | None = None,  # accepted for API parity
    is_canceled: Optional[Callable[[], bool]] = None,
    k_mad: float = 3.0,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Adaptive-MAD variant of :func:`filter_frames`.

    Reuses the same per-frame footprint-area metric the v1 first pass
    already extracts. Computes ``median`` and scaled MAD
    (``1.4826 * median(|area - median|)``) across the dataset, then
    rejects frames whose area deviates from the median by more than
    ``k_mad`` scaled MADs (two-sided).

    ``thresholds`` is accepted to keep the registry-dispatch signature
    identical to v1; it is **not used** by the adaptive method (the
    point of v2 is to avoid hand-tuned thresholds).

    Edge case: when ``MAD == 0`` (every frame has the same area, e.g.
    a tiny test set) the spread is undefined, so we keep every frame
    rather than rejecting all of them.
    """
    del thresholds  # intentionally unused; v2 derives its own band

    # Single pass: read each frame's footprint area in CRS units squared.
    # Same shape as the v1 first pass -- no extra IO beyond that.
    areas: list[float] = []
    for p in paths:
        if is_canceled is not None and is_canceled():
            raise RuntimeError("canceled")
        with rasterio.open(p) as src:
            t = src.transform
            areas.append(src.width * src.height * abs(t.a) * abs(t.e))

    good: list[str] = []
    rejected: list[tuple[str, str]] = []
    if not areas:
        return good, rejected

    arr = np.asarray(areas, dtype=np.float64)
    median = float(np.median(arr))
    mad = float(np.median(np.abs(arr - median)))
    scaled_mad = 1.4826 * mad  # so k_mad is in σ-units for normal data

    # MAD == 0 means all frames are identical on this metric -- there is
    # no robust spread to threshold against. Keep every frame; this is
    # safer than rejecting all of them and matches "fall back to keeping
    # all frames" in the scope.
    if scaled_mad <= 0.0:
        return list(paths), rejected

    lo = median - k_mad * scaled_mad
    hi = median + k_mad * scaled_mad
    for p, area in zip(paths, areas):
        if is_canceled is not None and is_canceled():
            raise RuntimeError("canceled")
        if area < lo or area > hi:
            # Match the v1 "(key=measured, allowed=[lo, hi])" reason
            # shape so pipeline._parse_reason picks up the numbers for
            # the rejected-frames CSV without changes.
            rejected.append((p, (
                "area outlier vs MAD band (area={:.3g}, "
                "allowed=[{:.3g}, {:.3g}])"
            ).format(area, lo, hi)))
        else:
            good.append(p)
    return good, rejected


# ---------------------------------------------------------------------------
# Filter v3 -- per-band striping / dropout detection (roadmap item #3).
#
# Catches sensor row glitches and dead bands the centre-window v1 check
# misses. For each band we compute two simple, per-band quality signals:
#
# * dropout fraction -- share of pixels equal to NoData / NaN / 0 /
#   the integer dtype max within the valid footprint mask. A band that
#   is mostly zero or saturated is dropped.
# * striping indicator -- ratio of column-mean variance to overall
#   variance across valid pixels. The reasoning: clean imagery has
#   per-column means that fluctuate randomly, so var(col_means) is a
#   small fraction of the per-pixel variance (overall var also includes
#   within-column variance). When sensor columns drift consistently
#   (striping), the column-means carry most of the variance and the
#   ratio approaches 1.0. Single, robust, no extra dependencies.
#
# Memory: each band is read as a 2D array, statistics are computed,
# the array is released before reading the next band -- no full-cube
# load. ``is_canceled`` is honoured between frames.
# ---------------------------------------------------------------------------
def filter_frames_per_band(
    paths: list[str],
    *,
    thresholds: FilterThresholds | None = None,  # accepted for API parity
    is_canceled: Optional[Callable[[], bool]] = None,
    max_dropout_frac: float = MAX_DROPOUT_FRAC,
    max_stripe_ratio: float = MAX_STRIPE_RATIO,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Per-band striping / dropout variant of :func:`filter_frames`.

    A frame is rejected when ANY band has a dropout fraction above
    ``max_dropout_frac`` or a striping indicator above
    ``max_stripe_ratio`` (values closer to 1.0 mean more striping).

    ``thresholds`` is accepted to keep the registry-dispatch signature
    identical to v1 / v2; it is **not used** by the per-band method.
    """
    del thresholds  # intentionally unused; v3 has its own knobs

    good: list[str] = []
    rejected: list[tuple[str, str]] = []

    for p in paths:
        if is_canceled is not None and is_canceled():
            raise RuntimeError("canceled")

        reason = _per_band_reject_reason(
            p,
            max_dropout_frac=max_dropout_frac,
            max_stripe_ratio=max_stripe_ratio,
        )
        if reason:
            rejected.append((p, reason))
        else:
            good.append(p)

    return good, rejected


def _per_band_reject_reason(
    path: str,
    *,
    max_dropout_frac: float,
    max_stripe_ratio: float,
) -> str:
    """Return the reject reason for a single frame, or "" when good.

    Iterates over bands one at a time, computing per-band dropout
    fraction and striping ratio. Short-circuits on the first failing
    band so we do not pay for reading the remaining bands.
    """
    with rasterio.open(path) as src:
        nodata = src.nodata
        band_count = int(src.count)
        for b in range(1, band_count + 1):
            arr = src.read(b)
            dtype = arr.dtype

            # Build a "valid footprint" mask (excludes NoData / NaN).
            if nodata is not None:
                if isinstance(nodata, float) and np.isnan(nodata):
                    valid_mask = ~np.isnan(arr.astype(np.float64))
                else:
                    valid_mask = arr != nodata
                    if np.issubdtype(dtype, np.floating):
                        valid_mask &= ~np.isnan(arr)
            else:
                if np.issubdtype(dtype, np.floating):
                    valid_mask = ~np.isnan(arr)
                else:
                    valid_mask = np.ones(arr.shape, dtype=bool)

            valid_count = int(valid_mask.sum())

            # Edge case: empty footprint -> treat as 100% dropout and
            # skip the striping calc to avoid div-by-zero. The frame is
            # rejected on this band immediately.
            if valid_count == 0:
                return (
                    "band {} dropout (frac={:.2f}, allowed=[0, {:.2f}])"
                    .format(b, 1.0, max_dropout_frac)
                )

            # Dropout: pixels equal to 0 or to the dtype max (saturated)
            # within the valid footprint count as "lost". NoData is
            # excluded from the denominator -- we only judge the
            # within-swath pixels.
            sat_value = (np.iinfo(dtype).max
                         if np.issubdtype(dtype, np.integer) else None)
            inside = arr[valid_mask]
            zero_or_sat = (inside == 0)
            if sat_value is not None:
                zero_or_sat |= (inside == sat_value)
            dropout_frac = float(zero_or_sat.mean())
            if dropout_frac > max_dropout_frac:
                return (
                    "band {} dropout (frac={:.2f}, allowed=[0, {:.2f}])"
                    .format(b, dropout_frac, max_dropout_frac)
                )

            # Striping: var(column means) / var(all valid pixels).
            # NoData pixels are zeroed in a float32 working copy and
            # excluded from the per-column counts so empty columns do
            # not bias the column means.
            work = np.where(valid_mask, arr, 0).astype(np.float32, copy=False)
            col_counts = valid_mask.sum(axis=0)  # per-column valid count
            # Avoid div-by-zero on fully-empty columns: replace 0 with 1
            # in the denominator, then drop those columns from the mean
            # statistics by masking them out.
            safe_counts = np.where(col_counts > 0, col_counts, 1)
            col_means = work.sum(axis=0) / safe_counts
            non_empty = col_counts > 0
            if non_empty.sum() < 2:
                # Not enough columns to compute a meaningful ratio --
                # treat as no striping rather than NaN.
                continue
            col_means = col_means[non_empty]

            inside_f = inside.astype(np.float32, copy=False)
            overall_var = float(np.var(inside_f))
            if overall_var <= 0.0:
                # Constant band within the valid footprint = full dropout
                # of information; rejecting via the dropout path was
                # already handled when zero/saturated, so a tiny non-zero
                # constant slips through here. Flag it as 100% striping.
                return (
                    "band {} striping (ratio={:.2f}, allowed=[0, {:.2f}])"
                    .format(b, 1.0, max_stripe_ratio)
                )
            col_var = float(np.var(col_means))
            stripe_ratio = col_var / overall_var
            if stripe_ratio > max_stripe_ratio:
                return (
                    "band {} striping (ratio={:.2f}, allowed=[0, {:.2f}])"
                    .format(b, stripe_ratio, max_stripe_ratio)
                )

            # Release this band's array before reading the next band so
            # peak memory stays at one band, not the full cube.
            del arr, work, inside, inside_f, valid_mask

    return ""
