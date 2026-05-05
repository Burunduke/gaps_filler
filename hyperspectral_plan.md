# Hyperspectral Pipeline — Plan & Comparative Analysis

Three-stage processing pipeline for PIKA-L drone hyperspectral imagery
(per-frame, already orthorectified, single CRS, ~280 bands):

```
input frames ──▶ A. Frame filtering ──▶ B. Mosaic building ──▶ C. Gap filling ──▶ output mosaic
```

Current implementation:
[`frame_filter.py`](frame_filter.py:1),
[`mosaic.py`](mosaic.py:1),
[`fill_nodata.py`](fill_nodata.py:1),
[`pipeline.py`](pipeline.py:1),
QGIS Processing wrappers
[`frame_filter_algorithm.py`](frame_filter_algorithm.py:1),
[`mosaic_algorithm.py`](mosaic_algorithm.py:1),
[`gaps_filler_algorithm.py`](gaps_filler_algorithm.py:1),
[`hyperspectral_algorithm.py`](hyperspectral_algorithm.py:1).

This document compares possible approaches for each stage (simple → complex)
and lists concrete TO-DOs to bring the pipeline to commercial-grade level.

## Versioning principle (additive evolution)

Each stage lists approaches as `v0`, `v1`, `v2`, … **in order of increasing
complexity**, NOT in order of being a replacement. The rules:

- **Older versions are never removed** when a new one is added. They coexist
  as user-selectable options exposed through the QGIS algorithm dialog
  (a per-stage enum/dropdown).
- **Each version is marked _implemented_ or _planned_.** Implemented versions
  are wired into code today; planned versions are design intent only.
- **The dropdown default is the most reliable working version**
  (currently the `v1`-ish heuristic / first-write-wins / IDW path), **not**
  the most advanced one. A more complex method is offered as an opt-in;
  the user can always fall back to a simpler version if a complex one
  performs worse on their data.
- Every version carries a short **"When to use / Limits"** note for the
  end user — those bullets are the source of truth for the dropdown
  tooltips in QGIS.

---

## 1. Frame filtering / rejection

**Goal:** drop obviously-bad frames (skewed turns, take-off/landing, sensor
glitches, saturated, mostly-nodata) before mosaicking. Each rejected frame
must be logged with a reason.

Currently implemented in [`frame_filter.is_bad_frame()`](frame_filter.py:62)
and [`filter_frames()`](frame_filter.py:154): metadata heuristics + one
centre-window pixel read per frame.

### v0 — Pass everything through (no filter) — _planned (trivial)_

- **Description:** skip Stage A; mosaic all frames as-is.
- **Pros:** zero code; never drops a good frame; mosaic still hides bad
  frames if they fall under good ones (first-write-wins) or get filled by
  Stage C.
- **Cons:** bad frames written first poison the output; saturated/skewed
  frames create visible artifacts; wastes I/O on take-off/landing frames.
- **Implementation:** delete Stage A from [`pipeline.run_pipeline()`](pipeline.py:37).
- **When to use / Limits:**
  - Use for prototyping or very small flights (<20 frames) where manual QC
    is cheaper than tuning thresholds.
  - Use when every pixel matters and you'd rather see artifacts than
    silently lose a frame.
  - **Limit:** not safe for production — a single bad frame can dominate
    a strip's first-write-wins region.

### v1 — Hard threshold heuristics — _implemented (current default)_

- **Description:** per-frame rules with fixed thresholds bundled in a
  [`FilterThresholds`](frame_filter.py:43) dataclass: affine **skew**,
  **footprint area** vs median, **aspect ratio**, plus a centre-window
  band-1 read for **valid fraction**, **saturation fraction**, and
  **stddev**. Reject on first failure.
- **Pros:** ~150 LOC; fast (one small window read per frame); thresholds
  exposed in QGIS dialog ([`FrameFilterAlgorithm`](frame_filter_algorithm.py:24));
  rejection log shows measured value vs threshold.
