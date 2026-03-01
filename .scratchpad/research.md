# Research: AI Question Mode + Voice/Audio AI Handoff

Date: 2026-03-01
Agent: codex (team-lead-orchestrator / architect phase)

## User Request
1. Fix broken behavior for feature "Задать вопрос ИИ" (currently returns "Добро пожаловать!" repeatedly).
2. Add behavior: in "Задать вопрос ИИ" mode, when user sends voice/audio, bot must transcribe first and then send transcription text to AI.
3. Keep other bot functions unchanged.

## Current Behavior Findings
- `frontend/templates/buttons.py` contains reply keyboard button `"🤖 Задать вопрос ИИ"`.
- `frontend/bot_callbacks.py` has inline callback branch `ask_ai` that sets `context.user_data['state'] = 'waiting_ai_query'`.
- `frontend/bot_handlers.py::handle_text` does NOT handle text `"🤖 Задать вопрос ИИ"`.
- In `handle_text`, unknown text with no `kb_id` falls into `else -> handle_start(...)`.
- Result: pressing reply keyboard button is treated as unknown text, and bot replies with welcome message.

## Voice/Audio Pipeline Findings
- `handle_voice` and `handle_audio` perform ASR and always end with transcription message (or error/timeout).
- They ignore `context.user_data['state']` and therefore cannot route transcript to AI when in `waiting_ai_query`.

## Root Cause
Primary bug root cause is missing `handle_text` branch for reply-keyboard text `"🤖 Задать вопрос ИИ"`.
Secondary gap is missing AI-mode routing in voice/audio handlers.

## Impacted Components
- `frontend/bot_handlers.py` (primary logic changes)
- Tests:
  - `tests/test_bot_voice.py` (extend with AI-mode case)
  - `tests/test_bot_audio.py` (extend with AI-mode case)
  - add/extend text-mode test coverage for ask-ai entry (`handle_text`)

## Constraints / Non-regression Requirements
- Preserve current transcription-only behavior when NOT in `waiting_ai_query`.
- Preserve all other menu flows and states.
- Keep AI provider/model selection behavior as currently wired via `ai_manager.query` and `create_prompt_with_language`.

## Validation Targets
- Pressing `🤖 Задать вопрос ИИ` sets `state=waiting_ai_query` and prompts for AI question.
- In `waiting_ai_query`, voice/audio:
  - ASR runs.
  - Transcript is sent to AI.
  - User gets AI answer.
  - State resets to `None` after successful AI response.
- Outside `waiting_ai_query`, voice/audio still return transcription as before.
