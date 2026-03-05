# Review Report: RAG Factoid Hardening + KB Queue Session Reset

- Date: 2026-03-05
- Reviewer: codex-review
- Verdict: PASS

## Scope Reviewed
- `backend/api/routes/rag.py`
- `frontend/bot_handlers.py`
- `shared/utils.py`
- `tests/test_rag_query_definition_intent.py`
- `tests/test_bot_text_ai_mode.py`

## Findings
- Improved factoid/metric retrieval hints and scoring:
  - key phrases,
  - strict terms,
  - numeric evidence preference.
- Strengthened keyword SQL fallback with weak-candidate rejection for metric queries.
- Narrowed factoid context packing to top direct evidence chunks.
- Added KB query session reset/isolation to avoid stale queued answers from previous flows.
- Removed default "Дополнительно" prompt pattern in RU answer prompt.

## Verification
- `python -m py_compile backend/api/routes/rag.py frontend/bot_handlers.py shared/utils.py tests/test_bot_text_ai_mode.py tests/test_rag_query_definition_intent.py` -> PASS
- `.venv\Scripts\python.exe -m pytest -q tests/test_bot_text_ai_mode.py tests/test_rag_query_definition_intent.py` -> PASS
- `python scripts/scan_secrets.py` -> PASS
- `python scripts/ci_policy_gate.py --working-tree` -> PASS

## Residual Risk
- OCR/noisy PDFs with broken numeric tokens may still underperform and may require OCR-aware normalization in ingestion stage.
