# RAG Implementation Roadmap v1

Date: 2026-03-20
Status: APPROVED — ready for implementation
Reviewer: team-lead-orchestrator (reviews every commit)

## Purpose

This document is the single source of truth for the implementation agent.
Each task references the approved design document that defines the exact
behavior, interfaces, and acceptance criteria to implement.

**Review protocol:** every commit is reviewed by the team-lead-orchestrator
before merge. Do not squash or batch multiple tasks into one commit.
One task = one commit (or a small focused PR). See Section 5 for review gates.

---

## Wave 1 — Foundation (no query-path changes, pure additive)

### IMPL-RAGIDX-1: Embedding model version tracking + mismatch detection

**Design doc:** `docs/design/rag-index-lifecycle-v1.md` §4.2
**Task ID:** RAGIDX-IMPL-001

**What to implement:**

1. DB migration: `ALTER TABLE knowledge_bases ADD COLUMN embedding_model TEXT DEFAULT NULL`
2. On-startup backfill (idempotent): set `embedding_model = RAG_MODEL_NAME` for all rows
   where `embedding_model IS NULL`.
3. Set `embedding_model` at KB creation time (in the ingestion service).
4. `EmbeddingModelMismatchError` exception class in `shared/rag_system.py`.
5. Mismatch check in `rag_system._load_index(kb_id)` — raises `EmbeddingModelMismatchError`
   with actionable message containing migration command.
6. Route-level catch: HTTP 409 with `{"detail": "...", "error_code": "embedding_model_mismatch"}`.

**Files to touch:**
- `shared/migrate.py` (migration)
- `shared/rag_system.py` (exception class + `_load_index` check)
- `shared/database.py` (KB creation, new column)
- `backend/api/routes/ingestion.py` (HTTP 409 handler)

**Acceptance criteria (from design doc §8):**
- Loading a KB whose `embedding_model` differs from `RAG_MODEL_NAME` raises
  `EmbeddingModelMismatchError` with message containing the migration command.
- `python -m py_compile` passes on all modified files.
- All existing ingestion and retrieval regression tests remain green.

**New tests required:**
- `tests/test_rag_index_lifecycle.py::test_mismatch_detection_raises_on_model_change`
- `tests/test_rag_index_lifecycle.py::test_no_mismatch_when_models_match`
- `tests/test_rag_index_lifecycle.py::test_startup_backfill_is_idempotent`

---

### IMPL-RAGPERF-1: FAISS index persistence to disk

**Design doc:** `docs/design/rag-query-acceleration-v1.md` §4.2
**Task ID:** RAGPERF-IMPL-001

**Depends on:** IMPL-RAGIDX-1 (needs `model_name` in FAISS metadata sidecar)

**What to implement:**

1. After `rebuild_index(kb_id)` completes:
   - Write to `{RAG_INDEX_DIR}/{kb_id}.faiss.tmp` via `faiss.write_index`
   - Atomically rename `.tmp` → `.faiss`
   - Write sidecar `{kb_id}.faiss.meta.json` with `{kb_id, chunk_count, saved_at, model_name, dimension}`
2. On startup `_load_index(kb_id)`:
   - Check for `.faiss` file
   - Load sidecar; if `model_name != RAG_MODEL_NAME` → fall back to DB rebuild
   - On load error (corrupt file) → delete and fall back to DB rebuild
   - Log `"FAISS index loaded from file in {ms}ms"` vs `"rebuilt from DB in {ms}ms"`
3. On re-index or model mismatch: delete old `.faiss` file (replaced by rename).
4. `RAG_INDEX_DIR` defaults to `data/faiss_indexes`; `RAG_INDEX_PERSIST_ENABLED` flag.

**Files to touch:**
- `shared/rag_system.py` (`rebuild_index`, `_load_index`)
- `shared/config.py` (`RAG_INDEX_DIR`, `RAG_INDEX_PERSIST_ENABLED`)

**Acceptance criteria (from design doc §8):**
- Service startup with pre-existing `.faiss` file is ≥ 5× faster than rebuild
  from DB for a KB with ≥ 10k chunks.
- `.faiss` file with wrong `model_name` in metadata triggers rebuild from DB.
- `python -m py_compile` passes.
- Existing retrieval regression suite remains green.

