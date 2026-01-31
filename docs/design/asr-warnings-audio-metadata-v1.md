# Feature Design Spec: ASR Warnings + Audio Metadata (v1)

## 1) Summary
**Problem statement**: Current ASR pipeline logs several warnings (Transformers deprecations, missing attention mask, missing num_frames fallback, reranker unexpected keys) that indicate suboptimal or fragile usage. Also, when multiple voice messages are sent, users receive only plain transcription text without context about which audio was transcribed.

**Goals**
- Eliminate or reduce actionable ASR warnings by using supported pipeline inputs and config.
- Provide users with basic audio metadata per transcription so they can match output to the source audio.
- Keep changes minimal and aligned with existing architecture (bot -> backend -> worker).

**Non-goals**
- Changing model quality/accuracy beyond warning fixes.
- Adding new dependencies or external services.
- Implementing metrics/alerts beyond existing structured logs requirement.

## 2) Scope boundaries
**In-scope**
- ASR worker changes to pass proper inputs (`num_frames`, `attention_mask`) and supported config (`task`, `language`).
- Optional suppression of known benign warnings (reranker unexpected keys) with explicit rationale.
- Extend ASR job status payload to include audio metadata (duration, filename/id, sent time, size, codec, sample rate).
- Bot response format updates to show metadata alongside transcription.

**Out-of-scope**
- UI/UX beyond Telegram bot message formatting.
- Dependency upgrades (Transformers, Torch) unless already approved.
- New observability systems or alerting frameworks.

## 3) Assumptions + constraints
- Follow AGENTS.md workflow; minimal diffs.
- No new dependencies.
- ffmpeg is required for non-WAV input and must be available in PATH.
- Current ASR logic lives in backend services (see `backend/services/asr_worker.py`).
- Existing ASR queue/global job flow remains unchanged.

## 4) Architecture
**Components**
- `backend/services/asr_worker.py`: audio conversion, feature extraction, model/pipeline invocation.
- `backend/api/routes/asr.py`: job creation and job status response payload.
- `bot_handlers.py`: user-facing message composition for transcription + metadata.

**Data flow (textual)**
1. Bot receives voice message and sends file to backend `/api/v1/asr/transcribe`.
2. Backend saves file, captures metadata (original filename/id, send timestamp), enqueues job.
3. Worker converts audio (if needed), extracts features (input_features, attention_mask, num_frames).
4. ASR pipeline/model runs with explicit `task`/`language` config.
5. Job status includes transcription + audio metadata + timings; bot formats response.

## 5) Interfaces / contracts
**Public HTTP APIs**
- `POST /api/v1/asr/transcribe`
  - Request: multipart file + `telegram_id`, `message_id`, optional `language`, optional `message_date`.
  - Response: `{ job_id, queue_position }`
- `GET /api/v1/asr/jobs/{job_id}`
  - Response: `{ job_id, status, text, error, audio_meta, timing_meta }`

**New/extended response fields**
- `audio_meta`:
  - `original_name` (string, may be Telegram file name/id)
  - `duration_s` (float)
  - `size_bytes` (int)
  - `codec` (string)
  - `sample_rate` (int)
  - `channels` (int)
  - `sent_at` (ISO-8601 string, if provided)
- `timing_meta`:
  - `queued_at`, `started_at`, `finished_at` (ISO-8601)
  - `queue_wait_s`, `processing_s` (float)

**Internal module boundaries**
- `backend/services/asr_worker.py`
  - `_load_audio(...) -> {input_features, attention_mask, num_frames, meta}`
  - `_transcribe_audio(...) -> text`
- `backend/services/asr_queue.py`
  - `enqueue_asr_job(..., audio_meta: dict) -> job_id`
  - `get_job_status(job_id) -> AsrJobStatus`

**Error handling strategy**
- If audio load/conversion fails: mark job error with clear guidance (e.g., missing ffmpeg or unsupported format).
- If ASR fails: mark job error with user-friendly message; log full exception server-side.

## 6) Data model changes + migrations
- If jobs are stored in memory only: add `audio_meta` and `timing_meta` to in-memory job record.
- If persisted table exists (or will exist): add JSON fields for metadata and timings.

## 7) Edge cases + failure modes
- User sends multiple voice messages: ensure each response includes unique `sent_at`/duration/name.
- Missing `message_date`: omit or mark as unknown.
- Missing `ffmpeg`: fail fast with clear error.
- Unknown codec/sample rate: fields set to null/unknown.

## 8) Security requirements
- Authn/authz unchanged (API key requirement stays).
- Input validation: enforce size limits and content types.
- Logging: no raw audio content; metadata only.
- Dependency policy: no new dependencies.

## 9) Performance requirements
- Avoid extra audio copies where possible.
- Metadata extraction should be lightweight (reuse existing probe results).

## 10) Observability
- Required structured logs already defined in voice-to-text spec (enqueue/start/finish/error with job_id, duration, model, queue size).
- Additional logs: include audio metadata summary on job start (duration, codec, sample_rate) at INFO.

## 11) Test plan
**Targets**
- Unit: `_load_audio` returns `input_features`, `attention_mask`, `num_frames`, metadata.
- Integration: run ASR job on OGG/Opus; ensure no warnings and metadata returned.
- Manual: send multiple voice messages; verify each response includes metadata and matching timestamps.

**Commands**
- If tests listed in AGENTS.md: run them.
- Otherwise propose: `python -m pytest` (ask before running if expensive).

## 12) Rollout plan + rollback plan
- Rollout: deploy backend changes first, then bot formatting changes.
- Rollback: revert to previous image; bot will still show transcription text only.

## 13) Acceptance criteria checklist
- ASR worker no longer logs: missing `num_frames` warnings.
- ASR worker uses `task`/`language` config (no forced_decoder_ids deprecation warning).
- ASR worker provides attention_mask to avoid pad/eos warning.
- Each transcription message includes audio metadata (duration, name/id, sent time, size).
- Errors include clear guidance for missing ffmpeg or unsupported audio.

---

Approval

APPROVED:v1
