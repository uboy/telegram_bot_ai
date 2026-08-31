# Учебный эксперимент: RAG по нацстратегии ИИ (курс Вышка)

Материал спасён 2026-08-31 из `~/proj/test` (устаревший клон `HW_MWP_DATASIENCE`,
удалённый при аудите `~/proj`) - каталог был в `.gitignore` и существовал только на
диске. Владелец подтвердил: это не курсовое ДЗ конкретного предмета, а личный тест
RAG-конвейера, документ-источник взят из курса «Цифровая трансформация бизнеса»,
эксперимент проводился в контексте этого проекта (telegram_bot_ai).

## Что здесь

- `reference/` - исходный документ («Национальная стратегия развития ИИ 2024», PDF)
  и презентация задания.
- `code_extracted/` - 11 модулей RAG-конвейера на момент финальной сдачи: `retriever.py`,
  `generator.py`, `pipeline.py`, `classifier.py`, `index.py`, `client.py`, `config.py`,
  `document.py`, `grounded_rules.py`, `main.py`, `__init__.py`.
- `submissions/` - `submission_ready.csv` (вопрос-ответ) и `test_set_*.xlsx`, финальная
  сдача.
- `history_test/` - `evaluation_30.csv`/`fact_check_30.csv` с summary и промежуточные
  выводы (`output/`).
- `root_outputs/` - черновые прогоны `answers_debug.csv`, `answers_submission.csv`.
- `legacy/` - более ранняя версия конвейера на OpenWebUI (`rag_openwebui.py`).
- `coordination/` - ревью процесса разработки (`reviews/`, март 2026): качество RAG,
  точность и полнота извлечения, русский UX, готовность к сдаче.

## Что НЕ перенесено

- `.env` с `OLLAMA_API_TOKEN` и настройками (embedding/reranker/chunking) - секрет.
- `.agent-memory/`, `.scratchpad/` - рабочий процесс, не результат.
- `claude-settings.local.json` - локальные права инструмента.
