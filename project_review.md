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

1. **`gdal/` is a full clone of the GDAL C++ source repo inside the plugin.** This is the biggest problem:
   - **Size**: hundreds of MB of C++/CMake/docs that have nothing to do with a Python QGIS plugin. It will bloat the repo and break plugin packaging (QGIS Plugin Manager rejects oversized zips).
   - **Licensing**: GDAL is MIT/X-style but redistributing the whole tree drags in many third-party headers (`internal_libqhull/`, etc.) with their own notices — needless legal surface.
   - **Wrong artifact**: that repo is **C++ source**, not a Python library. You cannot `import` from it. The Python bindings (`from osgeo import gdal`) are a separate compiled package.
   - **Fix**: delete `gdal/` from the project and add it to `.gitignore`. QGIS already ships GDAL — just use `from osgeo import gdal, ogr, osr` directly. No install step needed.

2. **`metadata.txt` placeholders not filled in** ([`metadata.txt`](metadata.txt)):
   - `tracker=http://bugs`, `repository=http://repo`, `homepage=http://homepage` — invalid URLs.
   - `about` is identical to `description`; should describe usage/inputs/outputs.
   - `tags=python` — generic; add domain tags like `raster, hyperspectral, orthophoto, gdal`.
   - **Stray invalid line** at [`metadata.txt`](metadata.txt:42): `Category of the plugin: Raster, Vector, Database or Web` is **not** a comment (no `#`) — this can break `configparser`. Either prefix with `#` or delete it.
   - `experimental=True` is fine for now; flip to `False` before publishing.
   - `changelog=` is commented out — start one as soon as you ship anything.

3. **Bug in test file** [`test/test_gaps_filler_dialog.py`](test/test_gaps_filler_dialog.py:17): imports `QDialogButtonBox, QDialog` from `qgis.PyQt.QtGui`. In PyQt5 these live in `QtWidgets`. Test will fail to import on QGIS 3.x. Also imports `from utilities import get_qgis_app` — no `utilities.py` exists in the project. These tests are dead code today.

4. **`run()` recreates nothing on re-open** ([`gaps_filler.py`](gaps_filler.py:188)): `first_start` guard means the dialog is built once and reused. If you later add layer pickers, you must refresh their contents on each open, not rely on construction.

5. **Empty UI**: [`gaps_filler_dialog_base.ui`](gaps_filler_dialog_base.ui) has only OK/Cancel. Before any logic, the dialog needs at least an input layer / file picker and an output path field.

6. **No `Makefile`** even though [`README.txt`](README.txt:16) and [`pb_tool.cfg`](pb_tool.cfg) reference `make test` / pyrcc5 workflow. Either generate one (`pb_tool` does this) or document `pyrcc5 -o resources.py resources.qrc`.

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
