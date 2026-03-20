# RAG Query Acceleration v1

Date: 2026-03-20
Status: COMPLETED:v1
Task: `RAGPERF-001`, `RAGPERF-002`

## 1. Summary

### Problem statement

The RAG pipeline has three latency-relevant gaps:

**1. No semantic query cache.**
Repeated or near-identical queries re-run the full pipeline: embed → FAISS
search → rerank → LLM. In KB-heavy deployments users frequently ask the same
questions (common onboarding steps, frequently-referenced commands). These
re-runs are wasteful in time and LLM token cost.

**2. FAISS index rebuilt from DB on every startup.**
On service restart the backend re-reads all `knowledge_chunks.embedding` JSON
columns and rebuilds the FAISS index in memory. With large KBs (>50k chunks)
this takes 60–120+ seconds during which the service is either unavailable or
serving from a cold (empty) index. There is no pre-warmed index file.

**3. No HyDE (Hypothetical Document Embeddings) path.**
For vague or abstract queries the user's question text is semantically distant
from the document text. For example "how do I set up the development
environment?" is far from the dense literal text "sudo apt-get install
build-essential". A hypothetical answer embedding can bridge this gap and
improve dense recall without changing the index.

### Goals

- Add a semantic cache that short-circuits the full pipeline for near-duplicate
  queries within a TTL.
- Persist the FAISS index to disk so startup loads from file rather than
  re-embedding from DB.
- Add HyDE as an optional retrieval augmentation, gated by config flag.
- Keep each feature independently toggleable via env flags.
- Measure actual latency reduction with before/after timings in diagnostics.

### Non-goals

- No distributed cache (Redis-based semantic cache is optional extension, see
  phase 2); phase 1 uses in-process LRU.
- No GPU acceleration of FAISS in this slice.
- No reranker model changes.
- No changes to ingestion pipeline.

## 2. Scope Boundaries

### In scope

- In-process semantic query cache (LRU, configurable capacity and TTL).
- FAISS index persistence to disk (save on write, load on startup).
- HyDE retrieval augmentation (optional, config-gated).
- Diagnostics fields: `cache_hit`, `hyde_applied`, startup timing.

### Out of scope

- Redis-based distributed cache.
- Qdrant backend changes.
- Embedding model changes.
- Answer caching (only retrieval candidates are cached).

## 3. Assumptions and Constraints

- The semantic cache stores (query_embedding, kb_id) → candidate list.
  Cache entries are invalidated when the KB is re-indexed.
- Cache hits bypass retrieval but still run the LLM generation step (answer
  text is not cached because it depends on prompt context and may change).
- FAISS index files are stored alongside the SQLite DB path; for MySQL
  deployments they use a configurable `RAG_INDEX_DIR` path.
- HyDE adds one LLM call before retrieval; it is off by default.
- All three features have independent env flags so any can be disabled without
  affecting the others.

## 4. Architecture

### 4.1 Semantic query cache

**Phase 1 — Exact-match cache (SHA-256 key)**

Cache key: `SHA-256(kb_id || normalized_query_text)` where
`normalized_query_text` is lowercased, whitespace-collapsed query text. Two
queries are a cache hit if and only if their SHA-256 keys match.

Rationale: exact-match is simple, correct, and has no false-positive risk.
It catches repeated identical queries, which is the primary production use
case (common onboarding questions, frequently-referenced commands).

**Phase 2 — Semantic similarity cache (future extension)**

In a future cycle, the key mechanism can be extended to a brute-force
cosine-scan over cached embedding vectors: on each query, compute the
embedding, then scan all cached entries for `kb_id` using dot-product; if
`max_similarity >= RAG_CACHE_SIM_THRESHOLD (0.97)`, return the cached result.
This scan is O(256) dot products per lookup (bounded by LRU capacity), which
is negligible. Phase 2 requires no cache key change — the existing
`OrderedDict` stores `(kb_id, query_emb)` tuples as values alongside the hash
key.

**Phase 1 implementation (this document):**
- `OrderedDict`-based LRU cache, keyed by `(kb_id, sha256_hex)`.
- Cache capacity: `RAG_CACHE_CAPACITY = 256` entries per KB. Global limit
  `RAG_CACHE_MAX_ENTRIES = 2048` total across all KBs.
  (When many KBs exist, the 2048 global cap limits the total footprint;
  with ≤ 8 KBs the per-KB 256 cap is the effective limit.)
