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
| [`metadata.txt`](metadata.txt), [`pb_tool.cfg`](pb_tool.cfg), [`resources.qrc`](resources.qrc), `resources.py`, `icon.png` | QGIS plugin manifest, deploy/compile config, Qt resources. |
| [`hyperspectral_plan.md`](hyperspectral_plan.md) | Comparative-analysis design doc — three stages with `v0` / `v1` / `v2` / … approaches (additive evolution: older versions are never removed, they coexist as user-selectable options). Source of truth for tooltip copy. |
| `test/` | Plugin Builder test scaffolding (QGIS test app, metadata validator). |

## Current state

The five algorithms appear in **Processing Toolbox → Hyperspectral gaps filler → Raster analysis**. Use a single-stage algorithm when debugging or plugging into Model Builder; use the end-to-end pipeline for normal runs.

**Method registries (driven by [`methods.py`](methods.py:1), one entry per implemented version):**
- `FRAME_FILTER_METHODS` — `v1_hard_thresholds` (default).
- `MOSAIC_METHODS` — `v1_first_write_wins` (default).
- `GAP_FILL_METHODS` — `v2_idw_quadrants` (default, pure-Python), `v3_gdal_fillnodata` (native C, optional fallback to v2 on failure).

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
- Overlap rule is fixed to first-write-wins; no feathering or averaging.
- Intermediate mosaic (`<output>.mosaic.tif`) is not cached — deleted after Stage C.
- The standalone [`FrameFilterAlgorithm`](frame_filter_algorithm.py:24) **copies** kept frames to its output folder (no symlink), so disk usage roughly doubles.
- No tests added (per project policy); plugin-builder test scaffolding under `test/` is mostly placeholder.
- i18n machinery is wired up but no `i18n/*.qm` files exist.
- `qgisMinimumVersion=3.0` is technically too old for the modern Processing parameter API; `3.14`+ would be more honest.
- Optional dependencies: `scipy` (preferred for `binary_fill_holes` / `binary_closing`; pure-numpy fallback exists), `scikit-image` (required for SSIM in `mosaic_quality`).

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
