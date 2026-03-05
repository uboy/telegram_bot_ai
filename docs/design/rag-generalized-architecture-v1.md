# Design: Generalized RAG Architecture v1

Date: 2026-03-05  
Owner: codex (architect)

## 1) Summary

### Problem statement
Текущий RAG работает, но имеет признаки query-overfitting: часть релевантности достигается ручными эвристиками под конкретные формулировки и домены, а не устойчивой retrieval-архитектурой.

Кодовые индикаторы:
- `detect_intent`, `extract_query_hints`, `apply_boosts`, `fetch_keyword_fallback_chunks` в `backend/api/routes/rag.py`.
- `howto_keywords`, `strong_tokens`, command-эвристики в `shared/rag_system.py`.
- Узкий eval-набор с акцентом на Sync&Build в `tests/rag_eval.yaml`.

### Goals
1. Убрать зависимость качества от ручных query-эвристик.
2. Построить универсальный retrieval для RU/EN и разных типов запросов.
3. Перевести качество в измеряемые retrieval/evidence-метрики с gate.
4. Сохранить совместимость API `/api/v1/rag/query` и UX бота.
5. Обеспечить наблюдаемость и безопасный rollback.

### Non-goals
1. Редизайн Telegram UI.
2. Полная смена auth/user flows.
3. Немедленная замена всех моделей без A/B и benchmark.

## 2) Scope boundaries

### In scope
1. Ingestion canonicalization и chunking.
2. Hybrid retrieval (dense + sparse) с fusion и rerank.
3. Перестройка контекст-пакинга и evidence tracing.
4. Диагностика retrieval и eval pipeline.
5. Обновление SPEC/design/traceability/ops.

### Out of scope
1. Внешние BI-дашборды.
2. Мульти-тенант IAM beyond current role model.
3. Полная замена БД-движка.

## 3) Assumptions + constraints

1. Обратная совместимость `POST /api/v1/rag/query` обязательна.
2. Текущий стек уже содержит SQLite/MySQL + Qdrant path; миграция делается поэтапно.
3. Секреты только через env и redaction в logs/diagnostics.
4. Требуется поддержка всех текущих ingest источников (doc/web/wiki/chat/code/image).
5. В production допускается dual-run (legacy + vNext) до прохождения quality gates.

## 4) Architecture

### 4.1 Target components

1. Source Adapters
- Использовать текущие загрузчики из `shared/document_loaders/*` и сервис ingestion.
- Нормализовать выход в единый `CanonicalDocument`.

2. Parser & Canonicalizer
- Сохранять структуру: section hierarchy, page/span, list/code/table blocks.
- Писать parser confidence и warnings в metadata.

3. Chunk Strategy Engine
- Иерархический chunking по структуре.
- Динамический размер чанка по типу документа.
- Явные связи чанков (`prev_id`, `next_id`, `parent_id`).

4. Embedding + Sparse Encoding Pipeline
- Dense embedding (multilingual).
- Sparse representation для lexical retrieval.
- Версионирование embedding/sparse моделей в metadata.

5. Index Writer
- SQL: source of truth (document/chunk/version lineage).
- Qdrant: dense + sparse + payload filters (`kb_id`, `source_type`, `language`, `updated_at`, `section_path`).

6. Retrieval Orchestrator v4
- Query analyzer без hardcoded domain rules.
- Parallel candidate generation: dense, sparse, metadata-filtered.
- Fusion через RRF (baseline).
- Optional learned calibration поверх fusion.

7. Reranker
- Cross-encoder rerank top-N.
- Fallback при деградации reranker к fusion-order.

8. Context Composer
- Evidence pack: лучший чанк + структурные соседи + section header.
- Policy против lost-in-the-middle.

9. Answer Guardrails
- Ответ только по evidence.
- Citation validity checks.
- URL/command sanitization (retain from existing safety pipeline).

10. Diagnostics & Eval
- Per-request trace + candidate logs.
- Offline benchmark и regression gates.

### 4.2 Data flow

1. Ingestion request -> loader -> parser/canonicalizer.
2. Canonical chunks -> SQL upsert (versions, lineage).
3. Dense + sparse vectors -> Qdrant upsert with payload.
4. Query -> query analyzer -> multi-channel retrieval.
5. Fusion (RRF) -> rerank -> context composer.
6. LLM answer -> guardrails -> answer + citations.
7. Trace logs -> diagnostics + eval store.

## 5) Interfaces / contracts

### 5.1 Public APIs

1. Keep compatible:
- `POST /api/v1/rag/query` (schema unchanged externally).

2. Extend:
- `GET /api/v1/rag/diagnostics/{request_id}`: channel-level scores, fusion ranks, rerank deltas.
- `POST /api/v1/rag/eval/run`: запуск benchmark suite.
- `GET /api/v1/rag/eval/{run_id}`: retrieval + faithfulness report.
- `POST /api/v1/rag/index/rebuild`: controlled reindex job.
- `GET /api/v1/rag/index/status/{job_id}`: reindex progress.

### 5.2 Internal contracts