**New tests required:**
- `tests/test_rag_index_lifecycle.py::test_faiss_file_written_after_rebuild`
- `tests/test_rag_index_lifecycle.py::test_faiss_loaded_from_file_on_startup`
- `tests/test_rag_index_lifecycle.py::test_faiss_wrong_model_falls_back_to_db`
- `tests/test_rag_index_lifecycle.py::test_faiss_corrupt_file_falls_back_to_db`

---

## Wave 2 — Operational improvements

### IMPL-RAGIDX-2: Per-document reindex endpoint + debounce rebuild

**Design doc:** `docs/design/rag-index-lifecycle-v1.md` §4.1
**Task ID:** RAGIDX-IMPL-002

**Depends on:** IMPL-RAGPERF-1 (atomic `.faiss` write must exist before per-doc rebuild)

**What to implement:**

1. New endpoint `POST /api/v1/ingestion/reindex-document`:
   - Load all `knowledge_chunks` for `document_id`
   - Re-compute embeddings with current model
   - Update `knowledge_chunks.embedding` transactionally
   - Add `kb_id` to `rag_system._pending_rebuild_kbs: Set[int]`
   - Return `{"chunks_updated": N, "kb_id": K, "faiss_rebuild": "pending"}`
2. Background debounce thread in `rag_system`:
   - Polls `_pending_rebuild_kbs` every `RAG_REBUILD_DEBOUNCE_SEC` (default 5) seconds
   - If KB not updated in last debounce window → remove from set, call `rebuild_index(kb_id)`
3. New endpoint `POST /api/v1/ingestion/flush-index` — flushes pending rebuild immediately
   (for use by `scripts/reindex_kb.py`).
4. Update `scripts/reindex_kb.py` to call `/reindex-document` per-document (thread pool,
   4 workers default), then call `/flush-index` to wait for rebuild.

**Files to touch:**
- `shared/rag_system.py` (`_pending_rebuild_kbs`, debounce thread)
- `backend/api/routes/ingestion.py` (two new endpoints)
- `backend/schemas/ingestion.py` (request/response schemas)
- `shared/config.py` (`RAG_REBUILD_DEBOUNCE_SEC`)
- `scripts/reindex_kb.py` (use new endpoint)

**Acceptance criteria (from design doc §8):**
- `POST /api/v1/ingestion/reindex-document` with valid `document_id` updates chunks
  in DB and returns `{"ok": true, "chunks_updated": N}`.
- Updating 20 documents in sequence triggers exactly 1 FAISS rebuild (debounce).
- `python -m py_compile` passes.

**New tests required:**
- `tests/test_rag_index_lifecycle.py::test_reindex_document_updates_chunks`
- `tests/test_rag_index_lifecycle.py::test_debounce_coalesces_multiple_reindex_calls`
- `tests/test_rag_index_lifecycle.py::test_flush_index_triggers_immediate_rebuild`

---

### IMPL-RAGEVAL-1: LLM-as-judge offline eval + user feedback endpoint

**Design doc:** `docs/design/rag-answer-quality-evaluation-v1.md`
**Task ID:** RAGEVAL-IMPL-001

**Depends on:** none (fully additive; judge is offline-only)

**What to implement:**

1. DB migration: create `rag_answer_feedback` table (schema in design doc §4.2),
   including `UNIQUE(request_id, user_id)` and two indexes.
2. `POST /api/v1/rag/feedback` endpoint:
   - Accept `{request_id, vote: "helpful"|"not_helpful", comment?}`
   - Insert into `rag_answer_feedback`; swallow DB errors, always return `{"ok": true}`
3. Bot UX: after every KB-query answer, send inline keyboard `[👍 Полезно] [👎 Не то]`.
   - Callback `rag_feedback:{request_id}:{vote}`
   - On press: ACK user ("Спасибо!"), call `/api/v1/rag/feedback`, remove keyboard.
   - Auto-remove keyboard after 10 minutes (store `message_id` in user session).
4. `rag_eval_results` gains optional JSON column `judge_scores`.
5. Judge logic in `shared/rag_eval_service.py` (or new `shared/rag_judge.py`):
   - Prompt from design doc §4.1 (faithfulness/relevance/completeness 1–5)
   - Judge receives evidence pack truncated to 2000 chars
   - On failure: set `judge_skipped=True`, log warning, do not abort eval
   - Detect intentional refusal: set `intentional_refusal=True`, skip completeness
