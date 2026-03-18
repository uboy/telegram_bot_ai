# RAG Service Architecture and Pipeline v1

Date: 2026-03-18
Status: draft for approval
Task: `RAGSVC-001`

## 1. Summary

### Problem statement
The current system already contains many strong RAG building blocks, but they have grown through issue-driven iterations. As a result:
- retrieval behavior is still partially influenced by symptom-specific heuristics,
- the architecture is not expressed as one end-to-end service pipeline,
- quality work is harder to prioritize because recall, aggregation, context composition, and answer generation are not clearly separated.

For a general-purpose RAG service, the target is not "answer one known query better". The target is:
- ingest arbitrary documents and wikis,
- preserve structure,
- find all relevant material with high recall,
- aggregate and compose evidence coherently,
- generate grounded answers or correct refusals,
- measure quality continuously across corpora.

### Goals
- Define the whole RAG service as an explicit pipeline with stable stage contracts.
- Make the design general-purpose for arbitrary uploaded documents, wikis, instructions, code, and mixed corpora.
- Provide a stage-by-stage implementation roadmap.

### Non-goals
- No one-shot full rewrite in this architect cycle.
- No claim that the design is already mathematically optimal.
- No new dependency adoption by default in the design phase.

## 2. Scope Boundaries

### In scope
- Ingestion to answer-generation pipeline.
- Storage/index consistency model.
- Retrieval and aggregation strategy.
- Context composition and safety.
- Diagnostics, evaluation, and quality gates.
- Implementation slicing and rollback boundaries.

### Out of scope
- Telegram UI redesign.
- Auth/user-management redesign.
- Mandatory immediate migration to a different vendor stack.

## 3. Assumptions and Constraints

- Existing API contract for `/api/v1/rag/query` must stay backward compatible.
- Existing ingestion surface must remain supported:
  - document
  - web
  - wiki
  - code
  - chat
  - image
- Local and CI-safe eval lanes must remain separate.
- Any design must support mixed languages and varied corpus structures.
- Hardcoded corpus-specific retrieval boosts should not be part of the steady-state architecture.

## 4. Service Model

The RAG service should be treated as a 10-stage pipeline:

1. Source acquisition and ingest orchestration
2. Parse and canonical document normalization
3. Chunking and structural graph construction
4. Index write and consistency management
5. Query understanding
6. Candidate generation
7. Fusion, rerank, and family aggregation
8. Evidence-pack context composition
9. Answer generation, grounding, and safety
10. Diagnostics, evaluation, and quality gates

The key principle is:
- retrieval recall first,
- coherent aggregation second,
- answer synthesis third.

## 5. Pipeline Stage Designs

### Stage 1. Source Acquisition and Ingest Orchestration

#### Responsibility
- Accept source input from API/runtime flows.
- Route each source to the correct loader path.
- Keep source identity, provenance, and ingest status explicit.

#### Inputs
- uploaded files
- URLs
- git/wiki roots
- local code paths
- chat exports
- images

#### Outputs
- ingest jobs
- normalized source descriptor
- source-level import log

#### Failure modes
- partial ingest success masked as full success
- wrong loader path chosen
- missing provenance for recovered/archive sources

#### Design requirements
- source provenance must stay explicit through the full pipeline,
- ingest status must distinguish success / partial / failed,
- recovery flows (for example wiki archive restore) must preserve original source identity.

#### Test strategy
- loader-selection regressions
- ingest status contract tests
- recovery-path tests

### Stage 2. Parse and Canonical Document Normalization

#### Responsibility
- Convert heterogeneous source material into a canonical document model.
- Preserve headings, structure, block types, parser metadata, and source semantics.

#### Inputs
- source payloads from stage 1

#### Outputs
- canonical document
- canonical blocks
- parser profile and warnings

#### Failure modes
- loss of heading/section structure
- parser silently flattening important content
- mixed parser semantics across source types

#### Design requirements
- every source type must map to one canonical structure contract,
- parser confidence/warnings must be retained,
- canonical fields must be stable enough for downstream retrieval and diagnostics.

#### Test strategy
- source-type-specific parser fidelity tests
- canonical metadata contract tests

### Stage 3. Chunking and Structural Graph Construction

#### Responsibility
- Split canonical documents into retrieval units without losing structure.
- Build adjacency and family relationships between chunks.

#### Inputs
- canonical documents from stage 2

#### Outputs
- chunks
- chunk graph links:
  - prev/next
  - parent/section family
  - document family identifiers

#### Failure modes
- chunks too coarse for recall
- chunks too small and semantically noisy
- no reliable section/document family boundaries

#### Design requirements
- chunking should be source-aware but canonical in output,
- downstream ranking must be able to recover document/section families,
- chunks must support deterministic neighbor expansion.

#### Test strategy
- chunk-boundary tests
- adjacency/section-family tests
- source-aware chunking regressions

### Stage 4. Index Write and Consistency Management

