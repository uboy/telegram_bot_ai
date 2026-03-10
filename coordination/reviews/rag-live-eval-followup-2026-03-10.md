# Review Report: RAG Live Eval Follow-up
- Date: 2026-03-10
- Scope: answer-eval defaulting to the main `.env` provider/model, optional eval KB pinning, storage-safe markdown fallback, and local-only live quality verification artifacts.
- Verdict: PASS

## Reviewed artifacts
- `backend/services/rag_eval_service.py`
- `scripts/rag_eval_baseline_runner.py`
- `shared/kb_settings.py`
- `shared/document_loaders/markdown_loader.py`
- `tests/test_rag_eval_service.py`
- `tests/test_rag_eval_baseline_runner.py`
- `tests/test_markdown_loader_chunking.py`
- `SPEC.md`
- `docs/TESTING.md`
- `docs/OPERATIONS.md`
- `docs/CONFIGURATION.md`
- `docs/REQUIREMENTS_TRACEABILITY.md`
- `env.template`

## Findings
None blocking.

Low risk note:
- `shared/document_loaders/markdown_loader.py` uses a fixed `storage_safe_full_max = 60000`. This is a pragmatic mitigation for the observed MySQL `TEXT` overflow and is covered by regression tests, but it remains a storage-policy constant rather than a schema-derived limit.

## Verification
- `python -m py_compile backend/services/rag_eval_service.py scripts/rag_eval_baseline_runner.py shared/kb_settings.py shared/document_loaders/markdown_loader.py tests/test_rag_eval_service.py tests/test_rag_eval_baseline_runner.py tests/test_markdown_loader_chunking.py` -> PASS
- `.venv\Scripts\python.exe -m pytest -q tests/test_rag_eval_service.py tests/test_rag_eval_baseline_runner.py tests/test_markdown_loader_chunking.py` -> PASS (`18 passed, 3 warnings`)
- Independent static review confirmed:
  - answer eval now defaults to `AI_DEFAULT_PROVIDER` + main provider model envs,
  - `RAG_EVAL_KB_ID` is validated and propagated through retrieval and answer paths,
  - oversized markdown `full` chunks now degrade to bounded splitting before DB write,
  - committed-safe artifacts/docs/tests do not introduce local corpus paths or unsanitized Ollama base URLs.

## Final verdict
- PASS

## Clarifications required
- None.
