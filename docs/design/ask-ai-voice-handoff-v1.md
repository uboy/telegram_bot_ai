# Feature Design Spec: Ask-AI Voice/Audio Handoff (v1)

## 1) Summary
Problem: button-driven direct AI mode ("🤖 Задать вопрос ИИ") from reply keyboard was not entering AI mode, and voice/audio in this mode were not forwarded to AI after ASR.

Goals:
- Fix direct AI mode entry from reply keyboard text button.
- In `waiting_ai_query`, process voice/audio as `ASR -> AI answer`.
- Keep existing non-AI transcription behavior unchanged.

Non-goals:
- Changing ASR backend contracts.
- Changing AI provider selection UX.

## 2) Scope
In scope:
- `frontend/bot_handlers.py` state routing for text, voice, and audio handlers.
- Regression tests for text mode entry and voice/audio AI handoff.
- Spec/traceability/user-doc synchronization.

Out of scope:
- Backend API or schema changes.
- New dependencies.

## 3) Behavioral Contract
1. User presses reply keyboard button `🤖 Задать вопрос ИИ`.
2. Bot sets `context.user_data['state'] = 'waiting_ai_query'` and asks user for a question.
3. If user sends text, bot sends text prompt directly to AI and returns answer.
4. If user sends voice/audio while in `waiting_ai_query`:
   - Bot transcribes audio via ASR pipeline.
   - Bot sends transcript text to AI.
   - Bot returns AI answer and resets state to `None`.
5. Outside `waiting_ai_query`, voice/audio behavior remains transcription-only.

## 4) Risks and Mitigations
- Risk: regressions in existing ASR flow.
  - Mitigation: keep non-AI branch unchanged and add dedicated regression tests.
- Risk: duplicated AI prompt logic.
  - Mitigation: central helper `render_ai_answer_html(...)`.

## 5) Verification Plan
- `tests/test_bot_text_ai_mode.py`: reply keyboard enters AI mode.
- `tests/test_bot_voice.py`: baseline transcription and AI-mode handoff.
- `tests/test_bot_audio.py`: baseline transcription and AI-mode handoff.

## 6) Rollback
- Revert `frontend/bot_handlers.py` and new/updated tests.
- No database/data migration impact.