- **Cons:** thresholds are global per run; fragile across flights with
  different altitudes / sensors / lighting; centre window may miss
  edge-only defects; only band 1 is checked.
- **Implementation:** already done. `numpy` + `rasterio.windows.Window`.
- **When to use / Limits:**
  - **Default for production.** Tune
    [`FilterThresholds`](frame_filter.py:43) once per sensor model.
  - Best when all frames in the run share altitude / lighting / sensor.
  - **Limit:** if every frame is rejected, check the live log for the
    most common reason and relax that one threshold (do not jump to v2).

### v2 — Adaptive (per-flight) thresholds — _planned_

- **Description:** compute robust statistics across the input set
  (median, MAD, percentiles) and derive thresholds dynamically, e.g.
  reject if `|metric - median| > k·MAD` for skew, area, mean intensity.
- **Pros:** generalises across flights without manual tuning; robust to
  outliers thanks to MAD.
- **Cons:** needs a first pass over all frames before deciding; harder to
  explain to users; may keep all frames if the whole flight is bad.
- **Implementation:** extend [`filter_frames()`](frame_filter.py:154) —
  it already does a median-area pass; add MAD for the other metrics
  via `numpy.median` + `numpy.abs`. No new dependency.
- **When to use / Limits:**
  - Use when running on many flights with varying conditions and manual
    threshold tuning becomes a burden.
  - **Limit:** assumes the bulk of the flight is "good" — if >50% of
    frames are bad, the median itself is contaminated and v2 keeps
    garbage. Fall back to v1 with stricter thresholds.

### v3 — Per-band quality scan + spectral checks — _planned_

- **Description:** read several bands (e.g. RGB-equivalent, NIR, SWIR
  representatives), check each for saturation / dropout / striping
  (e.g. low row variance ⇒ sensor row glitch); compute per-frame
  spectral angle vs flight median to catch radiometric outliers.
- **Pros:** catches band-specific defects (single dead band, scan-line
  errors) the centre-window check misses; spectrally consistent output.
- **Cons:** ~10× slower per frame (more I/O); needs sensor knowledge
  (which band ranges matter); risk of dropping valid edge frames.
- **Implementation:** loop bands with `rasterio.read(b, window=...)`;
  use `numpy.std` per row for striping detection; `scipy.spatial.distance.cosine`
  for spectral angle.
- **When to use / Limits:**
  - Use before publishing scientific products, or for sensors with
    known band-specific failure modes.
  - **Limit:** ~10× slower than v1 — not for quick previews. Falls back
    to v1 cleanly if you turn it off.

### v4 — ML-based classifier — _planned (out of scope for v1 of the plugin)_

- **Description:** train a lightweight CNN / gradient-boosted classifier
  on hand-labelled good/bad frame thumbnails.
- **Pros:** captures defects no heuristic encodes (motion blur, glare
  patterns, partial cloud).
- **Cons:** needs labelled dataset; new heavy deps (`torch` or
  `scikit-learn` + `opencv`); model drift across sensors; black-box
  rejections hard to debug.
- **Implementation:** thumbnail extraction → `scikit-learn`
  `GradientBoostingClassifier` is the cheapest way in.
- **When to use / Limits:**
  - Only at scale (thousands of flights/day) where heuristic tuning no
    longer scales.
  - **Limit:** black-box rejections are hard to defend to users — always
    keep v1 available so a rejected frame can be re-checked manually.

---

## 2. Mosaic building

**Goal:** combine kept frames into one multi-band raster covering the
union extent. Inputs share CRS and pixel size (validated by
[`validate_inputs()`](mosaic.py:42)). NoData = `NaN`, dtype = `float32`,
~280 bands.

Currently implemented in [`mosaic.mosaic_frames()`](mosaic.py:96):
band-streamed [`rasterio.merge.merge`](mosaic.py:164) with `method="first"`,
output is tiled BigTIFF (512×512, deflate).

