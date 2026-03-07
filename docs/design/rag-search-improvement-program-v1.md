# Design: RAG Search Improvement Program v1

Date: 2026-03-07
Owner: codex (architect, self-authored fallback after interrupted subagent run)
Type: Research + implementation plan

Current detailed execution backlog:
- `docs/design/rag-near-ideal-task-breakdown-v1.md`

## 1) Summary

### Problem statement
The project already has a broad ingestion surface and a stronger retrieval stack than before, but search quality over uploaded knowledge is still limited by three practical issues:
1. retrieval calibration is incomplete and still partly heuristic inside `shared/rag_system.py`,
2. prompt/safety layers still distort some grounded answers,
3. end-to-end quality protection is not yet finished for the remaining RAG steps.

The example wiki URL flow is no longer broken on the main admin path, but there is still a legacy callback branch that is not aligned with the current user journey.

### Goals
1. Improve search quality for uploaded knowledge without starting a new stack rewrite.
2. Finish the open quality backlog in a way that is measurable and regression-safe.
3. Keep the current working ingestion matrix and Telegram UX stable.
4. Close the remaining wiki URL flow gap around legacy git/zip callbacks.

### Non-goals
1. Another full retrieval backend migration.
2. Telegram UI redesign.
3. Schema-heavy RAG redesign unless diagnostics show it is unavoidable.

## 2) Scope boundaries

### In scope
1. Retrieval calibration in `shared/rag_system.py` and `backend/api/routes/rag.py`.
2. Prompt and answer-format alignment in `shared/utils.py`.
3. Safety/postprocess precision in `shared/rag_safety.py`.
4. Eval/CI gate completion and diagnostics-based test coverage.
5. Wiki URL flow consolidation in bot callbacks/handlers.

### Out of scope
1. Replacing Qdrant with another backend.
2. Reworking ingestion source adapters from scratch.
3. Large UI changes unrelated to wiki/RAG quality.

## 3) Assumptions and constraints
1. Existing Qdrant + diagnostics + eval foundations are kept and extended.
2. `RAG_LEGACY_QUERY_HEURISTICS=false` remains the default runtime baseline.
3. Improvements must be delivered in small, reviewable slices that map to existing `RAGQLTY-*` tasks.
4. No new dependencies are planned in the first wave.
5. The user goal is better search quality on already uploaded knowledge, not a new ingestion product surface.

## 4) AS-IS conclusions

### 4.1 What is already in good shape
1. Ingestion coverage is broad across doc/web/wiki/code/image/chat sources.
2. Metadata normalization and regression coverage for ingestion were already improved in P1.
3. Diagnostics, eval runner, and threshold-based quality gate already exist.
4. The main wiki-by-URL admin flow is repaired and Gitee URLs now prefer git-based loading.

### 4.2 What still limits quality
1. Hidden retrieval heuristics remain in the retrieval core:
   - `compute_source_boost(...)`,
   - `_is_howto_query(...)`,
   - how-to candidate expansion/sort,
   - `_simple_search(...)` strong-token filtering.
2. Diagnostics are persisted, but pending tests do not yet assert candidate/fusion/rerank quality.
3. Prompt contract is inconsistent across RU and EN branches.
4. Safety filters are too strict for valid commands/URLs that are grounded in context.
5. Eval corpus is deterministic but still narrow relative to arbitrary uploaded knowledge.
6. `wiki_git_load` / `wiki_zip_load` callbacks look like orphan legacy paths because the current UI does not populate `wiki_urls`.

## 5) Implementation strategy

### Phase A: Retrieval calibration and visibility
Maps to: `RAGQLTY-009`, `RAGQLTY-010`

1. Stabilize hybrid retrieval boundaries:
   - audit dense candidate window,
   - audit BM25 candidate window,
   - make rerank top-N explicit,
   - verify how `source_boost` changes ordering.
2. Reduce implicit heuristics in the default path:
   - keep generalized ranking as the default,
   - either neutralize or explicitly measure `source_boost` and how-to-only behavior.
