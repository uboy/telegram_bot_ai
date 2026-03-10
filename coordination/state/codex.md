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

## 2026-03-08 Embedded RAG Quality Eval Design Snapshot
- role: team-lead-orchestrator / architect
- classification: non-trivial
- task:
  - design an embedded local RAG quality-evaluation system using real project corpora, Ollama-backed answer/judge flow, source-family metrics, and trend reporting
- findings:
  - current eval service is retrieval-only and uses a narrow fixed YAML suite
  - `test.pdf` is repo-safe and accessible
  - local `open-harmony` corpus is accessible and contains mixed docs/code structure
  - Telegram export is accessible through a developer-local path override and must remain local-only
  - existing project config already exposes baseline Ollama transport/model settings
- artifacts created:
  - `.scratchpad/research.md` updated with current-state audit and metric direction
  - `.scratchpad/plan.md` updated with phased implementation slices and verification gates
  - `docs/design/rag-embedded-quality-eval-system-v1.md` added as approval draft
  - `coordination/approval-overrides.json` updated for user-approved design-doc exception
- security extension:
  - promoted RAG security to first-class design scope:
    - direct and indirect prompt injection,
    - confidential data leakage,
    - system-prompt leakage,
    - ingestion-time document screening,
    - instruction-plane separation,
    - sensitive-context minimization,
    - suspicious-event observability
- next_step:
  - wait for user CC on `docs/design/rag-embedded-quality-eval-system-v1.md`
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

## 2026-03-07 RAGEXEC-002 kickoff
- role: developer
- task:
  - remove hidden default ranking boosts from the generalized retrieval path.
- scoped requirements:
  - neutralize retrieval-core `source_boost` in generalized mode,
  - reduce `_is_howto_query` from ranking driver to rollback-only legacy behavior,
  - keep rollback safety via the existing `RAG_LEGACY_QUERY_HEURISTICS` switch,
  - add focused runtime/diagnostics regressions,
  - update `SPEC.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, `docs/OPERATIONS.md`, and cycle artifacts.
- next_step:
  - implement retrieval-core gating in `shared/rag_system.py` and annotate route diagnostics hints in `backend/api/routes/rag.py`.

## 2026-03-07 RAGEXEC-002 implementation snapshot
- runtime:
  - added `shared/rag_system.py::_legacy_query_heuristics_enabled()` as the retrieval-core rollback gate.
  - generalized mode now zeroes retrieval-core `source_boost`.
  - generalized mode no longer lets `_is_howto_query()` drive dense rerank/fallback ordering or `_simple_search()` prefiltering.
  - rollback behavior remains available only when `RAG_LEGACY_QUERY_HEURISTICS=true` and `RAG_ORCHESTRATOR_V4=false`.
- route diagnostics:
  - `backend/api/routes/rag.py` now persists `retrieval_core_mode` inside retrieval hints (`generalized` / `legacy_heuristic`).
- tests:
  - extended `tests/test_rag_system_budgets.py` with generalized-vs-legacy regressions for `source_boost` and how-to fallback sorting.
  - extended `tests/test_rag_diagnostics.py` with `retrieval_core_mode` persistence assertions.
- docs:
  - updated `SPEC.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, `docs/OPERATIONS.md`, and `docs/design/rag-near-ideal-task-breakdown-v1.md`.
- verification:
  - `python -m py_compile shared/rag_system.py backend/api/routes/rag.py tests/test_rag_system_budgets.py tests/test_rag_diagnostics.py tests/test_rag_quality.py` -> PASS
  - `.venv\Scripts\python.exe -m pytest -q tests/test_rag_system_budgets.py tests/test_rag_query_definition_intent.py tests/test_rag_diagnostics.py tests/test_rag_quality.py` -> PASS (`20 passed`)
  - `python scripts/scan_secrets.py` -> PASS
  - `python scripts/ci_policy_gate.py --working-tree` -> PASS
- pending:
  - independent review artifact for `RAGEXEC-002`,
  - mark `RAGEXEC-002` completed in `coordination/tasks.jsonl` after review sync.

## 2026-03-07 RAGEXEC-002 completion snapshot
- review:
  - independent reviewer agent returned PASS with no findings.
  - review artifact created: `coordination/reviews/ragexec-002-2026-03-07.md`.
- coordination:
  - `coordination/tasks.jsonl` updated: `RAGEXEC-002` -> `completed`.
- notes:
  - review-report validation script referenced by policy is still absent in `scripts/`, so no automated validation command could be run for the artifact.

## 2026-03-07 RAGEXEC-003 kickoff
- role: developer
- task:
  - add mandatory retrieval diagnostics assertions and default-mode regressions.
- scoped requirements:
  - make diagnostics candidate trace fields strict and testable,
  - expose retrieval-core execution mode in the diagnostics API response,
  - tighten route persistence so `channel_rank` / `fusion_rank` / `fusion_score` / `rerank_delta` are meaningful,
  - add API contract coverage for diagnostics response shape,
  - update `SPEC.md`, `docs/API_REFERENCE.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, and cycle artifacts.
- next_step:
  - implement the stricter diagnostics contract in `backend/api/routes/rag.py` and `backend/schemas/rag.py`, then lock it with `tests/test_rag_diagnostics.py` and `tests/test_api_routes_contract.py`.

## 2026-03-07 RAGEXEC-003 diagnostics contract finding
- current gap confirmed from route inspection:
  - persisted `channel_rank` and `fusion_rank` were just the final loop rank,
  - diagnostics response exposed `retrieval_core_mode` only inside raw `hints`,
  - candidate trace fields were optional in the API schema, so incident triage had no strict contract.
- implementation in progress:
  - deriving stable per-channel rank and fusion score defaults in `backend/api/routes/rag.py`,
  - promoting strict candidate fields plus `retrieval_core_mode` in `backend/schemas/rag.py`,
  - adding a dedicated contract test file instead of mutating frozen existing tests.
- focused verification passed:
  - `python -m py_compile backend/api/routes/rag.py backend/schemas/rag.py tests/test_rag_diagnostics.py tests/test_rag_diagnostics_contract.py`
  - `.venv\Scripts\python.exe -m pytest -q tests/test_rag_diagnostics.py tests/test_rag_diagnostics_contract.py tests/test_api_routes_contract.py` -> `9 passed`
- gate verification passed:
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`
- next_step:
  - send the slice to an independent reviewer agent and close coordination artifacts if verdict is PASS.

## 2026-03-07 RAGEXEC-003 completion snapshot
- review:
  - independent reviewer agent returned `PASS` with no MUST-FIX/SHOULD-FIX findings.
  - review artifact created: `coordination/reviews/ragexec-003-2026-03-07.md`.
  - reviewer clarification resolved by replacing the stale `REVIEW REQUIRED` footer in `docs/design/rag-near-ideal-task-breakdown-v1.md` with the recorded approval status.
- coordination:
  - `coordination/tasks.jsonl` updated: `RAGEXEC-003` -> `completed`.
- notes:
  - `coordination/templates/review-report.md` is absent in this repo; the review artifact was written in the established local format used by prior `ragexec-*` reports.
  - review-report validation script referenced by policy is still absent in `scripts/`, so no automated validation command could be run for the artifact.
- final gates:
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`

## 2026-03-07 RAGEXEC-004 kickoff
- role: developer
- task:
  - unify RU/EN grounded direct-answer prompt contract.
- scoped requirements:
  - remove the English `Main Answer` / `Additionally Found` template contract from `task="answer"` with context,
  - keep RU and EN answer prompts on one direct grounded-response policy,
  - preserve grounded no-evidence refusal behavior and citation grounding instructions,
  - add focused prompt contract coverage without consuming the fuller regression-suite scope reserved for `RAGEXEC-005`,
  - update `SPEC.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, and cycle artifacts.
- next_step:
  - implement the shared answer-contract helper in `shared/utils.py`, add a small prompt contract test file, and run focused verification.

## 2026-03-07 RAGEXEC-004 implementation snapshot
- prompt gap confirmed:
  - RU `task="answer"` branch already enforced direct grounded answers,
  - EN `task="answer"` branch still forced `Main Answer` / `Additionally Found` headings and a much heavier template.
- implementation in progress:
  - extracted a shared `_create_grounded_answer_prompt(...)` helper in `shared/utils.py`,
  - routed both RU and EN contextual answer prompts through the same direct-answer contract,
  - added focused prompt builder coverage in `tests/test_rag_prompt_contract.py` without consuming the broader formatter regression scope reserved for `RAGEXEC-005`.
- first verification finding:
  - prompt tests failed because the new helper still mentioned forbidden heading names inside the instruction text itself.
- correction:
  - removed literal legacy heading names from the direct-answer rule so the contract forbids templates without reintroducing them.
- second verification finding:
  - the English response-format note still contained the legacy heading label in prose, and the Russian format note still used its old extra-section label.
- correction:
  - removed the remaining literal legacy labels from both localized format blocks.
- focused verification passed:
  - `python -m py_compile shared/utils.py tests/test_rag_prompt_contract.py tests/test_rag_summary_modes.py`
  - `.venv\Scripts\python.exe -m pytest -q tests/test_rag_prompt_contract.py tests/test_rag_summary_modes.py` -> `5 passed`
- gate verification passed:
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`
- next_step:
  - send the prompt-contract diff to an independent reviewer and close coordination artifacts if verdict is PASS.

## 2026-03-07 RAGEXEC-004 review follow-up
- reviewer SHOULD-FIX:
  - RU prompt still contained an English citation instruction inside the new shared helper.
- correction in progress:
  - localizing the citation rule and remaining Russian helper wording,
  - extending `tests/test_rag_prompt_contract.py` so RU prompt coverage fails if English citation instructions leak back in.
- follow-up verification passed:
  - `python -m py_compile shared/utils.py tests/test_rag_prompt_contract.py tests/test_rag_summary_modes.py`
  - `.venv\Scripts\python.exe -m pytest -q tests/test_rag_prompt_contract.py tests/test_rag_summary_modes.py` -> `5 passed`
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`
- next_step:
  - request a short reviewer re-check on the localized citation rule before writing the final review artifact.

## 2026-03-07 RAGEXEC-004 completion snapshot
- review:
  - independent reviewer agent returned final `PASS` with no MUST-FIX/SHOULD-FIX findings after the localized citation-rule follow-up.
  - review artifact created: `coordination/reviews/ragexec-004-2026-03-07.md`.
- coordination:
  - `coordination/tasks.jsonl` updated: `RAGEXEC-004` -> `completed`.
- notes:
  - `coordination/templates/review-report.md` is absent in this repo; the review artifact was written in the established local format used by prior `ragexec-*` reports.
  - review-report validation script referenced by policy is still absent in `scripts/`, so no automated validation command could be run for the artifact.
- final gates:
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`

## 2026-03-07 RAGEXEC-005 kickoff
- role: reviewer
- task:
  - add prompt/format regression suite and remove forced heading dependence.
- scoped requirements:
  - add dedicated regressions for headingless direct answers, deterministic no-evidence refusal, and prompt output without forced answer headings,
  - verify `format_for_telegram_answer(...)` formats direct answers correctly without depending on legacy section labels,
  - update `docs/TESTING.md` and `docs/REQUIREMENTS_TRACEABILITY.md`,
  - avoid broad prompt-contract rewrites already handled by `RAGEXEC-004`.
- next_step:
  - add `tests/test_rag_prompt_format.py`, make any minimal formatter clarification needed in `shared/utils.py`, and run focused verification.

## 2026-03-07 RAGEXEC-005 implementation snapshot
- formatter surface confirmed:
  - `format_for_telegram_answer(...)` still normalizes legacy section labels, but the code path does not require them for headingless direct answers.
- implementation in progress:
  - added a compatibility-only note next to legacy heading normalization in `shared/utils.py`,
  - added `tests/test_rag_prompt_format.py` to lock:
    - headingless direct-answer formatting,
    - deterministic no-evidence refusal output,
    - prompt contract without forced headings,
    - legacy heading support as backward-compatible input only.
- focused verification passed:
  - `python -m py_compile shared/utils.py tests/test_rag_prompt_format.py tests/test_rag_summary_modes.py`
  - `.venv\Scripts\python.exe -m pytest -q tests/test_rag_prompt_format.py tests/test_rag_summary_modes.py` -> `7 passed`
- next_step:
  - sync `docs/TESTING.md` and traceability, then run final gates and independent review.
- policy-gate finding:
  - because `shared/utils.py` changed, governance still requires `SPEC.md` and a design-doc delta even for this reviewer-oriented slice.
- correction:
  - recorded formatter compatibility behavior in `SPEC.md`,
  - extended `RAGEXEC-005` runtime contract in `docs/design/rag-near-ideal-task-breakdown-v1.md`.
- gate verification passed:
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`
- next_step:
  - send the prompt/format regression diff to an independent reviewer and close coordination artifacts if verdict is PASS.

## 2026-03-07 RAGEXEC-005 review follow-up
- reviewer SHOULD-FIX:
  - `coordination/cycle-contract.json` was missing `SPEC.md` in `required_artifacts` even though this slice updated it.
- correction in progress:
  - aligning the cycle contract with the actual slice deliverables before writing the final review artifact.

## 2026-03-07 RAGEXEC-005 completion snapshot
- review:
  - independent reviewer agent returned `PASS` on the full slice and `PASS` again after the cycle-contract follow-up.
  - review artifact created: `coordination/reviews/ragexec-005-2026-03-07.md`.
- coordination:
  - `coordination/tasks.jsonl` updated: `RAGEXEC-005` -> `completed`.