### v0 — GDAL VRT mosaic — _planned (preview only)_

- **Description:** build a `gdal.BuildVRT([...], output)` virtual
  mosaic; materialise with `gdal.Translate` only when needed.
- **Pros:** ~5 LOC; instant (no pixel I/O until read); works with QGIS.
- **Cons:** VRT does not natively do feathering; with hundreds of bands
  the VRT XML balloons; downstream tools (`fill_nodata`) still need a
  materialised raster.
- **Implementation:** `from osgeo import gdal; gdal.BuildVRT(...)`.
- **When to use / Limits:**
  - Use for quick previews and debugging frame coverage in QGIS.
  - **Limit:** not a real deliverable — Stage C cannot run on a VRT
    without materialisation. Switch to v1 for production.

### v1 — First-write-wins — _implemented (current default)_

- **Description:** [`rasterio.merge.merge`](mosaic.py:164) with
  `method="first"`, looped over band index `b ∈ [1..N]`. Each band: open
  all sources, merge, write band, close all sources.
- **Pros:** ~50 LOC; deterministic; preserves real radiometry of one
  source frame per pixel (no spectrum mixing); memory bounded (one band
  at a time); proven by `rasterio`.
- **Cons:** visible **seams** at strip boundaries; opens all source
  files per band → risk of fd exhaustion on Windows for large flights
  (>500 frames); chosen "first" frame is order-dependent, not quality-
  based.
- **Implementation:** done; `rasterio.merge` + `rasterio.open` in a band
  loop.
- **When to use / Limits:**
  - **Default for production.** Spectrally faithful — every output pixel
    comes from exactly one source frame, no mixing.
  - Use when downstream analysis is spectral (classifiers, indices).
  - **Limit:** visible seams; if seams are unacceptable, try v3 (best
    placement) or v4 (feathered).

### v2 — Pixel-wise reduction (max / min / mean / median) — _planned_

- **Description:** instead of "first wins", reduce overlapping pixels
  with a statistical operator. `mean` smooths seams; `median` is robust
  to one-frame outliers.
- **Pros:** `mean`/`median` smooth seams without introducing new
  spectra; `max` works for some vegetation indices.
- **Cons:** `max`/`min` bias toward saturation/shadow; `mean` mixes
  calibrations from different acquisition times and **blurs spectra**;
  `median` requires holding all overlapping samples in RAM (or a
  streaming approximation).
- **Implementation:** drop in `method="mean"` for `rasterio.merge` for
  the cheap path. For `median`, accumulate per-pixel sample lists in
  windowed tiles — non-trivial.
- **When to use / Limits:**
  - Use `mean` if seams are unacceptable and slight spectral blur in
    overlap zones is OK (visualisation).
  - Use `median` for robust outlier rejection when individual frames are
    occasionally corrupted in overlap regions.
  - **Limit:** any reduction breaks strict spectral fidelity — do not
    use upstream of a spectral classifier; use v1 instead.

### v3 — Quality-weighted pick (best-frame selection) — _planned_

- **Description:** before mosaicking, score each frame (e.g. distance
  of pixel from frame centre, or per-pixel local variance) and pick the
  best source per pixel. Special case: pick the frame whose footprint
  centre is closest to the pixel.
- **Pros:** seams shifted to lower-quality regions; no spectrum mixing.
- **Cons:** needs an extra pass to build score rasters; harder than
  `merge`; still produces seams (just better-placed).
- **Implementation:** rasterize per-frame distance-to-centre into the
  output grid, then for each pixel pick the source with minimum
  distance. ~80 LOC; `numpy.argmin` over a stack.
- **When to use / Limits:**
  - Use as a mid-step before going to full feathering — keeps spectral
    fidelity (one source per pixel) while reducing visible seams.
  - **Limit:** still seams, just relocated. If you need fully seamless
    output, jump to v4.

### v4 — Feathered / weighted blending — _planned_