- TTL: `RAG_CACHE_TTL_SEC = 1800` (30 min). Entries older than TTL are
  evicted on next access.
- Invalidation: all entries for a `kb_id` are purged when that KB is
  re-indexed (hook into existing `rag_system.rebuild_index`).

**Config flags:**
```
RAG_CACHE_ENABLED=true
RAG_CACHE_CAPACITY=256
RAG_CACHE_TTL_SEC=1800
RAG_CACHE_SIM_THRESHOLD=0.97
```

**Diagnostics:** `cache_hit: bool` added to `hints_json`.

### 4.2 FAISS index persistence

**Save:** after `rag_system.rebuild_index(kb_id)` completes, call
`faiss.write_index(index, path)` where:
```
path = {RAG_INDEX_DIR}/{kb_id}.faiss
```
Also save a metadata sidecar `{kb_id}.faiss.meta.json`:
```json
{
  "kb_id": 1,
  "chunk_count": 12450,
  "saved_at": "2026-03-19T14:00:00Z",
  "model_name": "intfloat/multilingual-e5-base",
  "dimension": 768
}
```

**Load:** on startup `_load_index(kb_id)` checks for the `.faiss` file first:
- If file exists AND `model_name` in metadata matches current
  `RAG_MODEL_NAME`: load from file (fast path, O(seconds)).
- If model mismatch or file missing: fall back to embedding from DB (existing
  behavior), then save the resulting index.

**Invalidation / atomic write:** when a KB is re-indexed, write to
`{kb_id}.faiss.tmp` first, then atomically rename to `{kb_id}.faiss`.
This avoids a window where the file is missing between delete and save.
The old file is never explicitly deleted — it is replaced by rename.
On load error (corrupt or truncated file), delete the bad file and fall
back to DB rebuild.

**Config flags:**
```
RAG_INDEX_DIR=data/faiss_indexes
RAG_INDEX_PERSIST_ENABLED=true
```

**Startup timing:** log `"FAISS index loaded from file in {ms}ms"` vs
`"FAISS index rebuilt from DB in {ms}ms"` so the improvement is visible.

### 4.3 HyDE (Hypothetical Document Embeddings)

**Concept:** before dense retrieval, generate a short hypothetical passage
that would answer the query, embed it, and add it as a third retrieval vector
alongside the original query embedding.

**Implementation:**
1. Call LLM with: `"Write a short factual passage that would answer: {query}.
   Max 3 sentences, no caveats."`
2. Embed the hypothetical passage with `_get_embedding(passage, is_query=False)`.
   Note: `is_query=False` is intentional — HyDE generates a *passage*, not a query.
   For E5 models this adds the "passage:" prefix, placing the HyDE vector in
   passage embedding space, which is exactly where the indexed document vectors
   live. The original query still uses `is_query=True` ("query:" prefix).
3. Run dense search with both the original query embedding AND the HyDE
   embedding.
4. Merge results using RRF (same as existing multi-query merge).

**Guard conditions (HyDE is skipped if):**
- LLM call fails or returns in < 5 words.
- Query is an exact-lookup query (already has strong field signal).
- `RAG_HYDE_ENABLED=false` (default).

**Config flags:**
```
RAG_HYDE_ENABLED=false
RAG_HYDE_MAX_TOKENS=80
```

**Diagnostics:** `hyde_applied: bool` added to `hints_json`.

## 5. Interfaces and Contracts

### Config

All new flags are additive; existing behavior is preserved when flags are
not set or are `false`.

| Flag | Default | Description |
|---|---|---|
| `RAG_CACHE_ENABLED` | `true` | Enable semantic LRU cache |
| `RAG_CACHE_CAPACITY` | `256` | Max entries per KB |
| `RAG_CACHE_TTL_SEC` | `1800` | Entry TTL in seconds |
| `RAG_CACHE_SIM_THRESHOLD` | `0.97` | Cosine similarity threshold for hit |
| `RAG_INDEX_PERSIST_ENABLED` | `true` | Persist FAISS index to disk |
| `RAG_INDEX_DIR` | `data/faiss_indexes` | Directory for `.faiss` files |
| `RAG_HYDE_ENABLED` | `false` | Enable HyDE augmentation |

### Diagnostics hints

```json
{
  "cache_hit": false,
  "hyde_applied": false,
  "faiss_load_source": "file" | "db"
}
```

### Invalidation contract

- Any call to `rag_system.rebuild_index(kb_id)` MUST purge cache entries for
  that `kb_id` and delete the `.faiss` file.
