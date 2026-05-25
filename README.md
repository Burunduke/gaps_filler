# Hyperspectral gaps filler

Плагин QGIS 3 для подготовки ортофотопланов из гиперспектральных снимков с дрона с интеграцией GPS/IMU и заполнения пропусков (NoData) в растрах. Реализован как Processing-провайдер: алгоритмы регистрируются в *Processing Toolbox → Hyperspectral gaps filler → Raster analysis*.

Имя плагина: **Hyperspectral gaps filler**, версия **0.2** (см. [`metadata.txt`](metadata.txt:1)).

---

## Содержание

1. [Назначение](#назначение)
2. [Архитектура](#архитектура)
3. [Алгоритмы Processing Toolbox](#алгоритмы-processing-toolbox)
4. [Конвенции выходных данных](#конвенции-выходных-данных)
   - [Side outputs](#side-outputs)
   - [Spectral-fidelity policy](#spectral-fidelity-policy)
5. [Установка и зависимости](#установка-и-зависимости)
6. [Использование](#использование)
   - [Расшифровка причин отбраковки в журнале](#расшифровка-причин-отбраковки-в-журнале)
   - [Типичный сценарий: «у меня все кадры отбраковались»](#типичный-сценарий-у-меня-все-кадры-отбраковались)
7. [Ограничения](#ограничения)
8. [Автор и лицензия](#автор-и-лицензия)

---

## Назначение

Плагин решает три связанные задачи обработки гиперспектральных аэроснимков:

1. отбраковка непригодных кадров пушбрумной камеры;
2. сборка отфильтрованных кадров в спектрально верный ортомозаик;
3. заполнение внутренних пропусков (NoData) в результирующем растре.

Сценарий применения — научно-прикладная обработка съёмочных линий гиперспектральной камеры с дрона; сохранение исходных спектральных значений приоритетнее визуального качества швов (см. [Spectral-fidelity policy](#spectral-fidelity-policy)).

---

## Архитектура

Плагин организован как тонкий QGIS-слой над набором pure-Python модулей в [`src/`](src/__init__.py:1) (зависят только от `numpy`, `rasterio`, `osgeo.gdal`, опционально `scipy` / `skimage` / `pymap3d` / `pyproj` / `spectral`). Qt-обёртки `*_algorithm.py` в корне проекта экспонируют ядро как алгоритмы [`QgsProcessingAlgorithm`](https://api.qgis.org/api/classQgsProcessingAlgorithm.html); регистрация выполняется в [`gaps_filler_provider.py`](gaps_filler_provider.py:1).

Сквозной поток обработки:

```
raw .bil ──▶ airborne_georef ──▶ filter ──▶ mosaic ──▶ fill_nodata ──▶ filled mosaic
                                  (Stage A)  (Stage B)   (Stage C)
                                                                    ──▶ mosaic_quality (vs reference)
```

Подробное описание модулей и их обязанностей вынесено в [`project_review.md`](project_review.md:1).

---

## Алгоритмы Processing Toolbox

Шесть алгоритмов регистрируются в [`gaps_filler_provider.py`](gaps_filler_provider.py:30) в группе **Raster analysis**. Отдельные стадии предназначены для отладки и сборки в Model Builder; сквозной конвейер — для штатных запусков.

### 1. Filter bad frames — `gapsfiller:frame_filter`

Реализован в [`frame_filter_algorithm.py`](frame_filter_algorithm.py:1). Применяет эвристику отбраковки гиперспектральных кадров и копирует принятые в указанную папку; в журнал и (опционально) в отчёт пишутся причины отбраковки.

- **Inputs:** `INPUT_LAYERS` (список растров).
- **Outputs:** `OUTPUT_FOLDER` (папка с принятыми кадрами), `REPORT` (опц. `.txt`).
- **Ключевые опции:** `FRAME_FILTER_METHOD` (`v1_hard_thresholds` по умолчанию, `v2_adaptive_mad`, `v3_per_band`); `THRESHOLD_PRESET` (Custom / Permissive / Default / Strict); восемь raw-порогов (`SKEW_MAX`, `AREA_LO`, `AREA_HI`, `ASPECT_MAX`, `CENTRE_WINDOW`, `MIN_VALID_FRACTION`, `STD_MIN`, `SATURATION_FRACTION`); v2-параметр `K_MAD`; v3-параметры `MAX_DROPOUT_FRAC`, `MAX_STRIPE_RATIO`. Метод-специфичные пороги скрыты под Advanced parameters.

### 2. Airborne georeference (raw hyperspectral) — `gapsfiller:airborne_georef`

Реализован в [`airborne_georef_algorithm.py`](airborne_georef_algorithm.py:1). Преобразует сырой `.bil`-куб гиперспектральной камеры и сайдкары `.lcf` / `.times` в георетированный GeoTIFF. Поддерживает плоскоземельный (`GROUND_ALT`) и DEM-aware варианты. Применяется перед фильтром в случае сырых pushbroom-кадров без геопривязки.

- **Inputs:** `BIL` (raw cube), опционально `HDR` / `TIMES` / `LCF` (auto-discovered), `DEM` (опц.).
- **Outputs:** `OUTPUT` (GeoTIFF), `OUTPUT_FOOTPRINT` (опц. вектор `<output>.footprint.geojson`).
- **Ключевые опции:** `FOV_DEG` (обязательный — линзы гиперспектральных камер различаются), `GROUND_ALT`, `DST_CRS` (по умолчанию EPSG:4326), `RESOLUTION`, `NODATA`; advanced — boresight `BORESIGHT_ROLL_DEG` / `BORESIGHT_PITCH_DEG` / `BORESIGHT_YAW_DEG` и `TIME_OFFSET_S` для калибровки.

### 3. Mosaic frames — `gapsfiller:mosaic_frames`

Реализован в [`mosaic_algorithm.py`](mosaic_algorithm.py:1). Объединяет отфильтрованные кадры в единый многоканальный GeoTIFF. Все три зарегистрированных метода спектрально верны: каждый выходной пиксель берётся из ровно одного исходного кадра (см. [Spectral-fidelity policy](#spectral-fidelity-policy)).

- **Inputs:** `INPUT_LAYERS` (растры с одинаковой CRS и pixel size, либо `REPROJECT_TO_FIRST=True`).
- **Outputs:** `OUTPUT` (GeoTIFF); по умолчанию также `<output>.overlap_count.tif` и `<output>.valid_coverage.tif`; для метода v2 — `<output>.sources.tif` (provenance).
- **Ключевые опции:** `MOSAIC_METHOD` — `v1_first_write_wins` (индекс 0, обратная совместимость), `v2_best_pixel` (индекс 1, рекомендуется — пиксель из кадра с максимальным расстоянием до края), `v3_vrt` (через `gdalbuildvrt`); `REPROJECT_TO_FIRST` (опц. перепроецирование к CRS первого входа); `EMIT_COVERAGE_OUTPUTS` (по умолчанию ON).

### 4. Mosaic quality — `gapsfiller:mosaic_quality`

Реализован в [`mosaic_quality_algorithm.py`](mosaic_quality_algorithm.py:1). Сравнивает мозаику с эталонным растром: per-band RMSE / MAE / PSNR / SSIM, whole-cube SAM / SAM_DEG, агрегаты MEAN / WORST / P05 с указанием «худших» каналов. Опционально читает `<output>.fillmask.tif` и `<output>.sources.tif` для дополнительных метрик (filled-only, overlap-only, seam consistency, source contributions).

- **Inputs:** `REFERENCE`, `MOSAIC`, опц. `SOURCES_PATH` (для seam-метрик).
- **Outputs:** числовые поля `MEAN_<M>` / `WORST_<M>` / `P05_<M>` / `WORST_<M>_BAND` / `P05_<M>_BAND` для каждой из RMSE / MAE / PSNR / SSIM, плюс `SAM` и `SAM_DEG`; текстовый отчёт; опц. `OUTPUT_REPORT_JSON` — структурированный JSON со всеми метриками.

### 5. Fill nodata — `gapsfiller:fillnodata`

Реализован в [`gaps_filler_algorithm.py`](gaps_filler_algorithm.py:1). Заполняет NoData по правилу обратного расстояния от ближайших валидных соседей; применяется ко всем полосам входа. Используется для пост-обработки готового растра (мозаики, DEM) вне сквозного конвейера.

- **Inputs:** `INPUT` (растр), опц. `MASK_LAYER` (маска валидности).
- **Outputs:** `OUTPUT` (многоканальный GeoTIFF). При `FILL_ONLY_INTERIOR=True` (по умолчанию) дополнительно пишется 3-state маска `<output>.fillmask.tif` (`0` = original, `1` = filled, `2` = outside).
- **Ключевые опции:** `GAP_FILL_METHOD` — `v3_gdal_fillnodata` (по умолчанию, native C, fallback на v2), `v2_idw_quadrants` (pure-Python); `DISTANCE` (макс. радиус поиска), `ITERATIONS` (сглаживание); `FILL_ONLY_INTERIOR`, `MAX_INTERIOR_GAP_PX` (мост через узкие щели); advanced — `TILE_SIZE` (windowed mode для v2), `N_WORKERS` (параллельные полосы для v2).

### 6. Hyperspectral pipeline (filter, mosaic, fill) — `gapsfiller:hyperspectral_pipeline`

Реализован в [`hyperspectral_algorithm.py`](hyperspectral_algorithm.py:1). Сквозной конвейер: фильтр → мозаика → fill. Принимает пачку кадров и выдаёт готовую мозаику с заполненными пропусками.

- **Inputs:** `INPUT_LAYERS`.
- **Outputs:** `OUTPUT` (заполненная мозаика); сопутствующие сайд-выходы соответствующих стадий — `<output>.rejected.csv` (audit стадии A), `<output>.fillmask.tif`, `<output>.overlap_count.tif`, `<output>.valid_coverage.tif`, опц. `<output>.sources.tif`. Промежуточная мозаика `<output>.mosaic.tif` удаляется в конце нормального запуска.
- **Ключевые опции:** объединение опций стадий A / B / C (выбор метода для каждой стадии, threshold preset, reproject, fill-only-interior, max-distance / smoothing-iterations и т.д.); `DRY_RUN` — выполнить только стадию A для подбора порогов без записи растра.

---

## Конвенции выходных данных

### Side outputs

Стадии плагина пишут вспомогательные файлы рядом с основным выходом. Все они являются опциональными потребителями друг друга — отсутствие сайдкара не нарушает работу следующей стадии.

| Файл | Кто пишет | Кто читает | Назначение |
|---|---|---|---|
| `<output>.mosaic.tif` | [`pipeline.py`](src/pipeline.py:1) | сам конвейер | Промежуточная Stage B мозаика; **удаляется** в конце нормального запуска. |
| `<output>.fillmask.tif` | [`fill_nodata.write_interior_fill_mask()`](src/fill_nodata.py:69) | *Mosaic quality* (`*_filled_only` метрики), сам fill (через `mask_path`) | uint8, 3 значения: `0` = original, `1` = filled, `2` = outside. |
| `<output>.sources.tif` | мозаика `v2_best_pixel` | [`mosaic_quality.analyze_sources()`](src/mosaic_quality.py:1), seam-consistency | uint16 provenance: `0` = nodata, `1..N` = 1-based индекс кадра-победителя. |
| `<output>.overlap_count.tif` | все три метода мозаики (если `EMIT_COVERAGE_OUTPUTS=True`) | *Mosaic quality* (coverage_ratio и т.д.) | uint16: сколько кадров покрывает каждый пиксель. |
| `<output>.valid_coverage.tif` | те же, что и выше | те же, что и выше | uint8, 0/1: пиксель покрыт хотя бы одним кадром. |
| `<output>.rejected.csv` | [`pipeline.py`](src/pipeline.py:1), стадия A | пользователь / Excel / Pandas | Audit стадии A: `path, reason, measured_value, threshold`. **Это deliverable**, не временный файл. |
| `<output>.footprint.geojson` | стадия raw-привязки ([`airborne_georef.py`](src/airborne_georef.py:1)) | QGIS / GIS | Полигон следа полётной линии в CRS итогового GeoTIFF. |

### Spectral-fidelity policy

Корректность пиксельных значений приоритетнее визуального качества мозаики. Видимые швы в перекрытиях принимаются как цена сохранения исходных спектров.

- Все зарегистрированные методы мозаики (`v1_first_write_wins`, `v2_best_pixel`, `v3_vrt`) спектрально верны: каждый выходной пиксель берётся из ровно одного входного кадра. Смешивание (feathering) и выравнивание гистограмм по умолчанию отсутствуют.
- Любой будущий метод, смешивающий, интерполирующий или нормализующий значения между кадрами, может существовать исключительно как явно помеченный экспериментальный экстра — он не может занимать индекс 0 в `MOSAIC_METHODS` и не может быть путём по умолчанию.
- Стадия Fill nodata формирует новые значения там, где их не было (это её прямая функция), но не модифицирует оригинальные пиксели. 3-state `fillmask` различает «original / filled / outside» и позволяет в *Mosaic quality* считать метрики по заполненной области отдельно.

---

## Установка и зависимости

Установка выполняется штатно: *Plugins → Manage and Install Plugins…* → выбор плагина из списка или установка из ZIP. После регистрации шесть алгоритмов появляются в *Processing Toolbox*.

### Обязательные зависимости

- `numpy`
- `rasterio` ≥ 1.3 (требуются kwargs `dtype=` / `nodata=` у `rasterio.merge.merge`)
- GDAL — поставляется вместе с QGIS, отдельная установка не требуется.
- Python 3.x (используется QGIS).

### Опциональные зависимости

Импортируются лениво — плагин загружается без них; ошибка возникает только при вызове соответствующей функции.

| Пакет | Когда нужен | Установка |
|---|---|---|
| `scipy` | Морфологическое закрытие пропусков (`MAX_INTERIOR_GAP_PX`), сегментация дыр, веса по расстоянию для мозаики v2. Есть pure-numpy fallback там, где возможно. | `pip install scipy` |
| `scikit-image` | Метрика SSIM в *Mosaic quality*. | `pip install scikit-image` |
| `pymap3d` | Преобразования ENU↔geodetic в стадии raw-привязки гиперспектральной камеры ([`airborne_georef.py`](src/airborne_georef.py:1)). | `pip install pymap3d` |
| `pyproj` | Перепроецирование DEM в DEM-aware raw-привязке. QGIS обычно поставляет `pyproj` сам — отдельная установка часто не требуется. | `pip install pyproj` |
| `spectral` | Чтение ENVI `.hdr` сайдкаров гиперспектральной камеры ([`envi_io.py`](src/envi_io.py:1)). | `pip install spectral` |

---

## Использование

Для отдельных стадий используются их standalone-алгоритмы; для штатной обработки пачки кадров — *Hyperspectral pipeline*. При подборе порогов отбраковки полезен флаг `DRY_RUN`: выполняется только Stage A, без записи мозаики.

### Расшифровка причин отбраковки в журнале

При запуске *Filter bad frames* (или сквозного конвейера) на каждый отбракованный кадр в журнал пишется строка вида `REJECTED <имя файла>: <причина>`. Все причины формирует [`frame_filter.py`](src/frame_filter.py:1); v2 / v3 используют ту же `"measured vs threshold"` конвенцию, поэтому таблица ниже применима и к ним (v3 дополнительно указывает индекс полосы).

| Строка в логе | Что это значит | Какой порог трогать |
|---|---|---|
| `skewed transform (skew=X > Y)` | Аффинное преобразование кадра имеет слишком большой перекос/поворот. | Увеличить `SKEW_MAX`. |
| `abnormal area (area=X, allowed=[lo, hi])` | Площадь следа кадра сильно отличается от медианы по полёту. | Расширить интервал: уменьшить `AREA_LO` и/или увеличить `AREA_HI`. |
| `abnormal aspect ratio (ar=X > Y)` | Кадр слишком вытянут (длинная сторона / короткая больше порога). | Увеличить `ASPECT_MAX`. |
| `mostly nodata in centre (valid=X < Y)` | В центральном окне доля валидных пикселей ниже порога. | Уменьшить `MIN_VALID_FRACTION` или увеличить `CENTRE_WINDOW`. |
| `low variance centre (std=X < Y)` | Центр кадра слишком однороден (низкое СКО). | Уменьшить `STD_MIN`. |
| `saturated centre (sat=X > Y)` | В центре кадра слишком много пикселей, равных максимуму типа данных (пересвет). | Увеличить `SATURATION_FRACTION`. |
| `band B dropout (frac=X, allowed=[0, Y])` | v3: в полосе B слишком много NoData / нулей / насыщенных. | Увеличить `MAX_DROPOUT_FRAC`. |
| `band B striping (ratio=X > Y)` | v3: в полосе B доминирует столбцовая полосатость. | Увеличить `MAX_STRIPE_RATIO`. |
| `area outside MAD (area=X, allowed=[lo, hi])` | v2: площадь следа выпала за `K_MAD * MAD` от медианы. | Увеличить `K_MAD` или сменить метод на v1. |

### Типичный сценарий: «у меня все кадры отбраковались»

1. Запустить *Filter bad frames* (или *Hyperspectral pipeline* с `DRY_RUN=True`) с дефолтами.
2. Открыть *Log* в окне Processing — там окажутся строки `REJECTED <файл>: <причина>`.
3. При наличии отчёта `REPORT` или запуска конвейера открыть `<output>.rejected.csv`: список всех отбракованных с измеренным значением и порогом.
4. Определить **доминирующую** причину (например, у 90% кадров одна и та же фраза).
5. По таблице выше найти порог, который её контролирует, и аккуратно ослабить его (например, `MIN_VALID_FRACTION` с `0.5` до `0.3`), либо переключить `THRESHOLD_PRESET` на `Permissive`.
6. Повторно запустить алгоритм. Итерировать до приемлемого процента «выживших»; не ослаблять одновременно все пороги.

---

## Ограничения

- Стадия мозаицирования требует одинаковой CRS и pixel size у всех входов; для автоматического перепроецирования к первому входу используется `REPROJECT_TO_FIRST=True` (билинейная интерполяция).
- Перекрытия в мозаике разрешаются правилом соответствующего метода (`v1` — первый записал; `v2` — пиксель из кадра, наиболее удалённого от своего края; `v3` — приоритет наивысшего разрешения через `gdalbuildvrt`). Спектральное смешивание отсутствует во всех методах (см. [Spectral-fidelity policy](#spectral-fidelity-policy)).
- Эвристика отбраковки настроена на кадры гиперспектральной камеры; для других сенсоров пороги, как правило, необходимо подбирать заново.
- Автоматических тестов в проекте нет (по решению поддерживающих).

---

## Автор и лицензия

- Автор: **Duke** (`st087204@student.spbu.ru`).
- Версия: **0.2**.
- Минимальная версия QGIS: **3.0** (de facto рекомендуется ≥ 3.14 из-за современного Processing API).
- Источник: [`metadata.txt`](metadata.txt:1). Файл лицензии в репозитории отсутствует.