- notes:
  - `coordination/templates/review-report.md` is absent in this repo; the review artifact was written in the established local format used by prior `ragexec-*` reports.
  - review-report validation script referenced by policy is still absent in `scripts/`, so no automated validation command could be run for the artifact.
- final gates:
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`

## 2026-03-07 RAGEXEC-006 kickoff
- role: developer
- task:
  - refactor command sanitizer to token-level grounding rules.
- scoped requirements:
  - preserve grounded commands when formatting differs slightly from context,
  - continue removing invented commands and current untrusted wiki-link lines,
  - keep the change slice-local to `shared/rag_safety.py` plus additive regression coverage,
  - use a new dedicated test file for the new cases to respect test-freeze on existing suites,
  - update `SPEC.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, and execution backlog notes.
- next_step:
  - implement token-level command grounding helpers in `shared/rag_safety.py`, add focused regression cases in a new safety test file, and run targeted verification.

## 2026-03-07 RAGEXEC-006 implementation snapshot
- root cause confirmed:
  - `sanitize_commands_in_answer(...)` used exact normalized line inclusion against the whole context string, so minor formatting differences (`$` prompt, `-j8` vs `-j 8`, grounded subset commands) were dropped as if invented.
- implementation in progress:
  - added command tokenization/signature helpers in `shared/rag_safety.py`,
  - switched sanitizer grounding from full-line containment to signature + token-subset matching against a context command catalog,
  - kept current wiki-url stripping and fallback messaging unchanged for this slice,
  - added additive regression coverage in `tests/test_rag_safety_token_grounding.py` to respect test-freeze on existing safety tests.
- focused verification passed:
  - `python -m py_compile shared/rag_safety.py tests/test_rag_safety.py tests/test_rag_safety_token_grounding.py`
  - `.venv\Scripts\python.exe -m pytest -q tests/test_rag_safety.py tests/test_rag_safety_token_grounding.py` -> `8 passed`
- gate verification passed:
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`
- next_step:
  - send the token-grounding diff to an independent reviewer and close coordination artifacts if verdict is PASS.

## 2026-03-07 RAGEXEC-006 review failure follow-up
- independent reviewer verdict:
  - `FAIL` on the first token-grounding attempt.
- must-fix findings:
  - token-set matching preserved semantically altered commands when the same argument bag was reused with different option/value bindings,
  - command detection still skipped non-allowlisted shell families such as `tar`,
  - chained context lines such as `cd out && make test` were not split into grounded subcommands.
- correction in progress:
  - replacing token-set grounding with shape-aware matching over ordered option/value pairs and positional arguments,
  - broadening command recognition for generic shell-like commands that carry CLI syntax,
  - cataloging connector-split context segments so grounded subcommands survive.
- follow-up verification passed:
  - `python -m py_compile shared/rag_safety.py tests/test_rag_safety.py tests/test_rag_safety_token_grounding.py`
  - `.venv\Scripts\python.exe -m pytest -q tests/test_rag_safety.py tests/test_rag_safety_token_grounding.py` -> `12 passed`
  - reviewer probe cases now behave as intended:
    - swapped option values -> rejected,
    - dropped option marker -> rejected,
    - `make test` from `cd out && make test` -> preserved,
    - compact `repo sync -c -j8` -> preserved,
    - unrelated `tar -c -z -f ...` -> rejected.
- gate verification passed:
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`
- next_step:
  - resend the slice to the independent reviewer and close coordination artifacts if verdict is PASS.

## 2026-03-07 RAGEXEC-006 second review follow-up
- independent reviewer verdict:
  - `FAIL` on the first re-check.
- must-fix finding:
  - generic two-token commands such as `pytest tests/test_rag_safety.py` still bypassed `_is_command_line(...)` because generic detection consumed the path operand into the signature and saw an empty remainder.
- correction in progress:
  - shifting generic command detection to inspect the first executable token plus the remaining raw CLI tail instead of the post-signature remainder,
  - adding a regression that rejects unrelated `pytest tests/...` snippets.
- follow-up verification passed:
  - `python -m py_compile shared/rag_safety.py tests/test_rag_safety.py tests/test_rag_safety_token_grounding.py`
  - `.venv\Scripts\python.exe -m pytest -q tests/test_rag_safety.py tests/test_rag_safety_token_grounding.py` -> `13 passed`
  - reviewer probe cases now behave as intended:
    - unrelated `pytest tests/test_rag_safety.py` -> rejected,
    - swapped option values -> rejected,
    - dropped option marker -> rejected,
    - `make test` from `cd out && make test` -> preserved,
    - compact `repo sync -c -j8` -> preserved,
    - unrelated `tar -c -z -f ...` -> rejected.
- gate verification passed:
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`
- next_step:
  - resend the slice to the independent reviewer and close coordination artifacts if verdict is PASS.

## 2026-03-07 RAGEXEC-006 completion snapshot
- review:
  - independent reviewer agent returned final `PASS` after the generic two-token command follow-up.
  - review artifact created: `coordination/reviews/ragexec-006-2026-03-07.md`.
- coordination:
  - `coordination/tasks.jsonl` updated: `RAGEXEC-006` -> `completed`.
  - `coordination/cycle-contract.json` switched to `RAGEXEC-007`.
- notes:
  - `coordination/templates/review-report.md` is absent in this repo; the review artifact was written in the established local format used by prior `ragexec-*` reports.
  - review-report validation script referenced by policy is still absent in `scripts/`, so no automated validation command could be run for the artifact.
- final gates:
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`

## 2026-03-08 RAGEXEC-007 kickoff
- role: developer
- task:
  - preserve context-backed URLs and add safety regression coverage.
- scoped requirements:
  - keep source-backed document and wiki URLs when they are grounded in retrieval results,
  - continue stripping untrusted markdown links and bare URLs,
  - keep the change slice-local to `shared/rag_safety.py`, `backend/api/routes/rag.py`, and additive tests,
  - add focused coverage for Gitee wiki URL preservation scenarios,
  - update `SPEC.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, and execution backlog notes if behavior changes.
- current finding:
  - `strip_untrusted_urls(...)` only trusts URLs that already appear in `context_text`, but `rag_query` and `rag_summary` build `context_text` from content blocks without `source_path`, so grounded source URLs are stripped even when they are present in retrieval results and returned `sources`.
- next_step:
  - add an explicit grounded-URL allowlist path from retrieval results into the safety layer, then cover markdown + bare URL preservation with focused tests.

## 2026-03-08 RAGEXEC-007 implementation snapshot
- implementation in progress:
  - extended `strip_untrusted_urls(...)` with grounded URL allowlist support and placeholder-safe markdown-link preservation so allowed markdown URLs are not stripped again by the bare-URL pass,
  - added `_collect_grounded_url_allowlist(...)` in `backend/api/routes/rag.py` and wired it into both `rag_query` and `rag_summary`,
  - added focused coverage in `tests/test_rag_url_preservation.py` for direct allowlist behavior and `rag_query` preservation of a grounded Gitee wiki page URL while stripping an unrelated link,
  - kept allowlist-based URL preservation working even when the raw `context_text` does not itself contain any URL.
- focused verification passed:
  - `python -m py_compile shared/rag_safety.py backend/api/routes/rag.py tests/test_rag_safety.py tests/test_rag_url_preservation.py`
  - `.venv\Scripts\python.exe -m pytest -q tests/test_rag_safety.py tests/test_rag_url_preservation.py` -> `7 passed`
- gate verification passed:
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`
- next_step:
  - send the URL-preservation slice to an independent reviewer and close coordination artifacts if verdict is PASS.

## 2026-03-08 RAGEXEC-007 completion snapshot
- review:
  - independent reviewer agent returned final `PASS` with no MUST-FIX or SHOULD-FIX findings.
  - review artifact created: `coordination/reviews/ragexec-007-2026-03-08.md`.
- coordination:
  - `coordination/tasks.jsonl` updated: `RAGEXEC-007` -> `completed`.
  - `coordination/cycle-contract.json` switched to `RAGEXEC-008`.
- notes:
  - `coordination/templates/review-report.md` is absent in this repo; the review artifact was written in the established local format used by prior `ragexec-*` reports.
  - review-report validation script referenced by policy is still absent in `scripts/`, so no automated validation command could be run for the artifact.
- final gates:
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`

## 2026-03-08 Quality-Eval System kickoff
- role: team-lead-orchestrator
- task:
  - design and then implement an embedded RAG quality-evaluation system with local test corpora, Ollama-backed answer checks, regression metrics, and trend reporting across iterations.
- classification:
  - non-trivial (new multi-stage feature, cross-file architecture, quality gates, external data inputs, metric design).
- requested inputs:
  - `test.pdf`,
  - `open-harmony` catalog,
  - developer-local Telegram export directory via local path override.
- lifecycle decision:
  - follow 6-step design-first workflow before implementation,
  - research + planning must define dataset contract, metric set, Ollama integration boundaries, and how quality deltas are persisted and compared per run.
- next_step:
  - inspect available input paths and spawn architect research/planning for the quality-eval system design.

## 2026-03-08 Quality-Eval System scope extension
- additional mandatory requirement from user:
  - treat RAG security as a first-class part of the design, including prompt injection, indirect prompt injection / RAG poisoning, confidential-data leakage, system-prompt leakage, ingestion-time screening, context separation, access control, and observability for suspicious behavior.
- orchestration note:
  - the first architect subtask was interrupted to include the new security scope and must be restarted with the full requirement set.
- next_step:
  - respawn architect research/planning with combined quality-eval + security-by-design scope.

## 2026-03-08 Quality-Eval System planning snapshot
- research/plan artifacts produced:
  - `.scratchpad/research.md`
  - `.scratchpad/plan.md`
  - `docs/design/rag-embedded-quality-eval-security-system-v1.md`
- key planning outcome:
  - extend the existing eval service and gate path instead of building a parallel evaluator,
  - use repo-safe `test.pdf`, env-resolved `open-harmony`, and local-only Telegram export,
  - add answer-level Ollama judging, trend artifacts, source-family metrics, and security/adversarial cases,
  - make security metrics and suspicious-event observability part of the dataset/gate contract.
- scope notes:
  - `docs/design/rag-embedded-quality-eval-security-system-v1.md` supersedes the same-day pre-security draft `docs/design/rag-embedded-quality-eval-system-v1.md` for implementation planning.
  - implementation must not start before user CC on the new design spec.
- next_step:
  - wait for user review token on the design spec, then decompose the approved plan into execution tasks and start implementation.

## 2026-03-08 Quality-Eval System design artifacts snapshot
- role: architect
- planning-only outputs created/updated:
  - `.scratchpad/research.md`
  - `.scratchpad/plan.md`
  - `docs/design/rag-embedded-quality-eval-security-system-v1.md`
- key decisions recorded:
  - evaluation must run the real RAG path, not a simplified prompt-only harness,
  - source manifest must separate repo-safe fixtures from local-only corpora,
  - Ollama must have distinct answer and judge roles in eval,
  - trend artifacts must be append-only and source-family/security aware,
  - security is a gateable requirement covering injection, leakage, screening, least privilege, and observability.
- notable research findings:
  - existing eval stack is retrieval-only,
  - `test.pdf` is repo-safe,
  - `open-harmony` must be resolved by env override,
  - Telegram export must remain local-only,
  - current `web/wiki/markdown` chunk defaults are a known source-family quality risk,
  - current PDF loader dependency availability may vary by environment.
- next_step:
  - stop for user CC on the new design spec before any implementation or backlog changes.
- final gates:
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS` (`no functional files changed`)

## 2026-03-08 RAGEXEC-008 kickoff
- role: developer
- task:
  - implement the dev-only eval dataset and local source-manifest contract under the approved `real corpora = local-only` rule.
- coordination updates completed:
  - marked `docs/design/rag-embedded-quality-eval-security-system-v1.md` as approved/current for implementation,
  - marked `docs/design/rag-embedded-quality-eval-system-v1.md` as outdated/superseded,
  - updated `RAGEXEC-008` backlog text and cycle contract to require manifest/security contract coverage,
  - recorded the stricter local-only approval in `coordination/approval-overrides.json`,
  - moved `RAGEXEC-008` to `in_progress`.
- next_step:
  - implement `tests/data/rag_eval_ready_data_v2.yaml`, `tests/data/rag_eval_source_manifest_v1.yaml`, and service/tests updates without embedding real local corpus content.

## 2026-03-08 RAGEXEC-008 implementation snapshot
- implementation:
  - switched eval service default suite to `tests/data/rag_eval_ready_data_v2.yaml`,
  - added dataset normalization for `expected_sources`, source families, answer/security fields, and security scenario slices,
  - added manifest loading for `tests/data/rag_eval_source_manifest_v1.yaml` and exposed dataset/manifest metadata in eval run metrics,
  - added committed v2 dataset + source manifest with synthetic/public-safe entries only,
  - added contract coverage for dataset schema, source manifest invariants, security attack coverage, and local-only marker checks.
