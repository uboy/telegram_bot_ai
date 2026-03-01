"""Conversation persistence and compact context assembly for direct AI mode."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func

from shared.ai_metrics import estimate_tokens
from shared.config import (
    AI_CONTEXT_BUDGETS,
    AI_CONTEXT_BUDGET_TOKENS_DEFAULT,
    AI_CONTEXT_RECENT_TURNS,
    AI_CONTEXT_RESTORE_TTL_HOURS,
)
from shared.database import AIConversation, AIConversationTurn, get_session
from shared.logging_config import logger


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_text(text: Optional[str]) -> str:
    return (text or "").strip()


def _compact_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def get_context_budget_tokens(model_name: Optional[str]) -> int:
    if model_name:
        exact = AI_CONTEXT_BUDGETS.get(model_name)
        if exact:
            return max(256, int(exact))
        lower = model_name.lower()
        for key, val in AI_CONTEXT_BUDGETS.items():
            if key.lower() in lower:
                return max(256, int(val))
    return max(256, int(AI_CONTEXT_BUDGET_TOKENS_DEFAULT))


def get_recent_active_conversation(user_telegram_id: str) -> Optional[AIConversation]:
    cutoff = _utcnow() - timedelta(hours=max(1, int(AI_CONTEXT_RESTORE_TTL_HOURS)))
    with get_session() as session:
        return (
            session.query(AIConversation)
            .filter(
                AIConversation.user_telegram_id == str(user_telegram_id),
                AIConversation.status == "active",
                AIConversation.last_activity_at >= cutoff,
            )
            .order_by(AIConversation.last_activity_at.desc())
            .first()
        )


def get_conversation(conversation_id: int) -> Optional[AIConversation]:
    with get_session() as session:
        return (
            session.query(AIConversation)
            .filter(AIConversation.id == int(conversation_id))
            .first()
        )


def create_conversation(
    user_telegram_id: str,
    *,
    provider_name: Optional[str] = None,
    model_name: Optional[str] = None,
    title: Optional[str] = None,
) -> AIConversation:
    with get_session() as session:
        conv = AIConversation(
            user_telegram_id=str(user_telegram_id),
            provider_name=provider_name,
            model_name=model_name,
            status="active",
            title=_compact_text(_normalize_text(title), 200) or None,
            summary_text=None,
            summary_version=1,
            last_activity_at=_utcnow(),
        )
        session.add(conv)
        session.flush()
        session.refresh(conv)
        return conv


def archive_conversation(conversation_id: int) -> None:
    with get_session() as session:
        conv = session.query(AIConversation).filter_by(id=int(conversation_id)).first()
        if conv:
            conv.status = "archived"
            conv.updated_at = _utcnow()


def touch_conversation(
    conversation_id: int,
    *,
    provider_name: Optional[str] = None,
    model_name: Optional[str] = None,
) -> None:
    with get_session() as session:
        conv = session.query(AIConversation).filter_by(id=int(conversation_id)).first()
        if not conv:
            return
        conv.last_activity_at = _utcnow()
        if provider_name:
            conv.provider_name = provider_name
        if model_name:
            conv.model_name = model_name


def _next_turn_index(session, conversation_id: int) -> int:
    current = (
        session.query(func.max(AIConversationTurn.turn_index))
        .filter(AIConversationTurn.conversation_id == int(conversation_id))
        .scalar()
    )
    return int(current or 0) + 1


def append_turn(conversation_id: int, role: str, content: str) -> Optional[AIConversationTurn]:
    clean = _normalize_text(content)
    if not clean:
        return None
    with get_session() as session:
        conv = session.query(AIConversation).filter_by(id=int(conversation_id)).first()
        if not conv:
            return None
        turn = AIConversationTurn(
            conversation_id=int(conversation_id),
            turn_index=_next_turn_index(session, int(conversation_id)),
            role=(role or "user")[:20],
            content=clean,
            content_chars=len(clean),
            content_tokens_est=estimate_tokens(clean),
        )
        session.add(turn)
        conv.last_activity_at = _utcnow()
        session.flush()
        session.refresh(turn)
        return turn


def list_turns(conversation_id: int) -> list[AIConversationTurn]:
    with get_session() as session:
        return (
            session.query(AIConversationTurn)
            .filter(AIConversationTurn.conversation_id == int(conversation_id))
            .order_by(AIConversationTurn.turn_index.asc())
            .all()
        )


def _build_summary_from_turns(turns: list[AIConversationTurn], max_chars: int = 1200) -> str:
    if not turns:
        return ""
    lines: list[str] = []
    for turn in turns:
        prefix = "U" if turn.role == "user" else ("A" if turn.role == "assistant" else "S")
        snippet = _compact_text(_normalize_text(turn.content).replace("\n", " "), 180)
        if snippet:
            lines.append(f"- {prefix}: {snippet}")
    text = "\n".join(lines)
    return _compact_text(text, max_chars)


def refresh_summary(conversation_id: int) -> None:
    try:
        with get_session() as session:
            conv = session.query(AIConversation).filter_by(id=int(conversation_id)).first()
            if not conv:
                return
            all_turns = (
                session.query(AIConversationTurn)
                .filter(AIConversationTurn.conversation_id == int(conversation_id))
                .order_by(AIConversationTurn.turn_index.asc())
                .all()
            )
            keep = max(2, int(AI_CONTEXT_RECENT_TURNS))
            older = all_turns[:-keep] if len(all_turns) > keep else []
            if not older:
                return
            conv.summary_text = _build_summary_from_turns(older, max_chars=1600)
            conv.summary_version = int(conv.summary_version or 1) + 1
            conv.last_activity_at = _utcnow()
    except Exception as exc:
        logger.debug("refresh_summary skipped: %s", exc)


def build_context_payload(
    conversation_id: int,
    *,
    model_name: Optional[str] = None,
) -> dict[str, Any]:
    """Return compact context payload for prompt assembly."""
    with get_session() as session:
        conv = session.query(AIConversation).filter_by(id=int(conversation_id)).first()
        if not conv:
            return {
                "context_text": "",
                "context_chars": 0,
                "context_tokens_est": 0,
                "history_turns_used": 0,
                "summary_text": "",
            }

        turns = (
            session.query(AIConversationTurn)
            .filter(AIConversationTurn.conversation_id == int(conversation_id))
            .order_by(AIConversationTurn.turn_index.asc())
            .all()
        )
        keep_recent = max(2, int(AI_CONTEXT_RECENT_TURNS))
        recent = turns[-keep_recent:] if len(turns) > keep_recent else turns
        older = turns[:-keep_recent] if len(turns) > keep_recent else []

        summary = _normalize_text(conv.summary_text)
        if older and not summary:
            summary = _build_summary_from_turns(older, max_chars=1200)
            conv.summary_text = summary
            conv.summary_version = int(conv.summary_version or 1) + 1

        recent_lines = []
        for t in recent:
            role = "Пользователь" if t.role == "user" else ("Ассистент" if t.role == "assistant" else "Система")
            recent_lines.append(f"{role}: {_compact_text(_normalize_text(t.content), 900)}")

        parts = []
        if summary:
            parts.append("Краткое резюме прошлого контекста:\n" + summary)
        if recent_lines:
            parts.append("Последние реплики:\n" + "\n".join(recent_lines))

        context_text = "\n\n".join(parts).strip()
        budget = get_context_budget_tokens(model_name or conv.model_name)
        tokens_est = estimate_tokens(context_text)

        # Shrink by removing oldest recent turns first, then summary tail.
        while tokens_est > budget and len(recent_lines) > 2:
            recent_lines.pop(0)
            parts = []
            if summary:
                parts.append("Краткое резюме прошлого контекста:\n" + summary)
            if recent_lines:
                parts.append("Последние реплики:\n" + "\n".join(recent_lines))
            context_text = "\n\n".join(parts).strip()
            tokens_est = estimate_tokens(context_text)

        if tokens_est > budget and summary:
            # Keep only compact tail of summary as last fallback.
            summary = _compact_text(summary, max(300, budget * 3))
            parts = []
            if summary:
                parts.append("Краткое резюме прошлого контекста:\n" + summary)
            if recent_lines:
                parts.append("Последние реплики:\n" + "\n".join(recent_lines))
            context_text = "\n\n".join(parts).strip()
            tokens_est = estimate_tokens(context_text)

        return {
            "context_text": context_text,
            "context_chars": len(context_text),
            "context_tokens_est": tokens_est,
            "history_turns_used": len(recent_lines),
            "summary_text": summary,
        }
