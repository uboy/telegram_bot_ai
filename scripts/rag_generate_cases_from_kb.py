#!/usr/bin/env python3
"""Generate comparator cases from existing KB chunks."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Dict, List


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate RAG eval cases from KB chunks")
    parser.add_argument("--kb-id", type=int, required=True)
    parser.add_argument("--out", required=True, help="Output JSON/YAML path (JSON content)")
    parser.add_argument("--source-like", default="", help="Optional source_path substring filter")
    parser.add_argument("--max-cases", type=int, default=5)
    parser.add_argument("--min-query-words", type=int, default=4)
    parser.add_argument("--scan-limit", type=int, default=200)
    return parser.parse_args()


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _first_sentence(value: str) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    # Keep the first sentence-like segment for retrieval query seeding.
    parts = re.split(r"(?<=[\.\!\?\n])\s+", text, maxsplit=1)
    return parts[0].strip() if parts else text


def _build_query(content: str, *, min_words: int) -> str:
    sentence = _first_sentence(content)
    candidate = sentence or _clean_text(content)
    words = candidate.split()
    if len(words) < min_words:
        candidate = _clean_text(content)
        words = candidate.split()
    if len(words) < min_words:
        return ""
    if len(words) > 16:
        candidate = " ".join(words[:16])
    return candidate.strip(" \t\r\n.,:;\"'")


def _source_label(path: str) -> str:
    p = str(path or "").strip()
    if not p:
        return ""
    base = os.path.basename(p)
    return base or p


def main() -> int:
    try:
        from sqlalchemy import func
        from shared.database import KnowledgeChunk, get_session
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] missing DB dependencies for case generation: {exc}")
        return 2

    args = _parse_args()
    out_path = Path(args.out)
    kb_id = int(args.kb_id)
    source_like = _clean_text(args.source_like).lower()
    max_cases = max(1, int(args.max_cases))
    min_query_words = max(1, int(args.min_query_words))
    scan_limit = max(max_cases, int(args.scan_limit))

    with get_session() as session:
        base_q = (
            session.query(KnowledgeChunk)
            .filter(KnowledgeChunk.knowledge_base_id == kb_id)
            .filter(KnowledgeChunk.is_deleted.is_(False))
            .filter(KnowledgeChunk.content.isnot(None))
        )

        rows = []
        if source_like:
            rows = (
                base_q.filter(func.lower(KnowledgeChunk.source_path).like(f"%{source_like}%"))
                .order_by(KnowledgeChunk.id.asc())
                .limit(scan_limit)
                .all()
            )

        if not rows:
            rows = base_q.order_by(KnowledgeChunk.id.asc()).limit(scan_limit).all()

    used_queries = set()
    cases: List[Dict[str, object]] = []
    for idx, row in enumerate(rows, start=1):
        query = _build_query(str(row.content or ""), min_words=min_query_words)
        if not query:
            continue
        qkey = query.lower()
        if qkey in used_queries:
            continue
        used_queries.add(qkey)
        cases.append(
            {
                "id": f"auto_{idx:03d}",
                "query": query,
                "expected_source": _source_label(str(row.source_path or "")),
                "expected_snippets": [],
            }
        )
        if len(cases) >= max_cases:
            break

    if not cases:
        print(f"[FAIL] failed to generate cases from KB {kb_id}: no suitable chunks")
        return 2

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[INFO] generated {len(cases)} cases from KB {kb_id}"
        + (f" (source_like={source_like})" if source_like else "")
    )
    print(f"CASES_FILE={out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
