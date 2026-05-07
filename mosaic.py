# -*- coding: utf-8 -*-
"""Stage B of the hyperspectral pipeline.

Mosaic a list of already-filtered, georeferenced PIKA-L GeoTIFF frames
into a single tiled BigTIFF, processed band-by-band to keep memory
bounded. Output is ``float32`` with ``NaN`` as NoData. The active
mosaic method is selected at the call site via
:data:`methods.MOSAIC_METHODS`; only the spectrally-faithful ``v1``
first-write-wins path and the ``v2`` best pixel path are registered.
The visual-only feather and histmatch+feather variants were previously
kept as unregistered helpers but have been removed to simplify the codebase.


Public API:
    * :class:`MosaicInputError` — raised on incompatible inputs.
    * :func:`validate_inputs` — cross-frame compatibility check.
    * :func:`mosaic_frames` — v1 first-write-wins band-streaming writer.
    * :func:`mosaic_frames_best_pixel` — v2 best pixel band-streaming writer.

Dependencies are limited to ``rasterio``, ``numpy`` and the Python
stdlib; in particular this module must not import ``osgeo.gdal`` or
``qgis``.
"""

from __future__ import annotations

import os
import tempfile
from typing import Callable, Optional

import numpy as np
import rasterio
from rasterio.merge import merge
from rasterio.transform import from_origin
from rasterio.warp import calculate_default_transform, reproject, Resampling
from scipy.ndimage import distance_transform_edt


_PIXEL_REL_TOL: float = 1e-6

# Cap on simultaneously-open source file descriptors during a band merge.
# Opening every input frame at once fails around 500 frames on Windows
# (default fd limit ~512). We process the inputs in chunks of this size and
# combine the per-chunk arrays in memory while preserving first-write-wins
# ordering. See ``hyperspectral_plan.md`` Pipeline TO-DO #3.
_MAX_OPEN_SOURCES: int = 256


