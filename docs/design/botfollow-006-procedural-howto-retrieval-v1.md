# BOTFOLLOW-006 Procedural HOWTO Retrieval Fix

## Goal

Improve broad procedural KB queries without corpus-specific hardcodes so multi-step how-to questions retrieve and present coherent step sequences from chunked technical documentation.

## Problem

The local open-harmony smoke corpus exposed a generic failure mode:

- the correct procedural document was present in the KB,
- metadata-field retrieval could already surface it,
- but generalized no-rerank search re-sorted fused candidates back by dense distance,
- and provider fallback answers surfaced isolated troubleshooting or introductory chunks instead of contiguous step-by-step guidance.

This affected compound queries like `how to build and sync`, but the pattern is generic for any chunked procedural documentation.

## Implemented approach

### 1. Preserve hybrid fusion order in generalized no-rerank retrieval

`shared/rag_system.py`

- keep RRF fusion order for merged candidates in the generalized path when reranker is disabled;
- do not sort merged candidates back by raw dense distance, because that discards BM25/field rescue signals.

### 2. Expand provider fallback over a broader procedural evidence pack

`backend/api/routes/rag.py`

- provider fallback no longer relies only on the tightly bounded LLM context rows;
- fallback builds a broader evidence pack from ranked results and expands neighboring chunks from the same section/document;
- fallback source attribution is rebuilt from the same fallback rows.

### 3. Prefer procedural guidance over troubleshooting fragments in HOWTO route ranking

`backend/api/routes/rag.py`

- add small generic boosts for procedural titles/sections (`how to`, `guide`, `initialize`, `setup`, `sync`, `build`, `run`);
- add small generic penalties for troubleshooting sections (`issue`, `error`, `fix`, `patch`, `workaround`, `regeneration`, `failed`);
- keep all signals corpus-agnostic.

## Explicit non-goals

- no hardcoded source paths, wiki URLs, document names, or `open-harmony`-specific boosts;
- no special-case answer templates tied to one corpus;
- no change to normal successful LLM answer path beyond better retrieval/context inputs.

## Verification

- focused regressions in `tests/test_rag_metadata_field_search.py`
- focused regressions in `tests/test_rag_context_composer.py`
- local-only open-harmony smoke in `tests/test_openharmony_wiki_local_smoke.py`

## Expected outcome

Compound procedural queries should:

- keep canonical procedural documents in top retrieval slots,
- include neighboring step chunks from the same section,
- and degrade to a useful extractive fallback if the provider is unavailable.