```python
class CanonicalChunk(TypedDict):
    kb_id: int
    document_id: int
    version: int
    chunk_no: int
    content: str
    section_path: str
    block_type: str
    page_no: int | None
    char_start: int | None
    char_end: int | None
    token_count_est: int
    language: str
    parser_confidence: float | None
    parser_warning: str | None
    metadata: dict[str, Any]

def canonicalize_document(source: SourceDoc) -> list[CanonicalChunk]: ...
def encode_dense(chunks: list[CanonicalChunk]) -> list[list[float]]: ...
def encode_sparse(chunks: list[CanonicalChunk]) -> list[SparseVector]: ...
def upsert_hybrid_index(chunks: list[CanonicalChunk], dense: list[list[float]], sparse: list[SparseVector]) -> None: ...
def retrieve_candidates(query: str, kb_id: int, filters: dict) -> CandidateSet: ...
def fuse_rrf(candidates: CandidateSet, k: int) -> list[Candidate]: ...
def rerank(query: str, candidates: list[Candidate], top_n: int) -> list[Candidate]: ...
def compose_evidence_pack(query: str, ranked: list[Candidate], budget_tokens: int) -> ContextPack: ...
def persist_trace(trace: RetrievalTrace) -> None: ...
```

### 5.3 Error handling strategy

1. Parser fail по файлу: partial ingest + warning, без silent success.
2. Dense/sparse encoding fail: retry + isolate failed doc, не валить весь job.
3. Qdrant outage: controlled degraded mode + explicit flag в diagnostics.
4. Reranker fail: fallback на fusion rank + metric alert.
5. Trace/log write fail: non-blocking для ответа, blocking для eval runs.

## 6) Data model changes + migrations

### 6.1 Existing model updates

1. `knowledge_chunks` add:
- `chunk_hash`, `chunk_no`, `block_type`
- `parent_chunk_id`, `prev_chunk_id`, `next_chunk_id`
- `section_path_norm`, `page_no`, `char_start`, `char_end`
- `token_count_est`, `parser_confidence`, `parser_warning`
- `embedding_model`, `sparse_model`, `index_backend`
- `is_active` (for soft replacement during reindex)

2. `documents` / `document_versions`:
- enforce hash/version lineage for all source types
- store parser profile and extraction stats

### 6.2 New tables

1. `retrieval_query_logs` (already exists, extend schema if needed)
2. `retrieval_candidate_logs` (already exists, extend with channel/fusion/rerank deltas)
3. `rag_eval_runs`
4. `rag_eval_results`
5. `index_sync_audit` (SQL <-> Qdrant consistency checks)

### 6.3 Migration sequence

1. Additive schema migration.
2. Backfill canonical metadata from existing chunks.
3. Dual-write mode (legacy + new payload).
4. Shadow retrieval compare.
5. Quality gate.
6. Traffic cutover.
7. Legacy heuristic path disable behind feature flag.

## 7) Edge cases + failure modes

1. Duplicate clauses across documents.
2. OCR-poor PDFs and malformed DOCX.
3. Very long sections causing context truncation.
4. RU/EN mixed-language tokens.
5. Stale vectors after document updates.
6. Query with strong lexical constraints but weak semantics.
7. Large KB with high candidate fanout and rerank saturation.

Mitigations:
1. Source diversity and version-aware tie-breakers.
2. Parser confidence-aware ranking penalties.
3. Adjacency retrieval and section-level packing.
4. Language normalization + multilingual embeddings.
5. Index drift checker (`index_sync_audit`).
6. Hybrid retrieval with RRF.
7. Hard caps on candidate/rerank sizes with fallback mode.

## 8) Security requirements

1. Preserve API-key enforcement from existing deps.
2. Validate all retrieval filters to prevent broad/abusive queries.
3. Redact secrets/tokens from diagnostics.
4. No raw credentials in payload/log metadata.
5. Mandatory secret scan before merge.
6. Dependency additions only by explicit approval.

## 9) Performance requirements + limits

1. Retrieval quality targets:
- Recall@10 >= 0.95 (mixed-domain benchmark)
- MRR@10 >= 0.85 (factoid/legal subset)
- nDCG@10 >= 0.88
- Faithfulness >= 0.96
- Citation accuracy >= 0.97

2. Latency SLO:
- p95 `/rag/query` <= 6s (medium KB)
- p95 `/rag/query` <= 10s (large KB)
- p99 <= 14s

3. Operational limits:
- Candidate pool: up to 200 pre-fusion.
- Rerank cap: top 50 by default.
- Context budget: model-specific token caps with deterministic truncation.

## 10) Observability

Metrics:
1. dense/sparse channel hit-rate
2. fusion uplift vs single-channel baseline
3. rerank uplift delta
4. no-answer rate by intent
5. citation validity failures
6. latency p50/p95/p99
7. index drift count

Alerts:
1. faithfulness below threshold
2. sharp increase in fallback/degraded retrieval mode
3. Qdrant error-rate spike
4. index sync drift beyond limit

## 11) Test plan

