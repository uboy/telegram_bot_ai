# Design: KB Create State Machine Fix v1

Date: 2026-03-04  
Type: Bugfix (Telegram admin KB flow)

## Problem
- Admin callback `kb_create` sets `context.user_data['state'] = 'waiting_kb_name'`.
- Text handler had no `waiting_kb_name` branch, so entered KB names fell into default fallback (`handle_start`) and showed "Добро пожаловать".
- Result: KB was never created from admin chat flow.

## Root Cause
- Regression in bot state machine: state assignment exists in callbacks, corresponding text-state handler was removed/missing.

## Solution
- Add `waiting_kb_name` branch in `frontend/bot_handlers.py::handle_text`:
  - accept non-empty KB name,
  - call `backend_client.create_knowledge_base`,
  - return explicit success/failure message with admin menu,
  - clear state after processing.

## Scope
- Runtime code: `frontend/bot_handlers.py`.
- Automated regression coverage: `tests/test_bot_text_ai_mode.py`.
- No API contract change.
- No DB schema/config/dependency changes.

## Verification
- `python -m py_compile frontend/bot_handlers.py tests/test_bot_text_ai_mode.py`
- `.venv\Scripts\python.exe -m pytest -q tests/test_bot_text_ai_mode.py`

## Rollback
- Revert the `waiting_kb_name` branch in `frontend/bot_handlers.py`.
- Revert the regression test if rollback is required.
