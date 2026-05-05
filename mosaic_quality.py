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
the mosaic are excluded from every metric (mask intersection). No
spatial cropping or alignment is performed — the two rasters must
already share CRS, resolution and extent.
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


def _grids_match(ref, mos):
    """Cheap geometric sanity check — same size, GT, projection."""
    if (ref.RasterXSize, ref.RasterYSize) != (mos.RasterXSize, mos.RasterYSize):
        return False, "size mismatch: ref={}x{} mos={}x{}".format(
            ref.RasterXSize, ref.RasterYSize,
            mos.RasterXSize, mos.RasterYSize)
    gt_r = ref.GetGeoTransform()
    gt_m = mos.GetGeoTransform()
    if any(abs(a - b) > 1e-9 for a, b in zip(gt_r, gt_m)):
        return False, "geotransform mismatch: {} vs {}".format(gt_r, gt_m)
    if (ref.GetProjection() or "") != (mos.GetProjection() or ""):
        return False, "projection mismatch"
    return True, ""


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
        If the two rasters disagree on grid (size / GT / projection)
        or band count.
    RuntimeError
        If scikit-image is not installed (only when SSIM is needed,
        i.e. there is at least one band with valid pixels).
    """
    ref = _open(reference_path)
    mos = _open(mosaic_path)

    ok, why = _grids_match(ref, mos)
    if not ok:
        msg = ("Reference and mosaic rasters must share grid "
               "(CRS / resolution / extent): " + why)
        if feedback is not None:
            feedback.reportError(msg, fatalError=True)
        raise ValueError(msg)

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
