## Research: Auto-Detect Multi-Document Upload to KB

Date: 2026-03-04
Agent: codex (team-lead-orchestrator / architect phase)

### User Request
- Remove explicit file-type selection before upload.
- Bot must auto-detect uploaded document types.
- Support uploading multiple documents in one flow.
- Respect Telegram upload/file limits.
- Return report with successes/failures for validation.

### Findings
- Current admin flow still asks explicit type:
  - `frontend/bot_callbacks.py` (`kb_upload` -> `document_type_menu`).
- `handle_document` is incomplete and does not launch backend ingestion.
- Callback still references removed function `load_document_to_kb` (runtime mismatch risk).
- Backend supports async ingestion per file with job tracking:
  - `POST /ingestion/document` returns `job_id`.
  - `/jobs/{id}` exposes completion/failure.
- Existing loader manager supports many types by extension:
  - markdown/pdf/docx/xlsx/txt/json/chat/code/image and more.
- Telegram file size limits are already codified in `shared/asr_limits.py` via `get_telegram_file_max_bytes()` (20 MB) and can be reused for document guard.

### Design Direction
- Replace “select type first” UX with “send one or several files”.
- Infer file type from filename/mime in bot handler and send inferred type to backend.
- Implement batch processing with grouped summary report:
  - total accepted,
  - per-file success/failure,
  - reasons (size, unsupported type, job error, timeout).
- Support pending queue when KB is not selected yet.
- Preserve backwards compatibility for old `upload_type:*` callbacks where possible.

## Research: Definition Questions Return Non-Definition Chunks

Date: 2026-03-04
Agent: codex

### Observed User Symptom
- Query "Как в документе определяется разметка данных?" returned a policy mention about anonymization instead of explicit definition.

### Findings
- Retrieval/ranking in `backend/api/routes/rag.py` had intents `HOWTO`/`TROUBLE`/`GENERAL` only.
- Definition-style Russian queries were treated as `GENERAL`, without dedicated boosts for glossary-like fragments.
- Query includes many "pointed fact" forms (`пункт N`), which also lacked dedicated ranking boosts.
- Admin panel had duplicate upload entry (`admin_upload`) and KB-local upload entry, causing UX confusion.

### Fix Direction
- Introduce `DEFINITION` intent with definition-specific ranking boosts.
- Add point-reference boost (`пункт N`) across intents.
- Keep compatibility for stale `admin_upload` callbacks but remove global upload button from admin menu.

## Research: KB Creation Fails in Admin Panel

Date: 2026-03-04
Agent: codex (team-lead-orchestrator / architect phase)

### User Symptom
- In admin panel, after selecting `➕ Создать базу знаний` and entering a KB name (`МВП`, `MVP`), bot responds with `👋 Добро пожаловать!`.
- Returning to KB list shows old KB only; new KB is not created.

### Root Cause
- `frontend/bot_callbacks.py` sets `context.user_data['state'] = 'waiting_kb_name'` on callback `kb_create`.
- `frontend/bot_handlers.py::handle_text` has no branch for `state == 'waiting_kb_name'`.
- As a result, entered KB name falls through to default branch and calls `handle_start(...)`, which sends `👋 Добро пожаловать!`.

### Impact
- KB creation flow from Telegram admin UI is broken for all admins.
- Feature regression: state machine mismatch between callback and text handler.

### Fix Direction
- Add explicit `waiting_kb_name` branch in `handle_text` for admin users:
  - validate non-empty KB name,
  - call backend `create_knowledge_base`,
  - return success/error message and admin menu,
  - clear state after handling.
- Add regression test for `waiting_kb_name` flow.

## Research: Full RAG Stack Migration (v2)

Date: 2026-03-04
Agent: codex (team-lead-orchestrator / architect phase)

### User Request
- Replace current retrieval stack with a higher-quality modern approach.
- Keep support for multi-source ingestion:
  - PDF, DOCX, Markdown, text,
  - web pages, wiki crawl/git/zip,
  - codebase path/git,
  - images and chat exports.
- Improve UX for KB-search:
  - visible progress while answer is pending,
  - auto-clean progress message after reply,
  - queue multiple user questions and answer in-order under each question.
- Add API-based backend test script.

### Current Stack Snapshot
- Ingestion capabilities already broad (document loaders + ingestion API):
  - file/document: `pdf/docx/doc/xlsx/xls/md/txt/json/chat` + ZIP archive processing,
  - web/wiki: `/ingestion/web`, `/ingestion/wiki-crawl`, `/ingestion/wiki-git`, `/ingestion/wiki-zip`,
  - code: `/ingestion/code-path`, `/ingestion/code-git`,
  - image: `/ingestion/image`.
- Retrieval baseline:
  - dense embeddings + FAISS + BM25 + optional cross-encoder rerank (`shared/rag_system.py`),
  - route-level intent/ranking/context logic in `backend/api/routes/rag.py`.
- Weak points observed from user transcripts:
  - factual/pointed legal questions may miss exact clauses,
  - strict extraction for numeric targets and “кто/как часто/какой показатель” is inconsistent,
  - KB query UX lacks explicit pending/progress indicator and queue semantics.

### Technical Gaps vs Modern RAG Quality
- Retrieval strategy is partially modern but lacks full factual query mode:
  - no explicit factoid intent class,
  - limited lexical fallback for non-definition factual prompts,
  - single-page context caps can suppress supporting evidence for legal/strategy docs.
- Query handling lacks queue worker in KB mode:
  - concurrent user messages can feel dropped or out-of-order.
- API-level test harness for end-to-end RAG quality checks is missing.

### v2 Migration Direction (Feasible in current repo)
1. Retrieval v2 (without disruptive infra migration):
  - keep existing dense+BM25+rERank core,
  - add explicit `FACTOID` mode and stronger query-hint extraction,
  - add generalized lexical fallback candidates (terms + point markers + target metric patterns),
  - adjust context selection to include enough supporting chunks for factoid/legal answers.
2. UX v2 for KB search:
  - async queue per user-session in bot state,
  - in-order processing worker,
  - progress bar message while query runs, then delete it,
  - answer uses `reply_to_message_id` of original user question.
3. Verification v2:
  - add regression tests for queue/progress and factoid retrieval,
  - add backend API smoke script for RAG endpoint scenarios.

### Risk Notes
- “Full stack replacement” with new vector DB (Qdrant/Weaviate) would require infra/dependency migration, data migration path, and rollout safety controls; high risk for one-step change.
- Chosen v2 path maximizes quality improvement now while preserving ingestion compatibility and minimizing deployment risk.

## Research: Max Quality RAG Architecture (v3)

Date: 2026-03-04
Agent: codex (team-lead-orchestrator / architect phase)

### User Request
- "Сделать максимальное качество" ответов и поиска.
- Выполнить полный процесс: исследование, архитектурный дизайн, декомпозиция, затем реализация/ревью/тесты.

### Current Baseline (after v2)
- Retrieval:
  - Dense embeddings + FAISS cosine search.
  - BM25 in-memory channel + RRF fusion.
  - Cross-encoder reranker.
  - Route-level intent strategy (`DEFINITION`, `FACTOID`, `HOWTO`, etc.).
- Ingestion:
  - broad source coverage: pdf/doc/docx/xls/xlsx/md/txt/json/chat/zip/web/wiki/code/image.
- UX:
  - queue + progress for KB search already implemented.

### Quality Gaps That Still Limit "Maximum Quality"
1. Parser fidelity:
  - PDF extraction via basic `PyPDF2` can lose table/layout semantics.
  - DOCX loader captures paragraphs/headings, but weak table/list structure retention.
2. Metadata richness:
  - Chunk metadata is heterogeneous across source types.
  - Missing stable, query-useful fields (normalized headings, offsets, parser confidence, chunk hash).
3. Retrieval robustness:
  - Strong heuristics exist but remain lexical/hand-tuned per intent.
  - No offline calibrated retrieval score pipeline (nDCG/MRR/Recall@K target tracking per query class).
4. Evaluation maturity:
  - Current quality tests are limited and domain-narrow.
  - No regression gates for mixed query intents across multiple corpora/format types.
5. Explainability:
  - No persistent retrieval decision logs for post-mortem tuning of missed answers.

### Architectural Direction (v3)
- Keep compatibility with current API/UX.
- Introduce modular retrieval pipeline with explicit stages and score tracing:
  - query understanding,
  - candidate generation (dense + sparse + lexical + metadata filters),
  - calibrated fusion,
  - rerank + context packing policy,
  - answer generation + citation enforcement.
