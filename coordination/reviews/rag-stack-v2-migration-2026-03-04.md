# Review Report: RAG Stack v2 Migration

- Date: 2026-03-04
- Reviewer: codex-review
- Task: RAGSTACK-004
- Verdict: PASS

## Scope Reviewed
- `backend/api/routes/rag.py`
- `frontend/bot_handlers.py`
- `frontend/bot_callbacks.py`
- `shared/utils.py`
- `scripts/rag_api_smoke_test.py`
- Tests:
  - `tests/test_rag_query_definition_intent.py`
  - `tests/test_bot_text_ai_mode.py`

## Findings
- Added factual retrieval mode (`FACTOID`) with stronger hints/boosting for legal and numeric questions.
- Extended SQL keyword fallback for definition/factoid/point/year patterns to recover exact clause chunks when semantic candidates are weak.
- Added FIFO KB-query queue and in-order processing in bot search mode.
- Added ephemeral KB-search progress message with automatic cleanup after answer.
- Ensured replies are sent under original user questions via `reply_to_message_id`.
- Added CLI smoke runner for `/api/v1/rag/query`.
- Simplified RU RAG prompt template to reduce noisy boilerplate and improve direct factual output quality.

## Verification
- `python -m py_compile backend/api/routes/rag.py frontend/bot_handlers.py frontend/bot_callbacks.py shared/utils.py scripts/rag_api_smoke_test.py tests/test_rag_query_definition_intent.py tests/test_bot_text_ai_mode.py` -> PASS
- `.venv\Scripts\python.exe -m pytest -q tests/test_rag_query_definition_intent.py tests/test_bot_text_ai_mode.py tests/test_buttons_admin_menu.py tests/test_bot_document_upload.py` -> PASS
- `.venv\Scripts\python.exe scripts/rag_api_smoke_test.py --help` -> PASS
- `python scripts/scan_secrets.py` -> PASS
- `python scripts/ci_policy_gate.py --working-tree` -> PASS

## Residual Risk
- Retrieval still depends on heuristic intent detection; rare mixed-language or very short ambiguous questions may need additional tuning.
- Full infra migration to external vector DB is intentionally deferred to a separate rollout phase.
