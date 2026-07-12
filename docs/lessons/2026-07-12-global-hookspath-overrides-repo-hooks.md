# Глобальный core.hooksPath молча отключает .git/hooks репозитория

На этой машине глобальный git config задаёт `core.hooksPath=C:\Users\andri\.claude\ua-auto-index\hooks` (тулинг understand-anything). Из-за этого положенный в `.git/hooks/pre-push` guard НЕ вызывался вовсе — dry-run пуш в main прошёл без блока.

**Почему важно:** «hook установлен» ≠ «hook работает». Глобальный hooksPath перекрывает per-repo хуки без каких-либо предупреждений.

**Как применять:** после установки хука всегда (1) `git config core.hooksPath` — проверить перекрытие; (2) реальный негативный тест (`git push --dry-run origin HEAD:main` при наличии коммитов — должен быть BLOCKED). Решение здесь: локальный `core.hooksPath=.githooks` + цепочка на глобальный post-commit, чтобы не сломать ua-auto-index.