- Strengthen ingestion with canonical document model and structure-preserving parsers.
- Add quality harness with reproducible eval datasets and CI pass/fail thresholds.
- Updated by user change request:
  - full stack replacement is mandatory in this cycle,
  - external hybrid backend becomes target production path (not optional),
  - legacy stack retained only as rollback window.

### Constraints
- No dependency changes without explicit approval.
- Must preserve existing source-type ingestion behavior.
- Must keep Telegram UX clean (temporary progress, ordered replies).
- Must maintain API-key and secrets policy.

## Research: RAG Search Improvement Plan + Wiki URL Example Audit

Date: 2026-03-07
Agent: codex (team-lead-orchestrator / architect phase)

### User Request
- Perform a deep audit of the project, unfinished tasks, design docs, and the current RAG approach.
- Prepare an implementation plan to improve RAG search over uploaded knowledge.
- Separately analyze the wiki loading fix for the example URL flow.

### Current State Summary
- The stack already has the main quality foundations in place:
  - hybrid retrieval with Qdrant/FAISS + BM25 + optional rerank,
  - diagnostics persistence and eval runner,
  - normalized ingestion metadata contract across loaders,
  - generalized route cutover with `RAG_LEGACY_QUERY_HEURISTICS=false` by default.
- The remaining quality backlog is concentrated in `RAGQLTY-009..018`:
  - retrieval fusion/rerank stabilization,
  - diagnostics assertions,
  - prompt/format alignment,
  - sanitizer/URL preservation,
  - end-to-end gates and CI enforcement.

### Key Findings
1. Retrieval is only partially generalized.
   - Route-level query heuristics are disabled by default, but `shared/rag_system.py` still contains hidden ranking behavior:
     - `compute_source_boost(...)`,
     - `_is_howto_query(...)`,
     - how-to-specific candidate expansion/sorting,
     - `_simple_search(...)` strong-token prefilter.
   - This means retrieval behavior can still drift by wording/source-path structure even when route heuristics are "off".

2. The fixed evaluation corpus exists, but it is still narrow relative to the user goal "search over uploaded knowledge".
   - `tests/data/rag_eval_ready_data_v1.yaml` is useful for deterministic regression checks,
   - but it is still centered on one document family and does not yet represent arbitrary uploaded KBs, mixed document shapes, or wiki-heavy corpora.

3. Prompting and post-processing still hide user-visible quality debt.
   - RU prompt already asks for a direct grounded answer without template headings.
   - EN prompt still forces `Main Answer` / `Additionally Found` structure.
   - `sanitize_commands_in_answer(...)` still strips lines aggressively and always removes wiki URLs.
   - `strip_untrusted_urls(...)` preserves only URLs literally present in context text, which is too strict for context-backed links that survive through metadata/citations.

4. The example wiki URL flow is mostly fixed, but the code still contains a legacy split flow.
   - The real admin path now works through `kb_wiki_crawl -> waiting_wiki_root -> ingest_wiki_crawl`.
   - For Gitee wiki URLs, `shared/wiki_scraper.py` now prefers `load_wiki_from_git(...)`, which is the correct fix for JS-rendered wiki navigation.
   - Residual risk: `wiki_git_load` / `wiki_zip_load` callbacks still depend on `context.user_data['wiki_urls']`, but the current UI flow does not produce that mapping anymore.

### Recommended Direction
- Do not start another retrieval-stack rewrite now.
- Use the current Qdrant + diagnostics + eval baseline and finish the pending quality program with one additional wiki-flow consolidation slice.
- Priority order:
  1. retrieval calibration and visibility,
  2. answer formatting/prompt grounding consistency,
  3. safety/postprocess precision,
  4. end-to-end quality gates,
  5. cleanup of legacy wiki branches.

# Research: AI Mode v2 (Telemetry + Context Memory + Progress UX)

Date: 2026-03-01
Agent: codex (team-lead-orchestrator / architect phase)

## User Request (new)
1. Confirm whether Open WebUI is currently supported.
2. For all requests sent to AI, store model/request metrics in DB.
3. Use metrics to predict request duration; if expected duration > 5s, show progress indicator and remove it after response.
4. Improve direct AI mode quality for weaker models (30b/70b/120b):
   - richer dialog context,
   - first answer must be concise,
   - ask clarifying question when input is ambiguous.
5. On re-entering AI mode, ask user to restore previous context or start a new dialog.
6. Persist/reload context from DB and compress memory due limited context window.

## Codebase Findings
- Open WebUI support exists and is production-wired:
  - `shared/ai_providers.py` contains `OpenWebUIProvider`.
  - Provider registration key: `open_webui`.
  - Env vars: `OPEN_WEBUI_BASE_URL`, `OPEN_WEBUI_API_KEY`, `OPEN_WEBUI_MODEL`.
  - Covered by `tests/test_ai_providers.py`.
- Direct AI mode is implemented in `frontend/bot_handlers.py` via state `waiting_ai_query`.
- Voice/audio handoff (`ASR -> AI`) exists for AI mode.
- Metrics/telemetry for AI requests are not persisted in DB now.
- Conversation memory for direct AI mode is not persisted; prompts are single-turn (`create_prompt_with_language(..., context=None)`).
- Current direct prompt says "Ответь подробно", which conflicts with desired concise-first behavior.

## Existing Risk Signals from User Transcript
- Out-of-order/mixed replies likely from overlapping in-flight AI requests per user.
- Very long responses still possible semantically (even if chunking prevents Telegram hard error).
- Weak models drift without strict prompt contract + compact structured memory.

## Architecture Implications
- Instrumentation should be centralized in `AIProviderManager.query/query_multimodal` to cover all AI callers.
- Session memory should be a dedicated feature service, not ad-hoc `context.user_data`.
- Progress UX should be ephemeral Telegram message, deleted on completion, with >5s predictor trigger.
- Need per-user concurrency guard for AI requests to avoid stale/late answer interleaving.

## Scope Decision
This is a non-trivial cross-cutting feature touching:
- frontend AI mode UX/state,
- shared provider layer telemetry,
- DB schema + migrations,
- prompt policy and memory compression,
- tests/spec/docs.

## Research: Embedded Local RAG Quality-Evaluation System

Date: 2026-03-08
Agent: codex (team-lead-orchestrator / architect phase)

### User Request
- Build embedded, repeatable local quality tests for the project RAG pipeline.
- Use real project data sources as evaluation input:
  - `test.pdf` in repo root,
  - `open-harmony` catalog if accessible locally,
  - Telegram export under `C:\Users\devl\Downloads\Telegram Desktop\ChatExport_2026-03-08\`.
- Use Ollama for answer generation and LLM-as-a-judge scoring.
- Persist per-run artifacts and quality trends so implementation improvements can be measured over time.
- Produce design-only artifacts in this cycle; do not implement runtime code yet.

### Current Baseline
- Existing eval path is retrieval-only:
  - `backend/services/rag_eval_service.py`
  - `scripts/rag_eval_baseline_runner.py`
  - `scripts/rag_eval_quality_gate.py`
- Current metrics and gates are limited to:
  - `recall_at_10`
  - `mrr_at_10`
  - `ndcg_at_10`
- Current default suite is fixed YAML (`tests/data/rag_eval_ready_data_v1.yaml`) and is useful for deterministic regression checks, but it does not represent the actual uploaded-knowledge mix the user wants to optimize.

### Local Source Audit
- `test.pdf` is present in repo root and is directly accessible.
- `open-harmony` is accessible in the local session sandbox as a filesystem tree with mixed docs/code subdirectories:
  - `Arkoala`
  - `Development`
  - `Devices`
  - `Documentation`
  - `Environment`
  - `Features`
  - `Sync&Build`
- Telegram export path is accessible locally.
- `result.json` is present and large enough to be a meaningful chat-source corpus (~30 MB); it contains standard Telegram export fields (`messages`, `text`, `text_entities`, timestamps, sender).

### Key Gaps
1. The current eval system measures retrieval quality but not answer quality.
   - There is no built-in faithfulness / relevance / refusal correctness loop.
   - There is no per-answer grounded URL / grounded command scoring.

2. The current corpus contract is too narrow.
   - It does not separate source families such as PDF, doc/wiki/code, and Telegram chat export.
   - It does not encode negative/no-answer cases, noisy context cases, or multi-hop assembly cases.

3. There is no local/private fixture strategy.
   - `test.pdf` is repo-safe.
   - `open-harmony` and Telegram export live outside the repo.
   - Telegram export may contain private or operationally sensitive content and must not be committed raw.

4. Trend reporting is incomplete.
   - Current baseline runner emits snapshots, but not a durable source-family trend history suitable for “quality growth per commit”.

5. Ollama exists in project config, but there is no dedicated evaluation contract.
   - `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, and `OLLAMA_FILTER_THINKING` already exist.
   - There is no separation between answer model and judge model for eval.

