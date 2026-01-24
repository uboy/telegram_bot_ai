# Implementation Plan (RAG Architecture Upgrade)

This plan is derived from the Product requirements and the target architecture.
It is designed as PR-sized steps with effort estimates and concrete tasks.

## Requirements (User-Facing)

1. Search across documents (wiki markdown, Word, PDF, web pages) with answers and citations.
2. Answer questions about large codebases with file/function references.
3. Index chat history and answer questions, generate summaries, instructions, and FAQs.

Non-functional:
- Fast, reliable retrieval (interactive latency target for typical KB sizes).
- Trustable answers with sources and previews.
- Incremental indexing for large repositories.

## Architecture Summary

- Telegram bot remains UI; backend provides ingestion, search, and job status.
- Shared RAG pipeline modularized: classify -> chunk -> embed -> index -> retrieve -> rerank.
- Hybrid retrieval: vector + BM25 + RRF fusion.
- Versioned documents and chunks; async ingestion with progress reporting.

## Work Breakdown (PR-Sized)

Effort scale: S (0.5-1d), M (1-3d), L (3-5d).

### PR-01: Data types + pipeline skeleton (S)

Scope:
- Add core dataclasses and pipeline stubs in shared/.

Files:
- Add `shared/types.py` additions (LoadedDocument, Chunk, SearchFilters, SearchResult, JobStatus).
- Add `shared/rag_pipeline/` modules with minimal interfaces.
- Update `shared/rag_system.py` to call pipeline stubs (no behavior changes yet).

Tasks:
- [ ] Define minimal dataclasses in `shared/types.py`.
- [ ] Create `shared/rag_pipeline/__init__.py`.
- [ ] Create `shared/rag_pipeline/classifier.py`, `chunker.py`, `embedder.py`, `retriever.py`, `reranker.py` with placeholders.
- [ ] Refactor `shared/rag_system.py` to use the pipeline interface while preserving current behavior.

Deliverable:
- Pipeline structure exists, current functionality unchanged.

Estimate: S

---

### PR-02: Document classification + adaptive chunking (M)

Scope:
- Add classification and type-specific chunking.

Files:
- Modify `shared/document_loaders/chunking.py` to support strategies.
- Update `shared/document_loaders/__init__.py` as needed.
- Implement `shared/rag_pipeline/classifier.py` and `chunker.py`.

Tasks:
- [ ] Implement heuristic classifier (by file extension + content markers).
- [ ] Add chunk strategies: markdown sections, code blocks, page/heading for PDFs/Word.
- [ ] Plug classifier into ingestion path (before chunking).
- [ ] Add unit tests for chunking strategies (basic fixtures).

Deliverable:
- Better chunk boundaries per doc type.

Estimate: M

---

### PR-03: Metadata schema upgrade + versioning (M)

Scope:
- Add document/version tables and chunk metadata.

Files:
- Update `shared/database.py` models and migrations.
- Update `shared/rag_system.py` to write versioned chunks and soft deletes.

Tasks:
- [ ] Add `documents`, `document_versions` tables.
- [ ] Extend `knowledge_chunks` with `document_id`, `version`, `is_deleted`, `metadata_json`.
- [ ] Add content hash, current version fields.
- [ ] Add migration path or automatic table update logic.
- [ ] Update ingestion to create/update versions on changes.

Deliverable:
- Versioned docs; safe reindexing.

Estimate: M

---

### PR-04: Hybrid retrieval (BM25 + vector + RRF) (M)

Scope:
- Add BM25 index and fusion with existing vector search.

Files:
- Add `shared/rag_pipeline/retriever.py` implementation.
- Update `shared/rag_system.py` retrieval path.

Tasks:
- [ ] Choose BM25 lib (e.g., Whoosh or local index in Python).
- [ ] Build per-KB BM25 index and keep in sync with chunks.
- [ ] Implement RRF fusion and filtering by metadata.
- [ ] Add retrieval unit tests for hybrid scoring.

Deliverable:
- Search improved for keyword-heavy queries.

Estimate: M

---

### PR-05: Optional reranking (S)

Scope:
- Add rerank toggle for top-k results.

Files:
- Implement `shared/rag_pipeline/reranker.py`.
- Update `shared/rag_system.py` to rerank when enabled.
- Update config to enable/disable reranking.

Tasks:
- [ ] Implement cross-encoder rerank in pipeline.
- [ ] Add config flag and default values.
- [ ] Add tests for rerank selection order (mock model).

Deliverable:
- Higher-quality top-k results for complex queries.

Estimate: S

---

### PR-06: Async ingestion jobs + status API (M)

Scope:
- Introduce ingestion jobs and progress.

Files:
- Add `backend/services/indexing_service.py`.
- Add `backend/api/routes/jobs.py`.
- Update `backend/api/routes/ingestion.py`.
- Update `shared/database.py` with jobs table.

Tasks:
- [ ] Add Job model (status, progress, stage, error).
- [ ] Create job on ingestion and return `job_id`.
- [ ] Process jobs in background (thread or worker).
- [ ] Add status endpoint.
- [ ] Add basic retry or failure reporting.

Deliverable:
- Users see ingestion progress and failures.

Estimate: M

---

### PR-07: Search filters + citations (S)

Scope:
- Add filters in API and expose via bot UI.

Files:
- Update `backend/api/routes/rag.py`.
- Update `backend/schemas/` with filter schemas.
- Update `frontend/backend_client.py`.
- Update `frontend/bot_callbacks.py` / `bot_handlers.py`.

Tasks:
- [ ] Add search filters (file type, language, date, path).
- [ ] Ensure responses include source previews and links.
- [ ] Add bot UI controls for filters.

Deliverable:
- Better precision; transparent sources.

Estimate: S

---

### PR-08: Codebase indexing enhancements (M)

Scope:
- Index large repos with incremental updates.

Files:
- Add `shared/document_loaders/code_loader.py`.
- Update ingestion service to support repo indexing.

Tasks:
- [ ] Implement repo loader (local path + git URL).
- [ ] Store file path, symbol hints, language in metadata.
- [ ] Incremental updates by file hash.

Deliverable:
- Fast, scalable code search.

Estimate: M

---

### PR-09: Chat history indexing + FAQ (M)

Scope:
- Add chat import and summarization utilities.

Files:
- Add `shared/document_loaders/chat_loader.py`.
- Extend ingestion service to support chat export.
- Add summarization helpers (optional in shared/utils.py).

Tasks:
- [ ] Parse chat export format(s).
- [ ] Chunk by thread/time window.
- [ ] Add endpoints for “summary” and “FAQ” queries if needed.

Deliverable:
- Chat-based knowledge retrieval.

Estimate: M

---

## Developer Checklist (Per PR)

- [ ] Update/verify schemas and migrations.
- [ ] Add tests for new logic (chunking, retrieval, jobs).
- [ ] Ensure bot responses include citations.
- [ ] Document config changes in `README.md` or `docs/`.
- [ ] Verify backward compatibility for existing KBs.

## Notes on Implementation Order

Start with PR-01, PR-02, PR-03 to build foundations. Then PR-04 and PR-05 for retrieval quality. Finish with PR-06 to improve UX, and PR-07 to expose filters. PR-08 and PR-09 are specialized but high-value for real users.
