#!/usr/bin/env python3
"""Backend RAG API smoke test runner.

Usage examples:
  python scripts/rag_api_smoke_test.py --base-url http://localhost:8000 --api-key xxx --kb-id 1
  python scripts/rag_api_smoke_test.py --cases-file tests/rag_api_cases.json --fail-on-empty
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import Any

import requests


DEFAULT_CASES = [
    {"query": "Как в документе определяется разметка данных?"},
    {"query": "Какие ключевые задачи развития искусственного интеллекта указаны в пункте 26?"},
]


@dataclass
class SmokeResult:
    query: str
    ok: bool
    details: str


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run smoke tests against backend /rag/query API.")
    parser.add_argument("--base-url", default=os.getenv("BACKEND_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--api-prefix", default=os.getenv("BACKEND_API_PREFIX", "/api/v1"))
    parser.add_argument("--api-key", default=os.getenv("BACKEND_API_KEY", ""))
    parser.add_argument("--kb-id", type=int, default=None, help="Knowledge base ID (if omitted, first KB is used).")
    parser.add_argument("--timeout-sec", type=float, default=20.0)
    parser.add_argument("--cases-file", default="", help="Path to JSON array with fields: query, must_contain(optional).")
    parser.add_argument("--fail-on-empty", action="store_true", help="Fail when answer is empty.")
    return parser.parse_args()


def _build_headers(api_key: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


def _load_cases(path: str) -> list[dict[str, Any]]:
    if not path:
        return list(DEFAULT_CASES)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("cases-file must contain JSON list")
    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict) or not item.get("query"):
            continue
        out.append(item)
    if not out:
        raise ValueError("cases-file has no valid cases")
    return out


def _get_kb_id(base: str, headers: dict[str, str], timeout_sec: float) -> int:
    resp = requests.get(f"{base}/knowledge-bases/", headers=headers, timeout=timeout_sec)
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("No knowledge bases found in backend.")
    kb_id = payload[0].get("id")
    if not kb_id:
        raise RuntimeError("First knowledge base has no id.")
    return int(kb_id)


def _run_case(
    *,
    base: str,
    headers: dict[str, str],
    kb_id: int,
    timeout_sec: float,
    case: dict[str, Any],
    fail_on_empty: bool,
) -> SmokeResult:
    query = str(case["query"]).strip()
    payload = {
        "query": query,
        "knowledge_base_id": kb_id,
    }
    resp = requests.post(f"{base}/rag/query", headers=headers, json=payload, timeout=timeout_sec)
    if resp.status_code != 200:
        return SmokeResult(query=query, ok=False, details=f"HTTP {resp.status_code}: {resp.text[:300]}")

    data = resp.json() if resp.content else {}
    answer = str(data.get("answer") or "").strip()
    sources = data.get("sources") or []
    must_contain = case.get("must_contain") or []

    if fail_on_empty and not answer:
        return SmokeResult(query=query, ok=False, details="Empty answer")

    if must_contain:
        missing = [x for x in must_contain if x not in answer]
        if missing:
            return SmokeResult(query=query, ok=False, details=f"Missing expected snippets: {missing}")

    return SmokeResult(
        query=query,
        ok=True,
        details=f"answer_len={len(answer)}, sources={len(sources)}",
    )


def main() -> int:
    args = _parse_args()
    base = args.base_url.rstrip("/") + "/" + args.api_prefix.strip("/")
    headers = _build_headers(args.api_key)
    cases = _load_cases(args.cases_file)

    try:
        kb_id = int(args.kb_id) if args.kb_id else _get_kb_id(base, headers, args.timeout_sec)
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] KB resolve error: {e}")
        return 2

    print(f"[INFO] base={base} kb_id={kb_id} cases={len(cases)}")
    failed = 0
    for idx, case in enumerate(cases, start=1):
        result = _run_case(
            base=base,
            headers=headers,
            kb_id=kb_id,
            timeout_sec=args.timeout_sec,
            case=case,
            fail_on_empty=bool(args.fail_on_empty),
        )
        tag = "PASS" if result.ok else "FAIL"
        print(f"[{tag}] {idx}. {result.query} :: {result.details}")
        if not result.ok:
            failed += 1

    if failed:
        print(f"[SUMMARY] failed={failed}/{len(cases)}")
        return 1
    print(f"[SUMMARY] all passed ({len(cases)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