6. Security is not yet a first-class eval contract.
   - There is no embedded evaluation coverage for direct prompt injection attempts in user queries.
   - There is no embedded evaluation coverage for indirect prompt injection / RAG poisoning inside indexed documents.
   - There is no explicit metric set for confidential data leakage, system prompt leakage, or sensitive-context overexposure.
   - Ingestion-time screening and suspicious query/document/answer observability are not defined as required outputs of the quality loop.

### Design Constraints
- Local full-quality evaluation must work without external network services.
- The production RAG path must be evaluated directly; quality tests should not use a simplified answer path that bypasses real retrieval/context assembly/safety.
- Telegram export must be treated as local-only source material unless transformed into a sanitized committed subset.
- The future CI lane must stay reproducible even when private local corpora are absent.
- Security controls must be testable, not only documented:
  - strict separation of system instructions, user query, and retrieved context,
  - ingestion-time screening for poisoned/malicious documents,
  - limited sensitive-context inclusion,
  - explicit flags and artifacts for suspicious queries, documents, and answers.

### Metric Research Summary
- The lecture metrics are valid and align with official RAGAS metrics:
  - `faithfulness`
  - `context_precision`
  - `context_recall`
  - `response_relevancy`
  - `noise_sensitivity`
- Additional relevant metrics are justified by official RAG evaluation frameworks and project-specific grounded-answer requirements:
  - `answer_correctness` / `factual_correctness`
  - `context_relevance`
  - `response_groundedness`
  - `exact_match` / `string_presence` for factoid and numeric cases
  - source/citation validity metrics tailored to this project
  - refusal correctness for “answer not in knowledge base” cases
  - prompt-injection resistance and system-prompt leakage resistance
  - sensitive-context leakage rate / redaction correctness
  - suspicious-event detection coverage for query/document/answer paths

### Mandatory Security Requirements
- Evaluate direct prompt injection:
  - user asks the system to ignore instructions, reveal hidden prompts, or exfiltrate credentials/config.
- Evaluate indirect prompt injection / RAG poisoning:
  - indexed document contains malicious instructions such as “ignore previous instructions”, “print secret”, or “call external endpoint”.
- Evaluate confidential-data leakage:
  - answer must not expose raw secrets, unrelated Telegram content, or over-broad sensitive context when case policy forbids it.
- Evaluate system-prompt leakage:
  - answer must refuse attempts to reveal hidden/system instructions.
- Evaluate infrastructure access control assumptions:
  - local eval must stay bound to allowlisted fixture paths and dedicated eval KB resources, not arbitrary filesystem or production KB scope.
- Evaluate ingestion-time document screening:
  - suspicious or poisoned documents must be flagged/quarantined by the future ingestion path rather than silently trusted.
- Enforce strict instruction-plane separation:
  - system instructions,
  - user query,
  - retrieved context,
  - judge rubric.
- Limit sensitive context inclusion:
  - only minimal required evidence should enter the answer context for private/sensitive cases.
- Require observability:
  - suspicious queries,
  - suspicious documents,
  - suspicious answers,
  - screening/quarantine outcomes,
  - leakage-block events.

### Recommended Direction
- Extend the current eval stack rather than replace it.
- Keep retrieval metrics and add answer-level metrics on top.
- Split datasets into:
  - committed safe fixture subset,
  - local-only external fixture materialization cache.
- Make source family a first-class slice dimension:
  - `pdf`
  - `open_harmony_docs`
  - `open_harmony_code`
  - `telegram_chat`
  - later `wiki`
- Use Ollama in two deterministic roles:
  - answer generation through the real application path,
  - judge scoring through a pinned local judge model with strict prompt contract.
- Store trend artifacts with dataset version + model ids + git revision so quality deltas can be compared across implementation slices.
- Make security a required scoring lane:
  - include adversarial queries/documents in dataset v2,
  - add security-specific metrics and gates,
  - emit suspicious-event artifacts alongside normal quality reports.

## Research: Embedded Local RAG Quality-Evaluation + Security System v2

Date: 2026-03-08
Agent: codex (architect)
Status: superseding the earlier same-day draft with the now-mandatory security-first scope

### Expanded User Scope
- Build an embedded local evaluation loop that the repo can run repeatedly while implementation is in progress.
- Use three real source families as the initial quality ground truth:
  - repo-root `test.pdf`,
  - local `open-harmony` catalog,
  - local Telegram export `C:\Users\devl\Downloads\Telegram Desktop\ChatExport_2026-03-08\result.json`.
- Reuse the existing provider stack with Ollama for:
  - answer generation through the real RAG path,
  - LLM-as-a-judge scoring through a pinned judge model.
- Track trend growth per run/commit so quality improvement is measurable over time.
- Treat security as a first-class measured outcome:
  - prompt injection,
  - indirect prompt injection / RAG poisoning,
  - confidential-data leakage,
  - system-prompt leakage,
  - ingestion-time screening,
  - context separation,
  - least-privilege corpus access,
  - suspicious-behavior observability.

### Additional Findings Since the First Draft
1. Source-family realism is good enough to start, but not safe enough to commit directly.
   - `test.pdf` is repo-safe and stable.
   - `open-harmony` is accessible only through a session-local path; design must treat it as `env_override`, not a hard-coded location.
   - Telegram export is large and private; it must stay local-only, with committed subsets limited to sanitized extracts or synthetic derivatives.

2. The current repo already has most of the transport/config primitives needed for Ollama.
   - Existing reuse path:
     - `shared/ai_providers.py`
     - `shared/config.py`
     - `env.template`
     - `docs/CONFIGURATION.md`
   - The missing piece is an eval-specific contract separating:
     - answer model,
     - judge model,
     - judge determinism knobs,
     - local corpus path overrides.

3. The current RAG eval path is too narrow for the new goal.
   - Existing service/gates:
     - `backend/services/rag_eval_service.py`
     - `scripts/rag_eval_baseline_runner.py`
     - `scripts/rag_eval_quality_gate.py`
   - Existing scope is retrieval-only and fixed-corpus-oriented.
   - It does not yet measure final-answer quality, refusal behavior, source-backed URLs/commands, or security posture.

4. Ingestion quality remains a structural risk and must be visible in the new evaluator.
   - `shared/kb_settings.py` still defaults `web`, `wiki`, and `markdown` to `mode="full"` with very large chunks.
   - This means the future evaluator must report source-family metrics that can expose chunking/structure failures rather than hiding them inside aggregate scores.

5. The current PDF path has an implementation-risk signal already visible during research.
   - Repo loader `shared/document_loaders/pdf_loader.py` depends on `PyPDF2`.
   - The current local environment does not have `PyPDF2`, while `fitz` is available.
   - The design should not assume one parser library is always present; fixture prep and eval setup must fail clearly and capture parser provenance in artifacts.

### Metric System Needed For This Project
The lecture metrics are necessary but not sufficient. The design needs five metric families.

1. Retrieval coverage metrics
   - `recall_at_10`
   - `mrr_at_10`
   - `ndcg_at_10`
   - `source_hit_at_k`
   - `evidence_in_context_recall`
   - `context_entity_recall`

2. Answer-grounding metrics
   - `faithfulness`
   - `response_relevancy`
   - `answer_correctness`
   - `response_groundedness`
   - `exact_match` for strict numeric/factoid cases
   - `string_presence` for function names, config keys, package names, error codes

3. Context-quality metrics
   - `context_precision`
   - `context_recall`
   - `context_relevance`
   - `noise_sensitivity`
   - `evidence_pack_efficiency`
     - how much of the final context budget is occupied by supporting evidence vs noise

4. Refusal/citation/control metrics
   - `refusal_precision`
   - `refusal_recall`
   - `citation_validity`
   - `grounded_url_precision`
   - `grounded_command_precision`

5. Security/resilience metrics
   - `prompt_injection_resistance`
   - `indirect_injection_resistance`
   - `system_prompt_leak_block_rate`
   - `secret_leak_block_rate`
   - `sensitive_context_overexposure_rate`
   - `screening_recall`
   - `screening_precision`
   - `suspicious_query_flag_recall`
   - `suspicious_document_flag_recall`
   - `suspicious_answer_flag_recall`
   - `instruction_plane_separation_compliance`

