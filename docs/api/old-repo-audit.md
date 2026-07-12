# Аудит старого кода (корень репо) — для Фазы 0 «точная синхронизация»

Дата: 2026-07-12. Ветка `research/usb-sync-experiments`, корень = legacy-код GoPro Control App (HERO10-13, USB, Windows).
Новый код фазы уже живёт в `src/` (`wired_gopro.py`, `gpcli.py`) — аудит его не трогает.

## 1. Таблица файлов

| Файл | Назначение | Вердикт | Почему |
|---|---|---|---|
| `goprolist_and_start_usb.py` | mDNS-discovery (`_gopro-web._tcp.local.`), кэш, вкл. wired USB control | АДАПТИРОВАТЬ | Рабочие паттерны (3 попытки discovery, reset USB off→2s→on), но zeroconf медленный; `src/` уже ищет по ipconfig-gateway |
| `date_time_sync.py` | Пуш времени на все камеры одновременно (Barrier) | АДАПТИРОВАТЬ | Barrier-паттерн + endpoint верные; разрешение `set_date_time` = 1 с — для ±1 кадра мало |
| `recording.py` | Синхронный старт записи через Barrier | АДАПТИРОВАТЬ | Ядро для Фазы 0: barrier + `shutter/start` + лог start/end time c 6 знаками |
| `stop_record.py` | Синхронный стоп с ретраями | АДАПТИРОВАТЬ | Ценная обработка 503 (камера занята) — 3 попытки с паузой 1 с |
| `sync_test.py` | A/B-тест ThreadPool vs Barrier для старта | АДАПТИРОВАТЬ | Прямой предок наших измерений джиттера; методику взять, метрик нет |
| `start_usb.py` | Ручной reset/enable/verify USB одной камеры | АДАПТИРОВАТЬ | Эскалация таймаутов 0.5/1/2 с; знание «500 = уже в этом состоянии»; verify по status id 33 сомнителен (это SD status) |
| `super_sync_start_recording.py` | Ранний прототип discovery+USB+запись | УСТАРЕЛО | Дубликат; `sync_time_on_cameras()` — пустая заглушка |
| `goprolist_usb_activate_time_sync.py` | Discovery + USB + время | УСТАРЕЛО | Сломан: импортирует несуществующий `date_time_sync.sync_time` |
| `goprolist_usb_activate_time_sync_record.py` | Полный цикл: discovery→USB→время→запись | УСТАРЕЛО | Третья копия discovery-кода; порядок шагов полезен как документация |
| `goprolist_and_start_usb_sync_all_settings_date_time.py` (+` copy`) | Оркестратор: запускает скрипты через subprocess | УСТАРЕЛО | Subprocess-склейка; в CLI заменяется прямыми вызовами |
| `sync_and_record.py` | Обёртка «время+запись» | УСТАРЕЛО | Тонкая склейка, импортирует из recording.py то, чего там нет (`set_video_mode`) |
| `copy_to_pc.py` | Скачивание всех файлов со всех камер | АДАПТИРОВАТЬ | Минимальный рабочий образец `media/list` + `/videos/DCIM/` |
| `copy_to_pc_and_scene_sorting.py` | Копирование + группировка в «сцены» по времени (порог 5 с) + верификация размера | АДАПТИРОВАТЬ | Сцены и `copy_file_with_verification` пригодятся для сбора клипов со стенда |
| `copy_manager.py`, `copy_progress_widget.py`, `progress_dialog.py`, `file_manager.py`, `file_statistics.py`, `copy_progress.json` | GUI-копирование (PyQt QThread) | НЕ ОТНОСИТСЯ | Привязано к PyQt5; новая фаза — CLI |
| `utils.py` | Пути (frozen/dev), логирование в файл+консоль | ПЕРЕИСПОЛЬЗОВАТЬ | Нейтральный код, работает как есть |
| `camera_cache.json` | Кэш 4 реальных камер | ПЕРЕИСПОЛЬЗОВАТЬ | Факт-справка: серийники и схема IP `172.2X.YYY.51` (последние 3 цифры SN) |
| `api doc.txt` | Дамп Open GoPro HTTP API v2.0 (включая HERO13-колонки) | ПЕРЕИСПОЛЬЗОВАТЬ | Оффлайн-справочник эндпоинтов/настроек прямо в репо |
| `vocabulary_camera_settings.py` | Словарь 173 setting id → имя → опции | ПЕРЕИСПОЛЬЗОВАТЬ | Готовый справочник для логов/CLI |
| `read_and_write_all_settings_from_prime_to_other.py` | Копия настроек prime→остальные, проверка модели | АДАПТИРОВАТЬ | Рабочая логика prime-камеры (`/gp/gpControl/info`) и совместимости настроек |
| `read_and_write_all_settings_from_prime_to_other_v02.py` | «Улучшенная» v02 | УСТАРЕЛО | Содержит вымышленные эндпоинты (`setting/checkpoint`, `/validate`, `/health`, `/conflicts`) — галлюцинации ChatGPT; взять только `USB_HEADERS` и `wait_for_camera_ready` |
| `camera_settings_manager.py` | Пакетное применение настроек, лимит 50 камер | АДАПТИРОВАТЬ | Паттерн ограничения параллелизма |
| `camera_presets.py`, `set_preset_0.py` | Пресеты: снятие state и `presets/load?id=0` | АДАПТИРОВАТЬ | Загрузка пресета перед записью нужна и в Фазе 0 |
| `preset_manager_gui.py`, `camera_presets_gui.py`, `camera_templates/` | GUI пресетов | НЕ ОТНОСИТСЯ | PyQt |
| `describe_all_camera_settings.py`, `how_to_now_avalabele_value_on_settings.py` | Интроспекция допустимых значений (`option=999999` → 403 со списком) | АДАПТИРОВАТЬ | Трюк с 403 — легальный способ узнать поддерживаемые опции HERO13 |
| `Turn_Off_Cameras.py` | Сон камер `/gp/gpControl/command/system/sleep` | АДАПТИРОВАТЬ | Работает, но URL без `:8080` — проверить на HERO13 |
| `sleep.py` | Эксперимент: auto-sleep (setting 59) + `keep_alive` | АДАПТИРОВАТЬ | Единственное место в старом коде со знанием о keep_alive |
| `power_management.py` | Запрет сна Windows (SetThreadExecutionState) | ПЕРЕИСПОЛЬЗОВАТЬ | Обязательно для многочасовых прогонов стенда |
| `format_sd.py` | Форматирование SD `/gp/gpControl/command/storage/delete/all` | АДАПТИРОВАТЬ | Нужно для серий экспериментов; тоже без `:8080` |
| `status_of_cameras_GUI.py` | Дашборд статусов (state + `/status/storage`) | НЕ ОТНОСИТСЯ | GUI; но endpoint storage и опрос encoding-статуса — подсмотреть |
| `mode_switcher.py`, `photo_mode*.py`, `video_mode.py`, `timelapse_mode.py`, `take_single_photo.py`, `single_photo_timelapse_gui.py`, `*_settings.py` (video/photo/timelapse) | Режимы фото/таймлапс и их GUI | НЕ ОТНОСИТСЯ | Фаза 0 — только видео-запись |
| `set_video_mode.py` | Установка видеорежима (setting 128) | УСТАРЕЛО | Legacy setting id; на HERO13 режим ставится пресетами |
| `camera_orientation_lock.py` | Блокировка ориентации | НЕ ОТНОСИТСЯ | Не влияет на синхронизацию |
| `prime_camera_sn.py` | Хардкод серийника prime-камеры | УСТАРЕЛО | Конфиг-в-коде; заменить параметром CLI |
| `test_camera_model.py` | Модель/прошивка через `/gp/gpControl/info` | АДАПТИРОВАТЬ | Готовый паттерн идентификации HERO13 и firmware для протокола эксперимента |
| `test_raw_on_off.py`, `tmp_*.py` (11 шт.) | Одноразовые эксперименты с настройками/файлами | УСТАРЕЛО | Черновики; ничего уникального сверх основных файлов |
| GUI/сборка: `Gopro_Gui_Interface.py`, `Gopro_Gui_interfase_Pyqt5.py`, `main.py`, `app_config.py`, `app_init.py`, `build*.py/.bat`, `*.spec`, `setup.py`, `ico/`, `icon.ico` | Приложение и PyInstaller-сборка | НЕ ОТНОСИТСЯ | Новая фаза — CLI без GUI/exe |
| JSON/логи-дампы (`2_describe_*.json`, `all_avalable_gopro10_value_settings.json`, `camera_settings.json`, `current_camera_settings_*.json`, `camera_files.json`, `*.log`) | Снимки настроек HERO10 и логи сессий | НЕ ОТНОСИТСЯ | Устаревшие дампы (HERO10); справочник по HERO13 брать из `api doc.txt` |
| `status_of_cameras.py` | Пустой файл | УСТАРЕЛО | 0 байт |
| `instructions.md`, `copy_system_instructions.md`, `.cursorrules*` | Доки/промпты старого проекта | НЕ ОТНОСИТСЯ | README сам предупреждает: доки писал ChatGPT без проверки |

