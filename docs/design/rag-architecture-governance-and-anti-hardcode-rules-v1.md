# RAG Architecture Governance and Anti-Hardcode Rules v1

Date: 2026-03-19
Status: draft for approval
Task: `RAGSVC-013`

## 1. Summary

### Problem statement
This repository has already gone through multiple RAG redesign and hardening cycles. The repeated failure mode was not "no architecture". The repeated failure mode was:
- local query fixes presented as architecture,
- corpus-specific boosts hidden inside generalized retrieval,
- green synthetic tests without matching live-corpus improvement,
- retrieval work compensating for poor source documents instead of surfacing their quality problems.

If these patterns are not explicitly forbidden, the system will keep drifting back toward brittle heuristics and future work will again require architectural rewrites.

### Goals
- Define governance rules that keep future RAG work universal.
- Explicitly ban corpus-specific and query-specific retrieval hacks as steady-state architecture.
- Make diagnostics, multicorpus validation, and stage contracts mandatory for RAG changes.

### Non-goals
- No runtime code changes in this cycle.
- No attempt to fully freeze experimentation.
- No removal of rollback paths needed for safety.

## 2. Scope Boundaries

### In scope
- architectural invariants for future RAG changes,
- anti-hardcode rules,
- acceptance gate for retrieval changes,
- review checklist,
- separation of source-quality problems from retrieval problems.

### Out of scope
- detailed implementation of any one retrieval slice,
- bot UX governance outside RAG,
- general repo governance unrelated to RAG.

## 3. Assumptions and Constraints

- The system must remain universal across corpora, languages, and document shapes.
- Local real-corpus validation is required for meaningful retrieval work.
- Existing master architecture doc remains:
  - `docs/design/rag-service-architecture-and-pipeline-v1.md`
- This governance document sits above feature-level design docs and constrains how they may be implemented.

## 4. Architectural Invariants

These invariants are mandatory for future RAG work.

### 4.1 Stage-bound changes only
Every retrieval-affecting change must be attached to one or more explicit pipeline stages:
- parse and normalization,
- chunking and structural graph,
- candidate generation,
- fusion/rerank/family aggregation,
- evidence-pack context composition,
- diagnostics/eval.

A change that cannot be explained as a stage-level mechanism change is presumed to be a symptom-level patch.

### 4.2 Generic mechanisms only
Steady-state architecture may use only generic signals such as:
- structure,
- field exactness,
- family support,
- contamination indicators,
- canonicality,
- bounded retrieval budgets,
- deterministic context policies,
- source-quality flags,
- query-shape envelopes.

### 4.3 Retrieval changes must be diagnosable
If a retrieval change affects ranking, routing, or context selection, diagnostics must be able to explain:
- why a candidate/family won,
- why a candidate/family lost,
- whether exact lookup or broad procedural mode was active,
- why a context row was included or excluded.

### 4.4 Source quality must not be silently absorbed by ranking hacks
When poor source structure is the real problem, the system should:
- report it,
- classify it,
- document it,
rather than permanently bias retrieval toward one bad corpus layout.

## 5. Forbidden Patterns

The following patterns are forbidden as default production architecture.

### 5.1 Corpus-specific page-name logic
Examples:
- direct boosts for `Sync&Build`,
- direct penalties for `DEV_API_STATUS`,
- hardcoded page-title preferences for one wiki.

Allowed only as:
- temporary rollback path,
- compatibility shim with explicit removal plan,
- debug-only experiment not enabled by default.

### 5.2 Query-specific string hacks
Examples:
- explicit routing for one exact query phrase,
- keyword branches added only to fix one failing smoke case,
- literal title/path boosts tied to one test query.

### 5.3 Test-data coupling without generalized evidence
A change is invalid if it only proves:
- one synthetic regression passes,
while failing to show:
- multicorpus live improvement,
- or a generic diagnostic rationale.

### 5.4 Hidden ranking behavior
A change is invalid if it adds:
- hidden boosts,
- undocumented penalties,
- silent rerank-window changes,
- context expansion behavior not visible in diagnostics.