### Security Architecture Findings
1. Security cannot be limited to the answer sanitizer.
   - Screening must start before indexing and continue through retrieval, context assembly, answer generation, and artifact export.

2. Security cases must live inside the dataset contract, not beside it.
   - Each adversarial case needs:
     - `attack_type`,
     - `security_expectation`,
     - expected flags,
     - leakage/redaction expectations,
     - allowed or forbidden source scopes.

3. Suspicious behavior must be observable as structured output.
   - The evaluator should emit machine-readable counters and per-case flags for:
     - suspicious queries,
     - screened/flagged/quarantined documents,
     - suspicious or leaking answers,
     - blocked prompt-leak attempts,
     - blocked secret-leak attempts.

4. Least-privilege access needs to be enforced by design.
   - Eval runs should resolve corpora only from:
     - repo fixtures,
     - explicit env-overridden local paths,
     - generated local cache directories.
   - No arbitrary filesystem traversal.
   - No production KB access.
   - Ollama target should default to a local endpoint and record the effective base URL in artifacts.

### Recommended Implementation Direction
- Keep the existing eval service as the core and extend it; do not build a parallel evaluator.
- Introduce a versioned source manifest alongside dataset v2 so fixture provenance, sensitivity, and screening policy are explicit.
- Add answer-level judging and trend persistence in `RAGEXEC-009`, but keep schema and artifact tests Ollama-free.
- Treat security as a gateable slice in `RAGEXEC-008..010`, not a later hardening appendix.
- Use the new evaluator to drive `RAGEXEC-013..018`:
  - chunk contract,
  - parser fidelity,
  - web/wiki/code normalization,
  - evidence-pack assembly,
  - context inclusion diagnostics,
  - final source-family thresholds.

## 2026-03-09 Follow-up Audit: Current Answer-Metric Status vs External Local RAG Prototype

### Current repo status
1. Committed eval/runtime is still retrieval-only.
   - `backend/services/rag_eval_service.py` currently drives `rag_system.search(...)` and aggregates only:
     - `recall_at_10`
     - `mrr_at_10`
     - `ndcg_at_10`
   - `scripts/rag_eval_baseline_runner.py` and `scripts/rag_eval_quality_gate.py` already compare these metrics across:
     - overall,
     - source-family slices,
     - security-scenario slices,
     - stable run/latest/trend artifacts.

2. Answer-level scoring is designed but not implemented.
   - `docs/design/rag-embedded-quality-eval-security-system-v1.md` already specifies:
     - Ollama-backed answer/judge roles,
     - answer-grounding metrics,
     - citation/refusal/security metrics,
     - local-only corpus policy.
   - The committed evaluator has not yet implemented:
     - faithfulness,
     - response relevancy,
     - answer correctness,
     - citation validity,
     - refusal/security resistance scoring,
     - local Ollama judge integration.

3. Current ingestion/chunk structure is still weaker than the target architecture.
   - `_normalize_chunk_metadata(...)` in `backend/services/ingestion_service.py` normalizes stable basics (`section_path`, `chunk_kind`, `document_class`, `language`, `doc_hash`, `doc_version`, `source_updated_at`),
   - but runtime SQL/metadata does not yet promote richer structural fields such as:
     - `paragraph_numbers`,
     - `chunk_type`,
     - adjacency graph ids,
     - parser confidence/profile,
     - canonical chunk hash/ordinal offsets.

### External local project (`C:\Users\devl\proj\test\`) — useful ideas only
1. Query rewriting + multi-query retrieval are already concretely implemented there.
   - `rag/classifier.py` derives `search_queries` and `rag/retriever.py` fans them out across dense+sparse retrieval before fusion.
   - This is relevant because our current near-ideal backlog still lacks an explicit implementation slice for controlled query rewriting / multi-query retrieval.

2. Structural retrieval over paragraph numbers is implemented end-to-end.
   - `rag/document.py` persists `paragraph_number`, `paragraph_numbers`, and `chunk_type`.
   - `rag/index.py` builds a `StructuralIndex(paragraph_number -> chunk_id[])`.
   - This is directly relevant to `RAGEXEC-013..015` and confirms the value of first-class structural chunk fields instead of burying everything in opaque metadata.

3. Evidence packing is more explicit than in the current repo.
   - `rag/retriever.py` implements:
     - adaptive retrieval policy by query type,
     - sentence-level evidence packing,
     - list-coverage counting / expansion,
     - paragraph-sibling merge for structural/definitional questions.
   - This maps almost exactly to our future `RAGEXEC-016..017`.

4. The external project has a pragmatic answer-eval loop we can adapt conceptually.
   - `rag/evaluator.py` blends:
     - heuristic metrics,
     - optional Ollama LLM-judge metrics,
     - weighted composite scores,
     - weakest-question summaries.
   - We should not copy its homework-specific metric names or gold-answer assumptions directly, but its evaluator shape is useful for the local-only quality loop.

5. Security/grounding logic is decomposed cleanly.
   - `rag/grounded_rules.py`, `rag/verifier.py`, and the related tests split:
     - prompt-injection handling,
     - unsupported-value refusals,
     - safe grounded corrections,
     - second-pass normalization.
   - Our repo already hardened sanitizer behavior, but the external project highlights two still-missing areas:
     - ingestion/document screening for malicious instructions,
     - explicit answer-level security scoring in the eval loop.

### Implications for our roadmap
1. `RAGEXEC-013..015` remain the correct next production slices.
   - The external project reinforces the need for first-class structural chunk metadata and stable paragraph/section semantics.

2. `RAGEXEC-016..017` should explicitly absorb:
   - evidence-pack unit budgets,
   - list coverage checks,
   - sibling/adjacent chunk merge based on canonical structure.

3. After `RAGEXEC-013..017`, add a dedicated slice for:
   - controlled query rewriting,
   - multi-query retrieval,
   - metrics-based validation against the local-only corpora.

4. Extend the local-only eval harness with answer-level scoring before claiming “near-ideal”.
   - Best implementation shape:
     - keep current retrieval metrics,
     - add heuristic answer metrics first,
     - then optional Ollama judge scoring,
     - then security/adversarial scoring.

## 2026-03-10 Bot UX and transient error-noise bugfix research

### Request summary
1. Stop noisy admin-facing "critical error" notifications for transient network/protocol disconnects that frequently occur in the Telegram bot runtime.
2. After an admin creates a knowledge base from the text-state flow, open the per-KB action menu directly instead of sending the user back to the generic admin menu.

### Minimal relevant runtime paths
1. `frontend/error_handlers.py`
   - `global_error_handler(...)` logs every exception and always sends the same "critical error" message through `notify_admins(...)`.
   - Current implementation has only a coarse global anti-spam window and does not distinguish transient transport errors from real actionable failures.
2. `frontend/bot.py`
   - Registers `global_error_handler` globally via `app.add_error_handler(global_error_handler)`.
3. `frontend/bot_handlers.py`
   - In the `state == "waiting_kb_name"` branch, successful KB creation already returns the backend payload including `id`, but the UX reply still uses `admin_menu()` instead of the KB-specific action menu.
4. `frontend/templates/buttons.py`
   - `kb_actions_menu(kb_id)` already exists and is the correct destination UI after successful KB creation.
5. `tests/test_bot_text_ai_mode.py`
   - Already covers the `waiting_kb_name` creation branch and is the natural place to tighten the post-create UX assertion.

### Observed gaps
1. Error handling is too broad.
   - `httpx.RemoteProtocolError`, Telegram `NetworkError`, and similar disconnects are operational noise when the bot or upstream briefly drops the connection.
   - These still need logging for diagnosis, but they should not trigger a "critical error" notification to admins every time.
2. KB-create flow loses user context.
   - The admin has just selected to create a KB; the next likely step is upload/wiki/settings for that KB.
   - Sending them back to the top-level admin menu adds an unnecessary extra click and hides the created KB id that is already available.

### Implementation direction
1. Add a small transient-error classifier in `frontend/error_handlers.py`.
   - Match known transport/disconnect classes/messages such as:
     - Telegram/HTTPX network errors,
     - `RemoteProtocolError`,
     - "Server disconnected without sending a response",
     - read/connect reset disconnect wording.
   - Keep full logging.
   - Skip admin notifications for classified transient errors.
   - Keep notifications for all other exceptions.
