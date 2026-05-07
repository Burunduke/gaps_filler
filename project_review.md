# Project Review — `gaps_filler`

## Project Overview

`gaps_filler` is a QGIS 3 plugin (per [`metadata.txt`](metadata.txt:8)) for processing PIKA-L hyperspectral drone frames into a single gap-filled orthomosaic. It started as a Plugin Builder skeleton and has grown into a Processing-framework provider that exposes six algorithms (filter / raw airborne georef / mosaic / fill / end-to-end pipeline / quality assessment).

## Vendored libraries

Several third-party trees are cloned at the repo root for inspection / offline reference. **None of them are deleted.** A few are imported at runtime (lazily) and the rest are kept as read-only references for future work. Prefer pip / system-installed copies over the vendored trees — the vendored copies are not on `sys.path` by default and are only there so a reviewer can read the upstream source without leaving the workspace.

| Tree | Status | Why it is here |
|---|---|---|
| [`pymap3d/`](pymap3d/) | **Used at runtime** | Lazy-imported by [`airborne_georef.py`](airborne_georef.py:1) for ENU↔geodetic conversions ([`pymap3d.enu2geodetic()`](pymap3d/src/pymap3d/enu.py:1)). Prefer `pip install pymap3d`. |
| [`pyproj/`](pyproj/) | **Used at runtime** | CRS reprojection in DEM-aware georef ([`airborne_georef.dem_ground_grid()`](airborne_georef.py:541)) via `pyproj.Transformer(always_xy=True)`. QGIS bundles `pyproj`; prefer the QGIS / pip copy. The vendored tree is for inspection only. |
| [`spectral/`](spectral/) | **Used at runtime** | Lazy-imported by [`envi_io.py`](envi_io.py:1) for ENVI BIL/HDR header parsing. Prefer `pip install spectral`. |
| [`rioxarray/`](rioxarray/) | Inspection only | Not imported anywhere in the plugin. Kept as upstream reference for future xarray-style raster IO. |
| [`otb/`](otb/) | Inspection only | Not imported. Heavyweight C++ Orfeo Toolbox source. Reserved for future advanced backends; not a runtime dependency. |
| [`micmac/`](micmac/) | Inspection only | Not imported. SfM / photogrammetry toolbox; out of current scope. |
| [`ODM/`](ODM/) | Inspection only | Not imported. Kept for cross-referencing algorithms only. |

## Future architecture notes

### Deferred: core/qgis split

