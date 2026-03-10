# Bot KB Search Session Scope v1

Date: 2026-03-10
Status: approved for implementation (`APPROVED:v1`)
Task: `BOTFOLLOW-002`

## Goal

Fix KB search so the bot does not silently reuse a stale admin-selected KB for search queries.

Required user-visible behavior:
- if multiple KBs exist, the user must choose which KB to search for the current KB-search session;
- if exactly one KB exists, the bot auto-selects it;
- explicit re-entry into KB search resets the previous search-session KB choice.

## Current problem

The existing search flow reuses generic `context.user_data["kb_id"]`, which is also populated by admin KB-management callbacks (`kb_select:*`, upload/wiki/settings flows). That makes KB search scope leak across unrelated admin actions and across separate KB-search sessions.

## Design

### Session-scoped search key

Introduce a dedicated search-session key:
- `active_search_kb_id`

Rules:
- use `active_search_kb_id` only for KB search;
- keep generic `kb_id` for admin KB-management actions;
- clear `active_search_kb_id` inside KB-search session reset.

### Entry behavior

Both KB-search entry points must share one behavior:
- text button `🔍 Поиск в базе знаний`
- callback `search_kb`

On entry:
1. reset the previous KB-search session state;
2. fetch the KB list;
3. if no KBs exist, return a clear error;
4. if exactly one KB exists, set `active_search_kb_id` and enter `waiting_query`;
5. if multiple KBs exist, enter `waiting_kb_for_query` and show KB choice UI.

### KB selection behavior

When `kb_select:<id>` is used while `state == "waiting_kb_for_query"`:
- set `active_search_kb_id`;
- do not rely on generic `kb_id` for search scope;
- if there are queued pending search queries, flush them against the chosen search KB;
- otherwise prompt the user to enter a search query.

Outside `waiting_kb_for_query`, `kb_select:<id>` keeps the existing admin KB-management behavior.

## Test plan

Required automated coverage:
- multi-KB KB-search entry prompts for KB choice;
- single-KB KB-search entry auto-selects the only KB;
- `waiting_query` enqueues against `active_search_kb_id`;
- explicit KB-search re-entry clears the previous search-session KB;
- callback `search_kb` follows the same multi/single-KB selection logic;
- callback `kb_select:<id>` uses `active_search_kb_id` during `waiting_kb_for_query`.

## Docs impact

Update:
- `SPEC.md`
- `docs/REQUIREMENTS_TRACEABILITY.md`
- `docs/USAGE.md`

No operations/runbook change is required because this is a bot UX/state fix rather than an operational contract change.