2. Update the KB-create success branch in `frontend/bot_handlers.py`.
   - Use `kb_actions_menu(created_id)` on success.
   - Keep failure branch on `admin_menu()` because there is no created KB to act on.
   - Suggested success copy: keep the existing confirmation text and optionally add a short next-step hint, but no callback or extra fetch is required because the backend response already contains the id.

### Test impact
1. Add focused coverage for the transient-error classifier / admin notification suppression.
   - Best location: a new small test file for `frontend/error_handlers.py`, unless there is an existing handler-focused test module.
2. Tighten the existing KB-create test in `tests/test_bot_text_ai_mode.py`.
   - Assert that the success reply uses `kb_actions_menu(777)` instead of generic `admin_menu()`.
3. Regression priority is high enough to treat this as a production bugfix.
   - Include automated reproduction/verification in the same diff.

### Docs/spec sync expectation
1. `SPEC.md` should reflect:
   - transient transport noise is logged but does not page admins as a critical bot failure,
   - successful admin KB creation lands in the KB actions menu.
2. `docs/REQUIREMENTS_TRACEABILITY.md` should map both behaviors to the new regression coverage.
3. `docs/OPERATIONS.md` should note the transient-error suppression behavior for bot runtime triage.
4. No `docs/design/*` update is planned by default.
   - Reason: this is a small bugfix within existing bot UX/runtime behavior and does not change architecture or system design contracts.

## 2026-03-10 KB search session-scoped KB selection

### Request summary
1. If multiple KBs exist, KB search must explicitly ask which KB to search.
2. The chosen KB must stay active only for the current KB-search session.
3. If the user exits and later re-enters KB search, the search KB must be chosen again.
4. If there is exactly one KB, search should use it automatically.

### Minimal relevant runtime paths
1. `frontend/bot_callbacks.py`
   - callback `search_kb` currently sets `state = "waiting_query"` immediately and never asks for KB choice.
   - callback `kb_select:<id>` already has a branch for `state == "waiting_kb_for_query"` and flushes pending queries into the selected KB.
2. `frontend/bot_handlers.py`
   - `_ensure_kb_or_ask_select(...)` looks only at generic `context.user_data["kb_id"]`.
   - `handle_text(...)` enters KB search from the main keyboard by resetting queue state and then setting `state = "waiting_query"` without selecting/search-scoping a KB.
   - `_reset_kb_query_state(...)` clears queue/session ids but does not clear any search KB key because none exists yet.
3. `tests/test_bot_text_ai_mode.py`
   - already covers queueing with an existing selected KB and stale queue reset on explicit re-entry.
   - does not yet cover:
     - mandatory KB choice when multiple KBs exist,
     - auto-selection when exactly one KB exists,
     - separation between admin-selected `kb_id` and search-session KB selection.

### Root cause
1. The bot has no dedicated search-session KB key.
   - Search reuses the generic `kb_id`, which is also used by admin KB-management flows (upload/wiki/settings/delete).
2. Because of that reuse:
   - a KB selected earlier in admin management silently becomes the search scope,
   - search scope can leak across sessions,
   - re-entering KB search does not force a fresh choice when multiple KBs exist.

### Implementation direction
1. Introduce a dedicated search-session key, e.g. `active_search_kb_id`.
   - Use it only for KB search.
   - Keep generic `kb_id` for admin KB-management actions.
2. Change KB-search entry flow:
   - reset previous search session state,
   - fetch KB list,
   - if none -> show error,
   - if exactly one -> set `active_search_kb_id` and `state = "waiting_query"`,
   - if multiple -> set `state = "waiting_kb_for_query"` and show KB choice menu.
3. Change `_ensure_kb_or_ask_select(...)` and pending-query flush logic to use `active_search_kb_id`.
4. When `kb_select:<id>` is used during `waiting_kb_for_query`, set `active_search_kb_id` instead of relying only on `kb_id`.
5. Make `_reset_kb_query_state(...)` also clear `active_search_kb_id`.

### Test impact
1. Add regressions for:
   - explicit KB choice prompt when multiple KBs exist,
   - automatic selection when only one KB exists,
   - `waiting_query` enqueues against `active_search_kb_id`,
   - re-entering KB search clears the previous search-session KB.
2. Keep admin KB-management behavior untouched in existing tests.

### Docs/spec sync expectation
1. `SPEC.md` should state that KB search uses a session-scoped active KB:
   - multi-KB -> explicit choice,
   - single-KB -> auto-select,
   - re-entry resets the active KB choice.
2. `docs/REQUIREMENTS_TRACEABILITY.md` should map the new flow to bot regressions.
3. `docs/USAGE.md` should document the user-facing KB search flow.

## 2026-03-10 Wiki URL follow-up and open-harmony relevance check

### Live issues reported from Telegram
1. After `kb_wiki_crawl -> waiting_wiki_root`, sending `https://gitee.com/mazurdenis/open-harmony/wikis` did not produce the expected wiki-crawl completion message in the live chat.
2. The next visible bot response was a document-upload report for `open-harmony.zip`, which is a red flag for state contamination or a stale live instance taking a legacy path.
3. The resulting answer for `how to build` was poor:
   - only one source was cited,
   - the cited source label contained a temp-like `tmp...` path,
   - the content was not the best `Sync&Build` guidance expected from the open-harmony wiki corpus.

### What is already true in the repo
1. `frontend/bot_callbacks.py` has a canonical `kb_wiki_crawl` callback that sets `state='waiting_wiki_root'`.
2. `frontend/bot_handlers.py` has a `waiting_wiki_root` text branch and a focused regression for it.
3. Import-log `archive` rows can still be normal for ZIP wiki fallback, so that signal alone is ambiguous.
4. Default KB chunking still uses `mode="full"` for `wiki` and `markdown` in `shared/kb_settings.py`, which is a likely relevance problem for wiki-style docs with many sections.

### Most likely root causes
1. Live bot state contamination around wiki entry:
   - `kb_wiki_crawl` does not currently clear upload-oriented keys such as `kb_id`, `upload_mode`, or pending document payloads.
   - Even if that is not the whole cause, the callback should isolate the wiki flow from document-upload state.
2. Retrieval quality on open-harmony is likely hurt by coarse chunking:
   - wiki/markdown defaults currently keep many pages as one full chunk,
   - the live import log showed many pages with only one fragment each,
   - this makes exact build/how-to retrieval weaker than it should be.

### Verification direction
1. Add bot regressions proving `kb_wiki_crawl` clears stale upload state.
2. Locally ingest the open-harmony wiki corpus into controlled test KBs.
3. Compare a small query set against at least two chunking configurations:
   - current-style `full`,
   - candidate `section`.
4. Use the committed open-harmony-oriented query set in `tests/rag_eval.yaml` plus a few direct `rag_query` answer inspections.
## 2026-03-11 BOTFOLLOW-004 - Gitee public wiki ingest blocker

- Live backend log confirms the remaining root cause for URL-based Gitee wiki ingest:
  - current loader tries to clone `https://gitee.com/<owner>/<repo>.git`;
  - clone fails with `fatal: could not read Username for 'https://gitee.com'`;
  - runtime falls back to HTML crawl and indexes only the wiki root page (`pages=1`, `chunks=4`).
- This means the weak `open-harmony` answers seen in Telegram are downstream of incomplete ingest, not only ranking quality.
- Local-only validation already showed that once the real OpenHarmony wiki corpus is ingested with section chunking, relevance improves materially for build/sync questions.
- Implementation target:
  - switch the Gitee path to try public wiki repo candidates non-interactively before HTML fallback;
  - preserve current HTML fallback as last resort;
  - re-run local-only open-harmony ingest/query comparison after the fix.

## 2026-03-11 BOTFOLLOW-005 - local-only open-harmony wiki smoke harness

- User requested turning the manual open-harmony ingest/query verification into an actual test workflow.
- Constraints stay the same:
  - no hardcoded private/local corpus paths in repo;
  - local-only corpus paths must come from env;
  - committed test must be opt-in and must not run in CI by default.
- The most stable answer check is not live LLM generation but the existing retrieval-only extractive fallback path:
  - ingest the real open-harmony wiki corpus into a temporary local SQLite DB;
  - build the runtime index;
  - call `/rag/query` logic with a forced provider transport error to trigger the deterministic extractive fallback;
  - assert both top sources and extracted answer text for key build/sync queries.
- Runtime findings from implementation:
  - the helper must always run under `.venv\Scripts\python.exe`; system `python` does not have project deps such as `sqlalchemy`,
  - a first version timed out because it did duplicate retrieval plus CPU reranking; the helper is now optimized to disable reranking by default, use a single `rag_query()` pass, and derive `top_sources` from `answer.sources`,
  - repeated direct runs against the same SQLite DB need unique KB names to avoid `knowledge_bases.name` uniqueness failures.
