# Design: Generalized RAG Architecture v2 (v1.1 hardening)

Date: 2026-03-05
Owner: codex (architect)
Replaces: `docs/design/rag-generalized-architecture-v1.md`

## 1) Summary

### Problem statement
Текущая RAG-реализация в проекте содержит слой ручных эвристик, который повышает качество на известных запросах, но ухудшает переносимость на новые формулировки и новые домены.

Прямые индикаторы в коде:
1. Доменные intent/boost/fallback правила в `backend/api/routes/rag.py`.
2. `howto_keywords` и `strong_tokens` в `shared/rag_system.py`.
3. Узкий eval-корпус в `tests/rag_eval.yaml`.

### Goals
1. Построить retrieval-архитектуру, которая обобщается на неизвестные запросы.
2. Удалить hardcoded domain-регулировки из primary retrieval path.
3. Ввести строгие качества и reliability gates с прозрачной статистикой.
4. Описать ресурсный (capacity) и инцидентный (failure-domain) контур.
5. Сохранить API/UX совместимость для бота.

### Non-goals
1. Редизайн Telegram UI.
2. Полная замена auth/user subsystem.
3. Обязательная смена всех текущих моделей в одном релизе.

## 2) Scope boundaries

### In scope
1. Canonical ingestion + structure-aware chunking.
2. Hybrid retrieval (dense + sparse), fusion, rerank.
3. SQL/Qdrant consistency protocol и reindex lifecycle.
4. Eval + diagnostics + quality gates.
5. Retention/PII policy для RAG-артефактов.
6. Failure-domain контракт и деградационные режимы.

### Out of scope
1. Новый пользовательский frontend.
2. Полная миграция на иной основной DBMS.
3. Vendor lock-in на конкретный reranker.

## 3) Assumptions + constraints

1. `POST /api/v1/rag/query` сохраняет backward compatibility.
2. Источники ingestion (doc/web/wiki/chat/code/image) сохраняются без функциональных регрессий.
3. `RAG_BACKEND=legacy` остается рабочим rollback-механизмом.
4. Секреты: только env, без логирования в открытом виде.
5. Внедрение по фазам с dual-run и quality gate перед cutover.

## 4) Target architecture

### 4.1 Components

1. Source adapters
- Переиспользовать текущие loaders/services.
- Нормализовать output в `CanonicalDocument`.

2. Parser + canonicalizer
- Извлекать структуру документа: heading path, list/code/table, page/span.
- Добавлять `parser_confidence`, `parser_warning`, `parser_profile`.

3. Chunk strategy engine
- Иерархическое разбиение с адаптацией к типу документа.
- Явные связи чанков: `prev_chunk_id`, `next_chunk_id`, `parent_chunk_id`.
- Стабильный `chunk_hash` для идемпотентной переиндексации.

4. Encoder pipeline
- Dense embeddings (multilingual).
- Sparse vectors (lexical retrieval).
- Версионирование: `embedding_model_id`, `sparse_model_id`.

5. Index writer
- SQL как source-of-truth.
- Qdrant как retrieval store (dense+sparse+payload filters).
- Dual-write через outbox + idempotency ключ.

6. Retrieval orchestrator v4
- Query understanding без статических доменных словарей в primary route.
- Candidate generation: dense channel + sparse channel + metadata-filtered channel.
- RRF fusion.
- Optional calibration layer (feature-flag).

7. Reranker
- Cross-encoder top-N rerank.
- Fallback на fusion order при ошибке/таймауте.

8. Context composer
- Evidence-pack: top chunk + структурные соседи + раздел.
- deterministic token budget policy.

9. Guardrails
- Citation/source validity checks.
- URL/command sanitization.
- Empty-evidence refusal policy.

10. Diagnostics + eval
- Per-request trace (channel ranks, fusion, rerank deltas).
- Offline benchmark + CI quality gate.

### 4.2 Data flow

1. Ingest source -> parse/canonicalize -> chunk/metadata.
2. SQL write (document/version/chunks/outbox event).
3. Async index writer consumes outbox -> upsert Qdrant.
4. Query -> multi-channel retrieval -> RRF -> rerank.
5. Context compose -> LLM -> guardrails.
6. Persist retrieval trace and metrics.

## 5) Interfaces and contracts

### 5.1 Public API compatibility

1. Keep:
- `POST /api/v1/rag/query`

