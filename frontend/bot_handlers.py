"""
Обработчики команд и сообщений для бота
"""
import asyncio
import os
import tempfile
import hashlib
from datetime import datetime, timezone
from typing import Optional, Tuple
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from shared.database import Session, User, Message, KnowledgeBase, KnowledgeImportLog
from shared.logging_config import logger
from shared.ai_providers import ai_manager
from frontend.backend_client import backend_client
from shared.document_loaders import document_loader_manager
from shared.image_processor import image_processor
from shared.web_search import search_web, format_search_results
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
    user_management_menu, knowledge_base_menu, kb_actions_menu,
    document_type_menu, confirm_menu, search_options_menu
)
from shared.config import ADMIN_IDS
from shared.n8n_client import n8n_client

# Глобальный session удалён - создаём session локально в функциях


def emit_n8n_import_event(
    kb_id: int,
    action_type: str,
    source_path: str,
    total_chunks: int,
    user_info: Optional[dict] = None,
    extra: Optional[dict] = None,
) -> None:
    """Отправить событие о загрузке знаний в n8n (если настроено)."""
    if not n8n_client.has_webhook():
        return

    # Информация о базе знаний теперь берётся через backend
    kb = None
    try:
        kbs = backend_client.list_knowledge_bases()
        kb = next((item for item in kbs if int(item.get("id")) == kb_id), None) if kbs else None
    except Exception:
        kb = None

    payload = {
        "knowledge_base": {
            "id": kb_id,
            "name": (kb.get("name") if isinstance(kb, dict) else getattr(kb, "name", None)) if kb else None,
        },
        "action_type": action_type,
        "source_path": source_path,
        "total_chunks": total_chunks,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if user_info:
        payload["user"] = user_info
    if extra:
        payload["details"] = extra

    ok, message = n8n_client.send_event("knowledge_import", payload)
    if not ok:
        logger.warning("Не удалось отправить событие в n8n: %s", message)


async def check_user(update: Update) -> Optional[UserContext]:
    """Проверить и зарегистрировать пользователя через backend.
    
    ВАЖНО: Возвращает UserContext (DTO), а не ORM объект, чтобы избежать
    DetachedInstanceError после закрытия session.
    
    Локальная БД всё ещё используется как кэш до полного выноса моделей в backend_service.
    """
    tg = update.effective_user
    if not tg:
        return None

    user_id = str(tg.id)
    username = tg.username or None
    full_name = getattr(tg, "full_name", None)

    # 1. Синхронизируем пользователя с backend (создание/обновление, роли, approved)
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

    # 2. Обновляем/создаём локальную запись (будет убрана после полного переноса моделей)
    # Получаем preferred_* из локальной БД для кэша
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
                phone=backend_user.get("phone"),
                role=backend_user.get("role") or "user",
                approved=bool(backend_user.get("approved", False)),
            )
            session.add(user)
        else:
            user.username = backend_user.get("username") or user.username
            if hasattr(user, "full_name"):
                user.full_name = backend_user.get("full_name")
            if hasattr(user, "phone"):
                user.phone = backend_user.get("phone")
            user.role = backend_user.get("role") or user.role
            user.approved = bool(backend_user.get("approved", user.approved))
        session.commit()

        # ВАЖНО: preferred_* должны быть заполнены и для нового пользователя тоже
        preferred_provider = getattr(user, "preferred_provider", None)
        preferred_model = getattr(user, "preferred_model", None)
        preferred_image_model = getattr(user, "preferred_image_model", None)
        show_asr_metadata = getattr(user, "show_asr_metadata", True)
    finally:
        session.close()

    # Формируем UserContext из backend_user (source of truth) + локального кэша
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
        # Пользователь не одобрен - отправить сообщение
        if update.message:
            await update.message.reply_text(
                "⏳ Ваша заявка еще не одобрена администратором. Пожалуйста, подождите."
            )
        return None

    return user_context


from frontend.templates.buttons import approve_menu


def render_rag_answer_html(backend_result: dict, enable_citations: bool = True) -> tuple[str, bool]:
    """
    Формирует HTML-ответ из результата RAG-запроса.
    
    Args:
        backend_result: Результат backend_client.rag_query()
        enable_citations: Включить ли citations в форматирование
        
    Returns:
        tuple: (answer_html, has_answer) - готовый HTML и флаг наличия ответа
    """
    backend_answer = (backend_result.get("answer") or "").strip()
    backend_sources = backend_result.get("sources") or []
    
    if not backend_answer:
        return "", False
    
    # Формируем HTML-ответ на основе markdown от backend
    ai_answer_html = format_for_telegram_answer(backend_answer, enable_citations=enable_citations)

    def _format_source_html(source_path: str, source_type: str, index: int) -> str:
        is_url = source_type == "web" or source_path.startswith(("http://", "https://"))
        if is_url:
            url_for_link = source_path
            display_url = normalize_wiki_url_for_display(source_path) or source_path
            if "/" in url_for_link:
                parts = [p for p in url_for_link.split("/") if p]
                title = parts[-1] if parts else url_for_link
            else:
                title = url_for_link
            title = unquote(title)
            if not title or len(title) < 2:
                parts = [p for p in url_for_link.split("/") if p]
                title = unquote(parts[-2]) if len(parts) > 1 else url_for_link
            url_escaped = escape(url_for_link, quote=True)
            nice_title = title if title and len(title) >= 2 else display_url
            return f'{index}. <a href="{url_escaped}">{escape(nice_title)}</a>'
        if "::" in source_path:
            file_name = source_path.split("::")[-1]
        elif "/" in source_path:
            file_name = source_path.split("/")[-1]
        else:
            file_name = source_path
        file_name = unquote(file_name) if "%" in file_name else file_name
        file_name_escaped = escape(file_name or "неизвестный источник")
        return f"{index}. <code>{file_name_escaped}</code>"

    # Формируем список источников из backend_sources (убираем дубликаты)
    sources_html_list: list[str] = []
    seen_paths = set()
    source_counter = 1

    for s in backend_sources:
        source_path = s.get("source_path") or ""
        source_type = s.get("source_type") or "unknown"

        if not source_path or ".keep" in source_path.lower():
            continue

        source_key = source_path.strip().lower()
        if source_key in seen_paths:
            continue
        seen_paths.add(source_key)
        sources_html_list.append(_format_source_html(source_path, source_type, source_counter))
        source_counter += 1

    # Дополнительные варианты из debug_chunks (если есть)
    extra_sources_html_list: list[str] = []
    debug_chunks = backend_result.get("debug_chunks") or []
    extra_counter = 1
    for chunk in debug_chunks:
        source_path = chunk.get("source_path") or chunk.get("doc_title") or chunk.get("section_path") or ""
        if not source_path or ".keep" in source_path.lower():
            continue
        source_key = source_path.strip().lower()
        if source_key in seen_paths:
            continue
        seen_paths.add(source_key)
        extra_sources_html_list.append(_format_source_html(source_path, "unknown", extra_counter))
        extra_counter += 1
        if extra_counter > 5:
            break

    if sources_html_list:
        sources_html = "\n".join(f"• {s}" for s in sources_html_list)
        answer_html = (
            f"🤖 <b>Ответ:</b>\n\n{ai_answer_html}\n\n"
            f"📎 <b>Использованные источники:</b>\n{sources_html}"
        )
    else:
        answer_html = f"🤖 <b>Ответ:</b>\n\n{ai_answer_html}"

    if extra_sources_html_list:
        extra_html = "\n".join(f"• {s}" for s in extra_sources_html_list)
        answer_html += f"\n\n🔎 <b>Другие возможные источники:</b>\n{extra_html}"

    return answer_html, True


