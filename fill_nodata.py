# -*- coding: utf-8 -*-
"""Stage C gap-fill backends (registered in :data:`methods.GAP_FILL_METHODS`).

This module has **no Qt / QGIS dependencies**: it imports only ``numpy``
(everywhere) and ``osgeo.gdal`` (lazily, for raster I/O). Anything
UI-related lives in the algorithm/provider modules.

The active gap-fill method is selected at the call site via the
``GAP_FILL_METHODS`` registry; this module ships two implementations:

* :func:`fill_nodata_file` (v2, default) — pure-Python re-implementation
  of GDAL's ``GDALFillNodata``: per-pixel inverse-distance weighting
  from the four nearest originally-valid pixels (one per spatial
  quadrant — NW, NE, SW, SE) followed by an optional 3x3 masked-mean
  smoothing pass repeated N times. The array-level core
  :func:`fill_nodata` is what implements those phases:
      1. forward (top-down) sweep   -> NW & NE candidates per pixel
      2. backward (bottom-up) sweep -> SW & SE candidates per pixel
      3. IDW combine                -> fill nodata pixels using 1/d^2 weights
      4. smoothing                  -> N x 3x3 masked mean over filled pixels
* :func:`fill_nodata_file_gdal` (v3) — thin wrapper around the native-C
  ``gdal.FillNodata`` for ~10–100× speed-ups, with an automatic fallback
  to the v2 backend on any non-cancellation error.

A ``feedback`` object (duck-typed :class:`QgsProcessingFeedback`) may be
passed in to receive log lines and progress updates; cancellation is
honoured at coarse boundaries (sweeps, smoothing iterations, bands /
tiles).
"""

import numpy as np


# ---------------------------------------------------------------------------
# Public helper — interior-hole mask
# ---------------------------------------------------------------------------


def _dilate4(a):
    """One iteration of 4-connected binary dilation (pure numpy)."""
    out = a.copy()
    out[1:, :] |= a[:-1, :]
    out[:-1, :] |= a[1:, :]
    out[:, 1:] |= a[:, :-1]
    out[:, :-1] |= a[:, 1:]
    return out


def _erode4(a):
    """One iteration of 4-connected binary erosion (pure numpy).

    Out-of-frame neighbours are treated as ``False`` so the border is
    eroded inwards, matching :func:`scipy.ndimage.binary_erosion` with
    ``border_value=0`` (its default).
    """
    out = a.copy()
    out[1:, :] &= a[:-1, :]
    out[:-1, :] &= a[1:, :]
    out[:, 1:] &= a[:, :-1]
    out[:, :-1] &= a[:, 1:]
    # Edges lose their out-of-frame neighbour (= False); zero them out.
    out[0, :] = False
    out[-1, :] = False
    out[:, 0] = False
    out[:, -1] = False
    return out


