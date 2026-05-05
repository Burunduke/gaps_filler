# Hyperspectral Pipeline — Architectural Plan

Extension of the [`gaps_filler`](metadata.txt:6) QGIS plugin from a single-raster
gap-filler into a small **3-stage hyperspectral mosaicking pipeline** for
PIKA-L drone imagery.

> **Audience:** junior Python dev. Keep it boring. No abstract base classes,
> no factories, no "engine" objects. Just functions in modules, plus one new
> [`QgsProcessingAlgorithm`](https://api.qgis.org/api/classQgsProcessingAlgorithm.html)
> that orchestrates them.

---

## 1. Pipeline Overview

```
                    ┌──────────────────────────────────────┐
   Folder of        │  Stage A — Filter bad frames          │
   per-frame   ──▶  │  Input : list[Path] (GeoTIFFs)        │
   GeoTIFFs         │  Output: list[Path] (kept frames)     │
                    │  Module: frame_filter.py              │
                    └──────────────┬───────────────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────────────────┐
                    │  Stage B — Mosaic with overlap rule   │
                    │  Input : list[Path] (kept frames)     │
                    │  Output: 1 multi-band GeoTIFF + mask  │
                    │  Module: mosaic.py                    │
                    └──────────────┬───────────────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────────────────┐
                    │  Stage C — Fill gaps (per band)       │
                    │  Input : mosaic GeoTIFF               │
                    │  Output: filled mosaic GeoTIFF        │
                    │  Module: fill_nodata.py (existing)    │
                    │  Driver: pipeline.py                  │
                    └──────────────────────────────────────┘
```

Stages are independent: each can be run on its own (good for debugging).
[`pipeline.py`](pipeline.py) chains them and exposes one new Processing
algorithm `gapsfiller:hyperspectral_pipeline`.

---

## 2. Stage A — Filter Bad Frames

### I/O
- **Input:** `input_folder: Path` — a directory of `*.tif` / `*.tiff`.
- **Output:** `kept: list[Path]`, `rejected: list[(Path, reason)]` (the
  rejected list goes to the Processing log so the user sees *why*).

### Approach
Open each file with [`rasterio`](https://rasterio.readthedocs.io/) (already
a transitive dep of the QGIS Python stack via GDAL; if not, fall back to
`osgeo.gdal` which is guaranteed). For every frame compute a small dict of
features and apply hard thresholds. **Reject if ANY rule fires** — simpler
than score-based ranking and easier to debug.

### Heuristics (GeoTIFF-only — no flight log)

All from the dataset's `transform`, `bounds`, `width`, `height` and one
cheap pixel read.

| # | Signal | What "bad" looks like | Rule of thumb |
|---|---|---|---|
| 1 | **Affine skew / rotation** | Drone in a turn → frame footprint is rotated relative to N-up. | `|b|+|d|` of the affine `(a,b,c,d,e,f)` divided by `|a|+|e|` ≥ `0.10` (more than ~6° off-axis). PIKA-L straight-line passes are ~axis-aligned. |
| 2 | **Footprint area outliers** | Take-off/landing frames cover much smaller (or huge) ground area. | Compute `area = |a*e - b*d| * width * height` (m²). Reject if `area` is outside `[0.5×median, 2×median]` over the whole folder. |
| 3 | **Aspect ratio of footprint** | Squashed/elongated rectangles indicate steep roll/pitch. | `ratio = max(W,H) / min(W,H)` of the *projected* footprint (not pixel grid). Reject if `> 2.0` when nominal sensor ratio is e.g. ~`1.0–1.5`. |
| 4 | **Low variance band** | Sensor saturated or shutter glitch → flat image. | Read 1 representative band (e.g. middle index), sample a 256×256 window from the centre; reject if `std < 1e-3 * dynamic_range` of dtype. |
| 5 | **Saturation fraction** | Direct sun hit / blown highlights. | Same window: reject if `> 30%` pixels equal `dtype max` or are at the band's nodata. |
| 6 | **(Optional) Timestamp / filename order gaps** | If filenames carry a frame counter, very-out-of-sequence frames are suspect. Skip in v1. | — |

Rules 1–3 use **only metadata** (no pixel reads) and are extremely cheap;
4–5 read one small window per file. **Run 1–3 first**, then 4–5 only on
survivors.

### Module
**New:** [`frame_filter.py`](frame_filter.py) — pure functions, no Qt:

- `inspect_frame(path) -> dict` — returns the feature dict above.
- `is_bad(features, thresholds) -> tuple[bool, str]` — verdict + reason.
- `filter_folder(folder, thresholds=None, feedback=None) -> (kept, rejected)`.

Thresholds default to a `DEFAULTS` dict at module top so a junior can tweak
one number without diving into logic.

---

## 3. Stage B — Mosaic with Overlap Handling

### I/O
- **Input:** `frames: list[Path]` (kept from Stage A).
- **Output:**
  - `mosaic_path: Path` — multi-band GeoTIFF in the **union extent** of all
    frames, in the CRS of the first frame (assert all CRSs match; if not,
    abort with a clear error in v1 — reprojection is out of scope).
  - Implicit nodata mask: pixels never covered by any frame stay at the
    band's nodata sentinel — that's exactly what Stage C needs.

### Approach — recommended default

Use [`rasterio.merge.merge`](https://rasterio.readthedocs.io/en/stable/api/rasterio.merge.html)
**band-by-band**, with `method="first"` (last-write-wins by reverse order
≈ first-write-wins by forward order — same family). This is the single
simplest correct mosaic; it is one library call per band and handles the
union-extent + resampling math for us.

### Why not the fancier options?

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **first / last-write-wins** | 1 line of code; deterministic; preserves real radiometry of one frame in each pixel. | Visible seams between strips. | ✅ **Default for v1.** |
| `max` / `min` | Trivial. | Biases toward saturated pixels (`max`) or shadow (`min`); not radiometrically meaningful for hyperspectral. | ❌ |
| `mean` / `average` | Smooths seams. | Blurs spectra; mixes calibrations from different acquisition times; can't be done by `rasterio.merge` directly per band without a count raster. | Skip in v1. |
| **Feathering** (distance-weighted blend at overlap edges) | Best-looking output. | Need a per-frame distance-to-edge raster and weighted accumulator → ~100 LOC; tricky for a junior. | Future work. |

> **Recommended default: `method="first"`**. Document it in the algorithm's
> `shortHelpString` so the user knows seams are expected.

### Streaming for many bands

PIKA-L produces ~`280` bands. Loading all frames × all bands into RAM is
the main risk. Strategy:

- Loop **band index `b = 1..N`**:
  - Open every frame with `rasterio.open(...)`, but only read band `b`
    (rasterio is lazy — `merge` reads windowed).
  - `merge(datasets, indexes=[b], method="first")` → 2-D array.
  - Write band `b` into the destination dataset (created once at `b == 1`
    with `count=N`, geotransform/CRS from the merged extent).
  - Close all sources before the next band (avoids fd leaks on Windows).
- Optional: write the destination as a **tiled BigTIFF** (`BIGTIFF=YES`,
  `TILED=YES`, `BLOCKXSIZE=256`, `BLOCKYSIZE=256`) so Stage C can also
  stream block-wise.

### Module
**New:** [`mosaic.py`](mosaic.py) — pure functions, no Qt:

- `compute_union_grid(frames) -> (transform, width, height, crs, dtype, nodata)`
- `mosaic_band(frames, band_index, dst_band, transform, ...)`
- `mosaic_folder(frames, output_path, method="first", feedback=None) -> Path`

---

## 4. Stage C — Fill Gaps (per band)

### I/O
- **Input:** the mosaic from Stage B.
- **Output:** a same-shape, same-band-count GeoTIFF with NoData filled.

### Approach
Reuse the existing [`fill_nodata.fill_nodata()`](fill_nodata.py:156)
**unchanged**. The current
[`fill_nodata_file()`](fill_nodata.py:305) is single-band, which is fine
— we just call it in a loop.

### Integration

A new thin wrapper alongside the existing one (do **not** modify
[`fill_nodata.py`](fill_nodata.py)):

```text
# pipeline.py
def fill_all_bands(in_path, out_path, max_search_dist, smoothing_iters, feedback):
    with rasterio.open(in_path) as src:
        N = src.count
        profile = src.profile
    # Create dst once with N bands, copy geotransform/CRS/nodata.
    # For b in 1..N:
    #     read band b -> arr
    #     fill_nodata.fill_nodata(arr, nodata=..., max_search_dist=...,
    #                              smoothing_iterations=..., feedback=child_fb)
    #     write filled into dst band b
    #     feedback.setProgress(100 * b / N)
```

Notes:
- Call the **array-level** [`fill_nodata()`](fill_nodata.py:156), **not**
  `fill_nodata_file`, to avoid N round-trips through GDAL `Create`/`Delete`
  and keep band-stream I/O in one place.
- Wrap the per-band iteration with a child `feedback` (or just translate
  `feedback.setProgress` to the outer 0–100 range) so the user sees real
  progress over `N` bands rather than per-band.
- Mask source for `fill_nodata`: derive from the band's own nodata value
  (mosaic write step sets it from the source frames). No separate mask
  raster needed.

---

## 5. New File / Module Layout

**Add these files only.** Do NOT touch existing source files.

| File | Role | Imports allowed |
|---|---|---|
| [`frame_filter.py`](frame_filter.py) | Stage A — pure functions, threshold dict, no Qt. | `numpy`, `rasterio` (or `osgeo.gdal`). |
| [`mosaic.py`](mosaic.py) | Stage B — pure functions; band-streaming mosaic. | `numpy`, `rasterio`. |
| [`pipeline.py`](pipeline.py) | Stage C wrapper + orchestrator that chains A → B → C. Re-uses [`fill_nodata.fill_nodata()`](fill_nodata.py:156). | `numpy`, `rasterio`, `.frame_filter`, `.mosaic`, `.fill_nodata`. |
| [`hyperspectral_algorithm.py`](hyperspectral_algorithm.py) | New `QgsProcessingAlgorithm` subclass: `id="hyperspectral_pipeline"`. Parameters: `INPUT_FOLDER` (file), `OUTPUT` (raster destination), `MAX_DISTANCE`, `SMOOTHING_ITERS`, plus advanced threshold knobs. Calls `pipeline.run(...)`. | `qgis.core`, `.pipeline`. |

**One existing file gets one extra line:** in
[`gaps_filler_provider.py`](gaps_filler_provider.py:25), inside
`loadAlgorithms()`, add `self.addAlgorithm(HyperspectralPipelineAlgorithm())`.
That is the **only** edit to existing code. Everything else is additive.

The plugin then exposes **two** algorithms in the Processing Toolbox under
"Hyperspectral gaps filler":
1. **Fill nodata** (existing single-band tool).
2. **Hyperspectral pipeline** (new end-to-end tool).

A user who wants finer control can still call Stages A/B/C individually
from a Python console using the modules directly.

---

## 6. Sequence (what `pipeline.run` does)

```
run(input_folder, output_path, params, feedback):
    feedback.pushInfo("Stage A: filtering frames…")
    kept, rejected = frame_filter.filter_folder(input_folder, params.thresholds, feedback)
    log_rejections(rejected, feedback)         # so the user can see why
    if not kept: raise QgsProcessingException("No frames passed filter")

    feedback.pushInfo("Stage B: mosaicking…")
    tmp_mosaic = tempdir / "mosaic.tif"
    mosaic.mosaic_folder(kept, tmp_mosaic, method="first", feedback=feedback)

    feedback.pushInfo("Stage C: filling gaps…")
    fill_all_bands(tmp_mosaic, output_path,
                   params.max_search_dist, params.smoothing_iters, feedback)

    return output_path
```

Progress is split roughly: A=10%, B=60%, C=30% (mosaic dominates I/O,
Stage C's per-band cost is the second-biggest item).

---

## 7. Open Questions & Risks

### Questions for the user
1. **Library policy.** Is it OK to add `rasterio` as a dependency, or must
   the plugin stay on `osgeo.gdal` only (as in the existing
   [`fill_nodata.py`](fill_nodata.py:322))? Mosaic/streaming are markedly
   simpler with rasterio; doable but uglier with GDAL alone.
2. **CRS handling.** Are *all* input frames guaranteed to share a CRS and
   pixel size? If not, do we (a) abort with an error, or (b) reproject the
   minority to the majority CRS? **v1 assumes (a)**.
3. **NoData convention.** Do PIKA-L outputs already carry a NoData tag, or
   is "0" used as a fill sentinel? Affects how Stage C's mask is derived.
4. **Output dtype.** Keep source dtype (commonly `uint16` for PIKA-L), or
   promote to `float32` for the mosaic? Promoting avoids overflow at the
   "average" overlap rule (if we add it later) but doubles disk size.
5. **Memory budget.** Typical raster size & count of frames per flight?
   This drives whether band-by-band is enough or we also need windowed
   tile streaming inside each band.
6. **"Bad frame" tolerance.** Are the threshold defaults in §2 acceptable
   as starting values, or should they be derived per-flight (e.g. median
   ± k·MAD)? Per-flight is more robust but slightly more code.

### Technical risks
- **R1 — Memory blowup.** A single PIKA-L flight is easily tens of GB of
  hyperspectral data. Mitigation: band-streaming everywhere; never hold
  more than 2 bands in RAM simultaneously; write tiled BigTIFF.
- **R2 — Seams from `method="first"`.** Visible at strip boundaries.
  Acceptable for v1; document it; revisit with feathering later.
- **R3 — Filter false-positives.** Aggressive thresholds may discard good
  frames at the field edge. Mitigate by always logging rejections with
  the offending feature value, so the user can adjust in one place
  (`frame_filter.DEFAULTS`).
- **R4 — Long runtime, no preview.** End-to-end run could take many
  minutes. Mitigation: granular `feedback.pushInfo`/`setProgress`; honour
  `feedback.isCanceled()` between every band.
- **R5 — Stage C edge artifacts.** `fill_nodata` IDW propagates a *long
  way* if `max_search_dist` is large; on real flight gaps that distance
  may need to be tens of pixels. Surface that knob in the pipeline UI
  and pick a sane default (~`50` px).
- **R6 — File handle exhaustion.** Opening hundreds of frames at once on
  Windows hits the 512-fd limit. Mitigation: open/close per band in
  Stage B rather than keeping a global list of open datasets.

---

## 8. Out of Scope (explicitly)

- Orthorectification (input is already orthorectified per the brief).
- Reprojection between CRSs.
- Radiometric normalisation across frames.
- Feathering / blending.
- Tests (per task constraints).