Unit:
1. parser canonicalization invariants
2. chunk adjacency/linking
3. RRF/fusion correctness
4. rerank fallback logic
5. context packing anti-truncation rules
6. citation guardrails

Integration:
1. ingestion->index->query for each source type
2. dual-write and reindex jobs
3. diagnostics payload schema

E2E:
1. Telegram KB search with queue/progress unaffected
2. multi-query burst and citation correctness

Benchmark:
1. Expand `tests/rag_eval.yaml` from single-domain to multi-domain matrix.
2. Add adversarial paraphrases, RU/EN mix, numeric/factoid/legal/how-to.

Verification commands:
1. `python -m py_compile backend/api/routes/rag.py backend/services/ingestion_service.py shared/rag_system.py shared/qdrant_backend.py`
2. `.venv\Scripts\python.exe -m pytest -q tests/test_rag_quality.py tests/test_rag_query_definition_intent.py tests/test_rag_diagnostics.py tests/test_qdrant_backend.py`
3. `.venv\Scripts\python.exe scripts/rag_api_smoke_test.py --fail-on-empty`
4. `python scripts/scan_secrets.py`
5. `python scripts/ci_policy_gate.py --working-tree`

## 12) Rollout plan + rollback plan

Rollout:
1. Phase A: diagnostics hardening + baseline measurement.
2. Phase B: canonical chunk metadata and dual-write.
3. Phase C: hybrid retrieval + RRF in shadow mode.
4. Phase D: rerank/context composer cutover.
5. Phase E: disable hardcoded heuristics; keep feature-flag rollback.

Rollback:
1. `RAG_ORCHESTRATOR_V4=false` -> old orchestrator.
2. `RAG_BACKEND=legacy` -> FAISS/BM25 legacy path.
3. keep last stable index snapshot for fast restore.
4. incident report on each rollback.

## 13) Acceptance criteria checklist

1. [ ] Hardcoded query-specific ranking rules removed from primary path.
2. [ ] Hybrid dense+sparse retrieval active in production path.
3. [ ] RRF fusion and rerank trace available per request.
4. [ ] Multi-domain benchmark added and passing thresholds.
5. [ ] Diagnostics endpoint exposes channel/fusion/rerank evidence.
6. [ ] Citation validity >= threshold with tests.
7. [ ] Index drift checker implemented and monitored.
8. [ ] Rollback switches validated in staging.
9. [ ] No regression in bot KB query queue/progress behavior.
10. [ ] Secret scan and policy gate pass.

## 14) Spec/doc update plan (implementation phase)

1. Update `SPEC.md`:
- replace RAG architecture section with generalized hybrid design and SLO/quality gates.

2. Update `docs/REQUIREMENTS_TRACEABILITY.md`:
- map new ACs to implementation/tests.

3. Add this design doc:
- `docs/design/rag-generalized-architecture-v1.md`.

4. Update ops/user docs:
- `docs/OPERATIONS.md`
- `docs/USAGE.md`
- `docs/API_REFERENCE.md`

## 15) Secret-safety impact

Risk points:
1. vector DB credentials
2. provider API keys in model pipelines
3. diagnostics payload containing sensitive text

Controls:
1. env-only secret provisioning
2. diagnostics redaction policy
3. never log full auth headers/tokens
4. mandatory `scan_secrets` in review gate

## 16) What to redesign in current repo (file-level plan)

1. `backend/api/routes/rag.py`
- Move `detect_intent/extract_query_hints/apply_boosts/fetch_keyword_fallback_chunks` into generalized query-analyzer + orchestrator strategy.
- Remove domain-specific phrase lists from primary retrieval path.
- Keep backward-compatible response schema.

2. `shared/rag_system.py`
- Split monolith into retrieval orchestrator modules.
- Implement hybrid channels as first-class pluggable components.
- Keep legacy path behind feature flag only.

3. `shared/qdrant_backend.py`
- Add hybrid search requests (dense + sparse) and richer filter/index operations.
- Add index sync audit helpers.

4. `shared/document_loaders/chunking.py`
- Move from mostly character-size heuristics to canonical structure-aware chunking contracts.
- Persist chunk relations and parser confidence metadata.

5. `backend/services/ingestion_service.py`
- Enforce canonical metadata contract for all source types.
- Ensure stable doc versioning and deterministic reindex triggers.

6. `tests/rag_eval.yaml`
- Replace single-domain set with balanced multi-domain benchmark and adversarial paraphrases.

## 17) Analysis conclusions (evidence-grounded)

1. Research and production docs converge on hybrid retrieval + fusion + rerank as robust baseline.
2. RRF is a strong low-risk fusion default for heterogeneous channels.
3. Structure-aware chunking materially improves retrieval reliability on long/complex docs.
4. Quality must be measured separately for retrieval and generation; answer-only checks are insufficient.
5. Current repo should reduce heuristic coupling and increase measurable retrieval diagnostics.

## Approval

REVIEW REQUIRED — Reply "APPROVED:v1" or "CHANGES:<bullets>"
