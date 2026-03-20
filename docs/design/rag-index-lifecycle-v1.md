# RAG Index Lifecycle v1

Date: 2026-03-19
Status: APPROVED:v1
Task: `RAGIDX-001`

## 1. Summary

### Problem statement

Three operational gaps make the RAG index brittle in production:

**1. No incremental re-indexing.**
When a document is updated (new version uploaded), the full KB must be
re-indexed via `scripts/reindex_kb.py`, which re-uploads every document
through the API. For a KB with 200+ documents this takes tens of minutes and
puts load on the ingestion pipeline. There is no partial update by
`document_id`.

**2. No managed embedding model migration path.**
Changing `RAG_MODEL_NAME` (e.g., from `multilingual-e5-base` to
`multilingual-e5-large`) silently makes all stored embeddings invalid — they
were computed with the old model but FAISS search uses the new model's query
embeddings. The mismatch is invisible until answer quality degrades. There is
no tooling to detect or handle model upgrades.

**3. Reranker model is not easily swappable.**
`RAG_RERANK_MODEL=BAAI/bge-reranker-base` is loaded at startup and used for
the life of the process. Switching to a better model (e.g., `bge-reranker-large`
or a fine-tuned variant) requires a full restart and has no rollback mechanism.
The current default `bge-reranker-base` is significantly weaker than available
alternatives.

### Goals

- Enable per-document re-indexing so one updated doc does not require full KB
  rebuild.
- Detect embedding model mismatches at startup and fail fast with actionable
  guidance.
- Provide a managed migration CLI that re-embeds all chunks with a new model
  without downtime.
- Make the reranker model easily upgradeable with a safe swap path and an
  explicit recommendation for a stronger default.
- Keep all changes backward-compatible with existing KBs and deployments.

### Non-goals

- Live model hot-swap without restart.
- Distributed multi-replica index coordination.
- Fine-tuning of embedding or reranker models.
- Automatic model selection or auto-upgrade.

## 2. Scope Boundaries

### In scope

- Per-document re-index API endpoint.
- Embedding model version metadata in `knowledge_bases` table.
- Startup mismatch detection with actionable error.
- Migration CLI (`scripts/migrate_embeddings.py`).
- Reranker model recommendation and swap procedure.
- Documentation updates.

### Out of scope

- Real-time (sub-second) partial index updates.
- Multi-model index (embedding model A for some docs, B for others).
- Fine-tuning pipeline.

## 3. Assumptions and Constraints

- One embedding model per KB at a time; mixing models within a KB is not
  supported.
- Migration is a background process, not a real-time API path.
- During migration the KB remains queryable with the old embeddings; the
  index is swapped atomically when migration completes.
- The `knowledge_bases` table gains a `embedding_model` column (nullable,
  defaults to `RAG_MODEL_NAME` at creation time).
- Backward compatibility: on first startup after the schema migration, a
  one-time backfill populates `embedding_model` for all existing KBs with the
  current `RAG_MODEL_NAME`. This makes the column effectively non-null going
  forward and ensures the mismatch check works for all KBs, not just new ones.
  The backfill is idempotent (skips rows where the column is already set).

## 4. Architecture

### 4.1 Per-document re-indexing

**New API endpoint:** `POST /api/v1/ingestion/reindex-document`

```json
{
  "document_id": 42,
  "knowledge_base_id": 1
}
```

**Behavior:**
1. Load all `knowledge_chunks` for `document_id` from DB.
2. Re-compute embeddings using current model.
3. Update `knowledge_chunks.embedding` in DB (transactional).
4. Mark the KB as "pending FAISS rebuild" in a lightweight in-memory set
   `_pending_rebuild_kbs: Set[int]` on the `rag_system` singleton.
5. Return `{"chunks_updated": N, "kb_id": 1, "faiss_rebuild": "pending"}`.

**Deferred FAISS rebuild** — a background thread polls
`_pending_rebuild_kbs` every `RAG_REBUILD_DEBOUNCE_SEC = 5` seconds:
- If a KB is in the pending set and has not received a new reindex request
  in the last `RAG_REBUILD_DEBOUNCE_SEC`, it is removed from the set and
  its FAISS index is rebuilt once.
- This batches N per-document calls into at most 1 FAISS rebuild per
  debounce window, regardless of how many documents were updated.

Phase 1 acceptance criterion: updating 20 documents in sequence triggers
exactly 1 FAISS rebuild (not 20).

**Existing `reindex_kb.py` script** is updated to call this endpoint
per-document in parallel (thread pool, 4 workers by default) and wait for
the debounce rebuild to complete via a `POST /api/v1/ingestion/flush-index`
endpoint (flushes pending rebuild immediately, returns when done).

### 4.2 Embedding model version tracking

**Schema change:** `knowledge_bases` table gains:

