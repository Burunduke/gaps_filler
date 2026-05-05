# -*- coding: utf-8 -*-
"""Pure-Python re-implementation of GDAL's GDALFillNodata.

This module has **no Qt / QGIS dependencies**: it imports only ``numpy``
(everywhere) and ``osgeo.gdal`` (lazily, in :func:`fill_nodata_file` for
raster I/O). Anything UI-related lives in the algorithm/provider modules.

Same observable behaviour as ``gdal.FillNodata``: per-pixel inverse-distance
weighting from the four nearest originally-valid pixels (one per spatial
quadrant — NW, NE, SW, SE) followed by an optional 3x3 masked-mean smoothing
pass repeated N times.

Phases:
    1. forward (top-down) sweep   -> NW & NE candidates per pixel
    2. backward (bottom-up) sweep -> SW & SE candidates per pixel
    3. IDW combine                -> fill nodata pixels using 1/d^2 weights
    4. smoothing                  -> N x 3x3 masked mean over filled pixels

A ``feedback`` object (duck-typed :class:`QgsProcessingFeedback`) may be
passed in to receive log lines and progress updates; cancellation is
honoured at coarse boundaries (sweeps, smoothing iterations).
"""

import numpy as np


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _box3_sum(a):
    """3x3 box sum with zero padding (border-clipped, like GDAL)."""
    H, W = a.shape
    p = np.zeros((H + 2, W + 2), dtype=a.dtype)
    p[1:-1, 1:-1] = a
    return (
        p[0:H,     0:W]     + p[0:H,     1:W + 1] + p[0:H,     2:W + 2] +
        p[1:H + 1, 0:W]     + p[1:H + 1, 1:W + 1] + p[1:H + 1, 2:W + 2] +
        p[2:H + 2, 0:W]     + p[2:H + 2, 1:W + 1] + p[2:H + 2, 2:W + 2]
    )


def _scan_quadrants(result, mask_orig, top_down):
    """One full sweep of the raster collecting two candidates per pixel.

    Returns (cand_W, dist_W, cand_E, dist_E) for the relevant half-plane:
      * top_down=True  -> NW (West side) & NE (East side)
      * top_down=False -> SW             & SE

    Strategy per row:
      * Two horizontal scans (left-to-right, right-to-left) give the nearest
        valid pixel within the current row from each side.
      * Persistent per-column trackers carry the best candidate seen in
        earlier rows. Updating each tracker every row with the better of
        (row scan vs tracker) propagates candidates diagonally — this is
        what turns four directional searches into four quadrant searches.
    """
    H, W = result.shape
    NEG = -1.0e18      # sentinel for "no candidate yet" coords
    POS = 1.0e18

    cand_W = np.full((H, W), np.nan, dtype=np.float64)
    cand_E = np.full((H, W), np.nan, dtype=np.float64)
    dist_W = np.full((H, W), np.inf, dtype=np.float64)
    dist_E = np.full((H, W), np.inf, dtype=np.float64)

    # Per-column "other half-plane" trackers.
    oth_W_val = np.full(W, np.nan, dtype=np.float64)
    oth_W_xo = np.full(W, NEG, dtype=np.float64)
    oth_W_yo = np.full(W, NEG, dtype=np.float64)
    oth_E_val = np.full(W, np.nan, dtype=np.float64)
    oth_E_xo = np.full(W, POS, dtype=np.float64)
    oth_E_yo = np.full(W, NEG, dtype=np.float64)

    cols = np.arange(W, dtype=np.float64)
    row_order = range(H) if top_down else range(H - 1, -1, -1)

    for y in row_order:
        mrow = mask_orig[y]
        vrow = result[y]
        # Sanitise possibly-NaN values at masked positions.
        vrow_safe = np.where(mrow, vrow, 0.0)
        yf = float(y)

        # --- West scan: column of latest valid pixel at col <= x.
        idx_w = np.where(mrow, cols, -1.0)
        west_xo = np.maximum.accumulate(idx_w)
        no_west = west_xo < 0
        safe_w = np.where(no_west, 0, west_xo).astype(np.int64)
        west_val = vrow_safe[safe_w]
        west_val = np.where(no_west, np.nan, west_val)
        west_xo = np.where(no_west, NEG, west_xo)
        west_yo = np.where(no_west, NEG, yf)

        # --- East scan: column of nearest valid pixel at col >= x.
        idx_e = np.where(mrow, cols, float(W))
        east_xo = np.minimum.accumulate(idx_e[::-1])[::-1]
        no_east = east_xo >= W
        safe_e = np.where(no_east, 0, east_xo).astype(np.int64)
        east_val = vrow_safe[safe_e]
        east_val = np.where(no_east, np.nan, east_val)
        east_xo = np.where(no_east, POS, east_xo)
        east_yo = np.where(no_east, NEG, yf)

        # --- West side: pick row scan vs persistent tracker, keep the closer.
        d_row = np.hypot(cols - west_xo, yf - west_yo)
        d_oth = np.hypot(cols - oth_W_xo, yf - oth_W_yo)
        take_row = d_row <= d_oth
        oth_W_val = np.where(take_row, west_val, oth_W_val)
        oth_W_xo = np.where(take_row, west_xo, oth_W_xo)
        oth_W_yo = np.where(take_row, west_yo, oth_W_yo)
        cand_W[y] = oth_W_val
        dist_W[y] = np.where(take_row, d_row, d_oth)

        # --- East side.
        d_row = np.hypot(cols - east_xo, yf - east_yo)
        d_oth = np.hypot(cols - oth_E_xo, yf - oth_E_yo)
        take_row = d_row <= d_oth
        oth_E_val = np.where(take_row, east_val, oth_E_val)
        oth_E_xo = np.where(take_row, east_xo, oth_E_xo)
        oth_E_yo = np.where(take_row, east_yo, oth_E_yo)
        cand_E[y] = oth_E_val
        dist_E[y] = np.where(take_row, d_row, d_oth)

    return cand_W, dist_W, cand_E, dist_E