2. Add/extend:
- `GET /api/v1/rag/diagnostics/{request_id}`
- `POST /api/v1/rag/eval/run`
- `GET /api/v1/rag/eval/{run_id}`
- `POST /api/v1/rag/index/rebuild`
- `GET /api/v1/rag/index/status/{job_id}`

### 5.2 API examples (added per review)

`POST /api/v1/rag/eval/run` request:
```json
{
  "suite": "rag-general-v1",
  "baseline_run_id": "2026-03-01-main",
  "slices": ["ru", "en", "mixed", "factoid", "howto", "legal", "numeric"]
}
```

Response:
```json
{
  "run_id": "eval_20260305_101530",
  "status": "queued"
}
```

`GET /api/v1/rag/index/status/{job_id}` response:
```json
{
  "job_id": "reindex_20260305_01",
  "status": "running",
  "processed_documents": 124,
  "processed_chunks": 18450,
  "failed_documents": 2,
  "drift_remaining": 0.007
}
```

### 5.3 Internal contracts

```python
class CanonicalChunk(TypedDict):
    kb_id: int
    document_id: int
    version: int
    chunk_no: int
    chunk_hash: str
    content: str
    block_type: str
    section_path: str
    page_no: int | None
    char_start: int | None
    char_end: int | None
    token_count_est: int
    language: str
    parser_profile: str
    parser_confidence: float | None
    parser_warning: str | None
    metadata: dict[str, Any]

class IndexOutboxEvent(TypedDict):
    event_id: str
    kb_id: int
    document_id: int
    version: int
    operation: Literal["UPSERT", "DELETE_SOURCE", "DELETE_KB"]
    idempotency_key: str
    payload_ref: str
```

## 6) Data model and migrations

### 6.1 Existing tables changes

`knowledge_chunks` add:
1. `chunk_hash`
2. `chunk_no`
3. `block_type`
4. `parent_chunk_id`
5. `prev_chunk_id`
6. `next_chunk_id`
7. `section_path_norm`
8. `page_no`
9. `char_start`
10. `char_end`
11. `token_count_est`
12. `parser_profile`
13. `parser_confidence`
14. `parser_warning`
15. `embedding_model_id`
16. `sparse_model_id`
17. `index_backend`
18. `is_active`

### 6.2 New tables

1. `index_outbox_events`
2. `index_sync_audit`
3. `rag_eval_runs`
4. `rag_eval_results`
5. `retention_deletion_audit`

### 6.3 Migration order

1. Additive migrations only.
2. Backfill canonical metadata for active chunks.
3. Enable outbox writer (dual-write OFF).
4. Enable dual-write ON for pilot KBs.
5. Run reindex and drift audit.
6. Shadow-read compare.
7. Quality gate.
8. Production cutover.

## 7) Dual-write consistency protocol (MUST-FIX closed)

1. SQL write and outbox event are in one DB transaction.
2. Outbox event has immutable `event_id` and deterministic `idempotency_key`.
3. Index worker:
- reads unprocessed outbox rows,
- executes idempotent Qdrant operation,
- marks event processed with `processed_at`.
4. Retries are safe:
- repeated event with same key must not duplicate points.
5. Exactly-once effect at index level is achieved as at-least-once delivery + idempotent consumer.
6. Drift auditor compares SQL active chunks vs Qdrant payload IDs and writes `index_sync_audit`.

## 8) Capacity and sizing model (MUST-FIX closed)

### 8.1 Input variables

1. `D`: documents per KB.
2. `C`: average chunks per document.
3. `N = D * C`: chunks per KB.
4. `Vd`: dense dimension.
5. `Sd`: sparse non-zero entries per chunk.
6. `Pd`: payload bytes per chunk (metadata + content preview).

### 8.2 Approximate memory/storage formulas

1. Dense vectors (float32):
- `DenseBytes ~= N * Vd * 4`
2. Sparse vectors (index+value int32/float32):
- `SparseBytes ~= N * Sd * 8`
3. Payload:
- `PayloadBytes ~= N * Pd`
4. Total raw:
- `RawBytes ~= DenseBytes + SparseBytes + PayloadBytes`
5. Index overhead coefficient:
- `TotalBytes ~= RawBytes * 1.3 .. 1.8` (depends on HNSW/segments)

### 8.3 Baseline planning targets

