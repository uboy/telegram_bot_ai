# codex state
- date: 2026-03-01
- role: developer
- current_step: done (AI mode v2 implementation + verification)
- summary:
  - Added AI mode v2 data models in `shared/database.py`: `AIConversation`, `AIConversationTurn`, `AIRequestMetric`.
  - Added services: `shared/ai_metrics.py`, `shared/ai_conversation_service.py`, `shared/ai_prompt_policy.py`.
  - Instrumented `AIProviderManager.query/query_multimodal` with best-effort telemetry persistence.
  - Integrated direct AI UX: restore/new session flow, context-aware prompting, progress status for long calls, per-user in-flight guard.
  - Updated tests/docs/spec/traceability/config templates for new behavior.
  - Added compatibility helpers in `backend/services/asr_worker.py` to align with existing worker tests (`_ensure_ffmpeg_available`, `_get_worker_devices`, fallback transcription path).
- verification:
  - `python -m py_compile shared/config.py shared/database.py shared/ai_metrics.py shared/ai_conversation_service.py shared/ai_prompt_policy.py shared/ai_providers.py frontend/bot_handlers.py frontend/bot_callbacks.py frontend/templates/buttons.py tests/test_bot_text_ai_mode.py tests/test_bot_voice.py tests/test_bot_audio.py tests/test_ai_providers.py tests/test_ai_metrics.py`
  - `python -m pytest -q tests/test_bot_text_ai_mode.py tests/test_bot_voice.py tests/test_bot_audio.py tests/test_ai_providers.py tests/test_ai_metrics.py` -> `8 passed, 3 skipped`
  - `.venv\Scripts\python.exe -m pytest -q tests` -> `61 passed, 16 warnings`
  - `python scripts/scan_secrets.py` -> PASS

## 2026-03-04 Session Resume
- role: team-lead-orchestrator + developer
- task: fix admin KB creation flow (`waiting_kb_name`)
- classification: non-trivial (unknown root cause at intake; cross-file bugfix + regression test)
- progress:
  - Startup ritual completed: checked `coordination/tasks.jsonl` and this state file.
  - Reproduced root cause from code inspection:
    - state is set in `frontend/bot_callbacks.py` (`kb_create` -> `waiting_kb_name`),
    - state is not handled in `frontend/bot_handlers.py::handle_text`.
  - Drafted research/plan updates in `.scratchpad/research.md` and `.scratchpad/plan.md`.
  - Added checklist entries `KBBUG-001..KBBUG-004` to `coordination/tasks.jsonl`.
- next_step: implement handler branch + add regression test + run focused verification.

## 2026-03-04 Completion Snapshot
- implementation:
  - Added `waiting_kb_name` branch in `frontend/bot_handlers.py::handle_text`.
  - Branch calls backend create KB API and returns explicit success/failure message.
  - State reset to `None` after create attempt to return admin flow to stable menu.
- tests:
  - Added regression test `test_handle_text_waiting_kb_name_creates_knowledge_base` in `tests/test_bot_text_ai_mode.py`.
- verification:
  - `python -m py_compile frontend/bot_handlers.py tests/test_bot_text_ai_mode.py` -> PASS
  - `.venv\Scripts\python.exe -m pytest -q tests/test_bot_text_ai_mode.py` -> PASS (`5 passed`)
  - `python scripts/scan_secrets.py` -> PASS
- docs/spec:
  - Updated `SPEC.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, `docs/USAGE.md`.
  - Added design note `docs/design/kb-create-state-machine-fix-v1.md`.
  - Added cycle/review artifacts in `coordination/`.
- notes:
  - Validation scripts referenced by policy were not found in repo:
    - `scripts/validate-review-report.ps1`
    - `scripts/validate-cycle-proof.ps1`

## 2026-03-04 New Task: Auto Document Upload
- task: improve KB document upload UX/logic
- classification: non-trivial
- findings:
  - `kb_upload` currently forces explicit `document_type_menu`.
  - `handle_document` does not run backend ingestion and does not produce upload report.
  - Callback path still imports missing `load_document_to_kb`, so pending-doc flow is inconsistent.
  - Backend ingestion already supports async per-file jobs and status polling (`/ingestion/document`, `/jobs/{id}`).
- plan:
  - remove explicit type menu step for document uploads,
  - add auto type inference and batch report flow with Telegram size guard,
  - support pending list before KB selection,
  - add tests and docs updates.
- current_step: implementation (DOCUPL-002 in progress).

## 2026-03-04 Auto Upload Completion Snapshot
- implementation:
  - `kb_upload` switched to direct file upload mode without type preselection.
  - Added auto file-type inference and Telegram size-limit validation in bot handler.
  - Added pending documents queue (list) for cases when KB is selected later.
  - Added media-group aggregation and batch processing with consolidated report.
  - Backend ingestion now infers `file_type` from filename when type is omitted.
- tests:
  - Added `tests/test_bot_document_upload.py` (inference/report/pending/batch behavior).
- verification:
  - `python -m py_compile frontend/bot_handlers.py frontend/bot_callbacks.py backend/services/ingestion_service.py tests/test_bot_document_upload.py` -> PASS
  - `.venv\Scripts\python.exe -m pytest -q tests/test_bot_document_upload.py tests/test_bot_text_ai_mode.py` -> PASS (`9 passed`)
  - `python scripts/scan_secrets.py` -> PASS
- docs/spec:
  - Updated `SPEC.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, `docs/USAGE.md`.
  - Added design doc `docs/design/kb-auto-document-upload-v1.md`.
  - Added review artifact `coordination/reviews/doc-upload-auto-detect-2026-03-04.md`.

## 2026-03-04 Extra Test + Incident Analysis
- user feedback:
  - document looked unprocessed in previous bot version with no visible error.
- log analysis:
  - provided backend logs contain no `/api/v1/ingestion/document` calls, so ingestion was not triggered from frontend in that run.
- additional hardening:
  - added diagnostic upload logs in `frontend/bot_handlers.py` for inferred type, backend response/job_id, and final batch status.
- extra regression tests added:
  - ingestion call is actually made (`test_ingest_single_document_payload_calls_backend_ingestion`),
  - missing `job_id` now yields explicit failed result (`test_ingest_single_document_payload_reports_missing_job_id`).
- latest verification:
  - `python -m py_compile frontend/bot_handlers.py tests/test_bot_document_upload.py` -> PASS
  - `.venv\Scripts\python.exe -m pytest -q tests/test_bot_document_upload.py` -> PASS (`6 passed`)
  - `.venv\Scripts\python.exe -m pytest -q tests/test_bot_document_upload.py tests/test_bot_text_ai_mode.py` -> PASS (`11 passed`)
  - `python scripts/scan_secrets.py` -> PASS

## 2026-03-04 Runtime Follow-up
- observed from user logs:
  - ingestion endpoint + job polling are now called correctly;
  - report still showed `total_chunks=0`.
- root cause:
  - async `/ingestion/document` launch response always returns `total_chunks=0` placeholder.
- fix:
  - after `job completed`, upload handler reads actual chunk count from KB import log (`source_path == file_name`).
  - normalized mojibake `??` labels in KB settings/code buttons.
- follow-up verification:
  - `python -m py_compile frontend/bot_handlers.py frontend/bot_callbacks.py frontend/templates/buttons.py tests/test_bot_document_upload.py` -> PASS
  - `.venv\Scripts\python.exe -m pytest -q tests/test_bot_document_upload.py tests/test_bot_text_ai_mode.py` -> PASS (`11 passed`)
  - `python scripts/scan_secrets.py` -> PASS

## 2026-03-04 RAG + Navigation Follow-up
- user ask:
  - analyze why definition-style questions get weak answers,
  - remove duplicate admin upload button/flow.
- implementation:
  - added `DEFINITION` intent + ranking boosts in `backend/api/routes/rag.py`.
  - added `пункт N` boost for question patterns with explicit point references.
  - removed global `admin_upload` button from `admin_menu`.
  - kept `admin_upload` callback as compatibility redirect to KB list.
- tests:
  - added `tests/test_rag_query_definition_intent.py`.
  - added `tests/test_buttons_admin_menu.py`.