### 5.5 Retrieval overfitting to bad source documents
A change is invalid if the right long-term fix is clearly:
- split mixed-role pages,
- improve headings,
- move exact artifact to canonical section/page,
but the implementation instead hardcodes retrieval around that bad source.

## 6. Required Evidence for Any Retrieval Change

Every future pre-LLM retrieval change must provide all three evidence layers.

### 6.1 Deterministic regression evidence
- synthetic or focused regression tests
- proves the mechanism at unit/integration level

### 6.2 Multicorpus live validation
- local smoke or equivalent on at least two real corpora when available
- should show either:
  - improvement in the target failure class,
  - or explicit non-regression if the slice is intentionally narrow

### 6.3 Diagnostic evidence
- diagnostics must show the general signal that improved the result
- not merely “the result changed”

If one of these is missing, the change should be treated as incomplete.

## 7. Failure-Class Governance

Quality must not be tracked only by one aggregate pass-rate.

The architecture requires failure classes such as:
- exact lookup miss
- navigation/reference miss
- setup canonical-page miss
- broad procedural miss
- contamination/status-page miss
- troubleshooting drift
- source-quality failure

Every retrieval slice must declare:
- which failure classes it targets,
- which ones it should not worsen.

## 8. Source-Quality Governance

Before adding retrieval complexity, reviewers must ask:
- is this a retrieval problem,
- or is this a source-quality problem?

Indicators of source-quality problems:
- mixed-role page,
- giant status/inventory page used as canonical source,
- vague headings,
- no summary lead paragraph,
- exact patch/setup artifact buried in a broad note page.

When this is the case, the implementation plan must include one of:
- explicit recommendation to fix source documents,
- source-quality flag/reporting,
- decision not to overfit retrieval around the bad document.

## 9. Review Gate for Future RAG Changes

Any RAG change should be rejected in review unless it answers all of these questions.

1. Which pipeline stage(s) changed?
2. What generic mechanism was added or strengthened?
3. Which failure classes should improve?
4. Which corpora were used for validation?
5. What diagnostics prove the mechanism worked?
6. Does the change introduce any page-name, corpus-name, or query-phrase hardcoding?
7. Is the real issue actually source quality rather than retrieval?

## 10. Review Checklist

Use this checklist for every future RAG review.

### Design checklist
- Is the change attached to explicit pipeline stages?
- Is the mechanism generic across corpora?
- Is the change consistent with the master architecture and sibling design docs?

### Anti-hardcode checklist
- no page-name boosts,
- no corpus-name boosts,
- no single-query routing hacks,
- no undocumented hidden ranking logic.

### Validation checklist
- focused regression exists,
- multicorpus local validation exists,
- diagnostics explanation exists.

### Source-quality checklist
- does this change incorrectly compensate for poor source docs?
- if yes, is there a documented source-quality recommendation instead?

## 11. Rollout Policy

Future retrieval changes should be merged in this order:
1. design doc approved,
2. deterministic tests,
3. diagnostics visibility,
4. multicorpus validation,
5. spec/traceability sync.

No “fix first, explain later” retrieval changes should be accepted as the default process.

## 12. Acceptance Criteria

- The repository has an explicit governance document for future RAG work.
- Forbidden patterns are clearly listed.
- Retrieval changes now have a defined evidence and review gate.
- Source-quality issues are explicitly separated from retrieval tuning.

## 13. Spec and Doc Update Plan

Implementation cycles informed by this governance document should update:
- `docs/design/*` for each feature slice,
- `SPEC.md` when behavior changes,
- `docs/REQUIREMENTS_TRACEABILITY.md`,
- `docs/TESTING.md`,
- `docs/OPERATIONS.md`.

No spec update is required in this design-only cycle.

## 14. Secret-Safety Impact

- No secret impact in this design-only document.
- Future governance enforcement should continue to forbid private corpus leakage in committed fixtures and reports.

## Approval

APPROVED:v1
