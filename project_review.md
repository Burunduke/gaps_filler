# Project Review — `gaps_filler`

## Project Overview

`gaps_filler` is a QGIS 3 plugin scaffolded by **Plugin Builder**. Its stated purpose (per [`metadata.txt`](metadata.txt:8)) is to "fill the gaps in hyperspectral photos to make an orthophoto."

Current reality: it is a **pure skeleton**. No domain logic exists. Running the plugin opens an empty dialog with OK/Cancel; pressing OK does nothing (see the `pass` at [`gaps_filler.py`](gaps_filler.py:200)).

## File Structure

| File | Role |
|---|---|
| [`__init__.py`](__init__.py) | QGIS entry point — exposes `classFactory(iface)` that returns a `GapsFiller` instance. |
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
