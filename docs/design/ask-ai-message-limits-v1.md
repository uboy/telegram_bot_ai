# Feature Design Spec: Ask-AI Message Length Safety (v1)

## Summary
Bug: direct AI mode could crash on Telegram send with `Message is too long` when model output exceeded Telegram limits.

Goals:
- Prevent blank prompt sends in `waiting_ai_query`.
- Deliver long AI responses safely without Telegram API errors.

## Scope
In scope:
- `frontend/bot_handlers.py` direct AI mode text/voice/audio answer delivery.
- Regression tests for empty-input validation and long-response chunking.

Out of scope:
- Changing model generation parameters.
- Backend API changes.

## Behavior
1. In `waiting_ai_query`, blank text input returns a validation prompt and keeps state.
2. AI HTML answer is sent normally if small enough.
3. If HTML answer is oversized, bot falls back to plain-text chunks under Telegram limits.
4. Voice/audio AI-mode path uses the same safe reply mechanism.

## Verification
- `tests/test_bot_text_ai_mode.py`:
  - empty-input validation in AI mode
  - long-answer split without `Message is too long`

## Rollback
- Revert `frontend/bot_handlers.py` and corresponding tests/docs.
- No database migrations.
