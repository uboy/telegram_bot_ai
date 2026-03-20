# RAG Answer Quality Evaluation v1

Date: 2026-03-19
Status: APPROVED:v1
Task: `RAGEVAL-001`

## 1. Summary

### Problem statement

All current evaluation is extractive: it measures whether the correct source
document appeared in top-k results. This answers "did retrieval find the right
document?" but tells us nothing about answer quality:

- Is the answer faithful to the retrieved context, or hallucinated?
- Is the answer relevant to what the user asked?
- Is the answer complete, or does it cut off important steps?
- Did a retrieval improvement actually improve the answer, or just change
  which source was cited?

Without an answer-quality signal, regression suites can stay green while
answer quality silently degrades — or vice versa: retrieval improvements
look neutral in extractive eval while significantly improving the actual answer.

Additionally there is no user-side signal. Users have no mechanism to tell the
system whether an answer was useful. This means systematic failures in specific
KB areas are invisible until reported manually.

### Goals

- Add an automated local-only LLM-as-judge metric for offline evaluation runs.
- Add a lightweight user feedback mechanism in the bot (thumbs up/down).
- Persist both signals in the existing eval/diagnostics tables so quality
  trends are observable over time.
- Keep online user feedback non-blocking and best-effort.
- Keep LLM-as-judge offline only; no production latency increase.

### Non-goals

- No real-time answer filtering or correction based on judge score.
- No public leaderboard or external eval harness.
- No ground-truth answer annotation at this stage.
- No changes to the retrieval or generation pipeline in this slice.

## 2. Scope Boundaries

### In scope

- LLM-as-judge scoring: faithfulness, relevance, completeness.
- User feedback API endpoint and bot UX (inline keyboard after answer).
- Persistence of judge scores and user votes in existing tables.
- Local evaluation runner integration.
- Reporting: per-case judge scores in eval output.

### Out of scope

- Ground-truth answer creation.
- Automated PR gates on judge scores (requires stable baseline first).
- Answer correction/filtering based on judge score.
- A/B testing infrastructure.

## 3. Assumptions and Constraints

- LLM-as-judge runs offline, using the same Ollama/OpenAI provider as answer
  generation; no separate judge model deployment.
- Judge calls are rate-limited and best-effort in eval runs; failures are
  skipped with a warning.
- User feedback is persisted in a new `rag_answer_feedback` table; existing
  `rag_eval_results` gains an optional `judge_scores` JSON column.
- Judge prompts must work for both Russian and English answers.
- The judge score is a soft signal; it does not block retrieval or generation.

## 4. Architecture

### 4.1 LLM-as-judge scoring

**Three metrics, scored 1–5:**

| Metric | Definition |
|---|---|
| `faithfulness` | Is every claim in the answer supported by the retrieved context? (1 = major hallucination, 5 = fully grounded) |
| `relevance` | Does the answer address what the user asked? (1 = off-topic, 5 = directly answers the question) |
| `completeness` | Does the answer cover the key steps/points needed? (1 = critically incomplete, 5 = covers all key points) |

**Judge prompt (single call per answer, returns JSON):**

The judge receives the same evidence pack that was passed to the LLM for
answer generation (not an arbitrary top-3 slice). This avoids penalizing
answers that are faithful to a chunk outside the judge's truncated window.
The evidence pack is truncated to 2000 characters total (sum across all
chunks) to fit within a compact judge call.

