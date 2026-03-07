# Design: RAG Max Quality Architecture v1 (Revised: Full Stack Replacement Required)

Status: OUTDATED / SUPERSEDED on 2026-03-07.
This full-stack-replacement roadmap is no longer the active implementation plan.
Current execution source of truth:
- `docs/design/rag-search-improvement-program-v1.md`
- `docs/design/rag-near-ideal-task-breakdown-v1.md`

Keep this document for historical context only.

Date: 2026-03-04  
Owner: codex (architect)

## 1) Summary

### Problem Statement
Требование изменено: нужно получить максимум качества **сейчас**, даже если это требует полной смены стека retrieval/индексации.

Текущий стек (FAISS + BM25 + rerank + route heuristics) уже улучшен, но не обеспечивает потолок качества для сложных factoid/legal/definition/how-to запросов на больших и гетерогенных корпусах.

### Goals
- Выполнить полную замену retrieval-стека на современную гибридную архитектуру:
  - внешнее векторное хранилище + sparse индекс,
  - калиброванный fusion,
  - сильный rerank,
  - structure-aware ingestion и canonical metadata.
- Повысить объективные quality-метрики до целевых порогов (ниже).
- Сохранить API-контракт бота (`/api/v1/rag/query`) и UX Telegram.

### Non-Goals
- Сохранение текущего internal FAISS/BM25 как production path.
- Мягкая “optional” миграция без cutover: по требованию пользователя делаем целевой production switch в этом цикле.

## 2) Scope Boundaries

### In Scope
- Полная миграция retrieval/indexing слоя:
  - Dense index + sparse index во внешнем backend.
  - Новый retrieval orchestrator.
  - Новый eval/diagnostics контур.
- Канонизация ingestion metadata.
- Перенастройка pipeline ранжирования/контекст-пакинга.
- Обновление docs/spec/traceability/ops.

### Out of Scope
- Редизайн Telegram UI.
- Изменение auth-модели пользователей.
- Нефункциональные рефакторинги, не влияющие на качество.

## 3) Assumptions + Constraints

- Разрешена смена стека и добавление зависимостей (по прямому запросу пользователя в этом треде).
- Требуется совместимость с текущими форматами ingestion:
  - pdf, doc/docx, xls/xlsx, md, txt, json/chat, zip, web/wiki, code, image.
- Текущие API endpoints должны остаться рабочими для bot/backend интеграции.
- Секреты/ключи не логируются и не попадают в артефакты.

## 4) Architecture

## 4.1 Target Stack (Production)

1. `Parsing & Canonicalization Layer`
- Replace/upgrade parsers to structure-preserving extraction:
  - PDF: layout-aware extraction (headings/lists/tables/clauses/page anchors).
  - DOCX: paragraph + heading + table semantics.
  - Markdown/code: AST-aware chunking.
- Normalize to `CanonicalChunk`.

2. `Hybrid Retrieval Backend`
- External vector+sparse backend (single production source of truth):
  - Dense vectors (multilingual embedding model).
  - Sparse lexical field (BM25-compatible).
  - Rich metadata filtering.

3. `Retrieval Orchestrator v3`
- Query understanding:
  - intent classification (definition/factoid/legal/howto/general),
  - entities, clause numbers, years, measures.
- Candidate generation channels:
  - dense ANN,
  - sparse lexical,
  - metadata-filtered channel,
  - adjacency expansion.
- Score fusion:
  - weighted RRF + calibration layer.

4. `Rerank & Context Composer`
- Strong cross-encoder rerank on fused top-N.
- Intent-aware context packing policy:
  - legal/factoid: clause precision first,
  - how-to: command continuity and procedure blocks.

5. `Answer Guardrails`
- Strict evidence grounding.
- Citation/source validity checks.
- Hallucination guards for commands/URLs/facts.

6. `Diagnostics & Eval`
- Retrieval trace per request.
- Offline benchmark runner with quality gates.

## 4.2 High-Level Data Flow

1. Ingestion -> parser -> canonical chunks.
2. Chunk upsert -> external hybrid index write.
3. Query -> orchestrator -> fused candidates.
4. Rerank -> context composer -> LLM.
5. Guardrails -> answer + citations.
6. Trace + metrics persisted for evaluation.

## 5) Interfaces / Contracts

## 5.1 Public APIs

### Keep (compatible)
- `POST /api/v1/rag/query`
  - same request/response schema as now.

### Add (required for operations and quality)
- `GET /api/v1/rag/diagnostics/{request_id}`
  - intent, hints, channel scores, fusion ranks, context-pack trace.

- `POST /api/v1/rag/eval/run`
  - start benchmark suite.
  - response: `run_id`, `status`.

- `GET /api/v1/rag/eval/{run_id}`
  - detailed metric report.

## 5.2 Internal Contracts

```python
class CanonicalChunk(TypedDict):
    kb_id: int
    source_type: str
    source_path: str
    content: str
    chunk_no: int
    chunk_hash: str
    heading_path: str
    page_no: int | None
    char_start: int | None
    char_end: int | None
    token_count_est: int
    parser_confidence: float | None
    parser_warning: str | None
    metadata: dict

def index_chunks_hybrid(chunks: list[CanonicalChunk]) -> IndexResult: ...
def retrieve_candidates_v3(query: str, kb_id: int, filters: dict) -> CandidateSet: ...
def fuse_and_rerank(query: str, candidates: CandidateSet) -> list[RankedCandidate]: ...
def compose_context(query: str, ranked: list[RankedCandidate], budget: int) -> ContextPack: ...
def persist_retrieval_trace(trace: RetrievalTrace) -> None: ...
```