```sql
ALTER TABLE knowledge_bases
    ADD COLUMN embedding_model TEXT DEFAULT NULL;
```

Value is set at KB creation time to the current `RAG_MODEL_NAME`.

**Startup mismatch detection** in `rag_system._load_index(kb_id)`:

```python
kb_model = db.get_kb_embedding_model(kb_id)  # from new column
current_model = config.RAG_MODEL_NAME
if kb_model and kb_model != current_model:
    logger.error(
        "Embedding model mismatch: KB %d was indexed with '%s', "
        "current model is '%s'. Run: python scripts/migrate_embeddings.py "
        "--kb-id %d --new-model %s",
        kb_id, kb_model, current_model, kb_id, current_model
    )
    raise EmbeddingModelMismatchError(kb_id, kb_model, current_model)
```

The error is surfaced in the RAG diagnostics response and in the
`retrieval_query_logs.failure_reason` field.

### 4.3 Embedding migration CLI

`scripts/migrate_embeddings.py`:

```
python scripts/migrate_embeddings.py \
    --kb-id 1 \
    --new-model intfloat/multilingual-e5-large \
    [--batch-size 64] \
    [--dry-run]
```

**Steps:**
1. Load all chunks for the KB from DB.
2. Re-embed in batches using the new model (`--batch-size 64` default).
3. Write updated embeddings to a staging table `knowledge_chunks_migration`:
   same schema as `knowledge_chunks`, keyed by `(chunk_id, kb_id)`.
   The original `knowledge_chunks` table is untouched during re-embedding,
   so the KB remains queryable with old embeddings throughout.
   Memory/disk cost: one full copy of embeddings per KB; for 100k chunks at
   768 floats/chunk this is ~300MB. This is acceptable; note it in ops docs.
4. On completion: within a single DB transaction:
   - UPDATE `knowledge_chunks` SET embedding = staging.embedding
     FROM `knowledge_chunks_migration` WHERE ids match
   - UPDATE `knowledge_bases` SET `embedding_model` = new_model WHERE id = kb_id
   - DROP TABLE `knowledge_chunks_migration`
5. Rebuild FAISS index and invalidate semantic cache for this KB.
6. Log progress: `"Re-embedded N/Total chunks (X chunks/sec)"`.

**Dry-run mode:** compute new embeddings and report estimated time/memory
without writing to DB.

**Safety:** if migration is interrupted midway, the KB retains old embeddings
(shadow column is only promoted on success). Re-running the script resumes
from the last completed batch using a progress marker.

### 4.4 Reranker model upgrade

**Current default:** `BAAI/bge-reranker-base` (110M params, cross-encoder).

**Recommended upgrade path:**

Source: BEIR benchmark (Thakur et al., 2021); BGE model scores from BAAI/bge-reranker-v2-m3
HuggingFace model card (2024). NDCG@10 on BEIR aggregate (15 datasets).

| Model | Params | BEIR NDCG@10 | Notes |
|---|---|---|---|
| `BAAI/bge-reranker-base` | 110M | 50.1 | Current default |
| `BAAI/bge-reranker-large` | 335M | 54.2 | Drop-in replacement, ~2× slower |
| `BAAI/bge-reranker-v2-m3` | 568M | 56.1 | Multilingual, recommended for RU+EN |
| `cross-encoder/ms-marco-MiniLM-L-12-v2` | 33M | 39.0 | Fast, EN-only |

**Recommended new default:** `BAAI/bge-reranker-v2-m3` for deployments with
GPU. For CPU-only deployments keep `bge-reranker-base`.

**Swap procedure (no code change needed):**
```
RAG_RERANK_MODEL=BAAI/bge-reranker-v2-m3
```
Restart the backend. The new model is downloaded via HuggingFace cache on
first startup. No re-indexing required (the reranker operates on text, not
stored embeddings).

**Rollback:** set `RAG_RERANK_MODEL` back to previous value and restart.

**Documentation update:** add reranker model comparison table to `docs/OPERATIONS.md`.

## 5. Interfaces and Contracts

### New API endpoint

`POST /api/v1/ingestion/reindex-document`

| Field | Type | Required | Description |
|---|---|---|---|
| `document_id` | int | yes | ID of the document to re-index |
| `knowledge_base_id` | int | yes | KB that contains the document |

Response:
```json
{"chunks_updated": 42, "kb_id": 1, "ok": true}
```

Error if document not found or model mismatch detected.

### DB schema

`knowledge_bases.embedding_model TEXT` — nullable, set at creation, updated
by migration CLI.

### Error contract

`EmbeddingModelMismatchError` is a new exception class in `shared/rag_system.py`.
It is caught at the route level and returned as HTTP 409 (Conflict) — the service
is operational but the KB's stored data conflicts with the current configuration:
```json
{
  "detail": "KB 1 was indexed with model X; current model is Y. Run migration.",
  "error_code": "embedding_model_mismatch"
}
```

