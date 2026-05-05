# Project Review — `gaps_filler`

## Project Overview

`gaps_filler` is a QGIS 3 plugin scaffolded by **Plugin Builder**. Its stated purpose (per [`metadata.txt`](metadata.txt:8)) is to "fill the gaps in hyperspectral photos to make an orthophoto."

Current reality: it is a **pure skeleton**. No domain logic exists. Running the plugin opens an empty dialog with OK/Cancel; pressing OK does nothing (see the `pass` at [`gaps_filler.py`](gaps_filler.py:200)).

## File Structure

| File | Role |
|---|---|
| [`__init__.py`](__init__.py) | QGIS entry point — exposes `classFactory(iface)` that returns a `GapsFiller` instance. |
| [`methods.py`](methods.py) | Per-stage method registries (`FRAME_FILTER_METHODS`, `MOSAIC_METHODS`, `GAP_FILL_METHODS`). Plain Python lists of dicts (`id`, `label`, `tooltip`, `func`) — the dispatch source of truth for the per-stage method dropdowns in the QGIS algorithms. |
| [`gaps_filler.py`](gaps_filler.py) | Main plugin class: registers menu/toolbar action, opens the dialog. No business logic. |
| [`gaps_filler_dialog.py`](gaps_filler_dialog.py) | Thin wrapper that loads the `.ui` file into a `QDialog` subclass. |
| [`gaps_filler_dialog_base.ui`](gaps_filler_dialog_base.ui) | Qt Designer UI — empty 400×300 dialog with only an OK/Cancel button box. |
| [`metadata.txt`](metadata.txt) | Plugin manifest used by QGIS Plugin Manager. |
| [`pb_tool.cfg`](pb_tool.cfg) | Config for `pb_tool` (deploy/compile helper). |
| [`resources.qrc`](resources.qrc) | Qt resource list (just `icon.png`). Must be compiled to `resources.py` via `pyrcc5`. |
| `resources.py` | Compiled output of `resources.qrc` (already present). |
| `icon.png` | Default Plugin Builder icon. |
| [`README.txt`](README.txt) | Auto-generated Plugin Builder "what's next" notes. |
| `README.html` | Same content, HTML form. |
| `test/` | Plugin Builder test scaffolding (QGIS test app, dialog test, metadata validator). |
| `gdal/` | **Full clone of OSGeo/GDAL** — vendored dependency (see Issues). |

## Current State

**Works out of the box:**
- Plugin loads in QGIS 3.44, adds a "Fill the gaps" toolbar/menu entry under "Hyperspectral gaps filler".
- Clicking the action shows the empty dialog.
- [`test/test_init.py`](test/test_init.py) validates that required metadata keys exist.

**Pure boilerplate (no real behavior):**
- The dialog has no input widgets — no layer picker, no parameters, no output path.
- [`gaps_filler.py`](gaps_filler.py:183) `run()` ends with `pass` — OK click does nothing.
- All docstrings/comments are Plugin Builder defaults.
- i18n machinery wired up but no `i18n/*.qm` files exist.

## Issues & Concerns

1. **`metadata.txt` URL placeholders unresolved**: `tracker=http://bugs`, `repository=http://repo`, `homepage=http://homepage` are still placeholder values. Replace with real URLs or remove the lines before publishing. (`tags`, `about`, the stray `Category of the plugin:` line, and other concerns from the previous review have been fixed.)

## Recommendations for a Junior Dev

Keep it simple. Two layers, no more:

1. **Delete `gdal/`.** Use the `osgeo` Python bindings shipped with QGIS:
   ```python
   from osgeo import gdal
   ds = gdal.Open(path)
   ```
   No vendored copy needed.

2. **Split UI from logic.** Create a new file `core.py` next to [`gaps_filler.py`](gaps_filler.py) with **plain functions** that take paths/arrays and return paths/arrays. Example:
   ```python
   # core.py
   from osgeo import gdal

   def fill_gaps(input_path: str, output_path: str, max_distance: int = 100) -> None:
       src = gdal.Open(input_path)
       # ... gdal.FillNodata(...) etc.
   ```
   Then [`gaps_filler.py`](gaps_filler.py:183) `run()` just reads dialog values and calls `core.fill_gaps(...)`. **Do not** make abstract base classes, factories, or service layers — you do not need them.

3. **Add real widgets** to [`gaps_filler_dialog_base.ui`](gaps_filler_dialog_base.ui) in Qt Designer:
   - `QgsMapLayerComboBox` (or `QgsFileWidget`) for the input raster.
   - `QgsFileWidget` for the output path.
   - A `QSpinBox` for "max gap size" or whatever parameter you need.
   Then read them in `run()` after `exec_()` returns truthy.

4. **Fix `metadata.txt`** — real URLs (or remove the lines), better `about`, real tags, remove the stray `Category of the plugin:` line.

5. **Look at GDAL's [`gdal.FillNodata`](https://gdal.org/api/python/osgeo.gdal.html#osgeo.gdal.FillNodata)** — it likely already does most of what you want for raster gaps. Try it before writing custom code.

6. **Use logging via `QgsMessageLog`** instead of `print` once you start adding behavior.

7. **Fix the broken test imports** if you want CI, otherwise delete `test/test_gaps_filler_dialog.py` and `test/test_resources.py` until you actually have something to test.

## Open Questions

Clarify before writing any code:

1. **What is a "gap"?** Missing pixels (NoData) inside a single hyperspectral raster, or empty regions between separate flight-line images that need to be mosaicked?
2. **Input format**: single multiband GeoTIFF? ENVI `.hdr`/`.dat`? a folder of per-band files? a set of georeferenced images to stitch?
3. **Are inputs already georeferenced** (have CRS + extent), or does the plugin need to do orthorectification too? "make an orthophoto" suggests the latter, which is a much bigger scope.
4. **Output**: one merged GeoTIFF? added to the QGIS map canvas automatically?
5. **Fill method**: simple interpolation (`gdal.FillNodata`), inpainting, or borrowing pixels from overlapping neighbor images?
6. **Scale**: typical raster size and band count? Affects whether you can load into memory or must stream.
7. **Target users / QGIS version**: is `qgisMinimumVersion=3.0` realistic, or do you rely on newer APIs?

## Change Log

- **2026-05-02** — Initial project review created.
- **2026-05-02** — Added a pure-Python re-implementation of `GDALFillNodata`
  in [`gaps_filler.py`](gaps_filler.py). New top-level helpers:
  `fill_nodata(band, mask, max_search_dist, smoothing_iterations, nodata, interpolation)`
  (the algorithm), `_scan_quadrants` and `_smooth_step` (private helpers),
  and `fill_nodata_file(input_path, output_path, ...)` (thin GDAL I/O
  wrapper that reads/writes rasters and delegates the actual fill to
  `fill_nodata`). The plugin had no live `gdal.FillNodata` call yet; the
  `run()` method now documents `fill_nodata_file` as the wiring hook for
  future UI work. Dependency added: `numpy` (already shipped with QGIS;
  no new install step). GDAL is used only for raster I/O.
- **2026-05-03** — Issue audit & cleanup pass. Closed 5 of 6 review issues:
  (1) `gdal/` already removed; (3) fixed PyQt5 import in
  [`test/test_gaps_filler_dialog.py`](test/test_gaps_filler_dialog.py:17)
  (`QtGui` → `QtWidgets` for `QDialogButtonBox`/`QDialog`);
  (4) `run()` already rebuilds the dialog every call;
  (5) dialog already has full widget set;
  (6) added a minimal hand-crafted [`Makefile`](Makefile) at repo root with
  `compile` (pyrcc5), `clean`, `test` (placeholder), and `help` targets.
  Partially fixed (2) [`metadata.txt`](metadata.txt): removed stray
  `Category of the plugin:` line, set domain tags
  (`raster, hyperspectral, orthophoto, gdal, nodata, fillnodata`),
  rewrote `about` to be distinct from `description`. Remaining open work:
  placeholder `tracker`/`repository`/`homepage` URLs.

## Spec verification (2026-05-02)

`fillnodata_spec.md` was verified against the implementation in `gaps_filler.py` and removed. All requirements (forward/backward quadrant sweeps with diagonal trackers, IDW `1/d²` and NEAREST modes, `d <= max_search_dist` boundary, 3×3 masked-mean smoothing reading from previous iteration, edge cases for empty mask, all-valid passthrough, `max_search_dist <= 0`, integer dtypes, and NaN handling) are implemented.

## End-to-end wiring (2026-05-02)

The plugin now runs end-to-end from QGIS.