def _smooth_step(result, mask_orig):
    """One masked-mean 3x3 smoothing iteration (out-of-place).

    * Only originally-nodata pixels are updated.
    * The mean uses only currently-finite neighbours.
    * Pixels with zero valid neighbours are left unchanged.
    """
    valid = ~np.isnan(result)
    weights = valid.astype(np.float64)
    values = np.where(valid, result, 0.0)
    sum_v = _box3_sum(values)
    sum_w = _box3_sum(weights)
    with np.errstate(divide="ignore", invalid="ignore"):
        avg = np.where(sum_w > 0, sum_v / sum_w, result)
    return np.where(mask_orig, result, avg)


def _canceled(feedback):
    """Return True if the feedback object reports cancellation."""
    return feedback is not None and feedback.isCanceled()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fill_nodata(band,
                mask=None,
                max_search_dist=100.0,
                smoothing_iterations=0,
                nodata=None,
                interpolation="INV_DIST",
                feedback=None):
    """Pure-numpy equivalent of ``gdal.FillNodata``.

    Parameters
    ----------
    band : np.ndarray
        2-D input raster (any numeric dtype).
    mask : np.ndarray or None
        Same shape as ``band``. Truthy = valid. If ``None``, derived from
        ``nodata`` (NaN-aware).
    max_search_dist : float
        Max candidate distance in pixels (inclusive). ``<= 0`` disables fill.
    smoothing_iterations : int
        Number of 3x3 smoothing passes after fill.
    nodata : numeric or None
        Used to derive the mask when ``mask is None`` and as the sentinel
        for pixels that remain unfilled.
    interpolation : {"INV_DIST", "NEAREST"}
    feedback : optional, duck-typed QgsProcessingFeedback
        If given, used to push log lines, progress and observe cancellation.

    Returns
    -------
    np.ndarray
        Filled raster, same shape and dtype as ``band``.
    """
    band = np.asarray(band)
    if band.ndim != 2:
        raise ValueError("fill_nodata expects a 2-D array")

    src_dtype = band.dtype
    H, W = band.shape

    # ---- Build the original mask (frozen for the whole algorithm). -------
    if mask is None:
        if nodata is None:
            mask_orig = ~np.isnan(band.astype(np.float64))
        else:
            if np.isnan(nodata):
                mask_orig = ~np.isnan(band.astype(np.float64))
            else:
                mask_orig = band != nodata
                if np.issubdtype(src_dtype, np.floating):
                    mask_orig &= ~np.isnan(band)
    else:
        mask_orig = np.asarray(mask).astype(bool)
        if mask_orig.shape != band.shape:
            raise ValueError("mask shape must match band shape")

    # Working copy in float64; nodata cells become NaN so smoothing can
    # detect "still missing" easily.
    result = band.astype(np.float64, copy=True)
    result[~mask_orig] = np.nan

    # ---- Edge cases ------------------------------------------------------
    if mask_orig.all():
        if feedback is not None:
            feedback.pushInfo("No nodata pixels to fill.")
        return band.copy()
    if not mask_orig.any():
        if feedback is not None:
            feedback.pushInfo("Mask is empty: nothing to fill.")
        return band.copy()

    total_steps = (2 if max_search_dist > 0 else 0) + int(smoothing_iterations)
    step_done = 0

    def _progress():
        if feedback is not None and total_steps > 0:
            feedback.setProgress(int(100 * step_done / total_steps))

    # ---- Quadrant scans --------------------------------------------------
    if max_search_dist > 0:
        if _canceled(feedback):
            raise RuntimeError("canceled")
        if feedback is not None:
            feedback.pushInfo("Forward sweep (NW/NE candidates)…")
        cand_NW, dist_NW, cand_NE, dist_NE = _scan_quadrants(
            result, mask_orig, top_down=True)
        step_done += 1
        _progress()

        if _canceled(feedback):
            raise RuntimeError("canceled")
        if feedback is not None:
            feedback.pushInfo("Backward sweep (SW/SE candidates)…")
        cand_SW, dist_SW, cand_SE, dist_SE = _scan_quadrants(
            result, mask_orig, top_down=False)
        step_done += 1
        _progress()

        cands = np.stack([cand_NW, cand_NE, cand_SW, cand_SE], axis=0)
        dists = np.stack([dist_NW, dist_NE, dist_SW, dist_SE], axis=0)

        # Keep only finite candidates within the search radius.
        in_range = (dists <= max_search_dist) & np.isfinite(cands)

        # ---- IDW combine -------------------------------------------------
        if interpolation.upper() == "NEAREST":
            big = np.where(in_range, dists, np.inf)
            best = np.argmin(big, axis=0)
            yy, xx = np.indices((H, W))
            picked_d = big[best, yy, xx]
            picked_v = cands[best, yy, xx]
            filled = np.where(np.isfinite(picked_d), picked_v, np.nan)
        else:
            d2 = dists * dists
            ok = in_range & (d2 > 0)
            with np.errstate(divide="ignore", invalid="ignore"):
                w = np.where(ok, 1.0 / np.where(ok, d2, 1.0), 0.0)
            v = np.where(ok & np.isfinite(cands), cands, 0.0)
            num = (v * w).sum(axis=0)
            den = w.sum(axis=0)
            with np.errstate(divide="ignore", invalid="ignore"):
                filled = np.where(den > 0, num / den, np.nan)

        # Update only originally-nodata pixels.
        result = np.where(mask_orig, result, filled)

    # ---- Smoothing -------------------------------------------------------
    for i in range(int(smoothing_iterations)):
        if _canceled(feedback):
            raise RuntimeError("canceled")
        if feedback is not None:
            feedback.pushInfo(
                "Smoothing iteration {}/{}".format(i + 1,
                                                   int(smoothing_iterations)))
        result = _smooth_step(result, mask_orig)
        step_done += 1
        _progress()

    # ---- Cast back to source dtype --------------------------------------
    if np.issubdtype(src_dtype, np.integer):
        fill_value = 0 if nodata is None else nodata
        out = np.where(np.isnan(result), float(fill_value), result)
        out = np.rint(out).astype(src_dtype)
    else:
        if nodata is not None and not (isinstance(nodata, float) and np.isnan(nodata)):
            result = np.where(np.isnan(result), float(nodata), result)
        out = result.astype(src_dtype)
    return out


