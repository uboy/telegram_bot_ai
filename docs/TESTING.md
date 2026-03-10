# Testing Guide

## Test Levels

- Unit: pure functions and service logic.
- API contract: routes, auth dependencies, schemas.
- Integration-lite: route handlers with monkeypatched services.
- Regression bot flows: key Telegram handlers.

## Main Command

```bash
.venv\Scripts\python -m pytest -q -p no:cacheprovider
```

## Focused Commands

```bash
# ASR critical path
.venv\Scripts\python -m pytest -q -p no:cacheprovider tests/test_asr_api.py tests/test_asr_worker.py tests/test_asr_queue.py tests/test_bot_voice.py tests/test_bot_audio.py

# Security + route contracts
.venv\Scripts\python -m pytest -q -p no:cacheprovider tests/test_security_api_key.py tests/test_api_routes_contract.py

# Ingestion/jobs and settings
.venv\Scripts\python -m pytest -q -p no:cacheprovider tests/test_ingestion_routes.py tests/test_indexing_jobs_lifecycle.py tests/test_jobs_status.py tests/test_knowledge_settings_routes.py

# Analytics
.venv\Scripts\python -m pytest -q -p no:cacheprovider tests/test_analytics_routes.py

# RAG summary and safety
.venv\Scripts\python -m pytest -q -p no:cacheprovider tests/test_rag_summary_date_filter.py tests/test_rag_summary_modes.py tests/test_rag_safety.py

# RAG prompt contract and formatter regressions
.venv\Scripts\python -m pytest -q -p no:cacheprovider tests/test_rag_prompt_contract.py tests/test_rag_prompt_format.py tests/test_rag_summary_modes.py

# RAG eval contract, baseline runner, and statistical quality gate logic
.venv\Scripts\python -m pytest -q -p no:cacheprovider tests/test_rag_eval_dataset_contract.py tests/test_rag_eval_fixture_manifest.py tests/test_rag_eval_security_contract.py tests/test_rag_eval_api.py tests/test_rag_eval_service.py tests/test_rag_eval_baseline_runner.py tests/test_rag_eval_quality_gate.py

# RAG diagnostics + orchestrator compare tooling
.venv\Scripts\python -m pytest -q -p no:cacheprovider tests/test_rag_diagnostics.py tests/test_rag_orchestrator_compare.py

# Docker stack one-command legacy vs v4 compare
bash scripts/run_rag_compare_stack.sh --max-source-hit-drop 0.10

# Docker stack with automatic test KB recreation + test.pdf ingestion
bash scripts/run_rag_compare_stack.sh --prepare-test-kb --test-pdf test.pdf --max-source-hit-drop 0.10

# Override v4 device if you intentionally want GPU for the temporary v4 container
bash scripts/run_rag_compare_stack.sh --v4-rag-device cuda --max-source-hit-drop 0.10

# Increase comparator HTTP retries when v4 cold-start is slow
bash scripts/run_rag_compare_stack.sh --prepare-test-kb --test-pdf test.pdf --connect-retries 180 --retry-sleep-sec 1.5 --max-source-hit-drop 0.10
```

## Fail-Fast Quality Sequence

Use this order for the committed public-safe quality lane and mirror it locally before broader checks:

```bash
python scripts/ci_policy_gate.py --working-tree
python scripts/scan_secrets.py
python -m py_compile scripts/ci_policy_gate.py scripts/scan_secrets.py scripts/rag_eval_quality_gate.py scripts/rag_eval_baseline_runner.py tests/test_rag_eval_quality_gate.py tests/test_rag_eval_baseline_runner.py tests/test_rag_eval_dataset_contract.py
.venv\Scripts\python -m pytest -q -p no:cacheprovider tests/test_rag_eval_quality_gate.py tests/test_rag_eval_baseline_runner.py tests/test_rag_eval_dataset_contract.py
.venv\Scripts\python -m pytest -q -p no:cacheprovider tests/test_security_api_key.py tests/test_rag_safety.py
```