- verification:
  - `python -m py_compile backend/api/routes/rag.py frontend/templates/buttons.py frontend/bot_callbacks.py tests/test_rag_query_definition_intent.py tests/test_buttons_admin_menu.py` -> PASS
  - `.venv\Scripts\python.exe -m pytest -q tests/test_rag_query_definition_intent.py tests/test_buttons_admin_menu.py tests/test_bot_document_upload.py tests/test_bot_text_ai_mode.py` -> PASS (`13 passed`)
  - `python scripts/scan_secrets.py` -> PASS

## 2026-03-04 RAG Clause Retrieval Follow-up
- user evidence:
  - definition/point queries still returned "контекст не найден" despite source PDF containing target sections.
- root cause:
  - semantic + rerank candidates can miss exact numeric clauses (`25.`, `26.`) and exact glossary definitions in some runs.
- implementation:
  - added query hints extraction (`definition_term`, `point_numbers`) in `backend/api/routes/rag.py`.
  - added SQL keyword fallback candidates from `knowledge_chunks` for:
    - definition term exact matches,
    - `пункт N` and numeric section markers (`N.`).
  - strengthened ranking boosts for exact definition term and numbered clause markers.
  - made rerank threshold guard apply only when rerank scores are actually present.
- tests:
  - extended `tests/test_rag_query_definition_intent.py` with
    `test_rag_query_point_uses_keyword_fallback_chunk`.
- verification:
  - `python -m py_compile backend/api/routes/rag.py tests/test_rag_query_definition_intent.py` -> PASS
  - `.venv\Scripts\python.exe -m pytest -q tests/test_rag_query_definition_intent.py tests/test_buttons_admin_menu.py tests/test_bot_document_upload.py tests/test_bot_text_ai_mode.py` -> PASS (`14 passed`)
  - `python scripts/scan_secrets.py` -> PASS

## 2026-03-04 Full RAG Stack Migration Kickoff
- classification: non-trivial
- startup ritual:
  - re-read `coordination/tasks.jsonl` and `coordination/state/codex.md`
  - synchronized last completed checkpoints
- user request:
  - full RAG stack quality upgrade for multi-source ingestion (pdf/docx/markdown/wiki/code/web/image/chat)
  - add KB query progress indicator (auto-clean)
  - add KB query queue with ordered replies under each original user message
  - add backend API testing script
- findings:
  - ingestion surface already broad and suitable for stack-v2 retrieval upgrades without source-format regressions
  - retrieval quality gaps remain for factual/legal numeric questions
  - bot KB-search currently lacks queue worker/progress UX
- orchestration artifacts updated:
  - `coordination/tasks.jsonl` with `RAGSTACK-001..004`
  - `coordination/cycle-contract.json` switched to `RAGSTACK-001`
  - `.scratchpad/research.md` and `.scratchpad/plan.md` updated with migration design/checklists
- in-progress implementation snapshot:
  - `frontend/bot_handlers.py` has initial queue/progress scaffolding edits in working tree, not yet integrated with callbacks/tests.
- next step:
  - finish code-level stack-v2 integration (`rag.py` + `bot_callbacks.py`) and add regression tests + API smoke script.

## 2026-03-04 RAG Stack v2 Completion Snapshot
- classification: non-trivial
- implementation:
  - retrieval v2 in `backend/api/routes/rag.py`:
    - added `FACTOID` intent,
    - extended query hints (`fact_terms`, `year_tokens`),
    - strengthened factual ranking boosts,
    - widened keyword fallback for legal/numeric/year queries,
    - tuned context assembly for factoid responses.
  - KB search UX v2 in `frontend/bot_handlers.py` + `frontend/bot_callbacks.py`:
    - FIFO queue worker for multiple KB questions,
    - per-question `reply_to_message_id` answer routing,
    - temporary progress bar during long KB requests with auto-delete cleanup,
    - pending query flush when KB is selected.
  - improved upload diagnostics in `frontend/bot_handlers.py`:
    - chunk-count lookup now falls back to KB sources when import-log entry is zero/ambiguous.
  - prompt cleanup in `shared/utils.py`:
    - RU RAG answer prompt now avoids noisy "Основной ответ/Дополнительно найдено" template output.
  - added backend smoke runner:
    - `scripts/rag_api_smoke_test.py`.
- tests:
  - added/extended:
    - `tests/test_rag_query_definition_intent.py` (factoid + metric/year fallback),
    - `tests/test_bot_text_ai_mode.py` (queue/progress/pending flush coverage).
- verification:
  - `python -m py_compile backend/api/routes/rag.py frontend/bot_handlers.py frontend/bot_callbacks.py shared/utils.py scripts/rag_api_smoke_test.py tests/test_rag_query_definition_intent.py tests/test_bot_text_ai_mode.py` -> PASS
  - `.venv\Scripts\python.exe -m pytest -q tests/test_rag_query_definition_intent.py tests/test_bot_text_ai_mode.py tests/test_buttons_admin_menu.py tests/test_bot_document_upload.py` -> PASS
  - `.venv\Scripts\python.exe scripts/rag_api_smoke_test.py --help` -> PASS
  - `python scripts/scan_secrets.py` -> PASS
  - `python scripts/ci_policy_gate.py --working-tree` -> PASS
- docs/spec/review:
  - added `docs/design/rag-stack-v2-migration-v1.md`,
  - updated `SPEC.md`,
  - updated `docs/REQUIREMENTS_TRACEABILITY.md`,
  - updated `docs/USAGE.md`,
  - added `coordination/reviews/rag-stack-v2-migration-2026-03-04.md`.

## 2026-03-04 Max Quality Architecture Kickoff
- classification: non-trivial
- mode: architect-only (design-first, no implementation changes)
- user request:
  - "сделать максимальное качество"
  - выполнить процесс: исследование -> дизайн архитектуры -> декомпозиция -> реализация/ревью/тесты
- actions completed:
  - reviewed current retrieval/indexing/quality baseline (`shared/rag_system.py`, loaders, eval tests).
  - documented new research section in `.scratchpad/research.md`.
  - documented process plan section in `.scratchpad/plan.md`.
  - created architecture spec `docs/design/rag-max-quality-architecture-v1.md`.
  - opened new cycle tasks `RAGMAX-001..004` in `coordination/tasks.jsonl`.
  - switched cycle contract to design stage (`coordination/cycle-contract.json`).
- next step:
  - wait for design approval token: `APPROVED:v1` or change request.

## 2026-03-04 Max Quality Architecture Revision (User CHANGES)
- user change request:
  - "нужно сейчас получить максимум качества, даже если для этого нужно поменять стек полностью"
- applied updates:
  - revised design doc now mandates full stack replacement (external hybrid retrieval backend) in this cycle.
  - removed optionality framing from research/plan artifacts.
  - preserved rollback window via feature switch for safety.
- next step:
  - await renewed design approval on revised v1 spec, then begin implementation phase (`RAGMAX-002`).

## 2026-03-04 Max Quality Implementation Snapshot (Qdrant + Diagnostics)
- task: RAGMAX-002/003/004
- classification: non-trivial
- implementation:
  - Added Qdrant dense retrieval adapter: `shared/qdrant_backend.py`.
  - Optimized adapter to cache ensured vector size and avoid redundant collection-check requests during batch upsert.
  - Added config switches and env/compose wiring:
    - `RAG_BACKEND`, `QDRANT_URL`, `QDRANT_API_KEY`, `QDRANT_COLLECTION`, `QDRANT_TIMEOUT_SEC`,
    - qdrant service in `docker-compose.yml`.
  - Integrated hybrid retrieval path in `shared/rag_system.py`:
    - qdrant dense channel + existing lexical BM25 channel,
    - CRUD sync hooks (`add`, `batch`, `delete`, `clear`, `delete_kb`),
    - legacy fallback path preserved.
  - Added retrieval diagnostics persistence:
    - DB models in `shared/database.py` (`retrieval_query_logs`, `retrieval_candidate_logs`).
  - Updated RAG API:
    - `/api/v1/rag/query` now always returns `request_id`,
    - added `GET /api/v1/rag/diagnostics/{request_id}` in `backend/api/routes/rag.py`.
  - Added schemas for diagnostics in `backend/schemas/rag.py`.
