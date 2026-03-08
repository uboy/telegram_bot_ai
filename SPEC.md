# Product Spec: Telegram Bot AI with RAG

## Problem statement
Teams and individuals need a Telegram-native assistant that can answer questions using their private documents, codebases, and web sources with reliable citations, while remaining easy to deploy (Docker or local) and safe for admins to control.

## Target users
- Internal team members who search or ask questions over a shared knowledge base.
- Admins who manage users, knowledge bases, ingestion, and AI provider settings.
- Developers/DevOps who deploy and operate the bot and backend.

## In-scope
- Telegram bot UI (menus, commands, upload flows).
- Backend API for auth, knowledge bases, ingestion, and RAG query.
- Document ingestion: files (Markdown, PDF, Word, Excel, text), images, web pages, and wiki via crawl/git/zip.
- RAG pipeline: chunking, embeddings, external Qdrant dense index + lexical BM25 channel, optional reranking, keyword fallback.
- Inline citations and source listing in responses.
- User/admin roles and approval flow.
- AI provider selection and routing (Ollama, OpenAI, Anthropic, DeepSeek, Open WebUI via OpenAI-compatible endpoint).
- n8n webhook integration for ingestion events.
- Docker-based deployment with MySQL/Redis; local run with SQLite.

## Out-of-scope
- Full web UI beyond Telegram.
- Additional vector stores beyond configured Qdrant backend.
- Enterprise SSO or complex IAM.
- Automatic migration of data between SQLite and MySQL.
- Advanced analytics dashboards (beyond logs and n8n hooks).

## Functional requirements
- Telegram bot:
  - Handle `/start`, text queries, document uploads, and images.
  - Provide menus for: KB search, web search, ask AI, settings.
  - In direct AI mode ("Задать вопрос ИИ"), voice/audio inputs are first transcribed (ASR), then the transcript is sent to AI and returned as the answer.
  - In direct AI mode, when re-entering mode the bot asks whether to restore the previous dialog context or start a new dialog.
  - In direct AI mode, conversation context is persisted in DB and compressed (summary + recent turns) before sending to weaker models.
  - In direct AI mode, first AI reply should be concise; for ambiguous queries model should ask one clarifying question first.
  - Direct AI mode validates empty user input and does not send blank prompts to AI.
  - Direct AI mode sends oversized model responses in Telegram-safe chunks to avoid `Message is too long` failures.
  - For AI requests, request/model metrics are persisted in DB and used to estimate expected latency.
  - If AI request is predicted (or observed) to exceed 5 seconds, bot shows temporary progress status and removes it after response/error.
  - Admin menu for users/KBs/ingestion/AI settings.
  - Wiki crawl flow from admin KB actions is stateful: after pressing "Собрать вики по URL" and sending root URL, bot must call backend wiki-crawl ingestion and return explicit result stats instead of falling back to unrelated default flow.
  - For Gitee wiki URLs, wiki-crawl uses git-based loader fallback to ensure full recursive synchronization when HTML pages expose wiki navigation mostly via JS.
  - Admin KB upload flow accepts one or multiple Telegram documents without manual type preselection; file type is auto-detected, Telegram size limits are validated, and per-file processing report is returned.
  - In KB search mode, if a user sends multiple questions подряд, bot processes them in FIFO order and replies under each original question (`reply_to`).
  - In KB search mode, long-running retrieval shows temporary progress indicator and removes it after final answer to keep chat clean.
  - On explicit re-entry to KB search mode ("🔍 Поиск в базе знаний"), stale pending/queued KB queries from previous session are dropped to prevent orphan/out-of-context answers.
  - ASR visibility control: users can toggle technical metadata display in their settings.
  - **ASR performance: support for `faster-whisper` engine and FP16/INT8 optimizations for high-speed transcription.**
  - **GPU Acceleration: automatic detection and utilization of NVIDIA GPUs (RTX 3090 support) inside Docker containers.**
  - Optional Hugging Face token (`HF_TOKEN`) is supported for gated/private model access and startup model loading.

- Backend API:
  - Auth by telegram_id and API key (`X-API-Key`).
  - CRUD for users and knowledge bases.
  - Ingestion endpoints for documents, images, web pages, wiki (crawl/git/zip).
  - RAG query endpoint returning answer + sources + metadata + `request_id`.
  - RAG diagnostics endpoint for retrieval trace by `request_id`, including orchestrator mode marker (`legacy`/`v4`) for incident triage.
  - RAG eval orchestration endpoints for benchmark run launch/status (`/rag/eval/run`, `/rag/eval/{run_id}`) with persisted run/result metrics.
  - Job status endpoint for ingestion progress (where implemented).