- **Description:** weight each source pixel by distance to its frame
  edge (e.g. linear ramp of `max_feather_pixels`). Accumulate
  `Σ(w·value)` and `Σ(w)` per output pixel; final = ratio.
- **Pros:** seamless visual output; industry-standard for orthomosaics.
- **Cons:** ~150–200 LOC; needs distance transform per frame
  (`scipy.ndimage.distance_transform_edt`); doubles RAM (sum + weight
  accumulators); blurs spectra in overlap zones (still better than
  global `mean`).
- **Implementation:** `scipy.ndimage.distance_transform_edt`, two
  windowed accumulators on disk, final divide pass. Reuse band-streaming
  loop from current [`mosaic_frames()`](mosaic.py:96).
- **When to use / Limits:**
  - Use for commercial-grade visual deliverables.
  - **Limit:** spectra blur in overlap zones — not for strict spectral
    analysis. Keep v1 selectable for the scientific export path.

### v5 — Histogram / radiometric matching + feather — _planned_

- **Description:** before blending, normalise each frame's per-band
  histogram to a reference (e.g. flight median) so adjacent frames are
  brightness-matched; then feather (v4).
- **Pros:** removes brightness jumps from changing illumination during
  flight; required for publishable orthomosaics.
- **Cons:** alters source pixel values → no longer faithful for
  scientific spectral analysis; needs reference frame choice; significant
  added complexity.
- **Implementation:** `skimage.exposure.match_histograms` per band, per
  frame, then v4.
- **When to use / Limits:**
  - Purely visual deliverables where uniform brightness matters more
    than radiometric truth.
  - **Limit:** **never** use upstream of a spectral classifier — pixel
    values are altered. Always offer v1 as the fallback for that case.

---

## 3. Gap filling

**Goal:** fill `NaN` pixels in the mosaic (both inter-frame gaps and
in-frame nodata). Output is same shape, same band count, no `NaN`
inside the mosaic footprint.

Currently implemented as a per-band loop in
[`pipeline.run_pipeline()`](pipeline.py:89) calling the array-level
[`fill_nodata.fill_nodata()`](fill_nodata.py:1) (custom IDW with
two-pass quadrant sweeps + optional 3×3 smoothing).

### v0 — Leave gaps (no fill) — _planned (trivial)_

- **Description:** keep `NaN`; let downstream tools handle them.
- **Pros:** zero risk of inventing values; honest about missing data;
  fastest.
- **Cons:** mosaic looks broken in QGIS; many downstream tools choke on
  `NaN`; not acceptable as a deliverable.
- **Implementation:** skip Stage C in [`pipeline.run_pipeline()`](pipeline.py:37).
- **When to use / Limits:**
  - Scientific export where "missing" must stay missing.
  - **Limit:** unusable as a visual deliverable; many downstream raster
    tools fail on `NaN`.

### v1 — Constant fill (zero / band mean) — _planned (trivial)_

- **Description:** replace `NaN` with `0` or per-band mean.
- **Pros:** trivial (`numpy.nan_to_num` or `numpy.where`); no edge
  artifacts.
- **Cons:** introduces obvious flat patches; destroys spectra; useless
  for real analysis.
- **Implementation:** one `numpy` call per band.
- **When to use / Limits:**
  - Quick visual preview only.
  - **Limit:** never for analysis — spectra in filled patches are fake.

### v2 — IDW with quadrant sweeps — _implemented (current default)_

- **Description:** the existing
  [`fill_nodata.fill_nodata()`](fill_nodata.py:1) — same algorithm as
  GDAL's `FillNodata`: forward + backward quadrant scans collecting
  4 nearest valid neighbours within `max_search_dist`, IDW (`1/d²`)
  blend, optional 3×3 masked-mean smoothing.
- **Pros:** already implemented and used in production; pure `numpy`
  (no extra dep); deterministic; bounded by `max_search_dist` so large
  empty regions stay `NaN`; per-band loop in
  [`pipeline.py`](pipeline.py:89) keeps memory bounded.