- tests added:
  - `tests/test_qdrant_backend.py`
  - `tests/test_rag_diagnostics.py`
- verification:
  - `python -m py_compile backend/api/routes/rag.py tests/test_rag_diagnostics.py tests/test_qdrant_backend.py` -> PASS
  - `python -m py_compile shared/qdrant_backend.py shared/rag_system.py shared/config.py shared/database.py backend/api/routes/rag.py backend/schemas/rag.py tests/test_qdrant_backend.py tests/test_rag_diagnostics.py` -> PASS
  - `.venv\Scripts\python.exe -m pytest -q tests/test_rag_query_definition_intent.py tests/test_rag_diagnostics.py tests/test_qdrant_backend.py` -> PASS (`9 passed`)
  - `.venv\Scripts\python.exe -m pytest -q tests/test_rag_query_definition_intent.py tests/test_rag_diagnostics.py tests/test_qdrant_backend.py tests/test_bot_text_ai_mode.py tests/test_bot_document_upload.py tests/test_buttons_admin_menu.py` -> PASS (`25 passed`)
  - `.venv\Scripts\python.exe scripts/rag_api_smoke_test.py --help` -> PASS
  - `python scripts/scan_secrets.py` -> PASS
  - `python scripts/ci_policy_gate.py --working-tree` -> PASS
- docs/spec:
  - Updated `SPEC.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, `docs/USAGE.md`, `docs/OPERATIONS.md`.
  - Added review artifact: `coordination/reviews/rag-max-quality-qdrant-diagnostics-2026-03-04.md`.

## 2026-03-05 Generalized RAG Design Hardening (v2)
- task: RAGGEN-001..004
- classification: non-trivial (design/review cycle)
- user ask:
  - prepare detailed RAG redesign without query-overfitting,
  - include deep analysis conclusions,
  - run independent review and identify weak points.
- implementation in this cycle:
  - Added hardened architecture spec `docs/design/rag-generalized-architecture-v2.md`.
  - Closed all previous MUST-FIX areas in design:
    - capacity/sizing model,
    - retention and PII lifecycle,
    - outbox idempotent dual-write consistency,
    - statistical quality gates,
    - failure-domain/degraded-mode contract,
    - parser/model/index epoch governance.
  - Added independent review artifact `coordination/reviews/rag-generalized-architecture-v2-review-2026-03-05.md` with PASS verdict and residual SHOULD-FIX notes.
  - Synchronized `coordination/tasks.jsonl` and `coordination/cycle-contract.json` for this design cycle.
- verification:
  - design/review artifact presence confirmed via file checks.
  - no code build/test executed (docs-only cycle).
- next step:
  - wait for user approval token on new design revision and proceed to implementation planning if approved.

## 2026-03-05 RAG Outbox Foundation (Phase A)
- task: RAGOUT-001..004
- classification: non-trivial (implementation + review cycle)
- user instruction:
  - continue process after approved architecture track.
- implementation:
  - extended `shared/database.py` with Phase A primitives:
    - `index_outbox_events`,
    - `index_sync_audit`,
    - `rag_eval_runs`,
    - `rag_eval_results`,
    - `retention_deletion_audit`.
  - extended retrieval diagnostics schema:
    - `retrieval_query_logs`: `degraded_mode`, `degraded_reason`,
    - `retrieval_candidate_logs`: `channel`, `channel_rank`, `fusion_rank`, `fusion_score`, `rerank_delta`.
  - added outbox lifecycle service `shared/index_outbox_service.py`:
    - idempotent enqueue,
    - claim pending,
    - mark processed/failed/dead,
    - pending count helper.
  - integrated ingestion hooks in `backend/services/ingestion_service.py`:
    - outbox event enqueue after successful non-empty upserts for web/document/chat/archive/code/image flows.
  - added tests:
    - `tests/test_index_outbox_service.py`,
    - `tests/test_ingestion_outbox.py`.
  - updated design/spec/traceability:
    - `docs/design/rag-generalized-architecture-v2.md` (implementation snapshot),
    - `SPEC.md`,
    - `docs/REQUIREMENTS_TRACEABILITY.md`.
  - added review artifact:
    - `coordination/reviews/rag-outbox-phase-a-2026-03-05.md` (PASS).
- verification:
  - `python -m py_compile shared/database.py shared/index_outbox_service.py backend/services/ingestion_service.py tests/test_index_outbox_service.py tests/test_ingestion_outbox.py` -> PASS
  - `$env:MYSQL_URL=''; $env:DB_PATH='data/test_bot_database.db'; .venv\Scripts\python.exe -m pytest -q tests/test_index_outbox_service.py tests/test_ingestion_outbox.py tests/test_rag_diagnostics.py tests/test_ingestion_routes.py tests/test_indexing_jobs_lifecycle.py` -> PASS (`12 passed`)
  - `python scripts/scan_secrets.py` -> PASS
  - `python scripts/ci_policy_gate.py --working-tree` -> PASS
- residual risk:
  - no live outbox consumer worker yet (planned for next phase).
- next step:
  - implement outbox consumer with retry/backoff metrics and drift audit jobs.

## 2026-03-05 Phase A checklist audit and corrections
- task: RAGOUT-005..006
- classification: non-trivial (review + corrective patch)
- findings:
  - no file deletions detected in working tree (`git diff --name-status` contains no `D` entries).
  - identified two miswired outbox invocations in `backend/services/ingestion_service.py`:
    - incorrect payload in `ingest_web_page`,
    - invalid final event parameters in `ingest_codebase_path`.
- fixes applied:
  - corrected web/codebase outbox payload wiring,
  - added outbox events for `ingest_wiki_crawl`, `ingest_wiki_git`, `ingest_wiki_zip`,
  - added regression tests:
    - `tests/test_ingestion_outbox.py::test_ingest_web_page_emits_web_outbox_event`
    - `tests/test_ingestion_outbox.py::test_ingest_codebase_path_emits_code_and_codebase_events`
- verification:
  - `$env:MYSQL_URL=''; $env:DB_PATH='data/test_bot_database.db'; .venv\Scripts\python.exe -m pytest -q tests/test_ingestion_outbox.py tests/test_index_outbox_service.py tests/test_rag_diagnostics.py tests/test_ingestion_routes.py tests/test_indexing_jobs_lifecycle.py` -> `14 passed`
  - `$env:MYSQL_URL=''; $env:DB_PATH='data/test_bot_database.db'; .venv\Scripts\python.exe -m pytest -q tests/test_bot_text_ai_mode.py tests/test_rag_query_definition_intent.py tests/test_bot_document_upload.py` -> `22 passed`
  - `python scripts/scan_secrets.py` -> PASS
  - `python scripts/ci_policy_gate.py --working-tree` -> PASS
- next step:
  - proceed with Phase B (outbox consumer worker + drift audit job + degraded-mode signaling in API path).

## 2026-03-05 Phase B completion snapshot
- task: RAGOUT-007..010
- classification: non-trivial (implementation + review cycle)
- implementation:
  - Added async outbox consumer worker `backend/services/index_outbox_worker.py`:
    - pending claim processing loop,
    - UPSERT/DELETE_SOURCE/DELETE_KB handling,
    - retry/backoff and dead-letter transitions.
  - Wired worker startup in backend app (`backend/app.py`).
  - Extended Qdrant adapter with count API (`shared/qdrant_backend.py::count_points`) for drift checks.
  - Added periodic drift audit (`index_sync_audit`) between SQL active embedding chunks and Qdrant point counts.
  - Extended RAG diagnostics persistence/response:
    - query log now stores `degraded_mode`, `degraded_reason`,
    - candidate logs/response include `channel`, `channel_rank`, `fusion_rank`, `fusion_score`, `rerank_delta`.
  - Added Phase B config/env knobs in `shared/config.py` and `env.template`.
- tests:
  - Added `tests/test_index_outbox_worker.py`.
  - Extended `tests/test_qdrant_backend.py` (count API).
  - Extended `tests/test_rag_diagnostics.py` (degraded + channel/fusion fields).
- verification:
  - `python -m py_compile backend/services/index_outbox_worker.py backend/api/routes/rag.py backend/schemas/rag.py shared/qdrant_backend.py tests/test_qdrant_backend.py tests/test_rag_diagnostics.py tests/test_index_outbox_worker.py` -> PASS
  - `$env:MYSQL_URL=''; $env:DB_PATH='data/test_bot_database.db'; .venv\Scripts\python.exe -m pytest -q tests/test_qdrant_backend.py tests/test_rag_diagnostics.py tests/test_index_outbox_worker.py` -> PASS (`12 passed`)
  - `$env:MYSQL_URL=''; $env:DB_PATH='data/test_bot_database.db'; .venv\Scripts\python.exe -m pytest -q tests/test_api_routes_contract.py tests/test_security_api_key.py tests/test_ingestion_outbox.py tests/test_index_outbox_service.py tests/test_indexing_jobs_lifecycle.py` -> PASS (`12 passed`)
  - `python scripts/scan_secrets.py` -> PASS
  - `python scripts/ci_policy_gate.py --working-tree` -> PASS
- docs/spec:
  - Updated `SPEC.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, `docs/design/rag-generalized-architecture-v2.md`.
  - Updated `docs/OPERATIONS.md`, `docs/API_REFERENCE.md`, `docs/CONFIGURATION.md`, `docs/USAGE.md`.
  - Added review artifact `coordination/reviews/rag-outbox-phase-b-2026-03-05.md`.
