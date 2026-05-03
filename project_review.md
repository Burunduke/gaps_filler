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
