---
type: resource
tags: [fable-5, prompting, goal-mode, anthropic]
created: 2026-07-12
status: active
---

# gopro-genlock — spec-fable: как построен промт под Fable 5

Роутер: [[spec]]. Выжимка актуальных доков Anthropic (сняты агентом 2026-07-12: introducing-claude-fable-5.md, migration-guide.md → Fable 5, prompt-engineering/prompting-claude-fable-5). Применять при любой будущей правке [[goal-prompt]].

## Ключевые принципы Fable 5 (применены в промте)

1. **Цель + ограничения, НЕ step-by-step**: prescriptive-сценарии ДЕГРАДИРУЮТ качество Fable — модель сама структурирует multi-step. Промт даёт MISSION, жёсткие факты-ограничения и DONE; порядок работ не расписан.
2. **Автономность явно**: «работаешь автономно, вопрос "сделать ли X?" = блокировка; перед завершением хода проверь последний абзац — если это план/обещание, сделай это сейчас». (сниппет из migration guide, адаптирован)
3. **Evidence-grounded прогресс**: «каждое заявление о прогрессе — против tool-result этой сессии; непроверенное называй непроверенным» — почти устраняет фабрикацию статусов на длинных ранах. Совпадает с правилом vault #12.
4. **Act when enough info**: «когда информации достаточно — действуй, не пере-выводи уже установленные факты» — против overplanning на high effort.
5. **Границы действий**: явный запрет-список (прошивка, main-ветка, System32) работает лучше размытых предостережений.
6. **Memory surface**: Fable заметно лучше работает, когда ему сказано КУДА писать уроки (docs/lessons/ в репо + Obsidian) и что консультироваться с ними.
7. **Субагенты**: Fable охотно делегирует — дать правило «параллельные независимые подзадачи → субагенты; простое — сам».
8. **Self-verification**: отдельный fresh-context верификатор лучше самокритики; в DONE вшита проверка фактом.
9. **Не просить «покажи своё мышление»** — триггерит reasoning_extraction refusal.
10. **Не бояться длинных ходов**: многоминутные turns — норма; чекпоинты прогресса в файлы.

## Анти-паттерны (избегали)

- «CRITICAL: YOU MUST…» — overtriggering; обычный тон достаточен.
- Enumerate всех кейсов — краткий принцип + 1 пример работает лучше.
- Шаги-сценарии («шаг 1… шаг 2…») в промте — только в спеках как ЧЕК-ЛИСТЫ проверки, не как сценарий выполнения.
- Guardrail на каждое действие — только реальные гейты (прошивка/main/физика).

## Готовые EN-сниппеты (дословно из доков, для будущих промтов)

```
When you have enough information to act, act. Do not re-derive facts already established in the conversation.
```
```
Before reporting progress, audit each claim against a tool result from this session. Only report work you can point to evidence for; if something is not yet verified, say so explicitly.
```
```
You are operating autonomously. The user is not watching in real time and cannot answer questions mid-task... Before ending your turn, check your last paragraph. If it is a plan, an analysis, a question, a list of next steps, or a promise about work you have not done ("I'll…"), do that work now with tool calls.
```
```
Delegate independent subtasks to subagents and keep working while they run. Intervene if a subagent goes off track or is missing relevant context.
```
```
Store one lesson per file with a one-line summary at the top... update an existing note rather than creating a duplicate; delete notes that turn out to be wrong.
```

## Связанные
[[spec]] · [[goal-prompt]]