- next step:
  - move to next architecture phase (retention worker + eval orchestration) after user confirmation.

## 2026-03-05 Phase C part-1 completion snapshot
- task: RAGOUT-011..014
- classification: non-trivial (implementation + review cycle)
- implementation:
  - Added retention lifecycle service `backend/services/rag_retention_service.py`:
    - scheduled purge for retrieval logs,
    - old document versions/chunks cleanup,
    - eval artifacts and drift audit retention cleanup,
    - per-policy audit entries in `retention_deletion_audit`.
  - Added eval orchestration service `backend/services/rag_eval_service.py`:
    - run creation and lifecycle state (`queued/running/completed/failed`),
    - persisted slice metrics in `rag_eval_results`.
  - Extended RAG API:
    - `POST /api/v1/rag/eval/run`
    - `GET /api/v1/rag/eval/{run_id}`
  - Integrated retention loop into background worker in `backend/services/index_outbox_worker.py`.
  - Added config/env knobs for retention/eval in `shared/config.py` and `env.template`.
- tests:
  - added `tests/test_rag_retention_service.py`
  - added `tests/test_rag_eval_service.py`
  - added `tests/test_rag_eval_api.py`
  - updated `tests/test_api_routes_contract.py`
  - updated `tests/test_index_outbox_worker.py`
- verification:
  - `python -m py_compile backend/services/rag_retention_service.py backend/services/rag_eval_service.py backend/services/index_outbox_worker.py backend/api/routes/rag.py backend/schemas/rag.py shared/config.py tests/test_api_routes_contract.py tests/test_rag_eval_api.py tests/test_rag_eval_service.py tests/test_rag_retention_service.py tests/test_index_outbox_worker.py` -> PASS
  - `$env:MYSQL_URL=''; $env:DB_PATH='data/test_bot_database.db'; .venv\Scripts\python.exe -m pytest -q tests/test_rag_eval_api.py tests/test_rag_eval_service.py tests/test_rag_retention_service.py tests/test_index_outbox_worker.py tests/test_api_routes_contract.py tests/test_rag_diagnostics.py` -> PASS (`17 passed`)
  - `$env:MYSQL_URL=''; $env:DB_PATH='data/test_bot_database.db'; .venv\Scripts\python.exe -m pytest -q tests/test_index_outbox_service.py tests/test_ingestion_outbox.py tests/test_indexing_jobs_lifecycle.py tests/test_qdrant_backend.py` -> PASS (`13 passed`)
  - `python scripts/scan_secrets.py` -> PASS
  - `python scripts/ci_policy_gate.py --working-tree` -> PASS
- docs/spec:
  - updated `SPEC.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, `docs/design/rag-generalized-architecture-v2.md`
  - updated `docs/API_REFERENCE.md`, `docs/CONFIGURATION.md`, `docs/OPERATIONS.md`, `docs/USAGE.md`
  - added review report `coordination/reviews/rag-outbox-phase-c1-2026-03-05.md`
- next step:
  - implement statistical CI quality gate wiring and richer generation-faithfulness eval metrics.

## 2026-03-05 Phase C part-2 completion snapshot
- task: RAGOUT-015..018
- classification: non-trivial (implementation + review cycle)
- implementation:
  - Added statistical quality gate CLI script `scripts/rag_eval_quality_gate.py`:
    - required slices/metrics enforcement,
    - thresholds and minimum sample-size checks,
    - baseline delta non-regression check,
    - bootstrap 95% CI lower-bound check against configurable negative margin,
    - JSON report output options.
  - Extended eval persistence in `backend/services/rag_eval_service.py` to store per-result `sample_size`, `suite_name`, and metric `values` arrays in `details_json` for CI bootstrap evaluation.
  - Added tests `tests/test_rag_eval_quality_gate.py` (bootstrap deterministic behavior, pass/fail gate scenarios).
  - Hardened gate script import behavior with lazy DB model loading so unit tests can import module without immediate DB connection bootstrap.
- verification:
  - `python -m py_compile scripts/rag_eval_quality_gate.py backend/services/rag_eval_service.py tests/test_rag_eval_quality_gate.py` -> PASS
  - `.venv\Scripts\python.exe -m pytest -q tests/test_rag_eval_quality_gate.py` -> PASS (`3 passed`)
  - `$env:MYSQL_URL=''; $env:DB_PATH='data/test_bot_database.db'; .venv\Scripts\python.exe -m pytest -q tests/test_rag_eval_quality_gate.py tests/test_rag_eval_service.py tests/test_rag_eval_api.py tests/test_api_routes_contract.py` -> PASS (`9 passed`)
  - `python scripts/scan_secrets.py` -> PASS
  - `python scripts/ci_policy_gate.py --working-tree` -> PASS
- docs/spec:
  - Updated `SPEC.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, `docs/design/rag-generalized-architecture-v2.md`.
  - Updated `docs/OPERATIONS.md`, `docs/TESTING.md`, `docs/USAGE.md`.
  - Added review report `coordination/reviews/rag-outbox-phase-c2-2026-03-05.md`.
- notes:
  - Focused pytest requiring backend imports should be run with test DB overrides in this shell (`MYSQL_URL=''`, `DB_PATH=data/test_bot_database.db`) to avoid accidental external MySQL dependency during collection.

## 2026-03-05 Phase D kickoff snapshot (feature-flag orchestrator cutover)
- task: RAGOUT-019..022
- classification: non-trivial (implementation + review cycle)
- implementation:
  - Added `RAG_ORCHESTRATOR_V4` in `shared/config.py` and `env.template`.
  - Updated `backend/api/routes/rag.py`:
    - `RAG_ORCHESTRATOR_V4=true` => disable route-level query-specific boosts/keyword fallback,
    - rank by base retrieval score only,
    - keep rollback path via `RAG_ORCHESTRATOR_V4=false`.
  - Added v4 regression coverage in `tests/test_rag_query_definition_intent.py`:
    - `test_rag_query_orchestrator_v4_disables_definition_boosts`,
    - `test_rag_query_orchestrator_v4_disables_keyword_fallback`.
- verification:
  - `python -m py_compile backend/api/routes/rag.py shared/config.py tests/test_rag_query_definition_intent.py` -> PASS
  - `$env:MYSQL_URL=''; $env:DB_PATH='data/test_bot_database.db'; .venv\Scripts\python.exe -m pytest -q tests/test_rag_query_definition_intent.py` -> PASS (`8 passed`)
  - `$env:MYSQL_URL=''; $env:DB_PATH='data/test_bot_database.db'; .venv\Scripts\python.exe -m pytest -q tests/test_rag_query_definition_intent.py tests/test_rag_eval_quality_gate.py tests/test_rag_eval_service.py tests/test_rag_eval_api.py tests/test_api_routes_contract.py` -> PASS (`17 passed`)
  - `python scripts/scan_secrets.py` -> PASS
  - `python scripts/ci_policy_gate.py --working-tree` -> PASS
