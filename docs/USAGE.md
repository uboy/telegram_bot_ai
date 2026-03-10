# Использование системы

## Запуск

1. Установите зависимости:
```bash
.venv\Scripts\python -m pip install -r requirements.txt
```

2. Настройте `.env` (см. `docs/CONFIGURATION.md`).

3. Запустите backend:
```bash
.venv\Scripts\python -m backend.main
```

4. Запустите бота:
```bash
.venv\Scripts\python -m frontend.bot
```

### Docker compose (умный запуск)

```bash
python scripts/start_stack.py
```

```powershell
.\scripts\start_stack.ps1
```

```bash
./scripts/start_stack.sh
```

- Если в `.env` задан `MYSQL_URL`, launcher поднимет профиль `mysql` (включая `db`).
- Если `MYSQL_URL` пустой/не задан, `db` контейнер не будет запускаться.

## Пользовательские функции

- Поиск по базе знаний (`/rag/query` через UI).
- Сводки, FAQ, инструкции (`/rag/summary` режимы).
- Web search.
- Обработка изображений.
- Голос/аудио транскрипция (ASR).

## Админ-функции

- Управление пользователями.
- Управление базами знаний (create/list/clear/delete).
- Загрузка документов запускается внутри выбранной базы знаний (в общем админ-меню отдельной кнопки загрузки нет).
- Ingestion:
  - документы
  - web/wiki
  - изображения
  - codebase path/git
- Мониторинг job-статусов.
- Настройки ASR и analytics.

## Типовые сценарии

### 1) Загрузка документа в KB
1. Откройте админ-меню.
2. Выберите KB.
3. Отправьте один или несколько документов — тип файла выбирать вручную не нужно.
4. Бот сам определит формат, запустит обработку и вернет итоговый отчет по каждому файлу (успех/ошибка).
5. Если файл превышает лимит Telegram, он будет отмечен в отчете как ошибочный.

### 1.1) Создание новой базы знаний
1. Откройте `👨‍💼 Админ-панель` -> `📚 Управление базами знаний`.
2. Нажмите `➕ Создать базу знаний`.
3. Отправьте название базы обычным текстовым сообщением.
4. При успехе бот возвращает явный результат (`✅`) и сразу показывает меню действий этой базы знаний: загрузка документов, wiki, настройки, очистка и т.д.
5. При ошибке бот возвращает `❌` и оставляет обычное админ-меню.

### 1.2) Сбор wiki по URL
1. Откройте `👨‍💼 Админ-панель` -> `📚 Управление базами знаний` и выберите нужную БЗ.
2. Нажмите `🌐 Собрать вики по URL`.
3. Отправьте корневой URL wiki-раздела (например, `https://gitee.com/<org>/<repo>/wikis`).
4. Бот запускает backend wiki-crawl и возвращает итог: корневой URL, режим синхронизации (`git fallback` или `HTML crawl`), число удаленных старых фрагментов, обработанных страниц и добавленных фрагментов.
5. Если вы нажали старую кнопку загрузки wiki через git/zip из устаревшего сообщения, бот не будет запускать отдельную orphan-ветку, а попросит вернуться к шагу `🌐 Собрать вики по URL`.

### 2) Вопрос по KB
1. Выберите режим поиска в KB.
2. Если база знаний одна, бот выберет ее автоматически; если баз несколько, бот попросит выбрать, по какой искать в текущей search-session.
3. После выбора все последующие запросы в этой search-session идут по выбранной KB.
4. Если вы заново входите в режим `🔍 Поиск в базе знаний`, предыдущий выбор KB сбрасывается и при нескольких базах выбор нужно сделать снова.
5. Введите вопрос.
6. Если ответ обрабатывается долго, бот покажет временный индикатор ожидания и удалит его после ответа.
7. Можно отправить несколько вопросов подряд: бот обработает их по очереди и ответит под каждым исходным вопросом.
8. Получите ответ с источниками.
9. Для Phase D cutover можно включить `RAG_ORCHESTRATOR_V4=true` в `.env`:
   - в этом режиме route-level intent boosts/keyword fallback отключаются,
   - rollback: вернуть `RAG_ORCHESTRATOR_V4=false` и перезапустить `backend` + `bot`.

