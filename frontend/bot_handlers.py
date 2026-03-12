"""
Обработчики команд и сообщений для бота
"""
import asyncio
import os
import tempfile
import hashlib
from datetime import datetime, timezone
from typing import Optional, Tuple, Any
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from shared.database import Session, User, Message, KnowledgeBase, KnowledgeImportLog
from shared.logging_config import logger
from shared.ai_providers import ai_manager
from shared.ai_metrics import estimate_tokens, predict_latency_ms
from shared.ai_conversation_service import (
    append_turn,
    build_context_payload,
    create_conversation,
    get_recent_active_conversation,
    refresh_summary,
    touch_conversation,
)
from shared.ai_prompt_policy import build_direct_ai_prompt
from frontend.backend_client import backend_client
from shared.document_loaders import document_loader_manager
from shared.image_processor import image_processor
from shared.web_search import search_web
from shared.utils import (
    format_text_safe, create_prompt_with_language, detect_language, 
    format_for_telegram_answer, strip_html_tags, normalize_wiki_url_for_display
)
from shared.asr_limits import get_asr_max_file_bytes, get_telegram_file_max_bytes
from shared.types import UserContext
from urllib.parse import unquote
from html import escape


from frontend.templates.buttons import (
    main_menu, admin_menu, settings_menu, ai_providers_menu,
    user_management_menu, knowledge_base_menu, knowledge_base_search_menu, kb_actions_menu,
    confirm_menu, search_options_menu,
    asr_models_menu, ai_context_choice_menu
)
from shared.config import ADMIN_IDS, AI_PROGRESS_THRESHOLD_SEC
from shared.n8n_client import n8n_client

# Глобальный session удалён - создаём session локально в функциях
DOCUMENT_UPLOAD_GROUP_DELAY_SEC = 1.2
DOCUMENT_JOB_POLL_INTERVAL_SEC = 2.0
DOCUMENT_JOB_TIMEOUT_SEC = 300.0
DOCUMENT_JOB_MAX_PARALLEL = 3
KB_QUERY_PROGRESS_TICK_SEC = 0.9
KB_QUERY_PROGRESS_WIDTH = 10


def _format_wiki_sync_mode(stats: dict | None) -> str:
    mode = str((stats or {}).get("crawl_mode") or "html").lower()
    attempted = bool((stats or {}).get("git_fallback_attempted", False))
    if mode == "git":
        return "git fallback (полная синхронизация wiki-репозитория)"
    if mode == "zip":
        return "ZIP archive import"
    if attempted:
        return "HTML crawl (после неудачной попытки git fallback)"
    return "HTML crawl"


def _clear_wiki_recovery_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop('kb_id_for_wiki', None)
    context.user_data.pop('wiki_urls', None)
    context.user_data.pop('wiki_zip_kb_id', None)
    context.user_data.pop('wiki_zip_url', None)


def _clear_upload_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop('pending_documents', None)
    context.user_data.pop('pending_document', None)
    context.user_data.pop('upload_mode', None)
    context.user_data.pop('kb_id', None)


