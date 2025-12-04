"""
Обработчики команд и сообщений для бота
"""
import os
import tempfile
import hashlib
from datetime import datetime, timezone
from typing import Optional
from telegram import Update
from telegram.ext import ContextTypes
from database import Session, User, Message, KnowledgeBase, KnowledgeImportLog
from logging_config import logger
from ai_providers import ai_manager
from rag_system import rag_system
from document_loaders import document_loader_manager
from image_processor import image_processor
from web_search import search_web, format_search_results
from utils import format_text_safe, create_prompt_with_language, detect_language
from urllib.parse import urlparse, parse_qs, unquote


def _normalize_wiki_url_for_display(url: str) -> str:
    """Нормализовать URL вики для отображения (конвертировать export URL в читаемый формат)"""
    if not url or not url.startswith(('http://', 'https://')):
        return url
    
    # Если это export URL Gitee вики, конвертируем в нормальный формат
    # Пример: https://gitee.com/.../wikis/pages/export?type=markdown&doc_id=2921510
    # -> https://gitee.com/.../wikis/Sync&Build/Sync%26Build
    if '/wikis/pages/export' in url:
        try:
            parsed = urlparse(url)
            query_params = parse_qs(parsed.query)
            
            # Извлекаем doc_id из query параметров
            if 'doc_id' in query_params:
                doc_id = query_params['doc_id'][0]
                # Строим нормальный URL вики
                # Базовый путь до /wikis
                path_parts = parsed.path.split('/wikis')
                if len(path_parts) >= 2:
                    base_path = path_parts[0] + '/wikis'
                    # Попытаемся найти название страницы из других параметров или использовать doc_id
                    # Для Gitee обычно можно использовать doc_id для построения URL
                    # Но лучше использовать оригинальный URL если он есть в метаданных
                    # Пока просто возвращаем базовый путь вики
                    return f"{parsed.scheme}://{parsed.netloc}{base_path}"
        except Exception:
            pass
    
    # Если это обычный URL вики, возвращаем как есть
    return url
from templates.buttons import (
    main_menu, admin_menu, settings_menu, ai_providers_menu,
    user_management_menu, knowledge_base_menu, kb_actions_menu,
    document_type_menu, confirm_menu, search_options_menu
)
from config import ADMIN_IDS
from n8n_client import n8n_client

