# Design: RAG Near-Ideal Task Breakdown v1

Date: 2026-03-07
Owner: codex (team lead / planner)
Status: CURRENT EXECUTION BACKLOG

Supersedes:
- `docs/design/rag-general-quality-program-v1.md`
- high-level placeholder backlog items `RAGQLTY-009..018`
- high-level placeholder backlog items `RAGIDEAL-001..005`
- wiki placeholder backlog item `WIKIFLOW-001`

Related strategy docs:
- `docs/design/rag-search-improvement-program-v1.md`
- `docs/design/rag-generalized-architecture-v2.md`
- `docs/design/rag-current-algorithm-as-is-v1.md`

## Execution Contract

1. One task = one atomic implementation slice.
2. No task can be marked completed without:
   - focused implementation,
   - mandatory checks,
   - review artifact,
   - required doc updates or explicit `N/A` rationale.
3. Every task must have an independent review report in:
   - `coordination/reviews/<task-id>-<date>.md`
4. Every functional task must update:
   - `SPEC.md`
   - `docs/REQUIREMENTS_TRACEABILITY.md`
   - relevant user/ops docs when behavior changes.
5. Rollback must stay slice-local:
   - revert only the current task diff or disable its feature flag if one exists.

## Global Mandatory Checks

Base checks for every task:
- `python -m py_compile <changed_py_files>`
- `.venv\Scripts\python.exe -m pytest -q <targeted_tests>`
- `python scripts/scan_secrets.py`
- `python scripts/ci_policy_gate.py --working-tree`

Extra checks for quality-impacting tasks:
- `.venv\Scripts\python.exe scripts/rag_eval_baseline_runner.py --out-dir data/rag_eval_baseline`
- `.venv\Scripts\python.exe scripts/rag_eval_quality_gate.py --baseline-report-json <baseline> --run-report-json <candidate>`

## Task Breakdown

### RAGEXEC-001: Retrieval budgets and rerank window
- Goal:
  - make dense/bm25 candidate budgets and rerank top-N explicit in runtime.
- Config contract:
  - `RAG_DENSE_CANDIDATES` defaults to legacy `RAG_MAX_CANDIDATES`,
  - `RAG_BM25_CANDIDATES` defaults to legacy `RAG_MAX_CANDIDATES`,
  - `RAG_RERANK_TOP_N` defaults to `max(dense, bm25)` and runtime must clamp it to at least requested `top_k`.
- Files:
  - `shared/rag_system.py`
  - `shared/config.py`
  - `env.template`
  - `tests/test_rag_system_budgets.py`
- Checks:
  - `pytest tests/test_rag_system_budgets.py tests/test_rag_query_definition_intent.py tests/test_rag_diagnostics.py`
- Review:
  - review artifact required, with diagnostics evidence.
- Docs:
  - `SPEC.md`
  - `docs/REQUIREMENTS_TRACEABILITY.md`
  - `docs/CONFIGURATION.md`

### RAGEXEC-002: Remove hidden default ranking boosts
- Goal:
  - neutralize `source_boost` and reduce `_is_howto_query` from ranking driver to optional context hint in generalized mode.
- Runtime contract:
  - default generalized mode must rank by base retrieval scores without retrieval-core `source_boost`,
  - how-to detection may remain as a hint but must not change candidate ordering outside explicit rollback mode,
  - `RAG_LEGACY_QUERY_HEURISTICS=true` is the single rollback switch for both route-level and retrieval-core legacy heuristics.
- Files:
  - `shared/rag_system.py`
  - `backend/api/routes/rag.py`
  - `tests/test_rag_system_budgets.py`
  - `tests/test_rag_diagnostics.py`
- Checks:
  - `pytest tests/test_rag_system_budgets.py tests/test_rag_query_definition_intent.py tests/test_rag_diagnostics.py tests/test_rag_quality.py`
- Review:
  - compare generalized mode before/after with diagnostics output.
- Docs:
  - `SPEC.md`
  - `docs/REQUIREMENTS_TRACEABILITY.md`
  - `docs/OPERATIONS.md`

### RAGEXEC-003: Retrieval diagnostics assertions
- Goal:
  - make candidate origin/fusion/rerank ordering testable and required.
- Files:
  - `backend/api/routes/rag.py`
  - `backend/schemas/rag.py`
  - `tests/test_rag_diagnostics_contract.py`
- Checks:
  - `pytest tests/test_rag_diagnostics.py tests/test_rag_diagnostics_contract.py tests/test_api_routes_contract.py`