- verification:
  - `python -m py_compile backend/services/rag_eval_service.py tests/test_rag_eval_dataset_contract.py tests/test_rag_eval_fixture_manifest.py tests/test_rag_eval_security_contract.py tests/test_rag_eval_service.py` -> `PASS`
  - `.venv\Scripts\python.exe -m pytest -q tests/test_rag_eval_dataset_contract.py tests/test_rag_eval_fixture_manifest.py tests/test_rag_eval_security_contract.py tests/test_rag_eval_service.py` -> `PASS` (`11 passed`)
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`
- next_step:
  - wait for independent review verdict, then finalize `RAGEXEC-008` coordination artifacts and move the cycle to the next slice.

## 2026-03-08 RAGEXEC-008 review-fix snapshot
- reviewer blockers addressed:
  - removed developer-local Telegram path markers from committed/untracked design + coordination artifacts,
  - aligned outdated draft env names to `RAG_EVAL_LOCAL_OPENHARMONY_PATH` / `RAG_EVAL_LOCAL_TELEGRAM_EXPORT_PATH`,
  - documented the local-only manifest placeholders in `docs/CONFIGURATION.md`,
  - stopped `rag_eval_service` from synthesizing a manifest version when no manifest file is actually loaded.
- repeated verification:
  - `python -m py_compile backend/services/rag_eval_service.py tests/test_rag_eval_dataset_contract.py tests/test_rag_eval_fixture_manifest.py tests/test_rag_eval_security_contract.py tests/test_rag_eval_service.py` -> `PASS`
  - `.venv\Scripts\python.exe -m pytest -q tests/test_rag_eval_dataset_contract.py tests/test_rag_eval_fixture_manifest.py tests/test_rag_eval_security_contract.py tests/test_rag_eval_service.py` -> `PASS` (`11 passed`)
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`
- next_step:
  - capture independent reviewer re-check output, then write `coordination/reviews/ragexec-008-2026-03-08.md` and close the slice.

## 2026-03-08 RAGEXEC-008 blocker-fix snapshot
- additional reviewer blockers addressed:
  - canonicalized explicit slice aliases in `RAGEvalService._normalize_slices(...)` so contract names such as `long_context`, `refusal_expected`, `direct_prompt_injection`, and `indirect_prompt_injection` normalize to runtime slice keys,
  - replaced developer-specific negative-test literals with generic local-path regex checks in the eval contract tests,
  - aligned `RAGEXEC-008` backlog/cycle artifacts so `docs/CONFIGURATION.md` and `docs/OPERATIONS.md` are required outputs for the slice.
- repeated verification:
  - `python -m py_compile backend/services/rag_eval_service.py tests/test_rag_eval_dataset_contract.py tests/test_rag_eval_fixture_manifest.py tests/test_rag_eval_security_contract.py tests/test_rag_eval_service.py` -> `PASS`
  - `.venv\Scripts\python.exe -m pytest -q tests/test_rag_eval_dataset_contract.py tests/test_rag_eval_fixture_manifest.py tests/test_rag_eval_security_contract.py tests/test_rag_eval_service.py` -> `PASS` (`12 passed`)
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`
- next_step:
  - obtain final reviewer PASS and record the review artifact.

## 2026-03-08 RAGEXEC-008 completion snapshot
- review:
  - independent reviewer re-check returned `PASS` with no remaining MUST-FIX or SHOULD-FIX items.
  - review artifact created: `coordination/reviews/ragexec-008-2026-03-08.md`.
- coordination:
  - `coordination/tasks.jsonl` updated: `RAGEXEC-008` -> `completed`.
  - `coordination/cycle-contract.json` switched to `RAGEXEC-009`.
- verification:
  - `python -m py_compile backend/services/rag_eval_service.py tests/test_rag_eval_dataset_contract.py tests/test_rag_eval_fixture_manifest.py tests/test_rag_eval_security_contract.py tests/test_rag_eval_service.py` -> `PASS`
  - `.venv\Scripts\python.exe -m pytest -q tests/test_rag_eval_dataset_contract.py tests/test_rag_eval_fixture_manifest.py tests/test_rag_eval_security_contract.py tests/test_rag_eval_service.py` -> `PASS` (`12 passed`)
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`
- notes:
  - `coordination/templates/review-report.md` and `scripts/validate-review-report.*` are still absent in this repo, so the review artifact was recorded in the established local format without automated validator support.

## 2026-03-08 RAGEXEC-008 independent re-check
- role: reviewer
- task:
  - perform a concise independent re-check after review fixes for `RAGEXEC-008`.
- scope:
  - `backend/services/rag_eval_service.py`
  - `docs/design/rag-embedded-quality-eval-system-v1.md`
  - `coordination/state/codex.md`
  - `docs/CONFIGURATION.md`
  - existing `RAGEXEC-008` files/tests in the current working-tree diff
- progress:
  - startup ritual completed (`coordination/tasks.jsonl` + state file re-read)
  - reviewer skill loaded
  - current diff inspected for the scoped files and coordination artifacts
  - initial check in progress for developer-local absolute paths and embedded real-corpus markers across dataset/manifest/docs/tests
- next_step:
  - finish leakage scan, compare diff against the approved security-first design contract, run required verification commands, and issue PASS/FAIL.
- outcome:
  - no scoped text artifact currently embeds a developer-local absolute path or raw local-corpus marker;
  - `tests/test_rag_eval_dataset_contract.py`, `tests/test_rag_eval_fixture_manifest.py`, and `tests/test_rag_eval_security_contract.py` use generic regex-based leakage checks rather than hardcoded local paths;
  - scoped service/docs/tests align with the approved `docs/design/rag-embedded-quality-eval-security-system-v1.md` Phase A contract for `RAGEXEC-008`;
  - no remaining MUST-FIX or SHOULD-FIX findings identified in the current diff.
