# Feature Design Spec: Runtime Warnings and Config Hardening (v1)

## Summary
This change hardens startup/runtime configuration to reduce noisy warnings and align configuration behavior with actual runtime usage.

## Goals
- Fix SQLite WAL verification warning caused by SQLAlchemy 2.x execution API.
- Add explicit Hugging Face token configuration (`HF_TOKEN`) loaded at startup.
- Reduce avoidable Docker runtime warnings for Redis, n8n, and MySQL startup flags.
- Keep existing bot/backend behavior unchanged.

## Scope
In scope:
- `shared/database.py` WAL check query execution.
- `shared/config.py` HF token loading/normalization.
- `docker-compose.yml` runtime parameters for Redis/n8n/MySQL.
- Config/operations documentation updates.

Out of scope:
- Replacing DB engine support (MySQL/SQLite remain supported).
- Refactoring backend storage architecture.
- Full e2e infrastructure test harness.

## Design Decisions
1. SQLite WAL check now executes SQL via `text("PRAGMA journal_mode")` for SQLAlchemy 2.x compatibility.
2. HF token is a first-class config value:
   - read from `HF_TOKEN` with fallback to `HUGGINGFACE_HUB_TOKEN`;
   - exported back to both env aliases for downstream library compatibility.
3. Docker runtime warning hardening:
   - Redis: set `vm.overcommit_memory=1` via service `sysctls`.
   - n8n: enforce secure config permissions via `N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS=true`.
   - MySQL: use non-deprecated auth/cache options in `mysqld` command.

## Verification
- Unit tests for HF token loading behavior.
- Static verification (`py_compile`) for modified Python files.
- Secret scan remains mandatory.
- Manual compose startup log check for warning reduction.

## Rollback
- Revert modified files:
  - `shared/database.py`
  - `shared/config.py`
  - `docker-compose.yml`
  - updated docs and tests
- No schema/data migration involved.