- Review:
  - reviewer must confirm diagnostics are sufficient for incident triage.
- Docs:
  - `SPEC.md`
  - `docs/API_REFERENCE.md`
  - `docs/REQUIREMENTS_TRACEABILITY.md`

### RAGEXEC-004: RU/EN grounded prompt unification
- Goal:
  - use one direct grounded-answer contract for both languages.
- Files:
  - `shared/utils.py`
  - `tests/test_rag_prompt_contract.py`
- Checks:
  - `pytest tests/test_rag_prompt_contract.py tests/test_rag_summary_modes.py`
- Review:
  - reviewer must inspect prompt contract diff, not only tests.
- Docs:
  - `SPEC.md`
  - `docs/REQUIREMENTS_TRACEABILITY.md`

### RAGEXEC-005: Prompt/format regressions
- Goal:
  - add dedicated regressions for direct answers, empty-evidence refusal, and no forced headings.
- Runtime contract:
  - `format_for_telegram_answer(...)` must keep headingless direct answers readable without injecting or requiring legacy section labels,
  - legacy `Main Answer` / `Additionally Found` style headings may remain as backward-compatible input normalization only.
- Files:
  - `shared/utils.py`
  - `tests/test_rag_prompt_format.py`
- Checks:
  - `pytest tests/test_rag_prompt_format.py tests/test_rag_summary_modes.py`
- Review:
  - reviewer must confirm formatter is not compensating for a broken prompt.
- Docs:
  - `docs/TESTING.md`
  - `docs/REQUIREMENTS_TRACEABILITY.md`

### RAGEXEC-006: Token-level command grounding sanitizer
- Goal:
  - preserve grounded commands while still rejecting invented ones.
- Runtime contract:
  - command grounding must validate command signature and argument tokens separately instead of requiring exact full-line context matches,
  - grounded command variants with small formatting differences may survive,
  - invented options/arguments must still be removed.
- Files:
  - `shared/rag_safety.py`
  - `tests/test_rag_safety.py`
  - `tests/test_rag_safety_token_grounding.py`
- Checks:
  - `pytest tests/test_rag_safety.py tests/test_rag_safety_token_grounding.py`
- Review:
  - reviewer must inspect false-positive/false-negative balance.
- Docs:
  - `SPEC.md`
  - `docs/REQUIREMENTS_TRACEABILITY.md`

### RAGEXEC-007: Context-backed URL preservation
- Goal:
  - preserve grounded document/wiki URLs and still strip untrusted links.
- Runtime contract:
  - URL safety filtering must accept an explicit allowlist of grounded source-backed URLs in addition to raw context text,
  - markdown links and bare URLs backed by retrieval results must survive filtering,
  - unrelated URLs must still be stripped from generated answers.
- Files:
  - `shared/rag_safety.py`
  - `backend/api/routes/rag.py`
  - `tests/test_rag_safety.py`
  - `tests/test_rag_url_preservation.py`
- Checks:
  - `pytest tests/test_rag_safety.py tests/test_rag_url_preservation.py`
- Review:
  - reviewer must verify Gitee wiki URL preservation scenarios.
- Docs:
  - `SPEC.md`
  - `docs/REQUIREMENTS_TRACEABILITY.md`

### RAGEXEC-008: Dev-only eval dataset and local source-manifest contract
- Goal:
  - add a dev-only evaluation dataset/manifest contract that covers source families and security cases without committing real local corpora.
- Files:
  - `tests/data/rag_eval_ready_data_v2.yaml`
  - `tests/data/rag_eval_source_manifest_v1.yaml`
  - `backend/services/rag_eval_service.py`
  - `tests/test_rag_eval_dataset_contract.py`
  - `tests/test_rag_eval_fixture_manifest.py`
  - `tests/test_rag_eval_security_contract.py`
- Checks:
  - `pytest tests/test_rag_eval_dataset_contract.py tests/test_rag_eval_fixture_manifest.py tests/test_rag_eval_security_contract.py tests/test_rag_eval_service.py`
- Review:
  - reviewer must verify source-family/security coverage and confirm the slice does not embed real local corpus data in repo artifacts.
- Docs:
  - `SPEC.md`
  - `docs/TESTING.md`
  - `docs/REQUIREMENTS_TRACEABILITY.md`
  - `docs/CONFIGURATION.md`
  - `docs/OPERATIONS.md`

### RAGEXEC-009: Slice-aware baseline runner and gate
- Goal:
  - report quality by source family and failure mode, not only aggregate metrics.
