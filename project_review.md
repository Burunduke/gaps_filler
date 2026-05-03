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