1. Small KB (N<=100k): single shard, p95 <= 6s.
2. Medium KB (100k < N <= 1M): shard by `kb_id` hash or dedicated collections.
3. Large KB (N > 1M): split per tenant/group, dedicated reindex window and segment tuning.

### 8.4 Reindex window model

1. `ReindexTime ~= N / ThroughputChunksPerSec`.
2. Throughput measured per environment and stored in ops baseline table.
3. Cutover allowed only if projected reindex < agreed maintenance window.

## 9) Retention and PII lifecycle (MUST-FIX closed)

### 9.1 Retention matrix

1. `knowledge_chunks`:
- keep active versions indefinitely unless source deleted.
- old versions: retain 30 days, then purge.
2. `retrieval_query_logs`, `retrieval_candidate_logs`:
- retain 30 days.
3. `rag_eval_runs`, `rag_eval_results`:
- retain 90 days.
4. `index_sync_audit`:
- retain 90 days.
5. `retention_deletion_audit`:
- retain 365 days.

### 9.2 PII controls

1. Diagnostics store only content preview with max length.
2. Optional redact rules for phone/email/token patterns before logging.
3. Full chunk content is never copied into diagnostics tables.

### 9.3 Delete workflow

1. Source delete request creates tombstone event.
2. SQL marks rows inactive and schedules hard delete.
3. Outbox emits index delete.
4. Hard delete job removes expired versions/logs by retention policy.
5. Every purge writes `retention_deletion_audit`.

## 10) Quality gates with statistical rigor (MUST-FIX closed)

### 10.1 Required slices

1. RU-only
2. EN-only
3. Mixed RU/EN
4. Factoid
5. How-to
6. Legal
7. Numeric/date
8. Long-context

### 10.2 Gate metrics

1. Retrieval:
- Recall@10
- MRR@10
- nDCG@10
2. Generation:
- Faithfulness
- Citation accuracy
- No-answer precision

### 10.3 Statistical conditions

1. Per-slice minimum sample size: `n >= 100` queries.
2. Cutover condition:
- metric >= threshold
- and delta vs baseline >= 0 (or approved exception)
- and bootstrap 95% CI for delta does not cross negative margin (default -0.01).
3. If any critical slice fails -> release blocked.

## 11) Failure-domain and degraded-mode contract (MUST-FIX closed)

### 11.1 Fallback matrix

1. Qdrant unavailable:
- `/rag/query`: serve `HTTP 200` with degraded retrieval flag + sparse-only path if available.
- diagnostics: include `degraded_mode=true`, `degraded_reason=qdrant_unavailable`.
2. Reranker timeout:
- use fusion order; mark `rerank_status=timeout`.
3. SQL logs unavailable:
- answer path continues, diagnostics persistence retried async.

### 11.2 Backpressure policy

1. Circuit breaker opens on repeated backend failures.
2. While open:
- cap candidate volume,
- disable expensive rerank,
- return concise answer or explicit no-evidence.
3. Auto half-open probe every fixed interval.

### 11.3 User-facing contract

1. No silent quality drop.
2. If evidence confidence below threshold -> explicit "недостаточно данных в базе" response.
3. Diagnostics always indicates degraded vs normal mode.

## 12) Parser/model governance (MUST-FIX closed)

1. Every chunk stores:
- `parser_profile` (tool+version),
- `embedding_model_id`,
- `sparse_model_id`,
- `index_epoch`.
2. Mixed index epochs in same active KB are blocked unless compatibility list allows.
3. Parser/model upgrades require:
- migration plan,
- shadow benchmark,
- rollback tag.
4. Emergency rollback:
- switch to last approved epoch and reindex from outbox snapshot.

## 13) Performance requirements + complexity

1. SLO:
- p95 `/rag/query` <= 6s (medium KB),
- p95 <= 10s (large KB),
- p99 <= 14s.
2. Candidate complexity:
- retrieval O(channels * log N) approximate,
- rerank O(top_n).
3. Limits:
- pre-fusion <= 200,
- rerank <= 50,
- context composer deterministic cap.

## 14) Security requirements

1. Preserve API-key enforcement.
2. Validate user filters (`source_types`, `languages`, date ranges, prefixes).
3. Prevent injection via strict schema parsing.
4. Redact secrets from logs/diagnostics.
5. Keep dependency policy: no new deps without explicit approval.

## 15) Observability and alerts