- docs/spec:
  - Updated `SPEC.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, `docs/design/rag-generalized-architecture-v2.md`.
  - Updated `docs/OPERATIONS.md`, `docs/CONFIGURATION.md`, `docs/USAGE.md`.
  - Added review report `coordination/reviews/rag-outbox-phase-d1-2026-03-05.md`.
- next step:
  - run comparative eval (`legacy` vs `RAG_ORCHESTRATOR_V4=true`) and decide production default cutover.

## 2026-03-05 Phase D observability + compare tooling snapshot
- task: RAGOUT-023..026
- classification: non-trivial (implementation + review cycle)
- implementation:
  - Added request-level `orchestrator_mode` diagnostics marker (`legacy`/`v4`) in RAG diagnostics response.
  - Persistence wiring: retrieval logs embed orchestrator mode in `hints_json` for historical triage.
  - Added real-API comparison utility `scripts/rag_orchestrator_compare.py`:
    - runs shared case suite against legacy and v4 backends,
    - computes summary deltas (`source_hit_rate`, `non_empty_rate`, `snippet_hit_rate`),
    - supports optional fail gate (`--max-source-hit-drop`),
    - supports JSON report output.
- tests:
  - Added `tests/test_rag_orchestrator_compare.py`.
  - Updated `tests/test_rag_diagnostics.py` for `orchestrator_mode` contract.
- verification:
  - `python -m py_compile backend/api/routes/rag.py backend/schemas/rag.py scripts/rag_orchestrator_compare.py tests/test_rag_diagnostics.py tests/test_rag_orchestrator_compare.py` -> PASS
  - `$env:MYSQL_URL=''; $env:DB_PATH='data/test_bot_database.db'; .venv\Scripts\python.exe -m pytest -q tests/test_rag_diagnostics.py tests/test_rag_orchestrator_compare.py tests/test_rag_query_definition_intent.py tests/test_rag_eval_quality_gate.py tests/test_rag_eval_service.py tests/test_rag_eval_api.py tests/test_api_routes_contract.py` -> PASS (`24 passed`)
  - `python scripts/scan_secrets.py` -> PASS
  - `python scripts/ci_policy_gate.py --working-tree` -> PASS
- docs/spec:
  - Updated `SPEC.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, `docs/API_REFERENCE.md`, `docs/TESTING.md`, `docs/OPERATIONS.md`, `docs/USAGE.md`, `docs/design/rag-generalized-architecture-v2.md`.
  - Added review report `coordination/reviews/rag-outbox-phase-d2-2026-03-05.md`.
- next step:
  - execute comparator on target host with real API for legacy/v4 and decide production cutover policy threshold.

## 2026-03-05 Phase D wrapper script snapshot
- task: RAGOUT-027..029
- implementation:
  - added host-ready wrapper `scripts/run_rag_compare_stack.sh` for one-command compare run in default docker stack,
  - wrapper starts temporary v4 backend, waits for health, runs comparator from legacy container, writes report to mounted `/app/data`.
- docs:
  - updated `docs/USAGE.md`, `docs/OPERATIONS.md`, `docs/TESTING.md` with wrapper command examples.
- verification:
  - `python -m py_compile scripts/rag_orchestrator_compare.py tests/test_rag_orchestrator_compare.py` -> PASS
  - `python scripts/scan_secrets.py` -> PASS
  - `python scripts/ci_policy_gate.py --working-tree` -> PASS
- review:
  - added `coordination/reviews/rag-outbox-phase-d3-2026-03-05.md`.
- next step:
  - user executes `bash scripts/run_rag_compare_stack.sh` on target host and shares report for cutover decision.

## 2026-03-06 RAG AS-IS analysis snapshot
- task:
  - сформировать детальное описание текущего RAG алгоритма и сохранить анализ как TODO в задачах проекта.
- artifacts:
  - added `docs/design/rag-current-algorithm-as-is-v1.md`:
    - технологический стек RAG,
    - ingestion pipeline по типам источников,
    - chunking/metadata/versioning/storage flow,
    - retrieval algorithm (`rag_system.search`) и route orchestration (`/rag/query`, legacy/v4),
    - diagnostics/eval/retention/outbox контур.
  - updated `coordination/tasks.jsonl`:
    - added `RAGAN-001` со статусом `todo` для формального закрепления анализа и следующего review цикла.
- notes:
  - изменения только в документации/координационных артефактах, без изменений runtime-кода.

## 2026-03-06 New Task: Wiki URL crawl flow broken
- role: team-lead-orchestrator + architect
- user report:
  - "сломалась \"собрать вики по URL\" функция. когда будешь исправлять, учти чтобы другие функции не сломались"
- classification: non-trivial (bug report with unknown root cause at intake + stateful bot flow + regression-protection requirement)
- startup ritual:
  - re-read `coordination/tasks.jsonl` and `coordination/state/codex.md`.
- findings so far:
  - `kb_wiki_crawl` callback sets `context.user_data['state'] = 'waiting_wiki_root'` in `frontend/bot_callbacks.py`.
  - `frontend/bot_handlers.py::handle_text` has no branch for `waiting_wiki_root`.
  - no `wiki_urls` producer found in current code, so `wiki_git_load/wiki_zip_load` callbacks cannot be reached through normal state flow.
- next_step:
  - produce focused fix design, then implement minimal-safe branch for wiki URL text state + add regression test and docs/traceability updates.

## 2026-03-06 Wiki URL crawl bugfix completion snapshot
- implementation:
  - Added `waiting_wiki_root` branch in `frontend/bot_handlers.py::handle_text`.
  - Branch validates KB selection and URL shape, calls `backend_client.ingest_wiki_crawl`, returns explicit crawl stats, and clears `state` + `kb_id_for_wiki`.
- regression tests:
  - Added `tests/test_bot_text_ai_mode.py::test_handle_text_waiting_wiki_root_ingests_wiki_crawl`.
  - Pre-fix reproduction verified (test failed before handler branch implementation).
- verification:
  - `.venv\Scripts\python.exe -m pytest -q tests/test_bot_text_ai_mode.py -k waiting_wiki_root` -> FAIL (expected before fix)
  - `.venv\Scripts\python.exe -m pytest -q tests/test_bot_text_ai_mode.py tests/test_bot_document_upload.py` -> PASS (`17 passed`)
  - `python -m py_compile frontend/bot_handlers.py tests/test_bot_text_ai_mode.py` -> PASS
  - `python scripts/scan_secrets.py` -> PASS
- docs/spec/traceability:
  - Updated `SPEC.md` with explicit wiki URL stateful acceptance criteria.
  - Added design doc `docs/design/wiki-url-crawl-state-fix-v1.md`.
  - Updated `docs/REQUIREMENTS_TRACEABILITY.md` and `docs/USAGE.md`.
  - Added review artifact `coordination/reviews/wiki-url-crawl-state-fix-2026-03-06.md`.
- notes:
  - Attempted independent sub-agent review (`code-review-qa`) and architect planning (`agent-architect`) but task tool returned `ProviderModelNotFoundError`; completed with self-review fallback.

## 2026-03-06 Wiki recursive sync hardening snapshot (Gitee)
- trigger:
  - user reported that wiki ingestion call succeeds but does not recursively sync all pages/files.
- diagnosis:
  - runtime log showed `pages=1` for `gitee.com/.../wikis` despite larger wiki.
  - direct HTML inspection revealed static page has almost no recursive wiki links in `<a href>`; navigation is JS-heavy.
- implementation:
  - added host-scoped fallback in `shared/wiki_scraper.py`:
    - for `gitee.com` + `/wikis`, prefer `load_wiki_from_git` for full sync;
    - map `files_processed` to `pages_processed` for compatibility;
    - if fallback fails, continue legacy HTML-crawl path.
