# Документация проекта

Единая документация проекта Telegram Bot AI (bot + backend + RAG + analytics).

## Основные документы

- **[ARCHITECTURE.md](ARCHITECTURE.md)** - актуальная архитектура и границы модулей.
- **[API_REFERENCE.md](API_REFERENCE.md)** - API-контракты `/api/v1/*`, auth, примеры.
- **[USAGE.md](USAGE.md)** - пользовательские и админские сценарии.
- **[CONFIGURATION.md](CONFIGURATION.md)** - env-переменные и профиль конфигурации.
- **[DATABASE.md](DATABASE.md)** - DB-режимы, миграции, эксплуатационные заметки.
- **[TESTING.md](TESTING.md)** - стратегия тестирования и команды запуска.
- **[OPERATIONS.md](OPERATIONS.md)** - runbook запуска/диагностики/отката.
- **[REQUIREMENTS_TRACEABILITY.md](REQUIREMENTS_TRACEABILITY.md)** - трассировка требований к реализации и тестам.

## Design Specs (`docs/design/`)

- `voice-to-text-v1.md` - APPROVED:v1
- `audio-handler-v1.md` - APPROVED:v1
- `asr-num-frames-v1.md` - APPROVED:v1
- `asr-warnings-audio-metadata-v1.md` - APPROVED:v1
- `async-ingestion-jobs-v1.md` - APPROVED:v1
- `kb-settings-api-v1.md` - APPROVED:v1
- `rag-summary-modes-v1.md` - APPROVED:v1
- `codebase-ingestion-v1.md` - APPROVED:v1

## Templates (`docs/templates/`)

- `feature-spec-template.md` - template for new feature specs.
- `bugfix-spec-template.md` - template for bugfix specs with regression requirements.

## Прочие материалы

- **[CHAT_ANALYTICS_DESIGN.md](CHAT_ANALYTICS_DESIGN.md)** - утвержденный детальный дизайн аналитики чатов.
- **[FILES_OVERVIEW.md](FILES_OVERVIEW.md)** - обзор модулей и потоков данных.
- **[IMPROVEMENTS.md](IMPROVEMENTS.md)** - backlog улучшений.

## Внешние ссылки в корне репозитория

- [ROOT_DOCS.md](ROOT_DOCS.md)
- [../README.md](../README.md)
- [../SPEC.md](../SPEC.md)
- [../IMPLEMENTATION_PLAN.md](../IMPLEMENTATION_PLAN.md)
- [../AGENTS.md](../AGENTS.md)