Metrics:
1. dense_hit_ratio
2. sparse_hit_ratio
3. rrf_uplift
4. rerank_uplift
5. degraded_mode_rate
6. faithfulness_rate
7. citation_validity_rate
8. index_drift_ratio

Alerts:
1. faithfulness below threshold.
2. degraded mode above SLO window.
3. drift ratio > 0.1% for > 15 min.
4. outbox backlog latency above threshold.

## 16) Sharding and partition strategy (added per review)

1. Default partition key: `kb_id`.
2. For high-volume KB:
- split by `kb_id + source_type` or dedicated collection.
3. Keep small KBs co-located; isolate large KBs.
4. Document shard plan in ops config with hard limits per collection.

## 17) Snapshot/restore runbook (added per review)

1. Snapshot frequency:
- daily full index snapshot,
- hourly metadata/outbox snapshot.
2. Drill frequency:
- monthly restore drill in staging.
3. Success criteria:
- restore completes within RTO,
- drift ratio after restore <= 0.1%.

## 18) Drift threshold and remediation SLA (added per review)

1. Drift thresholds:
- warning: > 0.05%
- critical: > 0.1%
2. SLA:
- warning resolved within 24h,
- critical resolved within 4h or rollback to legacy.
3. Remediation:
- replay outbox,
- targeted reindex for affected KB,
- incident postmortem for critical cases.

## 19) Test and verification plan

Unit:
1. canonicalization invariants
2. outbox idempotency
3. RRF and fallback logic
4. parser/model epoch guard

Integration:
1. ingest -> SQL/outbox -> Qdrant upsert
2. forced retry without duplicates
3. drift audit correctness
4. degraded mode contract

E2E:
1. bot KB queue/progress unaffected
2. mixed-language queries

Verification commands:
1. `python -m py_compile backend/api/routes/rag.py backend/services/ingestion_service.py shared/rag_system.py shared/qdrant_backend.py`
2. `.venv\Scripts\python.exe -m pytest -q tests/test_rag_quality.py tests/test_rag_query_definition_intent.py tests/test_rag_diagnostics.py tests/test_qdrant_backend.py`
3. `.venv\Scripts\python.exe scripts/rag_api_smoke_test.py --fail-on-empty`
4. `python scripts/scan_secrets.py`
5. `python scripts/ci_policy_gate.py --working-tree`

## 20) Rollout and rollback

Rollout:
1. Phase A: schema + outbox + diagnostics hardening.
2. Phase B: dual-write pilot on selected KBs.
3. Phase C: shadow-read and benchmark gate.
4. Phase D: production cutover by feature flag.
5. Phase E: remove hardcoded heuristics from primary path.

Rollback:
1. `RAG_ORCHESTRATOR_V4=false` -> previous orchestrator.
2. `RAG_BACKEND=legacy` -> legacy retrieval.
3. restore last approved index epoch snapshot.
4. publish incident report.

## 21) Acceptance criteria checklist

1. [ ] Primary retrieval path has no hardcoded query phrase boosting.
2. [ ] Outbox-based dual-write protocol implemented and idempotent.
3. [ ] Capacity model documented and approved with concrete limits.
4. [ ] Retention/PII policy enforced via scheduled jobs.
5. [ ] Statistical quality gate with CI and baseline deltas enabled.
6. [ ] Failure/degraded contract implemented and observable.
7. [ ] Parser/model/index epoch governance enforced.
8. [ ] Drift thresholds and SLA monitoring active.
9. [ ] No regression in existing bot KB search behavior.
10. [ ] Secret and policy checks pass.

## 22) Spec/doc update plan

1. Update `SPEC.md`:
- AC additions for outbox consistency, retention policy, statistical gates, degraded-mode contract.
2. Update `docs/REQUIREMENTS_TRACEABILITY.md`:
- map each new AC to code and tests.
3. Update `docs/OPERATIONS.md`:
- snapshot/restore drill, drift SLA, backpressure, fallback matrix.
4. Update `docs/API_REFERENCE.md`:
- diagnostics/eval/index API schemas.
5. Update `docs/USAGE.md`:
- degraded-mode behavior and eval usage.

## 23) Clarifications