6. `rag_eval_service.run()` gains `run_with_judge: bool = False` flag.
7. `scripts/eval_quality_gate.py` gains `--judge-threshold` flag
   (e.g. `faithfulness=3.5,relevance=4.0`).

**Files to touch:**
- `shared/migrate.py`
- `shared/rag_eval_service.py` (or new `shared/rag_judge.py`)
- `backend/api/routes/rag.py` (new `/feedback` endpoint)
- `backend/schemas/rag.py` (`RAGFeedbackRequest`, `RAGFeedbackResponse`)
- `frontend/bot.py` (inline keyboard after answer)
- `frontend/handlers/` (feedback callback handler)
- `scripts/eval_quality_gate.py`

**Acceptance criteria (from design doc §8):**
- `/api/v1/rag/feedback` returns `{"ok": true}` even when DB is unavailable.
- `rag_answer_feedback` table created by migration with correct columns.
- Bot shows inline keyboard after each KB-query answer.
- Eval runner with `run_with_judge=True` calls judge and stores scores.
- Judge failure sets `judge_skipped=True` and does not abort the run.
- `--judge-threshold faithfulness=3.0` fails gate when mean faithfulness < 3.0.
- `python -m py_compile` passes on all new/modified files.

**New tests required:**
- `tests/test_rag_answer_quality.py::test_feedback_endpoint_returns_ok`
- `tests/test_rag_answer_quality.py::test_feedback_endpoint_swallows_db_error`
- `tests/test_rag_answer_quality.py::test_judge_scores_stored_on_eval_run`
- `tests/test_rag_answer_quality.py::test_judge_skipped_on_llm_failure`
- `tests/test_rag_answer_quality.py::test_quality_gate_fails_below_threshold`
- `tests/test_rag_answer_quality.py::test_intentional_refusal_sets_flag`

---

## Wave 3 — Query path changes (medium risk)

### IMPL-RAGPERF-2: Semantic query cache (SHA-256 exact match)

**Design doc:** `docs/design/rag-query-acceleration-v1.md` §4.1
**Task ID:** RAGPERF-IMPL-002

**Depends on:** IMPL-RAGIDX-2 (cache invalidation hook must exist in `rebuild_index`)

**What to implement:**

1. `OrderedDict`-based LRU cache in `rag_system`:
   - Key: `(kb_id, sha256_hex)` where `sha256_hex = SHA-256(kb_id || normalized_query_text)`
   - `normalized_query_text`: lowercase + whitespace-collapsed query
   - Capacity: `RAG_CACHE_CAPACITY=256` per KB, `RAG_CACHE_MAX_ENTRIES=2048` global
   - TTL: `RAG_CACHE_TTL_SEC=1800`; evict stale entries on access
2. Cache check at the start of `rag_query` (before dense retrieval):
   - Hit → skip FAISS + rerank, go directly to LLM generation
   - `cache_hit: bool` added to `hints_json`
3. Cache invalidation: inside `rebuild_index(kb_id)` purge all entries for that `kb_id`.
4. `RAG_CACHE_ENABLED` flag (default `true`).

**Files to touch:**
- `shared/rag_system.py` (LRU cache, `rag_query`, `rebuild_index`)
- `shared/config.py` (new cache flags)

**Acceptance criteria (from design doc §8):**
- Second identical query to same KB returns `cache_hit: true` in diagnostics.
- After KB re-index, cache entries for that KB are purged (next query gets fresh results).
- `python -m py_compile` passes.
- Existing retrieval regression suite remains green.

**New tests required:**
- `tests/test_rag_query_cache.py::test_second_query_is_cache_hit`
- `tests/test_rag_query_cache.py::test_reindex_purges_cache`
- `tests/test_rag_query_cache.py::test_different_kb_id_is_cache_miss`
- `tests/test_rag_query_cache.py::test_cache_disabled_flag_bypasses_cache`
- `tests/test_rag_query_cache.py::test_ttl_expiry_causes_miss`

---

### IMPL-RAGCONV-1: Conversation-aware query reformulation

