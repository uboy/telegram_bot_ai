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
- AI provider selection (Ollama default; optional OpenAI).
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
  - Admin menu for users/KBs/ingestion/AI settings.
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
- Safety/quality:
  - Strip unknown citations and untrusted URLs in answers.
  - Sanitize command snippets not present in KB context.
- Storage:
  - SQL DB tables for users, knowledge bases, chunks, and import logs.
  - Optional Redis cache for conversation history.
- Integration:
  - n8n webhook events for ingestion actions.

## Non-functional requirements
- Reliability: bot and backend recover gracefully from ingestion or model errors.
- Latency: interactive responses for typical KB sizes (seconds, not minutes).
- Configurability: all operational settings via `.env` and DB-stored KB settings.
- Security: API key for bot → backend, admin ID allowlist, no secrets in code.
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
- Admin can ingest: Markdown, PDF, Word, Excel, text, image, web URL, and wiki (crawl/git/zip).
- RAG query returns an answer plus a list of sources with path/URL and metadata.
- Inline citations are present when enabled and only reference provided sources.
- Command snippets in answers are filtered to those present in KB context.
- Web search uses DuckDuckGo and returns summarized results with source links.
- Backend enforces API key header when configured.
- n8n receives `knowledge_import` events with KB id/name, source type, and stats.
- Docker Compose starts bot, backend, db, redis, and n8n with externalized data.

## Open questions
- Should admin approval be optional for certain environments (e.g., single-user mode)?
- Is OpenAI support required in all deployments or only optional via config?
- Do we need formal API versioning (`/api/v1`) stability guarantees?
- What is the expected maximum KB size and ingestion volume for baseline SLAs?

