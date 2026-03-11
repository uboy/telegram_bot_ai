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
