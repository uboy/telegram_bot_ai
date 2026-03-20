# RAG Architecture Gap Review v1

Date: 2026-03-19
Status: APPROVED:v1
Task: `RAGSVC-005`

## 1. Summary

### Problem statement
The repository already has a stronger-than-average RAG foundation:
- canonical chunk metadata,
- hybrid retrieval,
- diagnostics,
- local multicorpus smoke,
- answer-lane safety and fallback handling.

However, the current architecture still underperforms on real corpora for several important pre-LLM cases:
- navigation/reference lookup,
- canonical setup lookup,
- exact patch/fix lookup,
- contamination from broad status/archive pages.

The core problem is no longer "missing basic RAG". The core problem is that retrieval, fusion, and context selection are not yet fully aligned with the behavior expected from high-quality production RAG systems.

### Goals
- Critically assess the current architecture against production-grade RAG criteria.
- Identify which existing design docs are strong, incomplete, or outdated.
- Define the next feature-level design slices required before further LLM tuning.

### Non-goals
- No runtime code changes in this document.
- No vendor migration decision in this cycle.
- No per-corpus manual tuning recommendations as the primary strategy.

## 2. Scope Boundaries

### In scope
- Current RAG service architecture and its design artifacts.
- Pre-LLM pipeline quality:
  - ingestion fidelity
  - chunking
  - candidate generation
  - fusion/rerank
  - context composition
  - eval/diagnostics
- Comparison with Open WebUI and common production RAG patterns.

### Out of scope
- Telegram UX redesign.
- LLM prompt tuning as the primary quality lever.
- Rewriting source documents inside this cycle.

## 3. Assumptions and Constraints

- The system must remain universal across mixed corpora and languages.
- Corpus-specific page-name boosts are not acceptable as steady-state architecture.
- Existing `/api/v1/rag/query` compatibility must be preserved.
- The next cycle should optimize pre-LLM quality first.
- Poorly structured source documents should be reported, not silently overfit in ingestion.

## 4. Assessment Criteria for Production-Grade RAG

The architecture is judged against these criteria:

1. Canonical parse/chunk contract
- Strong systems preserve source structure and expose it consistently downstream.

2. Multi-channel retrieval
- Strong systems combine dense, sparse, and structural/field-aware retrieval instead of relying on one channel.

3. Explicit handling of exact lookup vs broad exploration
- Production systems usually distinguish:
  - exact page/section/reference lookup
  - broad how-to retrieval
  - agentic/tool-driven retrieval when needed

4. Contamination control
- Broad tables, changelogs, archives, and status dumps must not dominate reference/navigation queries by accident.

5. Deterministic context composition
- Final context should be explainable and bounded by evidence-family logic.

6. Eval and diagnostics as first-class systems
- Real quality work requires repeatable local corpora, retrieval diagnostics, and regression fixtures that match live failure shapes.

7. Operational pragmatism
- Good working systems also address:
  - context-window constraints,
  - follow-up latency,
  - ingestion observability,
  - reindex requirements,
  - document preview/debuggability.

## 5. Review of Existing Design Artifacts

### 5.1 `rag-service-architecture-and-pipeline-v1.md`

Strengths:
- Correctly models the service as a staged pipeline.
- Separates retrieval, fusion, context composition, and answer generation.
- Sets the right high-level direction: recall first, aggregation second, answer synthesis third.
- Good enough as the current service-level source of truth.

Gaps:
- Stage 5 and Stage 7 do not yet explicitly separate exact-lookup routing from broad procedural retrieval.
- Stage 7 mentions contamination penalties but does not define a generic mechanism for them.
- Stage 8 lacks stronger rules for canonicality when a query is navigational/reference-oriented.
- The implementation roadmap needs a more concrete next sequence after the current family-aware work.

Verdict:
- Keep as the master service architecture doc.
- Extend it by feature-level slices rather than replacing it.

### 5.2 `generalized-procedural-retrieval-and-multi-corpus-validation-v1.md`

Strengths:
- Good design for broad procedural retrieval.
- Correctly pushes the system toward structural families instead of literal title matching.
- Good local/live validation framing.

Gaps:
- It is scoped to procedural retrieval and does not solve:
  - reference lookup,
  - canonical navigation,
  - noisy status-page contamination.
- It needs a sibling design rather than further expansion into all retrieval problems.

Verdict:
- Keep for broad HOWTO logic.
- Do not overload it with exact-lookup and contamination-control concerns.

### 5.3 Missing design slices

The current design set is missing at least four focused documents:
- contamination control and canonicality scoring,
- exact-lookup and navigation retrieval lane,
- multicorpus live-failure regression expansion,
- source-document quality contract and authoring guidance.

## 6. Comparison with Open WebUI and Production Patterns

### 6.1 Open WebUI