Notes:
- The GitHub workflow `agent-quality-gates` uses the same fail-fast order.
- The fast lane must stay public-safe and synthetic-only.
- Any local-only corpus/Ollama verification stays outside CI and outside committed reports.
- Eval artifacts now include `source_families`, `security_scenarios`, `failure_modes`, and `slice_thresholds`; the quality gate uses those persisted slice thresholds for source-family/failure-mode checks before falling back to the flat CLI defaults.
- To enable local-only answer/judge scoring, set developer-local env vars before running the baseline runner:
```powershell
$env:RAG_EVAL_ENABLE_ANSWER_METRICS="true"
$env:RAG_EVAL_ENABLE_JUDGE_METRICS="true"
$env:RAG_EVAL_JUDGE_PROVIDER="<optional_judge_provider>"
$env:RAG_EVAL_JUDGE_MODEL="<optional_judge_model>"
$env:RAG_EVAL_OLLAMA_BASE_URL="http://localhost:11434"   # only if current provider or judge is ollama
python scripts/rag_eval_baseline_runner.py --suite rag-general-v1 --label local_answer_eval --out-dir data/rag_eval_baseline
```
- By default this lane reuses the main provider/model from `.env` via `AI_DEFAULT_PROVIDER` and provider-specific model envs; separate eval provider settings are optional, not required.
- If you want an isolated live run, set `RAG_EVAL_KB_ID=<id>` so eval uses only the prepared local test KB instead of searching across all KBs.
- When `--slices` is omitted, the local baseline runner now reports only slices that are actually covered by the selected suite/cases; this keeps filtered live runs free of irrelevant zero-sample groups.
- If you pass explicit `--slices`, that list stays strict on purpose and can still surface missing/zero-sample slices for debugging or gate validation.
- When answer metrics are enabled, the run artifact records `available_metrics`, `answer_provider`, `judge_provider`, `answer_model`, `judge_model`, sanitized `effective_ollama_base_url` only when Ollama is in use, `git_sha`, `git_dirty`, `screening_summary`, `security_summary`, `case_failures`, and `suspicious_events`.
- The same local-only answer lane now also records `case_analysis` entries for failed/suspicious cases with compact query/answer previews, failure reasons, suspicious events, score snapshots, and source-path hints; use these entries for commit-to-commit triage instead of embedding real corpus excerpts in committed tests.
- When answer metrics are disabled, retrieval-only artifacts must not render answer-lane provider/model/base-url metadata.

Note: wrapper auto-starts `backend redis qdrant` when `telegram_rag_backend` is not running.
Note: wrapper fails by default when retrieval selected-context rate is near zero (`--min-selected-rate 0.01`).
Note: with `--prepare-test-kb`, wrapper auto-generates cases from the uploaded PDF unless `--no-auto-cases` is passed.

## CI Smoke Recommendation

- Run on each PR:
  - `python scripts/ci_policy_gate.py --base <base_ref> --head <head_ref>`
  - `python scripts/scan_secrets.py`
  - `python -m py_compile scripts/ci_policy_gate.py scripts/scan_secrets.py scripts/rag_eval_quality_gate.py scripts/rag_eval_baseline_runner.py tests/test_rag_eval_quality_gate.py tests/test_rag_eval_baseline_runner.py tests/test_rag_eval_dataset_contract.py`
  - `tests/test_rag_eval_quality_gate.py`
  - `tests/test_rag_eval_baseline_runner.py`
  - `tests/test_rag_eval_dataset_contract.py`
  - `tests/test_asr_api.py`
  - `tests/test_bot_voice.py`
  - `tests/test_bot_audio.py`
  - `tests/test_security_api_key.py`
  - `tests/test_api_routes_contract.py`
  - `tests/test_rag_summary_date_filter.py`
  - `tests/test_rag_summary_modes.py`

## Notes

- Some modules initialize RAG models during import, so first test startup can be slow.
- Use `.venv` to keep dependency versions consistent with `requirements.txt`.
- Committed eval inputs must stay synthetic/public-safe; real local corpora are for developer-local verification only and must not be embedded in repo fixtures or reports.
- CI intentionally does not run local-only corpora or Ollama-based evaluation.