- tests:
  - added `tests/test_wiki_scraper.py`:
    - host detection test,
    - gitee crawl path uses git-loader fallback and bypasses HTML requests.
- verification:
  - `.venv\Scripts\python.exe -m pytest -q tests/test_wiki_scraper.py tests/test_bot_text_ai_mode.py -k wiki` -> PASS (`3 passed`)
  - `.venv\Scripts\python.exe -m pytest -q tests/test_wiki_scraper.py tests/test_bot_text_ai_mode.py tests/test_bot_document_upload.py` -> PASS (`19 passed`)
  - `python -m py_compile shared/wiki_scraper.py tests/test_wiki_scraper.py` -> PASS
  - `python scripts/scan_secrets.py` -> PASS
  - `python scripts/ci_policy_gate.py --working-tree` -> PASS
- docs/spec/review:
  - updated `SPEC.md`, `docs/REQUIREMENTS_TRACEABILITY.md`.
  - added `docs/design/wiki-url-crawl-state-fix-v2.md`.
  - updated review artifact `coordination/reviews/wiki-url-crawl-state-fix-2026-03-06.md`.

## 2026-03-06 Generalized RAG quality program planning snapshot
- user requirement:
  - produce full architecture/design plan with small-step decomposition,
    mandatory review and gate/test checks, and separate commit per step.
- documented:
  - added `docs/design/rag-general-quality-program-v1.md` with phased roadmap (P0..P5),
    mandatory workflow, and explicit rule: one completed step equals one atomic commit.
- coordination:
  - added backlog tasks `RAGQLTY-001..018` in `coordination/tasks.jsonl`.
- notes:
  - this update is planning/documentation only; no runtime code behavior changed.

## 2026-03-06 RAGQLTY-001 completion snapshot (P0-1)
- step:
  - define generalized RAG quality metrics contract and baseline gate semantics.
- artifacts:
  - added `docs/design/rag-quality-metrics-contract-v1.md`.
  - updated `docs/design/rag-general-quality-program-v1.md` with explicit P0-1 artifact link.
  - added review artifact `coordination/reviews/ragqlty-p0-1-metrics-contract-2026-03-06.md`.
  - marked `RAGQLTY-001` completed in `coordination/tasks.jsonl`.
- verification:
  - `python scripts/scan_secrets.py` -> PASS
  - `python scripts/ci_policy_gate.py --working-tree` -> PASS
- notes:
  - atomic documentation step only; runtime/API behavior unchanged.

## 2026-03-06 RAGQLTY-002 completion snapshot (P0-2)
- step:
  - add fixed ready-data eval corpus and enforce dataset contract.
- implementation:
  - added corpus: `tests/data/rag_eval_ready_data_v1.yaml`.
  - switched default eval suite path in `backend/services/rag_eval_service.py` to versioned ready-data corpus.
  - added contract test `tests/test_rag_eval_dataset_contract.py` (size/unique id/required fields/required slices).
- docs/spec:
  - added design note `docs/design/rag-eval-ready-corpus-v1.md`.
  - updated `docs/design/rag-general-quality-program-v1.md` with P0-2 artifact link.
  - updated `SPEC.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, `docs/USAGE.md`.
  - added review artifact `coordination/reviews/ragqlty-p0-2-ready-corpus-2026-03-06.md`.
- verification:
  - `python -m py_compile backend/services/rag_eval_service.py tests/test_rag_eval_dataset_contract.py` -> PASS
  - `.venv\\Scripts\\python.exe -m pytest -q tests/test_rag_eval_service.py tests/test_rag_eval_quality_gate.py tests/test_rag_eval_dataset_contract.py` -> PASS (`6 passed`)
  - `python scripts/scan_secrets.py` -> PASS
  - `python scripts/ci_policy_gate.py --working-tree` -> PASS

## 2026-03-06 RAGQLTY-003 completion snapshot (P0-3)
- step:
  - implement baseline eval runner and persist report artifacts.
- implementation:
  - added CLI runner `scripts/rag_eval_baseline_runner.py`.
  - runner writes timestamped JSON/Markdown reports and `latest.{json,md}` snapshots.
  - added tests `tests/test_rag_eval_baseline_runner.py`.
- docs/spec:
  - added design note `docs/design/rag-eval-baseline-runner-v1.md`.
  - updated `SPEC.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, `docs/USAGE.md`, `docs/OPERATIONS.md`.
  - updated quality program artifact map in `docs/design/rag-general-quality-program-v1.md`.
  - added review artifact `coordination/reviews/ragqlty-p0-3-baseline-runner-2026-03-06.md`.
- verification:
  - `python -m py_compile scripts/rag_eval_baseline_runner.py tests/test_rag_eval_baseline_runner.py backend/services/rag_eval_service.py` -> PASS
  - `.venv\\Scripts\\python.exe -m pytest -q tests/test_rag_eval_baseline_runner.py tests/test_rag_eval_service.py tests/test_rag_eval_quality_gate.py tests/test_rag_eval_dataset_contract.py` -> PASS (`8 passed`)
  - `python scripts/scan_secrets.py` -> PASS
  - `python scripts/ci_policy_gate.py --working-tree` -> PASS

## 2026-03-06 RAGQLTY-004 completion snapshot (P0-4)
- step:
  - integrate threshold-based quality gate into test workflow.
- implementation:
  - extended `scripts/rag_eval_quality_gate.py` with artifact mode (`--run-report-json`, `--baseline-report-json`).
  - kept DB mode (`--run-id`) for backward compatibility.
  - updated CI workflow `.github/workflows/agent-quality-gates.yml` to compile baseline runner and run eval-gate related tests.
  - extended `tests/test_rag_eval_quality_gate.py` with artifact-mode coverage.