3. Extend diagnostics assertions:
   - selected candidates,
   - origin/channel mix,
   - fusion/rerank ordering,
   - degraded-mode reasons.

Files to change:
1. `shared/rag_system.py`
2. `backend/api/routes/rag.py`
3. `backend/schemas/rag.py` if diagnostics response must expose additional calibrated fields
4. `tests/test_rag_query_definition_intent.py`
5. `tests/test_rag_diagnostics.py`
6. `tests/test_rag_quality.py`

Concrete code changes:
1. In `shared/rag_system.py`:
   - replace implicit `candidate_k` branching with explicit channel budgets,
   - make rerank input window explicit and capped,
   - isolate `compute_source_boost(...)` behind a config flag or remove it from the default generalized path,
   - reduce `_is_howto_query(...)` from ranking driver to optional context-selection hint only,
   - keep `_simple_search(...)` only as degraded fallback, not as hidden quality path.
2. In `backend/api/routes/rag.py`:
   - stop compensating for retrieval-core instability with route-side logic,
   - ensure diagnostics persist enough evidence to explain fused ordering and final selection,
   - keep `RAG_LEGACY_QUERY_HEURISTICS` only as rollback switch.
3. In tests:
   - add candidate-order assertions,
   - assert dense/bm25/fusion/rerank trace consistency,
   - assert no silent reintroduction of query-specific boosts into default mode.

Exit criteria:
1. Default path ranking is explainable from diagnostics.
2. Retrieval ordering is stable across repeated runs on the fixed corpus.
3. Legacy heuristics remain rollback-only, not quality-critical for default behavior.

### Phase B: Prompt and answer-format alignment
Maps to: `RAGQLTY-011`, `RAGQLTY-012`

1. Use one grounded direct-answer contract for RU and EN.
2. Remove remaining forced headings (`Main Answer`, `Additionally Found`) from prompt branches that still require them.
3. Add regression tests for:
   - direct grounded answers,
   - empty-evidence refusal,
   - consistent RU/EN formatting.

Files to change:
1. `shared/utils.py`
2. `frontend/bot_handlers.py` only if Telegram formatting assumptions need minor alignment
3. `tests/test_rag_summary_modes.py`
4. add `tests/test_rag_prompt_format.py`

Concrete code changes:
1. Unify RU/EN `task="answer"` prompt shape in `create_prompt_with_language(...)`.
2. Remove the remaining English template contract requiring `Main Answer` / `Additionally Found`.
3. Keep citations optional and grounded, but make the first answer block concise and direct in both languages.
4. Ensure `format_for_telegram_answer(...)` remains a formatter, not a semantic repair layer for prompt mistakes.

Exit criteria:
1. RU and EN answers follow the same grounded direct-answer policy.
2. Output format no longer depends on language-specific template headings.
3. Empty evidence still returns refusal behavior deterministically.

### Phase C: Safety/postprocess precision
Maps to: `RAGQLTY-013`, `RAGQLTY-014`, `RAGQLTY-015`

1. Move command sanitizer from line-level destructive matching toward token-level validation.
2. Preserve valid context-backed URLs, including wiki/document URLs.
3. Keep negative filtering for genuinely untrusted URLs and invented commands.
4. Add positive/negative tests that include Gitee wiki links and command snippets.

Files to change:
1. `shared/rag_safety.py`
2. `backend/api/routes/rag.py` only if postprocess call order changes
3. `tests/test_rag_safety.py`
4. add `tests/test_rag_url_preservation.py`

Concrete code changes:
1. Replace exact full-line command matching with token-level grounding:
   - verify command stems/options separately,
   - preserve partially matching grounded commands when they are clearly supported by context.
2. Replace blanket `contains_wiki_url(...)` stripping with trust decisions based on context/source grounding.
3. Extend URL preservation beyond literal context string matching:
   - allow URLs present in metadata-backed source blocks or citations,
   - still strip links not derivable from the retrieved evidence set.