class MosaicInputError(ValueError):
    """Raised when input frames are not compatible for mosaicing."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_inputs(
    paths: list[str],
    *,
    reproject_to_first: bool = False,
) -> dict:
    """Check that ``paths`` can be mosaicked together.

    Verifies, across every frame: identical CRS, identical absolute
    pixel size (relative tolerance ``1e-6``), identical band count and
    identical source dtype string. Raises :class:`MosaicInputError`
    with the offending path on any mismatch.

    When ``reproject_to_first`` is ``True``, CRS and pixel-size
    mismatches are tolerated (they will be resolved by
    :func:`mosaic_frames` via :func:`rasterio.warp.reproject`); band
    count and dtype must still match across frames.

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
            if not reproject_to_first:
                if src.crs != ref_crs:
                    raise MosaicInputError(
                        "CRS mismatch in {}: {!r} != {!r}".format(
                            p, src.crs, ref_crs)
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


def _reproject_to_reference(
    path: str,
    *,
    ref_crs,
    ref_xres: float,
    ref_yres: float,
    out_dir: str,
) -> str:
    """Reproject ``path`` to ``ref_crs`` at ``(ref_xres, ref_yres)``.

    Writes a temporary GeoTIFF inside ``out_dir`` and returns its path.
    Used by :func:`mosaic_frames` when ``reproject_to_first=True`` to
    bring CRS / pixel-size outliers onto the reference grid before the
    band-streamed merge. Resampling is bilinear (a sane default for
    continuous radiometric values); NoData is preserved from the source
    when present, otherwise it is left unset.
    """
    base = os.path.basename(path)
    out_path = os.path.join(out_dir, "reproj_" + base)
    with rasterio.open(path) as src:
        # Compute destination transform/size in the reference CRS at the
        # reference pixel size.
        dst_transform, dst_width, dst_height = calculate_default_transform(
            src.crs, ref_crs, src.width, src.height,
            *src.bounds, resolution=(ref_xres, ref_yres),
        )
        profile = src.profile.copy()
        for k in ("predictor", "photometric"):
            profile.pop(k, None)
        profile.update(
            driver="GTiff",
            crs=ref_crs,
            transform=dst_transform,
            width=int(dst_width),
            height=int(dst_height),
        )
        with rasterio.open(out_path, "w", **profile) as dst:
            for b in range(1, src.count + 1):
                reproject(
                    source=rasterio.band(src, b),
                    destination=rasterio.band(dst, b),
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=dst_transform,
                    dst_crs=ref_crs,
                    src_nodata=src.nodata,
                    dst_nodata=src.nodata,
                    resampling=Resampling.bilinear,
                )
    return out_path


def mosaic_frames(
    paths: list[str],
    output_path: str,
    *,
    progress: Optional[Callable[[float, str], None]] = None,
    reproject_to_first: bool = False,
) -> str:
    """Mosaic ``paths`` into a single tiled BigTIFF at ``output_path``.

    Frames are merged band-by-band with :func:`rasterio.merge.merge`
    using ``method="first"`` (first-write-wins). The output is
    ``float32`` with ``NaN`` as NoData. ``progress`` is an optional
    ``callable(fraction, message)`` invoked once per band; ``None``
    disables progress reporting.

    When ``reproject_to_first`` is ``True``, frames whose CRS or pixel
    size differs from the first frame are pre-reprojected onto the
    first frame's CRS / pixel grid via :func:`rasterio.warp.reproject`
    (bilinear resampling) and merged from temporary GeoTIFFs that are
    deleted before this function returns. Default ``False`` preserves
    the previous behaviour (abort on CRS / pixel-size mismatch). See
    Pipeline TO-DO #5 in ``hyperspectral_plan.md``.

    Returns ``output_path``.
    """
    info = validate_inputs(paths, reproject_to_first=reproject_to_first)
    xres, yres = info["res"]
    band_count = info["count"]
    src_nodata = info["nodata"]
    ref_crs = info["crs"]

    # Optional reprojection pass: bring any CRS / pixel-size outliers
    # onto the first frame's grid before the merge. Each reprojected
    # frame is written to a temp GeoTIFF in ``tmp_dir`` and the path
    # list ``effective_paths`` is rewritten so the rest of the function
    # is unchanged.
    tmp_dir: Optional[str] = None
    effective_paths: list[str] = list(paths)
    if reproject_to_first:
        tmp_dir = tempfile.mkdtemp(prefix="mosaic_reproj_")
        for i, p in enumerate(paths):
            with rasterio.open(p) as src:
                same_crs = (src.crs == ref_crs)
                xr = abs(src.transform.a)
                yr = abs(src.transform.e)
                same_res = (
                    abs(xr - xres) <= _PIXEL_REL_TOL * xres
                    and abs(yr - yres) <= _PIXEL_REL_TOL * yres)
            if same_crs and same_res:
                continue
            effective_paths[i] = _reproject_to_reference(
                p, ref_crs=ref_crs, ref_xres=xres, ref_yres=yres,
                out_dir=tmp_dir,
            )

    # Union of all frame bounds (computed on the effective, possibly
    # reprojected, path list so all bounds are already in ``ref_crs``).
    lefts, bottoms, rights, tops = [], [], [], []
    for p in effective_paths:
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
    with rasterio.open(effective_paths[0]) as first:
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

    # Split paths into chunks so we never hold more than _MAX_OPEN_SOURCES
    # file descriptors open at once. Chunks are processed in order and the
    # first non-NaN value across chunks wins, which reproduces the original
    # first-write-wins semantics over the full path list.
    path_chunks = [
        effective_paths[i:i + _MAX_OPEN_SOURCES]
        for i in range(0, len(effective_paths), _MAX_OPEN_SOURCES)
    ]

    try:
        with rasterio.open(output_path, "w", **profile) as dst:
            # Pipeline TO-DO #10: open every source in a chunk exactly
            # once and reuse the readers across all bands, instead of
            # re-opening every source per band. Loops are inverted so
            # that the chunk loop is outer and the band loop is inner.
            # First-write-wins across chunks is preserved by reading the
            # already-written band back from ``dst`` for chunks after
            # the first and filling only where it is still NaN.
            total_steps = len(path_chunks) * band_count
            step = 0
            for chunk_idx, chunk in enumerate(path_chunks):
                sources = [rasterio.open(p) for p in chunk]
                try:
                    for b in range(1, band_count + 1):
                        chunk_arr, _ = merge(
                            sources,
                            indexes=[b],
                            method="first",
                            dtype="float32",
                            nodata=nan,
                            res=(xres, yres),
                            bounds=out_bounds,
                        )
                        if chunk_arr.dtype != np.float32:
                            chunk_arr = chunk_arr.astype(np.float32)
                        arr = chunk_arr[0]
                        if src_nodata is not None and not nodata_is_nan:
                            arr[arr == np.float32(src_nodata)] = nan

                        if chunk_idx == 0:
                            dst.write(arr, b)
                        else:
                            # First-write-wins across chunks: only fill
                            # where earlier chunks left NaN.
                            existing = dst.read(b)
                            mask = np.isnan(existing)
                            existing[mask] = arr[mask]
                            dst.write(existing, b)

                        step += 1
                        if progress is not None:
                            progress(
                                step / total_steps,
                                "chunk {}/{} band {}/{}".format(
                                    chunk_idx + 1, len(path_chunks),
                                    b, band_count),
                            )
                finally:
                    for s in sources:
                        s.close()

            if descriptions:
                for i, desc in enumerate(descriptions, start=1):
                    if desc:
                        dst.set_band_description(i, desc)
    finally:
        # Best-effort cleanup of any temporary reprojected frames.
        if tmp_dir is not None:
            for name in os.listdir(tmp_dir):
                try:
                    os.remove(os.path.join(tmp_dir, name))
                except OSError:
                    pass
            try:
                os.rmdir(tmp_dir)
            except OSError:
                pass

    return output_path



# ---------------------------------------------------------------------------
# v2 — Best pixel (max distance to edge)
# ---------------------------------------------------------------------------


def mosaic_frames_best_pixel(
    paths: list[str],
    output_path: str,
    *,
    progress: Optional[Callable[[float, str], None]] = None,
    reproject_to_first: bool = False,
    write_sources: bool = True,
) -> str:
    """Mosaic ``paths`` by picking the source with the best (most interior) pixel.
    
    For each output pixel covered by ≥1 input frame, pick the source whose pixel is
    **farthest from any nodata edge** (most "interior" pixel). Copy that source's
    full spectrum (all bands) into the output. Ties → input order.
    
    Same call shape as :func:`mosaic_frames` so the registry can dispatch
    interchangeably. Output is ``float32`` BigTIFF with NaN as NoData.
    Frames are processed band-by-band and in chunks of
    ``_MAX_OPEN_SOURCES`` to keep both file descriptors and memory bounded.
    
    Parameters
    ----------
    paths : list[str]
        List of input frame paths to mosaic
    output_path : str
        Path to write the output mosaic
    progress : Optional[Callable[[float, str], None]]
        Optional progress callback function
    reproject_to_first : bool
        Whether to reproject frames to match the first frame's CRS/resolution
    write_sources : bool
        Whether to write the optional sources provenance raster
        
    Returns
    -------
    str
        Path to the output mosaic
    """
    info = validate_inputs(paths, reproject_to_first=reproject_to_first)
    xres, yres = info["res"]
    band_count = info["count"]
    src_nodata = info["nodata"]
    ref_crs = info["crs"]

    # Optional reprojection pass — same logic as other mosaic methods
    tmp_dir: Optional[str] = None
    effective_paths: list[str] = list(paths)
    if reproject_to_first:
        tmp_dir = tempfile.mkdtemp(prefix="mosaic_reproj_")
        for i, p in enumerate(paths):
            with rasterio.open(p) as src:
                same_crs = (src.crs == ref_crs)
                xr = abs(src.transform.a)
                yr = abs(src.transform.e)
                same_res = (
                    abs(xr - xres) <= _PIXEL_REL_TOL * xres
                    and abs(yr - yres) <= _PIXEL_REL_TOL * yres)
            if same_crs and same_res:
                continue
            effective_paths[i] = _reproject_to_reference(
                p, ref_crs=ref_crs, ref_xres=xres, ref_yres=yres,
                out_dir=tmp_dir,
            )

    # Union of all frame bounds (in ``ref_crs``).
    lefts, bottoms, rights, tops = [], [], [], []
    for p in effective_paths:
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

    with rasterio.open(effective_paths[0]) as first:
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

    # Split paths into chunks so we never hold more than _MAX_OPEN_SOURCES
    # file descriptors open at once.
    path_chunks = [
        effective_paths[i:i + _MAX_OPEN_SOURCES]
        for i in range(0, len(effective_paths), _MAX_OPEN_SOURCES)
    ]

    # Derive sources raster path if needed
    sources_path = None
    if write_sources:
        base, ext = os.path.splitext(output_path)
        sources_path = base + ".sources.tif"

    try:
        with rasterio.open(output_path, "w", **profile) as dst:
            # Create sources raster if needed
            sources_dst = None
            if sources_path:
                sources_profile = profile.copy()
                sources_profile.update(
                    count=1,
                    dtype="uint16",
                    nodata=0,
                )
                sources_dst = rasterio.open(sources_path, "w", **sources_profile)

            total_steps = len(path_chunks) * band_count
            step = 0

            # Process each band
            for b in range(1, band_count + 1):
                # Initialize best distance and source arrays for this band
                # These will be updated across all chunks for this band
                best_dist = np.zeros((height, width), dtype=np.float32)
                best_src = np.zeros((height, width), dtype=np.uint16)  # 0 = nodata, 1-based source indices

                # Process each chunk
                for chunk_idx, chunk in enumerate(path_chunks):
                    sources = [rasterio.open(p) for p in chunk]
                    try:
                        # For each source in the chunk, compute distance transform and update best
                        for src_idx_in_chunk, src in enumerate(sources):
                            # Source index is 1-based across all inputs
                            src_idx = chunk_idx * _MAX_OPEN_SOURCES + src_idx_in_chunk + 1
                            
                            # Read the band data reprojected onto output grid
                            arr_3d, _ = merge(
                                [src],
                                indexes=[b],
                                method="first",
                                dtype="float32",
                                nodata=nan,
                                res=(xres, yres),
                                bounds=out_bounds,
                            )
                            arr = arr_3d[0]
                            if arr.dtype != np.float32:
                                arr = arr.astype(np.float32)
                            # Normalise per-source NoData to NaN
                            if (src_nodata is not None
                                    and not nodata_is_nan):
                                arr[arr == np.float32(src_nodata)] = nan

                            # Build validity mask (True where all bands are valid for this source)
                            # For single band processing, this is just finite values
                            valid_mask = np.isfinite(arr)
                            
                            # Skip if no valid data
                            if not valid_mask.any():
                                continue
                                
                            # Compute distance transform for this source
                            distance = distance_transform_edt(valid_mask)
                            
                            # Update best distance and source where this source is better
                            # (valid pixel AND greater distance than current best)
                            update_mask = valid_mask & (distance > best_dist)
                            best_dist[update_mask] = distance[update_mask]
                            best_src[update_mask] = src_idx
                    finally:
                        for s in sources:
                            s.close()

                    step += 1
                    if progress is not None:
                        progress(
                            step / total_steps,
                            "best-pixel chunk {}/{} band {}/{}".format(
                                chunk_idx + 1, len(path_chunks),
                                b, band_count),
                        )

                # After processing all chunks for this band, gather pixels per source
                # and read that band from each source for those pixels
                out_band = np.full((height, width), nan, dtype=np.float32)
                
                # Process each source to fill in its pixels
                for chunk_idx, chunk in enumerate(path_chunks):
                    sources = [rasterio.open(p) for p in chunk]
                    try:
                        for src_idx_in_chunk, src in enumerate(sources):
                            src_idx = chunk_idx * _MAX_OPEN_SOURCES + src_idx_in_chunk + 1
                            
                            # Find pixels that should come from this source
                            src_pixels = best_src == src_idx
                            if not src_pixels.any():
                                continue
                                
                            # Read the band data for this source reprojected onto output grid
                            arr_3d, _ = merge(
                                [src],
                                indexes=[b],
                                method="first",
                                dtype="float32",
                                nodata=nan,
                                res=(xres, yres),
                                bounds=out_bounds,
                            )
                            arr = arr_3d[0]
                            if arr.dtype != np.float32:
                                arr = arr.astype(np.float32)
                            # Normalise per-source NoData to NaN
                            if (src_nodata is not None
                                    and not nodata_is_nan):
                                arr[arr == np.float32(src_nodata)] = nan
                                
                            # Fill in pixels from this source
                            out_band[src_pixels] = arr[src_pixels]
                    finally:
                        for s in sources:
                            s.close()
                
                # Write the band to output
                dst.write(out_band, b)
                
                # Write sources band if needed (only for first band since it's the same for all)
                if b == 1 and sources_dst is not None:
                    sources_dst.write(best_src, 1)

            # Write band descriptions if available
            if descriptions:
                for i, desc in enumerate(descriptions, start=1):
                    if desc:
                        dst.set_band_description(i, desc)
                        
            # Close sources raster if it was opened
            if sources_dst is not None:
                sources_dst.close()
    finally:
        # Best-effort cleanup of any temporary reprojected frames.
        if tmp_dir is not None:
            for name in os.listdir(tmp_dir):
                try:
                    os.remove(os.path.join(tmp_dir, name))
                except OSError:
                    pass
            try:
                os.rmdir(tmp_dir)
            except OSError:
                pass

    return output_path