1. Question: какой целевой максимум по объему KB для capacity-плана?
- Assumption: baseline target `<= 1M` chunks per medium deployment; larger requires dedicated collections.
2. Question: допускается ли degraded sparse-only режим в production?
- Assumption: да, если он явно маркируется и включен circuit breaker.
3. Question: какие регуляторные сроки хранения обязательны для вашей среды?
- Assumption: используем предложенный retention matrix; уточнение возможно до implementation phase.

## 24) Implementation snapshot (Phase A completed on 2026-03-05)

Implemented in this cycle:
1. DB foundation entities added:
- `index_outbox_events`
- `index_sync_audit`
- `rag_eval_runs`
- `rag_eval_results`
- `retention_deletion_audit`
2. Retrieval diagnostics schema extended:
- `retrieval_query_logs`: `degraded_mode`, `degraded_reason`
- `retrieval_candidate_logs`: `channel`, `channel_rank`, `fusion_rank`, `fusion_score`, `rerank_delta`
3. Outbox service implemented:
- idempotent enqueue,
- pending claim,
- processed/failed/dead lifecycle helpers.
4. Ingestion integration:
- successful non-empty upserts now enqueue outbox events (web/document/chat/archive/code/image flows).
5. Verification:
- focused tests and policy/secret checks executed (see review report in `coordination/reviews`).

## 25) Implementation snapshot (Phase B completed on 2026-03-05)

Implemented in this cycle:
1. Async outbox consumer worker added:
- backend service `backend/services/index_outbox_worker.py`,
- startup wiring in `backend/app.py`,
- configurable retry/backoff/dead-letter policy.
2. Drift audit loop implemented:
- periodic SQL-vs-Qdrant count audit,
- writes snapshots to `index_sync_audit`,
- warning/critical thresholds via env config.
3. Qdrant adapter extended:
- added `count_points(...)` for audit/filterable point counting.
4. Retrieval diagnostics upgraded:
- `rag_query` now persists `degraded_mode` and `degraded_reason`,
- diagnostics API returns channel/fusion/rerank delta fields from candidate logs.
5. Verification:
- focused runtime + regression tests passed for worker lifecycle, diagnostics fields, and Qdrant count API.

## 26) Implementation snapshot (Phase C part-1 completed on 2026-03-05)

Implemented in this cycle:
1. Retention lifecycle worker implemented:
- scheduled cleanup for retrieval logs, old document versions/chunks, eval artifacts, and drift audit rows,
- audit trail persisted to `retention_deletion_audit` for every cleanup policy run.
2. Eval orchestration services/API implemented:
- backend service `rag_eval_service` with persisted `rag_eval_runs` + `rag_eval_results`,
- API endpoints:
  - `POST /api/v1/rag/eval/run`
  - `GET /api/v1/rag/eval/{run_id}`
3. Runtime integration:
- retention loop integrated into index outbox background worker.
4. Verification:
- focused tests added for retention/eval services and eval API contract.

## 27) Implementation snapshot (Phase C part-2 completed on 2026-03-05)

Implemented in this cycle:
1. Statistical quality gate script added:
- `scripts/rag_eval_quality_gate.py`
- validates required slices/metrics against thresholds,
- enforces minimum sample size,
- compares run vs baseline delta with bootstrap 95% CI check against negative margin.
2. Eval persistence enriched for statistical gate:
- `rag_eval_service` now stores per-metric sample arrays in `RAGEvalResult.details_json.values`.
3. CI integration:
- quality-gate workflow compiles new script to protect contract from syntax/runtime drift.
4. Verification:
- focused tests added for gate logic and bootstrap behavior.

## 28) Implementation snapshot (Phase D kickoff completed on 2026-03-05)

Implemented in this cycle:
1. Feature-flag cutover wiring:
- added `RAG_ORCHESTRATOR_V4` in runtime config and env template.
2. `rag_query` primary path gating:
- when `RAG_ORCHESTRATOR_V4=true`, route-level query-specific intent boosts/fallback are disabled,
- ranking uses base retrieval score only (`rerank_score` or distance fallback),
- rollback remains one-flag switch (`RAG_ORCHESTRATOR_V4=false`).
3. Verification:
- added focused tests confirming v4 mode disables definition boost and keyword fallback.

Checklist note:
- item `Primary retrieval path has no hardcoded query phrase boosting` is now implemented behind Phase D feature flag and awaits full production cutover decision.

## Approval

REVIEW REQUIRED — Reply "APPROVED:v1" or "CHANGES:<bullets>"