**Design doc:** `docs/design/rag-conversation-aware-retrieval-v1.md`
**Task ID:** RAGCONV-IMPL-001

**Depends on:** IMPL-RAGPERF-2 (cache key uses reformulated query; must be in place first)

**What to implement:**

1. Schema: `ConversationTurn(role: Literal["user","assistant"], text: str)` in
   `backend/schemas/rag.py`. Add `conversation_context: Optional[List[ConversationTurn]]`
   to `RAGQuery`. Max 6 turns; silently truncate older turns.
2. Follow-up detection in `rag_system` (pre-retrieval):
   - Condition A: < 6 non-stopword tokens in current query
   - Condition B: contains pronoun/demonstrative from list in design doc §4.2
   - Only fires when `conversation_context` has ≥ 2 turns
3. LLM reformulation call (when follow-up detected):
   - Prompt from design doc §4.2 (last 2 prior turns, truncated to 400 tokens)
   - Reformulated query replaces original for all retrieval channels
   - Original query retained for LLM answer generation prompt
   - On LLM failure: log warning, use original query
4. Reformulated query injected into multi-query rewrite set.
5. Cache key = SHA-256 of reformulated query (when reformulation applied).
6. Diagnostics: `conv_reformulation_applied`, `conv_original_query`, `conv_turns_used`
   in `hints_json`.

**Files to touch:**
- `backend/schemas/rag.py` (`ConversationTurn`, `RAGQuery`)
- `shared/rag_system.py` (follow-up detection, reformulation step)
- `backend/api/routes/rag.py` (pass `conversation_context` through)

**Acceptance criteria (from design doc §8):**
- `conversation_context` field accepted by `/api/v1/rag/query` without schema error.
- Follow-up query after a prior exchange retrieves the correct source (source-hit).
- Reformulation not triggered for ≥ 6 non-stopword tokens + no pronouns.
- `conv_reformulation_applied` in diagnostics hints.
- Reformulation LLM failure → retrieval completes with original query.
- `python -m py_compile` passes.
- Existing single-turn regression suite remains green.

**New tests required:**
- `tests/test_rag_conversation.py::test_followup_detected_by_pronoun`
- `tests/test_rag_conversation.py::test_followup_detected_by_short_query`
- `tests/test_rag_conversation.py::test_long_query_no_pronouns_not_reformulated`
- `tests/test_rag_conversation.py::test_reformulation_fallback_on_llm_failure`
- `tests/test_rag_conversation.py::test_conv_hints_in_diagnostics`
- `tests/test_rag_conversation.py::test_single_turn_query_unaffected`

---

## Wave 4 — Heavy ops + experimental

### IMPL-RAGIDX-3: Embedding migration CLI + reranker upgrade docs

**Design doc:** `docs/design/rag-index-lifecycle-v1.md` §4.3, §4.4
**Task ID:** RAGIDX-IMPL-003

**Depends on:** IMPL-RAGIDX-1, IMPL-RAGPERF-1 (needs `.faiss` atomic write + model tracking)

**What to implement:**

1. `scripts/migrate_embeddings.py`:
   - Args: `--kb-id`, `--new-model`, `--batch-size` (default 64), `--dry-run`
   - Steps per design doc §4.3: staging table → batch re-embed → atomic swap
   - Progress output: JSON Lines to stdout
   - Exit codes: 0 success, 1 failure, 2 dry run
   - Interrupted migration leaves KB queryable with old embeddings
2. `docs/OPERATIONS.md`: add reranker comparison table (§4.4) and migration CLI usage.
3. `env.template`: note `RAG_RERANK_MODEL=BAAI/bge-reranker-v2-m3` as recommended
   for GPU deployments.

**Files to touch:**
- `scripts/migrate_embeddings.py` (new file)
- `docs/OPERATIONS.md`
- `env.template` (comment only)

**Acceptance criteria (from design doc §8):**
- `--dry-run` reports estimated time without writing to DB.
- Successful migration updates `knowledge_bases.embedding_model`.
- Interrupted migration (SIGINT during batch) leaves KB queryable with old embeddings.
- `python -m py_compile scripts/migrate_embeddings.py` passes.

**New tests required:**
- `tests/test_rag_index_lifecycle.py::test_migration_dry_run_no_db_writes`
- `tests/test_rag_index_lifecycle.py::test_migration_updates_embedding_model_column`
- `tests/test_rag_index_lifecycle.py::test_migration_interrupted_leaves_old_embeddings`