- docs/spec:
  - added design note `docs/design/rag-eval-threshold-gate-workflow-v1.md`.
  - updated `SPEC.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, `docs/USAGE.md`, `docs/OPERATIONS.md`.
  - updated quality program artifact map in `docs/design/rag-general-quality-program-v1.md`.
  - added review artifact `coordination/reviews/ragqlty-p0-4-threshold-gate-workflow-2026-03-06.md`.
- verification:
  - `python -m py_compile scripts/rag_eval_quality_gate.py scripts/rag_eval_baseline_runner.py tests/test_rag_eval_quality_gate.py` -> PASS
  - `.venv\\Scripts\\python.exe -m pytest -q tests/test_rag_eval_quality_gate.py tests/test_rag_eval_baseline_runner.py tests/test_rag_eval_dataset_contract.py tests/test_rag_eval_service.py` -> PASS (`10 passed`)
  - `python scripts/scan_secrets.py` -> PASS
  - `python scripts/ci_policy_gate.py --working-tree` -> PASS

## 2026-03-06 RAGQLTY-005 completion snapshot (P1-1)
- step:
  - normalize ingestion metadata contract across loading paths.
- implementation:
  - added `_normalize_chunk_metadata(...)` helper in `backend/services/ingestion_service.py`.
  - applied helper in web/archive/chat/document/codebase/image ingestion branches.
  - contract now ensures baseline keys: `type`, `title`, `doc_title`, `section_title`, `section_path`, `chunk_kind`, `document_class`, `language`, `doc_version`, `source_updated_at` (+ optional `doc_hash`).
- tests:
  - added `tests/test_ingestion_metadata_contract.py`.
- docs/spec:
  - added design note `docs/design/rag-ingestion-metadata-contract-v1.md`.
  - updated `docs/design/rag-general-quality-program-v1.md`.
  - updated `SPEC.md` and `docs/REQUIREMENTS_TRACEABILITY.md`.
  - added review artifact `coordination/reviews/ragqlty-p1-1-ingestion-metadata-contract-2026-03-06.md`.
- verification:
  - `python -m py_compile backend/services/ingestion_service.py tests/test_ingestion_metadata_contract.py` -> PASS
  - `.venv\\Scripts\\python.exe -m pytest -q tests/test_ingestion_metadata_contract.py tests/test_ingestion_routes.py tests/test_ingestion_outbox.py` -> PASS (`8 passed`)
  - `python scripts/scan_secrets.py` -> PASS
  - `python scripts/ci_policy_gate.py --working-tree` -> PASS

## 2026-03-06 RAGQLTY-006 completion snapshot (P1-2)
- step:
  - harden markdown/code chunk metadata consistency.
- implementation:
  - updated `shared/document_loaders/code_loader.py` to include stable `doc_title/section_title/section_path/chunk_no` metadata.
  - updated `shared/document_loaders/markdown_loader.py` section fallbacks and full-mode `code_lang` inference.
- tests:
  - updated `tests/test_code_loader.py`.
  - added `tests/test_markdown_loader_metadata_contract.py`.
  - validated with existing `tests/test_markdown_loader_preserves_commands.py`.
- docs/spec:
  - added design note `docs/design/rag-markdown-code-metadata-consistency-v1.md`.
  - updated `docs/design/rag-general-quality-program-v1.md`.
  - updated `SPEC.md` and `docs/REQUIREMENTS_TRACEABILITY.md`.
  - added review artifact `coordination/reviews/ragqlty-p1-2-markdown-code-metadata-2026-03-06.md`.
- verification:
  - `.venv\\Scripts\\python.exe -m pytest -q tests/test_code_loader.py tests/test_markdown_loader_preserves_commands.py tests/test_markdown_loader_metadata_contract.py tests/test_ingestion_metadata_contract.py` -> PASS (`6 passed`)
  - `python scripts/scan_secrets.py` -> PASS
  - `python scripts/ci_policy_gate.py --working-tree` -> PASS

## 2026-03-06 RAGQLTY-007 completion snapshot (P1-3)
- step:
  - add ingestion metadata regression tests and review evidence.
- implementation:
  - extended `tests/test_ingestion_outbox.py` with:
    - `test_ingest_web_page_normalizes_chunk_metadata`,
    - `test_ingest_codebase_path_sets_code_metadata_contract`.
- docs:
  - added design note `docs/design/rag-ingestion-regression-coverage-v1.md`.
  - updated `docs/design/rag-general-quality-program-v1.md` and `docs/REQUIREMENTS_TRACEABILITY.md`.
  - added review artifact `coordination/reviews/ragqlty-p1-3-ingestion-regression-coverage-2026-03-06.md`.
- verification:
  - `.venv\\Scripts\\python.exe -m pytest -q tests/test_ingestion_outbox.py tests/test_ingestion_metadata_contract.py tests/test_ingestion_routes.py` -> PASS (`10 passed`)
  - `python scripts/scan_secrets.py` -> PASS
  - `python scripts/ci_policy_gate.py --working-tree` -> PASS

## 2026-03-06 RAGQLTY-008 completion snapshot (P2-1)
- step:
  - remove brittle route-level query-specific boosts from default ranking behavior.
- implementation:
  - added `RAG_LEGACY_QUERY_HEURISTICS` config/env switch (default `false`).
  - updated `backend/api/routes/rag.py` so legacy route uses generalized path by default (`base_score`, no keyword fallback) unless explicit rollback switch is enabled.
  - kept `RAG_ORCHESTRATOR_V4` behavior unchanged.
- tests:
  - updated `tests/test_rag_query_definition_intent.py` to explicitly enable legacy heuristics where required and added generalized-default test.
  - validated diagnostics compatibility via `tests/test_rag_diagnostics.py`.
- docs/spec:
  - added design note `docs/design/rag-route-generalized-ranking-cutover-v1.md`.
  - updated `docs/design/rag-general-quality-program-v1.md`.
  - updated `SPEC.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, `docs/OPERATIONS.md`.
  - added review artifact `coordination/reviews/ragqlty-p2-1-generalized-ranking-cutover-2026-03-06.md`.
- verification:
  - `.venv\\Scripts\\python.exe -m pytest -q tests/test_rag_query_definition_intent.py tests/test_rag_diagnostics.py` -> PASS (`13 passed`)
  - `python scripts/scan_secrets.py` -> PASS
  - `python scripts/ci_policy_gate.py --working-tree` -> PASS

## 2026-03-07 Research kickoff: RAG improvement plan + wiki URL example analysis
- role: team-lead-orchestrator + architect
- user ask:
  - провести глубокое исследование проекта, незавершенных задач, дизайн-документов и текущего подхода,
  - подготовить план реализации для улучшения RAG-поиска по загруженным знаниям,
  - отдельно проанализировать исправление загрузки wiki по ссылке из примера.
- classification: non-trivial
- startup ritual:
  - read `coordination/tasks.jsonl` and `coordination/state/codex.md`.
  - no active `in_progress` task existed for this session; activated `RAGAN-001`.
- findings so far:
  - relevant unfinished backlog is `RAGQLTY-009..018`:
    - hybrid fusion/rerank stabilization,
    - diagnostics assertions,
    - answer prompt simplification,
    - RU/EN grounded-response alignment,
    - sanitizer/token URL preservation,
    - e2e regression suite + CI gate + docs finalization.
  - current RAG design surface is spread across:
    - `docs/design/rag-current-algorithm-as-is-v1.md`,
    - `docs/design/rag-generalized-architecture-v2.md`,
    - `docs/design/rag-general-quality-program-v1.md`,
    - `docs/design/rag-route-generalized-ranking-cutover-v1.md`,
    - eval/quality docs under `docs/design/rag-eval-*.md`.
  - wiki URL flow already has two fixes documented:
    - `docs/design/wiki-url-crawl-state-fix-v1.md` for missing `waiting_wiki_root` handler,
    - `docs/design/wiki-url-crawl-state-fix-v2.md` for Gitee JS navigation fallback to git loader.
  - code entry points confirmed:
    - bot state/input: `frontend/bot_callbacks.py`, `frontend/bot_handlers.py`,
    - ingestion: `backend/services/ingestion_service.py`, `backend/api/routes/ingestion.py`,
    - wiki crawling/loaders: `shared/wiki_scraper.py`, `shared/wiki_git_loader.py`,
    - retrieval/query path: `backend/api/routes/rag.py`, `shared/rag_system.py`.
- next_step:
  - inspect the main RAG docs/code paths and synthesize research + implementation plan artifacts for user review.

## 2026-03-07 Research synthesis snapshot
- deep findings:
  - unfinished quality work is concentrated in `RAGQLTY-009..018`; ingestion foundations and eval baseline already exist, so the highest-value next work is retrieval calibration, prompt/safety precision, and e2e gates.
  - `shared/rag_system.py` still contains hidden ranking heuristics even when route-level query heuristics are disabled:
    - `compute_source_boost(...)`,
    - `_is_howto_query(...)`,
    - how-to-specific candidate expansion/sorting,
    - `_simple_search(...)` strong-token filtering.
  - `shared/utils.py` prompt contract is inconsistent:
    - RU answer path requests direct grounded answers without template headings,
    - EN answer path still forces `Main Answer` / `Additionally Found`.
  - `shared/rag_safety.py` still over-filters:
    - strips wiki URLs by pattern,
    - keeps only URLs literally present in context text,
    - removes commands by exact line-presence rather than token-grounding.
  - wiki example URL (`https://gitee.com/mazurdenis/open-harmony/wikis`) is covered on the active path:
    - `kb_wiki_crawl` -> `waiting_wiki_root` -> `ingest_wiki_crawl`,
    - `shared/wiki_scraper.py` routes Gitee wiki URLs to `load_wiki_from_git(...)`.
  - residual wiki gap remains:
    - `wiki_git_load` / `wiki_zip_load` callbacks still depend on `context.user_data['wiki_urls']`, but current UI flow does not populate that mapping.
- subagent status:
  - backlog/wiki explorer completed and confirmed the same residual callback risk.
  - architect subagent was started per policy but later interrupted before writing artifacts; continued with self-authored docs-only fallback.
- artifacts created:
  - `docs/design/rag-search-improvement-program-v1.md`
  - appended research section in `.scratchpad/research.md`
  - appended implementation plan section in `.scratchpad/plan.md`
  - added backlog follow-up task `WIKIFLOW-001`
- next_step:
  - hand off research + plan to user for approval before any implementation.

