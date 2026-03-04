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
- RAG pipeline: chunking, embeddings, FAISS index, optional reranking, keyword fallback.
- Inline citations and source listing in responses.
- User/admin roles and approval flow.
- AI provider selection and routing (Ollama, OpenAI, Anthropic, DeepSeek, Open WebUI via OpenAI-compatible endpoint).
- n8n webhook integration for ingestion events.
- Docker-based deployment with MySQL/Redis; local run with SQLite.

## Out-of-scope
- Full web UI beyond Telegram.
- External MCP-compatible vector stores (only local FAISS + SQL).
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
  - Admin KB upload flow accepts one or multiple Telegram documents without manual type preselection; file type is auto-detected, Telegram size limits are validated, and per-file processing report is returned.
  - In KB search mode, if a user sends multiple questions подряд, bot processes them in FIFO order and replies under each original question (`reply_to`).
  - In KB search mode, long-running retrieval shows temporary progress indicator and removes it after final answer to keep chat clean.
  - ASR visibility control: users can toggle technical metadata display in their settings.
  - **ASR performance: support for `faster-whisper` engine and FP16/INT8 optimizations for high-speed transcription.**
  - **GPU Acceleration: automatic detection and utilization of NVIDIA GPUs (RTX 3090 support) inside Docker containers.**
  - Optional Hugging Face token (`HF_TOKEN`) is supported for gated/private model access and startup model loading.

- Backend API:
  - Auth by telegram_id and API key (`X-API-Key`).
  - CRUD for users and knowledge bases.
  - Ingestion endpoints for documents, images, web pages, wiki (crawl/git/zip).
  - RAG query endpoint returning answer + sources + metadata.
  - Job status endpoint for ingestion progress (where implemented).
- RAG pipeline:
  - Chunking with configurable size/overlap and Markdown-aware splitting.
  - Embeddings with sentence-transformers; FAISS for vector search.
  - Optional reranker with top-N candidates and top-k final results.
  - Fallback keyword search if embeddings unavailable.
  - Context assembly with `SOURCE_ID` tags for inline citations.
  - For definition-style questions ("что такое", "как определяется", "что включает"), ranking prioritizes explicit definitional fragments.
  - For questions with explicit clause references ("пункт N"), retrieval additionally prioritizes chunks containing numeric section markers (`N.`) and `пункт N`.
  - For factoid/legal/numeric questions ("кто", "как часто", "какой целевой показатель", "на 2030 год"), retrieval applies dedicated factual intent ranking + lexical fallback by terms/years/points.
- Safety/quality:
  - Strip unknown citations and untrusted URLs in answers.
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
- Admin KB upload does not require manual file-type selection; bot auto-detects document type, supports multiple files in one flow, validates Telegram file limits, and returns per-file success/failure report.
- Global admin-level "upload documents" entry is removed; document upload starts from a selected KB only.
- Admin can ingest: Markdown, PDF, Word, Excel, text, image, web URL, and wiki (crawl/git/zip).
- RAG query returns an answer plus a list of sources with path/URL and metadata.
- Inline citations are present when enabled and only reference provided sources.
- Command snippets in answers are filtered to those present in KB context.
- Web search uses DuckDuckGo and returns summarized results with source links.
- Direct AI mode works from reply keyboard button "🤖 Задать вопрос ИИ" and accepts text plus voice/audio (voice/audio are transcribed first, then sent to AI).
- Direct AI mode handles empty input with validation prompt and does not fail on long model outputs (responses are split into safe chunks).
- Direct AI mode on re-entry offers context restore/new dialog choice; selected context is loaded from DB.
- Direct AI mode prompt policy enforces concise first reply and clarification-first behavior for ambiguous user input.
- AI request metrics are persisted for all `ai_manager` calls and include provider/model/latency/status fields.
- AI mode shows temporary progress status for long requests (>5s predicted or observed) and removes it after completion to keep chat clean.
- RAG definition-style questions prefer glossary/definition fragments over generic policy mentions when both are present.
- RAG questions with "пункт N" return the corresponding clause context when it exists in indexed chunks (including numeric markers like `25.`/`26.`).
- RAG factoid/legal questions (including year/metric queries) return clause-level context when corresponding chunks exist in KB.
- In KB search mode, multiple user questions sent without waiting are answered in the same order and each bot reply is attached to its source user message.
- For long KB-search requests, bot shows temporary wait/progress message and deletes it after answer delivery.
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
- Any feature/bugfix that changes behavior updates `SPEC.md`, related design spec, and `docs/REQUIREMENTS_TRACEABILITY.md` in the same task.

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
