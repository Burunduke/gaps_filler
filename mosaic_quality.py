# -*- coding: utf-8 -*-
"""Pixel-wise quality metrics between a reference orthophoto and a mosaic.

Pure module — no Qt / no QGIS imports — so it can be reused from any
context. GDAL is used only for raster I/O, exactly as the rest of the
plugin does. The heavy lifting happens in :func:`compare_rasters`,
which loops band-by-band (consistent with the per-band loop in
:mod:`fill_nodata`) and returns a dict of per-band and aggregate
metrics.

Metrics computed per band (over the intersection of validity masks):

- ``rmse``  -- root mean squared error.
- ``mae``   -- mean absolute error.
- ``psnr``  -- peak signal-to-noise ratio, computed from MSE using the
  data range ``max - min`` of the *reference* band over valid pixels.
- ``ssim``  -- structural similarity index, via
  :func:`skimage.metrics.structural_similarity` (scikit-image required).

Aggregates across bands for each band-level metric: ``MEAN_<M>``,
``WORST_<M>`` (max for lower-is-better, min for higher-is-better) and
``P05_<M>`` ("5% of bands are at least this bad" — uses
``np.percentile(values, 5)`` for higher-is-better metrics and
``np.percentile(values, 95)`` for lower-is-better, so the polarity
convention stays consistent regardless of metric direction).

Whole-cube metric:

- ``sam`` / ``sam_deg`` -- Spectral Angle Mapper (lower is better),
  the mean angle (in radians / degrees) between reference and mosaic
  pixel spectra over valid pixels. Captures spectral fidelity that
  RMSE / PSNR / SSIM do not directly measure.

NoData handling: pixels marked nodata in **either** the reference or
the mosaic are excluded from every metric (mask intersection). For
SAM a pixel must be valid in **every** band.

Grid alignment: the mosaic typically covers a smaller extent than the
reference orthophoto. To compare only the overlapping region, the
reference is warped (in memory) onto the mosaic's exact grid — same
extent, same pixel size, same projection — before per-band metrics are
computed. After warping the two arrays share shape by construction, so
the old "size mismatch" failure cannot occur.
"""

import math

import numpy as np
from osgeo import gdal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open(path):
    ds = gdal.Open(path, gdal.GA_ReadOnly)
    if ds is None:
        raise IOError("Cannot open raster: {}".format(path))
    return ds


def _mosaic_bounds(mos):
    """Return (minX, minY, maxX, maxY) in mosaic CRS units.

    Assumes a north-up geotransform (pixel height is negative), which
    is what every raster produced by this plugin uses.
    """
    gt = mos.GetGeoTransform()
    min_x = gt[0]
    max_y = gt[3]
    max_x = min_x + gt[1] * mos.RasterXSize
    min_y = max_y + gt[5] * mos.RasterYSize
    return (min_x, min_y, max_x, max_y)


def _align_reference_to_mosaic(ref, mos, feedback=None):
    """Warp ``ref`` onto the mosaic's exact grid (in-memory dataset).

    The result has the mosaic's extent, pixel size and projection, so
    the per-band arrays we read from it are guaranteed to match the
    mosaic's arrays element-wise. NoData of the source reference is
    preserved so the existing mask logic still works.
    """
    bounds = _mosaic_bounds(mos)
    gt = mos.GetGeoTransform()
    px_w = gt[1]
    px_h = abs(gt[5])
    dst_srs = mos.GetProjection() or None

    warp_opts = gdal.WarpOptions(
        format="MEM",
        outputBounds=bounds,            # (minX, minY, maxX, maxY)
        xRes=px_w,
        yRes=px_h,
        targetAlignedPixels=False,
        dstSRS=dst_srs,
        resampleAlg="near",             # keep pixel values intact
        multithread=False,
    )
    if feedback is not None:
        feedback.pushInfo(
            "Aligning reference to mosaic grid "
            "({}x{} px, bounds={})".format(
                mos.RasterXSize, mos.RasterYSize, bounds))
    aligned = gdal.Warp("", ref, options=warp_opts)
    if aligned is None:
        raise RuntimeError(
            "Failed to align reference raster to mosaic grid (gdal.Warp "
            "returned None).")

    # Defensive: shapes must match the mosaic exactly.
    if (aligned.RasterXSize, aligned.RasterYSize) != \
            (mos.RasterXSize, mos.RasterYSize):
        raise RuntimeError(
            "Reference alignment produced wrong size: "
            "got {}x{}, expected {}x{}".format(
                aligned.RasterXSize, aligned.RasterYSize,
                mos.RasterXSize, mos.RasterYSize))
    return aligned


