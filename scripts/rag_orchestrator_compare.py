#!/usr/bin/env python3
"""Compare RAG API behavior between legacy and v4 orchestrator backends.

The script runs the same query suite against two backend endpoints (typically
legacy and v4 deployments) and produces side-by-side quality summary.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


@dataclass
class EvalCase:
    case_id: str
    query: str
    expected_source: str
    expected_snippets: List[str]


def _safe_ratio(numer: int, denom: int) -> float:
    if denom <= 0:
        return 0.0
    return float(numer / denom)


def _build_headers(api_key: str) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    token = (api_key or "").strip()
    if token:
        headers["X-API-Key"] = token
    return headers


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare legacy/v4 RAG API behavior")
    parser.add_argument("--legacy-base-url", required=True, help="Base URL for legacy orchestrator backend")
    parser.add_argument("--v4-base-url", required=True, help="Base URL for v4 orchestrator backend")
    parser.add_argument("--api-prefix", default=os.getenv("BACKEND_API_PREFIX", "/api/v1"))
    parser.add_argument("--api-key", default=os.getenv("BACKEND_API_KEY", ""), help="Fallback API key for both backends")
    parser.add_argument("--legacy-api-key", default="", help="API key override for legacy backend")
    parser.add_argument("--v4-api-key", default="", help="API key override for v4 backend")
    parser.add_argument("--kb-id", type=int, default=None, help="Knowledge base id. If omitted, first KB from legacy endpoint is used")
    parser.add_argument("--cases-file", default="tests/rag_eval.yaml", help="YAML/JSON file with test cases")
    parser.add_argument("--timeout-sec", type=float, default=20.0)
    parser.add_argument("--max-cases", type=int, default=0, help="Optional max number of cases (>0)")
    parser.add_argument("--max-source-hit-drop", type=float, default=-1.0, help="Fail if v4 source-hit drops by more than this absolute value (e.g. 0.1)")
    parser.add_argument("--json-out", default="", help="Optional path to write comparison JSON")
    parser.add_argument("--print-json", action="store_true")
    return parser.parse_args()


def _load_cases(path: str) -> List[EvalCase]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"cases file not found: {p}")

    if p.suffix.lower() == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
    else:
        try:
            import yaml  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"PyYAML is required to read {p}: {exc}") from exc
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    items: List[Dict[str, Any]]
    if isinstance(data, dict) and isinstance(data.get("test_cases"), list):
        items = data.get("test_cases") or []
    elif isinstance(data, list):
        items = data
    else:
        raise ValueError("Unsupported cases format. Expected list or dict with test_cases list.")

    out: List[EvalCase] = []
    for idx, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        query = str(item.get("query") or "").strip()
        if not query:
            continue
        out.append(
            EvalCase(
                case_id=str(item.get("id") or f"case_{idx}"),
                query=query,
                expected_source=str(item.get("expected_source") or "").strip(),
                expected_snippets=[
                    str(sn).strip()
                    for sn in (item.get("expected_snippets") or [])
                    if str(sn).strip()
                ],
            )
        )
    if not out:
        raise ValueError("No valid test cases found")
    return out


def _resolve_kb_id(base_api: str, headers: Dict[str, str], timeout_sec: float) -> int:
    resp = requests.get(f"{base_api}/knowledge-bases/", headers=headers, timeout=timeout_sec)
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("No knowledge bases available")
    first = payload[0]
    kb_id = first.get("id") if isinstance(first, dict) else None
    if kb_id is None:
        raise RuntimeError("Failed to resolve knowledge base id")
    return int(kb_id)


def _snippet_match_count(answer: str, patterns: List[str]) -> int:
    text = answer or ""
    hits = 0
    for pattern in patterns:
        try:
            if re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL):
                hits += 1
        except re.error:
            if pattern.lower() in text.lower():
                hits += 1
    return hits


def _run_case(
    *,
    base_api: str,
    headers: Dict[str, str],
    kb_id: int,
    case: EvalCase,
    timeout_sec: float,
) -> Dict[str, Any]:
    payload = {
        "query": case.query,
        "knowledge_base_id": kb_id,
    }
    response = requests.post(f"{base_api}/rag/query", headers=headers, json=payload, timeout=timeout_sec)
    if response.status_code != 200:
        return {
            "case_id": case.case_id,
            "query": case.query,
            "ok": False,
            "http_status": int(response.status_code),
            "error": (response.text or "")[:500],
            "answer_len": 0,
            "source_hit": False,
            "snippet_hits": 0,
            "snippet_total": len(case.expected_snippets),
            "request_id": None,
            "orchestrator_mode": None,
            "degraded_mode": None,
            "total_selected": None,
        }

    data = response.json() if response.content else {}
    answer = str(data.get("answer") or "")
    sources = data.get("sources") or []
    request_id = str(data.get("request_id") or "") or None

    source_paths = []
    for item in sources:
        if isinstance(item, dict):
            source_paths.append(str(item.get("source_path") or ""))

    expected_source = case.expected_source.lower()
    source_hit = bool(expected_source and any(expected_source in path.lower() for path in source_paths))
    snippet_hits = _snippet_match_count(answer, case.expected_snippets)

    orchestrator_mode = None
    degraded_mode = None
    total_selected = None
    if request_id:
        try:
            diag = requests.get(f"{base_api}/rag/diagnostics/{request_id}", headers=headers, timeout=timeout_sec)
            if diag.status_code == 200:
                diag_data = diag.json() if diag.content else {}
                mode_raw = diag_data.get("orchestrator_mode")
                if mode_raw is not None:
                    token = str(mode_raw).strip().lower()
                    orchestrator_mode = token or None
                if "degraded_mode" in diag_data:
                    degraded_mode = bool(diag_data.get("degraded_mode"))
                if "total_selected" in diag_data:
                    try:
                        total_selected = int(diag_data.get("total_selected") or 0)
                    except Exception:
                        total_selected = None
        except Exception:
            pass

    return {
        "case_id": case.case_id,
        "query": case.query,
        "ok": True,
        "http_status": 200,
        "error": None,
        "answer_len": len(answer.strip()),
        "source_hit": source_hit,
        "snippet_hits": int(snippet_hits),
        "snippet_total": len(case.expected_snippets),
        "request_id": request_id,
        "orchestrator_mode": orchestrator_mode,
        "degraded_mode": degraded_mode,
        "total_selected": total_selected,
    }


def _summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(rows)
    ok_count = sum(1 for row in rows if bool(row.get("ok")))
    non_empty = sum(1 for row in rows if int(row.get("answer_len") or 0) > 0)
    source_hit = sum(1 for row in rows if bool(row.get("source_hit")))
    snippet_hits = sum(int(row.get("snippet_hits") or 0) for row in rows)
    snippet_total = sum(int(row.get("snippet_total") or 0) for row in rows)

    degraded_known = [row for row in rows if row.get("degraded_mode") is not None]
    degraded_true = sum(1 for row in degraded_known if bool(row.get("degraded_mode")))

    return {
        "cases_total": total,
        "ok_rate": _safe_ratio(ok_count, total),
        "non_empty_rate": _safe_ratio(non_empty, total),
        "source_hit_rate": _safe_ratio(source_hit, total),
        "snippet_hit_rate": _safe_ratio(snippet_hits, snippet_total),
        "degraded_mode_rate": _safe_ratio(degraded_true, len(degraded_known)),
    }


def _run_mode(
    *,
    mode_name: str,
    base_api: str,
    headers: Dict[str, str],
    kb_id: int,
    cases: List[EvalCase],
    timeout_sec: float,
) -> Dict[str, Any]:
    rows = [
        _run_case(
            base_api=base_api,
            headers=headers,
            kb_id=kb_id,
            case=case,
            timeout_sec=timeout_sec,
        )
        for case in cases
    ]
    return {
        "mode": mode_name,
        "summary": _summarize(rows),
        "cases": rows,
    }


def _print_summary(report: Dict[str, Any]) -> None:
    legacy = report.get("legacy", {}).get("summary", {})
    v4 = report.get("v4", {}).get("summary", {})
    print("[COMPARE] legacy vs v4")
    print(
        "[SUMMARY] "
        f"legacy source_hit={legacy.get('source_hit_rate', 0.0):.3f} "
        f"non_empty={legacy.get('non_empty_rate', 0.0):.3f} "
        f"snippet={legacy.get('snippet_hit_rate', 0.0):.3f}"
    )
    print(
        "[SUMMARY] "
        f"v4     source_hit={v4.get('source_hit_rate', 0.0):.3f} "
        f"non_empty={v4.get('non_empty_rate', 0.0):.3f} "
        f"snippet={v4.get('snippet_hit_rate', 0.0):.3f}"
    )
    print(
        "[DELTA] "
        f"source_hit={report.get('delta', {}).get('source_hit_rate', 0.0):+.3f} "
        f"non_empty={report.get('delta', {}).get('non_empty_rate', 0.0):+.3f} "
        f"snippet={report.get('delta', {}).get('snippet_hit_rate', 0.0):+.3f}"
    )


def main() -> int:
    args = _parse_args()

    legacy_api = args.legacy_base_url.rstrip("/") + "/" + args.api_prefix.strip("/")
    v4_api = args.v4_base_url.rstrip("/") + "/" + args.api_prefix.strip("/")

    legacy_headers = _build_headers(args.legacy_api_key or args.api_key)
    v4_headers = _build_headers(args.v4_api_key or args.api_key)

    try:
        cases = _load_cases(args.cases_file)
        if int(args.max_cases or 0) > 0:
            cases = cases[: int(args.max_cases)]
        kb_id = int(args.kb_id) if args.kb_id else _resolve_kb_id(legacy_api, legacy_headers, args.timeout_sec)
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] setup error: {exc}")
        return 2

    print(f"[INFO] legacy={legacy_api} v4={v4_api} kb_id={kb_id} cases={len(cases)}")

    legacy_report = _run_mode(
        mode_name="legacy",
        base_api=legacy_api,
        headers=legacy_headers,
        kb_id=kb_id,
        cases=cases,
        timeout_sec=args.timeout_sec,
    )
    v4_report = _run_mode(
        mode_name="v4",
        base_api=v4_api,
        headers=v4_headers,
        kb_id=kb_id,
        cases=cases,
        timeout_sec=args.timeout_sec,
    )

    legacy_summary = legacy_report.get("summary", {})
    v4_summary = v4_report.get("summary", {})
    delta = {
        "source_hit_rate": float(v4_summary.get("source_hit_rate", 0.0)) - float(legacy_summary.get("source_hit_rate", 0.0)),
        "non_empty_rate": float(v4_summary.get("non_empty_rate", 0.0)) - float(legacy_summary.get("non_empty_rate", 0.0)),
        "snippet_hit_rate": float(v4_summary.get("snippet_hit_rate", 0.0)) - float(legacy_summary.get("snippet_hit_rate", 0.0)),
    }

    report = {
        "legacy": legacy_report,
        "v4": v4_report,
        "delta": delta,
    }

    _print_summary(report)

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.print_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    max_drop = float(args.max_source_hit_drop)
    if max_drop >= 0 and float(delta.get("source_hit_rate", 0.0)) < -max_drop:
        print(
            "[FAIL] v4 source_hit drop exceeds threshold: "
            f"delta={delta.get('source_hit_rate'):.3f}, max_drop={max_drop:.3f}"
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
