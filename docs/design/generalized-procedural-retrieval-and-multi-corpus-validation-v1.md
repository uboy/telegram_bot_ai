# Generalized Procedural Retrieval and Multi-Corpus Validation v1

Date: 2026-03-18
Status: draft for approval
Task: `RAGMULTI-001`

## 1. Summary

### Problem
The current broad-HOWTO hardening improved one real failure shape, but it still relies too much on surface wording. That is not robust enough when:
- the corpus language changes,
- titles use different verbs,
- section naming varies,
- the right answer lives in a procedural family that does not explicitly say `how to build and sync`.

### Goals
- Make procedural retrieval more universal by preferring one coherent evidence family over word-matched top chunks.
- Validate this behavior across multiple corpora, not only one OpenHarmony wiki.
- Reuse the repo's existing local smoke, provider, and API-eval infrastructure instead of creating a parallel testing system.

### Non-goals
- No network-fetching of external corpora during normal committed tests.
- No new external dependencies by default.
- No language-specific rule explosion or per-corpus hardcodes.

## 2. Scope Boundaries

### In scope
- Retrieval and context-assembly behavior for procedural questions.
- Family-level ranking and fallback behavior.
- Local-only source manifest extensions for additional corpora.
- Smoke/eval tooling updates for local corpora and optional live backend checks.

### Out of scope
- Full retraining of embeddings/rerank models.
- Automatic corpus download in CI.
- Changes to ingestion semantics unrelated to procedural retrieval or smoke tooling.

## 3. Assumptions and Constraints

- Project policy requires design-first for this non-trivial scope.
- Fast tests must remain deterministic and network-free.
- Local corpora must be referenced through env vars or local paths, not committed into the repo.
- Existing provider infrastructure (`ollama`, `open_webui`) should be reused for optional answer-lane checks.
- Any live-backend smoke must be opt-in and bounded to explicit target settings.

## 4. Architecture

### 4.1 Retrieval concept shift
The target unit is not "best matching chunk" but "best procedural evidence family".

An evidence family is a set of chunks that share a strong structural relationship:
- same document,
- same section path or nearby section neighborhood,
- sequential step continuity,
- low topical drift.

### 4.2 Family scoring
Candidate generation continues to produce rows, but ranking adds a family layer:
- row relevance remains a signal,
- family cohesion becomes a first-class signal,
- final context selection starts from the winning family, then expands within it.

Primary signals should be mostly language-agnostic:
- same `source_path` / `doc_title` / `section_path` clustering,
- neighbor continuity and ordered chunk adjacency,
- command/block/list density,
- numbered-step continuity,
- repeated action-result structure,
- low contamination from unrelated troubleshooting/version-specific families.

Weak hint signals may still exist:
- broad procedural intent classification,
- generic command markers,
- presence of action-oriented tokens.

But they must not dominate the decision.

### 4.3 Context packing and fallback
The current risk is that answer assembly or fallback mixes rows from several narrow pages. The new rule is:
- choose one family first,
- pack context from that family,
- only expand outside the family when there is a measurable coverage gap and no strong contamination signal,
- use the same family boundary for extractive/provider fallback.

## 5. Interfaces and Contracts

### Internal retrieval contracts
- `shared/rag_system.py`
  - continue returning candidate rows, but expose enough metadata for family aggregation and adjacency-aware packing.
- `backend/api/routes/rag.py`
  - procedural ranking path should consume family aggregates rather than only row-level boosts.
  - fallback row selection must accept a chosen family id / family envelope.

### Smoke/eval contracts
- `tests/data/rag_eval_source_manifest_v1.yaml`
  - extend with local-only entries for:
    - OpenHarmony wiki corpus
    - ArkUI wiki corpus
- `scripts/openharmony_wiki_local_smoke.py`
  - evolve into a corpus-agnostic local wiki smoke helper or a thin wrapper around a new generalized helper.
- `scripts/rag_api_smoke_test.py`
  - extend for optional procedural-family live smoke against a running backend.

### Environment contract
Proposed local-only env vars:
- `RAG_EVAL_LOCAL_OPENHARMONY_WIKI_PATH`
- `RAG_EVAL_LOCAL_ARKUIWIKI_PATH`
- `RAG_LOCAL_WIKI_SMOKE_CORPUS`
- `RAG_LOCAL_WIKI_SMOKE_PATH`
- `RAG_LIVE_SMOKE_BASE_URL`
- `RAG_LIVE_SMOKE_API_KEY`
- `RAG_LIVE_SMOKE_KB_ID`