#### Responsibility
- Persist canonical chunks in the source-of-truth store.
- Synchronize retrieval indexes safely and observably.

#### Inputs
- chunk graph payloads from stage 3

#### Outputs
- SQL truth rows
- index outbox events
- retrieval-store upserts/deletes
- drift audit state

#### Failure modes
- SQL/index divergence
- silent partial indexing
- duplicate or missing chunk updates

#### Design requirements
- additive writes with idempotent outbox flow,
- explicit drift auditing,
- clear degraded-mode behavior if retrieval backend is stale/unavailable.

#### Test strategy
- outbox idempotency tests
- drift-audit tests
- index lifecycle tests

### Stage 5. Query Understanding

#### Responsibility
- Interpret the query only enough to guide retrieval.
- Detect broad query class without overfitting to specific corpora or phrases.

#### Inputs
- raw user query

#### Outputs
- normalized query representation
- lightweight query class / hints
- optional bounded query variants

#### Failure modes
- overclassification by exact words
- corpus-specific branching
- excessive rewrite fan-out

#### Design requirements
- this stage must be assistive, not dominant,
- literal keyword heuristics must be weak hints only,
- multi-query expansion must be bounded and auditable.

#### Test strategy
- rewrite boundedness tests
- cross-language / alternate-phrasing tests
- no-corpus-specific-boost regressions

### Stage 6. Candidate Generation

#### Responsibility
- Maximize recall of relevant material.
- Surface candidates through complementary channels.

#### Inputs
- normalized query/hints
- indexed chunk store

#### Outputs
- candidate rows with per-channel provenance

#### Required channels
- dense semantic retrieval
- lexical/BM25 retrieval
- metadata/field retrieval
- optional filtered or rewritten query channels

#### Failure modes
- right document family never enters candidate set
- one channel dominates and hides others
- recall too dependent on wording

#### Design requirements
- prioritize recall over early precision,
- keep channel provenance visible,
- ensure candidate budgets are explicit and configurable.

#### Test strategy
- channel contribution tests
- recall rescue tests
- diagnostics-origin tests

### Stage 7. Fusion, Rerank, and Family Aggregation

#### Responsibility
- Combine candidate channels into one coherent ranked set.
- Rank not only rows, but document/section families.

#### Inputs
- channel candidates from stage 6

#### Outputs
- fused rows
- family aggregates
- reranked shortlist

#### Failure modes
- mixed top-k from unrelated families
- reranker helping row relevance but hurting family coherence
- troubleshooting/version pages outranking the main procedure family

#### Design requirements
- family aggregation must be first-class,
- row ranking and family ranking must both be visible,
- structural coherence must matter more than exact query-token overlap.

#### Test strategy
- synthetic family-selection regressions
- mixed-language family tests
- contamination-penalty tests

### Stage 8. Evidence-Pack Context Composition

#### Responsibility
- Build the final bounded evidence pack for answer generation.
- Choose support chunks that maximize coverage and coherence.

#### Inputs
- reranked rows and family aggregates

#### Outputs
- ordered context pack
- inclusion diagnostics

#### Failure modes
- context assembled from random top chunks
- answer-critical support chunks excluded by token budget
- fallback using different evidence than the main path

#### Design requirements
- context should start from the winning family,
- budget policy must be deterministic,
- fallback and normal answer path must share the same evidence boundary.

#### Test strategy
- context-budget regressions
- support-chunk inclusion tests
- fallback-family-boundary tests

### Stage 9. Answer Generation, Grounding, and Safety

#### Responsibility
- Generate an answer only from selected evidence.
- Refuse or degrade correctly when evidence is insufficient or unsafe.

#### Inputs
- evidence pack from stage 8

#### Outputs
- answer
- citations
- refusal/degraded response when needed

#### Failure modes
- stitched answer from irrelevant evidence
- hallucinated commands or URLs
- leaking provider transport errors or unsafe content

#### Design requirements
- answer path must be downstream of retrieval quality, not compensating for it,
- extractive fallback should remain available on provider failures,
- safety checks must preserve grounded content and reject invented content.

#### Test strategy
- grounded-answer regressions
- extractive-fallback regressions
- refusal/security regressions

### Stage 10. Diagnostics, Evaluation, and Quality Gates

#### Responsibility
- Measure retrieval, aggregation, context composition, and answer quality.
- Provide fast and slow feedback loops.

#### Inputs
- runtime traces
- local corpora
- eval datasets and source manifests

#### Outputs
- per-request diagnostics
- local quality runs
- baseline deltas
- gated metrics by slice/source family

#### Failure modes
- tuning on one query with no broader validation
- metrics that hide recall failures behind answer quality
- eval tied to one corpus only

#### Design requirements
- diagnostics must expose channel, fusion, family, and context decisions,
- eval must cover multiple corpora and query classes,
- local/live smoke lanes must be opt-in and explicit.

#### Test strategy
- deterministic contract tests
- local multi-corpus smoke
- optional live-backend smoke