def fill_nodata_file(input_path, output_path,
                     mask_path=None, max_search_dist=10.0,
                     smoothing_iterations=0, feedback=None):
    """Fill nodata in **every** band of a raster and write a multi-band GeoTIFF.

    All bands of the input are processed with the same parameters; each
    band keeps its own nodata value and is filled independently by
    :func:`fill_nodata`. The output has the same band count, size,
    geotransform and projection as the input.

    If ``mask_path`` is given, the first band of that raster is used as
    the validity mask (non-zero = valid pixel) for **all** bands;
    otherwise each band's own nodata value drives its mask.

    GDAL is used only for I/O — the algorithm runs in :func:`fill_nodata`.

    A duck-typed :class:`QgsProcessingFeedback` may be passed via
    ``feedback``; it will receive ``pushInfo`` messages and ``setProgress``
    updates and is polled for ``isCanceled()``.
    """
    from osgeo import gdal  # local import: keep plugin import-time light

    if feedback is not None:
        feedback.pushInfo("Opening input: {}".format(input_path))
    src = gdal.Open(input_path, gdal.GA_ReadOnly)
    if src is None:
        raise IOError("Cannot open {}".format(input_path))

    band_count = src.RasterCount
    if band_count < 1:
        raise ValueError("input raster has no bands")

    # Optional external validity mask: read first band, non-zero = valid.
    # Shared across all bands.
    mask = None
    if mask_path:
        if feedback is not None:
            feedback.pushInfo("Reading mask: {}".format(mask_path))
        msrc = gdal.Open(mask_path, gdal.GA_ReadOnly)
        if msrc is None:
            raise IOError("Cannot open mask {}".format(mask_path))
        marr = msrc.GetRasterBand(1).ReadAsArray()
        msrc = None
        if marr.shape != (src.RasterYSize, src.RasterXSize):
            raise ValueError("mask shape {} does not match raster {}"
                             .format(marr.shape,
                                     (src.RasterYSize, src.RasterXSize)))
        mask = marr != 0

    if feedback is not None:
        feedback.pushInfo("Creating output GeoTIFF: {}".format(output_path))
    driver = gdal.GetDriverByName("GTiff")
    # If a previous file exists at output_path, delete it first.
    # driver.Create alone is not always enough to guarantee a clean
    # replacement — on some platforms the existing dataset can stay
    # partially live (cached by GDAL/QGIS).
    if gdal.VSIStatL(output_path) is not None:
        try:
            driver.Delete(output_path)
        except RuntimeError:
            # Fallback: best-effort filesystem delete.
            import os
            try:
                os.remove(output_path)
            except OSError:
                pass

    # Use the first band's data type for the whole output. Hyperspectral
    # cubes (and basically every multi-band raster we care about) have
    # uniform dtype across bands; fill_nodata casts back to the source
    # dtype anyway, so any minor mismatch is a safe round-trip cast.
    out_dtype = src.GetRasterBand(1).DataType
    dst = driver.Create(
        output_path,
        src.RasterXSize,
        src.RasterYSize,
        band_count,
        out_dtype,
    )
    dst.SetGeoTransform(src.GetGeoTransform())
    dst.SetProjection(src.GetProjection())

    if feedback is not None:
        feedback.pushInfo(
            "Filling {} band(s), size {}x{}".format(
                band_count, src.RasterXSize, src.RasterYSize))
        feedback.setProgress(0)

    # Per-band fill loop. Each band gets its own nodata sentinel.
    for b in range(1, band_count + 1):
        in_band = src.GetRasterBand(b)
        arr = in_band.ReadAsArray()
        nodata = in_band.GetNoDataValue()

        if feedback is not None:
            feedback.pushInfo(
                "Band {}/{} (nodata={})".format(b, band_count, nodata))

        filled = fill_nodata(
            arr,
            mask=mask,
            max_search_dist=max_search_dist,
            smoothing_iterations=smoothing_iterations,
            nodata=nodata,
            feedback=feedback,
        )
        dst.GetRasterBand(b).WriteArray(filled)
        if nodata is not None:
            dst.GetRasterBand(b).SetNoDataValue(nodata)

        if feedback is not None:
            feedback.setProgress(int(100 * b / band_count))

    dst.FlushCache()
    written_bands = dst.RasterCount
    dst = None
    src = None

    # Re-open read-only to confirm what actually landed on disk.
    verify = gdal.Open(output_path, gdal.GA_ReadOnly)
    on_disk_bands = verify.RasterCount if verify is not None else -1
    verify = None

    if feedback is not None:
        feedback.pushInfo(
            "Output written: {} band(s) in dataset, {} band(s) on disk."
            .format(written_bands, on_disk_bands))
        feedback.setProgress(100)
        feedback.pushInfo("Done.")