- RAG pipeline:
  - Chunking with configurable size/overlap and Markdown-aware splitting.
  - Ingestion normalizes core chunk metadata contract (`type`, `title`, `doc_title`, `section_title`, `section_path`, `chunk_kind`, `document_class`, `language`, `doc_version`, `source_updated_at`) across source types.
  - Markdown and code loaders preserve document/section metadata consistently (`doc_title`, `section_title`, `section_path`, `chunk_no`) to reduce context assembly ambiguity.
  - Embeddings with sentence-transformers; Qdrant for dense retrieval in production mode (`RAG_BACKEND=qdrant`).
  - Legacy in-process FAISS path remains available as rollback mode (`RAG_BACKEND=legacy`).
  - Retrieval orchestrator cutover is controlled by `RAG_ORCHESTRATOR_V4` feature flag.
  - Index sync foundation uses idempotent SQL outbox events (`index_outbox_events`) to support retry-safe delivery into retrieval index backend.
  - Backend outbox worker consumes pending index events with retry/backoff + dead-letter handling and writes periodic drift snapshots into `index_sync_audit`.
  - Dense/BM25 retrieval budgets are explicitly configurable; dedicated knobs fall back to `RAG_MAX_CANDIDATES` for rollback-safe compatibility.
  - Optional reranker with explicit top-N input window and top-k final results.
  - Fallback keyword search if embeddings unavailable.
  - Context assembly with `SOURCE_ID` tags for inline citations.
  - In legacy orchestrator mode (`RAG_ORCHESTRATOR_V4=false`), definition/factoid/clause heuristics remain available:
    - for definition-style questions ("что такое", "как определяется", "что включает"), ranking prioritizes explicit definitional fragments;
    - for questions with explicit clause references ("пункт N"), retrieval additionally prioritizes chunks containing numeric section markers (`N.`) and `пункт N`;
    - for factoid/legal/numeric questions ("кто", "как часто", "какой целевой показатель", "на 2030 год"), retrieval applies dedicated factual intent ranking + lexical fallback by terms/years/points;
    - for metric/factoid questions, ranking additionally prioritizes key phrase overlap + numeric evidence and uses narrowed context packing.
  - In Phase D orchestrator mode (`RAG_ORCHESTRATOR_V4=true`), route-level query-specific boosts/fallback and retrieval-core legacy ranking boosts are disabled in primary path.
  - In legacy mode, route-level query-specific boosts/fallback and retrieval-core `source_boost` / how-to ranking are disabled by default (`RAG_LEGACY_QUERY_HEURISTICS=false`) and can be temporarily re-enabled only as rollback switch.
  - RAG eval uses a fixed, versioned ready-data suite by default (`tests/data/rag_eval_ready_data_v1.yaml`) to keep quality comparisons reproducible.
  - Baseline eval runner persists timestamped JSON/Markdown artifacts plus `latest` snapshots for reviewable quality evidence.
- Safety/quality:
  - Strip unknown citations and untrusted URLs in answers while preserving grounded source-backed document/wiki URLs.
  - Sanitize command snippets not present in KB context.
- Storage:
  - SQL DB tables for users, knowledge bases, chunks, and import logs.
  - SQL DB tables for AI conversations, turns, and per-request model metrics.
  - Optional Redis cache for conversation history.
- Integration:
  - n8n webhook events for ingestion actions.
  - Multi-provider AI access with configurable defaults and model selection.
  - Stack startup checks DB configuration and enables MySQL service only when `MYSQL_URL` is configured.

## Non-functional requirements
- Reliability: bot and backend recover gracefully from ingestion or model errors.
- Latency: interactive responses for typical KB sizes (seconds, not minutes).
- Configurability: all operational settings via `.env` and DB-stored KB settings.
- Security: API key for bot → backend, admin ID allowlist, no secrets in code.
- Operational resilience: startup should not fail due SQLAlchemy 2.x raw SQL execution API incompatibilities in SQLite WAL checks.
- Governance: feature/bugfix changes must keep specs and traceability docs up to date.
- Portability: run via Docker Compose or locally with minimal setup.
- Observability: structured logs to file and stdout; error notifications to admins.

## Success metrics
- ≥ 90% of sample RAG eval cases retrieve expected sources/snippets in top-5.
- Admins can create a KB and ingest at least one file and one web page without errors.
- Median end-to-end RAG response time under 5 seconds for small/medium KB.
- Zero leakage of non-KB commands/URLs in generated answers.
- Successful webhook delivery for ingestion events (n8n enabled).