Grounded observations from the docs:
- Open WebUI emphasizes chunking quality:
  - markdown header splitting,
  - min-size merging to reduce tiny/noisy chunks.
- It exposes a togglable hybrid pipeline:
  - BM25
  - reranking via `CrossEncoder`
  - relevance thresholds.
- It treats many "RAG failures" as context/ingestion failures rather than model failures.
- It explicitly addresses context-window bottlenecks and follow-up latency with `RAG_SYSTEM_CONTEXT`.
- It supports both classic pre-injected RAG and tool-driven/agentic retrieval modes.

Implications for this repo:
- Our pipeline is stronger on diagnostics and explicit staged architecture.
- Open WebUI is stronger on operator-facing pragmatism:
  - extractor quality guidance,
  - chunk-fragment mitigation,
  - context-window realism,
  - alternate retrieval modes when classic RAG is not enough.

### 6.2 Common production patterns beyond Open WebUI

Observed common patterns in working systems:
- hybrid retrieval plus structured/metadata-aware signals,
- metadata filters or structured query-routing when exact lookup is likely,
- aggressive retrieval evaluation before prompt tuning,
- explicit reindex expectations when embedding or parser settings change,
- differentiated modes for:
  - focused retrieval,
  - full-context fallback,
  - tool-driven retrieval.

### 6.3 Critical comparison

Where this repo is already stronger:
- better formalization of the pipeline,
- richer retrieval diagnostics,
- better local eval and source-family framing,
- stronger additive architecture discipline.

Where this repo is weaker:
- too little design around exact lookup as a first-class retrieval mode,
- not enough generic contamination control,
- too little explicit operator guidance for context-window constraints,
- not enough source-document quality guidance for authors/maintainers.

## 7. Main Architectural Gaps

### Gap 1. No explicit exact-lookup / navigation lane
The current architecture still implicitly assumes one generalized retrieval path can handle:
- broad HOWTO,
- exact reference lookup,
- definition lookup,
- navigation to canonical docs.

Live corpus results show this is not enough.

### Gap 2. No generic contamination-control layer
Large noisy pages still outrank canonical pages:
- status dumps,
- archive/history pages,
- broad previewer notes.

This must be solved by generic scoring rules, not page-name bans.

### Gap 3. Eval coverage still misses live failure shapes
Current tests are strong, but live misses still escaped.
That means the eval suite is not yet representative enough.

### Gap 4. Source quality is not yet part of the architecture contract
The system can preserve structure, but it still lacks an explicit document-quality contract saying what makes a source retrieval-friendly.

## 8. Recommended Feature-Level Design Slices

1. Contamination control and canonicality scoring
- Define generic downranking and family penalties for noisy broad pages.

2. Exact-lookup and navigation lane
- Add a dedicated pre-LLM retrieval mode for:
  - `where is`
  - `what patch`
  - `api reference`
  - `official documentation`
  - similar canonical lookup intents

3. Multicorpus live-failure regression expansion
- Convert current OpenHarmony and ArkUI misses into committed regression fixtures and evaluation slices.

4. Source-document quality contract
- Define what document authors should do when they want good retrieval:
  - stable titles,
  - summary lead paragraph,
  - heading clarity,
  - avoiding mixed role pages.

## 9. Work Plan

### Phase 1. Architecture completion
- Approve the feature-level design package.
- Keep `rag-service-architecture-and-pipeline-v1.md` as master.
- Add the four missing sibling specs.

### Phase 2. Pre-LLM implementation sequence
1. contamination control
2. exact-lookup/navigation lane
3. eval expansion
4. source-quality reporting and docs guidance

### Phase 3. Post-hardening validation
- rerun local OpenHarmony smoke
- rerun local ArkUI smoke
- compare exact-lookup vs broad-howto performance separately
- only then move into answer-lane/LLM tuning

## 10. Acceptance Criteria

- A critical architecture review exists and explicitly identifies missing design slices.
- The current master architecture doc is confirmed as usable but incomplete.
- The next design package is decomposed into feature-level specs.
- The plan explicitly prioritizes pre-LLM retrieval improvements before LLM tuning.

## 11. Spec and Doc Update Plan

If implementation follows from this review, the implementation cycle must update:
- `SPEC.md`
- `docs/REQUIREMENTS_TRACEABILITY.md`
- `docs/TESTING.md`
- `docs/OPERATIONS.md`
- any affected `docs/design/*`

No spec update is required in this design-only cycle.

## 12. Secret-Safety Impact

- No secrets should appear in design examples.
- Local corpus references must stay generic or developer-local.
- Review artifacts must not include private raw corpus excerpts beyond the existing local-only scratch workflow.

## Approval

REVIEW REQUIRED — Reply "APPROVED:v1" or "CHANGES:<bullets>"