- This is enforced in `rag_system.py`; ingestion callers do not need to
  handle invalidation explicitly.

## 6. Rollout and Evaluation

### Phase 1 — FAISS persistence
- Lowest risk, highest startup impact.
- Measure startup time before/after with a 50k-chunk KB.
- Gate: startup time with cold file load ≤ 10% of rebuild-from-DB time.

### Phase 2 — Semantic cache
- Enable with default threshold 0.97 (conservative).
- Monitor cache hit rate in diagnostics for 1 week.
- Gate: cache hit rate ≥ 5% in production traffic.

### Phase 3 — HyDE (opt-in)
- Enable only on a test KB.
- Compare source-hit rate vs no-HyDE using the multicorpus smoke harness
  with vague queries ("what is the recommended way to...").
- Only promote to default-on if source-hit improves ≥ 5% with no regressions
  on exact-lookup and compound-HOWTO cases.

## 7. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Cache returns stale results after re-index | Invalidation is enforced inside `rebuild_index`; no bypass path |
| FAISS file corrupted or incomplete | On load error fall back to rebuild from DB; delete bad file |
| HyDE hypothetical adds wrong context | HyDE vector is one of three retrieval signals (merged via RRF); it cannot dominate on its own |
| FAISS file from different model loaded | Metadata sidecar contains `model_name`; mismatch triggers rebuild |
| Cache grows unbounded with many KBs | Global cap `RAG_CACHE_MAX_ENTRIES=2048`; LRU eviction |

## 8. Acceptance Criteria

- Second identical query to same KB returns `cache_hit: true` in diagnostics.
- After a KB re-index, cache entries for that KB are purged (next query gets
  fresh results).
- Service startup with pre-existing `.faiss` file is at least 5× faster than
  rebuild from DB for a KB with ≥ 10k chunks.
- `.faiss` file with wrong `model_name` in metadata triggers rebuild from DB.
- With `RAG_HYDE_ENABLED=true`, `hyde_applied: true` appears in diagnostics
  for non-exact-lookup queries.
- HyDE failure (LLM timeout) does not block retrieval.
- `python -m py_compile` passes on all modified files.
- Existing retrieval regression suite remains fully green.

## 9. Pipeline Stage Mapping

| Stage | Where this feature hooks in |
|---|---|
| Stage 0 — query receipt | Normalize query text (lowercase, whitespace collapse) for cache key |
| Stage 1 — pre-retrieval (HyDE) | LLM call to generate hypothetical passage; embed with `is_query=False` |
| Stage 2 — dense retrieval | Semantic cache check before FAISS search; FAISS load from `.faiss` file at startup |
| Stage 7 — diagnostics | `cache_hit`, `hyde_applied`, `faiss_load_source` written to `hints_json` |

## 10. Dependencies and Interactions

- **RAGIDX-001**: Both features write and read `.faiss` files in `RAG_INDEX_DIR`. Interaction contract:
  - Per-document reindex triggers `rebuild_index(kb_id)` → purges semantic cache for that KB and writes new `.faiss` file atomically.
  - Migration CLI atomically replaces `.faiss` file after re-embedding, ensuring RAGPERF-001 reads the correct file.
- **RAGEVAL-001**: Cache hits bypass retrieval but still run LLM generation → judge scores are still valid on cache hits.
- **RAGCONV-001**: Reformulated query text (not original) is used as the cache key; `conv_original_query` is logged in diagnostics.

## 11. Secret-Safety Impact

- No new secrets. `RAG_CACHE_*`, `RAG_INDEX_*`, `RAG_HYDE_*` are plain config values with no credentials.
- FAISS index files stored on disk contain only float vectors and chunk IDs — no raw text, no secrets.

## 12. Spec and Doc Update Plan

- `SPEC.md`: add semantic cache, FAISS persistence, and HyDE requirements.
- `env.template`: add all new `RAG_CACHE_*`, `RAG_INDEX_*`, `RAG_HYDE_*` flags (see Section 5).
- `docs/CONFIGURATION.md` (if present): document new flags.
- `docs/REQUIREMENTS_TRACEABILITY.md`: add ACs from Section 8.

## 13. Governance Review Gate

- Anti-hardcode: cache key is corpus-agnostic (SHA-256 of kb_id + normalized query). ✓
- HyDE prompt is generic, not corpus-specific. ✓
- Approved by: review agent 2026-03-19 (PASS-WITH-CONDITIONS, conditions applied).