## Edge cases
- Large archives or wiki trees causing long ingestion times.
- Mixed-language content (RU/EN) with inconsistent chunking.
- Missing embeddings/reranker models; fallback to keyword search.
- Users without admin approval attempting restricted actions.
- Invalid or inaccessible URLs for web/wiki ingestion.
- Images without OCR tools installed (should still respond with AI description if available).
- Backend unavailable; bot should fail gracefully with user-facing error.

## Acceptance criteria
- Bot can register users and requires admin approval for non-admins.
- Admin can create, list, clear, and delete knowledge bases via bot UI.
- KB creation flow in admin panel is stateful: after "Создать базу знаний" and name input, bot must call backend create endpoint and return explicit success/failure instead of falling back to welcome screen.
- Wiki crawl flow in admin panel is stateful: after "Собрать вики по URL" and root URL input, bot must call backend `/ingestion/wiki-crawl`, return crawl stats, and clear temporary wiki state keys.
- For Gitee wiki URLs, `/ingestion/wiki-crawl` must synchronize full wiki content (not only root page) by using git-loader fallback when plain HTML crawl cannot discover recursive links.
- Admin KB upload does not require manual file-type selection; bot auto-detects document type, supports multiple files in one flow, validates Telegram file limits, and returns per-file success/failure report.
- Global admin-level "upload documents" entry is removed; document upload starts from a selected KB only.
- Admin can ingest: Markdown, PDF, Word, Excel, text, image, web URL, and wiki (crawl/git/zip).
- Ingested chunks keep a normalized metadata baseline so retrieval/context assembly can rely on consistent title/section/document fields across loaders.
- RAG query returns an answer plus a list of sources with path/URL and metadata.
- Inline citations are present when enabled and only reference provided sources.
- Grounded source-backed document/wiki URLs survive answer safety filtering even when they are not present verbatim in the assembled context text; untrusted links are still removed.
- Command snippets in answers are filtered by token-level grounding against KB context: grounded command variants survive small formatting differences, while invented options/arguments are removed.
- Web search uses DuckDuckGo and returns summarized results with source links.
- Direct AI mode works from reply keyboard button "🤖 Задать вопрос ИИ" and accepts text plus voice/audio (voice/audio are transcribed first, then sent to AI).
- Direct AI mode handles empty input with validation prompt and does not fail on long model outputs (responses are split into safe chunks).
- Direct AI mode on re-entry offers context restore/new dialog choice; selected context is loaded from DB.
- Direct AI mode prompt policy enforces concise first reply and clarification-first behavior for ambiguous user input.
- AI request metrics are persisted for all `ai_manager` calls and include provider/model/latency/status fields.
- AI mode shows temporary progress status for long requests (>5s predicted or observed) and removes it after completion to keep chat clean.
- In legacy orchestrator mode (`RAG_ORCHESTRATOR_V4=false`), RAG definition-style questions prefer glossary/definition fragments over generic policy mentions when both are present.
- In legacy orchestrator mode (`RAG_ORCHESTRATOR_V4=false`), RAG questions with "пункт N" return the corresponding clause context when it exists in indexed chunks (including numeric markers like `25.`/`26.`).
- In legacy orchestrator mode (`RAG_ORCHESTRATOR_V4=false`), RAG factoid/legal questions (including year/metric queries) return clause-level context when corresponding chunks exist in KB.
- In Phase D mode (`RAG_ORCHESTRATOR_V4=true`), `/api/v1/rag/query` disables route-level query-specific hardcoded boosts/keyword fallback and ranks by base retrieval score.
- RAG query response includes `request_id`, and retrieval diagnostics are available via `GET /api/v1/rag/diagnostics/{request_id}`.
- Retrieval diagnostics include `orchestrator_mode` marker to identify request execution mode (`legacy`/`v4`).
- Retrieval diagnostics include `retrieval_core_mode` marker to distinguish generalized retrieval from explicit legacy-heuristic rollback.
- Ingestion emits idempotent index outbox events for non-empty chunk upserts, enabling retry-safe index synchronization without duplicate writes.
- Outbox worker processes queued index events asynchronously with bounded retries/dead-letter transition, and periodic drift audit records SQL-vs-Qdrant divergence in `index_sync_audit`.
- Retrieval diagnostics include degraded-mode flags (`degraded_mode`, `degraded_reason`) and always expose non-null candidate trace fields for top candidates: `origin`, `channel`, `channel_rank`, `fusion_rank`, `fusion_score` (with derived defaults for older rows); `rerank_delta` remains optional when no rerank signal exists.
- Retention lifecycle runs on schedule: old retrieval logs, old document versions/chunks, eval artifacts, and drift audit snapshots are purged by policy with `retention_deletion_audit` entries.
- Backend exposes eval run lifecycle: `POST /api/v1/rag/eval/run` queues benchmark run and `GET /api/v1/rag/eval/{run_id}` returns run status + per-slice metrics.
- Statistical quality gate script validates eval run against baseline using thresholds, minimum sample size, and bootstrap 95% CI delta margin.
- Eval ready-data suite is contract-tested for minimum size, unique case ids, required fields, and required slice coverage before use in regression cycles.
- Baseline eval run is reproducibly executable via CLI runner and produces review artifacts (`*.json`, `*.md`) for each run.
- Quality gate supports both DB run mode (`--run-id`) and artifact mode (`--run-report-json`, optional `--baseline-report-json`) with identical threshold PASS/FAIL semantics.
- Route-level query-specific boosts/fallback and retrieval-core `source_boost` / how-to ranking are no longer part of the default ranking path; rollback requires explicit `RAG_LEGACY_QUERY_HEURISTICS=true`.
- RU and EN RAG answer prompts use the same direct grounded-answer contract: no forced answer-section headings, deterministic no-evidence refusal, and citations only when grounded by `SOURCE_ID`.
- Telegram answer formatting supports headingless direct answers without requiring legacy section labels; old `Main Answer` / `Additionally Found` style headings remain compatibility-only input.
- In KB search mode, multiple user questions sent without waiting are answered in the same order and each bot reply is attached to its source user message.
- For long KB-search requests, bot shows temporary wait/progress message and deletes it after answer delivery.
- Re-entering KB search mode resets stale queue/pending items from previous KB query session so old questions are not answered unexpectedly.
- **ASR results: technical metadata is hidden by default or toggleable by user.**
- ASR formatting: metadata is displayed as an expandable HTML block (`<blockquote expandable>`) in Telegram.
- **ASR Latency: transcription of 1 minute of audio completes in under 10 seconds using optimized engines on 3090 GPU.**
- **ASR Engine selection: admins can switch between standard `transformers` and `faster-whisper` via bot settings.**
- **Docker Infrastructure: GPU libraries (CUDA/cuDNN) are correctly integrated and visible to AI engines.**
- Backend enforces API key header when configured.
- `HF_TOKEN`/`HUGGINGFACE_HUB_TOKEN` from env is loaded at startup and available to Hugging Face libraries.
- SQLite WAL check runs without SQLAlchemy `Not an executable object` warning.