def _nodata_mask(arr, nodata):
    """Return boolean mask where pixels are nodata (True = nodata).

    NaNs are always treated as nodata, matching the rest of the plugin.
    """
    m = np.isnan(arr) if np.issubdtype(arr.dtype, np.floating) else \
        np.zeros(arr.shape, dtype=bool)
    if nodata is not None and not (isinstance(nodata, float) and math.isnan(nodata)):
        m = m | (arr == nodata)
    return m


def _ssim_or_raise(ref_valid_2d, mos_valid_2d, data_range):
    """Wrap the scikit-image SSIM call so the import error is friendly.

    SSIM is a 2D operation, but we may have masked out chunks of the
    image. We pass full 2D arrays (with invalid pixels set to the same
    constant in both) so SSIM remains well-defined; this is a pragmatic
    compromise that scikit-image itself does not handle natively.
    """
    try:
        from skimage.metrics import structural_similarity as ssim
    except ImportError as exc:
        raise RuntimeError(
            "scikit-image is required for SSIM. Install it with "
            "`pip install scikit-image` (or via your QGIS Python "
            "environment)."
        ) from exc
    return float(ssim(ref_valid_2d, mos_valid_2d, data_range=data_range))


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

# Lower-is-better metrics flip the WORST/P05 reduction direction.
_LOWER_IS_BETTER = {"rmse", "mae"}

# Numerical guard for SAM's denominator (||p|| * ||q||).
_SAM_EPS = 1e-12


