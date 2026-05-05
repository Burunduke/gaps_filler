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

import os
import tempfile
from typing import Callable, Optional

import numpy as np
import rasterio
from rasterio.merge import merge
from rasterio.transform import from_origin
from rasterio.warp import calculate_default_transform, reproject, Resampling


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
            for b in range(1, band_count + 1):
                combined = None  # type: Optional[np.ndarray]
                for chunk in path_chunks:
                    sources = [rasterio.open(p) for p in chunk]
                    try:
                        chunk_arr, _ = merge(
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

                    if chunk_arr.dtype != np.float32:
                        chunk_arr = chunk_arr.astype(np.float32)

                    if combined is None:
                        combined = chunk_arr
                    else:
                        # First-write-wins across chunks: only fill where
                        # the earlier chunks left NaN.
                        mask = np.isnan(combined)
                        combined[mask] = chunk_arr[mask]

                arr = combined
                if src_nodata is not None and not nodata_is_nan:
                    arr[arr == np.float32(src_nodata)] = nan

                dst.write(arr[0], b)

                if progress is not None:
                    progress(
                        b / band_count, "band {}/{}".format(b, band_count))

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
