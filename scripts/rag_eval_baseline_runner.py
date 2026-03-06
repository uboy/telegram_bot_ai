#!/usr/bin/env python3
"""Run RAG eval and persist baseline report artifacts (JSON + Markdown)."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from backend.services.rag_eval_service import rag_eval_service


def _parse_slices(raw: Optional[str]) -> Optional[List[str]]:
    if not raw:
        return None
    values = []
    for part in str(raw).split(","):
        token = part.strip().lower()
        if not token:
            continue
        if token in values:
            continue
        values.append(token)
    return values or None


def _safe_name(value: str) -> str:
    out = []
    for ch in (value or ""):
        if ch.isalnum() or ch in ("-", "_", "."):
            out.append(ch)
        else:
            out.append("_")
    normalized = "".join(out).strip("_")
    return normalized or "baseline"


def _coerce_iso(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return value.isoformat()
    except Exception:
        return str(value)


def _iter_result_rows(status: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    rows = status.get("results") or []
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def render_markdown_report(status: Dict[str, Any]) -> str:
    run_id = str(status.get("run_id") or "")
    suite_name = str(status.get("suite_name") or "")
    baseline_run_id = str(status.get("baseline_run_id") or "")
    run_status = str(status.get("status") or "")
    started_at = _coerce_iso(status.get("started_at"))
    finished_at = _coerce_iso(status.get("finished_at"))
    error_message = str(status.get("error_message") or "")
    metrics = status.get("metrics") or {}
    total_cases = int(metrics.get("total_cases") or 0) if isinstance(metrics, dict) else 0
    slices = metrics.get("slices") if isinstance(metrics, dict) else []
    thresholds = metrics.get("thresholds") if isinstance(metrics, dict) else {}

    lines: List[str] = []
    lines.append("# RAG Eval Baseline Report")
    lines.append("")
    lines.append(f"- run_id: `{run_id}`")
    lines.append(f"- suite: `{suite_name}`")
    lines.append(f"- baseline_run_id: `{baseline_run_id or '-'}`")
    lines.append(f"- status: `{run_status}`")
    lines.append(f"- started_at: `{started_at or '-'}`")
    lines.append(f"- finished_at: `{finished_at or '-'}`")
    lines.append(f"- total_cases: `{total_cases}`")
    if slices:
        lines.append(f"- slices: `{', '.join(str(s) for s in slices)}`")
    if thresholds:
        lines.append(f"- thresholds: `{json.dumps(thresholds, ensure_ascii=False)}`")
    if error_message:
        lines.append(f"- error: `{error_message}`")
    lines.append("")
    lines.append("## Metrics")
    lines.append("")
    lines.append("| Slice | Metric | Value | Threshold | Passed |")
    lines.append("|---|---|---:|---:|:---:|")
    for row in sorted(
        _iter_result_rows(status),
        key=lambda r: (str(r.get("slice_name") or ""), str(r.get("metric_name") or "")),
    ):
        slice_name = str(row.get("slice_name") or "")
        metric_name = str(row.get("metric_name") or "")
        value = float(row.get("metric_value") or 0.0)
        threshold = row.get("threshold_value")
        threshold_str = f"{float(threshold):.4f}" if threshold is not None else "-"
        passed = "yes" if bool(row.get("passed")) else "no"
        lines.append(f"| {slice_name} | {metric_name} | {value:.4f} | {threshold_str} | {passed} |")
    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run RAG eval and write baseline artifacts")
    parser.add_argument("--suite", default="rag-general-v1", help="Eval suite name")
    parser.add_argument("--baseline-run-id", default=None, help="Optional baseline run id")
    parser.add_argument("--slices", default=None, help="Comma-separated slices")
    parser.add_argument("--label", default="baseline", help="Artifact label prefix")
    parser.add_argument("--out-dir", default="data/rag_eval_baseline", help="Output directory")
    parser.add_argument("--print-json", action="store_true", help="Print resulting status JSON")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    slices = _parse_slices(args.slices)
    run_id = rag_eval_service.start_run(
        suite_name=str(args.suite or "rag-general-v1"),
        baseline_run_id=(str(args.baseline_run_id).strip() or None) if args.baseline_run_id is not None else None,
        slices=slices,
        run_async=False,
    )

    status = rag_eval_service.get_run_status(run_id)
    if not status:
        print("rag-eval-baseline: run status not found")
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    label = _safe_name(str(args.label or "baseline"))
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = out_dir / f"{label}_{stamp}_{run_id}.json"
    md_path = out_dir / f"{label}_{stamp}_{run_id}.md"
    latest_json = out_dir / "latest.json"
    latest_md = out_dir / "latest.md"

    json_payload = json.dumps(status, ensure_ascii=False, indent=2, default=_coerce_iso)
    markdown_payload = render_markdown_report(status)

    json_path.write_text(json_payload, encoding="utf-8")
    md_path.write_text(markdown_payload, encoding="utf-8")
    latest_json.write_text(json_payload, encoding="utf-8")
    latest_md.write_text(markdown_payload, encoding="utf-8")

    if args.print_json:
        print(json_payload)

    print(f"rag-eval-baseline: run_id={run_id} status={status.get('status')}")
    print(f"rag-eval-baseline: json={json_path}")
    print(f"rag-eval-baseline: md={md_path}")

    return 0 if str(status.get("status") or "").lower() == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