### 3) Транскрипция аудио
1. Отправьте voice/audio сообщение.
2. Дождитесь обновления статуса.
3. Получите транскрипцию и метаданные.

### 4) Прямой вопрос ИИ (текст + voice/audio)
1. Нажмите `🤖 Задать вопрос ИИ`.
2. Если есть недавний диалог, выберите: восстановить контекст или начать новый.
3. Отправьте текст, голосовое или аудиофайл.
4. Для voice/audio бот сначала делает транскрипцию, затем отправляет текст в ИИ и возвращает ответ.
5. Если запрос долгий (>5с прогноз/факт), бот показывает временный статус ожидания и удаляет его после ответа.
6. Первый ответ ИИ дается кратко; если запрос неоднозначный, бот сначала задаст один уточняющий вопрос.
7. Пустой текстовый ввод не отправляется в ИИ — бот попросит ввести непустой вопрос.
8. Если ответ ИИ слишком длинный, бот отправит его частями.

## Smoke-проверка RAG API

Быстрая проверка backend `/api/v1/rag/query`:

```bash
.venv\Scripts\python.exe scripts/rag_api_smoke_test.py --base-url http://localhost:8000 --api-key <API_KEY> --kb-id 1 --fail-on-empty
```

С кастомным набором кейсов:

```bash
.venv\Scripts\python.exe scripts/rag_api_smoke_test.py --cases-file tests/rag_api_cases.json --fail-on-empty
```

## Диагностика retrieval по `request_id`

1. Выполните запрос в KB-режиме или вызовите `/api/v1/rag/query`.
2. Возьмите `request_id` из ответа.
3. Запросите детализацию retrieval:

```bash
curl -H "X-API-Key: <API_KEY>" http://localhost:8000/api/v1/rag/diagnostics/<request_id>
```

В ответе будут:
- intent/orchestrator_mode/hints/filters запроса,
- количество кандидатов и выбранных фрагментов,
- latency и признаки деградации (`degraded_mode`, `degraded_reason`),
- top-кандидаты с channel/fusion/rerank метриками и метаданными.

## Запуск RAG eval-run

По умолчанию eval-run использует фиксированный ready-data корпус:
- `tests/data/rag_eval_ready_data_v2.yaml`

Запустить benchmark suite:

```bash
curl -X POST http://localhost:8000/api/v1/rag/eval/run \
  -H "X-API-Key: <API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"suite":"rag-general-v1","slices":["overall","en","howto"]}'
```

Проверить статус:

```bash
curl -H "X-API-Key: <API_KEY>" http://localhost:8000/api/v1/rag/eval/<run_id>
```

Прогнать quality gate по завершенному run:

```bash
python scripts/rag_eval_quality_gate.py --run-id <run_id> --baseline-run-id <baseline_run_id> --print-json
```

Прогнать quality gate по report-артефактам (без чтения run из БД):

```bash
python scripts/rag_eval_quality_gate.py --run-report-json data/rag_eval_baseline/latest/baseline_v1.json --allow-no-baseline --print-json
```

С baseline report comparison:

```bash
python scripts/rag_eval_quality_gate.py --run-report-json data/rag_eval_baseline/latest/candidate_v1.json --baseline-report-json data/rag_eval_baseline/latest/baseline_v1.json --print-json
```

Сформировать baseline-отчет с артефактами (`json` + `md`):

```bash
python scripts/rag_eval_baseline_runner.py --suite rag-general-v1 --label baseline_v1 --out-dir data/rag_eval_baseline
```

После запуска будут созданы:
- timestamped `*.json` и `*.md` отчеты в `data/rag_eval_baseline/runs/<label>/`,
- стабильные snapshots `data/rag_eval_baseline/latest/<label>.json` и `data/rag_eval_baseline/latest/<label>.md`,
- append-only history `data/rag_eval_baseline/trends/<label>.jsonl`.

