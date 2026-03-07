# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Telegram bot with RAG (Retrieval Augmented Generation) — a microservices system where a Telegram bot frontend communicates with a FastAPI backend via HTTP. The bot has **no direct DB or RAG access**; all business logic lives in the backend. Shared modules (RAG engine, DB models, document loaders) are in `shared/`.

Primary language: Python 3.11. The codebase and comments are a mix of Russian and English.

## Commands

### Running locally

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and fill in .env
cp env.template .env

# Start the Telegram bot (frontend)
python frontend/bot.py

# Start the FastAPI backend (separate terminal)
python backend/main.py
# or: uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

### Docker deployment

```bash
docker-compose up -d --build
# Logs
docker-compose logs -f bot
docker-compose logs -f backend
```

Services: `bot`, `backend`, `db` (MySQL 8.0), `redis` (Redis 7), `n8n` (automation webhooks).

### Running tests

```bash
# Individual test files (most tests are standalone scripts, not pytest-based)
python tests/test_rag_safety.py
python tests/test_chunking_markdown.py
python tests/test_rag_quality.py --kb-name "My KB"

# RAG quality test runner (bash, accepts --kb-name or --kb-id)
bash tests/run_tests.sh --kb-name "My KB"

# pytest works for the unit-style tests
pytest tests/test_rag_safety.py
pytest tests/test_chunking_markdown.py
```

### Database operations

```bash
python shared/migrate.py           # Run migrations
python scripts/reindex_kb.py       # Reindex knowledge base
```

## Architecture

### Service boundaries

```
User → Telegram Bot (frontend/) → HTTP (backend_client.py) → FastAPI Backend (backend/) → shared/
```

- **`frontend/`** — Telegram bot. Handles updates, menus, formatting. Calls backend via `backend_client.py` using `httpx`. Never touches DB or RAG directly.
- **`backend/`** — FastAPI service. REST API under `/api/v1/`. Routes in `backend/api/routes/`, services in `backend/services/`. Owns all DB writes and RAG operations.
- **`shared/`** — Common code used by the backend: `rag_system.py` (FAISS vector search + reranking), `database.py` (SQLAlchemy models), `document_loaders/` (per-format parsers + chunking), `ai_providers.py` (Ollama/OpenAI abstraction), `config.py` (loads `.env`).

### Key data flows

**RAG query:** Bot → `POST /api/v1/rag/query` → `rag_system.search()` → FAISS similarity (top-100) → cross-encoder rerank → top-k results → LLM generates answer with `<source_id>` citation tags → bot formats HTML response.

**Document ingestion:** Bot uploads file → `POST /api/v1/ingestion/document` → document loader parses → chunking (default 1800 chars, 300 overlap) → sentence-transformers embeddings → FAISS index + SQL storage → optional n8n webhook.

**ASR (voice-to-text):** Voice/audio message → bot sends to `POST /api/v1/asr/process` → queued in Redis → `asr_worker` transcribes via Whisper → bot polls `GET /api/v1/asr/status/{job_id}`.

### Database

MySQL (Docker) or SQLite (local). Key tables: `users`, `knowledge_bases`, `knowledge_chunks` (with embedding JSON + FAISS index), `documents`, `document_versions`, `knowledge_import_logs`, `messages`, `jobs`, `app_settings`.

### AI/ML stack

- Embeddings: `sentence-transformers` (default model: `intfloat/multilingual-e5-base`)
- Vector search: `faiss-cpu` (in-memory per backend instance)
- Reranker: `BAAI/bge-reranker-base` (cross-encoder)
- LLM: Ollama (default) or OpenAI
- ASR: Whisper (via transformers or OpenAI API)
- OCR: `pytesseract`

### Configuration

All config via `.env` (see `env.template`). Loaded by `shared/config.py`. Required: `TELEGRAM_BOT_TOKEN`, `ADMIN_IDS`, and either `MYSQL_URL` or `DB_PATH`.

## Multi-Agent Workflow

The project uses a sequential agent workflow defined in `AGENTS.md`: product → architect → developer → reviewer → qa (with optional security/devops roles). Follow this flow for non-trivial changes.

Global baseline rules are defined in `C:\Users\devl\.codex\AGENTS.md`; project `AGENTS.md` only adds stricter supplements.

Mandatory project governance:
- Do not run `git add`/`git commit`/`git push` without explicit user approval for current diff.
- Any functional/API/behavior/config change (including bug fixes) must update `SPEC.md`, relevant `docs/design/*` spec, and `docs/REQUIREMENTS_TRACEABILITY.md`.
- Run secret checks before completion; never commit tokens/passwords/keys or `.env` secrets.
- If request is ambiguous or has multiple valid outcomes, ask clarifying questions first.

## Conventions

- Bot→backend communication uses `X-API-Key` header (optional but recommended).
- KB-specific settings (chunking mode, reranking) stored in DB JSON, managed via `shared/kb_settings.py`.
- Document loaders follow a base class pattern in `shared/document_loaders/base.py`; `DocumentLoaderManager` auto-selects by file extension.
- System dependencies for full functionality: `ffmpeg`, `tesseract-ocr`, `poppler-utils` (installed in Dockerfile).