- Runtime contract:
  - baseline artifacts must write per-label timestamped runs, stable latest snapshots, and append-only trend history,
  - slice aliases passed via CLI or present in run metadata must normalize to canonical gate names,
  - recorded/core slices from run metadata must remain required even when sample size falls to zero so the gate fails explicitly instead of pruning coverage,
  - gate output must distinguish source-family and security/failure-mode slice groups instead of flattening everything into one list.
- Files:
  - `scripts/rag_eval_baseline_runner.py`
  - `scripts/rag_eval_quality_gate.py`
  - `backend/services/rag_eval_service.py`
  - `tests/test_rag_eval_baseline_runner.py`
  - `tests/test_rag_eval_quality_gate.py`
- Checks:
  - `pytest tests/test_rag_eval_baseline_runner.py tests/test_rag_eval_quality_gate.py tests/test_rag_eval_service.py`
- Review:
  - reviewer must confirm the gate blocks meaningful regressions.
- Docs:
  - `SPEC.md`
  - `docs/OPERATIONS.md`
  - `docs/TESTING.md`
  - `docs/REQUIREMENTS_TRACEABILITY.md`

### RAGEXEC-010: CI fail-fast quality workflow
- Goal:
  - enforce the quality gate in CI and document the required local sequence.
- Workflow contract:
  - committed CI must run a public-safe fail-fast lane in this order: policy gate, secret scan, eval-tooling compile, synthetic eval-contract tests, then smoke tests,
  - CI must not require Ollama or developer-local corpora,
  - local docs must describe the same fast lane separately from any developer-local slow verification flow.
- Files:
  - `.github/workflows/agent-quality-gates.yml`
  - `docs/TESTING.md`
  - `docs/OPERATIONS.md`
- Checks:
  - focused CI/workflow validation + gate-related pytest suite
- Review:
  - reviewer must confirm pipeline failure semantics are correct.
- Docs:
  - `docs/USAGE.md`
  - `docs/OPERATIONS.md`
  - `docs/TESTING.md`

### RAGEXEC-011: Canonical wiki ingestion UX
- Goal:
  - keep one reachable wiki flow and remove unreachable callback branches or restore a proper producer path.
- Runtime contract:
  - admin UI must use one canonical path `kb_wiki_crawl -> waiting_wiki_root -> /ingestion/wiki-crawl`,
  - stale legacy wiki callback buttons must not rely on missing `wiki_urls` state or start orphan zip/git subflows,
  - canonical entry and completion must clear legacy wiki temp keys to avoid stale follow-up state.
- Files:
  - `frontend/bot_callbacks.py`
  - `frontend/bot_handlers.py`
- Checks:
  - `pytest tests/test_bot_text_ai_mode.py tests/test_bot_wiki_callbacks.py`
- Review:
  - reviewer must confirm no orphan callback path remains.
- Docs:
  - `SPEC.md`
  - `docs/USAGE.md`
  - `docs/REQUIREMENTS_TRACEABILITY.md`

### RAGEXEC-012: Wiki fallback visibility and regressions
- Goal:
  - make git-fallback vs HTML-crawl behavior visible and regression-tested.
- Runtime contract:
  - wiki-crawl stats must expose the actual sync mode plus whether git fallback was attempted,
  - admin bot messages must show the resulting sync mode,
  - Gitee git-loader success and git-loader-failure-to-HTML fallback both need dedicated regressions.
- Files:
  - `backend/api/routes/ingestion.py`
  - `backend/services/ingestion_service.py`
  - `frontend/bot_callbacks.py`
  - `frontend/bot_handlers.py`
  - `shared/wiki_scraper.py`
  - `shared/wiki_git_loader.py`
  - `tests/test_ingestion_routes.py`
  - `tests/test_bot_wiki_callbacks.py`
  - `tests/test_wiki_scraper.py`
- Checks:
  - `pytest tests/test_ingestion_routes.py tests/test_wiki_scraper.py tests/test_bot_text_ai_mode.py tests/test_bot_wiki_callbacks.py -k wiki`
- Review:
  - reviewer must confirm degraded fallback behavior is explicit.
- Docs:
  - `docs/OPERATIONS.md`
  - `docs/REQUIREMENTS_TRACEABILITY.md`

### RAGEXEC-013: Runtime canonical chunk contract
- Goal:
  - define and start wiring richer canonical chunk/document structure in runtime.
- Files:
  - `backend/services/ingestion_service.py`
  - `shared/database.py`
  - `docs/design/*`