### Локальная fail-fast последовательность для quality lane

Для public-safe локальной проверки повторяйте тот же порядок, что и CI:

```bash
python scripts/ci_policy_gate.py --working-tree
python scripts/scan_secrets.py
python -m py_compile scripts/ci_policy_gate.py scripts/scan_secrets.py scripts/rag_eval_quality_gate.py scripts/rag_eval_baseline_runner.py tests/test_rag_eval_quality_gate.py tests/test_rag_eval_baseline_runner.py tests/test_rag_eval_dataset_contract.py
.venv\Scripts\python -m pytest -q -p no:cacheprovider tests/test_rag_eval_quality_gate.py tests/test_rag_eval_baseline_runner.py tests/test_rag_eval_dataset_contract.py
.venv\Scripts\python -m pytest -q -p no:cacheprovider tests/test_security_api_key.py tests/test_rag_safety.py
```

Примечания:
- этот committed lane использует только synthetic/public-safe данные;
- локальные реальные корпуса и Ollama-проверки остаются developer-local и не должны попадать в CI, docs или артефакты репозитория.

## Сравнение legacy vs v4 orchestrator на реальном API

Сравнить качество ответов между двумя backend-инстансами (например, `legacy` и `RAG_ORCHESTRATOR_V4=true`) на одном и том же наборе кейсов:

```bash
python scripts/rag_orchestrator_compare.py ^
  --legacy-base-url http://legacy-host:8000 ^
  --v4-base-url http://v4-host:8000 ^
  --api-key <API_KEY> ^
  --kb-id <KB_ID> ^
  --cases-file tests/data/rag_eval_ready_data_v1.yaml ^
  --json-out data/rag_compare_report.json
```

Опциональный fail-gate по просадке `source_hit_rate`:

```bash
python scripts/rag_orchestrator_compare.py --legacy-base-url http://legacy-host:8000 --v4-base-url http://v4-host:8000 --api-key <API_KEY> --kb-id <KB_ID> --max-source-hit-drop 0.10
```

Для docker-стека этого репозитория (одной командой, без ручного запуска второго backend):

```bash
bash scripts/run_rag_compare_stack.sh --max-source-hit-drop 0.10
```

Скрипт автоматически поднимает `backend redis qdrant`, если `telegram_rag_backend` не запущен (предпочитает `docker-compose`, fallback: `docker compose`).

Примечание: временный `v4` backend в этом скрипте запускается с `RAG_DEVICE=cpu` по умолчанию (чтобы избежать OOM/падений при параллельном запуске с legacy). При необходимости можно переопределить: `--v4-rag-device cuda`.

Опционально указать конкретную БЗ:

```bash
bash scripts/run_rag_compare_stack.sh --kb-id 1 --max-source-hit-drop 0.10
```

Подготовить тестовую БЗ автоматически (удалить старую с тем же именем, создать заново и загрузить `test.pdf` из корня проекта):

```bash
bash scripts/run_rag_compare_stack.sh --prepare-test-kb --test-pdf test.pdf --max-source-hit-drop 0.10
```

В режиме `--prepare-test-kb` wrapper по умолчанию генерирует eval-кейсы из чанков загруженного PDF (чтобы избежать пустых сравнений из несоответствующего `tests/rag_eval.yaml`). Отключение: `--no-auto-cases`.

Если `v4` поднимается медленно (первичная загрузка моделей), увеличьте сетевые retry comparator:

```bash
bash scripts/run_rag_compare_stack.sh --prepare-test-kb --test-pdf test.pdf --connect-retries 180 --retry-sleep-sec 1.5 --max-source-hit-drop 0.10
```

По умолчанию wrapper также валит прогон, если доля кейсов с выбранным retrieval-контекстом слишком низкая (`--min-selected-rate 0.01`), чтобы исключить ложный "зеленый" прогон при пустых ответах.