### Migration CLI

- Exit code 0: migration complete.
- Exit code 1: migration failed; DB unchanged.
- Exit code 2: dry run complete (no DB writes).
- Progress output to stdout in JSON Lines format for machine parsing.

## 6. Rollout and Evaluation

### Phase 1 — Schema and mismatch detection
- Add `embedding_model` column via migration script.
- Enable startup mismatch detection.
- No functional change for existing KBs.

### Phase 2 — Per-document re-index API
- Implement and test endpoint.
- Update `reindex_kb.py` to use it.
- Measure: re-indexing a single updated document should complete in < 5 seconds
  for a document with ≤ 100 chunks.

### Phase 3 — Migration CLI + reranker upgrade
- Ship migration CLI.
- Update `RAG_RERANK_MODEL` default recommendation in `env.template` and
  `docs/OPERATIONS.md` (keep current as default for safety; recommend upgrade).

## 7. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Partial migration leaves KB in inconsistent state | Shadow column promoted atomically only on full success |
| Per-document re-index triggers excessive FAISS rebuilds | Batch multiple doc updates before FAISS rebuild using a pending-reindex queue |
| Startup mismatch error blocks all queries for that KB | Error surfaces in diagnostics; other KBs on same instance are unaffected |
| Reranker model too large for available GPU memory | `bge-reranker-v2-m3` requires ~2.5GB GPU; add memory check and warn if below 3GB |
| Old `reindex_kb.py` users bypass new endpoint | Script updated to use endpoint; old behavior kept behind `--legacy-upload` flag for 1 cycle |

## 8. Acceptance Criteria

- `POST /api/v1/ingestion/reindex-document` with a valid `document_id` updates
  chunks in DB, rebuilds FAISS, and returns `{"ok": true, "chunks_updated": N}`.
- Loading a KB whose `embedding_model` differs from `RAG_MODEL_NAME` raises
  `EmbeddingModelMismatchError` with a message containing the migration command.
- `scripts/migrate_embeddings.py --dry-run` reports estimated time without
  writing to DB.
- Successful migration updates `knowledge_bases.embedding_model` to the new
  model name.
- Interrupted migration (SIGINT during batch) leaves the KB queryable with
  old embeddings.
- `python -m py_compile` passes on all new/modified files.
- All existing ingestion and retrieval regression tests remain green.

## 9. Pipeline Stage Mapping

| Stage | Where this feature hooks in |
|---|---|
| Startup — index load | Mismatch detection in `_load_index(kb_id)`; load from `.faiss` file if model matches |
| Ingestion — `POST /api/v1/ingestion/document` | Unchanged; continues to trigger full `rebuild_index` |
| New — `POST /api/v1/ingestion/reindex-document` | Re-embeds one document; queues KB in `_pending_rebuild_kbs` for debounced FAISS rebuild |
| Offline — `scripts/migrate_embeddings.py` | Re-embeds all chunks with new model via staging table; atomically swaps on completion |

## 10. Dependencies and Interactions

- **RAGPERF-001**: Both features write `.faiss` files to `RAG_INDEX_DIR`. Contract:
  - `rebuild_index(kb_id)` (called by per-document reindex and migration) always writes `{kb_id}.faiss` atomically via tmp rename.
  - RAGPERF-001 startup load checks `.faiss.meta.json` `model_name` field before trusting the file — exactly the check performed here.
  - Semantic cache entries for a KB are purged inside `rebuild_index` (RAGPERF-001 responsibility).
- **RAGEVAL-001**: No interaction. Judge operates on answers, not the index.
- **RAGCONV-001**: No interaction. Reformulation runs before retrieval; index is unchanged.

## 11. Secret-Safety Impact

- No new secrets. `RAG_REBUILD_DEBOUNCE_SEC` is a plain integer config.
- Staging table `knowledge_chunks_migration` stores float embeddings and chunk IDs — no raw text beyond what is already in `knowledge_chunks`.
- `EmbeddingModelMismatchError` message contains only model names (no secrets).

## 12. Spec and Doc Update Plan

- `SPEC.md`: add per-document reindex endpoint and embedding model version tracking requirements.
- `env.template`: add `RAG_REBUILD_DEBOUNCE_SEC=5`.
- `docs/OPERATIONS.md`: add reranker model comparison table (Section 4.4) and migration CLI usage.
- `docs/REQUIREMENTS_TRACEABILITY.md`: add ACs from Section 8.

## 13. Governance Review Gate

- Anti-hardcode: no corpus-specific logic. Mismatch detection is model-name comparison only. ✓
- Staging table migration is corpus-agnostic (operates on any KB by ID). ✓
- Approved by: review agent 2026-03-19 (PASS-WITH-CONDITIONS, conditions applied).