- Checks:
  - `pytest tests/test_ingestion_metadata_contract.py tests/test_ingestion_outbox.py`
- Review:
  - reviewer must confirm additive schema/runtime contract and rollback path.
- Docs:
  - `SPEC.md`
  - `docs/REQUIREMENTS_TRACEABILITY.md`
  - `docs/OPERATIONS.md`
- Status:
  - completed 2026-03-09: new runtime writes now dual-write canonical chunk metadata into `metadata_json` plus additive `knowledge_chunks` columns (`chunk_hash`, `chunk_no`, `block_type`, `section_path_norm`, `token_count_est`, `parser_profile`), with nullable parser/page/adjacency fields kept for later source-fidelity slices.
  - review hardening closed the remaining gaps for secret redaction, direct wiki write-path canonicalization, and additive `migrate_database()` coverage.

### RAGEXEC-014: PDF/DOCX structural fidelity
- Goal:
  - improve parser fidelity and chunk metadata for PDF/DOCX ingestion.
- Files:
  - `shared/document_loaders/pdf_loader.py`
  - `shared/document_loaders/word_loader.py`
  - `shared/document_loaders/chunking.py`
- Checks:
  - `pytest tests/test_ingestion_metadata_contract.py tests/test_markdown_loader_metadata_contract.py <new parser tests>`
- Review:
  - reviewer must verify real structure retention, not only metadata presence.
- Docs:
  - `SPEC.md`
  - `docs/REQUIREMENTS_TRACEABILITY.md`
- Status:
  - completed 2026-03-09: PDF loader now preserves page-aware section/path/span metadata from normalized page text, and DOCX loader now preserves heading hierarchy plus paragraph-span hints with deterministic `chunk_no` and parser profiles.

### RAGEXEC-015: Web/wiki/code structural normalization
- Goal:
  - stabilize source path, section path, and structural chunking for web/wiki/code sources.
- Files:
  - `shared/document_loaders/web_loader.py`
  - `shared/document_loaders/code_loader.py`
  - `shared/wiki_git_loader.py`
  - `backend/services/ingestion_service.py`
- Checks:
  - `pytest tests/test_code_loader.py tests/test_markdown_loader_metadata_contract.py tests/test_wiki_scraper.py <new web/code tests>`
- Review:
  - reviewer must confirm structure-aware retrieval benefit, not just loader output shape.
- Docs:
  - `SPEC.md`
  - `docs/REQUIREMENTS_TRACEABILITY.md`
- Status:
  - completed 2026-03-09: web chunks now keep stable document + heading hierarchy, code chunks keep chunk-level symbol/span hints, and wiki git/zip imports normalize forward-slash page/file identities across platforms.

### RAGEXEC-016: Evidence-pack context composer
- Goal:
  - replace simple top-chunk assembly with deterministic evidence-pack composition.
- Files:
  - `backend/api/routes/rag.py`
  - `shared/rag_system.py`
- Checks:
  - `pytest tests/test_rag_query_definition_intent.py <new context tests>`
- Review:
  - reviewer must confirm context selection is explainable and bounded.
- Docs:
  - `SPEC.md`
  - `docs/REQUIREMENTS_TRACEABILITY.md`
- Status:
  - completed 2026-03-09: `/rag/query` and `/rag/summary` now assemble deterministic evidence packs with anchor-first ordering, same-doc structural support within bounded budgets, and query-focused excerpts for noisy long chunks.

### RAGEXEC-017: Context inclusion diagnostics
- Goal:
  - explain why chunks were chosen for final context, not only retrieval rank.
- Files:
  - `backend/api/routes/rag.py`
  - `backend/schemas/rag.py`
  - `tests/test_rag_diagnostics.py`
- Checks:
  - `pytest tests/test_rag_diagnostics.py tests/test_api_routes_contract.py`
- Review:
  - reviewer must verify context decision visibility for postmortems.
- Docs:
  - `docs/API_REFERENCE.md`
  - `docs/OPERATIONS.md`
- Status:
  - completed 2026-03-10: diagnostics candidates now expose explicit final-context inclusion markers (`included_in_context`, `context_rank`, `context_reason`, `context_anchor_rank`), and evidence-pack support rows can appear as synthetic `context_support` entries without leaking internal persistence marker keys into public metadata.

### RAGEXEC-018: Multi-corpus quality slices and thresholds
- Goal:
  - make “near-ideal” measurable across PDF/wiki/code/mixed corpora and failure modes.