## 2026-03-07 Research handoff verification
- artifacts confirmed:
  - `docs/design/rag-search-improvement-program-v1.md` created (implementation strategy + acceptance criteria + approval block).
  - `.scratchpad/research.md` updated with RAG/wiki audit section.
  - `.scratchpad/plan.md` updated with execution checklist for `RAGQLTY-009..018` plus `WIKIFLOW-001`.
  - `coordination/tasks.jsonl` updated:
    - `RAGAN-001` -> `completed`,
    - `WIKIFLOW-001` added as follow-up backlog item.
- verification:
  - `python scripts/scan_secrets.py` -> PASS
  - `python scripts/ci_policy_gate.py --working-tree` -> PASS
- agent notes:
  - architect subagent and runtime explorer were explicitly closed/interrupted after they did not return in a reasonable timeout.
  - completed plan is grounded in local code/doc inspection plus completed backlog/wiki explorer output.

## 2026-03-07 Follow-up clarification: design quality vs ingestion maturity
- user follow-up:
  - asked whether the current design is already the best possible and whether ingestion is sufficiently complete given its impact on quality.
- assessment:
  - current design is the strongest state this repo has had so far, but not the best achievable state yet.
  - biggest remaining quality gap is now retrieval calibration + prompt/safety precision, not ingestion breadth.
  - ingestion is broadly sufficient as a base:
    - normalized metadata contract exists,
    - markdown/code metadata consistency was improved,
    - source coverage is broad.
  - ingestion is still not "max-quality complete":
    - canonical parser/chunk graph from generalized architecture v2 is not fully implemented,
    - PDF/DOCX/table/list fidelity can still limit downstream retrieval quality,
    - representative multi-corpus eval coverage is still narrower than real uploaded-knowledge diversity.

## 2026-03-07 Plan refinement for near-ideal target
- user follow-up:
  - asked for a concrete project plan to move the current system toward a near-ideal state.
- planning updates:
  - expanded `docs/design/rag-search-improvement-program-v1.md` with file-level change maps for:
    - retrieval calibration,
    - prompt/format alignment,
    - safety/postprocess precision,
    - e2e quality gates,
    - wiki flow consolidation.
  - added explicit near-ideal follow-up phases for:
    - ingestion excellence,
    - richer chunk/document structure,
    - evidence-pack context composer,
    - multi-corpus evaluation.
  - added roadmap backlog items `RAGIDEAL-001..005` in `coordination/tasks.jsonl`.
- outcome:
  - plan now answers not only "what to improve next" but "which files/modules must change to reach near-ideal quality".

## 2026-03-07 Concrete backlog approval and supersession sync
- user approval:
  - approved the concrete task breakdown with `APPROVED:v1`.
- artifacts synchronized:
  - created `docs/design/rag-near-ideal-task-breakdown-v1.md` as the current execution backlog,
  - marked superseded RAG roadmap docs as outdated,
  - replaced high-level placeholder tasks (`RAGQLTY-009..018`, `RAGIDEAL-001..005`, `WIKIFLOW-001`) with concrete `RAGEXEC-001..018`,
  - updated `coordination/cycle-contract.json` to `RAGEXEC-001`,
  - recorded explicit approval in `coordination/approval-overrides.json`.
- next_step:
  - implementation should start from `RAGEXEC-001` with mandatory verification and independent review.

### Mid-step update: runtime RAG/control-plane scan
- runtime findings:
  - `shared/config.py` shows the stack already has explicit rollout levers:
    - backend switch `RAG_BACKEND` (`legacy`/`qdrant`),
    - orchestration switches `RAG_ORCHESTRATOR_V4`, `RAG_LEGACY_QUERY_HEURISTICS`,
    - search sizing knobs (`RAG_MAX_CANDIDATES`, `RAG_TOP_K`, `RAG_CONTEXT_LENGTH`, `RAG_MIN_RERANK_SCORE`),
    - control-plane knobs for outbox/retention/eval thresholds.
  - `shared/rag_system.py` confirms hybrid retrieval is already implemented:
    - dense channel via Qdrant or legacy FAISS,
    - lexical BM25 channel,
    - RRF fusion,
    - optional reranker pass,
    - fallback path when reranker is unavailable.
  - `backend/api/routes/rag.py` still carries route-level orchestration complexity:
    - intent detection and heuristic boosts for `HOWTO` / `DEFINITION` / `FACTOID`,
    - optional SQL keyword fallback candidate injection,
    - generalized path when heuristics are disabled,
    - degraded-mode + `orchestrator_mode` diagnostics persistence.
  - quality/ops hooks are already present:
    - eval endpoints (`rag_eval_run`, `rag_eval_status`),
    - persisted diagnostics in `retrieval_query_logs` / `retrieval_candidate_logs`,
    - async index outbox worker + retention service,
    - baseline runner / quality gate / legacy-v4 compare scripts.
- emerging conclusion:
  - the next improvement plan should focus on unfinished ranking/generalization steps (`RAGQLTY-009..018`) and not propose redundant infra that the repo already has.

## 2026-03-07 RAGEXEC-001 kickoff
- role: developer
- approved source:
  - `docs/design/rag-search-improvement-program-v1.md`
  - `docs/design/rag-near-ideal-task-breakdown-v1.md`
- task:
  - implement explicit retrieval channel budgets and rerank top-N window.
- scoped requirements:
  - add explicit config/env knobs for dense candidate budget, BM25 candidate budget, and rerank input window,
  - wire these budgets into `shared/rag_system.py`,
  - keep behavior rollback-safe and avoid touching prompt/safety/context phases in this task,
  - add/adjust focused retrieval tests,
  - update spec/config/traceability docs for the new knobs and behavior.
- next_step:
  - inspect `shared/config.py`, `shared/rag_system.py`, and retrieval tests before code edits.

## 2026-03-07 RAGEXEC-001 runtime edit snapshot
- implementation:
  - added explicit config fallbacks in `shared/config.py`:
    - `RAG_DENSE_CANDIDATES`,
    - `RAG_BM25_CANDIDATES`,
    - `RAG_RERANK_TOP_N`,
    - all default back to legacy `RAG_MAX_CANDIDATES`.
  - updated `shared/rag_system.py` to:
    - load explicit channel budgets,
    - stop implicit how-to dense-window expansion in favor of config-driven dense budget,
    - cap rerank input to `max(top_k, RAG_RERANK_TOP_N)`.
  - added focused regression harness `tests/test_rag_system_budgets.py`.
- next_step:
  - run focused verification on the new runtime slice, then update docs/spec/traceability/configuration artifacts and cycle coordination files.

## 2026-03-07 RAGEXEC-001 docs + verification snapshot
- docs/config:
  - updated `env.template`, `docs/CONFIGURATION.md`, `SPEC.md`, `docs/REQUIREMENTS_TRACEABILITY.md`.
  - updated `docs/design/rag-near-ideal-task-breakdown-v1.md` with the concrete config contract for this slice.
  - updated `coordination/cycle-contract.json` to include `tests/test_rag_system_budgets.py` in required artifacts/commands.
- verification:
  - `python -m py_compile shared/config.py shared/rag_system.py tests/test_rag_system_budgets.py` -> PASS
  - `.venv\Scripts\python.exe -m pytest -q tests/test_rag_system_budgets.py tests/test_rag_query_definition_intent.py tests/test_rag_diagnostics.py` -> PASS (`15 passed`)
  - `python scripts/scan_secrets.py` -> PASS
  - `python scripts/ci_policy_gate.py --working-tree` -> PASS
- pending:
  - independent review artifact for `RAGEXEC-001`,
  - mark `RAGEXEC-001` completed in `coordination/tasks.jsonl` after review sync.

## 2026-03-07 RAGEXEC-001 completion snapshot
- review:
  - independent reviewer agent returned PASS with no findings.
  - review artifact created: `coordination/reviews/ragexec-001-2026-03-07.md`.
- coordination:
  - `coordination/tasks.jsonl` updated: `RAGEXEC-001` -> `completed`.
- notes:
  - review-report validation script referenced by policy is still absent in `scripts/`, so no automated validation command could be run for the artifact.
