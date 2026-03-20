# RAG Conversation-Aware Retrieval v1

Date: 2026-03-19
Status: APPROVED:v1
Task: `RAGCONV-001`

## 1. Summary

### Problem statement

Every RAG query is currently treated as independent. When a user asks a
follow-up question in the same conversation — "how do I do that on Windows
instead?", "what about the SDK version?", "and for ARM?" — the system has no
access to what "that", "the SDK", or "ARM" refers to. The retrieved context
is determined by the follow-up string alone, which is often too short or too
vague to retrieve the right document.

This means compound conversational flows always degrade: each follow-up is
either under-specified (too few retrieval signals) or forces the user to
re-state the full question.

### Goals

- Enable single-turn follow-up queries to resolve against the correct document
  family from the prior turn without any user effort.
- Keep the mechanism additive: existing single-turn retrieval is unchanged
  when there is no conversation history.
- Surface conversation context usage in diagnostics so the behavior is
  transparent and verifiable.
- Stay within the existing retrieval pipeline stages; no new external services.

### Non-goals

- No multi-turn answer synthesis (the LLM prompt change is out of scope here).
- No persistent cross-session memory — context window only.
- No intent-classification or slot-filling; reformulation is query-level only.
- No changes to the Telegram bot UX in this slice.

## 2. Scope Boundaries

### In scope

- Query reformulation using prior-turn context before retrieval.
- Conversation history storage as a lightweight in-request structure.
- RAG API schema extension (`conversation_context` input field).
- Diagnostics exposure of whether reformulation was applied.
- Fallback to original query when history is absent or empty.

### Out of scope

- Persistent conversation DB table (already exists as `ai_conversations`).
- Multi-turn grounded answer synthesis.
- UI changes.
- Memory across sessions.

## 3. Assumptions and Constraints

- Conversation history is supplied by the caller (bot) per-request; the RAG
  service does not maintain session state internally.
- Reformulation uses the same LLM that generates answers; no separate model.
- Reformulation adds ~1 LLM call latency for follow-up turns; single-turn
  queries must not be affected.
- The reformulated query replaces the original for retrieval but the original
  is also logged in diagnostics.
- History depth: last 3 turns is sufficient; deeper history adds noise.

## 4. Architecture

### 4.1 Conversation context structure

The caller supplies an optional list of prior turns:

```
conversation_context: [
  { role: "user",      text: "how to sync and build openharmony" },
  { role: "assistant", text: "Run repo init ... repo sync -c -j 8 ..." },
  { role: "user",      text: "what about for windows only?" }   ← current query
]
```

The current query is the last `user` entry. Prior turns are context only.

### 4.2 Query reformulation

When `conversation_context` has ≥ 2 turns (at least one prior exchange):

1. Detect if the current query is a follow-up:
   - short query (< 6 non-stopword tokens), OR
   - contains a pronoun/demonstrative referring to prior context.
     English: "that", "this", "it", "those", "the same", "another",
     "instead", "there", "which", "what about", "how about".
     Russian: "тот", "та", "то", "те", "это", "этот", "эта",
     "эти", "там", "туда", "же", "тоже", "также", "вместо",
     "другой", "другая", "другое", "другие", "ещё", "еще",
     "а что насчет", "а как насчет", "а если".
2. If follow-up detected: call the LLM with a compact reformulation prompt:
   ```
   Given the conversation:
   [last 2 prior turns, truncated to 400 tokens]
   Rewrite only the last user question as a self-contained search query.
   Output only the rewritten query, no explanation.
   ```
3. Use the reformulated query for all retrieval channels (dense + BM25 +
   metadata field search).
4. Keep the original query for the LLM answer generation prompt.

When the query is not a follow-up, skip reformulation entirely.

### 4.3 Retrieval pipeline integration

Insert reformulation as a pre-retrieval step in `rag_query`:

```
[existing multi-query rewrite] → [conversation reformulation if follow-up]
→ [dense + BM25 + metadata retrieval]
```

The reformulated query is also injected into the multi-query rewrite set so
reformulation and multi-query work together, not separately.

### 4.4 Diagnostics

Add to `hints_json` in `retrieval_query_logs`:
- `conv_reformulation_applied: bool`
- `conv_original_query: str` (if reformulation was applied)
- `conv_turns_used: int`

### 4.5 API schema change

`RAGQuery` gains an optional field:

```python
conversation_context: Optional[List[ConversationTurn]] = None
```