def _aggregate_band_metric(values, key, bands=None):
    """Return ``(mean, worst, p05, worst_band, p05_band)`` for a metric.

    - ``WORST`` = max for lower-is-better metrics (RMSE, MAE), min for
      higher-is-better metrics (PSNR, SSIM).
    - ``P05`` always means "5% of bands are at least this bad"
      regardless of polarity:
        * higher-is-better → ``np.percentile(values, 5)`` (near-worst
          small value);
        * lower-is-better  → ``np.percentile(values, 95)`` (near-worst
          large value).
    - ``worst_band`` is the 1-based band index that produced WORST
      (``np.argmax`` / ``np.argmin``; on ties numpy returns the first
      occurrence, so the lowest-indexed tying band wins → deterministic).
    - ``p05_band`` is the 1-based band index whose value is closest to
      ``P05`` (``np.argmin(abs(values - p05))``; first occurrence on ties).

    ``bands`` is the parallel list of 1-based band indices for
    ``values``; if omitted we fall back to ``1..len(values)`` (which is
    only correct when no bands were skipped).

    With all-equal inputs ``MEAN == WORST == P05`` exactly, by
    construction (numpy.percentile of a constant array returns that
    constant).
    """
    if not values:
        nan = float("nan")
        return nan, nan, nan, 0, 0
    arr = np.asarray(values, dtype=np.float64)
    if bands is None:
        bands_arr = np.arange(1, len(values) + 1, dtype=int)
    else:
        bands_arr = np.asarray(bands, dtype=int)
    mean_v = float(arr.mean())
    if key in _LOWER_IS_BETTER:
        worst_v = float(arr.max())
        worst_idx = int(np.argmax(arr))
        p05_v = float(np.percentile(arr, 95))
    else:
        worst_v = float(arr.min())
        worst_idx = int(np.argmin(arr))
        p05_v = float(np.percentile(arr, 5))
    p05_idx = int(np.argmin(np.abs(arr - p05_v)))
    worst_band = int(bands_arr[worst_idx])
    p05_band = int(bands_arr[p05_idx])
    return mean_v, worst_v, p05_v, worst_band, p05_band


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compare_rasters(reference_path, mosaic_path, feedback=None):
    """Compute per-band RMSE / MAE / PSNR / SSIM plus aggregates and SAM.

    Parameters
    ----------
    reference_path : str
        Path to the reference orthophoto (GDAL-readable).
    mosaic_path : str
        Path to the built mosaic (GDAL-readable).
    feedback : optional duck-typed ``QgsProcessingFeedback``
        Used for ``pushInfo`` / ``pushWarning`` / ``reportError``.
        May be ``None`` outside QGIS.

    Returns
    -------
    dict
        ``{
            "band_count": int,
            "per_band": [{"band": i, "rmse": .., "mae": .., "psnr": ..,
                          "ssim": .., "valid_pixels": int}, ...],
            "skipped_bands": [int, ...],
            "mean_<m>", "worst_<m>", "p05_<m>" for m in
                {rmse, mae, psnr, ssim},
            "sam": float,        # whole-cube SAM in radians, lower is better
            "sam_deg": float,    # same metric, in degrees
            "sam_valid_pixels": int,
        }``

    Raises
    ------
    IOError
        If a raster cannot be opened.
    ValueError
        If the two rasters disagree on band count.
    RuntimeError
        If reference alignment fails, or scikit-image is not installed
        (the latter only when SSIM is needed, i.e. there is at least
        one band with valid pixels).
    """
    ref_src = _open(reference_path)
    mos = _open(mosaic_path)

    # Align reference to the mosaic's grid so the two arrays are guaranteed
    # to have identical shape. The mosaic is the smaller raster (uneven
    # edges, possibly partial coverage); we keep it as-is and warp the
    # (typically larger) reference onto its exact extent + pixel grid.
    ref = _align_reference_to_mosaic(ref_src, mos, feedback=feedback)

    if ref.RasterCount != mos.RasterCount:
        msg = "Band count mismatch: ref={} mos={}".format(
            ref.RasterCount, mos.RasterCount)
        if feedback is not None:
            feedback.reportError(msg, fatalError=True)
        raise ValueError(msg)

    band_count = ref.RasterCount
    per_band = []
    skipped = []

    # SAM (Spectral Angle Mapper) is a whole-cube metric. To avoid
    # re-reading the rasters, accumulate per-pixel dot product and
    # squared norms inside the existing per-band loop. A pixel counts
    # for SAM only if it is valid in EVERY band (intersection across
    # bands of the same per-pixel valid mask the band-level metrics
    # already use), so we also AND a running validity mask.
    H = mos.RasterYSize
    W = mos.RasterXSize
    sam_dot = np.zeros((H, W), dtype=np.float64)
    sam_p_sq = np.zeros((H, W), dtype=np.float64)
    sam_q_sq = np.zeros((H, W), dtype=np.float64)
    sam_valid_all = np.ones((H, W), dtype=bool)

    for b in range(1, band_count + 1):
        if feedback is not None:
            feedback.pushInfo("Comparing band {}/{}".format(b, band_count))

        rb = ref.GetRasterBand(b)
        mb = mos.GetRasterBand(b)
        ref_arr = rb.ReadAsArray()
        mos_arr = mb.ReadAsArray()
        # Float64 keeps RMSE / MAE math exact even for int16 inputs
        # and avoids overflow on (ref - mos)**2 for wide dynamic ranges.
        ref_f = ref_arr.astype(np.float64, copy=False)
        mos_f = mos_arr.astype(np.float64, copy=False)

        ref_nd = rb.GetNoDataValue()
        mos_nd = mb.GetNoDataValue()
        valid = (~_nodata_mask(ref_arr, ref_nd)) & \
                (~_nodata_mask(mos_arr, mos_nd))
        # Also exclude any non-finite values (NaN/inf) so SAM math is
        # well-defined on every pixel that survives the AND below.
        valid = valid & np.isfinite(ref_f) & np.isfinite(mos_f)

        # Accumulate SAM components on the union of valid pixels in
        # this band; the cross-band intersection is applied at the end.
        sam_dot += np.where(valid, ref_f * mos_f, 0.0)
        sam_p_sq += np.where(valid, ref_f * ref_f, 0.0)
        sam_q_sq += np.where(valid, mos_f * mos_f, 0.0)
        sam_valid_all &= valid

        n = int(valid.sum())
        if n == 0:
            if feedback is not None:
                feedback.pushWarning(
                    "Band {}: no overlapping valid pixels — "
                    "skipping.".format(b))
            skipped.append(b)
            continue

        diff = ref_f[valid] - mos_f[valid]
        mse = float(np.mean(diff * diff))
        mae = float(np.mean(np.abs(diff)))
        rmse = math.sqrt(mse)

        # PSNR: data range from valid reference pixels.
        ref_valid = ref_f[valid]
        data_range = float(ref_valid.max() - ref_valid.min())
        if mse <= 0.0:
            psnr = float("inf")
            if feedback is not None:
                feedback.pushInfo(
                    "Band {}: MSE=0 (perfect match) -> PSNR=inf".format(b))
        elif data_range <= 0.0:
            # Constant reference band — PSNR is undefined; report inf
            # (signal has no dynamic range, any error is "infinite" relative).
            psnr = float("inf")
            if feedback is not None:
                feedback.pushWarning(
                    "Band {}: constant reference (range=0); "
                    "PSNR set to inf.".format(b))
        else:
            psnr = 20.0 * math.log10(data_range) - 10.0 * math.log10(mse)

        # SSIM needs 2D arrays. Set invalid pixels to a shared constant
        # in both images so they cancel and do not skew the structural
        # comparison. Using the reference mean keeps the constant inside
        # the data range.
        fill = float(ref_valid.mean())
        ref_for_ssim = np.where(valid, ref_f, fill).astype(np.float64)
        mos_for_ssim = np.where(valid, mos_f, fill).astype(np.float64)
        ssim_range = data_range if data_range > 0 else 1.0
        ssim_val = _ssim_or_raise(ref_for_ssim, mos_for_ssim, ssim_range)

        per_band.append({
            "band": b,
            "rmse": rmse,
            "mae": mae,
            "psnr": psnr,
            "ssim": ssim_val,
            "valid_pixels": n,
        })

    # ----- Per-band aggregates (mean / worst / p05) ------------------------
    summary = {
        "band_count": band_count,
        "per_band": per_band,
        "skipped_bands": skipped,
    }
    # Parallel list of 1-based band indices for the values above; this
    # matters when some bands were skipped (so list-position != band #).
    band_idxs = [m["band"] for m in per_band]
    for key in ("rmse", "mae", "psnr", "ssim"):
        vals = [m[key] for m in per_band]
        mean_v, worst_v, p05_v, worst_b, p05_b = _aggregate_band_metric(
            vals, key, bands=band_idxs)
        summary["mean_" + key] = mean_v
        summary["worst_" + key] = worst_v
        summary["p05_" + key] = p05_v
        summary["worst_" + key + "_band"] = worst_b
        summary["p05_" + key + "_band"] = p05_b

    # ----- Whole-cube SAM --------------------------------------------------
    # angle(p, q) = arccos( clip( dot(p, q) / (||p|| * ||q|| + eps),
    #                              -1, 1 ) )
    # Only pixels valid in every band, with non-zero norms in both p and q.
    p_norm = np.sqrt(sam_p_sq)
    q_norm = np.sqrt(sam_q_sq)
    sam_pixel_valid = sam_valid_all & (p_norm > 0.0) & (q_norm > 0.0)
    n_sam = int(sam_pixel_valid.sum())
    if n_sam > 0:
        denom = p_norm[sam_pixel_valid] * q_norm[sam_pixel_valid] + _SAM_EPS
        cos_theta = sam_dot[sam_pixel_valid] / denom
        cos_theta = np.clip(cos_theta, -1.0, 1.0)
        angles = np.arccos(cos_theta)
        sam_rad = float(np.mean(angles))
    else:
        sam_rad = float("nan")
        if feedback is not None:
            feedback.pushWarning(
                "SAM: no pixel is valid in every band — SAM set to NaN.")
    summary["sam"] = sam_rad
    summary["sam_deg"] = float(math.degrees(sam_rad)) if n_sam > 0 \
        else float("nan")
    summary["sam_valid_pixels"] = n_sam

    return summary


