#!/usr/bin/env python3
"""RAG eval quality gate with baseline delta and bootstrap CI checks."""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


DEFAULT_REQUIRED_SLICES = [
    "ru",
    "en",
    "mixed",
    "factoid",
    "howto",
    "legal",
    "numeric",
    "long-context",
]
DEFAULT_METRICS = ["recall_at_10", "mrr_at_10", "ndcg_at_10"]


def _safe_json_loads(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _parse_list_arg(raw: Optional[str], default: Sequence[str]) -> List[str]:
    if not raw:
        return [str(item).strip().lower() for item in default if str(item).strip()]
    out: List[str] = []
    for item in str(raw).split(","):
        token = item.strip().lower()
        if not token:
            continue
        if token in out:
            continue
        out.append(token)
    return out or [str(item).strip().lower() for item in default if str(item).strip()]


def _load_db_handles():
    # Lazy import avoids DB bootstrap side effects during unit-test module import.
    from shared.database import RAGEvalResult, RAGEvalRun, get_session

    return RAGEvalResult, RAGEvalRun, get_session


def _load_run_meta(run_id: str) -> Optional[Any]:
    _, eval_run_model, get_session = _load_db_handles()
    with get_session() as session:
        return session.query(eval_run_model).filter_by(run_id=run_id).first()


def _load_result_rows(run_id: str) -> Dict[Tuple[str, str], Dict[str, Any]]:
    eval_result_model, _, get_session = _load_db_handles()
    with get_session() as session:
        rows = session.query(eval_result_model).filter_by(run_id=run_id).all()
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        key = (str(row.slice_name or "").lower(), str(row.metric_name or "").lower())
        out[key] = {
            "slice_name": str(row.slice_name or "").lower(),
            "metric_name": str(row.metric_name or "").lower(),
            "metric_value": float(row.metric_value or 0.0),
            "threshold_value": (float(row.threshold_value) if row.threshold_value is not None else None),
            "passed": bool(row.passed),
            "details": _safe_json_loads(row.details_json),
        }
    return out


def _rows_from_status_payload(payload: Dict[str, Any]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    rows = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {}
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        slice_name = str(row.get("slice_name") or "").strip().lower()
        metric_name = str(row.get("metric_name") or "").strip().lower()
        if not slice_name or not metric_name:
            continue

        details: Dict[str, Any] = {}
        if isinstance(row.get("details"), dict):
            details = row.get("details") or {}
        else:
            details = _safe_json_loads(row.get("details_json"))

        out[(slice_name, metric_name)] = {
            "slice_name": slice_name,
            "metric_name": metric_name,
            "metric_value": float(row.get("metric_value") or 0.0),
            "threshold_value": (
                float(row.get("threshold_value")) if row.get("threshold_value") is not None else None
            ),
            "passed": bool(row.get("passed")),
            "details": details,
        }
    return out


def _load_status_report(path: str) -> Dict[str, Any]:
    raw = Path(path).read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("status report json must be an object")
    return data


def _extract_sample_size(details: Dict[str, Any]) -> int:
    try:
        return int(details.get("sample_size") or 0)
    except Exception:
        return 0


def _extract_values(details: Dict[str, Any]) -> List[float]:
    raw = details.get("values")
    if not isinstance(raw, list):
        return []
    out: List[float] = []
    for item in raw:
        try:
            out.append(float(item))
        except Exception:
            continue
    return out


def bootstrap_ci_delta(
    run_values: Sequence[float],
    baseline_values: Sequence[float],
    *,
    samples: int = 2000,
    seed: int = 42,
) -> Optional[Tuple[float, float]]:
    if not run_values or not baseline_values:
        return None
    n_run = len(run_values)
    n_base = len(baseline_values)
    if n_run <= 0 or n_base <= 0:
        return None

    rng = random.Random(seed)
    deltas: List[float] = []
    samples_count = max(100, int(samples))
    for _ in range(samples_count):
        sample_run = [run_values[rng.randrange(n_run)] for _ in range(n_run)]
        sample_base = [baseline_values[rng.randrange(n_base)] for _ in range(n_base)]
        delta = (sum(sample_run) / n_run) - (sum(sample_base) / n_base)
        deltas.append(delta)
    deltas.sort()
    low_idx = int(0.025 * (len(deltas) - 1))
    high_idx = int(0.975 * (len(deltas) - 1))
    return float(deltas[low_idx]), float(deltas[high_idx])


def evaluate_gate(
    *,
    run_id: str,
    run_rows: Dict[Tuple[str, str], Dict[str, Any]],
    baseline_id: Optional[str],
    baseline_rows: Optional[Dict[Tuple[str, str], Dict[str, Any]]],
    required_slices: Sequence[str],
    metrics: Sequence[str],
    thresholds: Dict[str, float],
    min_sample_size: int,
    negative_margin: float,
    bootstrap_samples: int,
    seed: int,
    allow_no_baseline: bool,
    allow_missing_bootstrap: bool,
) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    passed = True

    if not baseline_id and not allow_no_baseline:
        passed = False
        checks.append(
            {
                "type": "baseline",
                "status": "failed",
                "reason": "baseline_run_id is required for delta/CI gate",
            }
        )

    for slice_name in required_slices:
        for metric_name in metrics:
            key = (slice_name, metric_name)
            row = run_rows.get(key)
            threshold = float(thresholds.get(metric_name, 0.0))
            check: Dict[str, Any] = {
                "slice": slice_name,
                "metric": metric_name,
                "status": "passed",
                "run_value": None,
                "threshold": threshold,
                "sample_size": 0,
                "delta_vs_baseline": None,
                "ci95_delta": None,
                "reasons": [],
            }

            if row is None:
                check["status"] = "failed"
                check["reasons"].append("missing_metric_row")
                passed = False
                checks.append(check)
                continue

            run_value = float(row.get("metric_value") or 0.0)
            details = row.get("details") or {}
            sample_size = _extract_sample_size(details)

            check["run_value"] = run_value
            check["sample_size"] = sample_size

            if sample_size < int(min_sample_size):
                check["status"] = "failed"
                check["reasons"].append("sample_size_below_min")
                passed = False

            if run_value < threshold:
                check["status"] = "failed"
                check["reasons"].append("threshold_not_met")
                passed = False

            if baseline_id:
                base_row = (baseline_rows or {}).get(key) if baseline_rows else None
                if base_row is None:
                    check["status"] = "failed"
                    check["reasons"].append("missing_baseline_row")
                    passed = False
                else:
                    base_value = float(base_row.get("metric_value") or 0.0)
                    delta = run_value - base_value
                    check["delta_vs_baseline"] = delta
                    if delta < 0.0:
                        check["status"] = "failed"
                        check["reasons"].append("negative_delta_vs_baseline")
                        passed = False

                    run_values = _extract_values(details)
                    base_values = _extract_values(base_row.get("details") or {})
                    ci = bootstrap_ci_delta(
                        run_values,
                        base_values,
                        samples=bootstrap_samples,
                        seed=seed,
                    )
                    if ci is None:
                        if not allow_missing_bootstrap:
                            check["status"] = "failed"
                            check["reasons"].append("missing_bootstrap_samples")
                            passed = False
                    else:
                        check["ci95_delta"] = [ci[0], ci[1]]
                        if ci[0] < float(negative_margin):
                            check["status"] = "failed"
                            check["reasons"].append("ci_crosses_negative_margin")
                            passed = False

            checks.append(check)

    summary = {
        "run_id": run_id,
        "baseline_run_id": baseline_id,
        "required_slices": list(required_slices),
        "metrics": list(metrics),
        "min_sample_size": int(min_sample_size),
        "negative_margin": float(negative_margin),
        "passed": bool(passed),
        "checks": checks,
    }
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate RAG eval run against quality gate rules")
    parser.add_argument("--run-id", help="Target eval run_id (DB mode)")
    parser.add_argument("--run-report-json", help="Path to eval status JSON report (artifact mode)")
    parser.add_argument("--baseline-run-id", help="Baseline eval run_id (optional; defaults to run.baseline_run_id)")
    parser.add_argument("--baseline-report-json", help="Path to baseline status JSON report (artifact mode)")
    parser.add_argument("--slices", help="Comma-separated required slices")
    parser.add_argument("--metrics", help="Comma-separated metric names")
    parser.add_argument("--threshold-recall-at10", type=float, default=0.6)
    parser.add_argument("--threshold-mrr-at10", type=float, default=0.45)
    parser.add_argument("--threshold-ndcg-at10", type=float, default=0.5)
    parser.add_argument("--min-sample-size", type=int, default=100)
    parser.add_argument("--negative-margin", type=float, default=-0.01)
    parser.add_argument("--bootstrap-samples", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--allow-no-baseline", action="store_true", help="Allow threshold-only checks without baseline")
    parser.add_argument(
        "--allow-missing-bootstrap",
        action="store_true",
        help="Do not fail when per-query samples are missing for CI bootstrap",
    )
    parser.add_argument("--json-out", help="Optional path to write JSON report")
    parser.add_argument("--print-json", action="store_true", help="Print JSON report")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    run_id: Optional[str] = None
    baseline_run_id: Optional[str] = None
    run_rows: Dict[Tuple[str, str], Dict[str, Any]] = {}
    baseline_rows: Optional[Dict[Tuple[str, str], Dict[str, Any]]] = None

    if args.run_report_json:
        try:
            run_report = _load_status_report(str(args.run_report_json))
        except Exception as exc:  # noqa: BLE001
            print(f"quality-gate: failed to read run report json: {exc}")
            return 2

        run_id = str(run_report.get("run_id") or "").strip() or "report-run"
        report_status = str(run_report.get("status") or "").strip().lower()
        if report_status and report_status != "completed":
            print(f"quality-gate: run report is not completed: status={report_status}")
            return 2
        run_rows = _rows_from_status_payload(run_report)
        if not run_rows:
            print("quality-gate: run report has no metric rows")
            return 2

        baseline_run_id = (str(args.baseline_run_id).strip() if args.baseline_run_id else "") or (
            str(run_report.get("baseline_run_id") or "").strip() or None
        )

        if args.baseline_report_json:
            try:
                baseline_report = _load_status_report(str(args.baseline_report_json))
            except Exception as exc:  # noqa: BLE001
                print(f"quality-gate: failed to read baseline report json: {exc}")
                return 2
            baseline_rows = _rows_from_status_payload(baseline_report)
            if not baseline_rows:
                print("quality-gate: baseline report has no metric rows")
                return 2
            if not baseline_run_id:
                baseline_run_id = str(baseline_report.get("run_id") or "").strip() or "baseline-report"
        elif baseline_run_id:
            baseline_rows = _load_result_rows(baseline_run_id)
            if not baseline_rows:
                print(f"quality-gate: baseline run not found or has no results: {baseline_run_id}")
                return 2
    else:
        if not args.run_id:
            print("quality-gate: provide --run-id or --run-report-json")
            return 2
        run_id = str(args.run_id)

        run_meta = _load_run_meta(run_id)
        if not run_meta:
            print(f"quality-gate: run not found: {run_id}")
            return 2
        if str(run_meta.status or "").lower() != "completed":
            print(f"quality-gate: run is not completed: {run_id} status={run_meta.status}")
            return 2

        baseline_run_id = (
            str(args.baseline_run_id).strip()
            if args.baseline_run_id
            else str(run_meta.baseline_run_id or "").strip()
        ) or None

        run_rows = _load_result_rows(run_id)
        baseline_rows = _load_result_rows(baseline_run_id) if baseline_run_id else None
        if baseline_run_id and not baseline_rows:
            print(f"quality-gate: baseline run not found or has no results: {baseline_run_id}")
            return 2

    required_slices = _parse_list_arg(args.slices, DEFAULT_REQUIRED_SLICES)
    metrics = _parse_list_arg(args.metrics, DEFAULT_METRICS)
    thresholds = {
        "recall_at_10": float(args.threshold_recall_at10),
        "mrr_at_10": float(args.threshold_mrr_at10),
        "ndcg_at_10": float(args.threshold_ndcg_at10),
    }

    report = evaluate_gate(
        run_id=run_id or "unknown",
        run_rows=run_rows,
        baseline_id=baseline_run_id,
        baseline_rows=baseline_rows,
        required_slices=required_slices,
        metrics=metrics,
        thresholds=thresholds,
        min_sample_size=max(1, int(args.min_sample_size)),
        negative_margin=float(args.negative_margin),
        bootstrap_samples=max(100, int(args.bootstrap_samples)),
        seed=int(args.seed),
        allow_no_baseline=bool(args.allow_no_baseline),
        allow_missing_bootstrap=bool(args.allow_missing_bootstrap),
    )

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.print_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    failed_checks = [row for row in report.get("checks", []) if row.get("status") != "passed"]
    if report.get("passed"):
        print("quality-gate: PASS")
        return 0

    print("quality-gate: FAIL")
    print(f"run_id={report['run_id']} baseline={report.get('baseline_run_id')}")
    for row in failed_checks[:20]:
        print(
            f"- slice={row.get('slice')} metric={row.get('metric')} reasons={','.join(row.get('reasons') or [])} "
            f"run={row.get('run_value')} delta={row.get('delta_vs_baseline')} ci={row.get('ci95_delta')}"
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
