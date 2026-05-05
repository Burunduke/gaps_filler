# -*- coding: utf-8 -*-
"""Pixel-wise quality metrics between a reference orthophoto and a mosaic.

Pure module — no Qt / no QGIS imports — so it can be reused from any
context. GDAL is used only for raster I/O, exactly as the rest of the
plugin does. The heavy lifting happens in :func:`compare_rasters`,
which loops band-by-band (consistent with the per-band loop in
:mod:`fill_nodata`) and returns a dict of per-band and mean metrics.

Metrics computed per band (over the intersection of validity masks):

- ``rmse``  -- root mean squared error.
- ``mae``   -- mean absolute error.
- ``psnr``  -- peak signal-to-noise ratio, computed from MSE using the
  data range ``max - min`` of the *reference* band over valid pixels.
- ``ssim``  -- structural similarity index, via
  :func:`skimage.metrics.structural_similarity` (scikit-image required).

NoData handling: pixels marked nodata in **either** the reference or
the mosaic are excluded from every metric (mask intersection).

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
# Public API
# ---------------------------------------------------------------------------


def compare_rasters(reference_path, mosaic_path, feedback=None):
    """Compute per-band RMSE / MAE / PSNR / SSIM and their means.

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
            "mean_rmse": float, "mean_mae": float,
            "mean_psnr": float, "mean_ssim": float,
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

    # Means: average across bands that produced numbers; ignore inf in
    # PSNR mean only if every band is inf (then mean is inf).
    def _mean(key):
        vals = [m[key] for m in per_band]
        if not vals:
            return float("nan")
        return float(sum(vals) / len(vals))

    summary = {
        "band_count": band_count,
        "per_band": per_band,
        "skipped_bands": skipped,
        "mean_rmse": _mean("rmse") if per_band else float("nan"),
        "mean_mae": _mean("mae") if per_band else float("nan"),
        "mean_psnr": _mean("psnr") if per_band else float("nan"),
        "mean_ssim": _mean("ssim") if per_band else float("nan"),
    }
    return summary


def format_report(summary):
    """Render the result of :func:`compare_rasters` as a plain-text table.

    Returned string is multi-line and ready for ``feedback.pushInfo``.
    """
    lines = []
    lines.append("Band |       RMSE |        MAE |       PSNR |       SSIM |   Valid px")
    lines.append("-----+------------+------------+------------+------------+-----------")
    for m in summary["per_band"]:
        lines.append(
            "{band:>4} | {rmse:>10.4f} | {mae:>10.4f} | {psnr:>10.4f} | "
            "{ssim:>10.4f} | {n:>9d}".format(
                band=m["band"], rmse=m["rmse"], mae=m["mae"],
                psnr=m["psnr"], ssim=m["ssim"], n=m["valid_pixels"]))
    if summary["skipped_bands"]:
        lines.append("Skipped bands (no valid overlap): {}".format(
            summary["skipped_bands"]))
    lines.append("-----+------------+------------+------------+------------+-----------")
    lines.append(
        "MEAN | {r:>10.4f} | {m:>10.4f} | {p:>10.4f} | {s:>10.4f} |".format(
            r=summary["mean_rmse"], m=summary["mean_mae"],
            p=summary["mean_psnr"], s=summary["mean_ssim"]))
    return "\n".join(lines)