These are developer-local only and must not be required for fast tests or CI.

## 6. Data Model Changes

No database schema changes are required by default.

Optional diagnostic extension:
- include chosen family metadata in retrieval diagnostics for easier post-mortem analysis.

This is implementation-dependent and should remain additive.

## 7. Edge Cases and Failure Modes

- A corpus may express procedures without numbered lists.
  - family scoring must not require explicit numbering.
- A corpus may use another language or unconventional section names.
  - structural continuity must outweigh title-token matching.
- A corpus may split one procedure across sibling sections.
  - controlled neighbor expansion is allowed within a bounded family envelope.
- A troubleshooting page may contain many commands.
  - command density alone cannot define a procedural family; contamination penalties are required.
- Optional live smoke may point at an unreachable backend or a backend with stale KB contents.
  - tests/scripts must report this as environment failure, not as deterministic product regression.

## 8. Security Requirements

- No secrets or credential-bearing URLs may be written into committed fixtures or reports.
- Live smoke scripts must redact credentials in logged base URLs.
- Local corpus paths remain env-driven and uncommitted.
- Optional answer-lane tests must reuse existing provider controls; no ad-hoc remote calls should bypass project policy.

## 9. Performance Requirements

- Family aggregation must stay bounded to the existing top-N candidate window.
- Fast unit/integration tests must remain lightweight.
- Slow local smoke remains opt-in.
- Live smoke must be optional and should fail fast on connectivity/config errors.

## 10. Observability

Add diagnostics that make family-level decisions visible:
- chosen family identifier or family summary,
- family cohesion score,
- contamination penalty,
- whether fallback stayed inside the chosen family.

This is important because procedural regressions are otherwise hard to explain from row-level logs.

## 11. Test Plan

### Fast deterministic lane
- unit tests for family aggregation and scoring on synthetic fixtures:
  - mixed-language procedural families,
  - alternate wording without `build/sync`,
  - troubleshooting contamination cases.
- route-level tests for context/fallback family boundary behavior.
- manifest/schema tests for new local-only corpus entries.

### Local slow lane
- env-driven temp-SQLite smoke against:
  - local OpenHarmony wiki corpus
  - local ArkUI wiki corpus
- both should support:
  - deterministic extractive fallback mode,
  - optional real answer lane through current provider config.

### Optional live runtime lane
- API smoke against a running backend using:
  - explicit base URL
  - explicit KB id
  - current provider/runtime configuration
- intended for fast iteration during tuning, not for default CI.

## 12. Rollout and Rollback

### Rollout
- first land deterministic family-selection logic and tests,
- then extend local multi-corpus smoke,
- then add optional live smoke/reporting.

### Rollback
- revert family-level procedural ranking changes if regressions appear,
- keep smoke/eval tooling additive and removable independently,
- unset local/live smoke env vars to disable external-corpus validation lanes.

## 13. Acceptance Criteria

- Broad procedural queries no longer depend on literal corpus-specific boosts such as `Sync&Build`.
- Procedural context assembly prefers one coherent evidence family by structure, not by one page title string.
- Synthetic regressions prove the logic still works when wording/language/section labels differ.
- Local multi-corpus smoke can run against env-prepared OpenHarmony and ArkUI wiki corpora using temp local DBs.
- Optional answer-lane smoke can reuse the current `ollama` or `open_webui` configuration.
- Optional live-backend smoke can validate the currently running backend without being part of default CI.

## 14. Spec and Doc Update Plan

Implementation must update:
- `SPEC.md`
- `docs/REQUIREMENTS_TRACEABILITY.md`
- `docs/TESTING.md`
- `docs/CONFIGURATION.md` if new local/live smoke env vars are introduced
- `docs/OPERATIONS.md` if live smoke becomes part of runtime triage guidance

## 15. Secret-Safety Impact

Potential leak surfaces:
- local corpus paths,
- live backend URLs with credentials,
- provider base URLs with embedded credentials.

Controls:
- keep corpus paths local-only,
- redact credentials in any emitted artifact,
- do not commit local smoke output that contains private source excerpts.

## Approval

REVIEW REQUIRED — Reply "APPROVED:v1" or "CHANGES:<bullets>"
