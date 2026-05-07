# Hyperspectral gaps filler

Плагин QGIS 3 для подготовки ортофотопланов из гиперспектральных снимков с дрона **PIKA-L** и заполнения пропусков (NoData) в растрах. Работает как Processing-провайдер: все алгоритмы появляются в *Processing Toolbox → Hyperspectral gaps filler → Raster analysis*.

Имя плагина: **Hyperspectral gaps filler**, версия **0.2** (см. [`metadata.txt`](metadata.txt:1)).

---

## Установка

*Plugins → Manage and Install Plugins…* → выбрать плагин из списка либо установить из ZIP. После установки шесть алгоритмов появятся в *Processing Toolbox*.

### Зависимости (обязательные)

- `numpy`
- `rasterio` ≥ 1.3 (нужны kwargs `dtype=` / `nodata=` у `rasterio.merge.merge`)
- GDAL — поставляется вместе с QGIS, отдельно ставить не нужно.
- Python 3.x (используется QGIS).

### Опциональные зависимости

Все импортируются «лениво» — плагин загружается без них, ошибка появится только при вызове соответствующей функции.

| Пакет | Когда нужен | Установка |
|---|---|---|
| `scipy` | Морфологическое закрытие пропусков (`MAX_INTERIOR_GAP_PX`), сегментация дыр, веса по расстоянию для мозаики v2. Есть pure-numpy fallback там, где возможно. | `pip install scipy` |
| `scikit-image` | Метрика SSIM в *Mosaic quality*. | `pip install scikit-image` |
| `pymap3d` | Преобразования ENU↔geodetic в стадии raw-привязки PIKA-L ([`airborne_georef.py`](airborne_georef.py:1)). | `pip install pymap3d` |
| `pyproj` | Перепроецирование DEM в DEM-aware raw-привязке. QGIS обычно поставляет `pyproj` сам — отдельной установки часто не требуется. | `pip install pyproj` |
| `spectral` | Чтение ENVI `.hdr` сайдкаров PIKA-L ([`envi_io.py`](envi_io.py:1)). | `pip install spectral` |

---

## Алгоритмы Processing Toolbox

Все шесть алгоритмов регистрируются в [`gaps_filler_provider.py`](gaps_filler_provider.py:30) в группе **Raster analysis**. Используйте отдельные стадии для отладки или сборки в Model Builder; используйте сквозной конвейер для обычных запусков.

### 1. Filter bad frames — `gapsfiller:frame_filter`

Реализован в [`frame_filter_algorithm.py`](frame_filter_algorithm.py:1). Прогоняет эвристику отбраковки PIKA-L по входным растрам и копирует «выживших» в указанную папку; в журнал и (опционально) в отчёт пишутся причины отбраковки. Используйте, когда нужно вручную просмотреть отбракованные перед мозаицированием.

- **Inputs:** `INPUT_LAYERS` (список растров).
- **Outputs:** `OUTPUT_FOLDER` (папка с принятыми кадрами), `REPORT` (опц. `.txt`).
- **Ключевые опции:** `FRAME_FILTER_METHOD` (`v1_hard_thresholds` по умолчанию, `v2_adaptive_mad`, `v3_per_band`); `THRESHOLD_PRESET` (Custom / Permissive / Default / Strict); восемь raw-порогов (`SKEW_MAX`, `AREA_LO`, `AREA_HI`, `ASPECT_MAX`, `CENTRE_WINDOW`, `MIN_VALID_FRACTION`, `STD_MIN`, `SATURATION_FRACTION`); v2-параметр `K_MAD`; v3-параметры `MAX_DROPOUT_FRAC`, `MAX_STRIPE_RATIO`. Все «специфичные для метода» порядки спрятаны под Advanced parameters.

### 2. Airborne georeference (raw PIKA-L) — `gapsfiller:airborne_georef`

Реализован в [`airborne_georef_algorithm.py`](airborne_georef_algorithm.py:1). Превращает сырой `.bil`-куб PIKA-L и сайдкары `.lcf` / `.times` в один георетированный GeoTIFF. Поддерживает плоскую землю (`GROUND_ALT`) и DEM-aware вариант (если задан DEM). Используйте перед фильтром, когда на входе сырые pushbroom-кадры без геопривязки.

- **Inputs:** `BIL` (raw cube), опционально `HDR` / `TIMES` / `LCF` (auto-discovered), `DEM` (опц.).
- **Outputs:** `OUTPUT` (GeoTIFF), `OUTPUT_FOOTPRINT` (опц. вектор `<output>.footprint.geojson` — см. ниже).
- **Ключевые опции:** `FOV_DEG` (обязательный — линзы PIKA-L различаются), `GROUND_ALT`, `DST_CRS` (по умолчанию EPSG:4326), `RESOLUTION`, `NODATA`; advanced — boresight `BORESIGHT_ROLL_DEG` / `BORESIGHT_PITCH_DEG` / `BORESIGHT_YAW_DEG` и `TIME_OFFSET_S` для калибровки.

### 3. Mosaic frames — `gapsfiller:mosaic_frames`

