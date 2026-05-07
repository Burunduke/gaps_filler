# Plan

## Phase P1 — Quality wins (data correctness)
1. **GDAL fill as default.** Reorder [`methods.GAP_FILL_METHODS`](methods.py:1) so `v3_gdal_fillnodata` is index 0. No code logic change.
2. **New `v2_best_pixel` mosaic.** Per frame compute `scipy.ndimage.distance_transform_edt(valid_mask)`; per output pixel pick the source with max distance (ties → input order); copy that source's full spectrum. Write optional provenance raster `<output>.sources.tif` (uint16 source index, 0 = nodata). Recycle the chunked outer loop from the dead [`mosaic.mosaic_frames_feather()`](mosaic.py:383) skeleton. Register in [`methods.MOSAIC_METHODS`](methods.py:1). Delete dead code: [`mosaic._feather_weights()`](mosaic.py:361), [`mosaic.mosaic_frames_feather()`](mosaic.py:383), [`mosaic.mosaic_frames_histmatch_feather()`](mosaic.py:607).
3a. **Mosaic-quality metrics from existing rasters.** Extend [`mosaic_quality.compare_rasters()`](mosaic_quality.py:91) with: `coverage_ratio`, `filled_pixel_ratio` (from `<output>.fillmask.tif`), per-band `nodata_fraction`, filled-only stats, overlap-only stats.
3b. **Provenance-based metrics.** After step 2, add `overlap_ratio` and `source_contribution_stats` consuming `<output>.sources.tif`.

## Phase P2 — Hyperspectral metadata
4. `envi_io.py` — thin wrapper around `spectral.io.envi.read_envi_header` (vendored at [`spectral/spectral/io/`](spectral/spectral/io)). ~50 lines. Declare `spectral` optional; raise a clear error if missing.
5. `models.py` — `FlightLineMeta` dataclass bundling `bil/hdr/times/lcf` paths. Add `RasterCubeMeta` only after step 4 has a consumer. **No `ProcessingProfile`** (registry already enforces correctness).

## Phase P3 — Raw airborne georef (PIKA-L pushbroom)
6. New `airborne_georef.py` module, separate from [`pipeline.py`](pipeline.py:1).
7. **Flat-earth MVP.** `numpy.loadtxt` on `.lcf`/`.times` → `numpy.interp` per-line pose → [`pymap3d.aer.aer2enu()`](pymap3d/src/pymap3d/aer.py:1) + [`pymap3d.enu.enu2geodetic()`](pymap3d/src/pymap3d/enu.py:1) for ray-to-ground at constant `z=ground_alt` → [`pyproj.Transformer`](pyproj/) to target CRS → write GeoTIFF via `rasterio.warp.reproject` with geoloc arrays (or `gdal.Warp` with GCPs). **Note:** ODM is frame-camera; do not model on it.
8. **DEM-aware variant.** Replace flat intersection with ray-DEM cast; sample DEM via [`rioxarray`](rioxarray/).

## Phase P4 — Polish
9. Document chunked-processing defaults in [`project_review.md`](project_review.md:1). No code change.
10. Improve parameter help strings in algorithm wrappers; clearer report filenames; explicit temp-cleanup messages. (Drop obsolete "visual-only warnings" / "profile-aware defaults".)

## Ordering & dependencies
- 3b → after 2
- 5 → after 4
- 7 → after 4 and 5
- Dead-code deletion in [`mosaic.py`](mosaic.py:1) happens inside step 2.

## Dropped from previous plan
- `ProcessingProfile` / `scientific_mode` / `allow_visual_methods` — visual mosaics already unregistered.
- ODM-style frame-camera modeling — does not apply to pushbroom.
- `otb`, `micmac` runtime deps — too heavy for a junior-friendly plugin; reference reading only.