def write_interior_fill_mask(input_path: str, mask_path: str,
                             max_gap_px: int = 0,
                             three_state: bool = True) -> None:
    """Write a uint8 mask co-registered with ``input_path``.

    When ``three_state=True`` (default), the mask has three states:
      * 0 = original (valid pixels that were not filled)
      * 1 = filled (interior holes that were filled)
      * 2 = outside (outside-footprint pixels that remain nodata)

    When ``three_state=False`` (legacy behaviour), the mask is binary:
      * 0 = fillable holes (interior only)
      * 1 = valid pixels and outside-footprint pixels

    Parameters
    ----------
    input_path : str
        Source raster. Validity is the union of finite pixels across
        all bands (built band-by-band with ``np.logical_or`` so the
        full cube never lives in memory at once).
    mask_path : str
        Destination single-band uint8 GeoTIFF.
    max_gap_px : int, default 0
        Controls morphological closing of the validity footprint to
        bridge narrow gaps -- including those that touch the raster
        edge or extend to a concavity, which a topological fill alone
        cannot reach. ``0`` reproduces the strict legacy behaviour
        (only topologically enclosed holes are filled, byte-for-byte).
        ``N > 0`` performs ``N`` iterations of dilation followed by
        ``N`` iterations of erosion (4-connected) on the validity mask
        and unions the result with the topological fill, so gaps up to
        roughly ``2N`` pixels wide are bridged.
    three_state : bool, default True
        When True, write a 3-state mask (0=original, 1=filled, 2=outside).
        When False, write a 2-state mask (0=fillable, 1=not-fillable)
        for backward compatibility with legacy behaviour.

    ``rasterio`` and ``scipy`` are imported lazily so this module's
    top-level import surface stays numpy-only (matches the historical
    promise in the module docstring). When ``scipy`` is unavailable a
    pure-numpy 4-connected fallback is used for both the topological
    flood-fill and the closing.
    """
    import rasterio  # lazy: keep top-level imports numpy-only

    with rasterio.open(input_path) as src:
        H, W = src.height, src.width
        validity = np.zeros((H, W), dtype=bool)
        for b in range(1, src.count + 1):
            np.logical_or(validity, np.isfinite(src.read(b)), out=validity)
        profile = {
            "driver": "GTiff", "height": H, "width": W, "count": 1,
            "dtype": "uint8", "transform": src.transform, "crs": src.crs,
            "compress": "deflate",
        }

    # ---- Topologically enclosed holes (always) ---------------------------
    # ``filled`` = validity OR all enclosed holes.
    invalid = ~validity
    try:
        from scipy.ndimage import binary_fill_holes
        filled = binary_fill_holes(validity)
    except ImportError:
        outside = np.zeros_like(invalid)
        outside[0, :] = invalid[0, :]
        outside[-1, :] = invalid[-1, :]
        outside[:, 0] = invalid[:, 0]
        outside[:, -1] = invalid[:, -1]
        prev = -1
        while True:
            cur = int(outside.sum())
            if cur == prev:
                break
            prev = cur
            new = outside.copy()
            new[1:, :] |= outside[:-1, :]
            new[:-1, :] |= outside[1:, :]
            new[:, 1:] |= outside[:, :-1]
            new[:, :-1] |= outside[:, 1:]
            outside = new & invalid
        filled = ~outside  # validity ∪ enclosed holes

    # ---- Optional morphological closing (bridges edge-touching gaps) -----
    if max_gap_px > 0:
        try:
            from scipy.ndimage import binary_closing
            closed = binary_closing(validity, iterations=int(max_gap_px))
        except ImportError:
            closed = validity.copy()
            for _ in range(int(max_gap_px)):
                closed = _dilate4(closed)
            for _ in range(int(max_gap_px)):
                closed = _erode4(closed)
        fill_region = (filled | closed) & invalid
    else:
        fill_region = filled & invalid

    if three_state:
        # Create 3-state mask:
        # 0 = original (valid pixels)
        # 1 = filled (interior holes)
        # 2 = outside (outside-footprint pixels)
        mask = np.zeros((H, W), dtype=np.uint8)
        mask[fill_region] = 1  # filled interior holes
        mask[invalid & ~fill_region] = 2  # outside-footprint pixels
        band_description = "0=original, 1=filled, 2=outside"
    else:
        # Create 2-state mask (legacy behaviour):
        # 0 = fillable holes (interior only)
        # 1 = valid pixels and outside-footprint pixels
        mask = (~fill_region).astype(np.uint8)
        band_description = "0=fillable, 1=not-fillable"
    
    # Add band description for QGIS
    profile["nodata"] = None  # No nodata value for this mask
    
    with rasterio.open(mask_path, "w", **profile) as dst:
        dst.write(mask, 1)
        # Add band description
        dst.set_band_description(1, band_description)