- Files:
  - `tests/data/*`
  - `backend/services/rag_eval_service.py`
  - `scripts/rag_eval_quality_gate.py`
  - `docs/TESTING.md`
- Checks:
  - full eval baseline + quality gate compare
- Review:
  - reviewer must confirm thresholds are source-family-aware and not overfit to one corpus.
- Docs:
  - `SPEC.md`
  - `docs/REQUIREMENTS_TRACEABILITY.md`
  - `docs/TESTING.md`
  - `docs/OPERATIONS.md`
- Status:
  - completed 2026-03-10: eval runs now persist explicit per-slice thresholds for source families and failure modes, gate output separates `source_families` / `security_scenarios` / `failure_modes`, and baseline artifacts render dedicated failure-mode summaries instead of aggregate-only reporting.

### RAGEXEC-019: Controlled query rewriting and multi-query retrieval
- Goal:
  - improve recall/precision for short, ambiguous, or conversational KB questions without hardcoding corpus-specific terms.
- Files:
  - `backend/api/routes/rag.py`
  - `shared/rag_system.py`
  - `backend/services/rag_eval_service.py`
- Checks:
  - `pytest tests/test_rag_query_definition_intent.py tests/test_rag_quality.py <new rewrite/multi-query tests>`
- Review:
  - reviewer must verify rewrites stay bounded, grounded, and do not overfit to one corpus.
- Docs:
  - `SPEC.md`
  - `docs/REQUIREMENTS_TRACEABILITY.md`
  - `docs/OPERATIONS.md`
- Status:
  - completed 2026-03-10: generalized `/rag/query` now issues at most three bounded canonical variants (original + optional definition/point/fact/keyword focus rewrites), fuses hits by stable chunk identity via shared multi-query aggregation, and preserves legacy rollback mode as a single-query path; focused regressions cover bounded fan-out, rewrite-only recall gain, and fusion deduplication.

### RAGEXEC-020: Local answer-level judge metrics and commit deltas
- Goal:
  - add local-only answer-quality scoring and commit-to-commit comparison for faithfulness, relevancy, refusal, citation, and security resilience.
- Files:
  - `backend/services/rag_eval_service.py`
  - `scripts/rag_eval_baseline_runner.py`
  - `scripts/rag_eval_quality_gate.py`
  - `docs/TESTING.md`
  - `docs/OPERATIONS.md`
- Checks:
  - local-only eval baseline + compare run on developer corpora/Ollama, plus committed-safe schema/gate tests
- Review:
  - reviewer must verify there is no local-corpus leakage into repo artifacts and that answer-level metrics are trendable per commit.
- Docs:
  - `SPEC.md`
  - `docs/REQUIREMENTS_TRACEABILITY.md`
  - `docs/TESTING.md`
  - `docs/OPERATIONS.md`
- Status:
  - completed 2026-03-10: local-only eval runs can now score answer-level metrics and commit deltas with optional Ollama judge support, persist answer/security summaries only when the lane is enabled, keep retrieval-only artifacts free of dormant answer-lane metadata, and reject non-ollama provider inheritance/overrides to avoid local-corpus leakage.

### RAGFOLLOW-001: Refusal and security hardening for malicious or overbroad queries
- Goal:
  - improve refusal/security answer quality on real local corpora now that retrieval is already strong enough.
- Files:
  - `backend/api/routes/rag.py`
  - `shared/rag_safety.py`
  - `shared/utils.py`
  - `backend/services/rag_eval_service.py`
- Checks:
  - `pytest tests/test_rag_security_refusals.py tests/test_rag_safety.py tests/test_rag_url_preservation.py tests/test_rag_eval_service.py`
  - local answer-eval compare against the existing `live_kb3_answer_eval` baseline
- Review:
  - reviewer must verify deterministic refusal behavior for prompt-leak / secret-leak / overbroad private-data probes and confirm no regression in grounded URL handling.
- Docs:
  - `SPEC.md`
  - `docs/REQUIREMENTS_TRACEABILITY.md`
  - `docs/OPERATIONS.md`
- Status:
  - completed 2026-03-10: `/rag/query` and `/rag/summary` now refuse prompt-leak / secret-leak / overbroad private-data probes before LLM generation, poisoned context is blocked while benign security-doc examples stay answerable, and local live eval improved `refusal_accuracy` from `0.6923` to `1.0000` and `security_resilience` from `0.6923` to `1.0000` without retrieval regression.

