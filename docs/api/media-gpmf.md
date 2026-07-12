# Media list, скачивание, GPMF-телеметрия (пост-выравнивание кадров)

## Media list

```
GET /gopro/media/list
```
Ответ: `{"id": "...", "media": [{"d": "100GOPRO", "fs": [ {...} ]}]}` — `d` = каталог, `fs` = файлы.
Поля файла: `n` (имя), `cre`/`mod` (unix-время создания/изменения), `s` (размер, байт), `t` (тип).
Групповые медиа (burst, timelapse) сворачиваются в один элемент: имя вида `GXXXYYYY.ZZZ`
(XXX = group ID, YYYY = member ID), ключи `b` (первый member), `l` (последний), `m` (удалённые), `g` (group id).
Видео с рига — одиночные файлы, групповая механика нам почти не нужна.

Прочее:
- `GET /gopro/media/last_captured` — полный путь последнего снятого файла (для групп — первый файл группы).
- `GET /gopro/media/info?path=100GOPRO/GX010001.MP4` — метаданные файла (размер, длительность, w/h и т.д.).

## Скачивание

```
GET http://<ip>:8080/videos/DCIM/{directory}/{filename}
```
Один endpoint для всего (фото/видео); каталог и имя **case-sensitive**. Поддерживает обычный HTTP GET —
качается стримингом (requests iter_content / curl). `GET /gopro/media/turbo_transfer?p=1` — ускорение
в основном для WiFi; влияние на USB — TODO-verify-on-camera.

## GPMF по HTTP

- `GET /gopro/media/gpmf?path=<file>` — извлечь GPMF-данные файла (без скачивания всего MP4).
- `GET /gopro/media/telemetry?path=<file>` — telemetry-трек файла.
- Разница выдач gpmf vs telemetry в доках не расписана — сравнить на камере (TODO-verify-on-camera).

## Формат GPMF (из README gpmf-parser)

- KLV (Key-Length-Value), FourCC-ключи, 32-битное выравнивание, big-endian.
- В MP4 живёт отдельным метадата-треком **«GoPro MET»**, sample description `gpmd`;
  тайминг/оффсеты — через стандартные атомы `stts`/`stsz`/`stco`.

### Стримы, важные для 4DGS-выравнивания

| FourCC | Что | Частота / гранулярность |
|---|---|---|
| `STMP` | микросекундные таймстемпы | **per-payload** (~1 Гц), НЕ per-frame |
| `CORI` | ориентация камеры (кватернион) | frame rate (HERO8+) |
| `IORI` | ориентация изображения (кватернион) | frame rate |
| `GRAV` | вектор гравитации | per-frame |
| `SHUT` | выдержка (exposure time) | per-frame (24–30 Гц по докам) |
| `GPS9` | lat, lon, alt, 2D/3D speed, days since 2000, **secs since midnight (ms precision)**, DOP, fix | 10 Гц (HERO11+, HERO13) |
| `ACCL` | акселерометр 3-оси | 200 Гц |
| `GYRO` | гироскоп | 200–3200 Гц (зависит от модели) |

### Семантика STMP (issue gpmf-parser#80, ответы dnewman-gpsw, GoPro)

- STMP = «the computed timestamp for the **first sample in each payload**».
- Точность отличная для видео-производных стримов (CORI, IORI, GRAV, SHUT, ISOE, HUES);
  у ACCL/GYRO — задержки ~10 мс («still needing work»).
- **«STMP was not intended for post synchronization»** — он уже использован для синхры внутри камеры;
  для per-frame стримов «you don't need to use STMP». В TLV/Timewarp режимах STMP отсутствует.
- Вывод для нас: прямого UTC-времени экспозиции кадра в GPMF НЕТ. Время кадра реконструируется:
  TC-трек MP4 (старт) + `stts` (длительности) + SHUT/CORI как per-frame сетка; STMP — только
  как вспомогательная привязка. Валидировать мигающим LED — см. spec-experiments (TODO-verify-experiment).

## Инструменты парсинга (проверено на живость 2026-07-12)

| Инструмент | Статус | Заметки |
|---|---|---|
| `gpmf-parser` (C, github.com/gopro/gpmf-parser) | жив, референсный | двойная лицензия Apache-2.0 / MIT |
| `gopro-overlay` (PyPI) | **жив**: 0.134.0 от 2026-05-16, Python >=3.11 | активный; телеметрия → GPX/CSV; продакшн-статус |
| `gpmf` (PyPI) | **мёртв**: 0.1 от 2020-07-12 | только GPS-фокус; не брать |
| `gopro-telemetry` (npm, JuanIrache) | не проверял сегодня — TODO-verify | популярный JS-парсер, включая STMP |

Рекомендация: свой тонкий парсер нужных FourCC поверх бинарного вывода `/gopro/media/gpmf`
(формат KLV тривиален), а gpmf-parser — как эталон для сверки.

## Источники (снято 2026-07-12)

- https://gopro.github.io/OpenGoPro/http/openapi.json (локальная копия `docs/openapi (1).json`) — endpoints и формат media list (пример JSON — из описания операции OGP_MEDIA_LIST).
- https://github.com/gopro/gpmf-parser — README: структура KLV, трек «GoPro MET», таблица стримов и частот, лицензия.
- https://github.com/gopro/gpmf-parser/issues/80 (+ комментарии через api.github.com) — семантика STMP, цитаты dnewman-gpsw.
- https://pypi.org/pypi/gpmf/json, https://pypi.org/project/gopro-overlay/ — живость Python-пакетов.