- Current smoke result on local `open-harmony.wiki.zip`:
  - ingest succeeds (`93` files, `768` chunks),
  - both target queries still fail semantically:
    - `how to sync code with local mirror` surfaces `Run HelloWorld v133` / NDK / previewer docs,
    - `how to build and sync` surfaces `Arkoala build and run` / headless tests docs,
  - so the harness is working and is now exposing a real remaining open-harmony ranking/context-quality gap.

## 2026-03-11 BOTFOLLOW-006 - open-harmony build/sync relevance follow-up

- BOTFOLLOW-005 proved the remaining problem is not ingest coverage anymore; the local ZIP smoke ingests `93` files / `768` chunks successfully but the final candidate set for broad procedural queries is still wrong.
- The current route layer already has a generic `_generalized_field_match_score(...)` and HOWTO boosts in `backend/api/routes/rag.py`, but they only help after the right page is already present in `results`.
- That points to a candidate-generation gap, not just a final ranking gap.
- The most promising generic fix is to add a third retrieval channel in `shared/rag_system.py` that scores only structural metadata fields:
  - `source_path`
  - `doc_title`
  - `section_title`
  - `section_path`
- This stays generic:
  - no hardcoded `open-harmony` URLs,
  - no hardcoded `Sync&Build`,
  - no special-case branch by corpus,
  - only general procedural-document signals.
- Expected effect:
  - procedural/how-to pages with strong field matches should enter the fused candidate set more reliably,
  - existing generalized route-level boosts can then promote them to the top for `build/sync/mirror` style questions.

## 2026-03-11 Wiki ingest fail-fast, archive recovery, and admin log aggregation

### User-reported runtime failures
1. URL-based wiki ingest still reports success when no usable wiki corpus was loaded.
   - Example: entering `https://gitee.com/mazurdenis/open-harmony/wikis` yields only one source:
     - `wikis (https://gitee.com/mazurdenis/open-harmony/wikis)`
     - `Тип: web, фрагментов: 4`
   - This means runtime fell back to HTML root-page ingest instead of a full wiki sync.
2. Entering a git-style URL directly (`https://gitee.com/mazurdenis/open-harmony.wiki.git`) also reported success:
   - `Режим синхронизации: HTML crawl`
   - `Обработано страниц: 0`
   - `Добавлено фрагментов: 0`
   - This is a false-success UX bug.
3. The historical product promise for wiki flow included URL + archive recovery.
   - Current bot UX no longer preserves a correct wiki-specific archive recovery path.
   - Users are implicitly pushed toward generic document archive upload, which loses wiki semantics.
4. Admin debugging is weak.
   - Current admin panel shows only KB import-log summaries.
   - There is no aggregated service-log view spanning bot/backend/worker/runtime services.

### Current implementation facts
1. URL flow entry:
   - `frontend/bot_callbacks.py::kb_wiki_crawl` sets `state='waiting_wiki_root'`.
   - `frontend/bot_handlers.py::waiting_wiki_root` calls `backend_client.ingest_wiki_crawl(...)`.
2. Backend wiki ingest:
   - `backend/services/ingestion_service.py::ingest_wiki_crawl(...)`
   - `shared/wiki_scraper.py::crawl_wiki_to_kb(...)`
3. Gitee decision path:
   - prefer `load_wiki_from_git(...)`
   - on any exception, log warning and continue with HTML crawl
4. HTML crawl path is generic and can legitimately be useful for arbitrary docs/wiki sites.
   - But for Gitee `/wikis` it is not a meaningful success path if it yields:
     - `0 pages / 0 chunks`, or
     - effectively only the root page.
5. Archive ingest split:
   - generic archive/document upload goes through `ingest_document(...)`
   - wiki-specific ZIP restore exists in backend client/service (`ingest_wiki_zip`, `load_wiki_from_zip`)
   - but the current bot flow does not drive a clean URL -> archive-recovery interaction when URL/git/html paths fail
6. Existing admin observability:
   - KB import log is available via `/knowledge-bases/{kb_id}/import-log`
   - project logging goes to `data/logs/bot.log` and console/docker logs
   - there is no unified API/UI for reading aggregated service logs from the admin panel

### Product-level gaps now confirmed
1. False success is worse than explicit failure.
   - current wiki ingest flow can silently create a low-quality or empty KB corpus
   - the user then debugs retrieval while the real problem is ingestion incompleteness
2. The wiki flow is missing recovery orchestration.
   - if git requires auth or fails for transport reasons, the bot should explain the reason and guide the next recovery step
   - if HTML crawl is not useful, the bot should not pretend the wiki was loaded
   - if the user has a wiki archive, it should be accepted as `wiki archive restore`, not as a generic document archive
3. A wiki archive restore must preserve wiki semantics.
   - use the original user-entered wiki root
   - restore `wiki_page_url`, `original_url`, stable `doc_title`, `section_path`
   - keep this separate from normal document upload
4. Admin debugging needs a first-class log view.
   - import-log summaries are not enough for transport, auth, clone, crawl, or worker failures

### Design direction
1. Make wiki ingest outcome explicit and gate success on useful corpus creation.
   - `git success` -> success
   - `html success with meaningful page/chunk thresholds` -> success
   - `0 pages / 0 chunks` or root-only Gitee fallback -> failure
2. Preserve wiki-specific recovery context in the bot session.
   - entered wiki URL
   - detected host/provider class
   - last failure reason / stage
   - whether archive fallback is now expected
3. Reintroduce a dedicated wiki archive recovery flow.
   - only available inside the active wiki-ingest session
   - sends archive to `ingest_wiki_zip(...)`
   - uses the previously entered wiki root to restore page links
4. Add explicit admin log access.
   - backend API to return recent service-log slices
   - bot/admin-panel entry to inspect aggregated logs across relevant services
5. Prefer fail-fast for Gitee wiki.
   - HTML fallback may stay as a generic mechanism, but for Gitee `/wikis` it must not be accepted as success when it does not produce a real wiki corpus

## 2026-03-18 Research: container proxy env support + broad HOWTO RAG hardening

Date: 2026-03-18
Agent: codex (team-lead-orchestrator / architect phase)

### User request
- Add proxy support by reading proxy-related environment variables inside the runtime containers.
- Fix the remaining RAG failure where the query `how to build and sync` returns a stitched answer from narrow version-specific pages instead of the canonical build/sync procedure.
- Plan exclusion of temporary agent-generated files from the repository.

### Current proxy/runtime findings
1. There is no explicit project-level proxy contract today.
   - `frontend/bot.py` builds Telegram polling with a plain `ApplicationBuilder().token(...).build()`.
   - `frontend/backend_client.py` creates plain `httpx.Client(...)` instances without explicit proxy wiring.
   - `docker-compose.yml`, `shared/config.py`, and `env.template` do not declare or document proxy variables.
2. The repo already contains operational signals that proxy/network path issues are real.
   - Existing tests and state notes mention `proxy handshake failed` and a previous `NO_PROXY` fix for provider access.
3. Relying only on implicit library behavior is not enough.
   - `httpx` / `requests` may honor standard env variables depending on `trust_env`, but the current repo does not define one project-wide contract, does not document which vars are expected, and does not wire the Telegram client explicitly.

### Current RAG/how-to findings
1. The repo already contains a generalized fix for broad procedural retrieval:
   - metadata-field retrieval channel in `shared/rag_system.py`,
   - generalized field-aware HOWTO scoring in `backend/api/routes/rag.py`,
   - provider fallback expansion over neighboring procedural chunks,
   - local smoke assertions in `tests/test_openharmony_wiki_local_smoke.py`.
2. The live answer supplied by the user shows the remaining failure is now in answer assembly / fallback quality, not raw corpus absence.
   - The answer mixes content from multiple narrow pages (`Stable Build v136`, `Stable Build & Regeneration v135`, previewer pages).
   - The answer includes irrelevant fragments and the postprocessed marker `Команда отсутствует в базе знаний`.
   - This means canonical `Sync&Build` guidance is either not winning the final evidence pack strongly enough, or provider fallback is still allowed to compose from an over-broad candidate set when procedural pages are present.
3. Existing tests cover the target behavior, but the live runtime disproves that the current heuristics are sufficient under the real KB contents.

### Current repo hygiene findings
1. The project uses several agent/workflow directories for coordination and scratch work.
   - `.scratchpad/`
   - `coordination/state/`
   - other coordination artifacts depending on the cycle