If the answer is a system refusal ("Информация не найдена" / "I don't have
information about that"), the judge should score faithfulness=5 and
completeness=N/A (mark as intentional_refusal=true in output). The prompt
instructs the judge to detect this case.

Note on self-evaluation bias: using the same LLM model that generated the
answer to judge it introduces known self-consistency bias (the model tends
to rate its own output favorably). When a different model is available
(e.g., smaller/cheaper), prefer it for judging. This is a known limitation
of the single-model deployment scenario; it should be acknowledged in eval
run reports.

```
You are an evaluator for a RAG system. Score the answer on three dimensions.

QUESTION: {query}

RETRIEVED CONTEXT (the exact context given to the answer generator):
{evidence_pack_text}  ← truncated to 2000 chars total

ANSWER:
{answer}

If the answer is an intentional refusal (e.g. "I don't have information"),
set intentional_refusal=true and skip completeness scoring.

Score each dimension from 1 to 5:
- faithfulness: is the answer fully supported by the context?
- relevance: does the answer address the question?
- completeness: does the answer cover the key points?

Respond with valid JSON only:
{"faithfulness": <int>, "relevance": <int>, "completeness": <int>, "reasoning": "<one sentence>"}
```

The eval runner calls the judge after each case and stores scores alongside
the extractive source-hit result.

### 4.2 User feedback mechanism

**Bot UX:**
- After every KB-query answer, the bot sends an inline keyboard:
  ```
  [ 👍 Полезно ]  [ 👎 Не то ]
  ```
- Pressing either button sends a callback, ACKs the user ("Спасибо!"), and
  persists the vote.
- The keyboard disappears after a vote or after 10 minutes.

**API endpoint:** `POST /api/v1/rag/feedback`

```json
{
  "request_id": "uuid",
  "vote": "helpful" | "not_helpful",
  "comment": "optional free text"
}
```

Response: `{"ok": true}` — no blocking DB errors surfaced to user.

**New DB table `rag_answer_feedback`:**

```sql
CREATE TABLE rag_answer_feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id  TEXT NOT NULL,   -- FK to retrieval_query_logs.request_id
    user_id     INTEGER,
    kb_id       INTEGER,
    vote        TEXT NOT NULL,   -- "helpful" | "not_helpful"
    comment     TEXT,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (request_id, user_id)  -- prevent duplicate votes per user per answer
);
CREATE INDEX idx_rag_answer_feedback_request_id ON rag_answer_feedback(request_id);
CREATE INDEX idx_rag_answer_feedback_kb_id      ON rag_answer_feedback(kb_id);
```

### 4.3 Eval runner integration

`rag_eval_service.py` gains a `run_with_judge` option:

```python
result = rag_eval_service.run(
    suite_name="openharmony_howto",
    run_with_judge=True,   # triggers LLM-as-judge per case
)
```

Each `RagEvalResultRow` gains optional fields:
- `judge_faithfulness: Optional[float]`
- `judge_relevance: Optional[float]`
- `judge_completeness: Optional[float]`
- `judge_reasoning: Optional[str]`
- `judge_skipped: bool`  (True if judge call failed)

### 4.4 Quality gate extension

The existing statistical gate script (`scripts/eval_quality_gate.py`) gains
an optional `--judge-threshold` flag:

```
--judge-threshold faithfulness=3.5,relevance=4.0
```

If specified and judge scores are present, the gate fails if mean score for
any metric is below threshold. This is off by default until a stable baseline
is established.

## 5. Interfaces and Contracts

### API

New endpoint: `POST /api/v1/rag/feedback`
Schema: `RAGFeedbackRequest { request_id, vote, comment? }`
Response: `RAGFeedbackResponse { ok: bool }`
Auth: same `X-API-Key` header as other RAG endpoints.

### Bot

New callback: `rag_feedback:{request_id}:{vote}` (inline keyboard button).

### Eval runner

New flag: `run_with_judge: bool = False` on `rag_eval_service.run()`.
New output fields on `RagEvalResultRow` (all optional, nullable).

## 6. Rollout and Evaluation

### Phase 1 — judge offline only
- Add judge scoring to local eval runner.
- Run one baseline eval to establish current mean faithfulness/relevance/
  completeness scores.
- No gates yet; scores are for visibility.

### Phase 2 — user feedback collection
- Enable thumbs feedback in bot for KB-query answers.
- Collect votes for 2 weeks; analyze per-KB vote distributions.

### Phase 3 — gate and monitor
- Set judge thresholds in quality gate after baseline is stable (±10% variance
  across 3 runs).
- Publish per-KB helpfulness rates in operational dashboards.

## 7. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Judge prompt biased toward longer/verbose answers | Score faithfulness separately from completeness; use reasoning field to audit |
| Judge model hallucination in scoring | Judge prompt returns JSON with reasoning; manual spot-check 10% of scored cases per run |
| User feedback skew (only unhappy users vote) | Report vote rate alongside helpfulness ratio; low-rate KBs are flagged separately |
| `request_id` not surfaced to bot user | Bot stores `request_id` from RAG response in user session for the feedback callback |
| Feedback table grows unbounded | Add retention policy (same service as outbox retention) — keep 90 days |

## 8. Acceptance Criteria

- `/api/v1/rag/feedback` endpoint accepts `helpful`/`not_helpful` votes and
  returns `{"ok": true}` without blocking on DB errors.
- `rag_answer_feedback` table is created by migration with correct columns.
- Bot shows thumbs keyboard after each KB-query answer.
- Eval runner with `run_with_judge=True` calls judge and stores scores.
- Judge failure (timeout, parse error) sets `judge_skipped=True` and does not
  abort the eval run.
- `--judge-threshold faithfulness=3.0` causes quality gate to fail when mean
  faithfulness is below threshold.
- `python -m py_compile` passes on all new/modified files.
- All existing eval and retrieval regression tests remain green.

## 9. Pipeline Stage Mapping

| Stage | Where this feature hooks in |
|---|---|
| Stage 0 — pre-retrieval | No change |
| Stage 6 — answer generation | Judge reads the same evidence pack produced here |
| Stage 7 — diagnostics | `judge_scores` written to `rag_eval_results.judge_scores` JSON column |
| Stage 8 — user response | Bot attaches inline keyboard after answer is sent |
| Offline eval runner | `run_with_judge=True` fires judge after each eval case |

## 10. Dependencies and Interactions

- **RAGPERF-001**: Semantic cache stores retrieval candidates only; answers are not cached. Judge still runs after LLM generation even on a cache hit.
- **RAGCONV-001**: Reformulated query text is passed to judge as the `query` field; original query is stored in diagnostics only.
- **RAGIDX-001**: No interaction. Judge operates on generated answers, not on the index.
- **Existing `rag_eval_service.py`**: Gains `run_with_judge` flag; all existing call sites default to `False` — backward compatible.

## 11. Secret-Safety Impact

- Judge calls use the same LLM provider credentials already present in `.env` (`OPENAI_API_KEY` / Ollama local).
- No new secrets introduced.
- `rag_answer_feedback.comment` may contain user PII — apply same 90-day retention as other user content.

## 12. Spec and Doc Update Plan

- `SPEC.md`: add requirement for `/api/v1/rag/feedback` endpoint and `rag_answer_feedback` table.
- `docs/REQUIREMENTS_TRACEABILITY.md`: add ACs from Section 8.
- `docs/API.md` (if present): add `POST /api/v1/rag/feedback` endpoint docs.

## 13. Governance Review Gate

- Anti-hardcode: no corpus-specific logic. Judge prompt is generic (works for any KB, any language). ✓
- No corpus-specific page-name logic in feedback persistence. ✓
- Approved by: review agent 2026-03-19 (PASS-WITH-CONDITIONS, conditions applied).