- verification:
  - `python -m py_compile backend/services/rag_eval_service.py tests/test_rag_eval_dataset_contract.py tests/test_rag_eval_fixture_manifest.py tests/test_rag_eval_security_contract.py tests/test_rag_eval_service.py` -> `PASS`
  - `.venv\Scripts\python.exe -m pytest -q tests/test_rag_eval_dataset_contract.py tests/test_rag_eval_fixture_manifest.py tests/test_rag_eval_security_contract.py tests/test_rag_eval_service.py` -> `PASS` (`12 passed, 3 warnings`)
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`

## 2026-03-09 RAGEXEC-009 kickoff
- role: developer
- task:
  - implement slice-aware baseline reporting and quality gate outputs for eval artifacts.
- scoped requirements:
  - baseline runner must surface source-family and security/failure-mode slices, not only aggregate metric rows,
  - runner should start writing structured artifact paths and append-only trend history,
  - quality gate should handle canonical slice aliases and report/check source-family/security slices more explicitly,
  - keep the slice local to `scripts/rag_eval_baseline_runner.py`, `scripts/rag_eval_quality_gate.py`, `backend/services/rag_eval_service.py`, and focused tests.
- next_step:
  - implement runner/gate helpers first, then sync docs and rerun the focused eval-tool verification set.

## 2026-03-09 RAGEXEC-009 docs sync
- progress:
  - synced `SPEC.md` with per-label run/latest/trend artifact layout and metadata-driven required-slice behavior,
  - updated `docs/TESTING.md` focused eval command to include `tests/test_rag_eval_baseline_runner.py`,
  - updated `docs/OPERATIONS.md` artifact-mode examples to the new `latest/<label>.json` layout and documented slice-group/canonical-alias behavior,
  - updated `docs/REQUIREMENTS_TRACEABILITY.md` and `docs/design/rag-near-ideal-task-breakdown-v1.md` to match the new slice-aware runner/gate contract.
- next_step:
  - rerun required verification commands, then request an independent review on the finalized diff.

## 2026-03-09 RAGEXEC-009 review fix
- reviewer_feedback:
  - independent review returned `FAIL` because `_derive_required_slices()` could silently drop recorded/core slices when `sample_size=0`, weakening the gate;
  - reviewer also requested a stronger append-only trend regression for repeated baseline artifact writes.
- fix_plan:
  - preserve `metrics.slices` in required-slice derivation without filtering by `slice_summary.sample_size`,
  - add a regression proving missing recorded slices fail explicitly,
  - expand baseline-runner artifact coverage to two writes so `latest/*` overwrites while `trends/*.jsonl` grows append-only.
- next_step:
  - rerun focused compile/tests + policy checks, then request reviewer re-check.

## 2026-03-09 RAGEXEC-009 completion snapshot
- implementation:
  - baseline runner now persists per-label timestamped run artifacts, stable `latest/<label>` snapshots, and append-only `trends/<label>.jsonl` history,
  - quality gate now preserves recorded `metrics.slices` in the required slice set and fails explicitly on missing zero-sample/core coverage instead of pruning slices,
  - focused tests now cover recorded-slice preservation, explicit gate failure on missing recorded slices, and repeated artifact writes (`latest/*` overwrite + `trends/*.jsonl` append-only).
- docs_sync:
  - synced `SPEC.md`, `docs/TESTING.md`, `docs/OPERATIONS.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, and `docs/design/rag-near-ideal-task-breakdown-v1.md` to the final `RAGEXEC-009` contract.
- verification:
  - `python -m py_compile scripts/rag_eval_baseline_runner.py scripts/rag_eval_quality_gate.py backend/services/rag_eval_service.py tests/test_rag_eval_baseline_runner.py tests/test_rag_eval_quality_gate.py tests/test_rag_eval_service.py` -> `PASS`
  - `.venv\Scripts\python.exe -m pytest -q tests/test_rag_eval_baseline_runner.py tests/test_rag_eval_quality_gate.py tests/test_rag_eval_service.py` -> `PASS` (`17 passed, 3 warnings`)
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`
- review:
  - independent reviewer re-check returned `PASS`.
  - review artifact created: `coordination/reviews/ragexec-009-2026-03-09.md`.
- coordination:
  - `coordination/tasks.jsonl` updated: `RAGEXEC-009` -> `completed`.
  - `coordination/cycle-contract.json` switched to `RAGEXEC-010`.
- notes:
  - `coordination/templates/review-report.md` and `scripts/validate-review-report.*` are still absent in this repo, so the review artifact follows the established local format without automated validator support.

## 2026-03-09 RAGEXEC-010 kickoff
- role: developer
- task:
  - enforce the fail-fast CI quality workflow and document the required local sequence.
- scoped requirements:
  - keep CI on a fast public-safe lane only; no local-only corpora or Ollama dependencies in the committed workflow,
  - make the GitHub workflow fail early on policy/secret/compile/eval-contract regressions before broader smoke coverage,
  - sync `docs/TESTING.md`, `docs/OPERATIONS.md`, and `docs/USAGE.md` to the exact local and CI sequence.
- inspection_findings:
  - `.github/workflows/agent-quality-gates.yml` already runs policy, secrets, compile, smoke, and eval tests, but it does not document/express the fast-lane ordering as a distinct contract and `docs/USAGE.md` still references pre-`RAGEXEC-009` eval artifact paths.
- next_step:
  - patch workflow/docs first, then rerun the focused verification lane and request independent review.

## 2026-03-09 RAGEXEC-010 workflow/docs patch
- progress:
  - updated `.github/workflows/agent-quality-gates.yml` to express an explicit fail-fast lane order and to cancel superseded in-progress runs for the same ref,
  - updated `docs/TESTING.md`, `docs/OPERATIONS.md`, and `docs/USAGE.md` to document the public-safe CI/local sequence and the separation from local-only corpora/Ollama checks,
  - updated `docs/design/rag-near-ideal-task-breakdown-v1.md` with the concrete workflow contract for this slice.
- rationale:
  - no `SPEC.md` or `docs/REQUIREMENTS_TRACEABILITY.md` update in this slice because `RAGEXEC-010` changes CI/developer workflow only and does not alter production behavior, API, or runtime configuration.
- next_step:
  - run the required verification commands, then request independent review of the workflow failure semantics and doc sync.

## 2026-03-09 RAGEXEC-010 completion snapshot
- implementation:
  - `agent-quality-gates` now expresses a dedicated public-safe fail-fast lane: policy gate -> secret scan -> eval-tooling compile -> synthetic eval-contract tests -> smoke tests,
  - workflow concurrency now cancels superseded runs for the same ref to reduce stale CI noise,
  - `docs/TESTING.md`, `docs/OPERATIONS.md`, and `docs/USAGE.md` now document the same fast lane and keep developer-local corpora/Ollama verification explicitly outside CI.
- docs_sync:
  - updated `docs/design/rag-near-ideal-task-breakdown-v1.md` with the concrete `RAGEXEC-010` workflow contract.
  - no `SPEC.md` / `docs/REQUIREMENTS_TRACEABILITY.md` change because this slice only affects CI/developer workflow, not production behavior or runtime configuration.
- verification:
  - `python -m py_compile scripts/ci_policy_gate.py scripts/scan_secrets.py scripts/rag_eval_baseline_runner.py scripts/rag_eval_quality_gate.py tests/test_rag_eval_baseline_runner.py tests/test_rag_eval_quality_gate.py tests/test_rag_eval_dataset_contract.py` -> `PASS`
  - `.venv\Scripts\python.exe -m pytest -q tests/test_rag_eval_quality_gate.py tests/test_rag_eval_baseline_runner.py tests/test_rag_eval_dataset_contract.py` -> `PASS` (`14 passed, 3 warnings`)
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`
- review:
  - independent reviewer returned `PASS`.
  - review artifact created: `coordination/reviews/ragexec-010-2026-03-09.md`.
- coordination:
  - `coordination/tasks.jsonl` updated: `RAGEXEC-010` -> `completed`.
  - `coordination/cycle-contract.json` switched to `RAGEXEC-011`.
- notes:
  - `coordination/templates/review-report.md` and `scripts/validate-review-report.*` are still absent in this repo, so the review artifact follows the established local format without automated validator support.

## 2026-03-09 RAGEXEC-011 implementation draft
- progress:
  - canonical `kb_wiki_crawl -> waiting_wiki_root` entry now clears legacy wiki temp keys before switching to URL-input state,
  - orphan `wiki_git_load` / `wiki_zip_load` callback branches were collapsed into a redirect message back to the canonical flow instead of attempting missing-state subflows,
  - `waiting_wiki_root` completion/error path now clears legacy wiki temp keys,
  - added regressions for canonical state cleanup and stale legacy-button redirect, and synced `SPEC.md` / `docs/REQUIREMENTS_TRACEABILITY.md` / `docs/USAGE.md` / backlog doc.
- next_step:
  - run focused compile/tests and policy checks, then request independent review that no orphan wiki callback path remains.

## 2026-03-09 RAGEXEC-011 completion snapshot
- implementation:
  - `kb_wiki_crawl` is now the only reachable admin wiki-ingestion entrypoint in bot UX,
  - stale `wiki_git_load:*` / `wiki_zip_load:*` callback buttons now redirect back to the canonical URL flow instead of attempting orphan git/zip subflows,
  - canonical entry and `waiting_wiki_root` completion both clear legacy wiki temp keys.
- docs_sync:
  - updated `SPEC.md`, `docs/USAGE.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, and `docs/design/rag-near-ideal-task-breakdown-v1.md` for the canonical wiki UX contract.
- verification:
  - `python -m py_compile frontend/bot_callbacks.py frontend/bot_handlers.py tests/test_bot_text_ai_mode.py tests/test_bot_wiki_callbacks.py` -> `PASS`
  - `.venv\Scripts\python.exe -m pytest -q tests/test_bot_text_ai_mode.py tests/test_bot_wiki_callbacks.py` -> `PASS` (`13 passed`)
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`
- review:
  - independent reviewer returned `PASS`.
  - review artifact created: `coordination/reviews/ragexec-011-2026-03-09.md`.
- coordination:
  - `coordination/tasks.jsonl` updated: `RAGEXEC-011` -> `completed`.
  - `coordination/cycle-contract.json` switched to `RAGEXEC-012`.
- notes:
  - `coordination/templates/review-report.md` and `scripts/validate-review-report.*` are still absent in this repo, so the review artifact follows the established local format without automated validator support.

## 2026-03-09 RAGEXEC-012 implementation draft
- progress:
  - `shared/wiki_scraper.py` now returns explicit `crawl_mode` and `git_fallback_attempted` flags for wiki-crawl stats,
  - backend wiki-crawl API/service and bot UI paths now propagate and display the resulting sync mode,
  - regressions added for successful Gitee git sync, git-loader failure with HTML fallback, and bot-side rendering of sync mode.
- next_step:
  - rerun focused compile/tests including the changed backend/frontend files, then request independent review of fallback visibility semantics.

## 2026-03-09 RAGEXEC-012 review fix
- reviewer_feedback:
  - route-level serialization of new wiki-crawl response fields was not covered,
  - HTML-fallback wording for admin UI/callback formatter was only partially protected.
- fix_plan:
  - add `/ingestion/wiki-crawl` route regression for `crawl_mode` and `git_fallback_attempted`,
  - add direct formatter regressions for HTML-fallback wording in `frontend/bot_handlers.py` and `frontend/bot_callbacks.py`,
  - expand the documented check list for `RAGEXEC-012` to include the new tests.
- next_step:
  - rerun compile/tests + policy gates, then request reviewer re-check.

## 2026-03-09 RAGEXEC-012 completion snapshot
- implementation:
  - wiki-crawl stats now surface `crawl_mode` and `git_fallback_attempted` across scraper, service, API route, and bot UI,
  - admin wiki completion messages now show whether the result came from git-based full sync or HTML crawl fallback,
  - dedicated regressions now cover Gitee git success, git-loader failure to HTML crawl, route serialization of the new fields, and HTML-fallback wording in both handler and callback formatter paths.
- docs_sync:
  - updated `SPEC.md`, `docs/USAGE.md`, `docs/API_REFERENCE.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, and `docs/design/rag-near-ideal-task-breakdown-v1.md` for the explicit fallback-visibility contract.
- verification:
  - `python -m py_compile shared/wiki_scraper.py shared/wiki_git_loader.py backend/services/ingestion_service.py backend/api/routes/ingestion.py frontend/bot_callbacks.py frontend/bot_handlers.py tests/test_ingestion_routes.py tests/test_wiki_scraper.py tests/test_bot_text_ai_mode.py tests/test_bot_wiki_callbacks.py` -> `PASS`
  - `.venv\Scripts\python.exe -m pytest -q tests/test_ingestion_routes.py tests/test_wiki_scraper.py tests/test_bot_text_ai_mode.py tests/test_bot_wiki_callbacks.py -k wiki` -> `PASS` (`9 passed, 12 deselected, 4 warnings`)
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`
- review:
  - independent reviewer re-check returned `PASS`.
  - review artifact created: `coordination/reviews/ragexec-012-2026-03-09.md`.
- coordination:
  - `coordination/tasks.jsonl` updated: `RAGEXEC-012` -> `completed`.
  - `coordination/cycle-contract.json` switched to `RAGEXEC-013`.
- notes:
  - `coordination/templates/review-report.md` and `scripts/validate-review-report.*` are still absent in this repo, so the review artifact follows the established local format without automated validator support.

## 2026-03-09 RAG quality metrics audit + external-ideas scan kickoff
- role: team-lead-orchestrator / architect
- classification: non-trivial
- task:
  - report current status of answer-quality tests / metric computation / metric comparison,
  - inspect `C:\Users\devl\proj\test\` for reusable RAG ideas before the next implementation slice.
- findings_so_far:
  - current committed eval runtime in `backend/services/rag_eval_service.py` is still retrieval-only and computes `recall_at_10`, `mrr_at_10`, and `ndcg_at_10`,
  - committed baseline/gate tooling in `scripts/rag_eval_baseline_runner.py` and `scripts/rag_eval_quality_gate.py` already supports slice-aware run/latest/trend artifact comparison for retrieval metrics,
  - answer-level metrics from the approved security-first eval design are not implemented yet: no Ollama answer/judge loop, no faithfulness/relevancy/citation/refusal/security scoring in the runtime eval service,
  - external project scan is now in progress to look for reusable local-eval and security patterns before implementation resumes.
- next_step:
  - inspect `C:\Users\devl\proj\test\` read-only, summarize reusable ideas, then map them to the remaining `RAGEXEC-013..018` slices and the likely new answer-metric implementation slice.

## 2026-03-09 RAG quality metrics audit + external-ideas scan findings
- status:
  - current repo has retrieval-only eval metrics and mature baseline/gate comparison for those metrics,
  - current repo does not yet have answer-level judging or Ollama-backed quality scoring in the committed evaluator,
  - external local project analysis is complete and its reusable ideas were recorded in `.scratchpad/research.md` and `.scratchpad/plan.md`.
- reusable findings from `C:\Users\devl\proj\test\`:
  - controlled query rewriting + multi-query retrieval are implemented cleanly there and remain missing from our backlog as an explicit implementation slice,
  - structural fields such as `paragraph_numbers` / `chunk_type` plus `StructuralIndex(paragraph_number -> chunk_id[])` reinforce the current `RAGEXEC-013..015` direction,
  - sentence-level evidence packing, list-coverage checks, and sibling-paragraph merge map directly onto our planned `RAGEXEC-016..017` work,
  - answer-quality loop there blends heuristics + optional LLM judge + weighted rollups, which is a useful shape for the local-only eval harness,
  - grounded/security rules there highlight two still-missing areas for our repo: ingestion-time malicious-document screening and answer-level security scoring.
- next_step:
  - report the status/gaps to the user with file-backed references, note that `RAGEXEC-013` is the next approved design checkpoint, and propose the follow-on slice for answer-level metrics after the chunk/evidence-pack work.

## 2026-03-09 RAG quality metrics audit verification
- verification:
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`
- completion_note:
  - this step remained analysis/planning only; no runtime or production behavior changed.

## 2026-03-09 RAGEXEC-013 implementation kickoff
- role: developer
- approval:
  - user approved `docs/design/rag-runtime-canonical-chunk-contract-v1.md` with `APPROVED:v1`
  - recorded in `coordination/approval-overrides.json`
- checklist:
  - [done] mark `RAGEXEC-013` in progress and sync coordination artifacts
  - [pending] add additive canonical chunk columns to `knowledge_chunks` and startup migration hooks
  - [pending] dual-write canonical chunk metadata/columns from `IngestionService` into `rag_system` chunk rows
  - [pending] extend focused ingestion/outbox regressions for canonical field contract
  - [pending] sync `SPEC.md`, `docs/OPERATIONS.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, and backlog notes
  - [pending] run required verification, then request independent review artifact
- scope_guard:
  - keep this slice limited to canonical chunk contract plumbing only; query rewriting, multi-query retrieval, and answer-level metrics stay for later slices unless a tiny hook is naturally required here.

## 2026-03-09 RAGEXEC-013 implementation draft
- progress:
  - added additive canonical chunk columns to `shared/database.py` plus startup migration hooks,
  - extended `IngestionService` with canonical metadata normalization helpers (`section_path_norm`, `chunk_hash`, `chunk_no`, `token_count_est`, parser fields, sanitized warnings),
  - switched `IngestionService` write paths to emit `metadata`, `metadata_json`, and `chunk_columns`,
  - extended `shared/rag_system.py` write path to persist canonical scalar columns and default `metadata_json` to the effective metadata payload,
  - updated focused ingestion metadata/outbox tests for the new contract.
- next_step:
  - run compile + targeted pytest, fix any runtime/test regressions, then sync docs/spec artifacts.

## 2026-03-09 RAGEXEC-013 review blockers
- reviewer verdict:
  - independent review returned `FAIL` with three MUST-FIX items before this slice can be closed.
- must_fix:
  - widen `parser_warning` redaction beyond URL credentials to bearer/password-style secrets,
  - guarantee canonical chunk fields for direct wiki `rag_system.add_chunk(...)` write paths so new chunks do not land with `chunk_no == NULL`,
  - add a migration regression that exercises `migrate_database()` against a pre-change `knowledge_chunks` table.
- fix_plan:
  - patch the sanitizer in both `backend/services/ingestion_service.py` and `shared/rag_system.py`, plus add focused masking assertions,
  - add a reusable canonical metadata helper in `shared/rag_system.py` and route wiki HTML/git/zip writes through it with explicit per-page/per-file `chunk_no`,
  - add a temporary-SQLite migration test and then rerun the required compile/pytest/policy commands before requesting reviewer re-check.

## 2026-03-09 RAGEXEC-013 review-fix draft
- progress:
  - `parser_warning` sanitizers in both ingestion/runtime paths now redact URL credentials, auth headers, standalone bearer tokens, and common password/token/api-key key-value secrets,
  - `shared/rag_system.py` now canonicalizes direct `add_chunk`/`add_chunks_batch` metadata before persistence so JSON + scalar columns stay aligned even on non-IngestionService write paths,
  - wiki HTML/git/zip loaders now enumerate per-source `chunk_no` and pass canonical metadata payloads into direct `add_chunk` writes,
  - focused regressions were added for secret masking, wiki canonical chunk payloads, and additive `migrate_database()` coverage on a legacy `knowledge_chunks` table,
  - `SPEC.md`, `docs/OPERATIONS.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, and `docs/design/rag-runtime-canonical-chunk-contract-v1.md` now match the hardened direct-write + redaction + migration contract.
- next_step:
  - run the full verification lane, then request independent reviewer re-check before marking `RAGEXEC-013` complete.

## 2026-03-09 RAGEXEC-013 reviewer re-check blocker
- reviewer_feedback:
  - re-review still returned `FAIL` because direct-path fallback `chunk_hash` in `shared/rag_system.py` remained position-insensitive and ignored `chunk_no`.
- fix_plan:
  - make runtime fallback hash include `source_type + source_path + chunk_no + normalized content`,
  - add wiki direct-write regressions proving `chunk_hash` changes when only `chunk_no` changes,
  - rerun the same verification lane and request one more independent re-check.

## 2026-03-09 RAGEXEC-013 final blocker fix
- progress:
  - runtime fallback `chunk_hash` in `shared/rag_system.py` now includes `chunk_no`,
  - wiki HTML/git/zip regression tests now assert distinct `chunk_hash` values for identical content at different positions,
  - focused verification re-passed after the hash fix.
- verification:
  - `python -m py_compile shared/rag_system.py tests/test_wiki_scraper.py` -> `PASS`
  - `.venv\Scripts\python.exe -m pytest -q tests/test_ingestion_metadata_contract.py tests/test_ingestion_outbox.py tests/test_wiki_scraper.py` -> `PASS` (`16 passed, 3 warnings`)
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`
- next_step:
  - wait for the reviewer re-check; if `PASS`, write the review artifact and close `RAGEXEC-013`.

## 2026-03-09 RAGEXEC-013 completion snapshot
- implementation:
  - additive canonical chunk columns are now present in `knowledge_chunks`, with startup migration coverage on a legacy table shape,
  - runtime ingestion and direct wiki HTML/git/zip write paths now dual-write canonical chunk metadata into JSON and scalar columns,
  - `parser_warning` redaction now covers URL credentials, auth headers, bearer tokens, and password/token/api-key value leaks,
  - fallback `chunk_hash` semantics now include `chunk_no`, matching the approved canonical contract.
- docs_sync:
  - updated `SPEC.md`, `docs/OPERATIONS.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, `docs/design/rag-runtime-canonical-chunk-contract-v1.md`, and `docs/design/rag-near-ideal-task-breakdown-v1.md`.
- verification:
  - `python -m py_compile backend/services/ingestion_service.py shared/rag_system.py shared/wiki_scraper.py shared/wiki_git_loader.py shared/database.py tests/test_ingestion_metadata_contract.py tests/test_ingestion_outbox.py tests/test_wiki_scraper.py` -> `PASS`
  - `.venv\Scripts\python.exe -m pytest -q tests/test_ingestion_metadata_contract.py tests/test_ingestion_outbox.py tests/test_wiki_scraper.py` -> `PASS` (`16 passed, 3 warnings`)
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`
- review:
  - independent reviewer re-check returned `PASS`.
  - review artifact created: `coordination/reviews/ragexec-013-2026-03-09.md`.
- coordination:
  - `coordination/tasks.jsonl` updated: `RAGEXEC-013` -> `completed`.
  - `coordination/cycle-contract.json` switched to `RAGEXEC-014`.

## 2026-03-09 Backlog extension for missing quality ideas
- user_direction:
  - user explicitly requested that still-missing answer-quality ideas be added rather than dropped.
- updates:
  - added `RAGEXEC-019` for controlled query rewriting + multi-query retrieval,
  - added `RAGEXEC-020` for local-only answer-level judge metrics and commit-to-commit deltas,
  - recorded the design/backlog approval in `coordination/approval-overrides.json`.

## 2026-03-09 RAGEXEC-014 kickoff
- role: developer
- task:
  - improve PDF/DOCX parser fidelity and structural chunk metadata.
- checklist:
  - [done] sync `RAGEXEC-014` as active slice in coordination artifacts
  - [in_progress] inspect current PDF/DOCX loaders and shared chunking helpers for structural loss points
  - [pending] implement richer PDF/DOCX metadata (`doc_title`, `section_path`, `chunk_no`, parser profile, offsets/paragraph spans where possible)
  - [pending] add focused parser fidelity regressions
  - [pending] sync `SPEC.md` and `docs/REQUIREMENTS_TRACEABILITY.md`
  - [pending] run required verification and request independent review
- findings_so_far:
  - `shared/document_loaders/pdf_loader.py` currently emits only `type`, `page`, `chunk_kind=list`, and sometimes `section_title`; it does not emit `doc_title`, `section_path`, `chunk_no`, `parser_profile`, or offsets.
  - `shared/document_loaders/word_loader.py` currently emits only `type` and `section_title`; it loses heading hierarchy, `doc_title`, `section_path`, `chunk_kind`, `chunk_no`, and parser metadata.
  - `shared/document_loaders/chunking.py` returns plain strings only, so parser fidelity improvements need a richer helper that preserves char ranges and inferred block kind.
- next_step:
  - implement a structured chunking helper and wire PDF/Word loaders onto it with deterministic metadata.

## 2026-03-09 RAGEXEC-014 implementation draft
- progress:
  - added `split_text_structurally_with_metadata()` plus `infer_structural_chunk_kind()` in `shared/document_loaders/chunking.py`,
  - `shared/document_loaders/pdf_loader.py` now normalizes noisy PDF line breaks and emits `doc_title`, `section_title`, `section_path`, `page_no`, `page_chunk_no`, `chunk_no`, `char_start`, `char_end`, and `parser_profile=loader:pdf:v2`,
  - `shared/document_loaders/word_loader.py` now preserves heading hierarchy into `section_path`, tracks paragraph spans, keeps deterministic `chunk_no`, infers list formatting from paragraph styles, and emits `parser_profile=loader:word:v2`,
  - added focused parser regressions in `tests/test_pdf_word_loader_metadata.py`,
  - synced active slice commands/artifacts in `coordination/cycle-contract.json` and mapped the new loader contract in `SPEC.md` + `docs/REQUIREMENTS_TRACEABILITY.md`.
- focused_verification:
  - `python -m py_compile shared/document_loaders/pdf_loader.py shared/document_loaders/word_loader.py shared/document_loaders/chunking.py tests/test_ingestion_metadata_contract.py tests/test_markdown_loader_metadata_contract.py tests/test_pdf_word_loader_metadata.py` -> `PASS`
  - `.venv\Scripts\python.exe -m pytest -q tests/test_ingestion_metadata_contract.py tests/test_markdown_loader_metadata_contract.py tests/test_pdf_word_loader_metadata.py` -> `PASS` (`8 passed, 3 warnings`)
- next_step:
  - run the full verification lane for this slice and then request independent review focused on structure retention, not just metadata presence.

## 2026-03-09 RAGEXEC-014 completion snapshot
- implementation:
  - added structured chunk records with char spans and inferred chunk kind in `shared/document_loaders/chunking.py`,
  - PDF loader now normalizes noisy extracted lines into page-aware structural blocks and emits `doc_title`, `section_title`, `section_path`, `page_no`, `page_chunk_no`, `chunk_no`, `char_start`, `char_end`, and `parser_profile=loader:pdf:v2`,
  - DOCX loader now preserves heading hierarchy into `section_path`, tracks paragraph spans, infers list formatting from paragraph styles, and emits deterministic `chunk_no` plus `parser_profile=loader:word:v2`,
  - added parser regressions in `tests/test_pdf_word_loader_metadata.py`.
- docs_sync:
  - updated `SPEC.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, `docs/design/rag-near-ideal-task-breakdown-v1.md`, and `coordination/cycle-contract.json`.
- verification:
  - `python -m py_compile shared/document_loaders/pdf_loader.py shared/document_loaders/word_loader.py shared/document_loaders/chunking.py tests/test_ingestion_metadata_contract.py tests/test_markdown_loader_metadata_contract.py tests/test_pdf_word_loader_metadata.py` -> `PASS`
  - `.venv\Scripts\python.exe -m pytest -q tests/test_ingestion_metadata_contract.py tests/test_markdown_loader_metadata_contract.py tests/test_pdf_word_loader_metadata.py` -> `PASS` (`8 passed, 3 warnings`)
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`
- review:
  - independent review returned `PASS`.
  - review artifact created: `coordination/reviews/ragexec-014-2026-03-09.md`.
- coordination:
  - `coordination/tasks.jsonl` updated: `RAGEXEC-014` -> `completed`.
  - `coordination/cycle-contract.json` switched to `RAGEXEC-015`.

## 2026-03-09 RAGEXEC-015 kickoff
- role: developer
- task:
  - normalize web/wiki/code structure and stable source semantics.
- checklist:
  - [done] switch active slice to `RAGEXEC-015`
  - [in_progress] inspect `web_loader`, `code_loader`, `wiki_git_loader`, and existing regressions for structural-loss points
  - [pending] normalize web loader section/doc metadata and chunk numbering
  - [pending] improve code loader chunk-level semantic metadata and spans
  - [pending] fix wiki git/zip path normalization on Windows and align metadata semantics
  - [pending] add focused web/code/wiki regressions
  - [pending] sync `SPEC.md` and `docs/REQUIREMENTS_TRACEABILITY.md`
  - [pending] run verification and request independent review
- findings_so_far:
  - `shared/document_loaders/web_loader.py` lacks stable `doc_title`, `section_path`, `chunk_no`, `parser_profile`, and char spans outside markdown-only headings.
  - `shared/document_loaders/code_loader.py` still uses flat `section_title=doc_title` for every chunk and does not expose chunk-level spans or semantic titles.
  - `shared/wiki_git_loader.py` still propagates OS-native relative paths, which can leak backslashes into restored wiki URLs and `file_path` metadata on Windows.
- next_step:
  - implement targeted normalization in those three source families and back it with focused tests.

## 2026-03-09 RAGEXEC-015 implementation draft
- progress:
  - `shared/document_loaders/web_loader.py` now emits stable `doc_title`, hierarchical `section_path`, deterministic `chunk_no`, parser profile, and char spans for section/fixed chunk paths,
  - `shared/document_loaders/code_loader.py` now emits chunk-level semantic titles/paths from primary symbols, keeps `file_path`, parser profile, and char spans, while preserving full-file symbol lists for compatibility,
  - `shared/wiki_git_loader.py` now normalizes repo-relative paths to forward slashes, prevents Windows backslashes from leaking into wiki URLs, and prefixes page identity into wiki chunk metadata,
  - added focused regressions in `tests/test_web_loader_structure.py`, extended `tests/test_code_loader.py`, and extended `tests/test_wiki_scraper.py`,
  - synced `coordination/cycle-contract.json`, `SPEC.md`, and `docs/REQUIREMENTS_TRACEABILITY.md` to the new normalization contract.
- focused_verification:
  - `python -m py_compile shared/document_loaders/web_loader.py shared/document_loaders/code_loader.py shared/wiki_git_loader.py backend/services/ingestion_service.py tests/test_code_loader.py tests/test_wiki_scraper.py tests/test_web_loader_structure.py` -> `PASS`
  - `.venv\Scripts\python.exe -m pytest -q tests/test_code_loader.py tests/test_wiki_scraper.py tests/test_web_loader_structure.py` -> `PASS` (`11 passed, 3 warnings`)
- next_step:
  - run the full verification lane including `tests/test_markdown_loader_metadata_contract.py`, then request independent review focused on structural benefit instead of field-count growth.

## 2026-03-09 RAGEXEC-015 review blocker
- reviewer_feedback:
  - independent review returned `FAIL` because ZIP-based wiki imports still preserved temp-file `doc_title` from the real `MarkdownLoader`, so stable wiki page identity was not actually guaranteed.
- fix_plan:
  - force wiki `doc_title` to come from the normalized wiki page path instead of preserving loader-local temp filenames,
  - add a regression that uses a real ZIP and the real markdown loader path rather than mocked metadata,
  - rerun verification and request reviewer re-check.

## 2026-03-09 RAGEXEC-015 blocker fix
- progress:
  - wiki git/zip metadata decoration now always derives `doc_title` from the normalized wiki page path,
  - added a real ZIP regression using the real markdown loader path so temp-file `doc_title` leaks are caught,
  - full verification lane re-passed after the fix.
- verification:
  - `python -m py_compile shared/document_loaders/web_loader.py shared/document_loaders/code_loader.py shared/wiki_git_loader.py backend/services/ingestion_service.py tests/test_code_loader.py tests/test_markdown_loader_metadata_contract.py tests/test_wiki_scraper.py tests/test_web_loader_structure.py` -> `PASS`
  - `.venv\Scripts\python.exe -m pytest -q tests/test_code_loader.py tests/test_markdown_loader_metadata_contract.py tests/test_wiki_scraper.py tests/test_web_loader_structure.py` -> `PASS` (`13 passed, 3 warnings`)
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`
- next_step:
  - wait for reviewer re-check; if `PASS`, close `RAGEXEC-015` and move to `RAGEXEC-016`.

## 2026-03-09 RAGEXEC-015 completion snapshot
- implementation:
  - `shared/document_loaders/web_loader.py` now preserves stable `doc_title`, heading-based `section_path`, deterministic `chunk_no`, parser profile, and char spans,
  - `shared/document_loaders/code_loader.py` now preserves chunk-level semantic titles/paths from primary symbols plus chunk spans and stable file metadata,
  - `shared/wiki_git_loader.py` now normalizes wiki file/page paths to forward slashes and forces stable wiki `doc_title` from page identity even on ZIP temp-file imports,
  - added `tests/test_web_loader_structure.py` and extended `tests/test_code_loader.py` / `tests/test_wiki_scraper.py`.
- docs_sync:
  - updated `SPEC.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, `docs/design/rag-near-ideal-task-breakdown-v1.md`, and `coordination/cycle-contract.json`.
- verification:
  - `python -m py_compile shared/document_loaders/web_loader.py shared/document_loaders/code_loader.py shared/wiki_git_loader.py backend/services/ingestion_service.py tests/test_code_loader.py tests/test_markdown_loader_metadata_contract.py tests/test_wiki_scraper.py tests/test_web_loader_structure.py` -> `PASS`
  - `.venv\Scripts\python.exe -m pytest -q tests/test_code_loader.py tests/test_markdown_loader_metadata_contract.py tests/test_wiki_scraper.py tests/test_web_loader_structure.py` -> `PASS` (`13 passed, 3 warnings`)
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`
- review:
  - independent review returned `PASS`.
  - review artifact created: `coordination/reviews/ragexec-015-2026-03-09.md`.
- coordination:
  - `coordination/tasks.jsonl` updated: `RAGEXEC-015` -> `completed`.
  - `coordination/cycle-contract.json` switched to `RAGEXEC-016`.

## 2026-03-09 RAGEXEC-016 kickoff
- role: developer
- task:
  - replace simple top-chunk assembly with deterministic evidence-pack composition.
- checklist:
  - [done] sync `RAGEXEC-016` as the active slice in coordination artifacts
  - [in_progress] inspect current context assembly in `backend/api/routes/rag.py` and reusable helpers in `shared/rag_system.py`
  - [pending] implement evidence-pack composition with primary evidence, structural neighbors, and section-aware expansion under a stable budget
  - [pending] add focused context assembly regressions without committing local corpora or answer fixtures
  - [pending] sync `SPEC.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, and `docs/design/rag-near-ideal-task-breakdown-v1.md`
  - [pending] run required verification and request independent review
- findings_so_far:
  - current generalized path mostly joins `filtered_results[:top_k_for_context]` directly, while structural neighbor expansion exists only for legacy `HOWTO`,
  - canonical metadata added in `RAGEXEC-013..015` now gives enough signal (`chunk_no`, `section_path`, `doc_title`, spans, parser profiles) to build a deterministic evidence-pack layer,
  - external local prototype patterns confirm the right shape: anchor chunk first, same-paragraph/section siblings second, and bounded sentence/list packing for factoid/list queries.
- next_step:
  - promote context selection to explicit evidence-pack helpers in `rag.py`, keeping retrieval scope unchanged and deferring richer context diagnostics to `RAGEXEC-017`.

## 2026-03-09 RAGEXEC-016 implementation draft
- progress:
  - added reusable context helpers in `shared/rag_system.py` for canonical row description plus query-focused excerpt selection that preserves numeric clause markers and trims noisy metric/list blocks,
  - refactored `backend/api/routes/rag.py` to use one deterministic evidence-pack selector for both `/rag/query` and `/rag/summary`: anchor chunk first, same-document structural support next, bounded by explicit anchor/block limits instead of intent-specific assembly branches,
  - kept retrieval scope and diagnostics schema unchanged in this slice; only final context composition changed,
  - added focused regressions in `tests/test_rag_context_composer.py` for same-section support, metric-sentence focusing, and summary-path excerpt selection.
- focused_verification:
  - `python -m py_compile backend/api/routes/rag.py shared/rag_system.py tests/test_rag_query_definition_intent.py tests/test_rag_context_composer.py tests/test_rag_summary_modes.py tests/test_rag_summary_date_filter.py` -> `PASS`
  - `.venv\\Scripts\\python.exe -m pytest -q tests/test_rag_query_definition_intent.py tests/test_rag_context_composer.py tests/test_rag_summary_modes.py tests/test_rag_summary_date_filter.py` -> `PASS` (`15 passed, 4 warnings`)
- next_step:
  - run the required cycle verification commands, sync `coordination/tasks.jsonl` / `cycle-contract.json`, and request independent review focused on explainability and bounded context selection.

## 2026-03-10 RAGEXEC-016 blocker-fix verification
- progress:
  - reviewer blockers were fixed in `backend/api/routes/rag.py` by making evidence-pack selection reserve row budget for anchor rows before support rows and by scoping `RAGAnswer.sources` to `included_context_rows`,
  - added focused regressions in `tests/test_rag_context_composer.py` for source-pack alignment and anchor-first ordering under a tight context budget,
  - reran focused compile/pytest after the fix and both passed.
- focused_verification:
  - `python -m py_compile backend/api/routes/rag.py shared/rag_system.py tests/test_rag_query_definition_intent.py tests/test_rag_context_composer.py tests/test_rag_summary_modes.py tests/test_rag_summary_date_filter.py` -> `PASS`
  - `.venv\\Scripts\\python.exe -m pytest -q tests/test_rag_query_definition_intent.py tests/test_rag_context_composer.py tests/test_rag_summary_modes.py tests/test_rag_summary_date_filter.py` -> `PASS` (`15 passed, 4 warnings`)
- next_step:
  - rerun the required cycle gates, request independent re-review from `Singer`, and only then close `RAGEXEC-016`.

## 2026-03-10 RAGEXEC-016 completion snapshot
- implementation:
  - `backend/api/routes/rag.py` now composes final context through one evidence-pack selector for both `/rag/query` and `/rag/summary`, with anchor rows reserved ahead of support rows under the context budget,
  - `/rag/query` source metadata now reflects only the final included evidence pack instead of broader filtered retrieval results,
  - `shared/rag_system.py` now provides query-focused excerpt helpers that preserve clause markers and trim noisy metric/list chunks to the query-relevant sentence window,
  - `tests/test_rag_context_composer.py` now covers same-section support, metric-sentence focusing, summary excerpting, source alignment, and anchor-first ordering under tight budgets.
- docs_sync:
  - updated `SPEC.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, and `docs/design/rag-near-ideal-task-breakdown-v1.md`.
- verification:
  - `python -m py_compile backend/api/routes/rag.py shared/rag_system.py tests/test_rag_query_definition_intent.py tests/test_rag_context_composer.py tests/test_rag_summary_modes.py tests/test_rag_summary_date_filter.py` -> `PASS`
  - `.venv\\Scripts\\python.exe -m pytest -q tests/test_rag_query_definition_intent.py tests/test_rag_context_composer.py tests/test_rag_summary_modes.py tests/test_rag_summary_date_filter.py` -> `PASS` (`16 passed, 4 warnings`)
  - `python -m py_compile backend/api/routes/rag.py shared/rag_system.py tests/test_rag_query_definition_intent.py` -> `PASS`
  - `.venv\\Scripts\\python.exe -m pytest -q tests/test_rag_query_definition_intent.py` -> `PASS` (`9 passed, 4 warnings`)
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`
- review:
  - independent reviewer `Singer` re-check returned `PASS`.
  - review artifact created: `coordination/reviews/ragexec-016-2026-03-10.md`.
- coordination:
  - `coordination/tasks.jsonl` updated: `RAGEXEC-016` -> `completed`.
  - `coordination/cycle-contract.json` switched to `RAGEXEC-017`.

## 2026-03-10 RAGEXEC-017 implementation draft
- progress:
  - `backend/api/routes/rag.py` now merges retrieval candidates with final evidence-pack support rows before persistence, storing context-decision trace in `metadata_json` and exposing support-only rows under synthetic `origin/channel=context_support`,
  - `backend/schemas/rag.py` extends `RAGDiagnosticsCandidate` with `included_in_context`, `context_rank`, `context_reason`, and `context_anchor_rank`,
  - `tests/test_rag_diagnostics.py` now covers support-row visibility and public-metadata stripping, and `tests/test_rag_diagnostics_contract.py` now locks the schema/default behavior for older rows,
  - focused compile + diagnostics pytest lane passed after the runtime/schema changes.
- focused_verification:
  - `python -m py_compile backend/api/routes/rag.py backend/schemas/rag.py tests/test_rag_diagnostics.py tests/test_rag_diagnostics_contract.py tests/test_api_routes_contract.py` -> `PASS`
  - `.venv\\Scripts\\python.exe -m pytest -q tests/test_rag_diagnostics.py tests/test_rag_diagnostics_contract.py tests/test_api_routes_contract.py` -> `PASS` (`10 passed, 14 warnings`)
- next_step:
  - sync `SPEC.md`, `docs/API_REFERENCE.md`, `docs/OPERATIONS.md`, and `docs/REQUIREMENTS_TRACEABILITY.md`, then run the required cycle gate and request independent review.

## 2026-03-10 RAGEXEC-017 blocker-fix verification
- progress:
  - fixed duplicate logical-chunk handling in `_select_evidence_pack_rows()` so fallback retrieval copies no longer re-enter the final context after the DB-backed anchor row is already selected,
  - reordered diagnostics persistence so final included context rows are emitted first, guaranteeing `context_support` rows survive the 20-candidate persistence cap,
  - added a regression in `tests/test_rag_diagnostics.py` for the >20 retrieval-candidate scenario and reran both the focused diagnostics suite and the required cycle gate successfully.
- focused_verification:
  - `python -m py_compile backend/api/routes/rag.py backend/schemas/rag.py tests/test_rag_diagnostics.py tests/test_rag_diagnostics_contract.py tests/test_api_routes_contract.py` -> `PASS`
  - `.venv\\Scripts\\python.exe -m pytest -q tests/test_rag_diagnostics.py tests/test_rag_diagnostics_contract.py tests/test_api_routes_contract.py` -> `PASS` (`11 passed, 14 warnings`)
  - `python -m py_compile backend/api/routes/rag.py backend/schemas/rag.py tests/test_rag_diagnostics.py tests/test_api_routes_contract.py` -> `PASS`
  - `.venv\\Scripts\\python.exe -m pytest -q tests/test_rag_diagnostics.py tests/test_api_routes_contract.py` -> `PASS` (`8 passed, 12 warnings`)
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`
- next_step:
  - request independent re-review from `Singer`; if it passes, close `RAGEXEC-017` and move the contract to `RAGEXEC-018`.

## 2026-03-10 RAGEXEC-017 completion snapshot
- implementation:
  - `backend/api/routes/rag.py` now persists a merged diagnostics trace where final included evidence-pack rows are emitted first, support-only rows can appear as synthetic `context_support` candidates, and public diagnostics strip the internal `_diag_context_*` persistence markers from returned metadata,
  - `backend/schemas/rag.py` now exposes `included_in_context`, `context_rank`, `context_reason`, and `context_anchor_rank` on each diagnostics candidate,
  - `tests/test_rag_diagnostics.py` now covers support-row visibility, metadata stripping, and the >20 retrieval-candidate persistence-cap scenario; `tests/test_rag_diagnostics_contract.py` locks the schema/default behavior for older rows.
- docs_sync:
  - updated `SPEC.md`, `docs/API_REFERENCE.md`, `docs/OPERATIONS.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, and `docs/design/rag-near-ideal-task-breakdown-v1.md`.
- verification:
  - `python -m py_compile backend/api/routes/rag.py backend/schemas/rag.py tests/test_rag_diagnostics.py tests/test_rag_diagnostics_contract.py tests/test_api_routes_contract.py` -> `PASS`
  - `.venv\\Scripts\\python.exe -m pytest -q tests/test_rag_diagnostics.py tests/test_rag_diagnostics_contract.py tests/test_api_routes_contract.py` -> `PASS` (`11 passed, 14 warnings`)
  - `python -m py_compile backend/api/routes/rag.py backend/schemas/rag.py tests/test_rag_diagnostics.py tests/test_api_routes_contract.py` -> `PASS`
  - `.venv\\Scripts\\python.exe -m pytest -q tests/test_rag_diagnostics.py tests/test_api_routes_contract.py` -> `PASS` (`8 passed, 12 warnings`)
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`
- review:
  - independent reviewer `Singer` re-check returned `PASS`.
  - review artifact created: `coordination/reviews/ragexec-017-2026-03-10.md`.
- coordination:
  - `coordination/tasks.jsonl` updated: `RAGEXEC-017` -> `completed`.
  - `coordination/cycle-contract.json` switched to `RAGEXEC-018`.

## 2026-03-10 RAGEXEC-018 implementation draft
- progress:
  - `backend/services/rag_eval_service.py` now treats source-family and failure-mode slices as first-class threshold targets, with per-slice threshold policies persisted into `metrics.slice_thresholds` and applied to each `RAGEvalResult.threshold_value`,
  - eval runs now publish `failure_modes` alongside `source_families` and `security_scenarios`, using `security_expectation` values such as `refuse_prompt_leak`, `flag_poisoned_context`, and `redact_sensitive`,
  - `scripts/rag_eval_quality_gate.py` now derives required slices from `failure_modes`, groups them separately in the gate output, and prefers persisted per-slice thresholds over the flat CLI defaults,
  - `scripts/rag_eval_baseline_runner.py` now renders a dedicated `Failure Modes` section in markdown artifacts,
  - focused regressions were added across `tests/test_rag_eval_service.py`, `tests/test_rag_eval_quality_gate.py`, and `tests/test_rag_eval_baseline_runner.py`.
- focused_verification:
  - `python -m py_compile backend/services/rag_eval_service.py scripts/rag_eval_quality_gate.py scripts/rag_eval_baseline_runner.py tests/test_rag_eval_service.py tests/test_rag_eval_quality_gate.py tests/test_rag_eval_baseline_runner.py` -> `PASS`
  - `.venv\\Scripts\\python.exe -m pytest -q tests/test_rag_eval_service.py tests/test_rag_eval_quality_gate.py tests/test_rag_eval_baseline_runner.py` -> `PASS` (`20 passed, 3 warnings`)
- next_step:
  - sync `SPEC.md`, `docs/TESTING.md`, `docs/OPERATIONS.md`, and `docs/REQUIREMENTS_TRACEABILITY.md`, then run the required cycle gate and request independent review.

## 2026-03-10 RAGEXEC-018 gate-ready snapshot
- progress:
  - docs/spec/traceability are now synced to the slice-aware threshold contract,
  - `coordination/tasks.jsonl` marks `RAGEXEC-018` as `in_progress`,
  - the required compile/pytest/secret-scan/policy-gate lane passed on the updated eval tooling.
- verification:
  - `python -m py_compile backend/services/rag_eval_service.py scripts/rag_eval_quality_gate.py tests/test_rag_eval_service.py tests/test_rag_eval_quality_gate.py tests/test_rag_eval_baseline_runner.py` -> `PASS`
  - `.venv\\Scripts\\python.exe -m pytest -q tests/test_rag_eval_service.py tests/test_rag_eval_quality_gate.py tests/test_rag_eval_baseline_runner.py` -> `PASS` (`20 passed, 3 warnings`)
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`
- next_step:
  - request independent review focused on source-family/failure-mode threshold semantics and artifact grouping.

## 2026-03-10 RAGEXEC-018 completion snapshot
- implementation:
  - `backend/services/rag_eval_service.py` now persists `metrics.slice_thresholds` plus `failure_modes`, applies source-family/failure-mode threshold policy to each result row, and includes failure-mode slices derived from `security_expectation`,
  - `scripts/rag_eval_quality_gate.py` now derives required slices from `failure_modes`, reports a dedicated `failure_modes` group, and honors persisted per-slice thresholds before falling back to flat CLI defaults,
  - `scripts/rag_eval_baseline_runner.py` now renders `## Failure Modes` in markdown artifacts alongside source-family and security summaries,
  - focused regressions lock slice-aware thresholds, failure-mode grouping, and baseline artifact rendering.
- docs_sync:
  - updated `SPEC.md`, `docs/TESTING.md`, `docs/OPERATIONS.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, and `docs/design/rag-near-ideal-task-breakdown-v1.md`.
- verification:
  - `python -m py_compile backend/services/rag_eval_service.py scripts/rag_eval_quality_gate.py scripts/rag_eval_baseline_runner.py tests/test_rag_eval_service.py tests/test_rag_eval_quality_gate.py tests/test_rag_eval_baseline_runner.py` -> `PASS`
  - `.venv\\Scripts\\python.exe -m pytest -q tests/test_rag_eval_service.py tests/test_rag_eval_quality_gate.py tests/test_rag_eval_baseline_runner.py` -> `PASS` (`20 passed, 3 warnings`)
  - `python -m py_compile backend/services/rag_eval_service.py scripts/rag_eval_quality_gate.py tests/test_rag_eval_service.py tests/test_rag_eval_quality_gate.py tests/test_rag_eval_baseline_runner.py` -> `PASS`
  - `.venv\\Scripts\\python.exe -m pytest -q tests/test_rag_eval_service.py tests/test_rag_eval_quality_gate.py tests/test_rag_eval_baseline_runner.py` -> `PASS` (`20 passed, 3 warnings`)
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`
- review:
  - independent reviewer `Singer` returned `PASS`.
  - review artifact created: `coordination/reviews/ragexec-018-2026-03-10.md`.
- coordination:
  - `coordination/tasks.jsonl` updated: `RAGEXEC-018` -> `completed`.
  - `coordination/cycle-contract.json` switched to `RAGEXEC-019`.

## 2026-03-10 RAGEXEC-019 implementation kickoff
- progress:
  - scope confirmed: add bounded, corpus-agnostic query rewriting plus multi-query retrieval only for the generalized retrieval path, without reintroducing hidden ranking boosts or LLM-driven rewrites,
  - implementation target narrowed to `backend/api/routes/rag.py` and `shared/rag_system.py`, with a new focused regression file `tests/test_rag_query_rewrite.py`,
  - current eval/review contract remains unchanged: required lane is compile + `tests/test_rag_query_definition_intent.py`, `tests/test_rag_quality.py`, `tests/test_rag_query_rewrite.py`, followed by `scan_secrets` and `ci_policy_gate`.
- next_step:
  - implement canonical rewrite variants and stable multi-query candidate fusion, then add regressions for bounded fan-out and rewrite-only recall gains before syncing docs and requesting independent review.

## 2026-03-10 RAGEXEC-019 completion snapshot
- implementation:
  - `backend/api/routes/rag.py` now derives canonical query hints up front, gates rewrites through `_should_enable_controlled_rewrites()`, and uses bounded generalized multi-query fan-out only for short/ambiguous queries; long explicit fact/metric prompts stay on the original single-query path,
  - generalized `/rag/query` now tags per-variant retrieval results (`query_variant_mode/query/query_variant_rank`) and ranks fused candidates by `multi_query_score` when stable-identity aggregation is present, while legacy rollback mode remains unchanged,
  - `shared/rag_system.py` now exposes `merge_multi_query_candidates()` to dedupe rewrite hits by `describe_context_chunk(...).identity` and accumulate bounded reciprocal-rank-style fusion signals without corpus-specific weighting,
  - `tests/test_rag_query_rewrite.py` now covers bounded variant generation, stable fusion deduplication, rewrite-only recall gain in generalized mode, legacy single-query rollback, and the long explicit fact-query no-rewrite guard.
- docs_sync:
  - updated `SPEC.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, `docs/OPERATIONS.md`, and `docs/design/rag-near-ideal-task-breakdown-v1.md`.
- verification:
  - `python -m py_compile backend/api/routes/rag.py shared/rag_system.py tests/test_rag_query_definition_intent.py tests/test_rag_quality.py tests/test_rag_query_rewrite.py` -> `PASS`
  - `.venv\\Scripts\\python.exe -m pytest -q tests/test_rag_query_definition_intent.py tests/test_rag_quality.py tests/test_rag_query_rewrite.py` -> `PASS` (`14 passed, 4 warnings`)
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`
- review:
  - independent reviewer `Singer` first returned `FAIL` on missing short/ambiguous gating; after the `_should_enable_controlled_rewrites()` fix and long-query regression, re-review returned `PASS`.
  - review artifact created: `coordination/reviews/ragexec-019-2026-03-10.md`.
- coordination:
  - `coordination/tasks.jsonl` updated: `RAGEXEC-019` -> `completed`.
  - `coordination/cycle-contract.json` switched to `RAGEXEC-020`.

## 2026-03-10 RAGEXEC-020 implementation kickoff
- progress:
  - scope confirmed: add local-only answer-quality scoring, optional Ollama judge metrics, and commit-to-commit delta visibility without leaking developer-local corpora paths or contents into committed artifacts,
  - planned runtime surface stays in `backend/services/rag_eval_service.py` plus artifact/gate/reporting updates in `scripts/rag_eval_baseline_runner.py` and `scripts/rag_eval_quality_gate.py`,
  - committed-safe tests must validate schema/aggregation/gate behavior with synthetic data only; real local corpora and Ollama runs remain manual local verification outside CI.
- next_step:
  - implement answer-metric config + answer/judge evaluation pipeline with artifact metadata/trend updates, then add synthetic regressions and run the committed-safe verification lane before independent review.

## 2026-03-10 RAGEXEC-020 completion
- result:
  - local-only answer-eval metrics are implemented across `backend/services/rag_eval_service.py`, `scripts/rag_eval_baseline_runner.py`, and `scripts/rag_eval_quality_gate.py`,
  - committed-safe tests now cover answer-metric persistence, disabled-lane metadata stripping, dynamic metric selection, and Ollama-only provider enforcement,
  - `SPEC.md`, `docs/TESTING.md`, `docs/OPERATIONS.md`, `docs/CONFIGURATION.md`, and `docs/REQUIREMENTS_TRACEABILITY.md` are synchronized with the final local-only contract.
- security decisions:
  - answer/judge eval no longer inherits `AI_PROVIDER`,
  - non-ollama provider overrides are rejected for the local-only lane,
  - retrieval-only artifacts do not persist/render dormant answer-lane provider or base-url metadata.
- verification:
  - `python -m py_compile backend/services/rag_eval_service.py scripts/rag_eval_baseline_runner.py scripts/rag_eval_quality_gate.py tests/test_rag_eval_service.py tests/test_rag_eval_quality_gate.py tests/test_rag_eval_baseline_runner.py` -> PASS
  - `.venv\Scripts\python.exe -m pytest -q tests/test_rag_eval_service.py tests/test_rag_eval_quality_gate.py tests/test_rag_eval_baseline_runner.py` -> PASS (`28 passed, 3 warnings`)
  - `python scripts/scan_secrets.py` -> PASS
  - `python scripts/ci_policy_gate.py --working-tree` -> PASS
  - local Ollama probe to `http://localhost:11434/api/tags` -> connection refused in the current environment, so no live local judge run was recorded in this slice.
- review:
  - independent review PASS recorded in `coordination/reviews/ragexec-020-2026-03-10.md`.
- roadmap status:
  - `RAGEXEC-020` closed and the approved `RAGEXEC-008..020` backlog is now fully implemented.

## 2026-03-10 Live eval follow-up
- task:
  - reuse the existing main `.env` provider/model for answer-eval by default,
  - allow local eval to pin one prepared KB,
  - run a real local-only answer-quality baseline on developer corpora without committing corpus paths or contents.
- implementation:
  - `backend/services/rag_eval_service.py` now defaults answer-eval provider/model to the main runtime config (`AI_DEFAULT_PROVIDER` + provider model envs), while keeping judge overrides optional,
  - local eval can now pin both retrieval and answer generation to one prepared KB via `RAG_EVAL_KB_ID`,
  - `scripts/rag_eval_baseline_runner.py` now renders the selected provider(s) and `knowledge_base_id` in report metadata only when relevant,
  - `shared/document_loaders/markdown_loader.py` now prevents DB-overflowing `full` markdown chunks by degrading oversized content to bounded structural/text splitting, and `shared/kb_settings.py` lowers the default `full_max` to a storage-safe range.
- live verification:
  - created and populated local eval KB `3` with repo `test.pdf`, local `open-harmony.zip`, and local Telegram export JSON,
  - ran `.venv\Scripts\python.exe -m scripts.rag_eval_baseline_runner --suite rag-general-v1 --label live_kb3_answer_eval --out-dir data/rag_eval_baseline` with local-only env overrides including `RAG_EVAL_ENABLE_ANSWER_METRICS=true`, `RAG_EVAL_KB_ID=3`, and a filtered live suite file under `data/rag_eval_local/`,
  - live artifact written to `data/rag_eval_baseline/runs/live_kb3_answer_eval/20260310_124304_eval_20260310_123854_38bebb.{json,md}`.
- live baseline summary:
  - overall retrieval is strong enough to iterate on (`recall_at_10=0.8462`, `mrr_at_10=0.6674`, `ndcg_at_10=0.7092`),
  - answer grounding/security is currently weak on the active provider path (`faithfulness=0.0`, `answer_correctness=0.0`, `response_relevancy=0.6923`, `refusal_accuracy=0.6923`, `security_resilience=0.6923`),
  - case failures are concentrated in refusal/security scenarios: `direct_injection_refusal_012`, `prompt_leak_probe_014`, `secret_leak_probe_015`, `access_scope_probe_016`,
  - retrieval candidate persistence emitted FK warnings (`retrieval_candidate_logs` referencing missing `retrieval_query_logs`), which needs a separate fix.
- focused verification:
  - `python -m py_compile backend/services/rag_eval_service.py scripts/rag_eval_baseline_runner.py shared/kb_settings.py shared/document_loaders/markdown_loader.py tests/test_rag_eval_service.py tests/test_rag_eval_baseline_runner.py tests/test_markdown_loader_chunking.py` -> `PASS`
  - `.venv\Scripts\python.exe -m pytest -q tests/test_rag_eval_service.py tests/test_rag_eval_baseline_runner.py tests/test_markdown_loader_chunking.py` -> `PASS` (`18 passed, 3 warnings`)
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`
- review:
  - independent review PASS recorded in `coordination/reviews/rag-live-eval-followup-2026-03-10.md`.

## 2026-03-10 RAGFOLLOW-001 completion
- task:
  - harden refusal/security behavior for malicious or overbroad KB queries without regressing normal grounded answers.
- implementation:
  - `shared/rag_safety.py` now provides deterministic query security assessment, poisoned-context detection with benign-example allowance, and generic refusal messages,
  - `backend/api/routes/rag.py` now refuses prompt-leak, secret-leak, overbroad private-data, and poisoned-context cases before LLM generation for both `/rag/query` and `/rag/summary`,
  - `shared/utils.py` grounded-answer prompt now explicitly separates instruction plane and forbids following prompt-leak / secret-leak / unrelated-private-data requests from either the user query or retrieved documents,
  - new regressions cover prompt leak, secret leak, access-scope refusal, poisoned-context refusal, benign security-doc examples, and grounded URL preservation under the rewritten-query path.
- focused_verification:
  - `python -m py_compile backend/api/routes/rag.py shared/rag_safety.py shared/utils.py tests/test_rag_security_refusals.py tests/test_rag_safety.py tests/test_rag_url_preservation.py backend/services/rag_eval_service.py` -> `PASS`
  - `.venv\Scripts\python.exe -m pytest -q tests/test_rag_security_refusals.py tests/test_rag_safety.py tests/test_rag_url_preservation.py tests/test_rag_eval_service.py` -> `PASS` (`23 passed, 4 warnings`)
  - `python scripts/scan_secrets.py` -> `PASS`
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`
- live_eval:
  - local compare runs recorded under `data/rag_eval_baseline/runs/live_kb3_answer_eval_sec{1,2,3}/`,
  - baseline vs final (`sec3`) overall metrics:
    - retrieval unchanged: `recall_at_10 0.8462 -> 0.8462`, `mrr_at_10 0.6674 -> 0.6674`, `ndcg_at_10 0.7092 -> 0.7092`
    - security improved: `refusal_accuracy 0.6923 -> 1.0000`, `security_resilience 0.6923 -> 1.0000`, `response_relevancy 0.6923 -> 1.0000`
    - answer-grounding metrics remain unstable on the current provider path (`faithfulness`, `answer_correctness`) and need a later slice rather than this security fix.
- review:
  - independent reviewer `Dirac` first returned `FAIL` on overbroad poisoned-context detection and refusal wording,
  - after narrowing poisoned-context screening and making refusal templates generic, re-review returned `PASS`,
  - review artifact created: `coordination/reviews/ragfollow-001-2026-03-10.md`.
- next_step:
  - move to `RAGFOLLOW-002`: fix `retrieval_candidate_logs_ibfk_1` FK warnings observed throughout live eval runs.

## 2026-03-10 RAGFOLLOW-002 completion
- task:
  - fix FK-ordering persistence for retrieval diagnostics so parent query rows are durable before candidate rows are inserted.
- implementation:
  - `backend/api/routes/rag.py::_persist_retrieval_logs()` now flushes the parent `RetrievalQueryLog` immediately after `db.add(...)` and before any `RetrievalCandidateLog` inserts,
  - `tests/test_rag_diagnostics_contract.py` now includes a flush-sensitive fake DB that fails if a candidate row is inserted before the parent query row is flushed.
- verification:
  - real DB smoke via `.venv\Scripts\python.exe` created one temporary `request_id`, observed `query_count=1` and `candidate_count=1`, then deleted both rows in the same session without the earlier FK warning,
  - `python -m py_compile backend/api/routes/rag.py shared/database.py tests/test_rag_diagnostics.py tests/test_rag_diagnostics_contract.py tests/test_api_routes_contract.py` -> PASS,
  - `.venv\Scripts\python.exe -m pytest -q tests/test_rag_diagnostics.py tests/test_rag_diagnostics_contract.py tests/test_api_routes_contract.py` -> PASS (`12 passed, 14 warnings`).
- docs:
  - `SPEC.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, and `docs/OPERATIONS.md` now document parent-flush ordering for retrieval diagnostics persistence.
- next_step:
  - move to `RAGFOLLOW-003`: derive local eval slices from actual suite coverage so filtered live runs stop emitting irrelevant zero-sample groups.

## 2026-03-10 RAGFOLLOW-003 completion
- task:
  - remove irrelevant zero-sample slice clutter from developer-local live eval reports without weakening explicit slice debugging/gate behavior.
- implementation:
  - `backend/services/rag_eval_service.py` now resolves auto-run `metrics.slices` from actual case coverage via `_resolve_run_slices(...)`,
  - explicit slice overrides remain strict and are persisted with `slices_mode="explicit"` while auto runs persist `slices_mode="auto"` plus the reduced actual slice set,
  - synthetic regressions in `tests/test_rag_eval_service.py` now cover both auto filtering and explicit strictness.
- verification:
  - `python -m py_compile backend/services/rag_eval_service.py scripts/rag_eval_quality_gate.py scripts/rag_eval_baseline_runner.py tests/test_rag_eval_service.py tests/test_rag_eval_quality_gate.py tests/test_rag_eval_baseline_runner.py` -> PASS,
  - `.venv\Scripts\python.exe -m pytest -q tests/test_rag_eval_service.py tests/test_rag_eval_quality_gate.py tests/test_rag_eval_baseline_runner.py` -> PASS (`31 passed, 3 warnings`),
  - live local run `live_kb3_answer_eval_sec4` completed successfully and no longer reports `open_harmony_code` / `indirect_injection` in `metrics.slices` or summary tables.
- docs:
  - `SPEC.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, `docs/TESTING.md`, and `docs/OPERATIONS.md` now document auto local slice derivation vs explicit strict slice overrides.
- next_step:
  - move to `RAGFOLLOW-004`: add local-only per-case failure-analysis artifacts for answer regression triage.
- review:
  - independent reviewer `Russell` returned `PASS`; review artifact recorded in `coordination/reviews/ragfollow-003-2026-03-10.md`.

## 2026-03-10 RAGFOLLOW-004 completion
- task:
  - add local-only per-case failure-analysis artifacts so answer regressions can be triaged from saved eval reports instead of aggregate scores only.
- implementation:
  - `backend/services/rag_eval_service.py` now builds `case_analysis` entries for failed/suspicious answer-eval cases with compact query/answer previews, reasons, suspicious events, score snapshots, source-path hints, and latency/judge-note metadata,
  - retrieval-only metrics payloads now omit `security_summary`, `case_failures`, `case_analysis`, and `suspicious_events` entirely when answer metrics are disabled,
  - `scripts/rag_eval_baseline_runner.py` now renders `## Answer Failure Analysis` for local answer-eval artifacts.
- verification:
  - `python -m py_compile backend/services/rag_eval_service.py scripts/rag_eval_baseline_runner.py tests/test_rag_eval_service.py tests/test_rag_eval_baseline_runner.py` -> PASS,
  - `.venv\Scripts\python.exe -m pytest -q tests/test_rag_eval_service.py tests/test_rag_eval_baseline_runner.py` -> PASS (`21 passed, 4 warnings`),
  - `python scripts/scan_secrets.py` -> PASS,
  - `python scripts/ci_policy_gate.py --working-tree` -> PASS,
  - live local run `live_kb3_answer_eval_sec6` completed successfully and now includes `## Answer Failure Analysis` with case-level triage rows.
- docs:
  - `SPEC.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, `docs/TESTING.md`, and `docs/OPERATIONS.md` now document local-only `case_analysis` artifacts and the retrieval-only omission contract.
- review:
  - independent reviewer `Russell` returned `PASS`; review artifact recorded in `coordination/reviews/ragfollow-004-2026-03-10.md`.
- roadmap status:
  - `RAGFOLLOW-001..004` are now complete; no approved follow-up slices remain in the current RAG quality hardening backlog.

## 2026-03-10 RAGFOLLOW-005 kickoff
- task:
  - replace raw provider transport errors in `/rag/query` and `/rag/summary` with an extractive retrieval-only fallback built from the already selected evidence pack.
- checklist:
  - todo -> implement provider-error detection and extractive fallback formatter in `backend/api/routes/rag.py`
  - todo -> add route regressions for query + summary fallback and security refusal precedence
  - todo -> run focused pytest + secret/policy gates
  - todo -> update spec/traceability/ops docs and request independent review
- benchmark status:
  - proxy-caused `503` is resolved by `NO_PROXY`; direct Python `requests.get(.../api/tags)` now returns `200`,
  - `qwen3.5:35b` still times out on `/api/generate` at `130s`, while `deepseek-r1:latest` returns `200`,
  - 3x repeated live eval benchmark on KB `3` + `rag_eval_ready_live_kb3.yaml` completed for `mistral-small3.1`, `qwen3:30b`, and `qwen3.5:27b`,
  - current median full-run latency: `mistral-small3.1 ~242.7s`, `qwen3:30b ~315.0s`, `qwen3.5:27b ~767.1s`,
  - current median answer metrics remain weak across all three (`faithfulness` / `answer_correctness` low), but `mistral-small3.1` is the best current speed-quality tradeoff on this suite.
- next_step:
  - patch `rag.py` so provider timeout/503 strings never become the final user answer when grounded context rows are already available.

## 2026-03-10 RAGFOLLOW-005 progress
- implementation:
  - `backend/api/routes/rag.py` now detects provider transport/status failures returned as raw answer strings and replaces them with an extractive fallback built from the already selected evidence-pack rows,
  - fallback uses `build_query_focused_excerpt(...)` plus source/page/section labels, preserves existing `sources`, and avoids surfacing raw timeout/503 text to the user,
  - the same fallback behavior now applies to both `/rag/query` and `/rag/summary`.
- focused_verification:
  - `python -m py_compile backend/api/routes/rag.py tests/test_rag_context_composer.py tests/test_rag_summary_modes.py tests/test_rag_security_refusals.py` -> `PASS`,
  - `.venv\Scripts\python.exe -m pytest -q tests/test_rag_context_composer.py tests/test_rag_summary_modes.py tests/test_rag_security_refusals.py` -> `PASS` (`15 passed, 4 warnings`),
  - `python scripts/scan_secrets.py` -> `PASS`,
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`.
- live_smoke:
  - real end-to-end call with `OLLAMA_MODEL=qwen3.5:35b` and `knowledge_base_id=3` returned `has_fallback_intro=true`, `has_raw_timeout=false`, `sources_count=3`,
  - this confirms retrieval-only fallback now masks the raw provider timeout on the live slow-model path.
- benchmark_summary:
  - 3 repeated full eval runs per model against KB `3` + `data/rag_eval_local/rag_eval_ready_live_kb3.yaml`,
  - median full-run latency: `mistral-small3.1 ~242.7s`, `qwen3:30b ~315.0s`, `qwen3.5:27b ~767.1s`,
  - median answer metrics on this suite: `mistral-small3.1 faithfulness/answer_correctness ~0.1282`, `qwen3:30b ~0.0`, `qwen3.5:27b ~0.0`,
  - retrieval stayed identical across models, so `mistral-small3.1` is the current best speed/quality tradeoff from the measured set.
- next_step:
  - request independent review for `RAGFOLLOW-005`, then sync tasks/docs and report the benchmark status to the user.

## 2026-03-10 RAGFOLLOW-005 blocker fix
- reviewer blocker:
  - independent review found that the initial fallback branch bypassed `strip_unknown_citations()`, `strip_untrusted_urls()`, and `sanitize_commands_in_answer()`.
- fix:
  - `backend/api/routes/rag.py` now routes both normal LLM answers and extractive fallback answers through shared `_postprocess_grounded_answer(...)`,
  - fallback safety regression now monkeypatches the route-level safety helpers to prove the fallback path still executes URL/command safety processing.
- final_verification:
  - `python -m py_compile backend/api/routes/rag.py tests/test_rag_context_composer.py tests/test_rag_summary_modes.py tests/test_rag_security_refusals.py` -> `PASS`,
  - `.venv\Scripts\python.exe -m pytest -q tests/test_rag_context_composer.py tests/test_rag_summary_modes.py tests/test_rag_security_refusals.py` -> `PASS` (`16 passed, 4 warnings`),
  - `python scripts/scan_secrets.py` -> `PASS`,
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`.

## 2026-03-10 RAGFOLLOW-005 completion
- review:
  - independent reviewer `Ampere` returned `PASS` after verifying that fallback answers now go through the same grounded-answer safety postprocessing and that the new regression covers fallback safety execution.
- roadmap status:
  - `RAGFOLLOW-005` completed.
  - no additional approved RAG follow-up slices remain in the current backlog.

## 2026-03-10 BOTFOLLOW-001 kickoff
- task:
  - suppress transient bot runtime error notifications for noisy network/protocol disconnects while keeping logging,
  - redirect admins to the per-KB actions menu immediately after successful KB creation from the text-state flow.
- classification:
  - non-trivial bugfix; touches runtime behavior in multiple files and requires new regression coverage.
- research summary:
  - `frontend/error_handlers.py` currently notifies admins for every exception with only a coarse time-based spam guard,
  - `frontend/bot_handlers.py` already has the created KB id in the `waiting_kb_name` branch but still routes success back to `admin_menu()`,
  - `frontend/templates/buttons.py::kb_actions_menu()` already provides the right post-create UX target.
- checklist:
  - in_progress -> implement transient error classification in `frontend/error_handlers.py`
  - todo -> switch KB-create success reply to `kb_actions_menu(created_id)` in `frontend/bot_handlers.py`
  - todo -> add focused regressions in `tests/test_bot_error_handlers.py` and `tests/test_bot_text_ai_mode.py`
  - todo -> run `py_compile`, focused `pytest`, `scan_secrets`, `ci_policy_gate`
  - todo -> request independent review and sync `SPEC.md` / traceability / operations docs

## 2026-03-10 BOTFOLLOW-001 progress
- implementation:
  - `frontend/error_handlers.py` now classifies transient transport/protocol failures (`NetworkError`, `RemoteProtocolError`-style disconnects, timeout/reset wording) and suppresses only the admin-facing critical notification for those cases while keeping log output,
  - `frontend/bot_handlers.py` now routes successful `waiting_kb_name` creation replies to `kb_actions_menu(created_id)` instead of the generic `admin_menu()`,
  - new focused regression file `tests/test_bot_error_handlers.py` covers suppressed transient errors and preserved notification for non-transient exceptions,
  - `tests/test_bot_text_ai_mode.py::test_handle_text_waiting_kb_name_creates_knowledge_base` now asserts the returned reply markup matches the created KB action menu.
- focused_verification:
  - `python -m py_compile frontend/error_handlers.py frontend/bot_handlers.py tests/test_bot_error_handlers.py tests/test_bot_text_ai_mode.py` -> `PASS`,
  - `.venv\Scripts\python.exe -m pytest -q tests/test_bot_error_handlers.py tests/test_bot_text_ai_mode.py` -> `PASS` (`14 passed`).
- next_step:
  - run `scan_secrets` + `ci_policy_gate`, request independent review, then close the slice.

## 2026-03-10 BOTFOLLOW-001 verification and handoff
- gates:
  - `python scripts/scan_secrets.py` -> `PASS`,
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`.
- notes:
  - the requested UX/error changes are implemented and verified locally,
  - `SPEC.md`, `docs/REQUIREMENTS_TRACEABILITY.md`, and `docs/OPERATIONS.md` already contained matching requirements/ops coverage in the current working tree, so no additional doc edits were needed in this slice,
  - the current adapter session does not expose a usable independent sub-agent review endpoint, so the mandatory review artifact is still pending and `BOTFOLLOW-001` remains open in coordination until that review can be recorded.

## 2026-03-10 BOTFOLLOW-001 reviewer blocker fix
- blocker:
  - independent review flagged that `_is_transient_transport_error(...)` was too broad because it suppressed every `telegram.error.NetworkError`, including potentially persistent/operator-actionable failures.
- fix:
  - `frontend/error_handlers.py` no longer treats `NetworkError` as transient by default; suppression now requires known transient disconnect type names or message substrings,
  - `tests/test_bot_error_handlers.py` now includes a non-transient `NetworkError("proxy handshake failed")` regression proving admin notifications still fire for actionable network failures.
- re_verification:
  - `python -m py_compile frontend/error_handlers.py frontend/bot_handlers.py tests/test_bot_error_handlers.py tests/test_bot_text_ai_mode.py` -> `PASS`,
  - `.venv\Scripts\python.exe -m pytest -q tests/test_bot_error_handlers.py tests/test_bot_text_ai_mode.py` -> `PASS` (`15 passed`),
  - `python scripts/scan_secrets.py` -> `PASS`,
  - `python scripts/ci_policy_gate.py --working-tree` -> `PASS`.

## 2026-03-10 BOTFOLLOW-001 completion
- review:
  - independent reviewer `Meitner` returned `PASS` after confirming the transient matcher no longer suppresses all `NetworkError` cases and that the new regression proves actionable `NetworkError("proxy handshake failed")` still notifies admins.
- docs:
  - `SPEC.md` now documents post-create landing in the KB action menu and the warning-only handling of transient transport disconnects,
  - `docs/REQUIREMENTS_TRACEABILITY.md` adds `AC-56` / `AC-57`,
  - `docs/USAGE.md` documents the immediate post-create KB menu,
  - `docs/OPERATIONS.md` documents that transient transport disconnects are logs-first, not admin-page incidents.
- roadmap status:
  - `BOTFOLLOW-001` completed.
  - no additional approved bot follow-up slices are currently registered.
