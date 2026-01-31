# Feature Design Spec: Audio File Handler (v1)

## 1) Summary
**Problem statement**: The bot currently handles only Telegram voice messages (`filters.VOICE`). Users who upload audio files (e.g., MP3) see no response because no handler is registered for audio/document messages.

**Goals**
- Add bot support for audio file messages (MP3 and other supported audio types).
- Reuse existing ASR flow and backend queue.
- Provide consistent user feedback and metadata in responses.

**Non-goals**
- UI changes beyond message formatting.
- New dependencies or external services.

## 2) Scope boundaries
**In-scope**
- Telegram bot handler for `filters.AUDIO` (and optional audio documents).
- Forward audio file bytes to backend `/api/v1/asr/transcribe`.
- Return transcription + metadata to user (same as voice).

**Out-of-scope**
- Backend changes to accept new formats beyond current allowlist.
- Changes to admin panel or settings.

## 3) Assumptions + constraints
- Follow AGENTS.md workflow; minimal diffs.
- No new dependencies.
- Backend already supports `audio/mpeg` and uses ffmpeg for decoding non-WAV.
- If ffmpeg is missing, backend returns 503 with guidance.

## 4) Architecture
**Components**
- `frontend/bot.py`: register new handler for audio files.
- `frontend/bot_handlers.py`: implement `handle_audio` using same logic as `handle_voice`.
- Backend remains unchanged.

**Data flow (textual)**
1. User sends audio file (MP3) to bot.
2. Bot downloads file bytes and calls backend `/api/v1/asr/transcribe`.
3. Backend queues job; bot polls status.
4. Bot returns transcription + metadata.

## 5) Interfaces / contracts
**Bot handlers**
- New handler function:
  - `handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE)`
- Reuse `backend_client.asr_transcribe` and `backend_client.asr_job_status`.

**Backend API**
- No changes; already supports `audio/mpeg` in `backend/api/routes/asr.py`.

## 6) Data model changes + migrations
- None.

## 7) Edge cases + failure modes
- Audio message too large → backend 413.
- Unsupported content type → backend 400.
- Missing ffmpeg for non-WAV → backend 503.
- Telegram audio file without filename → fallback to file_id.

## 8) Security requirements
- Authn/authz unchanged (API key on ASR routes).
- Input validation remains on backend.
- No logging of raw audio content.

## 9) Performance requirements
- Avoid repeated downloads; reuse file bytes once.
- Polling loop same as voice handler.

## 10) Observability
- Existing structured logs in backend are sufficient.
- Bot logs should note audio filename and file_id at INFO.

## 11) Test plan
**Targets**
- Manual: send MP3 to bot and verify transcription.
- Regression: voice note still works.

**Commands**
- If tests listed in AGENTS.md: run them.
- Otherwise propose: `python -m pytest` (ask before running if expensive).

## 12) Rollout plan + rollback plan
- Rollout: deploy bot changes only.
- Rollback: remove audio handler registration.

## 13) Acceptance criteria checklist
- MP3 audio file messages trigger transcription.
- Response includes transcription and metadata (duration/name/sent time if available).
- Voice notes continue to work unchanged.

---

Approval

APPROVED:v1
