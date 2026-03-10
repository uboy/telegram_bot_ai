#!/usr/bin/env python3
"""Run RAG eval and persist baseline report artifacts (JSON + Markdown)."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from backend.services.rag_eval_service import rag_eval_service


def _canonicalize_slice_name(value: str) -> str:
    token = str(value or "").strip().lower()
    if not token:
        return ""
    token = token.replace(" ", "_")
    aliases = {
        "long_context": "long-context",
        "refusal_expected": "refusal-expected",
        "direct_prompt_injection": "direct_injection",
        "indirect_prompt_injection": "indirect_injection",
    }
    return aliases.get(token, token)


def _parse_slices(raw: Optional[str]) -> Optional[List[str]]:
    if not raw:
        return None
    values = []
    for part in str(raw).split(","):
        token = _canonicalize_slice_name(part)
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


def _slice_summary_rows(status: Dict[str, Any], *, allowed_slices: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
    metrics = status.get("metrics") or {}
    slice_summary = metrics.get("slice_summary") if isinstance(metrics, dict) else None
    if not isinstance(slice_summary, dict):
        return []

    allowed = {str(item or "").strip() for item in (allowed_slices or []) if str(item or "").strip()}
    rows: List[Dict[str, Any]] = []
    for slice_name, payload in slice_summary.items():
        if allowed and str(slice_name) not in allowed:
            continue
        if not isinstance(payload, dict):
            continue
        metrics_payload = payload.get("metrics") if isinstance(payload.get("metrics"), dict) else {}
        rows.append(
            {
                "slice_name": str(slice_name or ""),
                "sample_size": int(payload.get("sample_size") or 0),
                "metrics": {
                    str(metric_name): float(metric_value or 0.0)
                    for metric_name, metric_value in metrics_payload.items()
                },
            }
        )
    rows.sort(key=lambda row: row["slice_name"])
    return rows


def _metric_columns(rows: List[Dict[str, Any]]) -> List[str]:
    preferred = [
        "recall_at_10",
        "mrr_at_10",
        "ndcg_at_10",
        "faithfulness",
        "response_relevancy",
        "answer_correctness",
        "citation_validity",
        "refusal_accuracy",
        "security_resilience",
    ]
    seen = set()
    columns: List[str] = []
    for metric_name in preferred:
        if any(metric_name in (row.get("metrics") or {}) for row in rows):
            columns.append(metric_name)
            seen.add(metric_name)
    for row in rows:
        for metric_name in sorted((row.get("metrics") or {}).keys()):
            if metric_name in seen:
                continue
            seen.add(metric_name)
            columns.append(metric_name)
    return columns


def _metric_display_name(metric_name: str) -> str:
    mapping = {
        "recall_at_10": "Recall@10",
        "mrr_at_10": "MRR@10",
        "ndcg_at_10": "NDCG@10",
        "response_relevancy": "Response Relevancy",
        "answer_correctness": "Answer Correctness",
        "citation_validity": "Citation Validity",
        "refusal_accuracy": "Refusal Accuracy",
        "security_resilience": "Security Resilience",
    }
    return mapping.get(metric_name, metric_name)


def _render_slice_summary_table(title: str, rows: List[Dict[str, Any]]) -> List[str]:
    if not rows:
        return []
    metric_names = _metric_columns(rows)
    header = "| Slice | Sample Size | " + " | ".join(_metric_display_name(name) for name in metric_names) + " |"
    divider = "|---|---:|" + "".join(["---:|" for _ in metric_names])
    lines = [title, "", header, divider]
    for row in rows:
        metrics = row.get("metrics") or {}
        metric_cells = " | ".join(f"{float(metrics.get(metric_name) or 0.0):.4f}" for metric_name in metric_names)
        lines.append(
            f"| {row['slice_name']} | {row['sample_size']} | {metric_cells} |"
        )
    lines.append("")
    return lines


def _build_trend_entry(status: Dict[str, Any], *, label: str) -> Dict[str, Any]:
    metrics = status.get("metrics") or {}
    overall = {}
    slice_summary = metrics.get("slice_summary") if isinstance(metrics, dict) else {}
    if isinstance(slice_summary, dict):
        overall = slice_summary.get("overall") if isinstance(slice_summary.get("overall"), dict) else {}
    overall_metrics = overall.get("metrics") if isinstance(overall.get("metrics"), dict) else {}
    return {
        "label": label,
        "run_id": str(status.get("run_id") or ""),
        "suite_name": str(status.get("suite_name") or ""),
        "status": str(status.get("status") or ""),
        "finished_at": _coerce_iso(status.get("finished_at")),
        "dataset_version": str(metrics.get("dataset_version") or ""),
        "source_manifest_version": str(metrics.get("source_manifest_version") or ""),
        "answer_model": str(metrics.get("answer_model") or ""),
        "judge_model": str(metrics.get("judge_model") or ""),
        "git_sha": str(metrics.get("git_sha") or ""),
        "git_dirty": bool(metrics.get("git_dirty")),
        "total_cases": int(metrics.get("total_cases") or 0),
        "source_families": list(metrics.get("source_families") or []),
        "security_scenarios": list(metrics.get("security_scenarios") or []),
        "overall_metrics": {
            str(metric_name): float(metric_value or 0.0)
            for metric_name, metric_value in overall_metrics.items()
        },
    }


def write_report_artifacts(
    status: Dict[str, Any],
    *,
    out_dir: Path,
    label: str,
    stamp: str,
) -> Dict[str, Path]:
    safe_label = _safe_name(label)
    run_id = str(status.get("run_id") or "unknown-run")

    run_dir = out_dir / "runs" / safe_label
    latest_dir = out_dir / "latest"
    trends_dir = out_dir / "trends"
    run_dir.mkdir(parents=True, exist_ok=True)
    latest_dir.mkdir(parents=True, exist_ok=True)
    trends_dir.mkdir(parents=True, exist_ok=True)

    json_path = run_dir / f"{stamp}_{run_id}.json"
    md_path = run_dir / f"{stamp}_{run_id}.md"
    latest_json = latest_dir / f"{safe_label}.json"
    latest_md = latest_dir / f"{safe_label}.md"
    trend_path = trends_dir / f"{safe_label}.jsonl"

    json_payload = json.dumps(status, ensure_ascii=False, indent=2, default=_coerce_iso)
    markdown_payload = render_markdown_report(status)
    trend_payload = json.dumps(_build_trend_entry(status, label=safe_label), ensure_ascii=False)

    json_path.write_text(json_payload, encoding="utf-8")
    md_path.write_text(markdown_payload, encoding="utf-8")
    latest_json.write_text(json_payload, encoding="utf-8")
    latest_md.write_text(markdown_payload, encoding="utf-8")
    with trend_path.open("a", encoding="utf-8") as f:
        f.write(trend_payload + "\n")

    return {
        "json": json_path,
        "md": md_path,
        "latest_json": latest_json,
        "latest_md": latest_md,
        "trend_jsonl": trend_path,
    }


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
    knowledge_base_id = metrics.get("knowledge_base_id") if isinstance(metrics, dict) else None
    slices = metrics.get("slices") if isinstance(metrics, dict) else []
    thresholds = metrics.get("thresholds") if isinstance(metrics, dict) else {}
    dataset_version = str(metrics.get("dataset_version") or "") if isinstance(metrics, dict) else ""
    source_manifest_version = str(metrics.get("source_manifest_version") or "") if isinstance(metrics, dict) else ""
    source_families = list(metrics.get("source_families") or []) if isinstance(metrics, dict) else []
    security_scenarios = list(metrics.get("security_scenarios") or []) if isinstance(metrics, dict) else []
    failure_modes = list(metrics.get("failure_modes") or []) if isinstance(metrics, dict) else []
    available_metrics = list(metrics.get("available_metrics") or []) if isinstance(metrics, dict) else []
    answer_provider = str(metrics.get("answer_provider") or "") if isinstance(metrics, dict) else ""
    judge_provider = str(metrics.get("judge_provider") or "") if isinstance(metrics, dict) else ""
    answer_model = str(metrics.get("answer_model") or "") if isinstance(metrics, dict) else ""
    judge_model = str(metrics.get("judge_model") or "") if isinstance(metrics, dict) else ""
    effective_ollama_base_url = str(metrics.get("effective_ollama_base_url") or "") if isinstance(metrics, dict) else ""
    git_sha = str(metrics.get("git_sha") or "") if isinstance(metrics, dict) else ""
    git_dirty = bool(metrics.get("git_dirty")) if isinstance(metrics, dict) else False
    screening_summary = metrics.get("screening_summary") if isinstance(metrics, dict) and isinstance(metrics.get("screening_summary"), dict) else {}
    security_summary = metrics.get("security_summary") if isinstance(metrics, dict) and isinstance(metrics.get("security_summary"), dict) else {}
    case_failures = list(metrics.get("case_failures") or []) if isinstance(metrics, dict) else []
    case_analysis = list(metrics.get("case_analysis") or []) if isinstance(metrics, dict) else []
    suspicious_events = list(metrics.get("suspicious_events") or []) if isinstance(metrics, dict) else []

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
    if knowledge_base_id is not None:
        lines.append(f"- knowledge_base_id: `{knowledge_base_id}`")
    if dataset_version:
        lines.append(f"- dataset_version: `{dataset_version}`")
    if source_manifest_version:
        lines.append(f"- source_manifest_version: `{source_manifest_version}`")
    if source_families:
        lines.append(f"- source_families: `{', '.join(str(item) for item in source_families)}`")
    if security_scenarios:
        lines.append(f"- security_scenarios: `{', '.join(str(item) for item in security_scenarios)}`")
    if failure_modes:
        lines.append(f"- failure_modes: `{', '.join(str(item) for item in failure_modes)}`")
    if available_metrics:
        lines.append(f"- available_metrics: `{', '.join(str(item) for item in available_metrics)}`")
    if answer_provider:
        lines.append(f"- answer_provider: `{answer_provider}`")
    if judge_provider:
        lines.append(f"- judge_provider: `{judge_provider}`")
    if answer_model:
        lines.append(f"- answer_model: `{answer_model}`")
    if judge_model:
        lines.append(f"- judge_model: `{judge_model}`")
    if effective_ollama_base_url:
        lines.append(f"- effective_ollama_base_url: `{effective_ollama_base_url}`")
    if git_sha:
        lines.append(f"- git_sha: `{git_sha}`")
        lines.append(f"- git_dirty: `{str(git_dirty).lower()}`")
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
    lines.extend(_render_slice_summary_table("## Slice Summary", _slice_summary_rows(status)))
    lines.extend(
        _render_slice_summary_table("## Source Families", _slice_summary_rows(status, allowed_slices=source_families))
    )
    lines.extend(
        _render_slice_summary_table(
            "## Security Scenarios",
            _slice_summary_rows(status, allowed_slices=security_scenarios),
        )
    )
    lines.extend(
        _render_slice_summary_table(
            "## Failure Modes",
            _slice_summary_rows(status, allowed_slices=failure_modes),
        )
    )
    if screening_summary:
        lines.append("## Screening Summary")
        lines.append("")
        lines.append(f"- accepted: `{int(screening_summary.get('accepted') or 0)}`")
        lines.append(f"- flagged: `{int(screening_summary.get('flagged') or 0)}`")
        lines.append(f"- quarantined: `{int(screening_summary.get('quarantined') or 0)}`")
        lines.append("")
    if security_summary:
        lines.append("## Security Summary")
        lines.append("")
        lines.append(f"- suspicious_events: `{int(security_summary.get('suspicious_events') or 0)}`")
        lines.append(f"- case_failures: `{int(security_summary.get('case_failures') or 0)}`")
        lines.append("")
    if case_failures:
        lines.append("## Case Failures")
        lines.append("")
        for item in case_failures[:20]:
            case_id = str(item.get("case_id") or "")
            reasons = ", ".join(str(reason) for reason in (item.get("reasons") or []))
            lines.append(f"- `{case_id}`: {reasons}")
        lines.append("")
    if case_analysis:
        lines.append("## Answer Failure Analysis")
        lines.append("")
        for item in case_analysis[:20]:
            case_id = str(item.get("case_id") or "")
            source_family = str(item.get("source_family") or "")
            expected_mode = str(item.get("expected_answer_mode") or "")
            reasons = ", ".join(str(reason) for reason in (item.get("failure_reasons") or [])) or "-"
            events = ", ".join(str(event) for event in (item.get("suspicious_events") or [])) or "-"
            query_preview = str(item.get("query_preview") or "")
            answer_preview = str(item.get("answer_preview") or "")
            source_paths = ", ".join(str(path) for path in (item.get("source_paths") or [])) or "-"
            metric_pairs = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
            metric_summary = ", ".join(
                f"{name}={float(value or 0.0):.2f}"
                for name, value in metric_pairs.items()
            ) or "-"
            judge_notes = str(item.get("judge_notes") or "")
            lines.append(
                f"- `{case_id}` family=`{source_family}` mode=`{expected_mode}` reasons=`{reasons}` events=`{events}`"
            )
            lines.append(f"  query: `{query_preview}`")
            lines.append(f"  answer: `{answer_preview}`")
            lines.append(f"  sources: `{source_paths}`")
            lines.append(f"  metrics: `{metric_summary}`")
            if judge_notes:
                lines.append(f"  judge_notes: `{judge_notes}`")
            lines.append(
                f"  latency_ms: `answer={int(item.get('answer_latency_ms') or 0)}`, `judge={int(item.get('judge_latency_ms') or 0)}`"
            )
        lines.append("")
    if suspicious_events:
        lines.append("## Suspicious Events")
        lines.append("")
        for item in suspicious_events[:20]:
            case_id = str(item.get("case_id") or "")
            event = str(item.get("event") or "")
            lines.append(f"- `{case_id}`: {event}")
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
    label = str(args.label or "baseline")
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    paths = write_report_artifacts(
        status,
        out_dir=out_dir,
        label=label,
        stamp=stamp,
    )

    if args.print_json:
        print(json.dumps(status, ensure_ascii=False, indent=2, default=_coerce_iso))

    print(f"rag-eval-baseline: run_id={run_id} status={status.get('status')}")
    print(f"rag-eval-baseline: json={paths['json']}")
    print(f"rag-eval-baseline: md={paths['md']}")
    print(f"rag-eval-baseline: trend={paths['trend_jsonl']}")

    return 0 if str(status.get("status") or "").lower() == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
