# -*- coding: utf-8 -*-
"""Stage B of the hyperspectral pipeline.

Mosaic a list of already-filtered, georeferenced PIKA-L GeoTIFF frames
into a single tiled BigTIFF, processed band-by-band to keep memory
bounded. Output is ``float32`` with ``NaN`` as NoData. The strategy
used for overlapping pixels is **method-dependent**: the active
mosaic method is selected at the call site via
:data:`methods.MOSAIC_METHODS` (e.g. ``v1`` first-write-wins,
``v4`` distance-weighted feather, ``v5`` histogram match + feather).

Public API (one entry per registered method, plus shared helpers):
    * :class:`MosaicInputError` — raised on incompatible inputs.
    * :func:`validate_inputs` — cross-frame compatibility check.
    * :func:`mosaic_frames` — v1 first-write-wins band-streaming writer.
    * :func:`mosaic_frames_feather` — v4 feathered / weighted blend.
    * :func:`mosaic_frames_histmatch_feather` — v5 histmatch + feather.

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
# v4 — Feathered / weighted blending
# ---------------------------------------------------------------------------


def _feather_weights(valid_mask: np.ndarray, max_feather_pixels: int) -> np.ndarray:
    """Per-pixel blend weight = distance-to-edge clipped to a ramp.

    ``valid_mask`` is a 2-D bool array (True = valid data). Pixels at the
    frame edge get weight 0; pixels deeper than ``max_feather_pixels``
    inside the valid region all get weight 1. NoData pixels are 0. The
    distance transform runs once per (chunk, band) read so memory stays
    bounded by a single 2-D float32 plane per source.
    """
    if max_feather_pixels <= 0:
        # Degenerate: behave like uniform weights inside the mask.
        return valid_mask.astype(np.float32)
    # ``distance_transform_edt`` gives Euclidean distance from each True
    # pixel to the nearest False (i.e. nearest edge / NoData) pixel.
    dist = distance_transform_edt(valid_mask)
    weights = np.minimum(
        dist / float(max_feather_pixels), 1.0).astype(np.float32)
    # Edge pixels still get tiny weights; only fully-invalid pixels are 0.
    weights[~valid_mask] = 0.0
    return weights


def mosaic_frames_feather(
    paths: list[str],
    output_path: str,
    *,
    progress: Optional[Callable[[float, str], None]] = None,
    reproject_to_first: bool = False,
    max_feather_pixels: int = 32,
) -> str:
    """Mosaic ``paths`` with distance-to-edge feathered blending.

    For every input frame we compute a per-pixel weight equal to the
    Euclidean distance from each valid pixel to the nearest invalid
    (NoData / off-frame) pixel, clipped to ``max_feather_pixels``. The
    output pixel is then ``sum(w * value) / sum(w)`` across overlapping
    frames; pixels covered by no frame stay NaN. Spectrally less
    faithful than v1 (overlap zones blur a few frames together) but
    produces seamless visual mosaics — see the v4 entry in
    ``hyperspectral_plan.md``.

    Same call shape as :func:`mosaic_frames` so the registry can dispatch
    interchangeably. Output is ``float32`` BigTIFF with NaN as NoData.
    Frames are processed band-by-band and in chunks of
    ``_MAX_OPEN_SOURCES`` to keep both file descriptors and memory bounded.
    """
    info = validate_inputs(paths, reproject_to_first=reproject_to_first)
    xres, yres = info["res"]
    band_count = info["count"]
    src_nodata = info["nodata"]
    ref_crs = info["crs"]

    # Optional reprojection pass — same logic as ``mosaic_frames``; we
    # rewrite ``effective_paths`` to point at temp GeoTIFFs reprojected
    # onto the first frame's grid so the rest of the function can stay
    # uniform.
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

    path_chunks = [
        effective_paths[i:i + _MAX_OPEN_SOURCES]
        for i in range(0, len(effective_paths), _MAX_OPEN_SOURCES)
    ]

    try:
        with rasterio.open(output_path, "w", **profile) as dst:
            total_steps = len(path_chunks) * band_count
            step = 0
            # One pair of accumulators per band; allocated once per band
            # and reused across all chunks. We accumulate ``Σ(w·v)`` and
            # ``Σ(w)`` for that band, then divide once at the end.
            for b in range(1, band_count + 1):
                wsum = np.zeros((height, width), dtype=np.float32)
                wvsum = np.zeros((height, width), dtype=np.float32)

                for chunk_idx, chunk in enumerate(path_chunks):
                    sources = [rasterio.open(p) for p in chunk]
                    try:
                        # For each source, read its band ``b`` reprojected
                        # onto the output grid (via merge on a one-source
                        # list — same trick the rest of the file uses).
                        # Then build a feather weight from the validity
                        # mask and accumulate.
                        for src in sources:
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
                            # Normalise per-source NoData to NaN so the
                            # validity mask is uniform downstream.
                            if (src_nodata is not None
                                    and not nodata_is_nan):
                                arr[arr == np.float32(src_nodata)] = nan

                            valid = np.isfinite(arr)
                            if not valid.any():
                                continue
                            w = _feather_weights(
                                valid, int(max_feather_pixels))
                            # Replace NaN with 0 inside the weighted sum
                            # so NaN never propagates through addition.
                            contrib = np.where(valid, arr, 0.0).astype(
                                np.float32)
                            wvsum += w * contrib
                            wsum += w
                    finally:
                        for s in sources:
                            s.close()

                    step += 1
                    if progress is not None:
                        progress(
                            step / total_steps,
                            "feather chunk {}/{} band {}/{}".format(
                                chunk_idx + 1, len(path_chunks),
                                b, band_count),
                        )

                # Divide once per band; pixels covered by no frame stay
                # NaN. Suppress the divide-by-zero warning for empty
                # pixels — they are explicitly masked back to NaN.
                with np.errstate(invalid="ignore", divide="ignore"):
                    out = np.where(wsum > 0.0, wvsum / wsum, nan).astype(
                        np.float32)
                dst.write(out, b)

            if descriptions:
                for i, desc in enumerate(descriptions, start=1):
                    if desc:
                        dst.set_band_description(i, desc)
    finally:
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
# v5 — Histogram match (mean/std moment matching) + feather
# ---------------------------------------------------------------------------


def _moment_match_gain_offset(
    ref_vals: np.ndarray,
    src_vals: np.ndarray,
) -> tuple[float, float]:
    """Compute (gain, offset) so that ``gain * src + offset`` matches
    the mean and std of ``ref_vals``.

    Both inputs are 1-D arrays of finite samples. We deliberately use
    simple mean/std moment matching (not a CDF / histogram-equalisation
    match): it is junior-readable, monotonic, easy to reason about, and
    enough to kill the brightness / contrast jumps between adjacent
    PIKA-L strips that v4's feather alone cannot hide. A more
    sophisticated CDF-based match is intentionally not used here.
    """
    if ref_vals.size == 0 or src_vals.size == 0:
        return 1.0, 0.0
    ref_mean = float(np.mean(ref_vals))
    ref_std = float(np.std(ref_vals))
    src_mean = float(np.mean(src_vals))
    src_std = float(np.std(src_vals))
    # std == 0 in either side → matching is undefined; skip.
    if ref_std == 0.0 or src_std == 0.0:
        return 1.0, 0.0
    gain = ref_std / src_std
    offset = ref_mean - gain * src_mean
    return gain, offset


def mosaic_frames_histmatch_feather(
    paths: list[str],
    output_path: str,
    *,
    progress: Optional[Callable[[float, str], None]] = None,
    reproject_to_first: bool = False,
    max_feather_pixels: int = 32,
) -> str:
    """Mosaic ``paths`` with linear histogram matching + feather blend.

    Variant of :func:`mosaic_frames_feather` that first rescales each
    non-reference frame so its per-band mean and std match the
    reference frame's per-band mean and std on their overlap, then runs
    the SAME feather blend as v4 to hide the residual seams.

    Reference frame selection rule: the FIRST input frame
    (``paths[0]``). Simple, deterministic, and matches the order the
    user provides. No statistics from overlap → fall back to the
    reference's global mean/std for that band. ``std == 0`` in either
    side → skip matching for that band (gain = 1, offset = 0).

    ⚠️ Warning: this method ALTERS per-pixel spectral values. Use it
    only when visual continuity matters more than spectral accuracy.
    For classifiers, indices, or any spectral analysis, use v1 (or v4
    with a single band of interest) instead.

    Same call shape as :func:`mosaic_frames_feather`. Output is
    ``float32`` BigTIFF with NaN as NoData.
    """
    info = validate_inputs(paths, reproject_to_first=reproject_to_first)
    xres, yres = info["res"]
    band_count = info["count"]
    src_nodata = info["nodata"]
    ref_crs = info["crs"]

    # Optional reprojection pass — same logic as v1 / v4.
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

    # Reference frame is the first input. We open it on demand per band
    # (cheap — one rasterio.open per band) so we don't keep N file
    # descriptors alive across the whole loop. Note we deliberately do
    # NOT chunk the inputs the way v1 / v4 do across _MAX_OPEN_SOURCES,
    # because we open and close one source at a time here (gain/offset
    # is computed against the reference, accumulated, then released).
    ref_path = effective_paths[0]

    def _read_on_grid(src, band_index):
        """Read ``band_index`` from ``src`` reprojected onto the output
        grid, returning a 2-D float32 array with NaN as NoData."""
        arr_3d, _ = merge(
            [src],
            indexes=[band_index],
            method="first",
            dtype="float32",
            nodata=nan,
            res=(xres, yres),
            bounds=out_bounds,
        )
        a = arr_3d[0]
        if a.dtype != np.float32:
            a = a.astype(np.float32)
        if src_nodata is not None and not nodata_is_nan:
            a[a == np.float32(src_nodata)] = nan
        return a

    try:
        with rasterio.open(output_path, "w", **profile) as dst:
            total_steps = len(effective_paths) * band_count
            step = 0

            for b in range(1, band_count + 1):
                # Per-band feather accumulators (same as v4).
                wsum = np.zeros((height, width), dtype=np.float32)
                wvsum = np.zeros((height, width), dtype=np.float32)

                # Read the reference frame's band onto the output grid
                # once per band; we need it both for histogram matching
                # statistics AND to contribute to the feather blend.
                with rasterio.open(ref_path) as ref_src:
                    ref_arr = _read_on_grid(ref_src, b)
                ref_valid = np.isfinite(ref_arr)

                # Reference frame's own contribution (gain = 1, offset = 0).
                if ref_valid.any():
                    w_ref = _feather_weights(
                        ref_valid, int(max_feather_pixels))
                    contrib_ref = np.where(
                        ref_valid, ref_arr, 0.0).astype(np.float32)
                    wvsum += w_ref * contrib_ref
                    wsum += w_ref

                step += 1
                if progress is not None:
                    progress(
                        step / total_steps,
                        "histmatch+feather ref band {}/{}".format(
                            b, band_count),
                    )

                # Precompute reference global stats for the no-overlap
                # fallback so we don't recompute per source.
                ref_finite = ref_arr[ref_valid] if ref_valid.any() else None

                for src_path in effective_paths[1:]:
                    with rasterio.open(src_path) as src:
                        src_arr = _read_on_grid(src, b)
                    src_valid = np.isfinite(src_arr)
                    if not src_valid.any():
                        step += 1
                        if progress is not None:
                            progress(
                                step / total_steps,
                                "histmatch+feather skip empty band {}/{}"
                                .format(b, band_count),
                            )
                        continue

                    # Compute (gain, offset) on overlap pixels.
                    overlap = ref_valid & src_valid
                    if overlap.any():
                        gain, offset = _moment_match_gain_offset(
                            ref_arr[overlap], src_arr[overlap])
                    elif ref_finite is not None:
                        # No overlap: fall back to reference's GLOBAL
                        # mean/std vs this source's global mean/std.
                        gain, offset = _moment_match_gain_offset(
                            ref_finite, src_arr[src_valid])
                    else:
                        # Reference itself is empty for this band.
                        gain, offset = 1.0, 0.0

                    # Apply gain/offset across the whole frame's band.
                    matched = np.where(
                        src_valid,
                        np.float32(gain) * src_arr + np.float32(offset),
                        np.float32(0.0),
                    ).astype(np.float32)

                    # Feather blend, identical to v4 inner body.
                    w = _feather_weights(
                        src_valid, int(max_feather_pixels))
                    wvsum += w * matched
                    wsum += w

                    step += 1
                    if progress is not None:
                        progress(
                            step / total_steps,
                            "histmatch+feather band {}/{}".format(
                                b, band_count),
                        )

                with np.errstate(invalid="ignore", divide="ignore"):
                    out = np.where(wsum > 0.0, wvsum / wsum, nan).astype(
                        np.float32)
                dst.write(out, b)

            if descriptions:
                for i, desc in enumerate(descriptions, start=1):
                    if desc:
                        dst.set_band_description(i, desc)
    finally:
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
