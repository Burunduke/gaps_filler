# Project Review — `gaps_filler`

## Project Overview

`gaps_filler` is a QGIS 3 plugin (per [`metadata.txt`](metadata.txt:8)) for processing PIKA-L hyperspectral drone frames into a single gap-filled orthomosaic. It started as a Plugin Builder skeleton and has grown into a Processing-framework provider that exposes five algorithms (filter / mosaic / fill / end-to-end pipeline / quality assessment).

## Architecture & module map

The plugin is organised as a thin QGIS-plugin entry layer over plain-Python core modules. Core modules have **no Qt dependency** (only `numpy`, `osgeo.gdal`, `rasterio`, optional `scipy` / `skimage`) and are unit-testable without QGIS. Qt-side wrappers expose them as [`QgsProcessingAlgorithm`](https://api.qgis.org/api/classQgsProcessingAlgorithm.html) subclasses; QGIS auto-builds the dialogs.

```
input rasters ──▶ filter ──▶ mosaic ──▶ fill_nodata ──▶ filled mosaic
                  (Stage A)  (Stage B)  (Stage C)
                                                   ──▶ mosaic_quality (vs reference)
```

| File | Role |
|---|---|
| [`__init__.py`](__init__.py) | QGIS entry point — `classFactory(iface)` returns a `GapsFiller`. |
| [`gaps_filler.py`](gaps_filler.py) | Plugin class. Slim: `__init__` / `initGui` / `unload` register the Processing provider. No menu / toolbar / dialog. |
| [`gaps_filler_provider.py`](gaps_filler_provider.py) | [`QgsProcessingProvider`](https://api.qgis.org/api/classQgsProcessingProvider.html) (id `gapsfiller`); `loadAlgorithms()` registers all five algorithms. |
| [`methods.py`](methods.py) | Per-stage method registries (`FRAME_FILTER_METHODS`, `MOSAIC_METHODS`, `GAP_FILL_METHODS`). Each is a list of `{id, label, tooltip, func}` dicts; `labels()` and `tooltip_block()` render dropdown options + help. The dispatch source of truth for the per-stage method enums in every QGIS algorithm. |
| [`frame_filter.py`](frame_filter.py) | Stage A core (`rasterio` + `numpy`). [`is_bad_frame()`](frame_filter.py:62) single-frame heuristic check; [`filter_frames()`](frame_filter.py:154) batch wrapper; [`FilterThresholds`](frame_filter.py:43) dataclass bundling 8 tunables; `THRESHOLD_PRESETS` + [`preset_thresholds()`](frame_filter.py:95) for Permissive / Default / Strict bundles. |
| [`mosaic.py`](mosaic.py) | Stage B core. [`MosaicInputError`](mosaic.py:33), [`validate_inputs()`](mosaic.py:42), [`mosaic_frames()`](mosaic.py:96) (band-streaming, first-write-wins, NaN nodata, float32, BigTIFF); chunked merge capped at `_MAX_OPEN_SOURCES = 256`; optional CRS reprojection helper [`_reproject_to_reference()`](mosaic.py:118). |
| [`fill_nodata.py`](fill_nodata.py) | Stage C core. Array-level [`fill_nodata()`](fill_nodata.py:156) (numpy IDW quadrant sweeps + smoothing); file-level v2 [`fill_nodata_file()`](fill_nodata.py:305) (per-band loop, optional windowed/tiled mode, optional `ThreadPoolExecutor` parallelism); v3 [`fill_nodata_file_gdal()`](fill_nodata.py:436) (`gdal.FillNodata` C backend with v2 fallback); public [`write_interior_fill_mask()`](fill_nodata.py:32) for footprint-aware masks. |
| [`pipeline.py`](pipeline.py) | Orchestrator chaining A → B → C. [`run_pipeline()`](pipeline.py:32) returns `{input_count, kept_count, rejected, output_path, band_count}`; writes `<output>.rejected.csv` audit; honours optional `progress`, `log`, `is_canceled`, `gap_fill_func`, `tile_size`, `n_workers`, `fill_only_interior`, `max_interior_gap_px`, `reproject_to_first`, `thresholds`. |
| [`mosaic_quality.py`](mosaic_quality.py) | Quality assessment (`numpy` + `osgeo.gdal`, optional `skimage`). [`compare_rasters()`](mosaic_quality.py:91) — per-band RMSE / MAE / PSNR / SSIM, plus whole-cube SAM / SAM_DEG; aggregates into MEAN / WORST / P05 with offending band indices; reference is warped onto the mosaic grid via in-memory `gdal.Warp` so unequal extents are handled. [`format_report()`](mosaic_quality.py:222) renders a per-band table + footer. |
| [`gaps_filler_algorithm.py`](gaps_filler_algorithm.py) | [`FillNoDataAlgorithm`](gaps_filler_algorithm.py:22) — Stage C standalone (`gapsfiller:fillnodata`). |
| [`frame_filter_algorithm.py`](frame_filter_algorithm.py) | [`FrameFilterAlgorithm`](frame_filter_algorithm.py:24) — Stage A standalone (`gapsfiller:frame_filter`); copies kept frames to a folder, writes a rejection report. |
| [`mosaic_algorithm.py`](mosaic_algorithm.py) | [`MosaicAlgorithm`](mosaic_algorithm.py:20) — Stage B standalone (`gapsfiller:mosaic_frames`). |
| [`hyperspectral_algorithm.py`](hyperspectral_algorithm.py) | [`HyperspectralPipelineAlgorithm`](hyperspectral_algorithm.py:22) — end-to-end (`gapsfiller:hyperspectral_pipeline`). |
| [`mosaic_quality_algorithm.py`](mosaic_quality_algorithm.py) | [`MosaicQualityAlgorithm`](mosaic_quality_algorithm.py:21) — quality vs reference (`gapsfiller:mosaic_quality`). |
| [`envi_io.py`](envi_io.py) | ENVI `.hdr` reader — thin wrapper over `spectral.io.envi.read_envi_header`. Public: [`EnviHeader`](envi_io.py:16) frozen dataclass + [`read_envi_header()`](envi_io.py:31). `spectral` is lazy-imported (optional dep). |
| [`models.py`](models.py) | Passive path-bundling for PIKA-L flight lines. Public: [`FlightLineMeta`](models.py:15) frozen dataclass + [`discover_flight_line()`](models.py:28) (sibling `.hdr` required, `.times` / `.lcf` optional). No I/O, no header parsing. |
| [`airborne_georef.py`](airborne_georef.py) | PIKA-L pushbroom raw-cube georeferencing. Supports: PIKA-L sidecar parsers ([`read_lcf()`](airborne_georef.py:78), [`read_times()`](airborne_georef.py:101), [`load_flight_line_poses()`](airborne_georef.py:155)); per-frame pose interpolation ([`interpolate_poses()`](airborne_georef.py:117)) aligned by **relative** time (`.lcf` and `.times` epochs differ); flat-earth ground grid ([`flat_ground_grid()`](airborne_georef.py:201)) intersecting per-pixel rays with a flat plane at user-supplied `ground_alt`; DEM-aware ground grid ([`dem_ground_grid()`](airborne_georef.py:541)) iteratively intersecting rays with terrain sampled from a DEM raster; flat / DEM GeoTIFF writer ([`write_flat_geotiff()`](airborne_georef.py:376), with optional `dem_path`) reading the raw `.bil` cube and writing a reprojected GeoTIFF via rasterio geolocation arrays + nearest-neighbor resampling. **Still no QGIS UI wiring** — the writer is callable from Python only. |
| [`metadata.txt`](metadata.txt), [`pb_tool.cfg`](pb_tool.cfg), [`resources.qrc`](resources.qrc), `resources.py`, `icon.png` | QGIS plugin manifest, deploy/compile config, Qt resources. |
| [`hyperspectral_plan.md`](hyperspectral_plan.md) | Comparative-analysis design doc — three stages with `v0` / `v1` / `v2` / … approaches (additive evolution: older versions are never removed, they coexist as user-selectable options). Source of truth for tooltip copy. |
| `test/` | Plugin Builder test scaffolding (QGIS test app, metadata validator). |

## Current state

The five algorithms appear in **Processing Toolbox → Hyperspectral gaps filler → Raster analysis**. Use a single-stage algorithm when debugging or plugging into Model Builder; use the end-to-end pipeline for normal runs.

**Method registries (driven by [`methods.py`](methods.py:1), one entry per implemented version):**
- `FRAME_FILTER_METHODS` — `v1_hard_thresholds` (default), `v2_adaptive_mad` (per-flight adaptive MAD thresholds), `v3_per_band` (per-band striping / dropout detection).
- `MOSAIC_METHODS` — `v1_first_write_wins` and `v2_best_pixel` (default since 2026-05-07 — recommended). Both are spectrally faithful (every output pixel comes from exactly one source frame, no mixing). The visual-only `v4_feather` and `v5_histmatch_feather` variants were removed (see "Project policy — data correctness over visual appearance" below).
- `GAP_FILL_METHODS` — `v3_gdal_fillnodata` (default since 2026-05-07, native C, optional fallback to v2 on failure), `v2_idw_quadrants` (pure-Python).

**QGIS algorithm parameters now exposed:**
- Stage A core: `INPUT_LAYERS`, 8 raw thresholds (`SKEW_MAX`, `AREA_LO`, `AREA_HI`, `ASPECT_MAX`, `CENTRE_WINDOW`, `MIN_VALID_FRACTION`, `STD_MIN`, `SATURATION_FRACTION`), `THRESHOLD_PRESET` (Custom / Permissive / Default / Strict), `FRAME_FILTER_METHOD`. Stage A standalone adds `OUTPUT_FOLDER`, `REPORT`.
- Stage B: `INPUT_LAYERS`, `MOSAIC_METHOD`, `REPROJECT_TO_FIRST` (optional CRS reprojection), `OUTPUT`.
- Stage C: `INPUT`, `MASK_LAYER`, `DISTANCE` (max search), `ITERATIONS` (smoothing), `GAP_FILL_METHOD`, `FILL_ONLY_INTERIOR`, `MAX_INTERIOR_GAP_PX`, `TILE_SIZE` (windowed mode), `N_WORKERS` (parallel bands), `OUTPUT`.
- Pipeline: union of the above (Stage A thresholds + preset + all three method enums + reproject + fill toggles + tile / workers + `MAX_DISTANCE` / `SMOOTHING_ITERATIONS` + `OUTPUT`).
- Mosaic Quality: `REFERENCE`, `MOSAIC`; outputs `MEAN_<M>` + `WORST_<M>` + `P05_<M>` + `WORST_<M>_BAND` + `P05_<M>_BAND` for each of RMSE / MAE / PSNR / SSIM, plus `SAM` and `SAM_DEG`.

**Defaults:** all method dropdowns default to the most-reliable working version (index 0). When `OUTPUT` is empty, Stage C / Stage B / pipeline derive a path from the input folder (`<input>_filled.tif` / `mosaic.tif` / `filled_mosaic.tif`) and log it.

## Original review — known limitations

(Trimmed substance from the first-version review and subsequent open items.)

- `metadata.txt` still has placeholder URLs (`tracker=http://bugs`, `repository=http://repo`, `homepage=http://homepage`). Replace before publishing.
- No reprojection or resampling unless `REPROJECT_TO_FIRST=True`; otherwise CRS / pixel-size mismatch aborts the run via [`MosaicInputError`](mosaic.py:33).
- Thresholds are global per run; no auto-derivation from per-flight statistics.
- Overlap rule defaults to first-write-wins (`v1`); a feathered / weighted-blending option (`v4_feather`, see Quality changelog 2026-05-06) is now available via the `MOSAIC_METHOD` dropdown.
- Intermediate mosaic (`<output>.mosaic.tif`) is not cached — deleted after Stage C.
- The standalone [`FrameFilterAlgorithm`](frame_filter_algorithm.py:24) **copies** kept frames to its output folder (no symlink), so disk usage roughly doubles.
- No tests added (per project policy); plugin-builder test scaffolding under `test/` is mostly placeholder.
- i18n machinery is wired up but no `i18n/*.qm` files exist.
- `qgisMinimumVersion=3.0` is technically too old for the modern Processing parameter API; `3.14`+ would be more honest.
- Optional dependencies: `scipy` (preferred for `binary_fill_holes` / `binary_closing`; pure-numpy fallback exists), `scikit-image` (required for SSIM in `mosaic_quality`), `spectral` (lazy-imported by [`envi_io.py`](envi_io.py:1) for ENVI `.hdr` parsing; the plugin still loads without it — `read_envi_header()` raises a clear `ImportError` with install hint when called), `pymap3d` (used by both [`airborne_georef.flat_ground_grid()`](airborne_georef.py:201) and [`airborne_georef.dem_ground_grid()`](airborne_georef.py:541) for ENU→geodetic conversion; module-level `try/except ImportError` falls back to a stub that raises with an install hint when actually called), `rasterio` (lazy-imported inside [`airborne_georef.write_flat_geotiff()`](airborne_georef.py:376) for raw `.bil` cube read, GeoTIFF write, and DEM sampling in [`airborne_georef.dem_ground_grid()`](airborne_georef.py:541) — the rest of the plugin already requires `rasterio >= 1.3` so this is not a new hard dep, only the import is deferred), `pyproj` (lazy-imported inside [`write_flat_geotiff()`](airborne_georef.py:376) **only** when `dst_crs != "EPSG:4326"`, and inside [`dem_ground_grid()`](airborne_georef.py:541) **only** when the DEM CRS is not `EPSG:4326`, via `Transformer(always_xy=True)`).

## Tuning the filter

- Open **"Filter bad frames"** ([`frame_filter_algorithm.py`](frame_filter_algorithm.py:24)) or **"Hyperspectral pipeline"** ([`hyperspectral_algorithm.py`](hyperspectral_algorithm.py:22)). Pick a preset from `THRESHOLD_PRESET` (Permissive / Default / Strict) or leave it on `Custom` to use the eight raw inputs.
- Defaults match the module-level constants in [`frame_filter.py`](frame_filter.py:33) (`SKEW_MAX=0.05`, `AREA_LO=0.5`, `AREA_HI=2.0`, `ASPECT_MAX=2.0`, `CENTRE_WINDOW=64`, `MIN_VALID_FRACTION=0.5`, `STD_MIN=1.0`, `SATURATION_FRACTION=0.95`).
- Rejection lines in the Processing log embed both the measured value AND the violated threshold, so it is obvious which knob to relax. Example: `"abnormal aspect ratio (ar=2.15 > 2.00)"` → raise `aspect_max`.
- If every frame is rejected, scan the live log for the most common reason and bump the matching threshold (or pick the `Permissive` preset).
- Bigger `centre_window` = more pixels feed the variance / saturation / valid-fraction check (more reliable but slower).

## Changelog — 2026-05-05 plan iteration

Consolidates per-item dated entries from the Pipeline TO-DO sweep into one thematic block. Older history (initial review, fill-nodata extraction, Processing-framework refactor, single→multi-band fix, hyperspectral pipeline introduction) is summarised in the Architecture section above.

**Method-selection scaffolding.**
- [`methods.py`](methods.py:1) introduced — three plain-Python registries (`FRAME_FILTER_METHODS`, `MOSAIC_METHODS`, `GAP_FILL_METHODS`) with `labels()` / `tooltip_block()` helpers; tooltips taken verbatim from the "When to use / Limits" copy in [`hyperspectral_plan.md`](hyperspectral_plan.md:1).
- All four QGIS algorithms gained a `QgsProcessingParameterEnum` per relevant stage; each `processAlgorithm` reads the enum index, looks up the registry entry, logs `method["id"]`, and dispatches via `entry["func"](...)`.
- End-to-end pipeline now honours the gap-fill enum: [`pipeline.run_pipeline()`](pipeline.py:32) gained an optional `gap_fill_func=None` kwarg and Stage C is now a single file-level call (`<output>.mosaic.tif` consumed from disk by either v2 or v3); the all-NaN-band guard moved one step earlier so the abort message is identical regardless of backend.

**Robustness.**
- Early input validation in [`HyperspectralPipelineAlgorithm`](hyperspectral_algorithm.py:34) — `mosaic.validate_inputs()` runs before Stage A, so CRS / pixel-size / band-count / dtype mismatch fails in seconds, re-raised as `QgsProcessingException`.
- All-NaN-band guard in [`pipeline.run_pipeline()`](pipeline.py:1) (`np.isfinite(arr).any()`) — fully-corrupted bands now fail loudly with the band index instead of silently returning all-NaN.
- Optional CRS reprojection (`reproject_to_first: bool` on [`mosaic.validate_inputs()`](mosaic.py:42), [`mosaic.mosaic_frames()`](mosaic.py:96), [`pipeline.run_pipeline()`](pipeline.py:32)) via `rasterio.warp.calculate_default_transform` + `rasterio.warp.reproject` (bilinear) into a temp dir; default `False` reproduces previous behaviour byte-for-byte.
- File-descriptor cap in [`mosaic.mosaic_frames()`](mosaic.py:96) — `_MAX_OPEN_SOURCES = 256` chunking, with cross-chunk first-write-wins preserved via read-modify-write on still-NaN pixels (single-chunk fast path is byte-identical to before).
- Rejected-frames CSV — [`pipeline.run_pipeline()`](pipeline.py:1) writes `<output>.rejected.csv` (`path, reason, measured_value, threshold`) for audit; `_parse_reason()` lifts the measured/threshold pair out of the existing reason strings; `try/except OSError` keeps disk failure from aborting the pipeline.
- Cancel handling — `is_canceled: Optional[Callable[[], bool]] = None` threaded into [`frame_filter.filter_frames()`](frame_filter.py:154) (Stage A: polled per frame, both passes), [`pipeline._PipelineFeedback`](pipeline.py:1) (Stage C: forwarded into `fill_nodata_file`'s per-band/per-tile polls); Stage B already cancels via the `progress` callback shim. `RuntimeError("canceled")` translates to `QgsProcessingException` in every wrapper.

**Performance.**
- Chunked mosaic merge (see Robustness) — also a perf win on small frame counts due to a single `dst.write` per band.
- Frame-open inversion in [`mosaic.mosaic_frames()`](mosaic.py:169) — chunk loop is now outer, band loop inner; each chunk opens its sources exactly once and iterates `rasterio.merge.merge(..., indexes=[b])`. Total open/close count drops from `band_count × n_chunks × chunk_size` to `n_chunks × chunk_size` (factor = `band_count`, ~280× on PIKA-L cubes). Progress now ticks per `(chunk, band)`.
- Gap-fill v3 GDAL backend — [`fill_nodata.fill_nodata_file_gdal()`](fill_nodata.py:436) wraps `gdal.FillNodata` (native C, ~10–100× faster than v2 quadrant sweeps); registered as a second `GAP_FILL_METHODS` entry; on any non-cancellation `Exception` falls back to v2 so the algorithm still produces output.
- Windowed / tiled gap-fill — `tile_size: int = 0` kwarg on both file-level fill callables; `> 0` walks each band as `tile_size`-square inner cores read with a halo of `max_search_dist + smoothing_iterations` pixels, so the windowed result is observationally identical to whole-band processing. v3 ignores `tile_size` (logs once) since `gdal.FillNodata` already streams in C.
- Parallel per-band gap-fill — `n_workers: int = 1` kwarg on the file-level v2 backend; `> 1` submits each band to a `concurrent.futures.ThreadPoolExecutor` via top-level worker [`fill_nodata._fill_band_worker()`](fill_nodata.py:507) (each thread re-opens the raster, returns `(b, filled, nodata)` to the parent which serialises GDAL writes via `as_completed`). Tiled+parallel mode (#9 follow-up) extended this so bands run concurrently with each band tiled internally.
- Fix: parallel gap-fill switched from `ProcessPoolExecutor` to `ThreadPoolExecutor` to avoid Windows `spawn` re-importing QGIS and crashing workers. Same UX, no API change.

**Quality.**
- Footprint-aware gap-fill — [`pipeline.run_pipeline()`](pipeline.py:1) builds a validity mask (union across bands of finite pixels), computes interior holes via `scipy.ndimage.binary_fill_holes` (pure-numpy 4-connected flood-fill fallback), writes a 0/1 uint8 mask GeoTIFF and forwards it as `mask_path=` to the gap-fill callable. v2 / v3 polarity is single — outside-footprint NaN stays NaN. New kwarg `fill_only_interior: bool = True`; `False` reverts to legacy `mask_path=None`. Promoted to public [`fill_nodata.write_interior_fill_mask()`](fill_nodata.py:32) so the standalone Fill NoData algorithm can use it too (user-supplied mask still wins; auto-mask cleaned up in `finally`).
- Morphological closing with `MAX_INTERIOR_GAP_PX` — [`fill_nodata.write_interior_fill_mask()`](fill_nodata.py:62) gained `max_gap_px: int = 0`; `0` is byte-identical to topological-only behaviour, `N > 0` additionally runs `binary_closing(validity, iterations=N)` and unions it into the fill region (bridges slits / edge-touching holes up to ~`2N` px wide that `binary_fill_holes` alone cannot reach). scipy preferred (`binary_closing`), pure-numpy `_dilate4`/`_erode4` fallback. Wired into both standalone Fill NoData and the combined pipeline as `MAX_INTERIOR_GAP_PX` (default 50).
- 2026-05-06 — Mosaic v4 (feathered / weighted blending) — new [`mosaic.mosaic_frames_feather()`](mosaic.py:380) registered as `v4_feather` ("v4 — Feathered / weighted blending") in [`MOSAIC_METHODS`](methods.py:1) alongside the unchanged `v1_first_write_wins` default. Per-frame weights are built with [`scipy.ndimage.distance_transform_edt`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.ndimage.distance_transform_edt.html) clipped to a `MAX_FEATHER_PX` ramp (Python kwarg `max_feather_pixels`, default 32, min 0); the final pixel is `Σ(w·v) / Σ(w)`, band-streamed in the same chunked pattern as v1, with NaN nodata wherever `Σw == 0`. New `MAX_FEATHER_PX` parameter wired into [`MosaicAlgorithm`](mosaic_algorithm.py:20) and [`HyperspectralPipelineAlgorithm`](hyperspectral_algorithm.py:22) and forwarded only when the user picks `v4`. [`pipeline.run_pipeline()`](pipeline.py:32) gained matching `mosaic_func=None` and `max_feather_pixels=32` kwargs; defaults preserve old behaviour, so `v1` is byte-equivalent to before. Closes Quality TO-DO "Mosaic v4 — feathered blending" in [`hyperspectral_plan.md`](hyperspectral_plan.md:520) and roadmap item #1 in [`plans/roadmap.md`](plans/roadmap.md:9).
- 2026-05-06 — Filter v2 (per-flight adaptive MAD thresholds) — new [`frame_filter.filter_frames_adaptive_mad()`](frame_filter.py:256) registered as `v2_adaptive_mad` ("v2 — Adaptive MAD thresholds (per-flight)") in [`FRAME_FILTER_METHODS`](methods.py:1) alongside the unchanged `v1_hard_thresholds` default (index 0). The metric is per-frame footprint area, reusing v1's existing first-pass extraction so no new IO is added; rejection is two-sided at `K_MAD * MAD` around the dataset median, where `MAD = 1.4826 · median(|x − median(x)|)`. Edge cases: `MAD == 0` keeps every frame, empty input returns `([], [])`. Rejection-reason strings reuse v1's `"measured vs threshold"` convention so the existing `<output>.rejected.csv` audit and [`pipeline.py`](pipeline.py:1) `_parse_reason()` keep working unchanged. New `K_MAD` parameter (Python kwarg `k_mad`, Double, default 3.0, min 0.0) wired into [`FrameFilterAlgorithm`](frame_filter_algorithm.py:1) and [`HyperspectralPipelineAlgorithm`](hyperspectral_algorithm.py:1) and forwarded through [`pipeline.run_pipeline()`](pipeline.py:1) only when the user picks `v2`; the `v1` dispatch path is byte-equivalent to before. Closes Quality TO-DO "Add filter v2 (per-flight adaptive thresholds)" in [`hyperspectral_plan.md`](hyperspectral_plan.md:526) and roadmap item #2 in [`plans/roadmap.md`](plans/roadmap.md:23).
- 2026-05-06 — Filter v3 (per-band striping / dropout detection) — new [`frame_filter.filter_frames_per_band()`](frame_filter.py:341) registered as `v3_per_band` ("v3 — Per-band striping / dropout detection") in [`FRAME_FILTER_METHODS`](methods.py:1) alongside the unchanged `v1_hard_thresholds` default (index 0). Each frame is opened once and bands are scanned one at a time (no full-cube load): per band, the **dropout fraction** is the share of NoData / 0 / saturated-max pixels within the valid footprint mask, and the **striping indicator** is `var(column means) / var(all valid pixels)` — values close to 1.0 indicate strong column-direction striping. A frame is rejected if any band exceeds `MAX_DROPOUT_FRAC` (default 0.30) or `MAX_STRIPE_RATIO` (default 0.5). Edge cases: empty valid mask reports 100% dropout, constant valid pixels yield striping ratio 1.0, fewer than 2 columns skip the striping check, NoData covers both NaN and the dataset sentinel, and saturation is only flagged for integer dtypes. Rejection-reason strings reuse the v1/v2 `"measured vs threshold"` convention and identify the offending band, e.g. `"band 12 dropout (frac=0.42, allowed=[0, 0.30])"`, so the existing `<output>.rejected.csv` audit and [`pipeline.py`](pipeline.py:1) `_parse_reason()` keep working unchanged. Two new parameters — `MAX_DROPOUT_FRAC` (Double, 0–1, default 0.30) and `MAX_STRIPE_RATIO` (Double, min 0, default 0.5) — are wired into [`FrameFilterAlgorithm`](frame_filter_algorithm.py:1) and [`HyperspectralPipelineAlgorithm`](hyperspectral_algorithm.py:1) and forwarded through [`pipeline.run_pipeline()`](pipeline.py:1) only when the user picks `v3`; `v1` and `v2` dispatch paths are byte-equivalent to before. Closes Quality TO-DO "Add filter v3 (per-band striping / dropout check)" in [`hyperspectral_plan.md`](hyperspectral_plan.md:531) and roadmap item #3 in [`plans/roadmap.md`](plans/roadmap.md:37).
- 2026-05-06 — Mosaic v5 (histogram match + feather) — new [`mosaic.mosaic_frames_histmatch_feather()`](mosaic.py:606) registered as `v5_histmatch_feather` ("v5 — Histogram match + feather (visual; alters spectra)") in [`MOSAIC_METHODS`](methods.py:1) alongside the unchanged `v1_first_write_wins` default and the spectrally-faithful `v4_feather`. ⚠️ **Spectral-fidelity warning:** v5 is a visual-quality option only — it alters per-pixel spectral values and must not be used as input to spectral analysis. The label and the `MAX_FEATHER_PX` parameter tooltips were updated to flag this. Algorithm: deterministic linear mean/std (moment) histogram matching against the **first input frame** as reference (matches user-provided input order). For each non-reference frame and each band, a `gain = ref_std / src_std` and `offset = ref_mean − gain · src_mean` are computed on overlap pixels when an overlap exists, falling back to global mean/std of the reference and source otherwise; matched pixels are then blended through the same v4 feather path (reuses [`_feather_weights()`](mosaic.py:357)). CDF-based matching is intentionally **not** used (kept simple and junior-readable). Edge cases: no overlap → global-stats fallback; either side with `std == 0` → `gain=1, offset=0` (skip matching); reference empty for a band → no matching applied; source empty for a band → frame skipped for that band; NoData / footprint handling identical to v4. No new QGIS parameter — the existing `MAX_FEATHER_PX` is reused, and conditional forwarding now covers both v4 and v5; the `v1` dispatch path remains byte-equivalent. Files touched: [`methods.py`](methods.py:1), [`mosaic.py`](mosaic.py:1), [`mosaic_algorithm.py`](mosaic_algorithm.py:1), [`pipeline.py`](pipeline.py:1), [`hyperspectral_algorithm.py`](hyperspectral_algorithm.py:1). Closes Quality TO-DO "Add mosaic v5 (histogram match + feather)" in [`hyperspectral_plan.md`](hyperspectral_plan.md:524) and roadmap item #4 in [`plans/roadmap.md`](plans/roadmap.md:50) — **all roadmap items now complete.**

**Diagnostics (Mosaic Quality).**
- `WORST_<M>` and `P05_<M>` for each per-band metric (RMSE / MAE / PSNR / SSIM) plus offending band indices `WORST_<M>_BAND` / `P05_<M>_BAND` (1-based). Polarity-aware: `WORST` is `max` for lower-is-better, `min` for higher-is-better; `P05` always means "5% of bands are at least this bad". Tie-breaking is deterministic (lowest band index).
- Whole-cube SAM and SAM_DEG accumulated **inside the existing per-band loop** (no extra raster read): `arccos(clip(dot/(||p||·||q||+eps), -1, 1))` averaged over pixels valid in every band.
- 14 + 8 = 22 outputs exposed on [`MosaicQualityAlgorithm`](mosaic_quality_algorithm.py:21); `format_report()` row order is `MEAN_<M>` → `WORST_<M>` → `WORST_<M>_BAND` → `P05_<M>` → `P05_<M>_BAND`, then `SAM` / `SAM_DEG`. Existing `MEAN_<M>` numeric values are unchanged.
- The earlier "size mismatch" failure (when reference and mosaic differ in extent) was fixed by warping the reference onto the mosaic's exact grid in-memory via `gdal.Warp` (`format="MEM"`, `resampleAlg="near"`).

**Usability.**
- Threshold presets — `THRESHOLD_PRESETS` dict in [`frame_filter.py`](frame_filter.py:1) (`permissive` / `default` / `strict`) plus [`preset_thresholds()`](frame_filter.py:95) lookup. `THRESHOLD_PRESET` enum exposed on [`FrameFilterAlgorithm`](frame_filter_algorithm.py:24) and [`HyperspectralPipelineAlgorithm`](hyperspectral_algorithm.py:22) (4 options: Custom / Permissive / Default / Strict, default `Custom`); when non-Custom, the 8 raw inputs are ignored and the active preset is logged.
- Default OUTPUT path — when `OUTPUT` is empty (`None`, `""`, or `"TEMPORARY_OUTPUT"`) the three algorithms with a raster destination derive a path from the input folder before calling `parameterAsOutputLayer` and log it: `<input_dir>/<input_stem>_filled.tif` (Fill NoData), `<first_input_dir>/mosaic.tif` (Mosaic), `<first_input_dir>/filled_mosaic.tif` (pipeline). [`FrameFilterAlgorithm`](frame_filter_algorithm.py:24) is intentionally untouched (its outputs are a folder + optional report).
- Granular progress wiring — [`HyperspectralPipelineAlgorithm.processAlgorithm()`](hyperspectral_algorithm.py:1) defines a `cb(fraction, message)` shim forwarding `int(fraction * 100)` into `feedback.setProgress` and `feedback.isCanceled()` (cancellation double-duty). Stage A reports `cb(0.05, ...)`, Stage B `cb(0.05 + 0.65 * f, ...)` per `(chunk, band)` tick, Stage C lands in `[0.70 .. 1.00]` via [`_PipelineFeedback.setProgress()`](pipeline.py:127).
- 2026-05-06 — Auto-add result to canvas as RGB composite — new [`canvas_styling.attach_rgb_post_processor()`](canvas_styling.py:120) attaches a `QgsProcessingLayerPostProcessorInterface` to the output `LayerDetails` (only when `context.willLoadLayerOnCompletion()` is true), swapping the default grayscale-band-1 renderer for a `QgsMultiBandColorRenderer` with bands picked at fractional positions of the cube (R≈40%, G≈25%, B≈12% → ~640/550/470 nm on PIKA-L) plus a cumulative-cut min/max stretch; wired into Mosaic / Fill NoData / Pipeline algorithms (Frame Filter and Mosaic Quality intentionally untouched — folder + numeric outputs).
- 2026-05-06 — Documented `rasterio >= 1.3` requirement in [`metadata.txt`](metadata.txt:13) `about=` (the `dtype=` / `nodata=` kwargs on `rasterio.merge.merge` need 1.3+); QGIS plugin metadata has no machine-readable `python_dependencies` field, so the constraint is recorded as user-facing prose alongside the existing optional-dep note.
- 2026-05-06 — Documented the temp-file convention in the [`pipeline.py`](pipeline.py:1) module docstring (`<output>.mosaic.tif` + `<output>.fillmask.tif`) and moved the [`mosaic.mosaic_frames()`](mosaic.py:96) call **inside** [`run_pipeline()`](pipeline.py:144)'s existing `try/finally` so a `KeyboardInterrupt` or QGIS cancellation mid-Stage-B no longer leaks a partial mosaic next to the user's output (Pipeline TO-DO #17 in [`hyperspectral_plan.md`](hyperspectral_plan.md:500)). The `<output>.rejected.csv` audit report is explicitly called out as a deliverable, not a temp file.
- 2026-05-06 — Dry-run mode on [`HyperspectralPipelineAlgorithm`](hyperspectral_algorithm.py:57) — new `DRY_RUN` boolean parameter (default `False`); when ON, only Stage A runs (direct [`frame_filter.filter_frames()`](frame_filter.py:208) call), every rejected frame is logged with its reason, kept / rejected / total counts are reported, and the algorithm returns `{}` without writing a raster or queueing the RGB post-processor — saves iteration time when tuning thresholds (Pipeline TO-DO #18).
- 2026-05-06 — Resolved placeholder URLs in [`metadata.txt`](metadata.txt:15) — `homepage`, `repository`, `tracker` now point at `https://github.com/Burunduke/gaps_filler` (and `/issues` for tracker), closing Pipeline TO-DO #19 and the long-standing flag in this review.
- Fix: post-processor attach used wrong PyQGIS API (`layersToLoadOnCompletion` takes no args). Result is rendered as RGB in QGIS again.

**New algorithm parameters added in this iteration:** `FRAME_FILTER_METHOD`, `MOSAIC_METHOD`, `GAP_FILL_METHOD`, `REPROJECT_TO_FIRST`, `FILL_ONLY_INTERIOR`, `MAX_INTERIOR_GAP_PX`, `TILE_SIZE`, `N_WORKERS`, `THRESHOLD_PRESET`. Mosaic Quality outputs: `WORST_<M>`, `P05_<M>`, `WORST_<M>_BAND`, `P05_<M>_BAND` for each of RMSE / MAE / PSNR / SSIM, plus `SAM` and `SAM_DEG`.

## Quality benchmarks

### 2026-05-05 — Footprint-aware gap-fill: strict topological mask vs. morphological closing

Context: same hyperspectral flight dataset; the only difference between the two runs is the gap-fill mask construction (strict `binary_fill_holes` → `binary_fill_holes ∪ binary_closing(N=50)`, i.e. [`fill_nodata.write_interior_fill_mask()`](fill_nodata.py:62) with `max_gap_px=0` vs. `MAX_INTERIOR_GAP_PX=50`); all other parameters held equal.

| Metric    | Before        | After         | Δ (After − Before)        |
|-----------|---------------|---------------|---------------------------|
| MEAN_RMSE | 0.07097884    | 0.00387872    | −0.06710012 (improved)    |
| MEAN_MAE  | 0.01861339    | 0.00060489    | −0.01800850 (improved)    |
| MEAN_PSNR | 28.21847672   | 51.41162463   | +23.19314791 (improved)   |
| MEAN_SSIM | 0.93811815    | 0.99524193    | +0.05712378 (improved)    |

Legend: RMSE / MAE — lower is better; PSNR / SSIM — higher is better.

### 2026-05-05 — Run with worst-band diagnostics (`MAX_INTERIOR_GAP_PX=50`, all current improvements)

| Metric | MEAN | WORST (band) | P05 (band) |
|---|---|---|---|
| RMSE | 0.003910660253532197 | 0.013559284618043038 (38) | 0.012115237730602016 (27) |
| MAE  | 0.0005470915808632717 | 0.0017883461460990143 (38) | 0.0016464821544427116 (48) |
| PSNR | 51.754405044769726 | 28.049728903659542 (202) | 33.33327192993308 (134) |
| SSIM | 0.9956317627913356 | 0.9597805687499575 (138) | 0.9775634930766522 (199) |

| Whole-cube | Value |
|---|---|
| SAM (rad) | 0.00024121789320524526 |
| SAM_DEG   | 0.013820767223697971 |

**Notes.**
- Mean metrics are excellent (RMSE ~0.4%, PSNR 51.8 dB, SSIM 0.996, SAM 0.014°) — the cube is, on average, a near-perfect reconstruction.
- Worst-band differs across metrics (38 / 202 / 138) → at least three distinct failure modes rather than one universally bad band.
- Band 38 owns both `WORST_RMSE` and `WORST_MAE`: highest absolute error, likely a bright channel with steep gradients near gap edges where any interpolation slip translates into large pixel-space residuals.
- Band 202 has `WORST_PSNR ≈ 28 dB` at only moderate RMSE → low-MAX band (dark / SWIR or H₂O absorption region, low SNR), so PSNR is dominated by the small dynamic range rather than reconstruction error.
- Band 138 has `WORST_SSIM` at moderate pixel error → structural artefacts (likely visible mosaic seams / texture discontinuities) that pixel-wise metrics under-weight.

## Changelog — 2026-05-06 plan retirement

`hyperspectral_plan.md` has been fully completed and removed from the repository. All committed items in its three stages and the cross-cutting Pipeline TO-DO list shipped and are documented in the dated entries above; remaining `_planned_` markers in the plan were explicit non-commitments and are intentionally not carried forward.

Delivered across the three stages:
- **Stage A — Frame filter:** `v1_hard_thresholds` (default), `v2_adaptive_mad` (per-flight MAD around the median footprint area), `v3_per_band` (per-band striping ratio + dropout fraction).
- **Stage B — Mosaic:** `v1_first_write_wins` (default), `v4_feather` (distance-transform-weighted blending), `v5_histmatch_feather` (linear mean/std match against the first frame, then v4 — visual-only; alters spectra).
- **Stage C — Gap-fill:** `v2_idw_quadrants` (default, pure-Python) and `v3_gdal_fillnodata` (native C with v2 fallback).

Cross-cutting Pipeline TO-DO closed: method-selection UX (per-stage [`methods.py`](methods.py:1) registries with tooltip dispatch), robustness (early `validate_inputs`, all-NaN-band guard, optional CRS reprojection, FD-cap chunked merge, rejected-frames CSV, cancellation), performance (frame-open inversion, GDAL gap-fill backend, windowed/tiled fill, parallel per-band threads), QGIS UX (threshold presets, default OUTPUT paths, granular progress, RGB post-processor, dry-run, resolved metadata URLs, documented `rasterio >= 1.3`), maintenance (temp-file convention `<output>.mosaic.tif` / `<output>.fillmask.tif` inside `try/finally`), and footprint-aware gap-fill (interior-hole topology + `MAX_INTERIOR_GAP_PX` morphological closing).

## Changelog — 2026-05-06 multi-method consistency sweep

Follow-up cleanup after the three method registries shipped: every place that still implicitly assumed a single fixed method per stage has been wired through the registry, and the Stage A / B / C parameter dialogs were tidied so only the relevant knobs are visible by default. No behavioural change for users who keep all three dropdowns on their default (index 0); v2 / v3 / v4 / v5 paths only get touched when the user picks them.

**Dispatch correctness.**
- [`pipeline.run_pipeline()`](pipeline.py:166) now defaults its three method funcs from `methods.FRAME_FILTER_METHODS[0]["func"]` / `methods.MOSAIC_METHODS[0]["func"]` / `methods.GAP_FILL_METHODS[0]["func"]` (lazy `from . import methods` to avoid any top-level circular-import surprise) instead of the hard-coded `frame_filter.filter_frames` / `mosaic.mosaic_frames` / `fill_nodata.fill_nodata_file` fallbacks. Reordering or replacing the index-0 entry in [`methods.py`](methods.py:1) now propagates without touching the orchestrator. Behaviour is byte-identical for v1 / default users (the registry's index 0 is still those same three callables).

**Dry-run consistency.**
- [`HyperspectralPipelineAlgorithm.processAlgorithm()`](hyperspectral_algorithm.py:596) dry-run path now reads `FRAME_FILTER_METHOD` and dispatches through `methods.FRAME_FILTER_METHODS[idx]["func"]` (was hard-coded to v1 [`frame_filter.filter_frames()`](frame_filter.py:214)). The conditional kwarg-building (forward `k_mad` only for v2; `max_dropout_frac` / `max_stripe_ratio` only for v3) mirrors [`pipeline.run_pipeline()`](pipeline.py:225) line-for-line so the v2 / v3 dry-run paths cannot drift from the non-dry-run path.

**Dialog UX — `FlagAdvanced` on method-specific knobs.**
- The 8 raw v1 thresholds (`SKEW_MAX`, `AREA_LO`, `AREA_HI`, `ASPECT_MAX`, `CENTRE_WINDOW`, `MIN_VALID_FRACTION`, `STD_MIN`, `SATURATION_FRACTION`), the v2 `K_MAD`, and the v3 `MAX_DROPOUT_FRAC` / `MAX_STRIPE_RATIO` are now flagged `QgsProcessingParameterDefinition.FlagAdvanced` in [`FrameFilterAlgorithm`](frame_filter_algorithm.py:104) and [`HyperspectralPipelineAlgorithm`](hyperspectral_algorithm.py:216) — the threshold preset / method dropdowns stay on top, the raw knobs live under "Advanced parameters".
- `MAX_FEATHER_PX` (used only by mosaic v4 / v5) flagged `FlagAdvanced` in [`MosaicAlgorithm`](mosaic_algorithm.py:108) and [`HyperspectralPipelineAlgorithm`](hyperspectral_algorithm.py:382).
- `TILE_SIZE` and `N_WORKERS` (Stage C performance knobs, ignored by v3) flagged `FlagAdvanced` in [`FillNoDataAlgorithm`](gaps_filler_algorithm.py:164) and [`HyperspectralPipelineAlgorithm`](hyperspectral_algorithm.py:341).
- Defaults / parameter IDs / descriptions / declaration order are unchanged — only the flag was OR'd in. Cleaner dialog: only the relevant knobs are visible by default; the rest live one click away.

**Terminology sweep.**
- Module / class docstrings, comments and a couple of `shortHelpString` strings reworded where they implied a single fixed method per stage. Touched: [`pipeline.py`](pipeline.py:1) (already accurate, left as-is), [`frame_filter.py`](frame_filter.py:2) module docstring (Public API now lists v1 / v2 / v3 batch wrappers; rejection heuristic flagged as method-dependent), [`mosaic.py`](mosaic.py:2) module docstring (overlap strategy is method-dependent — v1 / v4 / v5 — Public API lists all three callables), [`fill_nodata.py`](fill_nodata.py:2) module docstring (now describes both v2 IDW and v3 gdal.FillNodata as registered backends instead of claiming the module *is* a pure-Python re-implementation), [`mosaic_algorithm.py`](mosaic_algorithm.py:2) module docstring (notes that the display name keeps its historical `first-write-wins` wording for Model Builder backwards compat), [`gaps_filler_algorithm.py`](gaps_filler_algorithm.py:82) `shortHelpString` (mentions both v2 / v3 backends), [`hyperspectral_algorithm.py`](hyperspectral_algorithm.py:125) `shortHelpString` + the stale "currently always uses the implemented v1/v1/v2 path" comment near [`processAlgorithm`](hyperspectral_algorithm.py:638). `QgsProcessingAlgorithm.name()` / `displayName()` / `groupId()` / parameter descriptions were intentionally left untouched (saved Model Builder graphs reference them by string).

## Project policy — data correctness over visual appearance

Reliability and correctness of pixel values is more important than the
visual appearance of the mosaic. Visible seams in overlap regions are
accepted as the cost of preserving exact per-pixel spectra. Any future
mosaic option that mixes / rescales / interpolates spectral values
across frames (feathering, histogram matching, blending, etc.) must
**not** be registered as the production path; it may exist only as an
unregistered helper or as an explicit, clearly-flagged experimental
extra. Index 0 of [`MOSAIC_METHODS`](methods.py:84) must always be the
spectrally-faithful path.

## Changelog — 2026-05-06 visual-mosaic removal

Global change to align the codebase with the policy above. Only the
exact-data `v1_first_write_wins` mosaic path remains reachable; the
two visual-only variants were unregistered with the smallest safe
edit possible.

- [`methods.py`](methods.py:84) — `MOSAIC_METHODS` trimmed to the single
  `v1_first_write_wins` entry; the `v4_feather` and `v5_histmatch_feather`
  entries (which mixed/altered per-pixel spectra) were removed so the
  QGIS dropdown can no longer dispatch to them. Tooltip rewritten to
  explain that visible seams are an accepted cost of exact-data
  preservation.
- [`mosaic.py`](mosaic.py:1) — module docstring updated to state that
  only `v1` is registered. The helper functions `_feather_weights()`,
  `mosaic_frames_feather()` and `mosaic_frames_histmatch_feather()`
  were initially left as unreachable dead code; they have since been
  fully removed (see Phase P1 changelog 2026-05-07).
- [`mosaic_algorithm.py`](mosaic_algorithm.py:1) — removed the
  `MAX_FEATHER_PX` parameter (UI knob and class constant), the
  feather-only `extra_kwargs` dispatch, and the v4/v5 mentions in the
  module docstring + `shortHelpString`. The algorithm now always calls
  the registered method with `(paths, out_path, progress, reproject_to_first)`.
- [`hyperspectral_algorithm.py`](hyperspectral_algorithm.py:1) — same
  cleanup: dropped the `MAX_FEATHER_PX` class constant + parameter
  declaration, the `parameterAsInt` read, and the `max_feather_pixels`
  kwarg forwarded into [`pipeline.run_pipeline()`](pipeline.py:166).
- [`pipeline.py`](pipeline.py:166) — removed the `max_feather_pixels`
  kwarg and the `if mosaic_func in (mosaic_frames_feather, mosaic_frames_histmatch_feather)`
  conditional dispatch. Stage B now always calls the mosaic func with
  the v1 signature `(good, mosaic_path, progress=..., reproject_to_first=...)`.
- No QGIS algorithm `name()` / `displayName()` / `groupId()` was
  touched; saved Model Builder graphs that reference the Mosaic / Pipeline
  algorithms keep resolving. Saved graphs that explicitly set
  `MAX_FEATHER_PX` or selected mosaic indices 1 / 2 will silently
  ignore the now-unknown parameter / fall back to index 0 — both are
  acceptable degradations.
- Verification: `python3 -m py_compile methods.py mosaic.py mosaic_algorithm.py hyperspectral_algorithm.py pipeline.py` succeeded; project-wide regex sweep for `MAX_FEATHER_PX|max_feather_pixels|mosaic_frames_feather|mosaic_frames_histmatch|v4_feather|v5_histmatch` finds matches only inside [`mosaic.py`](mosaic.py:1) (the unreachable helpers), confirming no other importer / registration site references the removed paths.

## Changelog — 2026-05-07 Phase P1 — Quality wins

- **Gap-fill default reordered.** [`methods.GAP_FILL_METHODS`](methods.py:1) now lists `v3_gdal_fillnodata` first (default in QGIS dropdowns); `v2_idw_quadrants` moves to index 1. Spectral fidelity preferred over interpolated invention — the C backend is the same algorithm family as v2 but ~10–100× faster and is now the production default.
- **New mosaic method `v2_best_pixel`** in [`mosaic.py`](mosaic.py:1) (registered as index 1 of [`MOSAIC_METHODS`](methods.py:88), labelled "v2 — Best pixel (max distance to edge) — recommended"): per output pixel picks the source frame with the maximum distance-to-edge (via [`scipy.ndimage.distance_transform_edt`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.ndimage.distance_transform_edt.html)); copies that source's full spectrum (all bands) into the output, so spectral fidelity is preserved (no mixing). Ties resolved by input order. Optionally writes `<output>.sources.tif` (uint16 provenance raster, `0` = nodata, `1..N` = 1-based frame index). [`v1_first_write_wins`](mosaic.py:1) remains index 0 for backward compatibility with saved Model Builder graphs.
- **Dead feather code removed** from [`mosaic.py`](mosaic.py:1): `_feather_weights`, `mosaic_frames_feather` and `mosaic_frames_histmatch_feather` (left as unreachable dead code by the 2026-05-06 visual-mosaic removal) are now fully deleted. The "Project policy — data correctness over visual appearance" section above remains the source of truth for why no feather/histmatch path can be re-introduced as a registered method.
- **Mosaic-quality metrics extended** in [`mosaic_quality.compare_rasters()`](mosaic_quality.py:1): added `coverage_ratio`, `filled_pixel_ratio`, `nodata_fraction_per_band`, plus `*_filled_only` and `*_overlap_only` per-band statistical variants (lets users see how the mosaic performs on the filled-vs-original-vs-overlap regions separately). Fillmask-dependent metrics gracefully return `None` when `<output>.fillmask.tif` is absent (e.g. dry-run or external mosaic). New optional `output_path` kwarg on [`compare_rasters()`](mosaic_quality.py:1) so the helper can locate the side outputs.
- **New provenance analyzer** [`mosaic_quality.analyze_sources()`](mosaic_quality.py:1) (`output_path`, `frame_paths=None`): consumes `<output>.sources.tif` written by `v2_best_pixel`. Returns `n_sources`, `source_contribution_stats` (per-source pixel-share), and optional `source_filenames` when `frame_paths` is supplied. `overlap_ratio` is intentionally `None` (true overlap cannot be reconstructed from winner-only provenance — documented inline so a future reviewer doesn't try to "fix" it). Wired into the [`mosaic_quality_algorithm.py`](mosaic_quality_algorithm.py:1) report.

**Side outputs convention (current):**
- `<output>.mosaic.tif` — intermediate Stage B mosaic (deleted at end of pipeline run).
- `<output>.fillmask.tif` — interior-only fill mask (uint8, 0/1) used by Stage C; consumed by `mosaic_quality` `*_filled_only` metrics.
- `<output>.sources.tif` — uint16 provenance raster written by `v2_best_pixel` (0 = nodata, 1..N = 1-based frame index); consumed by [`mosaic_quality.analyze_sources()`](mosaic_quality.py:1).
- `<output>.rejected.csv` — Stage A audit (`path, reason, measured_value, threshold`); a deliverable, not a temp file.

## Changelog — 2026-05-07 Phase P2 — Hyperspectral metadata foundation

- **New module [`envi_io.py`](envi_io.py:1)** — thin wrapper over `spectral.io.envi.read_envi_header`. Public: [`EnviHeader`](envi_io.py:16) (frozen dataclass with `samples`, `lines`, `bands`, `interleave`, `data_type`, `byte_order`, `wavelengths`, `fwhm`, `bbl`, `band_names`, `wavelength_units`) and [`read_envi_header(hdr_path)`](envi_io.py:31). `spectral` is an OPTIONAL dep — lazy-imported with a clear `ImportError` ("install with: pip install spectral") so the plugin still loads without it.
- **New module [`models.py`](models.py:1)** — passive path-bundling for PIKA-L flight lines. Public: [`FlightLineMeta`](models.py:15) (frozen dataclass: `name`, `bil`, `hdr`, `times?`, `lcf?`) and [`discover_flight_line(bil_path)`](models.py:28) which finds sibling `.hdr` (required), `.times` / `.lcf` (optional). No I/O, no header parsing — that lives in [`envi_io.py`](envi_io.py:1).
- **Deferred per adjusted plan**: `RasterCubeMeta` (no consumer yet), `ProcessingProfile` / `scientific_mode` (registry already enforces correctness).

## Changelog — 2026-05-07 Phase P3 — Raw airborne georef MVP (flat-earth)

- **New [`airborne_georef.py`](airborne_georef.py:1) parsers** — [`read_lcf()`](airborne_georef.py:78), [`read_times()`](airborne_georef.py:101), [`interpolate_poses()`](airborne_georef.py:117), [`load_flight_line_poses()`](airborne_georef.py:155). `.lcf` columns are documented as `time, roll, pitch, yaw` (radians), `lon, lat` (degrees), `alt` (meters); `.times` is aligned to `.lcf` by **relative** time (each file's first timestamp subtracted) because the two files use different absolute epochs but cover the same flight segment. `np.unwrap(yaw)` before interpolation handles wrap-around. Length of `.times` is validated against ENVI header `lines`.
- **Projection math added** — [`PushbroomSensor`](airborne_georef.py:61), [`GroundGrid`](airborne_georef.py:70), [`sample_view_angles()`](airborne_georef.py:175), [`flat_ground_grid()`](airborne_georef.py:201). FOV is a **required** parameter (`fov_deg`) because PIKA-L lenses differ — there is no safe default. The flat ground plane `ground_alt` is also explicit. Body-frame ray `[0, sin(angle), -cos(angle)]` is rotated into ENU via `R = Rz(yaw) @ Ry(pitch) @ Rx(roll)` and intersected with `z = ground_alt`; rays not pointing downward (`dz >= -1e-6`) or with non-positive scale are marked invalid. ENU→geodetic uses [`pymap3d.enu2geodetic()`](airborne_georef.py:351); the return-order bug `(lat, lon, alt)` (not `lon, lat, alt`) was caught and fixed during integration.
- **Flat GeoTIFF writer added** — [`GeorefResult`](airborne_georef.py:365), [`write_flat_geotiff()`](airborne_georef.py:376). Uses rasterio geolocation arrays (`src_geoloc_array=(grid.lon, grid.lat)`) and `Resampling.nearest` for spectral safety (no inter-band mixing or interpolation across pixels). Fails fast with `FileNotFoundError` if the raw `.bil` cube is missing. Validates `width / height / count` against the ENVI header. Inspects the rasterio `reproject()` signature and raises a clear `RuntimeError` if the installed version lacks `src_geoloc_array` support. **No QGIS UI wiring yet** — the writer is callable from Python only.
- **Verified on sidecars** — example `.lcf` / `.times` parse and interpolate cleanly to 2000 frame poses; the projection grid has shape `(2000, 900)`, valid ratio `1.0`, lon ≈ `30.00058..30.00090`, lat ≈ `59.418675..59.418678`. End-to-end flat-earth raw `.bil` smoke test now succeeds (see 2026-05-07 Real Pika L 28 smoke test changelog below); DEM-aware mode still lacks a real DEM end-to-end test.
- **Still pending (at the time of P3.7)** — DEM-aware variant: at the time of P3.7 this was pending; implemented in the P3.8 entry below. Orientation calibration / sign-flip sweep on roll-pitch-yaw if the first real-cube output turns out mirrored or rotated (the IMU export convention is not yet confirmed); QGIS UI integration (a Processing algorithm exposing `FOV_DEG`, `GROUND_ALT`, `DST_CRS`, optional `RESOLUTION`).

## Changelog — 2026-05-07 Phase P3.8 — DEM-aware georef MVP

- **New DEM-aware geolocation** in [`airborne_georef.dem_ground_grid()`](airborne_georef.py:541): iteratively intersects pushbroom rays with DEM terrain, starting from `fallback_ground_alt`, stopping by tolerance or max iterations.
- **Writer integration**: [`airborne_georef.write_flat_geotiff()`](airborne_georef.py:376) now accepts optional `dem_path`; when provided, it uses DEM-aware geolocation while preserving backward-compatible flat-earth calls.
- **DEM sampling behaviour**: nearest-neighbor DEM sampling via rasterio; DEM CRS supported (EPSG:4326 direct, other CRS via `pyproj.Transformer(always_xy=True)`); missing DEM path fails clearly; nodata / non-finite DEM samples are invalid rather than accepted as fallback terrain.
- **Verification**: syntax/import OK; missing DEM path reports `FileNotFoundError DEM file not found: example/missing_dem.tif`; flat-grid regression unchanged: `(2000, 900)`, valid ratio `1.0`, lon ≈ `30.00058..30.00090`, lat ≈ `59.418675..59.418678`; scratch root files removed.
- **Still pending**: QGIS UI integration, real raw `.bil` end-to-end test **with a DEM** (flat-earth raw `.bil` smoke test landed — see 2026-05-07 Real Pika L 28 smoke test below), orientation calibration / sign flips if output is mirrored/rotated, optional bilinear DEM sampling later.

## Changelog — 2026-05-07 Real Pika L 28 smoke test

First end-to-end exercise of [`airborne_georef.write_flat_geotiff()`](airborne_georef.py:376) on a real raw `.bil` cube. This is a **smoke test only** — it confirms the code path runs end-to-end and produces a readable GeoTIFF; it does not validate scientific georeferencing accuracy (FOV, lens model, IMU sign conventions still need visual checking in QGIS against a basemap).

- **Test environment**: used [`venv/bin/python`](venv/bin/python); dependencies present: `numpy 2.4.4`, `rasterio 1.5.0`, `pyproj 3.7.2`, `pymap3d 3.2.0`, `spectral 0.24`.
- **Input capture**: [`example/manual_Pika_L_28.bil`](example/manual_Pika_L_28.bil) with sidecars [`example/manual_Pika_L_28.bil.hdr`](example/manual_Pika_L_28.bil.hdr), [`example/manual_Pika_L_28.bil.times`](example/manual_Pika_L_28.bil.times), [`example/manual_Pika_L_28.lcf`](example/manual_Pika_L_28.lcf). Header: `900x2000`, `200` bands, `bil`, `uint16`, `200` wavelengths. `.times` row count matches `lines`; `.lcf` has `1604` rows and `12` columns.
- **Flat projection sanity**: `fov_deg=20.0`, `ground_alt=mean(poses.alt)-50.0 = 41.77847056900127`; grid `(2000, 900)`, valid ratio `1.0`, lon `30.000590025604847..30.0009019613634`, lat `59.418678470247166..59.418681272733956`.
- **End-to-end flat GeoTIFF succeeded**: wrote [`example/manual_Pika_L_28_flat_smoke.tif`](example/manual_Pika_L_28_flat_smoke.tif); readable GeoTIFF, size `900x9`, `200` bands, dtype `uint16`, CRS `EPSG:4326`, bounds `left=30.000590025604847`, `bottom=59.418678470247166`, `right=30.0009019613634`, `top=59.418681272733956`.
- **Code fix from smoke test**: [`airborne_georef.write_flat_geotiff()`](airborne_georef.py:376) now passes `src_crs=CRS.from_string("EPSG:4326")` instead of a raw string for rasterio geolocation-array reprojection (rasterio 1.5 rejects the raw string here).
- **DEM status**: no DEM file in [`example/`](example/), so DEM-aware mode is implemented but not end-to-end tested on this capture.
- **Caveat**: the output height is `9` pixels because the example flight line spans a very small lat range; FOV/lens and orientation calibration still need visual validation in QGIS against a basemap before any scientific use.