def _format_bytes_short(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"


async def check_user(update: Update) -> Optional[UserContext]:
    """Проверить и зарегистрировать пользователя через backend."""
    tg = update.effective_user
    if not tg:
        return None

    user_id = str(tg.id)
    username = tg.username or None
    full_name = getattr(tg, "full_name", None)

    # 1. Синхронизируем пользователя с backend
    backend_user = await asyncio.to_thread(
        backend_client.auth_telegram,
        telegram_id=user_id,
        username=username,
        full_name=full_name,
    )
    if not backend_user:
        if update.message:
            await update.message.reply_text(
                "❌ Ошибка при проверке пользователя на backend. Попробуйте позже."
            )
        return None

    # 2. Обновляем локальный кэш
    preferred_provider = None
    preferred_model = None
    preferred_image_model = None
    show_asr_metadata = True
    
    session = Session()
    try:
        user = session.query(User).filter_by(telegram_id=user_id).first()
        if not user:
            user = User(
                telegram_id=user_id,
                username=backend_user.get("username") or user_id,
                full_name=backend_user.get("full_name"),
                role=backend_user.get("role") or "user",
                approved=bool(backend_user.get("approved", False)),
            )
            session.add(user)
        else:
            user.username = backend_user.get("username") or user.username
            user.role = backend_user.get("role") or user.role
            user.approved = bool(backend_user.get("approved", user.approved))
        
        # Настройка из backend имеет приоритет
        show_asr_metadata = bool(backend_user.get("show_asr_metadata", True))
        user.show_asr_metadata = show_asr_metadata
        
        session.commit()
        preferred_provider = getattr(user, "preferred_provider", None)
        preferred_model = getattr(user, "preferred_model", None)
        preferred_image_model = getattr(user, "preferred_image_model", None)
    finally:
        session.close()

    user_context = UserContext(
        telegram_id=user_id,
        username=backend_user.get("username"),
        full_name=backend_user.get("full_name"),
        role=backend_user.get("role") or "user",
        approved=bool(backend_user.get("approved", False)),
        preferred_provider=preferred_provider,
        preferred_model=preferred_model,
        preferred_image_model=preferred_image_model,
        show_asr_metadata=show_asr_metadata,
    )

    if not user_context.approved and user_context.role != "admin":
        if update.message:
            await update.message.reply_text("⏳ Ваша заявка еще не одобрена.")
        return None

    return user_context


def render_rag_answer_html(backend_result: dict, enable_citations: bool = True) -> tuple[str, bool]:
    backend_answer = (backend_result.get("answer") or "").strip()
    backend_sources = backend_result.get("sources") or []
    if not backend_answer: return "", False
    
    ai_answer_html = format_for_telegram_answer(backend_answer, enable_citations=enable_citations)

    def _format_source_html(s: dict, index: int) -> str:
        source_path = s.get("source_path") or ""
        source_type = s.get("source_type", "unknown")
        page_number = s.get("page_number")
        section_title = (s.get("section_title") or "").strip()

        is_url = source_type == "web" or source_path.startswith(("http://", "https://"))
        if is_url:
            url_escaped = escape(source_path, quote=True)
            display_url = normalize_wiki_url_for_display(source_path) or source_path
            return f'{index}. <a href="{url_escaped}">{escape(display_url)}</a>'

        f_name = source_path.split("/")[-1].split("::")[-1]
        location_parts = []
        if page_number:
            location_parts.append(f"стр. {page_number}")
        if section_title:
            location_parts.append(section_title)
        location = ", ".join(location_parts)
        if location:
            return f"{index}. <code>{escape(f_name)}</code> — {escape(location)}"
        return f"{index}. <code>{escape(f_name)}</code>"

    sources_html_list = []
    seen_sources: set = set()
    for s in backend_sources:
        path = s.get("source_path") or ""
        page = s.get("page_number")
        key = (path.lower(), page)
        if not path or key in seen_sources:
            continue
        seen_sources.add(key)
        sources_html_list.append(_format_source_html(s, len(sources_html_list) + 1))

    if sources_html_list:
        sources_html = "\n".join(f"• {s}" for s in sources_html_list)
        return f"🤖 <b>Ответ:</b>\n\n{ai_answer_html}\n\n📎 <b>Источники:</b>\n{sources_html}", True
    return f"🤖 <b>Ответ:</b>\n\n{ai_answer_html}", True


async def perform_rag_query_and_render(query: str, kb_id: int, user: UserContext, filters: dict = None) -> tuple[str, bool]:
    backend_result = await asyncio.to_thread(backend_client.rag_query, query=query, knowledge_base_id=kb_id, **(filters or {}))
    if (backend_result.get("answer") or "").strip():
        return render_rag_answer_html(backend_result)
    
    prompt = create_prompt_with_language(query, None, task="answer")
    ai_answer = await asyncio.to_thread(ai_manager.query, prompt, provider_name=user.preferred_provider, model=user.preferred_model)
    ai_html = format_for_telegram_answer(ai_answer, enable_citations=False)
    return f"🤖 <b>Ответ:</b>\n\n{ai_html}\n\n<i>(Основано на общих знаниях ИИ)</i>", True


def _build_kb_progress_text(step: int) -> str:
    width = max(3, int(KB_QUERY_PROGRESS_WIDTH))
    cycle = max(1, (width * 2) - 2)
    pos = step % cycle
    filled = pos + 1 if pos < width else (cycle - pos + 1)
    filled = max(1, min(width, filled))
    bar = ("█" * filled) + ("░" * (width - filled))
    return f"⏳ Ищу ответ в базе знаний\n[{bar}]"


async def _run_rag_query_with_progress(
    *,
    message,
    query: str,
    kb_id: int,
    user: UserContext,
    filters: dict | None = None,
) -> tuple[str, bool]:
    threshold_sec = max(0.8, float(AI_PROGRESS_THRESHOLD_SEC))
    progress_message = None
    progress_task = None
    query_task = asyncio.create_task(
        perform_rag_query_and_render(query=query, kb_id=kb_id, user=user, filters=filters),
    )

    async def _progress_loop():
        nonlocal progress_message
        step = 0
        while not query_task.done():
            progress_text = _build_kb_progress_text(step)
            if progress_message is None:
                progress_message = await message.reply_text(
                    progress_text,
                    reply_to_message_id=getattr(message, "message_id", None),
                )
            else:
                try:
                    await progress_message.edit_text(progress_text)
                except Exception:
                    pass
            step += 1
            await asyncio.sleep(KB_QUERY_PROGRESS_TICK_SEC)

    try:
        try:
            return await asyncio.wait_for(asyncio.shield(query_task), timeout=threshold_sec)
        except asyncio.TimeoutError:
            progress_task = asyncio.create_task(_progress_loop())
            return await query_task
    finally:
        if progress_task:
            progress_task.cancel()
            try:
                await progress_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        await _delete_message_safe(progress_message)


def _get_kb_query_queue(context: ContextTypes.DEFAULT_TYPE) -> list[dict[str, Any]]:
    return context.user_data.setdefault("kb_query_queue", [])


def _next_kb_query_session_id(context: ContextTypes.DEFAULT_TYPE) -> int:
    current = int(context.user_data.get("kb_query_session_id", 0) or 0)
    current += 1
    context.user_data["kb_query_session_id"] = current
    return current


async def _reset_kb_query_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    # Drop stale pending items from previous KB selection flows.
    context.user_data.pop("pending_queries", None)
    context.user_data.pop("pending_query", None)
    context.user_data.pop("active_search_kb_id", None)

    queue = context.user_data.get("kb_query_queue")
    if isinstance(queue, list):
        queue.clear()

    worker = context.user_data.get("kb_query_worker")
    if worker and hasattr(worker, "done") and not worker.done():
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
    context.user_data["kb_query_worker"] = None
    _next_kb_query_session_id(context)


def _resolve_kb_identity(kb: Any) -> Tuple[int, str]:
    kb_id = getattr(kb, "id", None) or kb.get("id")
    kb_name = getattr(kb, "name", None) or kb.get("name") or "Без названия"
    return int(kb_id), str(kb_name)


async def enter_kb_search_mode(context: ContextTypes.DEFAULT_TYPE) -> Tuple[str, Any]:
    await _reset_kb_query_state(context)
    kbs = await asyncio.to_thread(backend_client.list_knowledge_bases)
    if not kbs:
        context.user_data["state"] = None
        return "❌ Нет баз знаний для поиска.", None

    if len(kbs) == 1:
        kb_id, kb_name = _resolve_kb_identity(kbs[0])
        context.user_data["active_search_kb_id"] = kb_id
        context.user_data["state"] = "waiting_query"
        return f"📚 Для поиска выбрана база знаний '{kb_name}'.\n🔍 Введите запрос:", None

    context.user_data["state"] = "waiting_kb_for_query"
    return "📚 Выберите базу знаний для поиска:", knowledge_base_search_menu(kbs)


async def _process_kb_query_queue(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        while True:
            queue = _get_kb_query_queue(context)
            if not queue:
                break

            item = queue.pop(0)
            active_session_id = int(context.user_data.get("kb_query_session_id", 0) or 0)
            item_session_id = int(item.get("session_id", active_session_id) or 0)
            if item_session_id != active_session_id:
                continue

            message = item.get("message")
            query = (item.get("query") or "").strip()
            kb_id = item.get("kb_id")
            user = item.get("user")
            filters = item.get("filters") or {}
            reply_to_message_id = item.get("reply_to_message_id")

            if not message or not query or not kb_id or not isinstance(user, UserContext):
                continue

            try:
                html, _ = await _run_rag_query_with_progress(
                    message=message,
                    query=query,
                    kb_id=int(kb_id),
                    user=user,
                    filters=filters,
                )
            except Exception as e:  # noqa: BLE001
                logger.error("KB queue query failed: kb_id=%s query=%r error=%s", kb_id, query, e, exc_info=True)
                html = (
                    "❌ <b>Ошибка при поиске в базе знаний.</b>\n\n"
                    "Попробуйте повторить запрос немного позже."
                )

            await reply_html_safe(
                message,
                html,
                reply_to_message_id=reply_to_message_id,
            )
    finally:
        context.user_data["kb_query_worker"] = None


async def _enqueue_kb_query(
    *,
    context: ContextTypes.DEFAULT_TYPE,
    message,
    query: str,
    kb_id: int,
    user: UserContext,
    filters: dict | None = None,
) -> int:
    queue = _get_kb_query_queue(context)
    session_id = int(context.user_data.get("kb_query_session_id", 0) or 0)
    queue.append(
        {
            "message": message,
            "reply_to_message_id": getattr(message, "message_id", None),
            "query": query,
            "kb_id": int(kb_id),
            "user": user,
            "filters": dict(filters or {}),
            "session_id": session_id,
        }
    )
    position = len(queue)
    worker = context.user_data.get("kb_query_worker")
    if not worker or worker.done():
        context.user_data["kb_query_worker"] = asyncio.create_task(_process_kb_query_queue(context))
    return position


async def enqueue_pending_queries_for_kb(
    *,
    context: ContextTypes.DEFAULT_TYPE,
    kb_id: int,
    fallback_message=None,
    fallback_user: Optional[UserContext] = None,
) -> int:
    pending_items = list(context.user_data.pop("pending_queries", []) or [])
    legacy_query = context.user_data.pop("pending_query", None)
    if legacy_query:
        pending_items.insert(0, {"query": legacy_query})

    queued = 0
    for item in pending_items:
        query = str(item.get("query") or "").strip()
        if not query:
            continue
        message = item.get("message") or fallback_message
        user = item.get("user")
        if not isinstance(user, UserContext):
            user = fallback_user
        if not message or not isinstance(user, UserContext):
            continue
        filters = item.get("filters") or {}
        await _enqueue_kb_query(
            context=context,
            message=message,
            query=query,
            kb_id=kb_id,
            user=user,
            filters=filters,
        )
        queued += 1
    return queued


async def render_ai_answer_html(query: str, user: UserContext) -> str:
    """Legacy helper kept for compatibility with tests/old call-sites."""
    prompt = create_prompt_with_language(query, None, task="answer")
    ans = await asyncio.to_thread(
        ai_manager.query,
        prompt,
        provider_name=user.preferred_provider,
        model=user.preferred_model,
        telemetry_meta={"feature": "ask_ai_text"},
    )
    return f"🤖 <b>Ответ:</b>\n\n{format_for_telegram_answer(ans, False)}"


async def _delete_message_safe(message_obj) -> None:
    if not message_obj:
        return
    try:
        await message_obj.delete()
    except Exception:
        pass


async def _run_ai_query_with_progress(
    *,
    message,
    prompt: str,
    user: UserContext,
    telemetry_meta: dict,
    predicted_latency_ms: int,
) -> str:
    threshold_sec = max(1.0, float(AI_PROGRESS_THRESHOLD_SEC))
    threshold_ms = int(threshold_sec * 1000)
    progress_message = None
    task = asyncio.create_task(
        asyncio.to_thread(
            ai_manager.query,
            prompt,
            provider_name=user.preferred_provider,
            model=user.preferred_model,
            telemetry_meta=telemetry_meta,
        )
    )
    try:
        if predicted_latency_ms > threshold_ms:
            progress_message = await message.reply_text("⏳ Запрос к ИИ выполняется, пожалуйста подождите...")
            return await task
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=threshold_sec)
        except asyncio.TimeoutError:
            progress_message = await message.reply_text("⏳ Запрос к ИИ выполняется, пожалуйста подождите...")
            return await task
    finally:
        await _delete_message_safe(progress_message)


async def _ensure_ai_conversation(context: ContextTypes.DEFAULT_TYPE, user: UserContext) -> int:
    conversation_id = context.user_data.get("ai_conversation_id")
    if conversation_id:
        await asyncio.to_thread(
            touch_conversation,
            int(conversation_id),
            provider_name=user.preferred_provider,
            model_name=user.preferred_model,
        )
        return int(conversation_id)

    conv = await asyncio.to_thread(
        create_conversation,
        str(user.telegram_id),
        provider_name=user.preferred_provider,
        model_name=user.preferred_model,
    )
    context.user_data["ai_conversation_id"] = int(conv.id)
    context.user_data["ai_answer_count"] = 0
    return int(conv.id)


async def _render_ai_answer_html_with_context(
    *,
    query: str,
    user: UserContext,
    context: ContextTypes.DEFAULT_TYPE,
    message,
    feature: str,
) -> str:
    if context.user_data.get("ai_inflight"):
        return "⏳ <i>Предыдущий AI-запрос еще выполняется. Подождите его завершения.</i>"

    context.user_data["ai_inflight"] = True
    try:
        conversation_id = await _ensure_ai_conversation(context, user)
        is_first_turn = int(context.user_data.get("ai_answer_count", 0)) == 0
        payload = await asyncio.to_thread(
            build_context_payload,
            conversation_id,
            model_name=user.preferred_model,
        )
        prompt = build_direct_ai_prompt(
            query=query,
            context_text=payload.get("context_text") or "",
            is_first_turn=is_first_turn,
        )
        prompt_tokens_est = estimate_tokens(prompt)
        predicted_latency_ms = await asyncio.to_thread(
            predict_latency_ms,
            provider_name=user.preferred_provider,
            model_name=user.preferred_model,
            feature=feature,
            prompt_tokens_est=prompt_tokens_est,
            context_tokens_est=int(payload.get("context_tokens_est") or 0),
        )

        await asyncio.to_thread(append_turn, conversation_id, "user", query)
        answer = await _run_ai_query_with_progress(
            message=message,
            prompt=prompt,
            user=user,
            predicted_latency_ms=predicted_latency_ms,
            telemetry_meta={
                "feature": feature,
                "user_telegram_id": str(user.telegram_id),
                "conversation_id": conversation_id,
                "prompt_chars": len(prompt),
                "prompt_tokens_est": prompt_tokens_est,
                "context_chars": int(payload.get("context_chars") or 0),
                "context_tokens_est": int(payload.get("context_tokens_est") or 0),
                "history_turns_used": int(payload.get("history_turns_used") or 0),
                "predicted_latency_ms": predicted_latency_ms,
            },
        )

        await asyncio.to_thread(append_turn, conversation_id, "assistant", answer)
        await asyncio.to_thread(refresh_summary, conversation_id)
        context.user_data["ai_answer_count"] = int(context.user_data.get("ai_answer_count", 0)) + 1
        return f"🤖 <b>Ответ:</b>\n\n{format_for_telegram_answer(answer, False)}"
    finally:
        context.user_data["ai_inflight"] = False


def _split_plain_text_for_telegram(text: str, max_len: int = 3900) -> list[str]:
    """Разбить длинный plain text на безопасные для Telegram куски."""
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    start = 0
    text_len = len(text)
    while start < text_len:
        end = min(start + max_len, text_len)
        split_at = end
        if end < text_len:
            newline_idx = text.rfind("\n", start, end)
            if newline_idx > start + max_len // 2:
                split_at = newline_idx

        chunk = text[start:split_at].strip()
        if not chunk:
            chunk = text[start:end]
            split_at = end

        chunks.append(chunk)
        start = split_at
        while start < text_len and text[start] in (" ", "\n"):
            start += 1
    return chunks


async def reply_html_safe(message, html_text: str, reply_markup=None, reply_to_message_id: Optional[int] = None) -> None:
    """Отправить HTML-ответ, а при превышении лимита — plain text частями."""
    base_kwargs = {}
    if reply_to_message_id:
        base_kwargs["reply_to_message_id"] = reply_to_message_id
    if len(html_text) <= 3900:
        await message.reply_text(html_text, parse_mode='HTML', reply_markup=reply_markup, **base_kwargs)
        return

    plain_text = strip_html_tags(html_text)
    chunks = _split_plain_text_for_telegram(plain_text, max_len=3900)
    for i, chunk in enumerate(chunks):
        kwargs = dict(base_kwargs)
        if i == 0 and reply_markup is not None:
            kwargs["reply_markup"] = reply_markup
        await message.reply_text(chunk, **kwargs)


def _infer_document_file_type(file_name: str, mime_type: Optional[str] = None) -> Optional[str]:
    name = (file_name or "").strip().lower()
    ext = os.path.splitext(name)[1].lstrip(".")
    mime = (mime_type or "").strip().lower()

    ext_aliases = {
        "markdown": "md",
        "jpeg": "jpg",
        "yml": "txt",
        "yaml": "txt",
        "log": "txt",
        "conf": "txt",
        "cfg": "txt",
        "ini": "txt",
    }
    if ext in ext_aliases:
        ext = ext_aliases[ext]

    if ext == "zip":
        return "zip"
    if ext and document_loader_manager.get_loader(ext):
        return ext

    mime_to_type = {
        "application/pdf": "pdf",
        "application/zip": "zip",
        "application/x-zip-compressed": "zip",
        "application/msword": "doc",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "application/vnd.ms-excel": "xls",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
        "application/json": "json",
        "text/markdown": "md",
        "text/plain": "txt",
    }
    if mime in mime_to_type:
        return mime_to_type[mime]
    if mime.startswith("text/"):
        return "txt"
    return None


def _build_document_upload_report(kb_id: int, results: list[dict[str, Any]]) -> str:
    total = len(results)
    success = [r for r in results if r.get("status") == "completed"]
    failed = [r for r in results if r.get("status") != "completed"]

    lines = [
        f"📄 Отчет по загрузке документов (KB #{kb_id})",
        f"Всего файлов: {total}",
        f"Успешно: {len(success)}",
        f"С ошибкой: {len(failed)}",
        "",
    ]

    if success:
        lines.append("✅ Успешно:")
        for item in success:
            f_name = item.get("file_name") or "без имени"
            f_type = item.get("file_type") or "unknown"
            chunks = item.get("total_chunks")
            chunks_part = f", фрагментов: {chunks}" if isinstance(chunks, int) else ""
            lines.append(f"• {f_name} ({f_type}){chunks_part}")
        lines.append("")

    if failed:
        lines.append("❌ Ошибки:")
        for item in failed:
            f_name = item.get("file_name") or "без имени"
            f_type = item.get("file_type") or "unknown"
            err = item.get("error") or "Неизвестная ошибка"
            lines.append(f"• {f_name} ({f_type}) — {err}")
        lines.append("")

    lines.append("Проверьте журнал загрузок в админ-панели для дополнительной диагностики.")
    return "\n".join(lines)


def _lookup_import_log_chunks(kb_id: int, source_path: str) -> Optional[int]:
    logs = backend_client.get_import_log(kb_id) or []
    source_path_norm = (source_path or "").strip()
    if not source_path_norm:
        return None
    matched_zero_or_unknown: Optional[int] = None
    for row in logs:
        row_path = str(row.get("source_path") or "").strip()
        if row_path == source_path_norm:
            try:
                chunks = int(row.get("total_chunks") or 0)
                if chunks > 0:
                    return chunks
                if matched_zero_or_unknown is None:
                    matched_zero_or_unknown = chunks
            except Exception:  # noqa: BLE001
                matched_zero_or_unknown = None
                break

    # Fallback: сверяем фактическое количество чанков по агрегированному списку источников.
    # Это снижает риск ложного "0", если в import-log попала неполная запись.
    try:
        sources = backend_client.list_knowledge_sources(kb_id) or []
        for src in sources:
            src_path = str(src.get("source_path") or "").strip()
            if src_path != source_path_norm:
                continue
            return int(src.get("chunks_count") or 0)
    except Exception:
        pass

    return matched_zero_or_unknown


async def _wait_document_job(job_id: int) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + DOCUMENT_JOB_TIMEOUT_SEC
    while loop.time() < deadline:
        job = await asyncio.to_thread(backend_client.get_job_status, job_id)
        status = (job.get("status") or "").lower()
        if status in {"completed", "failed"}:
            return job
        await asyncio.sleep(DOCUMENT_JOB_POLL_INTERVAL_SEC)
    return {
        "job_id": job_id,
        "status": "failed",
        "error": f"Таймаут ожидания завершения ({int(DOCUMENT_JOB_TIMEOUT_SEC)} сек).",
    }


async def _ingest_single_document_payload(
    *,
    context: ContextTypes.DEFAULT_TYPE,
    user: UserContext,
    kb_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    file_id = str(payload.get("file_id") or "")
    file_name = str(payload.get("file_name") or "document.bin")
    file_size = int(payload.get("file_size") or 0)
    mime_type = payload.get("mime_type")

    inferred_type = _infer_document_file_type(file_name, mime_type)
    logger.info(
        "document-upload: file=%s size=%s mime=%s inferred_type=%s kb_id=%s",
        file_name,
        file_size,
        mime_type,
        inferred_type,
        kb_id,
    )
    if not inferred_type:
        return {
            "file_name": file_name,
            "file_type": "unknown",
            "status": "failed",
            "error": "Неподдерживаемый тип файла.",
        }

    limit = get_telegram_file_max_bytes()
    if file_size > 0 and file_size > limit:
        return {
            "file_name": file_name,
            "file_type": inferred_type,
            "status": "failed",
            "error": (
                f"Файл превышает лимит Telegram: {_format_bytes_short(file_size)} > "
                f"{_format_bytes_short(limit)}."
            ),
        }

    try:
        tg_file = await context.bot.get_file(file_id)
        raw = await tg_file.download_as_bytearray()
        file_bytes = bytes(raw)
    except Exception as e:  # noqa: BLE001
        return {
            "file_name": file_name,
            "file_type": inferred_type,
            "status": "failed",
            "error": f"Не удалось скачать файл из Telegram: {e}",
        }

    if len(file_bytes) > limit:
        return {
            "file_name": file_name,
            "file_type": inferred_type,
            "status": "failed",
            "error": (
                f"Скачанный файл превышает лимит Telegram: {_format_bytes_short(len(file_bytes))} > "
                f"{_format_bytes_short(limit)}."
            ),
        }

    try:
        resp = await asyncio.to_thread(
            backend_client.ingest_document,
            kb_id,
            file_name,
            file_bytes,
            inferred_type,
            str(user.telegram_id),
            user.username,
        )
    except Exception as e:  # noqa: BLE001
        return {
            "file_name": file_name,
            "file_type": inferred_type,
            "status": "failed",
            "error": f"Ошибка вызова backend ingestion: {e}",
        }

    job_id_raw = resp.get("job_id")
    logger.info(
        "document-upload: backend response file=%s inferred_type=%s job_id=%s summary=%s",
        file_name,
        inferred_type,
        job_id_raw,
        resp.get("summary"),
    )
    if not job_id_raw:
        return {
            "file_name": file_name,
            "file_type": inferred_type,
            "status": "failed",
            "error": resp.get("summary") or "Backend не вернул job_id.",
        }

    try:
        job_id = int(job_id_raw)
    except Exception:  # noqa: BLE001
        job_id = -1

    job = await _wait_document_job(job_id)
    status = (job.get("status") or "").lower()
    logger.info("document-upload: job finished file=%s job_id=%s status=%s", file_name, job_id, status)
    if status == "completed":
        chunks_from_log: Optional[int] = None
        if inferred_type != "zip":
            chunks_from_log = await asyncio.to_thread(_lookup_import_log_chunks, kb_id, file_name)
        chunks_from_response = resp.get("total_chunks")
        final_chunks: Optional[int] = None
        if isinstance(chunks_from_log, int):
            final_chunks = chunks_from_log
        elif isinstance(chunks_from_response, int) and chunks_from_response > 0:
            final_chunks = chunks_from_response
        return {
            "file_name": file_name,
            "file_type": inferred_type,
            "status": "completed",
            "job_id": job_id,
            "total_chunks": final_chunks,
        }
    return {
        "file_name": file_name,
        "file_type": inferred_type,
        "status": "failed",
        "job_id": job_id,
        "error": job.get("error") or "Задача ingestion завершилась с ошибкой.",
    }


async def _process_document_batch_upload(
    *,
    message: Any,
    context: ContextTypes.DEFAULT_TYPE,
    user: UserContext,
    kb_id: int,
    payloads: list[dict[str, Any]],
) -> None:
    if not payloads:
        return

    status_message = await message.reply_text(
        f"📥 Получено документов: {len(payloads)}. Начинаю обработку..."
    )
    semaphore = asyncio.Semaphore(max(1, DOCUMENT_JOB_MAX_PARALLEL))

    async def _run_one(item: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await _ingest_single_document_payload(
                context=context,
                user=user,
                kb_id=kb_id,
                payload=item,
            )

    raw_results = await asyncio.gather(*[_run_one(item) for item in payloads], return_exceptions=True)
    results: list[dict[str, Any]] = []
    for idx, result in enumerate(raw_results):
        if isinstance(result, Exception):
            item = payloads[idx]
            results.append(
                {
                    "file_name": item.get("file_name") or "без имени",
                    "file_type": _infer_document_file_type(
                        str(item.get("file_name") or ""),
                        item.get("mime_type"),
                    )
                    or "unknown",
                    "status": "failed",
                    "error": f"Необработанное исключение: {result}",
                }
            )
        else:
            results.append(result)

    await _delete_message_safe(status_message)
    ok_count = sum(1 for r in results if r.get("status") == "completed")
    fail_count = len(results) - ok_count
    logger.info(
        "document-upload: batch finished kb_id=%s total=%s success=%s failed=%s",
        kb_id,
        len(results),
        ok_count,
        fail_count,
    )
    report_text = _build_document_upload_report(kb_id, results)
    for chunk in _split_plain_text_for_telegram(report_text, max_len=3900):
        await message.reply_text(chunk)


def _extract_document_payload(message: Any) -> dict[str, Any]:
    doc = getattr(message, "document", None)
    file_name = getattr(doc, "file_name", None) or f"document_{getattr(doc, 'file_unique_id', 'unknown')}.bin"
    return {
        "file_id": getattr(doc, "file_id", ""),
        "file_name": file_name,
        "file_size": int(getattr(doc, "file_size", 0) or 0),
        "mime_type": getattr(doc, "mime_type", None),
    }


def _get_document_group_store(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
    return context.application.bot_data.setdefault("document_upload_groups", {})


async def _flush_document_group(
    *,
    group_key: str,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    try:
        await asyncio.sleep(DOCUMENT_UPLOAD_GROUP_DELAY_SEC)
    except asyncio.CancelledError:
        return

    groups = _get_document_group_store(context)
    group = groups.pop(group_key, None)
    if not group:
        return

    try:
        await _process_document_batch_upload(
            message=group["message"],
            context=context,
            user=group["user"],
            kb_id=int(group["kb_id"]),
            payloads=list(group["payloads"]),
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Ошибка обработки группы документов %s: %s", group_key, e, exc_info=True)
        await group["message"].reply_text(f"❌ Ошибка batch-обработки документов: {e}")


def _queue_document_in_media_group(
    *,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: UserContext,
    kb_id: int,
    payload: dict[str, Any],
) -> int:
    message = update.message
    media_group_id = getattr(message, "media_group_id", None)
    if not media_group_id or not update.effective_chat:
        return 0

    group_key = f"{update.effective_chat.id}:{media_group_id}:{user.telegram_id}:{kb_id}"
    groups = _get_document_group_store(context)
    group = groups.get(group_key)
    if not group:
        group = {
            "message": message,
            "user": user,
            "kb_id": kb_id,
            "payloads": [],
            "task": None,
        }
        groups[group_key] = group

    group["message"] = message
    group["payloads"].append(payload)
    old_task = group.get("task")
    if old_task and not old_task.done():
        old_task.cancel()
    group["task"] = asyncio.create_task(_flush_document_group(group_key=group_key, context=context))
    return len(group["payloads"])


async def process_pending_documents_for_kb(
    *,
    message: Any,
    context: ContextTypes.DEFAULT_TYPE,
    user: UserContext,
    kb_id: int,
    pending_payloads: list[dict[str, Any]],
) -> None:
    await _process_document_batch_upload(
        message=message,
        context=context,
        user=user,
        kb_id=kb_id,
        payloads=pending_payloads,
    )


async def _ensure_kb_or_ask_select(update: Update, context: ContextTypes.DEFAULT_TYPE, user: UserContext, query: str) -> Tuple[Optional[int], bool]:
    kb_id = context.user_data.get('active_search_kb_id')
    if kb_id:
        return int(kb_id), False
    kbs = await asyncio.to_thread(backend_client.list_knowledge_bases)
    if not kbs:
        await update.message.reply_text("❌ Нет баз знаний.")
        return None, True
    if len(kbs) == 1:
        kb_id, _kb_name = _resolve_kb_identity(kbs[0])
        context.user_data["active_search_kb_id"] = kb_id
        return kb_id, False
    pending_queries = context.user_data.setdefault("pending_queries", [])
    pending_queries.append(
        {
            "query": query,
            "message": update.message,
            "user": user,
            "filters": dict(context.user_data.get("rag_filters") or {}),
        }
    )
    context.user_data['state'] = 'waiting_kb_for_query'
    if len(pending_queries) == 1:
        await update.message.reply_text(
            "📚 Выберите базу знаний:",
            reply_markup=knowledge_base_search_menu(kbs),
        )
    else:
        await update.message.reply_text(
            f"🕒 Запрос добавлен в очередь ({len(pending_queries)}).\n"
            "Выберите базу знаний для запуска поиска:",
            reply_markup=knowledge_base_search_menu(kbs),
        )
    return None, True


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await check_user(update)
    if not user: return
    await update.message.reply_text("👋 Добро пожаловать!", reply_markup=main_menu(is_admin=(user.role == 'admin')))


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await check_user(update)
    if not user: return
    
    text_input = update.message.text.strip()
    state = context.user_data.get('state')

    if text_input == "🔍 Поиск в базе знаний":
        text, reply_markup = await enter_kb_search_mode(context)
        await update.message.reply_text(text, reply_markup=reply_markup)
        return
    if text_input == "🤖 Задать вопрос ИИ":
        recent = await asyncio.to_thread(get_recent_active_conversation, str(user.telegram_id))
        if recent:
            context.user_data['state'] = 'waiting_ai_resume_choice'
            context.user_data['pending_ai_restore_id'] = int(recent.id)
            await update.message.reply_text(
                "🤖 Найден предыдущий диалог.\nВыберите действие:",
                reply_markup=ai_context_choice_menu(int(recent.id)),
            )
        else:
            conv = await asyncio.to_thread(
                create_conversation,
                str(user.telegram_id),
                provider_name=user.preferred_provider,
                model_name=user.preferred_model,
            )
            context.user_data['ai_conversation_id'] = int(conv.id)
            context.user_data['ai_answer_count'] = 0
            context.user_data['ai_inflight'] = False
            context.user_data['state'] = 'waiting_ai_query'
            await update.message.reply_text("🤖 Задайте вопрос ИИ:")
        return
    if text_input == "👨‍💼 Админ-панель" and user.role == 'admin':
        await update.message.reply_text("👨‍💼 Админ-панель:", reply_markup=admin_menu())
        return

    if state == 'waiting_query':
        kb_id, prompted = await _ensure_kb_or_ask_select(update, context, user, text_input)
        if prompted:
            return
        queue_pos = await _enqueue_kb_query(
            context=context,
            message=update.message,
            query=text_input,
            kb_id=int(kb_id),
            user=user,
            filters=context.user_data.get("rag_filters") or {},
        )
        if queue_pos > 1:
            await update.message.reply_text(f"🕒 Запрос добавлен в очередь: {queue_pos}.")
    elif state == 'waiting_kb_for_query':
        pending_queries = context.user_data.setdefault("pending_queries", [])
        pending_queries.append(
            {
                "query": text_input,
                "message": update.message,
                "user": user,
                "filters": dict(context.user_data.get("rag_filters") or {}),
            }
        )
        kbs = await asyncio.to_thread(backend_client.list_knowledge_bases)
        if not kbs:
            await update.message.reply_text("❌ Нет баз знаний.")
            return
        await update.message.reply_text(
            f"🕒 Запрос добавлен в очередь ({len(pending_queries)}).\n"
            "Выберите базу знаний для запуска поиска:",
            reply_markup=knowledge_base_search_menu(kbs),
        )
    elif state == 'waiting_ai_query':
        if not text_input:
            await update.message.reply_text("⚠️ Введите непустой вопрос для ИИ.")
            return
        html = await _render_ai_answer_html_with_context(
            query=text_input,
            user=user,
            context=context,
            message=update.message,
            feature="ask_ai_text",
        )
        await reply_html_safe(update.message, html, reply_markup=main_menu(user.role == 'admin'))
    elif state == 'waiting_ai_resume_choice':
        text_lower = text_input.lower()
        if "нов" in text_lower:
            conv = await asyncio.to_thread(
                create_conversation,
                str(user.telegram_id),
                provider_name=user.preferred_provider,
                model_name=user.preferred_model,
            )
            context.user_data['ai_conversation_id'] = int(conv.id)
            context.user_data['ai_answer_count'] = 0
            context.user_data['ai_inflight'] = False
            context.user_data['state'] = 'waiting_ai_query'
            await update.message.reply_text("🆕 Начат новый диалог.\n🤖 Задайте вопрос ИИ:")
        elif "восст" in text_lower or "прод" in text_lower:
            conv_id = context.user_data.get('pending_ai_restore_id')
            if not conv_id:
                await update.message.reply_text("⚠️ Не удалось восстановить диалог. Нажмите «🤖 Задать вопрос ИИ» снова.")
                context.user_data['state'] = None
                return
            context.user_data['ai_conversation_id'] = int(conv_id)
            context.user_data['state'] = 'waiting_ai_query'
            context.user_data['ai_inflight'] = False
            await update.message.reply_text("♻️ Контекст восстановлен.\n🤖 Задайте вопрос ИИ:")
        else:
            await update.message.reply_text("Выберите действие кнопками: восстановить диалог или начать новый.")
    elif state == 'waiting_asr_model' and user.role == 'admin':
        await update.message.reply_text(f"⏳ Проверяю модель <code>{text_input}</code>...", parse_mode='HTML')
        try:
            res = await asyncio.to_thread(backend_client.update_asr_settings, {"asr_model_name": text_input})
            await update.message.reply_text(f"✅ Модель: <code>{res.get('asr_model_name')}</code>", parse_mode='HTML', reply_markup=admin_menu())
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {str(e)}")
        context.user_data['state'] = None
    elif state == 'waiting_kb_name' and user.role == 'admin':
        kb_name = text_input.strip()
        if not kb_name:
            await update.message.reply_text("⚠️ Название базы знаний не может быть пустым. Введите название:")
            return

        created = await asyncio.to_thread(backend_client.create_knowledge_base, kb_name)
        if created and created.get("id"):
            created_id = int(created["id"])
            await update.message.reply_text(
                f"✅ База знаний '{kb_name}' создана!",
                reply_markup=kb_actions_menu(created_id),
            )
        else:
            await update.message.reply_text(
                f"❌ Не удалось создать базу знаний '{kb_name}' через backend.",
                reply_markup=admin_menu(),
            )
        context.user_data['state'] = None
    elif state == 'waiting_wiki_root' and user.role == 'admin':
        wiki_url = text_input.strip()
        kb_id_raw = context.user_data.get('kb_id_for_wiki')
        kb_id = int(kb_id_raw) if kb_id_raw else None

        if not kb_id:
            await update.message.reply_text(
                "❌ Не выбрана база знаний для загрузки вики.",
                reply_markup=admin_menu(),
            )
            context.user_data['state'] = None
            _clear_wiki_recovery_state(context)
            return

        if not wiki_url.startswith(("http://", "https://")):
            await update.message.reply_text(
                "⚠️ Введите корректный URL, начинающийся с http:// или https://",
            )
            return

        try:
            stats = await asyncio.to_thread(
                backend_client.ingest_wiki_crawl,
                kb_id=kb_id,
                url=wiki_url,
                telegram_id=str(user.telegram_id),
                username=user.username,
            )
            deleted = stats.get("deleted_chunks", 0) if isinstance(stats, dict) else 0
            pages = stats.get("pages_processed", 0) if isinstance(stats, dict) else 0
            added = stats.get("chunks_added", 0) if isinstance(stats, dict) else 0
            wiki_root = (stats.get("wiki_root", wiki_url) if isinstance(stats, dict) else wiki_url)
            sync_mode = _format_wiki_sync_mode(stats if isinstance(stats, dict) else None)
            status = str(stats.get("status", "success") or "success") if isinstance(stats, dict) else "success"
            failure_message = (stats.get("failure_message") if isinstance(stats, dict) else None) or "Не удалось синхронизировать wiki."
            recovery_options = list(stats.get("recovery_options") or []) if isinstance(stats, dict) else []

            if status == "failed":
                context.user_data['state'] = 'waiting_wiki_archive'
                context.user_data['wiki_zip_kb_id'] = kb_id
                context.user_data['wiki_zip_url'] = wiki_root
                context.user_data.pop('kb_id_for_wiki', None)
                context.user_data.pop('wiki_urls', None)
                _clear_upload_state(context)
                recovery_hint = "Загрузите ZIP-архив wiki в этот чат, и бот восстановит страницы по исходному URL."
                if "provide_auth" in recovery_options:
                    recovery_hint = (
                        "Для git-синхронизации wiki нужен доступ к репозиторию. "
                        "Если авторизацию не дать, загрузите ZIP-архив wiki в этот чат."
                    )
                await update.message.reply_text(
                    "❌ Сканирование wiki не завершилось успешно.\n\n"
                    f"Исходный URL: {wiki_url}\n"
                    f"Корневой wiki-URL: {wiki_root}\n"
                    f"Режим синхронизации: {sync_mode}\n"
                    f"Удалено старых фрагментов: {deleted}\n"
                    f"Обработано страниц: {pages}\n"
                    f"Добавлено фрагментов: {added}\n\n"
                    f"Причина: {failure_message}\n"
                    f"{recovery_hint}",
                    reply_markup=kb_actions_menu(kb_id),
                )
            else:
                await update.message.reply_text(
                    "✅ Сканирование вики завершено.\n\n"
                    f"Исходный URL: {wiki_url}\n"
                    f"Корневой wiki-URL: {wiki_root}\n"
                    f"Режим синхронизации: {sync_mode}\n"
                    f"Удалено старых фрагментов: {deleted}\n"
                    f"Обработано страниц: {pages}\n"
                    f"Добавлено фрагментов: {added}",
                    reply_markup=kb_actions_menu(kb_id),
                )
        except Exception as e:  # noqa: BLE001
            logger.error("Ошибка при wiki-crawl через bot text state: %s", e, exc_info=True)
            await update.message.reply_text(
                f"❌ Ошибка при сканировании вики: {str(e)}\n\n"
                "Убедитесь, что URL корректный и доступен. Затем загрузите ZIP-архив wiki для восстановления.",
                reply_markup=kb_actions_menu(kb_id),
            )
            context.user_data['state'] = 'waiting_wiki_archive'
            context.user_data['wiki_zip_kb_id'] = kb_id
            context.user_data['wiki_zip_url'] = wiki_url
            context.user_data.pop('kb_id_for_wiki', None)
            context.user_data.pop('wiki_urls', None)
            _clear_upload_state(context)
        else:
            if context.user_data.get('state') != 'waiting_wiki_archive':
                context.user_data['state'] = None
                _clear_wiki_recovery_state(context)
                _clear_upload_state(context)
    else:
        await handle_start(update, context)


async def _handle_wiki_archive_recovery(
    *,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: UserContext,
    payload: dict,
) -> bool:
    if context.user_data.get('state') != 'waiting_wiki_archive':
        return False

    kb_id_raw = context.user_data.get('wiki_zip_kb_id')
    wiki_url = context.user_data.get('wiki_zip_url')
    kb_id = int(kb_id_raw) if kb_id_raw else None
    file_name = str(payload.get("file_name") or "")

    if not kb_id or not wiki_url:
        await update.message.reply_text(
            "❌ Сессия восстановления wiki потеряна. Запустите загрузку wiki заново.",
            reply_markup=admin_menu(),
        )
        context.user_data['state'] = None
        _clear_wiki_recovery_state(context)
        return True

    if not file_name.lower().endswith(".zip"):
        await update.message.reply_text(
            "⚠️ Для восстановления wiki нужен ZIP-архив. Для обычных документов используйте режим загрузки документов.",
            reply_markup=kb_actions_menu(kb_id),
        )
        return True

    file = await context.bot.get_file(str(payload.get("file_id") or ""))
    file_bytes = await file.download_as_bytearray()
    result = await asyncio.to_thread(
        backend_client.ingest_wiki_zip,
        kb_id=kb_id,
        url=str(wiki_url),
        zip_bytes=bytes(file_bytes),
        filename=file_name,
        telegram_id=str(user.telegram_id),
        username=user.username,
    )
    files = int(result.get("files_processed", 0) or 0) if isinstance(result, dict) else 0
    chunks = int(result.get("chunks_added", 0) or 0) if isinstance(result, dict) else 0
    await update.message.reply_text(
        "✅ Восстановление wiki из ZIP завершено.\n\n"
        f"Корневой wiki-URL: {wiki_url}\n"
        f"Обработано файлов: {files}\n"
        f"Добавлено фрагментов: {chunks}",
        reply_markup=kb_actions_menu(kb_id),
    )
    context.user_data['state'] = None
    _clear_wiki_recovery_state(context)
    return True


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик голосовых сообщений (ASR)."""
    user = await check_user(update)
    if not user: return
    voice = update.message.voice
    if not voice: return

    status_message = await update.message.reply_text("🎙️ Распознаю голосовое сообщение...")

    try:
        limit = min(get_asr_max_file_bytes(), get_telegram_file_max_bytes())
        if voice.file_size and voice.file_size > limit:
            await status_message.edit_text(f"❌ Файл слишком большой")
            return

        file = await context.bot.get_file(voice.file_id)
        file_bytes = await file.download_as_bytearray()

        result = await asyncio.to_thread(
            backend_client.asr_transcribe,
            file_name=f"{voice.file_id}.ogg",
            file_bytes=bytes(file_bytes),
            telegram_id=str(user.telegram_id),
            message_id=str(update.message.message_id),
            content_type=voice.mime_type or "audio/ogg"
        )
        job_id = result.get("job_id")
        if not job_id:
            await status_message.edit_text("❌ Ошибка создания задачи.")
            return

        last_status = "queued"
        for _ in range(300):
            await asyncio.sleep(2)
            job = await asyncio.to_thread(backend_client.asr_job_status, job_id)
            status = job.get("status")
            if status != last_status and status == "processing":
                await status_message.edit_text("🛠️ Обработка аудио...")
                last_status = status
            
            if status == "done":
                text = (job.get("text") or "").strip()
                meta_block = ""
                
                # Получаем глобальную настройку
                asr_settings = await asyncio.to_thread(backend_client.get_asr_settings)
                global_show = bool(asr_settings.get("show_asr_metadata", True))
                
                # Показываем только если включено И глобально И у пользователя
                if user.show_asr_metadata and global_show:
                    meta = job.get("audio_meta") or {}
                    orig_name = meta.get("original_name") or voice.file_id
                    lines = [f"Файл: {escape(orig_name)}"]
                    if meta.get("duration_s"): lines.append(f"Длительность: {meta['duration_s']:.1f}с")
                    if meta.get("size_bytes"): lines.append(f"Размер: {meta['size_bytes']} байт")
                    if meta.get("sample_rate"): lines.append(f"Частота: {meta['sample_rate']} Гц")
                    if meta.get("channels"): lines.append(f"Каналы: {meta['channels']}")
                    if meta.get("sent_at"): lines.append(f"Отправлено: {escape(str(meta['sent_at']))}")
                    
                    meta_content = "\n".join(lines)
                    meta_block = f"\n\n<blockquote expandable>{escape(meta_content)}</blockquote>"

                if text:
                    await status_message.edit_text(f"📝 <b>Транскрипция</b>\n\n{escape(text)}{meta_block}", parse_mode='HTML')
                    if context.user_data.get('state') == 'waiting_ai_query':
                        html = await _render_ai_answer_html_with_context(
                            query=text,
                            user=user,
                            context=context,
                            message=update.message,
                            feature="ask_ai_voice",
                        )
                        await reply_html_safe(update.message, html, reply_markup=main_menu(user.role == 'admin'))
                else:
                    await status_message.edit_text("⚠️ Текст не распознан.")
                return
            if status == "error":
                await status_message.edit_text(f"❌ Ошибка: {job.get('error')}")
                return
        await status_message.edit_text("⏳ Время ожидания истекло.")
    except Exception as e:
        logger.error("Error in handle_voice: %s", e, exc_info=True)
        await status_message.edit_text(f"❌ Ошибка: {str(e)}")


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик аудиофайлов."""
    user = await check_user(update)
    if not user: return
    audio = update.message.audio or update.message.document
    if not audio: return

    status_message = await update.message.reply_text("🎵 Распознаю аудиофайл...")

    try:
        file = await context.bot.get_file(audio.file_id)
        file_bytes = await file.download_as_bytearray()
        f_name = getattr(audio, "file_name", "audio.mp3")

        result = await asyncio.to_thread(
            backend_client.asr_transcribe,
            file_name=f_name,
            file_bytes=bytes(file_bytes),
            telegram_id=str(user.telegram_id),
            message_id=str(update.message.message_id),
            content_type=getattr(audio, "mime_type", "audio/mpeg")
        )
        job_id = result.get("job_id")
        if not job_id:
            await status_message.edit_text("❌ Ошибка бэкенда.")
            return

        for _ in range(300):
            await asyncio.sleep(2)
            job = await asyncio.to_thread(backend_client.asr_job_status, job_id)
            if job.get("status") == "done":
                text = (job.get("text") or "").strip()
                meta_block = ""
                
                asr_settings = await asyncio.to_thread(backend_client.get_asr_settings)
                global_show = bool(asr_settings.get("show_asr_metadata", True))
                
                if user.show_asr_metadata and global_show:
                    meta = job.get("audio_meta") or {}
                    lines = [f"Файл: {escape(f_name)}"]
                    if meta.get("duration_s"): lines.append(f"Длительность: {meta['duration_s']:.1f}с")
                    if meta.get("size_bytes"): lines.append(f"Размер: {meta['size_bytes']} байт")
                    if meta.get("sample_rate"): lines.append(f"Частота: {meta['sample_rate']} Гц")
                    if meta.get("channels"): lines.append(f"Каналы: {meta['channels']}")
                    if meta.get("sent_at"): lines.append(f"Отправлено: {escape(str(meta['sent_at']))}")
                    
                    meta_content = "\n".join(lines)
                    meta_block = f"\n\n<blockquote expandable>{escape(meta_content)}</blockquote>"
                await status_message.edit_text(f"📝 <b>Транскрипция</b>\n\n{escape(text)}{meta_block}", parse_mode='HTML')
                if text and context.user_data.get('state') == 'waiting_ai_query':
                    html = await _render_ai_answer_html_with_context(
                        query=text,
                        user=user,
                        context=context,
                        message=update.message,
                        feature="ask_ai_audio",
                    )
                    await reply_html_safe(update.message, html, reply_markup=main_menu(user.role == 'admin'))
                return
            if job.get("status") == "error":
                await status_message.edit_text(f"❌ Ошибка: {job.get('error')}")
                return
        await status_message.edit_text("⏳ Истекло время.")
    except Exception as e:
        logger.error("Error in handle_audio: %s", e, exc_info=True)
        await status_message.edit_text(f"❌ Ошибка: {str(e)}")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик документов."""
    user = await check_user(update)
    if not user or user.role != 'admin':
        await update.message.reply_text("Только админы могут загружать документы.")
        return
    
    doc = update.message.document
    if not doc:
        return

    payload = _extract_document_payload(update.message)
    if await _handle_wiki_archive_recovery(
        update=update,
        context=context,
        user=user,
        payload=payload,
    ):
        return

    kb_id_raw = context.user_data.get('kb_id')
    kb_id = int(kb_id_raw) if kb_id_raw else None
    if not kb_id:
        pending = context.user_data.setdefault("pending_documents", [])
        pending.append(payload)
        kbs = await asyncio.to_thread(backend_client.list_knowledge_bases)
        if not kbs:
            await update.message.reply_text("❌ Нет доступных баз знаний для загрузки.")
            return
        await update.message.reply_text(
            f"📎 Файл добавлен в очередь ({len(pending)} шт.).\n"
            "Выберите БЗ, и бот загрузит все ожидающие документы:",
            reply_markup=knowledge_base_menu(kbs),
        )
        return

    queued_in_group = _queue_document_in_media_group(
        update=update,
        context=context,
        user=user,
        kb_id=kb_id,
        payload=payload,
    )
    if queued_in_group:
        if queued_in_group == 1:
            await update.message.reply_text(
                "📦 Принята группа документов. Дождусь остальных файлов и отправлю единый отчет."
            )
        return

    await _process_document_batch_upload(
        message=update.message,
        context=context,
        user=user,
        kb_id=kb_id,
        payloads=[payload],
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик фото."""
    user = await check_user(update)
    if not user: return
    photo = update.message.photo[-1]
    
    try:
        file = await context.bot.get_file(photo.file_id)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
            await file.download_to_drive(tf.name)
            desc = image_processor.describe_image(tf.name, "Опиши картинку.", model=user.preferred_model)
            await update.message.reply_text(f"🖼️ <b>Описание:</b>\n\n{format_for_telegram_answer(desc, False)}", parse_mode='HTML')
            os.remove(tf.name)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")


async def message_collector(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Коллектор сообщений для аналитики."""
    msg = update.effective_message
    if not msg or not msg.text or not update.effective_chat: return
    if update.effective_chat.type not in ('group', 'supergroup'): return
    
    payload = {
        "chat_id": str(update.effective_chat.id),
        "message_id": msg.message_id,
        "author_telegram_id": str(update.effective_user.id) if update.effective_user else None,
        "text": msg.text,
        "timestamp": msg.date.isoformat() if msg.date else None,
    }
    asyncio.create_task(asyncio.to_thread(backend_client.analytics_store_message, payload))