The current flat layout (20 modules at the repo root) is fine for one consumer (QGIS Processing). Defer splitting the package into `core/` (no-Qt modules) + `qgis/` (`*_algorithm.py` + [`gaps_filler_provider.py`](gaps_filler_provider.py:1)) until module count exceeds ~25 **or** the codebase grows multiple consumers (e.g. a CLI in addition to QGIS). When the time comes, do it as a single mechanical rename PR — pure file moves, no new abstractions, no service containers, no staged refactor.

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
| [`gaps_filler_provider.py`](gaps_filler_provider.py) | [`QgsProcessingProvider`](https://api.qgis.org/api/classQgsProcessingProvider.html) (id `gapsfiller`); `loadAlgorithms()` registers all six algorithms. |
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
| [`mosaic_quality_algorithm.py`](mosaic_quality_algorithm.py) | [`MosaicQualityAlgorithm`](mosaic_quality_algorithm.py:21) — quality vs reference (`gapsfiller:mosaic_quality`); now also produces seam-consistency metrics (`SEAM_MEAN_ABS_DIFF` / `SEAM_MEDIAN_ABS_DIFF` / `SEAM_P95_ABS_DIFF` / `SEAM_MAX_ABS_DIFF` / `SEAM_PIXEL_COUNT` / `SEAM_LENGTH_PX`) when a `<output>.sources.tif` provenance raster is available. |
| [`envi_io.py`](envi_io.py) | ENVI `.hdr` reader — thin wrapper over `spectral.io.envi.read_envi_header`. Public: [`EnviHeader`](envi_io.py:16) frozen dataclass + [`read_envi_header()`](envi_io.py:31). `spectral` is lazy-imported (optional dep). |
| [`models.py`](models.py) | Passive path-bundling for PIKA-L flight lines. Public: [`FlightLineMeta`](models.py:15) frozen dataclass + [`discover_flight_line()`](models.py:28) (sibling `.hdr` required, `.times` / `.lcf` optional). No I/O, no header parsing. |
| [`airborne_georef.py`](airborne_georef.py) | PIKA-L pushbroom raw-cube georeferencing. Supports: PIKA-L sidecar parsers ([`read_lcf()`](airborne_georef.py:78), [`read_times()`](airborne_georef.py:101), [`load_flight_line_poses()`](airborne_georef.py:155)); per-frame pose interpolation ([`interpolate_poses()`](airborne_georef.py:117)) aligned by **relative** time (`.lcf` and `.times` epochs differ); flat-earth ground grid ([`flat_ground_grid()`](airborne_georef.py:201)) intersecting per-pixel rays with a flat plane at user-supplied `ground_alt`; DEM-aware ground grid ([`dem_ground_grid()`](airborne_georef.py:541)) iteratively intersecting rays with terrain sampled from a DEM raster; flat / DEM GeoTIFF writer ([`write_flat_geotiff()`](airborne_georef.py:376) with optional `dem_path`) reading the raw `.bil` cube and writing a reprojected GeoTIFF via rasterio geolocation arrays + nearest-neighbor resampling. **Still no QGIS UI wiring** — the writer is callable from Python only. |
| [`airborne_georef_algorithm.py`](airborne_georef_algorithm.py) | [`AirborneGeorefAlgorithm`](airborne_georef_algorithm.py:1) — Stage Raw standalone (`gapsfiller:airborne_georef`); georeferences raw PIKA-L pushbroom cubes using sidecar LCF/times files. |
| [`canvas_styling.py`](canvas_styling.py) | RGB canvas post-processor for QGIS layer styling; automatically attaches to output layers to display multi-band rasters as RGB composites. |
| [`metadata.txt`](metadata.txt), [`pb_tool.cfg`](pb_tool.cfg), [`resources.qrc`](resources.qrc), `resources.py`, `icon.png` | QGIS plugin manifest, deploy/compile config, Qt resources. |
| `test/` | Plugin Builder test scaffolding (QGIS test app, metadata validator). |

## Current state

The six algorithms appear in **Processing Toolbox → Hyperspectral gaps filler → Raster analysis**. Use a single-stage algorithm when debugging or plugging into Model Builder; use the end-to-end pipeline for normal runs.

**Method registries (driven by [`methods.py`](methods.py:1), one entry per implemented version):**
- `FRAME_FILTER_METHODS` — `v1_hard_thresholds` (default), `v2_adaptive_mad` (per-flight adaptive MAD thresholds), `v3_per_band` (per-band striping / dropout detection).
- `MOSAIC_METHODS` — `v1_first_write_wins` (index 0, default), `v2_best_pixel` (index 1, recommended for visual quality), `v3_vrt` (index 2, lazy GDAL VRT). All are spectrally faithful (every output pixel comes from exactly one source frame, no mixing). No feathering / blending — see Project policy section.
- `GAP_FILL_METHODS` — `v3_gdal_fillnodata` (index 0, default), `v2_idw_quadrants` (index 1, pure-Python).

**QGIS algorithm parameters now exposed:**
- Stage A core: `INPUT_LAYERS`, 8 raw thresholds (`SKEW_MAX`, `AREA_LO`, `AREA_HI`, `ASPECT_MAX`, `CENTRE_WINDOW`, `MIN_VALID_FRACTION`, `STD_MIN`, `SATURATION_FRACTION`), `THRESHOLD_PRESET` (Custom / Permissive / Default / Strict), `FRAME_FILTER_METHOD`, `K_MAD` (v2), `MAX_DROPOUT_FRAC` and `MAX_STRIPE_RATIO` (v3). Stage A standalone adds `OUTPUT_FOLDER`, `REPORT`.
- Stage B: `INPUT_LAYERS`, `MOSAIC_METHOD`, `REPROJECT_TO_FIRST` (optional CRS reprojection), `OUTPUT`.
- Stage C: `INPUT`, `MASK_LAYER`, `DISTANCE` (max search), `ITERATIONS` (smoothing), `GAP_FILL_METHOD`, `FILL_ONLY_INTERIOR`, `MAX_INTERIOR_GAP_PX`, `TILE_SIZE` (windowed mode), `N_WORKERS` (parallel bands), `OUTPUT`.
- Pipeline: union of the above (Stage A thresholds + preset + all three method enums + reproject + fill toggles + tile / workers + `MAX_DISTANCE` / `SMOOTHING_ITERATIONS` + `OUTPUT`).
- Mosaic Quality: `REFERENCE`, `MOSAIC`; outputs `MEAN_<M>` + `WORST_<M>` + `P05_<M>` + `WORST_<M>_BAND` + `P05_<M>_BAND` for each of RMSE / MAE / PSNR / SSIM, plus `SAM` and `SAM_DEG`.

**Defaults:** all method dropdowns default to index 0 (the most-reliable working version). When `OUTPUT` is empty, Stage C / Stage B / pipeline derive a path from the input folder (`<input>_filled.tif` / `mosaic.tif` / `filled_mosaic.tif`) and log it.

## Original review — known limitations

(Trimmed substance from the first-version review and subsequent open items.)

- `metadata.txt` still has placeholder URLs (`tracker=http://bugs`, `repository=http://repo`, `homepage=http://homepage`). Replace before publishing.
- No reprojection or resampling unless `REPROJECT_TO_FIRST=True`; otherwise CRS / pixel-size mismatch aborts the run via [`MosaicInputError`](mosaic.py:33).
- Thresholds are global per run; no auto-derivation from per-flight statistics.
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
- [`methods.py`](methods.py:1) introduced — three plain-Python registries (`FRAME_FILTER_METHODS`, `MOSAIC_METHODS`, `GAP_FILL_METHODS`) with `labels()` / `tooltip_block()` helpers; tooltips taken verbatim from the "When to use / Limits" copy in the plan document (now retired).
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
- Footprint-aware gap-fill — [`pipeline.run_pipeline()`](pipeline.py:1) builds a validity mask (union across bands of finite pixels), computes interior holes via `scipy.ndimage.binary_fill_holes` (pure-numpy 4-connected flood-fill fallback), writes a 0/1 uint8 mask GeoTIFF and forwards it as `mask_path=` to the gap-fill callable. v2 / v3 polarity is single — outside-footprint NaN stays NaN. New kwarg `fill_only_interior: bool = True`; `False` reverts to legacy `mask_path=None`. Promoted to public [`fill_nodata.write_interior_fill_mask()`](fill_nodata.py:69) so the standalone Fill NoData algorithm can use it too (user-supplied mask still wins; auto-mask cleaned up in `finally`).
- Morphological closing with `MAX_INTERIOR_GAP_PX` — [`fill_nodata.write_interior_fill_mask()`](fill_nodata.py:69) gained `max_gap_px: int = 0`; `0` is byte-identical to topological-only behaviour, `N > 0` additionally runs `binary_closing(validity, iterations=N)` and unions it into the fill region (bridges slits / edge-touching holes up to ~`2N` px wide that `binary_fill_holes` alone cannot reach). scipy preferred (`binary_closing`), pure-numpy `_dilate4`/`_erode4` fallback. Wired into both standalone Fill NoData and the combined pipeline as `MAX_INTERIOR_GAP_PX` (default 50).
- 2026-05-06 — Mosaic v4 (feathered / weighted blending) — new [`mosaic.mosaic_frames_feather()`](mosaic.py:380) registered as `v4_feather` ("v4 — Feathered / weighted blending") in [`MOSAIC_METHODS`](methods.py:1) alongside the unchanged `v1_first_write_wins` default. Per-frame weights are built with [`scipy.ndimage.distance_transform_edt`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.ndimage.distance_transform_edt.html) clipped to a `MAX_FEATHER_PX` ramp (Python kwarg `max_feather_pixels`, default 32, min 0); the final pixel is `Σ(w·v) / Σ(w)`, band-streamed in the same chunked pattern as v1, with NaN nodata wherever `Σw == 0`. New `MAX_FEATHER_PX` parameter wired into [`MosaicAlgorithm`](mosaic_algorithm.py:20) and [`HyperspectralPipelineAlgorithm`](hyperspectral_algorithm.py:22) and forwarded only when the user picks `v4`; the `v1` dispatch path is byte-equivalent to before. Closes Quality TO-DO "Mosaic v4 — feathered blending" in the plan document and roadmap item #1 in the roadmap document.
- 2026-05-06 — Filter v2 (per-flight adaptive MAD thresholds) — new [`frame_filter.filter_frames_adaptive_mad()`](frame_filter.py:256) registered as `v2_adaptive_mad` ("v2 — Adaptive MAD thresholds (per-flight)") in [`FRAME_FILTER_METHODS`](methods.py:1) alongside the unchanged `v1_hard_thresholds` default (index 0). The metric is per-frame footprint area, reusing v1's existing first-pass extraction so no new IO is added; rejection is two-sided at `K_MAD * MAD` around the dataset median, where `MAD = 1.4826 · median(|x − median(x)|)`. Edge cases: `MAD == 0` keeps every frame, empty input returns `([], [])`. Rejection-reason strings reuse v1's `"measured vs threshold"` convention so the existing `<output>.rejected.csv` audit and [`pipeline.py`](pipeline.py:1) `_parse_reason()` keep working unchanged. New `K_MAD` parameter (Python kwarg `k_mad`, Double, default 3.0, min 0.0) wired into [`FrameFilterAlgorithm`](frame_filter_algorithm.py:1) and [`HyperspectralPipelineAlgorithm`](hyperspectral_algorithm.py:1) and forwarded through [`pipeline.run_pipeline()`](pipeline.py:1) only when the user picks `v2`; the `v1` dispatch path is byte-equivalent to before. Closes Quality TO-DO "Add filter v2 (per-flight adaptive thresholds)" in the plan document and roadmap item #2 in the roadmap document.
- 2026-05-06 — Filter v3 (per-band striping / dropout detection) — new [`frame_filter.filter_frames_per_band()`](frame_filter.py:341) registered as `v3_per_band` ("v3 — Per-band striping / dropout detection") in [`FRAME_FILTER_METHODS`](methods.py:1) alongside the unchanged `v1_hard_thresholds` default (index 0). Each frame is opened once and bands are scanned one at a time (no full-cube load): per band, the **dropout fraction** is the share of NoData / 0 / saturated-max pixels within the valid footprint mask, and the **striping indicator** is `var(column means) / var(all valid pixels)` — values close to 1.0 indicate strong column-direction striping. A frame is rejected if any band exceeds `MAX_DROPOUT_FRAC` (default 0.30) or `MAX_STRIPE_RATIO` (default 0.5). Edge cases: empty valid mask reports 100% dropout, constant valid pixels yield striping ratio 1.0, fewer than 2 columns skip the striping check, NoData covers both NaN and the dataset sentinel, and saturation is only flagged for integer dtypes. Rejection-reason strings reuse the v1/v2 `"measured vs threshold"` convention and identify the offending band, e.g. `"band 12 dropout (frac=0.42, allowed=[0, 0.30])"`, so the existing `<output>.rejected.csv` audit and [`pipeline.py`](pipeline.py:1) `_parse_reason()` keep working unchanged. Two new parameters — `MAX_DROPOUT_FRAC` (Double, 0–1, default 0.30) and `MAX_STRIPE_RATIO` (Double, min 0, default 0.5) — are wired into [`FrameFilterAlgorithm`](frame_filter_algorithm.py:1) and [`HyperspectralPipelineAlgorithm`](hyperspectral_algorithm.py:1) and forwarded through [`pipeline.run_pipeline()`](pipeline.py:1) only when the user picks `v3`; `v1` and `v2` dispatch paths are byte-equivalent to before. Closes Quality TO-DO "Add filter v3 (per-band striping / dropout check)" in the plan document and roadmap item #3 in the roadmap document.
- 2026-05-06 — Mosaic v5 (histogram match + feather) — new [`mosaic.mosaic_frames_histmatch_feather()`](mosaic.py:606) registered as `v5_histmatch_feather` ("v5 — Histogram match + feather (visual; alters spectra)") in [`MOSAIC_METHODS`](methods.py:1) alongside the unchanged `v1_first_write_wins` default and the spectrally-faithful `v4_feather`. ⚠️ **Spectral-fidelity warning:** v5 is a visual-quality option only — it alters per-pixel spectral values and must not be used as input to spectral analysis. The label and the `MAX_FEATHER_PX` parameter tooltips were updated to flag this. Algorithm: deterministic linear mean/std (moment) histogram matching against the **first input frame** as reference (matches user-provided input order). For each non-reference frame and each band, a `gain = ref_std / src_std` and `offset = ref_mean − gain · src_mean` are computed on overlap pixels when an overlap exists, falling back to global mean/std of the reference and source otherwise; matched pixels are then blended through the same v4 feather path (reuses [`_feather_weights()`](mosaic.py:357)). CDF-based matching is intentionally **not** used (kept simple and junior-readable). Edge cases: no overlap → global-stats fallback; either side with `std == 0` → `gain=1, offset=0` (skip matching); reference empty for a band → no matching applied; source empty for a band → frame skipped for that band; NoData / footprint handling identical to v4. No new QGIS parameter — the existing `MAX_FEATHER_PX` is reused, and conditional forwarding now covers both v4 and v5; the `v1` dispatch path remains byte-equivalent. Files touched: [`methods.py`](methods.py:1), [`mosaic.py`](mosaic.py:1), [`mosaic_algorithm.py`](mosaic_algorithm.py:1), [`pipeline.py`](pipeline.py:1), [`hyperspectral_algorithm.py`](hyperspectral_algorithm.py:1). Closes Quality TO-DO "Add mosaic v5 (histogram match + feather)" in the plan document and roadmap item #4 in the roadmap document.

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
- 2026-05-06 — Documented the temp-file convention in the [`pipeline.py`](pipeline.py:1) module docstring (`<output>.mosaic.tif` + `<output>.fillmask.tif`) and moved the [`mosaic.mosaic_frames()`](mosaic.py:96) call **inside** [`run_pipeline()`](pipeline.py:144)'s existing `try/finally` so a `KeyboardInterrupt` or QGIS cancellation mid-Stage-B no longer leaks a partial mosaic next to the user's output (Pipeline TO-DO #17 in the plan document). The `<output>.rejected.csv` audit report is explicitly called out as a deliverable, not a temp file.
- 2026-05-06 — Dry-run mode on [`HyperspectralPipelineAlgorithm`](hyperspectral_algorithm.py:57) — new `DRY_RUN` boolean parameter (default `False`); when ON, only Stage A runs (direct [`frame_filter.filter_frames()`](frame_filter.py:208) call), every rejected frame is logged with its reason, kept / rejected / total counts are reported, and the algorithm returns `{}` without writing a raster or queueing the RGB post-processor — saves iteration time when tuning thresholds (Pipeline TO-DO #18).
- 2026-05-06 — Resolved placeholder URLs in [`metadata.txt`](metadata.txt:15) — `homepage`, `repository`, `tracker` now point at `https://github.com/Burunduke/gaps_filler` (and `/issues` for tracker), closing Pipeline TO-DO #19 and the long-standing flag in this review.
- Fix: post-processor attach used wrong PyQGIS API (`layersToLoadOnCompletion` takes no args). Result is rendered as RGB in QGIS again.

**New algorithm parameters added in this iteration:** `FRAME_FILTER_METHOD`, `MOSAIC_METHOD`, `GAP_FILL_METHOD`, `REPROJECT_TO_FIRST`, `FILL_ONLY_INTERIOR`, `MAX_INTERIOR_GAP_PX`, `TILE_SIZE`, `N_WORKERS`, `THRESHOLD_PRESET`. Mosaic Quality outputs: `WORST_<M>`, `P05_<M>`, `WORST_<M>_BAND`, `P05_<M>_BAND` for each of RMSE / MAE / PSNR / SSIM, plus `SAM` and `SAM_DEG`.

## Quality benchmarks

### 2026-05-05 — Footprint-aware gap-fill: strict topological mask vs. morphological closing

Context: same hyperspectral flight dataset; the only difference between the two runs is the gap-fill mask construction (strict `binary_fill_holes` → `binary_fill_holes ∪ binary_closing(N=50)`, i.e. [`fill_nodata.write_interior_fill_mask()`](fill_nodata.py:69) with `max_gap_px=0` vs. `MAX_INTERIOR_GAP_PX=50`); all other parameters held equal.

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
- **Stage B — Mosaic:** `v1_first_write_wins` (default), `v2_best_pixel` (index 1, recommended for visual quality), `v3_vrt` (index 2, lazy GDAL VRT). All are spectrally faithful (every output pixel comes from exactly one source frame, no mixing). No feathering / blending — see Project policy section.
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
explicit, clearly-flagged experimental extra. Index 0 of [`MOSAIC_METHODS`](methods.py:84) must always be the
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

- **Gap-fill default reordered.** [`methods.GAP_FILL_METHODS`](methods.py:134) now lists `v3_gdal_fillnodata` first (default in QGIS dropdowns); `v2_idw_quadrants` moves to index 1. Spectral fidelity preferred over interpolated invention — the C backend is the same algorithm family as v2 but ~10–100× faster and is now the production default.
- **New mosaic method `v2_best_pixel`** in [`mosaic.py`](mosaic.py:1) (registered as index 1 of [`MOSAIC_METHODS`](methods.py:102), labelled "v2 — Best pixel (max distance to edge) — recommended"): per output pixel picks the source frame with the maximum distance-to-edge (via [`scipy.ndimage.distance_transform_edt`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.ndimage.distance_transform_edt.html)); copies that source's full spectrum (all bands) into the output, so spectral fidelity is preserved (no mixing). Ties resolved by input order. Optionally writes `<output>.sources.tif` (uint16 provenance raster, `0` = nodata, `1..N` = 1-based frame index). [`v1_first_write_wins`](mosaic.py:1) remains index 0 for backward compatibility with saved Model Builder graphs.
- **Dead feather code removed** from [`mosaic.py`](mosaic.py:1): `_feather_weights`, `mosaic_frames_feather` and `mosaic_frames_histmatch_feather` (left as unreachable dead code by the 2026-05-06 visual-mosaic removal) are now fully deleted. The "Project policy — data correctness over visual appearance" section above remains the source of truth for why no feather/histmatch path can be re-introduced as a registered method.
- **Mosaic-quality metrics extended** in [`mosaic_quality.compare_rasters()`](mosaic_quality.py:1): added `coverage_ratio`, `filled_pixel_ratio`, `nodata_fraction_per_band`, plus `*_filled_only` and `*_overlap_only` per-band statistical variants (lets users see how the mosaic performs on the filled-vs-original-vs-overlap regions separately). Fillmask-dependent metrics gracefully return `None` when `<output>.fillmask.tif` is absent (e.g. dry-run or external mosaic). New optional `output_path` kwarg on [`compare_rasters()`](mosaic_quality.py:1) so the helper can locate the side outputs. Extended with `seam_consistency` block, `analyze_sources` provenance block, `n_gap_regions`, `largest_gap_px`, `largest_gap_area_m2`, plus optional `OUTPUT_REPORT_JSON` and `SOURCES_PATH` parameters.
- **New provenance analyzer** [`mosaic_quality.analyze_sources()`](mosaic_quality.py:1) (`output_path`, `frame_paths=None`): consumes `<output>.sources.tif` written by `v2_best_pixel`. Returns `n_sources`, `source_contribution_stats` (per-source pixel-share), and optional `source_filenames` when `frame_paths` is supplied. `overlap_ratio` is intentionally `None` (true overlap cannot be reconstructed from winner-only provenance — documented inline so a future reviewer doesn't try to "fix" it). Wired into the [`mosaic_quality_algorithm.py`](mosaic_quality_algorithm.py:1) report.

**Side outputs convention (current):**
- `<output>.mosaic.tif` — intermediate Stage B mosaic (deleted at end of pipeline run).
- `<output>.fillmask.tif` — interior-only fill mask (uint8, 0/1) used by Stage C; consumed by `mosaic_quality` `*_filled_only` metrics.
- `<output>.sources.tif` — uint16 provenance raster written by `v2_best_pixel` (0 = nodata, 1..N = 1-based frame index); consumed by [`mosaic_quality.analyze_sources()`](mosaic_quality.py:1).
- `<output>.overlap_count.tif` — uint16 raster indicating how many input frames cover each pixel; emitted by all Stage B mosaic methods when `emit_coverage_outputs=True`.
- `<output>.valid_coverage.tif` — uint8 binary mask (0/1) indicating pixels covered by at least one frame; emitted by all Stage B mosaic methods when `emit_coverage_outputs=True`.
- `<output>.rejected.csv` — Stage A audit (`path, reason, measured_value, threshold`); a deliverable, not a temp file.
- `<output>.footprint.geojson` — flight-line footprint polygon (optional vector output) written by the raw georeferencing stage; matches the outer boundary of valid ground samples in the GeoTIFF.

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

## Changelog — 2026-05-07 Phase 1.1 — QGIS Processing algorithm for raw PIKA-L georeferencing

- **New QGIS Processing algorithm** [`airborne_georef_algorithm.py`](airborne_georef_algorithm.py) implementing a [`QgsProcessingAlgorithm`](https://api.qgis.org/api/classQgsProcessingAlgorithm.html) subclass that wraps [`airborne_georef.write_flat_geotiff()`](airborne_georef.py:376) so users can run raw PIKA-L → georeferenced GeoTIFF from the QGIS toolbox.
- **Algorithm parameters**: `BIL` (required raster), `HDR`/`TIMES`/`LCF` (optional auto-discovered sidecar files), `FOV_DEG` (required Double), `GROUND_ALT` (required Double), `DEM` (optional raster for DEM-aware mode), `DST_CRS` (CRS with EPSG:4326 default), `RESOLUTION` (optional Double), `NODATA` (optional Double), `OUTPUT` (raster destination).
- **Registration**: Algorithm registered in [`gaps_filler_provider.py`](gaps_filler_provider.py) as `gapsfiller:airborne_georef` alongside the existing algorithms, positioned in the raw stage before filter/mosaic/fill.
- **Plugin deployment**: Added [`airborne_georef_algorithm.py`](airborne_georef_algorithm.py) to [`pb_tool.cfg`](pb_tool.cfg) `python_files` list for plugin deployment.
- **Acceptance signal**: New algorithm appears in Processing Toolbox → Hyperspectral gaps filler → Raster analysis; running it on a sample PIKA-L flight (cube + LCF + times, optionally DEM) produces a single georeferenced GeoTIFF at the chosen output path with the requested CRS.
- **Caveats**: Boresight angles and time offset parameters not implemented (reserved for Phase 1.4); no footprint polygon side-output (reserved for Phase 1.5).

## Changelog — 2026-05-07 Phase 1.2 — 3-state provenance fillmask

- **Files changed**: [`fill_nodata.py`](fill_nodata.py), [`pipeline.py`](pipeline.py), [`gaps_filler_algorithm.py`](gaps_filler_algorithm.py), [`mosaic_quality.py`](mosaic_quality.py)
- **What changed**: Extended [`fill_nodata.write_interior_fill_mask()`](fill_nodata.py:69) from a 2-state mask (0=fillable, 1=not-fillable) to a 3-state provenance mask with values:
  - `0` = pixel was original (had real data before fill)
  - `1` = pixel was filled (was a nodata interior pixel that got filled)
  - `2` = pixel is outside the flight-line footprint (was nodata and remains nodata, not filled)
- **Semantics**: When `FILL_ONLY_INTERIOR=True` (default), the 3-state mask is generated and consumers treat value 1 as fillable. When `FILL_ONLY_INTERIOR=False` (legacy), no mask is generated and everything is filled as before.
- **Band description**: Added band description `"0=original, 1=filled, 2=outside"` to the fillmask raster for QGIS visualization.
- **Consumer updates**: Updated [`fill_nodata.fill_nodata_file()`](fill_nodata.py:586) and [`fill_nodata.fill_nodata_file_gdal()`](fill_nodata.py:834) to correctly interpret the 3-state mask. Updated [`mosaic_quality.compare_rasters()`](mosaic_quality.py:91) to use `fillmask == 1` for filled-only metrics.
- **Backward compatibility**: Added `three_state` parameter to `write_interior_fill_mask()` to support legacy 2-state mode, though it's not currently used.

## Changelog — 2026-05-07 Phase 1.3 — `v3_vrt` mosaic method via `gdalbuildvrt`

- **New mosaic method** `v3_vrt` registered in `MOSAIC_METHODS` ([`methods.py`](methods.py)) with label "v3 — VRT-based mosaic (no blending; first-tile wins per pixel)". Spectrally faithful implementation using GDAL's `BuildVRT` and `Translate` functions.
- **Implementation**: New function [`mosaic.mosaic_frames_vrt()`](mosaic.py) that builds a VRT from input frames using `gdal.BuildVRT` with resolution="highest" and resampling=nearest (preserves spectral fidelity), then translates to GeoTIFF using `gdal.Translate` with the same creation options as other methods (tiled, deflate compression, BigTIFF).
- **Files changed**: [`mosaic.py`](mosaic.py) (new function), [`methods.py`](methods.py) (registry entry)
- **Overlap behavior**: Uses GDAL's default "highest resolution" rule where the highest resolution source wins for overlapping pixels; no blending or feathering.
- **Memory management**: VRT is created in memory (`/vsimem/`) and properly cleaned up after translation; temporary reprojected frames (when `reproject_to_first=True`) are also cleaned up.
- **Acceptance signal**: Selecting "v3_vrt" in the QGIS Processing algorithm produces a valid GeoTIFF mosaic of the input tiles with byte-for-byte identical results to `v1_first_write_wins` on non-overlapping inputs.

## Changelog — 2026-05-07 Phase 1.4 — Boresight angles + time offset on the raw georef path

- **New parameters** Added four optional kwargs to [`airborne_georef.write_flat_geotiff()`](airborne_georef.py:376):
  - `boresight_roll_deg: float = 0.0`
  - `boresight_pitch_deg: float = 0.0`
  - `boresight_yaw_deg: float = 0.0`
  - `time_offset_s: float = 0.0`
- **Convention** Boresight is a small fixed rotation of the camera frame relative to the IMU body frame, applied as Z-Y-X intrinsic Euler (yaw → pitch → roll), right-handed, degrees, with the convention: positive roll = right wing down, positive pitch = nose up, positive yaw = nose right (clockwise from above). At each frame, the effective camera attitude in the local ENU frame is `R_enu_from_camera = R_enu_from_imu(t) · R_imu_from_camera(boresight)`. `time_offset_s` is added to the image-frame timestamp before looking up the IMU/GPS pose: `t_lookup = t_image + time_offset_s`. Positive value means the images were stamped earlier than GPS, so we shift forward to align.
- **Implementation details** Boresight rotation is applied at the seam where current code rotates from IMU body frame to ENU/camera frame in both [`flat_ground_grid()`](airborne_georef.py:298) and [`dem_ground_grid()`](airborne_georef.py:721). Time offset is applied at the seam where image timestamps are matched against the LCF poses in [`interpolate_poses()`](airborne_georef.py:131). All defaults are zero → strict backward compatibility.
- **Unit assumptions** LCF angles are in radians per Resonon spec; we convert boresight from degrees to radians before composing.
- **QGIS integration** Added the four matching parameters to [`airborne_georef_algorithm.py`](airborne_georef_algorithm.py) as `QgsProcessingParameterNumber` (Double, defaults 0.0, advanced=True) and wired them through to the `write_flat_geotiff()` call.
- **Backward compatibility** With all four kwargs at default 0.0, `write_flat_geotiff()` produces identical output to before. Non-zero boresight rotates the projected ground footprint by the expected amount in the expected direction. Non-zero `time_offset_s` shifts the projected footprint along the flight direction by approximately `speed_m_s * time_offset_s`.

## Changelog — 2026-05-07 Phase 1.5 — Footprint polygon side-output for the raw stage

- **New helper function** [`airborne_georef.extract_footprint_polygon()`](airborne_georef.py:414) that, given the projected ground-grid coordinates from `flat_ground_grid()` / `dem_ground_grid()`, returns the flight-line footprint polygon as a list of (lon, lat) tuples representing the outer hull of all valid (non-nodata) ground samples.
- **Footprint writing function** [`airborne_georef.write_footprint_vector()`](airborne_georef.py:485) that writes the footprint polygon as a vector file using OGR. Supports GeoJSON (default), GPKG, and SHP formats. Format is inferred from file extension.
- **New parameter** Added optional `footprint_path: str | Path | None = None` kwarg to [`airborne_georef.write_flat_geotiff()`](airborne_georef.py:613). When set, writes the footprint as a vector file with CRS matching the output GeoTIFF.
- **Vector schema** The footprint vector file contains one feature with attributes:
  - `flight_line` (string) — derived from input cube basename
  - `n_frames` (int) — number of along-track frames
  - `n_xtrack` (int) — number of across-track samples
  - `mean_alt_m` (float, optional) — mean ground altitude when available
- **QGIS integration** Added `OUTPUT_FOOTPRINT` as `QgsProcessingParameterVectorDestination` (optional) to [`airborne_georef_algorithm.py`](airborne_georef_algorithm.py). When set, passes `footprint_path` to `write_flat_geotiff()`. Default = unset (no footprint output).
- **Naming convention** Parameter description suggests the side-output naming convention: `<output>.footprint.geojson`.
- **Backward compatibility** When `footprint_path` is None (default), behavior is identical to before. No footprint file is written.
- **Files changed**: [`airborne_georef.py`](airborne_georef.py), [`airborne_georef_algorithm.py`](airborne_georef_algorithm.py)

## Changelog — 2026-05-07 Phase 2.1 — JSON serializer for mosaic-quality results

- **Files changed**: [`mosaic_quality.py`](mosaic_quality.py), [`mosaic_quality_algorithm.py`](mosaic_quality_algorithm.py)
- **New functions**:
  - `format_report_json(report: dict, indent: int = 2) -> str` in [`mosaic_quality.py`](mosaic_quality.py) - serializes metrics dict to JSON with proper handling of numpy types, Path objects, and NaN/Inf values
  - `write_json_report(report: dict, path: str | Path) -> None` in [`mosaic_quality.py`](mosaic_quality.py) - writes JSON report to disk
- **JSON top-level keys**: `global_metrics`, `filled_only_metrics`, `overlap_only_metrics`, `per_band`, `coverage`, `provenance`, `meta`
- **Algorithm enhancement**: Added optional `OUTPUT_REPORT_JSON` parameter (`QgsProcessingParameterFileDestination` with `.json` filter) to [`MosaicQualityAlgorithm`](mosaic_quality_algorithm.py:21) - when set, writes a JSON file with all metrics alongside the existing text report
- **Additive functionality**: JSON report is completely additive - text report behavior is unchanged when JSON parameter is not set
- **Acceptance signal**: Setting the JSON parameter produces a `.json` file that round-trips through `json.load` and contains the same numbers as the text report

## Changelog — 2026-05-07 Phase 2.2 — Seam consistency metric

- **Files changed**: [`mosaic_quality.py`](mosaic_quality.py), [`mosaic_quality_algorithm.py`](mosaic_quality_algorithm.py)
- **New function**: `compute_seam_consistency(mosaic_path, sources_path, sample_distance_px=1)` in [`mosaic_quality.py`](mosaic_quality.py) — identifies seam pixels using 4-neighbor connectivity (up/down/left/right) where adjacent pixels have different (non-nodata) source IDs, then computes per-band statistics of absolute differences across seams
- **Seam detection**: For each seam pixel pair (p, q) where `sources[p] != sources[q]`, samples the mosaic at both p and q per band and computes mean/median/p95/max absolute differences
- **Memory efficiency**: Processes bands one at a time to manage memory usage, properly closes GDAL datasets in try/finally blocks
- **Integration**: Added optional `sources_path` parameter to [`compare_rasters()`](mosaic_quality.py:375) — when provided, calls `compute_seam_consistency()` and stores results in `summary["seam_consistency"]`
- **Backward compatibility**: `sources_path=None` (default) maintains existing behavior; seam metrics are `None` when sources raster is absent
- **Text formatter update**: [`format_report()`](mosaic_quality.py:970) displays seam consistency metrics including seam pixel count, seam length, overall statistics, and per-band metrics for first 5 bands
- **JSON formatter update**: [`format_report_json()`](mosaic_quality.py:1079) includes seam consistency metrics under `"seam_consistency"` key
- **Algorithm integration**: Added optional `SOURCES_PATH` parameter to [`MosaicQualityAlgorithm`](mosaic_quality_algorithm.py:23) — when set, passes sources raster path to `compare_rasters()`
- **Acceptance signal**: On a `v2_best_pixel` mosaic with sources raster, the metric is finite and non-negative; on a single-frame mosaic it is 0 or `None`

## Changelog — 2026-05-07 Phase 2.3 — Overlap-count + valid-coverage side rasters from Stage B

- **Files changed**: [`mosaic.py`](mosaic.py), [`mosaic_algorithm.py`](mosaic_algorithm.py), [`pipeline.py`](pipeline.py)
- **New side outputs**: Two extra single-band GeoTIFFs written next to the mosaic:
  - `<output>.overlap_count.tif` — uint16 raster with values 0-N indicating how many input frames cover each pixel
  - `<output>.valid_coverage.tif` — uint8 binary mask (0/1) indicating pixels covered by at least one frame
- **Implementation**: Extended all three mosaic methods (`v1_first_write_wins`, `v2_best_pixel`, `v3_vrt`) to accumulate overlap counts during processing:
  - v1/v2: Accumulated during the same per-tile loop that writes the mosaic (no extra IO)
  - v3: Separate pass after VRT translation (documented caveat in docstring as the only acceptable extra-IO case)
- **Helper function**: Added `_write_count_raster()` helper in [`mosaic.py`](mosaic.py) to write side outputs with same georef/CRS as main mosaic and identical GeoTIFF creation options (compression, tiling)
- **Optional kwarg**: Added `emit_coverage_outputs: bool = True` to all three mosaic functions so callers can disable (default ON because side-outputs are cheap)
- **QGIS integration**: Added advanced boolean parameter `EMIT_COVERAGE_OUTPUTS` (default True) to [`MosaicAlgorithm`](mosaic_algorithm.py:20) that passes through to the mosaic functions
- **Pipeline integration**: Stage B in [`pipeline.py`](pipeline.py) always enables coverage outputs (hardcoded `emit_coverage_outputs=True`)
- **Backward compatibility**: Default ON does not break anything because new files are additive; setting `emit_coverage_outputs=False` produces no side files
- **Acceptance signal**: A v1/v2/v3 mosaic run produces two new files: `<output>.overlap_count.tif` and `<output>.valid_coverage.tif`; `overlap_count` values are non-negative integers; their max equals (or is ≤) the number of input frames; `valid_coverage` = 1 exactly where `overlap_count ≥ 1`; setting `emit_coverage_outputs=False` produces no side files

## Changelog — 2026-05-07 Phase 2.4 — Fill metrics: distinct gap regions + largest gap area

- **Files changed**: [`fill_nodata.py`](fill_nodata.py), [`pipeline.py`](pipeline.py), [`gaps_filler_algorithm.py`](gaps_filler_algorithm.py), [`mosaic_quality.py`](mosaic_quality.py)
- **What changed**: Added two new fill-quality metrics computed from the 3-state fillmask (0=original, 1=filled, 2=outside):
  - `n_gap_regions` (int) = number of distinct connected gap regions before fill
  - `largest_gap_px` (int) = max region size in pixels
  - `largest_gap_area_m2` (float) = largest gap area in square meters (computed from geotransform when available)
- **Implementation details**: Added `compute_gap_region_metrics()` helper function in [`fill_nodata.py`](fill_nodata.py) that uses `scipy.ndimage.label` for connected components labeling (with pure-numpy BFS fallback when scipy is unavailable). The metrics are computed after gap filling in both the pipeline and standalone algorithms.
- **Metrics reporting**: Added logging of gap metrics in both [`pipeline.py`](pipeline.py) and [`gaps_filler_algorithm.py`](gaps_filler_algorithm.py). Extended [`mosaic_quality.compare_rasters()`](mosaic_quality.py:91) to include these metrics in its output dict and updated both text and JSON formatters to display them.
- **Fallback behavior**: When `FILL_ONLY_INTERIOR=False` (legacy mode) or when the fillmask is not available, the metrics are set to `None` gracefully.
- **Acceptance signal**: After a fill run with interior gaps, the log shows `n_gap_regions=...` and `largest_gap_px=...` (and `largest_gap_area_m2=...` when geotransform is known). With `FILL_ONLY_INTERIOR=False` (legacy), behavior unchanged. If a JSON QA report is produced downstream, it contains the new keys.

## Changelog — 2026-05-07 Phase 3 — Cleanup & docs (plan complete)

Documentation-only sweep that closes out [`plan.md`](plan.md:1). No code changes.

- **Files changed**: [`README.md`](README.md), [`project_review.md`](project_review.md).
- **3.1 — [`README.md`](README.md) refresh.** Rewrote the README to reflect the current six Processing algorithms (frame filter, raw airborne georef, mosaic, mosaic quality, fill nodata, full pipeline), each with a short purpose / inputs / outputs / key options block. Added a "Side outputs convention" table covering `<output>.mosaic.tif`, `<output>.fillmask.tif`, `<output>.sources.tif`, `<output>.overlap_count.tif`, `<output>.valid_coverage.tif`, `<output>.rejected.csv`, `<output>.footprint.geojson`. Added a "Spectral-fidelity policy" section stating that all registered mosaic methods (`v1` / `v2` / `v3`) preserve original pixel values — no blending, no histogram matching by default. Added a tight "Опциональные зависимости" matrix covering `scipy`, `scikit-image`, `pymap3d`, `pyproj`, `spectral`. Existing rejection-reason table and "all frames rejected" troubleshooting recipe were preserved and extended to cover v2 / v3 reasons.
- **3.2 — Vendored libraries section.** Added a new top-level "Vendored libraries" section near the top of this file (after Project Overview, before the architecture map) labelling each cloned tree as **used at runtime** ([`pymap3d/`](pymap3d/), [`pyproj/`](pyproj/), [`spectral/`](spectral/)) or **inspection only** ([`rioxarray/`](rioxarray/), [`otb/`](otb/), [`micmac/`](micmac/), [`ODM/`](ODM/)) with a one-line rationale per tree. **No clones were deleted.** Recommends `pip` / QGIS-bundled copies over the vendored trees.
- **3.3 — Deferred core/qgis split note.** Added a "Future architecture notes" subsection with a short paragraph stating that the current ~16-module flat layout is fine, the split should wait until module count exceeds ~25 or a second consumer (e.g. CLI) appears, and when done it must be a single mechanical rename PR — not a staged refactor.
- **Plan status**: [`plan.md`](plan.md:1) is now fully delivered (Phases 1.1 → 2.4 shipped in earlier dated entries above; Phase 3.1 / 3.2 / 3.3 documented here).

## Changelog — 2026-05-07 Documentation synchronization

Documentation updates to synchronize `project_review.md` with current code state:

- `plan.md` deleted (fully implemented)
- Fixed 14 documentation inconsistencies identified in the synchronization review
- Removed outdated references to `hyperspectral_plan.md` (file no longer exists)
- Updated method descriptions to reflect current implementation (no feathering/blending in mosaic methods)
- Refreshed all stale line-number links
- Removed dead links to non-existent `plans/roadmap.md`

## Changelog — 2026-05-07 Code review pass

Code-quality sweep across core modules. Behaviour-preserving except where noted; no public API changes.

**[`fill_nodata.py`](fill_nodata.py:1).**
- Hoisted `from osgeo import gdal` from inside `_fill_band_worker`, [`fill_nodata_file()`](fill_nodata.py:1), and [`fill_nodata_file_gdal()`](fill_nodata.py:1) to the top-of-module imports.
- Extracted duplicated defensive file-deletion logic into module-level helper [`_safe_delete_raster(path, driver_name="GTiff")`](fill_nodata.py:1); both call sites in [`fill_nodata_file()`](fill_nodata.py:1) and [`fill_nodata_file_gdal()`](fill_nodata.py:1) now use it.

**[`frame_filter.py`](frame_filter.py:1).**
- [`_per_band_reject_reason()`](frame_filter.py:1) saturation check now distinguishes integer / floating / other dtypes via `np.issubdtype(...)`, so it no longer raises on non-int non-float arrays.

**[`metadata.txt`](metadata.txt:1).**
- Updated `description=` to correctly describe the plugin as offering multiple gap-filling methods, with GDAL's native `FillNodata` as the default and a pure-Python implementation as an alternative.

**[`envi_io.py`](envi_io.py:1).**
- Moved nested `_convert_list` helper from inside [`read_envi_header()`](envi_io.py:31) to module level. Behaviour unchanged.

**[`airborne_georef.py`](airborne_georef.py:1).**
- Extracted module-level [`_build_rotation_matrix(roll, pitch, yaw)`](airborne_georef.py:1) helper; replaces duplicated rotation-matrix construction in [`flat_ground_grid()`](airborne_georef.py:201), [`dem_ground_grid()`](airborne_georef.py:541), and the boresight rotations in both.
- OGR resource creation in the footprint vector writing block (~lines 557–638) is now wrapped in `try/finally` so dataSource / layer / feature / geometry are always cleaned up on exception.
- Footprint generation now reuses the already-computed `grid` instead of recomputing it.
- Verified: convergence check already uses the `tolerance_m` parameter (no change needed; previous review claim was incorrect).

**[`mosaic.py`](mosaic.py:1).**
- Extracted [`_reproject_if_needed(path, ref_crs, ref_xres, ref_yres, out_dir)`](mosaic.py:1) helper; replaces three duplicated CRS-/resolution-comparison blocks in [`mosaic_frames()`](mosaic.py:96), `mosaic_frames_best_pixel()`, and [`mosaic_frames_vrt()`](mosaic.py:1).
- `mosaic_frames_best_pixel()` no longer reads each source twice per chunk: a `pixel_cache` dict stores per-source pixel data from the validity-mask pass and is reused in the output-build pass; cache is cleared per-band to bound memory.

**[`mosaic_quality.py`](mosaic_quality.py:1).**
- [`_load_fillmask()`](mosaic_quality.py:1) replaced bare `except Exception` with specific `except (OSError, RuntimeError)` so unexpected errors propagate.

### Reviewed but no change needed
- [`fill_nodata.py:699`](fill_nodata.py:699) and [`fill_nodata.py:814`](fill_nodata.py:814) — `mask = marr != 1` is correct for both `rasterio.fill.fillnodata` (mask True = valid → don't fill) and the GDAL conversion path. Not inverted.
- [`mosaic_quality.py`](mosaic_quality.py:46) — `os` is imported at module level (line 46). No missing import.
- [`airborne_georef.py:400`](airborne_georef.py:400) — `enu2geodetic` unpacking is correct (`lat, lon, alt`).

### Backlog / deferred
- ✅ done — I6: expose seam-consistency metrics as [`MosaicQualityAlgorithm`](mosaic_quality_algorithm.py:21) outputs (see Recent changes below).
- ✅ done — QGIS-wrapper cosmetic cleanups: `handle_processing_exception` helper, canvas-styling attachment moved to [`canvas_styling.py`](canvas_styling.py:1), magic-number `280` replaced with `DEFAULT_PIKA_L_BANDS` (see Recent changes below).
- Deferred: larger reorganization — split workspace into `src/` (pure Python, GDAL/rasterio logic) and `qgis/` (QGIS Processing wrappers, plugin entry, provider). The `src/` half landed; the `qgis/` half is still pending and should be done as a single mechanical rename PR.

## Changelog — 2026-05-07 Repository reorganization — pure-Python core moved to `src/`

Layering made explicit: `src/` is now a pure GDAL/rasterio/numpy package with no QGIS dependency (testable, reusable in scripts), while the workspace root holds QGIS Processing wrappers and plugin glue. No logic was changed.

### What changed
- Created new package [`src/`](src/__init__.py:1) with empty [`src/__init__.py`](src/__init__.py:1).
- Moved 9 pure-Python core modules into `src/`: [`airborne_georef.py`](src/airborne_georef.py:1), [`envi_io.py`](src/envi_io.py:1), [`fill_nodata.py`](src/fill_nodata.py:1), [`frame_filter.py`](src/frame_filter.py:1), [`methods.py`](src/methods.py:1), [`models.py`](src/models.py:1), [`mosaic.py`](src/mosaic.py:1), [`mosaic_quality.py`](src/mosaic_quality.py:1), [`pipeline.py`](src/pipeline.py:1).
- All QGIS-facing files stay at the workspace root: [`__init__.py`](__init__.py:1), [`gaps_filler.py`](gaps_filler.py:1), [`gaps_filler_provider.py`](gaps_filler_provider.py:1), the `*_algorithm.py` modules, [`canvas_styling.py`](canvas_styling.py:1), [`metadata.txt`](metadata.txt:1), [`pb_tool.cfg`](pb_tool.cfg:1), etc.
- Updated imports throughout:
  - Inside `src/`: cross-module imports use relative form (e.g. `from .models import …`, `from . import fill_nodata, frame_filter, mosaic`).
  - Root QGIS files now import core logic via `from .src import …` or `from .src.<module> import …`.
- Updated [`pb_tool.cfg`](pb_tool.cfg:1): added `src` to `extra_dirs`; removed the 9 moved files from `python_files`.
- Verification subtask grepped for stale references and caught two leftovers in [`frame_filter_algorithm.py`](frame_filter_algorithm.py:1) and [`hyperspectral_algorithm.py`](hyperspectral_algorithm.py:1) (`from .frame_filter import (…)` → fixed to `from .src.frame_filter import (…)`).

### New project layout
```
gaps_filler/
├── __init__.py                 # plugin entry (classFactory)
├── gaps_filler.py              # plugin class
├── gaps_filler_provider.py     # QGIS Processing provider
├── *_algorithm.py              # six QGIS Processing algorithms
├── canvas_styling.py           # QGIS post-processors
├── metadata.txt, pb_tool.cfg, icon.png, resources.*
└── src/                        # pure-Python core (no QGIS imports)
    ├── __init__.py
    ├── airborne_georef.py
    ├── envi_io.py
    ├── fill_nodata.py
    ├── frame_filter.py
    ├── methods.py
    ├── models.py
    ├── mosaic.py
    ├── mosaic_quality.py
    └── pipeline.py
```

### Backlog / deferred (carried forward)
- ✅ done — seam-consistency metrics exposed on [`MosaicQualityAlgorithm`](mosaic_quality_algorithm.py:21).
- ✅ done — QGIS-wrapper cosmetic cleanups (helper module + canvas-styling extraction + named band-count constant).
- Deferred: complete the `src/` + `qgis/` split — pure-Python core already in [`src/`](src/__init__.py:1); QGIS wrappers still at the workspace root and should be moved into a `qgis/` package as a single mechanical rename PR when scheduled.

## Recent changes — 2026-05-07

- **Seam-consistency metrics on Mosaic Quality.** New core function [`compute_seam_consistency()`](src/mosaic_quality.py:1) detects seam pixel pairs as 4-neighbor pairs with different (non-nodata) source IDs and aggregates per-band absolute differences. Six new `QgsProcessingOutputNumber` outputs on [`MosaicQualityAlgorithm`](mosaic_quality_algorithm.py:21): `SEAM_MEAN_ABS_DIFF`, `SEAM_MEDIAN_ABS_DIFF`, `SEAM_P95_ABS_DIFF`, `SEAM_MAX_ABS_DIFF`, `SEAM_PIXEL_COUNT`, `SEAM_LENGTH_PX`. Backward-compatible: `sources_path` defaults to `None`; metric outputs are `None` and counts `0` on empty overlap.
- **QGIS-wrapper cosmetic cleanups.** New file [`qgis_helpers.py`](qgis_helpers.py:1) with `handle_processing_exception(exc)` consolidating the repeated try/except → `QgsProcessingException` pattern across wrappers. New `attach_rgb_post_processor_if_needed` in [`canvas_styling.py`](canvas_styling.py:1) replaces the inline post-processor blocks in [`gaps_filler_algorithm.py`](gaps_filler_algorithm.py:1), [`mosaic_algorithm.py`](mosaic_algorithm.py:1) and [`airborne_georef_algorithm.py`](airborne_georef_algorithm.py:1). Magic number `280` replaced by `DEFAULT_PIKA_L_BANDS` in [`src/models.py`](src/models.py:1), now used by [`airborne_georef_algorithm.py`](airborne_georef_algorithm.py:1). No public IDs / parameters changed.
- **Fix black borders in georeferenced GeoTIFFs.** Modified [`airborne_georef.write_flat_geotiff()`](src/airborne_georef.py:708) to always set a nodata value in the output profile and properly initialize destination nodata values during reprojection. Previously, georeferenced GeoTIFFs had opaque black borders (0-value fill) instead of transparent nodata areas. The fix adds a default nodata value (NaN for floating point data types, 0 for integer types) when none is provided, ensures the profile always includes a nodata entry, and configures the reprojection to properly handle nodata values with `src_nodata`, `dst_nodata`, and `init_dest_nodata=True` parameters.
- **Fix undefined variable error.** Removed references to undefined variable `_diag_counts` in [`airborne_georef.flat_ground_grid()`](src/airborne_georef.py:201) which was causing runtime errors. These diagnostic counters were not critical to functionality and appear to be leftover debugging code.