- **Dialog** ([`gaps_filler_dialog.py`](gaps_filler_dialog.py)) is built in
  Python (the `.ui` file is no longer loaded). Widgets mirror QGIS's
  built-in "Fill nodata" tool:
  - Input layer — `QgsMapLayerComboBox` filtered to raster layers.
  - Band number — `QgsRasterBandComboBox`, follows the input layer.
  - Maximum distance (pixels) — `QSpinBox`, default `10`.
  - Smoothing iterations — `QSpinBox`, default `0`.
  - Validity mask (optional) — `QgsMapLayerComboBox` (raster, allows empty).
  - Output raster — `QgsFileWidget` in `SaveFile` mode (GeoTIFF).
- **`run()` flow** ([`gaps_filler.py`](gaps_filler.py)): the dialog is
  rebuilt on every invocation (so layer combos always reflect the current
  project), parameters are read on accept, [`gaps_filler.fill_nodata_file()`](gaps_filler.py:292)
  is called, and on success the output is added to the canvas via
  `iface.addRasterLayer` and a success message is shown on the message
  bar; failures pop up a `QMessageBox.critical`.
- **Extended signature**:
  `fill_nodata_file(input_path, output_path, band_number=1, mask_path=None, max_search_dist=10.0, smoothing_iterations=0)`.
  Only the chosen band is processed (other bands are copied verbatim by
  `CreateCopy`, which also preserves geotransform/projection/nodata). If
  `mask_path` is given, its first band is read and `!= 0` is used as the
  validity mask; otherwise the band's own nodata value drives the mask
  inside `fill_nodata`.

## Processing algorithm refactor plan

