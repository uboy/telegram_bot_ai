# Feature Design Spec: RAG Summary Modes (v1)

## 1) Summary
**Problem statement**: Users need concise outputs over knowledge-base context in different formats (summary/FAQ/instructions), including optional date filtering.

**Goals**
- Provide `summary`, `faq`, and `instructions` modes via one API.
- Support `date_from` / `date_to` filtering.
- Reuse existing RAG retrieval and safety post-processing.

**Non-goals**
- Independent summarization index.
- Multi-stage planner/critic pipelines.

## 2) Scope boundaries
**In-scope**
- `POST /api/v1/rag/summary`.
- Mode-specific prompt templates.
- Date filtering on search results metadata.

**Out-of-scope**
- Streaming output.
- Rich citation rendering in this endpoint.

## 3) Assumptions + constraints
- No new dependencies.
- Existing `rag_system.search` and `ai_manager.query` are reused.
- API key auth is mandatory on `/rag/*`.

## 4) Architecture
**Components**
- `backend/api/routes/rag.py` (`rag_summary`).
- `backend/schemas/rag.py` (`RAGSummaryQuery`, `RAGSummaryAnswer`).
- `shared/rag_safety.py` post-processing.

**Data flow**
1. Receive summary request with mode and optional date filters.
2. Retrieve top-k chunks from KB.
3. Apply date filters.
4. Build mode-specific prompt and query LLM.
5. Apply safety filters, return answer + source list.

## 5) Interfaces / contracts
**Public API**
- `POST /api/v1/rag/summary`
  - request: `{query, knowledge_base_id, mode, top_k, date_from, date_to}`
  - response: `{answer, sources[]}`

**Internal boundaries**
- `_passes_date(item) -> bool`
- mode switch: `summary|faq|instructions`

**Error handling**
- Empty query -> `400`.
- No results after filtering -> empty answer/sources.

## 6) Data model changes + migrations
- None.

## 7) Edge cases + failure modes
- Invalid date formats ignored by parser fallback.
- No metadata dates in chunks -> chunk is not rejected solely by missing date.

## 8) Security requirements
- API key required.
- Output passes URL/citation/command safety filters.

## 9) Performance requirements
- Use bounded `top_k` and truncated chunk context.
- Keep single LLM call per request in v1.

## 10) Observability
- Log summary mode, kb_id, and result counts at debug/info levels.

## 11) Test plan
- Mode prompt generation for `faq` and `instructions`.
- Date filtering excludes out-of-range chunks.
- Empty result behavior.
- API route contract exists and is protected.

**Commands**
- `python -m pytest`

## 12) Rollout plan + rollback plan
- Rollout: deploy backend endpoint and bot callbacks using summary modes.
- Rollback: route can fallback to default `summary` only behavior.

## 13) Acceptance criteria checklist
- Endpoint supports all three modes.
- Date filters affect selected context.
- Safety filters applied to final answer.
- Endpoint requires API key.

---

Approval

APPROVED:v1