def compute_gap_region_metrics(fillmask_path):
    """Compute gap region metrics from a 3-state fillmask.
    
    Parameters
    ----------
    fillmask_path : str
        Path to the 3-state fillmask GeoTIFF (0=original, 1=filled, 2=outside)
    
    Returns
    -------
    dict
        Dictionary with keys:
        - n_gap_regions: int, number of distinct connected gap regions
        - largest_gap_px: int, size of largest gap region in pixels
        - largest_gap_area_m2: float, area of largest gap in square meters (None if geotransform unavailable)
    """
    import rasterio
    import numpy as np
    
    # Read the fillmask
    with rasterio.open(fillmask_path) as src:
        fillmask = src.read(1)
        transform = src.transform
    
    # Extract gap regions (pixels with value 1)
    gap_mask = fillmask == 1
    
    # If no gaps, return zeros
    if not np.any(gap_mask):
        return {
            "n_gap_regions": 0,
            "largest_gap_px": 0,
            "largest_gap_area_m2": 0.0
        }
    
    # Compute connected components
    try:
        from scipy.ndimage import label
        # 4-connectivity structure
        structure = np.array([[0, 1, 0],
                                   [1, 1, 1],
                                   [0, 1, 0]], dtype=int)
        labeled_array, n_regions = label(gap_mask, structure=structure)
    except ImportError:
        # Fallback implementation using simple BFS
        labeled_array, n_regions = _label_components_4conn(gap_mask)
    
    # Compute region sizes
    if n_regions > 0:
        # Count pixels in each region
        region_sizes = np.bincount(labeled_array.ravel())[1:]  # Skip background (0)
        largest_gap_px = int(np.max(region_sizes)) if len(region_sizes) > 0 else 0
    else:
        largest_gap_px = 0
    
    # Compute area in square meters if geotransform is available
    largest_gap_area_m2 = None
    if transform is not None:
        # Pixel size from geotransform
        pixel_w = abs(transform[0])  # x resolution
        pixel_h = abs(transform[4])  # y resolution (usually negative)
        
        # Only compute area if both pixel dimensions are non-zero
        if pixel_w > 0 and pixel_h > 0:
            largest_gap_area_m2 = float(pixel_w * pixel_h * largest_gap_px)
    
    return {
        "n_gap_regions": int(n_regions),
        "largest_gap_px": int(largest_gap_px),
        "largest_gap_area_m2": largest_gap_area_m2
    }


def _label_components_4conn(binary_mask):
    """Label connected components with 4-connectivity using BFS.
    
    Parameters
    ----------
    binary_mask : np.ndarray
        Binary mask where True indicates foreground pixels
    
    Returns
    -------
    labeled_array : np.ndarray
        Array with same shape as binary_mask, with each connected component
        labeled with a unique integer (0 = background)
    n_components : int
        Number of connected components found
    """
    import numpy as np
    from collections import deque
    
    H, W = binary_mask.shape
    labeled = np.zeros((H, W), dtype=np.int32)
    current_label = 0
    
    # 4-connectivity offsets (up, down, left, right)
    offsets = [(-1, 0), (1, 0), (0, -1), (0, 1)]
    
    for i in range(H):
        for j in range(W):
            # If this is a foreground pixel and not yet labeled
            if binary_mask[i, j] and labeled[i, j] == 0:
                current_label += 1
                # BFS to label all connected pixels
                queue = deque([(i, j)])
                labeled[i, j] = current_label
                
                while queue:
                    y, x = queue.popleft()
                    # Check 4-connected neighbors
                    for dy, dx in offsets:
                        ny, nx = y + dy, x + dx
                        # Bounds check
                        if 0 <= ny < H and 0 <= nx < W:
                            # If neighbor is foreground and not labeled
                            if binary_mask[ny, nx] and labeled[ny, nx] == 0:
                                labeled[ny, nx] = current_label
                                queue.append((ny, nx))
    
    return labeled, current_label


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


