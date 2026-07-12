# GoPro Labs для HERO13 — миллисекундная синхра времени и автоматизация

## Что это и версия

- Официальная экспериментальная прошивка GoPro (хостится на github GoPro): «safe to install, safe to use,
  only the extended features are experimental and they are all off by default». Штатные режимы сохраняются.
- **HERO13 Black: v2.10.70 от 2025-10-17** (актуально на 2026-07-12; там же HERO12: v2.40.70).
- Установка — ручное обновление прошивки с SD-карты. **⚠️ Установка Labs = операторский гейт**
  (физический доступ к каждой камере, см. spec-ops). Обратима откатом на стоковую прошивку.

## Precision Date & Time QR — главный инструмент синхры для рига

https://gopro.github.io/labs/control/precisiontime/
- Анимированный QR с мс-точностью; камера наводится на экран → часы выставлены. Поддержка:
  «Labs enabled HERO5 Session, HERO7-13, MAX and BONES».
- Страница прямо позиционирует под мультикам: «helps synchronize the timecode between cameras»;
  часы дрейфуют → «sync just before your multi-camera shoot».
- Численная точность на странице НЕ заявлена. По ресёрчу spec-api: **±1 кадр**, пока камеры не
  выключались; после выключения ±1 с (med-high) — TODO-verify-experiment (мигающий LED).
- **QR можно показывать на мониторе** → агент ресинхронизирует камеры без телефона (ключевое отличие
  от стокового Timecode Sync, которому нужен Quik).
- Известный баг: приложение QRControl добавляло +1.2 с — использовать веб-страницу Precision Time (spec-api, high).

## Command Language (QR-команды)

https://gopro.github.io/labs/control/tech/ — точный синтаксис:

| Команда | Что делает |
|---|---|
| `oTyymmddhhmmss.sss` | установить время с мс: `oT241208174033.556` = 2024-12-08 17:40:33.556 |
| `oTD1` / `oTZ-7` / `oTZ-420` | DST on / таймзона (часы или минуты) |
| `*TCAL=ms` | Timecode CALibration — компенсация задержки сканирования QR, ± миллисекунды |
| `!S` | старт записи немедленно |
| `!5S` | старт через 5 с; `!20:00S` — старт ровно в 20:00; `!SQ` — «примерно в срок», энергосберегающий |
| `!60E` / `!20:00E` | стоп через 60 с / стоп в 20:00 («End commands are not used for photos») |
| задержка >9 с | камера уходит в сон до срока (планируемый отложенный старт) |
| `BURN` + `(x,y)[fmt]` | burn-in времени в кадр: `(0,30)Local Time: [HH:MMaa]`; позиции TL/TC/TR/ML/BL/BC/BR |
| `*SYNC=1` | GPS time sync («to millisecond precision») |
| `*RLTC=1` | читать LTC-таймкод с аудиовхода (и включать при загрузке) |
| `*OLTC=x` | сдвиг LTC на x кадров |

Конструктор команд (Blockly): https://gopro.github.io/labs/build/

## LTC по аудиовходу

https://gopro.github.io/labs/control/ltc/ — «Labs enabled HERO9-13 only».
- Нужен line-in: **Media Mod или GoPro Mic Adapter** (в Media Mod выбрать line-in источником).
- Jam-sync: камера подстраивает внутренние часы под последний принятый таймкод; держится «about hour».
- Индикация: красный/жёлтый TC = лока ещё нет (обычно 1-2 с), зелёный ~15 с = лок.
- «At the start of each capture, the current inputted timecode is sampled, and it is used to update the MP4s timecode track».
- Для 100 камер дорого (аксессуар на каждую); вариант «LTC колонками в комнате» = непрерывный аудио-маркер (spec-api).

## GPS Time Sync

`*SYNC=1`; страница tech заявляет мс-точность, spec-api оценивает 1-2 кадра. Нужен GPS-фикс —
**в помещении не работает**, для нашего indoor-рига неприменимо.

## USB Power Trigger

https://gopro.github.io/labs/control/usb/ — «Labs enabled HERO7 (limited), HERO8-13 and MAX».
- Подача USB-питания → камера бутится и начинает запись; снятие питания → стоп и выключение.
  Отмена конкретной записи — кнопкой shutter, полный ручной контроль сохраняется.
- Требуется живой аккумулятор в камере (нужно корректно закрыть файл при пропаже питания).
- Команды (community, gopro/labs discussions #912, #1180): `*WAKE=2*TUSB=1` (wake on power + trust USB);
  надёжнее современный вариант `!MWAKE=2` — «camera will always boot when power is added».
  Точные строки для HERO13 снять с офиц. QR на странице — TODO-verify (QR-картинка, текст в HTML не виден).
- Для рига это единственный способ программно «включить» камеры (USB-хабы с управляемым питанием).

## Источники (снято 2026-07-12)

- https://gopro.github.io/labs/ — версии прошивок, дисклеймеры.
- https://gopro.github.io/labs/control/precisiontime/ · .../tech/ · .../ltc/ · .../usb/ — первоисточники по разделам выше.
- https://github.com/gopro/labs/discussions/912, /1180 (через WebSearch) — синтаксис WAKE/TUSB/!MWAKE.
- `docs/specs/spec-api.md` — оценки точности (±1 кадр, 1-2 кадра GPS) и баг QRControl +1.2 с.
