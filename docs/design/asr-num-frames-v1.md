# Feature Design Spec: ASR num_frames Crash Fix

## 1) Summary
**Problem statement:** ASR worker crashes on forwarded Telegram voice messages with a `KeyError: 'num_frames'` inside the Transformers ASR pipeline preprocessing.  
**Goals:** prevent worker crashes; log detected audio format/metadata; return clear, user-facing errors for unsupported/undecodable audio.  
**Non-goals:** model quality changes; reranker warnings; pinning/upgrading dependency versions.

## 2) Scope boundaries
**In-scope**
- Harden ASR pipeline invocation to avoid `num_frames` KeyError.
- Detect and log audio format/metadata (extension + ffmpeg probe if available).
- Propagate clear error messages back to user when audio is unsupported/undecodable.

**Out-of-scope**
- Changes to Telegram bot UX beyond error text.
- Dependency upgrades or pinning (Transformers version remains as-is).

## 3) Assumptions + constraints
- Follow AGENTS.md workflow; minimal diffs.
- No new dependencies.
- ffmpeg is available in container (per Dockerfile) but may be missing in other environments.
- ASR audio files are saved to disk by backend API and processed in `backend/services/asr_worker.py`.

## 4) Architecture
**Components**
- `backend/api/routes/asr.py`: accepts uploads, enqueues ASR jobs.
- `backend/services/asr_worker.py`: pulls jobs, converts audio (ffmpeg), loads audio, runs Transformers pipeline.
- `frontend/bot_handlers.py`: sends audio to backend and shows job status/errors to user.

**Responsibilities**
- API route: validate content type, size, file extension, save temp file.
- Worker: convert to WAV if needed, load samples, run ASR, handle errors, set job status.
- Frontend: show transcription or error string from backend.

**Data flow (textual)**
1. Telegram voice forwarded → bot downloads bytes.
2. Bot calls backend `/asr/transcribe` with file bytes + metadata.
3. Backend saves file, enqueues job.
4. ASR worker reads job → converts to WAV → loads samples → ASR pipeline → status update.
5. Bot polls `/asr/jobs/{job_id}` and shows result/error.

## 5) Interfaces / contracts
### Public HTTP APIs
- `POST /api/v1/asr/transcribe`
  - Request: multipart file + `telegram_id`, `message_id`, optional `language`
  - Response: `{ job_id, queue_position }`
  - Error: 4xx for invalid/unsupported content types; 5xx for internal errors.
- `GET /api/v1/asr/jobs/{job_id}`
  - Response: `{ job_id, status, text, error }`

### Internal module boundaries
- `backend/services/asr_worker.py`
  - `_convert_audio_if_needed(audio_path: str) -> str`
  - `_load_audio(audio_path: str) -> dict`
  - `_transcribe_audio(audio_path: str, language: Optional[str], model_name: str, device: Optional[str]) -> str`

### Error handling strategy
- Any ASR failures must be caught and set job status to `error` with a clear message.
- For unsupported/undecodable audio, set error message indicating format issue.
- Log format metadata at INFO/WARN level without dumping raw audio.

## 6) Data model changes + migrations
- None.

## 7) Edge cases + failure modes
- Audio with unknown extension.
- ffmpeg missing when non-WAV input provided.
- Corrupted/empty audio.
- Very short audio resulting in empty transcription.
- GPU vs CPU device mismatch.

## 8) Security requirements
- Authn/authz: existing API key requirement on ASR routes stays unchanged.
- Input validation: continue enforcing allowed content types and max size.
- Injection risks: do not include untrusted user data in shell commands except ffmpeg input path (already internal temp file).
- Secrets/logging: do not log audio bytes or user secrets; log only metadata.
- Dependency policy: no new dependencies.

## 9) Performance requirements
- Avoid extra copies of audio when possible.
- Metadata probing should be lightweight; only run when needed.

## 10) Observability
- Log audio format metadata (extension, content type if known, sample rate, channels, duration if available).
- Log specific fallback usage if alternative pipeline invocation is used.
- Alert on repeated ASR job errors.

## 11) Test plan
**Targets**
- Unit: `_load_audio`/format detection helpers.
- Integration: enqueue ASR job with forwarded voice OGG/Opus and ensure no crash.
- Manual: send forwarded voice message; check bot response.

**Commands**
- If tests listed in AGENTS.md: run them.
- Otherwise propose: `python -m pytest` (ask before running if expensive).

## 12) Rollout plan + rollback plan
- Rollout: deploy backend container with changes; monitor ASR error logs.
- Rollback: revert to previous image if error rate increases.

## 13) Acceptance criteria checklist
- Forwarded Telegram voice messages do not crash ASR worker.
- ASR jobs return either transcript or a controlled error (no traceback).
- Logs include one concise line with detected audio format and sample rate.
- Unsupported or undecodable audio returns a clear user-facing error.

---

Approval

APPROVED:v1