Реализован в [`mosaic_algorithm.py`](mosaic_algorithm.py:1). Склеивает уже отфильтрованные кадры в один многоканальный GeoTIFF. Все три метода **спектрально верны** — каждый выходной пиксель берётся из ровно одного исходного кадра, без смешивания. Видимые швы в перекрытиях — принятая цена сохранения исходных значений (см. «Spectral-fidelity policy» ниже).

- **Inputs:** `INPUT_LAYERS` (список растров с одинаковой CRS и pixel size, либо включите `REPROJECT_TO_FIRST`).
- **Outputs:** `OUTPUT` (GeoTIFF); по умолчанию также `<output>.overlap_count.tif` и `<output>.valid_coverage.tif`; для метода v2 — `<output>.sources.tif` (provenance).
- **Ключевые опции:** `MOSAIC_METHOD` — `v1_first_write_wins` (индекс 0, обратная совместимость), `v2_best_pixel` (рекомендуется, индекс 1 — пиксель из кадра с максимальным расстоянием до края), `v3_vrt` (через `gdalbuildvrt`); `REPROJECT_TO_FIRST` (опц. перепроецирование к CRS первого входа); `EMIT_COVERAGE_OUTPUTS` (по умолчанию ON).

### 4. Mosaic quality — `gapsfiller:mosaic_quality`

Реализован в [`mosaic_quality_algorithm.py`](mosaic_quality_algorithm.py:1). Сравнивает мозаику с эталонным растром: per-band RMSE / MAE / PSNR / SSIM, whole-cube SAM / SAM_DEG, агрегаты MEAN / WORST / P05 с указанием «худших» каналов. Опционально читает `<output>.fillmask.tif` и `<output>.sources.tif` для дополнительных метрик (filled-only, overlap-only, seam consistency, source contributions).

- **Inputs:** `REFERENCE`, `MOSAIC`, опц. `SOURCES_PATH` (для seam-метрик).
- **Outputs:** числовые поля `MEAN_<M>` / `WORST_<M>` / `P05_<M>` / `WORST_<M>_BAND` / `P05_<M>_BAND` для каждой из RMSE / MAE / PSNR / SSIM, плюс `SAM` и `SAM_DEG`; текстовый отчёт; опц. `OUTPUT_REPORT_JSON` — структурированный JSON со всеми метриками.

### 5. Fill nodata — `gapsfiller:fillnodata`

Реализован в [`gaps_filler_algorithm.py`](gaps_filler_algorithm.py:1). Заполняет NoData по правилу обратного расстояния от ближайших валидных соседей; работает по всем полосам входа. Используйте, чтобы «залатать» уже готовый растр (мозаику, DEM), не запуская фильтр/мозаику.

- **Inputs:** `INPUT` (растр), опц. `MASK_LAYER` (маска валидности).
- **Outputs:** `OUTPUT` (многоканальный GeoTIFF). Если `FILL_ONLY_INTERIOR=True` (по умолчанию), плагин строит и пишет 3-state маску `<output>.fillmask.tif` (`0` = original, `1` = filled, `2` = outside).
- **Ключевые опции:** `GAP_FILL_METHOD` — `v3_gdal_fillnodata` (по умолчанию, native C, fallback на v2), `v2_idw_quadrants` (pure-Python); `DISTANCE` (макс. радиус поиска), `ITERATIONS` (сглаживание); `FILL_ONLY_INTERIOR`, `MAX_INTERIOR_GAP_PX` (мост через узкие щели); advanced — `TILE_SIZE` (windowed mode для v2), `N_WORKERS` (параллельные полосы для v2).

### 6. Hyperspectral pipeline (filter, mosaic, fill) — `gapsfiller:hyperspectral_pipeline`

Реализован в [`hyperspectral_algorithm.py`](hyperspectral_algorithm.py:1). Сквозной конвейер: фильтр → мозаика → fill. Используйте, когда на входе пачка кадров и нужно одной кнопкой получить готовую мозаику с заполненными пропусками.

- **Inputs:** `INPUT_LAYERS`.
- **Outputs:** `OUTPUT` (заполненная мозаика); по пути появляются те же сайд-выходы, что и у соответствующих стадий — `<output>.rejected.csv` (audit стадии A), `<output>.fillmask.tif`, `<output>.overlap_count.tif`, `<output>.valid_coverage.tif`, опц. `<output>.sources.tif`. Промежуточная мозаика `<output>.mosaic.tif` удаляется в конце.
- **Ключевые опции:** объединение опций стадий A / B / C (выбор метода для каждой стадии, threshold preset, reproject, fill-only-interior, max-distance / smoothing-iterations и т.д.); `DRY_RUN` — выполнить только стадию A для подбора порогов без записи растра.

---

## Side outputs convention

Стадии плагина пишут несколько вспомогательных файлов рядом с основным выходом. Все они опциональные потребители друг друга — отсутствие сайдкара не ломает следующую стадию.

