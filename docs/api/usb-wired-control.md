# HTTP-over-USB (Open GoPro HTTP API 2.0) — wired-управление HERO13

Одна и та же HTTP-спека для WiFi и USB; по USB отличаются только адрес и отсутствие аутентификации.
HERO13 Black поддержана (badge HERO13 стоит на всех перечисленных ниже операциях в OpenAPI).
Минимальная прошивка HERO13 для Open GoPro: v01.10.00 (spec-api ресёрч; страница supported-cameras BLE-спеки JS-рендерится — TODO-verify точную строку там).

## Транспорт и адресация

- Камера по USB работает как NCM-сеть (Network Control Model). Windows-драйвер — отдельная боль: см. [windows-usb-driver.md](windows-usb-driver.md).
- **Адрес камеры: `172.2X.1YZ.51:8080`**, где X,Y,Z — последние 3 цифры серийника.
  Официальный пример из доков: серийник `C0000123456789` → `172.27.189.51`.
  Серийник: наклейка в батарейном отсеке / Preferences >> About >> Camera Info / BLE Hardware Info.
- Альтернатива по докам: **mDNS**, камера регистрирует сервис `_gopro-web`.
- Камера раздаёт DHCP; хост получает `.54` в той же подсети (наблюдение, в офиц. доках не зафиксировано — TODO-verify).
- **Практическое правило проекта:** IP надёжнее вычислять из `ipconfig` — gateway адаптера в подсети `172.2*` = адрес камеры, а не из серийника. На ~100 камерах подсеть из 3 цифр даёт коллизии с P≈99% (birthday problem, см. spec-api).
- Аутентификация по USB: **не требуется** (по WiFi — SSID/пароль).

## Включение wired-режима

```
GET http://172.2X.1YZ.51:8080/gopro/camera/control/wired_usb?p=1
```
`p=1` — включить wired usb control, `p=0` — выключить (query, integer, обязателен).

## Keep-alive и сон камеры

```
GET /gopro/camera/keep_alive
```
Из доков: камера засыпает, когда обнулились ОБА таймера — Auto Power Down (настройка 59) и Keep Alive.
Таймер APD сбрасывают: тап по экрану, кнопка, программный shutter, установка настройки, загрузка пресета.
**Best practice из офиц. доков: слать keep-alive каждые 3.0 секунды** после установления соединения
(интервал подтверждён первоисточником, не community).

## Готовность к командам

Флаги **System Busy** и **Encoding Active** в `/gopro/camera/state`. Best practice: перед любой командой
(кроме запросов статуса) ждать снятия обоих флагов. Камера отклоняет смену настроек во время записи.
Для захвата контроля у подключённого приложения: `GET /gopro/camera/control/set_ui_controller` (Set Camera Control Status).

## Нужные нам endpoints (все — точные пути из OpenAPI, метод GET, USB+WiFi)

| Операция | Endpoint | Параметры |
|---|---|---|
| Включить wired-управление | `/gopro/camera/control/wired_usb` | `p` = 0/1 |
| Keep-alive | `/gopro/camera/keep_alive` | — |
| Состояние (настройки+статусы) | `/gopro/camera/state` | — |
| Инфо о железе | `/gopro/camera/info` | — |
| Версия Open GoPro | `/gopro/version` | — |
| Старт/стоп записи | `/gopro/camera/shutter/{mode}` | `mode` = `start` \| `stop` |
| Установка/чтение настройки | `/gopro/camera/setting` | `setting`=ID, `option`=ID (без `option` — чтение) |
| Прочитать дату/время | `/gopro/camera/get_date_time` | — |
| Установить дату/время | `/gopro/camera/set_date_time` | `date`=YYYY_MM_DD, `time`=HH_MM_SS, `tzone`=минуты, `dst`=0/1 |
| Список медиа | `/gopro/media/list` | — |
| Последний снятый файл | `/gopro/media/last_captured` | — |
| Инфо о файле | `/gopro/media/info` | `path` |
| Скачать файл | `/videos/DCIM/{directory}/{filename}` | пути case-sensitive |
| GPMF-телеметрия файла | `/gopro/media/gpmf` | `path` |
| Telemetry-трек файла | `/gopro/media/telemetry` | `path` |
| Turbo Transfer | `/gopro/media/turbo_transfer` | `p` = 0/1 (в основном для WiFi) |

Точность секундная: `set_date_time` принимает только `HH_MM_SS` — миллисекундная синхра времени
стоковым HTTP API невозможна; для мс-точности см. [labs.md](labs.md) и [timecode-sync-stock.md](timecode-sync-stock.md).

## Ограничения

- **Включить камеру по USB программно нельзя** (только BLE, кнопка или Labs USB Power Trigger) — spec-api, high.
- Запрос значения настройки вне текущего пресета → undefined value (из доков).
- Python SDK: `open-gopro` 0.22.0 (PyPI, 2025-09-24), Python >=3.11 <3.14, есть wired/USB интерфейс.
  Запасной путь — тонкий requests-клиент (двух десятков строк хватает на весь цикл E0).

## Источники (снято 2026-07-12)

- https://gopro.github.io/OpenGoPro/http/ — страница JS-рендерится из openapi.json; весь текст раздела
  Setup/USB/Socket Address/Keep Alive взят из `info.description` OpenAPI (локальная копия `docs/openapi (1).json`).
- https://gopro.github.io/OpenGoPro/http/openapi.json — OpenAPI 3.1, 91 путь; параметры endpoints — оттуда.
- https://pypi.org/pypi/open-gopro/json — версия SDK.
- `docs/specs/spec-api.md` — факты ресёрча (хост `.54`, запрет power-on по USB, IP-коллизии).