async def perform_rag_summary_and_render(
    query: str,
    kb_id: int,
    mode: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> tuple[str, bool]:
    try:
        from shared.config import RAG_ENABLE_CITATIONS
        enable_citations = RAG_ENABLE_CITATIONS
    except ImportError:
        enable_citations = True

    backend_result = await asyncio.to_thread(
        backend_client.rag_summary,
        query=query,
        knowledge_base_id=kb_id,
        mode=mode,
        top_k=8,
        date_from=date_from,
        date_to=date_to,
    )
    answer = (backend_result.get("answer") or "").strip()
    if not answer:
        return "", False
    answer_html = format_for_telegram_answer(answer, enable_citations=enable_citations)
    title = "Сводка" if mode == "summary" else ("FAQ" if mode == "faq" else "Инструкция")
    return f"📝 <b>{title}:</b>\n\n{answer_html}", True


async def perform_rag_query_and_render(
    query: str,
    kb_id: int,
    user: UserContext,
    fallback_to_ai: bool = True,
    filters: Optional[dict] = None,
) -> tuple[str, bool]:
    """
    Выполняет RAG-запрос и формирует HTML-ответ.
    
    Args:
        query: Текст запроса
        kb_id: ID базы знаний
        user: Пользователь (для fallback на AI)
        fallback_to_ai: Если True, при отсутствии ответа от RAG использовать общий AI
        
    Returns:
        tuple: (answer_html, has_answer) - готовый HTML и флаг наличия ответа
    """
    # Получить настройки RAG из конфига
    try:
        from shared.config import RAG_TOP_K, RAG_ENABLE_CITATIONS
        top_k_search = RAG_TOP_K
        enable_citations = RAG_ENABLE_CITATIONS
    except ImportError:
        top_k_search = 10
        enable_citations = True
    
    # Поиск через backend RAG API (единый источник правды)
    backend_result = await asyncio.to_thread(
        backend_client.rag_query,
        query=query,
        knowledge_base_id=kb_id,
        top_k=top_k_search,
        source_types=(filters or {}).get("source_types"),
        languages=(filters or {}).get("languages"),
        path_prefixes=(filters or {}).get("path_prefixes"),
        date_from=(filters or {}).get("date_from"),
        date_to=(filters or {}).get("date_to"),
    )
    backend_answer = (backend_result.get("answer") or "").strip()
    backend_sources = backend_result.get("sources") or []
    debug_chunks = backend_result.get("debug_chunks")
    
    logger.info(
        "Поиск в БЗ (backend): user=%s, query=%r, kb_id=%s, has_answer=%s, sources=%s",
        user.telegram_id if user else "unknown",
        query,
        kb_id,
        bool(backend_answer),
        len(backend_sources),
    )
    
    # Логирование debug_chunks если включен debug mode
    if debug_chunks:
        logger.info("Debug chunks (top-5): %s", [
            {
                "chunk_kind": c.get("chunk_kind"),
                "section_path": c.get("section_path"),
                "score": c.get("score"),
                "rerank_score": c.get("rerank_score"),
            }
            for c in debug_chunks
        ])
    
    if backend_answer:
        answer_html, has_answer = render_rag_answer_html(backend_result, enable_citations=enable_citations)
        return answer_html, has_answer
    elif fallback_to_ai:
        # Если backend не нашёл релевантных фрагментов, fallback на общий ИИ-ответ
        prompt = create_prompt_with_language(query, None, task="answer")
        model = user.preferred_model if user and user.preferred_model else None
        provider = user.preferred_provider if user else None
        ai_answer = await asyncio.to_thread(
            ai_manager.query,
            prompt,
            provider_name=provider,
            model=model,
        )
        # Используем format_for_telegram_answer() для единообразного форматирования
        ai_answer_html = format_for_telegram_answer(ai_answer, enable_citations=False)
        answer_html = (
            f"🤖 <b>Ответ:</b>\n\n{ai_answer_html}\n\n"
            f"<i>(В базе знаний ничего не найдено, ответ основан на общих знаниях)</i>"
        )
        return answer_html, True
    else:
        return "", False


async def _ensure_kb_or_ask_select(update: Update, context: ContextTypes.DEFAULT_TYPE, user: UserContext, query: str) -> Tuple[Optional[int], bool]:
    """
    Проверяет, выбрана ли KB. Если нет — показывает меню выбора и сохраняет pending_query.
    Returns: (kb_id, did_prompt_select)
    """
    kb_id = context.user_data.get('kb_id')
    if kb_id:
        return kb_id, False

    kbs = await asyncio.to_thread(backend_client.list_knowledge_bases)
    if not kbs:
        await update.message.reply_text(
            "❌ Нет доступных баз знаний. Создайте базу знаний в админ-панели.",
            reply_markup=main_menu(is_admin=(user.role == 'admin'))
        )
        context.user_data['state'] = None
        return None, True

    context.user_data['pending_query'] = query
    context.user_data['state'] = 'waiting_kb_for_query'
    await update.message.reply_text(
        "📚 Выберите базу знаний для поиска:",
        reply_markup=knowledge_base_menu(kbs)
    )
    return None, True


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = await check_user(update)
    if not user:
        return
    
    text = "👋 Добро пожаловать в бота-помощника!\n\nВыберите действие:"
    menu = main_menu(is_admin=(user.role == 'admin'))
    await update.message.reply_text(text, reply_markup=menu)
    logger.info("Пользователь %s (%s) запустил /start", user.username, user.telegram_id)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений"""
    user = await check_user(update)
    if not user:
        # Пользователь не одобрен или не зарегистрирован
        # check_user уже отправил сообщение, просто выходим
        return
    
    # Проверить, ожидается ли ввод от пользователя
    chat_id = str(update.effective_chat.id)
    state = context.user_data.get('state')
    text_input = update.message.text.strip() if update.message and update.message.text else ""

    # Обработка "кнопок" обычной клавиатуры (ReplyKeyboardMarkup) всегда имеет приоритет
    if text_input == "🔍 Поиск в базе знаний":
        context.user_data['state'] = 'waiting_query'
        await update.message.reply_text("🔍 Введите запрос для поиска в базе знаний:")
        logger.info("Пользователь %s выбрал режим: поиск в базе знаний", user.telegram_id)
        return
    if text_input == "🌐 Поиск в интернете":
        context.user_data['state'] = 'waiting_web_query'
        await update.message.reply_text("🌐 Введите запрос для поиска в интернете:")
        logger.info("Пользователь %s выбрал режим: поиск в интернете", user.telegram_id)
        return
    if text_input == "🤖 Задать вопрос ИИ":
        context.user_data['state'] = 'waiting_ai_query'
        await update.message.reply_text("🤖 Задайте вопрос ИИ:")
        logger.info("Пользователь %s выбрал режим: прямой вопрос ИИ", user.telegram_id)
        return
    if text_input == "🖼️ Обработать изображение":
        await update.message.reply_text("🖼️ Отправьте изображение для обработки")
        logger.info("Пользователь %s выбрал режим: обработка изображения", user.telegram_id)
        return
    if text_input == "👨‍💼 Админ-панель" and user.role == 'admin':
        await update.message.reply_text("👨‍💼 Админ-панель:", reply_markup=admin_menu())
        logger.info("Администратор %s открыл админ-панель", user.telegram_id)
        return

    if text_input.startswith("/job "):
        parts = text_input.split()
        if len(parts) > 1 and parts[1].isdigit():
            job_id = int(parts[1])
            result = await asyncio.to_thread(backend_client.get_job_status, job_id)
            status = result.get("status") or "unknown"
            progress = result.get("progress", 0)
            stage = result.get("stage") or "-"
            error = result.get("error")
            message = f"🧩 Job {job_id}: {status} ({progress}%)\nStage: {stage}"
            if error:
                message += f"\nError: {error}"
            await update.message.reply_text(message)
        else:
            await update.message.reply_text("Использование: /job <id>")
        return
    
    if state == 'waiting_filter_path':
        path_prefix = (update.message.text or "").strip()
        if path_prefix == "-":
            context.user_data["rag_filters"] = {
                **(context.user_data.get("rag_filters") or {}),
                "path_prefixes": [],
            }
            await update.message.reply_text("✅ Префикс пути очищен.")
        else:
            context.user_data["rag_filters"] = {
                **(context.user_data.get("rag_filters") or {}),
                "path_prefixes": [path_prefix],
            }
            await update.message.reply_text(f"✅ Установлен префикс пути: {path_prefix}")
        context.user_data['state'] = None
        return

    if state == 'waiting_summary_date_from':
        value = (update.message.text or "").strip()
        summary_filters = context.user_data.get("summary_filters") or {}
        if value != "-":
            summary_filters["date_from"] = value
        else:
            summary_filters.pop("date_from", None)
        context.user_data["summary_filters"] = summary_filters
        context.user_data["state"] = "waiting_summary_date_to"
        await update.message.reply_text("Введите дату ДО (YYYY-MM-DD) или '-' для пропуска:")
        return

    if state == 'waiting_summary_date_to':
        value = (update.message.text or "").strip()
        summary_filters = context.user_data.get("summary_filters") or {}
        if value != "-":
            summary_filters["date_to"] = value
        else:
            summary_filters.pop("date_to", None)
        context.user_data["summary_filters"] = summary_filters
        context.user_data["state"] = None
        await update.message.reply_text("✅ Диапазон дат сохранен.")
        return

    if state == 'waiting_summary_query':
        query = update.message.text
        kb_id = context.user_data.get('kb_id')
        if not kb_id:
            kbs = await asyncio.to_thread(backend_client.list_knowledge_bases)
            if not kbs:
                await update.message.reply_text("❌ Нет доступных баз знаний.")
                return
            context.user_data['pending_summary_query'] = query
            context.user_data['state'] = 'waiting_kb_for_query'
            await update.message.reply_text(
                "Выберите базу знаний для сводки:",
                reply_markup=knowledge_base_menu(kbs),
            )
            return
        mode = context.user_data.get("summary_mode", "summary")
        summary_filters = context.user_data.get("summary_filters") or {}
        answer_html, has_answer = await perform_rag_summary_and_render(
            query,
            kb_id,
            mode,
            date_from=summary_filters.get("date_from"),
            date_to=summary_filters.get("date_to"),
        )
        if has_answer:
            await update.message.reply_text(answer_html, parse_mode='HTML')
        else:
            await update.message.reply_text("❌ Не удалось сформировать сводку.")
        context.user_data['state'] = None
        return

    if state == 'waiting_query':
        # Поиск в базе знаний через backend (RAG API)
        query = update.message.text
        kb_id, prompted = await _ensure_kb_or_ask_select(update, context, user, query)
        if prompted:
            return
        
        # Выполняем RAG-запрос и формируем HTML-ответ
        answer_html, has_answer = await perform_rag_query_and_render(
            query,
            kb_id,
            user,
            filters=context.user_data.get("rag_filters"),
        )
        
        menu = main_menu(is_admin=(user.role == 'admin'))
        # Используем HTML для форматирования, но с безопасной обработкой ошибок
        try:
            await update.message.reply_text(answer_html, reply_markup=menu, parse_mode='HTML')
        except Exception as e:
            # Если HTML не работает, отправляем plain текст без HTML-тегов
            logger.warning("Ошибка форматирования HTML, отправляю plain текст: %s", e)
            answer_plain = strip_html_tags(answer_html)
            await update.message.reply_text(answer_plain, reply_markup=menu, parse_mode=None)
        context.user_data['state'] = None
        
    elif state == 'waiting_web_query':
        # Поиск в интернете
        query = update.message.text
        await update.message.reply_text("🔍 Ищу информацию в интернете...")
        
        results = search_web(query, max_results=5)
        logger.info("Поиск в интернете: user=%s, query=%r, результатов=%s", user.telegram_id, query, len(results))
        
        if results:
            # Сформировать контекст из результатов поиска
            search_context = "\n\n".join([
                f"Источник {i+1}: {r.get('title', '')}\n{r.get('snippet', '')[:300]}"
                for i, r in enumerate(results[:3])
            ])
            
            # Использовать ИИ для обработки результатов
            prompt = create_prompt_with_language(query, search_context, task="search_summary")
            model = user.preferred_model if user.preferred_model else None
            ai_answer = ai_manager.query(prompt, provider_name=user.preferred_provider, model=model)
            
            # Форматировать ответ с HTML (используем единый пайплайн форматирования)
            from shared.utils import format_for_telegram_answer
            ai_answer_html = format_for_telegram_answer(ai_answer, enable_citations=False)
            
            # Добавить ссылки в HTML формате
            sources_html_parts = []
            from html import escape
            for i, result in enumerate(results[:3], 1):
                url = result.get('url', '')
                title = result.get('title', 'Без названия')
                title_escaped = escape(title)
                if url:
                    url_escaped = escape(url, quote=True)
                    sources_html_parts.append(f"• {i}. <a href=\"{url_escaped}\">{title_escaped}</a>")
                else:
                    sources_html_parts.append(f"• {i}. <b>{title_escaped}</b>")
            
            sources_html = "\n".join(sources_html_parts)
            answer_html = f"🌐 <b>Результаты поиска:</b>\n\n{ai_answer_html}\n\n📎 <b>Источники:</b>\n{sources_html}"
        else:
            answer_html = "❌ <b>Не удалось найти информацию в интернете.</b>\n\nПопробуйте переформулировать запрос."
        
        menu = main_menu(is_admin=(user.role == 'admin'))
        try:
            await update.message.reply_text(answer_html, reply_markup=menu, parse_mode='HTML')
        except Exception as e:
            logger.warning("Ошибка форматирования HTML, отправляю plain текст: %s", e)
            answer_plain = strip_html_tags(answer_html)
            await update.message.reply_text(answer_plain, reply_markup=menu, parse_mode=None)
        context.user_data['state'] = None
        
    elif state == 'waiting_ai_query':
        # Прямой запрос к ИИ
        query = update.message.text
        prompt = create_prompt_with_language(query, None, task="answer")
        model = user.preferred_model if user.preferred_model else None
        ai_answer = ai_manager.query(prompt, provider_name=user.preferred_provider, model=model)
        
        # Форматируем ответ с HTML для лучшего форматирования (используем единый пайплайн)
        from shared.utils import format_for_telegram_answer
        ai_answer_html = format_for_telegram_answer(ai_answer, enable_citations=False)
        answer_html = f"🤖 <b>Ответ:</b>\n\n{ai_answer_html}"
        
        menu = main_menu(is_admin=(user.role == 'admin'))
        try:
            await update.message.reply_text(answer_html, reply_markup=menu, parse_mode='HTML')
        except Exception as e:
            logger.warning("Ошибка форматирования HTML, отправляю без форматирования: %s", e)
            answer_plain = strip_html_tags(f"🤖 Ответ:\n\n{ai_answer}")
            await update.message.reply_text(answer_plain, reply_markup=menu, parse_mode=None)
        context.user_data['state'] = None
        
    elif state == 'waiting_url':
        # Загрузка веб-страницы
        url = update.message.text
        kb_id = context.user_data.get('kb_id')
        if kb_id:
            settings_resp = await asyncio.to_thread(backend_client.get_kb_settings, kb_id)
            settings = settings_resp.get("settings") if isinstance(settings_resp, dict) else {}
            prompt_ingest = (settings.get("ui") or {}).get("prompt_on_ingest", True)
            if prompt_ingest:
                context.user_data["pending_ingest"] = {"kind": "web", "url": url, "kb_id": kb_id}
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("Целиком", callback_data=f"ingest_chunking:{kb_id}:web:full")],
                    [InlineKeyboardButton("По заголовкам", callback_data=f"ingest_chunking:{kb_id}:web:section")],
                    [InlineKeyboardButton("Фикс. размер", callback_data=f"ingest_chunking:{kb_id}:web:fixed")],
                ])
                await update.message.reply_text(
                    "Как разбивать страницу перед индексированием?",
                    reply_markup=keyboard,
                )
            else:
                logger.info("Загрузка одной веб-страницы в БЗ: kb_id=%s, url=%s, user=%s", kb_id, url, user.telegram_id)
                await load_web_page(update, context, url, kb_id)
        context.user_data['state'] = None
    
    elif state == 'waiting_wiki_root':
        # Рекурсивный сбор wiki-раздела сайта через backend ingestion API
        wiki_url = (update.message.text or "").strip()
        kb_id = context.user_data.get('kb_id_for_wiki')

        if not kb_id:
            await update.message.reply_text(
                "Не выбрана база знаний для загрузки вики. Сначала выберите БЗ в админ-панели.",
                reply_markup=admin_menu(),
            )
            context.user_data['state'] = None
            return

        settings_resp = await asyncio.to_thread(backend_client.get_kb_settings, kb_id)
        settings = settings_resp.get("settings") if isinstance(settings_resp, dict) else {}
        prompt_ingest = (settings.get("ui") or {}).get("prompt_on_ingest", True)
        if prompt_ingest:
            context.user_data["pending_ingest"] = {"kind": "wiki", "url": wiki_url, "kb_id": kb_id}
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("Целиком", callback_data=f"ingest_chunking:{kb_id}:wiki:full")],
                [InlineKeyboardButton("По заголовкам", callback_data=f"ingest_chunking:{kb_id}:wiki:section")],
                [InlineKeyboardButton("Фикс. размер", callback_data=f"ingest_chunking:{kb_id}:wiki:fixed")],
            ])
            await update.message.reply_text(
                "Как разбивать страницы вики перед индексированием?",
                reply_markup=keyboard,
            )
            context.user_data['state'] = None
            context.user_data.pop('kb_id_for_wiki', None)
            return

        await update.message.reply_text(
            "🚀 Запускаю рекурсивный обход вики.\n"
            "Это может занять несколько минут в зависимости от размеров раздела.",
        )
        logger.info("Старт сканирования вики из Telegram: kb_id=%s, url=%s, user=%s", kb_id, wiki_url, user.telegram_id)

        try:
            tg_id = str(update.effective_user.id) if update.effective_user else ""
            username = update.effective_user.username if update.effective_user else ""

            stats = await asyncio.to_thread(
                backend_client.ingest_wiki_crawl,
                kb_id=kb_id,
                url=wiki_url,
                telegram_id=tg_id or None,
                username=username or None,
            )
            deleted = stats.get("deleted_chunks", 0)
            pages = stats.get("pages_processed", 0) or 0
            added = stats.get("chunks_added", 0)
            wiki_root = stats.get("wiki_root", wiki_url)

            text = (
                "✅ Сканирование вики завершено.\n\n"
                f"Исходный URL: {wiki_url}\n"
                f"Корневой wiki-URL: {wiki_root}\n"
                f"Удалено старых фрагментов: {deleted}\n"
                f"Обработано страниц: {pages}\n"
                f"Добавлено фрагментов: {added}"
            )

            # Если загружено мало страниц (<= 1), предложить догрузить через git или zip
            if pages <= 1:
                from frontend.templates.buttons import InlineKeyboardButton, InlineKeyboardMarkup
                # Используем MD5 хеш для URL, чтобы избежать превышения лимита callback_data (64 байта)
                import hashlib
                wiki_url_hash = hashlib.md5(wiki_url.encode('utf-8')).hexdigest()[:8]
                # Сохраняем полный URL в context для последующего использования
                if 'wiki_urls' not in context.user_data:
                    context.user_data['wiki_urls'] = {}
                context.user_data['wiki_urls'][wiki_url_hash] = wiki_url
                buttons = [
                    [InlineKeyboardButton(
                        "🔗 Загрузить вики из Git репозитория",
                        callback_data=f"wiki_git_load:{kb_id}:{wiki_url_hash}"
                    )],
                    [InlineKeyboardButton(
                        "📦 Загрузить вики из ZIP архива",
                        callback_data=f"wiki_zip_load:{kb_id}:{wiki_url_hash}"
                    )],
                    [InlineKeyboardButton("🔙 К админ-меню", callback_data="admin_menu")]
                ]
                text += (
                    "\n\n⚠️ Загружено мало страниц. "
                    "Вики Gitee можно загрузить полностью:\n"
                    "• Из Git репозитория (автоматическое клонирование)\n"
                    "• Из ZIP архива (если вы скачали архив отдельно)"
                )
                await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
            else:
                await update.message.reply_text(text, reply_markup=admin_menu())
        except Exception as e:
            logger.error("Ошибка при сканировании вики (backend): %s", e)
            await update.message.reply_text(
                f"❌ Ошибка при сканировании вики: {str(e)}",
                reply_markup=admin_menu(),
            )

        context.user_data['state'] = None
        context.user_data.pop('kb_id_for_wiki', None)
        
    elif state == 'waiting_code_path':
        code_path = (update.message.text or "").strip()
        kb_id = context.user_data.get('kb_id_for_code')
        if not kb_id:
            await update.message.reply_text(
                "Не выбрана база знаний для индексации кода.",
                reply_markup=admin_menu(),
            )
            context.user_data['state'] = None
            return

        await update.message.reply_text("?? Индексация кода началась. Это может занять время.")
        try:
            tg_id = str(update.effective_user.id) if update.effective_user else ""
            username = update.effective_user.username if update.effective_user else ""
            stats = await asyncio.to_thread(
                backend_client.ingest_codebase_path,
                kb_id=kb_id,
                path=code_path,
                telegram_id=tg_id or None,
                username=username or None,
            )
            text = (
                "? Индексация кода завершена.\n\n"
                f"Корень: {stats.get('root', code_path)}\n"
                f"Файлов обработано: {stats.get('files_processed', 0)}\n"
                f"Файлов пропущено: {stats.get('files_skipped', 0)}\n"
                f"Файлов обновлено: {stats.get('files_updated', 0)}\n"
                f"Добавлено фрагментов: {stats.get('chunks_added', 0)}"
            )
            await update.message.reply_text(text, reply_markup=admin_menu())
        except Exception as e:
            await update.message.reply_text(f"? Ошибка индексации кода: {str(e)}", reply_markup=admin_menu())
        finally:
            context.user_data['state'] = None
            context.user_data.pop('kb_id_for_code', None)

    elif state == 'waiting_code_git':
        git_url = (update.message.text or "").strip()
        kb_id = context.user_data.get('kb_id_for_code')
        if not kb_id:
            await update.message.reply_text(
                "Не выбрана база знаний для индексации кода.",
                reply_markup=admin_menu(),
            )
            context.user_data['state'] = None
            return

        await update.message.reply_text("?? Индексация кода из git началась. Это может занять время.")
        try:
            tg_id = str(update.effective_user.id) if update.effective_user else ""
            username = update.effective_user.username if update.effective_user else ""
            stats = await asyncio.to_thread(
                backend_client.ingest_codebase_git,
                kb_id=kb_id,
                git_url=git_url,
                telegram_id=tg_id or None,
                username=username or None,
            )
            text = (
                "? Индексация кода завершена.\n\n"
                f"Корень: {stats.get('root', git_url)}\n"
                f"Файлов обработано: {stats.get('files_processed', 0)}\n"
                f"Файлов пропущено: {stats.get('files_skipped', 0)}\n"
                f"Файлов обновлено: {stats.get('files_updated', 0)}\n"
                f"Добавлено фрагментов: {stats.get('chunks_added', 0)}"
            )
            await update.message.reply_text(text, reply_markup=admin_menu())
        except Exception as e:
            await update.message.reply_text(f"? Ошибка индексации кода: {str(e)}", reply_markup=admin_menu())
        finally:
            context.user_data['state'] = None
            context.user_data.pop('kb_id_for_code', None)

    elif state == 'waiting_kb_name':
        # Создание базы знаний
        kb_name = update.message.text
        created = backend_client.create_knowledge_base(kb_name)
        if created and created.get("id"):
            kb_id = int(created.get("id"))
            await update.message.reply_text(
                f"✅ База знаний '{kb_name}' создана!",
                reply_markup=kb_actions_menu(kb_id),
            )
        else:
            await update.message.reply_text(
                f"❌ Не удалось создать базу знаний '{kb_name}' через backend.",
                reply_markup=admin_menu(),
            )
        context.user_data['state'] = None

    elif state == 'waiting_asr_model':
        if user.role != 'admin':
            await update.message.reply_text("Только администраторы могут менять ASR модель.")
            context.user_data['state'] = None
            return

        model_name = (update.message.text or "").strip()
        if not model_name:
            await update.message.reply_text("Введите непустое имя модели ASR.", reply_markup=admin_menu())
            context.user_data['state'] = None
            return

        result = await asyncio.to_thread(
            backend_client.update_asr_settings,
            {"asr_model_name": model_name},
        )
        if result and result.get("asr_model_name"):
            await update.message.reply_text(
                f"✅ ASR модель изменена на: {result.get('asr_model_name')}",
                reply_markup=admin_menu(),
            )
        else:
            await update.message.reply_text(
                "⚠️ Не удалось обновить ASR модель через backend.",
                reply_markup=admin_menu(),
            )
        context.user_data['state'] = None
        
    elif state == 'waiting_user_delete':
        # Удаление пользователя
        if user.role != 'admin':
            await update.message.reply_text("Только администраторы могут удалять пользователей.")
            context.user_data['state'] = None
            return
        
        try:
            target_tg = (update.message.text or "").strip()
            users = await asyncio.to_thread(backend_client.list_users)
            target = next((u for u in users if str(u.get("telegram_id")) == target_tg), None)

            if not target or not target.get("id"):
                await update.message.reply_text("Пользователь не найден.", reply_markup=admin_menu())
                context.user_data['state'] = None
                return

            ok = backend_client.delete_user(int(target["id"]))
            if ok:
                await update.message.reply_text("✅ Пользователь удален!", reply_markup=admin_menu())
            else:
                await update.message.reply_text("❌ Не удалось удалить пользователя (backend).", reply_markup=admin_menu())
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {str(e)}", reply_markup=admin_menu())
        context.user_data['state'] = None
    
    elif state == 'waiting_user_role_change':
        # Смена роли пользователя
        if user.role != 'admin':
            await update.message.reply_text("Только администраторы могут менять роли пользователей.")
            context.user_data['state'] = None
            return
        
        try:
            parts = (update.message.text or "").strip().split()
            if len(parts) != 2:
                await update.message.reply_text(
                    "❌ Неверный формат.\n\n"
                    "Ожидается: <code>TELEGRAM_ID роль</code>\n"
                    "Например: <code>123456789 admin</code>",
                    reply_markup=admin_menu(),
                    parse_mode='HTML',
                )
                context.user_data['state'] = None
                return
            
            target_id, new_role = parts[0], parts[1].lower()
            if new_role not in ("user", "admin"):
                await update.message.reply_text(
                    "❌ Недопустимая роль. Используйте: <b>user</b> или <b>admin</b>.",
                    reply_markup=admin_menu(),
                    parse_mode='HTML',
                )
                context.user_data['state'] = None
                return
            
            session = Session()
            try:
                target_user = session.query(User).filter_by(telegram_id=target_id).first()
                if not target_user:
                    await update.message.reply_text("Пользователь не найден.", reply_markup=admin_menu())
                    context.user_data['state'] = None
                    return
                
                old_role = target_user.role
                target_user.role = new_role
                session.commit()
                
                await update.message.reply_text(
                    f"✅ Роль пользователя @{target_user.username} изменена: {old_role} → {new_role}.",
                    reply_markup=admin_menu(),
                )
            finally:
                session.close()
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {str(e)}", reply_markup=admin_menu())
        finally:
            context.user_data['state'] = None
        
    else:
        # Любой обычный текст без активного состояния считаем запросом к базе знаний
        query = update.message.text

        # Сохранить сообщение в историю (как и раньше)
        session = Session()
        try:
            session.add(Message(
                chat_id=chat_id,
                user=update.effective_user.username or str(update.effective_user.id),
                text=query,
            ))
            session.commit()
        finally:
            session.close()
        
        # Проверка: KB должна быть выбрана
        kb_id, prompted = await _ensure_kb_or_ask_select(update, context, user, query)
        if prompted:
            return
        
        # Выполняем RAG-запрос и формируем HTML-ответ
        answer_html, has_answer = await perform_rag_query_and_render(
            query,
            kb_id,
            user,
            filters=context.user_data.get("rag_filters"),
        )
        
        menu = main_menu(is_admin=(user.role == 'admin'))
        # Используем HTML для форматирования, но с безопасной обработкой ошибок
        try:
            await update.message.reply_text(answer_html, reply_markup=menu, parse_mode='HTML')
        except Exception as e:
            # Если HTML не работает, отправляем plain текст без HTML-тегов
            logger.warning("Ошибка форматирования HTML, отправляю plain текст: %s", e)
            answer_plain = strip_html_tags(answer_html)
            await update.message.reply_text(answer_plain, reply_markup=menu, parse_mode=None)


async def load_document_to_kb(query_or_update, context, document_info, kb_id):
    """Загрузить документ или архив в базу знаний через backend ingestion API."""
    from telegram import Update

    is_update = isinstance(query_or_update, Update)

    try:
        if is_update:
            bot = query_or_update.get_bot()
            file = await bot.get_file(document_info["file_id"])
            message = query_or_update.message
        else:
            bot = query_or_update.message.bot if hasattr(query_or_update, "message") else context.bot
            file = await bot.get_file(document_info["file_id"])
            message = None

        # Считать файл в память
        file_bytes = await file.download_as_bytearray()

        # Определить пользователя для журнала загрузок
        try:
            if is_update and query_or_update.effective_user:
                tg_id = str(query_or_update.effective_user.id)
                username = query_or_update.effective_user.username or tg_id
            else:
                user_obj = getattr(query_or_update, "from_user", None)
                tg_id = str(user_obj.id) if user_obj else ""
                username = (user_obj.username if user_obj else "") or tg_id
        except Exception:  # noqa: BLE001
            tg_id = ""
            username = ""

        file_type = (document_info.get("file_type") or "").lower()
        file_name = document_info.get("file_name") or ""

        result = await asyncio.to_thread(
            backend_client.ingest_document,
            kb_id=kb_id,
            file_name=file_name,
            file_bytes=bytes(file_bytes),
            file_type=file_type,
            telegram_id=tg_id or None,
            username=username or None,
        )

        total_chunks = int(result.get("total_chunks", 0)) if result else 0
        mode = result.get("mode", "document") if result else "document"
        job_id = result.get("job_id") if result else None

        if total_chunks > 0:
            if mode == "archive":
                response_text = f"✅ Архив обработан, загружено {total_chunks} фрагментов в базу знаний!"
            else:
                response_text = f"✅ Документ загружен, фрагментов: {total_chunks}!"
        elif job_id:
            response_text = f"⏳ Задача запущена. Job ID: {job_id}. Статус можно проверить в /jobs/{job_id}."
        else:
            response_text = "⚠️ Backend не вернул добавленные фрагменты для этого файла."

        if is_update and message is not None:
            await message.reply_text(response_text, reply_markup=admin_menu())
        else:
            await query_or_update.edit_message_text(response_text, reply_markup=admin_menu())
    except Exception as e:  # noqa: BLE001
        error_text = f"❌ Ошибка загрузки: {str(e)}"
        if is_update and message is not None:
            await message.reply_text(error_text)
        else:
            try:
                await query_or_update.edit_message_text(error_text)
            except Exception:
                pass


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик загрузки документов"""
    user = await check_user(update)
    if not user or user.role != 'admin':
        await update.message.reply_text("Только администраторы могут загружать документы.")
        return
    
    document = update.message.document
    if not document:
        return
    
    file_name = document.file_name or ''
    file_type = file_name.split('.')[-1].lower() if '.' in file_name else None
    state = context.user_data.get('state')
    
    # Проверяем, ожидается ли ZIP архив для вики
    if state == 'waiting_wiki_zip' and file_type == 'zip':
        kb_id = context.user_data.get('wiki_zip_kb_id')
        wiki_url = context.user_data.get('wiki_zip_url')
        
        if not kb_id or not wiki_url:
            await update.message.reply_text("❌ Ошибка: не найдена информация о базе знаний или URL вики.")
            context.user_data.pop('state', None)
            context.user_data.pop('wiki_zip_kb_id', None)
            context.user_data.pop('wiki_zip_url', None)
            return
        
        await update.message.reply_text("🔄 Обработка ZIP архива и загрузка вики через backend...\n\nЭто может занять некоторое время.")
        
        try:
            # Скачать ZIP файл
            bot = update.get_bot()
            file = await bot.get_file(document.file_id)
            temp_bytes = await file.download_as_bytearray()

            result = await asyncio.to_thread(
                backend_client.ingest_wiki_zip,
                kb_id=kb_id,
                url=wiki_url,
                zip_bytes=bytes(temp_bytes),
                filename=document.file_name or f"wiki_{document.file_id}.zip",
                telegram_id=str(update.effective_user.id) if update.effective_user else None,
                username=update.effective_user.username if update.effective_user else None,
            )

            deleted = result.get("deleted_chunks", 0)
            files = result.get("files_processed", 0)
            added = result.get("chunks_added", 0)
            wiki_root = result.get("wiki_root", wiki_url)
            text = (
                "✅ Загрузка вики из ZIP архива завершена.\n\n"
                f"Исходный URL: {wiki_url}\n"
                f"Корневой wiki-URL: {wiki_root}\n"
                f"Удалено старых фрагментов: {deleted}\n"
                f"Обработано файлов: {files}\n"
                f"Добавлено фрагментов: {added}"
            )
            from frontend.templates.buttons import kb_actions_menu
            await update.message.reply_text(text, reply_markup=kb_actions_menu(kb_id))
        except Exception as e:
            logger.error(f"Ошибка при загрузке вики из ZIP (backend): {e}", exc_info=True)
            await update.message.reply_text(
                f"❌ Ошибка при загрузке вики из ZIP: {str(e)}\n\n"
                "Убедитесь, что:\n"
                "• ZIP архив содержит markdown файлы (.md)\n"
                "• Структура архива соответствует структуре вики\n"
                "• URL вики корректный"
            )
        finally:
            # Очистить состояние
            context.user_data.pop('state', None)
            context.user_data.pop('wiki_zip_kb_id', None)
            context.user_data.pop('wiki_zip_url', None)
        return

    # Импорт истории чата для аналитики
    if state == 'waiting_analytics_import':
        chat_id = context.user_data.get('analytics_import_chat_id')
        if not chat_id:
            await update.message.reply_text("❌ Ошибка: не найден ID чата для импорта.")
            context.user_data.pop('state', None)
            context.user_data.pop('analytics_import_chat_id', None)
            return

        await update.message.reply_text("🔄 Импорт истории чата...\nЭто может занять некоторое время.")

        try:
            bot = update.get_bot()
            tg_file = await bot.get_file(document.file_id)
            file_bytes = await tg_file.download_as_bytearray()

            result = await asyncio.to_thread(
                backend_client.analytics_import_history,
                file_bytes=bytes(file_bytes),
                chat_id=chat_id,
                format_hint=file_type,
                filename=file_name or f"import_{document.file_id}",
            )

            if result:
                imported = result.get('messages_imported', 0)
                skipped = result.get('messages_skipped', 0)
                total = result.get('messages_found', 0)
                import_id = result.get('import_id', '')
                text = (
                    f"✅ Импорт завершён!\n\n"
                    f"Найдено сообщений: {total}\n"
                    f"Импортировано: {imported}\n"
                    f"Пропущено (дубликаты): {skipped}"
                )
                if import_id:
                    text += f"\n\nID импорта: {import_id}"
            else:
                text = "❌ Ошибка при импорте истории чата."

            from frontend.templates.buttons import analytics_chat_menu
            await update.message.reply_text(text, reply_markup=analytics_chat_menu(chat_id))
        except Exception as e:
            logger.error("Ошибка при импорте истории чата: %s", e, exc_info=True)
            await update.message.reply_text(f"❌ Ошибка при импорте: {str(e)}")
        finally:
            context.user_data.pop('state', None)
            context.user_data.pop('analytics_import_chat_id', None)
        return

    # Обычная загрузка документа
    kb_id = context.user_data.get('kb_id')
    
    logger.info("Получен документ: file_name=%s, file_type=%s, kb_id=%s", file_name, file_type, kb_id)
    
    # Если база знаний не выбрана, показать меню выбора
    if not kb_id:
        kbs = await asyncio.to_thread(backend_client.list_knowledge_bases)
        if not kbs:
            await update.message.reply_text("Сначала создайте базу знаний в админ-панели.")
            return
        # Сохранить информацию о документе для последующей загрузки
        context.user_data['pending_document'] = {
            'file_id': document.file_id,
            'file_name': document.file_name,
            'file_type': file_type
        }
        await update.message.reply_text("Выберите базу знаний для загрузки документа:", reply_markup=knowledge_base_menu(kbs))
        return
    
    # Загрузить документ напрямую через backend
    await load_document_to_kb(update, context, {
        'file_id': document.file_id,
        'file_name': document.file_name,
        'file_type': file_type
    }, kb_id)
    
    context.user_data['kb_id'] = None


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик изображений"""
    user = await check_user(update)
    if not user:
        return
    
    photo = update.message.photo[-1]  # Самое большое изображение
    temp_path = None
    
    try:
        file = await context.bot.get_file(photo.file_id)
        # Уникальное имя файла во избежание коллизий
        temp_path = os.path.join(tempfile.gettempdir(), f"{photo.file_id}_{os.getpid()}_{int(datetime.now().timestamp())}.jpg")
        await file.download_to_drive(temp_path)
        # Проверить, нужно ли загрузить в RAG
        kb_id = context.user_data.get('kb_id')
        
        # Выбрать модель пользователя для изображений (если не указана, использовать текстовую)
        image_model = getattr(user, 'preferred_image_model', None) or (user.preferred_model if user.preferred_model else None)
        
        if kb_id:
            # Загрузить изображение в RAG через backend
            with open(temp_path, "rb") as f:
                img_bytes = f.read()

            tg_id = str(update.effective_user.id) if update.effective_user else ""
            username = update.effective_user.username if update.effective_user else ""

            try:
                result = await asyncio.to_thread(
                    backend_client.ingest_image,
                    kb_id=kb_id,
                    file_id=photo.file_id,
                    image_bytes=img_bytes,
                    telegram_id=tg_id or None,
                    username=username or None,
                    model=image_model,
                )
                chunks_added = int(result.get("chunks_added", 0)) if result else 0
                if chunks_added > 0:
                    await update.message.reply_text(
                        "✅ Изображение обработано и добавлено в базу знаний!",
                        reply_markup=admin_menu(),
                    )
                else:
                    await update.message.reply_text(
                        "⚠️ Backend не вернул добавленные фрагменты для этого изображения.",
                        reply_markup=admin_menu(),
                    )
            except Exception as e:  # noqa: BLE001
                await update.message.reply_text(f"❌ Ошибка загрузки изображения в базу знаний: {str(e)}")
        else:
            # Просто описать изображение, используя выбранную модель
            description = image_processor.describe_image(
                temp_path,
                "Опиши подробно, что изображено на этой картинке. Будь детальным и точным.",
                model=image_model,
            )
            menu = main_menu(is_admin=(user.role == 'admin'))
            # Форматируем описание изображения с HTML
            from shared.utils import format_for_telegram_answer
            description_html = format_for_telegram_answer(description, enable_citations=False)
            answer = f"🖼️ <b>Описание изображения:</b>\n\n{description_html}"
            try:
                await update.message.reply_text(answer, reply_markup=menu, parse_mode='HTML')
            except Exception as e:
                logger.warning("Ошибка форматирования HTML, отправляю без форматирования: %s", e)
                answer_plain = strip_html_tags(f"🖼️ Описание изображения:\n\n{description}")
                await update.message.reply_text(answer_plain, reply_markup=menu, parse_mode=None)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка обработки изображения: {str(e)}")
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик голосовых сообщений (ASR)."""
    user = await check_user(update)
    if not user:
        return

    voice = update.message.voice if update.message else None
    if not voice:
        return

    status_message = await update.message.reply_text(
        "🎙️ Получено голосовое сообщение. Ставлю в очередь на распознавание..."
    )
    def _size_limit_message(file_size: int, limit_bytes: int, is_telegram_limit: bool) -> str:
        header = "❌ Аудио слишком большое для распознавания.\n"
        if is_telegram_limit:
            header = (
                "❌ Бот не может скачать файл больше 20MB из Telegram.\n"
                "Пожалуйста, отправьте файл меньше 20MB.\n"
            )
        return (
            f"{header}"
            f"Лимит: {limit_bytes} байт\n"
            f"Размер файла: {file_size} байт"
        )

    try:
        backend_limit = get_asr_max_file_bytes()
        telegram_limit = get_telegram_file_max_bytes()
        effective_limit = min(backend_limit, telegram_limit)
        is_telegram_limit = effective_limit == telegram_limit
        if voice.file_size and voice.file_size > effective_limit:
            await update.message.reply_text(
                _size_limit_message(voice.file_size, effective_limit, is_telegram_limit)
            )
            return

        file = await context.bot.get_file(voice.file_id)
        file_bytes = await file.download_as_bytearray()

        tg_id = str(update.effective_user.id) if update.effective_user else ""
        msg_id = str(update.message.message_id) if update.message else ""
        message_date = None
        if update.message and update.message.date:
            try:
                message_date = update.message.date.isoformat()
            except Exception:
                message_date = None
        content_type = getattr(voice, "mime_type", None) or "audio/ogg"

        logger.info("ASR voice upload: file_id=%s mime_type=%s", voice.file_id, content_type)

        result = await asyncio.to_thread(
            backend_client.asr_transcribe,
            file_name=f"{voice.file_id}.ogg",
            file_bytes=bytes(file_bytes),
            telegram_id=tg_id,
            message_id=msg_id,
            language=None,
            message_date=message_date,
            content_type=content_type,
        )
        job_id = result.get("job_id")
        if not job_id:
            error_payload = result.get("error_payload") or {}
            error_detail = error_payload.get("error") if isinstance(error_payload, dict) else None
            if isinstance(error_detail, dict):
                message = error_detail.get("message") or "Не удалось создать задачу распознавания."
                limit_bytes = error_detail.get("limit_bytes")
                size_bytes = error_detail.get("size_bytes")
                if limit_bytes and size_bytes:
                    message = (
                        f"❌ {message}\n"
                        f"Лимит: {limit_bytes} байт\n"
                        f"Размер файла: {size_bytes} байт"
                    )
                else:
                    message = f"❌ {message}"
            else:
                message = "❌ Не удалось создать задачу распознавания. Попробуйте позже."
            await status_message.edit_text(message)
            return

        queue_position = result.get("queue_position")
        await status_message.edit_text(
            f"⏳ В очереди на распознавание. Позиция: {queue_position or '?'}.\nJob ID: {job_id}"
        )

        last_status = "queued"
        for _ in range(30):
            await asyncio.sleep(2)
            job = await asyncio.to_thread(backend_client.asr_job_status, job_id)
            status = job.get("status")
            if not status:
                continue
            if status != last_status and status == "processing":
                try:
                    await status_message.edit_text(f"🛠️ Распознаю сообщение...\nJob ID: {job_id}")
                except Exception:
                    pass
                last_status = status
            if status == "done":
                text = (job.get("text") or "").strip()
                audio_meta = job.get("audio_meta") or {}
                
                # Формируем тех. информацию
                meta_block = ""
                if user.show_asr_metadata:
                    meta_lines = []
                    original_name = audio_meta.get("original_name") or voice.file_id
                    duration_s = audio_meta.get("duration_s")
                    size_bytes = audio_meta.get("size_bytes")
                    sample_rate = audio_meta.get("sample_rate")
                    channels = audio_meta.get("channels")
                    sent_at = audio_meta.get("sent_at")
                    
                    if original_name:
                        meta_lines.append(f"Файл: {escape(original_name)}")
                    if duration_s:
                        meta_lines.append(f"Длительность: {duration_s:.1f}с")
                    if size_bytes:
                        meta_lines.append(f"Размер: {size_bytes} байт")
                    if sample_rate:
                        meta_lines.append(f"Частота: {sample_rate} Гц")
                    if channels:
                        meta_lines.append(f"Каналы: {channels}")
                    if sent_at:
                        meta_lines.append(f"Отправлено: {escape(str(sent_at))}")
                    
                    if meta_lines:
                        meta_content = "\n".join(meta_lines)
                        meta_block = f"\n\n<blockquote expandable>{meta_content}</blockquote>"

                if text:
                    message_text = f"📝 <b>Транскрипция</b>\n\n{escape(text)}{meta_block}"
                    await update.message.reply_text(message_text, parse_mode='HTML')
                else:
                    await update.message.reply_text("⚠️ Распознавание завершилось без текста.")
                return
            if status == "error":
                error = job.get("error") or "Неизвестная ошибка"
                await update.message.reply_text(f"❌ Ошибка распознавания: {error}")
                return

        await update.message.reply_text(
            f"⏳ Распознавание продолжается. Попробуйте позже. Job ID: {job_id}"
        )
    except Exception as e:  # noqa: BLE001
        if "File is too big" in str(e) and voice.file_size:
            backend_limit = get_asr_max_file_bytes()
            telegram_limit = get_telegram_file_max_bytes()
            effective_limit = min(backend_limit, telegram_limit)
            is_telegram_limit = effective_limit == telegram_limit
            await update.message.reply_text(
                _size_limit_message(voice.file_size, effective_limit, is_telegram_limit)
            )
        else:
            await update.message.reply_text(f"❌ Ошибка обработки голосового сообщения: {str(e)}")


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик аудиофайлов (ASR)."""
    user = await check_user(update)
    if not user:
        return

    audio = update.message.audio if update.message else None
    document = update.message.document if update.message else None
    file_info = audio or document
    if not file_info:
        return

    status_message = await update.message.reply_text(
        "🎵 Получен аудиофайл. Ставлю в очередь на распознавание..."
    )
    def _size_limit_message(file_size: int, limit_bytes: int, is_telegram_limit: bool) -> str:
        header = "❌ Аудио слишком большое для распознавания.\n"
        if is_telegram_limit:
            header = (
                "❌ Бот не может скачать файл больше 20MB из Telegram.\n"
                "Пожалуйста, отправьте файл меньше 20MB.\n"
            )
        return (
            f"{header}"
            f"Лимит: {limit_bytes} байт\n"
            f"Размер файла: {file_size} байт"
        )

    try:
        backend_limit = get_asr_max_file_bytes()
        telegram_limit = get_telegram_file_max_bytes()
        effective_limit = min(backend_limit, telegram_limit)
        is_telegram_limit = effective_limit == telegram_limit
        file_size = getattr(file_info, "file_size", None)
        if file_size and file_size > effective_limit:
            await update.message.reply_text(
                _size_limit_message(file_size, effective_limit, is_telegram_limit)
            )
            return

        file = await context.bot.get_file(file_info.file_id)
        file_bytes = await file.download_as_bytearray()

        tg_id = str(update.effective_user.id) if update.effective_user else ""
        msg_id = str(update.message.message_id) if update.message else ""
        message_date = None
        if update.message and update.message.date:
            try:
                message_date = update.message.date.isoformat()
            except Exception:
                message_date = None
        file_name = getattr(file_info, "file_name", None) or f"{file_info.file_id}.mp3"
        content_type = getattr(file_info, "mime_type", None) or "application/octet-stream"

        logger.info(
            "ASR audio upload: file_id=%s filename=%s mime_type=%s",
            file_info.file_id,
            file_name,
            content_type,
        )

        result = await asyncio.to_thread(
            backend_client.asr_transcribe,
            file_name=file_name,
            file_bytes=bytes(file_bytes),
            telegram_id=tg_id,
            message_id=msg_id,
            language=None,
            message_date=message_date,
            content_type=content_type,
        )
        job_id = result.get("job_id")
        if not job_id:
            error_payload = result.get("error_payload") or {}
            error_detail = error_payload.get("error") if isinstance(error_payload, dict) else None
            if isinstance(error_detail, dict):
                message = error_detail.get("message") or "Не удалось создать задачу распознавания."
                limit_bytes = error_detail.get("limit_bytes")
                size_bytes = error_detail.get("size_bytes")
                if limit_bytes and size_bytes:
                    message = (
                        f"❌ {message}\n"
                        f"Лимит: {limit_bytes} байт\n"
                        f"Размер файла: {size_bytes} байт"
                    )
                else:
                    message = f"❌ {message}"
            else:
                message = "❌ Не удалось создать задачу распознавания. Попробуйте позже."
            await status_message.edit_text(message)
            return

        queue_position = result.get("queue_position")
        await status_message.edit_text(
            f"⏳ В очереди на распознавание. Позиция: {queue_position or '?'}.\nJob ID: {job_id}"
        )

        last_status = "queued"
        for _ in range(30):
            await asyncio.sleep(2)
            job = await asyncio.to_thread(backend_client.asr_job_status, job_id)
            status = job.get("status")
            if not status:
                continue
            if status != last_status and status == "processing":
                try:
                    await status_message.edit_text(f"🛠️ Распознаю аудиофайл...\nJob ID: {job_id}")
                except Exception:
                    pass
                last_status = status
            if status == "done":
                text = (job.get("text") or "").strip()
                audio_meta = job.get("audio_meta") or {}
                
                # Формируем тех. информацию
                meta_block = ""
                if user.show_asr_metadata:
                    meta_lines = []
                    original_name = audio_meta.get("original_name") or file_name
                    duration_s = audio_meta.get("duration_s")
                    size_bytes = audio_meta.get("size_bytes")
                    sample_rate = audio_meta.get("sample_rate")
                    channels = audio_meta.get("channels")
                    sent_at = audio_meta.get("sent_at")
                    
                    if original_name:
                        meta_lines.append(f"Файл: {escape(original_name)}")
                    if duration_s:
                        meta_lines.append(f"Длительность: {duration_s:.1f}с")
                    if size_bytes:
                        meta_lines.append(f"Размер: {size_bytes} байт")
                    if sample_rate:
                        meta_lines.append(f"Частота: {sample_rate} Гц")
                    if channels:
                        meta_lines.append(f"Каналы: {channels}")
                    if sent_at:
                        meta_lines.append(f"Отправлено: {escape(str(sent_at))}")
                    
                    if meta_lines:
                        meta_content = "\n".join(meta_lines)
                        meta_block = f"\n\n<blockquote expandable>{meta_content}</blockquote>"

                if text:
                    message_text = f"📝 <b>Транскрипция</b>\n\n{escape(text)}{meta_block}"
                    await update.message.reply_text(message_text, parse_mode='HTML')
                else:
                    await update.message.reply_text("⚠️ Распознавание завершилось без текста.")
                return
            if status == "error":
                error = job.get("error") or "Неизвестная ошибка"
                await update.message.reply_text(f"❌ Ошибка распознавания: {error}")
                return

        await update.message.reply_text(
            f"⏳ Распознавание продолжается. Попробуйте позже. Job ID: {job_id}"
        )
    except Exception as e:  # noqa: BLE001
        file_size = getattr(file_info, "file_size", None)
        if "File is too big" in str(e) and file_size:
            backend_limit = get_asr_max_file_bytes()
            telegram_limit = get_telegram_file_max_bytes()
            effective_limit = min(backend_limit, telegram_limit)
            is_telegram_limit = effective_limit == telegram_limit
            await update.message.reply_text(
                _size_limit_message(file_size, effective_limit, is_telegram_limit)
            )
        else:
            await update.message.reply_text(f"❌ Ошибка обработки аудиофайла: {str(e)}")


async def load_web_page(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, kb_id: int):
    """Загрузить веб-страницу в базу знаний через backend ingestion API."""
    try:
        tg_id = str(update.effective_user.id) if update.effective_user else ""
        username = update.effective_user.username if update.effective_user else ""

        result = await asyncio.to_thread(
            backend_client.ingest_web_page,
            kb_id=kb_id,
            url=url,
            telegram_id=tg_id or None,
            username=username or None,
        )
        chunks_added = int(result.get("chunks_added", 0)) if result else 0
        job_id = result.get("job_id") if result else None
        if chunks_added > 0:
            await update.message.reply_text(
                f"✅ Загружено {chunks_added} фрагментов с веб-страницы!",
                reply_markup=admin_menu(),
            )
        elif job_id:
            await update.message.reply_text(
                f"⏳ Задача запущена. Job ID: {job_id}. Статус можно проверить в /jobs/{job_id}.",
                reply_markup=admin_menu(),
            )
        else:
            await update.message.reply_text(
                "⚠️ Backend не вернул добавленные фрагменты для этой страницы.",
                reply_markup=admin_menu(),
            )
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка загрузки веб-страницы: {str(e)}")


# ── Chat Analytics: message collector ──────────────────────────────────

async def message_collector(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Collect text messages from groups/supergroups for analytics.

    Registered with group=10 so it runs AFTER normal handlers.
    Fire-and-forget: does not block the main bot flow.
    """
    msg = update.effective_message
    if not msg or not msg.text:
        return

    chat = update.effective_chat
    if not chat or chat.type not in ('group', 'supergroup'):
        return

    user = update.effective_user

    payload = {
        "chat_id": str(chat.id),
        "thread_id": getattr(msg, "message_thread_id", None),
        "message_id": msg.message_id,
        "author_telegram_id": str(user.id) if user else None,
        "author_username": user.username if user else None,
        "author_display_name": user.full_name if user else None,
        "text": msg.text,
        "timestamp": msg.date.isoformat() if msg.date else None,
        "message_link": _build_message_link(chat.id, msg.message_id),
        "is_bot_message": user.is_bot if user else False,
        "is_system_message": False,
    }

    asyncio.create_task(_send_to_analytics(payload))


async def _send_to_analytics(payload: dict) -> None:
    """Send message to backend analytics endpoint (fire-and-forget)."""
    try:
        await asyncio.to_thread(backend_client.analytics_store_message, payload)
    except Exception as e:
        logger.debug("Analytics store_message failed: %s", e)


def _build_message_link(chat_id: int, message_id: int) -> str:
    """Build Telegram message link for supergroups."""
    clean_id = str(chat_id).replace("-100", "")
    return f"https://t.me/c/{clean_id}/{message_id}"