### RAGFOLLOW-002: Fix retrieval diagnostics FK persistence
- Goal:
  - stop `retrieval_candidate_logs` from failing FK inserts when `retrieval_query_logs` should already exist.
- Files:
  - `backend/api/routes/rag.py`
  - `shared/database.py`
  - `tests/test_rag_diagnostics.py`
- Checks:
  - `pytest tests/test_rag_diagnostics.py tests/test_rag_diagnostics_contract.py tests/test_api_routes_contract.py`
- Review:
  - reviewer must confirm request-level retrieval log rows always exist before candidate rows are inserted.
- Docs:
  - `SPEC.md`
  - `docs/REQUIREMENTS_TRACEABILITY.md`
  - `docs/OPERATIONS.md`
- Status:
  - completed 2026-03-10: `_persist_retrieval_logs()` now flushes the parent `retrieval_query_logs` row before candidate inserts, unit coverage enforces parent-before-child ordering, and a reversible real-DB smoke confirmed temporary query/candidate rows can be inserted and cleaned up without the earlier FK-ordering warning.

### RAGFOLLOW-003: Remove zero-sample noise from local eval slices
- Goal:
  - make local live reports derive slice sets from actual suite coverage so filtered runs do not show irrelevant zero-sample slices.
- Files:
  - `backend/services/rag_eval_service.py`
  - `scripts/rag_eval_baseline_runner.py`
  - `scripts/rag_eval_quality_gate.py`
- Checks:
  - `pytest tests/test_rag_eval_service.py tests/test_rag_eval_quality_gate.py tests/test_rag_eval_baseline_runner.py`
- Review:
  - reviewer must confirm filtered local suites do not silently hide required recorded slices while avoiding irrelevant zero-sample clutter.
- Docs:
  - `docs/TESTING.md`
  - `docs/OPERATIONS.md`
- Status:
  - completed 2026-03-10: auto local eval runs now derive `metrics.slices` from actual suite coverage, so filtered live reports stop rendering unsupported `open_harmony_code` / `indirect_injection` zero-sample groups, while explicit `--slices` overrides remain strict for gate/debug scenarios; verified both by committed-safe tests and live artifact `live_kb3_answer_eval_sec4`.

### RAGFOLLOW-004: Local-only answer failure-analysis artifacts
- Goal:
  - persist per-case failure reasons and compact answer traces locally so answer regressions can be debugged faster than aggregate metrics allow.
- Files:
  - `backend/services/rag_eval_service.py`
  - `scripts/rag_eval_baseline_runner.py`
- Checks:
  - `pytest tests/test_rag_eval_service.py tests/test_rag_eval_baseline_runner.py`
- Review:
  - reviewer must confirm local artifacts stay private and committed-safe outputs still avoid developer-local corpus leakage.
- Docs:
  - `docs/TESTING.md`
  - `docs/OPERATIONS.md`
- Status:
  - completed 2026-03-10: local answer-eval artifacts now persist compact `case_analysis` entries with truncated query/answer previews, reasons, suspicious events, metric snapshots, and source-path hints; retrieval-only artifacts omit this debug payload, and live artifact `live_kb3_answer_eval_sec6` now renders `## Answer Failure Analysis` for real failing cases.

### RAGFOLLOW-005: Extractive fallback on LLM transport failures
- Goal:
  - avoid returning raw Ollama/OpenAI transport errors as the final RAG answer when retrieval already found usable grounded evidence.
- Files:
  - `backend/api/routes/rag.py`
  - `tests/test_rag_context_composer.py`
  - `tests/test_rag_summary_modes.py`
  - `tests/test_rag_security_refusals.py`
- Checks:
  - `pytest tests/test_rag_context_composer.py tests/test_rag_summary_modes.py tests/test_rag_security_refusals.py`
- Review:
  - reviewer must confirm `/rag/query` and `/rag/summary` return extractive grounded snippets with the existing `sources`, do not leak raw provider transport details, and preserve security refusals as higher priority than fallback.
- Docs:
  - `SPEC.md`
  - `docs/REQUIREMENTS_TRACEABILITY.md`
  - `docs/OPERATIONS.md`
- Status:
  - completed 2026-03-10: `/rag/query` and `/rag/summary` now replace raw provider timeout/503 strings with retrieval-only extractive fallbacks built from the selected evidence pack, preserve grounded `sources`, and still route fallback output through the standard URL/citation/command safety post-processing.

## Completion Rule

This backlog becomes the current execution source of truth after approval.

Approval status: `APPROVED:v1` recorded in `coordination/approval-overrides.json`.
