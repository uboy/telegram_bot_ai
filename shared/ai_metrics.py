"""AI request telemetry persistence and latency prediction."""
from __future__ import annotations

from datetime import datetime, timezone
from statistics import median
from typing import Iterable, Optional
import uuid

from shared.logging_config import logger


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def estimate_tokens(text: Optional[str]) -> int:
    """Rough token estimate (~4 chars/token for mixed RU/EN text)."""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def estimate_tokens_from_chars(chars: int) -> int:
    if chars <= 0:
        return 0
    return max(1, (chars + 3) // 4)


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def build_request_id() -> str:
    return uuid.uuid4().hex


def record_ai_metric(
    *,
    request_id: str,
    feature: str = "unknown",
    user_telegram_id: Optional[str] = None,
    conversation_id: Optional[int] = None,
    provider_name: Optional[str] = None,
    model_name: Optional[str] = None,
    request_kind: str = "text",
    prompt_chars: int = 0,
    prompt_tokens_est: int = 0,
    context_chars: int = 0,
    context_tokens_est: int = 0,
    history_turns_used: int = 0,
    predicted_latency_ms: Optional[int] = None,
    latency_ms: int = 0,
    response_chars: int = 0,
    response_tokens_est: int = 0,
    status: str = "ok",
    error_type: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    """Persist one telemetry row. Best-effort: never raises."""
    try:
        from shared.database import AIRequestMetric, get_session

        with get_session() as session:
            row = AIRequestMetric(
                request_id=request_id,
                created_at=_utcnow(),
                feature=feature or "unknown",
                user_telegram_id=user_telegram_id,
                conversation_id=conversation_id,
                provider_name=provider_name,
                model_name=model_name,
                request_kind=request_kind or "text",
                prompt_chars=max(0, int(prompt_chars or 0)),
                prompt_tokens_est=max(0, int(prompt_tokens_est or 0)),
                context_chars=max(0, int(context_chars or 0)),
                context_tokens_est=max(0, int(context_tokens_est or 0)),
                history_turns_used=max(0, int(history_turns_used or 0)),
                predicted_latency_ms=int(predicted_latency_ms) if predicted_latency_ms is not None else None,
                latency_ms=max(0, int(latency_ms or 0)),
                response_chars=max(0, int(response_chars or 0)),
                response_tokens_est=max(0, int(response_tokens_est or 0)),
                status=(status or "ok")[:20],
                error_type=(error_type or None),
                error_message=((error_message or "")[:4000] or None),
            )
            session.add(row)
    except Exception as exc:
        logger.warning("AI telemetry write failed: %s", exc)


def _bucket(tokens: int) -> int:
    # 256-token buckets are robust enough for coarse ETA.
    return max(0, int(tokens or 0) // 256)


def _median_latency(rows: Iterable[AIRequestMetric], target_bucket: int) -> Optional[int]:
    rows_list = list(rows)
    if not rows_list:
        return None

    same_bucket = [
        int(r.latency_ms or 0)
        for r in rows_list
        if _bucket((r.prompt_tokens_est or 0) + (r.context_tokens_est or 0)) == target_bucket
    ]
    if len(same_bucket) >= 5:
        return int(median(same_bucket))

    all_values = [int(r.latency_ms or 0) for r in rows_list if (r.latency_ms or 0) > 0]
    if not all_values:
        return None
    return int(median(all_values))


def _fetch_recent_rows(
    *,
    provider_name: Optional[str],
    model_name: Optional[str],
    feature: Optional[str],
    limit: int = 200,
) -> list[AIRequestMetric]:
    try:
        from shared.database import AIRequestMetric, get_session

        with get_session() as session:
            query = session.query(AIRequestMetric).filter(AIRequestMetric.status == "ok")
            if provider_name:
                query = query.filter(AIRequestMetric.provider_name == provider_name)
            if model_name:
                query = query.filter(AIRequestMetric.model_name == model_name)
            if feature:
                query = query.filter(AIRequestMetric.feature == feature)
            return (
                query.order_by(AIRequestMetric.created_at.desc())
                .limit(limit)
                .all()
            )
    except Exception:
        return []


def predict_latency_ms(
    *,
    provider_name: Optional[str],
    model_name: Optional[str],
    feature: str = "unknown",
    prompt_tokens_est: int = 0,
    context_tokens_est: int = 0,
) -> int:
    """Predict latency from recent successful calls.

    Fallback chain:
    1) provider+model+feature
    2) provider+model
    3) provider
    4) global
    """
    target_bucket = _bucket(int(prompt_tokens_est or 0) + int(context_tokens_est or 0))
    default_ms = 3000

    try:
        attempts = [
            (provider_name, model_name, feature),
            (provider_name, model_name, None),
            (provider_name, None, None),
            (None, None, None),
        ]
        for p_name, m_name, feat in attempts:
            rows = _fetch_recent_rows(
                provider_name=p_name,
                model_name=m_name,
                feature=feat,
                limit=200,
            )
            value = _median_latency(rows, target_bucket)
            if value is not None and value > 0:
                return _clamp(value, 500, 120000)
    except Exception as exc:
        logger.debug("Latency prediction fallback to default: %s", exc)

    return default_ms
