# Plan: AI Question Mode Fix + Voice/Audio -> AI

Date: 2026-03-01
Status: Draft for CC approval

## Proposed Implementation
1. `frontend/bot_handlers.py`
- Add explicit text branch in `handle_text`:
  - If `text_input == "🤖 Задать вопрос ИИ"`:
    - set `context.user_data['state'] = 'waiting_ai_query'`
    - send prompt `"🤖 Задайте вопрос ИИ:"`
    - `return`
- Add internal helper for AI response from plain text (to avoid duplication between text/voice/audio branches).
- Extend `handle_voice`:
  - Keep existing ASR queue and metadata logic.
  - If state is `waiting_ai_query` and transcript is non-empty:
    - build AI prompt from transcript,
    - call `ai_manager.query`,
    - return AI answer to user,
    - reset state to `None`.
  - Else: keep existing transcription output behavior unchanged.
- Extend `handle_audio` with same state-aware AI routing as `handle_voice`.

2. Tests
- Add/extend tests to verify:
  - text button `🤖 Задать вопрос ИИ` moves to AI mode instead of welcome fallback.
  - `handle_voice` in `waiting_ai_query` sends transcript to AI and resets state.
  - `handle_audio` in `waiting_ai_query` sends transcript to AI and resets state.
  - existing transcription-only behavior remains for non-AI mode.

3. Spec/docs updates (mandatory by project policy)
- Update `SPEC.md` acceptance criteria to include voice/audio handoff rule in AI mode.
- Add design note: `docs/design/ask-ai-voice-handoff-v1.md`.
- Update `docs/REQUIREMENTS_TRACEABILITY.md` with new AC mapping and tests.
- Update user docs (`README.md` and/or `docs/USAGE.md`) for new mode behavior.

4. Verification
- Run targeted tests first:
  - `pytest tests/test_bot_voice.py tests/test_bot_audio.py`
  - plus new text-mode test file.
- Run broader sanity tests if quick enough.
- Run secret scan: `python scripts/scan_secrets.py` (or project standard command).

## Risks
- Low-to-medium risk around message formatting changes in voice/audio path.
- Potentially brittle tests due Telegram object mocking; keep mocks minimal and deterministic.

## Rollback
- Revert modified files to previous versions if behavior regression occurs.
- No schema/data migration involved.
