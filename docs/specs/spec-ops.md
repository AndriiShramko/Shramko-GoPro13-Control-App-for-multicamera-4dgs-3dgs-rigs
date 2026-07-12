---
type: resource
tags: [gopro, genlock, ops, windows, git, safety]
created: 2026-07-12
status: active
---

# gopro-genlock — spec-ops: среда, git, safety (машина `andri`)

Роутер: [[spec]]. Факты сняты живыми командами 2026-07-12.

## Машина (проверено)

- Python **3.11.9** (`python`/`py`) — идеален для open-gopro (≥3.11 <3.14). git 2.49.0. winget есть. `C:\dev\` существует.
- **⚠️ ЛОВУШКА ffmpeg**: ffmpeg НЕ установлен, но в `C:\Windows\System32\` лежит ПУСТОЙ файл `ffmpeg` (0 байт, без расширения). Git Bash молча «выполняет» его с exit 0 = тихий ложный успех. Правила (red-team #14): (1) static build (gyan.dev) → `C:\dev\gopro-sync\bin\ffmpeg.exe`/`ffprobe.exe`, путь в конфиге проекта; (2) вызывать ТОЛЬКО по абсолютному пути; (3) smoke-тест: `-version` возвращает строку версии И декод тестового клипа — «exit 0 без вывода» = провал; (4) удаление пустышки = admin/System32 → только оффер оператору.

## Рабочая среда

- Код: **`C:\dev\gopro-sync\`** = клон репо `Shramko-GoPro13-Control-App-for-multicamera-4dgs-3dgs-rigs`. НЕ `C:\Users\andri\projects\` (ломает сборки — урок машины). Сразу `git config core.longpaths true`.
- venv: `py -3.11 -m venv .venv`. Зависимости (пиновать `pip freeze`): open-gopro, requests, numpy, scipy, pandas, matplotlib, opencv-python, pygame, sounddevice. БЕЗ ffmpeg-python (subprocess + `ffprobe.exe -print_format json`).
- Кодировки: `PYTHONIOENCODING=utf-8` при запуске; .ps1 — ASCII/UTF-8-BOM (уроки машины).
- USB-канал: камера в режиме **GoPro Connect** (не MTP — если в проводнике диск, управления нет); `ipconfig` → адаптер `172.2*`, IP камеры = gateway `.51`; `ping` → `curl.exe http://<ip>:8080/gopro/camera/info`. Драйвер NCM при нужде — ставить можно, с логом и уведомлением оператора. Только data-кабель, порт в мать (не хаб).
- Тест-паттерны: **python+pygame fullscreen** (счётчик = perf_counter_ns + номер кадра + движущийся бар; вспышки по расписанию; лог каждого flip → CSV). Не-сон экрана: `SetThreadExecutionState(ES_DISPLAY_REQUIRED)` внутри процесса (НЕ powercfg). Focus Assist = Alarms only на сессию (вернуть). Браузер/rAF — только для прикидок, не для измерений (троттлинг).

## Git-дисциплина

- Ветка **`research/usb-sync-experiments`** сразу после клона. Перед каждым commit/push: `git branch --show-current` ≠ main.
- Hook `.git/hooks/pre-push` отклоняет push в main. **Hooks НЕ клонируются с репо** (red-team #23) — поставить ЛОКАЛЬНО в свежем клоне и проверить срабатывание тестовым dry-push; копия-эталон в `tools/hooks/`. Push ветки = бэкап, можно и нужно. Merge/PR в main — только оператор.
- `.gitignore` первым коммитом: `captures/`, `*.mp4 *.MP4 *.LRV *.THM *.wav *.bin`, `.venv/`, `__pycache__/`. `.gitattributes`: `*.py *.csv text eol=lf`. LFS не нужен.
- Коммитить: код, requirements, docs/, малые CSV (<1 МБ), PNG-графики в `docs/plots/` (vault text-only — картинки в репо).

## Данные

- Видео: `captures\<YYYYMMDD_HHMMSS>_<test-id>\` (вне git) + `meta.json` (снимок `/state`, серийник, fw, описание). test-id slug: `counter-60p`, `flash-240fps`, `drift-30min`…
- CSV → `docs/data/`, графики → `docs/plots/`, в vault — только числа/выводы/таблицы + ссылки на коммит.
- Чистка captures: не автоматически; после извлечения CSV пометить `_processed`.

## Safety

- **SD-карта**: разрешено чистить, НО: (1) перед первой очисткой — полный `media/list` (с размерами/датами) → `docs/session-logs/sd-inventory-<дата>.json` + сводка в чат; файлы старше сессии → пауза, вопрос оператору; (2) удалять файл ТОЛЬКО после download+checksum, если он решён к сохранению (red-team #24); чистка = поштучный HTTP-delete (не формат).
- **Диск/время** (red-team #16-17): чек свободного места перед каждым тестом; конвейер download→checksum→delete на SD; битрейт/разрешение снижать там, где не влияет на измерение; длинные записи чаптерятся (~4 ГБ) — загрузчик склеивает главы и фильтрует LRV/THM.
- **Долгие тесты Windows** (red-team #20): перед ними чек-лист — active hours/Windows Update, автолок off на сессию, оверлеи; в пост-анализе контроль «счётчик виден во всех кадрах».
- **Прошивки — жёсткий гейт**: ничего не флешить (ни сток-апдейт, ни GoPro Labs) без явного «да» оператора в чате. Labs очень нужна для синка (Precision Time QR) — изложить оператору кейс и ждать решения. Установка — операторская операция.
- Снимок исходных настроек (`/state`) до изменений; в конце — оффер отката. Auto Power Down → Never на сессию (вернуть).
- **Тепло** (HERO13 греется, USB-питание греет): перед каждым дублем читать флаг System Hot (id 6) + busy/encoding (8/10); Hot → пауза ≥5 мин. Тест-клип ≤60 с (кроме дрейф-теста), пауза 60–90 с между дублями; для длинных прогонов GPS off, 1080p где хватает. Отвал HTTP посреди записи → ждать 30 с, re-ping, `last_captured`; кабель руками — оператор.
- Не трогать: другие USB-устройства, powercfg/реестр/службы/брандмауэр. Белый список: winget install, Focus Assist, SetThreadExecutionState.

## Профили настроек камеры для тестов

- **A «быстрый»**: 1080p60, стандартный объектив, 10-bit off, GPS off, HyperSmooth OFF — дешёвые итерации.
- **B «временно́е разрешение»**: 2.7K240, HyperSmooth OFF, выдержка 1/480+ — субкадровые измерения (полосы вспышки).

## Чек-лист инициализации Фазы 0 (пройдена = все шаги зелёные за один прогон)

подключение (GoPro Connect) → ipconfig/IP → ping → wired_usb?p=1 → `/state` (снимок) → `/info` (модель/fw/serial) → профили A/B применяются и читаются обратно → keep-alive 60 c стабильно → запись 5 c (encoding поднялся/опустился) → media/list (файл появился) → скачивание (размер сходится) → ffprobe.exe (длительность/fps/дорожки gpmd+tmcd) → GPMF-извлечение (FourCC DEVC) → meta.json+лог+commit+push.

## Связанные
[[spec]] · [[spec-api]] · [[spec-experiments]]