`ConversationTurn`:
```python
class ConversationTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str
```

Max 6 turns accepted; older turns silently truncated.

## 5. Interfaces and Contracts

### Input contract

- `conversation_context` is optional; absence is equivalent to single-turn.
- Turns are ordered oldest-first, current query last.
- Text is raw user/assistant message; the service strips HTML/markdown before
  using it in the reformulation prompt.

### Output contract

- `answer`, `sources`, `request_id` — unchanged from current schema.
- `diagnostics.hints` gains the three new fields listed in 4.4.

### Fallback contract

If the reformulation LLM call fails or times out:
- log warning, use original query, set `conv_reformulation_applied: false`.
- Never block retrieval because of a failed reformulation.

## 6. Rollout and Evaluation

### Phase 1 — opt-in per request
- Bot passes `conversation_context` for KB-search flows; other flows remain
  single-turn.
- Evaluate with local smoke cases: design follow-up pairs where the original
  query fails but the reformulated query retrieves the correct source.

### Phase 2 — measure
- Compare source-hit rate for follow-up queries with vs without reformulation
  using the multicorpus smoke harness.
- Gate: reformulated queries must hit correct source ≥ 10% more often than
  original follow-up queries.

## 7. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Reformulation introduces wrong context from prior turn | Fallback to original if reformulated query returns 0 candidates above min_rerank_score |
| Adds LLM latency on every follow-up | Only fires when follow-up is detected (short or pronoun-bearing); typical single-turn unaffected |
| Reformulation "corrects" a valid short query | Detection threshold (< 6 non-stopwords) is conservative; border cases fall through to normal retrieval |
| History privacy — prior turns reach RAG service | Turns are already in the same conversation; no new cross-user exposure |

## 8. Acceptance Criteria

- `conversation_context` field accepted by `/api/v1/rag/query` with no schema error.
- Follow-up query "what about for windows only?" after "how to sync and build"
  retrieves the Sync&Build page (source-hit), while without conversation context
  it does not.
- Reformulation is not triggered for a query with ≥ 6 non-stopword tokens and
  no pronouns.
- `conv_reformulation_applied` appears in diagnostics hints.
- If reformulation LLM call raises an exception, retrieval completes using the
  original query.
- `python -m py_compile` passes on all modified files.
- Existing single-turn regression suite remains fully green.

## 9. Pipeline Stage Mapping

| Stage | Where this feature hooks in |
|---|---|
| Stage 0 — query receipt | Accept `conversation_context` in `RAGQuery` schema |
| Stage 1 — pre-retrieval | Follow-up detection → LLM reformulation → reformulated query replaces original for retrieval |
| Stage 2–4 — retrieval channels | Reformulated query used for dense + BM25 + metadata field search |
| Stage 5 — multi-query rewrite | Reformulated query injected into multi-query rewrite set |
| Stage 7 — diagnostics | `conv_reformulation_applied`, `conv_original_query`, `conv_turns_used` written to `hints_json` |

## 10. Dependencies and Interactions

- **RAGPERF-001**: Reformulated query text (not original) is used as the semantic cache key. Cache key = `SHA-256(kb_id || normalized_reformulated_query)`.
- **RAGEVAL-001**: Judge receives the reformulated query as `QUESTION` field; `conv_original_query` is available in diagnostics for manual review.
- **RAGIDX-001**: No interaction.
- **Multicorpus smoke harness**: add `conversation_context` field to smoke case format for follow-up test cases; `query_mode: "follow_up"` signals harness to supply context.

## 11. Secret-Safety Impact

- `conversation_context.text` fields contain user message text — same privacy classification as `retrieval_query_logs.query_text` (already retained 90 days).
- No new credentials introduced.

## 12. Spec and Doc Update Plan

- `SPEC.md`: add `conversation_context` field to `/api/v1/rag/query` schema.
- `backend/schemas/rag.py`: add `ConversationTurn` and `conversation_context: Optional[List[ConversationTurn]]` to `RAGQuery`.
- `docs/REQUIREMENTS_TRACEABILITY.md`: add ACs from Section 8.

## 13. Governance Review Gate

- Anti-hardcode: follow-up detection uses generic pronoun/token-count heuristics, not corpus-specific terminology. ✓
- Reformulation prompt is corpus-agnostic. ✓
- Approved by: review agent 2026-03-19 (PASS-WITH-CONDITIONS, conditions applied).
