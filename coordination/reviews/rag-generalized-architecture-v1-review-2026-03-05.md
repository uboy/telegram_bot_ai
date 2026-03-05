# Review Report: rag-generalized-architecture-v1

Date: 2026-03-05  
Reviewer: codex-review (independent pass)

## Inputs reviewed

1. Proposed design spec: `docs/design/rag-generalized-architecture-v1.md`.
2. Current implementation evidence:
- `backend/api/routes/rag.py`
- `shared/rag_system.py`
- `shared/qdrant_backend.py`
- `backend/services/ingestion_service.py`
- `tests/rag_eval.yaml`

## MUST-FIX issues

1. Capacity plan is underspecified for hybrid index growth.
- Gap: design sets SLO but not memory/storage sizing formula for dense+sparse payload.
- Risk: production OOM/latency collapse under large KB.
- Fix: add explicit sizing model (vectors/doc, payload bytes/chunk, shard/segment limits, reindex window).

2. Data retention and PII lifecycle is not explicit.
- Gap: no formal retention/deletion SLA for raw chunk content, diagnostics logs, and eval artifacts.
- Risk: compliance and privacy exposure.
- Fix: define retention matrix by table/artifact + hard-delete workflow + audit trail.

3. Dual-write consistency semantics need stricter guarantees.
- Gap: no idempotency key/transaction boundary between SQL write and Qdrant upsert.
- Risk: split-brain indexes and hard-to-debug retrieval misses.
- Fix: add write-ahead ingest event id + retry-safe idempotent upsert protocol.

4. Quality gate lacks statistical rigor.
- Gap: thresholds defined, but no baseline delta and no significance criteria.
- Risk: false confidence from small or biased benchmark.
- Fix: require baseline-vs-new deltas with minimum sample counts and confidence checks.

5. Failure-domain design for Qdrant outage is partial.
- Gap: degraded mode exists conceptually, but no explicit user-facing/error contract and backpressure policy.
- Risk: silent quality drop or cascading timeout.
- Fix: define fallback matrix per endpoint and operational circuit-breaker rules.

6. Parser supply-chain and model governance is incomplete.
- Gap: no strict policy for parser/model version pinning and rollback across docs.
- Risk: non-reproducible extraction drift.
- Fix: pin parser/model versions in metadata; block mixed-version index without migration marker.

## SHOULD-FIX issues

1. Add schema examples for new/extended APIs in design (request/response JSON samples).
2. Add concrete sharding/partition recommendation for multi-KB scale.
3. Add runbook for index snapshot/restore drill frequency.
4. Define multilingual benchmark slices explicitly (RU-only, EN-only, mixed).
5. Add acceptance criterion for index drift threshold and remediation SLA.

## Spec mismatches

1. Current design aligns with existing AC direction, but new ACs should be appended to SPEC before implementation starts.
2. Traceability rows should be pre-declared for new eval endpoints and index jobs.

## Commands run + results

1. `rg --files` and targeted `rg -n` scans for RAG heuristics and tests.
- Result: confirmed hardcoded heuristics and narrow eval domain.

2. `Get-Content` on key implementation/docs files.
- Result: verified current architecture and constraints.

3. External source review via primary docs/papers.
- Result: confirmed hybrid retrieval + fusion + rerank + structured chunking direction.

Note:
- No build/test commands executed in this pass because deliverable is design-only.

## Final verdict

FAIL (Design quality is high, but MUST-FIX items above should be addressed in v1.1 before implementation kickoff.)

## Clarifications required

1. Target max KB size and ingestion throughput for capacity model?
2. Retention policy requirements for logs/chunks/eval artifacts?
3. Is temporary degraded lexical-only mode acceptable for production incidents?
