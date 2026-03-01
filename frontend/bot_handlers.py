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
    user_management_menu, knowledge_base_menu, kb_actions_menu,
    document_type_menu, confirm_menu, search_options_menu,
    asr_models_menu
)
from shared.config import ADMIN_IDS
from shared.n8n_client import n8n_client

# Глобальный session удалён - создаём session локально в функциях


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

    def _format_source_html(source_path: str, source_type: str, index: int) -> str:
        is_url = source_type == "web" or source_path.startswith(("http://", "https://"))
        if is_url:
            url_escaped = escape(source_path, quote=True)
            display_url = normalize_wiki_url_for_display(source_path) or source_path
            return f'{index}. <a href="{url_escaped}">{escape(display_url)}</a>'
        
        f_name = source_path.split("/")[-1].split("::")[-1]
        return f"{index}. <code>{escape(f_name)}</code>"

    sources_html_list = []
    seen_paths = set()
    for i, s in enumerate(backend_sources, 1):
        path = s.get("source_path") or ""
        if not path or path.lower() in seen_paths: continue
        seen_paths.add(path.lower())
        sources_html_list.append(_format_source_html(path, s.get("source_type", "unknown"), i))

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


async def _ensure_kb_or_ask_select(update: Update, context: ContextTypes.DEFAULT_TYPE, user: UserContext, query: str) -> Tuple[Optional[int], bool]:
    kb_id = context.user_data.get('kb_id')
    if kb_id: return kb_id, False
    kbs = await asyncio.to_thread(backend_client.list_knowledge_bases)
    if not kbs:
        await update.message.reply_text("❌ Нет баз знаний.")
        return None, True
    context.user_data['pending_query'] = query
    context.user_data['state'] = 'waiting_kb_for_query'
    await update.message.reply_text("📚 Выберите базу знаний:", reply_markup=knowledge_base_menu(kbs))
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
        context.user_data['state'] = 'waiting_query'
        await update.message.reply_text("🔍 Введите запрос для поиска:")
        return
    if text_input == "👨‍💼 Админ-панель" and user.role == 'admin':
        await update.message.reply_text("👨‍💼 Админ-панель:", reply_markup=admin_menu())
        return

    if state == 'waiting_query':
        kb_id, prompted = await _ensure_kb_or_ask_select(update, context, user, text_input)
        if prompted: return
        html, _ = await perform_rag_query_and_render(text_input, kb_id, user)
        await update.message.reply_text(html, parse_mode='HTML')
        context.user_data['state'] = None
    elif state == 'waiting_ai_query':
        prompt = create_prompt_with_language(text_input, None, task="answer")
        ans = await asyncio.to_thread(ai_manager.query, prompt, provider_name=user.preferred_provider, model=user.preferred_model)
        html = f"🤖 <b>Ответ:</b>\n\n{format_for_telegram_answer(ans, False)}"
        await update.message.reply_text(html, parse_mode='HTML', reply_markup=main_menu(user.role == 'admin'))
        context.user_data['state'] = None
    elif state == 'waiting_asr_model' and user.role == 'admin':
        await update.message.reply_text(f"⏳ Проверяю модель <code>{text_input}</code>...", parse_mode='HTML')
        try:
            res = await asyncio.to_thread(backend_client.update_asr_settings, {"asr_model_name": text_input})
            await update.message.reply_text(f"✅ Модель: <code>{res.get('asr_model_name')}</code>", parse_mode='HTML', reply_markup=admin_menu())
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {str(e)}")
        context.user_data['state'] = None
    else:
        kb_id = context.user_data.get('kb_id')
        if kb_id:
            html, _ = await perform_rag_query_and_render(text_input, kb_id, user)
            await update.message.reply_text(html, parse_mode='HTML')
        else:
            await handle_start(update, context)


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
    if not doc: return
    
    kb_id = context.user_data.get('kb_id')
    if not kb_id:
        kbs = await asyncio.to_thread(backend_client.list_knowledge_bases)
        context.user_data['pending_document'] = {'file_id': doc.file_id, 'file_name': doc.file_name}
        await update.message.reply_text("Выберите БЗ для загрузки:", reply_markup=knowledge_base_menu(kbs))
        return

    await update.message.reply_text("🔄 Загружаю документ...")


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
