#!/usr/bin/env python3
"""Local-only open-harmony wiki ingest/query smoke runner.

This script is intentionally opt-in and developer-local:
- corpus paths come only from env/CLI,
- it uses a temporary SQLite DB by default,
- default answer mode is deterministic extractive fallback (no live LLM required).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


DEFAULT_WIKI_URL = "https://gitee.com/mazurdenis/open-harmony/wikis"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_CASES = [
    {
        "query": "how to sync code with local mirror",
        "expected_source_fragment": "Sync%26Build/Sync%26Build",
        "expected_answer_fragments": ["repo init", "repo sync", "ohos_mirror"],
    },
    {
        "query": "how to build and sync",
        "expected_source_fragment": "Sync%26Build/Sync%26Build",
        "expected_answer_fragments": ["repo sync", "build/prebuilts_download.sh"],
    },
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a local-only open-harmony wiki smoke test.")
    parser.add_argument("--mode", choices=("zip", "git"), default=os.getenv("RAG_OPENHARMONY_WIKI_MODE", "zip"))
    parser.add_argument("--wiki-url", default=os.getenv("RAG_OPENHARMONY_WIKI_URL", DEFAULT_WIKI_URL))
    parser.add_argument("--zip-path", default=os.getenv("RAG_OPENHARMONY_WIKI_ZIP_PATH", ""))
    parser.add_argument("--db-path", default=os.getenv("RAG_OPENHARMONY_WIKI_DB_PATH", ""))
    parser.add_argument("--json-out", default="")
    parser.add_argument("--answer-mode", choices=("extractive", "llm"), default=os.getenv("RAG_OPENHARMONY_WIKI_ANSWER_MODE", "extractive"))
    parser.add_argument("--min-files", type=int, default=int(os.getenv("RAG_OPENHARMONY_WIKI_MIN_FILES", "50")))
    parser.add_argument("--min-chunks", type=int, default=int(os.getenv("RAG_OPENHARMONY_WIKI_MIN_CHUNKS", "100")))
    parser.add_argument("--top-k", type=int, default=int(os.getenv("RAG_OPENHARMONY_WIKI_TOP_K", "5")))
    return parser.parse_args()


def _prepare_env(db_path: str) -> None:
    os.environ["MYSQL_URL"] = ""
    os.environ["DB_PATH"] = db_path
    os.environ.setdefault("RAG_BACKEND", "legacy")
    os.environ.setdefault("RAG_ORCHESTRATOR_V4", "true")
    os.environ.setdefault("RAG_LEGACY_QUERY_HEURISTICS", "false")
    os.environ.setdefault("RAG_ENABLE_RERANK", "false")


def _source_path_list(sources: list[Any]) -> list[str]:
    out: list[str] = []
    for source in sources or []:
        path = str(getattr(source, "source_path", "") or "").strip()
        if path:
            out.append(path)
    return out


def _source_section_list(sources: list[Any]) -> list[str]:
    out: list[str] = []
    for source in sources or []:
        section = str(getattr(source, "section_title", "") or "").strip()
        if section:
            out.append(section)
    return out


def _run_smoke(args: argparse.Namespace, db_path: str) -> dict[str, Any]:
    _prepare_env(db_path)

    from shared.rag_system import rag_system
    from shared.wiki_git_loader import load_wiki_from_git, load_wiki_from_zip
    from shared.database import get_session
    from backend.api.routes import rag as rag_module
    from backend.schemas.rag import RAGQuery

    kb_name = f"openharmony-local-smoke-{int(time.time())}"
    kb = rag_system.add_knowledge_base(kb_name, "local-only smoke")
    kb_id = int(kb.id)

    if args.mode == "zip":
        zip_path = Path(args.zip_path).expanduser().resolve()
        if not zip_path.exists():
            raise FileNotFoundError(f"ZIP corpus not found: {zip_path}")
        ingest_stats = load_wiki_from_zip(
            zip_path=str(zip_path),
            wiki_url=args.wiki_url,
            knowledge_base_id=kb_id,
            loader_options={"chunk_size": 1200, "overlap": 150},
        )
    else:
        ingest_stats = load_wiki_from_git(
            wiki_url=args.wiki_url,
            knowledge_base_id=kb_id,
            loader_options={"chunk_size": 1200, "overlap": 150},
        )

    rag_system._load_index(kb_id)

    if args.answer_mode == "extractive":
        rag_module.ai_manager.query = staticmethod(  # type: ignore[assignment]
            lambda _prompt: "Ошибка подключения к Ollama: forced openharmony smoke"
        )

    query_results: list[dict[str, Any]] = []
    with get_session() as db:
        for case in DEFAULT_CASES:
            query = case["query"]
            answer = rag_module.rag_query(RAGQuery(query=query, knowledge_base_id=kb_id, top_k=args.top_k), db)
            source_paths = _source_path_list(answer.sources)
            section_titles = _source_section_list(answer.sources)
            query_results.append(
                {
                    "query": query,
                    "top_sources": source_paths[: args.top_k],
                    "top_sections": section_titles[: args.top_k],
                    "answer_preview": str(answer.answer or "").strip()[:1200],
                    "answer_sources": source_paths,
                }
            )

    failures: list[str] = []
    files_processed = int(ingest_stats.get("files_processed", 0) or 0)
    chunks_added = int(ingest_stats.get("chunks_added", 0) or 0)
    if files_processed < args.min_files:
        failures.append(f"files_processed {files_processed} < {args.min_files}")
    if chunks_added < args.min_chunks:
        failures.append(f"chunks_added {chunks_added} < {args.min_chunks}")

    for case, result in zip(DEFAULT_CASES, query_results):
        expected_source = case["expected_source_fragment"]
        source_hits = [path for path in result["top_sources"] if expected_source in path]
        if not source_hits:
            failures.append(f"{case['query']}: expected source fragment `{expected_source}` not found in top sources")
        answer_text = result["answer_preview"].lower()
        if not any(fragment.lower() in answer_text for fragment in case["expected_answer_fragments"]):
            failures.append(
                f"{case['query']}: answer preview missing expected fragments {case['expected_answer_fragments']}"
            )

    return {
        "ok": not failures,
        "mode": args.mode,
        "answer_mode": args.answer_mode,
        "knowledge_base_id": kb_id,
        "db_path": db_path,
        "wiki_url": args.wiki_url,
        "ingest": {
            "files_processed": files_processed,
            "chunks_added": chunks_added,
            "wiki_root": str(ingest_stats.get("wiki_root") or ""),
        },
        "queries": query_results,
        "failures": failures,
    }


def main() -> int:
    args = _parse_args()
    temp_dir_obj: tempfile.TemporaryDirectory[str] | None = None
    if args.db_path:
        db_path = str(Path(args.db_path).expanduser().resolve())
    else:
        temp_dir_obj = tempfile.TemporaryDirectory(prefix="openharmony_smoke_")
        db_path = str(Path(temp_dir_obj.name) / "smoke.db")

    try:
        result = _run_smoke(args, db_path)
        payload = json.dumps(result, ensure_ascii=False, indent=2)
        if args.json_out:
            Path(args.json_out).write_text(payload, encoding="utf-8")
        print(payload)
        return 0 if result.get("ok") else 1
    finally:
        if temp_dir_obj is not None:
            try:
                shutil.rmtree(temp_dir_obj.name, ignore_errors=True)
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
