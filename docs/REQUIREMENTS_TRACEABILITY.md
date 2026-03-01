# Requirements Traceability Matrix

Source: `SPEC.md` acceptance criteria.

| # | Requirement | Implementation Evidence | Verification Evidence | Status |
|---|---|---|---|---|
| AC-01 | User registration + non-admin approval flow | `backend/api/routes/auth.py`, `backend/api/routes/users.py`, `frontend/bot_handlers.py` | `tests/test_auth_and_users_routes.py` | PASS |
| AC-02 | Admin can create/list/clear/delete KB | `backend/api/routes/knowledge.py`, admin callbacks in `frontend/bot_callbacks.py` | `tests/test_api_routes_contract.py` | PASS |
| AC-03 | Admin can ingest docs/web/wiki/image/code | `backend/api/routes/ingestion.py`, `backend/services/ingestion_service.py` | `tests/test_ingestion_routes.py`, `tests/test_indexing_jobs_lifecycle.py` | PASS |
| AC-04 | RAG query returns answer + sources + metadata | `backend/api/routes/rag.py` (`/rag/query`) | `tests/test_api_routes_contract.py`, `tests/test_rag_quality.py` | PASS |
| AC-05 | Inline citations only from provided sources | `shared/rag_safety.py`, `backend/api/routes/rag.py` | `tests/test_rag_safety.py` | PASS |
| AC-06 | Command snippets filtered to KB context | `shared/rag_safety.py` | `tests/test_rag_safety.py` | PASS |
| AC-07 | Web search via DuckDuckGo with source links | `shared/web_search.py`, bot handlers | Manual flow; no dedicated automated test yet | PARTIAL |
| AC-08 | Backend enforces API key when configured | `backend/api/deps.py`, protected routers | `tests/test_security_api_key.py` | PASS |
| AC-09 | n8n receives `knowledge_import` events | `shared/n8n_client.py`, ingestion service hooks | Manual/ops verification; no dedicated automated test yet | PARTIAL |
| AC-10 | Docker Compose starts full stack | `docker-compose.yml`, `Dockerfile` | Manual ops verification | PARTIAL |
| AC-11 | AI providers (Ollama/OpenAI/Anthropic/DeepSeek/Open WebUI) configurable through unified provider manager | `shared/ai_providers.py`, `env.template`, `README.md` | `tests/test_ai_providers.py` | PASS |
| AC-12 | Feature/bugfix changes must include spec/design/traceability updates | `AGENTS.md`, `scripts/ci_policy_gate.py`, `.github/workflows/agent-quality-gates.yml` | CI policy gate execution | PASS |
| AC-13 | ASR results: technical metadata is hidden by default or toggleable by user | `shared/database.py`, `frontend/bot_callbacks.py`, `frontend/bot_handlers.py` | `tests/test_bot_voice.py` | PASS |
| AC-14 | ASR formatting: metadata is displayed as an expandable HTML block (`<blockquote expandable>`) | `frontend/bot_handlers.py` | `tests/test_bot_voice.py`, manual Telegram verification | PASS |

## Gaps to close

- Add automated test for web-search output contract.
- Add automated integration test for n8n webhook delivery.
- Add container smoke check for compose startup in CI.
