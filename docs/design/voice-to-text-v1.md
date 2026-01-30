# Feature Design Spec: Voice to Text (v1)

## 1) Summary
**Problem statement**: Users send voice messages to the Telegram bot, but the bot cannot currently transcribe them into text. This limits usability for voice-first workflows and accessibility.

**Goals**
- Transcribe Telegram voice messages to text and return the transcription to the user.
- Support concurrent processing with a global queue shared across users.
- Scale transcription workers based on available GPU devices, with safe CPU fallback.
- Allow admins to configure the transcription model in the admin panel.

**Non-goals**
- Real-time streaming transcription.
- Diarization (speaker separation) or translation.
- Per-user private queues; queueing is global by design.

## 2) Scope boundaries
**In-scope**
- Bot ingestion of Telegram voice messages.
- Backend transcription queue with worker pool sized by GPU count.
- Admin-configurable ASR (speech-to-text) model selection.
- Status/feedback to user (queued, processing, done, error).

**Out-of-scope**
- UI beyond existing Telegram admin menus.
- Adding new external services or dependencies without approval.
- Advanced audio pre/post-processing (noise reduction, VAD tuning).

## 3) Assumptions + constraints
- Follow AGENTS.md workflow; this spec precedes any code changes.
- No new dependencies unless explicitly approved.
- Bot uses backend API for business logic; bot should not access DB directly.
- Current architecture includes `ai_providers.py` for model/provider selection and admin model selection for text/image.
- GPU availability may be zero; system must gracefully fall back to CPU.

## 4) Architecture
**High-level components**
- **Telegram bot**: receives voice messages, uploads audio to backend, relays status and final transcription.
- **Backend API (FastAPI)**: accepts voice uploads, enqueues jobs, manages worker pool, returns results.
- **Transcription worker pool**: consumes global queue and performs ASR using configured model/provider.
- **AI provider manager**: selects ASR model based on admin configuration and auto-selection logic.

**Data flow (textual)**
1. User sends voice message to Telegram bot.
2. Bot downloads audio file from Telegram API, sends it to backend `/asr/transcribe`.
3. Backend enqueues job with metadata (user, message id, duration, file path).
4. Worker pool processes jobs one-by-one per worker (global queue).
5. ASR model transcribes audio; backend persists result (optional) and returns to bot.
6. Bot sends transcription message to the user.

## 5) Interfaces / contracts
**Public API**
- `POST /asr/transcribe`
  - **Request**: multipart/form-data with fields:
    - `file` (audio file, required)
    - `telegram_id` (string/int, required)
    - `message_id` (string/int, required)
    - `language` (optional, BCP-47, e.g., `en`, `ru`)
  - **Response (202)**:
    - `{ "job_id": "uuid", "status": "queued", "queue_position": 5 }`
- `GET /asr/jobs/{job_id}`
  - **Response**:
    - `{ "job_id": "uuid", "status": "queued|processing|done|error", "text": "...", "error": "..." }`

**Internal module boundaries**
- `asr_queue.py`
  - `enqueue_asr_job(file_path: str, telegram_id: int, message_id: int, language: str | None) -> str`
  - `get_job_status(job_id: str) -> AsrJobStatus`
- `asr_worker.py`
  - `start_workers(worker_count: int) -> None`
  - `process_job(job: AsrJob) -> AsrResult`
- `ai_providers.py`
  - `get_asr_model(provider: str, model_name: str, device: str) -> AsrModel`
  - `transcribe(audio_path: str, language: str | None) -> str`

**Error handling strategy**
- If ASR fails, mark job `error` with a user-friendly message; bot sends a concise failure notice.
- Retry policy: single retry on transient errors (e.g., model load failure), no infinite loops.

## 6) Data model changes + migrations
- Add ASR model settings in DB (e.g., table or fields in settings):
  - `asr_provider` (default `ollama` or existing provider)
  - `asr_model_name` (default chosen by system if not set)
  - `asr_device` (optional: `cpu`, `cuda`, `cuda:N`)
- Optional: `asr_jobs` table for persistence (job_id, status, created_at, text, error, metadata).

## 7) Edge cases + failure modes
- No GPU devices available: worker count falls back to 1 CPU worker.
- Multiple GPUs: worker pool size equals number of GPUs unless capped by config.
- Large audio files or unsupported formats: return error with guidance.
- Backend restart: queued in-memory jobs are lost unless persisted.
- Admin changes model while jobs are queued: new jobs use new model; in-flight jobs continue.

## 8) Security requirements
- Authn/authz: backend endpoints require existing API key mechanism.
- Input validation: verify audio mime type and size; reject invalid files.
- Injection risks: no shell execution of user data; all file paths are generated server-side.
- Secrets/logging: do not log raw audio; log job metadata only.
- Dependency policy: no new dependencies unless explicitly approved.

## 9) Performance requirements
- Worker count = number of available GPUs; if 0, use 1 CPU worker (configurable max).
- Queue should handle bursts without dropping jobs; backpressure via queue size limit.
- Target average transcription latency < 30s for short voice notes.

## 10) Observability
- Logs: job enqueue, start, completion, error (include job_id, duration, model).
- Metrics: queue length, job latency, error rate, per-model usage.
- Alerts: sustained high queue length, repeated model load failures.

## 11) Test plan
- Unit tests: queue behavior, worker count selection, model selection logic.
- Integration tests: API `/asr/transcribe` + `/asr/jobs/{id}` with sample audio.
- Manual: send voice messages from Telegram; verify queueing and transcription.

**Commands** (no existing test commands in AGENTS.md):
- `python -m pytest`

## 12) Rollout plan + rollback plan
- Rollout: deploy backend changes, then bot changes; enable admin model selection UI.
- Rollback: disable voice handler in bot; keep backend endpoints unused.

## 13) Acceptance criteria checklist
- Bot accepts Telegram voice messages and returns a transcription message.
- Global queue processes voice messages sequentially per worker.
- Worker pool size equals GPU count (or 1 if none available).
- Admin can select ASR model in admin panel; config is persisted.
- System uses configured model; if unavailable, falls back with logged warning.
- Errors are handled gracefully with user-friendly messages.

---

**Approval**

APPROVED:v1