session = Session()


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

    kb = rag_system.get_knowledge_base(kb_id)
    payload = {
        "knowledge_base": {
            "id": kb_id,
            "name": getattr(kb, "name", None) if kb else None,
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


async def check_user(update: Update) -> Optional[User]:
    """Проверить и зарегистрировать пользователя"""
    user_id = str(update.effective_user.id)
    user_id_int = int(update.effective_user.id)
    
    # Проверить, является ли пользователь администратором
    is_admin = user_id_int in ADMIN_IDS
    
    user = session.query(User).filter_by(telegram_id=user_id).first()
    
    if not user:
        # Новый пользователь
        user = User(
            telegram_id=user_id,
            username=update.effective_user.username or user_id,
            approved=is_admin,  # Автоматически одобрить администраторов
            role='admin' if is_admin else 'user'
        )
        session.add(user)
        session.commit()
        
        if is_admin:
            # Администратор - сразу одобрен
            if update.message:
                await update.message.reply_text("✅ Вы администратор. Доступ предоставлен.")
            return user
        else:
            # Обычный пользователь - отправить уведомление админам
            for admin in ADMIN_IDS:
                try:
                    await update.get_bot().send_message(
                        chat_id=admin,
                        text=f"Новая заявка: @{user.username} (ID: {user_id})",
                        reply_markup=approve_menu(user_id)
                    )
                except:
                    pass
            
            if update.message:
                await update.message.reply_text("Отправлен запрос на регистрацию. Ожидайте одобрения.")
            return None
    
    # Пользователь существует - проверить и обновить статус администратора
    if is_admin:
        # Если пользователь в списке администраторов, автоматически одобрить и сделать админом
        if not user.approved or user.role != 'admin':
            user.approved = True
            user.role = 'admin'
            session.commit()
        return user
    
    if not user.approved:
        # Пользователь не одобрен - отправить сообщение
        if update.message:
            await update.message.reply_text("⏳ Ваша заявка еще не одобрена администратором. Пожалуйста, подождите.")
        return None
    
    return user


from templates.buttons import approve_menu


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
    
    if state == 'waiting_query':
        # Поиск в базе знаний
        query = update.message.text
        # Получить настройки RAG из конфига
        try:
            from config import RAG_TOP_K
            top_k_search = RAG_TOP_K
        except ImportError:
            top_k_search = 10
        
        # Увеличиваем top_k для лучшей релевантности
        results = rag_system.search(query, top_k=top_k_search)
        logger.info("Поиск в БЗ: user=%s, query=%r, найдено фрагментов=%s", user.telegram_id, query, len(results))
        
        # Использовать ИИ для формирования ответа
        if results:
            # Получить настройки RAG из конфига
            try:
                from config import RAG_TOP_K, RAG_CONTEXT_LENGTH, RAG_ENABLE_CITATIONS
                top_k_for_context = RAG_TOP_K
                context_length = RAG_CONTEXT_LENGTH
                enable_citations = RAG_ENABLE_CITATIONS
            except ImportError:
                top_k_for_context = 8
                context_length = 1200
                enable_citations = True
            
            # Сформировать контекст с указанием источников и source_id тегами для citations
            context_parts = []
            sources = []
            for idx, r in enumerate(results[:top_k_for_context], start=1):
                source_type = r.get('source_type') or 'unknown'
                source_path = r.get('source_path') or ''
                meta = r.get('metadata') or {}
                title = meta.get('title') or source_path or 'Без названия'
                doc_version = meta.get('doc_version')
                language = meta.get('language')
                updated_at = meta.get('source_updated_at')

                # Формируем source_id для citation (имя файла без расширения или путь)
                if source_path and '.keep' not in source_path.lower():
                    # Извлекаем имя файла для source_id
                    if '::' in source_path:
                        # Для архивов: берем имя файла внутри архива
                        source_id = source_path.split('::')[-1]
                    elif '/' in source_path:
                        # Для URL или путей: берем последний сегмент
                        source_id = source_path.split('/')[-1]
                    else:
                        source_id = source_path
                    # Убираем расширение для более читаемого citation
                    source_id = source_id.rsplit('.', 1)[0] if '.' in source_id else source_id
                else:
                    source_id = title.replace(' ', '_').lower()[:50]  # Fallback на title

                content_preview = r['content'][:context_length]
                if len(r['content']) > context_length:
                    content_preview += "..."
                
                # Формируем контекст с тегом <source_id> для inline citations
                if enable_citations:
                    context_parts.append(
                        f"<source_id>{source_id}</source_id>\n{content_preview}"
                    )
                else:
                    header = f"=== Источник {idx}: {title} ==="
                    context_parts.append(
                        f"{header}\n{content_preview}"
                    )

                # Формируем краткую информацию об источнике для списка в конце (в HTML формате)
                from html import escape
                if source_path and '.keep' not in source_path.lower() and source_path.startswith(('http://', 'https://')):
                    # Для URL создаем HTML ссылку
                    display_path = _normalize_wiki_url_for_display(source_path)
                    url_for_link = source_path if source_path else display_path
                    
                    # Извлекаем название из пути для отображения
                    if '/' in url_for_link:
                        parts = [p for p in url_for_link.split('/') if p]
                        if parts:
                            title_from_url = parts[-1]
                        else:
                            title_from_url = url_for_link
                    else:
                        title_from_url = url_for_link
                    
                    # Декодируем URL для читаемости
                    title_from_url = unquote(title_from_url)
                    
                    # Если title из URL пустой или слишком короткий, используем title из метаданных
                    if not title_from_url or len(title_from_url) < 2:
                        parts = [p for p in url_for_link.split('/') if p]
                        if len(parts) > 1:
                            title_from_url = unquote(parts[-2])
                        else:
                            title_from_url = title
                    
                    # Используем title из метаданных, если он лучше
                    display_title = title if title and title != 'Без названия' else title_from_url
                    
                    title_escaped = escape(display_title)
                    url_escaped = escape(url_for_link)
                    source_info = f"{idx}. <a href=\"{url_escaped}\">{title_escaped}</a>"
                else:
                    # Для не-URL источников показываем просто текст
                    title_escaped = escape(title)
                    if source_path and '.keep' not in source_path.lower():
                        if '::' in source_path:
                            file_name = source_path.split('::')[-1]
                        elif '/' in source_path:
                            file_name = source_path.split('/')[-1]
                        else:
                            file_name = source_path
                        file_name_escaped = escape(file_name)
                        source_info = f"{idx}. <b>{title_escaped}</b> (<code>{file_name_escaped}</code>)"
                    else:
                        source_info = f"{idx}. <b>{title_escaped}</b>"
                sources.append(source_info)
            
            context_text = "\n\n".join(context_parts)
            prompt = create_prompt_with_language(
                query,
                context_text,
                task="answer",
                enable_citations=enable_citations,
            )
            model = user.preferred_model if user.preferred_model else None
            ai_answer = ai_manager.query(
                prompt,
                provider_name=user.preferred_provider,
                model=model,
            )
            
            # Форматируем ответ с HTML для лучшего форматирования
            from utils import format_markdown_to_html
            ai_answer_html = format_markdown_to_html(ai_answer)
            # Источники уже в HTML формате, просто добавляем маркеры списка
            sources_html = "\n".join([f"• {s}" for s in sources])
            answer_html = f"🤖 <b>Ответ:</b>\n\n{ai_answer_html}\n\n📎 <b>Использованные источники:</b>\n{sources_html}"
        else:
            # Если ничего не найдено, попробовать ответить через ИИ
            prompt = create_prompt_with_language(query, None, task="answer")
            model = user.preferred_model if user.preferred_model else None
            ai_answer = ai_manager.query(
                prompt, provider_name=user.preferred_provider, model=model
            )
            from utils import format_markdown_to_html
            from html import escape
            ai_answer_html = format_markdown_to_html(ai_answer)
            answer_html = f"🤖 <b>Ответ:</b>\n\n{ai_answer_html}\n\n<i>(В базе знаний ничего не найдено, ответ основан на общих знаниях)</i>"
        
        menu = main_menu(is_admin=(user.role == 'admin'))
        # Используем HTML для форматирования, но с безопасной обработкой ошибок
        try:
            await update.message.reply_text(answer_html, reply_markup=menu, parse_mode='HTML')
        except Exception as e:
            # Если HTML не работает, отправляем без форматирования
            logger.warning("Ошибка форматирования HTML, отправляю без форматирования: %s", e)
            answer_plain = format_text_safe(answer_html)
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
            
            # Форматировать ответ с HTML
            from utils import format_markdown_to_html
            ai_answer_html = format_markdown_to_html(ai_answer)
            
            # Добавить ссылки в HTML формате
            sources_html_parts = []
            from html import escape
            for i, result in enumerate(results[:3], 1):
                url = result.get('url', '')
                title = result.get('title', 'Без названия')
                title_escaped = escape(title)
                if url:
                    sources_html_parts.append(f"• {i}. <a href=\"{url}\">{title_escaped}</a>")
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
            logger.warning("Ошибка форматирования HTML, отправляю без форматирования: %s", e)
            answer_plain = format_text_safe(answer_html)
            await update.message.reply_text(answer_plain, reply_markup=menu, parse_mode=None)
        context.user_data['state'] = None
        
    elif state == 'waiting_ai_query':
        # Прямой запрос к ИИ
        query = update.message.text
        prompt = create_prompt_with_language(query, None, task="answer")
        model = user.preferred_model if user.preferred_model else None
        ai_answer = ai_manager.query(prompt, provider_name=user.preferred_provider, model=model)
        
        # Форматируем ответ с HTML для лучшего форматирования
        from utils import format_markdown_to_html
        ai_answer_html = format_markdown_to_html(ai_answer)
        answer_html = f"🤖 <b>Ответ:</b>\n\n{ai_answer_html}"
        
        menu = main_menu(is_admin=(user.role == 'admin'))
        try:
            await update.message.reply_text(answer_html, reply_markup=menu, parse_mode='HTML')
        except Exception as e:
            logger.warning("Ошибка форматирования HTML, отправляю без форматирования: %s", e)
            answer_plain = format_text_safe(f"🤖 Ответ:\n\n{ai_answer}")
            await update.message.reply_text(answer_plain, reply_markup=menu, parse_mode=None)
        context.user_data['state'] = None
        
    elif state == 'waiting_url':
        # Загрузка веб-страницы
        url = update.message.text
        kb_id = context.user_data.get('kb_id')
        if kb_id:
            logger.info("Загрузка одной веб-страницы в БЗ: kb_id=%s, url=%s, user=%s", kb_id, url, user.telegram_id)
            await load_web_page(update, context, url, kb_id)
        context.user_data['state'] = None
    
    elif state == 'waiting_wiki_root':
        # Рекурсивный сбор wiki-раздела сайта
        from wiki_scraper import crawl_wiki_to_kb_async

        wiki_url = (update.message.text or "").strip()
        kb_id = context.user_data.get('kb_id_for_wiki')

        if not kb_id:
            await update.message.reply_text(
                "Не выбрана база знаний для загрузки вики. Сначала выберите БЗ в админ-панели.",
                reply_markup=admin_menu(),
            )
            context.user_data['state'] = None
            return

        await update.message.reply_text(
            "🚀 Запускаю рекурсивный обход вики.\n"
            "Это может занять несколько минут в зависимости от размеров раздела.",
        )
        logger.info("Старт сканирования вики из Telegram: kb_id=%s, url=%s, user=%s", kb_id, wiki_url, user.telegram_id)

        try:
            stats = await crawl_wiki_to_kb_async(wiki_url, kb_id, max_pages=500)
            deleted = stats.get("deleted_chunks", 0)
            pages = stats.get("pages_processed", 0)
            added = stats.get("chunks_added", 0)
            wiki_root = stats.get("wiki_root", wiki_url)

            # Записать в журнал загрузок
            tg_id = str(update.effective_user.id) if update.effective_user else ""
            db_user = session.query(User).filter_by(telegram_id=tg_id).first() if tg_id else None
            username = db_user.username if db_user else tg_id
            user_info = {"telegram_id": tg_id, "username": username}
            log = KnowledgeImportLog(
                knowledge_base_id=kb_id,
                user_telegram_id=tg_id,
                username=username,
                action_type="wiki",
                source_path=wiki_root,
                total_chunks=added,
            )
            session.add(log)
            session.commit()

            emit_n8n_import_event(
                kb_id=kb_id,
                action_type="wiki",
                source_path=wiki_root,
                total_chunks=added,
                user_info=user_info,
                extra={
                    "deleted_chunks": deleted,
                    "pages_processed": pages,
                    "wiki_root": wiki_root,
                    "original_url": wiki_url,
                },
            )

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
                from templates.buttons import InlineKeyboardButton, InlineKeyboardMarkup
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
            logger.error("Ошибка при сканировании вики: %s", e)
            await update.message.reply_text(
                f"❌ Ошибка при сканировании вики: {str(e)}",
                reply_markup=admin_menu(),
            )

        context.user_data['state'] = None
        context.user_data.pop('kb_id_for_wiki', None)
        
    elif state == 'waiting_kb_name':
        # Создание базы знаний
        kb_name = update.message.text
        kb = rag_system.add_knowledge_base(kb_name)
        await update.message.reply_text(f"✅ База знаний '{kb_name}' создана!", reply_markup=admin_menu())
        context.user_data['state'] = None
        
    elif state == 'waiting_user_delete':
        # Удаление пользователя
        if user.role != 'admin':
            await update.message.reply_text("Только администраторы могут удалять пользователей.")
            context.user_data['state'] = None
            return
        
        try:
            target_id = update.message.text.strip()
            target_user = session.query(User).filter_by(telegram_id=target_id).first()
            if target_user:
                username = target_user.username
                session.delete(target_user)
                session.commit()
                await update.message.reply_text(f"✅ Пользователь @{username} удален!", reply_markup=admin_menu())
            else:
                await update.message.reply_text("Пользователь не найден.", reply_markup=admin_menu())
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {str(e)}", reply_markup=admin_menu())
        context.user_data['state'] = None
        
    else:
        # Любой обычный текст без активного состояния считаем запросом к базе знаний
        query = update.message.text

        # Сохранить сообщение в историю (как и раньше)
        session.add(Message(
            chat_id=chat_id,
            user=update.effective_user.username or str(update.effective_user.id),
            text=query,
        ))
        session.commit()
        
        # Получить настройки RAG из конфига
        try:
            from config import RAG_TOP_K, RAG_MAX_CANDIDATES
            top_k_search = RAG_TOP_K
            max_candidates = RAG_MAX_CANDIDATES
        except ImportError:
            top_k_search = 10
            max_candidates = 100
        
        # Увеличиваем количество результатов для лучшего поиска
        # Reranker обработает больше кандидатов для лучшей релевантности
        search_top_k = min(top_k_search * 2, max_candidates)
        results = rag_system.search(query, top_k=search_top_k)
        
        # Берем только top_k лучших для контекста (reranker уже отсортировал)
        results = results[:top_k_search]
        
        # Фильтруем пустые файлы и файлы .keep
        filtered_results = []
        for r in results:
            content = r.get('content', '').strip()
            source_path = r.get('source_path', '')
            # Пропускаем пустые файлы и файлы .keep
            if not content or len(content) < 10:
                continue
            if '.keep' in source_path.lower() or source_path.endswith('/.keep'):
                continue
            filtered_results.append(r)
        
        # Если после фильтрации ничего не осталось, проверяем были ли вообще результаты
        if not filtered_results:
            # Если результатов нет, не используем общие знания
            # Попробуем найти похожие слова/темы из базы знаний
            similar_suggestions = []
            if results:
                # Берем первые несколько результатов для предложения похожих тем
                for r in results[:5]:
                    source_path = r.get('source_path', '')
                    meta = r.get('metadata') or {}
                    title = meta.get('title') or source_path or 'Без названия'
                    if source_path and '.keep' not in source_path.lower():
                        similar_suggestions.append({
                            'title': title,
                            'source_path': source_path
                        })
            
            if similar_suggestions:
                # Формируем список похожих тем
                suggestions_text = "Возможно, вас интересуют следующие темы из базы знаний:\n\n"
                for i, sug in enumerate(similar_suggestions[:5], 1):
                    display_url = _normalize_wiki_url_for_display(sug['source_path']) if sug['source_path'] else ''
                    from html import escape
                    title_escaped = escape(sug['title'])
                    if display_url and display_url.startswith(('http://', 'https://')):
                        suggestions_text += f"• {i}. <a href=\"{display_url}\">{title_escaped}</a>\n"
                    else:
                        suggestions_text += f"• {i}. <b>{title_escaped}</b>\n"
                
                answer = f"❌ <b>В базе знаний не найдено точного ответа на ваш вопрос.</b>\n\n{suggestions_text}"
            else:
                answer = "❌ <b>В базе знаний не найдено информации по вашему запросу.</b>\n\nПопробуйте переформулировать вопрос или загрузить соответствующие документы в базу знаний."
            
            menu = main_menu(is_admin=(user.role == 'admin'))
            try:
                await update.message.reply_text(answer, reply_markup=menu, parse_mode='HTML')
            except Exception as e:
                logger.warning("Ошибка форматирования HTML, отправляю без форматирования: %s", e)
                answer_plain = format_text_safe(answer)
                await update.message.reply_text(answer_plain, reply_markup=menu, parse_mode=None)
            return
        
        if filtered_results:
            # Получить настройки RAG из конфига
            try:
                from config import RAG_TOP_K, RAG_CONTEXT_LENGTH, RAG_ENABLE_CITATIONS
                top_k_for_context = RAG_TOP_K
                context_length = RAG_CONTEXT_LENGTH
                enable_citations = RAG_ENABLE_CITATIONS
            except ImportError:
                top_k_for_context = 8
                context_length = 1200
                enable_citations = True
            
            context_parts = []
            sources = []
            # Используем до top_k лучших результатов после фильтрации для лучшей релевантности
            for idx, r in enumerate(filtered_results[:top_k_for_context], start=1):
                source_type = r.get('source_type') or 'unknown'
                source_path = r.get('source_path') or ''
                meta = r.get('metadata') or {}
                title = meta.get('title') or source_path or 'Без названия'
                doc_version = meta.get('doc_version')
                language = meta.get('language')
                updated_at = meta.get('source_updated_at')
                
                # Формируем source_id для citation (имя файла без расширения или путь)
                if source_path and '.keep' not in source_path.lower():
                    # Извлекаем имя файла для source_id
                    if '::' in source_path:
                        # Для архивов: берем имя файла внутри архива
                        source_id = source_path.split('::')[-1]
                    elif '/' in source_path:
                        # Для URL или путей: берем последний сегмент
                        source_id = source_path.split('/')[-1]
                    else:
                        source_id = source_path
                    # Убираем расширение для более читаемого citation
                    source_id = source_id.rsplit('.', 1)[0] if '.' in source_id else source_id
                else:
                    source_id = title.replace(' ', '_').lower()[:50]  # Fallback на title
                
                content_preview = r['content'][:context_length]
                if len(r['content']) > context_length:
                    content_preview += "..."

                # Формируем контекст с тегом <source_id> для inline citations
                if enable_citations:
                    context_parts.append(
                        f"<source_id>{source_id}</source_id>\n{content_preview}"
                    )
                else:
                    header = f"=== Источник {idx}: {title} ==="
                    context_parts.append(
                        f"{header}\n{content_preview}"
                    )

                # Сохраняем информацию об источнике для формирования HTML списка
                sources.append({
                    'title': title,
                    'source_path': source_path,
                    'index': idx
                })
            
            context_text = "\n\n".join(context_parts)
            prompt = create_prompt_with_language(
                query,
                context_text,
                task="answer",
                enable_citations=enable_citations,
            )
            model = user.preferred_model if user.preferred_model else None
            ai_answer = ai_manager.query(
                prompt,
                provider_name=user.preferred_provider,
                model=model,
            )
            
            # Форматируем ответ с HTML для лучшего форматирования
            from utils import format_markdown_to_html
            ai_answer_html = format_markdown_to_html(ai_answer)
            
            # Формируем HTML список источников с ссылками
            # Группируем по уникальным URL для устранения дубликатов
            seen_urls = set()
            sources_html_parts = []
            source_counter = 1
            
            for source_data in sources:
                idx = source_data['index']
                title = source_data['title']
                source_path = source_data['source_path']
                
                # Нормализуем URL для вики
                display_url = _normalize_wiki_url_for_display(source_path) if source_path else source_path
                
                # Используем полный URL как ключ для группировки
                url_key = display_url if display_url else source_path
                
                # Пропускаем дубликаты
                if url_key in seen_urls:
                    continue
                seen_urls.add(url_key)
                
                # Экранируем title для HTML
                from html import escape
                title_escaped = escape(title)
                
                if display_url and display_url.startswith(('http://', 'https://')):
                    # Создаем HTML ссылку с полным URL и названием источника
                    sources_html_parts.append(f"• {source_counter}. <a href=\"{display_url}\">{title_escaped}</a>")
                else:
                    # Без ссылки (файл) - показываем имя файла
                    if source_path:
                        if '::' in source_path:
                            file_name = source_path.split('::')[-1]
                        elif '/' in source_path:
                            file_name = source_path.split('/')[-1]
                        else:
                            file_name = source_path
                        file_name_escaped = escape(file_name)
                        sources_html_parts.append(f"• {source_counter}. <b>{title_escaped}</b> (<code>{file_name_escaped}</code>)")
                    else:
                        sources_html_parts.append(f"• {source_counter}. <b>{title_escaped}</b>")
                
                source_counter += 1
            
            sources_html = "\n".join(sources_html_parts)
            answer_html = f"🤖 <b>Ответ:</b>\n\n{ai_answer_html}\n\n📎 <b>Использованные источники:</b>\n{sources_html}"
            answer = answer_html
        # Этот блок больше не нужен, так как мы обрабатываем отсутствие результатов выше

        menu = main_menu(is_admin=(user.role == 'admin'))
        # Используем HTML для форматирования, но с безопасной обработкой ошибок
        try:
            await update.message.reply_text(answer, reply_markup=menu, parse_mode='HTML')
        except Exception as e:
            # Если HTML не работает, отправляем без форматирования
            logger.warning("Ошибка форматирования HTML, отправляю без форматирования: %s", e)
            answer_plain = format_text_safe(answer)
            await update.message.reply_text(answer_plain, reply_markup=menu, parse_mode=None)


async def load_document_to_kb(query_or_update, context, document_info, kb_id):
    """Загрузить документ в базу знаний"""
    from telegram import Update
    is_update = isinstance(query_or_update, Update)
    temp_path = None
    
    try:
        if is_update:
            bot = query_or_update.get_bot()
            file = await bot.get_file(document_info['file_id'])
            message = query_or_update.message
        else:
            bot = query_or_update.message.bot if hasattr(query_or_update, 'message') else context.bot
            file = await bot.get_file(document_info['file_id'])
            message = None
        
        temp_path = os.path.join(tempfile.gettempdir(), f"{document_info['file_id']}.{document_info['file_type']}")
        await file.download_to_drive(temp_path)
        
        # Определить пользователя для журнала загрузок
        try:
            if is_update and query_or_update.effective_user:
                tg_id = str(query_or_update.effective_user.id)
            else:
                tg_id = str(query_or_update.from_user.id) if hasattr(query_or_update, "from_user") else ""
        except Exception:
            tg_id = ""
        db_user = session.query(User).filter_by(telegram_id=tg_id).first() if tg_id else None
        username = db_user.username if db_user else tg_id
        user_info = {"telegram_id": tg_id, "username": username}

        file_type = (document_info['file_type'] or '').lower()

        # Поддержка архивов (zip)
        per_file_stats = []
        total_chunks = 0

        if file_type in ("zip",):
            import zipfile

            with zipfile.ZipFile(temp_path, 'r') as zf:
                for name in zf.namelist():
                    # Пропустить каталоги
                    if name.endswith('/'):
                        continue
                    # Пропустить файлы .keep и другие служебные файлы
                    if '.keep' in name.lower() or name.endswith('.keep'):
                        continue
                    inner_ext = os.path.splitext(name)[1].lstrip('.').lower()
                    # Извлечь во временный файл
                    with zf.open(name) as src, tempfile.NamedTemporaryFile(delete=False, suffix=f".{inner_ext}") as dst:
                        data = src.read()
                        dst.write(data)
                        inner_path = dst.name
                    # Хеш содержимого файла для идентификации версии
                    doc_hash = hashlib.sha256(data).hexdigest()
                    # В качестве source_path используем имя файла внутри архива,
                    # чтобы источники отображались как реальный документ, а не архив.
                    source_path = name

                    # Удалить старые фрагменты этой версии документа (обновление)
                    rag_system.delete_chunks_by_source_exact(
                        knowledge_base_id=kb_id,
                        source_type=inner_ext or 'unknown',
                        source_path=source_path,
                    )
                    try:
                        chunks = document_loader_manager.load_document(inner_path, inner_ext or None)
                        # Фильтруем пустые чанки (менее 10 символов)
                        chunks = [chunk for chunk in chunks if chunk.get('content', '').strip() and len(chunk.get('content', '').strip()) > 10]
                    except Exception:
                        chunks = []
                    added = 0
                    # Версия документа — порядковый номер загрузки этого источника
                    existing_logs = session.query(KnowledgeImportLog).filter_by(
                        knowledge_base_id=kb_id,
                        source_path=source_path,
                    ).count()
                    doc_version = existing_logs + 1
                    source_updated_at = datetime.now(timezone.utc).isoformat()

                    for chunk in chunks:
                        content = chunk.get('content', '')
                        base_meta = dict(chunk.get('metadata') or {})
                        base_meta.setdefault('title', chunk.get('title') or name)
                        base_meta['language'] = detect_language(content) if content else 'ru'
                        base_meta['doc_hash'] = doc_hash
                        base_meta['doc_version'] = doc_version
                        base_meta['source_updated_at'] = source_updated_at

                        rag_system.add_chunk(
                            knowledge_base_id=kb_id,
                            content=content,
                            source_type=inner_ext or 'unknown',
                            source_path=source_path,
                            metadata=base_meta,
                        )
                        added += 1
                    total_chunks += added
                    per_file_stats.append((name, added))
                    # Записать в журнал загрузок для каждого файла
                    log = KnowledgeImportLog(
                        knowledge_base_id=kb_id,
                        user_telegram_id=tg_id,
                        username=username,
                        action_type="archive",
                        source_path=source_path,
                        total_chunks=added,
                    )
                    session.add(log)
                    session.commit()
                    emit_n8n_import_event(
                        kb_id=kb_id,
                        action_type="archive",
                        source_path=source_path,
                        total_chunks=added,
                        user_info=user_info,
                        extra={
                            "archive_name": document_info.get('file_name'),
                            "inner_file": name,
                            "doc_hash": doc_hash,
                            "doc_version": doc_version,
                            "source_updated_at": source_updated_at,
                        },
                    )
                    try:
                        os.remove(inner_path)
                    except Exception:
                        pass

            if per_file_stats:
                # Ограничить вывод, чтобы не превысить лимит Telegram (4096 символов)
                MAX_MESSAGE_LENGTH = 3500  # Оставляем запас
                MAX_FILES_TO_SHOW = 50  # Показывать максимум 50 файлов
                
                lines = ["✅ Архив обработан. Загрузка файлов в базу знаний:"]
                total_files = len(per_file_stats)
                total_chunks_all = sum(added for _, added in per_file_stats)
                
                # Показать статистику по первым файлам
                shown_count = 0
                for name, added in per_file_stats[:MAX_FILES_TO_SHOW]:
                    line = f"- {name}: фрагментов {added}"
                    if len("\n".join(lines) + "\n" + line) > MAX_MESSAGE_LENGTH:
                        break
                    lines.append(line)
                    shown_count += 1
                
                # Добавить итоговую статистику
                if total_files > shown_count:
                    lines.append(f"\n... и еще {total_files - shown_count} файлов")
                
                lines.append(f"\n📊 Итого: {total_files} файлов, {total_chunks_all} фрагментов")
                
                response_text = "\n".join(lines)
                
                # Если сообщение все еще слишком длинное, сократить еще больше
                if len(response_text) > MAX_MESSAGE_LENGTH:
                    response_text = (
                        f"✅ Архив обработан!\n\n"
                        f"📊 Обработано файлов: {total_files}\n"
                        f"📝 Всего фрагментов: {total_chunks_all}\n\n"
                        f"(Показаны первые {shown_count} файлов из {total_files})"
                    )
            else:
                response_text = "⚠️ Архив не содержит поддерживаемых файлов для загрузки."
        else:
            # Обычный одиночный документ
            with open(temp_path, 'rb') as f:
                data = f.read()
            doc_hash = hashlib.sha256(data).hexdigest()
            source_path = document_info['file_name'] or ''

            # Удалить старые фрагменты этого документа (если загружается новая версия)
            rag_system.delete_chunks_by_source_exact(
                knowledge_base_id=kb_id,
                source_type=file_type or 'unknown',
                source_path=source_path,
            )

            chunks = document_loader_manager.load_document(temp_path, document_info['file_type'])
            
            # Версия документа
            existing_logs = session.query(KnowledgeImportLog).filter_by(
                knowledge_base_id=kb_id,
                source_path=source_path,
            ).count()
            doc_version = existing_logs + 1
            source_updated_at = datetime.now(timezone.utc).isoformat()

            added = 0
            for chunk in chunks:
                content = chunk['content']
                base_meta = dict(chunk.get('metadata') or {})
                base_meta.setdefault('title', chunk.get('title') or source_path)
                base_meta['language'] = detect_language(content) if content else 'ru'
                base_meta['doc_hash'] = doc_hash
                base_meta['doc_version'] = doc_version
                base_meta['source_updated_at'] = source_updated_at

                rag_system.add_chunk(
                    knowledge_base_id=kb_id,
                    content=content,
                    source_type=file_type or 'unknown',
                    source_path=source_path,
                    metadata=base_meta,
                )
                added += 1
            
            total_chunks = added
            # Записать в журнал загрузок
            log = KnowledgeImportLog(
                knowledge_base_id=kb_id,
                user_telegram_id=tg_id,
                username=username,
                action_type="document",
                source_path=document_info['file_name'] or '',
                total_chunks=added,
            )
            session.add(log)
            session.commit()
            emit_n8n_import_event(
                kb_id=kb_id,
                action_type="document",
                source_path=document_info['file_name'] or '',
                total_chunks=added,
                user_info=user_info,
                extra={
                    "doc_hash": doc_hash,
                    "doc_version": doc_version,
                    "source_updated_at": source_updated_at,
                },
            )

        response_text = f"✅ Загружено {added} фрагментов в базу знаний!"
        
        if is_update:
            await message.reply_text(response_text, reply_markup=admin_menu())
        else:
            await query_or_update.edit_message_text(response_text, reply_markup=admin_menu())
    except Exception as e:
        error_text = f"❌ Ошибка загрузки: {str(e)}"
        if is_update:
            await message.reply_text(error_text)
        else:
            try:
                await query_or_update.edit_message_text(error_text)
            except:
                pass
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except:
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
        
        await update.message.reply_text("🔄 Обработка ZIP архива и загрузка вики...\n\nЭто может занять некоторое время.")
        
        try:
            # Скачать ZIP файл
            bot = update.get_bot()
            file = await bot.get_file(document.file_id)
            import tempfile
            temp_zip_path = os.path.join(tempfile.gettempdir(), f"wiki_zip_{document.file_id}.zip")
            await file.download_to_drive(temp_zip_path)
            
            # Загрузить вики из ZIP
            from wiki_git_loader import load_wiki_from_zip_async
            stats = await load_wiki_from_zip_async(temp_zip_path, wiki_url, kb_id)
            
            # Удалить временный файл
            try:
                os.unlink(temp_zip_path)
            except Exception:
                pass
            
            deleted = stats.get("deleted_chunks", 0)
            files = stats.get("files_processed", 0)
            added = stats.get("chunks_added", 0)
            wiki_root = stats.get("wiki_root", wiki_url)
            processed_files = stats.get("processed_files", [])
            
            # Обновить индекс RAG системы для доступа к новым чанкам
            try:
                rag_system.index = None
                rag_system.chunks = []
                logger.info("[wiki-zip] Индекс RAG системы сброшен, будет пересоздан при следующем поиске")
            except Exception as idx_error:
                logger.warning(f"[wiki-zip] Не удалось обновить индекс: {idx_error}")
            
            # Записать в журнал загрузок для каждого файла отдельно
            from database import KnowledgeImportLog, User
            tg_id = str(update.effective_user.id) if update.effective_user else ""
            db_user = session.query(User).filter_by(telegram_id=tg_id).first() if tg_id else None
            username = db_user.username if db_user else tg_id
            user_info = {"telegram_id": tg_id, "username": username}
            
            # Записываем каждый файл отдельно в журнал
            for file_info in processed_files:
                log = KnowledgeImportLog(
                    knowledge_base_id=kb_id,
                    user_telegram_id=tg_id,
                    username=username,
                    action_type="archive",  # Используем "archive" как в примере пользователя
                    source_path=file_info['wiki_url'],  # URL страницы вики
                    total_chunks=file_info['chunks'],
                )
                session.add(log)
            session.commit()
            
            try:
                from bot_handlers import emit_n8n_import_event
                emit_n8n_import_event(
                    kb_id=kb_id,
                    action_type="wiki_zip",
                    source_path=wiki_root,
                    total_chunks=added,
                    user_info=user_info,
                    extra={
                        "deleted_chunks": deleted,
                        "files_processed": files,
                        "wiki_root": wiki_root,
                        "original_url": wiki_url,
                    },
                )
            except ImportError:
                logger.warning("n8n integration not available")
            
            text = (
                "✅ Загрузка вики из ZIP архива завершена.\n\n"
                f"Исходный URL: {wiki_url}\n"
                f"Корневой wiki-URL: {wiki_root}\n"
                f"Удалено старых фрагментов: {deleted}\n"
                f"Обработано файлов: {files}\n"
                f"Добавлено фрагментов: {added}"
            )
            from templates.buttons import kb_actions_menu
            await update.message.reply_text(text, reply_markup=kb_actions_menu(kb_id))
        except Exception as e:
            logger.error(f"Ошибка при загрузке вики из ZIP: {e}", exc_info=True)
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
    
    # Обычная загрузка документа
    kb_id = context.user_data.get('kb_id')
    
    logger.info("Получен документ: file_name=%s, file_type=%s, kb_id=%s", file_name, file_type, kb_id)
    
    # Если база знаний не выбрана, показать меню выбора
    if not kb_id:
        kbs = rag_system.list_knowledge_bases()
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
    
    # Загрузить документ напрямую
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
        temp_path = os.path.join(tempfile.gettempdir(), f"{photo.file_id}.jpg")
        await file.download_to_drive(temp_path)
        # Проверить, нужно ли загрузить в RAG
        kb_id = context.user_data.get('kb_id')
        
        # Выбрать модель пользователя для изображений (если не указана, использовать текстовую)
        image_model = getattr(user, 'preferred_image_model', None) or (user.preferred_model if user.preferred_model else None)
        
        if kb_id:
            # Обновление: удалить старый фрагмент для этого изображения (если был)
            source_path = f"photo_{photo.file_id}.jpg"
            rag_system.delete_chunks_by_source_exact(
                knowledge_base_id=kb_id,
                source_type='image',
                source_path=source_path,
            )

            # Обработать и загрузить в RAG
            processed_text = image_processor.process_image_for_rag(
                temp_path,
                model=image_model,
            )
            source_updated_at = datetime.now(timezone.utc).isoformat()

            rag_system.add_chunk(
                knowledge_base_id=kb_id,
                content=processed_text,
                source_type='image',
                source_path=source_path,
                metadata={
                    'type': 'image',
                    'file_id': photo.file_id,
                    'source_updated_at': source_updated_at,
                },
            )
            # Записать в журнал загрузок
            tg_id = str(update.effective_user.id) if update.effective_user else ""
            db_user = session.query(User).filter_by(telegram_id=tg_id).first() if tg_id else None
            username = db_user.username if db_user else tg_id
            log = KnowledgeImportLog(
                knowledge_base_id=kb_id,
                user_telegram_id=tg_id,
                username=username,
                action_type="image",
                source_path=f"photo_{photo.file_id}.jpg",
                total_chunks=1,
            )
            session.add(log)
            session.commit()
            user_info = {"telegram_id": tg_id, "username": username}
            emit_n8n_import_event(
                kb_id=kb_id,
                action_type="image",
                source_path=f"photo_{photo.file_id}.jpg",
                total_chunks=1,
                user_info=user_info,
                extra={"file_id": photo.file_id},
            )
            await update.message.reply_text("✅ Изображение обработано и добавлено в базу знаний!", reply_markup=admin_menu())
        else:
            # Просто описать изображение, используя выбранную модель
            description = image_processor.describe_image(
                temp_path,
                "Опиши подробно, что изображено на этой картинке. Будь детальным и точным.",
                model=image_model,
            )
            menu = main_menu(is_admin=(user.role == 'admin'))
            answer = format_text_safe(f"🖼️ Описание изображения:\n\n{description}")
            await update.message.reply_text(answer, reply_markup=menu, parse_mode=None)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка обработки изображения: {str(e)}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


async def load_web_page(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, kb_id: int):
    """Загрузить веб-страницу в базу знаний"""
    try:
        chunks = document_loader_manager.load_document(url, 'web')
        
        added = 0
        # Удалить старые фрагменты этой страницы (обновление версии)
        rag_system.delete_chunks_by_source_exact(
            knowledge_base_id=kb_id,
            source_type='web',
            source_path=url,
        )

        # Версия документа (по журналу загрузок)
        existing_logs = session.query(KnowledgeImportLog).filter_by(
            knowledge_base_id=kb_id,
            source_path=url,
        ).count()
        doc_version = existing_logs + 1
        source_updated_at = datetime.now(timezone.utc).isoformat()

        for chunk in chunks:
            content = chunk['content']
            base_meta = dict(chunk.get('metadata') or {})
            base_meta.setdefault('title', chunk.get('title') or url)
            base_meta['language'] = detect_language(content) if content else 'ru'
            base_meta['doc_version'] = doc_version
            base_meta['source_updated_at'] = source_updated_at

            rag_system.add_chunk(
                knowledge_base_id=kb_id,
                content=content,
                source_type='web',
                source_path=url,
                metadata=base_meta,
            )
            added += 1
        
        # Записать в журнал загрузок
        tg_id = str(update.effective_user.id) if update.effective_user else ""
        db_user = session.query(User).filter_by(telegram_id=tg_id).first() if tg_id else None
        username = db_user.username if db_user else tg_id
        user_info = {"telegram_id": tg_id, "username": username}
        log = KnowledgeImportLog(
            knowledge_base_id=kb_id,
            user_telegram_id=tg_id,
            username=username,
            action_type="web",
            source_path=url,
            total_chunks=added,
        )
        session.add(log)
        session.commit()
        emit_n8n_import_event(
            kb_id=kb_id,
            action_type="web",
            source_path=url,
            total_chunks=added,
            user_info=user_info,
            extra={
                "doc_version": doc_version,
                "source_updated_at": source_updated_at,
            },
        )

        await update.message.reply_text(f"✅ Загружено {added} фрагментов с веб-страницы!", reply_markup=admin_menu())
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка загрузки веб-страницы: {str(e)}")
