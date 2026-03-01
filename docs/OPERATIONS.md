# Operations Runbook

## Local Startup

1. Install deps:
```bash
.venv\Scripts\python -m pip install -r requirements.txt
```

2. Set env:
- Copy `env.template` to `.env`.
- Fill `TELEGRAM_BOT_TOKEN`, `ADMIN_IDS`, DB and backend settings.
- If you use gated/private Hugging Face models, set `HF_TOKEN`.

3. Start backend:
```bash
.venv\Scripts\python -m backend.main
```

4. Start bot:
```bash
.venv\Scripts\python -m frontend.bot
```

## Docker Startup

Use smart launcher:
```bash
python scripts/start_stack.py
```

- `MYSQL_URL` set -> launcher enables `mysql` compose profile and starts `db`.
- `MYSQL_URL` missing/empty -> launcher starts stack without `db`.
- Dry-run mode:
```bash
python scripts/start_stack.py --dry-run
```

## Health Checks

- API health: `GET /api/v1/health`
- Ingestion jobs: `GET /api/v1/jobs/{job_id}`
- ASR jobs: `GET /api/v1/asr/jobs/{job_id}`
- Analytics digest status: `GET /api/v1/analytics/digests/{digest_id}`

## Container Runtime Notes

- Redis: compose config sets `vm.overcommit_memory=1` to avoid Redis persistence warnings.
- n8n: compose config enforces secure settings file permissions (`N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS=true`).
- MySQL: compose starts `mysqld` with non-deprecated auth/cache options (`--authentication-policy`, `--host-cache-size=0`).

## Security Checks

- Confirm `BACKEND_API_KEY` is set in production.
- Verify protected routes reject requests without `X-API-Key`.
- Keep `.env` untracked in git.

## Incident Quick Actions

- ASR queue overload:
  - reduce incoming load
  - inspect `ASR_QUEUE_MAX`, `ASR_MAX_WORKERS`
  - restart backend workers if stalled

- Ingestion failures:
  - poll `/jobs/{job_id}` and inspect `error`
  - validate file/path/url input and external connectivity

- Analytics failures:
  - inspect digest/import status endpoints
  - check scheduler and DB availability

## Rollback

- Revert to previous image/tag.
- Disable affected menu path in bot callbacks if needed.
- Keep DB schema backward-compatible for v1 rollback.