- **Cons:** **slow** in Python — ~280 bands × O(W·H·max_search_dist)
  is a real bottleneck; quadrant sweeps don't see beyond axis directions
  → diagonal gaps fill less smoothly; smoothing is naive.
- **Implementation:** done. Knobs: `max_distance`, `smoothing_iterations`
  in [`HyperspectralPipelineAlgorithm`](hyperspectral_algorithm.py:22).
- **When to use / Limits:**
  - **Default for production.** Good for small-to-medium mosaics with
    gaps ≤ 100 px.
  - **Limit:** slow — if Stage C runtime hurts, switch to v3 (same
    algorithm, C speed). For gaps > `max_search_dist` the pixel stays
    `NaN` by design.

### v3 — GDAL `FillNodata` (built-in, C-speed) — _planned (highest-ROI upgrade)_

- **Description:** call [`gdal.FillNodata`](https://gdal.org/api/python/osgeo.gdal.html#osgeo.gdal.FillNodata)
  per band on the mosaic.
- **Pros:** native C implementation → 10–100× faster than the Python
  port; same algorithm family (IDW); already shipped with QGIS.
- **Cons:** requires writing the band to a GDAL `MEM` dataset (NaN →
  GDAL nodata sentinel translation); per-call overhead; less control
  over progress reporting.
- **Implementation:** swap the `fill_nodata.fill_nodata()` call in
  [`pipeline.py`](pipeline.py:98) for a `gdal.FillNodata` on a `MEM`
  band. Keep the existing function as a fallback.
- **When to use / Limits:**
  - Use when Stage C runtime hurts and the algorithm is good enough
    (it's the same family as v2).
  - **Limit:** keep v2 selectable as a fallback for environments where
    GDAL's `FillNodata` misbehaves on NaN sentinels.

### v4 — `scipy` interpolation (`griddata`, RBF) — _planned_

- **Description:** for each band, use `scipy.interpolate.griddata`
  (linear / cubic) over the valid pixels.
- **Pros:** smoother than IDW; cubic gives nicer-looking surfaces.
- **Cons:** **does not scale** — `griddata` builds a Delaunay
  triangulation over millions of points; OOM for big mosaics; slow.
- **Implementation:** `scipy.interpolate.griddata` per band per tile.
- **When to use / Limits:**
  - Only on small tiles or downsampled mosaics where smooth surfaces
    matter.
  - **Limit:** OOM on full-size hyperspectral cubes — fall back to v2/v3.

### v5 — OpenCV inpainting (Telea / Navier–Stokes) — _planned_

- **Description:** [`cv2.inpaint`](https://docs.opencv.org/4.x/df/d3d/tutorial_py_inpainting.html)
  per band with `INPAINT_TELEA` or `INPAINT_NS`.
- **Pros:** very fast (C++); visually pleasing for small gaps;
  edge-aware.
- **Cons:** `cv2.inpaint` only accepts `uint8` / `uint16` 1- or
  3-channel images → cast/scale per band → loses precision; new heavy
  dep (`opencv-python` ≈ 60 MB); not designed for large geographic
  gaps.
- **Implementation:** `pip install opencv-python-headless`; per-band
  cast + `cv2.inpaint`.
- **When to use / Limits:**
  - Small in-frame defects (dust, dropouts) on visual bands.
  - **Limit:** precision loss from `uint8`/`uint16` cast — never use on
    bands feeding spectral analysis. Not for large inter-frame gaps.

### v6 — Spectral / cross-band regression fill — _planned_

- **Description:** exploit the ~280-band redundancy: for each pixel
  with `NaN` in band `b`, predict its value from neighbouring bands at
  the same `(row, col)` using a per-pixel linear regression learned on
  rows where all bands are valid.
- **Pros:** uses real signal (spectral correlation) instead of spatial
  guesses; preserves spectra better than IDW for hyperspectral data.
- **Cons:** only works when the gap is band-specific (not common —
  PIKA-L gaps are usually whole-pixel); needs a global regression model
  or per-pixel fit (expensive); new logic to design.
- **Implementation:** `numpy.linalg.lstsq` on a sample of fully-valid
  pixels; ~100 LOC; no new dep.
- **When to use / Limits:**
  - As a **complement** (not replacement) to a spatial fill: do
    spectral fill first for partial-spectrum pixels, then v2/v3 for
    whole-pixel gaps.
  - **Limit:** does nothing when all bands are missing at a pixel —
    must be paired with a spatial method.

### v7 — Deep-learning inpainting — _planned (far future)_

- **Description:** train / use a U-Net or partial-convolutions network
  on hyperspectral cubes.
- **Pros:** state-of-the-art visual + spectral fidelity.
- **Cons:** GPU dependency; massive engineering effort; out of scope
  for a junior dev / QGIS plugin.
- **When to use / Limits:**
  - Never for v1 of the plugin. Treat as a research direction.
  - **Limit:** GPU + labelled training data required; black-box outputs.

---

## Pipeline TO-DO

Concrete, actionable items to bring the pipeline from current state to a
commercial-grade tool. Ordered roughly by ROI.

### Method-selection UX (cross-cutting)

- [x] **Expose method selection as a QGIS algorithm parameter (enum) per
  stage**, with tooltips matching the "When to use / Limits" notes from
  this plan. Each stage gets its own dropdown
  (`FRAME_FILTER_METHOD`, `MOSAIC_METHOD`, `GAP_FILL_METHOD`) wired into
  [`HyperspectralPipelineAlgorithm`](hyperspectral_algorithm.py:22) and
  the corresponding standalone wrappers
  ([`FrameFilterAlgorithm`](frame_filter_algorithm.py:24),
  [`MosaicAlgorithm`](mosaic_algorithm.py:20),
  [`FillNoDataAlgorithm`](gaps_filler_algorithm.py:1)).
  Defaults: `v1` for filter, `v1` for mosaic, `v2` for gap fill (the
  current implemented working versions). **Never remove an older
  version when adding a new one — additive evolution.** (done 2026-05-05)

### Robustness

- [x] **Validate inputs early in [`HyperspectralPipelineAlgorithm`](hyperspectral_algorithm.py:22).**
  Call [`mosaic.validate_inputs()`](mosaic.py:42) **before** Stage A so
  the user gets a clear error in 1 second instead of after the filter
  pass on a CRS-mismatched flight. (done 2026-05-05)
- [x] **Cap open file descriptors in [`mosaic.mosaic_frames()`](mosaic.py:96).**
  Currently opens all `paths` per band — fails at >~500 frames on
  Windows. Chunk into groups of 256 sources, merge group-mosaics last. (done 2026-05-05)
- [x] **Detect & abort on all-NaN bands** in
  [`pipeline.run_pipeline()`](pipeline.py:37) so a corrupted band
  doesn't crash [`fill_nodata.fill_nodata()`](fill_nodata.py:1) silently. (done 2026-05-05)
- [x] **Handle CRS mismatch with optional reprojection.** Add a
  `reproject_to_first` param to [`mosaic.mosaic_frames()`](mosaic.py:96)
  using `rasterio.warp.reproject` instead of aborting. (done 2026-05-05)
- [x] **Persist rejected-frames report.** [`pipeline.run_pipeline()`](pipeline.py:37)
  currently only logs; write `<output>.rejected.csv` with
  `path, reason, measured_value, threshold` for audit. (done 2026-05-05)

### Performance

- [ ] **Add gap-fill v3 ([`gdal.FillNodata`](https://gdal.org/api/python/osgeo.gdal.html#osgeo.gdal.FillNodata))**
  alongside v2 in Stage C. Single biggest speedup. Keep v2 (the Python
  version) as the default fallback when GDAL is unavailable or
  misbehaves.
  - [x] End-to-end pipeline now honours the gap-fill method dropdown
    (registry dispatch through [`run_pipeline()`](pipeline.py:128)) — done 2026-05-05
- [ ] **Windowed band processing in Stage C.** Read & fill in tiles
  (e.g. 2048×2048 with `max_distance` overlap) instead of full-band
  arrays — current code holds an entire band in RAM (~hundreds of MB
  per band on big flights).
- [ ] **Parallelise per-band loop** in
  [`pipeline.run_pipeline()`](pipeline.py:89) with
  `concurrent.futures.ProcessPoolExecutor` (bands are independent).
  Watch GDAL thread-safety: keep one process per band.
- [ ] **Skip redundant frame opens** in
  [`mosaic.mosaic_frames()`](mosaic.py:96) — currently re-opens every
  source per band. Use `rasterio.open` once and pass `indexes=[b]`
  per band; close after the whole loop. Profile fd usage first.


### Usability (QGIS)

- [ ] **Honour `feedback.isCanceled()`** between frames in Stage A and
  between bands in Stages B and C — currently a multi-minute run can
  not be cancelled cleanly.
- [ ] **Granular progress** in
  [`HyperspectralPipelineAlgorithm`](hyperspectral_algorithm.py:22):
  forward [`pipeline.run_pipeline()`](pipeline.py:37)'s `progress` to
  `feedback.setProgress`; current 0.05 / 0.65 / 0.30 split is OK, just
  wire it.
- [ ] **Threshold presets** — add a dropdown ("Permissive / Default /
  Strict") in [`FrameFilterAlgorithm`](frame_filter_algorithm.py:24)
  alongside the 8 raw threshold inputs so junior users don't have to
  understand each knob.
- [ ] **Default output path** — derive from input folder when
  `OUTPUT` is empty, instead of forcing the user to type a path.
- [ ] **Auto-add result to canvas** with sensible band combination
  (e.g. RGB-equivalent indices for PIKA-L) instead of grayscale band 1.

### Maintenance

- [ ] **Pin `rasterio` minimum version** in [`metadata.txt`](metadata.txt:1)
  (`rasterio.merge.merge` `dtype=`/`nodata=` kwargs need ≥ 1.3).
- [ ] **Document the temp-file convention** (`<output>.mosaic.tif`,
  see [`pipeline.py`](pipeline.py:65)) and ensure cleanup on
  `KeyboardInterrupt` / cancellation, not only on success.
- [ ] **Add a "dry run" mode** to
  [`HyperspectralPipelineAlgorithm`](hyperspectral_algorithm.py:22)
  that runs Stage A only and reports kept/rejected counts — saves
  iteration time when tuning thresholds.
- [ ] **Resolve placeholder URLs** in [`metadata.txt`](metadata.txt:1)
  (`tracker`, `repository`, `homepage`) — still flagged in
  [`project_review.md`](project_review.md:1).

### Quality

- [ ] **Add mosaic v4 (feathered blending)** in
  [`mosaic.py`](mosaic.py:1) as an opt-in `method="feather"` option in
  the new `MOSAIC_METHOD` dropdown. Adds `scipy` dep (already pulled by
  `rasterio`'s extras).
- [ ] **Add mosaic v5 (histogram match + feather)** behind the same
  dropdown (default off — destroys spectra).
- [ ] **Add filter v2 (per-flight adaptive thresholds)** in
  [`frame_filter.filter_frames()`](frame_filter.py:154) using
  median ± k·MAD on each metric. Expose `k` instead of all 8 thresholds
  in [`HyperspectralPipelineAlgorithm`](hyperspectral_algorithm.py:22)
  when the user picks `v2` from the filter dropdown.
- [ ] **Add filter v3 (per-band striping / dropout check)** — extend
  [`is_bad_frame()`](frame_filter.py:62) to read 3–5 representative
  bands and check row-variance.
