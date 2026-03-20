# RAG Exact Lookup and Navigation Lane v1

Date: 2026-03-19
Status: draft for approval
Task: `RAGSVC-007`

## 1. Summary

### Problem statement
The current generalized retrieval path still treats these query types too much like broad semantic retrieval:
- `where is arkui api reference`
- `what patch should i apply`
- `where can i find official documentation`
- `how to install repo tool on ubuntu`

In practice these are not broad semantic questions. They are canonical lookup and navigation requests.

### Goals
- Add a dedicated pre-LLM retrieval lane for exact lookup and navigation-style queries.
- Prefer one authoritative page or section when query shape suggests that behavior.
- Keep the lane generic, lightweight, and auditable.

### Non-goals
- No corpus-specific routing.
- No LLM-based classifier in the hot path.
- No answer-generation changes in this slice.

## 2. Scope Boundaries

### In scope
- Query understanding
- candidate generation adjustments
- fused ranking strategy for exact lookup
- route/context composition for canonical single-page answers

### Out of scope
- broad procedural retrieval changes
- answer prompt tuning
- tool-calling/agentic retrieval

## 3. Assumptions and Constraints

- The system already has:
  - field-aware structural retrieval,
  - family-aware ranking,
  - bounded query rewriting.
- Query classification must remain cheap and deterministic.

## 4. Architecture

### 4.1 New query class
Add an exact-lookup/navigation intent envelope for queries shaped like:
- `where is`
- `where can i find`
- `what patch`
- `official documentation`
- `api reference`
- `which page`
- similar reference/navigation phrasing

This is not a hard keyword decision.
It is a bounded heuristic using:
- query form,
- request for a location/reference/page,
- request for one named artifact (patch, API reference, docs page),
- low procedural breadth and high canonical target density.

### 4.2 Retrieval behavior

For this lane:
- increase the weight of structural exactness,
- favor a single authoritative family,
- reduce value of broad semantic neighbors,
- avoid over-expanding context outside the winning page/section,
- let exact field/content-anchor hits enter the rerank window aggressively.

### 4.3 Context behavior

Exact-lookup context should usually be:
- one anchor section,
- optional close neighbors from the same page/section,
- minimal or no external family expansion.

## 5. Interfaces and Contracts

### Internal contracts
- `backend/api/routes/rag.py`
  - derive a new query envelope for exact lookup/navigation,
  - select exact-lookup context policy.
- `shared/rag_system.py`
  - allow candidate-generation/fusion policy to prefer exact structural hits for this lane.

### Diagnostics contract
- additive diagnostics should surface:
  - `query_mode=exact_lookup`
  - `lookup_anchor_family`
  - `lookup_anchor_reason`

## 6. Data Model Changes

No DB schema changes are required by default.

## 7. Edge Cases and Failure Modes

- Some queries mix navigation and procedure:
  - `where is the doc that explains how to install repo tool on ubuntu`
  - exact-lookup lane should still choose the canonical page, not the broadest procedural page.
- Some queries are broad but start with `where`:
  - the lane must not overfire for open-ended exploratory queries.
- Some corpora do not have one canonical page.
  - the lane should degrade gracefully back to generalized retrieval.

## 8. Security Requirements

- No new external services.
- No prompt-inferred routing in this slice.
- No hidden document-type labels required.

## 9. Performance Requirements

- Query classification must be O(query length).
- Candidate-generation changes must stay within current bounded windows.
- No unbounded second-pass retrieval.

## 10. Observability

Diagnostics should make exact-lookup routing visible:
- when it activated,
- which family won,
- whether fallback stayed inside the same family.

## 11. Test Plan

### Unit tests
- `official documentation` query
- `api reference` query
- `what patch should i apply` query
- `repo tool on ubuntu` query

### Integration tests
- route-level context selection for exact-lookup mode
- diagnostics exposure for query mode and anchor family

### Live local validation
- rerun currently failing navigation/reference cases on OpenHarmony and ArkUI

## 12. Rollout and Rollback

### Rollout
- add routing and diagnostics first,
- keep fallback to generalized retrieval when confidence is low.

### Rollback
- exact-lookup lane should be switchable off independently.

## 13. Acceptance Criteria

- Canonical reference/setup/navigation queries prefer one authoritative page/section when available.
- The lane is generic and bounded.
- Diagnostics clearly show when exact-lookup mode was used.

## 14. Spec and Doc Update Plan

Implementation must update:
- `SPEC.md`
- `docs/REQUIREMENTS_TRACEABILITY.md`
- `docs/TESTING.md`
- `docs/OPERATIONS.md`

No spec update is required in this design-only cycle.

## 15. Secret-Safety Impact

- No private corpus paths or excerpts should be embedded in committed design/test fixtures beyond public-safe examples.

## Approval

APPROVED:v1

Implementation note (arch-review 2026-03-19):
- The degradation fallback from exact-lookup to generalized retrieval must define an explicit confidence threshold. Implementation should track `anchor_family_support_count` and/or `field_match_score`; if no candidate reaches the threshold, the lane degrades to generalized retrieval transparently and `query_mode=exact_lookup_degraded` should be surfaced in diagnostics.
- The exact-lookup lane must not fire for open-ended exploratory queries; use phrasing pattern check (location/reference/artifact keywords) as gate, not just "where" prefix alone.
