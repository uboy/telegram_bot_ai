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
```

## CI Smoke Recommendation

- Run on each PR:
  - `python scripts/ci_policy_gate.py --base <base_ref> --head <head_ref>`
  - `python scripts/scan_secrets.py`
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