> **Why the change?** The previous "Dialog redesign plan" tried to hand-build a Qt dialog that *looks* like the GDAL Fill nodata dialog (tabs, log, progress bar, console-call preview, batch button stub, etc.). That is a lot of UI code to maintain — and to get wrong. QGIS already ships a framework that renders exactly that dialog automatically, given a parameter list: the **Processing framework**. By exposing our gap-filler as a [`QgsProcessingAlgorithm`](https://api.qgis.org/api/classQgsProcessingAlgorithm.html) registered through a [`QgsProcessingProvider`](https://api.qgis.org/api/classQgsProcessingProvider.html), we get — for free — a dialog visually identical to GDAL Fill nodata, plus **batch mode**, **history**, **"Run as Python command"**, model-builder integration, and a standard log/progress pane. The plan below replaces the hand-rolled dialog entirely.

### 1. New file structure

**Add:**

| File | Purpose |
|---|---|
| `gaps_filler_provider.py` | Subclass of [`QgsProcessingProvider`](https://api.qgis.org/api/classQgsProcessingProvider.html). Holds the provider id (`gapsfiller`), display name ("Hyperspectral gaps filler"), icon, and a `loadAlgorithms()` that registers a single `GapsFillerAlgorithm` instance. |
| `gaps_filler_algorithm.py` | Subclass of [`QgsProcessingAlgorithm`](https://api.qgis.org/api/classQgsProcessingAlgorithm.html). Defines parameters in `initAlgorithm()`, runs the work in `processAlgorithm()`. **No Qt UI code** — the dialog is auto-built. |
| `fill_nodata.py` | Pure-Python module containing the existing fill logic, lifted out of [`gaps_filler.py`](gaps_filler.py). Importable from the algorithm with **zero Qt dependency** (only `numpy` + `osgeo.gdal`). |

**Remove / simplify:**

| File | Action |
|---|---|
| [`gaps_filler_dialog.py`](gaps_filler_dialog.py) | **Delete.** No custom dialog any more. |
| [`gaps_filler_dialog_base.ui`](gaps_filler_dialog_base.ui) | **Delete.** Already unused at runtime. |
| [`gaps_filler.py`](gaps_filler.py) | Strip down to the QGIS plugin entry class only: `__init__`, `initGui`, `unload`. Remove the `QAction`, the toolbar/menu wiring, and the dialog import. **Move** `fill_nodata`, `fill_nodata_file`, `_scan_quadrants`, `_smooth_step`, `_box3_sum` into the new `fill_nodata.py`. (Optional in v1: keep one menu shortcut — see §4.) |
| [`__init__.py`](__init__.py) | Unchanged — still returns `GapsFiller(iface)`. |

### 2. Algorithm parameters (`initAlgorithm`)

Mirror QGIS's built-in `gdal:fillnodata` one-for-one. Constants are class attributes (uppercase strings). Use `self.tr(...)` for human labels (i18n is wired up already).

| Const | Qt class | Args (besides name + label) | Default | Notes |
|---|---|---|---|---|
| `INPUT` | `QgsProcessingParameterRasterLayer` | — | — | Source raster. |
| `BAND` | `QgsProcessingParameterBand` | `parentLayerParameterName=self.INPUT` | `1` | Auto-populates with bands of the chosen layer. |
| `DISTANCE` | `QgsProcessingParameterNumber` | `type=QgsProcessingParameterNumber.Integer, minValue=0` | `10` | Maximum search distance in pixels. |
| `ITERATIONS` | `QgsProcessingParameterNumber` | `type=Integer, minValue=0` | `0` | Smoothing iterations. |
| `MASK_LAYER` | `QgsProcessingParameterRasterLayer` | `optional=True` | `None` | Validity mask. |
| `OUTPUT` | `QgsProcessingParameterRasterDestination` | — | — | Destination GeoTIFF (Processing handles "Save to temporary file"). |

**Advanced flag.** GDAL's `fillnodata` exposes two more — *"Additional creation options"* and *"Don't use the default validity mask"* — both flagged `FlagAdvanced`. Per the brief ("only include params we'll actually use"), we **omit them in v1**: `fill_nodata_file` does not accept creation options, and the default-mask toggle is not meaningful for our pure-Python path. They can be added later by appending a `QgsProcessingParameterString` (creation opts) and `QgsProcessingParameterBoolean` (no-mask) and calling `param.setFlags(param.flags() | QgsProcessingParameterDefinition.FlagAdvanced)` before `addParameter(...)`.

Algorithm metadata methods to implement: `name()` → `"fillnodata"`; `displayName()` → `"Fill nodata"`; `group()`/`groupId()` → `"Raster analysis"`/`"rasteranalysis"`; `shortHelpString()` → one paragraph mirroring [`metadata.txt`](metadata.txt) `about`; `createInstance()` → `return GapsFillerAlgorithm()`.

### 3. `processAlgorithm` skeleton

Read inputs, call the extracted fill function, return the output path keyed by `OUTPUT`.

```text
def processAlgorithm(self, parameters, context, feedback):
    src_layer = self.parameterAsRasterLayer(parameters, self.INPUT, context)
    band      = self.parameterAsInt(parameters, self.BAND, context)
    distance  = self.parameterAsInt(parameters, self.DISTANCE, context)
    iters     = self.parameterAsInt(parameters, self.ITERATIONS, context)
    mask_lyr  = self.parameterAsRasterLayer(parameters, self.MASK_LAYER, context)  # may be None
    out_path  = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)

    feedback.pushInfo(f"Filling band {band} of {src_layer.source()} …")
    fill_nodata.fill_nodata_file(
        input_path        = src_layer.source(),
        output_path       = out_path,
        band_number       = band,
        mask_path         = mask_lyr.source() if mask_lyr else None,
        max_search_dist   = distance,
        smoothing_iterations = iters,
        feedback          = feedback,   # NEW — see below
    )
    return {self.OUTPUT: out_path}
```

**Feedback wiring.** Extend `fill_nodata` / `fill_nodata_file` (in `fill_nodata.py`) with one optional argument `feedback=None` of duck type [`QgsProcessingFeedback`](https://api.qgis.org/api/classQgsProcessingFeedback.html). Inside the algorithm:

- Progress: at the start of each quadrant scan / smoothing iteration call `feedback.setProgress(percent)` where `percent` is computed as `100 * step_done / total_steps` (`total_steps = 2 (forward+backward sweeps) + iterations`).
- Logging: `feedback.pushInfo("…")` for milestones; `feedback.reportError("…", fatalError=True)` if GDAL I/O fails.
- Cancellation: at the top of each major loop check `if feedback.isCanceled(): return` (or raise `QgsProcessingException("Canceled")` so Processing reports it cleanly).

`fill_nodata.py` must NOT import anything from `qgis.PyQt` or `qgis.gui`; it only uses the `feedback` object via duck typing, so it stays unit-testable without QGIS.

### 4. Provider registration (`gaps_filler.py`)

Reduced to:

```text
class GapsFiller:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None

    def initGui(self):
        from .gaps_filler_provider import GapsFillerProvider
        self.provider = GapsFillerProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

    def unload(self):
        if self.provider is not None:
            QgsApplication.processingRegistry().removeProvider(self.provider)
            self.provider = None
```

`GapsFillerProvider`:

- `id()` → `"gapsfiller"`
- `name()` → `"Hyperspectral gaps filler"`
- `icon()` → `QIcon(":/plugins/gaps_filler/icon.png")` (already in `resources.py`)
- `loadAlgorithms()` → `self.addAlgorithm(GapsFillerAlgorithm())`

The algorithm now appears under **Processing Toolbox → Hyperspectral gaps filler → Fill nodata** automatically. **No `QAction`, no menu/toolbar entry, no `add_action`, no `tr` boilerplate** — delete all of it from [`gaps_filler.py`](gaps_filler.py).

**Optional menu shortcut (skip in v1).** If a top-level "Raster → Fill the gaps" entry is desired for discoverability, add a single `QAction` in `initGui` whose slot is:

```text
import processing
processing.execAlgorithmDialog("gapsfiller:fillnodata", {})
```

Remove that action in `unload`. Recommendation: **don't bother in v1** — the Toolbox is the QGIS-idiomatic entry point.

### 5. Where the existing fill logic lives (extraction target)

Despite the brief mentioning `gaps_filler_dialog.py`, the actual numpy/GDAL code lives in [`gaps_filler.py`](gaps_filler.py). Functions to **move verbatim** into the new `fill_nodata.py`:

| Function | Line (current) | Role |
|---|---|---|
| [`_box3_sum()`](gaps_filler.py:56) | 56 | 3×3 box sum helper. |
| [`_scan_quadrants()`](gaps_filler.py:68) | 68 | One forward/backward IDW sweep. |
| [`_smooth_step()`](gaps_filler.py:153) | 153 | One masked-mean smoothing pass. |
| [`fill_nodata()`](gaps_filler.py:170) | 170 | Pure-array entry point (numpy in, numpy out). |
| [`fill_nodata_file()`](gaps_filler.py:292) | 292 | GDAL I/O wrapper (path in, path out). Add the optional `feedback=None` parameter here and forward it to `fill_nodata`. |

After extraction [`gaps_filler.py`](gaps_filler.py) keeps **only** the `GapsFiller` plugin class (~30 lines). [`gaps_filler_dialog.py`](gaps_filler_dialog.py) is deleted (it never held the fill logic — it only built the form widgets).

Imports inside `fill_nodata.py`: `import numpy as np`, `from osgeo import gdal`. **Nothing else.**

### 6. `metadata.txt` / `pb_tool.cfg` migration

[`metadata.txt`](metadata.txt) — no schema changes; consider tweaking `description` / `about` to mention "Processing algorithm" instead of "dialog". Optionally bump `qgisMinimumVersion` to `3.14` (when `QgsProcessingParameterBand.parentLayerParameterName` and modern provider API stabilized) — `3.0` is technically too old anyway.

[`pb_tool.cfg`](pb_tool.cfg:51) — update the `[files]` section:

- `python_files`: replace `gaps_filler_dialog.py` with `gaps_filler_provider.py`, `gaps_filler_algorithm.py`, `fill_nodata.py`. Final value:
  ```
  python_files: __init__.py gaps_filler.py gaps_filler_provider.py gaps_filler_algorithm.py fill_nodata.py
  ```
- `main_dialog`: clear it (was `gaps_filler_dialog_base.ui`, now empty/removed).
- `compiled_ui_files`: stays empty.
- `resource_files`: unchanged (`resources.qrc`).
- `extras`, `extra_dirs`, `locales`: unchanged.

[`Makefile`](Makefile) — unchanged (still just `pyrcc5` for resources).

### 7. Step-by-step implementation order

1. **Create `fill_nodata.py`.** Cut-and-paste `_box3_sum`, `_scan_quadrants`, `_smooth_step`, `fill_nodata`, `fill_nodata_file` out of [`gaps_filler.py`](gaps_filler.py). Verify the file imports cleanly (`python -c "import fill_nodata"`) — it must not pull in PyQt/qgis.
2. **Add `feedback` plumbing to `fill_nodata_file` and `fill_nodata`.** Optional kwarg, default `None`. Inside, guard every call: `if feedback is not None: feedback.setProgress(p)` / `feedback.pushInfo(...)`; check `feedback.isCanceled()` at the top of each sweep and each smoothing iteration; on cancel, raise `RuntimeError("canceled")` (the algorithm wrapper translates it to `QgsProcessingException`).
3. **Create `gaps_filler_algorithm.py`.** Subclass `QgsProcessingAlgorithm`. Implement `name`, `displayName`, `group`, `groupId`, `shortHelpString`, `createInstance`, `initAlgorithm` (the 6 parameters from §2), and `processAlgorithm` (the skeleton from §3, calling `fill_nodata.fill_nodata_file`).
4. **Create `gaps_filler_provider.py`.** Subclass `QgsProcessingProvider` per §4. Use `:/plugins/gaps_filler/icon.png` for the icon.
5. **Slim down [`gaps_filler.py`](gaps_filler.py).** Remove every `QAction`, `add_action`, `iface.addToolBarIcon`, `iface.addPluginToMenu`, the `tr` helper, the `run()` method, the dialog import, and all the moved fill-logic functions. Keep only `GapsFiller.__init__/initGui/unload` per §4.
6. **Delete [`gaps_filler_dialog.py`](gaps_filler_dialog.py) and [`gaps_filler_dialog_base.ui`](gaps_filler_dialog_base.ui).** Also delete `test/test_gaps_filler_dialog.py` (it imports the deleted module).
7. **Update [`pb_tool.cfg`](pb_tool.cfg)** per §6.
8. **Smoke-test in QGIS.** Reload plugin → open Processing Toolbox → confirm "Hyperspectral gaps filler → Fill nodata" appears → run on a small raster → verify the auto-generated dialog matches GDAL Fill nodata in look & feel, batch button is live, history records the run, output is added to the canvas.
9. **(Optional follow-up.)** Add the two advanced parameters (`CREATION_OPTIONS`, `NO_MASK`) once `fill_nodata_file` learns to handle them.

## Changelog

- **2026-05-03** — Refactored to QgsProcessingAlgorithm; QGIS now auto-generates a GDAL-FillNoData-style dialog.
  - **Added:** [`fill_nodata.py`](fill_nodata.py) (extracted pure numpy/GDAL fill logic, with optional `feedback` plumbing for progress/cancellation), [`gaps_filler_algorithm.py`](gaps_filler_algorithm.py) (`FillNoDataAlgorithm`, id `gapsfiller:fillnodata`, parameters INPUT/BAND/DISTANCE/ITERATIONS/MASK_LAYER/OUTPUT), [`gaps_filler_provider.py`](gaps_filler_provider.py) (`GapsFillerProvider`, id `gapsfiller`, name "Hyperspectral gaps filler").
  - **Removed:** `gaps_filler_dialog.py`, `gaps_filler_dialog_base.ui` (no longer needed — Processing builds the dialog).
  - **Modified:** [`gaps_filler.py`](gaps_filler.py) slimmed to provider registration only (no `QAction`, no menu/toolbar, no dialog import); [`pb_tool.cfg`](pb_tool.cfg) `python_files` updated and `main_dialog` cleared; [`metadata.txt`](metadata.txt) `description` reworded for the Processing flow, `version` bumped to 0.2, `hasProcessingProvider=yes`.

- **2026-05-03** — Removed `test/test_gaps_filler_dialog.py` (imported the deleted `gaps_filler_dialog` module; would fail to collect).

- **2026-05-03** — Fixed bug where the output raster contained all bands of the input instead of only the selected band. Root cause: [`fill_nodata_file()`](fill_nodata.py:305) used `driver.CreateCopy(output_path, src)`, which clones every source band; only the selected band was then overwritten with filled data, leaving the rest passed through. Replaced with `driver.Create(output_path, W, H, 1, dtype)` plus explicit `SetGeoTransform` / `SetProjection`, and writing the filled array to band 1. Output now matches GDAL `FillNodata` semantics: a single-band raster containing only the user-selected band.

- **2026-05-03** — Follow-up fix: users still saw multi-band output after the previous change. Root cause this time was **distinct from the `CreateCopy` bug**: when the algorithm was re-run with the same output path, an existing multi-band file from a previous (buggy) run was already on disk and was already loaded into the QGIS canvas. `gdal.Driver.Create(path, W, H, 1, dtype)` does **not** robustly truncate a pre-existing dataset that GDAL/QGIS still holds a reference to (filesystem locks on Windows, GDAL block cache on the loaded layer), so the freshly-created "1-band" header could end up sitting on top of stale band data — QGIS continued to report the old band count. Fix in [`fill_nodata_file()`](fill_nodata.py:305): before `driver.Create`, explicitly call `driver.Delete(output_path)` (with an `os.remove` fallback) when the path already exists, then create the new single-band dataset. After write + `FlushCache` + `dst = None`, the file is re-opened read-only and its `RasterCount` is logged via `feedback.pushInfo` — this gives a hard, on-disk proof in the Processing log that exactly **1 band** was written. Also updated the now-stale wording in [`FillNoDataAlgorithm.shortHelpString()`](gaps_filler_algorithm.py:53) (it still claimed other bands were "copied as-is"). End-to-end justification that output is single-band: (a) any pre-existing file at `output_path` is deleted; (b) `driver.Create(..., 1, ...)` creates a fresh dataset with exactly one band; (c) only `dst.GetRasterBand(1).WriteArray(filled)` is called — no loop over bands anywhere in `fill_nodata.py`, `gaps_filler_algorithm.py` or `gaps_filler.py` (the GUI module only registers the provider, no alternate run path); (d) `FlushCache()` + `dst = None` finalize the write; (e) re-opening the file confirms `RasterCount == 1` and logs it.

- **2026-05-05** — [`fill_nodata_file()`](fill_nodata.py:305) now processes **all bands** of the input raster instead of only one. The function loops `for b in range(1, band_count + 1)`, reading each band's own `GetNoDataValue()` and passing it to [`fill_nodata()`](fill_nodata.py:156) so per-band nodata semantics are preserved; the output GeoTIFF is created with the same `band_count`, geotransform and projection as the input, and each output band gets its source nodata sentinel re-applied via `SetNoDataValue`. The output dtype is taken from band 1 (uniform across bands for the rasters we care about — hyperspectral cubes). The legacy ``band_number`` keyword argument is kept in the signature for backwards compatibility (default changed from `1` to `None`) but is **ignored** with a one-line note in the feedback log when supplied; this avoids breaking the existing call sites in [`gaps_filler_algorithm.py`](gaps_filler_algorithm.py:138). The optional external mask still applies to all bands. Note: [`pipeline.py`](pipeline.py:89) is unaffected — it calls the array-level [`fill_nodata()`](fill_nodata.py:156) directly in its own per-band loop and never goes through `fill_nodata_file`.

- **2026-05-05** — Removed the now-obsolete `band_number` parameter entirely. Dropped from the [`fill_nodata_file()`](fill_nodata.py:305) signature (along with its deprecation note in the feedback log) and from [`FillNoDataAlgorithm`](gaps_filler_algorithm.py:22): the `BAND` constant, the `QgsProcessingParameterBand` registration, the `parameterAsInt(... BAND ...)` retrieval, the unused `QgsProcessingParameterBand` import and the `band_number=band` kwarg at the call site are all gone. The algorithm processes every band of the input raster, so a single-band selector no longer made sense. Updated `shortHelpString()` to say "every band" and a multi-band output. No other call site referenced `band_number` (verified by `grep -r band_number *.py` → 0 matches).

## Hyperspectral pipeline (added)

### Goal

Extend the plugin to process PIKA-L hyperspectral drone frames — a set of already-orthorectified per-frame GeoTIFFs sharing CRS and pixel size — into a single gap-filled mosaic. The new end-to-end Processing algorithm rejects obviously-bad frames, mosaics the survivors with a deterministic overlap rule, and fills NoData gaps in every band of the resulting cube. The implementation follows [`hyperspectral_plan.md`](hyperspectral_plan.md:1).

### Pipeline

```
input rasters ──▶ filter ──▶ mosaic ──▶ fill_nodata ──▶ filled mosaic
                  (Stage A)  (Stage B)   (Stage C)
```

### New modules

- [`frame_filter.py`](frame_filter.py:1) — Stage A; per-frame heuristic rejection of bad PIKA-L frames using only `rasterio` + `numpy`. Public API: [`is_bad_frame()`](frame_filter.py:62) (single-frame check, returns `(is_bad, reason)`) and [`filter_frames()`](frame_filter.py:154) (batch wrapper that first computes the median footprint area, then dispatches; returns `(good_paths, rejected_pairs)`). Both accept a `thresholds=` keyword argument carrying a [`FilterThresholds`](frame_filter.py:43) dataclass that bundles all 8 tunables (`skew_max`, `area_lo`, `area_hi`, `aspect_max`, `centre_window`, `min_valid_fraction`, `std_min`, `saturation_fraction`); when omitted the dataclass defaults reproduce the module-level constants, so old call sites keep working unchanged. Rejection reasons now embed both the measured value and the violated threshold (e.g. `"abnormal aspect ratio (ar=2.15 > 2.00)"`).
- [`mosaic.py`](mosaic.py:1) — Stage B; band-streaming mosaic. Public API: [`MosaicInputError`](mosaic.py:33) (raised on incompatible inputs), [`validate_inputs()`](mosaic.py:42) (cross-frame CRS / pixel size / band count / dtype check, returns a summary dict), [`mosaic_frames()`](mosaic.py:96) (writes the mosaic; takes an optional `progress=callable(fraction, message)`). Output is **float32** with **NaN** as NoData; overlapping pixels follow **first-write-wins** (`rasterio.merge.merge` with `method="first"`); container is a tiled, deflate-compressed BigTIFF (512×512 blocks).
- [`pipeline.py`](pipeline.py:1) — Stage C orchestrator that chains A → B → C. Public API: [`run_pipeline(input_paths, output_path, *, max_distance=100, smoothing_iterations=0, progress=None, thresholds=None, log=None) -> dict`](pipeline.py:32). The new `thresholds=` kwarg accepts a [`FilterThresholds`](frame_filter.py:43) (forwarded to Stage A); the new `log=` kwarg accepts a `Callable[[str], None]` invoked once per rejected frame with a human-readable line, so the QGIS Processing log shows live drop-outs. Both default to `None` (old behaviour). The returned dict still carries `input_count`, `kept_count`, `rejected`, `output_path`, and `band_count`.
- [`hyperspectral_algorithm.py`](hyperspectral_algorithm.py:1) — QGIS Processing wrapper. [`HyperspectralPipelineAlgorithm`](hyperspectral_algorithm.py:22) registers as `gapsfiller:hyperspectral_pipeline` (display name "Hyperspectral pipeline (filter, mosaic, fill)", group "Raster analysis"). Parameters: [`INPUT_LAYERS`](hyperspectral_algorithm.py:25), [`MAX_DISTANCE`](hyperspectral_algorithm.py:26), [`SMOOTHING_ITERATIONS`](hyperspectral_algorithm.py:27), [`OUTPUT`](hyperspectral_algorithm.py:28), **plus 8 new threshold inputs** (`SKEW_MAX`, `AREA_LO`, `AREA_HI`, `ASPECT_MAX`, `CENTRE_WINDOW`, `MIN_VALID_FRACTION`, `STD_MIN`, `SATURATION_FRACTION`) shown in the auto-generated dialog as numeric fields with defaults from [`frame_filter.py`](frame_filter.py:33). The algorithm builds a [`FilterThresholds`](frame_filter.py:43) from these and forwards both `thresholds=` and `log=feedback.pushInfo` into [`run_pipeline()`](pipeline.py:32) so per-frame rejections stream into the Processing log.
- [`frame_filter_algorithm.py`](frame_filter_algorithm.py:1) — standalone Stage A wrapper. [`FrameFilterAlgorithm`](frame_filter_algorithm.py:24) registers as `gapsfiller:frame_filter` (display name "Filter bad frames", group "Raster analysis"). Parameters: [`INPUT_LAYERS`](frame_filter_algorithm.py:27), [`OUTPUT_FOLDER`](frame_filter_algorithm.py:28), [`REPORT`](frame_filter_algorithm.py:29) (optional `.txt`), **plus the same 8 threshold inputs** in the QGIS UI. The algorithm builds a [`FilterThresholds`](frame_filter.py:43), passes it to [`filter_frames()`](frame_filter.py:154), and calls `feedback.pushInfo` once per rejected frame so drop-outs appear live in the log alongside the final report file.
- [`mosaic_algorithm.py`](mosaic_algorithm.py:1) — standalone Stage B wrapper. [`MosaicAlgorithm`](mosaic_algorithm.py:20) registers as `gapsfiller:mosaic_frames` (display name "Mosaic frames (first-write-wins)", group "Raster analysis"). Parameters: [`INPUT_LAYERS`](mosaic_algorithm.py:23) (`QgsProcessingParameterMultipleLayers`, raster), [`OUTPUT`](mosaic_algorithm.py:24) (`QgsProcessingParameterRasterDestination`). Thin wrapper around [`mosaic.mosaic_frames`](mosaic.py:96) with a progress callback that forwards to `feedback`.

### Existing files touched

[`gaps_filler_provider.py`](gaps_filler_provider.py:1) is the only existing file modified for the pipeline work. Its [`loadAlgorithms()`](gaps_filler_provider.py:28) now registers four algorithms — the original [`FillNoDataAlgorithm`](gaps_filler_provider.py:9), [`HyperspectralPipelineAlgorithm`](gaps_filler_provider.py:10), [`FrameFilterAlgorithm`](gaps_filler_provider.py:11), and [`MosaicAlgorithm`](gaps_filler_provider.py:12) — so the file carries 4 imports and 4 matching `self.addAlgorithm(...)` lines. [`fill_nodata.fill_nodata`](fill_nodata.py:1) is reused **unchanged at the array level** — `pipeline.py` reads each mosaic band into a numpy array, calls it directly, and writes the filled array back, avoiding a per-band GDAL round-trip.

### Locked decisions

- **Dependency:** `rasterio` is allowed alongside `osgeo.gdal`; the new modules use `rasterio` exclusively, the existing `fill_nodata.py` keeps using `osgeo.gdal`.
- **NoData:** taken as-is from the source frames (`src.nodata`); the mosaic and final output use **NaN** as NoData.
- **Output dtype:** **float32**, regardless of source dtype.
- **CRS / pixel size:** must match across all input frames. [`validate_inputs()`](mosaic.py:42) aborts with [`MosaicInputError`](mosaic.py:33) on any mismatch — there is **no reprojection** in v1.
- **Overlap handling:** **first-write-wins** via [`rasterio.merge.merge`](mosaic.py:164) with `method="first"`.
- **Memory:** band-by-band streaming throughout (Stages B and C), so the worst case stays bounded even for the ~280-band PIKA-L cubes.

### Tuning the filter

- Open **"Filter bad frames"** ([`frame_filter_algorithm.py`](frame_filter_algorithm.py:24)) or **"Hyperspectral pipeline (filter, mosaic, fill)"** ([`hyperspectral_algorithm.py`](hyperspectral_algorithm.py:22)) in the Processing Toolbox; all 8 thresholds are exposed as numeric inputs in the auto-generated dialog.
- Defaults match the module-level constants in [`frame_filter.py`](frame_filter.py:33) (`SKEW_MAX=0.05`, `AREA_LO=0.5`, `AREA_HI=2.0`, `ASPECT_MAX=2.0`, `CENTRE_WINDOW=64`, `MIN_VALID_FRACTION=0.5`, `STD_MIN=1.0`, `SATURATION_FRACTION=0.95`), so a fresh run reproduces the previous behaviour exactly.
- Rejection lines in the Processing log now embed both the measured value AND the violated threshold, so it is obvious which knob to relax. Example: `"abnormal aspect ratio (ar=2.15 > 2.00)"` → raise `aspect_max`.
- If every frame is rejected, scan the live log for the most common reason and bump the matching threshold (e.g. raise `aspect_max` from `2.0` to `3.0`, or raise `skew_max` if frames are all flagged as skewed; raise `area_hi` / lower `area_lo` for footprint outliers).
- Bigger `centre_window` = more pixels feed the variance / saturation / valid-fraction check (more reliable but slower per frame).

### Known limitations / next steps

- No reprojection or resampling — incompatible CRS / pixel size aborts the run.
- Thresholds are global per run; no auto-derivation from per-flight statistics yet.
- Overlap method is fixed to first-write-wins; no feathering or averaging yet.
- No caching of intermediate mosaic — the temp file `<output>.mosaic.tif` is deleted after Stage C.
- The standalone [`FrameFilterAlgorithm`](frame_filter_algorithm.py:24) **copies** kept frames into the output folder (no symlink), so disk usage is roughly doubled for the kept set.
- No tests added (per project policy).

### How to use from QGIS

The Processing Toolbox group **Hyperspectral gaps filler → Raster analysis** now exposes four algorithms:

- **"Filter bad frames"** ([`FrameFilterAlgorithm`](frame_filter_algorithm.py:24)) — Stage A only; copies surviving frames into a folder and writes a rejection report.
- **"Mosaic frames (first-write-wins)"** ([`MosaicAlgorithm`](mosaic_algorithm.py:20)) — Stage B only; mosaics the supplied frames into a single GeoTIFF.
- **"Fill nodata"** ([`FillNoDataAlgorithm`](gaps_filler_algorithm.py:1)) — Stage C only; the original single-raster gap-fill algorithm.
- **"Hyperspectral pipeline (filter, mosaic, fill)"** ([`HyperspectralPipelineAlgorithm`](hyperspectral_algorithm.py:22)) — all three stages end-to-end.

Use a single-stage algorithm when debugging a specific stage, plugging the plugin into a custom Model Builder workflow, or re-running just one step on already-prepared inputs. Use the end-to-end pipeline for normal use — pick raster layers as **Input frames**, set **Maximum distance** and **Smoothing iterations**, choose the **Filled mosaic** output path and run; the rejected-frame report appears in the Processing log.

- **2026-05-05** — Added a mosaic-vs-reference quality assessment feature.
  - **Added:** [`mosaic_quality.py`](mosaic_quality.py) — pure module (numpy + `osgeo.gdal`, no Qt) exposing [`compare_rasters(reference_path, mosaic_path, feedback=None)`](mosaic_quality.py:91) and [`format_report(summary)`](mosaic_quality.py:222). The function loops band-by-band (consistent with the per-band loop introduced in [`fill_nodata.py`](fill_nodata.py:305)) and returns a dict with per-band RMSE / MAE / PSNR / SSIM, plus the mean of each metric across bands.
  - **Added:** [`mosaic_quality_algorithm.py`](mosaic_quality_algorithm.py) — [`MosaicQualityAlgorithm`](mosaic_quality_algorithm.py:21) registered as `gapsfiller:mosaic_quality` (display name "Mosaic quality (vs reference)", group "Raster analysis"). Parameters: `REFERENCE` and `MOSAIC` (`QgsProcessingParameterRasterLayer`). Outputs: `MEAN_RMSE`, `MEAN_MAE`, `MEAN_PSNR`, `MEAN_SSIM` (`QgsProcessingOutputNumber`); per-band values are reported as a readable table via `feedback.pushInfo`.
  - **Modified:** [`gaps_filler_provider.py`](gaps_filler_provider.py:13) — registers the new algorithm alongside the existing four (`self.addAlgorithm(MosaicQualityAlgorithm())`).
  - **Metric definitions.** All metrics are computed on **valid pixels only**, where `valid = (~ref_nodata_mask) & (~mos_nodata_mask)` (NaNs always count as nodata; per-band `GetNoDataValue()` from each raster drives the rest). Arrays are cast to `float64` before arithmetic. **RMSE** = `sqrt(mean((ref - mos)^2))`. **MAE** = `mean(|ref - mos|)`. **PSNR** = `20·log10(data_range) − 10·log10(MSE)` where `data_range = ref_max − ref_min` over the valid reference pixels; if `MSE == 0` or `data_range == 0` PSNR is reported as `inf` (logged). **SSIM** uses [`skimage.metrics.structural_similarity`](https://scikit-image.org/docs/stable/api/skimage.metrics.html#skimage.metrics.structural_similarity); SSIM needs full 2D arrays, so invalid pixels are filled with the reference mean in **both** images so the masked region cancels out. If scikit-image is missing, the algorithm fails with a clear "install scikit-image" message.
  - **NoData handling.** The user explicitly said the mosaic edges are uneven, so nodata exclusion is the **only** spatial filtering — neither raster is cropped or aligned. Both rasters must already share grid (CRS / GeoTransform / size); a mismatch raises `ValueError` and is reported via `feedback.reportError(..., fatalError=True)`. Bands with zero valid overlapping pixels are skipped with a warning and excluded from the means; their indices are listed in the log under "Skipped bands".
  - **Sample log table.** Per-band table format produced by [`format_report()`](mosaic_quality.py:222):
    ```
    Band |       RMSE |        MAE |       PSNR |       SSIM |   Valid px
    -----+------------+------------+------------+------------+-----------
       1 |    12.3456 |     8.9012 |    34.5678 |     0.9123 |    1048576
       2 |    13.1111 |     9.4444 |    33.9999 |     0.9050 |    1048576
    -----+------------+------------+------------+------------+-----------
    MEAN |    12.7283 |     9.1728 |    34.2838 |     0.9087 |
    ```

- **2026-05-05** — Fixed the "size mismatch" failure in
  [`compare_rasters()`](mosaic_quality.py:144). Before this change the
  function aborted with `ValueError: size mismatch …` whenever the
  reference orthophoto and the built mosaic had different extents,
  which is the **normal** case (the mosaic only covers part of the
  flight area). The previous strict `_grids_match` check was replaced
  by a single in-memory `gdal.Warp` call (new helper
  [`_align_reference_to_mosaic()`](mosaic_quality.py:48)) that warps
  the reference onto the mosaic's exact grid — same `outputBounds`,
  same `xRes`/`yRes`, same projection, `resampleAlg="near"` so pixel
  values are not altered, `format="MEM"` so nothing hits disk. After
  warping the two per-band arrays are guaranteed to have identical
  shape, so the comparison proceeds normally and per-band NoData masks
  (preserved through the warp) still drive the valid-pixel filter.
  Chosen approach: **gdal.Warp align** (single API call, junior-
  friendly) over the alternative mask-and-clip path. No new files;
  [`pb_tool.cfg`](pb_tool.cfg) unchanged. Algorithm parameters and
  outputs of [`MosaicQualityAlgorithm`](mosaic_quality_algorithm.py:20)
  are untouched; only its `shortHelpString()` was updated to describe
  the new behaviour.

- **2026-05-05** — Fully rewrote [`hyperspectral_plan.md`](hyperspectral_plan.md)
  as a comparative-analysis document. It is now organised into three
  blocks — **frame filtering**, **mosaic building**, **gap filling** —
  each listing approaches from simple to complex with pros/cons and
  when-to-use guidance. A final actionable **Pipeline TO-DO** checklist
  groups concrete next steps into Robustness, Performance, Quality,
  Usability/QGIS, and Maintenance buckets for reaching commercial-grade
  quality.

- **2026-05-05** — Refactored [`hyperspectral_plan.md`](hyperspectral_plan.md)
  presentation: each of the three stages now labels its approaches as
  `v0`, `v1`, `v2`, … in order of increasing complexity, with explicit
  _implemented_ vs _planned_ tags. Currently implemented working versions:
  filter `v1` (hard-threshold heuristics), mosaic `v1` (first-write-wins),
  gap-fill `v2` (IDW quadrant sweeps). Introduced an **additive evolution**
  principle — older versions are never removed when a new one lands; they
  coexist as user-selectable options so the user can fall back to a
  simpler method if a complex one performs worse. Every version now
  carries a short **"When to use / Limits"** note (1–3 bullets) intended
  as the source of truth for tooltips on per-stage method dropdowns
  (`FRAME_FILTER_METHOD`, `MOSAIC_METHOD`, `GAP_FILL_METHOD`) to be added
  to the QGIS algorithms — the dropdown default is the most reliable
  working version (current `v1`/`v2`), not the most advanced. Added a
  matching cross-cutting item to **Pipeline TO-DO** ("Method-selection
  UX") capturing the enum-per-stage requirement and the never-remove
  rule. No code changed; documentation-only refactor.

- **2026-05-05** — Method-selection UX scaffolding: implemented the
  first TO-DO item from [`hyperspectral_plan.md`](hyperspectral_plan.md:419).
  - **Added:** [`methods.py`](methods.py) — three plain-Python registries
    (`FRAME_FILTER_METHODS`, `MOSAIC_METHODS`, `GAP_FILL_METHODS`); each
    is a list of dicts with `id` / `label` / `tooltip` / `func`.
    Today each list has exactly one entry pointing to the existing
    implementation ([`frame_filter.filter_frames`](frame_filter.py:154),
    [`mosaic.mosaic_frames`](mosaic.py:96),
    [`fill_nodata.fill_nodata_file`](fill_nodata.py:1)).
    Tooltips are taken verbatim from the "When to use / Limits" bullets
    in [`hyperspectral_plan.md`](hyperspectral_plan.md:1). Two helpers
    [`labels()`](methods.py:1) and [`tooltip_block()`](methods.py:1)
    render dropdown options + concatenated help for the QGIS dialog.
  - **Modified:** all four algorithm files now expose a
    `QgsProcessingParameterEnum` per stage with default index 0 (the
    only currently-implemented version):
    [`frame_filter_algorithm.py`](frame_filter_algorithm.py:1) → adds
    `FRAME_FILTER_METHOD`; [`mosaic_algorithm.py`](mosaic_algorithm.py:1)
    → adds `MOSAIC_METHOD`; [`gaps_filler_algorithm.py`](gaps_filler_algorithm.py:1)
    → adds `GAP_FILL_METHOD`; [`hyperspectral_algorithm.py`](hyperspectral_algorithm.py:1)
    → adds all three. `processAlgorithm` in each file reads the enum
    index, looks up the matching registry entry, logs `method["id"]`,
    and calls `entry["func"](...)` instead of the previous hard-coded
    function. The hyperspectral algorithm still delegates the actual
    work to [`pipeline.run_pipeline()`](pipeline.py:32) (whose internal
    callables match the registry entries today); the registry lookup is
    the future plug-point once the pipeline learns to take method
    callables as kwargs. Help text on each enum param is set via
    `setHelp(methods.tooltip_block(...))` so the user sees the
    "When to use / Limits" copy in the QGIS dialog.
  - **Behaviour:** byte-identical to before for the only available
    option in each registry. No helper-function signatures changed; no
    parameter names removed; no new tests added. Future v2/v3/...
    versions plug in additively by appending to the matching list in
    [`methods.py`](methods.py) — no UI churn required.

- **2026-05-05** — Early input validation in
  [`HyperspectralPipelineAlgorithm`](hyperspectral_algorithm.py:34)
  (Pipeline TO-DO item #2 from
  [`hyperspectral_plan.md`](hyperspectral_plan.md:436)). Right after
  collecting input layer paths and before any pipeline work starts,
  the algorithm now calls [`mosaic.validate_inputs()`](mosaic.py:42)
  and re-raises any [`MosaicInputError`](mosaic.py:33) as a
  `QgsProcessingException` with a clear message. This means a
  CRS / pixel-size / band-count / dtype mismatch fails in ~1 second
  instead of after the full Stage A filter pass. Only
  [`hyperspectral_algorithm.py`](hyperspectral_algorithm.py) was
  modified — added `mosaic` to the package import line and a small
  validation block in `processAlgorithm()`. Scope is limited to the
  end-to-end pipeline algorithm per the TO-DO bullet; the standalone
  Stage B [`MosaicAlgorithm`](mosaic_algorithm.py:20) already calls
  `validate_inputs` inside [`mosaic_frames()`](mosaic.py:96), and the
  Stage A / Stage C standalone algorithms operate on inputs that need
  not be mutually compatible. No registry, signature or method-list
  change in [`methods.py`](methods.py); behaviour for valid inputs is
  unchanged.

- **2026-05-05** — Capped open file descriptors in
[`mosaic.mosaic_frames()`](mosaic.py:103) (Pipeline TO-DO item #3
from [`hyperspectral_plan.md`](hyperspectral_plan.md:440)). Previously
every input frame was opened simultaneously per band, which fails on
Windows above ~500 frames (default fd limit). The per-band loop now
splits the input list into chunks of `_MAX_OPEN_SOURCES = 256`,
runs [`rasterio.merge.merge`](mosaic.py:185) on each chunk, and folds
the chunk arrays together with `combined[isnan] = chunk[isnan]` —
identical first-write-wins semantics across the full path list while
never holding more than 256 source readers open at once. Public API,
helper signatures and method registries are unchanged; with ≤ 256
inputs the result is byte-identical to the previous code path. Only
[`mosaic.py`](mosaic.py) was modified.

- **2026-05-05** — Detect & abort on all-NaN bands in
  [`pipeline.run_pipeline()`](pipeline.py:89) (Pipeline TO-DO item #4
  from [`hyperspectral_plan.md`](hyperspectral_plan.md:443)). Inside
  the per-band gap-fill loop, after reading each band, the pipeline
  now checks `np.isfinite(arr).any()` and raises a `RuntimeError`
  naming the band index when no valid pixel exists. Previously a
  fully-corrupted band would be passed to
  [`fill_nodata.fill_nodata()`](fill_nodata.py:1), which has no valid
  samples to interpolate from and could either return the same all-NaN
  array or hit a divide-by-zero — silently masking a broken input as a
  successful run. Only [`pipeline.py`](pipeline.py) was modified; no
  helper signatures, method registries, public APIs or default
  behaviour for healthy inputs changed.

- **2026-05-05** — Plan / pipeline housekeeping pass.
  - **Plan ([`hyperspectral_plan.md`](hyperspectral_plan.md:1)).** Marked
    Pipeline TO-DO items #1–#4 as `[x]` done with a `(done 2026-05-05)`
    trailing tag: Method-selection UX (#1), early input validation
    (#2), capped open file descriptors in
    [`mosaic.mosaic_frames()`](mosaic.py:1) (#3) and the all-NaN-band
    abort in [`pipeline.run_pipeline()`](pipeline.py:1) (#4). All other
    items still show `[ ]` so future runs can tick them off.
  - **Implemented Pipeline TO-DO item #5: handle CRS mismatch with
    optional reprojection.** New optional kwarg
    `reproject_to_first: bool = False` added to
    [`mosaic.validate_inputs()`](mosaic.py:52),
    [`mosaic.mosaic_frames()`](mosaic.py:169) and
    [`pipeline.run_pipeline()`](pipeline.py:37); default `False`
    reproduces the previous behaviour exactly (mismatch still raises
    [`MosaicInputError`](mosaic.py:40)). When `True`,
    [`validate_inputs()`](mosaic.py:52) tolerates CRS / pixel-size
    differences (band count + dtype are still strict), and
    [`mosaic_frames()`](mosaic.py:169) calls a small new helper
    [`_reproject_to_reference()`](mosaic.py:118) that uses
    [`rasterio.warp.calculate_default_transform`](https://rasterio.readthedocs.io/en/stable/api/rasterio.warp.html#rasterio.warp.calculate_default_transform)
    + [`rasterio.warp.reproject`](https://rasterio.readthedocs.io/en/stable/api/rasterio.warp.html#rasterio.warp.reproject)
    (bilinear) to write each mismatched frame to a temporary GeoTIFF
    in the reference CRS / pixel grid; the existing band-streamed
    first-write-wins merge then runs unchanged on the rewritten
    `effective_paths` list, and the temp directory is cleaned up in a
    `finally` block. Files modified: [`mosaic.py`](mosaic.py),
    [`pipeline.py`](pipeline.py),
    [`mosaic_algorithm.py`](mosaic_algorithm.py) (new
    `REPROJECT_TO_FIRST` boolean parameter forwarded to the registry
    call) and
    [`hyperspectral_algorithm.py`](hyperspectral_algorithm.py) (new
    `REPROJECT_TO_FIRST` boolean parameter, threaded through the
    early `validate_inputs()` and `pipeline.run_pipeline()` calls).
    [`frame_filter_algorithm.py`](frame_filter_algorithm.py) and
    [`gaps_filler_algorithm.py`](gaps_filler_algorithm.py) were left
    untouched — they do not call `mosaic_frames()`, so adding a
    reproject toggle there would be dead UI. Method-registry
    signatures in [`methods.py`](methods.py) are unchanged (the kwarg
    is forwarded by name through the registry callable). Verification:
    `python3 -m py_compile` on every modified file → exit 0; `grep`
    over `mosaic_frames|validate_inputs|run_pipeline` confirmed no
    stale call sites.

- **2026-05-05** — Plan / pipeline housekeeping pass.
  - **Plan ([`hyperspectral_plan.md`](hyperspectral_plan.md:446)).** Marked
    Pipeline TO-DO item #5 (Handle CRS mismatch with optional
    reprojection) as `[x]` done with a `(done 2026-05-05)` trailing
    tag — the work itself shipped in the previous changelog entry; this
    pass only updates the checkbox so the plan reflects reality.
  - **Implemented Pipeline TO-DO item #6: persist rejected-frames
    report.** [`pipeline.run_pipeline()`](pipeline.py:87) now writes a
    `<output>.rejected.csv` next to the final mosaic with columns
    `path, reason, measured_value, threshold` for audit. The report is
    written before the all-rejected guard so a user can inspect *why*
    every frame was dropped without re-running Stage A. Two small
    helpers were added to [`pipeline.py`](pipeline.py): a
    [`_parse_reason()`](pipeline.py:44) regex pair that lifts the
    measured value and threshold out of the existing reason strings
    emitted by [`frame_filter.is_bad_frame()`](frame_filter.py:62)
    (e.g. `"abnormal aspect ratio (ar=2.15 > 2.00)"` →
    `("2.15", "2.00")`; the area check's `"allowed=[lo, hi]"` form is
    rendered as a single bracketed string in the `threshold` column),
    and a [`_write_rejected_report()`](pipeline.py:59) writer using the
    stdlib `csv` module (no new dependency). When the reason text does
    not match a known shape (future heuristic, unexpected text) the
    `measured_value` / `threshold` columns stay empty and the full
    reason string is still recorded. A `try/except OSError` around the
    write keeps a disk failure from aborting the pipeline — the error
    is logged via the existing `log` callback. Files modified:
    [`pipeline.py`](pipeline.py) only. Helper signatures unchanged;
    `run_pipeline()` signature, return dict, and default behaviour are
    unchanged for the happy path. The four QGIS algorithms
    ([`frame_filter_algorithm.py`](frame_filter_algorithm.py),
    [`mosaic_algorithm.py`](mosaic_algorithm.py),
    [`gaps_filler_algorithm.py`](gaps_filler_algorithm.py),
    [`hyperspectral_algorithm.py`](hyperspectral_algorithm.py)) needed
    no changes — only the end-to-end pipeline runs Stage A through
    `run_pipeline()`; the standalone Stage A
    [`FrameFilterAlgorithm`](frame_filter_algorithm.py:24) already
    writes its own text report via the existing `REPORT` parameter.
    Verification: `python3 -m py_compile pipeline.py` → exit 0; manual
    smoke test of `_parse_reason()` against all 5 reason shapes from
    [`frame_filter.is_bad_frame()`](frame_filter.py:62) plus an
    unknown-shape sample produced the expected `(measured, threshold)`
    pairs and an empty fallback; `grep -rn run_pipeline` confirmed the
    only call site is
    [`hyperspectral_algorithm.py`](hyperspectral_algorithm.py:295) and
    its kwargs are untouched.

- **2026-05-05** — Plan / pipeline housekeeping pass.
  - **Plan ([`hyperspectral_plan.md`](hyperspectral_plan.md:449)).** Marked
    Pipeline TO-DO item #6 (Persist rejected-frames report) as `[x]`
    done with a `(done 2026-05-05)` trailing tag — the work itself
    shipped in the previous changelog entry; this pass only updates
    the checkbox so the plan reflects reality.
  - **Implemented Pipeline TO-DO item #7: gap-fill v3 (`gdal.FillNodata`).**
    Added [`fill_nodata.fill_nodata_file_gdal()`](fill_nodata.py:436),
    a drop-in alternative to the existing
    [`fill_nodata.fill_nodata_file()`](fill_nodata.py:305) (v2) with
    the **same signature** so the gap-fill registry can dispatch to
    either version without touching any algorithm wrapper. The new
    function: opens the input read-only, defensively deletes any
    pre-existing output file, uses `driver.CreateCopy` to seed the
    output with the input's geometry / projection / dtype / per-band
    nodata, then loops `for b in range(1, band_count + 1)` calling
    [`gdal.FillNodata`](https://gdal.org/api/python/osgeo.gdal.html#osgeo.gdal.FillNodata)
    on each output band in place. Each band's own `NoDataValue`
    (preserved by `CreateCopy`) drives the validity mask when no
    external mask is given; an external `mask_path` overrides it for
    every band. Cancellation via `feedback.isCanceled()` is honoured
    between bands. Per the plan ("keep v2 as the default fallback when
    GDAL is unavailable or misbehaves"), the per-band loop is wrapped
    in a `try/except`: any non-cancellation `Exception` from
    `gdal.FillNodata` is logged and the function delegates to
    [`fill_nodata.fill_nodata_file()`](fill_nodata.py:305) so the
    algorithm still produces an output. Native C speed is reported as
    10-100x faster than the pure-Python quadrant sweeps in
    [`fill_nodata.fill_nodata()`](fill_nodata.py:156).
  - **Registered v3 in [`methods.GAP_FILL_METHODS`](methods.py:70)** as
    a second list entry (`id="v3_gdal_fillnodata"`,
    `func=fill_nodata.fill_nodata_file_gdal`). v2 stays at index 0 so
    the dropdown default is unchanged. Tooltip is the verbatim "When
    to use / Limits" copy from the plan's gap-fill v3 section. Because
    all four QGIS algorithms
    ([`frame_filter_algorithm.py`](frame_filter_algorithm.py),
    [`mosaic_algorithm.py`](mosaic_algorithm.py),
    [`gaps_filler_algorithm.py`](gaps_filler_algorithm.py),
    [`hyperspectral_algorithm.py`](hyperspectral_algorithm.py))
    already build their gap-fill enum from the registry via
    [`methods.labels()`](methods.py:88) /
    [`methods.tooltip_block()`](methods.py:93) and dispatch via
    `entry["func"](...)`, no algorithm-file change was required and
    the four files stay consistent. The standalone Stage C
    [`FillNoDataAlgorithm`](gaps_filler_algorithm.py:22) now actually
    routes through v3 when the user picks it; the end-to-end
    [`HyperspectralPipelineAlgorithm`](hyperspectral_algorithm.py:35)
    still logs the chosen `gf_entry["id"]` but delegates fill to
    [`pipeline.run_pipeline()`](pipeline.py:87) which uses the
    array-level [`fill_nodata.fill_nodata()`](fill_nodata.py:156) by
    design (call sites unchanged) — wiring the file-level v3 callable
    through the pipeline orchestrator is deferred to a future TO-DO
    along with the rest of the registry-driven dispatch refactor
    flagged in [`hyperspectral_algorithm.py`](hyperspectral_algorithm.py:307).
  - **Files modified:** [`fill_nodata.py`](fill_nodata.py),
    [`methods.py`](methods.py),
    [`hyperspectral_plan.md`](hyperspectral_plan.md). No helper
    signatures changed; no parameter names removed; default behaviour
    is unchanged (v2 is still the default). `python3 -m py_compile` on
    every modified `.py` exited 0; `grep -n "fill_nodata_file\|GAP_FILL_METHODS" *.py`
    confirmed no stale call sites and the new function is wired only
    via the registry.

- **2026-05-05** — End-to-end pipeline now honours the gap-fill method
  dropdown via registry-driven Stage C dispatch. Resolves the deferred
  follow-up flagged at the end of Pipeline TO-DO item #7 in
  [`hyperspectral_plan.md`](hyperspectral_plan.md): the combined
  [`HyperspectralPipelineAlgorithm`](hyperspectral_algorithm.py:35)
  exposed the `GAP_FILL_METHOD` enum but
  [`pipeline.run_pipeline()`](pipeline.py:128) hard-coded the array-level
  [`fill_nodata.fill_nodata()`](fill_nodata.py:156) per-band loop, so the
  user's choice was silently ignored.
  - `run_pipeline()` gains exactly one new optional kwarg
    `gap_fill_func=None`; when `None` it defaults to
    [`fill_nodata.fill_nodata_file`](fill_nodata.py:305) (the v2 entry of
    [`methods.GAP_FILL_METHODS`](methods.py:70)). All other public kwargs
    are unchanged.
  - Stage C is now a single file-level call:
    `gap_fill_func(stage_b_path, final_output_path, mask_path=None,
    max_search_dist=..., smoothing_iterations=..., feedback=...)`. The
    Stage-B mosaic is materialised to `<output>.mosaic.tif` (which the
    pipeline already wrote) and consumed from disk by Stage C.
  - The all-NaN-band guard from TO-DO item #4 has been moved one step
    earlier — it now scans the Stage-B mosaic on disk before Stage C is
    invoked, so the abort message and behaviour stay identical
    regardless of whether v2 (pure Python) or v3 (`gdal.FillNodata`)
    runs the fill.
  - [`HyperspectralPipelineAlgorithm`](hyperspectral_algorithm.py:35)
    now passes `gap_fill_func=gf_entry["func"]` into `run_pipeline()`;
    selecting v3 from the dropdown actually invokes
    [`fill_nodata.fill_nodata_file_gdal`](fill_nodata.py:436). The
    standalone [`FillNoDataAlgorithm`](gaps_filler_algorithm.py:1) was
    not touched (it already dispatches through the registry).
  - **Default code path is byte-equivalent for v2:**
    `fill_nodata_file` is itself a per-band loop that calls the same
    array-level [`fill_nodata.fill_nodata()`](fill_nodata.py:156) with
    the same `max_search_dist` and `smoothing_iterations` the pipeline
    used before; nothing else in the new path touches pixel values.
  - **Files modified:** [`pipeline.py`](pipeline.py),
    [`hyperspectral_algorithm.py`](hyperspectral_algorithm.py),
    [`hyperspectral_plan.md`](hyperspectral_plan.md). Helper signatures
    unchanged for `frame_filter.filter_frames`, `mosaic.mosaic_frames`,
    `mosaic.validate_inputs`, `fill_nodata.fill_nodata` (array),
    `fill_nodata.fill_nodata_file`, `fill_nodata.fill_nodata_file_gdal`.
    `python3 -m py_compile pipeline.py hyperspectral_algorithm.py
    fill_nodata.py methods.py` exited 0;
    `grep -n "fill_nodata\.fill_nodata(" pipeline.py` returned no
    matches (Stage C no longer calls the array fn directly);
    `grep -n "gap_fill_func" pipeline.py hyperspectral_algorithm.py`
    confirmed the kwarg is defined, defaulted, and passed end-to-end.

- **2026-05-05** — Footprint-aware gap-fill in the hyperspectral pipeline.
  Both v2 ([`fill_nodata.fill_nodata_file`](fill_nodata.py:305)) and v3
  ([`fill_nodata.fill_nodata_file_gdal`](fill_nodata.py:436)) used to
  spread filled values out to the raster bounding box, far past the
  actual swath. Now [`pipeline.run_pipeline()`](pipeline.py:1) builds a
  validity mask from the Stage-B mosaic (union across bands of finite
  pixels, streamed band-by-band with `np.logical_or`), computes the
  interior holes via `scipy.ndimage.binary_fill_holes` (with a
  pure-numpy 4-connected flood-fill fallback — scipy is not currently
  a project dep, verified by grep), writes a 0/1 uint8 GeoTIFF with
  `0` only on interior holes and `1` everywhere else (valid + outside),
  and forwards it as `mask_path=` to the gap-fill callable. Single
  polarity covers both backends: v2 preserves any pixel with `mask!=0`
  (so outside-footprint NaN stays NaN), v3 only fills targets where
  the mask band is zero (same effect). New opt-out kwarg
  `fill_only_interior: bool = True` on `run_pipeline()`; when `False`
  the call reverts to today's `mask_path=None` behaviour byte-for-byte.
  Exposed as a checkbox `FILL_ONLY_INTERIOR` (default `True`) in
  [`hyperspectral_algorithm.py`](hyperspectral_algorithm.py:1). The
  standalone [`FillNoDataAlgorithm`](gaps_filler_algorithm.py:1) is
  intentionally untouched — it operates on a user-supplied raster
  with a user-supplied mask, where "footprint" is not well-defined.
  The `<output>.fillmask.tif` is removed in `finally` on success and
  failure. **Files modified:** [`pipeline.py`](pipeline.py),
  [`hyperspectral_algorithm.py`](hyperspectral_algorithm.py),
  [`hyperspectral_plan.md`](hyperspectral_plan.md). Helper signatures
  unchanged for `fill_nodata.fill_nodata_file`,
  `fill_nodata.fill_nodata_file_gdal`, `mosaic.mosaic_frames`,
  `mosaic.validate_inputs`, `frame_filter.filter_frames`.
  `python3 -m py_compile pipeline.py hyperspectral_algorithm.py`
  exited 0.

- **2026-05-05** — Footprint-aware gap-fill toggle in the standalone
  Fill NoData algorithm. The interior-hole mask helper, previously
  private as `pipeline._write_interior_fill_mask`, was promoted to a
  public function
  [`fill_nodata.write_interior_fill_mask`](fill_nodata.py:32)
  (rasterio imported lazily so the module's top-level import surface
  stays numpy-only). [`pipeline.py`](pipeline.py:1) now calls the
  public helper; behaviour is byte-equivalent (same scipy-with-numpy
  fallback, same 0/1 mask polarity).
  [`FillNoDataAlgorithm`](gaps_filler_algorithm.py:1) gains a
  `FILL_ONLY_INTERIOR` boolean parameter (default `True`). Mask
  dispatch in `processAlgorithm`: a user-supplied validity mask always
  wins (an info line is logged when the checkbox is also ON);
  otherwise `FILL_ONLY_INTERIOR=True` builds an interior-hole mask
  next to the output as `<output>.fillmask.tif` and forwards it as
  `mask_path=`; otherwise `mask_path=None` is passed (today's legacy
  behaviour reproduced byte-for-byte). The auto-generated mask is
  removed in `finally` on success and on exception. **Files
  modified:** [`fill_nodata.py`](fill_nodata.py:1),
  [`pipeline.py`](pipeline.py:1),
  [`gaps_filler_algorithm.py`](gaps_filler_algorithm.py:1),
  [`hyperspectral_plan.md`](hyperspectral_plan.md:1). Helper
  signatures unchanged for `fill_nodata.fill_nodata_file`,
  `fill_nodata.fill_nodata_file_gdal`, `mosaic.mosaic_frames`,
  `frame_filter.filter_frames`. `python3 -m py_compile
  gaps_filler_algorithm.py fill_nodata.py pipeline.py
  hyperspectral_algorithm.py methods.py` exited 0.