## 6. Cross-Cutting Principles

### 6.1 Canonical document-first design
All source types must converge to one canonical document/chunk contract.

### 6.2 Recall-first retrieval
The system should first avoid missing relevant material, then optimize precision and answer quality.

### 6.3 Family-aware ranking
The final answer should come from coherent document/section families, not a random chunk mix.

### 6.4 Deterministic bounded context
Context packing must be explainable and reproducible.

### 6.5 Diagnostics-led tuning
Architectural choices should be validated through diagnostics and eval, not only anecdotal query wins.

## 7. Implementation Roadmap

### Phase A. Architecture consolidation
- establish this document as the service-level source of truth
- map existing code to pipeline stages
- mark which stage contracts already exist and which are still heuristic

### Phase B. Retrieval-core hardening
- reduce residual corpus/query-specific boosts
- strengthen recall-first candidate generation
- introduce explicit family aggregation

#### Phase B, slice 1 implemented on 2026-03-18
- route-level context/fallback selection now applies a generic family-first ordering before narrower query-class refinements;
- this slice is intentionally narrow:
  - no retrieval backend change,
  - no new dependency,
  - no API contract change,
  - only better ordering of close-scored candidate families for context and provider fallback;
- focused regressions cover:
  - generic family ordering on non-HOWTO rows,
  - route-level context selection for a non-HOWTO query,
  - existing procedural/fallback regressions remaining green.

#### Phase B, slice 2 implemented on 2026-03-18
- retrieval diagnostics now surface family annotations for candidate rows:
  - `family_key`
  - `family_rank`
- this keeps family-aware routing observable without changing the public query contract;
- focused diagnostics regressions cover:
  - persistence of family annotations,
  - context-support rows retaining family identity,
  - OpenAPI schema exposure for the additive fields.

#### Phase B, slice 3 implemented on 2026-03-18
- generalized retrieval-core fusion in `shared/rag_system.py` now computes structural family support from dense, BM25, and metadata-field channels using existing `doc_key` / `scope_key` contracts instead of corpus-specific query words;
- family-aware ordering activates only when there is actual corroboration across channels or multiple candidate hits, so singleton-only candidate sets preserve the original fusion order;
- rerank window selection now sees the same family-aware order, preventing supported families from being dropped before reranking;
- focused regressions cover:
  - rerank-window promotion of a structurally supported family,
  - generalized no-rerank ordering by supported family,
  - singleton-only candidate sets preserving the previous fusion order.

#### Phase B, slice 4 implemented on 2026-03-18
- route-level HOWTO boosts no longer hardcode the literal `Sync&Build` page name;
- broad procedural ranking now relies on generic field coverage and family support, so a misleading title match cannot outrank a stronger procedural family on name alone;
- focused regressions cover:
  - misleading title match losing to stronger procedural evidence,
  - existing compound-HOWTO family-focus behavior remaining green.

### Phase C. Context and fallback hardening
- make context/fallback family-bounded
- improve deterministic evidence-pack coverage

### Phase D. Multi-corpus validation
- generalize local smoke harnesses
- add additional local corpus fixtures
- add optional live backend smoke

#### Phase D, slice 1 implemented on 2026-03-18
- eval-service suite loading now supports named public-safe datasets instead of one implicit fixture path;
- the committed `rag-multicorpus-v1` suite validates both `open_harmony_docs` and `arkuiwiki_docs` without embedding local absolute paths;
- the source manifest now exposes developer-local corpus roots only through env overrides, including `RAG_EVAL_LOCAL_ARKUIWIKI_PATH`;
- focused regressions cover:
  - named suite-path resolution,
  - suite-name propagation into YAML loading,
  - public-safe multicorpus dataset contract.

### Phase E. Quality-gated iteration
- compare candidate strategies on metrics and failure analysis
- promote the best one to default only after evidence

## 8. Acceptance Criteria

- The architecture is defined as an explicit stage pipeline rather than issue-specific fixes.
- Each pipeline stage has responsibilities, contracts, failure modes, and a test strategy.
- The implementation roadmap is stage-based and rollback-friendly.
- The service design is explicitly general-purpose for arbitrary uploaded documents and mixed corpora.
- Future retrieval work is judged by recall, family coherence, context quality, and answer grounding, not by one query symptom.

## 9. Spec and Doc Update Plan

Implementation cycles derived from this design must update:
- `SPEC.md`
- `docs/REQUIREMENTS_TRACEABILITY.md`
- `docs/TESTING.md`
- `docs/CONFIGURATION.md` when env/contracts change
- `docs/OPERATIONS.md` when runtime/triage behavior changes

## 10. Secret-Safety Impact

- Keep local corpora local-only and env-driven.
- Do not commit raw private corpora or live smoke payloads with sensitive excerpts.
- Redact credentials from provider/backend URLs in diagnostics and smoke reports.

## Approval

REVIEW REQUIRED — Reply "APPROVED:v1" or "CHANGES:<bullets>"