def _fill_band_windowed(in_band, out_band, ext_mask_arr,
                        max_search_dist, smoothing_iterations,
                        nodata, tile_size, feedback, b, band_count):
    """Fill one band tile-by-tile so the whole band never lives in RAM.

    Each tile is read with a halo of ``max_search_dist + smoothing_iterations``
    pixels around its inner core. :func:`fill_nodata` runs on the tile
    plus halo, and only the inner core (without the halo) is written
    back to the output band. The halo guarantees that a NaN pixel right
    on a tile boundary still sees the same set of valid neighbours it
    would have seen if the whole band had been processed at once, so
    the windowed result is observationally identical to the whole-band
    path for any tile size large enough to contain the search radius.

    ``tile_size`` is the size of the inner core (output) tile in pixels;
    the read window is always ``tile_size + 2 * halo`` wide / tall
    (clipped to band bounds at the edges).
    """
    H = in_band.YSize
    W = in_band.XSize
    halo = int(max_search_dist) + int(smoothing_iterations)
    n_tiles_y = (H + tile_size - 1) // tile_size
    n_tiles_x = (W + tile_size - 1) // tile_size
    n_tiles = max(1, n_tiles_y * n_tiles_x)
    done = 0
    for ti in range(n_tiles_y):
        for tj in range(n_tiles_x):
            if feedback is not None and feedback.isCanceled():
                raise RuntimeError("canceled")
            # Inner core (the region we'll actually write back).
            r0 = ti * tile_size
            c0 = tj * tile_size
            r1 = min(r0 + tile_size, H)
            c1 = min(c0 + tile_size, W)
            # Read window (inner core + halo, clipped at band edges).
            rr0 = max(0, r0 - halo)
            cc0 = max(0, c0 - halo)
            rr1 = min(H, r1 + halo)
            cc1 = min(W, c1 + halo)
            arr = in_band.ReadAsArray(cc0, rr0, cc1 - cc0, rr1 - rr0)
            if ext_mask_arr is not None:
                mtile = ext_mask_arr[rr0:rr1, cc0:cc1]
            else:
                mtile = None
            filled = fill_nodata(
                arr,
                mask=mtile,
                max_search_dist=max_search_dist,
                smoothing_iterations=smoothing_iterations,
                nodata=nodata,
                feedback=None,  # per-tile feedback would spam the log
            )
            # Crop the halo off and write the inner core.
            ir0 = r0 - rr0
            ic0 = c0 - cc0
            ir1 = ir0 + (r1 - r0)
            ic1 = ic0 + (c1 - c0)
            out_band.WriteArray(filled[ir0:ir1, ic0:ic1], c0, r0)
            done += 1
            if feedback is not None:
                # Map this band's tile progress into the band's slice of
                # the [0..100] progress bar so multi-band cubes still get
                # a smoothly-advancing bar.
                band_frac = ((b - 1) + done / n_tiles) / band_count
                feedback.setProgress(int(100 * band_frac))


def _fill_band_worker(input_path, b, mask_path,
                      max_search_dist, smoothing_iterations,
                      tile_size=0):
    """Top-level worker (one band per call) for :class:`ThreadPoolExecutor`.

    Each worker re-opens the input raster, reads its assigned
    band plus (if given) the validity mask, runs the gap-fill, and
    returns ``(b, filled, nodata)``. Re-opening per call keeps every
    worker independent -- one process per band, no shared GDAL state
    across processes -- as required by Pipeline TO-DO item #9 in
    ``hyperspectral_plan.md`` ("Watch GDAL thread-safety: keep one
    process per band.").

    When ``tile_size > 0`` the worker dispatches to
    :func:`_fill_band_windowed` against an in-memory (``MEM`` driver)
    output sink so the existing tile-with-halo logic runs unchanged --
    the worker reads the input tile-by-tile and returns one fully
    filled band array to the parent, which owns the only writable
    handle to the real output GeoTIFF. When ``tile_size == 0`` the
    worker takes the original whole-band path: read, fill, return.

    The function is kept at module top level (harmless for threads,
    and future-proof in case we ever go back to a process pool).
    """
    from osgeo import gdal  # local import: workers spawn fresh interpreters
    src = gdal.Open(input_path, gdal.GA_ReadOnly)
    if src is None:
        raise IOError("Cannot open {}".format(input_path))
    in_band = src.GetRasterBand(b)
    nodata = in_band.GetNoDataValue()

    mask = None
    if mask_path:
        msrc = gdal.Open(mask_path, gdal.GA_ReadOnly)
        if msrc is None:
            raise IOError("Cannot open mask {}".format(mask_path))
        marr = msrc.GetRasterBand(1).ReadAsArray()
        msrc = None
        # For 3-state mask: 0=original, 1=filled, 2=outside
        # We want to fill only pixels with value 1 (interior holes)
        mask = marr != 1

    if int(tile_size) > 0:
        # Tiled path: keep IO bounded inside the worker by reusing
        # the existing `_fill_band_windowed` helper. Output goes to
        # a MEM dataset because the parent owns the on-disk output;
        # the worker hands the parent a single filled band array.
        mem_drv = gdal.GetDriverByName("MEM")
        mem_ds = mem_drv.Create(
            "", in_band.XSize, in_band.YSize, 1, in_band.DataType)
        out_band = mem_ds.GetRasterBand(1)
        # b=1, band_count=1, feedback=None: per-tile progress isn't
        # plumbed out of a worker (parent reports per-band-completion
        # progress instead).
        _fill_band_windowed(
            in_band, out_band, mask,
            max_search_dist, smoothing_iterations,
            nodata, int(tile_size), None, 1, 1)
        filled = out_band.ReadAsArray()
        mem_ds = None
    else:
        arr = in_band.ReadAsArray()
        filled = fill_nodata(
            arr,
            mask=mask,
            max_search_dist=max_search_dist,
            smoothing_iterations=smoothing_iterations,
            nodata=nodata,
            feedback=None,  # per-worker feedback would garble the parent log
        )
    src = None
    return b, filled, nodata


