from __future__ import annotations

import json
import math
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from uuid import uuid4

from shared.database import RAGEvalResult, RAGEvalRun, get_session
from shared.logging_config import logger
from shared.rag_system import rag_system


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return float(default)


def _default_slices() -> List[str]:
    raw = (os.getenv("RAG_EVAL_DEFAULT_SLICES", "") or "").strip()
    if raw:
        return [item.strip().lower() for item in raw.split(",") if item.strip()]
    return ["overall", "ru", "en", "mixed", "factoid", "howto", "legal", "numeric", "long-context"]


def _thresholds() -> Dict[str, float]:
    return {
        "recall_at_10": _float_env("RAG_EVAL_THRESHOLD_RECALL_AT10", 0.6),
        "mrr_at_10": _float_env("RAG_EVAL_THRESHOLD_MRR_AT10", 0.45),
        "ndcg_at_10": _float_env("RAG_EVAL_THRESHOLD_NDCG_AT10", 0.5),
    }


def _suite_path() -> Path:
    raw = (os.getenv("RAG_EVAL_SUITE_FILE", "") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / "tests" / "rag_eval.yaml"


def _load_yaml_suite() -> List[Dict[str, Any]]:
    path = _suite_path()
    if not path.exists():
        logger.warning("RAG eval suite file not found: %s", path)
        return []

    try:
        import yaml  # type: ignore
    except Exception as exc:  # noqa: BLE001
        logger.warning("PyYAML is not available for RAG eval suite loading: %s", exc)
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to read eval suite file: %s", exc)
        return []

    if not isinstance(data, dict):
        return []
    cases = data.get("test_cases")
    if not isinstance(cases, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in cases:
        if not isinstance(item, dict):
            continue
        query = str(item.get("query") or "").strip()
        if not query:
            continue
        out.append(item)
    return out


def _detect_language_slice(text: str) -> str:
    has_cyr = bool(re.search(r"[а-яё]", text, flags=re.IGNORECASE))
    has_lat = bool(re.search(r"[a-z]", text, flags=re.IGNORECASE))
    if has_cyr and has_lat:
        return "mixed"
    if has_cyr:
        return "ru"
    if has_lat:
        return "en"
    return "mixed"


def _case_slices(case: Dict[str, Any]) -> set[str]:
    query = str(case.get("query") or "")
    q = query.lower()

    slices: set[str] = {"overall", _detect_language_slice(q)}
    if len(query) >= 140:
        slices.add("long-context")
    if re.search(r"\d", q):
        slices.add("numeric")

    if any(term in q for term in ("how to", "как ", "инструкция", "setup", "build", "install", "run")):
        slices.add("howto")
    if any(term in q for term in ("кто", "какой", "какие", "сколько", "when", "who", "what", "how often")):
        slices.add("factoid")
    if any(term in q for term in ("закон", "стратег", "policy", "regulation", "legal", "прав")):
        slices.add("legal")
    return slices


def _calc_ndcg(rank: int) -> float:
    if rank <= 0:
        return 0.0
    return float(1.0 / math.log2(rank + 1.0))


def _relevant_rank(case: Dict[str, Any], results: List[Dict[str, Any]]) -> int:
    expected_source = str(case.get("expected_source") or "").strip().lower()
    snippets = [str(s).lower() for s in (case.get("expected_snippets") or []) if str(s).strip()]

    for idx, row in enumerate(results[:10], start=1):
        source_path = str(row.get("source_path") or "").lower()
        content = str(row.get("content") or "").lower()

        if expected_source and expected_source in source_path:
            return idx
        if snippets and any(snippet in content for snippet in snippets):
            return idx
    return 0


def _evaluate_case(case: Dict[str, Any]) -> Dict[str, Any]:
    query = str(case.get("query") or "").strip()
    if not query:
        return {
            "query": "",
            "rank": 0,
            "recall_at_10": 0.0,
            "mrr_at_10": 0.0,
            "ndcg_at_10": 0.0,
            "slices": ["overall"],
        }

    try:
        results = rag_system.search(query=query, knowledge_base_id=None, top_k=10) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("Eval query failed: %s", exc)
        results = []

    rank = _relevant_rank(case, results)
    recall = 1.0 if rank > 0 else 0.0
    mrr = float(1.0 / rank) if rank > 0 else 0.0
    ndcg = _calc_ndcg(rank)
    slices = sorted(_case_slices(case))
    return {
        "query": query,
        "rank": rank,
        "recall_at_10": recall,
        "mrr_at_10": mrr,
        "ndcg_at_10": ndcg,
        "slices": slices,
    }


class RAGEvalService:
    def __init__(self) -> None:
        self._lock = threading.Lock()

    def _normalize_slices(self, slices: Optional[Sequence[str]]) -> List[str]:
        if not slices:
            return _default_slices()
        normalized = [str(item or "").strip().lower() for item in slices if str(item or "").strip()]
        if not normalized:
            return _default_slices()
        seen = set()
        out: List[str] = []
        for item in normalized:
            if item in seen:
                continue
            seen.add(item)
            out.append(item)
        if "overall" not in seen:
            out.insert(0, "overall")
        return out

    def start_run(
        self,
        *,
        suite_name: str,
        baseline_run_id: Optional[str] = None,
        slices: Optional[Sequence[str]] = None,
        run_async: bool = True,
    ) -> str:
        run_id = f"eval_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"
        slices_norm = self._normalize_slices(slices)
        with get_session() as session:
            session.add(
                RAGEvalRun(
                    run_id=run_id,
                    suite_name=(suite_name or "rag-general-v1")[:80],
                    baseline_run_id=(baseline_run_id or "")[:64] or None,
                    status="queued",
                    metrics_json=json.dumps({"slices": slices_norm}, ensure_ascii=False),
                )
            )

        if run_async:
            thread = threading.Thread(
                target=self._execute_run,
                kwargs={
                    "run_id": run_id,
                    "suite_name": suite_name,
                    "baseline_run_id": baseline_run_id,
                    "slices": slices_norm,
                },
                daemon=True,
                name=f"rag-eval-{run_id}",
            )
            thread.start()
        else:
            self._execute_run(
                run_id=run_id,
                suite_name=suite_name,
                baseline_run_id=baseline_run_id,
                slices=slices_norm,
            )
        return run_id

    def _update_run(
        self,
        *,
        run_id: str,
        status: str,
        started_at: Optional[datetime] = None,
        finished_at: Optional[datetime] = None,
        metrics: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ) -> None:
        with get_session() as session:
            run = session.query(RAGEvalRun).filter_by(run_id=run_id).first()
            if not run:
                return
            run.status = (status or "failed")[:20]
            if started_at is not None:
                run.started_at = started_at
            if finished_at is not None:
                run.finished_at = finished_at
            if metrics is not None:
                run.metrics_json = json.dumps(metrics, ensure_ascii=False, default=str)
            if error_message is not None:
                run.error_message = (error_message or "")[:4000] or None

    def _execute_run(
        self,
        *,
        run_id: str,
        suite_name: str,
        baseline_run_id: Optional[str],
        slices: Sequence[str],
    ) -> None:
        started_at = datetime.now(timezone.utc)
        self._update_run(run_id=run_id, status="running", started_at=started_at)

        try:
            cases = _load_yaml_suite()
            if not cases:
                raise RuntimeError("No eval cases found")

            case_metrics = [_evaluate_case(case) for case in cases]
            thresholds = _thresholds()
            metric_names = ["recall_at_10", "mrr_at_10", "ndcg_at_10"]

            with get_session() as session:
                session.query(RAGEvalResult).filter_by(run_id=run_id).delete(synchronize_session=False)

                slice_summary: Dict[str, Any] = {}
                for slice_name in slices:
                    slice_cases = [row for row in case_metrics if slice_name == "overall" or slice_name in (row.get("slices") or [])]
                    sample_size = len(slice_cases)
                    aggregate = {metric_name: 0.0 for metric_name in metric_names}
                    metric_values = {metric_name: [] for metric_name in metric_names}
                    if sample_size > 0:
                        for metric_name in metric_names:
                            metric_values[metric_name] = [float(row.get(metric_name, 0.0)) for row in slice_cases]
                            aggregate[metric_name] = float(
                                sum(metric_values[metric_name]) / sample_size
                            )

                    slice_summary[slice_name] = {
                        "sample_size": sample_size,
                        "metrics": aggregate,
                    }
                    for metric_name in metric_names:
                        threshold = float(thresholds.get(metric_name, 0.0))
                        value = float(aggregate.get(metric_name, 0.0))
                        passed = bool(sample_size > 0 and value >= threshold)
                        session.add(
                            RAGEvalResult(
                                run_id=run_id,
                                slice_name=slice_name[:64],
                                metric_name=metric_name[:64],
                                metric_value=value,
                                threshold_value=threshold,
                                passed=passed,
                                details_json=json.dumps(
                                    {
                                        "sample_size": sample_size,
                                        "suite_name": suite_name,
                                        "values": metric_values.get(metric_name, []),
                                    },
                                    ensure_ascii=False,
                                ),
                            )
                        )

            finished_at = datetime.now(timezone.utc)
            metrics_payload = {
                "suite_name": suite_name,
                "baseline_run_id": baseline_run_id,
                "total_cases": len(case_metrics),
                "slices": list(slices),
                "thresholds": thresholds,
                "slice_summary": slice_summary,
            }
            self._update_run(
                run_id=run_id,
                status="completed",
                finished_at=finished_at,
                metrics=metrics_payload,
                error_message=None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("RAG eval run failed run_id=%s: %s", run_id, exc, exc_info=True)
            self._update_run(
                run_id=run_id,
                status="failed",
                finished_at=datetime.now(timezone.utc),
                error_message=str(exc),
            )

    def get_run_status(self, run_id: str) -> Optional[Dict[str, Any]]:
        with get_session() as session:
            run = session.query(RAGEvalRun).filter_by(run_id=run_id).first()
            if not run:
                return None
            results = (
                session.query(RAGEvalResult)
                .filter_by(run_id=run_id)
                .order_by(RAGEvalResult.slice_name.asc(), RAGEvalResult.metric_name.asc())
                .all()
            )

        metrics_obj: Dict[str, Any] = {}
        if run.metrics_json:
            try:
                parsed = json.loads(run.metrics_json)
                if isinstance(parsed, dict):
                    metrics_obj = parsed
            except Exception:
                metrics_obj = {}

        return {
            "run_id": run.run_id,
            "suite_name": run.suite_name,
            "baseline_run_id": run.baseline_run_id,
            "status": run.status,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "metrics": metrics_obj,
            "error_message": run.error_message,
            "results": [
                {
                    "slice_name": row.slice_name,
                    "metric_name": row.metric_name,
                    "metric_value": float(row.metric_value or 0.0),
                    "threshold_value": (float(row.threshold_value) if row.threshold_value is not None else None),
                    "passed": bool(row.passed),
                    "details_json": row.details_json,
                }
                for row in results
            ],
        }


rag_eval_service = RAGEvalService()
