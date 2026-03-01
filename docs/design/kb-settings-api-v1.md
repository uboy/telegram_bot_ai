# Feature Design Spec: Knowledge Base Settings API (v1)

## 1) Summary
**Problem statement**: RAG behavior differs by knowledge base and requires a persisted, editable settings contract.

**Goals**
- Expose KB settings via API for read/update.
- Normalize and persist settings per KB.
- Keep defaults stable when fields are omitted.

**Non-goals**
- Per-user overrides.
- Dynamic schema migration framework for settings.

## 2) Scope boundaries
**In-scope**
- `GET /api/v1/knowledge-bases/{kb_id}/settings`
- `PUT /api/v1/knowledge-bases/{kb_id}/settings`
- Admin UI integration via backend client.

**Out-of-scope**
- New settings categories not already supported by `shared.kb_settings`.

## 3) Assumptions + constraints
- No new dependencies.
- Existing `KnowledgeBase.settings` JSON/text field remains storage target.
- API key auth required.

## 4) Architecture
**Components**
- `backend/api/routes/knowledge.py`
- `shared/kb_settings.py` (`normalize_kb_settings`, defaults, serialization)

**Data flow**
1. Client requests current settings.
2. Backend loads KB, normalizes settings and returns shape.
3. Client updates settings.
4. Backend normalizes + persists merged settings.

## 5) Interfaces / contracts
**Public APIs**
- `GET /knowledge-bases/{kb_id}/settings` -> `KnowledgeBaseSettings`
- `PUT /knowledge-bases/{kb_id}/settings` -> updated `KnowledgeBaseSettings`

**Internal boundaries**
- `normalize_kb_settings(raw) -> dict`
- `dump_kb_settings(settings) -> str|dict`

**Error handling**
- Missing KB -> `404`.
- Invalid payload -> `422` via schema validation.

## 6) Data model changes + migrations
- No schema migration in v1.

## 7) Edge cases + failure modes
- Partial payloads: normalize and keep supported defaults.
- Unknown keys: normalized/ignored according to settings policy.

## 8) Security requirements
- API key required for get/update operations.
- No secrets in settings payload logs.

## 9) Performance requirements
- Settings operations should be low-latency (single row read/write).

## 10) Observability
- Log settings update events with `kb_id` and caller metadata (if available).

## 11) Test plan
- GET returns normalized settings.
- PUT persists updated values.
- Missing KB returns `404`.
- API key dependency exists on both endpoints.

**Commands**
- `python -m pytest`

## 12) Rollout plan + rollback plan
- Rollout: deploy backend and bot callbacks using settings API.
- Rollback: revert to default settings-only behavior.

## 13) Acceptance criteria checklist
- KB settings are retrievable through API.
- KB settings can be updated and persisted.
- Nonexistent KB returns controlled `404`.
- Endpoints protected by API key.

---

Approval

APPROVED:v1