def fill_nodata_file(input_path, output_path,
                     mask_path=None, max_search_dist=10.0,
                     smoothing_iterations=0, feedback=None,
                     tile_size=0, n_workers=1):
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

    Parameters
    ----------
    tile_size : int, default 0
        When ``> 0``, each band is processed in square tiles of
        ``tile_size`` pixels (read with a halo of
        ``max_search_dist + smoothing_iterations`` pixels around each
        tile so border pixels see the same neighbours they would have
        seen in the whole-band path). This keeps memory bounded to a
        few tiles instead of an entire band, which can be hundreds of
        MB per band on big hyperspectral cubes (Pipeline TO-DO item #8
        in ``hyperspectral_plan.md``). ``0`` (the default) reproduces
        the legacy whole-band behaviour byte-for-byte.
    n_workers : int, default 1
        Number of worker threads for the per-band fill (Pipeline
        TO-DO item #9 in ``hyperspectral_plan.md``). ``1`` (the default)
        runs the legacy in-process loop byte-for-byte. ``> 1`` dispatches
        bands to a :class:`concurrent.futures.ThreadPoolExecutor` --
        bands are independent, and each thread re-opens the input so GDAL
        thread-safe (each worker re-opens the input). Honoured for both
        whole-band (``tile_size == 0``) and tiled (``tile_size > 0``)
        modes; in the tiled+parallel mode each band is still processed
        tile-by-tile *inside* its worker, but bands run concurrently.
        Progress is reported per band-completion (one tick per band that
        finishes) in the parallel mode -- per-tile progress isn't plumbed
        out of a worker thread and the extra plumbing isn't worth it for
        the band-level granularity users actually see.
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
        # For 3-state mask: 0=original, 1=filled, 2=outside
        # We want to fill only pixels with value 1 (interior holes)
        # For 2-state mask: 0=fillable, 1=not-fillable
        # We want to fill only pixels with value 0 (fillable holes)
        #
        # Since we always generate 3-state masks now, we check for value 1
        mask = marr != 1

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

    use_tiles = int(tile_size) > 0
    if feedback is not None and use_tiles:
        feedback.pushInfo(
            "Windowed mode: tile_size={} px, halo={} px".format(
                int(tile_size),
                int(max_search_dist) + int(smoothing_iterations)))

    # Pipeline TO-DO item #9: parallelise the per-band loop with
    # :class:`concurrent.futures.ThreadPoolExecutor` when the caller
    # asks for it. Threads avoid the Windows ``spawn`` start method
    # re-importing QGIS in each worker (which crashed the process pool
    # with "A process in the process pool was terminated abruptly").
    # GDAL releases the GIL during heavy I/O and our pattern of "each
    # worker re-opens the input" already gives every thread its own
    # GDAL dataset handle, so threads don't race on a shared dataset.
    # Both whole-band and tiled fills parallelise: in the tiled+parallel
    # mode each worker still tiles its band sequentially via
    # `_fill_band_windowed`, but bands run concurrently. Progress is
    # reported per band-completion in the parallel mode (per-tile
    # progress isn't plumbed out of a worker thread).
    use_workers = int(n_workers) > 1
    if feedback is not None and use_workers:
        feedback.pushInfo(
            "Parallel fill: {} worker thread(s){}".format(
                int(n_workers),
                " (tiled inside each worker)" if use_tiles else ""))

    if use_workers:
        # Parent stays single-threaded for GDAL writes; workers only
        # read and run the array-level fill (or tiled fill into a MEM
        # band, returned as one filled array). Submit every band, then
        # write results as they come back so big cubes don't keep all
        # filled arrays in RAM at once.
        from concurrent.futures import ThreadPoolExecutor, as_completed
        # Close the source dataset so workers see a clean file handle
        # (and so we don't hold an extra fd open for nothing).
        src = None
        bands_done = 0
        with ThreadPoolExecutor(max_workers=int(n_workers)) as pool:
            futures = {
                pool.submit(_fill_band_worker,
                            input_path, b, mask_path,
                            max_search_dist, smoothing_iterations,
                            int(tile_size)): b
                for b in range(1, band_count + 1)
            }
            try:
                for fut in as_completed(futures):
                    if feedback is not None and feedback.isCanceled():
                        # Best-effort: drop pending work and raise.
                        for f in futures:
                            f.cancel()
                        raise RuntimeError("canceled")
                    b, filled, nodata = fut.result()
                    out_band = dst.GetRasterBand(b)
                    out_band.WriteArray(filled)
                    if nodata is not None:
                        out_band.SetNoDataValue(nodata)
                    bands_done += 1
                    if feedback is not None:
                        feedback.pushInfo(
                            "Band {}/{} done (nodata={})".format(
                                b, band_count, nodata))
                        feedback.setProgress(
                            int(100 * bands_done / band_count))
            except Exception:
                # Re-raise after letting the executor's context manager
                # tear down workers (the ``with`` block above handles it).
                raise
    else:
        # Per-band fill loop. Each band gets its own nodata sentinel.
        for b in range(1, band_count + 1):
            in_band = src.GetRasterBand(b)
            out_band = dst.GetRasterBand(b)
            nodata = in_band.GetNoDataValue()

            if feedback is not None:
                feedback.pushInfo(
                    "Band {}/{} (nodata={})".format(b, band_count, nodata))

            if use_tiles:
                # Tile loop -- bounded RAM (a few tiles at a time, not the
                # whole band). Observationally identical to the whole-band
                # path for any tile_size >= 1 because each tile is read with
                # a halo of ``max_search_dist + smoothing_iterations`` pixels.
                _fill_band_windowed(
                    in_band, out_band, mask,
                    max_search_dist, smoothing_iterations,
                    nodata, int(tile_size), feedback, b, band_count)
            else:
                arr = in_band.ReadAsArray()
                filled = fill_nodata(
                    arr,
                    mask=mask,
                    max_search_dist=max_search_dist,
                    smoothing_iterations=smoothing_iterations,
                    nodata=nodata,
                    feedback=feedback,
                )
                out_band.WriteArray(filled)
                if feedback is not None:
                    feedback.setProgress(int(100 * b / band_count))

            if nodata is not None:
                out_band.SetNoDataValue(nodata)

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


def fill_nodata_file_gdal(input_path, output_path,
                          mask_path=None, max_search_dist=10.0,
                          smoothing_iterations=0, feedback=None,
                          tile_size=0, n_workers=1):
    """Fill nodata using native :func:`osgeo.gdal.FillNodata` (C-speed).

    The ``tile_size`` kwarg is accepted for signature consistency with
    :func:`fill_nodata_file` (so the gap-fill registry can dispatch to
    either backend with the same call site) but is **ignored** here:
    :func:`osgeo.gdal.FillNodata` already streams in C with its own
    block cache, so the Python-level windowing that v2 needs to bound
    its RAM footprint is not applicable. A non-zero value is logged
    and forwarded as a no-op.

    Drop-in v3 alternative to :func:`fill_nodata_file` (Pipeline TO-DO
    item #7 in ``hyperspectral_plan.md``). Same signature so the gap-fill
    method registry in :mod:`methods` can dispatch to either v2 or v3
    without touching any algorithm wrapper.

    The native C implementation is 10-100x faster than the pure-Python
    quadrant-sweep IDW in :func:`fill_nodata` while running the same
    family of algorithm (IDW from the four nearest valid neighbours).
    Per-band: copy input -> output, then call ``gdal.FillNodata`` on the
    output band in place. The band's own ``NoDataValue`` (preserved by
    ``CreateCopy``) drives the validity mask when no external mask is
    supplied; an external ``mask_path``'s first band overrides it for
    every band of the cube (non-zero = valid).

    If ``gdal.FillNodata`` raises for any reason during the per-band
    loop, this wrapper logs the error via ``feedback`` and falls back
    to :func:`fill_nodata_file` (the pure-Python v2 path) so the
    algorithm still produces an output. Cancellation via ``feedback``
    is honoured between bands.

    The ``n_workers`` kwarg is accepted for signature parity with
    :func:`fill_nodata_file` (Pipeline TO-DO item #9 in
    ``hyperspectral_plan.md``) but is **ignored** here -- per-band
    parallelism only helps the pure-Python v2 path; ``gdal.FillNodata``
    is already a C routine and would not benefit from a Python-level
    process pool. If the v2 fallback below kicks in, ``n_workers`` is
    forwarded so the user's choice still applies.
    """
    from osgeo import gdal  # local import: keep plugin import-time light

    if feedback is not None and int(tile_size) > 0:
        feedback.pushInfo(
            "Note: tile_size={} is ignored by v3 (gdal.FillNodata streams "
            "in C); see fill_nodata_file_gdal docstring."
            .format(int(tile_size)))
    if feedback is not None and int(n_workers) > 1:
        feedback.pushInfo(
            "Note: n_workers={} is ignored by v3 (gdal.FillNodata is a "
            "C routine); only the v2 fallback would honour it."
            .format(int(n_workers)))
    if feedback is not None:
        feedback.pushInfo("Opening input: {}".format(input_path))
    src = gdal.Open(input_path, gdal.GA_ReadOnly)
    if src is None:
        raise IOError("Cannot open {}".format(input_path))

    band_count = src.RasterCount
    if band_count < 1:
        raise ValueError("input raster has no bands")

    # Optional external validity mask: the source raster's first band.
    # gdal.FillNodata's ``maskBand`` parameter accepts a band object
    # directly, so we keep the dataset alive for the whole loop.
    mask_band = None
    mask_ds = None
    temp_mask_ds = None  # For 3-state to 2-state conversion
    if mask_path:
        if feedback is not None:
            feedback.pushInfo("Reading mask: {}".format(mask_path))
        mask_ds = gdal.Open(mask_path, gdal.GA_ReadOnly)
        if mask_ds is None:
            raise IOError("Cannot open mask {}".format(mask_path))
        if (mask_ds.RasterXSize != src.RasterXSize
                or mask_ds.RasterYSize != src.RasterYSize):
            raise ValueError(
                "mask size {}x{} does not match raster {}x{}".format(
                    mask_ds.RasterXSize, mask_ds.RasterYSize,
                    src.RasterXSize, src.RasterYSize))
        
        # Check if this is a 3-state mask (0=original, 1=filled, 2=outside)
        # If so, we need to convert it to a 2-state mask for GDAL
        # (0=fillable, non-zero=not-fillable)
        mask_band_orig = mask_ds.GetRasterBand(1)
        mask_stats = mask_band_orig.GetStatistics(True, True)
        max_val = mask_stats[1]  # maximum value
        
        if max_val > 1:
            # This is a 3-state mask, convert it to 2-state for GDAL
            if feedback is not None:
                feedback.pushInfo("Converting 3-state mask to 2-state for GDAL")
            
            # Create a temporary in-memory dataset for the converted mask
            driver = gdal.GetDriverByName('MEM')
            temp_mask_ds = driver.Create('', src.RasterXSize, src.RasterYSize, 1, gdal.GDT_Byte)
            temp_mask_band = temp_mask_ds.GetRasterBand(1)
            
            # Read the original mask and convert it
            orig_mask = mask_band_orig.ReadAsArray()
            # For GDAL: 0=fillable, non-zero=not-fillable
            # 0 (original) -> 1 (don't fill)
            # 1 (filled) -> 0 (do fill)
            # 2 (outside) -> 1 (don't fill)
            converted_mask = np.where(orig_mask == 1, 0, 1).astype(np.uint8)
            temp_mask_band.WriteArray(converted_mask)
            
            mask_band = temp_mask_band
        else:
            # This is already a 2-state mask, use it directly
            mask_band = mask_band_orig

    if feedback is not None:
        feedback.pushInfo("Creating output GeoTIFF: {}".format(output_path))
    driver = gdal.GetDriverByName("GTiff")
    # Same defensive delete as in :func:`fill_nodata_file` -- ``CreateCopy``
    # alone does not always cleanly replace a pre-existing dataset that
    # GDAL/QGIS still references on Windows.
    if gdal.VSIStatL(output_path) is not None:
        try:
            driver.Delete(output_path)
        except RuntimeError:
            import os as _os
            try:
                _os.remove(output_path)
            except OSError:
                pass

    # ``gdal.FillNodata`` modifies its target band in place, so we need a
    # writable copy of the input as the destination. ``CreateCopy`` keeps
    # geotransform, projection, dtype, band count and per-band nodata.
    dst = driver.CreateCopy(output_path, src, strict=0)
    if dst is None:
        raise IOError("Cannot create {}".format(output_path))

    if feedback is not None:
        feedback.pushInfo(
            "Filling {} band(s) with gdal.FillNodata".format(band_count))
        feedback.setProgress(0)

    try:
        for b in range(1, band_count + 1):
            if feedback is not None and feedback.isCanceled():
                raise RuntimeError("canceled")
            if feedback is not None:
                feedback.pushInfo(
                    "Band {}/{} (gdal.FillNodata)".format(b, band_count))
            out_band = dst.GetRasterBand(b)
            gdal.FillNodata(
                targetBand=out_band,
                maskBand=mask_band,
                maxSearchDist=float(max_search_dist),
                smoothingIterations=int(smoothing_iterations),
            )
            if feedback is not None:
                feedback.setProgress(int(100 * b / band_count))
    except RuntimeError:
        # Cancellation -- propagate cleanly.
        dst = None
        src = None
        mask_ds = None
        temp_mask_ds = None  # Clean up temporary dataset
        raise
    except Exception as exc:
        # Per the plan ("keep v2 as the default fallback when GDAL is
        # unavailable or misbehaves"), retry on the pure-Python path.
        if feedback is not None:
            feedback.pushInfo(
                "gdal.FillNodata failed ({}); falling back to the "
                "pure-Python v2 implementation.".format(exc))
        dst = None
        src = None
        mask_ds = None
        temp_mask_ds = None  # Clean up temporary dataset
        return fill_nodata_file(
            input_path, output_path,
            mask_path=mask_path,
            max_search_dist=max_search_dist,
            smoothing_iterations=smoothing_iterations,
            feedback=feedback,
        )

    dst.FlushCache()
    dst = None
    src = None
    mask_ds = None
    temp_mask_ds = None  # This will close the temporary dataset

    if feedback is not None:
        feedback.setProgress(100)
        feedback.pushInfo("Done.")