4. Add explicit positive tests for:
   - Gitee wiki URLs,
   - document links coming from context,
   - preserved commands with small formatting variation.

Exit criteria:
1. Grounded wiki/document links survive.
2. Invented commands/URLs are still removed.
3. How-to answer quality improves without weakening safety guarantees.

### Phase D: End-to-end quality gate completion
Maps to: `RAGQLTY-016`, `RAGQLTY-017`, `RAGQLTY-018`

1. Extend the fixed corpus beyond the current narrow source family.
2. Add end-to-end regression runs against representative uploaded-knowledge snapshots.
3. Make CI fail fast on source-hit / grounding regressions.
4. Document how to run the suite locally and how to interpret failures.

Files to change:
1. `tests/data/rag_eval_ready_data_v1.yaml` or new versioned corpus file
2. `backend/services/rag_eval_service.py`
3. `scripts/rag_eval_baseline_runner.py`
4. `scripts/rag_eval_quality_gate.py`
5. `.github/workflows/agent-quality-gates.yml`
6. `docs/TESTING.md`
7. `docs/OPERATIONS.md`

Concrete code changes:
1. Add a broader fixed corpus that includes:
   - wiki pages,
   - code-heavy chunks,
   - long-structure markdown,
   - mixed RU/EN factual queries,
   - legal/numeric/how-to cases over uploaded knowledge.
2. Add slice-aware reporting so failures show which source family regressed.
3. Wire quality gate thresholds to fail on retrieval regressions before answer-format regressions hide them.
4. Document the local command sequence that developers must run before merge.

Exit criteria:
1. The project has one deterministic end-to-end quality gate.
2. CI fails on meaningful quality regressions.
3. The suite covers more than one “happy path” corpus family.

### Phase E: Wiki flow consolidation
Proposed follow-up task: `WIKIFLOW-001`

1. Keep the repaired path:
   - `kb_wiki_crawl -> waiting_wiki_root -> ingest_wiki_crawl`.
2. Decide one of two directions for legacy callbacks:
   - reintroduce a producer flow for `wiki_git_load` / `wiki_zip_load`, or
   - remove/retire those orphan branches.
3. Add regression tests for:
   - root wiki URL input,
   - nested `/wikis/...` normalization,
   - Gitee git fallback,
   - git-loader failure fallback to HTML crawl,
   - legacy callback availability only if the UI can still reach it.

Files to change:
1. `frontend/bot_callbacks.py`
2. `frontend/bot_handlers.py`
3. `shared/wiki_scraper.py`
4. `shared/wiki_git_loader.py` only if explicit mode reporting is needed
5. `tests/test_bot_text_ai_mode.py`
6. `tests/test_wiki_scraper.py`

Concrete code changes:
1. Make one canonical wiki ingestion UX.
2. Either:
   - add an explicit “choose import mode” producer that populates `wiki_urls`, or
   - delete/retire `wiki_git_load` / `wiki_zip_load` dead paths.
3. Expose degraded-mode messages clearly when Gitee git fallback fails and HTML crawl is used instead.

Exit criteria:
1. No unreachable wiki callback branches remain.
2. The example Gitee wiki URL continues to work.
3. Mode-specific behavior is testable and visible.

### Phase F: Ingestion excellence for near-ideal quality
Proposed follow-up tasks: `RAGIDEAL-001`, `RAGIDEAL-002`, `RAGIDEAL-003`

1. Introduce a canonical document/chunk contract in runtime, not only in architecture docs.
2. Improve parser fidelity for PDF/DOCX/web structural extraction.
3. Persist richer structural metadata for better retrieval and context assembly.

Files to change:
1. `shared/document_loaders/pdf_loader.py`
2. `shared/document_loaders/word_loader.py`
3. `shared/document_loaders/web_loader.py`
4. `shared/document_loaders/chunking.py`
5. `backend/services/ingestion_service.py`
6. `shared/database.py`
7. `shared/rag_system.py`
8. tests:
   - `tests/test_ingestion_metadata_contract.py`
   - `tests/test_markdown_loader_metadata_contract.py`
   - new parser-fidelity tests for PDF/DOCX/web