- n8n receives `knowledge_import` events with KB id/name, source type, and stats.
- Docker Compose starts bot, backend, db, redis, and n8n with externalized data.
- Smart startup launcher (`scripts/start_stack.py`) starts MySQL profile only when `MYSQL_URL` is configured; otherwise stack runs without `db` service.
- Supported AI providers are configurable via env and available through unified provider management.
- Backend includes a runnable RAG API smoke script (`scripts/rag_api_smoke_test.py`) for quick endpoint sanity checks.
- Backend includes a runnable legacy-vs-v4 compare script (`scripts/rag_orchestrator_compare.py`) for cutover evaluation on real API.
- Any feature/bugfix that changes behavior updates `SPEC.md`, related design spec, and `docs/REQUIREMENTS_TRACEABILITY.md` in the same task.
- Retrieval runtime uses explicit dense/BM25 candidate budgets and rerank input window (`RAG_DENSE_CANDIDATES`, `RAG_BM25_CANDIDATES`, `RAG_RERANK_TOP_N`); when dedicated knobs are unset they inherit `RAG_MAX_CANDIDATES`, and rerank window never drops below requested `top_k`.

## Specification maintenance policy
- `SPEC.md` is the source of truth for user-facing requirements and acceptance criteria.
- Every behavior/API/config/flow change must update this spec in the same PR/task.
- Every bugfix must include a regression expectation in the spec or linked design doc.
- `docs/REQUIREMENTS_TRACEABILITY.md` must map updated criteria to implementation and verification evidence.
- If no spec update is required, the task must include an explicit rationale.

## Open questions
- Should admin approval be optional for certain environments (e.g., single-user mode)?
- Should any provider-specific capabilities be mandatory, or is all provider support optional via configuration?
- Do we need formal API versioning (`/api/v1`) stability guarantees?
- What is the expected maximum KB size and ingestion volume for baseline SLAs?
