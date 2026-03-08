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

### RAGEXEC-008: Eval corpus expansion for real uploaded knowledge
- Goal:
  - extend the fixed corpus with wiki/code/long-doc/multilingual cases.
- Files:
  - `tests/data/rag_eval_ready_data_v2.yaml`
  - `backend/services/rag_eval_service.py`
  - `tests/test_rag_eval_dataset_contract.py`
- Checks:
  - `pytest tests/test_rag_eval_dataset_contract.py tests/test_rag_eval_service.py`
- Review:
  - reviewer must verify source-family coverage, not only dataset validity.
- Docs:
  - `SPEC.md`
  - `docs/TESTING.md`
  - `docs/REQUIREMENTS_TRACEABILITY.md`

### RAGEXEC-009: Slice-aware baseline runner and gate
- Goal:
  - report quality by source family and failure mode, not only aggregate metrics.
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
  - `docs/OPERATIONS.md`
  - `docs/TESTING.md`

### RAGEXEC-010: CI fail-fast quality workflow
- Goal:
  - enforce the quality gate in CI and document the required local sequence.
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
- Files:
  - `frontend/bot_callbacks.py`
  - `frontend/bot_handlers.py`
- Checks:
  - `pytest tests/test_bot_text_ai_mode.py`
- Review:
  - reviewer must confirm no orphan callback path remains.
- Docs:
  - `SPEC.md`
  - `docs/USAGE.md`
  - `docs/REQUIREMENTS_TRACEABILITY.md`

### RAGEXEC-012: Wiki fallback visibility and regressions
- Goal:
  - make git-fallback vs HTML-crawl behavior visible and regression-tested.
- Files:
  - `shared/wiki_scraper.py`
  - `shared/wiki_git_loader.py`
  - `tests/test_wiki_scraper.py`
- Checks:
  - `pytest tests/test_wiki_scraper.py tests/test_bot_text_ai_mode.py -k wiki`
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

## Completion Rule

This backlog becomes the current execution source of truth after approval.

Approval status: `APPROVED:v1` recorded in `coordination/approval-overrides.json`.
