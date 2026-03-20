# RAG Source Document Quality Guidelines v1

Date: 2026-03-19
Status: draft for approval
Task: `RAGSVC-009`

## 1. Summary

### Problem statement
Some retrieval misses are not failures of ranking alone. They are caused by source documents that mix multiple roles or hide the key answer in noisy pages:
- status + reference in one page,
- archive + current guidance in one page,
- vague headings,
- no lead summary,
- patch instructions buried in a broad note page.

### Goals
- Define a source-document quality contract for retrieval-friendly corpora.
- Help operators and authors improve bad data instead of forcing ingestion to overfit it.
- Keep guidance generic across wiki, markdown, docs, and knowledge pages.

### Non-goals
- No mandatory content rewrite in this cycle.
- No automatic document rewriting system.
- No source-specific style guide for one corpus only.

## 2. Scope Boundaries

### In scope
- authoring guidance for retrieval-friendly docs,
- anti-patterns that harm retrieval,
- recommendations for reporting bad source quality during validation.

### Out of scope
- parser implementation,
- ranking implementation,
- editorial workflow tooling.

## 3. Assumptions and Constraints

- The RAG system can preserve structure but cannot invent clean source semantics when authors do not provide them.
- Universal retrieval quality depends partly on source quality.

## 4. Architecture

This document is a source-quality contract for Stage 2 and Stage 3 inputs.

If authors provide good source structure, downstream retrieval improves with less tuning.

### Recommended document properties

1. Stable page title
- The page title should clearly describe the one main topic.

2. Clear first-paragraph summary
- The first paragraph should say what the page is for and who it is for.

3. One dominant role per page
- Prefer separate pages for:
  - overview/reference
  - setup/howto
  - troubleshooting
  - status/inventory
  - changelog/archive

4. Descriptive headings
- Avoid headings like:
  - `General`
  - `Notes`
  - `Setup`
without scope context.

5. Canonical patch/setup pages
- If a page contains a named patch, file, or exact setup prerequisite, that artifact should appear in:
  - page title or section title,
  - first paragraph,
  - stable heading.

6. Avoid giant mixed-role pages
- Large archive/status pages should not be the only place where canonical reference links live.

## 5. Interfaces and Contracts

### Validation/ops contract
Local smoke and corpus review should be able to report:
- likely mixed-role page,
- vague-heading page,
- no-summary page,
- giant noisy status/archive page,
- exact-artifact buried in broad note page.

This reporting can stay manual or semi-structured at first.

## 6. Data Model Changes

No runtime data-model change is required by default.

## 7. Edge Cases and Failure Modes

- Some pages must genuinely combine roles.
  - in that case headings and lead summary become more important.
- Historical/archive pages are still useful.
  - they should just not be the only canonical entry point.

## 8. Security Requirements

- No sensitive data should be introduced into source examples.
- Guidance should not encourage copying secrets, credentials, or internal-only URLs into docs.

## 9. Performance Requirements

- Better document structure should reduce:
  - noisy chunk count,
  - retrieval ambiguity,
  - context pollution.

## 10. Observability

The architecture should eventually surface source-quality flags during local validation, but design-only acceptance does not require implementing that now.

## 11. Test Plan

No code tests in this design-only cycle.

Future verification can include:
- corpus review checklist,
- local smoke notes linking misses to document-quality issues,
- optional authoring lint rules later.

## 12. Rollout and Rollback

### Rollout
- use this guideline in future corpus reviews and local validation reports.

### Rollback
- none required; this is additive guidance.

## 13. Acceptance Criteria

- The repo has an explicit, reusable document-quality guideline for retrieval-friendly sources.
- Future architecture work can reference source-quality problems without overfitting ingestion logic.

## 14. Spec and Doc Update Plan

If later adopted into user-facing documentation, implementation may update:
- `docs/USAGE.md`
- `docs/OPERATIONS.md`
- `docs/TESTING.md`

No spec update is required in this design-only cycle.

## 15. Secret-Safety Impact

- Guidance explicitly discourages embedding credentials or secret-bearing URLs in source docs.

## Approval

APPROVED:v1