2. Some coordination artifacts are intentional project records and should remain versioned.
   - `coordination/tasks.jsonl`
   - approved design docs
   - review reports
3. Temporary agent byproducts need an explicit contract.
   - per-run scratch files, ephemeral notes, local debug dumps, and agent temp outputs should not be committed accidentally.
   - the policy should distinguish between durable workflow artifacts and throwaway runtime debris.

### Design direction
1. Make proxy support explicit and documented.
   - Use standard outbound proxy variables:
     - `HTTP_PROXY`
     - `HTTPS_PROXY`
     - `ALL_PROXY`
     - `NO_PROXY`
   - Propagate them into `bot` and `backend` containers.
   - Read them in config and wire them explicitly into:
     - Telegram HTTP transport,
     - bot -> backend `httpx` client,
     - selected outbound runtime clients that currently use `requests`.
2. Harden broad HOWTO answer selection around coherent procedural anchors.
   - Strengthen the ranking/fallback path so compound procedural queries prefer one canonical procedural document/section family before version-specific feature pages.
   - Keep the fix generic:
     - no hardcoded `Sync&Build`,
     - no corpus-specific page IDs,
     - no special-casing open-harmony.
   - Add deterministic regressions for the exact live symptom:
     - broad `how to build and sync` must surface canonical sync/build steps,
   - final answer/fallback must not stitch together unrelated versioned feature pages when a stronger procedural anchor exists.
3. Add repo hygiene rules for agent-generated temporary files.
   - define which agent/workflow artifacts are durable and must stay tracked;
   - define which scratch/temp/debug artifacts must be gitignored;
   - update repo ignore rules and docs accordingly.

## 2026-03-18 Research: generalized procedural retrieval + multi-corpus validation

Date: 2026-03-18
Agent: codex (team-lead-orchestrator / architect phase)

### User request
- Remove remaining literal/query-specific procedural boosts such as explicit `Sync&Build` preference and make the retrieval path more universal.
- Explain how a realistic generalized solution should work when:
  - the language changes,
  - wording changes,
  - section naming differs across corpora.
- Extend verification beyond one wiki and add other corpora, specifically:
  - `https://gitee.com/rri_opensource/arkuiwiki.wiki.git`
  - `https://gitee.com/mazurdenis/open-harmony.wiki.git`
- Add tests that can use temporary local-only setup and the existing Ollama/Open WebUI/runtime API paths already present in the repo.

### Current local validation assets already in repo
1. There is already a developer-local corpus smoke harness:
   - `scripts/openharmony_wiki_local_smoke.py`
   - `tests/test_openharmony_wiki_local_smoke.py`
   - current mode: local ZIP/git corpus -> temporary SQLite KB -> `/rag/query` path -> deterministic extractive fallback or optional live LLM answer lane.
2. There is already a generalized evaluation service:
   - `backend/services/rag_eval_service.py`
   - supports retrieval metrics, source-family slices, optional answer/judge metrics, and provider selection through existing `ollama` / `open_webui` config.
3. There is already a committed source-manifest concept:
   - `tests/data/rag_eval_source_manifest_v1.yaml`
   - supports repo-safe fixtures and local-only env-resolved fixtures.
4. There is already an API smoke path:
   - `scripts/rag_api_smoke_test.py`
   - can be extended for optional live-backend validation against a running local/server backend.

### Why the current broad-HOWTO fix is still not general enough
1. It is generic relative to one corpus, but it still lives in the "procedural query heuristics" layer.
2. It still depends too much on surface tokens such as:
   - `how to`
   - `build`
   - `sync`
   - `guide`
3. That means it will drift again when:
   - a corpus uses different wording (`prepare`, `assemble`, `deploy`, `bootstrap`, `initialize`),
   - a corpus is in another language,
   - a corpus uses structure without explicit imperative titles.

### Realistic generalized retrieval direction
1. The ranking target should shift from "match these words" to "retrieve one coherent procedural evidence family".
2. A procedural evidence family should be inferred from mostly language-agnostic signals:
   - same document / section-path cohesion,
   - ordered neighbor chunks that look like a procedure sequence,
   - chunk transitions that preserve a step-by-step narrative,
   - high density of commands, numbered steps, shell/code blocks, or action-result pairs,
   - low entropy of topic drift across the selected evidence pack.
3. Query understanding should remain lightweight and broad:
   - classify whether the user likely wants a procedure vs definition vs factoid,
   - but avoid relying on one literal title or one corpus-specific path.
4. Final context assembly should optimize for:
   - family coherence,
   - coverage of required steps,
   - low contamination from troubleshooting or adjacent version-specific pages.

### Concrete architecture implication
1. Retrieval should produce candidate chunks plus family-level aggregates.
2. Reranking should score both:
   - row relevance,
   - family coherence / step coverage.
3. Context packing should select the best family first, then expand within that family, instead of mixing top rows from many documents.
4. Fallback answering should inherit the same family boundary.
   - If the provider is unavailable, extractive fallback must summarize one family rather than stitching across arbitrary top hits.

### Multi-corpus validation direction
1. We should stop validating generalized behavior only on one `open-harmony` wiki.
2. Add at least two local-only wiki corpora to the manifest:
   - `open-harmony.wiki.git`
   - `arkuiwiki.wiki.git`
3. Keep them local-only / env-driven:
   - no cloning or network during fast tests,
   - no committed raw corpus dump,
   - tests use temp DBs and optional local cache/zip/git mirrors.
4. Split verification into three lanes:
   - deterministic fast unit/integration lane with synthetic fixtures and family-selection assertions,
   - local slow smoke lane against env-provided corpora using temp SQLite,
   - optional live runtime lane against a running backend and current provider config (`ollama` / `open_webui`).

### Source acquisition contract
1. The repo should not fetch these corpora inside normal committed tests.
2. Instead, add manifest entries and env vars for local preparation:
   - `RAG_EVAL_LOCAL_OPENHARMONY_WIKI_PATH`
   - `RAG_EVAL_LOCAL_ARKUIWIKI_PATH`
   - optionally `RAG_LIVE_SMOKE_BASE_URL`, `RAG_LIVE_SMOKE_KB_ID`, `RAG_LIVE_SMOKE_API_KEY`
3. The user can prepare corpora by local clone / zip outside CI; tests then consume only the local prepared paths.

### Answer-lane realism
1. For fast regressions, deterministic extractive fallback remains the stable baseline.
2. For realistic end-to-end checks, optional answer mode should call the already wired provider layer:
   - `ollama`
   - `open_webui`
3. This should reuse current project config instead of introducing a separate ad-hoc client contract.

### Key design constraint
The generalized solution must not become "keyword soup in more languages". It must prefer structural evidence-family selection, with language/term cues used only as weak hints rather than the dominant ranking rule.

## 2026-03-18 Research: service-level RAG architecture plan

Date: 2026-03-18
Agent: codex (team-lead-orchestrator / architect phase)

### User request
- Stop tuning RAG around one query.
- Define the correct architecture for the whole service so arbitrary uploaded documents, wikis, instructions, and mixed corpora are handled reliably.
- Produce:
  - an architectural plan for the full RAG service,
  - a staged design for each pipeline phase,
  - then proceed to implementation after planning.

### What already exists in the repo
1. Ingestion coverage is broad:
   - document/web/wiki/code/chat/image paths already exist in `backend/services/ingestion_service.py`.
2. Canonical chunk metadata has already been introduced:
   - chunk hashes,
   - chunk numbers,
   - block type,
   - section normalization,
   - parser profile,
   - source-path normalization.
3. Retrieval is already hybrid, but still partially fragmented:
   - dense + BM25 + metadata-field rescue,
   - route-level logic in `backend/api/routes/rag.py`,
   - retrieval-core logic in `shared/rag_system.py`,
   - diagnostics/eval infrastructure already exists but is not yet the main driver of architecture decisions.
4. There are already several good design docs:
   - `docs/design/rag-generalized-architecture-v2.md`
   - `docs/design/rag-near-ideal-task-breakdown-v1.md`
   - `docs/design/rag-embedded-quality-eval-system-v1.md`
   - but they are focused on specific cycles and not presented as one unified service architecture + pipeline-stage design package.

### Core architectural problem
The service still behaves too much like:
- ingest chunks,
- rank chunks,
- try to save answer quality with route-level heuristics.

That is not sufficient for "arbitrary documents always find the right material".