Concrete code changes:
1. Add stable fields where feasible:
   - `chunk_no`,
   - normalized `section_path`,
   - `page_no`,
   - `char_start` / `char_end`,
   - `parser_profile`,
   - `parser_confidence`,
   - `parser_warning`.
2. Upgrade chunking from “best effort by source type” toward structure-aware deterministic segmentation.
3. Preserve table/list/code boundaries more explicitly for downstream retrieval.
4. Add migrations only if runtime consumers actually need these fields persisted in SQL.

Exit criteria:
1. Retrieval can reason over document structure better than plain text slices.
2. Parser quality is measurable by tests, not assumed.
3. Ingestion becomes a quality multiplier, not just a compatibility layer.

### Phase G: Context composer and evidence-pack upgrade
Proposed follow-up tasks: `RAGIDEAL-004`, `RAGIDEAL-005`

1. Move from “top chunks joined together” to evidence-pack assembly.
2. Use structural neighbors and section scope deterministically.

Files to change:
1. `backend/api/routes/rag.py`
2. `shared/rag_system.py`
3. `backend/schemas/rag.py` if richer diagnostics/context reporting is needed
4. tests:
   - `tests/test_rag_query_definition_intent.py`
   - new context-assembly regression tests

Concrete code changes:
1. Promote context assembly to a dedicated policy layer:
   - top evidence chunk,
   - adjacent structural siblings,
   - section-level expansion within a token budget.
2. Stop using query intent as the main context strategy selector.
3. Add diagnostics showing why a chunk entered the final context.

Exit criteria:
1. Context is assembled deterministically and explainably.
2. Numeric/legal/how-to answers get supporting evidence without prompt bloat.

### Phase H: Multi-corpus near-ideal evaluation
Proposed follow-up tasks: `RAGIDEAL-006`, `RAGIDEAL-007`

1. Expand the evaluation program beyond one corpus family.
2. Measure quality separately for ingestion families, not only query types.

Files to change:
1. `tests/data/` new versioned corpora
2. `backend/services/rag_eval_service.py`
3. `scripts/rag_eval_baseline_runner.py`
4. `scripts/rag_eval_quality_gate.py`
5. `docs/TESTING.md`

Concrete code changes:
1. Add corpora for:
   - PDF-heavy docs,
   - wiki-heavy docs,
   - code-heavy docs,
   - mixed multilingual docs.
2. Add per-slice thresholds and reporting by source family.
3. Track not only retrieval hit metrics but also “postprocess-damaged answer” failures.

Exit criteria:
1. “Near-ideal” quality is measured across source families, not guessed.
2. Ingestion defects become visible through eval slices immediately.

## 6) Interfaces and contracts

### Public API
1. Keep `POST /api/v1/rag/query` unchanged.
2. Keep `GET /api/v1/rag/diagnostics/{request_id}` as the main debugging surface.
3. Keep current wiki ingestion endpoints unchanged unless legacy wiki modes are intentionally retired later.

### Internal contracts
1. Retrieval diagnostics must stay sufficient to explain:
   - candidate origin,
   - fusion position,
   - rerank deltas,
   - degraded mode.
2. Prompt contract must stay "grounded only" for both RU and EN.
3. Safety contract must block invented commands/URLs without deleting grounded ones.

## 7) Data model changes
1. No mandatory schema changes are planned for the first implementation wave.
2. Optional follow-up only if diagnostics are insufficient:
   - add explicit postprocess-action markers,
   - add richer eval slice metadata.
3. Near-ideal follow-up likely requires additive schema changes for richer chunk structure:
   - `chunk_no`,
   - `page_no`,
   - `char_start`,
   - `char_end`,
   - `parser_profile`,
   - `parser_confidence`,
   - `parser_warning`,
   - optional neighbor/parent chunk links.

