#!/usr/bin/env python3
"""Local-only wiki corpus ingest/query smoke runner.

This helper is intentionally opt-in and developer-local:
- corpus paths come only from env/CLI,
- it uses a temporary SQLite DB by default,
- default answer mode is deterministic extractive fallback (no live LLM required),
- it can run either against the in-process backend codepath or an already running backend API.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


PROFILE_DEFAULTS: dict[str, dict[str, Any]] = {
    "openharmony": {
        "wiki_url": "https://gitee.com/mazurdenis/open-harmony/wikis",
        "env_prefix": "RAG_OPENHARMONY_WIKI_",
        "default_mode": "zip",
        "default_top_k": 5,
        "default_answer_mode": "extractive",
        "default_cases": [
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
        ],
        "default_min_files": 50,
        "default_min_chunks": 100,
    },
    "arkuiwiki": {
        "wiki_url": "https://gitee.com/rri_opensource/arkuiwiki/wikis",
        "env_prefix": "RAG_ARKUI_WIKI_",
        "default_mode": "zip",
        "default_top_k": 5,
        "default_answer_mode": "extractive",
        "default_cases": [],
        "default_min_files": 10,
        "default_min_chunks": 20,
    },
}


def _profile_config(profile: str) -> dict[str, Any]:
    key = str(profile or "").strip().lower()
    if key not in PROFILE_DEFAULTS:
        raise ValueError(f"unsupported profile: {profile}")
    return dict(PROFILE_DEFAULTS[key])


def _profile_env_value(profile: str, suffix: str, fallback: str = "") -> str:
    config = _profile_config(profile)
    return str(os.getenv(f"{config['env_prefix']}{suffix}", fallback)).strip()


def _parse_cases_payload(raw: str) -> list[dict[str, Any]]:
    payload = json.loads(raw)
    if not isinstance(payload, list):
        raise ValueError("cases payload must be a JSON list")
    cases: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("each case must be a JSON object")
        query = str(item.get("query") or "").strip()
        if not query:
            raise ValueError("each case must include a non-empty `query`")
        normalized = {
            "query": query,
            "expected_source_fragment": str(item.get("expected_source_fragment") or "").strip(),
            "expected_family_fragment": str(item.get("expected_family_fragment") or "").strip(),
            "expected_answer_fragments": [
                str(fragment).strip()
                for fragment in (item.get("expected_answer_fragments") or [])
                if str(fragment).strip()
            ],
            "failure_class": str(item.get("failure_class") or "").strip(),
            "query_mode": str(item.get("query_mode") or "").strip(),
        }
        cases.append(normalized)
    return cases


def _load_cases(profile: str, *, cases_json: str = "", cases_file: str = "") -> list[dict[str, Any]]:
    if cases_json.strip():
        return _parse_cases_payload(cases_json)
    if cases_file.strip():
        raw = Path(cases_file).expanduser().read_text(encoding="utf-8")
        return _parse_cases_payload(raw)
    return list(_profile_config(profile).get("default_cases") or [])


def _parse_args(default_profile: Optional[str] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a local-only wiki corpus smoke test.")
    parser.add_argument("--profile", choices=tuple(PROFILE_DEFAULTS.keys()), default=default_profile or "openharmony")
    bootstrap, _ = parser.parse_known_args()
    profile = bootstrap.profile
    config = _profile_config(profile)
    env_prefix = str(config["env_prefix"])

    parser.add_argument("--mode", choices=("zip", "dir", "git"), default=os.getenv(f"{env_prefix}MODE", str(config["default_mode"])))
    parser.add_argument("--wiki-url", default=os.getenv(f"{env_prefix}URL", str(config["wiki_url"])))
    parser.add_argument("--zip-path", default=os.getenv(f"{env_prefix}ZIP_PATH", ""))
    parser.add_argument("--dir-path", default=os.getenv(f"{env_prefix}DIR_PATH", ""))
    parser.add_argument("--db-path", default=os.getenv(f"{env_prefix}DB_PATH", ""))
    parser.add_argument("--json-out", default="")
    parser.add_argument(
        "--answer-mode",
        choices=("extractive", "llm"),
        default=os.getenv(f"{env_prefix}ANSWER_MODE", str(config["default_answer_mode"])),
    )
    parser.add_argument("--min-files", type=int, default=int(os.getenv(f"{env_prefix}MIN_FILES", str(config["default_min_files"]))))
    parser.add_argument("--min-chunks", type=int, default=int(os.getenv(f"{env_prefix}MIN_CHUNKS", str(config["default_min_chunks"]))))
    parser.add_argument("--top-k", type=int, default=int(os.getenv(f"{env_prefix}TOP_K", str(config["default_top_k"]))))
    parser.add_argument("--cases-json", default=os.getenv(f"{env_prefix}CASES_JSON", ""))
    parser.add_argument("--cases-file", default=os.getenv(f"{env_prefix}CASES_FILE", ""))
    parser.add_argument("--backend-url", default=os.getenv(f"{env_prefix}BACKEND_URL", ""))
    parser.add_argument("--api-key", default=os.getenv(f"{env_prefix}API_KEY", os.getenv("BACKEND_API_KEY", "")))
    return parser.parse_args()


def _prepare_env(db_path: str) -> None:
    os.environ["MYSQL_URL"] = ""
    os.environ["DB_PATH"] = db_path
    os.environ.setdefault("RAG_BACKEND", "legacy")
    os.environ.setdefault("RAG_ORCHESTRATOR_V4", "true")
    os.environ.setdefault("RAG_LEGACY_QUERY_HEURISTICS", "false")
    os.environ.setdefault("RAG_ENABLE_RERANK", "false")


def _materialize_zip_input(args: argparse.Namespace) -> tuple[str, tempfile.TemporaryDirectory[str] | None]:
    if args.mode == "zip":
        zip_path = Path(args.zip_path).expanduser().resolve()
        if not zip_path.exists():
            raise FileNotFoundError(f"ZIP corpus not found: {zip_path}")
        return str(zip_path), None
    if args.mode != "dir":
        return "", None
    dir_path = Path(args.dir_path).expanduser().resolve()
    if not dir_path.exists() or not dir_path.is_dir():
        raise FileNotFoundError(f"Directory corpus not found: {dir_path}")
    temp_dir = tempfile.TemporaryDirectory(prefix=f"{args.profile}_dir_smoke_")
    archive_path = Path(temp_dir.name) / "wiki.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in sorted(dir_path.rglob("*")):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(dir_path).as_posix())
    return str(archive_path), temp_dir


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


def _run_local_queries(*, args: argparse.Namespace, kb_id: int, cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from shared.database import get_session
    from backend.api.routes import rag as rag_module
    from backend.schemas.rag import RAGQuery

    if args.answer_mode == "extractive":
        rag_module.ai_manager.query = staticmethod(  # type: ignore[assignment]
            lambda _prompt: f"Ошибка подключения к Ollama: forced {args.profile} smoke"
        )

    query_results: list[dict[str, Any]] = []
    with get_session() as db:
        for case in cases:
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
                    "request_id": str(getattr(answer, "request_id", "") or ""),
                }
            )
    return query_results


def _run_remote_queries(*, args: argparse.Namespace, kb_id: int, cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    import httpx

    base_url = str(args.backend_url or "").rstrip("/")
    if not base_url:
        raise ValueError("backend_url is required for remote smoke mode")
    url = f"{base_url}/api/v1/rag/query"
    headers = {}
    if args.api_key:
        headers["X-API-Key"] = str(args.api_key)

    query_results: list[dict[str, Any]] = []
    timeout = httpx.Timeout(120.0, connect=15.0)
    with httpx.Client(timeout=timeout) as client:
        for case in cases:
            response = client.post(
                url,
                headers=headers,
                json={
                    "query": case["query"],
                    "knowledge_base_id": kb_id,
                    "top_k": args.top_k,
                },
            )
            response.raise_for_status()
            payload = response.json()
            sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
            top_sources = []
            top_sections = []
            for source in sources:
                if not isinstance(source, dict):
                    continue
                path = str(source.get("source_path") or "").strip()
                section = str(source.get("section_title") or "").strip()
                if path:
                    top_sources.append(path)
                if section:
                    top_sections.append(section)
            query_results.append(
                {
                    "query": case["query"],
                    "top_sources": top_sources[: args.top_k],
                    "top_sections": top_sections[: args.top_k],
                    "answer_preview": str(payload.get("answer") or "").strip()[:1200],
                    "answer_sources": top_sources,
                    "request_id": str(payload.get("request_id") or ""),
                }
            )
    return query_results


def _run_smoke(args: argparse.Namespace, db_path: str) -> dict[str, Any]:
    _prepare_env(db_path)

    from shared.rag_system import rag_system
    from shared.wiki_git_loader import load_wiki_from_git, load_wiki_from_zip

    cases = _load_cases(args.profile, cases_json=args.cases_json, cases_file=args.cases_file)
    kb_name = f"{args.profile}-local-smoke-{int(time.time())}"
    kb = rag_system.add_knowledge_base(kb_name, "local-only smoke")
    kb_id = int(kb.id)

    temp_zip_dir: tempfile.TemporaryDirectory[str] | None = None
    try:
        if args.mode in {"zip", "dir"}:
            zip_path, temp_zip_dir = _materialize_zip_input(args)
            ingest_stats = load_wiki_from_zip(
                zip_path=zip_path,
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
    finally:
        if temp_zip_dir is not None:
            temp_zip_dir.cleanup()

    rag_system._load_index(kb_id)

    if args.backend_url:
        query_results = _run_remote_queries(args=args, kb_id=kb_id, cases=cases)
    else:
        query_results = _run_local_queries(args=args, kb_id=kb_id, cases=cases)

    failures: list[str] = []
    files_processed = int(ingest_stats.get("files_processed", 0) or 0)
    chunks_added = int(ingest_stats.get("chunks_added", 0) or 0)
    if files_processed < args.min_files:
        failures.append(f"files_processed {files_processed} < {args.min_files}")
    if chunks_added < args.min_chunks:
        failures.append(f"chunks_added {chunks_added} < {args.min_chunks}")

    # Per-class counters: {class: {"pass": int, "fail": int, "family_hit": int}}
    class_breakdown: dict[str, dict[str, int]] = {}

    for case, result in zip(cases, query_results):
        failure_class = str(case.get("failure_class") or "").strip() or "unclassified"
        if failure_class not in class_breakdown:
            class_breakdown[failure_class] = {"pass": 0, "fail": 0, "family_hit": 0}

        case_pass = True
        expected_source = str(case.get("expected_source_fragment") or "").strip()
        if expected_source:
            source_hits = [path for path in result["top_sources"] if expected_source in path]
            if not source_hits:
                failures.append(f"[{failure_class}] {case['query']}: expected source fragment `{expected_source}` not found in top sources")
                case_pass = False

        expected_family = str(case.get("expected_family_fragment") or "").strip()
        if expected_family:
            family_hits = [path for path in result["top_sources"] if expected_family in path]
            if family_hits:
                class_breakdown[failure_class]["family_hit"] += 1
            elif case_pass:
                failures.append(f"[{failure_class}] {case['query']}: expected family fragment `{expected_family}` not found in top sources")
                case_pass = False

        expected_answer_fragments = [str(fragment).strip() for fragment in (case.get("expected_answer_fragments") or []) if str(fragment).strip()]
        if expected_answer_fragments:
            answer_text = result["answer_preview"].lower()
            if not any(fragment.lower() in answer_text for fragment in expected_answer_fragments):
                failures.append(
                    f"[{failure_class}] {case['query']}: answer preview missing expected fragments {expected_answer_fragments}"
                )
                case_pass = False

        if case_pass:
            class_breakdown[failure_class]["pass"] += 1
        else:
            class_breakdown[failure_class]["fail"] += 1

    return {
        "ok": not failures,
        "profile": args.profile,
        "mode": args.mode,
        "answer_mode": args.answer_mode,
        "backend_url": str(args.backend_url or ""),
        "knowledge_base_id": kb_id,
        "db_path": db_path,
        "wiki_url": args.wiki_url,
        "ingest": {
            "files_processed": files_processed,
            "chunks_added": chunks_added,
            "wiki_root": str(ingest_stats.get("wiki_root") or ""),
        },
        "cases_count": len(cases),
        "queries": query_results,
        "failures": failures,
        "failure_class_breakdown": class_breakdown,
    }


def _emit_payload(payload: str) -> None:
    text = f"{payload}\n"
    stdout = sys.stdout
    try:
        stdout.write(text)
    except UnicodeEncodeError:
        buffer = getattr(stdout, "buffer", None)
        if buffer is not None:
            buffer.write(text.encode("utf-8", errors="replace"))
        else:
            escaped = text.encode("ascii", errors="backslashreplace").decode("ascii")
            stdout.write(escaped)


def main(default_profile: Optional[str] = None) -> int:
    args = _parse_args(default_profile=default_profile)
    temp_dir_obj: tempfile.TemporaryDirectory[str] | None = None
    if args.db_path:
        db_path = str(Path(args.db_path).expanduser().resolve())
    else:
        temp_dir_obj = tempfile.TemporaryDirectory(prefix=f"{args.profile}_smoke_")
        db_path = str(Path(temp_dir_obj.name) / "smoke.db")

    try:
        result = _run_smoke(args, db_path)
        payload = json.dumps(result, ensure_ascii=False, indent=2)
        if args.json_out:
            Path(args.json_out).write_text(payload, encoding="utf-8")
        _emit_payload(payload)
        return 0 if result.get("ok") else 1
    finally:
        if temp_dir_obj is not None:
            try:
                temp_dir_obj.cleanup()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
