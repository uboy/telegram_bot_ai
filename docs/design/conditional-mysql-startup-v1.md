# Feature Design Spec: Conditional MySQL Startup (v1)

## Summary
Goal: avoid starting MySQL container when project is configured to run with SQLite.

## Problem
`docker compose up` always started `db` service even when `MYSQL_URL` was not set and app runtime used SQLite.

## Solution
1. Move `db` service under compose profile `mysql`.
2. Remove hard `depends_on: db` from `bot` and `backend`.
3. Add startup launcher `scripts/start_stack.py`:
   - reads `.env`,
   - if `MYSQL_URL` is set -> runs `docker compose --profile mysql up ...`,
   - else runs `docker compose up ...` without MySQL profile.
4. Add convenience wrappers:
   - `scripts/start_stack.ps1` for Windows PowerShell
   - `scripts/start_stack.sh` for Linux/macOS shells

## Scope
In scope:
- `docker-compose.yml`
- `scripts/start_stack.py`
- `scripts/start_stack.ps1`
- `scripts/start_stack.sh`
- tests for launcher logic
- docs/spec/traceability updates

Out of scope:
- removing MySQL support from application code
- DB migration strategy changes

## Verification
- unit tests for startup decision logic and command generation.
- manual runtime check via `python scripts/start_stack.py --dry-run`.

## Rollback
- restore previous compose dependencies and remove launcher script.
- no schema/data changes.