def format_report(summary):
    """Render the result of :func:`compare_rasters` as a plain-text table.

    Returned string is multi-line and ready for ``feedback.pushInfo``.
    Aggregate row order, per metric: ``MEAN_<M>``, ``WORST_<M>``,
    ``P05_<M>``. Final two rows: ``SAM`` (radians) and ``SAM_DEG``
    (degrees).
    """
    lines = []
    lines.append(
        "Band |       RMSE |        MAE |       PSNR |       SSIM |   Valid px")
    lines.append(
        "-----+------------+------------+------------+------------+-----------")
    for m in summary["per_band"]:
        lines.append(
            "{band:>4} | {rmse:>10.4f} | {mae:>10.4f} | {psnr:>10.4f} | "
            "{ssim:>10.4f} | {n:>9d}".format(
                band=m["band"], rmse=m["rmse"], mae=m["mae"],
                psnr=m["psnr"], ssim=m["ssim"], n=m["valid_pixels"]))
    if summary["skipped_bands"]:
        lines.append("Skipped bands (no valid overlap): {}".format(
            summary["skipped_bands"]))
    lines.append(
        "-----+------------+------------+------------+------------+-----------")

    # Aggregates per metric in the documented order: MEAN, WORST, P05.
    # P05 means "5% of bands are at least this bad" regardless of polarity
    # (higher-is-better → 5th pct; lower-is-better → 95th pct).
    def _row(label, prefix):
        return ("{lbl:>10} | {r:>10.4f} | {m:>10.4f} | {p:>10.4f} | "
                "{s:>10.4f} |").format(
            lbl=label,
            r=summary[prefix + "_rmse"],
            m=summary[prefix + "_mae"],
            p=summary[prefix + "_psnr"],
            s=summary[prefix + "_ssim"])

    def _band_row(label, prefix):
        # Band-index rows: integer band numbers, formatted as right-aligned
        # ints inside the same 10-wide columns the float rows use.
        return ("{lbl:>10} | {r:>10d} | {m:>10d} | {p:>10d} | "
                "{s:>10d} |").format(
            lbl=label,
            r=summary[prefix + "_rmse_band"],
            m=summary[prefix + "_mae_band"],
            p=summary[prefix + "_psnr_band"],
            s=summary[prefix + "_ssim_band"])

    # Order per metric: MEAN → WORST → WORST_<M>_BAND → P05 → P05_<M>_BAND.
    lines.append(_row("MEAN", "mean"))
    lines.append(_row("WORST", "worst"))
    lines.append(_band_row("WORST_BAND", "worst"))
    lines.append(_row("P05", "p05"))
    lines.append(_band_row("P05_BAND", "p05"))

    # SAM is a single whole-cube scalar (lower is better).
    lines.append(
        "-----+------------+------------+------------+------------+-----------")
    lines.append("SAM     (rad) : {:.6f}   [{} pixels valid in every band]"
                 .format(summary["sam"], summary["sam_valid_pixels"]))
    lines.append("SAM_DEG (deg) : {:.6f}".format(summary["sam_deg"]))
    return "\n".join(lines)
