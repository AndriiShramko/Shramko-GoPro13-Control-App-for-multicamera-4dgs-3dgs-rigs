# docs/api — база знаний по API для gopro-sync (HERO13, 4DGS-риг)

Собрано 2026-07-12 по первоисточникам (Open GoPro, GoPro Labs, gpmf-parser).
Скелет — `docs/specs/spec-api.md`. Каждый файл содержит блок «Источники» с датой снятия.

## Файлы

| Файл | О чём |
|---|---|
| [usb-wired-control.md](usb-wired-control.md) | HTTP-over-USB: включение wired-режима, схема IP `172.2X.1YZ.51`, keep-alive, полный список нужных endpoints |
| [settings-recording.md](settings-recording.md) | Механизм `setting?setting=ID&option=ID`; ID и опции HERO13: разрешение (2), fps (234), HyperSmooth (135), Auto Power Down (59) и др. |
| [media-gpmf.md](media-gpmf.md) | Формат media list, скачивание файлов, GPMF endpoint; стримы STMP/CORI/SHUT/GPS9; инструменты парсинга |
| [labs.md](labs.md) | GoPro Labs (HERO13 v2.10.70): Precision Date & Time QR, команды oT/TCAL/!S/!E/BURN, LTC, GPS sync, USB Power Trigger |
| [windows-usb-driver.md](windows-usb-driver.md) | Проблема NCM-драйвера Windows: наша диагностика + внешнее подтверждение (OpenGoPro issue #384), два способа лечения |
| [timecode-sync-stock.md](timecode-sync-stock.md) | Стоковый Timecode Sync HERO12/13 через Quik QR: механизм, формат, точность, ограничения |
| [old-repo-audit.md](old-repo-audit.md) | Аудит legacy-кода GoPro Control App в корне репо: что адаптировать для Фазы 0 (barrier-старт, ретраи, discovery) |

## Локальные офлайн-источники в репо

- `docs/openapi (1).json` — полная копия OpenAPI 3.1 спеки Open GoPro HTTP API 2.0
  (91 путь; из неё извлечены точные endpoints и setting/option ID — сверено 2026-07-12).
- `docs/session-logs/2026-07-12-E0-connectivity-gate.md` — живая диагностика NCM-драйвера на этой машине.
- `docs/specs/spec-api.md` — фактология ресёрча 2026-07-12 с уровнями уверенности.

## Конвенции

- Endpoints и команды — точные строки на английском; НЕ выдуманные: всё сверено с OpenAPI/страницами Labs.
- Непроверенное помечено `TODO-verify` (обычно = проверить экспериментом на камере, см. spec-experiments).
