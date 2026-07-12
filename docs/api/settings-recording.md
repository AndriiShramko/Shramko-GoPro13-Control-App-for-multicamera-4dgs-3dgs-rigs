# Настройки записи HERO13 через HTTP API (setting?setting=ID&option=ID)

## Механизм

```
GET /gopro/camera/setting?setting={SETTING_ID}&option={OPTION_ID}
```
- С параметром `option` — установка значения. **Без `option` — запрос текущего значения** этой настройки
  (из описания параметра в OpenAPI: «If this parameter is not used, instead the current setting's option will be queried»).
- Все текущие значения разом — в `/gopro/camera/state` (секция settings).
- Камера **отклоняет смену настроек во время записи** (Encoding Active) и при System Busy.
- Значение настройки, не относящейся к текущему пресету, — undefined (официальное ограничение).

## Ключевые ID для нашего рига (HERO13, сверено по badge HERO13 в OpenAPI)

⚠️ Легаси-настройка **3 (Frames Per Second) на HERO13 НЕ поддержана** — fps ставится через **234 (Frame Rate)**.

### 2 — Video Resolution (HERO13-опции)
`1`=4K, `4`=2.7K, `9`=1080, `12`=720, `18`=4K 4:3, `27`=5.3K 4:3, `37`=4K 1:1, `38`=900,
`100`=5.3K, `107`=5.3K 8:7 V2, `108`=4K 8:7 V2, `109`=4K 9:16 V2, `110`=1080 9:16 V2, `111`=2.7K 4:3 V2

### 234 — Frame Rate (HERO13-опции)
`0`=240, `1`=120, `2`=100, `5`=60, `6`=50, `8`=30, `9`=25, `10`=24, `13`=200, `15`=400, `16`=360, `17`=300

### 135 — Hypersmooth (HERO13-опции)
`0`=Off, `1`=Low, `4`=Auto Boost
(для 4DGS-рига: **Off** — стабилизация ломает геометрию мультивью)

### 59 — Auto Power Down (HERO13-опции)
`0`=Never, `1`=1 Min, `4`=5 Min, `6`=15 Min, `7`=30 Min
(взаимодействует с keep-alive: камера спит, только когда обнулены ОБА таймера — см. usb-wired-control.md)

## Прочие полезные ID (HERO13, из той же OpenAPI)

| ID | Настройка | HERO13-опции |
|---|---|---|
| 134 | Anti-Flicker | `0`=NTSC, `1`=PAL |
| 121 | Video Lens | `0`=Wide, `2`=Narrow, `3`=Superview, `4`=Linear, `8`=Linear+Horizon Leveling, `9`=HyperView, `10`=Linear+Horizon Lock, `12`=Ultra SuperView, `13`=Ultra Wide, `104`=Ultra HyperView |
| 232 | Video Framing | `0`=4:3, `1`=16:9, `3`=8:7, `4`=9:16, `6`=1:1 |
| 182 | Video Bit Rate | `0`=Standard, `1`=High |
| 183 | Bit Depth | `0`=8-Bit, `2`=10-Bit |
| 180 | System Video Mode | `0`=Highest Quality, `111`=Standard Quality, `112`=Basic Quality |
| 83 | GPS | `0`=Off, `1`=On (нужен для GPMF GPS9; в помещении бесполезен) |
| 128 | Media Format | `13`=Time Lapse Video, `20`=TL Photo, `21`=NL Photo, `26`=NL Video |
| 162 | Max Lens | на HERO13 НЕ поддержана (есть 189 Max Lens Mod) |

Полный перечень: 47 setting-путей в `docs/openapi (1).json`; выше — только релевантные ригу.

## Известные грабли / TODO-verify-on-camera

- **Валидные комбинации** resolution×fps×lens зависят от режима/пресета: не каждое сочетание из enum'ов
  принимается (например 5.3K@240 не существует). Матрицу валидных комбо снять экспериментом — TODO-verify-on-camera.
- Опции с суффиксом «V2» у настройки 2 — специфика HERO12/13 (новая нумерация соотношений); проверить,
  какие реально выбираются на HERO13 при Video Framing 232 — TODO-verify-on-camera.
- Порядок применения: сперва framing/режим, потом resolution, потом fps — иначе камера может молча
  перещёлкнуть зависимую настройку. Порядок подтвердить экспериментом — TODO-verify-on-camera.
- Ответ на успешную установку — HTTP 200 с пустым JSON `{}`; при невалидном сочетании — не-200
  (точный код зафиксировать на камере — TODO-verify-on-camera).
- Старый репо Андрия копировал ВСЕ настройки с prime-камеры перебором ID из `/gopro/camera/state` —
  рабочий паттерн для клонирования конфигурации на риг.

## Источники (снято 2026-07-12)

- https://gopro.github.io/OpenGoPro/http/openapi.json (локальная копия `docs/openapi (1).json`) —
  все ID, имена и enum-опции извлечены из путей `/gopro/camera/setting?setting=*`; принадлежность
  HERO13 — по camera-badge в описании каждой опции.
- `docs/specs/spec-api.md` — контекст рига (2026-07-12).
