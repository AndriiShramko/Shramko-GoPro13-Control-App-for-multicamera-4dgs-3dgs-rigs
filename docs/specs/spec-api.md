---
type: resource
tags: [gopro, genlock, 4dgs, api, open-gopro, labs]
created: 2026-07-12
status: active
---

# gopro-genlock — spec-api: API/SDK/Labs (факты с источниками)

Роутер: [[spec]]. Ресёрч 2026-07-12 (evidence-агент, все факты с URL). Это ядро того, что goal-агент структурирует в `docs/api/` репо.

## Open GoPro HTTP-over-USB (стоковая прошивка)

- Спека: **Open GoPro HTTP API 2.0**, едина для WiFi/USB — https://gopro.github.io/OpenGoPro/http/ + OpenAPI https://gopro.github.io/OpenGoPro/http/openapi.json (high)
- HERO13 поддержана (мин. FW v01.10.00) (high)
- **IP камеры по USB: `172.2X.1YZ.51:8080`**, X,Y,Z = последние 3 цифры серийника; камера = DHCP-сервер, хост получает `.54`. Надёжнее вычислять из ipconfig (gateway адаптера подсети `172.2*`), а не из серийника (high)
- Включение wired-управления: `GET /gopro/camera/control/wired_usb?p=1` (high)
- Keep-alive: `GET /gopro/camera/keep_alive` периодически (интервал ~3 c — community, low — уточнить экспериментом)
- Запись: `GET /gopro/camera/shutter/start|stop`; состояние `/gopro/camera/state`; инфо `/gopro/camera/info`; настройки `/gopro/camera/setting?setting=ID&option=ID`; медиа `/gopro/media/list`, `/gopro/media/last_captured`; скачивание `GET :8080/videos/DCIM/<dir>/<file>`; GPMF `/gopro/media/gpmf?path=...`
- По USB камеру НЕ включить программно (только BLE/кнопка/Labs USB Power Trigger) (high)
- **Python SDK `open-gopro`**: активен, Python ≥3.11 <3.14, класс `WiredGoPro`, tested Windows 10 (high). Запасной путь: тонкий requests-клиент (~100 строк) — держать всегда как инструмент диагностики
- Windows-драйверы: HERO13 = USB **NCM** («GoPro UsbNcm Host Device», VID 2672); известны проблемы подписи драйвера; BLE-паринг HERO13+Win11 сломан (med-high)

## HERO13 стоковый Timecode Sync

- QR из приложения **Quik** → TC-трек в MP4 (frame-accurate, читается NLE) (high)
- Точность <50 мс; дрейф заметен после 60–90 мин → пересинхронизация (med)
- Это синхронизация МЕТАДАННЫХ, не genlock (high). Требует телефон оператора → плохо автоматизируется

## GoPro Labs (HERO13: v2.10.70 от 2025-10-17 — доступна)

- https://gopro.github.io/labs/ — прошивка официальная, обратимая. **Установка = операторский гейт** (см. [[spec-ops]])
- **Precision Date & Time QR** (https://gopro.github.io/labs/control/precisiontime/): анимированный QR с мс-точностью; **±1 кадр**, пока камеры не выключались; после выключения ±1 с. QR можно показывать НА МОНИТОРЕ → агент ресинхронизирует камеры сам, без телефона (high)
- Command Language (https://gopro.github.io/labs/control/tech/): `oTyymmddhhmmss.sss` (время с мс), `TCAL` (калибровка TC ±мс), `*SYNC=1` (GPS-время), `*RLTC=1`/`OLTC` (LTC с аудиовхода), `!5S`/`!20:00S` (старт по задержке/времени!), `!E` (стоп), BURN (burn-in времени в кадр) (high)
- GPS Time Sync: 1–2 кадра, нужен GPS-приём (в помещении нет) (high)
- LTC по аудиовходу: HERO9–13 + Media Mod/Mic Adapter; jam-sync живёт ~час (high). Дорого на 100 камер; вариант «LTC колонками в микрофон» = непрерывный аудио-маркер
- Известный баг: QRControl-приложение добавляло +1.2 с — использовать веб-страницу Precision Time (high)

## GPMF-метаданные (пост-выравнивание)

- **STMP**: таймстемпы 1 мкс, per-payload (не per-frame); семантика спорна (issue gpmf-parser#80) — валидировать экспериментом (high/med)
- **CORI/IORI/GRAV** — кватернионы с частотой кадров (HERO8+); **SHUT** — выдержка per-frame (high)
- Время кадра реконструируется: TSMP + MP4 stts/stsz + TC-трек — прямого UTC-времени экспозиции кадра нет (high)
- Парсер: https://github.com/gopro/gpmf-parser

## Мульти-камерные факты (для будущих фаз)

- Старый репо Андрия (https://github.com/AndriiShramko/Shramko-GoPro13-Control-App-for-multicamera-4dgs-3dgs-rigs): discovery+wired enable, UTC-пуш времени, копирование настроек с prime, camera_cache.json. Аудит → `docs/api/old-repo-audit.md`
- **IP-коллизии**: подсеть из 3 цифр серийника → 1000 вариантов → на 100 камерах P(коллизия)≈99% (birthday problem). Решения: несколько хостов, проверка серийников, netns (расчёт med — проверить стендово)
- Дрейф между камерами: ~3 кадра за 5 мин @240fps ≈ 42 ppm (labs#1101, med)
- **Genlock невозможен** — подтверждено D. Newman (GoPro Labs) и Xangle: фаза сенсора free-running, API нет (high)
- HERO14 не существует (2026-07-12); актуальная платформа = HERO13+Labs; GP3-камеры (MISSION 1) уже в Open GoPro (high)

## Связанные
[[spec]] · [[spec-measure]] · [[spec-experiments]] · [[spec-ops]]