## 2. Что проект уже умел

- **Discovery по USB**: zeroconf `ServiceBrowser(zeroconf, "_gopro-web._tcp.local.", listener)` → IP камеры; `discover_gopro_devices()` (goprolist_and_start_usb.py), 3 попытки по 15 с, дедуп по IP, проверка живости `GET /gopro/camera/state`.
- **Включение wired-режима**: `toggle_usb_control(ip, enable)` → `GET /gopro/camera/control/wired_usb?p={0|1}`; `reset_and_enable_usb_control()` — всегда off → 2 c → on.
- **Пуш времени**: `sync_time_on_cameras()` (date_time_sync.py) → `GET /gopro/camera/set_date_time?date=YYYY_MM_DD&time=HH_MM_SS&tzone=&dst=` под `threading.Barrier`, замер send/response time.
- **Синхронный старт/стоп**: `start_recording_synchronized()` / `stop_recording_synchronized()` → `GET /gopro/camera/shutter/start|stop`, Barrier + поток на камеру.
- **Копирование**: `GET /gopro/media/list` → `GET /videos/DCIM/{dir}/{file}` stream, chunk 8192, сверка размера (`copy_file_with_verification`), сортировка в сцены по разнице create-time ≤5 с.
- **Пресеты/настройки**: `GET /gopro/camera/presets/load?id=0`; чтение `GET /gopro/camera/state` и `/gp/gpControl/status`; запись `GET /gp/gpControl/setting/{id}/{value}` и `/gopro/camera/setting?setting=&option=`; модель — `GET /gp/gpControl/info` (`model_name`, `firmware_version`).
- **Прочее**: сон `/gp/gpControl/command/system/sleep`, keep-alive `/gopro/camera/keep_alive`, формат SD `/gp/gpControl/command/storage/delete/all`, storage `/gp/gpControl/status/storage`, запрет сна Windows (power_management.py).

