# RAG Contamination Control and Canonicality v1

Date: 2026-03-19
Status: approved for implementation
Task: `RAGSVC-006`

APPROVED:v1

## 1. Summary

### Problem statement
Real-corpus validation shows that broad noisy pages still contaminate retrieval:
- `DEV_API_STATUS`
- archive/history pages
- broad previewer notes
- broad process dumps

These pages often contain many query terms and therefore outrank the one canonical page that should answer the question.

### Goals
- Introduce a generic contamination-control layer for pre-LLM retrieval.
- Promote canonical pages for definition, navigation, setup, and exact patch/reference lookup.
- Keep the mechanism universal and free from corpus-name hardcodes.

### Non-goals
- No manual per-document role labeling as the primary mechanism.
- No blacklist of specific page names.
- No changes to answer generation in this slice.

## 2. Scope Boundaries

### In scope
- Stage 6 and Stage 7 retrieval/fusion behavior.
- Candidate-level and family-level contamination signals.
- Diagnostics for canonicality and contamination decisions.

### Out of scope
- Chunk parser changes.
- LLM prompt changes.
- Source-document rewriting.

## 3. Assumptions and Constraints

- Signals must remain generic across languages and corpora.
- Existing structural metadata is available:
  - `doc_title`
  - `section_title`
  - `section_path`
  - `source_path`
  - chunk text
- The system already supports family-aware ordering and field-aware retrieval.

## 4. Architecture

### 4.1 Concept
Add a canonicality layer on top of current hybrid retrieval.

Each candidate/family receives two additional scores:
- canonicality score
- contamination penalty

Final ranking uses:
- retrieval relevance,
- structural specificity,
- family support,
- canonicality,
- contamination penalty.

### 4.2 Canonicality signals

Generic signals that a page/section is likely canonical for a query:
- high exactness in title/section/path fields,
- concentrated term coverage in one section rather than scattered coverage across many rows,
- short-to-medium structural scope instead of giant inventory-like scope,
- lead-paragraph presence matching the query,
- coherent family support from nearby chunks in the same page/section,
- stable reference-like section names inferred structurally from query shape.

### 4.3 Contamination signals

Generic signals that a page/section is noisy for a given query:
- very high entity or identifier density but low focused coverage,
- giant list/table/status/inventory shape,
- broad historical/archive scope with weak query specificity,
- many matched terms spread across unrelated subtopics,
- low family coherence for the current query,
- broad note page with many scenarios but weak exactness for the target intent.

### 4.4 Ranking behavior

The ranking layer should not "ban" such pages.
It should only make them lose unless the query explicitly asks for:
- status,
- backlog,
- archive history,
- broad notes,
- test inventory.

## 5. Interfaces and Contracts

### Internal retrieval contract
- `shared/rag_system.py`
  - add canonicality and contamination annotations per candidate,
  - aggregate those annotations at family level,
  - expose them to rerank ordering and final merged ranking.

### Diagnostics contract
- `GET /rag/diagnostics/{request_id}` should expose additive fields such as:
  - `canonicality_score`
  - `contamination_penalty`
  - `canonicality_reason`
  - `contamination_reason`

These should remain additive and backward compatible.

## 6. Data Model Changes

No DB schema change is required by default.

If diagnostics persistence needs explicit columns, prefer additive nullable fields or JSON detail payloads.

## 7. Edge Cases and Failure Modes

- A valid canonical page can also be large.
  - contamination must depend on query focus, not size alone.
- A status page may be correct when the user asks for status.
  - penalty must be query-shape-aware.
- An archive page may be canonical if the query asks for a historical version.
  - canonicality must not blindly prefer non-archive pages.

## 8. Security Requirements

- No new external dependencies by default.
- No raw page-content logging beyond current diagnostics safety policy.
- No page-name allowlists/denylists with sensitive internal naming assumptions.

## 9. Performance Requirements

- Candidate annotations must stay bounded to the existing candidate window.
- Family-level aggregation must remain linear or near-linear in candidate count.
- No second full-corpus pass is allowed in the hot path.

## 10. Observability

Must make contamination decisions debuggable:
- top penalized families,
- winning canonical family,
- whether a candidate lost mainly due to contamination or weak relevance.

## 11. Test Plan

### Unit tests
- giant status page vs canonical reference page
- archive page vs canonical current setup page
- broad notes page vs exact patch page

### Integration tests
- route/context selection for navigation and definition queries
- diagnostics exposure of canonicality and contamination fields

### Live local validation
- rerun current OpenHarmony and ArkUI cases that still fail due to noisy pages

## 12. Rollout and Rollback

### Rollout
- add additive scoring and diagnostics first,
- keep thresholds conservative,
- validate on local smoke before enabling stronger penalties.

### Rollback
- isolate the new scoring layer behind one feature flag if needed,
- additive diagnostics can remain even if ranking is rolled back.

## 13. Acceptance Criteria

- Noisy status/archive/note pages stop dominating canonical lookup queries when a stronger canonical page exists.
- The mechanism is defined without corpus-name hardcodes.
- Diagnostics can explain why a candidate was treated as contaminating.

## 14. Spec and Doc Update Plan

Implementation must update:
- `SPEC.md`
- `docs/REQUIREMENTS_TRACEABILITY.md`
- `docs/TESTING.md`
- `docs/OPERATIONS.md`

No spec update is required in this design-only cycle.

## 15. Secret-Safety Impact

- No secret-bearing URLs or private identifiers should appear in examples.
- Local validation artifacts remain local-only.

## Approval

REVIEW REQUIRED — Reply "APPROVED:v1" or "CHANGES:<bullets>"