| Файл | Кто пишет | Кто читает | Назначение |
|---|---|---|---|
| `<output>.mosaic.tif` | [`pipeline.py`](pipeline.py:1) | сам конвейер | Промежуточная Stage B мозаика; **удаляется** в конце нормального запуска. |
| `<output>.fillmask.tif` | [`fill_nodata.write_interior_fill_mask()`](fill_nodata.py:69) | *Mosaic quality* (`*_filled_only` метрики), сам fill (через `mask_path`) | uint8, 3 значения: `0` = original, `1` = filled, `2` = outside. |
| `<output>.sources.tif` | мозаика `v2_best_pixel` | [`mosaic_quality.analyze_sources()`](mosaic_quality.py:1), seam-consistency | uint16 provenance: `0` = nodata, `1..N` = 1-based индекс кадра-победителя. |
| `<output>.overlap_count.tif` | все три метода мозаики (если `EMIT_COVERAGE_OUTPUTS=True`) | *Mosaic quality* (coverage_ratio и т.д.) | uint16: сколько кадров покрывает каждый пиксель. |
| `<output>.valid_coverage.tif` | те же, что и выше | те же, что и выше | uint8, 0/1: пиксель покрыт хотя бы одним кадром. |
| `<output>.rejected.csv` | [`pipeline.py`](pipeline.py:1), стадия A | пользователь / Excel / Pandas | Audit стадии A: `path, reason, measured_value, threshold`. **Это deliverable**, не временный файл. |
| `<output>.footprint.geojson` | стадия raw-привязки ([`airborne_georef.py`](airborne_georef.py:1)) | QGIS / GIS | Полигон следа полётной линии в CRS итогового GeoTIFF. |

---

## Spectral-fidelity policy

Корректность пиксельных значений важнее визуальной красоты. Видимые швы в перекрытиях — принятая цена сохранения исходных спектров.

- Все **зарегистрированные** методы мозаики (`v1_first_write_wins`, `v2_best_pixel`, `v3_vrt`) спектрально верны: каждый выходной пиксель берётся из ровно одного входного кадра. **Ни смешивания (feathering), ни выравнивания гистограмм по умолчанию нет.**
- Любой будущий метод, который смешивает / интерполирует / нормализует значения между кадрами, может существовать только как явно помеченный экспериментальный экстра — он не может стать индексом 0 в `MOSAIC_METHODS` и не может быть путём по умолчанию.
- Стадия Fill nodata строит **новые** значения там, где их не было (это её работа), но не трогает оригинальные пиксели. 3-state `fillmask` различает «original / filled / outside», чтобы вы могли в Mosaic quality считать метрики по filled-области отдельно.

---

## Расшифровка причин отбраковки в журнале

При запуске *Filter bad frames* (или сквозного конвейера) на каждый отбракованный кадр в журнал пишется строка вида `REJECTED <имя файла>: <причина>`. Все причины формирует [`frame_filter.py`](frame_filter.py:1); v2 / v3 используют ту же `"measured vs threshold"` конвенцию, поэтому таблица ниже подходит и для них (плюс v3 указывает индекс полосы).

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

---

## Типичный сценарий: «у меня все кадры отбраковались»

1. Запустить *Filter bad frames* (или *Hyperspectral pipeline* с `DRY_RUN=True`) **с дефолтами**.
2. Открыть *Log* в окне Processing — там будут строки `REJECTED <файл>: <причина>`.
3. Если задали отчёт `REPORT` или конвейер — открыть `<output>.rejected.csv`: список всех отбракованных с измеренным значением и порогом.
4. Найти **доминирующую** причину (например, у 90% кадров одна и та же фраза).
5. По таблице выше определить порог, который её контролирует, и **аккуратно ослабить** его (например, `MIN_VALID_FRACTION` с `0.5` до `0.3`), либо переключить `THRESHOLD_PRESET` на `Permissive`.
6. Запустить алгоритм повторно. Повторять, пока процент «выживших» не станет приемлемым — не ослабляйте сразу всё.

---

## Ограничения

- Стадия мозаицирования требует одинаковой CRS и pixel size у всех входов. Включите `REPROJECT_TO_FIRST=True` для автоматического перепроецирования к первому входу (билинейная интерполяция).
- Перекрытия в мозаике разрешаются по правилам соответствующего метода (`v1` — первый записал; `v2` — пиксель из кадра, дальше всего от своего края; `v3` — приоритет наивысшего разрешения через `gdalbuildvrt`). Спектрального смешивания нет ни в одном из методов — это сознательный выбор (см. «Spectral-fidelity policy»).
- Эвристика отбраковки рассчитана на кадры PIKA-L; для других сенсоров пороги, скорее всего, придётся подбирать заново.
- Автоматических тестов в проекте нет (по решению поддерживающих).

---

## Автор и лицензия

- Автор: **Duke** (`st087204@student.spbu.ru`).
- Версия: **0.2**.
- Минимальная версия QGIS: **3.0** (de facto рекомендуется ≥ 3.14 из-за современного Processing API).
- Источник: [`metadata.txt`](metadata.txt:1). Файл лицензии в репозитории отсутствует.