## 8) Edge cases and failure modes
1. Retrieval looks "generalized" at the route layer but still drifts because of retrieval-core boosts.
2. Good candidates are found, but prompt/safety strips useful commands or URLs.
3. Wiki git fallback fails because `git` is unavailable or clone access fails.
4. HTML fallback on Gitee remains incomplete by design and should be treated as degraded behavior.

## 9) Security requirements
1. Do not log secrets, tokens, or credential-bearing URLs.
2. Keep command sanitization, but narrow it to grounded destructive filtering.
3. Keep URL trust checks, but preserve URLs that are explicitly grounded in source/context.
4. Do not add dependencies without explicit approval.

## 10) Performance requirements
1. Retrieval calibration changes must not expand candidate windows without measurement.
2. Diagnostics tests should run on focused fixtures, not on heavyweight corpora by default.
3. End-to-end eval can be slower, but it must be CI-usable and deterministic.

## 11) Observability
1. Continue using `retrieval_query_logs` and `retrieval_candidate_logs` as the source of truth for search debugging.
2. Add assertions that fail when diagnostics become incomplete or misleading.
3. For wiki ingestion, surface whether git fallback or HTML crawl path was used when useful for troubleshooting.

## 12) Test plan
1. Retrieval:
   - focused `pytest` for fusion/rerank/order regressions,
   - diagnostics assertions tests.
2. Prompt/safety:
   - format regression tests,
   - positive/negative URL and command tests.
3. End-to-end:
   - eval baseline runner,
   - threshold gate compare.
4. Wiki:
   - bot state flow test,
   - scraper fallback tests,
   - legacy callback tests only if the path remains supported.

Planned commands per step:
1. `python -m py_compile <changed_py_files>`
2. `.venv\Scripts\python.exe -m pytest -q <targeted_tests>`
3. `python scripts/scan_secrets.py`
4. `python scripts/ci_policy_gate.py --working-tree`
5. quality-step only:
   - `.venv\Scripts\python.exe scripts/rag_eval_baseline_runner.py ...`
   - `.venv\Scripts\python.exe scripts/rag_eval_quality_gate.py ...`

## 13) Rollout and rollback
1. Follow the existing atomic step model from `RAGQLTY-009..018`.
2. Prefer feature flags and small ranking changes over large rewrites.
3. If a step regresses quality, rollback only that slice or re-enable the previous flag behavior.
4. For wiki flow cleanup, keep the current working `waiting_wiki_root` path untouched until replacement coverage exists.

## 14) Acceptance criteria
1. Remaining `RAGQLTY-009..018` steps are completed with tests and review artifacts.
2. Default search quality improves without reintroducing domain-specific route hacks.
3. RU and EN grounded answers follow the same direct-answer policy.
4. Grounded commands/URLs survive post-processing; invented ones do not.
5. End-to-end eval and CI gates protect search quality regressions.
6. The example Gitee wiki URL path remains working.
7. Legacy wiki callback behavior is either consolidated into a reachable UX flow or explicitly retired.
8. Near-ideal follow-up is decomposed into explicit source-quality phases for ingestion, context assembly, and multi-corpus evaluation.

## 15) Spec/doc update plan
When implementation begins, update:
1. `SPEC.md`
2. `docs/REQUIREMENTS_TRACEABILITY.md`
3. `docs/USAGE.md`
4. `docs/OPERATIONS.md`
5. `docs/TESTING.md`
6. `docs/API_REFERENCE.md` if endpoint/runtime contracts change
7. step-specific design docs and review artifacts

## 16) Secret-safety impact
1. No secrets are introduced by this plan.
2. Wiki diagnostics and errors must avoid echoing credentials from private URLs.
3. Eval artifacts must not include secret-bearing source paths.

## Approval
REVIEW REQUIRED — Reply "APPROVED:v1" or "CHANGES:<bullets>"