---

### IMPL-RAGPERF-3: HyDE retrieval augmentation

**Design doc:** `docs/design/rag-query-acceleration-v1.md` §4.3
**Task ID:** RAGPERF-IMPL-003

**Depends on:** IMPL-RAGEVAL-1 (≥ 2 weeks eval data required before enabling in production)
**Gate:** source-hit rate on vague queries must improve ≥ 5% with no regressions on
exact-lookup and compound-HOWTO cases (run multicorpus smoke before promoting to default).

**What to implement:**

1. When `RAG_HYDE_ENABLED=true` and query is not exact-lookup:
   - Call LLM: `"Write a short factual passage that would answer: {query}. Max 3 sentences, no caveats."`
   - Embed result with `_get_embedding(passage, is_query=False)` (passage space for E5)
   - Add HyDE vector as third retrieval signal alongside original query embedding
   - Merge results using existing RRF
2. Guard conditions: LLM failure, < 5 words returned, exact-lookup query → skip HyDE
3. `RAG_HYDE_ENABLED=false` (default); `RAG_HYDE_MAX_TOKENS=80`
4. Diagnostics: `hyde_applied: bool` in `hints_json`

**Files to touch:**
- `shared/rag_system.py` (HyDE step in `rag_query`)
- `shared/config.py` (`RAG_HYDE_ENABLED`, `RAG_HYDE_MAX_TOKENS`)

**Acceptance criteria (from design doc §8):**
- With `RAG_HYDE_ENABLED=true`, `hyde_applied: true` in diagnostics for non-exact-lookup queries.
- HyDE LLM timeout does not block retrieval.
- `python -m py_compile` passes.
- Existing retrieval regression suite remains green.
- Multicorpus smoke: source-hit on vague queries ≥ baseline + 5% (must be measured before merge).

**New tests required:**
- `tests/test_rag_query_cache.py::test_hyde_applied_flag_in_diagnostics`
- `tests/test_rag_query_cache.py::test_hyde_skipped_on_llm_failure`
- `tests/test_rag_query_cache.py::test_hyde_skipped_for_exact_lookup_query`
- `tests/test_rag_query_cache.py::test_hyde_disabled_flag`

---

## 5. Review Gates (applies to every commit)

The reviewer (team-lead-orchestrator) will check the following for each commit:

### Mandatory checks
- [ ] `python -m py_compile` on all new/modified `.py` files
- [ ] `python scripts/scan_secrets.py` — no tokens/keys/passwords
- [ ] All existing tests remain green (`pytest -q`)
- [ ] New tests listed in the task are present and pass
- [ ] No corpus-specific page-name logic introduced (anti-hardcode rule)
- [ ] No new config values hardcoded in source — all via `shared/config.py`

### Design conformance
- [ ] Implementation matches the design doc section referenced in the task
- [ ] API schema changes match the interface contract in the design doc
- [ ] DB migrations are idempotent (safe to run twice)
- [ ] Diagnostics fields named exactly as specified in the design doc

### Size limits (per commit)
- Max 12 files changed
- Max 2000 lines added
- Max 300 lines deleted

### Forbidden patterns
- No `import hardcoded_page` or page-name string constants in `rag_system.py`
- No `if kb_id == X:` corpus-specific branches
- No unguarded `os.remove` on FAISS files (use atomic rename pattern)
- No blocking operations in the debounce background thread outside of `rebuild_index`

### What the reviewer will produce
For each commit: a review artifact in `coordination/reviews/` with:
- PASS / PASS-WITH-CONDITIONS / FAIL verdict
- Any blocking issues (must be fixed before merge)
- Any non-blocking notes

---

## 6. Spec and traceability updates (after each task)

After each task is completed, the implementing agent must:
1. Update `SPEC.md` with any new endpoints, config flags, or behaviors introduced.
2. Add new ACs to `docs/REQUIREMENTS_TRACEABILITY.md`.
3. Update `coordination/tasks.jsonl` — set `status: completed` with evidence.
4. Update `coordination/state/codex.md` with a session entry.

The reviewer will verify these updates are present before marking a commit as PASS.