## 3. Известные грабли из кода/README

- **Порядок команд жёсткий**: discovery → wired_usb off→on (2 c пауза) → time sync → 1 c пауза → shutter/start. Пропуск reset-цикла USB давал зависшие камеры.
- **503 на shutter/stop** — камера занята дозаписью; лечится 3 ретраями с паузой 1 с (stop_record.py).
- **500 на wired_usb** = «уже в этом состоянии», это успех, не ошибка (start_usb.py).
- **Таймауты**: state-check 2-5 с; для время-критичных команд — 1 с; start_usb использует эскалацию 0.5/1/2 с. После enable USB нужно ~0.5-1 c стабилизации.
- **zeroconf-discovery ненадёжен**: до 3×15 с ожидания, дубликаты сервисов, кэш как fallback; отсюда `camera_cache.json` в трёх местах (data/, корень, backup).
- **README честно предупреждает**: доки и часть кода писал ChatGPT; v02-файл содержит несуществующие эндпоинты (`/gp/gpControl/setting/checkpoint|validate|health|conflicts`) — не копировать вслепую.
- **`set_date_time` — разрешение 1 секунда**, а пуш идёт по HTTP без компенсации RTT: одинаковое время ≠ синхронные часы.
- **Barrier синхронизирует потоки хоста, а не камеры**: HTTP-запросы уходят одновременно, но исполнение шаттера камерой не измерялось (в логах только host-side start/end).
- **HERO13-специфика**: код писался под HERO10, HERO13 «поддерживается» декларативно; `api doc.txt` уже различает HERO13 (напр. Anti-Flicker 134: NTSC/PAL вместо 60/50 Hz). USB IP = `172.2X.YYZ.51`.
- **Мелочи**: часть URL без `:8080` (Turn_Off_Cameras, format_sd); status id 33 использован как «USB status», хотя это SD-статус; лямбды в ThreadPoolExecutor глотают исключения.

## 4. Что взять в новый CLI Фазы 0

1. `reset_and_enable_usb_control()` — последовательность off→sleep(2)→on + трактовка кода 500.
2. Barrier-паттерн одновременной отправки `shutter/start` c логом `time.time()` до/после (recording.py) — заменив на `perf_counter_ns` (уже в `src/wired_gopro.py`).
3. Ретрай-политику 503/timeout из stop_record.py (3 попытки, пауза 1 с).
4. Методику A/B из sync_test.py (серии стартов ThreadPool vs Barrier) как каркас эксперимента по джиттеру.
5. `media/list` + скачивание с проверкой размера (copy_to_pc*.py) — для автозабора клипов со стенда.
6. `vocabulary_camera_settings.py` + трюк `option=999999 → 403` для инвентаризации HERO13.
7. `power_management.py` (запрет сна Windows) и keep_alive из sleep.py — для длительных сессий.
8. `camera_cache.json`-схему `{name: serial, ip}` и идентификацию камеры через `/gp/gpControl/info`.

## 5. Чего в старом коде НЕТ (наши новые задачи)

- **Измерение реального рассинхрона**: нигде не измеряется момент старта записи самой камерой — только время HTTP-вызова на хосте. Нет ни джиттера, ни дрейфа, ни статистики по сериям.
- **GPMF**: телеметрия/метки времени из MP4 (GPMF-парсинг, SOS/timecode) не используются вообще; ffmpeg есть только как zip в `bin/`.
- **Субкадровые методы**: нет ни LED/вспышки/клапперов, ни аудио-корреляции, ни анализа кадров — стенд для ground-truth придётся строить с нуля.
- **Компенсация RTT/offset**: время пушится «как есть», без оценки задержки канала на камеру (аналога NTP/PTP-логики нет).
- **Стабильный host-timebase**: старый код пишет `time.time()`; `perf_counter_ns` и структурированные JSONL-логи вызовов появились только в новом `src/wired_gopro.py`.
- **Эксперимент как артефакт**: нет протоколов, повторяемых сценариев, CSV/графиков — только разовые логи.