## 5.3 Error Handling
- Parser failure on single doc: mark import as partial failed with file-level reason.
- Index backend outage: fail fast + retry strategy + no silent success.
- Retrieval channel failure: degrade to available channels but log degraded mode.
- Rerank failure: fallback to fusion order + alert metric.

## 6) Data Model Changes + Migrations

## 6.1 Existing DB Additions
- `knowledge_chunks` add fields:
  - `chunk_hash`, `chunk_no`, `heading_path_norm`, `page_no`,
  - `char_start`, `char_end`, `token_count_est`,
  - `parser_confidence`, `parser_warning`,
  - `embedding_model`, `index_backend`.

## 6.2 New Tables
- `retrieval_query_logs`
- `retrieval_candidate_logs`
- `rag_eval_runs`
- `rag_eval_results`

Migration strategy:
- additive migrations first,
- backfill job for historical chunks,
- cutover only after reindex completion.

## 7) Edge Cases + Failure Modes

- Duplicate clauses across docs -> wrong source chosen.
- Multi-language clause references.
- OCR-poor PDFs.
- Very long docs causing context truncation.
- Index drift between DB and external backend.

Mitigations:
- source diversity penalty/boost rules,
- language-aware token normalization,
- parser confidence-aware ranking,
- chunk adjacency reinforcement,
- periodic index consistency checker.

## 8) Security Requirements

- API key enforcement unchanged.
- Validate and sanitize filters/query hints before index calls.
- Diagnostics endpoint must redact secrets/tokens.
- No raw external credentials in logs.
- Secret scan required before merge.

## 9) Performance Requirements + Limits

Targets (mandatory):
- Recall@10 >= 0.97 (core benchmark set).
- MRR@10 >= 0.88 (factoid/legal subset).
- Faithfulness pass rate >= 0.97.
- p95 `/rag/query` latency:
  - <= 7s medium KB,
  - <= 12s large KB.

Operational bounds:
- Candidate pool caps by intent.
- Rerank cap configurable with fail-safe fallback.

## 10) Observability

Metrics:
- channel recall contribution,
- rerank uplift delta,
- no-answer rate by intent,
- guardrail trigger rate,
- latency p50/p95/p99.

Alerts:
- faithfulness drop below threshold,
- spike in legal/factoid no-answer,
- external index error rate spike,
- index-sync drift detected.

## 11) Test Plan

Unit:
- query understanding, fusion, rerank, context packing, guardrails.

Integration:
- ingestion->index->query for each source type.
- diagnostics endpoint correctness.

E2E:
- Telegram ordered queue + progress cleanup under load.
- multi-question burst scenario.

Quality suite:
- curated benchmark corpora (legal/factoid/howto/definition/mixed).
- CI quality gate by thresholds above.

Verification commands (target implementation phase):
- `python -m py_compile backend/api/routes/rag.py backend/services/ingestion_service.py frontend/bot_handlers.py frontend/bot_callbacks.py`
- `.venv\Scripts\python.exe -m pytest -q tests/test_rag_query_definition_intent.py tests/test_bot_text_ai_mode.py tests/test_bot_document_upload.py`
- `.venv\Scripts\python.exe scripts/rag_api_smoke_test.py --fail-on-empty`
- `python scripts/scan_secrets.py`
- `python scripts/ci_policy_gate.py --working-tree`

## 12) Rollout Plan + Rollback Plan

## 12.1 Rollout (Full Switch, No Optional Retention)
1. Prepare schema additions + canonical metadata writer.
2. Deploy external hybrid backend.
3. Run full reindex of KBs.
4. Enable new orchestrator in shadow validation mode (internal compare only).
5. Execute benchmark gates; if pass -> production cutover.
6. Keep old stack code for emergency rollback window only.

## 12.2 Rollback
- Single feature switch `RAG_V3_PROD=false` returns traffic to legacy path.
- Keep dual-index artifacts during rollback window.
- Incident report required for any rollback trigger.

## 13) Acceptance Criteria Checklist

- [ ] Production traffic switched to new external hybrid retrieval stack.
- [ ] Quality thresholds achieved on benchmark suite.
- [ ] No regression in source-type ingestion support.
- [ ] Telegram queue/progress behavior remains correct.
- [ ] Diagnostics available for each failed/missed retrieval case.
- [ ] Rollback switch validated in staging.

## 14) Spec/Doc Update Plan (Implementation Phase)

- `SPEC.md`
  - replace retrieval architecture section with new stack and thresholds.
- `docs/REQUIREMENTS_TRACEABILITY.md`
  - add mappings for new retrieval/eval/diagnostics AC.
- `docs/USAGE.md`
  - add eval/diagnostics usage.
- `docs/OPERATIONS.md`
  - add external backend operations, index sync, rollback procedure.

## 15) Secret-Safety Impact

Risk points:
- external backend credentials,
- diagnostics payloads,
- provider API keys.

Controls:
- env-based secret provisioning only,
- redaction in logs/diagnostics,
- mandatory `scan_secrets` on each cycle.

## Open Questions For Approval

1. Подтверждаете production backend для full stack switch (Qdrant + BM25-compatible sparse layer)?
2. Подтверждаете указанные quality thresholds как merge gate?
3. Разрешаете cutover только после полного reindex всех KB (без частичных исключений)?

## Approval

REVIEW REQUIRED — Reply "APPROVED:v1" or "CHANGES:<bullets>"