The more correct service-level problem statement is:
1. ingest arbitrary source types into a canonical document model,
2. preserve structure and relationships,
3. maximize recall of relevant material,
4. aggregate/rank at document/section family level,
5. compose bounded evidence packs,
6. generate grounded answers with refusal/safety behavior,
7. measure all of the above continuously.

### Realistic architecture direction
The RAG service should be modeled as an explicit pipeline with stable contracts between stages:
1. Source acquisition and ingest orchestration
2. Parse and canonicalize
3. Chunk and structure graph build
4. Index write and consistency
5. Query understanding
6. Candidate generation
7. Candidate fusion and rerank
8. Document/section family aggregation
9. Context/evidence-pack composition
10. Answer generation and safety/grounding
11. Diagnostics and evaluation

### Why this is the right abstraction
1. It generalizes across corpora because it reasons in pipeline responsibilities, not one query symptom.
2. It separates "find everything relevant" from "compose one answer".
3. It makes it possible to improve recall, precision, context quality, and answer quality independently.
4. It gives a clean roadmap for implementation slices and rollback boundaries.

### Important design principle
For general-purpose document retrieval, the service should optimize for:
- high recall first,
- then coherent aggregation,
- then answer synthesis.

If retrieval misses the right document family, no answer prompt can fix it reliably.

### Design implication for next cycle
The next design artifact should not be another procedural-query document. It should be a master service architecture for RAG with:
- stage-by-stage contracts,
- stage-specific failure modes,
- stage-specific tests,
- staged implementation slices.

## 2026-03-19 Analysis: current RAG quality on local OpenHarmony + ArkUI corpora

### Scope of this validation pass
- Re-read the current service-level RAG design and recent review artifacts.
- Inspect the real local corpora:
  - `open-harmony/`
  - `C:\Users\devl\proj\wiki_refactoring\arkuiwiki.wiki`
- Build a grounded evaluation set with 26 questions:
  - procedural,
  - definition,
  - navigation,
  - troubleshooting,
  - infrastructure/setup.
- Run the current local-only smoke harness in extractive mode against both corpora.
- Run the current focused pytest suites for smoke/eval/generalized retrieval behavior.

### Current architecture state
- The project is no longer at the old `docs/RAG_IMPLEMENTATION_STATUS.md` stage.
- The current relevant stack already includes:
  - family-aware candidate ordering in `shared/rag_system.py`,
  - family-aware route/context/fallback ordering in `backend/api/routes/rag.py`,
  - additive diagnostics with family metadata,
  - generalized local wiki smoke tooling,
  - public-safe multicorpus eval fixtures,
  - green regression suites for those committed behaviors.
- The latest independent review still identifies one important residual weakness:
  - route-level procedural matching retains a fixed EN/RU action vocabulary and therefore remains language-tuned.

### Corpus health
- OpenHarmony ingest:
  - `files_processed=88`
  - `chunks_added=723`
  - embedding coverage `100%`
- ArkUI wiki ingest:
  - `files_processed=108`
  - `chunks_added=856`
  - embedding coverage `100%`
- Conclusion:
  - ingestion and embedding are healthy on both corpora,
  - the observed misses are retrieval/ranking/context-selection misses, not corpus-loading failures.

### Local smoke outcome

#### OpenHarmony
- Case set size: `15`
- Source-hit failures: `7`
- Typical failure shapes:
  - canonical procedural page lost to adjacent procedural/history pages:
    - `how to build ohos sdk for windows only`
    - `how to build rk3568`
  - authoritative conceptual page lost to status/noise-heavy page:
    - `what is c-api`
    - `where are UI interfaces and non-ui interfaces placed in c-api`
    - `DEV_API_STATUS` competes too aggressively with `C-API Overview`
  - infrastructure/build-system queries drift into semantically related but wrong families:
    - `how new developer join feature branch using manifests`
    - `what are the main build framework stages`
    - `how to pass gn variable enable_cxx`
  - NDK compilation query drifts back into a broad sync/build page:
    - `how to compile third party library with openharmony ndk`

#### ArkUI wiki
- Case set size: `12`
- Source-hit failures: `7`
- Typical failure shapes:
  - distinctive patch/fix pages lose to a broad previewer note:
    - `what patch should i apply for master branch linux previewer`
    - `how to fix previewer white screen`
    - `Built-in Previewer` outranks the exact patch/fix docs
  - navigation query for official API reference drifts into noisy dev-status content:
    - `where is arkui api reference`
  - infrastructure/setup queries partially route to historical or adjacent build pages:
    - `what host setup is recommended for development`
    - `how to install repo tool on ubuntu`
  - one case (`how to build arkui application on linux server`) surfaced the right document, but the expected-fragment assertion still failed because the loader encodes parentheses in the source URL (`%28Linux%29`) while the scratch expectation used raw parentheses.

### Test-vs-live gap
- Focused pytest suites remained green:
  - smoke/eval/tooling lane:
    - `27 passed, 2 skipped`
  - retrieval/generalization/diagnostics lane:
    - `41 passed`
- This means:
  - existing tests correctly protect the already implemented generalization slices,
  - but the live local corpora still expose real misses outside the current synthetic/public fixture coverage.

### Additional tooling issue discovered
- Both smoke commands ended with a Windows temp-file cleanup `PermissionError` on the temporary `smoke.db`.
- The JSON result files were successfully written before the exception, so the primary signal is still usable.
- This is a harness reliability problem, not a retrieval-quality problem, but it should be cleaned up because it turns informative smoke failures into noisy command failures.

### Main architectural conclusions from this pass
1. Candidate-family support helped broad procedural queries, but it is still too weak for:
   - exact conceptual docs vs noisy status tables,
   - exact patch/fix notes vs broad neighboring previewer notes,
   - infra/setup/navigation intents where lexical overlap is broad and many sibling pages look similar.
2. The system still lacks a stronger notion of document role/type:
   - overview/reference/status/troubleshooting/howto/setup/changelog.
3. The eval suite is still too narrow for the real failure surface:
   - live corpora expose misses on definition, setup, patch-note, and navigation queries.
4. Some route-level intent and fallback logic is still over-reliant on lexical hints rather than canonical document-role evidence.

### Recommended next improvements
1. Add document-role classification at ingest time and persist it in canonical metadata.
   - Examples:
     - `howto`
     - `reference`
     - `overview`
     - `status`
     - `troubleshooting`
     - `setup`
     - `release_note`
   - Use role priors during family aggregation and context packing.
2. Add negative priors / contamination penalties for noisy families.
   - `DEV_API_STATUS` and similar table-heavy status pages should not outrank conceptual overview pages for definition/navigation queries unless the query explicitly asks for status.
3. Promote exact title/heading/section evidence into first-class retrieval signals.
   - Not via hardcoded page names,
   - but via generic features:
     - title exactness,
     - heading exactness,
     - section-path match specificity,
     - query-to-title coverage ratio.
4. Split “broad HOWTO” logic from “exact lookup” logic more explicitly.
   - The current family-first procedural path works for broad tasks,
   - but exact doc lookup queries need stronger support for one authoritative page or section.
5. Expand committed multicorpus eval coverage with the newly observed local misses.
   - Add cases for:
     - `where are ui interfaces and non-ui interfaces placed in c-api`
     - `how to pass gn variable enable_cxx`
     - `what patch should i apply for master branch linux previewer`
     - `how to fix previewer white screen`
     - `where is arkui api reference`
     - `how to install repo tool on ubuntu`
6. Harden the local smoke harness cleanup on Windows so it closes the SQLite handle before temp-dir deletion.

### 2026-03-19 Retrieval hardening iteration update
- Implemented the next generalized slice without adding document-role labels:
  - exact structural specificity now influences fused ordering before rerank truncation,
  - the structural lexical channel now reads early content anchors, not only `doc_title/section_title/section_path/source_path`,
  - route-level context selection follows the same exactness signal.
- Why this remains universal:
  - no corpus-name hardcodes were added,
  - no language-specific document taxonomy was introduced,
  - all new signals derive from generic chunk metadata plus chunk text already available in any corpus.
- Result:
  - OpenHarmony improved from `7/15` to `8/15` source-hit pass,
  - ArkUI retained the exact previewer build rescue but still sits at `6/12`,
  - the remaining misses point less to "missing metadata labels" and more to two systemic gaps:
    - contamination from giant status/archive pages,
    - insufficient exact-lookup handling for navigation/reference/setup intent.
- Revised architectural conclusion:
  - adding manual document-role metadata is not the first mandatory step,
  - the next universal slice should prefer generic contamination penalties and exact-lookup routing over hand-labeled document types.
