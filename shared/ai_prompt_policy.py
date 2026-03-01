"""Prompt policy for direct AI mode."""
from __future__ import annotations

from typing import Optional

from shared.config import AI_FIRST_REPLY_MAX_WORDS
from shared.utils import detect_language


_AMBIGUOUS_RU = {
    "помоги",
    "что делать",
    "как быть",
    "ну и",
    "вопрос",
}

_AMBIGUOUS_EN = {
    "help",
    "what now",
    "question",
}


def is_ambiguous_query(query: str) -> bool:
    q = (query or "").strip().lower()
    if not q:
        return True
    if len(q.split()) <= 2:
        return True
    return q in _AMBIGUOUS_RU or q in _AMBIGUOUS_EN


def build_direct_ai_prompt(
    *,
    query: str,
    context_text: Optional[str] = None,
    is_first_turn: bool = True,
) -> str:
    language = detect_language(query or "")
    max_words = max(40, int(AI_FIRST_REPLY_MAX_WORDS))
    ambiguous = is_ambiguous_query(query)

    if language == "ru":
        head = (
            "Ты помощник в режиме чата. Следуй правилам строго:\n"
            "1) На ПЕРВЫЙ ответ отвечай кратко и по делу.\n"
            "2) Если запрос неясный или слишком общий, задай РОВНО ОДИН уточняющий вопрос и остановись.\n"
            "3) Не используй таблицы и длинные списки, если пользователь не попросил подробно.\n"
            "4) Используй контекст диалога ниже, но не выдумывай факты.\n"
        )
        if is_first_turn:
            head += f"5) Лимит первого ответа: до {max_words} слов.\n"
        if ambiguous:
            head += "6) Этот запрос выглядит неоднозначным: сначала уточни.\n"
        context_block = f"\nКонтекст диалога:\n{context_text}\n" if context_text else "\nКонтекст диалога: (пусто)\n"
        return (
            f"{head}"
            f"{context_block}\n"
            f"Запрос пользователя:\n{query}\n\n"
            "Формат ответа:\n"
            "- Если все понятно: краткий ответ.\n"
            "- Если неясно: один уточняющий вопрос.\n"
        )

    head = (
        "You are an assistant in direct chat mode. Follow these rules strictly:\n"
        "1) First answer must be concise and focused.\n"
        "2) If the request is ambiguous, ask EXACTLY ONE clarifying question and stop.\n"
        "3) Avoid long lists/tables unless user explicitly asks for details.\n"
        "4) Use dialog context below, do not invent facts.\n"
    )
    if is_first_turn:
        head += f"5) First-answer limit: up to {max_words} words.\n"
    if ambiguous:
        head += "6) This request appears ambiguous: clarify first.\n"
    context_block = f"\nDialog context:\n{context_text}\n" if context_text else "\nDialog context: (empty)\n"
    return (
        f"{head}"
        f"{context_block}\n"
        f"User request:\n{query}\n\n"
        "Output format:\n"
        "- If clear: concise answer.\n"
        "- If unclear: one clarifying question.\n"
    )
